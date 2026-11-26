"""Korsvågs end-to-end-testsvit för Våg 5 (binder ihop B1–B14 och Våg 1–4).

Fyra rena, oberoende, deterministiska E2E-tester mot riktiga integrerade komponenter
(inga mockade fysikresultat, ingen nätverkstrafik, 0 PII i utdata):

1. `test_e2e_v5_geografi_flerrut_vattenytor_planera_och_alla_analys_endpoints`:
   - Syntetiskt 2-rutors K1 v2-geografiprojekt med sammanhängande vattenyta över skarven
     (`vattenytor_for_rutor` -> exakt 0,000 m steg), byggnader, NMD-lager och `NaN`-celler.
   - `floden geografi planera` + `planera_geografi` på samma avgränsning (rutantal,
     `uppskattning == True`, K38-dataklass för Göteborg resp. Stockholm).
   - `GET /api/analys/ekologi` (`lasare` -> 200 utan projektmutering) vs
     `POST /api/analys/ekologi` (`lasare` -> 403, `utredare` -> 200), samt
     `POST /api/analys/{bifurkationer, intervall, svf-intervall}` -> 200 med fysikaliska tal
     (`svf_max_medel < 1.0`, `bredd_max > 0`, `osakerhet_byggnad_m > 0`), samt
     `sankor`/`flodesvagar` -> 200 (ej 409) och `sektion` på icke-`max_djup` -> 400.
2. `test_e2e_v5_steg3_rapporter_lage_idag_ekologi_luft_och_k38_mcp`:
   - `RapportJobbTjanst` + `FlodenApiApp`: (a) läget idag-skyfallsrapport (utan 409, >= 1 karta
     via `rapport.figurer`), (b) Ekologi-rapport (utan 409, distinkta kartor `stromtathet` +
     `kansighet`, Word + PDF), (c) Luft-rapport med extern timserie-CSV + `bakgrund_ug_m3`
     (utan 409, `luft.mkn` + Word + PDF).
   - K38 via MCP `resources/read` (`floden://rapport/{id}`): öppen rapport läses, stängd
     rapport blockeras utan innehållsläckage och loggas som `nekad_oppenhet`.
3. `test_e2e_v5_konto_backup_verifiera_halsa_och_noll_pii`:
   - Riktig SQLite WAL-kontodatabas med syntetiskt konto (`hemlig.person@example.test`) och
     krypterat nyckelvalv, `skapa_backup` (inkl. uppladdning till `LokalLagring`),
     `verifiera_backup`, `drifts_halsa`: `0o600`, giltig SHA-256 och 0 PII / 0 hemligheter.
4. `test_e2e_v5_tvad_skilda_kedjor_och_parallell_batch_dsm`:
   - Kopplad 1D/2D med 2 skilda kantstenskedjor separerade av byggnadsvägg (exakt 0,0 m
     läckage över barriären + utlopp per kedja).
   - Parallell `kor_batch_rutor(..., max_arbetare=2)` på K1 v2-rutor med DSM + 10 % `NaN`
     (`svf < 1.0` intill 20 m hus, `NaN` bevarade + manifestvarning, bitidentisk mot
     `max_arbetare=1`).
"""

from __future__ import annotations

import io
import json
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import docx
import numpy as np
import pytest
import rasterio
from cryptography.fernet import Fernet
from mcp import Client
from mcp.shared.exceptions import MCPError
from PIL import Image

from data_inlasning.nmd import NmdLager, skriv_nmd
from demovarld import varld as demo_varld
from demovarld.demo import LITEN
from demovarld.fragor import EKOLOGI_NATVERK, text_vatten
from demovarld.luftserier import timserie_csv
from floden.cli import main as floden_main
from floden_mcp.dataklass import Policy
from floden_mcp.karna_api import FlodenTjanst
from floden_mcp.korlogg import Korlogg
from floden_mcp.server import bygg_server
from gemensamt.hash import sha256_fil
from gemensamt.manifest import las_manifest
from gemensamt.objektlagring import LokalLagring
from geografi.modell import KONTRAKT_GEOGRAFI, lager_mapp, lagra, skriv_manifest
from geografi.planera import planera_geografi
from karna.batch import BatchKonfig, kor_batch_rutor, skapa_k1_rutkorare
from karna.scenario import Planscenario, skriv_scenario
from kommuner.vattenytor import vattenytor_for_rutor
from konto.backup import BackupFel, drifts_halsa, skapa_backup, verifiera_backup
from konto.databas import Databas
from konto.nycklar import NyckelForrad
from konto.register import Register
from markmodell.byggnadsregister import ByggnadsKandidat, ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import HARDGJORT
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta
from rapport.figurer import FARG_BAKGRUND
from rapport.skissrapport import RapportTjanst
from sankgraf.typer import MARKSLAG_BYGGNAD
from tests.konto.conftest import (  # noqa: F401
    ANNA,
    ORIGIN,
    Miljo,
    Webblasare,
    idp,
    klocka,
    miljo,
)
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, las_geotiff, skriv_geotiff
from tvad.rannsten_1d import diskretisera_rannsten_1d, kor_kopplad_1d_2d
from webb.api import FlodenApiApp
from webb.projektkalla import KONTRAKT_PROJEKT

REPO = Path(__file__).resolve().parents[2]
DEMO = "demokvarteret"

BO = {
    "sub": "sub-bo-v5-e2e",
    "email": "bo@example.test",
    "email_verified": True,
    "name": "Bo Lasare",
}
DI = {
    "sub": "sub-di-v5-e2e",
    "email": "di@example.test",
    "email_verified": True,
    "name": "Di Utomstaende",
}


def _bygg_snabb_demovarld(rot: Path) -> tuple[Path, Path]:
    """Bygger demovärldens vatten-K1 (2 m-celler), K9-scenario och NMD utan 40 s förkörning."""
    varld_dir = rot / "varld"
    fragedir = rot / "fragor"
    fragedir.mkdir(parents=True, exist_ok=True)
    v = LITEN.varld()
    bas_v = demo_varld.bygg_ruta_vatten(v, varld_dir / "vatten" / "bas")
    recept = tuple(sha256_fil(f) for f in sorted(bas_v.glob("mm_*/recept.json")))
    skriv_scenario(
        Planscenario(
            "efter",
            recept,
            (demo_varld.andring_nytt_hus(v),),
            beskrivning="Nytt 18 m-hus i sydvästra gården.",
        ),
        varld_dir / "vatten" / "k9_nytt_hus.json",
    )
    nmd, x0, y1, cell = demo_varld.bygg_nmd(v)
    skriv_nmd(NmdLager(nmd, x0, y1, cell), varld_dir / "ekologi" / "nmd.tif")
    (fragedir / "demo_vatten.toml").write_text(
        text_vatten(v, fragedir, varld_dir), encoding="utf-8"
    )
    (varld_dir / "demo.json").write_text(
        json.dumps({"delar": {"demo_vatten": {"jobb": 1}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    return varld_dir, fragedir


# ------------------------------------------------------------------ Hjälpare


def _http_json(
    app: FlodenApiApp,
    webb: Webblasare | None,
    metod: str,
    sokvag: str,
    kropp: Any = None,
) -> tuple[int, dict[str, Any]]:
    rub: dict[str, str] = {}
    if webb is not None:
        rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
        if metod != "GET":
            rub["Origin"] = ORIGIN
            rub.update(webb.csrf_headers())
    data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
    status, _, svar = app.hantera_http(metod, sokvag, rub, lambda: data)
    return status, json.loads(svar.decode("utf-8")) if svar else {}


def _http_raw(
    app: FlodenApiApp,
    webb: Webblasare,
    metod: str,
    sokvag: str,
    kropp: Any = None,
) -> tuple[int, dict[str, str], bytes]:
    rub: dict[str, str] = {"Cookie": "; ".join(f"{k}={v}" for k, v in webb.kakor.items())}
    if metod != "GET":
        rub["Origin"] = ORIGIN
        rub.update(webb.csrf_headers())
    data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
    status, hs, raw = app.hantera_http(metod, sokvag, rub, lambda: data)
    return status, {k.lower(): v for k, v in hs}, raw


def _vanta_korning(app: FlodenApiApp, kid: str, max_s: float = 120.0) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        st = app.fragor.korning_status(kid)
        if st["status"] in ("klar", "fel"):
            assert st["status"] == "klar", st
            return
        time.sleep(0.1)
    raise AssertionError(f"Körning {kid} blev aldrig klar")


def _vanta_rapportjobb(
    app: FlodenApiApp, webb: Webblasare, jobb: dict[str, Any], max_s: float = 120.0
) -> dict[str, Any]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        st, data = _http_json(app, webb, "GET", jobb["status_url"])
        assert st == 200, data
        if data["status"] in ("klar", "fel"):
            return data
        time.sleep(0.1)
    raise AssertionError(f"Rapportjobb {jobb['jobb_id']} blev aldrig klart")


def _csv_tid_s_halt(lage: str) -> str:
    """Omvandlar demoserien till kontraktets timseriekolumner `tid_s,halt_ug_m3`."""
    rader = timserie_csv(lage).splitlines()
    ut = ["tid_s,halt_ug_m3"]
    for rad in rader[1:]:
        timme, halt = rad.split(",")
        ut.append(f"{int(timme) * 3600},{halt}")
    return "\n".join(ut) + "\n"


def _skriv_k1_v2_ruta(
    k1_rot: Path,
    ruta: Ruta,
    *,
    terrang_z: np.ndarray[Any, Any],
    mark_z: np.ndarray[Any, Any] | None = None,
    byggnader: tuple[ByggnadsPost, ...] = (),
) -> Path:
    """Skriver en K1 v2-rutkatalog (`terrang.tif`, `byggnader.gpkg` + v1-filer)."""
    e_min, n_min, e_max, n_max = ruta.yttre_bbox
    dx = ruta.upplosning_m
    mapp = k1_rot / ruta.namn
    mapp.mkdir(parents=True, exist_ok=True)
    terr32 = np.asarray(terrang_z, dtype=np.float32)
    mark32 = terr32.copy() if mark_z is None else np.asarray(mark_z, dtype=np.float32)
    osak = np.full(terr32.shape, 0.05, dtype=np.float32)
    slag = np.full(terr32.shape, HARDGJORT, dtype=np.uint8)
    skriv_geotiff(mapp / "terrang.tif", terr32, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "mark.tif", mark32, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "markslag.tif", slag, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_byggnader_gpkg(mapp / "byggnader.gpkg", byggnader, (e_min, n_min, e_max, n_max))
    return mapp


# ------------------------------------------------------------------ 1. Geografi + Planera + Analys


def test_e2e_v5_geografi_flerrut_vattenytor_planera_och_alla_analys_endpoints(
    tmp_path: Path,
    miljo: Miljo,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Korsvågs-E2E: 2-rutors K1 v2 med skarvfri vattenyta (0,000 m steg), byggnader, NMD och NaN,
    `floden geografi planera`, `/api/analys/*` med roller, `sankor`/`flodesvagar` och `sektion`."""
    # Placera två 100×100 m-rutor (dx=2 m) så skarven ligger exakt på E = 150 500 m
    # (vilket även korsar standardrutnätets 500 m-gräns i `planera_geografi`).
    e0, n0 = 150400.0, 6580000.0
    storlek_m, dx = 100.0, 2.0
    r_vast = Ruta(e0, n0, storlek_m, 0.0, dx)
    r_ost = Ruta(e0 + storlek_m, n0, storlek_m, 0.0, dx)
    x0, y0, x1, y1 = e0, n0, e0 + 2.0 * storlek_m, n0 + storlek_m

    def _bygg_ra_terrang(ruta: Ruta, *, med_nan: bool) -> np.ndarray[Any, Any]:
        ex, ny = ruta.cell_centra_en()
        gx, gy = np.meshgrid(ex, ny)
        # Grundmark runt +10 m med svag lutning in mot två tydliga lokala sänkor i södra halvan
        z = np.full(gx.shape, 10.5, dtype=np.float32)
        dist_v = np.hypot(gx - (e0 + 50.0), gy - (n0 + 30.0))
        dist_o = np.hypot(gx - (e0 + 150.0), gy - (n0 + 30.0))
        z -= (1.4 * np.exp(-((dist_v / 22.0) ** 2))).astype(np.float32)
        z -= (1.2 * np.exp(-((dist_o / 22.0) ** 2))).astype(np.float32)
        # Sammanhängande kanal tvärs över rutskarven vid y i [n0+72, n0+86] (botten -3,0 m)
        kanal = (gy >= n0 + 72.0) & (gy <= n0 + 86.0)
        z[kanal] = np.float32(-3.0)
        # Äkta NaN-celler i nordvästra hörnet (utanför kanal och byggnader)
        if med_nan:
            z[0:2, 0:4] = np.nan
        return z

    z_vast_ra = _bygg_ra_terrang(r_vast, med_nan=True)
    z_ost_ra = _bygg_ra_terrang(r_ost, med_nan=False)
    assert int(np.count_nonzero(np.isnan(z_vast_ra))) == 8

    # Sammanhängande kanalens stränder har avsiktligt olika höjd i väst (+1,0 m) och öst (+1,4 m):
    # `vattenytor_for_rutor` ska ge en enda global mediannivå (1,2 m) och 0,000 m steg över skarven.
    strandlinjer = [
        np.array(
            [
                [e0, n0 + 72.0, 1.0],
                [e0 + 99.0, n0 + 72.0, 1.0],
                [e0 + 101.0, n0 + 72.0, 1.4],
                [e0 + 200.0, n0 + 72.0, 1.4],
            ],
            dtype=np.float64,
        ),
        np.array(
            [
                [e0, n0 + 86.0, 1.0],
                [e0 + 99.0, n0 + 86.0, 1.0],
                [e0 + 101.0, n0 + 86.0, 1.4],
                [e0 + 200.0, n0 + 86.0, 1.4],
            ],
            dtype=np.float64,
        ),
    ]
    (m_vast, niva_vast), (m_ost, niva_ost) = vattenytor_for_rutor(
        [(r_vast, z_vast_ra), (r_ost, z_ost_ra)],
        strandlinjer,
        marginal_m=0.2,
        max_avstand_m=3.0,
    )
    skarv_vatten = m_vast[:, -1] & m_ost[:, 0]
    assert int(np.count_nonzero(skarv_vatten)) >= 5
    steg_skarv_m = float(np.max(np.abs(niva_vast[skarv_vatten, -1] - niva_ost[skarv_vatten, 0])))
    assert steg_skarv_m == 0.0
    assert float(np.median(niva_vast[m_vast])) == pytest.approx(1.2, abs=1e-5)
    assert float(np.median(niva_ost[m_ost])) == pytest.approx(1.2, abs=1e-5)

    # Bygg riktiga K1 v2-rutor med vattenytan inlagd (markslag=8), byggnader och bevarade NaN-celler
    z_vast = np.where(m_vast, niva_vast, z_vast_ra).astype(np.float32)
    z_ost = np.where(m_ost, niva_ost, z_ost_ra).astype(np.float32)
    slag_vast = np.full(r_vast.form_rc, HARDGJORT, dtype=np.uint8)
    slag_ost = np.full(r_ost.form_rc, HARDGJORT, dtype=np.uint8)
    slag_vast[m_vast] = np.uint8(8)
    slag_ost[m_ost] = np.uint8(8)

    hus_vast = ByggnadsKandidat(
        byggnad_id="hus_vast",
        polygon=(
            (e0 + 18.0, n0 + 44.0),
            (e0 + 38.0, n0 + 44.0),
            (e0 + 38.0, n0 + 62.0),
            (e0 + 18.0, n0 + 62.0),
        ),
        tak_rh2000=28.0,
        hojd_m=18.0,
        kalla="syntetisk_v5",
    )
    hus_ost = ByggnadsKandidat(
        byggnad_id="hus_ost",
        polygon=(
            (e0 + 162.0, n0 + 44.0),
            (e0 + 182.0, n0 + 44.0),
            (e0 + 182.0, n0 + 62.0),
            (e0 + 162.0, n0 + 62.0),
        ),
        tak_rh2000=29.0,
        hojd_m=19.0,
        kalla="syntetisk_v5",
    )

    k1_tmp = tmp_path / "bygge_k1"
    bygg_markmodell_ruta(
        r_vast,
        k1_tmp,
        grund_z=z_vast,
        markslag_grund=slag_vast,
        byggnadskandidater=(hus_vast,),
        byggnadshojd_fran_laser=False,
        kodversion="v5-e2e",
        start_tid="2026-11-26T12:00:00+00:00",
    )
    bygg_markmodell_ruta(
        r_ost,
        k1_tmp,
        grund_z=z_ost,
        markslag_grund=slag_ost,
        byggnadskandidater=(hus_ost,),
        byggnadshojd_fran_laser=False,
        kodversion="v5-e2e",
        start_tid="2026-11-26T12:00:00+00:00",
    )

    # Bekräfta att 0,000 m vattensteg och NaN-celler finns kvar i utskrivna K1 v2-rutor
    terr_v_ut, _ = las_geotiff(k1_tmp / r_vast.namn / "terrang.tif")
    terr_o_ut, _ = las_geotiff(k1_tmp / r_ost.namn / "terrang.tif")
    assert int(np.count_nonzero(np.isnan(terr_v_ut))) == 8
    assert float(np.max(np.abs(terr_v_ut[skarv_vatten, -1] - terr_o_ut[skarv_vatten, 0]))) == 0.0

    # --- `floden geografi planera` (`planera_geografi`) på exakt samma avgränsning ---
    avgr_fil = tmp_path / "omrade_v5.geojson"
    avgr_fil.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3011"}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"namn": "v5_flerruta"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan_gbg = planera_geografi(
        "goteborg",
        avgr_fil,
        upplosning_m=dx,
        buffert_m=0.0,
        rutstorlek_m=storlek_m,
        overlapp_m=0.0,
    )
    assert plan_gbg.antal_karnrutor == 2
    assert plan_gbg.karnrutor == (r_vast.namn, r_ost.namn)
    assert plan_gbg.uppskattning is True
    assert plan_gbg.dataklass == "oppen"
    assert plan_gbg.k38_varning is False

    rc_cli = floden_main(
        [
            "geografi",
            "planera",
            "stockholm",
            "--avgransning",
            str(avgr_fil),
            "--upplosning-m",
            "1.0",
            "--buffert-m",
            "0",
            "--json",
        ]
    )
    assert rc_cli == 0
    plan_sthlm_json = json.loads(capsys.readouterr().out)
    assert plan_sthlm_json["antal_karnrutor"] == 2
    assert plan_sthlm_json["uppskattning"] is True
    assert plan_sthlm_json["dataklass"] == "stangd"
    assert plan_sthlm_json["k38_varning"] is True

    # --- Lagra K1 v2 + NMD-lager i geografin och registrera 2-rutors geografiprojekt ---
    geo_rot = tmp_path / "geo"
    proj_rot = tmp_path / "projekt"
    k1_lager = lagra(geo_rot, k1_tmp)
    k1_lagrad = lager_mapp(geo_rot, k1_lager.sha256)

    nmd_tmp = tmp_path / "bygge_nmd"
    nmd_tmp.mkdir(parents=True)
    nmd_cell_m = 10.0
    nmd_b = int(np.ceil((x1 - x0) / nmd_cell_m)) + 4
    nmd_h = int(np.ceil((y1 - y0) / nmd_cell_m)) + 4
    klasser = np.full((nmd_h, nmd_b), 53, dtype=np.uint8)
    # Två tydliga barrskogsfläckar (NMD 111) över västra resp. östra rutan
    klasser[2 : nmd_h - 2, 2 : nmd_b // 2 - 1] = 111
    klasser[2 : nmd_h - 2, nmd_b // 2 + 1 : nmd_b - 2] = 111
    skriv_nmd(NmdLager(klasser, x0 - 20.0, y1 + 20.0, nmd_cell_m), nmd_tmp / "nmd.tif")
    nmd_lager = lagra(geo_rot, nmd_tmp)

    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "v5flerruta",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": [x0 + 4.0, y0 + 4.0, x1 - 4.0, y1 - 4.0],
            "gransen_3011": [x0, y0, x1, y1],
            "lager": {
                "k1_rutnat": {"sha256": k1_lager.sha256},
                "nmd": {"sha256": nmd_lager.sha256, "buffert_m": 150.0, "cell_m": nmd_cell_m},
            },
            "kallor": {
                "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk v5"},
                "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk v5"},
                "nmd": {"licens": "CC0-1.0", "kalla": "Syntetisk NMD v5"},
            },
            "oppen_for_mcp": True,
        },
    )

    reg = miljo.register
    org1 = reg.skapa_organisation("V5Kommun")
    org2 = reg.skapa_organisation("AnnanKommun")
    anna_id = reg.forbered_konto("anna@example.test")
    reg.gor_organisationsadmin(anna_id, org1)
    bo_id = reg.forbered_konto("bo@example.test")
    reg.lagg_till_medlem(bo_id, org1, None, "test")
    di_id = reg.forbered_konto("di@example.test")
    reg.gor_organisationsadmin(di_id, org2)

    pid, _ = reg.registrera_projekt_for_konto(anna_id, org1, "V5 Flerrutsprojekt")
    reg.bjud_in(anna_id, pid, "bo@example.test", "lasare")

    pmapp = proj_rot / "v5-flerrutsprojekt"
    fdir = pmapp / "fragor"
    fdir.mkdir(parents=True)
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "v5-flerrutsprojekt",
                "register_id": pid,
                "namn": "V5 Flerrutsprojekt",
                "kommun": "goteborg",
                "geografi": "v5flerruta@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [x0 + 6.0, y0 + 6.0, x1 - 6.0, y1 - 6.0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    rel_k1 = Path(os.path.relpath(k1_lagrad.resolve(), fdir.resolve())).as_posix()
    nmd_lagrad = lager_mapp(geo_rot, nmd_lager.sha256)
    rel_nmd = Path(os.path.relpath((nmd_lagrad / "nmd.tif").resolve(), fdir.resolve())).as_posix()
    (fdir / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\n'
        'fraga_id = "v5-flerrut-skyfall"\n'
        'beskrivning = "Skyfall på 2-rutors K1 v2-geografi"\n\n'
        f'[scenarier.bas]\nbas = "{rel_k1}"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 60.0 }\n",
        encoding="utf-8",
    )
    (fdir / "ekologi.toml").write_text(
        'kontrakt = "K20/v1"\n'
        'fraga_id = "v5-flerrut-ekologi"\n'
        'beskrivning = "Ekologi på 2-rutors K1 v2-geografi"\n\n'
        f'[scenarier.bas]\nbas = "{rel_k1}"\n\n'
        '[[analyser]]\nid = "konnektivitet"\nlosare = "ekologi.konnektivitet"\n'
        'parametrar = { natverk = "barrskog-nacka" }\n'
        f'indata = {{ nmd = "{rel_nmd}" }}\n',
        encoding="utf-8",
    )

    w_anna = miljo.webblasare()
    w_bo = miljo.webblasare()
    w_di = miljo.webblasare()
    assert w_anna.logga_in(miljo.idp, ANNA)[0] == 302
    assert w_bo.logga_in(miljo.idp, BO)[0] == 302
    assert w_di.logga_in(miljo.idp, DI)[0] == 302

    kv_mapp = tmp_path / "kartverktyg"
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[tmp_path / "ext_fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=miljo.adapter,
        geomapp=geo_rot,
        projektmapp=proj_rot,
        kartverktygsmapp=kv_mapp,
    )

    try:
        # 1. GET /api/analys/ekologi: lasare -> 200 utan projektmutering; utomstående -> 403
        filer_fore = sorted(p.relative_to(pmapp).as_posix() for p in pmapp.rglob("*"))
        st_eg, d_eg = _http_json(
            app, w_bo, "GET", f"/api/analys/ekologi?omrade_id={pid}&natverk=barrskog-nacka"
        )
        assert st_eg == 200, d_eg
        assert d_eg["format"] == "floden-ekologi/v1"
        assert d_eg["fore"]["ECA_m2"] > 0.0
        assert d_eg["fore"]["antal_flackar"] >= 2
        assert sorted(p.relative_to(pmapp).as_posix() for p in pmapp.rglob("*")) == filer_fore
        assert not (kv_mapp / pid).exists()
        assert (
            _http_json(
                app, w_di, "GET", f"/api/analys/ekologi?omrade_id={pid}&natverk=barrskog-nacka"
            )[0]
            == 403
        )

        # 2. POST /api/analys/ekologi: lasare -> 403 (B13), utredare -> 200
        eko_payload = {"omrade_id": pid, "natverk": "barrskog-nacka"}
        assert _http_json(app, w_bo, "POST", "/api/analys/ekologi", eko_payload)[0] == 403
        st_ep, d_ep = _http_json(app, w_anna, "POST", "/api/analys/ekologi", eko_payload)
        assert st_ep == 200, d_ep
        assert d_ep["fore"]["ECA_m2"] == pytest.approx(d_eg["fore"]["ECA_m2"], rel=1e-9)

        # 3. POST /api/analys/bifurkationer -> 200 med fysikaliska tal
        st_b, d_b = _http_json(app, w_anna, "POST", "/api/analys/bifurkationer", {"omrade_id": pid})
        assert st_b == 200, d_b
        assert d_b["format"] == "floden-bifurkation-felindikator/v1"
        assert d_b["felindikator"]["antal_aktiva_celler"] > 0
        assert d_b["felindikator"]["antal_noder_totalt"] >= 2

        # 4. POST /api/analys/intervall -> 200 med fysikaliska tal och osäkerhetsspridning
        st_i, d_i = _http_json(
            app,
            w_anna,
            "POST",
            "/api/analys/intervall",
            {"omrade_id": pid, "referensregn_mm": 50.0, "klimatfaktor_intervall": [1.0, 1.3]},
        )
        assert st_i == 200, d_i
        assert d_i["format"] == "floden-vatten-intervall/v1"
        sm = d_i["sammanfattning"]
        assert sm["storsta_max_djup_m"] > 0.0
        assert sm["storsta_max_djup_m"] >= sm["storsta_min_djup_m"]
        assert d_i["max_volymfel_rel"] < 1e-2

        # 5. POST /api/analys/svf-intervall -> 200 med fysikaliska tal (svf < 1.0, osäkerhet > 0)
        svf_payload = {
            "omrade_id": pid,
            "osakerhet_byggnad_m": 1.5,
            "antal_riktningar": 8,
            "svf_troskel": 0.9,
        }
        st_s, d_s = _http_json(app, w_anna, "POST", "/api/analys/svf-intervall", svf_payload)
        assert st_s == 200, d_s
        assert d_s["format"] == "floden-dagsljus-svf-intervall/v1"
        assert 0.0 < d_s["svf_min_medel"] < d_s["svf_max_medel"] < 1.0
        assert d_s["osakerhet_byggnad_m"] > 0.0
        assert d_s["bredd_max"] > 0.0

        # 6. Flerruts `sankor` & `flodesvagar` -> 200 (ej 409)
        st_vk, _ = app.fragor.kor_synkront("skyfall", "k-v5-skyfall", projekt_id=pid)
        assert st_vk["status"] == "klar"

        st_sank, d_sank = _http_json(
            app,
            w_bo,
            "GET",
            "/api/korningar/k-v5-skyfall/vektor/bas/sankor.json?min_volym_m3=0&min_djup_m=0",
        )
        assert st_sank == 200, d_sank
        assert d_sank["type"] == "FeatureCollection"
        assert d_sank["slag"] == "sankor"
        assert len(d_sank["features"]) >= 1

        st_flod, d_flod = _http_json(
            app,
            w_bo,
            "GET",
            "/api/korningar/k-v5-skyfall/vektor/bas/flodesvagar.json?min_yta_m2=20",
        )
        assert st_flod == 200, d_flod
        assert d_flod["type"] == "FeatureCollection"
        assert d_flod["slag"] == "flodesvagar"
        assert len(d_flod["features"]) >= 1

        # 7. `sektion` på icke-`max_djup`-lager (`stromtathet`) -> 400; med `max_djup` -> 200
        st_ek, _ = app.fragor.kor_synkront("ekologi", "k-v5-ekologi", projekt_id=pid)
        assert st_ek["status"] == "klar"
        eko_res = app.fragor.resultat("k-v5-ekologi")
        strom_lager = next(la["id"] for la in eko_res["lager"] if la["roll"] == "stromtathet")
        vat_res = app.fragor.resultat("k-v5-skyfall")
        djup_lager = next(la["id"] for la in vat_res["lager"] if la["roll"] == "max_djup")

        linje = [[e0 + 10.0, n0 + 30.0], [e0 + 190.0, n0 + 30.0]]
        st_sek_fel, d_sek_fel = _http_json(
            app,
            w_bo,
            "POST",
            f"/api/kartverktyg/{pid}/sektion",
            {"punkter": linje, "korning_id": "k-v5-ekologi", "fore_lager": strom_lager},
        )
        assert st_sek_fel == 400, d_sek_fel
        assert "max_djup" in d_sek_fel["fel"]

        st_sek_ok, d_sek_ok = _http_json(
            app,
            w_bo,
            "POST",
            f"/api/kartverktyg/{pid}/sektion",
            {"punkter": linje, "korning_id": "k-v5-skyfall", "fore_lager": djup_lager},
        )
        assert st_sek_ok == 200, d_sek_ok
        assert any(v is not None and v > 0.0 for v in d_sek_ok["vatten_djup_fore_m"])
    finally:
        app.stang()


# ------------------------------------------------------------------ 2. Steg 3 Rapporter + K38 MCP


def test_e2e_v5_steg3_rapporter_lage_idag_ekologi_luft_och_k38_mcp(
    miljo: Miljo,  # noqa: F811
    tmp_path: Path,
) -> None:
    """Korsvågs-E2E: Steg 3 läget idag-skyfall (figurer.py), Ekologi (stromtathet + kansighet),
    Luft (extern timserie-CSV + bakgrund_ug_m3 -> luft.mkn), samt K38-spärr i MCP resources/read."""
    rot = tmp_path / "rapport_e2e"
    varld_dir, fragedir_demo = _bygg_snabb_demovarld(rot / "demo")
    projektmapp = rot / "projekt"
    rapportmapp = rot / "rapporter"
    korningsmapp = rot / "korningar"
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[fragedir_demo],
        cachemapp=rot / "cache",
        korningsmapp=korningsmapp,
        kartverktygsmapp=rot / "kartverktyg",
        datamappar=[rot / "data"],
        rapportmapp=rapportmapp,
        projektmapp=projektmapp,
        konto=miljo.adapter,
    )
    app.fragor.demomapp = varld_dir

    org = miljo.register.skapa_organisation("Rapportkommun")
    miljo.register.lagg_till_medlem(
        miljo.register.forbered_konto("anna@example.test"), org, None, "test"
    )
    w_anna = miljo.webblasare()
    assert w_anna.logga_in(miljo.idp, ANNA)[0] == 302
    url = f"/api/projekt/{DEMO}/rapporter"

    try:
        # --- (a) Läget idag-skyfallsrapport (utan 409, >= 1 karta via rapport.figurer) ---
        kor = app.kartverktyg.kor_lage_idag(DEMO, ["vatten.skyfall"])
        vat_kid = kor["fragor"][0]["korning"]["korning_id"]
        _vanta_korning(app, vat_kid)

        st_sk, jobb_sk = _http_json(app, w_anna, "POST", url, {"amne": "skyfall"})
        assert st_sk == 202, jobb_sk
        assert jobb_sk["underlag"]["slag"] == "lage_idag"
        slut_sk = _vanta_rapportjobb(app, w_anna, jobb_sk)
        assert slut_sk["status"] == "klar", slut_sk
        assert slut_sk["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}
        assert set(slut_sk["lankar"]) == {"pdf", "word"}

        rap_sk_id = slut_sk["rapport_id"]
        rap_sk = json.loads((rapportmapp / rap_sk_id / "rapport.json").read_text(encoding="utf-8"))
        assert len(rap_sk["kartor"]) >= 1
        k_sk = rap_sk["kartor"][0]
        assert k_sk["meta"]["kartmotor"] == "rapport.figurer"
        assert k_sk["meta"]["figurstil"] == "skyfall_djup"
        with Image.open(rapportmapp / rap_sk_id / k_sk["fil"]) as img:
            assert img.format == "PNG"
            assert img.getpixel((2, 2)) == FARG_BAKGRUND
        s_pdf, _, pdf_sk = _http_raw(app, w_anna, "GET", slut_sk["lankar"]["pdf"])
        s_doc, _, doc_sk = _http_raw(app, w_anna, "GET", slut_sk["lankar"]["word"])
        assert s_pdf == 200 and pdf_sk.startswith(b"%PDF")
        assert s_doc == 200 and doc_sk.startswith(b"PK")

        # --- (b) Ekologi-rapport (distinkta kartor `stromtathet` + `kansighet`, Word + PDF) ---
        fragedir = app.kartverktyg.fragedir
        fragedir.mkdir(parents=True, exist_ok=True)
        rel = lambda p: Path(os.path.relpath(p.resolve(), fragedir.resolve())).as_posix()  # noqa: E731
        ek_k20 = {
            "kontrakt": "K20/v1",
            "fraga_id": "v5-ekologi-tva-roller",
            "beskrivning": "Ekologisk konnektivitet (skiss) med strömtäthet och kansighet.",
            "scenarier": {
                "fore": {"bas": rel(varld_dir / "vatten" / "bas")},
                "efter": {
                    "bas": rel(varld_dir / "vatten" / "bas"),
                    "k9": [rel(varld_dir / "vatten" / "k9_nytt_hus.json")],
                },
            },
            "analyser": [
                {
                    "id": "konnektivitet",
                    "losare": "ekologi.konnektivitet",
                    "parametrar": {"natverk": EKOLOGI_NATVERK},
                    "indata": {"nmd": rel(varld_dir / "ekologi" / "nmd.tif")},
                }
            ],
            "jamforelser": [
                {
                    "analys": "konnektivitet",
                    "utdata": "stromtathet",
                    "fore": "fore",
                    "efter": "efter",
                    "troskel": 0.01,
                },
                {
                    "analys": "konnektivitet",
                    "utdata": "kansighet",
                    "fore": "fore",
                    "efter": "efter",
                    "troskel": 0.01,
                },
            ],
        }
        (fragedir / "v5-ekologi-tva-roller.json").write_text(
            json.dumps(ek_k20, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        ek_kid = "v5-ekologi-tva-roller-run"
        app.fragor.kor_synkront("v5-ekologi-tva-roller", ek_kid, projekt_id=DEMO)

        st_ek, jobb_ek = _http_json(
            app, w_anna, "POST", url, {"amne": "ekologi", "korning_id": ek_kid}
        )
        assert st_ek == 202, jobb_ek
        slut_ek = _vanta_rapportjobb(app, w_anna, jobb_ek)
        assert slut_ek["status"] == "klar", slut_ek
        assert slut_ek["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}

        rap_ek = json.loads(
            (rapportmapp / slut_ek["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
        )
        assert rap_ek["amne"] == "ekologi"
        roller = {k["meta"]["roll"] for k in rap_ek["kartor"]}
        assert roller == {"stromtathet", "kansighet"}
        strom_bas = next(
            k for k in rap_ek["kartor"] if k["meta"]["roll"] == "stromtathet" and k["typ"] == "bas"
        )
        kans_bas = next(
            k for k in rap_ek["kartor"] if k["meta"]["roll"] == "kansighet" and k["typ"] == "bas"
        )
        assert strom_bas["lager_id"] != kans_bas["lager_id"]
        assert strom_bas["png_sha256"] != kans_bas["png_sha256"]

        s_ek_pdf, _, pdf_ek = _http_raw(app, w_anna, "GET", slut_ek["lankar"]["pdf"])
        s_ek_doc, _, doc_ek = _http_raw(app, w_anna, "GET", slut_ek["lankar"]["word"])
        assert s_ek_pdf == 200 and pdf_ek.startswith(b"%PDF")
        assert s_ek_doc == 200 and doc_ek.startswith(b"PK")
        assert len(docx.Document(io.BytesIO(doc_ek)).inline_shapes) == 6

        # --- (c) Luft-rapport med extern timserie-CSV + bakgrund_ug_m3 (`luft.mkn`, Word + PDF) ---
        st_lu, jobb_lu = _http_json(
            app,
            w_anna,
            "POST",
            url,
            {
                "amnen": ["luft"],
                "luft": {
                    "serie_fore_csv": _csv_tid_s_halt("fore"),
                    "serie_efter_csv": _csv_tid_s_halt("efter"),
                    "bakgrund_ug_m3": 12.0,
                    "osakerhet_halt_rel": 0.15,
                    "osakerhet_bakgrund_ug_m3": 2.0,
                },
                "bestallning": {"syfte": "V5 E2E luft-MKN med extern timserie och bakgrundshalt"},
            },
        )
        assert st_lu == 202, jobb_lu
        slut_lu = _vanta_rapportjobb(app, w_anna, jobb_lu)
        assert slut_lu["status"] == "klar", slut_lu
        assert slut_lu["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}

        rap_lu = json.loads(
            (rapportmapp / slut_lu["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
        )
        assert rap_lu["amne"] == "luft"
        assert rap_lu["fragor"] == [f"LU-{i:02d}" for i in range(1, 16)]
        assert [m["losare"] for m in rap_lu["metoder"]] == ["luft.mkn"]
        man_fore = json.loads(
            (korningsmapp / jobb_lu["korning_id"] / "resultat" / "mkn_fore" / "bas" / "run.json")
            .read_text(encoding="utf-8")
        )
        assert man_fore["parametrar"]["bakgrund_ug_m3"] == 12.0
        assert man_fore["matvarden"]["antal_hela_dygn"] == 365

        s_lu_pdf, _, pdf_lu = _http_raw(app, w_anna, "GET", slut_lu["lankar"]["pdf"])
        s_lu_doc, _, doc_lu = _http_raw(app, w_anna, "GET", slut_lu["lankar"]["word"])
        assert s_lu_pdf == 200 and pdf_lu.startswith(b"%PDF")
        assert s_lu_doc == 200 and doc_lu.startswith(b"PK")

        # --- (d) K38 via MCP `resources/read` (`floden://rapport/{id}`) ---
        stangt_pid = "9" * 32
        stangt_mapp = projektmapp / "stockholm-stangt"
        (stangt_mapp / "fragor").mkdir(parents=True)
        (stangt_mapp / "projekt.json").write_text(
            json.dumps(
                {
                    "kontrakt": KONTRAKT_PROJEKT,
                    "projekt_id": "stockholm-stangt",
                    "register_id": stangt_pid,
                    "namn": "Stockholm stängt",
                }
            ),
            encoding="utf-8",
        )
        (stangt_mapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = false\nlicens = "intern"\n'
            'kalla = "Stockholms baskarta"\n',
            encoding="utf-8",
        )

        # In-process MCP-server mot exakt samma rapport- och projektarkiv (inloggning av)
        # för att renodlat verifiera K38-öppenhetsspärren på `floden://rapport/{id}`.
        mcp_app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[fragedir_demo],
            cachemapp=rot / "cache",
            korningsmapp=korningsmapp,
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot / "data"],
            rapportmapp=rapportmapp,
            projektmapp=projektmapp,
        )
        mcp_app.fragor.demomapp = varld_dir
        try:
            mcp_tjanst = FlodenTjanst(mcp_app, rot=REPO, mcp_mapp=rot / "mcp")
            arkiv = RapportTjanst(mcp_tjanst, rapportmapp=rapportmapp).arkiv
            stangd_rap_id = "r-deadbeef0001"
            hemlig_titel = "HEMLIG_K38_STOCKHOLM_BASKARTA_RAPPORT"
            arkiv.spara(
                arkiv.las(rap_sk_id).med(
                    rapport_id=stangd_rap_id,
                    projekt=stangt_pid,
                    titel=hemlig_titel,
                )
            )

            mcp_logg = Korlogg(rot / "mcp" / "korlogg.jsonl")
            mcp_server = bygg_server(mcp_tjanst, policy=Policy(), logg=mcp_logg, miljo={})

            async def _las_resurs(uri: str) -> tuple[Any | None, MCPError | None]:
                async with Client(mcp_server) as klient:
                    try:
                        return await klient.read_resource(uri), None
                    except MCPError as exc:
                        return None, exc

            # Öppen rapport går att läsa via MCP resources/read
            res_ok, fel_ok = anyio.run(lambda: _las_resurs(f"floden://rapport/{rap_sk_id}"))
            assert fel_ok is None and res_ok is not None
            assert json.loads(res_ok.contents[0].text)["rapport_id"] == rap_sk_id

            # Stängd rapport blockeras av K38, läcker ingen titel/JSON och loggas
            res_st, fel_st = anyio.run(lambda: _las_resurs(f"floden://rapport/{stangd_rap_id}"))
            assert res_st is None and fel_st is not None
            assert "inte öppna data" in str(fel_st) or "inte är öppna" in str(fel_st)
            assert hemlig_titel not in str(fel_st)
            nekade = [rad for rad in mcp_logg.las() if rad["utfall"] == "nekad_oppenhet"]
            assert any(
                rad["verktyg"] == f"resurs:floden://rapport/{stangd_rap_id}" for rad in nekade
            )
        finally:
            mcp_app.stang()
    finally:
        app.stang()


# ------------------------------------------------------------------ 3. Konto-backup + Hälsa + 0 PII


def test_e2e_v5_konto_backup_verifiera_halsa_och_noll_pii(tmp_path: Path) -> None:
    """Korsvågs-E2E: SQLite WAL-kontodatabas + krypterat nyckelvalv -> `skapa_backup` (inkl.
    `LokalLagring`), `verifiera_backup`, `drifts_halsa`: `0o600`, SHA-256 och 0 PII/hemligheter."""
    fernet_nyckel = Fernet.generate_key().decode("ascii")
    epost = "hemlig.person@example.test"
    namn = "Hemlig Person"
    api_hemlighet = "sk-ant-syntetisk-v5-hemlig-9876543210"
    s3_access = "AKIA_V5_HEMLIG_ACCESS"
    s3_secret = "SUPER_V5_HEMLIG_SECRET"
    url_losen = "topphemligt-losenord-v5"

    db_fil = tmp_path / "konto" / "konto.sqlite"
    db = Databas(db_fil)
    reg = Register(db)
    konto_id = reg.forbered_konto(epost)
    with db.transaktion() as c:
        c.execute(
            "UPDATE konto SET namn = ?, epost_verifierad = 1 WHERE id = ?",
            (namn, konto_id),
        )
    reg.skapa_organisation("V5 Teststad", agare=konto_id)
    NyckelForrad(reg, hemlighet=fernet_nyckel).spara(
        konto_id, "anthropic", {"nyckel": api_hemlighet}
    )

    malmapp = tmp_path / "backuper"
    lagring = LokalLagring(tmp_path / "objektlager")
    nu = datetime(2026, 11, 26, 14, 0, 0, tzinfo=UTC)

    res = skapa_backup(
        db_fil,
        malmapp,
        objektlagring=lagring,
        s3_prefix="backup/konto",
        nu=nu,
    )
    assert res.backup_fil.is_file() and res.manifest_fil.is_file()
    assert stat.S_IMODE(malmapp.stat().st_mode) == 0o700
    assert stat.S_IMODE(res.backup_fil.stat().st_mode) == 0o600
    assert stat.S_IMODE(res.manifest_fil.stat().st_mode) == 0o600
    assert res.integrity_check == "ok"
    assert res.foreign_key_check_ok is True
    assert res.sha256 == sha256_fil(res.backup_fil)
    assert res.tabellrader["konto"] == 1
    assert res.tabellrader["konto_nyckel"] == 1

    # Uppladdning till LokalLagring verifierad med identisk SHA-256
    assert len(res.s3_nycklar) == 2
    for nyckel in res.s3_nycklar:
        assert lagring.finns(nyckel)
    assert sha256_fil(lagring.rot / res.s3_nycklar[0]) == res.sha256

    # Verifiera lokal backup + upptäckt av manipulerad kopia
    ver = verifiera_backup(res.backup_fil)
    assert ver.ok is True
    assert ver.manifest_verifierat is True
    assert ver.sha256 == res.sha256
    assert ver.integrity_check == "ok"
    assert ver.foreign_key_check_ok is True

    kopia_mapp = tmp_path / "manipulerad"
    kopia_mapp.mkdir()
    manip_fil = kopia_mapp / res.backup_fil.name
    manip_man = kopia_mapp / res.manifest_fil.name
    ra = bytearray(res.backup_fil.read_bytes())
    ra[-1] ^= 0xFF
    manip_fil.write_bytes(bytes(ra))
    manip_man.write_text(res.manifest_fil.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(BackupFel, match="SHA-256"):
        verifiera_backup(manip_fil)

    # Driftskontroll (`drifts_halsa`)
    miljo_env = {
        "FLODEN_NYCKELHEMLIGHET": fernet_nyckel,
        "FLODEN_S3_TYP": "s3",
        "FLODEN_S3_ENDPOINT": f"https://admin:{url_losen}@fsn1.your-objectstorage.com",
        "FLODEN_S3_BUCKET": "floden-v5-bucket",
        "FLODEN_S3_ACCESS_KEY": s3_access,
        "FLODEN_S3_SECRET_KEY": s3_secret,
    }
    halsa = drifts_halsa(db_sokvag=db_fil, miljo=miljo_env)
    assert halsa["ok"] is True
    assert halsa["status"] == "ok"
    assert halsa["databas"]["wal_aktivt"] is True
    assert halsa["databas"]["integrity_check"] == "ok"
    assert halsa["nyckelforrad"]["paslagen"] is True
    assert halsa["lagring"]["endpoint"] == "https://fsn1.your-objectstorage.com"

    # 0 PII och 0 hemligheter i lokalt manifest, uppladdat manifest, resultat och hälsosvar
    manifest_lokal = res.manifest_fil.read_text(encoding="utf-8")
    manifest_s3 = (lagring.rot / res.s3_nycklar[1]).read_text(encoding="utf-8")
    texter = (
        manifest_lokal,
        manifest_s3,
        json.dumps(res.till_dict(), ensure_ascii=False),
        json.dumps(ver.till_dict(), ensure_ascii=False),
        json.dumps(halsa, ensure_ascii=False),
    )
    for kanslig in (epost, namn, fernet_nyckel, api_hemlighet, s3_access, s3_secret, url_losen):
        for text in texter:
            assert kanslig not in text


# ------------------------------------------------------------------ 4. 1D/2D + Parallell batch DSM


def test_e2e_v5_tvad_skilda_kedjor_och_parallell_batch_dsm(tmp_path: Path) -> None:
    """Korsvågs-E2E: Kopplad 1D/2D med 2 skilda kantstenskedjor (0,0 m läckage över barriären)
    samt parallell `kor_batch_rutor(..., max_arbetare=2)` på K1 v2-rutor med DSM + `NaN`
    (`svf < 1.0` intill hus, `NaN` bevarade + manifestvarning, bitidentisk mot `max_arbetare=1`)."""
    # --- Del A: Kopplad 1D/2D med 2 skilda kantstenskedjor och 8 m byggnadsvägg emellan ---
    h, b, dx_1d = 42, 24, 1.0
    x_min, y_max = 150000.0, 6580042.0
    geo = GeoTransformInfo(
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=dx_1d,
        pixelstorlek_y=dx_1d,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    xx = np.broadcast_to(np.arange(b, dtype=np.float64)[None, :], (h, b))
    z = (15.0 - 0.02 * xx).astype(np.float32).copy()
    markslag = np.full((h, b), 1, dtype=np.uint8)
    markslag[16:24, :] = MARKSLAG_BYGGNAD
    z[16:24, :] = np.float32(25.0)

    y_norr, y_soder = y_max - 8.0, y_max - 34.0
    x0_k, x1_k = x_min + 4.0, x_min + 16.0
    z_n0, z_n1 = 15.0 - 0.02 * 4.0, 15.0 - 0.02 * 16.0
    brytlinjer = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x0_k, y_norr), (x1_k, y_norr)),
            z_kron_rh2000=(z_n0 + 0.12, z_n1 + 0.12),
            z_botten_rh2000=(z_n0, z_n1),
            tvarfall=0.025,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x0_k, y_soder), (x1_k, y_soder)),
            z_kron_rh2000=(z_n0 + 0.12, z_n1 + 0.12),
            z_botten_rh2000=(z_n0, z_n1),
            tvarfall=0.025,
            kalla="baskarta_3d",
        ),
    ]
    regn_norr = np.zeros((h, b), dtype=np.bool_)
    regn_norr[2:8, 4:16] = True

    lank_stangd = diskretisera_rannsten_1d(
        brytlinjer, z, markslag, geo, ds_mal_m=2.0, korbana_sida="vanster", oppen_nedstroms=False
    )
    assert lank_stangd.antal_kedjor == 2
    norr_idx = [s.index for s in lank_stangd.segment if s.kedje_id == 0]
    soder_idx = [s.index for s in lank_stangd.segment if s.kedje_id == 1]
    assert (norr_idx[-1], soder_idx[0]) not in lank_stangd.effektiva_anslutna_par()

    res_stangd = kor_kopplad_1d_2d(
        z,
        markslag,
        geo,
        [0.0, 60.0],
        [360.0, 360.0],
        lank_stangd,
        brytlinjer=brytlinjer,
        slut_tid_s=120.0,
        oppna_rander=False,
        regn_mask=regn_norr,
    )
    assert res_stangd.volymfel_rel < 1e-11
    assert float(np.max(res_stangd.max_djup_1d_m[norr_idx])) > 0.02
    assert float(np.max(res_stangd.max_djup_1d_m[soder_idx])) == 0.0
    assert float(np.max(res_stangd.slut_djup_1d_m[soder_idx])) == 0.0
    assert float(np.max(res_stangd.max_djup_2d_m[24:, :])) == 0.0

    # Öppet nedströmsutlopp dränerar kedja 0 trots att kedja 1 ligger sist, fortsatt 0,0 m läckage
    lank_oppen = diskretisera_rannsten_1d(
        brytlinjer, z, markslag, geo, ds_mal_m=2.0, korbana_sida="vanster", oppen_nedstroms=True
    )
    res_oppen = kor_kopplad_1d_2d(
        z,
        markslag,
        geo,
        [0.0, 60.0],
        [360.0, 360.0],
        lank_oppen,
        brytlinjer=brytlinjer,
        slut_tid_s=120.0,
        oppna_rander=False,
        regn_mask=regn_norr,
    )
    assert res_oppen.volymfel_rel < 1e-11
    assert res_oppen.utflode_1d_m3 > 0.05
    assert float(np.max(res_oppen.max_djup_1d_m[soder_idx])) == 0.0

    # --- Del B: Parallell `kor_batch_rutor(..., max_arbetare=2)` på K1 v2-rutor med DSM + NaN ---
    rutor = [
        Ruta(150000.0 + i * 100.0, 6580000.0, 100.0, 0.0, 5.0)
        for i in range(4)
    ]
    k1_rot = tmp_path / "k1_batch"
    nan_mask = np.zeros(rutor[0].form_rc, dtype=bool)
    nan_mask[0:2, :] = True  # 40 av 400 celler = 10,0 % NaN på ruta 1

    for i, ruta in enumerate(rutor):
        re0, rn0, _, _ = ruta.yttre_bbox
        bas_z = 10.0 + float(i)
        terr = np.full(ruta.form_rc, bas_z, dtype=np.float32)
        if i == 1:
            terr[nan_mask] = np.nan
        hus = ByggnadsPost(
            byggnad_id=f"hus_batch_{i}",
            polygon=(
                (re0 + 40.0, rn0 + 35.0),
                (re0 + 60.0, rn0 + 35.0),
                (re0 + 60.0, rn0 + 55.0),
                (re0 + 40.0, rn0 + 55.0),
            ),
            takhojd_rh2000=bas_z + 20.0,
            markhojd_rh2000=bas_z,
            hojd_m=20.0,
            kalla="syntetisk_v5",
            hojdkalla="laser_klass6",
            osakerhet_hojd_m=0.15,
        )
        # `mark.tif` är avsiktligt platt utan byggnadshöjd -> bevisar K1 v2 DSM från byggnader.gpkg
        _skriv_k1_v2_ruta(k1_rot, ruta, terrang_z=terr, mark_z=terr, byggnader=(hus,))

    korare = skapa_k1_rutkorare(k1_rot, azimutriktningar=24, dagsljus_radie_m=100.0)
    ut_sek = tmp_path / "batch_sek"
    ut_par = tmp_path / "batch_par"

    st_sek = kor_batch_rutor(
        rutor,
        BatchKonfig(
            moduler=("dagsljus",),
            utdata_rot=ut_sek,
            enhet="cpu",
            rutstorlek_m=100.0,
            overlapp_m=0.0,
            upplosning_m=5.0,
            max_arbetare=1,
        ),
        rutkorare=korare,
        avbryt_vid_fel=True,
    )
    st_par = kor_batch_rutor(
        rutor,
        BatchKonfig(
            moduler=("dagsljus",),
            utdata_rot=ut_par,
            enhet="cpu",
            rutstorlek_m=100.0,
            overlapp_m=0.0,
            upplosning_m=5.0,
            max_arbetare=2,
        ),
        rutkorare=korare,
        avbryt_vid_fel=True,
    )

    assert st_par.klara == st_sek.klara == tuple(r.namn for r in rutor)
    assert st_par.felade == {}

    for i, ruta in enumerate(rutor):
        svf_sek_fil = ut_sek / ruta.namn / "dagsljus" / "svf.tif"
        svf_par_fil = ut_par / ruta.namn / "dagsljus" / "svf.tif"
        svf_sek, _ = las_geotiff(svf_sek_fil)
        svf_par, _ = las_geotiff(svf_par_fil)
        # Bitidentiskt utdata mellan max_arbetare=2 och max_arbetare=1 (inkl. NaN-positioner)
        np.testing.assert_array_equal(svf_par, svf_sek)

        m_sek = las_manifest(ut_sek / ruta.namn / "run.json")
        m_par = las_manifest(ut_par / ruta.namn / "run.json")
        assert m_par.matvarden["dagsljus_k1_version"] == 2
        assert m_par.matvarden["dagsljus_dsm_kalla"] == "k1_v2_ytmodell"
        assert m_par.matvarden["dagsljus_svf_min"] == m_sek.matvarden["dagsljus_svf_min"]
        assert m_par.matvarden["dagsljus_svf_medel"] == m_sek.matvarden["dagsljus_svf_medel"]
        assert m_par.varningar == m_sek.varningar

        giltig = np.isfinite(svf_par)
        # Verklig byggnadsskugga intill 20 m-huset (SVF klart under 1,0)
        assert float(np.min(svf_par[giltig])) < 0.85
        assert float(np.mean(svf_par[giltig])) < 0.99

        if i == 1:
            assert np.all(np.isnan(svf_par[nan_mask]))
            assert np.all(np.isfinite(svf_par[~nan_mask]))
            with rasterio.open(svf_par_fil) as ds:
                assert ds.nodata is not None and np.isnan(ds.nodata)
            assert m_par.matvarden["dagsljus_nan_celler"] == 40
            assert m_par.matvarden["dagsljus_nan_andel"] == pytest.approx(0.1)
            assert len(m_par.varningar) == 1
            assert "NaN" in m_par.varningar[0] and "10.0 %" in m_par.varningar[0]
        else:
            assert np.all(giltig)
            assert m_par.matvarden["dagsljus_nan_celler"] == 0
            assert m_par.varningar == []
