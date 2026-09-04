"""Bounded, lossless pages for model-visible context documents."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# A selected writing context commonly reaches 35k-45k Chinese characters.
# The old 3k/10 KiB envelope forced 12-14 serial tool turns before generation,
# which can exhaust a provider's long-run deadline even though the declared
# prepare_task_context receipt already reserves 32 KiB.  Keep one page well
# inside that receipt while making the normal Chinese page roughly twice as
# large.
CONTEXT_PAGE_DEFAULT_CHARS = 6000
CONTEXT_PAGE_MAX_CHARS = 7000
CONTEXT_PAGE_TEXT_BYTES = 20 * 1024
CONTEXT_PAGE_INPUTS = {
    "content_cursor": {"type": "integer", "minimum": 0, "default": 0,
                       "description": "Unicode code-point offset; use context_page.next_cursor."},
    "content_limit": {"type": "integer", "minimum": 1, "maximum": CONTEXT_PAGE_MAX_CHARS,
                      "default": CONTEXT_PAGE_DEFAULT_CHARS},
    "expected_context_sha256": {"type": "string",
                                "description": "Copy context_page.sha256 when reading another page of the same document."},
}
CONTEXT_DELIVERY_STATE_KEY = "context_delivery"


def build_context_page(text: str, args: dict[str, Any]) -> dict[str, Any]:
    cursor = args.get("content_cursor", 0)
    limit = args.get("content_limit", CONTEXT_PAGE_DEFAULT_CHARS)
    if type(cursor) is not int or cursor < 0 or cursor > len(text):
        raise ValueError("content_cursor must be a Unicode code-point offset within this context document")
    if type(limit) is not int or not 1 <= limit <= CONTEXT_PAGE_MAX_CHARS:
        raise ValueError(f"content_limit must be an integer from 1 to {CONTEXT_PAGE_MAX_CHARS}")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    expected = args.get("expected_context_sha256")
    if expected and expected != digest:
        raise ValueError("Context document changed; restart at content_cursor=0 without the old hash")
    end = min(len(text), cursor + limit)
    if len(json.dumps(text[cursor:end], ensure_ascii=False).encode("utf-8")) > CONTEXT_PAGE_TEXT_BYTES:
        low, high = cursor, end
        while low < high:
            middle = (low + high + 1) // 2
            if len(json.dumps(text[cursor:middle], ensure_ascii=False).encode("utf-8")) <= CONTEXT_PAGE_TEXT_BYTES:
                low = middle
            else:
                high = middle - 1
        end = low
    return {"text": text[cursor:end], "cursor": cursor, "limit": limit,
            "next_cursor": end if end < len(text) else None, "has_more": end < len(text),
            "total_chars": len(text), "sha256": digest, "offset_unit": "unicode_code_points"}


def context_page_arguments(manifest_id: str, task_type: str, page: dict[str, Any]) -> dict[str, Any]:
    return {"context_manifest_id": manifest_id, "task_type": task_type,
            "content_cursor": page["next_cursor"] or 0, "content_limit": page["limit"],
            "expected_context_sha256": page["sha256"]}


def context_delivery_state(manifest: Any) -> dict[str, Any] | None:
    query = manifest.query_json if isinstance(getattr(manifest, "query_json", None), dict) else {}
    value = query.get(CONTEXT_DELIVERY_STATE_KEY)
    return dict(value) if isinstance(value, dict) else None


def set_context_delivery_state(manifest: Any, state: dict[str, Any] | None) -> None:
    query = dict(manifest.query_json or {})
    if state is None:
        query.pop(CONTEXT_DELIVERY_STATE_KEY, None)
    else:
        query[CONTEXT_DELIVERY_STATE_KEY] = dict(state)
    manifest.query_json = query


def context_delivery_status(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {"status": "not_started", "ready": False}
    return {
        key: state.get(key)
        for key in (
            "status",
            "ready",
            "sha256",
            "total_chars",
            "delivered_until",
            "expected_cursor",
            "page_limit",
            "completed_at",
        )
    }


def begin_context_delivery(
    manifest: Any,
    page: dict[str, Any],
    selection_token: str,
) -> dict[str, Any]:
    """Persist that the model-visible receipt delivered exactly the first page."""
    if page.get("cursor") != 0:
        raise ValueError("Selected context delivery must begin at content_cursor=0")
    complete = not bool(page.get("has_more"))
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "status": "complete" if complete else "pending",
        "ready": complete,
        "sha256": str(page.get("sha256") or ""),
        "total_chars": int(page.get("total_chars") or 0),
        "delivered_until": int(page.get("total_chars") or 0)
        if complete
        else int(page.get("next_cursor") or 0),
        "expected_cursor": None if complete else int(page.get("next_cursor") or 0),
        "page_limit": int(page.get("limit") or CONTEXT_PAGE_DEFAULT_CHARS),
        "last_cursor": 0,
        "last_limit": int(page.get("limit") or CONTEXT_PAGE_DEFAULT_CHARS),
        "selection_token_sha256": hashlib.sha256(selection_token.encode("utf-8")).hexdigest(),
        "started_at": now,
        "completed_at": now if complete else None,
    }
    set_context_delivery_state(manifest, state)
    return state


def context_delivery_ready(manifest: Any, selection_token: str) -> bool:
    """Raw full-context callers have no page gate; paged callers must finish it."""
    state = context_delivery_state(manifest)
    if state is None:
        return True
    token_hash = hashlib.sha256(selection_token.encode("utf-8")).hexdigest()
    return (
        state.get("status") == "complete"
        and state.get("ready") is True
        and state.get("selection_token_sha256") == token_hash
    )


def deliver_next_context_page(
    manifest: Any,
    text: str,
    args: dict[str, Any],
    selection_token: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deliver one exact sequential page and atomically advance the persisted gate."""
    state = context_delivery_state(manifest)
    if state is None:
        if args.get("content_cursor", 0) != 0:
            raise ValueError("Selected context delivery must restart at content_cursor=0")
        page = build_context_page(text, args)
        return page, begin_context_delivery(manifest, page, selection_token)

    token_hash = hashlib.sha256(selection_token.encode("utf-8")).hexdigest()
    if state.get("selection_token_sha256") != token_hash:
        raise ValueError("Context selection changed; submit evidence again before reading pages")

    expected_hash = str(state.get("sha256") or "")
    expected_limit = int(state.get("page_limit") or CONTEXT_PAGE_DEFAULT_CHARS)
    if args.get("expected_context_sha256") != expected_hash:
        raise ValueError("expected_context_sha256 must match the active selected context document")
    if args.get("content_limit") != expected_limit:
        raise ValueError(f"content_limit must remain {expected_limit} for this context delivery")

    if state.get("status") == "complete":
        if (
            args.get("content_cursor") != state.get("last_cursor")
            or args.get("content_limit") != state.get("last_limit")
        ):
            raise ValueError("Selected context is already complete; only the final page may be replayed")
        page = build_context_page(text, args)
        if page.get("has_more") or page.get("sha256") != expected_hash:
            raise ValueError("Completed context delivery no longer matches the selected document")
        return page, state

    expected_cursor = state.get("expected_cursor")
    if args.get("content_cursor") != expected_cursor:
        raise ValueError(
            f"context_page delivery is out of order; expected content_cursor={expected_cursor}"
        )
    page = build_context_page(text, args)
    if page.get("sha256") != expected_hash or page.get("total_chars") != state.get("total_chars"):
        raise ValueError("Context document changed; submit evidence again before generation")

    complete = not bool(page.get("has_more"))
    next_state = {
        **state,
        "status": "complete" if complete else "pending",
        "ready": complete,
        "delivered_until": int(page.get("total_chars") or 0)
        if complete
        else int(page.get("next_cursor") or 0),
        "expected_cursor": None if complete else int(page.get("next_cursor") or 0),
        "last_cursor": int(page.get("cursor") or 0),
        "last_limit": int(page.get("limit") or expected_limit),
        "completed_at": datetime.now(timezone.utc).isoformat() if complete else None,
    }
    set_context_delivery_state(manifest, next_state)
    return page, next_state


def compact_context_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep control metadata; exact source content is delivered only in pages."""
    result = {key: payload[key] for key in ("id", "task_type", "status", "budget", "coverage", "warnings") if key in payload}
    result["item_count"] = len(payload.get("items") or [])
    selection = payload.get("selection") or {}
    result["selection"] = {key: selection[key] for key in ("status", "selected_at") if key in selection}
    return result


def context_selection_diagnostics(rejected: list[Any]) -> dict[str, Any]:
    """Expose bounded source-validation reasons without returning source bodies."""
    errors = []
    for row in rejected[:6]:
        if isinstance(row, dict):
            error = {key: str(row[key])[:72] for key in ("item_id", "source_id") if row.get(key)}
            error["reason"] = str(row.get("reason") or "Invalid evidence reference.")[:240]
        else:
            error = {"reason": str(row)[:240]}
        errors.append(error)
    return {"validation_errors": errors, "validation_error_count": len(rejected),
            "validation_errors_has_more": len(rejected) > len(errors)}
