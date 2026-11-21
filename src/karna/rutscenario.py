"""Scenarier som bas + diff (T20).

Ett scenario är en uppsättning K1-rutor. Basen är en K1-ruta (katalog med `mark.tif`)
eller en katalog med K1-rutor (`mm_E_N/`). Projektlappar (K3) tillämpas ruta för ruta;
rutor som en lapp inte ändrar behåller basens innehåll och därmed basens hash. Det är
det som gör att en lapp bara räknas om lokalt i kärnan.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from gemensamt.hash import sha256_fil
from karna.hashning import nyckel_for as kanonisk_hash
from karna.resultatcache import ResultatCache
from karna.scenario import Andring
from karna.scenario import ScenarioFel as _K9ScenarioFel
from karna.utredning import ScenarioSpec

if TYPE_CHECKING:
    from lapp.kontrakt import Gatusektion, ProjektLapp

# Höjs när tillämpningen av lappar i kärnan (eller i lapp.tillampa) ändras.
MATERIALISERING_VERSION = "2"
REPO_ROT = Path(__file__).resolve().parents[2]


class ScenarioFel(_K9ScenarioFel):
    """Fel när ett rutscenario byggs. Underklass till K9:s `ScenarioFel`."""


def mapp_hash(mapp: str | Path, *, utelamna: tuple[str, ...] = ()) -> str:
    """Innehållsnyckel för en katalog: sorterade relativa sökvägar och filernas sha256."""
    rot = Path(mapp)
    if not rot.is_dir():
        raise ScenarioFel(f"Katalogen finns inte: {rot}")
    poster = [
        [p.relative_to(rot).as_posix(), sha256_fil(p)]
        for p in sorted(rot.rglob("*"))
        if p.is_file() and p.relative_to(rot).as_posix() not in utelamna
    ]
    return kanonisk_hash({"mapp": poster})


@dataclass(frozen=True)
class Ruta:
    """En K1-ruta i ett scenario. `hash` är innehållshashen för rutans katalog."""

    ruta_id: str
    mapp: Path
    hash: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def avstand_m(self, annan: Ruta) -> float:
        """Kortaste avstånd mellan rutornas utsträckningar (0 om de överlappar)."""
        dx = max(0.0, max(annan.x_min - self.x_max, self.x_min - annan.x_max))
        dy = max(0.0, max(annan.y_min - self.y_max, self.y_min - annan.y_max))
        return float((dx * dx + dy * dy) ** 0.5)


@dataclass(frozen=True)
class RutScenario:
    """Ett materialiserat scenario. `nyckel` beror bara på rutornas innehåll."""

    id: str
    nyckel: str
    rutor: tuple[Ruta, ...]
    lappar: tuple[Path, ...] = ()

    def ruta(self, ruta_id: str) -> Ruta:
        for r in self.rutor:
            if r.ruta_id == ruta_id:
                return r
        raise ScenarioFel(f"Scenariot '{self.id}' saknar rutan '{ruta_id}'.")

    def grannar(self, ruta: Ruta, radie_m: float) -> tuple[Ruta, ...]:
        """Andra rutor som ligger närmare än `radie_m` från `ruta`.

        Radien 0 ger inga grannar, inte ens rutor som bara nuddar: då beror resultatet bara
        på rutan själv (som har 25 m överlapp enligt K1).
        """
        return tuple(
            r for r in self.rutor if r.ruta_id != ruta.ruta_id and ruta.avstand_m(r) < radie_m
        )


def _las_tiff_utstrackning(fil: Path) -> tuple[float, float, float, float]:
    """Utsträckning (x_min, y_min, x_max, y_max) för ett K1-GeoTIFF (okomprimerat eller deflate)."""
    from karna.geometri.rasterfil import GeometriFel, las_raster
    from testsvit.k1_io import K1FormatFel

    try:
        _, geo = las_raster(fil)
    except (K1FormatFel, GeometriFel) as exc:
        raise ScenarioFel(f"Kunde inte läsa georeferens i {fil}: {exc}") from exc
    return (geo.x_min, geo.y_min, geo.x_max, geo.y_max)


def _las_ruta(mapp: Path, ruta_id: str | None = None) -> Ruta:
    mark = mapp / "mark.tif"
    if not mark.is_file():
        raise ScenarioFel(f"K1-rutan saknar mark.tif: {mapp}")
    x0, y0, x1, y1 = _las_tiff_utstrackning(mark)
    return Ruta(
        ruta_id=ruta_id or mapp.name,
        mapp=mapp,
        hash=mapp_hash(mapp),
        x_min=x0,
        y_min=y0,
        x_max=x1,
        y_max=y1,
    )


def las_bas(bas: Path) -> tuple[Ruta, ...]:
    """Läser en bas: en enskild K1-ruta eller en katalog med K1-rutor."""
    if not bas.is_dir():
        raise ScenarioFel(f"Basen finns inte eller är inte en katalog: {bas}")
    if (bas / "mark.tif").is_file():
        return (_las_ruta(bas, ruta_id=_ruta_id_fran_recept(bas) or bas.name),)
    rutor = [
        _las_ruta(p) for p in sorted(bas.iterdir()) if p.is_dir() and (p / "mark.tif").is_file()
    ]
    if not rutor:
        raise ScenarioFel(f"Basen innehåller varken mark.tif eller K1-rutor: {bas}")
    return tuple(rutor)


def _ruta_id_fran_recept(mapp: Path) -> str | None:
    recept = mapp / "recept.json"
    if not recept.is_file():
        return None
    try:
        data = json.loads(recept.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rid = data.get("ruta_id") if isinstance(data, dict) else None
    return rid if isinstance(rid, str) and rid.strip() else None


@dataclass(frozen=True)
class ForberedLapp:
    """En inläst lapp med allt den läser utanför `lapp.json` upplöst och hashat."""

    lapp: ProjektLapp
    sektioner: dict[str, Gatusektion] | None
    nyckel: dict[str, Any]
    geometri: tuple[Andring, ...] = ()
    """K9-ändringar som tillämpas av `karna.geometri` på K1 v2-rutor (byggnader, vegetation)."""


def forbered_lapp(lapp_fil: Path) -> ForberedLapp:
    """Läser en K3-lapp och samlar allt som påverkar tillämpningen i en nyckel.

    LandXML-sökvägar tolkas relativt lappfilen (inte arbetskatalogen) och deras innehåll
    hashas. Gatusektioner läses ur repots `config.toml` och de som används hashas.
    """
    from lapp.kontrakt import las_lapp_k3

    return _forbered(las_lapp_k3(lapp_fil), lapp_fil, {"lapp_json": sha256_fil(lapp_fil)})


def _forbered(lapp: ProjektLapp, kalla: Path, kallnyckel: dict[str, Any]) -> ForberedLapp:
    from lapp.kontrakt import las_gatusektioner_fran_konfig

    lapp_fil = kalla
    ytor = []
    landxml: dict[str, str] = {}
    for yta in lapp.hojdytor:
        if yta.landxml is not None:
            p = Path(yta.landxml)
            p = p if p.is_absolute() else (lapp_fil.parent / p).resolve()
            if not p.is_file():
                raise ScenarioFel(f"{lapp_fil}: LandXML-filen finns inte: {p}")
            landxml[yta.yta_id] = sha256_fil(p)
            yta = replace(yta, landxml=str(p))
        ytor.append(yta)
    lapp = replace(lapp, hojdytor=tuple(ytor))

    sektioner: dict[str, Gatusektion] | None = None
    sektion_nyckel: dict[str, Any] = {}
    if lapp.gator:
        sektioner = las_gatusektioner_fran_konfig(REPO_ROT / "config.toml")
        for gata in lapp.gator:
            if gata.sektion not in sektioner:
                raise ScenarioFel(f"{lapp_fil}: okänd gatusektion '{gata.sektion}'.")
            sektion_nyckel[gata.sektion] = asdict(sektioner[gata.sektion])
    return ForberedLapp(
        lapp=lapp,
        sektioner=sektioner,
        nyckel={
            **kallnyckel,
            "landxml": landxml,
            "sektioner": sektion_nyckel,
        },
    )


def _tillampa_lapp_pa_ruta(ruta: Ruta, forberedd: ForberedLapp, ut: Path) -> bool:
    """Tillämpar en K3-lapp (och K9-geometri på v2-rutor) på en ruta, resultatet skrivs i `ut`.

    Returnerar False (och skriver inget) om lappen inte ändrar rutan.
    """
    from karna.geometri.lasa import k1_version

    if k1_version(ruta.mapp) >= 2:
        from karna.geometri.tillampa import tillampa_pa_v2_ruta

        recept = ruta.mapp / "recept.json"
        return tillampa_pa_v2_ruta(
            ruta.mapp,
            ut,
            lapp=forberedd.lapp,
            sektioner=forberedd.sektioner,
            geometri=forberedd.geometri,
            bas_recept_sha256=sha256_fil(recept) if recept.is_file() else ruta.hash,
        )
    if forberedd.geometri:
        raise ScenarioFel(f"Rutan {ruta.ruta_id} är K1 v1 men scenariot har geometriändringar.")
    from karna.geometri.rasterfil import las_raster as las_geotiff
    from lapp.tillampa import tillampa_lapp
    from sankgraf.typer import las_brytlinjer_fran_gpkg, las_oppningar_fran_gpkg
    from testsvit.k1_io import (
        skriv_brytlinjer_gpkg,
        skriv_geotiff,
        skriv_oppningar_gpkg,
    )

    lapp = forberedd.lapp
    mark_raw, geo = las_geotiff(ruta.mapp / "mark.tif")
    osak_raw, _ = las_geotiff(ruta.mapp / "osakerhet.tif")
    slag_raw, _ = las_geotiff(ruta.mapp / "markslag.tif")
    bryt = ruta.mapp / "brytlinjer.gpkg"
    opp = ruta.mapp / "oppningar.gpkg"
    bas_bryt = las_brytlinjer_fran_gpkg(bryt) if bryt.is_file() else []
    bas_opp = las_oppningar_fran_gpkg(opp) if opp.is_file() else []
    recept = ruta.mapp / "recept.json"
    bas_recept_sha = sha256_fil(recept) if recept.is_file() else ruta.hash

    res = tillampa_lapp(
        np.asarray(mark_raw, dtype=np.float32),
        np.asarray(osak_raw, dtype=np.float32),
        np.asarray(slag_raw, dtype=np.uint8),
        geo,
        lapp,
        bas_recept_sha256=bas_recept_sha,
        bas_brytlinjer=bas_bryt,
        bas_oppningar=bas_opp,
        sektioner=forberedd.sektioner,
    )
    oforandrad = (
        not bool(np.any(res.andrad_mask))
        and not res.kantstens_brytlinjer
        and len(res.oppningar) == len(bas_opp)
    )
    if oforandrad:
        return False

    ut.mkdir(parents=True, exist_ok=True)
    # Kopiera först allt från basen (t.ex. regn.csv), skriv sedan över det lappen ändrar.
    shutil.copytree(ruta.mapp, ut, dirs_exist_ok=True)
    bbox = (geo.x_min, geo.y_min, geo.x_max, geo.y_max)
    for namn, raster in (
        ("mark.tif", res.mark_z),
        ("osakerhet.tif", res.osakerhet_z),
        ("markslag.tif", res.markslag),
    ):
        skriv_geotiff(
            ut / namn, raster, x_min=geo.x_min, y_max=geo.y_max, pixelstorlek=geo.pixelstorlek_x
        )
    skriv_brytlinjer_gpkg(ut / "brytlinjer.gpkg", list(res.alla_brytlinjer), bbox_fallback=bbox)
    skriv_oppningar_gpkg(ut / "oppningar.gpkg", list(res.oppningar), bbox_fallback=bbox)
    (ut / "recept.json").write_text(
        json.dumps(res.recept_data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def _forbered_k9(spec: ScenarioSpec, rutor: list[Ruta]) -> ForberedLapp:
    """Översätter scenariots K9-kedja till en lapp och kontrollerar att basen stämmer."""
    from karna.k9_till_k1 import k9_till_lapp, las_k9_kedja

    for f in spec.k9:
        if not f.is_file():
            raise ScenarioFel(f"Scenario '{spec.id}': K9-filen finns inte: {f}")
    k9, andringar, k9_nyckel = las_k9_kedja(spec.k9)
    bas_recept = {
        sha256_fil(r.mapp / "recept.json") for r in rutor if (r.mapp / "recept.json").is_file()
    }
    saknas = sorted(set(k9.bas_recept) - bas_recept)
    if saknas:
        raise ScenarioFel(
            f"Scenario '{spec.id}': K9-scenariot '{k9.scenario_id}' bygger på recept som inte "
            f"finns i basen {spec.bas}: {[s[:12] for s in saknas]}. Fel bas eller ändrad bas?"
        )
    from karna.geometri.tillampa import (
        GEOMETRI_VERSION,
        valj_k1_version,
        verifiera_byggnadsreferenser,
    )
    from karna.k9_till_k1 import dela_andringar

    version = valj_k1_version([r.mapp for r in rutor])
    k3_andringar, geometri = dela_andringar(andringar, version, k9.scenario_id)
    if version >= 2:
        verifiera_byggnadsreferenser([r.mapp for r in rutor], geometri)
    kallnyckel: dict[str, Any] = {"k9": k9_nyckel}
    if geometri:
        kallnyckel["geometri"] = {
            "version": GEOMETRI_VERSION,
            "andringar": sorted((a.till_dict() for a in geometri), key=lambda d: d["andring_id"]),
        }
    forberedd = _forbered(k9_till_lapp(k3_andringar, k9), spec.k9[-1], kallnyckel)
    return replace(forberedd, geometri=geometri)


def materialisera_scenario(spec: ScenarioSpec, cache: ResultatCache) -> RutScenario:
    """Bygger scenariot: läser basen och tillämpar lapparna ruta för ruta, med cache."""
    from karna.geometri.lasa import k1_version
    from karna.geometri.tillampa import GEOMETRI_VERSION

    rutor = list(las_bas(spec.bas))
    steg: list[ForberedLapp] = []
    if spec.k9:
        steg.append(_forbered_k9(spec, rutor))
    for lapp_fil in spec.lappar:
        if not lapp_fil.is_file():
            raise ScenarioFel(f"Scenario '{spec.id}': lappen finns inte: {lapp_fil}")
        steg.append(forbered_lapp(lapp_fil))
    for forberedd in steg:
        nya: list[Ruta] = []
        for ruta in rutor:
            nyckel_data: dict[str, Any] = {
                "slag": "lapp_pa_ruta",
                "version": MATERIALISERING_VERSION,
                "ruta": ruta.hash,
                "lapp": forberedd.nyckel,
            }
            if k1_version(ruta.mapp) >= 2:
                nyckel_data["k1_v2"] = GEOMETRI_VERSION  # v2-rutor tillämpas av karna.geometri
            nyckel = kanonisk_hash(nyckel_data)

            def skrivare(
                tmp: Path, _ruta: Ruta = ruta, _lapp: ForberedLapp = forberedd
            ) -> dict[str, Any]:
                andrad = _tillampa_lapp_pa_ruta(_ruta, _lapp, tmp / "ruta")
                return {"andrad": andrad, "bas": _ruta.hash}

            post = cache.skapa("scenarier", nyckel, skrivare)
            meta = json.loads((post / ".klar").read_text(encoding="utf-8"))
            if meta.get("andrad"):
                ny = _las_ruta(post / "ruta", ruta_id=ruta.ruta_id)
                nya.append(ny)
            else:
                nya.append(ruta)
        rutor = nya
    rutor.sort(key=lambda r: r.ruta_id)
    nyckel = kanonisk_hash({"rutor": [[r.ruta_id, r.hash] for r in rutor]})
    return RutScenario(
        id=spec.id, nyckel=nyckel, rutor=tuple(rutor), lappar=(*spec.k9, *spec.lappar)
    )
