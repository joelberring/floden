"""Mark i nivåer (R4): lutande yta, delta_m, exakt kantavstånd och slänt (K3 och K9)."""

from __future__ import annotations

import math

import numpy as np
import pytest
import shapely

from karna.k9_till_k1 import markhojd_till_hojdyta
from karna.scenario import Andring, ScenarioFel
from lapp.kontrakt import HojdYta, LappFel, ProjektLapp, normalisera_lutning
from lapp.markniva import lutning_z
from lapp.tillampa import _polygon_mask, tillampa_lapp
from testsvit.k1_io import GeoTransformInfo

DX = 0.5
N = 160  # 80 x 80 m
GEO = GeoTransformInfo(
    bredd=N, hojd=N, x_min=0.0, y_max=N * DX, pixelstorlek_x=DX, pixelstorlek_y=DX,
    epsg=3011, dtype="float32",
)
Z0 = np.full((N, N), 10.0, dtype=np.float32)
OSAK = np.full((N, N), 0.1, dtype=np.float32)
SLAG = np.full((N, N), 4, dtype=np.uint8)
FYRKANT = ((20.0, 20.0), (50.0, 20.0), (50.0, 50.0), (20.0, 50.0))


def _rot(cx: float, cy: float, w: float, h: float, ang: float) -> tuple[tuple[float, float], ...]:
    a = math.radians(ang)
    c, s = math.cos(a), math.sin(a)
    return tuple(
        (cx + c * x - s * y, cy + s * x + c * y)
        for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))
    )


def _kor(yta: HojdYta, z0: np.ndarray = Z0):  # type: ignore[no-untyped-def,type-arg]
    lapp = ProjektLapp(lapp_id="t", bas_recept=("x",), hojdytor=(yta,))
    return tillampa_lapp(
        z0, OSAK, SLAG, GEO, lapp, bas_recept_sha256="x", sektioner={}
    )


def _xy() -> tuple[np.ndarray, np.ndarray]:  # type: ignore[type-arg]
    x = (np.arange(N) + 0.5) * DX
    y = N * DX - (np.arange(N) + 0.5) * DX
    return np.meshgrid(x, y)


# ---------------------------------------------------------------- bakåtkompatibilitet


def test_markhojd_utan_nya_falt_ger_byte_for_byte_samma_raster_som_forr() -> None:
    poly = _rot(35.0, 35.0, 24.0, 14.0, 33.0)  # snedställd: cellmasken trappar
    res = _kor(HojdYta(yta_id="a", polygon=poly, hojd_rh2000=10.6, markslag=1, osakerhet_m=0.02))
    # Gamla vägen utskriven: z = nivå i celler vars centrum ligger i polygonen, inget annat.
    fore = Z0.copy()
    m = _polygon_mask(poly, GEO)
    fore[m] = np.float32(10.6)
    assert res.mark_z.tobytes() == fore.tobytes()
    slag = SLAG.copy()
    slag[m] = 1
    osak = OSAK.copy()
    osak[m] = np.float32(0.02)
    assert res.markslag.tobytes() == slag.tobytes()
    assert res.osakerhet_z.tobytes() == osak.tobytes()
    assert np.array_equal(res.projekt_mask, m)


def test_k3_json_oforandrad_utan_nya_falt() -> None:
    y = HojdYta(yta_id="a", polygon=FYRKANT, hojd_rh2000=10.6)
    assert set(y.till_dict()) == {
        "yta_id", "polygon", "hojd_rh2000", "landxml", "koordinat_ordning", "osakerhet_m",
        "markslag",
    }
    assert not y.har_nya_falt
    assert HojdYta.fran_dict(y.till_dict()) == y


def test_k3_json_rundtur_med_nya_falt() -> None:
    y = HojdYta(
        yta_id="a", polygon=FYRKANT, lutning={"punkt": [30, 30, 10.0], "fall": 0.02},
        slant_forhallande=3.0, slant_markslag=4,
    )
    d = y.till_dict()
    assert d["lutning"]["riktning_grader"] == 0.0 and d["slant_forhallande"] == 3.0
    assert HojdYta.fran_dict(d) == y


# ---------------------------------------------------------------- validering


@pytest.mark.parametrize(
    "kw",
    [
        {},  # ingen höjd
        {"hojd_rh2000": 1.0, "delta_m": 0.1},
        {"hojd_rh2000": 1.0, "lutning": {"punkt": [30, 30, 1.0], "fall": 0.01}},
        {"delta_m": 0.1, "slant_forhallande": 3.0, "slant_bredd_m": 2.0},
        {"delta_m": 0.1, "slant_forhallande": 0.0},
        {"delta_m": 0.1, "slant_markslag": 4},  # markslag utan slänt
        {"lutning": {"a": [1, 1, 1.0], "b": [1, 1, 2.0]}},  # a och b på samma plats
        {"lutning": {"punkter": [[0, 0, 1.0], [1, 1, 1.0], [2, 2, 2.0]]}},  # på en rak linje
        {"lutning": {"punkt": [0, 0, 1.0]}},  # fall saknas
        {"lutning": {"punkt": [0, 0, 1.0], "fall": 0.1, "a": [0, 0, 1.0]}},  # två former
        {"delta_m": float("nan")},
    ],
)
def test_ogiltiga_kombinationer_nekas(kw: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(LappFel):
        HojdYta(yta_id="a", polygon=FYRKANT, **kw)


def test_landxml_kan_inte_kombineras_med_nya_falt() -> None:
    with pytest.raises(LappFel):
        HojdYta(yta_id="a", landxml="x.xml", delta_m=0.1)


# ---------------------------------------------------------------- lutande yta


def test_lutande_ytor_fyra_former() -> None:
    x = np.array([0.0, 10.0, 10.0])
    y = np.array([0.0, 0.0, 10.0])
    # punkt + fall: faller 2 % söderut (azimut 180): z minskar med 0,02 per m mot söder
    f = normalisera_lutning({"punkt": [0, 0, 5.0], "fall": 0.02, "riktning_grader": 180.0})
    assert lutning_z(f, x, y) == pytest.approx([5.0, 5.0, 5.2])
    # a + b: stiger 1 m över 10 m i x, inget tvärfall
    ab = normalisera_lutning({"a": [0, 0, 5.0], "b": [10, 0, 6.0]})
    assert lutning_z(ab, x, y) == pytest.approx([5.0, 6.0, 6.0])
    # punkter: plan i minsta kvadrat, exakt för ett plan
    pk = normalisera_lutning(
        {"punkter": [[0, 0, 1.0], [10, 0, 2.0], [0, 10, 3.0], [10, 10, 4.0]]}
    )
    assert lutning_z(pk, x, y) == pytest.approx([1.0, 2.0, 4.0])
    # två höjdlinjer: z interpoleras efter avståndet till linjerna
    hl = normalisera_lutning(
        {"hojdlinje_a": [[0, 0, 5.0], [0, 10, 5.0]], "hojdlinje_b": [[10, 0, 7.0], [10, 10, 7.0]]}
    )
    assert lutning_z(hl, np.array([0.0, 5.0, 10.0]), np.array([5.0, 5.0, 5.0])) == pytest.approx(
        [5.0, 6.0, 7.0]
    )


def test_lutande_yta_i_rastret() -> None:
    lut = {"a": [20, 35, 10.0], "b": [50, 35, 11.5]}  # 5 cm/m i x
    res = _kor(HojdYta(yta_id="a", polygon=FYRKANT, lutning=lut))
    gx, gy = _xy()
    inne = _polygon_mask(FYRKANT, GEO)
    forvantat = 10.0 + 0.05 * (gx - 20.0)
    assert np.max(np.abs(res.mark_z[inne] - forvantat[inne])) < 1e-4
    assert np.array_equal(res.mark_z[~inne], Z0[~inne])  # hård kant utan slänt: ingen annan cell


# ---------------------------------------------------------------- delta_m


def test_delta_m_ar_basen_plus_delta() -> None:
    z0 = Z0 + np.linspace(0.0, 1.0, N, dtype=np.float32)[None, :]
    res = _kor(HojdYta(yta_id="a", polygon=FYRKANT, delta_m=-0.3), z0)
    inne = _polygon_mask(FYRKANT, GEO)
    assert np.max(np.abs((res.mark_z - z0)[inne] + 0.3)) < 1e-5
    assert np.array_equal(res.mark_z[~inne], z0[~inne])


# ---------------------------------------------------------------- exakt kant, ingen trappa


def test_exakt_kant_ger_ratt_volym_men_cellmasken_trappar() -> None:
    poly = _rot(40.0, 40.0, 30.0, 12.0, 27.0)
    area = shapely.Polygon(poly).area
    cell = DX * DX
    ny = _kor(HojdYta(yta_id="a", polygon=poly, delta_m=1.0))
    gammal = _kor(HojdYta(yta_id="a", polygon=poly, hojd_rh2000=11.0))
    vol_ny = float(np.sum(ny.mark_z - Z0)) * cell
    vol_gammal = float(np.sum(gammal.mark_z - Z0)) * cell
    assert vol_ny == pytest.approx(area, rel=1e-4)  # exakt täckningsandel per kantcell
    assert abs(vol_gammal - area) > 5 * abs(vol_ny - area)
    # kantcellerna har mellanvärden (halvcellstrappan är borta)
    mellan = (ny.mark_z > 10.001) & (ny.mark_z < 10.999)
    assert np.count_nonzero(mellan) > 20
    assert not np.any((gammal.mark_z > 10.001) & (gammal.mark_z < 10.999))


# ---------------------------------------------------------------- slänt


def test_slant_ar_rak_med_lutning_1_pa_n_och_mots_basen() -> None:
    n = 3.0
    delta = 1.2
    res = _kor(HojdYta(yta_id="a", polygon=FYRKANT, delta_m=delta, slant_forhallande=n))
    gx, gy = _xy()
    # avstånd till rektangelns kant utanför (exakt, shapely)
    poly = shapely.Polygon(FYRKANT)
    d = shapely.distance(poly, shapely.points(gx, gy))
    ute = d > 0
    w = delta * n
    forvantat = 10.0 + delta * np.clip(1.0 - d / w, 0.0, None)
    assert np.max(np.abs(res.mark_z[ute] - forvantat[ute])) < 2e-4
    # Inget ändras längre bort än W, och slänten har markslag 4 (gräs) och ytans osäkerhet
    assert np.array_equal(res.mark_z[d >= w], Z0[d >= w])
    slant = ute & (d < w - 1e-6)
    assert np.all(res.markslag[slant] == 4)
    assert np.allclose(res.osakerhet_z[slant], 0.05)
    # lutning i en rät del: z sjunker 1/n per meter vinkelrätt ut från kanten
    rad = int(round((N * DX - 35.0) / DX))
    z_rad = res.mark_z[rad, :].astype(np.float64)
    kol_kant = int(round(50.0 / DX))
    steg = (z_rad[kol_kant + 1] - z_rad[kol_kant + 5]) / (4 * DX)
    assert steg == pytest.approx(1.0 / n, rel=2e-3)


def test_slant_skar_ned_ocksa_for_negativ_delta_och_med_fast_bredd() -> None:
    res = _kor(HojdYta(yta_id="a", polygon=FYRKANT, delta_m=-0.5, slant_bredd_m=4.0,
                       slant_markslag=7))
    gx, gy = _xy()
    d = shapely.distance(shapely.Polygon(FYRKANT), shapely.points(gx, gy))
    ute = (d > 0) & (d < 4.0 - 1e-6)
    assert np.max(np.abs(res.mark_z[ute] - (10.0 - 0.5 * (1 - d[ute] / 4.0)))) < 2e-4
    assert np.all(res.markslag[ute] == 7)
    assert np.array_equal(res.mark_z[d >= 4.0], Z0[d >= 4.0])


def test_slant_hoppar_over_byggnadsceller_och_ar_deterministisk() -> None:
    slag = SLAG.copy()
    slag[40:60, 104:120] = 9  # byggnad i släntzonen öster om kvadraten (x = 52..60 m)
    lapp = ProjektLapp(
        lapp_id="t", bas_recept=("x",),
        hojdytor=(HojdYta(yta_id="a", polygon=FYRKANT, delta_m=1.0, slant_forhallande=3.0),),
    )
    a = tillampa_lapp(Z0, OSAK, slag, GEO, lapp, bas_recept_sha256="x", sektioner={})
    b = tillampa_lapp(Z0, OSAK, slag, GEO, lapp, bas_recept_sha256="x", sektioner={})
    assert a.mark_z.tobytes() == b.mark_z.tobytes()
    assert a.nytt_recept_sha256 == b.nytt_recept_sha256
    assert np.array_equal(a.mark_z[40:60, 104:120], Z0[40:60, 104:120])
    assert np.all(a.markslag[40:60, 104:120] == 9)


def test_slant_pa_lutande_yta_foljer_ytans_nivå_vid_kanten() -> None:
    """Δ(p) tas vid kantpunkten: på en lutande yta varierar släntens bredd längs kanten."""
    lut = {"a": [20, 35, 10.0], "b": [50, 35, 11.5]}
    res = _kor(HojdYta(yta_id="a", polygon=FYRKANT, lutning=lut, slant_forhallande=2.0))
    rad = int(round((N * DX - 35.0) / DX))
    z = res.mark_z[rad, :].astype(np.float64)
    kol_vast = int(round(20.0 / DX))  # väst: Δ = 0 (ytan möter basen), ingen slänt
    kol_ost = int(round(50.0 / DX))  # öst: Δ = 1,5, W = 3 m
    assert z[kol_vast - 2] == pytest.approx(10.0, abs=1e-6)
    assert z[kol_ost + 1] == pytest.approx(10.0 + 1.5 * (1 - 0.75 / 3.0), abs=1e-3)
    assert z[kol_ost + 6] == pytest.approx(10.0, abs=1e-6)  # utanför W = 3 m


# ---------------------------------------------------------------- K9 → K3


def _k9(egenskaper: dict) -> Andring:  # type: ignore[type-arg]
    ring = [[20, 20], [50, 20], [50, 50], [20, 50], [20, 20]]
    return Andring("markhojd", "m1", {"type": "Polygon", "coordinates": [ring]}, egenskaper)


def test_k9_markhojd_oversatts_med_lutning_delta_och_slant() -> None:
    y = markhojd_till_hojdyta(_k9({"niva_rh2000": 10.5}))
    assert y.hojd_rh2000 == 10.5 and not y.har_nya_falt
    y = markhojd_till_hojdyta(_k9({"delta_m": -0.2, "slant": {"forhallande": 3.0, "markslag": 4}}))
    assert y.delta_m == -0.2 and y.slant_forhallande == 3.0 and y.slant_markslag == 4
    y = markhojd_till_hojdyta(
        _k9({"lutning": {"punkt": [30, 30, 10.0], "fall": 0.02, "riktning_grader": 190},
             "slant": {"bredd_m": 4.0}})
    )
    assert y.lutning is not None and y.slant_bredd_m == 4.0


@pytest.mark.parametrize(
    "e",
    [
        {},
        {"niva_rh2000": 10.0, "delta_m": 0.1},
        {"delta_m": 0.1, "slant": {"forhallande": 3.0, "bredd_m": 2.0}},
        {"delta_m": 0.1, "slant": {"vad": 1}},
        {"delta_m": 0.1, "slant": 3.0},
        {"lutning": {"a": [0, 0, 1.0]}},
        {"landxml": "x.xml", "delta_m": 0.1},
    ],
)
def test_k9_markhojd_ogiltigt_ger_scenariofel(e: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ScenarioFel):
        markhojd_till_hojdyta(_k9(e))
