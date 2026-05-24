"""Web search tool — queries the internet via DuckDuckGo."""
from __future__ import annotations

from typing import Any

from ddgs import DDGS
from sqlalchemy.orm import Session


async def web_search(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Search the web and return results with title, URL, and snippet.

    Arguments: query (required), max_results (default 5, max 10).
    """
    query = str(args.get("query") or "").strip()
    if not query:
        return {"tool": "web_search", "status": "error", "detail": "query 参数不能为空"}

    max_results = max(1, min(int(args.get("max_results") or 5), 10))
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
    except Exception as exc:
        return {"tool": "web_search", "status": "error", "detail": f"搜索失败：{exc}"}

    results = []
    for item in raw:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "snippet": item.get("body", ""),
        })
    return {
        "tool": "web_search",
        "status": "ok",
        "detail": f"共 {len(results)} 条结果",
        "data": results,
    }
