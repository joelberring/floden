"""Bigraderad bifurkationsdetektor för sänkgrafer under höjdosäkerhet (Q2).

Identifierar överfallspunkter/sadlar där en sänkas primära utlopp har så liten
höjdmarginal mot ett konkurrerande grannutlopp (inom `k_sigma * sigma_komb`) att
mätosäkerheten i höjdmodellen kan få vattnet att byta utlopp (`ut_till_id`).

Innehåller även en ren JSON-export (`exportera_bifiltrering_json`) utan GIS-beroenden
för bigraderade experiment (t.ex. i Python eller Macaulay2).
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sankgraf.typer import (
    BYGGNAD_NOD_ID,
    HAV_NOD_ID,
    SankGraf,
    SankGrafFel,
)
from testsvit.k1_io import EPSG_SWEREF99_1800, WKT_EPSG_3011, GeoTransformInfo

STANDARD_K_SIGMA = 2.0
STANDARD_REFERENSREGN_MM = 100.0


@dataclass(frozen=True)
class BifurkationsPunkt:
    """En topologiskt känslig eller stabil överfallspunkt med konkurrerande utlopp."""

    nod_id: int
    primar_ut_till_id: int | None
    alternativ_ut_till_id: int | None
    overfall_hojd_m: float
    alternativ_hojd_m: float
    marginal_m: float
    osakerhet_komb_m: float
    sakerhetsmarginal: float
    instabil: bool
    overfall_x: float
    overfall_y: float
    alternativ_x: float
    alternativ_y: float
    paverkad_avrinningsyta_m2: float
    paverkad_volym_m3: float

    def till_dict(self) -> dict[str, Any]:
        """Serialiserbar ordlista med SI-enheter i kolumnnamnen."""
        return {
            "nod_id": int(self.nod_id),
            "primar_ut_till_id": (
                int(self.primar_ut_till_id) if self.primar_ut_till_id is not None else None
            ),
            "alternativ_ut_till_id": (
                int(self.alternativ_ut_till_id) if self.alternativ_ut_till_id is not None else None
            ),
            "overfall_hojd_m": float(self.overfall_hojd_m),
            "alternativ_hojd_m": float(self.alternativ_hojd_m),
            "marginal_m": float(self.marginal_m),
            "osakerhet_komb_m": float(self.osakerhet_komb_m),
            "sakerhetsmarginal": (
                float(self.sakerhetsmarginal) if math.isfinite(self.sakerhetsmarginal) else None
            ),
            "instabil": bool(self.instabil),
            "overfall_x": float(self.overfall_x),
            "overfall_y": float(self.overfall_y),
            "alternativ_x": float(self.alternativ_x),
            "alternativ_y": float(self.alternativ_y),
            "paverkad_avrinningsyta_m2": float(self.paverkad_avrinningsyta_m2),
            "paverkad_volym_m3": float(self.paverkad_volym_m3),
        }


@dataclass(frozen=True)
class _SadelKant:
    """Lägsta sadelkant mellan två distinkta lövregioner (eller en lövregion och HAV)."""

    a: int
    b: int
    hojd_m: float
    osakerhet_m: float
    x: float
    y: float


def _cell_xy(geo: GeoTransformInfo, rad: float, kol: float) -> tuple[float, float]:
    """Konverterar (rad, kol) till koordinater i grafens koordinatsystem."""
    x = float(geo.x_min) + (kol + 0.5) * float(geo.pixelstorlek_x)
    y = float(geo.y_max) - (rad + 0.5) * float(geo.pixelstorlek_y)
    return x, y


def _bygg_osakerhetsraster(
    graf: SankGraf,
    osakerhet_m: NDArray[np.float32] | NDArray[np.float64] | float | None,
) -> NDArray[np.float64]:
    """Returnerar ett 2D-raster med höjdosäkerhet (m) per cell."""
    form = graf.mark_z.shape
    if osakerhet_m is None:
        ut = np.zeros(form, dtype=np.float64)
        for nid, nod in graf.noder.items():
            if nod.ar_lov:
                ut[graf.cell_till_nod == nid] = max(0.0, float(nod.osakerhet_m))
        return ut
    if isinstance(osakerhet_m, int | float):
        val = float(osakerhet_m)
        if not math.isfinite(val) or val < 0.0:
            raise SankGrafFel(f"osakerhet_m måste vara ändligt och >= 0, fick {osakerhet_m}.")
        return np.full(form, val, dtype=np.float64)
    arr = np.asarray(osakerhet_m, dtype=np.float64)
    if arr.shape != form:
        raise SankGrafFel(
            f"osakerhet_m har form {arr.shape}, förväntade {form}."
        )
    return np.where(np.isfinite(arr) & (arr >= 0.0), arr, 0.0)


def _samla_lov_sadelkanter(
    graf: SankGraf,
    sigma: NDArray[np.float64],
) -> list[_SadelKant]:
    """Hittar lägsta 4-grannsadel mellan varje par av distinkta lövnoder (samt mot HAV)."""
    ctn = graf.cell_till_nod
    z = np.asarray(graf.mark_z, dtype=np.float64)
    h, w = ctn.shape
    basta: dict[tuple[int, int], tuple[float, float, float, float]] = {}

    def uppdatera(a: int, b: int, z_val: float, s_val: float, rad: float, kol: float) -> None:
        nyckel = (a, b) if a <= b else (b, a)
        bef = basta.get(nyckel)
        x, y = _cell_xy(graf.geo, rad, kol)
        kand = (z_val, s_val, x, y)
        if bef is None or (z_val, x, y) < (bef[0], bef[2], bef[3]):
            basta[nyckel] = kand

    # Horisontella 4-grannar
    if w >= 2:
        l_id = ctn[:, :-1]
        r_id = ctn[:, 1:]
        z_l = z[:, :-1]
        z_r = z[:, 1:]
        giltig = (
            (l_id != r_id)
            & (l_id != BYGGNAD_NOD_ID)
            & (r_id != BYGGNAD_NOD_ID)
            & np.isfinite(z_l)
            & np.isfinite(z_r)
        )
        rader, kolar = np.nonzero(giltig)
        for r_i, c_i in zip(rader.tolist(), kolar.tolist(), strict=True):
            zl = float(z[r_i, c_i])
            zr = float(z[r_i, c_i + 1])
            if zl > zr:
                z_s, s_s = zl, float(sigma[r_i, c_i])
            elif zr > zl:
                z_s, s_s = zr, float(sigma[r_i, c_i + 1])
            else:
                z_s, s_s = zl, max(float(sigma[r_i, c_i]), float(sigma[r_i, c_i + 1]))
            uppdatera(int(ctn[r_i, c_i]), int(ctn[r_i, c_i + 1]), z_s, s_s, r_i, c_i + 0.5)

    # Vertikala 4-grannar
    if h >= 2:
        u_id = ctn[:-1, :]
        d_id = ctn[1:, :]
        z_u = z[:-1, :]
        z_d = z[1:, :]
        giltig = (
            (u_id != d_id)
            & (u_id != BYGGNAD_NOD_ID)
            & (d_id != BYGGNAD_NOD_ID)
            & np.isfinite(z_u)
            & np.isfinite(z_d)
        )
        rader, kolar = np.nonzero(giltig)
        for r_i, c_i in zip(rader.tolist(), kolar.tolist(), strict=True):
            zu = float(z[r_i, c_i])
            zd = float(z[r_i + 1, c_i])
            if zu > zd:
                z_s, s_s = zu, float(sigma[r_i, c_i])
            elif zd > zu:
                z_s, s_s = zd, float(sigma[r_i + 1, c_i])
            else:
                z_s, s_s = zu, max(float(sigma[r_i, c_i]), float(sigma[r_i + 1, c_i]))
            uppdatera(int(ctn[r_i, c_i]), int(ctn[r_i + 1, c_i]), z_s, s_s, r_i + 0.5, c_i)

    # Rasterranden ut mot HAV_NOD_ID
    kant_koordinater: list[tuple[int, int]] = []
    for c_i in range(w):
        kant_koordinater.append((0, c_i))
        if h > 1:
            kant_koordinater.append((h - 1, c_i))
    for r_i in range(1, h - 1):
        kant_koordinater.append((r_i, 0))
        if w > 1:
            kant_koordinater.append((r_i, w - 1))
    for r_i, c_i in kant_koordinater:
        nid = int(ctn[r_i, c_i])
        zv = float(z[r_i, c_i])
        if nid >= 0 and math.isfinite(zv):
            uppdatera(nid, HAV_NOD_ID, zv, float(sigma[r_i, c_i]), float(r_i), float(c_i))

    return [
        _SadelKant(a=a, b=b, hojd_m=val[0], osakerhet_m=val[1], x=val[2], y=val[3])
        for (a, b), val in sorted(basta.items())
    ]


def _berakna_uppstroms_avrinningsyta(graf: SankGraf) -> dict[int, float]:
    """Kumulativ avrinningsyta (m²) som når varje nod (egna löv + uppströms överfall)."""
    in_kanter: dict[int, list[int]] = {nid: [] for nid in graf.noder}
    for nid, nod in graf.noder.items():
        if nod.ut_till_id is not None and nod.ut_till_id in in_kanter:
            in_kanter[nod.ut_till_id].append(nid)

    lov_cache = {nid: set(graf.lov_under_nod(nid)) for nid in graf.noder}
    ut: dict[int, float] = {}
    for nid in sorted(graf.noder):
        besokta: set[int] = set()
        stack = [nid]
        lov_summa: set[int] = set()
        while stack:
            akt = stack.pop()
            if akt in besokta:
                continue
            besokta.add(akt)
            lov_summa.update(lov_cache[akt])
            stack.extend(in_kanter.get(akt, ()))
        ut[nid] = sum(float(graf.noder[lid].avrinningsyta_m2) for lid in lov_summa)
    return ut


def _mal_nod_for_lov(graf: SankGraf, u_id: int, mal_lov: int) -> int | None:
    """Översätter en grannlöv-cell till det närmaste syskonet/utloppsmålet för nod `u_id`."""
    if mal_lov == HAV_NOD_ID or mal_lov not in graf.noder:
        return None
    u_forfader: set[int] = set()
    akt: int | None = u_id
    while akt is not None and akt in graf.noder:
        u_forfader.add(akt)
        akt = graf.noder[akt].forald_id
    # Klättra uppåt från mal_lov tills föräldern ligger i u_forfader (eller är None).
    kand = mal_lov
    while True:
        forald = graf.noder[kand].forald_id
        if forald is None or forald in u_forfader:
            return kand
        kand = forald


def detektera_bifurkationer(
    graf: SankGraf,
    *,
    osakerhet_m: NDArray[np.float32] | NDArray[np.float64] | float | None = None,
    k_sigma: float = STANDARD_K_SIGMA,
    referensregn_mm: float = STANDARD_REFERENSREGN_MM,
    inkludera_stabila: bool = False,
) -> list[BifurkationsPunkt]:
    """Detekterar topologiskt instabila överfallspunkter inom höjdosäkerheten.

    För varje nod `u` i sänkgrafen:
    1. Hittar lägsta alternativa utloppssadel mot ett *annat* mål än `u.ut_till_id`.
    2. Beräknar marginalen `Δz = z_alt - z_öv`, kombinerad osäkerhet
       `σ_komb = sqrt(σ_öv² + σ_alt²)` och säkerhetsmarginalen `m = Δz / σ_komb`.
    3. Flaggar noden som topologiskt instabil (`instabil = True`) när `m <= k_sigma`.

    Returnerar deterministiskt sorterad lista (högst påverkad volym först, därefter
    minsta säkerhetsmarginal och `nod_id`).
    """
    if not math.isfinite(k_sigma) or k_sigma < 0.0:
        raise SankGrafFel(f"k_sigma måste vara ändligt och >= 0, fick {k_sigma}.")
    if not math.isfinite(referensregn_mm) or referensregn_mm < 0.0:
        raise SankGrafFel(f"referensregn_mm måste vara >= 0, fick {referensregn_mm}.")

    sigma = _bygg_osakerhetsraster(graf, osakerhet_m)
    sadelkanter = _samla_lov_sadelkanter(graf, sigma)
    uppstroms_area = _berakna_uppstroms_avrinningsyta(graf)
    lov_per_nod = {nid: set(graf.lov_under_nod(nid)) for nid in graf.noder}

    resultat: list[BifurkationsPunkt] = []
    for nid in sorted(graf.noder):
        nod = graf.noder[nid]
        if nod.ar_slutsanka:
            continue
        egna_lov = lov_per_nod[nid]
        if not egna_lov:
            continue

        primar_id = nod.ut_till_id
        if primar_id is not None and primar_id in lov_per_nod:
            primar_lov: set[int] | None = lov_per_nod[primar_id]
        else:
            primar_lov = None  # Primärt utlopp är HAV_NOD_ID / rasterranden

        basta_primar: tuple[float, float] | None = None
        basta_alt: tuple[float, float, int | None, float, float] | None = None

        for kant in sadelkanter:
            a_inne = kant.a in egna_lov
            b_inne = kant.b in egna_lov
            if a_inne == b_inne:
                continue
            yttre = kant.b if a_inne else kant.a

            ar_primar = yttre == HAV_NOD_ID if primar_lov is None else yttre in primar_lov

            if ar_primar:
                if basta_primar is None or kant.hojd_m < basta_primar[0]:
                    basta_primar = (kant.hojd_m, kant.osakerhet_m)
            else:
                alt_mal = _mal_nod_for_lov(graf, nid, yttre)
                alt_kand = (kant.hojd_m, kant.osakerhet_m, alt_mal, kant.x, kant.y)
                if basta_alt is None or (kant.hojd_m, kant.x, kant.y) < (
                    basta_alt[0],
                    basta_alt[3],
                    basta_alt[4],
                ):
                    basta_alt = alt_kand

        if basta_alt is None:
            continue

        z_ov = float(nod.overfall_rh2000)
        sigma_ov = basta_primar[1] if basta_primar is not None else max(0.0, float(nod.osakerhet_m))
        z_alt, sigma_alt, alt_mal_id, alt_x, alt_y = basta_alt

        marginal = max(0.0, z_alt - z_ov)
        sigma_komb = math.hypot(sigma_ov, sigma_alt)
        if sigma_komb > 0.0:
            sak_marg = marginal / sigma_komb
        else:
            sak_marg = 0.0 if marginal <= 0.0 else math.inf
        instabil = sak_marg <= k_sigma

        if not instabil and not inkludera_stabila:
            continue

        area_m2 = uppstroms_area[nid]
        vol_m3 = area_m2 * (referensregn_mm / 1000.0)
        resultat.append(
            BifurkationsPunkt(
                nod_id=nid,
                primar_ut_till_id=primar_id,
                alternativ_ut_till_id=alt_mal_id,
                overfall_hojd_m=z_ov,
                alternativ_hojd_m=z_alt,
                marginal_m=marginal,
                osakerhet_komb_m=sigma_komb,
                sakerhetsmarginal=sak_marg,
                instabil=instabil,
                overfall_x=float(nod.overfall_x),
                overfall_y=float(nod.overfall_y),
                alternativ_x=alt_x,
                alternativ_y=alt_y,
                paverkad_avrinningsyta_m2=area_m2,
                paverkad_volym_m3=vol_m3,
            )
        )

    resultat.sort(
        key=lambda p: (
            -p.paverkad_volym_m3,
            p.sakerhetsmarginal,
            p.nod_id,
        )
    )
    return resultat


def skriv_bifurkationer_json(
    punkter: Sequence[BifurkationsPunkt],
    utfil: str | Path,
    *,
    k_sigma: float = STANDARD_K_SIGMA,
    referensregn_mm: float = STANDARD_REFERENSREGN_MM,
) -> Path:
    """Skriver bifurkationssammanfattning som JSON (redo för webb och skissrapport)."""
    mal = Path(utfil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    innehall = {
        "format": "floden-bifurkationer/v1",
        "k_sigma": float(k_sigma),
        "referensregn_mm": float(referensregn_mm),
        "antal_instabila": sum(1 for p in punkter if p.instabil),
        "antal_totalt": len(punkter),
        "punkter": [p.till_dict() for p in punkter],
    }
    mal.write_text(json.dumps(innehall, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return mal


def _koda_gpkg_punkt(x: float, y: float, epsg: int) -> bytes:
    """GeoPackage StandardBinary (GP-header + WKB Point)."""
    gp_header = struct.pack("<2sBBi", b"GP", 0, 1, epsg)
    wkb = struct.pack("<BIdd", 1, 1, float(x), float(y))
    return gp_header + wkb


def skriv_bifurkationer_gpkg(
    punkter: Sequence[BifurkationsPunkt],
    utfil: str | Path,
    *,
    epsg: int = EPSG_SWEREF99_1800,
) -> Path:
    """Skriver bifurkationspunkter som ren GeoPackage (`bifurkationer.gpkg`) utan GDAL."""
    mal = Path(utfil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    con = sqlite3.connect(str(mal))
    try:
        con.execute("PRAGMA application_id = 1196444487")
        con.execute("PRAGMA user_version = 10300")
        con.executescript(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            );
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
                srs_id INTEGER
            );
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_gc PRIMARY KEY (table_name, column_name)
            );
            CREATE TABLE bifurkationer (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                nod_id INTEGER NOT NULL,
                primar_ut_till_id INTEGER,
                alternativ_ut_till_id INTEGER,
                overfall_hojd_m REAL NOT NULL,
                alternativ_hojd_m REAL NOT NULL,
                marginal_m REAL NOT NULL,
                osakerhet_komb_m REAL NOT NULL,
                sakerhetsmarginal REAL,
                instabil INTEGER NOT NULL,
                paverkad_avrinningsyta_m2 REAL NOT NULL,
                paverkad_volym_m3 REAL NOT NULL
            );
            """
        )
        wkt = WKT_EPSG_3011 if epsg == EPSG_SWEREF99_1800 else "undefined"
        con.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
            (f"EPSG:{epsg}", epsg, "EPSG", epsg, wkt, "Floden bifurkationer"),
        )
        min_x = min((p.overfall_x for p in punkter), default=0.0)
        max_x = max((p.overfall_x for p in punkter), default=0.0)
        min_y = min((p.overfall_y for p in punkter), default=0.0)
        max_y = max((p.overfall_y for p in punkter), default=0.0)
        con.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, "
            "min_x, min_y, max_x, max_y, srs_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bifurkationer",
                "features",
                "bifurkationer",
                "Topologiskt instabila överfallspunkter att mäta in i fält",
                min_x,
                min_y,
                max_x,
                max_y,
                epsg,
            ),
        )
        con.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?, ?, ?, ?, ?, ?)",
            ("bifurkationer", "geom", "POINT", epsg, 0, 0),
        )
        for p in punkter:
            con.execute(
                "INSERT INTO bifurkationer (geom, nod_id, primar_ut_till_id, "
                "alternativ_ut_till_id, overfall_hojd_m, alternativ_hojd_m, marginal_m, "
                "osakerhet_komb_m, sakerhetsmarginal, instabil, paverkad_avrinningsyta_m2, "
                "paverkad_volym_m3) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _koda_gpkg_punkt(p.overfall_x, p.overfall_y, epsg),
                    int(p.nod_id),
                    int(p.primar_ut_till_id) if p.primar_ut_till_id is not None else None,
                    int(p.alternativ_ut_till_id) if p.alternativ_ut_till_id is not None else None,
                    float(p.overfall_hojd_m),
                    float(p.alternativ_hojd_m),
                    float(p.marginal_m),
                    float(p.osakerhet_komb_m),
                    float(p.sakerhetsmarginal) if math.isfinite(p.sakerhetsmarginal) else None,
                    1 if p.instabil else 0,
                    float(p.paverkad_avrinningsyta_m2),
                    float(p.paverkad_volym_m3),
                ),
            )
        con.commit()
    finally:
        con.close()
    return mal


def exportera_bifiltrering_json(
    graf: SankGraf,
    utfil: str | Path,
    *,
    osakerhet_m: NDArray[np.float32] | NDArray[np.float64] | float | None = None,
) -> Path:
    """Exporterar ett rent, GIS-fritt dataset för bigraderade k[x, y]-experiment (Q2).

    Innehåller noder (bottenhöjd, överfallshöjd, persistens, osäkerhet, avrinningsyta,
    hierarki) och alla 4-grannsadelkanter mellan lövnoder (och mot HAV = -1).
    """
    sigma = _bygg_osakerhetsraster(graf, osakerhet_m)
    sadelkanter = _samla_lov_sadelkanter(graf, sigma)

    noder_ut = [
        {
            "nod_id": int(nod.nod_id),
            "ar_lov": bool(nod.ar_lov),
            "ar_slutsanka": bool(nod.ar_slutsanka),
            "forald_id": int(nod.forald_id) if nod.forald_id is not None else None,
            "barn_id": [int(b) for b in nod.barn_id],
            "ut_till_id": int(nod.ut_till_id) if nod.ut_till_id is not None else None,
            "botten_hojd_m": float(nod.botten_rh2000),
            "overfall_hojd_m": None if nod.ar_slutsanka else float(nod.overfall_rh2000),
            "persistens_m": float(nod.persistens_m),
            "osakerhet_m": float(nod.osakerhet_m),
            "avrinningsyta_m2": float(nod.avrinningsyta_m2),
            "max_volym_m3": float(nod.max_volym_m3),
        }
        for _, nod in sorted(graf.noder.items())
    ]
    kanter_ut = [
        {
            "fran_nod": int(k.a),
            "till_nod": int(k.b),
            "sadel_hojd_m": float(k.hojd_m),
            "sadel_osakerhet_m": float(k.osakerhet_m),
            "x": float(k.x),
            "y": float(k.y),
        }
        for k in sadelkanter
    ]
    data = {
        "format": "floden-bifiltrering/v1",
        "epsg": int(graf.geo.epsg),
        "hav_nod_id": int(HAV_NOD_ID),
        "antal_noder": len(noder_ut),
        "antal_sadelkanter": len(kanter_ut),
        "noder": noder_ut,
        "sadelkanter": kanter_ut,
    }
    mal = Path(utfil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    mal.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return mal
