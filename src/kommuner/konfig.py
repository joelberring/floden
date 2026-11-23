# ruff: noqa: E501
"""Kommunkonfiguration (`data/kommuner/<id>.toml`, kontrakt `floden-kommun/v1`).

En kommun är konfigurerad, inte kodad: vilka källor som finns, deras licenser, vilket koordinatsystem
kommunen publicerar i, testområden och beräkningsparametrar står i en TOML-fil. Fler kommuner läggs till
med en ny fil, utan kodändring. Filen valideras strikt (okända nycklar och fel typer ger ett tydligt
`KommunKonfigFel`), eftersom en felstavad nyckel annars bara skulle tystas ned.

Intern beräkning är alltid EPSG:3011 (`AGENTS.md`, `docs/BESLUT.md`); `intern_epsg` finns i filen för
att det ska synas, och något annat än 3011 nekas tills ett beslut om lokalt koordinatsystem per kommun
finns (öppen fråga i `STATUS.md`).
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemensamt.koordinater import EPSG_INTERN, SWEREF99_ZONER

KONTRAKT = "floden-kommun/v1"
KOMMUNMAPP_REL = Path("data") / "kommuner"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
_PRODUKTER = ("laser", "orto")
_BYGGNADSKALLOR = ("filpaket", "wfs", "lantmateriet")
_HOJDKALLOR = ("filpaket", "lantmateriet")
_REPO_ROT = Path(__file__).resolve().parents[2]


class KommunKonfigFel(Exception):
    """Kommunfilen saknas eller är ogiltig. Texten säger vad som ska rättas."""


@dataclass(frozen=True)
class Testomrade:
    id: str
    namn: str
    beskrivning: str
    bbox: tuple[float, float, float, float]  # (e_min, n_min, e_max, n_max) i publicering_epsg
    motivering: str

    @property
    def sida_m(self) -> float:
        e0, n0, e1, n1 = self.bbox
        return max(e1 - e0, n1 - n0)


@dataclass(frozen=True)
class LantmaterietKonfig:
    produkter: tuple[str, ...]
    marginal_m: float
    kontext_m: float
    upplosning_m: float
    licens: str
    licens_anmarkning: str


@dataclass(frozen=True)
class ByggnadsKonfig:
    kalla: str
    wfs_url: str
    tillatna_varddomaner: tuple[str, ...]
    lager: str
    lager_monster: str
    max_objekt: int
    licens: str
    licens_anmarkning: str
    falt: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class HojdKonfig:
    """Varifrån markhöjden kommer. `reserv_lantmateriet`: använd Lantmäteriets laser om kommunens fil fallerar och kontot har nyckel."""

    kalla: str
    reserv_lantmateriet: bool


@dataclass(frozen=True)
class PaketKalla:
    """Ett eller flera ZIP-paket på kommunens öppna datasida."""

    urlar: tuple[str, ...]
    index_url: str          # HTML-sida vars .zip-länkar används när `urlar` är tom ("" = ingen)
    epsg: int
    lager_monster: str      # bara baskartan: lagernamn (uttryck), t.ex. "(?i)^tak$"
    licens: str
    licens_anmarkning: str


@dataclass(frozen=True)
class FilpaketKonfig:
    tillatna_varddomaner: tuple[str, ...]
    max_gib: float
    baskarta: PaketKalla | None
    hojdmodell: PaketKalla | None


@dataclass(frozen=True)
class BaskartaKonfig:
    lager: tuple[str, ...]
    licens: str


@dataclass(frozen=True)
class GeografiDef:
    id: str
    namn: str
    omrade: str
    tillrinning_cell_m: float
    max_buffert_m: float


@dataclass(frozen=True)
class KommunKonfig:
    id: str
    namn: str
    publicering_epsg: int
    standard_omrade: str
    omraden: Mapping[str, Testomrade]
    lantmateriet: LantmaterietKonfig
    byggnader: ByggnadsKonfig
    baskarta: BaskartaKonfig
    hojd: HojdKonfig
    filpaket: FilpaketKonfig | None
    dagsljus: Mapping[str, Any]
    skyfall: Mapping[str, Any]
    geografier: Mapping[str, GeografiDef]
    fil: Path = field(default=Path("."), compare=False)

    def omrade(self, omrade_id: str | None) -> Testomrade:
        valt = omrade_id or self.standard_omrade
        if valt not in self.omraden:
            raise KommunKonfigFel(
                f"Okänt område '{valt}' i {self.id}. Tillgängliga: "
                f"{', '.join(sorted(self.omraden))}."
            )
        return self.omraden[valt]


# ---------------------------------------------------------------------------------- tolkning


def _tabell(d: Mapping[str, Any], namn: str, tillatna: Sequence[str]) -> Mapping[str, Any]:
    t = d.get(namn)
    if not isinstance(t, Mapping):
        raise KommunKonfigFel(f"Avsnittet [{namn}] saknas i kommunfilen.")
    return _stram(t, f"[{namn}]", tillatna)


def _stram(t: Mapping[str, Any], var: str, tillatna: Sequence[str]) -> Mapping[str, Any]:
    okanda = sorted(set(t) - set(tillatna))
    if okanda:
        raise KommunKonfigFel(f"{var}: okända nycklar {okanda}. Tillåtna: {sorted(tillatna)}.")
    return t


def _text(t: Mapping[str, Any], nyckel: str, var: str, *, tom_ok: bool = False) -> str:
    v = t.get(nyckel)
    if not isinstance(v, str) or (not tom_ok and not v.strip()):
        raise KommunKonfigFel(f"{var}: '{nyckel}' ska vara en text{'' if tom_ok else ' (inte tom)'}.")
    return v.strip()


def _tal(t: Mapping[str, Any], nyckel: str, var: str, *, minst: float = 0.0) -> float:
    v = t.get(nyckel)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise KommunKonfigFel(f"{var}: '{nyckel}' ska vara ett tal.")
    if float(v) < minst:
        raise KommunKonfigFel(f"{var}: '{nyckel}' ska vara minst {minst:g}, fick {v}.")
    return float(v)


def _textlista(t: Mapping[str, Any], nyckel: str, var: str) -> tuple[str, ...]:
    v = t.get(nyckel)
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise KommunKonfigFel(f"{var}: '{nyckel}' ska vara en lista med texter.")
    return tuple(x.strip() for x in v)


def tolka_kommun(data: Mapping[str, Any], fil: Path = Path(".")) -> KommunKonfig:
    """Validerar en inläst kommunfil. Se `data/kommuner/goteborg.toml` för formatet."""
    _stram(
        data,
        "kommunfilen",
        (
            "kontrakt", "id", "namn", "publicering_epsg", "intern_epsg", "standard_omrade",
            "omraden", "geografier", "lantmateriet", "hojd", "filpaket", "byggnader", "baskarta", "dagsljus", "skyfall",
        ),
    )  # fmt: skip
    if data.get("kontrakt") != KONTRAKT:
        raise KommunKonfigFel(f"kontrakt ska vara '{KONTRAKT}', fick {data.get('kontrakt')!r}.")
    kid = _text(data, "id", "kommunfilen")
    if not _ID_RE.match(kid):
        raise KommunKonfigFel(f"id '{kid}' ska vara små bokstäver, siffror och bindestreck.")
    epsg = data.get("publicering_epsg")
    if isinstance(epsg, bool) or not isinstance(epsg, int) or epsg not in SWEREF99_ZONER:
        raise KommunKonfigFel(
            f"publicering_epsg ska vara en SWEREF 99-zon (EPSG:3006-3018), fick {epsg!r}."
        )
    intern = data.get("intern_epsg", EPSG_INTERN)
    if intern != EPSG_INTERN:
        raise KommunKonfigFel(
            f"intern_epsg ska vara {EPSG_INTERN}: Floden räknar internt i EPSG:3011 (AGENTS.md). "
            "Lokalt koordinatsystem per kommun är en öppen fråga (STATUS.md), inte ett val här."
        )

    raa = data.get("omraden")
    if not isinstance(raa, Mapping) or not raa:
        raise KommunKonfigFel("Avsnittet [omraden.<id>] saknas: minst ett testområde krävs.")
    omraden: dict[str, Testomrade] = {}
    for oid, post in raa.items():
        var = f"[omraden.{oid}]"
        if not isinstance(post, Mapping) or not _ID_RE.match(str(oid)):
            raise KommunKonfigFel(f"{var}: ogiltigt område-id (små bokstäver, siffror, bindestreck).")
        _stram(post, var, ("namn", "beskrivning", "bbox", "motivering"))
        bb = post.get("bbox")
        if (
            not isinstance(bb, list)
            or len(bb) != 4
            or not all(isinstance(x, int | float) and not isinstance(x, bool) for x in bb)
        ):
            raise KommunKonfigFel(f"{var}: 'bbox' ska vara [e_min, n_min, e_max, n_max].")
        e0, n0, e1, n1 = (float(x) for x in bb)
        if not (e0 < e1 and n0 < n1):
            raise KommunKonfigFel(f"{var}: 'bbox' har min större än eller lika med max.")
        sida = max(e1 - e0, n1 - n0)
        if not 50.0 <= sida <= 2000.0:
            raise KommunKonfigFel(
                f"{var}: längsta sidan är {sida:g} m. Provområden är 50-2000 m "
                "(etapp 3 siktar på 300-500 m)."
            )
        omraden[str(oid)] = Testomrade(
            id=str(oid),
            namn=_text(post, "namn", var),
            beskrivning=_text(post, "beskrivning", var),
            bbox=(e0, n0, e1, n1),
            motivering=_text(post, "motivering", var),
        )
    standard = _text(data, "standard_omrade", "kommunfilen")
    if standard not in omraden:
        raise KommunKonfigFel(f"standard_omrade '{standard}' finns inte under [omraden].")

    geos: dict[str, GeografiDef] = {}
    for gid, post in (data.get("geografier") or {}).items():
        var = f"[geografier.{gid}]"
        if not isinstance(post, Mapping) or not _ID_RE.match(str(gid)):
            raise KommunKonfigFel(f"{var}: ogiltigt geografi-id.")
        _stram(post, var, ("namn", "omrade", "tillrinning_cell_m", "max_buffert_m"))
        if _text(post, "omrade", var) not in omraden:
            raise KommunKonfigFel(f"{var}: området '{post.get('omrade')}' finns inte under [omraden].")
        geos[str(gid)] = GeografiDef(
            id=str(gid),
            namn=_text(post, "namn", var),
            omrade=_text(post, "omrade", var),
            tillrinning_cell_m=_tal(post, "tillrinning_cell_m", var, minst=1.0),
            max_buffert_m=_tal(post, "max_buffert_m", var, minst=100.0),
        )

    lm = _tabell(
        data, "lantmateriet",
        ("produkter", "marginal_m", "kontext_m", "upplosning_m", "licens", "licens_anmarkning"),
    )  # fmt: skip
    produkter = _textlista(lm, "produkter", "[lantmateriet]")
    if not produkter or any(p not in _PRODUKTER for p in produkter):
        raise KommunKonfigFel(f"[lantmateriet]: 'produkter' ska vara delmängd av {_PRODUKTER}.")

    hj = data.get("hojd", {})
    if not isinstance(hj, Mapping):
        raise KommunKonfigFel("[hojd] ska vara en tabell.")
    _stram(hj, "[hojd]", ("kalla", "reserv_lantmateriet"))
    hojd_kalla = _text(hj, "kalla", "[hojd]") if "kalla" in hj else "lantmateriet"
    if hojd_kalla not in _HOJDKALLOR:
        raise KommunKonfigFel(f"[hojd]: 'kalla' ska vara en av {_HOJDKALLOR}.")
    reserv = hj.get("reserv_lantmateriet", False)
    if not isinstance(reserv, bool):
        raise KommunKonfigFel("[hojd]: 'reserv_lantmateriet' ska vara true eller false.")
    if "laser" not in produkter and (hojd_kalla == "lantmateriet" or reserv):
        raise KommunKonfigFel("[lantmateriet]: 'laser' krävs när höjden hämtas från Lantmäteriet (kalla eller reserv).")

    fp_raa = data.get("filpaket")
    filpaket: FilpaketKonfig | None = None
    if fp_raa is not None:
        if not isinstance(fp_raa, Mapping):
            raise KommunKonfigFel("[filpaket] ska vara en tabell.")
        _stram(fp_raa, "[filpaket]", ("tillatna_varddomaner", "max_gib", "baskarta", "hojdmodell"))
        fdom = _textlista(fp_raa, "tillatna_varddomaner", "[filpaket]")
        if not fdom:
            raise KommunKonfigFel("[filpaket]: 'tillatna_varddomaner' får inte vara tom.")

        def _paket(namn: str, med_lager: bool) -> PaketKalla | None:
            t = fp_raa.get(namn)
            if t is None:
                return None
            var = f"[filpaket.{namn}]"
            if not isinstance(t, Mapping):
                raise KommunKonfigFel(f"{var} ska vara en tabell.")
            _stram(t, var, ("urlar", "index_url", "epsg", "lager_monster", "licens", "licens_anmarkning"))
            urlar = _textlista(t, "urlar", var)
            index_url = _text(t, "index_url", var, tom_ok=True)
            if not urlar and not index_url:
                raise KommunKonfigFel(f"{var}: ange 'urlar' eller 'index_url'.")
            monster_p = _text(t, "lager_monster", var, tom_ok=not med_lager) if med_lager else ""
            if med_lager:
                try:
                    re.compile(monster_p)
                except re.error as exc:
                    raise KommunKonfigFel(f"{var}: 'lager_monster' är inte ett giltigt uttryck: {exc}") from exc
            return PaketKalla(
                urlar=urlar, index_url=index_url, epsg=int(_tal(t, "epsg", var, minst=1.0)),
                lager_monster=monster_p, licens=_text(t, "licens", var),
                licens_anmarkning=_text(t, "licens_anmarkning", var),
            )  # fmt: skip

        filpaket = FilpaketKonfig(
            tillatna_varddomaner=fdom,
            max_gib=_tal(fp_raa, "max_gib", "[filpaket]", minst=0.1),
            baskarta=_paket("baskarta", True),
            hojdmodell=_paket("hojdmodell", False),
        )

    by = _tabell(
        data, "byggnader",
        (
            "kalla", "wfs_url", "tillatna_varddomaner", "lager", "lager_monster", "max_objekt",
            "licens", "licens_anmarkning", "falt",
        ),
    )  # fmt: skip
    if (by.get("kalla") == "filpaket" and (filpaket is None or filpaket.baskarta is None)) or (
        hojd_kalla == "filpaket" and (filpaket is None or filpaket.hojdmodell is None)
    ):
        raise KommunKonfigFel("[byggnader]/[hojd] med kalla = filpaket kräver [filpaket.baskarta] resp. [filpaket.hojdmodell].")
    kalla = _text(by, "kalla", "[byggnader]")
    if kalla not in _BYGGNADSKALLOR:
        raise KommunKonfigFel(f"[byggnader]: 'kalla' ska vara en av {_BYGGNADSKALLOR}.")
    falt_raa = by.get("falt", {})
    if not isinstance(falt_raa, Mapping):
        raise KommunKonfigFel("[byggnader.falt] ska vara en tabell.")
    falt = {str(k): _textlista(falt_raa, str(k), "[byggnader.falt]") for k in falt_raa}
    monster = _text(by, "lager_monster", "[byggnader]")
    try:
        re.compile(monster)
    except re.error as exc:
        raise KommunKonfigFel(f"[byggnader]: 'lager_monster' är inte ett giltigt uttryck: {exc}") from exc
    wfs_url = _text(by, "wfs_url", "[byggnader]", tom_ok=True)
    domaner = _textlista(by, "tillatna_varddomaner", "[byggnader]")
    if kalla == "wfs" and not (wfs_url and domaner):
        raise KommunKonfigFel("[byggnader]: 'wfs_url' och 'tillatna_varddomaner' krävs för kalla = wfs.")

    bk = _tabell(data, "baskarta", ("lager", "licens"))
    sky = _tabell(data, "skyfall", ("regn_id", "takvatten"))
    if not isinstance(sky.get("takvatten"), bool):
        raise KommunKonfigFel("[skyfall]: 'takvatten' ska vara true eller false.")
    dag = _tabell(
        data, "dagsljus",
        (
            "datum", "steg_min", "azimutriktningar", "cellstorlek_m", "hojd_max_m", "h_min_grader",
            "fasad_steg_m", "vaningshojd_m",
        ),
    )  # fmt: skip
    _textlista(dag, "datum", "[dagsljus]")
    cell = _tal(dag, "cellstorlek_m", "[dagsljus]", minst=0.25)
    upp = _tal(lm, "upplosning_m", "[lantmateriet]", minst=0.25)
    if abs(cell / upp - round(cell / upp)) > 1e-9 or round(cell / upp) < 1:
        raise KommunKonfigFel(
            f"[dagsljus] cellstorlek_m ({cell:g}) ska vara en heltalsmultipel av [lantmateriet] upplosning_m "
            f"({upp:g}), annars kan dagsljuslösaren inte läsa markmodellen."
        )

    return KommunKonfig(
        id=kid,
        namn=_text(data, "namn", "kommunfilen"),
        publicering_epsg=int(epsg),
        standard_omrade=standard,
        omraden=omraden,
        lantmateriet=LantmaterietKonfig(
            produkter=produkter,
            marginal_m=_tal(lm, "marginal_m", "[lantmateriet]"),
            kontext_m=_tal(lm, "kontext_m", "[lantmateriet]"),
            upplosning_m=_tal(lm, "upplosning_m", "[lantmateriet]", minst=0.25),
            licens=_text(lm, "licens", "[lantmateriet]"),
            licens_anmarkning=_text(lm, "licens_anmarkning", "[lantmateriet]"),
        ),
        byggnader=ByggnadsKonfig(
            kalla=kalla,
            wfs_url=wfs_url,
            tillatna_varddomaner=domaner,
            lager=_text(by, "lager", "[byggnader]", tom_ok=True),
            lager_monster=monster,
            max_objekt=int(_tal(by, "max_objekt", "[byggnader]", minst=1.0)),
            licens=_text(by, "licens", "[byggnader]"),
            licens_anmarkning=_text(by, "licens_anmarkning", "[byggnader]"),
            falt=falt,
        ),
        baskarta=BaskartaKonfig(
            lager=_textlista(bk, "lager", "[baskarta]"), licens=_text(bk, "licens", "[baskarta]")
        ),
        hojd=HojdKonfig(kalla=hojd_kalla, reserv_lantmateriet=reserv),
        filpaket=filpaket,
        dagsljus=dict(dag),
        skyfall=dict(sky),
        geografier=geos,
        fil=fil,
    )


def kommunfil(kommun_id: str, *, rot: Path | None = None, fil: Path | None = None) -> Path:
    """Sökvägen till kommunens fil: `fil` om den anges, annars `data/kommuner/<id>.toml` i
    arbetsmappen eller repots rot (i avbildningen är båda `/app`)."""
    if fil is not None:
        return fil
    if not _ID_RE.match(kommun_id):
        raise KommunKonfigFel(f"Ogiltigt kommun-id '{kommun_id}'.")
    for bas in (rot, Path.cwd(), _REPO_ROT):
        if bas is not None and (bas / KOMMUNMAPP_REL / f"{kommun_id}.toml").is_file():
            return bas / KOMMUNMAPP_REL / f"{kommun_id}.toml"
    kanda = ", ".join(lista_kommuner(rot)) or "inga"
    raise KommunKonfigFel(
        f"Kommunen '{kommun_id}' finns inte: data/kommuner/{kommun_id}.toml saknas. "
        f"Konfigurerade kommuner: {kanda}."
    )


def lista_kommuner(rot: Path | None = None) -> list[str]:
    ut: set[str] = set()
    for bas in (rot, Path.cwd(), _REPO_ROT):
        if bas is not None and (bas / KOMMUNMAPP_REL).is_dir():
            ut.update(p.stem for p in (bas / KOMMUNMAPP_REL).glob("*.toml"))
    return sorted(ut)


def las_kommun(
    kommun_id: str, *, rot: Path | None = None, fil: Path | None = None
) -> KommunKonfig:
    sokvag = kommunfil(kommun_id, rot=rot, fil=fil)
    try:
        with sokvag.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise KommunKonfigFel(f"{sokvag.name} är inte giltig TOML: {exc}") from exc
    except OSError as exc:
        raise KommunKonfigFel(f"{sokvag} går inte att läsa: {exc.strerror}.") from exc
    kfg = tolka_kommun(data, sokvag)
    if kfg.id != sokvag.stem and fil is None:
        raise KommunKonfigFel(f"id '{kfg.id}' i filen ska vara filnamnet '{sokvag.stem}'.")
    return kfg


def hitta_kommun_for_geografi(
    geografi_id: str, *, rot: Path | None = None, kommunfil_sokvag: Path | None = None
) -> KommunKonfig:
    """Kommunen vars fil definierar geografin (`[geografier.<id>]`)."""
    if kommunfil_sokvag is None:
        for kid in lista_kommuner(rot):
            k = las_kommun(kid, rot=rot)
            if geografi_id in k.geografier:
                return k
        raise KommunKonfigFel(
            f"Ingen kommunfil definierar geografin '{geografi_id}' ([geografier.{geografi_id}] "
            f"i data/kommuner/<kommun>.toml). Konfigurerade kommuner: "
            f"{', '.join(lista_kommuner(rot)) or 'inga'}."
        )
    k = las_kommun("x", fil=kommunfil_sokvag)
    if geografi_id not in k.geografier:
        raise KommunKonfigFel(f"{kommunfil_sokvag.name} definierar inte geografin '{geografi_id}'.")
    return k
