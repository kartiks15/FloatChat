#!/bin/sh
set -eu
# This file is sourced by the official Postgres entrypoint on first initialization.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=reader_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE floatchat_reader LOGIN PASSWORD :'reader_password';
GRANT CONNECT ON DATABASE floatchat TO floatchat_reader;
GRANT USAGE ON SCHEMA public TO floatchat_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO floatchat_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE floatchat_admin IN SCHEMA public GRANT SELECT ON TABLES TO floatchat_reader;
ALTER ROLE floatchat_reader SET default_transaction_read_only = on;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
