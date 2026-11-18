"""chat-api: único serviço exposto ao cliente. Conversa com o LLM e usa o commerce-api como ferramentas."""
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import conversas
from app.agent import run_turn
from app.commerce_client import CommerceClient, CommerceErro, CommerceIndisponivel
from app.config import Settings
from app.db import make_engine
from app.llm.base import LLMProvider
from app.logs import correlation_id_var
from app.tools import ToolExecutor

log = logging.getLogger("chat.api")
STATIC = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1)


def make_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "bedrock":
        from app.llm.bedrock import BedrockProvider

        return BedrockProvider(
            settings.llm_model_id, settings.aws_region, settings.llm_timeout_s,
            settings.llm_max_tokens, settings.llm_temperature,
        )
    if settings.llm_provider == "fake":
        from app.llm.fake import FakeProvider

        return FakeProvider()
    raise ValueError(f"LLM_PROVIDER inválido: {settings.llm_provider!r} (use bedrock ou fake)")


CATEGORIAS = ("teclado", "mouse", "headset", "monitor", "webcam", "acessorio")
# Polling do acompanhamento do pedido: não polui o log com uma linha a cada consulta bem-sucedida.
_ROTA_POLLING = re.compile(r"^/api/conversas/[^/]+/pedidos/[^/]+$")


def _resumo(mensagens: list[dict]) -> list[dict]:
    """Falas de texto, para a página reconstruir a conversa. Cada fala do consultor leva as ferramentas que ele
    chamou para respondê-la (a página mostra o que foi consultado de verdade)."""
    out, ferramentas = [], []
    for m in mensagens:
        ferramentas += [b["toolUse"]["name"] for b in m["content"] if "toolUse" in b]
        texto = "\n".join(b["text"] for b in m["content"] if "text" in b).strip()
        if not texto:
            continue
        fala = {"role": m["role"], "texto": texto}
        if m["role"] == "assistant":
            fala["ferramentas"], ferramentas = ferramentas, []
        out.append(fala)
    return out


def _kit_resumo(k: dict) -> dict:
    campos = ("nome", "variante", "quantidade", "preco_unit", "desconto_pct", "total")
    return {
        "itens": [{c: linha.get(c) for c in campos} for linha in k.get("itens", [])],
        **{c: k.get(c) for c in ("produtos_diferentes", "desconto_faixa_pct", "subtotal", "desconto", "total",
                                 "proxima_faixa", "observacao")},
    }


def estado_da_conversa(mensagens: list[dict]) -> dict:
    """Último kit cotado e último pedido vistos nos resultados das ferramentas, para o carrinho e o
    acompanhamento do pedido na página. Tudo vem do commerce-api (resultados gravados), nada do texto do LLM."""
    nomes: dict[str, str] = {}
    kit = pedido_id = None
    for m in mensagens:
        for b in m.get("content", []):
            if "toolUse" in b:
                nomes[b["toolUse"]["toolUseId"]] = b["toolUse"]["name"]
            elif "toolResult" in b:
                tr = b["toolResult"]
                dados = next((c["json"] for c in tr.get("content", []) if "json" in c), None)
                if tr.get("status") == "error" or not isinstance(dados, dict):
                    continue
                nome = nomes.get(tr.get("toolUseId"))
                if nome == "calcular_kit" and dados.get("itens"):
                    kit = _kit_resumo(dados)
                elif nome in ("criar_pedido", "status_pedido") and dados.get("pedido_id"):
                    pedido_id = str(dados["pedido_id"])
    return {"kit": kit, "pedido_id": pedido_id}


def _destaques(esp: dict) -> list[str]:
    """Até 4 características curtas para o cartão da vitrine."""
    d = []
    if esp.get("sem_fio"):
        d.append("Sem fio")
    if esp.get("tamanho_pol"):
        d.append(f'{esp["tamanho_pol"]}"')
    if esp.get("resolucao"):
        d.append(str(esp["resolucao"]))
    if esp.get("taxa_hz"):
        d.append(f'{esp["taxa_hz"]} Hz')
    if esp.get("formato_teclado"):
        d.append(str(esp["formato_teclado"]))
    d += [c for c in esp.get("conexoes", []) if isinstance(c, str)]
    return list(dict.fromkeys(d))[:4]


def _item_vitrine(p: dict) -> dict:
    variantes = p.get("variantes", [])
    return {
        "sku": p["sku"],
        "nome": p["nome"],
        "categoria": p["categoria"],
        "marca": p.get("marca"),
        "descricao": p.get("descricao"),
        "preco": p["preco"],
        "em_estoque": p.get("em_estoque", False),
        "estoque": sum(int(v.get("quantidade") or 0) for v in variantes),
        "cores": list(dict.fromkeys(v["cor"] for v in variantes if v.get("cor"))),
        "layouts": list(dict.fromkeys(v["layout"] for v in variantes if v.get("layout"))),
        "destaques": _destaques(p.get("especificacoes") or {}),
    }


def _erro_commerce(exc: Exception) -> JSONResponse:
    if isinstance(exc, CommerceErro):
        if exc.status == 404:
            return JSONResponse(status_code=404, content={"erro": exc.mensagem, "codigo": "nao_encontrado"})
        return JSONResponse(status_code=502, content={"erro": exc.mensagem, "codigo": "erro_commerce"})
    return JSONResponse(status_code=503, content={"erro": "Catálogo indisponível no momento.",
                                                  "codigo": "commerce_indisponivel"})


def create_app(settings: Settings | None = None, engine=None, provider: LLMProvider | None = None,
               commerce: CommerceClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or make_engine(settings, "chat-api")
    provider = provider or make_provider(settings)
    commerce = commerce or CommerceClient(settings.commerce_api_url, settings.commerce_timeout_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("chat-api iniciado", extra={"llm_provider": provider.name, "model_id": provider.model_id,
                                              "commerce_api_url": settings.commerce_api_url})
        yield
        commerce.close()
        engine.dispose()

    app = FastAPI(title="Setup Pronto - chat-api", lifespan=lifespan)

    @app.middleware("http")
    async def correlation_and_access_log(request: Request, call_next):
        # O chat-api é a borda: gera o correlation_id (aceita um vindo de fora só se for um UUID).
        recebido = request.headers.get("x-correlation-id", "")
        try:
            cid = str(uuid.UUID(recebido))
        except ValueError:
            cid = str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        inicio = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            silencioso = request.url.path == "/health" or _ROTA_POLLING.match(request.url.path)
            nivel = logging.DEBUG if silencioso and status == 200 else logging.INFO
            log.log(nivel, f"{request.method} {request.url.path} {status}",
                    extra={"event": "http_request", "method": request.method, "path": request.url.path,
                           "status": status, "duration_ms": round((time.perf_counter() - inicio) * 1000, 1)})
            correlation_id_var.reset(token)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"erro": "Requisição inválida", "codigo": "validacao"})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("erro não tratado", extra={"event": "unhandled_error"})
        return JSONResponse(status_code=500, content={"erro": "Erro interno", "codigo": "erro_interno"})

    @app.get("/", include_in_schema=False)
    def index():
        # no-cache: o navegador revalida a cada visita, então um deploy novo aparece sem precisar de Ctrl+F5.
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    def health():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db = "ok"
        except Exception as exc:
            db = f"erro: {exc.__class__.__name__}"
        ok = db == "ok"
        # O commerce-api NÃO entra no health: se ele cair, o chat continua no ar e responde
        # "não consegui consultar agora" em vez de ser reiniciado pelo ALB/ECS.
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ok" if ok else "erro", "service": "chat-api", "checks": {"db": db},
                     "llm_provider": provider.name},
        )

    @app.post("/chat")
    def chat(body: ChatRequest):
        mensagem = body.message.strip()
        if not mensagem:
            return JSONResponse(status_code=422, content={"erro": "Mensagem vazia.", "codigo": "validacao"})
        if len(mensagem) > settings.max_message_chars:
            return JSONResponse(
                status_code=422,
                content={"erro": f"Mensagem muito longa (máx. {settings.max_message_chars} caracteres).",
                         "codigo": "mensagem_longa"},
            )

        if body.conversation_id:
            conv_id = str(body.conversation_id)
            carregado = conversas.carregar(engine, conv_id)
            if carregado is None:
                return JSONResponse(status_code=404, content={"erro": "Conversa não encontrada.",
                                                              "codigo": "conversa_nao_encontrada"})
            historico, versao = carregado
        else:
            conv_id, historico, versao = conversas.criar(engine), [], 0

        resultado = run_turn(provider, ToolExecutor(commerce, conv_id), historico, mensagem, settings)

        # Turnos que falharam (LLM ou commerce fora) não entram no histórico: o cliente repete a pergunta.
        if resultado.novas_mensagens:
            maximo = max(settings.history_stored_max_messages, settings.history_max_messages)
            gravou, descartadas = conversas.gravar_turno(engine, conv_id, historico, resultado.novas_mensagens,
                                                         versao, maximo)
            if not gravou:
                return JSONResponse(
                    status_code=409,
                    content={"erro": "A conversa foi atualizada por outra requisição; envie a mensagem de novo.",
                             "codigo": "conflito_conversa", "conversation_id": conv_id},
                )
            if descartadas:
                log.info("mensagens antigas da conversa descartadas",
                         extra={"event": "conversation_trimmed", "conversation_id": conv_id,
                                "descartadas": descartadas, "mantidas_max": maximo})

        log.info("turno concluído", extra={"event": "chat_turn", "conversation_id": conv_id,
                                          "turn_status": resultado.status, "tools": resultado.ferramentas})
        return {
            "conversation_id": conv_id,
            "resposta": resultado.resposta,
            "status": resultado.status,
            "correlation_id": correlation_id_var.get(),
            "ferramentas": resultado.ferramentas,
            **estado_da_conversa(resultado.novas_mensagens),
        }

    @app.get("/conversations/{conversation_id}")
    def obter_conversa(conversation_id: uuid.UUID):
        carregado = conversas.carregar(engine, str(conversation_id))
        if carregado is None:
            return JSONResponse(status_code=404, content={"erro": "Conversa não encontrada.",
                                                          "codigo": "conversa_nao_encontrada"})
        return {"conversation_id": str(conversation_id), "mensagens": _resumo(carregado[0]),
                **estado_da_conversa(carregado[0])}

    @app.get("/api/vitrine")
    def vitrine(categoria: Literal[CATEGORIAS] | None = None):  # type: ignore[valid-type]
        """Catálogo para a vitrine da página, direto do commerce-api (o chat-api é o único serviço público)."""
        try:
            produtos = []
            for c in [categoria] if categoria else CATEGORIAS:
                produtos += commerce.request("GET", "/products", params={"categoria": c, "limite": 30})["produtos"]
        except (CommerceIndisponivel, CommerceErro) as exc:
            return _erro_commerce(exc)
        return {"total": len(produtos), "produtos": [_item_vitrine(p) for p in produtos]}

    @app.get("/api/conversas/{conversation_id}/pedidos/{pedido_id}")
    def acompanhar_pedido(conversation_id: uuid.UUID, pedido_id: uuid.UUID):
        """Status do pedido para o acompanhamento na página. Só responde se o pedido for da conversa informada."""
        try:
            p = commerce.request("GET", f"/orders/{pedido_id}")
        except (CommerceIndisponivel, CommerceErro) as exc:
            return _erro_commerce(exc)
        if str(p.get("conversation_id")) != str(conversation_id):
            return JSONResponse(status_code=404, content={"erro": "Pedido não encontrado.", "codigo": "nao_encontrado"})
        campos = ("pedido_id", "status", "motivo_rejeicao", "itens", "subtotal", "desconto", "total", "criado_em",
                  "atualizado_em")
        return {c: p.get(c) for c in campos}

    return app
