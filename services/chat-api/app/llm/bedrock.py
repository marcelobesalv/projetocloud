"""Claude no Amazon Bedrock via API Converse.

O modelId é um inference profile (us.* / global.*), vindo de LLM_MODEL_ID. Credenciais vêm
da cadeia padrão do boto3: AWS_PROFILE localmente, IAM Role da task no ECS.
"""
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.llm.base import LLMError, LLMResponse


class BedrockProvider:
    name = "bedrock"

    def __init__(self, model_id: str, region: str, timeout_s: float, max_tokens: int,
                 temperature: float | None = None, client=None):
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = client or boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                connect_timeout=5,
                read_timeout=timeout_s,
                retries={"max_attempts": 2, "mode": "standard"},  # cobre throttling ocasional
            ),
        )

    def converse(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        inference = {"maxTokens": self.max_tokens}
        if self.temperature is not None:
            inference["temperature"] = self.temperature
        try:
            resp = self.client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=messages,
                toolConfig={"tools": tools},
                inferenceConfig=inference,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            raise LLMError(f"{code}: {exc.response.get('Error', {}).get('Message', '')}") from exc
        except BotoCoreError as exc:
            raise LLMError(f"{exc.__class__.__name__}: {exc}") from exc
        return LLMResponse(
            message=resp["output"]["message"],
            stop_reason=resp.get("stopReason", "end_turn"),
            usage=resp.get("usage", {}),
        )
