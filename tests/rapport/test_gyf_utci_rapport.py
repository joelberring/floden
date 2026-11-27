# ruff: noqa: E501
"""Tester för additivt GYF- och UTCI-stöd i skissunderlag (PR-6: belägg, figurstil, tabeller, kontroll och export)."""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from ekologi.gyf import (
    GyfTillagg,
    berakna_gyf_fran_raster,
    gyf_till_dict,
)
from mikroklimat.fysik import berakna_varmestress, varmestress_till_dict
from rapport.belaggskalla import BelaggFel
from rapport.figurer import (
    FARG_BAKGRUND,
    FARG_BYGGNAD,
    FARG_BYGGNAD_KANT,
    FIGURSTILAR,
    figurstil_for_lager,
    hamta_figurstil,
    klassificera_raster,
    spara_kartfigur,
)
from rapport.gyf_utci_belagg import GYF_LOSARE, UTCI_LOSARE
from rapport.pastaende import PastaendeFel
from rapport.referensflode import gardens_mottagare
from rapport.referensflode_dagsljus import kontekst_fran_rapport as dagsljus_kontekst
from rapport.referensflode_dagsljus import pastaenden as dagsljus_pastaenden
from rapport.referensflode_ekologi import kontekst_fran_rapport as ekologi_kontekst
from rapport.referensflode_ekologi import pastaenden as ekologi_pastaenden
from rapport.skissexport import ExportStoppad
from rapport.skissmall import Tabell, bygg_dokument
from tests.demovarld.test_demo import Demo
from tests.rapport.test_skissrapport import Miljo, _norm, _text_docx, _text_pdf
from testsvit.k1_io import GeoTransformInfo


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("gyf_utci"))


def _skapa_gyf_data() -> tuple[dict[str, object], dict[str, object]]:
    """Deterministiska GyfResultat-dicts direkt från `src/ekologi/gyf.py` för före och efter."""
    mark_fore = np.full((10, 10), 4, dtype=np.int32)  # gräs (1.0)
    mark_fore[0:4, 0:5] = 1  # hårdgjort (0.0)
    mark_fore[0:2, 5:7] = 0  # okänt -> flaggas
    res_fore = berakna_gyf_fran_raster(mark_fore, cellstorlek_m=2.0, profil="stockholm")

    mark_efter = np.full((10, 10), 1, dtype=np.int32)  # hårdgjort
    mark_efter[0:5, 0:10] = 4  # gräs
    mark_efter[5:8, 0:5] = 3  # grönt tak
    mark_efter[9, 9] = 0  # 1 flaggad cell (4 m²)
    res_efter = berakna_gyf_fran_raster(
        mark_efter,
        cellstorlek_m=2.0,
        profil="stockholm",
        tillagg=(GyfTillagg(tillagg_id="trad_krontackning", area_m2=40.0),),
    )
    return gyf_till_dict(res_fore), gyf_till_dict(res_efter)


def _skapa_utci_data() -> tuple[dict[str, object], dict[str, object]]:
    """Deterministiska VarmestressResultat-dicts direkt från `src/mikroklimat/` för före och efter."""
    svf_fore = np.full((8, 8), 0.75, dtype=np.float64)
    skugga_fore = np.ones((8, 8), dtype=np.float64)
    skugga_fore[0:3, 0:4] = 0.0
    res_fore = berakna_varmestress(
        svf=svf_fore,
        skuggmask=skugga_fore,
        solhojd_rad=math.radians(52.0),
        sol_direkt_w_m2=780.0,
        sol_diffus_w_m2=110.0,
        t_luft_c=27.0,
        relativ_fuktighet_pct=45.0,
        vind_10m_m_s=2.0,
    )

    svf_efter = np.full((8, 8), 0.55, dtype=np.float64)
    skugga_efter = np.ones((8, 8), dtype=np.float64)
    skugga_efter[0:5, 0:5] = 0.0
    res_efter = berakna_varmestress(
        svf=svf_efter,
        skuggmask=skugga_efter,
        solhojd_rad=math.radians(52.0),
        sol_direkt_w_m2=780.0,
        sol_diffus_w_m2=110.0,
        t_luft_c=27.0,
        relativ_fuktighet_pct=45.0,
        vind_10m_m_s=2.0,
    )
    return varmestress_till_dict(res_fore), varmestress_till_dict(res_efter)


@pytest.fixture
def gyf_i_demo_ekologi(m: Miljo) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
    utmapp = m.rt.kalla.utmapp("demo-ekologi")
    gyf_rot = utmapp / "gyf"
    fore_dok, efter_dok = _skapa_gyf_data()
    try:
        for scen, dok in (("fore", fore_dok), ("efter", efter_dok)):
            fil = gyf_rot / scen / "gyf.json"
            fil.parent.mkdir(parents=True, exist_ok=True)
            fil.write_text(json.dumps(dok, ensure_ascii=False, indent=2), encoding="utf-8")
        yield fore_dok, efter_dok
    finally:
        if gyf_rot.exists():
            shutil.rmtree(gyf_rot)


@pytest.fixture
def utci_i_demo_dagsljus(m: Miljo) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
    utmapp = m.rt.kalla.utmapp("demo-dagsljus")
    utci_rot = utmapp / "utci"
    fore_dok, efter_dok = _skapa_utci_data()
    try:
        for scen, dok in (("bas", fore_dok), ("nytt_hus", efter_dok)):
            fil = utci_rot / scen / "utci.json"
            fil.parent.mkdir(parents=True, exist_ok=True)
            fil.write_text(json.dumps(dok, ensure_ascii=False, indent=2), encoding="utf-8")
        yield fore_dok, efter_dok
    finally:
        if utci_rot.exists():
            shutil.rmtree(utci_rot)


# ------------------------------------------------------------------ 1. Beläggsupplösning


def test_gyf_belagg_fran_riktigt_gyfresultat(
    m: Miljo, gyf_i_demo_ekologi: tuple[dict[str, object], dict[str, object]]
) -> None:
    fore_dok, efter_dok = gyf_i_demo_ekologi
    k = m.rt.kalla

    b_gyf = k.hamta("gyf.efter.gyf", "demo-ekologi", {})
    assert b_gyf.varde == pytest.approx(float(efter_dok["gyf"]))  # type: ignore[arg-type]
    assert b_gyf.enhet == "andel"
    assert b_gyf.losare == GYF_LOSARE
    assert b_gyf.fil == "gyf/efter/gyf.json"
    assert b_gyf.fil_sha256 and len(b_gyf.fil_sha256) == 64
    assert "skiss" in b_gyf.namnare

    b_rikt = k.hamta("gyf.efter.riktmarke", "demo-ekologi", {})
    assert b_rikt.varde == pytest.approx(0.60)
    assert b_rikt.statistik.startswith("parameter (indata)")

    b_eko = k.hamta("gyf.efter.eko_effektiv_area_m2", "demo-ekologi", {})
    assert b_eko.varde == pytest.approx(float(efter_dok["eko_effektiv_area_m2"]))  # type: ignore[arg-type]
    assert b_eko.enhet == "m²"

    b_flag = k.hamta("gyf.efter.flaggad_area_m2", "demo-ekologi", {})
    assert b_flag.varde == pytest.approx(4.0)
    assert b_flag.enhet == "m²"

    b_tot = k.hamta("gyf.efter.total_area_m2", "demo-ekologi", {})
    assert b_tot.varde == pytest.approx(float(efter_dok["tomt_area_m2"]))  # type: ignore[arg-type]
    assert b_tot.varde == pytest.approx(400.0)
    assert b_tot.enhet == "m²"

    b_df = k.hamta("gyf.efter.delfaktor.vegetation_mark.area_m2", "demo-ekologi", {})
    assert b_df.varde == pytest.approx(200.0)
    assert b_df.enhet == "m²"

    b_till = k.hamta("gyf.efter.tillagg.trad_krontackning.area_m2", "demo-ekologi", {})
    assert b_till.varde == pytest.approx(40.0)
    assert b_till.enhet == "m²"

    b_fore = k.hamta("gyf.fore.gyf", "demo-ekologi", {})
    assert b_fore.varde == pytest.approx(float(fore_dok["gyf"]))  # type: ignore[arg-type]
    assert b_fore.jamforbarhet == b_gyf.jamforbarhet

    with pytest.raises(BelaggFel, match="Okänt GYF-mått"):
        k.hamta("gyf.efter.paahittat_matt", "demo-ekologi", {})
    with pytest.raises(BelaggFel, match="Ingen GYF-resultatfil"):
        k.hamta("gyf.saknas.gyf", "demo-ekologi", {})


def test_utci_belagg_fran_riktigt_varmestressresultat(
    m: Miljo, utci_i_demo_dagsljus: tuple[dict[str, object], dict[str, object]]
) -> None:
    fore_dok, efter_dok = utci_i_demo_dagsljus
    k = m.rt.kalla
    st_efter = efter_dok["statistik"]
    assert isinstance(st_efter, dict)
    st_fore = fore_dok["statistik"]
    assert isinstance(st_fore, dict)

    b_medel = k.hamta("utci.nytt_hus.utci_medel_c", "demo-dagsljus", {})
    assert b_medel.varde == pytest.approx(float(st_efter["utci_medel_c"]))
    assert b_medel.enhet == "°C"
    assert b_medel.losare == UTCI_LOSARE
    assert b_medel.fil == "utci/nytt_hus/utci.json"
    assert b_medel.fil_sha256 and len(b_medel.fil_sha256) == 64
    assert "skiss" in b_medel.namnare

    b_p90 = k.hamta("utci.nytt_hus.utci_p90_c", "demo-dagsljus", {})
    assert b_p90.varde == pytest.approx(float(st_efter["utci_p90_c"]))
    assert b_p90.enhet == "°C"

    b_max = k.hamta("utci.nytt_hus.utci_max_c", "demo-dagsljus", {})
    assert b_max.varde == pytest.approx(float(st_efter["utci_max_c"]))
    assert b_max.enhet == "°C"

    b_tmrt = k.hamta("utci.nytt_hus.t_mrt_medel_c", "demo-dagsljus", {})
    assert b_tmrt.varde == pytest.approx(float(st_efter["t_mrt_medel_c"]))
    assert b_tmrt.enhet == "°C"

    b_stark = k.hamta("utci.nytt_hus.andel_stark_varmestress", "demo-dagsljus", {})
    assert b_stark.varde == pytest.approx(float(st_efter["andel_stark_varmestress"]))
    assert b_stark.enhet == "andel"

    b_stark_pct = k.hamta("utci.nytt_hus.andel_stark_varmestress_pct", "demo-dagsljus", {})
    assert b_stark_pct.varde == pytest.approx(float(st_efter["andel_stark_varmestress"]) * 100.0)
    assert b_stark_pct.enhet == "%"

    b_utan = k.hamta("utci.nytt_hus.andel_utanfor_giltighetsomrade", "demo-dagsljus", {})
    assert b_utan.varde == pytest.approx(float(st_efter["andel_utanfor_giltighetsomrade"]))
    assert b_utan.enhet == "andel"

    b_fore = k.hamta("utci.bas.utci_medel_c", "demo-dagsljus", {})
    assert b_fore.varde == pytest.approx(float(st_fore["utci_medel_c"]))
    assert b_fore.jamforbarhet == b_medel.jamforbarhet

    with pytest.raises(BelaggFel, match="Okänt UTCI-mått"):
        k.hamta("utci.nytt_hus.okant_matt", "demo-dagsljus", {})
    with pytest.raises(BelaggFel, match="Ingen UTCI-resultatfil"):
        k.hamta("utci.saknas.utci_medel_c", "demo-dagsljus", {})


def test_gyf_utci_avvisar_sokvagsflykt_glob_och_symlank(
    m: Miljo,
    tmp_path: Path,
    gyf_i_demo_ekologi: tuple[dict[str, object], dict[str, object]],
    utci_i_demo_dagsljus: tuple[dict[str, object], dict[str, object]],
) -> None:
    """Sökvägsflykt, glob-injektion och symlänkar utanför utmapp avvisas alltid med BelaggFel."""
    k = m.rt.kalla

    # Absoluta sökvägar och glob-wildcards avvisas som BelaggFel (aldrig ValueError eller filflykt)
    for adress, kid in (
        ("gyf./tmp/x.gyf", "demo-ekologi"),
        ("gyf.*.gyf", "demo-ekologi"),
        ("utci./tmp/x.utci_medel_c", "demo-dagsljus"),
        ("utci.*.utci_medel_c", "demo-dagsljus"),
    ):
        with pytest.raises(BelaggFel):
            k.hamta(adress, kid, {})

    # Symlänk i utmapp som pekar utanför utmapp avvisas
    extern_fil = tmp_path / "extern_gyf.json"
    extern_fil.write_text(json.dumps(gyf_i_demo_ekologi[0]), encoding="utf-8")
    lank_mapp = k.utmapp("demo-ekologi") / "gyf" / "lankad"
    lank_mapp.mkdir(parents=True, exist_ok=True)
    lank_fil = lank_mapp / "gyf.json"
    lank_fil.symlink_to(extern_fil)
    try:
        with pytest.raises(BelaggFel, match="Ingen GYF-resultatfil"):
            k.hamta("gyf.lankad.gyf", "demo-ekologi", {})
    finally:
        lank_fil.unlink(missing_ok=True)
        if lank_mapp.exists():
            shutil.rmtree(lank_mapp)


# ------------------------------------------------------------------ 2. Rapport, kontroll, tamper, Word & PDF


def test_ekologi_rapport_med_gyf_pastaende_kontroll_tamper_och_export(
    m: Miljo, gyf_i_demo_ekologi: tuple[dict[str, object], dict[str, object]]
) -> None:
    best = m.rt.bestallning_skapa(
        syfte="Skissa påverkan på skogssamband och grönytefaktor i kvartersmark",
        amnen=["ekologi"],
        projekt="demokvarteret",
        scenario={"fore": "fore", "efter": ["efter"]},
    )
    r = m.rt.skapa(projekt="demokvarteret", korningar=["demo-ekologi"], ai_stod=False, bestallning=best)

    # Adresser och metodstatus innehåller GYF (skiss)
    adresser = m.rt.adresser(r)
    assert "gyf.efter.gyf" in adresser
    assert "gyf.efter.total_area_m2" in adresser
    gyf_metod = next(met for met in r.metoder if met["losare"] == GYF_LOSARE)
    assert gyf_metod["status"] == "skiss"

    # Fyll referensflödets EK-frågor
    for kw in ekologi_pastaenden(ekologi_kontekst(m.rt, r), r.fragor):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)

    # Lägg till ett GYF-påstående med platshållare
    p_gyf = m.rt.lagg_till_pastaende(
        r.rapport_id,
        fraga_id="EK-08",
        status="visat",
        fordjupning="ja",
        forfattare="ai",
        belagg=[
            "gyf.fore.gyf",
            "gyf.efter.gyf",
            "gyf.efter.riktmarke",
            "gyf.efter.eko_effektiv_area_m2",
            "gyf.efter.flaggad_area_m2",
            "gyf.efter.total_area_m2",
        ],
        text=(
            "Grönytefaktorn i skissberäkningen ändras från {{matt:gyf.fore.gyf}} till "
            "{{matt:gyf.efter.gyf}} mot riktmärket {{matt:gyf.efter.riktmarke}} "
            "(ekoeffektiv yta {{matt:gyf.efter.eko_effektiv_area_m2}}, flaggad yta "
            "{{matt:gyf.efter.flaggad_area_m2}} av totalt {{matt:gyf.efter.total_area_m2}})."
        ),
    )
    fylld = p_gyf.fylld_text()
    assert "{{" not in fylld and "m²" in fylld

    # Ounderbyggda siffror vägras fortfarande
    with pytest.raises(PastaendeFel, match="belägg"):
        m.rt.lagg_till_pastaende(
            r.rapport_id,
            fraga_id="EK-08",
            status="visat",
            fordjupning="ja",
            forfattare="ai",
            belagg=["gyf.efter.gyf"],
            text="GYF blir {{matt:gyf.efter.gyf}} på 400 m² tomt.",
        )

    # Kontroll passerar
    kontroll = m.rt.kontrollera(r.rapport_id)
    assert kontroll["godkand"], kontroll

    # Word + PDF innehåller valfri GYF-sammanfattningstabell
    word_text = _norm(_text_docx(m.rt.exportera(r.rapport_id, "word")))
    _sidor_n, pdf_sidor = _text_pdf(m.rt.exportera(r.rapport_id, "pdf"))
    pdf_text = _norm(" ".join(pdf_sidor))
    for text in (word_text, pdf_text):
        assert "Grönytefaktor (GYF, skiss)" in text
        assert "Ekoeffektiv yta" in text
        assert "Flaggad yta" in text
        assert GYF_LOSARE in text

    # Manipulerat GYF-värde i rapport.json fångas av kontrollen och stoppar exporten
    m.andra(
        r,
        lambda d: d["pastaenden"][-1]["belagg"][0].__setitem__(
            "varde", d["pastaenden"][-1]["belagg"][0]["varde"] + 0.15
        ),
    )
    kontroll_fel = m.rt.kontrollera(r.rapport_id)
    assert not kontroll_fel["godkand"]
    with pytest.raises(ExportStoppad):
        m.rt.exportera(r.rapport_id, "pdf")


def test_dagsljus_rapport_med_utci_pastaende_kontroll_tamper_och_export(
    m: Miljo, utci_i_demo_dagsljus: tuple[dict[str, object], dict[str, object]]
) -> None:
    best = m.rt.bestallning_skapa(
        syfte="Skissa påverkan på soltimmar, VSC och termiskt mikroklimat (UTCI)",
        amnen=["dagsljus", "sol"],
        projekt="demokvarteret",
        bara_med_underlag=True,
        scenario={"fore": "bas", "efter": ["hojd", "nytt_hus"]},
    )
    r = m.rt.skapa(
        projekt="demokvarteret",
        korningar=["demo-dagsljus"],
        mottagare=[gardens_mottagare()],
        ai_stod=False,
        bestallning=best,
    )

    # Adresser och metodstatus innehåller UTCI (skiss)
    adresser = m.rt.adresser(r)
    assert "utci.nytt_hus.utci_medel_c" in adresser
    assert "utci.nytt_hus.andel_stark_varmestress" in adresser
    utci_metod = next(met for met in r.metoder if met["losare"] == UTCI_LOSARE)
    assert utci_metod["status"] == "skiss"

    # Fyll referensflödets SO/DL-frågor
    for kw in dagsljus_pastaenden(dagsljus_kontekst(m.rt, r), r.fragor):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)

    # Lägg till UTCI-påstående med platshållare (medel och p90 + andel stark värmestress)
    p_utci = m.rt.lagg_till_pastaende(
        r.rapport_id,
        fraga_id="SO-01",
        status="visat",
        fordjupning="ja",
        forfattare="ai",
        belagg=[
            "utci.bas.utci_medel_c",
            "utci.nytt_hus.utci_medel_c",
            "utci.nytt_hus.utci_p90_c",
            "utci.nytt_hus.t_mrt_medel_c",
            "utci.nytt_hus.andel_stark_varmestress_pct",
            "utci.nytt_hus.andel_utanfor_giltighetsomrade_pct",
        ],
        text=(
            "I mikroklimatskissen ändras UTCI i medel från {{matt:utci.bas.utci_medel_c}} till "
            "{{matt:utci.nytt_hus.utci_medel_c}} (övre percentil {{matt:utci.nytt_hus.utci_p90_c}}, "
            "T_mrt i medel {{matt:utci.nytt_hus.t_mrt_medel_c}}). Andelen med minst stark värmestress "
            "är {{matt:utci.nytt_hus.andel_stark_varmestress_pct}} och andelen utanför giltighetsområdet "
            "är {{matt:utci.nytt_hus.andel_utanfor_giltighetsomrade_pct}}."
        ),
    )
    fylld = p_utci.fylld_text()
    assert "{{" not in fylld and "°C" in fylld and "%" in fylld

    # Ounderbyggda siffror vägras
    with pytest.raises(PastaendeFel, match="belägg"):
        m.rt.lagg_till_pastaende(
            r.rapport_id,
            fraga_id="SO-01",
            status="visat",
            fordjupning="ja",
            forfattare="ai",
            belagg=["utci.nytt_hus.utci_medel_c"],
            text="UTCI är {{matt:utci.nytt_hus.utci_medel_c}} vid 27 grader lufttemperatur.",
        )

    # Kontroll passerar
    kontroll = m.rt.kontrollera(r.rapport_id)
    assert kontroll["godkand"], kontroll

    # Word + PDF innehåller valfri UTCI-sammanfattningstabell
    word_text = _norm(_text_docx(m.rt.exportera(r.rapport_id, "word")))
    _sidor_n, pdf_sidor = _text_pdf(m.rt.exportera(r.rapport_id, "pdf"))
    pdf_text = _norm(" ".join(pdf_sidor))
    for text in (word_text, pdf_text):
        assert "Termiskt mikroklimat (UTCI, skiss)" in text
        assert "UTCI medel" in text
        assert "Stark värmestress" in text
        assert UTCI_LOSARE in text

    # Manipulerat UTCI-värde i källfilen fångas via SHA-256 och värdekontroll
    utci_fil = m.rt.kalla.utmapp("demo-dagsljus") / "utci" / "nytt_hus" / "utci.json"
    dok = json.loads(utci_fil.read_text(encoding="utf-8"))
    dok["statistik"]["utci_medel_c"] = float(dok["statistik"]["utci_medel_c"]) + 2.5
    utci_fil.write_text(json.dumps(dok, ensure_ascii=False, indent=2), encoding="utf-8")

    kontroll_fel = m.rt.kontrollera(r.rapport_id)
    assert not kontroll_fel["godkand"]
    with pytest.raises(ExportStoppad):
        m.rt.exportera(r.rapport_id, "pdf")


# ------------------------------------------------------------------ 3. No-op utan GYF/UTCI


def test_befintliga_rapporter_utan_gyf_utci_ar_oforandrade(m: Miljo) -> None:
    best = m.rt.bestallning_skapa(
        syfte="Skissa skogsspridning utan GYF",
        amnen=["ekologi"],
        projekt="demokvarteret",
        scenario={"fore": "fore", "efter": ["efter"]},
    )
    r = m.rt.skapa(projekt="demokvarteret", korningar=["demo-ekologi"], ai_stod=False, bestallning=best)
    assert all(not a.startswith("gyf.") for a in m.rt.adresser(r))
    assert all(met["losare"] != GYF_LOSARE for met in r.metoder)

    for kw in ekologi_pastaenden(ekologi_kontekst(m.rt, r), r.fragor):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)
    r = m.rt.hamta(r.rapport_id)
    dok = bygg_dokument(r, m.rt)
    tabell_rubriker = [
        b.kolumner for b in dok.block if isinstance(b, Tabell) and b.kolumner is not None
    ]
    assert not any("GYF" in k for kol in tabell_rubriker for k in kol)
    assert not any("UTCI medel" in k for kol in tabell_rubriker for k in kol)
    assert m.rt.kontrollera(r.rapport_id)["godkand"]


# ------------------------------------------------------------------ 4. FigurStil("mikroklimat_utci")


def test_figurstil_mikroklimat_utci_renderar_ren_png(tmp_path: Path) -> None:
    assert "mikroklimat_utci" in FIGURSTILAR
    stil = hamta_figurstil("mikroklimat_utci")
    assert stil.doman == "mikroklimat"
    assert stil.enhet == "°C"
    assert len(stil.klasser) == 6
    assert figurstil_for_lager({"roll": "utci", "doman": "mikroklimat"}).namn == "mikroklimat_utci"

    # Klassificering över alla stressband + NaN + byggnadsmask
    test_vals = np.array([[np.nan, 4.0, 20.0, 29.0], [35.0, 42.0, 48.0, 22.0]], dtype=np.float64)
    rgb = klassificera_raster(test_vals, stil)
    assert tuple(rgb[0, 0]) == stil.tomvarde_farg
    assert tuple(rgb[0, 1]) == stil.klasser[0].farg
    assert tuple(rgb[0, 2]) == stil.klasser[1].farg
    assert tuple(rgb[0, 3]) == stil.klasser[2].farg
    assert tuple(rgb[1, 0]) == stil.klasser[3].farg
    assert tuple(rgb[1, 1]) == stil.klasser[4].farg
    assert tuple(rgb[1, 2]) == stil.klasser[5].farg

    # Byggnadsmask ger byggnadskropp och kant
    bygg = np.zeros((40, 50), dtype=bool)
    bygg[10:18, 12:22] = True
    raster = np.linspace(6.0, 49.0, 40 * 50, dtype=np.float64).reshape((40, 50))
    rgb_bygg = klassificera_raster(raster, stil, byggnad_mask=bygg)
    assert tuple(rgb_bygg[14, 16]) == FARG_BYGGNAD
    assert tuple(rgb_bygg[10, 12]) == FARG_BYGGNAD_KANT

    geo = GeoTransformInfo(
        bredd=50,
        hojd=40,
        x_min=152000.0,
        y_max=6578000.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        epsg=3011,
        dtype="float32",
    )
    ut = spara_kartfigur(
        raster,
        "mikroklimat_utci",
        tmp_path / "utci_karta.png",
        titel="Termisk stress (UTCI) – sommarfall",
        undertitel="Scenario: efter · metodstatus skiss",
        metodstatus="Skissunderlag",
        geo=geo,
        byggnad_mask=bygg,
        run_hash="abcdef1234567890",
        kalla="Floden mikroklimat (skiss)",
    )
    assert ut.is_file()
    with Image.open(ut) as img:
        assert img.format == "PNG"
        assert img.mode == "RGB"
        assert img.width >= 600
        assert img.height >= 450
        assert img.getpixel((2, 2)) == FARG_BAKGRUND
