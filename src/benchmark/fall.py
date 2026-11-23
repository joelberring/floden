"""Benchmarkens fasta fall (`floden benchmark`). Varje fall är en funktion utan argument som
kör fallet en gång och returnerar `{"avvikelse": {...} | None, "extra": {...}}`.

Avvikelsen är fallets eget mått mot facit (analytiskt, jämförelse mot full 2D, oklustrad
beräkning) med den gräns som testsviten använder. `avvikelse = None` betyder att fallet inte har
något facit (bara tid och minne mäts). Importer sker i funktionerna så att en del som inte körs
inte kostar något och så att RSS-måttet bara räknar det fallet behöver.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Fall:
    id: str
    namn: str
    funktion: Callable[[], dict[str, Any]]
    max_upprepningar: int | None = None
    tidsgrans_s: float | None = None
    """Första körningen över gränsen markerar fallet "hoppas över"."""


def _avv(
    varde: float, enhet: str, grans: float | None, beskrivning: str, **matt: float
) -> dict[str, Any]:
    return {
        "varde": float(varde),
        "enhet": enhet,
        "grans": grans,
        "godkand": None if grans is None else bool(varde <= grans),
        "beskrivning": beskrivning,
        "matt": {k: float(v) for k, v in matt.items()},
    }


# ----------------------------------------------------------------------------- skyfall


def _ea(res: Any, grans: float, besk: str) -> dict[str, Any]:
    return {
        "avvikelse": _avv(res.jamforelse_mae_m, "m", grans, besk, iou=res.iou),
        "extra": {
            "svit_godkand": bool(res.godkand),
            "volymfel_rel": float(res.volymfel_rel),
            "max_djup_m": float(res.max_djup_m),
            "kortid_2d_s_enligt_fallet": float(res.kortid_2d_s),
        },
    }


def ea1() -> dict[str, Any]:
    from testsvit.ea_fall import kor_ea_test1

    return _ea(
        kor_ea_test1(pixelstorlek_m=10.0),
        0.015,
        "MAE för vattennivån i bakre sänkan mot analytisk jämviktsnivå (gräns 1,5 cm)",
    )


def ea2() -> dict[str, Any]:
    from testsvit.ea_fall import kor_ea_test2

    return _ea(
        kor_ea_test2(celler_per_sanka=16, pixelstorlek_m=5.0),
        0.05,
        "MAE för maxdjup, hybrid 2D + sänkgraf mot full 2D (gräns 5 cm)",
    )


def ea8a() -> dict[str, Any]:
    from testsvit.ea_fall import kor_ea_test8a

    return _ea(
        kor_ea_test8a(pixelstorlek_m=2.0),
        0.05,
        "MAE för maxdjup, subgrid/hybrid mot full 2D (gräns 5 cm)",
    )


def d1() -> dict[str, Any]:
    from testsvit.hunter import D1_MAX_RMSE_M, kor_hunter

    m = kor_hunter(5.0, 3600.0, n=0.05, u=0.2)
    return {
        "avvikelse": _avv(
            m.rmse_m,
            "m",
            D1_MAX_RMSE_M,
            "RMSE för djupet mot Hunters analytiska lösning (dx 5 m, t 3 600 s)",
            max_fel_m=m.max_fel_m,
        ),
        "extra": {
            "volymfel_rel": float(m.volymfel_rel),
            "front_fel_m": float(m.front_fel_m),
            "antal_tidssteg": int(m.antal_tidssteg),
        },
    }


def vatten2d_referens() -> dict[str, Any]:
    """Serverns f64-schema för det fall webbläsaren kör på GPU (`#/prov/webgpu`)."""
    from benchmark.provfall import vatten_kvarter_facit, vatten_massbalans_rel

    ind, maxd, _slut, vol, _t = vatten_kvarter_facit()
    mb = abs(vatten_massbalans_rel(ind, vol))
    return {
        "avvikelse": _avv(mb, "relativ volym", 1e-6, "volymbalans i schemat (regn in = volym ut)"),
        "extra": {"max_djup_m": float(maxd.max()), "celler": int(ind.nx * ind.ny)},
    }


# ----------------------------------------------------------------------------- dagsljus


def dagsljus_grupp_e() -> dict[str, Any]:
    from testsvit.dagsljus_fall import kor_dagsljus_testfall

    res, _ = kor_dagsljus_testfall()
    per_fall: list[dict[str, Any]] = []
    for r in res:
        andel = r.avvikelse / r.grans if r.grans > 0 else r.avvikelse
        per_fall.append(
            {
                "id": r.id,
                "avvikelse": float(r.avvikelse),
                "grans": float(r.grans),
                "enhet": r.enhet,
                "godkand": bool(r.godkand),
                "andel_av_grans": float(andel),
                "kortid_s": float(r.kortid_s),
            }
        )
    varst = max(per_fall, key=lambda p: p["andel_av_grans"])
    return {
        "avvikelse": _avv(
            float(varst["andel_av_grans"]),
            "andel av gräns",
            1.0,
            f"största avvikelse som andel av sin gräns (fall {varst['id']}); 7 fall E1-E7",
        ),
        "extra": {"fall": per_fall, "alla_godkanda": all(p["godkand"] for p in per_fall)},
    }


def dagsljus_kvarter300() -> dict[str, Any]:
    """Samma beräkning som webbläsarens GPU-prov, på servern (numpy, f64)."""
    from benchmark.provfall import dagsljus_kvarter300_facit

    ind, svf, sol, _t = dagsljus_kvarter300_facit()
    return {
        "avvikelse": None,
        "extra": {
            "celler": int(ind["z_m"].size),
            "riktningar": int(ind["n_riktningar"]),
            "svf_min": float(svf.min()),
            "soltimmar_max_h": float(sol.max()),
        },
    }


def demokvarter_liten() -> dict[str, Any]:
    """`floden demo --liten` i en tillfällig mapp: bygger världen och kör dagsljus och vatten
    genom frågetjänsten med tom cache (inget återanvänds mellan körningarna)."""
    from demovarld.demo import LITEN, kor_demo
    from webb.fragor import FragaTjanst

    with tempfile.TemporaryDirectory(prefix="floden-bench-demo-") as t:
        rot = Path(t)
        tjanst = FragaTjanst(
            rot=REPO_ROT,
            fragemappar=[rot / "fragor"],
            cachemapp=rot / "cache",
            korningsmapp=rot / "korningar",
            demomapp=rot / "varld",
        )
        s = kor_demo(
            tjanst=tjanst,
            varld_dir=rot / "varld",
            fragedir=rot / "fragor",
            lage=LITEN,
            logg=lambda _t: None,
        )
    delar = s["delar"]
    return {
        "avvikelse": None,
        "extra": {
            "varld_s": float(s["varld"]["tid_s"]),
            "dagsljus_s": float(delar["demo_dagsljus"]["tid_s"]),
            "dagsljus_jobb": int(delar["demo_dagsljus"]["jobb"]),
            "vatten_s": float(delar["demo_vatten"]["tid_s"]),
            "lage": "liten (2 m-celler, 64 riktningar)",
        },
    }


# ----------------------------------------------------------------------------- luft


def luft_l4() -> dict[str, Any]:
    """L4: punktkälla i likformigt flöde mot analytisk lösning (som test_transport.py)."""
    from luft.transport import Amne, TransportOperator
    from luft.typer import Inflode, Vindfalt, Voxelgrid

    nx, ny, nz = 48, 28, 16
    dx, u, k = 1.0, 1.0, 1.0
    form = (nx, ny, nz)
    grid = Voxelgrid(dx_m=dx, fast=np.zeros(form, dtype=np.bool_), lad_m2_m3=np.zeros(form))
    uu = np.zeros((3, *form))
    uu[0] = u
    vind = Vindfalt(grid, Inflode(u), uu, np.full(form, k), medelperiod_s=100.0)
    op = TransportOperator(vind, Amne("spar", sc_t=1.0, diffusivitet_m2_s=0.0))
    i0, j0, k0, q = 12, ny // 2, 2, 1.0
    s = np.zeros(form)
    s[i0, j0, k0] = q
    c = op.los(s)
    x, y, z = np.meshgrid(
        (np.arange(nx) - i0) * dx,
        (np.arange(ny) - j0) * dx,
        (np.arange(nz) + 0.5) * dx,
        indexing="ij",
    )
    h, tak = (k0 + 0.5) * dx, nz * dx
    kx = k + u * dx / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        ca = np.zeros_like(x)
        for zs in (h, -h, 2 * tak - h, 2 * tak + h, -2 * tak + h, -2 * tak - h):
            for ys in (0.0, ny * dx, -ny * dx):
                r = np.sqrt(x**2 + (kx / k) * ((y - ys) ** 2 + (z - zs) ** 2))
                ca += q / (4 * np.pi * k * r) * np.exp(-u * (r - x) / (2 * kx))
        ii, jj, kk = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
        avst = np.sqrt((ii - i0) ** 2 + (jj - j0) ** 2 + (kk - k0) ** 2)
        omr = (avst > 3) & (ii >= i0) & (ii < nx - 8) & (jj >= 3) & (jj < ny - 3) & (kk < nz - 3)
        m = omr & (ca >= 1e-3 * ca[omr].max())
        fel = np.abs(c - ca) / ca
    mb = op.massbalans(s, c)
    return {
        "avvikelse": _avv(
            float(fel[m].max()),
            "relativt fel",
            0.10,
            "största relativa fel mot analytisk punktkälla (anisotrop diffusion), gräns 10 %",
            l2_rel=float(np.linalg.norm((c - ca)[m]) / np.linalg.norm(ca[m])),
        ),
        "extra": {"celler": int(c.size), "massbalans_rel_fel": float(mb["rel_fel"])},
    }


def luft_l5() -> dict[str, Any]:
    """L5: adjungerad dualitet, <w, c> = <y, s> (som tests/luft/test_transport.py)."""
    from luft.transport import Amne, TransportOperator
    from luft.typer import Inflode, Vindfalt, Voxelgrid

    form = (24, 12, 10)
    fast = np.zeros(form, dtype=np.bool_)
    fast[8:12, 4:8, 0:4] = True
    grid = Voxelgrid(dx_m=2.0, fast=fast, lad_m2_m3=np.zeros(form))
    inflode = Inflode(2.0, z_ref_m=10.0, z0_m=0.3)
    zc = grid.cellcentrum_z_m()
    u = np.zeros((3, *form))
    u[0] = inflode.profil_m_s(zc)[None, None, :]
    rng = np.random.default_rng(1)
    u[1] = 0.1 * rng.standard_normal(form)
    u[2] = 0.05 * rng.standard_normal(form)
    u[:, fast] = 0.0
    nu_t = 0.2 + 0.05 * zc[None, None, :] + 0.05 * rng.random(form)
    vind = Vindfalt(grid, inflode, u, nu_t, medelperiod_s=600.0)
    op = TransportOperator(
        vind, Amne("NOx", deposition_m_s=0.005, sedimentation_m_s=0.01, nedbrytning_1_s=1e-4)
    )
    rng_s = np.random.default_rng(3)
    s = np.zeros(form)
    luft = np.argwhere(grid.luft)
    for i, j, k in luft[rng_s.choice(len(luft), size=30, replace=False)]:
        s[i, j, k] = rng_s.random() + 0.1
    rng_w = np.random.default_rng(4)
    w = np.zeros(form)
    w[14:18, 3:9, 0:3] = rng_w.random((4, 6, 3))
    c = op.los(s)
    c_r = float((w * c).sum() / w.sum())
    y = op.los_adjungerad(w)
    fel = abs(c_r - float((y * s).sum())) / abs(c_r)
    return {
        "avvikelse": _avv(fel, "relativt fel", 1e-8, "dualitet framåt mot adjungerad (gräns 1e-8)"),
        "extra": {"celler": int(c.size)},
    }


# ----------------------------------------------------------------------------- buller


def buller_helkedja() -> dict[str, Any]:
    """Scen -> banor -> baslinjekärna -> operatorlager på 300 m, som bench/buller/helkedja.py.

    Facit är den oklustrade beräkningen av alla par; avvikelsen är klustringens största fel i dB
    mot den garanterade felgränsen. Emissionen är påhittad och kärnan är baslinjen, så nivåerna
    är inte bullernivåer; fallet mäter kedjans tid och minne och att klustringen håller sitt fel.
    """
    from buller.akustik import BaslinjeKarna
    from buller.akustik.band import a_vagning_db
    from buller.banor.banfinnare import bygg_banuppsattning
    from buller.lagen.korning import kor_buller
    from buller.lagen.profil import las_inbyggd_profil
    from buller.scen.fasad import fasadpunkter
    from buller.scen.kallsegment import kallsegment_fran_vagar
    from buller.scen.syntetisk import syntetisk_scen
    from buller.typer import ANTAL_BAND, Vader

    sida_m, antal_mottagare = 300.0, 20
    scen = syntetisk_scen(sida_m=sida_m, cell_m=1.0, fro=3)
    alla = fasadpunkter(
        scen,
        avstand_fran_fasad_m=2.0,
        punktavstand_m=10.0,
        forsta_vaning_hojd_m=2.0,
        vaningshojd_m=3.0,
        max_vaningar=1,
    )
    mottagare = list(alla.mottagare)[:: max(1, len(alla.mottagare) // antal_mottagare)]
    mottagare = mottagare[:antal_mottagare]
    kallor = kallsegment_fran_vagar(scen.vagar, max_langd_m=10.0, delkallhojder_m=(0.3,))
    vader = (Vader(),)
    karna = BaslinjeKarna()
    banor = bygg_banuppsattning(scen, mottagare, kallor, maxradie_m=sida_m * 2.0)
    parber = banor.parberaknare(karna, vader)
    lw = np.full((len(kallor), ANTAL_BAND), 90.0)
    a = a_vagning_db()
    alla_index = list(range(len(kallor)))
    sann = np.empty(len(mottagare))
    for m in range(len(mottagare)):
        hh = parber(m, alla_index)[:, 0, :]
        with np.errstate(divide="ignore"):
            sann[m] = 10.0 * np.log10(np.sum(10.0 ** ((lw + hh + a) / 10.0)))
    korning = kor_buller(
        mottagare=mottagare,
        kallor=kallor,
        lw_db=lw,
        vaderklasser=vader,
        parberaknare=parber,
        ovre_grans=banor.ovre_grans_par(karna.ovre_grans_db),
        a_vagning_db=a,
        karna=karna,
        profil=las_inbyggd_profil("skiss"),
        reflexordning=1,
        antal_banor=len(banor.banor),
    )
    res = korning.resultat
    fel = sann - res.niva_a_db
    return {
        "avvikelse": _avv(
            float(np.max(np.abs(fel))),
            "dB",
            float(np.max(res.felgrans_db)),
            "klustringens största fel mot oklustrad beräkning, mot garanterad felgräns",
        ),
        "extra": {
            "mottagare": len(mottagare),
            "kallor": len(kallor),
            "par": len(mottagare) * len(kallor),
            "banor": len(banor.banor),
            "markning": str(res.markning),
        },
    }


def buller_tvastrale() -> dict[str, Any]:
    """Nord2000-kärnan, plan hård mark, stilla homogen atmosfär: ΔL_t i 25-315 Hz mot den
    analytiska koherenta tvåstrålesumman (facit som i tests/buller/nord2000/test_delmodeller.py)."""
    from buller.akustik.reflektion import tvastralefaktor
    from buller.nord2000 import Nord2000Karna
    from buller.nord2000.frekvenser import mittfrekvenser_hz
    from buller.typer import Terrangprofil, Vader

    karna = Nord2000Karna()
    f = mittfrekvenser_hz()
    c = 340.3
    sigma = 200.0
    storsta = 0.0
    antal = 0
    for d in np.linspace(40.0, 600.0, 60):
        for hs, hr in ((0.3, 1.5), (0.3, 4.0), (1.0, 1.5)):
            profil = Terrangprofil(
                np.array([0.0, d]), np.zeros(2), np.array([sigma]), np.array([0.0]), hs, hr
            )
            terrang = karna.berakna(profil, Vader()).terrang_db
            for i in range(12):
                facit = 10.0 * np.log10(
                    float(tvastralefaktor(float(f[i]), hs, hr, float(d), sigma, c))
                )
                storsta = max(storsta, abs(float(terrang[i]) - facit))
            antal += 1
    return {
        "avvikelse": _avv(
            storsta,
            "dB",
            0.02,
            "största avvikelse mot analytisk tvåstrålesumma, 25-315 Hz, d 40-600 m (gräns satt "
            "av benchmarken; testsviten använder 0,005 dB vid d = 100 m)",
        ),
        "extra": {"profiler": antal, "band": 12},
    }


# ----------------------------------------------------------------------------- register

FALL: dict[str, tuple[Fall, ...]] = {
    "skyfall": (
        Fall("EA1", "EA Test 1: sänka över tröskel (10 m)", ea1),
        Fall("EA2", "EA Test 2: 4x4 sänkor, hybrid mot full 2D (5 m)", ea2),
        Fall("EA8A", "EA Test 8A: urbant regn, subgrid mot full 2D (2 m)", ea8a),
        Fall("D1", "D1: Hunter, friktionsdominerad (dx 5 m, 3 600 s)", d1),
        Fall(
            "vatten2d-ref-64x64",
            "Vatten 2D-schemat i f64, 64x64 (serversidan av GPU-provet)",
            vatten2d_referens,
        ),
    ),
    "dagsljus": (
        Fall("grupp-E", "Grupp E: dagsljus mot analytiska facit (E1-E7)", dagsljus_grupp_e),
        Fall(
            "kvarter300-svf-soltimmar",
            "SVF och soltimmar, kvarter 300x300 m (serversidan av " "GPU-provet)",
            dagsljus_kvarter300,
        ),
        Fall(
            "demokvarter-liten",
            "Demokvarteret (liten): bygg + dagsljus + vatten, tom cache",
            demokvarter_liten,
            max_upprepningar=3,
        ),
    ),
    "luft": (
        Fall(
            "L4-punktkalla",
            "L4: punktkälla mot analytisk lösning, 48x28x16",
            luft_l4,
            tidsgrans_s=60.0,
        ),
        Fall("L5-dualitet", "L5: adjungerad dualitet, 24x12x10", luft_l5, tidsgrans_s=60.0),
    ),
    "buller": (
        Fall(
            "helkedja-300m",
            "Hela kedjan scen -> operatorlager, 300 m, 20 mottagare",
            buller_helkedja,
            max_upprepningar=3,
            tidsgrans_s=60.0,
        ),
        Fall(
            "nord2000-tvastrale",
            "Nord2000-kärnan: plan mark mot tvåstrålesumma, 180 profiler",
            buller_tvastrale,
            tidsgrans_s=60.0,
        ),
    ),
}

KRAVER_KALLMAPP: dict[str, tuple[tuple[str, str, str], ...]] = {
    "buller": (
        (
            "nord2000-rl18-18",
            "Nord2000-testfallen RL 18-18 (grupp N, 84 fall)",
            "kräver lokal källmapp: testsvit/N_nord2000 ligger inte i det publika repot "
            "(se docs/buller/kallor.md); fallen körs av tests/buller/test_testsvit_n.py",
        ),
    ),
}
