import uuid

from botocore.stub import Stubber

from app.agent import MSG_COMMERCE_FORA, MSG_LIMITE, MSG_LLM_FORA, janela_historico
from app.llm.base import LLMError, LLMResponse
from app.tools import idempotency_key
from conftest import CommerceStub


class ProviderQuebrado:
    name, model_id = "quebrado", "x"

    def converse(self, system, messages, tools):
        raise LLMError("ThrottlingException: muitas requisições")


class ProviderEmLoop:
    """Sempre pede mais uma ferramenta — o agente tem que parar sozinho."""

    name, model_id = "loop", "x"

    def __init__(self):
        self.chamadas = 0

    def converse(self, system, messages, tools):
        self.chamadas += 1
        tu = {"toolUseId": f"t{self.chamadas}", "name": "buscar_produtos", "input": {"categoria": "mouse"}}
        return LLMResponse({"role": "assistant", "content": [{"toolUse": tu}]}, "tool_use")


def test_conversa_usa_dados_do_commerce_e_guarda_historico(make_client):
    client, stub = make_client()
    r = client.post("/chat", json={"message": "Procuro um teclado"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "Teclado Stub Bluetooth" in body["resposta"] and "R$ 123,45" in body["resposta"]

    # correlation_id gerado no chat-api chega ao commerce-api
    assert stub.requisicoes[0].headers["X-Correlation-ID"] == body["correlation_id"]
    assert r.headers["X-Correlation-ID"] == body["correlation_id"]

    hist = client.get(f"/conversations/{body['conversation_id']}").json()["mensagens"]
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_commerce_fora_do_ar_nao_inventa_dados(make_client):
    client, stub = make_client(stub=CommerceStub(fora_do_ar=True))
    r = client.post("/chat", json={"message": "Procuro um teclado"})
    body = r.json()
    assert body["resposta"] == MSG_COMMERCE_FORA
    assert body["status"] == "commerce_indisponivel"
    assert len(stub.requisicoes) == 2  # uma nova tentativa e desiste
    # o turno com falha não é gravado
    assert client.get(f"/conversations/{body['conversation_id']}").json()["mensagens"] == []


def test_llm_fora_do_ar(make_client):
    client, _ = make_client(provider=ProviderQuebrado())
    body = client.post("/chat", json={"message": "oi"}).json()
    assert body["resposta"] == MSG_LLM_FORA
    assert body["status"] == "llm_indisponivel"


def test_loop_de_ferramentas_e_limitado(make_client, settings):
    provider = ProviderEmLoop()
    client, _ = make_client(provider=provider)
    body = client.post("/chat", json={"message": "oi"}).json()
    assert body["status"] == "limite"
    assert body["resposta"] == MSG_LIMITE
    assert provider.chamadas == settings.max_tool_iterations
    # histórico continua válido para a próxima chamada (alternância e toolUse sempre com toolResult)
    r2 = client.post("/chat", json={"conversation_id": body["conversation_id"], "message": "oi de novo"})
    assert r2.status_code == 200


def test_conversa_inexistente_da_404(make_client):
    client, _ = make_client()
    r = client.post("/chat", json={"conversation_id": str(uuid.uuid4()), "message": "oi"})
    assert r.status_code == 404


def test_mensagem_vazia_ou_longa(make_client):
    client, _ = make_client()
    assert client.post("/chat", json={"message": "   "}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 5000}).status_code == 422


def test_idempotency_key_estavel():
    conv = str(uuid.uuid4())
    a = idempotency_key(conv, "Ana Souza", [{"sku_variante": "B", "quantidade": 1}, {"sku_variante": "A", "quantidade": 2}])
    b = idempotency_key(conv, " ana souza ", [{"sku_variante": "A", "quantidade": 2}, {"sku_variante": "B", "quantidade": 1}])
    c = idempotency_key(conv, "Ana Souza", [{"sku_variante": "A", "quantidade": 1}])
    assert a == b != c


def test_janela_nunca_comeca_em_tool_result():
    msgs = [
        {"role": "user", "content": [{"text": "a"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "1", "name": "x", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "content": [{"json": {}}]}}]},
        {"role": "assistant", "content": [{"text": "b"}]},
        {"role": "user", "content": [{"text": "c"}]},
        {"role": "assistant", "content": [{"text": "d"}]},
    ]
    assert janela_historico(msgs, 4)[0]["content"][0]["text"] == "c"
    assert janela_historico(msgs, 6) == msgs


def test_bedrock_provider_usa_inference_profile_e_tool_config(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "teste")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "teste")
    import boto3

    from app.llm.bedrock import BedrockProvider
    from app.tools import TOOL_SPECS

    client = boto3.client("bedrock-runtime", region_name="sa-east-1")
    provider = BedrockProvider("global.anthropic.claude-haiku-5-5", "sa-east-1", 30, 512, client=client)
    msgs = [{"role": "user", "content": [{"text": "oi"}]}]
    with Stubber(client) as stub:
        stub.add_response(
            "converse",
            {"output": {"message": {"role": "assistant", "content": [{"text": "Olá!"}]}}, "stopReason": "end_turn",
             "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12}, "metrics": {"latencyMs": 100}},
            {"modelId": "global.anthropic.claude-haiku-5-5", "system": [{"text": "sys"}], "messages": msgs,
             "toolConfig": {"tools": TOOL_SPECS}, "inferenceConfig": {"maxTokens": 512}},
        )
        stub.add_client_error("converse", service_error_code="ThrottlingException", http_status_code=429)
        r = provider.converse("sys", msgs, TOOL_SPECS)
        assert r.message["content"][0]["text"] == "Olá!"
        try:
            provider.converse("sys", msgs, TOOL_SPECS)
            raise AssertionError("deveria falhar")
        except LLMError as exc:
            assert "ThrottlingException" in str(exc)
