"""Tester för lokal förberäknad katalog utan S3 (`geografi.forberaknad_lager`).

Verifierar:
1. Lokal `data/forberaknat/`-läsning med `FLODEN_S3_TYP` osatt eller `lokal`, samt
   `FLODEN_FORBERAKNAT_DIR` med riktiga ZSTD-COG-rutor (0 S3 / 0 boto3).
2. Ren fallback när katalogen saknas (returnerar None / tom lista, skapar aldrig tom mapp).
3. Explicit path-traversal- och symlänksskydd i kommun-, ruta- och fil-ID.
4. Hantering av korrupt/trunkerad ZSTD-COG och trasig metadata-JSON (alltid `ForberaknadLagerFel`).
5. 100 % bakåtkompatibilitet med `FLODEN_S3_TYP=s3` och uttrycklig `ObjektLagring`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from gemensamt.cog import skriv_komprimerad_cog
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from gemensamt.objektlagring import LokalLagring, geografi_nyckel
from geografi.forberaknad_lager import (
    FLODEN_FORBERAKNAT_DIR_ENV,
    STANDARD_FORBERAKNAT_DIR,
    ForberaknadKommunLager,
    ForberaknadLagerFel,
    forberaknad_lager_fran_miljo,
    las_forberaknat_dir,
    validera_forberaknad_geotiff,
)
from webb.api import FlodenApiApp


def _skapa_cog_ruta(
    rot: Path,
    kommun: str,
    rut_id: str,
    *,
    e_min: float = 150000.0,
    n_max: float = 6580500.0,
    dataklass: str = "oppen",
    skriv_utdata_sha: bool = True,
) -> Path:
    """Skriver en komplett lokal förberäknad ruta med riktig ZSTD/DEFLATE-COG och K0-manifest."""
    rut_mapp = rot / "geografi" / kommun / rut_id
    rut_mapp.mkdir(parents=True, exist_ok=True)
    mark = np.full((32, 32), 12.5, dtype=np.float32)
    djup = np.full((32, 32), 0.25, dtype=np.float32)
    mark_fil = skriv_komprimerad_cog(
        rut_mapp / "mark.tif",
        mark,
        x_min=e_min,
        y_max=n_max,
        pixelstorlek=1.0,
        bygg_overviews=False,
    )
    djup_fil = skriv_komprimerad_cog(
        rut_mapp / "max_djup.tif",
        djup,
        x_min=e_min,
        y_max=n_max,
        pixelstorlek=1.0,
        bygg_overviews=False,
    )
    utdata: tuple[FilPost, ...] = ()
    if skriv_utdata_sha:
        utdata = (
            FilPost(roll="mark", fil="mark.tif", sha256=sha256_fil(mark_fil)),
            FilPost(roll="max_djup", fil="max_djup.tif", sha256=sha256_fil(djup_fil)),
        )
    skriv_manifest(
        skapa_manifest(
            modul="forberakna",
            parametrar={"dataklass": dataklass, "ruta_id": rut_id},
            utdata=utdata,
        ),
        rut_mapp / "run.json",
    )
    return rut_mapp


def test_lokal_data_forberaknat_utan_s3_och_med_env_dir(tmp_path: Path) -> None:
    """Standard `data/forberaknat` och `FLODEN_FORBERAKNAT_DIR` fungerar utan S3 / boto3."""
    # 1. Standardkatalog `data/forberaknat` under bas_rot när FLODEN_S3_TYP är osatt
    forb_rot = tmp_path / STANDARD_FORBERAKNAT_DIR
    _skapa_cog_ruta(forb_rot, "nacka", "mm_150000_6580000")
    _skapa_cog_ruta(forb_rot, "nacka", "mm_150500_6580000", e_min=150500.0)

    lager = forberaknad_lager_fran_miljo(
        cache_rot=tmp_path / "cache_std",
        miljo={},
        bas_rot=tmp_path,
    )
    assert lager is not None
    assert lager.kalla_typ == "lokal"
    assert lager.ar_tillganglig is True
    assert lager.lokal_rot == forb_rot.resolve()
    assert lager.lista_kommuner() == ["nacka"]
    assert lager.lista_rutor("nacka") == ["mm_150000_6580000", "mm_150500_6580000"]

    mapp = lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif", "max_djup.tif"))
    assert (mapp / "mark.tif").is_file()
    assert (mapp / "max_djup.tif").is_file()
    arr, meta = lager.las_raster("nacka", "mm_150000_6580000", "max_djup.tif")
    assert arr.shape == (32, 32)
    assert float(np.mean(arr)) == pytest.approx(0.25)
    assert meta["bredd"] == 32 and meta["hojd"] == 32

    # 2. Uttrycklig `FLODEN_FORBERAKNAT_DIR` + `FLODEN_S3_TYP=lokal`
    egen_rot = tmp_path / "colab_rutor"
    _skapa_cog_ruta(egen_rot, "stockholm", "mm_153000_6581000", e_min=153000.0, n_max=6581500.0)
    lager_env = forberaknad_lager_fran_miljo(
        cache_rot=tmp_path / "cache_env",
        miljo={
            "FLODEN_S3_TYP": "lokal",
            FLODEN_FORBERAKNAT_DIR_ENV: str(egen_rot),
        },
        bas_rot=tmp_path,
    )
    assert lager_env is not None
    assert lager_env.kalla_typ == "lokal"
    assert lager_env.lista_kommuner() == ["stockholm"]
    assert lager_env.hitta_rutor_for_utbredning(
        "stockholm", (153100.0, 6581100.0, 153400.0, 6581400.0)
    ) == ["mm_153000_6581000"]

    # 3. `FLODEN_S3_TYP=lokal` överrider även eventuella S3-bucket/endpoint-variabler
    lager_lokal_force = forberaknad_lager_fran_miljo(
        cache_rot=tmp_path / "cache_force",
        miljo={
            "FLODEN_S3_TYP": "lokal",
            "FLODEN_S3_BUCKET": "ska-ignoreras",
            "FLODEN_S3_ENDPOINT": "https://fsn1.your-objectstorage.com",
            FLODEN_FORBERAKNAT_DIR_ENV: str(egen_rot),
        },
    )
    assert lager_lokal_force is not None
    assert lager_lokal_force.kalla_typ == "lokal"


def test_saknad_lokal_katalog_ger_rent_tomt_svar_utan_krasch(tmp_path: Path) -> None:
    """Saknad `data/forberaknat/` ger None (i miljöfabrik) resp. tom lista (utan att skapa mapp)."""
    # 1. Miljöfabriken returnerar None och skapar inte `data/forberaknat/`
    assert forberaknad_lager_fran_miljo(tmp_path / "cache", miljo={}, bas_rot=tmp_path) is None
    assert not (tmp_path / STANDARD_FORBERAKNAT_DIR).exists()

    # 2. Saknad explicit `FLODEN_FORBERAKNAT_DIR` returnerar None utan krasch
    saknad = tmp_path / "finns_inte_alls"
    assert (
        forberaknad_lager_fran_miljo(
            tmp_path / "cache",
            miljo={FLODEN_FORBERAKNAT_DIR_ENV: str(saknad)},
        )
        is None
    )
    assert not saknad.exists()

    # 3. `ForberaknadKommunLager.fran_lokal_katalog` på saknad mapp ger rent tomt svar
    lager_tom = ForberaknadKommunLager.fran_lokal_katalog(saknad, cache_rot=tmp_path / "cache2")
    assert lager_tom.kalla_typ == "lokal"
    assert lager_tom.ar_tillganglig is False
    assert lager_tom.lista_kommuner() == []
    assert lager_tom.lista_rutor("nacka") == []
    assert (
        lager_tom.hitta_rutor_for_utbredning("nacka", (150000.0, 6580000.0, 150500.0, 6580500.0))
        == []
    )
    assert not saknad.exists()

    # 4. Pekar FLODEN_FORBERAKNAT_DIR på en fil i stället för katalog -> tydligt fel
    vanlig_fil = tmp_path / "inte_en_katalog.txt"
    vanlig_fil.write_text("hej", encoding="utf-8")
    with pytest.raises(ForberaknadLagerFel, match="katalog"):
        forberaknad_lager_fran_miljo(
            tmp_path / "cache3",
            miljo={FLODEN_FORBERAKNAT_DIR_ENV: str(vanlig_fil)},
        )
    with pytest.raises(ForberaknadLagerFel, match="katalog"):
        ForberaknadKommunLager.fran_lokal_katalog(vanlig_fil, cache_rot=tmp_path / "cache3")


def test_path_traversal_och_symlanksflykt_avvisas_explicit(tmp_path: Path) -> None:
    """Path traversal (`..`, `/`, `\\`, NUL) och symlänkar ut ur roten avvisas med tydligt fel."""
    forb_rot = tmp_path / "forberaknat"
    rut_mapp = _skapa_cog_ruta(forb_rot, "nacka", "mm_150000_6580000", skriv_utdata_sha=False)
    lager = ForberaknadKommunLager.fran_lokal_katalog(forb_rot, cache_rot=tmp_path / "cache")

    # 1. Path traversal i kommun-, ruta- och fil-ID
    ogiltiga_kommuner = ("..", "../etc", "nacka/subdir", "nacka\\sub", "/etc", ".hidden", "a\x00b")
    for ogiltig_kommun in ogiltiga_kommuner:
        with pytest.raises(ForberaknadLagerFel):
            lager.lista_rutor(ogiltig_kommun)
        with pytest.raises(ForberaknadLagerFel):
            lager.hamta_ruta(ogiltig_kommun, "mm_150000_6580000")

    for ogiltig_ruta in ("..", "../nacka", "mm_1/mm_2", "mm_1\\mm_2", "/root", "mm\x001"):
        with pytest.raises(ForberaknadLagerFel):
            lager.hamta_ruta("nacka", ogiltig_ruta)

    ogiltiga_filer = (
        "../secret.txt",
        "sub/../../etc/passwd",
        "/etc/passwd",
        "a\\b.tif",
        "a\x00.tif",
        "",
    )
    for ogiltig_fil in ogiltiga_filer:
        with pytest.raises(ForberaknadLagerFel):
            lager.hamta_ruta("nacka", "mm_150000_6580000", filer=(ogiltig_fil,))

    # 2. Filsymlänk ut ur lagringsroten -> avvisas explicit
    hemlig_fil = tmp_path / "hemlig_utanfor.tif"
    hemlig_fil.write_bytes(b"HEMLIG-DATA")
    sym_fil = rut_mapp / "escape.tif"
    sym_fil.symlink_to(hemlig_fil)
    with pytest.raises(ForberaknadLagerFel, match="utanför roten"):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("escape.tif",))

    # 3. Katalogsymlänk på kommunnivå ut ur roten -> avvisas i både lista_rutor och hamta_ruta
    extern_mapp = tmp_path / "extern_kommun"
    extern_mapp.mkdir()
    (extern_mapp / "mm_150000_6580000").mkdir()
    (forb_rot / "geografi" / "ondkommun").symlink_to(extern_mapp)
    with pytest.raises(ForberaknadLagerFel, match="utanför roten"):
        lager.lista_rutor("ondkommun")
    with pytest.raises(ForberaknadLagerFel, match="utanför roten"):
        lager.hamta_ruta("ondkommun", "mm_150000_6580000", filer=("mark.tif",))

    # 4. Symlänk i cachekatalogen som pekar ut ur cacheroten -> avvisas explicit
    cache_rot = tmp_path / "cache_sym"
    extern_cache_mal = tmp_path / "extern_cache_mal"
    extern_cache_mal.mkdir()
    (cache_rot / "geografi" / "nacka").mkdir(parents=True)
    (cache_rot / "geografi" / "nacka" / "mm_150000_6580000").symlink_to(extern_cache_mal)
    lager_cache_sym = ForberaknadKommunLager.fran_lokal_katalog(forb_rot, cache_rot=cache_rot)
    with pytest.raises(ForberaknadLagerFel, match="utanför roten"):
        lager_cache_sym.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))


def test_korrupt_cog_och_trasig_metadata_json_ger_hanterat_fel(tmp_path: Path) -> None:
    """Trunkerad ZSTD-COG, 0-byte, SHA-avvikelse och trasig JSON ger `ForberaknadLagerFel`."""
    forb_rot = tmp_path / "forberaknat"

    # 1. Trunkerad ZSTD-COG (halv GeoTIFF-fil med giltig TIFF-magi men avkapad dataström)
    rut1 = _skapa_cog_ruta(forb_rot, "nacka", "mm_150000_6580000", skriv_utdata_sha=False)
    orig_bytes = (rut1 / "mark.tif").read_bytes()
    assert len(orig_bytes) > 64
    (rut1 / "mark.tif").write_bytes(orig_bytes[: len(orig_bytes) // 3])

    cache1 = tmp_path / "cache1"
    lager = ForberaknadKommunLager.fran_lokal_katalog(forb_rot, cache_rot=cache1)
    with pytest.raises(ForberaknadLagerFel, match="Korrupt eller trunkerad GeoTIFF/COG"):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))
    # Korrupt fil får inte hamna i cachen
    assert not (cache1 / "geografi" / "nacka" / "mm_150000_6580000" / "mark.tif").exists()
    with pytest.raises(ForberaknadLagerFel):
        validera_forberaknad_geotiff(rut1 / "mark.tif")

    # 2. Tom 0-bytes .tif-fil
    rut2 = _skapa_cog_ruta(forb_rot, "nacka", "mm_150500_6580000", skriv_utdata_sha=False)
    (rut2 / "max_djup.tif").write_bytes(b"")
    with pytest.raises(ForberaknadLagerFel, match="Tom eller trunkerad"):
        lager.hamta_ruta("nacka", "mm_150500_6580000", filer=("max_djup.tif",))

    # 3. Slumpskräp i .tif fångas av `verifiera_geotiff=True` och `las_raster`
    (rut2 / "max_djup.tif").write_bytes(b"INTE-EN-GEOTIFF-ALLS")
    with pytest.raises(ForberaknadLagerFel, match="Korrupt eller trunkerad"):
        lager.las_raster("nacka", "mm_150500_6580000", "max_djup.tif")

    # 4. SHA-256-avvikelse mot `run.json` (`utdata`)
    rut3 = _skapa_cog_ruta(forb_rot, "nacka", "mm_151000_6580000", skriv_utdata_sha=True)
    # Skriv över mark.tif med annan giltig COG så bara SHA-256 skiljer sig
    skriv_komprimerad_cog(
        rut3 / "mark.tif",
        np.full((32, 32), 99.0, dtype=np.float32),
        x_min=151000.0,
        y_max=6580500.0,
        pixelstorlek=1.0,
        bygg_overviews=False,
    )
    with pytest.raises(ForberaknadLagerFel, match="SHA-256"):
        lager.hamta_ruta("nacka", "mm_151000_6580000", filer=("mark.tif",))

    # 5. Trasig metadata-JSON: ogiltig syntax, trasig UTF-8 och icke-objekt ("[]")
    for idx, innehall in enumerate((b"{trasig json", b"\xff\xfe\xfd", b"[]", b"null")):
        rid = f"mm_{152000 + idx * 500}_6580000"
        rut_bad = forb_rot / "geografi" / "nacka" / rid
        rut_bad.mkdir(parents=True, exist_ok=True)
        (rut_bad / "run.json").write_bytes(innehall)
        (rut_bad / "mark.tif").write_bytes(orig_bytes)
        with pytest.raises(ForberaknadLagerFel):
            lager.hamta_ruta("nacka", rid, filer=("mark.tif",))

    # Extra korrupt JSON-metadatafil i rutan
    (rut2 / "metadata.json").write_text("{ej-giltig", encoding="utf-8")
    with pytest.raises(ForberaknadLagerFel, match="Korrupt metadata-JSON"):
        lager.hamta_ruta("nacka", "mm_150500_6580000", filer=("metadata.json",))


def test_bakatkompatibilitet_s3_uttrycklig_lagring_och_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FLODEN_S3_TYP=s3`, uttrycklig `ObjektLagring` och `FlodenApiApp` med lokal katalog."""
    # 1. S3-läge oförändrat (även om lokal `data/forberaknat` också finns!)
    forb_rot = tmp_path / STANDARD_FORBERAKNAT_DIR
    _skapa_cog_ruta(forb_rot, "nacka", "mm_150000_6580000")
    mock_klient = MagicMock()
    lager_s3 = forberaknad_lager_fran_miljo(
        tmp_path / "cache_s3",
        miljo={
            "FLODEN_S3_TYP": "s3",
            "FLODEN_S3_ENDPOINT": "https://fsn1.your-objectstorage.com",
            "FLODEN_S3_BUCKET": "floden-oppen",
            "FLODEN_S3_ACCESS_KEY": "ak",
            "FLODEN_S3_SECRET_KEY": "sk",
        },
        klient=mock_klient,
        bas_rot=tmp_path,
    )
    assert lager_s3 is not None
    assert lager_s3.kalla_typ == "s3"

    # 2. Uttrycklig `LokalLagring` skickad direkt i konstruktorn
    lokal_dir = tmp_path / "direkt_lokal"
    _skapa_cog_ruta(lokal_dir, "solna", "mm_150000_6580000")
    direkt = ForberaknadKommunLager(LokalLagring(lokal_dir), tmp_path / "cache_dir")
    assert direkt.kalla_typ == "lokal"
    assert direkt.lista_kommuner() == ["solna"]

    # 3. `las_forberaknat_dir` respekterar konfig-fallback
    assert las_forberaknat_dir(
        {"lagring": {"forberaknat_dir": "egna/rutor"}}, miljo={}, bas_rot=tmp_path
    ) == tmp_path / "egna/rutor"

    # 4. `FlodenApiApp` hittar lokal katalog via `FLODEN_FORBERAKNAT_DIR` utan S3
    for var in (
        "FLODEN_S3_TYP",
        "FLODEN_S3_ENDPOINT",
        "FLODEN_S3_BUCKET",
        "FLODEN_S3_ACCESS_KEY",
        "FLODEN_S3_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(FLODEN_FORBERAKNAT_DIR_ENV, str(forb_rot))
    monkeypatch.setenv("FLODEN_LOKAL_CACHE_MAPP", str(tmp_path / "api_cache"))

    app = FlodenApiApp(
        konfig_fil=tmp_path / "saknas.toml",
        testsvit_rot=tmp_path / "testsvit",
    )
    status, _, kropp = app.hantera_forfragan("GET", "/api/geografi/forberaknade")
    assert status == 200
    data: dict[str, Any] = json.loads(kropp)
    assert data == {
        "s3_aktiv": True,
        "kommuner": [{"kommun": "nacka", "antal_rutor": 1}],
    }
    assert app.forberaknad_lager is not None
    assert app.forberaknad_lager.kalla_typ == "lokal"

    # Uttrycklig `geografi_nyckel` fungerar fortsatt oförändrat
    assert geografi_nyckel("nacka", "mm_150000_6580000", "mark.tif") == (
        "geografi/nacka/mm_150000_6580000/mark.tif"
    )
