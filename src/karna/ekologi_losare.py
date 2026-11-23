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
STANDARD_MAX_FLACKAR = 40
STANDARD_MAX_CELLER = 250_000
STANDARD_SPRIDNING_FAKTOR = (0.8, 1.25)
"""Spridningsavståndets faktorer för intervallet (kärnans standard)."""
_PARAMETRAR = {
    "artprofil",
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
        "dPC per habitatfläck, strömtäthet (flaskhalsar) och känslighet. Parametrarna artprofil, "
        "cellstorlek_m, max_flackar och max_celler."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("artprofil",)
    utdataroller: ClassVar[tuple[str, ...]] = ("stromtathet", "kansighet", "ekologi")

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        from ekologi import STANDARD_ARTPROFILER

        okanda = sorted(set(parametrar) - _PARAMETRAR)
        if okanda:
            raise LosarFel(f"{self.namn}: okända parametrar {okanda}.")
        if "markslagsmappning_sha256" in parametrar:
            raise LosarFel(f"{self.namn}: 'markslagsmappning_sha256' sätts av lösaren.")
        profil = parametrar.get("artprofil")
        if profil is not None and profil not in STANDARD_ARTPROFILER:
            raise LosarFel(
                f"{self.namn}: okänd artprofil {profil!r}. Giltiga: "
                f"{', '.join(sorted(STANDARD_ARTPROFILER))} (eller indata 'artprofil', K44)."
            )
        spr = _intervall(parametrar, "spridning_faktor_intervall")
        return {
            "artprofil": profil,
            "cellstorlek_m": _tal(parametrar, "cellstorlek_m", STANDARD_CELLSTORLEK_M, over=0.0),
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
        fil_profil = k.indata.get("artprofil")
        if (p["artprofil"] is None) == (fil_profil is None):
            raise LosarFel(
                f"{self.namn}: ange exakt en av parametern 'artprofil' och indata 'artprofil'."
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


LOSARE: list[Analyslosare] = [Konnektivitet()]
