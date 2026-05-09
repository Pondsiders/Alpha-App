"""Round-trip every wire-payload fixture through its Pydantic model.

The fixtures at `fixtures/wire-payloads/` are the witness for the wire
protocol — JSON examples that both the backend (this test) and the
frontend (planned Vitest test) must round-trip cleanly. Adding a new
shape: drop a `<name>.json` file in the fixtures directory and add a
registry entry below.
"""

import json
from pathlib import Path

import pytest

from alpha.ws.commands import BaseCommand, CreateChat, Interrupt, JoinChat, Send
from alpha.ws.events import AppState, BaseEvent, ChatCreated, Error

# Map a fixture's discriminator value (the `event` or `command` field) to
# its Pydantic class. Adding a new wire shape means a class in
# `alpha.ws.events` or `alpha.ws.commands` and one entry here.
_EVENT_CLASSES: dict[str, type[BaseEvent]] = {
    "error": Error,
    "chat-created": ChatCreated,
    "app-state": AppState,
}

_COMMAND_CLASSES: dict[str, type[BaseCommand]] = {
    "create-chat": CreateChat,
    "join-chat": JoinChat,
    "send": Send,
    "interrupt": Interrupt,
}

_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "wire-payloads"
)


def _fixture_paths() -> list[Path]:
    """Every JSON file in `fixtures/wire-payloads/`."""
    return sorted(_FIXTURES_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_wire_fixture_round_trip(fixture_path: Path) -> None:
    """Validate fixture against its Pydantic class, then re-serialize, then compare."""
    payload: dict[str, object] = json.loads(fixture_path.read_text())

    if "event" in payload:
        discriminator = payload["event"]
        assert isinstance(discriminator, str)
        cls = _EVENT_CLASSES.get(discriminator)
        assert cls is not None, (
            f"fixture {fixture_path.name} has unknown event "
            f"{discriminator!r}; add it to _EVENT_CLASSES"
        )
    elif "command" in payload:
        discriminator = payload["command"]
        assert isinstance(discriminator, str)
        cls = _COMMAND_CLASSES.get(discriminator)
        assert cls is not None, (
            f"fixture {fixture_path.name} has unknown command "
            f"{discriminator!r}; add it to _COMMAND_CLASSES"
        )
    else:
        pytest.fail(f"fixture {fixture_path.name} has neither `event` nor `command`")

    instance = cls.model_validate(payload)
    serialized = instance.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert serialized == payload, (
        f"round-trip mismatch for {fixture_path.name}:\n"
        f"  fixture:    {payload}\n"
        f"  serialized: {serialized}"
    )
