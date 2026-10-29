"""Consultas de catálogo e estoque."""
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

CATEGORIAS = ("teclado", "mouse", "headset", "monitor", "webcam", "acessorio")


def brl(centavos: int) -> str:
    reais, cent = divmod(int(centavos), 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


_PRODUTO_COLS = """
    p.id, p.sku, p.nome, p.categoria, p.marca, p.descricao, p.preco_centavos,
    p.especificacoes,
    COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
                   'sku_variante', e.sku_variante, 'switch', e.switch, 'layout', e.layout,
                   'cor', e.cor, 'quantidade', e.quantidade) ORDER BY e.id)
        FROM estoque e WHERE e.produto_id = p.id), '[]'::jsonb) AS variantes
"""


def _produto(row) -> dict:
    variantes = [{k: v for k, v in var.items() if v is not None} for var in row.variantes]
    for v in variantes:
        v["disponivel"] = v["quantidade"] > 0
    return {
        "sku": row.sku,
        "nome": row.nome,
        "categoria": row.categoria,
        "marca": row.marca,
        "descricao": row.descricao,
        "preco": brl(row.preco_centavos),
        "preco_centavos": row.preco_centavos,
        "especificacoes": row.especificacoes,
        "em_estoque": any(v["disponivel"] for v in variantes),
        "variantes": variantes,
    }


def buscar(
    conn: Connection,
    q: str | None = None,
    categoria: str | None = None,
    preco_max_centavos: int | None = None,
    conexao: str | None = None,
    so: str | None = None,
    somente_em_estoque: bool = False,
    limite: int = 10,
) -> list[dict]:
    where = ["p.ativo"]
    params: dict = {"limite": max(1, min(limite, 30))}
    if categoria:
        where.append("p.categoria = :categoria")
        params["categoria"] = categoria
    if preco_max_centavos:
        where.append("p.preco_centavos <= :preco_max")
        params["preco_max"] = preco_max_centavos
    if conexao:
        where.append("p.especificacoes @> CAST(:f_conexao AS jsonb)")
        params["f_conexao"] = json.dumps({"conexoes": [conexao]})
    if so:
        where.append("p.especificacoes @> CAST(:f_so AS jsonb)")
        params["f_so"] = json.dumps({"so_suportados": [so]})
    if somente_em_estoque:
        where.append("EXISTS (SELECT 1 FROM estoque e WHERE e.produto_id = p.id AND e.quantidade > 0)")
    for i, palavra in enumerate((q or "").split()[:6]):
        where.append(
            f"(p.nome || ' ' || p.marca || ' ' || p.descricao || ' ' || p.categoria) ILIKE :w{i}"
        )
        params[f"w{i}"] = f"%{palavra}%"
    sql = f"SELECT {_PRODUTO_COLS} FROM produtos p WHERE {' AND '.join(where)} ORDER BY p.categoria, p.preco_centavos LIMIT :limite"
    return [_produto(r) for r in conn.execute(text(sql), params)]


def por_skus(conn: Connection, skus: list[str]) -> list[dict]:
    """Aceita SKU de produto ou de variante; devolve produtos na ordem pedida (sem duplicar)."""
    if not skus:
        return []
    rows = conn.execute(
        text(
            f"""
            SELECT DISTINCT ON (p.id) {_PRODUTO_COLS}, s.sku_pedido
            FROM unnest(CAST(:skus AS text[])) AS s(sku_pedido)
            JOIN produtos p
              ON p.sku = s.sku_pedido
              OR p.id = (SELECT produto_id FROM estoque WHERE sku_variante = s.sku_pedido)
            WHERE p.ativo
            """
        ),
        {"skus": skus},
    ).all()
    found = {}
    for r in rows:
        found[r.sku] = _produto(r)
        found[r.sku]["_pedido"] = r.sku_pedido
    ordem = {s: i for i, s in enumerate(skus)}
    produtos = sorted(found.values(), key=lambda p: ordem.get(p["_pedido"], 0))
    for p in produtos:
        p.pop("_pedido")
    return produtos


def estoque(conn: Connection, sku: str) -> dict | None:
    """Estoque atual de um produto (todas as variantes) ou de uma variante específica."""
    produtos = por_skus(conn, [sku])
    if not produtos:
        return None
    p = produtos[0]
    variantes = p["variantes"]
    if sku != p["sku"]:
        variantes = [v for v in variantes if v["sku_variante"] == sku]
    return {
        "sku": p["sku"],
        "nome": p["nome"],
        "preco": p["preco"],
        "variantes": variantes,
        "total_disponivel": sum(v["quantidade"] for v in variantes),
    }
