"""Dagsljuslösare enligt K21 (`docs/ANSLUTA_LOSARE.md`): upptäcks automatiskt av kärnan.

- `dagsljus.horisont`: horisontprofiler per cell (uint16, 0,01 grader) för den egna rutan.
- `dagsljus.soltimmar_mark`: soltimmar per dygn (medel över valda datum) för markceller.
- `dagsljus.svf_mark`: himmelsexponering (SVF) för horisontella ytor.
- `dagsljus.vsc_fasad`: VSC (BRE, mulen CIE-himmel) för fasadpunkter: K11 ur byggnadsregistret
  (standard) eller en CSV-fil.

Höjdfältet är ytmodellen (DSM) ur K1 v2 via `karna.geometri.las_ytmodell`; K1 v1-rutor vägras
(fast hinderhöjd). Byggnader med gissad höjd (`hojdkalla = standard`) ger varning och flaggade
resultat. Alla lösare är rutvisa med påverkansradien H_max / tan(h_min) (konservativ). Strålar
går högst så långt, så hinder som skymmer mindre än h_min bortom radien ingår inte; se
`docs/DAGSLJUS.md`.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

_GEMENSAMMA = {
    "azimutriktningar",
    "hojd_max_m",
    "h_min_grader",
    "cellstorlek_m",
    "omsampling",
    "vegetation",
}
STANDARD_K = 64
"""Standard för azimutriktningar (horisont, SVF, VSC). Soltimmar har högre standard, se nedan."""
STANDARD_K_SOLTIMMAR = 180
"""Mätt mot strålgång i `docs/DAGSLJUS.md` avsnitt 6: K = 64 räcker inte för skuggstudier."""
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


def _gemensamma(p: Mapping[str, Any], standard_k: int = STANDARD_K) -> dict[str, Any]:
    h_min = _tal(p, "h_min_grader", 5.0, over=0.0)
    if h_min >= 90.0:
        raise LosarFel(f"h_min_grader måste vara < 90, fick {h_min}.")
    oms = p.get("omsampling", "medel")
    if oms not in _OMSAMPLING:
        raise LosarFel(f"omsampling måste vara en av {_OMSAMPLING}, fick {oms!r}.")
    veg = p.get("vegetation", False)
    if not isinstance(veg, bool):
        raise LosarFel(f"vegetation måste vara true/false, fick {veg!r}.")
    return {
        "azimutriktningar": _heltal(p, "azimutriktningar", standard_k, minst=8),
        "hojd_max_m": _tal(p, "hojd_max_m", 60.0, over=0.0),
        "h_min_grader": h_min,
        "cellstorlek_m": _tal(p, "cellstorlek_m", 1.0, over=0.0),
        "omsampling": oms,
        "vegetation": veg,
    }


def _kontrollera_okanda(p: Mapping[str, Any], tillatna: set[str], namn: str) -> None:
    okanda = sorted(set(p) - tillatna)
    if okanda:
        raise LosarFel(f"{namn}: okända parametrar {okanda}. Tillåtna: {sorted(tillatna)}.")


def _radie_m(p: Mapping[str, Any]) -> float:
    """Sökavstånd för horisonten: H_max / tan(h_min)."""
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
            vegetation=bool(k.parametrar["vegetation"]),
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
    if m.vegetation:
        v.append(
            "vegetation ingår som ogenomskinliga hinder (vegetation.tif); trädkronor är delvis "
            "transparenta, särskilt lövträd utan löv på vårdagjämningen, så skuggan överskattas"
        )
    return v


def _standard_varning(m: Mosaik, antal_flaggade: int, antal_totalt: int, enhet: str) -> list[str]:
    """Varning när byggnader med gissad höjd (`hojdkalla = standard`) finns i mosaiken."""
    if not m.standard_byggnader:
        return []
    return [
        f"{len(m.standard_byggnader)} byggnader i mosaiken (varav {m.standard_byggnader_egen_ruta} "
        "i den egna rutans register) har standardhöjd (hojdkalla = standard: takhöjden är en "
        f"gissning, inte data). Resultatet beror på dem i {antal_flaggade} av {antal_totalt} "
        f"{enhet}, flaggade i standardhojd (1 = resultatet ändras om byggnaderna tas bort)"
    ]


@dataclass(frozen=True)
class _Pass:
    """Resultat av horisontpasset: trunkering och, om mosaiken har byggnader med standardhöjd,
    antal riktningar per cell där de sätter horisonten."""

    trunk: NDArray[np.int32]
    standard_riktningar: NDArray[np.int32] | None


def _horisontpass(
    k: LosarKontext,
    m: Mosaik,
    mottagare: list[Callable[[int, float, NDArray[np.float32]], None]],
    mottagare_utan_standard: list[Callable[[int, float, NDArray[np.float32]], None]] | None = None,
    *,
    aktiva_riktningar: frozenset[int] | None = None,
    hoppa_over: list[Callable[[int], None]] | None = None,
    hoppa_over_utan_standard: list[Callable[[int], None]] | None = None,
) -> _Pass:
    """Beräknar horisonten för det egna fönstret riktning för riktning. `trunk` är antal
    riktningar per cell vars stråle lämnade höjdfältet före max avstånd.

    Har mosaiken byggnader med standardhöjd beräknas horisonten också utan dem
    (`falt_utan_standard`) och skickas till `mottagare_utan_standard`; det kostar ett varv till
    per riktning. När `aktiva_riktningar` anges (t.ex. solens azimutsektor i `SoltimmarMark`)
    hoppas inaktiva riktningar över utan att anropa `horisont_riktning_ruta`."""
    antal = int(k.parametrar["azimutriktningar"])
    radie = _radie_m(k.parametrar)
    r0, r1, c0, c1 = m.fonster
    trunk = np.zeros((r1 - r0, c1 - c0), dtype=np.int32)
    std = None if m.falt_utan_standard is None else np.zeros_like(trunk)
    for i, phi in enumerate(riktningar_rad(antal)):
        if aktiva_riktningar is not None and i not in aktiva_riktningar:
            for g in hoppa_over or []:
                g(i)
            if m.falt_utan_standard is not None:
                for g in hoppa_over_utan_standard or []:
                    g(i)
            continue
        h, tr = horisont_riktning_ruta(m.falt, float(phi), radie, fonster=m.fonster)
        trunk += tr
        for f in mottagare:
            f(i, float(phi), h)
        if m.falt_utan_standard is not None and std is not None:
            h0, _ = horisont_riktning_ruta(
                m.falt_utan_standard, float(phi), radie, fonster=m.fonster
            )
            std += h0 != h
            for f in mottagare_utan_standard or []:
                f(i, float(phi), h0)
    return _Pass(trunk, std)


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


def _skriv_standardflagga(
    k: LosarKontext, m: Mosaik, flagga: NDArray[np.bool_] | None, form: tuple[int, int]
) -> tuple[int, list[str]]:
    """Skriver `standardhojd.tif` (1 = cellens resultat beror på en byggnad med standardhöjd,
    alltid skriven, noll om inga sådana byggnader finns) och returnerar antal flaggade celler
    samt varning."""
    ark = np.zeros(form) if flagga is None else flagga.astype(np.float64)
    _skriv_raster(k.utmapp / "standardhojd.tif", ark, m)
    antal = int(np.count_nonzero(ark))
    return antal, _standard_varning(m, antal, int(ark.size), "celler")


class _DagsljusLosare(Analyslosare):
    doman: ClassVar[str] = "dagsljus"
    version: ClassVar[str] = "2"
    _egna: ClassVar[set[str]] = set()
    _standard_k: ClassVar[int] = STANDARD_K

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        _kontrollera_okanda(parametrar, _GEMENSAMMA | self._egna, self.namn)
        return _gemensamma(parametrar, self._standard_k)

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        # Horisonten räknas mot cellens närmaste kant, inte dess mitt. Påverkansradien till
        # kärnan får därför en celldiagonals marginal så att den är konservativ: en punkt exakt
        # på H/tan(h_min) från en cellkant ändrades annars (0,333 h i fas 1b-mätningen).
        return _radie_m(parametrar) + math.sqrt(2.0) * float(parametrar["cellstorlek_m"])


class Horisont(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.horisont"
    beskrivning: ClassVar[str] = (
        "Horisontprofil h(phi) i K azimutriktningar per cell (DSM = mark + byggnader)."
    )
    utdataroller: ClassVar[tuple[str, ...]] = (
        "horisont",
        "horisont_meta",
        "trunkering",
        "standardhojd",
    )

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        lagr = np.empty((antal, r1 - r0, c1 - c0), dtype=np.uint16)

        def lagra(i: int, _phi: float, h: NDArray[np.float32]) -> None:
            lagr[i] = np.rint(np.degrees(h.astype(np.float64)) * 100.0).astype(np.uint16)

        pas = _horisontpass(kontext, m, [lagra])
        trunk = pas.trunk
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
        n_std, std_var = _skriv_standardflagga(
            kontext,
            m,
            None if pas.standard_riktningar is None else pas.standard_riktningar > 0,
            trunk.shape,
        )
        return LosarUtdata(
            filer={
                "horisont": "horisont.npy",
                "horisont_meta": "horisont.json",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "horisont_max_grader": float(lagr.max()) / 100.0,
                "celler": int(trunk.size),
                "azimutriktningar": antal,
                "celler_standardhojd": n_std,
            },
            varningar=tuple(
                _gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal) + std_var
            ),
        )


class SvfMark(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.svf_mark"
    beskrivning: ClassVar[str] = "Himmelsexponering (SVF) för horisontella markytor och tak."
    utdataroller: ClassVar[tuple[str, ...]] = ("svf", "trunkering", "standardhojd")

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        summa = np.zeros((r1 - r0, c1 - c0))
        summa0 = np.zeros((r1 - r0, c1 - c0))

        def ackumulera(_i: int, _phi: float, h: NDArray[np.float32]) -> None:
            summa.__iadd__(np.sin(h.astype(np.float64)) ** 2)

        def ackumulera0(_i: int, _phi: float, h: NDArray[np.float32]) -> None:
            summa0.__iadd__(np.sin(h.astype(np.float64)) ** 2)

        pas = _horisontpass(kontext, m, [ackumulera], [ackumulera0])
        trunk = pas.trunk
        svf = 1.0 - summa / antal
        _skriv_raster(kontext.utmapp / "svf.tif", svf, m)
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        flagga = None if pas.standard_riktningar is None else (1.0 - summa0 / antal) != svf
        n_std, std_var = _skriv_standardflagga(kontext, m, flagga, svf.shape)
        return LosarUtdata(
            filer={
                "svf": "svf.tif",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "svf_medel": float(svf.mean()),
                "svf_min": float(svf.min()),
                "celler": int(svf.size),
                "celler_standardhojd": n_std,
            },
            varningar=tuple(
                _gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal) + std_var
            ),
        )


class SoltimmarMark(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.soltimmar_mark"
    beskrivning: ClassVar[str] = "Soltimmar per dygn (medel över valda datum) för markceller."
    utdataroller: ClassVar[tuple[str, ...]] = ("soltimmar", "trunkering", "standardhojd")
    _egna: ClassVar[set[str]] = {"datum", "steg_min", "refraktion"}
    _standard_k: ClassVar[int] = STANDARD_K_SOLTIMMAR

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
        form = (r1 - r0, c1 - c0)
        steg_h = steg_min / 60.0 / len(datum)
        ack = SoltimmarAckumulator(antal, sol_h, sol_az, steg_h, form)
        ack0 = SoltimmarAckumulator(antal, sol_h, sol_az, steg_h, form)
        pas = _horisontpass(
            kontext,
            m,
            [lambda i, _p, h: ack.mata(i, h)],
            [lambda i, _p, h: ack0.mata(i, h)],
            aktiva_riktningar=ack.aktiva_riktningar(),
            hoppa_over=[lambda i: ack.mata(i, None)],
            hoppa_over_utan_standard=[lambda i: ack0.mata(i, None)],
        )
        trunk = pas.trunk
        timmar = ack.timmar()
        _skriv_raster(kontext.utmapp / "soltimmar.tif", timmar, m)
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        flagga = None if pas.standard_riktningar is None else ack0.timmar() != timmar
        n_std, std_var = _skriv_standardflagga(kontext, m, flagga, timmar.shape)
        var = _gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal) + std_var
        if antal < 128:
            var.append(
                f"azimutriktningar={antal}: soltimmar nära skuggkanter kan avvika flera tiondels "
                "timmar (uppmätt på syntetiskt block, docs/DAGSLJUS.md); använd >= 180 för "
                "skuggstudier"
            )
        return LosarUtdata(
            filer={
                "soltimmar": "soltimmar.tif",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "soltimmar_medel_h": float(timmar.mean()),
                "soltimmar_min_h": float(timmar.min()),
                "soltimmar_max_h": float(timmar.max()),
                "datum": len(datum),
                "lat_grader": lat,
                "lon_grader": lon,
                "celler_standardhojd": n_std,
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


_UT_KOLUMNER = (
    *_FASAD_KOLUMNER,
    "vsc_procent",
    "trunkerade_riktningar",
    "status",
    "standardhojd",
    "byggnad_id",
)


def _k11_punkter(k: LosarKontext, m: Mosaik) -> list[dict[str, Any]]:
    """Fasadpunkter (K11) ur byggnadsregistret för rutan och dess grannar inom radien. Rutan
    äger punkterna som ligger djupast inne i den; `byggnader` (valfritt urval av byggnad_id)
    begränsar punkterna. Punkter på byggnader med standardhöjd flaggas."""
    from karna.geometri import GeometriFel, fasadpunkter

    p = k.parametrar
    try:
        f = fasadpunkter(
            [r.mapp for r in k.rutor_att_lasa()],
            float(p["fasad_steg_m"]),
            float(p["vaningshojd_m"]),
            hojd_over_golv_m=float(p["hojd_over_golv_m"]),
        )
    except GeometriFel as exc:
        raise LosarFel(f"fasadpunkter (K11): {exc}") from exc
    urval = set(p["byggnader"])
    ut: list[dict[str, Any]] = []
    for i in range(len(f)):
        bid = str(f.byggnad_id[i])
        if urval and bid not in urval:
            continue
        x, y = float(f.e_m[i]), float(f.n_m[i])
        if not _agd_av_ruta(k, x, y):
            continue
        ut.append(
            {
                "fasad_id": str(f.fasadpunkt_id[i]),
                "x": x,
                "y": y,
                "z": float(f.z_rh2000[i]),
                "az": float(f.normal_azimut_grader[i]),
                "byggnad_id": bid,
                "standard": bid in m.standard_byggnader,
            }
        )
    return ut


@dataclass(frozen=True)
class _VscResultat:
    vsc: NDArray[np.float64]
    trunk_ant: NDArray[np.int64]
    status: list[str]


def _vsc_pa_falt(
    falt: Any,
    x0: NDArray[np.float64],
    y0: NDArray[np.float64],
    z0: NDArray[np.float64],
    az_n: NDArray[np.float64],
    radie: float,
    kf: int,
) -> _VscResultat:
    from dagsljus.horisont import MaxPyramid

    n = x0.size
    vsc = np.full(n, np.nan)
    trunk_ant = np.zeros(n, dtype=np.int64)
    status = ["ok"] * n
    if not n:
        return _VscResultat(vsc, trunk_ant, status)
    ox, oy = falt.cellkoord(x0, y0)
    inne = (ox >= 0) & (ox < falt.bredd) & (oy >= 0) & (oy < falt.hojd)
    for i in np.nonzero(~inne)[0]:
        status[int(i)] = "utanfor_data"
    chunk = max(1, _VSC_CHUNK_STRALAR // kf)
    ids = np.nonzero(inne)[0]
    pyr = MaxPyramid.bygg(falt)
    for a in range(0, ids.size, chunk):
        s = ids[a : a + chunk]
        phi = halvplansriktningar(az_n[s], kf)
        res = horisont_mipmap(
            falt,
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
    return _VscResultat(vsc, trunk_ant, status)


class VscFasad(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.vsc_fasad"
    beskrivning: ClassVar[str] = (
        "Vertical Sky Component (BRE, mulen CIE-himmel) på fasadpunkter: K11 ur byggnadsregistret "
        "eller en CSV-fil."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("fasadpunkter",)
    utdataroller: ClassVar[tuple[str, ...]] = ("vsc",)
    _egna: ClassVar[set[str]] = {
        "halvplansriktningar",
        "utanfor_m",
        "fasad_steg_m",
        "vaningshojd_m",
        "hojd_over_golv_m",
        "byggnader",
    }

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        standard_k = max(4, int(ut["azimutriktningar"]) // 2)
        ut["halvplansriktningar"] = _heltal(parametrar, "halvplansriktningar", standard_k, minst=4)
        ut["utanfor_m"] = _tal(parametrar, "utanfor_m", 0.5 * float(ut["cellstorlek_m"]), over=0.0)
        ut["fasad_steg_m"] = _tal(parametrar, "fasad_steg_m", 3.0, over=0.0)
        ut["vaningshojd_m"] = _tal(parametrar, "vaningshojd_m", 3.0, over=0.0)
        ut["hojd_over_golv_m"] = _tal(parametrar, "hojd_over_golv_m", 1.5, over=0.0)
        urval = parametrar.get("byggnader", [])
        if not isinstance(urval, list | tuple) or not all(isinstance(b, str) for b in urval):
            raise LosarFel(f"byggnader måste vara en lista av byggnad_id (text), fick {urval!r}.")
        ut["byggnader"] = sorted(set(urval))
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        csv_fil = kontext.indata.get("fasadpunkter")
        if csv_fil is not None:
            if kontext.parametrar["byggnader"]:
                raise LosarFel(
                    "byggnader (urval av byggnad_id) gäller bara fasadpunkter ur K11; med en "
                    "CSV-fil i indata styr filen vilka punkter som räknas."
                )
            punkter = [
                {**p, "byggnad_id": "", "standard": False}
                for p in las_fasadpunkter(csv_fil)
                if _agd_av_ruta(kontext, p["x"], p["y"])
            ]
            kalla = "csv"
        else:
            punkter = _k11_punkter(kontext, m)
            kalla = "k11"
        radie = _radie_m(kontext.parametrar)
        kf = int(kontext.parametrar["halvplansriktningar"])
        ut_m = float(kontext.parametrar["utanfor_m"])
        n = len(punkter)
        az_n = np.radians([p["az"] for p in punkter])
        # Minst halva cellens projektion längs fasadnormalen (0,5·cell axelparallellt,
        # √2/2·cell vid 45°) så att diagonala väggar inte fastnar i egen rastercell.
        cell_proj = (
            0.5 * (1.0 + 1e-6) * m.falt.cell_m * (np.abs(np.sin(az_n)) + np.abs(np.cos(az_n)))
        )
        ut_eff = np.maximum(ut_m, cell_proj)
        x0 = np.array([p["x"] for p in punkter]) + ut_eff * np.sin(az_n)
        y0 = np.array([p["y"] for p in punkter]) + ut_eff * np.cos(az_n)
        z0 = np.array([p["z"] for p in punkter])
        res = _vsc_pa_falt(m.falt, x0, y0, z0, az_n, radie, kf)
        vsc, trunk_ant, status = res.vsc, res.trunk_ant, res.status
        # Byggnader med standardhöjd: flagga punkter på dem och punkter vars VSC ändras när
        # byggnaderna tas bort (samma fält utan dem).
        std_flagga = np.array([bool(p["standard"]) for p in punkter], dtype=bool)
        if m.falt_utan_standard is not None and n:
            utan = _vsc_pa_falt(m.falt_utan_standard, x0, y0, z0, az_n, radie, kf)
            std_flagga |= (utan.vsc != vsc) & ~(np.isnan(utan.vsc) & np.isnan(vsc))
            std_flagga |= np.array([a != b for a, b in zip(utan.status, status, strict=True)])
        with (kontext.utmapp / "vsc.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(_UT_KOLUMNER)
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
                        int(std_flagga[i]),
                        p["byggnad_id"],
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
        n_std = int(np.count_nonzero(std_flagga))
        if m.standard_byggnader:
            var += _standard_varning(m, n_std, n, "fasadpunkter")
            var[-1] += " eller sitter på en sådan byggnad"
        return LosarUtdata(
            filer={"vsc": "vsc.csv"},
            matvarden={
                "fasadpunkter": n,
                "fasadkalla": kalla,
                "fasadpunkter_standardhojd": n_std,
                "vsc_min_procent": float(100.0 * giltiga.min()) if giltiga.size else None,
                "vsc_medel_procent": float(100.0 * giltiga.mean()) if giltiga.size else None,
            },
            varningar=tuple(var),
        )


LOSARE: list[Analyslosare] = [Horisont(), SoltimmarMark(), SvfMark(), VscFasad()]
