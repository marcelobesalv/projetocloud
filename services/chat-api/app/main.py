"""chat-api: único serviço exposto ao cliente. Conversa com o LLM e usa o commerce-api como ferramentas."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import conversas
from app.agent import run_turn
from app.commerce_client import CommerceClient
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


def _resumo(mensagens: list[dict]) -> list[dict]:
    """Só as falas de texto, para a página reconstruir a conversa."""
    out = []
    for m in mensagens:
        texto = "\n".join(b["text"] for b in m["content"] if "text" in b).strip()
        if texto:
            out.append({"role": m["role"], "texto": texto})
    return out


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
            nivel = logging.DEBUG if request.url.path == "/health" and status == 200 else logging.INFO
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
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

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
        }

    @app.get("/conversations/{conversation_id}")
    def obter_conversa(conversation_id: uuid.UUID):
        carregado = conversas.carregar(engine, str(conversation_id))
        if carregado is None:
            return JSONResponse(status_code=404, content={"erro": "Conversa não encontrada.",
                                                          "codigo": "conversa_nao_encontrada"})
        return {"conversation_id": str(conversation_id), "mensagens": _resumo(carregado[0])}

    return app
