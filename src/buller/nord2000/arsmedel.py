"""Årsmedelvärde (L_den) och väderstatistik (M1–M25) för Nord2000.

Källa: Referencelaboratoriet RL 18-18 kapitel 5 ("Test Cases for the Yearly Average"),
EU-direktiv 2002/49/EG (L_den med dag 12 h, kväll 3 h +5 dB, natt 9 h +10 dB som i
RL 18-18 bladet `Met. statistics`: 07–19, 19–22, 22–07) samt VTT-R-02530-06 (väderklasser
M1–M25).

Modulen innehåller:
- `berakna_lden_db`: exakt sammanvägning av dag-, kvälls- och nattnivå till L_den.
- `MetStatistik` + `las_met_statistik`: inläsning och cirkulär linjär interpolation av
  riktningsberoende klassandelar (%), temperatur (°C) och relativ luftfuktighet (%) ur
  bladet `Met. statistics`.
- `VaderklassBas`: de klasspecifika parametrarna (u, dt/dz, s_u, s_dt/dz, C_v², C_T², z_0, z_u)
  för en väderklass (t.ex. ur VTT-R-02530-06). Modulen hittar aldrig på dessa tal själv;
  saknas en väderklass med nollskild vikt ges ett tydligt `BullerFel`.
- `berakna_arsmedel_rak_vag`: beräknar L_dag, L_kvall, L_natt och L_den (spektrum + A-vägd
  totalnivå) för en rak väg med given normalriktning i grader från norr.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np

from buller.emission.modell import Trafikflode, Vagforhallanden
from buller.testfall.excelimport import Celler, _som_tal
from buller.testfall.format import N2kFall
from buller.testfall.korare import (
    Emissionsmodell,
    EmissionsmodellMedDirektivitet,
    forhallanden_for_fall,
    riktning_for_kallpunkt,
)
from buller.typer import ANTAL_BAND, BullerFel, Flyttal, P2PKarna, Vader

# Periodlängder i RL 18-18 bladet `Met. statistics`: dag 07–19 (12 h), kväll 19–22 (3 h),
# natt 22–07 (9 h).
PERIOD_TIMMAR_RL18: tuple[float, float, float] = (12.0, 3.0, 9.0)
LDEN_TILLAGG_DB: tuple[float, float, float] = (0.0, 5.0, 10.0)

# Normalriktning från väg mot mottagare (grader medurs från norr) för RL 18-18 årsmedel 1–4.
NORMALRIKTNING_ARSMEDEL_GRADER: Mapping[str, float] = {
    "N18A1": 90.0,  # N–S väg, mottagare öster om vägen
    "N18A2": 270.0,  # N–S väg, mottagare väster om vägen
    "N18A3": 180.0,  # Ö–V väg, mottagare söder om vägen
    "N18A4": 0.0,  # Ö–V väg, mottagare norr om vägen
}


@dataclass(frozen=True)
class VaderklassBas:
    """Fysikaliska parametrar för en väderklass (t.ex. M1–M25 ur VTT-R-02530-06).

    Temperatur och relativ fuktighet hämtas per riktning och period ur `MetStatistik`.
    """

    klass_id: str
    vind_m_s: float
    dt_dz_k_m: float
    std_vind_m_s: float
    std_dt_dz_k_m: float
    cv2: float
    ct2: float
    z0_m: float = 0.05
    zu_m: float = 10.0
    lufttryck_kpa: float = 101.325

    def som_vader(
        self, temperatur_c: float, relativ_fuktighet_procent: float, vikt: float = 1.0
    ) -> Vader:
        """Bygger ett `Vader`-objekt med given temperatur, luftfuktighet och klassvikt."""
        return Vader(
            temperatur_c=temperatur_c,
            relativ_fuktighet_procent=relativ_fuktighet_procent,
            lufttryck_kpa=self.lufttryck_kpa,
            vind_m_s=self.vind_m_s,
            zu_m=self.zu_m,
            z0_m=self.z0_m,
            dt_dz_k_m=self.dt_dz_k_m,
            std_vind_m_s=self.std_vind_m_s,
            std_dt_dz_k_m=self.std_dt_dz_k_m,
            cv2=self.cv2,
            ct2=self.ct2,
            vikt=vikt,
        )


@dataclass(frozen=True)
class PeriodStatistik:
    """Riktningsberoende andelar (0–1), temperatur (°C) och relativ fuktighet (%) för en period."""

    riktningar_grader: tuple[float, ...]
    andelar: Mapping[str, tuple[float, ...]]
    temperatur_c: Mapping[str, tuple[float | None, ...]]
    relativ_fuktighet_procent: Mapping[str, tuple[float | None, ...]]

    def _interpolera_cirkular(
        self, varden: Sequence[float | None], riktning_grader: float
    ) -> float | None:
        r = riktning_grader % 360.0
        n = len(self.riktningar_grader)
        if n == 0:
            return None
        # Hitta omgivande vinkelintervall på cirkeln [0, 360).
        for i in range(n):
            a = self.riktningar_grader[i]
            j = (i + 1) % n
            b = self.riktningar_grader[j] if j > 0 else self.riktningar_grader[0] + 360.0
            r_test = r if r >= a else r + 360.0
            if a <= r_test <= b:
                va, vb = varden[i], varden[j]
                if va is None or vb is None:
                    return va if vb is None else vb
                t = 0.0 if b == a else (r_test - a) / (b - a)
                return va + t * (vb - va)
        return None

    def aktiva_klasser(self) -> tuple[str, ...]:
        """Klass-id:n med någon andel > 0 i perioden."""
        return tuple(k for k, vals in self.andelar.items() if any(v > 0.0 for v in vals))

    def for_riktning(self, riktning_grader: float) -> dict[str, tuple[float, float, float]]:
        """Returnerar `{klass_id: (vikt, temp_c, rh_procent)}` för klasser med vikt > 0."""
        ut: dict[str, tuple[float, float, float]] = {}
        for klass, andelar in self.andelar.items():
            vikt = self._interpolera_cirkular(andelar, riktning_grader)
            if vikt is None or vikt <= 0.0:
                continue
            temp = self._interpolera_cirkular(self.temperatur_c.get(klass, ()), riktning_grader)
            rh = self._interpolera_cirkular(
                self.relativ_fuktighet_procent.get(klass, ()), riktning_grader
            )
            if temp is None or rh is None:
                raise BullerFel(
                    f"Met. statistics saknar temperatur eller luftfuktighet för {klass} "
                    f"vid riktning {riktning_grader:g}°."
                )
            ut[klass] = (vikt, temp, rh)
        return ut


@dataclass(frozen=True)
class MetStatistik:
    """Väderstatistik per period (dag, kväll, natt) ur bladet `Met. statistics`."""

    dag: PeriodStatistik
    kvall: PeriodStatistik
    natt: PeriodStatistik

    def aktiva_klasser(self) -> tuple[str, ...]:
        """Alla klass-id:n som har nollskild vikt i minst en period."""
        sedda: list[str] = []
        for p in (self.dag, self.kvall, self.natt):
            for k in p.aktiva_klasser():
                if k not in sedda:
                    sedda.append(k)
        return tuple(sedda)


@dataclass(frozen=True)
class ArsmedelResultat:
    """Beräknat årsmedel (L_dag, L_kvall, L_natt och L_den) per tersband och A-vägd totalnivå."""

    ldag_spektrum_db: Flyttal
    lkvall_spektrum_db: Flyttal
    lnatt_spektrum_db: Flyttal
    lden_spektrum_db: Flyttal
    ldag_a_db: float
    lkvall_a_db: float
    lnatt_a_db: float
    lden_a_db: float


def berakna_lden_db(
    ldag_db: Flyttal | float,
    lkvall_db: Flyttal | float,
    lnatt_db: Flyttal | float,
    *,
    timmar: tuple[float, float, float] = PERIOD_TIMMAR_RL18,
    tillagg_db: tuple[float, float, float] = LDEN_TILLAGG_DB,
) -> Flyttal:
    """L_den ur dag-, kvälls- och nattnivå enligt EU-direktiv 2002/49/EG och RL 18-18.

    `L_den = 10·lg((T_d·10^(L_d/10) + T_e·10^((L_e+5)/10) + T_n·10^((L_n+10)/10)) / 24)`.
    """
    t_dag, t_kvall, t_natt = timmar
    if not (t_dag > 0.0 and t_kvall > 0.0 and t_natt > 0.0):
        raise BullerFel("Periodlängderna i L_den ska vara större än 0.")
    t_tot = t_dag + t_kvall + t_natt
    if abs(t_tot - 24.0) > 1.0e-9:
        raise BullerFel(f"Periodlängderna i L_den ska summera till 24 h, fick {t_tot:g} h.")
    a_d, a_e, a_n = tillagg_db
    e_d = (t_dag / 24.0) * np.power(10.0, (np.asarray(ldag_db, dtype=np.float64) + a_d) / 10.0)
    e_e = (t_kvall / 24.0) * np.power(10.0, (np.asarray(lkvall_db, dtype=np.float64) + a_e) / 10.0)
    e_n = (t_natt / 24.0) * np.power(10.0, (np.asarray(lnatt_db, dtype=np.float64) + a_n) / 10.0)
    with np.errstate(divide="ignore"):
        return np.asarray(10.0 * np.log10(e_d + e_e + e_n), dtype=np.float64)


def _norm(text: str) -> str:
    return "".join(text.split()).lower()


def _las_tabellblock(
    celler: Celler, startrad: int, skala: float = 1.0
) -> tuple[tuple[float, ...], dict[str, tuple[float | None, ...]]]:
    """Läser en Direction + M1..M25-tabell från `startrad` (raden med rubriken 'Direction')."""
    rubrik = celler[startrad]
    riktningar: list[float] = []
    kolumner: list[int] = []
    for k in range(1, len(rubrik)):
        tal = _som_tal(rubrik[k])
        if tal is None:
            break
        riktningar.append(float(tal))
        kolumner.append(k)
    if not riktningar:
        raise BullerFel(f"Met. statistics rad {startrad + 1}: saknar riktningskolumner.")
    tabell: dict[str, tuple[float | None, ...]] = {}
    for r in range(startrad + 1, len(celler)):
        rad = celler[r]
        if not rad or not rad[0].strip().upper().startswith("M"):
            if tabell:
                break
            continue
        klass = rad[0].strip().upper()
        varden: list[float | None] = []
        for k in kolumner:
            tal = _som_tal(rad[k]) if k < len(rad) else None
            varden.append(None if tal is None else float(tal) * skala)
        tabell[klass] = tuple(varden)
    if not tabell:
        raise BullerFel(f"Met. statistics rad {startrad + 1}: hittade inga M-klasser.")
    return tuple(riktningar), tabell


def las_met_statistik(celler: Celler) -> MetStatistik:
    """Läser de nio tabellerna (andel, temperatur, RH × dag/kväll/natt) ur `Met. statistics`."""
    direction_rader = [r for r, rad in enumerate(celler) if rad and _norm(rad[0]) == "direction"]
    if len(direction_rader) < 9:
        raise BullerFel(
            f"Met. statistics väntade 9 Direction-tabeller (andel, temp, RH × dag/kväll/natt), "
            f"hittade {len(direction_rader)}."
        )
    perioder: list[PeriodStatistik] = []
    for p_idx in range(3):
        rikt_a, andelar_raw = _las_tabellblock(celler, direction_rader[p_idx], skala=0.01)
        rikt_t, temp = _las_tabellblock(celler, direction_rader[3 + p_idx], skala=1.0)
        rikt_rh, rh = _las_tabellblock(celler, direction_rader[6 + p_idx], skala=1.0)
        if rikt_a != rikt_t or rikt_a != rikt_rh:
            raise BullerFel("Met. statistics har olika riktningskolumner mellan tabellerna.")
        andelar = {
            k: tuple(0.0 if v is None else v for v in vals) for k, vals in andelar_raw.items()
        }
        perioder.append(
            PeriodStatistik(
                riktningar_grader=rikt_a,
                andelar=andelar,
                temperatur_c=temp,
                relativ_fuktighet_procent=rh,
            )
        )
    return MetStatistik(dag=perioder[0], kvall=perioder[1], natt=perioder[2])


def _energisumma_db(varden_db: Flyttal, axis: int | None = None) -> Flyttal:
    with np.errstate(divide="ignore"):
        return np.asarray(10.0 * np.log10(np.sum(10.0 ** (varden_db / 10.0), axis=axis)))


def berakna_arsmedel_rak_vag(
    karna: P2PKarna,
    emission: Emissionsmodell | EmissionsmodellMedDirektivitet,
    fall: N2kFall,
    met_statistik: MetStatistik,
    vaderklasser: Mapping[str, VaderklassBas],
    normalriktning_grader: float,
    a_vagning_db: Flyttal,
    *,
    antal_kallpunkter: int = 179,
    vinkelupplosning_grader: float = 1.0,
    timmar: tuple[float, float, float] = PERIOD_TIMMAR_RL18,
) -> ArsmedelResultat:
    """Beräknar L_dag, L_kvall, L_natt och L_den för ett årsmedelfall på rak väg (RL 18-18 kap. 5).

    Kräver att alla aktiva väderklasser i `met_statistik` finns i `vaderklasser` (ur
    VTT-R-02530-06); annars ges ett tydligt `BullerFel`.
    """
    if fall.terrang is None or fall.mottagare_x_m is None or fall.mottagare_hojd_m is None:
        raise BullerFel(f"Årsmedelfall {fall.id} saknar terräng eller mottagare.")
    if a_vagning_db.shape != (ANTAL_BAND,):
        raise BullerFel(f"a_vagning_db ska ha {ANTAL_BAND} värden.")
    saknade = [k for k in met_statistik.aktiva_klasser() if k not in vaderklasser]
    if saknade:
        raise BullerFel(
            "Saknar parametrar (VTT-R-02530-06) för aktiva väderklasser i Met. statistics: "
            f"{', '.join(saknade)}."
        )

    hastighet = float(fall.ovrigt["hastighet_km_h"])
    kategori = int(fall.ovrigt.get("fordonskategori", 1))
    antal_per_period = (
        float(fall.ovrigt["antal_fordon_dag"]),
        float(fall.ovrigt["antal_fordon_kvall"]),
        float(fall.ovrigt["antal_fordon_natt"]),
    )
    kalla_x_m = fall.terrang.x_m[0] if fall.vag is None else fall.vag.kalla_x_m
    avstand_m = fall.mottagare_x_m - kalla_x_m
    steg = math.radians(vinkelupplosning_grader)
    vinklar = (np.arange(antal_kallpunkter) - (antal_kallpunkter - 1) / 2.0) * steg
    hojder = emission.delkallhojder_m

    # Bygg ett temporärt fall med Vagdata för fall.profil() om vag saknas i N-fall/v1.
    from buller.testfall.format import Vagdata

    fall_med_vag = (
        fall
        if fall.vag is not None
        else replace(
            fall,
            vag=Vagdata(kalla_x_m, antal_kallpunkter, vinkelupplosning_grader),
        )
    )
    bas_forhallanden = forhallanden_for_fall(fall_med_vag)
    riktad = emission if isinstance(emission, EmissionsmodellMedDirektivitet) else None
    if riktad is not None and not riktad.stoder_direktivitet:
        riktad = None

    period_obj = (met_statistik.dag, met_statistik.kvall, met_statistik.natt)
    period_energi = [np.zeros(ANTAL_BAND, dtype=np.float64) for _ in range(3)]

    for vinkel in vinklar:
        v_float = float(vinkel)
        langd_m = avstand_m * (math.tan(v_float + steg / 2.0) - math.tan(v_float - steg / 2.0))
        profiler = [
            fall_med_vag.profil(kalla_hojd_m=h, stracka=1.0 / math.cos(v_float)) for h in hojder
        ]
        riktning_kalla = riktning_for_kallpunkt(v_float, profiler) if riktad is not None else None
        prop_riktning_grader = (normalriktning_grader + math.degrees(v_float)) % 360.0

        for p_idx, p_stat in enumerate(period_obj):
            if antal_per_period[p_idx] <= 0.0:
                continue
            period_s = timmar[p_idx] * 3600.0
            trafik = [Trafikflode(kategori, antal_per_period[p_idx], hastighet)]
            klasser = p_stat.for_riktning(prop_riktning_grader)
            for klass_id, (vikt, temp_c, rh) in klasser.items():
                forh = (
                    Vagforhallanden(
                        bas_forhallanden.belaggning,
                        bas_forhallanden.stenstorlek_mm,
                        lufttemperatur_c=temp_c,
                    )
                    if bas_forhallanden is not None
                    else None
                )
                if riktad is not None and riktning_kalla is not None:
                    effekt_db = riktad.ljudeffekt_db(
                        trafik, langd_m, period_s, hojder, forh, riktning_kalla
                    )
                else:
                    effekt_db = emission.ljudeffekt_db(trafik, langd_m, period_s, hojder, forh)
                vader = vaderklasser[klass_id].som_vader(temp_c, rh, vikt=1.0)
                for j, profil in enumerate(profiler):
                    h_db = np.asarray(karna.overforing_db(profil, vader), dtype=np.float64)
                    period_energi[p_idx] += vikt * np.power(10.0, (effekt_db[j, :] + h_db) / 10.0)

    with np.errstate(divide="ignore"):
        spektra = [np.asarray(10.0 * np.log10(e), dtype=np.float64) for e in period_energi]
    lden_spektrum = berakna_lden_db(spektra[0], spektra[1], spektra[2], timmar=timmar)
    a_tot = [float(_energisumma_db(s + a_vagning_db)) for s in spektra]
    lden_a = float(_energisumma_db(lden_spektrum + a_vagning_db))
    return ArsmedelResultat(
        ldag_spektrum_db=spektra[0],
        lkvall_spektrum_db=spektra[1],
        lnatt_spektrum_db=spektra[2],
        lden_spektrum_db=lden_spektrum,
        ldag_a_db=a_tot[0],
        lkvall_a_db=a_tot[1],
        lnatt_a_db=a_tot[2],
        lden_a_db=lden_a,
    )
