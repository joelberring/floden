# ruff: noqa: E501
"""Tester för Etapp 3 (PR-D): planerarvänliga PDF- & Word-rapporter.

Verifierar:
- jämna 20 mm marginaler (170 mm fullbredd, 166 mm figur, 81+4+81 mm figurpar) i både PDF och Word,
- nya blocktyper (`Nyckeltalsrad`, `FigurPar`, `Faktaruta`) i dokumentmodell, PDF och Word,
- grammatikfixar (`ingress` utan dubblering av "för att skissunderlag", `_dagsljusantagande` utan tomt datum),
- ren sida 1 för planerare (läsbart projektnamn utan rå UUID, utan körnings-slugs/repo-sökvägar/Rapport-id),
- kompakt teknisk spårbarhetsbilaga sist samt valfri `separat_bilaga=True` med bibehållen 9/9-kontroll,
- filtrering av misslyckade MCP-retrier (`utfall == "fel"`) ur läsbilagan med obruten hashkedja,
- deterministiska skyfallströsklar (`>= 10/20/30 cm`) och lågpunktsdetektion (`P1`–`P3`),
- avrundade skillnadsklasser (`_bygg_skillnadsstil`) inkl. `< 2 cm`-standardisering,
- kartografiska lager (vägkanter, höjdkurvor, flödesvägar, P1–P3), inzoomad detaljkarta och RH 2000-tvärsektion.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from rapport import amnestext, skissexport
from rapport.belaggskalla import berakna_skyfallstrosklar, detektera_lagpunkter
from rapport.figurer import (
    FIGURSTILAR,
    FigurFel,
    _bygg_skillnadsstil,
    rendera_kartfigur,
    rita_sektionsfigur,
)
from rapport.referensflode import pastaenden
from rapport.skissexport import ExportStoppad
from rapport.skissmall import (
    Faktaruta,
    FigurPar,
    Nyckeltalsrad,
    bygg_bilagedokument,
    bygg_dokument,
    dokumentets_text,
    huvudtext,
)
from rapport.skissrapport import Rapport, _dagsljusantagande, ar_ra_uuid, metodnotis_text
from tests.demovarld.test_demo import Demo
from tests.rapport.test_skissrapport import Miljo, _forvanska_varde, _norm, _text_docx, _text_pdf
from testsvit.k1_io import GeoTransformInfo


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("planerar_rapport"))


def _full(m: Miljo, **over: Any) -> Rapport:
    r = m.ny_rapport(**over)
    for kw in pastaenden(sprakprofil=r.installningar.sprakprofil):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)
    return m.rt.hamta(r.rapport_id)


# ------------------------------------------------------------------ 1. Marginaler & blocktyper


def test_jamna_20mm_marginaler_och_fullbredd_i_pdf_och_word(m: Miljo) -> None:
    import docx

    assert skissexport._UTSTICK_MM == 0.0
    assert skissexport._MARGINAL_V_MM == 20.0
    assert skissexport._MARGINAL_H_MM == 20.0
    assert skissexport._BREDD_MM == 170.0
    assert skissexport._FIGUR_MM == 166.0
    assert skissexport._FIGUR_PAR_BILD_MM == 81.0
    assert skissexport._FIGUR_PAR_GAP_MM == 4.0
    assert (
        skissexport._FIGUR_PAR_BILD_MM * 2 + skissexport._FIGUR_PAR_GAP_MM == skissexport._FIGUR_MM
    )

    r = _full(m)
    word_fil = m.rt.exportera(r.rapport_id, "word")
    pdf_fil = m.rt.exportera(r.rapport_id, "pdf")

    d = docx.Document(str(word_fil))
    sek = d.sections[0]
    assert sek.left_margin is not None and sek.right_margin is not None
    assert abs(sek.left_margin.mm - 20.0) < 0.2
    assert abs(sek.right_margin.mm - 20.0) < 0.2
    assert abs(sek.top_margin.mm - 19.0) < 0.2
    assert abs(sek.bottom_margin.mm - 18.0) < 0.2
    # Inget negativt hängutstick på rubriker
    for niva in (1, 2, 3):
        indent = d.styles[f"Heading {niva}"].paragraph_format.left_indent
        assert indent is None or abs(indent.mm) < 0.05

    dok = bygg_dokument(r, m.rt)
    assert any(isinstance(b, Nyckeltalsrad) for b in dok.block)
    assert any(isinstance(b, FigurPar) for b in dok.block)
    assert any(isinstance(b, Faktaruta) for b in dok.block)
    assert pdf_fil.is_file() and pdf_fil.stat().st_size > 20_000


# ------------------------------------------------------------------ 2. Grammatik & ren sida 1


def test_grammatikfix_ingress_och_dagsljusantagande(m: Miljo) -> None:
    best_subst = m.rt.bestallning_skapa(
        syfte="Skissunderlag skyfall för kvarteret Haga",
        amnen=["skyfall"],
        projekt="demokvarteret",
    )
    r_subst = m.ny_rapport(bestallning=best_subst)
    ing_subst = amnestext.ingress(r_subst, True, False)
    assert "för att skissunderlag" not in ing_subst.lower()
    assert "Beställningen avser:" in ing_subst
    assert "Skissunderlag skyfall för kvarteret Haga" in ing_subst

    best_inf = m.rt.bestallning_skapa(
        syfte="Skissa hur ett nytt hus påverkar skyfall",
        amnen=["skyfall"],
        projekt="demokvarteret",
    )
    r_inf = m.ny_rapport(bestallning=best_inf)
    ing_inf = amnestext.ingress(r_inf, True, False)
    assert "för att skissa hur ett nytt hus påverkar skyfall" in ing_inf

    # Tom datumlista får aldrig ge "Soltimmar på mark för  (tidssteg..."
    ant_tom = _dagsljusantagande(
        {
            "losare": "dagsljus.soltimmar_mark",
            "parametrar": {
                "datum": [],
                "steg_min": 30,
                "azimutriktningar": 36,
                "cellstorlek_m": 2,
                "hojd_max_m": 40,
                "h_min_grader": 5,
                "vegetation": False,
            },
        }
    )
    assert "för (" not in ant_tom and "för  (" not in ant_tom
    assert ant_tom.startswith("Soltimmar på mark (tidssteg 30 min, 36 riktningar);")

    ant_med = _dagsljusantagande(
        {
            "losare": "dagsljus.soltimmar_mark",
            "parametrar": {
                "datum": ["2026-06-21"],
                "steg_min": 30,
                "azimutriktningar": 36,
                "cellstorlek_m": 2,
                "hojd_max_m": 40,
                "h_min_grader": 5,
                "vegetation": True,
            },
        }
    )
    assert "Soltimmar på mark för 21 juni 2026 (tidssteg 30 min" in ant_med


def test_ren_sida1_utan_ra_uuid_eller_utvecklardata_och_kompakt_bilaga_sist(
    m: Miljo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ra_uuid = "a1b2c3d4e5f64789a0123456789abcde"
    assert ar_ra_uuid(ra_uuid)
    assert not ar_ra_uuid("demokvarteret")

    # Isolerad projektrot med projekt.json så RapportTjanst.projektnamn() slår upp mänskligt namn
    from floden_mcp.dataklass import DataKlass, Policy

    monkeypatch.setattr(m.app, "projektmapp", tmp_path)
    monkeypatch.setattr(m.app.fragor, "projektrot", tmp_path)
    monkeypatch.setattr(m.tjanst, "kravs_rapportprojekt", lambda _p: None)
    monkeypatch.setattr(
        m.rt,
        "policy",
        Policy(projektklasser={"demokvarteret": DataKlass.OPPET, ra_uuid: DataKlass.OPPET}),
    )
    proj_mapp = tmp_path / ra_uuid
    proj_mapp.mkdir(parents=True, exist_ok=True)
    (proj_mapp / "projekt.json").write_text(
        json.dumps({"projekt_id": ra_uuid, "namn": "Haga kvarter 4"}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert m.rt.projektnamn(ra_uuid) == "Haga kvarter 4"

    r = _full(m, projekt=ra_uuid)
    assert ra_uuid not in r.titel
    assert "Haga kvarter 4" in r.titel

    pdf_fil = m.rt.exportera(r.rapport_id, "pdf")
    _sidor, texter = _text_pdf(pdf_fil)
    sida1 = _norm(texter[0])
    allt = _norm(" ".join(texter))

    # Sida 1: läsbart projektnamn + KPI-kort + faktaruta, men NOLL teknisk rådata
    assert "Haga kvarter 4" in sida1
    assert ra_uuid not in sida1
    assert r.rapport_id not in sida1
    assert "demo-vatten" not in sida1
    assert "docs/utredningar-stockholm/fragor.json" not in sida1
    assert "Dataklass" not in sida1
    assert "AI:n har valt körningar och skrivit text" not in allt

    # Huvudtexten ska inte innehålla rå AI-jargong
    dok = bygg_dokument(r, m.rt)
    assert "AI:n har valt" not in huvudtext(dok)
    assert "AI:n har valt" not in metodnotis_text({"ai_stod": True, "modeller": ["claude"]}, sbk=True)
    assert "AI-stöd (claude)" in metodnotis_text({"ai_stod": True, "modeller": ["claude"]}, sbk=True)

    # Teknisk bilaga sist innehåller alla spårbarhetsnycklar
    bilaga_sida = next(i for i, t in enumerate(texter) if "Teknisk spårbarhetsbilaga" in t)
    assert bilaga_sida >= 4
    bilaga_text = _norm(" ".join(texter[bilaga_sida:]))
    assert r.rapport_id in bilaga_text
    assert ra_uuid in bilaga_text
    assert "demo-vatten" in bilaga_text
    assert "docs/utredningar-stockholm/fragor.json" in bilaga_text
    assert "Bilaga 1 Belägg" in bilaga_text
    assert "Bilaga 4 Kontroll inför export" in bilaga_text


# ------------------------------------------------------------------ 3. Separat bilaga & MCP-filtrering


def test_separat_bilaga_exporterar_tva_filer_med_godkand_kontroll(m: Miljo) -> None:
    r = _full(m, installningar={"separat_bilaga": True})
    k = m.rt.kontrollera(r.rapport_id)
    assert k["godkand"], k

    pdf_huvud = m.rt.exportera(r.rapport_id, "pdf")
    word_huvud = m.rt.exportera(r.rapport_id, "word")
    pdf_bilaga = pdf_huvud.with_name(f"{r.rapport_id}_bilaga.pdf")
    word_bilaga = word_huvud.with_name(f"{r.rapport_id}_bilaga.docx")

    assert pdf_bilaga.is_file() and pdf_bilaga.stat().st_size > 10_000
    assert word_bilaga.is_file() and word_bilaga.stat().st_size > 10_000

    _, huvud_sidor = _text_pdf(pdf_huvud)
    _, bilaga_sidor = _text_pdf(pdf_bilaga)
    huvud_t = _norm(" ".join(huvud_sidor))
    bilaga_t = _norm(" ".join(bilaga_sidor))

    assert "Bilaga 1 Belägg" not in huvud_t
    assert "Bilaga 1 Belägg" in bilaga_t
    assert "Bilaga 4 Kontroll inför export" in bilaga_t
    assert r.rapport_id in bilaga_t
    assert "Teknisk spårbarhetsbilaga" in _norm(_text_docx(word_bilaga))
    assert "Bilaga 1 Belägg" in dokumentets_text(bygg_bilagedokument(m.rt.hamta(r.rapport_id), m.rt))


def test_mcp_misslyckade_retrier_filtreras_ur_lasbilagan_med_obruten_hashkedja(m: Miljo) -> None:
    r = _full(m)
    assert m.rt.kontrollera(r.rapport_id)["godkand"]
    r = m.rt.hamta(r.rapport_id)
    rader = [
        {
            "tid": "2026-11-27T10:00:00+00:00",
            "klient": {"namn": "mcp-klient"},
            "modell": "testmodell",
            "roll": "planhandlaggare",
            "verktyg": "pastaende_lagg_till",
            "utfall": "fel",
            "hash": "deadbeef0001",
        },
        {
            "tid": "2026-11-27T10:00:05+00:00",
            "klient": {"namn": "mcp-klient"},
            "modell": "testmodell",
            "roll": "planhandlaggare",
            "verktyg": "pastaende_lagg_till",
            "utfall": "ok",
            "hash": "cafe12340002",
        },
    ]
    dok = bygg_dokument(r, m.rt, korlogg_rader=rader)
    text = dokumentets_text(dok)
    assert "cafe123400" in text
    assert "deadbeef00" not in text
    assert "Misslyckade anrop/retrier utelämnade ur läsbilagan: 1 st" in text
    assert "obrutna hashkedjan" in text


# ------------------------------------------------------------------ 4. Deterministiska skyfallsmått & P1–P3


def test_skyfallstrosklar_och_lagpunkter_har_kant_svar() -> None:
    # 50×50 rutnät med 2 m celler (100×100 m), cellarea = 4 m²
    djup = np.zeros((50, 50), dtype=np.float64)
    tak = np.zeros((50, 50), dtype=bool)
    mark = np.full((50, 50), 10.0, dtype=np.float64)

    # Lågpunkt 1 vid (r=10, c=10): 0,45 m (1 cell) + 4 grannar 0,25 m + 4 grannar 0,12 m
    djup[10, 10] = 0.45
    djup[9, 10] = djup[11, 10] = djup[10, 9] = djup[10, 11] = 0.25
    djup[9, 9] = djup[9, 11] = djup[11, 9] = djup[11, 11] = 0.12
    # Lågpunkt 2 vid (r=12, c=12) — bara ~5,7 m bort från P1 -> ska undertryckas (< 30 m)!
    djup[12, 12] = 0.38
    # Lågpunkt 3 vid (r=35, c=35) — 70,7 m bort -> ska detekteras som P2!
    djup[35, 35] = 0.32
    mark[35, 35] = 11.2
    # Lågpunkt 4 vid (r=40, c=10) — bara 0,14 m (< 0,15 m) -> ska INTE bli P3!
    djup[40, 10] = 0.14
    # Takcell med högt skräpvärde -> ska maskas bort ur både tröskelytor och lågpunkter!
    tak[25, 25] = True
    djup[25, 25] = 1.80

    tr = berakna_skyfallstrosklar(djup, cellstorlek_m=2.0, tak_mask=tak)
    # >= 0.30 m: (10,10), (12,12), (35,35) = 3 celler = 12 m² (takcellen 25,25 räknas EJ!)
    assert tr["yta_over_0_3m_m2"] == pytest.approx(12.0)
    # >= 0.20 m: 3 + 4 = 7 celler = 28 m²
    assert tr["yta_over_0_2m_m2"] == pytest.approx(28.0)
    # >= 0.10 m: 7 + 4 + 1 (40,10) = 12 celler = 48 m²
    assert tr["yta_over_0_1m_m2"] == pytest.approx(48.0)
    assert tr["max_djup_m"] == pytest.approx(0.45)

    geo = GeoTransformInfo(
        bredd=50,
        hojd=50,
        x_min=150000.0,
        y_max=6580000.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        epsg=3011,
        dtype="float32",
    )
    lps = detektera_lagpunkter(
        djup,
        geo,
        tak_mask=tak,
        mark_z=mark,
    )
    assert [lp.id for lp in lps] == ["P1", "P2"]
    assert lps[0].djup_m == pytest.approx(0.45)
    assert lps[0].markhojd_rh2000_m == pytest.approx(10.0)
    assert lps[0].vattenniva_rh2000_m == pytest.approx(10.45)
    assert lps[1].djup_m == pytest.approx(0.32)
    assert lps[1].markhojd_rh2000_m == pytest.approx(11.2)
    assert np.hypot(lps[0].e_m - lps[1].e_m, lps[0].n_m - lps[1].n_m) >= 30.0


def test_skyfallstrosklar_finns_som_verifierade_belagg_i_demo_vatten(m: Miljo) -> None:
    r = m.ny_rapport()
    adr = set(m.rt.adresser(r))
    for namn in ("yta_over_0_1m_m2", "yta_over_0_2m_m2", "yta_over_0_3m_m2"):
        full = f"matvarde.skyfall.efter.{namn}"
        assert full in adr
        b = m.rt.kalla.hamta(full, "demo-vatten", {})
        assert b.enhet == "m²" and b.varde >= 0 and b.fil_sha256
    b10 = m.rt.kalla.hamta("matvarde.skyfall.efter.yta_over_0_1m_m2", "demo-vatten", {}).varde
    b20 = m.rt.kalla.hamta("matvarde.skyfall.efter.yta_over_0_2m_m2", "demo-vatten", {}).varde
    b30 = m.rt.kalla.hamta("matvarde.skyfall.efter.yta_over_0_3m_m2", "demo-vatten", {}).varde
    assert b10 >= b20 >= b30 > 0


# ------------------------------------------------------------------ 5. Kartografi, skillnadsklasser & tvärsektion


def test_rundade_skillnadsklasser_och_liten_forandring_under_2cm() -> None:
    # Små skillnader (< 2 cm) får standardiserade klasser och tydlig förklaring — aldrig −0,00738!
    stil_liten = _bygg_skillnadsstil(
        roll="max_djup",
        doman="vatten",
        storhet="Skillnad i största vattendjup",
        enhet="m",
        max_abs=0.00738,
        troskel=0.00474,
    )
    etiketter_liten = [k.etikett for k in stil_liten.klasser]
    assert not any(re.search(r",\d{3,}", e) for e in etiketter_liten), etiketter_liten
    assert any("Ingen förändring ≥ 2 cm" in e for e in etiketter_liten), etiketter_liten
    assert any("+0,02" in e and "+0,05" in e for e in etiketter_liten)
    assert any("+0,15" in e for e in etiketter_liten)

    # Normal skyfallsskillnad (~0,16 m): prydliga runda gränser
    stil_norm = _bygg_skillnadsstil(
        roll="max_djup",
        doman="vatten",
        storhet="Skillnad i största vattendjup",
        enhet="m",
        max_abs=0.159178,
        troskel=0.01,
    )
    etiketter_norm = [k.etikett for k in stil_norm.klasser]
    assert not any(re.search(r",\d{3,}", e) for e in etiketter_norm), etiketter_norm
    assert any("−0,02 – +0,02 m" in e for e in etiketter_norm), etiketter_norm

    # Soltimmar: rundade timsteg (0,5 / 1 / 2 / 4)
    stil_sol = _bygg_skillnadsstil(
        roll="soltimmar",
        doman="dagsljus",
        storhet="Skillnad i soltimmar",
        enhet="timmar",
        max_abs=3.42,
        troskel=0.25,
    )
    etiketter_sol = [k.etikett for k in stil_sol.klasser]
    assert not any(re.search(r",\d{2,}", e) for e in etiketter_sol), etiketter_sol


def test_kartografiska_lager_och_tvarsektion_renderar_deterministiskt() -> None:
    yy, xx = np.mgrid[0:60, 0:60]
    mark = 12.0 + 0.03 * xx + 0.02 * yy
    djup = np.where((xx - 30) ** 2 + (yy - 30) ** 2 < 64, 0.38, 0.0)
    geo = GeoTransformInfo(
        bredd=60,
        hojd=60,
        x_min=151000.0,
        y_max=6580120.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        epsg=3011,
        dtype="float32",
    )
    img1 = rendera_kartfigur(
        djup,
        FIGURSTILAR["skyfall_djup"],
        titel="Provkarta",
        geo=geo,
        baskarta_linjer_3011=[[(151010.0, 6580100.0), (151110.0, 6580020.0)]],
        markhojd_m=mark,
        hojdkurvor_ekvidistans_m=1.0,
        flodesvagar_3011=[[(151020.0, 6580110.0), (151060.0, 6580060.0)]],
        markorer_3011=[{"id": "P1", "e_m": 151060.0, "n_m": 6580060.0}],
    )
    img2 = rendera_kartfigur(
        djup,
        FIGURSTILAR["skyfall_djup"],
        titel="Provkarta",
        geo=geo,
        baskarta_linjer_3011=[[(151010.0, 6580100.0), (151110.0, 6580020.0)]],
        markhojd_m=mark,
        hojdkurvor_ekvidistans_m=1.0,
        flodesvagar_3011=[[(151020.0, 6580110.0), (151060.0, 6580060.0)]],
        markorer_3011=[{"id": "P1", "e_m": 151060.0, "n_m": 6580060.0}],
    )
    assert img1.size == img2.size
    assert img1.tobytes() == img2.tobytes()

    avst = np.linspace(0.0, 100.0, 101)
    m_lin = 13.5 - 0.6 * np.exp(-(((avst - 35.0) / 14.0) ** 2))
    vf = np.maximum(0.0, 13.15 - m_lin)
    ve = np.maximum(0.0, 13.30 - m_lin)
    bmask = (avst >= 65.0) & (avst <= 82.0)
    bhojd = np.where(bmask, 22.0, np.nan)
    sek1 = rita_sektionsfigur(
        avst,
        m_lin,
        vatten_fore_m=vf,
        vatten_efter_m=ve,
        byggnad_mask=bmask,
        byggnad_hojd_rh2000_m=bhojd,
        byggnad_ny_mask=bmask,
        markor_avstand_m=35.0,
        markor_etikett="P1",
    )
    sek2 = rita_sektionsfigur(
        avst,
        m_lin,
        vatten_fore_m=vf,
        vatten_efter_m=ve,
        byggnad_mask=bmask,
        byggnad_hojd_rh2000_m=bhojd,
        byggnad_ny_mask=bmask,
        markor_avstand_m=35.0,
        markor_etikett="P1",
    )
    assert sek1.size == (1120, 440)
    assert sek1.tobytes() == sek2.tobytes()

    with pytest.raises(FigurFel, match="1D-vektorer"):
        rita_sektionsfigur([0.0], [10.0])


def test_detaljkarta_och_tvarsektion_pa_demo_och_tamper_stoppas(m: Miljo) -> None:
    r = _full(m)
    # K33/v1-kontraktet för r.kartor förblir oförändrat (3 kartor)
    assert len(r.kartor) == 3

    pf = m.rt.planerarfigurer(r, skriv_filer=True)
    assert len(pf.detaljkartor) == 1
    assert len(pf.sektioner) == 1
    assert len(pf.lagpunkter) >= 1 and pf.lagpunkter[0].id == "P1"
    assert pf.trosklar_fore is not None and pf.trosklar_efter is not None
    assert pf.trosklar_efter.yta_over_0_2m_m2 > pf.trosklar_fore.yta_over_0_2m_m2

    # Inplanterat fel i belägg stoppar fortfarande exporten 100 %
    m.andra(r, _forvanska_varde)
    with pytest.raises(ExportStoppad):
        m.rt.exportera(r.rapport_id, "pdf")


def test_troskeltabell_summerar_exakt_negativ_rh2000_och_deduplicerad_fot(
    m: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rapport.belaggskalla import Lagpunkt, SkyfallsTroskelytor
    from rapport.figurer import _sanera_fot_kalla
    from rapport.skissmall import Tabell
    from rapport.skissrapport import PlanerarFigurer

    # 1. Tröskeltabellen på demo-vatten ska summera exakt per rad (Före + Skillnad == Efter)
    r = _full(m)
    dok = bygg_dokument(r, m.rt)
    tr_tab = next(
        b
        for b in dok.block
        if isinstance(b, Tabell) and b.kolumner and b.kolumner[0].startswith("Djupklass")
    )

    def _tolka_m2(s: str) -> int:
        ren = s.replace("\u202f", "").replace("\u00a0", "").replace(" ", "").replace("m²", "")
        ren = ren.replace("−", "-")
        return int(ren)

    for rad in tr_tab.rader:
        fore_v, efter_v, skill_v = _tolka_m2(rad[1]), _tolka_m2(rad[2]), _tolka_m2(rad[3])
        assert fore_v + skill_v == efter_v, rad

    # 2. Negativ RH 2000-höjd får aldrig ge "+−0,3 m" utan "−0,32 m" / "−0,12 m" (2 decimaler)
    kust_pf = PlanerarFigurer(
        trosklar_fore=SkyfallsTroskelytor("fore", 0.30, 654.0, 315.0, 104.0, 1.0, "f", None),
        trosklar_efter=SkyfallsTroskelytor("efter", 0.44, 1374.0, 539.0, 267.0, 1.0, "e", None),
        lagpunkter=(
            Lagpunkt(
                id="P1",
                e_m=151256.0,
                n_m=6580258.0,
                djup_m=0.20,
                rad=10,
                kol=10,
                markhojd_rh2000_m=-0.32,
                vattenniva_rh2000_m=-0.12,
            ),
            Lagpunkt(
                id="P2",
                e_m=151296.0,
                n_m=6580220.0,
                djup_m=0.16,
                rad=20,
                kol=20,
                markhojd_rh2000_m=13.08,
                vattenniva_rh2000_m=13.24,
            ),
        ),
    )
    monkeypatch.setattr(m.rt, "planerarfigurer", lambda _r, skriv_filer=True: kust_pf)
    dok_kust = bygg_dokument(r, m.rt)
    kust_text = dokumentets_text(dok_kust)
    assert "+−" not in kust_text and "+-" not in kust_text
    lp_tab = next(
        b for b in dok_kust.block if isinstance(b, Tabell) and b.kolumner and b.kolumner[0] == "Punkt"
    )
    assert lp_tab.rader[0][2] == "−0,32\u00a0m"
    assert lp_tab.rader[0][3] == "−0,12\u00a0m"
    assert lp_tab.rader[1][2] == "+13,08\u00a0m"
    assert lp_tab.rader[1][3] == "+13,24\u00a0m"

    # 3. Figurfot skyddad mot dubblerat SWEREF 99 18 00 / RH 2000
    assert _sanera_fot_kalla("Floden · SWEREF 99 18 00", "SWEREF 99 18 00 · RH 2000") == "Floden"
    assert (
        _sanera_fot_kalla(
            "Floden · RH 2000", "SWEREF 99 18 00 · RH 2000 · Vertikal överförhöjning: 11,9×"
        )
        == "Floden"
    )
    assert _sanera_fot_kalla("Floden", "SWEREF 99 18 00 · RH 2000") == "Floden"

