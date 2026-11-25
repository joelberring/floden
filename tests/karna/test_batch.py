"""Tester för återupptagbar rut-kö och dataskyddsspärr (`karna.batch` + `floden forberakna`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from floden.cli import main as floden_main
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, las_manifest
from karna.batch import (
    BatchFel,
    BatchKonfig,
    RutUtdata,
    generera_rutor_for_omrade,
    kontrollera_dataskydd,
    kor_batch_rutor,
)
from markmodell.rutnat import Ruta


def _skriv_2x2_omrade_geojson(sokvag: Path) -> Path:
    """Skriver en 1000×1000 m polygon i EPSG:3011 som täcker exakt 2×2 standardrutor (500 m)."""
    poly = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"namn": "test_2x2"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [150010.0, 6580010.0],
                            [150990.0, 6580010.0],
                            [150990.0, 6580990.0],
                            [150010.0, 6580990.0],
                            [150010.0, 6580010.0],
                        ]
                    ],
                },
            }
        ],
    }
    sokvag.write_text(json.dumps(poly), encoding="utf-8")
    return sokvag


def test_generera_rutor_2x2_och_aterupptagning_efter_avbrott(tmp_path: Path) -> None:
    omrade = _skriv_2x2_omrade_geojson(tmp_path / "omrade.geojson")
    rutor = generera_rutor_for_omrade(omrade, rutstorlek_m=500.0, overlapp_m=25.0, upplosning_m=1.0)
    assert len(rutor) == 4
    assert [r.namn for r in rutor] == [
        "mm_150000_6580000",
        "mm_150000_6580500",
        "mm_150500_6580000",
        "mm_150500_6580500",
    ]

    utdata = tmp_path / "cache"
    konfig = BatchKonfig(
        moduler=("vatten2d", "dagsljus"),
        utdata_rot=utdata,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        indata_filer=(omrade,),
    )

    anrop_pass1: list[str] = []

    def korare_med_avbrott(
        ruta: Ruta, arbetsmapp: Path, _konfig: BatchKonfig, plats: str
    ) -> RutUtdata:
        anrop_pass1.append(ruta.namn)
        # Skriv en halvskriven fil först för att verifiera att tempkatalogen städas vid fel
        utfil = arbetsmapp / "resultat.txt"
        utfil.write_text(f"{ruta.namn}:{plats}\n", encoding="utf-8")
        if len(anrop_pass1) == 3:
            raise RuntimeError("Simulerat avbrott på ruta 3")
        return RutUtdata(
            utdata_filer=(FilPost(roll="resultat", fil="resultat.txt", sha256=sha256_fil(utfil)),),
            matvarden={"berakningsplats": plats},
        )

    # Pass 1: avbryts på ruta 3
    with pytest.raises(RuntimeError, match="Simulerat avbrott"):
        kor_batch_rutor(rutor, konfig, rutkorare=korare_med_avbrott, avbryt_vid_fel=True)

    # Ruta 1 och 2 ska vara kompletta, ruta 3 får INTE ha halvskriven katalog eller tmp kvar
    assert (utdata / rutor[0].namn / "run.json").is_file()
    assert (utdata / rutor[1].namn / "run.json").is_file()
    assert not (utdata / rutor[2].namn).exists()
    assert not list(utdata.glob(".tmp_*"))

    # Pass 2: återuppta – ruta 1 och 2 ska hoppas över (0 omberäkning), ruta 3 och 4 beräknas
    anrop_pass2: list[str] = []

    def korare_ok(ruta: Ruta, arbetsmapp: Path, _konfig: BatchKonfig, plats: str) -> RutUtdata:
        anrop_pass2.append(ruta.namn)
        utfil = arbetsmapp / "resultat.txt"
        utfil.write_text(f"{ruta.namn}:{plats}\n", encoding="utf-8")
        return RutUtdata(
            utdata_filer=(FilPost(roll="resultat", fil="resultat.txt", sha256=sha256_fil(utfil)),),
            matvarden={"berakningsplats": plats},
        )

    status = kor_batch_rutor(rutor, konfig, rutkorare=korare_ok)
    assert len(status.hoppade) == 2
    assert len(status.klara) == 2
    assert len(status.felade) == 0
    assert anrop_pass2 == [rutor[2].namn, rutor[3].namn]

    # Kontrollera K0-manifest och batch_status.json
    for r in rutor:
        m = las_manifest(utdata / r.namn / "run.json")
        assert m.kontrakt == "K0/v1"
        assert m.parametrar["berakningsplats"] == "cpu_f64"

    status_json = json.loads((utdata / "batch_status.json").read_text(encoding="utf-8"))
    assert status_json["totalt_rutor"] == 4
    assert status_json["aterstaende"] == 0
    assert status_json["berakningsplats"] == "cpu_f64"


def test_dataskyddssparr_vagrar_icke_oppen_data_i_colab() -> None:
    colab_miljo = {"COLAB_RELEASE_TAG": "release-colab-20261125"}
    # Öppen data tillåts
    kontrollera_dataskydd("oppen", miljo=colab_miljo)
    # Intern och skyddad data vägras
    with pytest.raises(BatchFel, match="Dataskyddsspärr"):
        kontrollera_dataskydd("skyddad", miljo=colab_miljo)
    with pytest.raises(BatchFel, match="Dataskyddsspärr"):
        kontrollera_dataskydd("intern", miljo=colab_miljo)

    # Lokalt (utan molnvariabel) tillåts alla klasser
    kontrollera_dataskydd("skyddad", miljo={})


def test_cli_forberakna_torrkor_och_kor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    omrade = _skriv_2x2_omrade_geojson(tmp_path / "omrade.geojson")
    utdata = tmp_path / "ut"

    # Torrkörning
    rc = floden_main(
        ["forberakna", "--omrade", str(omrade), "--utdata", str(utdata), "--torrkor"]
    )
    assert rc == 0
    assert not utdata.exists()

    # Riktig körning (CPU)
    rc2 = floden_main(
        [
            "forberakna",
            "--omrade",
            str(omrade),
            "--moduler",
            "vatten2d,dagsljus",
            "--enhet",
            "cpu",
            "--utdata",
            str(utdata),
            "--dataklass",
            "oppen",
        ]
    )
    assert rc2 == 0
    assert (utdata / "batch_status.json").is_file()

    # Dataskyddsspärr via CLI när COLAB_RELEASE_TAG är satt
    monkeypatch.setenv("COLAB_RELEASE_TAG", "release-colab-test")
    rc3 = floden_main(
        [
            "forberakna",
            "--omrade",
            str(omrade),
            "--utdata",
            str(utdata),
            "--dataklass",
            "skyddad",
        ]
    )
    assert rc3 == 2
