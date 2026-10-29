"""FakeProvider: "LLM" determinístico baseado em regras, para rodar sem AWS.

Ele fala o mesmo protocolo do Bedrock Converse (toolUse/toolResult), então exercita o loop de
ferramentas, o commerce-api e o worker de verdade. Entende o roteiro de demonstração do README:
busca -> compatibilidade -> kit -> negociação -> pedido -> status. Todos os dados das respostas
vêm dos resultados das ferramentas; o Fake não sabe nada do catálogo.
"""
import re
import unicodedata

from app.llm.base import LLMResponse

# slot -> parâmetros de busca
SLOTS = {
    "teclado": {"categoria": "teclado"},
    "mouse": {"categoria": "mouse"},
    "headset": {"categoria": "headset"},
    "monitor": {"categoria": "monitor"},
    "webcam": {"categoria": "webcam"},
    "mousepad": {"categoria": "acessorio", "consulta": "mousepad"},
    "suporte": {"categoria": "acessorio", "consulta": "suporte"},
    "braco": {"categoria": "acessorio", "consulta": "braço"},
    "hub": {"categoria": "acessorio", "consulta": "hub"},
    "adaptador": {"categoria": "acessorio", "consulta": "adaptador"},
    "apoio": {"categoria": "acessorio", "consulta": "apoio"},
}
SLOT_PALAVRAS = [
    ("teclado", r"\bteclados?\b"),
    ("mouse", r"\bmouses?\b(?!\s*pad)"),
    ("mousepad", r"\bmouse\s*pads?\b|\bmousepads?\b"),
    ("headset", r"\bheadsets?\b|\bfones?\b|\bheadphones?\b"),
    ("monitor", r"\bmonitor(es)?\b"),
    ("webcam", r"\bwebcams?\b|\bcameras?\b"),
    ("braco", r"\bbracos?\b"),
    ("suporte", r"\bsuportes?\b"),
    ("hub", r"\bhubs?\b"),
    ("adaptador", r"\badaptador(es)?\b"),
    ("apoio", r"\bapoio\b"),
]
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def slots_em(texto: str) -> list[str]:
    t = norm(texto)
    return [slot for slot, rx in SLOT_PALAVRAS if re.search(rx, t)]


def perfil_de(textos: list[str]) -> dict:
    perfil: dict = {"conexoes": []}
    for texto in textos:
        t = norm(texto)
        achadas = []
        for rx, porta in [
            (r"usb[\s-]?c|type[\s-]?c|thunderbolt", "USB-C"),
            (r"usb[\s-]?a\b|usb comum", "USB-A"),
            (r"bluetooth", "Bluetooth"),
            (r"hdmi", "HDMI"),
            (r"displayport|\bdp\b", "DisplayPort"),
            (r"\bp2\b|entrada de fone", "P2"),
        ]:
            if re.search(rx, t):
                achadas.append(porta)
        if achadas:
            perfil["conexoes"] = sorted(set(perfil["conexoes"]) | set(achadas))
        if re.search(r"\bmac(book|os)?\b|\bimac\b", t):
            perfil["so"] = "macOS"
        elif "windows" in t:
            perfil["so"] = "Windows"
        elif "linux" in t or "ubuntu" in t:
            perfil["so"] = "Linux"
        m = re.search(r"(\d{2,3})\s*hz", t)
        if m:
            perfil["taxa_hz_max"] = int(m.group(1))
        if "abnt" in t:
            perfil["layout"] = "ABNT2"
        elif re.search(r"layout (us|americano)|\bamericano\b", t):
            perfil["layout"] = "US"
    return perfil


class Estado:
    """O que o Fake "lembra" lendo o histórico (só dados que vieram de ferramentas)."""

    def __init__(self, messages: list[dict]):
        self.textos_usuario: list[str] = []
        self.candidatos: dict[str, list[dict]] = {}  # slot -> produtos da última busca
        self.compat: dict[str, str] = {}  # sku -> status
        self.ultimo_kit: dict | None = None
        self.ultimo_kit_itens: list[dict] = []
        self.ultimo_pedido: dict | None = None
        self.turno: list[tuple[str, dict, dict, str]] = []  # (tool, input, resultado, status) deste turno
        pendentes: dict[str, tuple[str, dict]] = {}
        for m in messages:
            for b in m["content"]:
                if "text" in b and m["role"] == "user":
                    self.textos_usuario.append(b["text"])
                    self.turno = []
                if "toolUse" in b:
                    pendentes[b["toolUse"]["toolUseId"]] = (b["toolUse"]["name"], b["toolUse"].get("input", {}))
                if "toolResult" in b:
                    tr = b["toolResult"]
                    nome, entrada = pendentes.get(tr["toolUseId"], ("?", {}))
                    res = tr["content"][0].get("json", {}) if tr.get("content") else {}
                    self.turno.append((nome, entrada, res, tr.get("status", "success")))
                    if tr.get("status") == "error":
                        continue
                    self._absorver(nome, entrada, res)

    def _absorver(self, nome, entrada, res):
        if nome == "buscar_produtos":
            slot = slot_da_busca(entrada)
            if slot:
                self.candidatos[slot] = res.get("produtos", [])
        elif nome == "verificar_compatibilidade":
            for i in res.get("itens", []):
                self.compat[i["sku"]] = i["status"]
        elif nome == "calcular_kit":
            self.ultimo_kit = res
            self.ultimo_kit_itens = entrada.get("itens", [])
        elif nome in ("criar_pedido", "status_pedido"):
            self.ultimo_pedido = res

    @property
    def texto_atual(self) -> str:
        return self.textos_usuario[-1] if self.textos_usuario else ""

    def chamado_no_turno(self, nome: str) -> bool:
        return any(t[0] == nome for t in self.turno)


def slot_da_busca(entrada: dict) -> str | None:
    for slot, params in SLOTS.items():
        if params.get("categoria") == entrada.get("categoria") and params.get("consulta") == entrada.get("consulta"):
            return slot
    return None


def escolher(produtos: list[dict], compat: dict, perfil: dict) -> tuple[dict, dict] | None:
    """Primeiro produto compatível (se já verificado) com uma variante em estoque."""
    for p in produtos:
        if compat.get(p["sku"]) == "incompativel":
            continue
        variantes = [v for v in p["variantes"] if v.get("quantidade", 0) > 0]
        if perfil.get("layout") and p["categoria"] == "teclado":
            variantes = [v for v in variantes if v.get("layout") == perfil["layout"]]
        if variantes:
            return p, variantes[0]
    return None


def fmt_variante(v: dict) -> str:
    return " / ".join(str(v[k]) for k in ("switch", "layout", "cor") if v.get(k))


class FakeProvider:
    name = "fake"
    model_id = "fake-deterministico"

    def converse(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self._n = len(messages)
        estado = Estado(messages)
        t = norm(estado.texto_atual)
        perfil = perfil_de(estado.textos_usuario)

        erros = [r for r in estado.turno if r[3] == "error"]
        if erros:
            return self._texto("Não consegui completar: " + erros[-1][2].get("erro", "erro na consulta") + ".")

        if re.search(r"\bstatus\b|andamento|como esta (o|meu) pedido|acompanhar", t):
            return self._status(estado, t)
        if re.search(r"\bfech|finaliz|pode (fazer|criar|registrar) o pedido|confirmo|quero comprar", t):
            return self._pedido(estado, t)
        if re.search(r"\b(adiciona|adicione|inclui|inclua|coloca|coloque|acrescenta)\b", t):
            return self._kit(estado, perfil, slots_em(t), adicionar=True)
        if "desconto" in t and estado.ultimo_kit and not re.search(r"\bmonta|kit com\b", t):
            return self._negociar(estado)
        if re.search(r"\bkit\b|\bmont|orcamento|quanto fica|\bsetup\b", t):
            return self._kit(estado, perfil, slots_em(t))
        if re.search(r"compativ|funciona|\bserve\b", t):
            return self._compat(estado, perfil, slots_em(t))
        if re.search(r"estoque|disponiv|\btem\b.*\bcor", t) and estado.candidatos:
            return self._estoque(estado, slots_em(t))
        slots = slots_em(t)
        if slots:
            return self._buscar(estado, perfil, slots)
        return self._texto(
            "Olá! Sou o consultor da Setup Pronto. Me conte o que você procura (teclado, mouse, headset, "
            "monitor, webcam...) e como é o seu computador: portas (USB-A, USB-C, Bluetooth, HDMI), sistema "
            "operacional e, se for monitor, quantos Hz sua placa entrega."
        )

    # ------------------------------------------------------------------ respostas
    def _texto(self, texto: str) -> LLMResponse:
        return LLMResponse({"role": "assistant", "content": [{"text": texto}]}, "end_turn",
                           {"inputTokens": 0, "outputTokens": 0})

    def _ferramentas(self, chamadas: list[tuple[str, dict]], prefacio: str | None = None) -> LLMResponse:
        content = [{"text": prefacio}] if prefacio else []
        for i, (nome, entrada) in enumerate(chamadas):
            content.append({"toolUse": {"toolUseId": f"fake_{self._n}_{i}", "name": nome, "input": entrada}})
        return LLMResponse({"role": "assistant", "content": content}, "tool_use", {"inputTokens": 0, "outputTokens": 0})

    def _busca_params(self, slot: str, perfil: dict) -> dict:
        params = dict(SLOTS[slot])
        if perfil.get("so") and slot in ("teclado", "mouse", "headset", "webcam"):
            params["so"] = perfil["so"]
        return params

    def _buscar(self, estado: Estado, perfil: dict, slots: list[str]) -> LLMResponse:
        if not estado.chamado_no_turno("buscar_produtos"):
            return self._ferramentas([("buscar_produtos", self._busca_params(s, perfil)) for s in slots])
        linhas = ["Encontrei estas opções no catálogo:"]
        for s in slots:
            for p in estado.candidatos.get(s, [])[:4]:
                conexoes = ", ".join(p["especificacoes"].get("conexoes", [])) or "—"
                disp = "em estoque" if p["em_estoque"] else "sem estoque"
                linhas.append(f"- **{p['nome']}** ({p['sku']}) — {p['preco']} — conexões: {conexoes} — {disp}")
            if not estado.candidatos.get(s):
                linhas.append(f"- Nenhum resultado para {s} com esses filtros.")
        linhas.append("\nQuer que eu verifique a compatibilidade com o seu equipamento ou monte um kit?")
        return self._texto("\n".join(linhas))

    def _compat(self, estado: Estado, perfil: dict, slots: list[str]) -> LLMResponse:
        if not estado.chamado_no_turno("verificar_compatibilidade"):
            slots = slots or list(estado.candidatos)
            skus = [p["sku"] for s in slots for p in estado.candidatos.get(s, [])[:4]]
            if not skus:
                return self._texto("Primeiro me diga quais produtos você quer avaliar (ex.: teclado e mouse).")
            return self._ferramentas([("verificar_compatibilidade", {"skus": skus[:15], **_perfil_args(perfil)})])
        res = next(r[2] for r in reversed(estado.turno) if r[0] == "verificar_compatibilidade")
        return self._texto(_texto_compat(res))

    def _estoque(self, estado: Estado, slots: list[str]) -> LLMResponse:
        if not estado.chamado_no_turno("consultar_estoque"):
            slot = (slots or list(estado.candidatos))[0]
            produtos = estado.candidatos.get(slot) or []
            if not produtos:
                return self._texto("Qual produto você quer que eu consulte?")
            return self._ferramentas([("consultar_estoque", {"sku": produtos[0]["sku"]})])
        res = next(r[2] for r in reversed(estado.turno) if r[0] == "consultar_estoque")
        linhas = [f"Estoque de **{res['nome']}** agora:"]
        for v in res["variantes"]:
            linhas.append(f"- {fmt_variante(v) or 'padrão'}: {v['quantidade']} un.")
        return self._texto("\n".join(linhas))

    def _kit(self, estado: Estado, perfil: dict, slots: list[str], adicionar: bool = False) -> LLMResponse:
        base_itens = list(estado.ultimo_kit_itens) if adicionar else []
        if not slots and not adicionar:
            slots = list(estado.candidatos)
        if not slots and not base_itens:
            return self._texto("Me diga quais itens você quer no kit (ex.: teclado, mouse e headset).")

        # 1) buscar o que ainda não foi buscado
        faltam = [s for s in slots if s not in estado.candidatos]
        if faltam and not estado.chamado_no_turno("buscar_produtos"):
            return self._ferramentas([("buscar_produtos", self._busca_params(s, perfil)) for s in faltam])

        # 2) verificar compatibilidade dos candidatos (se sabemos algo do equipamento)
        if _perfil_args(perfil) and not estado.chamado_no_turno("verificar_compatibilidade"):
            skus = [p["sku"] for s in slots for p in estado.candidatos.get(s, [])[:4]]
            skus = [s for s in skus if s not in estado.compat]
            if skus:
                return self._ferramentas([("verificar_compatibilidade", {"skus": skus[:15], **_perfil_args(perfil)})])

        # 3) escolher variantes e cotar
        if not estado.chamado_no_turno("calcular_kit"):
            itens = list(base_itens)
            sem_opcao = []
            for s in slots:
                escolha = escolher(estado.candidatos.get(s, []), estado.compat, perfil)
                if escolha is None:
                    sem_opcao.append(s)
                    continue
                _, v = escolha
                if v["sku_variante"] not in {i["sku_variante"] for i in itens}:
                    itens.append({"sku_variante": v["sku_variante"], "quantidade": 1})
            if not itens:
                return self._texto(
                    "Não encontrei opções compatíveis e com estoque para " + ", ".join(sem_opcao) + "."
                )
            return self._ferramentas([("calcular_kit", {"itens": itens})])

        kit = next(r[2] for r in reversed(estado.turno) if r[0] == "calcular_kit")
        return self._texto(_texto_kit(kit, estado, intro="Montei este kit (todos compatíveis e com estoque):"))

    def _negociar(self, estado: Estado) -> LLMResponse:
        kit = estado.ultimo_kit
        prox = kit.get("proxima_faixa")
        texto = (
            f"O desconto do kit é calculado pelo sistema e hoje está em {kit['desconto_faixa_pct']:g}% "
            f"({kit['produtos_diferentes']} produtos diferentes), total de {kit['total']}. "
            "Não consigo aplicar um desconto por fora dessa regra."
        )
        if prox:
            texto += (
                f" Mas, se você incluir mais um produto diferente (chegando a {prox['produtos_diferentes']}), "
                f"a faixa sobe para {prox['desconto_pct']:g}% em todo o kit — um mousepad ou um suporte de "
                "monitor costumam valer a pena. Quer que eu adicione?"
            )
        else:
            texto += " O kit já está na maior faixa de desconto."
        return self._texto(texto)

    def _pedido(self, estado: Estado, t: str) -> LLMResponse:
        if estado.chamado_no_turno("criar_pedido"):
            p = next(r[2] for r in reversed(estado.turno) if r[0] == "criar_pedido")
            return self._texto(
                f"Pedido registrado! Número: **{p['pedido_id']}**\n"
                f"Total: {p['total']} (desconto de {p['desconto']}).\n"
                f"Status: {p['status']} — estamos reservando o estoque agora; em alguns segundos ele deve "
                "ficar CONFIRMADO. É só me pedir o status quando quiser."
            )
        if not estado.ultimo_kit_itens:
            return self._texto("Ainda não montamos um kit. Me diga quais itens você quer e eu calculo o preço.")
        texto_orig = estado.texto_atual
        m = re.search(r"(?:nome d[aeo]|em nome de|meu nome e|sou o|sou a)\s+([A-Za-zÀ-ÿ' ]{2,60})", texto_orig, re.I)
        if not m:
            return self._texto(
                "Perfeito! Para registrar o pedido com o kit de "
                f"{estado.ultimo_kit['total']}, me diga em nome de quem ele deve ficar."
            )
        nome = m.group(1).strip().rstrip(".").strip()
        nome = re.split(r"\s+(?:por favor|pfv|obrigad[oa])\b", nome, flags=re.I)[0].strip()
        return self._ferramentas([("criar_pedido", {"cliente_nome": nome.title(), "itens": estado.ultimo_kit_itens})])

    def _status(self, estado: Estado, t: str) -> LLMResponse:
        if estado.chamado_no_turno("status_pedido"):
            p = next(r[2] for r in reversed(estado.turno) if r[0] == "status_pedido")
            texto = f"O pedido {p['pedido_id']} está **{p['status']}**."
            if p["status"] == "CONFIRMADO":
                texto += f" Estoque reservado. Total: {p['total']}."
            elif p["status"] == "REJEITADO":
                texto += f" Motivo: {p['motivo_rejeicao']}"
            else:
                texto += " A reserva de estoque ainda está em processamento; tente de novo em alguns segundos."
            return self._texto(texto)
        m = UUID_RE.search(t)
        pedido_id = m.group(0) if m else (estado.ultimo_pedido or {}).get("pedido_id")
        if not pedido_id:
            return self._texto("Qual é o número do pedido?")
        return self._ferramentas([("status_pedido", {"pedido_id": pedido_id})])


def _perfil_args(perfil: dict) -> dict:
    return {k: v for k, v in perfil.items() if v}


def _texto_compat(res: dict) -> str:
    simbolo = {"compativel": "✅", "alerta": "⚠️", "incompativel": "❌"}
    linhas = ["Verifiquei com o seu equipamento:"]
    for i in res["itens"]:
        detalhe = " ".join(i["problemas"] + i["alertas"] + i["observacoes"])
        linhas.append(f"- {simbolo[i['status']]} **{i['nome']}** — {i['status']}" + (f": {detalhe}" if detalhe else ""))
    for par in res.get("pares", []):
        if par["problemas"]:
            linhas.append(f"- ❌ {' '.join(par['problemas'])}")
    return "\n".join(linhas)


def _texto_kit(kit: dict, estado: Estado, intro: str) -> str:
    linhas = [intro]
    for i in kit["itens"]:
        var = f" ({i['variante']})" if i.get("variante") else ""
        linhas.append(f"- {i['quantidade']}x **{i['nome']}**{var} — {i['preco_unit']} — desconto {i['desconto_pct']:g}%")
    linhas.append(f"\nSubtotal: {kit['subtotal']} · Desconto: {kit['desconto']} · **Total: {kit['total']}**")
    if kit.get("observacao"):
        linhas.append(kit["observacao"])
    if kit.get("proxima_faixa"):
        pf = kit["proxima_faixa"]
        linhas.append(f"Com {pf['produtos_diferentes']} produtos diferentes o desconto sobe para {pf['desconto_pct']:g}%.")
    linhas.append("Posso fechar o pedido? Se sim, me diga em nome de quem.")
    return "\n".join(linhas)
