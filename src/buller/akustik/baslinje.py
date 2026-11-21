"""Baslinjekärna: enkel, oberoende dokumenterad punkt-till-punkt-modell (INTE Nord2000).

Kärnan finns för att övriga delar av bullermodulen ska ha något fysikaliskt rimligt att
räkna mot innan referenskärnan (B4) finns. Den har `nord2000_konform = False` och får
därför aldrig användas i rapportläget (KB3).

Modell, per tersband (`L_p − L_W` i dB):

1. **Geometrisk spridning** för punktkälla i fritt fält: `−10·lg(4π·R₁²)`, med R₁ = rak
   sträcka källa–mottagare (ISO 9613-2:1996 ekv. (7) ger samma sak som
   `20·lg(d/1 m) + 11 dB`).
2. **Luftabsorption** `α(f_m)·sträcka` med α enligt ISO 9613-1 vid bandets exakta
   mittfrekvens (`luftabsorption.py`).
3. **Markeffekt** när siktlinjen är fri: koherent tvåstrålesumma över ett ekvivalent plan
   med sfärisk reflektionskoefficient (`reflektion.py`, Chien & Soroka 1975) och
   Delany–Bazley-impedans (`impedans.py`), energimedelvärde över `antal_delfrekvenser`
   frekvenser inom bandet.
4. **Skärmterm** när profilen bryter siktlinjen: Kurze–Andersons formel för den
   dominerande kanten, och då *ingen* markeffekt.

Egna val (inte hämtade ur någon norm – dokumenterade i `docs/buller/logg/B4a.md`):

- *Ekvivalent plan:* minsta kvadrat-linjen genom hela profilen (kontinuerlig anpassning
  till den styckvis linjära markytan, så att tätt liggande brytpunkter inte väger tyngre).
  Källans och mottagarens höjder mäts vinkelrätt mot planet. Hamnar en av dem under planet
  sätts höjden till 0 och en varning ges.
- *Impedans:* flödesresistiviteten i det profilsegment där spegelpunkten på planet hamnar.
  Ingen viktning över en Fresnelzon (det är Nord2000:s metod och finns inte här).
- *Tak på marktillskottet:* tvåstrålefaktorn begränsas till 4 (+6,02 dB) per delfrekvens.
  Närmeformen för Q kan ge |Q| > 1 (ytvåg) över mjuk mark vid låga frekvenser; taket gör
  att `ovre_grans_db` är en bevisbar gräns.
- *Homogen atmosfär:* vind, temperaturgradient, turbulens och fluktuationer ignoreras,
  liksom markens råhet. `varningar()` talar om vad som ignorerats för ett visst anrop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.akustik.band import delfrekvenser_hz, exakta_mittfrekvenser_hz
from buller.akustik.impedans import delany_bazley_admittans
from buller.akustik.luftabsorption import ljudhastighet_m_s, luftabsorption_db_per_m
from buller.akustik.reflektion import sfarisk_reflektion
from buller.typer import ANTAL_BAND, BullerFel, Flyttal, Terrangprofil, Vader

BASLINJE_VERSION = "0.1.0"

# Största tillåtna tvåstrålefaktor |p|²/|p_fritt|² (två lika starka strålar i fas).
MAX_MARKFAKTOR = 4.0
# Golv för tvåstrålefaktorn, enbart för att undvika lg(0) vid exakt utsläckning.
MIN_MARKFAKTOR = 1.0e-12
# Övre gräns för Kurze–Andersons skärmdämpning i dB (enkel skärm).
MAX_SKARMDAMPNING_DB = 24.0

# Sådant som kärnan aldrig tar hänsyn till, oavsett indata.
ALLTID_IGNORERAT: tuple[str, ...] = (
    "vind och vindgradient (homogen atmosfär)",
    "temperaturgradient (homogen atmosfär)",
    "turbulens och fluktuationer (cv2, ct2, std_vind_m_s, std_dt_dz_k_m)",
    "markens råhet (rahet_m)",
    "varierande markimpedans längs profilen (endast segmentet vid spegelpunkten används)",
    "terrängform utöver ekvivalent plan; flera skärmkanter (endast dominerande kant)",
    "markreflexer i kombination med skärm",
)


@dataclass(frozen=True)
class BaslinjeResultat:
    """Delresultat för en profil. Alla fält i dB med form (27,) om inget annat anges.

    `overforing_db = spridning_db + mark_db − skarm_db − luft_db`.
    """

    overforing_db: Flyttal
    spridning_db: Flyttal  # −10·lg(4π·R₁²), samma värde i alla band
    luft_db: Flyttal  # dämpning (positiv)
    mark_db: Flyttal  # tillskott relativt fritt fält (0 när skärm finns)
    skarm_db: Flyttal  # dämpning (positiv, 0 när siktlinjen är fri)
    skarmad: bool
    gangvagsskillnad_m: float  # δ för dominerande kant, 0 när siktlinjen är fri
    varningar: tuple[str, ...]


def kurze_anderson_db(fresneltal: Flyttal) -> Flyttal:
    """Skärmdämpning i dB för Fresneltal N ≥ 0, samma form som indata.

    Kurze & Anderson, "Sound attenuation by barriers", Applied Acoustics 4 (1971) 35–53:
    `ΔL = 5 + 20·lg(√(2πN)/tanh √(2πN))`, begränsad till 24 dB. Formeln är en anpassning
    till Maekawas mätkurva (Z. Maekawa, Applied Acoustics 1 (1968) 157–173) för punktkälla
    och tunn halvoändlig skärm. `N = 2δ/λ` där δ är gångvägsskillnaden över kanten.
    N = 0 (kanten precis i siktlinjen) ger 5 dB.
    """
    n = np.asarray(fresneltal, dtype=np.float64)
    if not bool(np.all(n >= 0.0)):
        raise ValueError("Fresneltalet ska vara icke-negativt (skuggzon).")
    x = np.sqrt(2.0 * math.pi * n)
    liten = x < 1.0e-8
    kvot = np.where(liten, 1.0, np.where(liten, 1.0, x) / np.tanh(np.where(liten, 1.0, x)))
    resultat: Flyttal = np.minimum(5.0 + 20.0 * np.log10(kvot), MAX_SKARMDAMPNING_DB)
    return resultat


def _minsta_kvadrat_linje(x_m: Flyttal, z_m: Flyttal) -> tuple[float, float]:
    """Linjen z = a + b·x som minimerar ∫(z(x) − a − b·x)² dx över den styckvis linjära profilen.

    Momenten ∫z dx och ∫x·z dx räknas exakt per segment.
    """
    dx = np.diff(x_m)
    z0 = z_m[:-1]
    z1 = z_m[1:]
    x0 = x_m[:-1]
    langd = float(x_m[-1] - x_m[0])
    m_z = float(np.sum(dx * (z0 + z1) / 2.0))
    # ∫ x·z dx över ett segment med linjärt z: dx·[x0·(2·z0 + z1) + x1·(z0 + 2·z1)]/6
    m_xz = float(np.sum(dx * (x0 * (2.0 * z0 + z1) + x_m[1:] * (z0 + 2.0 * z1)) / 6.0))
    x_medel = float(x_m[0]) + langd / 2.0
    z_medel = m_z / langd
    varians_x = langd**2 / 12.0
    kovarians = m_xz / langd - x_medel * z_medel
    b = kovarians / varians_x
    a = z_medel - b * x_medel
    return a, b


class BaslinjeKarna:
    """Enkel punkt-till-punkt-kärna som uppfyller `buller.typer.P2PKarna`. Inte Nord2000.

    Kärnan är tillståndslös och deterministisk: samma profil och väder ger bit för bit
    samma resultat.
    """

    namn: str = "baslinje"
    version: str = BASLINJE_VERSION
    nord2000_konform: bool = False
    ignorerat: tuple[str, ...] = ALLTID_IGNORERAT

    def __init__(self, antal_delfrekvenser: int = 7) -> None:
        """`antal_delfrekvenser`: antal frekvenser per tersband i markeffektens medelvärde."""
        if antal_delfrekvenser < 1:
            raise BullerFel("antal_delfrekvenser ska vara minst 1.")
        self.antal_delfrekvenser = antal_delfrekvenser
        self._mitt_hz = exakta_mittfrekvenser_hz()
        self._del_hz = delfrekvenser_hz(antal_delfrekvenser)

    # ------------------------------------------------------------------ gränssnitt

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        """Överföring `L_p − L_W` per tersband, form (27,), decibel."""
        return self.berakna(profil, vader).overforing_db

    def ovre_grans_db(self, avstand_m: float) -> Flyttal:
        """Garanterad övre gräns för `overforing_db` på minst avståndet `avstand_m`, form (27,).

        `−10·lg(4π·d²) + 10·lg 4`: fri spridning plus största möjliga marktillskott
        (+6,02 dB, se `MAX_MARKFAKTOR`), utan luftabsorption. Gränsen gäller för varje
        profil där den raka sträckan källa–mottagare är minst `avstand_m`; eftersom den
        sträckan aldrig är kortare än profilens horisontella längd `x_m[-1]` gäller den
        också med `avstand_m = x_m[-1]`. Gränsen avtar monotont med avståndet. Skärm- och
        luftdämpning är aldrig negativa och kan därför bara sänka nivån.
        """
        if not math.isfinite(avstand_m) or avstand_m <= 0.0:
            raise BullerFel("avstand_m ska vara ändligt och större än 0.")
        grans = -10.0 * math.log10(4.0 * math.pi * avstand_m**2) + 10.0 * math.log10(MAX_MARKFAKTOR)
        return np.full(ANTAL_BAND, grans, dtype=np.float64)

    def varningar(self, profil: Terrangprofil, vader: Vader) -> tuple[str, ...]:
        """Vad som ignoreras eller förenklas för just detta anrop (tom tupel om inget)."""
        return self.berakna(profil, vader).varningar

    # ------------------------------------------------------------------ beräkning

    def berakna(self, profil: Terrangprofil, vader: Vader) -> BaslinjeResultat:
        """Räknar överföringen och returnerar även delresultaten."""
        varningar = _vadervarningar(vader)
        if bool(np.any(profil.rahet_m != 0.0)):
            varningar.append("rahet_m ≠ 0 ignoreras (ingen råhetsmodell)")
        sigma = profil.flodesresistivitet_kNsm4
        if bool(np.any(np.isnan(sigma))) or not bool(np.all(sigma > 0.0)):
            raise BullerFel("Flödesresistiviteten ska vara större än 0 i alla segment.")
        try:
            c_m_s = ljudhastighet_m_s(vader.temperatur_c)
            alfa_db_m = luftabsorption_db_per_m(
                self._mitt_hz,
                vader.temperatur_c,
                vader.relativ_fuktighet_procent,
                vader.lufttryck_kpa,
            )
        except ValueError as fel:
            raise BullerFel(f"Ogiltigt väder: {fel}") from fel

        x = profil.x_m
        z = profil.z_m
        x_s, z_s = 0.0, float(z[0]) + profil.kalla_hojd_m
        x_r, z_r = float(x[-1]), float(z[-1]) + profil.mottagare_hojd_m
        r1_m = math.hypot(x_r - x_s, z_r - z_s)
        spridning_db = np.full(ANTAL_BAND, -10.0 * math.log10(4.0 * math.pi * r1_m**2))

        delta_m = _gangvagsskillnad_dominerande_kant(x, z, z_s, z_r, r1_m)
        if delta_m is not None:
            fresneltal = 2.0 * delta_m * self._mitt_hz / c_m_s
            skarm_db = kurze_anderson_db(fresneltal)
            mark_db = np.zeros(ANTAL_BAND)
            luft_db = alfa_db_m * (r1_m + delta_m)
            skarmad = True
        else:
            delta_m = 0.0
            skarm_db = np.zeros(ANTAL_BAND)
            mark_db = self._markeffekt_db(profil, z_s, z_r, c_m_s, varningar)
            luft_db = alfa_db_m * r1_m
            skarmad = False

        overforing_db = spridning_db + mark_db - skarm_db - luft_db
        return BaslinjeResultat(
            overforing_db=overforing_db,
            spridning_db=spridning_db,
            luft_db=luft_db,
            mark_db=mark_db,
            skarm_db=skarm_db,
            skarmad=skarmad,
            gangvagsskillnad_m=delta_m,
            varningar=tuple(varningar),
        )

    def _markeffekt_db(
        self, profil: Terrangprofil, z_s: float, z_r: float, c_m_s: float, varningar: list[str]
    ) -> Flyttal:
        """Marktillskott relativt fritt fält per band (dB), tvåstrålemodell över ekvivalent plan."""
        x = profil.x_m
        a, b = _minsta_kvadrat_linje(x, profil.z_m)
        cos_fi = 1.0 / math.sqrt(1.0 + b * b)
        x_r = float(x[-1])
        # Höjd vinkelrätt mot planet och läge längs planet.
        h_s = (z_s - a) * cos_fi
        h_r = (z_r - (a + b * x_r)) * cos_fi
        t_s = b * (z_s - a) * cos_fi
        t_r = (x_r + b * (z_r - a)) * cos_fi
        if h_s < 0.0 or h_r < 0.0:
            varningar.append(
                "källa eller mottagare ligger under det ekvivalenta planet; höjden satt till 0"
            )
            h_s = max(h_s, 0.0)
            h_r = max(h_r, 0.0)
        d_m = t_r - t_s
        r1 = math.hypot(d_m, h_s - h_r)
        r2 = math.hypot(d_m, h_s + h_r)
        if r2 <= 0.0:
            raise BullerFel("Källa och mottagare sammanfaller på det ekvivalenta planet.")
        cos_theta = (h_s + h_r) / r2
        andel = h_s / (h_s + h_r) if (h_s + h_r) > 0.0 else 0.5
        x_spegel = (t_s + andel * d_m) * cos_fi
        segment = int(np.clip(np.searchsorted(x, x_spegel, side="right") - 1, 0, x.shape[0] - 2))
        sigma = float(profil.flodesresistivitet_kNsm4[segment])

        f = self._del_hz
        k = 2.0 * math.pi * f / c_m_s
        beta = delany_bazley_admittans(f, sigma)
        q = sfarisk_reflektion(k, r2, cos_theta, beta)
        falt = 1.0 + q * (r1 / r2) * np.exp(1j * k * (r2 - r1))
        faktor = np.minimum(np.square(np.abs(falt)), MAX_MARKFAKTOR)
        medel = np.maximum(np.mean(faktor, axis=1), MIN_MARKFAKTOR)
        resultat: Flyttal = 10.0 * np.log10(medel)
        return resultat


def _vadervarningar(vader: Vader) -> list[str]:
    """Väderparametrar med värde skilt från noll som kärnan inte använder."""
    varningar: list[str] = []
    if vader.vind_m_s != 0.0:
        varningar.append("vind_m_s ≠ 0 ignoreras (homogen atmosfär)")
    if vader.dt_dz_k_m != 0.0:
        varningar.append("dt_dz_k_m ≠ 0 ignoreras (homogen atmosfär)")
    if vader.std_vind_m_s != 0.0 or vader.std_dt_dz_k_m != 0.0:
        varningar.append("std_vind_m_s/std_dt_dz_k_m ≠ 0 ignoreras (inga fluktuationer)")
    if vader.cv2 != 0.0 or vader.ct2 != 0.0:
        varningar.append("cv2/ct2 ≠ 0 ignoreras (ingen turbulens)")
    return varningar


def _gangvagsskillnad_dominerande_kant(
    x: Flyttal, z: Flyttal, z_s: float, z_r: float, r1_m: float
) -> float | None:
    """Gångvägsskillnaden δ (m) för den kant som ger störst δ, eller None om siktlinjen är fri.

    En inre profilpunkt bryter siktlinjen om den ligger strikt över linjen källa–mottagare.
    Profilens ändpunkter (marken under källa och mottagare) räknas inte som kanter.
    """
    if x.shape[0] < 3:
        return None
    xi = x[1:-1]
    zi = z[1:-1]
    x_r = float(x[-1])
    sikt = z_s + (z_r - z_s) * xi / x_r
    over = zi > sikt
    if not bool(np.any(over)):
        return None
    xo = xi[over]
    zo = zi[over]
    delta = np.hypot(xo, zo - z_s) + np.hypot(x_r - xo, z_r - zo) - r1_m
    return max(float(np.max(delta)), 0.0)
