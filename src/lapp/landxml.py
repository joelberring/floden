"""Ren stdlib LandXML TIN-importör och deterministisk barycentrisk rasterisering (T04)."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from lapp.kontrakt import GILTIGA_KOORDINATORDNINGAR, LappFel
from testsvit.k1_io import GeoTransformInfo

_BARY_EPS = 1e-9
_DET_EPS = 1e-12


@dataclass(frozen=True)
class LandXmlYta:
    """En triangulerad ytmodell (TIN) inläst från LandXML i (E, N, Z)-ordning."""

    namn: str
    punkter: dict[str, tuple[float, float, float]]
    trianglar: tuple[tuple[str, str, str], ...]
    koordinat_ordning: str = "NEZ"

    @property
    def antal_punkter(self) -> int:
        """Antal unika TIN-hörnpunkter."""
        return len(self.punkter)

    @property
    def antal_trianglar(self) -> int:
        """Antal trianglar (faces) i ytmodellen."""
        return len(self.trianglar)


def _lokalt_namn(tagg: str) -> str:
    if "}" in tagg:
        return tagg.rsplit("}", 1)[1]
    return tagg


def _hitta_barn(element: ET.Element, namn: str) -> list[ET.Element]:
    return [barn for barn in element if _lokalt_namn(barn.tag) == namn]


def _las_xml_rot(sokvag_eller_xml: str | Path) -> ET.Element:
    if isinstance(sokvag_eller_xml, str) and sokvag_eller_xml.lstrip().startswith("<"):
        xml_text = sokvag_eller_xml
    else:
        fil = Path(sokvag_eller_xml)
        if not fil.is_file():
            raise LappFel(f"LandXML-filen saknas: {fil}")
        try:
            xml_text = fil.read_text(encoding="utf-8")
        except OSError as exc:
            raise LappFel(f"Kunde inte läsa LandXML-filen {fil}: {exc}") from exc

    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LappFel(f"Ogiltig LandXML-syntax: {exc}") from exc


def las_landxml_tin(
    sokvag_eller_xml: str | Path,
    *,
    yta_namn: str | None = None,
    koordinat_ordning: str = "NEZ",
) -> LandXmlYta:
    """Läser en TIN-yta ur en LandXML-fil eller XML-sträng med ren stdlib.

    LandXML använder normalt koordinatordningen `Northing Easting Elevation` (`"NEZ"`).
    Genom `koordinat_ordning="ENZ"` kan även filer i `(E, N, Z)`-ordning läsas.
    Alla returnerade punkter lagras alltid internt som `(e_m, n_m, z_rh2000)`.
    """
    ordning = koordinat_ordning.strip().upper()
    if ordning not in GILTIGA_KOORDINATORDNINGAR:
        raise LappFel(
            f"Ogiltig koordinat_ordning '{koordinat_ordning}'. "
            f"Giltiga: {sorted(GILTIGA_KOORDINATORDNINGAR)}."
        )

    rot = _las_xml_rot(sokvag_eller_xml)
    if _lokalt_namn(rot.tag) != "LandXML":
        raise LappFel(f"Rot-elementet måste vara <LandXML>, fick <{_lokalt_namn(rot.tag)}>.")

    ytor: list[ET.Element] = []
    for surfaces_el in _hitta_barn(rot, "Surfaces"):
        ytor.extend(_hitta_barn(surfaces_el, "Surface"))

    if not ytor:
        raise LappFel("LandXML saknar <Surfaces>/<Surface>.")

    vald_yta: ET.Element | None = None
    if yta_namn is None:
        vald_yta = ytor[0]
    else:
        for surface_el in ytor:
            if surface_el.attrib.get("name") == yta_namn:
                vald_yta = surface_el
                break
        if vald_yta is None:
            tillgangliga = [el.attrib.get("name", "") for el in ytor]
            raise LappFel(
                f"LandXML saknar ytan '{yta_namn}'. Tillgängliga ytor: {tillgangliga}."
            )

    namn = str(vald_yta.attrib.get("name", "landxml-yta"))
    definitioner = _hitta_barn(vald_yta, "Definition")
    if not definitioner:
        raise LappFel(f"LandXML-ytan '{namn}' saknar <Definition>.")
    definition = definitioner[0]

    punkter: dict[str, tuple[float, float, float]] = {}
    for pnts_el in _hitta_barn(definition, "Pnts"):
        for p_el in _hitta_barn(pnts_el, "P"):
            pid = p_el.attrib.get("id")
            if not pid:
                raise LappFel(f"Punkt <P> saknar 'id'-attribut i LandXML-ytan '{namn}'.")
            delar = (p_el.text or "").split()
            if len(delar) != 3:
                raise LappFel(
                    f"Punkt '{pid}' i LandXML-ytan '{namn}' måste ha 3 tal, fick {len(delar)}."
                )
            try:
                v0, v1, v2 = float(delar[0]), float(delar[1]), float(delar[2])
            except ValueError as exc:
                raise LappFel(
                    f"Ogiltigt flyttal i punkt '{pid}' i LandXML-ytan '{namn}': {p_el.text!r}"
                ) from exc
            if not (math.isfinite(v0) and math.isfinite(v1) and math.isfinite(v2)):
                raise LappFel(f"Icke-ändliga koordinater i punkt '{pid}' i LandXML-ytan '{namn}'.")
            if ordning == "NEZ":
                n_m, e_m, z_rh2000 = v0, v1, v2
            else:
                e_m, n_m, z_rh2000 = v0, v1, v2
            punkter[str(pid)] = (e_m, n_m, z_rh2000)

    if not punkter:
        raise LappFel(f"LandXML-ytan '{namn}' saknar punkter under <Definition>/<Pnts>.")

    trianglar: list[tuple[str, str, str]] = []
    for faces_el in _hitta_barn(definition, "Faces"):
        for f_el in _hitta_barn(faces_el, "F"):
            # Enligt LandXML-specifikationen markerar attributet i="1" en osynlig/exkluderad yta.
            if f_el.attrib.get("i") == "1":
                continue
            delar = (f_el.text or "").split()
            if len(delar) != 3:
                raise LappFel(
                    f"Triangel <F> i LandXML-ytan '{namn}' måste ha 3 punkt-ID, fick {len(delar)}."
                )
            p0, p1, p2 = str(delar[0]), str(delar[1]), str(delar[2])
            for pid in (p0, p1, p2):
                if pid not in punkter:
                    raise LappFel(
                        f"Triangel i LandXML-ytan '{namn}' refererar okänt punkt-ID '{pid}'."
                    )
            trianglar.append((p0, p1, p2))

    if not trianglar:
        raise LappFel(f"LandXML-ytan '{namn}' saknar synliga trianglar under <Definition>/<Faces>.")

    return LandXmlYta(
        namn=namn,
        punkter=punkter,
        trianglar=tuple(trianglar),
        koordinat_ordning=ordning,
    )


def rastrera_landxml_tin(
    yta: LandXmlYta,
    geo: GeoTransformInfo,
) -> tuple[NDArray[np.float32], NDArray[np.bool_]]:
    """Rasteriserar en LandXML-TIN på `geo`:s rutnät med barycentrisk interpolation.

    Returnerar `(hojd_raster, tackt_mask)` där `hojd_raster` har `NaN` utanför TIN-ytan
    och `tackt_mask` är `True` för varje cell vars centrum faller inom minst en triangel.
    """
    hojd = geo.hojd
    bredd = geo.bredd
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)

    z_out = np.full((hojd, bredd), np.nan, dtype=np.float64)
    tackt = np.zeros((hojd, bredd), dtype=bool)

    e_vek = geo.x_min + (np.arange(bredd, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(hojd, dtype=np.float64) + 0.5) * dy

    for id0, id1, id2 in yta.trianglar:
        e0, n0, z0 = yta.punkter[id0]
        e1, n1, z1 = yta.punkter[id1]
        e2, n2, z2 = yta.punkter[id2]

        det = (n1 - n2) * (e0 - e2) + (e2 - e1) * (n0 - n2)
        if abs(det) <= _DET_EPS:
            continue

        e_min = min(e0, e1, e2)
        e_max = max(e0, e1, e2)
        n_min = min(n0, n1, n2)
        n_max = max(n0, n1, n2)

        if e_max < geo.x_min or e_min > geo.x_max or n_max < geo.y_min or n_min > geo.y_max:
            continue

        kol0 = max(0, math.floor((e_min - geo.x_min) / dx))
        kol1 = min(bredd, math.ceil((e_max - geo.x_min) / dx) + 1)
        rad0 = max(0, math.floor((geo.y_max - n_max) / dy))
        rad1 = min(hojd, math.ceil((geo.y_max - n_min) / dy) + 1)
        if kol1 <= kol0 or rad1 <= rad0:
            continue

        sub_e, sub_n = np.meshgrid(e_vek[kol0:kol1], n_vek[rad0:rad1])
        inv_det = 1.0 / det
        l0 = ((n1 - n2) * (sub_e - e2) + (e2 - e1) * (sub_n - n2)) * inv_det
        l1 = ((n2 - n0) * (sub_e - e2) + (e0 - e2) * (sub_n - n2)) * inv_det
        l2 = 1.0 - l0 - l1

        inuti = (l0 >= -_BARY_EPS) & (l1 >= -_BARY_EPS) & (l2 >= -_BARY_EPS)
        if not np.any(inuti):
            continue

        z_tri = l0 * z0 + l1 * z1 + l2 * z2
        z_sub = z_out[rad0:rad1, kol0:kol1]
        t_sub = tackt[rad0:rad1, kol0:kol1]
        z_sub[inuti] = z_tri[inuti]
        t_sub[inuti] = True

    return (
        np.asarray(z_out, dtype=np.float32),
        np.asarray(tackt, dtype=np.bool_),
    )
