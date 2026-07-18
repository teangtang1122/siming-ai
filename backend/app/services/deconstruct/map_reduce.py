"""LLM-calling async functions for the deconstruct map-reduce pipeline."""
import asyncio
from typing import AsyncGenerator, Optional

from ...modules.model_runtime.application.execution import model_executor as LLMGateway
from ...prompts.deconstruct import (
    JSON_REPAIR_SYSTEM_PROMPT,
    MAP_JSON_TEMPLATE,
    REDUCE_SECTION_INSTRUCTIONS,
    REDUCE_SECTION_LABELS,
    REDUCE_SECTION_TEMPLATES,
    REDUCE_SYSTEM_PROMPT,
)
from ..deconstruct.json_repair import parse_model_json
from .constants import (
    JSON_REPAIR_TIMEOUT_SECONDS,
    MAP_PARSE_RETRIES,
    MAP_STREAM_IDLE_TIMEOUT_SECONDS,
    MAP_TIMEOUT_SECONDS,
    REDUCE_MAX_TOKENS,
    REDUCE_PARSE_RETRIES,
    REDUCE_TIMEOUT_SECONDS,
)
from .model_selection import map_output_limit_for, model_limits_for
from .pipeline import (
    build_map_messages,
    default_reduce_result,
    merge_reduce_section,
    reduce_section_keys,
    reduce_source_text,
    sanitize_reduce_section_output,
)


async def repair_json_output(raw_text: str, model: Optional[str]) -> tuple[Optional[dict], Optional[str], str]:
    """Use a short LLM call to repair malformed JSON without re-reading source text."""
    if not raw_text.strip():
        return None, "empty_response", ""
    messages = [
        {"role": "system", "content": JSON_REPAIR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "请修复下面这段JSON，使其符合事实卡片模板。"
                "不要重新分析小说，不要补充新事实；如果某个字段损坏严重，可以删除该字段值或置为空数组。"
                "字段模板：\n"
                f"{MAP_JSON_TEMPLATE}\n\n"
                "待修复内容：\n"
                f"{raw_text[:12000]}"
            ),
        },
    ]
    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0,
            max_tokens=map_output_limit_for(model),
            timeout=JSON_REPAIR_TIMEOUT_SECONDS,
            retry=1,
            extra_body={"thinking": {"type": "disabled"}} if model and "deepseek" in model.lower() else None,
        )
    except Exception as exc:
        return None, "repair_failed", str(exc)

    repaired_text = result.get("content", "") or ""
    parsed, error = parse_model_json(repaired_text)
    if parsed is not None:
        parsed["_json_llm_repaired"] = True
        return parsed, None, repaired_text
    return None, error or "repair_failed", repaired_text


async def map_chunk(chunk: str, index: int, model: Optional[str], options: dict) -> dict:
    """Map phase: analyze a single text chunk."""
    last_raw = ""
    last_error = "parse_failed"
    for attempt in range(MAP_PARSE_RETRIES):
        retry_tip = ""
        if attempt > 0:
            if last_error == "truncated_json":
                retry_tip = (
                    "\n\n上一轮JSON输出不完整（被截断）。请大幅缩短每条字符串，"
                    "减少 characters 和 events 数量，确保JSON以 } 完整结束。"
                )
            elif last_error == "empty_response":
                retry_tip = (
                    "\n\n上一轮没有输出任何内容。请严格按模板输出一个紧凑的JSON对象，"
                    "即使内容很少也必须返回完整结构。"
                )
            else:
                retry_tip = (
                    "\n\n上一轮输出无法解析为合法JSON。请检查是否有多余逗号、"
                    "中文引号或未转义字符，重新输出一个更短、更紧凑、完整闭合的JSON对象。"
                )
        messages = build_map_messages(chunk, index, options, retry_tip)
        try:
            result = await LLMGateway.chat_completion(
                messages=messages,
                model=model,
                temperature=0.0 if attempt > 0 else 0.1,
                max_tokens=map_output_limit_for(model),
                timeout=MAP_TIMEOUT_SECONDS,
                retry=1,
                extra_body={"thinking": {"type": "disabled"}} if model and "deepseek" in model.lower() else None,
            )
        except Exception as exc:
            error_text = str(exc)
            error_code = "timeout" if "超时" in error_text or "timeout" in error_text.lower() else "llm_failed"
            return {"_raw": error_text, "_error": error_code}
        text_result = result.get("content", "") or ""
        if text_result.strip():
            last_raw = text_result
        parsed, error = parse_model_json(text_result)
        if parsed is not None:
            if attempt > 0:
                parsed["_retry_count"] = attempt
            return parsed
        if text_result.strip():
            repaired, repair_error, repair_raw = await repair_json_output(text_result, model)
            if repaired is not None:
                if attempt > 0:
                    repaired["_retry_count"] = attempt
                return repaired
            last_error = repair_error or error or "parse_failed"
            if repair_raw.strip():
                last_raw = repair_raw
        else:
            last_error = error or "empty_response"
        await asyncio.sleep(0.4 * (attempt + 1))
    return {"_raw": last_raw, "_error": last_error}


async def stream_map_chunk(
    chunk: str,
    index: int,
    model: Optional[str],
    options: dict,
) -> AsyncGenerator[dict, None]:
    """Stream one map chunk and parse only after the chunk stream is complete."""
    last_raw = ""
    last_error = "parse_failed"
    for attempt in range(MAP_PARSE_RETRIES):
        retry_tip = ""
        if attempt > 0:
            if last_error == "truncated_json":
                retry_tip = (
                    "\n\n上一轮JSON输出不完整（被截断）。请大幅缩短每条字符串，"
                    "减少 characters 和 events 数量，确保JSON以 } 完整结束。"
                )
            elif last_error == "empty_response":
                retry_tip = (
                    "\n\n上一轮没有输出任何内容。请严格按模板输出一个紧凑的JSON对象，"
                    "即使内容很少也必须返回完整结构。"
                )
            else:
                retry_tip = (
                    "\n\n上一轮输出无法解析为合法JSON。请检查是否有多余逗号、"
                    "中文引号或未转义字符，重新输出一个更短、更紧凑、完整闭合的JSON对象。"
                )
            yield {"type": "retry", "index": index, "attempt": attempt + 1, "error": last_error}

        full_text = ""
        try:
            gen = LLMGateway.stream_chat_completion(
                messages=build_map_messages(chunk, index, options, retry_tip),
                model=model,
                temperature=0.0 if attempt > 0 else 0.1,
                max_tokens=map_output_limit_for(model),
                timeout=MAP_STREAM_IDLE_TIMEOUT_SECONDS,
                retry=1,
                extra_body={"thinking": {"type": "disabled"}} if model and "deepseek" in model.lower() else None,
            )
            async for token in gen:
                full_text += token
                yield {"type": "token", "index": index, "content": token}
        except Exception as exc:
            error_text = str(exc)
            error_code = "timeout" if "超时" in error_text or "timeout" in error_text.lower() else "llm_failed"
            yield {"type": "result", "index": index, "result": {"_raw": error_text, "_error": error_code}}
            return

        if full_text.strip():
            last_raw = full_text
        parsed, error = parse_model_json(full_text)
        if parsed is not None:
            if attempt > 0:
                parsed["_retry_count"] = attempt
            yield {"type": "result", "index": index, "result": parsed}
            return

        if full_text.strip():
            yield {"type": "repair_start", "index": index, "error": error or "parse_failed"}
            repaired, repair_error, repair_raw = await repair_json_output(full_text, model)
            if repaired is not None:
                if attempt > 0:
                    repaired["_retry_count"] = attempt
                yield {"type": "repair_done", "index": index, "status": "success"}
                yield {"type": "result", "index": index, "result": repaired}
                return
            last_error = repair_error or error or "parse_failed"
            if repair_raw.strip():
                last_raw = repair_raw
            yield {"type": "repair_done", "index": index, "status": "failed", "error": last_error}
        else:
            last_error = error or "empty_response"
        await asyncio.sleep(0.4 * (attempt + 1))

    yield {"type": "result", "index": index, "result": {"_raw": last_raw, "_error": last_error}}


async def reduce_section(
    section_key: str,
    map_results: list[dict],
    title: str,
    total_words: int,
    model: Optional[str],
    options: dict,
    golden_text: str = "",
) -> dict:
    template = REDUCE_SECTION_TEMPLATES[section_key]
    section_instruction = REDUCE_SECTION_INSTRUCTIONS[section_key]
    limits = model_limits_for(model)
    source_text = reduce_source_text(map_results, section_key, limits)
    golden_section = ""
    if section_key == "golden_three" and golden_text.strip():
        golden_section = f"\n\n前三章原文摘录：\n{golden_text[:16000]}\n"

    last_raw = ""
    last_error = "reduce_parse_failed"
    for attempt in range(REDUCE_PARSE_RETRIES):
        retry_tip = ""
        if attempt > 0:
            retry_tip = "\n\n上一轮输出不是合法JSON。请缩短内容，只返回合法JSON对象。"
        messages = [
            {"role": "system", "content": REDUCE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"作品标题：{title}\n"
                    f"总字数：{total_words}\n"
                    f"合并分项：{REDUCE_SECTION_LABELS.get(section_key, section_key)}\n\n"
                    f"分块事实卡片：\n{source_text}\n"
                    f"{golden_section}\n"
                    f"{section_instruction}\n"
                    "必须只输出一个合法JSON对象，不要Markdown，不要解释。\n"
                    f"输出模板：\n{template}{retry_tip}"
                ),
            },
        ]
        try:
            result = await LLMGateway.chat_completion(
                messages=messages,
                model=model,
                temperature=0.3 if attempt > 0 else 0.4,
                max_tokens=limits.max_output_tokens or REDUCE_MAX_TOKENS,
                timeout=REDUCE_TIMEOUT_SECONDS,
                retry=1,
            )
        except Exception as exc:
            error_text = str(exc)
            error_code = "reduce_timeout" if "超时" in error_text or "timeout" in error_text.lower() else "reduce_failed"
            return {"_raw": error_text, "_error": error_code}
        text_result = result.get("content", "") or ""
        if text_result.strip():
            last_raw = text_result
        parsed, error = parse_model_json(text_result)
        if parsed is not None:
            return sanitize_reduce_section_output(section_key, parsed, limits)
        last_error = "empty_reduce_response" if error == "empty_response" else "reduce_parse_failed"
        await asyncio.sleep(0.5 * (attempt + 1))
    return {"_raw": last_raw, "_error": last_error}


async def reduce_sections(
    map_results: list[dict],
    title: str,
    total_words: int,
    model: Optional[str],
    options: dict,
    golden_text: str = "",
    on_section=None,
) -> dict:
    combined = default_reduce_result(options)
    for section_key in reduce_section_keys(options):
        if on_section:
            await on_section("start", section_key, None)
        section_data = await reduce_section(section_key, map_results, title, total_words, model, options, golden_text)
        if section_data.get("_error"):
            combined["reduce_errors"][section_key] = section_data.get("_error")
            if on_section:
                await on_section("error", section_key, section_data)
        else:
            merge_reduce_section(combined, section_key, section_data, options)
            combined["reduce_sections"].append(section_key)
            if on_section:
                await on_section("complete", section_key, section_data)
    if combined["reduce_errors"]:
        combined["_error"] = "partial_reduce_failed"
    return combined


async def reduce_analysis(
    map_results: list[dict],
    title: str,
    total_words: int,
    model: Optional[str],
    options: dict,
    golden_text: str = "",
) -> dict:
    """Reduce phase: combine chunk fact cards by section, then assemble locally."""
    return await reduce_sections(map_results, title, total_words, model, options, golden_text)
