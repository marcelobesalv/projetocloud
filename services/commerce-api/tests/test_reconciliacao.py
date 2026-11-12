"""Reconciliação: pedidos PENDENTE cujo evento se perdeu (DLQ, fila purgada) voltam para a fila."""
import uuid

from sqlalchemy import text

from app.outbox import publicar_lote
from app.reconciliacao import LOCK_ID, reconciliar

IDADE = 600  # s; os pedidos "antigos" dos testes são envelhecidos para 1 hora


class FakeSQS:
    def send_message_batch(self, QueueUrl, Entries):
        return {"Successful": [{"Id": e["Id"]} for e in Entries], "Failed": []}


def _publicar_tudo(engine):
    while publicar_lote(engine, FakeSQS(), "http://fila"):
        pass


def _pedido(client, produto_teste, cid=None):
    _, variantes = produto_teste()
    headers = {"Idempotency-Key": f"teste-{uuid.uuid4()}"}
    if cid:
        headers["X-Correlation-ID"] = cid
    r = client.post("/orders", json={"cliente_nome": "Rita", "itens": [{"sku_variante": variantes[0]}]}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["pedido_id"]


def _envelhecer(engine, pedido_id):
    """Simula que o pedido e as publicações dele aconteceram há 1 hora."""
    with engine.begin() as c:
        c.execute(text("UPDATE pedidos SET criado_em = now() - interval '1 hour' WHERE id = CAST(:id AS uuid)"),
                  {"id": pedido_id})
        c.execute(
            text("""UPDATE outbox SET criado_em = now() - interval '1 hour', publicado_em = now() - interval '1 hour'
                    WHERE aggregate_id = :id AND publicado_em IS NOT NULL"""),
            {"id": pedido_id},
        )


def _eventos(engine, pedido_id):
    with engine.connect() as c:
        return c.execute(
            text("SELECT payload, publicado_em FROM outbox WHERE aggregate_id = :id ORDER BY id"), {"id": pedido_id}
        ).all()


def test_pedido_parado_e_reenviado_uma_vez_por_janela(client, engine, produto_teste):
    pedido_id = _pedido(client, produto_teste, cid="cid-reconc-1")
    _publicar_tudo(engine)
    _envelhecer(engine, pedido_id)

    resumo = reconciliar(engine, IDADE, max_reenvios=3)
    assert resumo["reenviados"] >= 1
    eventos = _eventos(engine, pedido_id)
    assert len(eventos) == 2
    novo = eventos[1].payload
    assert novo["pedido_id"] == pedido_id and novo["reenvio"] == 1
    assert novo["correlation_id"] == "cid-reconc-1"  # rastreável como o pedido original

    # Evento ainda não publicado: o próximo ciclo não duplica.
    reconciliar(engine, IDADE, max_reenvios=3)
    assert len(_eventos(engine, pedido_id)) == 2

    # Publicado agora: dentro da janela, não reenvia de novo.
    _publicar_tudo(engine)
    reconciliar(engine, IDADE, max_reenvios=3)
    assert len(_eventos(engine, pedido_id)) == 2


def test_pedido_recente_ou_ja_processado_nao_e_reenviado(client, engine, produto_teste):
    recente = _pedido(client, produto_teste)
    processado = _pedido(client, produto_teste)
    _publicar_tudo(engine)
    _envelhecer(engine, processado)
    with engine.begin() as c:
        c.execute(text("UPDATE pedidos SET status = 'CONFIRMADO' WHERE id = CAST(:id AS uuid)"), {"id": processado})

    reconciliar(engine, IDADE, max_reenvios=3)
    assert len(_eventos(engine, recente)) == 1
    assert len(_eventos(engine, processado)) == 1


def test_pedido_travado_para_de_ser_reenviado_e_e_contado(client, engine, produto_teste):
    pedido_id = _pedido(client, produto_teste)
    _publicar_tudo(engine)
    for _ in range(2):  # max_reenvios=2: dois reenvios, cada um publicado e "esquecido" há 1 hora
        _envelhecer(engine, pedido_id)
        reconciliar(engine, IDADE, max_reenvios=2)
        _publicar_tudo(engine)
    _envelhecer(engine, pedido_id)
    assert len(_eventos(engine, pedido_id)) == 3

    resumo = reconciliar(engine, IDADE, max_reenvios=2)
    assert len(_eventos(engine, pedido_id)) == 3  # não reenvia mais
    assert resumo["travados"] >= 1


def test_so_uma_task_reconcilia_por_vez(engine):
    with engine.connect() as outra_task:
        outra_task.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_ID})
        try:
            assert reconciliar(engine, IDADE, max_reenvios=3) is None
        finally:
            outra_task.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})
    assert reconciliar(engine, IDADE, max_reenvios=3) is not None
