"""`floden data lantmateriet` och `floden omrade bygg` via kommandoraden (falsk server)."""

from __future__ import annotations

from pathlib import Path

import pytest

from floden.cli import main
from lantmateriet.omrade import las_omrade
from lantmateriet.stac import wgs84_bbox
from tests.lantmateriet.falsk_server import HEMLIGHET, KLIENT_ID, FalskLm
from tests.lantmateriet.test_bygg import skriv_laser
from tests.lantmateriet.test_hamta import _feat, skriv_omrade, zip_med_gpkg


def _miljo(lm: FalskLm, monkeypatch: pytest.MonkeyPatch) -> None:
    t = lm.tjanster()
    monkeypatch.setenv("LANTMATERIET_KLIENT_ID", KLIENT_ID)
    monkeypatch.setenv("LANTMATERIET_KLIENT_HEMLIGHET", HEMLIGHET)
    monkeypatch.setenv("LANTMATERIET_TOKEN_URL", t.token)
    monkeypatch.setenv("LANTMATERIET_STAC_HOJD_URL", t.hojd)
    monkeypatch.setenv("LANTMATERIET_STAC_BILD_URL", t.bild)
    monkeypatch.setenv("LANTMATERIET_STAC_VEKTOR_URL", t.vektor)


def test_cli_saknade_nycklar_ger_kod_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LANTMATERIET_KLIENT_ID", raising=False)
    monkeypatch.delenv("LANTMATERIET_KLIENT_HEMLIGHET", raising=False)
    monkeypatch.chdir(tmp_path)  # ingen config.local.toml här
    monkeypatch.setattr("lantmateriet.nycklar._REPO_ROT", tmp_path)
    om = skriv_omrade(tmp_path / "in")
    assert main(["data", "lantmateriet", "--omrade", str(om), "--ut", str(tmp_path / "d")]) == 3
    assert "config.local.toml" in capsys.readouterr().err


def test_cli_lista_samlingar(
    lm: FalskLm, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _miljo(lm, monkeypatch)
    assert main(["data", "lantmateriet", "--lista-samlingar"]) == 0
    ut = capsys.readouterr().out
    assert "laserdata-skog" in ut and "ortofoto" in ut and "byggnad" in ut
    assert KLIENT_ID not in ut and HEMLIGHET not in ut


def test_cli_hamta_och_bygg(
    lm: FalskLm,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _miljo(lm, monkeypatch)
    omr = skriv_omrade(tmp_path / "in", storlek=200.0)
    om = las_omrade(omr)
    laser = skriv_laser(tmp_path)
    lm.filer[f"/files/{laser.name}"] = laser.read_bytes()
    bb = wgs84_bbox(om.polygon.buffer(500))
    lm.sidor = [
        [_feat(lm, "t1", bb, "2023-05-01T00:00:00Z", laser.name, "application/octet-stream")]
    ]
    lm.filer["/files/byggnad_kn.zip"] = zip_med_gpkg(tmp_path)
    lm.vektor_features = [
        _feat(lm, "kn", bb, "2025-01-01T00:00:00Z", "byggnad_kn.zip", "application/zip", "byggnad")
    ]
    data, k1 = tmp_path / "data", tmp_path / "k1"
    assert (
        main(
            [
                "data",
                "lantmateriet",
                "--omrade",
                str(omr),
                "--laser",
                "--byggnader",
                "--ut",
                str(data),
            ]
        )
        == 0
    )
    ut = capsys.readouterr().out
    assert "2 hämtade, 0 hoppades över" in ut and "©Lantmäteriet" in ut
    assert (
        main(
            [
                "data",
                "lantmateriet",
                "--omrade",
                str(omr),
                "--laser",
                "--byggnader",
                "--ut",
                str(data),
            ]
        )
        == 0
    )
    assert "0 hämtade, 2 hoppades över" in capsys.readouterr().out
    assert (
        main(
            [
                "omrade",
                "bygg",
                "--omrade",
                str(omr),
                "--data",
                str(data),
                "--ut",
                str(k1),
                "--ruta-storlek-m",
                "100",
            ]
        )
        == 0
    )
    assert "Klart: 4 rutor" in capsys.readouterr().out
    assert (k1 / "mm_151000_6580000" / "mark.tif").is_file()
    assert (
        main(
            [
                "omrade",
                "bygg",
                "--omrade",
                str(omr),
                "--data",
                str(data),
                "--ut",
                str(tmp_path / "en"),
                "--en-ruta",
            ]
        )
        == 0
    )
    assert "Klart: 1 rutor" in capsys.readouterr().out
    # fel i data ger kod 2 och ett läsbart meddelande, inte ett spår
    assert (
        main(
            [
                "omrade",
                "bygg",
                "--omrade",
                str(omr),
                "--data",
                str(tmp_path / "tom"),
                "--ut",
                str(k1),
            ]
        )
        == 2
    )
    assert "floden data lantmateriet" in capsys.readouterr().err


def test_cli_fel_nycklar_ger_kod_3(
    lm: FalskLm, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _miljo(lm, monkeypatch)
    monkeypatch.setenv("LANTMATERIET_KLIENT_HEMLIGHET", "fel-hemlighet-xyz")
    omr = skriv_omrade(tmp_path / "in")
    assert main(["data", "lantmateriet", "--omrade", str(omr), "--ut", str(tmp_path / "d")]) == 3
    err = capsys.readouterr().err
    assert "fel-hemlighet-xyz" not in err and "401" in err
