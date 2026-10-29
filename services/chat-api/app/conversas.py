"""Histórico de conversas (tabela conversas) com controle otimista de versão."""
import json

from sqlalchemy import text
from sqlalchemy.engine import Engine


def criar(engine: Engine) -> str:
    with engine.begin() as conn:
        return str(conn.execute(text("INSERT INTO conversas DEFAULT VALUES RETURNING id")).scalar_one())


def carregar(engine: Engine, conversation_id: str) -> tuple[list[dict], int] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT mensagens, versao FROM conversas WHERE id = CAST(:id AS uuid)"), {"id": conversation_id}
        ).first()
    return (row.mensagens, row.versao) if row else None


def anexar(engine: Engine, conversation_id: str, novas: list[dict], versao: int) -> bool:
    """Acrescenta mensagens se ninguém alterou a conversa desde a leitura. False = conflito."""
    with engine.begin() as conn:
        r = conn.execute(
            text(
                """
                UPDATE conversas
                SET mensagens = mensagens || CAST(:novas AS jsonb), versao = versao + 1, atualizado_em = now()
                WHERE id = CAST(:id AS uuid) AND versao = :versao
                """
            ),
            {"novas": json.dumps(novas, ensure_ascii=False), "id": conversation_id, "versao": versao},
        )
        return r.rowcount == 1
