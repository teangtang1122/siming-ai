#!/usr/bin/env python3
"""Compile every PromptSpec without contacting a model."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    from app.modules.assistant.infrastructure.runtime import compile_prompt_catalog
    from app.services.workspace.registry import registry

    compiled = compile_prompt_catalog(known_tools=registry.all_names())
    for spec_id, prompt in sorted(compiled.items()):
        print(
            f"{spec_id} v{prompt.version} "
            f"chars={len(prompt.template)} sha256={prompt.sha256}"
        )
    print(f"Compiled {len(compiled)} PromptSpecs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
