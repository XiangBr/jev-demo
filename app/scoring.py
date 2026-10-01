"""Métricas: acurácia vs gabarito, concordância com o LLM frontier, calibração e pipeline híbrido."""
from __future__ import annotations

import csv
import math
from pathlib import Path

from .questions import Question

GAB_TYPES = {"pedindo_preco": "noul", "decisor": "noul", "segmento": "choice", "urgencia": "score"}


def load_gabarito(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["id"]] = {
                "pedindo_preco": r["pedindo_preco"] == "1", "decisor": r["decisor"] == "1",
                "segmento": r["segmento"], "urgencia": int(r["urgencia"]),
                "amb": {k: r[f"amb_{k}"] == "1" for k in GAB_TYPES},
            }
    return out


def gab_applicable(q: Question, gab: dict) -> bool:
    if not gab or GAB_TYPES.get(q.name) != q.type:
        return False
    if q.type == "choice":
        keys = {o.key for o in q.options}
        return {g["segmento"] for g in gab.values()} <= keys
    if q.type == "score":
        return len(q.levels) == 5
    return True


def _correct(q: Question, ans: dict, truth) -> bool:
    if q.type == "score":
        return round(ans["value"]) == truth
    return ans["value"] == truth


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


def _pctl(sorted_vals, p):
    if not sorted_vals:
        return None
    return round(sorted_vals[min(len(sorted_vals) - 1, math.ceil(p * len(sorted_vals)) - 1)], 1)


def summarize(questions: list[Question], models: list[str], results: dict, gab: dict, threshold: float,
              elapsed: dict, ref: str = "llm_frontier") -> dict:
    out = {"models": {}, "threshold": threshold, "reference": ref if ref in models else None}
    for m in models:
        rs = list(results.get(m, {}).values())
        ok = [r for r in rs if not r.get("error")]
        lat = sorted(r["latency_ms"] for r in ok)
        el = elapsed.get(m)
        ms = {
            "n_ok": len(ok), "n_err": len(rs) - len(ok),
            "cost_usd": sum(r["cost_usd"] for r in ok),
            "in_tokens": sum(r["in_tokens"] for r in ok), "out_tokens": sum(r["out_tokens"] for r in ok),
            "billed_out_tokens": sum(r["billed_out_tokens"] for r in ok),
            "p50_ms": _pctl(lat, 0.5), "p95_ms": _pctl(lat, 0.95),
            "elapsed_s": el, "throughput": round(len(ok) / el, 2) if el else None,
            "per_question": {},
        }
        for q in questions:
            per = {"metric": "none"}
            if gab_applicable(q, gab):
                rows = [(r["answers"][q.name], gab[r["lead_id"]]) for r in ok if r["lead_id"] in gab]
                tot = len(rows)
                hit = [(_correct(q, a, g[q.name]), g["amb"][q.name], a["confidence"]) for a, g in rows]
                clear = [h for h in hit if not h[1]]
                hi = [h for h in hit if h[2] >= threshold]
                lo = [h for h in hit if h[2] < threshold]
                per = {"metric": "gabarito", "n": tot,
                       "acc": _pct(sum(h[0] for h in hit), tot),
                       "acc_clear": _pct(sum(h[0] for h in clear), len(clear)),
                       "acc_high": _pct(sum(h[0] for h in hi), len(hi)),
                       "acc_low": _pct(sum(h[0] for h in lo), len(lo)),
                       "share_low": _pct(len(lo), tot)}
                if q.type == "score":
                    diffs = [abs(a["value"] - g[q.name]) for a, g in rows]
                    per["mae"] = round(sum(diffs) / len(diffs), 2) if diffs else None
            elif out["reference"] and m != ref and ref in results:
                refr = results[ref]
                pairs = [(r["answers"][q.name], refr[r["lead_id"]]["answers"][q.name]) for r in ok
                         if r["lead_id"] in refr and not refr[r["lead_id"]].get("error")]
                if pairs:
                    if q.type == "score":
                        agree = sum(abs(a["value"] - b["value"]) <= 0.5 for a, b in pairs)
                    else:
                        agree = sum(a["value"] == b["value"] for a, b in pairs)
                    per = {"metric": "concordancia", "n": len(pairs), "acc": _pct(agree, len(pairs))}
            ms["per_question"][q.name] = per
        out["models"][m] = ms

    if "jev" in results and ref in results:
        out["hybrid"] = _hybrid(questions, results, gab, threshold)
    return out


def _hybrid(questions, results, gab, threshold):
    jev, fr = results["jev"], results["llm_frontier"]
    ids = [i for i in jev if i in fr and not jev[i].get("error") and not fr[i].get("error")]
    low_ids = [i for i in ids if jev[i]["low_confidence"]]
    cost_jev = sum(jev[i]["cost_usd"] for i in ids)
    cost_fr = sum(fr[i]["cost_usd"] for i in ids)
    cost_low = sum(fr[i]["cost_usd"] for i in low_ids)
    res = {"n": len(ids), "n_low": len(low_ids), "share_low": _pct(len(low_ids), len(ids)),
           "cost_hybrid_usd": cost_jev + cost_low, "cost_jev_usd": cost_jev, "cost_frontier_usd": cost_fr}
    app = [q for q in questions if gab_applicable(q, gab)]
    if app and ids:
        def acc(pick):
            tot = hit = 0
            for i in ids:
                if i not in gab:
                    continue
                r = pick(i)
                for q in app:
                    tot += 1
                    hit += _correct(q, r["answers"][q.name], gab[i][q.name])
            return _pct(hit, tot)
        res["accuracy"] = {"jev": acc(lambda i: jev[i]), "frontier": acc(lambda i: fr[i]),
                           "hybrid": acc(lambda i: fr[i] if jev[i]["low_confidence"] else jev[i])}
    return res
