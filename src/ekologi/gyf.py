"""Svensk Grönytefaktor (GYF) och delfaktoranalys för kvartersmark (`src/ekologi/gyf.py`).

Deterministisk beräkning i SWEREF 99 18 00 (EPSG:3011) med SI-enheter (`_m`, `_m2`, `_pct`):

    GYF = (sum_i A_i * f_i + sum_j T_j * g_j) / A_tomt

där:
- `A_i` är arean (m²) för grundytans delfaktor `i` med delfaktorvikt `f_i >= 0`,
- `T_j` är arean (m²) för tilläggsfaktor `j` (t.ex. träd/krontäckning, småbiotop) med vikt `g_j`,
- `A_tomt` är tomtens/kvarterets totala area (m²).

Samtliga kommunprofiler i `data/gyf/profiler.toml` bär `status = "skiss"` tills
vikterna har granskats och godkänts. Inga tysta fel: okända K1-markslagskoder (inklusive
K1-kod 0 = okänt) samt `NaN`/ogiltiga celler inom `tomt_mask` gissas aldrig utan redovisas
uttryckligen i `flaggad_area_m2`, `okanda_koder`, `ogiltiga_celler` och `varningar`.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from ekologi.typer import EkologiFel
from gemensamt.hash import sha256_fil

FORMAT_GYF_PROFILER: Final[str] = "floden-gyf-profiler/v1"
FORMAT_GYF_RESULTAT: Final[str] = "floden-gyf-resultat/v1"
FORMAT_GYF_JAMFORELSE: Final[str] = "floden-gyf-jamforelse/v1"
KOORDINATSYSTEM_EPSG: Final[str] = "EPSG:3011"

PROFILER_FIL: Final[Path] = Path(__file__).resolve().parents[2] / "data" / "gyf" / "profiler.toml"

# Lokalt dokumenterat uppslag av K1 v2-markslagskoder (docs/DATAKONTRAKT.md, data/markklasser.toml).
# `src/ekologi/` importerar aldrig `sankgraf` eller andra domänpaket (test_paketgranser.py).
K1_MARKSLAG_OKANT: Final[int] = 0
K1_MARKSLAG_HARDGJORT: Final[int] = 1
K1_MARKSLAG_TAK: Final[int] = 2
K1_MARKSLAG_GRONT_TAK: Final[int] = 3
K1_MARKSLAG_GRAS: Final[int] = 4
K1_MARKSLAG_SKOG: Final[int] = 5
K1_MARKSLAG_BERG: Final[int] = 6
K1_MARKSLAG_GRUS: Final[int] = 7
K1_MARKSLAG_VATTEN: Final[int] = 8
K1_MARKSLAG_BYGGNAD: Final[int] = 9

K1_MARKSLAG_NAMN: Final[Mapping[int, str]] = {
    K1_MARKSLAG_OKANT: "okänt",
    K1_MARKSLAG_HARDGJORT: "hårdgjort",
    K1_MARKSLAG_TAK: "tak",
    K1_MARKSLAG_GRONT_TAK: "grönt tak",
    K1_MARKSLAG_GRAS: "gräs",
    K1_MARKSLAG_SKOG: "skog",
    K1_MARKSLAG_BERG: "berg i dagen",
    K1_MARKSLAG_GRUS: "grus",
    K1_MARKSLAG_VATTEN: "vatten",
    K1_MARKSLAG_BYGGNAD: "byggnad",
}

STANDARD_OVERLAGRING_PRIORITET: Final[tuple[str, ...]] = (
    "regnbadd",
    "vattenyta",
    "vaxtbadd_bjalklag",
    "gront_tak",
    "genomslapplig",
    "skog_naturmark",
    "vegetation_mark",
    "berg_i_dagen",
    "hardgjord",
    "byggnad_tak",
)

_MAL_TOLERANS: Final[float] = 1e-12


def _validera_andligt_tal(varde: Any, namn: str, *, strikt_positivt: bool = False) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise EkologiFel(f"GYF: '{namn}' måste vara ett tal, fick {varde!r}.")
    tal = float(varde)
    if not math.isfinite(tal):
        raise EkologiFel(f"GYF: '{namn}' måste vara ändligt, fick {varde!r}.")
    if strikt_positivt and tal <= 0.0:
        raise EkologiFel(f"GYF: '{namn}' måste vara > 0, fick {tal}.")
    if not strikt_positivt and tal < 0.0:
        raise EkologiFel(f"GYF: '{namn}' måste vara >= 0, fick {tal}.")
    return tal


def _validera_text(varde: Any, namn: str, *, tillat_tom: bool = False) -> str:
    if not isinstance(varde, str):
        raise EkologiFel(f"GYF: '{namn}' måste vara text, fick {type(varde).__name__}.")
    if not tillat_tom and not varde.strip():
        raise EkologiFel(f"GYF: '{namn}' får inte vara tom.")
    return varde


def _validera_bool_mask(mask: NDArray[Any], form: tuple[int, int], namn: str) -> NDArray[np.bool_]:
    arr = np.asarray(mask)
    if arr.ndim != 2 or arr.shape != form:
        raise EkologiFel(
            f"GYF: '{namn}' måste vara en 2D-mask med form {form}, fick {arr.shape}."
        )
    if arr.dtype == np.bool_:
        return arr
    if np.issubdtype(arr.dtype, np.integer):
        if not bool(np.all((arr == 0) | (arr == 1))):
            raise EkologiFel(
                f"GYF: heltalsmask '{namn}' får bara innehålla 0 och 1."
            )
        return arr.astype(np.bool_)
    raise EkologiFel(
        f"GYF: '{namn}' måste ha boolesk dtype (eller heltals-0/1), fick {arr.dtype}."
    )


@dataclass(frozen=True)
class GyfDelfaktor:
    """En delfaktor (grundyta eller tilläggsfaktor) i en kommunal GYF-profil."""

    faktor_id: str
    namn: str
    faktor: float
    k1_koder: tuple[int, ...] = ()
    beskrivning: str = ""
    kalla: str = ""
    typ: str = "schablon"
    anmarkning: str = ""

    def __post_init__(self) -> None:
        _validera_text(self.faktor_id, "faktor_id")
        _validera_text(self.namn, "namn")
        _validera_andligt_tal(self.faktor, f"delfaktor '{self.faktor_id}'.faktor")
        for kod in self.k1_koder:
            if isinstance(kod, bool) or not isinstance(kod, int) or kod < 0:
                raise EkologiFel(
                    f"GYF: ogiltig K1-markslagskod {kod!r} i delfaktor '{self.faktor_id}'."
                )


@dataclass(frozen=True)
class GyfProfil:
    """Kommunal GYF-profil med riktmärke, delfaktorer, tilläggsfaktorer och källhänvisning."""

    profil_id: str
    namn: str
    kommun: str
    riktmarke: float
    status: str
    version: str
    beskrivning: str
    kalla: str
    kalla_url: str
    delfaktorer: Mapping[str, GyfDelfaktor]
    tillaggsfaktorer: Mapping[str, GyfDelfaktor]
    overlagring_prioritet: tuple[str, ...]
    anmarkning: str = ""
    profilfil_sha256: str = ""

    def __post_init__(self) -> None:
        _validera_text(self.profil_id, "profil_id")
        _validera_text(self.namn, "namn")
        _validera_text(self.kommun, "kommun")
        _validera_andligt_tal(self.riktmarke, "riktmarke", strikt_positivt=True)
        _validera_text(self.status, "status")
        _validera_text(self.version, "version")
        if not self.delfaktorer:
            raise EkologiFel(f"GYF-profil '{self.profil_id}' saknar delfaktorer.")

        sedda_koder: dict[int, str] = {}
        for fid, df in self.delfaktorer.items():
            if fid != df.faktor_id:
                raise EkologiFel(
                    f"GYF-profil '{self.profil_id}': nyckel '{fid}' matchar inte "
                    f"delfaktor.faktor_id '{df.faktor_id}'."
                )
            for kod in df.k1_koder:
                if kod in sedda_koder:
                    raise EkologiFel(
                        f"GYF-profil '{self.profil_id}': K1-kod {kod} mappar till både "
                        f"'{sedda_koder[kod]}' och '{fid}'."
                    )
                sedda_koder[kod] = fid

        for tid, tf in self.tillaggsfaktorer.items():
            if tid != tf.faktor_id:
                raise EkologiFel(
                    f"GYF-profil '{self.profil_id}': tilläggsnyckel '{tid}' matchar inte "
                    f"faktor_id '{tf.faktor_id}'."
                )

        prio_set = set(self.overlagring_prioritet)
        if len(prio_set) != len(self.overlagring_prioritet):
            raise EkologiFel(
                f"GYF-profil '{self.profil_id}': dubbletter i overlagring_prioritet."
            )
        okanda_prio = sorted(prio_set - set(self.delfaktorer))
        if okanda_prio:
            raise EkologiFel(
                f"GYF-profil '{self.profil_id}': okända delfaktorer i "
                f"overlagring_prioritet: {okanda_prio}."
            )

    def k1_kod_till_delfaktor(self) -> dict[int, GyfDelfaktor]:
        """Returnerar uppslag från K1-markslagskod till `GyfDelfaktor`."""
        karta: dict[int, GyfDelfaktor] = {}
        for df in self.delfaktorer.values():
            for kod in df.k1_koder:
                karta[kod] = df
        return karta

    def effektiv_overlagring_prioritet(self) -> tuple[str, ...]:
        """Deterministisk prioritetsordning för K50-överlagringar (högst prioritet först)."""
        ordnade = list(self.overlagring_prioritet)
        redan = set(ordnade)
        for fid in self.delfaktorer:
            if fid not in redan:
                ordnade.append(fid)
        return tuple(ordnade)


@dataclass(frozen=True)
class GyfTillagg:
    """Ett tilläggselement i GYF-beräkningen (t.ex. träd/krontäckning i m² eller småbiotop).

    Om `faktor` utelämnas (`None`) hämtas vikten från `profil.tillaggsfaktorer[tillagg_id]`.
    """

    tillagg_id: str
    area_m2: float
    faktor: float | None = None
    namn: str = ""
    beskrivning: str = ""

    def __post_init__(self) -> None:
        _validera_text(self.tillagg_id, "tillagg_id")
        _validera_andligt_tal(self.area_m2, f"tillagg '{self.tillagg_id}'.area_m2")
        if self.faktor is not None:
            _validera_andligt_tal(self.faktor, f"tillagg '{self.tillagg_id}'.faktor")


@dataclass(frozen=True)
class GyfDelfaktorUtfall:
    """Beräknad yta och ekoeffektivt bidrag för en enskild delfaktor i GYF."""

    faktor_id: str
    namn: str
    faktor: float
    antal_celler: int
    area_m2: float
    andel_av_tomt_pct: float
    eko_effektiv_area_m2: float
    bidrag_gyf: float


@dataclass(frozen=True)
class GyfTillaggUtfall:
    """Beräknad yta och ekoeffektivt bidrag för en tilläggsfaktor i GYF."""

    tillagg_id: str
    namn: str
    area_m2: float
    faktor: float
    andel_av_tomt_pct: float
    eko_effektiv_area_m2: float
    bidrag_gyf: float


@dataclass(frozen=True)
class GyfResultat:
    """Fullständigt deterministiskt GYF-resultat med delfaktoruppdelning och osäkerhetsflaggor."""

    profil_id: str
    profil_namn: str
    kommun: str
    metodstatus: str
    riktmarke: float
    cellstorlek_m: float
    tomt_celler: int
    tomt_area_m2: float
    grund_eko_effektiv_area_m2: float
    tillagg_eko_effektiv_area_m2: float
    eko_effektiv_area_m2: float
    gyf: float
    uppfyller_mal: bool
    saknas_till_mal_m2: float
    delfaktorer: tuple[GyfDelfaktorUtfall, ...]
    tillagg: tuple[GyfTillaggUtfall, ...]
    flaggade_celler: int
    flaggad_area_m2: float
    flaggad_andel_pct: float
    okanda_koder: tuple[int, ...]
    ogiltiga_celler: int
    overlagring_anvand: tuple[str, ...]
    varningar: tuple[str, ...]
    profilfil_sha256: str = ""
    koordinatsystem: str = KOORDINATSYSTEM_EPSG


@dataclass(frozen=True)
class GyfDelfaktorJamforelse:
    """Före/efter-skillnad för en enskild delfaktor."""

    faktor_id: str
    namn: str
    area_fore_m2: float
    area_efter_m2: float
    delta_area_m2: float
    eko_effektiv_area_fore_m2: float
    eko_effektiv_area_efter_m2: float
    delta_eko_effektiv_area_m2: float
    delta_bidrag_gyf: float


@dataclass(frozen=True)
class GyfJamforelse:
    """Före/efter-jämförelse av Grönytefaktor mellan två scenarier med samma profil."""

    profil_id: str
    riktmarke: float
    fore: GyfResultat
    efter: GyfResultat
    delta_gyf: float
    delta_eko_effektiv_area_m2: float
    delta_grund_eko_effektiv_area_m2: float
    delta_tillagg_eko_effektiv_area_m2: float
    delta_tomt_area_m2: float
    uppfyller_mal_fore: bool
    uppfyller_mal_efter: bool
    saknas_till_mal_fore_m2: float
    saknas_till_mal_m2: float
    per_delfaktor: tuple[GyfDelfaktorJamforelse, ...]
    varningar: tuple[str, ...]


def _tolka_delfaktor(
    faktor_id: str,
    rad: Mapping[str, Any],
    *,
    krav_k1_koder: bool,
) -> GyfDelfaktor:
    if not isinstance(rad, Mapping):
        raise EkologiFel(f"GYF-profil: posten '{faktor_id}' måste vara en tabell.")
    namn = _validera_text(rad.get("namn"), f"{faktor_id}.namn")
    faktor = _validera_andligt_tal(rad.get("faktor"), f"{faktor_id}.faktor")
    beskrivning = _validera_text(
        rad.get("beskrivning", ""), f"{faktor_id}.beskrivning", tillat_tom=True
    )
    kalla = _validera_text(rad.get("kalla", ""), f"{faktor_id}.kalla", tillat_tom=True)
    typ = _validera_text(rad.get("typ", "schablon"), f"{faktor_id}.typ")
    anmarkning = _validera_text(
        rad.get("anmarkning", ""), f"{faktor_id}.anmarkning", tillat_tom=True
    )

    k1_koder_ra = rad.get("k1_koder", [])
    if krav_k1_koder and "k1_koder" not in rad:
        raise EkologiFel(f"GYF-profil: delfaktor '{faktor_id}' saknar fältet 'k1_koder'.")
    if not isinstance(k1_koder_ra, list):
        raise EkologiFel(f"GYF-profil: '{faktor_id}.k1_koder' måste vara en lista av heltal.")
    k1_koder: list[int] = []
    for k in k1_koder_ra:
        if isinstance(k, bool) or not isinstance(k, int) or k < 0:
            raise EkologiFel(
                f"GYF-profil: ogiltig K1-kod {k!r} i '{faktor_id}.k1_koder'."
            )
        k1_koder.append(k)

    return GyfDelfaktor(
        faktor_id=faktor_id,
        namn=namn,
        faktor=faktor,
        k1_koder=tuple(k1_koder),
        beskrivning=beskrivning,
        kalla=kalla,
        typ=typ,
        anmarkning=anmarkning,
    )


def ladda_gyf_profiler(sokvag: Path | None = None) -> dict[str, GyfProfil]:
    """Laddar och validerar kommunala GYF-profiler från `data/gyf/profiler.toml`."""
    fil = PROFILER_FIL if sokvag is None else Path(sokvag)
    if not fil.is_file():
        raise EkologiFel(f"GYF-profilfilen saknas: {fil}")
    try:
        ra = tomllib.loads(fil.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EkologiFel(f"GYF-profilfilen {fil.name} kunde inte tolkas: {exc}") from exc

    if ra.get("format") != FORMAT_GYF_PROFILER:
        raise EkologiFel(
            f"GYF-profilfilen {fil.name} har okänt format {ra.get('format')!r}, "
            f"förväntade '{FORMAT_GYF_PROFILER}'."
        )
    profiler_ra = ra.get("profiler")
    if not isinstance(profiler_ra, Mapping) or not profiler_ra:
        raise EkologiFel(f"GYF-profilfilen {fil.name} saknar avsnittet [profiler].")

    fil_sha = sha256_fil(fil)
    ut: dict[str, GyfProfil] = {}
    for profil_id, p in profiler_ra.items():
        if not isinstance(p, Mapping):
            raise EkologiFel(f"GYF-profil '{profil_id}' måste vara en tabell.")
        delfaktorer_ra = p.get("delfaktorer")
        if not isinstance(delfaktorer_ra, Mapping) or not delfaktorer_ra:
            raise EkologiFel(f"GYF-profil '{profil_id}' saknar delfaktorer.")
        delfaktorer = {
            fid: _tolka_delfaktor(fid, rad, krav_k1_koder=True)
            for fid, rad in delfaktorer_ra.items()
        }

        tillagg_ra = p.get("tillaggsfaktorer", {})
        if not isinstance(tillagg_ra, Mapping):
            raise EkologiFel(f"GYF-profil '{profil_id}': 'tillaggsfaktorer' måste vara en tabell.")
        tillaggsfaktorer = {
            tid: _tolka_delfaktor(tid, rad, krav_k1_koder=False)
            for tid, rad in tillagg_ra.items()
        }

        prio_ra = p.get("overlagring_prioritet", list(STANDARD_OVERLAGRING_PRIORITET))
        if not isinstance(prio_ra, list) or not all(isinstance(x, str) for x in prio_ra):
            raise EkologiFel(
                f"GYF-profil '{profil_id}': 'overlagring_prioritet' måste vara en lista av text."
            )

        ut[profil_id] = GyfProfil(
            profil_id=profil_id,
            namn=_validera_text(p.get("namn"), f"{profil_id}.namn"),
            kommun=_validera_text(p.get("kommun"), f"{profil_id}.kommun"),
            riktmarke=_validera_andligt_tal(
                p.get("riktmarke"), f"{profil_id}.riktmarke", strikt_positivt=True
            ),
            status=_validera_text(p.get("status"), f"{profil_id}.status"),
            version=_validera_text(p.get("version"), f"{profil_id}.version"),
            beskrivning=_validera_text(
                p.get("beskrivning", ""), f"{profil_id}.beskrivning", tillat_tom=True
            ),
            kalla=_validera_text(p.get("kalla"), f"{profil_id}.kalla"),
            kalla_url=_validera_text(
                p.get("kalla_url", ""), f"{profil_id}.kalla_url", tillat_tom=True
            ),
            delfaktorer=delfaktorer,
            tillaggsfaktorer=tillaggsfaktorer,
            overlagring_prioritet=tuple(prio_ra),
            anmarkning=_validera_text(
                p.get("anmarkning", ""), f"{profil_id}.anmarkning", tillat_tom=True
            ),
            profilfil_sha256=fil_sha,
        )
    return ut


def hamta_gyf_profil(profil_id: str, *, sokvag: Path | None = None) -> GyfProfil:
    """Hämtar en kommunal GYF-profil efter id, eller lyfter `EkologiFel` (ValueError)."""
    profiler = ladda_gyf_profiler(sokvag)
    profil = profiler.get(profil_id)
    if profil is None:
        giltiga = ", ".join(sorted(profiler))
        raise EkologiFel(f"Okänd GYF-profil {profil_id!r}. Giltiga profiler: {giltiga}.")
    return profil


def berakna_gyf_fran_raster(
    markslag: NDArray[Any],
    cellstorlek_m: float,
    profil: str | GyfProfil = "stockholm",
    *,
    tomt_mask: NDArray[np.bool_] | None = None,
    yt_overlagring: Mapping[str, NDArray[Any]] | None = None,
    tillagg: Sequence[GyfTillagg] = (),
) -> GyfResultat:
    """Beräknar Grönytefaktor (GYF) och delfaktorutfall ur ett K1 v2-markslagsraster.

    Formel (area i m², cellarea = `cellstorlek_m ** 2` i SWEREF 99 18 00):
        GYF = (sum_i A_i * f_i + sum_j T_j * g_j) / A_tomt

    K50-överlagringar (`yt_overlagring`) appliceras före basrastret i den deterministiska
    prioritetsordning som profilens `overlagring_prioritet` anger (högst prioritet vinner
    utan dubbelräkning; överlapp inom `tomt_mask` rapporteras i `varningar`).

    Inga tysta fel: celler inom `tomt_mask` med okända markslagskoder (inkl. K1-kod 0 = okänt)
    eller `NaN`/icke-ändliga/icke-heltalsvärden gissas aldrig utan redovisas i `flaggad_area_m2`,
    `okanda_koder`, `ogiltiga_celler` och `varningar` med bidrag 0,0 till ekoeffektiv yta.
    """
    cell_m = _validera_andligt_tal(cellstorlek_m, "cellstorlek_m", strikt_positivt=True)

    arr = np.asarray(markslag)
    if arr.ndim != 2 or arr.shape[0] <= 0 or arr.shape[1] <= 0:
        raise EkologiFel(
            f"GYF: 'markslag' måste vara ett icke-tomt 2D-raster, fick form {arr.shape}."
        )
    if not (
        np.issubdtype(arr.dtype, np.integer) or np.issubdtype(arr.dtype, np.floating)
    ):
        raise EkologiFel(
            f"GYF: 'markslag' måste ha heltals- eller flyttalstyp, fick {arr.dtype}."
        )

    if isinstance(profil, str):
        gyf_profil = hamta_gyf_profil(profil)
    elif isinstance(profil, GyfProfil):
        gyf_profil = profil
    else:
        raise EkologiFel(
            "GYF: 'profil' måste vara profil-id (str) eller GyfProfil, "
            f"fick {type(profil).__name__}."
        )

    form = (int(arr.shape[0]), int(arr.shape[1]))
    aktiv_mask: NDArray[np.bool_] = (
        np.ones(form, dtype=np.bool_)
        if tomt_mask is None
        else _validera_bool_mask(tomt_mask, form, "tomt_mask")
    )

    tomt_celler = int(np.count_nonzero(aktiv_mask))
    if tomt_celler <= 0:
        raise EkologiFel("GYF: tomtarea måste vara positiv (tomt_mask har 0 aktiva celler).")

    cell_area_m2 = cell_m * cell_m
    tomt_area_m2 = float(tomt_celler) * cell_area_m2
    if not math.isfinite(tomt_area_m2) or tomt_area_m2 <= 0.0:
        raise EkologiFel(f"GYF: ogiltig tomtarea {tomt_area_m2} m².")

    varningar: list[str] = []
    celler_per_faktor: dict[str, int] = {fid: 0 for fid in gyf_profil.delfaktorer}
    kvarvarande_mask = aktiv_mask.copy()
    anvanda_overlagringar: list[str] = []

    if yt_overlagring is not None:
        okanda_nycklar = sorted(set(yt_overlagring) - set(gyf_profil.delfaktorer))
        if okanda_nycklar:
            giltiga = ", ".join(gyf_profil.delfaktorer)
            raise EkologiFel(
                f"GYF: okända nycklar i yt_overlagring för profil '{gyf_profil.profil_id}': "
                f"{okanda_nycklar}. Giltiga delfaktorer: {giltiga}."
            )
        validerade_overlagringar: dict[str, NDArray[np.bool_]] = {
            fid: _validera_bool_mask(mask, form, f"yt_overlagring[{fid!r}]")
            for fid, mask in yt_overlagring.items()
        }
        overlapp_par: list[str] = []
        redan_takt = np.zeros(form, dtype=np.bool_)
        for fid in gyf_profil.effektiv_overlagring_prioritet():
            if fid not in validerade_overlagringar:
                continue
            ov_inom_tomt = validerade_overlagringar[fid] & aktiv_mask
            overlapp_celler = int(np.count_nonzero(ov_inom_tomt & redan_takt))
            if overlapp_celler > 0:
                overlapp_par.append(
                    f"'{fid}' ({overlapp_celler} celler / {overlapp_celler * cell_area_m2:.2f} m²)"
                )
            tilldelade = ov_inom_tomt & kvarvarande_mask
            antal = int(np.count_nonzero(tilldelade))
            if antal > 0:
                celler_per_faktor[fid] += antal
                kvarvarande_mask &= ~tilldelade
                anvanda_overlagringar.append(fid)
            redan_takt |= ov_inom_tomt
        if overlapp_par:
            varningar.append(
                "Överlappande K50-överlagringar inom tomtgränsen löstes deterministiskt "
                f"enligt profilens prioritetsordning; åsidosatta ytor: {', '.join(overlapp_par)}."
            )

    ogiltiga_celler = 0
    okanda_koder_antal: dict[int, int] = {}
    kod_till_df = gyf_profil.k1_kod_till_delfaktor()

    if bool(np.any(kvarvarande_mask)):
        sub = arr[kvarvarande_mask]
        if np.issubdtype(arr.dtype, np.floating):
            sub_f = sub.astype(np.float64, copy=False)
            giltig = np.isfinite(sub_f) & (np.floor(sub_f) == sub_f)
            ogiltiga_celler = int(sub_f.size - int(np.count_nonzero(giltig)))
            giltiga_koder = sub_f[giltig].astype(np.int64)
        else:
            giltiga_koder = sub.astype(np.int64, copy=False)

        if giltiga_koder.size > 0:
            unika, antal_arr = np.unique(giltiga_koder, return_counts=True)
            for kod_np, antal_np in zip(unika, antal_arr, strict=True):
                kod = int(kod_np)
                antal = int(antal_np)
                df = kod_till_df.get(kod)
                if df is not None:
                    celler_per_faktor[df.faktor_id] += antal
                else:
                    okanda_koder_antal[kod] = okanda_koder_antal.get(kod, 0) + antal

    okanda_celler = sum(okanda_koder_antal.values())
    flaggade_celler = ogiltiga_celler + okanda_celler
    flaggad_area_m2 = float(flaggade_celler) * cell_area_m2
    flaggad_andel_pct = 100.0 * flaggad_area_m2 / tomt_area_m2
    okanda_koder = tuple(sorted(okanda_koder_antal))

    if okanda_koder:
        kod_beskrivning = ", ".join(
            f"{k} ({K1_MARKSLAG_NAMN[k]}: {okanda_koder_antal[k]} celler)"
            if k in K1_MARKSLAG_NAMN
            else f"{k} ({okanda_koder_antal[k]} celler)"
            for k in okanda_koder
        )
        okand_area_m2 = float(okanda_celler) * cell_area_m2
        varningar.append(
            f"Okända/omappade markslagskoder inom tomtgränsen ({kod_beskrivning}; "
            f"totalt {okand_area_m2:.2f} m², {100.0 * okand_area_m2 / tomt_area_m2:.2f} % av "
            "tomtarean) gissas inte och räknas med bidrag 0,0 i flaggad_area_m2."
        )
    if ogiltiga_celler > 0:
        ogiltig_area_m2 = float(ogiltiga_celler) * cell_area_m2
        varningar.append(
            f"{ogiltiga_celler} celler ({ogiltig_area_m2:.2f} m², "
            f"{100.0 * ogiltig_area_m2 / tomt_area_m2:.2f} % av tomtarean) inom tomtgränsen "
            "har NaN eller ogiltigt markslagsvärde; de gissas inte och redovisas i flaggad_area_m2."
        )

    delfaktor_utfall: list[GyfDelfaktorUtfall] = []
    grund_eko_m2 = 0.0
    for fid, df in gyf_profil.delfaktorer.items():
        antal = celler_per_faktor[fid]
        area_m2 = float(antal) * cell_area_m2
        eko_m2 = area_m2 * df.faktor
        grund_eko_m2 += eko_m2
        delfaktor_utfall.append(
            GyfDelfaktorUtfall(
                faktor_id=fid,
                namn=df.namn,
                faktor=df.faktor,
                antal_celler=antal,
                area_m2=area_m2,
                andel_av_tomt_pct=100.0 * area_m2 / tomt_area_m2,
                eko_effektiv_area_m2=eko_m2,
                bidrag_gyf=eko_m2 / tomt_area_m2,
            )
        )

    tillagg_utfall: list[GyfTillaggUtfall] = []
    tillagg_eko_m2 = 0.0
    for post in tillagg:
        if not isinstance(post, GyfTillagg):
            raise EkologiFel(
                f"GYF: varje element i 'tillagg' måste vara GyfTillagg, fick {type(post).__name__}."
            )
        profil_tf = gyf_profil.tillaggsfaktorer.get(post.tillagg_id)
        if post.faktor is not None:
            vikt = post.faktor
            namn = post.namn or (profil_tf.namn if profil_tf is not None else post.tillagg_id)
        else:
            if profil_tf is None:
                giltiga = ", ".join(sorted(gyf_profil.tillaggsfaktorer))
                raise EkologiFel(
                    f"GYF: okänd tilläggsfaktor {post.tillagg_id!r} för profil "
                    f"'{gyf_profil.profil_id}' (ange explicit faktor eller använd en av: "
                    f"{giltiga})."
                )
            vikt = profil_tf.faktor
            namn = post.namn or profil_tf.namn

        eko_m2 = post.area_m2 * vikt
        tillagg_eko_m2 += eko_m2
        tillagg_utfall.append(
            GyfTillaggUtfall(
                tillagg_id=post.tillagg_id,
                namn=namn,
                area_m2=post.area_m2,
                faktor=vikt,
                andel_av_tomt_pct=100.0 * post.area_m2 / tomt_area_m2,
                eko_effektiv_area_m2=eko_m2,
                bidrag_gyf=eko_m2 / tomt_area_m2,
            )
        )

    eko_effektiv_area_m2 = grund_eko_m2 + tillagg_eko_m2
    gyf = eko_effektiv_area_m2 / tomt_area_m2
    mal_area_m2 = gyf_profil.riktmarke * tomt_area_m2
    uppfyller_mal = (gyf + _MAL_TOLERANS) >= gyf_profil.riktmarke
    saknas_till_mal_m2 = 0.0 if uppfyller_mal else max(0.0, mal_area_m2 - eko_effektiv_area_m2)

    return GyfResultat(
        profil_id=gyf_profil.profil_id,
        profil_namn=gyf_profil.namn,
        kommun=gyf_profil.kommun,
        metodstatus=gyf_profil.status,
        riktmarke=gyf_profil.riktmarke,
        cellstorlek_m=cell_m,
        tomt_celler=tomt_celler,
        tomt_area_m2=tomt_area_m2,
        grund_eko_effektiv_area_m2=grund_eko_m2,
        tillagg_eko_effektiv_area_m2=tillagg_eko_m2,
        eko_effektiv_area_m2=eko_effektiv_area_m2,
        gyf=gyf,
        uppfyller_mal=uppfyller_mal,
        saknas_till_mal_m2=saknas_till_mal_m2,
        delfaktorer=tuple(delfaktor_utfall),
        tillagg=tuple(tillagg_utfall),
        flaggade_celler=flaggade_celler,
        flaggad_area_m2=flaggad_area_m2,
        flaggad_andel_pct=flaggad_andel_pct,
        okanda_koder=okanda_koder,
        ogiltiga_celler=ogiltiga_celler,
        overlagring_anvand=tuple(anvanda_overlagringar),
        varningar=tuple(varningar),
        profilfil_sha256=gyf_profil.profilfil_sha256,
        koordinatsystem=KOORDINATSYSTEM_EPSG,
    )


def jamfor_gyf(fore: GyfResultat, efter: GyfResultat) -> GyfJamforelse:
    """Jämför GYF före och efter ett planförslag med exakta deltan per delfaktor och målstatus."""
    if fore.profil_id != efter.profil_id:
        raise EkologiFel(
            f"Kan inte jämföra GYF mellan olika profiler: '{fore.profil_id}' mot "
            f"'{efter.profil_id}'."
        )
    if abs(fore.riktmarke - efter.riktmarke) > _MAL_TOLERANS:
        raise EkologiFel(
            f"Kan inte jämföra GYF med olika riktmärken: {fore.riktmarke} mot {efter.riktmarke}."
        )

    fore_karta = {d.faktor_id: d for d in fore.delfaktorer}
    efter_karta = {d.faktor_id: d for d in efter.delfaktorer}
    ordnade_id = list(fore_karta) + [fid for fid in efter_karta if fid not in fore_karta]

    per_delfaktor: list[GyfDelfaktorJamforelse] = []
    for fid in ordnade_id:
        df_fore = fore_karta.get(fid)
        df_efter = efter_karta.get(fid)
        namn = df_efter.namn if df_efter is not None else (df_fore.namn if df_fore else fid)
        area_f = df_fore.area_m2 if df_fore is not None else 0.0
        area_e = df_efter.area_m2 if df_efter is not None else 0.0
        eko_f = df_fore.eko_effektiv_area_m2 if df_fore is not None else 0.0
        eko_e = df_efter.eko_effektiv_area_m2 if df_efter is not None else 0.0
        bidrag_f = df_fore.bidrag_gyf if df_fore is not None else 0.0
        bidrag_e = df_efter.bidrag_gyf if df_efter is not None else 0.0
        per_delfaktor.append(
            GyfDelfaktorJamforelse(
                faktor_id=fid,
                namn=namn,
                area_fore_m2=area_f,
                area_efter_m2=area_e,
                delta_area_m2=area_e - area_f,
                eko_effektiv_area_fore_m2=eko_f,
                eko_effektiv_area_efter_m2=eko_e,
                delta_eko_effektiv_area_m2=eko_e - eko_f,
                delta_bidrag_gyf=bidrag_e - bidrag_f,
            )
        )

    varningar: list[str] = []
    delta_tomt_area_m2 = efter.tomt_area_m2 - fore.tomt_area_m2
    if abs(delta_tomt_area_m2) > 1e-9:
        varningar.append(
            f"Tomtarean skiljer sig mellan före ({fore.tomt_area_m2:.2f} m²) och "
            f"efter ({efter.tomt_area_m2:.2f} m²; delta {delta_tomt_area_m2:+.2f} m²)."
        )
    for v in fore.varningar:
        varningar.append(f"Före: {v}")
    for v in efter.varningar:
        varningar.append(f"Efter: {v}")

    return GyfJamforelse(
        profil_id=efter.profil_id,
        riktmarke=efter.riktmarke,
        fore=fore,
        efter=efter,
        delta_gyf=efter.gyf - fore.gyf,
        delta_eko_effektiv_area_m2=efter.eko_effektiv_area_m2 - fore.eko_effektiv_area_m2,
        delta_grund_eko_effektiv_area_m2=(
            efter.grund_eko_effektiv_area_m2 - fore.grund_eko_effektiv_area_m2
        ),
        delta_tillagg_eko_effektiv_area_m2=(
            efter.tillagg_eko_effektiv_area_m2 - fore.tillagg_eko_effektiv_area_m2
        ),
        delta_tomt_area_m2=delta_tomt_area_m2,
        uppfyller_mal_fore=fore.uppfyller_mal,
        uppfyller_mal_efter=efter.uppfyller_mal,
        saknas_till_mal_fore_m2=fore.saknas_till_mal_m2,
        saknas_till_mal_m2=efter.saknas_till_mal_m2,
        per_delfaktor=tuple(per_delfaktor),
        varningar=tuple(varningar),
    )


def gyf_profil_till_dict(profil: GyfProfil) -> dict[str, Any]:
    """Serialiserar en `GyfProfil` till deterministisk JSON-vänlig dict."""
    return {
        "profil_id": profil.profil_id,
        "namn": profil.namn,
        "kommun": profil.kommun,
        "riktmarke": float(profil.riktmarke),
        "status": profil.status,
        "version": profil.version,
        "beskrivning": profil.beskrivning,
        "kalla": profil.kalla,
        "kalla_url": profil.kalla_url,
        "anmarkning": profil.anmarkning,
        "profilfil_sha256": profil.profilfil_sha256,
        "overlagring_prioritet": list(profil.effektiv_overlagring_prioritet()),
        "delfaktorer": [
            {
                "faktor_id": df.faktor_id,
                "namn": df.namn,
                "faktor": float(df.faktor),
                "k1_koder": [int(k) for k in df.k1_koder],
                "beskrivning": df.beskrivning,
                "kalla": df.kalla,
                "typ": df.typ,
                "anmarkning": df.anmarkning,
            }
            for df in profil.delfaktorer.values()
        ],
        "tillaggsfaktorer": [
            {
                "faktor_id": tf.faktor_id,
                "namn": tf.namn,
                "faktor": float(tf.faktor),
                "beskrivning": tf.beskrivning,
                "kalla": tf.kalla,
                "typ": tf.typ,
                "anmarkning": tf.anmarkning,
            }
            for tf in profil.tillaggsfaktorer.values()
        ],
    }


def gyf_till_dict(resultat: GyfResultat) -> dict[str, Any]:
    """Serialiserar `GyfResultat` till deterministisk JSON-vänlig dict (Våg 7 webb/API/R2)."""
    return {
        "format": FORMAT_GYF_RESULTAT,
        "koordinatsystem": resultat.koordinatsystem,
        "profil_id": resultat.profil_id,
        "profil_namn": resultat.profil_namn,
        "kommun": resultat.kommun,
        "metodstatus": resultat.metodstatus,
        "profilfil_sha256": resultat.profilfil_sha256,
        "riktmarke": float(resultat.riktmarke),
        "cellstorlek_m": float(resultat.cellstorlek_m),
        "tomt_celler": int(resultat.tomt_celler),
        "tomt_area_m2": float(resultat.tomt_area_m2),
        "grund_eko_effektiv_area_m2": float(resultat.grund_eko_effektiv_area_m2),
        "tillagg_eko_effektiv_area_m2": float(resultat.tillagg_eko_effektiv_area_m2),
        "eko_effektiv_area_m2": float(resultat.eko_effektiv_area_m2),
        "gyf": float(resultat.gyf),
        "uppfyller_mal": bool(resultat.uppfyller_mal),
        "saknas_till_mal_m2": float(resultat.saknas_till_mal_m2),
        "delfaktorer": [
            {
                "faktor_id": d.faktor_id,
                "namn": d.namn,
                "faktor": float(d.faktor),
                "antal_celler": int(d.antal_celler),
                "area_m2": float(d.area_m2),
                "andel_av_tomt_pct": float(d.andel_av_tomt_pct),
                "eko_effektiv_area_m2": float(d.eko_effektiv_area_m2),
                "bidrag_gyf": float(d.bidrag_gyf),
            }
            for d in resultat.delfaktorer
        ],
        "tillagg": [
            {
                "tillagg_id": t.tillagg_id,
                "namn": t.namn,
                "area_m2": float(t.area_m2),
                "faktor": float(t.faktor),
                "andel_av_tomt_pct": float(t.andel_av_tomt_pct),
                "eko_effektiv_area_m2": float(t.eko_effektiv_area_m2),
                "bidrag_gyf": float(t.bidrag_gyf),
            }
            for t in resultat.tillagg
        ],
        "flaggade_celler": int(resultat.flaggade_celler),
        "flaggad_area_m2": float(resultat.flaggad_area_m2),
        "flaggad_andel_pct": float(resultat.flaggad_andel_pct),
        "okanda_koder": [int(k) for k in resultat.okanda_koder],
        "ogiltiga_celler": int(resultat.ogiltiga_celler),
        "overlagring_anvand": list(resultat.overlagring_anvand),
        "varningar": list(resultat.varningar),
    }


def gyf_jamforelse_till_dict(jamforelse: GyfJamforelse) -> dict[str, Any]:
    """Serialiserar `GyfJamforelse` till deterministisk JSON-vänlig dict (Våg 7 webb/API/R2)."""
    return {
        "format": FORMAT_GYF_JAMFORELSE,
        "profil_id": jamforelse.profil_id,
        "riktmarke": float(jamforelse.riktmarke),
        "delta_gyf": float(jamforelse.delta_gyf),
        "delta_eko_effektiv_area_m2": float(jamforelse.delta_eko_effektiv_area_m2),
        "delta_grund_eko_effektiv_area_m2": float(jamforelse.delta_grund_eko_effektiv_area_m2),
        "delta_tillagg_eko_effektiv_area_m2": float(jamforelse.delta_tillagg_eko_effektiv_area_m2),
        "delta_tomt_area_m2": float(jamforelse.delta_tomt_area_m2),
        "uppfyller_mal_fore": bool(jamforelse.uppfyller_mal_fore),
        "uppfyller_mal_efter": bool(jamforelse.uppfyller_mal_efter),
        "saknas_till_mal_fore_m2": float(jamforelse.saknas_till_mal_fore_m2),
        "saknas_till_mal_m2": float(jamforelse.saknas_till_mal_m2),
        "per_delfaktor": [
            {
                "faktor_id": d.faktor_id,
                "namn": d.namn,
                "area_fore_m2": float(d.area_fore_m2),
                "area_efter_m2": float(d.area_efter_m2),
                "delta_area_m2": float(d.delta_area_m2),
                "eko_effektiv_area_fore_m2": float(d.eko_effektiv_area_fore_m2),
                "eko_effektiv_area_efter_m2": float(d.eko_effektiv_area_efter_m2),
                "delta_eko_effektiv_area_m2": float(d.delta_eko_effektiv_area_m2),
                "delta_bidrag_gyf": float(d.delta_bidrag_gyf),
            }
            for d in jamforelse.per_delfaktor
        ],
        "varningar": list(jamforelse.varningar),
        "fore": gyf_till_dict(jamforelse.fore),
        "efter": gyf_till_dict(jamforelse.efter),
    }
