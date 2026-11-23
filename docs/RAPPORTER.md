# Rapporter: påståenden med belägg, kontroll och export (R1, R2)

**Floden räknar, AI:n ställer frågor, väljer körningar och skriver – men äger aldrig ett tal.**
En rapport är en lista *påståenden* (K32) som var och en besvarar en fråga ur frågekatalogen och
är bunden till *belägg* ur Flodens körningar. Talen i texten är typade platshållare som Floden
fyller. Före export körs en deterministisk kontroll; ett enda fel stoppar exporten. Beslutat av
Joel 2026-11-22 (se `BESLUT.md`). Kod: `src/rapport/` (`pastaende`, `belaggskalla`,
`fragekatalog`, `bestallning`, `skissrapport`, `skisskontroll`, `skissmall`, `amnestext`,
`skissexport`, `fasadbelagg`, `referensflode`, `referensflode_dagsljus`).
Kontrakt: K30, K32 och K33 i `DATAKONTRAKT.md`.

## Två nivåer

| Nivå | Vad | Status |
| --- | --- | --- |
| 1 Skissunderlag | Sållning, inte bedömning mot krav. 2–6 sidor: karta per ämne (före, efter, skillnad), jämförelse av alternativ, trafikljus per fråga ("behöver fördjupas"), saknat underlag, alla metoder med valideringsstatus. Ansvarig utredare valfri. | Finns för **skyfall** (R1) och **dagsljus och sol** (R2) |
| 2 Publicerbart planunderlag | Bedömning mot bedömningsgrunder, metodnotis och ansvarig utredare, granskning och signering. | R3; fälten finns förberedda i K32/K33 (`bedomningsgrund`, `granskning`, `signering`, `skede`, `revision`) |

Ordning för ämnena (Joel 2026-11-22): skyfall → dagsljus/sol → luft → buller. Skyfall och dagsljus/sol är klara på nivå 1; luft och buller återstår.

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

## Beställning (K30)

En beställning är valfri och styr två saker: vilka katalogfrågor underlaget tar med och hur
ingressen skrivs. Utan beställning gäller som förut (alla frågor för ämnet, mallens ingress).
Fälten (kontraktet står i `DATAKONTRAKT.md`):

- **syfte**: en mening om vad underlaget ska hjälpa till att avgöra. Skrivs ut i handlingsrutan
  och ingressen. Syftet får inte likna en e-postadress eller ett personnummer.
- **skede**: `skiss` eller `planunderlag`. Bara `skiss` kan köras; `planunderlag` (nivå 2) vägras
  med hänvisning till R3.
- **mottagare** (`mottagare_roll`): `planhandlaggare`, `byggaktor` eller `granskare`. Rollen
  väljer ingressens tonläge och filtrerar frågorna på typ (byggaktören ser föreskrifter, lagar,
  riktvärden, riktlinjer, vägledning och metod; granskaren metod, projektkontroll och föreskrift;
  planhandläggaren alla).
- **ämnen och frågor**: `dagsljus` och `sol` (hör ihop i en rapport) eller `skyfall`. `fragor`
  anger frågor uttryckligen och går före urvalet; `villkor` filtrerar på frågans villkor;
  `bara_med_underlag` tar bara frågor som Floden kan besvara helt eller delvis.
- **projekt och scenario**: `scenario = {"fore": "bas", "efter": ["hojd", "nytt_hus"]}` väljer
  vilka jämförelser som redovisas. Utelämnat tas alla jämförelser i körningen.

Beställningen räknar ut urvalet (`urval`, `utelamnade`) när den skapas och sparas i
`<rapportmapp>/bestallningar/b-<12 hex>.json`; en kopia ligger i rapportens K33 (`bestallning`).
Ingressen nämner hur många av frågorna i ämnet som togs med. Kontrollen ändras inte av en
beställning: varje fråga som kommer med behöver fortfarande ett påstående med belägg.

## Skissunderlaget för dagsljus och sol

Ämnet `dagsljus` (rapporten heter "Skissunderlag dagsljus och sol") täcker katalogens DL- och
SO-frågor. Det byggs på demons körning `demo-dagsljus` (soltimmar på mark och vertikal
himmelskomponent, VSC, på fasadpunkter, före och efter *höjt hus* och *nytt hus*) och använder
samma disposition, kartmönster, bilagor, kontroll och språkprofiler som skyfallsunderlaget. Bara det
kärnan faktiskt räknar redovisas (`docs/DAGSLJUS.md`).

Måtten, med värdena i demon (hela demokvarteret, fullt läge):

| Mått | Före | Höjt hus | Nytt hus |
| --- | --- | --- | --- |
| Soltimmar på mark, medel (h per dygn, 20 mars) | 4,4 | 4,4 | 4,3 |
| Markyta som mister sol, jämfört med nuläget | | 2 758 m² | 5 318 m² |
| Markyta som mister mer än 1 timme | | 1 456 m² | 3 077 m² |
| Markyta som mister mer än 2 timmar | | 598 m² | 1 609 m² |
| Största minskning i en cell (h) | | 5,5 | 7,0 |
| Fasadpunkter (K11) med VSC under BRE:s gräns, 27 % | 1 245 av 3 553 | 1 247 av 3 553 | 1 272 av 3 545 jämförbara |
| Punkter som går från över till under gränsen | | 2 | 27 |
| Punkter där VSC efter är lägre än 0,8 av före | | 0 | 38 |
| Största minskning av VSC (procentenheter) | | 3,0 | 17,3 |
| Lägsta VSC i nuläget | 7,7 % | | |

Talen fylls av Floden ur körningen; de står i rapporten som platshållare. Små avvikelser mot
talen här kan förekomma i det lilla testläget (2 m-celler). Mätpunkterna är genererade
fasadpunkter (en per våning och fasadsteg), inte verkliga fönster, och VSC är himmelskomponent
under mulen CIE-himmel, inte dagsljusfaktor i rum; kontrollen (`dagsljusfel`) vägrar påståenden
som säger något annat. Soltimmarna är en dygnssumma på mark i en ruta, inte sammanhängande tid
och inte sol på fasad. SVF (himmelsandel på mark) och ekvinoktieskuggor redovisas bara om
körningen har dem; demon har det inte, och det står då som underlag som saknas. Det finns ingen
bedömning mot krav: BRE:s 27 % och kvoten 0,8 används som räknade antal ("antal punkter under
gränsen"), inte som slutsats. Parametrarna (gräns, kvot, datum) är belägg av typen *parameter*
och blandas inte med mätvärden i nämnarkontrollen.

Beläggsadresser, utöver skyfallets: `fasad.vsc.<scenario>.<mått>` (mått ur ett scenario:
`vsc_min_procent`, `antal_punkter`, `antal_under_grans`), `fasad.vsc.grans_procent` och
`fasad.vsc.kvot` (parametrar), `fasadjamforelse.vsc.<före>.<efter>.<mått>` (`antal_jamforbara`,
`antal_under_grans_efter`, `antal_nytt_under_grans`, `antal_under_kvot`, `antal_under_bada`,
`antal_nya`, `min_kvot`, `max_minskning_procentenheter`), `jamforelse.<namn>.yta_minskning_over_<X>h_m2`
(markyta som mister mer än X timmars sol; X i hela timmar) och mottagaren `hela_omradet` i
`omrade.<mottagare>.<lager_id>.<statistik>` (samt statistiken `yta_m2`). Fasadmåtten räknas av
Floden ur `vsc.csv` per ruta och scenario; beläggets fil-sha256 är en gemensam sha256 över
filerna.

Kartorna är soltimmar före, efter per alternativ och skillnad per alternativ (samma färgskala,
samma utsnitt), ritade som skyfallets kartor (takmasken används). Bilagorna är Belägg, Körlogg,
Manifest och Kontroll.

### Frågorna DL och SO: vad Floden kan belägga

Tabellen skrivs ur `FLODEN_FORMAGA` och testet (`tests/rapport/test_dagsljus.py`) kontrollerar att
den stämmer med koden. *Floden i dag* är `visat` (frågans kärna), `delvis`, `underlag saknas`
(Floden räknar inte det frågan gäller) eller `ej tillämpligt` (frågan avgörs utanför en
beräkning). "Status i demon" är referensflödets status. I demon blir bara DL-10, DL-15, SO-09 och
SO-15 *visat*; de som markerats *delvis* får status *underlag saknas* för det som fattas.

| Fråga | Floden i dag | Belägg Floden kan ge | Saknas och varför | Status i demon |
| --- | --- | --- | --- | --- |
| DL-01 Vilken regelversion och vilken byggåtgärd gäller? | ej tillämpligt | – | Regelval och byggåtgärd är ett beslut i ärendet, inte en beräkning. Skissen jämför före och efter ett planförslag (grannpåverkan) och väljer ingen regelversion. | ej tillämpligt |
| DL-02 Vilka fasader, våningar och grannar har störst avskärmning? | delvis | Himmelskomponent (VSC) på fasadpunkter före och efter: antal punkter, lägsta värde, punkter under BRE:s gräns och med stor relativ minskning. | Sektioner och kritiska fönster redovisas inte; fasadpunkterna är genererade (en per våning och fasadsteg), inte verkliga fönster. Balkonger modelleras inte. | underlag saknas |
| DL-03 Har modellen rätt geometri och material? | underlag saknas | – | Ingen rumsmodell: glasarea, karm, smyg, ljustransmittans, rumsdjup och reflektanser ingår inte (docs/DAGSLJUS.md avsnitt 11). | underlag saknas |
| DL-04 Hur bestäms provpunkternas värde och representerade area? | underlag saknas | – | Dagsljusfaktor (DF) i typrum finns inte; Floden räknar VSC på fasad under mulen CIE-himmel. DF är en senare fas. | underlag saknas |
| DL-05 Hur stor andel av den bedömda vistelseytan klarar dagsljusfaktorn i varje bostad? | underlag saknas | – | Ingen dagsljusfaktor och inga bostäder eller vistelseytor i modellen. | underlag saknas |
| DL-06 Uppfylls dagsljuskravet i både boendeenhet och delade utrymmen? | underlag saknas | – | Ingen dagsljusfaktor och ingen uppdelning i boendeenhet och delade utrymmen. | underlag saknas |
| DL-07 Finns utblick där användningen kräver det? | underlag saknas | – | Fönster, bröstningshöjd och sikthinder för utblick modelleras inte. | underlag saknas |
| DL-08 Hur visas tillräckligt dagsljus och utblick för den avsedda verksamheten? | underlag saknas | – | Verksamhet, arbetsplatser och bedömningsmetod för dagsljus och utblick ingår inte. | underlag saknas |
| DL-09 Finns stöd för att anpassa kravet i just denna ändring? | ej tillämpligt | – | Anpassning av krav vid ändrad användning är en juridisk avvägning i ärendet, inte en beräkning. | ej tillämpligt |
| DL-10 Hur påverkas befintliga fönster och vistelserum? | visat | VSC i samma fasadpunkt (samma fasad_id) före och efter: utgångsläge, minskning, antal punkter under BRE:s gräns och under kvoten mot läget före. | Fasadpunkter är inte verkliga fönster och planlösningar är okända; omfattningen markeras. Reflekterat ljus, glas och balkonger ingår inte. | visat |
| DL-11 Vilka val av glas, balkong eller fasadkulör styr slutsatsen? | underlag saknas | – | Glas, balkonger och fasadkulör modelleras inte (balkonger och utkragningar ger fel VSC, docs/DAGSLJUS.md avsnitt 11). | underlag saknas |
| DL-12 Kan en annan granskare återskapa varje bostads resultat? | delvis | Körningens manifest, kontrollsummor och VSC per fasadpunkt (vsc.csv) gör att en annan kan återskapa fasadpunkternas värden. | Ingen lägenhetslista, ritningsrevision eller bedömningsyta per bostad. | underlag saknas |
| DL-13 Hur förhåller sig fönster och utblick till miljöförvaltningens skolvägledning? | underlag saknas | – | Fönstermått, golvyta, användning och utblick från barnets höjd ingår inte. | underlag saknas |
| DL-14 Vilket underlag svarar på hälsoskydd, byggkrav respektive arbetsmiljö? | ej tillämpligt | – | Att koppla hälsoskydd, byggkrav och arbetsmiljö till varsin slutsats är en bedömning i ärendet, inte en beräkning. VSC är ett geometriskt mått. | ej tillämpligt |
| DL-15 Kan förändringens storlek följas för samma berörda objekt även när jämförelsenivån klaras? | visat | Förändringens storlek per fasadpunkt (samma fasad_id): största minskning, lägsta kvot, antal punkter under BRE:s gräns och under kvoten. | Resultattabellen per punkt finns i körningen (vsc.csv); lägenhet och våningsplan anges bara genom fasad_id. Planlösningar är okända. | visat |
| SO-01 Vilka grannfönster och vistelseytor kan förlora sol? | delvis | Yta på mark som förlorar mer än en viss tid sol (influensområde i differenskartan) och största förlust, efter tak undantagna. | Sol vid grannfönster och på fasad räknas inte (soltimmar gäller markytan); mottagarnas funktion och våningsplan finns inte. | underlag saknas |
| SO-02 Vilken del av tillåten byggrätt representerar modellen? | underlag saknas | – | Tillåten byggrätt och etapper finns inte i Floden; scenariot är den ändring (K9) som lagts in. | underlag saknas |
| SO-03 Är solbanan rätt i tid och rum? | delvis | Solläge (latitud och longitud i rutans mittpunkt) och datum som beräkningen använde; solläget är mätt mot NREL SPA. | Tidszon och UTC-förskjutning per datum, geografiskt norr och korrigering av kartnorr redovisas inte i resultatet. | underlag saknas |
| SO-04 Vilka dagar och tider svarar på användningen av platsen? | delvis | De datum körningen räknade (parametern datum) och soltimmar för dem. | Val av dagar och brukstider för platsens användning är ett beslut; tidsfönster (öppettid, kväll) kan inte anges, soltimmar gäller hela dygnet. | underlag saknas |
| SO-05 Vilka hinder kastar skugga vid relevant årstid? | delvis | Terräng och byggnader (platta tak) kastar skugga i beräkningen; kartor och tak undantagna i talen. | Balkonger och sadeltak modelleras inte; vegetation ingår inte som standard (ogenomskinliga kronor om den slås på, ingen lövstatus). | underlag saknas |
| SO-06 Finns användbar aktiv lek och reträtt i både sol och skugga? | delvis | Soltimmar (medel, största, minsta) i varje yta som anges som mottagare (polygon), före och efter. | Ytornas funktion (aktiv lek, reträtt) och bedömning mot Lekvärdesriktlinjerna ingår inte. | underlag saknas |
| SO-07 Är gårdens zoner och tidsmål för skuggad friyta uppfyllda? | underlag saknas | – | Vegetationsskugga, trädens tillväxt och skuggad andel vid ett klockslag räknas inte (vegetation ingår inte som standard). | underlag saknas |
| SO-08 Hur länge får varje mottagare direkt sol under tidsfönstret? | delvis | Summerad direkt sol per dygn (timmar per dygn, medel över valda datum) på marken och i en mottagare, före och efter, med tidssteg ur körningen. | Sammanhängande tid och tidsfönster (brukstid) beräknas inte. | underlag saknas |
| SO-09 Var och när tillkommer skugga jämfört med nuläget? | visat | Matchade före- och efterkartor i samma färgskala, differenskarta, yta som förlorar sol och tid per mottagare (polygon). | Klockslag för när skuggan faller ingår inte (dygnssumma). Sol per fönster och sittplats anges bara som mottagare med polygon. | visat |
| SO-10 Vilken fråga avser sol vid fönstret respektive inne i rummet? | underlag saknas | – | Sol vid fönster och i rum räknas inte; soltimmar gäller markytan. | underlag saknas |
| SO-11 Vilken ändring förbättrar den mest påverkade platsen? | delvis | Jämförelse av prövade alternativ (varje scenario ställs mot nuläget) med samma mått: yta som förlorar sol och största förlust. | Floden föreslår ingen åtgärd; indragen våning, omplacerad lekyta och träd prövas bara om de lagts in som scenario (K9). | underlag saknas |
| SO-12 Kan läsaren följa varje slutsats från bild till underliggande geometri? | delvis | Kartor med norrpil, skalstock, scenarionamn, enhet och teckenförklaring; varje tal har belägg med körning, fil och kontrollsumma. | Tidszon och tidsdata per tidssteg redovisas inte i resultatet. | underlag saknas |
| SO-13 Var finns skydd för stillasittande lek och samling när barnen faktiskt använder gården? | underlag saknas | – | Skydd mot sol (skugga för lek och samling under brukstiden, vegetation) räknas inte. | underlag saknas |
| SO-14 Vilken lokal sol- eller värmestudie behövs för just gården eller byggnaden? | delvis | Soltimmar och skuggor i lokal geometri för gården eller byggnaden (mottagare). | Länets värmekartläggning och termisk analys (yttemperatur, värmestress) ingår inte. | underlag saknas |
| SO-15 Vilken tidsperiod representerar varje figur och hur knyts den till delytans användning? | visat | Varje karta är en dygnssumma (timmar per dygn, medel över de datum körningen räknade); datum och tidssteg står i antagandena och beläggen. | Ögonblicksbilder och periodmedel för flera perioder ingår inte; vilken yta slutsatsen gäller anges med mottagare. | visat |


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
| `typsnitt_brodtext`, `typsnitt_rubriker` | brödtext Gelasio (PDF) / Georgia (Word); rubriker, tabeller och småtext Liberation Sans / Arial | lokala TTF/OTF-filer, t.ex. Stockholm Type |

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
med serif i brödtexten och sans i rubriker, tabeller, småtext, sidhuvud och sidfot: Gelasio
(SIL OFL 1.1, samma teckenbredder som Georgia, siffrorna ändrade till versala) och Liberation
Sans 2.1.5 (SIL OFL 1.1, samma teckenbredder som Arial). Båda ligger med licenser i
`src/rapport/typsnitt/`. Word-filen använder Georgia och Arial, så PDF och Word ser likadana ut;
Word får versala siffror via `w14:numForm`; `invariant` och fasta tider i
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
| SK-05 Hur representeras förluster, nät och initialtillstånd? | visat | Avtappning till ledningsnätet och takvatten som de räknades (statisk fyllning har ingen infiltration och inget förlopp). | Markfukt, igensättning och upptryckning modelleras inte. | visat |
| SK-06 Vilken skillnad beror på exploateringen? | visat | Skillnad före/efter i största vattendjup på mark (takmask som webben), samma regn och område, med differenskarta. | Planförslag med åtgärd körs bara om det finns som eget scenario. | visat |
| SK-07 Ökar skaderisken för en granne eller nedströms funktion? | delvis | Stående vattenvolym och utflöde före/efter, djup i en namngiven mottagare (polygon). | Flöde och varaktighet saknas (statisk fyllning); nedströms mottagare är inte utpekade. | underlag saknas |
| SK-08 När begränsas tillträde, och finns en fungerande alternativ väg? | underlag saknas | – | Framkomlighet ej bedömd: ingen rutt i frågan, fordonsgränsen (K7) är inte fastställd och tidsserie för djup och hastighet saknas i statisk fyllning. | underlag saknas |
| SK-09 Vem kan genomföra och underhålla lösningen? | ej tillämpligt | – | Genomförande och drift är projektkontroll utanför Floden. | ej tillämpligt |
| SK-10 Vilka slutsatser ändras med ändrad terräng, regn eller avledning? | delvis | Volymbalansens relativa fel (massbalans) per körning. | Känslighetsfall (terräng, regn, avledning) kräver fler körningar; jämförelse mot oberoende underlag finns inte. | underlag saknas |
| SK-11 Är vanlig dagvattenhantering och extremregn båda redovisade? | ej tillämpligt | – | Dagvattenhantering (vardagsregn, rening) räknas inte i Floden. | ej tillämpligt |
| SK-12 Kan en läsare följa vattennivån från karta till kritisk sektion? | delvis | Maxdjup i meter på karta med teckenförklaring, tomvärde och modellversion. | Vattenyta i RH 2000, sektioner och tidsserie redovisas inte. | underlag saknas |
| SK-13 Fungerar skyfallsåtgärden i hela avrinningsområdet och innan samtliga etapper byggts? | ej tillämpligt | – | Åtgärder utanför planområdet och etappvis utbyggnad prövas inte i skissen. | ej tillämpligt |
| SK-14 Kan både vägen fram och den avsedda insatsplatsen användas vid relevant tid? | underlag saknas | – | Räddningsväg och insatsplats: fordonsgräns och brandunderlag saknas (K7 tom). | underlag saknas |
| SK-15 Var blir delområdenas resultat en bedömning av hela planområdet? | ej tillämpligt | – | Sammanvägning av flera utredningar görs inte i en skiss för ett beräkningsområde. | ej tillämpligt |


Floden kan alltså i dag ge belägg för kärnan i **SK-05** (förluster och nät som de räknades) och
**SK-06** (skillnad före/efter på mark), och för delar av SK-01, SK-02, SK-03, SK-07 (volymer och
djup i en namngiven mottagare), SK-10 (massbalans) och SK-12 (maxdjup på karta). SK-04, SK-08 och
SK-14 blir *underlag saknas* (terrängobjekt, rutt och fordonsgräns saknas; framkomlighet ej
bedömd), och SK-09, SK-11, SK-13 och SK-15 gäller projektkontroll eller sammanvägning utanför
Floden.

## Köra

- **Referensflöde utan AI:** `PYTHONPATH=src python tools/rapport_demo.py [--amne skyfall|dagsljus]
  [--roll planhandlaggare|byggaktor|granskare] [--alla-fragor] [--exempel docs/exempel]`
  (kräver `floden demo`). Skapar rapporten i `data/lokal/rapporter/<rapport_id>/`, lägger fasta
  påståenden (`rapport.referensflode`, `rapport.referensflode_dagsljus`), kontrollerar och
  exporterar Word och PDF. För dagsljus skapas först en beställning; `--alla-fragor` tar med alla 30
  frågor i stället för de 15 som Floden kan belägga helt eller delvis.
- **Via MCP:** en AI kan gå beställning, frågor, körning, rapport och export: `bestallning_skapa`,
  `bestallning_hamta`, `fragekatalog_sok`, `rapport_skapa` (med `bestallning_id`),
  `pastaende_lagg_till`, `rapport_kontrollera`, `rapport_exportera`, `rapport_hamta` och resursen
  `floden://rapport/{rapport_id}`; promptarna `skissunderlag_skyfall` och
  `skissunderlag_dagsljus`. Exempel: `PYTHONPATH=src python tools/mcp_rapportflode.py`.
- **Webben:** projektsidan listar rapporterna med kontrollens utfall (grön/röd) och länkar till
  Word och PDF (`GET /api/projekt/<id>/rapporter`, `…/rapporter/<rapport_id>/fil/word|pdf`).
  Rapportmappen kan ändras med `[webb] rapportmapp`. Frågekatalogen nås med
  `GET /api/fragekatalog?omrade=dagsljus&villkor=…&text=…&ids=…` (samma innehåll som
  `fragekatalog_sok`, inklusive `floden.markning` per fråga).

Exempel: `docs/exempel/skissunderlag_dagsljus_demo.pdf` och `.docx` (dagsljus och sol, 15 frågor)
samt `docs/exempel/skissunderlag_skyfall_demo.pdf` och `.docx` (påhittat demokvarter, förvalen:
språkprofil sbk, inga nummer, svensk sifferform, ingen logotyp, typsnitt Liberation Sans/Arial, svarta rubriker, smal spalt).

## Begränsningar

- Bara skyfall, dagsljus/sol och nivå 1 (luft och buller har ingen skissrapport än; frågorna finns i katalogen). Kartor ritas för varje jämförelse i körningen (före, efter, skillnad).
- Bara måtten i `MATTKATALOG` kan användas (vatten: statisk fyllning och dynamik; dagsljus:
  soltimmar på mark, SVF, VSC på fasadpunkter).
- Dagsljus: demons körning har ett datum (20 mars) och ingen SVF. Sol på fasad, sol på tak,
  dagsljusfaktor i rum, utblick, årsstatistik och ekvinoktieskuggor (höst) finns inte i kärnan och
  redovisas som underlag som saknas. Nivå 1 bedömer inte mot krav.
- Beställningen styr urval och ingress men inte kontrollen; rollen byggaktör/granskare filtrerar
  på frågetyp, vilket är en förenkling som ska granskas.
- Kartornas text är Pillows bitmapptypsnitt (samma kartografi som MCP och webben); det lokala
  typsnittet används inte i kartorna.
- Rapporter skapade före 2026-11-22 underkänns i kontrollen ("ritas inte likadant") eftersom
  kartbilden har ändrats (axeletiketterna); skapa dem på nytt.
- Frågekatalogen och mappningen är redaktionellt underlag och ska granskas av Joel.
