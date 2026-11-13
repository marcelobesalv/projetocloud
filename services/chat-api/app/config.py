"""Configuração 12-factor do chat-api."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "setup_pronto"
    db_user: str = "chat_api"
    db_password: str = ""
    db_sslmode: str = "prefer"
    db_pool_size: int = 5
    db_statement_timeout_ms: int = 5000

    commerce_api_url: str = "http://localhost:8081"
    commerce_timeout_s: float = 5.0

    llm_provider: str = "fake"  # bedrock | fake
    llm_model_id: str = "us.anthropic.claude-haiku-5-5"
    aws_region: str = "us-east-2"
    llm_timeout_s: float = 30.0
    llm_max_tokens: int = 1024
    llm_temperature: float | None = None

    max_tool_iterations: int = 6
    turn_budget_s: float = 50.0  # abaixo do idle timeout padrão do ALB (60 s)
    history_max_messages: int = 40
    history_stored_max_messages: int = 200  # o que fica gravado em conversas.mensagens (nunca menos que a janela)
    max_message_chars: int = 2000

    log_level: str = "INFO"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        temp = e.get("LLM_TEMPERATURE")
        return cls(
            db_host=e.get("DB_HOST", "localhost"),
            db_port=int(e.get("DB_PORT", "5432")),
            db_name=e.get("DB_NAME", "setup_pronto"),
            db_user=e.get("DB_USER", "chat_api"),
            db_password=e.get("DB_PASSWORD", ""),
            db_sslmode=e.get("DB_SSLMODE", "prefer"),
            db_pool_size=int(e.get("DB_POOL_SIZE", "5")),
            db_statement_timeout_ms=int(e.get("DB_STATEMENT_TIMEOUT_MS", "5000")),
            commerce_api_url=e.get("COMMERCE_API_URL", "http://localhost:8081").rstrip("/"),
            commerce_timeout_s=float(e.get("COMMERCE_TIMEOUT_SECONDS", "5")),
            llm_provider=e.get("LLM_PROVIDER", "fake").strip().lower(),
            llm_model_id=e.get("LLM_MODEL_ID", "us.anthropic.claude-haiku-5-5"),
            aws_region=e.get("AWS_REGION", "us-east-2"),
            llm_timeout_s=float(e.get("LLM_TIMEOUT_SECONDS", "30")),
            llm_max_tokens=int(e.get("LLM_MAX_TOKENS", "1024")),
            llm_temperature=float(temp) if temp else None,
            max_tool_iterations=int(e.get("MAX_TOOL_ITERATIONS", "6")),
            turn_budget_s=float(e.get("CHAT_TURN_BUDGET_SECONDS", "50")),
            history_max_messages=int(e.get("HISTORY_MAX_MESSAGES", "40")),
            history_stored_max_messages=int(e.get("HISTORY_STORED_MAX_MESSAGES", "200")),
            max_message_chars=int(e.get("MAX_MESSAGE_CHARS", "2000")),
            log_level=e.get("LOG_LEVEL", "INFO"),
            port=int(e.get("PORT", "8080")),
        )
