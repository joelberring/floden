# ruff: noqa: E501
"""Habitatnätverk med signalarter som konfiguration (`data/ekologi/natverk/*.toml`, kontrakt K47).

Ett nätverk är ett aggregerat samband (till exempel barrskogssamband eller ädellövsamband) med
signalarter, så som kommunala rapporter redovisar dem. Floden arbetar med nätverk i stället för
fria artprofiler: varje parameter (habitat, minsta area, avståndströskel, d50, motstånd per klass)
har en källnyckel och sida eller tabell ur källsammanställningen
`docs/ekologi/habitatnatverk-svensk-stadsmiljo.md`, en `typ` och en `sakerhet`. Det som saknas i
källan skrivs som saknat (`[[saknas]]`) eller som ett uttryckligt antagande (`typ = "antagande"`).

Spridningsfunktionen är p(d) = 2^(-d/d50) i kostnadsavstånd (kärnan: `berakna_pc_eca` med p0 = 0,5).
Anger källan bara en tröskel och inte d50 används antagandet i konfigurationen (`[d50_antagande]`,
förval d50 = tröskel/2, samma kvot som Södertälje) och märks i resultatet.

Oändligt motstånd (total barriär) kan bara sättas uttryckligen i konfigurationen
(`oandligt = true` med `motivering`); sammanställningen finner inget empiriskt stöd för totala
barriärer, så inget förval är oändligt.

Nätverkets klasser är NMD-klasser (`omfattning = "nmd"`, går att köra på servern med öppen data) eller
stadens biotopklasser (`omfattning = "biotop"`, bygger på en biotopdatabas som inte är öppen och körs
bara lokalt med en lokal klasskarta). Biotopklasser kan inte översättas rakt av till NMD.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from ekologi.typer import ArtProfil, EkologiFel
from gemensamt.hash import sha256_fil

FORMAT_NATVERK: Final[str] = "floden-habitatnatverk/v1"
RESULTAT_ETIKETT_OKAND_KVALITET: Final[str] = "Potentiell livsmiljö, okänd kvalitet"
DATA_MAPP: Final[Path] = Path(__file__).resolve().parents[2] / "data" / "ekologi"
NATVERK_MAPP: Final[Path] = DATA_MAPP / "natverk"
KALLOR_FIL: Final[Path] = DATA_MAPP / "kallor.toml"
KOMMUNER_FIL: Final[Path] = DATA_MAPP / "kommuner.toml"
SAMMANSTALLNING: Final[str] = "docs/ekologi/habitatnatverk-svensk-stadsmiljo.md"

TYPER: Final[frozenset[str]] = frozenset(
    {"uppmätt", "litteratur", "modell", "expertbedömning", "antagande"}
)
"""`antagande` är ett uttryckligt Flodenval där källan saknar värdet."""
SAKERHET: Final[frozenset[str]] = frozenset({"hög", "måttlig", "låg"})
OMFATTNINGAR: Final[frozenset[str]] = frozenset({"nmd", "biotop"})
NATVERK_ID_MONSTER: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]{0,59}$")
STANDARD_D50_REGEL: Final[str] = "troskel/2"
METODSTATUS_TEXT: Final[str] = (
    "Parametrarna är källbelagda (kommunala modellval), biologisk säkerhet låg."
)
NMD_BYGGNAD: Final[int] = 51
"""NMD-klassen för byggnad; ritade hus (K9) läggs ovanpå som denna klass."""


@dataclass(frozen=True)
class Belagd:
    """Källhänvisning, typ och säkerhet för ett värde i konfigurationen."""

    kalla: str
    sida: str
    typ: str
    sakerhet: str
    anmarkning: str = ""

    def som_dict(self) -> dict[str, str]:
        d = {"kalla": self.kalla, "sida": self.sida, "typ": self.typ, "sakerhet": self.sakerhet}
        if self.anmarkning:
            d["anmarkning"] = self.anmarkning
        return d


@dataclass(frozen=True)
class Scenario:
    """Ett avståndsscenario: tröskel i effektiva meter och d50 (angiven av källan eller antagen)."""

    scenario_id: str
    namn: str
    troskel_m: float | None
    d50_m: float | None
    belagd: Belagd

    @property
    def har_avstand(self) -> bool:
        return self.troskel_m is not None


@dataclass(frozen=True)
class D50Antagande:
    """Regeln som används när källan bara anger en tröskel (p saknas i källan)."""

    regel: str
    kvot: float
    text: str
    motivering: str
    belagd: Belagd


@dataclass(frozen=True)
class Habitatvariant:
    """Ett alternativt habitaturval (jämförelsescenario) vid samma tröskel som huvudscenariot."""

    variant_id: str
    namn: str
    klasser: tuple[int | str, ...]
    beskrivning: str
    belagd: Belagd


@dataclass(frozen=True)
class ValfrittLager:
    """Ett valfritt indatalager som datamodellen förutser men som inte är byggt (visas, används inte)."""

    lager_id: str
    namn: str
    text: str
    status: str
    belagd: Belagd


@dataclass(frozen=True)
class Motstandspost:
    """Ett motstånd för en eller flera klasser. `varde` är oändligt bara med `oandligt`."""

    klasser: tuple[int | str, ...]
    namn: str
    varde: float
    oandligt: bool
    motivering: str
    belagd: Belagd


@dataclass(frozen=True)
class Saknas:
    parameter: str
    text: str


@dataclass(frozen=True)
class Kompletterande:
    """Ett värde ur källsammanställningen som visas men inte används i beräkningen."""

    parameter: str
    varde: str
    anvands: bool
    belagd: Belagd


@dataclass(frozen=True)
class Natverk:
    """Ett habitatnätverk (K47) läst ur `data/ekologi/natverk/<id>.toml`."""

    natverk_id: str
    namn: str
    underlag: str
    omfattning: str
    korbar: bool
    korbar_orsak: str
    signalarter: tuple[str, ...]
    beskrivning: str
    biologisk_sakerhet: str
    biologisk_sakerhet_text: str
    huvudscenario: str
    kalla_nyckel: str
    kalla_sida: str
    habitat_klasser: tuple[int | str, ...]
    habitat_beskrivning: str
    habitat_belagd: Belagd | None
    min_area_m2: float | None
    min_area_antagen: bool
    min_area_belagd: Belagd | None
    scenarier: tuple[Scenario, ...]
    d50_antagande: D50Antagande | None
    standard_motstand: float | None
    standard_motstand_belagd: Belagd | None
    motstand: tuple[Motstandspost, ...]
    saknas: tuple[Saknas, ...]
    kompletterande: tuple[Kompletterande, ...]
    nmd_overforing_text: str
    vmi_klass: str | None
    kvalitetskalla: bool
    filnamn: str
    sha256: str
    kvalitetsklasser: tuple[str, ...] = field(default_factory=tuple)
    habitatvarianter: tuple[Habitatvariant, ...] = field(default_factory=tuple)
    valfria_lager: tuple[ValfrittLager, ...] = field(default_factory=tuple)

    @property
    def resultat_etikett(self) -> str:
        """Namn på vad resultatet visar. NMD-nätverk saknar uppgifter om ålder, naturvärde och trädinventering."""
        if self.omfattning == "nmd":
            return RESULTAT_ETIKETT_OKAND_KVALITET
        return ""

    def antagna_motstand(self) -> list[dict[str, Any]]:
        """Motstånd som är Flodens eget modellval (typ antagande), redovisas separat från källans värden."""
        return [
            {"klasser": list(p.klasser), "namn": p.namn, "varde": p.varde, "kalla": p.belagd.kalla,
             "sida": p.belagd.sida, "text": p.belagd.anmarkning}
            for p in self.motstand if p.belagd.typ == "antagande"
        ]  # fmt: skip

    # ------------------------------------------------------------------ uppslag

    def scenario(self, scenario_id: str | None = None) -> Scenario:
        sid = scenario_id or self.huvudscenario
        for s in self.scenarier:
            if s.scenario_id == sid:
                return s
        giltiga = ", ".join(s.scenario_id for s in self.scenarier)
        raise EkologiFel(f"Nätverket {self.natverk_id} har inget scenario {sid!r} ({giltiga}).")

    def d50_for(self, scen: Scenario) -> tuple[float, str | None]:
        """(d50 i effektiva m, antagandetext eller None om d50 anges av källan)."""
        if scen.d50_m is not None:
            return scen.d50_m, None
        if scen.troskel_m is None or self.d50_antagande is None:
            raise EkologiFel(
                f"Nätverket {self.natverk_id}, scenario {scen.scenario_id}: varken d50 eller "
                "tröskel med antagande finns."
            )
        return scen.troskel_m * self.d50_antagande.kvot, self.d50_antagande.text

    def motstand_per_klass(self) -> dict[int | str, float]:
        ut: dict[int | str, float] = {}
        for post in self.motstand:
            for k in post.klasser:
                ut[k] = post.varde
        return ut

    def oandliga_klasser(self) -> tuple[int | str, ...]:
        return tuple(k for p in self.motstand if p.oandligt for k in p.klasser)

    @property
    def signalarter_text(self) -> str:
        return ", ".join(self.signalarter)

    def kalla_rad(self) -> str:
        """En rad med signalart och källa, till resultatpanelen och rapporttexten."""
        kort = kalla_kort(self.kalla_nyckel)
        arter = "signalart" if len(self.signalarter) == 1 else "signalarter"
        return f"{arter.capitalize()}: {self.signalarter_text}. Källa: {kort}, {self.kalla_sida}."

    def antaganden(self, scenario_id: str | None = None) -> list[str]:
        """Texter om antaganden som gäller för körningen (visas i resultat och rapport)."""
        scen = self.scenario(scenario_id)
        ut: list[str] = []
        if scen.d50_m is None and self.d50_antagande is not None and scen.troskel_m is not None:
            d50, _ = self.d50_for(scen)
            ut.append(f"{self.d50_antagande.text} ({_tal(d50)} effektiva m).")
        if self.habitat_belagd is not None and self.habitat_belagd.typ == "antagande":
            ut.append(
                "Habitatdefinitionen i NMD-klasser är ett antagande i Floden; källan anger den inte."
                if self.omfattning == "nmd"
                else "Habitatklasserna är en förenkling för den lokala klasskartan."
            )
        if self.min_area_antagen:
            ut.append("Minsta area saknas i källan; varje sammanhängande habitatyta räknas.")
        if scen.belagd.anmarkning and "tolkar" in scen.belagd.anmarkning:
            ut.append(scen.belagd.anmarkning)
        for post in self.motstand:
            if post.belagd.typ == "antagande":
                ut.append(
                    f"Motstånd för {_klasser_text(post.klasser)} saknas i källan; antagande "
                    f"{_tal(post.varde)}."
                )
        return ut

    def som_dict(self, scenario_id: str | None = None) -> dict[str, Any]:
        """JSON-vänlig beskrivning (ekologi.json, API och rapport). Inga värden räknas om."""
        scen = self.scenario(scenario_id)
        return {
            "natverk_id": self.natverk_id,
            "namn": self.namn,
            "underlag": self.underlag,
            "omfattning": self.omfattning,
            "signalarter": list(self.signalarter),
            "kalla": {
                "nyckel": self.kalla_nyckel,
                "referens": kalla_kort(self.kalla_nyckel),
                "sida": self.kalla_sida,
                "sammanstallning": SAMMANSTALLNING,
            },
            "kalla_rad": self.kalla_rad(),
            "biologisk_sakerhet": self.biologisk_sakerhet,
            "biologisk_sakerhet_text": self.biologisk_sakerhet_text,
            "scenario": scen.scenario_id,
            "habitat_klasser": list(self.habitat_klasser),
            "habitat_typ": self.habitat_belagd.typ if self.habitat_belagd else None,
            "resultat_etikett": self.resultat_etikett,
            "habitatvarianter": [
                {"id": v.variant_id, "namn": v.namn, "klasser": list(v.klasser), **v.belagd.som_dict()}
                for v in self.habitatvarianter
            ],
            "antagna_motstand": self.antagna_motstand(),
            "valfria_lager": [
                {"id": v.lager_id, "namn": v.namn, "text": v.text, "status": v.status, **v.belagd.som_dict()}
                for v in self.valfria_lager
            ],
            "min_area_m2": self.min_area_m2,
            "d50_m": self.d50_for(scen)[0] if scen.har_avstand else None,
            "troskel_m": scen.troskel_m,
            "d50_antagen": scen.d50_m is None and scen.har_avstand,
            "oandliga_klasser": list(self.oandliga_klasser()),
            "antaganden": self.antaganden(scen.scenario_id),
            "saknas": [{"parameter": s.parameter, "text": s.text} for s in self.saknas],
            "sha256": self.sha256,
        }

    def katalogpost(self) -> dict[str, Any]:
        """Kort post för nätverkslistan i API och webb."""
        return {
            "natverk_id": self.natverk_id,
            "namn": self.namn,
            "visningsnamn": self.visningsnamn(),
            "underlag": self.underlag,
            "omfattning": self.omfattning,
            "korbar": self.korbar,
            "korbar_orsak": self.korbar_orsak or None,
            "kvalitetskalla": self.kvalitetskalla,
            "signalarter": list(self.signalarter),
            "kalla_rad": self.kalla_rad(),
            "scenarier": [
                {"id": s.scenario_id, "namn": s.namn, "troskel_m": s.troskel_m}
                for s in self.scenarier
            ],
            "huvudscenario": self.huvudscenario,
            "lokalt": self.omfattning == "biotop",
        }

    def visningsnamn(self) -> str:
        """Namn med underlag och datakälla, till exempel `Barrskogssamband (Nacka/NMD)`."""
        if self.omfattning == "nmd":
            kommun = self.underlag.rsplit(" ", 1)[0]
            return f"{self.namn} ({kommun}/NMD)"
        return f"{self.namn} ({self.underlag}, lokalt)" if "lokalt" not in self.namn else self.namn


# ---------------------------------------------------------------------- inläsning


def _tal(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def _klasser_text(klasser: tuple[int | str, ...]) -> str:
    return ", ".join(str(k) for k in klasser)


@lru_cache(maxsize=4)
def _kallor(sokvag: str, sha: str) -> dict[str, dict[str, str]]:
    _ = sha
    try:
        d = tomllib.loads(Path(sokvag).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EkologiFel(f"Källfilen {sokvag} går inte att läsa: {exc}") from exc
    return {k: dict(v) for k, v in d.items() if isinstance(v, dict)}


def kallor(sokvag: Path = KALLOR_FIL) -> dict[str, dict[str, str]]:
    return _kallor(str(sokvag), sha256_fil(sokvag))


def kalla_kort(nyckel: str) -> str:
    post = kallor().get(nyckel)
    if post is None:
        raise EkologiFel(f"Okänd källnyckel {nyckel!r} (finns inte i {KALLOR_FIL.name}).")
    return str(post["kort"])


def _fel(fil: str, text: str) -> EkologiFel:
    return EkologiFel(f"Nätverket {fil}: {text}")


def _text(d: Mapping[str, Any], nyckel: str, fil: str, *, valfri: bool = False) -> str:
    v = d.get(nyckel)
    if v is None and valfri:
        return ""
    if not isinstance(v, str) or not v.strip():
        raise _fel(fil, f"'{nyckel}' måste vara en icke-tom text.")
    return v


def _belagd(d: Mapping[str, Any], fil: str, var: str) -> Belagd:
    """Läser `kalla`, `sida`, `typ`, `sakerhet` (alla krävs) för värdet `var`."""
    kalla = _text(d, "kalla", fil)
    if kalla not in kallor():
        raise _fel(fil, f"{var}: okänd källnyckel {kalla!r}.")
    sida = _text(d, "sida", fil)
    typ = d.get("typ")
    if typ not in TYPER:
        raise _fel(fil, f"{var}: 'typ' ska vara en av {sorted(TYPER)}, fick {typ!r}.")
    sak = d.get("sakerhet")
    if sak not in SAKERHET:
        raise _fel(fil, f"{var}: 'sakerhet' ska vara en av {sorted(SAKERHET)}, fick {sak!r}.")
    return Belagd(kalla, sida, str(typ), str(sak), str(d.get("anmarkning", "")))


def _positivt(d: Mapping[str, Any], nyckel: str, fil: str, var: str) -> float:
    v = d.get(nyckel)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise _fel(fil, f"{var}: '{nyckel}' måste vara ett ändligt tal, fick {v!r}.")
    if float(v) <= 0.0:
        raise _fel(fil, f"{var}: '{nyckel}' måste vara > 0, fick {v}.")
    return float(v)


def _klasslista(
    v: Any, fil: str, var: str, omfattning: str
) -> tuple[int | str, ...]:
    if not isinstance(v, list) or not v:
        raise _fel(fil, f"{var}: 'klasser' måste vara en icke-tom lista.")
    ut: list[int | str] = []
    for k in v:
        if omfattning == "nmd":
            if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 255:
                raise _fel(fil, f"{var}: NMD-klass ska vara ett heltal 1-255, fick {k!r}.")
            ut.append(int(k))
        else:
            if not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", k):
                raise _fel(fil, f"{var}: biotopklass ska vara en text (a-z, 0-9, _), fick {k!r}.")
            ut.append(k)
    if len(set(ut)) != len(ut):
        raise _fel(fil, f"{var}: klasser förekommer flera gånger.")
    return tuple(ut)


def las_natverk(sokvag: str | Path) -> Natverk:
    """Läser och validerar ett nätverk (strikt: ett värde utan källa, typ eller säkerhet ger fel)."""
    fil = Path(sokvag)
    namn = fil.name
    try:
        d = tomllib.loads(fil.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EkologiFel(f"Nätverksfilen {namn} går inte att läsa: {exc}") from exc
    if d.get("format") != FORMAT_NATVERK:
        raise _fel(namn, f"format ska vara '{FORMAT_NATVERK}'.")
    nid = _text(d, "natverk_id", namn)
    if not NATVERK_ID_MONSTER.match(nid) or nid != fil.stem:
        raise _fel(namn, f"natverk_id '{nid}' ska matcha filnamnet och {NATVERK_ID_MONSTER.pattern}.")
    omfattning = d.get("omfattning")
    if omfattning not in OMFATTNINGAR:
        raise _fel(namn, f"omfattning ska vara en av {sorted(OMFATTNINGAR)}.")
    omf = str(omfattning)
    korbar = d.get("korbar")
    if not isinstance(korbar, bool):
        raise _fel(namn, "korbar ska vara true eller false.")
    arter = d.get("signalarter")
    if not isinstance(arter, list) or not arter or not all(isinstance(a, str) and a for a in arter):
        raise _fel(namn, "signalarter ska vara en icke-tom lista med namn.")
    bio_s = d.get("biologisk_sakerhet")
    if bio_s not in SAKERHET:
        raise _fel(namn, f"biologisk_sakerhet ska vara en av {sorted(SAKERHET)}.")
    kalla = d.get("kalla")
    if not isinstance(kalla, dict):
        raise _fel(namn, "tabellen [kalla] saknas.")
    kalla_nyckel = _text(kalla, "nyckel", namn)
    if kalla_nyckel not in kallor():
        raise _fel(namn, f"okänd källnyckel {kalla_nyckel!r}.")
    kalla_sida = _text(kalla, "sida", namn)

    # habitat
    hab = d.get("habitat")
    if not isinstance(hab, dict):
        raise _fel(namn, "tabellen [habitat] saknas.")
    habitat_belagd = _belagd(hab, namn, "habitat")
    if "klasser" in hab:
        habitat_klasser = _klasslista(hab["klasser"], namn, "habitat", omf)
    elif korbar:
        raise _fel(namn, "ett körbart nätverk måste ha habitat.klasser.")
    else:
        habitat_klasser = ()
    habitat_besk = str(hab.get("nmd_namn") or hab.get("biotop_namn") or hab.get("biotop_beskrivning") or "")
    kvalitetsklasser = tuple(str(x) for x in hab.get("kvalitetsklasser", []))
    varianter: list[Habitatvariant] = []
    for r in d.get("habitatvariant", []):
        vid = _text(r, "id", namn)
        if vid in {v.variant_id for v in varianter} or vid == "kandidater":
            raise _fel(namn, f"habitatvariant {vid!r} förekommer flera gånger (eller heter 'kandidater').")
        varianter.append(
            Habitatvariant(
                vid, _text(r, "namn", namn), _klasslista(r["klasser"], namn, f"habitatvariant {vid}", omf),
                str(r.get("nmd_namn", "")), _belagd(r, namn, f"habitatvariant {vid}"),
            )
        )

    # minsta area
    ma = d.get("min_area")
    if not isinstance(ma, dict):
        raise _fel(namn, "tabellen [min_area] saknas.")
    min_area_antagen = False
    min_area_m2: float | None
    if ma.get("saknas") is True:
        min_area_belagd: Belagd | None
        if "antagande_m2" in ma:
            min_area_m2 = _positivt(ma, "antagande_m2", namn, "min_area")
            min_area_belagd = _belagd(ma, namn, "min_area")
            if min_area_belagd.typ != "antagande":
                raise _fel(namn, "min_area: ett antaget värde ska ha typ 'antagande'.")
            min_area_antagen = True
        else:
            min_area_m2 = None
            min_area_belagd = None
            _text(ma, "kalla", namn)
            _text(ma, "sida", namn)
            if korbar:
                raise _fel(namn, "ett körbart nätverk saknar minsta area (ange antagande_m2).")
    else:
        min_area_m2 = _positivt(ma, "varde_ha", namn, "min_area") * 10_000.0
        min_area_belagd = _belagd(ma, namn, "min_area")

    # scenarier
    rader = d.get("scenario")
    if not isinstance(rader, list) or not rader:
        raise _fel(namn, "minst ett [[scenario]] krävs.")
    scenarier: list[Scenario] = []
    for r in rader:
        sid = _text(r, "id", namn)
        troskel: float | None = None
        d50: float | None = None
        if "troskel_m" in r:
            troskel = _positivt(r, "troskel_m", namn, f"scenario {sid}")
        if "d50_m" in r:
            d50 = _positivt(r, "d50_m", namn, f"scenario {sid}")
            if troskel is not None and d50 > troskel:
                raise _fel(namn, f"scenario {sid}: d50 ({d50:g}) är större än tröskeln ({troskel:g}).")
        scenarier.append(
            Scenario(sid, _text(r, "namn", namn), troskel, d50, _belagd(r, namn, f"scenario {sid}"))
        )
    if len({s.scenario_id for s in scenarier}) != len(scenarier):
        raise _fel(namn, "scenario-id förekommer flera gånger.")
    huvud = _text(d, "huvudscenario", namn)
    if huvud not in {s.scenario_id for s in scenarier}:
        raise _fel(namn, f"huvudscenario {huvud!r} finns inte bland scenarierna.")

    # d50-antagande
    da = d.get("d50_antagande")
    d50_antagande: D50Antagande | None = None
    if da is not None:
        if da.get("regel") != STANDARD_D50_REGEL:
            raise _fel(namn, f"d50_antagande.regel stöds bara som '{STANDARD_D50_REGEL}'.")
        kvot = _positivt(da, "kvot", namn, "d50_antagande")
        if not 0.0 < kvot <= 1.0:
            raise _fel(namn, "d50_antagande.kvot ska vara i (0, 1].")
        b = _belagd(da, namn, "d50_antagande")
        if b.typ != "antagande":
            raise _fel(namn, "d50_antagande ska ha typ 'antagande'.")
        d50_antagande = D50Antagande(
            str(da["regel"]), kvot, _text(da, "text", namn), _text(da, "motivering", namn), b
        )
    if korbar:
        for s in scenarier:
            if s.d50_m is None and (s.troskel_m is None or d50_antagande is None):
                raise _fel(
                    namn,
                    f"scenario {s.scenario_id}: d50 saknas och inget d50_antagande med tröskel finns.",
                )
    # motstånd
    sm = d.get("standard_motstand")
    standard: float | None = None
    standard_belagd: Belagd | None = None
    if isinstance(sm, dict):
        standard = _positivt(sm, "varde", namn, "standard_motstand")
        if standard < 1.0:
            raise _fel(namn, "standard_motstand ska vara >= 1.")
        standard_belagd = _belagd(sm, namn, "standard_motstand")
    poster: list[Motstandspost] = []
    sedda: set[int | str] = set()
    for r in d.get("motstand", []):
        klasser = _klasslista(r.get("klasser"), namn, "motstand", omf)
        oandligt = r.get("oandligt", False)
        if not isinstance(oandligt, bool):
            raise _fel(namn, "motstand.oandligt ska vara true eller false.")
        if oandligt:
            motivering = _text(r, "motivering", namn)
            varde = math.inf
            if "varde" in r:
                raise _fel(namn, f"motstand för {_klasser_text(klasser)}: oändligt anges utan 'varde'.")
        else:
            motivering = ""
            varde = _positivt(r, "varde", namn, f"motstand {_klasser_text(klasser)}")
            if varde < 1.0:
                raise _fel(namn, f"motstand för {_klasser_text(klasser)} ska vara >= 1, fick {varde:g}.")
        dubbletter = sedda & set(klasser)
        if dubbletter:
            raise _fel(namn, f"motstånd anges flera gånger för {sorted(map(str, dubbletter))}.")
        sedda |= set(klasser)
        poster.append(
            Motstandspost(
                klasser,
                str(r.get("nmd_namn") or r.get("biotop_namn") or ""),
                varde,
                oandligt,
                motivering,
                _belagd(r, namn, f"motstand {_klasser_text(klasser)}"),
            )
        )
    if korbar:
        if not poster:
            raise _fel(namn, "ett körbart nätverk måste ha [[motstand]].")
        if standard is None:
            raise _fel(namn, "ett körbart nätverk måste ha [standard_motstand].")
        saknade = [k for k in habitat_klasser if k not in sedda]
        if saknade:
            raise _fel(namn, f"habitatklasser utan motstånd: {saknade}.")
        for v in varianter:
            sakn = [k for k in v.klasser if k not in sedda]
            if sakn:
                raise _fel(namn, f"habitatvariant {v.variant_id}: klasser utan motstånd: {sakn}.")

    saknas = tuple(
        Saknas(_text(r, "parameter", namn), _text(r, "text", namn)) for r in d.get("saknas", [])
    )
    komp = tuple(
        Kompletterande(
            _text(r, "parameter", namn),
            _text(r, "varde", namn),
            bool(r.get("anvands", False)),
            _belagd(r, namn, f"kompletterande {r.get('parameter')}"),
        )
        for r in d.get("kompletterande", [])
    )
    valfria = tuple(
        ValfrittLager(
            _text(r, "id", namn), _text(r, "namn", namn), _text(r, "text", namn),
            _text(r, "status", namn), _belagd(r, namn, f"valfritt_lager {r.get('id')}"),
        )
        for r in d.get("valfritt_lager", [])
    )
    vmi = d.get("vmi_klass")
    if vmi is not None and (not isinstance(vmi, str) or vmi not in sedda):
        raise _fel(namn, "vmi_klass ska vara en klass med motstånd.")
    return Natverk(
        natverk_id=nid,
        namn=_text(d, "namn", namn),
        underlag=_text(d, "underlag", namn),
        omfattning=omf,
        korbar=korbar,
        korbar_orsak=_text(d, "korbar_orsak", namn, valfri=bool(korbar)),
        signalarter=tuple(arter),
        beskrivning=_text(d, "beskrivning", namn),
        biologisk_sakerhet=str(bio_s),
        biologisk_sakerhet_text=_text(d, "biologisk_sakerhet_text", namn),
        huvudscenario=huvud,
        kalla_nyckel=kalla_nyckel,
        kalla_sida=kalla_sida,
        habitat_klasser=habitat_klasser,
        habitat_beskrivning=habitat_besk,
        habitat_belagd=habitat_belagd,
        min_area_m2=min_area_m2,
        min_area_antagen=min_area_antagen,
        min_area_belagd=min_area_belagd,
        scenarier=tuple(scenarier),
        d50_antagande=d50_antagande,
        standard_motstand=standard,
        standard_motstand_belagd=standard_belagd,
        motstand=tuple(poster),
        saknas=saknas,
        kompletterande=komp,
        nmd_overforing_text=str(d.get("nmd_overforing_text", "")),
        vmi_klass=vmi,
        kvalitetskalla=bool(d.get("kvalitetskalla", False)),
        filnamn=namn,
        sha256=sha256_fil(fil),
        kvalitetsklasser=kvalitetsklasser,
        habitatvarianter=tuple(varianter),
        valfria_lager=valfria,
    )


@lru_cache(maxsize=8)
def _alla(mapp: str, nyckel: tuple[tuple[str, str], ...]) -> dict[str, Natverk]:
    _ = nyckel
    ut: dict[str, Natverk] = {}
    for fil in sorted(Path(mapp).glob("*.toml")):
        n = las_natverk(fil)
        ut[n.natverk_id] = n
    return ut


def alla_natverk(mapp: Path = NATVERK_MAPP) -> dict[str, Natverk]:
    """Alla nätverk i mappen, efter id. Läses om när en fil ändras."""
    nyckel = tuple((f.name, sha256_fil(f)) for f in sorted(mapp.glob("*.toml")))
    return _alla(str(mapp), nyckel)


def hamta_natverk(natverk_id: str) -> Natverk:
    """Hämtar ett nätverk efter id, eller lyfter `EkologiFel` med giltiga id."""
    n = alla_natverk().get(natverk_id)
    if n is None:
        giltiga = ", ".join(sorted(alla_natverk()))
        raise EkologiFel(f"Okänt habitatnätverk {natverk_id!r}. Giltiga nätverk: {giltiga}.")
    return n


def korbara_natverk(omfattning: str | None = None) -> dict[str, Natverk]:
    return {
        k: n
        for k, n in alla_natverk().items()
        if n.korbar and (omfattning is None or n.omfattning == omfattning)
    }


def natverk_sha256(natverk_id: str) -> str:
    """Nätverksfilens och källfilens sha256 (ingår i lösarens parametrar och därmed cachenyckeln)."""
    n = hamta_natverk(natverk_id)
    return f"{n.sha256}:{sha256_fil(KALLOR_FIL)}"


# ---------------------------------------------------------------------- kommuner


@dataclass(frozen=True)
class Kommunforval:
    kommun_id: str
    namn: str
    forval_server: tuple[str, ...]
    forval_lokalt: tuple[str, ...]
    kvalitetskallor: Mapping[str, str]
    anmarkning: str


def kommunforval(sokvag: Path = KOMMUNER_FIL) -> dict[str, Kommunforval]:
    """Förval av nätverk per kommun (`data/ekologi/kommuner.toml`); alla id kontrolleras."""
    try:
        d = tomllib.loads(sokvag.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EkologiFel(f"Kommunfilen {sokvag.name} går inte att läsa: {exc}") from exc
    alla = alla_natverk()
    ut: dict[str, Kommunforval] = {}
    for kid, post in d.get("kommun", {}).items():
        server = tuple(post.get("forval_server", []))
        lokalt = tuple(post.get("forval_lokalt", []))
        kv = dict(post.get("kvalitetskallor", {}))
        for nid in (*server, *lokalt, *kv, *kv.values()):
            if nid not in alla:
                raise EkologiFel(f"Kommunfilen: {kid} nämner okänt nätverk {nid!r}.")
        for nid in server:
            if alla[nid].omfattning != "nmd" or not alla[nid].korbar:
                raise EkologiFel(f"Kommunfilen: {kid}: {nid} är inget körbart NMD-nätverk.")
        for nid in lokalt:
            if alla[nid].omfattning != "biotop":
                raise EkologiFel(f"Kommunfilen: {kid}: {nid} är inte kommunspecifikt.")
        ut[kid] = Kommunforval(
            kid, str(post.get("namn", kid)), server, lokalt, kv, str(post.get("anmarkning", ""))
        )
    return ut


# ---------------------------------------------------------------------- till kärnan


def till_artprofil(
    natverk: Natverk,
    scenario_id: str | None = None,
    *,
    klasskoder: Mapping[str, int] | None = None,
    habitatvariant: str | None = None,
) -> ArtProfil:
    """Kärnans profil (`ArtProfil`) för ett scenario i ett nätverk.

    NMD-nätverk använder NMD-koderna som markslagskoder. Biotopnätverk kräver `klasskoder`
    (biotopklassens namn till heltalskod 0-255 i den lokala klasskartan) för alla klasser som
    nätverket använder. d50 blir profilens `spridningsavstand_m`; kärnan räknar p = 2^(-d/d50).
    """
    if not natverk.korbar:
        raise EkologiFel(
            f"Nätverket {natverk.natverk_id} är inte körbart: {natverk.korbar_orsak}"
        )
    scen = natverk.scenario(scenario_id)
    d50, _ = natverk.d50_for(scen)
    habitat_klasser = natverk.habitat_klasser
    profil_id = natverk.natverk_id
    if habitatvariant is not None:
        hv = next((v for v in natverk.habitatvarianter if v.variant_id == habitatvariant), None)
        if hv is None:
            giltiga = ", ".join(v.variant_id for v in natverk.habitatvarianter) or "inga"
            raise EkologiFel(f"Nätverket {natverk.natverk_id} har ingen habitatvariant {habitatvariant!r} ({giltiga}).")
        habitat_klasser = hv.klasser
        profil_id = f"{natverk.natverk_id}:{hv.variant_id}"
    assert natverk.min_area_m2 is not None and natverk.standard_motstand is not None

    def kod(k: int | str) -> int:
        if natverk.omfattning == "nmd":
            return int(k)
        if klasskoder is None:
            raise EkologiFel(
                f"Nätverket {natverk.natverk_id} bygger på stadens biotopdatabas och körs bara "
                "lokalt: ange en lokal klasskarta (klasskoder)."
            )
        if str(k) not in klasskoder:
            raise EkologiFel(f"Klasskartan saknar biotopklassen {k!r} i {natverk.natverk_id}.")
        v = klasskoder[str(k)]
        if isinstance(v, bool) or not 0 <= v <= 255:
            raise EkologiFel(f"Klasskod för {k!r} ska vara ett heltal 0-255, fick {v!r}.")
        return int(v)

    motstand: dict[int, float] = {}
    for k, r in natverk.motstand_per_klass().items():
        motstand[kod(k)] = r
    koder = list(motstand)
    if len(set(koder)) != len(koder):
        raise EkologiFel(f"Klasskartan ger samma kod till flera klasser i {natverk.natverk_id}.")
    return ArtProfil(
        profil_id=profil_id,
        namn=natverk.visningsnamn(),
        beskrivning=natverk.beskrivning,
        kalla=natverk.kalla_rad(),
        habitat_markslag=frozenset(kod(k) for k in habitat_klasser),
        min_area_m2=natverk.min_area_m2,
        spridningsavstand_m=d50,
        motstand_per_markslag=motstand,
        standard_motstand=natverk.standard_motstand,
        min_kronhojd_m=0.0,
        motstand_osakerhet_faktor=(1.0, 1.0),
        metodstatus="skiss",
    )
