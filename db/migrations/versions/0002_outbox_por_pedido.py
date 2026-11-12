"""Índice do outbox por agregado (reconciliação de pedidos PENDENTE busca os eventos de cada pedido).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbox_agregado ON outbox (aggregate_type, aggregate_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_outbox_agregado")
