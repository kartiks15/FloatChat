#!/bin/bash
# Create the production .env on the server, with fresh random database passwords.
# Usage (from the repository directory):  bash deploy/make-env.sh [your.domain.com]
# Without a domain it uses <public-ip>.nip.io, which resolves to this server, so Caddy
# can still obtain an HTTPS certificate. Attach the Lightsail static IP BEFORE running it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -e .env ]; then
  echo ".env already exists; refusing to overwrite it (its passwords protect your database)." >&2
  exit 1
fi

domain="${1:-}"
if [ -z "$domain" ]; then
  ip="$(curl -fsS --max-time 5 https://checkip.amazonaws.com | tr -d '[:space:]')"
  if ! [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Could not detect this server's public IPv4 address; pass a domain instead." >&2
    exit 1
  fi
  domain="${ip//./-}.nip.io"
fi

umask 077
cat > .env <<EOF
# Production settings. COMPOSE_FILE makes plain "docker compose ..." use the HTTPS proxy setup.
COMPOSE_FILE=compose.yaml:deploy/compose.production.yaml
POSTGRES_PASSWORD=$(openssl rand -hex 24)
APP_DB_PASSWORD=$(openssl rand -hex 24)
DOMAIN=$domain

# Optional hosted AI (docs/aws-deployment.md, "Open-weight inference"); uncomment after setting up Bedrock.
# AI_ENABLED=true
# AI_PROVIDER=bedrock
# AWS_REGION=us-east-1
# BEDROCK_MODEL_ID=qwen.qwen3-32b-v1:0
# AWS_BEARER_TOKEN_BEDROCK=
# AI_DAILY_REQUEST_LIMIT=20
# AI_HOURLY_CLIENT_LIMIT=5
EOF
echo "Wrote .env for https://$domain"
