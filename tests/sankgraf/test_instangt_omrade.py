"""Slutsänkor (K2/v2): ett markområde som byggnader helt omger.

Sedan K2/v2 blir en sådan gård en slutsänka: rotnod utan överfall, med ett hinder (lägsta
byggnadscellen runt gården, höjden i `mark.tif`). Vattnet blir stående; når det hindret räknas
överskottet som utflöde och flaggas. Ett område vid celler utan höjd ger fortfarande fel.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd, kor_handelsestyrd_kardinal
from lapp.lokal_omrakning import uppdatera_sankgraf_lokalt
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf, hitta_instangda_omraden
from sankgraf.k2_las import las_sankgraf_k2
from sankgraf.persistens import filtrera_persistens
from sankgraf.typer import (
    KONTRAKT_K2,
    KONTRAKT_K2_V2,
    MARKSLAG_BYGGNAD,
    SLUTSANKA_VARNING,
    TYP_SLUTSANKA,
    InstangtOmradeFel,
    SankGraf,
    SankGrafFel,
    skriv_sankgraf_k2,
    skriv_statiskt_resultat_k6,
    slutsanka_varningar,
)
from testsvit.k1_io import GeoTransformInfo, las_geotiff

N = 20


def _geo(n: int = N) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=n, hojd=n, x_min=0.0, y_max=float(n), pixelstorlek_x=1.0, pixelstorlek_y=1.0,
        epsg=3011, dtype="float32",
    )


def _varld(oppning: bool) -> tuple[NDArray[np.float32], NDArray[np.uint8], GeoTransformInfo]:
    """Svagt lutande mark med en 6x6-gård (sänka) omgiven av byggnader (rad/kolumn 5..12).

    Byggnaderna har taket 10 m över marken (som i `mark.tif`), utom ett lägre hus i
    nordöstra hörnet (rad 5, kolumn 12) med taket 3 m över marken: hindret.
    """
    jj = np.arange(N, dtype=np.float32)[None, :].repeat(N, axis=0)
    z = (10.0 - 0.01 * jj).astype(np.float32)
    z[7:11, 7:11] -= 0.5  # gårdens sänka
    slag = np.zeros((N, N), dtype=np.uint8)
    slag[5:13, 5:13] = MARKSLAG_BYGGNAD
    slag[6:12, 6:12] = 0  # gården: 6x6 celler
    if oppning:
        slag[5, 8] = 0  # lucka i norra huset: gatan förblir öppen
    hus = slag == MARKSLAG_BYGGNAD
    z[hus] += np.float32(10.0)
    z[5, 12] -= np.float32(7.0)  # lägre hus i hörnet: 3 m över marken (ej 4-granne till gården)
    z[6, 12] -= np.float32(6.0)  # lägsta hindret som gränsar till gården: 4 m över marken
    return z, slag, _geo()


def _bygg(oppning: bool = False) -> tuple[SankGraf, NDArray[np.float32]]:
    z, slag, geo = _varld(oppning)
    osak = np.full((N, N), 0.05, dtype=np.float32)
    return bygg_sankgraf(z, osak, slag, geo), z


def test_hitta_instangda_omraden_ger_storlek_och_cell() -> None:
    aktiv = np.ones((10, 10), dtype=bool)
    aktiv[2:7, 2:7] = False
    aktiv[3:6, 3:6] = True  # 3x3 = 9 instängda celler innanför en ring av falska celler
    assert hitta_instangda_omraden(aktiv) == [(9, 3, 3)]
    aktiv[4, 2] = True  # lucka i ringen (4-grannar): inte längre instängt
    aktiv[4, 3] = True
    assert hitta_instangda_omraden(aktiv) == []


def test_helt_omsluten_garden_blir_slutsanka_med_hinder() -> None:
    z, slag, geo = _varld(oppning=False)
    graf = bygg_sankgraf(z, np.full((N, N), 0.05, dtype=np.float32), slag, geo)
    slut = graf.slutsankor()
    assert len(slut) == 1
    s = slut[0]
    assert s.typ == TYP_SLUTSANKA and s.forald_id is None and s.ut_till_id is None
    # Hindret: lägsta byggnadscell som är 4-granne till gården, (6, 12) med z = 10 - 0,12 + 4.
    assert s.hinder_rh2000 == pytest.approx(float(z[6, 12]))
    assert s.hinder_rh2000 == pytest.approx(13.88, abs=1e-5)
    assert s.overfall_rh2000 == s.hinder_rh2000  # i Python: vatten över hindret rinner ut
    assert graf.overfall_celler[s.nod_id] == ((6, 12),)
    assert (s.overfall_x, s.overfall_y) == (12.5, N - 6.5)
    assert sum(graf.noder[lid].avrinningsyta_m2 for lid in graf.lov_under_nod(s.nod_id)) == 36.0
    assert int(graf.slutsanka_mask().sum()) == 36
    assert graf.kontrakt == KONTRAKT_K2_V2
    # Volymkurvan slutar vid hindret och är exakt: hela gården under hindret.
    gard = z[6:12, 6:12].astype(np.float64)
    assert s.max_volym_m3 == pytest.approx(float(np.sum(s.hinder_rh2000 - gard)), rel=1e-12)


def test_cell_utan_hojd_i_ringen_ger_fortfarande_tydligt_fel() -> None:
    z, slag, geo = _varld(oppning=False)
    z[5, 8] = np.nan  # ringen har en cell utan höjd: hindret är okänt
    with pytest.raises(InstangtOmradeFel) as ex:
        bygg_sankgraf(z, np.full((N, N), 0.05, dtype=np.float32), slag, geo)
    assert ex.value.antal_celler == 36
    assert isinstance(ex.value, SankGrafFel)
    assert "utan höjd" in str(ex.value)


def test_lucka_i_ringen_ger_en_vanlig_sanka_utan_slutsankor() -> None:
    graf, _ = _bygg(oppning=True)
    assert not graf.har_slutsankor and graf.kontrakt == KONTRAKT_K2
    garden = list(graf.noder.values())
    assert len(garden) == 1
    # Gårdens enda utlopp är luckan på rad 5, kolumn 8 (z = 10,00 - 0,01 * 8 = 9,92 m).
    assert garden[0].overfall_rh2000 == pytest.approx(9.92, abs=1e-3)


def test_statisk_fyllning_kant_svar_och_hindret() -> None:
    z, slag, geo = _varld(oppning=False)
    graf = bygg_sankgraf(z, np.full((N, N), 0.05, dtype=np.float32), slag, geo)
    s = graf.slutsankor()[0]
    # 100 mm regn på gården (36 m²) = 3,6 m³; taken räknas inte (regn på byggnadsceller).
    res = statisk_fyllning(graf, 0.10)
    (r,) = res.slutsankor
    assert r.volym_m3 == pytest.approx(3.6, rel=1e-12)
    assert r.over_hinder_m3 == 0.0 and not r.nadde_hinder
    # Känt svar: nivån L där gårdens celler rymmer 3,6 m³, sum(max(0, L - z)) = 3,6 (bisektion).
    gard = z[6:12, 6:12].astype(np.float64)
    lo, hi = float(gard.min()), float(gard.max()) + 1.0
    for _ in range(200):
        mitt = 0.5 * (lo + hi)
        lo, hi = (mitt, hi) if float(np.sum(np.maximum(0.0, mitt - gard))) < 3.6 else (lo, mitt)
    assert r.niva_rh2000 == pytest.approx(lo, abs=1e-9)
    assert r.max_djup_m == pytest.approx(lo - float(gard.min()), abs=1e-6)
    assert res.volymfel_rel < 1e-12
    assert res.slutsanka_mask is not None and int(res.slutsanka_mask.sum()) == 36
    assert slutsanka_varningar(res.slutsankor) == (SLUTSANKA_VARNING,)
    # Så mycket regn att hindret nås: allt över hindret räknas som utflöde och flaggas.
    stor = statisk_fyllning(graf, 50.0)
    (r2,) = stor.slutsankor
    assert r2.niva_rh2000 == pytest.approx(s.hinder_rh2000)
    assert r2.over_hinder_m3 == pytest.approx(36 * 50.0 - s.max_volym_m3, rel=1e-9)
    assert r2.nadde_hinder
    assert stor.volymfel_rel < 1e-12
    assert len(slutsanka_varningar(stor.slutsankor)) == 2


def test_persistensfiltret_tar_aldrig_bort_en_slutsanka() -> None:
    graf, _ = _bygg()
    filt = filtrera_persistens(graf, faktor=1e6)
    assert [n.typ for n in filt.noder.values()] == [TYP_SLUTSANKA]
    res = statisk_fyllning(filt, 0.10)
    assert res.slutsankor[0].volym_m3 == pytest.approx(3.6, rel=1e-12)


def test_k2_v2_rundresa_och_overfall_null_i_fil(tmp_path: Path) -> None:
    graf, _ = _bygg()
    filer = skriv_sankgraf_k2(graf, tmp_path / "k2", kodversion="t", start="2026-01-01T00:00:00Z")
    huvud = json.loads(filer["sankgraf_json"].read_text(encoding="utf-8"))
    assert huvud["kontrakt"] == KONTRAKT_K2_V2 and huvud["antal_slutsankor"] == 1
    (nod,) = [n for n in huvud["noder"] if n["typ"] == TYP_SLUTSANKA]
    assert nod["overfall_rh2000"] is None and nod["hinder_rh2000"] > 13.0
    tillbaka = las_sankgraf_k2(tmp_path / "k2")
    assert tillbaka.noder == graf.noder
    assert tillbaka.overfall_celler == graf.overfall_celler
    if "sankgraf_parquet" in filer:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        tab = pq.read_table(filer["sankgraf_parquet"]).to_pylist()
        rad = next(r for r in tab if r["typ"] == TYP_SLUTSANKA)
        assert rad["overfall_rh2000"] is None and rad["hinder_rh2000"] == nod["hinder_rh2000"]
    # En v1-fil kan inte ha slutsänkor.
    huvud["kontrakt"] = KONTRAKT_K2
    for namn in ("sankgraf.json", "sankgraf_topologi.json"):
        d = json.loads((tmp_path / "k2" / namn).read_text(encoding="utf-8"))
        d["kontrakt"] = KONTRAKT_K2
        (tmp_path / "k2" / namn).write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(SankGrafFel, match="K2/v1"):
        las_sankgraf_k2(tmp_path / "k2")


def test_k6_skriver_flaggraster_och_varning(tmp_path: Path) -> None:
    graf, _ = _bygg()
    res = statisk_fyllning(graf, 0.10)
    filer = skriv_statiskt_resultat_k6(res, graf.geo, tmp_path, kodversion="t")
    flagga, _ = las_geotiff(filer["slutsanka"])
    assert int(np.sum(flagga == 1.0)) == 36 and int(np.sum(np.isfinite(flagga))) == 36
    run = json.loads(filer["run"].read_text(encoding="utf-8"))
    assert SLUTSANKA_VARNING in run["varningar"]
    assert run["matvarden"]["antal_slutsankor"] == 1
    # Utan slutsänkor: inget flaggraster, inga varningar (som före K2/v2).
    oppen, _ = _bygg(oppning=True)
    filer2 = skriv_statiskt_resultat_k6(statisk_fyllning(oppen, 0.1), oppen.geo, tmp_path / "b")
    assert set(filer2) == {"max_djup", "run"}


def test_dynamik_utan_avtappning_stannar_vattnet_och_med_avtappning_toms() -> None:
    graf, _ = _bygg()
    s = graf.slutsankor()[0]
    # 50 mm/h i 1 h: 1,8 m³ på gården. Ingen avtappning: allt ligger kvar efter regnet.
    d = kor_dynamik_handelsestyrd(graf, [0.0, 3600.0], [50.0, 50.0], slut_tid_s=7200.0)
    assert d.noder[s.nod_id].volym_m3[-1] == pytest.approx(1.8, rel=1e-12)
    assert d.volymfel_rel < 1e-12 and d.avtappat_m3 == 0.0
    assert d.slutsankor == (s.nod_id,) and d.noder[s.nod_id].t_overfall_s is None
    # Avtappning 20 mm/h: netto 30 mm/h under regnet, sedan -20 mm/h tills det är tomt.
    tider = [0.0, 1800.0, 3600.0, 5400.0, 7200.0, 9000.0, 10800.0]
    d2 = kor_dynamik_handelsestyrd(
        graf, [0.0, 3600.0], [50.0, 50.0], slut_tid_s=10800.0,
        utdata_tider_s=tider, avtappning_mm_h=20.0,
    )
    facit = [0.0, 0.54, 1.08, 0.72, 0.36, 0.0, 0.0]  # 36 m² × (30 t − 20 (t − 1 h)) mm
    for v, f in zip(d2.noder[s.nod_id].volym_m3, facit, strict=True):
        assert v == pytest.approx(f, abs=1e-12)
    assert d2.avtappat_m3 == pytest.approx(1.8, rel=1e-12)
    assert d2.volymfel_rel < 1e-12
    k = kor_handelsestyrd_kardinal(
        graf, [0.0, 3600.0], [50.0, 50.0], slut_tid_s=10800.0, avtappning_mm_h=20.0,
    )
    assert k.nod_max_volym_m3[s.nod_id] == pytest.approx(1.08, rel=1e-12)


def test_lokal_omrakning_hus_som_stanger_in_garden_ger_samma_som_full() -> None:
    z_oppen, slag_oppen, geo = _varld(oppning=True)
    osak = np.full((N, N), 0.05, dtype=np.float32)
    bas = bygg_sankgraf(z_oppen, osak, slag_oppen, geo)
    z_stangd, slag_stangd, _ = _varld(oppning=False)
    andrad = (z_stangd != z_oppen) | (slag_stangd != slag_oppen)
    lok = uppdatera_sankgraf_lokalt(bas, z_stangd, osak, slag_stangd, andrad)
    full = bygg_sankgraf(z_stangd, osak, slag_stangd, geo)
    assert lok.ny_graf.noder == full.noder
    np.testing.assert_array_equal(lok.ny_graf.cell_till_nod, full.cell_till_nod)
    assert lok.ny_graf.slutsankor()[0].hinder_rh2000 == full.slutsankor()[0].hinder_rh2000
    # Och tillbaka: huset rivs, gården öppnas igen.
    andrad2 = andrad.copy()
    lok2 = uppdatera_sankgraf_lokalt(full, z_oppen, osak, slag_oppen, andrad2)
    assert not lok2.ny_graf.har_slutsankor
    for regn in (0.05, 0.5):
        a = statisk_fyllning(lok2.ny_graf, regn).djup_m
        b = statisk_fyllning(bas, regn).djup_m
        assert float(np.max(np.abs(a - b))) == 0.0


def test_lokal_omrakning_langt_fran_garden_bevarar_slutsankan() -> None:
    n = 40
    jj = np.arange(n, dtype=np.float32)[None, :].repeat(n, axis=0)
    z = (10.0 - 0.01 * jj).astype(np.float32)
    slag = np.zeros((n, n), dtype=np.uint8)
    slag[2:10, 2:10] = MARKSLAG_BYGGNAD
    slag[3:9, 3:9] = 0
    z[slag == MARKSLAG_BYGGNAD] += np.float32(10.0)
    z[30:34, 30:34] -= np.float32(0.4)  # en vanlig sänka långt från gården
    osak = np.full((n, n), 0.01, dtype=np.float32)
    geo = _geo(n)
    bas = bygg_sankgraf(z, osak, slag, geo)
    assert len(bas.slutsankor()) == 1
    z2 = z.copy()
    z2[31:33, 31:33] -= np.float32(0.2)  # fördjupa sänkan
    andrad = z2 != z
    lok = uppdatera_sankgraf_lokalt(bas, z2, osak, slag, andrad)
    full = bygg_sankgraf(z2, osak, slag, geo)
    assert lok.antal_ateranvanda_noder >= 1
    slut_id = bas.slutsankor()[0].nod_id
    assert lok.ny_graf.noder[slut_id] == bas.noder[slut_id]  # bevarad som den var
    for regn in (0.05, 0.3):
        a = statisk_fyllning(lok.ny_graf, regn)
        b = statisk_fyllning(full, regn)
        assert float(np.max(np.abs(a.djup_m - b.djup_m))) < 1e-6
        assert a.slutsankor[0].volym_m3 == pytest.approx(b.slutsankor[0].volym_m3, rel=1e-12)
