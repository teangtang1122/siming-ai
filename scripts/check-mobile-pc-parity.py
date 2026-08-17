#!/usr/bin/env python3
"""Validate and render the Android ↔ PC capability parity contract.

The contract is intentionally source-backed.  Every implemented claim must
point at the PC authority, Android adapter, or regression test that proves it.
Coverage extractors also fail when a new Android PC route, writable entity, or
standalone workspace tool is added without an explicit parity decision.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "contracts" / "mobile-pc-parity.json"
DEFAULT_DOC = ROOT / "docs" / "mobile-pc-parity.md"

MODES = ("pc", "android_online", "android_offline", "android_standalone")
SUPPORT_STATES = {
    "canonical",
    "canonical_proxy",
    "read_only_proxy",
    "sync_replay",
    "read_only_cache",
    "local_cache",
    "degraded",
    "blocked",
    "unsupported",
    "not_applicable",
}
IMPLEMENTED_STATES = {
    "canonical",
    "canonical_proxy",
    "read_only_proxy",
    "sync_replay",
    "read_only_cache",
    "local_cache",
    "degraded",
}
STATUS_STATES = {"aligned", "partial", "planned"}
AUTHORITY_TYPES = {
    "pc_http",
    "pc_workspace_tool",
    "pc_sync_protocol",
    "pc_domain_service",
    "shared_generated_contract",
}
IDEMPOTENCY_STRATEGIES = {
    "read_only",
    "server_transaction",
    "revisioned_outbox",
    "set_replacement",
    "client_serialization",
    "draft_reference",
    "request_key",
    "not_applicable",
}
AREAS = {
    "assistant",
    "authoring",
    "chapter",
    "character",
    "context",
    "governance",
    "novel_creation",
    "outline",
    "sync",
    "worldbuilding",
}
DESTRUCTIVE_SIDE_EFFECTS = {
    "chapter_restore",
    "relationship_replace",
    "governance_transition",
    "authoritative_reorder",
}

SUPPORT_LABELS = {
    "canonical": "PC 权威实现",
    "canonical_proxy": "调用 PC 权威接口",
    "read_only_proxy": "调用 PC 只读接口",
    "sync_replay": "修订队列回放",
    "read_only_cache": "只读缓存",
    "local_cache": "本地副本",
    "degraded": "明确降级实现",
    "blocked": "明确阻止",
    "unsupported": "尚未支持",
    "not_applicable": "不适用",
}
STATUS_LABELS = {
    "aligned": "已对齐",
    "partial": "部分对齐",
    "planned": "待实现",
}
MODE_LABELS = {
    "pc": "PC",
    "android_online": "Android 在线",
    "android_offline": "Android 离线",
    "android_standalone": "Android 独立 Agent",
}


class ContractError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"missing contract: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {path}: {exc}") from exc
    _require(isinstance(data, dict), "contract root must be an object")
    return data


def _read_source(root: Path, relative: str, cache: dict[str, str]) -> str:
    _require(relative and not relative.startswith("/"), f"invalid source path: {relative!r}")
    _require(".." not in Path(relative).parts, f"source path escapes repository: {relative}")
    if relative not in cache:
        path = root / relative
        _require(path.is_file(), f"source path does not exist: {relative}")
        cache[relative] = path.read_text(encoding="utf-8")
    return cache[relative]


def _validate_ref(
    root: Path,
    ref: Any,
    *,
    label: str,
    cache: dict[str, str],
) -> None:
    _require(isinstance(ref, dict), f"{label} must be an object")
    path = ref.get("path")
    _require(isinstance(path, str) and path.strip(), f"{label}.path is required")
    text = _read_source(root, path, cache)
    contains = ref.get("contains")
    if contains is None:
        return
    _require(isinstance(contains, str) and contains, f"{label}.contains must be non-empty")
    minimum = ref.get("min_count", 1)
    _require(isinstance(minimum, int) and minimum >= 1, f"{label}.min_count must be >= 1")
    actual = text.count(contains)
    _require(
        actual >= minimum,
        f"{label} expected {path!r} to contain {contains!r} at least {minimum} time(s); found {actual}",
    )


def _extract_symbols(target: dict[str, Any], text: str) -> set[str]:
    extractor = target.get("extractor")
    start_marker = target.get("start_marker")
    end_marker = target.get("end_marker")
    window = text
    if start_marker:
        start = text.find(start_marker)
        _require(start >= 0, f"coverage target {target.get('id')} missing start_marker")
        window = text[start:]
    if end_marker:
        end = window.find(end_marker, len(start_marker or ""))
        _require(end >= 0, f"coverage target {target.get('id')} missing end_marker")
        window = window[:end]

    if extractor == "kotlin_functions":
        return set(
            re.findall(
                r"^\s*(?:(?:internal|private|public)\s+)?fun\s+(\w+)\s*\(",
                window,
                flags=re.MULTILINE,
            )
        )
    if extractor == "kotlin_map_list_keys":
        return set(
            re.findall(
                r'^\s*"([a-z_]+)"\s+to\s+listOf\(',
                window,
                flags=re.MULTILINE,
            )
        )
    if extractor == "kotlin_when_strings":
        return set(re.findall(r'"([^"]+)"\s*->', window))
    raise ContractError(f"unsupported coverage extractor: {extractor!r}")


def _validate_coverage(
    root: Path,
    coverage: Any,
    capability_ids: set[str],
    cache: dict[str, str],
) -> None:
    _require(isinstance(coverage, list) and coverage, "coverage_targets must be a non-empty array")
    target_ids: set[str] = set()
    for index, target in enumerate(coverage):
        label = f"coverage_targets[{index}]"
        _require(isinstance(target, dict), f"{label} must be an object")
        target_id = target.get("id")
        _require(isinstance(target_id, str) and target_id, f"{label}.id is required")
        _require(target_id not in target_ids, f"duplicate coverage target id: {target_id}")
        target_ids.add(target_id)
        path = target.get("path")
        _require(isinstance(path, str) and path, f"{label}.path is required")
        text = _read_source(root, path, cache)
        extracted = _extract_symbols(target, text)
        mapping = target.get("symbols")
        ignored = target.get("ignore_symbols", {})
        _require(isinstance(mapping, dict), f"{label}.symbols must be an object")
        _require(isinstance(ignored, dict), f"{label}.ignore_symbols must be an object")
        mapped_symbols = set(mapping)
        ignored_symbols = set(ignored)
        for symbol, capability_id in mapping.items():
            _require(
                capability_id in capability_ids,
                f"{label}.symbols[{symbol!r}] references unknown capability {capability_id!r}",
            )
        for symbol, reason in ignored.items():
            _require(isinstance(reason, str) and reason.strip(), f"{label}.ignore_symbols[{symbol!r}] needs a reason")
        missing_decisions = extracted - mapped_symbols - ignored_symbols
        stale_decisions = (mapped_symbols | ignored_symbols) - extracted
        _require(
            not missing_decisions,
            f"{target_id} has uncovered source symbols: {sorted(missing_decisions)}",
        )
        _require(
            not stale_decisions,
            f"{target_id} lists source symbols that no longer exist: {sorted(stale_decisions)}",
        )


def validate_contract(root: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    _require(data.get("schema_version") == 1, "schema_version must be 1")
    contract_version = data.get("contract_version")
    _require(
        isinstance(contract_version, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", contract_version),
        "contract_version must use YYYY-MM-DD",
    )
    _require(data.get("canonical_authority") == "pc", "canonical_authority must be 'pc'")
    mode_definitions = data.get("modes")
    _require(isinstance(mode_definitions, dict), "modes must be an object")
    _require(set(mode_definitions) == set(MODES), f"modes must contain exactly {list(MODES)}")
    _require(
        all(isinstance(value, str) and value.strip() for value in mode_definitions.values()),
        "every mode definition must be non-empty text",
    )
    capabilities = data.get("capabilities")
    _require(isinstance(capabilities, list) and capabilities, "capabilities must be a non-empty array")
    ids = [item.get("id") for item in capabilities if isinstance(item, dict)]
    _require(len(ids) == len(capabilities), "every capability must be an object with an id")
    _require(all(isinstance(item, str) and item for item in ids), "capability ids must be non-empty strings")
    _require(len(ids) == len(set(ids)), "capability ids must be unique")
    _require(ids == sorted(ids), "capabilities must be sorted by id")

    cache: dict[str, str] = {}
    for index, capability in enumerate(capabilities):
        capability_id = capability["id"]
        label = f"capabilities[{index}] ({capability_id})"
        _require(capability.get("area") in AREAS, f"{label}.area is invalid")
        _require(isinstance(capability.get("summary"), str) and capability["summary"].strip(), f"{label}.summary is required")
        status = capability.get("status")
        _require(status in STATUS_STATES, f"{label}.status is invalid")

        authority = capability.get("authority")
        _require(isinstance(authority, dict), f"{label}.authority must be an object")
        _require(authority.get("type") in AUTHORITY_TYPES, f"{label}.authority.type is invalid")
        _require(isinstance(authority.get("entrypoint"), str) and authority["entrypoint"].strip(), f"{label}.authority.entrypoint is required")
        authority_refs = authority.get("source_refs")
        _require(isinstance(authority_refs, list) and authority_refs, f"{label}.authority.source_refs is required")
        for ref_index, ref in enumerate(authority_refs):
            _validate_ref(root, ref, label=f"{label}.authority.source_refs[{ref_index}]", cache=cache)

        modes = capability.get("modes")
        _require(isinstance(modes, dict), f"{label}.modes must be an object")
        _require(set(modes) == set(MODES), f"{label}.modes must contain exactly {list(MODES)}")
        _require(
            modes["pc"].get("support") == "canonical",
            f"{label}.modes.pc must be canonical",
        )
        has_gap = False
        for mode in MODES:
            mode_data = modes[mode]
            mode_label = f"{label}.modes.{mode}"
            _require(isinstance(mode_data, dict), f"{mode_label} must be an object")
            support = mode_data.get("support")
            _require(support in SUPPORT_STATES, f"{mode_label}.support is invalid")
            refs = mode_data.get("source_refs", [])
            _require(isinstance(refs, list), f"{mode_label}.source_refs must be an array")
            if support in IMPLEMENTED_STATES and not (mode == "pc" and support == "canonical"):
                _require(refs, f"{mode_label} claims {support!r} without source_refs")
            for ref_index, ref in enumerate(refs):
                _validate_ref(root, ref, label=f"{mode_label}.source_refs[{ref_index}]", cache=cache)
            if support in {"blocked", "unsupported", "not_applicable"}:
                _require(
                    isinstance(mode_data.get("reason"), str) and mode_data["reason"].strip(),
                    f"{mode_label}.reason is required for {support}",
                )
            if support == "degraded":
                _require(
                    isinstance(mode_data.get("limitations"), str) and mode_data["limitations"].strip(),
                    f"{mode_label}.limitations is required for degraded support",
                )
            if support in {"degraded", "unsupported"}:
                has_gap = True

        if has_gap:
            _require(status in {"partial", "planned"}, f"{label} has a parity gap but status is {status!r}")
            gaps = capability.get("known_gaps")
            _require(isinstance(gaps, list) and gaps, f"{label}.known_gaps is required")
            _require(all(isinstance(item, str) and item.strip() for item in gaps), f"{label}.known_gaps must contain text")
        elif status == "aligned":
            _require(not capability.get("known_gaps"), f"{label} is aligned but still declares known_gaps")

        side_effects = capability.get("side_effects")
        _require(isinstance(side_effects, list), f"{label}.side_effects must be an array")
        _require(len(side_effects) == len(set(side_effects)), f"{label}.side_effects contains duplicates")
        _require(all(isinstance(item, str) and item for item in side_effects), f"{label}.side_effects must contain strings")

        idempotency = capability.get("idempotency")
        _require(isinstance(idempotency, dict), f"{label}.idempotency must be an object")
        required = idempotency.get("required")
        _require(isinstance(required, bool), f"{label}.idempotency.required must be boolean")
        strategy = idempotency.get("strategy")
        _require(strategy in IDEMPOTENCY_STRATEGIES, f"{label}.idempotency.strategy is invalid")
        if required:
            _require(strategy != "not_applicable", f"{label} requires idempotency but has no strategy")
        if DESTRUCTIVE_SIDE_EFFECTS.intersection(side_effects):
            _require(required, f"{label} has destructive side effects and must require idempotency")
        if strategy == "client_serialization":
            _require(
                isinstance(idempotency.get("limitations"), str) and idempotency["limitations"].strip(),
                f"{label}.idempotency.limitations is required for client_serialization",
            )

        tests = capability.get("tests")
        _require(isinstance(tests, list) and tests, f"{label}.tests must be a non-empty array")
        for ref_index, ref in enumerate(tests):
            _validate_ref(root, ref, label=f"{label}.tests[{ref_index}]", cache=cache)

    _validate_coverage(root, data.get("coverage_targets"), set(ids), cache)
    return capabilities


def _mode_text(mode: dict[str, Any]) -> str:
    support = mode["support"]
    text = SUPPORT_LABELS[support]
    detail = mode.get("reason") or mode.get("limitations") or mode.get("notes")
    if detail:
        text += f"：{detail}"
    return text


def render_markdown(data: dict[str, Any], capabilities: list[dict[str, Any]]) -> str:
    aligned_count = sum(item["status"] == "aligned" for item in capabilities)
    partial_count = sum(item["status"] == "partial" for item in capabilities)
    planned_count = sum(item["status"] == "planned" for item in capabilities)
    lines = [
        "# Android ↔ PC 能力对齐契约",
        "",
        "> 本文由 `contracts/mobile-pc-parity.json` 通过 `scripts/check-mobile-pc-parity.py` 生成；请勿手工修改。",
        "",
        "PC 是小说数据、领域副作用和上下文治理的唯一权威实现。Android 在线模式应尽量作为薄客户端；离线模式只允许可验证回放的修订；手机独立 Agent 的降级能力必须显式记录。",
        "",
        f"当前共登记 **{len(capabilities)}** 项能力：**{aligned_count}** 项已对齐、**{partial_count}** 项部分对齐、**{planned_count}** 项待实现。",
        "",
        "## 总览",
        "",
        "| 能力 | 权威入口 | Android 在线 | Android 离线 | Android 独立 Agent | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for item in capabilities:
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} |".format(
                item["id"],
                item["authority"]["entrypoint"].replace("|", "\\|"),
                SUPPORT_LABELS[item["modes"]["android_online"]["support"]],
                SUPPORT_LABELS[item["modes"]["android_offline"]["support"]],
                SUPPORT_LABELS[item["modes"]["android_standalone"]["support"]],
                STATUS_LABELS[item["status"]],
            )
        )

    lines.extend(["", "## 详细能力", ""])
    for item in capabilities:
        lines.extend(
            [
                f"### `{item['id']}` — {item['summary']}",
                "",
                f"- **权威入口：** `{item['authority']['entrypoint']}`（`{item['authority']['type']}`）",
                f"- **状态：** {STATUS_LABELS[item['status']]}",
                f"- **副作用：** {('、'.join(item['side_effects']) if item['side_effects'] else '无写入副作用')}",
                f"- **幂等策略：** `{item['idempotency']['strategy']}`；{'必须防重' if item['idempotency']['required'] else '只读或无需防重'}",
            ]
        )
        if item["idempotency"].get("limitations"):
            lines.append(f"- **幂等限制：** {item['idempotency']['limitations']}")
        for mode in MODES:
            lines.append(f"- **{MODE_LABELS[mode]}：** {_mode_text(item['modes'][mode])}")
        if item.get("known_gaps"):
            lines.append("- **已知缺口：**")
            lines.extend(f"  - {gap}" for gap in item["known_gaps"])
        lines.append("")

    lines.extend(
        [
            "## 维护规则",
            "",
            "1. 新增 `PcApiPaths` 方法、`PcAuthoringContract` 可写类型或 `MobileWorkspaceAgent` 工具时，必须在契约中做出能力归属或写明忽略理由。",
            "2. 声称已实现的模式必须引用实际源码；每项能力必须引用至少一个回归测试。",
            "3. `degraded`、`unsupported` 或高风险写操作必须明确限制和幂等策略，不能以“行为大致相同”代替契约。",
            "4. 修改契约后运行：`python scripts/check-mobile-pc-parity.py --write-doc docs/mobile-pc-parity.md`。",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--check-doc", type=Path)
    parser.add_argument("--write-doc", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    root = args.root.resolve()
    contract_path = args.contract if args.contract.is_absolute() else root / args.contract
    data = _load_json(contract_path)
    capabilities = validate_contract(root, data)
    rendered = render_markdown(data, capabilities)

    if args.write_doc:
        output = args.write_doc if args.write_doc.is_absolute() else root / args.write_doc
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    if args.check_doc:
        expected = args.check_doc if args.check_doc.is_absolute() else root / args.check_doc
        _require(expected.is_file(), f"generated parity document is missing: {expected}")
        actual = expected.read_text(encoding="utf-8")
        _require(
            actual == rendered,
            f"{expected.relative_to(root)} is stale; run: python scripts/check-mobile-pc-parity.py --write-doc {expected.relative_to(root)}",
        )

    print(f"mobile/PC parity contract valid: {len(capabilities)} capabilities")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"parity contract error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
