"""Preço de kit com desconto progressivo.

Faixa de desconto pelo número de PRODUTOS DIFERENTES no kit (comprar 5 mousepads iguais não é
um kit). Cada linha recebe min(desconto da faixa, desconto_max_pct do produto). Todo cálculo é
em centavos inteiros com arredondamento HALF_UP por linha.
"""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.catalog import brl


class ItemInvalido(ValueError):
    pass


def normalizar_itens(itens: list[dict], max_qtd: int) -> list[dict]:
    """Junta linhas repetidas da mesma variante e valida quantidades."""
    agregados: dict[str, int] = {}
    for it in itens:
        skuv = str(it.get("sku_variante", "")).strip()
        qtd = int(it.get("quantidade", 1))
        if not skuv:
            raise ItemInvalido("Item sem sku_variante.")
        if qtd < 1:
            raise ItemInvalido(f"Quantidade inválida para {skuv}.")
        agregados[skuv] = agregados.get(skuv, 0) + qtd
    if not agregados:
        raise ItemInvalido("O kit precisa de pelo menos um item.")
    for skuv, qtd in agregados.items():
        if qtd > max_qtd:
            raise ItemInvalido(f"Máximo de {max_qtd} unidades por item ({skuv}).")
    return [{"sku_variante": k, "quantidade": v} for k, v in agregados.items()]


def faixa_para(n_produtos: int, tiers: list[tuple[int, Decimal]]) -> Decimal:
    pct = Decimal(0)
    for minimo, desconto in tiers:
        if n_produtos >= minimo:
            pct = desconto
    return pct


def cotar(conn: Connection, itens: list[dict], tiers, max_qtd: int) -> dict:
    itens = normalizar_itens(itens, max_qtd)
    rows = conn.execute(
        text(
            """
            SELECT e.id AS estoque_id, e.sku_variante, e.switch, e.layout, e.cor, e.quantidade AS em_estoque,
                   p.id AS produto_id, p.sku, p.nome, p.preco_centavos, p.desconto_max_pct
            FROM estoque e JOIN produtos p ON p.id = e.produto_id
            WHERE e.sku_variante = ANY(CAST(:skus AS text[])) AND p.ativo
            """
        ),
        {"skus": [i["sku_variante"] for i in itens]},
    ).all()
    por_sku = {r.sku_variante: r for r in rows}
    faltando = [i["sku_variante"] for i in itens if i["sku_variante"] not in por_sku]
    if faltando:
        raise ItemInvalido(f"Variante(s) inexistente(s): {', '.join(faltando)}. Use o sku_variante do catálogo.")

    n_produtos = len({por_sku[i["sku_variante"]].produto_id for i in itens})
    pct_faixa = faixa_para(n_produtos, tiers)

    linhas = []
    subtotal = desconto_total = 0
    for i in itens:
        r = por_sku[i["sku_variante"]]
        pct = min(pct_faixa, Decimal(r.desconto_max_pct))
        bruto = r.preco_centavos * i["quantidade"]
        desconto = int((Decimal(bruto) * pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        subtotal += bruto
        desconto_total += desconto
        variante = " / ".join(x for x in (r.switch, r.layout, r.cor) if x)
        linhas.append(
            {
                "sku_variante": r.sku_variante,
                "estoque_id": r.estoque_id,
                "produto_id": r.produto_id,
                "sku": r.sku,
                "nome": r.nome,
                "variante": variante,
                "quantidade": i["quantidade"],
                "preco_unit_centavos": r.preco_centavos,
                "preco_unit": brl(r.preco_centavos),
                "desconto_pct": float(pct),
                "total_centavos": bruto - desconto,
                "total": brl(bruto - desconto),
                "estoque_suficiente_agora": r.em_estoque >= i["quantidade"],
            }
        )

    proxima = next(((m, d) for m, d in tiers if m > n_produtos), None)
    total = subtotal - desconto_total
    return {
        "itens": linhas,
        "produtos_diferentes": n_produtos,
        "desconto_faixa_pct": float(pct_faixa),
        "subtotal_centavos": subtotal,
        "desconto_centavos": desconto_total,
        "total_centavos": total,
        "subtotal": brl(subtotal),
        "desconto": brl(desconto_total),
        "total": brl(total),
        "desconto_efetivo_pct": round(desconto_total * 100 / subtotal, 2) if subtotal else 0.0,
        "proxima_faixa": (
            {"produtos_diferentes": proxima[0], "desconto_pct": float(proxima[1])} if proxima else None
        ),
        "observacao": (
            "Alguns produtos têm desconto limitado abaixo da faixa (política do fabricante)."
            if any(Decimal(str(l["desconto_pct"])) < pct_faixa for l in linhas)
            else None
        ),
    }
