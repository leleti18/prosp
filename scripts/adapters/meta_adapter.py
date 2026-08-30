"""
Adapter para WhatsApp Cloud API (Meta oficial).

Espera accounts.whatsapp_config no formato:
{
    "phone_number_id": "1234567890",
    "access_token": "EAAG...",
    "template_name": "primeiro_contato",   # opcional
    "template_language": "pt_BR"           # opcional, default pt_BR
}

Fora da janela de 24h apos o ultimo contato do lead, a Meta so aceita mensagens
via template pre-aprovado. Se `template_name` estiver configurado, a mensagem
gerada e enviada como parametro {{1}} do corpo do template. Sem `template_name`,
a mensagem e enviada como texto livre (so funciona dentro da janela de 24h).
"""

import requests

from .base import ChannelAdapter

GRAPH_API_URL = "https://graph.facebook.com/v20.0"


class MetaAdapter(ChannelAdapter):
    def __init__(self, config):
        self.phone_number_id = config["phone_number_id"]
        self.access_token = config["access_token"]
        self.template_name = config.get("template_name")
        self.template_language = config.get("template_language", "pt_BR")

    def send(self, to, message):
        url = f"{GRAPH_API_URL}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        if self.template_name:
            body = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": self.template_name,
                    "language": {"code": self.template_language},
                    "components": [
                        {"type": "body", "parameters": [{"type": "text", "text": message}]}
                    ],
                },
            }
        else:
            body = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": message},
            }

        response = requests.post(url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        return response.json()
