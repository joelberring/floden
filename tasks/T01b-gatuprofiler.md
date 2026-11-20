# T01b – Gatuprofiler

**Mål:** körbana, kantsten och gångbana rekonstrueras som parametriska profiler i stället för att terrängen interpoleras, så att vatten följer rännstenar och lämnar gatan vid sänkta kantstenar.

**Läs först:** `tasks/T01-markmodell.md`, `docs/DATAKONTRAKT.md` (K1).
**Beroenden:** T01 (markpunkter, baskartans linjer, rutnät).

## Steg
1. Korridorer ur baskartan: körbana, gångbana, refuger, infarter och kantstenslinjer, med gatans mittlinje som axel (NVDB eller baskartan).
2. Tvärsektioner var 1–2 m vinkelrätt mot mittlinjen.
3. I varje sektion passas en modell till laserns markpunkter: körbanan som två plan med nock (bombering) eller ett plan (sidolutning), ett höjdsprång vid kantstenen och gångbanans lutning.
4. Robust passning (Huber eller liknande) så att bilar och felpunkter inte drar iväg den.
5. Jämnhet längs gatan: profilens parametrar som ett strukturerat minsta kvadratproblem med jämnhetsvillkor (bandat ekvationssystem, löses i linjär tid per gata, som en Kalman-utjämnare). Osäkerheten per sektion sparas.
6. Typvärden där data inte räcker: kantsten 10–12 cm, tvärfall runt 2,5 %. Markera som osäkra.
7. Sänkta kantstenar vid infarter och övergångsställen från baskartans lager för infarter.
8. Rännstensbrunnar från ledningsdata, annars i profilens lågpunkter.
9. Profilerna rastreras in i `mark.tif` inom gatukorridoren. Utanför gäller T01.
10. Kontroll mot stadens tätare laser (om den finns) och mot projekteringsritningar för några gator.

## Acceptans
- Testfall A5 och A6 går igenom.
- För gator med tätare laser: rapportera medelfel i kantstenshöjd och tvärfall.
- Tvärsnittsbilder för tio slumpvis valda sektioner i provområdet.

## Stoppa och fråga
- Om baskartan saknar kantsten på många gator.
- Om typvärdena verkar fel för Stockholm.
