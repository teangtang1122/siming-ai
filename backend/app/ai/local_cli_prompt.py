"""Prompt and launch helpers for local Agent CLIs."""
from __future__ import annotations

import os
import tempfile
from typing import Any


def file_prompt_instruction(
    prompt_file: str,
    attachments: list[str],
    *,
    allow_mcp: bool = False,
) -> str:
    attachment_note = ""
    if attachments:
        attachment_note = "\n任务可能引用以下只读资料文件：\n" + "\n".join(
            f"- {path}" for path in attachments
        )
    tool_rule = (
        "本任务明确允许使用已配置的 Siming MCP 工具。需要读取或修改司命结构化数据时，"
        "必须通过 Siming MCP 执行，并在写入后再次读取验证；不得仅用文字声称已经保存。"
        if allow_mcp
        else "除读取该任务文件和其中明确引用的资料外，不要扫描代码仓库，不要修改文件，"
        "不要调用 Siming MCP 或其他外部工具。"
    )
    return (
        "你是司命内部的文本生成执行器，不是代码助手。"
        f"请读取 UTF-8 任务文件：{prompt_file}\n"
        "严格按文件中的 SYSTEM/USER 指令完成任务。"
        f"{tool_rule}"
        "最终只输出任务要求的正文或结构化结果，不要回复 Ready。"
        f"{attachment_note}"
    )


def prepare_opencode_launch(
    adapter: Any,
    *,
    prompt: str,
    model: str,
    cwd: str,
    attachments: list[str],
    allow_mcp: bool,
    isolated: bool,
) -> tuple[Any, str, dict[str, str]]:
    launch, prompt_file = adapter._opencode_family_launch(
        prompt=prompt,
        model=model,
        cwd=cwd,
        attachments=attachments,
        allow_mcp=allow_mcp,
    )
    base_env = os.environ.copy()
    if adapter._provider == "opencode_cli" and not allow_mcp:
        base_env = adapter._opencode_env()
    return launch, prompt_file, adapter._isolated_environment(base_env, isolated)


def prepare_long_prompt_launch(adapter: Any, prompt: str, model: str) -> tuple[Any, str]:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".md",
        prefix="siming-cli-prompt-",
        delete=False,
    ) as handle:
        handle.write(prompt)
        prompt_file = handle.name
    instruction = (
        "Read the complete UTF-8 task prompt from this local file and follow it exactly: "
        f"{prompt_file}"
    )
    return adapter._launch(instruction, model), prompt_file
