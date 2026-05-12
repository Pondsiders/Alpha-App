"""Round-trip every implemented wire-payload fixture through its Pydantic model.

The fixtures at `fixtures/wire-payloads/` are the witness for the wire
protocol — JSON examples that both the backend (this test) and the
frontend (Vitest test) must round-trip cleanly. The fixture set tracks
the spec; the test set tracks what's currently implemented.

Fixtures whose discriminator isn't in `_EVENT_CLASSES` / `_COMMAND_CLASSES`
/ `_RESPONSE_CLASSES` are silently filtered out of the parametrize set;
they'll start running automatically the moment their Pydantic class is
registered. Implementing a new wire shape: add the class, add it to the
registry below.
"""

import json
from pathlib import Path

import pytest

from alpha.ws.commands import BaseCommand, CreateChat, Hello, Interrupt, JoinChat, Send
from alpha.ws.events import (
    AppState,
    AssistantMessage,
    BaseEvent,
    ChatCreated,
    ChatState,
)
from alpha.ws.responses import BaseResponse, HiYourself

# Map a fixture's discriminator value to its Pydantic class. Fixtures whose
# discriminator isn't in one of these dicts are skipped — they describe wire
# shapes the spec defines but the backend hasn't implemented yet.
_EVENT_CLASSES: dict[str, type[BaseEvent]] = {
    "chat-created": ChatCreated,
    "app-state": AppState,
    "chat-state": ChatState,
    "assistant-message": AssistantMessage,
}

_RESPONSE_CLASSES: dict[str, type[BaseResponse]] = {
    "hi-yourself": HiYourself,
}

_COMMAND_CLASSES: dict[str, type[BaseCommand]] = {
    "hello": Hello,
    "create-chat": CreateChat,
    "join-chat": JoinChat,
    "send": Send,
    "interrupt": Interrupt,
}

_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "fixtures" / "wire-payloads"
)


def _implemented_fixture_paths() -> list[Path]:
    """Every fixture whose discriminator has a registered Pydantic class."""
    paths: list[Path] = []
    for path in sorted(_FIXTURES_DIR.glob("*.json")):
        payload: dict[str, object] = json.loads(path.read_text())
        if (
            ("event" in payload and payload["event"] in _EVENT_CLASSES)
            or ("response" in payload and payload["response"] in _RESPONSE_CLASSES)
            or ("command" in payload and payload["command"] in _COMMAND_CLASSES)
        ):
            paths.append(path)
    return paths


@pytest.mark.parametrize(
    "fixture_path", _implemented_fixture_paths(), ids=lambda p: p.stem
)
def test_wire_fixture_round_trip(fixture_path: Path) -> None:
    """Validate fixture against its Pydantic class, then re-serialize, then compare."""
    payload: dict[str, object] = json.loads(fixture_path.read_text())

    cls: type[BaseEvent] | type[BaseCommand] | type[BaseResponse]
    if "event" in payload:
        discriminator = payload["event"]
        assert isinstance(discriminator, str)
        cls = _EVENT_CLASSES[discriminator]
    elif "response" in payload:
        discriminator = payload["response"]
        assert isinstance(discriminator, str)
        cls = _RESPONSE_CLASSES[discriminator]
    elif "command" in payload:
        discriminator = payload["command"]
        assert isinstance(discriminator, str)
        cls = _COMMAND_CLASSES[discriminator]
    else:
        pytest.fail(f"fixture {fixture_path.name} has no discriminator field")

    instance = cls.model_validate(payload)
    serialized = instance.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert serialized == payload, (
        f"round-trip mismatch for {fixture_path.name}:\n"
        f"  fixture:    {payload}\n"
        f"  serialized: {serialized}"
    )
