# Skyfallsmodellen – överlämningspaket

Det här är startpunkten för alla som (människa eller AI-agent) ska bygga skyfallsmodellen.
Läs filerna i den här ordningen:

1. `AGENTS.md` – regler som gäller för alla agenter, alltid.
2. `PLAN.md` – milstolpar, ordning och beroenden. Här står vad som ska göras härnäst.
3. `docs/ARKITEKTUR.md` – hur delarna hänger ihop.
4. `docs/DATAKONTRAKT.md` – exakta format mellan delarna. Den viktigaste filen för att flera agenter ska kunna arbeta parallellt.
5. `docs/BESLUT.md` – beslut som redan är fattade och inte ska omprövas utan en människa.
6. `docs/DATA.md` – datakällor, koordinatsystem och vad som är känsligt (Lantmäteriets produkter och avgifter: `docs/GEOTORGET.md`).
7. `docs/VALIDERING.md` – testsvit, mått och facit.
8. `docs/VATTEN.md` – takvatten (regn på tak) och avtappning till ledningsnätet: regler och antaganden.
9. `tasks/Txx-*.md` – en uppdragsbeskrivning per arbetsområde. En agent tar ett uppdrag i taget.
10. `STATUS.md` – logg som varje agent fyller på när den avslutar ett arbetspass.

## Vad som byggs, i en mening

En öppen skyfallsmodell som räknar dynamiskt (djup, hastighet, tid) på gatunivå,
snabbt nog för skissarbete i detaljplaner och beslutsstöd för räddningstjänsten,
med redovisad osäkerhet och färdiga rapporter.

## Bakgrundsdokument (för människor)

- Ansökan med teknisk plan som bilaga (Claude-dokument, delas av Joel).
- Den tekniska planen är sammanfattad i `docs/ARKITEKTUR.md` och `tasks/`.

## Hur en agent startar ett arbetspass

1. Läs `AGENTS.md` och `PLAN.md`.
2. Välj nästa uppdrag i `PLAN.md` som har status `redo` och vars beroenden är klara.
3. Läs uppdragets fil i `tasks/` och de kontrakt den hänvisar till.
4. Arbeta tills uppdragets acceptanskriterier är uppfyllda, eller tills ett stoppvillkor i `AGENTS.md` inträffar.
5. Skriv en post i `STATUS.md` och uppdatera status i `PLAN.md`.

## Webben (lokalt, inloggning av som standard)

`webb/` och `src/webb/` är ett lokalt webbgränssnitt som kör Flodens utredningsfrågor (K20): välj
en fråga, se planen (rutor och vad som finns i cachen), kör, och se kartvy med lager (bas, scenario,
skillnad), byggnader, varningar och mätvärden. Allt beräknas av kärnan; webben visar. Som standard
(`inloggning = "av"`) finns **ingen inloggning**: servern lyssnar på `127.0.0.1` och resultaten ligger
på den egna disken. Med `inloggning = "pa"` kopplas kontosystemet in (Google-inloggning, CSRF,
skyddat API), se `docs/KONTO.md`. Start och API: se `webb/README.md`.

## Styra Floden med AI

`floden mcp` gör Floden till en MCP-server: en AI-klient (Claude Desktop, Claude Code, Codex, lokal
modell) kan utforska demokvarteret, rita ett hus, köra före/efter och läsa resultaten, via samma kärna
som webben. Principen: **Floden räknar, AI frågar och skriver men äger aldrig en siffra**; varje tal
har belägg (körning, fil, cell, mått, enhet), varje anrop loggas i en körlogg och svaren märks med
dataklass (förval: bara `oppet`). Anslutning, verktyg och policy: `docs/MCP.md`. Kort:
`pip install -e ".[dev]"`, `floden demo`, sedan `claude mcp add floden -- floden mcp`.

**Rapporter (R1):** AI:n (eller referensflödet) lägger påståenden med belägg i ett skissunderlag
för skyfall; Floden fyller talen, kontrollerar allt före export och skriver Word och PDF. Se
`docs/RAPPORTER.md` och exemplet `docs/exempel/skissunderlag_skyfall_demo.pdf`. Utan AI:
`PYTHONPATH=src python tools/rapport_demo.py`.

## Koppla Claude till Floden på servern

Servern `https://floden.berring.se` har en MCP-server på `/mcp` (`docs/MCP.md`, `docs/DRIFT.md`). Du ser bara projekt där du har en roll,
och skrivande verktyg (rita, köra, skapa rapport) kräver rollen *utredare*. Allt som inte är öppna data stängs av spärren i
verktygslagret.

**Claude Code (personlig token).**

1. Logga in på https://floden.berring.se, öppna *Nycklar*, avsnittet *MCP-token*, ge tokenen ett namn (till exempel datorns) och
   tryck *Skapa token*. Tokenen visas **en enda gång**; sidan visar kommandot färdigt ifyllt.
2. Kör kommandot i en terminal:
   `claude mcp add --transport http floden https://floden.berring.se/mcp --header "Authorization: Bearer <token>"`
3. `claude mcp list` ska visa `floden`. Be Claude Code: "lista mina Floden-projekt" (verktyget `projekt_lista`).
4. Tappade du tokenen, eller ska en dator inte ha tillgång längre: återkalla den på samma sida och skapa en ny. Högst 5 token.

**Claude-appen (egen koppling, OAuth).** Ingen token behövs.

1. I Claude-appen (claude.ai, Desktop eller mobil): *Inställningar > Kopplingar > Lägg till egen koppling*. Namn: Floden. Adress:
   `https://floden.berring.se/mcp`. Lämna OAuth-klient-id och hemlighet tomma (Floden registrerar appen själv).
2. Tryck *Anslut*. Du skickas till Floden: logga in med Google om du inte redan är inloggad, och godkänn på sidan "Ge Claude tillgång
   till Floden som <ditt konto>?". Kontrollera att adressen under texten är `claude.ai`.
3. Be Claude: "lista mina Floden-projekt". Appen ser bara projekt där du har en roll, precis som i webben.
4. Ta bort tillgången: *Nycklar > Anslutna appar > Ta bort tillgång* (appen slutar fungera direkt), eller koppla bort i Claude-appen.
   Tillgången gäller en timme i taget och förnyas av appen i upp till 30 dagar; efter det får du godkänna igen.

**Inte provat mot den riktiga Claude-appen** när det här skrevs: flödet är provat mot specifikationen och Claudes krav med tester.

## Köra ett verkligt område på din dator

Hämtar Lantmäteriets öppna laser, byggnader och ortofoto för ditt provområde och kör dagsljus och vatten på det. Allt
körs och sparas **lokalt**; inget laddas upp. Detaljer, villkor och begränsningar: `docs/DATA.md`
("Hämta öppna data från Lantmäteriet"). **Prövat bara mot en falsk server**, inte mot Lantmäteriet.

1. **Nycklar.** Hos Lantmäteriet (API-portalen `apimanager.lantmateriet.se`) ska du ha nyckel och hemlighet, och
   produkterna Laserdata Nedladdning, skog, Byggnad Nedladdning, vektor (valfri) och Ortofoto Nedladdning (valfri) ska vara
   beställda på Geotorget (en beställning kan ta dagar; tabellen finns på sidan Nycklar och i `data/lantmateriet/produkter.toml`).
   Har du lagt in dem i O-Tid (Mitt konto) går de inte att läsa ut därifrån, så hämta dem igen. Lägg dem i
   `config.local.toml` (kopiera `config.local.toml.example`, avsnittet `[lantmateriet]`) eller i miljön:
   `export LANTMATERIET_KLIENT_ID=...` och `export LANTMATERIET_KLIENT_HEMLIGHET=...`.
2. **Området.** Rita provområdet (cirka 1 km², gärna med marginal) i QGIS och spara det som GeoPackage i EPSG:3011:
   `data/omraden/prov1.gpkg`.
3. **Bygg laserprogrammet (en gång, för LAZ):**
   `cargo build --release --locked --manifest-path tools/lasclip/Cargo.toml`
4. **Titta vad tjänsten har** (valfritt): `floden data lantmateriet --lista-samlingar`
5. **Hämta** till en mapp utanför repot (kan köras om, redan hämtat hoppas över):
   `floden data lantmateriet --omrade data/omraden/prov1.gpkg --laser --byggnader --orto --ut ~/floden-data/prov1`
6. **Bygg K1 v2-rutor:**
   `floden omrade bygg --omrade data/omraden/prov1.gpkg --data ~/floden-data/prov1 --ut data/lokal/prov1/k1`
   och för vattenlösaren (en enda ruta):
   `floden omrade bygg --omrade data/omraden/prov1.gpkg --data ~/floden-data/prov1 --en-ruta --ut data/lokal/prov1/k1_en_ruta`
7. **Kör frågorna** (exempel i `exempel/fragor/omrade_dagsljus.toml` och `omrade_vatten.toml`):
   `floden fraga kor exempel/fragor/omrade_dagsljus.toml` och `floden fraga kor exempel/fragor/omrade_vatten.toml`
8. **Visa i webben:** `floden webb` och öppna http://localhost:8765, välj frågorna *omrade-dagsljus* och *omrade-vatten*
   under **Frågor**.

Lasern har 1-2 punkter per m2 och ingen byggnadsklass; byggnadshöjderna är därför uppskattade ur oklassade punkter i
husens polygoner (se varningarna). Källa: ©Lantmäteriet. Kontrollera produkternas användarvillkor.

## Visa Floden på din dator

Så här visar du Floden för kollegor, från noll till en körande demo på en Mac. Allt körs lokalt och
inget skickas någonstans.

**Krav:** Python 3.12 (3.11–3.13 fungerar; **inte 3.14**, som Homebrews `python3` numera är –
då finns inga färdiga paket och pip försöker bygga scipy från källkod),
Node.js 20 eller senare och git: `brew install python@3.12 node`.

```bash
git clone https://github.com/joelberring/floden.git && cd floden
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
(cd webb && npm ci)
floden demo
floden webb
```

I tur och ordning: hämta koden, skapa en Python 3.12-miljö (`python --version` ska visa 3.12),
installera Floden, installera webbklientens beroenden (en gång), bygg det påhittade kvarteret och
kör frågorna (cirka 40 sekunder), och starta servern. Låt terminalen vara öppen medan du visar;
Ctrl+C stänger servern. Nästa gång räcker `source .venv/bin/activate` och `floden webb`.
Hittas inte `floden`, kör `export PYTHONPATH=src` och `python -m floden.cli demo` respektive
`python -m floden.cli webb`. Kör inte `npm audit fix --force`.

Öppna sedan **http://localhost:8765** (`floden webb --port 9000` ändrar porten). `floden demo` kan
köras om utan kostnad: är inget ändrat tar det under en sekund, och `--tvinga` bygger om allt.
`floden demo --liten` är en grövre och snabbare variant (cirka 15 sekunder) som mest är till för test.

Kvarteret är **syntetiskt**: ett påhittat område med fyra kvarter, gator, gårdar och en skålformad
sänka, byggt med markmodellens egna byggare. Inga verkliga data och inga verkliga byggnader.

**Förslag till visning (följ flödets fem steg: Projekt, Förslag, Frågor, Resultat, Rapport):**

1. **Start.** Tre steg: välj ett exempel, se före och efter, läs av värden. Under dem står vad Floden
   kan räkna på, med ärlig status per del: *Vatten* och *Dagsljus* finns i demon; *Luft* finns men en
   beräkning tar mer än tio minuter och är inte förberäknad i demon; *Buller* är under utveckling
   (metodkontroll pågår, 33 av 80 officiella testfall godkända). Statusen kommer från `GET /api/moduler`.
2. **Projekt.** Öppna *Demokvarteret*, ett tydligt märkt påhittat kvarter. Sidan visar vad som ändras
   (höjt hus, nytt hus) och siffror ur körningarnas egna resultat: soltimmar på mark och skyfall före
   och efter. Byggnadernas tak räknas inte som mark. Ett mått som inte går att räkna visas inte.
   *Teknisk information* (lösare, versioner, regnets parametrar) är hopfälld.
3. **Förslag (steg 2) och Resultat (steg 4).** Det finns en enda kartsida i webben, flödet; den gamla
   sidan `#/karta` är borttagen och adressen leder hit (`#/karta/<projekt>` till steg 2,
   `#/karta/<projekt>?vy=<vy>` till steg 4).
   **Steg 2, Förslag:** ritverktygen ligger alltid synliga till vänster (Markera, Rita hus, Lägg till och ta
   bort hörn, Ta bort hus, Fäst, Rät vinkel, Ångra, Gör om, Läs in fil), zoom uppe till höger. Panelen har
   *Lager och underlag* (lagerpanelen med Underlag, Geometri och Planförslag; ortofoto finns bara om du
   hämtat det lokalt med `floden data lantmateriet`; en WMS-tjänst eller kakelmall läggs till under
   *Underlag*, ingen är förvald, servern hämtar bilderna) och *Sparade förslag* (öppna, byt namn, ta bort).
   Valen sparas i webbläsaren per projekt.
   **Prova själv: rita ett hus.** Välj *Rita hus* (W), klicka husets hörn (hörn och kanter på befintliga
   byggnader fäster, en orange ring visar var; *Rät vinkel* låser 45 och 90 grader), dubbelklicka eller
   tryck Enter. Ange höjden i panelen (yta och uppskattad bruttoarea visas, 3 m per våning). Förslaget
   sparas som en fil på din dator (`data/lokal/kartverktyg/`, inte i git). Ett hus utanför kvarteret, ett
   som korsar sig självt eller en höjd över 200 m nekas med ett tydligt besked. Under steg 3 körs
   beräkningen.
   **Steg 4, Resultat:** flikarna Skyfall, Sol och Ekologi, med lägena *Före*, *Efter*, *Skillnad*, *Svep*
   och *3D*. Rutan *Så läser du kartan* förklarar färgerna; hovra för värden och slå av och på värden på
   tak. **Visa osäkerhet** (flikarna Skyfall och Sol) lägger intervall från K40 och K41 under siffrorna
   som `[min; max]` och känsliga överfall (K36) som markörer i kartan, med listan *Var bör vi mäta*.
   Osäkerheten gäller projektets markmodell (läget före), inte det du ritat. Alla tal skrivs med
   decimalkomma.
   **Se kvarteret i 3D.** Välj *3D* i lägesraden. Soltimmar på marken, dagsljus (VSC) som färgade punkter
   på fasaderna, vattenytan med maxdjup, före, efter, skillnad, svep eller sida vid sida. Dra för att
   rotera, högerknapp för att flytta, rulla för att zooma; *Rakt uppifrån*, *Snett*, *Norr uppåt* och
   *Hela kvarteret* ställer kameran. Allt som färgas är beräknat av Floden. Se `docs/WEBB3D.md`.
4. **Läs av.** Värdena för före, efter och skillnad står under kartan i steg 4. Grå ytor är byggnader
   (tak, inte mark).
5. **Utredningsfrågor.** Demokorten överst går att köra direkt (resultatet finns redan i cachen).
   *Fler exempel (kräver egna data)* är hopfälld: de frågorna kan inte köras i demon.
6. **Om beräkningarna.** Modulerna, och *Kontroll av beräkningarna*: syntetiska fall med känt svar som
   modellen prövas mot.

**Inloggning med Google** är avslagen som standard och då visas varken förvaltningssida eller
organisationsväljare. De dyker upp först när inloggningen är på och kontoservern har uppgifter för
kontot. Den slås på i `config.local.toml`
(`inloggning = "pa"` plus Googles klient-id och hemlighet) och beskrivs steg för steg i
`docs/KONTO.md` (avsnitt 2 för Google-klienten, avsnitt 8 för hur den är kopplad i webben). Med
inloggning på visas en *Logga in med Google*-knapp och, när du är inloggad, ditt namn och *Logga ut*.

Flödets fem steg: `docs/bilder/flode-{1-projekt,2-forslag,3-fragor,4-resultat,5-rapport}.png` och `-smal` (390 px; `python tools/flode_skarmbild.py`), dessutom `flode-2-forslag-lager`, `flode-4-resultat-osakerhet` och `flode-4-resultat-sol-osakerhet` (lager och underlag, intervall och känsliga överfall). Övriga skärmbilder (`python tools/webb_skarmbild.py --demo`): `docs/bilder/demo-projekt.png`, `demo-fragor.png`, `demo-dagsljus.png`, `demo-vatten.png`. 3D (`--3d`): `docs/bilder/3d-soltimmar-fore.png`, `3d-soltimmar-efter.png`, `3d-vsc-fasader.png`, `3d-vatten.png`.

## En ny kommun: konfigurera, inte koda

En kommun är en fil, `data/kommuner/<id>.toml` (kontrakt `floden-kommun/v1`; förebild `data/kommuner/goteborg.toml`). Filen har
källor och licenser (Lantmäteriets produkter, kommunens WFS för byggnader med tillåtna värdnamn), koordinatsystemet kommunen
publicerar i (`publicering_epsg`, t.ex. 3007 för Göteborg; Floden räknar alltid i EPSG:3011 och skriver skalfelet),
testområden (`[omraden.<id>]` med bbox i kommunens system och en motivering), geografier (`[geografier.<id>]`: namn, område
som kärna, `tillrinning_cell_m`, `max_buffert_m`), dagsljusparametrar, regnförval (`[skyfall] regn_id`) och, valfritt, kommunens förval av habitatnätverk för ekologi (`[ekologi]`, se `docs/EKOLOGI.md`). Okända nycklar,
http-adresser och värdnamn utanför listan avvisas. Då räcker det att:

1. kopiera `goteborg.toml` till `<id>.toml` och byta källor, område och koordinatsystem,
2. lägga in din egen Lantmäteri-nyckel på sidan Nycklar,
3. `floden geografi mappa <geografi-id> --konto <superadmin>` (bara superadmin), därefter
   `floden projekt skapa --geografi <geografi-id>@v1 --avgransning ... --namn ... --konto <konto-id>`.
   Det går också i webben: **Nytt projekt** på startsidan (`#/nytt`) för superadmin, organisationsadmin och ägare. Välj geografi, rita en
   rektangel eller polygon (giltig kärna är markerad; en polygon sparas och avgör vad som räknas till projektet; rutnätet är rektangeln runt den), ange namn och, om du vill,
   "Synligt för organisationen", och tryck **Skapa**. Varningar visas tydligt, och du hamnar i steg 2. En rapport skapas i steg 5
   (**Skapa rapport**, jobbet visar förlopp och ger PDF och Word); samma sak gör MCP-verktygen `projekt_skapa` och `rapport_skapa`.

Serverkommandona står rad för rad i `docs/DRIFT.md` ("Göteborg"). Kommunfilen kan i stället för WFS och Lantmäteriet peka på öppna filpaket (`[hojd]`, `[filpaket.*]`, `[byggnader] kalla = "filpaket"`): ZIP med GeoTIFF/SHP/GPKG/DWG, hämtas en gång till en cache med sha256 (`src/kommuner/filpaket.py`). Okända lagernamn och attribut i kommunens WFS löses utan
kodändring: `floden projekt kommun-lager <id>` listar lagren och `lager = "..."` eller `--byggnadslager` väljer. Villkoren för
AI-visning kontrolleras av en människa (`--villkor-kontrollerade`); tills dess är det härledda stängt för MCP.
