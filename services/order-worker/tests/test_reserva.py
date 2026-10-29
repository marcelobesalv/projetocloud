from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.reserva import processar_pedido
from app.worker import handle_message
from conftest import ADMIN_URL, FakeSQS, estoque_de, mensagem, pedido_de


def test_confirma_e_baixa_todos_os_itens(engine, variante, pedido):
    a, b = variante(5), variante(3)
    pid = pedido([(a, 2), (b, 3)])

    r = processar_pedido(engine, pid)

    assert r.acao == "CONFIRMADO"
    assert pedido_de(engine, pid).status == "CONFIRMADO"
    assert (estoque_de(engine, a), estoque_de(engine, b)) == (3, 0)


def test_estoque_insuficiente_em_um_item_rejeita_e_nao_reserva_nada(engine, variante, pedido):
    a, b, c = variante(10), variante(1), variante(10)
    pid = pedido([(a, 2), (b, 2), (c, 1)])  # b só tem 1

    r = processar_pedido(engine, pid)

    assert r.acao == "REJEITADO"
    status, motivo = pedido_de(engine, pid)
    assert status == "REJEITADO"
    assert "pedido 2, disponível 1" in motivo
    # Nada foi reservado — nem os itens que tinham estoque.
    assert (estoque_de(engine, a), estoque_de(engine, b), estoque_de(engine, c)) == (10, 1, 10)


def test_reentrega_da_mesma_mensagem_so_apaga(engine, variante, pedido):
    a = variante(5)
    pid = pedido([(a, 2)])
    sqs = FakeSQS()

    assert handle_message(engine, sqs, "fila", mensagem(pid, receipt="rh-1")) == "apagada"
    assert handle_message(engine, sqs, "fila", mensagem(pid, receipt="rh-2", receive_count=2)) == "apagada"

    assert estoque_de(engine, a) == 3  # baixou uma vez só
    assert pedido_de(engine, pid).status == "CONFIRMADO"
    assert sqs.apagadas == ["rh-1", "rh-2"]


def test_reentrega_de_pedido_rejeitado_nao_muda_nada(engine, variante, pedido):
    a = variante(1)
    pid = pedido([(a, 3)])
    sqs = FakeSQS()
    handle_message(engine, sqs, "fila", mensagem(pid))
    motivo = pedido_de(engine, pid).motivo_rejeicao
    variante_extra = a  # mesmo com estoque reposto, um REJEITADO não volta a ser processado
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE estoque SET quantidade = 50 WHERE id = %s", (variante_extra,))
    assert handle_message(engine, sqs, "fila", mensagem(pid, receipt="rh-2")) == "apagada"
    assert pedido_de(engine, pid) == ("REJEITADO", motivo)
    assert estoque_de(engine, a) == 50


def test_entregas_duplicadas_simultaneas_baixam_uma_vez(engine, variante, pedido):
    a = variante(10)
    pid = pedido([(a, 4)])
    with ThreadPoolExecutor(max_workers=6) as ex:
        resultados = list(ex.map(lambda _: processar_pedido(engine, pid).acao, range(6)))
    assert sorted(resultados) == ["CONFIRMADO"] + ["IGNORADO"] * 5
    assert estoque_de(engine, a) == 6


def test_pedidos_concorrentes_pela_ultima_unidade(engine, variante, pedido):
    ultima = variante(1)
    p1, p2 = pedido([(ultima, 1)]), pedido([(ultima, 1)])
    with ThreadPoolExecutor(max_workers=2) as ex:
        resultados = sorted(ex.map(lambda p: processar_pedido(engine, p).acao, [p1, p2]))
    assert resultados == ["CONFIRMADO", "REJEITADO"]
    assert estoque_de(engine, ultima) == 0


def test_erro_transitorio_nao_apaga_a_mensagem(variante, pedido):
    pid = pedido([(variante(5), 1)])
    banco_fora = create_engine(
        make_url(ADMIN_URL).set(port=1, database="x"), connect_args={"connect_timeout": 1}
    )
    sqs = FakeSQS()
    assert handle_message(banco_fora, sqs, "fila", mensagem(pid)) == "mantida"
    assert sqs.apagadas == []


def test_mensagem_invalida_fica_para_a_dlq(engine):
    sqs = FakeSQS()
    msg = {"MessageId": "x", "ReceiptHandle": "rh", "Body": "isto não é json"}
    assert handle_message(engine, sqs, "fila", msg) == "mantida"
    assert sqs.apagadas == []
