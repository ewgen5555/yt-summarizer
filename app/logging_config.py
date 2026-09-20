import logging
import logging.handlers
import sys
from pathlib import Path


def setup_logging(level: str, data_dir: Path) -> None:
    """Логи в stdout (для docker/systemd) и в файл data/app.log с ротацией."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    file_handler = logging.handlers.RotatingFileHandler(
        data_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    root.addHandler(stream)
    root.addHandler(file_handler)

    # приглушаем шумные библиотеки
    for noisy in ("httpx", "httpcore", "urllib3", "yt_dlp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
