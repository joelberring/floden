# T06 – Infiltration, grundvatten och brunnar

**Mål:** förluster per cell och nod i stället för ett schablonavdrag.

**Läs först:** `docs/DATAKONTRAKT.md` (K1, K5), `docs/DATA.md`.
**Beroenden:** T01.

## Steg
1. Infiltrationskapacitet per markslag och jordart (SGU). Green–Ampt eller Horton med parametrar i `config.toml`, plus initial förlust.
2. Tak från grundvattnet: tillgänglig lagring = porositet × djup till grundvattenytan. Grundvattendjup från djup-till-vatten (DTW) ur terrängen, kalibrerat mot observationsrör (SGU, staden).
3. In- och utströmningsområden som karta.
4. Brunnar som sänkor med inloppskapacitet per typ. Ledningsnätet först som kapacitet per delområde.
5. Scenarier enligt K5: tjäle, hög grundvattennivå, igensatta brunnar, fulla ledningar.
6. Årsvattenbalans före/efter med vanligt regn (körs i T03:s grafmodell).

## Acceptans
- Förluster per cell som raster och per nod i grafen.
- Känslighet: hur mycket maxdjupet i provområdet ändras mellan scenarierna.

## Stoppa och fråga
- Ledningsdata: bara lokalt. Använd syntetiska nät i tester.
- Parametervärden för jordarter som saknas i litteraturen.
