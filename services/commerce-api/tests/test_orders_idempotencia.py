import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text


def _body(variantes, nome="Ana Souza"):
    return {"cliente_nome": nome, "itens": [{"sku_variante": v, "quantidade": 1} for v in variantes]}


def _contar(engine, key):
    with engine.connect() as c:
        pedidos = c.execute(text("SELECT count(*) FROM pedidos WHERE idempotency_key=:k"), {"k": key}).scalar_one()
        outbox = c.execute(
            text(
                "SELECT count(*) FROM outbox o JOIN pedidos p ON p.id::text = o.aggregate_id "
                "WHERE p.idempotency_key=:k"
            ),
            {"k": key},
        ).scalar_one()
    return pedidos, outbox


def test_mesma_chave_devolve_o_mesmo_pedido(client, engine, produto_teste):
    _, variantes = produto_teste()
    key = f"teste-{uuid.uuid4()}"

    r1 = client.post("/orders", json=_body(variantes), headers={"Idempotency-Key": key})
    r2 = client.post("/orders", json=_body(variantes), headers={"Idempotency-Key": key})

    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r2.headers["Idempotent-Replay"] == "true"
    assert r1.json()["pedido_id"] == r2.json()["pedido_id"]
    assert r1.json()["status"] == "PENDENTE"
    assert _contar(engine, key) == (1, 1)  # um pedido e um evento no outbox


def test_chave_reutilizada_com_outro_conteudo_da_409(client, engine, produto_teste):
    _, variantes = produto_teste()
    key = f"teste-{uuid.uuid4()}"
    assert client.post("/orders", json=_body(variantes), headers={"Idempotency-Key": key}).status_code == 201
    r = client.post("/orders", json=_body(variantes[:1]), headers={"Idempotency-Key": key})
    assert r.status_code == 409
    assert _contar(engine, key) == (1, 1)


def test_requisicoes_concorrentes_com_mesma_chave_criam_um_pedido(client, engine, produto_teste):
    _, variantes = produto_teste()
    key = f"teste-{uuid.uuid4()}"

    def post(_):
        return client.post("/orders", json=_body(variantes), headers={"Idempotency-Key": key})

    with ThreadPoolExecutor(max_workers=8) as ex:
        respostas = list(ex.map(post, range(8)))

    assert sorted(r.status_code for r in respostas) == [200] * 7 + [201]
    assert len({r.json()["pedido_id"] for r in respostas}) == 1
    assert _contar(engine, key) == (1, 1)


def test_sem_idempotency_key_da_400(client, produto_teste):
    _, variantes = produto_teste()
    assert client.post("/orders", json=_body(variantes)).status_code == 400


def test_variante_inexistente_da_422_e_nao_grava(client, engine):
    key = f"teste-{uuid.uuid4()}"
    r = client.post("/orders", json=_body(["NAO-EXISTE-123"]), headers={"Idempotency-Key": key})
    assert r.status_code == 422
    assert _contar(engine, key) == (0, 0)


def test_preco_do_pedido_e_calculado_no_servidor(client, produto_teste):
    _, v1 = produto_teste(preco=10000, desconto_max=50)
    _, v2 = produto_teste(preco=20000, desconto_max=3)
    r = client.post(
        "/orders",
        json={"cliente_nome": "Bia", "itens": [{"sku_variante": v1[0]}, {"sku_variante": v2[0]}]},
        headers={"Idempotency-Key": f"teste-{uuid.uuid4()}"},
    )
    assert r.status_code == 201
    p = r.json()
    # 2 produtos -> faixa 5%; o segundo é limitado a 3%: 500 + 600 = 1100 centavos de desconto
    assert p["total_centavos"] == 30000 - 1100
