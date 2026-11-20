# T08 – Rapporter med AI-stöd

**Mål:** färdiga rapporter där varje tal och karta kommer från beräkningen och kontrolleras automatiskt.

**Läs först:** `AGENTS.md` (princip 1), `docs/DATAKONTRAKT.md` (K0, K6, K8).
**Beroenden:** T04, T07.

## Steg
1. Strukturerat resultat: samla tal, tabeller och kartor från K6/K8 och manifestet i en JSON per rapport.
2. Mallar per rapporttyp: skyfallsutredning för detaljplan, ruttanalys, grundvatten och vattenbalans, jämförelse av alternativ, underlag till planbeskrivningen.
3. Kartor renderas på servern med fast kartografi.
4. Språkmodellen skriver löptext från resultatet. Tal sätts in som platshållare som fylls från resultatet, aldrig från modellens text.
5. Kontroll: varje tal i texten finns i resultatet, varje nämnd karta finns, slutsatserna stämmer med gränsvärdena. Misslyckad kontroll stoppar rapporten.
6. Metod och osäkerhet skrivs ur manifestet. Rapporten får körningens id.
7. Export till Word och PDF.

## Acceptans
- En rapport för provområdet och en lapp, där kontrollen fångar ett medvetet inplanterat fel.
- Rapporten kan återskapas exakt från körnings-id.

## Stoppa och fråga
- Vilken språkmodell och vilken tjänst som får användas, och vilka data som får skickas dit.
