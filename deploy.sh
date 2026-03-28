#!/bin/bash
# deploy.sh — Build Alpha-App images and deploy.
#
# Builds x86 (Primer) and ARM64 (Pi lifeboat) images.
# Recreates the local container and pushes the ARM image to the Pi.
#
# Prerequisites (one-time setup, already done):
#   docker run --privileged --rm tonistiigi/binfmt --install arm64
#   docker buildx create --name multiarch --use
#
# Usage:
#   ./deploy.sh          # Full deploy: build both, recreate local, push to Pi
#   ./deploy.sh local    # Local only: rebuild and recreate on Primer
#   ./deploy.sh pi       # Pi only: cross-build ARM64 and push to Pi

set -euo pipefail

IMAGE_NAME="alpha-app"
PI_HOST="alpha-pi"
APP_DIR="/Pondside/Workshop/Projects/Alpha-App"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

step() { echo -e "${GREEN}▸${NC} $1"; }
warn() { echo -e "${YELLOW}▸${NC} $1"; }
timer() { date +%s; }

build_local() {
    step "Building x86 image and recreating containers..."
    local start=$(timer)
    docker compose build
    docker compose up -d
    local elapsed=$(( $(timer) - start ))
    step "Local deploy complete in ${elapsed}s."
}

build_pi() {
    step "Cross-building ARM64 image for Pi..."
    local start=$(timer)

    docker buildx build \
        --platform linux/arm64 \
        -t "${IMAGE_NAME}:arm64" \
        --load \
        .

    local build_time=$(( $(timer) - start ))
    step "ARM64 build complete in ${build_time}s."

    step "Shipping image to Pi (this may take a moment)..."
    local push_start=$(timer)
    docker save "${IMAGE_NAME}:arm64" | ssh "$PI_HOST" docker load
    local push_time=$(( $(timer) - push_start ))

    step "ARM64 image delivered to Pi in ${push_time}s."
    warn "To activate on Pi: ssh $PI_HOST 'cd $APP_DIR && docker compose up -d'"
}

case "${1:-full}" in
    local)
        build_local
        ;;
    pi)
        build_pi
        ;;
    full)
        build_local
        build_pi
        ;;
    *)
        echo "Usage: $0 [local|pi|full]"
        exit 1
        ;;
esac

echo -e "${GREEN}▸${NC} Done. 🦆"
