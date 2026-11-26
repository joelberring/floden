"""Regressionstester för Våg 3 P1 Dagsljus:
- K21 inkrementell horisont i `dagsljus.svf_mark` och `dagsljus.soltimmar_mark`
  (samt delad horisontcache utan dubbelberäkning mellan `sol` och `svf`/`horisont`),
- K-B10 fasadvarningar i `SoltimmarFasad.kor` (`inuti_hinder`, `utanfor_data`,
  `trunk_ant`, låg vinkelupplösning `< 128`),
- P1 SVF (`dagsljus.svf_mark`) i geografiprojektets `dagsljus.toml` och höstdagjämning
  (`2026-09-22`) i `data/kommuner/{goteborg,stockholm}.toml`.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import dagsljus.floden_losare as floden_losare_mod
import dagsljus.horisont as horisont_mod
from dagsljus.exempelkvarter import bygg_ruta, bygg_rutnat, byggnads_id, rektangel
from dagsljus.floden_losare import SoltimmarFasad, rensa_horisont_cache
from gemensamt.hash import sha256_fil
from geografi.modell import KONTRAKT_GEOGRAFI, lagra, skriv_manifest
from karna.analyslosare import LosarKontext
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.rutscenario import RutScenario, _las_ruta
from karna.scenario import Andring, Planscenario, skriv_scenario
from karna.utredning import fraga_fran_dict, las_fraga
from kommuner.konfig import las_kommun
from kommuner.projekt import skapa_projekt, skapa_scenario_hus
from testsvit.k1_io import las_geotiff

REPO = Path(__file__).resolve().parents[2]
_PAR_SVF = {"hojd_max_m": 20.0, "h_min_grader": 10.0, "azimutriktningar": 32}
_PAR_SOL = {
    "hojd_max_m": 20.0,
    "h_min_grader": 10.0,
    "azimutriktningar": 32,
    "datum": ["2026-03-20", "2026-09-22"],
    "steg_min": 30,
}


def _rekt_poly(x0: float, x1: float, y0: float, y1: float) -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def _skapa_k1_v2_bas(
    rot: Path, *, sida_m: float = 100.0, med_standard: bool = False
) -> tuple[Path, Path, str]:
    hus = [rektangel(150020.0, 150032.0, 6576020.0, 6576035.0, 12.0, "hus_bas")]
    if med_standard:
        hus.append(rektangel(150040.0, 150048.0, 6576020.0, 6576030.0, None, "hus_std"))
    ruta_mapp = bygg_ruta(rot, 150000.0, 6576000.0, sida_m, hus)
    bas_sha = sha256_fil(ruta_mapp / "recept.json")
    return rot, ruta_mapp, bas_sha


def _skriv_k9(sokvag: Path, scenario_id: str, bas_sha: str, andringar: list[Andring]) -> Path:
    scen = Planscenario(
        scenario_id=scenario_id,
        bas_recept=(bas_sha,),
        andringar=tuple(andringar),
    )
    return skriv_scenario(scen, sokvag)


def _installera_cellstrale_raknare(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Räknar deterministiskt antal beräknade cell-strålar i `horisont_riktning_ruta`."""
    orig = horisont_mod.horisont_riktning_ruta
    raknare: dict[str, int] = {"anrop": 0, "cell_stralar": 0}

    def _wrapper(
        falt: Any,
        phi_rad: float,
        max_avstand_m: float,
        *,
        fonster: tuple[int, int, int, int] | None = None,
        **kw: Any,
    ) -> Any:
        ny, nx = falt.z_m.shape
        r_a, r_b, c_a, c_b = fonster if fonster is not None else (0, ny, 0, nx)
        raknare["anrop"] += 1
        raknare["cell_stralar"] += (r_b - r_a) * (c_b - c_a)
        return orig(falt, phi_rad, max_avstand_m, fonster=fonster, **kw)

    monkeypatch.setattr(horisont_mod, "horisont_riktning_ruta", _wrapper)
    monkeypatch.setattr(floden_losare_mod, "horisont_riktning_ruta", _wrapper)
    return raknare


# ------------------------------------------- 1. K21 inkrementell SVF & soltimmar


@pytest.mark.parametrize("losare_kort", ["svf_mark", "soltimmar_mark"])
@pytest.mark.parametrize(
    ("fall", "andringar"),
    [
        (
            "nytt_hus",
            [
                Andring(
                    typ="byggnad_ny",
                    andring_id="hus_nytt",
                    geometri=_rekt_poly(150068.0, 150076.0, 6576068.0, 6576076.0),
                    egenskaper={"hojd_m": 14.0},
                )
            ],
        ),
        (
            "hojt_hus",
            [
                Andring(
                    typ="byggnad_hojd",
                    andring_id="hojd_upp",
                    egenskaper={"byggnads_id": byggnads_id("hus_bas"), "hojd_m": 18.0},
                )
            ],
        ),
        (
            "borttaget_hus",
            [
                Andring(
                    typ="byggnad_bort",
                    andring_id="riv_hus",
                    egenskaper={"byggnads_id": byggnads_id("hus_bas")},
                )
            ],
        ),
    ],
)
def test_k21_inkrementell_svf_och_soltimmar_identisk_med_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    losare_kort: str,
    fall: str,
    andringar: list[Andring],
) -> None:
    """Inkrementell `svf_mark`/`soltimmar_mark`: `max(|inkr - full|) <= 1e-6` och färre strålar."""
    rensa_horisont_cache()
    bas_rot, ruta_mapp, bas_sha = _skapa_k1_v2_bas(
        tmp_path / "bas", sida_m=100.0, med_standard=(fall == "nytt_hus")
    )
    rid = ruta_mapp.name
    k9_fil = _skriv_k9(tmp_path / f"{fall}.k9.json", fall, bas_sha, andringar)

    losare = f"dagsljus.{losare_kort}"
    par = _PAR_SVF if losare_kort == "svf_mark" else _PAR_SOL
    huvud_tif = "svf.tif" if losare_kort == "svf_mark" else "soltimmar.tif"

    # 1. Värm cachen på basen
    fraga_bas = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"bas-{losare_kort}-{fall}",
            "scenarier": {"fore": {"bas": str(bas_rot)}},
            "analyser": [{"id": "a", "losare": losare, "parametrar": par}],
        },
        tmp_path,
    )
    res_bas = kor_utredning(fraga_bas, ResultatCache(tmp_path / "c_bas"), tmp_path / "ut_bas")
    assert res_bas.sammanstallning["jobb"][0]["matvarden"]["inkrementell"] is False

    # 2. Inkrementell körning på K9-scenariot med deterministisk strålräkning
    raknare = _installera_cellstrale_raknare(monkeypatch)
    fraga_efter = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"efter-{losare_kort}-{fall}",
            "scenarier": {"efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]}},
            "analyser": [{"id": "a", "losare": losare, "parametrar": par}],
        },
        tmp_path,
    )
    res_i = kor_utredning(fraga_efter, ResultatCache(tmp_path / "c_i"), tmp_path / "ut_i")
    mv_i = res_i.sammanstallning["jobb"][0]["matvarden"]
    assert mv_i["inkrementell"] is True
    assert mv_i["inkrementell_orsak"] == "lokal_uppdatering"
    assert mv_i["andrade_celler"] > 0
    stralar_inkr = raknare["cell_stralar"]

    # 3. Full referenskörning (rensad cache)
    raknare["anrop"] = 0
    raknare["cell_stralar"] = 0
    rensa_horisont_cache()
    res_f = kor_utredning(fraga_efter, ResultatCache(tmp_path / "c_f"), tmp_path / "ut_f")
    mv_f = res_f.sammanstallning["jobb"][0]["matvarden"]
    assert mv_f["inkrementell"] is False
    stralar_full = raknare["cell_stralar"]

    # Deterministisk arbetsminskning + numerisk identitet (atol=1e-6)
    assert stralar_full > 0
    assert stralar_inkr < stralar_full, (
        f"{losare} ({fall}): inkrementell räknade {stralar_inkr} >= full {stralar_full}"
    )
    if fall == "nytt_hus":
        assert stralar_inkr < 0.75 * stralar_full

    mapp_i = tmp_path / "ut_i" / "a" / "efter" / rid
    mapp_f = tmp_path / "ut_f" / "a" / "efter" / rid
    for tif in (huvud_tif, "trunkering.tif", "standardhojd.tif"):
        a_i, _ = las_geotiff(mapp_i / tif)
        a_f, _ = las_geotiff(mapp_f / tif)
        max_fel = float(np.max(np.abs(a_i.astype(np.float64) - a_f.astype(np.float64))))
        assert max_fel <= 1e-6, f"{losare} ({fall}) {tif}: max_fel={max_fel}"


@pytest.mark.parametrize("losare_kort", ["svf_mark", "soltimmar_mark"])
def test_k21_svf_och_soltimmar_fallback_vid_stor_andring(tmp_path: Path, losare_kort: str) -> None:
    """Ändring som täcker >= 25 % av rutan faller transparent tillbaka till full beräkning."""
    rensa_horisont_cache()
    bas_rot, ruta_mapp, bas_sha = _skapa_k1_v2_bas(tmp_path / "bas", sida_m=60.0)
    rid = ruta_mapp.name
    # 35×35 m = 1225 celler av 3600 (~34 % >= 25 %)
    k9_fil = _skriv_k9(
        tmp_path / "stor.k9.json",
        "stor-andring",
        bas_sha,
        [
            Andring(
                typ="byggnad_ny",
                andring_id="jattehus",
                geometri=_rekt_poly(150020.0, 150055.0, 6576020.0, 6576055.0),
                egenskaper={"hojd_m": 15.0},
            )
        ],
    )
    losare = f"dagsljus.{losare_kort}"
    par = _PAR_SVF if losare_kort == "svf_mark" else _PAR_SOL
    tif = "svf.tif" if losare_kort == "svf_mark" else "soltimmar.tif"

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"fallback-{losare_kort}",
            "scenarier": {
                "fore": {"bas": str(bas_rot)},
                "efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]},
            },
            "analyser": [{"id": "a", "losare": losare, "parametrar": par}],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "c_s"), tmp_path / "ut_s")
    jobb = {j["scenario"]: j for j in res.sammanstallning["jobb"]}
    assert jobb["efter"]["matvarden"]["inkrementell"] is False
    assert jobb["efter"]["matvarden"]["inkrementell_orsak"] == "for_stor_andring"

    rensa_horisont_cache()
    fraga_ref = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"ref-{losare_kort}",
            "scenarier": {"efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]}},
            "analyser": [{"id": "a", "losare": losare, "parametrar": par}],
        },
        tmp_path,
    )
    kor_utredning(fraga_ref, ResultatCache(tmp_path / "c_r"), tmp_path / "ut_r")
    a_s, _ = las_geotiff(tmp_path / "ut_s" / "a" / "efter" / rid / tif)
    a_r, _ = las_geotiff(tmp_path / "ut_r" / "a" / "efter" / rid / tif)
    np.testing.assert_allclose(a_s, a_r, atol=1e-6)


def test_delad_horisontcache_eliminerar_dubbelberakning_mellan_sol_och_svf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sol` + `svf` räknar exakt 1,0× riktningar på bas och bara skuggfönster på K9."""
    rensa_horisont_cache()
    bas_rot, ruta_mapp, bas_sha = _skapa_k1_v2_bas(tmp_path / "bas", sida_m=100.0)
    rid = ruta_mapp.name
    k9_fil = _skriv_k9(
        tmp_path / "litet.k9.json",
        "litet-hus",
        bas_sha,
        [
            Andring(
                typ="byggnad_ny",
                andring_id="litet",
                geometri=_rekt_poly(150076.0, 150082.0, 6576076.0, 6576082.0),
                egenskaper={"hojd_m": 10.0},
            )
        ],
    )
    par_gem = {"hojd_max_m": 15.0, "h_min_grader": 15.0, "azimutriktningar": 32}
    par_sol = {**par_gem, "datum": ["2026-03-20", "2026-09-22"], "steg_min": 30}

    raknare = _installera_cellstrale_raknare(monkeypatch)
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "delad-horisont",
            "scenarier": {
                "bas": {"bas": str(bas_rot)},
                "efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]},
            },
            "analyser": [
                {"id": "sol", "losare": "dagsljus.soltimmar_mark", "parametrar": par_sol},
                {"id": "svf", "losare": "dagsljus.svf_mark", "parametrar": par_gem},
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "c_del"), tmp_path / "ut_del")
    total_stralar = raknare["cell_stralar"]
    en_full_kub = 100 * 100 * 32
    # Basen tar exakt 1,0× (sol + svf kompletterar varandra) och K9-scenariot tar < 0,35×:
    assert total_stralar < 1.35 * en_full_kub, (
        f"För många cell-strålar: {total_stralar} mot 1 full kub = {en_full_kub}"
    )
    jobb = {(j["analys"], j["scenario"]): j for j in res.sammanstallning["jobb"]}
    assert jobb[("svf", "bas")]["matvarden"]["inkrementell"] is True
    assert jobb[("sol", "efter")]["matvarden"]["inkrementell"] is True
    assert jobb[("svf", "efter")]["matvarden"]["inkrementell"] is True

    # Verifiera identitet mot kalla enskilda körningar (atol=1e-6)
    rensa_horisont_cache()
    f_ref_svf = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "ref-svf",
            "scenarier": {"efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]}},
            "analyser": [{"id": "svf", "losare": "dagsljus.svf_mark", "parametrar": par_gem}],
        },
        tmp_path,
    )
    kor_utredning(f_ref_svf, ResultatCache(tmp_path / "c_ref_svf"), tmp_path / "ut_ref_svf")
    svf_del, _ = las_geotiff(tmp_path / "ut_del" / "svf" / "efter" / rid / "svf.tif")
    svf_ref, _ = las_geotiff(tmp_path / "ut_ref_svf" / "svf" / "efter" / rid / "svf.tif")
    np.testing.assert_allclose(svf_del, svf_ref, atol=1e-6)

    # Även horisont -> svf_mark (med beror_pa) ger 0 extra cell-strålar i svf_mark
    rensa_horisont_cache()
    f_hor = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "hor-till-svf",
            "scenarier": {"bas": {"bas": str(bas_rot)}},
            "analyser": [
                {"id": "hor", "losare": "dagsljus.horisont", "parametrar": par_gem},
                {
                    "id": "svf",
                    "losare": "dagsljus.svf_mark",
                    "beror_pa": ["hor"],
                    "parametrar": par_gem,
                },
            ],
        },
        tmp_path,
    )
    raknare["anrop"] = 0
    raknare["cell_stralar"] = 0
    res_hor = kor_utredning(f_hor, ResultatCache(tmp_path / "c_hor"), tmp_path / "ut_hor")
    assert raknare["cell_stralar"] == en_full_kub  # 0 extra strålar i svf!
    j_hor = {j["analys"]: j for j in res_hor.sammanstallning["jobb"]}
    assert j_hor["svf"]["matvarden"]["inkrementell"] is True
    assert j_hor["svf"]["matvarden"]["inkrementell_orsak"] == "cache_traff"


# ------------------------------------------- 2. K-B10 fasadvarningar i SoltimmarFasad.kor


def test_soltimmar_fasad_varnar_inuti_hinder_utanfor_data_trunkering_och_fa_riktningar(
    tmp_path: Path,
) -> None:
    """`SoltimmarFasad.kor` rapporterar explicita varningar för osäkra fasadpunkter och låg K."""
    hus = [rektangel(150016.0, 150026.0, 6576016.0, 6576026.0, 12.0, "hus_a")]
    ruta_mapp = bygg_ruta(tmp_path / "bas", 150000.0, 6576000.0, 40.0, hus)
    ruta = _las_ruta(ruta_mapp, ruta_mapp.name)
    sc = RutScenario(id="test", nyckel="k", rutor=(ruta,))

    # 3 CSV-punkter som ägs av rutan:
    # - ok: söder om huset (utanför väggen, normal söderut = 180°), strålar trunkeras vid kanten
    # - inuti: mitt inne i huset på z = 2 m (< takhöjd 12 m) -> inuti_hinder
    # - kant_ute: 0,1 m innanför östra rutkanten (x = 150039,9) med normal österut (90°):
    #   ut_eff = 0,5 m flyttar startpunkten till x0 = 150040,4 -> utanfor_data!
    csv_fil = tmp_path / "fasader.csv"
    with csv_fil.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["fasad_id", "x_m", "y_m", "z_m", "normal_azimut_grader"])
        w.writerow(["ok_syd", 150021.0, 6576016.0, 2.0, 180.0])
        w.writerow(["inuti", 150021.0, 6576021.0, 2.0, 180.0])
        w.writerow(["kant_ute", 150039.9, 6576020.0, 2.0, 90.0])

    los = SoltimmarFasad()
    par_lag_k = los.validera_parametrar(
        {
            "datum": ["2026-03-20"],
            "hojd_max_m": 20.0,
            "h_min_grader": 10.0,  # radie ~113 m > 40 m ruta -> garanterad trunkering
            "azimutriktningar": 64,  # -> halvplansriktningar = 32 (< 64, motsvarar 64 < 128)
            "steg_min": 30,
        }
    )
    ut = los.kor(
        LosarKontext(
            tmp_path / "ut_lag",
            sc,
            par_lag_k,
            {"fasadpunkter": csv_fil},
            {},
            ruta=ruta,
        )
    )

    with (tmp_path / "ut_lag" / "soltimmar_fasad.csv").open(encoding="utf-8", newline="") as fh:
        rader = {r["fasad_id"]: r for r in csv.DictReader(fh)}
    assert rader["ok_syd"]["status"] == "ok"
    assert int(rader["ok_syd"]["trunkerade_riktningar"]) > 0
    assert rader["inuti"]["status"] == "inuti_hinder"
    assert rader["inuti"]["soltimmar_h"] == ""
    assert rader["kant_ute"]["status"] == "utanfor_data"
    assert rader["kant_ute"]["soltimmar_h"] == ""

    var_text = "\n".join(ut.varningar)
    assert any("inuti_hinder" in v and "under höjden" in v for v in ut.varningar), var_text
    assert any("utanfor_data" in v and "utanför höjdfältet" in v for v in ut.varningar), var_text
    assert any("trunkerade_riktningar" in v for v in ut.varningar), var_text
    assert any(
        "halvplansriktningar=32" in v and "64 azimutriktningar" in v for v in ut.varningar
    ), var_text

    # Med tillräckligt många riktningar (180 -> 90 halvplansriktningar) uteblir varningen.
    par_hog_k = los.validera_parametrar(
        {
            "datum": ["2026-03-20"],
            "hojd_max_m": 20.0,
            "h_min_grader": 10.0,
            "azimutriktningar": 180,
            "steg_min": 30,
        }
    )
    ut_hog = los.kor(
        LosarKontext(
            tmp_path / "ut_hog",
            sc,
            par_hog_k,
            {"fasadpunkter": csv_fil},
            {},
            ruta=ruta,
        )
    )
    assert not any("halvplansriktningar=" in v for v in ut_hog.varningar)


# ------------------------------------------- 3. P1 SVF + höstdagjämning i kommuner & projekt


def test_kommunfiler_innehaller_var_och_hostdagjamning() -> None:
    for kid in ("goteborg", "stockholm"):
        k = las_kommun(kid, rot=REPO)
        assert list(k.dagsljus["datum"]) == ["2026-03-20", "2026-09-22"], kid


def test_geografiprojekt_genererar_dagsljus_toml_med_svf_och_hostdagjamning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nya projekt får `dagsljus.svf_mark`, vår-/höstdagjämning och ingen dubbel horisont."""
    rensa_horisont_cache()
    e0, n0 = 150_000.0, 6_576_000.0
    geo_rot = tmp_path / "geo"
    k1_tmp = tmp_path / "k1_tmp"
    hus = [
        rektangel(e0 + 10.0, e0 + 22.0, n0 + 10.0, n0 + 22.0, 12.0, "a"),
        rektangel(e0 + 50.0, e0 + 62.0, n0 + 50.0, n0 + 62.0, 16.0, "b"),
    ]
    bygg_rutnat(k1_tmp, hus, e_min_m=e0, n_min_m=n0, antal=2, rutstorlek_m=40.0)
    for rdir in sorted(k1_tmp.glob("mm_*")):
        (rdir / "recept.json").write_text('{"test": true}\n', encoding="utf-8")
    k1_lager = lagra(geo_rot, k1_tmp)
    karna = (e0 + 8.0, n0 + 8.0, e0 + 72.0, n0 + 72.0)
    gransen = (e0, n0, e0 + 80.0, n0 + 80.0)
    skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "svf-geo",
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
        },
    )

    gbg_orig = las_kommun("goteborg", rot=REPO)
    gbg = replace(
        gbg_orig,
        dagsljus={
            **gbg_orig.dagsljus,
            "azimutriktningar": 32,
            "steg_min": 60,
            "hojd_max_m": 20,
            "h_min_grader": 15,
            "fasad_steg_m": 10.0,
        },
    )
    konfig_fil = tmp_path / "config.toml"
    konfig_fil.write_text("", encoding="utf-8")
    res = skapa_projekt(
        gbg,
        ref="svf-geo@v1",
        avgransning=(e0 + 10.0, n0 + 10.0, e0 + 30.0, n0 + 30.0),  # 1 fokusruta
        namn="SVF Test",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=tmp_path / "projekt",
        konfig={},
        konfig_fil=konfig_fil,
        skriv=lambda _: None,
    )
    dag_fil = res.mapp / "fragor" / "dagsljus.toml"
    f_dag = las_fraga(dag_fil)
    assert [a.id for a in f_dag.analyser] == ["sol", "svf", "vsc"]
    assert [a.losare for a in f_dag.analyser] == [
        "dagsljus.soltimmar_mark",
        "dagsljus.svf_mark",
        "dagsljus.vsc_fasad",
    ]
    assert f_dag.analys("sol").parametrar["datum"] == ["2026-03-20", "2026-09-22"]
    assert f_dag.analys("svf").parametrar["azimutriktningar"] == 32
    assert f_dag.analys("svf").fokus == f_dag.analys("sol").fokus

    # Lägg till ett scenario och kontrollera att både sol och svf jämförs utan dubbel horisont
    skapa_scenario_hus(
        res.mapp,
        kommun=gbg,
        geo_rot=geo_rot,
        namn="Gardsbyggnad",
        centrum=(e0 + 28.0, n0 + 28.0),
        bredd_m=6.0,
        djup_m=6.0,
        hojd_m=12.0,
        skriv=lambda _: None,
    )
    f_scen = las_fraga(dag_fil)
    assert {(j.analys, j.utdata) for j in f_scen.jamforelser} == {
        ("sol", "soltimmar"),
        ("svf", "svf"),
    }

    raknare = _installera_cellstrale_raknare(monkeypatch)
    kor = kor_utredning(f_scen, ResultatCache(tmp_path / "cache"), tmp_path / "ut_proj")
    # 1 fokusruta (40×40 m = 1600 celler) × 32 riktningar = 51 200 cell-strålar för full bas;
    # delad horisontcache + inkrementell K9 håller sol + svf klart under 2 fulla kuber:
    en_kub = 40 * 40 * 32
    assert raknare["cell_stralar"] < 2 * en_kub, (
        f"Dubbel horisontberäkning detekterad: {raknare['cell_stralar']} >= 2 * {en_kub}"
    )
    jobb = {(j["analys"], j["scenario"]): j for j in kor.sammanstallning["jobb"]}
    assert jobb[("svf", "bas")]["matvarden"]["inkrementell"] is True
    assert jobb[("sol", "gardsbyggnad")]["matvarden"]["inkrementell"] is True
    assert jobb[("svf", "gardsbyggnad")]["matvarden"]["inkrementell"] is True
    assert 0.0 < jobb[("svf", "bas")]["matvarden"]["svf_medel"] < 1.0
    assert jobb[("sol", "bas")]["matvarden"]["datum"] == 2
