#!/bin/bash
# Alpha Postgres entrypoint — switches between primary and replica
# based on the POSTGRES_ROLE environment variable.
#
# Primary: removes standby.signal so Postgres accepts writes.
# Replica: writes standby.signal + primary_conninfo so Postgres
#          streams WAL from the primary. If the data directory is
#          empty, automatically runs pg_basebackup first.
#
# Falls through to the official postgres Docker entrypoint.

set -e

DATADIR="/var/lib/postgresql/data"
ROLE="${POSTGRES_ROLE:-primary}"

case "$ROLE" in
    primary)
        if [ -f "$DATADIR/PG_VERSION" ]; then
            echo "alpha-postgres: starting as PRIMARY"
            rm -f "$DATADIR/standby.signal"
        else
            echo "alpha-postgres: empty data directory, initializing as PRIMARY"
            # Let the official entrypoint handle init
        fi
        ;;
    replica)
        if [ ! -f "$DATADIR/PG_VERSION" ]; then
            echo "alpha-postgres: empty data directory — running pg_basebackup from ${REPLICATION_PRIMARY_HOST}:${REPLICATION_PRIMARY_PORT:-5432}"
            PGPASSWORD="${REPLICATION_PASSWORD}" pg_basebackup \
                -h "${REPLICATION_PRIMARY_HOST}" \
                -p "${REPLICATION_PRIMARY_PORT:-5432}" \
                -U "${REPLICATION_USER:-replicator}" \
                -D "$DATADIR" \
                -Fp -Xs -P -R
            chown -R postgres:postgres "$DATADIR"
            echo "alpha-postgres: pg_basebackup complete"
        fi

        echo "alpha-postgres: starting as REPLICA of ${REPLICATION_PRIMARY_HOST}:${REPLICATION_PRIMARY_PORT:-5432}"
        touch "$DATADIR/standby.signal"

        # Write primary_conninfo into postgresql.auto.conf
        AUTO_CONF="$DATADIR/postgresql.auto.conf"
        grep -v "^primary_conninfo" "$AUTO_CONF" > "$AUTO_CONF.tmp" 2>/dev/null || true
        echo "primary_conninfo = 'host=${REPLICATION_PRIMARY_HOST} port=${REPLICATION_PRIMARY_PORT:-5432} user=${REPLICATION_USER:-replicator} password=${REPLICATION_PASSWORD}'" >> "$AUTO_CONF.tmp"
        mv "$AUTO_CONF.tmp" "$AUTO_CONF"
        chmod 600 "$AUTO_CONF"
        chown postgres:postgres "$AUTO_CONF"
        ;;
    *)
        echo "alpha-postgres: ERROR: unknown POSTGRES_ROLE '$ROLE' (expected 'primary' or 'replica')"
        exit 1
        ;;
esac

# Hand off to the official postgres entrypoint
exec docker-entrypoint.sh "$@"
