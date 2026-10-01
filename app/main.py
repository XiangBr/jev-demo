from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import datasets, rules, scoring
from .questions import PRESET_REELS, validate_questions
from .runner import Run, execute, sample_leads

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
RUNS_DIR = ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)
ENV_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

# Planilhas disponíveis. "exemplo" é o CSV de leads fictícios do repositório; uploads ficam só na memória.
DATASETS: dict[str, dict] = {}
MAX_UPLOADS = 5
EXAMPLE_PATH = ROOT / CFG["leads_csv"]
if EXAMPLE_PATH.exists():
    _cols, _rows = datasets.load_csv_file(EXAMPLE_PATH)
    DATASETS["exemplo"] = {"id": "exemplo", "name": EXAMPLE_PATH.name, "columns": _cols, "rows": _rows, "example": True}
GAB = scoring.load_gabarito(ROOT / CFG["gabarito_csv"])

RUNS: dict[str, Run] = {}
MODEL_CACHE: dict = {"t": 0, "data": []}

app = FastAPI(title="Jev vs LLMs")


class ModelSel(BaseModel):
    key: str = Field(pattern="^(jev|llm_frontier)$")
    slug: str | None = None


class RunReq(BaseModel):
    questions: list[dict]
    models: list[ModelSel]
    n: int = Field(ge=1, le=100000)
    seed: int = 42
    threshold: float = Field(default=CFG["confidence_threshold"], ge=0, le=1)
    usd_brl: float = Field(default=CFG["usd_brl"], gt=0)
    dataset: str = "exemplo"
    state_fields: list[str] = []  # vazio = todas as colunas menos o id
    api_key: str | None = Field(default=None, repr=False)  # chave digitada na página; nunca vai para disco
    mock: bool = False


class KeyReq(BaseModel):
    api_key: str | None = Field(default=None, repr=False)


def _key(given: str | None) -> str:
    return (given or "").strip() or ENV_KEY


def _ds_meta(ds: dict) -> dict:
    return {"id": ds["id"], "name": ds["name"], "columns": ds["columns"], "n_rows": len(ds["rows"]),
            "example": ds.get("example", False), "preview": ds["rows"][:5]}


def _dataset(ds_id: str) -> dict:
    ds = DATASETS.get(ds_id)
    if not ds:
        raise HTTPException(404, "Planilha não encontrada. O servidor pode ter reiniciado: suba o arquivo de novo.")
    return ds


def _fields(req: RunReq, ds: dict) -> list[str]:
    return req.state_fields or [c for c in ds["columns"] if c != "id"]


def _labels(models: list[ModelSel]) -> list[dict]:
    out = []
    for m in models:
        if m.key == "jev":
            out.append({"key": "jev", "label": "Jev", "slug": CFG["jev"]["model"]})
        else:
            out.append({"key": m.key, "label": CFG["llm"]["frontier"]["label"], "slug": m.slug or CFG["llm"]["frontier"]["slug"]})
    return out


def _check(req: RunReq):
    try:
        qs = validate_questions(req.questions)
    except Exception as e:  # erros do Pydantic viram mensagem legível
        msg = "; ".join(x["msg"].replace("Value error, ", "") for x in e.errors()) if hasattr(e, "errors") else str(e)
        raise HTTPException(422, msg)
    if not req.models:
        raise HTTPException(422, "Selecione ao menos um modelo.")
    ds = _dataset(req.dataset)
    fields = _fields(req, ds)
    bad = [f for f in fields if f not in ds["columns"]]
    if bad or not fields:
        raise HTTPException(422, f"Colunas inválidas para enviar ao modelo: {bad or 'nenhuma selecionada'}")
    if not req.mock and not _key(req.api_key):
        raise HTTPException(422, "Sem chave do OpenRouter. Cole a chave no campo da página, configure o .env ou use o modo simulado.")
    return qs, ds, fields


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/api/config")
def config():
    return {
        "usd_brl": CFG["usd_brl"], "threshold": CFG["confidence_threshold"], "has_env_key": bool(ENV_KEY),
        "datasets": [_ds_meta(d) for d in DATASETS.values()],
        "max_rows": datasets.MAX_ROWS,
        "preset": PRESET_REELS,
        "models": {
            "jev": {"label": "Jev", "slug": CFG["jev"]["model"]},
            "llm_frontier": {"label": CFG["llm"]["frontier"]["label"], "slug": CFG["llm"]["frontier"]["slug"]},
        },
    }


@app.post("/api/datasets")
async def upload_dataset(file: UploadFile = File(...)):
    raw = await file.read(datasets.MAX_BYTES + 1)
    try:
        cols, rows = datasets.parse(file.filename or "", raw)
    except datasets.DatasetError as e:
        raise HTTPException(422, str(e))
    ds_id = "up-" + uuid.uuid4().hex[:10]
    uploads = [k for k in DATASETS if k.startswith("up-")]
    for old in uploads[:max(0, len(uploads) - MAX_UPLOADS + 1)]:
        DATASETS.pop(old, None)
    DATASETS[ds_id] = {"id": ds_id, "name": file.filename or "planilha", "columns": cols, "rows": rows}
    return _ds_meta(DATASETS[ds_id])


@app.get("/api/datasets/{ds_id}")
def get_dataset(ds_id: str):
    return _ds_meta(_dataset(ds_id))


@app.post("/api/key/check")
async def check_key(req: KeyReq):
    key = _key(req.api_key)
    if not key:
        return {"ok": False, "error": "Nenhuma chave informada e nenhuma no .env."}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Sem conexão com o OpenRouter ({type(e).__name__})."}
    if r.status_code != 200:
        return {"ok": False, "error": "Chave recusada pelo OpenRouter." if r.status_code in (401, 403) else f"HTTP {r.status_code}"}
    d = r.json().get("data", {})
    return {"ok": True, "source": "página" if (req.api_key or "").strip() else ".env", "label": d.get("label"),
            "usage": d.get("usage"), "limit": d.get("limit"), "limit_remaining": d.get("limit_remaining")}


async def _openrouter_models() -> list[dict]:
    if time.time() - MODEL_CACHE["t"] < 3600 and MODEL_CACHE["data"]:
        return MODEL_CACHE["data"]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://openrouter.ai/api/v1/models")
            r.raise_for_status()
        data = [{"id": m["id"], "name": m.get("name", m["id"]),
                 "prompt": float(m.get("pricing", {}).get("prompt") or 0),
                 "completion": float(m.get("pricing", {}).get("completion") or 0)} for m in r.json()["data"]]
        MODEL_CACHE.update(t=time.time(), data=data)
    except Exception:
        pass
    return MODEL_CACHE["data"]


@app.get("/api/models")
async def models():
    return await _openrouter_models()


@app.post("/api/estimate")
async def estimate(req: RunReq):
    qs, ds, fields = _check(req.model_copy(update={"mock": True}))
    rows = ds["rows"]
    n = min(req.n, len(rows))
    sample = sample_leads(rows, n, req.seed)[:200]
    state_chars = sum(len(f) + len(str(l.get(f, ""))) for l in sample for f in fields) / max(1, len(sample))
    q_chars = sum(len(q.instructions) + sum(len(o.key) + len(o.desc) for o in q.options) + sum(len(x) for x in q.levels) for q in qs)
    or_models = {m["id"]: m for m in await _openrouter_models()}
    out = {}
    for m in _labels(req.models):
        if m["key"] == "jev":
            # calibrado com execuções reais: ~260 tokens fixos por requisição + ~24 por pergunta
            tokens = 260 + 24 * len(qs) + (state_chars + q_chars) / 3
            out["jev"] = {"per_lead_usd": tokens * CFG["jev"]["input_price_per_mtok"] / 1e6, "note": "entrada apenas; saída grátis"}
        else:
            info = or_models.get(m["slug"])
            if not info:
                out[m["key"]] = {"per_lead_usd": None, "note": "modelo não encontrado no OpenRouter"}
                continue
            in_t = 250 + 120 * len(qs) + (state_chars + q_chars) / 3  # system prompt + formato de cada pergunta
            out_t = 14 * len(qs)
            out[m["key"]] = {"per_lead_usd": in_t * info["prompt"] + out_t * info["completion"],
                             "note": "sem contar raciocínio (modelos que pensam custam mais)"}
    return {"n": n, "per_model": out}


@app.post("/api/runs")
async def create_run(req: RunReq):
    qs, ds, fields = _check(req)
    leads = sample_leads(ds["rows"], min(req.n, len(ds["rows"])), req.seed)
    run_id = time.strftime("%Y%m%d-%H%M%S") + ("-mock" if req.mock else "")
    run = Run(run_id, RUNS_DIR)
    RUNS[run_id] = run
    body = req.model_dump(exclude={"api_key"})
    body["models"] = _labels(req.models)
    body["state_fields"] = fields
    body["dataset_name"] = ds["name"]
    # gabarito só vale para o CSV de exemplo (as respostas certas foram geradas junto com ele)
    gab = GAB if ds.get("example") else {}
    run.task = asyncio.create_task(execute(run, cfg=CFG, api_key=_key(req.api_key), req=body, questions=qs,
                                           leads=leads, gab=gab, mock=req.mock))
    return {"run_id": run_id}


@app.post("/api/runs/{run_id}/stop")
async def stop_run(run_id: str):
    run = RUNS.get(run_id)
    if not run or not run.task or run.done:
        raise HTTPException(404, "Execução não está rodando.")
    run.task.cancel()
    return {"ok": True}


def _saved_events(run_id: str) -> list[dict]:
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(404, "Execução não encontrada.")
    p = RUNS_DIR / f"{run_id}.jsonl"
    if not p.exists():
        raise HTTPException(404, "Execução não encontrada.")
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


@app.get("/api/runs")
def list_runs():
    out = []
    for p in sorted(RUNS_DIR.glob("*.jsonl"), reverse=True)[:30]:
        with open(p, encoding="utf-8") as f:
            first = f.readline()
        try:
            e = json.loads(first)
        except ValueError:
            continue
        out.append({"id": p.stem, "n": e.get("n"), "mock": e.get("mock"), "dataset": e.get("dataset_name"),
                    "models": [m["key"] for m in e.get("models", [])]})
    return out


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str, replay: bool = False, speed: float = 1.0):
    speed = max(0.1, min(speed, 500))
    live = RUNS.get(run_id)

    async def gen():
        if live and not replay:
            async for ev in live.stream():
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            return
        evs = live.events if live and live.done else _saved_events(run_id)
        prev = 0.0
        for ev in evs:
            wait = (ev["t"] - prev) / speed
            prev = ev["t"]
            if replay and wait > 0.002:
                await asyncio.sleep(wait)
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    if not live:
        _saved_events(run_id)  # 404 se não existir
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _fmt(ans: dict | None):
    if not ans:
        return "", ""
    if ans["kind"] == "noul":
        return ("sim" if ans["value"] else "nao"), ans["confidence"]
    return ans["value"], ans["confidence"]


@app.get("/api/runs/{run_id}/export.csv")
def export_csv(run_id: str):
    evs = RUNS[run_id].events if run_id in RUNS and RUNS[run_id].done else _saved_events(run_id)
    started = next((e for e in evs if e["type"] == "run_started"), None)
    if not started:
        raise HTTPException(404, "Execução sem dados.")
    leads = {e["lead"]["id"]: e["lead"] for e in evs if e["type"] == "lead_started"}
    res: dict[str, dict[str, dict]] = {}
    for e in evs:
        if e["type"] == "lead_result":
            res.setdefault(e["model"], {})[e["lead_id"]] = e
    keys = [m["key"] for m in started["models"]]
    qnames = [q["name"] for q in started["questions"]]
    cols = list(next(iter(leads.values())).keys()) if leads else []
    fat = "faturamento" in cols
    header = cols + (["faturamento_anual_regra", "faixa_faturamento_regra"] if fat else [])
    for k in keys:
        for q in qnames:
            header += [f"{k}__{q}", f"{k}__{q}_conf"]
        header += [f"{k}__latencia_ms", f"{k}__custo_usd"]
    header.append("revisar")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for lid, lead in leads.items():
        row = [lead.get(c, "") for c in cols]
        if fat:
            anual = rules.parse_faturamento(lead.get("faturamento", ""))
            row += [anual if anual is not None else "", rules.faixa_faturamento(anual)]
        low = False
        for k in keys:
            r = res.get(k, {}).get(lid)
            for q in qnames:
                v, c = _fmt(r["answers"].get(q) if r and not r.get("error") else None)
                row += [v, c]
            row += [r["latency_ms"] if r else "", r["cost_usd"] if r else ""]
            low = low or bool(r and r.get("low_confidence"))
        row.append("sim" if low else "nao")
        w.writerow(row)
    data = "\ufeff" + buf.getvalue()
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="resultado-{run_id}.csv"'})
