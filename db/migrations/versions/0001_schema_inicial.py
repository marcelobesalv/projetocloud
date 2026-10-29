"""Schema inicial: produtos, estoque, pedidos, itens_pedido, outbox, conversas.

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE = """
CREATE TABLE produtos (
    id              BIGSERIAL PRIMARY KEY,
    sku             TEXT NOT NULL UNIQUE,
    nome            TEXT NOT NULL,
    categoria       TEXT NOT NULL
                    CHECK (categoria IN ('teclado','mouse','headset','monitor','webcam','acessorio')),
    marca           TEXT NOT NULL,
    descricao       TEXT NOT NULL DEFAULT '',
    preco_centavos  INTEGER NOT NULL CHECK (preco_centavos > 0),
    desconto_max_pct NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (desconto_max_pct BETWEEN 0 AND 50),
    especificacoes  JSONB NOT NULL DEFAULT '{}'::jsonb,
    ativo           BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_produtos_categoria ON produtos (categoria) WHERE ativo;
CREATE INDEX ix_produtos_especificacoes ON produtos USING GIN (especificacoes jsonb_path_ops);

CREATE TABLE estoque (
    id              BIGSERIAL PRIMARY KEY,
    produto_id      BIGINT NOT NULL REFERENCES produtos(id),
    sku_variante    TEXT NOT NULL UNIQUE,
    switch          TEXT,
    layout          TEXT CHECK (layout IN ('ABNT2','US')),
    cor             TEXT,
    quantidade      INTEGER NOT NULL CHECK (quantidade >= 0),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_estoque_variante UNIQUE NULLS NOT DISTINCT (produto_id, switch, layout, cor)
);
CREATE INDEX ix_estoque_produto ON estoque (produto_id);

CREATE TABLE pedidos (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key   TEXT NOT NULL,
    request_hash      TEXT NOT NULL,
    conversation_id   UUID,
    cliente_nome      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'PENDENTE'
                      CHECK (status IN ('PENDENTE','CONFIRMADO','REJEITADO')),
    motivo_rejeicao   TEXT,
    subtotal_centavos INTEGER NOT NULL CHECK (subtotal_centavos >= 0),
    desconto_centavos INTEGER NOT NULL CHECK (desconto_centavos >= 0),
    total_centavos    INTEGER NOT NULL CHECK (total_centavos >= 0),
    correlation_id    TEXT,
    criado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_pedidos_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT ck_pedidos_motivo CHECK ((status = 'REJEITADO') = (motivo_rejeicao IS NOT NULL))
);
CREATE INDEX ix_pedidos_pendentes ON pedidos (criado_em) WHERE status = 'PENDENTE';
CREATE INDEX ix_pedidos_conversation ON pedidos (conversation_id);

CREATE TABLE itens_pedido (
    id                  BIGSERIAL PRIMARY KEY,
    pedido_id           UUID NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    estoque_id          BIGINT NOT NULL REFERENCES estoque(id),
    produto_id          BIGINT NOT NULL REFERENCES produtos(id),
    quantidade          INTEGER NOT NULL CHECK (quantidade > 0),
    preco_unit_centavos INTEGER NOT NULL CHECK (preco_unit_centavos > 0),
    desconto_pct        NUMERIC(5,2) NOT NULL DEFAULT 0,
    total_centavos      INTEGER NOT NULL CHECK (total_centavos >= 0),
    CONSTRAINT uq_itens_pedido_variante UNIQUE (pedido_id, estoque_id)
);

CREATE TABLE outbox (
    id             BIGSERIAL PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id   TEXT NOT NULL,
    tipo_evento    TEXT NOT NULL,
    payload        JSONB NOT NULL,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
    publicado_em   TIMESTAMPTZ,
    tentativas     INTEGER NOT NULL DEFAULT 0,
    ultimo_erro    TEXT
);
CREATE INDEX ix_outbox_pendentes ON outbox (id) WHERE publicado_em IS NULL;

CREATE TABLE conversas (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mensagens     JSONB NOT NULL DEFAULT '[]'::jsonb,
    versao        INTEGER NOT NULL DEFAULT 0,
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS conversas;
DROP TABLE IF EXISTS outbox;
DROP TABLE IF EXISTS itens_pedido;
DROP TABLE IF EXISTS pedidos;
DROP TABLE IF EXISTS estoque;
DROP TABLE IF EXISTS produtos;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
