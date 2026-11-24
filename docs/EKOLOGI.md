# Ekologisk konnektivitet med habitatnätverk (skiss)

Ekologiläget visar hur ett förslag, till exempel ett nytt hus, ändrar möjligheten för ett **habitatnätverk** att hänga ihop.
Kommunala rapporter redovisar aggregerade samband, till exempel barrskogssamband och ädellövsamband, med signalarter. Floden
arbetar på samma sätt och har därför nätverk i stället för fria artprofiler. Varje parameter har en källa ur
källsammanställningen [`docs/ekologi/habitatnatverk-svensk-stadsmiljo.md`](ekologi/habitatnatverk-svensk-stadsmiljo.md)
(källnyckel och sida eller tabell). Kontrakten är K44 (artprofil, föråldrad), K45 (lösaren, `stromtathet.tif`, `ekologi.json`,
webb-API) och **K47** (habitatnätverk) i `docs/DATAKONTRAKT.md`.

**Metodstatus: `skiss`. Parametrar källbelagda (kommunala modellval), biologisk säkerhet låg.** Källorna är kommunala
modellval (expertbedömningar och litteratur), inte fältkalibrerade. Sammanställningen anger för samtliga nätverk att en visad
minsta fläckarea som säkerställer en långsiktigt livskraftig population och en fältkalibrerad sannolikhet för varje marktyps
passage saknas.

## Nätverken

Varje nätverk är en TOML-fil i `data/ekologi/natverk/`, en per nätverk och källa. Källnycklar finns i `data/ekologi/kallor.toml`.

| Id | Namn i webben | Signalart | Källa | Körs |
| --- | --- | --- | --- | --- |
| `barrskog-nacka` | Barrskogssamband (Nacka/NMD) | tofsmes, talltita | Nacka 2020, bil. 2 s. 3–6, tab. 2 | server (NMD) |
| `adellov-nacka` | Ädellövsamband (Nacka/NMD) | stjärtmes m.fl. | Nacka 2020, bil. 2 s. 1–3, 7, tab. 1 | server (NMD) |
| `mindre-hackspett-nacka` | Mindre hackspett (Nacka/NMD) | mindre hackspett | Nacka 2020, bil. 1 s. 16, tab. 2 | server (NMD) |
| `grongoling-nacka` | Gröngöling (Nacka/NMD) | gröngöling | Nacka 2020, bil. 1 s. 20, tab. 4 | server (NMD) |
| `barrskog-stockholm-2022`, `adellov-ek-stockholm-2022`, `groddjur-stockholm-2022` | Stockholm 2022 (lokalt) | tofsmes m.fl.; ekinsekter; vanlig padda | Stockholm 2023, tab. 5–6, bil. 4 | lokalt (stadens biotopdatabas) |
| `tofsmes-`, `kungsfagel-`, `brun-guldbagge-`, `storre-vattensalamander-sodertalje` | Södertälje 2020 | | Södertälje 2020 | endast d50 (inget motstånd i källan) |
| `mindre-hackspett-goteborg-2023` | Göteborg 2023:11 | mindre hackspett | Göteborg 2023 | kvalitetskälla (ingen friktion i källan) |

Förval per kommun står i avsnittet `[ekologi]` i kommunens egen fil, `data/kommuner/<kommun>.toml` (kontrakt `floden-kommun/v1`;
`src/kommuner/konfig.py` validerar det strikt, och `ekologi.kommunforval()` kontrollerar nätverks-id mot nätverksfilerna). Stockholm
kör Stockholms nätverk lokalt och Nackas NMD-nätverk på servern, Göteborg kör Nackas NMD-nätverk med Göteborg 2023:11 som lokal
kvalitetskälla för mindre hackspett. Avsnittet är valfritt: en kommun utan det har inget förval. En ny kommun får sitt förval genom att
kommunfilen får ett `[ekologi]`-avsnitt (`forval_server`, `forval_lokalt`, `[ekologi.kvalitetskallor]`, `anmarkning`); den gamla filen
`data/ekologi/kommuner.toml` är borttagen.

Varje värde har `kalla`, `sida`, `typ` (uppmätt, litteratur, modell, expertbedömning eller **antagande**) och `sakerhet`. Det som
saknas i källan står som `[[saknas]]` eller som ett uttryckligt antagande. Exempel som är antaganden i Floden och inte värden ur
källan:

- **Habitat i NMD-klasser** för barrskog- och ädellövsambanden (uppdaterat efter Codex jämförelse åt Joel,
  `docs/ekologi/habitatnatverk-svensk-stadsmiljo.md`, avsnittet "Vilka NMD-klasser kan användas som habitat i Nackas två
  skogsnätverk?"). Nacka anger NMD bara för motståndet; habitatet byggs i källan ur skogsskikt, naturvärden och ortofoto.
  Översättningen till NMD-rastret är märkt `antagande`. Habitatkandidater: **barrskog 111–114 och 121–124** (Stockholms län,
  bilaga 1&2, PDF-s. 4–5, stöds av Botkyrka tab. 1); **ädellöv 116, 117, 126 och 127** (Stockholms län PDF-s. 7, där 126
  uttryckligen räknas som ädellöv; stöds av Botkyrka tab. 2). Det snävare urvalet **111–113 och 121–123** (Umeå s. 11) körs
  som jämförelsescenario ("snävt") vid huvudscenariots tröskel och redovisas bredvid kandidaterna.
- **Resultatet kallas "potentiell livsmiljö, okänd kvalitet"** (webb, API och `ekologi.json`, fältet `resultat_etikett`) eftersom
  ålder, naturvärde och trädinventering saknas. Det visar hur marktyperna hänger ihop, inte gammal skog.
- **Valfritt lager för inventerade gamla ädellövträd** (punkter som blir noder även på öppen mark): byggt, se avsnittet
  "Valfritt trädlager" nedan. Buffert 20 m är Nackas värde (litteratur); trädklass 116, undantaget från minsta area och artlistan
  är antaganden i Floden.
- **d50 = tröskel/2** där källan bara anger en tröskel (se nedan).
- **Motstånd för NMD 126–127** i ädellövsambandet (saknas i Nackas tabell 1; eget modellval, typ `antagande`, 2 som 124–125). Redovisas separat i resultatet (`natverk.antagna_motstand`).
- **Minsta area** för groddjursnätverket (saknas i källan; varje sammanhängande habitatyta räknas).

Stockholms nätverk bygger på stadens biotopdatabas, som inte är öppen. Biotopklasserna kan inte översättas rakt av till NMD (NMD 42
innehåller till exempel mer än gräsmatta, och stadens skikt skiljer mellan vägyta i grundkartan och större vägar som läggs ovanpå).
De körs bara lokalt, med indata `biotopraster` och parametern `biotop_klasskoder`.

## Metod

1. **Mark.** NMD-nätverk läser ett NMD-lager (`nmd.tif`, 10 m, EPSG:3011, kärna plus 2–5 km kontextbuffert) som indata `nmd`
   (`data_inlasning/nmd.py`, se `docs/DRIFT.md`). Byggnader ur K1 efter K9, alltså även ritade hus, läggs ovanpå som **NMD 51**.
   Rivna hus tas inte bort ur NMD. Artprofiler (K44, föråldrade) använder K1-rutor och `data/ekologi/markslag.toml` som förut.
2. **Nätverket** ger habitatklasser, minsta area, avståndsscenarier och motstånd per klass (`ekologi.natverk.till_artprofil`).
3. **Habitatfläckar, kretsteori, PC, ECA och dPC** som förut (McRae m.fl. 2008; Saura och Pascual-Hortal 2007; Saura och Rubio
   2010): `src/ekologi/`. Strömtäthet J (flaskhalsar) räknas för huvudscenariot.
4. **Spridningsfunktion: p(d) = 2^(−d/d50)** i kostnadsavstånd (effektiva meter). Tröskeln är ingen hård avklippning. Anger källan
   bara en tröskel (Nacka, Stockholm) syns det i resultatet: "p saknas i källan; d50 = tröskel/2". Konfigurationen
   (`[d50_antagande]`) anger antagandet; kvoten 0,5 är samma som i Södertälje (d50 1 250/2 500, 750/1 500, 500/1 000). Södertäljes
   d50 är lagda in som egna nätverk men går inte att köra, eftersom huvudrapporten saknar motståndslistan.
5. **Före och efter.** Samma körning för bas och för bas plus K9. ΔECA = ECA efter − ECA före.

## Valfritt trädlager (inventerade ädellövträd)

Ett valfritt lager för ädellövsambandet (`adellov-nacka`, `[[valfritt_lager]]` med `status = "byggt"`): inventerade gamla
ädellövträd som punkter blir habitatnoder **även på öppen mark** (park, hage, gatuträd), där NMD inte ser dem som skog. Inga
träddata följer med Floden; filen hämtar du själv. Utan lagret är körningen oförändrad.

**Konfiguration** (allt i `data/ekologi/natverk/adellov-nacka.toml`, varje värde med källa, typ och säkerhet):

| Avsnitt | Värde | Belägg |
| --- | --- | --- |
| `[valfritt_lager.buffert]` | `varde_m = 20`: radien i m runt trädpunkten | Nacka 2020: "punktträd buffrades 20 m" (litteratur, måttlig) |
| `[valfritt_lager.nod]` | `klass = 116` (ädellövskog, en av nätverkets habitatklasser), `undantar_min_area = true`, `ej_pa_klasser = [51, 52, 53, 61, 62]` | **antagande** (låg): källan anger bara bufferten och att färdiga fläckar är minst 0,25 ha |
| `[valfritt_lager.filter]` | `tradslag` (ek, bok, ask, alm, lönn, lind, avenbok med vanliga sammansättningar och latinska släktnamn); `min_stamomkrets_cm` och `min_diameter_cm` är valfria och **inte satta** | **antagande** (låg): ingen artlista och ingen gräns för stamomkrets i källan |
| `[valfritt_lager.falt]` | kandidater för fältnamn (trädslag, stamomkrets, diameter) och enhet (cm, m eller mm) | teknisk inställning |

**Så blir ett träd en nod.** En skiva med radien `buffert_m` läggs i klassrastret (NMD 10 m) som klass 116, utom på byggnad (51),
annan exploaterad yta (52), väg (53) och vatten (61, 62); cellen med själva punkten tas alltid med om den får bli nod. Ritade hus
(K9) ligger redan som 51 och tar därför bort trädets skiva där de står, så ett nytt hus kan minska nodens yta. Skivan bildar en
fläck med angränsande habitat i rastret. Fläckar som innehåller en skiva undantas från minsta area (0,25 ha): en 20 m-skiva är
cirka 1 250 m² och skulle annars aldrig bli en nod. Träd som inte ger några celler (bara hårdgjort runt) eller ligger utanför
markunderlaget räknas och redovisas som varning. Snävt urval (jämförelsen 111–113/121–123) innehåller inte klass 116 och får därför
inga trädnoder.

**Inläsning och dataminimering.** GeoPackage, Shapefile eller GeoJSON (valfritt CRS, omprojiceras till EPSG:3011); `Point` krävs,
andra geometrier avvisas och räknas. Filen läses med en kolumnlista (`columns=`), så **bara punkt, trädslag och stamomkrets/diameter
(cm)** läses och sparas; adresser, fastighetsuppgifter och inventerarens namn läses aldrig. Saknas fältet för trädslag är det ett fel
(med filens fältnamn i texten). Urvalet matchar trädslag mot helt namn eller ett ord i namnet (skiftlägesokänsligt). Är en måttgräns
satt och trädet saknar måttet (omkrets och diameter ersätter varandra via pi) sorteras det bort och antalet redovisas, det
antas aldrig. Källfilens sha256 ingår i cachenyckeln; filen läggs inte i git.

**Två vägar in.**

- Indata `tradpunkter` (en fil) tillsammans med parametern `anvand_trad = true`, till exempel
  `parametrar = { natverk = "adellov-nacka", anvand_trad = true }` och `indata = { nmd = "...", tradpunkter = "...gpkg" }`. En
  `tradpunkter.csv` (de fem kolumnerna nedan) läses också.
- **Geografins manifest (K46):** `floden geografi mappa <geografi-id> ... --trad <fil>` (valfritt `--trad-natverk` för att välja vilket
  nätverks fältnamn som gäller, förval `adellov-nacka`) klipper punkterna till kärnan plus kontextbufferten och sparar lagret
  `lager.tradpunkter` (`tradpunkter.csv` med `e_m`, `n_m`, `tradslag`, `stamomkrets_cm`, `diameter_cm`, och `tradpunkter.json`).
  Lösaren läser det med indata `geografi` och `anvand_trad = true`. **Öppet eller stängt:** lagret är stängt för MCP tills
  `--villkor-kontrollerade` anges (som VMI), eftersom trädfilens licens inte är känd av Floden; NMD är CC0 och öppet. Filer ur
  stadens egna inventeringar bör behandlas som stängda.

**Redovisning.** `ekologi.json` får `natverk.tradlager` (id, ursprung med sha256, antal i filen, i utsnittet och efter urval,
bortfiltrerade, antal utan nod, antal celler, buffert, klass, urval, belägg per värde och antaganden), `valfria_lager[].anvands`
och antagandena i `natverk.antaganden`. Resultatet heter fortfarande "potentiell livsmiljö, okänd kvalitet": trädens ålder och
hålighet ingår inte. **Begränsningar:** NMD-cellen är 10 m, så skivan är en grov bild av 20 m; trädslagslistan och trädklassen är
Flodens val; ingen riktig trädinventering har körts (testad med påhittade punkter).

## Intervall: källbaserat

Intervallet [ECA_min, ECA_max] är **lägsta och högsta värde mellan källor och scenarier för samma nätverk**, till exempel
tröskelscenarierna 1 000 och 3 000 effektiva m i Nacka. Det ersätter faktorerna ×0,7/×1,4 (de gäller bara artprofiler). ECA och PC är
icke-avtagande i d50, så för scenarier som bara skiljer i d50 ligger min och max vid minsta respektive största d50 (monotonin G6).
Har källan bara ett scenario (mindre hackspett, gröngöling) är intervallet en punkt, och det står i antagandena. Intervallet säger
inget om osäkerhet i klassningen av marken, rastreringen eller de biologiska parametrarna.

## Ändligt motstånd, inga totala barriärer som förval

Sammanställningen finner inget empiriskt stöd för totala barriärer. Källornas ändliga motstånd används: byggnad 100
(mindre hackspett och barrskog, Nacka), 40 (ädellöv, Nacka); Stockholm långdistans 200 (barrskog och ek) och 2 000 (groddjur).
Oändligt motstånd kan bara sättas uttryckligen i en nätverksfil (`oandligt = true` med `motivering`); ingen fil i repot gör det.
Ett hus med ändligt högt motstånd minskar ECA (`tests/ekologi/test_natverk.py`). Facit G1–G7 gäller oförändrade.

## VMI: känd våtmark (äldre)

Våtmarksinventeringen (Naturvårdsverket, inventerad 1981–2005) läses som lagret `kand_vatmark` (`data_inlasning/vmi.py`,
märkt som äldre). Det visas och används som kvalitetsinformation, och med parametern `anvand_vmi` kan det väljas som habitat för
nätverk med `vmi_klass` (groddjur, lokalt). Lagret kan vara inaktuellt.

## Begränsningar

- **Skiss.** Kommunala modellval; biologisk säkerhet låg. Resultatet visar hur just nätverket reagerar, inte vad en art gör.
- **Habitat ur NMD är potentiell livsmiljö, okänd kvalitet** (ingen ålder, volym eller naturvärde). Nackas fulla urval går inte att återskapa med öppen data.
- **Tröskeln 3 000 m** för mindre hackspett och gröngöling är "fågelväg" i artmetoden och "kostnadsviktat" i översikten; Floden
  tolkar den som effektiva meter. Osäkert.
- **Födosöksgränser** (500 effektiva m) och arealkrav utöver minsta area ingår inte.
- **Rastrering.** NMD är 10 m; hus och smala hinder kan försvinna eller bli för breda.
- **Inte verifierat mot fältdata**, inte jämfört med Nackas eller Stockholms egna körningar, och NMD och VMI är inte hämtade i
  den här miljön (inläsarna är testade mot syntetiska raster och vektorer). Demokvarteret och dess NMD-lager är påhittade.
- **Artskydd.** Inga artfynd används; skyddsklassade fynd får aldrig förekomma i indata, resultat eller rapporter.

## Användning

- Webben: steg 4 Resultat, fliken Ekologi: välj habitatnätverk ("Barrskogssamband (Nacka/NMD)"); panelen visar signalart och
  källa på en rad, antaganden och intervallets typ.
- API: `POST /api/analys/ekologi` med `natverk`; `GET /api/ekologi/natverk` listar nätverk och kommunförval.
- Lösare: `ekologi.konnektivitet` (K21) med parametern `natverk`, valfritt `anvand_trad` (trädlagret ovan) och markunderlag på ett av två sätt (aldrig båda):
  - indata `nmd` (en NMD-fil; `exempel/fragor/demo_ekologi.toml`), eller
  - indata `geografi = ".../<geo-rot>/<id>/<version>/manifest.json"` (K46): lösaren läser `lager.nmd` (och `lager.vmi` med
    `anvand_vmi`), verifierar lagrens sha256, klipper ut rutorna plus kontextbuffert (nätverkets längsta tröskel, 2-5 km, aldrig
    utanför lagret) och lägger K9-hus ovanpå som NMD 51. Är geografins buffert kortare än behovet, eller täcker lagret inte
    bufferten, varnar lösaren. Markunderlagets ursprung (`ref`, manifestets och lagrens sha256, klippt utsnitt) skrivs i
    `ekologi.json` under `natverk.klassraster.markunderlag`. Samma NMD ger samma ECA som med rollen `nmd` (testat). Höj `max_celler`
    och `max_flackar` för en stor buffert.
- Rapport: `ekologi` är ett skissunderlag (R2) med frågorna EK-01 till EK-15; se `docs/RAPPORTER.md`, avsnittet om ekologi.
  Exempel: `docs/exempel/skissunderlag_ekologi_demo.pdf`.

## Nästa steg

Se `PLAN.md`: köra NMD- och VMI-inläsarna (och geografivägen) mot riktiga filer på servern, lokal körning av Stockholms nätverk mot stadens biotopdatabas.
