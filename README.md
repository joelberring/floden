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
python3.12 -m venv .venv && source .venv/bin/activate   # kontrollera: python --version
pip install -e ".[dev]"          # Flodens Python-paket och verktygen; `.[konto]` räcker för inloggning
export PYTHONPATH=src            # om `floden` inte hittar paketen
(cd webb && npm ci)              # webbklientens beroenden (en gång)

floden demo                      # bygger ett påhittat kvarter och kör frågorna: cirka 30 sekunder
floden webb                      # bygger klienten vid behov och startar servern
```

Öppna sedan **http://localhost:8765** (`floden webb --port 9000` ändrar porten). `floden demo` kan
köras om utan kostnad: är inget ändrat tar det under en sekund, och `--tvinga` bygger om allt.
`floden demo --liten` är en grövre och snabbare variant (cirka 15 sekunder) som mest är till för test.

Kvarteret är **syntetiskt**: ett påhittat område med fyra kvarter, gator, gårdar och en skålformad
sänka, byggt med markmodellens egna byggare. Inga verkliga data och inga verkliga byggnader.

**Förslag till visning (5–7 punkter):**

1. Öppna **Frågor**. De tre korten överst är demon: dagsljus, vatten och luft (luft är märkt
   *Ej förkörd*: vindkomfortlösaren finns men en körning tar över tio minuter på CPU, se
   `exempel/fragor/luft_fore_efter.toml`).
2. **Dagsljus.** Tryck *Visa resultatet*. Lagret *Soltimmar · höjd − bas* visar var skuggan faller
   när en byggnad görs högre (blått är färre soltimmar). Byt till *nytt hus* för det andra fallet.
3. **Hovra** över kartan: värdena för bas, scenario och skillnad står under kartan. Grå ytor är
   byggnader (*tak, inte mark*): där visas inget markvärde. Slå på *Visa värden på tak* om du vill
   se dem ändå.
4. **Vatten.** Tryck *Visa resultatet* på vattenkortet. Rutan *Antaganden* överst visar regnet
   som lösaren faktiskt använde: Stockholmsregn 2024, 100 år, 360 min, klimatfaktor 1,4. Källa och
   faktor är körningens egna, inte webbens.
5. Skillnadslagret visar var vattnet dämms upp när det nya huset byggs. Växla mellan *Bas*,
   *Scenario* och *Skillnad* och jämför djupen.
6. **Varningar** står överst i resultatet. Där står bara det lösarna själva varnat för; en rad som
   *Inga varningar från lösarna* är ett verkligt besked.
7. Frågan om *hur det räknades*: allt går att följa till `run.json` per jobb (K0-manifest), och
   samma fråga ger samma svar (cachen, K22).

**Inloggning med Google** är avslagen som standard. Den slås på i `config.local.toml`
(`inloggning = "pa"` plus Googles klient-id och hemlighet) och beskrivs steg för steg i
`docs/KONTO.md` (avsnitt 2 för Google-klienten, avsnitt 8 för hur den är kopplad i webben). Med
inloggning på visas en *Logga in med Google*-knapp, *Inloggad som …* och *Logga ut* i sidhuvudet.

Skärmbilder: `docs/bilder/demo-start.png`, `demo-dagsljus.png`, `demo-vatten.png`
(`python tools/webb_skarmbild.py --demo`).
