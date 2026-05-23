"""Pydantic schemas for deconstruct/book analysis."""
from typing import Optional
from pydantic import BaseModel, Field


class DeconstructRequest(BaseModel):
    """Request for deconstruct analysis."""
    text: Optional[str] = Field(None, min_length=100, description="Full text of the novel/chapter to analyze")
    chapter_ids: list[str] = Field(default_factory=list, description="Existing chapters to analyze in order")
    title: Optional[str] = Field(None, max_length=200, description="Title for reference")
    model: Optional[str] = Field(None, description="Model override")
    map_model: Optional[str] = Field(None, description="Fast model for chunk fact-card extraction")
    reduce_model: Optional[str] = Field(None, description="Strong model for final merge and detailed assets")
    analysis_mode: str = Field("fast", description="Analysis mode: fast or detailed")
    include_golden_three: bool = Field(True, description="Analyze golden first three chapters")
    include_characters: bool = Field(True, description="Extract import-ready character profiles")
    include_outline: bool = Field(True, description="Extract import-ready outline structure")
    include_worldbuilding: bool = Field(True, description="Extract import-ready worldbuilding entries")
    include_rhythm: bool = Field(True, description="Include narrative rhythm analysis")
    include_patterns: bool = Field(True, description="Include writing pattern analysis")
    map_concurrency: int = Field(4, ge=1, le=12, description="Concurrent chunk analysis requests")


class DeconstructImportRequest(BaseModel):
    """Request to import selected deconstruct report sections into a project."""

    import_outline: bool = Field(False, description="Import extracted outline nodes")
    import_characters: bool = Field(False, description="Import extracted characters")
    import_worldbuilding: bool = Field(False, description="Import extracted worldbuilding entries")


