"""Export av skissunderlaget till Word (.docx) och PDF (R1).

Båda formaten skrivs ur samma block (`rapport.skissmall`). Exporten görs bara efter en godkänd
kontroll (`RapportTjanst.exportera`); ett fel stoppar med `ExportStoppad`.

Layouten är lugn och vänsterställd: titelhuvud med avsändare, eventuell logotyp, dokumenttyp,
datum, diarienummer och handläggare; rubriker utan nummer som förval; bildtexter i kursiv under
bilden; sidfot med "Sida 2 (9)". Språket är satt till svenska i båda formaten.

- **Word:** python-docx (ren Python). Rubrikerna använder Words inbyggda format (Rubrik 1–3 /
  Heading 1–3), så att innehållsförteckning och navigeringsfönstret fungerar. Typsnitt Arial,
  eller familjenamnet för ett lokalt typsnitt (Word bäddar inte in det; det måste finnas på
  datorn som öppnar filen).
- **PDF:** reportlab (ren Python, inga systemberoenden). Typsnittet är Bitstream Vera Sans, som
  följer med reportlab, eller ett lokalt TTF/OTF-typsnitt (TrueType-konturer) som bäddas in; dess
  familjenamn och sha256 står då i PDF:ens metadata (nyckelord) och i rapportens exportpost.
  `invariant` gör filen byte-identisk för samma innehåll.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Final
from xml.sax.saxutils import escape

from rapport.skissmall import (
    Dokument,
    Figur,
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
_TEXTFARG = "1F2933"
_SVAG = "56616C"
_LINJE = "C9D1D9"
_HUVUD_BG = "EEF2F5"
_MARGINAL_MM = 22.0
_BREDD_MM = 210.0 - 2 * _MARGINAL_MM
_FIGUR_MM = 116.0


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
    brod = us.typsnitt_brodtext.familj if us.typsnitt_brodtext else "Arial"
    rubr = us.typsnitt_rubriker.familj if us.typsnitt_rubriker else brod

    d = Document()
    sek = d.sections[0]
    sek.orientation = WD_ORIENT.PORTRAIT
    sek.page_width, sek.page_height = Mm(210), Mm(297)
    sek.left_margin = sek.right_margin = Mm(_MARGINAL_MM)
    sek.top_margin, sek.bottom_margin = Mm(22), Mm(20)
    sek.header_distance, sek.footer_distance = Mm(10), Mm(9)

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

    # Svenska som språk för hela dokumentet (stavningskontrollen) – i standardformaten och i Normal.
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
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.2
    for niva, storlek, fore in ((1, 15, 20), (2, 12, 14), (3, 10.5, 10)):
        st = d.styles[f"Heading {niva}"]
        st.font.name = rubr
        st.font.size = Pt(storlek)
        st.font.bold = True
        st.font.italic = False
        st.font.color.rgb = farg(us.accent if niva < 3 else _TEXTFARG)
        st.paragraph_format.space_before = Pt(fore)
        st.paragraph_format.space_after = Pt(5 if niva == 1 else 3)
        st.paragraph_format.keep_with_next = True
        satt_typsnitt(st.element, rubr)
    titel_st = d.styles["Title"]
    titel_st.font.name = rubr
    titel_st.font.size = Pt(22)
    titel_st.font.bold = True
    titel_st.font.color.rgb = farg(_TEXTFARG)
    satt_typsnitt(titel_st.element, rubr)
    # Titelformatet i standardmallen har en linje under; den ersätts av titelhuvudets linje.
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

    def liten_run(p: Any, text: str) -> None:
        r = p.add_run(text)
        r.font.size = Pt(8)
        r.font.color.rgb = farg(_SVAG)

    hp = sek.header.paragraphs[0]
    liten_run(hp, dok.sidhuvud)
    # Första sidan har titelhuvudet i stället för sidhuvud; sidfoten är densamma på alla sidor.
    sek.different_first_page_header_footer = dok.titelhuvud is not None
    for sidfot in (sek.footer, sek.first_page_footer):
        fp = sidfot.paragraphs[0]
        fp.paragraph_format.tab_stops.add_tab_stop(Mm(_BREDD_MM), WD_TAB_ALIGNMENT.RIGHT)
        liten_run(fp, dok.sidfot + "\t")
        # "Sida {sida} ({antal})" med fälten PAGE och NUMPAGES
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

    def fast_layout(tab: Any, marginal: bool = True) -> None:
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tab._tbl.tblPr.append(layout)
        if marginal:
            mar = OxmlElement("w:tblCellMar")
            for kant, v in (("top", 45), ("left", 85), ("bottom", 45), ("right", 85)):
                el = OxmlElement(f"w:{kant}")
                el.set(qn("w:w"), str(v))
                el.set(qn("w:type"), "dxa")
                mar.append(el)
            tab._tbl.tblPr.append(mar)

    def rad_hel(rad: Any, rubrikrad: bool) -> None:
        """Raden delas inte mellan sidor; rubrikraden upprepas på varje sida."""
        tr_pr = rad._tr.get_or_add_trPr()
        tr_pr.append(OxmlElement("w:cantSplit"))
        if rubrikrad:
            tr_pr.append(OxmlElement("w:tblHeader"))

    def linje_under(p: Any) -> None:
        ppr = p._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        el = OxmlElement("w:bottom")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "8")
        el.set(qn("w:space"), "6")
        el.set(qn("w:color"), us.accent)
        bdr.append(el)
        ppr.append(bdr)

    # Titelhuvud: logotyp och avsändare till vänster, dokumenttyp, datum, dnr och handläggare
    # till höger, en linje i accentfärgen, sedan titeln.
    th = dok.titelhuvud
    if th is not None:
        tab = d.add_table(rows=1, cols=2)
        tab.alignment = WD_TABLE_ALIGNMENT.LEFT
        tab.autofit = False
        kanter(tab, synliga=False)
        fast_layout(tab, marginal=False)
        for j, b in enumerate((0.55, 0.45)):
            tab.columns[j].width = Mm(_BREDD_MM * b)
            tab.cell(0, j).width = Mm(_BREDD_MM * b)
        v = tab.cell(0, 0).paragraphs[0]
        v.paragraph_format.space_after = Pt(0)
        if us.logotyp is not None:
            v.add_run().add_picture(str(us.logotyp), height=Mm(14))
            if th.avsandare:
                v = tab.cell(0, 0).add_paragraph()
                v.paragraph_format.space_after = Pt(0)
        if th.avsandare:
            r = v.add_run(th.avsandare)
            r.bold = True
            r.font.size = Pt(11)
        h = tab.cell(0, 1).paragraphs[0]
        h.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        h.paragraph_format.space_after = Pt(1)
        r = h.add_run(th.dokumenttyp)
        r.bold = True
        r.font.size = Pt(10)
        r.font.color.rgb = farg(us.accent)
        for etikett, varde in th.rader():
            h = tab.cell(0, 1).add_paragraph()
            h.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            h.paragraph_format.space_after = Pt(0)
            liten_run(h, f"{etikett} ")
            rv = h.add_run(varde)
            rv.font.size = Pt(8.5)
        sep = d.add_paragraph()
        sep.paragraph_format.space_after = Pt(14)
        linje_under(sep)

    t = d.add_paragraph(dok.titel, style="Title")
    t.paragraph_format.space_after = Pt(10)

    forra: Any = None
    for b in dok.block:
        if isinstance(b, Rubrik):
            d.add_heading(b.text, level=min(b.niva, 3))
        elif isinstance(b, Stycke):
            if b.stil == "notis" and forra is not None:
                forra.paragraph_format.keep_with_next = True  # påståendet hålls ihop med status
            p = d.add_paragraph()
            forra = p
            run = p.add_run(b.text)
            if b.stil == "ingress":
                run.font.size = Pt(11.5)
                p.paragraph_format.space_after = Pt(12)
            elif b.stil in ("liten", "notis"):
                run.font.size = Pt(8.5)
                run.font.color.rgb = farg(_SVAG)
                if b.stil == "notis":
                    p.paragraph_format.space_after = Pt(10)
        elif isinstance(b, Punktlista):
            for punkt in b.punkter:
                p = d.add_paragraph(style="List Bullet")
                p.add_run(punkt)
                p.paragraph_format.space_after = Pt(3)
        elif isinstance(b, Tabell):
            antal_kol = len(b.bredder)
            rader = ([b.kolumner] if b.kolumner else []) + list(b.rader)
            tab = d.add_table(rows=len(rader), cols=antal_kol)
            tab.alignment = WD_TABLE_ALIGNMENT.LEFT
            tab.autofit = False
            kanter(tab)
            fast_layout(tab)
            for j, kol in enumerate(tab.columns):
                kol.width = Mm(_BREDD_MM * b.bredder[j])
            storlek = Pt(8) if b.liten else Pt(9)
            for i, wrad in enumerate(tab.rows):
                rad_hel(wrad, b.kolumner is not None and i == 0)
            for i, rad in enumerate(rader):
                for j, cell_text in enumerate(rad):
                    cell = tab.cell(i, j)
                    cell.width = Mm(_BREDD_MM * b.bredder[j])
                    p = cell.paragraphs[0]
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.line_spacing = 1.1
                    huvud = b.kolumner is not None and i == 0
                    if (
                        not huvud
                        and b.ljus_kolumn is not None
                        and j == b.ljus_kolumn
                        and cell_text in LJUSFARG
                    ):
                        hexa, _namn = LJUSFARG[cell_text]
                        prick = p.add_run("● ")
                        prick.font.color.rgb = farg(hexa)
                        prick.font.size = Pt(11)
                        r = p.add_run(_ljustext(dok, cell_text))
                        r.font.size = storlek
                        continue
                    r = p.add_run(cell_text)
                    r.font.size = storlek
                    if huvud or (b.kolumner is None and j == 0):
                        r.bold = True
                    if huvud:
                        skugga(cell, _HUVUD_BG)
            d.add_paragraph().paragraph_format.space_after = Pt(4)
        elif isinstance(b, Figur):
            p = d.add_paragraph()
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(3)
            p.add_run().add_picture(str(Path(rapportmapp) / b.fil), width=Mm(_FIGUR_MM))
            cap = d.add_paragraph()
            cr = cap.add_run(b.bildtext)
            cr.font.size = Pt(8.5)
            cr.italic = us.kursiv_bildtext
            cr.font.color.rgb = farg(_SVAG)
            cap.paragraph_format.space_after = Pt(14)
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
    import reportlab
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "FlodenVera" not in pdfmetrics.getRegisteredFontNames():
        mapp = Path(reportlab.__file__).parent / "fonts"
        pdfmetrics.registerFont(TTFont("FlodenVera", str(mapp / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("FlodenVera-Bold", str(mapp / "VeraBd.ttf")))
        pdfmetrics.registerFont(TTFont("FlodenVera-Italic", str(mapp / "VeraIt.ttf")))
        pdfmetrics.registerFont(TTFont("FlodenVera-BoldItalic", str(mapp / "VeraBI.ttf")))
        addMapping("FlodenVera", 0, 0, "FlodenVera")
        addMapping("FlodenVera", 1, 0, "FlodenVera-Bold")
        addMapping("FlodenVera", 0, 1, "FlodenVera-Italic")
        addMapping("FlodenVera", 1, 1, "FlodenVera-BoldItalic")


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


def _registrera_typsnitt(dok: Dokument) -> tuple[str, str, str, str]:
    """(brödtext, fet, kursiv, rubriker) som namn registrerade i reportlab."""
    from reportlab.lib.fonts import addMapping

    _registrera_vera()
    us = dok.utseende
    if us.typsnitt_brodtext is None and us.typsnitt_rubriker is None:
        return "FlodenVera", "FlodenVera-Bold", "FlodenVera-Italic", "FlodenVera-Bold"
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
    return brod, fet, kursiv, rubr


_UPPHOJT_TILL: Final[dict[str, str]] = dict(zip("⁻⁰¹²³⁴⁵⁶⁷⁸⁹", "−0123456789", strict=True))


def _pdf_text(s: str, storlek: float = 9.5) -> str:
    """Escapar för reportlab, gör upphöjda exponenter till <super> och smalt hårt mellanslag
    (U+202F, saknas i typsnittet) till ett hårt mellanslag i mindre grad (bryts aldrig)."""
    s = escape(s)

    def sup(m: re.Match[str]) -> str:
        return "<super>" + "".join(_UPPHOJT_TILL[c] for c in m.group(0)) + "</super>"

    s = re.sub("[⁻⁰¹⁴⁵⁶⁷⁸⁹][⁻⁰¹²³⁴⁵⁶⁷⁸⁹]*|(?<=10)[²³]", sup, s)
    return s.replace(" ", f'<font size="{0.55 * storlek:.2f}"> </font>')


def skriv_pdf(dok: Dokument, ut: str | Path, rapportmapp: str | Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import (
        Flowable,
        HRFlowable,
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
    typsnitt, fet, kursiv, rubriktyp = _registrera_typsnitt(dok)
    text_c = colors.HexColor("#" + _TEXTFARG)
    svag_c = colors.HexColor("#" + _SVAG)
    linje_c = colors.HexColor("#" + _LINJE)
    accent_c = colors.HexColor("#" + us.accent)
    bas = ParagraphStyle(
        "bas",
        fontName=typsnitt,
        fontSize=9.5,
        leading=13.8,
        textColor=text_c,
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    stilar = {
        "normal": bas,
        "ingress": ParagraphStyle("ingress", parent=bas, fontSize=11, leading=15.5, spaceAfter=12),
        "liten": ParagraphStyle("liten", parent=bas, fontSize=8, leading=10.8, textColor=svag_c),
        "notis": ParagraphStyle(
            "notis", parent=bas, fontSize=8, leading=10.8, textColor=svag_c, spaceAfter=10
        ),
        "titel": ParagraphStyle(
            "titel", parent=bas, fontName=rubriktyp, fontSize=20, leading=24.5, spaceAfter=10
        ),
        "cell": ParagraphStyle("cell", parent=bas, fontSize=8.5, leading=11, spaceAfter=0),
        "cell_liten": ParagraphStyle(
            "cell_liten", parent=bas, fontSize=7.3, leading=9.3, spaceAfter=0
        ),
        "bildtext": ParagraphStyle(
            "bildtext",
            parent=bas,
            fontName=kursiv if us.kursiv_bildtext else typsnitt,
            fontSize=8,
            leading=10.8,
            textColor=svag_c,
            spaceAfter=14,
        ),
        "huvud_v": ParagraphStyle("huvud_v", parent=bas, fontName=fet, fontSize=11, leading=14),
        "huvud_h": ParagraphStyle(
            "huvud_h", parent=bas, fontSize=8.5, leading=11.5, alignment=TA_RIGHT, spaceAfter=0
        ),
    }
    rubrikstilar = {
        1: ParagraphStyle(
            "h1", parent=bas, fontName=rubriktyp, fontSize=14, leading=18,
            textColor=accent_c, spaceBefore=16, spaceAfter=6,
        ),
        2: ParagraphStyle(
            "h2", parent=bas, fontName=rubriktyp, fontSize=11.5, leading=15,
            textColor=accent_c, spaceBefore=12, spaceAfter=4,
        ),
        3: ParagraphStyle(
            "h3", parent=bas, fontName=rubriktyp, fontSize=9.5, leading=13,
            spaceBefore=9, spaceAfter=2,
        ),
    }  # fmt: skip

    def para(text: str, stil: ParagraphStyle, **kw: Any) -> Paragraph:
        return Paragraph(_pdf_text(text, float(stil.fontSize)), stil, **kw)

    class Prick(Flowable):  # type: ignore[misc]
        """Trafikljus: en fylld cirkel."""

        def __init__(self, hexa: str) -> None:
            super().__init__()
            self.farg = colors.HexColor("#" + hexa)
            self.width = self.height = 3.2 * mm

        def draw(self) -> None:
            self.canv.setFillColor(self.farg)
            self.canv.setStrokeColor(self.farg)
            self.canv.circle(1.6 * mm, 1.6 * mm, 1.5 * mm, fill=1, stroke=0)

    marg = _MARGINAL_MM * mm
    bredd = A4[0] - 2 * marg
    flode: list[Any] = []
    th = dok.titelhuvud
    if th is not None:
        vanster: list[Any] = []
        if us.logotyp is not None:
            logo = Image(str(us.logotyp))
            skala = (14 * mm) / logo.imageHeight
            logo.drawHeight = 14 * mm
            logo.drawWidth = min(logo.imageWidth * skala, bredd * 0.5)
            logo.hAlign = "LEFT"
            vanster += [logo, Spacer(1, 2 * mm)]
        if th.avsandare:
            vanster.append(para(th.avsandare, stilar["huvud_v"]))
        hoger: list[Any] = [
            Paragraph(
                f'<font name="{fet}" color="#{us.accent}">{_pdf_text(th.dokumenttyp)}</font>',
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
        huvud = Table(
            [[vanster or "", hoger]], colWidths=[bredd * 0.55, bredd * 0.45], hAlign="LEFT"
        )
        huvud.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        flode += [
            huvud,
            Spacer(1, 3 * mm),
            HRFlowable(width="100%", thickness=1.2, color=accent_c, spaceAfter=7 * mm),
        ]
    flode.append(para(dok.titel, stilar["titel"]))
    vantande_figurer: list[Any] = []

    def tom_figurer() -> None:
        if vantande_figurer:
            flode.extend(vantande_figurer)
            vantande_figurer.clear()

    for b in dok.block:
        if not isinstance(b, Figur):
            tom_figurer()
        if isinstance(b, Rubrik):
            flode.append(para(b.text, rubrikstilar[min(b.niva, 3)]))
        elif isinstance(b, Stycke):
            p = para(b.text, stilar.get(b.stil, bas))
            if b.stil == "notis" and flode and isinstance(flode[-1], Paragraph):
                # påståendet och dess status hålls ihop på samma sida
                flode.append(KeepTogether([flode.pop(), p]))
            else:
                flode.append(p)
        elif isinstance(b, Punktlista):
            pst = ParagraphStyle("punkt", parent=bas, leftIndent=12, bulletIndent=2, spaceAfter=3)
            for punkt in b.punkter:
                flode.append(para(punkt, pst, bulletText="•"))
            flode.append(Spacer(1, 4))
        elif isinstance(b, Tabell):
            cst = stilar["cell_liten"] if b.liten else stilar["cell"]
            hst = ParagraphStyle("huvud", parent=cst, fontName=fet)
            data: list[list[Any]] = []
            if b.kolumner:
                data.append([para(k, hst) for k in b.kolumner])
            for rad in b.rader:
                celler: list[Any] = []
                for j, cell_text in enumerate(rad):
                    if b.ljus_kolumn == j and cell_text in LJUSFARG:
                        hexa, _namn = LJUSFARG[cell_text]
                        inre = Table(
                            [[Prick(hexa), para(_ljustext(dok, cell_text), cst)]],
                            colWidths=[4.2 * mm, bredd * b.bredder[j] - 4.2 * mm - 8],
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
                colWidths=[bredd * x for x in b.bredder],
                repeatRows=1 if b.kolumner else 0,
                hAlign="LEFT",
            )
            stil_lista: list[Any] = [
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, linje_c),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, linje_c),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
            if b.kolumner:
                stil_lista.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + _HUVUD_BG)))
            tab.setStyle(TableStyle(stil_lista))
            flode.append(tab)
            flode.append(Spacer(1, 9))
        elif isinstance(b, Figur):
            fil = Path(rapportmapp) / b.fil
            img = Image(str(fil))
            skala = (_FIGUR_MM * mm) / img.imageWidth
            img.drawWidth = img.imageWidth * skala
            img.drawHeight = img.imageHeight * skala
            img.hAlign = "LEFT"
            vantande_figurer.append(
                KeepTogether(
                    [Spacer(1, 4), img, Spacer(1, 3), para(b.bildtext, stilar["bildtext"])]
                )
            )
        elif isinstance(b, Sidbrytning):
            flode.append(PageBreak())
    tom_figurer()
    # Rubriker hålls ihop med det som följer (aldrig en ensam rubrik sist på en sida).
    hopslagen: list[Any] = []
    for f in reversed(flode):
        if hopslagen and isinstance(f, Paragraph) and f.style.name in ("h1", "h2", "h3"):
            nasta = hopslagen[-1]
            inre = list(nasta._content) if isinstance(nasta, KeepTogether) else [nasta]
            hopslagen[-1] = KeepTogether([f, *inre])  # platt: nästlade KeepTogether bryter sidan
        else:
            hopslagen.append(f)
    flode = list(reversed(hopslagen))

    class SidCanvas(Canvas):  # type: ignore[misc]
        """Ritar sidhuvud och sidfot när antalet sidor är känt ("Sida 2 (9)")."""

        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self._sidor: list[dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 - reportlabs namn
            self._sidor.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            antal = len(self._sidor)
            for sida in self._sidor:
                self.__dict__.update(sida)
                self._rita_ram(antal)
                super().showPage()
            super().save()

        def _rita_ram(self, antal: int) -> None:
            self.saveState()
            self.setFont(typsnitt, 7.5)
            self.setFillColor(svag_c)
            if self._pageNumber > 1 or th is None:
                self.drawString(marg, A4[1] - 12 * mm, dok.sidhuvud)
                self.setStrokeColor(linje_c)
                self.setLineWidth(0.4)
                self.line(marg, A4[1] - 13.5 * mm, A4[0] - marg, A4[1] - 13.5 * mm)
            self.drawString(marg, 11 * mm, dok.sidfot)
            self.drawRightString(
                A4[0] - marg, 11 * mm, us.sidnummer.format(sida=self._pageNumber, antal=antal)
            )
            self.restoreState()

    ut_p = Path(ut)
    nyckelord = typsnittsmetadata(dok)
    mall = SimpleDocTemplate(
        str(ut_p),
        pagesize=A4,
        leftMargin=marg,
        rightMargin=marg,
        topMargin=20 * mm,
        bottomMargin=19 * mm,
        title=dok.titel,
        author=(th.avsandare if th else "") or "Floden",
        subject=dok.sidfot,
        keywords=nyckelord,
        creator="Floden (reportlab)",
        lang=us.sprak,
        invariant=1,
    )
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
