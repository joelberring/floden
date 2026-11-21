# B00 – Buller enligt Nord2000: översikt

**Mål:** vägtrafikbuller enligt Nord2000 Road i två lägen ur samma kärna: `skiss` (sekunder, märkt "ej för beslut")
och `rapport` (verifierbart: testfall, manifest, spårbar indata, oberoende jämförelse).

**Läs först:** `AGENTS.md`, `docs/buller/KONTRAKT.md`, `docs/buller/kallor.md`, `src/buller/typer.py`.

## Regler utöver AGENTS.md

1. Metoddokumenten är facit. Formler ur Nord2000 skrivs aldrig ur minnet. Saknas dokumentet: stoppa och skriv i `docs/buller/avvikelser.md`.
2. Tvetydigheter i metoden loggas i `docs/buller/tolkningar.md` med val, alternativ och skäl.
3. Ingen anpassning mot testfall. Inga korrektionsfaktorer.
4. Skiss och rapport går genom samma kod; bara toleransprofilen skiljer.
5. Rapportläget använder aldrig en kärna med `nord2000_konform = False`.
6. Kopiera inte text ur rapporterna. Hänvisa med rapportnummer och avsnitt. Tabellvärden läggs i datafiler med källa.
7. Håll dig till din mapp enligt `docs/buller/KONTRAKT.md`. Delade filer (`PLAN.md`, `STATUS.md`, `pyproject.toml`) ändras bara av den som samordnar.

## Arbetspaket

| Paket | Innehåll | Beroenden | Status |
| --- | --- | --- | --- |
| B1 | Källor och testdata: testfallsformat, körare (`testsvit` grupp N), digitaliserade fall | – | pågår |
| B2 | Utvärdering av färdiga komponenter (NoiseModelling, Embree m.fl.) | B3 | redo |
| B3 | Bullerscen: profilextraktion, markklass → impedansklass, källsegment, fasadpunkter | – | klar (första version) |
| B4a | Akustiska grundfunktioner och baslinjekärna (inte Nord2000) | – | klar |
| B4 | Referenskärna Nord2000 punkt-till-punkt | B1, metodrapporter | pågår |
| B5 | Källmodell väg (Nord2000 Road, svenska inställningar) | B1 | pågår |
| B6 | Banfinnare: direkta och reflekterade banor, korridorindex | B3 | klar (första version) |
| B7 | Snabbkärna (vektoriserad/GPU), ≤ 0,05 dB mot B4 | B4 | väntar |
| B8 | Operatorlager: överföringslager, klustring med felgräns, inkrementell omräkning | KB1–KB2 | klar (första version) |
| B9 | Lägen, toleransprofiler, manifest (frågetyper återstår) | B8 | klar (första version) |
| B10 | Rapportavsnitt och verifieringsbilaga | B9, B4 | väntar |
| B11 | Jämförelse mot etablerad programvara på 2–3 planområden | B10, utredningar från Joel | väntar |
| B12 | Gradienter och osäkerhet (endast skiss) | B7, B8 | väntar |
| B13 | Vågfysik för låga frekvenser (forskningsspår) | B3 | väntar |

## Acceptans (sammanfattning)

- **B3:** profiler stämmer mot enkel referens (≤ 1 cm på 10 000 slumpade snitt).
- **B4:** alla spridningsfall i grupp N inom publicerat intervall.
- **B6:** banor lika med brute force-sökning på testscen.
- **B7:** ≤ 0,05 dB per tersband mot B4 på testfall och 100 000 slumpade profiler.
- **B8:** trafikändring utan ny geometri = full körning (≤ 0,01 dB); uppmätt klustringsfel ≤ satt gräns i varje
  mottagare; inkrementellt = full omräkning.
- **B9:** samma fråga körbar i båda lägen; skillnaden skiss − rapport mäts och redovisas.
- **B10:** omräkning från bilagan ger samma resultat (≤ 0,01 dB).

## Stoppa och fråga

- Metodrapporter eller testfallsfiler saknas (se `docs/buller/kallor.md`).
- Ett testfall faller efter två ärliga försök.
- Riktvärden, trafikdata eller väderstatistik behövs för skarp användning och saknar angiven källa.
