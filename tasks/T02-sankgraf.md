# T02 – Sänkgrafen och statisk nivå

**Mål:** sänkhierarki, volymkurvor och flödesvägar enligt K2, och en statisk fyllning för ett givet regn.

**Läs först:** `docs/DATAKONTRAKT.md` (K1, K2), `docs/MATEMATIK.md` (Q2, Q4).
**Beroenden:** T01.

## Bakgrund
Fill–Spill–Merge (Barnes, Callaghan och Wickert 2021, Earth Surface Dynamics 9) bygger en sänkhierarki där vatten fyller sänkor som svämmar över och slås ihop. Den ger slutläget snabbt men inte förloppet över tid. Källkod finns öppet (Barnes, RichDEM och relaterade repo): kontrollera licens innan kod lånas.

## Steg
1. Priority-flood över markmodellen, med byggnader som hinder och öppningar som passager.
2. Sänkhierarki (sammanslagningsträd): löv, föräldrar, överfallsnivå och överfallspunkt.
3. Volymkurva och ytkurva per nod (kumulativa summor uppåt i trädet).
4. Flödesvägar från varje cell till sin nod, med gångtid vid referensdjup.
5. Persistens per nod och osäkerhet i överfallsnivån (från `osakerhet.tif`).
6. Persistensfilter: noder grundare än en parameter × osäkerheten blir lagring i föräldern. Spara både ofiltrerad och filtrerad graf.
7. Statisk fyllning: givet regnvolym per cell, fyll, låt svämma över och slå ihop. Skriv `max_djup.tif` (K6, nivå `graf`, statisk).
8. Hantera rutgränser: hierarkin ska vara global över området, inte per ruta.

## Acceptans
- Testfall A1–A4, A7–A9 (statiskt slutläge) går igenom.
- Volymfel < 0,1 %.
- Provområdet: bild av de största sänkorna och flödesvägarna, och en körning jämförd med Scalgo (via T09b).

## Stoppa och fråga
- Om persistensfiltret tar bort sänkor som Joel vet är verkliga.
- Matematiska frågor om filtret: skriv dem i `docs/MATEMATIK.md` (Q2).
