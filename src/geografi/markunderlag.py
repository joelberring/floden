# ruff: noqa: E501
"""Markunderlag för ekologi som lager i geografins manifest (K46): NMD och VMI.

Lagren mappas en gång per geografi med en kontextbuffert runt kärnan (`buffert_for_troskel`, 2-5 km) och
delas av alla projekt i geografin; de ligger i `_lager/<sha256>/` som övriga lager och ingår i manifestet
under `lager.nmd` och `lager.vmi`. De är valfria: en geografi utan dem är fortfarande giltig.

Indata är filer som användaren själv hämtat (NMD-GeoTIFF i EPSG:3006 från Naturvårdsverket, CC0;
VMI-polygoner). Floden hämtar dem inte själv.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from data_inlasning.nmd import NMD_KALLA, NmdFel, buffert_for_troskel, klipp_nmd
from data_inlasning.vmi import VMI_ALDER_TEXT, VMI_KALLA, rastrera_vmi
from geografi.modell import GeografiFel, lagra
from kommuner.skapa import skriv_oppenhet_toml


def bygg_markunderlag(
    geo_rot: Path,
    bygge: Path,
    karna_3011: tuple[float, float, float, float],
    *,
    nmd_fil: Path | None,
    vmi_fil: Path | None,
    troskel_m: float,
    villkor_kontrollerade: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Bygger och lagrar NMD- och VMI-lagren. Returnerar (lager, kallor, varningar) att lägga i manifestet.

    `troskel_m` är den längsta spridningströskel (effektiva m) som geografins nätverk ska prövas mot;
    bufferten blir 2-5 km runt kärnan. VMI utan NMD är tillåtet men ger en varning (VMI har bara mening
    ihop med NMD-rutnätet).
    """
    lager: dict[str, Any] = {}
    kallor: dict[str, Any] = {}
    varningar: list[str] = []
    if nmd_fil is None and vmi_fil is None:
        return lager, kallor, varningar
    buffert = buffert_for_troskel(troskel_m)
    for fil, namn in ((nmd_fil, "NMD"), (vmi_fil, "VMI")):
        if fil is not None and not Path(fil).exists():
            raise GeografiFel(f"{namn}-filen {fil} finns inte.")
    if nmd_fil is not None:
        mapp = bygge / "nmd"
        if mapp.exists():
            shutil.rmtree(mapp)
        try:
            klipp_nmd(nmd_fil, karna_3011, buffert, ut_fil=mapp / "nmd.tif")
        except NmdFel as exc:
            raise GeografiFel(f"NMD: {exc}") from exc
        lg = lagra(geo_rot, mapp)
        # NMD är CC0, men härledd data i geografin följer ändå geografins öppenhet (villkoren för övriga lager).
        skriv_oppenhet_toml(
            lg.mapp, oppen=True, licens="CC0", kalla=NMD_KALLA,
            anledning="NMD är CC0 (Naturvårdsverket): öppen.",
        )  # fmt: skip
        lager["nmd"] = {
            "sha256": lg.sha256, "buffert_m": buffert, "cell_m": 10.0,
            "filer": [{"fil": f, "sha256": s} for f, s in lg.filer],
        }  # fmt: skip
        kallor["nmd"] = {"licens": "CC0", "kalla": NMD_KALLA}
    if vmi_fil is not None:
        if nmd_fil is None:
            varningar.append("VMI lades till utan NMD: VMI-masken använder NMD-rutnätet och är bara användbar ihop med det.")
        mapp = bygge / "vmi"
        if mapp.exists():
            shutil.rmtree(mapp)
        try:
            rastrera_vmi(vmi_fil, karna_3011, buffert, ut_fil=mapp / "vmi.tif")
        except NmdFel as exc:
            raise GeografiFel(f"VMI: {exc}") from exc
        lg = lagra(geo_rot, mapp)
        skriv_oppenhet_toml(
            lg.mapp, oppen=villkor_kontrollerade, licens="se källan", kalla=VMI_KALLA,
            anledning="VMI-villkoren är inte kontrollerade av Floden: stängt tills --villkor-kontrollerade."
            if not villkor_kontrollerade else "Villkoren är kontrollerade (--villkor-kontrollerade).",
        )  # fmt: skip
        lager["vmi"] = {
            "sha256": lg.sha256, "buffert_m": buffert, "cell_m": 10.0, "alder": "äldre",
            "filer": [{"fil": f, "sha256": s} for f, s in lg.filer],
        }  # fmt: skip
        kallor["vmi"] = {"kalla": VMI_KALLA, "alder": VMI_ALDER_TEXT}
    return lager, kallor, varningar
