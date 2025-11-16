#!/usr/bin/env bash
set -euo pipefail

: "${DB_HOST:=localhost}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=api_challenge}"
: "${DB_USER:=api_user}"
: "${DB_PASSWORD:=api_password}"

if [ $# -lt 1 ]; then
  echo "Использование: $0 <backup.sql>"
  exit 1
fi

export PGPASSWORD="$DB_PASSWORD"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$1"
echo "Восстановление завершено"
