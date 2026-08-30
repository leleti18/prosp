"""
Cria a primeira conta (uso pessoal) na tabela accounts.

Uso:
    python create_account.py --name "Meu Negocio" --provider evolution
"""

import argparse

from config import get_supabase_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--provider", choices=["meta", "evolution"], default="evolution")
    args = parser.parse_args()

    supabase = get_supabase_client()
    result = (
        supabase.table("accounts")
        .insert({"name": args.name, "provider": args.provider})
        .execute()
    )
    account = result.data[0]
    print(f"Conta criada: {account['id']} ({account['name']})")


if __name__ == "__main__":
    main()
