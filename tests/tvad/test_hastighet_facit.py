"""Facit för 2D-lösarens hastighet och CFL-steg (lokal tröghet, Python-kärnan).

Hastigheten räknas som v = |q| / max(h_före, h_efter) (se `docs/BESLUT.md`, 2026-11-23). Den
ändringen hade inget facittest, så här mäts den mot ett handräknat fall.

**Fall: jämn lutande ränna med konstant regn (kinematisk våg, stationärt tillstånd).**
Ett plan med lutning S och Manning-n, regn i mm/h, öppen nedre kant. Vid stationärt tillstånd är
flödet per meterbredd q(x) = i·x (i i m/s, x avståndet från krönet), och Manning ger
q = h^(5/3) · S^(1/2) / n. Alltså

    h(x) = (i · x · n / S^(1/2))^(3/5)    och    v(x) = q / h = h^(2/3) · S^(1/2) / n.

Lösarens `max_hastighet_m_s` (maximum över tid; hastigheten växer monotont mot det stationära
värdet när regnet är konstant) jämförs med v(x) i tre punkter, bort från kanterna.

**Toleranser.** 3 % relativt för x = 25, 50, 100 och 150 m, 6 % för x = 10 m (nära krönet, där
djupet bara är 3–5 mm). Uppmätt avvikelse 2026-11-23, alla tre fall: −0,2 % till −2,1 % för
x ≥ 25 m och −2,9 % till −4,0 % vid x = 10 m; modellen ligger alltid något under facit. Resten
kommer av att lokal tröghet har vattenytans lutning och inte bara bottenlutningen i friktions-
balansen, av att tillståndet nära krönet inte är fullt stationärt efter 3 000 s, och av att
cellhastigheten är ett medelvärde av två kantflöden. Toleransen ger 1,5 till 15 gånger marginal.

**Före och efter hastighetsändringen** (`cbaba1d`; mätt på `6ef22ae` med samma fall): före gav
lösaren vid x = 10 m +167 % (S = 0,01), +2 407 % (S = 0,02, n = 0,05) och −3 % (S = 0,005), vid
x = 25 m +20 %, +490 % och −1,7 %, och vid x = 50 m i andra fallet +235 %: hastighetsspikar där
vattnet är som tunnast. Efter ändringen är avvikelsen högst 4,0 % överallt. Testet underkänner
alltså den gamla beräkningen och godkänner den nya. I det inre av rännan (x ≥ 100 m) var den
gamla lika nära facit (−0,3 % till −0,7 % i första fallet).
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from testsvit.k1_io import GeoTransformInfo
from tvad.lokal_troghet import TvadResultat, kor_2d_lokal_troghet

TOLERANS_REL = 0.03
TOLERANS_KROK_REL = 0.06
LANGD_M = 200
BREDD_CELLER = 5
SLUT_TID_S = 3000.0


def _ranna(
    lutning: float,
    manning_n: float,
    regn_mm_h: float,
    *,
    dx_m: float = 1.0,
    cfl: float = 0.5,
    dt_max_s: float = 50.0,
) -> tuple[NDArray[np.float64], TvadResultat]:
    """Plan som lutar mot öster, markslag 1 med Manning-n överallt, öppen östkant."""
    nx = int(LANGD_M / dx_m)
    x = (np.arange(nx) + 0.5) * dx_m
    z = np.tile((lutning * (LANGD_M - x)).astype(np.float32), (BREDD_CELLER, 1))
    slag = np.full(z.shape, 1, dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=nx, hojd=BREDD_CELLER, x_min=0.0, y_max=BREDD_CELLER * dx_m,
        pixelstorlek_x=dx_m, pixelstorlek_y=dx_m, epsg=3011, dtype="float32",
    )
    res = kor_2d_lokal_troghet(
        z, slag, geo, [0.0, SLUT_TID_S], [regn_mm_h, regn_mm_h], slut_tid_s=SLUT_TID_S,
        manning_tabell={0: manning_n, 1: manning_n}, cfl=cfl, dt_max_s=dt_max_s,
    )
    return x, res


def _manning_facit(x_m: float, lutning: float, n: float, regn_mm_h: float) -> tuple[float, float]:
    i_m_s = regn_mm_h / 1000.0 / 3600.0
    h = (i_m_s * x_m * n / lutning**0.5) ** 0.6
    return h, h ** (2.0 / 3.0) * lutning**0.5 / n


@pytest.mark.parametrize(
    ("lutning", "n", "regn_mm_h"),
    [(0.01, 0.03, 100.0), (0.02, 0.05, 60.0), (0.005, 0.02, 150.0)],
)
def test_hastighet_mot_manning_i_jamn_ranna(lutning: float, n: float, regn_mm_h: float) -> None:
    x, res = _ranna(lutning, n, regn_mm_h)
    assert res.volymfel_rel < 1e-12
    mitt = BREDD_CELLER // 2
    for x_m, tol in ((10, TOLERANS_KROK_REL), (25, TOLERANS_REL), (50, TOLERANS_REL),
                     (100, TOLERANS_REL), (150, TOLERANS_REL)):
        h_facit, v_facit = _manning_facit(float(x[x_m]), lutning, n, regn_mm_h)
        v_modell = float(res.max_hastighet_m_s[mitt, x_m])
        assert v_modell == pytest.approx(v_facit, rel=tol), (x_m, v_modell, v_facit)
        # Djupet är en egen kontroll: hastighetsändringen får inte ha rubbat djupet.
        assert float(res.max_djup_m[mitt, x_m]) == pytest.approx(h_facit, rel=tol)


def test_hastighet_och_flode_hanger_ihop_med_djupet() -> None:
    """Fara (|q|, m²/s) delat med hastighet (m/s) ger ett djup nära maxdjupet i rännans mitt."""
    x, res = _ranna(0.01, 0.03, 100.0)
    mitt = BREDD_CELLER // 2
    i_m_s = 100.0 / 1000.0 / 3600.0
    q_facit = i_m_s * float(x[100])
    assert float(res.fara_m2_s[mitt, 100]) == pytest.approx(q_facit, rel=TOLERANS_REL)
    h_ur_v = float(res.fara_m2_s[mitt, 100]) / float(res.max_hastighet_m_s[mitt, 100])
    assert h_ur_v == pytest.approx(float(res.max_djup_m[mitt, 100]), rel=TOLERANS_REL)


@pytest.mark.parametrize("cfl", [0.5, 0.3])
def test_cfl_steget_haller_u_max_dt_per_dx(cfl: float) -> None:
    """u_max · dt / dx ≤ Courant-talet i varje tidssteg (u_max = största cellhastighet)."""
    _, res = _ranna(0.05, 0.03, 100.0, cfl=cfl)
    # Fallet ska ha strömning, annars säger olikheten ingenting.
    assert float(res.max_hastighet_m_s.max()) > 0.3
    assert res.courant_hast_max > 0.05
    assert res.courant_hast_max <= cfl + 1e-12
    # CFL-steget (inte dt_max_s = 50 s) styr tidsteget: många steg under 3 000 s.
    assert res.antal_tidssteg > SLUT_TID_S / 50.0 * 10


def test_courant_for_hastighet_ar_inte_naget_stort_vid_finare_rutnat() -> None:
    """Samma regel gäller vid dx = 0,5 m (dx i nämnaren, inte en fast längd)."""
    _, res = _ranna(0.05, 0.03, 100.0, dx_m=0.5, cfl=0.5)
    assert 0.05 < res.courant_hast_max <= 0.5 + 1e-12


def test_rust_karnan_ger_samma_hastighet_som_python_i_ranna() -> None:
    """Rust-kärnan (`rust/tvad_karna/src/losare.rs`) har samma hastighetsregel som Python."""
    from tvad.lokal_troghet import TvadFel
    from tvad.rust_backend import hitta_tvad_karna, kor_2d_lokal_troghet_rust

    try:
        hitta_tvad_karna()
    except TvadFel:
        pytest.skip("rust/tvad_karna är inte byggd")
    lutning, n, regn_mm_h = 0.01, 0.03, 100.0
    x, py = _ranna(lutning, n, regn_mm_h)
    nx = len(x)
    z = np.tile((lutning * (LANGD_M - x)).astype(np.float32), (BREDD_CELLER, 1))
    slag = np.full(z.shape, 1, dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=nx, hojd=BREDD_CELLER, x_min=0.0, y_max=float(BREDD_CELLER),
        pixelstorlek_x=1.0, pixelstorlek_y=1.0, epsg=3011, dtype="float32",
    )
    rs = kor_2d_lokal_troghet_rust(
        z, slag, geo, [0.0, SLUT_TID_S], [regn_mm_h, regn_mm_h], slut_tid_s=SLUT_TID_S,
        manning_tabell={0: n, 1: n}, dt_max_s=50.0,
    )
    # Uppmätt 2026-11-23: bitidentiskt (0 m/s skillnad).
    assert float(np.max(np.abs(rs.max_hastighet_m_s - py.max_hastighet_m_s))) < 1e-6
    mitt = BREDD_CELLER // 2
    _, v_facit = _manning_facit(float(x[100]), lutning, n, regn_mm_h)
    assert float(rs.max_hastighet_m_s[mitt, 100]) == pytest.approx(v_facit, rel=TOLERANS_REL)
