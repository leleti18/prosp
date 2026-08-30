"""
Enriquecimento e scoring de leads via OpenRouter (Fase 3).

Le leads sem score ainda, pede pra um agente de IA avaliar o quao bom
o lead e como prospect (score, motivo, dor provavel), e grava no Supabase.

Uso:
    python score_leads.py --account-id <uuid> --limit 20
"""

import argparse
import json
import re

import requests

from config import OPENROUTER_API_KEY, get_supabase_client

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

SYSTEM_PROMPT = """Voce e um analista de qualificacao de leads (SDR) para uma empresa que vende \
servicos de automacao, criacao de sites e gestao de redes sociais para negocios locais.

Voce recebe dados publicos de um negocio (encontrado no Google Maps) que NAO TEM SITE proprio. \
Avalie o quao bom prospect ele e para nossos servicos.

Responda SOMENTE um JSON valido, sem texto antes ou depois, no formato:
{"score": <inteiro de 1 a 10>, "motivo": "<1-2 frases explicando o score>", \
"dor_provavel": "<1 frase sobre a dor/necessidade mais provavel deste negocio>", \
"service_type": "<um destes: site, automacao, social_media, outro>"}

Criterios para o score (10 = prospect excelente, 1 = prospect fraco):
- Negocio ativo e com boa reputacao (rating alto, muitas avaliacoes) mas sem site = \
oportunidade clara de perder clientes para concorrentes que tem presenca online.
- Poucas avaliacoes ou rating baixo pode indicar negocio pequeno demais ou instavel \
para investir agora.
- Categoria do negocio importa: comercio e servicos que dependem de descoberta local \
(salao, restaurante, clinica, loja) se beneficiam mais de site/redes sociais do que \
negocios B2B ja estabelecidos.

Criterios para escolher o service_type (escolha APENAS UM, o mais relevante):
- "site": o negocio nao tem presenca online nenhuma e se beneficiaria mais de uma \
vitrine digital (portfolio, cardapio, catalogo, informacoes basicas, SEO local).
- "automacao": o negocio parece ter volume de atendimento/agendamento que poderia ser \
automatizado (ex: agendamento online, respostas automaticas, fluxo de atendimento).
- "social_media": o negocio depende de apelo visual/engajamento (ex: salao, restaurante, \
moda) e se beneficiaria mais de gestao ativa de redes sociais do que de um site estatico.
- "outro": quando nenhuma das opcoes acima se encaixa bem.
"""


def build_user_message(lead):
    payload = {
        "nome": lead.get("name"),
        "categoria": lead.get("category"),
        "cidade": lead.get("city"),
        "estado": lead.get("state"),
        "rating": lead.get("rating"),
        "total_avaliacoes": lead.get("user_ratings_total"),
        "tem_telefone": bool(lead.get("phone")),
        "endereco": lead.get("formatted_address"),
    }
    return json.dumps(payload, ensure_ascii=False)


def score_lead(lead, model=DEFAULT_MODEL):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(lead)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
    }
    response = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(extract_json(content))


def extract_json(content):
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError(f"Nenhum JSON encontrado na resposta: {content!r}")
    return match.group(0)


def fetch_unscored_leads(supabase, account_id, limit):
    result = (
        supabase.table("leads")
        .select("*")
        .eq("account_id", account_id)
        .is_("score", "null")
        .limit(limit)
        .execute()
    )
    return result.data or []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if not OPENROUTER_API_KEY:
        raise SystemExit("OPENROUTER_API_KEY nao configurada no .env")

    supabase = get_supabase_client()
    leads = fetch_unscored_leads(supabase, args.account_id, args.limit)
    print(f"Leads sem score encontrados: {len(leads)}")

    scored = 0
    failed = 0
    for lead in leads:
        try:
            result = score_lead(lead, args.model)
            supabase.table("leads").update({
                "score": result["score"],
                "motivo": result["motivo"],
                "dor_provavel": result["dor_provavel"],
                "service_type": result["service_type"],
                "status": "enriched",
            }).eq("id", lead["id"]).execute()
            print(f"OK  [{result['score']}] {lead['name']}")
            scored += 1
        except Exception as e:
            print(f"FALHA {lead['name']}: {e}")
            failed += 1

    print(f"Enriquecidos: {scored} | Falhas: {failed}")


if __name__ == "__main__":
    main()
