"""Deterministic PromptSpec compiler."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from string import Formatter
from typing import Protocol

from ..domain.prompt_spec import CompiledPrompt, PromptSpec


class PromptRepository(Protocol):
    def load_all(self) -> Sequence[PromptSpec]: ...


class PromptCompiler:
    """Validate and compile all prompt sources from one repository."""

    def __init__(
        self,
        repository: PromptRepository,
        *,
        known_tools: Iterable[str] | None = None,
    ) -> None:
        self._repository = repository
        self._known_tools = set(known_tools) if known_tools is not None else None
        self._compiled: dict[str, CompiledPrompt] | None = None

    def compile_all(self) -> Mapping[str, CompiledPrompt]:
        specs = list(self._repository.load_all())
        by_id = {spec.id: spec for spec in specs}
        if len(by_id) != len(specs):
            raise ValueError("PromptSpec ids must be unique")

        compiled: dict[str, CompiledPrompt] = {}
        for spec_id in sorted(by_id):
            self._compile_one(spec_id, by_id, compiled, stack=[])
        self._compiled = compiled
        return dict(compiled)

    def get(self, spec_id: str) -> CompiledPrompt:
        if self._compiled is None:
            self.compile_all()
        assert self._compiled is not None
        try:
            return self._compiled[spec_id]
        except KeyError as exc:
            raise KeyError(f"Unknown PromptSpec: {spec_id}") from exc

    def render(self, spec_id: str, **values: object) -> str:
        return self.get(spec_id).render(**values)

    def _compile_one(
        self,
        spec_id: str,
        specs: Mapping[str, PromptSpec],
        compiled: dict[str, CompiledPrompt],
        *,
        stack: list[str],
    ) -> CompiledPrompt:
        if spec_id in compiled:
            return compiled[spec_id]
        if spec_id in stack:
            cycle = " -> ".join([*stack, spec_id])
            raise ValueError(f"PromptSpec fragment cycle: {cycle}")
        if spec_id not in specs:
            raise ValueError(f"Unknown PromptSpec fragment: {spec_id}")

        spec = specs[spec_id]
        fragment_templates: list[str] = []
        source_paths: list[str] = []
        next_stack = [*stack, spec_id]
        for fragment_id in spec.fragments:
            fragment = self._compile_one(
                fragment_id,
                specs,
                compiled,
                stack=next_stack,
            )
            fragment_templates.append(fragment.template)
            source_paths.extend(fragment.source_paths)

        template = "\n\n".join([*fragment_templates, spec.body.strip()]).strip()
        placeholders = {
            field_name
            for _, field_name, _, _ in Formatter().parse(template)
            if field_name
        }
        undeclared = placeholders - set(spec.inputs)
        if undeclared:
            names = ", ".join(sorted(undeclared))
            raise ValueError(f"Prompt {spec.id} uses undeclared inputs: {names}")
        unused = set(spec.inputs) - placeholders
        if unused:
            names = ", ".join(sorted(unused))
            raise ValueError(f"Prompt {spec.id} declares unused inputs: {names}")
        if len(template) > spec.budget.fixed_chars:
            raise ValueError(
                f"Prompt {spec.id} fixed text is {len(template)} chars; "
                f"budget is {spec.budget.fixed_chars}"
            )
        if self._known_tools is not None:
            unknown_tools = set(spec.tools) - self._known_tools
            if unknown_tools:
                names = ", ".join(sorted(unknown_tools))
                raise ValueError(f"Prompt {spec.id} references unknown tools: {names}")

        for case in spec.golden_cases:
            for required in case.required_text:
                if required not in template:
                    raise ValueError(
                        f"Prompt {spec.id} golden case {case.name!r} "
                        f"is missing {required!r}"
                    )
            for forbidden in case.forbidden_text:
                if forbidden in template:
                    raise ValueError(
                        f"Prompt {spec.id} golden case {case.name!r} "
                        f"contains forbidden text {forbidden!r}"
                    )

        source_paths.append(spec.source_path)
        result = CompiledPrompt.from_template(
            spec=spec,
            template=template,
            source_paths=list(dict.fromkeys(path for path in source_paths if path)),
        )
        compiled[spec_id] = result
        return result


__all__ = ["PromptCompiler", "PromptRepository"]
