"""Consumidor da fila "orders".

Decisão por mensagem:
  * processou (CONFIRMADO/REJEITADO) ou já estava processado (IGNORADO) -> apaga a mensagem;
  * erro transitório (banco fora, deadlock, timeout) -> NÃO apaga; volta após a visibility timeout;
  * mensagem inválida ou pedido inexistente -> NÃO apaga; após maxReceiveCount=5 vai para a DLQ,
    onde fica para investigação.
"""
import json
import logging
import threading
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import DBAPIError, OperationalError

from app.logs import correlation_id_var
from app.reserva import PedidoNaoEncontrado, processar_pedido

log = logging.getLogger("worker.consumer")


class MensagemInvalida(Exception):
    pass


def make_sqs_client(settings):
    return boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_url,
        config=Config(
            connect_timeout=5,
            read_timeout=settings.sqs_wait_time_s + 10,  # maior que o long polling
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _parse(msg: dict) -> tuple[str, str | None]:
    try:
        body = json.loads(msg["Body"])
        pedido_id = str(uuid.UUID(str(body["pedido_id"])))
    except (KeyError, ValueError, TypeError) as exc:
        raise MensagemInvalida(str(exc)) from exc
    cid = body.get("correlation_id") or (
        msg.get("MessageAttributes", {}).get("correlation_id", {}).get("StringValue")
    )
    return pedido_id, cid


def handle_message(engine, sqs, queue_url: str, msg: dict) -> str:
    """Processa uma mensagem. Devolve 'apagada' ou 'mantida'."""
    receive_count = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", 1))
    inicio = time.perf_counter()
    try:
        pedido_id, cid = _parse(msg)
    except MensagemInvalida as exc:
        log.error(
            "mensagem inválida; ficará na fila até ir para a DLQ",
            extra={"event": "poison_message", "message_id": msg.get("MessageId"), "erro": str(exc),
                   "receive_count": receive_count},
        )
        return "mantida"

    token = correlation_id_var.set(cid)
    try:
        try:
            resultado = processar_pedido(engine, pedido_id)
        except PedidoNaoEncontrado:
            log.error(
                "pedido da mensagem não existe; ficará na fila até ir para a DLQ",
                extra={"event": "order_not_found", "pedido_id": pedido_id, "receive_count": receive_count},
            )
            return "mantida"
        except (OperationalError, DBAPIError) as exc:
            log.warning(
                "erro transitório; mensagem volta após a visibility timeout",
                extra={"event": "order_transient_error", "pedido_id": pedido_id, "erro": exc.__class__.__name__,
                       "receive_count": receive_count},
            )
            return "mantida"

        log.info(
            f"pedido {resultado.acao.lower()}",
            extra={
                "event": "order_processed",
                "pedido_id": pedido_id,
                "resultado": resultado.acao,
                "status_final": resultado.status_final,
                "motivo": resultado.motivo,
                "receive_count": receive_count,
                "duration_ms": round((time.perf_counter() - inicio) * 1000, 1),
            },
        )
        try:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
        except (BotoCoreError, ClientError) as exc:
            # Sem problema: a mensagem volta e será só apagada (pedido já não está PENDENTE).
            log.warning("falha ao apagar mensagem", extra={"event": "sqs_delete_failed", "erro": str(exc)})
            return "mantida"
        return "apagada"
    finally:
        correlation_id_var.reset(token)


class Worker(threading.Thread):
    def __init__(self, engine, settings, sqs=None):
        super().__init__(name="order-consumer", daemon=True)
        self.engine = engine
        self.settings = settings
        self.sqs = sqs or make_sqs_client(settings)
        self.stop_event = threading.Event()
        self.heartbeat = time.monotonic()
        self.processadas = 0
        self.queue_url: str | None = settings.sqs_queue_url

    def _resolver_fila(self) -> str:
        # Na AWS usamos SQS_QUEUE_URL; localmente basta o nome (ElasticMQ resolve a URL).
        if not self.queue_url:
            self.queue_url = self.sqs.get_queue_url(QueueName=self.settings.sqs_queue_name)["QueueUrl"]
            log.info("fila resolvida pelo nome", extra={"queue_url": self.queue_url})
        return self.queue_url

    def run(self):
        log.info("consumidor iniciado",
                 extra={"queue_url": self.settings.sqs_queue_url, "queue_name": self.settings.sqs_queue_name})
        backoff = 1.0
        while not self.stop_event.is_set():
            self.heartbeat = time.monotonic()
            try:
                queue_url = self._resolver_fila()
                resp = self.sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=self.settings.sqs_max_messages,
                    WaitTimeSeconds=self.settings.sqs_wait_time_s,
                    MessageAttributeNames=["All"],
                    AttributeNames=["ApproximateReceiveCount"],
                )
                backoff = 1.0
            except (BotoCoreError, ClientError) as exc:
                log.warning("falha ao ler a fila", extra={"event": "sqs_receive_failed", "erro": str(exc)})
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            for msg in resp.get("Messages", []):
                if handle_message(self.engine, self.sqs, queue_url, msg) == "apagada":
                    self.processadas += 1
        log.info("consumidor parado")

    def stop(self, timeout: float = 25):
        self.stop_event.set()
        self.join(timeout)

    def saudavel(self) -> tuple[bool, float]:
        idade = time.monotonic() - self.heartbeat
        return self.is_alive() and idade < self.settings.health_max_loop_age_s, round(idade, 1)
