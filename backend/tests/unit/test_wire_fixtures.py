"""Round-trip every implemented wire-payload fixture through its Pydantic model.

The fixtures at `fixtures/wire-payloads/` are the witness for the wire
protocol — JSON examples that both the backend (this test) and the
frontend (Vitest test) must round-trip cleanly. The fixture set tracks
the spec; the test set tracks what's currently implemented.

Every fixture is accounted for: its discriminator is either in
`_IMPLEMENTED` (round-tripped through its Pydantic class) or in
`_NOT_YET_IMPLEMENTED` (explicitly deferred). A fixture whose
discriminator is in neither set fails `test_every_fixture_accounted_for`
— there is no silent third category.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from alpha.ws.commands import CreateChat, Hello, Interrupt, JoinChat, Send
from alpha.ws.events import (
    AppState,
    AssistantMessage,
    ChatCreated,
    ChatState,
)
from alpha.ws.responses import HiYourself

# Discriminators we round-trip through a Pydantic class. The discriminator
# is the value of the `event` / `response` / `command` field in the fixture.
_IMPLEMENTED: dict[str, type[BaseModel]] = {
    "hello": Hello,
    "create-chat": CreateChat,
    "join-chat": JoinChat,
    "send": Send,
    "interrupt": Interrupt,
    "hi-yourself": HiYourself,
    "chat-created": ChatCreated,
    "app-state": AppState,
    "chat-state": ChatState,
    "assistant-message": AssistantMessage,
}

# Discriminators that the spec defines and the fixture set carries, but
# the backend hasn't implemented yet. Moving a discriminator out of this
# set into `_IMPLEMENTED` is what "we implemented this shape" looks like
# in the test infrastructure.
_NOT_YET_IMPLEMENTED: set[str] = {
    "chat-joined",
    "error",
    "turn-started",
    "user-message",
    "thinking-delta",
    "text-delta",
    "tool-call-start",
    "tool-call-delta",
    "tool-call-result",
    "turn-complete",
}

_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "fixtures" / "wire-payloads"
)


def _discriminator(payload: dict[str, object]) -> str:
    """Pull the event/response/command value out of a fixture payload."""
    for key in ("event", "response", "command"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    msg = f"fixture has no event/response/command discriminator: {payload!r}"
    raise AssertionError(msg)


def _fixture_paths() -> list[Path]:
    """Every `*.json` file under `fixtures/wire-payloads/`, sorted."""
    return sorted(_FIXTURES_DIR.glob("*.json"))


def _implemented_fixture_paths() -> list[Path]:
    """Fixtures whose discriminator is in `_IMPLEMENTED`."""
    return [
        path
        for path in _fixture_paths()
        if _discriminator(json.loads(path.read_text())) in _IMPLEMENTED
    ]


def test_every_fixture_accounted_for() -> None:
    """Every fixture's discriminator is either implemented or explicitly deferred."""
    unaccounted: list[tuple[str, str]] = []
    for path in _fixture_paths():
        discriminator = _discriminator(json.loads(path.read_text()))
        if (
            discriminator not in _IMPLEMENTED
            and discriminator not in _NOT_YET_IMPLEMENTED
        ):
            unaccounted.append((path.name, discriminator))
    assert not unaccounted, (
        "fixtures with unaccounted discriminators (add to either "
        f"`_IMPLEMENTED` or `_NOT_YET_IMPLEMENTED`): {unaccounted}"
    )


def test_implemented_and_deferred_sets_are_disjoint() -> None:
    """A discriminator can be implemented or deferred, not both."""
    overlap = set(_IMPLEMENTED) & _NOT_YET_IMPLEMENTED
    assert not overlap, f"discriminators appear in both sets: {overlap}"


@pytest.mark.parametrize(
    "fixture_path", _implemented_fixture_paths(), ids=lambda p: p.stem
)
def test_wire_fixture_round_trip(fixture_path: Path) -> None:
    """Validate fixture against its Pydantic class, then re-serialize, then compare."""
    payload: dict[str, object] = json.loads(fixture_path.read_text())
    cls = _IMPLEMENTED[_discriminator(payload)]
    instance = cls.model_validate(payload)
    serialized = instance.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert serialized == payload, (
        f"round-trip mismatch for {fixture_path.name}:\n"
        f"  fixture:    {payload}\n"
        f"  serialized: {serialized}"
    )
