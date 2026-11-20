# T03 – Dynamik i sänkgrafen

**Mål:** djup över tid i varje nod, för hundratals regn på några sekunder, med osäkerhetsintervall.

**Läs först:** `docs/DATAKONTRAKT.md` (K2, K4, K5, K6), `docs/MATEMATIK.md` (Q1).
**Beroenden:** T02. T06 för förluster (kan börja med enkla förluster).

## Steg
1. Nodmodell: varje nod har volym, nivå (via volymkurvan), inflöde från avrinningsyta och uppströms noder, och utflöde via överfall (överfallsformel, flöde ∝ (h − h_överfall)^1,5 × bredd).
2. Fördröjning längs flödesvägar med gångtid (enkel linjär reservoar eller tidsförskjutning).
3. Lös med en enkel, robust tidsstegning först (referens).
4. Händelsestyrd variant: mellan händelser (överfall startar, sammanslagning) räknas förloppet analytiskt eller med stora steg. Jämför mot referensen.
5. Enhetssvar per nod före första överfall, och nytt regn som faltning. Mät vinsten.
6. Ensemble: kör scenarier (regn, förluster, igensatta brunnar) och skriv percentil 10/50/90 enligt K6.
7. Rapportera körtid per scenario och per ensemble.

## Acceptans
- Testfall A1–A4 dynamiskt: fyllnadstider och överfallsordning inom 1 % av facit.
- Volymfel < 0,1 %.
- Provområdet: 500 scenarier på under en minut på en vanlig dator.

## Stoppa och fråga
- Allt som rör exakthetsvillkor, komplexitet och tropisk formulering: skriv observationer och frågor i `docs/MATEMATIK.md` (Q1). Agenten ska bygga en fungerande numerisk variant, inte teorin.
