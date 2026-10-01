"""Regras de código para o que é número: faturamento e porte ficam fora do Jev."""
from __future__ import annotations

import re


def parse_faturamento(texto: str) -> float | None:
    """Converte textos como 'R$ 480.000', '480k', '1,2 mi', 'R$ 40.000/mês' em valor anual (R$). None se não der."""
    if not texto:
        return None
    t = texto.lower().replace("~", "").replace("r$", "").strip()
    if not re.search(r"\d", t):
        return None
    nums = re.findall(r"\d+(?:[.,]\d+)*", t)
    if "entre" in t and len(nums) >= 2:
        vals = [_num(n) for n in nums[:2]]
        v = sum(vals) / 2
    else:
        v = _num(nums[0])
    if re.search(r"\bmi\b|milh", t):
        v *= 1_000_000
    elif re.search(r"\d\s*k\b", t):
        v *= 1_000
    elif re.search(r"\bmil\b", t):
        v *= 1_000
    if "entre" in t and re.search(r"\bmil\b", t) and v < 1000:
        v *= 1_000
    if re.search(r"/\s*m[eê]s|por m[eê]s|mensal", t):
        v *= 12
    return v


def _num(s: str) -> float:
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) != 3 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[-1]) == 3):
        s = s.replace(".", "")
    return float(s)


def faixa_faturamento(anual: float | None) -> str:
    if anual is None:
        return "sem_informacao"
    if anual < 81_000:
        return "ate_81k"
    if anual < 360_000:
        return "81k_a_360k"
    if anual < 4_800_000:
        return "360k_a_4_8mi"
    return "acima_4_8mi"
