from abc import ABC, abstractmethod


class ChannelAdapter(ABC):
    @abstractmethod
    def send(self, to, message):
        """Envia `message` para `to` (numero de telefone com DDI, ex: 5541999999999).

        Retorna o JSON de resposta do provedor. Lanca excecao em caso de erro.
        """
