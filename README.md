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

## Webben (lokalt, utan inloggning)

`webb/` och `src/webb/` är ett lokalt webbgränssnitt som kör Flodens utredningsfrågor (K20): välj
en fråga, se planen (rutor och vad som finns i cachen), kör, och se kartvy med lager (bas, scenario,
skillnad), varningar och mätvärden. Allt beräknas av kärnan; webben visar. **Det finns ingen
inloggning och ingen kontoserver**: API:t lyssnar på `127.0.0.1` och resultaten ligger på den egna
disken. Kontoserver ingår inte (driftplanen i `docs/ARKITEKTUR-DRIFT.md` beslutar det senare).
Start och API: se `webb/README.md`.
