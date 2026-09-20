#!/usr/bin/env bash
# Установка yt-summarizer на чистый Ubuntu 22.04/24.04 VPS одной командой:
#   curl -fsSL https://raw.githubusercontent.com/ewgen5555/yt-summarizer/main/deploy/install.sh | sudo bash -s -- \
#       --key sk_XXXX [--provider inception] [--model mercury-2.5] [--domain yt.example.com]
#
# Что делает: ставит Docker, клонирует репо в /opt/yt-summarizer, создаёт .env,
# запускает контейнер (restart=always). С --domain дополнительно поднимает Caddy с HTTPS.
set -euo pipefail

REPO="https://github.com/ewgen5555/yt-summarizer.git"
DIR="/opt/yt-summarizer"
PROVIDER="inception"; MODEL="mercury-2.5"; KEY=""; DOMAIN=""; TRANSCRIBER="faster_whisper"; WHISPER="tiny"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --key) KEY="$2"; shift 2;;
    --provider) PROVIDER="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    --transcriber) TRANSCRIBER="$2"; shift 2;;
    --whisper) WHISPER="$2"; shift 2;;
    *) echo "Неизвестный аргумент: $1"; exit 1;;
  esac
done
[[ -n "$KEY" ]] || { echo "Нужен --key <LLM_API_KEY>"; exit 1; }
[[ $EUID -eq 0 ]] || { echo "Запускайте через sudo"; exit 1; }

echo ">> Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
apt-get install -y -qq git >/dev/null

echo ">> Swap 2G (защита от OOM на маленьких VPS)"
if ! swapon --show | grep -q swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo ">> Код -> $DIR"
if [[ -d "$DIR/.git" ]]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i "s|^LLM_PROVIDER=.*|LLM_PROVIDER=$PROVIDER|; s|^LLM_MODEL=.*|LLM_MODEL=$MODEL|; s|^LLM_API_KEY=.*|LLM_API_KEY=$KEY|" .env
  sed -i "s|^TRANSCRIBER=.*|TRANSCRIBER=$TRANSCRIBER|; s|^WHISPER_MODEL=.*|WHISPER_MODEL=$WHISPER|" .env
  chmod 600 .env
fi

echo ">> Запуск приложения"
docker compose up -d --build

if [[ -n "$DOMAIN" ]]; then
  echo ">> Caddy (HTTPS) для $DOMAIN"
  sed "s|DOMAIN|$DOMAIN|" deploy/Caddyfile > ./deploy/Caddyfile.local
  docker compose -f docker-compose.yml -f deploy/docker-compose.caddy.yml up -d
  ufw allow 80,443/tcp >/dev/null 2>&1 || true
  URL="https://$DOMAIN"
else
  ufw allow 8000/tcp >/dev/null 2>&1 || true
  URL="http://$(curl -s ifconfig.me):8000"
fi

echo
echo "Готово: $URL   (health: $URL/health)"
echo "Логи:      cd $DIR && docker compose logs -f"
echo "Обновить:  cd $DIR && git pull && docker compose up -d --build"
