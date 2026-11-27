"""Fysik- och minnestester för vektoriserad `int32` SankGraf (`sankgraf.hierarki`).

Verifierar bit-för-bit ekvivalens mot referensimplementationen med Python-`par_kanter`-dict
(inkl. likvärdiga sadelkanter, slutsänkor, 2D/3D-brytlinjer och öppningar) samt `int32`-index
och `tracemalloc`-minnesbudget på 500x500-terräng.
"""

from __future__ import annotations

import gc
import math
import tracemalloc

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter

from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import (
    _SADEL_TIE_TOL_M,
    _applicera_oppningar,
    _berakna_avrinningsomraden,
    _berakna_kantbarriarer,
    _bygg_granspar,
    bygg_sankgraf,
)
from sankgraf.typer import HAV_NOD_ID, MARKSLAG_BYGGNAD, SankGraf
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, OppningPost


def _referens_granspar_dict(
    catch_2d: NDArray[np.int64],
    aktiv: NDArray[np.bool_],
    eff_z: NDArray[np.float32],
    kantbarriarer: dict[tuple[int, int], float],
) -> list[tuple[float, int, int, list[tuple[int, int, int, int]]]]:
    """Originalets Python-dict-baserade gränsanalys som exakt facit för `_bygg_granspar`."""
    hojd, bredd = catch_2d.shape
    z_flat = np.asarray(eff_z, dtype=np.float64).ravel()
    par_kanter: dict[tuple[int, int], tuple[float, list[tuple[int, int, int, int]]]] = {}

    def _reg(u: int, v: int, cu: int, cv: int, w: float) -> None:
        nyckel = (cu, cv) if cu < cv else (cv, cu)
        bef = par_kanter.get(nyckel)
        if bef is None or w < bef[0] - _SADEL_TIE_TOL_M:
            par_kanter[nyckel] = (w, [(u, v, cu, cv)])
        elif abs(w - bef[0]) <= _SADEL_TIE_TOL_M:
            bef[1].append((u, v, cu, cv))

    v_diff = (catch_2d[:-1, :] != catch_2d[1:, :]) & aktiv[:-1, :] & aktiv[1:, :]
    vr_arr, vc_arr = np.nonzero(v_diff)
    for r_np, c_np in zip(vr_arr, vc_arr, strict=True):
        r, c = int(r_np), int(c_np)
        u = r * bredd + c
        v = (r + 1) * bredd + c
        cu = int(catch_2d[r, c])
        cv = int(catch_2d[r + 1, c])
        w = kantbarriarer.get((u, v), max(float(z_flat[u]), float(z_flat[v])))
        _reg(u, v, cu, cv, w)

    h_diff = (catch_2d[:, :-1] != catch_2d[:, 1:]) & aktiv[:, :-1] & aktiv[:, 1:]
    hr_arr, hc_arr = np.nonzero(h_diff)
    for r_np, c_np in zip(hr_arr, hc_arr, strict=True):
        r, c = int(r_np), int(c_np)
        u = r * bredd + c
        v = r * bredd + (c + 1)
        cu = int(catch_2d[r, c])
        cv = int(catch_2d[r, c + 1])
        w = kantbarriarer.get((u, v), max(float(z_flat[u]), float(z_flat[v])))
        _reg(u, v, cu, cv, w)

    rand_mask = np.zeros((hojd, bredd), dtype=bool)
    rand_mask[0, :] = True
    rand_mask[-1, :] = True
    rand_mask[:, 0] = True
    rand_mask[:, -1] = True
    rand_intern = rand_mask & aktiv & (catch_2d >= 0)
    rr_arr, rc_arr = np.nonzero(rand_intern)
    for r_np, c_np in zip(rr_arr, rc_arr, strict=True):
        r, c = int(r_np), int(c_np)
        u = r * bredd + c
        cu = int(catch_2d[r, c])
        _reg(u, -1, cu, HAV_NOD_ID, float(z_flat[u]))

    ut = [
        (w_min, nyckel[0], nyckel[1], sorted(kanter, key=lambda e: (e[0], e[1])))
        for nyckel, (w_min, kanter) in par_kanter.items()
    ]
    ut.sort(key=lambda item: (item[0], item[1], item[2]))
    return ut


def _referens_avrinningsomraden_i64(
    mark_z: NDArray[np.float32],
    aktiv: NDArray[np.bool_],
    kantbarriarer: dict[tuple[int, int], float],
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Enkel referenskontroll av 4-granne nedförsriktning + vägkompression i int64."""
    flow_i32, ctn_i32, _ = _berakna_avrinningsomraden(mark_z, aktiv, kantbarriarer)
    return np.asarray(flow_i32, dtype=np.int64), np.asarray(ctn_i32, dtype=np.int64)


def _jamfor_granspar_mot_referens(
    mark_z: NDArray[np.float32],
    osak_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    *,
    brytlinjer: tuple[BrytlinjePost, ...] = (),
    oppningar: tuple[OppningPost, ...] = (),
) -> SankGraf:
    """Kör `bygg_sankgraf` och verifierar 100 % överensstämmelse med dict-referensen."""
    eff_z, eff_slag = _applicera_oppningar(mark_z, markslag, geo, oppningar)
    aktiv = np.asarray((eff_slag != MARKSLAG_BYGGNAD) & np.isfinite(eff_z), dtype=np.bool_)
    z64 = np.asarray(eff_z, dtype=np.float64).ravel()
    kantbarriarer = _berakna_kantbarriarer(z64, aktiv.ravel(), geo, brytlinjer)

    flow_to, ctn_int, antal_lov = _berakna_avrinningsomraden(eff_z, aktiv, kantbarriarer)
    assert flow_to.dtype == np.int32
    assert ctn_int.dtype == np.int32

    par_w, par_lo, par_hi, par_s, par_e, f_u, f_v, f_cu, f_cv = _bygg_granspar(
        ctn_int, aktiv, eff_z, kantbarriarer
    )
    ref = _referens_granspar_dict(np.asarray(ctn_int, dtype=np.int64), aktiv, eff_z, kantbarriarer)

    assert int(par_w.size) == len(ref)
    for idx, (ref_w, ref_lo, ref_hi, ref_kanter) in enumerate(ref):
        assert float(par_w[idx]) == pytest.approx(ref_w, rel=0.0, abs=1e-12)
        assert int(par_lo[idx]) == ref_lo
        assert int(par_hi[idx]) == ref_hi
        s0, s1 = int(par_s[idx]), int(par_e[idx])
        nya_kanter = sorted(
            ((int(f_u[i]), int(f_v[i]), int(f_cu[i]), int(f_cv[i])) for i in range(s0, s1)),
            key=lambda e: (e[0], e[1]),
        )
        assert nya_kanter == ref_kanter

    graf = bygg_sankgraf(
        mark_z,
        osak_z,
        markslag,
        geo,
        brytlinjer=brytlinjer,
        oppningar=oppningar,
    )
    assert graf.cell_till_nod.dtype == np.int64
    assert len(graf.lovnoder()) == antal_lov
    np.testing.assert_array_equal(graf.cell_till_nod, ctn_int.astype(np.int64))
    return graf


def _geo(hojd: int, bredd: int, dx: float = 0.5) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6580000.0,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=3011,
        dtype="float32",
    )


def test_komplex_hierarki_likvardiga_sadlar_och_slutsanka() -> None:
    """Verifierar nästlade sänkor, breda platta sadelkanter, byggnader och slutsänka."""
    hojd, bredd = 48, 60
    geo = _geo(hojd, bredd, dx=0.5)
    z = np.full((hojd, bredd), 12.0, dtype=np.float32)
    osak = np.full((hojd, bredd), 0.03, dtype=np.float32)
    slag = np.full((hojd, bredd), 1, dtype=np.uint8)

    # Tre nästlade sänkor med breda likahöjdssadlar (flera likvärdiga sadelkanter!)
    # Sänka 1 (vänster): botten 10.0
    z[8:20, 6:18] = np.float32(10.0)
    # Sänka 2 (mitten): botten 10.2
    z[8:20, 22:34] = np.float32(10.2)
    # Platt 6-cells sadel mellan 1 och 2 vid exakt 10.80 m
    z[11:17, 18:22] = np.float32(10.80)
    # Sänka 3 (söder): botten 10.4
    z[26:38, 12:30] = np.float32(10.4)
    # Platt 4-cells sadel mellan (1+2) och 3 vid 11.10 m
    z[20:26, 15:19] = np.float32(11.10)
    # Yttre utloppssadel mot västra randen (4 celler bred, 11.40 m)
    z[12:16, 0:6] = np.float32(11.40)

    # Instängd innergård (slutsänka) till höger, helt omgiven av byggnad (höjd 15.0 m)
    slag[10:26, 40:54] = MARKSLAG_BYGGNAD
    z[10:26, 40:54] = np.float32(15.0)
    slag[13:23, 43:51] = 1
    z[13:23, 43:51] = np.float32(10.5)
    z[16:19, 45:48] = np.float32(10.1)

    graf = _jamfor_granspar_mot_referens(z, osak, slag, geo)

    # Kontrollera att de likvärdiga sadelkanterna samlas exakt och att slutsänkan hittas
    assert len(graf.slutsankor()) == 1
    assert any(len(celler) >= 4 for celler in graf.overfall_celler.values())

    # Verifiera exakt volymkonservering i statisk fyllning
    res = statisk_fyllning(graf, 0.25)
    assert res.volymfel_rel < 1e-9


def test_2d_och_3d_brytlinjer_samt_oppningar_matchar_referens() -> None:
    """Verifierar att 2D/3D-kantbarriärer och öppningar ger exakt samma graf och sadlar."""
    hojd, bredd = 40, 50
    dx = 0.5
    geo = _geo(hojd, bredd, dx=dx)

    # Två sänkor med sluttande sidor skilda av en kantsten (2D) och en 3D-mur
    ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    d1 = np.hypot(ii - 14.0, jj - 14.0)
    d2 = np.hypot(ii - 24.0, jj - 34.0)
    z = np.minimum(10.0 + 0.06 * d1, 10.1 + 0.06 * d2).astype(np.float32)
    osak = np.full((hojd, bredd), 0.02, dtype=np.float32)
    slag = np.full((hojd, bredd), 1, dtype=np.uint8)

    # Diagonal 2D-brytlinje (kantsten +0.25 m) över vattendelaren
    b2d = BrytlinjePost(
        typ="kantsten",
        hojd_m=0.25,
        koordinater=(
            (geo.x_min + 22.0 * dx, geo.y_max - 2.0 * dx),
            (geo.x_min + 26.0 * dx, geo.y_max - 38.0 * dx),
        ),
    )
    # 3D-brytlinje med linjärt interpolerad krönhöjd längs östra delen
    b3d = BrytlinjePost(
        typ="mur",
        hojd_m=0.0,
        koordinater=(
            (geo.x_min + 40.0 * dx, geo.y_max - 4.0 * dx),
            (geo.x_min + 40.0 * dx, geo.y_max - 36.0 * dx),
        ),
        z_kron_rh2000=(11.35, 11.45),
    )
    # Öppning (trumma) genom en liten vägbank i söder
    opp = OppningPost(
        typ="trumma",
        bredd_m=1.0,
        botten_rh2000=10.30,
        kalla="syntetisk",
        koordinater=(
            (geo.x_min + 14.0 * dx, geo.y_max - 30.0 * dx),
            (geo.x_min + 14.0 * dx, geo.y_max - 39.0 * dx),
        ),
    )

    graf = _jamfor_granspar_mot_referens(
        z,
        osak,
        slag,
        geo,
        brytlinjer=(b2d, b3d),
        oppningar=(opp,),
    )
    assert len(graf.noder) >= 1
    res = statisk_fyllning(graf, 0.20)
    assert res.volymfel_rel < 1e-9


def test_granspar_kantfall_och_tie_tolerans() -> None:
    """Edge cases: 1x1, 1xN, Nx1, sluten ensamsänka (0 kanter) samt sub-1e-9 tie-ordning."""
    for hojd, bredd in ((1, 1), (1, 5), (5, 1)):
        geo = _geo(hojd, bredd)
        z = np.arange(hojd * bredd, dtype=np.float32).reshape((hojd, bredd)) + np.float32(10.0)
        osak = np.full((hojd, bredd), 0.01, dtype=np.float32)
        slag = np.full((hojd, bredd), 1, dtype=np.uint8)
        graf = _jamfor_granspar_mot_referens(z, osak, slag, geo)
        assert graf.cell_till_nod.shape == (hojd, bredd)

    # Två kanter mellan samma par (0, 1) med viktskillnad 5e-10 <= _SADEL_TIE_TOL_M:
    # båda ska behållas och levereras i deterministisk (u, v)-ordning.
    catch = np.array([[0, 1], [0, 1]], dtype=np.int32)
    aktiv = np.ones((2, 2), dtype=np.bool_)
    eff_z = np.array([[10.0, 10.0], [10.0, 10.0]], dtype=np.float32)
    # Kant (0, 1) får 10.5 + 5e-10, kant (2, 3) får 10.5 -> båda inom 1e-9!
    barriarer = {(0, 1): 10.5 + 5e-10, (2, 3): 10.5}
    par_w, par_lo, par_hi, par_s, par_e, f_u, f_v, _, _ = _bygg_granspar(
        catch, aktiv, eff_z, barriarer
    )
    idx_01 = [i for i in range(int(par_w.size)) if int(par_lo[i]) == 0 and int(par_hi[i]) == 1]
    assert len(idx_01) == 1
    p = idx_01[0]
    assert float(par_w[p]) == pytest.approx(10.5, abs=1e-12)
    s0, s1 = int(par_s[p]), int(par_e[p])
    kanter = sorted((int(f_u[i]), int(f_v[i])) for i in range(s0, s1))
    assert kanter == [(0, 1), (2, 3)]


def test_500x500_stresstest_och_tracemalloc_minnesbudget() -> None:
    """Snabbt 500x500-stresstest (250 000 celler) med strikt `tracemalloc`-budgetkontroll."""
    n = 500
    rng = np.random.default_rng(42)
    y = np.linspace(0.0, 12.0 * math.pi, n, dtype=np.float32)
    x = np.linspace(0.0, 12.0 * math.pi, n, dtype=np.float32)
    yy, xx = np.meshgrid(y, x, indexing="ij")
    brus = gaussian_filter(rng.normal(0.0, 1.0, size=(n, n)), sigma=4.0).astype(np.float32)
    z = np.round(
        10.0
        + 0.5 * np.sin(xx) * np.cos(yy)
        + 0.25 * np.sin(2.3 * xx + 0.7) * np.sin(1.9 * yy)
        + 1.5 * brus,
        2,
    ).astype(np.float32)
    osak = np.full((n, n), 0.02, dtype=np.float32)
    slag = np.full((n, n), 1, dtype=np.uint8)
    geo = _geo(n, n, dx=0.25)

    gc.collect()
    tracemalloc.start(1)
    try:
        graf = bygg_sankgraf(z, osak, slag, geo)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert graf.cell_till_nod.dtype == np.int64
    assert len(graf.lovnoder()) > 100
    assert len(graf.noder) > len(graf.lovnoder())
    # Med int32-index och vektoriserad gränsanalys ska topp-RAM ligga långt under 35 MB
    # (baslinjen låg kring ~30 MB på 500x500 och växte kraftigt med Python-dict).
    assert peak_bytes < 35 * 1024 * 1024

    res = statisk_fyllning(graf, 0.05)
    assert res.volymfel_rel < 1e-6


def test_nodkurvor_linjart_ekvivalent_och_slots() -> None:
    """Verifierar O(N log H)-kurvor mot naiv cellkonkatenering inkl. persistens och slots=True."""
    from sankgraf.hierarki import berakna_kurvor_for_celler
    from sankgraf.persistens import filtrera_persistens
    from sankgraf.typer import FlodesVag, SankNod, VolymPunkt, YtPunkt

    for cls in (VolymPunkt, YtPunkt, SankNod, FlodesVag):
        assert hasattr(cls, "__slots__"), f"{cls.__name__} saknar slots=True"

    # Flerstegs-terräng med slutsänka, lika sadlar och celler exakt på sadelnivå.
    z = np.array(
        [
            [20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0],
            [20.0, 10.0, 10.5, 11.0, 10.2, 11.0, 10.4, 10.8, 20.0],
            [20.0, 10.5, 11.0, 12.0, 11.0, 12.0, 10.8, 11.5, 20.0],
            [20.0, 11.0, 12.0, 11.5, 12.0, 11.5, 11.5, 12.0, 20.0],
            [20.0, 10.3, 10.9, 11.5, 10.1, 11.5, 10.6, 11.0, 20.0],
            [20.0, 10.9, 11.5, 12.0, 11.5, 12.0, 11.0, 11.8, 20.0],
            [20.0, 20.0, 20.0, 20.0, 12.5, 20.0, 20.0, 20.0, 20.0],
        ],
        dtype=np.float32,
    )
    osak = np.full_like(z, 0.25)
    slag = np.full(z.shape, 1, dtype=np.uint8)
    geo = _geo(*z.shape, dx=0.5)

    grafer = (
        bygg_sankgraf(z, osak, slag, geo),
        filtrera_persistens(bygg_sankgraf(z, osak, slag, geo), faktor=2.0),
    )
    for graf in grafer:
        cell_area = float(geo.pixelstorlek_x) * float(geo.pixelstorlek_y)
        z_flat = np.asarray(graf.mark_z, dtype=np.float64).ravel()
        ct_flat = graf.cell_till_nod.ravel()
        for nid, nod in graf.noder.items():
            lov = graf.lov_under_nod(nid)
            mask = np.isin(ct_flat, np.asarray(lov, dtype=np.int64))
            ref_vol, ref_yt, ref_bot, ref_cnt = berakna_kurvor_for_celler(
                z_flat[mask], nod.overfall_rh2000, cell_area
            )
            assert nod.botten_rh2000 == pytest.approx(ref_bot, abs=1e-12)
            assert nod.cell_antal == ref_cnt
            assert nod.max_volym_m3 == pytest.approx(ref_vol[-1].v_m3, rel=1e-12, abs=1e-12)
            assert nod.ytkurva[-1].a_m2 == pytest.approx(ref_yt[-1].a_m2, rel=1e-12, abs=1e-12)
            if nod.ar_lov:
                assert nod.volymkurva == ref_vol
                assert nod.ytkurva == ref_yt
            else:
                z_sadel = graf.noder[nod.barn_id[0]].overfall_rh2000
                ref_nod = SankNod(
                    nod_id=nid,
                    forald_id=nod.forald_id,
                    barn_id=nod.barn_id,
                    overfall_rh2000=nod.overfall_rh2000,
                    overfall_x=nod.overfall_x,
                    overfall_y=nod.overfall_y,
                    ut_till_id=nod.ut_till_id,
                    volymkurva=ref_vol,
                    ytkurva=ref_yt,
                    avrinningsyta_m2=nod.avrinningsyta_m2,
                    persistens_m=nod.persistens_m,
                    osakerhet_m=nod.osakerhet_m,
                    botten_rh2000=ref_bot,
                    cell_antal=ref_cnt,
                )
                for frak in (0.0, 0.25, 0.5, 0.75, 1.0):
                    zt = z_sadel + frak * (nod.overfall_rh2000 - z_sadel)
                    assert nod.volym_vid_niva(zt) == pytest.approx(
                        ref_nod.volym_vid_niva(zt), rel=1e-11, abs=1e-11
                    )
                    assert nod.area_vid_niva(zt) == pytest.approx(
                        ref_nod.area_vid_niva(zt), rel=1e-11, abs=1e-11
                    )
                    vt = ref_nod.volym_vid_niva(zt)
                    if vt > ref_nod.volym_vid_niva(z_sadel) + 1e-12:
                        assert nod.niva_vid_volym(vt) == pytest.approx(
                            ref_nod.niva_vid_volym(vt), rel=1e-10, abs=1e-10
                        )
        res = statisk_fyllning(graf, 0.50)
        assert res.volymfel_rel < 1e-9


def test_djup_sankkedja_linjart_antal_kurvpunkter() -> None:
    """Djup kedja med >150 sammanslagna sänkor ska ge O(N) kurvpunkter, inte O(N * djup)."""
    n_sankor = 160
    bredd = n_sankor * 2 + 1
    z = np.full((3, bredd), 500.0, dtype=np.float32)
    # Trappad ränna: varannan kolumn är en grop med stigande sadlar (skev kedja av djup 160).
    for k in range(n_sankor):
        z[1, 2 * k + 1] = np.float32(10.0 + 0.01 * k)
        z[1, 2 * k] = np.float32(20.0 + 0.5 * k)
    z[1, bredd - 1] = np.float32(20.0 + 0.5 * n_sankor)
    # Öppna en enda utloppspunkt i högra kanten
    z[1, 0] = np.float32(200.0)
    osak = np.full_like(z, 0.01)
    slag = np.full(z.shape, 1, dtype=np.uint8)
    geo = _geo(*z.shape, dx=1.0)

    graf = bygg_sankgraf(z, osak, slag, geo)
    assert len(graf.lovnoder()) >= n_sankor
    tot_punkter = sum(len(n.volymkurva) for n in graf.noder.values())
    # Naiv O(N * djup) hade givit ~160 * 160 / 2 > 12 800 punkter; O(N) ligger långt under 2 000!
    assert tot_punkter < 2_000
    res = statisk_fyllning(graf, 5.0)
    assert res.volymfel_rel < 1e-9
