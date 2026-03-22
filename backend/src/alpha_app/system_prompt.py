"""system_prompt.py — Assemble system prompt from identity documents.

Reads identity documents from the directory pointed to by JE_NE_SAIS_QUOI
and concatenates them into a single flat string for --system-prompt.

Public API:
    assemble_system_prompt()  — the one function consumers call
    read_soul()               — backwards-compatible, reads just soul.md

Internal helpers load each piece. If a piece doesn't exist, it's silently
skipped — the frog gets just the soul, Alpha gets the full stack.

System prompt pieces (in order):
    1. Soul doc          — prompts/system/soul.md (required)
    2. Bill of Rights    — prompts/system/bill-of-rights.md (optional)
"""

from __future__ import annotations

from pathlib import Path

from alpha_app.constants import JE_NE_SAIS_QUOI


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
