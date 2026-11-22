# SPDX-License-Identifier: MIT
"""Vindkomfort och vindfara för fotgängare, t.ex. för att bedöma höga hus.

Metod (samma som i vindtunnel- och CFD-studier enligt NEN 8100 och Lawson):

1. För varje vindriktning d ger LES-körningen *förstärkningen* på gånghöjd,
   ``A_d(x) = |u_h(x)| / u_ref`` (horisontell medelhastighet relativt
   inflödets referensvind). Vid neutral skiktning är A_d oberoende av
   vindstyrkan (Reynoldsoberoende), så en körning per riktning räcker.
2. Lokal timmedelvind en timme h med referensvind U_h från riktning d(h):
   ``U_lok = A_d(h) · U_h``. Med byighet (Lawson, GEM) används en effektiv
   förstärkning ``Ã_d = max(A_d, (A_d + g·S_d) / 1,85)``, där ``S_d = σ_d/u_ref``
   är normerad standardavvikelse och g en toppfaktor (indata).
3. Överskridandesannolikheten för tröskeln V är andelen giltiga timmar med
   ``U_lok > V``. Med närmaste riktningssektor räknas den exakt med sortering
   och binärsökning: ``P = Σ_d #{h ∈ d : U_h > V / Ã_d} / N``, O(N_celler · S · log H).

Kriterier (värden med källa, se `KRITERIER`):

* **NEN 8100:2006** (Nederländerna): obehag när timmedelvinden på 1,75 m
  överstiger 5 m/s; klass A–E efter andel av årets timmar (<2,5, 2,5–5, 5–10,
  10–20, ≥20 %). Fara när 15 m/s överskrids: 0,05–0,3 % begränsad risk,
  ≥0,3 % farligt. Källor: NEN 8100-tabeller återgivna i nederländska
  planhandlingar (ruimtelijkeplannen.nl, t.ex. bp1518 Apeldoorn 2023) och
  SimScale (2026), "NEN 8100: the Dutch wind comfort and wind danger criteria".
* **Lawson LDDC** (Storbritannien): högsta av medelvind och GEM (byvind/1,85);
  komfort om ≤5 % av tiden över 4 (sitta), 6 (stå), 8 (promenera),
  10 m/s (gå målmedvetet), annars obekvämt; obehag/fara om >0,022 % över 15
  respektive 20 m/s. Källa: CPP Wind, "Pedestrian wind comfort criteria" (Lawson).
  Obs: olika Lawson-versioner har olika trösklar; varianten anges alltid i utdata.

Begränsningar: kriterierna bygger på referensvind i öppen terräng; väderdata
måste räknas om till inflödets referenshöjd och råhet (`omrakna_vindhastighet`).
Ingen svensk norm finns; valet av kriterium är ett planeringsbeslut.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from luft.geometri import RasterRuta, nivaskiva_till_raster
from luft.typer import FloatArray, LuftFel, Vindfalt
from luft.vindbibliotek import Vaderserie, Vindbibliotek, fordela

GEM_DIVISOR = 1.85  # Lawson: GEM = byvind (3 s) / 1,85 (CPP Wind)


@dataclass(frozen=True)
class Kriterium:
    """Ett komfortkriterium: trösklar (m/s) och tillåten överskridandeandel."""

    namn: str
    kalla: str
    utvardering_hojd_m: float
    hastighetsmatt: Literal["medel", "max_medel_gem"]
    komfort_trosklar_m_s: tuple[float, ...]
    komfort_max_andel: tuple[float, ...]  # andel (0–1) per tröskel
    komfort_klasser: tuple[str, ...]  # len = antal trösklar + 1 (sista = sämst)
    fara_trosklar_m_s: tuple[float, ...]
    fara_max_andel: tuple[float, ...]
    fara_klasser: tuple[str, ...]  # len = antal faragränser + 1 (första = ingen risk)
    fara_inklusiv: tuple[bool, ...]  # True: andel ≥ gräns räknas, False: andel > gräns
    klassning: Literal["andel_vid_en_troskel", "minsta_uppfyllda_troskel"]

    def __post_init__(self) -> None:
        if self.klassning == "andel_vid_en_troskel":
            if len(self.komfort_trosklar_m_s) != 1:
                raise LuftFel("andel_vid_en_troskel kräver exakt en komforttröskel.")
            if len(self.komfort_klasser) != len(self.komfort_max_andel) + 1:
                raise LuftFel("Antal komfortklasser ska vara antal andelsgränser + 1.")
        else:
            if len(self.komfort_trosklar_m_s) != len(self.komfort_max_andel):
                raise LuftFel("En andelsgräns per komforttröskel krävs.")
            if len(self.komfort_klasser) != len(self.komfort_trosklar_m_s) + 1:
                raise LuftFel("Antal komfortklasser ska vara antal trösklar + 1.")
        n_fara = len(self.fara_trosklar_m_s)
        if len(self.fara_max_andel) != n_fara or len(self.fara_inklusiv) != n_fara:
            raise LuftFel("fara_max_andel och fara_inklusiv ska ha en post per faragräns.")
        if len(self.fara_klasser) != n_fara + 1:
            raise LuftFel("Antal faraklasser ska vara antal faragränser + 1.")


KRITERIER: dict[str, Kriterium] = {
    "nen8100": Kriterium(
        namn="NEN 8100:2006",
        kalla=(
            "NEN 8100:2006 tabell för windhinder/windgevaar, återgiven i bl.a. "
            "ruimtelijkeplannen.nl NL.IMRO.0200.bp1518 (2023) och SimScale 2026"
        ),
        utvardering_hojd_m=1.75,
        hastighetsmatt="medel",
        komfort_trosklar_m_s=(5.0,),
        komfort_max_andel=(0.025, 0.05, 0.10, 0.20),
        komfort_klasser=("A", "B", "C", "D", "E"),
        fara_trosklar_m_s=(15.0, 15.0),
        fara_max_andel=(0.0005, 0.003),
        fara_klasser=("ingen risk", "begränsad risk", "farligt"),
        fara_inklusiv=(False, True),  # 0,05 < p < 0,30 begränsad; p ≥ 0,30 farligt
        klassning="andel_vid_en_troskel",
    ),
    "lawson-lddc": Kriterium(
        namn="Lawson LDDC (variant enligt CPP Wind)",
        kalla="CPP Wind, Pedestrian wind comfort criteria (Lawson LDDC)",
        utvardering_hojd_m=1.5,
        hastighetsmatt="max_medel_gem",
        komfort_trosklar_m_s=(4.0, 6.0, 8.0, 10.0),
        komfort_max_andel=(0.05, 0.05, 0.05, 0.05),
        komfort_klasser=("sitta", "stå", "promenera", "gå målmedvetet", "obekvämt"),
        fara_trosklar_m_s=(15.0, 20.0),
        fara_max_andel=(0.00022, 0.00022),
        fara_klasser=("under gränserna", "över 15 m/s-gränsen", "över 20 m/s-gränsen"),
        fara_inklusiv=(False, False),
        klassning="minsta_uppfyllda_troskel",
    ),
}


def omrakna_vindhastighet(
    u_m_s: FloatArray | float,
    z_fran_m: float,
    z0_fran_m: float,
    z_till_m: float,
    z0_till_m: float,
    blandningshojd_m: float,
) -> FloatArray:
    """Räknar om vindhastighet mellan höjd/råhet via en blandningshöjd (log-lag).

    Används för att föra väderstationens vind (t.ex. 10 m över öppen terräng)
    till inflödets referenshöjd och råhet: först upp till blandningshöjden där
    vinden antas opåverkad av lokal råhet, sedan ned med målets råhetslängd.
    Blandningshöjden är indata (ofta antas några tiotal meter; ange källa).
    """
    for namn, v in (("z_fran_m", z_fran_m), ("z0_fran_m", z0_fran_m), ("z_till_m", z_till_m),
                    ("z0_till_m", z0_till_m), ("blandningshojd_m", blandningshojd_m)):
        if not (v > 0 and math.isfinite(v)):
            raise LuftFel(f"{namn} måste vara positiv.")
    if blandningshojd_m <= max(z_fran_m, z_till_m):
        raise LuftFel("blandningshojd_m måste ligga över båda höjderna.")
    u = np.asarray(u_m_s, dtype=np.float64)
    u_bland = (
        u
        * math.log((blandningshojd_m + z0_fran_m) / z0_fran_m)
        / math.log((z_fran_m + z0_fran_m) / z0_fran_m)
    )
    ut: FloatArray = (
        u_bland
        * math.log((z_till_m + z0_till_m) / z0_till_m)
        / math.log((blandningshojd_m + z0_till_m) / z0_till_m)
    )
    return ut


def forstarkning_3d(vind: Vindfalt, toppfaktor: float | None) -> FloatArray:
    """Effektiv förstärkning Ã = U_eff/u_ref per cell (nx, ny, nz), NaN i fasta celler."""
    uh = np.sqrt(vind.u_m_s[0] ** 2 + vind.u_m_s[1] ** 2)
    a = uh / vind.inflode.u_ref_m_s
    if toppfaktor is not None:
        if vind.u_std_m_s is None:
            raise LuftFel(
                "Kriteriet kräver byighet men vindfältet saknar u_std_m_s – kör om LES."
            )
        s = vind.u_std_m_s / vind.inflode.u_ref_m_s
        a = np.maximum(a, (a + toppfaktor * s) / GEM_DIVISOR)
    ut: FloatArray = np.where(vind.grid.fast, np.nan, a)
    return ut


def overskridande_andel(
    forstarkning_per_riktning: Sequence[FloatArray],
    riktningar_grad: Sequence[float],
    vader: Vaderserie,
    troskel_m_s: float,
) -> FloatArray:
    """Andel av giltiga timmar då ``Ã_d(h) · U_h > troskel`` (exakt, närmaste sektor).

    Timmar utan vind (U = 0) räknas som giltiga timmar utan överskridande.
    NaN i förstärkningen (fasta celler, utanför) ger NaN.
    """
    if troskel_m_s <= 0:
        raise LuftFel("troskel_m_s måste vara positiv.")
    # u_min nära noll: alla giltiga timmar med vind fördelas på närmaste sektor
    f = fordela(vader, riktningar_grad, 1.0, u_min_m_s=1e-9, interpolera=False)
    giltiga = f.antal_timmar - f.antal_ogiltiga
    if giltiga == 0:
        raise LuftFel("Väderserien saknar giltiga timmar.")
    u = vader.vindhastighet_m_s
    form = np.asarray(forstarkning_per_riktning[0]).shape
    antal = np.zeros(form, dtype=np.float64)
    nan = np.zeros(form, dtype=np.bool_)
    for s in range(len(f.riktningar_grad)):
        a = np.asarray(forstarkning_per_riktning[s], dtype=np.float64)
        if a.shape != form:
            raise LuftFel("Alla förstärkningsfält måste ha samma form.")
        nan |= np.isnan(a)
        u_s = np.sort(u[f.modellerad & (f.sektor_index[:, 0] == s)])
        if u_s.size == 0:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            grans = np.where(a > 0, troskel_m_s / a, np.inf)
        grans = np.nan_to_num(grans, nan=np.inf)
        antal += u_s.size - np.searchsorted(u_s, grans, side="right")
    ut: FloatArray = np.where(nan, np.nan, antal / giltiga)
    return ut


@dataclass
class Komfortresultat:
    """Komfort- och faraklassning på ett nordorienterat raster."""

    kriterium: Kriterium
    ruta: RasterRuta
    forstarkning_per_riktning: list[FloatArray]
    andel_komfort: dict[float, FloatArray]  # tröskel → andel
    andel_fara: dict[float, FloatArray]
    komfortklass: FloatArray  # index i kriterium.komfort_klasser, NaN i byggnader
    faraklass: FloatArray  # index i kriterium.fara_klasser
    varningar: list[str] = field(default_factory=list)


def _klassa_komfort(k: Kriterium, andel: dict[float, FloatArray]) -> FloatArray:
    if k.klassning == "andel_vid_en_troskel":
        p = andel[k.komfort_trosklar_m_s[0]]
        klass = np.searchsorted(np.asarray(k.komfort_max_andel), p, side="right").astype(float)
        # NEN: gränsen "< 2,5 %" → p exakt 2,5 % hör till nästa klass (side="right")
        return np.where(np.isnan(p), np.nan, klass)
    forsta = next(iter(andel.values()))
    klass = np.full(forsta.shape, float(len(k.komfort_trosklar_m_s)))
    for i in reversed(range(len(k.komfort_trosklar_m_s))):
        p = andel[k.komfort_trosklar_m_s[i]]
        klass = np.where(p <= k.komfort_max_andel[i], float(i), klass)
    return np.where(np.isnan(forsta), np.nan, klass)


def _klassa_fara(k: Kriterium, andel: dict[float, FloatArray]) -> FloatArray:
    forsta = andel[k.fara_trosklar_m_s[0]]
    klass = np.zeros(forsta.shape)
    for i, (v, gr, inkl) in enumerate(
        zip(k.fara_trosklar_m_s, k.fara_max_andel, k.fara_inklusiv, strict=True)
    ):
        over = andel[v] >= gr if inkl else andel[v] > gr
        klass = np.where(over, float(i + 1), klass)
    return np.where(np.isnan(forsta), np.nan, klass)


def berakna_komfort(
    bibliotek: Vindbibliotek,
    vader: Vaderserie,
    utbredning: tuple[float, float, float, float],
    raster_dx_m: float,
    kriterium: Kriterium | str = "nen8100",
    toppfaktor: float | None = None,
    hojd_m: float | None = None,
) -> Komfortresultat:
    """Vindkomfort och vindfara på gånghöjd för hela biblioteket.

    `vader` ska ge referensvinden vid inflödets `z_ref_m` och `z0_m` (se
    `omrakna_vindhastighet`). `toppfaktor` krävs för kriterier med GEM.
    """
    k = KRITERIER[kriterium] if isinstance(kriterium, str) else kriterium
    varningar: list[str] = []
    if k.hastighetsmatt == "max_medel_gem" and toppfaktor is None:
        varningar.append(
            f"{k.namn}: ingen toppfaktor angiven – bedömt på medelvind utan byighet (GEM)."
        )
    g = toppfaktor if k.hastighetsmatt == "max_medel_gem" else None
    z = k.utvardering_hojd_m if hojd_m is None else hojd_m
    forst: list[FloatArray] = []
    ruta: RasterRuta | None = None
    for r in bibliotek.riktningar_grad:
        vind = bibliotek.for_riktning(r)
        if vind.grid.dx_m > 2 * z:
            varningar.append(
                f"Cellstorlek {vind.grid.dx_m:g} m är grov för gånghöjd {z:g} m "
                f"(riktning {r:g}°); resultatet är översiktligt."
            )
        a3 = forstarkning_3d(vind, g)
        skiva, ruta = nivaskiva_till_raster(vind.grid, np.nan_to_num(a3, nan=0.0), z,
                                            raster_dx_m, utbredning, metod="bilinjar")
        forst.append(skiva)
    assert ruta is not None
    rikt = bibliotek.riktningar_grad
    andel_k = {v: overskridande_andel(forst, rikt, vader, v) for v in set(k.komfort_trosklar_m_s)}
    andel_f = {v: overskridande_andel(forst, rikt, vader, v) for v in set(k.fara_trosklar_m_s)}
    return Komfortresultat(
        kriterium=k,
        ruta=ruta,
        forstarkning_per_riktning=forst,
        andel_komfort=andel_k,
        andel_fara=andel_f,
        komfortklass=_klassa_komfort(k, andel_k),
        faraklass=_klassa_fara(k, andel_f),
        varningar=sorted(set(varningar)),
    )


def jamfor(fore: Komfortresultat, efter: Komfortresultat) -> dict[str, FloatArray | float]:
    """Före/efter: ändrad klass och ändrad överskridandeandel (efter − före)."""
    if fore.ruta != efter.ruta or fore.kriterium != efter.kriterium:
        raise LuftFel("Före och efter måste ha samma raster och kriterium.")
    v = fore.kriterium.komfort_trosklar_m_s[0]
    d_andel = efter.andel_komfort[v] - fore.andel_komfort[v]
    d_klass = efter.komfortklass - fore.komfortklass
    d_fara = efter.faraklass - fore.faraklass
    bada = np.isfinite(d_klass)
    return {
        "andel_skillnad": d_andel,
        "klass_skillnad": d_klass,
        "fara_skillnad": d_fara,
        "andel_celler_forsamrade": float(np.mean(d_klass[bada] > 0)) if bada.any() else math.nan,
        "andel_celler_forbattrade": float(np.mean(d_klass[bada] < 0)) if bada.any() else math.nan,
        "nya_farliga_celler": float(np.sum((d_fara > 0) & np.isfinite(d_fara))),
    }
