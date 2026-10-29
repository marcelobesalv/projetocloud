"""Testes rodam contra um PostgreSQL real: cria um banco temporário, aplica as migrações e o seed.

TEST_DB_ADMIN_URL  usuário com permissão de CREATE DATABASE
                   (padrão: postgresql+psycopg://postgres:postgres@localhost:5432/postgres)
DB_MIGRATIONS_DIR  pasta db/ do repositório (padrão: ../../db relativo a este serviço)
"""
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
    from seed import seed

    name = f"test_commerce_{uuid.uuid4().hex[:10]}"
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
        seed(conn)
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def settings():
    from app.config import Settings

    return Settings(sqs_queue_url=None, outbox_enabled=False)


@pytest.fixture(scope="session")
def client(engine, settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(settings, engine=engine, start_outbox=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def produto_teste(engine):
    """Cria um produto exclusivo do teste com duas variantes e devolve os SKUs."""

    def _criar(qtds=(5, 5), preco=10000, desconto_max=10, categoria="acessorio"):
        sku = f"TST-{uuid.uuid4().hex[:8].upper()}"
        with engine.begin() as conn:
            pid = conn.execute(
                text(
                    """INSERT INTO produtos (sku, nome, categoria, marca, preco_centavos, desconto_max_pct)
                       VALUES (:s, :n, :c, 'Teste', :p, :d) RETURNING id"""
                ),
                {"s": sku, "n": f"Produto {sku}", "c": categoria, "p": preco, "d": desconto_max},
            ).scalar_one()
            variantes = []
            for i, q in enumerate(qtds):
                skuv = f"{sku}-V{i}"
                conn.execute(
                    text("INSERT INTO estoque (produto_id, sku_variante, cor, quantidade) VALUES (:p, :v, :c, :q)"),
                    {"p": pid, "v": skuv, "c": f"Cor{i}", "q": q},
                )
                variantes.append(skuv)
        return sku, variantes

    return _criar
