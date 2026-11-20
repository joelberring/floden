# T09 – Testsvit och jämförelser

**Mål:** testfall med facit och jämförelser med andra verktyg, körbara med ett kommando.

**Läs först:** `docs/VALIDERING.md`.

## T09a – Syntetiska fall (beroende: T00)
Bygg fall A1–A9 i `docs/VALIDERING.md`: terräng (K1-format), regn, facit (analytiskt eller handräknat) och `fall.toml`.
Varje fall ska vara litet (under 1 MB) och dokumenterat med en bild.

## T09b – Scalgo och SMHI (beroende: T02)
Kör provområdet med samma regn. Scalgo körs av Joel (konto). SMHI:s kartering hämtas från deras tjänst.
Jämför maxdjup och träffad yta, och förklara skillnaderna en och en: terräng, förluster, dynamik.

## T09c – Environment Agency (beroende: T05)
Ladda ner Environment Agencys testfall för 2D-modeller (benchmarking-the-latest-generation-of-2d-hydraulic-flood-modelling-packages på gov.uk) och kör de som är relevanta för skyfall: fyllning av sänkor och det urbana fallet med regn. Jämför med de redovisade resultaten för andra program.

## Acceptans
- `floden testsvit` kör alla tillgängliga fall och skriver en tabell med mått.
- Inga gränser ändras av agenter.
