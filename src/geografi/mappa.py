# ruff: noqa: E501
"""`floden geografi mappa <id>`: hämta, bygg och förberäkna en geografi (kärna + buffert).

Kräver superadmin när inloggning är på (öppen fråga i STATUS.md: ska organisationens administratör få
göra det?). Data hämtas som i övrigt med kontots egen Lantmäteri-nyckel och kommunens öppna data enligt
kommunfilen. Bara öppna data används på servern: rådatalagret är alltid stängt för MCP och det härledda
är stängt tills villkoren kontrollerats (`--villkor-kontrollerade`).

Bufferten är den större av
- dagsljus: påverkansradien `hojd_max_m / tan(h_min_grader)` (5 grader), och
- skyfall: avståndet från kärnan till tillrinningsområdet uppströms (`geografi.tillrinning`).
Når tillrinningsområdet kanten av det som hämtats vidgas området (högst `max_buffert_m`); räcker inte
det är det en varning i manifestet.

Lager (innehållsadresserade, `geografi.modell`): `k1_rutnat` (K1 v2 per 500 m-ruta: DTM `mark.tif`,
DSM/terräng, byggnader, markklass) och `radata` (Lantmäteriets filer och kommunens byggnader). Sänkgrafen
förberäknas inte här: den bygges och cachas av kärnan (K22) vid första körningen.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from geografi.modell import (
    KONTRAKT_GEOGRAFI,
    GeografiFel,
    lagra,
    las_manifest,
    skriv_manifest,
    versionsmapp,
)
from geografi.tillrinning import nedsampla, tillrinning_uppstroms
from kommuner.konfig import _REPO_ROT, KommunKonfig
from kommuner.skapa import (
    SkapaFel,
    _oppna_register,
    _strangaste_licens,
    bbox_till_kommun,
    beraekna_yta,
    geojson_till_gpkg,
    skalfel_langd,
    skriv_omrade_gpkg,
    skriv_oppenhet_toml,
    terrangstatistik_mosaik,
)
from kommuner.wfs import Hamtare, KommunKallaFel, hamta_geojson, lista_lager, valj_lager

Skriv = Callable[[str], None]


@dataclass
class MappaResultat:
    ref: str
    manifest_fil: Path
    manifest_sha256: str
    nytt: bool
    varningar: list[str] = field(default_factory=list)
    oppen_for_mcp: bool = False


def _mosaik(k1: Path) -> tuple[np.ndarray[Any, Any], tuple[float, float, float], list[str]]:
    """Höjdmodellen för alla rutor som en matris: (z, (x_min, y_max, pixel), rutnamn)."""
    from testsvit.k1_io import las_geotiff

    delar: list[tuple[np.ndarray[Any, Any], Any, str]] = []
    for mark in sorted(k1.glob("mm_*/mark.tif")):
        z, geo = las_geotiff(mark)
        delar.append((np.asarray(z, dtype=np.float64), geo, mark.parent.name))
    if not delar:
        raise SkapaFel("Inga K1-rutor byggdes: ingen laser i området?")
    px = delar[0][1].pixelstorlek_x
    x0 = min(g.x_min for _, g, _ in delar)
    y1 = max(g.y_max for _, g, _ in delar)
    x1 = max(g.x_min + g.bredd * px for _, g, _ in delar)
    y0 = min(g.y_max - g.hojd * px for _, g, _ in delar)
    full = np.full((int(round((y1 - y0) / px)), int(round((x1 - x0) / px))), np.nan)
    for z, g, _ in delar:
        c, r = int(round((g.x_min - x0) / px)), int(round((y1 - g.y_max) / px))
        blk = np.where(z > -9000.0, z, np.nan)
        full[r : r + z.shape[0], c : c + z.shape[1]] = blk
    return full, (x0, y1, px), [n for _, _, n in delar]


def _tillrinning_for_kärna(
    k1: Path, karna: tuple[float, float, float, float], cell_m: float,
    extent: tuple[float, float, float, float],
) -> dict[str, Any]:
    z, (x0, y1, px), rutor = _mosaik(k1)
    # Bara det som hämtats (kärna + buffert) räknas: rutorna är större än området, och tillrinningen ska
    # prövas mot områdets kant, inte mot rutnätets.
    c0, r0 = max(0, int(round((extent[0] - x0) / px))), max(0, int(round((y1 - extent[3]) / px)))
    c1, r1 = int(round((extent[2] - x0) / px)), int(round((y1 - extent[1]) / px))
    z = z[r0:r1, c0:c1]
    x0, y1 = x0 + c0 * px, y1 - r0 * px
    faktor = max(1, int(round(cell_m / px)))
    zz = nedsampla(z, faktor)
    p = px * faktor
    h, b = zz.shape
    kx0, ky0, kx1, ky1 = karna
    kc0, kc1 = int(math.floor((kx0 - x0) / p)), int(math.ceil((kx1 - x0) / p))
    kr0, kr1 = int(math.floor((y1 - ky1) / p)), int(math.ceil((y1 - ky0) / p))
    maske = np.zeros((h, b), dtype=bool)
    maske[max(kr0, 0) : min(kr1, h), max(kc0, 0) : min(kc1, b)] = True
    if not maske.any():
        raise SkapaFel("Kärnan ligger utanför höjdmodellen.")
    t = tillrinning_uppstroms(zz, maske)
    rr, cc = np.nonzero(t.maske)
    # Avstånd (m) från kärnans kant till tillrinningsområdets yttersta cell, per sida.
    ut_vast = max(0.0, kx0 - (x0 + cc.min() * p))
    ut_ost = max(0.0, (x0 + (cc.max() + 1) * p) - kx1)
    ut_nord = max(0.0, (y1 - rr.min() * p) - ky1)
    ut_syd = max(0.0, ky0 - (y1 - (rr.max() + 1) * p))
    return {
        "cell_m": p,
        "celler": t.antal_celler,
        "nar_kanten": t.nar_kanten,
        "krav_buffert_m": round(max(ut_vast, ut_ost, ut_nord, ut_syd), 1),
        "yta_m2": round(t.antal_celler * p * p, 1),
        "rutor": rutor,
    }


def _hamta_api_nyckel(
    typ_id: str,
    *,
    explicit_nyckel: str | None,
    konto_id: str | None,
    konfig: Mapping[str, Any],
    konfig_fil: Path,
    register: Any,
    nyckel_forrad: Any,
) -> tuple[str | None, str | None]:
    """Läser API-nyckel (`trafikverket` eller `artdatabanken`) ur explicit parameter, kontots
    krypterade `NyckelForrad` eller (lokalt) miljö/`config.local.toml`. Läckagesäker (0 värden i varning)."""
    from lantmateriet.nycklar import inloggning_pa

    if explicit_nyckel is not None and explicit_nyckel.strip():
        return explicit_nyckel.strip(), None

    if nyckel_forrad is not None and konto_id:
        if not getattr(nyckel_forrad, "paslagen", True):
            return None, f"Nyckelhanteringen är avstängd på servern; kan inte läsa '{typ_id}'-nyckel."
        try:
            varden = nyckel_forrad.las(konto_id, typ_id)
        except Exception as exc:  # noqa: BLE001 - texten är fri från nyckelvärden
            return None, str(getattr(exc, "text", f"Nyckeln '{typ_id}' kunde inte läsas."))
        if isinstance(varden, Mapping) and varden.get("nyckel"):
            return str(varden["nyckel"]).strip(), None
        return None, None

    if inloggning_pa(konfig):
        if not konto_id:
            return None, None
        from konto.nycklar import NyckelForrad

        reg = register if register is not None else _oppna_register(konfig, konfig_fil.parent)
        forrad = NyckelForrad(reg)
        if not forrad.paslagen:
            return None, forrad.avstangd_text()
        try:
            varden = forrad.las(konto_id, typ_id)
        except Exception as exc:  # noqa: BLE001 - texten är fri från nyckelvärden
            return None, str(getattr(exc, "text", f"Nyckeln '{typ_id}' kunde inte läsas."))
        if isinstance(varden, Mapping) and varden.get("nyckel"):
            return str(varden["nyckel"]).strip(), None
        return None, None

    env_map = {
        "trafikverket": "TRAFIKVERKET_API_NYCKEL",
        "artdatabanken": "ARTDATABANKEN_API_NYCKEL",
    }
    env_namn = env_map.get(typ_id)
    if env_namn and os.environ.get(env_namn, "").strip():
        return os.environ[env_namn].strip(), None
    avsnitt = konfig.get(typ_id)
    if isinstance(avsnitt, Mapping) and isinstance(avsnitt.get("nyckel"), str) and avsnitt["nyckel"].strip():
        return avsnitt["nyckel"].strip(), None
    return None, None


def _lankar_fran_gator_gpkg(
    gator_fil: Path,
    bbox_3011: tuple[float, float, float, float],
) -> list[Any]:
    """Bygger klickbara `TrafikLank`-linjer ur baskartans `gator.gpkg` utan påhittat ÅDT."""
    import geopandas as gpd
    import shapely
    from shapely.geometry import LineString, box

    from data_inlasning.trafikverket import TrafikLank

    try:
        gdf = gpd.read_file(gator_fil, layer="gator")
    except Exception:  # noqa: BLE001 - fallback till första lagret
        gdf = gpd.read_file(gator_fil)
    if gdf.empty:
        return []
    if gdf.crs is not None and gdf.crs.to_epsg() != 3011:
        gdf = gdf.to_crs(epsg=3011)
    if "kategori" in gdf.columns:
        gdf = gdf[gdf["kategori"].astype(str).str.lower() == "gata"]
    klipp = box(*bbox_3011)
    linje_koordinater: list[tuple[tuple[float, float], ...]] = []
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        snitt = geom.intersection(klipp)
        if snitt.is_empty:
            continue
        for del_g in shapely.get_parts(snitt):
            if isinstance(del_g, LineString) and float(del_g.length) > 1.0e-3:
                pts = tuple((round(float(c[0]), 3), round(float(c[1]), 3)) for c in del_g.coords)
                if len(pts) >= 2:
                    linje_koordinater.append(pts)
    linje_koordinater.sort()
    return [
        TrafikLank(
            lank_id=f"baskarta_gata_{idx:04d}",
            koordinater_3011=pts,
            gatunamn=None,
            vagnummer=None,
            vaghallare="kommunal",
            hastighet_km_h=None,
            korfalt=None,
            adt_fordon_dygn=None,
            andel_tung_pct=None,
            matar=None,
            adt_kalla=None,
            osaker=False,
        )
        for idx, pts in enumerate(linje_koordinater, start=1)
    ]


def _sammanfatta_vmi_fil(
    vmi_fil: Path,
    karna_3011: tuple[float, float, float, float],
    *,
    buffert_m: float = 1000.0,
) -> dict[str, Any]:
    """Beräknar deterministisk VMI-sammanfattning (antal polygoner, överlapp, minsta avstånd) mot kärnan."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.read_file(vmi_fil)
    if gdf.empty:
        return {
            "tillganglig": True,
            "antal_polygoner": 0,
            "overlapp_karna_m2": 0.0,
            "minsta_avstand_m": None,
        }
    if gdf.crs is not None and gdf.crs.to_epsg() != 3011:
        gdf = gdf.to_crs(epsg=3011)
    e0, n0, e1, n1 = karna_3011
    karna_box = box(e0, n0, e1, n1)
    sok_box = box(e0 - buffert_m, n0 - buffert_m, e1 + buffert_m, n1 + buffert_m)
    antal = 0
    overlapp_m2 = 0.0
    min_avst: float | None = None
    for geom in gdf.geometry:
        if geom is None or geom.is_empty or not geom.intersects(sok_box):
            continue
        antal += 1
        if geom.intersects(karna_box):
            overlapp_m2 += float(geom.intersection(karna_box).area)
            min_avst = 0.0
        else:
            avst = float(geom.distance(karna_box))
            if min_avst is None or avst < min_avst:
                min_avst = avst
    return {
        "tillganglig": True,
        "antal_polygoner": antal,
        "overlapp_karna_m2": round(overlapp_m2, 2),
        "minsta_avstand_m": None if min_avst is None else round(min_avst, 2),
    }


def mappa_geografi(
    kommun: KommunKonfig,
    geografi_id: str,
    *,
    konto_id: str | None,
    geo_rot: Path,
    konfig: Mapping[str, Any],
    konfig_fil: Path,
    version: str = "v1",
    villkor_kontrollerade: bool = False,
    byggnadslager: str | None = None,
    byggnader_kalla: str | None = None,
    skriv: Skriv = print,
    hamta_bytes: Hamtare | None = None,
    filhamtare: Any = None,
    hojdmodell_urlar: Sequence[str] = (),
    hojdmodell_filer: Sequence[Path] = (),
    nycklar_fran: Callable[[str | None], Any] | None = None,
    klient_fabrik: Callable[[Any], Any] | None = None,
    register: Any = None,
    nmd_fil: Path | None = None,
    vmi_fil: Path | None = None,
    trad_fil: Path | None = None,
    trad_natverk: str = "adellov-nacka",
    nmd_troskel_m: float = 3000.0,
    baskarta_filer: Sequence[Path] = (),
    dwgread: str | None = None,
    nan_varning_andel: float = 0.20,
    forberaknad_lager: Any | None = None,
    trafik_fil: Path | None = None,
    hamta_trafikverket: bool = False,
    trafik_fran_baskarta: bool = False,
    hamta_vader_luft: bool = False,
    luft_bakgrund_fil: Path | None = None,
    hamta_skyddad_natur: bool = False,
    vmi_lan: str | None = None,
    hamta_artfynd: bool = False,
    nyckel_forrad: Any | None = None,
    trafikverket_nyckel: str | None = None,
    trafikverket_hamtare: Any | None = None,
    vader_hamtare: Any | None = None,
    luft_hamtare: Any | None = None,
    skyddad_natur_hamtare: Any | None = None,
    skogsstyrelsen_hamtare: Any | None = None,
    vmi_hamtare: Any | None = None,
    artdatabanken_nyckel: str | None = None,
    artdatabanken_hamtare: Any | None = None,
) -> MappaResultat:
    from karna.paverkan import dagsljus_paverkansradie_m
    from kommuner.baskarta import (
        brytlinjer_till_gpkg,
        byggnader_ur_lokala_filer,
        gator_till_gpkg,
        tak_till_gpkg,
        trad_fran_baskarta,
    )
    from kommuner.filkallor import hamta_baskarta, hamta_hojdmodell, hamta_nmd
    from kommuner.hojdmodell import Hojdmodell, skriv_klippt_hojd
    from kommuner.k1_filpaket import bygg_omrade_filpaket
    from lantmateriet.bygg import bygg_omrade
    from lantmateriet.hamta import hamta_omrade
    from lantmateriet.klient import LmKlient, Tjanster
    from lantmateriet.nycklar import inloggning_pa, las_nycklar_for_konto
    from lantmateriet.omrade import las_omrade

    gdef = kommun.geografier.get(geografi_id)
    if gdef is None:
        raise SkapaFel(f"Kommunen {kommun.id} har ingen geografi '{geografi_id}'.")
    omrade = kommun.omrade(gdef.omrade)
    ref = f"{geografi_id}@{version}"
    yta = beraekna_yta(kommun, omrade)
    karna = yta.studie_3011
    skriv(f"Geografi {ref}: {gdef.namn}")
    skriv(
        f"Koordinater: kommunen publicerar i EPSG:{kommun.publicering_epsg}, Floden räknar i EPSG:3011. "
        f"Skalfel i området: längder {yta.skalfel_procent:.2f} % längre, ytor ca "
        f"{(yta.skalfaktor_langd**2 - 1) * 100:.2f} % större i EPSG:3011."
    )

    # 1. Behörighet: superadmin (när inloggning är på).
    reg = register
    if inloggning_pa(konfig):
        if not konto_id:
            raise SkapaFel("Inloggning är på: ange --konto <konto-id> (ett superadmin-konto).")
        reg = reg if reg is not None else _oppna_register(konfig, konfig_fil.parent)
        if not reg.ar_superadmin(konto_id):
            raise SkapaFel("Att mappa en geografi kräver superadmin. Kontot är inte det (eller finns inte).")
    else:
        skriv("Inloggning är av: ingen behörighetskontroll (lokal körning).")

    # 2. Finns versionen redan? Då är mappningen klar och oförändrad.
    if (versionsmapp(geo_rot, geografi_id, version) / "manifest.json").is_file():
        d, sha = las_manifest(geo_rot, ref)
        skriv(f"{ref} finns redan och stämmer (manifest sha256 {sha[:12]}). Inget att göra.")
        return MappaResultat(
            ref, versionsmapp(geo_rot, geografi_id, version) / "manifest.json", sha, False,
            list(d.get("varningar", [])), bool(d.get("oppen_for_mcp")),
        )  # fmt: skip

    for _f, _n in (
        (nmd_fil, "NMD"),
        (vmi_fil, "VMI"),
        (trad_fil, "Träd"),
        (trafik_fil, "Trafik"),
        (luft_bakgrund_fil, "Bakgrundsluft"),
    ):
        if _f is not None and not Path(_f).is_file():
            raise SkapaFel(f"{_n}-filen {_f} finns inte.")
    # 3. Höjdkälla. Kommunens filpaket kräver ingen nyckel; Lantmäteriets laser kräver kontots egen nyckel
    # (och är reserven om kommunens fil fallerar). Nyckeln kontrolleras före all hämtning när den behövs.
    fil_hojd = kommun.hojd.kalla == "filpaket"
    kalla_bygg = byggnader_kalla or kommun.byggnader.kalla
    klient: Any = None
    try:
        # Byggnader från Lantmäteriet (Byggnad Nedladdning, vektor, avgiftsfri) kräver kontots nyckel även när
        # höjden kommer ur kommunens fil.
        if not fil_hojd or kommun.hojd.reserv_lantmateriet or kalla_bygg == "lantmateriet":
            nycklar = nycklar_fran(konto_id) if nycklar_fran else las_nycklar_for_konto(konto_id, konfig=konfig)
            klient = (klient_fabrik or (lambda n: LmKlient(n, Tjanster.fran_miljo())))(nycklar)
    except Exception as exc:
        if not fil_hojd or kalla_bygg == "lantmateriet":
            raise
        skriv(f"Ingen Lantmäteri-nyckel ({exc}); reserven används inte. Kommunens höjdmodell används.")
    if fil_hojd:
        skriv("Höjd: kommunens höjdmodell (filpaket, öppna data, ingen nyckel).")

    h_min = float(kommun.dagsljus["h_min_grader"])
    hojd_max = float(kommun.dagsljus["hojd_max_m"])
    r_dagsljus = dagsljus_paverkansradie_m(hojd_max, h_min)
    # Baskartan som lokal fil är INTE öppna data (docs/BESLUT.md 2026-11-24): byggnaderna och allt som härleds direkt
    # ur dem är stängt för MCP, vad `--villkor-kontrollerade` än säger.
    stangd_baskarta = kalla_bygg == "lokal_fil"
    if stangd_baskarta:
        lk = kommun.byggnader.lokal
        if lk is None:
            raise SkapaFel("Kommunfilen saknar [byggnader.lokal] för byggnader ur en lokal fil.")
        if not baskarta_filer:
            raise SkapaFel(
                "Byggnaderna kommer ur en lokal baskartefil som inte följer med Floden: ange den med "
                "--baskarta-fil <sökväg> (ZIP, DWG eller dwgread-JSON; kan anges flera gånger), i containern "
                "under /app/data/lokal/."
            )
        for nr, f in enumerate(baskarta_filer, start=1):
            if not Path(f).is_file():
                raise SkapaFel(f"Baskartefil {nr} finns inte (kontrollera sökvägen, i containern t.ex. under /app/data/lokal/).")
        if villkor_kontrollerade:
            skriv("--villkor-kontrollerade gäller inte byggnaderna ur den lokala baskartan: de förblir stängda för MCP.")
        if kommun.hojd.kalla == "filpaket":
            raise SkapaFel("Byggnader ur en lokal baskartefil kräver höjd från Lantmäteriets laser ([hojd] kalla = lantmateriet).")
    elif baskarta_filer:
        raise SkapaFel("--baskarta-fil hör till byggnader ur en lokal fil (kalla = lokal_fil i kommunfilen).")
    bygge = geo_rot / geografi_id / f".bygge-{version}"
    varningar: list[str] = []
    hojd_anvand = "lantmateriet"
    buffert = math.ceil(r_dagsljus)
    skriv(f"Buffert (dagsljus): {r_dagsljus:.0f} m = {hojd_max:g} / tan({h_min:g} grader).")

    # Automatiskt öppet läns-NMD (CC0) ur [filpaket.nmd] vid filpaket-mappning om användaren inte angav --nmd.
    nmd_paket: Any = None
    if fil_hojd and nmd_fil is None and kommun.filpaket is not None and kommun.filpaket.nmd is not None:
        nmd_urlar = kommun.filpaket.nmd.urlar
        mock_saknar_nmd = (
            filhamtare is not None
            and hasattr(filhamtare, "filer")
            and isinstance(filhamtare.filer, Mapping)
            and bool(nmd_urlar)
            and nmd_urlar[0] not in filhamtare.filer
        )
        if mock_saknar_nmd:
            varningar.append("NMD (CC0) hoppades över: filhämtaren tillhandahåller inte NMD-paketet.")
        else:
            try:
                nmd_fil, nmd_paket = hamta_nmd(
                    kommun, geo_rot / "_filcache", hamtare=filhamtare, hamta_bytes=hamta_bytes, skriv=skriv
                )
            except (KommunKallaFel, OSError, KeyError) as exc:
                varningar.append(f"NMD (CC0) kunde inte hämtas ({exc}); fortsätter utan NMD.")
                skriv(f"VARNING: {varningar[-1]}")

    for _varv in range(4):
        extent = (karna[0] - buffert, karna[1] - buffert, karna[2] + buffert, karna[3] + buffert)
        extent = tuple(float(math.floor(v)) if i < 2 else float(math.ceil(v)) for i, v in enumerate(extent))  # type: ignore[assignment]
        ra = bygge / "radata"
        k1 = bygge / "k1_rutnat"
        if k1.exists():
            shutil.rmtree(k1)
        omr = las_omrade(
            skriv_omrade_gpkg(extent, ra / "omrade" / "geografi.gpkg"),
            max_km2=max(25.0, (extent[2] - extent[0]) * (extent[3] - extent[1]) / 1e6 + 1.0),
        )
        wfs_bbox = bbox_till_kommun(kommun.publicering_epsg, extent)
        by = kommun.byggnader
        bygg_fil: Path | None = None
        bygg: Any = None
        vatten_info: dict[str, Any] | None = None

        def _wfs_byggnader(ra: Path = ra, wfs_bbox: tuple[float, float, float, float] = wfs_bbox, by: Any = by) -> Path:
            lager = byggnadslager or by.lager
            if not lager:
                lager = valj_lager(
                    by.lager_monster, lista_lager(by.wfs_url, by.tillatna_varddomaner, hamta_bytes=hamta_bytes)
                )
            skriv(f"Hämtar byggnader ur {kommun.namn}s WFS, lager '{lager}' ...")
            fc = hamta_geojson(
                by.wfs_url, lager, wfs_bbox, kommun.publicering_epsg, by.tillatna_varddomaner,
                max_objekt=by.max_objekt, hamta_bytes=hamta_bytes,
            )  # fmt: skip
            fil, antal, v = geojson_till_gpkg(
                fc, epsg_antagen=kommun.publicering_epsg, ut=ra / "kommun" / "byggnader.gpkg",
                forvantad_bbox=wfs_bbox, falt=by.falt, lagernamn=lager,
            )  # fmt: skip
            varningar.extend(x for x in v if x not in varningar)
            skriv(f"Byggnader: {antal} polygoner sparade (bara attribut ur kommunfilen).")
            return fil

        for namn in kommun.baskarta.lager:  # valfria WFS-lager (tom lista = ingen WFS)
            fc = hamta_geojson(
                by.wfs_url, namn, wfs_bbox, kommun.publicering_epsg, by.tillatna_varddomaner,
                max_objekt=by.max_objekt, hamta_bytes=hamta_bytes,
            )  # fmt: skip
            geojson_till_gpkg(
                fc, epsg_antagen=kommun.publicering_epsg,
                ut=ra / "kommun" / f"baskarta_{namn.replace(':', '_')}.gpkg", forvantad_bbox=wfs_bbox,
                falt={}, lagernamn=namn, kravs_objekt=False,
            )  # fmt: skip

        def _baskarta_byggnader(ra: Path = ra, wfs_bbox: tuple[float, float, float, float] = wfs_bbox) -> tuple[Path, list[Any]]:
            fk = kommun.filpaket
            assert fk is not None and fk.baskarta is not None
            bpaket = hamta_baskarta(kommun, geo_rot / "_filcache", hamtare=filhamtare, hamta_bytes=hamta_bytes, skriv=skriv)
            fil, antal, v = tak_till_gpkg(
                [q.fil for q in bpaket], bbox_publicering=wfs_bbox, epsg_publicering=fk.baskarta.epsg,
                lager_monster=fk.baskarta.lager_monster, ut=ra / "kommun" / "byggnader.gpkg",
                objekt_falt=fk.baskarta.objekt_falt, objekt_monster=fk.baskarta.objekt_monster,
            )  # fmt: skip
            varningar.extend(x for x in v if x not in varningar)
            skriv(f"Byggnader: {antal} takkonturer ur baskartan.")
            return fil, bpaket

        def _lokal_byggnader(ra: Path = ra, wfs_bbox: tuple[float, float, float, float] = wfs_bbox) -> Path:
            from gemensamt.hash import sha256_fil
            from markmodell.lagerprofil import las_lagerprofil

            assert kommun.byggnader.lokal is not None
            lk = kommun.byggnader.lokal
            profil = las_lagerprofil(_REPO_ROT / "data" / "lagerprofiler" / f"{lk.lagerprofil}.toml")
            skriv(f"Byggnader ur lokal baskarta ({len(baskarta_filer)} fil(er); namn och lager skrivs inte) ...")
            fil, antal, v = byggnader_ur_lokala_filer(
                list(baskarta_filer), bbox_publicering=wfs_bbox, epsg_publicering=lk.epsg, lagerprofil=profil,
                ut=ra / "kommun" / "byggnader.gpkg", lager_monster=lk.lager_monster, hojd_fran=lk.hojd_fran,
                z_tillagg_m=lk.z_tillagg_m, dwgread=dwgread,
            )  # fmt: skip
            varningar.extend(x for x in v if x not in varningar)
            (ra / "filpaket").mkdir(parents=True, exist_ok=True)
            (ra / "filpaket" / "kallor.json").write_text(
                json.dumps(
                    {"baskarta_lokal": [{"nr": i, "sha256": sha256_fil(f)} for i, f in enumerate(baskarta_filer, start=1)],
                     "lagerprofil_sha256": profil.sha256},
                    indent=2, sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            skriv(f"Byggnader: {antal} byggnadsytor ur baskartan (stängda för MCP).")
            return fil

        def _lm_byggnader(ra: Path = ra, omr: Any = omr) -> Path | None:
            """Byggnad Nedladdning, vektor ur Lantmäteriet, klippt till området (bara geometri och höjd/våningar)."""
            skriv("Hämtar Lantmäteriets byggnader (Byggnad Nedladdning, vektor; kontots egen nyckel) ...")
            lm_b = hamta_omrade(
                klient, omr, ra / "lantmateriet", laser=False, byggnader=True,
                marginal_m=kommun.lantmateriet.marginal_m, skriv=skriv,
            )  # fmt: skip
            varningar.extend(v for v in lm_b.varningar if v not in varningar)
            return lm_b.byggnader_klippt

        if fil_hojd:
            try:
                cache = geo_rot / "_filcache"
                fk = kommun.filpaket
                assert fk is not None
                # K1-rutorna snäpps utåt till hela 500 m-rutor (`rutor_for_omrade(..., 500.0)`),
                # så höjdmodellen hämtas och klipps mot samma 500 m-rutnät + 5 m marginal när
                # paketen täcker det (annars faller vi tillbaka till `extent`).
                _rutsteg_m = 500.0
                _hojd_marg_m = 5.0
                hojd_extent = (
                    math.floor(extent[0] / _rutsteg_m) * _rutsteg_m - _hojd_marg_m,
                    math.floor(extent[1] / _rutsteg_m) * _rutsteg_m - _hojd_marg_m,
                    math.ceil(extent[2] / _rutsteg_m) * _rutsteg_m + _hojd_marg_m,
                    math.ceil(extent[3] / _rutsteg_m) * _rutsteg_m + _hojd_marg_m,
                )
                klipp_extent = hojd_extent
                try:
                    hoj, hpaket = hamta_hojdmodell(
                        kommun, cache, hojd_extent, extra_urlar=hojdmodell_urlar, extra_filer=hojdmodell_filer,
                        hamtare=filhamtare, hamta_bytes=hamta_bytes, skriv=skriv,
                    )  # fmt: skip
                except KommunKallaFel:
                    klipp_extent = extent
                    hoj, hpaket = hamta_hojdmodell(
                        kommun, cache, extent, extra_urlar=hojdmodell_urlar, extra_filer=hojdmodell_filer,
                        hamtare=filhamtare, hamta_bytes=hamta_bytes, skriv=skriv,
                    )  # fmt: skip
                bpaket: list[Any] = []
                if kalla_bygg == "filpaket":
                    try:
                        bygg_fil, bpaket = _baskarta_byggnader()
                    except KommunKallaFel as exc:
                        # Höjden är redan hämtad: ett fel i baskartan är inte ett höjdfel och ska inte
                        # leda till Lantmäteriets laser (som inte ger några byggnader här).
                        raise SkapaFel(
                            f"Kommunens baskarta (byggnader) kunde inte användas: {exc} Höjdmodellen hämtades "
                            "och ligger kvar i cachen. Kontrollera adressen i [filpaket.baskarta] eller välj "
                            "--byggnader-kalla wfs."
                        ) from exc
                elif kalla_bygg == "wfs":
                    bygg_fil = _wfs_byggnader()
                elif kalla_bygg == "lantmateriet":
                    bygg_fil = _lm_byggnader()
                strandlinjer: list[Any] = []
                barriarlinjer: list[Any] = []
                vk = fk.baskarta.vatten if fk.baskarta is not None else None
                if vk is not None:
                    from kommuner.vattenytor import (
                        las_barriarlinjer,
                        las_strandlinjer,
                        skriv_strandlinjer,
                    )

                    if not bpaket:
                        bpaket = hamta_baskarta(kommun, cache, hamtare=filhamtare, hamta_bytes=hamta_bytes, skriv=skriv)
                    v_bbox = tuple(m + s * 50.0 for m, s in zip(wfs_bbox, (-1, -1, 1, 1), strict=True))
                    try:
                        strandlinjer, vv = las_strandlinjer(
                            [q.fil for q in bpaket], vk, bbox_publicering=v_bbox,  # type: ignore[arg-type]
                            epsg_publicering=fk.baskarta.epsg,  # type: ignore[union-attr]
                        )
                        barriarlinjer = las_barriarlinjer(
                            [q.fil for q in bpaket], vk, bbox_publicering=v_bbox,  # type: ignore[arg-type]
                            epsg_publicering=fk.baskarta.epsg,  # type: ignore[union-attr]
                        )
                    except KommunKallaFel as exc:
                        raise SkapaFel(f"Baskartans strandlinjer kunde inte läsas: {exc}") from exc
                    varningar.extend(x for x in vv if x not in varningar)
                    skriv(f"Strandlinjer: {len(strandlinjer)} ur baskartan.")
                    if strandlinjer:
                        skriv_strandlinjer(strandlinjer, ra / "kommun" / "strandlinjer.gpkg")

                brytlinjer: list[Any] = []
                gator_fil: Path | None = None
                trad_punkter: list[tuple[float, float, str]] = []
                trad_kronradie_m = 3.5
                trad_hojd_m = 8.0
                if fk.baskarta is not None and (fk.baskarta.brytlinjer or fk.baskarta.vektorbakgrund):
                    if not bpaket:
                        bpaket = hamta_baskarta(kommun, cache, hamtare=filhamtare, hamta_bytes=hamta_bytes, skriv=skriv)
                    if fk.baskarta.brytlinjer:
                        _, brytlinjer, bv = brytlinjer_till_gpkg(
                            [q.fil for q in bpaket],
                            regler=fk.baskarta.brytlinjer,
                            bbox_publicering=wfs_bbox,
                            epsg_publicering=fk.baskarta.epsg,
                            ut=ra / "kommun" / "brytlinjer.gpkg",
                        )
                        varningar.extend(x for x in bv if x not in varningar)
                        skriv(f"Brytlinjer: {len(brytlinjer)} ur baskartan.")
                    vb = fk.baskarta.vektorbakgrund
                    if vb is not None:
                        if vb.gator:
                            gator_fil, n_gator, gv = gator_till_gpkg(
                                [q.fil for q in bpaket],
                                regler=vb.gator,
                                bbox_publicering=wfs_bbox,
                                epsg_publicering=fk.baskarta.epsg,
                                ut=ra / "kommun" / "gator.gpkg",
                            )
                            varningar.extend(x for x in gv if x not in varningar)
                            skriv(f"Gatuvektorer: {n_gator} linjer ur baskartan.")
                        if vb.trad is not None:
                            trad_punkter, tv = trad_fran_baskarta(
                                [q.fil for q in bpaket],
                                trad_konfig=vb.trad,
                                bbox_publicering=wfs_bbox,
                                epsg_publicering=fk.baskarta.epsg,
                            )
                            trad_kronradie_m = vb.trad.kronradie_m
                            trad_hojd_m = vb.trad.antagen_hojd_m
                            varningar.extend(x for x in tv if x not in varningar)
                            if trad_punkter:
                                skriv(f"Träd: {len(trad_punkter)} punkter ur baskartan (antagen höjd {trad_hojd_m:g} m).")

                klippt = ra / "hojd" / "hojdmodell_klippt.tif"
                varningar.extend(
                    x
                    for x in skriv_klippt_hojd(
                        hoj,
                        klipp_extent,
                        kommun.lantmateriet.upplosning_m,
                        klippt,
                        nan_varning_andel=nan_varning_andel,
                    )
                    if x not in varningar
                )
                (ra / "filpaket").mkdir(parents=True, exist_ok=True)
                kallor_fp: dict[str, Any] = {
                    "hojdmodell": [{"url": q.url, "fil": q.namn, "sha256": q.sha256} for q in hpaket],
                    "baskarta": [{"url": q.url, "fil": q.namn, "sha256": q.sha256} for q in bpaket],
                }
                if nmd_paket is not None:
                    kallor_fp["nmd"] = [{"url": nmd_paket.url, "fil": nmd_paket.namn, "sha256": nmd_paket.sha256}]
                (ra / "filpaket" / "kallor.json").write_text(
                    json.dumps(kallor_fp, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                hojd_anvand = "filpaket"
                skriv("Bygger K1-markmodellen ur kommunens höjdmodell (rutor om 500 m) ...")
                bygg = bygg_omrade_filpaket(
                    omr,
                    k1,
                    hojdmodell=Hojdmodell(
                        [klippt],
                        epsg_antagen=3011,
                        fyll_smahal=False,
                        nan_varning_andel=nan_varning_andel,
                    ),
                    hojd_paket=hpaket,
                    byggnader_fil=bygg_fil,
                    byggnader_paket=bpaket,
                    upplosning_m=kommun.lantmateriet.upplosning_m,
                    skriv=skriv,
                    strandlinjer=strandlinjer,
                    vatten_barriarer=barriarlinjer,
                    vatten_marginal_m=vk.marginal_m if vk else 0.2,
                    vatten_max_avstand_m=vk.max_avstand_m if vk else 2.0,
                    nan_varning_andel=nan_varning_andel,
                    brytlinjer=brytlinjer,
                    nmd_fil=nmd_fil,
                    trad_punkter=trad_punkter,
                    trad_kronradie_m=trad_kronradie_m,
                    trad_hojd_m=trad_hojd_m,
                )
                if gator_fil is not None and gator_fil.is_file():
                    shutil.copy2(gator_fil, k1 / "gator.gpkg")
                vatten_info = (
                    {"m2": round(bygg.vatten_m2, 1), "niva_m_rh2000": list(bygg.vatten_niva_m) if bygg.vatten_niva_m else None,
                     "strandlinjer": len(strandlinjer), "marginal_m": vk.marginal_m, "max_avstand_m": vk.max_avstand_m}
                    if vk is not None else None
                )  # fmt: skip
            except KommunKallaFel as exc:
                if klient is None:
                    raise SkapaFel(
                        f"Kommunens höjdmodell kunde inte användas: {exc} Ingen Lantmäteri-nyckel finns som reserv "
                        "([hojd] reserv_lantmateriet) eller den saknas på sidan Nycklar."
                    ) from exc
                varningar.append(f"Kommunens höjdmodell kunde inte användas ({exc}); reserv: Lantmäteriets laser.")
                skriv(varningar[-1])
                if k1.exists():
                    shutil.rmtree(k1)
        if bygg is None:
            if klient is None:
                raise SkapaFel("Ingen höjdkälla: kommunens höjdmodell gav inget och ingen Lantmäteri-nyckel finns.")
            hojd_anvand = "lantmateriet"
            skriv(f"Hämtar Lantmäteriets laser (kontots egen nyckel), område {extent[2]-extent[0]:.0f} x {extent[3]-extent[1]:.0f} m ...")
            lm = hamta_omrade(
                klient, omr, ra / "lantmateriet", laser=True,
                orto="orto" in kommun.lantmateriet.produkter,
                byggnader=kalla_bygg == "lantmateriet",
                marginal_m=kommun.lantmateriet.marginal_m, skriv=skriv,
            )  # fmt: skip
            varningar.extend(v for v in lm.varningar if v not in varningar)
            if kalla_bygg == "wfs":
                bygg_fil = _wfs_byggnader()
            elif kalla_bygg == "filpaket":
                bygg_fil, _ = _baskarta_byggnader()
            elif kalla_bygg == "lokal_fil":
                bygg_fil = _lokal_byggnader()
            skriv("Bygger K1-markmodellen (rutor om 500 m) ...")
            bygg = bygg_omrade(
                omr, ra / "lantmateriet", k1, byggnader_fil=bygg_fil, upplosning_m=kommun.lantmateriet.upplosning_m,
                anvand_orto="orto" in kommun.lantmateriet.produkter, skriv=skriv,
            )  # fmt: skip
        varningar.extend(f"{x}" for x in bygg.varningar if x not in varningar)
        tr = _tillrinning_for_kärna(k1, karna, gdef.tillrinning_cell_m, extent)
        skriv(
            f"Tillrinning uppströms kärnan: {tr['yta_m2'] / 1e4:.1f} ha, kräver {tr['krav_buffert_m']:.0f} m buffert"
            f"{', når kanten av det hämtade området' if tr['nar_kanten'] else ''}."
        )
        if tr["nar_kanten"] and buffert < gdef.max_buffert_m:
            ny = min(gdef.max_buffert_m, buffert + max(100.0, 0.5 * buffert))
            skriv(f"Vidgar bufferten {buffert} -> {ny:.0f} m och hämtar om.")
            buffert = int(ny)
            continue
        if tr["nar_kanten"]:
            varningar.append(
                f"Tillrinningsområdet når kanten även vid största bufferten ({gdef.max_buffert_m:g} m): "
                "skyfallsresultat nära kärnans uppströmsgräns kan sakna tillrinning."
            )
        break

    terr = terrangstatistik_mosaik(*_mosaik(k1)[:2], karna)
    skriv(
        f"Terräng i kärnan: {terr.get('z_min_m')} till {terr.get('z_max_m')} m (relief {terr.get('relief_m')} m), "
        f"lägsta punkt E {terr.get('lagpunkt_e_m')}, N {terr.get('lagpunkt_n_m')} (EPSG:3011)."
    )

    luck_poster: list[dict[str, Any]] = []
    if hasattr(bygg, "hojd_luckor") and getattr(bygg, "hojd_luckor", None):
        luck_poster = [d.till_dict() for d in bygg.hojd_luckor]
    else:
        from testsvit.k1_io import las_geotiff

        for mark_fil in sorted(k1.glob("mm_*/mark.tif")):
            zm, _ = las_geotiff(mark_fil)
            z_arr = np.asarray(zm, dtype=np.float32)
            tot = int(z_arr.size)
            n_nan = int(np.count_nonzero(np.isnan(z_arr)))
            andel = n_nan / tot if tot > 0 else 0.0
            luck_poster.append(
                {
                    "ruta": mark_fil.parent.name,
                    "totalt_celler": tot,
                    "nan_fore_andel": round(andel, 6),
                    "nan_efter_andel": round(andel, 6),
                    "nan_fore_procent": round(100.0 * andel, 2),
                    "nan_efter_procent": round(100.0 * andel, 2),
                    "huvudsaklig_orsak": "saknar_markdata" if n_nan > 0 else "inga_luckor",
                    "over_troskel": andel > nan_varning_andel,
                }
            )
    max_nan_andel = max((float(p["nan_efter_andel"]) for p in luck_poster), default=0.0)
    over_rutor = [str(p["ruta"]) for p in luck_poster if p["over_troskel"]]
    hog_nan_flagga = bool(over_rutor)
    if hog_nan_flagga:
        nan_varn = (
            f"Höjdmodellens NaN-andel överstiger tröskeln ({100.0 * nan_varning_andel:.0f} %): "
            f"upp till {100.0 * max_nan_andel:.1f} % saknar markhöjd i {len(over_rutor)} ruta/rutor "
            f"({', '.join(over_rutor[:5])})."
        )
        if nan_varn not in varningar:
            varningar.append(nan_varn)
        skriv(f"VARNING: {nan_varn}")
    hojd_kvalitet: dict[str, Any] = {
        "nan_troskel_andel": float(nan_varning_andel),
        "hog_nan_flagga": hog_nan_flagga,
        "max_nan_andel": round(max_nan_andel, 6),
        "rutor_over_troskel": over_rutor,
        "rutor": luck_poster,
    }

    # 4. Lager (innehållsadresserade) och öppenhet.
    lm_manifest_sha = None
    lmf = bygge / "radata" / "lantmateriet" / "lantmateriet_manifest.json"
    if lmf.is_file():
        from gemensamt.hash import sha256_fil

        lm_manifest_sha = sha256_fil(lmf)
    lager_ra = lagra(geo_rot, bygge / "radata")
    lager_k1 = lagra(geo_rot, bygge / "k1_rutnat")
    from data_inlasning.cache import DataInlasningFel
    from geografi.markunderlag import bygg_markunderlag

    def _lagg_till_varning(v: str) -> None:
        if v and v not in varningar:
            varningar.append(v)

    fk = kommun.filpaket
    # VMI läns-ZIP (öppen CC0-data från Naturvårdsverket) när ingen lokal vmi_fil angivits
    # och vmi_lan begärts eller [filpaket.vmi] finns vid hamta_skyddad_natur=True.
    vmi_zip_sha: str | None = None
    eff_vmi_lan = (vmi_lan or "").strip() or (
        fk.vmi.vmi_lan if (hamta_skyddad_natur and fk is not None and fk.vmi is not None) else ""
    )
    if vmi_fil is None and eff_vmi_lan:
        from data_inlasning.skyddad_natur import SkyddadNaturFel, hamta_vmi_lan_zip

        vmi_filnamn = (
            fk.vmi.filnamn
            if (not vmi_lan and fk is not None and fk.vmi is not None and fk.vmi.filnamn)
            else None
        )
        try:
            vmi_post = hamta_vmi_lan_zip(
                eff_vmi_lan,
                geo_rot / "_filcache" / "vmi",
                hamtare=vmi_hamtare if vmi_hamtare is not None else skyddad_natur_hamtare,
                filnamn=vmi_filnamn,
            )
            vmi_zip_sha = vmi_post.sha256
            if vmi_post.gis_fil is not None and vmi_post.gis_fil.is_file():
                vmi_fil = vmi_post.gis_fil
                skriv(f"VMI ({eff_vmi_lan}): hämtad/cachad ({vmi_post.sha256[:12]}).")
            else:
                _lagg_till_varning(
                    f"VMI-paket för {eff_vmi_lan} innehöll ingen .gpkg/.shp-fil; hoppar över VMI-lager."
                )
        except (SkyddadNaturFel, DataInlasningFel, OSError) as exc:
            _lagg_till_varning(f"VMI ({eff_vmi_lan}) kunde inte hämtas ({exc}); fortsätter utan VMI.")
            skriv(f"VARNING: {varningar[-1]}")

    mark_lager, mark_kallor, mark_varn = bygg_markunderlag(
        geo_rot, bygge, karna, nmd_fil=nmd_fil, vmi_fil=vmi_fil, troskel_m=nmd_troskel_m,
        villkor_kontrollerade=villkor_kontrollerade, trad_fil=trad_fil, trad_natverk=trad_natverk,
    )  # fmt: skip
    for mv in mark_varn:
        _lagg_till_varning(mv)

    extra_lager: dict[str, Any] = {}
    extra_kallor: dict[str, Any] = {}

    # 4a. Additivt K46-lager: lager.trafik (Trafikverket API, trafik_fil eller baskarta gator.gpkg utan påhittat ÅDT).
    if trafik_fil is not None or hamta_trafikverket or trafik_fran_baskarta:
        from data_inlasning.trafikverket import (
            hamta_trafikverket_vagnat,
            las_kommunal_trafik_fil,
            las_lastkajen_trafik_gpkg,
        )
        from geografi.trafik_lager import bygg_trafik_lager

        trafik_lankar: list[Any] = []
        trafik_kallor_delar: list[str] = []
        trafik_licenser: list[str] = []
        har_trafik_underlag = False
        gator_k1_fil = lager_k1.mapp / "gator.gpkg"
        if not gator_k1_fil.is_file():
            gator_k1_fil = lager_ra.mapp / "kommun" / "gator.gpkg"

        if hamta_trafikverket:
            tv_nyckel, tv_nyckel_varn = _hamta_api_nyckel(
                "trafikverket",
                explicit_nyckel=trafikverket_nyckel,
                konto_id=konto_id,
                konfig=konfig,
                konfig_fil=konfig_fil,
                register=reg,
                nyckel_forrad=nyckel_forrad,
            )
            if tv_nyckel_varn:
                _lagg_till_varning(tv_nyckel_varn)
            if tv_nyckel is None:
                _lagg_till_varning(
                    "Trafikverket API-nyckel saknas (lägg in nyckeltyp 'trafikverket' på #/nycklar); "
                    "hämtning från Trafikverket Öppet API hoppades över."
                )
                skriv(f"VARNING: {varningar[-1]}")
            else:
                try:
                    trafik_lankar = hamta_trafikverket_vagnat(
                        extent,
                        api_nyckel=tv_nyckel,
                        hamtare=trafikverket_hamtare,
                    )
                    trafik_kallor_delar.append("Trafikverket Öppet API (NVDB)")
                    trafik_licenser.append("CC0-1.0")
                    har_trafik_underlag = True
                    skriv(f"Trafik (Trafikverket NVDB): {len(trafik_lankar)} väglänkar hämtade.")
                except (DataInlasningFel, OSError) as exc:
                    _lagg_till_varning(
                        f"Trafikverket Öppet API misslyckades ({exc}); fortsätter utan NVDB-trafik."
                    )
                    skriv(f"VARNING: {varningar[-1]}")

        if trafik_fil is not None:
            tf_path = Path(trafik_fil)
            try:
                if tf_path.suffix.lower() == ".csv":
                    if not trafik_lankar and gator_k1_fil.is_file():
                        trafik_lankar = _lankar_fran_gator_gpkg(gator_k1_fil, extent)
                    trafik_lankar = las_kommunal_trafik_fil(
                        tf_path,
                        vagnat_lankar=trafik_lankar if trafik_lankar else None,
                        bbox_3011=extent,
                        kall_epsg=kommun.publicering_epsg,
                    )
                elif trafik_lankar:
                    trafik_lankar = las_kommunal_trafik_fil(
                        tf_path,
                        vagnat_lankar=trafik_lankar,
                        bbox_3011=extent,
                        kall_epsg=kommun.publicering_epsg,
                    )
                else:
                    trafik_lankar = las_lastkajen_trafik_gpkg(
                        tf_path,
                        bbox_3011=extent,
                        kall_epsg=kommun.publicering_epsg,
                    )
                trafik_kallor_delar.append(f"Trafikfil ({tf_path.name})")
                trafik_licenser.append("intern" if stangd_baskarta else "CC0-1.0")
                har_trafik_underlag = True
                skriv(f"Trafikfil ({tf_path.name}): {len(trafik_lankar)} väglänkar inlästa.")
            except (DataInlasningFel, OSError) as exc:
                _lagg_till_varning(f"Trafikfilen '{tf_path.name}' kunde inte läsas ({exc}); fortsätter.")
                skriv(f"VARNING: {varningar[-1]}")

        if not trafik_lankar and (trafik_fran_baskarta or (gator_k1_fil.is_file() and hamta_trafikverket)):
            if gator_k1_fil.is_file():
                try:
                    trafik_lankar = _lankar_fran_gator_gpkg(gator_k1_fil, extent)
                    trafik_kallor_delar.append(f"{kommun.namn} baskarta (gator.gpkg, utan ÅDT)")
                    bk_lic = (
                        fk.baskarta.licens
                        if (fk is not None and fk.baskarta is not None)
                        else kommun.byggnader.licens
                    )
                    trafik_licenser.append("intern" if stangd_baskarta else bk_lic)
                    har_trafik_underlag = True
                    skriv(
                        f"Trafik (baskarta gator.gpkg): {len(trafik_lankar)} klickbara väglänkar "
                        "(ÅDT=null, aldrig påhittat)."
                    )
                except Exception as exc:  # noqa: BLE001
                    _lagg_till_varning(f"Kunde inte bygga väglänkar ur gator.gpkg ({exc}).")
            elif trafik_fran_baskarta:
                _lagg_till_varning(
                    "trafik_fran_baskarta begärdes men gator.gpkg saknas i geografin; inget trafiklager byggdes."
                )

        if har_trafik_underlag:
            t_kalla_text = " + ".join(dict.fromkeys(trafik_kallor_delar)) or "Trafikunderlag"
            t_licens = _strangaste_licens(trafik_licenser) if trafik_licenser else "CC0-1.0"
            t_lager, t_kalla, t_varn = bygg_trafik_lager(
                geo_rot,
                bygge,
                karna,
                lankar=trafik_lankar,
                kalla_beskrivning=t_kalla_text,
                licens=t_licens,
                villkor_kontrollerade=villkor_kontrollerade and not stangd_baskarta,
                buffert_m=float(buffert),
            )
            extra_lager["trafik"] = t_lager
            extra_kallor["trafik"] = t_kalla
            for t_rad in t_varn:
                _lagg_till_varning(t_rad)

    # 4b. Additivt K46-lager: lager.vader_luft (SMHI MetObs + STRÅNG + urban bakgrundsluft med graciös timeout).
    if hamta_vader_luft or luft_bakgrund_fil is not None:
        from data_inlasning.smhi_luft_strang import (
            SmhiLuftStrangFel,
            hamta_datavardluft_bakgrund,
            hamta_smhi_vaderpaket,
            las_bakgrund_luft_fil,
        )
        from geografi.vader_lager import bygg_vader_lager

        smhi_cache = geo_rot / "_filcache" / "smhi"
        vader_paket: Any = None
        try:
            vader_paket = hamta_smhi_vaderpaket(
                karna,
                cache_mapp=smhi_cache,
                hamtare=vader_hamtare,
            )
            skriv(
                f"SMHI väderpaket: station {vader_paket.station_namn} ({vader_paket.station_id}, "
                f"{vader_paket.station_avstand_km:.1f} km), år {vader_paket.ar}."
            )
            for pv in vader_paket.varningar:
                _lagg_till_varning(pv)
        except (SmhiLuftStrangFel, DataInlasningFel, OSError) as exc:
            _lagg_till_varning(
                f"SMHI väderpaket kunde inte hämtas ({exc}); fortsätter utan lager.vader_luft."
            )
            skriv(f"VARNING: {varningar[-1]}")

        if vader_paket is not None:
            bakgrund_res: Any = None
            if luft_bakgrund_fil is not None:
                try:
                    bakgrund_res = las_bakgrund_luft_fil(luft_bakgrund_fil)
                except (SmhiLuftStrangFel, DataInlasningFel, OSError) as exc:
                    _lagg_till_varning(
                        f"Bakgrundsluftfilen kunde inte läsas ({exc}); urban bakgrund utelämnas."
                    )
            else:
                bakgrund_res = hamta_datavardluft_bakgrund(
                    karna,
                    cache_mapp=smhi_cache,
                    hamtare=luft_hamtare if luft_hamtare is not None else vader_hamtare,
                )
            vl_lager, vl_kallor, vl_varn = bygg_vader_lager(
                geo_rot,
                bygge,
                karna,
                vindros=vader_paket.vindros,
                utci_sommar=vader_paket.utci_sommar,
                bakgrund_luft=bakgrund_res,
                vader_timserie_csv=vader_paket.vader_timserie_csv,
                villkor_kontrollerade=villkor_kontrollerade,
                station_avstand_km=vader_paket.station_avstand_km,
                ar=vader_paket.ar,
                period=vader_paket.period,
                strang=vader_paket.strang,
                kallor_sha256=vader_paket.kallor_sha256,
            )
            extra_lager.update(vl_lager)
            extra_kallor.update(vl_kallor)
            for v_rad in vl_varn:
                _lagg_till_varning(v_rad)

    # 4c. Additivt K46-lager: lager.skyddad_natur (Naturvårdsverket WFS + Skogsstyrelsen ArcGIS REST + VMI).
    if hamta_skyddad_natur:
        from data_inlasning.skyddad_natur import (
            SkyddadNaturFel,
            hamta_skogsstyrelsen_naturvarden,
            hamta_skyddad_natur_wfs,
        )
        from geografi.ekologi_skrivbord import bygg_skyddad_natur_lager

        skydd_omraden: tuple[Any, ...] = ()
        skog_objekt: tuple[Any, ...] = ()
        sn_kallor_sha: dict[str, str] = {}
        if vmi_zip_sha:
            sn_kallor_sha["vmi_lan_zip"] = vmi_zip_sha

        try:
            nv_res = hamta_skyddad_natur_wfs(
                karna,
                buffert_m=1000.0,
                hamtare=skyddad_natur_hamtare,
            )
            skydd_omraden = nv_res.omraden
            sn_kallor_sha.update(nv_res.kallor_sha256)
            for nv_v in nv_res.varningar:
                _lagg_till_varning(nv_v)
            skriv(f"Skyddad natur (NV WFS): {len(skydd_omraden)} skyddade områden inom 1000 m.")
        except (SkyddadNaturFel, DataInlasningFel, OSError) as exc:
            _lagg_till_varning(
                f"Naturvårdsverket WFS (Skyddad natur/N2000) misslyckades ({exc}); fortsätter med tomt skyddslager."
            )
            skriv(f"VARNING: {varningar[-1]}")

        try:
            sks_res = hamta_skogsstyrelsen_naturvarden(
                karna,
                buffert_m=1000.0,
                hamtare=skogsstyrelsen_hamtare if skogsstyrelsen_hamtare is not None else skyddad_natur_hamtare,
            )
            skog_objekt = sks_res.objekt
            sn_kallor_sha.update(sks_res.kallor_sha256)
            for sks_v in sks_res.varningar:
                _lagg_till_varning(sks_v)
            skriv(f"Skogsstyrelsen (ArcGIS REST): {len(skog_objekt)} skogliga naturvärden inom 1000 m.")
        except (SkyddadNaturFel, DataInlasningFel, OSError) as exc:
            _lagg_till_varning(
                f"Skogsstyrelsen ArcGIS REST misslyckades ({exc}); fortsätter utan skogliga objekt."
            )
            skriv(f"VARNING: {varningar[-1]}")

        vmi_sam: dict[str, Any] | None = None
        if vmi_fil is not None and Path(vmi_fil).is_file():
            try:
                vmi_sam = _sammanfatta_vmi_fil(Path(vmi_fil), karna, buffert_m=1000.0)
            except Exception as exc:  # noqa: BLE001
                _lagg_till_varning(f"Kunde inte summera VMI mot kärnan ({exc}).")
                vmi_sam = {"tillganglig": True, "antal_polygoner": 0, "overlapp_karna_m2": 0.0, "minsta_avstand_m": None}

        sn_lager, sn_kalla, sn_varn = bygg_skyddad_natur_lager(
            geo_rot,
            bygge,
            karna,
            skyddade_omraden=skydd_omraden,
            skogliga_varden=skog_objekt,
            vmi_sammanfattning=vmi_sam,
            buffert_m=1000.0,
            kallor_sha256=sn_kallor_sha,
            villkor_kontrollerade=villkor_kontrollerade,
        )
        extra_lager["skyddad_natur"] = sn_lager
        extra_kallor["skyddad_natur"] = sn_kalla
        for sv in sn_varn:
            _lagg_till_varning(sv)

    # 4d. Additivt K46-lager: lager.artfynd_publika (SLU Artdatabanken SOS med hård OSL 20 kap. 1 §-spärr).
    if hamta_artfynd:
        art_nyckel, art_nyckel_varn = _hamta_api_nyckel(
            "artdatabanken",
            explicit_nyckel=artdatabanken_nyckel,
            konto_id=konto_id,
            konfig=konfig,
            konfig_fil=konfig_fil,
            register=reg,
            nyckel_forrad=nyckel_forrad,
        )
        if art_nyckel_varn:
            _lagg_till_varning(art_nyckel_varn)
        if art_nyckel is None:
            _lagg_till_varning(
                "SLU Artdatabanken API-nyckel saknas (lägg in nyckeltyp 'artdatabanken' på #/nycklar); "
                "hoppar över publika rödlistefynd (lager.artfynd_publika)."
            )
            skriv(f"VARNING: {varningar[-1]}")
        else:
            from data_inlasning.artdatabanken import ArtdatabankenFel, hamta_publika_artfynd
            from geografi.ekologi_skrivbord import bygg_artfynd_publika_lager

            try:
                art_res = hamta_publika_artfynd(
                    karna,
                    api_nyckel=art_nyckel,
                    buffert_m=1000.0,
                    hamtare=artdatabanken_hamtare,
                )
                art_lager, art_kalla, art_varn = bygg_artfynd_publika_lager(
                    geo_rot,
                    bygge,
                    karna,
                    resultat=art_res,
                    villkor_kontrollerade=villkor_kontrollerade,
                )
                extra_lager["artfynd_publika"] = art_lager
                extra_kallor["artfynd_publika"] = art_kalla
                for av in art_varn:
                    _lagg_till_varning(av)
                skriv(
                    f"SLU Artdatabanken (publika fynd): {len(art_res.fynd)} fynd, "
                    f"{len(art_res.artlista)} arter ({art_res.antal_skyddsklassade_filtrerade} skyddsklassade filtrerade)."
                )
            except (ArtdatabankenFel, DataInlasningFel, OSError) as exc:
                fel_txt = str(exc).replace(art_nyckel, "***")
                _lagg_till_varning(
                    f"SLU Artdatabanken SOS misslyckades ({fel_txt}); fortsätter utan lager.artfynd_publika."
                )
                skriv(f"VARNING: {varningar[-1]}")

    licenser = [kommun.lantmateriet.licens] if hojd_anvand == "lantmateriet" else []
    if hojd_anvand == "filpaket" and fk is not None and fk.hojdmodell is not None:
        licenser.append(fk.hojdmodell.licens)
    if kalla_bygg == "lantmateriet":
        licenser.append(kommun.lantmateriet.licens)
    elif kalla_bygg == "wfs":
        licenser.append(kommun.byggnader.licens)
    elif kalla_bygg == "filpaket" and fk is not None and fk.baskarta is not None:
        licenser.append(fk.baskarta.licens)
    elif stangd_baskarta:
        licenser.append(kommun.byggnader.licens)
    lic = _strangaste_licens(licenser)
    skriv_oppenhet_toml(
        lager_ra.mapp, oppen=False, licens="intern" if stangd_baskarta else kommun.lantmateriet.licens,
        kalla="Lantmäteriet (punktmoln, ortofoto, byggnader) och kommunens rådata"
        + (" (inklusive byggnader ur en baskarta som inte är öppna data)" if stangd_baskarta else ""),
        anledning="Rådata: punktmoln kan innehålla personuppgifter enligt Lantmäteriets villkor, och villkoren "
        "för ortofoto och byggnader är inte kontrollerade. Lämnas aldrig ut via MCP.",
    )  # fmt: skip
    oppna_k1 = villkor_kontrollerade and not stangd_baskarta
    kalla_text = f"Härlett ur Lantmäteriet (laser) och {kommun.namn} (byggnader). ©Lantmäteriet."
    if stangd_baskarta:
        kalla_text = f"Härlett ur Lantmäteriet (laser) och {kommun.namn}s baskarta (byggnader), som inte är öppna data."
    anledning = (
        "Byggnaderna är härledda ur en baskarta som inte är öppna data (beslut 2026-11-24): stängt för MCP och extern AI, "
        "oavsett --villkor-kontrollerade. Ändras bara av en människa."
        if stangd_baskarta
        else "Villkoren är kontrollerade (--villkor-kontrollerade): markmodell och resultat är öppna."
        if villkor_kontrollerade
        else "Villkoren för Lantmäteriets och kommunens data är inte kontrollerade av Floden. Stängt för MCP tills "
        "geografin mappas om med --villkor-kontrollerade (ny version) eller oppenhet.toml ändras av en människa."
    )
    for mapp in (lager_k1.mapp, versionsmapp(geo_rot, geografi_id, version)):
        skriv_oppenhet_toml(mapp, oppen=oppna_k1, licens=lic, kalla=kalla_text, anledning=anledning)

    forberaknad_bas_info: dict[str, Any] | None = None
    if forberaknad_lager is not None:
        rutor_forb = forberaknad_lager.hitta_rutor_for_utbredning(kommun.id, extent)
        if rutor_forb:
            forberaknad_bas_info = {
                "kalla": getattr(forberaknad_lager, "kalla_typ", "s3"),
                "kommun": kommun.id,
                "rutor": list(rutor_forb),
            }

    manifest: dict[str, Any] = {
        "kontrakt": KONTRAKT_GEOGRAFI,
        "id": geografi_id,
        "version": version,
        "namn": gdef.namn,
        "kommun": kommun.id,
        "koordinater": {
            "publicering_epsg": kommun.publicering_epsg,
            "intern_epsg": 3011,
            "skalfel_langd_procent": round(yta.skalfel_procent, 4),
            "skalfel_yta_procent": round((yta.skalfaktor_langd**2 - 1) * 100, 4),
        },
        "karna_3011": list(karna),
        "gransen_3011": list(extent),
        "buffert": {
            "dagsljus_paverkansradie_m": round(r_dagsljus, 1),
            "skyfall_tillrinning_krav_m": tr["krav_buffert_m"],
            "vald_m": buffert,
            "max_m": gdef.max_buffert_m,
            "tillrinning_nar_kanten": tr["nar_kanten"],
        },
        "parametrar": {
            "hojd_max_m": hojd_max, "h_min_grader": h_min, "upplosning_m": kommun.lantmateriet.upplosning_m,
            "ruta_storlek_m": 500.0, "byggnadskalla": kalla_bygg, "hojdkalla": hojd_anvand,
        },  # fmt: skip
        "lager": {
            "k1_rutnat": {
                "sha256": lager_k1.sha256,
                "rutor": sorted(p.name for p in lager_k1.mapp.glob("mm_*")),
                "filer": [{"fil": f, "sha256": s} for f, s in lager_k1.filer],
            },
            "radata": {
                "sha256": lager_ra.sha256,
                "lantmateriet_manifest_sha256": lm_manifest_sha,
                "filer": [{"fil": f, "sha256": s} for f, s in lager_ra.filer],
            },
            **mark_lager,
            **extra_lager,
        },
        "forberakningar": {
            "terrang_karna": terr,
            "tillrinning": {k: tr[k] for k in ("cell_m", "celler", "yta_m2", "krav_buffert_m", "nar_kanten")},
            **({"vattenytor": vatten_info} if vatten_info else {}),
            "hojdmodell_kvalitet": hojd_kvalitet,
            **({"forberaknad_bas": forberaknad_bas_info} if forberaknad_bas_info else {}),
            "sankgraf": "inte förberäknad: byggs och cachas av kärnan (K22) vid första körningen",
        },
        "flaggor": {
            "hojdmodell_hog_nan": hog_nan_flagga,
        },
        "kallor": {
            "hojd": {
                "kalla": hojd_anvand,
                **(
                    {"licens": fk.hojdmodell.licens, "anmarkning": fk.hojdmodell.licens_anmarkning}
                    if hojd_anvand == "filpaket" and fk is not None and fk.hojdmodell is not None
                    else {"licens": kommun.lantmateriet.licens, "anmarkning": kommun.lantmateriet.licens_anmarkning}
                ),
            },
            "byggnader": (
                {
                    "kalla": kalla_bygg, "licens": kommun.byggnader.licens, "anmarkning": kommun.byggnader.licens_anmarkning,
                    "stangd": True, "hojd_fran": kommun.byggnader.lokal.hojd_fran if kommun.byggnader.lokal else None,
                    "antal_filer": len(baskarta_filer),
                }
                if stangd_baskarta
                else {"kalla": kalla_bygg, "licens": fk.baskarta.licens, "anmarkning": fk.baskarta.licens_anmarkning}
                if kalla_bygg == "filpaket" and fk is not None and fk.baskarta is not None
                else {
                    "kalla": kalla_bygg, "licens": kommun.lantmateriet.licens,
                    "anmarkning": "Byggnad Nedladdning, vektor (avgiftsfri). ©Lantmäteriet. Bara geometri och höjd/våningar sparas.",
                }
                if kalla_bygg == "lantmateriet"
                else {"kalla": kalla_bygg, "licens": kommun.byggnader.licens, "anmarkning": kommun.byggnader.licens_anmarkning}
            ),
            **mark_kallor,
            **extra_kallor,
        },
        "motivering_karna": omrade.motivering,
        "oppen_for_mcp": oppna_k1,
        "varningar": varningar,
    }
    fil, sha = skriv_manifest(geo_rot, manifest)
    shutil.rmtree(bygge, ignore_errors=True)
    skriv(
        f"Klart: {ref}, manifest sha256 {sha[:12]}. Lager k1_rutnat {lager_k1.sha256[:12]} "
        f"({'nytt' if lager_k1.nytt else 'delas med en tidigare version'}), radata {lager_ra.sha256[:12]}."
    )
    if not oppna_k1:
        skriv("MCP: härledd data är STÄNGD tills villkoren är kontrollerade (--villkor-kontrollerade).")
    return MappaResultat(ref, fil, sha, True, varningar, oppna_k1)


__all__ = ["GeografiFel", "MappaResultat", "mappa_geografi", "skalfel_langd"]
