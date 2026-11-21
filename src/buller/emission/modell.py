"""Källmodell för vägtrafik (Nord2000 Road): ljudeffektnivå per segment, delkälla och band.

Alla koefficienter läses ur en versionerad TOML-fil under `data/`. Primärkällan är
SP Rapport 2006:12 (Jonasson); varje tabell har dokument, tabell och sida i datafilen.
En datafil med tomma tabeller går att läsa, men modellen kastar då `BullerFel` som säger
vad som saknas.

Infört ur SP Rapport 2006:12:

- rullbuller (ekv. 2.1/A.1) och drivlinebuller (ekv. 2.7/A.3) per tersband, tabell A.1,
- fördelning på delkällhöjder (avsnitt 2.2),
- antal axlar för kategori 3 (ekv. 2.2/A.2),
- korrektion för svenska, norska och finska vägar (tabell A.2),
- beläggning DAC/SMA med stenstorlek (ekv. 2.3), beläggningens ålder (ekv. 2.4) och
  lufttemperatur för DAC (ekv. 2.5),
- acceleration (ekv. 2.8).

Inte infört (se `docs/buller/logg/B1-B5.md`): direktivitet (ekv. 2.9–2.12, tabell 2.4;
kräver vinklar mot mottagaren), våt vägbana (ekv. 2.6), dubbdäck (tabell A.3 är inläst men
används inte), porösa och andra beläggningar (avsnitt 8.3), extra delkälla för högt avgasrör,
motorbroms och lutning som ekvivalent acceleration, samt maximalnivå (avsnitt 2.6).

Från fordon till segment (energibalans, inte hämtad ur metoddokumenten): ett fordon med
hastigheten v befinner sig Δl / v sekunder på ett segment med längden Δl. Med N fordon under
tiden T blir L_W,segment = L_W,fordon + 10·lg(N · Δl / (v · T)), med v i m/s, T i s, Δl i m.

Resultatet har formen `(antal delkällor, ANTAL_BAND)` och passar `L_W` i KB2: en punktkälla
per delkällhöjd som representerar segmentet, i dB re 1 pW. En delkällhöjd som en kategori
inte använder får ingen effekt från den kategorin (−inf om ingen kategori använder den).
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from buller.typer import ANTAL_BAND, BullerFel, Flyttal

DATAFIL = Path(__file__).parent / "data" / "nord2000_road_v2.toml"
BULLERTYPER: tuple[str, ...] = ("rull", "drivlina")
_HOJDTOLERANS_M = 1.0e-9


def _form_a_plus_b_lg(a_db: Flyttal, b_db: Flyttal, v_km_h: float, v_ref_km_h: float) -> Flyttal:
    return a_db + b_db * math.log10(v_km_h / v_ref_km_h)


def _form_a_plus_b_linjar(
    a_db: Flyttal, b_db: Flyttal, v_km_h: float, v_ref_km_h: float
) -> Flyttal:
    return a_db + b_db * (v_km_h - v_ref_km_h) / v_ref_km_h


# Funktionsformer för nivå som funktion av hastighet. Datafilen anger vilken som gäller för
# varje tabell: den första är ekvation (2.1)/(A.1) och den andra (2.7)/(A.3) i SP 2006:12.
FORMTYPER: Mapping[str, Callable[[Flyttal, Flyttal, float, float], Flyttal]] = {
    "a_plus_b_lg_v_genom_vref": _form_a_plus_b_lg,
    "a_plus_b_ganger_v_minus_vref_genom_vref": _form_a_plus_b_linjar,
}


@dataclass(frozen=True)
class Trafikflode:
    """Trafik för en fordonskategori under den tidsperiod beräkningen gäller."""

    kategori: int
    antal_fordon: float  # under perioden
    hastighet_km_h: float
    antal_axlar: float | None = None  # bara kategori 3; None = tabellens antal (4)
    acceleration_m_s2: float = 0.0  # positiv = acceleration (ekv. 2.8)

    def __post_init__(self) -> None:
        if self.antal_axlar is not None and not self.antal_axlar > 0.0:
            raise BullerFel("antal_axlar ska vara större än noll.")
        if not math.isfinite(self.acceleration_m_s2):
            raise BullerFel("acceleration_m_s2 ska vara ett ändligt tal.")
        if not math.isfinite(self.antal_fordon) or self.antal_fordon < 0.0:
            raise BullerFel("antal_fordon ska vara ett ändligt tal ≥ 0.")
        if not math.isfinite(self.hastighet_km_h) or self.hastighet_km_h <= 0.0:
            raise BullerFel("hastighet_km_h ska vara större än noll.")


@dataclass(frozen=True)
class Vagforhallanden:
    """Förhållanden som korrigerar källstyrkan. Standardvärdena är referensförhållandet.

    Referensförhållandet i SP 2006:12 är konstant hastighet, 20 °C och en beläggning som är
    ett medel av DAC 0/11 och SMA 0/11, minst två år gammal.
    """

    belaggning: str | None = None  # "DAC" eller "SMA"; None = referensbeläggningen
    stenstorlek_mm: float | None = None  # största stenstorlek; krävs med belaggning
    belaggningsalder_ar: float | None = None  # None = minst två år
    lufttemperatur_c: float | None = None  # None = referenstemperaturen
    land: str | None = None  # "SE", "NO" eller "FI" ger korrektionen i tabell A.2

    def __post_init__(self) -> None:
        if (self.belaggning is None) != (self.stenstorlek_mm is None):
            raise BullerFel("belaggning och stenstorlek_mm ska anges tillsammans.")
        if self.belaggningsalder_ar is not None and self.belaggningsalder_ar < 0.0:
            raise BullerFel("belaggningsalder_ar får inte vara negativ.")


@dataclass(frozen=True)
class Korrektionsdata:
    """Korrektionernas konstanter ur datafilen. `None` betyder att avsnittet saknas."""

    axlar: Mapping[str, Any] | None = None
    regional: Mapping[str, Any] | None = None
    belaggning: Mapping[str, Any] | None = None
    belaggningsalder: Mapping[str, Any] | None = None
    temperatur: Mapping[str, Any] | None = None
    acceleration: Mapping[str, Any] | None = None
    dubbdack: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Bullertyptabell:
    """Koefficienter för en bullertyp (rull eller drivlina) i en fordonskategori."""

    formtyp: str
    referenshastighet_km_h: float
    a_db: Flyttal  # form (ANTAL_BAND,)
    b_db: Flyttal  # form (ANTAL_BAND,)
    fordelning: Flyttal  # form (antal delkällor, ANTAL_BAND), summa 1 per band
    kalla: str

    def ljudeffekt_per_fordon_db(self, hastighet_km_h: float) -> Flyttal:
        """Ljudeffektnivå per fordon och tersband, dB re 1 pW."""
        return FORMTYPER[self.formtyp](
            self.a_db, self.b_db, hastighet_km_h, self.referenshastighet_km_h
        )


@dataclass(frozen=True)
class Kategoridata:
    """Koefficienttabeller för en fordonskategori."""

    kategori: int
    namn: str
    tabeller: Mapping[str, Bullertyptabell]  # nycklar: BULLERTYPER


@dataclass(frozen=True)
class Emissionsdata:
    """Inläst källmodellsdata. `saknas` är tom bara om alla tabeller är ifyllda."""

    namn: str
    dataversion: str
    syntetisk: bool
    verifierad_av_manniska: bool
    kallor: tuple[Mapping[str, str], ...]
    delkallhojder_m: tuple[float, ...]
    delkallor_kalla: str
    kategorinamn: Mapping[int, str]
    kategorier: Mapping[int, Kategoridata]  # bara fullständigt ifyllda kategorier
    saknas: tuple[str, ...]
    fil: Path
    korrektioner: Korrektionsdata = Korrektionsdata()

    @property
    def fullstandig(self) -> bool:
        return not self.saknas


def _fel(fil: Path, text: str) -> BullerFel:
    return BullerFel(f"{fil}: {text}")


def _bandlista(varde: Any, namn: str, fil: Path) -> Flyttal:
    if not isinstance(varde, list) or len(varde) != ANTAL_BAND:
        raise _fel(fil, f"{namn} ska ha {ANTAL_BAND} värden (ett per tersband).")
    if any(isinstance(v, bool) or not isinstance(v, int | float) for v in varde):
        raise _fel(fil, f"{namn} ska bara innehålla tal.")
    tal = np.asarray(varde, dtype=np.float64)
    if not bool(np.all(np.isfinite(tal))):
        raise _fel(fil, f"{namn} innehåller icke-ändliga tal.")
    return tal


def _fordelning(varde: list[Any], antal_delkallor: int, namn: str, fil: Path) -> Flyttal:
    if len(varde) != antal_delkallor:
        raise _fel(fil, f"{namn} ska ha ett värde (eller en bandlista) per delkälla.")
    if all(isinstance(v, list) for v in varde):
        andelar = np.stack([_bandlista(v, namn, fil) for v in varde])
    elif all(isinstance(v, int | float) and not isinstance(v, bool) for v in varde):
        andelar = np.repeat(np.asarray(varde, dtype=np.float64)[:, None], ANTAL_BAND, axis=1)
    else:
        raise _fel(fil, f"{namn} ska vara en lista med tal eller en lista med bandlistor.")
    if bool(np.any(andelar < 0.0)) or not bool(np.allclose(andelar.sum(axis=0), 1.0, atol=1e-6)):
        raise _fel(fil, f"{namn} ska vara andelar ≥ 0 som summerar till 1 över delkällorna.")
    return andelar


def _las_bullertyp(
    post: Any, antal_delkallor: int, namn: str, fil: Path
) -> tuple[Bullertyptabell | None, list[str]]:
    """Läser en bullertypstabell. Tomma fält ger en lista över vad som saknas."""
    if not isinstance(post, dict):
        return None, [f"{namn}: hela tabellen"]
    saknas = [
        f"{namn}.{falt}"
        for falt in ("formtyp", "referenshastighet_km_h", "a_db", "b_db", "fordelning", "kalla")
        if post.get(falt) in (None, "", [])
    ]
    if saknas:
        return None, saknas
    formtyp = post["formtyp"]
    if formtyp not in FORMTYPER:
        raise _fel(fil, f"{namn}.formtyp '{formtyp}' är okänd. Kända: {', '.join(FORMTYPER)}.")
    v_ref = post["referenshastighet_km_h"]
    if isinstance(v_ref, bool) or not isinstance(v_ref, int | float) or v_ref <= 0:
        raise _fel(fil, f"{namn}.referenshastighet_km_h ska vara ett tal större än noll.")
    if not isinstance(post["fordelning"], list) or not isinstance(post["kalla"], str):
        raise _fel(fil, f"{namn}: fordelning ska vara en lista och kalla en text.")
    tabell = Bullertyptabell(
        formtyp=formtyp,
        referenshastighet_km_h=float(v_ref),
        a_db=_bandlista(post["a_db"], f"{namn}.a_db", fil),
        b_db=_bandlista(post["b_db"], f"{namn}.b_db", fil),
        fordelning=_fordelning(post["fordelning"], antal_delkallor, f"{namn}.fordelning", fil),
        kalla=post["kalla"],
    )
    return tabell, []


def las_emissionsdata(fil: str | Path = DATAFIL) -> Emissionsdata:
    """Läser källmodellens datafil.

    Tomma tabeller är tillåtna vid inläsning och räknas upp i `Emissionsdata.saknas`;
    `Vagemission.ljudeffekt_db` vägrar sedan räkna. Felaktigt ifyllda tabeller (fel antal
    band, okänd formtyp, fördelning som inte summerar till 1) ger `BullerFel` direkt.
    """
    sokvag = Path(fil)
    try:
        data: dict[str, Any] = tomllib.loads(sokvag.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BullerFel(f"Kunde inte läsa emissionsdata {sokvag}: {exc}") from exc

    meta = data.get("meta")
    delkallor = data.get("delkallor")
    kategorier_ra = data.get("kategorier")
    if not isinstance(meta, dict) or not isinstance(delkallor, dict):
        raise _fel(sokvag, "saknar [meta] eller [delkallor].")
    if not isinstance(kategorier_ra, dict) or not kategorier_ra:
        raise _fel(sokvag, "saknar [kategorier].")
    for falt in ("namn", "dataversion", "syntetisk", "verifierad_av_manniska"):
        if falt not in meta:
            raise _fel(sokvag, f"[meta] saknar {falt}.")
    if not isinstance(meta["syntetisk"], bool) or not isinstance(
        meta["verifierad_av_manniska"], bool
    ):
        raise _fel(sokvag, "[meta] syntetisk och verifierad_av_manniska ska vara sant/falskt.")

    hojder_ra = delkallor.get("hojder_m")
    if not isinstance(hojder_ra, list) or not hojder_ra:
        raise _fel(sokvag, "[delkallor] hojder_m saknas eller är tom.")
    if any(isinstance(h, bool) or not isinstance(h, int | float) or h < 0 for h in hojder_ra):
        raise _fel(sokvag, "[delkallor] hojder_m ska vara tal ≥ 0.")
    hojder = tuple(float(h) for h in hojder_ra)
    if list(hojder) != sorted(set(hojder)):
        raise _fel(sokvag, "[delkallor] hojder_m ska vara strikt växande.")

    saknas: list[str] = []
    kategorinamn: dict[int, str] = {}
    kategorier: dict[int, Kategoridata] = {}
    for nyckel, post in kategorier_ra.items():
        if not nyckel.isdigit() or not isinstance(post, dict):
            raise _fel(sokvag, f"[kategorier.{nyckel}] ska vara en tabell med heltalsnamn.")
        kategori = int(nyckel)
        kategorinamn[kategori] = str(post.get("namn", ""))
        tabeller: dict[str, Bullertyptabell] = {}
        for bullertyp in BULLERTYPER:
            namn = f"kategorier.{kategori}.{bullertyp}"
            tabell, saknade = _las_bullertyp(post.get(bullertyp), len(hojder), namn, sokvag)
            saknas.extend(saknade)
            if tabell is not None:
                tabeller[bullertyp] = tabell
        if len(tabeller) == len(BULLERTYPER):
            kategorier[kategori] = Kategoridata(kategori, kategorinamn[kategori], tabeller)

    korr = data.get("korrektioner", {})
    if not isinstance(korr, dict):
        raise _fel(sokvag, "[korrektioner] ska vara en tabell.")
    regional = data.get("regional_korrektion")
    if regional is not None:
        _bandlista(regional.get("aR_tillagg_db"), "regional_korrektion.aR_tillagg_db", sokvag)
    dubbdack = data.get("dubbdack")
    if dubbdack is not None:
        for falt in ("a_db", "b_db"):
            _bandlista(dubbdack.get(falt), f"dubbdack.{falt}", sokvag)
    korrektioner = Korrektionsdata(
        axlar=data.get("axlar"),
        regional=regional,
        belaggning=korr.get("belaggning"),
        belaggningsalder=korr.get("belaggningsalder"),
        temperatur=korr.get("temperatur"),
        acceleration=korr.get("acceleration"),
        dubbdack=dubbdack,
    )

    kallor_ra = meta.get("kallor", [])
    if not isinstance(kallor_ra, list):
        raise _fel(sokvag, "[[meta.kallor]] ska vara en lista.")
    return Emissionsdata(
        namn=str(meta["namn"]),
        dataversion=str(meta["dataversion"]),
        syntetisk=meta["syntetisk"],
        verifierad_av_manniska=meta["verifierad_av_manniska"],
        kallor=tuple({str(k): str(v) for k, v in post.items()} for post in kallor_ra),
        delkallhojder_m=hojder,
        delkallor_kalla=str(delkallor.get("kalla", "")),
        kategorinamn=kategorinamn,
        kategorier=kategorier,
        saknas=tuple(saknas),
        fil=sokvag,
        korrektioner=korrektioner,
    )


class Vagemission:
    """Källmodell för vägtrafik byggd på en inläst datafil."""

    def __init__(self, data: Emissionsdata | None = None) -> None:
        self.data = data if data is not None else las_emissionsdata()

    @property
    def delkallhojder_m(self) -> tuple[float, ...]:
        """Delkällornas höjder över vägbanan, i samma ordning som raderna i resultatet."""
        return self.data.delkallhojder_m

    def _kategori(self, kategori: int) -> Kategoridata:
        if kategori in self.data.kategorier:
            return self.data.kategorier[kategori]
        if kategori not in self.data.kategorinamn:
            kanda = ", ".join(str(k) for k in sorted(self.data.kategorinamn))
            raise BullerFel(f"Okänd fordonskategori {kategori}. Datafilen har: {kanda}.")
        saknade = [s for s in self.data.saknas if s.startswith(f"kategorier.{kategori}.")]
        raise BullerFel(
            f"Källmodellen saknar data för fordonskategori {kategori} i {self.data.fil}. "
            f"Tomma fält: {', '.join(saknade)}. Tabellerna ska matas in för hand ur "
            "källdokumentet (se docs/buller/kontroll/emission_tabeller.md); "
            "inga värden får gissas."
        )

    def _avsnitt(self, namn: str) -> Mapping[str, Any]:
        avsnitt: Mapping[str, Any] | None = getattr(self.data.korrektioner, namn)
        if avsnitt is None:
            raise BullerFel(
                f"Datafilen {self.data.fil} saknar konstanterna för korrektionen '{namn}'."
            )
        return avsnitt

    def rullkorrektion_db(
        self, flode: Trafikflode, forhallanden: Vagforhallanden
    ) -> Flyttal | float:
        """Tillägg till rullbullrets nivå (alla delkällor), per band eller som ett tal."""
        kategori = flode.kategori
        tillagg: Flyttal | float = 0.0
        if flode.antal_axlar is not None:
            axlar = self._avsnitt("axlar")
            if kategori != int(axlar["kategori"]):
                raise BullerFel(f"antal_axlar gäller bara fordonskategori {axlar['kategori']}.")
            tillagg += 10.0 * math.log10(
                flode.antal_axlar / float(axlar["tabellen_avser_antal_axlar"])
            )
        if forhallanden.land is not None:
            regional = self._avsnitt("regional")
            if forhallanden.land in regional["lander"]:
                tillagg = tillagg + np.asarray(regional["aR_tillagg_db"], dtype=np.float64)
            elif forhallanden.land != "DK":
                raise BullerFel(f"Okänt land '{forhallanden.land}' (DK, SE, NO eller FI).")
        if forhallanden.belaggning is not None and forhallanden.stenstorlek_mm is not None:
            ytor = self._avsnitt("belaggning")
            if forhallanden.belaggning not in ytor["RS_db"]:
                raise BullerFel(
                    f"Beläggningen '{forhallanden.belaggning}' saknas i datafilen "
                    f"({', '.join(ytor['RS_db'])})."
                )
            lagst, hogst = ytor["giltig_stenstorlek_mm"]
            if not lagst <= forhallanden.stenstorlek_mm <= hogst:
                raise BullerFel(
                    f"Beläggningskorrektionen gäller stenstorlek {lagst:g}–{hogst:g} mm."
                )
            if kategori in ytor["kategorier"]:
                tillagg += float(ytor["RS_db"][forhallanden.belaggning]) + float(
                    ytor["per_mm_db"]
                ) * (forhallanden.stenstorlek_mm - float(ytor["referens_stenstorlek_mm"]))
        if forhallanden.belaggningsalder_ar is not None:
            alder = self._avsnitt("belaggningsalder")
            t = forhallanden.belaggningsalder_ar
            if t <= float(alder["galler_till_ar"]) and kategori in alder["kategorier"]:
                tillagg -= float(alder["k2"]) * t * t + float(alder["k1"]) * t + float(alder["k0"])
        if forhallanden.lufttemperatur_c is not None:
            temperatur = self._avsnitt("temperatur")
            skillnad = float(temperatur["referens_c"]) - forhallanden.lufttemperatur_c
            if skillnad != 0.0:
                yta = forhallanden.belaggning
                if yta is None or yta not in temperatur["K_db_per_grad"]:
                    raise BullerFel(
                        "Temperaturkorrektionen kräver en beläggning med temperaturkoefficient "
                        f"i datafilen ({', '.join(temperatur['K_db_per_grad'])}); fick {yta}."
                    )
                faktor = 1.0 if kategori == 1 else float(temperatur["faktor_kategori_2_och_hogre"])
                tillagg += faktor * float(temperatur["K_db_per_grad"][yta]) * skillnad
        return tillagg

    def drivlinekorrektion_db(self, flode: Trafikflode) -> float:
        """Tillägg till drivlinebullrets nivå för acceleration (ekv. 2.8)."""
        if flode.acceleration_m_s2 == 0.0:
            return 0.0
        acceleration = self._avsnitt("acceleration")
        if abs(flode.acceleration_m_s2) > float(acceleration["storsta_belopp_m_s2"]):
            raise BullerFel(
                "Accelerationskorrektionen gäller bara belopp upp till "
                f"{acceleration['storsta_belopp_m_s2']:g} m/s²."
            )
        koefficienter = acceleration["C_db_per_m_s2"]
        if str(flode.kategori) not in koefficienter:
            raise BullerFel(f"Accelerationskoefficient saknas för kategori {flode.kategori}.")
        return float(koefficienter[str(flode.kategori)]) * flode.acceleration_m_s2

    def ljudeffekt_per_fordon_db(
        self,
        kategori: int,
        hastighet_km_h: float,
        forhallanden: Vagforhallanden | None = None,
        flode: Trafikflode | None = None,
    ) -> Flyttal:
        """Ljudeffektnivå för ett fordon per delkälla och band, form (antal delkällor, 27)."""
        if not math.isfinite(hastighet_km_h) or hastighet_km_h <= 0.0:
            raise BullerFel("hastighet_km_h ska vara större än noll.")
        data = self._kategori(kategori)
        flode = flode if flode is not None else Trafikflode(kategori, 1.0, hastighet_km_h)
        tillagg = {
            "rull": self.rullkorrektion_db(flode, forhallanden or Vagforhallanden()),
            "drivlina": self.drivlinekorrektion_db(flode),
        }
        effekt = np.zeros((len(self.delkallhojder_m), ANTAL_BAND))
        for bullertyp, tabell in data.tabeller.items():
            niva_db = tabell.ljudeffekt_per_fordon_db(hastighet_km_h) + tillagg[bullertyp]
            effekt += tabell.fordelning * 10.0 ** (niva_db / 10.0)
        with np.errstate(divide="ignore"):
            return np.asarray(10.0 * np.log10(effekt), dtype=np.float64)

    def ljudeffekt_db(
        self,
        trafik: Sequence[Trafikflode],
        segment_langd_m: float,
        period_s: float,
        delkallhojder_m: Sequence[float] | None = None,
        forhallanden: Vagforhallanden | None = None,
    ) -> Flyttal:
        """Ekvivalent ljudeffektnivå för ett vägsegment, form (antal delkällor, ANTAL_BAND).

        Args:
            trafik: antal fordon och hastighet per kategori under perioden.
            segment_langd_m: segmentets längd längs vägen.
            period_s: periodens längd (t.ex. 86 400 s för ett dygn).
            delkallhojder_m: om angivet ska det vara modellens delkällhöjder; raderna i
                resultatet följer den ordningen. Utelämnat ger `self.delkallhojder_m`.
            forhallanden: beläggning, temperatur och land; utelämnat ger referensförhållandet.

        Returns:
            L_W per delkälla och tersband i dB re 1 pW (−inf där trafiken är noll).
        """
        if not math.isfinite(segment_langd_m) or segment_langd_m <= 0.0:
            raise BullerFel("segment_langd_m ska vara större än noll.")
        if not math.isfinite(period_s) or period_s <= 0.0:
            raise BullerFel("period_s ska vara större än noll.")
        if not trafik:
            raise BullerFel("Trafiken saknar poster.")
        ordning = self._ordning(delkallhojder_m)

        effekt = np.zeros((len(self.delkallhojder_m), ANTAL_BAND))
        for flode in trafik:
            per_fordon_db = self.ljudeffekt_per_fordon_db(
                flode.kategori, flode.hastighet_km_h, forhallanden, flode
            )
            hastighet_m_s = flode.hastighet_km_h / 3.6
            tidsandel = flode.antal_fordon * segment_langd_m / (hastighet_m_s * period_s)
            effekt += tidsandel * 10.0 ** (per_fordon_db / 10.0)
        with np.errstate(divide="ignore"):
            niva_db = 10.0 * np.log10(effekt)
        return np.asarray(niva_db[ordning, :], dtype=np.float64)

    def maxniva_ljudeffekt_db(self, kategori: int, hastighet_km_h: float) -> Flyttal:
        """Maximalnivå stöds inte: metoden är inte fullständigt läsbar i tillgängliga källor."""
        raise BullerFel(
            "Maximalnivå är inte införd. Metoden står i SP Rapport 2006:12 avsnitt 2.6 "
            "(ekvation 2.13–2.16) och kräver direktivitet och utbredning per fordonsläge."
        )

    def _ordning(self, delkallhojder_m: Sequence[float] | None) -> list[int]:
        egna = self.delkallhojder_m
        if delkallhojder_m is None:
            return list(range(len(egna)))
        ordning: list[int] = []
        for hojd in delkallhojder_m:
            traffar = [i for i, h in enumerate(egna) if abs(h - hojd) <= _HOJDTOLERANS_M]
            if not traffar:
                raise BullerFel(
                    f"Delkällhöjden {hojd:g} m finns inte i källmodellen "
                    f"(höjder: {', '.join(f'{h:g}' for h in egna)} m)."
                )
            ordning.append(traffar[0])
        if sorted(ordning) != list(range(len(egna))):
            raise BullerFel(
                "delkallhojder_m ska innehålla modellens alla delkällhöjder exakt en gång; "
                "annars tappas ljudeffekt bort."
            )
        return ordning


def ljudeffekt_db(
    trafik: Sequence[Trafikflode],
    segment_langd_m: float,
    period_s: float,
    delkallhojder_m: Sequence[float] | None = None,
    forhallanden: Vagforhallanden | None = None,
    *,
    modell: Vagemission | None = None,
) -> Flyttal:
    """Ekvivalent ljudeffektnivå för ett vägsegment med standarddatafilen (se `Vagemission`)."""
    return (modell or Vagemission()).ljudeffekt_db(
        trafik, segment_langd_m, period_s, delkallhojder_m, forhallanden
    )
