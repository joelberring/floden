# UX-genomgång av webbappen (2026-11-24)

Genomgång som en planarkitekt på en kommun möter appen, från inloggning till rapport. Gjord mot demodata (`floden demo`,
`floden webb`, inloggning av) i Chrome med Playwright, bred (1440x900) och smal (390x844). Nycklar och Administration är
tagna med påhittade kontosvar (webbläsarens rutt; inget konto, inget sparas). Skärmbilder: `docs/bilder/ux/` (före åtgärd)
och `docs/bilder/ux/efter/` (efter de små åtgärderna). Skript: `python tools/ux_skarmbild.py`.

Storlek: **L** = litet (timmar), **M** = medel (en dag), **S** = stort (flera dagar eller API-ändring).
Status: *åtgärdat* = gjort i den här grenen; *annan agent* = ligger i filerna som en annan agent ändrar (Nytt projekt och
projektavgränsningen) och är inte rört.

## De fem viktigaste fynden

1. **P1 · Rapporten speglar inte användarens förslag eller val (steg 3 och 5, S).** Skapa rapport skickar bara syfte, skede och mottagare
   och kör mot ämnets förvalda körning (`forval_korning`). Frågorna som valts i steg 3 följer inte med, och det ritade förslaget
   (scenariot) används inte. Planhandläggaren tror att rapporten gäller hens förslag och de valda frågorna; den gäller projektets
   färdiga beräkning med standardfrågor. Kräver API-stöd (körning och frågor i beställningen). Tills dess bör steg 5 säga vilken
   körning rapporten bygger på (se P2-15). **Åtgärdat (U2, 2026-11-24):** steg 5 skickar frågorna från steg 3 och körningen för det
   ritade förslaget, visar dem innan man skapar rapporten, och säger tydligt när rapporten skulle bygga på demokörningen eller en körning
   som inte är förslagets; saknas körning länkas steg 3. Se `docs/RAPPORTER.md`.
2. **P1 · Två skal och två vägar till samma sak (alla sidor utanför flödet, M–S). Åtgärdat i U3.** `#/projekt/<id>`, `#/fragor`, `#/om`, `#/nycklar`,
   `#/administration`, `#/prov/webgpu` har ett äldre sidhuvud (START, PROJEKT, UTREDNINGSFRÅGOR, OM BERÄKNINGARNA) utan stegraden.
   "Projekt" där leder till en sida som inte är flödets steg 1, och Utredningsfrågor (`#/fragor`) är en andra frågesida vid sidan av
   steg 3 med andra ord (Vatten i stället för Skyfall). Man tappar orienteringen när man går till Nycklar och kommer inte tillbaka med ett klick.
3. **P1 · Nytt projekt är en återvändsgränd lokalt (annan agent, plus åtgärdat i projektlistan). Åtgärdat i U4: sidan förklarar varför och ger en handling, se "U4".** Knappen visades alltid och ledde
   till "Geografierna gick inte att hämta: Att skapa projekt kräver inloggning". Knappen ersätts nu i lokalt läge av en förklaring.
   Själva sidans felhantering ligger hos den andra agenten (se "Nytt projekt").
4. **P2 · Steg 3 kan inte köras utan ett ritat förslag, och säger det först nederst (S3, L). Åtgärdat i U4: förklaring överst och fast handlingsrad, se "U4"; läget idag kan räknas utan förslag (U5a).** Knappen "Kör beräkningarna" är grå
   och ligger under skärmens nederkant i bredd 1440x900 på steg 3; orsaken står i en liten text ovanför. Åtgärdat delvis: länk till
   Resultat och `aria-describedby`. Kvar: knappen bör ligga synlig utan rullning.
5. **P2 · Fristående räknare med exempeltal i Resultat (steg 4, L, åtgärdat).** "Fler analyser: MKN-intervall och bullerskärmar"
   var förifylld med påhittade tal och gav "resultat" som ser ut att höra till projektet. Märkt som fristående med egna värden. Bör
   på sikt flyttas till Matematik eller tas bort ur planarkitektens flöde (M).

## Vyer, ett avsnitt var

### 1 Inloggning (`/konto/...`, servern med inloggning)
Försöker: komma in. Sidan (ej fotograferad här; läst ur koden, `sidaInloggning`) är en målning med "Logga in med Google" och "Bara för inbjudna".
- P2 · Utloggad besökare får aldrig veta *vem man ber om inbjudan hos*; ingen kontaktväg, ingen text om vad Floden är (medvetet kort,
  men en kommunanställd utan inbjudan har ingen väg vidare). Förslag: en rad "Saknar du inbjudan? Kontakta din organisations administratör." (L)
- P3 · Kontrollera kontrasten för texten över målningen mot WCAG AA, särskilt på smal skärm. (L)

### 2 Projektlistan (steg 1, `#/`)
Försöker: hitta sitt projekt och komma till kartan.
- P2 · **Oklart att man måste klicka två gånger** (första klicket väljer, andra öppnar). *Åtgärdat:* raden säger "Visa på kartan"
  respektive "Öppna projektet →", och kartkortet har en knapp "Öppna projektet".
- P2 · "Nytt projekt" gav 403 lokalt. *Åtgärdat:* förklaring i stället för knapp när inloggning är av (och knappen ritas om när
  kontoläget har hämtats; det kommer efter första ritningen).
- P2 · Tomtillstånd: "Det finns inga projekt än. Demokvarteret byggs med `floden demo`" är bra för en utvecklare men inte för en
  planarkitekt på en server. Förslag: olika text för inloggad (be administratören ge dig en roll i ett projekt, eller skapa ett) och lokal. (L)
- P3 · Miniatyrerna är grå rutor utan ortofoto; projektraden saknar roll (utredare/läsare) och organisation, som finns i API:t. (L)
- P3 · Kartan visar bara byggnader mot grå bakgrund; en grov kartbakgrund (valfri) skulle göra det lättare att känna igen området. (M)
- Smal skärm: listan över kartan, fungerar. Kartkortet och knappen får plats.

### 3 Nytt projekt (`#/nytt`, `webb/src/flode/nyttprojekt*.ts`, `src/webb/projektskapa.py`) – annan agent, orört
Endast fynd:
- ~~P1 · Felet från `GET /api/geografier` visas som röd text utan väg vidare utom länken "Till projektlistan". Ge en handling:
  "Be en administratör om rollen utredare eller ägare" respektive "Lokalt: floden projekt skapa". Skilj 403 (behörighet) från
  nätverksfel. (L)~~ *Åtgärdat (U4).*
- P2 · *Åtgärdat i U3 (huvudet, utan att röra nyttprojekt-filerna):* stegraden visas inte utan projekt. Tidigare: sidan behöll stegraden med "1 Projekt" aktivt, men saknade projekt i huvudet; "Floden /" saknar sammanhang. (L)
- P2 · Polygon blir sin omslutande rektangel (står i STATUS): visa den rektangel som faktiskt skapas innan man trycker Skapa. (M)
- P3 · Kontrollera att "Skapa" förklarar varför den är grå (saknat namn, ingen avgränsning) med `aria-describedby`, som steg 3 och 5. (L)

### 4 Förslag (steg 2)
Försöker: rita ett hus och se vad det gör.
- P2 · Ritverktygen är många och korta ("Fäst", "Rät vinkel" är påslagna från början men visas bara som mörka knappar utan förklaring av att
  de är lägen). Förslag: `aria-pressed` finns; lägg till tooltip/ledtext på första besök. (L)
- P2 · Tom panel: "Inget ritat än" är bra, men "Välj frågor" längst ned är aktiv utan förslag; man kan gå vidare utan att ha ritat. Det
  är avsikten (projektets beräkningar), men knappen bör heta "Välj frågor" bara med förslag och annars "Hoppa över: se projektets beräkningar". (L)
- ~~P2 · Planunderlag/Lager och underlag: filfältet `#fs-fil` saknar etikett (dolt, men skärmläsare når det). (L)~~ *Åtgärdat (U4): `aria-label`.*
- P3 · Pekytor under 32 px för verktygsknappar på smal skärm (25 element mätta). (L)
- P3 · Smal skärm: verktygsraden tar 1/3 av kartans höjd. Förslag: horisontellt rullbar rad med ikoner. (M)

### 5 Frågor (steg 3)
Försöker: välja vad som ska besvaras och köra.
- ~~P1 (delvis åtgärdat) · Kan inte köras utan förslag. *Åtgärdat:* texten länkar till Resultat och knappen har `aria-describedby`.
  *Kvar:* knappen ligger under vikningen på bred skärm (panelen är högre än fönstret); sätt panelen i `position: sticky; bottom: 0` (L).~~ *Åtgärdat (U4): fast handlingsrad.*
- P1 · **De valda frågorna påverkar inget**: de styr bara vilka beräkningar som körs (via frågans domän), inte rapporten (se fynd 1).
  Frågor med "Underlag saknas" går att kryssa utan effekt. Förslag: inaktivera kryssrutan eller visa "Besvaras inte i den här körningen". (M)
- P2 · "Valda frågor 34" räknar alla ämnen men listan visar ett ämne. *Åtgärdat:* etiketten säger "(alla ämnen)".
- P2 · Ämnesraden blandar Skyfall/Dagsljus och sol/Luft/Buller/Ekologi; Luft och Buller är valbara men går inte att köra
  (se README: Buller under utveckling). Markera ämnen utan körning i raden, inte bara per fråga. (L)
- P2 · "Planunderlag" kan väljas men finns inte. *Åtgärdat:* heter "Planunderlag (inte än)".
- ~~P2 · WebGPU-raden ("f32 skissläge · formella resultat räknas i f64") är teknisk text för planarkitekten; flytta till hjälp eller
  Om beräkningarna. (L)~~ *Åtgärdat (U4): ligger under "Teknisk information".*
- ~~P2 · "Uppskattad tid –" utan förklaring när inget är beräknat; skriv "visas när ett förslag är ritat". (L)~~ *Åtgärdat (U4).*
- P3 · Beställningen (skede, mottagare, syfte) finns både här och i steg 5 med samma värden; två ställen att ändra samma sak. Behåll ett. (M)
- Smal skärm: en lång lista och beställningen under; knappen längst ned i listan, tydligt.

### 6 Resultat (steg 4)
Försöker: förstå vad förslaget gör och om det är ok.
- P2 · Man ser projektets färdiga beräkning utan att det sägs om man inte ritat. *Åtgärdat:* rad som säger det med länkar till steg 2 och 3.
- P2 · Knappen hette "Skapa rapport" men leder bara till steg 5 där man får trycka igen. *Åtgärdat:* "Gå vidare till rapport".
- P2 · Fristående räknare med exempeltal (fynd 5). *Åtgärdat:* tydligt märkt.
- P2 · Osäkerheten ligger bakom en knapp ("Visa osäkerhet") och gäller markmodellen före, inte förslaget (står i panelen). Det är ärligt
  men missförstås lätt; sätt in en rad direkt under talen: "Osäkerhet: ej visad". (M)
- P2 · Termer: Skyfall (flik), Vatten (äldre sidor), Sol/Dagsljus/Soltimmar; Före/Efter/Skillnad/Svep/3D utan förklaring av Svep. (L)
- P3 · "Yta där djupet minskar 0 m²" är ett korrekt men tomt tal; dölj eller förklara när 0. (L)
- P3 · Kartans teckenförklaring täcker nederkant till vänster på smal skärm. (L)
- Tangentbord: flikarna har pilnavigering (bra). Kartan går inte att läsa av med tangentbord (värdet bara vid hovring); en tabell
  "största värden" finns i panelen, vilket är en acceptabel reserv.

### 7 Rapport (steg 5)
Försöker: få en rapport att lämna vidare.
- ~~P1 · Rapporten bygger på förvald körning, inte på användarens förslag och val (fynd 1).~~ *Åtgärdat (U2):* panelen "Rapporten bygger på" och API-fälten `fragor`/`scenario`/`korning_id` (K48).
- P2 · Tomtillståndet hänvisade till `floden mcp`. *Åtgärdat:* tydlig handling i webben. Samma text fanns på `#/projekt/<id>` (`python tools/rapport_demo.py`); *åtgärdat*.
- P2 · "Planunderlag" valbart men knappen grå. *Åtgärdat:* "(inte än)".
- P2 · Rapporten visar inte vilken körning, vilket förslag och vilket datum beräkningen har; bara skapad-datum. *Delvis åtgärdat (U2):* raden Underlag i handlingsrutan har körning och förslagsnamn (eller att det är demokörningen); datum kvar. (L)
- P2 · Förhandsvisningens statusprickar är bara färg + `aria-label`; lägg text i listan (grön/gul/grå förklaras bara per prick). (L)
- P3 · Datum skrivs med `toLocaleDateString("sv-SE")`; okej, men gå via en gemensam funktion som tal.ts. (L)
- Smal skärm: förhandsvisningen under innehållsförteckningen och panelen; fungerar.

### 8 Nycklar (`#/nycklar`)
Försöker: lägga in Lantmäteri-nyckel eller skapa MCP-token.
- P1 · *Åtgärdat (U3, sidhuvudet):* flödets sidhuvud med "Floden / Nycklar". Kvar: inget sätt att se *varför* man behöver nycklarna eller om de redan behövs (lokalt: config). (M)
- P2 · Formulären var hoptryckta (etikett bredvid fält, knappar utan avstånd). *Åtgärdat:* etikett ovanför, bryts på smal skärm.
- P2 · Sidan blandar tre saker (API-nycklar, MCP-token, anslutna appar) utan innehållsförteckning. Lägg ankarlänkar överst. (L)
- P2 · Lokalt (inloggning av) visas bara en ruta som hänvisar till `config.local.toml`: korrekt men ger ingen väg att kontrollera
  om nyckeln fungerar. Förslag: "Testa anslutningen". (M)
- P3 · Ordet "Hemlighet" som fältnamn; skriv "Klienthemlighet (secret)". (L)

### 9 Administration (`#/administration`)
Försöker: bjuda in kollegor och sätta roller.
- P2 · Roller visades som `lasare`. *Åtgärdat:* "läsare", "ägare" (värdena till servern är oförändrade).
- P2 · Tabellerna rullar i sidled på smal skärm utan tecken på att mer finns (roll- och åtgärdskolumnerna klipps). Förslag: kort per person
  på smal skärm (M). Formulären är *åtgärdade* (staplade).
- P2 · Spärra och ta bort har `confirm()` (bra) men samma knapprad för farliga och ofarliga åtgärder; skilj med färg. (L)
- P2 · Rollernas innebörd står i en mening i ingressen; lägg en förklaringsruta per roll bredvid rollvalet. (L)
- P3 · Datum visas som ISO (2027-01-29) men resten av appen skriver svenska datum. (L)

### 10 Matematik (`#/matematik`)
Försöker: (forskare) förstå metoderna; (planarkitekt) kontrollera att man kan lita på talen.
- P2 · Mycket text och formler som text; många länkar till GitHub som är avstängda i kommunnät. En sammanfattning "Vad betyder skiss för mitt
  resultat?" överst vore mer användbar för målgruppen. (M)
- P3 · 145 små pekytor (länkarna); på smal skärm svårt att träffa. (L)
- Är nåbar bara via kontomenyn; rätt för en sida som är för specialister. (Flödets huvud sedan tidigare; i U3 utan stegrad.)

### 11 WebGPU-prov (`#/prov/webgpu`)
- P3 · Teknisk provsida utan länk (avsiktligt). Visas sedan U3 med flödets sidhuvud ("Floden / WebGPU-prov"); "Inga resultat än" är tydligt. Bör ha en rad om att den bara
  är för utvecklare och inte ändrar något resultat. (L)

### 12 Äldre sidor (`#/projekt/<id>`, `#/fragor`, `#/om`, `#/forvaltning`)
- P1 · Dubbla vägar (fynd 2). *Åtgärdat i U3:* `#/projekt/<id>` omdirigeras till `#/p/<id>/forslag`, `#/fragor` är en frågekatalog, `#/forvaltning` har flödets huvud (omdirigeringen till projektlistan när den inte finns är kvar och avsiktlig).
- P2 · *Åtgärdat (sidan är ersatt av frågekatalogen):* Utredningsfrågor-sidan visade "Körs lokalt utan inloggning. Resultat sparas på den här datorn" även för vem som helst på en server. (L)
- P2 · Luft och Buller visas på tre ställen med olika statustext. Hämta alltid ur `/api/moduler`. (L) *Delvis åtgärdat i U3:* startsidan, projektsidan och Utredningsfrågor-demokorten är borta; kvar är steg 3 och Om beräkningarna.

## Tvärgående

**Språk och termer.** Skyfall/Vatten, Sol/Dagsljus/Soltimmar, Förslag/Scenario/Ritat, Projektets exempel/Projekt. Besluta en ordlista
(förslag: Skyfall, Sol och dagsljus, Förslag) och använd den i flik, fråga, rapport och MCP. Decimalkomma och smalt mellanrum är konsekvent
via `tal.ts` i flödet; ISO-datum och `toLocaleDateString` förekommer (se ovan). (M)

**Tomtillstånd och fel.** Fel visas med `role="alert"` och nästan alltid "Är servern igång?", vilket är fel text för en användare på en
server. Skilj nätverksfel, 401/403 (behörighet) och 5xx, och ge en handling. (M) *Åtgärdat (U4) för projektlistan, Nytt projekt, steg 2–5.*

**Tillgänglighet.** Hoppa-till-innehåll finns, `lang="sv"`, h1 per sida, synlig fokusram (2 px) på tangentbordet, flikar med pilar, förlopp med
`role="status"`. Kvar: filfält utan etikett (steg 2), kartvärden bara vid hovring, färgade statusprickar, pekytor under 32 px, kontrast på
dämpad text (ej mätt; kontrollera 4,5:1). (M)

**Smal skärm.** Ingen sida har sidledes rullning (scrollWidth = 390 överallt). Flödet staplar karta över panel. Svagast: steg 2 (verktygsrad)
och tabeller i Administration. (M)

**Navigering.** *Åtgärdat i U3:* Tillbaka-väg från kontosidorna (ordmärket och "Floden / <sida>" leder till projektlistan; samma huvud överallt). Tidigare: tillbaka-väg från kontosidorna saknades (fynd 2). Stegraden bör vara klickbar bara till steg som har det som krävs, och visa
vad som saknas på ett gråat steg. (M)

## Vad som åtgärdades i den här grenen

Projektlistan (öppna-handling, Nytt projekt lokalt), steg 3 (länk, etikett, planunderlag, `aria-describedby`), steg 4 (källrad, knappnamn,
märkning av fristående räknare), steg 5 (tomtext, planunderlag), Administration och Nycklar (rollnamn, formulärlayout), start-/projektsidans
text om rapporter. Tester: `flode/projektlogik.test.ts` och rättade `kontosidor.test.ts`.

## Uppdrag att lägga i PLAN.md

1. Rapporten ska byggas på användarens körning och valda frågor (API + steg 3/5). S
2. Ett skal: flytta `#/projekt`, `#/fragor`, `#/om`, Nycklar, Administration till flödets sidhuvud och ta bort de äldre dubbletterna. M–S (**U3, klar**, se nedan)
3. Gemensam ordlista, felmeddelanden med handling, sticky åtgärdsknapp i steg 3. M
4. Tillgänglighetspass (kontrast, pekytor, kartavläsning med tangentbord, kort i stället för tabeller på smal skärm). M

## U3 · Ett skal (åtgärdat)

Före: `docs/bilder/ux/u3-fore/`, efter: `docs/bilder/ux/u3-efter/` (samma 13 vyer, bred och `-smal`; `python tools/ux_skarmbild.py --ut <mapp>`).
Bilderna före är tagna mot en server med bygget före U3; `11-projektsida` visar efter U3 det flödessteg adressen leder till.

- **Ett sidhuvud** (`webb/src/flode/huvud.ts`) på alla vyer: projektlistan, Nytt projekt, stegen, `#/fragor`, `#/om`, `#/matematik`, `#/nycklar`,
  `#/administration`, `#/forvaltning`, `#/prov/webgpu` och arbetsbänken. Det äldre sidhuvudet (`sidhuvud()` i `sidor.ts`: START, PROJEKT, UTREDNINGSFRÅGOR,
  OM BERÄKNINGARNA) är borttaget. Stegraden visas bara i ett projekt (`#/p/<id>/...`); på övriga sidor står "Floden / <sidans namn>" och sidan är markerad i kontomenyn.
  Kontomenyn: Nycklar (inloggad), Administration (admin), Förvaltning (med kontodata), Frågekatalog, Om beräkningarna, Matematik och forskning.
- **Adresser** (`omdirigering` i `webb/src/rutter.ts`, tester i `rutter.test.ts`):

  | Gammal adress | Leder till |
  | --- | --- |
  | `#/projekt/<id>`, `#/oversikt/<id>` | `#/p/<id>/forslag` |
  | `#/projekt`, `#/oversikt` | `#/` (projektlistan) |
  | `#/fragor/<ref>` | `#/fragor` (frågekatalogen) |
  | `#/karta...` | som förut (steg 2, med `?vy=` steg 4) |
  | okänd adress | `#/` |

- **`#/fragor` är en ren katalogvy** (`flode/fragekatalog.ts`, `fragekatalog-html.ts`): samma ämnen (Skyfall, Dagsljus och sol, Luft, Buller, Ekologi), samma frågor och
  samma statusetiketter som steg 3, utan kryssrutor och utan körning, med länkar "Välj frågor i <projekt>" till steg 3. Den äldre K20-körsidan (`fragor.ts`, med
  demokort, plan, körning och resultatkarta för enskilda frågefiler) är borttagen; frågefiler körs fortfarande med `floden fraga kor`. `fragor.css` ligger kvar
  (importeras av routern) eftersom arbetsbänken, provsidan och kontosidorna delar stilar därifrån.
- Borttaget som var parallella vägar: startsidan (`sidaStart`), projektsidan (`sidaProjekt`, `sidaProjektUtanData`, `rapportLista`), kontodelen i det äldre huvudet.
  Projektraden i flödets steg 1 visar nu roll och delning med organisationen (`rollRad`), som "Dina projekt" på startsidan gjorde.

## U4 · Ordlista, fel med handling, fast handlingsrad (åtgärdat 2026-11-24)

Före: `docs/bilder/ux/u4-fore/` (Nytt projekt och steg 3). Efter: `docs/bilder/ux/u4-efter/` (`02-nytt-projekt` lokalt, `02c-nytt-saknar-roll`,
`02d-nytt-natverksfel`, `04-fragor`, `04b-fragor-vy` = bara fönstret, `06-rapport`; bred och `-smal`; `python tools/ux_skarmbild.py --ut <mapp>`).
Tillstånden 02c och 02d är påhittade svar i webbläsarens rutt. Utloggad besökare på en server med inloggning når aldrig Nytt projekt: appen visar
inloggningssidan först. Inloggningslänken i Nytt projekt gäller bara en session som gått ut medan sidan var öppen.

**Nytt projekt (`#/nytt`).** `felhandling.ts` (`nyttProjektFel`) skiljer fem fall utan att förlita sig på texten: inte inloggad/utgången session (länk "Logga in", återvänder
hit), lokalt utan inloggning (kontoläge av: förklarar att rollerna finns i kontosystemet och pekar på `floden projekt skapa`), inloggad utan rätt (vem som kan
skapa projekt: superadmin, organisationsadmin, ägare; be din organisationsadmin), nätverksfel och serverfel (Försök igen), och ingen mappad geografi
(olika text för superadmin och andra). Formuläret visas aldrig om det inte går att skapa: `GET /api/geografier` ger redan `far_skapa_projekt` och 403,
så inget API-fält behövdes. `lasJson` kastar nu `ApiFel` med HTTP-status (0 = ingen kontakt; `apifel.ts`), meddelandet är oförändrat.

**Steg 3 utan förslag.** `POST /api/kartverktyg/<projekt>/scenarier/<slug>/kor` kräver ett sparat förslag med minst en ändring som berör området
(`Förslaget har ingen ändring…`), och `POST /api/kor` är den äldre områdesberäkningen (`omrade_id`), inte projektets frågeflöde. Att räkna "läget idag"
utan förslag skulle kräva en ny körväg (K20 med bara `fore`) och är därför ett uppdrag (se PLAN). Valt: sidan säger det överst ("Inget förslag är ritat …
projektets befintliga läge är redan beräknat och visas i Resultat") med knapparna **Rita ett förslag** (steg 2) och **Visa projektets befintliga resultat**
(steg 4), och körknappen visas inte som död knapp. Frågorna går att välja redan nu.

**Fast handlingsrad.** Steg 3: `.flode-handlingsrad` (sticky, längst ned, `korlage.ts`) med statustext (`role="status"`) och knapp(ar); tar plats i flödet så att den
inte täcker slutet av innehållet, staplar ihop sig på smal skärm (knapparna sida vid sida), `scroll-padding-bottom` så att fokuserade fält inte hamnar under den,
`env(safe-area-inset-bottom)`. Steg 5: knappen och dess förklaring (`.flode-skapa-rad`) är sticky i panelen.

**Fel med handling i övrigt.** Projektlistan, steg 2 (kartan), steg 3 (frågorna), steg 4 och steg 5 använder `sidFel` (inloggning, behörighet, nätverk, server; "Försök igen"
laddar om sidan). "Är servern igång?" är borttagen ur flödet och Om beräkningarna.

**Tillgänglighet.** Kontrast uträknad för flödets text- och bakgrundspar (dämpad text 5,6–6,3:1, fel 7,6:1, etiketter 6,9–7,7:1; alla över 4,5:1). Filfältet `#fs-fil`
har etikett. Pekytor minst 40 px på smal skärm (verktyg, zoom, segment, ämnesknappar, flikar). Handlingsraden ligger sist i DOM, så Tab slutar vid körknappen. Felrutor
har `role="alert"` och får fokus på Nytt projekt.

### Ordlista (använd i gränssnitt, rapporter och dokument)

| Använd | Inte | Betyder |
| --- | --- | --- |
| **Skyfall** | Vatten, översvämning (som ämne) | Ämnet för regn som ger vatten på marken; "vatten" bara för vattendjup och vattnets väg |
| **Dagsljus och sol** | Sol, Soltimmar (som ämne) | Ämnet; *soltimmar*, *VSC* och *dagsljusfaktor* är mått under det |
| **Förslag** | Scenario, Ritat, K9 | Det planarkitekten ritar i steg 2 (hus, höjder, rivningar); sparas som scenario på servern |
| **Läget idag** / projektets befintliga läge | Före, bas, referens (utom på kartans Före/Efter-knappar) | Projektet utan förslaget |
| **Beräkning** | Körning, jobb | Det Floden räknar; "körning" bara i spårbarhet (körningens id) och i "Kör beräkningarna" |
| **Fråga** | Analys, mall | En utredningsfråga ur frågekatalogen (SK-01 …); en *beräkning* svarar på en eller flera frågor |
| **Beställning** | Order, uppdrag | Skede, mottagare och syfte som styr rapporten |
| **Skiss** / **Planunderlag** | Utkast | Skede: skiss finns, planunderlag inte än |
| **Skillnad** | Delta, diff | Efter minus före; **Svep** = dra en gräns på kartan mellan före och efter |
| **Underlag saknas** | Ej tillgänglig | Frågan kan inte besvaras med data som finns |
| **Osäkerhet** | Felmarginal | Intervall och känsliga överfall; gäller markmodellen före, inte förslaget |

Termerna i ordlistan är genomgångna i steg 3, 5 och felmeddelandena; steg 4 och kartans lager återstår att läsa mot listan (se PLAN, U5).

## U5a · Läget idag utan förslag (åtgärdat)

Steg 3 hade ingen körväg utan ritat förslag (scenario-`kor` nekar ett förslag utan ändring, `/api/kor` är den äldre områdesberäkningen).

- **Knapp:** när inget förslag finns har den fasta handlingsraden (`flode/korlage.ts`) tre handlingar: "Rita ett förslag", "Räkna läget idag" och "Visa projektets befintliga resultat". Raden och sammanfattningen visar uppskattad tid för läget idag (ur planen, `uppskattadTidText`; "klart i cachen" när allt redan är räknat). Knappen är grå utan valda frågor som Floden kan räkna.
- **Steg 4:** källan "Läget idag" (bredvid "Ditt förslag" och "Projektets exempel") visar bara Före; Efter, Skillnad, Svep och 3D döljs, markörens värde heter "Läget idag", och panelen säger att ingen jämförelse finns. Inga nyckeltal räknas i klienten.
- **Rapport (steg 5):** körningen är projektets egen och märkt `lage_idag` i underlaget, men rapportens mallar bygger på före och efter, så den väljs aldrig av sig själv och en uttrycklig begäran ger 409 med vad som ska göras (rita ett förslag, steg 3). En rapport om enbart läget idag är inte byggd.
- **Kvar (P3):** steg 4 visar inga mätvärden för läget idag (bara kartan och värdet under markören); ekologi visas som karta utan nätverkssiffror.
