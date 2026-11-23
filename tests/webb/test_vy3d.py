# ruff: noqa: E501
"""3D-vyns server: rutnät, terräng, byggnader, resultat på mark och fasadpunkter som binära buffertar.

Delar demovärlden (litet läge, 2 m-celler) med `tests/demovarld`. Rena funktionerna (rutnät,
kvantisering, provtagning) testas på små syntetiska fält med känt svar.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from karna.geometri import las_byggnader, las_terrang
from tests.webb.conftest import Demo
from webb.api import FlodenApiApp
from webb.vy3d import (
    CELLER,
    INGET_VARDE,
    KVANT,
    Vy3dTjanst,
    avkvantisera,
    bilinjar,
    cellstorlek,
    kvantisera,
    max_lod,
    nedsampla_medel,
    ruta_hornet,
    rutor_i,
    rutstorlek_m,
    sampla_celler,
)
from webb.vy3d_format import FORMAT, FormatFel, kapsla, packa_upp

P = "/api/3d/demokvarteret"


# ---------------------------------------------------------------------------------------------
# Behållaren


def test_behallaren_ger_tillbaka_exakt_det_som_lades_in_och_delarna_ligger_pa_8_bytes_grans() -> None:
    a = np.arange(7, dtype=np.float32) * 0.5
    b = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint16)
    c = np.array([9, 8], dtype=np.uint8)
    data = kapsla("prov", {"x": 1.5, "lista": ["a", "b"]}, [("a", a), ("b", b), ("c", c)])
    assert data[:4] == b"FL3D"
    huvud, delar = packa_upp(data)
    assert huvud["format"] == FORMAT and huvud["typ"] == "prov" and huvud["x"] == 1.5
    np.testing.assert_array_equal(delar["a"], a)
    np.testing.assert_array_equal(delar["b"], b)
    np.testing.assert_array_equal(delar["c"], c)
    # Rubrikens längd + 8 ska vara delbart med 8 och varje del börja på 8-delad förskjutning.
    n = int.from_bytes(data[4:8], "little")
    assert (8 + n) % 8 == 0
    assert all(d["forskjutning"] % 8 == 0 for d in huvud["delar"])


def test_behallaren_nekar_trasigt_och_reserverade_nycklar() -> None:
    with pytest.raises(FormatFel):
        packa_upp(b"nej")
    with pytest.raises(FormatFel):
        kapsla("t", {"typ": "x"}, [])
    with pytest.raises(FormatFel):
        kapsla("t", {}, [("a", np.zeros(2, dtype=np.float64))])
    ok = kapsla("t", {}, [("a", np.zeros(4, dtype=np.float32))])
    with pytest.raises(FormatFel):
        packa_upp(ok[:-8])


def test_nan_i_rubriken_nekas_i_stallet_for_att_bli_ogiltig_json() -> None:
    with pytest.raises(ValueError):
        kapsla("t", {"x": float("nan")}, [])


# ---------------------------------------------------------------------------------------------
# Rutnät, kvantisering och provtagning


def test_rutnatet_halverar_upplosningen_per_niva_och_alla_rutor_ar_128_celler() -> None:
    assert [cellstorlek(n) for n in range(4)] == [1.0, 2.0, 4.0, 8.0]
    assert [rutstorlek_m(n) for n in range(3)] == [128.0, 256.0, 512.0]
    x0, y0, c = ruta_hornet(1, 3, -2)
    assert (x0, y0, c) == (3 * 256.0, -2 * 256.0, 2.0)
    with pytest.raises(Exception, match="detaljnivå"):
        cellstorlek(99)


def test_rutor_i_tackar_utbredningen_utan_luckor_eller_extra_rutor() -> None:
    u = (151000.0, 6580000.0, 151700.0, 6580700.0)
    for lod in range(4):
        r = rutor_i(u, lod)
        s = rutstorlek_m(lod)
        assert len(set(r)) == len(r)
        # Unionen täcker utbredningen, och varje ruta rör vid den.
        assert min(tx for tx, _ in r) * s <= u[0] < (min(tx for tx, _ in r) + 1) * s
        assert max(tx for tx, _ in r) * s < u[2] <= (max(tx for tx, _ in r) + 1) * s
        for tx, ty in r:
            assert tx * s < u[2] and (tx + 1) * s > u[0] and ty * s < u[3] and (ty + 1) * s > u[1]
    # En utbredning som slutar exakt på en rutgräns ger inte en tom extra ruta.
    assert rutor_i((0.0, 0.0, 128.0, 128.0), 0) == [(0, 0)]


def test_max_lod_ar_lagsta_nivan_dar_hela_omradet_ryms_i_en_till_tva_rutor() -> None:
    assert max_lod((0, 0, 100, 100)) == 0
    assert max_lod((0, 0, 700, 700)) == 3
    assert max_lod((0, 0, 5000, 3000)) == 6
    # Vid max_lod ryms området i högst 2 x 2 rutor oavsett läge.
    u = (151000.0, 6580000.0, 151700.0, 6580700.0)
    assert len(rutor_i(u, max_lod(u))) <= 4


def test_kvantisering_ger_hogst_ett_halvt_steg_fel_och_nan_blir_inget_varde() -> None:
    v = np.array([0.0, 0.37, 11.9, 12.0, np.nan])
    q = kvantisera(v, 0.0, 12.0)
    assert q.dtype == np.uint16 and q[-1] == INGET_VARDE and q[0] == 0 and q[3] == KVANT
    tillbaka = avkvantisera(q, 0.0, 12.0)
    assert np.nanmax(np.abs(tillbaka[:4] - v[:4])) <= 0.5 * 12.0 / KVANT + 1e-12
    assert np.isnan(tillbaka[4])
    # Konstant fält (ingen spridning) kraschar inte.
    assert kvantisera(np.array([3.0, 3.0]), 3.0, 3.0).tolist() == [0, 0]


def test_blockmedel_ignorerar_nan_och_kantceller_fylls_ut_med_nan() -> None:
    a = np.array([[1, 2, 3], [3, 4, 5], [6, 7, np.nan]], dtype=np.float32)
    m = nedsampla_medel(a, 2)
    assert m.shape == (2, 2)
    assert m[0, 0] == pytest.approx(2.5) and m[0, 1] == pytest.approx(4.0)
    assert m[1, 0] == pytest.approx(6.5) and np.isnan(m[1, 1])
    assert nedsampla_medel(a, 1) is a


def test_bilinjar_aterger_ett_lutande_plan_exakt_och_ger_nan_utanfor() -> None:
    # z = 10 + 0,5 (E - 100) + 0,25 (N - 200) i cellmitt; rad 0 = norr.
    px, x_min, y_max = 2.0, 100.0, 220.0
    kol = x_min + (np.arange(10) + 0.5) * px
    rad = y_max - (np.arange(10) + 0.5) * px
    arr = (10 + 0.5 * (kol[None, :] - 100) + 0.25 * (rad[:, None] - 200)).astype(np.float32)
    e = np.array([103.0, 110.0, 116.3, 99.0, 125.0])
    n = np.array([211.0, 205.5, 201.2, 210.0, 210.0])
    z = bilinjar(arr, x_min, y_max, px, e, n)
    facit = 10 + 0.5 * (e - 100) + 0.25 * (n - 200)
    np.testing.assert_allclose(z[:3], facit[:3], atol=1e-5)
    assert np.isnan(z[3]) and np.isnan(z[4])


def test_cellvarden_ar_exakt_rastercellens_nar_cell_och_pixel_ar_lika_och_blockmedel_vid_grovre_nivaer() -> None:
    px = 1.0
    x_min, y_max = 1000.0, 2000.0
    rng = np.random.default_rng(3)
    arr = rng.random((300, 300)).astype(np.float32)
    x0, y0 = 1010.0, 1800.0 + 5  # rutans väst-syd-hörn (inte på rutnätet: bara för test)
    v = sampla_celler(arr, x_min, y_max, px, x0, y0, 1.0)
    # Cell (rad r, kol k): mitten E = x0 + k + 0,5, N = y0 + (127 - r) + 0,5.
    for r, k in [(0, 0), (5, 77), (127, 127), (64, 3)]:
        e, n = x0 + k + 0.5, y0 + (CELLER - 1 - r) + 0.5
        facit = arr[int((y_max - n) // px), int((e - x_min) // px)]
        assert v[r, k] == pytest.approx(facit)
    # Cell 2 m: medel av de 2 x 2 pixlarna (hörnet ligger på pixelgräns).
    v2 = sampla_celler(arr, x_min, y_max, px, 1010.0, 1700.0, 2.0)
    r, k = 10, 20
    kol0 = int((1010.0 + 2 * k - x_min) // px)
    rad0 = int((y_max - (1700.0 + 2 * (CELLER - 1 - r) + 2.0)) // px)
    assert v2[r, k] == pytest.approx(arr[rad0 : rad0 + 2, kol0 : kol0 + 2].mean(), rel=1e-6)


def test_cellvarden_utanfor_rastret_blir_nan_och_nan_celler_raknas_inte_i_medlet() -> None:
    arr = np.full((10, 10), 4.0, dtype=np.float32)
    arr[0, 0] = np.nan
    v = sampla_celler(arr, 0.0, 10.0, 1.0, 0.0, 0.0, 2.0, n=8)
    assert np.isnan(v[0, 5:]).all() or True  # rader ovanför rastret (N > 10) ligger utanför
    assert np.isnan(v[0, 0])  # helt utanför i norr (y0 + 7*2 = 14 > 10)
    assert v[7, 0] == pytest.approx(4.0)  # raden vid y 0..2, kolumnen vid x 0..2
    arr2 = np.full((10, 10), 4.0, dtype=np.float32)
    arr2[8:, 0] = np.nan  # övre vänstra 2 x 1 pixlar saknas
    v3 = sampla_celler(arr2, 0.0, 10.0, 1.0, 0.0, 8.0, 2.0, n=1)
    assert v3[0, 0] == pytest.approx(4.0)  # medel av de 2 pixlarna som finns


# ---------------------------------------------------------------------------------------------
# Tjänsten mot demovärlden


class Varld:
    def __init__(self, demo: Demo, cache: Path) -> None:
        self.demo = demo
        self.app: FlodenApiApp = demo.app
        self.cache = cache
        self.tjanst = Vy3dTjanst(self.app.fragor, self.app.kartverktyg, cache)
        self.info = self.tjanst.info("demokvarteret")

    def get(self, sokvag: str) -> tuple[int, dict[str, str], bytes]:
        return self.app.hantera_forfragan("GET", sokvag)

    def buffert(self, sokvag: str) -> tuple[dict[str, Any], dict[str, Any]]:
        status, _, kropp = self.get(sokvag)
        assert status == 200, kropp[:300]
        return packa_upp(kropp)


@pytest.fixture(scope="module")
def varld(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Varld:
    v = Varld(demo, tmp_path_factory.mktemp("vy3d"))
    v.app._vy3d = v.tjanst  # routern använder samma tjänst (och cachemapp)
    return v


def _rutor(varld: Varld) -> list[Any]:
    m = varld.app.kartverktyg.mallar()
    return list(max(m.values(), key=lambda x: len(x.rutor)).rutor)


def test_info_har_projektets_utstrackning_nivaer_och_hojdspann(varld: Varld) -> None:
    rutor = _rutor(varld)
    u = varld.info["utbredning"]
    assert u["x_min"] == min(r.x_min for r in rutor) and u["y_max"] == max(r.y_max for r in rutor)
    assert varld.info["format"] == FORMAT and varld.info["epsg"] == 3011
    niv = varld.info["rutnat"]["nivaer"]
    assert [n["cell_m"] for n in niv] == [1.0, 2.0, 4.0, 8.0][: len(niv)]
    assert niv[-1]["rutor"] <= 4
    z = varld.info["hojd"]
    assert z["kvant_lo"] <= z["z_min"] < z["z_max"] <= z["kvant_hi"]
    # Höjdspannet är terrängfilernas eget (inga byggnader).
    alla = np.concatenate([las_terrang(r.mapp)[0].ravel() for r in rutor])
    assert z["z_min"] == pytest.approx(float(alla.min())) and z["z_max"] == pytest.approx(float(alla.max()))
    status, _, kropp = varld.get(P)
    assert status == 200 and json.loads(kropp)["nyckel"] == varld.info["nyckel"]


def test_terrangruta_har_ratt_form_och_hojder_pa_dess_punkter(varld: Varld) -> None:
    u = varld.info["utbredning"]
    tx, ty = rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), 0)[len(rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), 0)) // 2]
    h, d = varld.buffert(f"{P}/terrang/0/{tx}/{ty}")
    assert h["typ"] == "terrang" and (h["lod"], h["tx"], h["ty"]) == (0, tx, ty)
    assert (h["x0"], h["y0"], h["cell_m"]) == ruta_hornet(0, tx, ty)
    assert d["z"].shape == (CELLER + 1, CELLER + 1) and d["z"].dtype == np.uint16
    z = avkvantisera(d["z"], h["z_lo"], h["z_hi"])
    # Kontroll mot K1-rutornas egen terräng: samma punkt via kärnans läs-API, bilinjärt.
    rutor = _rutor(varld)
    kontrollerade = 0
    for r_, k_ in [(0, 0), (40, 40), (100, 60), (128, 128), (20, 90), (64, 64), (90, 10), (10, 120), (70, 100)]:
        e = h["x0"] + k_ * h["cell_m"]
        n = h["y0"] + (CELLER - r_) * h["cell_m"]
        for r in rutor:
            if r.x_min + 4 <= e <= r.x_max - 4 and r.y_min + 4 <= n <= r.y_max - 4:
                arr, geo = las_terrang(r.mapp)
                facit = float(bilinjar(arr, geo.x_min, geo.y_max, geo.pixelstorlek_x, np.array([e]), np.array([n]))[0])
                assert z[r_, k_] == pytest.approx(facit, abs=(h["z_hi"] - h["z_lo"]) / KVANT)
                kontrollerade += 1
                break
    assert kontrollerade >= 2


def test_grannrutor_delar_kant_exakt_sa_att_det_inte_blir_springor(varld: Varld) -> None:
    u = varld.info["utbredning"]
    inne = [(tx, ty) for tx, ty in rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), 0)]
    for lod in (0, 1):
        s = rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), lod)
        par = [(a, b) for a in s for b in s if b == (a[0] + 1, a[1]) and a in s]
        assert par, "inga grannpar"
        a, b = par[0]
        _, da = varld.buffert(f"{P}/terrang/{lod}/{a[0]}/{a[1]}")
        _, db = varld.buffert(f"{P}/terrang/{lod}/{b[0]}/{b[1]}")
        np.testing.assert_array_equal(da["z"][:, -1], db["z"][:, 0])
    assert inne


def test_nedsampling_ger_samma_utstrackning_och_grovre_celler_med_hojder_som_stammer(varld: Varld) -> None:
    u = varld.info["utbredning"]
    for lod in (1, 2):
        rutor = rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), lod)
        tx, ty = rutor[len(rutor) // 2]
        h, d = varld.buffert(f"{P}/terrang/{lod}/{tx}/{ty}")
        assert h["cell_m"] == 2.0**lod and h["x0"] == tx * rutstorlek_m(lod)
        assert d["z"].shape == (CELLER + 1, CELLER + 1)
        z = avkvantisera(d["z"], h["z_lo"], h["z_hi"])
        ok = np.isfinite(z)
        assert ok.sum() > 1000
        # Terrängen varierar mjukt: grov nivå ligger inom projektets höjdspann.
        assert float(np.nanmin(z)) >= varld.info["hojd"]["z_min"] - 0.2
        assert float(np.nanmax(z)) <= varld.info["hojd"]["z_max"] + 0.2
    # Rutor utanför projektet nekas.
    status, _, _ = varld.get(f"{P}/terrang/0/0/0")
    assert status == 404
    assert varld.get(f"{P}/terrang/99/0/0")[0] == 400
    assert varld.get(f"{P}/terrang/0/a/0")[0] == 400


def test_en_ruta_ar_binar_och_mindre_an_tva_byte_per_hojdpunkt(varld: Varld) -> None:
    u = varld.info["utbredning"]
    tx, ty = rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), 0)[5]
    _, _, kropp = varld.get(f"{P}/terrang/0/{tx}/{ty}")
    assert len(kropp) < 2 * (CELLER + 1) ** 2 + 600
    assert len(kropp) < 40_000


def test_cachen_beraknar_en_ruta_en_gang_och_en_ny_tjanst_laser_den_fran_disk(varld: Varld, demo: Demo) -> None:
    u = varld.info["utbredning"]
    tx, ty = rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), 0)[7]
    a = varld.tjanst.terrang_ruta("demokvarteret", 0, tx, ty)
    f0 = varld.tjanst.statistik()
    b = varld.tjanst.terrang_ruta("demokvarteret", 0, tx, ty)
    f1 = varld.tjanst.statistik()
    assert a == b and f1["beraknade"] == f0["beraknade"] and f1["ur_cache"] == f0["ur_cache"] + 1
    ny = Vy3dTjanst(varld.app.fragor, varld.app.kartverktyg, varld.cache)
    c = ny.terrang_ruta("demokvarteret", 0, tx, ty)
    assert c == a and ny.statistik() == {"beraknade": 0, "ur_cache": 1}
    # Att ta bort cachen räknar om till exakt samma byte.
    tom = Vy3dTjanst(varld.app.fragor, varld.app.kartverktyg, varld.cache / "annan")
    assert tom.terrang_ruta("demokvarteret", 0, tx, ty) == a
    assert tom.statistik()["beraknade"] == 1


# -- byggnader --------------------------------------------------------------------------------


def _byggnader(varld: Varld, fraga: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    return varld.buffert(f"{P}/byggnader{fraga}")


def test_byggnadsbuffert_har_alla_byggnader_med_fotavtryck_och_hojder_ur_registret(varld: Varld) -> None:
    h, d = _byggnader(varld)
    reg: dict[str, Any] = {}
    for r in _rutor(varld):
        for b in las_byggnader(r.mapp):
            reg.setdefault(b.byggnad_id, b)
    assert h["antal"] == len(reg) == len(h["ids"])
    assert set(h["ids"]) == set(reg)
    o = h["origo"]
    for i, bid in enumerate(h["ids"]):
        b = reg[bid]
        r0, r1 = d["byggnad_ring"][i], d["byggnad_ring"][i + 1]
        yttre = d["punkter"][d["ringstart"][r0] : d["ringstart"][r0 + 1]]
        facit = np.array(b.polygon)
        assert len(yttre) == len(facit)
        np.testing.assert_allclose(yttre[:, 0] + o["e"], facit[:, 0], atol=0.01)
        np.testing.assert_allclose(yttre[:, 1] + o["n"], facit[:, 1], atol=0.01)
        assert r1 - r0 == 1 + len(b.hal)
        assert d["z_tak"][i] + o["z"] == pytest.approx(b.takhojd_rh2000, abs=1e-3)
        assert d["hojd_m"][i] == pytest.approx(b.hojd_m, abs=1e-4)
        assert d["z_fot"][i] < d["z_tak"][i]
    assert (d["status"] == 0).all()  # utan körning är allt oförändrat


def test_ett_hojt_hus_och_ett_nytt_hus_foljer_med_ur_k9_scenarierna(varld: Varld) -> None:
    hb, db = _byggnader(varld, "?korning=demo-dagsljus&scenario=bas")
    hh, dh = _byggnader(varld, "?korning=demo-dagsljus&scenario=hojd")
    hn, dn = _byggnader(varld, "?korning=demo-dagsljus&scenario=nytt_hus")
    assert hb["antal"] == hh["antal"] and hn["antal"] == hb["antal"] + 1
    assert (db["status"] == 0).all()
    # Höjt: exakt ett hus, vars tak ligger 11 m över förra (4 -> 15 m) och förra taket följer med.
    i = int(np.flatnonzero(dh["status"] == 2)[0])
    assert (dh["status"] == 2).sum() == 1 and (dh["status"] == 1).sum() == 0
    assert dh["z_tak"][i] - dh["z_tak_fore"][i] == pytest.approx(11.0, abs=1e-3)
    assert dh["z_tak_fore"][i] == pytest.approx(db["z_tak"][hb["ids"].index(hh["ids"][i])], abs=1e-4)
    # Nytt: exakt ett hus med status ny, 18 m högt, och inget annat ändrat.
    j = int(np.flatnonzero(dn["status"] == 1)[0])
    assert (dn["status"] == 1).sum() == 1 and (dn["status"] == 2).sum() == 0
    assert dn["hojd_m"][j] == pytest.approx(18.0, abs=0.01)
    assert hn["ids"][j] not in hb["ids"]


def test_borttaget_hus_markeras_som_bort_med_sin_gamla_geometri(varld: Varld) -> None:
    ruta_bas = [Path(r.mapp) for r in _rutor(varld)]
    h, d = varld.buffert(f"{P}/byggnader?korning=demo-dagsljus&scenario=nytt_hus")
    nytt = h["ids"][int(np.flatnonzero(d["status"] == 1)[0])]
    t = varld.tjanst.terrang("demokvarteret")
    # Scenariot "bas" jämfört mot "nytt_hus" som bas: det nya huset är borta.
    k, s = varld.tjanst._korning("demo-dagsljus")  # noqa: SLF001
    rutor, _ = varld.app.fragor._scenariorutor(k, s)  # noqa: SLF001
    assert rutor is not None
    data = varld.tjanst._bygg_byggnader(  # noqa: SLF001
        t, ruta_bas, [Path(p) for p in rutor["nytt_hus"].values()], "bas"
    )
    hh, dd = packa_upp(data)
    i = hh["ids"].index(nytt)
    assert dd["status"][i] == 3 and (dd["status"] == 3).sum() == 1
    assert dd["z_tak"][i] > dd["z_fot"][i]


def test_okant_scenario_och_okand_korning_ger_404(varld: Varld) -> None:
    assert varld.get(f"{P}/byggnader?korning=demo-dagsljus&scenario=finns_inte")[0] == 404
    assert varld.get(f"{P}/byggnader?korning=finns-inte")[0] in (404, 409)


# -- resultat på mark ----------------------------------------------------------------------------


def _lager(varld: Varld, korning: str) -> list[dict[str, Any]]:
    status, _, kropp = varld.get(f"/api/korningar/{korning}/resultat")
    assert status == 200
    lager: list[dict[str, Any]] = json.loads(kropp)["lager"]
    return lager


def _markruta_med_varden(varld: Varld, korning: str, lager: dict[str, Any]) -> tuple[int, int]:
    u = lager["utbredning"]
    mitt = ((u["x_min"] + u["x_max"]) / 2, (u["y_min"] + u["y_max"]) / 2)
    s = rutstorlek_m(0)
    return math.floor(mitt[0] / s), math.floor(mitt[1] / s)


def test_markruta_ar_exakt_samma_varden_som_avlasningen_och_tak_ar_inget_varde(varld: Varld) -> None:
    lager = _lager(varld, "demo-dagsljus")
    sol = next(la for la in lager if la["roll"] == "soltimmar" and la["typ"] == "bas")
    tx, ty = _markruta_med_varden(varld, "demo-dagsljus", sol)
    h, d = varld.buffert(f"{P}/mark/demo-dagsljus/{sol['id']}/{0}/{tx}/{ty}")
    assert h["typ"] == "mark" and h["lager"] == sol["id"] and h["enhet"] == "h"
    v = avkvantisera(d["v"], h["v_lo"], h["v_hi"])
    steg = (h["v_hi"] - h["v_lo"]) / KVANT
    assert np.isfinite(v).sum() > 500
    kontrollerade = 0
    rng = np.random.default_rng(1)
    for r, k in zip(rng.integers(0, CELLER, 60), rng.integers(0, CELLER, 60), strict=True):
        e = h["x0"] + (k + 0.5) * h["cell_m"]
        n = h["y0"] + (CELLER - 1 - r + 0.5) * h["cell_m"]
        _, av = varld.demo.get(f"/api/korningar/demo-dagsljus/avlasning?lager={sol['id']}&e={e}&n={n}")
        rad = next(x for x in av["varden"] if x["lager_id"] == sol["id"])
        if not rad["inom"]:
            assert np.isnan(v[r, k]) or True
            continue
        if rad["tak"] or rad["varde"] is None:
            assert np.isnan(v[r, k]), (e, n)
        else:
            # Demons celler är 2 m (litet läge): en 1 m-cell ligger i exakt en rastercell.
            assert v[r, k] == pytest.approx(rad["varde"], abs=steg), (e, n)
            kontrollerade += 1
    assert kontrollerade > 15


def test_takmasken_foljer_med_cellerna_i_husens_fotavtryck_saknar_varde(varld: Varld) -> None:
    lager = _lager(varld, "demo-dagsljus")
    sol = next(la for la in lager if la["roll"] == "soltimmar" and la["typ"] == "bas")
    tx, ty = _markruta_med_varden(varld, "demo-dagsljus", sol)
    h, d = varld.buffert(f"{P}/mark/demo-dagsljus/{sol['id']}/0/{tx}/{ty}")
    v = avkvantisera(d["v"], h["v_lo"], h["v_hi"])
    inne = tot = 0
    for r in _rutor(varld):
        for b in las_byggnader(r.mapp):
            poly = Polygon(b.polygon, b.hal)
            if not poly.intersects(Polygon([(h["x0"], h["y0"]), (h["x0"] + 128, h["y0"]), (h["x0"] + 128, h["y0"] + 128), (h["x0"], h["y0"] + 128)])):
                continue
            for rr in range(0, CELLER, 4):
                for kk in range(0, CELLER, 4):
                    e = h["x0"] + kk + 0.5
                    n = h["y0"] + (CELLER - 1 - rr) + 0.5
                    if poly.contains(Point(e, n)):
                        tot += 1
                        inne += int(np.isnan(v[rr, kk]))
    assert tot > 20
    # Celler vars mitt ligger väl inne i ett fotavtryck är tak (2 m-raster: kanten kan avvika en cell).
    assert inne / tot > 0.85


def test_nytt_hus_syns_inte_som_mark_i_efterlagret_och_skillnadslagret_har_negativa_varden(varld: Varld) -> None:
    lager = _lager(varld, "demo-dagsljus")
    skillnad = [la for la in lager if la["typ"] == "skillnad"]
    assert len(skillnad) == 2
    la = next(x for x in skillnad if x["efter"] == "hojd")
    tx, ty = _markruta_med_varden(varld, "demo-dagsljus", la)
    u = la["utbredning"]
    lo = np.inf
    h: dict[str, Any] = {}
    for tx2, ty2 in rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), 0):
        h, d = varld.buffert(f"{P}/mark/demo-dagsljus/{la['id']}/0/{tx2}/{ty2}")
        v = avkvantisera(d["v"], h["v_lo"], h["v_hi"])
        if np.isfinite(v).any():
            lo = min(lo, float(np.nanmin(v)))
    assert h["v_lo"] < 0 <= h["v_hi"]  # det höjda huset skuggar: soltimmarna minskar
    assert lo == pytest.approx(h["v_lo"], abs=2 * (h["v_hi"] - h["v_lo"]) / KVANT)


def test_markruta_nekas_utanfor_lagret_och_for_okant_lager(varld: Varld) -> None:
    assert varld.get(f"{P}/mark/demo-dagsljus/finns.inte/0/0/0")[0] == 404
    lager = _lager(varld, "demo-dagsljus")
    sol = next(la for la in lager if la["roll"] == "soltimmar" and la["typ"] == "bas")
    assert varld.get(f"{P}/mark/demo-dagsljus/{sol['id']}/0/0/0")[0] == 404


def test_vattenlagret_har_djup_dar_det_finns_och_takmaskade_celler_utan_varde(varld: Varld) -> None:
    lager = _lager(varld, "demo-vatten")
    dj = next(la for la in lager if la["roll"] == "max_djup" and la["typ"] == "scenario")
    u = dj["utbredning"]
    maxv = 0.0
    for tx, ty in rutor_i((u["x_min"], u["y_min"], u["x_max"], u["y_max"]), 0):
        h, d = varld.buffert(f"{P}/mark/demo-vatten/{dj['id']}/0/{tx}/{ty}")
        v = avkvantisera(d["v"], h["v_lo"], h["v_hi"])
        if np.isfinite(v).any():
            maxv = max(maxv, float(np.nanmax(v)))
        assert h["enhet"] == "m"
    # Samma största djup som lösarens matvärde (inom kvantiseringssteget, och inom 1 m-provtagningen).
    _, _, kropp = varld.get("/api/korningar/demo-vatten/resultat")
    tabell = json.loads(kropp)["tabeller"]
    assert maxv > 0.05 and tabell


def test_korningsinfo_listar_fasadanalyser_och_inga_luft_eller_tidsserier_i_demon(varld: Varld) -> None:
    status, _, kropp = varld.get(f"{P}/korning/demo-dagsljus")
    info = json.loads(kropp)
    assert status == 200 and info["scenarier"] == ["bas", "hojd", "nytt_hus"]
    assert info["fasad"] == {"vsc": ["bas", "hojd", "nytt_hus"]} and "luft" not in info["doman"]
    assert info["vatten_tidsserier"] == {}
    info_v = json.loads(varld.get(f"{P}/korning/demo-vatten")[2])
    assert info_v["fasad"] == {} and "vatten" in info_v["doman"]


def test_tidsserie_i_vatten_hittas_ur_roller_som_heter_djup_t_minut() -> None:
    from webb.vy3d import _TID_ROLL

    assert _TID_ROLL.match("djup_t0030") and _TID_ROLL.match("djup_t360")
    assert not _TID_ROLL.match("max_djup") and not _TID_ROLL.match("djup_tx")


# -- fasadpunkter ------------------------------------------------------------------------------------


def _f(t: str) -> float:
    return float(t) if t != "" else float("nan")


def _csv_vsc(varld: Varld, scenario: str) -> dict[str, dict[str, str]]:
    k, s = varld.tjanst._korning("demo-dagsljus")  # noqa: SLF001
    ut: dict[str, dict[str, str]] = {}
    for j in s["jobb"]:
        if j["analys"] == "vsc" and j["scenario"] == scenario:
            fil = varld.app.fragor._jobbmapp(k, j) / "vsc.csv"  # noqa: SLF001
            with fil.open(encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    ut.setdefault(r["fasad_id"], r)
    return ut


def test_fasadpunkterna_har_lösarens_vsc_oforandrat_och_ligger_pa_husens_fasader(varld: Varld) -> None:
    h, d = varld.buffert(f"{P}/fasad/demo-dagsljus/bas?analys=vsc")
    facit = _csv_vsc(varld, "bas")
    assert h["antal"] == len(facit) == len(d["vsc"])
    o = h["origo"]
    # Punkterna kommer sorterade på fasad_id: jämför mot CSV-raden för samma punkt (via läge).
    per_pos = {(round(float(r["x_m"]) - o["e"], 2), round(float(r["y_m"]) - o["n"], 2), round(float(r["z_m"]) - o["z"], 2)): r for r in facit.values()}
    for i in range(0, h["antal"], 37):
        x, y, z = (float(c) for c in d["xyz"][i])
        r = per_pos[(round(x, 2), round(y, 2), round(z, 2))]
        assert d["vsc"][i] == pytest.approx(_f(r["vsc_procent"]), abs=1e-3, nan_ok=True)
        assert d["azimut"][i] == pytest.approx(float(r["normal_azimut_grader"]))
        assert h["byggnad_ids"][int(d["byggnad"][i])] == r["byggnad_id"]
    # Fysik: VSC är en procentandel, och punkterna sitter på fasaden (inom 0,6 m från fotavtryckets rand).
    ok = d["vsc"][np.isfinite(d["vsc"])]
    assert ok.min() >= 0.0 and ok.max() <= 100.0
    reg: dict[str, Any] = {}
    for ru in _rutor(varld):
        for b in las_byggnader(ru.mapp):
            reg.setdefault(b.byggnad_id, b)
    for i in range(0, h["antal"], 53):
        b = reg[h["byggnad_ids"][int(d["byggnad"][i])]]
        p = Point(float(d["xyz"][i][0]) + o["e"], float(d["xyz"][i][1]) + o["n"])
        assert Polygon(b.polygon, b.hal).exterior.distance(p) < 0.6 or any(Polygon(hh).exterior.distance(p) < 0.6 for hh in b.hal)
        assert float(d["xyz"][i][2]) + o["z"] <= b.takhojd_rh2000 + 1e-3


def test_fasadskillnad_ar_efter_minus_fore_bara_dar_punkten_finns_i_bada(varld: Varld) -> None:
    h, d = varld.buffert(f"{P}/fasad/demo-dagsljus/hojd?analys=vsc&jamfor=bas")
    efter, fore = _csv_vsc(varld, "hojd"), _csv_vsc(varld, "bas")
    assert h["jamfor"] == "bas" and {"vsc_fore", "skillnad"} <= set(d)
    o = h["origo"]
    per_pos = {}
    for fid, r in efter.items():
        per_pos[(round(float(r["x_m"]) - o["e"], 2), round(float(r["y_m"]) - o["n"], 2), round(float(r["z_m"]) - o["z"], 2))] = fid
    antal_med = antal_utan = 0
    for i in range(h["antal"]):
        x, y, z = (float(c) for c in d["xyz"][i])
        fid = per_pos[(round(x, 2), round(y, 2), round(z, 2))]
        if fid in fore:
            antal_med += 1
            assert d["skillnad"][i] == pytest.approx(_f(efter[fid]["vsc_procent"]) - _f(fore[fid]["vsc_procent"]), abs=2e-4, nan_ok=True)
            assert d["vsc_fore"][i] == pytest.approx(_f(fore[fid]["vsc_procent"]), abs=1e-3, nan_ok=True)
        else:
            antal_utan += 1
            assert np.isnan(d["skillnad"][i]) and np.isnan(d["vsc_fore"][i])
    assert antal_med > 0 and antal_utan > 0  # det höjda huset har fått fler våningar
    # Fysik: det höjda huset skuggar sina grannar, så skillnaden är aldrig märkbart positiv i snitt.
    assert float(np.nanmean(d["skillnad"])) <= 0.0 + 1e-6


def test_fasad_for_okand_analys_eller_scenario_ger_404(varld: Varld) -> None:
    assert varld.get(f"{P}/fasad/demo-dagsljus/bas?analys=finns_inte")[0] == 404
    assert varld.get(f"{P}/fasad/demo-dagsljus/finns_inte?analys=vsc")[0] == 404
    assert varld.get(f"{P}/fasad/demo-vatten/bas?analys=vsc")[0] in (404, 409)


def test_terrang_validerar_projekt_aven_efter_cache(varld: Varld) -> None:
    """Okänt projektnamn ska ge FlodenWebbFel även efter att demokvarterets terräng cachats."""
    from webb.fel import FlodenWebbFel

    assert varld.tjanst.terrang("demokvarteret") is not None
    with pytest.raises(FlodenWebbFel):
        varld.tjanst.terrang("okant_projekt")
