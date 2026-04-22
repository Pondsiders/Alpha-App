"""constants.py — Fixed facts about the world.

If it doesn't change between deployments, it goes here.
If it contains credentials, it stays in .env.
One file to change, one place to look.
"""

import os
from pathlib import Path

# -- Filesystem ---------------------------------------------------------------

JE_NE_SAIS_QUOI = Path("/Pondside/Alpha-Home/Alpha")
THUMBNAIL_DIR = Path("/Pondside/Alpha-Home/images/thumbnails")
CONTEXT_FILE_NAME = "ALPHA.md"

# Claude subprocess — where it runs and where it stores transcripts.
# /Pondside is the one path guaranteed identical inside and outside Docker.
CLAUDE_CWD = Path("/Pondside")
# CLAUDE_CONFIG_DIR defaults to /Pondside/Alpha-Home/.claude — real on the host,
# and mapped to /home/alpha/.claude inside the container via bind mount. Works
# in both deployment modes. Override via env var if another rig needs a different
# location. (See memory #16963 for the "nonexistent /home/alpha on bare-metal"
# landmine this replaces.)
CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", "/Pondside/Alpha-Home/.claude"))

# -- Network ------------------------------------------------------------------

REDIS_URL = "redis://alpha-pi.tail8bd569.ts.net:6379"
GARAGE_ENDPOINT = "http://127.0.0.1:3900"
GARAGE_BUCKET = "pondside"
GARAGE_REGION = "pondside"
PORT = int(os.environ.get("PORT", "18010"))

# Inference endpoint: configured via OPENAI_BASE_URL + OPENAI_API_KEY in .env.
# The AsyncOpenAI client in inference_client.py reads these automatically.
# Any OpenAI-compatible endpoint works (Ollama /v1, llmster, Harbormaster,
# llama-server on Modal, OpenAI itself).

# -- Models -------------------------------------------------------------------

# The model IS the definition. When we upgrade, we change these and bump the version.
CLAUDE_MODEL = "claude-opus-4-7[1m]"
CONTEXT_WINDOW = 1_000_000

EMBED_MODEL = "text-embedding-qwen3-embedding-4b"
CHAT_MODEL = "unsloth/qwen3.5-4b"
CHAT_MODEL_CONTEXT = int(os.environ.get("CHAT_MODEL_CONTEXT", "16384"))

# -- Disallowed tools ---------------------------------------------------------
# Claude Code tools that don't apply in Alpha-App. Removed from the model's
# context entirely via --disallowedTools. Everything else stays available —
# "I don't want allowed tools to exist as a concept" (Feb 21, 2026).
DISALLOWED_TOOLS = [
    "EnterPlanMode",       # Got us stuck Feb 12 — no plan-mode UI
    "ExitPlanMode",        # Meaningless without plan mode
    "AskUserQuestion",     # Multi-choice widget doesn't render in our frontend
    "EnterWorktree",       # CC terminal feature, never used
    "ExitWorktree",        # Same
    "CronCreate",          # CC's in-memory cron — NOT our scheduler, confusing
    "CronDelete",          # Same
    "CronList",            # Same
    "RemoteTrigger",       # Claude.ai remote control, irrelevant
    "NotebookEdit",        # No notebooks — add back if needed
]
