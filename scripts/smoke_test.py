"""Teste de fumaça: 1 lead (todas as colunas) em cada provedor, imprimindo a resposta bruta, tokens e custo.

Roda antes de qualquer execução grande. Confirma o formato de `state` e de `criteria` que o Jev aceita e
se o custo (`usage.cost`) chega em todas as respostas.

    python scripts/smoke_test.py
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.clients.base import OPENROUTER  # noqa: E402
from app.clients.jev import JevClient  # noqa: E402
from app.clients.llm import LLMClient  # noqa: E402
from app.questions import PRESET_REELS, jev_questions, validate_questions  # noqa: E402

load_dotenv(ROOT / ".env")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
KEY = os.getenv("OPENROUTER_API_KEY", "")


def short(obj, n=1400):
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    return s if len(s) <= n else s[:n] + " ..."


async def raw_jev(http: httpx.AsyncClient, path: str, state, qs) -> None:
    payload = {"model": CFG["jev"]["model"], "state": state, "questions": jev_questions(qs)}
    r = await http.post(OPENROUTER + path, json=payload, timeout=60,
                        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    kind = "objeto" if isinstance(state, dict) else "texto"
    print(f"\n--- Jev {path} | state como {kind} | HTTP {r.status_code}")
    try:
        print(short(r.json()))
    except ValueError:
        print(r.text[:600])


async def main() -> int:
    if not KEY:
        print("OPENROUTER_API_KEY ausente. Copie .env.example para .env e preencha.")
        return 1
    with open(ROOT / CFG["leads_csv"], encoding="utf-8-sig", newline="") as f:
        lead = next(csv.DictReader(f))
    state = {k: v for k, v in lead.items() if k != "id"}
    qs = validate_questions(PRESET_REELS)
    print("Lead de teste:", json.dumps(state, ensure_ascii=False))

    async with httpx.AsyncClient() as http:
        print("\n=== 1. Formato bruto do Jev (qual combinação a API aceita) ===")
        await raw_jev(http, "/v1/systemone", state, qs)
        await raw_jev(http, "/v1/systemone", json.dumps(state, ensure_ascii=False), qs)
        await raw_jev(http, "/alpha/decisions", state, qs)
        await raw_jev(http, "/alpha/decisions", json.dumps(state, ensure_ascii=False), qs)

        print("\n=== 2. Cliente do app (é o que roda de verdade) ===")
        jev = await JevClient(http, KEY, CFG["jev"]).classify(state, qs)
        print("Jev :", "ERRO " + jev.error if jev.error else short(jev.answers, 900))
        print(f"      {jev.latency_ms:.0f} ms | entrada {jev.in_tokens} tok | saída {jev.out_tokens} tok | US$ {jev.cost_usd:.7f}")

        c = CFG["llm"]["frontier"]
        res = await LLMClient(http, KEY, c["slug"], c).classify(state, qs)
        print(f"\n{c['label']} ({c['slug']}):", "ERRO " + res.error if res.error else short(res.answers, 900))
        print(f"      {res.latency_ms:.0f} ms | entrada {res.in_tokens} tok | saída {res.out_tokens} tok | US$ {res.cost_usd:.7f}")
        if not res.error and res.cost_usd == 0:
            print("      AVISO: usage.cost veio zerado. Confira o slug e o campo usage na resposta.")

    print("\nSe 'Jev' deu ERRO, compare os HTTP da seção 1 e ajuste `endpoint` em config.yaml "
          "(systemone ou decisions) e o formato de state/criteria em app/questions.py.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
