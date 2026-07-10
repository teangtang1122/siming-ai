"""Schemas for prompt-pack contribution exports."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PromptContributionCreate(BaseModel):
    """Request body for exporting a prompt contribution package."""

    pack_id: str = Field(..., min_length=1, max_length=100)
    base_version: Optional[str] = Field(default=None, max_length=40)
    edited_system_prompt: str = Field(..., min_length=1)
    change_summary: str = Field(..., min_length=8, max_length=4000)
    expected_effect: str = Field(..., min_length=8, max_length=4000)
    test_notes: Optional[str] = Field(default=None, max_length=4000)
    contributor_name: Optional[str] = Field(default=None, max_length=120)
    contact: Optional[str] = Field(default=None, max_length=200)
