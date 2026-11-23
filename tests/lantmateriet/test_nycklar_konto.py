"""Lantmäteri-hämtningen: med inloggning på gäller användarens egen nyckel, annars som förut."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from konto import Databas, Register
from konto.nycklar import HEMLIGHET_MILJOVARIABEL, NyckelForrad
from lantmateriet.nycklar import NyckelFel, las_nycklar_for_konto

SERVER_ID, SERVER_HEMLIGHET = "server-id-111111", "server-hemlighet-111111"
ANV_ID, ANV_HEMLIGHET = "anvandar-id-222222", "anvandar-hemlighet-222222"
SERVERMILJO = {
    "LANTMATERIET_KLIENT_ID": SERVER_ID,
    "LANTMATERIET_KLIENT_HEMLIGHET": SERVER_HEMLIGHET,
}


@pytest.fixture
def forrad(tmp_path: Path) -> tuple[NyckelForrad, str, str]:
    reg = Register(Databas(tmp_path / "konto.sqlite"))
    anna, bo = reg.forbered_konto("anna@example.test"), reg.forbered_konto("bo@example.test")
    f = NyckelForrad(reg, Fernet.generate_key().decode())
    f.spara(anna, "lantmateriet", {"klient_id": ANV_ID, "klient_hemlighet": ANV_HEMLIGHET})
    return f, anna, bo


def test_inloggning_av_anvander_servernyckeln_som_forut() -> None:
    n = las_nycklar_for_konto(None, konfig={}, miljo=SERVERMILJO)
    assert (n.id, n.hemlighet) == (SERVER_ID, SERVER_HEMLIGHET)
    n = las_nycklar_for_konto(
        "ignoreras", konfig={"konto": {"inloggning": "av"}}, miljo=SERVERMILJO
    )
    assert n.id == SERVER_ID


def test_inloggning_pa_anvander_kontots_nyckel_och_aldrig_servernyckeln(
    forrad: tuple[NyckelForrad, str, str],
) -> None:
    f, anna, bo = forrad
    pa = {"konto": {"inloggning": "pa"}}
    n = las_nycklar_for_konto(anna, konfig=pa, miljo=SERVERMILJO, forrad=f)
    assert (n.id, n.hemlighet, n.metod) == (ANV_ID, ANV_HEMLIGHET, "oauth2")
    # Bo har ingen nyckel: fel, trots att servernyckeln finns i miljön.
    with pytest.raises(NyckelFel, match="ingen Lantmäteri-nyckel"):
        las_nycklar_for_konto(bo, konfig=pa, miljo=SERVERMILJO, forrad=f)
    # Inget konto angivet: fel i stället för att falla tillbaka på servernyckeln.
    with pytest.raises(NyckelFel, match="--konto"):
        las_nycklar_for_konto(None, konfig=pa, miljo=SERVERMILJO, forrad=f)


def test_felen_innehaller_inga_nyckelvarden(forrad: tuple[NyckelForrad, str, str]) -> None:
    f, anna, _ = forrad
    annan = NyckelForrad(f._register, Fernet.generate_key().decode())  # fel hemlighet
    with pytest.raises(NyckelFel) as fel:
        las_nycklar_for_konto(anna, konfig={"konto": {"inloggning": "pa"}}, forrad=annan)
    for hemligt in (ANV_ID, ANV_HEMLIGHET, SERVER_ID, SERVER_HEMLIGHET):
        assert hemligt not in str(fel.value)


def test_oppnar_kontodatabasen_ur_konfigurationen(tmp_path: Path) -> None:
    sokvag = tmp_path / "konto.sqlite"
    hemlighet = Fernet.generate_key().decode()
    reg = Register(Databas(sokvag))
    anna = reg.forbered_konto("anna@example.test")
    NyckelForrad(reg, hemlighet).spara(
        anna,
        "lantmateriet",
        {"klient_id": ANV_ID, "klient_hemlighet": ANV_HEMLIGHET, "metod": "basic"},
    )
    konfig = {
        "konto": {
            "inloggning": "pa",
            "databas": str(sokvag),
            "offentlig_url": "https://floden.test",
            "leverantorer": {"google": {"aktiv": True, "klient_id": "k", "klient_hemlighet": "h"}},
        }
    }
    miljo = {**SERVERMILJO, HEMLIGHET_MILJOVARIABEL: hemlighet}
    n = las_nycklar_for_konto(anna, konfig=konfig, miljo=miljo)
    assert (n.id, n.hemlighet, n.metod) == (ANV_ID, ANV_HEMLIGHET, "basic")
    # Utan hemligheten är nyckelhanteringen avstängd: fel, inte servernyckeln.
    with pytest.raises(NyckelFel, match="inte påslagen"):
        las_nycklar_for_konto(anna, konfig=konfig, miljo=SERVERMILJO)
    # Nycklarna syns aldrig i objektets text.
    assert ANV_HEMLIGHET not in repr(n) and ANV_HEMLIGHET not in str(n)
