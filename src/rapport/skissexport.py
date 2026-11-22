"""Export av skissunderlaget till Word (.docx) och PDF (R1).

Båda formaten skrivs ur samma block (`rapport.skissmall`). Exporten görs bara efter en godkänd
kontroll (`RapportTjanst.exportera`); ett fel stoppar med `ExportStoppad`.

- **Word:** python-docx (ren Python). Arial, A4.
- **PDF:** reportlab (ren Python med färdiga hjul för macOS, Linux och Windows; inga
  systemberoenden som LibreOffice). Typsnittet är Bitstream Vera Sans, som följer med reportlab, så
  att PDF:en ser likadan ut på alla datorer och har svenska tecken, ², ³ och −. `invariant` gör
  filen byte-identisk för samma innehåll.
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
_SVAG = "52606D"
_LINJE = "C5CDD5"
_HUVUD_BG = "EEF2F5"


class ExportStoppad(Exception):
    """Kontrollen inför export underkände rapporten; ingen fil skrevs."""

    def __init__(self, kontroll: dict[str, Any]) -> None:
        from rapport.skisskontroll import kontroll_text

        self.kontroll = kontroll
        self.fel = [f for p in kontroll.get("punkter", []) for f in p.get("fel", [])]
        super().__init__("Exporten stoppades.\n" + kontroll_text(kontroll))


# ---------------------------------------------------------------------- Word


def skriv_word(dok: Dokument, ut: str | Path, rapportmapp: str | Path) -> Path:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    d = Document()
    sek = d.sections[0]
    sek.orientation = WD_ORIENT.PORTRAIT
    sek.page_width, sek.page_height = Mm(210), Mm(297)
    sek.left_margin = sek.right_margin = Mm(20)
    sek.top_margin, sek.bottom_margin = Mm(20), Mm(18)

    def farg(h: str) -> RGBColor:
        return RGBColor.from_string(h)

    normal = d.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    normal.font.color.rgb = farg(_TEXTFARG)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), "Arial")
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.15
    for niva, storlek in ((1, 14), (2, 12), (3, 10.5)):
        st = d.styles[f"Heading {niva}"]
        st.font.name = "Arial"
        st.font.size = Pt(storlek)
        st.font.bold = True
        st.font.italic = False
        st.font.color.rgb = farg(_TEXTFARG)
        st.paragraph_format.space_before = Pt(14 if niva == 1 else 9)
        st.paragraph_format.space_after = Pt(4)
        st.paragraph_format.keep_with_next = True
        st_rpr = st.element.get_or_add_rPr()
        st_fonts = st_rpr.find(qn("w:rFonts"))
        if st_fonts is None:
            st_fonts = OxmlElement("w:rFonts")
            st_rpr.append(st_fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            st_fonts.set(qn(attr), "Arial")
            st_fonts.attrib.pop(qn("w:asciiTheme"), None)
            st_fonts.attrib.pop(qn("w:hAnsiTheme"), None)
    titel_st = d.styles["Title"]
    titel_st.font.name = "Arial"
    titel_st.font.size = Pt(20)
    titel_st.font.color.rgb = farg(_TEXTFARG)

    def sidfalt(p: Any, kod: str) -> None:
        r = p.add_run()
        for typ, text in (("begin", None), (None, kod), ("end", None)):
            if typ:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), typ)
                r._r.append(el)
            else:
                it = OxmlElement("w:instrText")
                it.set(qn("xml:space"), "preserve")
                it.text = text
                r._r.append(it)

    hp = sek.header.paragraphs[0]
    hr = hp.add_run(dok.sidhuvud)
    hr.font.size = Pt(8)
    hr.font.color.rgb = farg(_SVAG)
    fp = sek.footer.paragraphs[0]
    fr = fp.add_run(dok.sidfot + "   Sida ")
    fr.font.size = Pt(8)
    fr.font.color.rgb = farg(_SVAG)
    sidfalt(fp, "PAGE")

    d.core_properties.title = dok.titel
    d.core_properties.author = "Floden"
    d.core_properties.comments = dok.sidfot

    t = d.add_paragraph(dok.titel, style="Title")
    t.paragraph_format.space_after = Pt(6)

    def skugga(cell: Any, hexa: str) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hexa)
        tc_pr.append(shd)

    def kanter(tabell: Any) -> None:
        tbl_pr = tabell._tbl.tblPr
        b = OxmlElement("w:tblBorders")
        for kant in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{kant}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), _LINJE)
            b.append(el)
        tbl_pr.append(b)

    bredd_mm = 170.0
    for b in dok.block:
        if isinstance(b, Rubrik):
            d.add_heading(b.text, level=min(b.niva, 3))
        elif isinstance(b, Stycke):
            p = d.add_paragraph()
            run = p.add_run(b.text)
            if b.stil == "ingress":
                run.font.size = Pt(11)
                p.paragraph_format.space_after = Pt(10)
            elif b.stil in ("liten", "notis"):
                run.font.size = Pt(8.5)
                run.font.color.rgb = farg(_SVAG)
                if b.stil == "notis":
                    p.paragraph_format.space_after = Pt(9)
        elif isinstance(b, Punktlista):
            for punkt in b.punkter:
                p = d.add_paragraph(style="List Bullet")
                p.add_run(punkt)
                p.paragraph_format.space_after = Pt(2)
        elif isinstance(b, Tabell):
            antal_kol = len(b.bredder)
            rader = ([b.kolumner] if b.kolumner else []) + list(b.rader)
            tab = d.add_table(rows=len(rader), cols=antal_kol)
            tab.alignment = WD_TABLE_ALIGNMENT.LEFT
            tab.autofit = False
            kanter(tab)
            layout = OxmlElement("w:tblLayout")
            layout.set(qn("w:type"), "fixed")
            tab._tbl.tblPr.append(layout)
            for j, kol in enumerate(tab.columns):
                kol.width = Mm(bredd_mm * b.bredder[j])
            storlek = Pt(8) if b.liten else Pt(9)
            for i, rad in enumerate(rader):
                for j, cell_text in enumerate(rad):
                    cell = tab.cell(i, j)
                    cell.width = Mm(bredd_mm * b.bredder[j])
                    p = cell.paragraphs[0]
                    p.paragraph_format.space_after = Pt(0)
                    huvud = b.kolumner is not None and i == 0
                    if (
                        not huvud
                        and b.ljus_kolumn is not None
                        and j == b.ljus_kolumn
                        and cell_text in LJUSFARG
                    ):
                        hexa, namn = LJUSFARG[cell_text]
                        prick = p.add_run("● ")
                        prick.font.color.rgb = farg(hexa)
                        prick.font.size = Pt(11)
                        r = p.add_run(f"{namn}: {LJUSTEXT[cell_text]}")
                        r.font.size = storlek
                        continue
                    r = p.add_run(cell_text)
                    r.font.size = storlek
                    if huvud or (b.kolumner is None and j == 0):
                        r.bold = True
                    if huvud:
                        skugga(cell, _HUVUD_BG)
            d.add_paragraph().paragraph_format.space_after = Pt(2)
        elif isinstance(b, Figur):
            p = d.add_paragraph()
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.space_after = Pt(2)
            p.add_run().add_picture(str(Path(rapportmapp) / b.fil), width=Mm(150))
            cap = d.add_paragraph()
            cr = cap.add_run(b.bildtext)
            cr.font.size = Pt(8.5)
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


def _registrera_typsnitt() -> tuple[str, str]:
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "FlodenVera" not in pdfmetrics.getRegisteredFontNames():
        mapp = Path(reportlab.__file__).parent / "fonts"
        pdfmetrics.registerFont(TTFont("FlodenVera", str(mapp / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("FlodenVera-Bold", str(mapp / "VeraBd.ttf")))
        pdfmetrics.registerFont(TTFont("FlodenVera-Italic", str(mapp / "VeraIt.ttf")))
        pdfmetrics.registerFont(TTFont("FlodenVera-BoldItalic", str(mapp / "VeraBI.ttf")))
        from reportlab.lib.fonts import addMapping

        addMapping("FlodenVera", 0, 0, "FlodenVera")
        addMapping("FlodenVera", 1, 0, "FlodenVera-Bold")
        addMapping("FlodenVera", 0, 1, "FlodenVera-Italic")
        addMapping("FlodenVera", 1, 1, "FlodenVera-BoldItalic")
    return "FlodenVera", "FlodenVera-Bold"


_UPPHOJT_TILL: Final[dict[str, str]] = dict(zip("⁻⁰¹²³⁴⁵⁶⁷⁸⁹", "−0123456789", strict=True))


def _pdf_text(s: str) -> str:
    """Escapar för reportlab och gör upphöjda exponenter till <super>."""
    s = escape(s)

    def sup(m: re.Match[str]) -> str:
        return "<super>" + "".join(_UPPHOJT_TILL[c] for c in m.group(0)) + "</super>"

    return re.sub("[⁻⁰¹⁴⁵⁶⁷⁸⁹][⁻⁰¹²³⁴⁵⁶⁷⁸⁹]*|(?<=10)[²³]", sup, s)


def skriv_pdf(dok: Dokument, ut: str | Path, rapportmapp: str | Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
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

    typsnitt, fet = _registrera_typsnitt()
    text_c = colors.HexColor("#" + _TEXTFARG)
    svag_c = colors.HexColor("#" + _SVAG)
    linje_c = colors.HexColor("#" + _LINJE)
    bas = ParagraphStyle(
        "bas",
        fontName=typsnitt,
        fontSize=9.5,
        leading=13.2,
        textColor=text_c,
        alignment=TA_LEFT,
        spaceAfter=5,
    )
    stilar = {
        "normal": bas,
        "ingress": ParagraphStyle("ingress", parent=bas, fontSize=11, leading=15, spaceAfter=9),
        "liten": ParagraphStyle("liten", parent=bas, fontSize=8, leading=10.5, textColor=svag_c),
        "notis": ParagraphStyle(
            "notis", parent=bas, fontSize=8, leading=10.5, textColor=svag_c, spaceAfter=9
        ),
        "titel": ParagraphStyle(
            "titel", parent=bas, fontName=fet, fontSize=19, leading=23, spaceAfter=8
        ),
        "cell": ParagraphStyle("cell", parent=bas, fontSize=8.5, leading=10.8, spaceAfter=0),
        "cell_liten": ParagraphStyle(
            "cell_liten", parent=bas, fontSize=7.3, leading=9.2, spaceAfter=0
        ),
        "bildtext": ParagraphStyle(
            "bildtext", parent=bas, fontSize=8, leading=10.5, textColor=svag_c, spaceAfter=10
        ),
    }
    rubrikstilar = {
        1: ParagraphStyle(
            "h1",
            parent=bas,
            fontName=fet,
            fontSize=13.5,
            leading=17,
            spaceBefore=12,
            spaceAfter=5,
        ),
        2: ParagraphStyle(
            "h2",
            parent=bas,
            fontName=fet,
            fontSize=11,
            leading=14,
            spaceBefore=9,
            spaceAfter=4,
        ),
        3: ParagraphStyle(
            "h3",
            parent=bas,
            fontName=fet,
            fontSize=9.5,
            leading=12.5,
            spaceBefore=7,
            spaceAfter=3,
        ),
    }

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

    bredd = A4[0] - 40 * mm
    flode: list[Any] = [Paragraph(_pdf_text(dok.titel), stilar["titel"])]
    vantande_figurer: list[Any] = []

    def tom_figurer() -> None:
        if vantande_figurer:
            flode.extend(vantande_figurer)
            vantande_figurer.clear()

    for b in dok.block:
        if not isinstance(b, Figur):
            tom_figurer()
        if isinstance(b, Rubrik):
            flode.append(Paragraph(_pdf_text(b.text), rubrikstilar[min(b.niva, 3)]))
        elif isinstance(b, Stycke):
            para = Paragraph(_pdf_text(b.text), stilar.get(b.stil, bas))
            if b.stil == "notis" and flode and isinstance(flode[-1], Paragraph):
                # påståendet och dess status hålls ihop på samma sida
                flode.append(KeepTogether([flode.pop(), para]))
            else:
                flode.append(para)
        elif isinstance(b, Punktlista):
            for punkt in b.punkter:
                flode.append(
                    Paragraph(
                        _pdf_text(punkt),
                        ParagraphStyle(
                            "punkt", parent=bas, leftIndent=11, bulletIndent=2, spaceAfter=2
                        ),
                        bulletText="•",
                    )
                )
            flode.append(Spacer(1, 3))
        elif isinstance(b, Tabell):
            cst = stilar["cell_liten"] if b.liten else stilar["cell"]
            hst = ParagraphStyle("huvud", parent=cst, fontName=fet)
            data: list[list[Any]] = []
            if b.kolumner:
                data.append([Paragraph(_pdf_text(k), hst) for k in b.kolumner])
            for rad in b.rader:
                celler: list[Any] = []
                for j, cell_text in enumerate(rad):
                    if b.ljus_kolumn == j and cell_text in LJUSFARG:
                        hexa, namn = LJUSFARG[cell_text]
                        inre = Table(
                            [
                                [
                                    Prick(hexa),
                                    Paragraph(_pdf_text(f"{namn}: {LJUSTEXT[cell_text]}"), cst),
                                ]
                            ],
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
                        celler.append(Paragraph(_pdf_text(cell_text), stil))
                data.append(celler)
            tab = Table(
                data,
                colWidths=[bredd * x for x in b.bredder],
                repeatRows=1 if b.kolumner else 0,
                hAlign="LEFT",
            )
            stil_lista: list[Any] = [
                ("GRID", (0, 0), (-1, -1), 0.4, linje_c),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
            if b.kolumner:
                stil_lista.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + _HUVUD_BG)))
            tab.setStyle(TableStyle(stil_lista))
            flode.append(tab)
            flode.append(Spacer(1, 7))
        elif isinstance(b, Figur):
            fil = Path(rapportmapp) / b.fil
            img = Image(str(fil))
            skala = (150 * mm) / img.imageWidth
            img.drawWidth = img.imageWidth * skala
            img.drawHeight = img.imageHeight * skala
            img.hAlign = "LEFT"
            vantande_figurer.append(
                KeepTogether(
                    [img, Spacer(1, 2), Paragraph(_pdf_text(b.bildtext), stilar["bildtext"])]
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

    def sida(canv: Any, doc: Any) -> None:
        canv.saveState()
        canv.setFont(typsnitt, 7.5)
        canv.setFillColor(svag_c)
        canv.drawString(20 * mm, A4[1] - 12 * mm, dok.sidhuvud)
        canv.setStrokeColor(linje_c)
        canv.setLineWidth(0.4)
        canv.line(20 * mm, A4[1] - 13.5 * mm, A4[0] - 20 * mm, A4[1] - 13.5 * mm)
        canv.drawString(20 * mm, 10 * mm, dok.sidfot)
        canv.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Sida {doc.page}")
        canv.restoreState()

    ut_p = Path(ut)
    mall = SimpleDocTemplate(
        str(ut_p),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=19 * mm,
        bottomMargin=17 * mm,
        title=dok.titel,
        author="Floden",
        subject=dok.sidfot,
        creator="Floden (reportlab)",
        invariant=1,
    )
    mall.build(flode, onFirstPage=sida, onLaterPages=sida)
    return ut_p


__all__ = ["LJUSFARG", "ExportStoppad", "skriv_pdf", "skriv_word"]
