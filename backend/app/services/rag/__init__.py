"""RAG: full-text retrieval and budget-aware context packing."""
from .indexer import detect_fts5_available, index_document, reindex_project, reindex_project_types, mark_dirty, ensure_indexed, project_has_chunks, refresh_source_index, delete_source_index
from .retriever import search_chunks, get_chunks_for_source, get_chunk_by_id, SearchResult
from .context_packer import pack_context, ContextBudget, ContextSection, PackedContext

__all__ = [
    "detect_fts5_available",
    "index_document",
    "reindex_project",
    "mark_dirty",
    "ensure_indexed",
    "refresh_source_index",
    "delete_source_index",
    "search_chunks",
    "get_chunks_for_source",
    "get_chunk_by_id",
    "SearchResult",
    "pack_context",
    "ContextBudget",
    "ContextSection",
    "PackedContext",
]
