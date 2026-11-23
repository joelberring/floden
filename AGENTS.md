# Regler för agenter

Gäller för alla AI-agenter och människor som arbetar i projektet. Vid konflikt med ett uppdrag gäller den här filen.

## Grundprinciper

1. **Fysiken är facit, AI är verktyg.** Alla djup, flöden och tider kommer från deterministiska beräkningar.
   En språkmodell får aldrig hitta på eller "uppskatta" ett tal som visas för en användare.
2. **Mät innan du påstår.** En ändring som påverkar resultat ska köras mot testsviten (`docs/VALIDERING.md`)
   och mätvärdena ska skrivas i `STATUS.md`. "Ser rimligt ut" räcker inte: ett djup som är 15 cm fel ser lika
   rimligt ut som ett rätt.
3. **Allt är reproducerbart.** Varje körning skriver ett manifest (`run.json`, se `docs/DATAKONTRAKT.md`) med
   kontrollsummor för all indata, kodversion och parametrar. Samma manifest ska ge samma resultat.
4. **Recept i stället för filer.** Härledd data (markmodell, sänkgraf, resultat) sparas som recept + cache.
   Rådata ändras aldrig.
5. **Kontrakten är heliga.** Ändra inte ett format i `docs/DATAKONTRAKT.md` utan att höja dess version och
   skriva varför i `docs/BESLUT.md`. Andra agenter bygger mot kontrakten parallellt.
6. **Små steg som går att visa.** Varje arbetspass ska sluta med något som går att köra och titta på
   (en bild, en tabell, ett test som går igenom).

## Data och säkerhet

- **Inga personuppgifter** i repo, loggar eller prompter. Samrådsyttranden, köpeskillingar och liknande används inte.
- **Ledningsdata kan vara skyddsklassade.** De checkas aldrig in och skickas aldrig till en extern AI-tjänst.
  Kod som använder dem testas med syntetiska ledningsnät.
- **Stadens data som inte är öppen** (baskarta, tätare laser) ligger utanför repot i en lokal datamapp
  som anges i `config.local.toml` (finns inte i git).
- Öppna data får laddas ner och cachas, med källan angiven.

## Kod

- **Licens:** GPL v3 för kärnan (beslut i `docs/BESLUT.md`). Kod från SFINCS får lånas med källhänvisning.
  Undantag: `src/luft/` är MIT och får inte importera SFINCS-härledd kod. Repot som helhet: `GPL-3.0-or-later AND MIT`.
- **Språk:** Python för prototyper och dataflöden. Rust för beräkningskärnor när en prototyp är validerad.
  TypeScript för webbklienten.
- **Koordinatsystem:** all intern beräkning i SWEREF 99 18 00 (EPSG:3011), höjder i RH 2000.
  Konvertera vid inläsning, aldrig mitt i en beräkning.
- **Enheter:** meter, sekunder, kubikmeter, mm/h för regnintensitet. Skriv enheten i variabel- och kolumnnamn
  (`depth_m`, `rain_mm_h`).
- **Tester:** varje modul har enhetstester. Numeriska moduler har dessutom minst ett fall med känt svar.
- **Inga tysta fel.** Hellre ett tydligt fel än ett resultat som kan vara fel. Flagga celler och noder
  med osäker data i stället för att gissa.

## Stoppvillkor – fråga en människa (Joel) innan du går vidare

- Ett kontrakt i `docs/DATAKONTRAKT.md` behöver ändras på ett sätt som bryter mot andra uppdrag.
- Ett beslut i `docs/BESLUT.md` verkar fel.
- Du behöver data som inte finns i öppna källor eller i den lokala datamappen.
- Ett resultat avviker från facit mer än gränserna i `docs/VALIDERING.md` och du inte hittar orsaken på
  rimlig tid.
- Något kräver kostnad (molnmaskiner, betaldata) eller kontakt med en extern part.
- Matematiska frågor som hör till forskningspartens del (`tasks/T03`, `docs/MATEMATIK.md`): formulera frågan och lämna den, gissa inte fram teori.

## När ett arbetspass är klart

Skriv i `STATUS.md`:

```
## ÅÅÅÅ-MM-DD – Txx – <kort rubrik>
Gjort: …
Mätt: <mått mot testsviten, med siffror>
Öppet: …
Nästa: …
```

Uppdatera statusen för uppdraget i `PLAN.md` (`redo`, `pågår`, `klar`, `blockerad`).
