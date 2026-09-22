"""Доступ к API: опознание клиента, владение задачами и лимиты запросов.

Состояние держится в памяти процесса: приложение запускается с `--workers 1`
(см. Dockerfile), поэтому отдельное хранилище не нужно. При переходе на несколько
воркеров лимиты и счётчики придётся вынести в Redis — иначе у каждого воркера
будет свой бюджет.
"""
import hashlib
import hmac
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from app.config import settings

log = logging.getLogger(__name__)

ANONYMOUS_OWNER = "legacy"  # задачи, созданные до появления владельцев


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def anonymous_owner(ip: str) -> str:
    """Идентификатор неавторизованного клиента: хэш IP, чтобы адрес не оседал в файлах задач."""
    return f"anon:{_hash(ip)}"


def token_owner(token: str) -> str:
    """Идентификатор владельца токена: сам токен на диск не попадает."""
    return f"token:{_hash(token)}"


def client_ip(request: Request) -> str:
    """IP клиента. За прокси берём последний элемент X-Forwarded-For: его дописывает наш прокси,
    а всё, что клиент прислал слева, подделывается и потому игнорируется."""
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def authenticate(request: Request) -> str:
    """Возвращает идентификатор клиента для проверки владения задачей.

    Токен задан — требуем его и различаем клиентов по хэшу токена.
    Токена нет — API открыт, различаем по IP (хэш, чтобы не хранить адрес на диске).
    """
    expected = settings.api_token
    provided = ""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        provided = header[7:].strip()

    if expected:
        # compare_digest — сравнение за постоянное время, чтобы токен нельзя было подобрать по времени ответа
        if not provided or not hmac.compare_digest(provided, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Нужен заголовок Authorization: Bearer <API_TOKEN>",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return token_owner(provided)

    return anonymous_owner(client_ip(request))


def owns(job_owner: str, client_id: str) -> bool:
    """Чужие задачи не отличаем от несуществующих, чтобы не подтверждать их наличие."""
    if job_owner == client_id:
        return True
    # Старые задачи без владельца доступны только владельцу токена.
    return job_owner == ANONYMOUS_OWNER and client_id.startswith("token:")


def _retry_after(reset_at: float) -> str:
    return str(max(1, int(reset_at - time.time()) + 1))


@dataclass
class _Bucket:
    """Скользящее окно: храним времена запросов внутри окна, этого достаточно для одного процесса."""

    window: float
    hits: list[float] = field(default_factory=list)

    def allow(self, limit: int, now: float) -> tuple[bool, float]:
        self.hits = [t for t in self.hits if now - t < self.window]
        if len(self.hits) >= limit:
            return False, self.hits[0] + self.window
        self.hits.append(now)
        return True, 0.0


class RateLimiter:
    """Несколько независимых окон на одного клиента (минута, сутки).

    Лимиты запрашиваются при каждой проверке, а не запоминаются при импорте: иначе
    изменение настроек (в тестах или при правке .env с перезапуском) не действовало бы.
    """

    #: как часто подчищать записи неактивных клиентов (каждая N-я проверка)
    PRUNE_EVERY = 1000

    def __init__(self, name: str, limits_provider: Callable[[], list[tuple[str, int, float]]]) -> None:
        self.name = name
        self._limits_provider = limits_provider
        self._buckets: dict[str, dict[str, _Bucket]] = defaultdict(dict)
        self._lock = threading.Lock()
        self._checks = 0

    def check(self, client_id: str) -> None:
        if not settings.rate_limit_enabled:
            return
        now = time.time()
        with self._lock:
            self._checks += 1
            if self._checks % self.PRUNE_EVERY == 0:
                self._prune(now)
            buckets = self._buckets[client_id]
            for key, limit, window in self._limits_provider():
                if limit <= 0:
                    continue
                bucket = buckets.setdefault(key, _Bucket(window=window))
                ok, reset_at = bucket.allow(limit, now)
                if not ok:
                    log.warning("rate limit %s/%s для %s: %s запросов за %s с",
                                self.name, key, client_id, limit, int(window))
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=f"Слишком много запросов ({self.name}, лимит {limit} за "
                               f"{int(window)} с). Повторите позже.",
                        headers={"Retry-After": _retry_after(reset_at)},
                    )

    def _prune(self, now: float) -> None:
        """Иначе при переборе IP словарь клиентов растёт без предела."""
        for cid in list(self._buckets):
            if all(now - hits[-1] >= b.window
                   for b in self._buckets[cid].values()
                   if (hits := b.hits)):
                del self._buckets[cid]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._checks = 0


# Дорогие запросы: транскрибация + вызовы LLM.
cost_limiter = RateLimiter(
    "запуск обработки",
    lambda: [
        ("minute", settings.rate_limit_process_per_minute, 60.0),
        ("day", settings.rate_limit_process_per_day, 86400.0),
    ],
)
# Дешёвые чтения: фронтенд опрашивает /api/jobs/{id} каждые 2.5 с.
read_limiter = RateLimiter(
    "чтение",
    lambda: [("minute", settings.rate_limit_read_per_minute, 60.0)],
)


class ConcurrencyLimiter:
    """Ограничение числа одновременно обрабатываемых задач на клиента.

    Задачу отклоняем сразу (429), а не ставим в очередь: пользователь должен видеть,
    почему ничего не происходит, вместо бесконечного ожидания.
    """

    def __init__(self) -> None:
        self._active: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def acquire(self, client_id: str) -> None:
        limit = settings.max_concurrent_jobs_per_client
        if not settings.rate_limit_enabled or limit <= 0:
            return
        with self._lock:
            if self._active[client_id] >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Уже обрабатывается {limit} задачи. Дождитесь завершения.",
                    headers={"Retry-After": "30"},
                )
            self._active[client_id] += 1

    def release(self, client_id: str) -> None:
        with self._lock:
            if self._active.get(client_id):
                self._active[client_id] -= 1
                if self._active[client_id] <= 0:
                    self._active.pop(client_id, None)

    def reset(self) -> None:
        with self._lock:
            self._active.clear()


concurrency_limiter = ConcurrencyLimiter()


@contextmanager
def job_slot(client_id: str) -> Iterator[None]:
    """Слот на время синхронной обработки: снимается всегда, даже при исключении."""
    concurrency_limiter.acquire(client_id)
    try:
        yield
    finally:
        concurrency_limiter.release(client_id)


def reset_all() -> None:
    """Сбрасывает счётчики — используется в тестах, чтобы лимиты не протекали между ними."""
    cost_limiter.reset()
    read_limiter.reset()
    concurrency_limiter.reset()
