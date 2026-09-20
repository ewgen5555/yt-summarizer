# YouTube AI Summarizer

Ссылка на YouTube → аудио/транскрипт → структура содержания → краткое резюме и ключевые идеи.

Стек подобран под слабый ПК и дешёвый хостинг (1 vCPU / 1–2 ГБ RAM):

| Слой | Решение | Почему |
|---|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn | минимум зависимостей, авто-документация `/docs` |
| Загрузка | yt-dlp + ffmpeg | сначала берём **готовые субтитры YouTube** (бесплатно и за секунды), аудио качаем только если субтитров нет |
| Транскрибация | faster-whisper (CPU, int8, модель `tiny`/`base`) **или** OpenAI Whisper API | локально бесплатно; через API — ~$0.006/мин без нагрузки на ПК |
| ИИ-модель | любой **OpenAI-совместимый API** (OpenAI, Groq, DeepSeek, OpenRouter, Ollama) | провайдер и модель меняются в `.env`, код не трогаем |
| Хранилище | JSON-файлы в `data/jobs/` | без БД; для одного сервера достаточно |
| Frontend | один HTML + vanilla JS | без сборки и node |
| Деплой | `run.sh` или Docker (образ ~600 МБ) | Railway / Fly.io / любой VPS за $3–5 |

## Структура репозитория

```
yt-summarizer/
├── app/
│   ├── main.py                 # FastAPI-приложение, /health, отдача фронтенда
│   ├── config.py               # все настройки (pydantic-settings, .env)
│   ├── logging_config.py       # логи в stdout + data/app.log с ротацией
│   ├── models.py               # Pydantic-схемы запросов/ответов, Job
│   ├── api/
│   │   └── routes.py           # REST-эндпоинты всех этапов
│   └── services/
│       ├── youtube.py          # yt-dlp: метаданные, субтитры, аудио
│       ├── transcribe.py       # faster-whisper / OpenAI Whisper
│       ├── llm.py              # клиент ИИ-модели (заменяемый провайдер)
│       ├── analysis.py         # промпты: структура, резюме, идеи (map-reduce)
│       ├── storage.py          # хранилище задач (JSON-файлы)
│       └── pipeline.py         # ОСНОВНОЙ ОБРАБОТЧИК: ссылка → результат
├── frontend/
│   ├── index.html              # форма: ссылка → кнопка → результат
│   ├── app.js                  # запрос /api/process + опрос статуса
│   └── style.css
├── tests/
│   └── test_api.py             # pytest, без сети (моки)
├── data/                       # задачи, логи, кэш модели (в .gitignore)
├── .env.example                # шаблон конфигурации
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
├── run.sh                      # запуск без Docker одной командой
└── pyproject.toml              # ruff / pytest
```

## Быстрый запуск на слабом ПК (без Docker)

Нужны: Python 3.10+, `ffmpeg` (`sudo apt install ffmpeg` / `brew install ffmpeg` / [Windows](https://www.gyan.dev/ffmpeg/builds/)).

```bash
git clone https://github.com/<you>/yt-summarizer.git
cd yt-summarizer
cp .env.example .env        # впишите LLM_API_KEY
./run.sh                    # создаст .venv, поставит зависимости, запустит сервер
```

Откройте http://localhost:8000 — форма. http://localhost:8000/docs — Swagger, http://localhost:8000/health — статус.

Windows без bash:

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --port 8000
```

### Рекомендации для слабого железа

- `WHISPER_MODEL=tiny` (~75 МБ, ~1× реального времени на 2 ядрах) или `base` (~145 МБ, лучше качество). `small` — только если ≥4 ГБ RAM.
- `WHISPER_COMPUTE_TYPE=int8` — уже по умолчанию, в 2–3 раза быстрее fp32.
- Оставьте `PREFER_YOUTUBE_SUBTITLES=true`: у большинства видео есть авто-субтитры, и Whisper вообще не запускается.
- Совсем слабый ПК → `TRANSCRIBER=openai`: аудио уходит в Whisper API, локально ничего не считается.
- Дешёвая/бесплатная ИИ-модель: `LLM_PROVIDER=groq` + `LLM_MODEL=llama-3.1-8b-instant` (бесплатный тариф) или `LLM_PROVIDER=ollama` + `LLM_MODEL=qwen2.5:3b` (полностью офлайн, нужно ~3 ГБ RAM).
- `MAX_VIDEO_MINUTES` — ограничьте, чтобы не ждать час транскрибации.

## Запуск в Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

Модель whisper и задачи сохраняются в `./data`, так что при перезапуске ничего не скачивается заново.

## Деплой на VPS (одна команда)

### Где взять сервер

Нужен VPS с **1 vCPU / 2 ГБ RAM / 20 ГБ диска**, Ubuntu 22.04 или 24.04. С `TRANSCRIBER=openai` хватит и 1 ГБ. Примеры (цены — ориентир):

| Хостинг | Тариф | Цена/мес | Заметки |
|---|---|---|---|
| [Hetzner Cloud](https://www.hetzner.com/cloud) | CX22 (2 vCPU / 4 ГБ) | ~€4 | лучшее соотношение цена/мощность, Германия/Финляндия |
| [Timeweb Cloud](https://timeweb.cloud) | 1 vCPU / 2 ГБ | ~300 ₽ | оплата картами РФ |
| [Selectel / VDSina / Aeza](https://aeza.net) | 1 vCPU / 2 ГБ | 300–500 ₽ | оплата картами РФ |
| [DigitalOcean](https://www.digitalocean.com) | Basic 1 ГБ | $6 | есть стартовый кредит |
| [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/) | ARM 4 vCPU / 24 ГБ | $0 | бесплатно навсегда, но регистрация капризная |

Важно: с IP дата-центров YouTube часто отвечает «Sign in to confirm you're not a bot» — см. раздел ниже про `YT_COOKIES_FILE`/`YT_PROXY`. Хостинги с «домашними»/резидентными IP этой проблемы обычно не имеют.

### Установка

На свежем сервере (по SSH под root или через sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/ewgen5555/yt-summarizer/main/deploy/install.sh | sudo bash -s -- \
  --key sk_ВАШ_КЛЮЧ --provider inception --model mercury-2.5
```

Скрипт поставит Docker, добавит 2 ГБ swap, склонирует репо в `/opt/yt-summarizer`, создаст `.env` и запустит контейнер с автозапуском. Через минуту сервис доступен на `http://IP_СЕРВЕРА:8000`.

С доменом и HTTPS (заранее направьте A-запись домена на IP сервера):

```bash
curl -fsSL https://raw.githubusercontent.com/ewgen5555/yt-summarizer/main/deploy/install.sh | sudo bash -s -- \
  --key sk_ВАШ_КЛЮЧ --domain yt.example.com
```

Поднимется Caddy, сертификат Let's Encrypt выпустится автоматически → `https://yt.example.com`.

Опции: `--transcriber openai` (не считать whisper на сервере), `--whisper base` (модель whisper, по умолчанию `tiny`).

### Обслуживание

```bash
cd /opt/yt-summarizer
docker compose logs -f            # логи
nano .env && docker compose up -d # изменить настройки
./deploy/update.sh                # обновить до последней версии
```

Файлы: `deploy/install.sh` — установщик, `deploy/update.sh` — обновление, `deploy/Caddyfile` + `deploy/docker-compose.caddy.yml` — HTTPS-прокси.

## Смена ИИ-модели (конфиг)

Всё в `.env`, код менять не нужно:

```env
LLM_PROVIDER=openai      # openai | openrouter | groq | deepseek | inception | ollama | custom
LLM_API_KEY=...
LLM_MODEL=gpt-4o-mini
# LLM_BASE_URL=https://my-proxy.example/v1   # для custom
```

Для провайдеров из списка `base_url` подставляется автоматически (`app/services/llm.py`, словарь `PROVIDER_URLS`). Любой другой сервис с OpenAI-совместимым `/chat/completions` подключается через `LLM_PROVIDER=custom` + `LLM_BASE_URL`.

## API

| Метод | Путь | Что делает |
|---|---|---|
| `GET` | `/health` | статус сервера, версия, провайдер, число задач, uptime |
| `POST` | `/api/process` | `{url}` → запускает полный пайплайн в фоне, возвращает `Job` (202) |
| `GET` | `/api/jobs/{id}` | статус/прогресс/результат задачи |
| `GET` | `/api/jobs` | последние задачи |
| `POST` | `/api/transcript` | `{url}` → транскрипт (синхронно) |
| `POST` | `/api/structure` | `{text}` → тема + разделы |
| `POST` | `/api/summary` | `{text}` → краткое резюме |
| `POST` | `/api/ideas` | `{text}` → список ключевых идей |

```bash
curl -X POST localhost:8000/api/process -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
# {"id":"3f9c...","status":"queued",...}
curl localhost:8000/api/jobs/3f9c...
```

Статусы задачи: `queued → downloading → transcribing → analyzing → done | error`.

## Логи и мониторинг

- Логи пишутся в stdout и в `data/app.log` (ротация 5 МБ × 3). Уровень — `LOG_LEVEL`.
- Каждый вызов модели логирует расход токенов — удобно следить за стоимостью.
- `/health` подходит для uptime-мониторов (UptimeRobot, Docker `HEALTHCHECK` уже прописан).

## Если YouTube пишет «Sign in to confirm you're not a bot»

С IP-адресов дата-центров YouTube часто требует вход. Варианты:

1. Экспортировать cookies из своего браузера (расширение *Get cookies.txt LOCALLY*) в `data/cookies.txt` и задать `YT_COOKIES_FILE=./data/cookies.txt`;
2. Указать прокси с домашним IP: `YT_PROXY=socks5://user:pass@host:1080`.

На домашнем ПК проблема обычно не возникает.

## Разработка

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

## Дальнейшее развитие

- очередь задач (RQ/Redis) и несколько воркеров;
- SQLite вместо JSON-файлов, история пользователя;
- экспорт результата в Markdown/Notion/Telegram;
- таймкоды в структуре (сегменты whisper уже их содержат).
