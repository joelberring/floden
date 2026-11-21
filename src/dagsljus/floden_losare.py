"""Dagsljuslösare enligt K21 (`docs/ANSLUTA_LOSARE.md`): upptäcks automatiskt av kärnan.

- `dagsljus.horisont`: horisontprofiler per cell (uint16, 0,01 grader) för den egna rutan.
- `dagsljus.soltimmar_mark`: soltimmar per dygn (medel över valda datum) för markceller.
- `dagsljus.svf_mark`: himmelsexponering (SVF) för horisontella ytor.
- `dagsljus.vsc_fasad`: VSC (BRE, mulen CIE-himmel) för fasadpunkter ur en fil.

Alla är rutvisa med påverkansradien H_max / tan(h_min) (konservativ). Strålar går högst så
långt, så hinder som skymmer mindre än h_min bortom radien ingår inte; se `docs/DAGSLJUS.md`.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray

from dagsljus.horisont import (
    horisont_mipmap,
    horisont_riktning_ruta,
    riktningar_rad,
)
from dagsljus.matt import SoltimmarAckumulator, halvplansriktningar, vsc_fran_halvplansprofil
from dagsljus.ruta import Mosaik, RutaFel, bygg_mosaik, fonster_koordinater, mitt_3011
from dagsljus.solposition import dygnsfonster_utc, lat_lon_fran_3011, solposition
from karna.analyslosare import Analyslosare, LosarFel, LosarKontext, LosarUtdata
from karna.paverkan import dagsljus_paverkansradie_m

_GEMENSAMMA = {"azimutriktningar", "hojd_max_m", "h_min_grader", "cellstorlek_m", "omsampling"}
_OMSAMPLING = ("medel", "max", "punkt")
_VSC_CHUNK_STRALAR = 200_000


def _tal(p: Mapping[str, Any], namn: str, standard: float, *, over: float | None = None) -> float:
    v = p.get(namn, standard)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise LosarFel(f"Parametern '{namn}' måste vara ett ändligt tal, fick {v!r}.")
    if over is not None and float(v) <= over:
        raise LosarFel(f"Parametern '{namn}' måste vara > {over}, fick {v}.")
    return float(v)


def _heltal(p: Mapping[str, Any], namn: str, standard: int, *, minst: int) -> int:
    v = p.get(namn, standard)
    if isinstance(v, bool) or not isinstance(v, int | float) or float(v) != int(v):
        raise LosarFel(f"Parametern '{namn}' måste vara ett heltal, fick {v!r}.")
    if int(v) < minst:
        raise LosarFel(f"Parametern '{namn}' måste vara >= {minst}, fick {v}.")
    return int(v)


def _gemensamma(p: Mapping[str, Any]) -> dict[str, Any]:
    h_min = _tal(p, "h_min_grader", 5.0, over=0.0)
    if h_min >= 90.0:
        raise LosarFel(f"h_min_grader måste vara < 90, fick {h_min}.")
    oms = p.get("omsampling", "medel")
    if oms not in _OMSAMPLING:
        raise LosarFel(f"omsampling måste vara en av {_OMSAMPLING}, fick {oms!r}.")
    return {
        "azimutriktningar": _heltal(p, "azimutriktningar", 64, minst=8),
        "hojd_max_m": _tal(p, "hojd_max_m", 60.0, over=0.0),
        "h_min_grader": h_min,
        "cellstorlek_m": _tal(p, "cellstorlek_m", 1.0, over=0.0),
        "omsampling": oms,
    }


def _kontrollera_okanda(p: Mapping[str, Any], tillatna: set[str], namn: str) -> None:
    okanda = sorted(set(p) - tillatna)
    if okanda:
        raise LosarFel(f"{namn}: okända parametrar {okanda}. Tillåtna: {sorted(tillatna)}.")


def _radie_m(p: Mapping[str, Any]) -> float:
    return dagsljus_paverkansradie_m(float(p["hojd_max_m"]), float(p["h_min_grader"]))


def _mosaik(k: LosarKontext) -> Mosaik:
    if k.ruta is None:
        raise LosarFel("Dagsljuslösare körs rutvis men fick ingen ruta.")
    try:
        return bygg_mosaik(
            k.ruta.mapp,
            tuple(g.mapp for g in k.grannar),
            float(k.parametrar["cellstorlek_m"]),
            k.parametrar["omsampling"],
        )
    except RutaFel as exc:
        raise LosarFel(str(exc)) from exc


def _gemensamma_varningar(k: LosarKontext, m: Mosaik) -> list[str]:
    v: list[str] = []
    if m.saknade_celler:
        v.append(
            f"{m.saknade_celler} celler i mosaikens utsträckning saknar höjddata (rutor som inte "
            "finns i scenariot); de antas inte skymma, så horisonter mot dem kan vara för låga"
        )
    spann = m.hojdspann_m
    if spann > float(k.parametrar["hojd_max_m"]):
        v.append(
            f"höjdskillnaden {spann:.1f} m i data överstiger hojd_max_m="
            f"{k.parametrar['hojd_max_m']} m: hinder högre än så kan skymma över h_min även "
            "bortom påverkansradien, och ingår då inte"
        )
    return v


def _horisontpass(
    k: LosarKontext,
    m: Mosaik,
    mottagare: list[Callable[[int, float, NDArray[np.float32]], None]],
) -> NDArray[np.int32]:
    """Beräknar horisonten för det egna fönstret riktning för riktning. Returnerar antal
    riktningar per cell vars stråle lämnade höjdfältet före max avstånd."""
    antal = int(k.parametrar["azimutriktningar"])
    radie = _radie_m(k.parametrar)
    r0, r1, c0, c1 = m.fonster
    trunk = np.zeros((r1 - r0, c1 - c0), dtype=np.int32)
    for i, phi in enumerate(riktningar_rad(antal)):
        h, tr = horisont_riktning_ruta(m.falt, float(phi), radie, fonster=m.fonster)
        trunk += tr
        for f in mottagare:
            f(i, float(phi), h)
    return trunk


def _skriv_raster(sokvag: Path, a: NDArray[Any], m: Mosaik) -> None:
    from testsvit.k1_io import skriv_geotiff

    x_min, y_max = fonster_koordinater(m)
    skriv_geotiff(
        sokvag,
        np.asarray(a, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=m.falt.cell_m,
    )


def _trunk_varning(trunk: NDArray[np.int32], antal: int) -> list[str]:
    n = int(np.count_nonzero(trunk))
    if not n:
        return []
    return [
        f"{n} av {trunk.size} celler har strålar som lämnar höjdfältet före påverkansradien "
        f"(rutan ligger vid kanten av scenariots data); se trunkering.tif (andel av {antal} "
        "riktningar)"
    ]


class _DagsljusLosare(Analyslosare):
    doman: ClassVar[str] = "dagsljus"
    version: ClassVar[str] = "1"
    _egna: ClassVar[set[str]] = set()

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        _kontrollera_okanda(parametrar, _GEMENSAMMA | self._egna, self.namn)
        return _gemensamma(parametrar)

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        return _radie_m(parametrar)


class Horisont(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.horisont"
    beskrivning: ClassVar[str] = (
        "Horisontprofil h(phi) i K azimutriktningar per cell (DSM = mark + byggnader)."
    )
    utdataroller: ClassVar[tuple[str, ...]] = ("horisont", "horisont_meta", "trunkering")

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        lagr = np.empty((antal, r1 - r0, c1 - c0), dtype=np.uint16)

        def lagra(i: int, _phi: float, h: NDArray[np.float32]) -> None:
            lagr[i] = np.rint(np.degrees(h.astype(np.float64)) * 100.0).astype(np.uint16)

        trunk = _horisontpass(kontext, m, [lagra])
        np.save(kontext.utmapp / "horisont.npy", lagr)
        x_min, y_max = fonster_koordinater(m)
        (kontext.utmapp / "horisont.json").write_text(
            json.dumps(
                {
                    "kontrakt": "dagsljus-horisont/v1",
                    "enhet": "0,01 grader (uint16)",
                    "form": ["riktning", "rad", "kolumn"],
                    "azimutriktningar": antal,
                    "azimut": "phi_k = 2 pi k / K, norr = 0, medurs",
                    "x_min_m": x_min,
                    "y_max_m": y_max,
                    "cellstorlek_m": m.falt.cell_m,
                    "epsg": 3011,
                    "max_avstand_m": _radie_m(kontext.parametrar),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        return LosarUtdata(
            filer={
                "horisont": "horisont.npy",
                "horisont_meta": "horisont.json",
                "trunkering": "trunkering.tif",
            },
            matvarden={
                "horisont_max_grader": float(lagr.max()) / 100.0,
                "celler": int(trunk.size),
                "azimutriktningar": antal,
            },
            varningar=tuple(_gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal)),
        )


class SvfMark(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.svf_mark"
    beskrivning: ClassVar[str] = "Himmelsexponering (SVF) för horisontella markytor och tak."
    utdataroller: ClassVar[tuple[str, ...]] = ("svf", "trunkering")

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        summa = np.zeros((r1 - r0, c1 - c0))

        def ackumulera(_i: int, _phi: float, h: NDArray[np.float32]) -> None:
            summa.__iadd__(np.sin(h.astype(np.float64)) ** 2)

        trunk = _horisontpass(kontext, m, [ackumulera])
        svf = 1.0 - summa / antal
        _skriv_raster(kontext.utmapp / "svf.tif", svf, m)
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        return LosarUtdata(
            filer={"svf": "svf.tif", "trunkering": "trunkering.tif"},
            matvarden={
                "svf_medel": float(svf.mean()),
                "svf_min": float(svf.min()),
                "celler": int(svf.size),
            },
            varningar=tuple(_gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal)),
        )


class SoltimmarMark(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.soltimmar_mark"
    beskrivning: ClassVar[str] = "Soltimmar per dygn (medel över valda datum) för markceller."
    utdataroller: ClassVar[tuple[str, ...]] = ("soltimmar", "trunkering")
    _egna: ClassVar[set[str]] = {"datum", "steg_min", "refraktion"}

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        datum = parametrar.get("datum", ["2026-03-20"])
        if (
            not isinstance(datum, list | tuple)
            or not datum
            or not all(isinstance(d, str) for d in datum)
        ):
            raise LosarFel(f"datum måste vara en icke-tom lista av 'ÅÅÅÅ-MM-DD', fick {datum!r}.")
        steg = _tal(parametrar, "steg_min", 10.0, over=0.0)
        if abs(1440.0 / steg - round(1440.0 / steg)) > 1e-9:
            raise LosarFel(f"1440 måste vara delbart med steg_min, fick {steg}.")
        refr = parametrar.get("refraktion", False)
        if not isinstance(refr, bool):
            raise LosarFel(f"refraktion måste vara true/false, fick {refr!r}.")
        ut.update({"datum": sorted(str(d) for d in datum), "steg_min": steg, "refraktion": refr})
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from dagsljus.solposition import SolFel

        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        x_min, y_max = fonster_koordinater(m)
        e, n = mitt_3011((x_min, y_max), (r1 - r0, c1 - c0), m.falt.cell_m)
        lat, lon = lat_lon_fran_3011(e, n)
        datum = list(kontext.parametrar["datum"])
        steg_min = float(kontext.parametrar["steg_min"])
        try:
            tider = np.concatenate([dygnsfonster_utc(d, lon, steg_min) for d in datum])
            sol_h, sol_az = solposition(
                tider, lat, lon, refraktion=bool(kontext.parametrar["refraktion"])
            )
        except SolFel as exc:
            raise LosarFel(str(exc)) from exc
        ack = SoltimmarAckumulator(
            antal, sol_h, sol_az, steg_min / 60.0 / len(datum), (r1 - r0, c1 - c0)
        )
        trunk = _horisontpass(kontext, m, [lambda i, _p, h: ack.mata(i, h)])
        timmar = ack.timmar()
        _skriv_raster(kontext.utmapp / "soltimmar.tif", timmar, m)
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        var = _gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal)
        if antal < 128:
            var.append(
                f"azimutriktningar={antal}: soltimmar nära skuggkanter kan avvika flera tiondels "
                "timmar (uppmätt på syntetiskt block, docs/DAGSLJUS.md); använd >= 180 för "
                "skuggstudier"
            )
        return LosarUtdata(
            filer={"soltimmar": "soltimmar.tif", "trunkering": "trunkering.tif"},
            matvarden={
                "soltimmar_medel_h": float(timmar.mean()),
                "soltimmar_min_h": float(timmar.min()),
                "soltimmar_max_h": float(timmar.max()),
                "datum": len(datum),
                "lat_grader": lat,
                "lon_grader": lon,
            },
            varningar=tuple(var),
        )


_FASAD_KOLUMNER = ("fasad_id", "x_m", "y_m", "z_m", "normal_azimut_grader")


def las_fasadpunkter(sokvag: Path) -> list[dict[str, Any]]:
    """Läser fasadpunkter (CSV; kolumner `fasad_id,x_m,y_m,z_m,normal_azimut_grader`).

    Position (x_m, y_m, z_m) är en punkt på fasadytan (z = fönstrets mitt) i EPSG:3011/RH 2000,
    `normal_azimut_grader` är fasadens utåtriktade normal, norr = 0, medurs.
    """
    if sokvag.suffix.lower() != ".csv":
        raise LosarFel(f"fasadpunkter måste vara en CSV-fil (.csv), fick {sokvag.name}.")
    with sokvag.open("r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        saknas = [c for c in _FASAD_KOLUMNER if c not in (rd.fieldnames or [])]
        if saknas:
            raise LosarFel(
                f"{sokvag.name} saknar kolumner {saknas}; krävs: {list(_FASAD_KOLUMNER)}."
            )
        rader: list[dict[str, Any]] = []
        for nr, rad in enumerate(rd, start=2):
            try:
                x, y, z, az = (float(rad[c]) for c in _FASAD_KOLUMNER[1:])
            except (TypeError, ValueError) as exc:
                raise LosarFel(f"{sokvag.name} rad {nr}: ogiltigt tal ({exc}).") from exc
            if not all(math.isfinite(v) for v in (x, y, z, az)):
                raise LosarFel(f"{sokvag.name} rad {nr}: x, y, z och azimut måste vara ändliga.")
            if not (0.0 <= az < 360.0):
                raise LosarFel(f"{sokvag.name} rad {nr}: azimut {az} utanför [0, 360).")
            rader.append({"fasad_id": rad["fasad_id"], "x": x, "y": y, "z": z, "az": az})
    ids = [r["fasad_id"] for r in rader]
    if len(set(ids)) != len(ids):
        raise LosarFel(f"{sokvag.name}: fasad_id måste vara unika.")
    return rader


def _agd_av_ruta(k: LosarKontext, x: float, y: float) -> bool:
    """Rutan äger punkten om den ligger djupast inne i den (K1 har 25 m överlapp); lika djupt
    avgörs av ruta-id. Samma regel som kärnans jämförelser använder för celler."""
    assert k.ruta is not None

    def djup(r: Any) -> float:
        return float(min(x - r.x_min, r.x_max - x, y - r.y_min, r.y_max - y))

    egen = djup(k.ruta)
    if egen < 0:
        return False
    for g in k.grannar:
        d = djup(g)
        if d < 0:
            continue
        if g.ruta_id < k.ruta.ruta_id and d >= egen:
            return False
        if g.ruta_id > k.ruta.ruta_id and d > egen:
            return False
    return True


class VscFasad(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.vsc_fasad"
    beskrivning: ClassVar[str] = (
        "Vertical Sky Component (BRE, mulen CIE-himmel) på fasadpunkter ur en CSV-fil."
    )
    indataroller: ClassVar[tuple[str, ...]] = ("fasadpunkter",)
    utdataroller: ClassVar[tuple[str, ...]] = ("vsc",)
    _egna: ClassVar[set[str]] = {"halvplansriktningar", "utanfor_m"}

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        standard_k = max(4, int(ut["azimutriktningar"]) // 2)
        ut["halvplansriktningar"] = _heltal(parametrar, "halvplansriktningar", standard_k, minst=4)
        ut["utanfor_m"] = _tal(parametrar, "utanfor_m", 0.5 * float(ut["cellstorlek_m"]), over=0.0)
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        punkter = [
            p
            for p in las_fasadpunkter(kontext.indata["fasadpunkter"])
            if _agd_av_ruta(kontext, p["x"], p["y"])
        ]
        radie = _radie_m(kontext.parametrar)
        kf = int(kontext.parametrar["halvplansriktningar"])
        ut_m = float(kontext.parametrar["utanfor_m"])
        n = len(punkter)
        vsc = np.full(n, np.nan)
        trunk_ant = np.zeros(n, dtype=np.int64)
        status = ["ok"] * n
        if n:
            az_n = np.radians([p["az"] for p in punkter])
            x0 = np.array([p["x"] for p in punkter]) + ut_m * np.sin(az_n)
            y0 = np.array([p["y"] for p in punkter]) + ut_m * np.cos(az_n)
            z0 = np.array([p["z"] for p in punkter])
            f = m.falt
            ox, oy = f.cellkoord(x0, y0)
            inne = (ox >= 0) & (ox < f.bredd) & (oy >= 0) & (oy < f.hojd)
            for i in np.nonzero(~inne)[0]:
                status[int(i)] = "utanfor_data"
            chunk = max(1, _VSC_CHUNK_STRALAR // kf)
            ids = np.nonzero(inne)[0]
            from dagsljus.horisont import MaxPyramid

            pyr = MaxPyramid.bygg(f)
            for a in range(0, ids.size, chunk):
                s = ids[a : a + chunk]
                phi = halvplansriktningar(az_n[s], kf)
                res = horisont_mipmap(
                    f,
                    np.repeat(x0[s], kf),
                    np.repeat(y0[s], kf),
                    np.repeat(z0[s], kf),
                    phi.ravel(),
                    radie,
                    pyramid=pyr,
                )
                h = res.h_rad.reshape(len(s), kf)
                v = vsc_fran_halvplansprofil(h)
                inuti = res.inuti_hinder.reshape(len(s), kf)[:, 0]
                tr = res.trunkerad.reshape(len(s), kf).sum(axis=1)
                for q, idx in enumerate(s):
                    trunk_ant[idx] = int(tr[q])
                    if inuti[q]:
                        status[int(idx)] = "inuti_hinder"
                    else:
                        vsc[idx] = float(v[q])
        with (kontext.utmapp / "vsc.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow([*_FASAD_KOLUMNER, "vsc_procent", "trunkerade_riktningar", "status"])
            for i, p in enumerate(punkter):
                w.writerow(
                    [
                        p["fasad_id"],
                        repr(p["x"]),
                        repr(p["y"]),
                        repr(p["z"]),
                        repr(p["az"]),
                        "" if math.isnan(vsc[i]) else f"{100.0 * vsc[i]:.4f}",
                        int(trunk_ant[i]),
                        status[i],
                    ]
                )
        giltiga = vsc[np.isfinite(vsc)]
        var = _gemensamma_varningar(kontext, m)
        for namn, text in (
            (
                "inuti_hinder",
                "startpunkten ligger under höjden i sin egen cell (punkten sitter i en "
                "byggnad/under ett tak i DSM); VSC lämnas tom, kontrollera läget",
            ),
            ("utanfor_data", "startpunkten ligger utanför höjdfältet; VSC lämnas tom"),
        ):
            antal = status.count(namn)
            if antal:
                var.append(f"{antal} fasadpunkter: {text}")
        n_trunk = int(np.count_nonzero(trunk_ant))
        if n_trunk:
            var.append(
                f"{n_trunk} fasadpunkter har strålar som lämnar höjdfältet före påverkansradien "
                "(kolumnen trunkerade_riktningar)"
            )
        return LosarUtdata(
            filer={"vsc": "vsc.csv"},
            matvarden={
                "fasadpunkter": n,
                "vsc_min_procent": float(100.0 * giltiga.min()) if giltiga.size else None,
                "vsc_medel_procent": float(100.0 * giltiga.mean()) if giltiga.size else None,
            },
            varningar=tuple(var),
        )


LOSARE: list[Analyslosare] = [Horisont(), SoltimmarMark(), SvfMark(), VscFasad()]
