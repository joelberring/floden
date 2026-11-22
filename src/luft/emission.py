# SPDX-License-Identifier: MIT
"""Emissionsmodell för vägtrafik: vägdamm (NORTRIP-typ) och avgaser (`docs/LUFT.md` 2.5, L-g).

Översikt
--------
I Stockholms gaturum domineras PM10 av vägdamm från dubbdäcksslitage, sandning och
uppvirvling, och vägbanans fukt avgör när dammet kommer upp i luften. Modulen är en
förenklad men strukturellt trogen version av NORTRIP (NOn-exhaust Road TRaffic Induced
Particle emissions, Denby & Sundvor 2012, NILU OR 23/2012; Denby m.fl. 2013a, 2013b):

1. **Vägfuktmodell** (vattenmagasin ``g`` mm på vägbanan), se `_vattensteg`.
2. **Retentionsfaktor** ``f_q ∈ [0, 1]`` ur fukten (1 = torrt, 0 = vått), se `retentionsfaktor`.
3. **Massbalans för vägytans massa** per vägavsnitt, källa och storleksklass, se `berakna_emission`:

       dM/dt = P − R·M,     R = R_upp·f_q + R_spray + R_stad,     därefter dränering.

4. **Utsläpp** = direkt slitage (andel ``f_dir·f_q``) + uppvirvling (``R_upp·f_q·M``) per
   storleksklass, plus avgaser (emissionsfaktor × flöde).

Källor och ekvationsnummer
--------------------------
Själva rapporten OR 23/2012 gick inte att hämta i det här arbetspasset (nilu.no svarade
med fel), så ekvationsnummer därifrån anges **inte**. Hänvisningarna avser:

* **[D13a]** Denby m.fl. 2013, *NORTRIP Part 1: road dust loading and suspension
  modelling*, Atmos. Environ. 77, 283–300 – här läst genom återgivningen i
* **[DAN26]** Denby, Aas & Norman 2026, *Estimating the Contribution of Traction Sanding
  to Observed PM10 Concentrations Using Mass Balance Arguments*, Environ. Model. Assess.
  31, 2165–2187, som anger att deras ekv. 7–20 är hämtade från [D13a]. Ekvationsnumren
  nedan med prefixet DAN26 är numren i den artikeln.
* **[D13b]** Denby m.fl. 2013, *NORTRIP Part 2: surface moisture and salt impact
  modelling*, Atmos. Environ. 81, 485–503 (ekv. 1, 3, 4, 9, 10; avsnitt 2.4–2.7).
* **[NP]** NORTRIP_python (metno, github.com/metno/NORTRIP_python, LGPL-3.0) –
  referensimplementationen, *läst* för att fastställa funktionsformer och
  parametertabellen ``Road_dust_parameter_table_v12_params.txt``. Ingen kod är kopierad.
* **[MET16]** MET report 05/2016 (Meteorologisk institutt), appendix 1: parametertabell
  för operativ NORTRIP i Norge, skriver ut ``W = W0·(a1 + a2·(V/Vref)^a3)``.

Vad som är förenklat jämfört med NORTRIP
---------------------------------------
* Ingen snö/is: all nederbörd är vatten, ingen plogning, ingen snöspärr för slitage
  (NORTRIP ``s_roadwear_thresh``), ingen smältning/frysning. Saltets inverkan på
  ytfuktighet och fryspunkt [D13b avsnitt 2.8–2.9] ingår inte.
* Ingen ytenergibalans [D13b 2.7]: avdunstning = potentiell avdunstning (Penman, som
  ``evap_pot`` i [NP] ``penman_modified_func``, med markvärmeflöde 0) × linjär
  ytfuktighet ``min(1, g/g_avd)`` [D13b 2.7.5, linjär form]. Kondensation ingår inte.
  Potentiell avdunstning kan i stället ges direkt i väderserien.
* Dränering endast av typen "momentan" ([NP] ``drainage_type_flag = 2``) och
  dammdränering med kontinuerlig blandning ([NP] ``dust_drainage_flag = 2``).
* Ett spår (ingen in-/ytterspårsfördelning), en hastighet för alla fordon, två
  däckklasser (dubb/ej dubb; NORTRIP:s vinter- och sommardäck har samma tal i v12).
* Ingen nötning/krossning av sand, ingen vinduppvirvling, ingen deposition av
  bakgrundspartiklar, ingen hastighetsberoende storleksfördelning (v12 har
  ``c_PM-fraction = 0``), salt löses inte upp (allt salt behandlas som partiklar).
* Bromsens retention (``g_retention`` för broms) dämpar bara det direkta bromsutsläppet.
  Bromsdamm som ligger på vägytan virvlas upp med vägens ``f_q`` (i [NP] används
  bromsens ``f_q`` även för uppvirvlingen av källan broms).
* Avgaspartiklar släpps ut direkt (NORTRIP lägger 2 % på vägytan, ``f0,dir-exhaust = 0,98``).
* Massbalansens sänkor fördelas exakt: med konstanta P och R under timmen är
  ``∫ R_i M dt = (R_i/R)·(M0 + P·Δt − M1)``. [NP] diagnostiserar i stället med ``R_i·M0``.

Inga parametrar är inbyggda. Allt står i `EmissionsParametrar` (utan standardvärden).
`testparametrar()` ger värden ur NORTRIP-dokumentationen med källa per värde; objektet är
märkt ``ar_testvarde=True`` och varje resultat från det bär en varningsflagga.

Koppling till spridningen
-------------------------
Halten är linjär i utsläppen och skalar med ``u_ref/U`` vid neutral skiktning
(`docs/LUFT.md` 2.2–2.3). Om ``C_{v,d}`` är halten från väg ``v`` med enhetsutsläpp
1 g/(m·s) löst i riktning ``d`` vid ``u_ref`` (t.ex. `TransportOperator.los` på
``linjekalla(grid, [linje_v], [1.0])``) är timhalten

    halt_h = (u_ref/U_h) · Σ_c w_{h,c} · Σ_v e_{v,h} · C_{v, d(h,c)},

där ``e_{v,h}`` (g/(m·s)) kommer från `EmissionsResultat.emissionsmatris` och
``d(h,c), w_{h,c}, u_ref/U_h`` från `luft.vindbibliotek.fordela`. `timvis_halt` räknar
årsmedel, max och antal timmar över en gräns strömmande, block för block, utan att
lagra alla timfält. För ett enda källfält räcker `emission_for_linjekalla`.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np
import numpy.typing as npt

from luft.geometri import LuftVarning
from luft.typer import LuftFel
from luft.vindbibliotek import Fordelning

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]

# ---------------------------------------------------------------------------
# Index för källor och storleksklasser
# ---------------------------------------------------------------------------

KALLOR: tuple[str, ...] = (
    "vagslitage",
    "dackslitage",
    "bromsslitage",
    "sand",
    "salt",
    "dammbindare",
)
I_VAG, I_DACK, I_BROMS, I_SAND, I_SALT, I_BINDARE = range(6)
SLITAGEKALLOR = (I_VAG, I_DACK, I_BROMS)
DAMMKALLOR = (I_VAG, I_DACK, I_BROMS, I_SAND)

# Disjunkta storleksklasser, samma kolumner som NORTRIP:s effektivitetstabell [NP v12]:
# "PMall - PM200", "PM200 - PM10", "PM10 - PM2.5", "PM2.5".
STORLEKAR: tuple[str, ...] = ("over_pm200", "pm200_pm10", "pm10_pm25", "under_pm25")
PM10_KLASSER = (2, 3)
PM25_KLASSER = (3,)

ATGARDSTYPER = ("sandning", "saltning", "dammbindning", "stadning")
RETENTIONSFORMER = ("exponentiell", "linjar")
AMNEN = ("pm10", "pm25", "nox")

# ---------------------------------------------------------------------------
# Fysikaliska konstanter (inte modellparametrar)
# ---------------------------------------------------------------------------

LAMBDA_J_KG = 2.5e6  # ångbildningsvärme, [D13b] tabell B.1
CP_J_KG_K = 1006.0  # värmekapacitet torr luft, som i [NP] penman_modified_func
RD_J_KG_K = 287.0  # gaskonstant torr luft, [D13b] tabell B.1
KARMAN = 0.4  # von Kármáns konstant, [D13b] tabell B.1
T0_K = 273.15
# Magnus formel för mättnadsångtryck över vatten (hPa, °C), samma koefficienter som [NP] q_sat_func
MAGNUS_A_HPA = 6.1121
MAGNUS_B = 17.67
MAGNUS_C_C = 243.5
STANDARDTRYCK_HPA = 1013.25  # standardatmosfär, används bara om tryck saknas (flaggas)
# Numeriska skydd (inte fysik): som i [NP] mass_balance_func / R_cleaning / r_aero_func
_STAD_MAX_ANDEL = 0.99999
_TRAFIKTURBULENS_MIN_M_S = 1e-6


# ---------------------------------------------------------------------------
# Parametrar
# ---------------------------------------------------------------------------


def _kontrollera_tal(namn: str, v: float, *, min_varde: float | None = 0.0) -> None:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise LuftFel(f"{namn} måste vara ett ändligt tal (fick {v!r}).")
    if min_varde is not None and v < min_varde:
        raise LuftFel(f"{namn} får inte vara mindre än {min_varde} (fick {v}).")


def _kontrollera_andel(namn: str, v: float) -> None:
    _kontrollera_tal(namn, v)
    if v > 1.0:
        raise LuftFel(f"{namn} är en andel och måste ligga i [0, 1] (fick {v}).")


@dataclass(frozen=True)
class Hastighetsberoende:
    """Hastighetsfaktor ``h_V = max(0, a1 + a2·(max(V, V_min)/V_ref)^a3)``.

    Samma form för slitage (``W = W0·h_V``) och uppvirvling (``f_upp = f0·h_V``):
    [MET16] appendix 1 och rubrikraderna i [NP v12]-tabellen; [NP] ``w_func``,
    ``f_susroad_func``. [DAN26] ekv. 38 är specialfallet a1 = 0, a2 = a3 = 1.
    """

    a1: float
    a2: float
    a3: float
    v_ref_km_h: float
    v_min_km_h: float

    def __post_init__(self) -> None:
        for namn in ("a1", "a2", "a3"):
            _kontrollera_tal(namn, getattr(self, namn), min_varde=None)
        _kontrollera_tal("v_ref_km_h", self.v_ref_km_h)
        _kontrollera_tal("v_min_km_h", self.v_min_km_h)
        if self.v_ref_km_h <= 0:
            raise LuftFel("v_ref_km_h måste vara positiv.")

    def faktor(self, hastighet_km_h: npt.ArrayLike) -> FloatArray:
        v = np.maximum(np.asarray(hastighet_km_h, dtype=np.float64), self.v_min_km_h)
        ut: FloatArray = np.maximum(0.0, self.a1 + self.a2 * (v / self.v_ref_km_h) ** self.a3)
        return ut


@dataclass(frozen=True)
class Storleksfordelning:
    """Kumulativa massandelar ``f_PM`` (PM200, PM10, PM2.5) för en källa.

    NORTRIP anger ``f_PM,s`` kumulativt (PMall = 1); [D13a] via [DAN26] ekv. 9, 14.
    `klassandelar` ger andelen i de fyra disjunkta klasserna i `STORLEKAR`.
    """

    andel_pm200: float
    andel_pm10: float
    andel_pm25: float

    def __post_init__(self) -> None:
        for namn in ("andel_pm200", "andel_pm10", "andel_pm25"):
            _kontrollera_andel(namn, getattr(self, namn))
        if not (self.andel_pm200 >= self.andel_pm10 >= self.andel_pm25):
            raise LuftFel("Storleksandelarna måste uppfylla PM200 ≥ PM10 ≥ PM2.5.")

    def klassandelar(self) -> FloatArray:
        return np.array(
            [
                1.0 - self.andel_pm200,
                self.andel_pm200 - self.andel_pm10,
                self.andel_pm10 - self.andel_pm25,
                self.andel_pm25,
            ],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class Klassvarden:
    """Ett värde per disjunkt storleksklass (`STORLEKAR`)."""

    over_pm200: float
    pm200_pm10: float
    pm10_pm25: float
    under_pm25: float

    def __post_init__(self) -> None:
        for f in fields(self):
            _kontrollera_tal(f.name, getattr(self, f.name))

    @classmethod
    def lika(cls, varde: float) -> Klassvarden:
        return cls(varde, varde, varde, varde)

    def som_array(self) -> FloatArray:
        return np.array(
            [self.over_pm200, self.pm200_pm10, self.pm10_pm25, self.under_pm25],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class Retention:
    """Gränser för retention: ``f_q = 1`` under `min_varde`, starkt dämpad över `troskel`.

    [D13b] (hänvisar till [D13a] ekv. 18–19): ``g_retention-min`` och
    ``g_retention-thresh``. Enhet mm vatten (väg, broms) eller g/m² (dammbindare).
    """

    min_varde: float
    troskel: float

    def __post_init__(self) -> None:
        _kontrollera_tal("min_varde", self.min_varde)
        _kontrollera_tal("troskel", self.troskel)
        if self.troskel <= self.min_varde:
            raise LuftFel("Retention: troskel måste vara större än min_varde.")


@dataclass(frozen=True)
class EmissionsParametrar:
    """Alla modellparametrar. Inga standardvärden: skarpa värden måste anges.

    Enheter: slitage och avgaser i g per fordonskilometer (g/km/fordon),
    uppvirvling och spray i 1/fordon (andel per passerande fordon och körfält),
    fukt i mm vatten. Se `testparametrar` för värden ur NORTRIP med källa.
    """

    # --- Slitage, W0 [D13a] via [DAN26] ekv. 9; [NP v12] "Road/Tyre/Brake wear" ---
    vagslitage_dubb_latt_g_km: float
    vagslitage_dubb_tung_g_km: float
    vagslitage_ejdubb_latt_g_km: float
    vagslitage_ejdubb_tung_g_km: float
    dackslitage_latt_g_km: float
    dackslitage_tung_g_km: float
    bromsslitage_latt_g_km: float
    bromsslitage_tung_g_km: float
    hastighet_vagslitage: Hastighetsberoende
    hastighet_dackslitage: Hastighetsberoende
    hastighet_bromsslitage: Hastighetsberoende
    # --- Direktandel f_dir, [DAN26] ekv. 8, 13 ---
    direktandel_vagslitage: float
    direktandel_dackslitage: float
    direktandel_bromsslitage: float
    # --- Storleksfördelning f_PM per källa ---
    storlek_vagslitage: Storleksfordelning
    storlek_dackslitage: Storleksfordelning
    storlek_bromsslitage: Storleksfordelning
    storlek_sand: Storleksfordelning
    storlek_salt: Storleksfordelning
    storlek_dammbindare: Storleksfordelning
    # --- Uppvirvling, [DAN26] ekv. 10–11 ---
    uppvirvling_latt_per_fordon: float
    uppvirvling_tung_per_fordon: float
    hastighet_uppvirvling: Hastighetsberoende
    uppvirvling_skalning: tuple[Klassvarden, ...]  # h0,sus per källa i KALLOR-ordning
    # --- Borttagning (effektivitet per händelse/per enhet), [NP v12] "Activity efficiency" ---
    stadeffekt_damm: Klassvarden
    draneringseffekt_damm: Klassvarden
    sprayeffekt_damm: Klassvarden
    stadeffekt_salt: float  # gäller salt och dammbindare
    draneringseffekt_salt: float
    sprayeffekt_salt: float
    # --- Fukt och retention ---
    retention_vag_mm: Retention
    retention_broms_mm: Retention
    retention_dammbindare_g_m2: Retention
    retention_form: str  # "exponentiell" ([NP] retention_flag 2) eller "linjar" (flag 1)
    spray_latt_per_fordon: float
    spray_tung_per_fordon: float
    spray_v_ref_km_h: float
    spray_v_troskel_km_h: float
    spray_exponent: float
    spray_min_mm: float  # g_road,sprayable-min
    draneringsgrans_mm: float  # g_road,drainable-min
    avdunstning_troskel_mm: float  # g_road,evap-thresh
    rahetslangd_m: float  # z0 för Penmans aerodynamiska motstånd
    rahetslangd_varme_m: float  # z0t
    trafikturbulens_latt: float  # a_traffic
    trafikturbulens_tung: float
    vind_min_m_s: float  # undre gräns för vinden i r_aero
    # --- Avgaser (emissionsfaktorer, g/km/fordon) ---
    nox_latt_g_km: float
    nox_tung_g_km: float
    pm_avgas_latt_g_km: float
    pm_avgas_tung_g_km: float
    # --- Märkning ---
    ar_testvarde: bool = False

    def __post_init__(self) -> None:
        andelar = {
            "direktandel_vagslitage",
            "direktandel_dackslitage",
            "direktandel_bromsslitage",
            "stadeffekt_salt",
            "draneringseffekt_salt",
            "sprayeffekt_salt",
        }
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name == "ar_testvarde":
                if not isinstance(v, bool):
                    raise LuftFel("ar_testvarde måste vara bool.")
            elif f.name == "retention_form":
                if v not in RETENTIONSFORMER:
                    raise LuftFel(f"retention_form måste vara en av {RETENTIONSFORMER}.")
            elif f.name == "uppvirvling_skalning":
                if (
                    not isinstance(v, tuple)
                    or len(v) != len(KALLOR)
                    or not all(isinstance(k, Klassvarden) for k in v)
                ):
                    raise LuftFel(
                        f"uppvirvling_skalning ska vara {len(KALLOR)} Klassvarden ({KALLOR})."
                    )
            elif isinstance(v, Hastighetsberoende | Storleksfordelning | Klassvarden | Retention):
                pass  # validerade i sina egna __post_init__
            elif f.name in andelar:
                _kontrollera_andel(f.name, v)
            else:
                _kontrollera_tal(f.name, v)
        for namn in ("stadeffekt_damm", "draneringseffekt_damm", "sprayeffekt_damm"):
            if np.any(getattr(self, namn).som_array() > 1.0):
                raise LuftFel(f"{namn} är andelar och måste ligga i [0, 1].")
        if self.spray_v_ref_km_h <= self.spray_v_troskel_km_h:
            raise LuftFel("spray_v_ref_km_h måste vara större än spray_v_troskel_km_h.")
        if self.rahetslangd_m <= 0 or self.rahetslangd_varme_m <= 0:
            raise LuftFel("Råhetslängderna måste vara positiva.")
        if self.avdunstning_troskel_mm <= 0:
            raise LuftFel("avdunstning_troskel_mm måste vara positiv.")
        if self.vind_min_m_s <= 0:
            raise LuftFel("vind_min_m_s måste vara positiv.")

    # Hjälpare för beräkningen -------------------------------------------------

    def storlek_per_kalla(self) -> FloatArray:
        """(6, 4) klassandelar per källa i `KALLOR`-ordning."""
        return np.stack(
            [
                self.storlek_vagslitage.klassandelar(),
                self.storlek_dackslitage.klassandelar(),
                self.storlek_bromsslitage.klassandelar(),
                self.storlek_sand.klassandelar(),
                self.storlek_salt.klassandelar(),
                self.storlek_dammbindare.klassandelar(),
            ]
        )

    def effekt_per_kalla(self, typ: str) -> FloatArray:
        """(6, 4) borttagningseffektivitet (`stad`, `dranering`, `spray`) per källa och klass."""
        prefix = {"stad": "stad", "dranering": "dranerings", "spray": "spray"}.get(typ)
        if prefix is None:
            raise LuftFel(f"Okänd borttagningstyp '{typ}'.")
        damm: Klassvarden = getattr(self, f"{prefix}effekt_damm")
        salt: float = getattr(self, f"{prefix}effekt_salt")
        ut = np.empty((len(KALLOR), len(STORLEKAR)), dtype=np.float64)
        ut[list(DAMMKALLOR)] = damm.som_array()
        ut[[I_SALT, I_BINDARE]] = salt
        return ut


_K_V12 = "[NP] Road_dust_parameter_table_v12_params.txt"
_K_DEL2 = "[D13b] tabell C.2"
_K_DAN26 = "[DAN26]"
_K_KOD = "[NP] källkod"
SAKNAR_KALLA = "SAKNAR KÄLLA – syntetiskt testvärde"

# Källa per fält i testparametrar(). Visas i resultatets varningar.
TESTPARAMETRAR_KALLOR: dict[str, str] = {
    "vagslitage_dubb_latt_g_km": f"{_K_V12}, W0,roadwear Light st = 2.88; [DAN26] ekv. 36",
    "vagslitage_dubb_tung_g_km": f"{_K_V12}, W0,roadwear Heavy st = 14.4",
    "vagslitage_ejdubb_latt_g_km": f"{_K_V12}, W0,roadwear Light wi/su = 0.072",
    "vagslitage_ejdubb_tung_g_km": f"{_K_V12}, W0,roadwear Heavy wi/su = 0.36",
    "dackslitage_latt_g_km": f"{_K_V12}, W0,tyrewear Light = 0.1",
    "dackslitage_tung_g_km": f"{_K_V12}, W0,tyrewear Heavy = 0.5",
    "bromsslitage_latt_g_km": f"{_K_V12}, W0,brakewear Light = 0.01",
    "bromsslitage_tung_g_km": f"{_K_V12}, W0,brakewear Heavy = 0.05",
    "hastighet_vagslitage": f"{_K_V12}, a = (0, 1, 1), Vref = 70, Vmin = 30",
    "hastighet_dackslitage": f"{_K_V12}, a = (0, 1, 1), Vref = 70, Vmin = 30",
    "hastighet_bromsslitage": f"{_K_V12}, a = (1, -0.75, 1), Vref = 120, Vmin = 30",
    "direktandel_vagslitage": f"{_K_V12}, f0,dir-roadwear = 0.75; {_K_DAN26} 'currently 0.75'",
    "direktandel_dackslitage": f"{_K_V12}, f0,dir-tyrewear = 0.75",
    "direktandel_bromsslitage": f"{_K_V12}, f0,dir-brakewear = 0.75",
    "storlek_vagslitage": f"{_K_V12}, fPM,roadwear = (1, 0.28, 0.0224)",
    "storlek_dackslitage": f"{_K_V12}, fPM,tyrewear = (1, 0.1, 0.01)",
    "storlek_bromsslitage": f"{_K_V12}, fPM,brakewear = (1, 0.8, 0.5)",
    "storlek_sand": f"{_K_V12}, fPM,sand = (0.025, 0.004, 0.001)",
    "storlek_salt": f"{_K_V12}, fPM,salt(1) = (1, 0.28, 0.0224)",
    "storlek_dammbindare": f"{_K_V12}, fPM,salt(2) = (1, 0.28, 0.0224)",
    "uppvirvling_latt_per_fordon": f"{_K_V12}, f0,suspension Light = 3.0e-6",
    "uppvirvling_tung_per_fordon": f"{_K_V12}, f0,suspension Heavy = 1.2e-4",
    "hastighet_uppvirvling": f"{_K_V12}, a = (0, 1, 1), Vref = 50, Vmin = 20",
    "uppvirvling_skalning": f"{_K_V12}, h0: 1 överallt utom h0,sand PMall-PM200 = 100",
    "stadeffekt_damm": f"{_K_V12}, hcleaning-eff = (0.5, 0.001, 0.001, 0.001)",
    "draneringseffekt_damm": f"{_K_V12}, hdrainage-eff = (0.05, 0.005, 0.005, 0.005)",
    "sprayeffekt_damm": f"{_K_V12}, hspraying-eff = 0.1",
    "stadeffekt_salt": f"{_K_V12}, hcleaning-eff salt = 0.2",
    "draneringseffekt_salt": f"{_K_V12}, hdrainage-eff salt = 1",
    "sprayeffekt_salt": f"{_K_V12}, hspraying-eff salt = 1",
    "retention_vag_mm": f"{_K_V12}, gretention-min/thresh Road = 0.01 / 0.03 mm",
    "retention_broms_mm": f"{_K_V12}, gretention-min/thresh Brake = 0.1 / 0.5 mm",
    "retention_dammbindare_g_m2": f"{_K_V12}, Dust binder = 50 / 250 g/m2",
    "retention_form": "[NP] flaggfil, retention_flag = 2 (exponentiell)",
    "spray_latt_per_fordon": f"{_K_V12}, f0,spray li = 2.0e-5",
    "spray_tung_per_fordon": f"{_K_V12}, f0,spray he = 1.2e-4",
    "spray_v_ref_km_h": f"{_K_V12}, Vref,spray = 80",
    "spray_v_troskel_km_h": f"{_K_V12}, Vthresh,spray = 10",
    "spray_exponent": f"{_K_V12}, aspray = 3",
    "spray_min_mm": f"{_K_V12}, groad,sprayable-min = 0.05 mm",
    "draneringsgrans_mm": f"{_K_V12}, gdrainable = 0.6 mm",
    "avdunstning_troskel_mm": f"{_K_V12}, groad,evap-thresh = 0.02 mm; {_K_DEL2}",
    "rahetslangd_m": f"{_K_V12}, Roughness length = 10 mm",
    "rahetslangd_varme_m": f"{_K_KOD} road_dust_surface_wetness: z0t = z0/10",
    "trafikturbulens_latt": f"{_K_V12}, atraffic Light = 1.0e-3",
    "trafikturbulens_tung": f"{_K_V12}, atraffic Heavy = 1.0e-2",
    "vind_min_m_s": f"{_K_KOD} r_aero_func: max(FF, 0.2)",
    "nox_latt_g_km": SAKNAR_KALLA,
    "nox_tung_g_km": SAKNAR_KALLA,
    "pm_avgas_latt_g_km": SAKNAR_KALLA,
    "pm_avgas_tung_g_km": SAKNAR_KALLA,
}


def testparametrar() -> EmissionsParametrar:
    """Testvärden ur NORTRIP-dokumentationen (källa per fält i `TESTPARAMETRAR_KALLOR`).

    **Endast för tester.** Objektet är märkt ``ar_testvarde=True`` och resultat från det
    flaggas. Avgasfaktorerna saknar källa och är syntetiska (värdet 1 g/km för NOx och
    0,01 g/km för PM är valda för att vara uppenbart påhittade). Värdena i v12-tabellen
    skiljer sig från andra NORTRIP-publikationer (t.ex. retention 0,04/0,1 mm i [D13b]
    tabell C.2 och 0,02/0,06 mm i [MET16]): parametrarna är kalibrerade per tillämpning.
    """
    ett = Klassvarden.lika(1.0)
    return EmissionsParametrar(
        vagslitage_dubb_latt_g_km=2.88,
        vagslitage_dubb_tung_g_km=14.4,
        vagslitage_ejdubb_latt_g_km=0.072,
        vagslitage_ejdubb_tung_g_km=0.36,
        dackslitage_latt_g_km=0.1,
        dackslitage_tung_g_km=0.5,
        bromsslitage_latt_g_km=0.01,
        bromsslitage_tung_g_km=0.05,
        hastighet_vagslitage=Hastighetsberoende(0.0, 1.0, 1.0, 70.0, 30.0),
        hastighet_dackslitage=Hastighetsberoende(0.0, 1.0, 1.0, 70.0, 30.0),
        hastighet_bromsslitage=Hastighetsberoende(1.0, -0.75, 1.0, 120.0, 30.0),
        direktandel_vagslitage=0.75,
        direktandel_dackslitage=0.75,
        direktandel_bromsslitage=0.75,
        storlek_vagslitage=Storleksfordelning(1.0, 0.28, 0.0224),
        storlek_dackslitage=Storleksfordelning(1.0, 0.1, 0.01),
        storlek_bromsslitage=Storleksfordelning(1.0, 0.8, 0.5),
        storlek_sand=Storleksfordelning(0.025, 0.004, 0.001),
        storlek_salt=Storleksfordelning(1.0, 0.28, 0.0224),
        storlek_dammbindare=Storleksfordelning(1.0, 0.28, 0.0224),
        uppvirvling_latt_per_fordon=3.0e-6,
        uppvirvling_tung_per_fordon=1.2e-4,
        hastighet_uppvirvling=Hastighetsberoende(0.0, 1.0, 1.0, 50.0, 20.0),
        uppvirvling_skalning=(ett, ett, ett, Klassvarden(100.0, 1.0, 1.0, 1.0), ett, ett),
        stadeffekt_damm=Klassvarden(0.5, 0.001, 0.001, 0.001),
        draneringseffekt_damm=Klassvarden(0.05, 0.005, 0.005, 0.005),
        sprayeffekt_damm=Klassvarden.lika(0.1),
        stadeffekt_salt=0.2,
        draneringseffekt_salt=1.0,
        sprayeffekt_salt=1.0,
        retention_vag_mm=Retention(0.01, 0.03),
        retention_broms_mm=Retention(0.1, 0.5),
        retention_dammbindare_g_m2=Retention(50.0, 250.0),
        retention_form="exponentiell",
        spray_latt_per_fordon=2.0e-5,
        spray_tung_per_fordon=1.2e-4,
        spray_v_ref_km_h=80.0,
        spray_v_troskel_km_h=10.0,
        spray_exponent=3.0,
        spray_min_mm=0.05,
        draneringsgrans_mm=0.6,
        avdunstning_troskel_mm=0.02,
        rahetslangd_m=0.010,
        rahetslangd_varme_m=0.001,
        trafikturbulens_latt=1.0e-3,
        trafikturbulens_tung=1.0e-2,
        vind_min_m_s=0.2,
        nox_latt_g_km=1.0,  # SAKNAR KÄLLA – syntetiskt
        nox_tung_g_km=1.0,  # SAKNAR KÄLLA – syntetiskt
        pm_avgas_latt_g_km=0.01,  # SAKNAR KÄLLA – syntetiskt
        pm_avgas_tung_g_km=0.01,  # SAKNAR KÄLLA – syntetiskt
        ar_testvarde=True,
    )


# ---------------------------------------------------------------------------
# Indata
# ---------------------------------------------------------------------------


def _manadsvarden(namn: str, v: float | Sequence[float]) -> tuple[float, ...]:
    if isinstance(v, int | float) and not isinstance(v, bool):
        vals: tuple[float, ...] = (float(v),) * 12
    else:
        vals = tuple(float(x) for x in v)
    if len(vals) != 12:
        raise LuftFel(f"{namn} ska vara ett tal eller 12 månadsvärden (fick {len(vals)}).")
    for x in vals:
        _kontrollera_andel(namn, x)
    return vals


@dataclass(frozen=True)
class Vagavsnitt:
    """Ett vägavsnitt med trafik.

    `andel_dubbdack_*` är andelen fordon med dubbdäck, ett tal eller 12 månadsvärden
    (januari först). `belaggningsfaktor` (NORTRIP ``h_pave``, påverkar vägslitaget) och
    `korcykelfaktor` (``h_dc``, påverkar bromsslitaget) är 1 för referensfallet, vilket
    är ett neutralt värde och ingen kalibrering.
    """

    id: str
    langd_m: float
    adt_fordon_dygn: float
    andel_tung: float
    andel_dubbdack_latt: float | Sequence[float]
    andel_dubbdack_tung: float | Sequence[float]
    hastighet_km_h: float
    antal_korfalt: int
    vagbredd_m: float
    belaggningsfaktor: float = 1.0
    korcykelfaktor: float = 1.0

    def __post_init__(self) -> None:
        if not self.id:
            raise LuftFel("Vagavsnitt.id får inte vara tomt.")
        _kontrollera_tal("langd_m", self.langd_m)
        _kontrollera_tal("adt_fordon_dygn", self.adt_fordon_dygn)
        _kontrollera_andel("andel_tung", self.andel_tung)
        _kontrollera_tal("hastighet_km_h", self.hastighet_km_h)
        _kontrollera_tal("belaggningsfaktor", self.belaggningsfaktor)
        _kontrollera_tal("korcykelfaktor", self.korcykelfaktor)
        if not isinstance(self.antal_korfalt, int) or self.antal_korfalt < 1:
            raise LuftFel("antal_korfalt måste vara ett heltal ≥ 1.")
        _kontrollera_tal("vagbredd_m", self.vagbredd_m)
        if self.vagbredd_m <= 0:
            raise LuftFel("vagbredd_m måste vara positiv.")
        object.__setattr__(
            self,
            "andel_dubbdack_latt",
            _manadsvarden("andel_dubbdack_latt", self.andel_dubbdack_latt),
        )
        object.__setattr__(
            self,
            "andel_dubbdack_tung",
            _manadsvarden("andel_dubbdack_tung", self.andel_dubbdack_tung),
        )


@dataclass(frozen=True)
class Trafikprofil:
    """Fördelning av ÅDT på timmar: ``N_h = ÅDT · dygn[timme] · vecka[veckodag] · manad[månad]``.

    `dygn_andel` (24 värden, summa 1), `veckodag_faktor` (7, måndag först) och
    `manad_faktor` (12) är trafikdata, inga modellparametrar. `jamn()` ger en jämn profil.
    """

    dygn_andel: tuple[float, ...]
    veckodag_faktor: tuple[float, ...]
    manad_faktor: tuple[float, ...]

    def __post_init__(self) -> None:
        for namn, n in (("dygn_andel", 24), ("veckodag_faktor", 7), ("manad_faktor", 12)):
            v = tuple(float(x) for x in getattr(self, namn))
            if len(v) != n:
                raise LuftFel(f"Trafikprofil.{namn} ska ha {n} värden.")
            for x in v:
                _kontrollera_tal(namn, x)
            object.__setattr__(self, namn, v)
        if abs(sum(self.dygn_andel) - 1.0) > 1e-9:
            raise LuftFel("Trafikprofil.dygn_andel måste summera till 1.")

    @classmethod
    def jamn(cls) -> Trafikprofil:
        return cls((1.0 / 24.0,) * 24, (1.0,) * 7, (1.0,) * 12)


def _som_serie(namn: str, v: npt.ArrayLike, n: int | None) -> FloatArray:
    a = np.asarray(v, dtype=np.float64).ravel()
    if n is not None and a.shape != (n,):
        raise LuftFel(f"VaderserieTimme.{namn} ska ha {n} värden (fick {a.size}).")
    if not np.all(np.isfinite(a)):
        raise LuftFel(f"VaderserieTimme.{namn} innehåller NaN/Inf; fyll eller kapa serien.")
    return a


@dataclass(frozen=True)
class VaderserieTimme:
    """Timvisa väderdata för emissionsmodellen. Inga saknade värden tillåts.

    `start` är första timmens början i ISO-format (t.ex. ``"2025-01-01T00:00"``, lokal
    tid); timme, veckodag och månad räknas från den. `vindhojd_m` och `temperaturhojd_m`
    är mäthöjderna (för Penmans aerodynamiska motstånd). Valfria fält: `lufttryck_hpa`
    (saknas: standardatmosfär, flaggas), `nettostralning_w_m2` (saknas: bara den
    aerodynamiska termen, flaggas) och `potentiell_avdunstning_mm_h` (ges den används den
    i stället för Penman).
    """

    start: str
    temperatur_c: FloatArray
    nederbord_mm_h: FloatArray
    vindhastighet_m_s: FloatArray
    relativ_fuktighet_procent: FloatArray
    vindhojd_m: float
    temperaturhojd_m: float
    lufttryck_hpa: FloatArray | None = None
    nettostralning_w_m2: FloatArray | None = None
    potentiell_avdunstning_mm_h: FloatArray | None = None

    def __post_init__(self) -> None:
        t = _som_serie("temperatur_c", self.temperatur_c, None)
        n = t.size
        if n == 0:
            raise LuftFel("Väderserien är tom.")
        object.__setattr__(self, "temperatur_c", t)
        for namn in ("nederbord_mm_h", "vindhastighet_m_s", "relativ_fuktighet_procent"):
            object.__setattr__(self, namn, _som_serie(namn, getattr(self, namn), n))
        for namn in ("lufttryck_hpa", "nettostralning_w_m2", "potentiell_avdunstning_mm_h"):
            v = getattr(self, namn)
            if v is not None:
                object.__setattr__(self, namn, _som_serie(namn, v, n))
        if np.any(self.nederbord_mm_h < 0) or np.any(self.vindhastighet_m_s < 0):
            raise LuftFel("Nederbörd och vind får inte vara negativa.")
        rf = self.relativ_fuktighet_procent
        if np.any((rf < 0) | (rf > 100)):
            raise LuftFel("Relativ fuktighet ska ligga i [0, 100] %.")
        if self.potentiell_avdunstning_mm_h is not None and np.any(
            self.potentiell_avdunstning_mm_h < 0
        ):
            raise LuftFel("potentiell_avdunstning_mm_h får inte vara negativ.")
        if self.lufttryck_hpa is not None and np.any(self.lufttryck_hpa <= 0):
            raise LuftFel("lufttryck_hpa måste vara positivt.")
        _kontrollera_tal("vindhojd_m", self.vindhojd_m)
        _kontrollera_tal("temperaturhojd_m", self.temperaturhojd_m)
        try:
            np.datetime64(self.start, "h")
        except ValueError as exc:
            raise LuftFel(f"Ogiltig starttid '{self.start}': {exc}") from exc

    @property
    def antal_timmar(self) -> int:
        return int(self.temperatur_c.size)

    def kalender(self) -> tuple[IntArray, IntArray, IntArray]:
        """(timme 0–23, veckodag 0 = måndag, månad 0–11) för varje timme."""
        t0 = np.datetime64(self.start, "h")
        tider = t0 + np.arange(self.antal_timmar).astype("timedelta64[h]")
        timmar = tider.astype(np.int64)
        timme = (timmar % 24).astype(np.int64)
        dagar = timmar // 24
        veckodag = ((dagar + 3) % 7).astype(np.int64)  # 1970-01-01 var en torsdag
        manad = (tider.astype("datetime64[M]").astype(np.int64) % 12).astype(np.int64)
        return timme, veckodag, manad


Vaderserie_timme = VaderserieTimme  # namn enligt uppdragsbeskrivningen


@dataclass(frozen=True)
class Atgard:
    """Driftåtgärd på vägen under en viss timme (index från väderseriens start).

    `mangd`: sandning, saltning och dammbindning i g/m² torrsubstans; städning som
    städinsats ``t`` (dimensionslös, 1 = en städning), som tar bort andelen
    ``min(h_städ·t, 0,99999)`` av massan i varje storleksklass ([NP] R_cleaning).
    `avsnitt_id = None` betyder alla avsnitt.
    """

    typ: str
    timme: int
    mangd: float
    avsnitt_id: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.typ not in ATGARDSTYPER:
            raise LuftFel(f"Okänd åtgärd '{self.typ}', tillåtna: {ATGARDSTYPER}.")
        if not isinstance(self.timme, int) or self.timme < 0:
            raise LuftFel("Atgard.timme måste vara ett heltal ≥ 0.")
        _kontrollera_tal("mangd", self.mangd)


# ---------------------------------------------------------------------------
# Delmodeller
# ---------------------------------------------------------------------------


def retentionsfaktor(varde: npt.ArrayLike, retention: Retention, form: str) -> FloatArray:
    """Retentionsfaktor ``f_q`` (1 = torrt, ingen dämpning; → 0 vått).

    ``r = (g − g_min)/(g_thresh − g_min)``. Exponentiell form ``f_q = exp(−2·max(0, r))``
    ([NP] retention_flag = 2) ger en jämn övergång; linjär form ``f_q = clip(1 − r, 0, 1)``
    (flag 1). Gränserna: [D13b] (som hänvisar till [D13a] ekv. 18–19).
    """
    g = np.asarray(varde, dtype=np.float64)
    r = (g - retention.min_varde) / (retention.troskel - retention.min_varde)
    if form == "exponentiell":
        ut: FloatArray = np.exp(-2.0 * np.maximum(0.0, r))
    elif form == "linjar":
        ut = np.clip(1.0 - r, 0.0, 1.0)
    else:
        raise LuftFel(f"Okänd retentionsform '{form}'.")
    return ut


def mattnadsfuktighet(
    temperatur_c: npt.ArrayLike, tryck_hpa: npt.ArrayLike
) -> tuple[FloatArray, FloatArray]:
    """Mättnadsspecifik fuktighet ``q_sat`` (kg/kg) och ``dq_sat/dT`` (1/K), Magnus formel."""
    t = np.asarray(temperatur_c, dtype=np.float64)
    p = np.asarray(tryck_hpa, dtype=np.float64)
    esat = MAGNUS_A_HPA * np.exp(MAGNUS_B * t / (MAGNUS_C_C + t))
    qsat: FloatArray = 0.622 * esat / (p - 0.378 * esat)
    desat = esat * MAGNUS_B * MAGNUS_C_C / (MAGNUS_C_C + t) ** 2
    dqsat: FloatArray = 0.622 * p / (p - 0.378 * esat) ** 2 * desat
    return qsat, dqsat


def aerodynamiskt_motstand_s_m(
    vind_m_s: npt.ArrayLike,
    vindhojd_m: float,
    temperaturhojd_m: float,
    param: EmissionsParametrar,
    fordon_latt_h: npt.ArrayLike,
    fordon_tung_h: npt.ArrayLike,
    hastighet_km_h: npt.ArrayLike,
) -> FloatArray:
    """Aerodynamiskt motstånd ``r_a`` (s/m), neutral skiktning plus trafikturbulens.

    ``1/r_a = κ²·max(U, U_min)/(ln(z_U/z0)·ln(z_T/z0t)) + max(ε, Σ_v N_v·V·a_v/3600/3,6)``
    (form som [NP] ``r_aero_func``; trafiktermen [D13b] tabell C.2 ``α_traffic``).
    """
    if vindhojd_m <= param.rahetslangd_m or temperaturhojd_m <= param.rahetslangd_varme_m:
        raise LuftFel("Mäthöjderna måste vara större än råhetslängderna.")
    u = np.maximum(np.asarray(vind_m_s, dtype=np.float64), param.vind_min_m_s)
    inv_vind = (
        u
        * KARMAN**2
        / (
            math.log(vindhojd_m / param.rahetslangd_m)
            * math.log(temperaturhojd_m / param.rahetslangd_varme_m)
        )
    )
    nl = np.asarray(fordon_latt_h, dtype=np.float64)
    nt = np.asarray(fordon_tung_h, dtype=np.float64)
    v = np.asarray(hastighet_km_h, dtype=np.float64)
    inv_trafik = np.maximum(
        _TRAFIKTURBULENS_MIN_M_S,
        (nl * v * param.trafikturbulens_latt + nt * v * param.trafikturbulens_tung) / 3600.0 / 3.6,
    )
    ut: FloatArray = 1.0 / (inv_vind + inv_trafik)
    return ut


def potentiell_avdunstning_mm_h(
    temperatur_c: npt.ArrayLike,
    relativ_fuktighet_procent: npt.ArrayLike,
    tryck_hpa: npt.ArrayLike,
    nettostralning_w_m2: npt.ArrayLike,
    r_aero_s_m: npt.ArrayLike,
) -> FloatArray:
    """Potentiell avdunstning (mm/h) enligt Penman, som ``evap_pot`` i [NP] penman_modified_func.

    ``E = 3600/λ · (γ'·R_n + (1 − γ')·ρ·λ·q_sat·(1 − RF)/r_a)``, ``γ' = q'/(q' + c_p/λ)``.
    Markvärmeflödet sätts till 0 (ingen ytenergibalans, se modulens förenklingar).
    Negativa värden (kondensation) sätts till 0.
    """
    t = np.asarray(temperatur_c, dtype=np.float64)
    rf = np.asarray(relativ_fuktighet_procent, dtype=np.float64)
    p = np.asarray(tryck_hpa, dtype=np.float64)
    rn = np.asarray(nettostralning_w_m2, dtype=np.float64)
    ra = np.asarray(r_aero_s_m, dtype=np.float64)
    qsat, dqsat = mattnadsfuktighet(t, p)
    rho = p * 100.0 / (RD_J_KG_K * (T0_K + t))
    gamma = dqsat / (dqsat + CP_J_KG_K / LAMBDA_J_KG)
    qdef = qsat * (1.0 - rf / 100.0)
    e = 3600.0 / LAMBDA_J_KG * (gamma * rn + (1.0 - gamma) * rho * LAMBDA_J_KG * qdef / ra)
    ut: FloatArray = np.maximum(0.0, e)
    return ut


def _avdunsta(g_mm: FloatArray, e_mm_h: FloatArray, troskel_mm: float, dt_h: float) -> FloatArray:
    """Exakt lösning av ``dg/dt = −E·min(1, g/g_avd)`` över ``dt`` (styckvis lösning)."""
    e = np.maximum(e_mm_h, 0.0)
    ovan = g_mm > troskel_mm
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = np.where(e > 0, (g_mm - troskel_mm) / np.where(e > 0, e, 1.0), np.inf)
    rest = np.maximum(0.0, dt_h - np.where(ovan, t1, 0.0))
    exp_del = np.where(ovan, troskel_mm, g_mm) * np.exp(-e / troskel_mm * rest)
    ut: FloatArray = np.where(ovan & (t1 >= dt_h), g_mm - e * dt_h, exp_del)
    return np.maximum(ut, 0.0)


# ---------------------------------------------------------------------------
# Resultat
# ---------------------------------------------------------------------------


@dataclass
class EmissionsResultat:
    """Timserier per vägavsnitt, form (n_avsnitt, n_timmar). Utsläpp i g/(m·s) längs vägen.

    `massbalans` innehåller summor per (avsnitt, källa, klass) i g/m vägsträcka:
    ``start``, ``slut``, ``produktion``, ``uppvirvling``, ``spray``, ``stadning``,
    ``dranering``. `vattenbalans` innehåller summor per avsnitt i mm.
    """

    avsnitt_id: tuple[str, ...]
    langd_m: FloatArray
    start: str
    pm10_g_m_s: FloatArray
    pm25_g_m_s: FloatArray
    nox_g_m_s: FloatArray
    pm10_direkt_g_m_s: FloatArray
    pm10_uppvirvling_g_m_s: FloatArray
    pm10_avgas_g_m_s: FloatArray
    pm25_uppvirvling_g_m_s: FloatArray
    damm_g_m2: FloatArray
    damm_pm10_g_m2: FloatArray
    salt_g_m2: FloatArray
    fukt_mm: FloatArray
    f_q: FloatArray
    massbalans: dict[str, FloatArray]
    vattenbalans: dict[str, FloatArray]
    produktion_per_kalla_g_m: FloatArray  # (n_avsnitt, 6), alla klasser, till vägytan
    ar_testvarde: bool
    varningar: list[str] = field(default_factory=list)

    @property
    def antal_timmar(self) -> int:
        return int(self.pm10_g_m_s.shape[1])

    def emissionsmatris(self, amne: str) -> FloatArray:
        """(n_avsnitt, n_timmar) i g/(m·s) för `amne` ∈ {"pm10", "pm25", "nox"}."""
        if amne not in AMNEN:
            raise LuftFel(f"Okänt ämne '{amne}', tillåtna: {AMNEN}.")
        ut: FloatArray = getattr(self, f"{amne}_g_m_s")
        return ut

    def massbalans_relfel(self) -> float:
        """``|Σ produktion − Σ borttagning − (slut − start)| / Σ produktion`` över allt."""
        mb = self.massbalans
        bort = mb["uppvirvling"] + mb["spray"] + mb["stadning"] + mb["dranering"]
        fel = float(np.sum(mb["produktion"]) - np.sum(bort) - np.sum(mb["slut"] - mb["start"]))
        nam = float(np.sum(mb["produktion"]))
        return abs(fel) / nam if nam > 0 else abs(fel)

    def vattenbalans_relfel(self) -> float:
        vb = self.vattenbalans
        fel = float(
            np.sum(vb["nederbord"])
            - np.sum(vb["dranering"] + vb["spray"] + vb["avdunstning"])
            - np.sum(vb["slut"] - vb["start"])
        )
        nam = float(np.sum(vb["nederbord"]))
        return abs(fel) / nam if nam > 0 else abs(fel)

    def sammanfattning(self) -> dict[str, Any]:
        """Medelvärden och andelar per avsnitt, samt testvärdesflagga och varningar."""
        per_avsnitt: list[dict[str, Any]] = []
        for i, aid in enumerate(self.avsnitt_id):
            pm10 = float(np.sum(self.pm10_g_m_s[i]))
            pm25 = float(np.sum(self.pm25_g_m_s[i]))
            per_avsnitt.append(
                {
                    "id": aid,
                    "arsmedel_pm10_g_m_s": float(np.mean(self.pm10_g_m_s[i])),
                    "arsmedel_pm25_g_m_s": float(np.mean(self.pm25_g_m_s[i])),
                    "arsmedel_nox_g_m_s": float(np.mean(self.nox_g_m_s[i])),
                    "andel_uppvirvling_pm10": (
                        float(np.sum(self.pm10_uppvirvling_g_m_s[i])) / pm10 if pm10 > 0 else 0.0
                    ),
                    "andel_direkt_pm10": (
                        float(np.sum(self.pm10_direkt_g_m_s[i])) / pm10 if pm10 > 0 else 0.0
                    ),
                    "andel_avgas_pm10": (
                        float(np.sum(self.pm10_avgas_g_m_s[i])) / pm10 if pm10 > 0 else 0.0
                    ),
                    "andel_uppvirvling_pm25": (
                        float(np.sum(self.pm25_uppvirvling_g_m_s[i])) / pm25 if pm25 > 0 else 0.0
                    ),
                    "medel_damm_g_m2": float(np.mean(self.damm_g_m2[i])),
                    "medel_damm_pm10_g_m2": float(np.mean(self.damm_pm10_g_m2[i])),
                    "andel_vata_timmar": float(np.mean(self.f_q[i] < 0.5)),
                    "utslapp_pm10_kg_ar": pm10 * 3600.0 * float(self.langd_m[i]) / 1000.0,
                }
            )
        ut: dict[str, Any] = {
            "antal_timmar": self.antal_timmar,
            "start": self.start,
            "avsnitt": per_avsnitt,
            "massbalans_relfel": self.massbalans_relfel(),
            "vattenbalans_relfel": self.vattenbalans_relfel(),
            "varningar": list(self.varningar),
        }
        if self.ar_testvarde:
            ut["ar_testvarde"] = True
        return ut


# ---------------------------------------------------------------------------
# Huvudberäkning
# ---------------------------------------------------------------------------


def _vattensteg(
    g0: FloatArray,
    regn_mm: float | FloatArray,
    r_spray_1_h: FloatArray,
    e_pot_mm_h: FloatArray,
    param: EmissionsParametrar,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]:
    """En timme i vägfuktmodellen.

    Returnerar (g_slut, dränerat, sprayat, avdunstat, g_efter_dränering), allt i mm.

    [D13b] ekv. 1, 3, 4: ``dg/dt = P_regn − S_dränering − S_spray − S_avdunstning``, löst med
    operatorsplittring i den ordningen:

    1. Nederbörd läggs på.
    2. Momentan dränering ned till ``g_drainable-min`` ([D13b] ekv. 9–10, [NP] typ 2).
    3. Spray: överskottet över ``g_sprayable-min`` avtar som ``exp(−R_spray·Δt)``,
       ``R_spray = Σ_v N_v·f_spray,v(V)/n_körfält`` ([D13b] avsnitt 2.5; hastighetsform
       ``((V − V_tr)/(V_ref − V_tr))^a`` som [NP] ``f_spray_func``).
    4. Avdunstning ``dg/dt = −E_pot·min(1, g/g_avd)``, exakt lösning (`_avdunsta`).
    """
    g1 = g0 + regn_mm
    # Vatten över gränsen dräneras momentant, vatten under gränsen ligger kvar.
    g2 = np.minimum(g1, param.draneringsgrans_mm)
    dranerat = g1 - g2
    overskott = np.maximum(0.0, g2 - param.spray_min_mm)
    sprayat = overskott * (-np.expm1(-r_spray_1_h))
    g3 = g2 - sprayat
    g4 = _avdunsta(g3, e_pot_mm_h, param.avdunstning_troskel_mm, 1.0)
    return g4, dranerat, sprayat, g3 - g4, g2


def berakna_emission(
    avsnitt: Sequence[Vagavsnitt],
    vader: VaderserieTimme,
    trafikprofil: Trafikprofil,
    param: EmissionsParametrar,
    atgarder: Sequence[Atgard] = (),
    startmassa_g_m2: npt.ArrayLike | None = None,
    startfukt_mm: npt.ArrayLike | None = None,
    *,
    sno_smaltning: bool = False,
    sno_smaltfaktor_mm_k_h: float = 0.15,
    sno_sparr_mm: float = 0.5,
) -> EmissionsResultat:
    """Kör emissionsmodellen timme för timme för alla vägavsnitt.

    Massbalans per avsnitt ``a``, källa ``s`` (`KALLOR`) och storleksklass ``x``
    (`STORLEKAR`), med massan ``M`` i g per meter väg ([D13a] via [DAN26] ekv. 12–20):

    * Slitage ``W_s = Σ_v Σ_däck N·W0·h_V(V)·h_pave/h_dc`` (g/m/h; [DAN26] ekv. 9).
    * Produktion till vägytan ``P = W_s·(1 − f_dir·f_q)·f_x`` ([DAN26] ekv. 13),
      direkt utsläpp ``E_dir = W_s·f_dir·f_q·f_x`` ([DAN26] ekv. 8); sand, salt och
      dammbindare ``P = mängd·bredd·f_x`` vid åtgärden ([DAN26] ekv. 14).
    * Borttagningstakt ``R = R_upp + R_spray + R_städ`` (1/h) med
      ``R_upp = Σ_v N_v/n_körfält·f_upp,v·h_V(V)·f_q·h0`` ([DAN26] ekv. 10–11),
      ``R_spray = R_spray,vatten·h_spray`` när vägen är sprayvåt, och
      ``R_städ = −ln(1 − h_städ·t)/Δt`` ([NP] R_cleaning).
    * Tidssteg: ``M1 = M0·e^{−RΔt} + P·(1 − e^{−RΔt})/R`` ([NP] mass_balance_func), och
      sänkorna fördelas exakt med ``R_i/R``.
    * Dränering efter steget: ``M ← M·exp(−h_drän·d/(g_drän + d))``, där ``d`` är dränerat
      vatten (mm) ([NP] dust_drainage_flag = 2; [D13b] avsnitt 2.4 nämner
      effektivitetsfaktorerna).
    * Retention ``f_q`` från vägens vatten och dammbindarens massa (multiplikativt, som
      i [NP]); för direkt bromsutsläpp används bromsens retentionsgränser.

    Utsläpp: PM10 = Σ_s (E_dir + E_upp) i klasserna under PM10 + avgas-PM, PM2.5 likaså;
    NOx = Σ_v N_v·EF_v. Allt i g/(m·s).
    """
    if not avsnitt:
        raise LuftFel("Minst ett vägavsnitt krävs.")
    ids = tuple(a.id for a in avsnitt)
    if len(set(ids)) != len(ids):
        raise LuftFel("Vägavsnittens id måste vara unika.")
    na = len(avsnitt)
    nh = vader.antal_timmar
    nk, ns = len(KALLOR), len(STORLEKAR)
    varningar: list[str] = []
    if param.ar_testvarde:
        utan = sorted(k for k, v in TESTPARAMETRAR_KALLOR.items() if v == SAKNAR_KALLA)
        varningar.append(
            "TESTVÄRDEN: parametrarna är märkta ar_testvarde=True och får inte användas skarpt."
            + (f" Utan källa: {', '.join(utan)}." if utan else "")
        )

    # --- Trafik per timme (n_avsnitt, n_timmar) ---
    timme, veckodag, manad = vader.kalender()
    prof = (
        np.asarray(trafikprofil.dygn_andel)[timme]
        * np.asarray(trafikprofil.veckodag_faktor)[veckodag]
        * np.asarray(trafikprofil.manad_faktor)[manad]
    )
    adt = np.array([a.adt_fordon_dygn for a in avsnitt])
    andel_tung = np.array([a.andel_tung for a in avsnitt])
    n_tot = adt[:, None] * prof[None, :]
    n_tung = n_tot * andel_tung[:, None]
    n_latt = n_tot - n_tung
    dubb_l = np.array([a.andel_dubbdack_latt for a in avsnitt], dtype=np.float64)[:, manad]
    dubb_t = np.array([a.andel_dubbdack_tung for a in avsnitt], dtype=np.float64)[:, manad]
    v = np.array([a.hastighet_km_h for a in avsnitt])
    n_korfalt = np.array([a.antal_korfalt for a in avsnitt], dtype=np.float64)
    bredd = np.array([a.vagbredd_m for a in avsnitt])
    h_pave = np.array([a.belaggningsfaktor for a in avsnitt])
    h_dc = np.array([a.korcykelfaktor for a in avsnitt])

    # --- Slitage (g/m/h) ---
    p = param
    w = np.zeros((na, nh, 3))
    w[:, :, I_VAG] = (
        n_latt
        * (dubb_l * p.vagslitage_dubb_latt_g_km + (1 - dubb_l) * p.vagslitage_ejdubb_latt_g_km)
        + n_tung
        * (dubb_t * p.vagslitage_dubb_tung_g_km + (1 - dubb_t) * p.vagslitage_ejdubb_tung_g_km)
    ) * (p.hastighet_vagslitage.faktor(v) * h_pave)[:, None]
    w[:, :, I_DACK] = (
        n_latt * p.dackslitage_latt_g_km + n_tung * p.dackslitage_tung_g_km
    ) * p.hastighet_dackslitage.faktor(v)[:, None]
    w[:, :, I_BROMS] = (n_latt * p.bromsslitage_latt_g_km + n_tung * p.bromsslitage_tung_g_km) * (
        p.hastighet_bromsslitage.faktor(v) * h_dc
    )[:, None]
    w /= 1000.0  # g/km → g/m
    fdir = np.array(
        [p.direktandel_vagslitage, p.direktandel_dackslitage, p.direktandel_bromsslitage]
    )

    # --- Uppvirvling och spray (1/h) ---
    r_upp_bas = (
        (n_latt * p.uppvirvling_latt_per_fordon + n_tung * p.uppvirvling_tung_per_fordon)
        / n_korfalt[:, None]
        * p.hastighet_uppvirvling.faktor(v)[:, None]
    )
    f_spray_v = (
        np.maximum(0.0, v - p.spray_v_troskel_km_h) / (p.spray_v_ref_km_h - p.spray_v_troskel_km_h)
    ) ** p.spray_exponent
    r_spray_vatten = (
        (n_latt * p.spray_latt_per_fordon + n_tung * p.spray_tung_per_fordon)
        / n_korfalt[:, None]
        * f_spray_v[:, None]
    )

    # --- Potentiell avdunstning (mm/h) ---
    if vader.potentiell_avdunstning_mm_h is not None:
        e_pot = np.broadcast_to(vader.potentiell_avdunstning_mm_h[None, :], (na, nh))
    else:
        if vader.lufttryck_hpa is None:
            tryck = np.full(nh, STANDARDTRYCK_HPA)
            varningar.append("Lufttryck saknas: standardatmosfär 1013,25 hPa används.")
        else:
            tryck = vader.lufttryck_hpa
        if vader.nettostralning_w_m2 is None:
            rn = np.zeros(nh)
            varningar.append(
                "Nettostrålning saknas: avdunstningen har bara Penmans aerodynamiska term."
            )
        else:
            rn = vader.nettostralning_w_m2
        r_a = aerodynamiskt_motstand_s_m(
            vader.vindhastighet_m_s[None, :],
            vader.vindhojd_m,
            vader.temperaturhojd_m,
            p,
            n_latt,
            n_tung,
            v[:, None],
        )
        e_pot = potentiell_avdunstning_mm_h(
            vader.temperatur_c[None, :],
            vader.relativ_fuktighet_procent[None, :],
            tryck[None, :],
            rn[None, :],
            r_a,
        )

    # --- Avgaser (g/m/s) ---
    nox = (n_latt * p.nox_latt_g_km + n_tung * p.nox_tung_g_km) / 1000.0 / 3600.0
    pm_avgas = (n_latt * p.pm_avgas_latt_g_km + n_tung * p.pm_avgas_tung_g_km) / 1000.0 / 3600.0

    # --- Åtgärder per timme ---
    id_index = {aid: i for i, aid in enumerate(ids)}
    atgard_per_timme: dict[int, list[tuple[str, IntArray, float]]] = {}
    for atg in atgarder:
        if atg.timme >= nh:
            raise LuftFel(f"Åtgärd '{atg.typ}' timme {atg.timme} ligger utanför serien ({nh} h).")
        if atg.avsnitt_id is None:
            idx = np.arange(na, dtype=np.int64)
        else:
            okanda = [a for a in atg.avsnitt_id if a not in id_index]
            if okanda:
                raise LuftFel(f"Åtgärd avser okända avsnitt: {okanda}.")
            idx = np.array([id_index[a] for a in atg.avsnitt_id], dtype=np.int64)
        atgard_per_timme.setdefault(atg.timme, []).append((atg.typ, idx, atg.mangd))

    # --- Parametrar som arrayer ---
    fx = p.storlek_per_kalla()  # (6, 4)
    h0 = np.stack([k.som_array() for k in p.uppvirvling_skalning])  # (6, 4)
    eff_spray = p.effekt_per_kalla("spray")
    eff_stad = p.effekt_per_kalla("stad")
    eff_dran = p.effekt_per_kalla("dranering")
    i_pm10 = list(PM10_KLASSER)
    i_pm25 = list(PM25_KLASSER)

    # --- Starttillstånd ---
    if startmassa_g_m2 is None:
        m = np.zeros((na, nk, ns))
        varningar.append(
            "Startmassa 0 g/m²: kör en insvängningsperiod (NORTRIP kör från 1 augusti året före)."
        )
    else:
        m0 = np.asarray(startmassa_g_m2, dtype=np.float64)
        if m0.shape != (na, nk, ns) or not np.all(np.isfinite(m0)) or np.any(m0 < 0):
            raise LuftFel(f"startmassa_g_m2 ska ha formen ({na}, {nk}, {ns}) och vara ≥ 0.")
        m = m0 * bredd[:, None, None]
    if startfukt_mm is None:
        g = np.zeros(na)
    else:
        g = np.broadcast_to(np.asarray(startfukt_mm, dtype=np.float64), (na,)).copy()
        if not np.all(np.isfinite(g)) or np.any(g < 0):
            raise LuftFel("startfukt_mm måste vara ändlig och ≥ 0.")

    mb = {
        k: np.zeros((na, nk, ns))
        for k in ("produktion", "uppvirvling", "spray", "stadning", "dranering")
    }
    mb["start"] = m.copy()
    vb = {k: np.zeros(na) for k in ("nederbord", "dranering", "spray", "avdunstning")}
    vb["start"] = g.copy()
    prod_kalla = np.zeros((na, nk))

    ut_pm10 = np.empty((na, nh))
    ut_pm25 = np.empty((na, nh))
    ut_pm10_dir = np.empty((na, nh))
    ut_pm10_upp = np.empty((na, nh))
    ut_pm25_upp = np.empty((na, nh))
    ut_damm = np.empty((na, nh))
    ut_damm10 = np.empty((na, nh))
    ut_salt = np.empty((na, nh))
    ut_fukt = np.empty((na, nh))
    ut_fq = np.empty((na, nh))
    g_sno = np.zeros(na, dtype=np.float64)
    stad_kapad = 0

    for h in range(nh):
        # 1) Vägfukt (med valfri massbevarande snö- och graddags-smältning)
        regn = float(vader.nederbord_mm_h[h])
        if sno_smaltning:
            temp_c = float(vader.temperatur_c[h])
            if temp_c <= 0.0:
                g_sno += regn
                regn_eff: float | FloatArray = 0.0
            else:
                smalt = np.minimum(g_sno, sno_smaltfaktor_mm_k_h * temp_c)
                g_sno -= smalt
                regn_eff = regn + smalt
        else:
            regn_eff = regn
        g_ny, dran, spray_v, avd, g_efter_dran = _vattensteg(
            g, regn_eff, r_spray_vatten[:, h], e_pot[:, h], p
        )
        vb["nederbord"] += regn
        vb["dranering"] += dran
        vb["spray"] += spray_v
        vb["avdunstning"] += avd
        g = g_ny

        # 2) Retention
        fq_vag = retentionsfaktor(g, p.retention_vag_mm, p.retention_form)
        fq_broms = retentionsfaktor(g, p.retention_broms_mm, p.retention_form)
        bindare_g_m2 = m[:, I_BINDARE, :].sum(axis=1) / bredd
        fq_bind = retentionsfaktor(bindare_g_m2, p.retention_dammbindare_g_m2, p.retention_form)
        if sno_smaltning and sno_sparr_mm > 0.0:
            fq_sno = np.clip(1.0 - g_sno / sno_sparr_mm, 0.0, 1.0)
        else:
            fq_sno = np.ones(na, dtype=np.float64)
        fq_yta = fq_vag * fq_bind * fq_sno  # för allt som ligger på vägytan
        fq = np.repeat(fq_yta[:, None], nk, axis=1)  # för direkt utsläpp per källa
        fq[:, I_BROMS] = fq_broms

        # 3) Produktion och direkt utsläpp (g/m under timmen)
        prod = np.zeros((na, nk, ns))
        e_dir = np.zeros((na, nk, ns))
        for s in SLITAGEKALLOR:
            ws = w[:, h, s]
            prod[:, s, :] = (ws * (1.0 - fdir[s] * fq[:, s]))[:, None] * fx[s]
            e_dir[:, s, :] = (ws * fdir[s] * fq[:, s])[:, None] * fx[s]
        t_stad = np.zeros(na)
        for typ, idx, mangd in atgard_per_timme.get(h, []):
            if typ == "stadning":
                t_stad[idx] += mangd
            else:
                s = {"sandning": I_SAND, "saltning": I_SALT, "dammbindning": I_BINDARE}[typ]
                prod[idx, s, :] += (mangd * bredd[idx])[:, None] * fx[s]

        # 4) Borttagningstakter (1/h)
        r_upp = r_upp_bas[:, h, None, None] * fq_yta[:, None, None] * h0[None, :, :]
        sprayvat = g_efter_dran >= p.spray_min_mm
        r_spr = np.where(sprayvat, r_spray_vatten[:, h], 0.0)[:, None, None] * eff_spray[None]
        andel_stad = eff_stad[None, :, :] * t_stad[:, None, None]
        if np.any(andel_stad > _STAD_MAX_ANDEL):
            stad_kapad += 1
        r_stad = -np.log1p(-np.minimum(_STAD_MAX_ANDEL, andel_stad))
        r_tot = r_upp + r_spr + r_stad

        # 5) Massbalans med analytisk lösning över timmen
        exp_r = np.exp(-r_tot)
        with np.errstate(divide="ignore", invalid="ignore"):
            faktor = np.where(r_tot > 0, -np.expm1(-r_tot) / np.where(r_tot > 0, r_tot, 1.0), 1.0)
        m1 = m * exp_r + prod * faktor
        borttaget = m + prod - m1
        with np.errstate(divide="ignore", invalid="ignore"):
            andel = np.where(r_tot > 0, borttaget / np.where(r_tot > 0, r_tot, 1.0), 0.0)
        s_upp = r_upp * andel
        s_spr = r_spr * andel
        s_stad = borttaget - s_upp - s_spr  # resten, så att summan är exakt

        # 6) Dränering
        d_faktor = dran / (p.draneringsgrans_mm + dran)
        s_dran = m1 * (-np.expm1(-eff_dran[None] * d_faktor[:, None, None]))
        m = m1 - s_dran

        mb["produktion"] += prod
        mb["uppvirvling"] += s_upp
        mb["spray"] += s_spr
        mb["stadning"] += s_stad
        mb["dranering"] += s_dran
        prod_kalla += prod.sum(axis=2)

        # 7) Utsläpp (g/m/h → g/m/s)
        upp10 = s_upp[:, :, i_pm10].sum(axis=(1, 2))
        upp25 = s_upp[:, :, i_pm25].sum(axis=(1, 2))
        dir10 = e_dir[:, :, i_pm10].sum(axis=(1, 2))
        dir25 = e_dir[:, :, i_pm25].sum(axis=(1, 2))
        ut_pm10_upp[:, h] = upp10 / 3600.0
        ut_pm25_upp[:, h] = upp25 / 3600.0
        ut_pm10_dir[:, h] = dir10 / 3600.0
        ut_pm10[:, h] = (upp10 + dir10) / 3600.0 + pm_avgas[:, h]
        ut_pm25[:, h] = (upp25 + dir25) / 3600.0 + pm_avgas[:, h]
        ut_damm[:, h] = m[:, list(DAMMKALLOR), :].sum(axis=(1, 2)) / bredd
        ut_damm10[:, h] = m[:, list(DAMMKALLOR)][:, :, i_pm10].sum(axis=(1, 2)) / bredd
        ut_salt[:, h] = m[:, I_SALT, :].sum(axis=1) / bredd
        ut_fukt[:, h] = g
        ut_fq[:, h] = fq_yta

    mb["slut"] = m.copy()
    vb["slut"] = (g + g_sno).copy() if sno_smaltning else g.copy()
    if stad_kapad:
        varningar.append(
            f"Städinsatsen gav borttagningsandel > {_STAD_MAX_ANDEL} under {stad_kapad} timmar; "
            "andelen kapades (som i NORTRIP)."
        )

    resultat = EmissionsResultat(
        avsnitt_id=ids,
        langd_m=np.array([a.langd_m for a in avsnitt]),
        start=vader.start,
        pm10_g_m_s=ut_pm10,
        pm25_g_m_s=ut_pm25,
        nox_g_m_s=np.ascontiguousarray(nox),
        pm10_direkt_g_m_s=ut_pm10_dir,
        pm10_uppvirvling_g_m_s=ut_pm10_upp,
        pm10_avgas_g_m_s=np.ascontiguousarray(pm_avgas),
        pm25_uppvirvling_g_m_s=ut_pm25_upp,
        damm_g_m2=ut_damm,
        damm_pm10_g_m2=ut_damm10,
        salt_g_m2=ut_salt,
        fukt_mm=ut_fukt,
        f_q=ut_fq,
        massbalans=mb,
        vattenbalans=vb,
        produktion_per_kalla_g_m=prod_kalla,
        ar_testvarde=param.ar_testvarde,
        varningar=varningar,
    )
    if param.ar_testvarde:
        warnings.warn(varningar[0], LuftVarning, stacklevel=2)
    return resultat


# ---------------------------------------------------------------------------
# Koppling till spridningen
# ---------------------------------------------------------------------------


def emission_for_linjekalla(
    resultat: EmissionsResultat, amne: str = "pm10", timme: int | None = None
) -> FloatArray:
    """``emission_g_m_s`` per vägavsnitt (ordning `resultat.avsnitt_id`) för `linjekalla`.

    `timme = None` ger årsmedel (medel över alla timmar i serien), annars värdet för
    timmen med det indexet.
    """
    e = resultat.emissionsmatris(amne)
    if timme is None:
        ut: FloatArray = e.mean(axis=1)
        return ut
    if not 0 <= timme < e.shape[1]:
        raise LuftFel(f"timme {timme} ligger utanför serien (0–{e.shape[1] - 1}).")
    ut = e[:, timme].copy()
    return ut


@dataclass(frozen=True)
class TimvisHalt:
    """Statistik över modellerade timmar (svag vind och ogiltiga timmar räknas inte)."""

    arsmedel_g_m3: FloatArray
    max_g_m3: FloatArray
    antal_timmar_over: FloatArray | None
    grans_g_m3: float | None
    antal_modellerade_timmar: int
    antal_svag_vind: int
    antal_ogiltiga: int


def timvis_halt(
    emission_per_vag_h: npt.ArrayLike,
    halt_per_vag_och_riktning: npt.ArrayLike,
    fordelning: Fordelning,
    grans_g_m3: float | None = None,
    block_bytes: int = 64 * 1024 * 1024,
) -> TimvisHalt:
    """Timvisa halter ur timvisa utsläpp per väg och halter per väg och riktning.

    Args:
        emission_per_vag_h: (n_vag, n_timmar) i g/(m·s), t.ex. `emissionsmatris`.
        halt_per_vag_och_riktning: (n_vag, n_riktningar, ...) i g/m³ per g/(m·s) vid
            ``u_ref``, riktningar i samma ordning som `fordelning.riktningar_grad`.
        fordelning: från `luft.vindbibliotek.fordela`, med n_timmar timmar.
        grans_g_m3: om angiven räknas antal timmar med halt strikt över gränsen.

    ``halt_h = f_h · Σ_c w_{h,c} · Σ_v e_{v,h} · C_{v, d(h,c)}``, ``f_h = u_ref/U_h``.
    Beräknas blockvis över timmar: för ett block om B timmar byggs koefficienterna
    ``K[h, v, d]`` och timfälten ``K @ C`` (B, N). Minne O(B·N + V·S·N), tid O(H·V·S·N).
    Inga timfält sparas. NaN i halterna (t.ex. fasta celler) ger NaN i resultatet.
    """
    e = np.asarray(emission_per_vag_h, dtype=np.float64)
    c = np.asarray(halt_per_vag_och_riktning, dtype=np.float64)
    if e.ndim != 2:
        raise LuftFel("emission_per_vag_h ska ha formen (n_vag, n_timmar).")
    n_vag, n_tim = e.shape
    n_rikt = len(fordelning.riktningar_grad)
    if c.ndim < 2 or c.shape[0] != n_vag or c.shape[1] != n_rikt:
        raise LuftFel(
            f"halt_per_vag_och_riktning ska ha formen ({n_vag}, {n_rikt}, ...), fick {c.shape}."
        )
    if n_tim != fordelning.antal_timmar:
        raise LuftFel(
            f"{n_tim} emissionstimmar men fördelningen har {fordelning.antal_timmar} timmar."
        )
    if not np.all(np.isfinite(e)):
        raise LuftFel("emission_per_vag_h innehåller NaN/Inf.")
    if np.any(np.isinf(c)):
        raise LuftFel("halt_per_vag_och_riktning innehåller Inf.")
    modellerade = np.flatnonzero(fordelning.modellerad)
    if modellerade.size == 0:
        raise LuftFel("Inga modellerade timmar (alla svag vind eller ogiltiga).")

    form = c.shape[2:]
    n_cell = int(np.prod(form)) if form else 1
    c_platt = c.reshape(n_vag * n_rikt, n_cell)
    summa = np.zeros(n_cell)
    maxv = np.full(n_cell, -np.inf)
    antal = np.zeros(n_cell) if grans_g_m3 is not None else None
    b = max(1, int(block_bytes) // (8 * (n_cell + n_vag * n_rikt)))
    for start in range(0, modellerade.size, b):
        hb = modellerade[start : start + b]
        nb = hb.size
        koef = np.zeros((nb, n_vag, n_rikt))
        rad = np.arange(nb)
        eb = e[:, hb].T  # (B, V)
        for k in range(2):
            vikt = fordelning.skalfaktor[hb] * fordelning.vikt[hb, k]
            koef[rad, :, fordelning.sektor_index[hb, k]] += vikt[:, None] * eb
        falt = koef.reshape(nb, n_vag * n_rikt) @ c_platt  # (B, N)
        summa += falt.sum(axis=0)
        maxv = np.maximum(maxv, falt.max(axis=0))
        if antal is not None and grans_g_m3 is not None:
            antal += np.sum(falt > grans_g_m3, axis=0)
    medel = summa / modellerade.size
    nan_cell = np.isnan(summa)
    maxv[nan_cell] = np.nan
    if antal is not None:
        antal[nan_cell] = np.nan

    def forma(a: FloatArray) -> FloatArray:
        ut: FloatArray = a.reshape(form) if form else a
        return ut

    return TimvisHalt(
        arsmedel_g_m3=forma(medel),
        max_g_m3=forma(maxv),
        antal_timmar_over=forma(antal) if antal is not None else None,
        grans_g_m3=grans_g_m3,
        antal_modellerade_timmar=int(modellerade.size),
        antal_svag_vind=fordelning.antal_svag_vind,
        antal_ogiltiga=fordelning.antal_ogiltiga,
    )
