"""Regressionstester för Våg 2 geografiprojekt + `luft.toml` och `Projektkalla`."""

from __future__ import annotations

import csv
import tomllib
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from dagsljus.exempelkvarter import bygg_rutnat, rektangel
from geografi.modell import KONTRAKT_GEOGRAFI, lagra, skriv_manifest
from karna.korplan import planera
from karna.resultatcache import ResultatCache
from karna.utredning import las_fraga
from kommuner.konfig import KommunKonfig, KommunKonfigFel, tolka_kommun
from kommuner.projekt import skapa_projekt, skapa_scenario_hus
from webb.fragor import FragaTjanst
from webb.projektkalla import _FRAGEFILER, bygg_geografikalla

REPO = Path(__file__).resolve().parents[2]
E0, N0 = 150_000.0, 6_576_000.0


def _kommun_dict(**luft_override: Any) -> dict[str, Any]:
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        d = tomllib.load(fh)
    if luft_override:
        d["luft"] = luft_override
    return d


def _skapa_syntetisk_geografi(rot: Path) -> tuple[Path, str, tuple[float, float, float, float]]:
    """Skapar en minimal giltig geografi (`test-geo@v1`) med 2×2 K1 v2-rutor utan nät."""
    geo_rot = rot / "geo"
    k1_tmp = rot / "k1_tmp"
    hus = [
        rektangel(E0 + 8.0, E0 + 20.0, N0 + 8.0, N0 + 20.0, 12.0, "a"),
        rektangel(E0 + 48.0, E0 + 60.0, N0 + 48.0, N0 + 60.0, 18.0, "b"),
    ]
    bygg_rutnat(k1_tmp, hus, e_min_m=E0, n_min_m=N0, antal=2, rutstorlek_m=40.0)
    # Skriv recept.json per ruta (krävs av skapa_scenario_hus)
    for rdir in sorted(k1_tmp.glob("mm_*")):
        (rdir / "recept.json").write_text('{"test": true}\n', encoding="utf-8")
    k1_lager = lagra(geo_rot, k1_tmp)
    karna = (E0 + 10.0, N0 + 10.0, E0 + 70.0, N0 + 70.0)
    gransen = (E0, N0, E0 + 80.0, N0 + 80.0)
    manifest: dict[str, Any] = {
        "kontrakt": KONTRAKT_GEOGRAFI,
        "id": "test-geo",
        "version": "v1",
        "kommun": "goteborg",
        "oppen_for_mcp": True,
        "karna_3011": list(karna),
        "gransen_3011": list(gransen),
        "koordinater": {"publicering_epsg": 3007, "intern_epsg": 3011},
        "buffert": {"vald_m": 10.0, "tillrinning_nar_kanten": False},
        "forberakningar": {"tillrinning": {"ok": True}},
        "kallor": {
            "hojd": {"licens": "CC0-1.0"},
            "byggnader": {"licens": "CC0-1.0", "kalla": "filpaket", "stangd": False},
        },
        "lager": {
            "k1_rutnat": {
                "sha256": k1_lager.sha256,
                "rutor": sorted(p.name for p in k1_lager.mapp.glob("mm_*")),
                "filer": [{"fil": rel, "sha256": sha} for rel, sha in k1_lager.filer],
            }
        },
    }
    skriv_manifest(geo_rot, manifest)
    return geo_rot, "test-geo@v1", karna


def _skapa_luftfiler(rot: Path) -> tuple[Path, Path]:
    vader = rot / "vader.csv"
    with vader.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tid", "vindriktning_grad", "vindhastighet_m_s"])
        for h in range(48):
            w.writerow([f"2026-01-01T{h:02d}:00", 0.0 if h % 2 == 0 else 180.0, 4.0])
    vagar = rot / "vagar.gpkg"
    gpd.GeoDataFrame(
        {"emission_g_m_s": [2.0e-4]},
        geometry=[LineString([(E0, N0 + 36.0), (E0 + 80.0, N0 + 36.0)])],
        crs="EPSG:3011",
    ).to_file(vagar, driver="GPKG")
    return vader, vagar


def test_kommun_konfig_tolkar_valfritt_luft_avsnitt_och_validerar_strikt() -> None:
    k_utan = tolka_kommun(_kommun_dict())
    assert k_utan.luft is None

    k_bara_vader = tolka_kommun(_kommun_dict(vader_fil="vader.csv"))
    assert k_bara_vader.luft is not None
    assert k_bara_vader.luft.vader_fil == "vader.csv"
    assert k_bara_vader.luft.vagar_fil == ""
    assert k_bara_vader.luft.bakgrund_ug_m3 is None

    k_full = tolka_kommun(
        _kommun_dict(
            vader_fil="vader.csv",
            vagar_fil="vagar.gpkg",
            bakgrund_ug_m3=11.5,
            bakgrund_kalla="SLB-analys 2024",
            dx_m=4.0,
            riktningar=2,
            genomstromningar=0.5,
            kriterium="lawson-lddc",
        )
    )
    assert k_full.luft is not None
    assert k_full.luft.bakgrund_ug_m3 == 11.5
    assert k_full.luft.bakgrund_kalla == "SLB-analys 2024"
    assert k_full.luft.kriterium == "lawson-lddc"

    # Ogiltiga konfigurationer avvisas strikt
    with pytest.raises(KommunKonfigFel, match="okända nycklar"):
        tolka_kommun(_kommun_dict(vader_fil="v.csv", felstavad=1))
    with pytest.raises(KommunKonfigFel, match="vader_fil"):
        tolka_kommun(_kommun_dict(vagar_fil="vagar.gpkg"))
    with pytest.raises(KommunKonfigFel, match="vagar_fil"):
        tolka_kommun(_kommun_dict(vader_fil="v.csv", bakgrund_ug_m3=10.0))
    with pytest.raises(KommunKonfigFel, match="minst 0"):
        tolka_kommun(_kommun_dict(vader_fil="v.csv", vagar_fil="v.gpkg", bakgrund_ug_m3=-1.0))
    with pytest.raises(KommunKonfigFel, match="kriterium"):
        tolka_kommun(_kommun_dict(vader_fil="v.csv", kriterium="okand"))


def test_geografiprojekt_skriver_luft_toml_och_laddas_via_projektkalla(tmp_path: Path) -> None:
    assert ("luft.toml", "luft") in _FRAGEFILER

    geo_rot, ref, karna = _skapa_syntetisk_geografi(tmp_path)
    vader, vagar = _skapa_luftfiler(tmp_path)
    projektrot = tmp_path / "projekt"
    avgr = (karna[0] + 5.0, karna[1] + 5.0, karna[2] - 5.0, karna[3] - 5.0)
    konfig_fil = tmp_path / "config.toml"
    konfig_fil.write_text("", encoding="utf-8")

    # 1. Utan [luft]: ingen luft.toml
    k_utan: KommunKonfig = tolka_kommun(_kommun_dict())
    res_utan = skapa_projekt(
        k_utan,
        ref=ref,
        avgransning=avgr,
        namn="Utan Luft",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=projektrot,
        konfig={},
        konfig_fil=konfig_fil,
        skriv=lambda _: None,
    )
    assert not (res_utan.mapp / "fragor" / "luft.toml").exists()

    # 2. Med [luft] men UTAN bakgrund_ug_m3: vindkomfort + halt, ingen mkn (ingen gissad bakgrund!)
    k_ej_bg = tolka_kommun(
        _kommun_dict(vader_fil=str(vader), vagar_fil=str(vagar), dx_m=4.0, riktningar=2)
    )
    res_ej_bg = skapa_projekt(
        k_ej_bg,
        ref=ref,
        avgransning=avgr,
        namn="Luft Utan Bg",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=projektrot,
        konfig={},
        konfig_fil=konfig_fil,
        skriv=lambda _: None,
    )
    f_ej_bg = las_fraga(res_ej_bg.mapp / "fragor" / "luft.toml")
    assert [a.id for a in f_ej_bg.analyser] == ["vindkomfort", "halt"]
    assert [a.losare for a in f_ej_bg.analyser] == ["luft.vindkomfort", "luft.halt"]

    # 3. Med [luft] + explicit bakgrund_ug_m3: vindkomfort + halt + mkn (beror_pa = ["halt"])
    k_med = tolka_kommun(
        _kommun_dict(
            vader_fil=str(vader),
            vagar_fil=str(vagar),
            bakgrund_ug_m3=14.0,
            bakgrund_kalla="Mätstation Femman 2024",
            dx_m=4.0,
            riktningar=2,
            genomstromningar=0.25,
        )
    )
    res_med = skapa_projekt(
        k_med,
        ref=ref,
        avgransning=avgr,
        namn="Luft Komplett",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=projektrot,
        konfig={},
        konfig_fil=konfig_fil,
        skriv=lambda _: None,
    )
    luft_fil = res_med.mapp / "fragor" / "luft.toml"
    assert luft_fil.is_file()
    f_med = las_fraga(luft_fil)
    assert [a.id for a in f_med.analyser] == ["vindkomfort", "halt", "mkn"]
    assert f_med.analys("mkn").beror_pa == ("halt",)
    assert f_med.analys("mkn").parametrar["bakgrund_ug_m3"] == 14.0
    assert f_med.analys("vindkomfort").indata["vader"].resolve() == vader.resolve()
    assert f_med.analys("halt").indata["vagar"].resolve() == vagar.resolve()

    # Scenario läggs in i luft.toml med jämförelser för både vindkomfort och halt.
    skapa_scenario_hus(
        res_med.mapp,
        kommun=k_med,
        geo_rot=geo_rot,
        namn="Kvarter Nytt",
        centrum=(E0 + 36.0, N0 + 20.0),
        bredd_m=8.0,
        djup_m=8.0,
        hojd_m=16.0,
        skriv=lambda _: None,
    )
    f_scen = las_fraga(luft_fil)
    assert set(f_scen.scenarier) == {"bas", "kvarter-nytt"}
    assert {(j.analys, j.utdata) for j in f_scen.jamforelser} == {
        ("vindkomfort", "forstarkning"),
        ("halt", "arsmedel"),
    }

    # Planering med kärnan måste gå igenom för alla tre analyser och båda scenarierna.
    plan = planera(f_scen, ResultatCache(tmp_path / "cache"))
    assert len(plan.jobb) == 6  # 3 analyser × 2 scenarier

    # 4. Projektkalla laddar luft.toml under mallar["luft"]
    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[res_med.mapp / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        projektrot=projektrot,
    )
    kalla = bygg_geografikalla(
        res_med.projekt_id, res_med.mapp, geomapp=geo_rot, fragor=tjanst
    )
    assert "luft" in kalla.mallar
    assert kalla.mallar["luft"].kort == "luft"
    assert [a.losare for a in kalla.mallar["luft"].fraga.analyser] == [
        "luft.vindkomfort",
        "luft.halt",
        "luft.mkn",
    ]
    assert kalla.mallar["luft"].fore_id == "bas"

    # Utan luft.toml laddas inte "luft" i mallar
    tjanst_u = FragaTjanst(
        rot=tmp_path,
        fragemappar=[res_utan.mapp / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        projektrot=projektrot,
    )
    kalla_u = bygg_geografikalla(
        res_utan.projekt_id, res_utan.mapp, geomapp=geo_rot, fragor=tjanst_u
    )
    assert "luft" not in kalla_u.mallar
