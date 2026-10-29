"""Configuração 12-factor do order-worker."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "setup_pronto"
    db_user: str = "order_worker"
    db_password: str = ""
    db_sslmode: str = "prefer"
    db_pool_size: int = 3
    db_statement_timeout_ms: int = 10000

    aws_region: str = "us-east-2"
    sqs_queue_url: str | None = None
    sqs_queue_name: str | None = None  # alternativa local: resolve a URL pelo nome
    sqs_endpoint_url: str | None = None  # só para ElasticMQ local
    sqs_wait_time_s: int = 20  # long polling (máximo do SQS)
    sqs_max_messages: int = 10

    health_max_loop_age_s: int = 90
    log_level: str = "INFO"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        if not (e.get("SQS_QUEUE_URL") or e.get("SQS_QUEUE_NAME")):
            raise RuntimeError("Defina SQS_QUEUE_URL (ou SQS_QUEUE_NAME com SQS_ENDPOINT_URL no ambiente local).")
        return cls(
            db_host=e.get("DB_HOST", "localhost"),
            db_port=int(e.get("DB_PORT", "5432")),
            db_name=e.get("DB_NAME", "setup_pronto"),
            db_user=e.get("DB_USER", "order_worker"),
            db_password=e.get("DB_PASSWORD", ""),
            db_sslmode=e.get("DB_SSLMODE", "prefer"),
            db_pool_size=int(e.get("DB_POOL_SIZE", "3")),
            db_statement_timeout_ms=int(e.get("DB_STATEMENT_TIMEOUT_MS", "10000")),
            aws_region=e.get("AWS_REGION", "us-east-2"),
            sqs_queue_url=e.get("SQS_QUEUE_URL") or None,
            sqs_queue_name=e.get("SQS_QUEUE_NAME") or None,
            sqs_endpoint_url=e.get("SQS_ENDPOINT_URL") or None,
            sqs_wait_time_s=min(int(e.get("SQS_WAIT_TIME_SECONDS", "20")), 20),
            sqs_max_messages=min(int(e.get("SQS_MAX_MESSAGES", "10")), 10),
            health_max_loop_age_s=int(e.get("HEALTH_MAX_LOOP_AGE_SECONDS", "90")),
            log_level=e.get("LOG_LEVEL", "INFO"),
            port=int(e.get("PORT", "8080")),
        )
