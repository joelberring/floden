"""Tester för återupptagbar rut-kö och dataskyddsspärr (`karna.batch` + `floden forberakna`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from floden.cli import main as floden_main
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, las_manifest
from karna.batch import (
    BatchFel,
    BatchKonfig,
    RutUtdata,
    generera_rutor_for_omrade,
    kontrollera_dataskydd,
    kor_batch_rutor,
)
from markmodell.rutnat import Ruta


def _skriv_2x2_omrade_geojson(sokvag: Path) -> Path:
    """Skriver en 1000×1000 m polygon i EPSG:3011 som täcker exakt 2×2 standardrutor (500 m)."""
    poly = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"namn": "test_2x2"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [150010.0, 6580010.0],
                            [150990.0, 6580010.0],
                            [150990.0, 6580990.0],
                            [150010.0, 6580990.0],
                            [150010.0, 6580010.0],
                        ]
                    ],
                },
            }
        ],
    }
    sokvag.write_text(json.dumps(poly), encoding="utf-8")
    return sokvag


def test_generera_rutor_2x2_och_aterupptagning_efter_avbrott(tmp_path: Path) -> None:
    omrade = _skriv_2x2_omrade_geojson(tmp_path / "omrade.geojson")
    rutor = generera_rutor_for_omrade(omrade, rutstorlek_m=500.0, overlapp_m=25.0, upplosning_m=1.0)
    assert len(rutor) == 4
    assert [r.namn for r in rutor] == [
        "mm_150000_6580000",
        "mm_150000_6580500",
        "mm_150500_6580000",
        "mm_150500_6580500",
    ]

    utdata = tmp_path / "cache"
    konfig = BatchKonfig(
        moduler=("vatten2d", "dagsljus"),
        utdata_rot=utdata,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        indata_filer=(omrade,),
    )

    anrop_pass1: list[str] = []

    def korare_med_avbrott(
        ruta: Ruta, arbetsmapp: Path, _konfig: BatchKonfig, plats: str
    ) -> RutUtdata:
        anrop_pass1.append(ruta.namn)
        # Skriv en halvskriven fil först för att verifiera att tempkatalogen städas vid fel
        utfil = arbetsmapp / "resultat.txt"
        utfil.write_text(f"{ruta.namn}:{plats}\n", encoding="utf-8")
        if len(anrop_pass1) == 3:
            raise RuntimeError("Simulerat avbrott på ruta 3")
        return RutUtdata(
            utdata_filer=(FilPost(roll="resultat", fil="resultat.txt", sha256=sha256_fil(utfil)),),
            matvarden={"berakningsplats": plats},
        )

    # Pass 1: avbryts på ruta 3
    with pytest.raises(RuntimeError, match="Simulerat avbrott"):
        kor_batch_rutor(rutor, konfig, rutkorare=korare_med_avbrott, avbryt_vid_fel=True)

    # Ruta 1 och 2 ska vara kompletta, ruta 3 får INTE ha halvskriven katalog eller tmp kvar
    assert (utdata / rutor[0].namn / "run.json").is_file()
    assert (utdata / rutor[1].namn / "run.json").is_file()
    assert not (utdata / rutor[2].namn).exists()
    assert not list(utdata.glob(".tmp_*"))

    # Pass 2: återuppta – ruta 1 och 2 ska hoppas över (0 omberäkning), ruta 3 och 4 beräknas
    anrop_pass2: list[str] = []

    def korare_ok(ruta: Ruta, arbetsmapp: Path, _konfig: BatchKonfig, plats: str) -> RutUtdata:
        anrop_pass2.append(ruta.namn)
        utfil = arbetsmapp / "resultat.txt"
        utfil.write_text(f"{ruta.namn}:{plats}\n", encoding="utf-8")
        return RutUtdata(
            utdata_filer=(FilPost(roll="resultat", fil="resultat.txt", sha256=sha256_fil(utfil)),),
            matvarden={"berakningsplats": plats},
        )

    status = kor_batch_rutor(rutor, konfig, rutkorare=korare_ok)
    assert len(status.hoppade) == 2
    assert len(status.klara) == 2
    assert len(status.felade) == 0
    assert anrop_pass2 == [rutor[2].namn, rutor[3].namn]

    # Kontrollera K0-manifest och batch_status.json
    for r in rutor:
        m = las_manifest(utdata / r.namn / "run.json")
        assert m.kontrakt == "K0/v1"
        assert m.parametrar["berakningsplats"] == "cpu_f64"

    status_json = json.loads((utdata / "batch_status.json").read_text(encoding="utf-8"))
    assert status_json["totalt_rutor"] == 4
    assert status_json["aterstaende"] == 0
    assert status_json["berakningsplats"] == "cpu_f64"


def test_dataskyddssparr_vagrar_icke_oppen_data_i_colab() -> None:
    colab_miljo = {"COLAB_RELEASE_TAG": "release-colab-20261125"}
    # Öppen data tillåts
    kontrollera_dataskydd("oppen", miljo=colab_miljo)
    # Intern och skyddad data vägras
    with pytest.raises(BatchFel, match="Dataskyddsspärr"):
        kontrollera_dataskydd("skyddad", miljo=colab_miljo)
    with pytest.raises(BatchFel, match="Dataskyddsspärr"):
        kontrollera_dataskydd("intern", miljo=colab_miljo)

    # Lokalt (utan molnvariabel) tillåts alla klasser
    kontrollera_dataskydd("skyddad", miljo={})


def test_cli_forberakna_torrkor_och_kor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    omrade = _skriv_2x2_omrade_geojson(tmp_path / "omrade.geojson")
    utdata = tmp_path / "ut"

    # Torrkörning
    rc = floden_main(
        ["forberakna", "--omrade", str(omrade), "--utdata", str(utdata), "--torrkor"]
    )
    assert rc == 0
    assert not utdata.exists()

    # Riktig körning (CPU)
    rc2 = floden_main(
        [
            "forberakna",
            "--omrade",
            str(omrade),
            "--moduler",
            "vatten2d,dagsljus",
            "--enhet",
            "cpu",
            "--utdata",
            str(utdata),
            "--dataklass",
            "oppen",
        ]
    )
    assert rc2 == 0
    assert (utdata / "batch_status.json").is_file()

    # Dataskyddsspärr via CLI när COLAB_RELEASE_TAG är satt
    monkeypatch.setenv("COLAB_RELEASE_TAG", "release-colab-test")
    rc3 = floden_main(
        [
            "forberakna",
            "--omrade",
            str(omrade),
            "--utdata",
            str(utdata),
            "--dataklass",
            "skyddad",
        ]
    )
    assert rc3 == 2


def test_k1_rutkorare_vatten2d_och_dagsljus_end_to_end(tmp_path: Path) -> None:
    """Verifierar full K1 -> vatten2d + dagsljus genom `floden forberakna --k1-rot`."""
    import numpy as np

    from testsvit.k1_io import skriv_geotiff

    # 1×1-område (100×100 m) -> exakt en ruta mm_150000_6580000
    omrade_fil = tmp_path / "liten.geojson"
    omrade_fil.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [150010.0, 6580010.0],
                        [150090.0, 6580010.0],
                        [150090.0, 6580090.0],
                        [150010.0, 6580090.0],
                        [150010.0, 6580010.0],
                    ]
                ],
            }
        ),
        encoding="utf-8",
    )

    k1_rot = tmp_path / "k1_rutor"
    ruta_mapp = k1_rot / "mm_150000_6580000"
    yy, xx = np.mgrid[0:20, 0:20]
    mark_z = (10.0 + 0.02 * ((yy - 9.5) ** 2 + (xx - 9.5) ** 2)).astype(np.float32)
    mark_z[2:5, 2:5] = 20.0  # byggnad för dagsljus-SVF < 1
    osak = np.full((20, 20), 0.05, dtype=np.float32)
    slag = np.full((20, 20), 4, dtype=np.uint8)
    slag[2:5, 2:5] = 9
    skriv_geotiff(ruta_mapp / "mark.tif", mark_z, x_min=150000.0, y_max=6580100.0, pixelstorlek=5.0)
    skriv_geotiff(
        ruta_mapp / "osakerhet.tif", osak, x_min=150000.0, y_max=6580100.0, pixelstorlek=5.0
    )
    skriv_geotiff(
        ruta_mapp / "markslag.tif", slag, x_min=150000.0, y_max=6580100.0, pixelstorlek=5.0
    )

    utdata = tmp_path / "batch_ut"
    rc = floden_main(
        [
            "forberakna",
            "--omrade",
            str(omrade_fil),
            "--k1-rot",
            str(k1_rot),
            "--moduler",
            "vatten2d,dagsljus",
            "--enhet",
            "cpu",
            "--rutstorlek-m",
            "100",
            "--overlapp-m",
            "0",
            "--upplosning-m",
            "5",
            "--utdata",
            str(utdata),
        ]
    )
    assert rc == 0
    rut_ut = utdata / "mm_150000_6580000"
    assert (rut_ut / "vatten2d" / "max_djup.tif").is_file()
    assert (rut_ut / "dagsljus" / "svf.tif").is_file()
    manifest = las_manifest(rut_ut / "run.json")
    assert manifest.matvarden["vatten2d_max_djup_m"] > 0.0
    assert manifest.matvarden["vatten2d_volymfel_rel"] < 1e-3
    assert 0.5 < manifest.matvarden["dagsljus_svf_medel"] < 1.0

    # Andra körningen ska hoppa över rutan (100 % cache-träff)
    rc2 = floden_main(
        [
            "forberakna",
            "--omrade",
            str(omrade_fil),
            "--k1-rot",
            str(k1_rot),
            "--moduler",
            "vatten2d,dagsljus",
            "--enhet",
            "cpu",
            "--rutstorlek-m",
            "100",
            "--overlapp-m",
            "0",
            "--upplosning-m",
            "5",
            "--utdata",
            str(utdata),
        ]
    )
    assert rc2 == 0
    status_json = json.loads((utdata / "batch_status.json").read_text(encoding="utf-8"))
    assert status_json["hoppade"] == ["mm_150000_6580000"]
    assert status_json["klara"] == []


def test_kor_batch_rutor_med_objektlagring_och_omedelbar_rensning(tmp_path: Path) -> None:
    """Verifierar lager 1: temp-katalog -> verifierad S3/objektuppladdning -> direkt rensning."""
    from gemensamt.objektlagring import LokalLagring, geografi_nyckel

    omrade = _skriv_2x2_omrade_geojson(tmp_path / "omrade.geojson")
    rutor = generera_rutor_for_omrade(omrade, rutstorlek_m=500.0, overlapp_m=25.0, upplosning_m=1.0)
    lagring = LokalLagring(tmp_path / "objektlagring")
    utdata = tmp_path / "status_rot"

    konfig = BatchKonfig(
        moduler=("vatten2d",),
        utdata_rot=utdata,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        kommun="goteborg",
        lagring_typ="s3",
        behall_radata=False,
        indata_filer=(omrade,),
    )

    skapade_tmpmappar: list[Path] = []
    anrop: list[str] = []

    def korare(ruta: Ruta, arbetsmapp: Path, _k: BatchKonfig, plats: str) -> RutUtdata:
        skapade_tmpmappar.append(arbetsmapp)
        anrop.append(ruta.namn)
        # Simulera temporär rå-LAZ + färdig COG-fil
        (arbetsmapp / "radata.laz").write_bytes(b"RAW-LAZ-DATA")
        utfil = arbetsmapp / "mark.tif"
        utfil.write_bytes(b"COMPRESSED-COG")
        return RutUtdata(
            utdata_filer=(FilPost(roll="mark", fil="mark.tif", sha256=sha256_fil(utfil)),),
            matvarden={"berakningsplats": plats},
        )

    status1 = kor_batch_rutor(rutor, konfig, rutkorare=korare, objektlagring=lagring)
    assert len(status1.klara) == 4
    assert len(status1.hoppade) == 0
    assert len(status1.felade) == 0

    # Alla färdiga filer + run.json finns i objektlagringen; rå-LAZ laddas inte upp
    for r in rutor:
        assert lagring.finns(geografi_nyckel("goteborg", r.namn, "run.json"))
        assert lagring.finns(geografi_nyckel("goteborg", r.namn, "mark.tif"))
        assert not lagring.finns(geografi_nyckel("goteborg", r.namn, "radata.laz"))

    # Alla temporära rutkataloger ska vara raderade direkt efter klar ruta
    assert len(skapade_tmpmappar) == 4
    assert all(not m.exists() for m in skapade_tmpmappar)

    # Andra körningen ska se run.json i objektlagringen och hoppa över alla 4 rutor
    anrop.clear()
    status2 = kor_batch_rutor(rutor, konfig, rutkorare=korare, objektlagring=lagring)
    assert len(status2.hoppade) == 4
    assert len(status2.klara) == 0
    assert anrop == []


def test_s3_aterstart_verifierar_batch_nyckel_vid_andrad_upplosning_och_moduler(
    tmp_path: Path,
) -> None:
    """S3-återstart hoppar över matchande `batch_nyckel` men kör om vid ny upplösning/moduler."""
    from gemensamt.objektlagring import LokalLagring, geografi_nyckel

    omrade = _skriv_2x2_omrade_geojson(tmp_path / "omrade.geojson")
    rutor_25m = generera_rutor_for_omrade(
        omrade, rutstorlek_m=500.0, overlapp_m=25.0, upplosning_m=2.5
    )
    lagring = LokalLagring(tmp_path / "s3_bucket")
    utdata = tmp_path / "status_rot"

    anrop: list[tuple[str, float, tuple[str, ...]]] = []

    def korare(ruta: Ruta, arbetsmapp: Path, konfig: BatchKonfig, plats: str) -> RutUtdata:
        anrop.append((ruta.namn, ruta.upplosning_m, konfig.moduler))
        utfil = arbetsmapp / "mark.tif"
        utfil.write_bytes(
            f"{ruta.namn}:{ruta.upplosning_m}:{','.join(konfig.moduler)}:{plats}".encode()
        )
        return RutUtdata(
            utdata_filer=(FilPost(roll="mark", fil="mark.tif", sha256=sha256_fil(utfil)),),
            matvarden={"upplosning_m": ruta.upplosning_m},
        )

    konfig_v1 = BatchKonfig(
        moduler=("vatten2d",),
        utdata_rot=utdata,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        upplosning_m=2.5,
        kommun="goteborg",
        lagring_typ="s3",
        indata_filer=(omrade,),
    )

    # Pass 1: initial körning med vatten2d @ 2.5 m
    s1 = kor_batch_rutor(rutor_25m, konfig_v1, rutkorare=korare, objektlagring=lagring)
    assert len(s1.klara) == 4
    assert len(s1.hoppade) == 0
    assert len(anrop) == 4

    # Pass 2: oförändrad konfig -> 100 % återanvändning (0 omberäkningar)
    anrop.clear()
    s2 = kor_batch_rutor(rutor_25m, konfig_v1, rutkorare=korare, objektlagring=lagring)
    assert len(s2.hoppade) == 4
    assert len(s2.klara) == 0
    assert anrop == []

    # Pass 3: ändrad moduluppsättning -> ny batch_nyckel -> alla 4 rutor MÅSTE köras om!
    konfig_fler_moduler = BatchKonfig(
        moduler=("vatten2d", "dagsljus"),
        utdata_rot=utdata,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        upplosning_m=2.5,
        kommun="goteborg",
        lagring_typ="s3",
        indata_filer=(omrade,),
    )
    anrop.clear()
    s3 = kor_batch_rutor(rutor_25m, konfig_fler_moduler, rutkorare=korare, objektlagring=lagring)
    assert len(s3.klara) == 4
    assert len(s3.hoppade) == 0
    assert len(anrop) == 4

    # Pass 4: ändrad upplösning (1.0 m i stället för 2.5 m) -> alla 4 rutor MÅSTE köras om!
    rutor_1m = generera_rutor_for_omrade(
        omrade, rutstorlek_m=500.0, overlapp_m=25.0, upplosning_m=1.0
    )
    konfig_1m = BatchKonfig(
        moduler=("vatten2d", "dagsljus"),
        utdata_rot=utdata,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        upplosning_m=1.0,
        kommun="goteborg",
        lagring_typ="s3",
        indata_filer=(omrade,),
    )
    anrop.clear()
    s4 = kor_batch_rutor(rutor_1m, konfig_1m, rutkorare=korare, objektlagring=lagring)
    assert len(s4.klara) == 4
    assert len(s4.hoppade) == 0
    assert all(uppl == 1.0 for _, uppl, _ in anrop)

    # Pass 5: saknad utdatafil i lagringen trots befintlig run.json -> rutan körs om
    forsta = rutor_1m[0].namn
    lagring.radera_prefix(geografi_nyckel("goteborg", forsta, "mark.tif"))
    assert lagring.finns(geografi_nyckel("goteborg", forsta, "run.json"))
    assert not lagring.finns(geografi_nyckel("goteborg", forsta, "mark.tif"))

    anrop.clear()
    s5 = kor_batch_rutor(rutor_1m, konfig_1m, rutkorare=korare, objektlagring=lagring)
    assert s5.klara == (forsta,)
    assert len(s5.hoppade) == 3
    assert lagring.finns(geografi_nyckel("goteborg", forsta, "mark.tif"))

    # Pass 6: trasigt run.json i lagringen -> rutan körs om i stället för att hoppas över
    trasig = tmp_path / "trasig_run.json"
    trasig.write_text("{ogiltig json", encoding="utf-8")
    lagring.ladda_upp_fil(trasig, geografi_nyckel("goteborg", forsta, "run.json"))
    anrop.clear()
    s6 = kor_batch_rutor(rutor_1m, konfig_1m, rutkorare=korare, objektlagring=lagring)
    assert s6.klara == (forsta,)
    assert len(s6.hoppade) == 3


def test_colab_forberakna_s3_flaggor_och_bakatkompatibilitet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scripts/colab_forberakna.py` stöder `--lagring s3`, `--kommun` och `--upplosning-m`."""
    import importlib.util

    from gemensamt.objektlagring import LokalLagring, geografi_nyckel

    skript = Path(__file__).resolve().parents[2] / "scripts" / "colab_forberakna.py"
    spec = importlib.util.spec_from_file_location("colab_forberakna", skript)
    assert spec is not None and spec.loader is not None
    colab_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(colab_mod)

    omrade = _skriv_2x2_omrade_geojson(tmp_path / "omrade.geojson")
    utdata = tmp_path / "colab_ut"

    # 1. Bakåtkompatibla standardvärden utan nya flaggor
    args_gammal = colab_mod.bygg_parser().parse_args(
        ["--omrade", str(omrade), "--utdata", str(utdata)]
    )
    assert args_gammal.lagring == "lokal"
    assert args_gammal.kommun == "standard"
    assert args_gammal.upplosning_m == 1.0
    assert args_gammal.behall_radata is False

    # 2. Nya S3- och upplösningsflaggor matchar `floden forberakna`
    args_s3 = colab_mod.bygg_parser().parse_args(
        [
            "--omrade",
            str(omrade),
            "--utdata",
            str(utdata),
            "--moduler",
            "vatten2d,dagsljus",
            "--enhet",
            "cpu",
            "--precision",
            "f32",
            "--lagring",
            "s3",
            "--kommun",
            "goteborg",
            "--upplosning-m",
            "2.5",
            "--behall-radata",
        ]
    )
    konfig = colab_mod.skapa_batch_konfig(args_s3)
    assert konfig.lagring_typ == "s3"
    assert konfig.kommun == "goteborg"
    assert konfig.upplosning_m == 2.5
    assert konfig.behall_radata is True
    assert konfig.dataklass == "oppen"
    assert konfig.moduler == ("vatten2d", "dagsljus")
    assert konfig.precision == "f32"

    # 3. End-to-end-körning mot injicerad objektlagring (helt offline)
    lagring = LokalLagring(tmp_path / "colab_s3")
    rc = colab_mod.main(
        [
            "--omrade",
            str(omrade),
            "--utdata",
            str(utdata),
            "--enhet",
            "cpu",
            "--lagring",
            "s3",
            "--kommun",
            "goteborg",
            "--upplosning-m",
            "2.5",
        ],
        objektlagring=lagring,
    )
    assert rc == 0
    run_nyckel = geografi_nyckel("goteborg", "mm_150000_6580000", "run.json")
    info_nyckel = geografi_nyckel("goteborg", "mm_150000_6580000", "ruta_info.json")
    assert lagring.finns(run_nyckel)
    assert lagring.finns(info_nyckel)
    info_lokal = tmp_path / "ruta_info.json"
    lagring.ladda_ner_fil(info_nyckel, info_lokal)
    info = json.loads(info_lokal.read_text(encoding="utf-8"))
    assert info["upplosning_m"] == 2.5

    # 4. `--lagring s3` utan S3-konfig ger tydlig felkod 2 (aldrig tyst fallback till lokal)
    for var in (
        "FLODEN_S3_TYP",
        "FLODEN_S3_ENDPOINT",
        "FLODEN_S3_BUCKET",
        "FLODEN_S3_ACCESS_KEY",
        "FLODEN_S3_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    rc_fel = colab_mod.main(
        [
            "--omrade",
            str(omrade),
            "--utdata",
            str(tmp_path / "ut_fel"),
            "--enhet",
            "cpu",
            "--lagring",
            "s3",
            "--konfig",
            str(tmp_path / "saknas.toml"),
        ]
    )
    assert rc_fel == 2



