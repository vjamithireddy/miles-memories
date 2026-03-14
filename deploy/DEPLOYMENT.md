# VPS Deployment

This deploy path assumes:
- Ubuntu or Debian-based Hostinger VPS
- app code in `/opt/milesmemories/app`
- PostgreSQL running locally on the VPS
- Nginx reverse proxy in front of the FastAPI app
- application runtime user is `svc_miles`
- Nginx is exposed on `8080` because `80/443` are already used by other services

## 1. Server packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib nginx certbot python3-certbot-nginx
```

## 2. Database setup

```bash
id -u svc_miles >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash svc_miles
sudo -u postgres psql
```

```sql
CREATE USER miles WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE milesmemories OWNER miles;
\q
```

## 3. App install

```bash
sudo mkdir -p /opt/milesmemories
sudo chown svc_miles:svc_miles /opt/milesmemories
sudo -u svc_miles git clone <your-repo-url> /opt/milesmemories/app
cd /opt/milesmemories/app
sudo -u svc_miles python3 -m venv .venv
sudo -u svc_miles .venv/bin/pip install --upgrade pip
sudo -u svc_miles .venv/bin/pip install -e .
sudo -u svc_miles cp deploy/env.production.example .env
```

Update `.env` with the real database password.

## 4. Initialize schema

```bash
set -a && source .env && set +a
psql "$DATABASE_URL" -f database/schema.sql
```

If you want the schema step to run without root ownership:

```bash
sudo -u svc_miles bash -lc 'cd /opt/milesmemories/app && set -a && source .env && set +a && psql "$DATABASE_URL" -f database/schema.sql'
```

## 5. Systemd service

```bash
sudo cp deploy/systemd/milesmemories.service /etc/systemd/system/milesmemories.service
sudo systemctl daemon-reload
sudo systemctl enable milesmemories
sudo systemctl start milesmemories
sudo systemctl status milesmemories
```

## 6. Nginx

```bash
sudo cp deploy/nginx/travel.navi-services.com.conf /etc/nginx/sites-available/travel.navi-services.com
sudo ln -s /etc/nginx/sites-available/travel.navi-services.com /etc/nginx/sites-enabled/travel.navi-services.com
sudo nginx -t
sudo systemctl reload nginx
```

## 7. SSL

SSL is not part of the current server setup because the working reverse proxy runs on `8080` only.
Add HTTPS later only if you decide how `8443` should be managed and where certificates will be provisioned.

## 8. Smoke checks

```bash
curl http://127.0.0.1:8000/health
curl http://travel.navi-services.com:8080/health
```
