# ruff: noqa: E501
"""Kommunförvalet för ekologi ligger i `[ekologi]` i data/kommuner/<kommun>.toml (strikt validering i konfig.py)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from ekologi import EkologiFel, kommunforval
from kommuner.konfig import KommunKonfigFel, las_kommun, tolka_kommun

REPO = Path(__file__).resolve().parents[2]
KOMMUNMAPP = REPO / "data" / "kommuner"


def _data(kommun: str = "goteborg") -> dict[str, Any]:
    return tomllib.loads((KOMMUNMAPP / f"{kommun}.toml").read_text(encoding="utf-8"))


def test_den_gamla_filen_ar_borta_och_ingen_kod_laser_den() -> None:
    assert not (REPO / "data" / "ekologi" / "kommuner.toml").exists()
    for fil in (REPO / "src").rglob("*.py"):
        assert "ekologi/kommuner.toml" not in fil.read_text(encoding="utf-8"), fil


def test_stockholm_och_goteborg_har_sitt_forval_i_kommunfilen() -> None:
    sth = las_kommun("stockholm").ekologi
    gbg = las_kommun("goteborg").ekologi
    assert sth is not None and gbg is not None
    assert sth.forval_lokalt == ("barrskog-stockholm-2022", "adellov-ek-stockholm-2022", "groddjur-stockholm-2022")
    assert "adellov-nacka" in sth.forval_server and gbg.forval_lokalt == ()
    assert gbg.kvalitetskallor == {"mindre-hackspett-nacka": "mindre-hackspett-goteborg-2023"}
    k = kommunforval()
    assert set(k) == {"stockholm", "goteborg"}
    assert k["stockholm"].forval_server == sth.forval_server and k["stockholm"].namn == "Stockholms stad"
    assert k["goteborg"].anmarkning.startswith("Göteborg använder Nackas NMD-nätverk")


def test_ekologi_ar_valfritt_en_kommun_utan_forval_saknas_i_listan(tmp_path: Path) -> None:
    d = _data()
    d.pop("ekologi")
    assert tolka_kommun(d).ekologi is None
    mapp = tmp_path / "data" / "kommuner"
    mapp.mkdir(parents=True)
    # Samma fil utan [ekologi] under ett annat id: kommunen finns men har inget förval.
    text = (KOMMUNMAPP / "goteborg.toml").read_text(encoding="utf-8")
    text = text[: text.index("# --- Ekologi")].replace('id = "goteborg"', 'id = "utanforval"')
    (mapp / "utanforval.toml").write_text(text, encoding="utf-8")
    k = kommunforval(tmp_path)
    assert "utanforval" not in k and "goteborg" in k  # repots kommuner finns kvar, den nya saknar förval


@pytest.mark.parametrize(
    ("andring", "fel"),
    [
        ({"okand": 1}, "okända nycklar"),
        ({"forval_server": "barrskog-nacka"}, "lista med nätverks-id"),
        ({"forval_server": ["Barrskog Nacka"]}, "lista med nätverks-id"),
        ({"forval_server": ["a", "a"]}, "flera gånger"),
        ({"forval_server": [], "forval_lokalt": []}, "minst ett nätverk"),
        ({"forval_lokalt": ["barrskog-nacka"]}, "både forval_server och forval_lokalt"),
        ({"kvalitetskallor": {"inte-i-forvalet": "mindre-hackspett-goteborg-2023"}}, "finns inte i forval_server"),
        ({"kvalitetskallor": {"barrskog-nacka": 5}}, "nätverks-id till nätverks-id"),
        ({"anmarkning": 5}, "anmarkning"),
    ],
)
def test_ekologiavsnittet_valideras_strikt(andring: dict[str, Any], fel: str) -> None:
    d = _data()
    d["ekologi"] = {**d["ekologi"], **andring}
    with pytest.raises(KommunKonfigFel, match=fel):
        tolka_kommun(d)
    d2 = _data()
    d2["ekologi"] = "text"
    with pytest.raises(KommunKonfigFel, match="ska vara ett avsnitt"):
        tolka_kommun(d2)


def test_kommunforval_kontrollerar_natverks_id_mot_natverksfilerna(tmp_path: Path) -> None:
    mapp = tmp_path / "data" / "kommuner"
    mapp.mkdir(parents=True)
    text = (KOMMUNMAPP / "goteborg.toml").read_text(encoding="utf-8").replace('id = "goteborg"', 'id = "provkommun"')
    for server, fel in (
        ('["finns-inte"]', "okänt nätverk"),
        ('["groddjur-stockholm-2022"]', "inget körbart NMD-nätverk"),
        ('["tofsmes-sodertalje"]', "inget körbart NMD-nätverk"),
    ):
        ny = text.replace(
            'forval_server = ["barrskog-nacka", "adellov-nacka", "mindre-hackspett-nacka", "grongoling-nacka"]',
            f"forval_server = {server}",
        ).replace('[ekologi.kvalitetskallor]\n"mindre-hackspett-nacka" = "mindre-hackspett-goteborg-2023"\n', "")
        assert ny != text
        (mapp / "provkommun.toml").write_text(ny, encoding="utf-8")
        with pytest.raises(EkologiFel, match=fel):
            kommunforval(tmp_path)
    ny = text.replace("forval_lokalt = []", 'forval_lokalt = ["adellov-nacka"]').replace(
        '"barrskog-nacka", "adellov-nacka", ', '"barrskog-nacka", '
    )
    (mapp / "provkommun.toml").write_text(ny, encoding="utf-8")
    with pytest.raises(EkologiFel, match="inte kommunspecifikt"):
        kommunforval(tmp_path)
