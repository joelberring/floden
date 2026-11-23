"""Källbelagda svenska skissprofiler för fokusarter/artgrupper (`src/ekologi/`).

Markslagskoder följer Flodens K1-konvention (0 = öppen mark/gräs/grus, 1 = asfalt/gata/hårdgjord,
2 = byggnad, 3 = vatten, 4 = skog/träd/buskage, 5 = våtmark/damm, 6 = äng/grönyta).
Samtliga profiler är märkta `skiss` med explicita källhänvisningar till svensk grön
infrastruktur-metodik (Naturvårdsverket / Stockholms stads ESBO).
"""

from __future__ import annotations

import math

from ekologi.typer import ArtProfil, EkologiFel

# K1-kompatibla markslagskoder med ekologisk utbyggnad (4–6)
MARKSLAG_OPPEN = 0
MARKSLAG_HARDGJORD = 1
MARKSLAG_BYGGNAD = 2
MARKSLAG_VATTEN = 3
MARKSLAG_SKOG = 4
MARKSLAG_VATMARK = 5
MARKSLAG_ANG = 6

STANDARD_ARTPROFILER: dict[str, ArtProfil] = {
    "ek_insekter": ArtProfil(
        profil_id="ek_insekter",
        namn="Eklevande insekter (ädellöv)",
        beskrivning=(
            "Spridningskänsliga vedlevande insekter knutna till ädellöv/äldre trädbestånd."
        ),
        kalla="Stockholms stad ESBO / Naturvårdsverket Grön infrastruktur (skissparametrar).",
        habitat_markslag=frozenset({MARKSLAG_SKOG}),
        min_area_m2=1000.0,
        spridningsavstand_m=500.0,
        motstand_per_markslag={
            MARKSLAG_SKOG: 1.0,
            MARKSLAG_ANG: 3.0,
            MARKSLAG_OPPEN: 5.0,
            MARKSLAG_VATMARK: 8.0,
            MARKSLAG_HARDGJORD: 25.0,
            MARKSLAG_VATTEN: 40.0,
            MARKSLAG_BYGGNAD: math.inf,
        },
        standard_motstand=10.0,
        min_kronhojd_m=5.0,
        motstand_osakerhet_faktor=(0.7, 1.4),
    ),
    "barrskogsfagel": ArtProfil(
        profil_id="barrskogsfagel",
        namn="Barrskogsfåglar (t.ex. tofsmes)",
        beskrivning="Skogslevande småfåglar som undviker breda öppna och hårdgjorda barriärer.",
        kalla="Stockholms stad ESBO / Länsstyrelsen värdetrakter barrskog (skissparametrar).",
        habitat_markslag=frozenset({MARKSLAG_SKOG}),
        min_area_m2=2500.0,
        spridningsavstand_m=1000.0,
        motstand_per_markslag={
            MARKSLAG_SKOG: 1.0,
            MARKSLAG_ANG: 4.0,
            MARKSLAG_OPPEN: 6.0,
            MARKSLAG_VATMARK: 6.0,
            MARKSLAG_HARDGJORD: 20.0,
            MARKSLAG_VATTEN: 15.0,
            MARKSLAG_BYGGNAD: 60.0,
        },
        standard_motstand=10.0,
        min_kronhojd_m=4.0,
        motstand_osakerhet_faktor=(0.7, 1.4),
    ),
    "groddjur": ArtProfil(
        profil_id="groddjur",
        namn="Groddjur (t.ex. större vattensalamander / vanlig padda)",
        beskrivning=(
            "Markbundna arter som kräver fuktiga landhabitat/våtmarker och stoppas starkt av "
            "hårdgjorda vägar och byggnader."
        ),
        kalla="Naturvårdsverket åtgärdsprogram groddjur / ESBO groddjur (skissparametrar).",
        habitat_markslag=frozenset({MARKSLAG_VATMARK, MARKSLAG_VATTEN}),
        min_area_m2=200.0,
        spridningsavstand_m=400.0,
        motstand_per_markslag={
            MARKSLAG_VATMARK: 1.0,
            MARKSLAG_VATTEN: 1.5,
            MARKSLAG_SKOG: 2.5,
            MARKSLAG_ANG: 3.0,
            MARKSLAG_OPPEN: 8.0,
            MARKSLAG_HARDGJORD: 80.0,
            MARKSLAG_BYGGNAD: math.inf,
        },
        standard_motstand=20.0,
        min_kronhojd_m=0.0,
        motstand_osakerhet_faktor=(0.6, 1.5),
    ),
    "pollinerare": ArtProfil(
        profil_id="pollinerare",
        namn="Pollinerare (vildbin och humlor)",
        beskrivning="Blombesökande insekter knutna till solbelysta ängs- och grönytor.",
        kalla="Naturvårdsverket grön infrastruktur / SLU pollinerare i stad (skissparametrar).",
        habitat_markslag=frozenset({MARKSLAG_ANG, MARKSLAG_OPPEN}),
        min_area_m2=200.0,
        spridningsavstand_m=600.0,
        motstand_per_markslag={
            MARKSLAG_ANG: 1.0,
            MARKSLAG_OPPEN: 1.5,
            MARKSLAG_SKOG: 4.0,
            MARKSLAG_VATMARK: 5.0,
            MARKSLAG_HARDGJORD: 12.0,
            MARKSLAG_VATTEN: 25.0,
            MARKSLAG_BYGGNAD: 80.0,
        },
        standard_motstand=8.0,
        min_kronhojd_m=0.0,
        motstand_osakerhet_faktor=(0.7, 1.4),
    ),
}


def hamta_artprofil(profil_id: str) -> ArtProfil:
    """Hämtar en källbelagd artprofil efter id, eller lyfter `EkologiFel`."""
    profil = STANDARD_ARTPROFILER.get(profil_id)
    if profil is None:
        giltiga = ", ".join(sorted(STANDARD_ARTPROFILER))
        raise EkologiFel(f"Okänd artprofil {profil_id!r}. Giltiga profiler: {giltiga}.")
    return profil
