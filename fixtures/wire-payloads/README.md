# Wire payload fixtures

Canonical JSON examples of every command and event Alpha-App speaks. Each
file is one payload — exactly what crosses the WebSocket on the wire.

The fixtures are the witness. Pydantic models in `backend/` and Zod schemas
in `frontend/` are two parallel implementations of the same contract; both
have to round-trip every fixture in this directory. Tests on each side load
these files directly:

- `backend/tests/test_wire_fixtures.py` — Pydantic side
- `frontend/tests/wire-fixtures.test.ts` — Zod side (planned)

When the wire shape changes, change the fixture, change the Pydantic model,
change the Zod schema, in one PR. The tests fail loudly until all three agree.

## Naming

`<event-name>.json` for events, `<command-name>.json` for commands. One file
per shape. Use the wire-format name (kebab-case, matching the `event` or
`command` field), not the Python or TypeScript class name.
