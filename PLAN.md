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

## Spår L – Luft (3D)

Separat spår, licens MIT (`src/luft/`). Detaljer, matematik och färdplan i `docs/LUFT.md`.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| L-a | LBM-LES 3D, linjär spridning med adjungerad lösning, NO₂-kemi, vindbibliotek, statistik, export, CLI | – | klar |
| L-b | Serverkörning med CuPy, prestanda och minne | L-a, server | redo |
| L-c | Vindtunnelvalidering (CEDVAL/Michelstadt) | L-a, data | redo |
| L-d | Väggmodell, inflödesturbulens, rättade randvillkor, prestanda | L-a | klar |
| L-f | TVD-advektion och snabbare lösare (GPU-lösare kvar) | L-a | klar (CPU) |
| L-g | Emissionsmodell vägdamm (NORTRIP-typ) och avgaser | L-a | klar (kalibrering väntar på data) |
| L-k | Vindkomfort och vindfara för höga hus (NEN 8100, Lawson), före/efter | L-a | klar |
| L-h | Validering mot mätstationer | L-c, L-g | väntar |
