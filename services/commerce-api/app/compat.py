"""Verificação de compatibilidade entre produtos e o equipamento do cliente.

Função pura (sem banco) para ser fácil de testar. Regras:
  * conexão: o produto precisa de pelo menos UMA das suas conexões disponível no equipamento,
    diretamente ou por um hub/adaptador que esteja na mesma lista (e que também caiba no equipamento);
  * sistema operacional: o SO do cliente precisa estar em so_suportados;
  * layout (teclados): o layout pedido precisa existir no produto;
  * taxa de atualização (monitores): se o equipamento (ou a porta usada) não entrega a taxa
    máxima do monitor, gera ALERTA — funciona, mas limitado;
  * suportes de monitor x monitores da lista: VESA, tamanho e peso.
Status: "compativel" | "alerta" | "incompativel".
"""

CONEXOES_VALIDAS = {"USB-A", "USB-C", "Bluetooth", "HDMI", "DisplayPort", "P2"}
SO_ALIASES = {
    "mac": "macOS", "macos": "macOS", "osx": "macOS", "windows": "Windows", "win": "Windows",
    "linux": "Linux", "ubuntu": "Linux", "android": "Android", "ios": "iOS", "ipados": "iOS",
}
CONEXAO_ALIASES = {
    "usb-a": "USB-A", "usba": "USB-A", "usb": "USB-A", "usb-c": "USB-C", "usbc": "USB-C",
    "thunderbolt": "USB-C", "bluetooth": "Bluetooth", "bt": "Bluetooth", "hdmi": "HDMI",
    "displayport": "DisplayPort", "dp": "DisplayPort", "p2": "P2", "3.5mm": "P2", "p3": "P2",
}


def normalizar_perfil(perfil: dict) -> dict:
    conexoes = []
    for c in perfil.get("conexoes") or []:
        norm = CONEXAO_ALIASES.get(str(c).strip().lower(), c)
        if norm in CONEXOES_VALIDAS and norm not in conexoes:
            conexoes.append(norm)
    so = perfil.get("so")
    so = SO_ALIASES.get(str(so).strip().lower(), so) if so else None
    layout = perfil.get("layout")
    layout = str(layout).upper().replace("-", "") if layout else None
    taxa = perfil.get("taxa_hz_max")
    return {
        "conexoes": conexoes,
        "so": so,
        "layout": layout,
        "taxa_hz_max": int(taxa) if taxa else None,
    }


def _vias(perfil: dict, adaptadores: list[dict]) -> dict[str, str | None]:
    """Mapa porta -> None (direta) ou SKU do hub/adaptador que a fornece."""
    vias: dict[str, str | None] = {c: None for c in perfil["conexoes"]}
    for a in adaptadores:
        spec = a["especificacoes"]
        if any(c in perfil["conexoes"] for c in spec.get("conexoes", [])):
            for porta in spec.get("fornece", []):
                vias.setdefault(porta, a["sku"])
    return vias


def _checar_conexao(p, spec, vias, adaptadores_por_sku, perfil):
    """Devolve (problemas, alertas, observacoes, via_usada)."""
    requer = spec.get("conexoes")
    if not requer or not perfil["conexoes"]:
        return [], [], [], None
    diretas = [c for c in requer if c in vias and vias[c] is None]
    if diretas:
        return [], [], [], None
    indiretas = [c for c in requer if c in vias]
    if indiretas:
        via = vias[indiretas[0]]
        nome = adaptadores_por_sku[via]["nome"]
        return [], [], [f"Conecta via {nome} ({indiretas[0]})."], via
    return (
        [
            f"Precisa de {' ou '.join(requer)}, mas o equipamento informado tem "
            f"{', '.join(perfil['conexoes'])}. Um hub ou adaptador pode resolver."
        ],
        [],
        [],
        None,
    )


def _checar_monitor_hz(spec, vias, adaptadores_por_sku, perfil):
    alvo = spec.get("taxa_hz")
    if not alvo:
        return []
    por_conexao = spec.get("taxa_hz_por_conexao", {})
    melhor = 0
    porta_melhor = None
    for porta in spec.get("conexoes", []):
        if porta not in vias and perfil["conexoes"]:
            continue
        hz = por_conexao.get(porta, alvo)
        via = vias.get(porta)
        if via:
            hz = min(hz, adaptadores_por_sku[via]["especificacoes"].get("taxa_hz_max_video", hz))
        if hz > melhor:
            melhor, porta_melhor = hz, porta
    if not perfil["conexoes"]:
        melhor = alvo
    if perfil["taxa_hz_max"]:
        melhor = min(melhor, perfil["taxa_hz_max"]) if melhor else 0
    if melhor and melhor < alvo:
        detalhe = f" pela conexão {porta_melhor}" if porta_melhor and por_conexao else ""
        return [f"O monitor chega a {alvo} Hz, mas no seu equipamento{detalhe} vai rodar a no máximo {melhor} Hz."]
    return []


def _checar_suportes(suportes: list[dict], monitores: list[dict]) -> list[dict]:
    pares = []
    for s in suportes:
        ss = s["especificacoes"]
        for m in monitores:
            ms = m["especificacoes"]
            problemas = []
            if not ms.get("vesa"):
                problemas.append(f"{m['nome']} não tem furação VESA, não encaixa em suporte.")
            elif ms["vesa"] not in ss.get("vesa_suportado", []):
                problemas.append(
                    f"{m['nome']} usa VESA {ms['vesa']}; o suporte aceita {', '.join(ss.get('vesa_suportado', []))}."
                )
            if ms.get("tamanho_pol") and ss.get("tamanho_max_pol") and ms["tamanho_pol"] > ss["tamanho_max_pol"]:
                problemas.append(f"Monitor de {ms['tamanho_pol']}\" passa do limite de {ss['tamanho_max_pol']}\" do suporte.")
            if ms.get("peso_kg") and ss.get("peso_max_kg") and ms["peso_kg"] > ss["peso_max_kg"]:
                problemas.append(f"Monitor de {ms['peso_kg']} kg passa do limite de {ss['peso_max_kg']} kg do suporte.")
            pares.append(
                {
                    "skus": [s["sku"], m["sku"]],
                    "status": "incompativel" if problemas else "compativel",
                    "problemas": problemas,
                }
            )
    return pares


def verificar(perfil_bruto: dict, produtos: list[dict]) -> dict:
    perfil = normalizar_perfil(perfil_bruto or {})
    adaptadores = [p for p in produtos if p["especificacoes"].get("fornece")]
    adaptadores_por_sku = {a["sku"]: a for a in adaptadores}
    vias = _vias(perfil, adaptadores)

    itens = []
    for p in produtos:
        spec = p["especificacoes"]
        problemas, alertas, obs, _via = _checar_conexao(p, spec, vias, adaptadores_por_sku, perfil)

        so = perfil["so"]
        if so and spec.get("so_suportados") and so not in spec["so_suportados"]:
            problemas.append(f"Não suporta {so} (suporta: {', '.join(spec['so_suportados'])}).")

        if p["categoria"] == "teclado" and perfil["layout"] and spec.get("layouts"):
            if perfil["layout"] not in spec["layouts"]:
                problemas.append(f"Não existe em layout {perfil['layout']} (só {', '.join(spec['layouts'])}).")

        if p["categoria"] == "monitor" and not problemas:
            alertas += _checar_monitor_hz(spec, vias, adaptadores_por_sku, perfil)

        status = "incompativel" if problemas else ("alerta" if alertas else "compativel")
        itens.append(
            {
                "sku": p["sku"],
                "nome": p["nome"],
                "status": status,
                "problemas": problemas,
                "alertas": alertas,
                "observacoes": obs,
            }
        )

    suportes = [p for p in produtos if p["especificacoes"].get("tipo") == "suporte_monitor"]
    monitores = [p for p in produtos if p["categoria"] == "monitor"]
    pares = _checar_suportes(suportes, monitores)

    todos = [i["status"] for i in itens] + [x["status"] for x in pares]
    geral = "incompativel" if "incompativel" in todos else ("alerta" if "alerta" in todos else "compativel")
    return {"perfil_considerado": perfil, "status_geral": geral, "itens": itens, "pares": pares}
