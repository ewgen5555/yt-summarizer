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
- Jobs are owned by a client id: `token:<hash>` when `API_TOKEN` is set, else `anon:<hash of IP>`.
  Ownership is enforced in `GET /api/jobs*` (foreign jobs return 404, not 403). Raw tokens and IPs
  are never written to disk — only hashes.
- `owner` must be stripped from API responses. Use the `JobPublic` model, **not**
  `response_model_exclude={"owner"}`: FastAPI does not apply that exclusion to items of a
  `list[Job]`, so `owner` leaks from `GET /api/jobs`.
- Rate limiting lives in `app/services/security.py` and is in-process (`--workers 1`). Two classes:
  cheap reads (`GET /api/jobs*`) and paid work (transcription + LLM). Keep them separate — the
  frontend polls `/api/jobs/{id}` every 2.5 s and would exhaust a single shared bucket.
- The concurrency slot must not be acquired in a FastAPI dependency: the request body is validated
  *after* dependencies run, so a 422 would leak the slot forever. Acquire it in the endpoint body.
- `TRUST_PROXY` must stay `false` unless the app sits behind a proxy that overwrites
  `X-Forwarded-For`; otherwise the header is spoofable and bypasses per-IP limits. Only the last
  XFF entry is trusted. The hosted demo proxy does forward a client IP, so the demo `.env` sets
  `TRUST_PROXY=true`.
- Limiters hold in-memory counters, so `tests/test_api.py` has an autouse fixture calling
  `security.reset_all()`; without it the suite is order-dependent and flaky.
- `deploy/install.sh` enables `API_TOKEN` by default and prints it at the end (open access needs
  `--no-auth`). It also enables `TRUST_PROXY` when `--domain` (Caddy) is used.
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
- Merging one branch into another can silently revert a fix: the merge that brought `main` into the
  auth branch kept both sides of two conflicts in `app/api/routes.py`, leaving two definitions of
  `POST /api/process` and `GET /api/jobs`. Python keeps the last one, so the old unauthenticated
  handlers shipped while CI stayed green on both branches. After any merge, run `ruff check .` and
  `pytest` on the *merge commit* and look for duplicate definitions (`ruff check --select F811`).
- Keep test-only monkeypatching out of `autouse` fixtures unless every test wants it. An autouse
  fixture that stubs `analysis.analyze`/`pipeline.get_transcript` shadows the real functions in tests
  that verify those functions directly, so those tests pass against the stub instead of the code.
