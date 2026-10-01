"""Clientes simulados para ensaiar a UI sem gastar. Números NÃO são medições reais."""
from __future__ import annotations

import asyncio
import hashlib
import random

from ..questions import Question
from .base import CallResult

PROFILE = {  # latência em segundos (min, max), preço por token de entrada/saída, tokens de saída por pergunta
    "jev": dict(lat=(0.25, 0.55), pin=0.042e-6, pout=0.0, out_per_q=18),
    "llm_frontier": dict(lat=(1.5, 4.5), pin=3e-6, pout=15e-6, out_per_q=30),
}


class MockClient:
    def __init__(self, key: str):
        self.key = key
        self.p = PROFILE[key]

    async def classify(self, state: dict, questions: list[Question]) -> CallResult:
        text = " ".join(str(v) for v in state.values())
        rng = random.Random(hashlib.sha256((self.key + text).encode()).hexdigest())
        lat = rng.uniform(*self.p["lat"])
        await asyncio.sleep(lat)
        answers, raw = {}, {}
        for q in questions:
            conf = rng.choice([rng.uniform(0.9, 1.0)] * 4 + [rng.uniform(0.5, 0.75)])
            if q.type == "noul":
                p = conf if rng.random() < 0.5 else 1 - conf
                answers[q.name] = {"kind": "noul", "value": p >= 0.5, "p": round(p, 4), "confidence": round(max(p, 1 - p), 4)}
            elif q.type == "choice":
                answers[q.name] = {"kind": "choice", "value": rng.choice(q.options).key, "confidence": round(conf, 4)}
            else:
                answers[q.name] = {"kind": "score", "value": round(rng.uniform(1, len(q.levels)), 2), "confidence": round(conf, 4)}
            raw[q.name] = {"simulado": True}
        in_t = int(len(text + "".join(q.instructions for q in questions)) / 3.5) + 60
        out_t = self.p["out_per_q"] * len(questions)
        cost = in_t * self.p["pin"] + (0 if self.key == "jev" else out_t * self.p["pout"])
        return CallResult(answers=answers, raw=raw, latency_ms=lat * 1000, cost_usd=cost, in_tokens=in_t, out_tokens=out_t)
