"""
Captacao de leads via Google Places API (New) - Text Search.

Busca negocios por categoria + regiao, filtra os que nao tem site cadastrado
(lead quente) e grava no Supabase (tabelas leads + lead_sources).

Uso:
    python capture_google_places.py --account-id <uuid> --category "salao de beleza" --city "Curitiba, PR"
"""

import argparse
import time

import requests

from config import GOOGLE_PLACES_API_KEY, get_supabase_client

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
])

PAGE_DELAY_SECONDS = 2


def search_places(query, page_token=None, language_code="pt-BR"):
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {"textQuery": query, "languageCode": language_code}
    if page_token:
        body["pageToken"] = page_token

    response = requests.post(PLACES_SEARCH_URL, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_all_places(query, max_results, language_code="pt-BR"):
    places = []
    page_token = None

    while len(places) < max_results:
        data = search_places(query, page_token, language_code)
        places.extend(data.get("places", []))

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(PAGE_DELAY_SECONDS)

    return places[:max_results]


def extract_component(components, component_type):
    for component in components or []:
        if component_type in component.get("types", []):
            return component.get("shortText") or component.get("longText")
    return None


def extract_city(components):
    return extract_component(components, "locality") or extract_component(
        components, "administrative_area_level_2"
    )


def to_lead_row(place, account_id, category):
    address_components = place.get("addressComponents", [])
    return {
        "account_id": account_id,
        "name": place.get("displayName", {}).get("text"),
        "phone": place.get("nationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "formatted_address": place.get("formattedAddress"),
        "city": extract_city(address_components),
        "state": extract_component(address_components, "administrative_area_level_1"),
        "category": category,
        "rating": place.get("rating"),
        "user_ratings_total": place.get("userRatingCount"),
        "google_place_id": place.get("id"),
    }


def save_leads(supabase, leads, raw_places_by_place_id):
    if not leads:
        return []

    try:
        result = (
            supabase.table("leads")
            .upsert(leads, on_conflict="account_id,google_place_id")
            .execute()
        )
        saved_leads = result.data or []
    except Exception:
        # Uma linha pode colidir com a constraint unica de telefone (dedup de outra
        # origem, ex: CSV) mesmo tendo google_place_id diferente. Nesse caso o lote
        # inteiro falharia; gravamos uma a uma pra so pular a linha problematica.
        saved_leads = []
        for lead in leads:
            try:
                result = (
                    supabase.table("leads")
                    .upsert([lead], on_conflict="account_id,google_place_id")
                    .execute()
                )
                saved_leads.extend(result.data or [])
            except Exception as e:
                print(f"PULADO {lead.get('name')}: {e}")

    source_rows = [
        {
            "lead_id": lead["id"],
            "source_type": "google_places",
            "source_ref": lead["google_place_id"],
            "raw_data": raw_places_by_place_id.get(lead["google_place_id"], {}),
        }
        for lead in saved_leads
        if lead.get("google_place_id")
    ]
    if source_rows:
        supabase.table("lead_sources").insert(source_rows).execute()

    return saved_leads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True, help="UUID da conta (tabela accounts)")
    parser.add_argument("--category", required=True, help='Ex: "salao de beleza", "restaurante"')
    parser.add_argument("--city", required=True, help='Ex: "Curitiba, PR"')
    parser.add_argument("--max-results", type=int, default=60)
    parser.add_argument(
        "--language-code", default="pt-BR",
        help='Ex: "pt-BR" (Brasil) ou "pt-PT" (Portugal)',
    )
    parser.add_argument(
        "--include-with-website",
        action="store_true",
        help="Por padrao so grava leads sem site. Use esta flag para gravar todos.",
    )
    args = parser.parse_args()

    if not GOOGLE_PLACES_API_KEY:
        raise SystemExit("GOOGLE_PLACES_API_KEY nao configurada no .env")

    query = f"{args.category} em {args.city}"
    print(f"Buscando: {query}")

    places = fetch_all_places(query, args.max_results, args.language_code)
    print(f"Total encontrado: {len(places)}")

    if not args.include_with_website:
        places = [p for p in places if not p.get("websiteUri")]
        print(f"Sem site (lead quente): {len(places)}")

    raw_by_place_id = {p["id"]: p for p in places if p.get("id")}
    lead_rows = [to_lead_row(p, args.account_id, args.category) for p in places]

    supabase = get_supabase_client()
    saved = save_leads(supabase, lead_rows, raw_by_place_id)
    print(f"Gravados no Supabase: {len(saved)}")


if __name__ == "__main__":
    main()
