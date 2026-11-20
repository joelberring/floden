# T07 – Ruttanalys för räddningstjänsten

**Mål:** insatstider medan vattnet stiger och sjunker, nåbarhet för entréer, kritiska gatuavsnitt och användning vid vädervarning.

**Läs först:** `docs/DATAKONTRAKT.md` (K6, K7, K8).
**Beroenden:** T03 (graf-nivån räcker för första versionen), vägnät från NVDB.

## Steg
1. Vägnät som graf från NVDB och baskartan: längd, hastighet, höjdläge. Broar och tunnlar från markmodellens nivåer.
2. Tidsberoende vikter: djup och hastighet per gatuavsnitt och tidpunkt från K6. Hastighet per fordonstyp enligt K7.
3. Tidsberoende kortaste väg (Dijkstra med FIFO-egenskap) från varje station till varje adress.
4. Entréer: kortaste gångväg genom celler under djupgränsen till en uppställningsplats på framkomlig gata, inom maxavstånd.
5. Kritiska avsnitt: vilka få avsnitt återställer mest åtkomst om de hålls öppna (minsta snitt, girig sökning).
6. Ensemble: sannolikhet per avsnitt att bli ofarbart, från T03:s scenarier.
7. Vädervarning: kör från prognos- och radarscenarier, uppdatera med några minuters mellanrum.

## Acceptans
- K8 för provområdet med testparametrar.
- Modulen vägrar köra skarpt när K7 innehåller `null`, och listar vad som saknas.
- En bro över ett översvämmat område räknas som farbar.

## Stoppa och fråga
- Alla gränsvärden i K7 bestäms med räddningstjänsten. Hitta aldrig på värden för skarp användning.
- Stationer och särskilda objekt: be Joel om källa.
