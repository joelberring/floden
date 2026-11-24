"""Byggnadsregister för K1 v2: `byggnader.gpkg` med stabila id:n och höjder satta mot laser.

K1 v1 hade bara byggnader som hinder inbrända i `mark.tif`. v2 lägger till ett register
(`byggnader.gpkg`, lager `byggnader`) som luft, buller och dagsljus kan läsa, och som kärnan
kan ändra (`byggnad_bort`, `byggnad_hojd`, `byggnad_ny` i K9).

- `byggnad_id` är deterministiskt: sha256 av källa + källans id om källan har id, annars av den
  normaliserade geometrin (1 cm, startpunkt och orientering kanoniska). Samma byggnad får samma id
  i alla rutor, eftersom hela polygonen (inte den utklippta) skrivs i varje ruta den berör.
- Höjd: källans absoluta takhöjd > laser (klass 6, percentil av punkterna i polygonen) > källans
  relativa höjd (`hojd_m`, våningar) > standardhöjd. Källan till höjden står i `hojdkalla` och
  osäkerheten i `osakerhet_hojd_m`; en standardhöjd är en gissning och flaggas som sådan.
- Taket är platt på `takhojd_rh2000` i `mark.tif`/ytmodellen (`max(terräng, takhöjd)` i polygonen).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray

from markmodell.hinder_oppningar import Byggnad, byggnad_maske
from markmodell.k1_skrivare import EPSG_SWEREF99_1800, K1SkrivFel, _initiera_gpkg_bas
from markmodell.rutnat import Ruta

LAS_BYGGNADSKLASS = 6
"""ASPRS-klass för byggnad i laserdata."""

LAGER_BYGGNADER = "byggnader"
STANDARD_HINDERHOJD_M = 10.0
"""Samma standardvärde som `Byggnad.hinderhojd_m`; behandlas som "okänd höjd"."""
STANDARD_OSAKERHET_M = 5.0
LASER_MIN_OSAKERHET_M = 0.15
VANINGSHOJD_FRAN_VANINGAR_M = 3.0
GILTIGA_HOJDKALLOR: frozenset[str] = frozenset(
    {"kalla_tak", "laser_klass6", "kalla_hojd", "kalla_vaningar", "standard", "andring"}
)

_GPKG_TIDSSTAMPEL = "2026-01-01T00:00:00.000Z"  # fast, så att filen blir byte-identisk


class ByggnadsregisterFel(Exception):
    """Fel vid byggnadsregistrets uppbyggnad, skrivning eller läsning."""


Punkt = tuple[float, float]
Ring = tuple[Punkt, ...]


def _andlig(varde: float, namn: str) -> float:
    v = float(varde)
    if not math.isfinite(v):
        raise ByggnadsregisterFel(f"{namn} måste vara ett ändligt tal, fick {varde!r}.")
    return v


def _oppen_ring(ring: Sequence[Sequence[float]]) -> Ring:
    punkter = [(float(p[0]), float(p[1])) for p in ring]
    if len(punkter) > 1 and punkter[0] == punkter[-1]:
        punkter = punkter[:-1]
    if len(punkter) < 3:
        raise ByggnadsregisterFel("En ring måste ha minst 3 olika hörn.")
    for e, n in punkter:
        if not (math.isfinite(e) and math.isfinite(n)):
            raise ByggnadsregisterFel(f"Ogiltig koordinat i ring: {(e, n)!r}")
    return tuple(punkter)


def _signerad_area(ring: Ring) -> float:
    s = 0.0
    for i, (x0, y0) in enumerate(ring):
        x1, y1 = ring[(i + 1) % len(ring)]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _orientera(ring: Ring, moturs: bool) -> Ring:
    ar_moturs = _signerad_area(ring) > 0.0
    return ring if ar_moturs == moturs else tuple(reversed(ring))


def normalisera_ringar(polygon: Ring, hal: Sequence[Ring] = ()) -> tuple[Ring, tuple[Ring, ...]]:
    """Yttre ring moturs och hål medurs, öppna ringar. Ändrar inte hörnens läge."""
    yttre = _orientera(_oppen_ring(polygon), True)
    inre = tuple(_orientera(_oppen_ring(r), False) for r in hal)
    return yttre, inre


def _kanonisk_ring(ring: Ring, moturs: bool, avrunda_m: float) -> list[list[float]]:
    r = [(round(x / avrunda_m) * avrunda_m, round(y / avrunda_m) * avrunda_m) for x, y in ring]
    r = list(_orientera(tuple(r), moturs))
    start = min(range(len(r)), key=lambda i: r[i])
    r = r[start:] + r[:start]
    return [[round(x, 6), round(y, 6)] for x, y in r]


def byggnad_id_fran_kalla(kalla: str, kalla_id: str) -> str:
    """Stabilt id av källa och källans eget id (t.ex. `("lm_byggnad", "uuid")`)."""
    if not kalla.strip() or not str(kalla_id).strip():
        raise ByggnadsregisterFel("kalla och kalla_id får inte vara tomma.")
    h = hashlib.sha256(f"kalla\0{kalla}\0{kalla_id}".encode()).hexdigest()
    return "b_" + h[:16]


def byggnad_id_fran_geometri(
    polygon: Ring, hal: Sequence[Ring] = (), avrunda_m: float = 0.01
) -> str:
    """Stabilt id av normaliserad geometri (1 cm, kanonisk start och orientering)."""
    data = {
        "yttre": _kanonisk_ring(_oppen_ring(polygon), True, avrunda_m),
        "hal": sorted(_kanonisk_ring(_oppen_ring(r), False, avrunda_m) for r in hal),
    }
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "b_" + hashlib.sha256(("geometri\0" + text).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ByggnadsKandidat:
    """En byggnad från en källa, före höjdsättning. Höjdfälten är det källan själv anger."""

    polygon: Ring
    hal: tuple[Ring, ...] = ()
    kalla: str = "okand"
    kalla_id: str | None = None
    byggnad_id: str | None = None
    tak_rh2000: float | None = None
    hojd_m: float | None = None
    vaningar: int | None = None
    andamal: str | None = None
    takform: str | None = None

    def __post_init__(self) -> None:
        yttre, inre = normalisera_ringar(self.polygon, self.hal)
        object.__setattr__(self, "polygon", yttre)
        object.__setattr__(self, "hal", inre)
        if not self.kalla.strip():
            raise ByggnadsregisterFel("kalla får inte vara tom.")
        if self.tak_rh2000 is not None:
            _andlig(self.tak_rh2000, "tak_rh2000")
        if self.hojd_m is not None and not (_andlig(self.hojd_m, "hojd_m") > 0.0):
            raise ByggnadsregisterFel(f"hojd_m måste vara > 0, fick {self.hojd_m}.")

    def id(self) -> str:
        if self.byggnad_id is not None:
            return self.byggnad_id
        if self.kalla_id is not None:
            return byggnad_id_fran_kalla(self.kalla, self.kalla_id)
        return byggnad_id_fran_geometri(self.polygon, self.hal)


def kandidat_fran_byggnad(b: Byggnad, kalla: str = "baskarta") -> ByggnadsKandidat:
    """Gör en `Byggnad` (t.ex. ur DXF-baskartan) till en kandidat.

    `hinderhojd_m` lika med standardvärdet 10 m räknas som "okänd höjd": den får laser eller
    standardhöjd (flaggad) i stället för att se ut som en mätning.
    """
    okand = math.isclose(b.hinderhojd_m, STANDARD_HINDERHOJD_M, abs_tol=1e-9)
    return ByggnadsKandidat(
        polygon=b.polygon,
        hal=b.hal,
        kalla=kalla,
        tak_rh2000=b.takhoyd_rh2000,
        hojd_m=None if okand else b.hinderhojd_m,
    )


@dataclass(frozen=True)
class ByggnadsPost:
    """En rad i `byggnader.gpkg`. Polygonen är öppen: yttre ring moturs, hål medurs."""

    byggnad_id: str
    polygon: Ring
    takhojd_rh2000: float
    markhojd_rh2000: float
    hojd_m: float
    kalla: str
    hal: tuple[Ring, ...] = ()
    takform: str | None = None
    andamal: str | None = None
    vaningar: int | None = None
    osakerhet_hojd_m: float | None = None
    hojdkalla: str = "standard"

    def __post_init__(self) -> None:
        if not self.byggnad_id.strip():
            raise ByggnadsregisterFel("byggnad_id får inte vara tomt.")
        yttre, inre = normalisera_ringar(self.polygon, self.hal)
        object.__setattr__(self, "polygon", yttre)
        object.__setattr__(self, "hal", inre)
        tak = _andlig(self.takhojd_rh2000, "takhojd_rh2000")
        mark = _andlig(self.markhojd_rh2000, "markhojd_rh2000")
        hojd = _andlig(self.hojd_m, "hojd_m")
        if not math.isclose(tak - mark, hojd, abs_tol=1e-6):
            raise ByggnadsregisterFel(
                f"{self.byggnad_id}: hojd_m {hojd} != takhojd - markhojd {tak - mark}."
            )
        if self.hojdkalla not in GILTIGA_HOJDKALLOR:
            raise ByggnadsregisterFel(f"Okänd hojdkalla '{self.hojdkalla}'.")
        if self.osakerhet_hojd_m is not None and _andlig(self.osakerhet_hojd_m, "osakerhet") < 0:
            raise ByggnadsregisterFel("osakerhet_hojd_m måste vara >= 0.")

    def till_byggnad(self) -> Byggnad:
        """Som `Byggnad` med absolut tak, för inbränning i en rasterruta."""
        return Byggnad(
            polygon=self.polygon, takhoyd_rh2000=self.takhojd_rh2000, hal=self.hal
        )

    def med_tak(self, takhojd_rh2000: float, hojdkalla: str = "andring") -> ByggnadsPost:
        """Kopia med ny takhöjd (markhöjden oförändrad)."""
        return replace(
            self,
            takhojd_rh2000=takhojd_rh2000,
            hojd_m=takhojd_rh2000 - self.markhojd_rh2000,
            hojdkalla=hojdkalla,
        )

    def geometri(self) -> Any:
        """Shapely-polygon (yttre ring med hål)."""
        return shapely.Polygon(self.polygon, list(self.hal))

    def till_dict(self) -> dict[str, Any]:
        return {
            "byggnad_id": self.byggnad_id,
            "polygon": [list(p) for p in self.polygon],
            "hal": [[list(p) for p in r] for r in self.hal],
            "takhojd_rh2000": self.takhojd_rh2000,
            "markhojd_rh2000": self.markhojd_rh2000,
            "hojd_m": self.hojd_m,
            "takform": self.takform,
            "andamal": self.andamal,
            "vaningar": self.vaningar,
            "kalla": self.kalla,
            "osakerhet_hojd_m": self.osakerhet_hojd_m,
            "hojdkalla": self.hojdkalla,
        }


MIN_TAK_OVER_MARK_M = 1.0
"""Lägsta rimliga takhöjd över mark (m) för en takhöjd ur källan; lägre ignoreras med varning."""


def markhojd_under_byggnad(
    terrang_z: NDArray[np.float32], ruta: Ruta, polygon: Ring, hal: Sequence[Ring] = ()
) -> float:
    """Median av terrängen i byggnadens celler; närmaste cell till tyngdpunkten om ingen träffas."""
    traff = byggnad_maske(Byggnad(polygon=polygon, hal=tuple(hal)), ruta)
    if traff is not None:
        mask, (r0, r1, k0, k1) = traff
        v = terrang_z[r0:r1, k0:k1][mask]
        if np.isfinite(v).any():
            return float(np.nanmedian(v))
        # Inget värde under huset (höjdmodell utan mark under byggnader): ta marken runt omkring.
        for marg in (2, 5, 10, 25, 50):
            ram = terrang_z[max(r0 - marg, 0) : r1 + marg, max(k0 - marg, 0) : k1 + marg]
            if np.isfinite(ram).any():
                return float(np.nanmedian(ram))
    cx = sum(p[0] for p in polygon) / len(polygon)
    cy = sum(p[1] for p in polygon) / len(polygon)
    rad, kol = ruta.koordinat_till_cell(cx, cy)
    rad = min(max(rad, 0), terrang_z.shape[0] - 1)
    kol = min(max(kol, 0), terrang_z.shape[1] - 1)
    v0 = float(terrang_z[rad, kol])
    if np.isfinite(v0):
        return v0
    for marg in (2, 5, 10, 25, 50):
        ram = terrang_z[max(rad - marg, 0) : rad + marg + 1, max(kol - marg, 0) : kol + marg + 1]
        if np.isfinite(ram).any():
            return float(np.nanmedian(ram))
    return v0


def hojdsatt_byggnader(
    kandidater: Sequence[ByggnadsKandidat],
    terrang_z: NDArray[np.float32],
    ruta: Ruta,
    *,
    laser_e: NDArray[np.float64] | None = None,
    laser_n: NDArray[np.float64] | None = None,
    laser_z: NDArray[np.float64] | None = None,
    laser_klasser: NDArray[np.int32] | NDArray[np.uint8] | None = None,
    byggnadshojd_fran_laser: bool = True,
    min_laserpunkter: int = 5,
    takpercentil: float = 90.0,
    standard_hinderhojd_m: float = STANDARD_HINDERHOJD_M,
) -> tuple[tuple[ByggnadsPost, ...], list[str]]:
    """Höjdsätter byggnader som berör rutan och returnerar poster sorterade på `byggnad_id`.

    Byggnader som inte berör rutans yttre utsträckning hoppas över. Samma id två gånger ger fel
    (samma byggnad från två källor ska inte få två rader).
    """
    if terrang_z.shape != ruta.form_rc:
        raise ByggnadsregisterFel(
            f"terrang_z har form {terrang_z.shape}, förväntade {ruta.form_rc}."
        )
    har_laser = (
        byggnadshojd_fran_laser
        and laser_e is not None
        and laser_n is not None
        and laser_z is not None
        and laser_klasser is not None
    )
    tak_e = tak_n = tak_z = np.empty(0, dtype=np.float64)
    if har_laser:
        assert laser_e is not None and laser_n is not None and laser_z is not None
        assert laser_klasser is not None
        k = np.asarray(laser_klasser)
        if not (k.shape == laser_e.shape == laser_n.shape == laser_z.shape):
            raise ByggnadsregisterFel("Laserpunkternas arrayer måste ha samma längd.")
        urval = (k == LAS_BYGGNADSKLASS) & np.isfinite(laser_z)
        tak_e, tak_n, tak_z = laser_e[urval], laser_n[urval], laser_z[urval]

    poster: dict[str, ByggnadsPost] = {}
    varningar: list[str] = []
    antal_standard = 0
    for kand in kandidater:
        traff = byggnad_maske(Byggnad(polygon=kand.polygon, hal=kand.hal), ruta)
        e_min, n_min, e_max, n_max = ruta.yttre_bbox
        xs = [p[0] for p in kand.polygon]
        ys = [p[1] for p in kand.polygon]
        utanfor = max(xs) < e_min or min(xs) > e_max or max(ys) < n_min or min(ys) > n_max
        if utanfor:
            continue
        bid = kand.id()
        if bid in poster:
            raise ByggnadsregisterFel(
                f"Byggnads-id {bid} förekommer två gånger (samma byggnad från två källor eller "
                "två identiska polygoner). Använd en källa per byggnad."
            )
        mark = markhojd_under_byggnad(terrang_z, ruta, kand.polygon, kand.hal)
        if not np.isfinite(mark):
            varningar.append(f"{bid}: ingen markhöjd under eller kring byggnaden; utelämnad")
            continue
        if traff is None:
            varningar.append(f"{bid}: ingen cellcentrum i byggnaden; markhöjd från närmaste cell")

        tak: float | None = None
        osak: float | None = None
        hojdkalla = "standard"
        if kand.tak_rh2000 is not None and float(kand.tak_rh2000) - mark >= MIN_TAK_OVER_MARK_M:
            tak, hojdkalla, osak = float(kand.tak_rh2000), "kalla_tak", None
        elif kand.tak_rh2000 is not None:
            # z under eller nära marken (z = 0 i baskartan, fel höjdsystem): inte en takhöjd.
            over = float(kand.tak_rh2000) - mark
            varningar.append(f"{bid}: källans takhöjd ligger {over:.2f} m över marken; ignorerad")
        elif har_laser and tak_z.size:
            inne = shapely.contains_xy(
                shapely.Polygon(kand.polygon, list(kand.hal)), tak_e, tak_n
            )
            if int(inne.sum()) >= min_laserpunkter:
                z = tak_z[inne]
                kandidat_tak = float(np.percentile(z, takpercentil))
                if kandidat_tak - mark >= 0.5:
                    tak, hojdkalla = kandidat_tak, "laser_klass6"
                    osak = max(LASER_MIN_OSAKERHET_M, float(np.std(z)))
                else:
                    varningar.append(
                        f"{bid}: laserns takhöjd ligger {kandidat_tak - mark:.2f} m över marken; "
                        "ignorerad"
                    )
        if tak is None and kand.hojd_m is not None:
            tak, hojdkalla, osak = mark + float(kand.hojd_m), "kalla_hojd", None
        if tak is None and kand.vaningar is not None and kand.vaningar > 0:
            tak = mark + kand.vaningar * VANINGSHOJD_FRAN_VANINGAR_M
            hojdkalla, osak = "kalla_vaningar", VANINGSHOJD_FRAN_VANINGAR_M
        if tak is None:
            tak = mark + standard_hinderhojd_m
            hojdkalla, osak = "standard", STANDARD_OSAKERHET_M
            antal_standard += 1

        poster[bid] = ByggnadsPost(
            byggnad_id=bid,
            polygon=kand.polygon,
            hal=kand.hal,
            takhojd_rh2000=tak,
            markhojd_rh2000=mark,
            hojd_m=tak - mark,
            takform=kand.takform,
            andamal=kand.andamal,
            vaningar=kand.vaningar,
            kalla=kand.kalla,
            osakerhet_hojd_m=osak,
            hojdkalla=hojdkalla,
        )
    if antal_standard:
        varningar.append(
            f"{antal_standard} byggnader saknade höjdkälla och fick standardhöjden "
            f"{standard_hinderhojd_m:g} m (osäkerhet {STANDARD_OSAKERHET_M:g} m)"
        )
    return tuple(poster[k] for k in sorted(poster)), varningar


# --- GeoPackage ------------------------------------------------------------------------------


def _gpkg_polygon_blob(post: ByggnadsPost) -> bytes:
    geom = post.geometri()
    minx, miny, maxx, maxy = geom.bounds
    # Header: "GP", version 0, flags 0x03 (liten ände + XY-envelope), srs_id
    header = struct.pack("<2sBBi", b"GP", 0, 0x03, EPSG_SWEREF99_1800)
    header += struct.pack("<4d", minx, maxx, miny, maxy)
    return header + bytes(shapely.to_wkb(geom, byte_order=1))


def skriv_byggnader_gpkg(
    sokvag: str | Path,
    poster: Sequence[ByggnadsPost],
    bbox_fallback: tuple[float, float, float, float],
) -> Path:
    """Skriver `byggnader.gpkg` (lager `byggnader`, EPSG:3011). Byte-identisk för samma poster."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()
    sorterade = sorted(poster, key=lambda p: p.byggnad_id)
    ids = [p.byggnad_id for p in sorterade]
    if len(set(ids)) != len(ids):
        raise K1SkrivFel("byggnad_id måste vara unikt i byggnader.gpkg.")
    if sorterade:
        bs = [p.geometri().bounds for p in sorterade]
        bbox = (
            min(b[0] for b in bs),
            min(b[1] for b in bs),
            max(b[2] for b in bs),
            max(b[3] for b in bs),
        )
    else:
        bbox = bbox_fallback

    conn = sqlite3.connect(str(mal))
    try:
        _initiera_gpkg_bas(conn)
        conn.execute(
            f"""
            CREATE TABLE {LAGER_BYGGNADER} (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                byggnad_id TEXT NOT NULL UNIQUE,
                takhojd_rh2000 REAL NOT NULL,
                markhojd_rh2000 REAL NOT NULL,
                hojd_m REAL NOT NULL,
                takform TEXT,
                andamal TEXT,
                vaningar INTEGER,
                kalla TEXT NOT NULL,
                osakerhet_hojd_m REAL,
                hojdkalla TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change,
                 min_x, min_y, max_x, max_y, srs_id)
            VALUES (?, 'features', ?, '', ?, ?, ?, ?, ?, ?);
            """,
            (
                LAGER_BYGGNADER,
                LAGER_BYGGNADER,
                _GPKG_TIDSSTAMPEL,
                *bbox,
                EPSG_SWEREF99_1800,
            ),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id, z, m)
            VALUES (?, 'geom', 'POLYGON', ?, 0, 0);
            """,
            (LAGER_BYGGNADER, EPSG_SWEREF99_1800),
        )
        for p in sorterade:
            conn.execute(
                f"""
                INSERT INTO {LAGER_BYGGNADER}
                    (geom, byggnad_id, takhojd_rh2000, markhojd_rh2000, hojd_m, takform,
                     andamal, vaningar, kalla, osakerhet_hojd_m, hojdkalla)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _gpkg_polygon_blob(p),
                    p.byggnad_id,
                    float(p.takhojd_rh2000),
                    float(p.markhojd_rh2000),
                    float(p.hojd_m),
                    p.takform,
                    p.andamal,
                    p.vaningar,
                    p.kalla,
                    p.osakerhet_hojd_m,
                    p.hojdkalla,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def _wkb_efter_gp_header(blob: bytes) -> bytes:
    if len(blob) < 8 or blob[:2] != b"GP":
        raise ByggnadsregisterFel("Geometrin är inte GeoPackageBinary (saknar 'GP'-huvud).")
    flaggor = blob[3]
    envelope_typ = (flaggor >> 1) & 0x07
    langd = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope_typ)
    if langd is None:
        raise ByggnadsregisterFel(f"Okänd envelope-typ {envelope_typ} i GeoPackage-geometri.")
    return blob[8 + langd :]


def las_byggnader_gpkg(sokvag: str | Path) -> tuple[ByggnadsPost, ...]:
    """Läser `byggnader.gpkg` (lager `byggnader`). Sorterat på `byggnad_id`."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise ByggnadsregisterFel(f"Saknar {fil}.")
    conn = sqlite3.connect(f"file:{fil}?mode=ro", uri=True)
    try:
        try:
            rader = conn.execute(
                f"""SELECT geom, byggnad_id, takhojd_rh2000, markhojd_rh2000, hojd_m, takform,
                           andamal, vaningar, kalla, osakerhet_hojd_m, hojdkalla
                    FROM {LAGER_BYGGNADER} ORDER BY byggnad_id"""
            ).fetchall()
        except sqlite3.Error as exc:
            raise ByggnadsregisterFel(f"{fil} saknar lagret '{LAGER_BYGGNADER}': {exc}") from exc
    finally:
        conn.close()
    ut: list[ByggnadsPost] = []
    for geom, bid, tak, mark, hojd, takform, andamal, vaningar, kalla, osak, hkalla in rader:
        g = shapely.from_wkb(_wkb_efter_gp_header(bytes(geom)))
        if g.geom_type != "Polygon":
            raise ByggnadsregisterFel(f"{bid}: geometrin är {g.geom_type}, förväntade Polygon.")
        ut.append(
            ByggnadsPost(
                byggnad_id=bid,
                polygon=_oppen_ring(g.exterior.coords),
                hal=tuple(_oppen_ring(r.coords) for r in g.interiors),
                takhojd_rh2000=tak,
                markhojd_rh2000=mark,
                hojd_m=hojd,
                takform=takform,
                andamal=andamal,
                vaningar=vaningar,
                kalla=kalla,
                osakerhet_hojd_m=osak,
                hojdkalla=hkalla,
            )
        )
    return tuple(ut)


def poster_sha_underlag(poster: Sequence[ByggnadsPost]) -> str:
    """Kanonisk JSON av posterna (för receptets källpost)."""
    return json.dumps(
        [p.till_dict() for p in sorted(poster, key=lambda p: p.byggnad_id)],
        sort_keys=True,
        separators=(",", ":"),
    )


def kandidater_underlag(kandidater: Sequence[ByggnadsKandidat]) -> str:
    """Kanonisk JSON av kandidaterna (för receptets källpost)."""
    rader: list[Mapping[str, Any]] = [
        {
            "id": k.id(),
            "polygon": [list(p) for p in k.polygon],
            "hal": [[list(p) for p in r] for r in k.hal],
            "kalla": k.kalla,
            "tak_rh2000": k.tak_rh2000,
            "hojd_m": k.hojd_m,
            "vaningar": k.vaningar,
            "andamal": k.andamal,
            "takform": k.takform,
        }
        for k in kandidater
    ]
    rader.sort(key=lambda d: str(d["id"]))
    return json.dumps(rader, sort_keys=True, separators=(",", ":"))
