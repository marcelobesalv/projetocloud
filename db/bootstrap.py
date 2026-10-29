"""Job de migração (rodar como tarefa avulsa antes de subir os serviços).

1. Cria/atualiza um usuário de banco por serviço (se as senhas vierem do ambiente).
2. Aplica as migrações Alembic.
3. Concede o mínimo de privilégios a cada usuário.
4. Roda o seed idempotente (SEED_ON_MIGRATE=true).

Variáveis: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSLMODE (usuário dono do schema)
           CHAT_DB_USER/CHAT_DB_PASSWORD, COMMERCE_DB_USER/COMMERCE_DB_PASSWORD,
           WORKER_DB_USER/WORKER_DB_PASSWORD, SEED_ON_MIGRATE
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from dbconf import admin_url
from seed import seed

HERE = Path(__file__).resolve().parent

# Privilégios mínimos por serviço.
GRANTS = {
    "CHAT": [
        "GRANT SELECT, INSERT, UPDATE ON conversas TO {role}",
    ],
    "COMMERCE": [
        "GRANT SELECT ON produtos, estoque TO {role}",
        "GRANT SELECT, INSERT ON pedidos, itens_pedido TO {role}",
        "GRANT SELECT, INSERT, UPDATE ON outbox TO {role}",
        "GRANT USAGE ON SEQUENCE itens_pedido_id_seq, outbox_id_seq TO {role}",
    ],
    "WORKER": [
        "GRANT SELECT ON produtos, itens_pedido TO {role}",
        "GRANT SELECT, UPDATE ON pedidos, estoque TO {role}",
    ],
}
DEFAULT_ROLES = {"CHAT": "chat_api", "COMMERCE": "commerce_api", "WORKER": "order_worker"}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {"level": record.levelname, "service": "db-migrate", "message": record.getMessage()},
            ensure_ascii=False,
        )


log = logging.getLogger("db-migrate")


def wait_for_db(engine, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            if time.monotonic() > deadline:
                raise
            log.info(f"banco indisponível, tentando de novo: {exc.__class__.__name__}")
            time.sleep(2)


def ensure_roles(engine) -> list[tuple[str, str]]:
    """Cria ou atualiza a senha dos usuários de serviço. Devolve [(prefixo, role)]."""
    roles = []
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        raw = conn.connection.dbapi_connection
        db_name = conn.execute(text("SELECT current_database()")).scalar_one()
        for prefix, default in DEFAULT_ROLES.items():
            role = os.environ.get(f"{prefix}_DB_USER", default)
            password = os.environ.get(f"{prefix}_DB_PASSWORD")
            if not password:
                log.info(f"{prefix}_DB_PASSWORD não definido; usuário {role} não será criado/alterado")
                exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role}).first()
                if exists:
                    roles.append((prefix, role))
                continue
            exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role}).first()
            verb = "ALTER" if exists else "CREATE"
            stmt = sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
            with raw.cursor() as cur:
                cur.execute(stmt)
                cur.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(db_name), sql.Identifier(role)
                    )
                )
                cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            log.info(f"usuário {role} {'atualizado' if exists else 'criado'}")
            roles.append((prefix, role))
    return roles


def apply_grants(engine, roles):
    with engine.begin() as conn:
        raw = conn.connection.dbapi_connection
        with raw.cursor() as cur:
            for prefix, role in roles:
                for g in GRANTS[prefix]:
                    cur.execute(sql.SQL(g).format(role=sql.Identifier(role)))
                log.info(f"privilégios aplicados a {role}")


def migrate(engine):
    cfg = Config(str(HERE / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
    log.info("migrações aplicadas")


def main() -> int:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    engine = create_engine(admin_url())
    wait_for_db(engine)
    roles = ensure_roles(engine)
    migrate(engine)
    apply_grants(engine, roles)
    if os.environ.get("SEED_ON_MIGRATE", "true").lower() == "true":
        with engine.begin() as conn:
            log.info(f"seed: {seed(conn)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
