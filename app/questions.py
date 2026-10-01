"""Perguntas livres definidas na UI e conversão para Jev e para LLM."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,39}$")


class Option(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    desc: str = ""


class Question(BaseModel):
    name: str = ""  # se vier vazio, validate_questions gera a partir do texto da pergunta
    label: str = ""  # texto curto exibido na UI; se vazio, usa a instrução
    type: Literal["noul", "choice", "score"]
    instructions: str = Field(min_length=5)
    options: list[Option] = []  # choice
    levels: list[str] = []      # score, do menor para o maior

    @model_validator(mode="after")
    def _check(self):
        if not NAME_RE.match(self.name):
            raise ValueError(f"Nome '{self.name}' inválido: use minúsculas, números e _ (máx. 40).")
        if self.type == "choice":
            keys = [o.key.strip() for o in self.options]
            if len(keys) < 2:
                raise ValueError(f"'{self.name}': Escolha precisa de pelo menos 2 opções.")
            if len(keys) > 255:
                raise ValueError(f"'{self.name}': máximo de 255 opções.")
            if len(set(keys)) != len(keys):
                raise ValueError(f"'{self.name}': opções repetidas.")
        if self.type == "score":
            if not 2 <= len(self.levels) <= 20:
                raise ValueError(f"'{self.name}': Nota precisa de 2 a 20 níveis.")
            if any(not l.strip() for l in self.levels):
                raise ValueError(f"'{self.name}': todo nível precisa de uma descrição.")
        return self


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", t)[:6]
    s = "_".join(words)[:34].strip("_") or "pergunta"
    return "q_" + s if s[0].isdigit() else s


def validate_questions(raw: list[dict]) -> list[Question]:
    if not raw:
        raise ValueError("Adicione ao menos uma pergunta.")
    raw = [dict(q) for q in raw]
    used = {q["name"] for q in raw if q.get("name")}
    if len(used) != sum(1 for q in raw if q.get("name")):
        raise ValueError("Há perguntas com o mesmo nome.")
    for q in raw:
        if q.get("name"):
            continue
        base = _slug(q.get("label") or q.get("instructions") or "")
        name, i = base, 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        q["name"] = name
        used.add(name)
    return [Question(**q) for q in raw]


PRESET_REELS = [
    {"name": "pedindo_preco", "type": "noul",
     "instructions": "A mensagem pede orçamento, preço ou proposta com valores agora?"},
    {"name": "segmento", "type": "choice",
     "instructions": "Qual é o segmento do negócio de quem escreveu, segundo a mensagem?",
     "options": [
         {"key": "varejo", "desc": "Loja, e-commerce, mercado ou distribuidora que vende produto ao consumidor"},
         {"key": "servicos", "desc": "Escritório, agência, escola, consultoria, transporte ou outro prestador de serviço"},
         {"key": "industria", "desc": "Fábrica, metalúrgica, confecção ou outra que produz bens"},
         {"key": "saude", "desc": "Clínica, consultório, laboratório ou farmácia"},
         {"key": "outro", "desc": "Igreja, ONG, agro, associação, ou a mensagem não diz o segmento"}]},
    {"name": "urgencia", "type": "score",
     "instructions": "Qual a urgência de quem escreveu, pelo prazo que a mensagem cita?",
     "levels": [
         "Sem pressa ou só pesquisando; prazo em muitos meses ou sem prazo",
         "Avaliando para os próximos meses, sem data definida",
         "Quer resolver nas próximas semanas ou neste mês",
         "Prazo curto: até a semana que vem ou retorno em 24h",
         "Urgente: precisa hoje ou já está parado esperando"]},
    {"name": "decisor", "type": "noul",
     "instructions": "O cargo é de dono, sócio, fundador, CEO, diretor ou gerente?"},
]


def to_jev(q: Question) -> dict[str, Any]:
    body: dict[str, Any] = {"type": q.type, "instructions": q.instructions}
    if q.type == "choice":
        body["criteria"] = {o.key: (o.desc or o.key) for o in q.options}
    elif q.type == "score":
        body["criteria"] = list(q.levels)
    return body


def jev_questions(qs: list[Question]) -> dict[str, dict]:
    return {q.name: to_jev(q) for q in qs}


def llm_schema(qs: list[Question]) -> dict:
    props: dict[str, Any] = {}
    for q in qs:
        if q.type == "noul":
            props[q.name] = {"type": "object", "additionalProperties": False, "required": ["probability"],
                             "properties": {"probability": {"type": "number"}}}
        elif q.type == "choice":
            props[q.name] = {"type": "object", "additionalProperties": False, "required": ["choice", "confidence"],
                             "properties": {"choice": {"type": "string", "enum": [o.key for o in q.options]},
                                            "confidence": {"type": "number"}}}
        else:
            props[q.name] = {"type": "object", "additionalProperties": False, "required": ["score", "confidence"],
                             "properties": {"score": {"type": "number"}, "confidence": {"type": "number"}}}
    return {"type": "object", "additionalProperties": False, "required": [q.name for q in qs], "properties": props}


def llm_prompt(qs: list[Question], state: dict[str, str]) -> tuple[str, str]:
    system = ("Você classifica registros (linhas de uma planilha). Responda SOMENTE com um objeto JSON válido, sem texto extra, "
              "com uma chave por pergunta, no formato indicado.")
    lines = []
    for q in qs:
        if q.type == "noul":
            lines.append(f'- "{q.name}": {q.instructions}\n  Formato: {{"probability": número de 0 a 1 = chance de a resposta ser SIM}}')
        elif q.type == "choice":
            opts = "; ".join(f'"{o.key}" = {o.desc or o.key}' for o in q.options)
            lines.append(f'- "{q.name}": {q.instructions}\n  Opções: {opts}\n  Formato: {{"choice": uma das opções, "confidence": número de 0 a 1}}')
        else:
            lv = "; ".join(f"{i + 1} = {l}" for i, l in enumerate(q.levels))
            lines.append(f'- "{q.name}": {q.instructions}\n  Níveis: {lv}\n  Formato: {{"score": número de 1 a {len(q.levels)} (pode ser fracionário), "confidence": número de 0 a 1}}')
    reg = "\n".join(f"{k}: {v}" for k, v in state.items())
    user = f"Registro:\n{reg}\n\nPerguntas:\n" + "\n".join(lines)
    return system, user


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def normalize(q: Question, raw: dict, source: str) -> dict:
    """Resposta única, comparável entre modelos. source: 'jev' ou 'llm'."""
    if q.type == "noul":
        p = _clip01(raw["noul"] if source == "jev" else raw["probability"])
        return {"kind": "noul", "value": p >= 0.5, "p": round(p, 4), "confidence": round(max(p, 1 - p), 4)}
    if q.type == "choice":
        choice = raw["choice"]
        keys = [o.key for o in q.options]
        if choice not in keys:
            raise ValueError(f"opção inválida '{choice}'")
        ans = {"kind": "choice", "value": choice, "confidence": round(_clip01(raw.get("confidence", 0)), 4)}
        if source == "jev" and isinstance(raw.get("probabilities"), dict):
            ans["probs"] = raw["probabilities"]
        return ans
    n = len(q.levels)
    s = float(raw["score"]) + (1 if source == "jev" else 0)
    s = max(1.0, min(float(n), s))
    return {"kind": "score", "value": round(s, 2), "confidence": round(_clip01(raw.get("confidence", 0)), 4)}
