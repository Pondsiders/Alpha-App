#!/bin/bash
# Archive a completed WAL segment to local ZFS storage.
# Called by Postgres: archive_command = '/scripts/archive-wal.sh %p %f'
#   %p = full path to the WAL segment file
#   %f = just the filename (e.g., 000000010000000100000054)
#
# Writes to /mnt/Wilson/wal/ (ZFS with lz4 compression — a 16 MB segment
# compresses to ~30 KB transparently). A separate hourly job syncs to B2.
#
# Exit 0 = success (Postgres can recycle the segment).
# Exit non-zero = failure (Postgres retries).

set -e

WAL_PATH="$1"
WAL_NAME="$2"
ARCHIVE_DIR="${WAL_ARCHIVE_DIR:-/mnt/Wilson/wal}"

cp "$WAL_PATH" "${ARCHIVE_DIR}/${WAL_NAME}"
