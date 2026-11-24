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
) -> MappaResultat:
    from karna.paverkan import dagsljus_paverkansradie_m
    from kommuner.baskarta import byggnader_ur_lokala_filer, tak_till_gpkg
    from kommuner.filkallor import hamta_baskarta, hamta_hojdmodell
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

    for _f, _n in ((nmd_fil, "NMD"), (vmi_fil, "VMI"), (trad_fil, "Träd")):
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
                klippt = ra / "hojd" / "hojdmodell_klippt.tif"
                varningar.extend(x for x in skriv_klippt_hojd(hoj, extent, kommun.lantmateriet.upplosning_m, klippt) if x not in varningar)
                (ra / "filpaket").mkdir(parents=True, exist_ok=True)
                (ra / "filpaket" / "kallor.json").write_text(
                    json.dumps(
                        {
                            "hojdmodell": [{"url": q.url, "fil": q.namn, "sha256": q.sha256} for q in hpaket],
                            "baskarta": [{"url": q.url, "fil": q.namn, "sha256": q.sha256} for q in bpaket],
                        },
                        indent=2, sort_keys=True, ensure_ascii=False,
                    ) + "\n",
                    encoding="utf-8",
                )
                hojd_anvand = "filpaket"
                skriv("Bygger K1-markmodellen ur kommunens höjdmodell (rutor om 500 m) ...")
                bygg = bygg_omrade_filpaket(
                    omr, k1, hojdmodell=Hojdmodell([klippt], epsg_antagen=3011), hojd_paket=hpaket,
                    byggnader_fil=bygg_fil, byggnader_paket=bpaket,
                    upplosning_m=kommun.lantmateriet.upplosning_m, skriv=skriv,
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

    # 4. Lager (innehållsadresserade) och öppenhet.
    lm_manifest_sha = None
    lmf = bygge / "radata" / "lantmateriet" / "lantmateriet_manifest.json"
    if lmf.is_file():
        from gemensamt.hash import sha256_fil

        lm_manifest_sha = sha256_fil(lmf)
    lager_ra = lagra(geo_rot, bygge / "radata")
    lager_k1 = lagra(geo_rot, bygge / "k1_rutnat")
    from geografi.markunderlag import bygg_markunderlag

    mark_lager, mark_kallor, mark_varn = bygg_markunderlag(
        geo_rot, bygge, karna, nmd_fil=nmd_fil, vmi_fil=vmi_fil, troskel_m=nmd_troskel_m,
        villkor_kontrollerade=villkor_kontrollerade, trad_fil=trad_fil, trad_natverk=trad_natverk,
    )  # fmt: skip
    varningar.extend(mark_varn)
    fk = kommun.filpaket
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
        },
        "forberakningar": {
            "terrang_karna": terr,
            "tillrinning": {k: tr[k] for k in ("cell_m", "celler", "yta_m2", "krav_buffert_m", "nar_kanten")},
            "sankgraf": "inte förberäknad: byggs och cachas av kärnan (K22) vid första körningen",
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
