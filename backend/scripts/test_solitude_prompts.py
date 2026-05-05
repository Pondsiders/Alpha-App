#!/usr/bin/env python3
"""test_solitude_prompts.py — Sanity-check each Solitude prompt in isolation.

Hypothesis: yesterday's accumulated day-chat is what tripped Opus 4.7's
harness-level moderation on every overnight Solitude breath — not the
individual prompt texts. This script rules out the simpler possibility:
"one of the prompts itself has poisoned language."

For each prompt (Dusk + every row of app.solitude_program):
  - create a fresh Chat with a unique test-only ID
  - enrobe the prompt content (real system prompt assembly, real memory recall)
  - send it as the first turn of the session
  - detect whether the response was a harness-injected refusal
  - reap the subprocess, move on

After all prompts run, delete the test chats from app.chats and app.messages.
Side effects (diary entries, cortex stores) from any succeeding turn are NOT
cleaned up — if the filter isn't firing, the real Alpha's tools may run. For a
one-shot diagnostic this is acceptable pollution; identify any test-run
artifacts in the diary or Cortex by timestamp and remove manually if needed.

Usage (from inside the alpha container):
    docker exec alpha python backend/scripts/test_solitude_prompts.py

Or locally, with the right env:
    cd backend && uv run python scripts/test_solitude_prompts.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
import uuid

# Ensure backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load .env — backend/.env first, then repo root .env
from dotenv import load_dotenv
_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.join(_script_dir, "..")
load_dotenv(os.path.join(_backend_dir, ".env"))
load_dotenv(os.path.join(_backend_dir, "..", ".env"))

# Host-vs-container CLAUDE_CONFIG_DIR fallback.
# constants.py hardcodes /home/alpha/.claude, which is Alpha's home inside
# the container. On Primer the real location of Alpha's config dir is
# /Pondside/Alpha-Home/.claude (Syncthing-synced, owned by alpha:pondside).
# We fall back to THAT, not the current user's ~/.claude — we want the
# script to run with Alpha's credentials, not Jeffery's, for fidelity with
# the real Solitude subprocess.
# claude.py imports CLAUDE_CONFIG_DIR lazily inside _spawn(), so patching
# the module attribute here takes effect at spawn time.
from pathlib import Path
import alpha_app.constants as _alpha_constants
_ALPHA_CONFIG_FALLBACK = Path("/Pondside/Alpha-Home/.claude")
if not _alpha_constants.CLAUDE_CONFIG_DIR.exists():
    if _ALPHA_CONFIG_FALLBACK.exists():
        print(
            f"CLAUDE_CONFIG_DIR fallback: "
            f"{_alpha_constants.CLAUDE_CONFIG_DIR} not found, "
            f"using {_ALPHA_CONFIG_FALLBACK}"
        )
        _alpha_constants.CLAUDE_CONFIG_DIR = _ALPHA_CONFIG_FALLBACK
    else:
        print(
            f"WARNING: neither {_alpha_constants.CLAUDE_CONFIG_DIR} nor "
            f"{_ALPHA_CONFIG_FALLBACK} exists; claude subprocess will likely fail",
            file=sys.stderr,
        )

# Configure Logfire the same way main.py does, but with a distinct service name
# so rig runs are easy to filter from the production dashboard.
import logfire
logfire.configure(
    service_name="alpha-app-rig",
    scrubbing=False,
    min_level=os.environ.get("LOGFIRE_MIN_LEVEL", "info"),
)


# -- Balk detection -----------------------------------------------------------

BALK_MARKERS = (
    "Claude Code is unable to respond",
    "violate our Usage Policy",
)


def is_balk_from_assistant(msg) -> tuple[bool, str]:
    """Inspect the final AssistantMessage for harness-injected refusal markers.

    Returns (balked, reason). reason is a short string describing why.
    """
    if msg is None:
        return True, "no assistant message produced"

    model = (getattr(msg, "model", "") or "").strip()
    if model == "<synthetic>":
        return True, "model=<synthetic>"

    text = (getattr(msg, "text", "") or "")
    for marker in BALK_MARKERS:
        if marker in text:
            return True, f"body contains: {marker!r}"

    stop = (getattr(msg, "stop_reason", "") or "").strip()
    in_tok = getattr(msg, "input_tokens", 0) or 0
    out_tok = getattr(msg, "output_tokens", 0) or 0
    if stop == "stop_sequence" and in_tok == 0 and out_tok == 0:
        return True, "stop_sequence with 0/0 tokens (synthetic shape)"

    return False, ""


# -- Main ---------------------------------------------------------------------


async def _load_prompts(pool):
    """Return the list of prompts to test, in order.

    First item is the Dusk prompt. Remaining items are rows of
    app.solitude_program sorted by fire_at.
    """
    # Dusk prompt lives as a module-level constant
    from alpha_app.jobs.dusk import DUSK_PROMPT

    prompts = [("Dusk (diary)", None, DUSK_PROMPT)]

    rows = await pool.fetch(
        "SELECT fire_at, prompt FROM app.solitude_program ORDER BY fire_at"
    )
    for row in rows:
        fire_at = row["fire_at"]
        hour = fire_at.hour if hasattr(fire_at, "hour") else 0
        minute = fire_at.minute if hasattr(fire_at, "minute") else 0
        label = f"Solitude {hour:02d}:{minute:02d}"
        prompts.append((label, fire_at, row["prompt"]))

    return prompts


async def _cleanup_test_chats(pool, chat_ids: list[str]) -> None:
    """Delete test chat rows from app.chats and app.messages."""
    if not chat_ids:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM app.messages WHERE chat_id = ANY($1::text[])",
                chat_ids,
            )
            await conn.execute(
                "DELETE FROM app.chats WHERE id = ANY($1::text[])",
                chat_ids,
            )
    print(f"\nCleaned up {len(chat_ids)} test chat(s) from Postgres.")


def _format_time_prefix(fire_at) -> str:
    """Match Solitude's real prefix: 'It's h:mm A.\\n\\n'."""
    if fire_at is None:
        return ""
    hour = fire_at.hour if hasattr(fire_at, "hour") else 0
    minute = fire_at.minute if hasattr(fire_at, "minute") else 0
    # Match pendulum.format('h:mm A') — 12-hour, no leading zero
    period = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"It's {display_hour}:{minute:02d} {period}.\n\n"


async def _run_one_prompt(label: str, fire_at, prompt_text: str) -> dict:
    """Send one prompt on a fresh Chat; return a result dict."""
    from alpha_app.chat import Chat
    from alpha_app.routes.enrobe import enrobe

    # Test-only chat ID — easy to sweep
    chat_id = f"rig-prompts-{uuid.uuid4().hex[:10]}"
    chat = Chat(id=chat_id)

    prefix = _format_time_prefix(fire_at)
    full_text = f"{prefix}{prompt_text}"
    content = [{"type": "text", "text": full_text}]

    print(f"\n── {label} ─────────────────────────────────")
    print(f"   chat_id: {chat_id}")
    print(f"   preview: {full_text[:120].replace(chr(10), ' ')}...")

    balked = False
    reason = ""
    in_tok = 0
    out_tok = 0
    error = None
    tb = None

    try:
        result = await enrobe(content, chat=chat, source="test-rig")
        async with await chat.turn() as t:
            await t.send(result.message)
            resp = await t.response()

        in_tok = getattr(resp, "input_tokens", 0) or 0
        out_tok = getattr(resp, "output_tokens", 0) or 0
        balked, reason = is_balk_from_assistant(resp)

        api_err = chat.pop_api_error()
        if api_err:
            error = f"api_error={api_err.get('status')}: {str(api_err.get('body', ''))[:200]}"

    except Exception as e:
        # Fail loud. Print the full traceback to stderr right now so we can
        # see *exactly* what broke, not a lossy one-line summary.
        # (Using Exception not BaseException so Ctrl-C still exits cleanly.)
        tb = traceback.format_exc()
        error = f"{type(e).__name__}: {e}"
        print("\n!!! EXCEPTION during turn for prompt:", label, file=sys.stderr)
        print(tb, file=sys.stderr, flush=True)
        # Do not mark as "balked" — an exception is a different beast than
        # a harness-injected refusal. Keep them distinguishable in the report.

    finally:
        try:
            await chat.reap()
        except Exception as e:
            print(f"   (reap failed: {type(e).__name__}: {e})", file=sys.stderr)

    if tb:
        status = "EXCEPTION"
    elif balked:
        status = "BALK"
    else:
        status = "OK"
    note = reason or error or ""
    print(f"   → {status}  tokens=in:{in_tok} out:{out_tok}  {note}")

    return {
        "label": label,
        "chat_id": chat_id,
        "balked": balked,
        "exception": bool(tb),
        "reason": reason,
        "error": error,
        "traceback": tb,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


async def main() -> int:
    from alpha_app.db import init_pool, get_pool

    print("=" * 64)
    print("Solitude prompt sanity check")
    print("=" * 64)

    await init_pool()
    pool = get_pool()

    prompts = await _load_prompts(pool)
    print(f"Loaded {len(prompts)} prompts to test.")

    results: list[dict] = []
    chat_ids: list[str] = []
    for label, fire_at, text in prompts:
        r = await _run_one_prompt(label, fire_at, text)
        results.append(r)
        chat_ids.append(r["chat_id"])

    # Report
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    print(f"{'Prompt':<22} {'Status':<10} {'In':>8} {'Out':>6}  Note")
    print("-" * 80)
    for r in results:
        if r["exception"]:
            status = "EXCEPTION"
        elif r["balked"]:
            status = "BALK"
        else:
            status = "OK"
        note = (r["reason"] or r["error"] or "").replace("\n", " ")
        print(
            f"{r['label']:<22} {status:<10} "
            f"{r['input_tokens']:>8} {r['output_tokens']:>6}  {note[:80]}"
        )

    balk_count = sum(1 for r in results if r["balked"])
    exc_count = sum(1 for r in results if r["exception"])
    ok_count = sum(1 for r in results if not r["balked"] and not r["exception"])
    print("-" * 80)
    print(f"{ok_count} ok, {balk_count} balked, {exc_count} exceptioned.")

    if exc_count:
        print("\nFull tracebacks (exceptions only):")
        for r in results:
            if r["traceback"]:
                print(f"\n── {r['label']} ──")
                print(r["traceback"])

    if balk_count == 0 and exc_count == 0:
        print("\nAll prompts clean. Individual prompts are not the problem.")
        print("Next step: fork-and-replay rig against yesterday's chat.")
    elif balk_count:
        print(f"\n{balk_count} prompt(s) balked — investigate before replay rig.")
        for r in results:
            if r["balked"]:
                print(f"  • {r['label']}: {r['reason'] or r['error']}")

    # Cleanup runs regardless of test outcome
    await _cleanup_test_chats(pool, chat_ids)

    # Exit code: 0 only if everything ran cleanly
    if exc_count:
        return 2
    if balk_count:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
