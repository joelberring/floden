"""Exporterar GIS-fritt bifiltrerings- och bifurkationsunderlag för Q2 (Samuel).

Exempel:
    python tools/exportera_q2_underlag.py \\
        --indata testsvit/A9_brus_persistens/indata \\
        --ut utdata/q2_samuel
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np

from sankgraf.bifurkation import (
    STANDARD_K_SIGMA,
    STANDARD_REFERENSREGN_MM,
    detektera_bifurkationer,
    exportera_bifiltrering_json,
    skriv_bifurkationer_gpkg,
    skriv_bifurkationer_json,
)
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import las_geotiff


def exportera_q2_underlag(
    indata_mapp: str | Path,
    ut_mapp: str | Path,
    *,
    k_sigma: float = STANDARD_K_SIGMA,
    referensregn_mm: float = STANDARD_REFERENSREGN_MM,
) -> dict[str, Any]:
    """Bygger sänkgraf från en K1-indatamapp och exporterar Q2-filer.

    Skriver tre filer i `ut_mapp`:
    - `bifiltrering_q2.json` (GIS-fri noder+sadelkanter-fil för k[x, y]-experiment)
    - `bifurkationer.json` (sammanfattning med säkerhetsmarginaler och påverkad volym)
    - `bifurkationer.gpkg` (GeoPackage i SWEREF 99 för visning i QGIS/webb)
    """
    in_rot = Path(indata_mapp)
    ut_rot = Path(ut_mapp)
    ut_rot.mkdir(parents=True, exist_ok=True)

    mark_z, geo = las_geotiff(in_rot / "mark.tif")
    osak_fil = in_rot / "osakerhet.tif"
    if osak_fil.is_file():
        osakerhet_z, _ = las_geotiff(osak_fil)
    else:
        osakerhet_z = np.full_like(mark_z, 0.05, dtype=np.float32)

    markslag_fil = in_rot / "markslag.tif"
    if markslag_fil.is_file():
        markslag_raw, _ = las_geotiff(markslag_fil)
        markslag = markslag_raw.astype(np.uint8)
    else:
        markslag = np.zeros(mark_z.shape, dtype=np.uint8)

    graf = bygg_sankgraf(
        mark_z=mark_z.astype(np.float32),
        geo=geo,
        osakerhet_z=osakerhet_z.astype(np.float32),
        markslag=markslag,
    )
    punkter = detektera_bifurkationer(
        graf,
        osakerhet_m=osakerhet_z.astype(np.float32),
        k_sigma=k_sigma,
        referensregn_mm=referensregn_mm,
    )

    bifiltrering_fil = exportera_bifiltrering_json(
        graf,
        ut_rot / "bifiltrering_q2.json",
        osakerhet_m=osakerhet_z.astype(np.float32),
    )
    bifurkationer_json = skriv_bifurkationer_json(
        punkter,
        ut_rot / "bifurkationer.json",
        k_sigma=k_sigma,
        referensregn_mm=referensregn_mm,
    )
    bifurkationer_gpkg = skriv_bifurkationer_gpkg(
        punkter,
        ut_rot / "bifurkationer.gpkg",
        epsg=int(geo.epsg),
    )

    return {
        "antal_noder": len(graf.noder),
        "antal_bifurkationer_totalt": len(punkter),
        "antal_instabila": sum(1 for p in punkter if p.instabil),
        "bifiltrering_json": str(bifiltrering_fil),
        "bifurkationer_json": str(bifurkationer_json),
        "bifurkationer_gpkg": str(bifurkationer_gpkg),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="exportera_q2_underlag",
        description="Exportera GIS-fritt Q2-bifiltreringsunderlag från K1-indata.",
    )
    ap.add_argument(
        "--indata",
        default="testsvit/A9_brus_persistens/indata",
        help="K1-indatamapp med mark.tif (och ev. osakerhet.tif, markslag.tif).",
    )
    ap.add_argument(
        "--ut",
        default="utdata/q2_samuel",
        help="Utmapp för bifiltrering_q2.json, bifurkationer.json och bifurkationer.gpkg.",
    )
    ap.add_argument("--k-sigma", type=float, default=STANDARD_K_SIGMA)
    ap.add_argument("--referensregn-mm", type=float, default=STANDARD_REFERENSREGN_MM)
    args = ap.parse_args()

    res = exportera_q2_underlag(
        args.indata,
        args.ut,
        k_sigma=args.k_sigma,
        referensregn_mm=args.referensregn_mm,
    )
    print(
        f"Exporterade Q2-underlag: {res['antal_noder']} sänknoder, "
        f"{res['antal_instabila']}/{res['antal_bifurkationer_totalt']} instabila överfall."
    )
    print(f"  - {res['bifiltrering_json']}")
    print(f"  - {res['bifurkationer_json']}")
    print(f"  - {res['bifurkationer_gpkg']}")


if __name__ == "__main__":
    main()
