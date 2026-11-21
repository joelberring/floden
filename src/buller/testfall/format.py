"""Filformat för Nord2000-testfall (N-fall/v1 och N-facit/v1).

Ett fall är en mapp med `fall.toml` (metadata och indata) och `facit/facit.json`
(referensvärden). Huvudfälten i `fall.toml` är desamma som i Flodens övriga testfall
(`id`, `namn`, `grupp`, `typ`, `beskrivning`, `status`); därtill kommer indata för vägen,
mottagaren, terrängprofilen, trafiken och vädret.

Formatet gör skillnad på *fullständig* och *ofullständig* indata. Ett fall vars geometri bara
finns i en Excel-fil som saknas får ändå digitaliseras, men ska då ange
`[indata] fullstandig = false` och räkna upp vad som saknas. Sådana fall går inte att köra.

Fältet `ar_facit` i `facit.json` avgör om värdena får användas som facit. Det är falskt för
allt som kommer ur en äldre version av testdata.

Enheter: meter, km/h, timmar, kNs/m⁴, decibel. Terrängens x räknas som i källdokumentet
(vinkelrätt från vägens mittlinje); köraren flyttar origo till källan.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import numpy as np

from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel, Flyttal, Terrangprofil, Vader

KONTRAKT_FALL = "N-fall/v1"
KONTRAKT_FACIT = "N-facit/v1"
GRUPP_N = "N"
FALLFIL = "fall.toml"
FACITFIL = "facit/facit.json"

TYP_RAK_VAG = "nord2000_rak_vag"
KANDA_TYPER: tuple[str, ...] = (
    TYP_RAK_VAG,
    "nord2000_kurvig_vag",
    "nord2000_stadsgata",
    "nord2000_arsmedel",
)
KANDA_STATUS: tuple[str, ...] = ("redo", "ej_facit", "ofullstandig")

_VADERFALT: tuple[str, ...] = tuple(f.name for f in fields(Vader))
_HUVUDFALT: tuple[str, ...] = ("id", "namn", "grupp", "typ", "beskrivning", "status")

Skalar = str | int | float | bool


@dataclass(frozen=True)
class Vagdata:
    """Rak väg sedd från mottagaren: källinje parallell med vägen, delad i vinkelsteg.

    Källpunkterna ligger symmetriskt kring normalen från mottagaren till vägen, med
    `vinkelupplosning_grader` mellan punkterna.
    """

    kalla_x_m: float  # källinjens läge i terrängens x-koordinat
    antal_kallpunkter: int
    vinkelupplosning_grader: float

    def __post_init__(self) -> None:
        if self.antal_kallpunkter < 1:
            raise BullerFel("antal_kallpunkter ska vara minst 1.")
        if not 0.0 < self.vinkelupplosning_grader < 180.0:
            raise BullerFel("vinkelupplosning_grader ska ligga mellan 0 och 180.")
        halv_spann = (self.antal_kallpunkter * self.vinkelupplosning_grader) / 2.0
        if halv_spann > 90.0:
            raise BullerFel(
                "Källpunkterna täcker mer än ett halvplan: antal_kallpunkter × "
                f"vinkelupplosning_grader = {2.0 * halv_spann:g}° > 180°."
            )


@dataclass(frozen=True)
class Terrangdata:
    """Terrängprofil som i källdokumentet: punkter (x, z) och egenskaper per segment."""

    x_m: tuple[float, ...]
    z_m: tuple[float, ...]
    flodesresistivitet_kNsm4: tuple[float, ...]  # per segment (n − 1 värden)
    rahet_m: tuple[float, ...]  # per segment (n − 1 värden)

    def __post_init__(self) -> None:
        n = len(self.x_m)
        if n < 2 or len(self.z_m) != n:
            raise BullerFel("Terrängen kräver minst två punkter och lika långa x_m och z_m.")
        if len(self.flodesresistivitet_kNsm4) != n - 1 or len(self.rahet_m) != n - 1:
            raise BullerFel("Terrängens markegenskaper ska anges per segment (n − 1 värden).")
        if any(b <= a for a, b in zip(self.x_m, self.x_m[1:], strict=False)):
            raise BullerFel("Terrängens x_m ska vara strikt växande.")
        if any(v < 0.0 for v in self.flodesresistivitet_kNsm4) or any(
            v < 0.0 for v in self.rahet_m
        ):
            raise BullerFel("Flödesresistivitet och råhet får inte vara negativa.")


@dataclass(frozen=True)
class Trafikpost:
    """Trafik för en fordonskategori och en tidsperiod."""

    kategori: int
    antal_fordon: float  # under perioden
    hastighet_km_h: float
    period: str  # nyckel i fallets `perioder_h`

    def __post_init__(self) -> None:
        if self.antal_fordon < 0.0:
            raise BullerFel("antal_fordon får inte vara negativt.")
        if self.hastighet_km_h <= 0.0:
            raise BullerFel("hastighet_km_h ska vara större än noll.")


@dataclass(frozen=True)
class N2kFall:
    """Ett Nord2000-testfall inläst från `fall.toml`."""

    id: str
    namn: str
    typ: str
    beskrivning: str
    status: str
    kalla: Mapping[str, Skalar]
    indata_fullstandig: bool
    indata_saknas: tuple[str, ...] = ()
    vag: Vagdata | None = None
    mottagare_x_m: float | None = None
    mottagare_hojd_m: float | None = None
    terrang: Terrangdata | None = None
    trafik: tuple[Trafikpost, ...] = ()
    perioder_h: Mapping[str, float] = field(default_factory=dict)
    vader: Vader | None = None
    vader_tillagg: Mapping[str, float] = field(default_factory=dict)
    ovrigt: Mapping[str, Skalar] = field(default_factory=dict)
    grupp: str = GRUPP_N
    mapp: Path | None = None

    def __post_init__(self) -> None:
        for namn in ("id", "namn", "typ", "beskrivning", "status"):
            if not str(getattr(self, namn)).strip():
                raise BullerFel(f"Fältet '{namn}' får inte vara tomt.")
        if self.grupp != GRUPP_N:
            raise BullerFel(f"Nord2000-fall ska ha grupp = \"N\", fick \"{self.grupp}\".")
        if self.typ not in KANDA_TYPER:
            raise BullerFel(f"Okänd falltyp '{self.typ}'. Kända: {', '.join(KANDA_TYPER)}.")
        if self.status not in KANDA_STATUS:
            raise BullerFel(f"Okänd status '{self.status}'. Kända: {', '.join(KANDA_STATUS)}.")
        if "dokument" not in self.kalla:
            raise BullerFel("Fallet saknar källhänvisning ([kalla] dokument).")
        for post in self.trafik:
            if post.period not in self.perioder_h:
                raise BullerFel(
                    f"Trafikposten för kategori {post.kategori} hänvisar till perioden "
                    f"'{post.period}' som saknas i [perioder_h]."
                )
        if any(h <= 0.0 for h in self.perioder_h.values()):
            raise BullerFel("Periodlängder i [perioder_h] ska vara större än noll.")
        if self.indata_fullstandig:
            self._kontrollera_fullstandig()
        elif not self.indata_saknas:
            raise BullerFel(
                "Ofullständig indata ([indata] fullstandig = false) kräver en lista "
                "`saknas` som säger vad som fattas."
            )

    def _kontrollera_fullstandig(self) -> None:
        if self.indata_saknas:
            raise BullerFel("Fullständig indata får inte ha poster i [indata] saknas.")
        saknade = [
            namn
            for namn, varde in (
                ("[terrang]", self.terrang),
                ("[mottagare]", self.mottagare_x_m),
                ("[mottagare] hojd_over_mark_m", self.mottagare_hojd_m),
                ("[vader]", self.vader),
                ("[[trafik]]", self.trafik or None),
            )
            if varde is None
        ]
        if self.typ == TYP_RAK_VAG and self.vag is None:
            saknade.append("[vag]")
        if saknade:
            raise BullerFel(
                f"Fall {self.id} anges som fullständigt men saknar: {', '.join(saknade)}."
            )
        assert self.terrang is not None and self.mottagare_x_m is not None
        assert self.mottagare_hojd_m is not None
        if self.mottagare_hojd_m < 0.0:
            raise BullerFel("Mottagarens höjd över mark får inte vara negativ.")
        x_forsta, x_sista = self.terrang.x_m[0], self.terrang.x_m[-1]
        if not x_forsta <= self.mottagare_x_m <= x_sista:
            raise BullerFel("Mottagaren ligger utanför terrängprofilen.")
        if self.vag is not None and not x_forsta <= self.vag.kalla_x_m < self.mottagare_x_m:
            raise BullerFel("Källinjen ska ligga i terrängprofilen och före mottagaren.")

    def profil(self, kalla_hojd_m: float, stracka: float = 1.0) -> Terrangprofil:
        """Terrängprofil källa–mottagare med origo i källan.

        `stracka` multiplicerar alla horisontella avstånd. För en rak väg med samma tvärsnitt
        längs hela vägen blir snittet mot en källpunkt i vinkeln θ från normalen tvärsnittet
        utdraget med 1 / cos θ (ren geometri).
        """
        if self.terrang is None or self.vag is None or self.mottagare_x_m is None:
            raise BullerFel(f"Fall {self.id} saknar geometri för en terrängprofil.")
        if self.mottagare_hojd_m is None:
            raise BullerFel(f"Fall {self.id} saknar mottagarhöjd.")
        if stracka < 1.0:
            raise BullerFel("stracka ska vara minst 1.")
        x = np.asarray(self.terrang.x_m, dtype=np.float64)
        z = np.asarray(self.terrang.z_m, dtype=np.float64)
        sigma = np.asarray(self.terrang.flodesresistivitet_kNsm4, dtype=np.float64)
        rahet = np.asarray(self.terrang.rahet_m, dtype=np.float64)
        x0, x1 = self.vag.kalla_x_m, self.mottagare_x_m
        # Klipp profilen till [x0, x1] och lägg in ändpunkterna genom linjär interpolation.
        inre = (x > x0) & (x < x1)
        x_ny = np.concatenate(([x0], x[inre], [x1]))
        z_ny = np.interp(x_ny, x, z)
        mitt = 0.5 * (x_ny[:-1] + x_ny[1:])
        seg = np.clip(np.searchsorted(x, mitt, side="right") - 1, 0, x.shape[0] - 2)
        return Terrangprofil(
            x_m=(x_ny - x0) * stracka,
            z_m=z_ny,
            flodesresistivitet_kNsm4=sigma[seg],
            rahet_m=rahet[seg],
            kalla_hojd_m=kalla_hojd_m,
            mottagare_hojd_m=self.mottagare_hojd_m,
        )


@dataclass(frozen=True)
class Facitserie:
    """En uppsättning referensvärden (t.ex. ett vindfall eller en mottagarhöjd)."""

    id: str
    villkor: Mapping[str, Skalar]
    totalnivaer_db: Mapping[str, float]
    spektrum_db: Mapping[str, Flyttal]  # form (ANTAL_BAND,) per storhet

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise BullerFel("Facitserien saknar id.")
        if not self.totalnivaer_db and not self.spektrum_db:
            raise BullerFel(f"Facitserien '{self.id}' saknar både totalnivåer och spektrum.")
        for namn, varde in self.totalnivaer_db.items():
            if not np.isfinite(varde):
                raise BullerFel(f"Totalnivån '{namn}' i serien '{self.id}' är inte ändlig.")
        for namn, spektrum in self.spektrum_db.items():
            if spektrum.shape != (ANTAL_BAND,):
                raise BullerFel(
                    f"Spektrum '{namn}' i serien '{self.id}' ska ha {ANTAL_BAND} band, "
                    f"fick form {spektrum.shape}."
                )
            if not bool(np.all(np.isfinite(spektrum))):
                raise BullerFel(f"Spektrum '{namn}' i serien '{self.id}' har icke-ändliga tal.")


@dataclass(frozen=True)
class Facit:
    """Referensvärden för ett fall (`facit/facit.json`)."""

    ar_facit: bool
    ar_facit_skal: str
    metodversion: str
    kalla: Mapping[str, Skalar]
    serier: tuple[Facitserie, ...]
    avlasning: Mapping[str, Skalar] = field(default_factory=dict)
    regelverk: str = "mp1335_2010"  # nyckel i `data/acceptabel_avvikelse.toml`

    def __post_init__(self) -> None:
        if not self.regelverk.strip():
            raise BullerFel("Facit saknar `regelverk`.")
        if not self.ar_facit_skal.strip():
            raise BullerFel("Facit ska motivera `ar_facit` i fältet `ar_facit_skal`.")
        if not self.metodversion.strip():
            raise BullerFel("Facit saknar `metodversion`.")
        if "dokument" not in self.kalla:
            raise BullerFel("Facit saknar källhänvisning (`kalla.dokument`).")
        if not self.serier:
            raise BullerFel("Facit saknar serier.")
        idn = [s.id for s in self.serier]
        if len(set(idn)) != len(idn):
            raise BullerFel("Facitseriernas id ska vara unika.")


# --------------------------------------------------------------------------- inläsning


def _tabell(data: Mapping[str, Any], nyckel: str, fil: Path) -> dict[str, Any]:
    varde = data.get(nyckel, {})
    if not isinstance(varde, dict):
        raise BullerFel(f"{fil}: [{nyckel}] ska vara en tabell.")
    return varde


def _tal(varde: Any, namn: str, fil: Path) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise BullerFel(f"{fil}: '{namn}' ska vara ett tal, fick {varde!r}.")
    return float(varde)


def _tallista(varde: Any, namn: str, fil: Path) -> tuple[float, ...]:
    if not isinstance(varde, list):
        raise BullerFel(f"{fil}: '{namn}' ska vara en lista med tal.")
    return tuple(_tal(v, namn, fil) for v in varde)


def _skalarer(tabell: Mapping[str, Any], namn: str, fil: Path) -> dict[str, Skalar]:
    ut: dict[str, Skalar] = {}
    for nyckel, varde in tabell.items():
        if not isinstance(varde, str | int | float | bool):
            raise BullerFel(f"{fil}: [{namn}] {nyckel} ska vara text, tal eller sant/falskt.")
        ut[nyckel] = varde
    return ut


def _kraver(tabell: Mapping[str, Any], nycklar: Sequence[str], namn: str, fil: Path) -> None:
    saknade = [n for n in nycklar if n not in tabell]
    if saknade:
        raise BullerFel(f"{fil}: [{namn}] saknar {', '.join(saknade)}.")


def fall_fran_dict(data: Mapping[str, Any], fil: Path, mapp: Path | None = None) -> N2kFall:
    """Bygger ett `N2kFall` ur innehållet i en `fall.toml`. Fel ger `BullerFel`."""
    saknade = [f for f in _HUVUDFALT if f not in data]
    if saknade:
        raise BullerFel(f"{fil} saknar obligatoriska fält: {', '.join(saknade)}.")
    if data.get("kontrakt") != KONTRAKT_FALL:
        raise BullerFel(f"{fil}: kontrakt ska vara \"{KONTRAKT_FALL}\".")

    indata = _tabell(data, "indata", fil)
    if not isinstance(indata.get("fullstandig"), bool):
        raise BullerFel(f"{fil}: [indata] fullstandig ska vara sant eller falskt.")
    saknas = indata.get("saknas", [])
    if not isinstance(saknas, list) or not all(isinstance(s, str) for s in saknas):
        raise BullerFel(f"{fil}: [indata] saknas ska vara en lista med text.")

    vag: Vagdata | None = None
    if "vag" in data:
        v = _tabell(data, "vag", fil)
        _kraver(v, ("kalla_x_m", "antal_kallpunkter", "vinkelupplosning_grader"), "vag", fil)
        antal = v["antal_kallpunkter"]
        if isinstance(antal, bool) or not isinstance(antal, int):
            raise BullerFel(f"{fil}: [vag] antal_kallpunkter ska vara ett heltal.")
        vag = Vagdata(
            kalla_x_m=_tal(v["kalla_x_m"], "vag.kalla_x_m", fil),
            antal_kallpunkter=antal,
            vinkelupplosning_grader=_tal(v["vinkelupplosning_grader"], "vag.vinkel", fil),
        )

    mott = _tabell(data, "mottagare", fil)
    mott_x = _tal(mott["x_m"], "mottagare.x_m", fil) if "x_m" in mott else None
    mott_h = (
        _tal(mott["hojd_over_mark_m"], "mottagare.hojd_over_mark_m", fil)
        if "hojd_over_mark_m" in mott
        else None
    )

    terrang: Terrangdata | None = None
    if "terrang" in data:
        t = _tabell(data, "terrang", fil)
        namn = ("x_m", "z_m", "flodesresistivitet_kNsm4", "rahet_m")
        _kraver(t, namn, "terrang", fil)
        terrang = Terrangdata(*(_tallista(t[n], f"terrang.{n}", fil) for n in namn))

    trafik_ra = data.get("trafik", [])
    if not isinstance(trafik_ra, list):
        raise BullerFel(f"{fil}: trafik ska anges som [[trafik]]-poster.")
    trafik: list[Trafikpost] = []
    for post in trafik_ra:
        if not isinstance(post, dict):
            raise BullerFel(f"{fil}: trafik ska anges som [[trafik]]-poster.")
        _kraver(post, ("kategori", "antal_fordon", "hastighet_km_h", "period"), "trafik", fil)
        kategori = post["kategori"]
        if isinstance(kategori, bool) or not isinstance(kategori, int):
            raise BullerFel(f"{fil}: [[trafik]] kategori ska vara ett heltal.")
        trafik.append(
            Trafikpost(
                kategori=kategori,
                antal_fordon=_tal(post["antal_fordon"], "trafik.antal_fordon", fil),
                hastighet_km_h=_tal(post["hastighet_km_h"], "trafik.hastighet_km_h", fil),
                period=str(post["period"]),
            )
        )

    perioder = {
        k: _tal(v, f"perioder_h.{k}", fil) for k, v in _tabell(data, "perioder_h", fil).items()
    }

    vader: Vader | None = None
    if "vader" in data:
        vd = _tabell(data, "vader", fil)
        okanda = [k for k in vd if k not in _VADERFALT]
        if okanda:
            raise BullerFel(
                f"{fil}: [vader] har okända fält ({', '.join(okanda)}). Tillåtna är fälten i "
                "`buller.typer.Vader`; annat läggs i [vader_tillagg]."
            )
        vader = Vader(**{k: _tal(v, f"vader.{k}", fil) for k, v in vd.items()})

    tillagg = {
        k: _tal(v, f"vader_tillagg.{k}", fil)
        for k, v in _tabell(data, "vader_tillagg", fil).items()
    }

    return N2kFall(
        id=str(data["id"]),
        namn=str(data["namn"]),
        grupp=str(data["grupp"]).upper(),
        typ=str(data["typ"]),
        beskrivning=str(data["beskrivning"]),
        status=str(data["status"]),
        kalla=_skalarer(_tabell(data, "kalla", fil), "kalla", fil),
        indata_fullstandig=indata["fullstandig"],
        indata_saknas=tuple(saknas),
        vag=vag,
        mottagare_x_m=mott_x,
        mottagare_hojd_m=mott_h,
        terrang=terrang,
        trafik=tuple(trafik),
        perioder_h=perioder,
        vader=vader,
        vader_tillagg=tillagg,
        ovrigt=_skalarer(_tabell(data, "ovrigt", fil), "ovrigt", fil),
        mapp=mapp,
    )


def las_fall(fall_mapp: str | Path) -> N2kFall:
    """Läser och validerar `fall.toml` i en fallmapp."""
    mapp = Path(fall_mapp)
    fil = mapp / FALLFIL
    if not fil.is_file():
        raise BullerFel(f"Testfallet saknar {FALLFIL}: {mapp}")
    try:
        data = tomllib.loads(fil.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BullerFel(f"Kunde inte läsa {fil}: {exc}") from exc
    return fall_fran_dict(data, fil, mapp)


def facit_fran_dict(data: Any, fil: Path) -> Facit:
    """Bygger ett `Facit` ur innehållet i en `facit.json`. Fel ger `BullerFel`."""
    if not isinstance(data, dict):
        raise BullerFel(f"{fil}: facit ska vara ett JSON-objekt.")
    if data.get("kontrakt") != KONTRAKT_FACIT:
        raise BullerFel(f"{fil}: kontrakt ska vara \"{KONTRAKT_FACIT}\".")
    kravda = ("ar_facit", "ar_facit_skal", "metodversion", "kalla", "tersband_hz", "serier")
    saknade = [n for n in kravda if n not in data]
    if saknade:
        raise BullerFel(f"{fil} saknar fält: {', '.join(saknade)}.")
    if not isinstance(data["ar_facit"], bool):
        raise BullerFel(f"{fil}: ar_facit ska vara sant eller falskt.")
    band = _tallista(data["tersband_hz"], "tersband_hz", fil)
    if band != TERSBAND_HZ:
        raise BullerFel(
            f"{fil}: tersband_hz ska vara de {ANTAL_BAND} banden i `buller.typer.TERSBAND_HZ`."
        )
    if not isinstance(data["serier"], list) or not isinstance(data["kalla"], dict):
        raise BullerFel(f"{fil}: serier ska vara en lista och kalla ett objekt.")
    serier: list[Facitserie] = []
    for post in data["serier"]:
        if not isinstance(post, dict) or "id" not in post:
            raise BullerFel(f"{fil}: varje serie ska vara ett objekt med id.")
        total = post.get("totalnivaer_db", {})
        spektrum = post.get("spektrum_db", {})
        villkor = post.get("villkor", {})
        if not all(isinstance(d, dict) for d in (total, spektrum, villkor)):
            raise BullerFel(f"{fil}: serien '{post['id']}' har fel struktur.")
        serier.append(
            Facitserie(
                id=str(post["id"]),
                villkor=_skalarer(villkor, "villkor", fil),
                totalnivaer_db={k: _tal(v, k, fil) for k, v in total.items()},
                spektrum_db={
                    k: np.asarray(_tallista(v, k, fil), dtype=np.float64)
                    for k, v in spektrum.items()
                },
            )
        )
    avlasning = data.get("avlasning", {})
    if not isinstance(avlasning, dict):
        raise BullerFel(f"{fil}: avlasning ska vara ett objekt.")
    return Facit(
        ar_facit=data["ar_facit"],
        ar_facit_skal=str(data["ar_facit_skal"]),
        metodversion=str(data["metodversion"]),
        kalla=_skalarer(data["kalla"], "kalla", fil),
        serier=tuple(serier),
        avlasning=_skalarer(avlasning, "avlasning", fil),
        regelverk=str(data.get("regelverk", "mp1335_2010")),
    )


def las_facit(fall_mapp: str | Path) -> Facit:
    """Läser och validerar `facit/facit.json` i en fallmapp."""
    fil = Path(fall_mapp) / FACITFIL
    if not fil.is_file():
        raise BullerFel(f"Testfallet saknar {FACITFIL}: {fall_mapp}")
    try:
        data = json.loads(fil.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BullerFel(f"Kunde inte läsa {fil}: {exc}") from exc
    return facit_fran_dict(data, fil)


def hitta_fall(rot: str | Path) -> list[N2kFall]:
    """Hittar alla Nord2000-fall (undermappar med `fall.toml`) direkt under `rot`."""
    rot_mapp = Path(rot)
    if not rot_mapp.is_dir():
        raise BullerFel(f"Mappen för Nord2000-fall saknas: {rot_mapp}")
    return [
        las_fall(post)
        for post in sorted(rot_mapp.iterdir())
        if post.is_dir() and (post / FALLFIL).is_file()
    ]


# --------------------------------------------------------------------------- skrivning


def _toml_varde(varde: object) -> str:
    if isinstance(varde, bool):
        return "true" if varde else "false"
    if isinstance(varde, str):
        return json.dumps(varde, ensure_ascii=False)
    if isinstance(varde, int):
        return str(varde)
    if isinstance(varde, float):
        if not np.isfinite(varde):
            raise BullerFel("Icke-ändliga tal kan inte skrivas till fall.toml.")
        return repr(varde)
    if isinstance(varde, list | tuple):
        return "[" + ", ".join(_toml_varde(v) for v in varde) + "]"
    raise BullerFel(f"Värdet {varde!r} kan inte skrivas till fall.toml.")


def _toml_tabell(namn: str, poster: Mapping[str, object], dubbel: bool = False) -> list[str]:
    huvud = f"[[{namn}]]" if dubbel else f"[{namn}]"
    return ["", huvud, *(f"{k} = {_toml_varde(v)}" for k, v in poster.items())]


def fall_till_toml(fall: N2kFall) -> str:
    """Serialiserar ett fall till TOML (N-fall/v1)."""
    rader = [
        f"id = {_toml_varde(fall.id)}",
        f"namn = {_toml_varde(fall.namn)}",
        f"grupp = {_toml_varde(fall.grupp)}",
        f"typ = {_toml_varde(fall.typ)}",
        f"beskrivning = {_toml_varde(fall.beskrivning)}",
        f"status = {_toml_varde(fall.status)}",
        f"kontrakt = {_toml_varde(KONTRAKT_FALL)}",
    ]
    rader += _toml_tabell("kalla", fall.kalla)
    rader += _toml_tabell(
        "indata", {"fullstandig": fall.indata_fullstandig, "saknas": list(fall.indata_saknas)}
    )
    if fall.vag is not None:
        rader += _toml_tabell(
            "vag",
            {
                "kalla_x_m": fall.vag.kalla_x_m,
                "antal_kallpunkter": fall.vag.antal_kallpunkter,
                "vinkelupplosning_grader": fall.vag.vinkelupplosning_grader,
            },
        )
    mottagare: dict[str, object] = {}
    if fall.mottagare_x_m is not None:
        mottagare["x_m"] = fall.mottagare_x_m
    if fall.mottagare_hojd_m is not None:
        mottagare["hojd_over_mark_m"] = fall.mottagare_hojd_m
    if mottagare:
        rader += _toml_tabell("mottagare", mottagare)
    if fall.terrang is not None:
        rader += _toml_tabell(
            "terrang",
            {
                "x_m": fall.terrang.x_m,
                "z_m": fall.terrang.z_m,
                "flodesresistivitet_kNsm4": fall.terrang.flodesresistivitet_kNsm4,
                "rahet_m": fall.terrang.rahet_m,
            },
        )
    for post in fall.trafik:
        rader += _toml_tabell(
            "trafik",
            {
                "kategori": post.kategori,
                "antal_fordon": post.antal_fordon,
                "hastighet_km_h": post.hastighet_km_h,
                "period": post.period,
            },
            dubbel=True,
        )
    if fall.perioder_h:
        rader += _toml_tabell("perioder_h", fall.perioder_h)
    if fall.vader is not None:
        rader += _toml_tabell("vader", {n: getattr(fall.vader, n) for n in _VADERFALT})
    if fall.vader_tillagg:
        rader += _toml_tabell("vader_tillagg", fall.vader_tillagg)
    if fall.ovrigt:
        rader += _toml_tabell("ovrigt", fall.ovrigt)
    return "\n".join(rader) + "\n"


def facit_till_dict(facit: Facit) -> dict[str, Any]:
    """Serialiserar facit till ett JSON-vänligt objekt (N-facit/v1)."""
    return {
        "kontrakt": KONTRAKT_FACIT,
        "ar_facit": facit.ar_facit,
        "ar_facit_skal": facit.ar_facit_skal,
        "metodversion": facit.metodversion,
        "regelverk": facit.regelverk,
        "kalla": dict(facit.kalla),
        "avlasning": dict(facit.avlasning),
        "tersband_hz": list(TERSBAND_HZ),
        "serier": [
            {
                "id": s.id,
                "villkor": dict(s.villkor),
                "totalnivaer_db": dict(s.totalnivaer_db),
                "spektrum_db": {k: [float(v) for v in sp] for k, sp in s.spektrum_db.items()},
            }
            for s in facit.serier
        ],
    }


def skriv_facit(facit: Facit, fall_mapp: str | Path) -> Path:
    """Skriver `facit/facit.json` i fallmappen och returnerar sökvägen."""
    fil = Path(fall_mapp) / FACITFIL
    fil.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(facit_till_dict(facit), ensure_ascii=False, indent=1)
    fil.write_text(text + "\n", encoding="utf-8")
    return fil


def skriv_fall(fall: N2kFall, fall_mapp: str | Path, facit: Facit | None = None) -> Path:
    """Skriver `fall.toml` (och facit om det anges) och returnerar fallmappen."""
    mapp = Path(fall_mapp)
    mapp.mkdir(parents=True, exist_ok=True)
    (mapp / FALLFIL).write_text(fall_till_toml(fall), encoding="utf-8")
    if facit is not None:
        skriv_facit(facit, mapp)
    return mapp
