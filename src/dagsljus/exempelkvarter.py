"""Syntetiska K1 v2-kvarter för demo, exempelfrågor och tester av skuggstudier före/efter.

Allt byggs med repots egna byggare (`markmodell.pipeline.bygg_markmodell_ruta`): platt mark,
byggnader som `ByggnadsKandidat` med angiven höjd (`hojdkalla = kalla_hojd`) eller utan höjd
(`hojdkalla = standard`, en flaggad gissning). K9-scenarier skrivs med `karna.scenario`.
Inget här är data från verkligheten; kvarteret ligger bara på en plats för solläget.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from karna.scenario import Andring, Planscenario, skriv_scenario
from markmodell.byggnadsregister import ByggnadsKandidat, byggnad_id_fran_kalla
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta

KALLA = "exempelkvarter"
KODVERSION = "dagsljus-exempelkvarter/1"
START_TID = "2026-11-21T00:00:00+01:00"  # fast: samma kvarter ger samma recept (sha256)

# Demokvarteret ligger i närheten av Stockholms stadshus; platsen styr bara solläget.
DEMO_E_M = 151_000.0
DEMO_N_M = 6_580_800.0


def rektangel(
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    hojd_m: float | None,
    kalla_id: str,
    *,
    vaningar: int | None = None,
) -> ByggnadsKandidat:
    """Rektangulär byggnad (EPSG:3011). `hojd_m = None` ger standardhöjd (flaggad gissning)."""
    return ByggnadsKandidat(
        polygon=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
        kalla=KALLA,
        kalla_id=kalla_id,
        hojd_m=hojd_m,
        vaningar=vaningar,
    )


def byggnads_id(kalla_id: str) -> str:
    """`byggnad_id` i registret för en byggnad skapad med `rektangel(..., kalla_id)`."""
    return byggnad_id_fran_kalla(KALLA, kalla_id)


def bygg_rutnat(
    ut: Path,
    kandidater: Sequence[ByggnadsKandidat],
    *,
    e_min_m: float,
    n_min_m: float,
    antal: int,
    rutstorlek_m: float,
    upplosning_m: float = 1.0,
    vegetation_hojd_m: Callable[[Ruta], NDArray[np.float32] | None] | None = None,
) -> Path:
    """Bygger `antal` x `antal` K1 v2-rutor (utan överlapp, platt mark på 0 m) i `ut`/`mm_E_N`.

    `vegetation_hojd_m(ruta)` ger valfri vegetation per ruta (på 1 m-rutnätet, form
    `replace(ruta, upplosning_m=1.0).form_rc`). Returnerar `ut`. En befintlig `ut` rensas.
    """
    if ut.exists():
        shutil.rmtree(ut)
    ut.mkdir(parents=True)
    for i in range(antal):
        for j in range(antal):
            bygg_ruta(
                ut,
                e_min_m + i * rutstorlek_m,
                n_min_m + j * rutstorlek_m,
                rutstorlek_m,
                kandidater,
                upplosning_m=upplosning_m,
                vegetation_hojd_m=vegetation_hojd_m,
            )
    return ut


def bygg_ruta(
    ut: Path,
    e_min_m: float,
    n_min_m: float,
    storlek_m: float,
    kandidater: Sequence[ByggnadsKandidat],
    *,
    upplosning_m: float = 1.0,
    vegetation_hojd_m: Callable[[Ruta], NDArray[np.float32] | None] | None = None,
) -> Path:
    """En K1 v2-ruta (platt mark på 0 m, utan överlapp) i `ut`/`mm_E_N`; returnerar katalogen."""
    ruta = Ruta(
        e_min_m=e_min_m,
        n_min_m=n_min_m,
        storlek_m=storlek_m,
        overlapp_m=0.0,
        upplosning_m=upplosning_m,
    )
    veg = None if vegetation_hojd_m is None else vegetation_hojd_m(ruta)
    ut.mkdir(parents=True, exist_ok=True)
    res = bygg_markmodell_ruta(
        ruta,
        ut,
        grund_z=np.zeros(ruta.form_rc, dtype=np.float32),
        byggnadskandidater=tuple(kandidater),
        byggnadshojd_fran_laser=False,
        vegetation_hojd_m=veg,
        vegetation_upplosning_m=1.0,
        kodversion=KODVERSION,
        start_tid=START_TID,
    )
    return Path(res.katalog)


def bas_recept(bas: Path) -> tuple[str, ...]:
    """sha256 av alla rutors `recept.json` (K9 `bas_recept`)."""
    filer = sorted(bas.glob("mm_*/recept.json"))
    if not filer:
        raise FileNotFoundError(f"Inga K1-rutor (mm_*/recept.json) i {bas}")
    return tuple(sha256_fil(f) for f in filer)


def skriv_k9(
    sokvag: Path, bas: Path, scenario_id: str, andringar: Sequence[Andring], beskrivning: str = ""
) -> Path:
    """Skriver ett K9-planscenario på `bas`."""
    return skriv_scenario(
        Planscenario(scenario_id, bas_recept(bas), tuple(andringar), beskrivning=beskrivning),
        sokvag,
    )


def polygon_andring(
    typ: str,
    andring_id: str,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    egenskaper: dict[str, Any],
) -> Andring:
    """K9-ändring med rektangulär geometri (GeoJSON, EPSG:3011, moturs)."""
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return Andring(typ, andring_id, {"type": "Polygon", "coordinates": [ring]}, egenskaper)


# --- Demokvarteret -------------------------------------------------------------------------

DEMO_SIDA_M = 300.0
DEMO_RUTSTORLEK_M = 100.0
LAGT_HUS = "lagt_hus"
DEMO_NYTT_HUS = "nytt_hus"
LAGT_HUS_HOJD_M = 4.0
HOJD_EFTER_M = 21.0


def demo_kandidater() -> tuple[ByggnadsKandidat, ...]:
    """Kvarteret från `dagsljus.cli.kvarter` som byggnadsregister: 2 x 2 slutna kvarter om 120 m
    (fyra längor vardera, 14 m djupa, 18 m höga), ett 45 m torn i det sydvästra gårdsrummet och
    ett lågt hus (4 m) i det nordöstra. Alla höjder är angivna (inga standardhöjder)."""
    ut: list[ByggnadsKandidat] = []
    e0, n0 = DEMO_E_M, DEMO_N_M - DEMO_SIDA_M  # sydvästra hörnet
    for bx in (20.0, 160.0):
        for by in (20.0, 160.0):
            x, y = e0 + bx, n0 + by
            namn = f"kv_{int(bx)}_{int(by)}"
            ut += [
                rektangel(x, x + 120, y, y + 14, 18.0, f"{namn}_s"),
                rektangel(x, x + 120, y + 106, y + 120, 18.0, f"{namn}_n"),
                rektangel(x, x + 14, y + 14, y + 106, 18.0, f"{namn}_v"),
                rektangel(x + 106, x + 120, y + 14, y + 106, 18.0, f"{namn}_o"),
            ]
    ut.append(rektangel(e0 + 60, e0 + 80, n0 + 60, n0 + 80, 45.0, "torn"))
    ut.append(rektangel(e0 + 210, e0 + 225, n0 + 210, n0 + 222, LAGT_HUS_HOJD_M, LAGT_HUS))
    return tuple(ut)


def bygg_demokvarter(ut: Path) -> dict[str, Path]:
    """Bygger demokvarteret (3 x 3 rutor om 100 m à 1 m) och två K9-scenarier i `ut`:

    - `bas/`: K1 v2-rutorna,
    - `k9_hojd.json`: `byggnad_hojd`, det låga huset höjs till 21 m,
    - `k9_nytt_hus.json`: `byggnad_ny`, ett nytt 21 m-hus (14 x 40 m) i det nordöstra gårdsrummet.
    """
    e0, n0 = DEMO_E_M, DEMO_N_M - DEMO_SIDA_M
    bas = bygg_rutnat(
        ut / "bas",
        demo_kandidater(),
        e_min_m=e0,
        n_min_m=n0,
        antal=3,
        rutstorlek_m=DEMO_RUTSTORLEK_M,
    )
    hojd = skriv_k9(
        ut / "k9_hojd.json",
        bas,
        "hojd",
        [
            Andring(
                "byggnad_hojd",
                "hojd_lagt_hus",
                None,
                {"byggnads_id": byggnads_id(LAGT_HUS), "hojd_m": HOJD_EFTER_M},
            )
        ],
        "Det låga huset höjs från 4 m till 21 m.",
    )
    nytt = skriv_k9(
        ut / "k9_nytt_hus.json",
        bas,
        "nytt_hus",
        [
            polygon_andring(
                "byggnad_ny",
                DEMO_NYTT_HUS,
                e0 + 232,
                e0 + 246,
                n0 + 178,
                n0 + 218,
                {"hojd_m": HOJD_EFTER_M},
            )
        ],
        "Nytt 21 m-hus i det nordöstra gårdsrummet.",
    )
    return {"bas": bas, "k9_hojd": hojd, "k9_nytt_hus": nytt}
