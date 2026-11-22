"""Sammansättningen av delmodellerna till ΔL_t före delmodell 8.

Källa: DELTA AV 1106/07 (rev. 2014) avsnitt 5.21 (terrängtolkningen, ekv. 300–319) och 5.22
(ekv. 332, avläst från sidbild s. 144), med FORCE TC-101327 (2019) avsnitt 2.11–2.12:

    ΔL_t     = (r_scr1·ΔL_scr + (1 − r_scr1)·ΔL_noscr) ⊕ ΔL₈
    ΔL_scr   = r_scr2·(ΔL₆ ⊕ ΔL_7,2) + (1 − r_scr2)·ΔL_scr1
    ΔL_scr1  = r_scr12·(ΔL₅ ⊕ ΔL_7,2) + (1 − r_scr12)·(ΔL₄ ⊕ ΔL_7,1)
    ΔL_noscr = r_flat·ΔL_flat + (1 − r_flat)·ΔL₃
    ΔL_flat  = ΔL₁ för en marktyp, annars ΔL₂

Här räknas allt utom ⊕ ΔL₈, som kärnan lägger till ytterst (`karna.py`). Varje delmodell
räknas bara när dess vikt är större än 0 i något tersband; en delmodell som inte går att
räkna för profilen ger då `BullerFel` med delmodellens namn först i beskedet. I band där
vikten är 0 används inte delmodellens värde (det behöver inte ens vara ändligt).

Terrängtolkningen görs per profil, alltså per delkälla och källpunkt: primär och sekundär
skärm kan byta roll mellan delkällor i samma fall (loggen P-T).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from buller.nord2000 import delmodell4 as d4
from buller.nord2000 import delmodell5 as d5
from buller.nord2000 import delmodell6 as d6
from buller.nord2000 import delmodell7 as d7
from buller.nord2000.atmosfar import HomogenAtmosfar
from buller.nord2000.delmodell1 import Ct2Form, markeffekt_plan_mark_db
from buller.nord2000.delmodell2 import (
    Fresnelzonsval,
    Marksegment,
    Skuggzonsvikter,
    markeffekt_blandad_mark_db,
    marktyper,
)
from buller.nord2000.delmodell3 import (
    Delmodell3Resultat,
    Fresnelzonsregel,
    Overgangshojd,
    delmodell3,
    overgang_rflat,
)
from buller.nord2000.delmodell8 import energisumma_db
from buller.nord2000.kildiffraktion_dubbel import TvaKanter
from buller.nord2000.refraktion import Atmosfar
from buller.nord2000.refraktion_mark import Skuggzonslasning
from buller.nord2000.stralar_krokta import Uppatmetod
from buller.nord2000.terrang import (
    GeometriOverPlanMark,
    Terranggeometri,
    ekvivalent_plan_mark,
    geometri_over_plan_mark,
    terranggeometri,
)
from buller.nord2000.terrang_skarmar import (
    Skarmkanter,
    Terrangtolkning,
    tolka_terrang,
    valj_skarmkanter,
)
from buller.typer import BullerFel, Flyttal, Terrangprofil

# Största vertikala avvikelse från den ekvivalenta plana marken för att terrängen ska räknas
# som plan utan att delmodell 3 anropas. Värdet är ett numeriskt skydd mot avrundning, inte en
# del av metoden: för plan mark ger metodens egen övergång r_flat = 1 (ekv. 317–318), vilket
# testerna visar med `alltid_delmodell3=True` i homogen atmosfär. Genvägen behövs med starkt
# krökta strålar, där delmodell 3:s segmentklassning inte alltid går att räkna för plan mark.
PLAN_TOLERANS_M = 1.0e-6


@dataclass(frozen=True)
class Sammansattningsval:
    """De val i delmodellerna som sammansättningen för vidare (se respektive delmodell).

    Förvalen är delmodellernas egna förval. Inget av dem är ändrat i P-S2; utfallet för
    läsningarna sida vid sida står i `docs/buller/logg/P-S2.md`.
    """

    uppat: Uppatmetod = "exakt"
    ct2_form: Ct2Form = "kvadrat"
    fresnelzon: Fresnelzonsval = "klippt"  # delmodell 2
    skuggzonsvikter: Skuggzonsvikter = "ovre_band"
    skuggzon: Skuggzonslasning = field(default_factory=Skuggzonslasning)
    fresnelzon_delmodell3: Fresnelzonsregel = "klippt"  # P-3 tolkning 1
    overgangshojd: Overgangshojd = "prim"  # ekv. 140 för övergångssegment, P-3 tolkning 9
    viktregel: d4.Viktregel = "av1849"  # ekv. 187, 220, 269
    vinkelregel: d6.Vinkelregel = "avsnitt_5_7_3"  # delmodell 6, ekv. 227–228
    # Räkna delmodell 3 och r_flat även för plan mark (för prövning; se PLAN_TOLERANS_M).
    alltid_delmodell3: bool = False


@dataclass(frozen=True)
class Terrangeffekt:
    """ΔL_t utan ΔL₈ och alla led i ekv. (332), ett värde per tersband.

    Led som inte behövts (vikten 0 i alla band) är `None`. `delmodeller` listar de
    delmodeller som räknats, t.ex. ("2", "3", "4", "7").
    """

    frekvenser_hz: Flyttal
    delta_l_db: Flyttal
    delmodeller: tuple[str, ...]
    tolkning: Terrangtolkning
    r_flat: Flyttal | None
    plan_db: Flyttal | None  # ΔL_flat
    delmodell3_db: Flyttal | None  # ΔL₃
    utan_skarm_db: Flyttal | None  # ΔL_noscr
    delmodell4_db: Flyttal | None  # ΔL₄ ⊕ ΔL_7,1
    delmodell5_db: Flyttal | None  # ΔL₅ ⊕ ΔL_7,2
    delmodell6_db: Flyttal | None  # ΔL₆ ⊕ ΔL_7,2
    skarm_db: Flyttal | None  # ΔL_scr
    resultat3: Delmodell3Resultat | None = None
    resultat4: d4.Delmodell4Resultat | None = None
    resultat5: d5.Delmodell5Resultat | None = None
    resultat6: d6.Delmodell6Resultat | None = None

    @property
    def beteckning(self) -> str:
        """Delmodellerna som en sträng, t.ex. "2+3" eller "1+3+4+7"."""
        return "+".join(self.delmodeller)


def marksegment_over_plan_mark(
    geo: Terranggeometri, over_plan: GeometriOverPlanMark
) -> list[Marksegment]:
    """Terrängsegmenten projicerade på den ekvivalenta plana marken, AV 1106/07 ekv. (133)."""
    avstand = [
        over_plan.avstand_langs_mark_m(float(geo.x_m[i]), float(geo.z_m[i]))
        for i in range(geo.x_m.shape[0])
    ]
    return [
        Marksegment(
            d_start_m=avstand[i],
            d_slut_m=avstand[i + 1],
            flodesresistivitet_kNsm4=float(geo.flodesresistivitet_kNsm4[i]),
            rahet_m=float(geo.rahet_m[i]),
        )
        for i in range(geo.x_m.shape[0] - 1)
    ]


def markeffekt_plan_modell_db(
    frekvenser_hz: Flyttal,
    over_plan: GeometriOverPlanMark,
    segment: list[Marksegment],
    atmosfar: HomogenAtmosfar | Atmosfar,
    val: Sammansattningsval | None = None,
) -> tuple[Flyttal, str]:
    """ΔL_flat: delmodell 1 vid en marktyp, annars delmodell 2 (AV 1106/07 ekv. 332)."""
    val = Sammansattningsval() if val is None else val
    typer = marktyper(segment)
    if len(typer) == 1:
        sigma, rahet = typer[0]
        return (
            markeffekt_plan_mark_db(
                frekvenser_hz,
                over_plan.d_e_m,
                over_plan.h_se_m,
                over_plan.h_re_m,
                sigma,
                rahet,
                atmosfar,
                val.uppat,
                val.ct2_form,
                val.skuggzon,
            ),
            "1",
        )
    return (
        markeffekt_blandad_mark_db(
            frekvenser_hz,
            over_plan.d_e_m,
            over_plan.h_se_m,
            over_plan.h_re_m,
            segment,
            atmosfar,
            val.uppat,
            val.ct2_form,
            val.fresnelzon,
            val.skuggzonsvikter,
            val.skuggzon,
        ),
        "2",
    )


def _med_namn(namn: str, fel: BullerFel) -> BullerFel:
    """Samma fel med delmodellens namn först, så att beskedet säger var det tog stopp."""
    return BullerFel(f"{namn}: {fel}")


def _blanda(vikt: Flyttal, med_vikt_db: Flyttal | None, utan_vikt_db: Flyttal | None) -> Flyttal:
    """vikt·a + (1 − vikt)·b per band, där ett led med vikten 0 inte används alls.

    Formen i alla fyra raderna i AV 1106/07 ekv. (332). Ett led som inte räknats (`None`) får
    bara förekomma med vikten 0 i alla band; ett led med vikten 0 i ett band läses inte där
    (det kan vara −∞ dB eller sakna värde).
    """
    resultat = np.empty(vikt.shape[0], dtype=np.float64)
    for b in range(vikt.shape[0]):
        w = float(vikt[b])
        summa = 0.0
        if w > 0.0:
            if med_vikt_db is None:
                raise BullerFel("Sammansättningen: ett led med vikt större än 0 saknas.")
            summa += w * float(med_vikt_db[b])
        if w < 1.0:
            if utan_vikt_db is None:
                raise BullerFel("Sammansättningen: ett led med vikt större än 0 saknas.")
            summa += (1.0 - w) * float(utan_vikt_db[b])
        resultat[b] = summa
    return resultat


def gangvagsskillnader_refraktion(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    kanter: Skarmkanter,
    atmosfar: Atmosfar,
    val: Sammansattningsval,
) -> tuple[float | None, float | None, float | None]:
    """Δℓ med krökta strålar för T₁, T₂ och T₁₂ till krokarna i `tolka_terrang`.

    AV 1106/07 ekv. (301) för den primära kanten (ur delmodell 4:s strålar, ekv. 161–162) och
    ekv. (307) för den sekundära skärmens kant (ur delmodell 6:s strålar, ekv. 226–228). I
    homogen atmosfär är alla tre `None` (Δℓ = Δℓ₀).

    För den sekundära kanten T₁₂ säger avsnitt 5.21.3 att parametrarna räknas "in the same way
    as for the secondary screen as described in Section 5.21.2 but T2 is replaced by T12", alltså
    med Δℓ med refraktion som i ekv. (307). Strålarna är delmodell 5:s för kanterna T₁ och T₁₂
    (ekv. 193–195): ligger T₁₂ på källsidan om T₁ är R′_S = R_S, R′_R = R_M, θ′_S = θ_1S,
    θ′_R = θ_1R, annars R′_S = R_M, R′_R = R_R, θ′_S = θ_2S, θ′_R = θ_2R (P-S2, tolkning 2;
    b8bb734 använde Δℓ₀).

    När delmodell 6:s strålar inte går att räkna med refraktion för T₂ (en skärmtopp under
    linjen från den andra, P-6 tolkning 11) används Δℓ₀ för T₂; i det läget är r_Δℓ2 redan
    under 1 med räta strålar, och delmodell 6 ger själv `BullerFel` om den behövs. Samma
    reservregel gäller T₁₂ när delmodell 5:s strålar inte går att räkna.
    """
    if not atmosfar.har_refraktion or kanter.primar_form is None:
        return None, None, None
    stralar = d4.skarmstralar(profil, kanter.primar_form, atmosfar, val.uppat)
    dl1: float | None = stralar.gangvagsskillnad_m
    dl2: float | None = None
    if kanter.sekundar_form is not None:
        primar_forst = kanter.primar_form.index_kant < kanter.sekundar_form.index_kant
        forsta, andra = (
            (kanter.primar_form, kanter.sekundar_form)
            if primar_forst
            else (kanter.sekundar_form, kanter.primar_form)
        )
        try:
            st = d6.skarmstralar_tva(
                profil, forsta, andra, atmosfar, vinkelregel=val.vinkelregel, uppat=val.uppat
            )
            dl2 = st.gangvagsskillnad_andra_m if primar_forst else st.gangvagsskillnad_forsta_m
        except BullerFel:
            dl2 = None
    dl12: float | None = None
    if kanter.sekundar_kant is not None:
        try:
            ordnad = d5.tvakantsform(kanter.primar_form, kanter.sekundar_kant.index)
            st5 = d5.skarmstralar_tva_kanter(profil, ordnad, atmosfar, val.uppat).kanter
            if ordnad.index_t1 == kanter.sekundar_kant.index:  # T₁₂ närmast källan
                dl12 = d4.gangvagsskillnad_refraktion_m(
                    st5.vag_kalla_m, st5.vag_mitten_m, st5.theta_1s_rad, st5.theta_1r_rad
                )
            else:
                dl12 = d4.gangvagsskillnad_refraktion_m(
                    st5.vag_mitten_m, st5.vag_mottagare_m, st5.theta_2s_rad, st5.theta_2r_rad
                )
        except BullerFel:
            dl12 = None
    return dl1, dl2, dl12


@dataclass(frozen=True)
class Profilgeometri:
    """Väderoberoende förberäkningar för en terrängprofil (delas mellan väderklasser)."""

    geo: Terranggeometri
    kanter: Skarmkanter
    over_plan: GeometriOverPlanMark
    segment: list[Marksegment]
    plan_avvikelse_m: float


def forberakna_profilgeometri(profil: Terrangprofil) -> Profilgeometri:
    """Förberäknar väderoberoende terränggeometri, skärmkanter och plan-mark-projektion."""
    geo = terranggeometri(profil)
    kanter = valj_skarmkanter(geo)
    plan = ekvivalent_plan_mark(geo.x_m, geo.z_m)
    over_plan = geometri_over_plan_mark(geo, plan)
    segment = marksegment_over_plan_mark(geo, over_plan)
    avvikelse = float(np.max(np.abs(geo.z_m - plan.alfa_m - plan.beta * geo.x_m)))
    return Profilgeometri(
        geo=geo,
        kanter=kanter,
        over_plan=over_plan,
        segment=segment,
        plan_avvikelse_m=avvikelse,
    )


def terrangeffekt(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    atmosfar: Atmosfar,
    val: Sammansattningsval | None = None,
    *,
    profilgeometri: Profilgeometri | None = None,
) -> Terrangeffekt:
    """ΔL_t utan ΔL₈ för en profil, AV 1106/07 avsnitt 5.21–5.22, ekv. (306)–(319) och (332).

    Stegen: (1) kanterna T₁, T₂, T₁₂ väljs med räta strålar (avsnitt 5.21.1–5.21.3);
    (2) övergångsparametrarna r_scr1, r_scr2, r_scr12 räknas, med Δℓ ur ekv. (301) och (307)
    när atmosfären bryter; (3) ΔL_noscr räknas där r_scr1 < 1 i något band: ΔL_flat över den
    ekvivalenta plana marken (avsnitt 5.21.5) och, när profilen har mer än ett segment,
    ΔL₃ och r_flat (ekv. 317–319); (4) skärmdelmodellerna 4, 5 och 6 räknas där deras vikt är
    större än 0 i något band, var och en med turbulensspridningen (delmodell 7) lagd till
    energimässigt och med markfaktorn p_G från samma delmodell; (5) allt vägs ihop enligt
    ekv. (332).

    Ett enda terrängsegment är plan mark: r_flat = 1 och ΔL_noscr = ΔL_flat utan delmodell 3.
    För plan mark i flera segment är r_flat = 1 exakt i alla band, och ΔL₃ används då inte.
    """
    val = Sammansattningsval() if val is None else val
    pg = forberakna_profilgeometri(profil) if profilgeometri is None else profilgeometri
    geo = pg.geo
    c = atmosfar.c_m_s
    kanter = pg.kanter
    dl1, dl2, dl12 = gangvagsskillnader_refraktion(frekvenser_hz, profil, kanter, atmosfar, val)
    tolkning = tolka_terrang(
        geo,
        kanter,
        frekvenser_hz,
        c,
        gangvagsskillnad_t1_m=dl1,
        gangvagsskillnad_t2_m=dl2,
        gangvagsskillnad_t12_m=dl12,
    )
    r_scr1, r_scr2, r_scr12 = tolkning.r_scr1, tolkning.r_scr2, tolkning.r_scr12
    anvanda: list[str] = []

    # --- ΔL_noscr, ekv. (319) ---------------------------------------------------------------
    r_flat: Flyttal | None = None
    plan_db: Flyttal | None = None
    dl3_db: Flyttal | None = None
    noscr_db: Flyttal | None = None
    resultat3: Delmodell3Resultat | None = None
    if bool(np.any(r_scr1 < 1.0)):
        over_plan = pg.over_plan
        segment = pg.segment
        try:
            plan_db, namn = markeffekt_plan_modell_db(
                frekvenser_hz, over_plan, segment, atmosfar, val
            )
        except BullerFel as fel:
            raise _med_namn("Plan mark (delmodell 1–2)", fel) from fel
        anvanda.append(namn)
        avvikelse = pg.plan_avvikelse_m
        if geo.x_m.shape[0] == 2 or (avvikelse <= PLAN_TOLERANS_M and not val.alltid_delmodell3):
            # Plan mark: r_f = 1 (Δh = 0) och w_con ≤ 1 (segmenten delar en och samma
            # Fresnelzon), så r_flat = 1 enligt ekv. (317)–(318) och ΔL₃ behövs inte.
            r_flat = np.ones(frekvenser_hz.shape[0], dtype=np.float64)
            noscr_db = plan_db
        else:
            try:
                resultat3 = delmodell3(
                    frekvenser_hz,
                    geo,
                    atmosfar,
                    val.fresnelzon_delmodell3,
                    uppat=val.uppat,
                    ct2_form=val.ct2_form,
                    skuggzon=val.skuggzon,
                    uppskjut_fel=True,
                    overgangshojd=val.overgangshojd,
                )
            except BullerFel as fel:
                raise _med_namn("Delmodell 3 (icke-plan terräng)", fel) from fel
            r_flat = overgang_rflat(resultat3, geo, c)
            dl3_db = resultat3.markeffekt_db
            if bool(np.any(r_flat < 1.0)):
                anvanda.append("3")
            # Ett band där delmodell 3 inte gick att räkna får bara förekomma med vikten 0.
            for b, besked in enumerate(resultat3.fel):
                if besked is not None and float(r_flat[b]) < 1.0 and float(r_scr1[b]) < 1.0:
                    raise BullerFel(
                        "Delmodell 3 (icke-plan terräng): "
                        f"{float(frekvenser_hz[b]):.0f} Hz, {besked}"
                    )
            noscr_db = _blanda(r_flat, plan_db, dl3_db)

    # --- ΔL_scr, ekv. (310) och (315) --------------------------------------------------------
    dl4_tot: Flyttal | None = None
    dl5_tot: Flyttal | None = None
    dl6_tot: Flyttal | None = None
    scr_db: Flyttal | None = None
    resultat4: d4.Delmodell4Resultat | None = None
    resultat5: d5.Delmodell5Resultat | None = None
    resultat6: d6.Delmodell6Resultat | None = None
    skarm_verksam = r_scr1 > 0.0
    if bool(np.any(skarm_verksam)):
        form = tolkning.primar_form
        primar = tolkning.primar
        if form is None or primar is None:
            raise BullerFel("Sammansättningen: r_scr1 > 0 utan primär skärm.")
        behov_scr1 = skarm_verksam & (r_scr2 < 1.0)
        behov_4 = behov_scr1 & (r_scr12 < 1.0)
        behov_5 = behov_scr1 & (r_scr12 > 0.0)
        behov_6 = skarm_verksam & (r_scr2 > 0.0)
        t_luft = atmosfar.temperatur_luft_c
        if bool(np.any(behov_4)):
            try:
                resultat4 = d4.skarm_en_kant(
                    frekvenser_hz,
                    profil,
                    form,
                    atmosfar,
                    viktregel=val.viktregel,
                    uppat=val.uppat,
                    ct2_form=val.ct2_form,
                    skuggzon=val.skuggzon,
                )
                x_s, z_s, x_r, z_r = _kalla_mottagare_delmodell7(geo, resultat4.stralar, atmosfar)
                dl71 = d7.turbulensspridning_en_kant_db(
                    frekvenser_hz,
                    x_s,
                    z_s,
                    x_r,
                    z_r,
                    float(geo.x_m[form.index_kant]),
                    float(geo.z_m[form.index_kant]),
                    atmosfar.cv2,
                    atmosfar.ct2,
                    t_luft,
                    markfaktor_pg=resultat4.markfaktor_pg,
                )
            except BullerFel as fel:
                raise _med_namn("Delmodell 4 (en skärm, en kant)", fel) from fel
            dl4_tot = energisumma_db(resultat4.delta_l_db, dl71)
            anvanda.append("4")
        if bool(np.any(behov_5)):
            kant12 = tolkning.sekundar_kant
            if kant12 is None:
                raise BullerFel("Sammansättningen: r_scr12 > 0 utan sekundär kant.")
            try:
                resultat5 = d5.skarm_tva_kanter(
                    frekvenser_hz,
                    profil,
                    form,
                    kant12.index,
                    atmosfar,
                    viktregel=val.viktregel,
                    uppat=val.uppat,
                    ct2_form=val.ct2_form,
                    skuggzon=val.skuggzon,
                )
                f5 = resultat5.stralar.form
                dl72 = d7.turbulensspridning_tva_kanter_db(
                    frekvenser_hz,
                    *_kalla_mottagare_tva_kanter(
                        geo,
                        (f5.index_t1, f5.index_t2),
                        resultat5.stralar.kanter,
                        resultat5.stralar.vinkelandring_kalla_rad,
                        resultat5.stralar.vinkelandring_mottagare_rad,
                        atmosfar,
                    ),
                    float(geo.x_m[f5.index_t1]),
                    float(geo.z_m[f5.index_t1]),
                    float(geo.x_m[f5.index_t2]),
                    float(geo.z_m[f5.index_t2]),
                    atmosfar.cv2,
                    atmosfar.ct2,
                    t_luft,
                    markfaktor_pg=resultat5.markfaktor_pg,
                )
            except BullerFel as fel:
                raise _med_namn("Delmodell 5 (en skärm, två kanter)", fel) from fel
            dl5_tot = energisumma_db(resultat5.delta_l_db, dl72)
            anvanda.append("5")
        if bool(np.any(behov_6)):
            try:
                forsta, andra, viktigast = d6.skarmar_ur_tolkning(tolkning)
                resultat6 = d6.tva_skarmar(
                    frekvenser_hz,
                    profil,
                    forsta,
                    andra,
                    viktigast,
                    atmosfar,
                    viktregel=val.viktregel,
                    vinkelregel=val.vinkelregel,
                    uppat=val.uppat,
                    ct2_form=val.ct2_form,
                    skuggzon=val.skuggzon,
                )
                dl72_6 = d7.turbulensspridning_tva_kanter_db(
                    frekvenser_hz,
                    *_kalla_mottagare_tva_kanter(
                        geo,
                        (forsta.index_kant, andra.index_kant),
                        resultat6.stralar.kanter,
                        resultat6.stralar.vinkelandring_kalla_rad,
                        resultat6.stralar.vinkelandring_mottagare_rad,
                        atmosfar,
                    ),
                    float(geo.x_m[forsta.index_kant]),
                    float(geo.z_m[forsta.index_kant]),
                    float(geo.x_m[andra.index_kant]),
                    float(geo.z_m[andra.index_kant]),
                    atmosfar.cv2,
                    atmosfar.ct2,
                    t_luft,
                    markfaktor_pg=resultat6.markfaktor_pg,
                )
            except BullerFel as fel:
                raise _med_namn("Delmodell 6 (två skärmar)", fel) from fel
            dl6_tot = energisumma_db(resultat6.delta_l_db, dl72_6)
            anvanda.append("6")
        if resultat4 is not None or resultat5 is not None or resultat6 is not None:
            anvanda.append("7")
        # Ekv. (315) och (310). Led utan vikt läses inte.
        antal = frekvenser_hz.shape[0]
        scr_db = np.zeros(antal, dtype=np.float64)
        for b in range(antal):
            if not bool(skarm_verksam[b]):
                continue
            w2, w12 = float(r_scr2[b]), float(r_scr12[b])
            varde = 0.0
            if w2 > 0.0:
                varde += w2 * _band(dl6_tot, b)
            if w2 < 1.0:
                scr1 = 0.0
                if w12 > 0.0:
                    scr1 += w12 * _band(dl5_tot, b)
                if w12 < 1.0:
                    scr1 += (1.0 - w12) * _band(dl4_tot, b)
                varde += (1.0 - w2) * scr1
            scr_db[b] = varde

    delta_l = _blanda(r_scr1, scr_db, noscr_db)  # ekv. (306), första raden i ekv. (332)
    return Terrangeffekt(
        frekvenser_hz=frekvenser_hz,
        delta_l_db=delta_l,
        delmodeller=tuple(anvanda),
        tolkning=tolkning,
        r_flat=r_flat,
        plan_db=plan_db,
        delmodell3_db=dl3_db,
        utan_skarm_db=noscr_db,
        delmodell4_db=dl4_tot,
        delmodell5_db=dl5_tot,
        delmodell6_db=dl6_tot,
        skarm_db=scr_db,
        resultat3=resultat3,
        resultat4=resultat4,
        resultat5=resultat5,
        resultat6=resultat6,
    )


def _band(varden: Flyttal | None, band: int) -> float:
    if varden is None:
        raise BullerFel("Sammansättningen: ett led med vikt större än 0 saknas.")
    return float(varden[band])


def _kalla_mottagare_delmodell7(
    geo: Terranggeometri, stralar: d4.Skarmstralar, atmosfar: Atmosfar
) -> tuple[float, float, float, float]:
    """S och R, eller S′ och R′ i brytande atmosfär, till delmodell 7 (AV 1106/07 ekv. 275).

    I brytande atmosfär läggs S′ på avståndet R_S från kanten i riktningen θ₁ + Δθ_S och R′ på
    avståndet R_R i riktningen θ₂ + Δθ_R, med storheterna ur ekv. (161)–(162).
    """
    if not atmosfar.har_refraktion:
        return geo.x_s_m, geo.z_s_m, geo.x_r_m, geo.z_r_m
    x_t, z_t = float(geo.x_m[stralar.index_t]), float(geo.z_m[stralar.index_t])
    return d7.korrigerad_kalla_och_mottagare(
        x_t,
        z_t,
        x_t,
        z_t,
        stralar.kil.vag_kalla_m,
        stralar.kil.vag_mottagare_m,
        stralar.vinklar.theta1_rad,
        stralar.vinklar.theta2_rad,
        stralar.vinkelandring_kalla_rad,
        stralar.vinkelandring_mottagare_rad,
    )


def _kalla_mottagare_tva_kanter(
    geo: Terranggeometri,
    index_kanter: tuple[int, int],
    kanter: TvaKanter,
    vinkelandring_kalla_rad: float,
    vinkelandring_mottagare_rad: float,
    atmosfar: Atmosfar,
) -> tuple[float, float, float, float]:
    """S och R, eller S′ och R′ i brytande atmosfär, för två kanter (AV 1106/07 ekv. 276).

    S′ läggs på avståndet R_S från kanten närmast källan i riktningen θ₁ + Δθ_S, R′ på avståndet
    R_R från kanten närmast mottagaren i riktningen θ₂ + Δθ_R; θ₁ och θ₂ är riktningarna från
    respektive kant mot S och R, mätta från lodlinjen nedåt.
    """
    if not atmosfar.har_refraktion:
        return geo.x_s_m, geo.z_s_m, geo.x_r_m, geo.z_r_m
    x_t1, z_t1 = float(geo.x_m[index_kanter[0]]), float(geo.z_m[index_kanter[0]])
    x_t2, z_t2 = float(geo.x_m[index_kanter[1]]), float(geo.z_m[index_kanter[1]])
    return d7.korrigerad_kalla_och_mottagare(
        x_t1,
        z_t1,
        x_t2,
        z_t2,
        kanter.vag_kalla_m,
        kanter.vag_mottagare_m,
        d4.vinkel_fran_lodlinjen_rad(geo.z_s_m - z_t1, x_t1 - geo.x_s_m),
        d4.vinkel_fran_lodlinjen_rad(geo.z_r_m - z_t2, geo.x_r_m - x_t2),
        vinkelandring_kalla_rad,
        vinkelandring_mottagare_rad,
    )
