# T01 – Markmodellen

**Mål:** en markmodell för provområdet enligt K1, bättre än Lantmäteriets höjdmodell för vattenflöden i stad.

**Läs först:** `AGENTS.md`, `docs/DATAKONTRAKT.md` (K0, K1), `docs/DATA.md`, `docs/BESLUT.md`.
**Beroenden:** T00. Provområdet i `data/omraden/prov1.gpkg` (Joel).

## Indata
- Lantmäteriets laser (LAZ) för provområdet, hämtat via STAC eller lokalt.
- Stockholms baskarta (DWG eller GeoPackage) från den lokala datamappen.
- Trummor och passager: ur ledningsdata om de finns lokalt, annars ritade för hand i `data/omraden/prov1_oppningar.gpkg`.

## Steg
1. Rutnät av rutor (500 m, 25 m överlapp) över provområdet.
2. Laser: klipp till ruta (återanvänd `lasclip` från O-Tid), transformera till EPSG:3011, använd markklass 2. Ta bort broar (klass 17). Gör om markklassningen med SMRF i PDAL om klassningen är dålig.
3. Markraster 0,25 m genom triangulering av markpunkter med brytlinjer (se steg 5), rastrerat.
4. Byggnader: fotavtryck från baskartan. Byggnadsceller blir hinder. Portar och valv från baskartan eller `oppningar.gpkg` blir öppningar.
5. Brytlinjer: kantsten, mur, stödmur, staket och bankkant från baskartan med typhöjd per typ i `config.toml`. Fäst ihop linjerna till ett sammanhängande nät innan triangulering (i Solberga slöts över hälften av 12 343 lösa ändar redan vid 0,25 m).
6. Trummor och passager bränns in som öppningar genom bankar.
7. Osäkerhet per cell: från punkttäthet, avstånd till närmaste markpunkt, om cellen är interpolerad under hus eller vegetation, och skanningens ålder.
8. Markslag från baskartans ytor (+ O-Tids klassare för belagd yta som komplement).
9. Skriv K1 och `run.json`.
10. Jämförelsebild: markmodellen bredvid Lantmäteriets höjdmodell, med differens och utvalda tvärsnitt över gator.

## Acceptans
- K1-filer för alla rutor i provområdet, med giltiga recept.
- Testfall A7 (vägbank med trumma) och A8 (port) ger rätt beteende i den statiska analysen när T02 finns.
- Jämförelsebilden visar kantstenar och trummor som saknas i Lantmäteriets modell.
- Inga hål eller kanter mellan rutor (kontrollera överlappet).

## Stoppa och fråga
- Om baskartans lager inte går att tolka (lagerprofilerna i O-Tid kan hjälpa).
- Om tätare laser behövs för att komma vidare.

## Tips
- Rutorna får inte synas i resultatet. Kontrollera skarvar särskilt.
- Kantstenar är 10–15 cm. Ett fel på några cm i höjd ändrar flödesvägarna.
