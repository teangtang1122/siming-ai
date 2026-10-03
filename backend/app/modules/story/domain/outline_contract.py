"""Authoritative outline-planning limits shared by every PC execution route."""

OUTLINE_PROPOSAL_MAX_NODES = 12
DEFAULT_OUTLINE_BATCH_COUNT = 1

# This is an inactivity limit, not a deadline for a complete generated outline.
OUTLINE_GENERATION_IDLE_TIMEOUT_SECONDS = 180
OUTLINE_GENERATION_LOCAL_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


__all__ = [
    "OUTLINE_PROPOSAL_MAX_NODES",
    "DEFAULT_OUTLINE_BATCH_COUNT",
    "OUTLINE_GENERATION_IDLE_TIMEOUT_SECONDS",
    "OUTLINE_GENERATION_LOCAL_EXTRA_BODY",
]
