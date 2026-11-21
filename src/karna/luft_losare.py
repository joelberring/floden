"""Luftlösare för kärnan (K21) som räknar på K1 v2-rutor: `luft.vindkomfort` och `luft.halt`.

Licensgräns: den här filen ligger i kärnan (GPL v3) och importerar luftdelen (MIT, `src/luft/`),
inte
tvärtom, så luftpaketet saknar `floden_losare.py` (och beroende på `karna`). Lösarna registreras via
`karna.inbyggda`. Luftimporterna sker i `kor()` så att `floden fraga losare` inte laddar scipy i
onödan.

Geometrin kommer ur K1 v2 via `karna.geometri.luftgeometri` (terräng, byggnader, vegetation,
markslag), efter att kärnan har tillämpat K9-scenariot (`byggnad_ny`, `byggnad_hojd`, ...) på
rutorna.

Påverkansradie: `None` (globalt jobb över alla rutor i scenariot). Det finns inget konservativt
mått:
vindfältet kring en byggnad beror på hela rutnätet (tryckprojektionen är global, kölvattnet når ett
tiotal byggnadshöjder nedströms i alla vindriktningar som ingår i statistiken), och halten i en cell
beror på alla källor uppströms. Något mindre än hela scenariot vore ett antagande som inte kan
visas, så jobbet är globalt (se `docs/ANSLUTA_LOSARE.md`: "är du osäker, välj None"). Före/efter
jämförs med `[[jamforelser]]` på GeoTIFF-utdata.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from karna.analyslosare import Analyslosare, LosarFel, LosarKontext, LosarUtdata

_GEMENSAMMA = {
    "dx_m",
    "raster_cell_m",
    "riktningar",
    "hojd_doman_m",
    "buffert_uppstroms_m",
    "buffert_nedstroms_m",
    "buffert_sidor_m",
    "omsampling",
    "vegetation",
    "u_ref_m_s",
    "z_ref_m",
    "z0_m",
    "genomstromningar",
    "vaggmodell",
    "z0_vagg_m",
}


def _tal(
    p: Mapping[str, Any],
    namn: str,
    standard: float,
    *,
    over: float | None = None,
    minst: float | None = None,
) -> float:
    v = p.get(namn, standard)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise LosarFel(f"Parametern '{namn}' måste vara ett ändligt tal, fick {v!r}.")
    if over is not None and float(v) <= over:
        raise LosarFel(f"Parametern '{namn}' måste vara > {over}, fick {v}.")
    if minst is not None and float(v) < minst:
        raise LosarFel(f"Parametern '{namn}' måste vara >= {minst}, fick {v}.")
    return float(v)


def _valfritt_tal(p: Mapping[str, Any], namn: str) -> float | None:
    if p.get(namn) is None:
        return None
    return _tal(p, namn, 0.0, over=0.0)


def _gemensamma(p: Mapping[str, Any]) -> dict[str, Any]:
    ri = p.get("riktningar", 4)
    if isinstance(ri, bool) or not isinstance(ri, int | float) or float(ri) != int(ri):
        raise LosarFel(f"Parametern 'riktningar' måste vara ett heltal, fick {ri!r}.")
    if not 1 <= int(ri) <= 72:
        raise LosarFel(f"Parametern 'riktningar' måste vara 1-72, fick {ri}.")
    oms = p.get("omsampling", "medel")
    if oms not in ("medel", "max"):
        raise LosarFel(f"omsampling måste vara 'medel' eller 'max', fick {oms!r}.")
    veg = p.get("vegetation", False)
    if not isinstance(veg, bool):
        raise LosarFel(f"vegetation måste vara true/false, fick {veg!r}.")
    vagg = p.get("vaggmodell", "ingen")
    if vagg not in ("ingen", "loglag"):
        raise LosarFel(f"vaggmodell måste vara 'ingen' eller 'loglag', fick {vagg!r}.")
    dx = _tal(p, "dx_m", 4.0, over=0.0)
    return {
        "dx_m": dx,
        "raster_cell_m": _valfritt_tal(p, "raster_cell_m"),
        "riktningar": int(ri),
        "hojd_doman_m": _valfritt_tal(p, "hojd_doman_m"),
        "buffert_uppstroms_m": _tal(p, "buffert_uppstroms_m", 80.0, minst=0.0),
        "buffert_nedstroms_m": _tal(p, "buffert_nedstroms_m", 160.0, minst=0.0),
        "buffert_sidor_m": _tal(p, "buffert_sidor_m", 60.0, minst=0.0),
        "omsampling": oms,
        "vegetation": veg,
        "u_ref_m_s": _tal(p, "u_ref_m_s", 5.0, over=0.0),
        "z_ref_m": _tal(p, "z_ref_m", 10.0, over=0.0),
        "z0_m": _tal(p, "z0_m", 0.5, over=0.0),
        "genomstromningar": _tal(p, "genomstromningar", 2.0, over=0.0),
        "vaggmodell": vagg,
        "z0_vagg_m": _valfritt_tal(p, "z0_vagg_m"),
    }


def _kontrollera_okanda(p: Mapping[str, Any], tillatna: set[str], namn: str) -> None:
    okanda = sorted(set(p) - tillatna)
    if okanda:
        raise LosarFel(f"{namn}: okända parametrar {okanda}. Tillåtna: {sorted(tillatna)}.")


def _bygg_bibliotek(k: LosarKontext) -> tuple[Any, Any, list[str]]:
    """K1 v2-rutorna → luftgeometri → vindbibliotek (LES per riktning). Returnerar
    (LuftGeometri, Vindbibliotek, varningar)."""
    from karna.geometri import GeometriFel
    from karna.geometri.luftgeometri import luft_geometri
    from luft.kedja import bygg_vindbibliotek
    from luft.lbm import LbmParametrar
    from luft.typer import Inflode, LuftFel

    p = k.parametrar
    try:
        geom = luft_geometri(
            [r.mapp for r in k.scenario.rutor],
            dx_m=float(p["dx_m"]),
            raster_cell_m=p["raster_cell_m"],
            hojd_doman_m=p["hojd_doman_m"],
            buffert_uppstroms_m=float(p["buffert_uppstroms_m"]),
            buffert_nedstroms_m=float(p["buffert_nedstroms_m"]),
            buffert_sidor_m=float(p["buffert_sidor_m"]),
            omsampling=p["omsampling"],
            vegetation=bool(p["vegetation"]),
        )
        z0_vagg = p["z0_vagg_m"]
        if p["vaggmodell"] == "loglag" and z0_vagg is None:
            if geom.z0_markyta_m is None:
                raise LosarFel(
                    "vaggmodell='loglag' men markytans z0 kan inte räknas (inga markceller)."
                )
            z0_vagg = geom.z0_markyta_m
        g = float(p["genomstromningar"])
        param = LbmParametrar(
            upprampning_genomstromningar=g,
            medel_genomstromningar=g,
            vaggmodell=str(p["vaggmodell"]),
            z0_vagg_m=None if z0_vagg is None else float(z0_vagg),
        )
        n = int(p["riktningar"])
        bib = bygg_vindbibliotek(
            geom.spec,
            [i * 360.0 / n for i in range(n)],
            Inflode(float(p["u_ref_m_s"]), float(p["z_ref_m"]), float(p["z0_m"])),
            param,
        )
    except (GeometriFel, LuftFel) as exc:
        raise LosarFel(str(exc)) from exc
    return geom, bib, list(geom.varningar)


class LuftVindkomfort(Analyslosare):
    """Vindkomfort och vindfara på gånghöjd (NEN 8100 / Lawson) på K1 v2-rutor."""

    namn: ClassVar[str] = "luft.vindkomfort"
    doman: ClassVar[str] = "luft"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = (
        "Vindkomfort och vindfara på gånghöjd ur LES (LBM) på K1 v2-geometrin; "
        "väder som CSV (tid,vindriktning_grad,vindhastighet_m_s)."
    )
    indataroller: ClassVar[tuple[str, ...]] = ("vader",)
    utdataroller: ClassVar[tuple[str, ...]] = ("komfortklass", "faraklass", "forstarkning")

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        _kontrollera_okanda(
            parametrar, _GEMENSAMMA | {"kriterium", "toppfaktor", "hojd_m"}, self.namn
        )
        ut = _gemensamma(parametrar)
        krit = parametrar.get("kriterium", "nen8100")
        if krit not in ("nen8100", "lawson-lddc"):
            raise LosarFel(f"kriterium måste vara 'nen8100' eller 'lawson-lddc', fick {krit!r}.")
        ut["kriterium"] = krit
        ut["toppfaktor"] = _valfritt_tal(parametrar, "toppfaktor")
        ut["hojd_m"] = _valfritt_tal(parametrar, "hojd_m")
        return ut

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        return None  # globalt, se modulens docstring

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from luft.export import skriv_geotiff_float
        from luft.typer import LuftFel
        from luft.vindbibliotek import Vaderserie
        from luft.vindkomfort import berakna_komfort

        p = kontext.parametrar
        geom, bib, varningar = _bygg_bibliotek(kontext)
        try:
            vader = Vaderserie.las_csv(kontext.indata["vader"])
            res = berakna_komfort(
                bib,
                vader,
                geom.mosaik.utbredning,
                geom.mosaik.cell_m,
                p["kriterium"],
                p["toppfaktor"],
                p["hojd_m"],
            )
            ut = kontext.utmapp
            forst = np.mean(np.stack(res.forstarkning_per_riktning, axis=0), axis=0)
            skriv_geotiff_float(
                ut / "komfortklass.tif",
                res.komfortklass,
                res.ruta,
                "Komfortklass (index i kriteriets klasslista)",
                "klass",
            )
            skriv_geotiff_float(
                ut / "faraklass.tif",
                res.faraklass,
                res.ruta,
                "Faraklass (index i kriteriets klasslista)",
                "klass",
            )
            skriv_geotiff_float(
                ut / "forstarkning.tif",
                forst,
                res.ruta,
                "Vindförstärkning U_lok/U_ref på gånghöjd (medel över riktningar)",
                "-",
            )
        except LuftFel as exc:
            raise LosarFel(str(exc)) from exc
        k = res.komfortklass
        giltig = np.isfinite(k)
        matv: dict[str, Any] = dict(geom.matvarden)
        matv.update(
            {
                "luftceller_rutnat": int(
                    sum(int(bib.for_riktning(r).grid.luft.sum()) for r in bib.riktningar_grad)
                ),
                "komfortklass_max": float(np.nanmax(k)) if giltig.any() else None,
                "forstarkning_max": float(np.nanmax(forst)) if np.isfinite(forst).any() else None,
                "kriterium": res.kriterium.namn,
                "z0_markyta_m": geom.z0_markyta_m,
            }
        )
        return LosarUtdata(
            filer={
                "komfortklass": "komfortklass.tif",
                "faraklass": "faraklass.tif",
                "forstarkning": "forstarkning.tif",
            },
            matvarden=matv,
            varningar=tuple(sorted(set(varningar + list(res.varningar)))),
        )


class LuftHalt(Analyslosare):
    """Årsmedelhalt från vägar (linjekällor) på K1 v2-rutor."""

    namn: ClassVar[str] = "luft.halt"
    doman: ClassVar[str] = "luft"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = (
        "Årsmedelhalt på utvärderingshöjd ur LES + linjär spridning på K1 v2-geometrin; vägar som "
        "GeoPackage med emission_g_m_s, väder som CSV."
    )
    indataroller: ClassVar[tuple[str, ...]] = ("vagar", "vader")
    utdataroller: ClassVar[tuple[str, ...]] = ("arsmedel",)

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        _kontrollera_okanda(
            parametrar,
            _GEMENSAMMA | {"amne", "deposition_m_s", "sedimentation_m_s", "utvardering_hojd_m"},
            self.namn,
        )
        ut = _gemensamma(parametrar)
        amne = parametrar.get("amne", "NOx")
        if not isinstance(amne, str) or not amne:
            raise LosarFel(f"amne måste vara en icke-tom text, fick {amne!r}.")
        ut["amne"] = amne
        ut["deposition_m_s"] = _tal(parametrar, "deposition_m_s", 0.0, minst=0.0)
        ut["sedimentation_m_s"] = _tal(parametrar, "sedimentation_m_s", 0.0, minst=0.0)
        ut["utvardering_hojd_m"] = _tal(parametrar, "utvardering_hojd_m", 2.0, over=0.0)
        return ut

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        return None  # globalt: källor och vind uppströms når hela domänen

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from luft.export import skriv_halt_geotiff
        from luft.kedja import berakna_halter
        from luft.transport import Amne
        from luft.typer import LuftFel
        from luft.vindbibliotek import Vaderserie

        p = kontext.parametrar
        geom, bib, varningar = _bygg_bibliotek(kontext)
        try:
            linjer, emission = _las_vagar(kontext.indata["vagar"])
            vader = Vaderserie.las_csv(kontext.indata["vader"])
            amne = Amne(
                str(p["amne"]),
                deposition_m_s=float(p["deposition_m_s"]),
                sedimentation_m_s=float(p["sedimentation_m_s"]),
            )
            res = berakna_halter(
                bib,
                amne,
                linjer,
                emission,
                vader,
                geom.mosaik.utbredning,
                geom.mosaik.cell_m,
                utvardering_hojd_m=float(p["utvardering_hojd_m"]),
            )
            arsmedel = res.statistik.arsmedel()
            skriv_halt_geotiff(
                kontext.utmapp / "arsmedel.tif", arsmedel, res.ruta, f"Årsmedelhalt {amne.namn}"
            )
        except LuftFel as exc:
            raise LosarFel(str(exc)) from exc
        matv: dict[str, Any] = dict(geom.matvarden)
        matv.update(res.matvarden)
        matv["max_arsmedel_ug_m3"] = float(np.nanmax(arsmedel) * 1e6)
        matv["antal_linjer"] = len(linjer)
        varningar += [v for rp in res.kallrapporter for v in rp.varningar]
        return LosarUtdata(
            filer={"arsmedel": "arsmedel.tif"},
            matvarden=matv,
            varningar=tuple(sorted(set(varningar))),
        )


def _las_vagar(sokvag: Path) -> tuple[list[Any], list[float]]:
    """Linjekällor ur en GeoPackage med `emission_g_m_s` (EPSG:3011)."""
    import geopandas as gpd

    from luft.typer import LuftFel

    df = gpd.read_file(sokvag)
    if df.crs is None or df.crs.to_epsg() != 3011:
        raise LuftFel(f"{sokvag}: vägarna ska ligga i EPSG:3011.")
    if "emission_g_m_s" not in df.columns:
        raise LuftFel(f"{sokvag}: fältet emission_g_m_s saknas.")
    linjer: list[Any] = []
    emission: list[float] = []
    for geom, e in zip(df.geometry, df["emission_g_m_s"], strict=True):
        for g in getattr(geom, "geoms", [geom]):
            linjer.append(np.asarray(g.coords, dtype=np.float64)[:, :2])
            emission.append(float(e))
    return linjer, emission


LOSARE: list[Analyslosare] = [LuftVindkomfort(), LuftHalt()]
