"""Deterministiska regressionstester för P0-buggarna B1, B2 och B3 i `karna.inbyggda`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import tvad.lokal_troghet as lokal_troghet_mod
import tvad.rannsten_1d as rannsten_1d_mod
from gemensamt.regn import generera_cds_regn
from karna.analyslosare import LosarFel
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import las_geotiff, skriv_geotiff

# --- B1: En enda kopplad 1D/2D-körning i `karna.inbyggda.TvadLokalTroghet` -------------------


def test_b1_karna_vatten_2d_kor_endast_en_kopplad_korning_och_avspeglar_rannsten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1: `vatten.2d` (K21) kör exakt EN kopplad 1D/2D-körning när rännsten är aktiv,
    vidarebefordrar `oppna_rander`, och skriver rännstenspåverkat `max_djup.tif`."""
    a5b_indata = Path("testsvit/A5b_sned_gata_3d/indata").resolve()

    anrop_2d = 0
    anrop_1d = 0
    orig_2d = lokal_troghet_mod.kor_2d_lokal_troghet
    orig_1d = rannsten_1d_mod.kor_kopplad_1d_2d

    def _rakna_2d(*args: Any, **kwargs: Any) -> Any:
        nonlocal anrop_2d
        anrop_2d += 1
        return orig_2d(*args, **kwargs)

    def _rakna_1d(*args: Any, **kwargs: Any) -> Any:
        nonlocal anrop_1d
        anrop_1d += 1
        return orig_1d(*args, **kwargs)

    monkeypatch.setattr(lokal_troghet_mod, "kor_2d_lokal_troghet", _rakna_2d)
    monkeypatch.setattr(rannsten_1d_mod, "kor_kopplad_1d_2d", _rakna_1d)

    fraga_med = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "b1-k21-med",
            "scenarier": {"bas": {"bas": str(a5b_indata)}},
            "analyser": [
                {
                    "id": "tvad",
                    "losare": "vatten.2d",
                    "parametrar": {
                        "regn_tid_s": [0.0, 30.0],
                        "regn_mm_h": [180.0, 180.0],
                        "slut_tid_s": 45.0,
                        "oppna_rander": False,
                        "takvatten": False,
                        "rannsten_1d": "auto",
                    },
                }
            ],
        },
        tmp_path,
    )
    res_med = kor_utredning(fraga_med, ResultatCache(tmp_path / "c1"), tmp_path / "ut_med")
    assert anrop_2d == 0, "kor_2d_lokal_troghet ska inte anropas vid aktiv 1D-rännsten."
    assert anrop_1d == 1, "kor_kopplad_1d_2d ska anropas exakt en gång."

    mv_med = res_med.sammanstallning["jobb"][0]["matvarden"]
    assert mv_med["rannsten_1d_aktiv"] is True
    assert mv_med["massbalans_fel"] < 1e-11
    assert mv_med["ut_m3"] == pytest.approx(0.0, abs=1e-12)
    assert float(mv_med["volymbalans"]["volymfel_rel"]) < 1e-11
    assert (tmp_path / "ut_med" / "tvad" / "bas" / "rannsten_flode.geojson").is_file()

    fraga_utan = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "b1-k21-utan",
            "scenarier": {"bas": {"bas": str(a5b_indata)}},
            "analyser": [
                {
                    "id": "tvad",
                    "losare": "vatten.2d",
                    "parametrar": {
                        "regn_tid_s": [0.0, 30.0],
                        "regn_mm_h": [180.0, 180.0],
                        "slut_tid_s": 45.0,
                        "oppna_rander": False,
                        "takvatten": False,
                        "rannsten_1d": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    kor_utredning(fraga_utan, ResultatCache(tmp_path / "c2"), tmp_path / "ut_utan")
    assert anrop_2d == 1
    assert anrop_1d == 1

    djup_med, _ = las_geotiff(tmp_path / "ut_med" / "tvad" / "bas" / "max_djup.tif")
    djup_utan, _ = las_geotiff(tmp_path / "ut_utan" / "tvad" / "bas" / "max_djup.tif")
    assert float(np.max(np.abs(djup_med - djup_utan))) > 0.005

    # Explicit rannsten_1d=True med subgrid_faktor > 1 ger tydligt fel
    fraga_fel = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "b1-k21-fel",
            "scenarier": {"bas": {"bas": str(a5b_indata)}},
            "analyser": [
                {
                    "id": "tvad",
                    "losare": "vatten.2d",
                    "parametrar": {
                        "regn_tid_s": [0.0, 10.0],
                        "regn_mm_h": [60.0, 60.0],
                        "rannsten_1d": True,
                        "subgrid_faktor": 2,
                    },
                }
            ],
        },
        tmp_path,
    )
    with pytest.raises(LosarFel, match="subgrid_faktor"):
        kor_utredning(fraga_fel, ResultatCache(tmp_path / "c3"), tmp_path / "ut_fel")


# --- B2: Massbalans i `tidsserie.json` med takvatten och ledningsavtappning ------------------


def _skapa_b2_ruta_med_byggnad(mapp: Path, *, sluten: bool = False) -> Path:
    """24×24 m ruta (1 m celler) med sänka + byggnad; valfritt helt sluten gård (slutsänka)."""
    mapp.mkdir(parents=True, exist_ok=True)
    h, b = 24, 24
    x_min, y_max = 150000.0, 6580024.0
    if sluten:
        # Slutet ringkvarter runt en 12×12 m gård (rader/kol 6..17), byggnad runt (rader/kol 2..21)
        z = np.full((h, b), 10.0, dtype=np.float32)
        markslag = np.full((h, b), 1, dtype=np.uint8)
        markslag[2:22, 2:22] = MARKSLAG_BYGGNAD
        markslag[6:18, 6:18] = 1
        z[markslag == MARKSLAG_BYGGNAD] = np.float32(20.0)
        z[6:18, 6:18] = np.float32(9.5)
    else:
        yy, xx = np.mgrid[0:h, 0:b]
        r = np.hypot(yy - 12.0, xx - 12.0)
        z = np.where(
            r <= 7.0,
            9.5 + (r / 7.0) * 0.8,
            np.maximum(9.8, 10.3 - 0.1 * (r - 7.0)),
        ).astype(np.float32)
        markslag = np.full((h, b), 1, dtype=np.uint8)
        # Rektangulär byggnad 6×4 m på kanten av sänkan (del av taket in i sänkan, del mot rand)
        markslag[2:6, 8:14] = MARKSLAG_BYGGNAD
        z[2:6, 8:14] = np.float32(20.0)

    osak = np.full((h, b), 0.02, dtype=np.float32)
    skriv_geotiff(mapp / "mark.tif", z, x_min=x_min, y_max=y_max, pixelstorlek=1.0)
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=1.0)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=1.0)
    return mapp


@pytest.mark.parametrize("regntyp", ["block", "cds"])
def test_b2_tidsserie_massbalans_med_takvatten_och_avtappning(
    tmp_path: Path, regntyp: str
) -> None:
    """B2: `tidsserie.json` bevarar exakt massbalans i varje tidssteg med `takvatten=True`,
    `takvatten_andel_ledning=0.25` och `avtappning_mm_h=30.0` (både block- och CDS-regn)."""
    ruta = _skapa_b2_ruta_med_byggnad(tmp_path / f"k1_{regntyp}", sluten=False)
    if regntyp == "block":
        regn_param: dict[str, Any] = {
            "regn_tid_s": [0.0, 900.0],
            "regn_mm_h": [120.0, 120.0],
            "slut_tid_s": 1800.0,
        }
    else:
        cds = generera_cds_regn(100.0, klimatfaktor=1.25, varaktighet_min=30.0)
        regn_param = {
            "regn_tid_s": list(cds.tid_s),
            "regn_mm_h": list(cds.intensitet_mm_h),
            "slut_tid_s": 2400.0,
            "regntyp": "cds",
        }

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"b2-{regntyp}",
            "scenarier": {"bas": {"bas": str(ruta)}},
            "analyser": [
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        **regn_param,
                        "takvatten": True,
                        "takvatten_andel_ledning": 0.25,
                        "avtappning_mm_h": 30.0,
                    },
                }
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / f"c_{regntyp}"), tmp_path / f"ut_{regntyp}")
    mv = res.sammanstallning["jobb"][0]["matvarden"]
    ts = json.loads((tmp_path / f"ut_{regntyp}" / "dyn" / "bas" / "tidsserie.json").read_text())

    # Markarea = 24*24 - 24 = 552 m²; effektiv takyta = 24 * 0.75 = 18 m² -> total = 570 m²
    eff_area_m2 = 552.0 + 24.0 * 0.75
    punkter = ts["punkter"]
    assert len(punkter) >= 25
    for idx, p in enumerate(punkter):
        assert "in_kum_m3" in p and "avtappning_kum_m3" in p
        forv_in_m3 = eff_area_m2 * (float(p["kumulativt_regn_mm"]) / 1000.0)
        assert float(p["in_kum_m3"]) == pytest.approx(forv_in_m3, abs=2e-3)
        summa = float(p["volym_m3"]) + float(p["utflode_kum_m3"]) + float(p["avtappning_kum_m3"])
        assert summa == pytest.approx(float(p["in_kum_m3"]), abs=1e-3), (
            f"Massbalansfel vid steg {idx} (t={p['tid_min']} min): "
            f"in={p['in_kum_m3']} != {p['volym_m3']} + {p['utflode_kum_m3']} + "
            f"{p['avtappning_kum_m3']}"
        )
        if idx > 0:
            fore = punkter[idx - 1]
            assert float(p["avtappning_kum_m3"]) >= float(fore["avtappning_kum_m3"]) - 1e-4
            assert float(p["utflode_kum_m3"]) >= float(fore["utflode_kum_m3"]) - 1e-4

    # Ledningsavtappningen ska vara positiv och INTE sammanblandad med ytutflödet
    sista = punkter[-1]
    assert float(sista["avtappning_kum_m3"]) > 0.5
    assert float(sista["avtappning_kum_m3"]) == pytest.approx(mv["avtappat_m3"], abs=1e-3)
    assert float(sista["utflode_kum_m3"]) == pytest.approx(
        mv["volymbalans"]["utflode_m3"], abs=1e-3
    )
    assert float(sista["volym_m3"]) == pytest.approx(mv["lagrat_m3"], abs=1e-3)
    assert float(sista["in_kum_m3"]) == pytest.approx(mv["in_m3"], abs=1e-3)


def test_b2_tidsserie_slutsanka_avtappning_bokfors_ej_som_ytutflode(tmp_path: Path) -> None:
    """B2: I en sluten gård (A11) med avtappning och takvatten av ska ytutflödet vara 0
    för gårdsvattnet och all avtappning ligga i `avtappning_kum_m3`."""
    a11 = Path("testsvit/A11_sluten_gard_avtappning/indata").resolve()
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "b2-a11-slutsanka",
            "scenarier": {"bas": {"bas": str(a11)}},
            "analyser": [
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        "regn_tid_s": [0.0, 900.0],
                        "regn_mm_h": [120.0, 120.0],
                        "slut_tid_s": 2700.0,
                        "avtappning_mm_h": 60.0,
                        "takvatten": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    mv = res.sammanstallning["jobb"][0]["matvarden"]
    ts = json.loads((tmp_path / "ut" / "dyn" / "bas" / "tidsserie.json").read_text())

    # A11 har en 100 m² sluten gård (3,0 m³ avtappning) plus yttermark som rinner till HAV.
    # Verifiera exakt massbalans i varje steg och att sista steg matchar 3,0 m³ avtappning.
    for p in ts["punkter"]:
        summa = float(p["volym_m3"]) + float(p["utflode_kum_m3"]) + float(p["avtappning_kum_m3"])
        assert summa == pytest.approx(float(p["in_kum_m3"]), abs=1e-3)
    sista = ts["punkter"][-1]
    assert float(sista["avtappning_kum_m3"]) == pytest.approx(3.0, abs=1e-3)
    assert float(sista["utflode_kum_m3"]) == pytest.approx(
        mv["volymbalans"]["utflode_m3"], abs=1e-3
    )
    assert float(sista["volym_m3"]) == pytest.approx(0.0, abs=1e-4)

    # Ren sluten skål utan HAV-celler: ytutflödet ska vara EXAKT 0 i varje tidssteg!
    skal = tmp_path / "ren_skal"
    skal.mkdir()
    h, b = 12, 12
    z = np.full((h, b), 12.0, dtype=np.float32)
    z[1:-1, 1:-1] = np.float32(10.0)
    ms = np.full((h, b), 1, dtype=np.uint8)
    ms[0, :] = ms[-1, :] = ms[:, 0] = ms[:, -1] = MARKSLAG_BYGGNAD
    z[ms == MARKSLAG_BYGGNAD] = np.float32(20.0)
    skriv_geotiff(skal / "mark.tif", z, x_min=150000.0, y_max=6580012.0, pixelstorlek=1.0)
    skriv_geotiff(skal / "markslag.tif", ms, x_min=150000.0, y_max=6580012.0, pixelstorlek=1.0)
    skriv_geotiff(
        skal / "osakerhet.tif",
        np.full_like(z, 0.02),
        x_min=150000.0,
        y_max=6580012.0,
        pixelstorlek=1.0,
    )
    fraga_skal = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "b2-ren-skal",
            "scenarier": {"bas": {"bas": str(skal)}},
            "analyser": [
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        "regn_tid_s": [0.0, 600.0],
                        "regn_mm_h": [120.0, 120.0],
                        "slut_tid_s": 1800.0,
                        "avtappning_mm_h": 60.0,
                        "takvatten": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    kor_utredning(fraga_skal, ResultatCache(tmp_path / "cache2"), tmp_path / "ut2")
    ts_skal = json.loads((tmp_path / "ut2" / "dyn" / "bas" / "tidsserie.json").read_text())
    for p in ts_skal["punkter"]:
        assert float(p["utflode_kum_m3"]) == pytest.approx(0.0, abs=1e-9)
        assert float(p["volym_m3"]) + float(p["avtappning_kum_m3"]) == pytest.approx(
            float(p["in_kum_m3"]), abs=1e-3
        )
    assert float(ts_skal["punkter"][-1]["avtappning_kum_m3"]) == pytest.approx(2.0, abs=1e-3)


# --- B3: Sammanfogade `byggnader.gpkg` i flerrut & `vatten.2d` -------------------------------


def _skapa_b3_flerruts_k1_v2(
    rot: Path, *, fall: str
) -> tuple[Path, Path, dict[str, tuple[slice, slice]]]:
    """Bygger en 1-rutsreferens och en 2-ruts K1 v2-mosaik (4 m överlapp) med `byggnader.gpkg`.

    - `fall == "platt"`: rektangulärt platt tak (12×6 m) tvärs över rutgränsen med 4 isolerade tråg.
    - `fall == "radhus"`: två radhus (6×8 m vardera) med gemensam brandvägg exakt på rutgränsen.
    """
    dx = 0.5
    overlapp_m = 4.0
    ov_c = round(overlapp_m / dx)  # 8 celler
    inre_b = 32  # 16 m per ruta -> 32 m totalt (64 celler)
    inre_h = 40  # 20 m totalt
    tot_b = 2 * inre_b
    tot_h = inre_h

    bas_b = tot_b + 2 * ov_c
    bas_h = tot_h + 2 * ov_c
    x_inre_min = 151000.0
    y_inre_max = 6577020.0

    # Yttermark 9.0 m, 1-cells vall 10.5 m runt varje tråg och trågbotten 9.5 m:
    # vallen har brantare fall utåt (1.5 m) än inåt (1.0 m), så kringvatten rinner inte in.
    bas_mark = np.full((bas_h, bas_b), 9.0, dtype=np.float32)
    bas_slag = np.full((bas_h, bas_b), 1, dtype=np.uint8)
    bas_osak = np.full((bas_h, bas_b), 0.02, dtype=np.float32)

    r_mid = bas_h // 2
    c_mid = bas_b // 2  # exakt på rutgränsen!

    if fall == "platt":
        # Hus: 12 m brett (24 celler, c_mid-12..c_mid+12), 6 m högt (12 celler, r_mid-6..r_mid+6)
        r0, r1 = r_mid - 6, r_mid + 6
        c0, c1 = c_mid - 12, c_mid + 12
        e0 = x_inre_min + (c0 - ov_c) * dx
        e1 = x_inre_min + (c1 - ov_c) * dx
        n_top = y_inre_max - (r0 - ov_c) * dx
        n_bot = y_inre_max - (r1 - ov_c) * dx
        poster = [
            ByggnadsPost(
                byggnad_id="b_platt_tak_0001",
                polygon=((e0, n_bot), (e1, n_bot), (e1, n_top), (e0, n_top)),
                takhojd_rh2000=20.0,
                markhojd_rh2000=10.5,
                hojd_m=9.5,
                kalla="test",
                takform="platt",
            )
        ]
    else:
        # Radhus: två 6×8 m hus (12×16 celler) med brandvägg exakt på c_mid (12×8 m totalt)
        r0, r1 = r_mid - 8, r_mid + 8
        c0, c1 = c_mid - 12, c_mid + 12
        e0 = x_inre_min + (c0 - ov_c) * dx
        em = x_inre_min + (c_mid - ov_c) * dx
        e1 = x_inre_min + (c1 - ov_c) * dx
        n_top = y_inre_max - (r0 - ov_c) * dx
        n_bot = y_inre_max - (r1 - ov_c) * dx
        poster = [
            ByggnadsPost(
                byggnad_id="b_radhus_vast_01",
                polygon=((e0, n_bot), (em, n_bot), (em, n_top), (e0, n_top)),
                takhojd_rh2000=20.0,
                markhojd_rh2000=10.5,
                hojd_m=9.5,
                kalla="test",
                takform="sadel",
            ),
            ByggnadsPost(
                byggnad_id="b_radhus_ost_002",
                polygon=((em, n_bot), (e1, n_bot), (e1, n_top), (em, n_top)),
                takhojd_rh2000=20.0,
                markhojd_rh2000=10.5,
                hojd_m=9.5,
                kalla="test",
                takform="sadel",
            ),
        ]

    # Vall (10.5 m) runt de fyra trågen, sedan sänks själva trågen till 9.5 m
    bas_mark[r0 - 7 : r0, c0 - 1 : c1 + 1] = np.float32(10.5)
    bas_mark[r1 : r1 + 7, c0 - 1 : c1 + 1] = np.float32(10.5)
    bas_mark[r0 - 1 : r1 + 1, c0 - 7 : c0] = np.float32(10.5)
    bas_mark[r0 - 1 : r1 + 1, c1 : c1 + 7] = np.float32(10.5)
    bas_mark[r0 - 6 : r0, c0:c1] = np.float32(9.5)  # norr
    bas_mark[r1 : r1 + 6, c0:c1] = np.float32(9.5)  # söder
    bas_mark[r0:r1, c0 - 6 : c0] = np.float32(9.5)  # väst
    bas_mark[r0:r1, c1 : c1 + 6] = np.float32(9.5)  # öst

    bas_slag[r0:r1, c0:c1] = MARKSLAG_BYGGNAD
    bas_mark[r0:r1, c0:c1] = np.float32(20.0)

    # Trågslices i det inre (sammanfogade) koordinatsystemet
    tr = {
        "norr": (slice(r0 - ov_c - 6, r0 - ov_c), slice(c0 - ov_c, c1 - ov_c)),
        "soder": (slice(r1 - ov_c, r1 - ov_c + 6), slice(c0 - ov_c, c1 - ov_c)),
        "vast": (slice(r0 - ov_c, r1 - ov_c), slice(c0 - ov_c - 6, c0 - ov_c)),
        "ost": (slice(r0 - ov_c, r1 - ov_c), slice(c1 - ov_c, c1 - ov_c + 6)),
    }

    # 1. 1-rutsreferens
    ref_mapp = rot / f"ref_{fall}"
    ref_mapp.mkdir(parents=True)
    ref_mark = bas_mark[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
    ref_osak = bas_osak[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
    ref_slag = bas_slag[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
    skriv_geotiff(
        ref_mapp / "mark.tif", ref_mark, x_min=x_inre_min, y_max=y_inre_max, pixelstorlek=dx
    )
    skriv_geotiff(
        ref_mapp / "osakerhet.tif", ref_osak, x_min=x_inre_min, y_max=y_inre_max, pixelstorlek=dx
    )
    skriv_geotiff(
        ref_mapp / "markslag.tif", ref_slag, x_min=x_inre_min, y_max=y_inre_max, pixelstorlek=dx
    )
    skriv_byggnader_gpkg(
        ref_mapp / "byggnader.gpkg",
        poster,
        (x_inre_min, y_inre_max - tot_h * dx, x_inre_min + tot_b * dx, y_inre_max),
    )
    (ref_mapp / "recept.json").write_text(json.dumps({"ruta_id": f"ref_{fall}"}), encoding="utf-8")

    # 2. 2-rutsmosaik med överlappande byggnader.gpkg (samma byggnad_id i båda rutorna!)
    flerrut = rot / f"flerrut_{fall}"
    r_b = inre_b + 2 * ov_c
    for rid, x0, c_start in (
        ("mm_151000_6577000", x_inre_min - overlapp_m, 0),
        ("mm_151016_6577000", x_inre_min + inre_b * dx - overlapp_m, inre_b),
    ):
        rm = flerrut / rid
        rm.mkdir(parents=True)
        skriv_geotiff(
            rm / "mark.tif",
            bas_mark[:, c_start : c_start + r_b],
            x_min=x0,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek=dx,
        )
        skriv_geotiff(
            rm / "osakerhet.tif",
            bas_osak[:, c_start : c_start + r_b],
            x_min=x0,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek=dx,
        )
        skriv_geotiff(
            rm / "markslag.tif",
            bas_slag[:, c_start : c_start + r_b],
            x_min=x0,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek=dx,
        )
        skriv_byggnader_gpkg(
            rm / "byggnader.gpkg",
            poster,
            (x0, y_inre_max + overlapp_m - bas_h * dx, x0 + r_b * dx, y_inre_max + overlapp_m),
        )
        (rm / "recept.json").write_text(
            json.dumps({"ruta_id": rid, "overlapp_m": overlapp_m}), encoding="utf-8"
        )

    return ref_mapp, flerrut, tr


@pytest.mark.parametrize("fall", ["platt", "radhus"])
def test_b3_flerruts_mosaik_och_vatten_2d_bevarar_byggnader_gpkg(
    tmp_path: Path, fall: str
) -> None:
    """B3: 2-rutsmosaik (`statisk_fyllning`, `dynamik`, `vatten.2d`) ger samma takvattenfördelning
    som 1-rutsreferensen för både platt tak med invändig avvattning och radhusgräns."""
    ref_mapp, flerrut_mapp, trag = _skapa_b3_flerruts_k1_v2(tmp_path, fall=fall)
    andel_ledning = 0.4 if fall == "platt" else 0.0
    regn_mm = 20.0  # 0.020 m

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"b3-{fall}",
            "scenarier": {
                "ref": {"bas": str(ref_mapp)},
                "tva_rutor": {"bas": str(flerrut_mapp)},
            },
            "analyser": [
                {
                    "id": "stat",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {
                        "regn_mm": regn_mm,
                        "takvatten": True,
                        "takvatten_andel_ledning": andel_ledning,
                    },
                },
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        "regn_tid_s": [0.0, 300.0],
                        "regn_mm_h": [240.0, 240.0],
                        "takvatten": True,
                        "takvatten_andel_ledning": andel_ledning,
                    },
                },
                {
                    "id": "tvad",
                    "losare": "vatten.2d",
                    "parametrar": {
                        "regn_tid_s": [0.0, 120.0],
                        "regn_mm_h": [600.0, 600.0],
                        "slut_tid_s": 240.0,
                        "oppna_rander": False,
                        "takvatten": True,
                        "takvatten_andel_ledning": andel_ledning,
                    },
                },
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    jobb = {(j["analys"], j["scenario"]): j["matvarden"] for j in res.sammanstallning["jobb"]}

    cell_a = 0.25
    for analys in ("stat", "dyn", "tvad"):
        mv_ref = jobb[(analys, "ref")]
        mv_2r = jobb[(analys, "tva_rutor")]

        # Deduplicering på byggnad_id + bevarade takformer i 2-rutsmosaik och vatten.2d
        assert mv_2r["takvatten_antal_byggnader"] == mv_ref["takvatten_antal_byggnader"]
        assert mv_2r["takvatten_regler"] == mv_ref["takvatten_regler"]
        assert float(mv_2r["volymbalans"]["volymfel_rel"]) < 1e-11
        assert float(mv_ref["volymbalans"]["volymfel_rel"]) < 1e-11

        djup_ref, _ = las_geotiff(tmp_path / "ut" / analys / "ref" / "max_djup.tif")
        djup_2r, _ = las_geotiff(tmp_path / "ut" / analys / "tva_rutor" / "max_djup.tif")
        np.testing.assert_allclose(djup_2r, djup_ref, atol=1e-6, rtol=1e-6)

        v_norr = float(djup_2r[trag["norr"]].sum()) * cell_a
        v_vast = float(djup_2r[trag["vast"]].sum()) * cell_a
        v_ost = float(djup_2r[trag["ost"]].sum()) * cell_a

        if fall == "platt":
            # Platt tak 12×6 m (72 m², 40 % till ledning -> 43.2 m² effektivt, 0.864 m³ regn):
            # Gavlarna (V och Ö) får 1/6 av takvattnet = 0.144 m³ (inte 0 som vid sadel-fallback!).
            assert mv_2r["takvatten_antal_byggnader"] == 1
            assert mv_2r["takvatten_regler"] == {"platt_alla_fasader": 1}
            eget_gavel = (12 * 6 * cell_a) * (regn_mm / 1000.0)  # 18 m² * 0.02 m = 0.36 m³
            assert v_vast > eget_gavel + 0.10
            assert v_ost == pytest.approx(v_vast, rel=1e-3)
        else:
            # Radhusgräns: 2 separata byggnader (inte 1 rasterblock och inte 4 dubbletter!),
            # 100 % av respektive radhus (48 m² * 0.02 m = 0.96 m³) går till V resp. Ö; N får 0!
            assert mv_2r["takvatten_antal_byggnader"] == 2
            assert mv_2r["takvatten_regler"] == {"sadel_takfot": 2}
            eget_gavel = (16 * 6 * cell_a) * (regn_mm / 1000.0)  # 24 m² * 0.02 m = 0.48 m³
            eget_norr = (6 * 24 * cell_a) * (regn_mm / 1000.0)  # 36 m² * 0.02 m = 0.72 m³
            assert v_vast > eget_gavel + 0.80
            assert v_ost == pytest.approx(v_vast, rel=1e-3)
            if analys in ("stat", "dyn"):
                assert v_norr == pytest.approx(eget_norr, abs=1e-4)
            else:
                # 2D har liten tillrinning från platta kanten men inget takvatten i norr
                assert v_norr < eget_norr + 0.25
