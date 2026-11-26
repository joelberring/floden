"""Deterministiska regressionstester för Våg 1: K-B2 (inkrementell HAV) & K-B3 (flerrut)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from gemensamt.hash import sha256_fil
from karna.resultatcache import ResultatCache
from karna.rutscenario import materialisera_scenario
from karna.scenario import Andring, Planscenario, skriv_scenario
from karna.utredning import ScenarioSpec
from lapp.kontrakt import (
    Gatusektion,
    LappFel,
    LappGata,
    LappMagasin,
    ProjektLapp,
)
from lapp.lokal_omrakning import uppdatera_sankgraf_lokalt
from lapp.tillampa import rastrera_parametrisk_gata, tillampa_lapp
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import HAV_NOD_ID, MARKSLAG_BYGGNAD
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo, skriv_geotiff


def _geo(
    hojd: int,
    bredd: int,
    *,
    x_min: float = 150000.0,
    y_max: float = 6580010.0,
    dx: float = 1.0,
) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )


# --- K-B2: Inkrementell K21-sänkgraf vid marksänkning & byggnadsrivning i HAV ----------------


def test_kb2_marksankning_i_hav_ger_exakt_samma_klassning_som_full_10x10() -> None:
    """10×10-rutnät: marksänkning och rivning i HAV-sluttning ska ge 0 felklassade celler."""
    geo = _geo(10, 10, dx=1.0)
    # Västra delen (kol 0..3): egen oberoende sänka vid (5, 2) så basgrafen har en bevarad nod
    # Östra delen (kol 4..9): sluttar jämnt från öst (kol 9, z=19) västerut/norrut ut till rand
    # så hela östra sluttningen är HAV i basen!
    z = np.full((10, 10), 15.0, dtype=np.float32)
    z[0, :] = 10.0
    z[-1, :] = 10.0
    z[:, 0] = 10.0
    z[:, -1] = 10.0
    # Västra bevarade sänkan (rad 4..6, kol 2..3), botten 12.0, utloppssadel västerut vid 13.5,
    # avskild från östra sluttningen av en vattendelare vid kol 4 = 16.0 m
    z[4:7, 2:4] = 12.0
    z[5, 1] = 13.5
    z[1:9, 4] = 16.0
    # Östra HAV-sluttningen: sluttar jämnt ned mot norra randen (rad 0 = 10.0)
    for r in range(1, 9):
        for c in range(5, 9):
            z[r, c] = np.float32(10.5 + 0.5 * r + 0.1 * (c - 5))
    # Hög rygg vid (3, 6) = 18.0 (eller byggnad) — i basen har grannar lägre z än (3, 6)!
    z[3, 6] = np.float32(18.0)

    osak = np.full((10, 10), 0.02, dtype=np.float32)
    slag = np.full((10, 10), 1, dtype=np.uint8)
    bas_graf = bygg_sankgraf(z, osak, slag, geo)
    assert len(bas_graf.lovnoder()) == 1
    assert int(bas_graf.cell_till_nod[5, 6]) == HAV_NOD_ID

    # Sänk (3, 6) från 18,0 m till 9,0 m -> fångar uppströms HAV-celler i kol 5..8, rad 4..8!
    ny_z = z.copy()
    ny_z[3, 6] = np.float32(9.0)
    andrad = np.zeros((10, 10), dtype=np.bool_)
    andrad[3, 6] = True

    lokal = uppdatera_sankgraf_lokalt(bas_graf, ny_z, osak, slag, andrad)
    full = bygg_sankgraf(ny_z, osak, slag, geo)

    assert lokal.full_omrakning is False
    assert lokal.antal_ateranvanda_noder >= 1
    # Exakt samma HAV/sänka/byggnad-klassning på alla 100 celler!
    np.testing.assert_array_equal(
        lokal.ny_graf.cell_till_nod == HAV_NOD_ID,
        full.cell_till_nod == HAV_NOD_ID,
    )
    # Verifiera att nya sänkan verkligen fångade många uppströms HAV-celler
    ny_sanka_id = int(full.cell_till_nod[3, 6])
    assert ny_sanka_id >= 0
    assert int(np.count_nonzero(full.cell_till_nod == ny_sanka_id)) >= 10
    # Och statisk fyllning matchar maskinexakt
    for regn_m in (0.1, 1.0, 4.0):
        st_l = statisk_fyllning(lokal.ny_graf, regn_m)
        st_f = statisk_fyllning(full, regn_m)
        assert float(np.max(np.abs(st_l.djup_m - st_f.djup_m))) < 1e-6


def test_kb2_rivning_av_byggnad_i_hav_ger_exakt_samma_klassning_som_full() -> None:
    """Rivning av byggnad (`MARKSLAG_BYGGNAD -> 1`) som exponerar en grop fångar uppströms HAV."""
    geo = _geo(10, 10, dx=1.0)
    z = np.full((10, 10), 15.0, dtype=np.float32)
    z[0, :] = 10.0
    z[-1, :] = 10.0
    z[:, 0] = 10.0
    z[:, -1] = 10.0
    z[4:7, 2:4] = 12.0  # bevarad sänka i väst, sadel västerut vid 13.5, vattendelare kol 4 = 16.0
    z[5, 1] = 13.5
    z[1:9, 4] = 16.0
    for r in range(1, 9):
        for c in range(5, 9):
            z[r, c] = np.float32(10.5 + 0.5 * r + 0.05 * (c - 5))
    osak = np.full((10, 10), 0.02, dtype=np.float32)
    slag = np.full((10, 10), 1, dtype=np.uint8)
    # Byggnad vid (2, 6..7)
    slag[2, 6:8] = MARKSLAG_BYGGNAD
    z[2, 6:8] = np.float32(25.0)
    bas_graf = bygg_sankgraf(z, osak, slag, geo)
    assert len(bas_graf.lovnoder()) == 1

    # Riv byggnaden och exponera en låg schaktgrop på 8,5 m
    ny_z = z.copy()
    ny_slag = slag.copy()
    ny_slag[2, 6:8] = 1
    ny_z[2, 6:8] = np.float32(8.5)
    andrad = np.zeros((10, 10), dtype=np.bool_)
    andrad[2, 6:8] = True

    lokal = uppdatera_sankgraf_lokalt(bas_graf, ny_z, osak, ny_slag, andrad)
    full = bygg_sankgraf(ny_z, osak, ny_slag, geo)

    assert lokal.full_omrakning is False
    assert lokal.antal_ateranvanda_noder >= 1
    np.testing.assert_array_equal(
        lokal.ny_graf.cell_till_nod == HAV_NOD_ID,
        full.cell_till_nod == HAV_NOD_ID,
    )
    st_l = statisk_fyllning(lokal.ny_graf, 1.5)
    st_f = statisk_fyllning(full, 1.5)
    assert float(np.max(np.abs(st_l.djup_m - st_f.djup_m))) < 1e-6


def test_kb2_deterministisk_stresskontroll_sankning_hojning_och_platan() -> None:
    """Flertalet deterministiska lappoperationer på 12×12-terräng ger 0 avvikelse mot full."""
    rng = np.random.default_rng(20261126)
    geo = _geo(12, 12, dx=1.0)
    osak = np.full((12, 12), 0.02, dtype=np.float32)

    for fall in range(12):
        # Skapa deterministisk terräng med både platåer, sluttningar och minst en sänka
        z = np.round(rng.uniform(10.0, 14.0, size=(12, 12)), 1).astype(np.float32)
        z[0, :] = 10.0
        z[-1, :] = 10.0
        z[:, 0] = 10.0
        z[:, -1] = 10.0
        z[2:4, 2:4] = 8.5  # garanterad sänka i NV
        slag = np.full((12, 12), 1, dtype=np.uint8)
        if fall % 3 == 0:
            slag[6:8, 7:9] = MARKSLAG_BYGGNAD
            z[6:8, 7:9] = 22.0
        bas_graf = bygg_sankgraf(z, osak, slag, geo)

        ny_z = z.copy()
        ny_slag = slag.copy()
        andrad = np.zeros((12, 12), dtype=np.bool_)
        r0 = int(rng.integers(4, 9))
        c0 = int(rng.integers(5, 10))
        if fall % 3 == 0:
            # Riv byggnad -> sänk
            ny_slag[6:8, 7:9] = 1
            ny_z[6:8, 7:9] = np.float32(8.0)
            andrad[6:8, 7:9] = True
        elif fall % 3 == 1:
            # Sänk enstaka/2×2-cell i HAV-området
            ny_z[r0 : r0 + 2, c0 : c0 + 2] = np.float32(7.5)
            andrad[r0 : r0 + 2, c0 : c0 + 2] = True
        else:
            # Höj / skapa platå
            ny_z[r0 : r0 + 2, c0 : c0 + 2] = np.float32(13.0)
            andrad[r0 : r0 + 2, c0 : c0 + 2] = True

        lokal = uppdatera_sankgraf_lokalt(bas_graf, ny_z, osak, ny_slag, andrad)
        full = bygg_sankgraf(ny_z, osak, ny_slag, geo)
        np.testing.assert_array_equal(
            lokal.ny_graf.cell_till_nod == HAV_NOD_ID,
            full.cell_till_nod == HAV_NOD_ID,
            err_msg=f"HAV-avvikelse i slumpfall {fall}",
        )
        st_l = statisk_fyllning(lokal.ny_graf, 1.0)
        st_f = statisk_fyllning(full, 1.0)
        assert float(np.max(np.abs(st_l.djup_m - st_f.djup_m))) < 1e-6


# --- K-B3: Flerruts-magasin och parametrisk gata utanför/över rutgräns ----------------------


def _standard_sektion() -> Gatusektion:
    return Gatusektion(
        namn="standard-12m",
        total_bredd_m=12.0,
        korbana_bredd_m=6.5,
        gangbana_bredd_m=2.75,
        kantsten_hojd_m=0.12,
        tvarfall_korbana_andel=0.025,
        tvarfall_gangbana_andel=0.02,
        brunnsavstand_m=30.0,
    )


def test_kb3_magasin_och_gata_utanfor_ruta_ger_oforandrad_och_tomma_brytlinjer() -> None:
    """`magasin` och `gata` utanför rutans bbox kastar inte fel och ger inga brytlinjer."""
    geo = _geo(40, 40, x_min=150000.0, y_max=6580020.0, dx=0.5)
    z = np.full((40, 40), 10.0, dtype=np.float32)
    # NaN längs randen för att bevisa att _sampla_markhojd inte anropas för en gata utanför rutan!
    z[:, -1] = np.nan
    osak = np.full((40, 40), 0.02, dtype=np.float32)
    slag = np.full((40, 40), 4, dtype=np.uint8)
    sek = _standard_sektion()

    gata_borta = LappGata(
        gata_id="gata-annan-ruta",
        mittlinje=((150200.0, 6580010.0), (150250.0, 6580010.0)),
        sektion="standard-12m",
    )
    gatu_res = rastrera_parametrisk_gata(gata_borta, sek, z, slag, geo)
    assert not bool(np.any(gatu_res.tackt_mask))
    assert gatu_res.brytlinjer == ()

    mag_borta = LappMagasin(
        magasin_id="mag-annan-ruta",
        polygon=(
            (150200.0, 6580002.0),
            (150210.0, 6580002.0),
            (150210.0, 6580012.0),
            (150200.0, 6580012.0),
        ),
        volym_m3=25.0,
    )
    lapp = ProjektLapp(
        lapp_id="flerrut-lapp",
        bas_recept=("a" * 64,),
        gator=(gata_borta,),
        magasin=(mag_borta,),
    )
    till = tillampa_lapp(
        z, osak, slag, geo, lapp, bas_recept_sha256="a" * 64, sektioner={"standard-12m": sek}
    )
    assert not bool(np.any(till.andrad_mask))
    assert till.kantstens_brytlinjer == ()
    assert till.magasin_botten_rh2000 == {}


def test_kb3_magasin_over_rutgrans_och_gata_klippt_till_ruta() -> None:
    """Gränsöverskridande magasin utan `botten_rh2000` ger tydligt fel; med `botten_rh2000`
    fungerar det; delvis överlappande gata klipper sina kantstensbrytlinjer till rutan."""
    geo = _geo(40, 40, x_min=150000.0, y_max=6580020.0, dx=0.5)
    z = np.full((40, 40), 10.0, dtype=np.float32)
    osak = np.full((40, 40), 0.02, dtype=np.float32)
    slag = np.full((40, 40), 4, dtype=np.uint8)
    sek = _standard_sektion()

    # 1. Magasin som korsar östra rutgränsen (x_max = 150020.0) med enbart volym_m3 -> tydligt fel
    mag_kors = LappMagasin(
        magasin_id="mag-grans",
        polygon=(
            (150016.0, 6580005.0),
            (150024.0, 6580005.0),
            (150024.0, 6580015.0),
            (150016.0, 6580015.0),
        ),
        volym_m3=20.0,
    )
    lapp_fel = ProjektLapp(lapp_id="kors-vol", bas_recept=("b" * 64,), magasin=(mag_kors,))
    with pytest.raises(LappFel, match="botten_rh2000"):
        tillampa_lapp(z, osak, slag, geo, lapp_fel, bas_recept_sha256="b" * 64)

    # 2. Samma magasin med explicit botten_rh2000 fungerar rent utan dubbelgrävning
    mag_ok = LappMagasin(
        magasin_id="mag-grans",
        polygon=mag_kors.polygon,
        volym_m3=0.0,
        botten_rh2000=8.75,
    )
    lapp_ok = ProjektLapp(lapp_id="kors-botten", bas_recept=("b" * 64,), magasin=(mag_ok,))
    till_ok = tillampa_lapp(z, osak, slag, geo, lapp_ok, bas_recept_sha256="b" * 64)
    assert till_ok.magasin_botten_rh2000 == {"mag-grans": 8.75}
    assert bool(np.any(till_ok.andrad_mask))

    # 3. Gata som korsar östra rutgränsen klipper sina kantstens_brytlinjer till [x_min, x_max]
    gata_kors = LappGata(
        gata_id="gata-kors",
        mittlinje=((150010.0, 6580010.0), (150030.0, 6580010.0)),
        sektion="standard-12m",
        profil_rh2000=(10.0, 10.0),
    )
    gatu_res = rastrera_parametrisk_gata(gata_kors, sek, z, slag, geo)
    assert len(gatu_res.brytlinjer) == 2
    for b in gatu_res.brytlinjer:
        assert all(geo.x_min - 1e-6 <= p[0] <= geo.x_max + 1e-6 for p in b.koordinater)
        assert all(geo.y_min - 1e-6 <= p[1] <= geo.y_max + 1e-6 for p in b.koordinater)


def test_kb3_flerruts_materialisering_bevarar_oberord_ruta_vid_magasin_och_gata(
    tmp_path: Path,
) -> None:
    """I ett 2-rutsscenario med magasin och gata i ruta A förblir ruta B helt oberörd."""
    bas_rot = tmp_path / "bas_2r"
    dx = 0.5
    recept_shas: list[str] = []
    mappar: dict[str, Path] = {}
    for rid, x0 in (("mm_150000_6580000", 150000.0), ("mm_150020_6580000", 150020.0)):
        rm = bas_rot / rid
        rm.mkdir(parents=True)
        z = np.full((40, 40), 10.0, dtype=np.float32)
        skriv_geotiff(rm / "mark.tif", z, x_min=x0, y_max=6580020.0, pixelstorlek=dx)
        skriv_geotiff(
            rm / "osakerhet.tif",
            np.full((40, 40), 0.02, dtype=np.float32),
            x_min=x0,
            y_max=6580020.0,
            pixelstorlek=dx,
        )
        skriv_geotiff(
            rm / "markslag.tif",
            np.full((40, 40), 4, dtype=np.uint8),
            x_min=x0,
            y_max=6580020.0,
            pixelstorlek=dx,
        )
        rec = rm / "recept.json"
        rec.write_text(json.dumps({"ruta_id": rid}), encoding="utf-8")
        recept_shas.append(sha256_fil(rec))
        mappar[rid] = rm

    sc = Planscenario(
        scenario_id="flerrut-mag-gata",
        bas_recept=tuple(recept_shas),
        andringar=(
            Andring(
                typ="magasin",
                andring_id="mag-a",
                geometri={
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [150002.0, 6580002.0],
                            [150008.0, 6580002.0],
                            [150008.0, 6580008.0],
                            [150002.0, 6580008.0],
                            [150002.0, 6580002.0],
                        ]
                    ],
                },
                egenskaper={"volym_m3": 10.0},
            ),
            Andring(
                typ="gata",
                andring_id="gata-a",
                geometri={
                    "type": "LineString",
                    "coordinates": [[150002.0, 6580015.0], [150012.0, 6580015.0]],
                },
                egenskaper={"sektion": "standard-12m", "profil_rh2000": [10.0, 10.0]},
            ),
        ),
    )
    k9_fil = skriv_scenario(sc, tmp_path / "scen.k9.json")
    res = materialisera_scenario(
        ScenarioSpec(id="s", bas=bas_rot, k9=(k9_fil,)),
        ResultatCache(tmp_path / "cache"),
    )
    ut_mappar = {r.ruta_id: r.mapp for r in res.rutor}
    # Ruta A har ändrats, men ruta B behåller exakt basens katalog (ingen cache-invalidering!)
    assert ut_mappar["mm_150000_6580000"] != mappar["mm_150000_6580000"]
    assert ut_mappar["mm_150020_6580000"] == mappar["mm_150020_6580000"]
