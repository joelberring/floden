# Skyfallsmodellen – överlämningspaket

Det här är startpunkten för alla som (människa eller AI-agent) ska bygga skyfallsmodellen.
Läs filerna i den här ordningen:

1. `AGENTS.md` – regler som gäller för alla agenter, alltid.
2. `PLAN.md` – milstolpar, ordning och beroenden. Här står vad som ska göras härnäst.
3. `docs/ARKITEKTUR.md` – hur delarna hänger ihop.
4. `docs/DATAKONTRAKT.md` – exakta format mellan delarna. Den viktigaste filen för att flera agenter ska kunna arbeta parallellt.
5. `docs/BESLUT.md` – beslut som redan är fattade och inte ska omprövas utan en människa.
6. `docs/DATA.md` – datakällor, koordinatsystem och vad som är känsligt.
7. `docs/VALIDERING.md` – testsvit, mått och facit.
8. `tasks/Txx-*.md` – en uppdragsbeskrivning per arbetsområde. En agent tar ett uppdrag i taget.
9. `STATUS.md` – logg som varje agent fyller på när den avslutar ett arbetspass.

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

**Förslag till visning (följ menyn: Start, Projekt, Karta, Utredningsfrågor, Om beräkningarna):**

1. **Start.** Tre steg: välj ett exempel, se före och efter, läs av värden. Under dem står vad Floden
   kan räkna på, med ärlig status per del: *Vatten* och *Dagsljus* finns i demon; *Luft* finns men en
   beräkning tar mer än tio minuter och är inte förberäknad i demon; *Buller* är under utveckling
   (metodkontroll pågår, 33 av 80 officiella testfall godkända). Statusen kommer från `GET /api/moduler`.
2. **Projekt.** Öppna *Demokvarteret*, ett tydligt märkt påhittat kvarter. Sidan visar vad som ändras
   (höjt hus, nytt hus) och siffror ur körningarnas egna resultat: soltimmar på mark och skyfall före
   och efter. Byggnadernas tak räknas inte som mark. Ett mått som inte går att räkna visas inte.
   *Teknisk information* (lösare, versioner, regnets parametrar) är hopfälld.
3. **Karta.** Välj vy (dagsljus höjt hus, dagsljus nytt hus, vatten nytt hus) och läge: *Före*, *Efter*,
   *Skillnad*, *Svep* (dra reglaget) eller *Sida vid sida*. Rutan *Så läser du kartan* förklarar färgerna.
4. **Hovra** över kartan: värdena för före, efter och skillnad står under kartan. Grå ytor är byggnader
   (tak, inte mark). Byggnadslagret och värden på tak slås av och på under kartan.
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

Skärmbilder (bred 1440 px och smal 800 px med `-smal`): `docs/bilder/demo-start.png`,
`demo-projekt.png`, `demo-karta.png` (+ `demo-karta-vatten.png`), `demo-fragor.png`, `demo-dagsljus.png`,
`demo-vatten.png` (`python tools/webb_skarmbild.py --demo`).
