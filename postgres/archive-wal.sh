#!/bin/bash
# Archive a completed WAL segment to local ZFS storage, gzip-compressed.
# Called by Postgres: archive_command = '/scripts/archive-wal.sh %p %f'
#   %p = full path to the WAL segment file
#   %f = just the filename (e.g., 000000010000000100000054)
#
# Writes gzipped segments to /mnt/Wilson/wal/ with .gz suffix.
# ZFS lz4 underneath is harmless — it recognizes already-compressed
# data and passes through at ~1:1. The gzip matters for B2 sync,
# which sees file sizes, not ZFS logical sizes.
#
# Exit 0 = success (Postgres can recycle the segment).
# Exit non-zero = failure (Postgres retries).

set -e

WAL_PATH="$1"
WAL_NAME="$2"
ARCHIVE_DIR="${WAL_ARCHIVE_DIR:-/mnt/Wilson/wal}"

gzip -c "$WAL_PATH" > "${ARCHIVE_DIR}/${WAL_NAME}.gz"
