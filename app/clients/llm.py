from __future__ import annotations

import json
import re

import httpx

from ..questions import Question, llm_prompt, llm_schema, normalize
from .base import OPENROUTER, CallError, CallResult, RateLimiter, post_json


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


class LLMClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str, slug: str, cfg: dict):
        self.http = http
        self.key = api_key
        self.slug = slug
        self.limiter = RateLimiter(cfg.get("rpm", 0))
        self.use_schema = True

    async def classify(self, state: dict, questions: list[Question]) -> CallResult:
        system, user = llm_prompt(questions, state)
        payload = {
            "model": self.slug,
            "temperature": 0,
            "usage": {"include": True},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.use_schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "classificacao", "strict": True, "schema": llm_schema(questions)}}
        else:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        url = OPENROUTER + "/v1/chat/completions"
        try:
            try:
                data, ms, retries = await post_json(self.http, url, headers, payload, self.limiter)
            except CallError as e:
                if self.use_schema and str(e).startswith("HTTP 400"):
                    self.use_schema = False  # modelo sem json_schema: cai para json_object
                    payload["response_format"] = {"type": "json_object"}
                    data, ms, retries = await post_json(self.http, url, headers, payload, self.limiter)
                else:
                    raise
            content = data["choices"][0]["message"]["content"]
            raw = _parse_json(content)
            answers = {q.name: normalize(q, raw[q.name], "llm") for q in questions}
        except (CallError, KeyError, IndexError, ValueError, TypeError) as e:
            return CallResult(error=f"{type(e).__name__}: {e}" if not isinstance(e, CallError) else str(e))
        usage = data.get("usage", {})
        return CallResult(answers=answers, raw=raw, latency_ms=ms, cost_usd=float(usage.get("cost") or 0),
                          in_tokens=int(usage.get("prompt_tokens", 0)),
                          out_tokens=int(usage.get("completion_tokens", 0)), retries=retries)
