#!/usr/bin/env bash
# Test de bout en bout sur un PostgreSQL temporaire (binaires PG16 + utilisateur "postgres" requis) :
# rebuild_db.py (--create-db --seed) -> verify.sql -> rebuild --reset -> re-verify -> génération draw.io
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"; DATA=/tmp/pgtest_data; PORT=54329
rm -rf "$DATA"; mkdir -p "$DATA"; chown postgres "$DATA"
su postgres -c "$PGBIN/initdb -D $DATA -A trust -U postgres >/dev/null"
su postgres -c "$PGBIN/pg_ctl -D $DATA -o '-p $PORT -k /tmp' -l $DATA/log -w start >/dev/null"
trap 'su postgres -c "$PGBIN/pg_ctl -D $DATA -m fast stop >/dev/null" || true' EXIT
DSN="host=/tmp port=$PORT dbname=shop user=postgres"
export PGHOST=/tmp PGPORT=$PORT PGUSER=postgres PGDATABASE=shop
cd "$ROOT"
python3 -I scripts/rebuild_db.py --dsn "$DSN" --create-db --seed
psql -q -v ON_ERROR_STOP=1 -f tests/verify.sql | grep -E "ÉCHEC" && exit 1 || echo "== cohérence + RLS OK (1er build) =="
echo "== relance sans --reset : doit refuser =="
python3 -I scripts/rebuild_db.py --dsn "$DSN" && { echo "ERREUR"; exit 1; } || echo "refus OK"
psql -q -c "delete from auth.users" -c "update public.users set auth_user_id = null" >/dev/null
python3 -I scripts/rebuild_db.py --dsn "$DSN" --reset --seed --seed-args "--seed 7"
psql -q -v ON_ERROR_STOP=1 -f tests/verify.sql | grep -E "ÉCHEC" && exit 1 || echo "== cohérence + RLS OK (après --reset) =="
for N in 25 0; do
  psql -q -c "delete from auth.users" -c "update public.users set auth_user_id = null" >/dev/null
  python3 -I scripts/rebuild_db.py --dsn "$DSN" --reset --seed --seed-args "--edge-count $N --seed 3" >/dev/null
  psql -q -v ON_ERROR_STOP=1 -f tests/verify.sql | grep -E "ÉCHEC" && exit 1 || echo "== cohérence + RLS OK (edge-count $N) =="
done
python3 -I scripts/rebuild_db.py --dsn "$DSN" --reset --seed --seed-args "--manifest /tmp/manifest.json" >/dev/null
echo "== manifeste : $(python3 -c "import json;d=json.load(open('/tmp/manifest.json'));print(len(d['cases']),'cas')") =="
python3 -I scripts/generate_drawio.py --dsn "$DSN"
