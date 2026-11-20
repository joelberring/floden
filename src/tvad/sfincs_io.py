"""Översättare mellan K1/K4/K5/K6 och Deltares SFINCS (extern 2D-referens) (T05).

SFINCS (GPL v3) används som extern full 2D-referens när binären finns installerad.
När binären saknas kastas tydligt `SfincsSaknasFel` utan tysta fel.

Binär rasterordning för `sfincs.dep`, `sfincs.msk` och `sfincs.man` följer SFINCS
32-bitars little-endian-konvention: `m` (väst -> öst) löper snabbast och `n`
(söder -> norr) löper långsammast, vilket i C-ordning motsvarar `arr[::-1, :]`.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo
from tvad.lokal_troghet import (
    TvadFel,
    TvadResultat,
    bygg_manning_raster,
    skriv_tvad_resultat_k6,
    validera_regnserie,
)

SFINCS_MASK_INAKTIV = 0
SFINCS_MASK_AKTIV = 1
SFINCS_MASK_OPPEN_RAND = 2


class SfincsSaknasFel(TvadFel):
    """Kastas när extern SFINCS-binär inte finns installerad på maskinen."""


@dataclass(frozen=True)
class SfincsKonfig:
    """Kanonisk konfiguration för en SFINCS-körning (motsvarar `sfincs.inp`)."""

    mmax: int
    nmax: int
    dx: float
    dy: float
    x0: float
    y0: float
    epsg: int = EPSG_SWEREF99_1800
    tstart_s: float = 0.0
    tstop_s: float = 3600.0
    depfile: str = "sfincs.dep"
    mskfile: str = "sfincs.msk"
    manningfile: str = "sfincs.man"
    precipfile: str = "sfincs.prp"
    dtmaxout_s: float = 60.0
    alpha: float = 0.5
    huthresh: float = 0.01
    modell_mapp: Path | None = None
    inp_fil: Path | None = None


def _till_sfincs_binar_ordning(arr: NDArray[Any]) -> NDArray[Any]:
    """Konverterar ett norr-upp 2D-raster `(nmax, mmax)` till SFINCS söder-till-norr."""
    return np.ascontiguousarray(arr[::-1, :])


def _fran_sfincs_binar_ordning(arr: NDArray[Any]) -> NDArray[Any]:
    """Konverterar ett SFINCS söder-till-norr-raster tillbaka till norr-upp `(hojd, bredd)`."""
    return np.ascontiguousarray(arr[::-1, :])


def las_sfincs_binar_float32(
    fil: str | Path,
    nmax: int,
    mmax: int,
) -> NDArray[np.float32]:
    """Läser en 32-bitars little-endian float-fil från SFINCS till norr-upp `(nmax, mmax)`."""
    kalla = Path(fil)
    raw = kalla.read_bytes()
    forv_bytes = nmax * mmax * 4
    if len(raw) != forv_bytes:
        raise TvadFel(
            f"SFINCS-fil {kalla} har {len(raw)} byte, förväntade {forv_bytes} ({nmax}x{mmax}x4)."
        )
    arr = np.frombuffer(raw, dtype=np.dtype("<f4")).reshape((nmax, mmax))
    return np.asarray(_fran_sfincs_binar_ordning(arr), dtype=np.float32).copy()


def las_sfincs_binar_int32(
    fil: str | Path,
    nmax: int,
    mmax: int,
) -> NDArray[np.int32]:
    """Läser en 32-bitars little-endian int-fil (`sfincs.msk`) till norr-upp `(nmax, mmax)`."""
    kalla = Path(fil)
    raw = kalla.read_bytes()
    forv_bytes = nmax * mmax * 4
    if len(raw) != forv_bytes:
        raise TvadFel(
            f"SFINCS-maskfil {kalla} har {len(raw)} byte, "
            f"förväntade {forv_bytes} ({nmax}x{mmax}x4)."
        )
    arr = np.frombuffer(raw, dtype=np.dtype("<i4")).reshape((nmax, mmax))
    return np.asarray(_fran_sfincs_binar_ordning(arr), dtype=np.int32).copy()


def skriv_sfincs_indata(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    ut_mapp: str | Path,
    *,
    tstop_s: float | None = None,
    manning_karta: NDArray[np.floating[Any]] | None = None,
    manning_tabell: Mapping[int, float] | None = None,
    dtmaxout_s: float = 60.0,
    alpha: float = 0.5,
    huthresh: float = 0.01,
    oppna_rander: bool = True,
    scenario: Mapping[str, Any] | None = None,
) -> SfincsKonfig:
    """Översätter K1 + K4 (+ valfri K5) till komplett SFINCS indatakatalog.

    Skriver:
    - `sfincs.inp`: kanonisk parameterfil med EPSG:3011 och fildirektiv.
    - `sfincs.dep`: 32-bitars little-endian float32 markhöjder (RH 2000).
    - `sfincs.msk`: 32-bitars little-endian int32 cellmask (0=inaktiv/byggnad, 1=aktiv, 2=rand).
    - `sfincs.man`: 32-bitars little-endian float32 Manning-n per cell.
    - `sfincs.prp`: punktregn (`tid_s` och `intensitet_mm_h`).
    """
    if mark_z.ndim != 2 or markslag.ndim != 2 or mark_z.shape != markslag.shape:
        raise TvadFel("mark_z och markslag måste vara 2D-matriser med samma form.")
    nmax, mmax = mark_z.shape
    if nmax != geo.hojd or mmax != geo.bredd:
        raise TvadFel(
            f"GeoTransformInfo ({geo.hojd}x{geo.bredd}) matchar inte raster ({nmax}x{mmax})."
        )
    if not math.isfinite(dtmaxout_s) or dtmaxout_s <= 0.0:
        raise TvadFel(f"dtmaxout_s måste vara > 0, fick {dtmaxout_s}.")
    if not math.isfinite(alpha) or alpha <= 0.0 or alpha > 1.0:
        raise TvadFel(f"alpha måste ligga i (0, 1], fick {alpha}.")
    if not math.isfinite(huthresh) or huthresh <= 0.0:
        raise TvadFel(f"huthresh måste vara > 0, fick {huthresh}.")

    tider, _, t_start, t_slut = validera_regnserie(regn_tid_s, regn_intensitet_mm_h, tstop_s)
    intens_mm_h = [float(v) for v in regn_intensitet_mm_h]

    if manning_karta is not None:
        man_arr = np.asarray(manning_karta, dtype=np.float32)
        if man_arr.shape != mark_z.shape:
            raise TvadFel(
                f"manning_karta {man_arr.shape} matchar inte mark_z {mark_z.shape}."
            )
        if bool(np.any(~np.isfinite(man_arr))) or bool(np.any(man_arr <= 0.0)):
            raise TvadFel("manning_karta innehåller icke-positiva eller icke-ändliga värden.")
    else:
        man_arr = np.asarray(
            bygg_manning_raster(markslag, manning_tabell),
            dtype=np.float32,
        )

    aktiv = (markslag != MARKSLAG_BYGGNAD) & np.isfinite(mark_z)
    msk = np.where(aktiv, np.int32(SFINCS_MASK_AKTIV), np.int32(SFINCS_MASK_INAKTIV))
    if oppna_rander:
        rand = np.zeros((nmax, mmax), dtype=np.bool_)
        rand[0, :] = True
        rand[-1, :] = True
        rand[:, 0] = True
        rand[:, -1] = True
        msk[rand & aktiv] = np.int32(SFINCS_MASK_OPPEN_RAND)

    dep_clean = np.where(np.isfinite(mark_z), mark_z, np.float32(-9999.0))

    mal = Path(ut_mapp)
    mal.mkdir(parents=True, exist_ok=True)

    dep_fil = mal / "sfincs.dep"
    msk_fil = mal / "sfincs.msk"
    man_fil = mal / "sfincs.man"
    prp_fil = mal / "sfincs.prp"
    inp_fil = mal / "sfincs.inp"

    dep_bin = np.asarray(_till_sfincs_binar_ordning(dep_clean), dtype="<f4")
    msk_bin = np.asarray(_till_sfincs_binar_ordning(msk), dtype="<i4")
    man_bin = np.asarray(_till_sfincs_binar_ordning(man_arr), dtype="<f4")

    dep_fil.write_bytes(dep_bin.tobytes(order="C"))
    msk_fil.write_bytes(msk_bin.tobytes(order="C"))
    man_fil.write_bytes(man_bin.tobytes(order="C"))

    prp_rader = [
        f"{t_val:.3f} {i_val:.6f}"
        for t_val, i_val in zip(tider, intens_mm_h, strict=True)
    ]
    prp_fil.write_text("\n".join(prp_rader) + "\n", encoding="utf-8")

    konfig = SfincsKonfig(
        mmax=mmax,
        nmax=nmax,
        dx=float(geo.pixelstorlek_x),
        dy=float(geo.pixelstorlek_y),
        x0=float(geo.x_min),
        y0=float(geo.y_min),
        epsg=int(geo.epsg),
        tstart_s=t_start,
        tstop_s=t_slut,
        depfile=dep_fil.name,
        mskfile=msk_fil.name,
        manningfile=man_fil.name,
        precipfile=prp_fil.name,
        dtmaxout_s=float(dtmaxout_s),
        alpha=float(alpha),
        huthresh=float(huthresh),
        modell_mapp=mal,
        inp_fil=inp_fil,
    )

    inp_rader = [
        f"mmax = {konfig.mmax}",
        f"nmax = {konfig.nmax}",
        f"dx = {konfig.dx:.6f}",
        f"dy = {konfig.dy:.6f}",
        f"x0 = {konfig.x0:.6f}",
        f"y0 = {konfig.y0:.6f}",
        "rotation = 0.0",
        f"epsg = {konfig.epsg}",
        f"tstart = {konfig.tstart_s:.3f}",
        f"tstop = {konfig.tstop_s:.3f}",
        f"depfile = {konfig.depfile}",
        f"mskfile = {konfig.mskfile}",
        f"manningfile = {konfig.manningfile}",
        f"precipfile = {konfig.precipfile}",
        f"dtmaxout = {konfig.dtmaxout_s:.3f}",
        f"alpha = {konfig.alpha:.4f}",
        f"huthresh = {konfig.huthresh:.6f}",
        "inputformat = bin",
        "outputformat = bin",
    ]
    inp_fil.write_text("\n".join(inp_rader) + "\n", encoding="utf-8")

    if scenario is not None:
        kontrakt = scenario.get("kontrakt")
        if kontrakt is not None and str(kontrakt) != "K5/v1":
            raise TvadFel(f"Ogiltigt K5-scenariokontrakt: '{kontrakt}', förväntade 'K5/v1'.")
        (mal / "sfincs_scenario.json").write_text(
            json.dumps(dict(scenario), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return konfig


def kor_sfincs_binar(
    modell_mapp: str | Path,
    *,
    sfincs_bin: str = "sfincs",
    timeout_s: float = 600.0,
) -> Path:
    """Kör extern SFINCS-binär i `modell_mapp`.

    Kastar `SfincsSaknasFel` om binären inte finns på maskinen (inga tysta fel).
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0.0:
        raise TvadFel(f"timeout_s måste vara > 0, fick {timeout_s}.")

    binar_sokvag = shutil.which(sfincs_bin)
    if binar_sokvag is None:
        kandidat = Path(sfincs_bin)
        if kandidat.is_file() and os.access(kandidat, os.X_OK):
            binar_sokvag = str(kandidat)
    if binar_sokvag is None:
        raise SfincsSaknasFel(
            f"Extern SFINCS-binär '{sfincs_bin}' hittades inte på PATH. "
            "Installera SFINCS eller använd den inbyggda 2D-lösaren `kor_2d_lokal_troghet`."
        )

    mapp = Path(modell_mapp)
    inp_fil = mapp / "sfincs.inp"
    if not inp_fil.is_file():
        raise TvadFel(f"Saknar SFINCS parameterfil: {inp_fil}")

    try:
        proc = subprocess.run(
            [binar_sokvag],
            cwd=str(mapp),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TvadFel(f"SFINCS-körning överskred tidsgränsen {timeout_s} s.") from exc
    except OSError as exc:
        raise TvadFel(f"Kunde inte starta SFINCS-binären '{binar_sokvag}': {exc}") from exc

    if proc.returncode != 0:
        stderr_ut = proc.stderr.strip() or proc.stdout.strip()
        raise TvadFel(
            f"SFINCS avslutade med felkod {proc.returncode}: {stderr_ut}"
        )

    return mapp


def _hitta_forsta_fil(mapp: Path, namn_lista: Sequence[str]) -> Path | None:
    for namn in namn_lista:
        kandidat = mapp / namn
        if kandidat.is_file():
            return kandidat
    return None


def _las_sfincs_utdata_raw(
    mapp: Path,
    hojd: int,
    bredd: int,
) -> tuple[dict[str, NDArray[np.float32]], dict[str, float], list[Path]]:
    """Läser råa SFINCS-utdatafält från binärfiler, NPZ eller JSON."""
    falt: dict[str, NDArray[np.float32]] = {}
    meta: dict[str, float] = {}
    kalla_filer: list[Path] = []

    # 1. Binära SFINCS-filer (`zsmax.bin`, `hmax.bin`, `vmax.bin` m.fl.)
    for nyckel, kandidater in (
        ("hmax", ("hmax.bin", "sfincs_hmax.bin")),
        ("zsmax", ("zsmax.bin", "sfincs_zsmax.bin")),
        ("vmax", ("vmax.bin", "sfincs_vmax.bin")),
        ("fara", ("fara.bin", "sfincs_fara.bin")),
        ("tid_0_1m", ("tid_till_0_1m.bin", "t01.bin")),
        ("tid_0_2m", ("tid_till_0_2m.bin", "t02.bin")),
    ):
        fil = _hitta_forsta_fil(mapp, kandidater)
        if fil is not None:
            falt[nyckel] = las_sfincs_binar_float32(fil, hojd, bredd)
            kalla_filer.append(fil)

    if falt:
        return falt, meta, kalla_filer

    # 2. NPZ-resultat (`sfincs_utdata.npz` eller `sfincs_map.npz`)
    npz_fil = _hitta_forsta_fil(mapp, ("sfincs_utdata.npz", "sfincs_map.npz"))
    if npz_fil is not None:
        kalla_filer.append(npz_fil)
        with np.load(npz_fil) as data:
            for nyckel in ("hmax", "zsmax", "vmax", "fara", "slut_djup", "tid_0_1m", "tid_0_2m"):
                if nyckel in data:
                    arr = np.asarray(data[nyckel], dtype=np.float32)
                    if arr.shape != (hojd, bredd):
                        raise TvadFel(
                            f"Fält '{nyckel}' i {npz_fil} har form {arr.shape}, "
                            f"förväntade {(hojd, bredd)}."
                        )
                    falt[nyckel] = arr
            for m_nyckel in ("in_m3", "lagrat_m3", "ut_m3", "kortid_s", "antal_tidssteg"):
                if m_nyckel in data:
                    meta[m_nyckel] = float(np.asarray(data[m_nyckel]).item())
        return falt, meta, kalla_filer

    # 3. JSON-resultat (`sfincs_utdata.json`)
    json_fil = _hitta_forsta_fil(mapp, ("sfincs_utdata.json",))
    if json_fil is not None:
        kalla_filer.append(json_fil)
        raw_obj = json.loads(json_fil.read_text(encoding="utf-8"))
        if not isinstance(raw_obj, dict):
            raise TvadFel(f"Ogiltigt JSON-objekt i {json_fil}.")
        for nyckel in ("hmax", "zsmax", "vmax", "fara", "slut_djup", "tid_0_1m", "tid_0_2m"):
            if nyckel in raw_obj:
                arr = np.asarray(raw_obj[nyckel], dtype=np.float32)
                if arr.shape != (hojd, bredd):
                    raise TvadFel(
                        f"Fält '{nyckel}' i {json_fil} har form {arr.shape}, "
                        f"förväntade {(hojd, bredd)}."
                    )
                falt[nyckel] = arr
        for m_nyckel in ("in_m3", "lagrat_m3", "ut_m3", "kortid_s", "antal_tidssteg"):
            if m_nyckel in raw_obj:
                meta[m_nyckel] = float(raw_obj[m_nyckel])
        return falt, meta, kalla_filer

    raise TvadFel(
        f"Hittade inga SFINCS-utdatafiler (bin/npz/json) i {mapp}."
    )


def las_sfincs_utdata_till_k6(
    modell_mapp: str | Path,
    geo: GeoTransformInfo,
    ut_mapp: str | Path,
    *,
    mark_z: NDArray[np.float32] | None = None,
    markslag: NDArray[np.uint8] | None = None,
    indata: Sequence[FilPost] | None = None,
    parametrar: Mapping[str, Any] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> TvadResultat:
    """Läser tillbaka SFINCS-resultat och exporterar till K6 (`max_djup.tif`, `run.json` m.fl.)."""
    mapp = Path(modell_mapp)
    hojd, bredd = geo.hojd, geo.bredd

    falt, meta, kalla_filer = _las_sfincs_utdata_raw(mapp, hojd, bredd)

    aktiv_mask = np.ones((hojd, bredd), dtype=np.bool_)
    if markslag is not None:
        if markslag.shape != (hojd, bredd):
            raise TvadFel(f"markslag {markslag.shape} matchar inte geo {(hojd, bredd)}.")
        aktiv_mask &= markslag != MARKSLAG_BYGGNAD
    elif (mapp / "sfincs.msk").is_file():
        msk_arr = las_sfincs_binar_int32(mapp / "sfincs.msk", hojd, bredd)
        aktiv_mask &= msk_arr != SFINCS_MASK_INAKTIV

    if "hmax" in falt:
        max_djup = np.maximum(np.float32(0.0), falt["hmax"])
    elif "zsmax" in falt:
        if mark_z is not None:
            if mark_z.shape != (hojd, bredd):
                raise TvadFel(f"mark_z {mark_z.shape} matchar inte geo {(hojd, bredd)}.")
            dep_arr = np.asarray(mark_z, dtype=np.float32)
        elif (mapp / "sfincs.dep").is_file():
            dep_arr = las_sfincs_binar_float32(mapp / "sfincs.dep", hojd, bredd)
        else:
            raise TvadFel(
                "SFINCS-utdata innehåller `zsmax` men saknar både `mark_z` och `sfincs.dep`."
            )
        max_djup = np.maximum(np.float32(0.0), falt["zsmax"] - dep_arr)
    else:
        raise TvadFel("SFINCS-utdata saknar både `hmax` och `zsmax`.")

    max_djup = np.where(aktiv_mask & np.isfinite(max_djup), max_djup, np.float32(0.0))

    max_hast = (
        np.where(
            aktiv_mask & np.isfinite(falt["vmax"]),
            np.maximum(np.float32(0.0), falt["vmax"]),
            np.float32(0.0),
        )
        if "vmax" in falt
        else np.zeros((hojd, bredd), dtype=np.float32)
    )

    fara = (
        np.where(
            aktiv_mask & np.isfinite(falt["fara"]),
            np.maximum(np.float32(0.0), falt["fara"]),
            np.float32(0.0),
        )
        if "fara" in falt
        else np.asarray(max_djup * max_hast, dtype=np.float32)
    )

    slut_djup = (
        np.where(
            aktiv_mask & np.isfinite(falt["slut_djup"]),
            np.maximum(np.float32(0.0), falt["slut_djup"]),
            np.float32(0.0),
        )
        if "slut_djup" in falt
        else max_djup.copy()
    )

    tid_01 = (
        falt["tid_0_1m"].copy()
        if "tid_0_1m" in falt
        else np.full((hojd, bredd), np.nan, dtype=np.float32)
    )
    tid_02 = (
        falt["tid_0_2m"].copy()
        if "tid_0_2m" in falt
        else np.full((hojd, bredd), np.nan, dtype=np.float32)
    )

    cell_area = float(geo.pixelstorlek_x) * float(geo.pixelstorlek_y)
    lagrat_m3 = meta.get("lagrat_m3", float(np.sum(slut_djup, dtype=np.float64) * cell_area))
    in_m3 = meta.get("in_m3", lagrat_m3)
    ut_m3 = meta.get("ut_m3", 0.0)
    volymfel_rel = abs(in_m3 - ut_m3 - lagrat_m3) / in_m3 if in_m3 > 0.0 else 0.0
    aktiv_andel = float(np.count_nonzero(aktiv_mask)) / float(hojd * bredd)

    resultat = TvadResultat(
        max_djup_m=np.asarray(max_djup, dtype=np.float32),
        max_hastighet_m_s=np.asarray(max_hast, dtype=np.float32),
        fara_m2_s=np.asarray(fara, dtype=np.float32),
        tid_till_0_1m_s=np.asarray(tid_01, dtype=np.float32),
        tid_till_0_2m_s=np.asarray(tid_02, dtype=np.float32),
        slut_djup_m=np.asarray(slut_djup, dtype=np.float32),
        in_m3=in_m3,
        lagrat_m3=lagrat_m3,
        ut_m3=ut_m3,
        volymfel_rel=volymfel_rel,
        antal_tidssteg=int(meta.get("antal_tidssteg", 0.0)),
        kortid_s=meta.get("kortid_s", 0.0),
        niva="full2d",
        aktiv_cell_andel=aktiv_andel,
    )

    indata_poster = list(indata) if indata is not None else []
    for k_fil in kalla_filer:
        indata_poster.append(
            FilPost(roll=f"sfincs_{k_fil.stem}", fil=k_fil.name, sha256=sha256_fil(k_fil))
        )

    param_ut: dict[str, Any] = {"kalla": "sfincs"}
    if parametrar is not None:
        param_ut.update(parametrar)

    skriv_tvad_resultat_k6(
        resultat,
        geo,
        ut_mapp,
        indata=indata_poster,
        parametrar=param_ut,
        kodversion=kodversion,
        start=start,
    )
    return resultat
