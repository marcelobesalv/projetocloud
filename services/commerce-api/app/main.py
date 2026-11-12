"""commerce-api: catálogo, estoque, compatibilidade, preço de kit e pedidos (serviço interno)."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import catalog, compat, orders, pricing
from app.config import Settings
from app.db import make_engine
from app.logs import correlation_id_var
from app.outbox import OutboxPublisher, pendentes
from app.reconciliacao import Reconciliador

log = logging.getLogger("commerce.api")


class Item(BaseModel):
    sku_variante: str = Field(min_length=1, max_length=120)
    quantidade: int = Field(default=1, ge=1, le=100)


class KitRequest(BaseModel):
    itens: list[Item] = Field(min_length=1, max_length=20)


class OrderRequest(BaseModel):
    cliente_nome: str = Field(min_length=2, max_length=120)
    conversation_id: uuid.UUID | None = None
    itens: list[Item] = Field(min_length=1, max_length=20)


class Perfil(BaseModel):
    conexoes: list[str] = Field(default_factory=list, max_length=10)
    so: str | None = None
    taxa_hz_max: int | None = Field(default=None, ge=30, le=1000)
    layout: Literal["ABNT2", "US", "abnt2", "us"] | None = None


class CompatRequest(BaseModel):
    perfil: Perfil = Field(default_factory=Perfil)
    skus: list[str] = Field(min_length=1, max_length=15)


def erro(status: int, mensagem: str, codigo: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": mensagem, "codigo": codigo})


def create_app(settings: Settings | None = None, engine=None, start_outbox: bool | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or make_engine(settings, "commerce-api")
    if start_outbox is None:
        start_outbox = settings.outbox_enabled and bool(settings.sqs_queue_url or settings.sqs_queue_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        publisher = None
        if start_outbox:
            publisher = OutboxPublisher(engine, settings)
            publisher.start()
        else:
            log.warning("publicador do outbox desligado (OUTBOX_PUBLISHER_ENABLED=false ou sem SQS_QUEUE_URL/SQS_QUEUE_NAME)")
        app.state.publisher = publisher
        # Só faz sentido com o publicador ligado: o reconciliador apenas regrava eventos no outbox.
        reconciliador = None
        if start_outbox and settings.reconciliacao_enabled:
            reconciliador = Reconciliador(engine, settings)
            reconciliador.start()
        app.state.reconciliador = reconciliador
        yield
        if reconciliador:
            reconciliador.stop()
        if publisher:
            publisher.stop()
        engine.dispose()

    app = FastAPI(title="Setup Pronto - commerce-api", lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings
    app.state.publisher = None
    app.state.reconciliador = None

    @app.middleware("http")
    async def correlation_and_access_log(request: Request, call_next):
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        inicio = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            dur = round((time.perf_counter() - inicio) * 1000, 1)
            nivel = logging.DEBUG if request.url.path == "/health" and status == 200 else logging.INFO
            log.log(
                nivel,
                f"{request.method} {request.url.path} {status}",
                extra={"event": "http_request", "method": request.method, "path": request.url.path,
                       "status": status, "duration_ms": dur},
            )
            correlation_id_var.reset(token)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        detalhes = [f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=422, content={"erro": "Requisição inválida", "codigo": "validacao", "detalhes": detalhes})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("erro não tratado", extra={"event": "unhandled_error"})
        return erro(500, "Erro interno", "erro_interno")

    @app.get("/health")
    def health():
        checks = {}
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:
            checks["db"] = f"erro: {exc.__class__.__name__}"
        pub = app.state.publisher
        body = {
            "status": "ok" if checks["db"] == "ok" else "erro",
            "service": "commerce-api",
            "checks": checks,
            "outbox": pub.status() if pub else {"ativo": False},
            "reconciliacao": rec.status() if (rec := app.state.reconciliador) else {"ativo": False},
        }
        return JSONResponse(status_code=200 if body["status"] == "ok" else 503, content=body)

    @app.get("/products")
    def listar_produtos(
        q: str | None = Query(default=None, max_length=100),
        categoria: Literal["teclado", "mouse", "headset", "monitor", "webcam", "acessorio"] | None = None,
        preco_max: float | None = Query(default=None, gt=0, description="Preço máximo em reais"),
        conexao: str | None = None,
        so: str | None = None,
        somente_em_estoque: bool = False,
        limite: int = Query(default=10, ge=1, le=30),
    ):
        if conexao:
            conexao = compat.CONEXAO_ALIASES.get(conexao.strip().lower(), conexao)
        if so:
            so = compat.SO_ALIASES.get(so.strip().lower(), so)
        with engine.connect() as conn:
            produtos = catalog.buscar(
                conn, q=q, categoria=categoria,
                preco_max_centavos=int(round(preco_max * 100)) if preco_max else None,
                conexao=conexao, so=so, somente_em_estoque=somente_em_estoque, limite=limite,
            )
        return {"total": len(produtos), "produtos": produtos}

    @app.get("/products/{sku}")
    def obter_produto(sku: str):
        with engine.connect() as conn:
            produtos = catalog.por_skus(conn, [sku])
        if not produtos:
            return erro(404, f"Produto {sku} não encontrado.", "produto_nao_encontrado")
        return produtos[0]

    @app.get("/stock/{sku}")
    def consultar_estoque(sku: str):
        with engine.connect() as conn:
            r = catalog.estoque(conn, sku)
        if r is None:
            return erro(404, f"SKU {sku} não encontrado.", "produto_nao_encontrado")
        return r

    @app.post("/compatibility")
    def verificar_compatibilidade(body: CompatRequest):
        with engine.connect() as conn:
            produtos = catalog.por_skus(conn, body.skus)
        encontrados = {p["sku"] for p in produtos} | {v["sku_variante"] for p in produtos for v in p["variantes"]}
        nao_encontrados = [s for s in body.skus if s not in encontrados]
        resultado = compat.verificar(body.perfil.model_dump(), produtos)
        resultado["nao_encontrados"] = nao_encontrados
        return resultado

    @app.post("/kits/quote")
    def cotar_kit(body: KitRequest):
        try:
            with engine.connect() as conn:
                return pricing.cotar(
                    conn, [i.model_dump() for i in body.itens], settings.kit_tiers, settings.max_qtd_por_item
                )
        except pricing.ItemInvalido as exc:
            return erro(422, str(exc), "item_invalido")

    @app.post("/orders")
    def criar_pedido(
        body: OrderRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        if not idempotency_key or not (8 <= len(idempotency_key) <= 200):
            return erro(400, "Header Idempotency-Key obrigatório (8 a 200 caracteres).", "idempotency_key_ausente")
        try:
            pedido, criado = orders.criar_pedido(
                engine, settings, idempotency_key, body.cliente_nome,
                str(body.conversation_id) if body.conversation_id else None,
                [i.model_dump() for i in body.itens], correlation_id_var.get(),
            )
        except pricing.ItemInvalido as exc:
            return erro(422, str(exc), "item_invalido")
        except orders.ConflitoIdempotencia as exc:
            return erro(409, str(exc), "conflito_idempotencia")
        return JSONResponse(
            status_code=201 if criado else 200,
            content=pedido,
            headers={"Idempotent-Replay": "false" if criado else "true"},
        )

    @app.get("/orders/{pedido_id}")
    def obter_pedido(pedido_id: uuid.UUID):
        with engine.connect() as conn:
            p = orders.obter_pedido(conn, str(pedido_id))
        if p is None:
            return erro(404, "Pedido não encontrado.", "pedido_nao_encontrado")
        return p

    @app.get("/internal/outbox")
    def outbox_status():
        return {"pendentes": pendentes(engine), "publicador": app.state.publisher.status() if app.state.publisher else None}

    return app
