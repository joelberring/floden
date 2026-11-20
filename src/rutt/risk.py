"""Kumulativ översvämningsrisk för räddningstjänstens vägnät och framkomlighet.

Beräknar per gatuavsnitt och per adress den kritiska återkomsttiden `T*`,
årlig överskridandesannolikhet `p = 1 / T*`, kumulativ sannolikhet över en
vald tidshorisont `P_N = 1 - (1 - p)^N` samt förväntat årligt överdjup
(trapetsintegral över `[1/T_max, 1/T_min]`).

Fordonsspecifika gränsdjup hämtas alltid från validerade K7-parametrar; inga
skarpa gränsdjup får hittas på eller passera med `None`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from rutt.dijkstra import tidsberoende_dijkstra
from rutt.parametrar import (
    RuttParametrarFel,
    RuttParametrarK7,
    validera_parametrar,
)
from rutt.vagnat import VagNat, VagNatFel, effektivt_vagdjup_m


def _validera_positivt_tal(varde: object, namn: str) -> float:
    """Kontrollerar att `varde` är ett ändligt tal > 0 (och inte `bool`)."""
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise RuttParametrarFel(f"Fältet '{namn}' måste vara ett tal.")
    tal = float(varde)
    if not math.isfinite(tal) or tal <= 0.0:
        raise RuttParametrarFel(f"Fältet '{namn}' måste vara ändligt och > 0, fick {varde}.")
    return tal


def _validera_andligt_tal(varde: object, namn: str) -> float:
    """Kontrollerar att `varde` är ett ändligt tal (och inte `bool`)."""
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise RuttParametrarFel(f"Fältet '{namn}' måste vara ett tal.")
    tal = float(varde)
    if not math.isfinite(tal):
        raise RuttParametrarFel(f"Fältet '{namn}' måste vara ändligt, fick {varde}.")
    return tal


@dataclass(frozen=True)
class KantKumulativRisk:
    """Kumulativ översvämningsrisk för ett gatuavsnitt upp till `max_aterkomsttid_ar`."""

    kant_id: str
    fordon_typ: str
    gransdjup_m: float
    max_aterkomsttid_ar: float
    tidshorisont_ar: float
    kritisk_aterkomsttid_ar: float
    overskriden_vid_min_t: bool
    arlig_overskridande_sannolikhet: float
    kumulativ_sannolikhet: float
    forvantat_arligt_overdjup_m: float
    max_djup_vid_tmax_m: float
    koordinater: tuple[tuple[float, float], ...]

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar kantens kumulativa risk till ett JSON-kompatibelt lexikon."""
        return {
            "kant_id": self.kant_id,
            "fordon_typ": self.fordon_typ,
            "gransdjup_m": float(self.gransdjup_m),
            "max_aterkomsttid_ar": float(self.max_aterkomsttid_ar),
            "tidshorisont_ar": float(self.tidshorisont_ar),
            "kritisk_aterkomsttid_ar": (
                float(self.kritisk_aterkomsttid_ar)
                if math.isfinite(self.kritisk_aterkomsttid_ar)
                else None
            ),
            "overskriden_vid_min_t": self.overskriden_vid_min_t,
            "arlig_overskridande_sannolikhet": float(self.arlig_overskridande_sannolikhet),
            "kumulativ_sannolikhet": float(self.kumulativ_sannolikhet),
            "forvantat_arligt_overdjup_m": float(self.forvantat_arligt_overdjup_m),
            "max_djup_vid_tmax_m": float(self.max_djup_vid_tmax_m),
            "koordinater": [[float(x), float(y)] for x, y in self.koordinater],
        }


@dataclass(frozen=True)
class AdressKumulativRisk:
    """Kumulativ risk att en adress blir onåbar från samtliga räddningsstationer."""

    adress_id: str
    fordon_typ: str
    max_aterkomsttid_ar: float
    tidshorisont_ar: float
    kritisk_aterkomsttid_ar: float
    overskriden_vid_min_t: bool
    arlig_ofarbar_sannolikhet: float
    kumulativ_ofarbar_sannolikhet: float
    varsta_insatstid_s: float

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar adressens kumulativa risk till ett JSON-kompatibelt lexikon."""
        return {
            "adress_id": self.adress_id,
            "fordon_typ": self.fordon_typ,
            "max_aterkomsttid_ar": float(self.max_aterkomsttid_ar),
            "tidshorisont_ar": float(self.tidshorisont_ar),
            "kritisk_aterkomsttid_ar": (
                float(self.kritisk_aterkomsttid_ar)
                if math.isfinite(self.kritisk_aterkomsttid_ar)
                else None
            ),
            "overskriden_vid_min_t": self.overskriden_vid_min_t,
            "arlig_ofarbar_sannolikhet": float(self.arlig_ofarbar_sannolikhet),
            "kumulativ_ofarbar_sannolikhet": float(self.kumulativ_ofarbar_sannolikhet),
            "varsta_insatstid_s": (
                float(self.varsta_insatstid_s)
                if math.isfinite(self.varsta_insatstid_s)
                else None
            ),
        }


@dataclass(frozen=True)
class VagKumulativRiskResultat:
    """Samlingsresultat för kumulativ översvämningsrisk i vägnät och adressnåbarhet."""

    fordon_typ: str
    gransdjup_m: float
    aterkomsttider_ar: tuple[float, ...]
    max_aterkomsttid_ar: float
    tidshorisont_ar: float
    kanter: dict[str, KantKumulativRisk]
    adresser: dict[str, AdressKumulativRisk]
    antal_kanter_med_risk: int
    antal_adresser_med_risk: int
    max_kumulativ_kant_sannolikhet: float

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar hela vägnätets kumulativa riskresultat till lexikon."""
        return {
            "fordon_typ": self.fordon_typ,
            "gransdjup_m": float(self.gransdjup_m),
            "aterkomsttider_ar": [float(t) for t in self.aterkomsttider_ar],
            "max_aterkomsttid_ar": float(self.max_aterkomsttid_ar),
            "tidshorisont_ar": float(self.tidshorisont_ar),
            "antal_kanter_med_risk": int(self.antal_kanter_med_risk),
            "antal_adresser_med_risk": int(self.antal_adresser_med_risk),
            "max_kumulativ_kant_sannolikhet": float(self.max_kumulativ_kant_sannolikhet),
            "kanter": {kid: k.till_dict() for kid, k in self.kanter.items()},
            "adresser": {aid: a.till_dict() for aid, a in self.adresser.items()},
        }


def interpolera_kritisk_aterkomsttid_ar(
    aterkomsttider_ar: Sequence[float],
    djup_m: Sequence[float],
    gransdjup_m: float,
) -> tuple[float, bool]:
    """Beräknar kritisk återkomsttid `T*` där djupet först når `gransdjup_m`.

    Log-interpolerar i `ln(T)` mellan första paret där `d(T_{j-1}) < grans <= d(T_j)`.
    Om `d(T_1) >= gransdjup_m` returneras `(T_1, True)`.
    Om djupet aldrig når `gransdjup_m` returneras `(math.inf, False)`.
    """
    grans = _validera_positivt_tal(gransdjup_m, "gransdjup_m")
    if len(aterkomsttider_ar) == 0:
        raise RuttParametrarFel("Minst en återkomsttid krävs i 'aterkomsttider_ar'.")
    if len(aterkomsttider_ar) != len(djup_m):
        raise RuttParametrarFel(
            "Längden på 'aterkomsttider_ar' och 'djup_m' måste vara lika "
            f"({len(aterkomsttider_ar)} != {len(djup_m)})."
        )

    tider: list[float] = []
    djup: list[float] = []
    tidigare_t = 0.0
    for idx, (t_raw, d_raw) in enumerate(zip(aterkomsttider_ar, djup_m, strict=True)):
        t_val = _validera_positivt_tal(t_raw, f"aterkomsttider_ar[{idx}]")
        if idx > 0 and t_val <= tidigare_t:
            raise RuttParametrarFel(
                "Fältet 'aterkomsttider_ar' måste vara strikt stigande "
                f"({tidigare_t} >= {t_val})."
            )
        d_val = _validera_andligt_tal(d_raw, f"djup_m[{idx}]")
        tider.append(t_val)
        djup.append(d_val)
        tidigare_t = t_val

    if djup[0] >= grans:
        return (tider[0], True)

    for idx in range(1, len(tider)):
        d_prev = djup[idx - 1]
        d_curr = djup[idx]
        if d_curr >= grans:
            if d_curr == grans:
                return (tider[idx], False)
            andel = (grans - d_prev) / (d_curr - d_prev)
            ln_prev = math.log(tider[idx - 1])
            ln_curr = math.log(tider[idx])
            t_krit = math.exp(ln_prev + andel * (ln_curr - ln_prev))
            # Håll resultatet inom [T_{j-1}, T_j] vid flyttalsavrundning.
            t_krit = min(tider[idx], max(tider[idx - 1], t_krit))
            return (t_krit, False)

    return (math.inf, False)


def berakna_kumulativ_sannolikhet(
    arlig_sannolikhet: float,
    tidshorisont_ar: float,
) -> float:
    """Beräknar kumulativ sannolikhet `1 - (1 - p)^N` över `tidshorisont_ar` år."""
    p = _validera_andligt_tal(arlig_sannolikhet, "arlig_sannolikhet")
    if p < 0.0 or p > 1.0:
        raise RuttParametrarFel(
            f"Fältet 'arlig_sannolikhet' måste ligga i [0.0, 1.0], fick {arlig_sannolikhet}."
        )
    horisont = _validera_positivt_tal(tidshorisont_ar, "tidshorisont_ar")

    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    kum = 1.0 - math.pow(1.0 - p, horisont)
    return min(1.0, max(0.0, kum))


def _berakna_forvantat_arligt_overdjup_m(
    aterkomsttider_ar: Sequence[float],
    eff_djup_m: Sequence[float],
    gransdjup_m: float,
) -> float:
    """Trapetsintegral i årlig sannolikhet `p = 1/T` av `max(0, d - grans)`."""
    if len(aterkomsttider_ar) < 2:
        return 0.0
    sannolikheter = [min(1.0, 1.0 / t_ar) for t_ar in aterkomsttider_ar]
    overdjup = [max(0.0, d_m - gransdjup_m) for d_m in eff_djup_m]

    integral = 0.0
    for (p0, e0), (p1, e1) in zip(
        zip(sannolikheter[:-1], overdjup[:-1], strict=True),
        zip(sannolikheter[1:], overdjup[1:], strict=True),
        strict=True,
    ):
        dp = p0 - p1
        integral += 0.5 * (e0 + e1) * dp
    return max(0.0, integral)


def _arlig_sannolikhet_fran_kritisk_t(
    kritisk_t_ar: float,
    max_aterkomsttid_ar: float,
) -> float:
    """Omvandlar `T*` till årlig sannolikhet `1 / T*` i `[0.0, 1.0]` om `T* <= T_max`."""
    if not math.isfinite(kritisk_t_ar) or kritisk_t_ar > max_aterkomsttid_ar:
        return 0.0
    return min(1.0, max(0.0, 1.0 / kritisk_t_ar))


def berakna_vag_kumulativ_risk(
    vagnat: VagNat,
    kant_djup_per_aterkomsttid: Mapping[float, Mapping[str, float]],
    parametrar: RuttParametrarK7,
    fordon_typ: str,
    *,
    max_aterkomsttid_ar: float = 100.0,
    tidshorisont_ar: float = 50.0,
    skarp: bool = True,
    tillat_testvarden: bool = False,
    stationer: Mapping[str, str] | None = None,
    adresser: Mapping[str, str] | None = None,
    vattenniva_per_aterkomsttid: Mapping[float, Mapping[str, float]] | None = None,
    tidssteg_s: float = 0.0,
) -> VagKumulativRiskResultat:
    """Beräknar kumulativ översvämningsrisk per gatuavsnitt och valfritt per adress.

    Validerar K7-parametrar strikt via `validera_parametrar`, filtrerar återkomsttider
    till `T <= max_aterkomsttid_ar`, använder `effektivt_vagdjup_m` så att broar över
    översvämmad terräng hanteras korrekt, och kör vid angivna `stationer` och
    `adresser` `tidsberoende_dijkstra` för varje återkomsttid.
    """
    validera_parametrar(
        parametrar,
        skarp=skarp,
        tillat_testvarden=tillat_testvarden,
    )
    if fordon_typ not in parametrar.fordon:
        raise RuttParametrarFel(f"Okänd fordonstyp '{fordon_typ}' i K7-parametrar.")
    fordon = parametrar.fordon[fordon_typ]
    if fordon.gransdjup_m is None:
        raise RuttParametrarFel(f"Fordon '{fordon_typ}' saknar fastställt 'gransdjup_m'.")
    gransdjup_m = _validera_positivt_tal(
        fordon.gransdjup_m,
        f"fordon.{fordon_typ}.gransdjup_m",
    )

    max_t = _validera_positivt_tal(max_aterkomsttid_ar, "max_aterkomsttid_ar")
    horisont = _validera_positivt_tal(tidshorisont_ar, "tidshorisont_ar")
    start_tid_s = _validera_andligt_tal(tidssteg_s, "tidssteg_s")

    if (stationer is None) != (adresser is None):
        raise RuttParametrarFel(
            "Både 'stationer' och 'adresser' måste anges tillsammans för adressanalys."
        )

    normaliserat_djup: dict[float, Mapping[str, float]] = {}
    for t_raw, djup_karta in kant_djup_per_aterkomsttid.items():
        t_val = _validera_positivt_tal(t_raw, "aterkomsttid_ar")
        normaliserat_djup[t_val] = djup_karta

    valda_tider = tuple(sorted(t for t in normaliserat_djup if t <= max_t))
    if not valda_tider:
        raise RuttParametrarFel(
            f"Inga återkomsttider <= max_aterkomsttid_ar ({max_t}) finns i indata."
        )

    normaliserad_niva: dict[float, Mapping[str, float]] | None = None
    if vattenniva_per_aterkomsttid is not None:
        normaliserad_niva = {}
        for t_raw, niva_karta in vattenniva_per_aterkomsttid.items():
            t_val = _validera_positivt_tal(t_raw, "vattenniva_aterkomsttid_ar")
            normaliserad_niva[t_val] = niva_karta

    kant_risker: dict[str, KantKumulativRisk] = {}
    for kant_id in sorted(vagnat.kanter):
        kant = vagnat.kanter[kant_id]
        eff_djup_serie: list[float] = []
        for t_ar in valda_tider:
            mark_d = float(normaliserat_djup[t_ar].get(kant_id, 0.0))
            niva_karta = (
                normaliserad_niva.get(t_ar) if normaliserad_niva is not None else None
            )
            niva = niva_karta.get(kant_id) if niva_karta is not None else None
            eff_djup_serie.append(effektivt_vagdjup_m(kant, mark_d, niva))

        kritisk_t, overskriden_min = interpolera_kritisk_aterkomsttid_ar(
            valda_tider,
            eff_djup_serie,
            gransdjup_m,
        )
        arlig_p = _arlig_sannolikhet_fran_kritisk_t(kritisk_t, max_t)
        kum_p = berakna_kumulativ_sannolikhet(arlig_p, horisont)
        overdjup_m = _berakna_forvantat_arligt_overdjup_m(
            valda_tider,
            eff_djup_serie,
            gransdjup_m,
        )
        kant_risker[kant_id] = KantKumulativRisk(
            kant_id=kant_id,
            fordon_typ=fordon_typ,
            gransdjup_m=gransdjup_m,
            max_aterkomsttid_ar=max_t,
            tidshorisont_ar=horisont,
            kritisk_aterkomsttid_ar=kritisk_t,
            overskriden_vid_min_t=overskriden_min,
            arlig_overskridande_sannolikhet=arlig_p,
            kumulativ_sannolikhet=kum_p,
            forvantat_arligt_overdjup_m=overdjup_m,
            max_djup_vid_tmax_m=max(eff_djup_serie),
            koordinater=vagnat.kant_koordinater(kant_id),
        )

    adress_risker: dict[str, AdressKumulativRisk] = {}
    if stationer is not None and adresser is not None:
        for station_id, nod_id in stationer.items():
            if nod_id not in vagnat.noder:
                raise VagNatFel(
                    f"Station '{station_id}' refererar okänd nod '{nod_id}' i vägnätet."
                )
        for adress_id, nod_id in adresser.items():
            if nod_id not in vagnat.noder:
                raise VagNatFel(
                    f"Adress '{adress_id}' refererar okänd nod '{nod_id}' i vägnätet."
                )

        ankomster_per_t: dict[float, dict[str, dict[str, float]]] = {}
        for t_ar in valda_tider:
            djup_serie: dict[float, Mapping[str, float]] = {start_tid_s: normaliserat_djup[t_ar]}
            niva_karta = (
                normaliserad_niva.get(t_ar) if normaliserad_niva is not None else None
            )
            niva_serie: dict[float, Mapping[str, float]] | None = (
                {start_tid_s: niva_karta} if niva_karta is not None else None
            )
            ankomster_per_t[t_ar] = {
                station_id: tidsberoende_dijkstra(
                    vagnat,
                    station_nod,
                    start_tid_s,
                    djup_serie,
                    fordon,
                    vattenniva_per_tid=niva_serie,
                )
                for station_id, station_nod in stationer.items()
            }

        for adress_id, adress_nod in sorted(adresser.items()):
            kritisk_t = math.inf
            overskriden_min = False
            varsta_insatstid = 0.0

            for idx, t_ar in enumerate(valda_tider):
                basta_s = math.inf
                for station_id in stationer:
                    ankomst_s = ankomster_per_t[t_ar][station_id][adress_nod]
                    if math.isfinite(ankomst_s):
                        restid_s = ankomst_s - start_tid_s
                        basta_s = min(basta_s, restid_s)

                if not math.isfinite(basta_s):
                    if not math.isfinite(kritisk_t):
                        kritisk_t = t_ar
                        overskriden_min = idx == 0
                    varsta_insatstid = math.inf
                elif math.isfinite(varsta_insatstid):
                    varsta_insatstid = max(varsta_insatstid, basta_s)

            arlig_p = _arlig_sannolikhet_fran_kritisk_t(kritisk_t, max_t)
            kum_p = berakna_kumulativ_sannolikhet(arlig_p, horisont)
            adress_risker[adress_id] = AdressKumulativRisk(
                adress_id=adress_id,
                fordon_typ=fordon_typ,
                max_aterkomsttid_ar=max_t,
                tidshorisont_ar=horisont,
                kritisk_aterkomsttid_ar=kritisk_t,
                overskriden_vid_min_t=overskriden_min,
                arlig_ofarbar_sannolikhet=arlig_p,
                kumulativ_ofarbar_sannolikhet=kum_p,
                varsta_insatstid_s=varsta_insatstid,
            )

    antal_kanter_med_risk = sum(
        1 for k in kant_risker.values() if k.arlig_overskridande_sannolikhet > 0.0
    )
    antal_adresser_med_risk = sum(
        1 for a in adress_risker.values() if a.arlig_ofarbar_sannolikhet > 0.0
    )
    max_kumulativ_kant = max(
        (k.kumulativ_sannolikhet for k in kant_risker.values()),
        default=0.0,
    )

    return VagKumulativRiskResultat(
        fordon_typ=fordon_typ,
        gransdjup_m=gransdjup_m,
        aterkomsttider_ar=valda_tider,
        max_aterkomsttid_ar=max_t,
        tidshorisont_ar=horisont,
        kanter=kant_risker,
        adresser=adress_risker,
        antal_kanter_med_risk=antal_kanter_med_risk,
        antal_adresser_med_risk=antal_adresser_med_risk,
        max_kumulativ_kant_sannolikhet=max_kumulativ_kant,
    )
