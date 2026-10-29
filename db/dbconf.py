"""Configuração de conexão do job de migração, lida só de variáveis de ambiente."""
import os

from sqlalchemy.engine import URL


def admin_url() -> URL:
    """URL do usuário administrador (dono do schema). Usado só pelo job de migração."""
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD") or None,
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "setup_pronto"),
        query={"sslmode": os.environ.get("DB_SSLMODE", "prefer")},
    )
