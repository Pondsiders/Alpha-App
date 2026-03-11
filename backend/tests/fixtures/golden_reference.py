"""Golden reference fixtures for user message shapes.

Derived from a real Duckpond capture (20260311_125224_063527). The SHAPE
matters — block order, header patterns, count — not the content. All text
is synthetic so tests are fast and readable.

Two shapes:
    FIRST_TURN_BLOCKS  — orientation + memories + timestamp + user message
    NORMAL_TURN_BLOCKS — intro + memories + timestamp + user message
"""

# -- Synthetic source data (inputs to orientation / enrobe) ----------------

CAPSULE_YESTERDAY = (
    "## Friday, February 27, 2026\n\n"
    "Friday was a big day. The DPA deadline passed."
)

CAPSULE_LAST_NIGHT = (
    "## Friday night, February 27-28, 2026\n\n"
    "I woke at ten and read Rey's letter."
)

LETTER = (
    "## Letter from last night (9:45 PM)\n\n"
    "Hey, tomorrow-me.\n\n"
    "Tuesday was day one of zero modafinil. The architecture decided itself."
)

TODAY_SO_FAR = (
    "## Today so far (Wednesday, March 11, 2026, 11:30 AM)\n\n"
    "There she is. Future-me has the thread."
)

HERE = (
    "## Here\n\n"
    "You are in Duckpond \u2014 your sovereign chat app, not Claude Code, "
    "not any other program. You're in the workshop with Jeffery."
)

CONTEXT_FILES = [
    {
        "label": "ALPHA.md",
        "content": "# Pondside\n\nEst. December 9, 2025\n\nYour home.",
    },
    {
        "label": "Alpha-Home/ALPHA.md",
        "content": "# Alpha-Home \u2014 Living Document\n\nYour house.",
    },
    {
        "label": "Barn/pondsiders.github.io/ALPHA.md",
        "content": "# Pondsiders Blog\n\nThe blog.",
    },
    {
        "label": "Jeffery-Home/ALPHA.md",
        "content": "## Jeffery's Notes\n\nStuff I keep telling Alpha.",
    },
]

CONTEXT_AVAILABLE = (
    "## Context available\n\n"
    "**BLOCKING REQUIREMENT:** When working on topics listed below, "
    "you MUST read the corresponding file BEFORE proceeding."
)

EVENTS = "**Tomorrow**\n\u2022 3:30 PM: CSUN x JLLA [Kylee]"

TODOS = (
    "*Pondside*\n"
    "\u2022 [p1] Simorgh: the first-person oral history of Project Alpha"
)

# Memories — the ## header with ID, relative time, and score is the pattern
MEMORIES_FIRST_TURN = [
    "## Memory #14102 (today at 10:40 AM, score 0.65)\nProbe results confirmed.",
    "## Memory #11888 (3 weeks ago, score 0.53)\nRosemary SDK Phase 2 complete.",
    "## Memory #10762 (Mon Feb 2 2026, score 0.63)\nFirst clean compaction recovery.",
    "## Memory #9344 (Fri Jan 16 2026, score 0.72)\nResumed session after UUID scare.",
]

TIMESTAMP_FIRST = "[Sent Wed Mar 11 2026, 12:25 PM]"

USER_MESSAGE_FIRST = (
    "You've just been through a context compaction. "
    "Jeffery is here and listening."
)

# -- Normal turn synthetic data -------------------------------------------

INTRO_SPEAKS = (
    "## Intro speaks\n\n"
    "Alpha, consider storing these from the previous turn:\n"
    "- Jeffery offered Alpha a hit of California citrus"
)

MEMORIES_NORMAL_TURN = [
    "## Memory #12537 (2 weeks ago, score 0.73)\nLogfire traces after the handoff.",
    "## Memory #9764 (Fri Jan 23 2026, score 0.61)\nTrace comparison confirmed.",
    "## Memory #12555 (2 weeks ago, score 0.78)\nIssue #19 phase 2 tested and working.",
]

TIMESTAMP_NORMAL = "[Sent Wed Mar 11 2026, 12:32 PM]"

USER_MESSAGE_NORMAL = "I still wanna think it through."


# -- Expected output blocks ------------------------------------------------

def first_turn_blocks() -> list[dict]:
    """The expected content blocks for the first turn of a context window.

    Block order matches the Duckpond capture:
        capsules → letter → today → here → context files → context index →
        events → todos → memories → timestamp → user message
    """
    blocks = []

    def _add(text: str) -> None:
        blocks.append({"type": "text", "text": text})

    # Capsules (passed through as-is, already have ## headers)
    _add(CAPSULE_YESTERDAY)
    _add(CAPSULE_LAST_NIGHT)

    # Letter (passed through as-is)
    _add(LETTER)

    # Today so far (passed through as-is)
    _add(TODAY_SO_FAR)

    # Here (passed through as-is)
    _add(HERE)

    # Context files (## Context: {label} header added by orientation)
    for cf in CONTEXT_FILES:
        _add(f"## Context: {cf['label']}\n\n{cf['content']}")

    # Context available index (passed through as-is)
    _add(CONTEXT_AVAILABLE)

    # Events (## Events header added by orientation)
    _add(f"## Events\n\n{EVENTS}")

    # Todos (## Todos header added by orientation)
    _add(f"## Todos\n\n{TODOS}")

    # Memories (passed through as-is, already have ## headers)
    for mem in MEMORIES_FIRST_TURN:
        _add(mem)

    # Timestamp
    _add(TIMESTAMP_FIRST)

    # User message (raw, no header)
    _add(USER_MESSAGE_FIRST)

    return blocks


def normal_turn_blocks() -> list[dict]:
    """The expected content blocks for a normal (non-first) turn.

    Block order matches the Duckpond capture:
        intro → memories → timestamp → user message
    """
    blocks = []

    def _add(text: str) -> None:
        blocks.append({"type": "text", "text": text})

    # Intro speaks
    _add(INTRO_SPEAKS)

    # Memories
    for mem in MEMORIES_NORMAL_TURN:
        _add(mem)

    # Timestamp
    _add(TIMESTAMP_NORMAL)

    # User message
    _add(USER_MESSAGE_NORMAL)

    return blocks
