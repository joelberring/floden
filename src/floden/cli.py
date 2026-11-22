"""Kommandoradsgränssnitt för Floden (skyfallsmodellen)."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from data_inlasning.lantmateriet import PunktMoln, las_las_punktmoln
from data_inlasning.lasclip import las_laser_punktmoln
from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from dynamik.nodmodell import DynamikFel, DynamikResultat, skriv_dynamik_resultat_k6
from dynamik.risk import berakna_kumulativ_oversvamningsrisk
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, las_manifest, skriv_manifest
from gemensamt.regn import (
    IDF_DAHLSTROM_2010,
    IDF_KALLOR,
    STANDARD_CDS_TOPPFAKTOR,
    STANDARD_TIDSSTEG_MIN,
    STANDARD_VARAKTIGHET_MIN,
    IdfKalla,
    RegnFel,
    RegnSerieK4,
    las_regn_k4_csv,
    lista_regnforval,
    skapa_regn_fran_idf_kalla,
    skapa_regn_fran_spec,
    skriv_regn_k4_csv,
)
from mark_forluster.koppling import ForlustKopplingResultat, berakna_effektivt_regn_k5
from mark_forluster.parametrar import ForlustFel
from markmodell.brytlinjer import Brytlinje, BrytlinjeFel
from markmodell.byggnadskallor import las_byggnadskalla
from markmodell.byggnadsregister import ByggnadsKandidat, ByggnadsregisterFel
from markmodell.dwg import MarkslagsYta, las_baskarta_dwg
from markmodell.grundkarta import GrundkartaFel, interpolera_med_brytlinjer, las_baskarta_dxf
from markmodell.hinder_oppningar import (
    BYGGNAD,
    OKANT,
    Byggnad,
    HinderOppningFel,
    Oppning,
    brann_in_byggnader,
)
from markmodell.interpolering import InterpoleringFel
from markmodell.k1_skrivare import K1SkrivFel
from markmodell.k1_skrivare import skriv_geotiff as skriv_k1_geotiff
from markmodell.lagerprofil import Lagerprofil, hitta_lagerprofil
from markmodell.markslagskallor import belagd_mask_for_ruta
from markmodell.ortofoto import (
    OrtofotoFel,
    klassificera_ortofoto,
    kombinera_ortofoto_med_markmodell,
)
from markmodell.pipeline import PipelineFel, bygg_markmodell_ruta
from markmodell.recept import KallPost
from markmodell.rutnat import Ruta, RutnatFel
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.persistens import filtrera_persistens
from sankgraf.typer import (
    SankGrafFel,
    las_brytlinjer_fran_gpkg,
    las_oppningar_fran_gpkg,
    skriv_sankgraf_k2,
)
from testsvit.ea_fall import EaBenchmarkFel, kor_alla_ea_testfall
from testsvit.k1_io import K1FormatFel, las_geotiff
from testsvit.korare import TestsvitFel, hitta_testfall, kor_testsvit
from tvad.hybrid import kor_hybrid_2d_graf
from tvad.kalltermer import Avtappning, Kalltermer
from tvad.lokal_troghet import TvadFel, TvadResultat, kor_2d_lokal_troghet, skriv_tvad_resultat_k6


def _las_laser_csv(
    sokvag: Path,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.int32] | None,
]:
    """Läser laserpunktmoln från CSV (`e_m,n_m,z_m[,klass]` eller `e,n,z[,klass]`)."""
    if not sokvag.is_file():
        raise PipelineFel(f"Laser-CSV hittades inte: {sokvag}")

    with sokvag.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise PipelineFel(f"Laser-CSV {sokvag} saknar kolumnrubriker.")
        falt = [f.strip() for f in reader.fieldnames]
        reader.fieldnames = falt

        if {"e_m", "n_m", "z_m"}.issubset(falt):
            e_kol, n_kol, z_kol = "e_m", "n_m", "z_m"
        elif {"e", "n", "z"}.issubset(falt):
            e_kol, n_kol, z_kol = "e", "n", "z"
        else:
            raise PipelineFel(
                f"Laser-CSV {sokvag} måste ha kolumner 'e_m,n_m,z_m' eller 'e,n,z', fick {falt}."
            )

        har_klass = "klass" in falt
        e_lista: list[float] = []
        n_lista: list[float] = []
        z_lista: list[float] = []
        k_lista: list[int] = []

        for rad_nr, row in enumerate(reader, start=2):
            try:
                e_val = float(row[e_kol])
                n_val = float(row[n_kol])
                z_val = float(row[z_kol])
                if har_klass:
                    k_val = int(float(row["klass"]))
                    k_lista.append(k_val)
            except (TypeError, ValueError) as exc:
                raise PipelineFel(f"Ogiltigt tal på rad {rad_nr} i {sokvag}: {row}") from exc
            if not (math.isfinite(e_val) and math.isfinite(n_val) and math.isfinite(z_val)):
                raise PipelineFel(f"Icke-ändlig koordinat på rad {rad_nr} i {sokvag}.")
            e_lista.append(e_val)
            n_lista.append(n_val)
            z_lista.append(z_val)

    if not e_lista:
        raise PipelineFel(f"Laser-CSV {sokvag} innehåller inga punkter.")

    pe = np.asarray(e_lista, dtype=np.float64)
    pn = np.asarray(n_lista, dtype=np.float64)
    pz = np.asarray(z_lista, dtype=np.float64)
    klasser = np.asarray(k_lista, dtype=np.int32) if har_klass else None
    return pe, pn, pz, klasser


def _las_laser_las(sokvag: Path, epsg: int) -> PunktMoln:
    """Läser LAS direkt eller LAZ via lasclip (ingen klippning) som punktmoln i EPSG:3011."""
    if sokvag.suffix.lower() == ".laz":
        stor = 1.0e9
        return las_laser_punktmoln(sokvag, (-stor, -stor, stor, stor), kall_epsg=epsg)
    return las_las_punktmoln(sokvag, kall_epsg=epsg)


def _las_ortofoto_rgb(sokvag: Path, form_rc: tuple[int, int]) -> NDArray[np.uint8]:
    """Läser ortofoto (PNG/TIFF) som RGB-uint8 och skalar vid behov till `form_rc`."""
    if not sokvag.is_file():
        raise OrtofotoFel(f"Ortofotofil hittades inte: {sokvag}")
    rader, kolumner = form_rc
    with Image.open(sokvag) as img:
        rgb = img.convert("RGB")
        if rgb.size != (kolumner, rader):
            rgb = rgb.resize((kolumner, rader), Image.Resampling.BILINEAR)
        return np.asarray(rgb, dtype=np.uint8)


def _kor_markmodell_kommando(args: argparse.Namespace) -> int:
    try:
        if args.grund_tif is None and args.laser_csv is None and args.laser_las is None:
            raise PipelineFel(
                "Ange minst en höjdkälla: --grund-tif, --laser-csv eller --laser-las."
            )
        if not math.isfinite(args.upplosning_m) or args.upplosning_m <= 0.0:
            raise PipelineFel(f"upplosning_m måste vara > 0, fick {args.upplosning_m}.")

        extra_kallor: list[KallPost] = []
        grund_z: NDArray[np.float32] | None = None
        ruta: Ruta | None = None

        if args.grund_tif is not None:
            grund_fil = Path(args.grund_tif)
            grund_raw, geo = las_geotiff(grund_fil)
            if geo.hojd != geo.bredd:
                raise PipelineFel(
                    f"Grund-GeoTIFF måste vara kvadratisk för Ruta, fick {geo.hojd}x{geo.bredd}."
                )
            if not math.isclose(geo.pixelstorlek_x, geo.pixelstorlek_y, rel_tol=0.0, abs_tol=1e-9):
                raise PipelineFel(
                    "Grund-GeoTIFF måste ha kvadratiska pixlar, fick "
                    f"{geo.pixelstorlek_x}x{geo.pixelstorlek_y}."
                )
            grund_z = np.asarray(grund_raw, dtype=np.float32)
            ruta = Ruta(
                e_min_m=geo.x_min,
                n_min_m=geo.y_min,
                storlek_m=geo.bredd * geo.pixelstorlek_x,
                overlapp_m=0.0,
                upplosning_m=geo.pixelstorlek_x,
            )
            extra_kallor.append(
                KallPost(
                    roll="grund_tif",
                    kalla=grund_fil.name,
                    sha256=sha256_fil(grund_fil),
                )
            )

        pe: NDArray[np.float64] | None = None
        pn: NDArray[np.float64] | None = None
        pz: NDArray[np.float64] | None = None
        klasser: NDArray[np.int32] | None = None

        laser_moln: PunktMoln | None = None
        if args.laser_csv is not None and args.laser_las is not None:
            raise PipelineFel("Ange antingen --laser-csv eller --laser-las, inte båda.")
        if args.laser_las is not None:
            las_fil = Path(args.laser_las)
            laser_moln = _las_laser_las(las_fil, args.laser_epsg)
            pe, pn, pz = laser_moln.e_m, laser_moln.n_m, laser_moln.z_rh2000
            klasser = laser_moln.klass.astype(np.int32)
            extra_kallor.append(
                KallPost(roll="laser_las", kalla=las_fil.name, sha256=sha256_fil(las_fil))
            )
        if args.laser_csv is not None:
            laser_fil = Path(args.laser_csv)
            pe, pn, pz, klasser = _las_laser_csv(laser_fil)
            extra_kallor.append(
                KallPost(
                    roll="laser_csv",
                    kalla=laser_fil.name,
                    sha256=sha256_fil(laser_fil),
                )
            )
        if pe is not None and pn is not None and ruta is None:
            uppl = float(args.upplosning_m)
            e_lo = float(np.min(pe))
            e_hi = float(np.max(pe))
            n_lo = float(np.min(pn))
            n_hi = float(np.max(pn))
            e_min = math.floor(e_lo / uppl) * uppl
            n_min = math.floor(n_lo / uppl) * uppl
            n_kol = math.floor((e_hi - e_min) / uppl) + 1
            n_rad = math.floor((n_hi - n_min) / uppl) + 1
            n_celler = max(4, n_kol, n_rad)
            ruta = Ruta(
                e_min_m=e_min,
                n_min_m=n_min,
                storlek_m=n_celler * uppl,
                overlapp_m=0.0,
                upplosning_m=uppl,
            )

        assert ruta is not None

        brytlinjer: tuple[Brytlinje, ...] = ()
        byggnader: tuple[Byggnad, ...] = ()
        oppningar: tuple[Oppning, ...] = ()
        dxf_varningar: list[str] = []

        markslagsytor: tuple[MarkslagsYta, ...] = ()
        lagerprofil: Lagerprofil | None = None
        if args.dxf is not None and args.dwg is not None:
            raise PipelineFel("Ange antingen --dxf eller --dwg, inte båda.")
        if args.dwg is not None:
            from gemensamt.konfig import las_konfig

            try:
                konfig: dict[str, Any] | None = las_konfig()
            except Exception:  # config.toml saknas utanför repot: använd repots profil
                konfig = None
            lagerprofil = hitta_lagerprofil(args.lagerprofil, konfig)
            dwg_fil = Path(args.dwg)
            dwg_res = las_baskarta_dwg(dwg_fil, lagerprofil=lagerprofil)
            baskarta = dwg_res.baskarta
            markslagsytor = dwg_res.markslagsytor
            brytlinjer = baskarta.brytlinjer
            byggnader = baskarta.byggnader
            oppningar = baskarta.oppningar
            dxf_varningar = list(baskarta.varningar)
            extra_kallor.append(
                KallPost(roll="baskarta_dwg", kalla=dwg_fil.name, sha256=sha256_fil(dwg_fil))
            )
        if args.dxf is not None:
            dxf_fil = Path(args.dxf)
            baskarta = las_baskarta_dxf(dxf_fil)
            brytlinjer = baskarta.brytlinjer
            byggnader = baskarta.byggnader
            oppningar = baskarta.oppningar
            dxf_varningar = list(baskarta.varningar)
            extra_kallor.append(
                KallPost(
                    roll="baskarta_dxf",
                    kalla=dxf_fil.name,
                    sha256=sha256_fil(dxf_fil),
                )
            )

        byggnadskandidater: tuple[ByggnadsKandidat, ...] = ()
        if args.byggnader is not None:
            bygg_fil = Path(args.byggnader)
            bygg_res = las_byggnadskalla(
                bygg_fil, lager=args.byggnader_lager, fran_epsg=args.byggnader_epsg
            )
            byggnadskandidater = bygg_res.kandidater
            dxf_varningar.extend(bygg_res.varningar)
            extra_kallor.append(
                KallPost(roll="byggnadskalla", kalla=bygg_fil.name, sha256=bygg_res.sha256)
            )

        avstand_karta: NDArray[np.float32] | None = None
        anvand_barriar_idw = bool(
            args.barriar_idw
            and pe is not None
            and pn is not None
            and pz is not None
            and len(brytlinjer) > 0
        )
        if anvand_barriar_idw:
            assert pe is not None and pn is not None and pz is not None
            barriar_res = interpolera_med_brytlinjer(
                pe,
                pn,
                pz,
                ruta,
                brytlinjer,
                klasser=klasser,
            )
            if grund_z is None:
                grund_z = barriar_res.mark_z
            avstand_karta = barriar_res.avstand_m
            if barriar_res.antal_blockerade_celler > 0:
                dxf_varningar.append(
                    f"{barriar_res.antal_blockerade_celler} celler saknade oskuren "
                    "markpunkt vid barriär-IDW."
                )

        markslag_grund: NDArray[np.uint8] | None = None
        ortofoto_bild: NDArray[np.uint8] | None = None
        ortofoto_slag: NDArray[np.uint8] | None = None
        ortofoto_konf: NDArray[np.float32] | None = None
        belagd_mask: NDArray[np.bool_] | None = None
        belagd_inst: dict[str, float] | None = None
        if args.belagd_yta:
            if laser_moln is None or laser_moln.intensitet is None:
                raise PipelineFel("--belagd-yta kräver --laser-las med intensitet.")
            belagd_mask, belagd_res = belagd_mask_for_ruta(laser_moln, ruta)
            belagd_inst = belagd_res.installningar
            if belagd_res.skal is not None:
                dxf_varningar.append(f"Ingen belagd yta ur laser: {belagd_res.skal}.")
        # K1 v2-tillägget markslag_kalla.tif skrivs när fler källor än ortofotot används
        flerkalla = args.dwg is not None or belagd_mask is not None
        if args.ortofoto is not None:
            ortofoto_fil = Path(args.ortofoto)
            ortofoto_bild = _las_ortofoto_rgb(ortofoto_fil, ruta.form_rc)
            if flerkalla:
                _, bygg_slag = brann_in_byggnader(
                    np.zeros(ruta.form_rc, dtype=np.float32),
                    np.zeros(ruta.form_rc, dtype=np.uint8),
                    ruta,
                    byggnader,
                )
                kl = klassificera_ortofoto(ortofoto_bild, byggnad_mask=(bygg_slag == BYGGNAD))
                ortofoto_slag, ortofoto_konf = kl.markslag, kl.konfidens
            else:
                markslag_grund = np.full(ruta.form_rc, OKANT, dtype=np.uint8)
            extra_kallor.append(
                KallPost(
                    roll="ortofoto",
                    kalla=ortofoto_fil.name,
                    sha256=sha256_fil(ortofoto_fil),
                )
            )

        mal_ut = Path(args.ut)
        mal_ut.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            res = bygg_markmodell_ruta(
                ruta,
                tmp_dir,
                grund_z=grund_z,
                mark_punkter_e=pe,
                mark_punkter_n=pn,
                mark_punkter_z=pz,
                laser_klasser=klasser,
                brytlinjer=brytlinjer,
                byggnader=byggnader,
                oppningar=oppningar,
                byggnadskandidater=byggnadskandidater,
                markslag_grund=markslag_grund,
                markslagsytor=markslagsytor,
                belagd_yta_mask=belagd_mask,
                belagd_installningar=belagd_inst,
                ortofoto_markslag=ortofoto_slag,
                ortofoto_konfidens=ortofoto_konf,
                lagerprofil=lagerprofil,
                avstand_till_punkt_m=avstand_karta,
                extra_kallor=extra_kallor,
            )

            slut_markslag = res.markslag
            slut_osakerhet = res.osakerhet_z

            if ortofoto_bild is not None and flerkalla:
                # markslag avgjordes av prioritetsordningen; ortofotots skuggtillägg på osäkerheten
                kl_o = klassificera_ortofoto(ortofoto_bild, byggnad_mask=(res.markslag == BYGGNAD))
                _, slut_osakerhet = kombinera_ortofoto_med_markmodell(
                    res.markslag, res.osakerhet_z, kl_o
                )
                skriv_k1_geotiff(
                    res.katalog / "osakerhet.tif", slut_osakerhet, ruta, nodata=-9999.0
                )
            elif ortofoto_bild is not None:
                klassning = klassificera_ortofoto(
                    ortofoto_bild,
                    byggnad_mask=(res.markslag == BYGGNAD),
                )
                slut_markslag, slut_osakerhet = kombinera_ortofoto_med_markmodell(
                    res.markslag,
                    res.osakerhet_z,
                    klassning,
                )
                skriv_k1_geotiff(
                    res.katalog / "markslag.tif",
                    slut_markslag,
                    ruta,
                    nodata=0,
                )
                skriv_k1_geotiff(
                    res.katalog / "osakerhet.tif",
                    slut_osakerhet,
                    ruta,
                    nodata=-9999.0,
                )

            manifest = las_manifest(res.katalog / "run.json")
            manifest.parametrar["ruta_id"] = args.ruta_id
            manifest.parametrar["barriar_idw"] = anvand_barriar_idw
            manifest.parametrar["ortofoto"] = ortofoto_bild is not None
            if dxf_varningar:
                manifest.varningar.extend(dxf_varningar)
            manifest.matvarden["osakerhet_medel_m"] = round(float(np.mean(slut_osakerhet)), 4)
            manifest.matvarden["osakerhet_max_m"] = round(float(np.max(slut_osakerhet)), 4)
            manifest.utdata = [
                FilPost(
                    roll=post.roll,
                    fil=post.fil,
                    sha256=sha256_fil(res.katalog / post.fil),
                )
                for post in manifest.utdata
            ]
            skriv_manifest(manifest, res.katalog / "run.json")

            slut_filer: dict[str, Path] = {}
            for roll, kalla in res.filer.items():
                mal_fil = mal_ut / kalla.name
                shutil.move(str(kalla), str(mal_fil))
                slut_filer[roll] = mal_fil

        varningar = [*res.varningar, *dxf_varningar]
        rader, kolumner = ruta.form_rc
        osak_medel = float(np.mean(slut_osakerhet))
        osak_max = float(np.max(slut_osakerhet))

        payload: dict[str, Any] = {
            "ruta_id": args.ruta_id,
            "ruta_namn": ruta.namn,
            "katalog": str(mal_ut),
            "form_rc": [rader, kolumner],
            "upplosning_m": float(ruta.upplosning_m),
            "barriar_idw": anvand_barriar_idw,
            "ortofoto": ortofoto_bild is not None,
            "antal_brytlinjer": len(brytlinjer),
            "antal_byggnader": len(byggnader),
            "antal_oppningar": len(oppningar),
            "osakerhet_medel_m": osak_medel,
            "osakerhet_max_m": osak_max,
            "recept_sha256": res.recept_sha256,
            "varningar": varningar,
            "filer": {k: str(v) for k, v in slut_filer.items()},
        }

        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print(f"Markmodell byggd: {args.ruta_id} ({ruta.namn}) -> {mal_ut}")
        print(f"Rutnät: {kolumner}x{rader} celler ({ruta.upplosning_m:g} m upplösning)")
        print(
            f"Vektorlager: {len(brytlinjer)} brytlinjer, "
            f"{len(byggnader)} byggnader, {len(oppningar)} öppningar"
        )
        print(
            f"Osäkerhet: medel {osak_medel:.4f} m, max {osak_max:.4f} m "
            f"(barriär-IDW: {'ja' if anvand_barriar_idw else 'nej'})"
        )
        print(f"Recept SHA-256: {res.recept_sha256}")
        return 0
    except (
        PipelineFel,
        ByggnadsregisterFel,
        GrundkartaFel,
        InterpoleringFel,
        BrytlinjeFel,
        HinderOppningFel,
        OrtofotoFel,
        K1SkrivFel,
        K1FormatFel,
        RutnatFel,
        ValueError,
        OSError,
    ) as exc:
        print(f"Fel i markmodellkommando: {exc}", file=sys.stderr)
        return 2


def _kor_sankgraf_kommando(args: argparse.Namespace) -> int:
    try:
        indata = _hitta_omrade_mapp(args.k1, args.rot)
        mark_raw, geo = las_geotiff(indata / "mark.tif")
        osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
        slag_raw, _ = las_geotiff(indata / "markslag.tif")
        mark_z = np.asarray(mark_raw, dtype=np.float32)
        osak_z = np.asarray(osak_raw, dtype=np.float32)
        markslag = np.asarray(slag_raw, dtype=np.uint8)

        bryt_fil = indata / "brytlinjer.gpkg"
        brytlinjer = tuple(las_brytlinjer_fran_gpkg(bryt_fil)) if bryt_fil.is_file() else ()
        opp_fil = indata / "oppningar.gpkg"
        oppningar = tuple(las_oppningar_fran_gpkg(opp_fil)) if opp_fil.is_file() else ()

        graf = bygg_sankgraf(
            mark_z,
            osak_z,
            markslag,
            geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
        )
        if args.persistens_faktor is not None:
            graf = filtrera_persistens(graf, faktor=args.persistens_faktor)

        lovnoder = graf.lovnoder()
        max_sankvolym_m3 = max((n.max_volym_m3 for n in graf.noder.values()), default=0.0)

        skrivna_filer: dict[str, Path] = {}
        if args.ut is not None:
            indata_poster = [
                FilPost(roll="mark", fil="mark.tif", sha256=sha256_fil(indata / "mark.tif")),
                FilPost(
                    roll="osakerhet",
                    fil="osakerhet.tif",
                    sha256=sha256_fil(indata / "osakerhet.tif"),
                ),
                FilPost(
                    roll="markslag",
                    fil="markslag.tif",
                    sha256=sha256_fil(indata / "markslag.tif"),
                ),
            ]
            if bryt_fil.is_file():
                indata_poster.append(
                    FilPost(roll="brytlinjer", fil="brytlinjer.gpkg", sha256=sha256_fil(bryt_fil))
                )
            if opp_fil.is_file():
                indata_poster.append(
                    FilPost(roll="oppningar", fil="oppningar.gpkg", sha256=sha256_fil(opp_fil))
                )
            skrivna_filer = skriv_sankgraf_k2(
                graf,
                args.ut,
                indata=indata_poster,
                parametrar={"k1_mapp": str(indata)},
            )

        payload: dict[str, Any] = {
            "k1_mapp": str(indata),
            "antal_noder": len(graf.noder),
            "antal_lovnoder": len(lovnoder),
            "antal_rotnoder": len(graf.rotnoder()),
            "antal_flodesvagar": len(graf.flodesvagar),
            "max_sankvolym_m3": float(max_sankvolym_m3),
            "filtrerad": graf.filtrerad,
            "persistens_faktor": graf.persistens_faktor,
            "ut_filer": {k: str(v) for k, v in skrivna_filer.items()},
        }

        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print(f"Sänkgraf byggd från: {indata}")
        print(
            f"Noder: {len(graf.noder)} (lövnoder: {len(lovnoder)}, "
            f"flödesvägar: {len(graf.flodesvagar)})"
        )
        print(f"Max sänkvolym: {max_sankvolym_m3:.3f} m³")
        if graf.filtrerad:
            print(f"Persistensfilter: faktor {graf.persistens_faktor:g}")
        if args.ut is not None:
            print(f"Skrev K2-utdata till: {args.ut}")
        return 0
    except (TestsvitFel, K1FormatFel, SankGrafFel, ValueError, OSError) as exc:
        print(f"Fel i sankgrafkommando: {exc}", file=sys.stderr)
        return 2


def _kor_blaytor_kommando(args: argparse.Namespace) -> int:
    """Blåytor vid X mm (systemdesign 4.1): sänkgraf + statisk Fill–Spill–Merge, K6-likt ut."""
    from vatten.blaytor import Blaytemodell, BlaytorFel, k1_filer, skriv_blaytor

    try:
        indata = _hitta_omrade_mapp(args.k1, args.rot)
        filer = k1_filer(indata)
        modell = Blaytemodell.fran_k1_mapp(indata, args.persistens_faktor)
        payload: list[dict[str, Any]] = []
        for mm in args.mm:
            r = modell.vid_mm(mm)
            ut = Path(args.ut) if len(args.mm) == 1 else Path(args.ut) / f"mm_{mm:g}"
            skriv_blaytor(
                r,
                modell.graf.geo,
                ut,
                indata_filer=filer,
                parametrar={"k1_mapp": str(indata), "persistens_faktor": args.persistens_faktor},
            )
            m = r.matvarden()
            m["ut"] = str(ut)
            payload.append(m)
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        for m in payload:
            print(
                f"{m['regn_mm']:g} mm: lagrat {m['lagrat_m3']:.3f} m³, ut {m['ut_m3']:.3f} m³, "
                f"max djup {m['max_djup_m']:.3f} m, fyllda sänkor {m['antal_fyllda_sankor']}, "
                f"volymfel {m['volymfel_rel']:.1e} -> {m['ut']}"
            )
        return 0
    except (TestsvitFel, K1FormatFel, SankGrafFel, BlaytorFel, ValueError, OSError) as exc:
        print(f"Fel i blaytorkommando: {exc}", file=sys.stderr)
        return 2


IDF_VAL: tuple[str, ...] = (IDF_DAHLSTROM_2010, *sorted(IDF_KALLOR))


def _idf_kalla_fran_args(args: argparse.Namespace) -> IdfKalla | None:
    """Registrerad IDF-tabell vald med `--idf`, eller None för Dahlström (2010)."""
    val = getattr(args, "idf", IDF_DAHLSTROM_2010)
    return None if val == IDF_DAHLSTROM_2010 else IDF_KALLOR[val]


def _klimatfaktor_fran_args(args: argparse.Namespace, standard_utan_idf: float) -> float:
    """Uttryckligt `--klimatfaktor`, annars källans standard (Stockholmsregn 2024: 1,4)."""
    if args.klimatfaktor is not None:
        return float(args.klimatfaktor)
    kalla = _idf_kalla_fran_args(args)
    return kalla.standard_klimatfaktor if kalla is not None else standard_utan_idf


def _lagg_idf_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--idf",
        type=str,
        default=IDF_DAHLSTROM_2010,
        choices=IDF_VAL,
        help=(
            "IDF-källa: 'dahlstrom-2010' (standard, formel) eller 'stockholmsregn-2024' "
            "(Tabell 7, utan klimatfaktor; standardfaktor 1,4 enligt rapporten, 1,25 kan väljas "
            "med --klimatfaktor)."
        ),
    )


def _ladda_regn_for_kor(args: argparse.Namespace, indata: Path) -> tuple[RegnSerieK4, Path | None]:
    har_csv = args.regn_csv is not None
    har_spec = args.regn_id is not None or args.aterkomsttid_ar is not None
    idf_kalla = _idf_kalla_fran_args(args)
    if idf_kalla is not None:
        if har_csv or args.regn_id is not None:
            raise RegnFel(
                f"--idf {idf_kalla.kalla_id} kan inte kombineras med --regn-csv/--regn-id."
            )
        if args.aterkomsttid_ar is None:
            raise RegnFel(f"--idf {idf_kalla.kalla_id} kräver --aterkomsttid-ar.")
        return (
            skapa_regn_fran_idf_kalla(
                idf_kalla.kalla_id,
                args.aterkomsttid_ar,
                klimatfaktor=_klimatfaktor_fran_args(args, 1.0),
                typ=args.regntyp,
                varaktighet_min=args.varaktighet_min,
            ),
            None,
        )
    if har_csv and har_spec:
        raise RegnFel("Ange inte både --regn-csv och --regn-id/--aterkomsttid-ar samtidigt.")

    if har_csv:
        regn_fil = Path(args.regn_csv)
        return las_regn_k4_csv(regn_fil), regn_fil

    if har_spec:
        serie = skapa_regn_fran_spec(
            regn_id=args.regn_id,
            aterkomsttid_ar=args.aterkomsttid_ar,
            klimatfaktor=_klimatfaktor_fran_args(args, 1.0),
            varaktighet_min=args.varaktighet_min,
            typ=args.regntyp,
        )
        return serie, None

    k1_regn = indata / "regn.csv"
    if k1_regn.is_file():
        return las_regn_k4_csv(k1_regn), k1_regn

    raise RegnFel(
        f"Inget regn angavs (--regn-id, --regn-csv eller --aterkomsttid-ar) och '{k1_regn}' saknas."
    )


def _kor_kor_kommando(args: argparse.Namespace) -> int:
    try:
        indata = _hitta_omrade_mapp(args.k1, args.rot)
        mark_raw, geo = las_geotiff(indata / "mark.tif")
        osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
        slag_raw, _ = las_geotiff(indata / "markslag.tif")
        mark_z = np.asarray(mark_raw, dtype=np.float32)
        osak_z = np.asarray(osak_raw, dtype=np.float32)
        markslag = np.asarray(slag_raw, dtype=np.uint8)

        bryt_fil = indata / "brytlinjer.gpkg"
        brytlinjer = tuple(las_brytlinjer_fran_gpkg(bryt_fil)) if bryt_fil.is_file() else ()
        opp_fil = indata / "oppningar.gpkg"
        oppningar = tuple(las_oppningar_fran_gpkg(opp_fil)) if opp_fil.is_file() else ()

        graf = bygg_sankgraf(
            mark_z,
            osak_z,
            markslag,
            geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
        )
        # Takvatten (standard på) och avtappning (standard 0 mm/h), beslut 2026-11-22.
        from sankgraf.takvatten import med_takvatten_graf

        if args.takvatten == "pa":
            graf = med_takvatten_graf(graf, indata)

        serie, regn_fil = _ladda_regn_for_kor(args, indata)

        koppl: ForlustKopplingResultat | None = None
        if args.jordart is not None:
            koppl = berakna_effektivt_regn_k5(
                graf.markslag,
                serie.tid_s,
                serie.intensitet_mm_h,
                jordart=args.jordart,
                grundvatten=args.grundvatten,
                tjale=args.tjale,
                initial_mm=args.initial_mm,
                pixelstorlek_m=float(geo.pixelstorlek_x),
                mark_z=graf.mark_z,
                regn_ref=serie.regn_id,
            )
            regn_tid_s: Sequence[float] = koppl.effektiv_regn_tid_s
            regn_intensitet_mm_h: Sequence[float] = koppl.effektiv_regn_intensitet_mm_h
        else:
            regn_tid_s = serie.tid_s
            regn_intensitet_mm_h = serie.intensitet_mm_h

        indata_poster = [
            FilPost(roll="mark", fil="mark.tif", sha256=sha256_fil(indata / "mark.tif")),
            FilPost(
                roll="osakerhet",
                fil="osakerhet.tif",
                sha256=sha256_fil(indata / "osakerhet.tif"),
            ),
            FilPost(
                roll="markslag",
                fil="markslag.tif",
                sha256=sha256_fil(indata / "markslag.tif"),
            ),
        ]
        if regn_fil is not None:
            indata_poster.append(
                FilPost(roll="regn", fil=regn_fil.name, sha256=sha256_fil(regn_fil))
            )

        parametrar: dict[str, Any] = {
            "modell": args.modell,
            "regn_id": serie.regn_id,
            "brutto_regn_mm": float(serie.total_djup_mm),
            "regn_statistikkalla": serie.statistikkalla,
            "regn_klimatfaktor": float(serie.klimatfaktor),
            "subgrid_faktor": int(args.subgrid_faktor),
            "takvatten": args.takvatten == "pa",
            "avtappning_mm_h": float(args.avtappning_mm_h),
            "avtappning_kalla": (
                "angiven (--avtappning-mm-h)"
                if args.avtappning_mm_h > 0.0
                else "standard: 0 mm/h (ledningsnätet antas fullt)"
            ),
        }
        avt_kap = float(args.avtappning_mm_h) if args.avtappning_mm_h > 0.0 else None
        idf_kalla_kor = _idf_kalla_fran_args(args)
        if idf_kalla_kor is not None:
            parametrar.update(idf_kalla_kor.manifest_parametrar(serie.klimatfaktor))
            indata_poster.extend(
                FilPost(roll=roll, fil=fil.name, sha256=sha256_fil(fil))
                for roll, fil in idf_kalla_kor.indatafiler()
            )
        if koppl is not None:
            parametrar["t06"] = koppl.till_dict()

        skrivna_filer: dict[str, Path] = {}
        res_graf: DynamikResultat | None = None
        res_tvad: TvadResultat | None = None

        if args.modell == "graf":
            res_graf = kor_dynamik_handelsestyrd(
                graf, regn_tid_s, regn_intensitet_mm_h, avtappning_mm_h=avt_kap
            )
            if args.ut is not None:
                skrivna_filer = skriv_dynamik_resultat_k6(
                    res_graf,
                    graf,
                    args.ut,
                    indata=indata_poster,
                    parametrar=parametrar,
                )
            max_djup_m = float(np.max(res_graf.max_djup_m)) if res_graf.max_djup_m.size > 0 else 0.0
            in_m3 = float(res_graf.in_m3)
            lagrat_m3 = float(res_graf.lagrat_m3)
            ut_m3 = float(res_graf.ut_m3)
            volymfel_rel = float(res_graf.volymfel_rel)
            kortid_s = float(res_graf.kortid_s)
            metod = res_graf.metod
        elif args.modell == "2d":
            res_tvad = kor_2d_lokal_troghet(
                graf.mark_z,
                graf.markslag,
                geo,
                regn_tid_s,
                regn_intensitet_mm_h,
                subgrid_faktor=args.subgrid_faktor,
                takvatten=graf.takvatten,
                kalltermer=(
                    None
                    if avt_kap is None
                    else Kalltermer(avtappning=Avtappning.likformig(graf.mark_z.shape, avt_kap))
                ),
            )
            if args.ut is not None:
                skrivna_filer = skriv_tvad_resultat_k6(
                    res_tvad,
                    geo,
                    args.ut,
                    indata=indata_poster,
                    parametrar=parametrar,
                )
            max_djup_m = float(np.max(res_tvad.max_djup_m)) if res_tvad.max_djup_m.size > 0 else 0.0
            in_m3 = float(res_tvad.in_m3)
            lagrat_m3 = float(res_tvad.lagrat_m3)
            ut_m3 = float(res_tvad.ut_m3)
            volymfel_rel = float(res_tvad.volymfel_rel)
            kortid_s = float(res_tvad.kortid_s)
            metod = res_tvad.niva
        else:
            if avt_kap is not None:
                raise ValueError("--avtappning-mm-h stöds inte i hybriden; använd graf eller 2d.")
            res_tvad = kor_hybrid_2d_graf(
                graf.mark_z,
                graf.markslag,
                geo,
                regn_tid_s,
                regn_intensitet_mm_h,
                graf=graf,
                subgrid_faktor=args.subgrid_faktor,
            )

            if args.ut is not None:
                skrivna_filer = skriv_tvad_resultat_k6(
                    res_tvad,
                    geo,
                    args.ut,
                    indata=indata_poster,
                    parametrar=parametrar,
                )
            max_djup_m = float(np.max(res_tvad.max_djup_m)) if res_tvad.max_djup_m.size > 0 else 0.0
            in_m3 = float(res_tvad.in_m3)
            lagrat_m3 = float(res_tvad.lagrat_m3)
            ut_m3 = float(res_tvad.ut_m3)
            volymfel_rel = float(res_tvad.volymfel_rel)
            kortid_s = float(res_tvad.kortid_s)
            metod = res_tvad.niva

        payload: dict[str, Any] = {
            "k1_mapp": str(indata),
            "modell": args.modell,
            "metod": metod,
            "regn_id": serie.regn_id,
            "brutto_regn_mm": float(serie.total_djup_mm),
            "max_djup_m": max_djup_m,
            "in_m3": in_m3,
            "lagrat_m3": lagrat_m3,
            "ut_m3": ut_m3,
            "volymfel_rel": volymfel_rel,
            "kortid_s": kortid_s,
            "ut_filer": {k: str(v) for k, v in skrivna_filer.items()},
        }
        if koppl is not None:
            payload["t06"] = koppl.till_dict()
        if res_tvad is not None:
            payload["subgrid_faktor"] = int(args.subgrid_faktor)
            payload["max_hastighet_m_s"] = (
                float(np.max(res_tvad.max_hastighet_m_s))
                if res_tvad.max_hastighet_m_s.size > 0
                else 0.0
            )
            payload["max_fara_m2_s"] = (
                float(np.max(res_tvad.fara_m2_s)) if res_tvad.fara_m2_s.size > 0 else 0.0
            )

        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print(f"Körning ({args.modell} / {metod}): {indata}")
        print(f"Regn: {serie.namn} ({serie.regn_id}), brutto {serie.total_djup_mm:.2f} mm")
        if koppl is not None:
            print(
                f"T06 markförluster ({koppl.jordart}): "
                f"nettoavrinning {koppl.netto_avrinning_medel_mm:.2f} mm "
                f"(C={koppl.effektiv_avrinningskoefficient:.3f})"
            )
        print(f"Maxdjup: {max_djup_m:.4f} m")
        print(
            f"Volym: in={in_m3:.3f} m³, lagrat={lagrat_m3:.3f} m³, "
            f"ut={ut_m3:.3f} m³ (fel={volymfel_rel:.4%})"
        )
        print(f"Körtid: {kortid_s:.4f} s")
        if args.ut is not None:
            print(f"Skrev K6-resultat till: {args.ut}")
        return 0
    except (
        TestsvitFel,
        K1FormatFel,
        SankGrafFel,
        DynamikFel,
        TvadFel,
        RegnFel,
        ForlustFel,
        ValueError,
        OSError,
    ) as exc:
        print(f"Fel i korkommando: {exc}", file=sys.stderr)
        return 2


def _kor_testsvit_kommando(rot: str, grupp: str | None) -> int:
    if grupp is not None and grupp.strip().upper() == "D":
        from testsvit.hunter import kor_hunter_svit

        _, ok, tabell = kor_hunter_svit()
        print(tabell)
        utfall = "GODKAND" if ok else "UNDERKAND"
        print(f"\nSammanfattning (Grupp D / analytiska 2D-facit): {utfall}.")
        return 0 if ok else 1
    if grupp is not None and grupp.strip().upper() == "E":
        from testsvit.dagsljus_fall import kor_dagsljus_testfall

        dl_res, dl_tabell = kor_dagsljus_testfall()
        print(dl_tabell)
        antal_ok = sum(1 for r in dl_res if r.godkand)
        print(f"\nSammanfattning (Grupp E / dagsljus): {antal_ok}/{len(dl_res)} passerade.")
        return 0 if antal_ok == len(dl_res) else 1
    if grupp is not None and grupp.strip().upper() == "C":
        try:
            ea_res, ea_tabell = kor_alla_ea_testfall()
        except EaBenchmarkFel as exc:
            print(f"Fel i EA-testsvit (grupp C): {exc}", file=sys.stderr)
            return 2
        print(ea_tabell)
        antal_ok = sum(1 for r in ea_res if r.godkand)
        print(f"\nSammanfattning (Grupp C / EA): {antal_ok}/{len(ea_res)} testfall passerade.")
        return 0 if antal_ok == len(ea_res) else 1

    try:
        resultat, tabell = kor_testsvit(rot=rot, grupp=grupp)
    except TestsvitFel as exc:
        print(f"Fel i testsvit: {exc}", file=sys.stderr)
        return 2

    if not resultat:
        filter_info = f" för grupp {grupp.upper()}" if grupp else ""
        print(f"Inga testfall hittades under '{rot}'{filter_info}.")
        return 1

    print(tabell)
    antal_ok = sum(1 for r in resultat if r.godkand)
    antal_totalt = len(resultat)
    if grupp is None:
        ea_res, ea_tabell = kor_alla_ea_testfall()
        print("\n" + ea_tabell)
        antal_ok += sum(1 for r in ea_res if r.godkand)
        antal_totalt += len(ea_res)
    print(f"\nSammanfattning: {antal_ok}/{antal_totalt} testfall passerade.")
    return 0 if antal_ok == antal_totalt else 1


def _kor_regn_kommando(args: argparse.Namespace) -> int:
    try:
        if args.lista or (args.id is None and args.aterkomsttid_ar is None):
            if _idf_kalla_fran_args(args) is not None:
                raise RegnFel("--idf gäller inte --lista (förvalen bygger på Dahlström 2010).")
            forval = lista_regnforval(region=args.region)
            if args.json:
                print(json.dumps([f.till_dict() for f in forval], indent=2, ensure_ascii=False))
                return 0
            print(
                "| Regn-ID | Region | T (år) | kf | Varaktighet (min) | Totalt (mm) | Max (mm/h) |"
            )
            print("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
            for item in forval:
                info = item.till_dict()
                print(
                    f"| {item.regn_id} | {item.region} | {item.aterkomsttid_ar:g} "
                    f"| {item.klimatfaktor:.2f} | {item.varaktighet_min:g} "
                    f"| {info['total_djup_mm']:.1f} | {info['max_intensitet_mm_h']:.1f} |"
                )
            return 0

        idf_kalla = _idf_kalla_fran_args(args)
        if idf_kalla is not None:
            if args.id is not None or args.aterkomsttid_ar is None:
                raise RegnFel(
                    f"--idf {idf_kalla.kalla_id} kräver --aterkomsttid-ar och kan inte "
                    "kombineras med --id (förvalen bygger på Dahlström 2010)."
                )
            serie = skapa_regn_fran_idf_kalla(
                idf_kalla.kalla_id,
                args.aterkomsttid_ar,
                klimatfaktor=_klimatfaktor_fran_args(args, 1.0),
                typ=args.typ,
                varaktighet_min=args.varaktighet_min,
                tidssteg_min=args.tidssteg_min,
                toppfaktor=args.toppfaktor,
            )
        else:
            serie = skapa_regn_fran_spec(
                regn_id=args.id,
                aterkomsttid_ar=args.aterkomsttid_ar,
                klimatfaktor=_klimatfaktor_fran_args(args, 1.0),
                varaktighet_min=args.varaktighet_min,
                typ=args.typ,
                tidssteg_min=args.tidssteg_min,
                toppfaktor=args.toppfaktor,
            )
        if args.ut is not None:
            skriv_regn_k4_csv(args.ut, serie)

        if args.json:
            print(json.dumps(serie.till_dict(), indent=2, ensure_ascii=False))
        else:
            print(f"Regn: {serie.namn} ({serie.regn_id})")
            print(f"Typ: {serie.typ}, varaktighet: {serie.varaktighet_min:g} min")
            print(f"Klimatfaktor: {serie.klimatfaktor:.2f}, totalt: {serie.total_djup_mm:.2f} mm")
            print(f"Maxintensitet: {serie.max_intensitet_mm_h:.2f} mm/h")
            print(f"Källa: {serie.statistikkalla}")
            if idf_kalla is not None:
                print(f"Klimatfaktor-standard: {idf_kalla.standard_klimatfaktor_motivering}")
            if args.ut is not None:
                print(f"Skrev K4-CSV till: {args.ut}")
        return 0
    except (RegnFel, OSError) as exc:
        print(f"Fel i regnkommando: {exc}", file=sys.stderr)
        return 2


def _hitta_omrade_mapp(omrade_arg: str, rot: str) -> Path:
    direkt = Path(omrade_arg)
    if (direkt / "mark.tif").is_file():
        return direkt
    if (direkt / "indata" / "mark.tif").is_file():
        return direkt / "indata"

    fall_lista = hitta_testfall(rot=rot)
    for fall in fall_lista:
        if fall.id.upper() == omrade_arg.upper() or fall.mapp.name == omrade_arg:
            indata = fall.mapp / "indata"
            if (indata / "mark.tif").is_file():
                return indata
    raise TestsvitFel(
        f"Hittade inget K1-område för '{omrade_arg}' (sökte direkt sökväg och under '{rot}')."
    )


def _kor_risk_kommando(args: argparse.Namespace) -> int:
    try:
        indata = _hitta_omrade_mapp(args.omrade, args.rot)
        mark_raw, geo = las_geotiff(indata / "mark.tif")
        osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
        slag_raw, _ = las_geotiff(indata / "markslag.tif")
        mark_z = np.asarray(mark_raw, dtype=np.float32)
        osak_z = np.asarray(osak_raw, dtype=np.float32)
        markslag = np.asarray(slag_raw, dtype=np.uint8)

        bryt_fil = indata / "brytlinjer.gpkg"
        brytlinjer = tuple(las_brytlinjer_fran_gpkg(bryt_fil)) if bryt_fil.is_file() else ()
        opp_fil = indata / "oppningar.gpkg"
        oppningar = tuple(las_oppningar_fran_gpkg(opp_fil)) if opp_fil.is_file() else ()

        graf = bygg_sankgraf(
            mark_z,
            osak_z,
            markslag,
            geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
        )

        aterkomst_lista: list[float] | None = None
        if args.aterkomsttider:
            aterkomst_lista = [
                float(x.strip()) for x in args.aterkomsttider.split(",") if x.strip()
            ]

        idf_kalla = _idf_kalla_fran_args(args)
        idf_indata = (
            [
                FilPost(roll=roll, fil=fil.name, sha256=sha256_fil(fil))
                for roll, fil in idf_kalla.indatafiler()
            ]
            if idf_kalla is not None
            else None
        )
        res = berakna_kumulativ_oversvamningsrisk(
            graf,
            gransdjup_m=args.gransdjup_m,
            max_aterkomsttid_ar=args.max_aterkomsttid_ar,
            aterkomsttider_ar=aterkomst_lista,
            klimatfaktor=_klimatfaktor_fran_args(args, 1.24),
            idf_tabell=idf_kalla.las() if idf_kalla is not None else None,
            indata=idf_indata,
            varaktighet_min=args.varaktighet_min,
            regntyp=args.regntyp,
            tidshorisont_ar=args.tidshorisont_ar,
            infiltration_jordart=args.jordart,
            grundvatten=args.grundvatten,
            tjale=args.tjale,
            infiltration_initial_mm=args.initial_mm,
        )

        if args.json:
            print(json.dumps(res.sammanfattning_dict(), indent=2, ensure_ascii=False))
            return 0

        min_t = (
            f"{res.min_kritisk_aterkomsttid_ar:.1f} år"
            if math.isfinite(res.min_kritisk_aterkomsttid_ar)
            else f">{res.max_aterkomsttid_ar:g} år (ej uppnådd)"
        )
        print(
            f"Kumulativ översvämningsrisk (gränsdjup >= {res.gransdjup_m:.2f} m, "
            f"upp till {res.max_aterkomsttid_ar:g}-årsregn, kf={res.klimatfaktor:.2f}, "
            f"horisont {res.tidshorisont_ar:g} år)"
        )
        print(
            f"Yta över gränsdjup: {res.area_over_grans_m2:.1f} m² "
            f"({res.andel_over_grans * 100:.1f} %)"
        )
        print(f"Lägsta kritiska återkomsttid: {min_t}")
        print(f"Max årlig överskridandesannolikhet: {res.max_arlig_sannolikhet * 100:.2f} %/år")
        print(
            f"Max kumulativ sannolikhet ({res.tidshorisont_ar:g} år): "
            f"{res.max_kumulativ_sannolikhet * 100:.1f} %"
        )
        print(f"Max förväntat årligt djup: {res.max_forvantat_arligt_djup_m:.4f} m/år")
        print("")
        print("| Återkomsttid (år) | Regndjup (mm) | Maxdjup (m) | Yta >= gräns (m²) |")
        print("| ---: | ---: | ---: | ---: |")
        for steg in res.steg:
            print(
                f"| {steg.aterkomsttid_ar:g} | {steg.regn.total_djup_mm:.1f} "
                f"| {steg.max_djup_m:.3f} | {steg.area_over_grans_m2:.1f} |"
            )
        return 0
    except (
        TestsvitFel,
        K1FormatFel,
        SankGrafFel,
        DynamikFel,
        RegnFel,
        ForlustFel,
        ValueError,
        OSError,
    ) as exc:
        print(f"Fel i riskkommando: {exc}", file=sys.stderr)
        return 2


def bygg_parser() -> argparse.ArgumentParser:
    """Bygger huvudparsern för kommandoradsverktyget `floden`."""
    parser = argparse.ArgumentParser(
        prog="floden",
        description="Floden – öppen dynamisk skyfallsmodell på gatunivå.",
    )
    subparsers = parser.add_subparsers(dest="kommando", metavar="KOMMANDO")

    mm_parser = subparsers.add_parser(
        "markmodell",
        help="Bygg markmodell-ruta (K1/v2) från laser/GeoTIFF, DXF-baskarta och ortofoto.",
        description="Bygg komplett K1-markmodellruta i EPSG:3011 med recept.json och run.json.",
    )
    mm_parser.add_argument(
        "--ut",
        type=str,
        required=True,
        help="Målkatalog för K1-utdata (mark.tif, osakerhet.tif, recept.json, run.json m.m.).",
    )
    mm_parser.add_argument(
        "--grund-tif",
        type=str,
        default=None,
        help="Valfri GeoTIFF med grundhöjder i EPSG:3011.",
    )
    mm_parser.add_argument(
        "--laser-csv",
        type=str,
        default=None,
        help="Valfri CSV med laserpunkter (kolumner e_m,n_m,z_m eller e,n,z[,klass]).",
    )
    mm_parser.add_argument(
        "--laser-las",
        type=str,
        default=None,
        help="Valfri LAS eller LAZ med laserpunkter inklusive intensitet (LAZ via tools/lasclip, "
        "se `cargo build --release --manifest-path tools/lasclip/Cargo.toml`).",
    )
    mm_parser.add_argument(
        "--laser-epsg",
        type=int,
        default=3006,
        help="Koordinatsystem i --laser-las (standard 3006, Lantmäteriets laser); konverteras "
        "till EPSG:3011.",
    )
    mm_parser.add_argument(
        "--belagd-yta",
        action="store_true",
        help="Belagd yta ur laserintensiteten (kräver --laser-las) som markslagskälla; "
        "baskarta > belagd yta > ortofoto > standard, med markslag_kalla.tif.",
    )
    mm_parser.add_argument(
        "--dxf",
        type=str,
        default=None,
        help="Valfri ASCII-DXF baskarta (brytlinjer, byggnader, öppningar).",
    )
    mm_parser.add_argument(
        "--dwg",
        type=str,
        default=None,
        help="Valfri baskarta i DWG (eller dwgreads JSON): som --dxf plus markslagsytor ur "
        "lagerprofilens ytregler och markslag_kalla.tif "
        "(prioritet baskarta > ortofoto > standard).",
    )
    mm_parser.add_argument(
        "--lagerprofil",
        type=str,
        default=None,
        help="Lagerprofil (TOML) för --dwg; annars [markmodell] lagerprofil i config.local.toml, "
        "annars repots exempelprofil.",
    )
    mm_parser.add_argument(
        "--byggnader",
        type=str,
        default=None,
        help="Valfri byggnadskälla (GeoPackage/GeoJSON, t.ex. Lantmäteriet eller OSM) till "
        "byggnader.gpkg (K1 v2). Slås ihop med byggnader ur --dxf.",
    )
    mm_parser.add_argument("--byggnader-lager", type=str, default=None, help="Lager i --byggnader.")
    mm_parser.add_argument(
        "--byggnader-epsg",
        type=int,
        default=None,
        help="Koordinatsystem om --byggnader saknar eget (konverteras till EPSG:3011).",
    )
    mm_parser.add_argument(
        "--ortofoto",
        type=str,
        default=None,
        help="Valfri PNG/TIFF RGB-bild för ortofotoklassning av markslag och skuggosäkerhet.",
    )
    mm_parser.add_argument(
        "--barriar-idw",
        action="store_true",
        help="Använd ensidig barriär-IDW när både laserpunkter och brytlinjer finns.",
    )
    mm_parser.add_argument(
        "--ruta-id",
        type=str,
        default="ruta_01",
        help="Rutas identifikator (standard: ruta_01).",
    )
    mm_parser.add_argument(
        "--upplosning-m",
        type=float,
        default=0.25,
        help="Pixelupplösning i meter vid punktmolnsinterpolering (standard: 0.25).",
    )
    mm_parser.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut sammanfattning som JSON.",
    )

    sg_parser = subparsers.add_parser(
        "sankgraf",
        help="Bygg sänkgraf från K1-markmodell och exportera valfritt K2.",
        description=(
            "Bygg sänkhierarki och flödesvägar enligt K2 (v1, eller v2 med slutsänkor) "
            "från K1-indata."
        ),
    )
    sg_parser.add_argument(
        "--k1",
        type=str,
        required=True,
        help="K1-indatamapp eller testfalls-ID (t.ex. A3).",
    )
    sg_parser.add_argument(
        "--ut",
        type=str,
        default=None,
        help="Valfri målkatalog för K2-export.",
    )
    sg_parser.add_argument(
        "--persistens-faktor",
        type=float,
        default=None,
        help="Valfri persistensfaktor för brusfiltrering (t.ex. 2.0).",
    )
    sg_parser.add_argument(
        "--rot",
        type=str,
        default="testsvit",
        help="Rotmapp för testsviten vid testfalls-ID (standard: testsvit).",
    )
    sg_parser.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut sammanfattning som JSON.",
    )

    bl_parser = subparsers.add_parser(
        "blaytor",
        help="Blåytor vid X mm regn (sänkgraf + statisk fyllning, K6-likt ut).",
        description="Fördelar X mm likformigt regn genom sänkgrafens fill–spill-träd.",
    )
    bl_parser.add_argument(
        "--k1", type=str, required=True, help="K1-indatamapp eller testfalls-ID."
    )
    bl_parser.add_argument("--mm", type=float, nargs="+", required=True, help="Regnmängd(er) i mm.")
    bl_parser.add_argument("--ut", type=str, required=True, help="Målkatalog.")
    bl_parser.add_argument("--persistens-faktor", type=float, default=None)
    bl_parser.add_argument("--rot", type=str, default="testsvit")
    bl_parser.add_argument("--json", action="store_true", help="Skriv mätvärden som JSON.")

    kor_parser = subparsers.add_parser(
        "kor",
        help="Kör skyfallsberäkning (graf, 2d eller hybrid) till K6.",
        description="Kör dynamisk graf-, 2D- eller hybridmodell och exportera K6-resultat.",
    )
    kor_parser.add_argument(
        "--k1",
        type=str,
        required=True,
        help="K1-indatamapp eller testfalls-ID (t.ex. A3).",
    )
    kor_parser.add_argument(
        "--modell",
        type=str,
        default="graf",
        choices=("graf", "2d", "hybrid"),
        help="Beräkningsmodell: 'graf' (standard), '2d' eller 'hybrid'.",
    )
    kor_parser.add_argument(
        "--regn-id",
        type=str,
        default=None,
        help="Inbyggt regnförval, t.ex. stockholm_cds_6h_100ar_kf124.",
    )
    kor_parser.add_argument(
        "--regn-csv",
        type=str,
        default=None,
        help="Sökväg till punktregn i K4-CSV-format.",
    )
    kor_parser.add_argument(
        "--aterkomsttid-ar",
        type=float,
        default=None,
        help="Återkomsttid i år för syntetiskt regn (t.ex. 100).",
    )
    kor_parser.add_argument(
        "--klimatfaktor",
        type=float,
        default=None,
        help="Klimatfaktor (standard: 1.0; med --idf stockholmsregn-2024: 1.4).",
    )
    kor_parser.add_argument(
        "--takvatten",
        choices=("pa", "av"),
        default="pa",
        help="Regn på tak avrinner till marken längs fasaderna (standard pa; okänd takform = "
        "brutet tak, hälften till varje långsida).",
    )
    kor_parser.add_argument(
        "--avtappning-mm-h",
        type=float,
        default=0.0,
        help="Avtappning till ledningsnätet i mm/h (standard 0: ledningsnätet antas fullt).",
    )
    _lagg_idf_arg(kor_parser)
    kor_parser.add_argument(
        "--varaktighet-min",
        type=float,
        default=STANDARD_VARAKTIGHET_MIN,
        help="Regnvaraktighet i minuter (standard: 360.0).",
    )
    kor_parser.add_argument(
        "--regntyp",
        type=str,
        default="cds",
        choices=("cds", "block"),
        help="Regntyp: 'cds' (standard) eller 'block'.",
    )
    kor_parser.add_argument(
        "--jordart",
        type=str,
        default=None,
        help="Aktivera T06 Green–Ampt-infiltration för jordart (t.ex. sand, moran, lera).",
    )
    kor_parser.add_argument(
        "--grundvatten",
        type=str,
        default="normal",
        choices=("normal", "hog"),
        help="Grundvattenläge vid T06-infiltration: 'normal' (standard) eller 'hog'.",
    )
    kor_parser.add_argument(
        "--tjale",
        action="store_true",
        help="Aktivera tjäle vid T06-infiltration.",
    )
    kor_parser.add_argument(
        "--initial-mm",
        type=float,
        default=2.0,
        help="Initial ytmagasinering i mm vid T06-infiltration (standard: 2.0).",
    )
    kor_parser.add_argument(
        "--subgrid-faktor",
        type=int,
        default=2,
        help="Subgrid-aggregeringsfaktor för '2d' och 'hybrid' (standard: 2).",
    )
    kor_parser.add_argument(
        "--ut",
        type=str,
        default=None,
        help="Valfri målkatalog för K6-export (max_djup.tif, run.json m.m.).",
    )
    kor_parser.add_argument(
        "--rot",
        type=str,
        default="testsvit",
        help="Rotmapp för testsviten vid testfalls-ID (standard: testsvit).",
    )
    kor_parser.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut sammanfattning som JSON.",
    )

    testsvit_parser = subparsers.add_parser(
        "testsvit",
        help="Kör valideringstestsviten och visa en tabell med mått.",
        description="Kör testfall i testsviten och skriver ut en tabell med mätvärden.",
    )
    testsvit_parser.add_argument(
        "--grupp",
        type=str,
        default=None,
        help="Filtrera på testgrupp, t.ex. A, B, C eller D (analytiska 2D-facit).",
    )
    testsvit_parser.add_argument(
        "--rot",
        type=str,
        default="testsvit",
        help="Rotmapp för testsviten (standard: testsvit).",
    )

    regn_parser = subparsers.add_parser(
        "regn",
        help="Lista eller exportera svenska och stockholmska regn (K4/v1).",
        description="Lista inbyggda Sverige/Stockholm-regn eller generera K4-CSV.",
    )
    regn_parser.add_argument(
        "--lista",
        action="store_true",
        help="Lista alla inbyggda regnförval.",
    )
    regn_parser.add_argument(
        "--region",
        type=str,
        default=None,
        help="Filtrera förval på region ('Stockholm' eller 'Sverige').",
    )
    regn_parser.add_argument(
        "--id",
        type=str,
        default=None,
        help="Inbyggt regn-ID, t.ex. stockholm_cds_6h_100ar_kf140.",
    )
    regn_parser.add_argument(
        "--aterkomsttid-ar",
        type=float,
        default=None,
        help="Återkomsttid i år (t.ex. 20, 50, 100, 200, 500).",
    )
    regn_parser.add_argument(
        "--klimatfaktor",
        type=float,
        default=None,
        help=(
            "Klimatfaktor (t.ex. 1.0, 1.24, 1.25, 1.4). Standard: 1.0 (eller förvalets); "
            "med --idf stockholmsregn-2024: 1.4."
        ),
    )
    _lagg_idf_arg(regn_parser)
    regn_parser.add_argument(
        "--varaktighet-min",
        type=float,
        default=STANDARD_VARAKTIGHET_MIN,
        help="Regnvaraktighet i minuter (standard: 360 min = 6 h).",
    )
    regn_parser.add_argument(
        "--tidssteg-min",
        type=float,
        default=STANDARD_TIDSSTEG_MIN,
        help="Tidssteg för CDS-regn i minuter (standard: 5 min).",
    )
    regn_parser.add_argument(
        "--toppfaktor",
        type=float,
        default=STANDARD_CDS_TOPPFAKTOR,
        help="Toppfaktor r för CDS-regn (standard: 0.37).",
    )
    regn_parser.add_argument(
        "--typ",
        type=str,
        default="cds",
        choices=("cds", "block"),
        help="Regnprofil: 'cds' (standard) eller 'block'.",
    )
    regn_parser.add_argument(
        "--ut",
        type=str,
        default=None,
        help="Sökväg för att skriva K4-regnfil (.csv).",
    )
    regn_parser.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut resultat som JSON.",
    )

    risk_parser = subparsers.add_parser(
        "risk",
        help="Beräkna kumulativ översvämningsrisk upp till gränsdjup och max återkomsttid.",
        description="Beräknar kumulativ översvämningsrisk över återkomsttider för ett K1-område.",
    )
    risk_parser.add_argument(
        "--omrade",
        type=str,
        required=True,
        help="Områdes-ID (t.ex. A3) eller sökväg till K1-indatamapp.",
    )
    risk_parser.add_argument(
        "--gransdjup-m",
        type=float,
        required=True,
        help="Tröskeldjup i meter (t.ex. 0.20 eller 0.30 för räddningstjänst).",
    )
    risk_parser.add_argument(
        "--max-aterkomsttid-ar",
        type=float,
        default=100.0,
        help="Högsta återkomsttid i år (t.ex. 100 eller 500). Standard: 100.",
    )
    risk_parser.add_argument(
        "--aterkomsttider",
        type=str,
        default=None,
        help="Kommaseparerade återkomsttider i år, t.ex. '2,5,10,20,50,100'.",
    )
    risk_parser.add_argument(
        "--klimatfaktor",
        type=float,
        default=None,
        help=(
            "Klimatfaktor (t.ex. 1.0, 1.24, 1.25, 1.4). Standard: 1.24; "
            "med --idf stockholmsregn-2024: 1.4."
        ),
    )
    _lagg_idf_arg(risk_parser)
    risk_parser.add_argument(
        "--tidshorisont-ar",
        type=float,
        default=50.0,
        help="Tidshorisont i år för kumulativ sannolikhet (standard: 50).",
    )
    risk_parser.add_argument(
        "--varaktighet-min",
        type=float,
        default=STANDARD_VARAKTIGHET_MIN,
        help="Regnvaraktighet i minuter (standard: 360).",
    )
    risk_parser.add_argument(
        "--regntyp",
        type=str,
        default="cds",
        choices=("cds", "block"),
        help="Regntyp: 'cds' (standard) eller 'block'.",
    )
    risk_parser.add_argument(
        "--jordart",
        type=str,
        default=None,
        help="Aktivera T06 Green–Ampt-infiltration för jordart (t.ex. moran, sand, lera, berg).",
    )
    risk_parser.add_argument(
        "--grundvatten",
        type=str,
        default="normal",
        choices=("normal", "hog"),
        help="Grundvattenläge vid T06-infiltration: 'normal' (standard) eller 'hog'.",
    )
    risk_parser.add_argument(
        "--tjale",
        action="store_true",
        help="Aktivera tjäle (reducerar Ksat och porvolym) vid T06-infiltration.",
    )
    risk_parser.add_argument(
        "--initial-mm",
        type=float,
        default=2.0,
        help="Initial ytmagasinering i mm vid T06-infiltration (standard: 2.0).",
    )
    risk_parser.add_argument(
        "--rot",
        type=str,
        default="testsvit",
        help="Rotmapp för testsviten (standard: testsvit).",
    )
    risk_parser.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut sammanfattning som JSON.",
    )

    from karna.cli import lagg_till_parser as _lagg_till_fraga

    _lagg_till_fraga(subparsers)

    from dagsljus.cli import registrera as registrera_dagsljus

    registrera_dagsljus(subparsers)

    from luft.cli import registrera as registrera_luft

    registrera_luft(subparsers)

    from demovarld.cli import registrera as registrera_demo

    registrera_demo(subparsers)

    from lantmateriet.cli import registrera as registrera_lantmateriet

    registrera_lantmateriet(subparsers)

    from floden_mcp.cli import registrera as registrera_mcp

    registrera_mcp(subparsers)

    webb_parser = subparsers.add_parser(
        "webb",
        help="Starta Floden i webbläsaren: klient och API från samma port.",
        description="Bygger webbklienten vid behov och serverar den tillsammans med det lokala "
        "HTTP/JSON-API:t från samma port (http://127.0.0.1:8765 som standard).",
    )
    webb_parser.add_argument(
        "--bygg",
        action="store_true",
        help="Bygg om klienten (npm ci och npm run build) även om den verkar vara aktuell.",
    )
    webb_parser.add_argument(
        "--ingen-bygg",
        action="store_true",
        help="Bygg inte klienten (servera bara API:t om webb/dist saknas).",
    )
    webb_parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Värdadress (standard: 127.0.0.1).",
    )
    webb_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP-port (standard: 8765).",
    )
    webb_parser.add_argument(
        "--rot",
        type=str,
        default="testsvit",
        help="Rotmapp för K1-områden/testsvit (standard: testsvit).",
    )
    webb_parser.add_argument(
        "--konfig",
        type=str,
        default="config.toml",
        help="Sökväg till config.toml (standard: config.toml).",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Startpunkt för CLI:t `floden`."""
    parser = bygg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.kommando is None:
        parser.print_help()
        return 0
    if args.kommando == "markmodell":
        return _kor_markmodell_kommando(args)
    if args.kommando == "sankgraf":
        return _kor_sankgraf_kommando(args)
    if args.kommando == "blaytor":
        return _kor_blaytor_kommando(args)
    if args.kommando == "kor":
        return _kor_kor_kommando(args)
    if args.kommando == "testsvit":
        return _kor_testsvit_kommando(rot=args.rot, grupp=args.grupp)
    if args.kommando == "regn":
        return _kor_regn_kommando(args)
    if args.kommando == "risk":
        return _kor_risk_kommando(args)
    if args.kommando == "fraga":
        from karna.cli import kor_kommando as _kor_fraga

        return _kor_fraga(args)
    if args.kommando == "dagsljus":
        from dagsljus.cli import kor as kor_dagsljus

        return kor_dagsljus(args)
    if args.kommando == "luft":
        from luft.cli import kor as kor_luft

        return kor_luft(args)
    if args.kommando == "demo":
        from demovarld.cli import kor as kor_demo

        return kor_demo(args)
    if args.kommando in ("data", "omrade"):
        from lantmateriet.cli import kor as kor_lantmateriet

        return kor_lantmateriet(args)
    if args.kommando == "mcp":
        from floden_mcp.cli import kor as kor_mcp

        return kor_mcp(args)
    if args.kommando == "webb":
        from webb.api import skapa_http_server
        from webb.klientbygge import KlientByggeFel, sakerstall_klient

        if not args.ingen_bygg:
            try:
                sakerstall_klient(Path(args.konfig).resolve().parent / "webb", tvinga=args.bygg)
            except KlientByggeFel as exc:
                print(f"Kunde inte bygga webbklienten: {exc}", file=sys.stderr)
                return 1
        server = skapa_http_server(
            host=args.host,
            port=args.port,
            testsvit_rot=args.rot,
            konfig_fil=args.konfig,
        )
        print(f"Floden körs på http://{args.host}:{args.port}  (Ctrl+C avslutar)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
