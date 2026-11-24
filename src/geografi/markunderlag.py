# ruff: noqa: E501
"""Markunderlag för ekologi som lager i geografins manifest (K46): NMD, VMI och inventerade träd.

Lagren mappas en gång per geografi med en kontextbuffert runt kärnan (`buffert_for_troskel`, 2-5 km) och
delas av alla projekt i geografin; de ligger i `_lager/<sha256>/` som övriga lager och ingår i manifestet
under `lager.nmd`, `lager.vmi` och `lager.tradpunkter`. De är valfria: en geografi utan dem är fortfarande giltig.

Indata är filer som användaren själv hämtat (NMD-GeoTIFF i EPSG:3006 från Naturvårdsverket, CC0;
VMI-polygoner; trädpunkter som GeoPackage, Shapefile eller GeoJSON). Floden hämtar dem inte själv.
Trädlagret sparas som `tradpunkter.csv` med bara punkt, trädslag och stamomfång/diameter (dataminimering) och
är stängt för MCP tills villkoren kontrollerats (`--villkor-kontrollerade`), som VMI.
"""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_inlasning.nmd import (
    NMD_KALLA,
    NmdFel,
    NmdLager,
    buffert_for_troskel,
    klipp_nmd,
    las_nmd,
)
from data_inlasning.vmi import VMI_ALDER_TEXT, VMI_KALLA, las_vmi_mask, rastrera_vmi
from ekologi.tradlager import (
    TRADLAGER_FIL,
    TRADLAGER_KALLA,
    TradFel,
    Tradpunkt,
    klipp_punkter,
    las_tradlager,
    las_tradpunkter,
    skriv_tradlager,
)
from geografi.modell import (
    KONTRAKT_GEOGRAFI,
    MANIFEST_NAMN,
    GeografiFel,
    lager_mapp,
    lagra,
    las_manifest,
    versionsmapp,
)
from kommuner.skapa import skriv_oppenhet_toml

STANDARD_TRAD_NATVERK = "adellov-nacka"
"""Nätverket vars `[valfritt_lager.falt]` anger fältnamnen när en trädfil mappas (`--trad-natverk`)."""


def bygg_markunderlag(
    geo_rot: Path,
    bygge: Path,
    karna_3011: tuple[float, float, float, float],
    *,
    nmd_fil: Path | None,
    vmi_fil: Path | None,
    troskel_m: float,
    villkor_kontrollerade: bool = False,
    trad_fil: Path | None = None,
    trad_natverk: str = STANDARD_TRAD_NATVERK,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Bygger och lagrar NMD-, VMI- och trädlagren. Returnerar (lager, kallor, varningar) att lägga i manifestet.

    `troskel_m` är den längsta spridningströskel (effektiva m) som geografins nätverk ska prövas mot;
    bufferten blir 2-5 km runt kärnan. VMI utan NMD är tillåtet men ger en varning (VMI har bara mening
    ihop med NMD-rutnätet). Trädpunkterna läses med fältnamnen ur `trad_natverk` (valfritt lager),
    klipps till kärnan plus bufferten och sparas utan någon annan uppgift än punkt, trädslag och stamomfång.
    """
    lager: dict[str, Any] = {}
    kallor: dict[str, Any] = {}
    varningar: list[str] = []
    if nmd_fil is None and vmi_fil is None and trad_fil is None:
        return lager, kallor, varningar
    buffert = buffert_for_troskel(troskel_m)
    for fil, namn in ((nmd_fil, "NMD"), (vmi_fil, "VMI"), (trad_fil, "Träd")):
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
    if trad_fil is not None:
        mapp = bygge / "tradpunkter"
        if mapp.exists():
            shutil.rmtree(mapp)
        trad_lager, kalla_t, trad_varn = _bygg_tradlager(
            geo_rot, mapp, trad_fil, karna_3011, buffert, trad_natverk, villkor_kontrollerade
        )
        lager["tradpunkter"] = trad_lager
        kallor["tradpunkter"] = kalla_t
        varningar.extend(trad_varn)
    return lager, kallor, varningar


def _bygg_tradlager(
    geo_rot: Path, mapp: Path, trad_fil: Path, karna: tuple[float, float, float, float], buffert: float,
    trad_natverk: str, villkor_kontrollerade: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    from ekologi import EkologiFel, hamta_natverk

    try:
        nv = hamta_natverk(trad_natverk)
        konf = next((v.trad for v in nv.valfria_lager if v.trad is not None), None)
        if konf is None:
            raise GeografiFel(f"Nätverket {trad_natverk} har inget byggt trädlager ([[valfritt_lager]] med status byggt).")
        las = las_tradpunkter(trad_fil, konf.falt)
        e0, n0, e1, n1 = karna
        utsnitt = (e0 - buffert, n0 - buffert, e1 + buffert, n1 + buffert)
        kvar = klipp_punkter(las.punkter, utsnitt)
    except (TradFel, EkologiFel) as exc:
        raise GeografiFel(f"Träd: {exc}") from exc
    varningar = [f"Träd: {v}" for v in las.varningar]
    if not kvar:
        varningar.append("Träd: inga trädpunkter inom kärnan plus bufferten; lagret är tomt.")
    skriv_tradlager(kvar, mapp / TRADLAGER_FIL, antal_i_filen=las.antal_i_filen, kallfil_sha256=las.fil_sha256)
    lg = lagra(geo_rot, mapp)
    skriv_oppenhet_toml(
        lg.mapp, oppen=villkor_kontrollerade, licens="se källan", kalla=TRADLAGER_KALLA,
        anledning="Trädfilens villkor är inte kontrollerade av Floden: stängt tills --villkor-kontrollerade."
        if not villkor_kontrollerade else "Villkoren är kontrollerade (--villkor-kontrollerade).",
    )  # fmt: skip
    return (
        {
            "sha256": lg.sha256, "buffert_m": buffert, "antal_punkter": len(kvar), "antal_i_filen": las.antal_i_filen,
            "sparade_falt": ["e_m", "n_m", "tradslag", "stamomkrets_cm", "diameter_cm"],
            "filer": [{"fil": f, "sha256": s} for f, s in lg.filer],
        },
        {"kalla": TRADLAGER_KALLA, "licens": "se källan", "faltkonfiguration": trad_natverk},
        varningar,
    )


Rektangel = tuple[float, float, float, float]
"""(x_min, y_min, x_max, y_max) i EPSG:3011."""


@dataclass(frozen=True)
class GeografiMarkunderlag:
    """NMD (och valfritt VMI) ur en geografi, utklippt till scenariots rutor plus kontextbuffert.

    `nmd` och `vmi` ligger på samma rutnät (VMI-lagret mappas på NMD-rutnätet) och har klippts till
    samma utsnitt. `lager_buffert_m` är den buffert geografin själv mappade med (manifestet).
    """

    ref: str
    manifest_sha256: str
    nmd: NmdLager
    nmd_sha256: str
    vmi: NmdLager | None
    vmi_sha256: str | None
    klippt_utsnitt: Rektangel
    klippt_buffert_m: float
    lager_buffert_m: float | None
    kalla: str
    varningar: list[str] = field(default_factory=list)
    tradpunkter: tuple[Tradpunkt, ...] | None = None
    trad_sha256: str | None = None

    def som_dict(self) -> dict[str, Any]:
        """Proveniens för `ekologi.json` (`natverk.klassraster`); inga sökvägar."""
        return {
            "geografi": self.ref,
            "manifest_sha256": self.manifest_sha256,
            "nmd_lager_sha256": self.nmd_sha256,
            "vmi_lager_sha256": self.vmi_sha256,
            "tradpunkter_lager_sha256": self.trad_sha256,
            "utsnitt_3011": [float(v) for v in self.klippt_utsnitt],
            "kontextbuffert_m": self.klippt_buffert_m,
            "geografins_buffert_m": self.lager_buffert_m,
            "kalla": self.kalla,
        }


def klipp_lager(lager: NmdLager, utsnitt: Rektangel) -> tuple[NmdLager, Rektangel]:
    """Klipper ett rasterlager till `utsnitt` (hela celler, aldrig utanför lagret).

    Rutnätet behålls (samma cellgränser), så före och efter ger samma raster. Ett tomt snitt ger
    `NmdFel`."""
    e0, n0, e1, n1 = utsnitt
    c = lager.cell_m
    h, b = lager.klasser.shape
    k0 = max(0, math.floor((e0 - lager.x_min_m) / c + 1e-9))
    k1 = min(b, math.ceil((e1 - lager.x_min_m) / c - 1e-9))
    r0 = max(0, math.floor((lager.y_max_m - n1) / c + 1e-9))
    r1 = min(h, math.ceil((lager.y_max_m - n0) / c - 1e-9))
    if k1 <= k0 or r1 <= r0:
        raise NmdFel(
            f"Utsnittet {tuple(round(v) for v in utsnitt)} ligger utanför geografins lager "
            f"{tuple(round(v) for v in lager.utbredning)}."
        )
    ut = NmdLager(
        lager.klasser[r0:r1, k0:k1].copy(), lager.x_min_m + k0 * c, lager.y_max_m - r0 * c, c
    )
    return ut, ut.utbredning


def _geografins_manifest(manifest_fil: Path) -> tuple[dict[str, Any], str, Path]:
    """Läser och verifierar manifestet (K46) på `<geo-rot>/<id>/<version>/manifest.json`."""
    fil = Path(manifest_fil)
    if not fil.is_file():
        raise GeografiFel(f"Geografins manifest {fil.name} finns inte.")
    try:
        d = json.loads(fil.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise GeografiFel(f"Manifestet {fil.name} går inte att läsa.") from exc
    if not isinstance(d, dict) or d.get("kontrakt") != KONTRAKT_GEOGRAFI:
        raise GeografiFel(f"{fil.name} är inte ett geografimanifest ({KONTRAKT_GEOGRAFI}).")
    geo_rot = fil.resolve().parents[2]
    ref = f"{d.get('id')}@{d.get('version')}"
    if versionsmapp(geo_rot, str(d.get("id")), str(d.get("version"))).resolve() != fil.resolve().parent:
        raise GeografiFel(
            f"Manifestet ligger inte på {MANIFEST_NAMN}s plats <geo-rot>/{d.get('id')}/{d.get('version')}/."
        )
    manifest, sha = las_manifest(geo_rot, ref)  # verifierar sha256 för varje lagerfil
    return manifest, sha, geo_rot


def las_markunderlag(
    manifest_fil: str | Path,
    rutor: Rektangel,
    troskel_m: float,
    *,
    med_vmi: bool = False,
    med_trad: bool = False,
) -> GeografiMarkunderlag:
    """NMD (och VMI om `med_vmi`, trädpunkter om `med_trad`) ur geografins manifest, klippt till `rutor` plus kontextbuffert.

    `rutor` är scenariots rutor (union, EPSG:3011). Bufferten är nätverkets längsta tröskel,
    begränsad till 2-5 km (`buffert_for_troskel`); den klipps också mot geografins eget lager. Om
    geografins lager är mappat med mindre buffert än behovet står det i `varningar` (resultatet saknar
    då kontext utanför lagret). Lagren är sha256-verifierade av manifestet.
    """
    manifest, msha, geo_rot = _geografins_manifest(Path(manifest_fil))
    ref = f"{manifest['id']}@{manifest['version']}"
    lager = manifest.get("lager", {})
    if "nmd" not in lager:
        raise GeografiFel(
            f"Geografin {ref} har inget NMD-lager (lager.nmd). Mappa om geografin med "
            f"`floden geografi mappa {manifest['id']} ... --nmd <NMD-fil>`."
        )
    buffert = buffert_for_troskel(troskel_m)
    e0, n0, e1, n1 = rutor
    onskat: Rektangel = (e0 - buffert, n0 - buffert, e1 + buffert, n1 + buffert)
    nmd_mapp = lager_mapp(geo_rot, str(lager["nmd"]["sha256"]))
    nmd_full = las_nmd(nmd_mapp / "nmd.tif")
    nmd, utsnitt = klipp_lager(nmd_full, onskat)
    varningar: list[str] = []
    lager_buffert = lager["nmd"].get("buffert_m")
    if lager_buffert is not None and float(lager_buffert) < buffert - 1e-9:
        varningar.append(
            f"Geografins NMD-lager är mappat med {float(lager_buffert):g} m buffert, kortare än "
            f"nätverkets kontextbehov ({buffert:g} m): markunderlaget saknar kontext längre bort, "
            "vilket kan ge för låg konnektivitet vid kanten."
        )
    if (
        utsnitt[0] > onskat[0] + 1e-6
        or utsnitt[1] > onskat[1] + 1e-6
        or utsnitt[2] < onskat[2] - 1e-6
        or utsnitt[3] < onskat[3] - 1e-6
    ):
        varningar.append(
            "Geografins NMD-lager täcker inte hela kontextbufferten; utsnittet är begränsat till lagret."
        )
    vmi: NmdLager | None = None
    vsha: str | None = None
    if med_vmi:
        if "vmi" not in lager:
            raise GeografiFel(
                f"Geografin {ref} har inget VMI-lager (lager.vmi); anvand_vmi kan inte användas."
            )
        vmi_full = las_vmi_mask(lager_mapp(geo_rot, str(lager["vmi"]["sha256"])) / "vmi.tif")
        vmi, _ = klipp_lager(vmi_full, utsnitt)
        vsha = str(lager["vmi"]["sha256"])
    trad: tuple[Tradpunkt, ...] | None = None
    tsha: str | None = None
    if med_trad:
        if "tradpunkter" not in lager:
            raise GeografiFel(
                f"Geografin {ref} har inget trädlager (lager.tradpunkter). Mappa om geografin med "
                f"`floden geografi mappa {manifest['id']} ... --trad <fil>`."
            )
        tsha = str(lager["tradpunkter"]["sha256"])
        try:
            trad = klipp_punkter(las_tradlager(lager_mapp(geo_rot, tsha) / TRADLAGER_FIL).punkter, utsnitt)
        except TradFel as exc:
            raise GeografiFel(f"Träd: {exc}") from exc
        tb = lager["tradpunkter"].get("buffert_m")
        if tb is not None and float(tb) < buffert - 1e-9:
            varningar.append(
                f"Geografins trädlager är mappat med {float(tb):g} m buffert, kortare än nätverkets kontextbehov "
                f"({buffert:g} m): träd längre bort saknas."
            )
    kalla = str(manifest.get("kallor", {}).get("nmd", {}).get("kalla", NMD_KALLA))
    return GeografiMarkunderlag(
        ref=ref,
        manifest_sha256=msha,
        nmd=nmd,
        nmd_sha256=str(lager["nmd"]["sha256"]),
        vmi=vmi,
        vmi_sha256=vsha,
        klippt_utsnitt=utsnitt,
        klippt_buffert_m=buffert,
        lager_buffert_m=None if lager_buffert is None else float(lager_buffert),
        kalla=kalla,
        varningar=varningar,
        tradpunkter=trad,
        trad_sha256=tsha,
    )


__all__ = [
    "GeografiMarkunderlag",
    "bygg_markunderlag",
    "klipp_lager",
    "las_markunderlag",
]
