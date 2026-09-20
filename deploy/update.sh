#!/usr/bin/env bash
# Обновление до последней версии из main (учитывает включённый Caddy)
set -e
cd /opt/yt-summarizer
git pull
FILES=(-f docker-compose.yml)
[[ -f deploy/Caddyfile.local ]] && FILES+=(-f deploy/docker-compose.caddy.yml)
docker compose "${FILES[@]}" up -d --build
docker image prune -f >/dev/null
docker compose "${FILES[@]}" ps
