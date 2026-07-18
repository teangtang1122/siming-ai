#!/usr/bin/env python3
"""Deterministic architecture checks for the Siming modular monolith."""
from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "backend" / "app"
BASELINE_PATH = ROOT / "backend" / "architecture-baseline.json"
SOFT_MODULE_LINES = 600
HARD_MODULE_LINES = 1000
SOFT_FUNCTION_LINES = 80
HARD_FUNCTION_LINES = 150


@dataclass(frozen=True)
class ParsedModule:
    name: str
    path: Path
    tree: ast.Module
    text: str


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _module_name(path: Path) -> str:
    relative = path.relative_to(APP_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return "app" + (f".{'.'.join(parts)}" if parts else "")


def _parse_modules() -> dict[str, ParsedModule]:
    modules: dict[str, ParsedModule] = {}
    for path in APP_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        module = ParsedModule(
            name=_module_name(path),
            path=path,
            tree=ast.parse(text, filename=str(path)),
            text=text,
        )
        modules[module.name] = module
    return modules


def _resolve_import(
    source: ParsedModule,
    node: ast.ImportFrom,
) -> str:
    if not node.level:
        return node.module or ""
    package = source.name if source.path.name == "__init__.py" else source.name.rpartition(".")[0]
    package_parts = package.split(".")
    keep = max(0, len(package_parts) - node.level + 1)
    prefix = ".".join(package_parts[:keep])
    return ".".join(part for part in (prefix, node.module or "") if part)


def _import_graph(modules: dict[str, ParsedModule]) -> dict[str, set[str]]:
    graph = {name: set() for name in modules}
    for source in modules.values():
        candidates: list[str] = []
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = _resolve_import(source, node)
                candidates.append(target)
                candidates.extend(
                    f"{target}.{alias.name}"
                    for alias in node.names
                    if target
                )
        for candidate in candidates:
            target = candidate
            while target and target not in modules:
                target = target.rpartition(".")[0]
            if target in modules and target != source.name:
                graph[source.name].add(target)
    return graph


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> list[list[str]]:
    index = 0
    indexes: dict[str, int] = {}
    low_links: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        low_links[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in indexes:
                visit(target)
                low_links[node] = min(low_links[node], low_links[target])
            elif target in on_stack:
                low_links[node] = min(low_links[node], indexes[target])
        if low_links[node] != indexes[node]:
            return
        component: list[str] = []
        while True:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == node:
                break
        if len(component) > 1:
            components.append(sorted(component))

    for node in graph:
        if node not in indexes:
            visit(node)
    return components


def _is_uow_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "uow" or node.id.endswith("_uow")
    if isinstance(node, ast.Attribute):
        return node.attr == "uow" or node.attr.endswith("_uow")
    return False


def _commit_calls(module: ParsedModule) -> int:
    if _relative(module.path) == "backend/app/architecture/uow.py":
        return 0
    return sum(
        1
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
        and not _is_uow_receiver(node.func.value)
    )


def _router_boundary_counts(modules: dict[str, ParsedModule]) -> dict[str, int]:
    """Count persistence and model-runtime details leaking into HTTP routers."""

    query_calls = 0
    orm_imports = 0
    model_adapter_imports = 0
    adapter_names = {
        "LLMGateway",
        "OpenAIAdapter",
        "AnthropicAdapter",
        "LocalCLIAdapter",
        "AsyncOpenAI",
    }
    for module in modules.values():
        if not module.name.startswith("app.routers."):
            continue
        for node in ast.walk(module.tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "query"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "db"
            ):
                query_calls += 1
            elif isinstance(node, ast.ImportFrom):
                target = _resolve_import(module, node)
                if target == "app.database.models":
                    orm_imports += 1
                model_adapter_imports += sum(
                    1 for alias in node.names if alias.name in adapter_names
                )
            elif isinstance(node, ast.Import):
                model_adapter_imports += sum(
                    1
                    for alias in node.names
                    if alias.name.rpartition(".")[2] in adapter_names
                )
    return {
        "db_query_calls": query_calls,
        "orm_imports": orm_imports,
        "model_adapter_imports": model_adapter_imports,
    }


class _FunctionSizes(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack: list[str] = []
        self.sizes: dict[str, int] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        size = (node.end_lineno or node.lineno) - node.lineno + 1
        key = f"{self.path}:{'.'.join([*self.stack, node.name])}"
        self.sizes[key] = size
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def _is_strict_path(path: Path) -> bool:
    relative = path.relative_to(APP_ROOT).as_posix()
    return (
        relative.startswith(("architecture/", "bootstrap/", "modules/"))
        or relative
        in {
            "database/bootstrap.py",
            "database/schema_models.py",
            "core/legacy_env.py",
            "core/numbers.py",
            "prompts/workspace_contract.py",
            "services/scheduler/ports.py",
            "services/skills/tool_catalog.py",
            "services/workspace/idempotency.py",
            "services/workspace/scheduled_task_runner.py",
        }
    )


def _module_layer(name: str) -> tuple[str, str] | None:
    parts = name.split(".")
    if len(parts) < 4 or parts[:2] != ["app", "modules"]:
        return None
    if len(parts) < 4:
        return None
    module_name = parts[2]
    layer = parts[3]
    if layer not in {"domain", "application", "interfaces", "infrastructure"}:
        return None
    return module_name, layer


def _check_module_layers(
    graph: dict[str, set[str]],
) -> list[str]:
    errors: list[str] = []
    allowed_same_module = {
        "domain": {"domain"},
        "application": {"domain", "application"},
        "interfaces": {"domain", "application", "interfaces"},
        "infrastructure": {"domain", "application", "infrastructure"},
    }
    for source, targets in graph.items():
        source_layer = _module_layer(source)
        if source_layer is None:
            continue
        source_module, layer = source_layer
        for target in targets:
            target_layer = _module_layer(target)
            if target_layer is None:
                continue
            target_module, target_kind = target_layer
            if source_module == target_module:
                if target_kind not in allowed_same_module[layer]:
                    errors.append(
                        f"{source} cannot depend on {target} ({layer} layer violation)"
                    )
            elif target_kind != "interfaces":
                errors.append(
                    f"{source} must use another module's interfaces, not {target}"
                )
    return errors


def main() -> int:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    modules = _parse_modules()
    graph = _import_graph(modules)
    errors: list[str] = []
    warnings: list[str] = []

    cycles = _strongly_connected_components(graph)
    for cycle in cycles:
        errors.append("Import cycle: " + " -> ".join(cycle))
    errors.extend(_check_module_layers(graph))

    total_commits = sum(_commit_calls(module) for module in modules.values())
    baseline_commits = int(baseline["legacy_direct_commit_calls"])
    if total_commits > baseline_commits:
        errors.append(
            f"Direct commit calls increased from {baseline_commits} to {total_commits}"
        )
    elif total_commits < baseline_commits:
        warnings.append(
            f"Direct commit calls improved from {baseline_commits} to {total_commits}; "
            "lower the baseline in the same change."
        )

    router_counts = _router_boundary_counts(modules)
    router_baselines = {
        "db_query_calls": int(baseline["legacy_router_db_query_calls"]),
        "orm_imports": int(baseline["legacy_router_orm_imports"]),
        "model_adapter_imports": int(
            baseline["legacy_router_model_adapter_imports"]
        ),
    }
    for name, count in router_counts.items():
        expected = router_baselines[name]
        if count > expected:
            errors.append(
                f"Router {name} increased from {expected} to {count}; "
                "move persistence/model access behind an application port"
            )
        elif count < expected:
            warnings.append(
                f"Router {name} improved from {expected} to {count}; "
                "lower the baseline in the same change."
            )

    for module in modules.values():
        relative = _relative(module.path)
        if relative != "backend/app/core/legacy_env.py" and (
            "MOSHU_" in module.text or "NOVEL_AGENT_" in module.text
        ):
            errors.append(
                f"{relative} contains a pre-3.0 environment name outside core/legacy_env.py"
            )
        commits = _commit_calls(module)
        if (
            commits
            and _is_strict_path(module.path)
            and relative != "backend/app/architecture/uow.py"
        ):
            errors.append(f"{relative} contains {commits} direct commit call(s)")

        line_count = len(module.text.splitlines())
        grandfathered_lines = int(
            baseline["oversized_modules"].get(relative, 0)
        )
        if line_count > HARD_MODULE_LINES:
            if not grandfathered_lines or line_count > grandfathered_lines:
                errors.append(
                    f"{relative} has {line_count} lines (hard limit {HARD_MODULE_LINES})"
                )
            else:
                warnings.append(
                    f"Legacy oversized module: {relative} ({line_count} lines)"
                )
        elif line_count > SOFT_MODULE_LINES:
            warnings.append(f"Large module: {relative} ({line_count} lines)")

        visitor = _FunctionSizes(relative)
        visitor.visit(module.tree)
        for key, size in visitor.sizes.items():
            grandfathered_size = int(
                baseline["oversized_functions"].get(key, 0)
            )
            if size > HARD_FUNCTION_LINES:
                if not grandfathered_size or size > grandfathered_size:
                    errors.append(
                        f"{key} has {size} lines (hard limit {HARD_FUNCTION_LINES})"
                    )
                else:
                    warnings.append(f"Legacy oversized function: {key} ({size} lines)")
            elif size > SOFT_FUNCTION_LINES:
                warnings.append(f"Large function: {key} ({size} lines)")

    for warning in sorted(set(warnings)):
        print(f"ARCH-WARN: {warning}")
    for error in sorted(set(errors)):
        print(f"ARCH-ERROR: {error}", file=sys.stderr)
    print(
        "Architecture summary: "
        f"modules={len(modules)} cycles={len(cycles)} "
        f"direct_commits={total_commits} "
        f"router_queries={router_counts['db_query_calls']} "
        f"router_orm_imports={router_counts['orm_imports']} "
        f"router_model_adapters={router_counts['model_adapter_imports']} "
        f"errors={len(set(errors))}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
