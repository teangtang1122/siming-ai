"""Build local prompt contribution packages for non-git users."""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.database.models import Project
from app.services.content_store import ensure_project_folder


GITHUB_REPO_URL = "https://github.com/teangtang1122/siming-ai"
CONTRIBUTION_SCHEMA_VERSION = "siming.prompt_contribution.v1"


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = slug.strip("-._")
    return slug[:80] or "prompt-pack"


def _shorten(text: str, limit: int = 3000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[已截断，完整内容见投稿包]"


def _prompt_diff(before: str, after: str) -> tuple[str, dict[str, int]]:
    lines = list(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="before_prompt",
        tofile="after_prompt",
        lineterm="",
    ))
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return "\n".join(lines), {
        "added_lines": added,
        "removed_lines": removed,
        "changed_lines": added + removed,
    }


def _issue_url(title: str, body: str) -> str:
    return f"{GITHUB_REPO_URL}/issues/new?{urlencode({'title': title, 'body': body, 'labels': 'prompt-contribution'})}"


def _markdown_body(package: dict[str, Any], diff_text: str) -> str:
    contributor = package["contributor"]
    pack = package["base_pack"]
    return "\n".join([
        f"# 提示词投稿：{pack['title']}",
        "",
        "## 投稿对象",
        f"- pack_id: `{pack['pack_id']}`",
        f"- scope: `{pack['scope']}`",
        f"- base_version: `{pack['version']}`",
        f"- prompt_spec: `{pack.get('prompt_spec_id') or 'legacy'}`",
        f"- base_hash: `{pack.get('base_hash') or '未提供'}`",
        "",
        "## 投稿人",
        f"- 署名：{contributor.get('name') or '未填写'}",
        f"- 联系方式：{contributor.get('contact') or '未填写'}",
        "",
        "## 做了哪些修改",
        package["change_summary"],
        "",
        "## 预期带来什么更好效果",
        package["expected_effect"],
        "",
        "## 本地测试或对比记录",
        package.get("test_notes") or "未填写",
        "",
        "## 变更统计",
        f"- 新增行：{package['diff_stats']['added_lines']}",
        f"- 删除行：{package['diff_stats']['removed_lines']}",
        f"- 变更行合计：{package['diff_stats']['changed_lines']}",
        "",
        "## Diff",
        "```diff",
        diff_text or "无文本差异。",
        "```",
        "",
        "## 修改后的完整提示词",
        "```text",
        package["after_prompt"],
        "```",
        "",
    ])


def _issue_body(package: dict[str, Any], markdown_path: Path, json_path: Path, diff_text: str) -> str:
    pack = package["base_pack"]
    return "\n".join([
        "## 提示词投稿",
        f"- pack_id: `{pack['pack_id']}`",
        f"- scope: `{pack['scope']}`",
        f"- base_version: `{pack['version']}`",
        f"- prompt_spec: `{pack.get('prompt_spec_id') or 'legacy'}`",
        f"- base_hash: `{pack.get('base_hash') or '未提供'}`",
        f"- 投稿人：{package['contributor'].get('name') or '未填写'}",
        "",
        "## 做了哪些修改",
        package["change_summary"],
        "",
        "## 预期更好的效果",
        package["expected_effect"],
        "",
        "## 本地测试或对比记录",
        package.get("test_notes") or "未填写",
        "",
        "## 变更统计",
        f"- 新增行：{package['diff_stats']['added_lines']}",
        f"- 删除行：{package['diff_stats']['removed_lines']}",
        "",
        "## 投稿包",
        "这是由 Siming exe 生成的投稿包。完整提示词和完整 diff 已保存在本地文件，请在提交 issue 后把 Markdown 或 JSON 内容贴上/作为附件上传。",
        f"- Markdown: `{markdown_path}`",
        f"- JSON: `{json_path}`",
        "",
        "## Diff 预览",
        "```diff",
        _shorten(diff_text or "无文本差异。", 2500),
        "```",
    ])


def build_prompt_contribution_package(
    db: Session,
    project: Project,
    *,
    pack_detail: dict[str, Any],
    edited_system_prompt: str,
    change_summary: str,
    expected_effect: str,
    test_notes: str | None = None,
    contributor_name: str | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    """Write a prompt contribution JSON/Markdown pair under the project folder."""
    before_prompt = str(pack_detail.get("system_prompt") or "")
    after_prompt = edited_system_prompt
    diff_text, diff_stats = _prompt_diff(before_prompt, after_prompt)
    prompt_spec = pack_detail.get("prompt_spec")
    if not isinstance(prompt_spec, dict):
        prompt_spec = {}
    now = datetime.utcnow().replace(microsecond=0)
    package = {
        "schema_version": CONTRIBUTION_SCHEMA_VERSION,
        "created_at": now.isoformat() + "Z",
        "repository": GITHUB_REPO_URL,
        "project": {
            "id": project.id,
            "title": project.title,
        },
        "base_pack": {
            "pack_id": pack_detail.get("pack_id"),
            "title": pack_detail.get("title"),
            "scope": pack_detail.get("scope"),
            "version": pack_detail.get("version"),
            "summary": pack_detail.get("summary"),
            "prompt_spec_id": prompt_spec.get("prompt_spec_id"),
            "prompt_spec_version": prompt_spec.get("prompt_spec_version"),
            "base_hash": prompt_spec.get("prompt_spec_hash"),
        },
        "contributor": {
            "name": (contributor_name or "").strip() or None,
            "contact": (contact or "").strip() or None,
        },
        "change_summary": change_summary.strip(),
        "expected_effect": expected_effect.strip(),
        "test_notes": (test_notes or "").strip() or None,
        "diff_stats": diff_stats,
        "before_prompt": before_prompt,
        "after_prompt": after_prompt,
        "diff": diff_text,
    }

    project_folder = ensure_project_folder(db, project)
    out_dir = project_folder / ".siming" / "prompt-contributions"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug(str(pack_detail.get("pack_id") or "prompt-pack"))
    stem = f"{now.strftime('%Y%m%d-%H%M%S')}-{slug}"
    json_path = out_dir / f"{stem}.json"
    markdown_path = out_dir / f"{stem}.md"
    markdown = _markdown_body(package, diff_text)

    json_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    markdown_path.write_text(markdown, encoding="utf-8", newline="\n")

    issue_title = f"提示词投稿：{pack_detail.get('title') or pack_detail.get('pack_id')}"
    issue_body = _issue_body(package, markdown_path, json_path, diff_text)
    return {
        "package": package,
        "markdown": markdown,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "github_issue_url": _issue_url(issue_title, issue_body),
        "github_issue_title": issue_title,
        "github_issue_body": issue_body,
    }
