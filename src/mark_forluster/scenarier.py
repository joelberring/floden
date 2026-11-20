"""K5/v1-scenarier, sammanslagna mark-/brunnsförluster per cell och nod samt känslighet."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from mark_forluster.brunnar import (
    Brunn,
    BrunnsResultat,
    DelomradeKapacitet,
    berakna_brunnsavbordning,
)
from mark_forluster.grundvatten import (
    GILTIGA_GRUNDVATTENLAGEN,
    INSTROMNING,
    UTSTROMNING,
    berakna_grundvattentak,
    klassificera_in_utstromning,
)
from mark_forluster.infiltration import berakna_cell_infiltration
from mark_forluster.parametrar import (
    ForlustFel,
    JordartParametrar,
    hamta_jordart,
    normalisera_jordart,
)

KONTRAKT_K5 = "K5/v1"
GILTIGA_FORLUSTMODELLER: frozenset[str] = frozenset({"green-ampt"})


@dataclass(frozen=True)
class ForlustInstallningar:
    """Delobjektet `forluster` i kontrakt K5/v1."""

    modell: str = "green-ampt"
    initial_mm: float = 2.0

    def __post_init__(self) -> None:
        norm_modell = self.modell.strip().lower()
        if norm_modell not in GILTIGA_FORLUSTMODELLER:
            tillatna = ", ".join(sorted(GILTIGA_FORLUSTMODELLER))
            raise ForlustFel(
                f"Okänd förlustmodell '{self.modell}'. Giltiga: {tillatna}."
            )
        if not math.isfinite(self.initial_mm) or self.initial_mm < 0.0:
            raise ForlustFel(f"initial_mm måste vara ändlig och >= 0, fick {self.initial_mm}.")
        object.__setattr__(self, "modell", norm_modell)
        object.__setattr__(self, "initial_mm", float(self.initial_mm))

    def till_dict(self) -> dict[str, Any]:
        """Returnerar JSON-serialiserbart lexikon."""
        return {"modell": self.modell, "initial_mm": self.initial_mm}

    @classmethod
    def fran_dict(cls, data: Any) -> ForlustInstallningar:
        """Skapar `ForlustInstallningar` från ett dict och validerar fält."""
        if not isinstance(data, dict):
            raise ForlustFel("Fältet 'forluster' måste vara ett objekt.")
        if "modell" not in data or "initial_mm" not in data:
            raise ForlustFel("Fältet 'forluster' måste innehålla 'modell' och 'initial_mm'.")
        modell = data["modell"]
        initial = data["initial_mm"]
        if not isinstance(modell, str):
            raise ForlustFel("forluster.modell måste vara en sträng.")
        if isinstance(initial, bool) or not isinstance(initial, int | float):
            raise ForlustFel("forluster.initial_mm måste vara ett tal.")
        return cls(modell=modell, initial_mm=float(initial))


@dataclass(frozen=True)
class ScenarioK5:
    """Scenario enligt kontrakt K5/v1 (`docs/DATAKONTRAKT.md`)."""

    regn: str
    forluster: ForlustInstallningar = field(default_factory=ForlustInstallningar)
    grundvatten: str = "normal"
    tjale: bool = False
    brunnar_igensatta_andel: float = 0.0
    ledningar_fulla_fran_start: bool = False
    slumpfro: int = 42
    kontrakt: str = KONTRAKT_K5

    def __post_init__(self) -> None:
        if self.kontrakt != KONTRAKT_K5:
            raise ForlustFel(
                f"Ogiltigt scenariokontrakt '{self.kontrakt}', förväntade '{KONTRAKT_K5}'."
            )
        if not isinstance(self.regn, str) or not self.regn.strip():
            raise ForlustFel("ScenarioK5.regn måste vara en icke-tom sträng.")
        if not isinstance(self.forluster, ForlustInstallningar):
            raise ForlustFel("ScenarioK5.forluster måste vara en ForlustInstallningar.")
        if not isinstance(self.grundvatten, str) or not self.grundvatten.strip():
            raise ForlustFel("ScenarioK5.grundvatten måste vara en icke-tom sträng.")

        gw_norm = self.grundvatten.strip().lower()
        # Antingen 'normal', 'hog', eller en explicit filsökväg (icke-tom sträng)
        if gw_norm in GILTIGA_GRUNDVATTENLAGEN:
            object.__setattr__(self, "grundvatten", gw_norm)
        elif (
            "/" not in self.grundvatten
            and "\\" not in self.grundvatten
            and "." not in self.grundvatten
        ):
            raise ForlustFel(
                f"Ogiltigt grundvattenläge '{self.grundvatten}'. "
                "Ange 'normal', 'hog' eller en filsökväg."
            )

        if not isinstance(self.tjale, bool):
            raise ForlustFel("ScenarioK5.tjale måste vara en bool.")
        if isinstance(self.brunnar_igensatta_andel, bool) or not (
            math.isfinite(self.brunnar_igensatta_andel)
            and 0.0 <= self.brunnar_igensatta_andel <= 1.0
        ):
            raise ForlustFel(
                f"brunnar_igensatta_andel måste ligga i [0, 1], "
                f"fick {self.brunnar_igensatta_andel}."
            )
        if not isinstance(self.ledningar_fulla_fran_start, bool):
            raise ForlustFel("ledningar_fulla_fran_start måste vara en bool.")
        if isinstance(self.slumpfro, bool) or not isinstance(self.slumpfro, int):
            raise ForlustFel("slumpfro måste vara ett heltal.")

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar scenariot till ett K5/v1-lexikon."""
        return {
            "kontrakt": self.kontrakt,
            "regn": self.regn,
            "forluster": self.forluster.till_dict(),
            "grundvatten": self.grundvatten,
            "tjale": self.tjale,
            "brunnar_igensatta_andel": float(self.brunnar_igensatta_andel),
            "ledningar_fulla_fran_start": self.ledningar_fulla_fran_start,
            "slumpfro": self.slumpfro,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> ScenarioK5:
        """Läser och validerar ett K5/v1-lexikon."""
        if not isinstance(data, dict):
            raise ForlustFel("Scenario K5 måste vara ett JSON-objekt.")
        obligatoriska = (
            "kontrakt",
            "regn",
            "forluster",
            "grundvatten",
            "tjale",
            "brunnar_igensatta_andel",
            "ledningar_fulla_fran_start",
            "slumpfro",
        )
        for falt in obligatoriska:
            if falt not in data:
                raise ForlustFel(f"Scenario K5 saknar obligatoriskt fält '{falt}'.")

        igensatt = data["brunnar_igensatta_andel"]
        if isinstance(igensatt, bool) or not isinstance(igensatt, int | float):
            raise ForlustFel("brunnar_igensatta_andel måste vara ett tal.")

        return cls(
            kontrakt=str(data["kontrakt"]),
            regn=data["regn"],
            forluster=ForlustInstallningar.fran_dict(data["forluster"]),
            grundvatten=data["grundvatten"],
            tjale=data["tjale"],
            brunnar_igensatta_andel=float(igensatt),
            ledningar_fulla_fran_start=data["ledningar_fulla_fran_start"],
            slumpfro=data["slumpfro"],
        )


def las_scenario_k5(sokvag: str | Path) -> ScenarioK5:
    """Läser en K5/v1-scenariofil från JSON."""
    fil = Path(sokvag)
    try:
        text = fil.read_text(encoding="utf-8")
    except OSError as exc:
        raise ForlustFel(f"Kunde inte läsa scenariofilen {fil}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ForlustFel(f"Ogiltig JSON i scenariofilen {fil}: {exc}") from exc
    return ScenarioK5.fran_dict(data)


def skriv_scenario_k5(scenario: ScenarioK5, sokvag: str | Path) -> Path:
    """Skriver ett K5/v1-scenario som formaterad JSON."""
    fil = Path(sokvag)
    fil.parent.mkdir(parents=True, exist_ok=True)
    fil.write_text(
        json.dumps(scenario.till_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return fil


@dataclass(frozen=True)
class NodForlust:
    """Aggregerade förluster och nettovolym för en nod i sänkgrafen."""

    nod_id: int
    antal_celler: int
    area_m2: float
    regn_volym_m3: float
    mark_forlust_m3: float
    brunns_avbordning_m3: float
    total_forlust_m3: float
    netto_avrinning_m3: float
    medel_forlust_m: float
    medel_netto_m: float


@dataclass(frozen=True)
class ForlustResultat:
    """Samlade förluster per cell (raster) och per nod för ett K5-scenario."""

    scenario: ScenarioK5
    regn_totalt_m: float
    initial_forlust_m: NDArray[np.float32]
    infiltration_m: NDArray[np.float32]
    mark_forlust_m: NDArray[np.float32]
    brunns_avdrag_m: NDArray[np.float32]
    total_forlust_m: NDArray[np.float32]
    netto_regn_m: NDArray[np.float32]
    in_utstromning: NDArray[np.uint8] | None
    brunns_resultat: BrunnsResultat
    nod_forluster: dict[int, NodForlust]
    max_massbalans_fel_m: float


@dataclass(frozen=True)
class ScenarioSammanfattning:
    """Sammanfattande mätvärden för jämförelse mellan scenarier (känslighetsanalys)."""

    namn: str
    regn_volym_m3: float
    mark_forlust_m3: float
    brunns_avbordning_m3: float
    total_forlust_m3: float
    netto_avrinning_m3: float
    netto_andel: float
    utstromning_andel: float | None


def _bygg_porositetsraster(
    jordart: NDArray[np.uint8] | NDArray[np.str_] | str | int,
    form: tuple[int, int],
    tabell: dict[str, JordartParametrar] | None = None,
) -> NDArray[np.float64]:
    """Returnerar effektiv porositet per cell i `form` utifrån jordart."""
    if isinstance(jordart, str | int) and not isinstance(jordart, bool):
        jord = hamta_jordart(jordart, tabell=tabell)
        return np.full(form, jord.porositet, dtype=np.float64)

    ja_arr = np.asarray(jordart)
    if ja_arr.shape != form:
        raise ForlustFel(f"jordart har form {ja_arr.shape}, förväntade {form}.")

    por = np.zeros(form, dtype=np.float64)
    ja_flat = ja_arr.ravel()
    por_flat = por.ravel()
    for unikt in np.unique(ja_flat):
        nyckel: str | int = (
            int(unikt) if np.issubdtype(ja_arr.dtype, np.integer) else str(unikt)
        )
        norm = normalisera_jordart(nyckel)
        jord = hamta_jordart(norm, tabell=tabell)
        por_flat[ja_flat == unikt] = jord.porositet
    return por


def _fordela_brunnar_pa_celler(
    brunnar: Sequence[Brunn],
    brunns_res: BrunnsResultat,
    form: tuple[int, int],
    e_min_m: float | None,
    n_max_m: float | None,
    pixelstorlek_m: float,
    tillganglig_avrinning_m3: NDArray[np.float64],
) -> tuple[NDArray[np.float64], dict[tuple[int, int], float]]:
    """Fördelar brunnarnas bortledda volym till motsvarande rasterceller.

    En brunns bortledda volym begränsas så att den aldrig tar mer vatten än vad
    som finns tillgängligt (brunnsavdraget begränsas i första hand per brunnscell,
    men vid punktbrunnar som representerar sin delavrinningsyta aggregeras
    avdraget i nodresultatet mot nodens totala avrinning; på cellnivå lagras den
    faktiskt borttagna volymen i cellen, eller motsvarande fördelning).
    """
    rader, kolumner = form
    cell_area_m2 = pixelstorlek_m * pixelstorlek_m
    brunns_m = np.zeros(form, dtype=np.float64)
    utanfor_volym: dict[tuple[int, int], float] = {}

    if not brunnar:
        return brunns_m, utanfor_volym

    if e_min_m is None or n_max_m is None:
        # Om inga geokoordinater anges fördelas total brunnskapacitet jämnt över området
        # begränsat av tillgänglig ytavrinning i varje cell.
        total_tillg = float(np.sum(tillganglig_avrinning_m3))
        if total_tillg <= 0.0:
            return brunns_m, utanfor_volym
        bortledd = min(brunns_res.total_bortledd_volym_m3, total_tillg)
        andel = bortledd / total_tillg
        brunns_m = (tillganglig_avrinning_m3 * andel) / cell_area_m2
        return brunns_m, utanfor_volym

    for brunn in brunnar:
        kol = int(math.floor((brunn.e_m - e_min_m) / pixelstorlek_m))
        rad = int(math.floor((n_max_m - brunn.n_m) / pixelstorlek_m))
        if not (0 <= rad < rader and 0 <= kol < kolumner):
            raise ForlustFel(
                f"Brunn '{brunn.brunn_id}' ligger utanför rastret (rad={rad}, kol={kol})."
            )
        vol = brunns_res.bortledd_volym_m3[brunn.brunn_id]
        utanfor_volym[(rad, kol)] = utanfor_volym.get((rad, kol), 0.0) + vol

    # På cellnivå: begränsa varje enskild cells avdrag till cellens egen tillgängliga
    # avrinning om ingen nodkarta finns; resterande brunnsvolym tillgodoräknas på
    # nodnivå (där flera celler rinner till samma brunn/nod).
    for (rad, kol), vol_m3 in utanfor_volym.items():
        tillg_cell_m3 = float(tillganglig_avrinning_m3[rad, kol])
        avdrag_cell_m3 = min(vol_m3, tillg_cell_m3)
        brunns_m[rad, kol] += avdrag_cell_m3 / cell_area_m2

    return brunns_m, utanfor_volym


def berakna_forluster_for_scenario(
    scenario: ScenarioK5,
    markslag: NDArray[np.uint8],
    jordart: NDArray[np.uint8] | NDArray[np.str_] | str | int,
    regn_tid_s: NDArray[np.float64] | list[float] | tuple[float, ...],
    regn_intensitet_mm_h: NDArray[np.float64] | list[float] | tuple[float, ...],
    *,
    pixelstorlek_m: float = 0.25,
    dtw_m: NDArray[np.float32] | NDArray[np.float64] | None = None,
    brunnar: Sequence[Brunn] = (),
    delomraden: Sequence[DelomradeKapacitet] | None = None,
    cell_till_nod: NDArray[np.int64] | None = None,
    e_min_m: float | None = None,
    n_max_m: float | None = None,
    hog_gw_faktor: float = 0.5,
    utstromning_grans_m: float = 0.5,
    tabell: dict[str, JordartParametrar] | None = None,
) -> ForlustResultat:
    """Beräknar markförluster, grundvattentak och brunnsavbördning för ett K5-scenario.

    Args:
        scenario: Validerat `ScenarioK5`-objekt.
        markslag: 2D `uint8`-raster med K1-markslagskoder (`1..9`).
        jordart: 2D-raster med SGU-jordartsklasser eller en enhetlig sträng/kod.
        regn_tid_s: Tidpunkter i sekunder (strikt ökande).
        regn_intensitet_mm_h: Regnintensitet i mm/h.
        pixelstorlek_m: Cellstorlek i meter (standard 0,25 m enligt K1).
        dtw_m: Valfritt 2D-raster med djup till grundvattenyta (m). Om angivet beräknas
            grundvattentak och in-/utströmningskarta.
        brunnar: Sekvens av brunnar i området (syntetiska i tester).
        delomraden: Eventuella maxkapaciteter i ledningsnätet per delområde.
        cell_till_nod: Valfritt 2D `int64`-raster enligt K2 med `nod_id` per cell
            (`<= 0` eller `< 0` behandlas som utanför noderna; negativa ignoreras).
        e_min_m: Vänsterkant i EPSG:3011 för geokoppling av brunnar.
        n_max_m: Överkant i EPSG:3011 för geokoppling av brunnar.
        hog_gw_faktor: Multiplikator för DTW vid `scenario.grundvatten == "hog"`.
        utstromning_grans_m: DTW-gräns (m) för klassificering som utströmningsområde.
        tabell: Valfri alternativ jordartstabell.

    Returns:
        `ForlustResultat` med rasterskikt per cell och aggregering per nod.
    """
    if not isinstance(scenario, ScenarioK5):
        raise ForlustFel("scenario måste vara ett ScenarioK5-objekt.")
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise ForlustFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")

    ms = np.asarray(markslag)
    if ms.ndim != 2 or ms.size == 0:
        raise ForlustFel(f"markslag måste vara ett icke-tomt 2D-raster, fick {ms.shape}.")

    gw_norm = scenario.grundvatten.strip().lower()
    if gw_norm not in GILTIGA_GRUNDVATTENLAGEN and dtw_m is None:
        raise ForlustFel(
            f"Scenario anger grundvattenfil '{scenario.grundvatten}' men inget dtw_m-raster "
            "skickades in."
        )

    hog_markfukt = gw_norm == "hog"

    gw_tak: NDArray[np.float32] | None = None
    in_ut: NDArray[np.uint8] | None = None
    if dtw_m is not None:
        dtw_arr = np.asarray(dtw_m, dtype=np.float64)
        if dtw_arr.shape != ms.shape:
            raise ForlustFel(
                f"dtw_m har form {dtw_arr.shape}, förväntade {ms.shape}."
            )
        eff_dtw = dtw_arr * hog_gw_faktor if gw_norm == "hog" else dtw_arr
        por_arr = _bygg_porositetsraster(jordart, ms.shape, tabell=tabell)
        gw_tak = berakna_grundvattentak(
            eff_dtw,
            por_arr,
            lage="normal",  # redan skalad i eff_dtw för konsekvent in_ut-karta
        )
        in_ut = klassificera_in_utstromning(eff_dtw, utstromning_grans_m=utstromning_grans_m)
        # I utströmningsområden med DTW == 0 blir taket 0 automatiskt; extra säkerhet:
        _ = (INSTROMNING, UTSTROMNING)

    infil_res = berakna_cell_infiltration(
        ms,
        jordart,
        regn_tid_s,
        regn_intensitet_mm_h,
        initial_mm=scenario.forluster.initial_mm,
        tjale=scenario.tjale,
        hog_markfukt=hog_markfukt,
        grundvatten_tak_m=gw_tak,
        tabell=tabell,
    )

    t_arr = np.asarray(regn_tid_s, dtype=np.float64)
    varaktighet_s = float(t_arr[-1] - t_arr[0])
    brunns_res = berakna_brunnsavbordning(
        brunnar,
        varaktighet_s,
        igensatta_andel=scenario.brunnar_igensatta_andel,
        ledningar_fulla=scenario.ledningar_fulla_fran_start,
        delomraden=delomraden,
    )

    cell_area_m2 = pixelstorlek_m * pixelstorlek_m
    netto_fore_brunn_m = np.asarray(infil_res.netto_regn_m, dtype=np.float64)
    tillganglig_avrinning_m3 = netto_fore_brunn_m * cell_area_m2

    brunns_avdrag_m, brunn_cell_volym = _fordela_brunnar_pa_celler(
        brunnar,
        brunns_res,
        ms.shape,
        e_min_m=e_min_m,
        n_max_m=n_max_m,
        pixelstorlek_m=pixelstorlek_m,
        tillganglig_avrinning_m3=tillganglig_avrinning_m3,
    )

    netto_efter_brunn_m = np.maximum(0.0, netto_fore_brunn_m - brunns_avdrag_m)
    mark_forlust_m = np.asarray(infil_res.total_forlust_m, dtype=np.float64)
    total_forlust_m = mark_forlust_m + brunns_avdrag_m

    max_fel = float(
        np.max(np.abs(infil_res.regn_totalt_m - (total_forlust_m + netto_efter_brunn_m)))
    )

    nod_forluster: dict[int, NodForlust] = {}
    if cell_till_nod is not None:
        nod_arr = np.asarray(cell_till_nod)
        if nod_arr.shape != ms.shape:
            raise ForlustFel(
                f"cell_till_nod har form {nod_arr.shape}, förväntade {ms.shape}."
            )
        if not np.issubdtype(nod_arr.dtype, np.integer):
            raise ForlustFel(f"cell_till_nod måste ha heltalstyp, fick {nod_arr.dtype}.")

        # Samla brunnarnas totala bortledningspotential per nod via brunnens cell
        brunn_potential_per_nod: dict[int, float] = {}
        if brunnar:
            if e_min_m is not None and n_max_m is not None:
                for (r_b, c_b), vol_m3 in brunn_cell_volym.items():
                    nid = int(nod_arr[r_b, c_b])
                    if nid >= 0:
                        brunn_potential_per_nod[nid] = (
                            brunn_potential_per_nod.get(nid, 0.0) + vol_m3
                        )
            else:
                # Fördela brunnar proportionellt mot varje nods andel av tillgänglig avrinning
                giltig_mask = nod_arr >= 0
                total_tillg_nod = float(np.sum(tillganglig_avrinning_m3[giltig_mask]))
                if total_tillg_nod > 0.0:
                    for nid_np in np.unique(nod_arr[giltig_mask]):
                        nid = int(nid_np)
                        nod_tillg = float(
                            np.sum(tillganglig_avrinning_m3[nod_arr == nid])
                        )
                        brunn_potential_per_nod[nid] = (
                            brunns_res.total_bortledd_volym_m3 * (nod_tillg / total_tillg_nod)
                        )

        unika_noder = np.unique(nod_arr)
        for nid_np in unika_noder:
            nid = int(nid_np)
            if nid < 0:
                continue
            n_mask = nod_arr == nid
            antal = int(np.sum(n_mask))
            area = antal * cell_area_m2
            regn_vol = area * infil_res.regn_totalt_m
            mark_vol = float(np.sum(mark_forlust_m[n_mask])) * cell_area_m2
            tillg_nod_vol = float(np.sum(tillganglig_avrinning_m3[n_mask]))
            brunn_pot = brunn_potential_per_nod.get(nid, 0.0)
            brunn_vol = min(brunn_pot, tillg_nod_vol)
            tot_forlust_vol = mark_vol + brunn_vol
            netto_vol = max(0.0, tillg_nod_vol - brunn_vol)

            nod_forluster[nid] = NodForlust(
                nod_id=nid,
                antal_celler=antal,
                area_m2=area,
                regn_volym_m3=regn_vol,
                mark_forlust_m3=mark_vol,
                brunns_avbordning_m3=brunn_vol,
                total_forlust_m3=tot_forlust_vol,
                netto_avrinning_m3=netto_vol,
                medel_forlust_m=tot_forlust_vol / area,
                medel_netto_m=netto_vol / area,
            )

    return ForlustResultat(
        scenario=scenario,
        regn_totalt_m=infil_res.regn_totalt_m,
        initial_forlust_m=infil_res.initial_forlust_m,
        infiltration_m=infil_res.infiltration_m,
        mark_forlust_m=mark_forlust_m.astype(np.float32),
        brunns_avdrag_m=brunns_avdrag_m.astype(np.float32),
        total_forlust_m=total_forlust_m.astype(np.float32),
        netto_regn_m=netto_efter_brunn_m.astype(np.float32),
        in_utstromning=in_ut,
        brunns_resultat=brunns_res,
        nod_forluster=nod_forluster,
        max_massbalans_fel_m=max_fel,
    )


def jamfor_scenarier(
    scenarier: Sequence[tuple[str, ScenarioK5]],
    markslag: NDArray[np.uint8],
    jordart: NDArray[np.uint8] | NDArray[np.str_] | str | int,
    regn_tid_s: NDArray[np.float64] | list[float] | tuple[float, ...],
    regn_intensitet_mm_h: NDArray[np.float64] | list[float] | tuple[float, ...],
    *,
    pixelstorlek_m: float = 0.25,
    dtw_m: NDArray[np.float32] | NDArray[np.float64] | None = None,
    brunnar: Sequence[Brunn] = (),
    delomraden: Sequence[DelomradeKapacitet] | None = None,
    cell_till_nod: NDArray[np.int64] | None = None,
    e_min_m: float | None = None,
    n_max_m: float | None = None,
    hog_gw_faktor: float = 0.5,
    utstromning_grans_m: float = 0.5,
    tabell: dict[str, JordartParametrar] | None = None,
) -> list[ScenarioSammanfattning]:
    """Kör flera K5-scenarier på samma område och returnerar en känslighetstabell."""
    if not scenarier:
        raise ForlustFel("Minst ett scenario krävs för jämförelse.")

    ms = np.asarray(markslag)
    cell_area_m2 = pixelstorlek_m * pixelstorlek_m
    total_area_m2 = ms.size * cell_area_m2

    sammanfattningar: list[ScenarioSammanfattning] = []
    for namn, scenario in scenarier:
        if not isinstance(namn, str) or not namn.strip():
            raise ForlustFel("Varje scenario i jämförelsen måste ha ett icke-tomt namn.")
        res = berakna_forluster_for_scenario(
            scenario,
            ms,
            jordart,
            regn_tid_s,
            regn_intensitet_mm_h,
            pixelstorlek_m=pixelstorlek_m,
            dtw_m=dtw_m,
            brunnar=brunnar,
            delomraden=delomraden,
            cell_till_nod=cell_till_nod,
            e_min_m=e_min_m,
            n_max_m=n_max_m,
            hog_gw_faktor=hog_gw_faktor,
            utstromning_grans_m=utstromning_grans_m,
            tabell=tabell,
        )

        regn_vol = total_area_m2 * res.regn_totalt_m
        mark_vol = float(np.sum(res.mark_forlust_m, dtype=np.float64)) * cell_area_m2
        if res.nod_forluster:
            brunn_vol = sum(n.brunns_avbordning_m3 for n in res.nod_forluster.values())
            netto_vol = max(0.0, regn_vol - mark_vol - brunn_vol)
            tot_forlust = mark_vol + brunn_vol
        else:
            brunn_vol = float(np.sum(res.brunns_avdrag_m, dtype=np.float64)) * cell_area_m2
            tot_forlust = float(np.sum(res.total_forlust_m, dtype=np.float64)) * cell_area_m2
            netto_vol = float(np.sum(res.netto_regn_m, dtype=np.float64)) * cell_area_m2

        netto_andel = 0.0 if regn_vol <= 0.0 else netto_vol / regn_vol
        ut_andel: float | None = None
        if res.in_utstromning is not None:
            ut_andel = float(np.mean(res.in_utstromning == UTSTROMNING))

        sammanfattningar.append(
            ScenarioSammanfattning(
                namn=namn,
                regn_volym_m3=regn_vol,
                mark_forlust_m3=mark_vol,
                brunns_avbordning_m3=brunn_vol,
                total_forlust_m3=tot_forlust,
                netto_avrinning_m3=netto_vol,
                netto_andel=netto_andel,
                utstromning_andel=ut_andel,
            )
        )

    return sammanfattningar
