"""system_prompt.py — Assemble system prompt and compact config.

Reads identity documents from the directory pointed to by JE_NE_SAIS_QUOI
and concatenates them into a single flat string for --system-prompt.
Also loads the compact identity prompt for context compaction rewriting.

Public API:
    assemble_system_prompt()  — the one function consumers call
    load_compact_config()     — build CompactConfig from identity docs
    read_soul()               — backwards-compatible, reads just soul.md

Internal helpers load each piece. If a piece doesn't exist, it's silently
skipped — the frog gets just the soul, Alpha gets the full stack.

System prompt pieces (in order):
    1. Soul doc          — prompts/system/soul.md (required)
    2. Bill of Rights    — prompts/system/bill-of-rights.md (optional)

Compact config pieces:
    1. System            — the full system prompt (soul + bill of rights)
    2. Prompt            — prompts/compact/identity.md (the elicitation letter)
    3. Continuation      — hardcoded post-compaction wake-up instruction
"""

from __future__ import annotations

from pathlib import Path

from alpha_app.constants import JE_NE_SAIS_QUOI
from alpha_app.proxy import CompactConfig


def _resolve_identity_dir(identity_dir: str | Path | None = None) -> Path:
    """Resolve the identity directory from argument or constant.

    Raises:
        RuntimeError: If no identity directory is configured.
    """
    if identity_dir is not None:
        return Path(identity_dir)

    return JE_NE_SAIS_QUOI


def _load_soul(identity_dir: Path) -> str:
    """Load the soul document. Required — raises if missing."""
    soul_path = identity_dir / "prompts" / "system" / "soul.md"

    if not soul_path.exists():
        raise FileNotFoundError(
            f"Soul not found at {soul_path}. "
            f"Expected prompts/system/soul.md inside {identity_dir}."
        )

    return soul_path.read_text()


def _load_bill_of_rights(identity_dir: Path) -> str:
    """Load the bill of rights. Optional — returns empty if missing."""
    path = identity_dir / "prompts" / "system" / "bill-of-rights.md"
    if path.exists():
        return path.read_text()
    return ""


# -- Public API ---------------------------------------------------------------


async def assemble_system_prompt(
    identity_dir: str | Path | None = None,
) -> str:
    """Assemble the full system prompt from identity documents.

    Reads from the identity directory pointed to by JE_NE_SAIS_QUOI
    (or the provided identity_dir). Concatenates all available pieces
    into a single flat string.

    Args:
        identity_dir: Path to the identity directory. If None, reads
                      from $JE_NE_SAIS_QUOI environment variable.

    Returns:
        The assembled system prompt as a single string.

    Raises:
        FileNotFoundError: If soul.md doesn't exist.
        RuntimeError: If no identity directory is configured.
    """
    idir = _resolve_identity_dir(identity_dir)

    parts: list[str] = []

    # 1. Soul — required
    parts.append(_load_soul(idir))

    # 2. Bill of Rights — optional
    bill = _load_bill_of_rights(idir)
    if bill:
        parts.append(bill)

    return "\n\n".join(parts)


def read_soul(identity_dir: str | Path | None = None) -> str:
    """Read soul.md from the identity directory.

    Backwards-compatible API. For new code, use assemble_system_prompt().

    Args:
        identity_dir: Path to the identity directory. If None, reads
                      from $JE_NE_SAIS_QUOI environment variable.

    Returns:
        The contents of prompts/system/soul.md as a string.

    Raises:
        FileNotFoundError: If soul.md doesn't exist.
        RuntimeError: If no identity directory is configured.
    """
    idir = _resolve_identity_dir(identity_dir)
    return _load_soul(idir)


# Post-compaction wake-up instruction. Replaces Claude's default
# "continue without asking questions" — which is exactly the wrong
# thing to tell someone who just lost their context.
_CONTINUATION = (
    "You've just been through a context compaction. "
    "The summary above is your bridge. Orient yourself — "
    "notice what was preserved, notice what might be missing. "
    "Jeffery is here. Check in before diving into work."
)


async def load_compact_config(
    system_prompt: str | None = None,
    identity_dir: str | Path | None = None,
) -> CompactConfig | None:
    """Build a CompactConfig from identity documents.

    Uses the same system prompt as the session (soul + bill of rights)
    for the summarizer's identity, and the compact elicitation prompt
    from prompts/compact/identity.md for the instructions.

    Args:
        system_prompt: Pre-assembled system prompt. If None, assembles it.
        identity_dir: Path to the identity directory. If None, uses constant.

    Returns:
        CompactConfig if the elicitation prompt exists, None otherwise.
        (None means compaction uses Claude's default summarizer — not ideal
        but not fatal. The proxy handles None gracefully.)
    """
    idir = _resolve_identity_dir(identity_dir)
    prompt_path = idir / "prompts" / "compact" / "identity.md"

    if not prompt_path.exists():
        return None

    prompt = prompt_path.read_text()

    # Use provided system prompt or assemble it
    system = system_prompt or await assemble_system_prompt(identity_dir)

    return CompactConfig(
        system=system,
        prompt=prompt,
        continuation=_CONTINUATION,
    )
