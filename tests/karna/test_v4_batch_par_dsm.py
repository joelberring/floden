"""Regressionstester för Våg 4: K-B9 (K1 v2 DSM + NaN) och D-B8 (`max_arbetare`)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest
import rasterio

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, las_manifest
from gemensamt.objektlagring import LokalLagring, geografi_nyckel
from karna.batch import (
    BatchFel,
    BatchKonfig,
    RutUtdata,
    kor_batch_rutor,
    skapa_k1_rutkorare,
)
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.rutnat import Ruta
from testsvit.k1_io import las_geotiff, skriv_geotiff


def _skapa_ruta(e_min: float, n_min: float, storlek_m: float = 100.0, dx: float = 2.0) -> Ruta:
    return Ruta(
        e_min_m=e_min,
        n_min_m=n_min,
        storlek_m=storlek_m,
        overlapp_m=0.0,
        upplosning_m=dx,
    )


def _skriv_k1_v2_ruta(
    k1_rot: Path,
    ruta: Ruta,
    *,
    terrang_z: np.ndarray,
    mark_z: np.ndarray | None = None,
    byggnader: tuple[ByggnadsPost, ...] = (),
) -> Path:
    """Skriver en komplett K1 v2-rutkatalog (`terrang.tif`, `byggnader.gpkg` + K1 v1-filer)."""
    e_min, n_min, e_max, n_max = ruta.yttre_bbox
    dx = ruta.upplosning_m
    mapp = k1_rot / ruta.namn
    mapp.mkdir(parents=True, exist_ok=True)

    terr32 = np.asarray(terrang_z, dtype=np.float32)
    mark32 = terr32.copy() if mark_z is None else np.asarray(mark_z, dtype=np.float32)
    osak = np.full(terr32.shape, 0.05, dtype=np.float32)
    slag = np.full(terr32.shape, 4, dtype=np.uint8)

    skriv_geotiff(mapp / "terrang.tif", terr32, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "mark.tif", mark32, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "markslag.tif", slag, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_byggnader_gpkg(mapp / "byggnader.gpkg", byggnader, (e_min, n_min, e_max, n_max))
    return mapp


def _hus_20m(e0: float, n0: float, bredd: float = 20.0, mark_z: float = 10.0) -> ByggnadsPost:
    poly = (
        (e0, n0),
        (e0 + bredd, n0),
        (e0 + bredd, n0 + bredd),
        (e0, n0 + bredd),
    )
    return ByggnadsPost(
        byggnad_id="b_hus_20m",
        polygon=poly,
        takhojd_rh2000=mark_z + 20.0,
        markhojd_rh2000=mark_z,
        hojd_m=20.0,
        kalla="test",
        hojdkalla="laser_klass6",
        osakerhet_hojd_m=0.15,
    )


def test_k_b9_batch_dagsljus_anvander_k1_v2_dsm_och_ger_skugga_fran_byggnad(tmp_path: Path) -> None:
    """K-B9: K1 v2 med platt `terrang.tif` + 20 m byggnad i `byggnader.gpkg` ger SVF < 1.0.

    `mark.tif` är avsiktligt helt platt (10 m utan byggnadshöjd) så att en regression som
    återgår till `k1.mark_z` skulle ge falskt SVF == 1.0 överallt.
    """
    ruta = _skapa_ruta(150000.0, 6580000.0, storlek_m=100.0, dx=2.0)
    platt = np.full(ruta.form_rc, 10.0, dtype=np.float32)
    hus = _hus_20m(150040.0, 6580040.0, bredd=20.0, mark_z=10.0)

    k1_rot = tmp_path / "k1"
    _skriv_k1_v2_ruta(k1_rot, ruta, terrang_z=platt, mark_z=platt, byggnader=(hus,))

    utdata = tmp_path / "ut"
    konfig = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=utdata,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        rutstorlek_m=100.0,
        overlapp_m=0.0,
        upplosning_m=2.0,
    )
    korare = skapa_k1_rutkorare(k1_rot, azimutriktningar=36, dagsljus_radie_m=100.0)
    status = kor_batch_rutor([ruta], konfig, rutkorare=korare, avbryt_vid_fel=True)

    assert status.klara == (ruta.namn,)
    assert status.felade == {}

    svf_fil = utdata / ruta.namn / "dagsljus" / "svf.tif"
    assert svf_fil.is_file()
    svf, geo = las_geotiff(svf_fil)
    assert svf.shape == ruta.form_rc
    assert geo.epsg == 3011
    assert np.all(np.isfinite(svf))

    # Celler direkt intill den 20 m höga byggnaden (rader 20..30, kol 20..30) måste skuggas kraftigt
    svf_intill = float(svf[25, 19])
    assert svf_intill < 0.75, f"Förväntade kraftigt skymd himmel intill 20 m hus, fick {svf_intill}"
    assert float(svf.min()) < 0.75
    assert float(svf.mean()) < 0.98

    manifest = las_manifest(utdata / ruta.namn / "run.json")
    assert manifest.matvarden["dagsljus_k1_version"] == 2
    assert manifest.matvarden["dagsljus_dsm_kalla"] == "k1_v2_ytmodell"
    assert manifest.matvarden["dagsljus_svf_min"] < 0.75
    assert manifest.matvarden["dagsljus_svf_medel"] < 0.98
    assert manifest.matvarden["dagsljus_nan_celler"] == 0
    assert manifest.varningar == []


def test_k_b9_batch_dagsljus_k1_v1_bakatkompatibel_och_trasig_v2_ger_fel(tmp_path: Path) -> None:
    """K1 v1 (byggnad inbränd i `mark.tif`) fungerar fortfarande; trasig K1 v2 ger `BatchFel`."""
    ruta_v1 = _skapa_ruta(150000.0, 6580000.0, storlek_m=100.0, dx=5.0)
    k1_rot = tmp_path / "k1"
    mapp_v1 = k1_rot / ruta_v1.namn
    mapp_v1.mkdir(parents=True, exist_ok=True)
    mark_v1 = np.full(ruta_v1.form_rc, 10.0, dtype=np.float32)
    mark_v1[8:12, 8:12] = 30.0
    e0, _, _, n1 = ruta_v1.yttre_bbox
    skriv_geotiff(mapp_v1 / "mark.tif", mark_v1, x_min=e0, y_max=n1, pixelstorlek=5.0)

    ut_v1 = tmp_path / "ut_v1"
    konfig_v1 = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=ut_v1,
        enhet="cpu",
        rutstorlek_m=100.0,
        overlapp_m=0.0,
        upplosning_m=5.0,
    )
    korare = skapa_k1_rutkorare(k1_rot, azimutriktningar=24, dagsljus_radie_m=100.0)
    status_v1 = kor_batch_rutor([ruta_v1], konfig_v1, rutkorare=korare, avbryt_vid_fel=True)
    assert status_v1.klara == (ruta_v1.namn,)
    m_v1 = las_manifest(ut_v1 / ruta_v1.namn / "run.json")
    assert m_v1.matvarden["dagsljus_k1_version"] == 1
    assert m_v1.matvarden["dagsljus_dsm_kalla"] == "k1_v1_mark"
    assert m_v1.matvarden["dagsljus_svf_min"] < 0.85

    # Trasig K1 v2: byggnader.gpkg utan terrang.tif -> tydligt BatchFel (ingen tyst fallback)
    ruta_trasig = _skapa_ruta(150100.0, 6580000.0, storlek_m=100.0, dx=5.0)
    mapp_tr = k1_rot / ruta_trasig.namn
    mapp_tr.mkdir(parents=True, exist_ok=True)
    skriv_byggnader_gpkg(mapp_tr / "byggnader.gpkg", (), ruta_trasig.yttre_bbox)
    with pytest.raises(BatchFel, match="terrang.tif"):
        kor_batch_rutor([ruta_trasig], konfig_v1, rutkorare=korare, avbryt_vid_fel=True)


def test_k_b9_nan_10_procent_bevaras_i_utdata_med_varning_utan_falsk_skugga(tmp_path: Path) -> None:
    """K-B9 NaN: 10 % `NaN`-celler ger giltig SVF, bevarar `NaN`, varnar och skuggar ej falskt."""
    ruta = _skapa_ruta(150000.0, 6580000.0, storlek_m=100.0, dx=5.0)  # 20×20 = 400 celler
    terrang = np.full(ruta.form_rc, 10.0, dtype=np.float32)
    nan_mask = np.zeros(ruta.form_rc, dtype=bool)
    nan_mask[0:2, :] = True  # 40 av 400 celler = exakt 10 %
    terrang[nan_mask] = np.nan

    hus = _hus_20m(150040.0, 6580030.0, bredd=20.0, mark_z=10.0)
    k1_rot = tmp_path / "k1"
    _skriv_k1_v2_ruta(k1_rot, ruta, terrang_z=terrang, byggnader=(hus,))

    utdata = tmp_path / "ut_nan10"
    konfig = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=utdata,
        enhet="cpu",
        rutstorlek_m=100.0,
        overlapp_m=0.0,
        upplosning_m=5.0,
    )
    korare = skapa_k1_rutkorare(k1_rot, azimutriktningar=36, dagsljus_radie_m=100.0)
    status = kor_batch_rutor([ruta], konfig, rutkorare=korare, avbryt_vid_fel=True)
    assert status.klara == (ruta.namn,)
    assert status.felade == {}

    svf_fil = utdata / ruta.namn / "dagsljus" / "svf.tif"
    svf, _geo = las_geotiff(svf_fil)
    assert svf.shape == ruta.form_rc
    assert np.all(np.isnan(svf[nan_mask]))
    assert np.all(np.isfinite(svf[~nan_mask]))
    assert float(np.min(svf[~nan_mask])) < 0.85
    assert float(np.max(svf[~nan_mask])) <= 1.0

    with rasterio.open(svf_fil) as ds:
        assert ds.nodata is not None and np.isnan(ds.nodata)

    manifest = las_manifest(utdata / ruta.namn / "run.json")
    assert manifest.matvarden["dagsljus_nan_celler"] == 40
    assert manifest.matvarden["dagsljus_nan_andel"] == pytest.approx(0.1)
    assert manifest.matvarden["dagsljus_svf_medel"] == pytest.approx(
        float(np.mean(svf[~nan_mask])), rel=1e-5
    )
    assert manifest.matvarden["dagsljus_svf_min"] == pytest.approx(
        float(np.min(svf[~nan_mask])), rel=1e-5
    )
    assert len(manifest.varningar) == 1
    varning = manifest.varningar[0]
    assert "40" in varning and "400" in varning and "10.0 %" in varning and "NaN" in varning

    # Verifiera fysik: utan byggnad ger helt platt terräng med 10 % NaN exakt SVF == 1.0 på
    # giltiga celler (maskerade NaN-celler kastar alltså inga falska skuggor).
    ruta_platt = _skapa_ruta(150200.0, 6580000.0, storlek_m=100.0, dx=5.0)
    _skriv_k1_v2_ruta(k1_rot, ruta_platt, terrang_z=terrang, byggnader=())
    status_platt = kor_batch_rutor([ruta_platt], konfig, rutkorare=korare, avbryt_vid_fel=True)
    assert status_platt.klara == (ruta_platt.namn,)
    svf_platt, _ = las_geotiff(utdata / ruta_platt.namn / "dagsljus" / "svf.tif")
    assert np.all(np.isnan(svf_platt[nan_mask]))
    assert np.allclose(svf_platt[~nan_mask], 1.0, atol=1e-6)


def test_k_b9_nan_100_procent_ger_tydligt_fel(tmp_path: Path) -> None:
    """K-B9 NaN: 100 % `NaN`-celler kastar tydligt `BatchFel` och skriver ingen trasig ruta."""
    ruta = _skapa_ruta(150000.0, 6580000.0, storlek_m=100.0, dx=5.0)
    allt_nan = np.full(ruta.form_rc, np.nan, dtype=np.float32)

    k1_rot = tmp_path / "k1"
    _skriv_k1_v2_ruta(k1_rot, ruta, terrang_z=allt_nan, byggnader=())

    utdata = tmp_path / "ut_nan100"
    konfig = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=utdata,
        enhet="cpu",
        rutstorlek_m=100.0,
        overlapp_m=0.0,
        upplosning_m=5.0,
    )
    korare = skapa_k1_rutkorare(k1_rot, azimutriktningar=18, dagsljus_radie_m=100.0)

    with pytest.raises(BatchFel, match=r"100 %.*NaN"):
        kor_batch_rutor([ruta], konfig, rutkorare=korare, avbryt_vid_fel=True)

    status = kor_batch_rutor([ruta], konfig, rutkorare=korare, avbryt_vid_fel=False)
    assert status.klara == ()
    assert ruta.namn in status.felade
    assert "BatchFel" in status.felade[ruta.namn]
    assert "100 %" in status.felade[ruta.namn]
    assert not (utdata / ruta.namn).exists()
    assert not list(utdata.glob(".tmp_*"))


def test_d_b8_parallell_exekvering_deterministisk_ordning_och_felhantering(tmp_path: Path) -> None:
    """D-B8: `max_arbetare=3` kör samtidigt, matchar `max_arbetare=1` och isolerar rutfel."""
    rutor = [
        _skapa_ruta(150000.0 + i * 500.0, 6580000.0, storlek_m=500.0, dx=5.0)
        for i in range(6)
    ]
    forvantad_ordning = tuple(r.namn for r in rutor)

    # 1. Sekventiell referenskörning (max_arbetare=1): noll extra trådar
    huvudtrad = threading.get_ident()
    sek_tradar: list[int] = []
    ut_sek = tmp_path / "sek"
    konfig_sek = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=ut_sek,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        max_arbetare=1,
    )

    def deterministisk_korare(
        ruta: Ruta, arbetsmapp: Path, _k: BatchKonfig, plats: str
    ) -> RutUtdata:
        sek_tradar.append(threading.get_ident())
        utfil = arbetsmapp / "resultat.json"
        innehall = json.dumps({"ruta": ruta.namn, "e_min": ruta.e_min_m, "plats": plats})
        utfil.write_text(innehall, encoding="utf-8")
        return RutUtdata(
            utdata_filer=(FilPost(roll="resultat", fil="resultat.json", sha256=sha256_fil(utfil)),),
            matvarden={"e_min_m": ruta.e_min_m, "berakningsplats": plats},
            varningar=(f"info:{ruta.namn}",),
        )

    status_sek = kor_batch_rutor(rutor, konfig_sek, rutkorare=deterministisk_korare)
    assert sek_tradar == [huvudtrad] * 6
    assert status_sek.klara == forvantad_ordning
    assert status_sek.hoppade == ()
    assert status_sek.felade == {}
    assert status_sek.aterstaende == 0

    # 2. Parallell körning (max_arbetare=3): bevisa samtidig exekvering via Barrier(2) +
    #    framtvingad omkastad färdigordning (ruta 0 väntar på ruta 2) och verifiera identiska utdata
    ut_par = tmp_path / "par"
    konfig_par = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=ut_par,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        max_arbetare=3,
    )

    barriar = threading.Barrier(2, timeout=5.0)
    ruta2_klar = threading.Event()
    raknar_las = threading.Lock()
    ankomster = 0
    aktiva = 0
    max_aktiva = 0
    fardigordning: list[str] = []

    def parallell_korare(
        ruta: Ruta, arbetsmapp: Path, _k: BatchKonfig, plats: str
    ) -> RutUtdata:
        nonlocal ankomster, aktiva, max_aktiva
        with raknar_las:
            ankomster += 1
            min_ankomst = ankomster
            aktiva += 1
            if aktiva > max_aktiva:
                max_aktiva = aktiva
        try:
            # De två första trådarna måste mötas samtidigt (kraschar med BrokenBarrierError
            # om körningen vore sekventiell).
            if min_ankomst <= 2:
                barriar.wait(timeout=5.0)
            # Tvinga ruta 0 att bli klar EFTER ruta 2 för att stresstesta resultatordningen.
            if ruta.namn == rutor[0].namn:
                assert ruta2_klar.wait(timeout=5.0), "ruta 2 hann inte klart inom timeout"
            utfil = arbetsmapp / "resultat.json"
            innehall = json.dumps({"ruta": ruta.namn, "e_min": ruta.e_min_m, "plats": plats})
            utfil.write_text(innehall, encoding="utf-8")
            with raknar_las:
                fardigordning.append(ruta.namn)
            if ruta.namn == rutor[2].namn:
                ruta2_klar.set()
            return RutUtdata(
                utdata_filer=(
                    FilPost(roll="resultat", fil="resultat.json", sha256=sha256_fil(utfil)),
                ),
                matvarden={"e_min_m": ruta.e_min_m, "berakningsplats": plats},
                varningar=(f"info:{ruta.namn}",),
            )
        finally:
            with raknar_las:
                aktiva -= 1

    status_par = kor_batch_rutor(rutor, konfig_par, rutkorare=parallell_korare)

    # (a) Faktisk samtidig exekvering (och inte över max_arbetare=3)
    assert 2 <= max_aktiva <= 3
    assert fardigordning.index(rutor[2].namn) < fardigordning.index(rutor[0].namn)

    # (b) Exakt identisk status & deterministisk rutordning som max_arbetare=1
    assert status_par.klara == status_sek.klara == forvantad_ordning
    assert status_par.hoppade == status_sek.hoppade == ()
    assert status_par.felade == status_sek.felade == {}
    assert status_par.totalt_rutor == status_sek.totalt_rutor == 6
    assert status_par.aterstaende == status_sek.aterstaende == 0
    assert status_par.berakningsplats == status_sek.berakningsplats

    status_disk = json.loads((ut_par / "batch_status.json").read_text(encoding="utf-8"))
    assert tuple(status_disk["klara"]) == forvantad_ordning
    assert status_disk["aterstaende"] == 0
    assert not list(ut_par.glob(".tmp_*"))
    assert not list(ut_par.glob(".batch_status.*.tmp"))

    for r in rutor:
        assert (ut_par / r.namn / "resultat.json").read_bytes() == (
            ut_sek / r.namn / "resultat.json"
        ).read_bytes()
        m_sek = las_manifest(ut_sek / r.namn / "run.json")
        m_par = las_manifest(ut_par / r.namn / "run.json")
        assert m_par.parametrar == m_sek.parametrar
        assert m_par.utdata == m_sek.utdata
        assert m_par.varningar == m_sek.varningar
        mv_sek = {k: v for k, v in m_sek.matvarden.items() if k != "kortid_s"}
        mv_par = {k: v for k, v in m_par.matvarden.items() if k != "kortid_s"}
        assert mv_par == mv_sek

    # 3. (c) Felhantering: 1 av 6 rutor kastar undantag -> övriga 5 slutförs, felet rapporteras
    ut_fel = tmp_path / "par_fel"
    konfig_fel = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=ut_fel,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        max_arbetare=3,
    )
    felruta = rutor[3].namn

    def korare_med_ett_fel(
        ruta: Ruta, arbetsmapp: Path, _k: BatchKonfig, plats: str
    ) -> RutUtdata:
        utfil = arbetsmapp / "resultat.json"
        utfil.write_text(f"{ruta.namn}\n", encoding="utf-8")
        if ruta.namn == felruta:
            raise ValueError(f"Simulerat fel på {ruta.namn}")
        return RutUtdata(
            utdata_filer=(FilPost(roll="resultat", fil="resultat.json", sha256=sha256_fil(utfil)),),
            matvarden={"berakningsplats": plats},
        )

    status_fel = kor_batch_rutor(
        rutor, konfig_fel, rutkorare=korare_med_ett_fel, avbryt_vid_fel=False
    )
    forvantade_klara = tuple(r.namn for r in rutor if r.namn != felruta)
    assert status_fel.klara == forvantade_klara
    assert status_fel.hoppade == ()
    assert list(status_fel.felade.keys()) == [felruta]
    assert "ValueError: Simulerat fel på" in status_fel.felade[felruta]
    assert status_fel.aterstaende == 0
    assert not (ut_fel / felruta).exists()
    assert not list(ut_fel.glob(".tmp_*"))
    for namn in forvantade_klara:
        assert (ut_fel / namn / "run.json").is_file()

    # 4. Objektlagring + återupptagning med max_arbetare=3 (blandning av hoppade och nyberäknade)
    lagring = LokalLagring(tmp_path / "objektlagring")
    konfig_s3 = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=tmp_path / "status_s3",
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        kommun="goteborg",
        lagring_typ="s3",
        max_arbetare=3,
    )
    s3_pass1 = kor_batch_rutor(
        rutor[:3], konfig_s3, rutkorare=deterministisk_korare, objektlagring=lagring
    )
    assert s3_pass1.klara == forvantad_ordning[:3]
    s3_pass2 = kor_batch_rutor(
        rutor, konfig_s3, rutkorare=deterministisk_korare, objektlagring=lagring
    )
    assert s3_pass2.hoppade == forvantad_ordning[:3]
    assert s3_pass2.klara == forvantad_ordning[3:]
    assert s3_pass2.felade == {}
    for r in rutor:
        assert lagring.finns(geografi_nyckel("goteborg", r.namn, "run.json"))

    # 5. avbryt_vid_fel=True i parallellt läge propagerar undantaget och lämnar inga .tmp_-mappar
    ut_avbryt = tmp_path / "par_avbryt"
    konfig_avbryt = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=ut_avbryt,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        max_arbetare=3,
    )
    with pytest.raises(ValueError, match="Simulerat fel på"):
        kor_batch_rutor(rutor, konfig_avbryt, rutkorare=korare_med_ett_fel, avbryt_vid_fel=True)
    assert not list(ut_avbryt.glob(".tmp_*"))
    assert not (ut_avbryt / felruta).exists()


def test_k_b9_och_d_b8_tillsammans_parallell_k1_v2_dagsljus_identisk_med_sekventiell(
    tmp_path: Path,
) -> None:
    """Riktig `skapa_k1_rutkorare` på K1 v2-rutor ger bitidentisk SVF vid `max_arbetare=3` vs 1."""
    rutor = [
        _skapa_ruta(150000.0 + i * 100.0, 6580000.0, storlek_m=100.0, dx=5.0)
        for i in range(4)
    ]
    k1_rot = tmp_path / "k1_par"
    for i, ruta in enumerate(rutor):
        e0, n0, _, _ = ruta.yttre_bbox
        terr = np.full(ruta.form_rc, 10.0 + i, dtype=np.float32)
        if i == 1:
            terr[0:2, :] = np.nan  # 10 % NaN på en av rutorna
        hus = _hus_20m(e0 + 40.0, n0 + 35.0, bredd=20.0, mark_z=10.0 + i)
        _skriv_k1_v2_ruta(k1_rot, ruta, terrang_z=terr, mark_z=terr, byggnader=(hus,))

    korare = skapa_k1_rutkorare(k1_rot, azimutriktningar=24, dagsljus_radie_m=100.0)
    ut_sek = tmp_path / "k1_ut_sek"
    ut_par = tmp_path / "k1_ut_par"
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
            max_arbetare=3,
        ),
        rutkorare=korare,
        avbryt_vid_fel=True,
    )

    assert st_par.klara == st_sek.klara == tuple(r.namn for r in rutor)
    assert st_par.felade == {}
    for r in rutor:
        svf_sek, _ = las_geotiff(ut_sek / r.namn / "dagsljus" / "svf.tif")
        svf_par, _ = las_geotiff(ut_par / r.namn / "dagsljus" / "svf.tif")
        np.testing.assert_array_equal(svf_par, svf_sek)
        m_sek = las_manifest(ut_sek / r.namn / "run.json")
        m_par = las_manifest(ut_par / r.namn / "run.json")
        assert m_par.varningar == m_sek.varningar
        assert m_par.matvarden["dagsljus_svf_medel"] == m_sek.matvarden["dagsljus_svf_medel"]
        assert m_par.matvarden["dagsljus_svf_min"] == m_sek.matvarden["dagsljus_svf_min"]
        assert m_par.matvarden["dagsljus_nan_celler"] == m_sek.matvarden["dagsljus_nan_celler"]
