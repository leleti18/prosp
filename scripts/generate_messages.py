"""
Geracao de mensagem de abordagem personalizada (Fase 4).

Cobre dois tipos de lead:
- 'empresa': negocio local ja enriquecido (score/motivo/dor_provavel/service_type),
  mensagem focada no servico (site/automacao/social_media).
- 'pessoa': pessoa fisica (ex: lista de maternidade), sem enriquecimento por IA,
  mensagem focada na oferta configurada na conta (accounts.person_offer_description).

A funcao generate_message() tambem e usada pelo painel (app.py) pra gerar/
regerar a mensagem sob demanda.

Uso:
    python generate_messages.py --account-id <uuid> --limit 20
"""

import argparse
import json
import re

import requests

from config import OPENROUTER_API_KEY, get_supabase_client

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

DEFAULT_PERSON_OFFER = (
    "material de educacao infantil e apoio para familias, incluindo quem considera ou "
    "pratica homeschool"
)

SERVICE_FRAMING = {
    "site": (
        "O foco da mensagem e a AUSENCIA DE SITE do negocio. Fale sobre como isso faz ele "
        "perder clientes que buscam no Google, e ofereca ajuda pra criar uma presenca digital "
        "simples e profissional (site, catalogo ou portfolio online)."
    ),
    "automacao": (
        "O foco da mensagem e AUTOMACAO DE ATENDIMENTO/AGENDAMENTO. Fale sobre como automatizar "
        "respostas, agendamentos ou fluxos de atendimento pode economizar tempo do negocio e "
        "evitar perder cliente por demora em responder."
    ),
    "social_media": (
        "O foco da mensagem e GESTAO DE REDES SOCIAIS. Fale sobre como uma presenca mais ativa e "
        "profissional no Instagram/redes pode atrair mais clientes, ja que esse tipo de negocio "
        "depende de apelo visual e engajamento."
    ),
    "outro": (
        "Nao ha um servico especifico definido. Adapte a mensagem ao que fizer mais sentido pro "
        "negocio entre presenca digital, automacao ou redes sociais, com base na dor provavel."
    ),
}

BUSINESS_SYSTEM_PROMPT = """Voce e um redator de mensagens de prospeccao (SDR) para uma empresa que \
vende servicos de automacao, criacao de sites e gestao de redes sociais para negocios locais.

Voce recebe dados de um lead ja qualificado (nome do negocio, categoria, cidade, score, \
motivo do score, dor provavel) e deve escrever uma mensagem curta de primeiro contato via \
WhatsApp.

{service_instructions}

Regras da mensagem:
- Maximo 4-5 frases curtas, tom humano e direto, nunca robotizado ou generico.
- Mencione o nome do negocio e algo especifico e verdadeiro sobre ele (rating, reputacao).
- Conecte com a dor provavel de forma sutil, sem soar como discurso de vendas agressivo.
- Fale do servico especifico indicado acima, nao ofereca os tres servicos ao mesmo tempo.
- Termine com uma pergunta leve ou call-to-action de baixo compromisso (ex: "faz sentido \
uma conversa rapida?"), nunca pedindo reuniao ou compromisso direto.
- Nao invente promocoes, precos ou garantias.
- Nao use saudacao de horario (ex: "bom dia"), a mensagem pode ser enviada a qualquer hora.

{tone_instructions}

{alt_contact_instructions}

Responda SOMENTE um JSON valido, sem texto antes ou depois, no formato:
{{"mensagem": "<texto da mensagem>"}}
"""

PERSON_SYSTEM_PROMPT = """Voce e um redator de mensagens de prospeccao para uma empresa que oferece \
{person_offer}.

Voce recebe dados de um lead que e uma PESSOA FISICA (nao um negocio), vindo de uma lista de \
contatos ligada a maternidade. O lead pode ou nao ter nome disponivel.

Regras da mensagem:
- Maximo 4-5 frases curtas, tom humano, acolhedor e respeitoso — e um assunto sensivel \
(maternidade/criacao de filhos), nunca soe invasivo, generico ou como spam.
- Se houver nome, use-o naturalmente. Se nao houver nome, use uma saudacao neutra sem nome \
(nunca invente um nome).
- Conecte a oferta ao momento de vida da pessoa (maternidade, educacao infantil, duvida sobre \
homeschool) de forma sutil e gentil.
- Termine com uma pergunta leve ou call-to-action de baixo compromisso.
- Nao invente promocoes, precos ou garantias.
- Nao use saudacao de horario (ex: "bom dia"), a mensagem pode ser enviada a qualquer hora.

{tone_instructions}

{alt_contact_instructions}

Responda SOMENTE um JSON valido, sem texto antes ou depois, no formato:
{{"mensagem": "<texto da mensagem>"}}
"""


def build_tone_instructions(tone_of_voice, default):
    return f"Tom de voz da conta: {tone_of_voice}" if tone_of_voice else default


def build_alt_contact_instructions(alt_contact):
    if not alt_contact:
        return ""
    return (
        f'Ao final da mensagem, deixe uma opcao aberta e leve: a pessoa pode responder essa '
        f'mensagem mesmo por aqui, OU se preferir ja falar direto sobre o assunto, pode chamar '
        f'no numero {alt_contact}. Deixe claro que responder aqui tambem funciona — nao force '
        f'o outro numero como unico caminho, so ofereca como alternativa.'
    )


def build_business_system_prompt(tone_of_voice, service_type, alt_contact):
    service_instructions = SERVICE_FRAMING.get(service_type, SERVICE_FRAMING["outro"])
    return BUSINESS_SYSTEM_PROMPT.format(
        service_instructions=service_instructions,
        tone_instructions=build_tone_instructions(
            tone_of_voice, "Tom de voz: profissional, simpatico e direto ao ponto."
        ),
        alt_contact_instructions=build_alt_contact_instructions(alt_contact),
    )


def build_business_user_message(lead, service_type):
    payload = {
        "nome": lead.get("name"),
        "categoria": lead.get("category"),
        "cidade": lead.get("city"),
        "score": lead.get("score"),
        "motivo": lead.get("motivo"),
        "dor_provavel": lead.get("dor_provavel"),
        "servico_a_oferecer": service_type,
    }
    return json.dumps(payload, ensure_ascii=False)


def build_person_system_prompt(tone_of_voice, alt_contact, person_offer_description):
    return PERSON_SYSTEM_PROMPT.format(
        person_offer=person_offer_description or DEFAULT_PERSON_OFFER,
        tone_instructions=build_tone_instructions(
            tone_of_voice, "Tom de voz: acolhedor, humano e respeitoso."
        ),
        alt_contact_instructions=build_alt_contact_instructions(alt_contact),
    )


def build_person_user_message(lead):
    payload = {
        "nome": lead.get("name"),
        "cidade": lead.get("city"),
    }
    return json.dumps(payload, ensure_ascii=False)


def extract_json(content):
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError(f"Nenhum JSON encontrado na resposta: {content!r}")
    return match.group(0)


def generate_message(
    lead, tone_of_voice, service_type, alt_contact, person_offer_description=None,
    model=DEFAULT_MODEL,
):
    if lead.get("lead_type") == "pessoa":
        system_prompt = build_person_system_prompt(tone_of_voice, alt_contact, person_offer_description)
        user_message = build_person_user_message(lead)
    else:
        system_prompt = build_business_system_prompt(tone_of_voice, service_type, alt_contact)
        user_message = build_business_user_message(lead, service_type)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.6,
    }
    response = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(extract_json(content))["mensagem"]


def fetch_account(supabase, account_id):
    result = supabase.table("accounts").select("*").eq("id", account_id).single().execute()
    return result.data


def fetch_leads_without_draft(supabase, account_id, limit):
    result = (
        supabase.table("leads")
        .select("*")
        .eq("account_id", account_id)
        .is_("message_draft", "null")
        .or_("status.eq.enriched,and(status.eq.new,lead_type.eq.pessoa)")
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
    account = fetch_account(supabase, args.account_id)
    tone_of_voice = account.get("tone_of_voice") if account else None
    alt_contact = account.get("alt_contact") if account else None
    person_offer_description = account.get("person_offer_description") if account else None

    leads = fetch_leads_without_draft(supabase, args.account_id, args.limit)
    print(f"Leads sem draft: {len(leads)}")

    generated = 0
    failed = 0
    for lead in leads:
        label = lead.get("name") or f"(sem nome, {lead.get('phone')})"
        try:
            service_type = lead.get("service_type") or "outro"
            mensagem = generate_message(
                lead, tone_of_voice, service_type, alt_contact, person_offer_description,
                args.model,
            )
            supabase.table("leads").update({
                "message_draft": mensagem,
                "message_status": "draft",
                "status": "draft_ready",
            }).eq("id", lead["id"]).execute()
            print(f"OK  {label}")
            generated += 1
        except Exception as e:
            print(f"FALHA {label}: {e}")
            failed += 1

    print(f"Gerados: {generated} | Falhas: {failed}")


if __name__ == "__main__":
    main()
