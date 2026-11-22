"""3D-vyns server: terräng, byggnader och resultat som binära buffertar, rutvis (`floden-3d/v1`).

Servern bygger allt ur kärnans läs-API (`karna.geometri`: `las_terrang`, `las_byggnader`) och ur
körningarnas resultatfiler. Klienten (Three.js) ritar bara; den räknar inget som visas som
resultat. Inget här är ett nytt mått: soltimmar, djup och VSC är de värden lösarna skrev.

**Rutnät och detaljnivå (LOD).** Världen delas i fasta rutor som ligger på hela multipler av
rutstorleken i EPSG:3011, så att en ruta har samma identitet oavsett vem som frågar:

- `lod` 0 har cellstorlek 1 m (`CELL0_M`), `lod` n har `1 · 2^n` m.
- En ruta är alltid 128 × 128 celler (`CELLER`), alltså 128 m vid lod 0, 256 m vid lod 1 osv.
  `(lod, tx, ty)` pekar ut rutan: väst-syd-hörnet är `(tx, ty) · 128 · cell`.
- Terräng: 129 × 129 höjdpunkter per ruta (kanten delas med grannen, så ingen springa), uint16
  kvantiserade över projektets höjdspann (`z_min`..`z_max` i `info`): med 20 m spann blir steget
  0,3 mm. Rad 0 är norr.
- Resultat på mark: 128 × 128 cellvärden, uint16 kvantiserade över lagrets värdespann, 65535 =
  inget värde (tak eller utanför). Vid lod 0 och 1 m-celler är värdet exakt rastercellens.
- Byggnader och fasadpunkter är inte rutvisa: de är några tusen rader per kvarter och hämtas
  en gång per scenario (se `docs/WEBB3D.md` för hur det delas i större områden).

Alla rutor cachas på disk (`data/cache/webb/vy3d`, nyckel = rutornas innehåll och rutindex) och i
minnet. En ruta räknas aldrig om om inget i indata ändrats.
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from karna.geometri import GeometriFel, las_byggnader, las_terrang
from karna.rutscenario import Ruta
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst
from webb.kartverktyg import KartverktygTjanst
from webb.vy3d_format import FORMAT, kapsla

CELL0_M = 1.0
CELLER = 128
KVANT = 65534
INGET_VARDE = 65535
MAX_UNDERPROV = 8
MAX_LOD_TAK = 12
_MINNES_RUTOR = 384
_RASTER_MINNE = 24
STATUS = {"oforandrad": 0, "ny": 1, "andrad": 2, "bort": 3}
_TID_ROLL = re.compile(r"^djup_t(\d{1,5})$")


def _fel(text: str, status: int = 404) -> FlodenWebbFel:
    return FlodenWebbFel(text, status_kod=status)


# ---------------------------------------------------------------------------------------------
# Rutnätet (rena funktioner)


def cellstorlek(lod: int) -> float:
    if not 0 <= lod <= MAX_LOD_TAK:
        raise _fel(f"Okänd detaljnivå {lod} (0..{MAX_LOD_TAK}).", 400)
    return float(CELL0_M * 2**lod)


def rutstorlek_m(lod: int) -> float:
    return cellstorlek(lod) * CELLER


def ruta_hornet(lod: int, tx: int, ty: int) -> tuple[float, float, float]:
    """(x0, y0, cell): väst-syd-hörnet och cellstorleken för en ruta."""
    c = cellstorlek(lod)
    return tx * c * CELLER, ty * c * CELLER, c


def rutor_i(utbredning: tuple[float, float, float, float], lod: int) -> list[tuple[int, int]]:
    """Rutindex (tx, ty) på nivån `lod` för rutorna som berör (x_min, y_min, x_max, y_max)."""
    s = rutstorlek_m(lod)
    x0, y0, x1, y1 = utbredning
    tx0, tx1 = math.floor(x0 / s), math.floor((x1 - 1e-9) / s)
    ty0, ty1 = math.floor(y0 / s), math.floor((y1 - 1e-9) / s)
    return [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]


def max_lod(utbredning: tuple[float, float, float, float]) -> int:
    """Lägsta nivå där hela utbredningen ryms i högst 2 × 2 rutor."""
    sida = max(utbredning[2] - utbredning[0], utbredning[3] - utbredning[1])
    return max(0, min(MAX_LOD_TAK, math.ceil(math.log2(max(sida, 1.0) / (CELLER * CELL0_M)))))


def kvantisera(
    v: NDArray[np.floating[Any]], lo: float, hi: float
) -> NDArray[np.uint16]:
    """Värden -> uint16 över [lo, hi]; NaN blir `INGET_VARDE`."""
    span = hi - lo if hi > lo else 1.0
    ok = np.isfinite(v)
    q = np.full(v.shape, INGET_VARDE, dtype=np.uint16)
    q[ok] = np.clip(np.rint((v[ok] - lo) / span * KVANT), 0, KVANT).astype(np.uint16)
    return q


def avkvantisera(q: NDArray[np.uint16], lo: float, hi: float) -> NDArray[np.float64]:
    span = hi - lo if hi > lo else 1.0
    v = lo + q.astype(np.float64) / KVANT * span
    v[q == INGET_VARDE] = np.nan
    return v


def nedsampla_medel(arr: NDArray[np.float32], f: int) -> NDArray[np.float32]:
    """Blockmedel över f × f celler (NaN räknas inte); kanten fylls ut med NaN."""
    if f <= 1:
        return arr
    h, b = arr.shape
    hh, bb = -(-h // f) * f, -(-b // f) * f
    p = np.full((hh, bb), np.nan, dtype=np.float32)
    p[:h, :b] = arr
    blk = p.reshape(hh // f, f, bb // f, f)
    ok = np.isfinite(blk)
    summa = np.where(ok, blk, 0.0).sum(axis=(1, 3), dtype=np.float64)
    antal = ok.sum(axis=(1, 3))
    ut = np.full(summa.shape, np.nan, dtype=np.float32)
    mask = antal > 0
    ut[mask] = (summa[mask] / antal[mask]).astype(np.float32)
    return ut


def bilinjar(
    arr: NDArray[np.float32],
    x_min: float,
    y_max: float,
    px: float,
    e: NDArray[np.float64],
    n: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Bilinjär provtagning av ett raster (cellvärden i cellmitt, rad 0 = norr) i punkter (e, n).

    Punkter utanför rastrets cellmittsområde ger NaN; kantcellerna klämmer inte fram något."""
    fx = (e - x_min) / px - 0.5
    fy = (y_max - n) / px - 0.5
    h, b = arr.shape
    inom = (fx >= -0.5) & (fx <= b - 0.5) & (fy >= -0.5) & (fy <= h - 0.5)
    fx = np.clip(fx, 0, b - 1)
    fy = np.clip(fy, 0, h - 1)
    i0 = np.minimum(np.floor(fx).astype(int), max(b - 2, 0))
    j0 = np.minimum(np.floor(fy).astype(int), max(h - 2, 0))
    i1 = np.minimum(i0 + 1, b - 1)
    j1 = np.minimum(j0 + 1, h - 1)
    tx = fx - i0
    ty = fy - j0
    v00, v10 = arr[j0, i0], arr[j0, i1]
    v01, v11 = arr[j1, i0], arr[j1, i1]
    v = (v00 * (1 - tx) + v10 * tx) * (1 - ty) + (v01 * (1 - tx) + v11 * tx) * ty
    ut = np.asarray(v, dtype=np.float64)
    ut[~inom] = np.nan
    return ut


def sampla_celler(
    arr: NDArray[np.float32],
    x_min: float,
    y_max: float,
    px: float,
    x0: float,
    y0: float,
    cell: float,
    n: int = CELLER,
) -> NDArray[np.float32]:
    """Cellvärden för en ruta: medel av underprov i varje cell (NaN räknas inte).

    Vid cell = pixelstorlek är det exakt rastercellens värde. Fler än `MAX_UNDERPROV` underprov
    per axel tas inte (översiktsnivåerna är grova ändå)."""
    s = max(1, min(MAX_UNDERPROV, round(cell / px)))
    off = (np.arange(s) + 0.5) / s * cell
    e = (x0 + np.arange(n)[:, None] * cell + off[None, :]).ravel()
    nn = (y0 + (n - 1 - np.arange(n))[:, None] * cell + off[None, :]).ravel()
    kol = np.floor((e - x_min) / px).astype(np.int64)
    rad = np.floor((y_max - nn) / px).astype(np.int64)
    h, b = arr.shape
    kok = (kol >= 0) & (kol < b)
    rok = (rad >= 0) & (rad < h)
    kc = np.clip(kol, 0, b - 1)
    rc = np.clip(rad, 0, h - 1)
    v = np.asarray(arr[rc[:, None], kc[None, :]], dtype=np.float32)
    v = np.where(rok[:, None] & kok[None, :], v, np.float32(np.nan))
    blk = v.reshape(n, s, n, s)
    ok = np.isfinite(blk)
    summa = np.where(ok, blk, 0.0).sum(axis=(1, 3), dtype=np.float64)
    antal = ok.sum(axis=(1, 3))
    ut = np.full((n, n), np.nan, dtype=np.float32)
    m = antal > 0
    ut[m] = (summa[m] / antal[m]).astype(np.float32)
    return ut


# ---------------------------------------------------------------------------------------------
# Terrängkälla över K1-rutor


_Kalla = tuple[NDArray[np.float32], float, float, float]


class _Terrang:
    """Terrängen ur en uppsättning K1 v2-rutor (`terrang.tif`), med nedsampling per detaljnivå."""

    def __init__(self, rutor: Sequence[Ruta]) -> None:
        self.rutor = list(rutor)
        self._lock = threading.RLock()
        self._raster: OrderedDict[tuple[str, int], _Kalla] = OrderedDict()
        self.utbredning = (
            min(r.x_min for r in self.rutor),
            min(r.y_min for r in self.rutor),
            max(r.x_max for r in self.rutor),
            max(r.y_max for r in self.rutor),
        )
        self._z: tuple[float, float] | None = None

    def nyckel(self) -> str:
        h = hashlib.sha256()
        for r in sorted(self.rutor, key=lambda r: r.ruta_id):
            h.update(r.ruta_id.encode())
            h.update(sha256_fil(Path(r.mapp) / "recept.json").encode())
        return h.hexdigest()

    def _las(self, r: Ruta, faktor: int) -> _Kalla:
        nyckel = (str(r.mapp), faktor)
        with self._lock:
            if nyckel in self._raster:
                self._raster.move_to_end(nyckel)
                return self._raster[nyckel]
        try:
            arr, geo = las_terrang(r.mapp)
        except GeometriFel as exc:
            raise _fel(f"Terrängen kan inte läsas: {exc}", 422) from exc
        ned = nedsampla_medel(np.asarray(arr, dtype=np.float32), faktor)
        post = (ned, float(geo.x_min), float(geo.y_max), float(geo.pixelstorlek_x * faktor))
        with self._lock:
            self._raster[nyckel] = post
            while len(self._raster) > _RASTER_MINNE:
                self._raster.popitem(last=False)
        return post

    def hojdspann(self) -> tuple[float, float]:
        with self._lock:
            if self._z is None:
                lo, hi = math.inf, -math.inf
                for r in self.rutor:
                    arr, _, _, _ = self._las(r, 1)
                    if np.isfinite(arr).any():
                        lo = min(lo, float(np.nanmin(arr)))
                        hi = max(hi, float(np.nanmax(arr)))
                if not math.isfinite(lo):
                    raise _fel("Terrängen saknar höjdvärden.", 422)
                self._z = (lo, hi)
            return self._z

    def punkter(
        self, e: NDArray[np.float64], n: NDArray[np.float64], cell: float
    ) -> NDArray[np.float64]:
        """Terränghöjd (RH 2000) i punkterna; NaN där ingen ruta täcker."""
        ut = np.full(e.shape, np.nan)
        for r in self.rutor:
            m = (
                np.isnan(ut)
                & (e >= r.x_min - 1e-6)
                & (e <= r.x_max + 1e-6)
                & (n >= r.y_min - 1e-6)
                & (n <= r.y_max + 1e-6)
            )
            if not m.any():
                continue
            # Källrastret nedsamplas till ungefär rutans cellstorlek, så att en grov nivå inte
            # fastnar i enskilda celler.
            _, _, _, px = self._las(r, 1)
            faktor = int(max(1, math.floor(cell / px))) if cell > px * 1.5 else 1
            arr, xm, ym, pxf = self._las(r, faktor)
            v = bilinjar(arr, xm, ym, pxf, e[m], n[m])
            ut[m] = v
        return ut

    def ruta(self, lod: int, tx: int, ty: int) -> NDArray[np.float64]:
        """129 × 129 höjder (rad 0 = norr)."""
        x0, y0, c = ruta_hornet(lod, tx, ty)
        i = np.arange(CELLER + 1)
        e = np.broadcast_to(x0 + i[None, :] * c, (CELLER + 1, CELLER + 1))
        n = np.broadcast_to((y0 + (CELLER - i) * c)[:, None], (CELLER + 1, CELLER + 1))
        return self.punkter(np.array(e), np.array(n), c)


# ---------------------------------------------------------------------------------------------
# Tjänsten


class Vy3dTjanst:
    """Webbens 3D-endpoints (`/api/3d/...`)."""

    def __init__(
        self, fragor: FragaTjanst, kartverktyg: KartverktygTjanst, cachemapp: Path | None = None
    ) -> None:
        self.fragor = fragor
        self.kartverktyg = kartverktyg
        self.cachemapp = cachemapp or (fragor.korningsmapp.parent / "vy3d")
        self._lock = threading.RLock()
        self._terrang: _Terrang | None = None
        self._minne: OrderedDict[str, bytes] = OrderedDict()
        self._spann: dict[str, tuple[float, float]] = {}
        self._beraknade = 0
        self._traffar = 0

    # -- cache ----------------------------------------------------------------------------

    def statistik(self) -> dict[str, int]:
        """Antal rutor som räknats och som hämtats ur cachen sedan start (för test och mätning)."""
        return {"beraknade": self._beraknade, "ur_cache": self._traffar}

    def _cachad(self, nyckel: str, bygg: Callable[[], bytes]) -> bytes:
        h = hashlib.sha256(nyckel.encode()).hexdigest()
        with self._lock:
            if h in self._minne:
                self._minne.move_to_end(h)
                self._traffar += 1
                return self._minne[h]
        fil = self.cachemapp / h[:2] / f"{h}.fl3d"
        data: bytes | None = None
        if fil.is_file():
            try:
                data = fil.read_bytes()
            except OSError:
                data = None
        if data is not None:
            with self._lock:
                self._traffar += 1
        else:
            data = bygg()
            with self._lock:
                self._beraknade += 1
            try:
                fil.parent.mkdir(parents=True, exist_ok=True)
                tmp = fil.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(fil)
            except OSError:
                pass  # cachen är en optimering
        with self._lock:
            self._minne[h] = data
            while len(self._minne) > _MINNES_RUTOR:
                self._minne.popitem(last=False)
        return data

    # -- världen --------------------------------------------------------------------------

    def _bas_rutor(self, projekt: str) -> list[Ruta]:
        mallar = self.kartverktyg._kravs_projekt(projekt)  # noqa: SLF001 - samma paket
        # Den största basen (dagsljus: hela kvarteret) bär terrängen.
        bas = max(mallar.values(), key=lambda m: len(m.rutor))
        return list(bas.rutor)

    def terrang(self, projekt: str) -> _Terrang:
        with self._lock:
            if self._terrang is None:
                self._terrang = _Terrang(self._bas_rutor(projekt))
            return self._terrang

    def _origo(self, t: _Terrang) -> tuple[float, float, float]:
        u = t.utbredning
        z0 = math.floor(t.hojdspann()[0])
        return float(round((u[0] + u[2]) / 2)), float(round((u[1] + u[3]) / 2)), float(z0)

    def info(self, projekt: str) -> dict[str, Any]:
        t = self.terrang(projekt)
        u = t.utbredning
        zlo, zhi = t.hojdspann()
        e0, n0, z0 = self._origo(t)
        nivaer = []
        for lod in range(max_lod(u) + 1):
            nivaer.append(
                {
                    "lod": lod,
                    "cell_m": cellstorlek(lod),
                    "rutstorlek_m": rutstorlek_m(lod),
                    "rutor": len(rutor_i(u, lod)),
                }
            )
        return {
            "format": FORMAT,
            "projekt": projekt,
            "epsg": 3011,
            "nyckel": t.nyckel(),
            "utbredning": {"x_min": u[0], "y_min": u[1], "x_max": u[2], "y_max": u[3]},
            "origo": {"e": e0, "n": n0, "z": z0},
            "rutnat": {"celler": CELLER, "cell0_m": CELL0_M, "nivaer": nivaer},
            "hojd": {
                "z_min": zlo,
                "z_max": zhi,
                "kvant_lo": math.floor(zlo) - 1,
                "kvant_hi": math.ceil(zhi) + 1,
            },
        }

    # -- terräng --------------------------------------------------------------------------

    def terrang_ruta(self, projekt: str, lod: int, tx: int, ty: int) -> bytes:
        t = self.terrang(projekt)
        zlo, zhi = t.hojdspann()
        lo, hi = float(math.floor(zlo) - 1), float(math.ceil(zhi) + 1)
        u = t.utbredning
        x0, y0, c = ruta_hornet(lod, tx, ty)
        s = rutstorlek_m(lod)
        if x0 + s <= u[0] or x0 >= u[2] or y0 + s <= u[1] or y0 >= u[3]:
            raise _fel("Rutan ligger utanför projektet.")

        def bygg() -> bytes:
            z = t.ruta(lod, tx, ty)
            q = kvantisera(z, lo, hi)
            return kapsla(
                "terrang",
                {
                    "lod": lod,
                    "tx": tx,
                    "ty": ty,
                    "x0": x0,
                    "y0": y0,
                    "cell_m": c,
                    "celler": CELLER,
                    "z_lo": lo,
                    "z_hi": hi,
                    "kvant": KVANT,
                    "inget": INGET_VARDE,
                },
                [("z", q)],
            )

        return self._cachad(f"terrang|{t.nyckel()}|{lod}|{tx}|{ty}", bygg)

    # -- körningar och scenarier ------------------------------------------------------------

    def _korning(self, korning_id: str) -> tuple[Any, dict[str, Any]]:
        k = self.fragor._hamta(korning_id)  # noqa: SLF001 - samma paket
        return k, self.fragor._sammanstallning(k)  # noqa: SLF001

    def _scenariomappar(
        self, projekt: str, korning_id: str | None, scenario: str | None
    ) -> tuple[list[Path], str, str | None]:
        """(rutmappar, scenario-id, bas-id). Utan körning: projektets bas."""
        if korning_id is None:
            return [Path(r.mapp) for r in self._bas_rutor(projekt)], "bas", None
        k, s = self._korning(korning_id)
        ordning = list(s["scenarier"])
        sid = scenario or ordning[0]
        if sid not in s["scenarier"]:
            raise _fel(f"Körningen har inget scenario '{sid}'.")
        rutor, varning = self.fragor._scenariorutor(k, s)  # noqa: SLF001
        if rutor is None:
            raise _fel(varning or "Scenariots rutor kan inte läsas.", 409)
        return [Path(p) for p in rutor[sid].values()], sid, ordning[0]

    @staticmethod
    def _register(mappar: Sequence[Path]) -> dict[str, Any]:
        ut: dict[str, Any] = {}
        for m in mappar:
            for b in las_byggnader(m):
                ut.setdefault(b.byggnad_id, b)
        return ut

    def korningsinfo(self, projekt: str, korning_id: str) -> dict[str, Any]:
        """Vad en körning har att visa i 3D: scenarier, fasad, luft, buller och tidsserier."""
        k, s = self._korning(korning_id)
        lager, _ = self.fragor._lager(k, s)  # noqa: SLF001
        fasad: dict[str, set[str]] = {}
        doman: dict[str, list[str]] = {}
        for j in s["jobb"]:
            los = str(j["losare"])
            if los == "dagsljus.vsc_fasad":
                fasad.setdefault(str(j["analys"]), set()).add(str(j["scenario"]))
            doman.setdefault(los.split(".")[0], [])
            if j["analys"] not in doman[los.split(".")[0]]:
                doman[los.split(".")[0]].append(str(j["analys"]))
        tid: dict[str, list[dict[str, Any]]] = {}
        for la in lager:
            m = _TID_ROLL.match(str(la["roll"]))
            if m and la["typ"] != "skillnad":
                tid.setdefault(f"{la['analys']}.{la['scenario']}", []).append(
                    {"lager": la["id"], "minut": int(m.group(1))}
                )
        for v in tid.values():
            v.sort(key=lambda x: x["minut"])
        ordning = list(s["scenarier"])
        return {
            "korning": korning_id,
            "projekt": projekt,
            "scenarier": ordning,
            "bas": ordning[0],
            "fasad": {a: sorted(sc) for a, sc in fasad.items()},
            "doman": doman,
            "vatten_tidsserier": tid,
        }

    # -- byggnader ------------------------------------------------------------------------

    def byggnader(
        self, projekt: str, korning_id: str | None = None, scenario: str | None = None
    ) -> bytes:
        """Byggnader för ett scenario: fotavtryck, höjder och status mot basen (ny, andrad, bort)"""
        t = self.terrang(projekt)
        mappar, sid, bas_id = self._scenariomappar(projekt, korning_id, scenario)
        bas_mappar = (
            self._scenariomappar(projekt, korning_id, bas_id)[0]
            if bas_id and bas_id != sid
            else None
        )
        # Nyckel: rutornas innehåll (scenariots rutor har egna recept-sha).
        h = hashlib.sha256()
        for m in sorted(mappar + (bas_mappar or [])):
            h.update(str(m.name).encode())
            h.update(sha256_fil(m / "recept.json").encode())
        nyckel = f"byggnader|{t.nyckel()}|{h.hexdigest()}|{sid}"

        def bygg() -> bytes:
            return self._bygg_byggnader(t, mappar, bas_mappar, sid)

        return self._cachad(nyckel, bygg)

    def _bygg_byggnader(
        self, t: _Terrang, mappar: Sequence[Path], bas_mappar: Sequence[Path] | None, sid: str
    ) -> bytes:
        reg = self._register(mappar)
        bas = self._register(bas_mappar) if bas_mappar is not None else None
        poster: list[tuple[Any, int, float | None]] = []
        for bid in sorted(reg):
            b = reg[bid]
            status = "oforandrad"
            fore_tak: float | None = None
            if bas is not None:
                if bid not in bas:
                    status = "ny"
                elif abs(bas[bid].takhojd_rh2000 - b.takhojd_rh2000) > 1e-6:
                    status = "andrad"
                    fore_tak = float(bas[bid].takhojd_rh2000)
            poster.append((b, STATUS[status], fore_tak))
        if bas is not None:
            for bid in sorted(set(bas) - set(reg)):
                poster.append((bas[bid], STATUS["bort"], None))
        e0, n0, z0 = self._origo(t)
        punkter: list[tuple[float, float]] = []
        ringstart = [0]
        byggnad_ring = [0]
        z_fot: list[float] = []
        z_tak: list[float] = []
        z_fore: list[float] = []
        status_lista: list[int] = []
        ids: list[str] = []
        hojd: list[float] = []
        for b, st, fore_tak in poster:
            ringar = [b.polygon, *b.hal]
            for ring in ringar:
                r = list(ring)
                if len(r) > 1 and r[0] == r[-1]:
                    r = r[:-1]
                punkter.extend((float(x) - e0, float(y) - n0) for x, y in r)
                ringstart.append(len(punkter))
            byggnad_ring.append(len(ringstart) - 1)
            ids.append(b.byggnad_id)
            hojd.append(float(b.hojd_m))
            z_tak.append(float(b.takhojd_rh2000) - z0)
            z_fore.append((fore_tak if fore_tak is not None else float(b.takhojd_rh2000)) - z0)
            status_lista.append(st)
            pe = np.array([p[0] + e0 for p in punkter[ringstart[byggnad_ring[-2]] :]])
            pn = np.array([p[1] + n0 for p in punkter[ringstart[byggnad_ring[-2]] :]])
            zz = t.punkter(pe, pn, 1.0)
            zmin = float(np.nanmin(zz)) if np.isfinite(zz).any() else float(b.markhojd_rh2000)
            z_fot.append(min(zmin, float(b.markhojd_rh2000)) - 0.3 - z0)
        return kapsla(
            "byggnader",
            {
                "scenario": sid,
                "origo": {"e": e0, "n": n0, "z": z0},
                "antal": len(ids),
                "ids": ids,
                "status": {v: k for k, v in STATUS.items()},
            },
            [
                ("punkter", np.array(punkter, dtype=np.float32).reshape(-1, 2)),
                ("ringstart", np.array(ringstart, dtype=np.uint32)),
                ("byggnad_ring", np.array(byggnad_ring, dtype=np.uint32)),
                ("z_fot", np.array(z_fot, dtype=np.float32)),
                ("z_tak", np.array(z_tak, dtype=np.float32)),
                ("z_tak_fore", np.array(z_fore, dtype=np.float32)),
                ("hojd_m", np.array(hojd, dtype=np.float32)),
                ("status", np.array(status_lista, dtype=np.uint8)),
            ],
        )

    # -- resultat på mark ------------------------------------------------------------------

    def _lagerpost(self, korning_id: str, lager_id: str) -> tuple[Any, dict[str, Any]]:
        k, s = self._korning(korning_id)
        lager, _ = self.fragor._lager(k, s)  # noqa: SLF001
        la = next((x for x in lager if x["id"] == lager_id), None)
        if la is None:
            raise _fel(f"Okänt lager '{lager_id}'.")
        return k, la

    def _varden(self, k: Any, la: dict[str, Any]) -> tuple[NDArray[np.float32], Path]:
        fil = k.mapp / "webb" / f"{la['id']}.npy"
        arr = np.load(fil, mmap_mode="r")
        v = np.array(arr, dtype=np.float32) if arr.nbytes < 64e6 else arr
        takfil = k.mapp / "webb" / f"{la['id']}.tak.npy"
        if takfil.is_file():
            tak = np.load(takfil, mmap_mode="r")
            v = np.where(np.asarray(tak, dtype=bool), np.float32(np.nan), v).astype(np.float32)
        return np.asarray(v, dtype=np.float32), fil

    def varde_spann(self, korning_id: str, lager_id: str) -> tuple[float, float]:
        k, la = self._lagerpost(korning_id, lager_id)
        v, fil = self._varden(k, la)
        nyckel = f"{fil}|{fil.stat().st_mtime_ns}|{fil.stat().st_size}"
        with self._lock:
            if nyckel in self._spann:
                return self._spann[nyckel]
        ok = np.isfinite(v)
        lo, hi = (float(v[ok].min()), float(v[ok].max())) if ok.any() else (0.0, 1.0)
        with self._lock:
            self._spann[nyckel] = (lo, hi)
        return lo, hi

    def mark_ruta(
        self, projekt: str, korning_id: str, lager_id: str, lod: int, tx: int, ty: int
    ) -> bytes:
        """Cellvärden för en ruta ur ett resultatlager; tak och saknade värden blir inget värde."""
        k, la = self._lagerpost(korning_id, lager_id)
        x0, y0, c = ruta_hornet(lod, tx, ty)
        u = la["utbredning"]
        s = rutstorlek_m(lod)
        if x0 + s <= u["x_min"] or x0 >= u["x_max"] or y0 + s <= u["y_min"] or y0 >= u["y_max"]:
            raise _fel("Rutan ligger utanför lagret.")
        fil = k.mapp / "webb" / f"{la['id']}.npy"
        st = fil.stat()
        takfil = fil.with_suffix(".tak.npy")
        tst = takfil.stat().st_mtime_ns if takfil.is_file() else 0
        nyckel = f"mark|{korning_id}|{lager_id}|{st.st_mtime_ns}|{st.st_size}|{tst}|{lod}|{tx}|{ty}"
        lo, hi = self.varde_spann(korning_id, lager_id)

        def bygg() -> bytes:
            v, _ = self._varden(k, la)
            celler = sampla_celler(
                v, float(u["x_min"]), float(u["y_max"]), float(la["pixelstorlek_m"]), x0, y0, c
            )
            return kapsla(
                "mark",
                {
                    "lager": lager_id,
                    "korning": korning_id,
                    "lod": lod,
                    "tx": tx,
                    "ty": ty,
                    "x0": x0,
                    "y0": y0,
                    "cell_m": c,
                    "celler": CELLER,
                    "v_lo": lo,
                    "v_hi": hi,
                    "kvant": KVANT,
                    "inget": INGET_VARDE,
                    "enhet": la["enhet"],
                },
                [("v", kvantisera(celler, lo, hi))],
            )

        return self._cachad(nyckel, bygg)

    # -- fasadpunkter ----------------------------------------------------------------------

    def _fasad_punkter(
        self, k: Any, s: dict[str, Any], analys: str, sid: str
    ) -> dict[str, dict[str, Any]]:
        jobb = [j for j in s["jobb"] if j["analys"] == analys and j["scenario"] == sid]
        if not jobb:
            raise _fel(f"Inga fasadresultat för analys '{analys}' i scenariot '{sid}'.")
        ut: dict[str, dict[str, Any]] = {}
        for j in jobb:
            roller = self.fragor._roller(k, j)  # noqa: SLF001
            namn = roller.get("vsc")
            if namn is None:
                continue
            with (self.fragor._jobbmapp(k, j) / namn).open(encoding="utf-8", newline="") as f:  # noqa: SLF001
                for r in csv.DictReader(f):
                    ut.setdefault(r["fasad_id"], r)
        if not ut:
            raise _fel(f"Analysen '{analys}' har inga fasadpunkter.")
        return ut

    def fasad(
        self,
        projekt: str,
        korning_id: str,
        scenario: str,
        analys: str,
        jamfor: str | None = None,
    ) -> bytes:
        """VSC (procent) i K11-fasadpunkterna; med `jamfor` också skillnaden mot det scenariot."""
        t = self.terrang(projekt)
        k, s = self._korning(korning_id)
        if scenario not in s["scenarier"]:
            raise _fel(f"Körningen har inget scenario '{scenario}'.")
        e0, n0, z0 = self._origo(t)
        nyckel = f"fasad|{t.nyckel()}|{korning_id}|{scenario}|{analys}|{jamfor}|{k.slut}"

        def bygg() -> bytes:
            efter = self._fasad_punkter(k, s, analys, scenario)
            fore = self._fasad_punkter(k, s, analys, jamfor) if jamfor else {}
            ids = sorted(efter)
            bygg_ids = sorted({efter[i]["byggnad_id"] for i in ids})
            bidx = {b: n for n, b in enumerate(bygg_ids)}
            n = len(ids)
            xyz = np.zeros((n, 3), dtype=np.float32)
            az = np.zeros(n, dtype=np.float32)
            vsc = np.full(n, np.nan, dtype=np.float32)
            skillnad = np.full(n, np.nan, dtype=np.float32)
            fore_v = np.full(n, np.nan, dtype=np.float32)
            bi = np.zeros(n, dtype=np.uint32)
            vaning = np.zeros(n, dtype=np.uint16)
            for i, fid in enumerate(ids):
                r = efter[fid]
                xyz[i] = (float(r["x_m"]) - e0, float(r["y_m"]) - n0, float(r["z_m"]) - z0)
                az[i] = float(r["normal_azimut_grader"])
                ok = r.get("status", "ok") == "ok"
                vsc[i] = float(r["vsc_procent"]) if ok else np.nan
                bi[i] = bidx[r["byggnad_id"]]
                vaning[i] = int(fid.rsplit(":", 1)[-1])
                if jamfor and fid in fore and fore[fid].get("status", "ok") == "ok":
                    fore_v[i] = float(fore[fid]["vsc_procent"])
                    if ok:
                        skillnad[i] = vsc[i] - fore_v[i]
            delar: list[tuple[str, NDArray[Any]]] = [
                ("xyz", xyz),
                ("azimut", az),
                ("vsc", vsc),
                ("byggnad", bi),
                ("vaning", vaning),
            ]
            if jamfor:
                delar += [("vsc_fore", fore_v), ("skillnad", skillnad)]
            return kapsla(
                "fasad",
                {
                    "korning": korning_id,
                    "scenario": scenario,
                    "jamfor": jamfor,
                    "analys": analys,
                    "origo": {"e": e0, "n": n0, "z": z0},
                    "antal": n,
                    "byggnad_ids": bygg_ids,
                    "enhet": "%",
                },
                delar,
            )

        return self._cachad(nyckel, bygg)
