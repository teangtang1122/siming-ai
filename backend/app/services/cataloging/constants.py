"""Constants for the project cataloging pipeline."""

JOB_RUNNING_STATUSES = {"queued", "running", "waiting_confirmation", "paused", "paused_on_failure"}

APPLY_ORDER = {
    "chapter_summary": 10,
    "outline_create": 20,
    "outline_update": 21,
    "character_create": 12,
    "character_update": 13,
    "character_state_update": 14,
    "character_timeline": 15,
    "character_relationship": 16,
    "character_merge_candidate": 17,
    "worldbuilding_create": 40,
    "worldbuilding_update": 41,
    "worldbuilding_timeline": 42,
    "chapter_link": 50,
}

from ..story_granularity import VALID_CANDIDATE_TYPES
from ..story_granularity import WORLD_DIMENSIONS  # noqa: F401 - compatibility export

VALID_ITEM_TYPES = set(VALID_CANDIDATE_TYPES)

CATALOGING_MAX_TOKENS = 20000
CATALOGING_TIMEOUT_SECONDS = 300
CATALOGING_TEMPERATURE = 0.1
CATALOGING_NON_THINKING_PROVIDERS = frozenset({"deepseek"})
CATALOGING_STAGE_MAX_ATTEMPTS = 3
