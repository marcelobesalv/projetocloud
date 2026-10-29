import json
import uuid

from sqlalchemy import text

from app.outbox import publicar_lote


class FakeSQS:
    def __init__(self, falhar=False):
        self.enviadas = []
        self.falhar = falhar

    def send_message_batch(self, QueueUrl, Entries):
        if self.falhar:
            from botocore.exceptions import EndpointConnectionError

            raise EndpointConnectionError(endpoint_url=QueueUrl)
        self.enviadas.extend(Entries)
        return {"Successful": [{"Id": e["Id"]} for e in Entries], "Failed": []}


def test_desconto_progressivo_limitado_por_produto(client, produto_teste):
    skus = [produto_teste(preco=10000, desconto_max=d)[1][0] for d in (20, 20, 20, 6)]
    r = client.post("/kits/quote", json={"itens": [{"sku_variante": s} for s in skus]})
    body = r.json()
    assert body["desconto_faixa_pct"] == 10  # 4 produtos diferentes
    assert [i["desconto_pct"] for i in body["itens"]] == [10, 10, 10, 6]
    assert body["desconto_centavos"] == 1000 * 3 + 600
    assert body["proxima_faixa"] == {"produtos_diferentes": 5, "desconto_pct": 12.0}


def test_quantidade_do_mesmo_produto_nao_sobe_faixa(client, produto_teste):
    _, variantes = produto_teste(preco=10000, desconto_max=20)
    r = client.post("/kits/quote", json={"itens": [{"sku_variante": v, "quantidade": 3} for v in variantes]})
    assert r.json()["produtos_diferentes"] == 1
    assert r.json()["desconto_centavos"] == 0


def test_outbox_publica_e_marca(client, engine, produto_teste):
    while publicar_lote(engine, FakeSQS(), "http://fila"):  # esvazia pendências de outros testes
        pass
    _, variantes = produto_teste()
    pedido = client.post(
        "/orders",
        json={"cliente_nome": "Caio", "itens": [{"sku_variante": variantes[0]}]},
        headers={"Idempotency-Key": f"teste-{uuid.uuid4()}", "X-Correlation-ID": "cid-outbox-1"},
    ).json()

    # Fila fora do ar: nada é marcado como publicado, tentativas sobe.
    assert publicar_lote(engine, FakeSQS(falhar=True), "http://fila") == 0

    sqs = FakeSQS()
    while publicar_lote(engine, sqs, "http://fila"):
        pass

    corpos = [json.loads(e["MessageBody"]) for e in sqs.enviadas]
    meu = next(c for c in corpos if c["pedido_id"] == pedido["pedido_id"])
    assert meu["correlation_id"] == "cid-outbox-1"
    with engine.connect() as c:
        row = c.execute(
            text("SELECT publicado_em, tentativas FROM outbox WHERE aggregate_id=:id"), {"id": pedido["pedido_id"]}
        ).one()
    assert row.publicado_em is not None
    assert row.tentativas == 2
