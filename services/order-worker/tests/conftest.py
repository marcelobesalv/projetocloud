"""Testes contra PostgreSQL real (banco temporário com as migrações do repositório).

TEST_DB_ADMIN_URL  usuário com permissão de CREATE DATABASE
DB_MIGRATIONS_DIR  pasta db/ do repositório (padrão: ../../db)
"""
import json
import os
import sys
import uuid
from pathlib import Path

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

    name = f"test_worker_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(make_url(ADMIN_URL).set(database=name), pool_size=10, max_overflow=10)
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


@pytest.fixture
def variante(engine):
    """Cria uma variante com a quantidade dada e devolve o id do estoque."""

    def _criar(quantidade: int, preco: int = 10000) -> int:
        sku = f"TST-{uuid.uuid4().hex[:8].upper()}"
        with engine.begin() as conn:
            pid = conn.execute(
                text(
                    "INSERT INTO produtos (sku, nome, categoria, marca, preco_centavos) "
                    "VALUES (:s, :n, 'acessorio', 'Teste', :p) RETURNING id"
                ),
                {"s": sku, "n": f"Produto {sku}", "p": preco},
            ).scalar_one()
            return conn.execute(
                text(
                    "INSERT INTO estoque (produto_id, sku_variante, quantidade) VALUES (:p, :v, :q) RETURNING id"
                ),
                {"p": pid, "v": f"{sku}-V", "q": quantidade},
            ).scalar_one()

    return _criar


@pytest.fixture
def pedido(engine):
    """Cria um pedido PENDENTE com itens [(estoque_id, quantidade)] e devolve o id."""

    def _criar(itens: list[tuple[int, int]]) -> str:
        with engine.begin() as conn:
            pid = conn.execute(
                text(
                    "INSERT INTO pedidos (idempotency_key, request_hash, cliente_nome, subtotal_centavos, "
                    "desconto_centavos, total_centavos) VALUES (:k, 'h', 'Teste', 0, 0, 0) RETURNING id"
                ),
                {"k": str(uuid.uuid4())},
            ).scalar_one()
            for estoque_id, qtd in itens:
                conn.execute(
                    text(
                        "INSERT INTO itens_pedido (pedido_id, estoque_id, produto_id, quantidade, "
                        "preco_unit_centavos, total_centavos) "
                        "SELECT :p, e.id, e.produto_id, :q, 10000, 10000 * :q FROM estoque e WHERE e.id = :e"
                    ),
                    {"p": pid, "e": estoque_id, "q": qtd},
                )
        return str(pid)

    return _criar


def estoque_de(engine, estoque_id):
    with engine.connect() as c:
        return c.execute(text("SELECT quantidade FROM estoque WHERE id=:i"), {"i": estoque_id}).scalar_one()


def pedido_de(engine, pedido_id):
    with engine.connect() as c:
        return c.execute(
            text("SELECT status, motivo_rejeicao FROM pedidos WHERE id=CAST(:i AS uuid)"), {"i": pedido_id}
        ).one()


class FakeSQS:
    """Registra apagamentos; não precisa de AWS."""

    def __init__(self):
        self.apagadas = []

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.apagadas.append(ReceiptHandle)


def mensagem(pedido_id, receipt="rh-1", cid="cid-teste", receive_count=1):
    return {
        "MessageId": str(uuid.uuid4()),
        "ReceiptHandle": receipt,
        "Body": json.dumps({"evento": "PedidoCriado", "pedido_id": pedido_id, "correlation_id": cid}),
        "Attributes": {"ApproximateReceiveCount": str(receive_count)},
    }
