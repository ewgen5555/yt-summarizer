#!/usr/bin/env bash
# Установка yt-summarizer на чистый Ubuntu 22.04/24.04 VPS одной командой:
#   curl -fsSL https://raw.githubusercontent.com/ewgen5555/yt-summarizer/main/deploy/install.sh | sudo bash -s -- \
#       --key sk_XXXX [--provider inception] [--model mercury-2.5] [--domain yt.example.com]
#
# Что делает: ставит Docker, клонирует репо в /opt/yt-summarizer, создаёт .env,
# запускает контейнер (restart=always). С --domain дополнительно поднимает Caddy с HTTPS.
# По умолчанию включает API_TOKEN (API закрыт токеном); открытый доступ — с --no-auth.
set -euo pipefail

REPO="https://github.com/ewgen5555/yt-summarizer.git"
DIR="/opt/yt-summarizer"
PROVIDER="inception"; MODEL="mercury-2.5"; KEY=""; DOMAIN=""; TRANSCRIBER="faster_whisper"; WHISPER="tiny"
NO_AUTH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --key) KEY="$2"; shift 2;;
    --provider) PROVIDER="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    --transcriber) TRANSCRIBER="$2"; shift 2;;
    --whisper) WHISPER="$2"; shift 2;;
    --no-auth) NO_AUTH=1; shift;;
    *) echo "Неизвестный аргумент: $1"; exit 1;;
  esac
done
[[ -n "$KEY" ]] || { echo "Нужен --key <LLM_API_KEY>"; exit 1; }
[[ $EUID -eq 0 ]] || { echo "Запускайте через sudo"; exit 1; }

# set_env KEY VALUE — заменяет строку в .env или дописывает её, если ключа ещё нет
# (нужно для обновления уже установленных инстансов, где .env не пересоздаётся).
set_env() {
  local key="$1" value="$2" file="$DIR/.env"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

echo ">> Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
apt-get install -y -qq git >/dev/null

echo ">> Swap 2G (защита от OOM на маленьких VPS)"
if ! swapon --show | grep -q swapfile; then
  # fallocate не работает на части ФС (LXC/OpenVZ) — тогда пишем файл целиком через dd
  if ! fallocate -l 2G /swapfile 2>/dev/null; then
    dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none 2>/dev/null || true
  fi
  if chmod 600 /swapfile 2>/dev/null && mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile 2>/dev/null; then
    { grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab; } 2>/dev/null || true
    echo ">> Swap создан"
  else
    echo ">> Предупреждение: не удалось создать swap (LXC/OpenVZ контейнер?). Продолжаем без swap."
    rm -f /swapfile
  fi
fi

echo ">> Код -> $DIR"
if [[ -d "$DIR/.git" ]]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i "s|^LLM_PROVIDER=.*|LLM_PROVIDER=$PROVIDER|; s|^LLM_MODEL=.*|LLM_MODEL=$MODEL|; s|^LLM_API_KEY=.*|LLM_API_KEY=$KEY|" .env
  sed -i "s|^TRANSCRIBER=.*|TRANSCRIBER=$TRANSCRIBER|; s|^WHISPER_MODEL=.*|WHISPER_MODEL=$WHISPER|" .env
fi

# Публичный запуск закрываем токеном: иначе любой желающий жжёт платные транскрибацию и LLM
# и читает чужие транскрипты. Полностью открытый доступ — только явным --no-auth.
if [[ $NO_AUTH -eq 0 ]]; then
  if [[ -z "$(grep '^API_TOKEN=' .env | cut -d= -f2-)" ]]; then
    set_env API_TOKEN "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  echo ">> API_TOKEN включён (отключить: --no-auth)"
else
  echo ">> Внимание: API открыт без токена (--no-auth)."
fi

if [[ -n "$DOMAIN" ]]; then
  # За Caddy реальный IP клиента приходит в X-Forwarded-For — нужен для лимитов по клиенту.
  set_env TRUST_PROXY true
fi
chmod 600 .env

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
if [[ $NO_AUTH -eq 0 ]]; then
  echo "Токен:  $(grep '^API_TOKEN=' "$DIR/.env" | cut -d= -f2-)"
  echo "        Вставьте его в консоли браузера, чтобы фронтенд ходил в API:"
  echo "        localStorage.setItem('api_token', '<токен>')"
fi
echo "Логи:      cd $DIR && docker compose logs -f"
echo "Обновить:  cd $DIR && git pull && docker compose up -d --build"
