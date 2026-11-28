"""Export av skissunderlaget till Word (.docx) och PDF (R1/Etapp 3).

Båda formaten skrivs ur samma block (`rapport.skissmall`). Exporten görs bara efter en godkänd
kontroll (`RapportTjanst.exportera`); ett fel stoppar med `ExportStoppad`.

Layouten utnyttjar full sidbredd med jämna 20 mm-marginaler (170 mm spaltbredd, 166 mm kartbredd,
81 + 4 + 81 mm för kartpar sida-vid-sida), KPI-kort (`Nyckeltalsrad`), tonad faktaruta
(`Faktaruta` med #F7F5EE-bakgrund och 3 pt skogsgrön vänsterlinje #2F4A2C) samt `KeepTogether`
för rubriker, figurer och bildtexter. Språket är satt till svenska i båda formaten.

- **Word:** python-docx (ren Python). Rubrikerna använder Words inbyggda format (Heading 1–3).
- **PDF:** reportlab (ren Python, inga systemberoenden). Inbäddade typsnitt Gelasio (brödtext)
  och Liberation Sans (rubriker, tabeller, kort), med `invariant=1` för byte-identiska filer.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Final
from xml.sax.saxutils import escape

from rapport.skissmall import (
    Dokument,
    Faktaruta,
    Figur,
    FigurPar,
    Nyckeltalsrad,
    Punktlista,
    Rubrik,
    Sidbrytning,
    Stycke,
    Tabell,
    Typsnitt,
)

LJUSFARG: Final[dict[str, tuple[str, str]]] = {
    "nej": ("2E7D32", "Grön"),
    "ja": ("E0A800", "Gul"),
    "kan_inte_avgoras": ("C62828", "Röd"),
}
LJUSTEXT: Final[dict[str, str]] = {
    "nej": "Ingen fördjupning",
    "ja": "Behöver fördjupas",
    "kan_inte_avgoras": "Kan inte avgöras",
}
_TEXTFARG = "1D241C"
_SVAG = "4A5447"
_LINJE = "D6D2C4"
_HUVUD_BG = "EEF0EA"
_ZEBRA_BG = "FAF9F5"
_FAKTA_BG = "F7F5EE"
_SKOGSGRON = "2F4A2C"

# Jämna 20 mm-marginaler (A4 = 210 × 297 mm -> 170 mm full spaltbredd i både PDF och Word).
_MARGINAL_V_MM = 20.0
_MARGINAL_H_MM = 20.0
_UTSTICK_MM = 0.0
_HEL_MM = 210.0 - _MARGINAL_V_MM - _MARGINAL_H_MM  # 170.0 mm
_BREDD_MM = _HEL_MM - _UTSTICK_MM  # 170.0 mm
_RUBRIKFARG = "1D241C"
_FIGUR_MM = 166.0
_FIGUR_PAR_BILD_MM = 81.0
_FIGUR_PAR_GAP_MM = 4.0  # 81 + 4 + 81 = 166 mm
_FIGUR_MAX_HOJD_MM = 148.0
_FIGUR_PAR_MAX_HOJD_MM = 112.0


class ExportStoppad(Exception):
    """Kontrollen inför export underkände rapporten; ingen fil skrevs."""

    def __init__(self, kontroll: dict[str, Any]) -> None:
        from rapport.skisskontroll import kontroll_text

        self.kontroll = kontroll
        self.fel = [f for p in kontroll.get("punkter", []) for f in p.get("fel", [])]
        super().__init__("Exporten stoppades.\n" + kontroll_text(kontroll))


class TypsnittFel(Exception):
    """Ett lokalt typsnitt kan inte bäddas in i PDF:en (meddelandet har ingen sökväg)."""


def _ljustext(dok: Dokument, nyckel: str) -> str:
    return dok.utseende.ljus.get(nyckel) or f"{LJUSFARG[nyckel][1]}: {LJUSTEXT[nyckel]}"


def typsnittsmetadata(dok: Dokument) -> str:
    """Nyckelord om inbäddade lokala typsnitt (tom sträng när förvalet används)."""
    delar = []
    for roll, t in (
        ("brödtext", dok.utseende.typsnitt_brodtext),
        ("rubriker", dok.utseende.typsnitt_rubriker),
    ):
        if t is not None:
            delar.append(f"Typsnitt {roll}: {t.familj} (sha256 {t.sha256})")
    return "; ".join(delar)


# ---------------------------------------------------------------------- Word


def skriv_word(dok: Dokument, ut: str | Path, rapportmapp: str | Path) -> Path:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    us = dok.utseende
    brod = us.typsnitt_brodtext.familj if us.typsnitt_brodtext else "Georgia"
    rubr = (
        us.typsnitt_rubriker.familj
        if us.typsnitt_rubriker
        else (brod if us.typsnitt_brodtext else "Arial")
    )
    sans = rubr
    huvud_accent = _SKOGSGRON if us.accent == "000000" else us.accent

    d = Document()
    sek = d.sections[0]
    sek.orientation = WD_ORIENT.PORTRAIT
    sek.page_width, sek.page_height = Mm(210), Mm(297)
    sek.left_margin = Mm(_MARGINAL_V_MM)
    sek.right_margin = Mm(_MARGINAL_H_MM)
    sek.top_margin, sek.bottom_margin = Mm(19), Mm(18)
    sek.header_distance, sek.footer_distance = Mm(9), Mm(9)

    def farg(h: str) -> RGBColor:
        return RGBColor.from_string(h)

    def satt_typsnitt(element: Any, namn: str) -> None:
        rpr = element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            rfonts.attrib.pop(qn(attr), None)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), namn)

    def satt_sprak(rpr: Any) -> None:
        lang = rpr.find(qn("w:lang"))
        if lang is None:
            lang = OxmlElement("w:lang")
            rpr.append(lang)
        lang.set(qn("w:val"), us.sprak)
        lang.set(qn("w:eastAsia"), us.sprak)
        lang.set(qn("w:bidi"), us.sprak)

    stilar = d.styles.element
    rpr_default = stilar.find(qn("w:docDefaults"))
    if rpr_default is not None:
        rpr = rpr_default.find(qn("w:rPrDefault"))
        if rpr is not None:
            inre = rpr.find(qn("w:rPr"))
            if inre is not None:
                satt_sprak(inre)

    normal = d.styles["Normal"]
    normal.font.name = brod
    normal.font.size = Pt(10)
    normal.font.color.rgb = farg(_TEXTFARG)
    satt_typsnitt(normal.element, brod)
    satt_sprak(normal.element.get_or_add_rPr())
    w14 = "http://schemas.microsoft.com/office/word/2010/wordml"
    numform = normal.element.get_or_add_rPr().makeelement(f"{{{w14}}}numForm", {})
    numform.set(f"{{{w14}}}val", "lining")
    normal.element.get_or_add_rPr().append(numform)
    normal.paragraph_format.space_after = Pt(5.5)
    normal.paragraph_format.line_spacing = 1.22

    for niva, storlek, fore in ((1, 14.5, 16), (2, 11.5, 12), (3, 10.2, 8)):
        st = d.styles[f"Heading {niva}"]
        st.font.name = rubr
        st.font.size = Pt(storlek)
        st.font.bold = True
        st.font.italic = False
        st.font.color.rgb = farg(_RUBRIKFARG)
        st.paragraph_format.left_indent = Mm(0)
        st.paragraph_format.space_before = Pt(fore)
        st.paragraph_format.space_after = Pt(4 if niva == 1 else 2.5)
        st.paragraph_format.keep_with_next = True
        satt_typsnitt(st.element, rubr)

    titel_st = d.styles["Title"]
    titel_st.font.name = rubr
    titel_st.font.size = Pt(20)
    titel_st.font.bold = True
    titel_st.font.color.rgb = farg(_RUBRIKFARG)
    titel_st.paragraph_format.left_indent = Mm(0)
    satt_typsnitt(titel_st.element, rubr)
    ppr = titel_st.element.find(qn("w:pPr"))
    if ppr is not None:
        for b in ppr.findall(qn("w:pBdr")):
            ppr.remove(b)

    def sidfalt(p: Any, kod: str, storlek: Any) -> None:
        for typ, text in (
            ("begin", None),
            (None, kod),
            ("separate", None),
            (None, "1"),
            ("end", None),
        ):
            r = p.add_run()
            r.font.size = storlek
            r.font.color.rgb = farg(_SVAG)
            if typ:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), typ)
                r._r.append(el)
            elif text == "1":
                r.text = "1"
            else:
                it = OxmlElement("w:instrText")
                it.set(qn("xml:space"), "preserve")
                it.text = f" {text} "
                r._r.append(it)

    def sans_run(r: Any) -> Any:
        satt_typsnitt(r._r, sans)
        return r

    def liten_run(p: Any, text: str) -> None:
        r = sans_run(p.add_run(text))
        r.font.size = Pt(8)
        r.font.color.rgb = farg(_SVAG)

    hp = sek.header.paragraphs[0]
    hp.paragraph_format.left_indent = Mm(0)
    liten_run(hp, dok.sidhuvud)
    sek.different_first_page_header_footer = dok.titelhuvud is not None
    for sidfot in (sek.footer, sek.first_page_footer):
        fp = sidfot.paragraphs[0]
        fp.paragraph_format.tab_stops.add_tab_stop(Mm(_BREDD_MM), WD_TAB_ALIGNMENT.RIGHT)
        liten_run(fp, dok.sidfot + "\t")
        for del_ in re.split(r"(\{sida\}|\{antal\})", us.sidnummer):
            if del_ == "{sida}":
                sidfalt(fp, "PAGE", Pt(8))
            elif del_ == "{antal}":
                sidfalt(fp, "NUMPAGES", Pt(8))
            elif del_:
                liten_run(fp, del_)

    d.core_properties.title = dok.titel
    d.core_properties.author = (dok.titelhuvud.avsandare if dok.titelhuvud else "") or "Floden"
    d.core_properties.comments = dok.sidfot
    d.core_properties.language = us.sprak
    d.core_properties.keywords = typsnittsmetadata(dok)

    def skugga(cell: Any, hexa: str) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hexa)
        tc_pr.append(shd)

    def cell_kanter(cell: Any, kanter_map: dict[str, tuple[str, str, str]]) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_bdr = OxmlElement("w:tcBorders")
        for sida, (val, sz, col) in kanter_map.items():
            el = OxmlElement(f"w:{sida}")
            el.set(qn("w:val"), val)
            if val != "nil":
                el.set(qn("w:sz"), sz)
                el.set(qn("w:space"), "0")
                el.set(qn("w:color"), col)
            tc_bdr.append(el)
        tc_pr.append(tc_bdr)

    def kanter(tabell: Any, synliga: bool = True) -> None:
        tbl_pr = tabell._tbl.tblPr
        b = OxmlElement("w:tblBorders")
        for kant in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{kant}")
            if synliga and kant not in ("left", "right", "insideV"):
                el.set(qn("w:val"), "single")
                el.set(qn("w:sz"), "4")
                el.set(qn("w:space"), "0")
                el.set(qn("w:color"), _LINJE)
            else:
                el.set(qn("w:val"), "nil")
            b.append(el)
        tbl_pr.append(b)

    def fast_layout(tab: Any, marginal: bool = True, pad_v: int = 45, pad_h: int = 85) -> None:
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tab._tbl.tblPr.append(layout)
        if marginal:
            mar = OxmlElement("w:tblCellMar")
            for kant, v in (("top", pad_v), ("left", pad_h), ("bottom", pad_v), ("right", pad_h)):
                el = OxmlElement(f"w:{kant}")
                el.set(qn("w:w"), str(v))
                el.set(qn("w:type"), "dxa")
                mar.append(el)
            tab._tbl.tblPr.append(mar)

    def rad_hel(rad: Any, rubrikrad: bool) -> None:
        tr_pr = rad._tr.get_or_add_trPr()
        tr_pr.append(OxmlElement("w:cantSplit"))
        if rubrikrad:
            tr_pr.append(OxmlElement("w:tblHeader"))

    def linje_under(p: Any, farg_hex: str) -> None:
        ppr_el = p._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        el = OxmlElement("w:bottom")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "10")
        el.set(qn("w:space"), "5")
        el.set(qn("w:color"), farg_hex)
        bdr.append(el)
        ppr_el.append(bdr)

    # Titelhuvud i full 170 mm bredd
    th = dok.titelhuvud
    if th is not None:
        tab = d.add_table(rows=1, cols=2)
        tab.alignment = WD_TABLE_ALIGNMENT.LEFT
        tab.autofit = False
        kanter(tab, synliga=False)
        fast_layout(tab, marginal=False)
        for j, b_and in enumerate((0.58, 0.42)):
            tab.columns[j].width = Mm(_HEL_MM * b_and)
            tab.cell(0, j).width = Mm(_HEL_MM * b_and)
        v = tab.cell(0, 0).paragraphs[0]
        v.paragraph_format.space_after = Pt(0)
        if us.logotyp is not None:
            v.add_run().add_picture(str(us.logotyp), height=Mm(14))
            if th.avsandare:
                v = tab.cell(0, 0).add_paragraph()
                v.paragraph_format.space_after = Pt(0)
        if th.avsandare:
            r = sans_run(v.add_run(th.avsandare))
            r.bold = True
            r.font.size = Pt(11)
        h = tab.cell(0, 1).paragraphs[0]
        h.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        h.paragraph_format.space_after = Pt(1)
        r = sans_run(h.add_run(th.dokumenttyp))
        r.bold = True
        r.font.size = Pt(10)
        r.font.color.rgb = farg(huvud_accent)
        for etikett, varde in th.rader():
            h = tab.cell(0, 1).add_paragraph()
            h.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            h.paragraph_format.space_after = Pt(0)
            liten_run(h, f"{etikett} ")
            rv = sans_run(h.add_run(varde))
            rv.font.size = Pt(8.5)
        sep = d.add_paragraph()
        sep.paragraph_format.space_after = Pt(10)
        linje_under(sep, huvud_accent)

    t = d.add_paragraph(dok.titel, style="Title")
    t.paragraph_format.space_after = Pt(8)

    forra: Any = None
    for b in dok.block:
        if isinstance(b, Rubrik):
            d.add_heading(b.text, level=min(b.niva, 3))
        elif isinstance(b, Stycke):
            if b.stil == "notis" and forra is not None:
                forra.paragraph_format.keep_with_next = True
            p = d.add_paragraph()
            forra = p
            run = p.add_run(b.text)
            if b.stil == "ingress":
                run.font.size = Pt(11)
                p.paragraph_format.space_after = Pt(10)
            elif b.stil in ("liten", "notis"):
                sans_run(run)
                run.font.size = Pt(8.5)
                run.font.color.rgb = farg(_SVAG)
                if b.stil == "notis":
                    p.paragraph_format.space_after = Pt(8)
        elif isinstance(b, Punktlista):
            for punkt in b.punkter:
                p = d.add_paragraph(style="List Bullet")
                p.add_run(punkt)
                p.paragraph_format.space_after = Pt(2.5)
        elif isinstance(b, Nyckeltalsrad):
            n_kort = len(b.kort)
            if n_kort > 0:
                gap_mm = 3.0
                kort_mm = (_BREDD_MM - gap_mm * (n_kort - 1)) / n_kort
                ncols = 2 * n_kort - 1
                tab = d.add_table(rows=1, cols=ncols)
                tab.alignment = WD_TABLE_ALIGNMENT.LEFT
                tab.autofit = False
                kanter(tab, synliga=False)
                fast_layout(tab, marginal=True, pad_v=85, pad_h=95)
                rad_hel(tab.rows[0], False)
                for idx, kort in enumerate(b.kort):
                    col_i = idx * 2
                    cell = tab.cell(0, col_i)
                    tab.columns[col_i].width = Mm(kort_mm)
                    cell.width = Mm(kort_mm)
                    skugga(cell, _FAKTA_BG)
                    top_col = kort.accent or _SKOGSGRON
                    cell_kanter(
                        cell,
                        {
                            "top": ("single", "18", top_col),
                            "bottom": ("single", "4", _LINJE),
                            "left": ("single", "4", _LINJE),
                            "right": ("single", "4", _LINJE),
                        },
                    )
                    p_et = cell.paragraphs[0]
                    p_et.paragraph_format.space_after = Pt(2)
                    r_et = sans_run(p_et.add_run(kort.etikett.upper()))
                    r_et.font.size = Pt(7.2)
                    r_et.bold = True
                    r_et.font.color.rgb = farg(_SVAG)

                    p_val = cell.add_paragraph()
                    p_val.paragraph_format.space_after = Pt(2)
                    r_val = sans_run(p_val.add_run(kort.varde))
                    r_val.font.size = Pt(14)
                    r_val.bold = True
                    r_val.font.color.rgb = farg(_TEXTFARG)

                    if kort.undertitel:
                        p_sub = cell.add_paragraph()
                        p_sub.paragraph_format.space_after = Pt(0)
                        r_sub = sans_run(p_sub.add_run(kort.undertitel))
                        r_sub.font.size = Pt(7.5)
                        r_sub.font.color.rgb = farg(_SVAG)

                    if idx < n_kort - 1:
                        g_col = col_i + 1
                        tab.columns[g_col].width = Mm(gap_mm)
                        tab.cell(0, g_col).width = Mm(gap_mm)
                d.add_paragraph().paragraph_format.space_after = Pt(4)
        elif isinstance(b, Faktaruta):
            tab = d.add_table(rows=1, cols=1)
            tab.alignment = WD_TABLE_ALIGNMENT.LEFT
            tab.autofit = False
            kanter(tab, synliga=False)
            fast_layout(tab, marginal=True, pad_v=100, pad_h=140)
            rad_hel(tab.rows[0], False)
            cell = tab.cell(0, 0)
            tab.columns[0].width = Mm(_BREDD_MM)
            cell.width = Mm(_BREDD_MM)
            skugga(cell, _FAKTA_BG)
            cell_kanter(
                cell,
                {
                    "left": ("single", "24", _SKOGSGRON),  # 3 pt = 24 åttondels punkter
                    "top": ("nil", "0", _LINJE),
                    "right": ("nil", "0", _LINJE),
                    "bottom": ("nil", "0", _LINJE),
                },
            )
            p0 = cell.paragraphs[0]
            p0.paragraph_format.space_after = Pt(4)
            r0 = sans_run(p0.add_run(b.rubrik))
            r0.bold = True
            r0.font.size = Pt(10)
            r0.font.color.rgb = farg(_SKOGSGRON)
            for st_txt in b.stycken:
                ps = cell.add_paragraph()
                ps.paragraph_format.space_after = Pt(3)
                rs = ps.add_run(st_txt)
                rs.font.size = Pt(9.2)
            for pt_txt in b.punkter:
                pp = cell.add_paragraph()
                pp.paragraph_format.left_indent = Mm(3.5)
                pp.paragraph_format.first_line_indent = Mm(-3.5)
                pp.paragraph_format.space_after = Pt(2.5)
                rb = sans_run(pp.add_run("• "))
                rb.font.size = Pt(9)
                rb.font.color.rgb = farg(_SKOGSGRON)
                rt = pp.add_run(pt_txt)
                rt.font.size = Pt(9)
            d.add_paragraph().paragraph_format.space_after = Pt(4)
        elif isinstance(b, Tabell):
            antal_kol = len(b.bredder)
            rader = ([b.kolumner] if b.kolumner else []) + list(b.rader)
            tab = d.add_table(rows=len(rader), cols=antal_kol)
            tab.alignment = WD_TABLE_ALIGNMENT.LEFT
            tab.autofit = False
            kanter(tab)
            fast_layout(tab)
            tbredd = _HEL_MM
            for j, kol in enumerate(tab.columns):
                kol.width = Mm(tbredd * b.bredder[j])
            storlek = Pt(8) if b.liten else Pt(9)
            for i, wrad in enumerate(tab.rows):
                rad_hel(wrad, b.kolumner is not None and i == 0)
            for i, rad in enumerate(rader):
                huvud = b.kolumner is not None and i == 0
                zebra = not huvud and b.kolumner is not None and (i % 2 == 0)
                for j, cell_text in enumerate(rad):
                    cell = tab.cell(i, j)
                    cell.width = Mm(tbredd * b.bredder[j])
                    p = cell.paragraphs[0]
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.line_spacing = 1.12
                    if huvud:
                        skugga(cell, _HUVUD_BG)
                    elif zebra:
                        skugga(cell, _ZEBRA_BG)
                    if (
                        not huvud
                        and b.ljus_kolumn is not None
                        and j == b.ljus_kolumn
                        and cell_text in LJUSFARG
                    ):
                        hexa, _namn = LJUSFARG[cell_text]
                        prick = sans_run(p.add_run("● "))
                        prick.font.color.rgb = farg(hexa)
                        prick.font.size = Pt(10.5)
                        r = sans_run(p.add_run(_ljustext(dok, cell_text)))
                        r.font.size = storlek
                        continue
                    r = sans_run(p.add_run(cell_text))
                    r.font.size = storlek
                    if huvud or (b.kolumner is None and j == 0):
                        r.bold = True
            d.add_paragraph().paragraph_format.space_after = Pt(4)
        elif isinstance(b, FigurPar):
            tab = d.add_table(rows=1, cols=3)
            tab.alignment = WD_TABLE_ALIGNMENT.LEFT
            tab.autofit = False
            kanter(tab, synliga=False)
            fast_layout(tab, marginal=False)
            rad_hel(tab.rows[0], False)
            bredder_mm = (_FIGUR_PAR_BILD_MM, _FIGUR_PAR_GAP_MM, _FIGUR_PAR_BILD_MM)
            for j, w_mm in enumerate(bredder_mm):
                tab.columns[j].width = Mm(w_mm)
                tab.cell(0, j).width = Mm(w_mm)
            for j, fig in ((0, b.vanster), (2, b.hoger)):
                c = tab.cell(0, j)
                p_img = c.paragraphs[0]
                p_img.paragraph_format.space_after = Pt(2)
                p_img.paragraph_format.keep_with_next = True
                p_img.add_run().add_picture(
                    str(Path(rapportmapp) / fig.fil), width=Mm(_FIGUR_PAR_BILD_MM)
                )
                p_cap = c.add_paragraph()
                p_cap.paragraph_format.space_after = Pt(2)
                rc = p_cap.add_run(fig.bildtext)
                rc.font.size = Pt(7.8)
                rc.italic = us.kursiv_bildtext
                rc.font.color.rgb = farg(_SVAG)
            if b.gemensam_bildtext:
                gc = d.add_paragraph()
                gr = gc.add_run(b.gemensam_bildtext)
                gr.font.size = Pt(8.2)
                gr.italic = us.kursiv_bildtext
                gr.font.color.rgb = farg(_SVAG)
                gc.paragraph_format.space_after = Pt(10)
            else:
                d.add_paragraph().paragraph_format.space_after = Pt(6)
        elif isinstance(b, Figur):
            p = d.add_paragraph()
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(3)
            p.add_run().add_picture(str(Path(rapportmapp) / b.fil), width=Mm(_FIGUR_MM))
            cap = d.add_paragraph()
            cr = cap.add_run(b.bildtext)
            cr.font.size = Pt(8.5)
            cr.italic = us.kursiv_bildtext
            cr.font.color.rgb = farg(_SVAG)
            cap.paragraph_format.space_after = Pt(10)
        elif isinstance(b, Sidbrytning):
            d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    ut_p = Path(ut)
    d.save(str(ut_p))
    _normalisera_docx(ut_p)
    return ut_p


def _normalisera_docx(fil: Path) -> None:
    """Fasta tider i zip och kärnegenskaper, så att samma rapport ger samma fil."""
    import zipfile

    tmp = fil.with_name(fil.name + ".norm")
    with zipfile.ZipFile(fil) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                text = re.sub(
                    r"(<dcterms:(?:created|modified)[^>]*>)[^<]*(<)",
                    r"\g<1>2026-01-01T00:00:00Z\2",
                    text,
                )
                data = text.encode("utf-8")
            ny = zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0))
            ny.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(ny, data)
    os.replace(tmp, fil)


# ---------------------------------------------------------------------- PDF


def _registrera_vera() -> None:
    """Förvalda typsnitt (SIL OFL 1.1, följer med i src/rapport/typsnitt):

    - Gelasio (serif) för brödtexten – samma teckenbredder som Georgia, förvalet i Word.
    - Liberation Sans för rubriker, tabeller och småtext – samma teckenbredder som Arial.
    """
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "FlodenVera" not in pdfmetrics.getRegisteredFontNames():
        mapp = Path(__file__).parent / "typsnitt"
        for namn, fil in (
            ("FlodenVera", "LiberationSans-Regular.ttf"),
            ("FlodenVera-Bold", "LiberationSans-Bold.ttf"),
            ("FlodenVera-Italic", "LiberationSans-Italic.ttf"),
            ("FlodenVera-BoldItalic", "LiberationSans-BoldItalic.ttf"),
        ):
            pdfmetrics.registerFont(TTFont(namn, str(mapp / fil)))
        addMapping("FlodenVera", 0, 0, "FlodenVera")
        addMapping("FlodenVera", 1, 0, "FlodenVera-Bold")
        addMapping("FlodenVera", 0, 1, "FlodenVera-Italic")
        addMapping("FlodenVera", 1, 1, "FlodenVera-BoldItalic")
    if "FlodenSerif" not in pdfmetrics.getRegisteredFontNames():
        mapp = Path(__file__).parent / "typsnitt"
        for namn, fil in (
            ("FlodenSerif", "Gelasio-Regular.ttf"),
            ("FlodenSerif-Bold", "Gelasio-Bold.ttf"),
            ("FlodenSerif-Italic", "Gelasio-Italic.ttf"),
            ("FlodenSerif-BoldItalic", "Gelasio-BoldItalic.ttf"),
        ):
            pdfmetrics.registerFont(TTFont(namn, str(mapp / fil)))
        addMapping("FlodenSerif", 0, 0, "FlodenSerif")
        addMapping("FlodenSerif", 1, 0, "FlodenSerif-Bold")
        addMapping("FlodenSerif", 0, 1, "FlodenSerif-Italic")
        addMapping("FlodenSerif", 1, 1, "FlodenSerif-BoldItalic")


def _registrera_lokalt(t: Typsnitt, vad: str) -> str:
    """Registrerar ett lokalt typsnitt i reportlab under ett namn ur dess sha256."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    namn = f"FlodenLokal-{t.sha256[:12]}"
    if namn not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont(namn, str(t.fil)))
        except (TTFError, OSError) as exc:
            raise TypsnittFel(
                f"Typsnittet för {vad} ({t.familj}) kan inte bäddas in i PDF:en. Använd en "
                "TTF-fil eller en OTF-fil med TrueType-konturer (OTF med PostScript-konturer "
                "stöds inte av reportlab)."
            ) from exc
    return namn


def _registrera_typsnitt(dok: Dokument) -> tuple[str, str, str, str, str]:
    """(brödtext, fet, kursiv, rubriker, sans) som namn registrerade i reportlab."""
    from reportlab.lib.fonts import addMapping

    _registrera_vera()
    us = dok.utseende
    if us.typsnitt_brodtext is None and us.typsnitt_rubriker is None:
        return (
            "FlodenSerif",
            "FlodenSerif-Bold",
            "FlodenSerif-Italic",
            "FlodenVera-Bold",
            "FlodenVera",
        )
    brod = (
        _registrera_lokalt(us.typsnitt_brodtext, "brödtext")
        if us.typsnitt_brodtext
        else "FlodenVera"
    )
    rubr = (
        _registrera_lokalt(us.typsnitt_rubriker, "rubriker")
        if us.typsnitt_rubriker
        else ("FlodenVera-Bold" if brod == "FlodenVera" else brod)
    )
    fet = rubr if us.typsnitt_rubriker else ("FlodenVera-Bold" if brod == "FlodenVera" else brod)
    kursiv = "FlodenVera-Italic" if brod == "FlodenVera" else brod
    if brod != "FlodenVera":
        addMapping(brod, 0, 0, brod)
        addMapping(brod, 1, 0, fet)
        addMapping(brod, 0, 1, kursiv)
        addMapping(brod, 1, 1, fet)
    sans = brod if us.typsnitt_brodtext else "FlodenVera"
    return brod, fet, kursiv, rubr, sans


_UPPHOJT_TILL: Final[dict[str, str]] = dict(zip("⁻⁰¹²³⁴⁵⁶⁷⁸⁹", "−0123456789", strict=True))


def _pdf_text(s: str, storlek: float = 9.5) -> str:
    """Escapar för reportlab, gör upphöjda exponenter till <super> och smalt hårt mellanslag."""
    s = escape(s)

    def sup(m: re.Match[str]) -> str:
        return "<super>" + "".join(_UPPHOJT_TILL[c] for c in m.group(0)) + "</super>"

    s = re.sub("[⁻⁰¹⁴⁵⁶⁷⁸⁹][⁻⁰¹²³⁴⁵⁶⁷⁸⁹]*|(?<=10)[²³]", sup, s)
    return s.replace(" ", f'<font size="{0.55 * storlek:.2f}">\u00a0</font>')


_TOM_CM_RE: Final[re.Pattern[str]] = re.compile(r"^[-+0-9.eE\s]+cm$")


def _sida_har_innehall(code: list[str] | tuple[str, ...]) -> bool:
    """Sant bara när PDF-sidan innehåller ritad text, bild eller grafik (inte bara q/Q/cm)."""
    for rad in code:
        s = str(rad).strip()
        if not s or s.startswith("%") or s in ("q", "Q"):
            continue
        if _TOM_CM_RE.fullmatch(s):
            continue
        return True
    return False


def _rensa_flode(flode: list[Any], spacer_typ: type[Any], pagebreak_typ: type[Any]) -> list[Any]:
    """Tar bort avslutande och dubbla sidbrytningar/mellanrum som kan skapa en tom slutsida."""
    ut: list[Any] = []
    for obj in flode:
        if isinstance(obj, pagebreak_typ):
            while ut and isinstance(ut[-1], spacer_typ):
                ut.pop()
            if not ut or isinstance(ut[-1], pagebreak_typ):
                continue
        ut.append(obj)
    while ut and isinstance(ut[-1], spacer_typ | pagebreak_typ):
        ut.pop()
    return ut


def skriv_pdf(dok: Dokument, ut: str | Path, rapportmapp: str | Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import (
        Flowable,
        Image,
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    us = dok.utseende
    typsnitt, fet, kursiv, rubriktyp, sans = _registrera_typsnitt(dok)
    sans_fet = "FlodenVera-Bold" if sans == "FlodenVera" else fet
    text_c = colors.HexColor("#" + _TEXTFARG)
    svag_c = colors.HexColor("#" + _SVAG)
    linje_c = colors.HexColor("#" + _LINJE)
    skogs_c = colors.HexColor("#" + _SKOGSGRON)
    fakta_bg_c = colors.HexColor("#" + _FAKTA_BG)
    huvud_bg_c = colors.HexColor("#" + _HUVUD_BG)
    zebra_bg_c = colors.HexColor("#" + _ZEBRA_BG)
    accent_c = skogs_c if us.accent == "000000" else colors.HexColor("#" + us.accent)
    rubrik_c = colors.HexColor("#" + _RUBRIKFARG)

    bas = ParagraphStyle(
        "bas",
        fontName=typsnitt,
        fontSize=9.8,
        leading=14.0,
        textColor=text_c,
        alignment=TA_LEFT,
        spaceAfter=5.5,
    )
    stilar = {
        "normal": bas,
        "ingress": ParagraphStyle("ingress", parent=bas, fontSize=10.6, leading=15.0, spaceAfter=9),
        "liten": ParagraphStyle(
            "liten",
            parent=bas,
            fontName=sans,
            fontSize=8.0,
            leading=10.8,
            textColor=svag_c,
            spaceAfter=4,
        ),
        "notis": ParagraphStyle(
            "notis",
            parent=bas,
            fontName=sans,
            fontSize=8.0,
            leading=10.8,
            textColor=svag_c,
            spaceAfter=8,
        ),
        "titel": ParagraphStyle(
            "titel",
            parent=bas,
            fontName=rubriktyp,
            fontSize=19,
            leading=23.5,
            spaceAfter=8,
            textColor=rubrik_c,
        ),
        "cell": ParagraphStyle(
            "cell", parent=bas, fontName=sans, fontSize=8.5, leading=11.0, spaceAfter=0
        ),
        "cell_liten": ParagraphStyle(
            "cell_liten", parent=bas, fontName=sans, fontSize=7.5, leading=9.8, spaceAfter=0
        ),
        "bildtext": ParagraphStyle(
            "bildtext",
            parent=bas,
            fontName=kursiv if us.kursiv_bildtext else typsnitt,
            fontSize=8.0,
            leading=10.8,
            textColor=svag_c,
            spaceAfter=10,
        ),
        "bildtext_par": ParagraphStyle(
            "bildtext_par",
            parent=bas,
            fontName=kursiv if us.kursiv_bildtext else typsnitt,
            fontSize=7.5,
            leading=10.0,
            textColor=svag_c,
            spaceAfter=2,
        ),
        "huvud_v": ParagraphStyle(
            "huvud_v", parent=bas, fontName=sans_fet, fontSize=11, leading=14
        ),
        "huvud_h": ParagraphStyle(
            "huvud_h",
            parent=bas,
            fontName=sans,
            fontSize=8.5,
            leading=11.5,
            alignment=TA_RIGHT,
            spaceAfter=0,
        ),
        "kpi_etikett": ParagraphStyle(
            "kpi_etikett",
            parent=bas,
            fontName=sans_fet,
            fontSize=7.0,
            leading=9.2,
            textColor=svag_c,
            spaceAfter=2,
        ),
        "kpi_varde": ParagraphStyle(
            "kpi_varde",
            parent=bas,
            fontName=sans_fet,
            fontSize=14.0,
            leading=16.5,
            textColor=text_c,
            spaceAfter=2,
        ),
        "kpi_sub": ParagraphStyle(
            "kpi_sub",
            parent=bas,
            fontName=sans,
            fontSize=7.3,
            leading=9.5,
            textColor=svag_c,
            spaceAfter=0,
        ),
        "fakta_rubrik": ParagraphStyle(
            "fakta_rubrik",
            parent=bas,
            fontName=sans_fet,
            fontSize=9.8,
            leading=13.0,
            textColor=skogs_c,
            spaceAfter=4,
        ),
        "fakta_text": ParagraphStyle(
            "fakta_text",
            parent=bas,
            fontName=typsnitt,
            fontSize=9.0,
            leading=12.8,
            textColor=text_c,
            spaceAfter=3,
        ),
        "fakta_punkt": ParagraphStyle(
            "fakta_punkt",
            parent=bas,
            fontName=typsnitt,
            fontSize=8.9,
            leading=12.6,
            textColor=text_c,
            leftIndent=10,
            bulletIndent=1,
            spaceAfter=2.5,
        ),
    }
    rubrikstilar = {
        1: ParagraphStyle(
            "h1",
            parent=bas,
            fontName=rubriktyp,
            fontSize=13.5,
            leading=17.5,
            textColor=rubrik_c,
            spaceBefore=14,
            spaceAfter=5,
            keepWithNext=True,
        ),
        2: ParagraphStyle(
            "h2",
            parent=bas,
            fontName=rubriktyp,
            fontSize=11.0,
            leading=14.5,
            textColor=rubrik_c,
            spaceBefore=11,
            spaceAfter=3.5,
            keepWithNext=True,
        ),
        3: ParagraphStyle(
            "h3",
            parent=bas,
            fontName=rubriktyp,
            fontSize=9.8,
            leading=13.0,
            textColor=rubrik_c,
            spaceBefore=8,
            spaceAfter=2,
            keepWithNext=True,
        ),
    }

    def para(text: str, stil: ParagraphStyle, **kw: Any) -> Paragraph:
        return Paragraph(_pdf_text(text, float(stil.fontSize)), stil, **kw)

    class Prick(Flowable):  # type: ignore[misc]
        """Trafikljus: en fylld cirkel."""

        def __init__(self, hexa: str) -> None:
            super().__init__()
            self.farg = colors.HexColor("#" + hexa)
            self.width = self.height = 3.0 * mm

        def draw(self) -> None:
            self.canv.setFillColor(self.farg)
            self.canv.setStrokeColor(self.farg)
            self.canv.circle(1.5 * mm, 1.5 * mm, 1.4 * mm, fill=1, stroke=0)

    def _skala_bild(fil: Path, max_w_mm: float, max_h_mm: float) -> Image:
        img = Image(str(fil))
        skala = min((max_w_mm * mm) / img.imageWidth, (max_h_mm * mm) / img.imageHeight)
        img.drawWidth = img.imageWidth * skala
        img.drawHeight = img.imageHeight * skala
        img.hAlign = "LEFT"
        return img

    marg_v = _MARGINAL_V_MM * mm
    marg_h = _MARGINAL_H_MM * mm
    bredd = _BREDD_MM * mm  # 170 mm full bredd
    hel = _HEL_MM * mm
    flode: list[Any] = []

    th = dok.titelhuvud
    if th is not None:
        vanster: list[Any] = []
        if us.logotyp is not None:
            logo = Image(str(us.logotyp))
            skala = (14 * mm) / logo.imageHeight
            logo.drawHeight = 14 * mm
            logo.drawWidth = min(logo.imageWidth * skala, hel * 0.5)
            logo.hAlign = "LEFT"
            vanster += [logo, Spacer(1, 2 * mm)]
        if th.avsandare:
            vanster.append(para(th.avsandare, stilar["huvud_v"]))
        hoger: list[Any] = [
            Paragraph(
                f'<font name="{sans_fet}" color="#{accent_c.hexval()[2:]}">'
                f"{_pdf_text(th.dokumenttyp)}</font>",
                ParagraphStyle("dt", parent=stilar["huvud_h"], fontSize=10, leading=13),
            )
        ]
        for etikett, varde in th.rader():
            hoger.append(
                Paragraph(
                    f'<font color="#{_SVAG}">{escape(etikett)}</font> {_pdf_text(varde, 8.5)}',
                    stilar["huvud_h"],
                )
            )
        huvud = Table([[vanster or "", hoger]], colWidths=[hel * 0.58, hel * 0.42], hAlign="LEFT")
        huvud.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
                    ("LINEBELOW", (0, 0), (-1, -1), 1.4, accent_c),
                ]
            )
        )
        flode += [huvud, Spacer(1, 5.5 * mm)]

    flode.append(para(dok.titel, stilar["titel"]))

    for b in dok.block:
        if isinstance(b, Rubrik):
            flode.append(para(b.text, rubrikstilar[min(b.niva, 3)]))
        elif isinstance(b, Stycke):
            p = para(b.text, stilar.get(b.stil, bas))
            if b.stil == "notis" and flode and isinstance(flode[-1], Paragraph):
                grupp_n: list[Any] = [flode.pop(), p]
                while (
                    flode
                    and isinstance(flode[-1], Paragraph)
                    and getattr(flode[-1].style, "keepWithNext", False)
                ):
                    grupp_n.insert(0, flode.pop())
                flode.append(KeepTogether(grupp_n))
            else:
                flode.append(p)
        elif isinstance(b, Punktlista):
            pst = ParagraphStyle("punkt", parent=bas, leftIndent=12, bulletIndent=2, spaceAfter=2.5)
            for punkt in b.punkter:
                flode.append(para(punkt, pst, bulletText="•"))
            flode.append(Spacer(1, 3))
        elif isinstance(b, Nyckeltalsrad):
            n_kort = len(b.kort)
            if n_kort > 0:
                gap_w = 3.0 * mm
                kort_w = (bredd - gap_w * (n_kort - 1)) / n_kort
                rad_celler: list[Any] = []
                kol_bredder: list[float] = []
                stil_kpi: list[Any] = [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ]
                for idx, kort in enumerate(b.kort):
                    col_i = idx * 2
                    innehall = [
                        para(kort.etikett.upper(), stilar["kpi_etikett"]),
                        para(kort.varde, stilar["kpi_varde"]),
                    ]
                    if kort.undertitel:
                        innehall.append(para(kort.undertitel, stilar["kpi_sub"]))
                    rad_celler.append(innehall)
                    kol_bredder.append(kort_w)
                    top_c = colors.HexColor("#" + (kort.accent or _SKOGSGRON))
                    stil_kpi.extend(
                        [
                            ("BACKGROUND", (col_i, 0), (col_i, 0), fakta_bg_c),
                            ("LINEABOVE", (col_i, 0), (col_i, 0), 2.2, top_c),
                            ("LINEBELOW", (col_i, 0), (col_i, 0), 0.5, linje_c),
                            ("LINEBEFORE", (col_i, 0), (col_i, 0), 0.5, linje_c),
                            ("LINEAFTER", (col_i, 0), (col_i, 0), 0.5, linje_c),
                        ]
                    )
                    if idx < n_kort - 1:
                        g_col = col_i + 1
                        rad_celler.append("")
                        kol_bredder.append(gap_w)
                        stil_kpi.extend(
                            [
                                ("LEFTPADDING", (g_col, 0), (g_col, 0), 0),
                                ("RIGHTPADDING", (g_col, 0), (g_col, 0), 0),
                            ]
                        )
                kpi_tab = Table([rad_celler], colWidths=kol_bredder, hAlign="LEFT")
                kpi_tab.setStyle(TableStyle(stil_kpi))
                flode.append(KeepTogether([kpi_tab, Spacer(1, 6)]))
        elif isinstance(b, Faktaruta):
            inre_f: list[Any] = [para(b.rubrik, stilar["fakta_rubrik"])]
            for st_txt in b.stycken:
                inre_f.append(para(st_txt, stilar["fakta_text"]))
            for pt_txt in b.punkter:
                inre_f.append(para(pt_txt, stilar["fakta_punkt"], bulletText="•"))
            f_tab = Table([[inre_f]], colWidths=[bredd], hAlign="LEFT")
            f_tab.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), fakta_bg_c),
                        ("LINEBEFORE", (0, 0), (0, -1), 3.0, skogs_c),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ("LEFTPADDING", (0, 0), (-1, -1), 11),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ]
                )
            )
            flode.append(KeepTogether([f_tab, Spacer(1, 7)]))
        elif isinstance(b, Tabell):
            cst = stilar["cell_liten"] if b.liten else stilar["cell"]
            tbredd = hel
            hst = ParagraphStyle("huvud", parent=cst, fontName=sans_fet)
            data: list[list[Any]] = []
            if b.kolumner:
                data.append([para(k, hst) for k in b.kolumner])
            for rad in b.rader:
                celler: list[Any] = []
                for j, cell_text in enumerate(rad):
                    if b.ljus_kolumn == j and cell_text in LJUSFARG:
                        hexa, _namn = LJUSFARG[cell_text]
                        col_w_inre = max(12.0 * mm, tbredd * b.bredder[j] - 4.0 * mm - 8)
                        inre = Table(
                            [[Prick(hexa), para(_ljustext(dok, cell_text), cst)]],
                            colWidths=[4.0 * mm, col_w_inre],
                        )
                        inre.setStyle(
                            TableStyle(
                                [
                                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                ]
                            )
                        )
                        celler.append(inre)
                    else:
                        stil = hst if (b.kolumner is None and j == 0) else cst
                        celler.append(para(cell_text, stil))
                data.append(celler)
            tab = Table(
                data,
                colWidths=[tbredd * x for x in b.bredder],
                repeatRows=1 if b.kolumner else 0,
                hAlign="LEFT",
            )
            pad_v = 2.4 if b.liten else 3.2
            stil_lista: list[Any] = [
                ("LINEABOVE", (0, 0), (-1, 0), 0.7, linje_c),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, linje_c),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), pad_v),
                ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v),
                ("LEFTPADDING", (0, 0), (-1, -1), 4.0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4.0),
            ]
            if b.kolumner:
                stil_lista.append(("BACKGROUND", (0, 0), (-1, 0), huvud_bg_c))
                for ri in range(2, len(data), 2):
                    stil_lista.append(("BACKGROUND", (0, ri), (-1, ri), zebra_bg_c))
            tab.setStyle(TableStyle(stil_lista))
            # Håll samman föregående kort inledningsstycke (under en Rubrik med keepWithNext)
            # med tabellens rubrikrad och första rader så rubrik + ingress ej blir ensamma i sidfot.
            if (
                len(flode) >= 2
                and isinstance(flode[-1], Paragraph)
                and isinstance(flode[-2], Paragraph)
                and getattr(flode[-2].style, "keepWithNext", False)
                and not getattr(flode[-1].style, "keepWithNext", False)
                and len(getattr(flode[-1], "text", "")) <= 450
            ):
                flode[-1].style = flode[-1].style.clone(
                    f"{flode[-1].style.name}_kwn", keepWithNext=True
                )
            flode.append(tab)
            flode.append(Spacer(1, 5.5 if b.liten else 7))
        elif isinstance(b, FigurPar):
            v_img = _skala_bild(
                Path(rapportmapp) / b.vanster.fil, _FIGUR_PAR_BILD_MM, _FIGUR_PAR_MAX_HOJD_MM
            )
            h_img = _skala_bild(
                Path(rapportmapp) / b.hoger.fil, _FIGUR_PAR_BILD_MM, _FIGUR_PAR_MAX_HOJD_MM
            )
            par_tab = Table(
                [
                    [
                        [v_img, Spacer(1, 2.5), para(b.vanster.bildtext, stilar["bildtext_par"])],
                        "",
                        [h_img, Spacer(1, 2.5), para(b.hoger.bildtext, stilar["bildtext_par"])],
                    ]
                ],
                colWidths=[
                    _FIGUR_PAR_BILD_MM * mm,
                    _FIGUR_PAR_GAP_MM * mm,
                    _FIGUR_PAR_BILD_MM * mm,
                ],
                hAlign="LEFT",
            )
            par_tab.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            grupp: list[Any] = [Spacer(1, 3), par_tab]
            if b.gemensam_bildtext:
                grupp.extend([Spacer(1, 2), para(b.gemensam_bildtext, stilar["bildtext"])])
            else:
                grupp.append(Spacer(1, 7))
            flode.append(KeepTogether(grupp))
        elif isinstance(b, Figur):
            img = _skala_bild(Path(rapportmapp) / b.fil, _FIGUR_MM, _FIGUR_MAX_HOJD_MM)
            flode.append(
                KeepTogether(
                    [Spacer(1, 3), img, Spacer(1, 2.5), para(b.bildtext, stilar["bildtext"])]
                )
            )
        elif isinstance(b, Sidbrytning):
            flode.append(PageBreak())

    class SidCanvas(Canvas):  # type: ignore[misc]
        """Ritar sidhuvud och sidfot när antalet sidor är känt ("Sida 2 (9)")."""

        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self._sidor: list[dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 - reportlabs namn
            self._sidor.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            sidor = [s for s in self._sidor if _sida_har_innehall(s.get("_code", ()))]
            if not sidor and self._sidor:
                sidor = self._sidor[:1]
            antal = len(sidor)
            for nr, sida in enumerate(sidor, start=1):
                self.__dict__.update(sida)
                self._pageNumber = nr
                self._rita_ram(antal)
                super().showPage()
            super().save()

        def _rita_ram(self, antal: int) -> None:
            self.saveState()
            self.setFont(sans, 7.5)
            self.setFillColor(svag_c)
            if self._pageNumber > 1 or th is None:
                self.drawString(marg_v, A4[1] - 11.5 * mm, dok.sidhuvud)
                self.setStrokeColor(linje_c)
                self.setLineWidth(0.4)
                self.line(marg_v, A4[1] - 13.0 * mm, A4[0] - marg_h, A4[1] - 13.0 * mm)
            self.setStrokeColor(linje_c)
            self.setLineWidth(0.35)
            self.line(marg_v, 14.0 * mm, A4[0] - marg_h, 14.0 * mm)
            self.drawString(marg_v, 10.0 * mm, dok.sidfot)
            self.drawRightString(
                A4[0] - marg_h, 10.0 * mm, us.sidnummer.format(sida=self._pageNumber, antal=antal)
            )
            self.restoreState()

    ut_p = Path(ut)
    nyckelord = typsnittsmetadata(dok)
    mall = SimpleDocTemplate(
        str(ut_p),
        pagesize=A4,
        leftMargin=marg_v,
        rightMargin=marg_h,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=dok.titel,
        author=(th.avsandare if th else "") or "Floden",
        subject=dok.sidfot,
        keywords=nyckelord,
        creator="Floden (reportlab)",
        lang=us.sprak,
        invariant=1,
    )
    flode = _rensa_flode(flode, Spacer, PageBreak)
    mall.build(flode, canvasmaker=SidCanvas)
    return ut_p


__all__ = [
    "LJUSFARG",
    "ExportStoppad",
    "TypsnittFel",
    "skriv_pdf",
    "skriv_word",
    "typsnittsmetadata",
]
