"""Cliente HTTP do commerce-api (propaga X-Correlation-ID)."""
import logging

import httpx

from app.logs import correlation_id_var

log = logging.getLogger("chat.commerce")


class CommerceIndisponivel(Exception):
    """Rede, timeout ou erro 5xx: não dá para confiar em nenhum dado agora."""


class CommerceErro(Exception):
    """Erro de negócio (4xx), com a mensagem do commerce-api. O LLM pode corrigir e tentar de novo."""

    def __init__(self, status: int, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


class CommerceClient:
    def __init__(self, base_url: str, timeout_s: float, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=min(2.0, timeout_s)),
            transport=transport,
        )

    def request(self, method: str, path: str, *, params=None, json=None, headers=None) -> dict:
        h = {"X-Correlation-ID": correlation_id_var.get() or ""}
        h.update(headers or {})
        # Uma nova tentativa só em falha de transporte. Todas as chamadas são seguras para repetir:
        # leituras, cotações puras e POST /orders com Idempotency-Key.
        for tentativa in (1, 2):
            try:
                resp = self.http.request(method, path, params=params, json=json, headers=h)
                break
            except httpx.TransportError as exc:
                log.warning(
                    "falha de transporte com commerce-api",
                    extra={"event": "commerce_transport_error", "path": path, "tentativa": tentativa,
                           "erro": exc.__class__.__name__},
                )
                if tentativa == 2:
                    raise CommerceIndisponivel(exc.__class__.__name__) from exc
        if resp.status_code >= 500:
            raise CommerceIndisponivel(f"HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise CommerceIndisponivel("resposta inválida") from exc
        if resp.status_code >= 400:
            msg = body.get("erro", "erro") if isinstance(body, dict) else "erro"
            if isinstance(body, dict) and body.get("detalhes"):
                msg += ": " + "; ".join(body["detalhes"])
            raise CommerceErro(resp.status_code, msg)
        return body

    def close(self):
        self.http.close()
