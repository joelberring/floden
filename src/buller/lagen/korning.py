"""Orkestrering av en bullerkörning i skiss- eller rapportläge (KB3, KB4).

Båda lägena går genom samma kod. Det enda som skiljer är toleransprofilen och de kontroller
som rapportläget kräver (Nord2000-konform kärna och godkänd testsvit grupp N).
"""

from __future__ import annotations

import dataclasses
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from buller.lagen.profil import SKISSMARKNING, Lage, Toleransprofil
from buller.operator.energi import energisumma_db
from buller.operator.granssnitt import (
    KONTRAKT_KB2,
    Korridorindex,
    OvreGrans,
    Parberaknare,
    Rektangel,
)
from buller.operator.inkrementell import rakna_om_inkrementellt
from buller.operator.klustring import (
    ANTAGANDE_OVRE_GRANS,
    bygg_overforingslager,
    omklustra_mottagare,
)
from buller.operator.lager import Overforingslager, a_vagd_niva_db
from buller.typer import (
    ANTAL_BAND,
    KONTRAKT_KB1,
    BullerFel,
    Flyttal,
    Kallsegment,
    Mottagare,
    Vader,
)
from gemensamt.hash import sha256_bytes
from gemensamt.manifest import FilPost, RunManifest, skapa_manifest

KONTRAKT_KB3 = "KB3/v1"
# Marginal för avrundning när en felgräns jämförs med profilens gräns (dB).
_FELGRANS_MARGINAL_DB = 1e-9
# Tolerans när uppmätt fel i kontrollmottagare jämförs med garanterad gräns (dB).
_KONTROLL_TOLERANS_DB = 1e-6


class Karnbeskrivning(Protocol):
    """Det körningen behöver veta om kärnan. `typer.P2PKarna` uppfyller protokollet."""

    @property
    def namn(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def nord2000_konform(self) -> bool: ...


@dataclass(frozen=True)
class Karninfo:
    """Namn, version och konformitet för den kärna som parberäknaren bygger på."""

    namn: str
    version: str
    nord2000_konform: bool


@dataclass(frozen=True)
class SvitStatus:
    """Utfall av testsvitens grupp N för den kärna som används (från paket B1:s körare)."""

    grupp: str
    godkand: bool
    antal_fall: int
    antal_godkanda: int
    referens: str  # t.ex. sökväg eller sha256 för testkörningens manifest

    def ar_godkand_grupp_n(self) -> bool:
        return (
            self.grupp == "N"
            and self.godkand is True
            and self.antal_fall > 0
            and self.antal_godkanda == self.antal_fall
        )


@dataclass(frozen=True, eq=False)
class BullerResultat:
    """Nivåer per mottagare.

    Med klustring är `niva_db` och `niva_a_db` summan av de beräknade paren. Den sanna A-vägda
    nivån ligger i `[niva_a_db, niva_a_db + felgrans_db]` under antagandet om övre gränsen.
    """

    lage: Lage
    markning: str
    mottagare_id: NDArray[np.int64]  # form (n_mottagare,)
    niva_db: Flyttal  # form (n_mottagare, ANTAL_BAND)
    niva_a_db: Flyttal  # form (n_mottagare,)
    felgrans_db: Flyttal  # form (n_mottagare,), garanterad gräns för felet i niva_a_db


@dataclass(frozen=True, eq=False)
class BullerKorning:
    """Resultat, överföringslager (för återanvändning) och körmanifest."""

    resultat: BullerResultat
    lager: Overforingslager
    manifest: RunManifest


def _json_tal(varde: float) -> float | None:
    """Ändligt tal eller None (JSON saknar oändlighet)."""
    return float(varde) if math.isfinite(varde) else None


def _array_sha256(array: Flyttal) -> str:
    data = np.ascontiguousarray(array, dtype="<f8")
    return sha256_bytes(f"{data.shape}|".encode() + data.tobytes())


def _kontrollera_lage(
    profil: Toleransprofil,
    karna: Karnbeskrivning,
    testsvit_n: SvitStatus | None,
    reflexordning: int,
) -> list[str]:
    """Lägets krav enligt KB3. Returnerar varningar för manifestet."""
    varningar: list[str] = []
    if reflexordning != profil.reflexordning:
        raise BullerFel(
            f"Parberäknaren bygger på reflexordning {reflexordning} men profilen "
            f"'{profil.namn}' kräver {profil.reflexordning}."
        )
    if profil.lage == "rapport":
        if karna.nord2000_konform is not True:
            raise BullerFel(
                f"Rapportläget kräver en Nord2000-konform kärna. '{karna.namn}' "
                f"{karna.version} har nord2000_konform = {karna.nord2000_konform!r}."
            )
        if testsvit_n is None or not testsvit_n.ar_godkand_grupp_n():
            raise BullerFel(
                "Rapportläget kräver att testsvit grupp N är körd och godkänd för kärnan "
                f"(fick {testsvit_n!r})."
            )
    else:
        if profil.markning != SKISSMARKNING:
            raise BullerFel(f"Skissläget måste märkas '{SKISSMARKNING}'.")
        if karna.nord2000_konform is not True:
            varningar.append(
                f"Kärnan '{karna.namn}' {karna.version} är inte Nord2000-konform."
            )
    return varningar


def _kontrollera_tidigare_lager(
    lager: Overforingslager,
    mottagare_id: NDArray[np.int64],
    mottagare_e_m: Flyttal,
    mottagare_n_m: Flyttal,
    kalla_id: NDArray[np.int64],
    kalla_e_m: Flyttal,
    kalla_n_m: Flyttal,
    vikter: Flyttal,
) -> None:
    for namn, nytt in (
        ("mottagare_id", mottagare_id),
        ("mottagare_e_m", mottagare_e_m),
        ("mottagare_n_m", mottagare_n_m),
        ("kalla_id", kalla_id),
        ("kalla_e_m", kalla_e_m),
        ("kalla_n_m", kalla_n_m),
        ("vaderklass_vikter", vikter),
    ):
        if not np.array_equal(getattr(lager, namn), nytt):
            raise BullerFel(
                f"Det tidigare lagret passar inte körningen: {namn} skiljer sig. "
                "Bygg ett nytt lager."
            )


def _mat_klustringsfel(
    antal: int,
    parberaknare: Parberaknare,
    lw_db: Flyttal,
    vikter: Flyttal,
    a_vagning_db: Flyttal,
    niva_a_db: Flyttal,
    felgrans_db: Flyttal,
) -> tuple[float | None, int]:
    """Räknar ett urval mottagare utan klustring och jämför med lagrets nivå.

    Urvalet är jämnt fördelat över mottagarlistan (deterministiskt). Kastar `BullerFel` om
    felet är större än den garanterade gränsen: då håller inte antagandet om övre gränsen.
    """
    n_m, n_k = int(niva_a_db.shape[0]), int(lw_db.shape[0])
    if antal <= 0 or n_m == 0 or n_k == 0:
        return None, 0
    urval = np.unique(np.round(np.linspace(0, n_m - 1, min(antal, n_m))).astype(np.int64))
    alla = list(range(n_k))
    with np.errstate(divide="ignore"):
        vikt_db = 10.0 * np.log10(vikter)[np.newaxis, :, np.newaxis]
    storst = 0.0
    for m in urval.tolist():
        h_db = np.asarray(parberaknare(m, alla), dtype=np.float64)
        if h_db.shape != (n_k, vikter.shape[0], ANTAL_BAND):
            raise BullerFel(f"Parberäknaren gav formen {h_db.shape} för kontrollmottagare.")
        per_band = energisumma_db(h_db + lw_db[:, np.newaxis, :] + vikt_db, axel=(0, 1))
        facit_db = float(a_vagd_niva_db(per_band, a_vagning_db))
        klustrad_db = float(niva_a_db[m])
        fel_db = 0.0 if facit_db == klustrad_db else facit_db - klustrad_db
        if math.isnan(fel_db) or not (
            -_KONTROLL_TOLERANS_DB <= fel_db <= float(felgrans_db[m]) + _KONTROLL_TOLERANS_DB
        ):
            raise BullerFel(
                f"Kontrollmottagare (index {m}): uppmätt klustringsfel {fel_db:.6f} dB ligger "
                f"utanför den garanterade gränsen {float(felgrans_db[m]):.6f} dB. Antagandet "
                "om övre gränsen håller inte, eller så stämmer inte lagret med scenen."
            )
        storst = max(storst, fel_db)
    return storst, int(urval.shape[0])


def kor_buller(
    *,
    mottagare: Sequence[Mottagare],
    kallor: Sequence[Kallsegment],
    lw_db: Flyttal,
    vaderklasser: Sequence[Vader],
    parberaknare: Parberaknare,
    ovre_grans: OvreGrans,
    a_vagning_db: Flyttal,
    karna: Karnbeskrivning,
    profil: Toleransprofil,
    reflexordning: int,
    testsvit_n: SvitStatus | None = None,
    tidigare_lager: Overforingslager | None = None,
    andrad_yta: Rektangel | None = None,
    korridorindex: Korridorindex | None = None,
    antal_banor: int | None = None,
    indata: Sequence[FilPost] = (),
    kodversion: str | None = None,
    start: str | None = None,
) -> BullerKorning:
    """Kör en bullerberäkning och ger resultat, överföringslager och körmanifest (K0, KB4).

    Args:
        mottagare, kallor: scenens beräkningspunkter och punktkällor. Index i `lw_db` och i
            `parberaknare` är positionerna i dessa listor.
        lw_db: emission `L_W[n_kallor, band]` i dB re 1 pW.
        vaderklasser: väderklasser med `vikt` som summerar till 1. Parberäknaren ska ge
            överföringen för just dessa klasser, i samma ordning.
        parberaknare: överföring per par (se `buller.operator.granssnitt`), för scenen som
            den ser ut nu (efter en eventuell ändring).
        ovre_grans: garanterad övre gräns för överföringen som funktion av minsta avstånd.
            Felgränserna i resultatet förutsätter att den håller (`ANTAGANDE_OVRE_GRANS`).
        a_vagning_db: A-vägning per band, från `buller.akustik.band.a_vagning_db()`.
        karna: namn, version och `nord2000_konform` för kärnan bakom parberäknaren.
        profil: toleransprofil (`las_inbyggd_profil("skiss")` eller `"rapport"`).
        reflexordning: reflexordningen som banfinnaren använde. Måste stämma med profilen.
        testsvit_n: utfall av testsvit grupp N. Krävs godkänd i rapportläget.
        tidigare_lager: lager från en tidigare körning på samma mottagare, källor och
            väderklasser. Med enbart ändrad emission görs ingen ny geometriberäkning, så
            länge felgränsen fortfarande hålls.
        andrad_yta, korridorindex: ändrad yta sedan `tidigare_lager` byggdes. Bara de par
            som korridorindexet pekar ut räknas om.
        antal_banor: antal banor bakom parberäknaren, om banfinnaren rapporterar det (KB4).
        indata: filposter för indata (scen, trafik m.m.) till manifestet.

    Raises:
        BullerFel: i rapportläget om kärnan inte är Nord2000-konform, om testsvit grupp N
            inte är godkänd eller om felgränsen inte kan garanteras; i båda lägena om
            indata är inkonsekvent eller om en kontrollmottagare visar att övre gränsen
            inte håller.
    """
    t0 = time.perf_counter()
    varningar = _kontrollera_lage(profil, karna, testsvit_n, reflexordning)
    if not vaderklasser:
        raise BullerFel("Minst en väderklass krävs.")
    if (andrad_yta is None) != (korridorindex is None):
        raise BullerFel("andrad_yta och korridorindex ska anges tillsammans.")
    if andrad_yta is not None and tidigare_lager is None:
        raise BullerFel("En ändrad yta kräver ett tidigare lager att räkna om.")
    if antal_banor is not None and antal_banor < 0:
        raise BullerFel("antal_banor får inte vara negativt.")

    mottagare_id = np.asarray([m.id for m in mottagare], dtype=np.int64)
    mottagare_e_m = np.asarray([m.e_m for m in mottagare], dtype=np.float64)
    mottagare_n_m = np.asarray([m.n_m for m in mottagare], dtype=np.float64)
    kalla_id = np.asarray([k.id for k in kallor], dtype=np.int64)
    kalla_e_m = np.asarray([k.e_m for k in kallor], dtype=np.float64)
    kalla_n_m = np.asarray([k.n_m for k in kallor], dtype=np.float64)
    vikter = np.asarray([v.vikt for v in vaderklasser], dtype=np.float64)
    lw = np.asarray(lw_db, dtype=np.float64)
    a_vagning = np.asarray(a_vagning_db, dtype=np.float64)
    felgrans_mal_db = profil.klustring_felgrans_db

    antal_beraknade_par = 0
    antal_omraknade_inkrementellt = 0
    antal_omklustrade = 0
    if tidigare_lager is None:
        lager, statistik = bygg_overforingslager(
            mottagare_id=mottagare_id,
            mottagare_e_m=mottagare_e_m,
            mottagare_n_m=mottagare_n_m,
            kalla_id=kalla_id,
            kalla_e_m=kalla_e_m,
            kalla_n_m=kalla_n_m,
            vaderklass_vikter=vikter,
            parberaknare=parberaknare,
            ovre_grans=ovre_grans,
            lw_db=lw,
            a_vagning_db=a_vagning,
            felgrans_db=felgrans_mal_db,
            maxradie_m=profil.maxradie_m,
        )
        antal_beraknade_par = statistik.antal_beraknade_par
    else:
        _kontrollera_tidigare_lager(
            tidigare_lager,
            mottagare_id,
            mottagare_e_m,
            mottagare_n_m,
            kalla_id,
            kalla_e_m,
            kalla_n_m,
            vikter,
        )
        lager = tidigare_lager
        if andrad_yta is not None and korridorindex is not None:
            lager, inkrementell = rakna_om_inkrementellt(
                lager, andrad_yta, korridorindex, parberaknare
            )
            antal_omraknade_inkrementellt = inkrementell.antal_omraknade_par
            antal_beraknade_par += inkrementell.antal_omraknade_par
            if inkrementell.antal_okanda_par:
                varningar.append(
                    f"{inkrementell.antal_okanda_par} par från korridorindexet finns inte i "
                    "lagret och hoppades över."
                )
        # Ny emission eller ändrad geometri kan ha ätit upp marginalen: bygg om de mottagare
        # där felgränsen inte längre hålls.
        over = np.flatnonzero(
            lager.felgrans_db(lw, a_vagning) > felgrans_mal_db + _FELGRANS_MARGINAL_DB
        )
        if over.size:
            lager, omklustring = omklustra_mottagare(
                lager,
                over.tolist(),
                parberaknare=parberaknare,
                ovre_grans=ovre_grans,
                lw_db=lw,
                a_vagning_db=a_vagning,
                felgrans_db=felgrans_mal_db,
                maxradie_m=profil.maxradie_m,
            )
            antal_omklustrade = int(over.size)
            antal_beraknade_par += omklustring.antal_beraknade_par

    niva_db = lager.niva_db(lw)
    niva_a_db = a_vagd_niva_db(niva_db, a_vagning)
    felgrans_db = lager.felgrans_db(lw, a_vagning)
    storsta_felgrans_db = float(np.max(felgrans_db)) if felgrans_db.size else 0.0
    if storsta_felgrans_db > felgrans_mal_db + _FELGRANS_MARGINAL_DB:
        antal_over = int(np.sum(felgrans_db > felgrans_mal_db + _FELGRANS_MARGINAL_DB))
        text = (
            f"Felgränsen {felgrans_mal_db} dB kan inte garanteras i {antal_over} mottagare "
            f"(största garanterade gräns: {storsta_felgrans_db} dB). Orsak kan vara maxradien "
            "eller mottagare utan beräknad energi."
        )
        if profil.lage == "rapport":
            raise BullerFel(text)
        varningar.append(text)

    uppmatt_fel_db, antal_kontroll = _mat_klustringsfel(
        profil.kontrollmottagare_antal,
        parberaknare,
        lw,
        vikter,
        a_vagning,
        niva_a_db,
        felgrans_db,
    )
    antal_beraknade_par += antal_kontroll * int(kalla_id.shape[0])
    if antal_banor is None:
        varningar.append("Antal banor är inte rapporterat av banfinnaren (KB4).")
    antal_mojliga_par = lager.antal_mottagare * lager.antal_kallor
    kortid_s = time.perf_counter() - t0

    parametrar: dict[str, Any] = {
        "lage": profil.lage,
        "markning": profil.markning,
        "karna": {
            "namn": karna.namn,
            "version": karna.version,
            "nord2000_konform": bool(karna.nord2000_konform),
        },
        "toleransprofil": profil.till_manifest(),
        "vaderklasser": [dataclasses.asdict(v) for v in vaderklasser],
        "kontrakt_buller": {"KB1": KONTRAKT_KB1, "KB2": KONTRAKT_KB2, "KB3": KONTRAKT_KB3},
        "reflexordning": reflexordning,
        "testsvit_n": None if testsvit_n is None else dataclasses.asdict(testsvit_n),
        "emission_sha256": _array_sha256(lw),
        "a_vagning_sha256": _array_sha256(a_vagning),
        "lager_identitet": lager.identitet,
        "tidigare_lager_identitet": None if tidigare_lager is None else tidigare_lager.identitet,
        "andrad_yta": None if andrad_yta is None else dataclasses.asdict(andrad_yta),
        "antagande_ovre_grans": ANTAGANDE_OVRE_GRANS,
    }
    matvarden: dict[str, Any] = {
        "antal_mottagare": lager.antal_mottagare,
        "antal_kallor": lager.antal_kallor,
        "antal_banor": antal_banor,
        "antal_par_i_lager": lager.antal_par,
        "antal_restpar": lager.antal_restpar,
        "antal_mojliga_par": antal_mojliga_par,
        "antal_beraknade_par": antal_beraknade_par,
        "andel_beraknade_par": (
            antal_beraknade_par / antal_mojliga_par if antal_mojliga_par else 0.0
        ),
        "antal_omraknade_par_inkrementellt": antal_omraknade_inkrementellt,
        "antal_omklustrade_mottagare": antal_omklustrade,
        "kortid_s": kortid_s,
        "garanterad_felgrans_max_db": _json_tal(storsta_felgrans_db),
        "uppmatt_klustringsfel_db": uppmatt_fel_db,
        "antal_kontrollmottagare": antal_kontroll,
    }
    manifest = skapa_manifest(
        "buller",
        indata=list(indata),
        parametrar=parametrar,
        matvarden=matvarden,
        varningar=varningar,
        kodversion=kodversion,
        start=start,
    )
    resultat = BullerResultat(
        lage=profil.lage,
        markning=profil.markning,
        mottagare_id=mottagare_id,
        niva_db=niva_db,
        niva_a_db=niva_a_db,
        felgrans_db=felgrans_db,
    )
    return BullerKorning(resultat=resultat, lager=lager, manifest=manifest)
