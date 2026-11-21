"""Rust-kärnan för 2D-lösaren (lokal tröghet) – samma anrop som `kor_2d_lokal_troghet`.

`rust/tvad_karna` kör tidsslingan parallellt (rayon) och ger samma resultat som Python-lösaren
(`tvad.lokal_troghet`), som är facit. Förberedelserna (validering, Manning-raster, öppna ränder,
summor) görs här i Python med samma funktioner som Python-lösaren använder, så att båda får
exakt samma indata. Utbytet sker med `.npy`-filer i en tillfällig katalog.

Stöds: fint rutnät (`subgrid_faktor=1`). Subgrid ger `TvadFel` tills den är portad.

Binären hittas via argumentet `binar`, miljövariabeln `FLODEN_TVAD_KARNA`,
`rust/tvad_karna/target/release/tvad_karna` i repot eller `tvad_karna` på PATH.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import GeoTransformInfo
from tvad.lokal_troghet import (
    TvadFel,
    TvadResultat,
    berakna_rand_utflode_konfig,
    bygg_manning_raster,
    validera_regnserie,
)

_REPO_BINAR = (
    Path(__file__).resolve().parents[2]
    / "rust"
    / "tvad_karna"
    / "target"
    / "release"
    / "tvad_karna"
)


def hitta_tvad_karna(binar: str | Path | None = None) -> Path:
    """Sökvägen till `tvad_karna`, eller `TvadFel` med instruktion om hur den byggs."""
    kandidater: list[str | Path] = []
    if binar is not None:
        kandidater.append(binar)
    miljo = os.environ.get("FLODEN_TVAD_KARNA")
    if miljo:
        kandidater.append(miljo)
    kandidater.append(_REPO_BINAR)
    for kandidat in kandidater:
        p = Path(kandidat)
        if p.is_file() and os.access(p, os.X_OK):
            return p
    pa_path = shutil.which("tvad_karna")
    if pa_path:
        return Path(pa_path)
    raise TvadFel(
        "tvad_karna saknas. Bygg med "
        "`cargo build --release --locked --manifest-path rust/tvad_karna/Cargo.toml` "
        "eller ange FLODEN_TVAD_KARNA."
    )


def kor_2d_lokal_troghet_rust(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    slut_tid_s: float | None = None,
    cfl: float = 0.5,
    min_djup_m: float = 1e-4,
    gles_front: bool = True,
    subgrid_faktor: int = 1,
    oppna_rander: bool = True,
    manning_tabell: Mapping[int, float] | None = None,
    dt_max_s: float = 5.0,
    z_nivaer: int = 24,
    theta: float = 0.9,
    tradar: int | None = None,
    binar: str | Path | None = None,
) -> TvadResultat:
    """Som `tvad.lokal_troghet.kor_2d_lokal_troghet`, men tidsslingan körs i Rust.

    `tradar` = antal trådar (standard: alla kärnor). `z_nivaer` används bara med subgrid och
    finns med för att signaturen ska vara densamma.
    """
    del z_nivaer  # bara för subgrid, som inte stöds än
    t_klocka_0 = time.perf_counter()
    if subgrid_faktor != 1:
        raise TvadFel(
            f"Rust-kärnan stöder bara fint rutnät (subgrid_faktor=1), fick {subgrid_faktor}. "
            "Använd tvad.lokal_troghet.kor_2d_lokal_troghet för subgrid."
        )
    if mark_z.ndim != 2 or markslag.ndim != 2 or mark_z.shape != markslag.shape:
        raise TvadFel("mark_z och markslag måste vara 2D-matriser med samma form.")
    hojd, bredd = mark_z.shape
    if hojd != geo.hojd or bredd != geo.bredd:
        raise TvadFel(
            f"GeoTransformInfo ({geo.hojd}x{geo.bredd}) matchar inte raster ({hojd}x{bredd})."
        )
    if not math.isfinite(cfl) or cfl <= 0.0 or cfl > 1.0:
        raise TvadFel(f"cfl måste ligga i (0, 1], fick {cfl}.")
    if not math.isfinite(min_djup_m) or min_djup_m <= 0.0:
        raise TvadFel(f"min_djup_m måste vara > 0, fick {min_djup_m}.")
    if not math.isfinite(dt_max_s) or dt_max_s <= 0.0:
        raise TvadFel(f"dt_max_s måste vara > 0, fick {dt_max_s}.")
    if not math.isfinite(theta) or theta <= 0.0 or theta > 1.0:
        raise TvadFel(f"theta måste ligga i (0, 1], fick {theta}.")
    if tradar is not None and tradar < 1:
        raise TvadFel(f"tradar måste vara >= 1, fick {tradar}.")

    tider, intens_m_s, t_start, t_slut = validera_regnserie(
        regn_tid_s, regn_intensitet_mm_h, slut_tid_s
    )
    program = hitta_tvad_karna(binar)

    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    z_bed = np.asarray(mark_z, dtype=np.float64)
    aktiv = np.asarray((markslag != MARKSLAG_BYGGNAD) & np.isfinite(z_bed), dtype=np.bool_)
    aktiv_area = np.where(aktiv, dx * dy, 0.0)
    manning = bygg_manning_raster(markslag, manning_tabell)
    total_doman_area = float(hojd * bredd) * float(geo.pixelstorlek_x) * float(
        geo.pixelstorlek_y
    )
    total_aktiv_area = float(np.sum(aktiv_area))
    rand = berakna_rand_utflode_konfig(z_bed, aktiv, dx, dy, oppna_rander=oppna_rander)

    with tempfile.TemporaryDirectory(prefix="floden-tvad-") as tmp:
        ind = Path(tmp) / "in"
        ut = Path(tmp) / "ut"
        ind.mkdir()

        def spara(namn: str, arr: NDArray[np.generic]) -> None:
            np.save(ind / namn, np.ascontiguousarray(arr))

        spara("z_bed.npy", z_bed)
        spara("manning.npy", np.asarray(manning, dtype=np.float64))
        spara("aktiv.npy", aktiv.astype(np.uint8))
        for sida in ("vast", "ost", "norr", "soder"):
            spara(f"rand_{sida}_mask.npy", getattr(rand, f"{sida}_mask").astype(np.uint8))
            lutning = np.asarray(getattr(rand, f"{sida}_lutning"), np.float64)
            bredd_andel = np.asarray(getattr(rand, f"{sida}_bredd_andel"), np.float64)
            spara(f"rand_{sida}_lutning.npy", lutning)
            spara(f"rand_{sida}_bredd.npy", bredd_andel)
        spara("regn_tider.npy", np.asarray(tider, dtype=np.float64))
        spara("regn_intens_m_s.npy", np.asarray(intens_m_s, dtype=np.float64))
        spara(
            "param.npy",
            np.array(
                [dx, dy, cfl, min_djup_m, dt_max_s, theta, t_start, t_slut,
                 total_aktiv_area, total_doman_area, 1.0 if gles_front else 0.0],
                dtype=np.float64,
            ),
        )

        cmd = [str(program), str(ind), str(ut)]
        if tradar is not None:
            cmd.append(str(int(tradar)))
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise TvadFel(f"tvad_karna misslyckades: {res.stderr.strip()}")

        def hamta(namn: str) -> NDArray[np.float32]:
            return np.asarray(np.load(ut / namn), dtype=np.float32)

        max_djup = hamta("max_djup.npy")
        slut_djup = hamta("slut_djup.npy")
        max_hast = hamta("max_hastighet.npy")
        fara = hamta("fara.npy")
        tid_01 = hamta("tid_till_0_1m.npy")
        tid_02 = hamta("tid_till_0_2m.npy")
        volym = np.load(ut / "volym.npy")
        matv = np.load(ut / "matvarden.npy")

    in_m3 = float(matv[0])
    ut_m3 = float(matv[1])
    antal_steg = int(matv[2])
    lagrat_m3 = float(np.sum(volym))
    volymfel_rel = abs(in_m3 - ut_m3 - lagrat_m3) / in_m3 if in_m3 > 0.0 else 0.0
    aktiv_cell_andel = float(matv[3]) / float(antal_steg) if antal_steg > 0 else 0.0
    return TvadResultat(
        max_djup_m=max_djup,
        max_hastighet_m_s=max_hast,
        fara_m2_s=fara,
        tid_till_0_1m_s=tid_01,
        tid_till_0_2m_s=tid_02,
        slut_djup_m=slut_djup,
        in_m3=in_m3,
        lagrat_m3=lagrat_m3,
        ut_m3=ut_m3,
        volymfel_rel=volymfel_rel,
        antal_tidssteg=antal_steg,
        kortid_s=time.perf_counter() - t_klocka_0,
        niva="full2d",
        aktiv_cell_andel=aktiv_cell_andel,
    )
