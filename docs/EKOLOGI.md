# Ekologisk konnektivitet (skiss)

Ekologiläget (BESLUT 2026-11-23) visar hur ett förslag, till exempel ett nytt hus, ändrar möjligheten för en artgrupp att ta sig
mellan sina livsmiljöer. Allt är **skiss**: metoden är en förenkling, profilernas parametrar saknar exakta källhänvisningar och
inga artfynd används. Kontrakten är K44 (artprofil) och K45 (lösaren, `stromtathet.tif`, `ekologi.json` och webb-API:t) i
`docs/DATAKONTRAKT.md`.

## Metod

1. **Mark.** K1-rutorna (K1 v2: `markslag.tif`, `vegetation.tif`) och eventuella K9-ändringar (ritade hus) slås ihop till ett
   rutnät med 4 m celler (`cellstorlek_m`). Markslaget i en cell är det vanligaste i de ingående 1 m-cellerna; högsta
   byggnads- och trädhöjd följer med. Flodens markslag översätts till kärnans markslagskoder i `data/ekologi/markslag.toml`
   (konfiguration, inte kod; alla val i filen är egna val). Trädskikt (`vegetation.tif` minst 0,5 m) över hårdgjord mark, gräs,
   skog och grus räknas som trädskikt. Okänd mark får ingen gissad klass: profilens standardmotstånd gäller och antalet okända
   celler redovisas som varning.
2. **Profil.** En artprofil (K44, `src/ekologi/artprofiler.py`) säger vilka markslag som är habitat, hur svårt varje markslag är
   att röra sig över (motstånd), spridningsavstånd och minsta fläckstorlek. Standardprofiler: `ek_insekter`, `barrskogsfagel`,
   `groddjur`, `pollinerare`. En egen profil kan anges som indata.
3. **Habitatfläckar.** Sammanhängande habitatceller över minsta storlek blir fläckar. Fler fläckar än `max_flackar` (förval 40)
   ger ett tydligt fel, så att körtiden hålls nere.
4. **Kretsteori** (McRae m.fl. 2008). Marken är ett nät av motstånd; effektivt motstånd mellan två fläckar räknas med en
   gles ekvationslösning. Det ger ett mått som tar hänsyn till alla vägar och inte bara den bästa. Normaliserad strömtäthet J
   (0–1) visar var rörelsen trängs ihop: det är kartan över **flaskhalsar** (`stromtathet.tif`).
5. **Mått.**
   - **PC** (Saura och Pascual-Hortal 2007): sannolikheten att två slumpvis valda punkter i habitatet kan nå varandra, andel.
   - **ECA** (m²): den sammanhängande habitatyta som ger samma PC; ett nytt hus ska inte öka den.
   - **dPC** per fläck (Saura och Rubio 2010) delas i `intra` (fläckens egen yta), `flux` och `connector` (fläckens roll som
     länk mellan andra).
6. **Före och efter.** Samma körning görs för bas och för bas plus K9. ΔECA = ECA efter − ECA före. Flaskhalskartan finns före,
   efter och som skillnad.

## Osäkerhet

Varje mått får ett intervall `[min, max]` ur två extremkörningar: motstånd × 1,4 och spridningsavstånd × 0,8 (sämst), motstånd ×
0,7 och spridningsavstånd × 1,25 (bäst). Måtten är monotona i dessa parametrar, så två körningar räcker och intervallet är
garanterat; det är dock bara osäkerheten i de två parametrarna, inte i klassningen av marken, rastreringen eller profilens val.
Faktorerna är egna val (`motstand_faktor_intervall`, `spridning_faktor_intervall`). Ett nytt hus ska ge ΔECA ≤ 0 (facittest G7,
`tests/ekologi/test_losare_k21.py`).

## Begränsningar

- **Profilerna är skiss.** Habitatval, motstånd och spridningsavstånd är inspirerade av svensk grön infrastruktur men saknar exakta
  källhänvisningar (`metodstatus = skiss`, `data/mcp/metodstatus.toml`). Resultatet visar hur just profilen reagerar, inte vad
  en verklig art gör.
- **Gräs är grönyta.** K1 skiljer inte klippt gräsmatta från äng; båda räknas som grönyta (profilens habitat om den vill). Ingen
  NMD (Nationella marktäckedata) läses in än, så våtmark finns inte i markkartan.
- **Rastrering.** 4 m-celler med majoritet kan i enstaka fall ge ΔECA > 0 för ett nytt hus; API:t varnar då och resultatet ska
  inte läsas som en förbättring.
- **Ett hus på en cell:** takets värde som habitat (grönt tak) och byggnadens barriäreffekt över 4 m räknas bara som byggnadsmark.
- **Artskydd.** Ingen artdata används och skyddsklassade artfynd får aldrig förekomma i indata, resultat eller rapporter.
- **Inte verifierat mot fältdata** och inte jämfört med något annat verktyg. Demokvarteret är påhittat.

## Användning

- Webben: steg 3 Frågor, ämnet Ekologi; steg 4 Resultat, fliken Ekologi (flaskhalskarta före, efter och skillnad; ECA före och
  efter med intervall, ΔECA, PC, antal habitatfläckar och en slutsats; saknade tal visas som "–").
- API: `POST /api/analys/ekologi` (K45), behörighet som övriga analyser. Svaret innehåller ECA och PC före och efter med intervall,
  ΔECA, lagerreferenser och de största flaskhalsarna.
- Lösare: `ekologi.konnektivitet` (K21) i en K20-fråga; exempel i `exempel/fragor/demo_ekologi.toml`.
- MCP: inget eget verktyg (de andra analyserna har inget). Frågan körs via `kor` och demons mall `ekologi`.

## Nästa steg

Se `PLAN.md`: inläsning av NMD som markunderlag och en R2-rapport för ekologi (båda ej gjorda).
