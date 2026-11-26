# ruff: noqa: E501
"""Deterministiska cachebudget- och celltakstester för `webb.gyf_utci` och `webb.cad_buller_api`."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import webb.cad_buller_api as cb_mod
import webb.gyf_utci as gu_mod
from demovarld.varld import DemoVarld, bygg_ruta_vatten
from karna.geometri.rasterfil import ruta_fran_geo
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import brann_in_byggnader
from testsvit.k1_io import GeoTransformInfo, skriv_geotiff
from webb.api import FlodenApiApp
from webb.cad_buller_api import (
    STANDARD_MAX_BULLER_CELLER,
    STANDARD_MAX_BULLER_RASTERCELLER,
    STANDARD_MAX_CAD_EXPORT_BYGGNADER,
    STANDARD_MAX_CAD_EXPORT_CELLER,
    BullerLagerCache,
    max_buller_celler,
    max_buller_rasterceller,
    max_cad_export_byggnader,
    max_cad_export_bytes,
    max_cad_export_celler,
)
from webb.fel import FlodenWebbFel
from webb.gyf_utci import (
    STANDARD_MAX_GYF_CELLER,
    STANDARD_MAX_VARMESTRESS_PROJEKT_CELLER,
    STANDARD_MAX_WEBB_PNG_CACHE_MB,
    VarmestressLagerCache,
    _SvfCache,
    max_gyf_celler,
    max_varmestress_projekt_celler,
    max_webb_png_cache_bytes,
    max_webb_svf_cache_bytes,
)
from webb.resultatlager import Utbredning

_UTB = Utbredning(x_min=150_000.0, y_min=6_580_000.0, x_max=150_100.0, y_max=6_580_100.0)


def _post_json(
    app: FlodenApiApp, sokvag: str, payload: Mapping[str, Any]
) -> tuple[int, dict[str, Any]]:
    status, _, kropp = app.hantera_forfragan(
        "POST", sokvag, json.dumps(payload).encode("utf-8")
    )
    return status, json.loads(kropp.decode("utf-8"))


def _bygg_demo_app(rot: Path) -> FlodenApiApp:
    """Lokal FlodenApiApp med Demovärldens K1 v2-ruta (300×300 m = 90 000 celler)."""
    varld = DemoVarld()
    bygg_ruta_vatten(varld, rot / "varld" / "vatten" / "bas")
    konfig = rot / "config.toml"
    konfig.write_text(
        '[webb]\ndemomapp = "varld"\nfragemappar = ["fragor"]\ncachemapp = "cache"\n'
        'korningsmapp = "korningar"\nklientmapp = "klient"\n',
        encoding="utf-8",
    )
    tom_testsvit = rot / "tom-testsvit"
    tom_testsvit.mkdir(exist_ok=True)
    return FlodenApiApp(testsvit_rot=tom_testsvit, konfig_fil=konfig)


def _bygg_liten_buller_cad_app(rot: Path, *, antal_hus: int = 2) -> tuple[FlodenApiApp, float, float]:
    """Lokal, blixtsnabb K1 v2-ruta (60×60 m, 5 m celler = 144 celler) som demokvarteret."""
    e0, n0 = 150_000.0, 6_580_000.0
    sida_m, px = 60.0, 5.0
    n = round(sida_m / px)
    mapp = rot / "varld" / "vatten" / "bas" / "mm_150000_6580000"
    mapp.mkdir(parents=True, exist_ok=True)

    terrang = np.full((n, n), 10.0, dtype=np.float32)
    markslag = np.full((n, n), 4, dtype=np.uint8)
    osak = np.full((n, n), 0.05, dtype=np.float32)

    hus_lista: list[ByggnadsPost] = []
    for i in range(antal_hus):
        hx = e0 + 20.0 + i * 20.0
        hus_lista.append(
            ByggnadsPost(
                byggnad_id=f"b_hus_{i + 1}",
                polygon=(
                    (hx, n0 + 20.0),
                    (hx + 10.0, n0 + 20.0),
                    (hx + 10.0, n0 + 40.0),
                    (hx, n0 + 40.0),
                ),
                hal=(),
                takhojd_rh2000=22.0,
                markhojd_rh2000=10.0,
                hojd_m=12.0,
                kalla="test",
            )
        )
    geo = GeoTransformInfo(
        bredd=n,
        hojd=n,
        x_min=e0,
        y_max=n0 + sida_m,
        pixelstorlek_x=px,
        pixelstorlek_y=px,
        epsg=3011,
        dtype="float32",
    )
    mark, markslag = brann_in_byggnader(
        terrang,
        markslag,
        ruta_fran_geo(geo),
        [h.till_byggnad() for h in hus_lista],
    )
    skriv_geotiff(mapp / "terrang.tif", terrang, x_min=e0, y_max=n0 + sida_m, pixelstorlek=px)
    skriv_geotiff(
        mapp / "mark.tif",
        np.asarray(mark, dtype=np.float32),
        x_min=e0,
        y_max=n0 + sida_m,
        pixelstorlek=px,
    )
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=e0, y_max=n0 + sida_m, pixelstorlek=px)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=e0, y_max=n0 + sida_m, pixelstorlek=px)
    skriv_byggnader_gpkg(
        mapp / "byggnader.gpkg",
        tuple(hus_lista),
        bbox_fallback=(e0, n0, e0 + sida_m, n0 + sida_m),
    )
    (mapp / "recept.json").write_text(
        json.dumps(
            {
                "kontrakt": "K1/v2",
                "ruta_id": "mm_150000_6580000",
                "epsg": 3011,
                "hojdsystem": "RH 2000",
                "installningar": {"k1_version": 2, "upplosning_m": px},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    konfig = rot / "config.toml"
    konfig.write_text(
        '[webb]\ndemomapp = "varld"\nfragemappar = ["fragor"]\ncachemapp = "cache"\n'
        'korningsmapp = "korningar"\nklientmapp = "klient"\n',
        encoding="utf-8",
    )
    tom_testsvit = rot / "tom-testsvit"
    tom_testsvit.mkdir(exist_ok=True)
    return FlodenApiApp(testsvit_rot=tom_testsvit, konfig_fil=konfig), e0, n0


@pytest.mark.parametrize("cache_cls", [VarmestressLagerCache, BullerLagerCache])
def test_png_cacher_bytebudget_lru_eviction_och_isolering(
    cache_cls: type[VarmestressLagerCache] | type[BullerLagerCache],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Både `VarmestressLagerCache` och `BullerLagerCache` håller exakt bytebudget, LRU och projektisolering."""
    monkeypatch.delenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", raising=False)
    assert max_webb_png_cache_bytes() == int(STANDARD_MAX_WEBB_PNG_CACHE_MB * 1024 * 1024)

    for ogiltigt in ("-1", "abc", "nan", "inf"):
        monkeypatch.setenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", ogiltigt)
        with pytest.raises(FlodenWebbFel) as exc:
            max_webb_png_cache_bytes()
        assert exc.value.status_kod == 500
    monkeypatch.delenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", raising=False)

    cache = cache_cls(max_poster=64, max_bytes=1_000)
    png_400_a = b"A" * 400
    png_400_b = b"B" * 400
    png_400_c = b"C" * 400

    # 1. Lägg in A (400 B) och B (400 B) -> totalt 800 B <= 1 000 B.
    cache.satt("proj-a", "lager_a", png_400_a, _UTB)
    cache.satt("proj-a", "lager_b", png_400_b, _UTB)
    assert cache.antal_poster == 2
    assert cache.antal_bytes == 800

    # 2. Cross-tenant: annat projekt ser aldrig proj-a:s lager.
    assert cache.hamta("proj-b", "lager_a") is None

    # 3. Läs lager_a -> lager_a blir MRU, lager_b blir LRU.
    traff_a = cache.hamta("proj-a", "lager_a")
    assert traff_a is not None and traff_a[0] == png_400_a

    # 4. Lägg in C (400 B) -> totalt 1 200 > 1 000 -> LRU (lager_b) evikteras!
    cache.satt("proj-a", "lager_c", png_400_c, _UTB)
    assert cache.antal_bytes == 800
    assert cache.antal_poster == 2
    assert cache.hamta("proj-a", "lager_b") is None
    assert cache.hamta("proj-a", "lager_a") is not None
    assert cache.hamta("proj-a", "lager_c") is not None

    # 5. Överskrivning av befintlig nyckel uppdaterar bytebokföringen exakt.
    cache.satt("proj-a", "lager_a", b"X" * 500, _UTB)
    assert cache.antal_bytes == 900
    assert cache.antal_poster == 2

    # 6. Enskild PNG större än hela budgeten (1 200 > 1 000) cachas aldrig.
    cache.satt("proj-a", "jattebild", b"J" * 1_200, _UTB)
    assert cache.hamta("proj-a", "jattebild") is None
    assert cache.antal_bytes == 900

    # 7. Miljövariabelstyrning: `FLODEN_MAX_WEBB_PNG_CACHE_MB=0` inaktiverar cache utan fast `max_bytes`.
    miljo_cache = cache_cls(max_poster=64)
    monkeypatch.setenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", "0.001")  # 1 048 byte
    miljo_cache.satt("proj-a", "b1", b"1" * 700, _UTB)
    miljo_cache.satt("proj-a", "b2", b"2" * 700, _UTB)
    assert miljo_cache.antal_bytes == 700
    assert miljo_cache.hamta("proj-a", "b1") is None
    assert miljo_cache.hamta("proj-a", "b2") is not None

    monkeypatch.setenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", "0")
    assert miljo_cache.hamta("proj-a", "b2") is None
    miljo_cache.satt("proj-a", "b3", b"3" * 100, _UTB)
    assert miljo_cache.antal_bytes == 0
    assert miljo_cache.antal_poster == 0

    # 8. Ogiltigt bild-id ger alltid HTTP 400 (även när cachen är avstängd).
    with pytest.raises(FlodenWebbFel) as exc_id:
        miljo_cache.hamta("proj-a", "../hemlig")
    assert exc_id.value.status_kod == 400


def test_svf_cache_bytebudget_lru_och_inaktivering(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_SvfCache` i `webb.gyf_utci` begränsas på exakt `nbytes` + LRU och stängs av vid 0."""
    monkeypatch.delenv("FLODEN_MAX_WEBB_SVF_CACHE_MB", raising=False)
    assert max_webb_svf_cache_bytes() == 32 * 1024 * 1024

    cache = _SvfCache(max_poster=16, max_bytes=2_000)
    a1 = np.ones((10, 10), dtype=np.float64)  # 800 B
    a2 = np.full((10, 10), 0.5, dtype=np.float64)  # 800 B
    a3 = np.full((10, 10), 0.25, dtype=np.float64)  # 800 B
    stor = np.zeros((20, 20), dtype=np.float64)  # 3 200 B > 2 000 B

    cache.satt(("g1", 16, 100.0), a1)
    cache.satt(("g2", 16, 100.0), a2)
    assert cache.antal_bytes == 1_600
    assert cache.hamta(("g1", 16, 100.0)) is not None  # g1 blir MRU

    cache.satt(("g3", 16, 100.0), a3)
    assert cache.antal_bytes == 1_600
    assert cache.hamta(("g2", 16, 100.0)) is None  # LRU evikterad
    assert cache.hamta(("g1", 16, 100.0)) is not None

    cache.satt(("stor", 16, 100.0), stor)
    assert cache.hamta(("stor", 16, 100.0)) is None
    assert cache.antal_bytes == 1_600

    cache.rensa()
    assert cache.antal_bytes == 0
    assert cache.antal_poster == 0


def test_gyf_och_varmestress_celltak_ger_http_400_utan_pixelladdning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GYF och Värmestress ger tydligt HTTP 400 (aldrig OOM) före pixelladdning när celltak överskrids."""
    monkeypatch.delenv("FLODEN_MAX_GYF_CELLER", raising=False)
    monkeypatch.delenv("FLODEN_MAX_VARMESTRESS_CELLER", raising=False)
    assert max_gyf_celler() == STANDARD_MAX_GYF_CELLER == 4_000_000
    assert max_varmestress_projekt_celler() == STANDARD_MAX_VARMESTRESS_PROJEKT_CELLER == 4_000_000

    app = _bygg_demo_app(tmp_path)  # 300×300 = 90 000 celler

    pixel_lasningar = 0
    orig_las = gu_mod._las_en_k1_ruta_data  # noqa: SLF001

    def _spion_las(*args: Any, **kwargs: Any) -> Any:
        nonlocal pixel_lasningar
        pixel_lasningar += 1
        return orig_las(*args, **kwargs)

    monkeypatch.setattr(gu_mod, "_las_en_k1_ruta_data", _spion_las)

    # 1. GYF med celltak 10 000 < 90 000 -> HTTP 400 och 0 pixelläsningar.
    monkeypatch.setenv("FLODEN_MAX_GYF_CELLER", "10000")
    st_gyf, fel_gyf = _post_json(app, "/api/analys/gyf", {"omrade_id": "demokvarteret"})
    assert st_gyf == 400, fel_gyf
    assert "90000 celler" in fel_gyf["fel"]
    assert "FLODEN_MAX_GYF_CELLER" in fel_gyf["fel"]
    assert pixel_lasningar == 0

    # 2. Värmestress med celltak 10 000 < 90 000 -> HTTP 400 och 0 pixelläsningar.
    monkeypatch.setenv("FLODEN_MAX_VARMESTRESS_CELLER", "10000")
    st_vs, fel_vs = _post_json(app, "/api/analys/varmestress", {"omrade_id": "demokvarteret"})
    assert st_vs == 400, fel_vs
    assert "90000 celler" in fel_vs["fel"]
    assert "FLODEN_MAX_VARMESTRESS_CELLER" in fel_vs["fel"]
    assert pixel_lasningar == 0

    # 3. Med tillräckligt celltak fungerar både GYF och Värmestress och PNG-cachen bokför byte exakt.
    monkeypatch.setenv("FLODEN_MAX_GYF_CELLER", "100000")
    monkeypatch.setenv("FLODEN_MAX_VARMESTRESS_CELLER", "100000")
    monkeypatch.setenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", "4")
    st_gyf_ok, res_gyf = _post_json(app, "/api/analys/gyf", {"omrade_id": "demokvarteret"})
    assert st_gyf_ok == 200, res_gyf
    assert res_gyf["fore"]["gyf"] >= 0.0

    st_vs_ok, res_vs = _post_json(
        app,
        "/api/analys/varmestress",
        {"omrade_id": "demokvarteret", "antal_riktningar": 8},
    )
    assert st_vs_ok == 200, res_vs
    assert np.isfinite(res_vs["utci_medel_c"])
    assert 0 < app._varmestress_lager.antal_bytes <= 4 * 1024 * 1024  # noqa: SLF001


def test_buller_och_cad_3d_export_tak_ger_http_400_och_bevarar_invarianter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Buller och CAD/3D-export (OBJ/GLB) ger tydligt HTTP 400 över tak och bevarar `nord2000_konform=False`."""
    monkeypatch.delenv("FLODEN_MAX_BULLER_CELLER", raising=False)
    monkeypatch.delenv("FLODEN_MAX_BULLER_RASTERCELLER", raising=False)
    monkeypatch.delenv("FLODEN_MAX_CAD_EXPORT_CELLER", raising=False)
    monkeypatch.delenv("FLODEN_MAX_CAD_EXPORT_BYGGNADER", raising=False)
    monkeypatch.delenv("FLODEN_MAX_CAD_EXPORT_MB", raising=False)
    assert max_buller_celler() == STANDARD_MAX_BULLER_CELLER == 4_000_000
    assert max_buller_rasterceller() == STANDARD_MAX_BULLER_RASTERCELLER == 10_000
    assert max_cad_export_celler() == STANDARD_MAX_CAD_EXPORT_CELLER == 4_000_000
    assert max_cad_export_byggnader() == STANDARD_MAX_CAD_EXPORT_BYGGNADER == 5_000
    assert max_cad_export_bytes() == 50 * 1024 * 1024

    app, e0, n0 = _bygg_liten_buller_cad_app(tmp_path, antal_hus=2)  # 12×12 = 144 celler, 2 hus
    vag = [
        {
            "id": "v1",
            "koordinater": [[e0 + 5.0, n0 + 5.0], [e0 + 5.0, n0 + 55.0]],
            "adt_fordon_dygn": 5_000,
            "hastighet_km_h": 50.0,
            "andel_tung_pct": 10.0,
        }
    ]

    # 1. Buller K1-celltak 100 < 144 -> HTTP 400.
    monkeypatch.setenv("FLODEN_MAX_BULLER_CELLER", "100")
    st_b1, fel_b1 = _post_json(
        app,
        "/api/analys/buller",
        {"omrade_id": "demokvarteret", "vagsegment": vag, "raster_cell_m": 15.0},
    )
    assert st_b1 == 400, fel_b1
    assert "144 celler" in fel_b1["fel"]
    assert "FLODEN_MAX_BULLER_CELLER" in fel_b1["fel"]
    monkeypatch.delenv("FLODEN_MAX_BULLER_CELLER", raising=False)

    # 2. Buller explicit för högt `max_rasterceller` (50 000 > 10 000) -> HTTP 400.
    st_b2, fel_b2 = _post_json(
        app,
        "/api/analys/buller",
        {"omrade_id": "demokvarteret", "vagsegment": vag, "max_rasterceller": 50_000},
    )
    assert st_b2 == 400, fel_b2
    assert "FLODEN_MAX_BULLER_RASTERCELLER" in fel_b2["fel"]

    # 3. Buller för fint `raster_cell_m` som överskrider mottagartaket -> HTTP 400.
    monkeypatch.setenv("FLODEN_MAX_BULLER_RASTERCELLER", "20")
    st_b3, fel_b3 = _post_json(
        app,
        "/api/analys/buller",
        {"omrade_id": "demokvarteret", "vagsegment": vag, "raster_cell_m": 10.0},
    )
    assert st_b3 == 400, fel_b3
    assert "FLODEN_MAX_BULLER_RASTERCELLER" in fel_b3["fel"]
    monkeypatch.delenv("FLODEN_MAX_BULLER_RASTERCELLER", raising=False)

    # 4. Normal bullerkörning går igenom (200), bevarar hårda invarianter och cachar PNG under budget.
    monkeypatch.setenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", "4")
    st_bok, res_bok = _post_json(
        app,
        "/api/analys/buller",
        {
            "omrade_id": "demokvarteret",
            "vagsegment": vag,
            "raster_cell_m": 10.0,
            "segment_langd_m": 20.0,
            "fasad_steg_m": 15.0,
            "max_vaningar": 2,
            "reflexordning": 0,
        },
    )
    assert st_bok == 200, res_bok
    assert res_bok["nord2000_konform"] is False
    assert res_bok["metodstatus"] == "skiss"
    assert res_bok["rapport_sparrad"] is True
    assert 0 < app._buller_lager.antal_bytes <= 4 * 1024 * 1024  # noqa: SLF001

    # 5. CAD/3D-export (OBJ & GLB) K1-celltak 100 < 144 -> HTTP 400 före pixelladdning.
    monkeypatch.setenv("FLODEN_MAX_CAD_EXPORT_CELLER", "100")
    for fmt in ("obj", "glb", "landxml"):
        st_c1, _, kropp_c1 = app.hantera_forfragan(
            "GET", f"/api/projekt/demokvarteret/cad/exportera?format={fmt}", b""
        )
        assert st_c1 == 400, (fmt, kropp_c1)
        assert "FLODEN_MAX_CAD_EXPORT_CELLER" in json.loads(kropp_c1)["fel"]
    monkeypatch.delenv("FLODEN_MAX_CAD_EXPORT_CELLER", raising=False)

    # 6. CAD/3D-export byggnadstak (1 < 2 hus) -> HTTP 400.
    monkeypatch.setenv("FLODEN_MAX_CAD_EXPORT_BYGGNADER", "1")
    st_c2, _, kropp_c2 = app.hantera_forfragan(
        "GET", "/api/projekt/demokvarteret/cad/exportera?format=glb", b""
    )
    assert st_c2 == 400, kropp_c2
    assert "FLODEN_MAX_CAD_EXPORT_BYGGNADER" in json.loads(kropp_c2)["fel"]
    monkeypatch.delenv("FLODEN_MAX_CAD_EXPORT_BYGGNADER", raising=False)

    # 7. CAD/3D-export filstorlekstak (0,0001 MB ~ 104 B) -> HTTP 400 före `read_bytes()`.
    monkeypatch.setenv("FLODEN_MAX_CAD_EXPORT_MB", "0.0001")
    st_c3, _, kropp_c3 = app.hantera_forfragan(
        "GET", "/api/projekt/demokvarteret/cad/exportera?format=obj", b""
    )
    assert st_c3 == 400, kropp_c3
    assert "FLODEN_MAX_CAD_EXPORT_MB" in json.loads(kropp_c3)["fel"]
    monkeypatch.delenv("FLODEN_MAX_CAD_EXPORT_MB", raising=False)

    # 8. Normal 3D-export (OBJ & GLB) fungerar med HTTP 200.
    for fmt in ("obj", "glb"):
        st_ok, _, data = app.hantera_forfragan(
            "GET", f"/api/projekt/demokvarteret/cad/exportera?format={fmt}", b""
        )
        assert st_ok == 200, fmt
        assert len(data) > 64

    # Verifiera även modulkonstant så den inte glöms vid omfaktorering.
    assert cb_mod.NORD2000_KONFORM is False
