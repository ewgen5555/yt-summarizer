# AGENTS.md

## Project

YouTube AI Summarizer: FastAPI backend (`app/`), vanilla-JS frontend (`frontend/`), JSON job store in `data/jobs`.
Pipeline: URL → subtitles (yt-dlp) or Whisper → LLM analysis (OpenAI-compatible).

## Commands

```bash
pip install -r requirements-dev.txt
python -m pytest -q        # tests are network-free; YouTube/LLM are monkeypatched
ruff check .               # line-length 110, rules E,F,I,B,UP
docker build --tag yt-summarizer:ci .
docker compose up -d --build   # needs a .env; copy from .env.example
```

`.env` is gitignored and `docker-compose.yml` uses `env_file: .env`, so `docker compose` fails
without it — copy `.env.example` first.

## Conventions and gotchas

- Config is a pydantic `Settings` singleton (`app/config.py`); tests override it via
  `monkeypatch.setattr(settings, "data_dir", tmp_path)`.
- `settings.jobs_dir` / `media_dir` are properties with `mkdir` side effects, so they must be
  read lazily — rebinding `data_dir` after import is safe.
- Deploy scripts run under `set -euo pipefail`. Any command that can legitimately fail on a
  minimal VPS (swap, fstab, ufw) must be guarded, otherwise it kills the installer.
- CI lives in `.github/workflows/ci.yml`. GitHub rejects an empty workflow file, so never commit
  a placeholder YAML under `.github/workflows/`.
- The deploy target is `/opt/yt-summarizer`; `deploy/Caddyfile.local` marks an HTTPS setup and is
  gitignored (generated from `deploy/Caddyfile` by `install.sh`).
