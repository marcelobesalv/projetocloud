"""Publicador do outbox: lê eventos pendentes e envia para a fila SQS "orders".

Entrega "pelo menos uma vez": se o processo cair entre o envio e o commit, a mensagem pode
ser publicada de novo — o order-worker é idempotente. FOR UPDATE SKIP LOCKED permite rodar
várias tasks do commerce-api ao mesmo tempo sem publicar o mesmo evento em paralelo.
"""
import json
import logging
import threading
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.logs import correlation_id_var

log = logging.getLogger("commerce.outbox")


def make_sqs_client(settings):
    return boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_url,
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 3, "mode": "standard"}),
    )


def publicar_lote(engine: Engine, sqs, queue_url: str, batch_size: int = 10) -> int:
    """Publica até batch_size eventos. Devolve quantos foram publicados."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, payload FROM outbox
                WHERE publicado_em IS NULL
                ORDER BY id
                LIMIT :n
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"n": batch_size},
        ).all()
        if not rows:
            return 0

        entries = []
        for r in rows:
            cid = r.payload.get("correlation_id") or ""
            entry = {"Id": str(r.id), "MessageBody": json.dumps(r.payload)}
            if cid:
                entry["MessageAttributes"] = {"correlation_id": {"DataType": "String", "StringValue": cid}}
            entries.append(entry)

        try:
            resp = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
        except (BotoCoreError, ClientError) as exc:
            conn.execute(
                text("UPDATE outbox SET tentativas = tentativas + 1, ultimo_erro = :e WHERE id = ANY(:ids)"),
                {"e": str(exc)[:500], "ids": [r.id for r in rows]},
            )
            log.warning("falha ao publicar no SQS", extra={"event": "outbox_publish_failed", "erro": str(exc)})
            return 0

        ok_ids = [int(s["Id"]) for s in resp.get("Successful", [])]
        if ok_ids:
            conn.execute(
                text("UPDATE outbox SET publicado_em = now(), tentativas = tentativas + 1 WHERE id = ANY(:ids)"),
                {"ids": ok_ids},
            )
        for f in resp.get("Failed", []):
            conn.execute(
                text("UPDATE outbox SET tentativas = tentativas + 1, ultimo_erro = :e WHERE id = :id"),
                {"e": f"{f.get('Code')}: {f.get('Message')}"[:500], "id": int(f["Id"])},
            )
        payload_por_id = {r.id: r.payload for r in rows}
        for i in ok_ids:
            p = payload_por_id[i]
            log.info(
                "evento publicado na fila",
                extra={
                    "event": "outbox_published",
                    "outbox_id": i,
                    "pedido_id": p.get("pedido_id"),
                    "correlation_id": p.get("correlation_id"),
                },
            )
        return len(ok_ids)


class OutboxPublisher(threading.Thread):
    def __init__(self, engine: Engine, settings, sqs=None):
        super().__init__(name="outbox-publisher", daemon=True)
        self.engine = engine
        self.settings = settings
        self.sqs = sqs or make_sqs_client(settings)
        self.stop_event = threading.Event()
        self.ultimo_ciclo: datetime | None = None
        self.ultimo_erro: str | None = None
        self.queue_url: str | None = settings.sqs_queue_url

    def _resolver_fila(self) -> str:
        # Na AWS usamos SQS_QUEUE_URL; localmente basta o nome (ElasticMQ resolve a URL).
        if not self.queue_url:
            self.queue_url = self.sqs.get_queue_url(QueueName=self.settings.sqs_queue_name)["QueueUrl"]
            log.info("fila resolvida pelo nome", extra={"queue_url": self.queue_url})
        return self.queue_url

    def run(self):
        log.info("publicador do outbox iniciado",
                 extra={"queue_url": self.settings.sqs_queue_url, "queue_name": self.settings.sqs_queue_name})
        backoff = self.settings.outbox_poll_interval_s
        while not self.stop_event.is_set():
            correlation_id_var.set(None)
            try:
                n = publicar_lote(self.engine, self.sqs, self._resolver_fila(), self.settings.outbox_batch_size)
                self.ultimo_ciclo = datetime.now(timezone.utc)
                self.ultimo_erro = None
                backoff = self.settings.outbox_poll_interval_s
                if n == self.settings.outbox_batch_size:
                    continue  # ainda pode haver mais; não espera
            except Exception as exc:  # banco fora do ar etc.: tenta de novo com backoff
                self.ultimo_erro = str(exc)[:300]
                log.exception("erro no ciclo do outbox", extra={"event": "outbox_cycle_error"})
                backoff = min(backoff * 2, 30)
            self.stop_event.wait(backoff)
        log.info("publicador do outbox parado")

    def stop(self, timeout: float = 10):
        self.stop_event.set()
        self.join(timeout)

    def status(self) -> dict:
        return {
            "ativo": self.is_alive(),
            "ultimo_ciclo": self.ultimo_ciclo.isoformat() if self.ultimo_ciclo else None,
            "ultimo_erro": self.ultimo_erro,
        }


def pendentes(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM outbox WHERE publicado_em IS NULL")).scalar_one()

