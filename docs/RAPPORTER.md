# Rapporter: påståenden med belägg, kontroll och export (R1)

**Floden räknar, AI:n ställer frågor, väljer körningar och skriver – men äger aldrig ett tal.**
En rapport är en lista *påståenden* (K32) som var och en besvarar en fråga ur frågekatalogen och
är bunden till *belägg* ur Flodens körningar. Talen i texten är typade platshållare som Floden
fyller. Före export körs en deterministisk kontroll; ett enda fel stoppar exporten. Beslutat av
Joel 2026-11-22 (se `BESLUT.md`). Kod: `src/rapport/` (`pastaende`, `belaggskalla`,
`fragekatalog`, `skissrapport`, `skisskontroll`, `skissmall`, `skissexport`, `referensflode`).
Kontrakt: K32 och K33 i `DATAKONTRAKT.md`.

## Två nivåer

| Nivå | Vad | Status |
| --- | --- | --- |
| 1 Skissunderlag | Sållning, inte bedömning mot krav. 2–6 sidor: karta per ämne (före, efter, skillnad), jämförelse av alternativ, trafikljus per fråga ("behöver fördjupas"), saknat underlag, alla metoder med valideringsstatus. Ansvarig utredare valfri. | Finns för **skyfall** (R1) |
| 2 Publicerbart planunderlag | Bedömning mot bedömningsgrunder, metodnotis och ansvarig utredare, granskning och signering. | R3; fälten finns förberedda i K32/K33 (`bedomningsgrund`, `granskning`, `signering`, `skede`, `revision`) |

Ordning för ämnena (Joel 2026-11-22): skyfall → dagsljus/sol → luft → buller.

## Påstående, belägg och platshållare

Ett påstående har fråge-id (`SK-06`), status, fördjupningsbehov, text och belägg. AI:n (eller
skribenten) anger beläggets **adress**; Floden hämtar värdet och allt som behövs för att spåra och
tolka det ur samma fasad som MCP-servern och webben (`FlodenTjanst`; ingen ny räkneväg):

| Adress | Källa |
| --- | --- |
| `jamforelse.<namn>.<mått>` (`max_okning`, `max_minskning`, `yta_okning_m2`, `yta_minskning_m2`) | `FlodenTjanst.jamfor` = `FragaTjanst.marksiffror`: mark, byggnadernas tak undantagna med webbens takmask. Jämförelser utan takmask vägras. |
| `matvarde.<analys>.<scenario>[.<ruta>].<namn>` | Jobbets K0-manifest (`resultat_sammanfattning`): `max_djup_m`, `lagrat_m3`, `ut_m3`, `regn_mm`, `aterkomsttid_ar`, `varaktighet_min`, `klimatfaktor`, `avtappning_mm_h`, `takvatten_andel_ledning`, `volymfel_rel`, `takvatten_takyta_m2` |
| `omrade.<mottagare>.<lager_id>.<statistik>` (`min`, `medel`, `median`, `max`, `yta_okning_m2`, `yta_minskning_m2`) | `FlodenTjanst.resultat_varde` med rapportens mottagare (polygon); tak uteslutna |

Belägget får körnings-id, resultatfil och dess sha256, K0-manifest och dess sha256, mottagare,
nämnare, mått, storhet, enhet, tidsdefinition, statistik, värdet **före avrundning**, upplösning
och en hash av analysens parametrar (jämförbarhet). Ett mått utan känd tidsdefinition och
statistik (`MATTKATALOG`) vägras.

Text: tal skrivs bara som `{{matt:<adress>}}`, med valfria optioner `avrundning=10` (1, 2 eller 5
gånger en tiopotens, aldrig finare än underlagets upplösning, t.ex. cellens yta för ytmått),
`enhet=nej`, `enhet=<enhet>` (kontrolleras mot belägget), `tecken=ja` och `format=exp`. Utan
`avrundning` väljer Floden precision efter enhet och storlek (m: 0,01; m²/m³: 1, eller 10 från
1 000). Svensk sifferform: decimalkomma, smalt hårt mellanslag (U+202F) som tusentalsavgränsare
från fyra siffror ("7 430 m³") och hårt blanksteg före enheten. En siffra utanför en platshållare vägras (beteckningar som `SK-06`, `RH 2000`,
`EPSG:3011`, `K7` och `Stockholmsregn 2024 (Tabell 7)` är tillåtna).

## Status och trafikljus

| Status | Krav |
| --- | --- |
| visat | minst ett belägg och minst ett tal i texten |
| avvikelse | belägg; fördjupning `ja` |
| villkorat | `forutsattning` och `ansvarig_funktion` |
| underlag saknas | `saknat_underlag` (vad som saknas); fördjupning inte `nej` |
| ej tillämpligt | `motivering`; fördjupning `nej` |

Trafikljuset svarar på "behöver frågan fördjupas?": grönt `nej`, gult `ja`, rött
`kan_inte_avgoras`. En fråga med flera påståenden får den allvarligaste statusen och det
allvarligaste ljuset. Varje fråga i rapporten måste ha minst ett påstående före export.

## Kontroll inför export

Deterministisk, enligt tabellen i `utredningar-stockholm/rapportstod.md`. Ett fel stoppar
exporten (`ExportStoppad`) med ett meddelande per fel; inget skrivs.

| Punkt | Vad som kontrolleras med dagens data |
| --- | --- |
| Spåra tal och figurer | Varje belägg hämtas på nytt: samma värde, samma sha256 för resultatfil och K0-manifest. Inga siffror utanför platshållare. Kartorna ritas om ur körningen och ska ge samma PNG. |
| Scenario och regelår | Före och efter (och belägg i samma påstående) har samma analysparametrar (regn, avtappning, takvatten); ingen bedömning mot regel i nivå 1. |
| Mottagare och nämnare | Ett påstående gäller en nämnare (mark i hela området, en namngiven mottagare, en körning); områdesbelägg pekar på rapportens mottagare. |
| Mått och tidsstatistik | Storhet, tidsdefinition, statistik, upplösning och enhet finns; platshållarens enhet och avrundning följer belägget; ett maxdjup kallas inte vattennivå, ett största värde inte medelvärde. |
| Osäkerhet | Antagandena (regn, takvatten, avtappning) står i rapporten; resultat från skissmetoder kallas inte verifierade eller säkerställda. |
| Kartan | Teckenförklaring och skala, enhet, tomvärde i bildtexten, norr uppåt, skalstock, scenario i bildtexten, samma färgskala före och efter. |
| Status per fråga | Statusreglerna; varje fråga har påstående; nivå 1 innehåller inga krav- eller lämplighetsformuleringar ("uppfyller kravet", "marken är lämplig"). |
| Dataskydd | Rapportens dataklass är `oppet` (projektets klass i policyn); inga e-postadresser eller personnummer. |
| Metodstatus | Varje lösare har sin valideringsstatus i rapporten, oförändrad, och den syns i dokumentet; varje påståendes text står oförändrad i dokumentet. |

Det inplanterade felet i testerna (`tests/rapport/test_skissrapport.py`) ändrar ett lagrat tal i
`rapport.json` med 10 %; exporten stoppas med "talet för jamforelse.… stämmer inte med körningen
demo-vatten: rapporten har …, Floden ger …". `python tools/rapport_demo.py --inplantera-fel`
visar samma sak.

## Skissunderlaget för skyfall

Disposition (förkortad ur `utredningar-stockholm/rapportdisposition.md`): titelhuvud (avsändare,
eventuell logotyp, dokumenttyp "Skissunderlag", datum, diarienummer, handläggare), titel och
handling (underlag, frågekatalogens källäge, uppgifternas klass, ansvarig utredare, kontroll,
rapport-id), Sammanfattning med trafikljus per fråga, Uppdrag och beräkningsfall (antaganden,
plats), Resultat (kartor före, efter och skillnad i samma utsnitt och färgskala; resultat per
fråga), Saknat underlag och villkor, Metod och osäkerhet (metodnotis, metoder med
valideringsstatus). Bilagor: 1 Belägg (värde före avrundning, fil, sha256), 2 Körlogg (rapportens
händelser och MCP-körloggens rader för rapporten och dess körningar, hashkedjan), 3 Manifest
(K0-parametrar per jobb, metodernas beteckning och status), 4 Kontroll. Rubrikerna är utan nummer
och bildtexterna utan "Figur N" som förval; texten hänvisar till "kartorna nedan". Bilagorna
numreras (Bilaga 1, 2 …) eftersom texten hänvisar till dem; belägg-id (B1 …) behålls för
spårbarheten.

## Inställningar: språkprofil och layout

Inställningarna lagras i rapporten (K33, fältet `installningar`), så att samma rapport alltid ger
samma fil. Fältet är valfritt; en äldre rapport utan fältet får förvalen.

| Inställning | Förval | Vad |
| --- | --- | --- |
| `sprakprofil` | `"sbk"` | `"sbk"`: utredningstext från stadsbyggnadskontoret (se nedan). `"neutral"`: tonen i R1. |
| `numrerade_rubriker` | `false` | `true` ger "1 Sammanfattning", "2 Uppdrag …" |
| `numrerade_figurer` | `false` | `true` ger "Figur 1. …" i bildtexterna |
| `avsandare` | `"Stadsbyggnadskontoret"` | i titelhuvud, sidhuvud och dokumentets författare; tom sträng = ingen avsändare |
| `diarienummer` | `""` | i titelhuvud och sidhuvud |
| `handlaggare` | `""` | i titelhuvudet. Inga riktiga namn i exempel; skriv "Anges av beställaren" eller lämna tomt |
| `accentfarg` | `"#000000"` | linjen under titelhuvudet (rubrikerna är alltid svarta) |
| `logotyp` | ingen | lokal PNG/JPEG; ingen logotyp om ingen anges |
| `typsnitt_brodtext`, `typsnitt_rubriker` | Liberation Sans (PDF), Arial (Word) | lokala TTF/OTF-filer, t.ex. Stockholm Type |

**Filer.** Logotyp och typsnitt anges med lokal sökväg. Floden kontrollerar filen och lägger en
kopia i rapportens mapp (`resurser/<sha256>.<ändelse>`, under `data/lokal/`, utanför git).
Rapporten lagrar bara sha256, filtyp och typsnittets familjenamn – **aldrig sökvägen**, och
sökvägen står inte i felmeddelanden, i körloggen eller i Word- och PDF-filen. Stadens logotyp och
Stockholm Type är skyddade och checkas aldrig in. Saknas kopian vid export letar Floden efter en
fil med samma sha256 bland sökvägarna i `config.local.toml`; annars stoppas exporten med ett
tydligt fel. Typsnittet bäddas in i PDF:en (reportlab klarar TTF och OTF med TrueType-konturer;
OTF med PostScript-konturer vägras med ett meddelande) och dess familjenamn och sha256 står i
PDF:ens metadata (nyckelord), i Word-filens nyckelord och i rapportens exportpost
(`exporter[].typsnitt`). Word bäddar inte in typsnitt: Word-filen anger familjenamnet, och
typsnittet måste finnas på datorn som öppnar filen. Har typsnittet ingen kursiv används den
raka varianten i bildtexterna.

**Sätta inställningarna.**

- Python: `RapportTjanst.skapa(..., installningar={"diarienummer": "…"})` och
  `RapportTjanst.andra_installningar(rapport_id, {"numrerade_rubriker": True})` (nollställer
  kontrollen och loggar händelsen `installningar_andrade` med de ändrade nycklarna).
- MCP: `rapport_skapa(installningar={…})` och `rapport_installningar(rapport_id, andringar={…})`.
- Referensflödet: `tools/rapport_demo.py --sprakprofil neutral --numrerade-rubriker
  --numrerade-figurer --avsandare "" --diarienummer … --handlaggare … --accentfarg … --logotyp FIL
  --typsnitt-brodtext FIL --typsnitt-rubriker FIL`.
- Lokala förval: `[rapport]` i `config.local.toml` (versionshanteras inte). Relativa sökvägar
  räknas från Flodens rot. Förvalen gäller när en rapport skapas och lagras sedan i rapporten.

```toml
# config.local.toml (inte i git)
[rapport]
sprakprofil = "sbk"
numrerade_rubriker = false
numrerade_figurer = false
avsandare = "Stadsbyggnadskontoret"
handlaggare = "Anges av beställaren"
accentfarg = "#000000"
logotyp = "/Users/<du>/Typsnitt och logotyper/logotyp.png"
typsnitt_brodtext = "/Users/<du>/Library/Fonts/StockholmType-Regular.ttf"
typsnitt_rubriker = "/Users/<du>/Library/Fonts/StockholmType-Bold.ttf"
```

**Det gamla beteendet (R1)** fås med `sprakprofil = "neutral"`, `numrerade_rubriker = true` och
`numrerade_figurer = true`: numrerade rubriker, "Figur N." och bilagor med bokstäver (A–D).
Layouten (titelhuvud, luft, "Sida 2 (9)") är densamma i båda profilerna.

### Språkprofilen "sbk"

Skriver som en utredningstext eller planbeskrivning från stadsbyggnadskontoret: klarspråk,
sakligt, opersonligt och bestämt, korta meningar och slutsatsen först ("Av de 15
skyfallsfrågorna behöver 6 utredas vidare …"). Formuleringar som "Beräkningen visar att …",
"Frågan behöver utredas vidare i planarbetet" och "Underlag saknas för att bedöma …". Texten säger
**aldrig** att förvaltningen har tagit ställning ("Stadsbyggnadskontoret bedömer"), eftersom
rapporten är ett beräkningsunderlag. Ursprunget står i metodavsnittet: framtaget med Floden,
vilka beräkningar och om AI har använts. Scenarier och statusar skrivs i klarspråk ("före
förslaget", "Besvaras av beräkningen", "Kräver ställningstagande", "Inte aktuell i skedet",
trafikljusen "Utreds vidare", "Kan inte bedömas", "Ingen fördjupning"); tekniska beteckningar
(lösare, adresser, körnings-id i detalj) står bara i bilagorna. Datum skrivs "22 november 2026".
Mallens text finns i `rapport.skissmall` och `rapport.sprak`, referensflödets påståenden i
`rapport.referensflode` (båda profilerna). Påståenden skrivs i den profil som gällde när de lades
till; att byta profil senare ändrar mallens text, inte redan tillagda påståenden. Talen fylls
alltid av Floden via `{{matt:…}}`, och kontrollen är densamma i båda profilerna (bildtexten
kontrolleras både som Floden lagrade den och som den står i dokumentet). Språktestet
(`tests/rapport/test_rapportstil.py`) letar efter förbjudna formuleringar och engelska ord i
löptexten.

Före och efter (sbk):

| neutral (R1) | sbk |
| --- | --- |
| Sållningen omfattar 15 frågor ur frågekatalogen. 6 behöver fördjupas, 4 kan inte avgöras med dagens underlag … | Av de 15 skyfallsfrågorna behöver 6 utredas vidare och 4 kan inte bedömas med nuvarande underlag. |
| Körning demo-vatten: … Scenarier: fore, efter. efter jämförs med fore (skyfall, max_djup). | Läget efter förslaget jämförs med läget före förslaget, med samma regn. |
| Ledningsnätet antas fullt under skyfallet: avtappningen är 0,0 mm/h. | Beräkningen förutsätter att ledningsnätet är fullt under skyfallet. Avtappningen till nätet är 0,0 mm/h. |
| Största vattendjup över mark, före (scenario fore), i m. | Största vattendjup på marken före förslaget, i meter (m). |
| Status: Underlag saknas · Fördjupning: Kan inte avgöras | Underlag saknas. Kan inte bedömas med nuvarande underlag. |

Kartorna ritas av MCP:s kartfunktion (`FlodenTjanst.karta`, samma raster, färgskala och takmask
som webben): vit mark = under lägsta klassen, ljusgrått = tak eller inget värde, så att saknade
celler inte ser torra ut. Rapportens kartor har rubrik i klarspråk ("Största vattendjup före
förslaget", lagrad i `meta.rubrik` så att kontrollen ritar om samma bild) och decimalkomma i
teckenförklaringen; axeletiketter som skulle krocka i nedre vänstra hörnet ritas inte. Metodnotisen är förvald: "Underlaget har tagits fram med Floden och
AI-stöd (modell) … Körloggen finns som bilaga." och ansvarig utredare (valfri i nivå 1).

**Export:** Word med python-docx och PDF med reportlab, båda ren Python (`pip install -e
".[rapport]"`, ingår i `dev` och `mcp`), samma block i båda. PDF-vägen valdes för att den saknar
systemberoenden (ingen LibreOffice), fungerar likadant på Mac och har ett medföljande typsnitt
(Liberation Sans 2.1.5, SIL OFL 1.1, i `src/rapport/typsnitt/` med licensen) med svenska tecken,
², ³, − och smalt hårt mellanslag. Liberation Sans har samma teckenbredder som Arial, så PDF och
Word ser likadana ut; `invariant` och fasta tider i
docx-zip gör samma rapport och inställningar till samma fil. Word använder Arial (eller det lokala
typsnittets familjenamn), Words inbyggda rubrikformat (Rubrik 1–3, så att innehållsförteckning
och navigeringsfönstret fungerar), svenska som språk (stavningskontrollen) och sidfoten
"Sida 2 (9)" med fälten PAGE och NUMPAGES. Lokala typsnitt saknar ofta smalt hårt mellanslag; i
PDF:en ritas det därför som ett hårt mellanslag i mindre grad, så att tal aldrig bryts.

**Layout.** Svensk rapportlayout med smal textspalt (130 mm) och hängande rubriker: titel,
huvudrubriker (nivå 1–2), titelhuvud och de breda bilagetabellerna börjar vid vänster marginal
(22 mm), brödtext, bilder, underrubriker och smala tabeller 30 mm längre in. Alla rubriker är
svarta och fetstilta, utan nummer som förval.

## Frågekatalogen: vad Floden kan belägga för skyfall

Katalogen är `utredningar-stockholm/fragor.json` (75 frågor, källäge 21 november 2026,
sammanställd med AI-stöd och ska granskas). Tabellen skrivs ur `rapport.fragekatalog.FLODEN_FORMAGA`
(testet kontrollerar att den stämmer). "Status i demon" är referensflödets status; ingen fråga får
status *visat* utan belägg, det avgörs av påståendets egna belägg.

| Fråga | Floden i dag | Belägg Floden kan ge | Saknas och varför | Status i demon |
| --- | --- | --- | --- | --- |
| SK-01 Vilken översvämningsrisk avgör markens lämplighet? | delvis | Största vattendjup före och efter på mark (karta och tal). | Markanvändning, känsliga byggnader, entréer och riskkällor finns inte i Floden. | underlag saknas |
| SK-02 Vilket dimensionerande regn och vilken säkerhetsnivå krävs här? | delvis | Det regn som körningen använde: källa, återkomsttid, varaktighet, klimatfaktor, mängd. | Vilken säkerhetsnivå som krävs är ett beslut, inte en beräkning (villkorat). | villkorat |
| SK-03 Varifrån kommer vattnet och vart fortsätter det efter plangränsen? | delvis | Utflöde över beräkningsområdets kant och stående vatten. | Tillrinningsområde, plangräns och nedströms mottagare avgränsas inte; randvillkoret är fritt utflöde över rutans kant. | underlag saknas |
| SK-04 Vilka höjder och öppningar styr vattenvägen? | underlag saknas | – | Terrängens version finns i rutans recept (sha256) men inga mått per höjd, öppning eller tröskel; objekten behöver kontrolleras på plats. | underlag saknas |
| SK-05 Hur representeras förluster, nät och initialtillstånd? | kan belägga | Avtappning till ledningsnätet och takvatten som de räknades (statisk fyllning har ingen infiltration och inget förlopp). | Markfukt, igensättning och upptryckning modelleras inte. | visat |
| SK-06 Vilken skillnad beror på exploateringen? | kan belägga | Skillnad före/efter i största vattendjup på mark (takmask som webben), samma regn och område, med differenskarta. | Planförslag med åtgärd körs bara om det finns som eget scenario. | visat |
| SK-07 Ökar skaderisken för en granne eller nedströms funktion? | delvis | Stående vattenvolym och utflöde före/efter, djup i en namngiven mottagare (polygon). | Flöde och varaktighet saknas (statisk fyllning); nedströms mottagare är inte utpekade. | underlag saknas |
| SK-08 När begränsas tillträde, och finns en fungerande alternativ väg? | underlag saknas | – | Framkomlighet ej bedömd: ingen rutt i frågan, fordonsgränsen (K7) är inte fastställd och tidsserie för djup och hastighet saknas i statisk fyllning. | underlag saknas |
| SK-09 Vem kan genomföra och underhålla lösningen? | underlag saknas | – | Genomförande och drift är projektkontroll utanför Floden. | ej tillämpligt |
| SK-10 Vilka slutsatser ändras med ändrad terräng, regn eller avledning? | delvis | Volymbalansens relativa fel (massbalans) per körning. | Känslighetsfall (terräng, regn, avledning) kräver fler körningar; jämförelse mot oberoende underlag finns inte. | underlag saknas |
| SK-11 Är vanlig dagvattenhantering och extremregn båda redovisade? | underlag saknas | – | Dagvattenhantering (vardagsregn, rening) räknas inte i Floden. | ej tillämpligt |
| SK-12 Kan en läsare följa vattennivån från karta till kritisk sektion? | delvis | Maxdjup i meter på karta med teckenförklaring, tomvärde och modellversion. | Vattenyta i RH 2000, sektioner och tidsserie redovisas inte. | underlag saknas |
| SK-13 Fungerar skyfallsåtgärden i hela avrinningsområdet och innan samtliga etapper byggts? | underlag saknas | – | Åtgärder utanför planområdet och etappvis utbyggnad prövas inte i skissen. | ej tillämpligt |
| SK-14 Kan både vägen fram och den avsedda insatsplatsen användas vid relevant tid? | underlag saknas | – | Räddningsväg och insatsplats: fordonsgräns och brandunderlag saknas (K7 tom). | underlag saknas |
| SK-15 Var blir delområdenas resultat en bedömning av hela planområdet? | underlag saknas | – | Sammanvägning av flera utredningar görs inte i en skiss för ett beräkningsområde. | ej tillämpligt |


Floden kan alltså i dag ge belägg för kärnan i **SK-05** (förluster och nät som de räknades) och
**SK-06** (skillnad före/efter på mark), och för delar av SK-01, SK-02, SK-03, SK-07 (volymer och
djup i en namngiven mottagare), SK-10 (massbalans) och SK-12 (maxdjup på karta). SK-04, SK-08 och
SK-14 blir *underlag saknas* (terrängobjekt, rutt och fordonsgräns saknas; framkomlighet ej
bedömd), och SK-09, SK-11, SK-13 och SK-15 gäller projektkontroll eller sammanvägning utanför
Floden.

## Köra

- **Referensflöde utan AI:** `PYTHONPATH=src python tools/rapport_demo.py [--exempel docs/exempel]`
  (kräver `floden demo`). Skapar rapporten i `data/lokal/rapporter/<rapport_id>/`, lägger fasta
  påståenden (`rapport.referensflode`), kontrollerar och exporterar Word och PDF.
- **Via MCP:** verktygen `fragekatalog_sok`, `rapport_skapa`, `pastaende_lagg_till`,
  `rapport_kontrollera`, `rapport_exportera`, `rapport_hamta` och resursen
  `floden://rapport/{rapport_id}`; prompten `skissunderlag_skyfall`. Exempel:
  `PYTHONPATH=src python tools/mcp_rapportflode.py`.
- **Webben:** projektsidan listar rapporterna med kontrollens utfall (grön/röd) och länkar till
  Word och PDF (`GET /api/projekt/<id>/rapporter`, `…/rapporter/<rapport_id>/fil/word|pdf`).
  Rapportmappen kan ändras med `[webb] rapportmapp`.

Exempel: `docs/exempel/skissunderlag_skyfall_demo.pdf` och `.docx` (påhittat demokvarter, förvalen:
språkprofil sbk, inga nummer, svensk sifferform, ingen logotyp, typsnitt Liberation Sans/Arial, svarta rubriker, smal spalt).

## Begränsningar

- Bara skyfall och nivå 1. Kartor ritas för varje jämförelse i körningen (före, efter, skillnad).
- Bara vattenmåtten i `MATTKATALOG` kan användas (statisk fyllning och dynamik).
- Kartornas text är Pillows bitmapptypsnitt (samma kartografi som MCP och webben); det lokala
  typsnittet används inte i kartorna.
- Rapporter skapade före 2026-11-22 underkänns i kontrollen ("ritas inte likadant") eftersom
  kartbilden har ändrats (axeletiketterna); skapa dem på nytt.
- Frågekatalogen och mappningen är redaktionellt underlag och ska granskas av Joel.
