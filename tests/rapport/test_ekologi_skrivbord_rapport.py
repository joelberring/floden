# ruff: noqa: E501
"""Tester för ekologiskt skrivbordsunderlag i rapporter (`ekoskrivbord.*`, EK-01/02/05/06, NVI-checklista,
9/9-kontroll) samt de 2 PDF-putsfixarna (0 tomma PDF-sidor + svensk `dagsljus.svf_mark`-etikett)."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from data_inlasning.artdatabanken import OSL_VARNING
from geografi.ekologi_skrivbord import (
    ARTLISTA_FIL,
    FORMAT_ARTFYND_PUBLIKA,
    FORMAT_SKYDDAD_NATUR,
    SKRIVBORD_SAMMANFATTNING_FIL,
)
from rapport.belaggskalla import BelaggFel
from rapport.ekologibelagg import (
    skrivbord_adresser,
    skrivbord_antaganden,
    skrivbord_pastaendeforslag,
)
from rapport.referensflode_dagsljus import kontekst_fran_rapport as dl_kontekst
from rapport.referensflode_dagsljus import pastaenden as dl_pastaenden
from rapport.referensflode_ekologi import kontekst_fran_rapport as eko_kontekst
from rapport.referensflode_ekologi import pastaenden as eko_pastaenden
from rapport.skissexport import ExportStoppad, skriv_pdf
from rapport.skissmall import (
    Dokument,
    Rubrik,
    Sidbrytning,
    Tabell,
    Titelhuvud,
    bygg_dokument,
    huvudtext,
)
from tests.demovarld.test_demo import Demo
from tests.rapport.test_skissrapport import Miljo, _norm, _text_docx, _text_pdf


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("ekologi_skrivbord_rapport"))


def _skriv_skrivbordsfiler(utmapp: Path) -> Path:
    mapp = utmapp / "ekologi_skrivbord"
    mapp.mkdir(parents=True, exist_ok=True)
    skydd_dok = {
        "format": FORMAT_SKYDDAD_NATUR,
        "epsg": 3011,
        "buffert_m": 1000.0,
        "skyddade_omraden": {
            "antal": 2,
            "antal_overlappar_karna": 1,
            "overlapp_karna_m2": 1250.0,
            "minsta_avstand_m": 0.0,
            "antal_natura2000": 1,
            "per_skyddsform": {"NR": 1, "Natura2000": 1},
        },
        "skogliga_varden": {
            "antal": 3,
            "antal_overlappar_karna": 0,
            "overlapp_karna_m2": 0.0,
            "minsta_avstand_m": 240.0,
            "antal_nyckelbiotoper": 2,
            "antal_sumpskogar": 1,
            "antal_naturvarden": 0,
            "antal_biotopskydd": 0,
            "antal_naturvardsavtal": 0,
            "per_kategori": {"nyckelbiotop": 2, "sumpskog": 1},
        },
        "vmi": {
            "tillganglig": True,
            "antal_polygoner": 1,
            "overlapp_karna_m2": 320.0,
            "minsta_avstand_m": 0.0,
        },
        "licens": "CC0",
    }
    art_dok = {
        "format": FORMAT_ARTFYND_PUBLIKA,
        "epsg": 3011,
        "buffert_m": 1000.0,
        "period_ar": [2016, 2026],
        "antal_publika_fynd": 6,
        "antal_unika_arter": 2,
        "antal_rodlistade_arter": 2,
        "antal_fridlysta_arter": 1,
        "antal_skyddsklassade_filtrerade": 1,
        "osl_begransning": True,
        "osl_varning": OSL_VARNING,
        "arter": [],
    }
    (mapp / SKRIVBORD_SAMMANFATTNING_FIL).write_text(
        json.dumps(skydd_dok, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (mapp / ARTLISTA_FIL).write_text(
        json.dumps(art_dok, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return mapp


@pytest.fixture
def skrivbord_i_demo_ekologi(m: Miljo) -> Iterator[Path]:
    utmapp = m.rt.kalla.utmapp("demo-ekologi")
    mapp = _skriv_skrivbordsfiler(utmapp)
    try:
        yield mapp
    finally:
        if mapp.exists():
            shutil.rmtree(mapp)


def _kropps_text_utan_ram(sida_text: str, sidhuvud_bit: str, sidfot_bit: str) -> str:
    """Tar bort sidhuvud, sidfot och 'Sida X (Y)' för att verifiera att varje PDF-sida har riktigt innehåll."""
    ren = _norm(sida_text)
    ren = re.sub(r"Sida \d+ \(\d+\)", "", ren)
    for bit in (sidhuvud_bit, sidfot_bit):
        if bit:
            ren = ren.replace(_norm(bit), "")
    return ren.strip()


# ------------------------------------------------------------------ 1. Belägg, EK-01/02/05/06 & NVI-checklista


def test_ekoskrivbord_belagg_ek_fragor_nvi_checklista_och_9_av_9_kontroll(
    m: Miljo, skrivbord_i_demo_ekologi: Path
) -> None:
    utmapp = m.rt.kalla.utmapp("demo-ekologi")
    adresser = skrivbord_adresser(utmapp)
    assert "ekoskrivbord.skyddad_natur.antal_skyddade_omraden" in adresser
    assert "ekoskrivbord.artfynd_publika.antal_skyddsklassade_filtrerade" in adresser

    b_skydd = m.rt.kalla.hamta("ekoskrivbord.skyddad_natur.antal_skyddade_omraden", "demo-ekologi", {})
    assert b_skydd.varde == 2.0
    assert b_skydd.fil_sha256 and len(b_skydd.fil_sha256) == 64
    assert b_skydd.losare == "ekologi.konnektivitet"

    b_osl = m.rt.kalla.hamta("ekoskrivbord.artfynd_publika.antal_skyddsklassade_filtrerade", "demo-ekologi", {})
    assert b_osl.varde == 1.0
    assert b_osl.fil_sha256 and len(b_osl.fil_sha256) == 64

    ant = skrivbord_antaganden(utmapp)
    assert OSL_VARNING in ant

    with pytest.raises(BelaggFel, match="Okänt skrivbordsmått"):
        m.rt.kalla.hamta("ekoskrivbord.skyddad_natur.paahittat", "demo-ekologi", {})

    # Skapa ekologirapport där EK-01, EK-02, EK-05 och EK-06 lyfts med skrivbordsbelägg
    best = m.rt.bestallning_skapa(
        syfte="Skissa hur ett nytt hus påverkar ekologi och skyddad natur",
        amnen=["ekologi"],
        projekt="demokvarteret",
        scenario={"fore": "fore", "efter": ["efter"]},
    )
    r = m.rt.skapa(projekt="demokvarteret", korningar=["demo-ekologi"], ai_stod=False, bestallning=best)
    forslag = skrivbord_pastaendeforslag(utmapp)
    assert set(forslag) == {"EK-01", "EK-02", "EK-05", "EK-06"}

    for kw in eko_pastaenden(eko_kontekst(m.rt, r), r.fragor):
        fid = str(kw["fraga_id"])
        vald = forslag.get(fid, kw)
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **vald)

    kontroll = m.rt.kontrollera(r.rapport_id)
    assert kontroll["godkand"], kontroll
    assert len(kontroll["punkter"]) == 9 and all(p["godkand"] for p in kontroll["punkter"])

    # Exportera PDF & Word: verifiera NVI-checklista, OSL-varning, metodstatus skiss och 0 tomma sidor
    pdf_fil = m.rt.exportera(r.rapport_id, "pdf")
    word_fil = m.rt.exportera(r.rapport_id, "word")

    antal_sidor, sidor = _text_pdf(pdf_fil)
    pdf_allt = _norm(" ".join(sidor))
    word_allt = _norm(_text_docx(word_fil))
    for txt in (pdf_allt, word_allt):
        assert "Checklista inför fältinventering (NVI)" in txt
        assert "Skrivbordskontroll av skydd och naturvärden (skiss)" in txt
        assert _norm(OSL_VARNING) in txt
        assert "skiss" in txt.lower()

    # Ingen sida i PDF får vara tom (enbart sidhuvud/sidfot)
    dok = bygg_dokument(m.rt.hamta(r.rapport_id), m.rt)
    assert antal_sidor >= 6
    for idx, sida in enumerate(sidor, start=1):
        assert f"Sida {idx} ({antal_sidor})" in sida
        kropp = _kropps_text_utan_ram(sida, dok.sidhuvud, dok.sidfot)
        assert len(kropp) > 15, f"Tom PDF-sida {idx}/{antal_sidor}: {sida!r}"

    # Manipulering av skrivbordsfilen stoppar exporten med ExportStoppad (SHA-256-spårbarhet!)
    sam_fil = skrivbord_i_demo_ekologi / SKRIVBORD_SAMMANFATTNING_FIL
    d = json.loads(sam_fil.read_text(encoding="utf-8"))
    d["skyddade_omraden"]["antal"] = 99
    sam_fil.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ExportStoppad):
        m.rt.exportera(r.rapport_id, "pdf")


# ------------------------------------------------------------------ 2. PDF-putsfix #1: 0 tomma sidor


def test_pdf_export_eliminerar_tom_trailing_sida_efter_bilaga4(tmp_path: Path) -> None:
    """Verifierar att trailing Spacer / PageBreak / tabell vid sidslut aldrig skapar en tom sista PDF-sida."""
    # Skapa ett dokument med flera tabeller + sidbrytning som annars fyller sidan och trycker avslutande Spacer över kanten
    rader = tuple((f"Kontrollpunkt {i}", "godkänd", str(i)) for i in range(1, 36))
    dok = Dokument(
        titel="Testdokument för sidslut utan tom slutsida",
        sidhuvud="Test · Sidslut",
        sidfot="Skissunderlag – test.",
        titelhuvud=Titelhuvud("Stadsbyggnadskontoret", "Skissunderlag", "27 november 2026"),
        block=[
            Rubrik(1, "Bilaga 4 Kontroll inför export"),
            Tabell(("Kontroll", "Utfall", "Granskat"), rader, (0.60, 0.20, 0.20), liten=True),
            Sidbrytning(),  # explicit trailing sidbrytning ska inte ge en tom extra sida
        ],
    )
    pdf_fil = skriv_pdf(dok, tmp_path / "ut.pdf", tmp_path)
    antal, sidor = _text_pdf(pdf_fil)
    assert antal >= 1
    for i, s in enumerate(sidor, start=1):
        assert f"Sida {i} ({antal})" in s
        kropp = _kropps_text_utan_ram(s, dok.sidhuvud, dok.sidfot)
        assert len(kropp) > 10, f"Sida {i}/{antal} saknar kroppsinnehåll: {s!r}"

    # Testa även ett dokument vars näst sista tabell hamnar exakt vid sidans nederkant
    for antal_rader in range(24, 42):
        dok2 = Dokument(
            titel="Sidkants-svep",
            sidhuvud="Svep",
            sidfot="Sidfot.",
            block=[
                Rubrik(1, "Bilaga 4 Kontroll inför export"),
                Tabell(
                    ("Kontroll", "Utfall", "Granskat"),
                    tuple((f"Punkt {k}", "godkänd", str(k)) for k in range(antal_rader)),
                    (0.60, 0.20, 0.20),
                    liten=True,
                ),
            ],
        )
        ut = skriv_pdf(dok2, tmp_path / f"svep_{antal_rader}.pdf", tmp_path)
        n2, sidor2 = _text_pdf(ut)
        for i, s in enumerate(sidor2, start=1):
            assert f"Sida {i} ({n2})" in s
            kropp = _kropps_text_utan_ram(s, dok2.sidhuvud, dok2.sidfot)
            assert len(kropp) > 10, f"Tom sida vid {antal_rader} rader (sida {i}/{n2})"


# ------------------------------------------------------------------ 3. PDF-putsfix #2: svensk svf_mark-etikett


def test_svf_mark_i_antagandelistan_oversatts_till_svensk_etikett(m: Miljo) -> None:
    """Rått lösar-id `dagsljus.svf_mark` i antagandelistan översätts till 'Himmelsfaktor (SVF) på mark'."""
    best = m.rt.bestallning_skapa(
        syfte="Skissa hur ett nytt hus påverkar sol och dagsljus",
        amnen=["dagsljus"],
        projekt="demokvarteret",
        scenario={"fore": "bas", "efter": ["nytt_hus"]},
    )
    r = m.rt.skapa(projekt="demokvarteret", korningar=["demo-dagsljus"], ai_stod=False, bestallning=best)
    for kw in dl_pastaenden(dl_kontekst(m.rt, r), r.fragor):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)

    # Injicera ett lagrat dagsljus.svf_mark-antagande exakt som i 02_haga_dagsljus_sol_v2
    def lagg_till_svf(d: dict[str, Any]) -> None:
        d["korningar"][0]["antaganden"].append(
            "dagsljus.svf_mark; cellstorlek 2 m, påverkansradie ur högsta hinder 40 m och lägsta solhöjd 5 grader, vegetation utan."
        )

    m.andra(r, lagg_till_svf)
    r_uppd = m.rt.hamta(r.rapport_id)
    dok = bygg_dokument(r_uppd, m.rt)
    huvud = huvudtext(dok)
    assert "Himmelsfaktor (SVF) på mark; cellstorlek 2 m" in huvud
    assert "dagsljus.svf_mark" not in huvud

    # 9/9 kontroll och PDF-export ska fungera rent utan rått lösar-id på sida 2 och utan tomma sidor
    assert m.rt.kontrollera(r.rapport_id)["godkand"]
    pdf_fil = m.rt.exportera(r.rapport_id, "pdf")
    antal, sidor = _text_pdf(pdf_fil)
    assert "Himmelsfaktor (SVF) på mark" in _norm(" ".join(sidor))
    for i, s in enumerate(sidor, start=1):
        assert f"Sida {i} ({antal})" in s
        kropp = _kropps_text_utan_ram(s, dok.sidhuvud, dok.sidfot)
        assert len(kropp) > 10, f"Tom PDF-sida {i}/{antal}"
