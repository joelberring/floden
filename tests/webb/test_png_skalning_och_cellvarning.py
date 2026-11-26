"""Tester för PNG-nedskalning (block-max <= 2048 px), O(1)-cellvarning och robust run.json.

Täcker Våg 11 Steg S-O4 samt regressionstest för icke-dict `run.json` i `webb.fragor`.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst, Korning
from webb.kartverktyg import FORMAT as KV_FORMAT
from webb.kartverktyg import KartverktygTjanst
from webb.projektkalla import Mall, Projektkalla
from webb.resultatlager import (
    FLODEN_MAX_PNG_PIXLAR_ENV,
    MAX_PX,
    LagerFel,
    max_png_pixlar,
    rita_bild,
    rita_png,
)

# ------------------------------------------------------------------ 1. PNG-nedskalning (block-max)


def test_png_3000x3000_nedskalas_till_2048_bevarar_1px_gata_och_sparar_bytes() -> None:
    """3000×3000-raster skalas deterministiskt till 2048×2048 med block-max så 1-px gata syns."""
    # Syntetiskt flerruts-skyfall med gatugrid + en 1-pixel bred översvämmad gata + NaN-hörn
    raster = np.zeros((3000, 3000), dtype=np.float32)
    # Variation i bakgrunden så PNG-komprimeringen avspeglar verkliga djupmönster
    yy, xx = np.ogrid[:3000, :3000]
    polder = ((yy % 37) < 5) & ((xx % 41) < 5)
    raster[polder] = 0.08
    # 1-pixels översvämmad smal gata (0.45 m -> klass 0.30–0.50 m = #1a52b8)
    gata_rad = 1501
    raster[gata_rad, 100:2900] = 0.45
    # NaN-område i övre vänstra hörnet måste förbli helt transparent
    raster[:200, :200] = np.nan

    bild_skalad = rita_bild(raster, "vatten", lo=0.0, hi=1.2)
    assert bild_skalad.bredd_px == 2048
    assert bild_skalad.hojd_px == 2048
    assert bild_skalad.faktor == 2

    # Verifiera faktiska PNG-dimensioner och innehåll via Pillow
    rgba = np.asarray(Image.open(BytesIO(bild_skalad.png)).convert("RGBA"))
    assert rgba.shape == (2048, 2048, 4)

    # NaN-hörnet är fortsatt helt genomskinligt (alfa == 0)
    assert int(rgba[:100, :100, 3].max()) == 0

    # 1-pixels gata på rad 1501 hamnar i exakt motsvarande blockrad och har klassfärg #1a52b8!
    r_kanter = (np.arange(2049, dtype=np.int64) * 3000) // 2048
    mal_rad = int(np.searchsorted(r_kanter, gata_rad, side="right") - 1)
    gata_rgba = rgba[mal_rad, 100:1900]
    assert np.all(gata_rgba[:, 3] == 255), "1-px översvämmad gata tappades vid nedskalning!"
    assert np.all(gata_rgba[:, 0] == 0x1A)
    assert np.all(gata_rgba[:, 1] == 0x52)
    assert np.all(gata_rgba[:, 2] == 0xB8)

    # Jämför PNG-storlek och dekodad RGBA-RAM mot oskalad 3000×3000-rendering
    bild_full = rita_bild(raster, "vatten", lo=0.0, hi=1.2, max_pixlar=4096)
    assert bild_full.bredd_px == 3000 and bild_full.hojd_px == 3000
    assert len(bild_skalad.png) < len(bild_full.png), (
        f"Nedskalad PNG ({len(bild_skalad.png)} B) ej mindre än full ({len(bild_full.png)} B)"
    )
    ram_full = bild_full.bredd_px * bild_full.hojd_px * 4
    ram_skalad = bild_skalad.bredd_px * bild_skalad.hojd_px * 4
    assert ram_skalad < ram_full * 0.5  # > 50 % lägre RGBA-RAM

    # Invariant: källrastret är helt orört (full upplösning bevarad för pekaravläsning/GeoTIFF)
    assert raster.shape == (3000, 3000)
    assert float(raster[gata_rad, 500]) == pytest.approx(0.45)
    assert np.isnan(raster[10, 10])

    # `synlig_px` kartlägger korrekt och utan inverterad bbox med `fragor.py`-formeln
    assert bild_skalad.synlig_px is not None
    c0, r0, c1, r1 = bild_skalad.synlig_px
    assert 0 <= c0 < c1 <= (3000 + bild_skalad.faktor - 1) // bild_skalad.faktor
    assert 0 <= r0 < r1 <= (3000 + bild_skalad.faktor - 1) // bild_skalad.faktor


def test_png_block_max_takmask_fara_och_skillnad_bevarar_smala_strak() -> None:
    """Takmask döljer tak före block-max, och skillnadslager bevarar både - och + smala gator."""
    # Farolager med en byggnadscell (dold=True) bredvid en 1-px gata i samma block
    raster = np.zeros((300, 300), dtype=np.float32)
    dold = np.zeros((300, 300), dtype=np.bool_)
    # Rad 100 är tak med falskt högt värde, rad 101 är smal översvämmad gata
    raster[100, :] = 5.0
    dold[100, :] = True
    raster[101, 20:280] = 0.6
    # Rad 0..30 helt tak -> måste bli helt transparent
    raster[:30, :] = 9.0
    dold[:30, :] = True

    bild = rita_bild(raster, "fara", lo=0.0, hi=1.0, dold=dold, max_pixlar=100)
    assert (bild.bredd_px, bild.hojd_px) == (100, 100)
    rgba = np.asarray(Image.open(BytesIO(bild.png)).convert("RGBA"))
    assert int(rgba[:9, :, 3].max()) == 0  # Helt maskad zon förblir transparent
    # Block som rymmer rad 100 (tak) och rad 101 (gata 0.6) får gatans värde (synligt, inte max=5.0)
    rad_block = (101 * 100) // 300
    assert np.all(rgba[rad_block, 10:90, 3] == 255)

    # Skillnadslager: smal blå minskning (-0.8) och smal röd ökning (+0.8) bevaras båda!
    sk = np.zeros((300, 300), dtype=np.float32)
    sk[60, 20:280] = -0.8
    sk[210, 20:280] = 0.8
    bild_sk = rita_bild(sk, "skillnad", lo=0.0, hi=1.0, troskel=0.05, max_pixlar=100)
    rgba_sk = np.asarray(Image.open(BytesIO(bild_sk.png)).convert("RGBA"))
    rad_minsk = (60 * 100) // 300
    rad_okn = (210 * 100) // 300
    assert int(rgba_sk[rad_minsk, 50, 3]) == 255
    assert int(rgba_sk[rad_minsk, 50, 2]) > int(rgba_sk[rad_minsk, 50, 0])  # blå
    assert int(rgba_sk[rad_okn, 50, 3]) == 255
    assert int(rgba_sk[rad_okn, 50, 0]) > int(rgba_sk[rad_okn, 50, 2])  # röd


def test_png_avlanga_och_sma_raster_samt_env_validering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avlånga raster får aldrig 0-dim, små raster förblir orörda och ogiltig env ger fel."""
    # 1. 4000×100 -> 2048×51 (bibehållet bildförhållande, aldrig 0 px)
    _, b1, h1, f1 = rita_png(np.zeros((4000, 100), dtype=np.float32), "vatten", lo=0.0, hi=1.0)
    assert h1 == 2048
    assert b1 == 51
    assert f1 == 2

    # 2. 4096×1000 -> 2048×500
    _, b2, h2, f2 = rita_png(np.zeros((4096, 1000), dtype=np.float32), "vatten", lo=0.0, hi=1.0)
    assert (h2, b2, f2) == (2048, 500, 2)

    # 3. Extremt avlånga raster (båda riktningar) -> minsta sida >= 1 (aldrig 0!)
    _, b3, h3, _ = rita_png(np.zeros((10000, 2), dtype=np.float32), "sekventiell", lo=0.0, hi=1.0)
    assert h3 == 2048 and b3 == 1
    _, b4, h4, _ = rita_png(np.zeros((2, 10000), dtype=np.float32), "sekventiell", lo=0.0, hi=1.0)
    assert h4 == 1 and b4 == 2048

    # 4. Små och normala raster (<= 2048) behåller 100 % originalupplösning
    _, b5, h5, f5 = rita_png(np.zeros((500, 500), dtype=np.float32), "vatten", lo=0.0, hi=1.0)
    assert (b5, h5, f5) == (500, 500, 1)

    # 5. Konfigurerbar `FLODEN_MAX_PNG_PIXLAR`
    monkeypatch.delenv(FLODEN_MAX_PNG_PIXLAR_ENV, raising=False)
    assert max_png_pixlar() == MAX_PX == 2048

    monkeypatch.setenv(FLODEN_MAX_PNG_PIXLAR_ENV, "512")
    assert max_png_pixlar() == 512
    _, b6, h6, f6 = rita_png(np.zeros((1000, 500), dtype=np.float32), "vatten", lo=0.0, hi=1.0)
    assert (h6, b6, f6) == (512, 256, 2)

    # 6. Ogiltiga värden i `FLODEN_MAX_PNG_PIXLAR` -> tydligt LagerFel
    for ogiltigt in ("0", "-100", "63", "abc", "512.5"):
        monkeypatch.setenv(FLODEN_MAX_PNG_PIXLAR_ENV, ogiltigt)
        with pytest.raises(LagerFel, match=FLODEN_MAX_PNG_PIXLAR_ENV):
            max_png_pixlar()
        with pytest.raises(LagerFel, match=FLODEN_MAX_PNG_PIXLAR_ENV):
            rita_png(np.zeros((10, 10), dtype=np.float32), "vatten", lo=0.0, hi=1.0)


# ---------------------------------------------------------- 2. O(1) celluppskattning i kartverktyg


def _skapa_geografi_kartverktyg(
    tmp_path: Path,
    *,
    rut_ids: list[str] | None,
    upplosning_m: float = 0.5,
    fokus: tuple[float, float, float, float] | None = None,
    slag: str = "geografi",
    geografi_ref: str | None = "nacka-test@v1",
    skriv_manifest_fil: bool = True,
) -> KartverktygTjanst:
    fragor = FragaTjanst(
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    geomapp = tmp_path / "geografier"
    if geografi_ref is not None and skriv_manifest_fil:
        geo_id, ver = geografi_ref.split("@", 1)
        mapp = geomapp / geo_id / ver
        mapp.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "kontrakt": "floden-geografi/v1",
            "id": geo_id,
            "version": ver,
            "kommun": "nacka",
            "gransen_3011": [150000.0, 6580000.0, 151500.0, 6581000.0],
            "parametrar": {
                "upplosning_m": upplosning_m,
                "ruta_storlek_m": 500.0,
            },
        }
        if rut_ids is not None:
            manifest["lager"] = {"k1_rutnat": {"rutor": rut_ids}}
        (mapp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    kv = KartverktygTjanst(fragor, tmp_path / "kv", geomapp=geomapp)
    stub_ruta = MagicMock()
    stub_ruta.x_min = 150000.0
    stub_ruta.y_min = 6580000.0
    stub_ruta.x_max = 150500.0
    stub_ruta.y_max = 6580500.0
    stub_ruta.mapp = tmp_path / "tom_ruta"
    stub_ruta.mapp.mkdir(parents=True, exist_ok=True)

    analys = MagicMock()
    analys.id = "skyfall"
    analys.losare = "vatten.statisk_fyllning"
    analys.fokus = fokus
    analys.parametrar = {"regn_mm": 60.0}
    stub_fraga = MagicMock()
    stub_fraga.analyser = (analys,)
    stub_mall = Mall("vatten", "vatten", stub_fraga, tmp_path / "bas", (stub_ruta,), "fore")

    proj_mapp = tmp_path / "projekt" / "p1"
    proj_mapp.mkdir(parents=True, exist_ok=True)
    stub_kalla = Projektkalla(
        id="p1",
        slag=slag,
        namn="Testprojekt",
        paahittat=(slag == "demo"),
        oppen=True,
        licens="CC0",
        mallar={"vatten": stub_mall},
        mapp=proj_mapp if slag == "geografi" else None,
        geografi=geografi_ref if slag == "geografi" else None,
        avgransning=(150050.0, 6580050.0, 150450.0, 6580450.0) if slag == "geografi" else None,
    )
    kv.kalla = lambda projekt: stub_kalla  # type: ignore[method-assign]
    kv._alla_byggnader = lambda kalla: []  # type: ignore[method-assign]
    kv._alla_brytlinjer = lambda kalla: []  # type: ignore[method-assign]

    # Spara ett minimalt scenario så även `kv.status("p1", "scen")` kan anropas direkt
    scen_mapp = kv._scenariomapp("p1")  # noqa: SLF001
    scen_mapp.mkdir(parents=True, exist_ok=True)
    (scen_mapp / "scen.json").write_text(
        json.dumps(
            {
                "format": KV_FORMAT,
                "namn": "Testscenario",
                "slug": "scen",
                "andringar": [],
                "korningar": {},
            }
        ),
        encoding="utf-8",
    )
    return kv


def test_berakningsomfang_ok_varning_over_tak_och_utan_geografi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O(1)-uppskattning i `status()`, `status_lage_idag()` och `projektinfo()` utan raster."""
    import rasterio

    def _forbjuden_rasterlasning(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("berakningsomfang får aldrig öppna rasterfiler!")

    monkeypatch.setattr(rasterio, "open", _forbjuden_rasterlasning)
    monkeypatch.delenv("FLODEN_MAX_SANKGRAF_CELLER", raising=False)

    alla_6_rutor = [
        "mm_150000_6580000",
        "mm_150500_6580000",
        "mm_151000_6580000",
        "mm_150000_6580500",
        "mm_150500_6580500",
        "mm_151000_6580500",
    ]

    # 1. `ok`: 2 rutor à 500 m @ 0,5 m = 1000×500 m -> 2,0 M / 4,5 M celler (~0,444)
    kv_ok = _skapa_geografi_kartverktyg(
        tmp_path / "ok",
        rut_ids=alla_6_rutor,
        upplosning_m=0.5,
        fokus=(150100.0, 6580100.0, 150600.0, 6580400.0),
    )
    for svar in (
        kv_ok.status("p1", "scen"),
        kv_ok.status_lage_idag("p1"),
        kv_ok.projektinfo("p1"),
    ):
        omf = svar["berakningsomfang"]
        assert omf is not None
        assert omf["antal_rutor"] == 2
        assert omf["uppskattade_celler"] == 2_000_000
        assert omf["max_sankgraf_celler"] == 4_500_000
        assert omf["niva"] == "ok"
        assert omf["meddelande"] is None
        assert 0.44 < omf["andel_av_tak"] < 0.45

    # 2. `varning`: 2×2 = 4 rutor @ 0,5 m -> 4,0 M / 4,5 M celler (0,889 >= 0,75)
    kv_varn = _skapa_geografi_kartverktyg(
        tmp_path / "varn",
        rut_ids=alla_6_rutor,
        upplosning_m=0.5,
        fokus=(150100.0, 6580100.0, 150600.0, 6580600.0),
    )
    omf_v = kv_varn.status("p1", "scen")["berakningsomfang"]
    assert omf_v["antal_rutor"] == 4
    assert omf_v["uppskattade_celler"] == 4_000_000
    assert omf_v["niva"] == "varning"
    assert omf_v["andel_av_tak"] >= 0.75
    assert omf_v["meddelande"] is not None
    assert "nära serverns cellgräns" in omf_v["meddelande"]
    assert "4,0 / 4,5 M celler" in omf_v["meddelande"]

    # 3. `over_tak`: alla 6 rutor @ 0,5 m -> 6,0 M / 4,5 M celler (1,333 > 1,0)
    kv_over = _skapa_geografi_kartverktyg(
        tmp_path / "over",
        rut_ids=alla_6_rutor,
        upplosning_m=0.5,
        fokus=(150100.0, 6580100.0, 151100.0, 6580600.0),
    )
    omf_o = kv_over.status("p1", "scen")["berakningsomfang"]
    assert omf_o["antal_rutor"] == 6
    assert omf_o["uppskattade_celler"] == 6_000_000
    assert omf_o["niva"] == "over_tak"
    assert omf_o["andel_av_tak"] > 1.0
    assert omf_o["meddelande"] is not None
    assert "överstiger serverns cellgräns" in omf_o["meddelande"]
    assert "6,0 / 4,5 M celler" in omf_o["meddelande"]

    # 4. Respekterar `FLODEN_MAX_SANKGRAF_CELLER` dynamiskt
    monkeypatch.setenv("FLODEN_MAX_SANKGRAF_CELLER", "1500000")
    omf_env = kv_ok.status("p1", "scen")["berakningsomfang"]
    assert omf_env["max_sankgraf_celler"] == 1_500_000
    assert omf_env["niva"] == "over_tak"
    monkeypatch.delenv("FLODEN_MAX_SANKGRAF_CELLER", raising=False)

    # 5. Projekt utan geografi (demovärld) eller utan manifest -> None utan fel
    kv_demo = _skapa_geografi_kartverktyg(
        tmp_path / "demo", rut_ids=None, slag="demo", geografi_ref=None
    )
    assert kv_demo.status("p1", "scen")["berakningsomfang"] is None
    assert kv_demo.status_lage_idag("p1")["berakningsomfang"] is None
    assert kv_demo.projektinfo("p1")["berakningsomfang"] is None

    kv_utan_manifest = _skapa_geografi_kartverktyg(
        tmp_path / "utan_man", rut_ids=None, skriv_manifest_fil=False
    )
    assert kv_utan_manifest.status("p1", "scen")["berakningsomfang"] is None


# -------------------------------------------------- 3. Regressionstest: icke-dict run.json i fragor


def test_fragor_hanterar_icke_dict_run_json_utan_attribute_error(tmp_path: Path) -> None:
    """`run.json` med `"[]"` eller `"null"` får aldrig ge `AttributeError`/500 i `webb.fragor`."""
    fragor = FragaTjanst(
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )

    trasiga_json = ("[]", "null", '{"parametrar": null, "matvarden": [], "utdata": "fel"}')
    for idx, innehall in enumerate(trasiga_json):
        kid = f"k-bad-{idx}"
        kmapp = fragor.korningsmapp / kid
        utmapp = kmapp / "resultat"
        jobbmapp = utmapp / "skyfall" / "fore"
        jobbmapp.mkdir(parents=True, exist_ok=True)
        (jobbmapp / "run.json").write_text(innehall, encoding="utf-8")
        datafil = jobbmapp / "max_djup.tif"
        datafil.write_bytes(b"TESTDATA-FOR-SHA256")

        samman = {
            "fraga_id": "test",
            "sammanfattning": {},
            "scenarier": {"fore": {"nyckel": "k-fore", "rutor": []}},
            "jobb": [
                {
                    "analys": "skyfall",
                    "scenario": "fore",
                    "losare": "vatten.statisk_fyllning",
                    "version": "1",
                    "fran_cache": False,
                    "kortid_s": 0.1,
                    "matvarden": {},
                    "varningar": [],
                }
            ],
            "jamforelser": [],
        }
        (utmapp / "sammanstallning.json").write_text(json.dumps(samman), encoding="utf-8")
        k = Korning(
            korning_id=kid,
            ref="test",
            fraga_id="test",
            fraga_fil="test.json",
            mapp=kmapp,
            status="klar",
            jobb_totalt=1,
        )
        fragor._skriv_status(k)  # noqa: SLF001
        fragor._korningar[kid] = k  # noqa: SLF001

        # 1. `_roller`, `_antaganden`, `_fil_sha256` och `resultat()` kastar aldrig AttributeError
        assert fragor._roller(k, samman["jobb"][0]) == {}  # noqa: SLF001
        ant = fragor._antaganden(k, samman)  # noqa: SLF001
        assert isinstance(ant, dict)
        assert "regn" in ant and "analyser" in ant and "vatten" in ant
        sha = FragaTjanst._fil_sha256(datafil)  # noqa: SLF001
        assert len(sha) == 64

        res = fragor.resultat(kid)
        assert res["korning"]["korning_id"] == kid
        assert isinstance(res["lager"], list)
        assert isinstance(res["antaganden"], dict)

        # 2. `_sankgraf_vektor` ger rent FlodenWebbFel (409), inte AttributeError / 500
        with pytest.raises(FlodenWebbFel) as exc_info:
            fragor.vektor(kid, "fore", "sankor")
        assert exc_info.value.status_kod == 409
