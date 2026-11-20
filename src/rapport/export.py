"""Export av verifierade rapporter till Markdown, HTML5/PDF, Word (.docx) och JSON (T08)."""

from __future__ import annotations

import html
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, RunManifest, skriv_manifest
from rapport.kontroll import KontrollResultat, kontrollera_rapport
from rapport.mallar import rendera_mall
from rapport.underlag import RapportUnderlag, skriv_underlag_json

GILTIGA_EXPORTFORMAT: Final[tuple[str, ...]] = ("md", "html", "docx")

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_ZIP_TID = (2026, 1, 1, 0, 0, 0)

_RELS_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
_DOC_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_DOC_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    f'  <Default Extension="rels" ContentType="{_RELS_CONTENT_TYPE}"/>\n'
    '  <Default Extension="xml" ContentType="application/xml"/>\n'
    f'  <Override PartName="/word/document.xml" ContentType="{_DOC_MAIN_CONTENT_TYPE}"/>\n'
    "</Types>\n"
)

_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    f'  <Relationship Id="rId1" Type="{_DOC_REL_TYPE}" Target="word/document.xml"/>\n'
    "</Relationships>\n"
)


class RapportExportFel(Exception):
    """Fel vid export av rapportfiler."""


@dataclass(frozen=True)
class RapportResultat:
    """Resultat av verifierad rapportgenerering och export."""

    korning_id: str
    kontroll: KontrollResultat
    filer: dict[str, Path]
    underlag_fil: Path
    manifest_fil: Path


def _formatera_inline_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def _markdown_till_html_kropp(md_text: str) -> str:
    rader = md_text.splitlines()
    html_delar: list[str] = []
    i = 0
    while i < len(rader):
        rad = rader[i]
        strippad = rad.strip()
        if not strippad:
            i += 1
            continue

        # Rubrik
        rubrik_match = re.match(r"^(#{1,6})\s+(.*)$", strippad)
        if rubrik_match:
            niva = len(rubrik_match.group(1))
            innehall = _formatera_inline_html(rubrik_match.group(2))
            html_delar.append(f"<h{niva}>{innehall}</h{niva}>")
            i += 1
            continue

        # Bild ![alt](src)
        bild_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", strippad)
        if bild_match:
            alt = html.escape(bild_match.group(1))
            src = html.escape(bild_match.group(2))
            html_delar.append(
                f'<figure><img src="{src}" alt="{alt}" /><figcaption>{alt}</figcaption></figure>'
            )
            i += 1
            continue

        # Markdown-tabell
        if strippad.startswith("|") and i + 1 < len(rader) and rader[i + 1].strip().startswith("|"):
            huvud_celler = [c.strip() for c in strippad.strip("|").split("|")]
            i += 2  # hoppa över separatorraden
            kroppsrader: list[list[str]] = []
            while i < len(rader) and rader[i].strip().startswith("|"):
                kroppsrader.append([c.strip() for c in rader[i].strip().strip("|").split("|")])
                i += 1
            th_html = "".join(f"<th>{_formatera_inline_html(c)}</th>" for c in huvud_celler)
            tr_list = []
            for row in kroppsrader:
                td_html = "".join(f"<td>{_formatera_inline_html(c)}</td>" for c in row)
                tr_list.append(f"<tr>{td_html}</tr>")
            html_delar.append(
                f"<table><thead><tr>{th_html}</tr></thead><tbody>{''.join(tr_list)}</tbody></table>"
            )
            continue

        # Punktlista
        if strippad.startswith("- "):
            punkter: list[str] = []
            while i < len(rader) and rader[i].strip().startswith("- "):
                punkter.append(f"<li>{_formatera_inline_html(rader[i].strip()[2:])}</li>")
                i += 1
            html_delar.append(f"<ul>{''.join(punkter)}</ul>")
            continue

        # Vanligt stycke (samla ihop efterföljande vanliga rader)
        stycke_rader: list[str] = []
        while i < len(rader):
            nu = rader[i].strip()
            if (
                not nu
                or nu.startswith("#")
                or nu.startswith("|")
                or nu.startswith("- ")
                or nu.startswith("![")
            ):
                break
            stycke_rader.append(_formatera_inline_html(nu))
            i += 1
        html_delar.append(f"<p>{'<br />'.join(stycke_rader)}</p>")

    return "\n".join(html_delar)


def rendera_html_dokument(md_text: str, underlag: RapportUnderlag) -> str:
    """Renderar en självständig, utskriftsvänlig HTML5-rapport (redo för PDF-utskrift)."""
    titel_esc = html.escape(underlag.titel)
    korning_esc = html.escape(underlag.korning_id)
    kropp = _markdown_till_html_kropp(md_text)
    return f"""<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{titel_esc}</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: "DejaVu Sans", Arial, Helvetica, sans-serif;
      line-height: 1.5;
      color: #1c242b;
    }}
    body {{
      max-width: 900px;
      margin: 2rem auto;
      padding: 0 1.5rem 3rem;
      background: #ffffff;
    }}
    header.rapport-meta {{
      border-bottom: 2px solid #1b4d7e;
      margin-bottom: 1.5rem;
      padding-bottom: 0.5rem;
      font-size: 0.85rem;
      color: #4a5a68;
    }}
    h1, h2, h3 {{
      color: #133b63;
      page-break-after: avoid;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      margin: 1rem 0 1.5rem;
      font-size: 0.92rem;
    }}
    th, td {{
      border: 1px solid #c9d4de;
      padding: 0.45rem 0.65rem;
      text-align: left;
    }}
    th {{
      background: #eef4f9;
    }}
    figure {{
      margin: 1.2rem 0;
      page-break-inside: avoid;
    }}
    figure img {{
      max-width: 100%;
      height: auto;
      border: 1px solid #d0d7de;
    }}
    figcaption {{
      font-size: 0.85rem;
      color: #4a5a68;
      margin-top: 0.25rem;
    }}
    code {{
      font-family: "DejaVu Sans Mono", monospace;
      background: #f4f6f8;
      padding: 0.1rem 0.3rem;
      border-radius: 3px;
      font-size: 0.9em;
    }}
    @media print {{
      @page {{
        size: A4;
        margin: 18mm;
      }}
      body {{
        max-width: none;
        margin: 0;
        padding: 0;
      }}
    }}
  </style>
</head>
<body>
  <header class="rapport-meta">
    Floden rapport | Körnings-id: <code>{korning_esc}</code>
  </header>
  <main>
{kropp}
  </main>
</body>
</html>
"""


def _w_tag(namn: str) -> str:
    return f"{{{_W_NS}}}{namn}"


def _lagg_till_stycke(
    body: ET.Element,
    text: str,
    *,
    stil: str | None = None,
    fet: bool = False,
) -> None:
    p = ET.SubElement(body, _w_tag("p"))
    if stil is not None:
        p_pr = ET.SubElement(p, _w_tag("pPr"))
        ET.SubElement(p_pr, _w_tag("pStyle"), {_w_tag("val"): stil})
    r = ET.SubElement(p, _w_tag("r"))
    if fet:
        r_pr = ET.SubElement(r, _w_tag("rPr"))
        ET.SubElement(r_pr, _w_tag("b"))
    t = ET.SubElement(r, _w_tag("t"))
    t.set(f"{{{_XML_NS}}}space", "preserve")
    t.text = text


def _lagg_till_tabell(body: ET.Element, rader: list[list[str]]) -> None:
    tbl = ET.SubElement(body, _w_tag("tbl"))
    tbl_pr = ET.SubElement(tbl, _w_tag("tblPr"))
    ET.SubElement(tbl_pr, _w_tag("tblW"), {_w_tag("w"): "0", _w_tag("type"): "auto"})
    borders = ET.SubElement(tbl_pr, _w_tag("tblBorders"))
    kant_attrs = {
        _w_tag("val"): "single",
        _w_tag("sz"): "4",
        _w_tag("space"): "0",
        _w_tag("color"): "CCCCCC",
    }
    for kant in ("top", "left", "bottom", "right", "insideH", "insideV"):
        ET.SubElement(borders, _w_tag(kant), kant_attrs)

    for rad_idx, rad in enumerate(rader):
        tr = ET.SubElement(tbl, _w_tag("tr"))
        for cell_text in rad:
            tc = ET.SubElement(tr, _w_tag("tc"))
            p = ET.SubElement(tc, _w_tag("p"))
            r = ET.SubElement(p, _w_tag("r"))
            if rad_idx == 0:
                r_pr = ET.SubElement(r, _w_tag("rPr"))
                ET.SubElement(r_pr, _w_tag("b"))
            t = ET.SubElement(r, _w_tag("t"))
            t.set(f"{{{_XML_NS}}}space", "preserve")
            t.text = cell_text


def _bygg_document_xml(md_text: str, underlag: RapportUnderlag) -> bytes:
    ET.register_namespace("w", _W_NS)
    ET.register_namespace("r", _R_NS)

    doc = ET.Element(_w_tag("document"))
    body = ET.SubElement(doc, _w_tag("body"))

    _lagg_till_stycke(
        body,
        f"Körnings-id: {underlag.korning_id} | Kodversion: {underlag.manifest.kodversion}",
        fet=True,
    )

    rader = md_text.splitlines()
    i = 0
    while i < len(rader):
        strippad = rader[i].strip()
        if not strippad:
            i += 1
            continue

        rubrik_match = re.match(r"^(#{1,6})\s+(.*)$", strippad)
        if rubrik_match:
            niva = min(len(rubrik_match.group(1)), 3)
            stil = f"Heading{niva}"
            _lagg_till_stycke(body, rubrik_match.group(2), stil=stil, fet=True)
            i += 1
            continue

        if strippad.startswith("|") and i + 1 < len(rader) and rader[i + 1].strip().startswith("|"):
            tabell_rader: list[list[str]] = [
                [c.strip() for c in strippad.strip("|").split("|")]
            ]
            i += 2
            while i < len(rader) and rader[i].strip().startswith("|"):
                tabell_rader.append([c.strip() for c in rader[i].strip().strip("|").split("|")])
                i += 1
            _lagg_till_tabell(body, tabell_rader)
            continue

        if strippad.startswith("- "):
            while i < len(rader) and rader[i].strip().startswith("- "):
                rent = rader[i].strip()[2:].replace("**", "").replace("`", "")
                _lagg_till_stycke(body, f"• {rent}")
                i += 1
            continue

        bild_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", strippad)
        if bild_match:
            _lagg_till_stycke(body, f"Karta: {bild_match.group(1)} ({bild_match.group(2)})")
            i += 1
            continue

        rent_stycke = strippad.replace("**", "").replace("`", "")
        _lagg_till_stycke(body, rent_stycke)
        i += 1

    ET.SubElement(body, _w_tag("sectPr"))
    xml_bytes: bytes = ET.tostring(doc, encoding="utf-8", xml_declaration=True)
    return xml_bytes


def skriv_docx(md_text: str, underlag: RapportUnderlag, ut_fil: str | Path) -> Path:
    """Skriver en deterministisk, giltig Word `.docx`-fil med ren stdlib `zipfile` + OOXML."""
    mal = Path(ut_fil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    document_xml = _bygg_document_xml(md_text, underlag)

    poster: tuple[tuple[str, bytes], ...] = (
        ("[Content_Types].xml", _CONTENT_TYPES_XML.encode("utf-8")),
        ("_rels/.rels", _RELS_XML.encode("utf-8")),
        ("word/document.xml", document_xml),
    )

    with zipfile.ZipFile(mal, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arkivnamn, innehall in poster:
            info = zipfile.ZipInfo(filename=arkivnamn, date_time=_ZIP_TID)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, innehall)

    return mal


def generera_rapport(
    underlag: RapportUnderlag,
    ut_mapp: str | Path,
    *,
    text_leverantor: Callable[[RapportUnderlag], str] | None = None,
    kart_rot: str | Path | None = None,
    format_lista: Iterable[str] = ("md", "html", "docx"),
) -> RapportResultat:
    """Renderar, kontrollerar och exporterar rapport + `underlag.json` + `run.json`.

    Kastar `RapportKontrollFel` innan någon fil skrivs om kontrollen underkänns.
    """
    valda_format = tuple(format_lista)
    for fmt in valda_format:
        if fmt not in GILTIGA_EXPORTFORMAT:
            raise RapportExportFel(
                f"Ogiltigt exportformat '{fmt}'. Tillåtna: {', '.join(GILTIGA_EXPORTFORMAT)}."
            )

    md_text = rendera_mall(underlag, text_leverantor=text_leverantor)
    effektiv_kart_rot = Path(kart_rot) if kart_rot is not None else Path(ut_mapp)
    kontroll = kontrollera_rapport(md_text, underlag, kart_rot=effektiv_kart_rot)

    mal_mapp = Path(ut_mapp)
    mal_mapp.mkdir(parents=True, exist_ok=True)

    exporterade: dict[str, Path] = {}
    if "md" in valda_format:
        md_fil = mal_mapp / "rapport.md"
        md_fil.write_text(md_text, encoding="utf-8")
        exporterade["md"] = md_fil

    if "html" in valda_format:
        html_fil = mal_mapp / "rapport.html"
        html_fil.write_text(rendera_html_dokument(md_text, underlag), encoding="utf-8")
        exporterade["html"] = html_fil

    if "docx" in valda_format:
        docx_fil = mal_mapp / "rapport.docx"
        skriv_docx(md_text, underlag, docx_fil)
        exporterade["docx"] = docx_fil

    underlag_fil = skriv_underlag_json(underlag, mal_mapp / "underlag.json")

    utdata_poster = [
        FilPost(roll=f"rapport_{fmt}", fil=fil.name, sha256=sha256_fil(fil))
        for fmt, fil in sorted(exporterade.items())
    ]
    utdata_poster.append(
        FilPost(roll="rapport_underlag", fil=underlag_fil.name, sha256=sha256_fil(underlag_fil))
    )

    rapport_manifest = RunManifest(
        modul="rapport",
        kodversion=underlag.manifest.kodversion,
        start=underlag.manifest.start,
        indata=list(underlag.manifest.indata),
        parametrar={
            **underlag.manifest.parametrar,
            "rapport_typ": underlag.rapport_typ,
            "korning_id": underlag.korning_id,
            "exportformat": list(valda_format),
        },
        utdata=utdata_poster,
        matvarden=dict(underlag.tal),
        varningar=list(underlag.manifest.varningar),
    )
    manifest_fil = skriv_manifest(rapport_manifest, mal_mapp / "run.json")

    return RapportResultat(
        korning_id=underlag.korning_id,
        kontroll=kontroll,
        filer=exporterade,
        underlag_fil=underlag_fil,
        manifest_fil=manifest_fil,
    )
