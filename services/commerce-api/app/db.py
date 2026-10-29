from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine


def make_engine(s, application_name: str) -> Engine:
    url = URL.create(
        "postgresql+psycopg",
        username=s.db_user,
        password=s.db_password or None,
        host=s.db_host,
        port=s.db_port,
        database=s.db_name,
        query={"sslmode": s.db_sslmode},
    )
    return create_engine(
        url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": 5,
            "application_name": application_name,
            "options": f"-c statement_timeout={s.db_statement_timeout_ms}",
        },
    )
