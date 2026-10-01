# AGENTS.md

Contexto para agentes de código (Claude Code, Codex, Cursor etc.) trabalhando neste repositório.

## O que é

Demo local que compara o **Jev** (`typesafe/jev-1.13`, modelo de decisão da TypeSafe AI) com um LLM qualquer do OpenRouter na mesma planilha. O usuário sobe um CSV/Excel, escolhe as colunas de contexto, escreve perguntas (Sim/Não, Escolha, Nota) e vê respostas, confiança, latência e custo de cada modelo lado a lado.

Público: quem veio do reels do Instagram, muitas vezes dono de PME ou dev iniciante. Prioridade é rodar fácil e mostrar número honesto, não arquitetura sofisticada.

## Rodar

```bash
python -m venv .venv && .venv/Scripts/activate   # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000      # http://localhost:8000
```

- Sem chave: marque **Simulado** na página (respostas inventadas, custo zero).
- Teste rápido da API real com 1 lead: `python scripts/smoke_test.py` (lê `OPENROUTER_API_KEY` do `.env`).
- Não há suíte de testes. Para validar uma mudança: suba o servidor, rode no modo simulado e confira um replay: `/?replay=exemplo-50-leads-jev-vs-sonnet&speed=50`.

## Mapa

| Arquivo | Papel |
|---|---|
| `app/main.py` | FastAPI. Planilhas em memória (`DATASETS`), `/api/datasets` (upload), `/api/key/check`, `/api/estimate`, `/api/runs`, SSE de eventos, export CSV |
| `app/datasets.py` | Lê CSV (detecta `,` `;` tab, UTF-8 ou cp1252) e `.xlsx` (openpyxl). Garante coluna `id` única |
| `app/questions.py` | Modelo `Question`, conversão para o formato do Jev (`to_jev`), prompt e JSON schema do LLM, `normalize` para comparar respostas |
| `app/clients/jev.py` | `POST /api/v1/systemone` (ou `/api/alpha/decisions`) no OpenRouter |
| `app/clients/llm.py` | `chat/completions` com `json_schema` estrito; cai para `json_object` se o modelo não suportar |
| `app/clients/mock.py` | Cliente simulado determinístico |
| `app/runner.py` | Fila por modelo, concorrência e rate limit de `config.yaml`, emite eventos em `runs/<id>.jsonl` |
| `app/scoring.py` | Acurácia contra `data/gabarito.csv`, concordância com o LLM, pipeline híbrido |
| `app/rules.py` | Regra de código para faturamento (exemplo de "número fica fora do Jev") |
| `app/static/index.html` | Interface inteira: HTML, CSS e JS puro, sem build |

## Formato do Jev (o que o código assume)

Requisição: `{"model", "state": {coluna: texto}, "questions": {nome: {"type", "instructions", "criteria"}}}`.
`criteria` é `{opção: descrição}` em `choice` e lista de níveis em `score`.

Resposta em `answers[nome]`: `noul` → `{"noul": p}`; `choice` → `{"choice", "probabilities", "confidence"}`; `score` → `{"score", "confidence"}` com score começando em 0 (o app soma 1 para exibir de 1 a N). Custo em `usage.cost` ou calculado por `usage.input_tokens`.

Se a API mudar, ajuste `to_jev` e `normalize` em `app/questions.py` e rode o smoke test.

## Regras

- **Nunca grave a chave do usuário.** A chave digitada na página chega em `RunReq.api_key`, é usada só em memória e é excluída do `model_dump` que vai para `runs/`. Não logue, não persista, não devolva em resposta. `.env` está no `.gitignore`.
- **Não suba dados do usuário.** Planilhas enviadas ficam só em memória. `runs/` é ignorado no git, exceto `runs/exemplo-*.jsonl`.
- **Número tem que ser real.** O README e a página mostram custo e acurácia medidos. Não invente benchmark; se mudar o cálculo, diga o que mudou.
- **Mantenha o Jev honesto.** Números, datas e contas ficam em código (`rules.py`), não em pergunta para o Jev. Não "esconda" erro do Jev na UI.
- **Interface em português do Brasil**, texto direto, sem jargão desnecessário. Termos da API (`noul`, `choice`, `score`, `state`) aparecem só onde o usuário precisa deles.
- **Sem build e sem framework novo no front.** Tudo continua em `app/static/index.html`.
- Dependências novas só se forem indispensáveis; atualize `requirements.txt`.

## Gabarito

`data/gabarito.csv` tem a resposta certa das 4 perguntas do exemplo para cada lead, gerada junto com `leads_5000.csv` por `scripts/gerar_leads.py --seed 42`. A acurácia só é calculada quando a planilha é a de exemplo e a pergunta é idêntica à do preset (`scoring.gab_applicable`). Para outras planilhas, a métrica é concordância com o LLM.
