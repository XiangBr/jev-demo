from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

OPENROUTER = "https://openrouter.ai/api"


class CallError(Exception):
    pass


@dataclass
class CallResult:
    answers: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0
    retries: int = 0
    error: str | None = None


class RateLimiter:
    """Espaça as chamadas para respeitar um limite de requisições por minuto."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / rpm if rpm else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        if not self.interval:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            await asyncio.sleep(wait)


async def post_json(client: httpx.AsyncClient, url: str, headers: dict, payload: dict,
                    limiter: RateLimiter | None = None, attempts: int = 4) -> tuple[dict, float, int]:
    """POST com retry em 429/5xx/timeout. Retorna (json, latência_ms da tentativa bem-sucedida, retries)."""
    last = "erro desconhecido"
    for attempt in range(attempts):
        if limiter:
            await limiter.acquire()
        t0 = time.perf_counter()
        try:
            r = await client.post(url, headers=headers, json=payload, timeout=90)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = f"rede: {type(e).__name__}"
        else:
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                try:
                    return r.json(), ms, attempt
                except ValueError:
                    last = "resposta não é JSON"
            elif r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                ra = r.headers.get("retry-after")
                if ra and ra.replace(".", "", 1).isdigit():
                    await asyncio.sleep(min(float(ra), 10))
            else:
                raise CallError(f"HTTP {r.status_code}: {r.text[:300]}")
        if attempt < attempts - 1:
            await asyncio.sleep(min(2 ** attempt, 8))
    raise CallError(f"{last} após {attempts} tentativas")
