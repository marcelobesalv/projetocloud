"""Testes do chat-api: banco real (tabela conversas) e commerce-api simulado com httpx.MockTransport."""
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

DB_DIR = Path(os.environ.get("DB_MIGRATIONS_DIR") or Path(__file__).resolve().parents[3] / "db")
sys.path.insert(0, str(DB_DIR))
ADMIN_URL = os.environ.get(
    "TEST_DB_ADMIN_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)


@pytest.fixture(scope="session")
def engine():
    from alembic import command
    from alembic.config import Config

    name = f"test_chat_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(make_url(ADMIN_URL).set(database=name))
    cfg = Config(str(DB_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(DB_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


PRODUTO_STUB = {
    "sku": "TEC-STUB-1",
    "nome": "Teclado Stub Bluetooth",
    "categoria": "teclado",
    "marca": "Stub",
    "descricao": "Teclado de teste",
    "preco": "R$ 123,45",
    "preco_centavos": 12345,
    "especificacoes": {"conexoes": ["Bluetooth"], "so_suportados": ["macOS"], "layouts": ["ABNT2"]},
    "em_estoque": True,
    "variantes": [{"sku_variante": "TEC-STUB-1-ABNT2", "layout": "ABNT2", "quantidade": 3, "disponivel": True}],
}


class CommerceStub:
    """Simula o commerce-api e registra as requisições recebidas."""

    def __init__(self, fora_do_ar=False):
        self.fora_do_ar = fora_do_ar
        self.requisicoes: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requisicoes.append(request)
        if self.fora_do_ar:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/products":
            return httpx.Response(200, json={"total": 1, "produtos": [PRODUTO_STUB]})
        if request.url.path == "/orders" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(201, json={"pedido_id": str(uuid.uuid4()), "status": "PENDENTE",
                                             "total": "R$ 123,45", "desconto": "R$ 0,00", "itens": body["itens"]})
        return httpx.Response(404, json={"erro": "não encontrado", "codigo": "x"})


@pytest.fixture
def settings():
    from app.config import Settings

    return Settings(llm_provider="fake", max_tool_iterations=4, turn_budget_s=10)


@pytest.fixture
def make_client(engine, settings):
    from fastapi.testclient import TestClient

    from app.commerce_client import CommerceClient
    from app.llm.fake import FakeProvider
    from app.main import create_app

    clients = []

    def _make(stub=None, provider=None):
        stub = stub or CommerceStub()
        commerce = CommerceClient("http://commerce", 2, transport=httpx.MockTransport(stub))
        app = create_app(settings, engine=engine, provider=provider or FakeProvider(), commerce=commerce)
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c, stub

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
