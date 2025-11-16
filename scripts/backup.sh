#!/usr/bin/env bash
set -euo pipefail

: "${DB_HOST:=localhost}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=api_challenge}"
: "${DB_USER:=api_user}"
: "${DB_PASSWORD:=api_password}"

export PGPASSWORD="$DB_PASSWORD"
ts=$(date +%Y%m%d_%H%M%S)
file="backup_${DB_NAME}_${ts}.sql"
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -F p -d "$DB_NAME" > "$file"
echo "Создан бэкап: $file"
