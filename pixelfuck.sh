#!/bin/bash
# pixelfuck.sh — Start the test backend + Vite dev server for frontend-v2 work.
#
# Backend: bare metal on port 18020, pointed at alpha_pixelfuck database.
# Frontend: Vite dev server on port 5173, proxies /api and /ws to backend.
#
# Usage: ./pixelfuck.sh
#   Opens two background processes. Ctrl+C kills both.

set -e
cd "$(dirname "$0")"

# -- Config --
BACKEND_PORT=18020
DB_NAME="alpha_pixelfuck"
DB_PASSWORD="myths-livia-mcduck9SONGS"
DATABASE_URL="postgresql://postgres:${DB_PASSWORD}@alpha.tail8bd569.ts.net:5432/${DB_NAME}"

echo "🦆 pixelfuck mode"
echo "   Backend:  http://localhost:${BACKEND_PORT}"
echo "   Frontend: http://localhost:5173"
echo "   Database: ${DB_NAME}"
echo ""
echo "   Ctrl+C to stop both."
echo ""

# -- Trap to kill both on exit --
cleanup() {
    echo ""
    echo "🦆 Shutting down..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
    echo "🦆 Done."
}
trap cleanup EXIT INT TERM

# -- Start backend --
echo "Starting backend on :${BACKEND_PORT}..."
cd backend
DATABASE_URL="$DATABASE_URL" PORT="$BACKEND_PORT" uv run alpha &
BACKEND_PID=$!
cd ..

# -- Wait a beat for backend to bind --
sleep 2

# -- Start Vite dev server --
echo "Starting Vite dev server..."
cd frontend-v2
npm run dev &
FRONTEND_PID=$!
cd ..

# -- Wait for either to exit --
wait
