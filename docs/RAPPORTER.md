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
1 000). Svensk talform: decimalkomma, hårt blanksteg som tusentalsavgränsare från fem siffror och
före enheten. En siffra utanför en platshållare vägras (beteckningar som `SK-06`, `RH 2000`,
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

Disposition (förkortad ur `utredningar-stockholm/rapportdisposition.md`): titel och handling
(rapport-id, körningar, frågekatalogens källäge, dataklass, ansvarig utredare, kontroll),
1 Sammanfattning med trafikljus per fråga, 2 Uppdrag och beräkningsfall (scenarier, antaganden,
mottagare), 3 Resultat (kartor före, efter och skillnad i samma utsnitt och färgskala; påståenden
per fråga), 4 Saknat underlag och villkor, 5 Metod, osäkerhet och metodnotis (metoder med
valideringsstatus). Bilagor: A Belägg (värde före avrundning, fil, sha256), B Körlogg (rapportens
händelser och MCP-körloggens rader för rapporten och dess körningar, hashkedjan), C Manifest
(K0-parametrar per jobb), D Kontroll.

Kartorna ritas av MCP:s kartfunktion (`FlodenTjanst.karta`, samma raster, färgskala och takmask
som webben): vit mark = under lägsta klassen, ljusgrått = tak eller inget värde, så att saknade
celler inte ser torra ut. Metodnotisen är förvald: "Underlaget har tagits fram med Floden och
AI-stöd (modell) … Körloggen finns som bilaga." och ansvarig utredare (valfri i nivå 1).

**Export:** Word med python-docx och PDF med reportlab, båda ren Python (`pip install -e
".[rapport]"`, ingår i `dev` och `mcp`), samma block i båda. PDF-vägen valdes för att den saknar
systemberoenden (ingen LibreOffice), fungerar likadant på Mac och har ett medföljande typsnitt
(Bitstream Vera Sans i reportlab) med svenska tecken, ², ³ och −; `invariant` och fasta tider i
docx-zip gör samma rapport till samma fil. Word använder Arial.

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

Exempel: `docs/exempel/skissunderlag_skyfall_demo.pdf` och `.docx` (påhittat demokvarter).

## Begränsningar

- Bara skyfall och nivå 1. Kartor ritas för varje jämförelse i körningen (före, efter, skillnad).
- Bara vattenmåtten i `MATTKATALOG` kan användas (statisk fyllning och dynamik).
- Kartornas text är Pillows bitmapptypsnitt (samma kartografi som MCP och webben).
- Frågekatalogen och mappningen är redaktionellt underlag och ska granskas av Joel.
