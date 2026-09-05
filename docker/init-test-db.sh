#!/usr/bin/env bash
# Runs once, automatically, the first time the docker-compose `db`
# volume is created (postgres official image convention:
# /docker-entrypoint-initdb.d/*.sh). Creates a second, separate database
# for the automated test suite alongside the main development database
# named by POSTGRES_DB -- kept in the same local Postgres container for
# simplicity, never sharing tables/rows with the development database.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE school_timetable_test OWNER $POSTGRES_USER;
EOSQL
