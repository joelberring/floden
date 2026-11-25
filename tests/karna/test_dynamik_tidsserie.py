"""Enhetstester för `vatten.dynamik` v3: `tidsserie.json`, `tidssteg.npz` och CDS vs Block."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from gemensamt.regn import generera_blockregn, generera_cds_regn
from karna.analyslosare import hamta_losare
from karna.inbyggda import Dynamik, _skriv_dynamik_tidsserie_och_tidssteg
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from sankgraf.fyllning import kor_statisk_k1_mapp
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import las_geotiff, skriv_geotiff


def _skapa_sank_ruta_med_hus(mapp: Path) -> Path:
    """32 x 32 m (1 m celler) skål med en liten byggnad i hörnet."""
    mapp.mkdir(parents=True, exist_ok=True)
    h, b = 32, 32
    yy, xx = np.mgrid[0:h, 0:b]
    r = np.hypot(yy - 15.5, xx - 15.5)
    z = np.where(r <= 9.0, 9.4 + (r / 9.0) * 1.1, np.maximum(9.8, 10.5 - 0.1 * (r - 9.0))).astype(
        np.float32
    )
    markslag = np.full((h, b), 1, dtype=np.uint8)
    # Byggnad i nordvästra hörnet (ska maskas till 0 i tidssteg.npz)
    markslag[2:6, 2:6] = MARKSLAG_BYGGNAD
    osak = np.full((h, b), 0.05, dtype=np.float32)
    skriv_geotiff(mapp / "mark.tif", z, x_min=150000.0, y_max=6580032.0, pixelstorlek=1.0)
    skriv_geotiff(
        mapp / "markslag.tif", markslag, x_min=150000.0, y_max=6580032.0, pixelstorlek=1.0
    )
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=150000.0, y_max=6580032.0, pixelstorlek=1.0)
    return mapp


def test_dynamik_v3_tidsserie_volymbalans_och_uint16_precision(tmp_path: Path) -> None:
    """`Dynamik` v3 skriver lättviktig `tidsserie.json` och `tidssteg.npz` med < 1 mm fel."""
    ruta_mapp = _skapa_sank_ruta_med_hus(tmp_path / "k1")
    los = hamta_losare("vatten.dynamik")
    assert isinstance(los, Dynamik)
    assert los.version == "3"
    assert los.utdataroller == (
        "max_djup",
        "tid_till_0_1m",
        "tid_till_0_2m",
        "tidsserie",
        "tidssteg",
    )

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "dynamik-v3-test",
            "scenarier": {"bas": {"bas": str(ruta_mapp)}},
            "analyser": [
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        "idf": "stockholmsregn-2024",
                        "aterkomsttid_ar": 100.0,
                        "varaktighet_min": 60.0,
                        "klimatfaktor": 1.25,
                        "regntyp": "cds",
                        "takvatten": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    ut_mapp = tmp_path / "ut" / "dyn" / "bas"
    ts_fil = ut_mapp / "tidsserie.json"
    npz_fil = ut_mapp / "tidssteg.npz"
    assert ts_fil.is_file() and npz_fil.is_file()
    # Lättviktig lagring (< 0,5 MB)
    assert ts_fil.stat().st_size < 100_000
    assert npz_fil.stat().st_size < 500_000

    ts = json.loads(ts_fil.read_text(encoding="utf-8"))
    assert ts["kontrakt"] == "floden-tidsserie/v1"
    assert ts["losare"] == "vatten.dynamik"
    assert ts["regntyp"] == "cds"
    assert ts["varaktighet_min"] == pytest.approx(60.0)
    assert ts["slut_tid_min"] == pytest.approx(60.0)
    assert ts["total_regn_mm"] > 0.0

    punkter = ts["punkter"]
    assert 25 <= len(punkter) <= 60
    assert punkter[0]["tid_min"] == pytest.approx(0.0)
    assert punkter[-1]["tid_min"] == pytest.approx(60.0)
    assert punkter[-1]["kumulativt_regn_mm"] == pytest.approx(ts["total_regn_mm"], abs=1e-3)

    # Monotoniet i kumulativt regn, inga påhittade hastigheter, och exakt volymbalans
    graf, _ = kor_statisk_k1_mapp(ruta_mapp, regn_m=0.0, takvatten=False)
    aktiv_area_m2 = float(np.count_nonzero(graf.markslag != MARKSLAG_BYGGNAD))
    for idx, p in enumerate(punkter):
        assert p["max_hastighet_m_s"] is None
        assert p["vat_yta_03m_m2"] <= p["vat_yta_01m_m2"] + 1e-6
        if idx > 0:
            assert p["tid_min"] > punkter[idx - 1]["tid_min"]
            assert p["kumulativt_regn_mm"] >= punkter[idx - 1]["kumulativt_regn_mm"] - 1e-6
        in_m3 = aktiv_area_m2 * (p["kumulativt_regn_mm"] / 1000.0)
        assert p["volym_m3"] + p["utflode_kum_m3"] == pytest.approx(in_m3, abs=0.05)

    # Slutvolym matchar körningens lagrat_m3
    mv = res.sammanstallning["jobb"][0]["matvarden"]
    assert punkter[-1]["volym_m3"] == pytest.approx(mv["lagrat_m3"], rel=1e-3, abs=0.05)

    # tidssteg_tid_min: 12–16 steg inkl. t=0, topp_tid_min och slut_tid_min
    steg_tider = ts["tidssteg_tid_min"]
    assert 12 <= len(steg_tider) <= 16
    assert steg_tider[0] == pytest.approx(0.0)
    assert steg_tider[-1] == pytest.approx(ts["slut_tid_min"])
    assert any(abs(t - ts["topp_tid_min"]) < 1e-4 for t in steg_tider)

    # Verifiera tidssteg.npz: uint16 mm, < 1 mm kvantiseringsfel, byggnader maskade till 0
    max_djup_tif, _ = las_geotiff(ut_mapp / "max_djup.tif")
    with np.load(npz_fil) as npz:
        stack = npz["djup_mm"]
        tider_s = npz["tider_s"]
        nedsampling = int(npz["nedsampling"])
    assert stack.dtype == np.uint16
    assert nedsampling == 1
    assert stack.shape == (len(steg_tider), 32, 32)
    assert len(tider_s) == len(steg_tider)
    assert np.all(stack[0] == 0)  # t = 0
    assert np.all(stack[:, 2:6, 2:6] == 0)  # byggnadsceller alltid 0

    # Utan avtappning är sista steget lika med max_djup.tif (inom 1 mm kvantisering)
    sista_m = stack[-1].astype(np.float32) / 1000.0
    max_fel_m = float(np.max(np.abs(sista_m - max_djup_tif)))
    assert max_fel_m < 0.001, f"Kvantiseringsfel {max_fel_m:.6f} m >= 1 mm"


def test_cds_ger_distinkt_hogre_intensitetstopp_an_blockregn_for_samma_totalregn(
    tmp_path: Path,
) -> None:
    """CDS-regn och blockregn har samma totalregn men CDS har markant högre intensitetstopp."""
    ruta_mapp = _skapa_sank_ruta_med_hus(tmp_path / "k1")
    cache = ResultatCache(tmp_path / "cache")

    def _kor_typ(regntyp: str) -> dict[str, Any]:
        serie = (
            generera_cds_regn(100.0, klimatfaktor=1.25, varaktighet_min=60.0)
            if regntyp == "cds"
            else generera_blockregn(100.0, klimatfaktor=1.25, varaktighet_min=60.0)
        )
        fraga = fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": f"regntyp-{regntyp}",
                "scenarier": {"bas": {"bas": str(ruta_mapp)}},
                "analyser": [
                    {
                        "id": "dyn",
                        "losare": "vatten.dynamik",
                        "parametrar": {
                            "regn_tid_s": list(serie.tid_s),
                            "regn_mm_h": list(serie.intensitet_mm_h),
                            "regntyp": regntyp,
                            "takvatten": False,
                        },
                    }
                ],
            },
            tmp_path,
        )
        kor_utredning(fraga, cache, tmp_path / f"ut_{regntyp}")
        data: dict[str, Any] = json.loads(
            (tmp_path / f"ut_{regntyp}" / "dyn" / "bas" / "tidsserie.json").read_text(
                encoding="utf-8"
            )
        )
        return data

    cds = _kor_typ("cds")
    block = _kor_typ("block")

    assert cds["regntyp"] == "cds"
    assert block["regntyp"] == "block"
    assert cds["total_regn_mm"] == pytest.approx(block["total_regn_mm"], rel=1e-4)

    cds_max_i = max(float(p["regn_mm_h"]) for p in cds["punkter"])
    block_max_i = max(float(p["regn_mm_h"]) for p in block["punkter"])
    assert cds_max_i > block_max_i * 2.0


def test_tidssteg_nedsamplas_endast_over_1024_px(tmp_path: Path) -> None:
    """Rutor med max(H, W) > 1024 nedsamplas med ceil(max(H, W) / 1024)."""
    mapp = tmp_path / "stor_k1"
    mapp.mkdir()
    h, b = 1026, 8
    z = np.full((h, b), 10.0, dtype=np.float32)
    z[500:510, 2:6] = 9.5
    markslag = np.full((h, b), 1, dtype=np.uint8)
    osak = np.full((h, b), 0.05, dtype=np.float32)
    skriv_geotiff(mapp / "mark.tif", z, x_min=150000.0, y_max=6581026.0, pixelstorlek=1.0)
    skriv_geotiff(
        mapp / "markslag.tif", markslag, x_min=150000.0, y_max=6581026.0, pixelstorlek=1.0
    )
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=150000.0, y_max=6581026.0, pixelstorlek=1.0)

    graf, _ = kor_statisk_k1_mapp(mapp, regn_m=0.0, takvatten=False)
    res = kor_dynamik_handelsestyrd(graf, [0.0, 600.0], [60.0, 60.0])
    ut = tmp_path / "ut"
    ut.mkdir()
    _skriv_dynamik_tidsserie_och_tidssteg(
        ut,
        graf,
        res,
        [0.0, 600.0],
        [60.0, 60.0],
        slut_tid_s=None,
        regntyp="block",
        varaktighet_min=10.0,
        losare="vatten.dynamik",
    )
    with np.load(ut / "tidssteg.npz") as npz:
        assert int(npz["nedsampling"]) == 2
        assert npz["djup_mm"].shape[1:] == (513, 4)
