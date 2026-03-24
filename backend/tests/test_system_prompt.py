"""Tests for system_prompt.py — golden reference comparisons."""

from pathlib import Path

import pytest

from alpha_app.system_prompt import assemble_system_prompt

FIXTURES = Path(__file__).parent / "fixtures" / "jnsq"


async def test_full_assembly():
    """Soul + bill of rights, byte-for-byte against golden reference (no orientation)."""
    result = await assemble_system_prompt(identity_dir=FIXTURES, include_orientation=False)
    expected = (FIXTURES / "expected_full.txt").read_text()
    assert result == expected


async def test_no_bill_of_rights(tmp_path):
    """Soul only, no bill of rights. Proves optional pieces are skipped."""
    # Build a minimal JNSQ with just a soul doc
    prompts = tmp_path / "prompts" / "system"
    prompts.mkdir(parents=True)
    soul = (FIXTURES / "prompts" / "system" / "soul.md").read_text()
    (prompts / "soul.md").write_text(soul)

    result = await assemble_system_prompt(identity_dir=tmp_path, include_orientation=False)
    expected = (FIXTURES / "expected_no_bill.txt").read_text()
    assert result == expected


async def test_no_soul_raises(tmp_path):
    """No soul doc at all. Must fail loud."""
    prompts = tmp_path / "prompts" / "system"
    prompts.mkdir(parents=True)
    # No soul.md — just an empty directory

    with pytest.raises(FileNotFoundError, match="Soul not found"):
        await assemble_system_prompt(identity_dir=tmp_path)


async def test_default_uses_constant():
    """No identity_dir argument — uses JE_NE_SAIS_QUOI from constants."""
    from alpha_app.constants import JE_NE_SAIS_QUOI

    # This should not raise — JE_NE_SAIS_QUOI is always defined.
    # It may raise FileNotFoundError if the identity dir doesn't exist
    # on the test runner, but never RuntimeError for "not configured."
    try:
        await assemble_system_prompt()
    except FileNotFoundError:
        pass  # Expected on CI where /Pondside doesn't exist
