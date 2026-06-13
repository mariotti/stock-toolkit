#!/bin/sh
# Weekly SQLite housekeeping — VACUUM compacts the file after deletes,
# ANALYZE refreshes the query planner stats. Mirrors the same step in
# crontab.demo / the launchd db-maintenance plist. Reusable from a future
# native Mac wrapper too (just point STOCK_DIR at the right directory).
set -eu

DATA_DIR="${STOCK_DIR:-/data}"

for db in \
    "$DATA_DIR/stock_data.db" \
    "$DATA_DIR/stock_failures.db" \
    "$DATA_DIR"/data/*.db; do
    [ -f "$db" ] || continue
    echo "[maintenance] VACUUM $db"
    sqlite3 "$db" 'VACUUM; ANALYZE;'
done
