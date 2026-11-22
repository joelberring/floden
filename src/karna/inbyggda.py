"""Inbyggda referenslösare för kärnan (T20).

- `vatten.statisk_fyllning`: global; sänkgraf + statisk fyllning (T02) på en K1-ruta.
  Finns som referens för hur en befintlig domänmodul kopplas in. Vattendomänen kan
  ersätta eller komplettera den med egna lösare i `<paket>/floden_losare.py`.
- `luft.vindkomfort`, `luft.halt`: globala lösare på K1 v2 (`karna/luft_losare.py`; luftdelen är
  MIT).
- `geometri.lutning`: rutvis med kort påverkansradie; lutning i grader ur markmodellen.
  Visar och testar den lokala omräkningen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, ClassVar

import numpy as np

from karna.analyslosare import Analyslosare, LosarFel, LosarKontext, LosarUtdata
from karna.luft_losare import LOSARE as _LUFT_LOSARE  # luftimporterna i den filen är lata


def _tal(parametrar: Mapping[str, Any], namn: str, *, minst: float | None = None) -> float:
    if namn not in parametrar:
        raise LosarFel(f"Parametern '{namn}' saknas.")
    v = parametrar[namn]
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise LosarFel(f"Parametern '{namn}' måste vara ett ändligt tal, fick {v!r}.")
    if minst is not None and float(v) < minst:
        raise LosarFel(f"Parametern '{namn}' måste vara >= {minst}, fick {v}.")
    return float(v)


_IDF_PARAMETRAR = {"aterkomsttid_ar", "klimatfaktor", "varaktighet_min", "regntyp"}
_STATISK_PARAMETRAR = {"regn_mm", "persistens_faktor", "idf"} | _IDF_PARAMETRAR


class StatiskFyllning(Analyslosare):
    """Sänkgraf och exakt volymbevarande statisk fyllning för ett regndjup."""

    namn: ClassVar[str] = "vatten.statisk_fyllning"
    doman: ClassVar[str] = "vatten"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = (
        "Statisk Fill–Spill–Merge-fyllning (T02) för ett regndjup i mm, direkt (regn_mm) eller "
        "ur en registrerad IDF-källa (idf, aterkomsttid_ar, klimatfaktor, varaktighet_min)."
    )
    utdataroller: ClassVar[tuple[str, ...]] = ("max_djup",)

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        okanda = sorted(set(parametrar) - _STATISK_PARAMETRAR)
        if okanda:
            raise LosarFel(f"{self.namn}: okända parametrar {okanda}.")
        if "persistens_faktor" in parametrar:
            persistens: float | None = _tal(parametrar, "persistens_faktor", minst=0.0)
        else:
            persistens = None
        if "idf" not in parametrar:
            fel_idf = sorted(set(parametrar) & _IDF_PARAMETRAR)
            if fel_idf:
                raise LosarFel(f"{self.namn}: {fel_idf} kräver parametern 'idf'.")
            return {
                "regn_mm": _tal(parametrar, "regn_mm", minst=0.0),
                "persistens_faktor": persistens,
            }
        # Regn ur en registrerad IDF-källa (gemensamt.regn): källa, återkomsttid, varaktighet och
        # klimatfaktor är uttryckliga parametrar och ingår i nyckeln och i K0-manifestet.
        from gemensamt.regn import RegnFel, hamta_idf_kalla

        if "regn_mm" in parametrar:
            raise LosarFel(f"{self.namn}: 'regn_mm' och 'idf' kan inte anges samtidigt.")
        kalla_id = parametrar["idf"]
        if not isinstance(kalla_id, str):
            raise LosarFel(f"Parametern 'idf' måste vara en källa som text, fick {kalla_id!r}.")
        typ = parametrar.get("regntyp", "block")
        if typ not in ("block", "cds"):
            raise LosarFel(f"Parametern 'regntyp' måste vara 'block' eller 'cds', fick {typ!r}.")
        try:
            kalla = hamta_idf_kalla(kalla_id)
            kf = (
                _tal(parametrar, "klimatfaktor", minst=0.0)
                if "klimatfaktor" in parametrar
                else kalla.standard_klimatfaktor
            )
            ut: dict[str, Any] = {
                "persistens_faktor": persistens,
                "idf": kalla_id,
                "regntyp": typ,
                "aterkomsttid_ar": _tal(parametrar, "aterkomsttid_ar", minst=0.0),
                "varaktighet_min": (
                    _tal(parametrar, "varaktighet_min", minst=0.0)
                    if "varaktighet_min" in parametrar
                    else 60.0
                ),
                "klimatfaktor": kf,
            }
            ut.update(kalla.manifest_parametrar(kf))
        except RegnFel as exc:
            raise LosarFel(f"{self.namn}: {exc}") from exc
        return ut

    @staticmethod
    def _regn_mm(parametrar: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
        """Regndjup (mm) och mätvärden om regnet: `regn_mm` direkt, annars ur IDF-källan."""
        if "idf" not in parametrar:
            return float(parametrar["regn_mm"]), {}
        from gemensamt.regn import RegnFel, skapa_regn_fran_idf_kalla

        try:
            serie = skapa_regn_fran_idf_kalla(
                str(parametrar["idf"]),
                float(parametrar["aterkomsttid_ar"]),
                klimatfaktor=float(parametrar["klimatfaktor"]),
                typ=str(parametrar["regntyp"]),
                varaktighet_min=float(parametrar["varaktighet_min"]),
            )
        except RegnFel as exc:
            raise LosarFel(f"vatten.statisk_fyllning: {exc}") from exc
        return float(serie.total_djup_mm), {
            "regn_mm": float(serie.total_djup_mm),
            "klimatfaktor": float(serie.klimatfaktor),
            "aterkomsttid_ar": float(parametrar["aterkomsttid_ar"]),
            "varaktighet_min": float(parametrar["varaktighet_min"]),
        }

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from sankgraf.fyllning import kor_statisk_k1_mapp
        from sankgraf.typer import slutsanka_matvarden, slutsanka_varningar
        from testsvit.k1_io import las_geotiff, skriv_geotiff

        rutor = kontext.scenario.rutor
        if len(rutor) != 1:
            raise LosarFel(
                f"{self.namn} stöder än så länge bara scenarier med en K1-ruta, fick {len(rutor)}."
                " Sammanfogning över rutgränser (sankgraf.sammanfoga_rutor) är inte inkopplad."
            )
        ruta = rutor[0]
        regn_mm, regn_matvarden = self._regn_mm(kontext.parametrar)
        _, res = kor_statisk_k1_mapp(
            ruta.mapp,
            regn_m=regn_mm / 1000.0,
            persistens_faktor=kontext.parametrar["persistens_faktor"],
        )
        _, geo = las_geotiff(ruta.mapp / "mark.tif")
        skriv_geotiff(
            kontext.utmapp / "max_djup.tif",
            np.asarray(res.djup_m, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
        )
        filer = {"max_djup": "max_djup.tif"}
        # Slutna områden (K2/v2): flaggraster, mått och varning bara när de finns, så att
        # resultat utan slutsänkor är oförändrade (därför samma lösarversion).
        if res.slutsankor and res.slutsanka_mask is not None:
            from sankgraf.typer import SLUTSANKA_FIL, skriv_slutsanka_raster

            skriv_slutsanka_raster(kontext.utmapp / SLUTSANKA_FIL, res.slutsanka_mask, geo)
            filer["slutsanka"] = SLUTSANKA_FIL
        return LosarUtdata(
            filer=filer,
            matvarden={
                "max_djup_m": float(np.nanmax(res.djup_m)),
                "lagrat_m3": float(res.lagrat_m3),
                "ut_m3": float(res.ut_m3),
                "volymfel_rel": float(res.volymfel_rel),
                **regn_matvarden,
                **slutsanka_matvarden(res.slutsankor),
            },
            varningar=slutsanka_varningar(res.slutsankor),
        )


class Lutning(Analyslosare):
    """Marklutning i grader (centrala differenser). Påverkansradie: en cell."""

    namn: ClassVar[str] = "geometri.lutning"
    doman: ClassVar[str] = "geometri"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = "Marklutning i grader ur K1 mark.tif, rutvis."
    utdataroller: ClassVar[tuple[str, ...]] = ("lutning",)

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        # Centrala differenser ser en cell åt varje håll. K1-rutor har 25 m överlapp, så
        # rutan räcker själv; radien 0 gör att grannrutor inte ingår i nyckeln.
        return 0.0

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from testsvit.k1_io import las_geotiff, skriv_geotiff

        if kontext.ruta is None:
            raise LosarFel(f"{self.namn} körs rutvis men fick ingen ruta.")
        z_raw, geo = las_geotiff(kontext.ruta.mapp / "mark.tif")
        z = np.asarray(z_raw, dtype=np.float64)
        dz_dy, dz_dx = np.gradient(z, geo.pixelstorlek_y, geo.pixelstorlek_x)
        grader = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
        skriv_geotiff(
            kontext.utmapp / "lutning.tif",
            np.asarray(grader, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
        )
        return LosarUtdata(
            filer={"lutning": "lutning.tif"},
            matvarden={"max_lutning_grader": float(np.nanmax(grader))},
        )


LOSARE: list[Analyslosare] = [StatiskFyllning(), Lutning(), *_LUFT_LOSARE]
