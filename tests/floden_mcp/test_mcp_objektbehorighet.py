# ruff: noqa: E501
"""Rapporter och beställningar i MCP följer projektets roll: `rapport_id` och `bestallning_id` prövas mot
rapportens/beställningens projekt, inte mot `projekt`-argumentet (som förvalt är demokvarteret)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from floden_mcp.dataklass import DataKlass, Policy
from rapport.skissrapport import RapportTjanst
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp_http import (
    BO,
    Stack,
    _data,
    _initiera,
    _rpc,
    _skapa_token,
    anropa_verktyg,
)
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_nycklar import _inloggad
from tests.konto.test_projekt_per_anvandare_hjalp import setup_projekt


def _token(miljo: Miljo, claims: dict[str, str | bool]) -> str:
    webb, _ = _inloggad(miljo, claims)  # type: ignore[arg-type]
    return str(_skapa_token(webb)["token"])


@pytest.fixture
def varld(
    demo: Demo, miljo: Miljo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:
    ids = setup_projekt(miljo)  # Anna: lasare i A. Bo: utredare i B.
    rapporter = {"r-a": ids.anna_projekt, "r-b": ids.bo_projekt}
    monkeypatch.setattr(RapportTjanst, "hamta", lambda self, rid: SimpleNamespace(projekt=rapporter[rid]) if rid in rapporter else (_ for _ in ()).throw(KeyError(rid)))
    monkeypatch.setattr(RapportTjanst, "bestallning_hamta", lambda self, bid: SimpleNamespace(projekt=rapporter[bid]) if bid in rapporter else (_ for _ in ()).throw(KeyError(bid)))
    monkeypatch.setattr(
        RapportTjanst, "lista",
        lambda self, projekt=None: [{"rapport_id": "r-a", "projekt": ids.anna_projekt}, {"rapport_id": "r-b", "projekt": ids.bo_projekt}],
    )  # fmt: skip
    ta, tb = _token(miljo, ANNA), _token(miljo, BO)
    klasser = {"demokvarteret": DataKlass.OPPET, ids.anna_projekt: DataKlass.OPPET, ids.bo_projekt: DataKlass.OPPET}
    stack = Stack(demo, miljo, tmp_path, behorighet=True, policy=Policy(projektklasser=klasser))
    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        a = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        b = _initiera(klient, {"Authorization": f"Bearer {tb}"})
        yield klient, a, b, ids


def test_rapport_i_annat_projekt_ar_okand(varld: Any) -> None:
    klient, a, b, _ = varld
    # Annas egen rapport släpps igenom rollkontrollen för läsande verktyg; Bos rapport ser ut som en okänd.
    _, text, _ = anropa_verktyg(klient, a, "rapport_hamta", {"rapport_id": "r-a"})
    assert "Okänd rapport" not in text
    for verktyg in ("rapport_hamta", "rapport_kontrollera"):
        fel, text, _ = anropa_verktyg(klient, a, verktyg, {"rapport_id": "r-b"})
        assert fel and text == "Okänd rapport 'r-b'.", verktyg
    fel, text, _ = anropa_verktyg(klient, b, "rapport_hamta", {"rapport_id": "r-a"})
    assert fel and text == "Okänd rapport 'r-a'."


def test_skrivande_rapportverktyg_kraver_utredare_i_rapportens_projekt(varld: Any) -> None:
    klient, a, b, _ = varld
    # Anna är bara lasare i sitt projekt: kontroll, export, inställningar och påståenden nekas även utan `projekt`-argument.
    for verktyg in (
        "rapport_kontrollera",
        "rapport_exportera",
        "rapport_installningar",
        "pastaende_lagg_till",
    ):
        fel, text, _ = anropa_verktyg(klient, a, verktyg, {"rapport_id": "r-a"})
        assert fel and text == "Okänd rapport 'r-a'.", verktyg
    # Bo är utredare i sitt projekt: släpps igenom kontrollen.
    _, text, _ = anropa_verktyg(klient, b, "rapport_kontrollera", {"rapport_id": "r-b"})
    assert "Okänd rapport" not in text


def test_bestallning_i_annat_projekt_ar_okand(varld: Any) -> None:
    klient, a, _, _ = varld
    fel, text, _ = anropa_verktyg(klient, a, "bestallning_hamta", {"bestallning_id": "r-b"})
    assert fel and text == "Okänd beställning 'r-b'."


def test_rapportlistan_visar_bara_projekt_med_roll(varld: Any) -> None:
    klient, a, b, ids = varld
    _, _, svar = anropa_verktyg(klient, a, "rapport_hamta", {})
    assert [r["rapport_id"] for r in svar["rapporter"]] == ["r-a"]
    _, _, svar_b = anropa_verktyg(klient, b, "rapport_hamta", {})
    assert [r["rapport_id"] for r in svar_b["rapporter"]] == ["r-b"]


def test_rapportresursen_foljer_samma_regel(varld: Any) -> None:
    klient, a, _, _ = varld
    ok = _data(_rpc(klient, "resources/read", {"uri": "floden://rapport/r-a"}, a))
    assert "Okänd rapport" not in str(ok)
    nekad = _data(_rpc(klient, "resources/read", {"uri": "floden://rapport/r-b"}, a))
    assert "Okänd rapport 'r-b'." in str(nekad)
