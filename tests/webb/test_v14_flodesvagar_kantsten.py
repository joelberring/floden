"""Webbtjänst- och GeoJSON-tester för kantstensmedvetna flödesvägar (Etapp 1B / PR-B).

Verifierar:
1. `FragaTjanst._ladda_avrinningsnat_underlag` läser in och deduplicerar `brytlinjer.gpkg` över
   både enstaka och flera K1-rutor.
2. `FragaTjanst.vektor(..., "flodesvagar")` använder `brytlinjer.gpkg` (flödesvägar följer
   kantsten i stället för att läcka över till en lägre gård), skriver `avrinningsnat.<scen>.v3.npz`
   och återanvänder `.v3.npz` vid reglagebyte (`min_yta_m2`) med bevarad `typ = "rannsten"`.
3. `analysvektor.flodesvagar_fran_strackor` skriver additivt `properties["typ"]` (`"rannsten"` /
   `"markstrak"`).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import numpy as np

from sankgraf.avrinningsnat import Stracka
from testsvit.k1_io import BrytlinjePost, skriv_brytlinjer_gpkg, skriv_geotiff
from webb import analysvektor
from webb.fragor import FragaTjanst


def _skriv_gata_k1_med_kantsten(
    mapp: Path,
    *,
    x_min: float = 150000.0,
    y_max: float = 6580015.0,
    hojd: int = 60,
    bredd: int = 120,
    dx: float = 0.25,
    skriv_kantsten: bool = True,
) -> float:
    """Skriver en K1-ruta med lutande krönt gata, 12 cm sydlig kantsten och lägre gård."""
    mapp.mkdir(parents=True, exist_ok=True)
    r, c = np.mgrid[0:hojd, 0:bredd].astype(np.float64)
    rad_kant = 35
    gata = r <= rad_kant
    z = np.empty((hojd, bredd), dtype=np.float32)
    z[gata] = (
        10.0 + 0.015 * (rad_kant - r[gata]) + 0.004 * (bredd - 1 - c[gata])
    ).astype(np.float32)
    z[~gata] = (9.4 - 0.01 * (r[~gata] - (rad_kant + 1))).astype(np.float32)
    osak = np.full((hojd, bredd), 0.02, dtype=np.float32)
    slag = np.full((hojd, bredd), 1, dtype=np.uint8)
    skriv_geotiff(mapp / "mark.tif", z, x_min=x_min, y_max=y_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "markslag.tif", slag, x_min=x_min, y_max=y_max, pixelstorlek=dx)

    y_kant = y_max - (rad_kant + 1) * dx
    if skriv_kantsten:
        kant = BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min, y_kant), (x_min + bredd * dx, y_kant)),
        )
        skriv_brytlinjer_gpkg(mapp / "brytlinjer.gpkg", [kant])
    return y_kant


def _skapa_korning_med_kantsten(
    tmp_path: Path, *, exekutor: str = "trad"
) -> tuple[FragaTjanst, str, float]:
    k1 = tmp_path / "bas" / "r1"
    y_kant = _skriv_gata_k1_med_kantsten(k1)
    fragor_dir = tmp_path / "fragor"
    fragor_dir.mkdir(parents=True, exist_ok=True)
    (fragor_dir / "gata-vatten.json").write_text(
        json.dumps(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "gata-vatten",
                "beskrivning": "Gata med kantsten",
                "scenarier": {"fore": {"bas": str(tmp_path / "bas")}},
                "analyser": [
                    {
                        "id": "skyfall",
                        "losare": "vatten.statisk_fyllning",
                        "scenarier": ["fore"],
                        "parametrar": {"regn_mm": 40.0, "takvatten": False},
                    }
                ],
                "jamforelser": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[fragor_dir],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        exekutor=exekutor,
    )
    status, _ = tjanst.kor_synkront("gata-vatten", "k-kantsten")
    assert status["status"] == "klar"
    return tjanst, "k-kantsten", y_kant


def test_ladda_avrinningsnat_underlag_laser_och_deduplicerar_brytlinjer(tmp_path: Path) -> None:
    """`_ladda_avrinningsnat_underlag` läser `brytlinjer.gpkg` och deduplicerar mellan rutor."""
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    _skriv_gata_k1_med_kantsten(r1, x_min=150000.0, y_max=6580010.0, hojd=40, bredd=40, dx=0.25)
    _skriv_gata_k1_med_kantsten(r2, x_min=150010.0, y_max=6580010.0, hojd=40, bredd=40, dx=0.25)

    # Skriv samma delade brytlinje i båda rutorna + en unik per ruta -> ska bli 2 (inte 3)
    delad = BrytlinjePost(
        typ="kantsten",
        hojd_m=0.12,
        koordinater=((150000.0, 6580001.0), (150020.0, 6580001.0)),
    )
    unik_r2 = BrytlinjePost(
        typ="mur",
        hojd_m=1.0,
        koordinater=((150012.0, 6580008.0), (150018.0, 6580008.0)),
    )
    skriv_brytlinjer_gpkg(r1 / "brytlinjer.gpkg", [delad])
    skriv_brytlinjer_gpkg(r2 / "brytlinjer.gpkg", [delad, unik_r2])

    _, _, _, _, bryt_1 = FragaTjanst._ladda_avrinningsnat_underlag(  # noqa: SLF001
        [r1], takvatten=False, takvatten_andel_ledning=0.0
    )
    assert len(bryt_1) == 1
    assert bryt_1[0].typ == "kantsten"

    _, _, _, _, bryt_2 = FragaTjanst._ladda_avrinningsnat_underlag(  # noqa: SLF001
        [r1, r2], takvatten=False, takvatten_andel_ledning=0.0
    )
    assert len(bryt_2) == 2
    assert {b.typ for b in bryt_2} == {"kantsten", "mur"}


def test_vektor_flodesvagar_kantsten_v3_cache_och_typ_egenskap(tmp_path: Path) -> None:
    """`vektor(..., 'flodesvagar')` respekterar kantsten, skriver `.v3.npz` och sätter `typ`."""
    tjanst, kid, y_kant = _skapa_korning_med_kantsten(tmp_path, exekutor="trad")
    try:
        v1 = tjanst.vektor(kid, "fore", "flodesvagar", min_yta_m2=25.0)
        assert v1["slag"] == "flodesvagar"
        assert len(v1["features"]) > 0

        bas_npz = tmp_path / "korningar" / kid / "webb" / "avrinningsnat.fore.v3.npz"
        assert bas_npz.is_file()
        assert not (tmp_path / "korningar" / kid / "webb" / "avrinningsnat.fore.v2.npz").exists()

        rannstenar = [f for f in v1["features"] if f["properties"].get("typ") == "rannsten"]
        assert rannstenar, "Förväntade rännstenssträcka längs kantstenen"
        huvud = max(rannstenar, key=lambda f: f["properties"]["tillrinning_m2"])
        coords = huvud["geometry"]["coordinates"]
        assert all(y > y_kant for _, y in coords), "Flödesvägen får inte läcka över kantstenen"

        # Reglagebyte använder `.v3.npz` och behåller `typ == 'rannsten'` via `brytlinjer.gpkg`
        with mock.patch(
            "sankgraf.avrinningsnat.berakna_avrinningsfalt",
            side_effect=AssertionError("Borde återanvända avrinningsnat.fore.v3.npz"),
        ):
            v2 = tjanst.vektor(kid, "fore", "flodesvagar", min_yta_m2=15.0)
        assert any(f["properties"].get("typ") == "rannsten" for f in v2["features"])
    finally:
        tjanst.stang()


def test_analysvektor_flodesvagar_fran_strackor_exponerar_typ() -> None:
    """`analysvektor.flodesvagar_fran_strackor` skriver additivt `typ` i GeoJSON `properties`."""
    vagar = [
        Stracka(
            0, ((150000.0, 6580000.0), (150010.0, 6580000.0)), 600.0, 1, 10.0, 0.01, "rannsten"
        ),
        Stracka(1, ((150000.0, 6580020.0), (150010.0, 6580020.0)), 900.0, 2, 10.0, 0.02),
    ]
    fc = analysvektor.flodesvagar_fran_strackor(vagar, min_yta_m2=500.0)
    assert fc["features"][0]["properties"]["typ"] == "rannsten"
    assert fc["features"][1]["properties"]["typ"] == "markstrak"
