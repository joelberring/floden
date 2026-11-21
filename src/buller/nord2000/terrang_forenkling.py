"""Förenkling av en tät terrängprofil före beräkning (förbehandling, informativ bilaga).

Källa: FORCE TC-101327 (2019) avsnitt 2.15, som ersätter hela bilaga B i DELTA AV 1106/07
(rev. 2014): "maximal avvikelse-principen" med gränsen för r_max (ekv. 1 i TC, samma som
första delen av ekv. 393 i AV 1106/07), högsta antal segment N_ts,max, förenkling av
markytans profil (tabell 14) och sammanslagning av de två profilerna. Kravet på segmentlängd
d_segm finns bara i den äldre bilagan (AV 1106/07 ekv. 393, andra delen) och är valbart här.

Bilagan är informativ och kallar sig själv ett förslag. Funktionen är fristående: kärnan
anropar den inte, den som bygger profiler (B3) eller kör testfall väljer själv.

Konstanterna `TABELL_14_*` är avlästa ur TC-101327 tabell 14.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from buller.nord2000.geometri import langd, normlinje
from buller.typer import BullerFel, Flyttal, Terrangprofil


@dataclass(frozen=True)
class Forenklingsregler:
    """Kraven som styr förenklingen, TC-101327 avsnitt 2.15 och AV 1106/07 bilaga B.

    `max_antal_segment` är N_ts,max och gäller terrängformen (före sammanslagningen med
    markytans brytpunkter). `segmentlangdskrav` slår på d_segm ur AV 1106/07 ekv. (393), som
    TC-101327 har tagit bort. `antal_marktyper` (1, 2 eller 3) reducerar flödesresistiviteten
    enligt TC-101327 tabell 14; `None` lämnar den orörd.
    """

    max_antal_segment: int = 10
    segmentlangdskrav: bool = False
    antal_marktyper: int | None = None

    def __post_init__(self) -> None:
        if self.max_antal_segment < 1:
            raise BullerFel("max_antal_segment ska vara minst 1.")
        if self.antal_marktyper not in (None, 1, 2, 3):
            raise BullerFel("antal_marktyper ska vara 1, 2, 3 eller None.")


# TC-101327 avsnitt 2.15: 10 segment för oskärmad utbredning, upp till 20 med skärmar.
REGLER_TC_101327 = Forenklingsregler(max_antal_segment=10)
REGLER_TC_101327_SKARMAD = Forenklingsregler(max_antal_segment=20)
# AV 1106/07 (rev. 2014) bilaga B, ekv. (393): 10 segment och krav på segmentlängd.
REGLER_AV_1106_07 = Forenklingsregler(max_antal_segment=10, segmentlangdskrav=True)

# TC-101327 tabell 14: intervallgränser och representativa σ (kNs/m⁴).
TABELL_14_GRANS_MJUK = 125.0
TABELL_14_GRANS_HARD = 800.0
TABELL_14_SIGMA_MJUK = 31.5
TABELL_14_SIGMA_NORMAL = 200.0
TABELL_14_SIGMA_HARD = 20000.0


def storsta_tillatna_avvikelse_m(avstand_m: float) -> float:
    """Gränsen för r_max, TC-101327 avsnitt 2.15 ekv. (1) (= AV 1106/07 ekv. 393, första delen).

    `avstand_m` är d, här tolkat som det horisontella avståndet mellan profilens ändpunkter.
    """
    if avstand_m <= 0.0:
        raise BullerFel("Avståndet d ska vara större än 0.")
    if avstand_m <= 50.0:
        return 0.1
    if avstand_m < 500.0:
        return 0.002 * avstand_m
    return 1.0


def segmentlangdsgrans_m(avstand_m: float) -> float:
    """Gränsen för d_segm, AV 1106/07 (rev. 2014) bilaga B, ekv. (393), andra delen.

    Kravet är struket i TC-101327. Här används det som en nedre gräns för uppdelningen: ett
    segment som inte är längre än värdet delas inte vidare.
    """
    if avstand_m <= 0.0:
        raise BullerFel("Avståndet d ska vara större än 0.")
    if avstand_m <= 20.0:
        return 1.0
    if avstand_m < 200.0:
        return 0.05 * avstand_m
    return 10.0


def representativ_flodesresistivitet_kNsm4(
    flodesresistivitet_kNsm4: float, antal_marktyper: int
) -> float:
    """Representativt σ när antalet marktyper reduceras till 1–3, TC-101327 tabell 14.

    Tabellens intervall är "< 125", "125–800" och "> 800"; gränsvärdena 125 och 800 förs till
    mellanintervallet.
    """
    if antal_marktyper not in (1, 2, 3):
        raise BullerFel("antal_marktyper ska vara 1, 2 eller 3.")
    if flodesresistivitet_kNsm4 < 0.0:
        raise BullerFel("Flödesresistiviteten får inte vara negativ.")
    if antal_marktyper == 1:
        return TABELL_14_SIGMA_NORMAL
    if flodesresistivitet_kNsm4 > TABELL_14_GRANS_HARD:
        return TABELL_14_SIGMA_HARD
    if antal_marktyper == 3 and flodesresistivitet_kNsm4 < TABELL_14_GRANS_MJUK:
        return TABELL_14_SIGMA_MJUK
    return TABELL_14_SIGMA_NORMAL


def storsta_avvikelse(x_m: Flyttal, z_m: Flyttal, forsta: int, sista: int) -> tuple[float, int]:
    """r_max och dess punkt mellan terrängpunkterna `forsta` och `sista`, TC-101327 figur 44.

    r_max är det största vinkelräta avståndet från en mellanliggande punkt till linjen mellan
    de två punkterna. Utan mellanliggande punkter returneras (0, −1). Vid lika avstånd väljs
    punkten med lägst index.
    """
    r_max, index = 0.0, -1
    x1, z1, x2, z2 = float(x_m[forsta]), float(z_m[forsta]), float(x_m[sista]), float(z_m[sista])
    for i in range(forsta + 1, sista):
        _, _, avstand = normlinje(x1, z1, x2, z2, float(x_m[i]), float(z_m[i]))
        if abs(avstand) > r_max:
            r_max, index = abs(avstand), i
    return r_max, index


@dataclass(frozen=True)
class Forenkling:
    """Förenklad profil med redovisning av vad som gjordes.

    `terrangindex` är de punkter i den täta profilen som bildar den förenklade terrängformen
    och `r_max_kvar_m` den största kvarvarande avvikelsen mellan den täta profilen och formen.
    `kraven_uppfyllda` är falsk när N_ts,max nåddes innan alla segment klarade kravet på r_max
    (eller, med segmentlängdskravet påslaget, var korta nog att lämnas). `inlagda_markpunkter`
    är antalet brytpunkter i markytan som lades in utöver terrängformen.
    """

    profil: Terrangprofil
    terrangindex: tuple[int, ...]
    r_max_kvar_m: float
    r_max_grans_m: float
    kraven_uppfyllda: bool
    inlagda_markpunkter: int


def _marktyper(profil: Terrangprofil, regler: Forenklingsregler) -> list[tuple[float, float]]:
    """(σ, råhet) per segment i den täta profilen, σ ev. reducerad enligt tabell 14."""
    sigma = [float(v) for v in profil.flodesresistivitet_kNsm4]
    if regler.antal_marktyper is not None:
        sigma = [representativ_flodesresistivitet_kNsm4(s, regler.antal_marktyper) for s in sigma]
    return list(zip(sigma, (float(r) for r in profil.rahet_m), strict=True))


def forenkla_terrang_detalj(
    profil: Terrangprofil,
    regler: Forenklingsregler = REGLER_TC_101327,
    bevara_index: Sequence[int] = (),
) -> Forenkling:
    """Förenklar en tät terrängprofil, TC-101327 avsnitt 2.15 (ersätter AV 1106/07 bilaga B).

    1. Terrängformen: utgå från linjen mellan första och sista punkten. Dela det segment som
       har störst r_max i den punkten, och upprepa tills varje segment har r_max inom gränsen
       i ekv. (1) eller antalet segment är N_ts,max.
    2. Markytan: punkter där marktypen (σ, råhet) är densamma som i föregående segment tas
       bort, efter eventuell reduktion av σ enligt tabell 14.
    3. Sammanslagning: markytans brytpunkter som inte redan finns i terrängformen läggs in med
       z linjärt interpolerat i den förenklade formen.

    Ändpunkterna, källhöjden och mottagarhöjden bevaras. `bevara_index` är punkter som alltid
    ska vara kvar (t.ex. en skärmform W₁–T₁–W₂ ur `terrang_skarmar`); de räknas in i antalet
    segment.
    """
    x, z = profil.x_m, profil.z_m
    n = x.shape[0]
    if any(not 0 <= i < n for i in bevara_index):
        raise BullerFel("bevara_index pekar utanför profilen.")
    avstand = float(x[-1] - x[0])
    grans = storsta_tillatna_avvikelse_m(avstand)
    langdgrans = segmentlangdsgrans_m(avstand) if regler.segmentlangdskrav else None

    behallna = sorted({0, n - 1, *bevara_index})

    def oklara_segment() -> list[tuple[float, int]]:
        """(r_max, punkt) för segment som inte uppfyller kraven."""
        ut: list[tuple[float, int]] = []
        for a, b in zip(behallna, behallna[1:], strict=False):
            r_max, punkt = storsta_avvikelse(x, z, a, b)
            if punkt < 0 or r_max <= grans:
                continue
            if langdgrans is not None and (
                langd(float(x[a]), float(z[a]), float(x[b]), float(z[b])) <= langdgrans
            ):
                continue
            ut.append((r_max, punkt))
        return ut

    while True:
        oklara = oklara_segment()
        if not oklara or len(behallna) - 1 >= regler.max_antal_segment:
            break
        r_storst, punkt = oklara[0]
        for r_max, kandidat in oklara[1:]:
            if r_max > r_storst:
                r_storst, punkt = r_max, kandidat
        behallna = sorted([*behallna, punkt])

    r_kvar = max(
        (storsta_avvikelse(x, z, a, b)[0] for a, b in zip(behallna, behallna[1:], strict=False)),
        default=0.0,
    )

    typer = _marktyper(profil, regler)
    brytpunkter = [i for i in range(1, n - 1) if typer[i] != typer[i - 1]]
    alla = sorted({*behallna, *brytpunkter})
    x_form = np.array([float(x[i]) for i in behallna], dtype=np.float64)
    z_form = np.array([float(z[i]) for i in behallna], dtype=np.float64)
    x_ny = np.array([float(x[i]) for i in alla], dtype=np.float64)
    z_ny = np.interp(x_ny, x_form, z_form)
    for nr, i in enumerate(alla):
        if i in behallna:
            z_ny[nr] = float(z[i])  # behållna punkter exakt, utan interpolationens avrundning
    forenklad = Terrangprofil(
        x_m=x_ny,
        z_m=z_ny,
        # Segmentet mellan alla[k] och alla[k + 1] ligger inom ett stycke med en marktyp, den
        # som gäller i det täta segment som börjar i alla[k].
        flodesresistivitet_kNsm4=np.array([typer[i][0] for i in alla[:-1]], dtype=np.float64),
        rahet_m=np.array([typer[i][1] for i in alla[:-1]], dtype=np.float64),
        kalla_hojd_m=profil.kalla_hojd_m,
        mottagare_hojd_m=profil.mottagare_hojd_m,
    )
    return Forenkling(
        profil=forenklad,
        terrangindex=tuple(behallna),
        r_max_kvar_m=r_kvar,
        r_max_grans_m=grans,
        kraven_uppfyllda=not oklara,
        inlagda_markpunkter=len(alla) - len(behallna),
    )


def forenkla_terrang(
    profil: Terrangprofil,
    regler: Forenklingsregler = REGLER_TC_101327,
    bevara_index: Sequence[int] = (),
) -> Terrangprofil:
    """Förenklad terrängprofil enligt TC-101327 avsnitt 2.15; se `forenkla_terrang_detalj`."""
    return forenkla_terrang_detalj(profil, regler, bevara_index).profil
