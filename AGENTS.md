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
- `tests/test_api.py` and `tests/test_youtube.py` monkeypatch `yt_dlp` and the transcriber, so the
  real audio path is not covered by default. Bugs that only show up on actual media need an explicit
  test; `tests/test_transcribe_vad.py` builds a speech-over-music track with `espeak-ng` for that.
  CI installs `espeak-ng` so the test runs there, and it fails (rather than skipping) when its
  prerequisites are missing — a silent skip is how the empty-transcript bug shipped.
- `faster-whisper`'s bundled Silero VAD returns no segments on audio with music under the speech,
  so `WHISPER_VAD` defaults to false. Turning it on will blank out music videos, intros and ads.
- Media files are named by `video_id` and shared between jobs; same-video work must stay under
  `youtube.video_lock` or concurrent jobs race on the same file.
- Only YouTube hosts are accepted (`youtube._ALLOWED_HOSTS`); the video-id regex alone would let any
  URL through to yt-dlp.
