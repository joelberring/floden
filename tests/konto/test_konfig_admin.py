"""Konfigurationens validering, hemligheter och administratörs-CLI:t."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from konto.admin import main as admin_main
from konto.databas import Databas
from konto.konfig import KontoKonfigFel, las_konto_konfig
from konto.leverantor import bygg_leverantor
from konto.register import Register

GRUND: dict[str, Any] = {
    "konto": {
        "inloggning": "pa",
        "offentlig_url": "https://floden.test",
        "leverantorer": {"google": {"aktiv": True, "klient_id": "id", "klient_hemlighet": "hem"}},
    }
}


def test_utan_konto_sektion_ar_inloggning_av() -> None:
    assert las_konto_konfig({}, {}).inloggning == "av"
    assert las_konto_konfig(None, {}).aktiv is False


def test_hemligheter_ur_miljon_gar_fore_filen_och_syns_inte_i_repr() -> None:
    konf = las_konto_konfig(
        GRUND,
        {
            "FLODEN_KONTO_GOOGLE_KLIENT_ID": "id-fran-miljo",
            "FLODEN_KONTO_GOOGLE_KLIENT_HEMLIGHET": "hemlig-fran-miljo",
        },
    )
    lev = konf.leverantorer["google"]
    assert lev.klient_id == "id-fran-miljo" and lev.hemlighet == "hemlig-fran-miljo"
    assert "hemlig" not in repr(lev) and "hemlig" not in repr(bygg_leverantor(lev))
    assert konf.redirect_uri("google") == "https://floden.test/konto/callback/google"


@pytest.mark.parametrize(
    "andring",
    [
        {"offentlig_url": "http://floden.test"},  # http utanför localhost
        {"cookie_secure": False},  # Secure av utanför localhost
        {"inloggning": "ja"},
        {"nya_konton": "alla"},
        {"sessionstimmar": 0},
        {"leverantorer": {}},  # på men ingen leverantör
        {"leverantorer": {"google": {"aktiv": True, "klient_id": "id"}}},  # hemlighet saknas
    ],
)
def test_ogiltig_eller_ofullstandig_konfiguration_vagras(andring: dict[str, Any]) -> None:
    with pytest.raises(KontoKonfigFel):
        las_konto_konfig({"konto": {**GRUND["konto"], **andring}}, {})


def test_entra_och_generisk_oidc_byggs_ur_konfiguration_men_ar_av_som_standard() -> None:
    konf = las_konto_konfig(
        {
            "konto": {
                **GRUND["konto"],
                "leverantorer": {
                    "google": {"aktiv": True, "klient_id": "i", "klient_hemlighet": "h"},
                    "entra": {"aktiv": False},
                    "annan": {
                        "typ": "oidc",
                        "aktiv": False,
                        "discovery_url": "https://sso.test/.well-known/openid-configuration",
                        "claims": {"grupper": "roles", "tenant": "org"},
                        "flertenant": True,
                    },
                },
            }
        },
        {},
    )
    assert not konf.leverantorer["entra"].aktiv and not konf.leverantorer["annan"].aktiv
    entra = bygg_leverantor(konf.leverantorer["entra"])
    assert entra.claims.tenant == "tid" and entra.claims.grupper == "groups"
    assert entra.flertenant and not entra.epost_betrodd
    annan = bygg_leverantor(konf.leverantorer["annan"])
    assert annan.claims.grupper == "roles" and annan.claims.tenant == "org"
    assert annan.issuers == ("https://sso.test",) and annan.flertenant
    assert not annan.epost_betrodd  # måste slås på uttryckligen


def test_admin_cli_bootstrap(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    konf = tmp_path / "config.toml"
    db = tmp_path / "k.sqlite"
    konf.write_text(f'[konto]\ninloggning = "av"\ndatabas = "{db}"\n', encoding="utf-8")

    def kor(*args: str) -> str:
        assert admin_main(["--konfig", str(konf), *args]) == 0
        return capsys.readouterr().out.strip()

    org = kor("skapa-organisation", "Exempelkommun")
    kor("registrera-tenant", org, "entra", "11111111-1111-1111-1111-111111111111")
    forv = kor("skapa-forvaltning", org, "Stadsbyggnad")
    kor("mappa-grupp", org, forv, "grupp-sbk")
    konto = kor("forbered-konto", "joel@example.test")
    kor("ge-superadmin", konto)
    kor("gor-organisationsadmin", konto, org)
    projekt = kor("skapa-projekt", org, "Provområdet")
    r = Register(Databas(db))
    kontot = r.konto(konto)
    assert kontot is not None and kontot.superadmin
    assert len(projekt) == 32
