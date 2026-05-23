"""Pydantic schemas for AI writing engine endpoints."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class WorkspaceAssistantRequest(BaseModel):
    """Conversational assistant for project planning modules."""

    scope: Literal["outline", "characters", "worldbuilding", "project"] = Field(..., description="Management scope")
    message: str = Field(..., min_length=1)
    conversation_id: Optional[str] = None
    selected_outline_node_id: Optional[str] = None
    selected_character_id: Optional[str] = None
    selected_text: Optional[str] = Field(None, description="User-selected text in the editor")
    selected_text_chapter_id: Optional[str] = Field(None, description="Chapter ID the selected text belongs to")
    model: Optional[str] = None
    temperature: Optional[float] = Field(0.3, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    outline_batch_count: int = Field(3, ge=1, le=12, description="Preferred number of consecutive outline chapters to plan")
    auto_apply: bool = Field(True, description="Apply tool actions proposed by the model")
    history: list[dict] = Field(default_factory=list)
