"""Inbyggda referenslösare för kärnan (T20).

- `vatten.statisk_fyllning`: global; sänkgraf + statisk fyllning (T02) på en eller flera K1-rutor.
  Finns som referens för hur en befintlig domänmodul kopplas in. Vattendomänen kan
  ersätta eller komplettera den med egna lösare i `<paket>/floden_losare.py`.
- `vatten.dynamik`: global; händelsestyrd dynamik (T03) med takvatten och avtappning (en eller flera
  K1-rutor).
- `vatten.2d`: global; 2D lokal tröghet (Bates 2010 / de Almeida, T05) med automatisk Rust-dispatch
  och valfri 1D-rännstenskoppling.
- `luft.vindkomfort`, `luft.halt`: globala lösare på K1 v2 (`karna/luft_losare.py`; luftdelen är
  MIT).
- `luft.mkn`: global; monotont MKN-intervall (PM10) ur en given timserie och en bakgrund
  (`karna/luft_losare.py`; ingen spridningsberäkning; ger belägg för rapporttal).
- `ekologi.konnektivitet`: global; ekologisk konnektivitet (`karna/ekologi_losare.py`, K44/K45).
- `geometri.lutning`: rutvis med kort påverkansradie; lutning i grader ur markmodellen.
  Visar och testar den lokala omräkningen.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray

from karna.analyslosare import Analyslosare, LosarFel, LosarKontext, LosarUtdata
from karna.ekologi_losare import LOSARE as _EKOLOGI_LOSARE
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
        from sankgraf.fyllning import statisk_fyllning
        from sankgraf.typer import slutsanka_matvarden, slutsanka_varningar
        from testsvit.k1_io import skriv_geotiff

        rutor = kontext.scenario.rutor
        p = kontext.parametrar
        regn_mm, regn_matvarden = self._regn_mm(p)
        graf, geo = _bygg_sankgraf_for_rutor(
            self.namn,
            rutor,
            persistens_faktor=p["persistens_faktor"],
            takvatten=bool(p["takvatten"]),
            takvatten_andel_ledning=float(p["takvatten_andel_ledning"]),
        )
        avt_raster, avt_info, varaktighet_s = avtappning_for_kontext(self.namn, kontext, geo)
        res = statisk_fyllning(
            graf,
            regn_mm / 1000.0,
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
    version: ClassVar[str] = "3"
    """v3: exporterar även lättviktig `tidsserie.json` (floden-tidsserie/v1) och `tidssteg.npz`."""
    beskrivning: ClassVar[str] = (
        "Exakt händelsestyrd fyllning–överfall i sänkgrafen för ett regnförlopp (regn_tid_s + "
        "regn_mm_h, eller IDF-regn), med takvatten, avtappning (mm/h, per område), ankomsttider "
        "till 0,1 m och 0,2 m samt tidsserie och tidssteg."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("avtappning_omraden",)
    utdataroller: ClassVar[tuple[str, ...]] = (
        "max_djup",
        "tid_till_0_1m",
        "tid_till_0_2m",
        "tidsserie",
        "tidssteg",
    )

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
        if "regntyp" in parametrar:
            typ = parametrar["regntyp"]
            if typ not in ("block", "cds"):
                raise LosarFel(
                    f"Parametern 'regntyp' måste vara 'block' eller 'cds', fick {typ!r}."
                )
            ut["regntyp"] = typ
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
        from dynamik.nodmodell import DynamikFel
        from sankgraf.typer import SLUTSANKA_VARNING
        from testsvit.k1_io import skriv_geotiff

        rutor = kontext.scenario.rutor
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
            regntyp = str(p["regntyp"])
            varaktighet_min = float(p["varaktighet_min"])
        else:
            tider, mm_h = list(p["regn_tid_s"]), list(p["regn_mm_h"])
            if "regntyp" in p:
                regntyp = str(p["regntyp"])
            else:
                regntyp = "block" if (max(mm_h) - min(mm_h)) <= 1e-9 else "anpassad"
            varaktighet_min = float(tider[-1] - tider[0]) / 60.0
        graf, geo = _bygg_sankgraf_for_rutor(
            self.namn,
            rutor,
            persistens_faktor=p["persistens_faktor"],
            takvatten=bool(p["takvatten"]),
            takvatten_andel_ledning=float(p["takvatten_andel_ledning"]),
        )
        # Avtappningen i dynamiken behöver ingen varaktighet; den gäller hela förloppet.
        p_avt = dict(p)
        p_avt.setdefault("varaktighet_min", 1.0)
        avt_raster, avt_info, _ = avtappning_for_kontext(
            self.namn, LosarKontext(
                utmapp=kontext.utmapp, scenario=kontext.scenario, parametrar=p_avt,
                indata=kontext.indata, beroenden=kontext.beroenden,
            ), geo,
        )
        try:
            res = kor_dynamik_handelsestyrd(
                graf, tider, mm_h, slut_tid_s=p.get("slut_tid_s"), avtappning_mm_h=avt_raster
            )
        except DynamikFel as exc:
            raise LosarFel(f"{self.namn}: {exc}") from exc
        from gemensamt.cog import skriv_komprimerad_cog

        skriv_geotiff(
            kontext.utmapp / "max_djup.tif", np.asarray(res.max_djup_m, dtype=np.float32),
            x_min=geo.x_min, y_max=geo.y_max, pixelstorlek=geo.pixelstorlek_x,
        )
        skriv_komprimerad_cog(
            kontext.utmapp / "tid_till_0_1m.tif",
            np.asarray(res.tid_till_0_1m_s, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
            epsg=geo.epsg,
            nodata=float("nan"),
        )
        skriv_komprimerad_cog(
            kontext.utmapp / "tid_till_0_2m.tif",
            np.asarray(res.tid_till_0_2m_s, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
            epsg=geo.epsg,
            nodata=float("nan"),
        )
        total_regn_mm = _skriv_dynamik_tidsserie_och_tidssteg(
            kontext.utmapp,
            graf,
            res,
            tider,
            mm_h,
            slut_tid_s=p.get("slut_tid_s"),
            regntyp=regntyp,
            varaktighet_min=varaktighet_min,
            losare=self.namn,
        )
        return LosarUtdata(
            filer={
                "max_djup": "max_djup.tif",
                "tid_till_0_1m": "tid_till_0_1m.tif",
                "tid_till_0_2m": "tid_till_0_2m.tif",
                "tidsserie": "tidsserie.json",
                "tidssteg": "tidssteg.npz",
            },
            matvarden={
                "max_djup_m": float(np.nanmax(res.max_djup_m)),
                "regn_mm": float(total_regn_mm),
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


_TIDSSERIE_KONTRAKT = "floden-tidsserie/v1"
_TIDSSERIE_BAS_PUNKTER = 41
_TIDSSTEG_BAS_ANTAL = 12
_TIDSSTEG_MAX_PX = 1024


def _skriv_dynamik_tidsserie_och_tidssteg(
    utmapp: Path,
    graf: Any,
    res: Any,
    tider: list[float],
    mm_h: list[float],
    *,
    slut_tid_s: float | None,
    regntyp: str,
    varaktighet_min: float,
    losare: str,
) -> float:
    """Skriver `tidsserie.json` (floden-tidsserie/v1) och kvantiserad `tidssteg.npz` (uint16 mm).

    All beräkning är deterministisk och vektoriserad (inga pixelloopar). Returnerar totalt
    regndjup i mm.
    """
    from dynamik.nodmodell import berakna_doman_areor, skapa_regn_intervall
    from sankgraf.typer import MARKSLAG_BYGGNAD

    raw_iv, total_regn_m = skapa_regn_intervall(tider, mm_h, slut_tid_s=slut_tid_s)
    total_regn_mm = float(total_regn_m) * 1000.0
    t_start = float(res.tider_s[0]) if res.tider_s else float(tider[0])
    t_slut = (
        float(res.tider_s[-1])
        if res.tider_s
        else (float(slut_tid_s) if slut_tid_s is not None else float(tider[-1]))
    )

    # 1. Välj 25–60 samplade tidpunkter över [t_start, t_slut] inkl. regntopp
    regn_topp_s: float | None = None
    if raw_iv:
        topp_iv = max(
            raw_iv, key=lambda iv: max(iv.intensitet_start_mm_h, iv.intensitet_slut_mm_h)
        )
        if topp_iv.ar_konstant:
            regn_topp_s = 0.5 * (topp_iv.t_start_s + topp_iv.t_slut_s)
        elif topp_iv.intensitet_start_mm_h >= topp_iv.intensitet_slut_mm_h:
            regn_topp_s = topp_iv.t_start_s
        else:
            regn_topp_s = topp_iv.t_slut_s

    if t_slut > t_start:
        bas_t = [float(x) for x in np.linspace(t_start, t_slut, _TIDSSERIE_BAS_PUNKTER)]
        if (
            regn_topp_s is not None
            and t_start <= regn_topp_s <= t_slut
            and all(abs(regn_topp_s - x) > 1e-6 for x in bas_t)
        ):
            bas_t.append(regn_topp_s)
            bas_t.sort()
    else:
        bas_t = [t_start]
    samp_t = np.asarray(bas_t, dtype=np.float64)

    def _regn_vid(t_val: float) -> tuple[float, float]:
        """Returnerar `(intensitet_mm_h, kumulativt_regn_mm)` exakt ur `raw_iv`."""
        kum_m = 0.0
        intens = 0.0
        hittad = False
        for iv in raw_iv:
            if t_val >= iv.t_slut_s:
                kum_m += iv.regn_m
                continue
            if t_val <= iv.t_start_s:
                if not hittad and abs(t_val - iv.t_start_s) <= 1e-12:
                    intens = iv.intensitet_start_mm_h
                    hittad = True
                break
            dt = iv.dt_s
            andel = (t_val - iv.t_start_s) / dt if dt > 0.0 else 0.0
            i_t = iv.intensitet_start_mm_h + andel * (
                iv.intensitet_slut_mm_h - iv.intensitet_start_mm_h
            )
            kum_m += (0.5 * (iv.intensitet_start_mm_h + i_t) / 1000.0 / 3600.0) * (
                t_val - iv.t_start_s
            )
            intens = i_t
            hittad = True
            break
        if not hittad and raw_iv:
            if abs(t_val - raw_iv[-1].t_slut_s) <= 1e-9:
                intens = raw_iv[-1].intensitet_slut_mm_h
            elif t_val < raw_iv[0].t_start_s:
                intens = 0.0
        return float(intens), float(kum_m) * 1000.0

    # 2. Vektoriserad nod-nivåmatris w_lov[sample_idx, nod_id]
    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    h, b = z64.shape
    aktiv = (graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64)
    ct = graf.cell_till_nod
    har_nod = aktiv & (ct >= 0)
    ct_valid = ct[har_nod]
    z_valid = z64[har_nod]
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)
    total_aktiv_area_m2, _, _ = berakna_doman_areor(graf)

    har_noder = bool(graf.noder) and bool(res.tider_s) and bool(har_nod.any())
    w_lov: np.ndarray[Any, Any] | None = None
    if har_noder:
        res_t = np.asarray(res.tider_s, dtype=np.float64)
        max_nid = max(graf.noder)
        w_lov = np.full((len(samp_t), max_nid + 1), -np.inf, dtype=np.float64)
        for lov in graf.lovnoder():
            serie = res.noder.get(lov.nod_id)
            if serie is not None and serie.niva_rh2000:
                w_lov[:, lov.nod_id] = np.interp(
                    samp_t, res_t, np.asarray(serie.niva_rh2000, dtype=np.float64)
                )

    punkter: list[dict[str, Any]] = []
    for i, t_val in enumerate(bas_t):
        i_mm_h, kum_mm = _regn_vid(t_val)
        if har_noder and w_lov is not None:
            djup = np.maximum(0.0, w_lov[i, ct_valid] - z_valid)
            max_djup_m = float(djup.max()) if djup.size else 0.0
            vat_01 = float(np.count_nonzero(djup >= 0.10)) * cell_area_m2
            vat_03 = float(np.count_nonzero(djup >= 0.30)) * cell_area_m2
            volym_m3 = float(djup.sum()) * cell_area_m2
        else:
            max_djup_m = 0.0
            vat_01 = 0.0
            vat_03 = 0.0
            volym_m3 = 0.0
        in_kum_m3 = total_aktiv_area_m2 * (kum_mm / 1000.0)
        utflode_kum_m3 = max(0.0, in_kum_m3 - volym_m3)
        punkter.append(
            {
                "tid_min": round(t_val / 60.0, 4),
                "regn_mm_h": round(i_mm_h, 4),
                "kumulativt_regn_mm": round(kum_mm, 4),
                "max_djup_m": round(max_djup_m, 4),
                "vat_yta_01m_m2": round(vat_01, 2),
                "vat_yta_03m_m2": round(vat_03, 2),
                "volym_m3": round(volym_m3, 3),
                "utflode_kum_m3": round(utflode_kum_m3, 3),
                "max_hastighet_m_s": None,
            }
        )

    # Topp-tidpunkt: punkt med störst lagrad volym (därefter maxdjup, därefter regnintensitet)
    topp_idx = max(
        range(len(punkter)),
        key=lambda i: (
            punkter[i]["volym_m3"],
            punkter[i]["max_djup_m"],
            punkter[i]["regn_mm_h"],
        ),
    )
    topp_tid_min = float(punkter[topp_idx]["tid_min"])

    # 3. Välj 12–16 tidssteg för tidssteg.npz (alltid inkl. t=0, topp_tid_min och slut_tid_min)
    n_p = len(bas_t)
    if n_p <= 16:
        steg_idx = list(range(n_p))
    else:
        valda = {int(x) for x in np.rint(np.linspace(0, n_p - 1, _TIDSSTEG_BAS_ANTAL))}
        valda.update((0, topp_idx, n_p - 1))
        if regn_topp_s is not None and len(valda) < 16:
            regn_idx = min(range(n_p), key=lambda i: abs(bas_t[i] - regn_topp_s))
            valda.add(regn_idx)
        steg_idx = sorted(valda)

    tidssteg_tid_min = [float(punkter[i]["tid_min"]) for i in steg_idx]
    tidssteg_tider_s = np.asarray([bas_t[i] for i in steg_idx], dtype=np.float64)

    # 4. Bygg kvantiserad uint16-stack (millimeter), nedsamplad endast om max(H, W) > 1024
    faktor = max(1, math.ceil(max(h, b) / _TIDSSTEG_MAX_PX))
    h_ut = (h + faktor - 1) // faktor
    b_ut = (b + faktor - 1) // faktor
    stack_u16 = np.zeros((len(steg_idx), h_ut, b_ut), dtype=np.uint16)
    for k, idx in enumerate(steg_idx):
        djup_2d = np.zeros((h, b), dtype=np.float32)
        if har_noder and w_lov is not None:
            djup_2d[har_nod] = np.maximum(0.0, w_lov[idx, ct_valid] - z_valid).astype(np.float32)
        if faktor > 1:
            djup_2d = djup_2d[::faktor, ::faktor]
        stack_u16[k] = np.clip(np.rint(djup_2d * 1000.0), 0.0, 65535.0).astype(np.uint16)

    np.savez_compressed(
        utmapp / "tidssteg.npz",
        djup_mm=stack_u16,
        tider_s=tidssteg_tider_s,
        nedsampling=np.int32(faktor),
    )

    tidsserie_data = {
        "kontrakt": _TIDSSERIE_KONTRAKT,
        "losare": losare,
        "regntyp": regntyp,
        "varaktighet_min": round(float(varaktighet_min), 4),
        "slut_tid_min": round(t_slut / 60.0, 4),
        "total_regn_mm": round(total_regn_mm, 4),
        "topp_tid_min": topp_tid_min,
        "tidssteg_tid_min": tidssteg_tid_min,
        "punkter": punkter,
    }
    (utmapp / "tidsserie.json").write_text(
        json.dumps(tidsserie_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return total_regn_mm


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


def _bestam_overlapp_m(rutor: Sequence[Any], geos: Sequence[Any]) -> float:
    """Bestämmer gemensamt rutöverlapp (m) ur `recept.json` eller rutornas överlappande ränder."""
    recept_varden: list[float] = []
    for ruta in rutor:
        recept = ruta.mapp / "recept.json"
        if not recept.is_file():
            continue
        try:
            data = json.loads(recept.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "overlapp_m" in data:
            ov = data["overlapp_m"]
            if (
                not isinstance(ov, bool)
                and isinstance(ov, int | float)
                and math.isfinite(float(ov))
            ):
                if float(ov) < 0.0:
                    raise LosarFel(f"Ogiltigt overlapp_m ({ov}) i {recept}.")
                recept_varden.append(float(ov))
    if len(recept_varden) == len(rutor) and recept_varden:
        for ov in recept_varden[1:]:
            if not math.isclose(ov, recept_varden[0], rel_tol=0.0, abs_tol=1e-6):
                raise LosarFel(
                    f"Oförenliga overlapp_m i rutornas recept.json: {sorted(set(recept_varden))}."
                )
        return recept_varden[0]

    kandidater: list[float] = []
    tol = 1e-6
    for i in range(len(geos)):
        ga = geos[i]
        for j in range(i + 1, len(geos)):
            gb = geos[j]
            if min(ga.y_max, gb.y_max) > max(ga.y_min, gb.y_min) + tol:
                v, o = (ga, gb) if ga.x_min <= gb.x_min else (gb, ga)
                if v.x_min < o.x_min - tol and v.x_max >= o.x_min - tol and v.x_max < o.x_max - tol:
                    kandidater.append(max(0.0, 0.5 * (v.x_max - o.x_min)))
            if min(ga.x_max, gb.x_max) > max(ga.x_min, gb.x_min) + tol:
                s, n = (ga, gb) if ga.y_min <= gb.y_min else (gb, ga)
                if s.y_min < n.y_min - tol and s.y_max >= n.y_min - tol and s.y_max < n.y_max - tol:
                    kandidater.append(max(0.0, 0.5 * (s.y_max - n.y_min)))

    if not kandidater:
        return 0.0
    for ov in kandidater[1:]:
        if not math.isclose(ov, kandidater[0], rel_tol=0.0, abs_tol=1e-6):
            raise LosarFel(
                f"Oförenliga rutöverlapp mellan angränsande K1-rutor: {sorted(set(kandidater))}."
            )
    return kandidater[0]


def _las_sammanfogade_rutor(
    namn: str,
    rutor: Sequence[Any],
) -> tuple[
    Any,
    NDArray[np.float32],
    NDArray[np.float32],
    NDArray[np.uint8],
    list[Any],
    list[Any],
]:
    """Läser en eller flera K1-rutor och sammanfogar med `sankgraf.hierarki.sammanfoga_rutor`."""
    from sankgraf.hierarki import sammanfoga_rutor
    from sankgraf.typer import SankGrafFel, las_brytlinjer_fran_gpkg, las_oppningar_fran_gpkg
    from testsvit.k1_io import las_geotiff

    if not rutor:
        raise LosarFel(f"{namn}: scenariot saknar K1-rutor.")

    rut_data: list[tuple[Any, NDArray[np.float32], NDArray[np.float32], NDArray[np.uint8]]] = []
    geos: list[Any] = []
    brytlinjer: list[Any] = []
    oppningar: list[Any] = []
    sedda_bryt: set[Any] = set()
    sedda_opp: set[Any] = set()

    for ruta in rutor:
        mark_raw, geo = las_geotiff(ruta.mapp / "mark.tif")
        osak_raw, _ = las_geotiff(ruta.mapp / "osakerhet.tif")
        slag_raw, _ = las_geotiff(ruta.mapp / "markslag.tif")
        geos.append(geo)
        rut_data.append(
            (
                geo,
                np.asarray(mark_raw, dtype=np.float32),
                np.asarray(osak_raw, dtype=np.float32),
                np.asarray(slag_raw, dtype=np.uint8),
            )
        )
        bryt_fil = ruta.mapp / "brytlinjer.gpkg"
        if bryt_fil.is_file():
            for b in las_brytlinjer_fran_gpkg(bryt_fil):
                nyckel = (b.typ, b.hojd_m, tuple(b.koordinater), b.z_kron_rh2000)
                if nyckel not in sedda_bryt:
                    sedda_bryt.add(nyckel)
                    brytlinjer.append(b)
        opp_fil = ruta.mapp / "oppningar.gpkg"
        if opp_fil.is_file():
            for o in las_oppningar_fran_gpkg(opp_fil):
                nyckel_o = (o.typ, o.bredd_m, o.botten_rh2000, tuple(o.koordinater))
                if nyckel_o not in sedda_opp:
                    sedda_opp.add(nyckel_o)
                    oppningar.append(o)

    if len(rut_data) == 1:
        geo0, mz0, oz0, ms0 = rut_data[0]
        return geo0, mz0, oz0, ms0, brytlinjer, oppningar

    overlapp_m = _bestam_overlapp_m(rutor, geos)
    try:
        glob_geo, glob_mark, glob_osak, glob_slag = sammanfoga_rutor(
            rut_data, overlapp_m=overlapp_m
        )
    except SankGrafFel as exc:
        raise LosarFel(f"{namn}: kunde inte sammanfoga {len(rutor)} K1-rutor: {exc}") from exc
    return glob_geo, glob_mark, glob_osak, glob_slag, brytlinjer, oppningar


def _bygg_sankgraf_for_rutor(
    namn: str,
    rutor: Sequence[Any],
    *,
    persistens_faktor: float | None,
    takvatten: bool,
    takvatten_andel_ledning: float,
) -> tuple[Any, Any]:
    """Bygger en global `SankGraf` för en eller flera K1-rutor (via `sammanfoga_rutor`)."""
    from sankgraf.fyllning import kor_statisk_k1_mapp
    from sankgraf.hierarki import bygg_sankgraf
    from sankgraf.persistens import filtrera_persistens
    from sankgraf.takvatten import bygg_takvatten
    from sankgraf.typer import SankGrafFel

    if not rutor:
        raise LosarFel(f"{namn}: scenariot saknar K1-rutor.")
    if len(rutor) == 1:
        graf, _ = kor_statisk_k1_mapp(
            rutor[0].mapp,
            regn_m=0.0,
            persistens_faktor=persistens_faktor,
            takvatten=takvatten,
            takvatten_andel_ledning=takvatten_andel_ledning,
        )
        return graf, graf.geo

    glob_geo, glob_mark, glob_osak, glob_slag, brytlinjer, oppningar = _las_sammanfogade_rutor(
        namn, rutor
    )
    try:
        graf = bygg_sankgraf(
            glob_mark,
            glob_osak,
            glob_slag,
            glob_geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
        )
        if persistens_faktor is not None:
            graf = filtrera_persistens(graf, faktor=persistens_faktor)
        if takvatten:
            tv = bygg_takvatten(
                graf.mark_z,
                graf.markslag,
                graf.geo,
                andel_ledning=takvatten_andel_ledning,
            )
            graf = replace(graf, takvatten=tv)
    except SankGrafFel as exc:
        raise LosarFel(f"{namn}: {exc}") from exc
    return graf, glob_geo


_TVAD_PARAMETRAR = (
    {
        "regn_mm",
        "regn_tid_s",
        "regn_mm_h",
        "slut_tid_s",
        "subgrid_faktor",
        "oppna_rander",
        "anvand_rust",
        "enhet",
        "precision",
        "vattenniva_rh2000_m",
        "brunnar_igensatta_andel",
        "infiltration",
        "rannsten_1d",
        "rannstenar",
        "rannsten_korbana_sida",
        "idf",
    }
    | _IDF_PARAMETRAR
    | _VATTEN_ANTAGANDEN
)


class TvadLokalTroghet(Analyslosare):
    """2D lokal tröghet (Bates 2010 / de Almeida) med automatisk Rust-dispatch och 1D-rännsten."""

    namn: ClassVar[str] = "vatten.2d"
    doman: ClassVar[str] = "vatten"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = (
        "2D ytvattenberäkning med lokal tröghetsformulering (T05), automatisk bitidentisk "
        "Rust-dispatch och valfri 1D-rännstenskoppling."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("avtappning_omraden",)
    utdataroller: ClassVar[tuple[str, ...]] = ("max_djup", "hastighet", "ankomsttid")

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        okanda = sorted(set(parametrar) - _TVAD_PARAMETRAR)
        if okanda:
            raise LosarFel(f"{self.namn}: okända parametrar {okanda}.")
        ut: dict[str, Any] = validera_vattenantaganden(self.namn, parametrar)

        har_idf = "idf" in parametrar
        har_mm = "regn_mm" in parametrar
        har_serie = "regn_tid_s" in parametrar or "regn_mm_h" in parametrar
        if sum((har_idf, har_mm, har_serie)) != 1:
            raise LosarFel(
                f"{self.namn}: ange exakt en regnkälla: 'idf', 'regn_mm' eller "
                "'regn_tid_s'+'regn_mm_h'."
            )
        if har_idf:
            idf = StatiskFyllning().validera_parametrar(
                {k: v for k, v in parametrar.items() if k in _IDF_PARAMETRAR | {"idf"}}
            )
            for k in (
                "takvatten",
                "takvatten_andel_ledning",
                "avtappning_mm_h",
                "avtappning_angiven",
                "persistens_faktor",
            ):
                idf.pop(k, None)
            ut.update(idf)
        elif har_mm:
            fel_idf = sorted(set(parametrar) & (_IDF_PARAMETRAR - {"varaktighet_min"}))
            if fel_idf:
                raise LosarFel(f"{self.namn}: {fel_idf} kräver parametern 'idf'.")
            ut["regn_mm"] = _tal(parametrar, "regn_mm", minst=0.0)
            varakt = (
                _tal(parametrar, "varaktighet_min", minst=0.0)
                if "varaktighet_min" in parametrar
                else 60.0
            )
            if varakt <= 0.0:
                raise LosarFel(
                    f"{self.namn}: 'varaktighet_min' måste vara > 0 vid regn_mm, fick {varakt}."
                )
            ut["varaktighet_min"] = varakt
        else:
            tider = parametrar.get("regn_tid_s")
            mm_h = parametrar.get("regn_mm_h")
            if not isinstance(tider, list | tuple) or not isinstance(mm_h, list | tuple):
                raise LosarFel(f"{self.namn}: kräver listorna regn_tid_s och regn_mm_h.")
            if len(tider) != len(mm_h) or len(tider) < 2:
                raise LosarFel(
                    f"{self.namn}: regn_tid_s och regn_mm_h måste vara lika långa (>= 2)."
                )
            ut["regn_tid_s"] = [_tal({"t": t}, "t", minst=0.0) for t in tider]
            ut["regn_mm_h"] = [_tal({"i": i}, "i", minst=0.0) for i in mm_h]

        if "slut_tid_s" in parametrar:
            ut["slut_tid_s"] = _tal(parametrar, "slut_tid_s", minst=0.0)

        subgrid = parametrar.get("subgrid_faktor", 1)
        if isinstance(subgrid, bool) or not isinstance(subgrid, int) or subgrid < 1:
            raise LosarFel(
                f"{self.namn}: 'subgrid_faktor' måste vara ett heltal >= 1, fick {subgrid!r}."
            )
        ut["subgrid_faktor"] = subgrid

        oppna = parametrar.get("oppna_rander", True)
        if not isinstance(oppna, bool):
            raise LosarFel(
                f"{self.namn}: 'oppna_rander' måste vara true eller false, fick {oppna!r}."
            )
        ut["oppna_rander"] = oppna

        rust_val = parametrar.get("anvand_rust", None)
        if rust_val is not None and not isinstance(rust_val, bool):
            raise LosarFel(
                f"{self.namn}: 'anvand_rust' måste vara true, false eller null, fick {rust_val!r}."
            )
        ut["anvand_rust"] = rust_val

        enhet = parametrar.get("enhet", "cpu")
        if not isinstance(enhet, str) or enhet not in ("cpu", "cuda", "auto"):
            raise LosarFel(
                f"{self.namn}: 'enhet' måste vara 'cpu', 'cuda' eller 'auto', fick {enhet!r}."
            )
        ut["enhet"] = enhet

        precision = parametrar.get("precision", "f64")
        if precision not in ("f64", "f32"):
            raise LosarFel(
                f"{self.namn}: 'precision' måste vara 'f64' eller 'f32', fick {precision!r}."
            )
        ut["precision"] = precision

        if "vattenniva_rh2000_m" in parametrar and parametrar["vattenniva_rh2000_m"] is not None:
            ut["vattenniva_rh2000_m"] = _tal(parametrar, "vattenniva_rh2000_m")

        brunnar = (
            _tal(parametrar, "brunnar_igensatta_andel", minst=0.0)
            if "brunnar_igensatta_andel" in parametrar
            else 0.0
        )
        if brunnar > 1.0:
            raise LosarFel(
                f"{self.namn}: 'brunnar_igensatta_andel' måste ligga i [0, 1], fick {brunnar}."
            )
        ut["brunnar_igensatta_andel"] = brunnar

        if "infiltration" in parametrar and parametrar["infiltration"] is not None:
            inf = parametrar["infiltration"]
            if not isinstance(inf, Mapping) or "jordart" not in inf or "initial_mm" not in inf:
                raise LosarFel(
                    f"{self.namn}: 'infiltration' måste vara ett objekt med 'jordart' och "
                    "'initial_mm'."
                )
            ut["infiltration"] = {
                "jordart": str(inf["jordart"]),
                "initial_mm": _tal(inf, "initial_mm", minst=0.0),
                "hog_markfukt": bool(inf.get("hog_markfukt", False)),
                "tjale": bool(inf.get("tjale", False)),
            }

        rannsten_val = parametrar.get("rannstenar", parametrar.get("rannsten_1d", "auto"))
        if (
            "rannstenar" in parametrar
            and "rannsten_1d" in parametrar
            and parametrar["rannstenar"] != parametrar["rannsten_1d"]
        ):
            raise LosarFel(
                f"{self.namn}: ange inte olika värden för 'rannstenar' och 'rannsten_1d'."
            )
        if rannsten_val not in ("auto", True, False):
            raise LosarFel(
                f"{self.namn}: 'rannsten_1d'/'rannstenar' måste vara 'auto', true eller false, "
                f"fick {rannsten_val!r}."
            )
        ut["rannsten_1d"] = rannsten_val

        sida = parametrar.get("rannsten_korbana_sida", "vanster")
        if sida not in ("vanster", "hoger"):
            raise LosarFel(
                f"{self.namn}: 'rannsten_korbana_sida' måste vara 'vanster' eller 'hoger', "
                f"fick {sida!r}."
            )
        ut["rannsten_korbana_sida"] = sida
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from gemensamt.cog import skriv_komprimerad_cog
        from sankgraf.hierarki import _applicera_oppningar
        from sankgraf.takvatten import bygg_takvatten
        from tvad.kalltermer import Avtappning, Infiltration2D, Kalltermer
        from tvad.lokal_troghet import TvadFel, kor_2d_lokal_troghet
        from tvad.rannsten_1d import (
            diskretisera_rannsten_1d,
            exportera_rannsten_flode_geojson,
            kor_kopplad_1d_2d,
        )

        p = kontext.parametrar
        geo, mark_z, _, markslag, brytlinjer, oppningar = _las_sammanfogade_rutor(
            self.namn, kontext.scenario.rutor
        )
        if oppningar:
            mark_z, markslag = _applicera_oppningar(mark_z, markslag, geo, oppningar)

        regn_matvarden: dict[str, Any] = {}
        if "idf" in p:
            from gemensamt.regn import RegnFel, skapa_regn_fran_idf_kalla

            try:
                serie = skapa_regn_fran_idf_kalla(
                    str(p["idf"]),
                    float(p["aterkomsttid_ar"]),
                    klimatfaktor=float(p["klimatfaktor"]),
                    typ=str(p["regntyp"]),
                    varaktighet_min=float(p["varaktighet_min"]),
                )
            except RegnFel as exc:
                raise LosarFel(f"{self.namn}: {exc}") from exc
            tider, mm_h = list(serie.tid_s), list(serie.intensitet_mm_h)
            regn_matvarden = {
                "regn_mm": float(serie.total_djup_mm),
                "klimatfaktor": float(serie.klimatfaktor),
                "aterkomsttid_ar": float(p["aterkomsttid_ar"]),
                "varaktighet_min": float(p["varaktighet_min"]),
            }
        elif "regn_mm" in p:
            regn_mm = float(p["regn_mm"])
            varakt_min = float(p["varaktighet_min"])
            intens = regn_mm / (varakt_min / 60.0)
            tider = [0.0, varakt_min * 60.0]
            mm_h = [intens, intens]
            regn_matvarden = {"regn_mm": regn_mm, "varaktighet_min": varakt_min}
        else:
            tider = [float(t) for t in p["regn_tid_s"]]
            mm_h = [float(i) for i in p["regn_mm_h"]]

        p_avt = dict(p)
        p_avt.setdefault("varaktighet_min", 1.0)
        avt_raster, avt_info, _ = avtappning_for_kontext(
            self.namn,
            LosarKontext(
                utmapp=kontext.utmapp,
                scenario=kontext.scenario,
                parametrar=p_avt,
                indata=kontext.indata,
                beroenden=kontext.beroenden,
            ),
            geo,
        )
        avtappning = None
        if avt_raster is not None and np.any(avt_raster > 0.0):
            avtappning = Avtappning(
                avt_raster,
                brunnar_igensatta_andel=float(p["brunnar_igensatta_andel"]),
                kalla=str(avt_info["avtappning_kalla"]),
            )

        infiltration = None
        if p.get("infiltration") is not None:
            inf = p["infiltration"]
            infiltration = Infiltration2D.fran_markslag(
                markslag,
                str(inf["jordart"]),
                initial_mm=float(inf["initial_mm"]),
                hog_markfukt=bool(inf.get("hog_markfukt", False)),
                tjale=bool(inf.get("tjale", False)),
            )

        kalltermer = (
            Kalltermer(avtappning=avtappning, infiltration=infiltration)
            if (avtappning is not None or infiltration is not None)
            else None
        )
        takvatten = (
            bygg_takvatten(
                mark_z,
                markslag,
                geo,
                andel_ledning=float(p["takvatten_andel_ledning"]),
            )
            if bool(p["takvatten"])
            else None
        )

        try:
            res = kor_2d_lokal_troghet(
                mark_z,
                markslag,
                geo,
                tider,
                mm_h,
                slut_tid_s=p.get("slut_tid_s"),
                subgrid_faktor=int(p["subgrid_faktor"]),
                oppna_rander=bool(p["oppna_rander"]),
                kalltermer=kalltermer,
                takvatten=takvatten,
                brytlinjer=brytlinjer,
                enhet=str(p["enhet"]),
                precision=p["precision"],
                vattenniva_rh2000_m=p.get("vattenniva_rh2000_m"),
                anvand_rust=p["anvand_rust"],
            )
        except TvadFel as exc:
            raise LosarFel(f"{self.namn}: {exc}") from exc

        skriv_komprimerad_cog(
            kontext.utmapp / "max_djup.tif",
            np.asarray(res.max_djup_m, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
            epsg=geo.epsg,
        )
        skriv_komprimerad_cog(
            kontext.utmapp / "hastighet.tif",
            np.asarray(res.max_hastighet_m_s, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
            epsg=geo.epsg,
        )
        skriv_komprimerad_cog(
            kontext.utmapp / "ankomsttid.tif",
            np.asarray(res.tid_till_0_1m_s, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
            epsg=geo.epsg,
            nodata=float("nan"),
        )
        filer: dict[str, str] = {
            "max_djup": "max_djup.tif",
            "hastighet": "hastighet.tif",
            "ankomsttid": "ankomsttid.tif",
        }

        rannsten_lage = p["rannsten_1d"]
        kantstenar_3d = [
            b
            for b in brytlinjer
            if b.typ in ("kantsten", "sankt_kantsten") and b.z_kron_rh2000 is not None
        ]
        kor_1d = bool(
            (
                rannsten_lage is True
                and any(b.typ in ("kantsten", "sankt_kantsten") for b in brytlinjer)
            )
            or (rannsten_lage == "auto" and kantstenar_3d)
        )
        rannsten_info: dict[str, Any] = {"rannsten_1d_aktiv": False}
        if kor_1d:
            lank_linjer = kantstenar_3d or [
                b for b in brytlinjer if b.typ in ("kantsten", "sankt_kantsten")
            ]
            lank = diskretisera_rannsten_1d(
                lank_linjer,
                mark_z,
                markslag,
                geo,
                korbana_sida=str(p["rannsten_korbana_sida"]),
            )
            res_1d = kor_kopplad_1d_2d(
                mark_z,
                markslag,
                geo,
                tider,
                mm_h,
                lank,
                brytlinjer=brytlinjer,
                slut_tid_s=p.get("slut_tid_s"),
            )
            gj_fil = exportera_rannsten_flode_geojson(
                res_1d, kontext.utmapp / "rannsten_flode.geojson"
            )
            filer["rannsten_flode"] = gj_fil.name
            rannsten_info = {
                "rannsten_1d_aktiv": True,
                "rannsten_1d_fil": gj_fil.name,
                "rannsten_1d_segment": lank.antal,
                "rannsten_1d_max_djup_m": float(np.max(res_1d.max_djup_1d_m)),
                "rannsten_1d_overfall_sankt_m3": float(res_1d.overfall_sankt_m3),
                "rannsten_1d_overfall_hog_m3": float(res_1d.overfall_hog_m3),
                "rannsten_1d_volymfel_rel": float(res_1d.volymfel_rel),
            }

        return LosarUtdata(
            filer=filer,
            matvarden={
                "max_djup_m": float(np.nanmax(res.max_djup_m)),
                "max_hastighet_m_s": float(np.nanmax(res.max_hastighet_m_s)),
                "max_fara_m2_s": float(np.nanmax(res.fara_m2_s)),
                "massbalans_fel": float(res.volymfel_rel),
                "volymfel_rel": float(res.volymfel_rel),
                "in_m3": float(res.in_m3),
                "lagrat_m3": float(res.lagrat_m3),
                "ut_m3": float(res.ut_m3),
                "avtappat_m3": float(res.avtappat_m3),
                "infiltrerat_m3": float(res.infiltrerat_m3),
                "initial_forlust_m3": float(res.initial_forlust_m3),
                "antal_tidssteg": int(res.antal_tidssteg),
                "anvande_rust": bool(res.anvande_rust),
                "berakningsplats": str(res.berakningsplats),
                **regn_matvarden,
                **vattenantaganden_matvarden(takvatten, avt_info),
                **rannsten_info,
                "volymbalans": res.volymbalans(),
            },
            varningar=takvatten_varningar(takvatten),
        )


LOSARE: list[Analyslosare] = [
    StatiskFyllning(), Dynamik(), TvadLokalTroghet(), Lutning(), *_LUFT_LOSARE, *_EKOLOGI_LOSARE
]
