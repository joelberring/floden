"""Regressionstester med kända svar för fysik-, numerik- och prestandafixar."""

from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from buller.nord2000.stralar_krokta import reflexpunkter_m
from dagsljus.matt import MattFel, SoltimmarAckumulator
from dagsljus.solposition import SolFel, dygnsfonster_utc, lat_lon_fran_3011, solposition
from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from gemensamt.koordinater import EPSG_INTERN
from karna.graf import Berakningsgraf, Cache
from luft.vindkomfort import omrakna_vindhastighet
from mark_forluster.grundvatten import berakna_grundvattentak
from rutt.dijkstra import _interpolera_tidserie
from rutt.kritiska import hitta_kritiska_avsnitt
from rutt.parametrar import FordonParametrar, RuttParametrarK7
from rutt.risk import berakna_kumulativ_sannolikhet
from rutt.vagnat import VagKant, VagNat, VagNod
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo
from tvad.hybrid import kor_hybrid_2d_graf
from tvad.kalltermer import Avtappning, Kalltermer, SanktermTillstand
from tvad.lokal_troghet import kor_2d_lokal_troghet, uppdatera_bates_flode_1d


def _geo(nx: int, ny: int, dx: float = 1.0) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=nx,
        hojd=ny,
        x_min=150000.0,
        y_max=6580000.0 + ny * dx,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=EPSG_INTERN,
        dtype="float32",
    )


def _enkel_sanka_graf(djup_m: float = 0.5) -> tuple[object, int]:
    """Bygger en 6x6-terräng med en 2x2-sänka i mitten och utlopp över randen."""
    z = np.full((6, 6), 10.0, dtype=np.float32)
    z[2:4, 2:4] = np.float32(10.0 - djup_m)
    osak = np.zeros((6, 6), dtype=np.float32)
    markslag = np.ones((6, 6), dtype=np.uint8)
    graf = bygg_sankgraf(z, osak, markslag, _geo(6, 6, 1.0))
    nod_id = next(iter(graf.noder))
    return graf, nod_id


def test_handelsestyrd_rotnod_avtappning_raknas_in() -> None:
    """En full rotnod med avtappning ska redovisa avtappat_m3 exakt i volymbalansen."""
    graf, nod_id = _enkel_sanka_graf(djup_m=0.1)  # V_max = 4 * 0.1 = 0.4 m³
    # Regn 100 mm/h i 3600 s -> fyller snabbt sänkan, sedan full rotnod med avtappning 20 mm/h
    res = kor_dynamik_handelsestyrd(
        graf,  # type: ignore[arg-type]
        [0.0, 3600.0],
        [100.0, 100.0],
        avtappning_mm_h=20.0,
    )
    nod = graf.noder[nod_id]  # type: ignore[attr-defined]
    # Hela nodens avrinningsarea tappas med 20 mm/h under hela timmen (r = 100 > 20 mm/h)
    forvantat_avtappat = nod.avrinningsyta_m2 * (20.0 / 1000.0)
    assert math.isclose(res.avtappat_m3, forvantat_avtappat, rel_tol=1e-9)
    bal = res.volymbalans()
    assert abs(float(bal["volymfel_rel"])) < 1e-11
    assert float(bal["avtappat_m3"]) == res.avtappat_m3


def test_handelsestyrd_stigande_och_fallande_regn_exakt() -> None:
    """Stigande regn på torr nod bär inte torr-avtappning; fallande regn fångar inre topp."""
    graf, nod_id = _enkel_sanka_graf(djup_m=2.0)  # stor sänka (8 m³), blir inte full
    nod = graf.noder[nod_id]  # type: ignore[attr-defined]
    area = nod.avrinningsyta_m2
    # Regn: 0 -> 120 mm/h under 0..600 s, sedan 120 -> 0 mm/h under 600..1200 s
    # Avtappning: 60 mm/h. Nettotillflöde > 0 enbart för t in [300, 900] s!
    # Analytisk toppvolym vid t = 900 s: integralen av (r(t) - 60 mm/h) över [300, 900] s
    # Triangel med bas 600 s och höjd 60 mm/h -> 0.5 * 600 * (60 / 3.6e6) * area = 0.005 * area m³
    forvantad_topp_m3 = 0.005 * area

    res = kor_dynamik_handelsestyrd(
        graf,  # type: ignore[arg-type]
        [0.0, 600.0, 1200.0],
        [0.0, 120.0, 0.0],
        avtappning_mm_h=60.0,
    )
    assert math.isclose(max(res.noder[nod_id].volym_m3), forvantad_topp_m3, rel_tol=1e-9)
    assert res.volymfel_rel < 1e-12


def test_handelsestyrd_uppstroms_brunn_suger_inte_nedstroms() -> None:
    """En full uppströmsnod med brunn får aldrig suga lagrat vatten ur en nedströmsnod."""
    # Två sänkor i serie: vänster (botten 11.0, sadel 11.1) -> höger (botten 10.0, sadel 10.8)
    z = np.full((6, 10), 12.0, dtype=np.float32)
    z[2:4, 2:4] = 11.0  # vänster sänka (4 m², överfall vid 11.1 till höger)
    z[2:4, 4] = 11.1  # sadel mellan vänster och höger
    z[2:4, 5:7] = 10.0  # höger sänka (4 m², överfall vid 10.8 mot randen)
    z[2:4, 7:10] = 10.8  # utlopp till östra randen
    osak = np.zeros((6, 10), dtype=np.float32)
    markslag = np.ones((6, 10), dtype=np.uint8)
    geo = _geo(10, 6, 1.0)
    graf = bygg_sankgraf(z, osak, markslag, geo)

    lov = sorted((n for n in graf.noder.values() if n.ar_lov), key=lambda n: n.botten_rh2000)
    assert len(lov) >= 2
    nedstroms = lov[0]

    # Placera en kraftig avtappning (200 mm/h) ENDAST i uppströmsnoden (celler [2:4, 2:4])
    avt_raster = np.zeros((6, 10), dtype=np.float64)
    avt_raster[2:4, 2:4] = 200.0

    # Kraftigt regn (0..300 s) som fyller uppströms, sedan fallande regn till 0 (300..900 s)
    res_med_brunn = kor_dynamik_handelsestyrd(
        graf, [0.0, 300.0, 900.0], [360.0, 360.0, 0.0], avtappning_mm_h=avt_raster
    )
    # Nedströmsnoden har ingen egen brunn, så dess slutvolym får inte minska under toppvolymen
    assert res_med_brunn.volymfel_rel < 1e-12
    ned_vol = res_med_brunn.noder[nedstroms.nod_id].volym_m3
    assert ned_vol[-1] >= max(ned_vol) - 1e-9


def test_2d_inga_hastighetsspikar_vid_urtappning() -> None:
    """Snabb urtappning på lutande plan får inte ge ofysikaliska hastighetsspikar."""
    ny, nx = 10, 20
    x = np.arange(nx, dtype=np.float32)
    z = np.broadcast_to(20.0 - 0.1 * x, (ny, nx)).copy()
    markslag = np.ones((ny, nx), dtype=np.uint8)
    res = kor_2d_lokal_troghet(
        z,
        markslag,
        _geo(nx, ny, 1.0),
        [0.0, 30.0, 31.0, 120.0],
        [180.0, 180.0, 0.0, 0.0],
        slut_tid_s=120.0,
    )
    # Med 10 % lutning och tunt skiktflöde ska max_hastighet ligga väl under 5 m/s
    assert float(np.nanmax(res.max_hastighet_m_s)) < 5.0
    assert res.volymfel_rel < 1e-9


def test_hybrid_nod_till_nod_utan_overskjutning() -> None:
    """Två angränsande djupa grafnoder med flera delade kanter utjämnas stabilt."""
    ny, nx = 14, 24
    z = np.full((ny, nx), 12.0, dtype=np.float32)
    # Två angränsande 8x8-bassänger åtskilda av en låg tröskel på 11.4 m
    z[3:11, 3:11] = 10.0
    z[3:11, 11:19] = 10.5
    z[3:11, 11] = 11.4
    markslag = np.ones((ny, nx), dtype=np.uint8)
    res = kor_hybrid_2d_graf(
        z,
        markslag,
        _geo(nx, ny, 1.0),
        [0.0, 180.0],
        [150.0, 150.0],
        slut_tid_s=240.0,
        nod_persistens_grans_m=0.15,
    )
    assert res.volymfel_rel < 1e-9
    assert float(np.nanmax(res.max_hastighet_m_s)) < 10.0


def test_rutt_interpolera_gles_ordbok_fran_noll() -> None:
    """Saknad kant vid t0 i gles djupordbok ska interpoleras från 0.0, inte hoppa till v1."""
    v = _interpolera_tidserie(
        [0.0, 100.0],
        {0.0: {}, 100.0: {"k1": 0.5}},
        "k1",
        50.0,
        saknat_varde=0.0,
    )
    assert v is not None
    assert math.isclose(v, 0.25, rel_tol=1e-12)


def test_rutt_kritiska_avsnitt_snabb_och_exakt() -> None:
    """Verifierar både riktad/oriktad korrekthet och snabbhet på ett 20x20-vägnät."""
    nx, ny = 20, 20
    noder = [
        VagNod(f"n_{r}_{c}", 150000.0 + c * 50.0, 6580000.0 + r * 50.0, 10.0)
        for r in range(ny)
        for c in range(nx)
    ]
    kanter: list[VagKant] = []
    kant_djup: dict[str, float] = {}
    for r in range(ny):
        for c in range(nx):
            if c + 1 < nx:
                kid = f"h_{r}_{c}"
                kanter.append(VagKant(kid, f"n_{r}_{c}", f"n_{r}_{c + 1}", 50.0, 50.0))
                # En översvämmad nord-sydlig korridor vid kolumn 9
                if c == 9:
                    kant_djup[kid] = 0.60
                else:
                    kant_djup[kid] = 0.05  # våt men farbar
            if r + 1 < ny:
                kid = f"v_{r}_{c}"
                kanter.append(VagKant(kid, f"n_{r}_{c}", f"n_{r + 1}_{c}", 50.0, 50.0))
                kant_djup[kid] = 0.05

    vagnat = VagNat.fran_komponenter(noder, kanter)
    fordon_def = FordonParametrar(
        gransdjup_m=0.30,
        hastighet_per_djup=((0.0, 1.0), (0.30, 0.0)),
    )
    params = RuttParametrarK7(
        fordon={
            "personbil": fordon_def,
            "ambulans": fordon_def,
            "tung_raddningsbil": fordon_def,
        },
        entre_gransdjup_m=0.10,
        gangavstand_max_m=100.0,
        gangvag_djup_max_m=0.20,
        gangvag_fara_max_m2_s=0.50,
        insatstidsmal_min={"sjukhus": 10.0, "vardboende": 10.0, "skola": 10.0, "forskola": 10.0},
        ar_testvarde=True,
    )
    stationer = {"S1": "n_0_0"}
    adresser = {f"A_{r}_{c}": f"n_{r}_{c}" for r in range(ny) for c in range(10, nx)}

    t0 = time.perf_counter()
    valda = hitta_kritiska_avsnitt(
        vagnat,
        stationer,
        adresser,
        0.0,
        kant_djup,
        params,
        "tung_raddningsbil",
        max_avsnitt=3,
        skarp=False,
        tillat_testvarden=True,
    )
    dt = time.perf_counter() - t0
    assert len(valda) == 1
    assert valda[0].vinst == 200  # hela östra halvan (20x10) blir nåbar av 1 öppnad kant
    assert dt < 0.2


def test_karna_katalognyckel_ignorerar_ds_store() -> None:
    """macOS .DS_Store får inte ogiltigförklara katalogens cache-nyckel."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "mark.tif").write_bytes(b"data")
        graf = Berakningsgraf(Cache(p / "cache"))
        k1 = graf._katalognyckel(p)
        (p / ".DS_Store").write_bytes(b"junk")
        k2 = graf._katalognyckel(p)
        assert k1 == k2


def test_subgrid_konveyans_smal_grand_exakt_manning() -> None:
    """Smal gränd (alpha=0.25, h_vat=0.4 m) ska ge exakt Manning-flöde vid stationärt tillstånd."""
    alpha = 0.25
    h_vat = 0.4
    h_eff = alpha * h_vat
    manning_n = 0.03
    lutning = -0.01  # faller åt +x -> positivt flöde
    q_facit = alpha * (1.0 / manning_n) * (h_vat ** (5.0 / 3.0)) * math.sqrt(abs(lutning))

    q = np.array([0.0], dtype=np.float64)
    for _ in range(400):
        q = uppdatera_bates_flode_1d(
            q,
            np.array([h_eff]),
            np.array([lutning]),
            np.array([manning_n]),
            0.5,
            1e-4,
            bredd_andel=np.array([alpha]),
        )
    assert math.isclose(float(q[0]), q_facit, rel_tol=1e-4)


def test_grundvattentak_dranerbar_porositet() -> None:
    """Grundvattentak med fukthalt ska använda dränerbar porositet (porositet - fukthalt)."""
    tak_torr = berakna_grundvattentak(1.0, 0.45)
    tak_vat_lera = berakna_grundvattentak(1.0, 0.45, fukthalt=0.42)
    assert math.isclose(float(tak_torr), 0.45, rel_tol=1e-6)
    assert math.isclose(float(tak_vat_lera), 0.03, rel_tol=1e-5)


def test_solposition_lon_validering_och_cache() -> None:
    """dygnsfonster_utc validerar lon_grader och lat_lon_fran_3011 ger exakt resultat."""
    lat, lon = lat_lon_fran_3011(150000.0, 6580000.0)
    assert 59.0 < lat < 60.0
    assert math.isclose(lon, 18.0, abs_tol=1e-5)
    with pytest.raises(SolFel):
        dygnsfonster_utc("2026-03-20", 999.0, 10.0)


def test_2d_brytlinje_stoppar_flode() -> None:
    """En 0,30 m brytlinje (kantsten/mur) tvärs ett lutande plan ska dämma upp vatten i 2D."""
    ny, nx = 6, 12
    x = np.arange(nx, dtype=np.float32)
    z = np.broadcast_to(12.0 - 0.02 * x, (ny, nx)).copy()
    markslag = np.ones((ny, nx), dtype=np.uint8)
    geo = _geo(nx, ny, 1.0)
    # Nord-sydlig mur mellan kolumn 5 och 6
    x_mur = geo.x_min + 6.0
    mur = BrytlinjePost(
        typ="mur",
        hojd_m=0.30,
        koordinater=((x_mur, geo.y_max), (x_mur, geo.y_max - ny)),
    )
    res_utan = kor_2d_lokal_troghet(
        z, markslag, geo, [0.0, 60.0], [120.0, 120.0], slut_tid_s=120.0
    )
    res_med = kor_2d_lokal_troghet(
        z, markslag, geo, [0.0, 60.0], [120.0, 120.0], slut_tid_s=120.0, brytlinjer=[mur]
    )
    # 120 mm/h i 60 s = 2 mm regn; med muren däms uppströms (kolumn 5) upp >3x djupare
    d_med = float(np.mean(res_med.max_djup_m[:, 5]))
    d_utan = float(np.mean(res_utan.max_djup_m[:, 5]))
    assert d_med > 3.0 * d_utan
    assert float(np.mean(res_med.slut_djup_m[:, 5])) > 0.005
    assert res_med.volymfel_rel < 1e-9


def test_subgrid_kalltermer_skalar_med_vat_area() -> None:
    """Sänkor i delvis våt subgrid-cell ska bara avtappa över den våta delarean."""
    kap = np.array([[36.0]], dtype=np.float64)  # 36 mm/h = 1e-5 m/s
    sank = SanktermTillstand.bygg(
        Kalltermer(avtappning=Avtappning(kap)),
        (1, 1),
    )
    volym = np.array([[1.0]], dtype=np.float64)
    area = np.array([[16.0]], dtype=np.float64)
    vat_area = np.array([[4.0]], dtype=np.float64)  # 25 % våt
    sank.tillampa(volym, area, dt=100.0, vat_area=vat_area)
    # 1e-5 m/s * 100 s * 4.0 m² = 0.004 m³ (inte 0.016 m³)
    assert math.isclose(sank.avtappat_m3, 0.004, rel_tol=1e-12)
    assert math.isclose(float(volym[0, 0]), 0.996, rel_tol=1e-12)


def test_rutt_risk_expm1_utan_kancellation() -> None:
    """berakna_kumulativ_sannolikhet ska undvika kancellation för små p."""
    # 1 - (1 - 1e-12)^100 ≈ 1e-10
    kum = berakna_kumulativ_sannolikhet(1e-12, 100.0)
    assert math.isclose(kum, 1e-10, rel_tol=1e-9)


def test_soltimmar_aktiva_riktningar_exakt_och_hoppar_over_norr() -> None:
    """SoltimmarAckumulator hoppar över inaktiva norr-azimuter vid vårdagjämning."""
    tider = dygnsfonster_utc("2026-03-20", 18.06, 10.0)
    hojd, az = solposition(tider, 59.33, 18.06)
    k = 72
    form = (4, 4)
    rng = np.random.default_rng(42)
    h_alla = rng.uniform(0.0, 0.4, size=(k, *form))

    ack_alla = SoltimmarAckumulator(k, hojd, az, 10.0 / 60.0, form)
    ack_spar = SoltimmarAckumulator(k, hojd, az, 10.0 / 60.0, form)
    aktiva = ack_spar.aktiva_riktningar()
    assert len(aktiva) < k * 0.6  # ca hälften vid vårdagjämning
    assert 0 not in aktiva  # rakt norrut har ingen sol i mars

    for i in range(k):
        ack_alla.mata(i, h_alla[i])
        ack_spar.mata(i, h_alla[i] if i in aktiva else None)

    assert np.array_equal(ack_alla.timmar(), ack_spar.timmar())

    # Aktiv riktning med None ska ge ett tydligt MattFel
    ack_fel = SoltimmarAckumulator(k, hojd, az, 10.0 / 60.0, form)
    forsta_aktiva = min(aktiva)
    for i in range(forsta_aktiva):
        ack_fel.mata(i, None)
    with pytest.raises(MattFel):
        ack_fel.mata(forsta_aktiva, None)


def test_vindkomfort_log_lag_enhetlig() -> None:
    """omrakna_vindhastighet ska använda ln((z + z0) / z0) enhetligt med Inflode."""
    z0 = 0.5
    u = omrakna_vindhastighet(
        np.array([5.0]),
        z_fran_m=10.0,
        z0_fran_m=z0,
        z_till_m=2.0,
        z0_till_m=z0,
        blandningshojd_m=60.0,
    )
    forvantat = 5.0 * math.log((2.0 + z0) / z0) / math.log((10.0 + z0) / z0)
    assert math.isclose(float(u[0]), forvantat, rel_tol=1e-12)


def test_punkt_implicit_friktion_exakt_fran_vila() -> None:
    """Punkt-implicit kvadratisk friktion dämpar direkt från vila och löser q + c|q|q = q_rhs."""
    h = np.array([0.05], dtype=np.float64)
    s = np.array([-0.10], dtype=np.float64)  # brant lutning 10 %
    n = np.array([0.04], dtype=np.float64)
    dt = 0.5
    q_ny = uppdatera_bates_flode_1d(np.array([0.0]), h, s, n, dt, 1e-4)
    q = float(q_ny[0])
    q_rhs = -9.81 * 0.05 * dt * (-0.10)
    c = 9.81 * dt * (0.04**2) / (0.05 ** (7.0 / 3.0))
    # Uppfyller exakt q + c|q|q = q_rhs (och är strikt mindre än friktionsfria q_rhs)
    assert 0.0 < q < q_rhs
    assert math.isclose(q + c * abs(q) * q, q_rhs, rel_tol=1e-12)


def test_nord2000_reflexpunkter_newton_exakt_kubisk_rot() -> None:
    """reflexpunkter_m löser ekv. (49) till maskinprecision för både med- och motvind."""
    for d, hs, hr, xi in (
        (100.0, 0.3, 1.5, 1.0 / 2000.0),   # medvind (1 rot)
        (800.0, 1.0, 1.5, 1.0 / 500.0),    # stark medvind (flera rötter)
        (100.0, 1.0, 2.0, -1.0 / 3000.0),  # motvind (1 rot)
    ):
        rotter = reflexpunkter_m(d, hs, hr, xi)
        assert len(rotter) >= 1
        bs = hs * (2.0 + xi * hs)
        br = hr * (2.0 + xi * hr)
        p = bs + br - 0.5 * xi * d * d
        q = 0.5 * d * (br - bs)
        for x in rotter:
            assert 0.0 <= x <= d
            u = x - 0.5 * d
            rest = (2.0 * xi * u * u + p) * u + q
            skala = max(1.0, abs(p * u), abs(q))
            assert abs(rest) <= 1e-12 * skala




