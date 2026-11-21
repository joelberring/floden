"""LAZ-stöd via `lasclip`, lyft från O-Tid.

`tools/lasclip` är O-Tids eget lilla Rust-program (joelberring/otid, `tools/lasclip`,
commit 1d63297, MIT, © 2026 Joel Berring). Det läser LAS eller LAZ med Rust-paketet `las`
(med `laz`), klipper till en ruta och skriver punkterna i samma punktformat. Utfilens ändelse
avgör komprimeringen: `.las` ger okomprimerad LAS som `las_las_punktmoln` läser direkt, så
Floden behöver ingen egen LAZ-avkodare i Python.

Rutan anges i filens egna koordinater (Lantmäteriets laser: SWEREF 99 TM, EPSG:3006).
Omvandlingen till EPSG:3011 sker vid inläsningen, enligt AGENTS.md.

Binären hittas i ordningen: argumentet `binar`, miljövariabeln `FLODEN_LASCLIP`,
`tools/lasclip/target/release/lasclip` i repot, `lasclip` på PATH. Saknas den blir det
ett tydligt fel.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from data_inlasning.cache import DataInlasningFel
from data_inlasning.lantmateriet import PunktMoln, TransformatorArray, las_las_punktmoln
from gemensamt.koordinater import EPSG_LANTMATERIET

_REPO_BINAR = (
    Path(__file__).resolve().parents[2] / "tools" / "lasclip" / "target" / "release" / "lasclip"
)


def hitta_lasclip(binar: str | Path | None = None) -> Path:
    """Sökvägen till lasclip, eller `DataInlasningFel` med instruktion om hur den byggs."""
    kandidater: list[str | Path] = []
    if binar is not None:
        kandidater.append(binar)
    miljo = os.environ.get("FLODEN_LASCLIP")
    if miljo:
        kandidater.append(miljo)
    kandidater.append(_REPO_BINAR)
    for kandidat in kandidater:
        p = Path(kandidat)
        if p.is_file() and os.access(p, os.X_OK):
            return p
    pa_path = shutil.which("lasclip")
    if pa_path:
        return Path(pa_path)
    raise DataInlasningFel(
        "lasclip saknas (behövs för LAZ). Bygg med "
        "`cargo build --release --locked --manifest-path tools/lasclip/Cargo.toml` "
        "eller ange FLODEN_LASCLIP."
    )


def klipp_laser(
    infil: str | Path,
    utfil: str | Path,
    bbox: tuple[float, float, float, float],
    *,
    binar: str | Path | None = None,
) -> int:
    """Klipper LAS/LAZ till `bbox` (min_x, min_y, max_x, max_y i filens koordinater).

    Returnerar antalet behållna punkter. Noll punkter ger ingen utfil (lasclips beteende).
    """
    min_x, min_y, max_x, max_y = bbox
    if not (min_x < max_x and min_y < max_y):
        raise DataInlasningFel(f"Tom klippruta: {bbox}.")
    in_p = Path(infil)
    if not in_p.is_file():
        raise DataInlasningFel(f"Laserfil saknas: '{in_p}'.")
    ut_p = Path(utfil)
    if ut_p.suffix.lower() not in (".las", ".laz"):
        raise DataInlasningFel(f"Utfilen måste sluta på .las eller .laz: '{ut_p}'.")
    ut_p.parent.mkdir(parents=True, exist_ok=True)
    program = hitta_lasclip(binar)
    kord = [repr(float(v)) for v in (min_x, min_y, max_x, max_y)]
    res = subprocess.run(
        [str(program), str(in_p), str(ut_p), *kord],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise DataInlasningFel(f"lasclip misslyckades för '{in_p}': {res.stderr.strip()}")
    try:
        return int(res.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise DataInlasningFel(f"Oväntat svar från lasclip: {res.stdout!r}") from exc


def las_laser_punktmoln(
    sokvag: str | Path,
    bbox: tuple[float, float, float, float],
    *,
    kall_epsg: int = EPSG_LANTMATERIET,
    transformator: TransformatorArray | None = None,
    binar: str | Path | None = None,
) -> PunktMoln:
    """Läser LAS eller LAZ inom `bbox` (källans koordinater) som punktmoln i EPSG:3011.

    Filen klipps till en tillfällig okomprimerad LAS med lasclip och läses med `las_las_punktmoln`.
    Hamnar inga punkter i rutan blir det ett fel, inte ett tomt moln (inga tysta fel).
    """
    with tempfile.TemporaryDirectory(prefix="floden-lasclip-") as tmp:
        ut = Path(tmp) / "klipp.las"
        antal = klipp_laser(sokvag, ut, bbox, binar=binar)
        if antal == 0 or not ut.is_file():
            raise DataInlasningFel(f"Inga laserpunkter i rutan {bbox} i '{sokvag}'.")
        moln = las_las_punktmoln(ut, kall_epsg=kall_epsg, transformator=transformator)
    return PunktMoln(
        e_m=moln.e_m,
        n_m=moln.n_m,
        z_rh2000=moln.z_rh2000,
        klass=moln.klass,
        epsg=moln.epsg,
        kalla=str(sokvag),
        intensitet=moln.intensitet,
    )
