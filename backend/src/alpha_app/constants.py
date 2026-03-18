"""constants.py — Fixed facts about the world.

If it doesn't change between deployments, it goes here.
If it contains credentials, it stays in .env.
One file to change, one place to look.
"""

from pathlib import Path

# -- Filesystem ---------------------------------------------------------------

JE_NE_SAIS_QUOI = Path("/Pondside/Alpha-Home/Alpha")
THUMBNAIL_DIR = Path("/Pondside/Alpha-Home/images/thumbnails")
CONTEXT_FILE_NAME = "ALPHA.md"

# Claude subprocess — where it runs and where it stores transcripts.
# /Pondside is the one path guaranteed identical inside and outside Docker.
CLAUDE_CWD = Path("/Pondside")
CLAUDE_CONFIG_DIR = Path("/Pondside/Alpha-Home/.claude")

# -- Network ------------------------------------------------------------------

REDIS_URL = "redis://alpha-pi:6379"
OLLAMA_URL = "http://primer.tail8bd569.ts.net:8200"
PORT = 18010

# -- Models -------------------------------------------------------------------

# The model IS the definition. When we upgrade, we change these and bump the version.
CLAUDE_MODEL = "claude-opus-4-6[1m]"
CONTEXT_WINDOW = 1_000_000

OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_CHAT_MODEL = "qwen3.5:4b"
