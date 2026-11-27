# ruff: noqa: E501
"""Helhets- och integreringstester (Steg 3) för API-nycklar, K46-tilläggslager, webb-API, MCP och ekologirapport.

100 % nätverksfria och deterministiska tester som knyter ihop:
1. Krypterade API-nycklar (`trafikverket` och `artdatabanken`) i `konto.nycklar` + `/konto/nycklar/*` (0 läckage).
2. `mappa_geografi` (K46 `floden-geografi/v1`) med alla 4 nya additiva lager via mockade hämtare:
   `lager.trafik` (NVDB + baskarta-fallback utan påhittat ÅDT), `lager.vader_luft` (SMHI + graciös
   luftkvalitetstimeout), `lager.skyddad_natur` (NV WFS + Skogsstyrelsen REST + VMI läns-ZIP → `lager.vmi`),
   `lager.artfynd_publika` (SLU Artdatabanken SOS med hård OSL 20 kap. 1 §-spärr + varning utan nyckel).
3. Alla nya autentiserade, CSRF- och IDOR-skyddade webb-endpoints (`GET/POST /api/projekt/<id>/trafik`,
   `GET /api/projekt/<id>/vader_luft`, `POST /api/analys/luft_skiss`, `GET /api/projekt/<id>/ekologi/skrivbord`,
   `POST /api/projekt/<id>/ekologi/faltlager`), `GET /api/analys/varmestress?forval=platsens_smhi_p95`,
   `STANDARD_MAX_FLACKAR = 120` samt oförändrade bullerspärrar (`nord2000_konform=False`, `rapport_sparrad=True`).
4. Automatisk försörjning av `ekoskrivbord.*`-belägg i ekologirapporter (`RapportTjanst` + `webbflode`),
   9/9 kontroll, 0 tomma PDF-sidor, OSL-varning och svensk `dagsljus.svf_mark`-etikett.
5. `utv_geografi_mappa` med nya valfria additiva flaggor + strikt `vmi_lan`-validering.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import tomllib
import urllib.parse
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon

from data_inlasning.artdatabanken import OSL_VARNING
from data_inlasning.skyddad_natur import N2K_LAGER, NV_SKYDDSFORMER
from data_inlasning.smhi_luft_strang import las_datavardluft_csv_text
from data_inlasning.trafikverket import TrafikLank
from demovarld.varld import DemoVarld, bygg_ruta_vatten
from floden_mcp.karna_api import FlodenTjanst
from gemensamt.hash import sha256_fil
from gemensamt.koordinater import EPSG_INTERN, EPSG_LANTMATERIET, transformera
from geografi.ekologi_skrivbord import (
    MAX_FALTLAGER_BYTES,
    bygg_artfynd_publika_lager,
    bygg_skyddad_natur_lager,
)
from geografi.modell import KONTRAKT_GEOGRAFI, lager_mapp, lagra, las_manifest, skriv_manifest
from geografi.smhi_vader import (
    PARAM_RELATIV_FUKTIGHET,
    PARAM_TEMPERATUR,
    PARAM_VINDHASTIGHET,
    PARAM_VINDRIKTNING,
    berakna_varmebolja_utci,
    berakna_vindros_16,
    las_smhi_csv_text,
)
from geografi.trafik_lager import TRAFIK_GPKG_FIL, bygg_trafik_lager
from geografi.vader_lager import bygg_vader_lager
from karna.ekologi_losare import STANDARD_MAX_FLACKAR
from kommuner.konfig import las_kommun, tolka_kommun
from rapport.skissmall import bygg_dokument, huvudtext
from rapport.skissrapport import RapportTjanst
from rapport.webbflode import pastaenden_for
from tests.demovarld.test_demo import Demo, demo, demo_mall  # noqa: F401
from tests.geografi.test_etapp1_mappa_nmd_och_geometri import _bygg_kompletta_paket
from tests.kommuner.test_filpaket_mappa import HOJD_URL, FalskFilhamtare, inga_nycklar
from tests.kommuner.test_skapa import bygg_varld, mappa
from tests.konto.conftest import (  # noqa: F401
    ANNA,
    ORIGIN,
    Miljo,
    Webblasare,
    idp,
    klocka,
    miljo,
)
from tests.konto.test_nycklar import _inloggad, _las, _skriv, _slå_på
from tests.lantmateriet.conftest import lm  # noqa: F401
from tests.lantmateriet.falsk_server import FalskLm
from tests.rapport.test_skissrapport import _norm, _text_pdf
from testsvit.k1_io import las_geotiff
from webb.api import FlodenApiApp
from webb.projektkalla import KONTRAKT_PROJEKT

REPO = Path(__file__).resolve().parents[2]

BO = {
    "sub": "sub-bo-api-int",
    "email": "bo@example.test",
    "email_verified": True,
    "name": "Bo Lasare",
}
DI = {
    "sub": "sub-di-api-int",
    "email": "di@example.test",
    "email_verified": True,
    "name": "Di Utomstaende",
}


# ---------------------------------------------------------------------------
# Hjälpare
# ---------------------------------------------------------------------------


def _http_raw(
    app: FlodenApiApp,
    webb: Webblasare | None,
    metod: str,
    sokvag: str,
    kropp: Any = None,
    *,
    med_csrf: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    rub: dict[str, str] = {}
    if webb is not None:
        rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
        if metod != "GET":
            rub["Origin"] = ORIGIN
            if med_csrf:
                rub.update(webb.csrf_headers())
    if isinstance(kropp, bytes):
        data = kropp
    elif kropp is None:
        data = b""
    else:
        data = json.dumps(kropp).encode("utf-8")
    status, headers, raw = app.hantera_http(metod, sokvag, rub, lambda: data)
    return status, {k.lower(): v for k, v in headers}, raw


def _http_json(
    app: FlodenApiApp,
    webb: Webblasare | None,
    metod: str,
    sokvag: str,
    kropp: Any = None,
    *,
    med_csrf: bool = True,
) -> tuple[int, dict[str, Any]]:
    status, _, raw = _http_raw(app, webb, metod, sokvag, kropp, med_csrf=med_csrf)
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _poly_3011_till_epsg(
    coords_3011: list[tuple[float, float]], mal_epsg: int
) -> list[list[float]]:
    ut: list[list[float]] = []
    for e, n in coords_3011:
        x, y = transformera(e, n, EPSG_INTERN, mal_epsg)
        ut.append([round(x, 6), round(y, 6)])
    return ut


def _linje_3011_till_3006_wkt(pts_3011: list[tuple[float, float]]) -> str:
    pts_3006 = [transformera(e, n, EPSG_INTERN, EPSG_LANTMATERIET) for e, n in pts_3011]
    return f"LINESTRING ({', '.join(f'{e:.3f} {n:.3f}' for e, n in pts_3006)})"


def _bygg_syntetisk_smhi_csv(
    station_id: str,
    station_namn: str,
    param_namn: str,
    enhet: str,
    rader: list[tuple[str, str, str, str]],
) -> bytes:
    huvud = (
        "\ufeffStationsnamn;Stationsnummer;Mätstationsgrupp\n"
        f"{station_namn};{station_id};SMHI\n"
        "Parameternamn;Beskrivning;Enhet\n"
        f"{param_namn};timvärde;{enhet}\n\n"
        f"Datum;Tid (UTC);{param_namn};Kvalitet\n"
    )
    return (huvud + "\n".join(";".join(r) for r in rader) + "\n").encode("utf-8")


def _kropps_text_utan_ram(sida_text: str, sidhuvud_bit: str, sidfot_bit: str) -> str:
    ren = _norm(sida_text)
    ren = re.sub(r"Sida \d+ \(\d+\)", "", ren)
    for bit in (sidhuvud_bit, sidfot_bit):
        if bit:
            ren = ren.replace(_norm(bit), "")
    return ren.strip()


def _bygg_komplett_projekt_med_alla_lager(
    rot: Path,
    *,
    konto: Any | None = None,
    projekt_id: str = "haga-int-proj",
) -> tuple[FlodenApiApp, Path, Path, Path, str]:
    """Bygger ett 100 % lokalt K46-geografiprojekt med k1, nmd, trafik, vader_luft, skyddad_natur, artfynd."""
    geo_rot = rot / "geo"
    proj_rot = rot / "projekt"

    varld = DemoVarld()
    k1_tmp = rot / "bygge_k1"
    bygg_ruta_vatten(varld, k1_tmp)
    (k1_ruta,) = sorted(k1_tmp.glob("mm_*"))
    _, geo_info = las_geotiff(k1_ruta / "mark.tif")
    x0, y0 = float(geo_info.x_min), float(geo_info.y_min)
    x1, y1 = float(geo_info.x_max), float(geo_info.y_max)
    karna = (x0 + 10.0, y0 + 10.0, x1 - 10.0, y1 - 10.0)
    k1_lager = lagra(geo_rot, k1_tmp)

    # NMD 10 m-raster som täcker kärnan + buffert (ger stabila habitatfläckar för ekologi.konnektivitet)
    nmd_tmp = rot / "bygge_nmd"
    nmd_tmp.mkdir(parents=True, exist_ok=True)
    nmd_data = np.full((60, 60), 42, dtype=np.uint8)
    nmd_data[12:24, 12:24] = 111
    nmd_data[32:44, 32:44] = 111
    nmd_data[20:32, 30:40] = 116
    with rasterio.open(
        nmd_tmp / "nmd.tif",
        "w",
        driver="GTiff",
        height=60,
        width=60,
        count=1,
        dtype="uint8",
        crs="EPSG:3011",
        transform=from_origin(x0 - 100.0, y1 + 100.0, 10.0, 10.0),
    ) as ds:
        ds.write(nmd_data, 1)
    (nmd_tmp / "nmd.json").write_text(
        json.dumps({"epsg": 3011, "cell_m": 10.0, "buffert_m": 100.0}), encoding="utf-8"
    )
    nmd_lag = lagra(geo_rot, nmd_tmp)

    # 1. lager.trafik (placera väglänkar exakt på DemoVarlds 12 m breda gatucentrum x/y = 350 m)
    gata_y = varld.n0_m + 350.0
    gata_x = varld.e0_m + 350.0
    trafik_lankar = [
        TrafikLank(
            lank_id="nvdb_haga_1",
            koordinater_3011=((x0 + 20.0, gata_y), (x1 - 20.0, gata_y)),
            gatunamn="Sprängkullsgatan",
            vaghallare="kommunal",
            hastighet_km_h=40.0,
            korfalt=2,
            adt_fordon_dygn=9500.0,
            andel_tung_pct=8.0,
            matar=2025,
            adt_kalla="Trafikverket NVDB",
            osaker=False,
        ),
        TrafikLank(
            lank_id="baskarta_haga_2",
            koordinater_3011=((gata_x, y0 + 20.0), (gata_x, y1 - 20.0)),
            gatunamn="Haga Nygata",
            vaghallare="kommunal",
            hastighet_km_h=30.0,
            adt_fordon_dygn=None,
            andel_tung_pct=None,
            adt_kalla=None,
            osaker=False,
        ),
    ]
    trafik_lager, trafik_kalla, _ = bygg_trafik_lager(
        geo_rot,
        rot / "bygge_trafik",
        karna,
        lankar=trafik_lankar,
        kalla_beskrivning="NVDB + Baskarta",
        licens="CC0-1.0",
        villkor_kontrollerade=True,
    )

    # 2. lager.vader_luft
    su = las_smhi_csv_text(
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Vindhastighet;Kvalitet\n"
        "2024-07-10;12:00:00;2.0;G\n"
        "2024-07-10;13:00:00;3.4;G\n"
        "2024-07-10;14:00:00;1.9;G\n"
        "2024-07-10;15:00:00;4.1;G\n",
        parameter_id=PARAM_VINDHASTIGHET,
    )
    sd = las_smhi_csv_text(
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Vindriktning;Kvalitet\n"
        "2024-07-10;12:00:00;270;G\n"
        "2024-07-10;13:00:00;255;G\n"
        "2024-07-10;14:00:00;230;G\n"
        "2024-07-10;15:00:00;270;G\n",
        parameter_id=PARAM_VINDRIKTNING,
    )
    st = las_smhi_csv_text(
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Lufttemperatur;Kvalitet\n"
        "2024-07-10;12:00:00;25.5;G\n"
        "2024-07-10;13:00:00;28.0;G\n"
        "2024-07-10;14:00:00;31.2;G\n"
        "2024-07-10;15:00:00;32.4;G\n",
        parameter_id=PARAM_TEMPERATUR,
    )
    srh = las_smhi_csv_text(
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Relativ Luftfuktighet;Kvalitet\n"
        "2024-07-10;12:00:00;50;G\n"
        "2024-07-10;13:00:00;44;G\n"
        "2024-07-10;14:00:00;38;G\n"
        "2024-07-10;15:00:00;35;G\n",
        parameter_id=PARAM_RELATIV_FUKTIGHET,
    )
    bakg = las_datavardluft_csv_text(
        "Station;Femman\nDatum;NO2;PM10;O3\n"
        "2024-01-01;14,0;18,0;56,0\n"
        "2024-01-02;16,0;24,0;60,0\n",
        avstand_km=1.2,
    )
    timserie = (
        "tid_iso,temperatur_c,nederbord_mm_h,relativ_fuktighet_pct,vindhastighet_m_s\n"
        + "\n".join(
            f"2024-07-10T{h:02d}:00,24.000,{0.4 if h == 3 else 0.0:.3f},45.00,3.000"
            for h in range(24)
        )
        + "\n"
    )
    vl_lager, vl_kallor, _ = bygg_vader_lager(
        geo_rot,
        rot / "bygge_vl",
        karna,
        vindros=berakna_vindros_16(su, sd),
        utci_sommar=berakna_varmebolja_utci(st, relativ_fuktighet=srh, vindhastighet=su),
        bakgrund_luft=bakg,
        vader_timserie_csv=timserie,
        villkor_kontrollerade=True,
        station_avstand_km=2.0,
        ar=2024,
        strang={
            "global_p95_dag_w_m2": 730.0,
            "global_p99_dag_w_m2": 815.0,
            "direkt_normal_p95_dag_w_m2": 780.0,
            "direkt_normal_p99_dag_w_m2": 850.0,
            "kalla": "SMHI STRÅNG v1 (CC BY 4.0)",
        },
    )

    # 3. lager.skyddad_natur
    from data_inlasning.skyddad_natur import SkogligtNaturvarde, SkyddatOmrade

    sn_omrade = SkyddatOmrade(
        omrade_id="NVR-2001",
        namn="Änggårdsbergen",
        skyddsform="NR",
        skyddsform_namn="Naturreservat",
        lager="ps-nvr:naturreservat",
        beslutsdatum="1975-06-12",
        kalla="Naturvårdsverket WFS",
        geometri_3011=Polygon(
            [
                (x0 + 10.0, y0 + 10.0),
                (x0 + 35.0, y0 + 10.0),
                (x0 + 35.0, y0 + 35.0),
                (x0 + 10.0, y0 + 35.0),
            ]
        ),
        area_m2=625.0,
        overlapp_karna_m2=625.0,
        overlappar_karna=True,
        minsta_avstand_m=0.0,
    )
    sg_obj = SkogligtNaturvarde(
        objekt_id="NYB-9001",
        kategori="nyckelbiotop",
        kategori_namn="Nyckelbiotop",
        namn="Ekbacke",
        biotoptyp="Ädellövskog",
        beslutsdatum="2010-05-01",
        tjanst="Geodataportal/GeodataportalVisaNyckelbiotop/MapServer/0/query",
        kalla="Skogsstyrelsen ArcGIS REST",
        geometri_3011=Polygon(
            [
                (x1 + 30.0, y0 + 10.0),
                (x1 + 60.0, y0 + 10.0),
                (x1 + 60.0, y0 + 40.0),
                (x1 + 30.0, y0 + 40.0),
            ]
        ),
        area_m2=900.0,
        overlapp_karna_m2=0.0,
        overlappar_karna=False,
        minsta_avstand_m=40.0,
    )
    sn_lager, sn_kalla, _ = bygg_skyddad_natur_lager(
        geo_rot,
        rot / "bygge_sn",
        karna,
        skyddade_omraden=(sn_omrade,),
        skogliga_varden=(sg_obj,),
        villkor_kontrollerade=True,
    )

    # 4. lager.artfynd_publika
    from data_inlasning.artdatabanken import ArtfyndResultat, ArtSammanfattning, PubliktArtfynd

    art_fynd = PubliktArtfynd(
        taxon_id=102001,
        svenskt_namn="Ekoxe",
        vetenskapligt_namn="Lucanus cervus",
        rodlistekategori="NT",
        fridlyst=True,
        fyndar=2024,
        osakerhet_m=20.0,
        e_m=x0 + 25.0,
        n_m=y0 + 25.0,
    )
    art_sam = ArtSammanfattning(
        taxon_id=102001,
        svenskt_namn="Ekoxe",
        vetenskapligt_namn="Lucanus cervus",
        rodlistekategori="NT",
        fridlyst=True,
        antal_fynd=1,
        senaste_fyndar=2024,
        minsta_avstand_m=0.0,
    )
    art_res = ArtfyndResultat(
        karna_3011=karna,
        buffert_m=1000.0,
        start_ar=2016,
        slut_ar=2026,
        fynd=(art_fynd,),
        artlista=(art_sam,),
        antal_skyddsklassade_filtrerade=1,
        antal_bortfiltrerade_ej_prioriterade=0,
        kalla_sha256="b" * 64,
    )
    art_lager, art_kalla, _ = bygg_artfynd_publika_lager(
        geo_rot,
        rot / "bygge_art",
        karna,
        resultat=art_res,
        villkor_kontrollerade=True,
    )

    lager_dict: dict[str, Any] = {
        "k1_rutnat": {"sha256": k1_lager.sha256, "rutor": [k1_ruta.name]},
        "nmd": {"sha256": nmd_lag.sha256, "buffert_m": 100.0, "cell_m": 10.0},
        "trafik": trafik_lager,
        **vl_lager,
        "skyddad_natur": sn_lager,
        "artfynd_publika": art_lager,
    }
    kallor_dict: dict[str, Any] = {
        "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
        "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
        "nmd": {"licens": "CC0-1.0", "kalla": "Syntetisk NMD"},
        "trafik": trafik_kalla,
        **vl_kallor,
        "skyddad_natur": sn_kalla,
        "artfynd_publika": art_kalla,
    }
    manifest_fil, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "goteborg-int",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": list(karna),
            "gransen_3011": [x0, y0, x1, y1],
            "lager": lager_dict,
            "kallor": kallor_dict,
            "oppen_for_mcp": True,
        },
    )

    pmapp = proj_rot / projekt_id
    (pmapp / "fragor").mkdir(parents=True, exist_ok=True)
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": projekt_id,
                "register_id": projekt_id,
                "namn": "Göteborg Haga Integrering",
                "kommun": "goteborg",
                "geografi": "goteborg-int@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [x0 + 15.0, y0 + 15.0, x1 - 15.0, y1 - 15.0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    k1_lager_mapp = geo_rot / "_lager" / k1_lager.sha256
    (pmapp / "fragor" / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\n'
        'fraga_id = "haga-skyfall"\n'
        'beskrivning = "Haga skyfall"\n\n'
        f'[scenarier.fore]\nbas = "{k1_lager_mapp.as_posix()}"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 40.0 }\n",
        encoding="utf-8",
    )

    konfig = rot / "config.toml"
    konfig.write_text(
        '[webb]\ndemomapp = "varld"\nfragemappar = ["fragor"]\ncachemapp = "cache"\n'
        'korningsmapp = "korningar"\nklientmapp = "klient"\n',
        encoding="utf-8",
    )
    (rot / "tom-testsvit").mkdir(exist_ok=True)
    app = FlodenApiApp(
        testsvit_rot=rot / "tom-testsvit",
        konfig_fil=konfig,
        geomapp=geo_rot,
        projektmapp=proj_rot,
        kartverktygsmapp=rot / "kartverktyg",
        rapportmapp=rot / "rapporter",
        konto=konto,
    )
    return app, pmapp, geo_rot, manifest_fil, projekt_id


# ---------------------------------------------------------------------------
# 1. Krypterade API-nycklar: trafikverket & artdatabanken
# ---------------------------------------------------------------------------


def test_e2e_krypterade_api_nycklar_trafikverket_och_artdatabanken(miljo: Miljo) -> None:  # noqa: F811
    """Verifierar Fernet-kryptering, HTTP-API och 0-läckage för `trafikverket` och `artdatabanken`."""
    _slå_på(miljo)
    webb_anna, anna_id = _inloggad(miljo, ANNA)
    webb_bo, _ = _inloggad(miljo, BO)

    tv_secret = "TV_LIVE_SECRET_KEY_8811"
    art_secret = "SLU_SOS_SUB_KEY_7722"

    # Utan CSRF nekas sparande alltid (403)
    st_no_csrf, _ = _skriv(
        webb_anna, "PUT", "/konto/nycklar/trafikverket", {"nyckel": tv_secret}, csrf=False
    )
    assert st_no_csrf == 403

    # Spara båda nya nyckeltyperna med giltig CSRF
    for typ, hemlighet in (("trafikverket", tv_secret), ("artdatabanken", art_secret)):
        st, svar = _skriv(webb_anna, "PUT", f"/konto/nycklar/{typ}", {"nyckel": hemlighet})
        assert st == 200, svar
        assert hemlighet not in json.dumps(svar)

    # Listan visar båda som kategori="data" och enbart 4 avslutande tecken
    st_l, lista_data, lista_ra = _las(webb_anna, "/konto/nycklar")
    assert st_l == 200
    typer_map = {p["typ"]: p for p in lista_data["typer"]}
    assert typer_map["trafikverket"]["kategori"] == "data"
    assert typer_map["trafikverket"]["slutar_pa"] == "8811"
    assert typer_map["artdatabanken"]["kategori"] == "data"
    assert typer_map["artdatabanken"]["slutar_pa"] == "7722"
    assert _las(webb_anna, "/konto/nycklar/trafikverket")[1]["slutar_pa"] == "8811"
    assert _las(webb_anna, "/konto/nycklar/artdatabanken")[1]["slutar_pa"] == "7722"
    assert tv_secret.encode("utf-8") not in lista_ra
    assert art_secret.encode("utf-8") not in lista_ra

    # Annat konto ser aldrig Annas nycklar
    assert _las(webb_bo, "/konto/nycklar/trafikverket")[1]["sparad"] is None
    assert _las(webb_bo, "/konto/nycklar/artdatabanken")[1]["sparad"] is None

    # Rå SQLite-databas innehåller aldrig klartextnycklarna
    db_raw = miljo.register.db.sokvag.read_bytes()
    assert tv_secret.encode("utf-8") not in db_raw
    assert art_secret.encode("utf-8") not in db_raw

    # Inläsning via NyckelForrad dekrypterar korrekt
    assert miljo.adapter.nycklar is not None
    assert miljo.adapter.nycklar.las(anna_id, "trafikverket") == {"nyckel": tv_secret}
    assert miljo.adapter.nycklar.las(anna_id, "artdatabanken") == {"nyckel": art_secret}


# ---------------------------------------------------------------------------
# 2. K46-mappning med alla 4 nya lager + OSL-spärr + baskarta-fallback
# ---------------------------------------------------------------------------


def test_e2e_k46_mappa_alla_fyra_nya_lager_osl_sparr_och_baskarta_fallback(
    lm: FalskLm,  # noqa: F811
    miljo: Miljo,  # noqa: F811
    tmp_path: Path,
) -> None:
    """Kör `mappa_geografi` med mockade hämtare för alla 4 nya K46-lager + VMI + OSL-spärr + baskarta-fallback."""
    # Verifiera produktionskonfig i data/kommuner/goteborg.toml
    gbg = las_kommun("goteborg")
    assert gbg.filpaket is not None and gbg.filpaket.vmi is not None
    assert gbg.filpaket.vmi.vmi_lan == "O_Goteborg_Bohus"
    assert "geodata.naturvardsverket.se" in gbg.filpaket.tillatna_varddomaner

    v = bygg_varld(lm, tmp_path)
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh_toml:
        d = tomllib.load(fh_toml)
    d["omraden"]["haga"]["bbox"] = [147450.0, 6397550.0, 147550.0, 6397650.0]
    d["lantmateriet"].update(kontext_m=20.0, upplosning_m=2.0)
    d["dagsljus"].update(azimutriktningar=32, hojd_max_m=3, cellstorlek_m=2)
    d["geografier"]["goteborg-test"]["max_buffert_m"] = 400.0
    d["filpaket"]["hojdmodell"]["urlar"] = [HOJD_URL]
    v["kommun"] = tolka_kommun(d)
    paket = _bygg_kompletta_paket(tmp_path)
    fh = FalskFilhamtare(paket)

    # Spara krypterade nycklar för trafikverket och artdatabanken i NyckelForrad
    _slå_på(miljo)
    assert miljo.adapter.nycklar is not None
    konto_id = miljo.register.forbered_konto("mappa@example.test")
    tv_nyckel = "TV_SECRET_MAPPA_4455"
    art_nyckel = "ART_SECRET_MAPPA_6677"
    miljo.adapter.nycklar.spara(konto_id, "trafikverket", {"nyckel": tv_nyckel})
    miljo.adapter.nycklar.spara(konto_id, "artdatabanken", {"nyckel": art_nyckel})

    # Beräkna kärnans koordinater i EPSG:3011 för mockgeometrier
    x0_3011, y0_3011 = transformera(147450.0, 6397550.0, 3007, EPSG_INTERN)
    x1_3011, y1_3011 = transformera(147550.0, 6397650.0, 3007, EPSG_INTERN)
    lon_c, lat_c = transformera((x0_3011 + x1_3011) / 2.0, (y0_3011 + y1_3011) / 2.0, EPSG_INTERN, 4326)

    # 2a. Mock Trafikverket NVDB
    wkt_nvdb = _linje_3011_till_3006_wkt([(x0_3011 + 10.0, y0_3011 + 40.0), (x1_3011 - 10.0, y0_3011 + 40.0)])

    def fake_trafikverket(_url: str, body: bytes, _headers: Mapping[str, str]) -> bytes:
        assert tv_nyckel.encode("utf-8") in body
        return json.dumps(
            {
                "RESPONSE": {
                    "RESULT": [
                        {
                            "RoadData": [
                                {
                                    "LinkId": "tv_haga_1",
                                    "StreetName": "Sprängkullsgatan",
                                    "RoadOwner": "Kommunal",
                                    "SpeedLimit": 40,
                                    "NumberOfLanes": 2,
                                    "AverageDailyTraffic": 10200,
                                    "HeavyVehiclePercentage": 7.5,
                                    "MeasurementYear": 2025,
                                    "Geometry": {"SWEREF99TM": wkt_nvdb},
                                }
                            ]
                        }
                    ]
                }
            }
        ).encode("utf-8")

    # 2b. Mock SMHI MetObs + STRÅNG + timeout på Datavärdskap luftkvalitet (ska varna graciöst, ALDRIG krascha!)
    stationer_bytes = json.dumps(
        {
            "station": [
                {
                    "id": "71420",
                    "name": "Göteborg A",
                    "latitude": round(lat_c, 4),
                    "longitude": round(lon_c, 4),
                    "height": 5.0,
                    "active": True,
                }
            ]
        }
    ).encode("utf-8")
    rader_t, rader_u, rader_d, rader_rh, rader_ned = [], [], [], [], []
    strang_116, strang_117 = [], []
    t0 = datetime(2024, 7, 10, 0, 0, tzinfo=UTC)
    for h in range(24):
        dt = t0 + timedelta(hours=h)
        ds, ts, iso = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        rader_t.append((ds, ts, f"{24.0 + 0.3 * (h % 12):.1f}", "G"))
        rader_u.append((ds, ts, f"{2.2 + 0.1 * (h % 6):.1f}", "G"))
        rader_d.append((ds, ts, "270", "G"))
        rader_rh.append((ds, ts, "46", "G"))
        rader_ned.append((ds, ts, "0.0", "G"))
        strang_116.append({"date_time": iso, "value": 720.0 if 8 <= h <= 16 else 10.0})
        strang_117.append({"date_time": iso, "value": 780.0 if 8 <= h <= 16 else 0.0})

    def fake_smhi(url: str) -> bytes:
        if "strang1g" in url:
            return json.dumps(strang_116 if "/parameter/116/" in url else strang_117).encode("utf-8")
        if url.endswith(".json"):
            return stationer_bytes
        if "/parameter/1/" in url:
            return _bygg_syntetisk_smhi_csv("71420", "Göteborg A", "Lufttemperatur", "°C", rader_t)
        if "/parameter/4/" in url:
            return _bygg_syntetisk_smhi_csv("71420", "Göteborg A", "Vindhastighet", "m/s", rader_u)
        if "/parameter/3/" in url:
            return _bygg_syntetisk_smhi_csv("71420", "Göteborg A", "Vindriktning", "grader", rader_d)
        if "/parameter/6/" in url:
            return _bygg_syntetisk_smhi_csv("71420", "Göteborg A", "Relativ Luftfuktighet", "%", rader_rh)
        if "/parameter/7/" in url:
            return _bygg_syntetisk_smhi_csv("71420", "Göteborg A", "Nederbördsmängd", "mm", rader_ned)
        raise AssertionError(f"Oväntad SMHI-URL: {url}")

    def fake_luft_timeout(_url: str) -> bytes:
        raise TimeoutError("simulerad timeout mot datavardluft.smhi.se")

    # 2c. Mock Naturvårdsverket WFS + Skogsstyrelsen ArcGIS REST + VMI läns-ZIP
    nv_poly_3011 = [
        (x0_3011 + 5.0, y0_3011 + 5.0),
        (x0_3011 + 45.0, y0_3011 + 5.0),
        (x0_3011 + 45.0, y0_3011 + 45.0),
        (x0_3011 + 5.0, y0_3011 + 45.0),
        (x0_3011 + 5.0, y0_3011 + 5.0),
    ]

    def fake_nv_wfs(url: str) -> bytes:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        tn = qs["typeNames"][0]
        if tn == NV_SKYDDSFORMER["NR"][0]:
            return json.dumps(
                {
                    "type": "FeatureCollection",
                    "crs": {"type": "name", "properties": {"name": "EPSG:3006"}},
                    "features": [
                        {
                            "type": "Feature",
                            "id": "nr-haga",
                            "properties": {"nvrid": "2001234", "namn": "Änggårdsbergen", "beslutsdatum": "1975-06-12"},
                            "geometry": {"type": "Polygon", "coordinates": [_poly_3011_till_epsg(nv_poly_3011, 3006)]},
                        }
                    ],
                }
            ).encode("utf-8")
        if tn == N2K_LAGER:
            return json.dumps({"type": "FeatureCollection", "features": []}).encode("utf-8")
        return json.dumps({"type": "FeatureCollection", "features": []}).encode("utf-8")

    def fake_skogsstyrelsen(url: str) -> bytes:
        if "Nyckelbiotop" in url:
            return json.dumps(
                {
                    "type": "FeatureCollection",
                    "exceededTransferLimit": False,
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"Beteckn": "N 1001-2010", "Biotop1": "Ädellövskog", "Fastighetsagare": "HEMLIG AGARE"},
                            "geometry": {"type": "Polygon", "coordinates": [_poly_3011_till_epsg(nv_poly_3011, 4326)]},
                        }
                    ],
                }
            ).encode("utf-8")
        return json.dumps({"type": "FeatureCollection", "exceededTransferLimit": False, "features": []}).encode("utf-8")

    vmi_gpkg = tmp_path / "vmi_O.gpkg"
    gpd.GeoDataFrame(
        {"objekt": ["vmi_haga"]}, geometry=[Polygon(nv_poly_3011)], crs=EPSG_INTERN
    ).to_file(vmi_gpkg, driver="GPKG")
    vmi_zip_fil = tmp_path / "VMI_O_GIS_skikt.zip"
    with zipfile.ZipFile(vmi_zip_fil, "w") as zf:
        zf.write(vmi_gpkg, arcname="5_GIS_skikt/VMI_O_Goteborg_Bohus.gpkg")
    vmi_zip_bytes = vmi_zip_fil.read_bytes()

    def fake_vmi_lan(url: str) -> bytes:
        if url.endswith("/O_Goteborg_Bohus/"):
            return b'<html><a href="5_GIS_skikt/">5_GIS_skikt/</a></html>'
        if url.endswith("/5_GIS_skikt/"):
            return b'<html><a href="VMI_O_GIS_skikt.zip">VMI_O_GIS_skikt.zip</a></html>'
        if url.endswith("VMI_O_GIS_skikt.zip"):
            return vmi_zip_bytes
        raise AssertionError(f"Oväntad VMI-URL: {url}")

    # 2d. Mock SLU Artdatabanken SOS med 1 publik rödlistad + 1 skyddsklassad (OSL) + PII
    def fake_sos(_url: str, headers: Mapping[str, str], _kropp: bytes) -> bytes:
        assert headers.get("Ocp-Apim-Subscription-Key") == art_nyckel
        return json.dumps(
            {
                "totalCount": 2,
                "records": [
                    {
                        "taxon": {
                            "id": 102001,
                            "vernacularName": "Ekoxe",
                            "scientificName": "Lucanus cervus",
                            "redListCategory": "NT",
                            "isProtectedSpecies": True,
                        },
                        "event": {"startDate": "2024-06-18"},
                        "location": {
                            "decimalLatitude": round(lat_c, 6),
                            "decimalLongitude": round(lon_c, 6),
                            "coordinateUncertaintyInMeters": 25,
                            "accessLevel": "public",
                        },
                        "recordedBy": "Anna Observatör",
                    },
                    {
                        "taxon": {
                            "id": 102002,
                            "vernacularName": "Kungsörn",
                            "scientificName": "Aquila chrysaetos",
                            "redListCategory": "NT",
                            "isSensitiveSpecies": True,
                        },
                        "event": {"startDate": "2024-05-01"},
                        "location": {
                            "decimalLatitude": round(lat_c, 6),
                            "decimalLongitude": round(lon_c, 6),
                            "coordinateUncertaintyInMeters": 5000,
                            "isProtected": True,
                        },
                    },
                ],
            }
        ).encode("utf-8")

    res_v1 = mappa(
        v,
        version="v1",
        nycklar_fran=inga_nycklar,
        filhamtare=fh,
        hamta_trafikverket=True,
        hamta_vader_luft=True,
        hamta_skyddad_natur=True,
        hamta_artfynd=True,
        nyckel_forrad=miljo.adapter.nycklar,
        konto_id=konto_id,
        trafikverket_hamtare=fake_trafikverket,
        vader_hamtare=fake_smhi,
        luft_hamtare=fake_luft_timeout,
        skyddad_natur_hamtare=fake_nv_wfs,
        skogsstyrelsen_hamtare=fake_skogsstyrelsen,
        vmi_hamtare=fake_vmi_lan,
        artdatabanken_hamtare=fake_sos,
    )
    assert res_v1.nytt

    geo_rot = v["rot"] / "geo"
    manifest, _ = las_manifest(geo_rot, "goteborg-test@v1")
    assert manifest["kontrakt"] == KONTRAKT_GEOGRAFI
    for nyckel_lager in ("k1_rutnat", "radata", "nmd", "vmi", "trafik", "vader_luft", "skyddad_natur", "artfynd_publika"):
        assert nyckel_lager in manifest["lager"], f"Saknar lager.{nyckel_lager}"

    # Luftkvalitetstimeout ska ha gett graciös varning utan att krascha mappningen
    assert manifest["lager"]["vader_luft"]["har_bakgrund_luft"] is False
    assert any("bakgrund" in w.lower() or "luftkvalitet" in w.lower() or "timeout" in w.lower() for w in manifest["varningar"])

    # Hård OSL 20 kap. 1 §-spärr + 0 PII + 0 nyckelläckage över HELA geo_rot
    assert manifest["lager"]["artfynd_publika"]["antal_skyddsklassade_filtrerade"] == 1
    assert manifest["lager"]["artfynd_publika"]["antal_publika_fynd"] == 1
    for fil in geo_rot.rglob("*"):
        if fil.is_file() and fil.suffix.lower() in (".json", ".geojson", ".toml", ".csv"):
            txt = fil.read_text(encoding="utf-8", errors="ignore")
            for forbjuden in ("Kungsörn", "Aquila chrysaetos", "Anna Observatör", "HEMLIG AGARE", tv_nyckel, art_nyckel):
                assert forbjuden not in txt, f"Förbjuden sträng {forbjuden!r} hittades i {fil}"

    # v2: baskarta-fallback (trafik_fran_baskarta=True, ingen Trafikverket-nyckel) + hamta_artfynd utan nyckel
    res_v2 = mappa(
        v,
        version="v2",
        nycklar_fran=inga_nycklar,
        filhamtare=fh,
        trafik_fran_baskarta=True,
        hamta_artfynd=True,
    )
    assert res_v2.nytt
    m2, _ = las_manifest(geo_rot, "goteborg-test@v2")
    assert "trafik" in m2["lager"]
    assert m2["lager"]["trafik"]["antal_lankar"] >= 1
    assert m2["lager"]["trafik"]["antal_med_adt"] == 0
    assert m2["lager"]["trafik"]["antal_utan_adt"] == m2["lager"]["trafik"]["antal_lankar"]
    # Kontrollera att inga påhittade ÅDT-värden finns i trafik_vagnat.gpkg
    gpkg_v2 = lager_mapp(geo_rot, m2["lager"]["trafik"]["sha256"]) / TRAFIK_GPKG_FIL
    gdf_v2 = gpd.read_file(gpkg_v2)
    assert gdf_v2["adt_fordon_dygn"].isna().all()
    assert gdf_v2["andel_tung_pct"].isna().all()
    assert gdf_v2["hastighet_km_h"].isna().all()
    # Saknad Artdatabanken-nyckel ger tydlig varning, inget fel och inget artfynd_publika-lager
    assert "artfynd_publika" not in m2["lager"]
    assert any("artdatabanken" in w.lower() for w in m2["varningar"])


# ---------------------------------------------------------------------------
# 3. Nya webb-API-endpoints med RBAC, CSRF, IDOR, 413, forval & max_flackar=120
# ---------------------------------------------------------------------------


def test_e2e_nya_webb_endpoints_rbac_csrf_idor_och_max_flackar_120(
    miljo: Miljo,  # noqa: F811
    tmp_path: Path,
) -> None:
    """Verifierar alla 5 nya HTTP-endpoints, CSRF, IDOR, roller, 413, SMHI UTCI-forval och max_flackar=120."""
    org = miljo.register.skapa_organisation("Göteborgs Stad")
    admin_id = miljo.register.forbered_konto("admin@example.test")
    miljo.register.gor_organisationsadmin(admin_id, org)
    pid = miljo.register.skapa_projekt(org, "Haga Integrering")
    miljo.register.bjud_in(admin_id, pid, "anna@example.test", "utredare")
    miljo.register.bjud_in(admin_id, pid, "bo@example.test", "lasare")

    webb_anna, _ = _inloggad(miljo, ANNA)
    webb_bo, _ = _inloggad(miljo, BO)
    webb_di, _ = _inloggad(miljo, DI)

    app, pmapp, geo_rot, manifest_fil, _ = _bygg_komplett_projekt_med_alla_lager(
        tmp_path, konto=miljo.adapter, projekt_id=pid
    )
    try:
        # 1. Oinloggad -> 401 på alla nya endpoints
        for metod, vag, kropp in (
            ("GET", f"/api/projekt/{pid}/trafik", None),
            ("POST", f"/api/projekt/{pid}/trafik", {"justeringar": []}),
            ("GET", f"/api/projekt/{pid}/vader_luft", None),
            ("POST", "/api/analys/luft_skiss", {"omrade_id": pid, "vagsegment": []}),
            ("GET", f"/api/projekt/{pid}/ekologi/skrivbord", None),
            ("POST", f"/api/projekt/{pid}/ekologi/faltlager", {"filnamn": "a.geojson", "innehall_text": "{}"}),
        ):
            st, _ = _http_json(app, None, metod, vag, kropp)
            assert st == 401, (metod, vag, st)

        # 2. IDOR: utomstående konto (Di) -> 403 på alla nya endpoints
        for metod, vag, kropp in (
            ("GET", f"/api/projekt/{pid}/trafik", None),
            ("POST", f"/api/projekt/{pid}/trafik", {"justeringar": []}),
            ("GET", f"/api/projekt/{pid}/vader_luft", None),
            ("POST", "/api/analys/luft_skiss", {"omrade_id": pid, "vagsegment": []}),
            ("GET", f"/api/projekt/{pid}/ekologi/skrivbord", None),
            ("POST", f"/api/projekt/{pid}/ekologi/faltlager", {"filnamn": "a.geojson", "innehall_text": "{}"}),
        ):
            st, _ = _http_json(app, webb_di, metod, vag, kropp)
            assert st == 403, (metod, vag, st)

        # 3. lasare (Bo): 200 på GET, 403 på POST
        st, trafik_get = _http_json(app, webb_bo, "GET", f"/api/projekt/{pid}/trafik")
        assert st == 200 and trafik_get["har_trafiklager"] is True
        assert trafik_get["sammanfattning"]["antal_lankar"] == 2
        assert trafik_get["sammanfattning"]["antal_med_adt"] == 1
        assert trafik_get["sammanfattning"]["antal_utan_adt"] == 1

        st, vl_get = _http_json(app, webb_bo, "GET", f"/api/projekt/{pid}/vader_luft")
        assert st == 200 and vl_get["har_vader_luft"] is True
        assert len(vl_get["vindros"]["sektorer"]) == 16
        assert vl_get["utci_forval"]["platsens_smhi_p95"] is not None

        st, vs_smhi = _http_json(
            app, webb_bo, "GET", f"/api/analys/varmestress?omrade_id={pid}&forval=platsens_smhi_p95"
        )
        assert st == 200 and vs_smhi["forval"] == "platsens_smhi_p95"
        assert vs_smhi["platsens_vader"]["station_namn"] == "Göteborg A"

        st, eko_skriv = _http_json(app, webb_bo, "GET", f"/api/projekt/{pid}/ekologi/skrivbord")
        assert st == 200 and eko_skriv["har_skrivbord"] is True
        assert eko_skriv["osl_varning"] == OSL_VARNING
        assert eko_skriv["sammanfattning"]["skyddade_omraden"]["antal"] == 1
        assert eko_skriv["sammanfattning"]["artfynd_publika"]["antal_skyddsklassade_filtrerade"] == 1

        for vag, kropp in (
            (f"/api/projekt/{pid}/trafik", {"justeringar": [{"lank_id": "baskarta_haga_2", "adt_fordon_dygn": 2500}]}),
            ("/api/analys/luft_skiss", {"omrade_id": pid, "vagsegment": []}),
            (f"/api/projekt/{pid}/ekologi/faltlager", {"filnamn": "a.geojson", "innehall_text": "{}"}),
        ):
            st, _ = _http_json(app, webb_bo, "POST", vag, kropp)
            assert st == 403, (vag, st)

        # 4. utredare (Anna) utan CSRF -> 403 på POST
        for vag, kropp in (
            (f"/api/projekt/{pid}/trafik", {"justeringar": [{"lank_id": "baskarta_haga_2", "adt_fordon_dygn": 2500}]}),
            ("/api/analys/luft_skiss", {"omrade_id": pid, "vagsegment": []}),
            (f"/api/projekt/{pid}/ekologi/faltlager", {"filnamn": "a.geojson", "innehall_text": "{}"}),
        ):
            st, _ = _http_json(app, webb_anna, "POST", vag, kropp, med_csrf=False)
            assert st == 403, (vag, st)

        # 5. utredare (Anna) med CSRF: spara trafikjustering utan att röra rådata
        mf = json.loads(manifest_fil.read_text(encoding="utf-8"))
        gpkg_sha_fore = sha256_fil(lager_mapp(geo_rot, mf["lager"]["trafik"]["sha256"]) / TRAFIK_GPKG_FIL)
        st, trafik_post = _http_json(
            app,
            webb_anna,
            "POST",
            f"/api/projekt/{pid}/trafik",
            {
                "justeringar": [
                    {
                        "lank_id": "baskarta_haga_2",
                        "adt_fordon_dygn": 2800.0,
                        "prognos_adt_fordon_dygn": 3600.0,
                        "hastighet_km_h": 30.0,
                        "andel_tung_pct": 5.0,
                    }
                ]
            },
        )
        assert st == 200
        assert trafik_post["sammanfattning"]["antal_med_adt"] == 2
        per_id = {f["id"]: f["properties"] for f in trafik_post["features"]}
        assert per_id["baskarta_haga_2"]["kallmarke"] == "Egen"
        assert per_id["baskarta_haga_2"]["prognos_adt_fordon_dygn"] == 3600.0
        assert sha256_fil(lager_mapp(geo_rot, mf["lager"]["trafik"]["sha256"]) / TRAFIK_GPKG_FIL) == gpkg_sha_fore

        # 6. POST /api/analys/luft_skiss (3A aktivt skissval med projektets väglänk)
        seg_koord = trafik_post["features"][0]["geometry"]["coordinates"]
        st, luft_svar = _http_json(
            app,
            webb_anna,
            "POST",
            "/api/analys/luft_skiss",
            {
                "omrade_id": pid,
                "anvand_skiss_schablon": True,
                "vagsegment": [
                    {
                        "id": "nvdb_haga_1",
                        "gatunamn": "Sprängkullsgatan",
                        "koordinater": seg_koord,
                        "adt_fordon_dygn": 9500.0,
                        "hastighet_km_h": 40.0,
                        "andel_tung_pct": 8.0,
                    }
                ],
            },
        )
        assert st == 200, luft_svar
        assert luft_svar["metodstatus"] == "skiss"
        assert luft_svar["ar_schablon"] is True
        assert luft_svar["nortrip_kord"] is True
        assert luft_svar["totalt"]["no2_arsmedel_ug_m3"] is not None
        assert luft_svar["totalt"]["pm10_arsmedel_ug_m3"] is not None

        # 7. POST /api/projekt/<id>/ekologi/faltlager (0 PII + 413 vid >10 MB)
        x0_p, y0_p = float(mf["karna_3011"][0]), float(mf["karna_3011"][1])
        falt_geojson = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:3011"}},
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "objekttyp": "nvi_objekt",
                        "nvi_klass": "2",
                        "biotop": "Ädellövskog",
                        "inventerare": "Hemlig Ekolog",
                        "fastighetsagare": "Privat Person",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [x0_p + 5.0, y0_p + 5.0],
                                [x0_p + 25.0, y0_p + 5.0],
                                [x0_p + 25.0, y0_p + 25.0],
                                [x0_p + 5.0, y0_p + 25.0],
                                [x0_p + 5.0, y0_p + 5.0],
                            ]
                        ],
                    },
                }
            ],
        }
        b64_payload = base64.b64encode(json.dumps(falt_geojson).encode("utf-8")).decode("ascii")
        st, falt_post = _http_json(
            app,
            webb_anna,
            "POST",
            f"/api/projekt/{pid}/ekologi/faltlager",
            {"filnamn": "nvi_haga.geojson", "innehall_base64": b64_payload},
        )
        assert st == 200, falt_post
        assert falt_post["antal_objekt"] == 1
        assert falt_post["per_nvi_klass"].get("2") == 1
        falt_dump = json.dumps(falt_post, ensure_ascii=False)
        assert "Hemlig Ekolog" not in falt_dump and "Privat Person" not in falt_dump

        # >10 MB nekas med 413
        st_413, _ = _http_raw(
            app,
            webb_anna,
            "POST",
            f"/api/projekt/{pid}/ekologi/faltlager?filnamn=stor.geojson",
            b"x" * (MAX_FALTLAGER_BYTES + 1024),
        )[:2]
        assert st_413 == 413

        # GET skrivbord inkluderar nu det uppladdade fältlagret
        st, eko_skriv2 = _http_json(app, webb_bo, "GET", f"/api/projekt/{pid}/ekologi/skrivbord")
        assert st == 200 and eko_skriv2["faltlager"]["antal_objekt"] == 1

        # 8. STANDARD_MAX_FLACKAR = 120 (ingen gammal hårdkodad 40-gräns i api.py)
        assert STANDARD_MAX_FLACKAR == 120
        st, eko_svar = _http_json(
            app,
            webb_anna,
            "POST",
            "/api/analys/ekologi",
            {"omrade_id": pid, "natverk": "barrskog-nacka", "max_flackar": 120},
        )
        assert st == 200, eko_svar

        # 9. Bullerspärrar oförändrade: nord2000_konform=False och rapport_sparrad=True
        st, buller_svar = _http_json(
            app,
            webb_anna,
            "POST",
            "/api/analys/buller",
            {
                "omrade_id": pid,
                "mottagarhojd_m": 2.0,
                "raster_cell_m": 25.0,
                "segment_langd_m": 25.0,
                "fasad_steg_m": 25.0,
                "max_vaningar": 1,
                "reflexordning": 0,
                "vagsegment": [
                    {
                        "id": "nvdb_haga_1",
                        "koordinater": seg_koord,
                        "adt_fordon_dygn": 9500.0,
                        "hastighet_km_h": 40.0,
                        "andel_tung_pct": 8.0,
                    }
                ],
            },
        )
        assert st == 200, buller_svar
        assert buller_svar["nord2000_konform"] is False
        assert buller_svar["rapport_sparrad"] is True
        assert buller_svar["metodstatus"] == "skiss"
    finally:
        app.stang()


# ---------------------------------------------------------------------------
# 4. Ekologirapport med automatisk skrivbordskoppling + 0 tomma PDF-sidor + svf_mark
# ---------------------------------------------------------------------------


def test_e2e_ekologirapport_automatisk_skrivbordsforsorjning_0_tomma_pdf_och_svf_mark(
    tmp_path: Path,
) -> None:
    """RapportTjanst förser automatiskt ekologirapporter från projektets K46-geografi med 9/9 kontroll och 0 tomma sidor."""
    app, pmapp, geo_rot, manifest_fil, pid = _bygg_komplett_projekt_med_alla_lager(
        tmp_path, konto=None, projekt_id="haga-rapp-proj"
    )
    try:
        mf = json.loads(manifest_fil.read_text(encoding="utf-8"))
        k1_mapp = geo_rot / "_lager" / mf["lager"]["k1_rutnat"]["sha256"]
        ekologi_fil = pmapp / "fragor" / "ekologi.toml"
        ekologi_fil.write_text(
            'kontrakt = "K20/v1"\n'
            'fraga_id = "haga-ekologi-int"\n'
            'beskrivning = "Ekologisk konnektivitet och skrivbordskontroll"\n\n'
            f'[scenarier.fore]\nbas = "{k1_mapp.as_posix()}"\n\n'
            f'[scenarier.efter]\nbas = "{k1_mapp.as_posix()}"\n\n'
            '[[analyser]]\nid = "ekologi"\nlosare = "ekologi.konnektivitet"\n'
            f'indata = {{ geografi = "{manifest_fil.as_posix()}" }}\n'
            'parametrar = { natverk = "barrskog-nacka" }\n\n'
            '[[jamforelser]]\nanalys = "ekologi"\n'
            'utdata = "stromtathet"\nfore = "fore"\nefter = "efter"\n',
            encoding="utf-8",
        )
        korning_id = "haga-ekologi-int"
        st_kor, _ = app.fragor.kor_fil_synkront(ekologi_fil, korning_id, projekt_id=pid)
        assert st_kor["status"] == "klar", st_kor

        tjanst = FlodenTjanst(app, rot=REPO, mcp_mapp=tmp_path / "mcp")
        rt = RapportTjanst(tjanst, lokala_forval={})
        utmapp = rt.kalla.utmapp(korning_id)

        # Ingen ekologi_skrivbord/-mapp får existera före RapportTjanst.skapa
        assert not (utmapp / "ekologi_skrivbord").exists()

        best = rt.bestallning_skapa(
            syfte="Skissa påverkan på ekologisk konnektivitet och skyddad natur",
            amnen=["ekologi"],
            projekt=pid,
            scenario={"fore": "fore", "efter": ["efter"]},
        )
        r = rt.skapa(projekt=pid, korningar=[korning_id], ai_stod=False, bestallning=best)

        # RapportTjanst ska automatiskt ha materialiserat skrivbordsfiler, adresser och OSL-varning
        assert (utmapp / "ekologi_skrivbord" / "skrivbord_sammanfattning.json").is_file()
        adresser = rt.adresser(r)
        assert "ekoskrivbord.skyddad_natur.antal_skyddade_omraden" in adresser
        assert "ekoskrivbord.artfynd_publika.antal_skyddsklassade_filtrerade" in adresser
        assert OSL_VARNING in r.korningar[0]["antaganden"]

        # webbflode.pastaenden_for ska automatiskt ge skrivbordsbelägg på EK-01, EK-02, EK-05, EK-06
        pastaenden = pastaenden_for(rt, r)
        per_fraga = {str(p["fraga_id"]): p for p in pastaenden}
        for fid in ("EK-01", "EK-02", "EK-05", "EK-06"):
            assert any(
                (b if isinstance(b, str) else str(b.get("adress", ""))).startswith(
                    "ekoskrivbord."
                )
                for b in per_fraga[fid]["belagg"]
            ), (fid, per_fraga[fid])
        for kw in pastaenden:
            rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)

        kontroll = rt.kontrollera(r.rapport_id)
        assert kontroll["godkand"], kontroll
        assert len(kontroll["punkter"]) == 9 and all(p["godkand"] for p in kontroll["punkter"])

        pdf_fil = rt.exportera(r.rapport_id, "pdf")
        antal_sidor, sidor = _text_pdf(pdf_fil)
        dok = bygg_dokument(rt.hamta(r.rapport_id), rt)
        pdf_allt = _norm(" ".join(sidor))
        assert "Checklista inför fältinventering (NVI)" in pdf_allt
        assert _norm(OSL_VARNING) in pdf_allt
        for idx, sida in enumerate(sidor, start=1):
            assert f"Sida {idx} ({antal_sidor})" in sida
            kropp = _kropps_text_utan_ram(sida, dok.sidhuvud, dok.sidfot)
            assert len(kropp) > 15, f"Tom PDF-sida {idx}/{antal_sidor}: {sida!r}"

        # Verifiera även svensk etikett för dagsljus.svf_mark i rapportbyggaren
        r_obj = rt.hamta(r.rapport_id)
        k0_med_svf = {
            **r_obj.korningar[0],
            "antaganden": [
                *r_obj.korningar[0]["antaganden"],
                "dagsljus.svf_mark; cellstorlek 2 m, vegetation utan.",
            ],
        }
        r_med_svf = r_obj.__class__(**{**r_obj.__dict__, "korningar": [k0_med_svf]})
        huvud = huvudtext(bygg_dokument(r_med_svf, rt))
        assert "Himmelsfaktor (SVF) på mark; cellstorlek 2 m" in huvud
        assert "dagsljus.svf_mark" not in huvud
    finally:
        app.stang()


# ---------------------------------------------------------------------------
# 5. MCP utv_geografi_mappa: valfria additiva flaggor & säkerhetsvalidering
# ---------------------------------------------------------------------------


def test_e2e_mcp_utv_geografi_mappa_nya_additiva_flaggor(
    demo: Demo,  # noqa: F811
    miljo: Miljo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verifierar att `utv_geografi_mappa` validerar `vmi_lan`, vidarebefordrar nya flaggor och inte läcker nycklar."""
    from starlette.testclient import TestClient

    from tests.floden_mcp.test_mcp_http import _initiera, anropa_verktyg
    from tests.floden_mcp.test_mcp_utvecklingslage import UtvStack, _skapa_superadmin

    _slå_på(miljo)
    _, sup_token = _skapa_superadmin(miljo)
    stack = UtvStack(demo, miljo, tmp_path / "mcp_utv", env={"FLODEN_MCP_UTVECKLINGSLAGE": "1"})

    fanga: list[dict[str, Any]] = []

    class FalskResultat:
        ref = "goteborg-test@v7"
        version = "v7"
        nytt = True
        manifest_sha256 = "a" * 64
        buffert_m = 400.0
        oppen_for_mcp = True
        varningar: tuple[str, ...] = ()
        manifest: dict[str, Any] = {
            "lager": {
                "k1_rutnat": {"sha256": "1" * 64, "rutor": ["mm_0_0"]},
                "trafik": {"sha256": "2" * 64},
                "vader_luft": {"sha256": "3" * 64},
                "skyddad_natur": {"sha256": "4" * 64},
                "artfynd_publika": {"sha256": "5" * 64},
            }
        }

    def fake_mappa_geografi(*args: Any, **kwargs: Any) -> FalskResultat:
        fanga.append({"args": args, "kwargs": kwargs})
        return FalskResultat()

    monkeypatch.setattr("geografi.mappa.mappa_geografi", fake_mappa_geografi)

    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        sess = _initiera(klient, {"Authorization": f"Bearer {sup_token}"})

        # Path traversal i vmi_lan stoppas omedelbart
        fel, text, _ = anropa_verktyg(
            klient,
            sess,
            "utv_geografi_mappa",
            {"geografi": "goteborg-test", "version": "v7", "vmi_lan": "../etc"},
        )
        assert fel and "vmi_lan" in text

        # Anrop utan nyckelkrävande flaggor skickar inte vidare nyckel_forrad
        fel1, text1, data1 = anropa_verktyg(
            klient,
            sess,
            "utv_geografi_mappa",
            {
                "geografi": "goteborg-test",
                "version": "v7",
                "villkor_kontrollerade": True,
                "trafik_fran_baskarta": True,
                "hamta_vader_luft": True,
                "hamta_skyddad_natur": True,
                "vmi_lan": "O_Goteborg_Bohus",
            },
        )
        assert not fel1, text1
        assert isinstance(data1, dict) and data1["ref"] == "goteborg-test@v7"
        assert fanga[-1]["kwargs"]["trafik_fran_baskarta"] is True
        assert fanga[-1]["kwargs"]["hamta_vader_luft"] is True
        assert fanga[-1]["kwargs"]["hamta_skyddad_natur"] is True
        assert fanga[-1]["kwargs"]["vmi_lan"] == "O_Goteborg_Bohus"
        assert "nyckel_forrad" not in fanga[-1]["kwargs"]

        # Med hamta_trafikverket / hamta_artfynd skickas konto.nycklar vidare
        fel2, text2, data2 = anropa_verktyg(
            klient,
            sess,
            "utv_geografi_mappa",
            {
                "geografi": "goteborg-test",
                "version": "v8",
                "villkor_kontrollerade": True,
                "hamta_trafikverket": True,
                "hamta_artfynd": True,
            },
        )
        assert not fel2, text2
        assert isinstance(data2, dict) and data2["ref"] == "goteborg-test@v7"
        assert fanga[-1]["kwargs"]["hamta_trafikverket"] is True
        assert fanga[-1]["kwargs"]["hamta_artfynd"] is True
        assert fanga[-1]["kwargs"]["nyckel_forrad"] is miljo.adapter.nycklar
    shutil.rmtree(tmp_path / "mcp_utv", ignore_errors=True)
