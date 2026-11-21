# SPDX-License-Identifier: MIT
"""Tester för `luft.emission`: massbalans, jämvikt, fukt, åtgärder, testflagga och timvis halt.

Alla parametervärden kommer från `tp()` (märkta testvärden) eller är syntetiska.
"""

from __future__ import annotations

import dataclasses
import warnings

import numpy as np
import pytest

from luft.emission import (
    I_BROMS,
    I_DACK,
    I_SALT,
    I_VAG,
    KALLOR,
    STORLEKAR,
    Atgard,
    EmissionsResultat,
    Trafikprofil,
    VaderserieTimme,
    Vagavsnitt,
    _avdunsta,
    berakna_emission,
    emission_for_linjekalla,
    retentionsfaktor,
    timvis_halt,
)
from luft.emission import (
    testparametrar as tp,
)
from luft.geometri import LuftVarning, linjekalla_med_rapport, voxelisera
from luft.typer import LuftFel
from luft.vindbibliotek import Vaderserie, fordela

pytestmark = pytest.mark.filterwarnings("ignore::luft.geometri.LuftVarning")

AR_H = 8760


def _avsnitt(
    aid: str = "a",
    adt: float = 48_000.0,
    tung: float = 0.1,
    dubb: float | tuple[float, ...] = 0.5,
    v: float = 60.0,
    korfalt: int = 2,
) -> Vagavsnitt:
    return Vagavsnitt(
        id=aid,
        langd_m=250.0,
        adt_fordon_dygn=adt,
        andel_tung=tung,
        andel_dubbdack_latt=dubb,
        andel_dubbdack_tung=0.0,
        hastighet_km_h=v,
        antal_korfalt=korfalt,
        vagbredd_m=7.0,
    )


def _vader(
    n: int,
    regn_mm_h: float | np.ndarray = 0.0,
    e_pot: float | np.ndarray | None = 0.0,
    seed: int | None = None,
) -> VaderserieTimme:
    rng = np.random.default_rng(seed)
    regn = np.broadcast_to(np.asarray(regn_mm_h, dtype=np.float64), (n,)).copy()
    return VaderserieTimme(
        start="2025-01-01T00:00",
        temperatur_c=5.0 + 10.0 * rng.random(n) if seed is not None else np.full(n, 5.0),
        nederbord_mm_h=regn,
        vindhastighet_m_s=np.full(n, 3.0),
        relativ_fuktighet_procent=np.full(n, 70.0),
        vindhojd_m=10.0,
        temperaturhojd_m=2.0,
        lufttryck_hpa=np.full(n, 1010.0),
        nettostralning_w_m2=None if e_pot is not None else 100.0 * rng.random(n),
        potentiell_avdunstning_mm_h=(
            None if e_pot is None else np.broadcast_to(np.asarray(e_pot, float), (n,)).copy()
        ),
    )


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-300)


# ---------------------------------------------------------------------------
# Massbalans
# ---------------------------------------------------------------------------


def test_massbalans_exakt_over_ett_ar() -> None:
    rng = np.random.default_rng(1)
    regn = np.where(rng.random(AR_H) < 0.1, 3.0 * rng.random(AR_H), 0.0)
    vader = _vader(AR_H, regn, e_pot=None, seed=2)  # Penman med strålning
    manader = tuple(0.6 if m in (0, 1, 2, 10, 11) else 0.0 for m in range(12))
    avsnitt = [_avsnitt("a", dubb=manader), _avsnitt("b", adt=12_000, tung=0.05, v=40.0)]
    atgarder = [
        Atgard("sandning", 100, 200.0),
        Atgard("saltning", 200, 20.0, ("a",)),
        Atgard("dammbindning", 300, 100.0),
        Atgard("stadning", 400, 1.0),
        Atgard("stadning", 5000, 2.0, ("b",)),
    ]
    res = berakna_emission(avsnitt, vader, Trafikprofil.jamn(), tp(), atgarder)
    mb = res.massbalans
    produktion = float(np.sum(mb["produktion"]))
    bort = float(np.sum(mb["uppvirvling"] + mb["spray"] + mb["stadning"] + mb["dranering"]))
    delta = float(np.sum(mb["slut"] - mb["start"]))
    rel = abs(produktion - bort - delta) / produktion
    print(f"massbalans rel fel = {rel:.3e}, vattenbalans rel fel = {res.vattenbalans_relfel():.3e}")
    assert rel < 1e-12
    assert res.massbalans_relfel() < 1e-12
    assert res.vattenbalans_relfel() < 1e-12
    # Varje sänka är icke-negativ och alla sänkor har använts
    for nyckel in ("uppvirvling", "spray", "stadning", "dranering"):
        assert np.all(mb[nyckel] >= -1e-15)
        assert np.sum(mb[nyckel]) > 0
    assert np.all(res.pm10_g_m_s >= res.pm25_g_m_s)
    assert np.all(res.damm_g_m2 >= 0)


# ---------------------------------------------------------------------------
# Jämvikt
# ---------------------------------------------------------------------------


def test_jamvikt_analytisk() -> None:
    """Konstant produktion och uppvirvling, torrt: M* = P/k per källa och klass."""
    p = tp()
    adt, tung, dubb, v, korfalt = 48_000.0, 0.1, 0.4, 60.0, 2
    res = berakna_emission(
        [_avsnitt(adt=adt, tung=tung, dubb=dubb, v=v, korfalt=korfalt)],
        _vader(AR_H, 0.0, e_pot=0.0),
        Trafikprofil.jamn(),
        p,
    )
    nl, nt = adt / 24 * (1 - tung), adt / 24 * tung
    # handräknat: h_V = a1 + a2·(max(V, Vmin)/Vref)^a3 med v12-värdena
    hv_vag = v / 70.0
    hv_dack = v / 70.0
    hv_broms = 1.0 - 0.75 * v / 120.0
    hv_upp = v / 50.0
    w = {
        I_VAG: (nl * (dubb * 2.88 + (1 - dubb) * 0.072) + nt * 0.36) * hv_vag / 1000,
        I_DACK: (nl * 0.1 + nt * 0.5) * hv_dack / 1000,
        I_BROMS: (nl * 0.01 + nt * 0.05) * hv_broms / 1000,
    }
    fx = {
        I_VAG: [0.0, 1 - 0.28, 0.28 - 0.0224, 0.0224],
        I_DACK: [0.0, 0.9, 0.09, 0.01],
        I_BROMS: [0.0, 0.2, 0.3, 0.5],
    }
    k = (nl * 3.0e-6 + nt * 1.2e-4) / korfalt * hv_upp  # 1/h, f_q = 1 (torrt)
    m_slut = res.massbalans["slut"][0]
    max_rel = 0.0
    for s, ws in w.items():
        for x in range(len(STORLEKAR)):
            m_stj = ws * (1 - 0.75) * fx[s][x] / k
            if m_stj == 0:
                assert m_slut[s, x] == 0
                continue
            max_rel = max(max_rel, _rel(float(m_slut[s, x]), m_stj))
    print(f"jämvikt: k = {k:.5f} 1/h, max rel fel M mot P/k = {max_rel:.3e}")
    assert max_rel < 1e-9
    # Utsläppet i jämvikt = direkt + uppvirvlat = hela PM10-slitaget
    w10 = sum(ws * (fx[s][2] + fx[s][3]) for s, ws in w.items()) / 3600
    assert _rel(float(res.pm10_g_m_s[0, -1] - res.pm10_avgas_g_m_s[0, -1]), w10) < 1e-9


# ---------------------------------------------------------------------------
# Fukt
# ---------------------------------------------------------------------------


def test_vatt_vaglag_undertrycker_uppvirvling() -> None:
    p = tp()
    n = 24 * 20
    torr = berakna_emission([_avsnitt()], _vader(n, 0.0), Trafikprofil.jamn(), p)
    vat = berakna_emission([_avsnitt()], _vader(n, 0.5), Trafikprofil.jamn(), p)
    upp_torr = float(np.sum(torr.pm10_uppvirvling_g_m_s))
    upp_vat = float(np.sum(vat.pm10_uppvirvling_g_m_s))
    print(f"uppvirvling PM10 vått/torrt = {upp_vat / upp_torr:.3e}")
    assert np.all(torr.f_q == 1.0)
    assert np.all(vat.f_q < 1e-10)
    assert upp_vat < 1e-9 * upp_torr
    # Fukten ligger mellan spraygränsen och dräneringsgränsen (spray tar resten)
    assert np.all(vat.fukt_mm > p.spray_min_mm)
    assert np.all(vat.fukt_mm <= p.draneringsgrans_mm)
    # Direkt utsläpp: väg- och däckslitage dämpas helt, bromsen enligt sina egna gränser
    g_min = float(vat.fukt_mm.min())
    fq_broms = float(retentionsfaktor(g_min, p.retention_broms_mm, p.retention_form))
    kvot_dir = float(np.sum(vat.pm10_direkt_g_m_s)) / float(np.sum(torr.pm10_direkt_g_m_s))
    print(f"direkt PM10 vått/torrt = {kvot_dir:.3e} (f_q broms ≤ {fq_broms:.3f})")
    assert kvot_dir < fq_broms
    # Dammet samlas på den våta vägen (inget kommer upp), men dräneras och sprayas delvis
    assert vat.damm_g_m2[0, -1] > torr.damm_g_m2[0, -1]


def test_retention_jamn_overgang() -> None:
    p = tp()
    g = np.linspace(0.0, 0.1, 201)
    for form in ("exponentiell", "linjar"):
        fq = retentionsfaktor(g, p.retention_vag_mm, form)
        assert fq[0] == 1.0
        assert np.all(np.diff(fq) <= 0)
        assert np.all((fq >= 0) & (fq <= 1))
        assert np.max(np.abs(np.diff(fq))) < 0.05  # inga hopp
    with pytest.raises(LuftFel):
        retentionsfaktor(g, p.retention_vag_mm, "steg")


def test_avdunstning_exakt_mot_fina_steg() -> None:
    g0 = np.array([0.5, 0.05, 0.015, 0.0])
    e = np.array([0.3, 0.3, 0.1, 0.2])
    g1 = _avdunsta(g0, e, 0.02, 1.0)
    gf = g0.copy()
    n = 200_000
    for _ in range(n):
        gf = gf - e * np.minimum(1.0, gf / 0.02) / n
    assert np.allclose(g1, gf, rtol=1e-4, atol=1e-9)


# ---------------------------------------------------------------------------
# Åtgärder och dubbdäck
# ---------------------------------------------------------------------------


def _profil_noll_kl3() -> Trafikprofil:
    dygn = [1.0 / 23.0] * 24
    dygn[3] = 0.0
    return Trafikprofil(tuple(dygn), (1.0,) * 7, (1.0,) * 12)


def test_stadning_minskar_massan_med_angiven_andel() -> None:
    p = tp()
    profil = _profil_noll_kl3()  # ingen trafik kl 03: bara städningen verkar den timmen
    atg = [Atgard("saltning", 0, 10.0), Atgard("sandning", 1, 50.0)]
    fore = berakna_emission([_avsnitt()], _vader(3, 0.0), profil, p, atg)
    efter = berakna_emission(
        [_avsnitt()], _vader(4, 0.0), profil, p, [*atg, Atgard("stadning", 3, 1.0)]
    )
    m3 = fore.massbalans["slut"][0]
    m4 = efter.massbalans["slut"][0]
    forvantad = 1.0 - p.effekt_per_kalla("stad")
    kvot = np.divide(m4, m3, out=np.ones_like(m3), where=m3 > 0)
    fel = np.max(np.abs(kvot - forvantad)[m3 > 0])
    print(f"städning: max fel i kvarvarande andel = {fel:.3e}")
    assert fel < 1e-12
    assert np.isclose(kvot[I_SALT, 2], 1.0 - p.stadeffekt_salt)
    assert np.isclose(kvot[I_VAG, 0], 1.0 - p.stadeffekt_damm.over_pm200) or m3[I_VAG, 0] == 0


def test_dubbdacksandel_noll_ger_inget_dubbslitage() -> None:
    p = tp()
    n = 48
    utan = berakna_emission([_avsnitt(dubb=0.0)], _vader(n), Trafikprofil.jamn(), p)
    med = berakna_emission([_avsnitt(dubb=0.5)], _vader(n), Trafikprofil.jamn(), p)
    # Med W0 för ej dubbdäck = 0 återstår bara dubbslitage – som ska vara noll
    p0 = dataclasses.replace(p, vagslitage_ejdubb_latt_g_km=0.0, vagslitage_ejdubb_tung_g_km=0.0)
    noll = berakna_emission([_avsnitt(dubb=0.0)], _vader(n), Trafikprofil.jamn(), p0)
    assert noll.produktion_per_kalla_g_m[0, I_VAG] == 0.0
    assert np.sum(noll.massbalans["slut"][0, I_VAG]) == 0.0
    assert med.produktion_per_kalla_g_m[0, I_VAG] > 10 * utan.produktion_per_kalla_g_m[0, I_VAG]
    # Däck- och bromsslitage påverkas inte av dubbandelen
    for s in (I_DACK, I_BROMS):
        assert med.produktion_per_kalla_g_m[0, s] == utan.produktion_per_kalla_g_m[0, s]


def test_manadsvis_dubbandel() -> None:
    p = tp()
    dubb = tuple(0.5 if m == 0 else 0.0 for m in range(12))  # bara januari
    vader = VaderserieTimme(
        start="2025-01-31T00:00",
        temperatur_c=np.full(48, 2.0),
        nederbord_mm_h=np.zeros(48),
        vindhastighet_m_s=np.full(48, 3.0),
        relativ_fuktighet_procent=np.full(48, 80.0),
        vindhojd_m=10.0,
        temperaturhojd_m=2.0,
        potentiell_avdunstning_mm_h=np.zeros(48),
    )
    res = berakna_emission([_avsnitt(dubb=dubb)], vader, Trafikprofil.jamn(), p)
    direkt = res.pm10_direkt_g_m_s[0]
    assert np.all(direkt[:24] > 5 * direkt[24:])  # 31 jan med dubb, 1 feb utan
    assert np.allclose(direkt[24:], direkt[30])


# ---------------------------------------------------------------------------
# Testvärdesflagga
# ---------------------------------------------------------------------------


def test_testvardesflaggan_fortplantas() -> None:
    p = tp()
    assert p.ar_testvarde
    with pytest.warns(LuftVarning, match="TESTVÄRDEN"):
        res = berakna_emission([_avsnitt()], _vader(24), Trafikprofil.jamn(), p)
    assert res.ar_testvarde
    assert any("TESTVÄRDEN" in t and "nox_latt_g_km" in t for t in res.varningar)
    s = res.sammanfattning()
    assert s["ar_testvarde"] is True

    skarp = dataclasses.replace(p, ar_testvarde=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error", LuftVarning)
        res2 = berakna_emission([_avsnitt()], _vader(24), Trafikprofil.jamn(), skarp)
    assert not res2.ar_testvarde
    assert "ar_testvarde" not in res2.sammanfattning()
    assert not any("TESTVÄRDEN" in t for t in res2.varningar)


def test_parametrar_utan_standardvarden() -> None:
    from luft.emission import EmissionsParametrar

    with pytest.raises(TypeError):
        EmissionsParametrar()  # type: ignore[call-arg]
    with pytest.raises(LuftFel):
        dataclasses.replace(tp(), direktandel_vagslitage=1.5)
    with pytest.raises(LuftFel):
        dataclasses.replace(tp(), uppvirvling_latt_per_fordon=float("nan"))


def test_sammanfattning_andelar() -> None:
    res = berakna_emission([_avsnitt()], _vader(24 * 30), Trafikprofil.jamn(), tp())
    a = res.sammanfattning()["avsnitt"][0]
    total = a["andel_uppvirvling_pm10"] + a["andel_direkt_pm10"] + a["andel_avgas_pm10"]
    assert abs(total - 1.0) < 1e-12
    assert 0 < a["andel_uppvirvling_pm10"] < 1
    print(f"andel uppvirvling PM10 (30 torra dygn, testvärden) = {a['andel_uppvirvling_pm10']:.3f}")


# ---------------------------------------------------------------------------
# Koppling till spridningen
# ---------------------------------------------------------------------------


def test_emission_for_linjekalla() -> None:
    res = berakna_emission(
        [_avsnitt("a"), _avsnitt("b", adt=10_000)],
        _vader(48),
        Trafikprofil.jamn(),
        tp(),
    )
    e_ar = emission_for_linjekalla(res, "pm10")
    e_h = emission_for_linjekalla(res, "nox", timme=5)
    assert e_ar.shape == (2,) and np.allclose(e_ar, res.pm10_g_m_s.mean(axis=1))
    assert np.array_equal(e_h, res.nox_g_m_s[:, 5])
    with pytest.raises(LuftFel):
        emission_for_linjekalla(res, "co2")
    with pytest.raises(LuftFel):
        emission_for_linjekalla(res, "pm10", timme=48)

    grid = voxelisera(
        np.zeros((6, 6)),
        2.0,
        8.0,
        raster_dx_m=2.0,
        raster_origo_x_m=150_000.0,
        raster_origo_y_m=6_580_000.0,
        buffert_uppstroms_m=2.0,
        buffert_nedstroms_m=2.0,
        buffert_sidor_m=2.0,
    )
    linjer = [
        np.array([[150_002.0, 6_580_003.0], [150_010.0, 6_580_003.0]]),
        np.array([[150_002.0, 6_580_009.0], [150_010.0, 6_580_009.0]]),
    ]
    _, rapport = linjekalla_med_rapport(grid, linjer, e_ar)
    assert np.isclose(rapport.total_g_s, 8.0 * e_ar.sum(), rtol=1e-12)


@pytest.mark.parametrize("interpolera", [False, True])
def test_timvis_halt_mot_bruteforce(interpolera: bool) -> None:
    rng = np.random.default_rng(7)
    n_vag, n_tim, rikt = 3, 300, [0.0, 90.0, 180.0, 270.0]
    form = (5, 6)
    u = 0.2 + 8.0 * rng.random(n_tim)  # några timmar svag vind
    theta = 360.0 * rng.random(n_tim)
    giltig = rng.random(n_tim) > 0.03
    vs = Vaderserie.skapa(theta, u, giltig=giltig)
    ford = fordela(vs, rikt, u_ref_m_s=5.0, u_min_m_s=0.5, interpolera=interpolera)
    e = rng.random((n_vag, n_tim)) * 1e-3
    c = rng.random((n_vag, len(rikt), *form))
    grans = 1.5e-4

    res = timvis_halt(e, c, ford, grans_g_m3=grans, block_bytes=8 * 50 * 40)

    summa = np.zeros(form)
    maxv = np.full(form, -np.inf)
    antal = np.zeros(form)
    n_mod = 0
    for h in range(n_tim):
        if not ford.modellerad[h]:
            continue
        n_mod += 1
        falt = np.zeros(form)
        for k in range(2):
            d = ford.sektor_index[h, k]
            for vv in range(n_vag):
                falt += ford.vikt[h, k] * e[vv, h] * c[vv, d]
        falt *= ford.skalfaktor[h]
        summa += falt
        maxv = np.maximum(maxv, falt)
        antal += falt > grans
    medel = summa / n_mod
    rel_medel = float(np.max(np.abs(res.arsmedel_g_m3 - medel) / medel))
    rel_max = float(np.max(np.abs(res.max_g_m3 - maxv) / maxv))
    print(
        f"timvis_halt ({'interp' if interpolera else 'närmast'}): rel medel {rel_medel:.2e}, "
        f"rel max {rel_max:.2e}, n_mod {n_mod}"
    )
    assert res.antal_modellerade_timmar == n_mod
    assert rel_medel < 1e-12
    assert rel_max < 1e-12
    assert res.antal_timmar_over is not None
    assert np.array_equal(res.antal_timmar_over, antal)
    assert 0 < float(antal.mean()) < n_mod


def test_timvis_halt_fel_form() -> None:
    vs = Vaderserie.skapa([0.0, 90.0], [3.0, 3.0])
    ford = fordela(vs, [0.0, 90.0], 5.0)
    with pytest.raises(LuftFel):
        timvis_halt(np.ones((2, 3)), np.ones((2, 2, 4)), ford)
    with pytest.raises(LuftFel):
        timvis_halt(np.ones((2, 2)), np.ones((3, 2, 4)), ford)


def test_resultat_typ() -> None:
    res = berakna_emission([_avsnitt()], _vader(5), Trafikprofil.jamn(), tp())
    assert isinstance(res, EmissionsResultat)
    assert res.massbalans["slut"].shape == (1, len(KALLOR), len(STORLEKAR))
    for namn in ("pm10_g_m_s", "pm25_g_m_s", "nox_g_m_s", "damm_g_m2", "fukt_mm"):
        assert getattr(res, namn).shape == (1, 5)
