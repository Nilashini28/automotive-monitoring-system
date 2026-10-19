#!/bin/sh
# Container entrypoint: bring the schema up, then run the service command.
#
# Every service can be started in any order and on an empty or populated
# database - schema creation is `create_all`-idempotent and seeding upserts.
# Set MIGRATE_ON_START=0 to run migrations out-of-band instead.
set -e

if [ "${MIGRATE_ON_START:-1}" = "1" ]; then
  i=0
  until alembic upgrade head; do
    i=$((i + 1))
    if [ "$i" -ge 20 ]; then
      echo "entrypoint: database not reachable after 20 attempts" >&2
      exit 1
    fi
    echo "entrypoint: waiting for the database ($i)"
    sleep 2
  done
fi

if [ "${SEED_ON_START:-1}" = "1" ]; then
  python -m signals.cli seed
fi

exec "$@"
