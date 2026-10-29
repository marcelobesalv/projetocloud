"""Compatibilidade: regras puras usando o próprio catálogo do seed, mais um teste do endpoint."""
import pytest

from app.compat import verificar
from seed.catalogo import PRODUTOS

CAT = {p["sku"]: p for p in PRODUTOS}
MACBOOK = {"conexoes": ["USB-C", "Bluetooth"], "so": "macOS"}


def item(resultado, sku):
    return next(i for i in resultado["itens"] if i["sku"] == sku)


def produtos(*skus):
    return [CAT[s] for s in skus]


def test_receptor_usb_a_nao_liga_em_notebook_so_usb_c():
    r = verificar(MACBOOK, produtos("MOU-LOGI-G305"))
    assert item(r, "MOU-LOGI-G305")["status"] == "incompativel"
    assert "USB-A" in item(r, "MOU-LOGI-G305")["problemas"][0]
    assert r["status_geral"] == "incompativel"


def test_adaptador_na_lista_resolve_a_conexao():
    r = verificar(MACBOOK, produtos("MOU-LOGI-G305", "ACE-ADAPTADOR-USBC-USBA"))
    g305 = item(r, "MOU-LOGI-G305")
    assert g305["status"] == "compativel"
    assert "via" in g305["observacoes"][0]


def test_sistema_operacional_nao_suportado():
    r = verificar(MACBOOK, produtos("TEC-REDRAGON-KUMARA", "TEC-LOGI-K380"))
    assert item(r, "TEC-LOGI-K380")["status"] == "compativel"
    kumara = item(r, "TEC-REDRAGON-KUMARA")
    assert kumara["status"] == "incompativel"
    assert any("macOS" in p for p in kumara["problemas"])


@pytest.mark.parametrize("so_alias", ["mac", "MacOS", "osx"])
def test_aliases_de_so(so_alias):
    r = verificar({"so": so_alias}, produtos("MOU-APPLE-MAGIC-MOUSE"))
    assert r["status_geral"] == "compativel"


def test_layout_abnt2_indisponivel():
    r = verificar({"layout": "ABNT2"}, produtos("TEC-KEYCHRON-K8PRO", "TEC-LOGI-MXKEYS-S"))
    assert item(r, "TEC-KEYCHRON-K8PRO")["status"] == "incompativel"
    assert item(r, "TEC-LOGI-MXKEYS-S")["status"] == "compativel"


def test_taxa_de_atualizacao_limitada_vira_alerta():
    r = verificar({"conexoes": ["HDMI", "DisplayPort", "USB-A"], "taxa_hz_max": 144}, produtos("MON-SAMSUNG-G4-25"))
    g4 = item(r, "MON-SAMSUNG-G4-25")
    assert g4["status"] == "alerta"
    assert "144 Hz" in g4["alertas"][0]


def test_monitor_so_por_hdmi_limita_a_taxa_da_porta():
    r = verificar({"conexoes": ["HDMI", "USB-A"]}, produtos("MON-LG-27GP850"))
    assert item(r, "MON-LG-27GP850")["status"] == "alerta"
    assert "144 Hz" in item(r, "MON-LG-27GP850")["alertas"][0]


def test_monitor_via_hub_usb_c_fica_limitado_a_60hz():
    r = verificar(MACBOOK, produtos("MON-AOC-24G2", "ACE-HUB-USBC-7EM1"))
    aoc = item(r, "MON-AOC-24G2")
    assert aoc["status"] == "alerta"
    assert "60 Hz" in aoc["alertas"][0]


def test_monitor_sem_vesa_nao_encaixa_no_braco():
    r = verificar({}, produtos("ACE-ELG-BRACO-F80N", "MON-LG-24MP400"))
    assert r["pares"][0]["status"] == "incompativel"
    assert "VESA" in r["pares"][0]["problemas"][0]
    assert r["status_geral"] == "incompativel"


def test_suporte_vesa_100_e_27_pol_nao_aceita_odyssey_32():
    r = verificar({}, produtos("ACE-MULTILASER-SUPORTE-AC313", "MON-SAMSUNG-G5-32"))
    problemas = r["pares"][0]["problemas"]
    assert len(problemas) == 2  # VESA 75 e 32"
    r2 = verificar({}, produtos("ACE-ELG-BRACO-F80N", "MON-SAMSUNG-G5-32"))
    assert r2["status_geral"] == "compativel"


def test_setup_compativel_completo():
    r = verificar(
        {"conexoes": ["USB-A", "USB-C", "HDMI", "DisplayPort", "Bluetooth", "P2"], "so": "Windows", "layout": "ABNT2"},
        produtos("TEC-LOGI-MXKEYS-S", "MOU-LOGI-G305", "HEA-HYPERX-CLOUD-II", "MON-LG-27GP850",
                 "ACE-ELG-BRACO-F80N", "WEB-LOGI-C920S"),
    )
    assert r["status_geral"] == "compativel", r


def test_endpoint_compatibilidade(client):
    r = client.post(
        "/compatibility",
        json={"perfil": {"conexoes": ["usb-c", "bluetooth"], "so": "macos"},
              "skus": ["MOU-LOGI-G305-PRETO", "TEC-LOGI-K380", "NAO-EXISTE"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["nao_encontrados"] == ["NAO-EXISTE"]
    assert {i["sku"]: i["status"] for i in body["itens"]} == {
        "MOU-LOGI-G305": "incompativel",
        "TEC-LOGI-K380": "compativel",
    }
