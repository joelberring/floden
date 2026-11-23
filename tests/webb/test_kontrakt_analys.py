"""Kontrollerar att K40-K43 (och tillägget i K8) i docs/DATAKONTRAKT.md stämmer med webb-API:t.

Svarens nycklar jämförs exakt med tabellernas första kolumn, och de fält koden läser ur begäran
jämförs med begärans tabell. Ändras koden utan att dokumentet, eller tvärtom, går testet sönder."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
KONTRAKT = (REPO / "docs" / "DATAKONTRAKT.md").read_text(encoding="utf-8")


def _avsnitt(rubrikstart: str) -> str:
    """Texten från rubriken `## <rubrikstart>` till nästa `## `-rubrik."""
    start = KONTRAKT.index(f"\n## {rubrikstart}")
    slut = KONTRAKT.find("\n## ", start + 4)
    return KONTRAKT[start : slut if slut != -1 else None]


def _tabellnamn(text: str) -> dict[str, set[str]]:
    """Namn i tabellernas första kolumn, uppdelat på `begaran` och `svar` efter styckerubriken."""
    lage = ""
    ut: dict[str, set[str]] = {"begaran": set(), "svar": set()}
    for rad in text.splitlines():
        if rad.startswith("Begäran"):
            lage = "begaran"
        elif rad.startswith("Svar (200)"):
            lage = "svar"
        elif lage and rad.startswith("| `"):
            ut[lage].update(re.findall(r"`([^`]+)`", rad.split("|")[1]))
        elif lage and rad.strip() and not rad.startswith("|"):
            lage = ""  # löptext efter tabellen: nästa tabell hör till nästa rubrikstycke
    return ut


def _nyckelord_i_stycke(text: str, start: str) -> set[str]:
    """Alla `kodnamn` i stycket som börjar med `start` (för listor som beskrivs i löptext)."""
    i = text.index(start)
    slut = text.find("\n\n", i)
    return set(re.findall(r"`([a-z][a-z_0-9]*)`", text[i : slut if slut != -1 else None]))


def _lasta_fält(metod: Callable[..., Any]) -> set[str]:
    """Fält som metoden läser ur begäran (`payload.get("x")`, `_tolka_tal(payload, "x", ...)`)."""
    kalla = inspect.getsource(metod)
    return set(re.findall(r'payload\.get\("(\w+)"', kalla)) | set(
        re.findall(r'\(\s*payload,\s*"(\w+)"', kalla)
    )


@pytest.fixture
def app(tmp_path: Path) -> FlodenApiApp:
    return FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )


def _post(app: FlodenApiApp, sokvag: str, payload: dict[str, Any]) -> dict[str, Any]:
    status, _, kropp = app.hantera_forfragan("POST", sokvag, json.dumps(payload).encode())
    assert status == 200, kropp
    svar: dict[str, Any] = json.loads(kropp)
    return svar


def test_k40_vattenintervall(app: FlodenApiApp) -> None:
    text = _avsnitt("K40 ")
    namn = _tabellnamn(text)
    svar = _post(app, "/api/analys/intervall", {"omrade_id": "A1_enkel_sanka"})
    assert set(svar) == namn["svar"]
    assert svar["format"] == "floden-vatten-intervall/v1"
    assert set(svar["sammanfattning"]) <= _nyckelord_i_stycke(text, "| `sammanfattning`")
    assert svar["noder"] and len(svar["noder"]) <= 50
    assert set(svar["noder"][0]) == _nyckelord_i_stycke(text, "Nod (`noder[]`)") - {"noder"}
    assert _lasta_fält(FlodenApiApp.kor_intervall_analys) == namn["begaran"]


def test_k41_svf_intervall(app: FlodenApiApp) -> None:
    text = _avsnitt("K41 ")
    namn = _tabellnamn(text)
    svar = _post(app, "/api/analys/svf-intervall", {"omrade_id": "A8_port_byggnad"})
    assert set(svar) == namn["svar"]
    assert svar["format"] == "floden-dagsljus-svf-intervall/v1"
    assert _lasta_fält(FlodenApiApp.kor_svf_intervall_analys) == namn["begaran"]


_BULLER = {
    "bas_niva_db": [63.0, 61.0, 58.0, 52.0],
    "skarm_reduktion_db": {"S_norr": [8.0, 6.0, 0.0, 0.0], "S_soder": [0.0, 2.0, 4.0, 0.0]},
    "k": 2,
}


def test_k42_buller_skarmar(app: FlodenApiApp) -> None:
    text = _avsnitt("K42 ")
    namn = _tabellnamn(text)
    utan = _post(app, "/api/analys/buller-skarmar", _BULLER)
    avstand = [40.0, 180.0, 320.0, 500.0]
    med = _post(app, "/api/analys/buller-skarmar", {**_BULLER, "avstand_m": avstand})
    # `akustiska_bifurkationer` finns bara när `avstand_m` anges (står så i kontraktet).
    assert set(utan) == namn["svar"] - {"akustiska_bifurkationer"}
    assert set(med) == namn["svar"]
    assert utan["format"] == "floden-buller-optimering/v1"
    assert "Finns bara när `avstand_m` angavs" in text
    # Posternas nycklar beskrivs i löptext: varje nyckel i svaret ska stå som `kodnamn` där.
    assert set(med["valda_skarmar"][0]) <= _nyckelord_i_stycke(text, "`valda_skarmar[]`")
    assert set(med["akustiska_bifurkationer"][0]) <= _nyckelord_i_stycke(
        text, "`akustiska_bifurkationer[]`"
    )
    assert _lasta_fält(FlodenApiApp.kor_buller_optimering_analys) == namn["begaran"]


def test_k43_luft_mkn(app: FlodenApiApp) -> None:
    text = _avsnitt("K43 ")
    namn = _tabellnamn(text)
    svar = _post(
        app, "/api/analys/luft-mkn", {"timvis_lokal_ug_m3": [42.0] * (40 * 24) + [15.0] * (20 * 24)}
    )
    assert set(svar) == namn["svar"]
    assert svar["format"] == "floden-luft-mkn-intervall/v1"
    assert _lasta_fält(FlodenApiApp.kor_luft_mkn_analys) == namn["begaran"]


def test_k8_tillagg_antal_insatser_ar_additivt(app: FlodenApiApp) -> None:
    text = _avsnitt("K8 ")
    bas = {
        "omrade_id": "A1_enkel_sanka",
        "tillat_testvarden": True,
        "gransdjup_m": 0.10,
        "fordon_typ": "slacksbil",
    }
    utan = _post(app, "/api/rutt", bas)
    med = _post(app, "/api/rutt", {**bas, "antal_insatser": 2})
    # Additivt: samma nycklar som förut, plus `rekommenderade_insatser` när `antal_insatser > 0`.
    assert set(med) == set(utan) | {"rekommenderade_insatser"}
    assert "rekommenderade_insatser" not in utan
    tillagg = "**Tillägg i webb-API:t"
    assert set(med["rekommenderade_insatser"][0]) <= _nyckelord_i_stycke(text, tillagg)
    assert "K8 förblir v1" in text
    assert "antal_insatser" in _lasta_fält(FlodenApiApp.kor_rutt_analys)


def test_kontraktsnumren_ligger_i_ordning() -> None:
    """K38 och K39 kom med MCP-grenen (PR #58); formaten här är K40-K43, i nummerordning."""
    nummer = [int(n) for n in re.findall(r"^## K(\d+) ", KONTRAKT, flags=re.M)]
    assert {38, 39, 40, 41, 42, 43} <= set(nummer)
    assert nummer == sorted(nummer)
