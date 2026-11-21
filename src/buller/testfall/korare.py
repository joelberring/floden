"""Körare för Nord2000-testfall: kärna + emissionsmodell + fall → utfall per fall.

Köraren räknar bara falltypen rak väg (`nord2000_rak_vag`). Övriga typer saknar geometri
(den ligger i Excel-filer som inte finns) och redovisas som ej körbara eller ej facit.

Beräkningen för rak väg följer KB2:

1. Källinjen delas i källpunkter med konstant vinkelsteg sett från mottagaren, symmetriskt
   kring normalen (Miljøprojekt 1335/2010 kap. 2: 179 punkter, 1° upplösning).
2. Källpunkten i vinkeln θ representerar vägsträckan d·(tan(θ + Δ/2) − tan(θ − Δ/2)), där d är
   det vinkelräta avståndet (ren geometri).
3. Terrängprofilen mot källpunkten är tvärsnittet utdraget med 1 / cos θ.
4. Källstyrkan korrigeras för fallets beläggning och lufttemperatur (`forhallanden_for_fall`).
5. `L[b] = 10·lg Σ 10^((L_W[k, j, b] + H[k, j, b]) / 10)` över källpunkter k och delkällor j.

Exponeringsnivån för en fordonspassage fås ur definitionen L_E = L_eq,T + 10·lg(T / 1 s) med
ett fordon under T, alltså L_E = L_eq,T(N) + 10·lg(T / (N · 1 s)). Sambandet stämmer med
talen i rapportens bilaga A (skillnaden L_E − L_eq,24h är 9,37 dB = 10·lg(86 400 / 10 000)).

Maximalnivå beräknas inte (se `buller.emission`).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import numpy as np

from buller.emission.modell import Trafikflode, Vagforhallanden
from buller.testfall.format import (
    TYP_RAK_VAG,
    Facit,
    Facitserie,
    N2kFall,
    hitta_fall,
    las_facit,
)
from buller.testfall.jamforelse import (
    STANDARDVARIANT,
    Avvikelseregler,
    Seriejamforelse,
    Utfall,
    jamfor_serie,
    las_regler,
    samsta,
)
from buller.typer import ANTAL_BAND, BullerFel, Flyttal, P2PKarna, Vader

_VADERFALT = frozenset(Vader.__dataclass_fields__)
_VILLKOR_MOTTAGARHOJD = "mottagare_hojd_m"
VILLKOR_REGELVARIANT = "regelvariant"


class Emissionsmodell(Protocol):
    """Det köraren behöver av en källmodell (uppfylls av `buller.emission.Vagemission`)."""

    @property
    def delkallhojder_m(self) -> tuple[float, ...]: ...

    def ljudeffekt_db(
        self,
        trafik: Sequence[Trafikflode],
        segment_langd_m: float,
        period_s: float,
        delkallhojder_m: Sequence[float] | None = None,
        forhallanden: Vagforhallanden | None = None,
    ) -> Flyttal:
        """Form (antal delkällor, ANTAL_BAND), dB re 1 pW."""
        ...


@dataclass(frozen=True)
class Berakning:
    """Beräknade nivåer för en serie."""

    totalnivaer_db: dict[str, float]
    spektrum_db: dict[str, Flyttal]


@dataclass(frozen=True)
class Fallresultat:
    """Utfall för ett fall."""

    fall_id: str
    namn: str
    utfall: Utfall
    serier: tuple[Seriejamforelse, ...]
    karna: str
    karna_nord2000_konform: bool
    kommentar: str = ""

    @property
    def max_total_avvikelse_db(self) -> float | None:
        varden = [s.max_total_avvikelse_db for s in self.serier]
        return max((v for v in varden if v is not None), default=None)

    @property
    def max_band_avvikelse_db(self) -> float | None:
        varden = [s.max_band_avvikelse_db for s in self.serier]
        return max((v for v in varden if v is not None), default=None)


def _standard_a_vagning_db() -> Flyttal:
    try:
        from buller.akustik.band import a_vagning_db
    except ImportError as exc:  # pragma: no cover - beror på andra paket
        raise BullerFel(
            "A-vägningen saknas: `buller.akustik.band.a_vagning_db` (paket B4a) gick inte att "
            "importera. Ange `a_vagning_db` till köraren."
        ) from exc
    return np.asarray(a_vagning_db(), dtype=np.float64)


def _energisumma_db(nivaer_db: Flyttal, axis: int | None = None) -> Flyttal:
    with np.errstate(divide="ignore"):
        return np.asarray(10.0 * np.log10(np.sum(10.0 ** (nivaer_db / 10.0), axis=axis)))


def _periodnamn(period_h: float) -> str:
    return f"{period_h:g}h"


def _fall_med_villkor(fall: N2kFall, serie: Facitserie) -> N2kFall:
    """Tillämpar seriens villkor (mottagarhöjd, väderfält) på fallet. Okända villkor ger fel."""
    if fall.vader is None:
        raise BullerFel(f"Fall {fall.id} saknar väder.")
    vader_nytt: dict[str, float] = {}
    hojd = fall.mottagare_hojd_m
    for nyckel, varde in serie.villkor.items():
        if nyckel == VILLKOR_REGELVARIANT:
            continue
        if isinstance(varde, bool | str):
            raise BullerFel(
                f"Villkoret '{nyckel}' i serien '{serie.id}' kan köraren inte tillämpa."
            )
        if nyckel == _VILLKOR_MOTTAGARHOJD:
            hojd = float(varde)
        elif nyckel in _VADERFALT:
            vader_nytt[nyckel] = float(varde)
        else:
            raise BullerFel(
                f"Villkoret '{nyckel}' i serien '{serie.id}' kan köraren inte tillämpa."
            )
    return replace(fall, mottagare_hojd_m=hojd, vader=replace(fall.vader, **vader_nytt))


def forhallanden_for_fall(fall: N2kFall) -> Vagforhallanden | None:
    """Vägförhållanden ur fallets `[ovrigt]` (`belaggning`, `stenstorlek_mm`) och lufttemperatur.

    Testfallen anger beläggningen DAC 12 och lufttemperaturen t0. Att facit är räknat med
    temperaturkorrektionen vid t0 är ett antagande (se loggen); utan beläggning i fallet
    används referensförhållandet.
    """
    belaggning = fall.ovrigt.get("belaggning")
    stenstorlek = fall.ovrigt.get("stenstorlek_mm")
    if belaggning is None and stenstorlek is None:
        return None
    if not isinstance(belaggning, str) or isinstance(stenstorlek, bool | str | None):
        raise BullerFel(f"Fall {fall.id}: [ovrigt] belaggning och stenstorlek_mm hör ihop.")
    temperatur = fall.vader.temperatur_c if fall.vader is not None else None
    return Vagforhallanden(belaggning, float(stenstorlek), lufttemperatur_c=temperatur)


def berakna_rak_vag(
    karna: P2PKarna, emission: Emissionsmodell, fall: N2kFall, a_vagning_db: Flyttal
) -> Berakning:
    """Beräknar L_eq, L_E och A-vägda totalnivåer för ett fall med rak väg."""
    if fall.typ != TYP_RAK_VAG:
        raise BullerFel(f"Köraren kan bara räkna '{TYP_RAK_VAG}', inte '{fall.typ}'.")
    if not fall.indata_fullstandig:
        raise BullerFel(f"Indata är ofullständig: {'; '.join(fall.indata_saknas)}")
    if fall.vag is None or fall.vader is None or fall.mottagare_x_m is None:
        raise BullerFel(f"Fall {fall.id} saknar väg, väder eller mottagare.")
    if a_vagning_db.shape != (ANTAL_BAND,):
        raise BullerFel(f"a_vagning_db ska ha {ANTAL_BAND} värden.")
    if fall.vader.vind_m_s != 0.0:
        raise BullerFel(
            "Fallet har vind, men hur vindriktningen i testfallens blad ska räknas om till "
            "vindkomponent längs varje utbredningsväg är inte avläst ur någon källa. "
            "Köraren räknar därför bara fall utan vind."
        )
    perioder = {post.period for post in fall.trafik}
    if len(perioder) != 1:
        raise BullerFel("Köraren räknar bara fall där all trafik ligger i en och samma period.")
    period_h = fall.perioder_h[perioder.pop()]
    period_s = period_h * 3600.0
    trafik = [Trafikflode(p.kategori, p.antal_fordon, p.hastighet_km_h) for p in fall.trafik]

    forhallanden = forhallanden_for_fall(fall)
    hojder = emission.delkallhojder_m
    avstand_m = fall.mottagare_x_m - fall.vag.kalla_x_m
    steg = math.radians(fall.vag.vinkelupplosning_grader)
    antal = fall.vag.antal_kallpunkter
    vinklar = (np.arange(antal) - (antal - 1) / 2.0) * steg

    bidrag = np.empty((antal, len(hojder), ANTAL_BAND))
    for k, vinkel in enumerate(vinklar):
        langd_m = avstand_m * (math.tan(vinkel + steg / 2.0) - math.tan(vinkel - steg / 2.0))
        effekt_db = emission.ljudeffekt_db(trafik, langd_m, period_s, hojder, forhallanden)
        if effekt_db.shape != (len(hojder), ANTAL_BAND):
            raise BullerFel(
                f"Emissionsmodellen gav form {effekt_db.shape}, väntade "
                f"({len(hojder)}, {ANTAL_BAND})."
            )
        for j, hojd in enumerate(hojder):
            profil = fall.profil(kalla_hojd_m=hojd, stracka=1.0 / math.cos(vinkel))
            overforing_db = np.asarray(karna.overforing_db(profil, fall.vader))
            if overforing_db.shape != (ANTAL_BAND,):
                raise BullerFel(f"Kärnan {karna.namn} gav form {overforing_db.shape}.")
            bidrag[k, j, :] = effekt_db[j, :] + overforing_db

    leq_db = _energisumma_db(bidrag.reshape(-1, ANTAL_BAND), axis=0)
    if not bool(np.all(np.isfinite(leq_db))):
        raise BullerFel("Beräkningen gav icke-ändliga nivåer.")
    namn = _periodnamn(period_h)
    spektrum = {f"Leq_{namn}": leq_db}
    totaler = {f"LAeq_{namn}": float(_energisumma_db(leq_db + a_vagning_db))}
    if len(trafik) == 1 and trafik[0].antal_fordon > 0.0:
        le_db = leq_db + 10.0 * math.log10(period_s / trafik[0].antal_fordon)
        spektrum["LE"] = le_db
        totaler["LAE"] = float(_energisumma_db(le_db + a_vagning_db))
    return Berakning(totaler, spektrum)


def kor_fall(
    karna: P2PKarna,
    emission: Emissionsmodell,
    fall: N2kFall,
    facit: Facit | None = None,
    *,
    regler: Avvikelseregler | None = None,
    a_vagning_db: Flyttal | None = None,
    kravda_storheter: Collection[str] | None = None,
) -> Fallresultat:
    """Kör ett fall och jämför med facit.

    Utan `regler` används regelverket som facit anger (och seriens `regelvariant`).
    Fall vars facit har `ar_facit = false` får alltid utfallet `Utfall.EJ_FACIT`, oavsett hur
    beräkningen går. Fall som inte går att räkna (ofullständig indata, tom källmodell, typ som
    köraren inte stöder) får `Utfall.EJ_KORBAR` med skälet i kommentaren.
    """
    if facit is None:
        if fall.mapp is None:
            raise BullerFel(f"Fall {fall.id} saknar mapp; ange facit.")
        facit = las_facit(fall.mapp)
    serier: list[Seriejamforelse] = []
    hinder: list[str] = []
    try:
        vagning = a_vagning_db if a_vagning_db is not None else _standard_a_vagning_db()
        for serie in facit.serier:
            serieregler = regler or las_regler(
                regelverk=facit.regelverk,
                variant=str(serie.villkor.get(VILLKOR_REGELVARIANT, STANDARDVARIANT)),
            )
            berakning = berakna_rak_vag(karna, emission, _fall_med_villkor(fall, serie), vagning)
            serier.append(
                jamfor_serie(
                    berakning.totalnivaer_db,
                    berakning.spektrum_db,
                    serie,
                    serieregler,
                    ar_facit=facit.ar_facit,
                    kravda_storheter=kravda_storheter,
                )
            )
    except BullerFel as exc:
        hinder.append(str(exc))

    kommentarer: list[str] = []
    if not facit.ar_facit:
        utfall = Utfall.EJ_FACIT
        kommentarer.append(f"Ej facit: {facit.ar_facit_skal}")
        if hinder:
            kommentarer.append(f"Inte beräknat: {hinder[0]}")
    elif hinder:
        utfall = Utfall.EJ_KORBAR
        kommentarer.append(hinder[0])
    else:
        utfall = samsta([s.klass for s in serier])
    for s in serier:
        kommentarer += [f"[{s.serie_id}] {a}" for a in s.anmarkningar]
    if not karna.nord2000_konform:
        kommentarer.append("Kärnan är inte Nord2000-konform.")

    return Fallresultat(
        fall_id=fall.id,
        namn=fall.namn,
        utfall=utfall,
        serier=tuple(serier) if not hinder else (),
        karna=f"{karna.namn} {karna.version}",
        karna_nord2000_konform=karna.nord2000_konform,
        kommentar=" ".join(kommentarer),
    )


def kor_alla(
    karna: P2PKarna,
    emission: Emissionsmodell,
    rot: str | Path,
    *,
    regler: Avvikelseregler | None = None,
    a_vagning_db: Flyttal | None = None,
) -> list[Fallresultat]:
    """Kör alla Nord2000-fall under `rot` (t.ex. `testsvit/N_nord2000`).

    Utan `regler` används det regelverk som varje falls facit anger.
    """
    return [
        kor_fall(karna, emission, fall, regler=regler, a_vagning_db=a_vagning_db)
        for fall in hitta_fall(rot)
    ]


def _db(varde: float | None) -> str:
    return "–" if varde is None else f"{varde:.2f}".replace(".", ",")


def sammanstallning_md(resultat: Sequence[Fallresultat]) -> str:
    """Sammanställning i Markdown: antal per resultatklass och en rad per fall."""
    antal = {u: sum(1 for r in resultat if r.utfall is u) for u in Utfall}
    rader = [
        "# Testsvit grupp N – Nord2000",
        "",
        f"Antal fall: {len(resultat)}. "
        + ", ".join(f"{u.value}: {antal[u]}" for u in Utfall)
        + ".",
        "",
        "Fall som är \"ej facit\" räknas varken som godkända eller underkända; avvikelserna "
        "visas bara som upplysning.",
        "",
        "| ID | Namn | Kärna | Utfall | Max avvikelse total (dB) | Max avvikelse band (dB) "
        "| Kommentar |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in resultat:
        kommentar = r.kommentar.replace("|", "/").replace("\n", " ")
        rader.append(
            f"| {r.fall_id} | {r.namn} | {r.karna} | {r.utfall.value} "
            f"| {_db(r.max_total_avvikelse_db)} | {_db(r.max_band_avvikelse_db)} | {kommentar} |"
        )
    return "\n".join(rader) + "\n"
