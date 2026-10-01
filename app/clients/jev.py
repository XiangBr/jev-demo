from __future__ import annotations

import httpx

from ..questions import Question, jev_questions, normalize
from .base import OPENROUTER, CallError, CallResult, RateLimiter, post_json


class JevClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str, cfg: dict):
        self.http = http
        self.key = api_key
        self.model = cfg["model"]
        self.price = cfg["input_price_per_mtok"]
        path = "/alpha/decisions" if cfg.get("endpoint") == "decisions" else "/v1/systemone"
        self.url = OPENROUTER + path
        self.limiter = RateLimiter(cfg.get("rpm", 0))

    async def classify(self, state: dict, questions: list[Question]) -> CallResult:
        payload = {"model": self.model, "state": state, "questions": jev_questions(questions)}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        try:
            data, ms, retries = await post_json(self.http, self.url, headers, payload, self.limiter)
            raw_answers = data["answers"]
            answers = {q.name: normalize(q, raw_answers[q.name], "jev") for q in questions}
        except (CallError, KeyError, ValueError, TypeError) as e:
            return CallResult(error=str(e))
        usage = data.get("usage", {})
        in_t = int(usage.get("input_tokens", 0))
        cost = usage.get("cost")
        if cost is None:
            cost = in_t * self.price / 1e6
        return CallResult(answers=answers, raw=raw_answers, latency_ms=ms, cost_usd=float(cost), in_tokens=in_t,
                          out_tokens=int(usage.get("output_tokens", 0)), retries=retries)
