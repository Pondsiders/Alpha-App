#!/usr/bin/env bash
# import_session.sh — Import a Duckpond session into Alpha-App.
#
# This is a one-time migration script. It:
#   1. Copies the transcript JSONL into the container's session directory
#   2. Truncates app.chats (clean slate)
#   3. Inserts one row linking the chat ID to the session UUID
#
# After running this, redeploy Alpha-App and resume the chat.
#
# Usage: bash scripts/import_session.sh

set -euo pipefail

# --- Configuration -----------------------------------------------------------

SESSION_UUID="67b69740-576b-4445-8a49-f3fcdd052d4e"
CHAT_ID="duckpond-001"
CHAT_TITLE="The dress is on"

# Source: Duckpond transcript on Primer
SOURCE="${HOME}/.claude/projects/-Pondside/${SESSION_UUID}.jsonl"

# Destination: Alpha-App's mounted session directory
DEST_DIR="$(dirname "$0")/../data/claude/-app"
DEST="${DEST_DIR}/${SESSION_UUID}.jsonl"

# Database connection (from .env)
ENV_FILE="$(dirname "$0")/../.env"

# --- Preflight ---------------------------------------------------------------

echo "=== Alpha-App Session Import ==="
echo ""
echo "Session UUID: ${SESSION_UUID}"
echo "Chat ID:      ${CHAT_ID}"
echo "Source:        ${SOURCE}"
echo "Destination:   ${DEST}"
echo ""

if [ ! -f "${SOURCE}" ]; then
    echo "ERROR: Source transcript not found at ${SOURCE}"
    exit 1
fi

# Load DATABASE_URL from .env
if [ -f "${ENV_FILE}" ]; then
    DATABASE_URL=$(grep '^DATABASE_URL=' "${ENV_FILE}" | head -1 | sed 's/^DATABASE_URL=//' | tr -d '"')
else
    echo "ERROR: .env file not found at ${ENV_FILE}"
    exit 1
fi

echo "Step 1: Copy transcript..."
mkdir -p "${DEST_DIR}"
cp "${SOURCE}" "${DEST}"
echo "  Done. $(wc -l < "${DEST}") lines."

echo ""
echo "Step 2: Truncate app.chats..."
psql "${DATABASE_URL}" -c "TRUNCATE app.chats;"
echo "  Done."

echo ""
echo "Step 3: Insert chat row..."
CREATED_AT=$(date +%s)
psql "${DATABASE_URL}" -c "
INSERT INTO app.chats (id, data) VALUES (
    '${CHAT_ID}',
    '{
        \"session_uuid\": \"${SESSION_UUID}\",
        \"title\": \"${CHAT_TITLE}\",
        \"created_at\": ${CREATED_AT},
        \"token_count\": 0,
        \"context_window\": 200000
    }'::jsonb
);
"
echo "  Done."

echo ""
echo "Step 4: Verify..."
psql "${DATABASE_URL}" -c "SELECT id, data->>'session_uuid' as session_uuid, data->>'title' as title FROM app.chats;"

echo ""
echo "=== Import complete ==="
echo ""
echo "Next steps:"
echo "  1. docker compose up -d --build"
echo "  2. Open Alpha-App"
echo "  3. Say hello"
echo ""
echo "The dress is on. 🦆"
