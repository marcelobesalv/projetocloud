"""Interface do provedor de LLM.

O formato das mensagens é o da API Converse do Bedrock (role + content blocks com text,
toolUse e toolResult). É também o formato gravado na tabela conversas, então trocar de
provedor não exige migrar histórico.
"""
from dataclasses import dataclass, field
from typing import Protocol


class LLMError(Exception):
    """Falha ao chamar o LLM (timeout, throttling, credenciais, modelo indisponível...)."""


@dataclass
class LLMResponse:
    message: dict  # {"role": "assistant", "content": [...]}
    stop_reason: str  # end_turn | tool_use | max_tokens | ...
    usage: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model_id: str

    def converse(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...
