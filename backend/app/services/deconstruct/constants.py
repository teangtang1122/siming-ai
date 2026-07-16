"""Constants for the deconstruct map-reduce pipeline."""
CHUNK_SIZE = 2400  # characters per chunk for map phase
CHAPTER_CHUNK_THRESHOLD = 6000
CHAPTER_SUB_CHUNK_SIZE = 3000
DEFAULT_MAP_CONCURRENCY = 4
MAX_MAP_CONCURRENCY = 12
MAP_TIMEOUT_SECONDS = 0
MAP_STREAM_IDLE_TIMEOUT_SECONDS = 0
MAP_MAX_TOKENS = 16000
MAP_PARSE_RETRIES = 2
JSON_REPAIR_TIMEOUT_SECONDS = 60
REDUCE_TIMEOUT_SECONDS = 0
REDUCE_MAX_TOKENS = 16000
REDUCE_PARSE_RETRIES = 2
REDUCE_INPUT_MAX_CHARS = 120000
REDUCE_BRIEF_MIN_CHARS_PER_CHUNK = 80
REDUCE_BRIEF_MAX_CHARS_PER_CHUNK = 420
FINAL_OUTPUT_ARRAY_MAX_ITEMS = 400

CHEAP_MODEL_BY_PROVIDER = {
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "qwen": "qwen-turbo",
    "gemini": "gemini-2.5-flash-lite",
}

REDUCE_INPUT_PROFILES = [
    {
        "name": "normal",
        "characters": 8, "character_actions": 4, "character_traits": 4,
        "events": 8, "event_characters": 6, "world_facts": 6,
        "clues": 5, "themes": 5, "techniques": 5,
        "short": 48, "text": 120, "evidence": 100,
    },
    {
        "name": "compact",
        "characters": 6, "character_actions": 3, "character_traits": 3,
        "events": 5, "event_characters": 5, "world_facts": 4,
        "clues": 3, "themes": 4, "techniques": 4,
        "short": 36, "text": 84, "evidence": 72,
    },
    {
        "name": "tiny",
        "characters": 4, "character_actions": 2, "character_traits": 2,
        "events": 3, "event_characters": 4, "world_facts": 2,
        "clues": 2, "themes": 3, "techniques": 3,
        "short": 28, "text": 56, "evidence": 48,
    },
    {
        "name": "micro",
        "characters": 3, "character_actions": 1, "character_traits": 1,
        "events": 2, "event_characters": 3, "world_facts": 1,
        "clues": 1, "themes": 2, "techniques": 2,
        "short": 24, "text": 40, "evidence": 32,
    },
]

REDUCE_SECTION_SOURCE_FIELDS = {
    "plot_highlights": {"characters", "events", "clues", "themes", "pacing", "narrative_mode"},
    "outline": {"characters", "events", "clues", "themes"},
    "characters": {"characters", "events"},
    "worldbuilding": {"world_facts", "events", "characters"},
    "rhythm_patterns": {"events", "pacing", "narrative_mode", "themes", "techniques"},
    "golden_three": {"characters", "events", "clues", "pacing", "narrative_mode", "themes", "techniques"},
}

WORLD_DIMENSIONS = {"geography", "history", "factions", "power_system", "races", "culture"}
