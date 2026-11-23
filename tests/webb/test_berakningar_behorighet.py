# ruff: noqa: E501
"""Projektbehörighet på `POST /api/kor`, `/api/risk` och `/api/rutt` (omrade_id i kroppen) med inloggning på."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from konto.adapter import KontoAdapter
from konto.konfig import KontoKonfig
from tests.konto.conftest import ANNA, Miljo, Webblasare
from tests.webb.test_inloggning import _app, _http
from tests.webb.test_projekt_per_anvandare import BO, _setup
from webb.api import FlodenApiApp

SOKVAGAR = ("/api/kor", "/api/risk", "/api/rutt")


def _post(app: FlodenApiApp, w: Webblasare | None, sokvag: str, omrade: str) -> tuple[int, dict[str, Any]]:
    kropp = json.dumps({"omrade_id": omrade, "gransdjup_m": 0.1, "tillat_testvarden": True}).encode()
    extra = w.csrf_headers() if w is not None else None
    status, _, svar = _http(app, "POST", sokvag, w, extra, kropp)
    try:
        return status, json.loads(svar)
    except ValueError:
        return status, {}


@pytest.fixture
def varld(tmp_path: Path, miljo: Miljo) -> tuple[FlodenApiApp, Any, dict[str, Webblasare]]:
    st = _setup(miljo)  # Anna: lasare i projekt A. Bo: utredare i projekt B.
    app = _app(tmp_path, miljo.adapter)
    webb = {"anna": miljo.webblasare(), "bo": miljo.webblasare()}
    assert webb["anna"].logga_in(miljo.idp, ANNA)[0] == 302
    assert webb["bo"].logga_in(miljo.idp, BO)[0] == 302
    return app, st, webb


def _stoppad(status: int, svar: dict[str, Any], omrade: str) -> bool:
    """Är svaret behörighetskontrollens (exakt 404-texten, utan endpointens lista över områden)?"""
    return status == 404 and svar.get("fel") == f"Okänt område '{omrade}'."


@pytest.mark.parametrize("sokvag", SOKVAGAR)
def test_ratt_roll_i_projektet_slapps_igenom(varld: Any, sokvag: str) -> None:
    app, st, webb = varld
    # Lasare räcker (beräkningarna sparar inget). Förbi kontrollen svarar endpointen själv: projektet är
    # inget beräkningsområde (404 med lista över områden), vilket skiljer sig från kontrollens text.
    for vem, projekt in (("anna", st.anna_projekt), ("bo", st.bo_projekt)):
        status, svar = _post(app, webb[vem], sokvag, projekt)
        assert not _stoppad(status, svar, projekt), (vem, sokvag)
        assert "Tillgängliga" in svar.get("fel", ""), (vem, sokvag, svar)
@pytest.mark.parametrize("sokvag", SOKVAGAR)
def test_fel_projekt_ger_404_inte_403(varld: Any, sokvag: str) -> None:
    app, st, webb = varld
    for vem, projekt in (("anna", st.bo_projekt), ("bo", st.anna_projekt)):
        status, svar = _post(app, webb[vem], sokvag, projekt)
        assert status == 404, (vem, sokvag)
        assert svar["fel"] == f"Okänt område '{projekt}'."


@pytest.mark.parametrize("sokvag", SOKVAGAR)
def test_utan_inloggning_nekas_innan_projektet_provas(varld: Any, sokvag: str) -> None:
    app, st, _ = varld
    status, _ = _post(app, None, sokvag, st.anna_projekt)
    assert status in (401, 403)


@pytest.mark.parametrize("sokvag", SOKVAGAR)
def test_demoprojektet_ger_inloggade_lasare(varld: Any, sokvag: str) -> None:
    app, _, webb = varld
    status, svar = _post(app, webb["anna"], sokvag, "demokvarteret")
    assert not _stoppad(status, svar, "demokvarteret")


@pytest.mark.parametrize("sokvag", SOKVAGAR)
def test_inloggning_av_ger_ingen_projektkontroll(tmp_path: Path, sokvag: str) -> None:
    app = _app(tmp_path, KontoAdapter(KontoKonfig()))
    status, svar = _post(app, None, sokvag, "finns-inte")
    # Inloggning av: endpointen svarar själv om området (ingen roll prövas, ingen 401/403).
    assert status not in (401, 403)
    assert "Saknar behörighet" not in json.dumps(svar)
