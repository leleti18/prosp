"""
Adapter para Evolution API (self-hosted, WhatsApp via QR code / Baileys).

Espera accounts.whatsapp_config no formato:
{
    "base_url": "https://sua-evolution-api.com",
    "api_key": "chave-da-instancia",
    "instance": "nome-da-instancia"
}

Sem restricao de janela de 24h nem template, mas roda fora do canal oficial:
existe risco de bloqueio do numero se detectado padrao de spam/bot. Aplicar
rate-limit e aquecimento de numero antes de disparar em volume.
"""

import requests

from .base import ChannelAdapter


class EvolutionAdapter(ChannelAdapter):
    def __init__(self, config):
        self.base_url = config["base_url"].rstrip("/")
        self.api_key = config["api_key"]
        self.instance = config["instance"]

    def send(self, to, message):
        url = f"{self.base_url}/message/sendText/{self.instance}"
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        body = {"number": to, "text": message}

        response = requests.post(url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        return response.json()
