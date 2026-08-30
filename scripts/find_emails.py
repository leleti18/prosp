"""
Busca e-mail publico no site de leads que TEM website (o oposto do filtro
"sem site" usado pra WhatsApp) -- util pra prospeccao por e-mail (ex: RH de
empresas). Extrai da home e de paginas comuns de contato, via regex simples
no HTML publico. Nao usa nenhuma API paga.

Uso:
    python find_emails.py --account-id <uuid> --limit 20
"""

import argparse
import re

import requests

from config import get_supabase_client

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

INVALID_TLD_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "css", "js", "map",
}

# Dominios que geram falso-positivo comum ao raspar HTML: IDs de rastreamento/erro
# (Sentry embutido em sites Wix/Wordpress) e dominios de placeholder/exemplo.
BLOCKED_DOMAIN_SUFFIXES = {
    "wixpress.com", "sentry.io", "sentry-next.wixpress.com",
    "example.com", "example.org", "example.net", "exemplo.pt", "exemplo.com",
    "mysite.com", "meusite.com", "yourdomain.com", "seudominio.com", "domain.com", "email.com",
}

# IDs de rastreamento (Sentry DSN, hashes de sessao) tem local-part hexadecimal
# longo -- nao e um contato de verdade mesmo se o dominio nao estiver bloqueado.
HEX_ID_LOCAL_PART = re.compile(r"^[0-9a-f]{24,}$")

CONTACT_PATHS = ["", "/contato", "/contact", "/contact-us", "/fale-conosco", "/sobre", "/about"]

PREFERRED_KEYWORDS = ["rh", "hr", "contato", "contact", "info", "hello", "recrutamento", "careers", "carreiras"]


def is_valid_email(email):
    local_part, _, domain = email.partition("@")
    domain = domain.lower()

    domain_tld = domain.rsplit(".", 1)[-1]
    if domain_tld in INVALID_TLD_EXTENSIONS:
        return False

    if any(domain == d or domain.endswith("." + d) for d in BLOCKED_DOMAIN_SUFFIXES):
        return False

    if HEX_ID_LOCAL_PART.match(local_part.lower()):
        return False

    return True


def extract_emails(html):
    candidates = {m.group(0) for m in EMAIL_REGEX.finditer(html)}
    return {e for e in candidates if is_valid_email(e)}


def pick_best_email(emails):
    if not emails:
        return None
    for keyword in PREFERRED_KEYWORDS:
        for email in emails:
            if keyword in email.lower():
                return email
    return sorted(emails)[0]


def fetch_page(url, timeout=12):
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def find_email_for_website(website):
    base_url = website.rstrip("/")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    for path in CONTACT_PATHS:
        try:
            html = fetch_page(base_url + path)
        except Exception:
            continue
        emails = extract_emails(html)
        if emails:
            return pick_best_email(emails)

    return None


def fetch_leads_with_website_no_email(supabase, account_id, limit):
    result = (
        supabase.table("leads")
        .select("*")
        .eq("account_id", account_id)
        .not_.is_("website", "null")
        .is_("email", "null")
        .limit(limit)
        .execute()
    )
    return result.data or []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    supabase = get_supabase_client()
    leads = fetch_leads_with_website_no_email(supabase, args.account_id, args.limit)
    print(f"Leads com site e sem email: {len(leads)}")

    found = 0
    not_found = 0
    for lead in leads:
        email = find_email_for_website(lead["website"])
        if email:
            supabase.table("leads").update({"email": email}).eq("id", lead["id"]).execute()
            print(f"OK  {lead.get('name') or lead['website']}: {email}")
            found += 1
        else:
            print(f"NAO ENCONTRADO  {lead.get('name') or lead['website']} ({lead['website']})")
            not_found += 1

    print(f"Encontrados: {found} | Nao encontrados: {not_found}")


if __name__ == "__main__":
    main()
