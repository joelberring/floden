"""Fasta provfall för webbläsarens WebGPU-prov (`#/prov/webgpu`) och deras facit på servern.

Samma fall används av serverns benchmark (som tidtar facit-beräkningen) och av API:t
`/api/prov/webgpu/fall/<id>`, som ger klienten indata och facit. Facit räknas i f64 med
serverns egna funktioner (`dagsljus.horisont`, referensschemat nedan); GPU:n räknar i f32.

* `dagsljus-kvarter300`: det syntetiska kvarteret (`dagsljus.cli.kvarter`, 300 x 300 m, 1 m),
  K = 64 azimutriktningar, solbana 2026-03-20 i 10 minuters steg. Mått: SVF och soltimmar per cell.
* `vatten2d-lutande-plan`: 64 x 64 celler (2 m) med lutande plan, sänka och en byggnad, regn i
  150 s och 100 s avrinning. Facit är samma schema som `vatten_2d.wgsl` i f64 (`vatten_schema`),
  inte hela lösaren `tvad.lokal_troghet` (som har fler delar: flödesutjämning, dynamiskt
  tidssteg, ränder). Måttet är alltså GPU-genomförandets avvikelse från sitt eget schema.

Allt är skissläge: f32 på klienten är aldrig ett rapporttal (docs/WEBGPU-ARKITEKTUR.md).
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

FORMAT_PROVFALL = "floden-prov-webgpu/v1"
MARKNING = (
    "Skissläge: tal från klientens f32-beräkning får bara användas som skiss. "
    "Facit är serverns f64-beräkning."
)

Falt = NDArray[np.float64]

# ---------------------------------------------------------------------------- kodning


def till_b64(a: NDArray[Any], dtyp: str) -> dict[str, Any]:
    """Matris som little endian-bytes i base64, med form och dtyp (K37 provfall)."""
    arr = np.ascontiguousarray(a, dtype=np.dtype(dtyp).newbyteorder("<"))
    return {
        "dtyp": dtyp,
        "form": list(arr.shape),
        "base64": base64.b64encode(arr.tobytes()).decode("ascii"),
    }


def fran_b64(d: dict[str, Any]) -> NDArray[Any]:
    arr = np.frombuffer(base64.b64decode(d["base64"]), dtype=np.dtype(d["dtyp"]).newbyteorder("<"))
    return arr.reshape(d["form"])


# ---------------------------------------------------------------------------- dagsljus


def dagsljus_facit(
    z_m: Falt,
    cell_m: float,
    rmax_celler: float,
    n_riktningar: int,
    sol_hojd_rad: Falt,
    sol_azimut_rad: Falt,
    steg_h: float,
) -> tuple[Falt, Falt]:
    """SVF och direkta soltimmar per cell i f64 med serverns horisontkärna.

    SVF = medelvärdet av cos^2(h_k) över K lika fördelade riktningar. Soltimmar = antal
    solpositioner över horisonten i solens exakta azimut, gånger `steg_h` (utan interpolation
    mellan riktningar, som i shadern). Horisontvinkeln lagras som f32 av `horisont_riktning_ruta`
    (lutningen räknas i f64); det ger fel runt 1e-7 rad, långt under GPU:ns.
    """
    from dagsljus.horisont import Hojdfalt, horisont_riktning_ruta, riktningar_rad

    falt = Hojdfalt(z_m, cell_m, 0.0, z_m.shape[0] * cell_m)
    avstand_m = rmax_celler * cell_m
    summa = np.zeros(z_m.shape)
    for phi in riktningar_rad(n_riktningar):
        h, _ = horisont_riktning_ruta(falt, float(phi), avstand_m, dtyp=np.float64)
        summa += np.cos(h.astype(np.float64)) ** 2
    svf = summa / n_riktningar
    antal = np.zeros(z_m.shape)
    for hs, az in zip(sol_hojd_rad, sol_azimut_rad, strict=True):
        if hs > 0.0:
            h, _ = horisont_riktning_ruta(falt, float(az), avstand_m, dtyp=np.float64)
            antal += hs > h.astype(np.float64)
    return svf, antal * steg_h


def dagsljus_indata_kvarter300() -> dict[str, Any]:
    from dagsljus.cli import DEMO_E_M, DEMO_N_M, kvarter
    from dagsljus.solposition import dygnsfonster_utc, lat_lon_fran_3011, solposition

    sida, cell, steg_min = 300.0, 1.0, 10.0
    z = kvarter(sida, cell)
    lat, lon = lat_lon_fran_3011(DEMO_E_M + sida / 2, DEMO_N_M - sida / 2)
    hojd, azimut = solposition(dygnsfonster_utc("2026-03-20", lon, steg_min), lat, lon)
    return {
        "z_m": z,
        "cell_m": cell,
        "rmax_celler": 120.0,
        "n_riktningar": 64,
        "steg_h": steg_min / 60.0,
        "sol_hojd_rad": hojd,
        "sol_azimut_rad": azimut,
    }


def dagsljus_kvarter300_facit() -> tuple[dict[str, Any], Falt, Falt, float]:
    """(indata, svf, soltimmar_h, kortid_s) för `dagsljus-kvarter300`."""
    ind = dagsljus_indata_kvarter300()
    t0 = time.perf_counter()
    svf, sol = dagsljus_facit(
        ind["z_m"],
        ind["cell_m"],
        ind["rmax_celler"],
        ind["n_riktningar"],
        ind["sol_hojd_rad"],
        ind["sol_azimut_rad"],
        ind["steg_h"],
    )
    return ind, svf, sol, time.perf_counter() - t0


# ---------------------------------------------------------------------------- vatten

G = 9.81
FROUDE_MAX = 2.0
TREDJEDEL = 0.33333334  # som literalen i vatten_2d.wgsl


@dataclass(frozen=True)
class VattenIndata:
    nx: int
    ny: int
    dx: float
    dy: float
    dt: float
    min_djup: float
    z_bed: Falt
    manning: Falt
    aktiv: NDArray[np.uint8]
    steg: tuple[tuple[int, float], ...]
    """Fasta steg: (antal tidssteg, regn per tidssteg i m)."""


def vatten_indata_lutande_plan() -> VattenIndata:
    nx = ny = 64
    dx = dy = 2.0
    c = np.arange(nx)[None, :] * dx
    r = np.arange(ny)[:, None] * dy
    z = 10.0 - 0.01 * c - 0.004 * r + np.zeros((ny, nx))
    # Skålformad sänka i mitten (0,6 m djup) och en vall tvärs över nedre delen.
    z -= 0.6 * np.exp(-(((c - 70.0) / 18.0) ** 2 + ((r - 60.0) / 18.0) ** 2))
    z[44:46, 20:44] += 0.35
    aktiv = np.ones((ny, nx), dtype=np.uint8)
    aktiv[10:20, 45:50] = 0  # byggnad: ogenomträngliga celler
    manning = np.full((ny, nx), 0.03)
    manning[:, :12] = 0.015  # hårdgjord remsa
    dt = 0.25
    regn_m = 120.0e-3 / 3600.0 * dt  # 120 mm/h som meter per tidssteg
    return VattenIndata(nx, ny, dx, dy, dt, 1e-4, z, manning, aktiv, ((600, regn_m), (400, 0.0)))


def _bates(q_bar: Falt, h: Falt, lutning: Falt, n: Falt, dt: Any, min_djup: Any) -> Falt:
    """`bates_punkt_implicit` i vatten_2d.wgsl, elementvis. Samma ordning på operationerna."""
    ok = h > min_djup
    hs = np.where(ok, h, 1.0)
    h73 = (hs * hs) * np.power(hs, TREDJEDEL)
    q_rhs = q_bar - G * hs * dt * lutning
    c = G * dt * (n * n) / h73
    q = (2.0 * q_rhs) / (1.0 + np.sqrt(1.0 + 4.0 * c * np.abs(q_rhs)))
    q_max = FROUDE_MAX * hs * np.sqrt(G * hs)
    return np.where(ok, np.clip(q, -q_max, q_max), 0.0)


def vatten_schema(
    ind: VattenIndata, dtyp: type[np.floating[Any]] = np.float64
) -> tuple[Falt, Falt, Falt]:
    """Samma tidsteg som `vatten_2d.wgsl` (qx, qy, sedan volym): (max_djup, slutdjup, volym).

    `dtyp` = np.float32 ger en f32-körning på CPU för att se avrundningens storlek; facit är f64.
    """
    f = dtyp
    nx, ny = ind.nx, ind.ny
    area = f(ind.dx * ind.dy)
    dt = f(ind.dt)
    z = ind.z_bed.astype(f)
    n_man = ind.manning.astype(f)
    akt = ind.aktiv.astype(bool)
    vol = np.zeros((ny, nx), dtype=f)
    qx = np.zeros((ny, nx - 1), dtype=f)
    qy = np.zeros((ny - 1, nx), dtype=f)
    maxd = np.zeros((ny, nx), dtype=f)
    akt_x = akt[:, :-1] & akt[:, 1:]
    akt_y = akt[:-1, :] & akt[1:, :]
    dxdt, dydt = f(ind.dx) * dt, f(ind.dy) * dt
    md = f(ind.min_djup)
    for antal, regn in ind.steg:
        regn_f = f(regn)
        for _ in range(antal):
            zs = z + vol / area
            hu = np.maximum(
                f(0.0), np.maximum(zs[:, :-1], zs[:, 1:]) - np.maximum(z[:, :-1], z[:, 1:])
            )
            sx = (zs[:, 1:] - zs[:, :-1]) / f(ind.dx)
            qx = np.where(
                akt_x,
                _bates(qx, hu, sx, f(0.5) * (n_man[:, :-1] + n_man[:, 1:]), dt, md),
                f(0.0),
            ).astype(f)
            hv = np.maximum(
                f(0.0), np.maximum(zs[:-1, :], zs[1:, :]) - np.maximum(z[:-1, :], z[1:, :])
            )
            sy = (zs[1:, :] - zs[:-1, :]) / f(ind.dy)
            qy = np.where(
                akt_y,
                _bates(qy, hv, sy, f(0.5) * (n_man[:-1, :] + n_man[1:, :]), dt, md),
                f(0.0),
            ).astype(f)
            dv = np.zeros((ny, nx), dtype=f)
            dv[:, :-1] -= qx * dydt
            dv[:, 1:] += qx * dydt
            dv[:-1, :] -= qy * dxdt
            dv[1:, :] += qy * dxdt
            ny_vol = np.maximum(f(0.0), vol + area * regn_f + dv)
            vol = np.where(akt, ny_vol, f(0.0)).astype(f)
            maxd = np.where(akt, np.maximum(maxd, vol / area), maxd).astype(f)
    return maxd.astype(np.float64), (vol / area).astype(np.float64), vol.astype(np.float64)


def vatten_massbalans_rel(ind: VattenIndata, vol: Falt) -> float:
    """Relativt volymfel: (volym i cellerna - tillfört regn på aktiva celler) / tillfört regn."""
    tillfort = float(sum(a * r for a, r in ind.steg)) * ind.dx * ind.dy * float(ind.aktiv.sum())
    return (float(vol.sum()) - tillfort) / tillfort if tillfort > 0 else 0.0


def vatten_kvarter_facit() -> tuple[VattenIndata, Falt, Falt, Falt, float]:
    ind = vatten_indata_lutande_plan()
    t0 = time.perf_counter()
    maxd, slut, vol = vatten_schema(ind, np.float64)
    return ind, maxd, slut, vol, time.perf_counter() - t0


# ---------------------------------------------------------------------------- API-svar


def _kodversion() -> str:
    from gemensamt.manifest import hamta_kodversion

    return hamta_kodversion()


FALL_KATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "dagsljus-kvarter300",
        "typ": "dagsljus",
        "namn": "Dagsljus: syntetiskt kvarter 300 x 300 m, SVF och soltimmar",
        "beskrivning": (
            "Kvarteret ur `floden dagsljus demo` (1 m-celler), K = 64 riktningar, solbana "
            "2026-03-20 i 10 minuters steg. Skuggaren dagsljus_horisont.wgsl mot serverns f64."
        ),
    },
    {
        "id": "vatten2d-lutande-plan",
        "typ": "vatten_2d",
        "namn": "Vatten 2D: lutande plan med sänka och byggnad, 64 x 64 celler",
        "beskrivning": (
            "1 000 tidssteg à 0,25 s (regn 120 mm/h i 150 s, sedan avrinning). Skuggaren "
            "vatten_2d.wgsl mot samma schema i f64 (inte hela lösaren)."
        ),
    },
)

_CACHE: dict[str, dict[str, Any]] = {}


def provfall_svar(fall_id: str) -> dict[str, Any] | None:
    """Indata och facit för ett provfall (None om id saknas). Facit cachas i processen."""
    if fall_id in _CACHE:
        return _CACHE[fall_id]
    katalog = {k["id"]: k for k in FALL_KATALOG}
    if fall_id not in katalog:
        return None
    bas = {
        "format": FORMAT_PROVFALL,
        "id": fall_id,
        "typ": katalog[fall_id]["typ"],
        "namn": katalog[fall_id]["namn"],
        "beskrivning": katalog[fall_id]["beskrivning"],
        "kodversion": _kodversion(),
        "markning": MARKNING,
    }
    if fall_id == "dagsljus-kvarter300":
        ind, svf, sol, t = dagsljus_kvarter300_facit()
        ny, nx = ind["z_m"].shape
        svar = {
            **bas,
            "indata": {
                "nx": nx,
                "ny": ny,
                "cell_m": ind["cell_m"],
                "rmax_celler": ind["rmax_celler"],
                "n_riktningar": ind["n_riktningar"],
                "steg_h": ind["steg_h"],
                "z_m": till_b64(ind["z_m"], "float32"),
                "sol_hojd_rad": till_b64(ind["sol_hojd_rad"], "float32"),
                "sol_azimut_rad": till_b64(ind["sol_azimut_rad"], "float32"),
            },
            "facit": {
                "beraknad_med": "server, f64 (dagsljus.horisont.horisont_riktning_ruta)",
                "kortid_s": t,
                "falt": {
                    "svf": {"enhet": "andel", **till_b64(svf, "float32")},
                    "soltimmar_h": {"enhet": "h", **till_b64(sol, "float32")},
                },
                "tolerans": TOLERANS_FORSLAG["dagsljus-kvarter300"],
            },
        }
    else:
        vi, maxd, slut, vol, t = vatten_kvarter_facit()
        svar = {
            **bas,
            "indata": {
                "nx": vi.nx,
                "ny": vi.ny,
                "dx": vi.dx,
                "dy": vi.dy,
                "dt": vi.dt,
                "theta": 0.0,
                "min_djup": vi.min_djup,
                "z_bed": till_b64(vi.z_bed, "float32"),
                "manning": till_b64(vi.manning, "float32"),
                "aktiv": till_b64(vi.aktiv, "uint8"),
                "steg": [{"antal": a, "regn_m": r} for a, r in vi.steg],
            },
            "facit": {
                "beraknad_med": "server, f64 (benchmark.provfall.vatten_schema, samma schema som "
                "vatten_2d.wgsl)",
                "kortid_s": t,
                "massbalans_rel": vatten_massbalans_rel(vi, vol),
                "falt": {
                    "max_djup_m": {"enhet": "m", **till_b64(maxd, "float32")},
                    "slutdjup_m": {"enhet": "m", **till_b64(slut, "float32")},
                },
                "tolerans": TOLERANS_FORSLAG["vatten2d-lutande-plan"],
            },
        }
    _CACHE[fall_id] = svar
    return svar


TOLERANS_FORSLAG: dict[str, dict[str, float]] = {
    "dagsljus-kvarter300": {"svf": 0.01, "soltimmar_h": 0.25},
    "vatten2d-lutande-plan": {"max_djup_m": 0.0005, "slutdjup_m": 0.0005},
}
"""Förslag på toleranser per fält (absolut fel i fältets enhet). Sidan låter användaren ändra dem.
Soltimmar: en solposition som växlar om vid horisontkanten ger 1/6 h (ett steg); 0,25 h tillåter
ett steg men inte två. Se docs/benchmark/README.md för hur de är satta."""


def andel_ok(a: Falt, b: Falt, tolerans: float) -> float:
    return float(np.mean(np.abs(a - b) <= tolerans))
