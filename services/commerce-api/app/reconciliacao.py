"""Reconciliação de pedidos PENDENTE antigos.

O outbox garante que o evento de um pedido chega à fila, mas não que ele seja processado: a mensagem
pode ir para a DLQ por um erro transitório prolongado (banco fora por mais que 5 × visibility timeout),
a fila pode ser purgada, etc. Sem reconciliação o pedido ficaria PENDENTE para sempre.

A cada ciclo, para cada pedido PENDENTE criado há mais de `idade_s` cujo último evento foi publicado há
mais de `idade_s` (e que não tem evento esperando o publicador), grava de novo o evento PedidoCriado no
outbox. O publicador envia normalmente; o worker é idempotente (trava o pedido e ignora o que não está
PENDENTE), então um reenvio desnecessário não causa efeito.

Depois de `max_reenvios` reenvios o pedido é considerado **travado**: não é mais reenviado (a causa é
permanente e reenviar só geraria um loop) e entra na contagem `travados` do log `reconciliation_cycle`,
que vira a métrica OrdersStuck com alarme.

Várias tasks do commerce-api rodam o job; um advisory lock de transação faz só uma delas executar o
ciclo por vez. (O usuário do commerce não tem UPDATE em pedidos, então FOR UPDATE não é opção.)
"""
import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.logs import correlation_id_var

log = logging.getLogger("commerce.reconciliacao")

LOCK_ID = 7_310_001  # chave do advisory lock do ciclo de reconciliação

# Pedidos PENDENTE antigos com os seus eventos de outbox.
_RESUMO = """
    SELECT p.id::text AS pedido_id, p.correlation_id, p.criado_em,
           count(o.id) AS eventos,
           bool_or(o.id IS NOT NULL AND o.publicado_em IS NULL) AS aguardando_publicador,
           max(o.publicado_em) AS ultima_publicacao
    FROM pedidos p
    LEFT JOIN outbox o ON o.aggregate_type = 'pedido' AND o.aggregate_id = p.id::text
    WHERE p.status = 'PENDENTE' AND p.criado_em < now() - make_interval(secs => :idade)
    GROUP BY p.id
"""
# Sem evento pendente no outbox e sem publicação recente (o último envio teve tempo de ser processado).
_PARADO = """
    NOT aguardando_publicador
    AND (ultima_publicacao IS NULL OR ultima_publicacao < now() - make_interval(secs => :idade))
"""


def reconciliar(engine: Engine, idade_s: float, max_reenvios: int, limite: int = 100) -> dict | None:
    """Executa um ciclo. Devolve o resumo, ou None se outra task já está reconciliando."""
    with engine.begin() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_ID}).scalar_one():
            return None
        params = {"idade": idade_s, "max_eventos": max_reenvios + 1, "lim": limite}  # eventos = 1 original + reenvios

        candidatos = conn.execute(
            text(f"SELECT * FROM ({_RESUMO}) r WHERE {_PARADO} AND eventos < :max_eventos ORDER BY criado_em LIMIT :lim"),
            params,
        ).all()
        travados = conn.execute(
            text(f"SELECT count(*) FROM ({_RESUMO}) r WHERE {_PARADO} AND eventos >= :max_eventos"), params
        ).scalar_one()

        for c in candidatos:
            reenvio = max(c.eventos, 1)  # o evento original conta como o primeiro
            payload = {
                "evento": "PedidoCriado",
                "versao": 1,
                "pedido_id": c.pedido_id,
                "correlation_id": c.correlation_id,
                "reenvio": reenvio,
            }
            conn.execute(
                text(
                    """
                    INSERT INTO outbox (aggregate_type, aggregate_id, tipo_evento, payload)
                    VALUES ('pedido', :aid, 'PedidoCriado', CAST(:payload AS jsonb))
                    """
                ),
                {"aid": c.pedido_id, "payload": json.dumps(payload)},
            )
            log.warning(
                "pedido PENDENTE parado; evento reenviado",
                extra={"event": "order_requeued", "pedido_id": c.pedido_id, "correlation_id": c.correlation_id,
                       "reenvio": reenvio},
            )

    resumo = {"reenviados": len(candidatos), "travados": travados}
    # Um log por ciclo (inclusive com zeros): é a fonte da métrica OrdersStuck, que precisa de pontos regulares.
    log.info("ciclo de reconciliação", extra={"event": "reconciliation_cycle", **resumo})
    return resumo


class Reconciliador(threading.Thread):
    def __init__(self, engine: Engine, settings):
        super().__init__(name="reconciliador", daemon=True)
        self.engine = engine
        self.settings = settings
        self.stop_event = threading.Event()
        self.ultimo_ciclo: datetime | None = None
        self.ultimo_resumo: dict | None = None
        self.ultimo_erro: str | None = None

    def run(self):
        s = self.settings
        log.info("reconciliador iniciado", extra={"intervalo_s": s.reconciliacao_intervalo_s,
                                                  "idade_s": s.reconciliacao_idade_s,
                                                  "max_reenvios": s.reconciliacao_max_reenvios})
        while not self.stop_event.wait(s.reconciliacao_intervalo_s):
            correlation_id_var.set(None)
            try:
                resumo = reconciliar(self.engine, s.reconciliacao_idade_s, s.reconciliacao_max_reenvios)
                self.ultimo_ciclo = datetime.now(timezone.utc)
                self.ultimo_erro = None
                if resumo is not None:
                    self.ultimo_resumo = resumo
            except Exception as exc:  # banco fora do ar etc.: tenta no próximo ciclo
                self.ultimo_erro = str(exc)[:300]
                log.exception("erro no ciclo de reconciliação", extra={"event": "reconciliation_error"})
        log.info("reconciliador parado")

    def stop(self, timeout: float = 10):
        self.stop_event.set()
        self.join(timeout)

    def status(self) -> dict:
        return {
            "ativo": self.is_alive(),
            "ultimo_ciclo": self.ultimo_ciclo.isoformat() if self.ultimo_ciclo else None,
            "ultimo_resumo": self.ultimo_resumo,
            "ultimo_erro": self.ultimo_erro,
        }
