"""Pydantic schemas for writing statistics."""
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class GoalUpdate(BaseModel):
    """Update daily word goal."""
    daily_word_goal: int = Field(..., ge=0, description="Daily word count target")
