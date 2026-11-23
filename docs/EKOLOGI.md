# Ekologisk konnektivitet med habitatnätverk (skiss)

Ekologiläget visar hur ett förslag, till exempel ett nytt hus, ändrar möjligheten för ett **habitatnätverk** att hänga ihop.
Kommunala rapporter redovisar aggregerade samband, till exempel barrskogssamband och ädellövsamband, med signalarter. Floden
arbetar på samma sätt och har därför nätverk i stället för fria artprofiler. Varje parameter har en källa ur
källsammanställningen [`docs/ekologi/habitatnatverk-svensk-stadsmiljo.md`](ekologi/habitatnatverk-svensk-stadsmiljo.md)
(källnyckel och sida eller tabell). Kontrakten är K44 (artprofil, föråldrad), K45 (lösaren, `stromtathet.tif`, `ekologi.json`,
webb-API) och **K46** (habitatnätverk) i `docs/DATAKONTRAKT.md`.

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

Förval per kommun står i `data/ekologi/kommuner.toml`: Stockholm kör Stockholms nätverk lokalt och Nackas NMD-nätverk på servern,
Göteborg kör Nackas NMD-nätverk med Göteborg 2023:11 som lokal kvalitetskälla för mindre hackspett.

Varje värde har `kalla`, `sida`, `typ` (uppmätt, litteratur, modell, expertbedömning eller **antagande**) och `sakerhet`. Det som
saknas i källan står som `[[saknas]]` eller som ett uttryckligt antagande. Exempel som är antaganden i Floden och inte värden ur
källan:

- **Habitat i NMD-klasser** för barrskog- och ädellövsambanden. Nacka anger NMD bara för motståndet; habitatet byggs i källan ur
  skogsskikt, naturvärden och ortofoto. Floden använder barrdominerade klasser (111–113, 121–123) respektive 116–117 som ersättare,
  så resultatet visar hur de klasserna hänger ihop och inte gammal skog.
- **d50 = tröskel/2** där källan bara anger en tröskel (se nedan).
- **Motstånd för NMD 126–127** i ädellövsambandet (saknas i tabellen; antagande 2, som 124–125).
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
- **Habitat ur NMD är en ersättare** (ingen ålder, volym eller naturvärde). Nackas fulla urval går inte att återskapa med öppen data.
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
- Lösare: `ekologi.konnektivitet` (K21) med parametern `natverk` och indata `nmd`; exempel i `exempel/fragor/demo_ekologi.toml`.

## Nästa steg

Se `PLAN.md`: köra NMD- och VMI-inläsarna mot riktiga filer på servern, R2-rapport för ekologi med nätverkens källrad och
antaganden, lokal körning av Stockholms nätverk mot stadens biotopdatabas.
