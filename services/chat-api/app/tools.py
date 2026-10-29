"""Ferramentas expostas ao LLM. Cada uma chama o commerce-api por HTTP."""
import hashlib
import json
import logging
import time

from app.commerce_client import CommerceClient, CommerceErro

log = logging.getLogger("chat.tools")

CONEXOES = ["USB-A", "USB-C", "Bluetooth", "HDMI", "DisplayPort", "P2"]
CATEGORIAS = ["teclado", "mouse", "headset", "monitor", "webcam", "acessorio"]

_ITENS_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "maxItems": 20,
    "items": {
        "type": "object",
        "properties": {
            "sku_variante": {"type": "string", "description": "SKU da variante, ex.: TEC-LOGI-K380-ABNT2-GRAFITE"},
            "quantidade": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["sku_variante", "quantidade"],
    },
}


def _spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {"json": {"type": "object", "properties": properties, "required": required}},
        }
    }


TOOL_SPECS = [
    _spec(
        "buscar_produtos",
        "Busca periféricos no catálogo da loja. Devolve SKU, preço, especificações e variantes "
        "(sku_variante, switch, layout, cor, quantidade em estoque). Use sempre antes de citar qualquer produto ou preço.",
        {
            "consulta": {"type": "string", "description": "Palavras-chave, ex.: 'mecânico', 'mousepad', 'Logitech'"},
            "categoria": {"type": "string", "enum": CATEGORIAS},
            "preco_max": {"type": "number", "description": "Preço máximo em reais"},
            "conexao": {"type": "string", "enum": CONEXOES, "description": "Só produtos que aceitam esta conexão"},
            "so": {"type": "string", "description": "Sistema operacional: Windows, macOS, Linux, Android, iOS"},
        },
        [],
    ),
    _spec(
        "consultar_estoque",
        "Consulta o estoque atual de um produto (todas as variantes) ou de uma variante específica.",
        {"sku": {"type": "string", "description": "SKU do produto ou sku_variante"}},
        ["sku"],
    ),
    _spec(
        "verificar_compatibilidade",
        "Verifica se os produtos funcionam no equipamento do cliente (portas, SO, taxa de atualização, layout) "
        "e entre si (suporte de monitor x monitor; hubs/adaptadores na lista são considerados).",
        {
            "skus": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 15},
            "conexoes": {"type": "array", "items": {"type": "string", "enum": CONEXOES},
                         "description": "Portas disponíveis no computador do cliente"},
            "so": {"type": "string", "description": "Sistema operacional do cliente"},
            "taxa_hz_max": {"type": "integer", "description": "Taxa máxima (Hz) que o computador/placa de vídeo entrega"},
            "layout": {"type": "string", "enum": ["ABNT2", "US"]},
        },
        ["skus"],
    ),
    _spec(
        "calcular_kit",
        "Calcula o preço de um kit com o desconto progressivo oficial (por número de produtos diferentes, "
        "limitado ao desconto máximo de cada produto). É a ÚNICA fonte válida de desconto.",
        {"itens": _ITENS_SCHEMA},
        ["itens"],
    ),
    _spec(
        "criar_pedido",
        "Registra o pedido do kit. Só chame depois que o cliente confirmar explicitamente os itens, o total e o nome. "
        "O preço é recalculado pelo sistema. O pedido nasce PENDENTE e a reserva de estoque é feita em seguida.",
        {
            "cliente_nome": {"type": "string", "description": "Nome do cliente para o pedido"},
            "itens": _ITENS_SCHEMA,
        },
        ["cliente_nome", "itens"],
    ),
    _spec(
        "status_pedido",
        "Consulta o status de um pedido: PENDENTE, CONFIRMADO ou REJEITADO (com motivo).",
        {"pedido_id": {"type": "string", "description": "UUID do pedido"}},
        ["pedido_id"],
    ),
]


def _slim_produto(p: dict) -> dict:
    """Só o que o LLM precisa (menos tokens)."""
    return {
        "sku": p["sku"],
        "nome": p["nome"],
        "categoria": p["categoria"],
        "marca": p["marca"],
        "descricao": p["descricao"],
        "preco": p["preco"],
        "especificacoes": p["especificacoes"],
        "em_estoque": p["em_estoque"],
        "variantes": [
            {k: v for k, v in var.items() if k in ("sku_variante", "switch", "layout", "cor", "quantidade")}
            for var in p["variantes"]
        ],
    }


def idempotency_key(conversation_id: str, cliente_nome: str, itens: list[dict]) -> str:
    """Mesma conversa + mesmo nome + mesmos itens => mesma chave.

    Protege contra o LLM chamar criar_pedido duas vezes e contra retentativas de rede.
    """
    agreg: dict[str, int] = {}
    for i in itens:
        agreg[i["sku_variante"]] = agreg.get(i["sku_variante"], 0) + int(i.get("quantidade", 1))
    canon = json.dumps(
        {"c": conversation_id, "n": cliente_nome.strip().lower(), "i": sorted(agreg.items())}, sort_keys=True
    )
    return "chat-" + hashlib.sha256(canon.encode()).hexdigest()[:40]


class ToolExecutor:
    def __init__(self, commerce: CommerceClient, conversation_id: str):
        self.commerce = commerce
        self.conversation_id = conversation_id

    def execute(self, name: str, args: dict) -> tuple[dict, str]:
        """Executa a ferramenta. Devolve (json_resultado, status 'success'|'error').

        CommerceIndisponivel NÃO é tratado aqui: sobe para o agente, que encerra o turno
        com uma resposta fixa em vez de deixar o LLM improvisar.
        """
        inicio = time.perf_counter()
        status = "success"
        try:
            fn = getattr(self, f"_t_{name}", None)
            if fn is None:
                result, status = {"erro": f"Ferramenta desconhecida: {name}"}, "error"
            else:
                result = fn(**(args or {}))
        except CommerceErro as exc:
            result, status = {"erro": exc.mensagem, "http_status": exc.status}, "error"
        except TypeError as exc:
            result, status = {"erro": f"Parâmetros inválidos: {exc}"}, "error"
        finally:
            log.info(
                f"ferramenta {name}",
                extra={"event": "tool_call", "tool": name, "status": status,
                       "duration_ms": round((time.perf_counter() - inicio) * 1000, 1)},
            )
        return result, status

    def _t_buscar_produtos(self, consulta=None, categoria=None, preco_max=None, conexao=None, so=None):
        params = {"limite": 8}
        for k, v in {"q": consulta, "categoria": categoria, "preco_max": preco_max, "conexao": conexao, "so": so}.items():
            if v not in (None, ""):
                params[k] = v
        body = self.commerce.request("GET", "/products", params=params)
        return {"total": body["total"], "produtos": [_slim_produto(p) for p in body["produtos"]]}

    def _t_consultar_estoque(self, sku):
        return self.commerce.request("GET", f"/stock/{sku}")

    def _t_verificar_compatibilidade(self, skus, conexoes=None, so=None, taxa_hz_max=None, layout=None):
        perfil = {"conexoes": conexoes or [], "so": so, "taxa_hz_max": taxa_hz_max, "layout": layout}
        return self.commerce.request("POST", "/compatibility", json={"perfil": perfil, "skus": skus})

    def _t_calcular_kit(self, itens):
        body = self.commerce.request("POST", "/kits/quote", json={"itens": itens})
        for linha in body.get("itens", []):  # ids internos não interessam ao LLM
            linha.pop("estoque_id", None)
            linha.pop("produto_id", None)
        return body

    def _t_criar_pedido(self, cliente_nome, itens):
        key = idempotency_key(self.conversation_id, cliente_nome, itens)
        body = self.commerce.request(
            "POST",
            "/orders",
            json={"cliente_nome": cliente_nome, "conversation_id": self.conversation_id, "itens": itens},
            headers={"Idempotency-Key": key},
        )
        body["observacao"] = (
            "Pedido registrado como PENDENTE; a reserva de estoque acontece em segundos. "
            "Use status_pedido para acompanhar."
        )
        return body

    def _t_status_pedido(self, pedido_id):
        return self.commerce.request("GET", f"/orders/{pedido_id}")
