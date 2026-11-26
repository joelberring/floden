"""Regressionstester för Våg 4 (K-B6): flerpakets- och skarvfria vattenytor.

Verifierar:
1. `las_strandlinjer` läser och sammanfogar strandlinjer från samtliga baskarte-ZIP-paket
   (både flera SHP-paket och blandade GPKG/SHP-paket) utan tidigt `break`.
2. `vattenytor_for_rutor` och `bygg_omrade_filpaket` beräknar en konsekvent global RH 2000-
   mediannivå per sammanhängande vattenyta över rutgränser (öst-väst, syd-nord och med överlapp)
   så att steget över rutskarven blir exakt 0,000 m, samtidigt som skilda vattenytor behåller
   sina egna nivåer och enkelruts-API:t förblir bakåtkompatibelt.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import LineString

from kommuner.k1_filpaket import bygg_omrade_filpaket
from kommuner.konfig import VattenKonfig
from kommuner.vattenytor import (
    las_strandlinjer,
    vattenytor_for_rutor,
    vattenytor_i_ruta,
)
from lantmateriet.omrade import las_omrade
from markmodell.rutnat import Ruta, kontrollera_skarv
from testsvit.k1_io import las_geotiff

E0, N0 = 150000.0, 6400000.0  # SWEREF 99 18 00 (EPSG:3011)
VATTEN_KONFIG = VattenKonfig("(?i)^hydrografi_l$", "typ", "(?i)^strandlinje")


def _skapa_baskarta_zip(
    tmp_path: Path,
    namn: str,
    linjer: list[np.ndarray[Any, Any]],
    *,
    format_typ: str = "shp",
    inkl_extra_shp: list[np.ndarray[Any, Any]] | None = None,
) -> Path:
    """Bygger en syntetisk baskarte-ZIP med `hydrografi_l` i SHP och/eller GPKG."""
    mapp = tmp_path / f"bygg_{namn}"
    mapp.mkdir(parents=True, exist_ok=True)
    filer: list[Path] = []

    def _skriv_gdf(fil: Path, g_linjer: list[np.ndarray[Any, Any]], driver: str | None) -> None:
        geom = [LineString(c) for c in g_linjer] + [
            LineString([(E0 + 1.0, N0 + 1.0, 0.0), (E0 + 2.0, N0 + 2.0, 0.0)])
        ]
        typ = ["Strandlinje"] * len(g_linjer) + ["Bäck"]
        gdf = gpd.GeoDataFrame({"typ": typ}, geometry=geom, crs="EPSG:3011")
        if driver == "GPKG":
            gdf.to_file(fil, driver="GPKG", layer="hydrografi_l")
        else:
            gdf.to_file(fil)

    if format_typ == "gpkg":
        gpkg_fil = mapp / "baskarta.gpkg"
        _skriv_gdf(gpkg_fil, linjer, "GPKG")
        filer.append(gpkg_fil)
    else:
        shp_fil = mapp / "hydrografi_l.shp"
        _skriv_gdf(shp_fil, linjer, None)
        filer.extend(sorted(mapp.glob("hydrografi_l.*")))

    if inkl_extra_shp is not None:
        extra_mapp = mapp / "shp_duplikat"
        extra_mapp.mkdir(exist_ok=True)
        _skriv_gdf(extra_mapp / "hydrografi_l.shp", inkl_extra_shp, None)
        filer.extend(sorted(extra_mapp.glob("hydrografi_l.*")))

    zip_fil = tmp_path / f"{namn}.zip"
    with zipfile.ZipFile(zip_fil, "w") as zf:
        for f in filer:
            rel = f.relative_to(mapp).as_posix()
            zf.write(f, f"baskarta/{rel}")
    return zip_fil


class _SyntetiskHojdmodell:
    """Deterministisk höjdmodell som genererar `z` per ruta utifrån en funktion `(e, n) -> z`."""

    plattor: list[Any] = []

    def __init__(self, z_fn: Any) -> None:
        self._z_fn = z_fn

    def grund_z(self, ruta: Ruta) -> tuple[np.ndarray[Any, Any], list[str]]:
        ex, ny = ruta.cell_centra_en()
        gx, gy = np.meshgrid(ex, ny)
        return np.asarray(self._z_fn(gx, gy), dtype=np.float32), []


def test_k_b6_flerpaket_laser_strandlinjer_fran_samtliga_zip_paket(tmp_path: Path) -> None:
    """Två baskarte-ZIP-filer med varsin vattenyta ska båda läsas in (inte bara första)."""
    linje_vast = np.array(
        [[E0 + 20.0, N0 + 10.0, 1.0], [E0 + 20.0, N0 + 90.0, 1.0]], dtype=np.float64
    )
    linje_ost = np.array(
        [[E0 + 140.0, N0 + 10.0, 1.4], [E0 + 140.0, N0 + 90.0, 1.4]], dtype=np.float64
    )
    zip_vast = _skapa_baskarta_zip(tmp_path, "paket_vast", [linje_vast], format_typ="shp")
    zip_ost = _skapa_baskarta_zip(tmp_path, "paket_ost", [linje_ost], format_typ="shp")

    linjer, varn = las_strandlinjer(
        [zip_vast, zip_ost],
        VATTEN_KONFIG,
        bbox_publicering=(E0 - 10.0, N0 - 10.0, E0 + 210.0, N0 + 110.0),
        epsg_publicering=3011,
    )
    assert not varn
    assert len(linjer) == 2
    z_nivaer = sorted(float(c[0, 2]) for c in linjer)
    assert z_nivaer == pytest.approx([1.0, 1.4], abs=1e-9)
    e_koordinater = sorted(float(c[0, 0]) for c in linjer)
    assert e_koordinater == pytest.approx([E0 + 20.0, E0 + 140.0], abs=1e-6)


def test_k_b6_flerpaket_blandade_format_gpkg_och_shp(tmp_path: Path) -> None:
    """GPKG har prioritet inom samma ZIP, men ett andra ZIP-paket med SHP läses ändå in."""
    linje_gpkg = np.array(
        [[E0 + 20.0, N0 + 10.0, 1.1], [E0 + 20.0, N0 + 90.0, 1.1]], dtype=np.float64
    )
    linje_duplikat = np.array(
        [[E0 + 20.0, N0 + 10.0, 9.9], [E0 + 20.0, N0 + 90.0, 9.9]], dtype=np.float64
    )
    linje_shp = np.array(
        [[E0 + 160.0, N0 + 10.0, 1.5], [E0 + 160.0, N0 + 90.0, 1.5]], dtype=np.float64
    )
    zip_1 = _skapa_baskarta_zip(
        tmp_path, "paket_1", [linje_gpkg], format_typ="gpkg", inkl_extra_shp=[linje_duplikat]
    )
    zip_2 = _skapa_baskarta_zip(tmp_path, "paket_2", [linje_shp], format_typ="shp")

    linjer, varn = las_strandlinjer(
        [zip_1, zip_2],
        VATTEN_KONFIG,
        bbox_publicering=(E0 - 10.0, N0 - 10.0, E0 + 210.0, N0 + 110.0),
        epsg_publicering=3011,
    )
    assert not varn
    assert len(linjer) == 2
    assert sorted(float(c[0, 2]) for c in linjer) == pytest.approx([1.1, 1.5], abs=1e-9)


def test_k_b6_skarvfri_kanal_over_tva_grannrutor_i_bygg_omrade_filpaket(tmp_path: Path) -> None:
    """En kanal över 2 grannrutor med olika strandhöjder får exakt 0,000 m steg längs rutskarven."""
    # Område 200 x 100 m -> två grannrutor (100 x 100 m): väst [E0, E0+100], öst [E0+100, E0+200]
    omr_fil = tmp_path / "omr.gpkg"
    gpd.GeoDataFrame(
        {"namn": ["kanal"]},
        geometry=[shapely.box(E0, N0, E0 + 200.0, N0 + 100.0)],
        crs=3011,
    ).to_file(omr_fil, driver="GPKG")
    omr = las_omrade(omr_fil)

    def _kanal_z(gx: np.ndarray[Any, Any], gy: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        z = np.full(gx.shape, 3.5, dtype=np.float32)
        i_kanal = (gy >= N0 + 40.0) & (gy <= N0 + 60.0)
        z[i_kanal] = -5.0
        # En isolerad sänka på land långt från strandlinjen ska inte fyllas
        i_landsanka = (gx >= E0 + 10.0) & (gx <= E0 + 20.0) & (gy >= N0 + 10.0) & (gy <= N0 + 20.0)
        z[i_landsanka] = -2.0
        return z

    # Två strandlinjer (södra och norra kajen) med olika z i väst (+1,0 m) och öst (+1,4 m)
    strandlinjer = [
        np.array(
            [
                [E0, N0 + 40.0, 1.0],
                [E0 + 99.0, N0 + 40.0, 1.0],
                [E0 + 101.0, N0 + 40.0, 1.4],
                [E0 + 200.0, N0 + 40.0, 1.4],
            ],
            dtype=np.float64,
        ),
        np.array(
            [
                [E0, N0 + 60.0, 1.0],
                [E0 + 99.0, N0 + 60.0, 1.0],
                [E0 + 101.0, N0 + 60.0, 1.4],
                [E0 + 200.0, N0 + 60.0, 1.4],
            ],
            dtype=np.float64,
        ),
    ]

    ut = tmp_path / "k1"
    res = bygg_omrade_filpaket(
        omr,
        ut,
        hojdmodell=_SyntetiskHojdmodell(_kanal_z),  # type: ignore[arg-type]
        hojd_paket=[],
        byggnader_fil=None,
        upplosning_m=1.0,
        ruta_storlek_m=100.0,
        en_ruta=False,
        skriv=lambda _t: None,
        strandlinjer=strandlinjer,
        vatten_marginal_m=0.2,
        vatten_max_avstand_m=2.0,
    )

    assert len(res.rutor) == 2
    r_vast = f"mm_{int(E0)}_{int(N0)}"
    r_ost = f"mm_{int(E0 + 100)}_{int(N0)}"
    assert set(res.rutor) == {r_vast, r_ost}

    z_vast, _ = las_geotiff(ut / r_vast / "mark.tif")
    z_ost, _ = las_geotiff(ut / r_ost / "mark.tif")
    slag_vast, _ = las_geotiff(ut / r_vast / "markslag.tif")
    slag_ost, _ = las_geotiff(ut / r_ost / "markslag.tif")

    vatten_vast = slag_vast == 8
    vatten_ost = slag_ost == 8
    assert vatten_vast.any() and vatten_ost.any()

    # Längs rutskarven (västra rutans högra kolumn mot östra rutans vänstra kolumn): 0,000 m steg!
    skarv_vatten = vatten_vast[:, -1] & vatten_ost[:, 0]
    assert int(np.count_nonzero(skarv_vatten)) == 20
    steg_m = float(np.max(np.abs(z_vast[skarv_vatten, -1] - z_ost[skarv_vatten, 0])))
    assert steg_m == 0.0

    # Båda rutorna har exakt samma globala mediannivå (1,2 m RH 2000), ingen avvikelse
    assert float(np.min(z_vast[vatten_vast])) == pytest.approx(1.2, abs=1e-5)
    assert float(np.max(z_vast[vatten_vast])) == pytest.approx(1.2, abs=1e-5)
    assert float(np.min(z_ost[vatten_ost])) == pytest.approx(1.2, abs=1e-5)
    assert float(np.max(z_ost[vatten_ost])) == pytest.approx(1.2, abs=1e-5)
    assert res.vatten_niva_m is not None
    assert res.vatten_niva_m[0] == pytest.approx(res.vatten_niva_m[1], abs=1e-6)
    assert res.vatten_niva_m[0] == pytest.approx(1.2, abs=1e-5)

    # Sänkan på land i västra rutan förblir torr (markslag != 8 och botten -2 m kvar)
    assert not (slag_vast[80:90, 10:20] == 8).any()
    assert float(np.min(z_vast)) == pytest.approx(-2.0, abs=1e-4)

    # Varningstexten ska vara uppdaterad (inte den inaktuella "närmaste strandlinjes z")
    assert not any("närmaste strandlinjes z" in w for w in res.varningar)
    assert any("global mediannivå per sammanhängande vattenyta" in w for w in res.varningar)


def test_k_b6_nord_syd_skarv_och_separat_hojd_damm_samt_overlapp() -> None:
    """Syd-nordlig kanal får 0,000 m steg; separat högt belägen damm behåller sin egen nivå."""
    r_syd = Ruta(E0, N0, 100.0, 0.0, 1.0)
    r_nord = Ruta(E0, N0 + 100.0, 100.0, 0.0, 1.0)

    z_syd = np.full(r_syd.form_rc, 4.0, dtype=np.float32)
    z_syd[:, 40:60] = -4.0

    z_nord = np.full(r_nord.form_rc, 4.0, dtype=np.float32)
    z_nord[:, 40:60] = -4.0
    # Separat högt belägen damm i östra delen av norra rutan (mark 18 m, botten 10 m, strand 15 m)
    z_nord[10:30, 75:95] = 18.0
    z_nord[14:26, 79:91] = 10.0

    linjer = [
        # Kanalens två stränder: 0,8 m i södra rutan, 1,2 m i norra rutan
        np.array(
            [
                [E0 + 40.0, N0, 0.8],
                [E0 + 40.0, N0 + 99.0, 0.8],
                [E0 + 40.0, N0 + 101.0, 1.2],
                [E0 + 40.0, N0 + 200.0, 1.2],
            ],
            dtype=np.float64,
        ),
        np.array(
            [
                [E0 + 60.0, N0, 0.8],
                [E0 + 60.0, N0 + 99.0, 0.8],
                [E0 + 60.0, N0 + 101.0, 1.2],
                [E0 + 60.0, N0 + 200.0, 1.2],
            ],
            dtype=np.float64,
        ),
        # Dammens strandlinje på +15,0 m RH 2000
        np.array(
            [
                [E0 + 79.0, N0 + 174.0, 15.0],
                [E0 + 91.0, N0 + 174.0, 15.0],
                [E0 + 91.0, N0 + 186.0, 15.0],
                [E0 + 79.0, N0 + 186.0, 15.0],
                [E0 + 79.0, N0 + 174.0, 15.0],
            ],
            dtype=np.float64,
        ),
    ]

    (m_syd, n_syd), (m_nord, n_nord) = vattenytor_for_rutor(
        [(r_syd, z_syd), (r_nord, z_nord)],
        linjer,
        marginal_m=0.2,
        max_avstand_m=2.0,
    )

    # Nord-sydlig skarv: södra rutans norra kant (rad 0) möter norra rutans södra kant (rad -1)
    skarv = m_syd[0, 40:60] & m_nord[-1, 40:60]
    assert skarv.all()
    assert float(np.max(np.abs(n_syd[0, 40:60] - n_nord[-1, 40:60]))) == 0.0
    assert np.allclose(n_syd[m_syd], 1.0, atol=1e-5)
    assert np.allclose(n_nord[m_nord & (np.arange(100)[None, :] < 65)], 1.0, atol=1e-5)
    # Dammen behåller sin egen nivå (+15,0 m) oberoende av kanalen
    assert m_nord[15:25, 80:90].all()
    assert np.allclose(n_nord[15:25, 80:90], 15.0, atol=1e-5)

    # Överlappande rutor (25 m överlapp): skarvrapporten har 0,000 m maxavvikelse och ingen
    # dubbelräkning i överlappet
    r_ov_a = Ruta(E0, N0, 100.0, 25.0, 1.0)
    r_ov_b = Ruta(E0 + 100.0, N0, 100.0, 25.0, 1.0)
    z_ov_a = np.full(r_ov_a.form_rc, 3.5, dtype=np.float32)
    z_ov_b = np.full(r_ov_b.form_rc, 3.5, dtype=np.float32)
    z_ov_a[65:85, :] = -5.0  # y i [N0+40, N0+60] (yttre n_max = N0+125)
    z_ov_b[65:85, :] = -5.0
    linjer_ov = [
        np.array(
            [
                [E0 - 25.0, N0 + 40.0, 1.0],
                [E0 + 99.0, N0 + 40.0, 1.0],
                [E0 + 101.0, N0 + 40.0, 1.6],
                [E0 + 225.0, N0 + 40.0, 1.6],
            ],
            dtype=np.float64,
        ),
        np.array(
            [
                [E0 - 25.0, N0 + 60.0, 1.0],
                [E0 + 99.0, N0 + 60.0, 1.0],
                [E0 + 101.0, N0 + 60.0, 1.6],
                [E0 + 225.0, N0 + 60.0, 1.6],
            ],
            dtype=np.float64,
        ),
    ]
    (m_oa, n_oa), (m_ob, n_ob) = vattenytor_for_rutor(
        [(r_ov_a, z_ov_a), (r_ov_b, z_ov_b)],
        linjer_ov,
        marginal_m=0.2,
        max_avstand_m=2.0,
    )
    z_res_a = np.where(m_oa, n_oa, z_ov_a)
    z_res_b = np.where(m_ob, n_ob, z_ov_b)
    rapport = kontrollera_skarv(r_ov_a, z_res_a, r_ov_b, z_res_b, max_avvikelse_m=1e-6)
    assert rapport.godkand
    assert rapport.max_avvikelse_m == 0.0
    # Symmetrisk 125 m på 1,0 m och 125 m på 1,6 m -> unik global median exakt 1,3 m
    assert np.allclose(n_oa[m_oa], 1.3, atol=1e-5)
    assert np.allclose(n_ob[m_ob], 1.3, atol=1e-5)

    # Enkelruts-API bakåtkompatibelt
    m_enkel, n_enkel = vattenytor_i_ruta(
        z_syd, r_syd, linjer[:2], marginal_m=0.2, max_avstand_m=2.0
    )
    m_via_fler, n_via_fler = vattenytor_for_rutor(
        [(r_syd, z_syd)], linjer[:2], marginal_m=0.2, max_avstand_m=2.0
    )[0]
    np.testing.assert_array_equal(m_enkel, m_via_fler)
    np.testing.assert_array_equal(n_enkel, n_via_fler)
