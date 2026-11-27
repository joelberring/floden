# ruff: noqa: E501
"""Ekologilösaren för kärnan (K21): `ekologi.konnektivitet` på K1 v2-rutor (kontrakt K44 och K45).

Kärnan (`src/ekologi/`, numpy och scipy) räknar; den här filen är adaptern mot K1: rutorna (efter
att kärnan har tillämpat K9) läggs som en mosaik på ett grövre rutnät, K1:s markslag översätts till
ekologikärnans markslag enligt `data/ekologi/markslag.toml` (konfiguration, inte kod), trädskiktet
tas ur `vegetation.tif`, och resultatet skrivs som `stromtathet.tif`, `kansighet.tif` och
`ekologi.json` (K45). Metoden är `skiss`: artprofilernas parametrar saknar exakta källhänvisningar.

Lösaren är global (påverkansradie `None`): konnektivitet beror på fläckar och hinder i hela området.
Före/efter jämförs med `[[jamforelser]]` på `stromtathet`. Ekologipaketet importeras i `kor()`.
"""

from __future__ import annotations

import json
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from karna.analyslosare import Analyslosare, LosarFel, LosarKontext, LosarUtdata

STANDARD_MARKSLAGSKONFIG = (
    Path(__file__).resolve().parents[2] / "data" / "ekologi" / "markslag.toml"
)
FORMAT_K45 = "floden-ekologi-konnektivitet/v1"
STANDARD_CELLSTORLEK_M = 4.0
STANDARD_MAX_FLACKAR = 120
STANDARD_MAX_CELLER = 250_000
STANDARD_SPRIDNING_FAKTOR = (0.8, 1.25)
"""Spridningsavståndets faktorer för intervallet (kärnans standard)."""
_PARAMETRAR = {
    "artprofil",
    "natverk",
    "natverk_sha256",
    "anvand_vmi",
    "anvand_trad",
    "biotop_klasskoder",
    "cellstorlek_m",
    "max_flackar",
    "max_celler",
    "motstand_faktor_intervall",
    "spridning_faktor_intervall",
    "markslagsmappning_sha256",
}


@dataclass(frozen=True)
class Markslagskonfig:
    """`data/ekologi/markslag.toml` inläst och kontrollerad."""

    sokvag: Path
    sha256: str
    version: int
    okant_karnkod: int
    k1_till_karna: Mapping[int, int]
    tradskikt_min_hojd_m: float
    tradskikt_karnkod: int
    tradskikt_pa_k1_markslag: frozenset[int]

    def oversatt(
        self, k1_markslag: NDArray[np.uint8], vegetation_m: NDArray[np.float64] | None
    ) -> tuple[NDArray[np.uint8], int]:
        """Ekologikärnans markslag och antalet celler med okänt K1-markslag (kod 0)."""
        lut = np.full(256, self.okant_karnkod, dtype=np.uint8)
        for k1, karna in self.k1_till_karna.items():
            lut[k1] = karna
        ut = lut[k1_markslag]
        if vegetation_m is not None:
            pa = np.isin(k1_markslag, sorted(self.tradskikt_pa_k1_markslag))
            ut = np.where(
                pa & (vegetation_m >= self.tradskikt_min_hojd_m), self.tradskikt_karnkod, ut
            ).astype(np.uint8)
        return ut, int(np.count_nonzero(k1_markslag == 0))


def las_markslagskonfig(sokvag: str | Path = STANDARD_MARKSLAGSKONFIG) -> Markslagskonfig:
    fil = Path(sokvag)
    try:
        d = tomllib.loads(fil.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LosarFel(f"Markslagsmappningen {fil} går inte att läsa: {exc}") from exc
    try:
        rader = d["markslag"]
        veg = d["vegetation"]
        k1_till: dict[int, int] = {}
        for nyckel, post in rader.items():
            k1, kod = int(nyckel), int(post["karnkod"])
            if not (0 <= k1 <= 9 and 0 <= kod <= 255):
                raise ValueError(f"kod utanför intervallet: {nyckel}")
            k1_till[k1] = kod
        saknas = sorted(set(range(10)) - set(k1_till))
        if saknas:
            raise ValueError(f"K1-koder saknas: {saknas}")
        return Markslagskonfig(
            sokvag=fil,
            sha256=sha256_fil(fil),
            version=int(d["version"]),
            okant_karnkod=int(d["okant_karnkod"]),
            k1_till_karna=k1_till,
            tradskikt_min_hojd_m=float(veg["tradskikt_min_hojd_m"]),
            tradskikt_karnkod=int(veg["tradskikt_karnkod"]),
            tradskikt_pa_k1_markslag=frozenset(int(x) for x in veg["tradskikt_pa_k1_markslag"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LosarFel(f"Markslagsmappningen {fil.name} är ogiltig: {exc!r}") from exc


def _tal(p: Mapping[str, Any], namn: str, standard: float, *, over: float) -> float:
    v = p.get(namn, standard)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise LosarFel(f"Parametern '{namn}' måste vara ett ändligt tal, fick {v!r}.")
    if float(v) <= over:
        raise LosarFel(f"Parametern '{namn}' måste vara > {over}, fick {v}.")
    return float(v)


def _heltal(p: Mapping[str, Any], namn: str, standard: int, *, minst: int) -> int:
    v = p.get(namn, standard)
    if isinstance(v, bool) or not isinstance(v, int | float) or float(v) != int(v):
        raise LosarFel(f"Parametern '{namn}' måste vara ett heltal, fick {v!r}.")
    if int(v) < minst:
        raise LosarFel(f"Parametern '{namn}' måste vara >= {minst}, fick {v}.")
    return int(v)


def _intervall(p: Mapping[str, Any], namn: str) -> list[float] | None:
    v = p.get(namn)
    if v is None:
        return None
    if (
        not isinstance(v, list | tuple)
        or len(v) != 2
        or any(isinstance(x, bool) or not isinstance(x, int | float) for x in v)
    ):
        raise LosarFel(f"Parametern '{namn}' måste vara [min, max], fick {v!r}.")
    lo, hi = float(v[0]), float(v[1])
    if not (math.isfinite(lo) and math.isfinite(hi) and 0.0 < lo <= hi):
        raise LosarFel(f"Parametern '{namn}' måste uppfylla 0 < min <= max, fick {v!r}.")
    return [lo, hi]


class Konnektivitet(Analyslosare):
    """Ekologisk konnektivitet (kretsteori, PC och ECA) för en artprofil, före/efter via K9."""

    namn: ClassVar[str] = "ekologi.konnektivitet"
    doman: ClassVar[str] = "ekologi"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = (
        "Ekologisk konnektivitet för en artprofil (skiss): ECA och PC med monotona intervall, "
        "dPC per habitatfläck, strömtäthet (flaskhalsar) och känslighet. Parametrarna natverk "
        "(habitatnätverk med signalarter, K47; förval) eller artprofil (föråldrad), cellstorlek_m, "
        "max_flackar och max_celler."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = (
        "artprofil", "nmd", "kand_vatmark", "biotopraster", "geografi", "tradpunkter"
    )
    utdataroller: ClassVar[tuple[str, ...]] = ("stromtathet", "kansighet", "ekologi")

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        from ekologi import STANDARD_ARTPROFILER

        okanda = sorted(set(parametrar) - _PARAMETRAR)
        if okanda:
            raise LosarFel(f"{self.namn}: okända parametrar {okanda}.")
        for satt in ("markslagsmappning_sha256", "natverk_sha256"):
            if satt in parametrar:
                raise LosarFel(f"{self.namn}: '{satt}' sätts av lösaren.")
        profil = parametrar.get("artprofil")
        natverk = parametrar.get("natverk")
        if profil is not None and natverk is not None:
            raise LosarFel(f"{self.namn}: ange bara en av parametrarna 'natverk' och 'artprofil'.")
        if profil is not None and profil not in STANDARD_ARTPROFILER:
            raise LosarFel(
                f"{self.namn}: okänd artprofil {profil!r}. Giltiga: "
                f"{', '.join(sorted(STANDARD_ARTPROFILER))} (eller indata 'artprofil', K44)."
            )
        natverk_hash: str | None = None
        from ekologi import hamta_natverk

        if natverk is not None:
            from ekologi import EkologiFel, natverk_sha256

            try:
                n = hamta_natverk(str(natverk))
                natverk_hash = natverk_sha256(n.natverk_id)
            except EkologiFel as exc:
                raise LosarFel(f"{self.namn}: {exc}") from exc
            if not n.korbar:
                raise LosarFel(f"{self.namn}: nätverket {natverk} är inte körbart: {n.korbar_orsak}")
            for fel in ("motstand_faktor_intervall", "spridning_faktor_intervall"):
                if parametrar.get(fel) is not None:
                    raise LosarFel(
                        f"{self.namn}: '{fel}' gäller bara artprofil; nätverk har källbaserat intervall."
                    )
        anvand_trad = parametrar.get("anvand_trad", False)
        if not isinstance(anvand_trad, bool):
            raise LosarFel(f"{self.namn}: 'anvand_trad' ska vara true eller false.")
        if anvand_trad and (
            natverk is None or not any(v.trad is not None for v in hamta_natverk(str(natverk)).valfria_lager)
        ):
            raise LosarFel(
                f"{self.namn}: anvand_trad kräver ett nätverk med ett byggt trädlager "
                "([[valfritt_lager]] med status byggt), till exempel adellov-nacka."
            )
        spr = _intervall(parametrar, "spridning_faktor_intervall")
        cell_ra = parametrar.get("cellstorlek_m")
        biotop = parametrar.get("biotop_klasskoder")
        if biotop is not None and (
            not isinstance(biotop, Mapping)
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in biotop.values())
        ):
            raise LosarFel(f"{self.namn}: 'biotop_klasskoder' ska vara klassnamn till heltalskod.")
        return {
            "artprofil": profil,
            "natverk": natverk,
            "natverk_sha256": natverk_hash,
            "anvand_vmi": bool(parametrar.get("anvand_vmi", False)),
            "anvand_trad": anvand_trad,
            "biotop_klasskoder": dict(biotop) if biotop is not None else None,
            "cellstorlek_m": (
                None if (natverk is not None and cell_ra is None)
                else _tal(parametrar, "cellstorlek_m", STANDARD_CELLSTORLEK_M, over=0.0)
            ),
            "max_flackar": _heltal(parametrar, "max_flackar", STANDARD_MAX_FLACKAR, minst=2),
            "max_celler": _heltal(parametrar, "max_celler", STANDARD_MAX_CELLER, minst=1),
            "motstand_faktor_intervall": _intervall(parametrar, "motstand_faktor_intervall"),
            "spridning_faktor_intervall": (
                spr if spr is not None else list(STANDARD_SPRIDNING_FAKTOR)
            ),
            # Konfigurationens sha256 ingår i nyckeln: ändrad mappning ger nya resultat.
            "markslagsmappning_sha256": las_markslagskonfig().sha256,
        }

    def kor(self, k: LosarKontext) -> LosarUtdata:
        from ekologi import (
            STANDARD_ARTPROFILER,
            EkologiFel,
            berakna_konnektivitet,
            extrahera_habitatflackar,
            kor_monoton_ekologi_intervall,
            las_artprofil_fil,
        )
        from ekologi.artprofil_format import (
            PARAMETERKALLA_EGEN,
            PARAMETERKALLA_STANDARD,
            artprofil_till_dict,
        )
        from karna.geometri.mosaik import bygg_ytmosaik
        from karna.geometri.rasterfil import GeometriFel
        from testsvit.k1_io import skriv_geotiff

        p = k.parametrar
        if p.get("natverk") is not None:
            return _kor_natverk(self, k)
        fil_profil = k.indata.get("artprofil")
        if (p["artprofil"] is None) == (fil_profil is None):
            raise LosarFel(
                f"{self.namn}: ange exakt en av parametrarna 'natverk' och 'artprofil' "
                "eller indata 'artprofil'."
            )
        try:
            if fil_profil is not None:
                profil = las_artprofil_fil(fil_profil)
                if profil.profil_id in STANDARD_ARTPROFILER:
                    raise LosarFel(
                        f"{self.namn}: en egen artprofil får inte ha ett standardprofil-id "
                        f"('{profil.profil_id}')."
                    )
                parameterkalla = PARAMETERKALLA_EGEN
            else:
                profil = STANDARD_ARTPROFILER[str(p["artprofil"])]
                parameterkalla = PARAMETERKALLA_STANDARD
        except EkologiFel as exc:
            raise LosarFel(f"{self.namn}: {exc}") from exc

        konfig = las_markslagskonfig()
        if konfig.sha256 != p["markslagsmappning_sha256"]:
            raise LosarFel(f"{self.namn}: markslagsmappningen ändrades under körningen.")
        cell = float(p["cellstorlek_m"])
        rutor = k.scenario.rutor
        x0 = min(r.x_min for r in rutor)
        x1 = max(r.x_max for r in rutor)
        y0 = min(r.y_min for r in rutor)
        y1 = max(r.y_max for r in rutor)
        antal_celler = math.ceil((x1 - x0) / cell) * math.ceil((y1 - y0) / cell)
        if antal_celler > int(p["max_celler"]):
            raise LosarFel(
                f"{self.namn}: området blir {antal_celler} celler med cellstorlek_m={cell:g}, "
                f"över max_celler={p['max_celler']}. Öka cellstorlek_m, minska området eller "
                "höj max_celler."
            )
        varningar: list[str] = []
        med_veg = [r for r in rutor if (r.mapp / "vegetation.tif").is_file()]
        if len(med_veg) < len(rutor):
            varningar.append(
                f"{len(rutor) - len(med_veg)} av {len(rutor)} rutor saknar vegetation.tif: "
                "trädskikt och kronhöjd saknas där (antas inte finnas, ingen mätning)."
            )
        try:
            mos = bygg_ytmosaik(
                [r.mapp for r in rutor],
                cell,
                omsampling="max",
                vegetation=bool(med_veg),
                tillat_saknad_vegetation=True,
            )
        except GeometriFel as exc:
            raise LosarFel(f"{self.namn}: {exc}") from exc
        if mos.saknade_celler:
            varningar.append(
                f"{mos.saknade_celler} rutnätsceller täcks inte av någon ruta och räknas som okänt "
                "markslag."
            )
        veg = mos.vegetation_hojd_m
        slag, okanda = konfig.oversatt(mos.markslag, veg)
        if okanda:
            varningar.append(
                f"{okanda} celler har okänt markslag (K1-kod 0): profilens standardmotstånd "
                f"({profil.standard_motstand:g}) gäller där (flaggat, inte data)."
            )
        kron: NDArray[np.float64] | None = None
        if veg is not None:
            kron = np.where(mos.byggnadshojd_m > 0.0, 0.0, veg)
        elif profil.min_kronhojd_m > 0.0:
            varningar.append(
                "Ingen vegetation.tif: kronhöjd är okänd, så artprofilens krav på kronhöjd "
                f"({profil.min_kronhojd_m:g} m) tillämpas inte."
            )

        try:
            habitat = extrahera_habitatflackar(
                slag, profil, cell_m=cell, x_min_m=mos.x_min_m, y_max_m=mos.y_max_m, kronhojd_m=kron
            )
            if len(habitat.flackar) > int(p["max_flackar"]):
                raise LosarFel(
                    f"{self.namn}: {len(habitat.flackar)} habitatfläckar, över max_flackar="
                    f"{p['max_flackar']}. Antalet fläckpar växer kvadratiskt; höj max_flackar, "
                    "öka cellstorlek_m eller välj en profil med större min_area_m2."
                )
            res = berakna_konnektivitet(
                slag,
                profil,
                cell_m=cell,
                x_min_m=mos.x_min_m,
                y_max_m=mos.y_max_m,
                kronhojd_m=kron,
                berakna_flaskhalsar=True,
            )
            iv = kor_monoton_ekologi_intervall(
                slag,
                profil,
                cell_m=cell,
                motstand_faktor_intervall=(
                    None if p["motstand_faktor_intervall"] is None
                    else (p["motstand_faktor_intervall"][0], p["motstand_faktor_intervall"][1])
                ),
                spridning_faktor_intervall=(
                    p["spridning_faktor_intervall"][0], p["spridning_faktor_intervall"][1]
                ),
                kronhojd_m=kron,
            )
        except EkologiFel as exc:
            raise LosarFel(f"{self.namn}: {exc}") from exc

        form = slag.shape
        nodata = np.full(form, np.nan, dtype=np.float32)
        if res.stromtathet_sammanlagd is None:
            varningar.append(
                f"{res.antal_flackar} habitatfläck(ar): strömtäthet och känslighet kräver minst "
                "två fläckar och lämnas som nodata (NaN)."
            )
        j = nodata if res.stromtathet_sammanlagd is None else np.asarray(
            res.stromtathet_sammanlagd, dtype=np.float32
        )
        kg = nodata if res.kanslighet_sammanlagd is None else np.asarray(
            res.kanslighet_sammanlagd, dtype=np.float32
        )
        for namn, arr in (("stromtathet.tif", j), ("kansighet.tif", kg)):
            skriv_geotiff(
                k.utmapp / namn, arr, x_min=mos.x_min_m, y_max=mos.y_max_m, pixelstorlek=cell
            )

        betydelse = {b.flack_id: b for b in res.flack_betydelse}
        dpc = []
        for f in habitat.flackar:
            b = betydelse[f.flack_id]
            dpc.append(
                {
                    "flack_id": f.flack_id,
                    "area_m2": f.area_m2,
                    "effektiv_area_m2": f.effektiv_area_m2,
                    "centroid_e_m": f.centroid_e_m,
                    "centroid_n_m": f.centroid_n_m,
                    "dPC_procent": b.dpc_procent,
                    "dPC_intra_procent": b.dpc_intra_procent,
                    "dPC_flux_procent": b.dpc_flux_procent,
                    "dPC_connector_procent": b.dpc_connector_procent,
                }
            )
        dpc.sort(key=lambda r: (-float(r["dPC_procent"]), int(r["flack_id"])))
        dok = {
            "format": FORMAT_K45,
            "metodstatus": "skiss",
            "artprofil_id": profil.profil_id,
            "artprofil": artprofil_till_dict(profil),
            "parameterkallor": parameterkalla,
            "cellstorlek_m": cell,
            "analys_area_m2": res.analys_area_m2,
            "antal_flackar": res.antal_flackar,
            "total_habitat_area_m2": res.total_habitat_area_m2,
            "ECA_m2": res.eca_m2,
            "PC": res.pc,
            "ECA_intervall_m2": [iv.eca_min_m2, iv.eca_max_m2],
            "PC_intervall": [iv.pc_min, iv.pc_max],
            "intervall_faktorer": {
                "motstand": list(iv.motstand_faktor_intervall),
                "spridning": list(iv.spridning_faktor_intervall),
            },
            "dPC": dpc,
            "markslagsmappning": {"sha256": konfig.sha256, "version": konfig.version},
            "intervall_typ": "faktorer",
            "natverk": None,
            "varningar": varningar,
        }
        (k.utmapp / "ekologi.json").write_text(
            json.dumps(dok, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
        )
        return LosarUtdata(
            filer={
                "stromtathet": "stromtathet.tif",
                "kansighet": "kansighet.tif",
                "ekologi": "ekologi.json",
            },
            matvarden={
                "artprofil": profil.profil_id,
                "eca_m2": res.eca_m2,
                "eca_min_m2": iv.eca_min_m2,
                "eca_max_m2": iv.eca_max_m2,
                "pc": res.pc,
                "pc_min": iv.pc_min,
                "pc_max": iv.pc_max,
                "antal_flackar": res.antal_flackar,
                "total_habitat_area_m2": res.total_habitat_area_m2,
                "cellstorlek_m": cell,
            },
            varningar=tuple(varningar),
        )


def bygg_dpc_tabell(
    flackar: Any,
    betydelse: Mapping[int, Any],
) -> list[dict[str, Any]]:
    """Bygger sorterad dPC-tabell per habitatfläck (`flack_id`, `area_ha`, `dpc_procent`,
    `dpc_intra`, `dpc_flux`, `dpc_connector`), viktigaste fläcken först."""
    rader: list[dict[str, Any]] = []
    for f in flackar:
        b = betydelse[f.flack_id]
        rader.append(
            {
                "flack_id": int(f.flack_id),
                "area_ha": round(float(f.area_m2) / 10_000.0, 6),
                "dpc_procent": float(b.dpc_procent),
                "dpc_intra": float(b.dpc_intra_procent),
                "dpc_flux": float(b.dpc_flux_procent),
                "dpc_connector": float(b.dpc_connector_procent),
            }
        )
    rader.sort(key=lambda r: (-float(r["dpc_procent"]), int(r["flack_id"])))
    return rader


def _kor_natverk(losare: Konnektivitet, k: LosarKontext) -> LosarUtdata:
    """Körning för ett habitatnätverk (K47): NMD (eller biotopraster) som mark, K9-hus som NMD 51."""
    from data_inlasning.nmd import NMD_BYGGNAD, NmdFel, NmdLager, lagg_hus_som_nmd51, las_nmd
    from data_inlasning.vmi import las_vmi_mask, stampla_pa_grid
    from ekologi import (
        EkologiFel,
        berakna_konnektivitet,
        extrahera_habitatflackar,
        hamta_natverk,
        kallbaserat_intervall,
        till_artprofil,
    )
    from ekologi.artprofil_format import artprofil_till_dict
    from ekologi.natverk import METODSTATUS_TEXT, rensa_rivna_hus_i_nmd
    from karna.geometri.mosaik import bygg_ytmosaik
    from karna.geometri.rasterfil import GeometriFel
    from testsvit.k1_io import skriv_geotiff

    p = k.parametrar
    namn = losare.namn
    nv = hamta_natverk(str(p["natverk"]))
    biotop = nv.omfattning == "biotop"
    roll = "biotopraster" if biotop else "nmd"
    fil = k.indata.get(roll)
    geo_fil = k.indata.get("geografi")
    if geo_fil is not None and (fil is not None or biotop):
        raise LosarFel(
            f"{namn}: ange markunderlaget antingen som indata 'geografi' (K46-manifestet; NMD och VMI "
            "ur geografins lager) eller som indata 'nmd', inte båda"
            + (" (biotopnätverk läser bara 'biotopraster')." if biotop else ".")
        )
    klasskoder = p.get("biotop_klasskoder")
    geo_mark = None
    varningar: list[str] = []
    if fil is None and geo_fil is None:
        raise LosarFel(
            f"{namn}: nätverket {nv.natverk_id} kräver indata '{roll}'"
            + (
                " (stadens biotopklasser som heltalsraster; nätverket körs bara lokalt) och "
                "parametern 'biotop_klasskoder'."
                if biotop
                else " (NMD-lager, EPSG:3011; skapas med `python -m data_inlasning.nmd klipp`) eller "
                "indata 'geografi' (manifestet för en geografi med lager.nmd, K46)."
            )
        )
    try:
        if geo_fil is not None:
            from geografi.markunderlag import las_markunderlag
            from geografi.modell import GeografiFel

            rutor0 = k.scenario.rutor
            troskel = max((s_.troskel_m for s_ in nv.scenarier if s_.troskel_m is not None), default=None)
            if troskel is None:
                raise LosarFel(f"{namn}: nätverket {nv.natverk_id} har ingen tröskel att sätta kontextbufferten efter.")
            try:
                geo_mark = las_markunderlag(
                    geo_fil,
                    (
                        min(r.x_min for r in rutor0),
                        min(r.y_min for r in rutor0),
                        max(r.x_max for r in rutor0),
                        max(r.y_max for r in rutor0),
                    ),
                    troskel,
                    med_vmi=bool(p.get("anvand_vmi")) and k.indata.get("kand_vatmark") is None,
                    med_trad=bool(p.get("anvand_trad")) and k.indata.get("tradpunkter") is None,
                )
            except GeografiFel as exc:
                raise LosarFel(f"{namn}: {exc}") from exc
            lager = geo_mark.nmd
            varningar += geo_mark.varningar
        else:
            assert fil is not None  # kontrollerat ovan: fil eller geo_fil finns
            lager = las_nmd(fil) if not biotop else _las_klassraster(fil)
        if biotop and klasskoder is None:
            raise LosarFel(f"{namn}: biotopnätverk kräver parametern 'biotop_klasskoder'.")
        profiler = {
            s.scenario_id: till_artprofil(nv, s.scenario_id, klasskoder=klasskoder)
            for s in nv.scenarier
        }
    except (EkologiFel, NmdFel) as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    huvud = nv.scenario()
    profil = profiler[huvud.scenario_id]
    cell = float(p["cellstorlek_m"]) if p["cellstorlek_m"] is not None else lager.cell_m
    klasser = lager.klasser
    x0, y1 = lager.x_min_m, lager.y_max_m
    max_c = int(p["max_celler"])
    if p["cellstorlek_m"] is None and klasser.size > max_c:
        orig_celler = int(klasser.size)
        faktor = max(2, math.ceil(math.sqrt(orig_celler / max_c)))
        while True:
            cand_cell = float(lager.cell_m) * faktor
            cand_ny = max(1, int(round(lager.klasser.shape[0] * lager.cell_m / cand_cell)))
            cand_nx = max(1, int(round(lager.klasser.shape[1] * lager.cell_m / cand_cell)))
            if cand_ny * cand_nx <= max_c:
                cell = cand_cell
                klasser = _omsampla(lager, cell)
                varningar.append(
                    f"Markunderlaget ({orig_celler} celler vid {lager.cell_m:g} m) översteg "
                    f"max_celler={max_c}; omsamplades deterministiskt till {cell:g} m "
                    f"({klasser.size} celler) med närmaste granne (ange cellstorlek_m "
                    "uttryckligen för annan upplösning)."
                )
                break
            faktor += 1
    elif abs(cell - lager.cell_m) > 1e-9:
        klasser = _omsampla(lager, cell)
    if klasser.size > max_c:
        raise LosarFel(
            f"{namn}: området blir {klasser.size} celler med cellstorlek {cell:g} m, över "
            f"max_celler={max_c}. Öka cellstorlek_m, minska området eller höj max_celler."
        )
    # Ritade hus och befintliga K1-byggnader ovanpå som NMD 51 (biotop: klassen "byggnad").
    hus_kod = NMD_BYGGNAD if not biotop else (klasskoder or {}).get("byggnad")
    if hus_kod is None:
        raise LosarFel(f"{namn}: 'biotop_klasskoder' saknar klassen 'byggnad'.")
    konfig = las_markslagskonfig()
    try:
        mos = bygg_ytmosaik([r.mapp for r in k.scenario.rutor], cell, omsampling="max")
    except GeometriFel as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    slag, _ = konfig.oversatt(mos.markslag, None)
    hus_mask = slag == 2
    klasser, andrade = lagg_hus_som_nmd51(
        NmdLager(klasser, x0, y1, cell), hus_mask, mos.x_min_m, mos.y_max_m, cell, int(hus_kod)
    )
    rensade = 0
    if not biotop:
        klasser, rensade = rensa_rivna_hus_i_nmd(
            klasser,
            x0,
            y1,
            cell,
            mos.markslag,
            mos.x_min_m,
            mos.y_max_m,
            cell,
            hus_kod=int(hus_kod),
        )
        if rensade:
            varningar.append(
                f"{andrade} celler lades som byggnad ur K1/K9 (NMD 51) och {rensade} rivna "
                "byggnadsceller rensades från NMD 51 till öppen mark/grönyta enligt K1/K9."
            )
        else:
            varningar.append(
                f"{andrade} celler lades som byggnad ur K1/K9 (NMD 51); inga byggnadsceller "
                "rensades från NMD 51."
            )
    else:
        varningar.append(
            f"{andrade} celler lades som byggnad ur K1/K9 (klassen byggnad); "
            "rivna hus tas inte bort ur biotopunderlaget."
        )
    if p.get("anvand_vmi"):
        vfil = k.indata.get("kand_vatmark")
        vmi_lager = geo_mark.vmi if geo_mark is not None else None
        if (vfil is None and vmi_lager is None) or nv.vmi_klass is None or klasskoder is None:
            raise LosarFel(
                f"{namn}: anvand_vmi kräver indata 'kand_vatmark' (eller VMI-lager i geografin), ett "
                "nätverk med vmi_klass och 'biotop_klasskoder'."
            )
        if vmi_lager is None:
            assert vfil is not None  # kontrollerat ovan
            vmi_lager = las_vmi_mask(vfil)
        vm = stampla_pa_grid(vmi_lager, x0, y1, cell, klasser.shape)
        klasser = np.where(vm & (klasser != hus_kod), np.uint8(klasskoder[nv.vmi_klass]), klasser)
        varningar.append(
            "Känd våtmark ur VMI (äldre inventering, 1981-2005) används som habitat; lagret kan vara inaktuellt."
        )
    trad_dok: dict[str, Any] | None = None
    undantag: NDArray[np.bool_] | None = None
    tfil = k.indata.get("tradpunkter")
    if tfil is not None and not p.get("anvand_trad"):
        raise LosarFel(f"{namn}: indata 'tradpunkter' kräver parametern anvand_trad = true.")
    if p.get("anvand_trad"):
        klasser, undantag, trad_dok, trad_varn = _tradlager_i_raster(
            namn, nv, klasser, x0, y1, cell, geo_mark, tfil, int(hus_kod)
        )
        varningar += trad_varn
    ok = set(profil.motstand_per_markslag)
    saknade = int(np.count_nonzero(~np.isin(klasser, sorted(ok))))
    if saknade:
        varningar.append(
            f"{saknade} celler har en klass som saknas i nätverkets tabell: standardmotstånd "
            f"({profil.standard_motstand:g}) gäller där (flaggat antagande)."
        )
    try:
        habitat = extrahera_habitatflackar(
            klasser, profil, cell_m=cell, x_min_m=x0, y_max_m=y1, undantag_min_area=undantag
        )
        if len(habitat.flackar) > int(p["max_flackar"]):
            raise LosarFel(
                f"{namn}: {len(habitat.flackar)} habitatfläckar, över max_flackar={p['max_flackar']}. "
                "Antalet fläckpar växer kvadratiskt; höj max_flackar eller öka cellstorlek_m."
            )
        res = {}
        for sid, pr in profiler.items():
            res[sid] = berakna_konnektivitet(
                klasser, pr, cell_m=cell, x_min_m=x0, y_max_m=y1,
                berakna_flaskhalsar=(sid == huvud.scenario_id), undantag_min_area=undantag,
            )
        iv = kallbaserat_intervall(nv.natverk_id, res)
    except EkologiFel as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    hr = res[huvud.scenario_id]
    form = klasser.shape
    nodata = np.full(form, np.nan, dtype=np.float32)
    if hr.stromtathet_sammanlagd is None:
        varningar.append(
            f"{hr.antal_flackar} habitatfläck(ar): strömtäthet och känslighet kräver minst två "
            "fläckar och lämnas som nodata (NaN)."
        )
    j = nodata if hr.stromtathet_sammanlagd is None else np.asarray(hr.stromtathet_sammanlagd, dtype=np.float32)
    kg = nodata if hr.kanslighet_sammanlagd is None else np.asarray(hr.kanslighet_sammanlagd, dtype=np.float32)
    for fnamn, arr in (("stromtathet.tif", j), ("kansighet.tif", kg)):
        skriv_geotiff(k.utmapp / fnamn, arr, x_min=x0, y_max=y1, pixelstorlek=cell)
    betydelse = {b.flack_id: b for b in hr.flack_betydelse}
    dpc = [
        {
            "flack_id": f.flack_id, "area_m2": f.area_m2, "effektiv_area_m2": f.effektiv_area_m2,
            "centroid_e_m": f.centroid_e_m, "centroid_n_m": f.centroid_n_m,
            "dPC_procent": betydelse[f.flack_id].dpc_procent,
            "dPC_intra_procent": betydelse[f.flack_id].dpc_intra_procent,
            "dPC_flux_procent": betydelse[f.flack_id].dpc_flux_procent,
            "dPC_connector_procent": betydelse[f.flack_id].dpc_connector_procent,
        }
        for f in habitat.flackar
    ]
    dpc.sort(key=lambda r: (-float(r["dPC_procent"]), int(r["flack_id"])))
    scen_rader = []
    for s in nv.scenarier:
        d50, ant = nv.d50_for(s)
        scen_rader.append(
            {
                "id": s.scenario_id, "namn": s.namn, "troskel_m": s.troskel_m, "d50_m": d50,
                "d50_antagen": ant is not None, "antagande": ant,
                "ECA_m2": res[s.scenario_id].eca_m2, "PC": res[s.scenario_id].pc,
            }
        )
    # Jämförelse mellan habitaturval vid huvudscenariots tröskel (varje variant är ett eget habitatval).
    variant_rader = []
    try:
        for hv in nv.habitatvarianter:
            vpr = till_artprofil(nv, huvud.scenario_id, klasskoder=klasskoder, habitatvariant=hv.variant_id)
            vres = berakna_konnektivitet(
                klasser, vpr, cell_m=cell, x_min_m=x0, y_max_m=y1, berakna_flaskhalsar=False,
                undantag_min_area=undantag,
            )
            variant_rader.append(
                {"id": hv.variant_id, "namn": hv.namn, "klasser": list(hv.klasser), "typ": hv.belagd.typ,
                 "kalla": hv.belagd.kalla, "sida": hv.belagd.sida, "antal_flackar": vres.antal_flackar,
                 "total_habitat_area_m2": vres.total_habitat_area_m2, "ECA_m2": vres.eca_m2, "PC": vres.pc}
            )  # fmt: skip
    except EkologiFel as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    natverk_dok = nv.som_dict(huvud.scenario_id)
    natverk_dok["scenarier"] = scen_rader
    natverk_dok["dpc_tabell"] = bygg_dpc_tabell(habitat.flackar, betydelse)
    natverk_dok["rivna_hus_rensade_celler"] = rensade
    natverk_dok["habitat_huvud"] = {
        "id": "kandidater", "klasser": list(nv.habitat_klasser), "antal_flackar": hr.antal_flackar,
        "total_habitat_area_m2": hr.total_habitat_area_m2, "ECA_m2": hr.eca_m2, "PC": hr.pc,
    }
    natverk_dok["habitatvarianter"] = variant_rader
    natverk_dok["klassraster"] = {
        "roll": roll if geo_mark is None else "geografi",
        "cellstorlek_m": cell,
        **({"markunderlag": geo_mark.som_dict()} if geo_mark is not None else {}),
    }
    antaganden = list(natverk_dok["antaganden"])
    if trad_dok is not None:
        natverk_dok["tradlager"] = trad_dok
        for v in natverk_dok["valfria_lager"]:
            v["anvands"] = v["id"] == trad_dok["id"]
        antaganden.extend(trad_dok["antaganden"])
    if iv.ett_scenario:
        antaganden.append("Ett enda scenario i källan: inget källbaserat intervall (intervallet är en punkt).")
    dok = {
        "format": FORMAT_K45,
        "metodstatus": "skiss",
        "artprofil_id": nv.natverk_id,
        "artprofil": artprofil_till_dict(profil),
        "parameterkallor": METODSTATUS_TEXT,
        "cellstorlek_m": cell,
        "analys_area_m2": hr.analys_area_m2,
        "antal_flackar": hr.antal_flackar,
        "total_habitat_area_m2": hr.total_habitat_area_m2,
        "ECA_m2": hr.eca_m2,
        "PC": hr.pc,
        "ECA_intervall_m2": [iv.eca_min_m2, iv.eca_max_m2],
        "PC_intervall": [iv.pc_min, iv.pc_max],
        "intervall_faktorer": {"motstand": [1.0, 1.0], "spridning": [1.0, 1.0]},
        "intervall_typ": "kallbaserat",
        "dPC": dpc,
        "markslagsmappning": {"sha256": konfig.sha256, "version": konfig.version},
        "resultat_etikett": nv.resultat_etikett,
        "natverk": {**natverk_dok, "antaganden": antaganden},
        "varningar": varningar,
    }
    (k.utmapp / "ekologi.json").write_text(
        json.dumps(dok, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return LosarUtdata(
        filer={"stromtathet": "stromtathet.tif", "kansighet": "kansighet.tif", "ekologi": "ekologi.json"},
        matvarden={
            "artprofil": nv.natverk_id,
            "natverk": nv.natverk_id,
            "scenario": huvud.scenario_id,
            "eca_m2": hr.eca_m2,
            "eca_min_m2": iv.eca_min_m2,
            "eca_max_m2": iv.eca_max_m2,
            "pc": hr.pc,
            "pc_min": iv.pc_min,
            "pc_max": iv.pc_max,
            "antal_flackar": hr.antal_flackar,
            "total_habitat_area_m2": hr.total_habitat_area_m2,
            "cellstorlek_m": cell,
        },
        varningar=tuple(varningar),
    )


def _tradlager_i_raster(
    namn: str, nv: Any, klasser: NDArray[np.uint8], x0: float, y1: float, cell: float,
    geo_mark: Any, tfil: Path | None, hus_kod: int,
) -> tuple[NDArray[np.uint8], NDArray[np.bool_], dict[str, Any], list[str]]:
    """Valfritt trädlager (K47 `[[valfritt_lager]]`): träd som noder även på öppen mark. Returnerar
    (klassraster med skivor, undantag från minsta area, redovisning för ekologi.json, varningar)."""
    from ekologi import EkologiFel
    from ekologi.tradlager import (
        TradFel,
        filtrera_trad,
        klipp_punkter,
        lagg_tradnoder,
        las_tradlager,
        las_tradpunkter,
    )

    lager = next((v for v in nv.valfria_lager if v.trad is not None), None)
    if lager is None or lager.trad is None:
        raise LosarFel(f"{namn}: nätverket {nv.natverk_id} har inget byggt trädlager.")
    kf = lager.trad
    if tfil is None and (geo_mark is None or geo_mark.tradpunkter is None):
        raise LosarFel(
            f"{namn}: anvand_trad kräver indata 'tradpunkter' (GeoPackage, Shapefile, GeoJSON eller "
            "tradpunkter.csv) eller ett trädlager i geografin (lager.tradpunkter, K46)."
        )
    varn: list[str] = []
    try:
        if tfil is not None:
            las = (
                las_tradlager(tfil) if Path(tfil).suffix.lower() == ".csv" else las_tradpunkter(tfil, kf.falt)
            )
            ursprung = {"roll": "tradpunkter", "fil_sha256": las.fil_sha256}
            punkter = las.punkter
            varn += list(las.varningar)
            antal_filen = las.antal_i_filen
        else:
            punkter = geo_mark.tradpunkter
            ursprung = {"roll": "geografi", "lager_sha256": geo_mark.trad_sha256}
            antal_filen = len(punkter)
        ytter = (x0, y1 - klasser.shape[0] * cell, x0 + klasser.shape[1] * cell, y1)
        i_utsnitt = klipp_punkter(tuple(punkter), ytter)
        urval = filtrera_trad(i_utsnitt, kf.filter)
        varn += urval.varningar
        nod = lagg_tradnoder(
            klasser, urval.punkter, x_min_m=x0, y_max_m=y1, cell_m=cell, nod=kf.nod, forbjudna_extra=(hus_kod,)
        )  # ritade hus (K9) ligger redan som byggnad i klasserna: ett nytt hus tar bort noden där det står
    except (TradFel, EkologiFel) as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    if nod.antal_utan_nod:
        varn.append(
            f"{nod.antal_utan_nod} av {nod.antal_trad} träd gav ingen nod (skivan täcker bara byggnad, väg, "
            "vatten eller annan exploaterad yta, eller punkten ligger utanför markunderlaget)."
        )
    if not urval.punkter:
        varn.append("Trädlagret gav inga träd efter urvalet: inga trädnoder lades till.")
    antaganden = [
        f"Inventerade träd är noder även på öppen mark: skiva med radien {kf.nod.buffert_m:g} m "
        f"(Nacka buffrade punktträd 20 m, {kf.nod.buffert_belagd.sida}), lagd som NMD {kf.nod.klass}. "
        "Klassen och undantaget från minsta fläckarea är Flodens tolkning (antagande).",
        "Trädens ålder och hålighet ingår inte: resultatet är potentiell livsmiljö, okänd kvalitet.",
    ]
    if kf.filter.tradslag:
        antaganden.append("Urvalet av ädla lövträd (trädslag) är Flodens avgränsning (antagande), inte ur källan.")
    dok = {
        "id": lager.lager_id,
        "status": "använt",
        "ursprung": ursprung,
        "antal_i_filen": antal_filen,
        "antal_i_utsnittet": len(i_utsnitt),
        "antal_efter_urval": len(urval.punkter),
        "bortfiltrerade_tradslag": urval.bortfiltrerade_tradslag,
        "bortfiltrerade_matt": urval.bortfiltrerade_matt,
        "saknar_matt_och_sorterades_bort": urval.saknar_matt,
        "antal_utan_nod": nod.antal_utan_nod,
        "antal_celler_nod": nod.antal_celler_nod,
        "antal_celler_andrade": nod.antal_celler_andrade,
        "buffert_m": kf.nod.buffert_m,
        "klass": kf.nod.klass,
        "undantar_min_area": kf.nod.undantar_min_area,
        "urval": {
            "tradslag": list(kf.filter.tradslag),
            "min_stamomkrets_cm": kf.filter.min_stamomkrets_cm,
            "min_diameter_cm": kf.filter.min_diameter_cm,
        },
        "kalla": {"buffert": kf.nod.buffert_belagd.som_dict(), "nod": kf.nod.belagd.som_dict(), "urval": kf.filter.belagd.som_dict()},
        "sparade_uppgifter": ["punkt", "tradslag", "stamomkrets/diameter"],
        "antaganden": antaganden,
    }
    return nod.klasser, nod.undantag_min_area, dok, varn


def _las_klassraster(fil: Path) -> Any:
    """Lokal klassraster (uint8, EPSG:3011) för biotopnätverk; samma format som nmd.tif."""
    from data_inlasning.nmd import NmdFel, NmdLager
    from testsvit.k1_io import las_geotiff

    arr, geo = las_geotiff(fil)
    if geo.epsg != 3011 or arr.dtype != np.uint8:
        raise NmdFel(f"{Path(fil).name} ska vara uint8 i EPSG:3011.")
    return NmdLager(np.asarray(arr, dtype=np.uint8), geo.x_min, geo.y_max, geo.pixelstorlek_x)


def _omsampla(lager: Any, cell: float) -> NDArray[np.uint8]:
    """Närmaste granne till ny cellstorlek över samma utbredning (klasskoder interpoleras aldrig)."""
    ny = max(1, int(round(lager.klasser.shape[0] * lager.cell_m / cell)))
    nx = max(1, int(round(lager.klasser.shape[1] * lager.cell_m / cell)))
    e = (np.arange(nx) + 0.5) * cell
    n = (np.arange(ny) + 0.5) * cell
    kol = np.minimum((e / lager.cell_m).astype(int), lager.klasser.shape[1] - 1)
    rad = np.minimum((n / lager.cell_m).astype(int), lager.klasser.shape[0] - 1)
    return np.asarray(lager.klasser[np.ix_(rad, kol)], dtype=np.uint8)


LOSARE: list[Analyslosare] = [Konnektivitet()]
