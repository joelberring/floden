"""Jämförelse mellan beräknat resultat och facit enligt Miljøprojekt 1335/2010, kapitel 6.

Reglerna (gränsvärdena) läses ur `data/acceptabel_avvikelse.toml`, som har ett regelverk per
testomgång (Miljøprojekt 1335/2010 och RL 18-18) och källhänvisade undantag för kända fel i
testfallen. Tre resultatklasser:

- **godkänd**: inga avvikelser över gränserna,
- **ska utredas**: en avvikelse som enligt rapporten ska kontrolleras och förklaras,
- **underkänd**: en A-vägd totalnivå avviker mer än den hårda gränsen.

Värden som inte är facit (`ar_facit = false`) klassas aldrig; de får klassen **ej facit**.

Dippförskjutning: rapporten godtar i regel att markeffektens dipp hamnar ett tersband fel,
men säger inte hur det ska avgöras. Tolkningen här (se loggen): en dipp är ett inre lokalt
minimum i spektrumet. Om facit har en dipp i band `i`, beräkningen saknar dipp i `i` men har
en i `i ± 1` (där facit saknar dipp), räknas dippen som förskjuten ett band. Band över
gränsen som ligger nära dippens två lägen ursäktas då och redovisas som anmärkning.
"""

from __future__ import annotations

import tomllib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from buller.testfall.format import Facitserie
from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel, Flyttal

REGELFIL = Path(__file__).parent / "data" / "acceptabel_avvikelse.toml"


class Utfall(StrEnum):
    """Resultatklass för en storhet, en serie eller ett fall."""

    GODKAND = "godkänd"
    SKA_UTREDAS = "ska utredas"
    UNDERKAND = "underkänd"
    EJ_FACIT = "ej facit"
    EJ_KORBAR = "ej körbar"


_ALLVAR = {Utfall.GODKAND: 0, Utfall.SKA_UTREDAS: 1, Utfall.UNDERKAND: 2}


def samsta(klasser: Collection[Utfall]) -> Utfall:
    """Den allvarligaste av klasserna godkänd / ska utredas / underkänd."""
    if not klasser:
        raise BullerFel("Ingen klass att väga samman.")
    if any(k not in _ALLVAR for k in klasser):
        raise BullerFel("Bara godkänd, ska utredas och underkänd kan vägas samman.")
    return max(klasser, key=lambda k: _ALLVAR[k])


@dataclass(frozen=True)
class Undantag:
    """Känt fel i testfallen som påverkar jämförelsen, med källa. Ändrar aldrig facit."""

    id: str
    verkan: str  # "anmarkning" eller "jamfors_inte"
    storheter: tuple[str, ...]
    kalla: str
    text: str

    def __post_init__(self) -> None:
        if self.verkan not in ("anmarkning", "jamfors_inte"):
            raise BullerFel(f"Undantag {self.id}: okänd verkan '{self.verkan}'.")
        if self.verkan == "jamfors_inte" and not self.storheter:
            raise BullerFel(f"Undantag {self.id}: jamfors_inte kräver storheter.")
        if not self.kalla.strip() or not self.text.strip():
            raise BullerFel(f"Undantag {self.id} saknar källa eller text.")


@dataclass(frozen=True)
class Avvikelseregler:
    """Gränsvärden för tillåten avvikelse, med källhänvisning."""

    total_storheter: tuple[str, ...]
    total_grans_utredas_db: float
    total_grans_underkand_db: float | None  # None: regelverket anger ingen hård gräns
    band_jamfors: bool
    band_grans_utredas_db: float
    band_jamfors_inte: tuple[str, ...]
    dipp_tillaten_forskjutning_band: int
    dipp_narhet_band: int
    kalla: str
    undantag: tuple[Undantag, ...] = ()

    def __post_init__(self) -> None:
        if self.total_grans_utredas_db <= 0.0:
            raise BullerFel("Totalnivåns gräns för utredning ska vara större än noll.")
        if (
            self.total_grans_underkand_db is not None
            and self.total_grans_underkand_db < self.total_grans_utredas_db
        ):
            raise BullerFel("Totalnivåns gränser ska uppfylla utredas ≤ underkänd.")
        if self.band_grans_utredas_db <= 0.0:
            raise BullerFel("Bandgränsen ska vara större än noll.")
        if self.dipp_tillaten_forskjutning_band < 0 or self.dipp_narhet_band < 0:
            raise BullerFel("Dippreglernas bandantal får inte vara negativa.")

    def undantagna_storheter(self) -> dict[str, Undantag]:
        """Storheter som enligt ett källhänvisat undantag inte jämförs mot facit."""
        return {s: u for u in self.undantag if u.verkan == "jamfors_inte" for s in u.storheter}


STANDARDREGELVERK = "mp1335_2010"
STANDARDVARIANT = "standard"


def las_regler(
    fil: str | Path = REGELFIL,
    regelverk: str = STANDARDREGELVERK,
    variant: str = STANDARDVARIANT,
) -> Avvikelseregler:
    """Läser regelvärdena för ett regelverk och en variant. Saknade värden ger `BullerFel`.

    Varianten `standard` ska vara fullständig. Andra varianter (t.ex. `kurvig_vag_gul`)
    anger bara det som skiljer; resten tas från `standard` i samma regelverk.
    """
    sokvag = Path(fil)
    try:
        data: dict[str, Any] = tomllib.loads(sokvag.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BullerFel(f"Kunde inte läsa regelfilen {sokvag}: {exc}") from exc
    try:
        verk = data["regelverk"][regelverk]
        if variant not in verk or not isinstance(verk[variant], dict):
            raise KeyError(f"variant '{variant}' i regelverk '{regelverk}'")
        standard, egen = verk[STANDARDVARIANT], verk[variant]
        total = egen.get("totalniva", standard["totalniva"])
        band = {**standard["tersband"], **egen.get("tersband", {})}
        dipp = egen.get("markeffektdipp", standard["markeffektdipp"])
        underkand = total.get("grans_underkand_db")
        if underkand is None and variant == STANDARDVARIANT:
            raise KeyError("totalniva.grans_underkand_db")
        undantag = tuple(
            Undantag(
                id=str(u["id"]),
                verkan=str(u["verkan"]),
                storheter=tuple(u["storheter"]),
                kalla=str(u["kalla"]),
                text=str(u["text"]),
            )
            for u in data.get("undantag", [])
            if u["regelverk"] == regelverk
        )
        return Avvikelseregler(
            total_storheter=(*total["storheter"], *total.get("storheter_tolkning", [])),
            total_grans_utredas_db=float(total["grans_utredas_db"]),
            total_grans_underkand_db=None if underkand is None else float(underkand),
            band_jamfors=bool(band["jamfors"]),
            band_grans_utredas_db=float(band["grans_utredas_db"]),
            band_jamfors_inte=tuple(band.get("jamfors_inte", [])),
            dipp_tillaten_forskjutning_band=int(dipp["tillaten_forskjutning_band"]),
            dipp_narhet_band=int(data["tolkning_b1"]["dipp_narhet_band"]),
            kalla=f"{verk['dokument']}, avsnitt {verk['avsnitt']}, s. {verk['sida']}",
            undantag=undantag,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BullerFel(f"Regelfilen {sokvag} saknar eller har fel värde: {exc}") from exc


@dataclass(frozen=True)
class Totaljamforelse:
    """Jämförelse av en A-vägd totalnivå."""

    storhet: str
    beraknad_db: float
    facit_db: float
    klass: Utfall

    @property
    def avvikelse_db(self) -> float:
        return self.beraknad_db - self.facit_db


@dataclass(frozen=True)
class Bandjamforelse:
    """Jämförelse av ett tersbandsspektrum."""

    storhet: str
    avvikelse_db: Flyttal  # beräknat − facit, form (ANTAL_BAND,)
    band_over_grans: tuple[int, ...]  # bandindex över gränsen
    ursaktade_band: tuple[int, ...]  # därav förklarade av en godtagbar dippförskjutning
    dippforskjutning_band: int | None  # +1 = dippen ligger ett band högre i beräkningen
    klass: Utfall

    @property
    def max_avvikelse_db(self) -> float:
        return float(np.max(np.abs(self.avvikelse_db)))


@dataclass(frozen=True)
class Seriejamforelse:
    """Samlad jämförelse för en facitserie."""

    serie_id: str
    totaler: tuple[Totaljamforelse, ...]
    band: tuple[Bandjamforelse, ...]
    saknade_storheter: tuple[str, ...]  # finns i facit men är inte beräknade
    klass: Utfall
    anmarkningar: tuple[str, ...] = ()

    @property
    def max_total_avvikelse_db(self) -> float | None:
        if not self.totaler:
            return None
        return max(abs(t.avvikelse_db) for t in self.totaler)

    @property
    def max_band_avvikelse_db(self) -> float | None:
        if not self.band:
            return None
        return max(b.max_avvikelse_db for b in self.band)


def lokala_minima(spektrum_db: Flyttal) -> tuple[int, ...]:
    """Bandindex för inre lokala minima (strikt lägre än båda grannbanden)."""
    s = spektrum_db
    return tuple(i for i in range(1, s.shape[0] - 1) if s[i] < s[i - 1] and s[i] < s[i + 1])


def _sammanhangande(index: tuple[int, ...]) -> list[tuple[int, ...]]:
    grupper: list[list[int]] = []
    for i in index:
        if grupper and i == grupper[-1][-1] + 1:
            grupper[-1].append(i)
        else:
            grupper.append([i])
    return [tuple(g) for g in grupper]


def _dippforskjutningar(
    beraknad_db: Flyttal, facit_db: Flyttal, regler: Avvikelseregler
) -> list[tuple[int, int]]:
    """Par (dipp i facit, dipp i beräkningen) som är förskjutna högst det tillåtna antalet band."""
    dipp_facit = set(lokala_minima(facit_db))
    dipp_ber = set(lokala_minima(beraknad_db))
    par: list[tuple[int, int]] = []
    for i in sorted(dipp_facit - dipp_ber):
        for steg in range(1, regler.dipp_tillaten_forskjutning_band + 1):
            for j in (i - steg, i + steg):
                if j in dipp_ber and j not in dipp_facit:
                    par.append((i, j))
    return par


def jamfor_spektrum(
    storhet: str, beraknad_db: Flyttal, facit_db: Flyttal, regler: Avvikelseregler
) -> Bandjamforelse:
    """Klassar ett tersbandsspektrum: godkänd eller ska utredas (ingen hård gräns finns)."""
    if beraknad_db.shape != (ANTAL_BAND,) or facit_db.shape != (ANTAL_BAND,):
        raise BullerFel(f"Spektrum '{storhet}' ska ha {ANTAL_BAND} band.")
    if not bool(np.all(np.isfinite(beraknad_db))):
        raise BullerFel(f"Beräknat spektrum '{storhet}' innehåller icke-ändliga tal.")
    avvikelse = beraknad_db - facit_db
    over = tuple(int(i) for i in np.flatnonzero(np.abs(avvikelse) > regler.band_grans_utredas_db))
    ursaktade: list[int] = []
    forskjutning: int | None = None
    if over:
        par = _dippforskjutningar(beraknad_db, facit_db, regler)
        n = regler.dipp_narhet_band
        for grupp in _sammanhangande(over):
            for i, j in par:
                lagst, hogst = min(i, j) - n, max(i, j) + n
                if all(lagst <= b <= hogst for b in grupp):
                    ursaktade.extend(grupp)
                    forskjutning = j - i
                    break
    kvar = [b for b in over if b not in ursaktade]
    return Bandjamforelse(
        storhet=storhet,
        avvikelse_db=avvikelse,
        band_over_grans=over,
        ursaktade_band=tuple(ursaktade),
        dippforskjutning_band=forskjutning,
        klass=Utfall.SKA_UTREDAS if kvar else Utfall.GODKAND,
    )


def jamfor_total(
    storhet: str, beraknad_db: float, facit_db: float, regler: Avvikelseregler
) -> Totaljamforelse:
    """Klassar en A-vägd totalnivå: godkänd, ska utredas eller underkänd."""
    if not np.isfinite(beraknad_db):
        raise BullerFel(f"Beräknad totalnivå '{storhet}' är inte ändlig.")
    avvikelse = abs(beraknad_db - facit_db)
    hard_grans = regler.total_grans_underkand_db
    if hard_grans is not None and avvikelse > hard_grans:
        klass = Utfall.UNDERKAND
    elif avvikelse > regler.total_grans_utredas_db:
        klass = Utfall.SKA_UTREDAS
    else:
        klass = Utfall.GODKAND
    return Totaljamforelse(storhet, float(beraknad_db), float(facit_db), klass)


def jamfor_serie(
    beraknade_totaler_db: Mapping[str, float],
    beraknade_spektrum_db: Mapping[str, Flyttal],
    facit: Facitserie,
    regler: Avvikelseregler,
    *,
    ar_facit: bool,
    kravda_storheter: Collection[str] | None = None,
) -> Seriejamforelse:
    """Jämför en beräknad serie med facit.

    Alla storheter i facit krävs om inte `kravda_storheter` anger ett urval. En krävd storhet
    som inte är beräknad gör att serien inte kan bli godkänd (den ska då utredas).
    Med `ar_facit = False` blir klassen alltid `Utfall.EJ_FACIT`; avvikelserna redovisas
    ändå som upplysning.
    """
    totaler: list[Totaljamforelse] = []
    band: list[Bandjamforelse] = []
    saknade: list[str] = []
    anmarkningar: list[str] = []

    undantagna = regler.undantagna_storheter()
    anmarkningar += [
        f"Känt fel i testfallen ({u.id}, {u.kalla}): {u.text}"
        for u in regler.undantag
        if u.verkan == "anmarkning"
    ]
    for storhet, facit_db in facit.totalnivaer_db.items():
        if kravda_storheter is not None and storhet not in kravda_storheter:
            continue
        if storhet in undantagna:
            u = undantagna[storhet]
            anmarkningar.append(f"{storhet} jämförs inte ({u.id}, {u.kalla}): {u.text}")
            continue
        if storhet not in regler.total_storheter:
            if regler.total_storheter and not regler.band_jamfors:
                continue  # varianten gör bara vissa totalnivåer obligatoriska
            raise BullerFel(
                f"Totalnivån '{storhet}' i facit omfattas inte av reglerna ({regler.kalla})."
            )
        if storhet not in beraknade_totaler_db:
            saknade.append(storhet)
            continue
        totaler.append(jamfor_total(storhet, beraknade_totaler_db[storhet], facit_db, regler))

    for storhet, facit_spektrum in facit.spektrum_db.items():
        if not regler.band_jamfors or storhet in regler.band_jamfors_inte:
            continue
        if kravda_storheter is not None and storhet not in kravda_storheter:
            continue
        if storhet not in beraknade_spektrum_db:
            saknade.append(storhet)
            continue
        b = jamfor_spektrum(storhet, beraknade_spektrum_db[storhet], facit_spektrum, regler)
        band.append(b)
        if b.ursaktade_band:
            namn = ", ".join(f"{TERSBAND_HZ[i]:g} Hz" for i in b.ursaktade_band)
            anmarkningar.append(
                f"{storhet}: markeffektdippen förskjuten {b.dippforskjutning_band:+d} band; "
                f"avvikelser i {namn} godtas i regel."
            )

    if saknade:
        anmarkningar.append("Inte beräknat: " + ", ".join(saknade) + ".")
    if not totaler and not band:
        anmarkningar.append("Ingen storhet kunde jämföras.")

    if not ar_facit:
        klass = Utfall.EJ_FACIT
        totaler = [Totaljamforelse(t.storhet, t.beraknad_db, t.facit_db, klass) for t in totaler]
        band = [
            Bandjamforelse(
                b.storhet,
                b.avvikelse_db,
                b.band_over_grans,
                b.ursaktade_band,
                b.dippforskjutning_band,
                klass,
            )
            for b in band
        ]
    else:
        klasser = [t.klass for t in totaler] + [b.klass for b in band]
        if saknade or not klasser:
            klasser.append(Utfall.SKA_UTREDAS)
        klass = samsta(klasser)

    return Seriejamforelse(
        serie_id=facit.id,
        totaler=tuple(totaler),
        band=tuple(band),
        saknade_storheter=tuple(saknade),
        klass=klass,
        anmarkningar=tuple(anmarkningar),
    )
