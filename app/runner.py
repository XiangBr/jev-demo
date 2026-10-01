"""Executa uma rodada: cada provedor classifica os mesmos leads em paralelo e emite eventos."""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path

import httpx

from . import scoring
from .clients.jev import JevClient
from .clients.llm import LLMClient
from .clients.mock import MockClient
from .questions import Question


class Run:
    def __init__(self, run_id: str, runs_dir: Path):
        self.id = run_id
        self.events: list[dict] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self.t0 = time.perf_counter()
        self._waiters: list[asyncio.Future] = []
        self._file = open(runs_dir / f"{run_id}.jsonl", "w", encoding="utf-8")
        self.started_leads: set[str] = set()
        self.results: dict[str, dict[str, dict]] = {}
        self.elapsed: dict[str, float] = {}

    def emit(self, type_: str, **data):
        ev = {"type": type_, "t": round(time.perf_counter() - self.t0, 3), **data}
        self.events.append(ev)
        self._file.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._wake()

    def _wake(self):
        for f in self._waiters:
            if not f.done():
                f.set_result(None)
        self._waiters.clear()

    def close(self):
        self.done = True
        self._file.close()
        self._wake()

    async def stream(self, start: int = 0):
        i = start
        while True:
            while i < len(self.events):
                yield self.events[i]
                i += 1
            if self.done:
                return
            fut = asyncio.get_running_loop().create_future()
            self._waiters.append(fut)
            await fut


def sample_leads(leads: list[dict], n: int, seed: int) -> list[dict]:
    if n >= len(leads):
        return leads
    idx = sorted(random.Random(seed).sample(range(len(leads)), n))
    return [leads[i] for i in idx]


def make_client(key: str, http: httpx.AsyncClient, api_key: str, cfg: dict, slug: str | None, mock: bool):
    if mock:
        return MockClient(key)
    if key == "jev":
        return JevClient(http, api_key, cfg["jev"])
    return LLMClient(http, api_key, slug or cfg["llm"]["frontier"]["slug"], cfg["llm"]["frontier"])


async def execute(run: Run, *, cfg: dict, api_key: str, req: dict, questions: list[Question], leads: list[dict],
                  gab: dict, mock: bool):
    threshold = req["threshold"]
    fields = req["state_fields"]
    models = req["models"]  # [{key, label, slug}]
    keys = [m["key"] for m in models]
    run.emit("run_started", run_id=run.id, mock=mock, questions=[q.model_dump() for q in questions], models=models,
             n=len(leads), threshold=threshold, usd_brl=req["usd_brl"], state_fields=fields,
             dataset_name=req.get("dataset_name"),
             gabarito=[q.name for q in questions if scoring.gab_applicable(q, gab)])
    for k in keys:
        run.results[k] = {}

    async def provider(m: dict, http: httpx.AsyncClient):
        key = m["key"]
        client = make_client(key, http, api_key, cfg, m.get("slug"), mock)
        conc = cfg["jev"]["concurrency"] if key == "jev" else cfg["llm"]["frontier"]["concurrency"]
        queue: asyncio.Queue = asyncio.Queue()
        for lead in leads:
            queue.put_nowait(lead)

        async def worker():
            while True:
                try:
                    lead = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                if lead["id"] not in run.started_leads:
                    run.started_leads.add(lead["id"])
                    run.emit("lead_started", lead=lead)
                res = await client.classify({f: lead.get(f, "") for f in fields}, questions)
                low = bool(res.answers) and any(a["confidence"] < threshold for a in res.answers.values())
                ev = dict(lead_id=lead["id"], model=key, answers=res.answers, raw=res.raw, low_confidence=low,
                          latency_ms=round(res.latency_ms, 1), cost_usd=res.cost_usd, in_tokens=res.in_tokens,
                          out_tokens=res.out_tokens, billed_out_tokens=0 if key == "jev" else res.out_tokens,
                          retries=res.retries, error=res.error)
                run.results[key][lead["id"]] = ev
                run.emit("lead_result", **ev)

        await asyncio.gather(*[worker() for _ in range(conc)])
        run.elapsed[key] = round(time.perf_counter() - run.t0, 2)
        run.emit("model_finished", model=key, elapsed_s=run.elapsed[key])

    stopped = False
    try:
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
        async with httpx.AsyncClient(limits=limits) as http:
            await asyncio.gather(*[provider(m, http) for m in models])
    except asyncio.CancelledError:
        stopped = True
        for k in keys:
            run.elapsed.setdefault(k, round(time.perf_counter() - run.t0, 2))
    finally:
        summary = scoring.summarize(questions, keys, run.results, gab, threshold, run.elapsed)
        run.emit("run_finished", stopped=stopped, summary=summary)
        run.close()
