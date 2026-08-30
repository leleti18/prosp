"""
Criacao/conexao de instancias Evolution API via QR code.

Usado pelo painel (app.py) na pagina Canais, pra conectar um numero novo sem
precisar abrir o Evolution Manager separadamente.
"""

import requests


def get_connection_state(base_url, api_key, instance):
    url = f"{base_url.rstrip('/')}/instance/connectionState/{instance}"
    headers = {"apikey": api_key}
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def create_instance(base_url, api_key, instance):
    url = f"{base_url.rstrip('/')}/instance/create"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    body = {"instanceName": instance, "qrcode": True, "integration": "WHATSAPP-BAILEYS"}
    response = requests.post(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    return response.json()


def get_qrcode(base_url, api_key, instance):
    url = f"{base_url.rstrip('/')}/instance/connect/{instance}"
    headers = {"apikey": api_key}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def extract_qrcode_base64(response_json):
    qrcode_field = response_json.get("qrcode")
    if isinstance(qrcode_field, dict):
        return qrcode_field.get("base64")
    return response_json.get("base64")
