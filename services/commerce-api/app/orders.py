"""Criação idempotente de pedidos + outbox na mesma transação."""
import hashlib
import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app import pricing
from app.catalog import brl

log = logging.getLogger("commerce.orders")


class ConflitoIdempotencia(Exception):
    """Mesma Idempotency-Key reutilizada com outro conteúdo."""


def request_hash(cliente_nome: str, conversation_id: str | None, itens: list[dict]) -> str:
    canon = json.dumps(
        {
            "cliente_nome": cliente_nome.strip(),
            "conversation_id": conversation_id,
            "itens": sorted(itens, key=lambda i: i["sku_variante"]),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canon.encode()).hexdigest()


def _existente(conn: Connection, key: str):
    return conn.execute(
        text("SELECT id, request_hash FROM pedidos WHERE idempotency_key = :k"), {"k": key}
    ).first()


def criar_pedido(
    engine: Engine,
    settings,
    idempotency_key: str,
    cliente_nome: str,
    conversation_id: str | None,
    itens: list[dict],
    correlation_id: str | None,
) -> tuple[dict, bool]:
    """Devolve (pedido, criado). criado=False quando a chave já existia."""
    itens_norm = pricing.normalizar_itens(itens, settings.max_qtd_por_item)
    h = request_hash(cliente_nome, conversation_id, itens_norm)

    with engine.begin() as conn:
        existente = _existente(conn, idempotency_key)
        if existente is None:
            cot = pricing.cotar(conn, itens_norm, settings.kit_tiers, settings.max_qtd_por_item)
            novo = conn.execute(
                text(
                    """
                    INSERT INTO pedidos (idempotency_key, request_hash, conversation_id, cliente_nome,
                                         subtotal_centavos, desconto_centavos, total_centavos, correlation_id)
                    VALUES (:k, :h, :conv, :nome, :sub, :desc, :tot, :cid)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "k": idempotency_key,
                    "h": h,
                    "conv": conversation_id,
                    "nome": cliente_nome.strip(),
                    "sub": cot["subtotal_centavos"],
                    "desc": cot["desconto_centavos"],
                    "tot": cot["total_centavos"],
                    "cid": correlation_id,
                },
            ).first()
            if novo is not None:
                pedido_id = str(novo.id)
                conn.execute(
                    text(
                        """
                        INSERT INTO itens_pedido (pedido_id, estoque_id, produto_id, quantidade,
                                                  preco_unit_centavos, desconto_pct, total_centavos)
                        VALUES (:pid, :eid, :prod, :qtd, :preco, :pct, :tot)
                        """
                    ),
                    [
                        {
                            "pid": pedido_id,
                            "eid": l["estoque_id"],
                            "prod": l["produto_id"],
                            "qtd": l["quantidade"],
                            "preco": l["preco_unit_centavos"],
                            "pct": l["desconto_pct"],
                            "tot": l["total_centavos"],
                        }
                        for l in cot["itens"]
                    ],
                )
                payload = {
                    "evento": "PedidoCriado",
                    "versao": 1,
                    "pedido_id": pedido_id,
                    "correlation_id": correlation_id,
                }
                conn.execute(
                    text(
                        """
                        INSERT INTO outbox (aggregate_type, aggregate_id, tipo_evento, payload)
                        VALUES ('pedido', :aid, 'PedidoCriado', CAST(:payload AS jsonb))
                        """
                    ),
                    {"aid": pedido_id, "payload": json.dumps(payload)},
                )
                log.info(
                    "pedido criado",
                    extra={"event": "order_created", "pedido_id": pedido_id, "total_centavos": cot["total_centavos"]},
                )
                return obter_pedido(conn, pedido_id), True
            # Corrida: outra requisição com a mesma chave acabou de gravar.
            existente = _existente(conn, idempotency_key)

        if existente.request_hash != h:
            raise ConflitoIdempotencia(
                "Esta Idempotency-Key já foi usada para um pedido com conteúdo diferente."
            )
        log.info("pedido repetido (idempotência)", extra={"event": "order_idempotent_hit", "pedido_id": str(existente.id)})
        return obter_pedido(conn, str(existente.id)), False


def obter_pedido(conn: Connection, pedido_id: str) -> dict | None:
    p = conn.execute(
        text(
            """
            SELECT id, status, motivo_rejeicao, cliente_nome, conversation_id, subtotal_centavos,
                   desconto_centavos, total_centavos, criado_em, atualizado_em
            FROM pedidos WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": pedido_id},
    ).first()
    if p is None:
        return None
    itens = conn.execute(
        text(
            """
            SELECT e.sku_variante, pr.nome, i.quantidade, i.preco_unit_centavos, i.desconto_pct, i.total_centavos
            FROM itens_pedido i
            JOIN estoque e ON e.id = i.estoque_id
            JOIN produtos pr ON pr.id = i.produto_id
            WHERE i.pedido_id = :id ORDER BY i.id
            """
        ),
        {"id": p.id},
    ).all()
    return {
        "pedido_id": str(p.id),
        "status": p.status,
        "motivo_rejeicao": p.motivo_rejeicao,
        "cliente_nome": p.cliente_nome,
        "conversation_id": str(p.conversation_id) if p.conversation_id else None,
        "itens": [
            {
                "sku_variante": i.sku_variante,
                "nome": i.nome,
                "quantidade": i.quantidade,
                "preco_unit": brl(i.preco_unit_centavos),
                "desconto_pct": float(i.desconto_pct),
                "total": brl(i.total_centavos),
            }
            for i in itens
        ],
        "subtotal": brl(p.subtotal_centavos),
        "desconto": brl(p.desconto_centavos),
        "total": brl(p.total_centavos),
        "total_centavos": p.total_centavos,
        "criado_em": p.criado_em.isoformat(),
        "atualizado_em": p.atualizado_em.isoformat(),
    }
