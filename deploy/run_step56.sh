#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/milesmemories/app"
APP_USER="svc_miles"
DB_USER="miles"
DB_NAME="milesmemories"

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  echo "Missing app user: $APP_USER" >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "psql is not installed" >&2
  exit 1
fi

if [ ! -d "$APP_DIR" ]; then
  echo "Missing app directory: $APP_DIR" >&2
  exit 1
fi

if [ -z "${DB_PASS:-}" ]; then
  DB_PASS="$(openssl rand -base64 24 | tr -d '\n')"
fi

if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'" | grep -q 1; then
  runuser -u postgres -- psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
else
  runuser -u postgres -- psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
fi

if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
  runuser -u postgres -- psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
fi

runuser -u "$APP_USER" -- bash -lc "cd '$APP_DIR' && cp deploy/env.production.example .env"
runuser -u "$APP_USER" -- bash -lc "cd '$APP_DIR' && sed -i \"s/CHANGE_ME/${DB_PASS//\//\\/}/g\" .env"
runuser -u "$APP_USER" -- bash -lc "cd '$APP_DIR' && set -a && source .env && set +a && psql \"\$DATABASE_URL\" -f database/schema.sql"

cp "$APP_DIR/deploy/systemd/milesmemories.service" /etc/systemd/system/milesmemories.service
systemctl daemon-reload
systemctl enable milesmemories
systemctl restart milesmemories

cp "$APP_DIR/deploy/nginx/travel.navi-services.com.conf" /etc/nginx/sites-available/travel.navi-services.com
ln -sf /etc/nginx/sites-available/travel.navi-services.com /etc/nginx/sites-enabled/travel.navi-services.com
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "DB_PASS=$DB_PASS"
echo "APP_HEALTH=$(curl -fsS http://127.0.0.1:8000/health)"
