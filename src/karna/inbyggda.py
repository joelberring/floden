"""Inbyggda referenslösare för kärnan (T20).

- `vatten.statisk_fyllning`: global; sänkgraf + statisk fyllning (T02) på en K1-ruta.
  Finns som referens för hur en befintlig domänmodul kopplas in. Vattendomänen kan
  ersätta eller komplettera den med egna lösare i `<paket>/floden_losare.py`.
- `vatten.dynamik`: global; händelsestyrd dynamik (T03) med takvatten och avtappning.
- `luft.vindkomfort`, `luft.halt`: globala lösare på K1 v2 (`karna/luft_losare.py`; luftdelen är
  MIT).
- `geometri.lutning`: rutvis med kort påverkansradie; lutning i grader ur markmodellen.
  Visar och testar den lokala omräkningen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
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
_VATTEN_ANTAGANDEN = {
    "takvatten", "takvatten_andel_ledning", "avtappning_mm_h", "avtappning_omraden",
}
_STATISK_PARAMETRAR = {"regn_mm", "persistens_faktor", "idf"} | _IDF_PARAMETRAR | _VATTEN_ANTAGANDEN


def validera_vattenantaganden(namn: str, parametrar: Mapping[str, Any]) -> dict[str, Any]:
    """Takvatten och avtappning (Joels och samordnarens beslut 2026-11-22), med standardvärden.

    `takvatten` (på/av, standard på), `takvatten_andel_ledning` (0–1, standard 0),
    `avtappning_mm_h` (standard 0: ledningsnätet antas fullt) och `avtappning_omraden`
    (valfri lista `{geometri, mm_h}`). Standardvärdena fylls i här så att de ingår i nyckeln och
    i manifestet.
    """
    from sankgraf.avtappning import AvtappningFel, validera_omraden

    tak = parametrar.get("takvatten", True)
    if not isinstance(tak, bool):
        raise LosarFel(f"{namn}: 'takvatten' måste vara true eller false, fick {tak!r}.")
    andel = (
        _tal(parametrar, "takvatten_andel_ledning", minst=0.0)
        if "takvatten_andel_ledning" in parametrar
        else 0.0
    )
    if andel > 1.0:
        raise LosarFel(f"{namn}: 'takvatten_andel_ledning' måste ligga i [0, 1], fick {andel}.")
    if not tak and "takvatten_andel_ledning" in parametrar:
        raise LosarFel(f"{namn}: 'takvatten_andel_ledning' kräver takvatten = true.")
    avt = (
        _tal(parametrar, "avtappning_mm_h", minst=0.0)
        if "avtappning_mm_h" in parametrar
        else 0.0
    )
    try:
        omr = validera_omraden(parametrar.get("avtappning_omraden"))
    except AvtappningFel as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    ut: dict[str, Any] = {
        "takvatten": tak,
        "takvatten_andel_ledning": andel,
        "avtappning_mm_h": avt,
        "avtappning_angiven": "avtappning_mm_h" in parametrar,
    }
    if omr:
        ut["avtappning_omraden"] = omr
    return ut


class StatiskFyllning(Analyslosare):
    """Sänkgraf och exakt volymbevarande statisk fyllning för ett regndjup."""

    namn: ClassVar[str] = "vatten.statisk_fyllning"
    doman: ClassVar[str] = "vatten"
    version: ClassVar[str] = "2"
    """v2 (2026-11-22): takvatten (standard på) och avtappning (standard 0 mm/h)."""
    beskrivning: ClassVar[str] = (
        "Statisk Fill–Spill–Merge-fyllning (T02) för ett regndjup i mm, direkt (regn_mm) eller "
        "ur en registrerad IDF-källa (idf, aterkomsttid_ar, klimatfaktor, varaktighet_min); "
        "takvatten (takvatten, takvatten_andel_ledning) och avtappning (avtappning_mm_h, "
        "avtappning_omraden eller indata avtappning_omraden)."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("avtappning_omraden",)
    utdataroller: ClassVar[tuple[str, ...]] = ("max_djup",)

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        okanda = sorted(set(parametrar) - _STATISK_PARAMETRAR)
        if okanda:
            raise LosarFel(f"{self.namn}: okända parametrar {okanda}.")
        vatten = validera_vattenantaganden(self.namn, parametrar)
        if "persistens_faktor" in parametrar:
            persistens: float | None = _tal(parametrar, "persistens_faktor", minst=0.0)
        else:
            persistens = None
        if "idf" not in parametrar:
            fel_idf = sorted(set(parametrar) & (_IDF_PARAMETRAR - {"varaktighet_min"}))
            if fel_idf:
                raise LosarFel(f"{self.namn}: {fel_idf} kräver parametern 'idf'.")
            ut0: dict[str, Any] = {
                "regn_mm": _tal(parametrar, "regn_mm", minst=0.0),
                "persistens_faktor": persistens,
                **vatten,
            }
            if "varaktighet_min" in parametrar:
                ut0["varaktighet_min"] = _tal(parametrar, "varaktighet_min", minst=0.0)
            return ut0
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
        ut.update(vatten)
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
        p = kontext.parametrar
        regn_mm, regn_matvarden = self._regn_mm(p)
        _, geo = las_geotiff(ruta.mapp / "mark.tif")
        avt_raster, avt_info, varaktighet_s = avtappning_for_kontext(self.namn, kontext, geo)
        graf, res = kor_statisk_k1_mapp(
            ruta.mapp,
            regn_m=regn_mm / 1000.0,
            persistens_faktor=p["persistens_faktor"],
            takvatten=bool(p["takvatten"]),
            takvatten_andel_ledning=float(p["takvatten_andel_ledning"]),
            avtappning_mm_h=avt_raster,
            varaktighet_s=varaktighet_s,
        )
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
                **vattenantaganden_matvarden(graf.takvatten, avt_info),
                "avtappat_m3": float(res.avtappat_m3),
                "volymbalans": res.volymbalans(),
            },
            varningar=(
                *slutsanka_varningar(res.slutsankor),
                *takvatten_varningar(graf.takvatten),
            ),
        )


def avtappning_for_kontext(
    namn: str, kontext: LosarKontext, geo: Any
) -> tuple[Any, dict[str, Any], float | None]:
    """Avtappningsraster (None om 0 överallt), beskrivning för manifestet och varaktighet (s)."""
    from sankgraf.avtappning import AvtappningFel, avtappning_raster, beskrivning, las_omraden_fil

    p = kontext.parametrar
    omr = list(p.get("avtappning_omraden", []))
    fil = kontext.indata.get("avtappning_omraden")
    try:
        if fil is not None:
            omr += las_omraden_fil(fil)
        raster = avtappning_raster(geo, float(p["avtappning_mm_h"]), omr)
    except AvtappningFel as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    info = beskrivning(
        float(p["avtappning_mm_h"]), omr, angiven=bool(p["avtappning_angiven"]),
        fil=None if fil is None else Path(fil).name,
    )
    if not np.any(raster > 0.0):
        return None, info, None
    if p.get("varaktighet_min") is None:
        raise LosarFel(
            f"{namn}: avtappning i statisk fyllning kräver regnets varaktighet "
            "('varaktighet_min', eller IDF-regn)."
        )
    return raster, info, float(p["varaktighet_min"]) * 60.0


def vattenantaganden_matvarden(tak: Any, avt_info: Mapping[str, Any]) -> dict[str, Any]:
    """Takvattnets och avtappningens antaganden och mått (webbens Antaganden läser dem)."""
    from sankgraf.takvatten import TAKVATTEN_AV_ANTAGANDE

    ut: dict[str, Any] = dict(avt_info)
    if tak is None:
        ut.update({"takvatten": False, "takvatten_antagande": TAKVATTEN_AV_ANTAGANDE})
    else:
        ut.update(tak.sammanfattning())
    return ut


def takvatten_varningar(tak: Any) -> tuple[str, ...]:
    """Synliga flaggor när takvattnet fick ersättas (inga tysta fel)."""
    if tak is None:
        return ()
    f = dict(tak.flaggor)
    ut = []
    if f.get("prov_i_oppning_undvikna"):
        ut.append(
            "Takvatten: inget takvatten lagt i portar/öppningar; det gick till fasaderna intill."
        )
    if f.get("byggnader_via_grannbyggnad") or f.get("byggnader_takfot_saknas_alla_fasader"):
        ut.append(
            "Takvatten: några byggnader saknar takfot mot mark; vattnet gick till andra fasader "
            "eller via grannbyggnaden."
        )
    if f.get("okand_takform_text"):
        ut.append("Takvatten: okänd text i takform; räknad som brutet tak (sadeltak).")
    return tuple(ut)


_DYNAMIK_PARAMETRAR = (
    {"regn_tid_s", "regn_mm_h", "slut_tid_s", "persistens_faktor", "idf"}
    | _IDF_PARAMETRAR
    | _VATTEN_ANTAGANDEN
)


class Dynamik(Analyslosare):
    """Händelsestyrd dynamik i sänkgrafen (T03) med takvatten och avtappning per område."""

    namn: ClassVar[str] = "vatten.dynamik"
    doman: ClassVar[str] = "vatten"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = (
        "Exakt händelsestyrd fyllning–överfall i sänkgrafen för ett regnförlopp (regn_tid_s + "
        "regn_mm_h, eller IDF-regn), med takvatten och avtappning (mm/h, per område)."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("avtappning_omraden",)
    utdataroller: ClassVar[tuple[str, ...]] = ("max_djup",)

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        okanda = sorted(set(parametrar) - _DYNAMIK_PARAMETRAR)
        if okanda:
            raise LosarFel(f"{self.namn}: okända parametrar {okanda}.")
        ut: dict[str, Any] = validera_vattenantaganden(self.namn, parametrar)
        ut["persistens_faktor"] = (
            _tal(parametrar, "persistens_faktor", minst=0.0)
            if "persistens_faktor" in parametrar
            else None
        )
        if "slut_tid_s" in parametrar:
            ut["slut_tid_s"] = _tal(parametrar, "slut_tid_s", minst=0.0)
        if "idf" in parametrar:
            if "regn_tid_s" in parametrar or "regn_mm_h" in parametrar:
                raise LosarFel(f"{self.namn}: ange antingen idf eller regn_tid_s/regn_mm_h.")
            idf = StatiskFyllning().validera_parametrar(
                {k: v for k, v in parametrar.items() if k in _IDF_PARAMETRAR | {"idf"}}
            )
            for k in ("takvatten", "takvatten_andel_ledning", "avtappning_mm_h",
                      "avtappning_angiven", "persistens_faktor"):
                idf.pop(k, None)
            ut.update(idf)
            return ut
        tider = parametrar.get("regn_tid_s")
        mm_h = parametrar.get("regn_mm_h")
        if not isinstance(tider, list | tuple) or not isinstance(mm_h, list | tuple):
            raise LosarFel(f"{self.namn}: kräver idf eller listorna regn_tid_s och regn_mm_h.")
        if len(tider) != len(mm_h) or len(tider) < 2:
            raise LosarFel(f"{self.namn}: regn_tid_s och regn_mm_h måste vara lika långa (>= 2).")
        ut["regn_tid_s"] = [_tal({"t": t}, "t", minst=0.0) for t in tider]
        ut["regn_mm_h"] = [_tal({"i": i}, "i", minst=0.0) for i in mm_h]
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
        from dynamik.nodmodell import DynamikFel
        from sankgraf.fyllning import kor_statisk_k1_mapp
        from sankgraf.typer import SLUTSANKA_VARNING
        from testsvit.k1_io import las_geotiff, skriv_geotiff

        rutor = kontext.scenario.rutor
        if len(rutor) != 1:
            raise LosarFel(f"{self.namn} stöder bara scenarier med en K1-ruta, fick {len(rutor)}.")
        ruta = rutor[0]
        p = kontext.parametrar
        if "idf" in p:
            from gemensamt.regn import RegnFel, skapa_regn_fran_idf_kalla

            try:
                serie = skapa_regn_fran_idf_kalla(
                    str(p["idf"]), float(p["aterkomsttid_ar"]),
                    klimatfaktor=float(p["klimatfaktor"]), typ=str(p["regntyp"]),
                    varaktighet_min=float(p["varaktighet_min"]),
                )
            except RegnFel as exc:
                raise LosarFel(f"{self.namn}: {exc}") from exc
            tider, mm_h = list(serie.tid_s), list(serie.intensitet_mm_h)
        else:
            tider, mm_h = list(p["regn_tid_s"]), list(p["regn_mm_h"])
        _, geo = las_geotiff(ruta.mapp / "mark.tif")
        # Avtappningen i dynamiken behöver ingen varaktighet; den gäller hela förloppet.
        p_avt = dict(p)
        p_avt.setdefault("varaktighet_min", 1.0)
        avt_raster, avt_info, _ = avtappning_for_kontext(
            self.namn, LosarKontext(
                utmapp=kontext.utmapp, scenario=kontext.scenario, parametrar=p_avt,
                indata=kontext.indata, beroenden=kontext.beroenden,
            ), geo,
        )
        graf, _ = kor_statisk_k1_mapp(
            ruta.mapp, regn_m=0.0, persistens_faktor=p["persistens_faktor"],
            takvatten=bool(p["takvatten"]),
            takvatten_andel_ledning=float(p["takvatten_andel_ledning"]),
        )
        try:
            res = kor_dynamik_handelsestyrd(
                graf, tider, mm_h, slut_tid_s=p.get("slut_tid_s"), avtappning_mm_h=avt_raster
            )
        except DynamikFel as exc:
            raise LosarFel(f"{self.namn}: {exc}") from exc
        skriv_geotiff(
            kontext.utmapp / "max_djup.tif", np.asarray(res.max_djup_m, dtype=np.float32),
            x_min=geo.x_min, y_max=geo.y_max, pixelstorlek=geo.pixelstorlek_x,
        )
        return LosarUtdata(
            filer={"max_djup": "max_djup.tif"},
            matvarden={
                "max_djup_m": float(np.nanmax(res.max_djup_m)),
                "in_m3": float(res.in_m3),
                "lagrat_m3": float(res.lagrat_m3),
                "ut_m3": float(res.ut_m3),
                "avtappat_m3": float(res.avtappat_m3),
                "volymfel_rel": float(res.volymfel_rel),
                "slut_tid_s": float(res.tider_s[-1]) if res.tider_s else 0.0,
                **vattenantaganden_matvarden(graf.takvatten, avt_info),
                "volymbalans": res.volymbalans(),
            },
            varningar=(
                *((SLUTSANKA_VARNING,) if res.slutsankor else ()),
                *takvatten_varningar(graf.takvatten),
            ),
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


LOSARE: list[Analyslosare] = [StatiskFyllning(), Dynamik(), Lutning(), *_LUFT_LOSARE]
