"""Alpha — the duck in the machine."""

# Load .env BEFORE any submodule import that reaches constants.py.
# constants.py reads JE_NE_SAIS_QUOI_OVERRIDE from os.environ at module
# import time — if dotenv hasn't loaded yet, the override silently doesn't
# take effect and JE_NE_SAIS_QUOI freezes with the default. Docker case is
# unaffected: load_dotenv is a no-op when the file doesn't exist, and by
# default it doesn't override env vars that are already set.
from pathlib import Path as _Path
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_Path(__file__).resolve().parents[3] / ".env")

from .claude import (
    AssistantEvent,
    Claude,
    ClaudeState,
    ErrorEvent,
    Event,
    InitEvent,
    ResultEvent,
    StreamEvent,
    SystemEvent,
    UserEvent,
    replay_session,
)
from .system_prompt import assemble_system_prompt, read_soul

__all__ = [
    # The one class
    "Claude",
    "ClaudeState",
    # Events
    "Event",
    "InitEvent",
    "UserEvent",
    "AssistantEvent",
    "ResultEvent",
    "SystemEvent",
    "ErrorEvent",
    "StreamEvent",
    # Replay
    "replay_session",
    # System prompt
    "assemble_system_prompt",
    "read_soul",
]
