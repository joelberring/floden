"""Import och sammanslagning av 3D-baskartans linjer med markmodell/DEM.

Läser 3D-vektorlinjer (`LineStringZ` / `MultiLineStringZ`, samt 2D-reserv med explicit
osäkerhetsmarkering) för:
- `kantsten` (gatukant / kantstenskrön),
- `sankt_kantsten` (sänkta infarter, övergångsställen),
- `mur` / `stodmur`,
- `gata_mittlinje` och `rannsten_botten`.

Koordinater i valfri SWEREF 99-zon (`EPSG:3006`–`3018`) konverteras vid inläsning till
`EPSG:3011` med bevarad höjd i RH 2000. Icke-öppen baskarta checkas aldrig in i git.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from gemensamt.koordinater import transformera
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from markmodell.brytlinjer import STANDARDHOJDER_M, Brytlinje
from markmodell.gatuprofiler import TYP_KANTSTEN_HOJD_M, TYP_OSAKERHET_Z_M, TYP_TVARFALL
from markmodell.k1_skrivare import (
    EPSG_SWEREF99_1800,
    _gpkg_linestring_blob,
    _gpkg_linestring_z_blob,
    _initiera_gpkg_bas,
    skriv_brytlinjer_gpkg,
)
from markmodell.rutnat import Ruta
from testsvit.k1_io import GeoTransformInfo

_BASKARTA_TYP_ALIAS: dict[str, str] = {
    "kantsten": "kantsten",
    "gatukant": "kantsten",
    "kantstenskron": "kantsten",
    "kantstenskrön": "kantsten",
    "sankt_kantsten": "sankt_kantsten",
    "sänkt_kantsten": "sankt_kantsten",
    "infart": "sankt_kantsten",
    "overgangsstalle": "sankt_kantsten",
    "övergångsställe": "sankt_kantsten",
    "mur": "mur",
    "stodmur": "stodmur",
    "stödmur": "stodmur",
    "gata_mittlinje": "gata_mittlinje",
    "mittlinje": "gata_mittlinje",
    "vagmitt": "gata_mittlinje",
    "vägmitt": "gata_mittlinje",
    "rannsten_botten": "rannsten_botten",
    "rännsten_botten": "rannsten_botten",
    "rannsten": "rannsten_botten",
}

BASKARTA_3D_OSAKERHET_M: float = 0.02
SANKT_KANTSTEN_TYP_HOJD_M: float = 0.02


class Baskarta3DFel(Exception):
    """Fel vid inläsning eller sammanslagning av 3D-baskartans linjer."""


@dataclass(frozen=True)
class Linje3D:
    """En inmätt 3D- (eller 2D-) vektorlinje i EPSG:3011 / RH 2000."""

    typ: str
    punkter_3d: tuple[tuple[float, float, float | None], ...]
    lager: str = ""
    kalla: str = "baskarta_3d"

    @property
    def har_z(self) -> bool:
        return all(p[2] is not None for p in self.punkter_3d)

    @property
    def punkter_2d(self) -> tuple[tuple[float, float], ...]:
        return tuple((p[0], p[1]) for p in self.punkter_3d)

    @property
    def z_varden(self) -> tuple[float, ...] | None:
        if not self.har_z:
            return None
        return tuple(float(p[2]) for p in self.punkter_3d if p[2] is not None)


@dataclass(frozen=True)
class Baskarta3DResultat:
    """Resultat från sammanslagning av 3D-baskarta med markmodell/DEM."""

    brytlinjer: list[Brytlinje]
    foradlad_mask: NDArray[np.bool_]
    medel_skillnad_m: float
    max_skillnad_m: float
    antal_3d_linjer: int
    antal_typvarde_linjer: int
    varningar: tuple[str, ...] = ()


def normalisera_baskartatyp(typ: str) -> str | None:
    """Mappar baskartans lager- eller typnamn till kanonisk intern typ."""
    if not isinstance(typ, str):
        return None
    ren = typ.strip().lower().replace(" ", "_").replace("-", "_")
    if ren in _BASKARTA_TYP_ALIAS:
        return _BASKARTA_TYP_ALIAS[ren]
    for nyckel, kanonisk in _BASKARTA_TYP_ALIAS.items():
        if nyckel in ren:
            return kanonisk
    return None


def _avkoda_wkb_linjer(
    wkb: bytes, offset: int = 0
) -> tuple[list[list[tuple[float, float, float | None]]], int]:
    """Avkodar WKB LineString / LineStringZ / MultiLineString / MultiLineStringZ."""
    if offset + 5 > len(wkb):
        raise Baskarta3DFel("Trunkerad WKB-geometri.")
    byte_order = wkb[offset]
    endian = "<" if byte_order == 1 else ">"
    wkb_type_raw = struct.unpack_from(f"{endian}I", wkb, offset + 1)[0]
    offset += 5

    # Hantera både EWKB (0x80000000 = Z, 0x40000000 = M, 0x20000000 = SRID) och ISO WKB (1000..3999)
    ewkb_z = bool(wkb_type_raw & 0x80000000)
    ewkb_m = bool(wkb_type_raw & 0x40000000)
    ewkb_srid = bool(wkb_type_raw & 0x20000000)
    wkb_type = wkb_type_raw & 0x0FFFFFFF
    if ewkb_srid:
        offset += 4

    iso_dim = wkb_type // 1000
    base_type = wkb_type % 1000
    har_z = ewkb_z or iso_dim in (1, 3)
    har_m = ewkb_m or iso_dim in (2, 3)

    if base_type == 2:  # LineString
        (n_pts,) = struct.unpack_from(f"{endian}I", wkb, offset)
        offset += 4
        dims = 2 + int(har_z) + int(har_m)
        steg = 8 * dims
        pts: list[tuple[float, float, float | None]] = []
        for _ in range(n_pts):
            vals = struct.unpack_from(f"{endian}{dims}d", wkb, offset)
            offset += steg
            z_val: float | None = float(vals[2]) if har_z else None
            pts.append((float(vals[0]), float(vals[1]), z_val))
        return [pts], offset

    if base_type == 5:  # MultiLineString
        (n_geom,) = struct.unpack_from(f"{endian}I", wkb, offset)
        offset += 4
        alla: list[list[tuple[float, float, float | None]]] = []
        for _ in range(n_geom):
            del_linjer, offset = _avkoda_wkb_linjer(wkb, offset)
            alla.extend(del_linjer)
        return alla, offset

    raise Baskarta3DFel(
        f"Stöder endast LineString/MultiLineString i 3D-baskarta, fick WKB-typ {wkb_type_raw}."
    )


def _avkoda_gpkg_geometri(blob: bytes) -> tuple[int, list[list[tuple[float, float, float | None]]]]:
    """Avkodar GeoPackageBinary (GP) header + WKB till `(srs_id, linjer)`."""
    if len(blob) < 8 or blob[:2] != b"GP":
        raise Baskarta3DFel("Ogiltig GeoPackageBinary-header (saknar 'GP').")
    flags = blob[3]
    endian = "<" if (flags & 0x01) else ">"
    env_ind = (flags >> 1) & 0x07
    env_storlek = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env_ind)
    if env_storlek is None:
        raise Baskarta3DFel(f"Ogiltig GeoPackage-envelopeindikator: {env_ind}")
    (srs_id,) = struct.unpack_from(f"{endian}i", blob, 4)
    wkb_start = 8 + env_storlek
    linjer, _ = _avkoda_wkb_linjer(blob, wkb_start)
    return int(srs_id), linjer


def las_3d_baskarta_gpkg(
    sokvag: str | Path,
    *,
    lager: str | None = None,
    epsg: int | None = None,
) -> list[Linje3D]:
    """Läser 3D-baskartelinjer ur en GeoPackage-fil och konverterar till EPSG:3011 / RH 2000."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise Baskarta3DFel(f"3D-baskartefil saknas: {fil}")

    conn = sqlite3.connect(str(fil))
    try:
        rader_meta = conn.execute(
            "SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns"
        ).fetchall()
        if not rader_meta:
            raise Baskarta3DFel(f"Inga geometrilager hittades i {fil}.")

        vald = None
        if lager is not None:
            for rad in rader_meta:
                if rad[0] == lager:
                    vald = rad
                    break
            if vald is None:
                raise Baskarta3DFel(f"Lagret '{lager}' finns inte i {fil}.")
        else:
            vald = rader_meta[0]

        tabell, geom_kol, tabell_srs = str(vald[0]), str(vald[1]), int(vald[2])
        cur = conn.execute(f"SELECT * FROM {tabell}")  # noqa: S608 - validerat mot gpkg_geometry_columns
        kolumner = [d[0] for d in cur.description]
        typ_kol = next(
            (k for k in ("typ", "objekttyp", "lager", "layer", "klass") if k in kolumner),
            None,
        )
        if typ_kol is None:
            raise Baskarta3DFel(f"Lagret '{tabell}' saknar typkolumn (t.ex. 'typ' eller 'lager').")

        resultat: list[Linje3D] = []
        for row in cur.fetchall():
            post = dict(zip(kolumner, row, strict=True))
            raw_typ = post.get(typ_kol)
            kanonisk = normalisera_baskartatyp(str(raw_typ or ""))
            if kanonisk is None:
                continue
            blob = post.get(geom_kol)
            if not isinstance(blob, bytes | bytearray):
                continue
            geom_srs, del_linjer = _avkoda_gpkg_geometri(bytes(blob))
            kalla_epsg = epsg if epsg is not None else (geom_srs if geom_srs > 0 else tabell_srs)
            if kalla_epsg <= 0:
                kalla_epsg = EPSG_SWEREF99_1800

            for pts in del_linjer:
                if len(pts) < 2:
                    continue
                if kalla_epsg != EPSG_SWEREF99_1800:
                    pts_3011: list[tuple[float, float, float | None]] = []
                    for e, n, z in pts:
                        e3011, n3011 = transformera(e, n, kalla_epsg, EPSG_SWEREF99_1800)
                        pts_3011.append((e3011, n3011, z))
                else:
                    pts_3011 = pts
                resultat.append(
                    Linje3D(
                        typ=kanonisk,
                        punkter_3d=tuple(pts_3011),
                        lager=str(raw_typ or ""),
                        kalla="baskarta_3d"
                        if all(p[2] is not None for p in pts_3011)
                        else "typvarde",
                    )
                )
        return resultat
    finally:
        conn.close()


def skriv_3d_baskarta_gpkg(
    sokvag: str | Path,
    linjer: Sequence[Linje3D],
    *,
    lager: str = "baskarta_3d",
    epsg: int = EPSG_SWEREF99_1800,
) -> Path:
    """Skriver en 3D-baskarta i GeoPackage (LineStringZ när Z finns, annars 2D LineString)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    xs = [p[0] for lin in linjer for p in lin.punkter_3d]
    ys = [p[1] for lin in linjer for p in lin.punkter_3d]
    bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
    har_z = any(lin.har_z for lin in linjer)

    conn = sqlite3.connect(str(mal))
    try:
        _initiera_gpkg_bas(conn)
        if epsg != EPSG_SWEREF99_1800:
            conn.execute(
                "INSERT OR IGNORE INTO gpkg_spatial_ref_sys "
                "VALUES (?, ?, 'EPSG', ?, 'undefined', ?)",
                (f"EPSG:{epsg}", epsg, epsg, f"EPSG:{epsg}"),
            )
        conn.execute(
            f"""
            CREATE TABLE {lager} (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                typ TEXT NOT NULL,
                lager TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change,
                 min_x, min_y, max_x, max_y, srs_id)
            VALUES (?, 'features', ?, '', '2026-01-01T00:00:00.000Z', ?, ?, ?, ?, ?);
            """,
            (lager, lager, bbox[0], bbox[1], bbox[2], bbox[3], epsg),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id, z, m)
            VALUES (?, 'geom', 'LINESTRING', ?, ?, 0);
            """,
            (lager, epsg, 1 if har_z else 0),
        )
        for lin in linjer:
            if lin.har_z and lin.z_varden is not None:
                blob = _gpkg_linestring_z_blob(lin.punkter_2d, lin.z_varden)
                if epsg != EPSG_SWEREF99_1800:
                    blob = blob[:4] + struct.pack("<i", epsg) + blob[8:]
            else:
                blob = _gpkg_linestring_blob(lin.punkter_2d)
                if epsg != EPSG_SWEREF99_1800:
                    blob = blob[:4] + struct.pack("<i", epsg) + blob[8:]
            conn.execute(
                f"INSERT INTO {lager} (geom, typ, lager) VALUES (?, ?, ?)",
                (blob, lin.typ, lin.lager or lin.typ),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def _sampla_dem_bilinjart(
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    geo: GeoTransformInfo,
    e_m: float,
    n_m: float,
) -> float:
    """Bilinjär sampling av DEM i `(e_m, n_m)` (klämd mot rasterkanten)."""
    hojd, bredd = mark_z.shape
    cx = (e_m - geo.x_min) / float(geo.pixelstorlek_x) - 0.5
    cy = (geo.y_max - n_m) / float(geo.pixelstorlek_y) - 0.5
    c0 = max(0, min(bredd - 1, math.floor(cx)))
    r0 = max(0, min(hojd - 1, math.floor(cy)))
    c1 = min(bredd - 1, c0 + 1)
    r1 = min(hojd - 1, r0 + 1)
    fx = max(0.0, min(1.0, cx - c0))
    fy = max(0.0, min(1.0, cy - r0))
    z00 = float(mark_z[r0, c0])
    z10 = float(mark_z[r0, c1])
    z01 = float(mark_z[r1, c0])
    z11 = float(mark_z[r1, c1])
    return float(
        z00 * (1.0 - fx) * (1.0 - fy)
        + z10 * fx * (1.0 - fy)
        + z01 * (1.0 - fx) * fy
        + z11 * fx * fy
    )


def _narmaste_botten_z(
    e_m: float,
    n_m: float,
    botten_linjer: Sequence[Linje3D],
    max_avstand_m: float = 1.5,
) -> float | None:
    """Hittar närmaste 3D-höjd på en matchande `rannsten_botten`-linje inom `max_avstand_m`."""
    basta_d2 = max_avstand_m * max_avstand_m
    basta_z: float | None = None
    for blin in botten_linjer:
        if not blin.har_z:
            continue
        pts = blin.punkter_3d
        for i in range(len(pts) - 1):
            x0, y0, z0 = pts[i]
            x1, y1, z1 = pts[i + 1]
            if z0 is None or z1 is None:
                continue
            dx, dy = x1 - x0, y1 - y0
            len2 = dx * dx + dy * dy
            t = (
                0.0
                if len2 <= 1e-12
                else max(0.0, min(1.0, ((e_m - x0) * dx + (n_m - y0) * dy) / len2))
            )
            px = x0 + t * dx
            py = y0 + t * dy
            d2 = (e_m - px) ** 2 + (n_m - py) ** 2
            if d2 <= basta_d2:
                basta_d2 = d2
                basta_z = float(z0) + t * (float(z1) - float(z0))
    return basta_z


def _markera_korridor(
    mask: NDArray[np.bool_],
    punkter: Sequence[tuple[float, float]],
    geo: GeoTransformInfo,
    halvbredd_m: float,
) -> None:
    """Flaggar celler inom `halvbredd_m` från en vektorlinje som förädlade av 3D-baskartan."""
    hojd, bredd = mask.shape
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    r2 = halvbredd_m * halvbredd_m
    for i in range(len(punkter) - 1):
        x0, y0 = punkter[i]
        x1, y1 = punkter[i + 1]
        c_min = max(0, math.floor((min(x0, x1) - halvbredd_m - geo.x_min) / dx))
        c_max = min(bredd - 1, math.ceil((max(x0, x1) + halvbredd_m - geo.x_min) / dx))
        r_min = max(0, math.floor((geo.y_max - (max(y0, y1) + halvbredd_m)) / dy))
        r_max = min(hojd - 1, math.ceil((geo.y_max - (min(y0, y1) - halvbredd_m)) / dy))
        if c_min > c_max or r_min > r_max:
            continue
        seg_dx, seg_dy = x1 - x0, y1 - y0
        len2 = seg_dx * seg_dx + seg_dy * seg_dy
        for r in range(r_min, r_max + 1):
            ny = geo.y_max - (r + 0.5) * dy
            for c in range(c_min, c_max + 1):
                ex = geo.x_min + (c + 0.5) * dx
                t = (
                    0.0
                    if len2 <= 1e-12
                    else max(0.0, min(1.0, ((ex - x0) * seg_dx + (ny - y0) * seg_dy) / len2))
                )
                px = x0 + t * seg_dx
                py = y0 + t * seg_dy
                if (ex - px) ** 2 + (ny - py) ** 2 <= r2:
                    mask[r, c] = True


def kombinera_3d_baskarta_med_dem(
    linjer_3d: Sequence[Linje3D],
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    geo: GeoTransformInfo,
    *,
    standard_tvarfall: float = TYP_TVARFALL,
    standard_kantsten_m: float = TYP_KANTSTEN_HOJD_M,
    sankt_kantsten_m: float = SANKT_KANTSTEN_TYP_HOJD_M,
    korridor_halvbredd_m: float = 1.5,
    typvarde_osakerhet_m: float = TYP_OSAKERHET_Z_M,
) -> Baskarta3DResultat:
    """Kombinerar 3D-baskartans vektorlinjer med Lantmäteriets DEM (`mark_z`).

    - Linjer med 3D-höjd (`LineStringZ`) ger exakta `z_kron_rh2000` och `z_botten_rh2000`
      (från matchad `rannsten_botten`-linje eller samplad DEM) samt språnghöjd `hojd_m`.
    - Linjer utan Z (endast 2D) faller tillbaka på typvärde enligt T01b med `kalla="typvarde"`
      och förhöjd `osakerhet_m` — aldrig tyst.
    """
    botten_linjer = [lin for lin in linjer_3d if lin.typ == "rannsten_botten" and lin.har_z]
    brytlinjer: list[Brytlinje] = []
    foradlad_mask = np.zeros(mark_z.shape, dtype=bool)
    skillnader: list[float] = []
    antal_3d = 0
    antal_typ = 0
    varningar: list[str] = []

    for lin in linjer_3d:
        if lin.typ in ("gata_mittlinje", "rannsten_botten"):
            continue
        if lin.typ not in STANDARDHOJDER_M:
            continue

        pts_2d = lin.punkter_2d
        if lin.har_z and lin.z_varden is not None:
            z_kron = lin.z_varden
            z_botten_lista: list[float] = []
            for (e, n), zk in zip(pts_2d, z_kron, strict=True):
                zb_par = _narmaste_botten_z(e, n, botten_linjer)
                zb_dem = _sampla_dem_bilinjart(mark_z, geo, e, n)
                zb = zb_par if zb_par is not None else zb_dem
                z_botten_lista.append(zb)
                skillnader.append(abs(zk - zb_dem))
            z_botten = tuple(z_botten_lista)
            sprang = [max(0.0, zk - zb) for zk, zb in zip(z_kron, z_botten, strict=True)]
            hojd_m = float(np.median(sprang))
            brytlinjer.append(
                Brytlinje(
                    typ=lin.typ,
                    hojd_m=hojd_m,
                    punkter=pts_2d,
                    z_kron_rh2000=z_kron,
                    z_botten_rh2000=z_botten,
                    tvarfall=standard_tvarfall
                    if lin.typ in ("kantsten", "sankt_kantsten")
                    else None,
                    kalla="baskarta_3d",
                    osakerhet_m=BASKARTA_3D_OSAKERHET_M,
                )
            )
            _markera_korridor(foradlad_mask, pts_2d, geo, korridor_halvbredd_m)
            antal_3d += 1
        else:
            # 2D-linje utan Z: T01b-typvärde med explicit käll- och osäkerhetsflagga
            if lin.typ == "sankt_kantsten":
                typ_hojd = sankt_kantsten_m
            elif lin.typ == "kantsten":
                typ_hojd = standard_kantsten_m
            else:
                typ_hojd = STANDARDHOJDER_M[lin.typ]
            z_dem = tuple(_sampla_dem_bilinjart(mark_z, geo, e, n) for e, n in pts_2d)
            z_kron_est = tuple(zd + typ_hojd for zd in z_dem)
            brytlinjer.append(
                Brytlinje(
                    typ=lin.typ,
                    hojd_m=typ_hojd,
                    punkter=pts_2d,
                    z_kron_rh2000=z_kron_est,
                    z_botten_rh2000=z_dem,
                    tvarfall=standard_tvarfall
                    if lin.typ in ("kantsten", "sankt_kantsten")
                    else None,
                    kalla="typvarde",
                    osakerhet_m=typvarde_osakerhet_m,
                )
            )
            antal_typ += 1

    if antal_typ > 0:
        varningar.append(
            f"{antal_typ} baskartelinjer saknade 3D-höjd (Z) och fick typvärde (kalla='typvarde', "
            f"osakerhet_m={typvarde_osakerhet_m:.2f} m)."
        )

    medel_diff = float(np.mean(skillnader)) if skillnader else 0.0
    max_diff = float(np.max(skillnader)) if skillnader else 0.0
    return Baskarta3DResultat(
        brytlinjer=brytlinjer,
        foradlad_mask=foradlad_mask,
        medel_skillnad_m=medel_diff,
        max_skillnad_m=max_diff,
        antal_3d_linjer=antal_3d,
        antal_typvarde_linjer=antal_typ,
        varningar=tuple(varningar),
    )


def skriv_baskarta_3d_k1(
    utmapp: str | Path,
    resultat: Baskarta3DResultat,
    ruta: Ruta,
    *,
    indata_filer: Sequence[Path] = (),
) -> dict[str, Path]:
    """Skriver `brytlinjer.gpkg` (K1 med 3D-kolumner) och `run.json` (K0) med DEM-statistik."""
    mapp = Path(utmapp)
    mapp.mkdir(parents=True, exist_ok=True)
    bryt_fil = skriv_brytlinjer_gpkg(mapp / "brytlinjer.gpkg", resultat.brytlinjer, ruta)
    indata = [
        FilPost(roll=f"indata_{i}", fil=str(p), sha256=sha256_fil(p))
        for i, p in enumerate(indata_filer)
        if p.is_file()
    ]
    manifest = skapa_manifest(
        modul="baskarta_3d",
        indata=indata,
        parametrar={
            "ruta_id": ruta.namn,
            "antal_3d_linjer": resultat.antal_3d_linjer,
            "antal_typvarde_linjer": resultat.antal_typvarde_linjer,
        },
        utdata=[FilPost(roll="brytlinjer", fil=bryt_fil.name, sha256=sha256_fil(bryt_fil))],
        matvarden={
            "dem_medel_skillnad_m": round(resultat.medel_skillnad_m, 4),
            "dem_max_skillnad_m": round(resultat.max_skillnad_m, 4),
            "foradlade_celler": int(np.count_nonzero(resultat.foradlad_mask)),
        },
        varningar=list(resultat.varningar),
    )
    run_fil = skriv_manifest(manifest, mapp / "run.json")
    return {"brytlinjer": bryt_fil, "run": run_fil}
