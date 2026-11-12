"""Configuração 12-factor: tudo vem de variáveis de ambiente."""
import os
from dataclasses import dataclass, field
from decimal import Decimal


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "sim")


def parse_tiers(raw: str) -> list[tuple[int, Decimal]]:
    """'2:5,3:8,4:10,5:12' -> [(2, 5), (3, 8), ...] ordenado por número de itens."""
    tiers = []
    for part in raw.split(","):
        if part.strip():
            n, pct = part.split(":")
            tiers.append((int(n), Decimal(pct)))
    return sorted(tiers)


@dataclass(frozen=True)
class Settings:
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "setup_pronto"
    db_user: str = "commerce_api"
    db_password: str = ""
    db_sslmode: str = "prefer"
    db_pool_size: int = 5
    db_statement_timeout_ms: int = 5000

    aws_region: str = "us-east-2"
    sqs_queue_url: str | None = None
    sqs_queue_name: str | None = None  # alternativa local: resolve a URL pelo nome
    sqs_endpoint_url: str | None = None  # só para ElasticMQ local

    outbox_enabled: bool = True
    outbox_poll_interval_s: float = 1.0
    outbox_batch_size: int = 10

    # Reconciliação de pedidos PENDENTE parados (app/reconciliacao.py).
    reconciliacao_enabled: bool = True
    reconciliacao_intervalo_s: float = 60.0
    reconciliacao_idade_s: float = 600.0
    reconciliacao_max_reenvios: int = 3

    kit_tiers: list[tuple[int, Decimal]] = field(default_factory=lambda: parse_tiers("2:5,3:8,4:10,5:12"))
    max_qtd_por_item: int = 10
    log_level: str = "INFO"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        return cls(
            db_host=e.get("DB_HOST", "localhost"),
            db_port=int(e.get("DB_PORT", "5432")),
            db_name=e.get("DB_NAME", "setup_pronto"),
            db_user=e.get("DB_USER", "commerce_api"),
            db_password=e.get("DB_PASSWORD", ""),
            db_sslmode=e.get("DB_SSLMODE", "prefer"),
            db_pool_size=int(e.get("DB_POOL_SIZE", "5")),
            db_statement_timeout_ms=int(e.get("DB_STATEMENT_TIMEOUT_MS", "5000")),
            aws_region=e.get("AWS_REGION", "us-east-2"),
            sqs_queue_url=e.get("SQS_QUEUE_URL") or None,
            sqs_queue_name=e.get("SQS_QUEUE_NAME") or None,
            sqs_endpoint_url=e.get("SQS_ENDPOINT_URL") or None,
            outbox_enabled=_bool("OUTBOX_PUBLISHER_ENABLED", True),
            outbox_poll_interval_s=float(e.get("OUTBOX_POLL_INTERVAL_SECONDS", "1.0")),
            outbox_batch_size=min(int(e.get("OUTBOX_BATCH_SIZE", "10")), 10),
            reconciliacao_enabled=_bool("RECONCILIACAO_ENABLED", True),
            reconciliacao_intervalo_s=float(e.get("RECONCILIACAO_INTERVALO_SECONDS", "60")),
            reconciliacao_idade_s=float(e.get("RECONCILIACAO_IDADE_SECONDS", "600")),
            reconciliacao_max_reenvios=int(e.get("RECONCILIACAO_MAX_REENVIOS", "3")),
            kit_tiers=parse_tiers(e.get("KIT_DISCOUNT_TIERS", "2:5,3:8,4:10,5:12")),
            max_qtd_por_item=int(e.get("MAX_QTD_POR_ITEM", "10")),
            log_level=e.get("LOG_LEVEL", "INFO"),
            port=int(e.get("PORT", "8080")),
        )
