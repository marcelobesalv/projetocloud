"""Seed idempotente: atualiza produtos por SKU e cria variantes novas.

Quantidades de estoque só são definidas na criação da variante — rodar o seed de novo
(por exemplo, a cada deploy) nunca sobrescreve o estoque real.
"""
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from seed.catalogo import PRODUTOS, sku_variante


def seed(conn: Connection) -> dict:
    produtos = variantes_novas = 0
    for p in PRODUTOS:
        produto_id = conn.execute(
            text(
                """
                INSERT INTO produtos (sku, nome, categoria, marca, descricao, preco_centavos,
                                      desconto_max_pct, especificacoes)
                VALUES (:sku, :nome, :categoria, :marca, :descricao, :preco, :desc_max,
                        CAST(:specs AS jsonb))
                ON CONFLICT (sku) DO UPDATE SET
                    nome = EXCLUDED.nome, categoria = EXCLUDED.categoria, marca = EXCLUDED.marca,
                    descricao = EXCLUDED.descricao, preco_centavos = EXCLUDED.preco_centavos,
                    desconto_max_pct = EXCLUDED.desconto_max_pct,
                    especificacoes = EXCLUDED.especificacoes, atualizado_em = now()
                RETURNING id
                """
            ),
            {
                "sku": p["sku"],
                "nome": p["nome"],
                "categoria": p["categoria"],
                "marca": p["marca"],
                "descricao": p["descricao"],
                "preco": p["preco_centavos"],
                "desc_max": p["desconto_max_pct"],
                "specs": json.dumps(p["especificacoes"], ensure_ascii=False),
            },
        ).scalar_one()
        produtos += 1
        for v in p["variantes"]:
            res = conn.execute(
                text(
                    """
                    INSERT INTO estoque (produto_id, sku_variante, switch, layout, cor, quantidade)
                    VALUES (:pid, :skuv, :switch, :layout, :cor, :qtd)
                    ON CONFLICT (sku_variante) DO NOTHING
                    """
                ),
                {
                    "pid": produto_id,
                    "skuv": sku_variante(p["sku"], v),
                    "switch": v.get("switch"),
                    "layout": v.get("layout"),
                    "cor": v.get("cor"),
                    "qtd": v["quantidade"],
                },
            )
            variantes_novas += res.rowcount
    return {"produtos": produtos, "variantes_novas": variantes_novas}
