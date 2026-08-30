"""
Interface unica de disparo, independente do canal (numero de telefone) escolhido.

Uma conta pode ter varios canais (varios numeros/instancias conectados). Cada
canal tem seu proprio provider + config (ver tabela `channels`).

Uso:
    from channel_adapter import send
    send(channel, to="5541999999999", message="Oi, tudo bem?")
"""

from adapters.evolution_adapter import EvolutionAdapter
from adapters.meta_adapter import MetaAdapter

ADAPTERS_BY_PROVIDER = {
    "meta": MetaAdapter,
    "evolution": EvolutionAdapter,
}


def get_adapter(channel):
    provider = channel.get("provider")
    adapter_cls = ADAPTERS_BY_PROVIDER.get(provider)
    if not adapter_cls:
        raise ValueError(f"Provider desconhecido ou nao configurado: {provider!r}")
    return adapter_cls(channel.get("config") or {})


def send(channel, to, message):
    adapter = get_adapter(channel)
    return adapter.send(to, message)
