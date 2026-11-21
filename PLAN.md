# Plan

Milstolparna byggs i ordning. Inom en milstolpe kan uppdrag med uppfyllda beroenden köras parallellt av olika agenter.
Status: `redo` · `pågår` · `klar` · `blockerad` · `väntar` (väntar på något utanför projektet).

## M0 – Grund (vecka 1)

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T00 | Repo, mappstruktur, CI, `run.json`-manifest, konfiguration, testsvitens skelett | – | klar |

## M1 – Markmodell för provområdet

Provområde: ca 1 km² i Hägersten-Älvsjö med gator, kantstenar, en vägbank med trumma och lite grönyta.
Exakt avgränsning väljs av Joel och läggs i `data/omraden/prov1.gpkg`.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T01 | Markmodellen: laser, byggnader, brytlinjer, trummor, osäkerhet per cell | T00 | pågår |
| T01b | Gatuprofiler: parametrisk rekonstruktion av körbana, kantsten och gångbana | T01 (grundraster) | pågår |
| T09a | Testsvit del 1: syntetiska terränger med känt svar | T00 | klar |

**Klar när:** markmodellen för provområdet kan visas bredvid Lantmäteriets höjdmodell, och skillnaden i kantstenar
och trummor syns och är dokumenterad.

## M2 – Sänkgraf och statisk nivå

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T02 | Sänkhierarki, volymkurvor, flödesvägar, statisk fyllning | T01 | pågår |
| T09b | Jämförelse med Scalgo och SMHI för provområdet | T02 | väntar |

**Klar när:** var vattnet hamnar vid ett givet regn kan jämföras med Scalgo, och skillnaderna är förklarade.

## M3 – Dynamik i grafen

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T03 | Händelsestyrd grafmodell, enhetssvar, scenarier, osäkerhetsintervall | T02 | klar |
| T06 | Infiltration per markslag, grundvattentak, brunnar som sänkor | T01 | pågår |

**Klar när:** djup över tid i sänkorna går att räkna för hundratals regn på några sekunder, och resultatet
stämmer med de syntetiska fallen i testsviten.

## M4 – Projektlapp och före/efter (första användbara versionen)

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T04 | Lappar: import (LandXML), parametriska gator, lokal omräkning, före/efter | T02, T03 | pågår |
| T10 | Webbklient: karta, lager, lappar, före/efter-vy | T02 | pågår |

**Klar när:** Joel kan lägga in ett påhittat projekt i provområdet och se förändringen för grannarna på under en minut.

## M5 – 2D och hybrid

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T05 | SFINCS som 2D-referens, subgrid, koppling till sänkgrafen | T01, T03 | pågår |
| T09c | Environment Agency-testfallen och hybrid mot full 2D | T05 | klar |

## M6 – Ruttanalys och rapporter

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T07 | Ruttanalys: vägnät, fordon, entréer, kritiska avsnitt, vädervarning | T03 (räcker för första versionen) | pågår |
| T08 | Rapporter: mallar, textgenerering med kontroll av tal, export | T04, T07 | pågår |

## M7 – Ledningsnät och realtid (senare)

Ledningsnät kopplat i 1D (SWMM) och radarnederbörd. Kräver data från Stockholm Vatten och Avfall och beslut om
driftmiljö. Planeras när M4 är klar.

## Rekommenderad fördelning på agenter

- **En agent per uppdrag**, aldrig två i samma uppdrag samtidigt.
- Uppdrag som bara rör kontrakt och tester (T00, T09) passar en snabbare, billigare modell.
- Uppdrag med numerik eller geometrisk robusthet (T01b, T02, T03, T05) behöver den starkaste modellen och en
  människa som granskar mätvärdena.
- T03 innehåller frågor till Samuel. De samlas i `docs/MATEMATIK.md` i stället för att lösas av agenten.

## M8 – Buller enligt Nord2000 (parallellt spår, gren `buller-nord2000`)

Uppdragen och deras status står i `tasks/B00-buller-oversikt.md`; kontrakt i `docs/buller/KONTRAKT.md`.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| B3, B6 | Bullerscen, profiler, källsegment, fasadpunkter, banfinnare | – | klar (första version) |
| B4a | Akustiska grundfunktioner och baslinjekärna (inte Nord2000) | – | klar |
| B8, B9 | Operatorlager, klustring med felgräns, inkrementell omräkning, lägen och manifest | KB1–KB2 | klar (första version) |
| B1, B5 | Testfall grupp N och källmodell väg | källmapp | pågår |
| B4 | Referenskärna Nord2000 punkt-till-punkt | B1, källmapp | pågår |
| B7, B10–B13 | Snabbkärna, rapport, jämförelse, gradienter, vågfysik | B4 | väntar |

**Klar när:** rapportläget kör med en kärna som passerar testsvit grupp N, och en jämförelse mot en utredning i
etablerad programvara är redovisad.
