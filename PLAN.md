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
| T01 | Markmodellen: laser, byggnader, brytlinjer, trummor, osäkerhet per cell | T00 | klar (syntetiskt; verkligt provområde väntar). Markslag ur DWG-ytor, belagd yta och ortofoto med prioritet och `markslag_kalla.tif`: klart syntetiskt (grenen `markslag-dwg`); stadens lagernamn för skraffyrer väntar |
| T01b | Gatuprofiler: parametrisk rekonstruktion av körbana, kantsten och gångbana | T01 (grundraster) | klar (syntetiskt; verkligt provområde väntar) |
| T01c | Hämta öppna data från Lantmäteriet (laser, ortofoto, byggnader) till ett verkligt provområde, lokalt: `floden data lantmateriet`, `floden omrade bygg`, `exempel/fragor/omrade_*.toml` (`docs/DATA.md`) | T01 | klar mot falsk server; körning mot riktiga Lantmäteriet och Joels `prov1.gpkg` väntar |
| T09a | Testsvit del 1: syntetiska terränger med känt svar | T00 | klar |

**Klar när:** markmodellen för provområdet kan visas bredvid Lantmäteriets höjdmodell, och skillnaden i kantstenar
och trummor syns och är dokumenterad.

## M2 – Sänkgraf och statisk nivå

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T02 | Sänkhierarki, volymkurvor, flödesvägar, statisk fyllning | T01 | klar (syntetiskt; verkligt provområde väntar) |
| T09b | Jämförelse med Scalgo och SMHI för provområdet | T02 | väntar |
| T02c | Takvatten (regn på tak avrinner längs fasaderna; okänd takform = brutet tak, ring = hälften till gården) och avtappning som uttrycklig parameter (standard 0 mm/h, per område) i statisk fyllning, dynamik (`vatten.dynamik`), 2D (Python och Rust), hybrid, lokal omräkning/K9 och webb; testfall A12–A13; `docs/VATTEN.md` | T02b, fas 3 | klar (syntetiskt; regeltolkningen granskas av Joel) |
| T02b | Slutsänkor (K2/v2): slutna kvarter som rotnod utan överfall med hinder, i sänkgraf, statisk fyllning, persistens, händelsestyrd dynamik (med avtappning mm/h), lokal omräkning och webb (varning, `slutsanka.tif`); testfall A10–A11 | T02, T03, fas 3 | klar (syntetiskt; avtappning efter ytkurva är en öppen fråga i `docs/MATEMATIK.md`) |

**Klar när:** var vattnet hamnar vid ett givet regn kan jämföras med Scalgo, och skillnaderna är förklarade.

## M3 – Dynamik i grafen

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T03 | Händelsestyrd grafmodell, enhetssvar, scenarier, osäkerhetsintervall | T02 | klar |
| T06 | Infiltration per markslag, grundvattentak, brunnar som sänkor | T01 | klar (syntetiskt; verkligt provområde väntar) |
| R1 | Regnbibliotek: Stockholmsregn 2024 (Tabell 7) som registrerad IDF-källa, klimatfaktor som uttrycklig parameter (standard 1,4), `--idf` i CLI | T03 | klar |

**Klar när:** djup över tid i sänkorna går att räkna för hundratals regn på några sekunder, och resultatet
stämmer med de syntetiska fallen i testsviten.

## M4 – Projektlapp och före/efter (första användbara versionen)

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T04 | Lappar: import (LandXML), parametriska gator, lokal omräkning, före/efter | T02, T03 | klar (syntetiskt; verkligt provområde väntar) |
| T10 | Webbklient: karta, lager, lappar, före/efter-vy; frågesidan kör K20-frågor (plan, körning, resultat) lokalt utan inloggning | T02, T20 | klar (syntetiskt; verkligt provområde väntar; kontoserver ingår inte, se `docs/ARKITEKTUR-DRIFT.md`) |

**Klar när:** Joel kan lägga in ett påhittat projekt i provområdet och se förändringen för grannarna på under en minut.

## M5 – 2D och hybrid

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T05 | SFINCS som 2D-referens, subgrid, koppling till sänkgrafen | T01, T03 | klar (syntetiskt; verkligt provområde väntar) |
| T09c | Environment Agency-testfallen och hybrid mot full 2D | T05 | klar |

## M6 – Ruttanalys och rapporter

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| T07 | Ruttanalys: vägnät, fordon, entréer, kritiska avsnitt, vädervarning | T03 (räcker för första versionen) | klar (syntetiskt; verkligt provområde väntar) |
| T08 | Rapporter: mallar, textgenerering med kontroll av tal, export | T04, T07 | klar (syntetiskt; verkligt provområde väntar) |

## M7 – Ledningsnät och realtid (senare)

Ledningsnät kopplat i 1D (SWMM) och radarnederbörd. Kräver data från Stockholm Vatten och Avfall och beslut om
driftmiljö. Planeras när M4 är klar.

## M8 – Gemensam kärna för alla domäner

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| Fas 0 | Planscenario K9, utredningsfråga K10, beräkningsgraf med cache, påverkansradie (grenen `fas0-2`) | T00 | klar |
| T20 | Körmotor: K20-frågor i fil, rutvisa lösare K21, K9/K3 på K1-rutor, cache K22, jämförelser, `floden fraga` | Fas 0, T04 | klar |
| G1 | Gemensam geometri: K1 v2 (`byggnader.gpkg`, `terrang.tif`, `vegetation.tif`), `karna.geometri`, K9 `byggnad_*`/`vegetation` på v2, fasadpunkter K11, markklass per domän | T20 | klar (mergad 2026-11-21; platta tak och 10 m-standardhöjd granskas av Joel) |
| Demo 1 | Visningsbar lokalt: `floden demo` (syntetiskt kvarter, dagsljus och vatten förkörda, luft ej förkörd), `floden webb` (klient och API på en port), byggnadslager, takmask, värdeavläsning, regnkälla i resultatet, inloggning kopplad (av som standard), README "Visa Floden på din dator" | G1, T10, R1, D1 | klar (Google-inloggning prövad bara mot falsk leverantör) |
| Kartverktyg | Kartverktyg: lager och planförslag i kartan: en kartmotor (MapLibre, exakt SWEREF-omräkning), lagerpanel (Underlag, Geometri, Planförslag, Resultat), rita och ändra hus, K9 kontrollerat av kärnan, K20 och körning i samma karta, scenarier som lokala filer; ritlogik porterad från O-Tid | Webb-B, T10, G1 | klar (se STATUS; 3D-vy och verkligt ortofoto/WMS ej prövade) |
| 3D-vy i Three.js | 3D-vy som egen vy i lagerpanelen (Three.js, laddas vid behov): terräng och byggnader ur K1 v2 via `/api/3d/` (rutvis, nedsamplad, kvantiserade binärbuffertar), ritat förslag live, soltimmar, VSC på fasader och vattenyta ur beräkningarna, före/efter/skillnad/svep, hovring som i kartan | Kartverktyg, G1 | klar (prövad med programvaru-WebGL; luft, buller, vegetation och vattnets tidsreglage ej i 3D, se `docs/WEBB3D.md`) |
| Webb-B | Webben begriplig för planerare: Start/Projekt/Karta/Utredningsfrågor/Om beräkningarna, demokvarteret som tydligt påhittat projekt med siffror ur körningarna, en statuskälla (`/api/moduler`), inget som låtsas fungera (förvaltning och organisationsväljare bara med inloggning och kontodata), tester mot platshållare | Demo 1, T10 | klar (se STATUS) |
| Dagsljus fas 1 | Horisontprofiler, soltimmar, SVF och VSC (BRE) som rutvisa lösare, facittester (grupp E) och `docs/DAGSLJUS.md` | Fas 0, T20 | klar |
| Dagsljus fas 1b | Dagsljus på K1 v2: `karna.geometri.las_ytmodell`, fasadpunkter K11, K9-scenarier före/efter, K ≥ 180 för skuggstudier | G1, Dagsljus fas 1 | klar |
| G2 | Luft och buller på gemensam geometri: markklass per domän (buller, luft) i `data/markklasser.toml`, luft ur K1 v2 (`karna.geometri.mosaik`/`luftgeometri`, lösarna `luft.vindkomfort`/`luft.halt`, K9 med jämförelse), bullerscen och mottagare ur K1 v2 och K11 (`buller.scen.k1v2`) | G1, Spår L (L-a, L-d, L-k), M9 (B3) | klar (PR öppen; buller som K21-lösare och hål i byggnader kvar) |
| Dagsljus fas 5 | Dagsljusfaktor (DF) i typrum som riskklassning ovanpå VSC på K11-punkter (fönsterarea, rumsdjup, glasning, inre reflektion); alternativt reflekterat ljus (externt reflekterad komponent) och Radiance-referens för utkragningar | Dagsljus fas 1b | redo (förslag, Joel bekräftar) |

Domänerna ansluter enligt `docs/KARNA.md` (nodgraf) eller `docs/ANSLUTA_LOSARE.md` (rutvisa lösare).

## M10 – Modern matematik och produktionspass (Neon, 2026-11-22 till 2026-11-23)

Tillägg av en annan AI (Neon) ovanpå `6ef22ae`, införda utan uppdrag i planen. Granskade och dokumenterade i
`docs/BESLUT.md` (2026-11-23) och `STATUS.md`. **Fristående** betyder testad men inte inkopplad i något
användarflöde (se modulernas docstrings).

| Del | Beskrivning | Status |
| --- | --- | --- |
| Vattenkärnan | Hastighet v = \|q\| / max(h_före, h_efter), punkt-implicit friktion med u_max i CFL, ny subgrid-konveyans, brytlinjer och öppningar i `losare_2d._kor_2d` (`cbaba1d`, `7132e0e`, `1350379`); facittest för hastighet och CFL tillagda 2026-11-23 | klar, beslutet i `docs/BESLUT.md` |
| Q2 bifurkationer | `sankgraf.bifurkation` (K34, K35), a posteriori-felindikator `tvad.felindikator` och `POST /api/analys/bifurkationer` (K36) med panel i webbklienten; `tools/exportera_q2_underlag.py` | klar, inkopplad i webben (endpunkten) |
| Ekologi (Etapp 2) | K44 (artprofil) och K45 (lösaren `ekologi.konnektivitet`, `stromtathet.tif`, `ekologi.json`, `POST /api/analys/ekologi`), demofrågan `demo_ekologi`, ämnet Ekologi i webbflödet, `docs/EKOLOGI.md`. Allt skiss. | klar (skiss; profilparametrar utan källa) |
| Ekologi, habitatnätverk (K47) | Nätverk med signalarter som konfiguration (`data/ekologi/natverk/*.toml`, källa och sida per värde), p(d) = 2^(−d/d50) med märkt antagande d50 = tröskel/2, källbaserat intervall, ändligt motstånd som förval, NMD-inläsare (`data_inlasning.nmd`), VMI-lager (`data_inlasning.vmi`), nätverksval i webb och API, `docs/EKOLOGI.md` omskriven efter `docs/ekologi/habitatnatverk-svensk-stadsmiljo.md`. Metodstatus `skiss`: parametrar källbelagda (kommunala modellval), biologisk säkerhet låg. NMD och VMI är valfria lager i geografins manifest (K46, `floden geografi mappa --nmd --vmi`). Habitat: kandidater 111–114/121–124 (barrskog) och 116/117/126/127 (ädellöv), snävt urval som jämförelse, resultat kallat "potentiell livsmiljö, okänd kvalitet". **Kvar:** valfritt lager för inventerade gamla ädellövträd (punkter som noder även på öppen mark; finns i datamodellen som `[[valfritt_lager]]`, inte byggt), lösaren ska läsa NMD/VMI ur projektets geografi (`lager.nmd`) i stället för indataroller, och kommunförvalet (`data/ekologi/kommuner.toml`) ska flyttas till `data/kommuner/<kommun>.toml` (kräver nytt avsnitt i den strikta `src/kommuner/konfig.py`) | klar (syntetiskt; NMD och VMI inte körda mot riktiga filer, se `docs/DRIFT.md`; grenen `ekologi-natverk`) |
| Ekologi, nästa steg | Köra NMD- och VMI-inläsarna mot riktiga filer på servern; R2-rapport för ekologi med nätverkens källrad och antaganden; lokal körning av Stockholms nätverk mot stadens biotopdatabas; registrera lagren i `src/geografi` när den finns på main | väntar |
| Fristående moduler | CELF i ruttanalysen (`rutt.kritiska`), `luft.kanslighet`, `buller.optimering`, `dagsljus.intervall`, `dynamik.intervall`, `rapport.r2_domaner`, `luft.partiklar`, `dagsljus.uppdatera_horisont_inkrementellt`, `buller.operator.energi.pruna_med_energibudget` | klar som moduler, fristående |
| Göteborg | `data_inlasning.goteborg`, lagerprofil `goteborg_baskarta`: testkommun i EPSG:3007; formatet är antaget och aldrig provat mot tjänsten, testerna är syntetiska | klar (syntetiskt) |
| Webb och drift | ASGI-adapter (`webb.asgi`), projektisolerad 3D-terrängcache, 2D/3D-svep, WebGPU-motor för dagsljus med fallback, WGSL-skuggare, säkerhetsfixar inför server | klar |
| Övrigt | alla 13 SWEREF 99-zoner (EPSG:3006–3018), SMHI MetObs-klient, tvåfraktions-PM10 (`luft.partiklar`, fristående) | klar |

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
| L-i | Luft på gemensam geometri (K1 v2, K9, markklass) och K20-lösare | L-a, G1 | klar (geometri och lösare; webb väntar) |

## M9 – Buller enligt Nord2000 (parallellt spår)

Uppdragen och deras status står i `tasks/B00-buller-oversikt.md`; kontrakt i `docs/buller/KONTRAKT.md`.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| B3, B6 | Bullerscen, profiler, källsegment, fasadpunkter, banfinnare | – | klar (första version); scen och mottagare ur K1 v2/K11 klara (G2) |
| B4a | Akustiska grundfunktioner och baslinjekärna (inte Nord2000) | – | klar |
| B8, B9 | Operatorlager, klustring med felgräns, inkrementell omräkning, lägen och manifest | KB1–KB2 | klar (första version) |
| B1, B5 | Testfall grupp N (lokalt, ej i repot) och källmodell väg med direktivitet | källmapp | klar (första version) |
| B4 | Referenskärna Nord2000: plan mark, terräng och skärmar (delmodell 1–9) inkopplade med väder (P-S2: 33 av 80 fall godkända, 16 ska utredas, 4 underkända, se `docs/buller/logg/P-S2.md`); skog, reflexer och kurvig väg/stadsgata/årsmedel i köraren återstår; `nord2000_konform = False` | B1, källmapp | pågår |
| B7, B10–B13 | Snabbkärna, rapport, jämförelse, gradienter, vågfysik | B4 | väntar |

**Klar när:** rapportläget kör med en kärna som passerar testsvit grupp N, och en jämförelse mot en utredning i
etablerad programvara är redovisad.

## R – Rapporter i två steg, styrda via MCP

AI frågar, väljer körningar och skriver; Floden räknar och äger varje tal (belägg per värde). Se `docs/MCP.md`.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| R0 MCP-server | Floden som MCP-server (`src/floden_mcp/`, `floden mcp`, stdio och Streamable HTTP): läs-, plan-, kör- och loggverktyg över samma kärna som webben, belägg på varje tal, dataklasser med enkel policy, körlogg (hashkedjad JSONL), karta som PNG, `docs/MCP.md` | K0, K9, K20–K22, D1 (HTTP) | klar (alla metoder märkta skiss) |
| R1 Påståenden och belägg | Påståendemodell (text + belägg per tal), kontroll att varje tal i en text finns i en körning, skissrapport: K32/K33, frågekatalogen (SK-frågor mappade), deterministisk kontroll inför export, skissunderlag skyfall nivå 1 med Word och PDF, MCP-verktyg, referensflöde, rapportlista i webben (`docs/RAPPORTER.md`) | R0 | klar (bara skyfall, nivå 1; inställningar och språkprofil "sbk" tillagda 2026-11-22) |
| R2 Beställning, frågekatalog, mottagare | Beställning (K30: syfte, skede, mottagare, ämnen, frågor, scenario) styr urval och ingress; frågekatalogen i repot nås via `GET /api/fragekatalog` och MCP `fragekatalog_sok` med markering per fråga; skissunderlag dagsljus och sol (`docs/RAPPORTER.md`) | R1 | delvis (beställning, katalog och dagsljus/sol klara på skissnivå via MCP och API; saknas: beställningsformulär i webben, luft och buller, SVF och höstens ekvinox i körningen) |
| R3 Formell rapport (steg 2) | Rapport med validerade metoder (`rapport`-status), granskning och signering av människa | R1, R2, validering | väntar |
| R4 Roller, modellval, datapolicy | Roller (utforskare, skribent, granskare), vilken modell får vilken dataklass | R0, D1 | väntar (maskinåtkomst med token över HTTP, projektroller i verktygen och spärren för icke-öppna data: se R4a) |
| R4a MCP på servern | Personliga MCP-token, `/mcp` bakom kontoskyddet på floden.berring.se, projektroller i verktygen, spärr för icke-öppna data (K38); steg 2: OAuth för Claude-appen | R0, D1, S1 | klar (steg 1 och 2; riktig koppling från Claude-appen ej provad) |
| R5 Export och arkiv | Export (PDF/Word) med körloggen som bilaga, arkivering och spårbarhet | R3, R4 | väntar |

## Drift – konton (D)

Se `docs/ARKITEKTUR-DRIFT.md` och `docs/KONTO.md`.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| D1 Konton | Google-inloggning nu, SSO förberett: `src/konto/` (OIDC med PKCE, SQLite-konton, organisation/förvaltning/projekt, sessioner med CSRF, HTTP-adapter, läge `inloggning = "av"`); Entra/generisk OIDC finns men är inte på | BESLUT "Drift" | klar (ansluten i `src/webb/api.py`, se `docs/KONTO.md` avsnitt 8); riktig Google-klient på plats (S3, 2026-11-23) |

## Drift – server (S)

**Etapp 3 (pågår, Joel 2026-11-23): geografi först.** Ordningen är S5 mappa `goteborg-test`, S6 mät den, S7 Hägersten-Älvsjö med
Skärholmen, S8 Stockholm (`docs/ARKITEKTUR-DRIFT.md` 0b). Kön och demoläget (tidigare S5 och S6) är S5k och S6d.

Joels beslut 2026-11-22 (`docs/BESLUT.md`): basserver hos Hetzner, arbetare vid behov, egen bucket i O-Tids Object
Storage, månadsbudgetar och demo först. Se `docs/DRIFT.md`.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| S1 Avbildning | `Dockerfile` (webb, Rust-verktyg, Python), `deploy/start.sh` (vägrar starta utan inloggning), `docker-compose.prod.yml` med Caddy, avbildning i ghcr.io efter grön CI | D1 | klar (live 2026-11-23) |
| S2 Basserver | Eget Hetzner Cloud-projekt `floden`, CX23 i hel1, DNS `floden.berring.se`, Google-klient | S1 | klar (servern live 2026-11-23, https://floden.berring.se) |
| S3 Inloggning i drift | Riktig Google-klient, första administratören, inbjudningar | S2 | klar (Google-inloggning och superadmin på plats 2026-11-23; inbjudningar sköts nu i webben, se E1) |
| S4 Förråd och backup | Bucket `floden` i O-Tids Object Storage, sha256-nycklar, mellanlager på disk, presignerade URL:er till arbetarna, timkopia av `konto.sqlite` (backup) | S2 | väntar (Joels beslut om backup och bucket; ingår inte i E1) |
| S5 Geografi `goteborg-test` | Geografin som objekt (K46, `src/geografi/`): `floden geografi mappa goteborg-test --konto <superadmin>` hämtar och bygger kärna (Haga, 500 m) + buffert (dagsljus H/tan 5° och tillrinning uppströms), innehållsadresserade lager och manifest; `floden projekt skapa --geografi goteborg-test@v1 --avgransning ...` med scenario som diff (K9) och körning av skyfall och dagsljus (`docs/DRIFT.md`, `docs/DATAKONTRAKT.md` K46) | S3, E1 | **pågår (etapp 3)**: kod, tester och dokument klara; ej körd mot Lantmäteriet och Göteborgs tjänster (se STATUS) |
| S6 Mätning av `goteborg-test` | Tid, minne och disk per ruta och körning på servern; ersätter uppskattningarna i `docs/ARKITEKTUR-DRIFT.md` 5 | S5 | väntar |
| S7 Hägersten-Älvsjö med Skärholmen | Geografi nummer två (en stadsdel), batchkörning på stor maskin (klass `batch`, CCX53/CCX63) | S6 | väntar |
| S8 Stockholm | Hela Stockholm som geografi | S7 | väntar |
| S5k Kö, arbetare och kostnadsbok | Jobbkö med pull över HTTPS (O-Tids `geo-server.ts`), klass `liten`; kostnadsbok, månadstak per klass, uppskattning och godkännande per körning, larm 50/80/100 %, daglig avstämning mot leverantören (var S5 före "geografi först", 2026-11-23) | S4 | väntar |
| S6d Demoläge | Färdigräknade exempel, besökare läser och gör lätta skisser, tunga körningar bara för superadmin (var S6) | S5k | väntar |
| S7b Benchmarksvit | `floden benchmark`: fasta fall (skyfall, dagsljus, luft, buller), tid (median), minne (RSS och tracemalloc) och avvikelse mot facit, format `floden-benchmark/v1` (K37), jämförelse `--jamfor`; GPU-prov i webbläsaren `#/prov/webgpu` (superadmin) mot serverns f64-facit (`docs/benchmark/README.md`, `docs/WEBGPU-ARKITEKTUR.md` 5) | D1 | klar för servern (baslinje i delad container, `docs/benchmark/2026-11-23-container.json`); GPU-provet är byggt men inte körts på grafikkort |
| S9 GPU | Klass `gpu` hos Verda (L40S), L-b-mätning, vindbibliotek för ett kvarter med höga hus | S5k, L-b | väntar (Joel: konto och budget) |
| S10 Kvoter | Kvot per konto och organisation (förval 0), inbjudan med kvot; först därefter delning | S5k, S6d | väntar |

## Drift – etapp 1 i webben (E)

Etapp 1 av driftplanen (`design/server-och-drift.md`): det som behövs för att användare ska kunna ta sig in och arbeta på den
körande servern, utan serverkommandon. Backup (S4) ingår inte.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| E1 Nycklar, administration, städning, MCP | **Nyckelsida** (`/konto/nycklar`, `#/nycklar`): egna API-nycklar för Lantmäteriet och Anthropic, krypterade med Fernet i `konto_nyckel`, hemlighet i `FLODEN_NYCKELHEMLIGHET` (avstängd utan den), visas aldrig igen (bara datum och fyra sista tecken), aldrig i loggar eller svar; Lantmäteri-hämtningen använder kontots nyckel när inloggning är på. **Administration** (`/konto/admin`, `#/administration`, superadmin och organisationsadmin): bjud in till projekt (`lasare`/`utredare`), se inbjudningar och medlemmar, ändra och ta bort roll, spärra konto. **Städning:** arbetsbänken ut ur menyn och Om beräkningarna (bara adressen, bara superadmin eller lokalt); startsidan visar kontots projekt (`/api/projekt` per konto, demokvarteret inräknat); länkarna Nycklar och Administration i sidhuvudet. **MCP:** `projekt_lista` per konto; nycklar och administration når inte AI:n (`docs/MCP.md`). Dokumenterat i `docs/DRIFT.md`, `docs/KONTO.md` avsnitt 10-11, `deploy/prod.env.example` och `docker-compose.prod.yml` | S3, D1, R0 | klar (PR öppen; klienten är typkontrollerad och enhetstestad men inte prövad i webbläsare; `FLODEN_NYCKELHEMLIGHET` ska sättas på servern, se STATUS) |


## Sist – OAuth för Claude-appen (parkerat 2026-11-23, Joels beslut)

Claude-appen ansluter i dag med en personlig MCP-token som begärandehuvud (`authorization: Bearer fl_mcp_…`,
autentisering "Ingen inloggning"). OAuth-flödet (PR #58, #60–#64) når hela vägen till godkännandet men slutar
utan kod: loggen 2026-11-23 18:3x visar `GET /oauth/authorize -> 200`, `POST /oauth/authorize -> 302` och
sedan inget `/oauth/token`, och varken "kod utfärdad" eller "godkännande avvisat" loggas. Nästa steg när det
tas upp: logga vart 302:an pekar (felkod eller kod, aldrig koden själv) och prova hela flödet mot en lokal
Claude-liknande klient. Rättat hittills: Origin claude.ai (#60), Origin null (#61), okänt scope (#62),
saknade formulärfält (#64).
