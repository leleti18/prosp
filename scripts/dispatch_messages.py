"""
Disparo real de mensagens aprovadas (Fase 6).

Le leads com status 'approved', envia a message_draft via o adaptador de canal
configurado na conta (Meta ou Evolution), e grava o resultado no Supabase
(status vira 'sent', registra em conversation_events).

Por padrao roda em modo --dry-run (so mostra o que seria enviado). Use
--confirm para enviar de verdade.

Uso:
    python dispatch_messages.py --account-id <uuid> --limit 10           # dry-run
    python dispatch_messages.py --account-id <uuid> --limit 10 --confirm  # envia de verdade
"""

import argparse
import re
from datetime import datetime, timezone

from channel_adapter import send
from config import get_supabase_client


def normalize_phone_br(raw_phone):
    digits = re.sub(r"\D", "", raw_phone or "")
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    if len(digits) in (10, 11):
        return "55" + digits
    return digits


def fetch_channel(supabase, account_id, channel_id=None):
    query = supabase.table("channels").select("*").eq("account_id", account_id).eq("is_active", True)
    if channel_id:
        query = query.eq("id", channel_id)
    result = query.limit(1).execute()
    if not result.data:
        raise SystemExit("Nenhum canal ativo encontrado para essa conta. Cadastre um na aba Canais.")
    return result.data[0]


def fetch_approved_leads(supabase, account_id, limit):
    result = (
        supabase.table("leads")
        .select("*")
        .eq("account_id", account_id)
        .eq("status", "approved")
        .limit(limit)
        .execute()
    )
    return result.data or []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--channel-id", help="ID do canal a usar. Se omitido, usa o primeiro canal ativo.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--confirm", action="store_true", help="Envia de verdade. Sem essa flag, so simula.")
    args = parser.parse_args()

    supabase = get_supabase_client()
    channel = fetch_channel(supabase, args.account_id, args.channel_id) if args.confirm else None
    leads = fetch_approved_leads(supabase, args.account_id, args.limit)

    print(f"Leads aprovados para envio: {len(leads)}")
    if not args.confirm:
        print("--- MODO DRY-RUN (nenhuma mensagem sera enviada de verdade) ---")

    sent = 0
    failed = 0
    for lead in leads:
        to = normalize_phone_br(lead.get("phone"))
        message = lead.get("message_draft")

        if not to or not message:
            print(f"PULADO {lead['name']}: sem telefone normalizavel ou sem draft")
            continue

        if not args.confirm:
            print(f"[dry-run] enviaria para {lead['name']} ({to}):\n  {message}\n")
            continue

        try:
            send(channel, to, message)
            supabase.table("leads").update({
                "status": "sent",
                "message_status": "sent",
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", lead["id"]).execute()
            supabase.table("conversation_events").insert({
                "lead_id": lead["id"],
                "direction": "outbound",
                "content": message,
            }).execute()
            print(f"ENVIADO {lead['name']} ({to})")
            sent += 1
        except Exception as e:
            print(f"FALHA {lead['name']} ({to}): {e}")
            failed += 1

    if args.confirm:
        print(f"Enviados: {sent} | Falhas: {failed}")


if __name__ == "__main__":
    main()
