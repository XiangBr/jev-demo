#!/usr/bin/env python
"""Gera um CSV de CRM fictício e bagunçado (leads em PT-BR) + gabarito.

Determinístico: mesma --seed gera exatamente os mesmos arquivos.
Rótulos são conhecidos por construção (a mensagem é montada a partir deles).

Uso:
    python scripts/gerar_leads.py --n 5000 --seed 42
    python scripts/gerar_leads.py --n 5000 --pool data/seed_pool.json   # mensagens do LLM (opcional)
"""
import argparse
import csv
import json
import math
import random
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NOMES = ["Ana", "Carlos", "Mariana", "João", "Fernanda", "Ricardo", "Juliana", "Paulo", "Camila", "Rafael",
         "Patrícia", "Marcos", "Luciana", "Bruno", "Aline", "Felipe", "Tatiane", "Gustavo", "Renata", "Leandro",
         "Vanessa", "Diego", "Priscila", "Thiago", "Bianca", "André", "Larissa", "Eduardo", "Simone", "Rodrigo",
         "Cláudia", "Fábio", "Daniela", "Márcio", "Letícia", "Sérgio", "Beatriz", "Vinícius", "Adriana", "Henrique"]
SOBRENOMES = ["Silva", "Souza", "Oliveira", "Santos", "Pereira", "Lima", "Ferreira", "Costa", "Rodrigues", "Almeida",
              "Nascimento", "Carvalho", "Gomes", "Martins", "Araújo", "Ribeiro", "Barbosa", "Cardoso", "Moreira", "Rocha",
              "Dias", "Teixeira", "Fernandes", "Correia", "Mendes", "Nunes", "Vieira", "Pinto", "Monteiro", "Kowalski",
              "Schmidt", "Yamamoto", "Hilgemberg", "Bonfim", "Zanella", "Petrelli", "Tanaka", "Weber", "Campos", "Moraes"]

SEGMENTOS = {
    "varejo": {
        "peso": 28,
        "negocios": ["uma loja de roupas femininas", "um mercadinho de bairro", "um e-commerce de cosméticos",
                     "uma loja de material de construção", "uma pet shop", "uma loja de calçados",
                     "uma papelaria", "uma distribuidora de bebidas", "uma loja de eletrônicos"],
        "empresas": ["{s} Modas", "Mercadinho {s}", "{s} Cosméticos", "Casa {s} Construção", "Pet {s}",
                     "Calçados {s}", "Papelaria {s}", "Bebidas {s}"],
    },
    "servicos": {
        "peso": 28,
        "negocios": ["um escritório de contabilidade", "uma agência de marketing", "uma escola de idiomas",
                     "uma consultoria de RH", "uma empresa de limpeza predial", "um escritório de advocacia",
                     "uma empresa de segurança patrimonial", "uma transportadora"],
        "empresas": ["{s} Contabilidade", "Agência {s}", "{s} Idiomas", "{s} Consultoria", "{s} Serviços Gerais",
                     "{s} & Associados Advocacia", "{s} Transportes"],
    },
    "industria": {
        "peso": 14,
        "negocios": ["uma fábrica de móveis", "uma metalúrgica", "uma indústria de embalagens plásticas",
                     "uma confecção industrial", "uma fábrica de alimentos congelados", "uma usinagem de peças"],
        "empresas": ["Móveis {s}", "Metalúrgica {s}", "{s} Embalagens", "Confecções {s}", "{s} Alimentos Indústria",
                     "Usinagem {s}"],
    },
    "saude": {
        "peso": 18,
        "negocios": ["uma clínica odontológica", "um laboratório de análises clínicas", "uma clínica de fisioterapia",
                     "um consultório de dermatologia", "uma farmácia de manipulação", "uma clínica de estética"],
        "empresas": ["Clínica {s}", "Odonto {s}", "Laboratório {s}", "Fisio {s}", "Farmácia {s} Manipulação",
                     "Estética {s}"],
    },
    "outro": {
        "peso": 12,
        "negocios": ["uma igreja", "uma ONG", "uma fazenda de soja", "uma associação de moradores",
                     "uma cooperativa agrícola", "um sítio de hortaliças"],
        "empresas": ["Igreja {s}", "ONG {s}", "Fazenda {s}", "Associação {s}", "Cooperativa {s}", "Sítio {s}"],
    },
}
GENERICOS = ["uma empresa", "um negócio aqui na cidade", "uma empresa pequena", "um negócio de família"]
EMPRESAS_GENERICAS = ["{s} LTDA", "{s} ME", "{s} Comércio", "Grupo {s}", "{s} e Filhos"]

PRECO_SIM = [
    "Quanto custa o plano de vocês?",
    "Preciso de um orçamento para automatizar o atendimento.",
    "Podem me mandar uma proposta com os valores?",
    "Qual o valor mensal? Tem desconto pra pagamento anual?",
    "Me passa os preços por favor.",
    "Queria cotar a implantação, qual seria o investimento?",
    "Gostaria de saber o valor do serviço de vocês.",
    "Fazem orçamento sem compromisso? Preciso do preço pra 5 usuários.",
]
PRECO_NAO = [
    "Queria entender como funciona a solução de vocês.",
    "Vocês integram com o sistema que a gente já usa?",
    "Estou pesquisando o que existe no mercado, só conhecendo.",
    "Tem algum case de empresa parecida com a nossa?",
    "Gostaria de agendar uma conversa para conhecer melhor.",
    "Vocês têm suporte em português? Como é o atendimento?",
    "Dá pra testar antes de contratar?",
    "Recebi um material de vocês e fiquei curioso.",
    "Já tenho orçamento aprovado, só preciso saber se integra com o nosso ERP.",
    "Meu sócio já viu o preço com outra empresa, agora quero comparar a parte técnica.",
    "Como vocês tratam a segurança dos dados dos clientes?",
]
URGENCIA = {
    1: ["Sem pressa, estou só pesquisando.", "Quando der, sem compromisso.",
        "É para o ano que vem, ainda estou levantando opções."],
    2: ["Estou avaliando para o próximo trimestre.", "Devo decidir nos próximos meses.",
        "Ainda estamos começando a olhar isso."],
    3: ["Gostaria de resolver nas próximas semanas.", "Queremos começar ainda este mês.",
        "Estamos comparando opções para fechar em breve."],
    4: ["Preciso fechar até sexta.", "Temos que começar na semana que vem.",
        "É prioridade aqui, preciso de retorno em 24h."],
    5: ["URGENTE, preciso pra hoje, cliente esperando!", "Preciso disso ainda hoje, por favor respondam rápido.",
        "Estamos parados por causa disso, preciso resolver agora."],
}
URGENCIA_PESO = [15, 25, 30, 20, 10]

CARGOS_SIM = ["Dono", "Proprietário", "Sócio", "Sócio-diretor", "Diretor comercial", "CEO", "Fundador",
              "Diretora", "Dono(a)", "Sócio administrador", "Gerente geral", "PROPRIETARIO", "socio adm"]
CARGOS_NAO = ["Estagiário", "Assistente administrativo", "Analista de marketing jr", "Auxiliar", "Recepcionista",
              "Secretária", "Analista de compras", "Vendedor", "Atendente", "estag", "Aux. administrativo"]
CARGOS_AMB = {"Gerente": True, "Coordenador": False, "Responsável": True, "Financeiro": False,
              "Supervisor": False, "Compras": False, "": False}
DECISOR_MSG_SIM = ["Sou o dono e decido aqui.", "Eu que aprovo as compras aqui na empresa.",
                   "Sou sócio, então a decisão é minha."]
DECISOR_MSG_NAO = ["Estou pesquisando pro meu chefe.", "Vou levar pra diretoria avaliar.",
                   "Depois preciso da aprovação do financeiro."]

GREETS = ["Oi", "Olá", "Bom dia", "Boa tarde", "Boa noite", "Prezados", "Olá, tudo bem?", "Oi, tudo bem?", ""]
SIGNS = ["Obrigado!", "Att,", "Abraço", "Aguardo retorno.", "Grato.", "Desde já agradeço.", ""]
ORIGENS = ["Instagram", "insta", "Google Ads", "google", "Indicação", "indicacao", "Site", "site zait", "WhatsApp",
           "Whats", "Evento", "LinkedIn", "linkedin", ""]
EMOJIS = ["🙏", "👍", "😊", "🚀", "🙂"]
ABREV = [(r"\bvocês\b", "vcs"), (r"\bvocê\b", "vc"), (r"\bpara\b", "pra"), (r"\bpor favor\b", "pfv"),
         (r"\bporque\b", "pq"), (r"\btambém\b", "tb"), (r"\bObrigado\b", "obg"), (r"\borçamento\b", "orc"),
         (r"\bnão\b", "n"), (r"\bcomigo\b", "cmg")]


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def wchoice(rng, mapping):
    keys = list(mapping.keys())
    weights = [mapping[k]["peso"] if isinstance(mapping[k], dict) else mapping[k] for k in keys]
    return rng.choices(keys, weights=weights, k=1)[0]


def typo(word, rng):
    if len(word) < 5:
        return word
    i = rng.randrange(1, len(word) - 2)
    kind = rng.random()
    if kind < 0.4:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if kind < 0.7:
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]


def add_noise(text, rng):
    if rng.random() < 0.35:
        for pat, rep in ABREV:
            if rng.random() < 0.6:
                text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    if rng.random() < 0.15:
        text = strip_accents(text)
    r = rng.random()
    if r < 0.04:
        text = text.upper()
    elif r < 0.10:
        text = text.lower()
    words = text.split(" ")
    words = [typo(w, rng) if rng.random() < 0.04 else w for w in words]
    text = " ".join(words)
    if rng.random() < 0.10:
        text = re.sub(r"[.!?]+$", "", text)
    if rng.random() < 0.08:
        text += " " + rng.choice(EMOJIS)
    if rng.random() < 0.06:
        text = text.replace(". ", "  ")
    if rng.random() < 0.01 and len(text) > 40:
        text = text[: rng.randrange(25, len(text) - 5)]
    return text.strip()


def pick_cargo(rng):
    r = rng.random()
    if r < 0.42:
        return rng.choice(CARGOS_SIM), True, False
    if r < 0.80:
        return rng.choice(CARGOS_NAO), False, False
    c = rng.choice(list(CARGOS_AMB.keys()))
    return c, CARGOS_AMB[c], True


def fmt_faturamento(anual, rng):
    r = rng.random()
    if r < 0.08:
        return rng.choice(["nao informado", "", "?", "prefere nao dizer"])
    k = round(anual / 1000)
    opts = [
        f"R$ {anual:,.0f}".replace(",", "."),
        f"{k}k",
        f"{k} mil",
        f"~R${k}k/ano",
        f"R$ {anual / 12:,.0f}/mês".replace(",", "."),
        f"{anual / 1e6:.1f} mi".replace(".", ",") if anual >= 1e6 else f"{k} mil/ano",
        f"entre {round(k * 0.7)} e {round(k * 1.3)} mil",
    ]
    return rng.choice(opts)


def fmt_porte(anual, rng):
    if rng.random() < 0.2:
        return rng.choice(["", "nao sei", "MEI", "ME", "EPP", "Média"])
    if anual < 81_000:
        base = ["MEI", "mei", "Microempreendedor"]
    elif anual < 360_000:
        base = ["ME", "Micro empresa", "micro"]
    elif anual < 4_800_000:
        base = ["EPP", "Pequena", "pequena empresa"]
    else:
        base = ["Média", "Media empresa", "Grande"]
    return rng.choice(base)


def fmt_data(rng):
    dia = rng.randrange(1, 29)
    mes = rng.choice([8, 9])
    ano = 2026
    meses = ["", "", "", "", "", "", "", "", "ago", "set"]
    r = rng.random()
    if r < 0.35:
        return f"{ano}-{mes:02d}-{dia:02d}"
    if r < 0.65:
        return f"{dia:02d}/{mes:02d}/{ano}"
    if r < 0.85:
        return f"{dia} {meses[mes]} {ano}"
    return f"{dia}/{mes}/{str(ano)[2:]}"


def fmt_tel(rng):
    ddd = rng.choice(["41", "11", "21", "31", "48", "51", "61", "85"])
    n = f"9{rng.randrange(1000, 9999)}{rng.randrange(1000, 9999)}"
    r = rng.random()
    if r < 0.4:
        return f"({ddd}) {n[:5]}-{n[5:]}"
    if r < 0.7:
        return f"{ddd} {n}"
    if r < 0.9:
        return f"+55 {ddd} {n}"
    return f"{ddd}{n}"


def build_message(rng, seg, preco, urg, cargo, cargo_sim, cargo_amb, pool):
    """Devolve (mensagem, decisor, amb_segmento, amb_urgencia, amb_decisor, amb_preco)."""
    amb_seg = rng.random() < 0.08
    amb_urg = rng.random() < 0.10
    amb_preco = False

    decisor = cargo_sim
    amb_dec = cargo_amb
    dec_phrase = ""
    if cargo_amb or rng.random() < 0.12:
        if rng.random() < 0.55:
            decisor = rng.random() < 0.5
            dec_phrase = rng.choice(DECISOR_MSG_SIM if decisor else DECISOR_MSG_NAO)
            amb_dec = False

    pool_cell = pool.get((seg, preco, urg)) if pool else None
    if pool_cell and not amb_seg and not amb_urg:
        core = rng.choice(pool_cell)
    else:
        contexto = f"Tenho {rng.choice(GENERICOS)}." if amb_seg else f"Tenho {rng.choice(SEGMENTOS[seg]['negocios'])}."
        pedido = rng.choice(PRECO_SIM if preco else PRECO_NAO)
        urg_txt = "" if amb_urg else rng.choice(URGENCIA[urg])
        core = " ".join(x for x in [contexto, pedido, urg_txt] if x)
        if rng.random() < 0.25:
            partes = [contexto, pedido, urg_txt]
            rng.shuffle(partes)
            core = " ".join(x for x in partes if x)

    greet = rng.choice(GREETS)
    sign = rng.choice(SIGNS)
    msg = " ".join(x for x in [greet + ("," if greet and not greet.endswith(("?", ",")) else ""), core, dec_phrase, sign] if x)
    if rng.random() < 0.02:
        msg = rng.choice(["oi", "Olá", "quero saber", ".", "Gostaria de informações"])
        amb_seg = amb_urg = amb_preco = True
    return add_noise(msg, rng), decisor, amb_seg, amb_urg, amb_dec, amb_preco


def load_pool(path):
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pool = {}
    for item in data:
        pool.setdefault((item["segmento"], bool(item["pedindo_preco"]), int(item["urgencia"])), []).append(item["texto"])
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pool", default=None, help="JSON de mensagens geradas por LLM (build_seed_pool.py)")
    ap.add_argument("--out", default=str(ROOT / "data" / "leads_5000.csv"))
    ap.add_argument("--gabarito", default=str(ROOT / "data" / "gabarito.csv"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    pool = load_pool(args.pool)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    leads, gab = [], []
    for i in range(1, args.n + 1):
        seg = wchoice(rng, SEGMENTOS)
        preco = rng.random() < 0.45
        urg = rng.choices([1, 2, 3, 4, 5], weights=URGENCIA_PESO, k=1)[0]
        cargo, cargo_sim, cargo_amb = pick_cargo(rng)
        msg, decisor, a_seg, a_urg, a_dec, a_preco = build_message(rng, seg, preco, urg, cargo, cargo_sim, cargo_amb, pool)

        nome = f"{rng.choice(NOMES)} {rng.choice(SOBRENOMES)}"
        sob = rng.choice(SOBRENOMES)
        if a_seg:
            empresa = rng.choice(EMPRESAS_GENERICAS).format(s=sob)
        else:
            empresa = rng.choice(SEGMENTOS[seg]["empresas"]).format(s=sob)
        r = rng.random()
        nome_out = nome.upper() if r < 0.04 else nome.lower() if r < 0.10 else nome
        anual = math.exp(rng.uniform(math.log(15_000), math.log(8_000_000)))
        mail_user = strip_accents(nome).lower().replace(" ", rng.choice([".", "_", ""]))
        email = f"{mail_user}@{rng.choice(['gmail.com', 'hotmail.com', 'outlook.com', strip_accents(sob).lower() + '.com.br'])}"
        if rng.random() < 0.05:
            email = ""

        lid = f"L{i:04d}"
        leads.append({
            "id": lid, "nome": nome_out, "empresa": empresa, "cargo": add_noise(cargo, rng) if cargo and rng.random() < 0.1 else cargo,
            "mensagem": msg, "faturamento": fmt_faturamento(anual, rng), "porte": fmt_porte(anual, rng),
            "origem": rng.choice(ORIGENS), "data_entrada": fmt_data(rng), "email": email, "telefone": fmt_tel(rng),
        })
        gab.append({
            "id": lid, "pedindo_preco": int(preco), "segmento": "outro" if a_seg else seg,
            "urgencia": 2 if a_urg else urg, "decisor": int(decisor),
            "amb_pedindo_preco": int(a_preco), "amb_segmento": int(a_seg), "amb_urgencia": int(a_urg),
            "amb_decisor": int(a_dec),
        })

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(leads[0].keys()))
        w.writeheader()
        w.writerows(leads)
    with open(args.gabarito, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(gab[0].keys()))
        w.writeheader()
        w.writerows(gab)
    print(f"{len(leads)} leads -> {args.out}\ngabarito -> {args.gabarito}")


if __name__ == "__main__":
    main()
