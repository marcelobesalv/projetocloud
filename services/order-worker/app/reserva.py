"""Reserva de estoque tudo-ou-nada.

Tudo acontece numa única transação:
  1. trava a linha do pedido (SELECT ... FOR UPDATE) — entregas duplicadas simultâneas
     ficam em fila aqui e a segunda já encontra o pedido fora de PENDENTE;
  2. só mexe em pedidos PENDENTE (idempotência);
  3. baixa cada variante com UPDATE condicional (quantidade >= pedida), em ordem de estoque_id
     para evitar deadlock entre pedidos concorrentes;
  4. se algum item faltar, desfaz todas as baixas (savepoint) e marca REJEITADO com o motivo.
"""
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("worker.reserva")


class PedidoNaoEncontrado(Exception):
    pass


@dataclass
class Resultado:
    acao: str  # CONFIRMADO | REJEITADO | IGNORADO
    status_final: str
    motivo: str | None = None


def processar_pedido(engine: Engine, pedido_id: str) -> Resultado:
    with engine.begin() as conn:
        pedido = conn.execute(
            text("SELECT status FROM pedidos WHERE id = CAST(:id AS uuid) FOR UPDATE"), {"id": pedido_id}
        ).first()
        if pedido is None:
            raise PedidoNaoEncontrado(pedido_id)
        if pedido.status != "PENDENTE":
            return Resultado("IGNORADO", pedido.status)

        itens = conn.execute(
            text(
                """
                SELECT i.estoque_id, SUM(i.quantidade)::int AS quantidade, e.sku_variante, p.nome
                FROM itens_pedido i
                JOIN estoque e ON e.id = i.estoque_id
                JOIN produtos p ON p.id = i.produto_id
                WHERE i.pedido_id = CAST(:id AS uuid)
                GROUP BY i.estoque_id, e.sku_variante, p.nome
                ORDER BY i.estoque_id
                """
            ),
            {"id": pedido_id},
        ).all()

        faltando = []
        savepoint = conn.begin_nested()
        for it in itens:
            r = conn.execute(
                text(
                    """
                    UPDATE estoque SET quantidade = quantidade - :q, atualizado_em = now()
                    WHERE id = :id AND quantidade >= :q
                    """
                ),
                {"q": it.quantidade, "id": it.estoque_id},
            )
            if r.rowcount != 1:
                faltando.append(it)

        if faltando or not itens:
            savepoint.rollback()
            disponivel = dict(
                conn.execute(
                    text("SELECT id, quantidade FROM estoque WHERE id = ANY(:ids)"),
                    {"ids": [f.estoque_id for f in faltando]},
                ).all()
            )
            if itens:
                motivo = "Estoque insuficiente: " + "; ".join(
                    f"{f.nome} ({f.sku_variante}) — pedido {f.quantidade}, disponível {disponivel.get(f.estoque_id, 0)}"
                    for f in faltando
                )
            else:
                motivo = "Pedido sem itens."
            conn.execute(
                text(
                    """
                    UPDATE pedidos SET status = 'REJEITADO', motivo_rejeicao = :m, atualizado_em = now()
                    WHERE id = CAST(:id AS uuid) AND status = 'PENDENTE'
                    """
                ),
                {"m": motivo, "id": pedido_id},
            )
            return Resultado("REJEITADO", "REJEITADO", motivo)

        savepoint.commit()
        conn.execute(
            text(
                """
                UPDATE pedidos SET status = 'CONFIRMADO', atualizado_em = now()
                WHERE id = CAST(:id AS uuid) AND status = 'PENDENTE'
                """
            ),
            {"id": pedido_id},
        )
        return Resultado("CONFIRMADO", "CONFIRMADO")
