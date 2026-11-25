<!-- GENERERAD av tools/generera_om.py ur data/om/floden.toml. Redigera inte. -->

# Om Floden

Uppdaterad 2026-11-25. Floden är en öppen modell som räknar skyfall, dagsljus och sol, ekologi och luft på gatunivå, före och efter ett förslag. Buller finns som metodkärna under utveckling. Floden räknar och språkmodellen äger aldrig ett tal: varje tal har belägg i en beräkning. Den här sidan säger vad Floden kan nu, vad planen är och hur Floden förhåller sig till andra verktyg, med källa för varje uppgift.

**Metodstatus:** Alla metoder är skisser: ingen metod är klassad som rapportmetod av en människa. Skiss: 16 av 16 metoder i registret. Klassningen sätts bara i `data/mcp/metodstatus.toml`.

## 1. Vad Floden kan nu

### Arbetsflödet

1. **Geografi.** En kommun är en konfigurationsfil (`data/kommuner/<id>.toml`) med källor och licenser. `floden geografi mappa` hämtar Lantmäteriets öppna data eller kommunens filpaket och bygger kärna och buffert som innehållsadresserade lager. Göteborg är testkommun; Stockholm kommer efter mätningen.
2. **Projekt.** Nytt projekt väljer geografi och ritar en avgränsning (rektangel eller polygon). Roller (läsare, utredare) och organisationens delade projekt styr vem som ser vad. Demokvarteret är ett tydligt märkt påhittat projekt.
3. **Förslag.** Rita hus med takform, mark i nivåer, hårdgjort, vegetation, vatten, brytlinjer och öppningar i ritlager, med fäst, rät vinkel, ångra och pekskärm. Skarvkontrollen varnar där ny mark möter gammal. 3D-förhandsvisning och sparade förslag.
4. **Frågor.** Välj frågor ur frågekatalogen per ämne. Planen visar vad som räknas och vad som redan finns i cachen. Räkna förslaget, eller läget idag utan förslag. Beräkningen sparar ett manifest med kontrollsummor för all indata.
5. **Resultat.** Flikar per ämne med Före, Efter, Skillnad, Svep och 3D, alla analyser som kartlager, värden under pekaren och Visa osäkerhet med intervall och känsliga överfall.
6. **Rapport.** Beställning (syfte, skede, mottagare) styr frågorna och ingressen. R1 är skissunderlag för skyfall, R2 lägger till dagsljus och sol, ekologi och luft (luft via MCP). Varje tal är en platshållare med belägg; Floden fyller talen och kontrollerar alla påståenden före export till Word och PDF. Buller är spärrat.
7. **MCP.** En AI-klient (Claude Desktop, Claude Code, lokal modell) styr samma kärna: projekt, förslag, beräkning, resultat och rapport. AI:n äger aldrig ett tal; varje tal har belägg, varje anrop loggas i en körlogg och bara öppna data lämnas ut som förval.

### Skyfall

**Kan**

- Var vattnet samlas och hur djupt vid ett givet regn, som statisk fyllning av terrängens sänkor, med takvatten och avtappning som uttryckliga parametrar.
- Djup över tid i sänkorna utan tidsstegning (händelsestyrd dynamik), för många regn.
- Före och efter ett ritat förslag, där bara det som berörs räknas om.
- Osäkerhetsintervall och känsliga överfall (bifurkationer), som visas i resultatkartan.
- Skarvkontroll: varnar där ny mark ger nya eller djupare sänkor vid kanten.
- 2D lokal tröghet och hybrid mot sänkgrafen, för kommandoraden och MCP (inte som knapp i webben).

**Metoder och metodstatus**

| Metod | Metodstatus | Var den nås |
| --- | --- | --- |
| Sänkgraf som sammanslagningsträd | skiss | I användarflödet |
| Händelsestyrd dynamik i grafen | skiss | I användarflödet |
| 2D lokal tröghet, subgrid och hybrid | skiss | Kommandorad, MCP och testsvit |
| Bifurkationsdetektor | skiss | I användarflödet |
| Monoton intervallanalys för vatten | skiss | I användarflödet |

**Testat**

- Testsvit grupp A (A1 till A13 och A5b, syntetiska fall med känt svar) och Environment Agency-fallen EA1, EA2 och EA8A samt Hunters analytiska fall D1 (källa: [`docs/VALIDERING.md`](https://github.com/joelberring/floden/blob/main/docs/VALIDERING.md))
- Hela facittestsviten för alla ämnen (`floden testsvit`), godkända fall: 18 av 18 (källa: [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md))

**Begränsningar**

- Skiss: ingen metod är klassad som rapportmetod.
- Inget ledningsnät är kopplat. Ledningsnät i 1D och radarnederbörd ligger i planen.
- Provat mot syntetiska kvarter och en påhittad geografi, inte mot riktiga data på servern.
- Det snabba grafspåret förutsätter omedelbart överfall och plan vattenyta i varje sänka; felindikatorn flaggar där 2D behövs.

### Dagsljus och sol

**Kan**

- Soltimmar på marken, himmelsandel (SVF) och vertikal himmelskomponent (VSC) på fasadpunkter enligt BRE.
- Före och efter ett förslag, med intervall för SVF.
- Kartor, 3D-vy med VSC på fasader och skuggstudier.
- WebGPU-motor i webbläsaren med reservväg på CPU; serverns f64-beräkning är facit.

**Metoder och metodstatus**

| Metod | Metodstatus | Var den nås |
| --- | --- | --- |
| Horisont, soltimmar, SVF och VSC | skiss | I användarflödet |
| Dozier–Frew: horisont i linjär tid | skiss | Bara i API:t |
| Monotona SVF-intervall | skiss | I användarflödet |

**Testat**

- Testsvit grupp E (E1 till E7), godkända fall: 7 av 7 (källa: [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md))
- Solposition mot NREL SPA (pvlib, 2000 till 2040): största avvikelse i höjd: 0,013 grader (källa: [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md))

**Begränsningar**

- Skiss: ingen metod är klassad som rapportmetod.
- Dagsljusfaktor i typrum (DF) och reflekterat ljus finns inte; Radiance-referens saknas. Se planen.
- WebGPU räknar i enkelprecision och SVF-fältet ligger i ett fåtal celler över toleransen (se mätningarna).

### Ekologi

**Kan**

- Konnektivitet i ett habitatnätverk för signalarter: kretsteoretisk strömtäthet, sannolikhet för konnektivitet (PC), ekvivalent anslutet område (ECA) och bidrag per habitatyta (dPC).
- Nätverk som konfiguration (`data/ekologi/natverk/`) med källa och sida per värde; NMD- och VMI-lager som valfria lager i geografin.
- Före och efter ett förslag, med källbaserat intervall.

**Metoder och metodstatus**

| Metod | Metodstatus | Var den nås |
| --- | --- | --- |
| Kretsteoretisk konnektivitet | skiss | I användarflödet |
| PC, ECA och dPC | skiss | I användarflödet |
| Monotona osäkerhetsintervall | skiss | I användarflödet |

**Testat**

- Analytiska facitfall G1 till G6 (`tests/ekologi/test_facit_g.py`) och lösaren i K21 (G7) (källa: [`tests/ekologi/test_facit_g.py`](https://github.com/joelberring/floden/blob/main/tests/ekologi/test_facit_g.py))

**Begränsningar**

- Skiss: biologisk säkerhet är låg och en del profilparametrar saknar källa.
- Resultatet kallas potentiell livsmiljö, okänd kvalitet, och är inte verifierat mot fältdata.
- NMD och VMI är inte körda mot riktiga filer.
- I kartverktyget finns ekologi bara för demokvarteret.

### Luft

**Kan**

- Vindfält och spridning i 3D (LBM-LES med adjungerad lösning och NO₂-kemi) på gemensam geometri.
- Vindkomfort och vindfara för höga hus (NEN 8100, Lawson), före och efter.
- Överskridanden av miljökvalitetsnormer som intervall ur en given halttidsserie och en angiven bakgrund.

**Metoder och metodstatus**

| Metod | Metodstatus | Var den nås |
| --- | --- | --- |
| Vindfält och spridning som M-matris | skiss | Kommandorad, MCP och testsvit |
| Adjungerad känslighet och åtgärdsval | skiss | Fristående modul |
| Monotona MKN-intervall | skiss | Bara i API:t |

**Testat**

- Analytiska fall L1 till L8 (`docs/LUFT.md`), bland annat Poiseuille-flöde och adjungerad dualitet (källa: [`docs/LUFT.md`](https://github.com/joelberring/floden/blob/main/docs/LUFT.md))
- Körtid för en vindkomfortberäkning på CPU: mer än 10 minuter (källa: [`data/mcp/metodstatus.toml`](https://github.com/joelberring/floden/blob/main/data/mcp/metodstatus.toml))

**Begränsningar**

- Skiss: ingen metod är klassad som rapportmetod.
- En beräkning tar lång tid på CPU, så luft är inte förberäknad i demon.
- Floden räknar inte fram timserien för halter; den kommer utanför Floden. Validering mot mätstationer väntar.
- Luft finns inte som knapp för rapport i webben.

### Buller

**Kan**

- Bullerscen, källmodell för väg, banor, fasadpunkter, operatorlager och klustring med felgräns.
- Referenskärna för Nord2000 (delmodell 1 till 10 inkopplade) som provas mot de officiella testfallen.

**Metoder och metodstatus**

| Metod | Metodstatus | Var den nås |
| --- | --- | --- |
| Nord2000 referenskärna | skiss | Kommandorad, MCP och testsvit |
| CELF: submodulärt val av åtgärder | skiss | Bara i API:t |

**Testat**

- Nord2000, officiella testfall RL 18-18 (grupp N): godkända: 55 av 80 (källa: [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md))
- Av de övriga: ska utredas: 16 fall (källa: [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md))
- Av de övriga: underkända: 5 fall (källa: [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md))
- Av de övriga: ej körbara: 4 fall (källa: [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md))

**Begränsningar**

- Under utveckling: kärnan är inte Nord2000-konform och rapportläget är spärrat.
- Buller finns inte i användarflödet i webben, bara som kommandorad och modul.
- Testfallens facit ligger i en lokal källmapp och finns inte i det publika repot.
- Årsmedel, kurvig väg och skog är bara delvis klara; se kända avvikelser i Matematik och forskning.

## 2. Plan

Ur `PLAN.md`; inga datum som inte står där.

| Område | Vad | Status | Uppdrag |
| --- | --- | --- | --- |
| Riktig geografi och mätning | Köra geografin goteborg-test mot Lantmäteriet och Göteborgs tjänster, mäta tid, minne och disk per ruta (S5, S6), sedan Hägersten-Älvsjö med Skärholmen (S7) och Stockholm (S8). | pågår | S5 till S8 |
| Jämförelse med Scalgo och SMHI | Jämföra var vattnet hamnar för provområdet och förklara skillnaderna (T09b). | väntar | T09b |
| Jämförelseprov mot referensverktyg | Samma fall i Floden och ett referensverktyg per ämne, med tid och avvikelse (J1, förslag som projektets ägare bekräftar). | förslag | J1 |
| Buller enligt Nord2000 | Klara kvarvarande testfall i grupp N, årsmedel, kurvig väg och skog, så att rapportläget kan öppnas; därefter snabbkärna, rapport och jämförelse mot etablerad programvara (B4, B7, B10 till B13). | pågår | B4, B7, B10 till B13 |
| Formella rapporter | Rapport med validerade metoder, granskning och signering av människa (R3), därefter export, arkiv och spårbarhet (R5). | väntar | R3, R5 |
| Ledningsnät och realtid | Ledningsnät kopplat i 1D (SWMM) och radarnederbörd. Kräver data från Stockholm Vatten och Avfall och beslut om driftmiljö (M7). | senare | M7 |
| Luft: validering och snabbare körning | Serverkörning med CuPy, vindtunnelvalidering (CEDVAL, Michelstadt) och validering mot mätstationer (L-b, L-c, L-h). | redo | L-b, L-c, L-h |
| Dagsljusfaktor | Dagsljusfaktor i typrum ovanpå VSC, och en Radiance-referens för utkragningar (dagsljus fas 5). | redo | Dagsljus fas 5 |
| Ekologi mot riktiga data | Köra NMD, VMI och trädlagret mot riktiga filer, köra Stockholms nätverk mot stadens biotopdatabas och källhänvisa artprofilernas parametrar. | väntar | Ekologi, nästa steg |
| Drift: kö, demoläge, kvoter och backup | Jobbkö med arbetare och kostnadsbok, demoläge med färdigräknade exempel, kvoter per konto, backup, och GPU-klass på servern (S4, S5k, S6d, S9, S10). | väntar | S4, S5k, S6d, S9, S10 |

## 3. Jämförelse med andra verktyg

Sidan skiljer strikt på tre saker: det som är uppmätt i Floden, det som andra verktygs tillverkare själva har publicerat, och det som inte är mätt. Det finns ingen mätning av samma fall i Floden och i något annat verktyg.

### A. Uppmätt i Floden

Floden mot Floden. Varje tal har sin källa i repot och kontrolleras mot den.

| Fall | Jämförelse | Tal | Maskin | Källa |
| --- | --- | --- | --- | --- |
| Dagsljus 300 × 300 m, SVF och soltimmar (64 riktningar) | Webbläsarens GPU (WebGPU, f32) 53,9 ms mot Flodens server, CPU (f64) 4 242,6 ms | cirka 79 gånger | En Mac (Apple, Metal 3, 16 GB, Chrome 157) mot Flodens server floden.berring.se (Python, CPU) | [`docs/benchmark/2026-11-23-mac-webgpu.json`](https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-mac-webgpu.json) |
| Vatten 2D, lutande plan med sänka och byggnad, 64 × 64 celler, 1 000 tidssteg | Webbläsarens GPU (WebGPU, f32) 9,9 ms mot Flodens server, CPU (f64) 244,2 ms | cirka 25 gånger | En Mac (Apple, Metal 3, 16 GB, Chrome 157) mot Flodens server floden.berring.se (Python, CPU) | [`docs/benchmark/2026-11-23-mac-webgpu.json`](https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-mac-webgpu.json) |
| Hybrid (sänkgraf och 2D) mot full 2D | Hybrid mot full 2D (Floden) | cirka 1,2 till 1,8 gånger | mätt efter rättelserna i vattenkärnan 2026-11-23 | [`docs/MATEMATIK.md`](https://github.com/joelberring/floden/blob/main/docs/MATEMATIK.md) |
| Lokal omräkning mot full omräkning, 16 kvarter | Lokal omräkning (bara det som berörs) 26,7 ms mot Full omräkning 247,4 ms | cirka 9,3 gånger | mätt 2026-11-20 (T04), före rättelserna i vattenkärnan och inte omkörd | [`STATUS.md`](https://github.com/joelberring/floden/blob/main/STATUS.md) |

- *Dagsljus 300 × 300 m, SVF och soltimmar (64 riktningar):* SVF-fältet avviker 0,0126 från serverns f64 i 13 av 90 000 celler (nära fasader), över toleransen 0,01; soltimmar ligger långt inom toleransen. Jämförelsen är Flodens GPU-kod mot Flodens CPU-kod, inte mot något annat verktyg.
- *Vatten 2D, lutande plan med sänka och byggnad, 64 × 64 celler, 1 000 tidssteg:* Facit är skuggarens eget schema i f64, inte lösaren `tvad.lokal_troghet`. Största avvikelsen i max djup ligger långt under toleransen.
- *Hybrid (sänkgraf och 2D) mot full 2D:* De tidigare redovisade faktorerna (12,9 till 20) gällde före rättelserna och gäller inte längre.
- *Lokal omräkning mot full omräkning, 16 kvarter:* Resultatet är bitexakt lika (avvikelse 0,00 m). Bas är Flodens egen fullberäkning.

**Körtider i Floden** (median av fem körningar efter en uppvärmning).

Maskin: Delad molncontainer: 2 vCPU (Intel Xeon, 2,30 GHz), 8 GB, Python 3.13, ingen GPU. Andra processer delar maskinen, så tiderna är ungefärliga och varierar med flera tiotals procent. De är ingen referens för produktionsservern. Källa: [`docs/benchmark/2026-11-23-container.json`](https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-container.json).

| Ämne | Fall | Körtid |
| --- | --- | --- |
| Skyfall | EA1: Environment Agency Test 1, sänka över tröskel, 10 m | 0,55 s |
| Skyfall | D1: Hunters analytiska lösning, 3 600 s, dx 5 m | 0,83 s |
| Skyfall | EA8A: urbant regn, subgrid mot full 2D, 2 m | 1,75 s |
| Skyfall | EA2: 4 × 4 sänkor, hybrid 2D och sänkgraf, 5 m | 3,43 s |
| Dagsljus och sol | SVF och soltimmar, kvarter 300 × 300 m, 64 riktningar | 5,03 s |
| Skyfall och dagsljus | `floden demo --liten`: bygg värld, dagsljus och vatten med tom cache | 11,4 s |
| Luft | L4: punktkälla i likformigt flöde, 48 × 28 × 16 celler | 0,111 s |
| Buller | Helkedja 300 m, 20 mottagare (påhittad emission, baskärnan, inte Nord2000) | 7,39 s |

### B. Offentliga uppgifter om andra verktyg

Uppgifterna är hämtade ur tillverkarnas och projektens egna sidor den 2026-11-25. Att en uppgift saknas här betyder inte att den saknas hos tillverkaren, bara att den inte stod på sidorna som lästes. Floden har inte kört något av verktygen.

#### SCALGO Live, Flash Flood Map (Skyfall)

- **Utgivare:** SCALGO
- **Vad som är publicerat:** Beskrivs som en enkel, stationär och händelsebaserad screeningmodell. Vattenvolymen från regnet leds nedströms och fyller sänkor på vägen. Ingen tidskomponent; regnets varaktighet beaktas inte och vattnet antas finnas i cellen direkt. Regnet ställs med ett reglage och gäller likformigt över terrängen; avrinningsfunktioner efter markanvändning kan dra av förluster.
- **Metod:** Fyllning av sänkor (depression filling).
- **Licens:** Licensform inte angiven på sidan som lästes.
- **Pris:** Inte angivet på sidan som lästes.
- **Körtid:** Inte angiven.
- **Källa (hämtad 2026-11-25):** [SCALGO Live: Flash Flood Map](https://scalgo.com/en-US/scalgo-live-documentation/analysis/flash-flood-map)

#### SCALGO Live, DynamicFlood (Skyfall)

- **Utgivare:** SCALGO
- **Vad som är publicerat:** Hydrodynamiska 2D-simuleringar av ytavrinning med TUFLOW som lösare. Är en tilläggsmodul (Core+) som enligt sidan inte släppts i alla länder. Jobben tar enligt sidan avsevärt längre än vanliga beräkningar i arbetsytan, kan köra på GPU-resurser och köas när resurserna är upptagna. Användare med egen TUFLOW-licens kan ladda ner modellkonfigurationen.
- **Metod:** Hydrodynamisk 2D-ytavrinning (TUFLOW).
- **Licens:** Tilläggsmodul till Scalgo Live; kontakta Scalgo om tillgänglighet.
- **Pris:** Inte angivet på sidan som lästes.
- **Körtid:** Ingen körtid eller hastighetsfaktor angiven.
- **Källa (hämtad 2026-11-25):** [SCALGO Live: hydrodynamic engine](https://scalgo.com/en-US/scalgo-live-documentation/hydrodynamic-engine/introduction)

#### MIKE 21 FM (flexibelt nät) (Skyfall)

- **Utgivare:** DHI
- **Vad som är publicerat:** Den hydrodynamiska modulen (även k-ε-turbulens, temperatur och salthalt) och transportmodulen körs på GPU; in- och utdata körs på CPU med OpenMP och MPI. Vissa moduler, till exempel Navier–Stokes-modulen, sediment och spektralvåg, är inte GPU-parallelliserade. GPU-stödet kräver NVIDIA CUDA (compute capability 7,5 till 12 i MIKE Release 2026). MIKE+ ingår inte i det som lästes.
- **Metod:** Hydrodynamisk modell på flexibelt nät.
- **Licens:** Licensfil; GPU-alternativets utgångsdatum följer avtalets utgångsdatum (SMA).
- **Pris:** Inte angivet på sidan som lästes.
- **Körtid:** Artikeln anger ingen hastighetsfaktor och hänvisar till benchmarkrapporter som inte lästs.
- **Källa (hämtad 2026-11-25):** [DHI: GPU-stöd för MIKE 21/3 FM](https://support.dhigroup.com/knowledgebase/article/KA-01054/en-us)

#### InfoWorks ICM (Skyfall)

- **Utgivare:** Autodesk
- **Vad som är publicerat:** Integrerad 1D- och 2D-modellering av avloppssystem, ytvatten, vattendrag och översvämningsområden. Subgrid-sampling gör att större 2D-element kan användas med bibehållen topografisk detalj. 2D-beräkning på GPU kräver NVIDIA CUDA (compute capability 3,5 eller högre).
- **Metod:** Integrerad 1D/2D-hydraulik.
- **Licens:** Autodesk-produkt; licensform inte angiven på sidan som lästes.
- **Pris:** Inte angivet på sidan som lästes.
- **Körtid:** Tillverkaren anger att GPU kan snabba upp simuleringar med stora modeller; inget mätunderlag redovisas på sidan.
- **Tillverkarens tal:** GPU kan snabba upp simuleringar med stora modeller med (tillverkarens uppgift, utan redovisad mätmetod): minst 12 gånger
- **Källa (hämtad 2026-11-25):** [Autodesk: InfoWorks ICM, funktioner](https://autodesk.com/au/products/infoworks-icm/features), [Autodesk: krav på grafikmaskinvara för simulering](https://www.autodesk.com/learn/ondemand/tutorial/planning-graphics-hardware-requirements-for-simulation)

#### ClimateStudio (Dagsljus och sol)

- **Utgivare:** Tillverkarens webbplats
- **Vad som är publicerat:** Insticksmodul till Rhinoceros 3D (version 6, 7 eller 8). Enligt tillverkaren kör den Radiance i ett progressivt path tracing-läge med maskinvaruacceleration. Tillverkaren skriver att simuleringar som tidigare tog timmar kan göras på sekunder, och anger som exempel att sDA för ett kontor med sidoljus beräknas på en kort tid (se talet nedan) medan Daysim enligt tillverkaren tar timmar och Radiance dagar.
- **Metod:** Radiance (path tracing).
- **Licens:** Kommersiell licens och utbildningslicens finns; närmare form inte angiven på sidorna som lästes.
- **Pris:** Inte angivet på sidorna som lästes.
- **Körtid:** Tillverkarens egen uppgift för ett exempelkontor, utan redovisad mätmetod.
- **Tillverkarens tal:** sDA för ett kontor med sidoljus (tillverkarens exempel, utan redovisad mätmetod): ungefär 10 sekunder
- **Källa (hämtad 2026-11-25):** [ClimateStudio: Why is ClimateStudio so fast?](https://hibiscus-chartreuse-y9mk.squarespace.com/blog/why-is-climatestudio-so-fast), [ClimateStudio: produktsida](https://hibiscus-chartreuse-y9mk.squarespace.com/climatestudio)

#### Accelerad (Dagsljus och sol)

- **Utgivare:** MIT Sustainable Design Lab
- **Vad som är publicerat:** GPU-accelererad version av Radiance. Projektet anger att den är snabbare än Radiance på samma uppgift, med faktorer per program (se talen nedan) mätta på ett RTX 8000-kort. Kräver NVIDIA CUDA-GPU.
- **Metod:** Radiance på GPU.
- **Licens:** Gratis programsvit under slutanvändaravtal (EULA); licenstypen namnges inte på sidan som lästes.
- **Pris:** Gratis enligt projektet.
- **Körtid:** Se hastighetsfaktorerna; de gäller GPU mot samma program på CPU.
- **Tillverkarens tal:** rpict: GPU mot Radiance på CPU (projektets uppgift, RTX 8000): upp till 100 gånger
- **Tillverkarens tal:** rcontrib: GPU mot Radiance på CPU (projektets uppgift, RTX 8000): 45 gånger
- **Tillverkarens tal:** rtrace: GPU mot Radiance på CPU (projektets uppgift, RTX 8000): 28 gånger
- **Källa (hämtad 2026-11-25):** [Accelerad](https://nljones.github.io/Accelerad)

#### Honeybee-radiance (Pollination) (Dagsljus och sol)

- **Utgivare:** Pollination
- **Vad som är publicerat:** Tillägg som ger dagsljussimulering med Radiance i Pollination, med recept för bland annat årligt dagsljus, dagsljusfaktor och himmelsandel.
- **Metod:** Radiance.
- **Licens:** Öppet förråd på GitHub; licenstexten lästes inte.
- **Pris:** Inte undersökt.
- **Körtid:** Inte angiven.
- **Källa (hämtad 2026-11-25):** [pollination/honeybee-radiance](https://github.com/pollination/honeybee-radiance)

#### SoundPLAN (SoundPLANnoise) (Buller)

- **Utgivare:** SoundPLAN (soundplan.eu)
- **Vad som är publicerat:** Listar Nord2000 för väg, järnväg och industri bland ett stort antal beräkningsstandarder. Nya standarder, till exempel Nord2000 Rev. 2019, läggs enligt tillverkaren till i aktuell version utan extra kostnad via underhåll.
- **Metod:** Beräkningsstandarder inklusive Nord2000.
- **Licens:** Kommersiell; licensvillkor ligger på en annan sida som inte lästes.
- **Pris:** Inte angivet på sidan som lästes.
- **Körtid:** Inte angiven.
- **Källa (hämtad 2026-11-25):** [SoundPLAN: tillgängliga beräkningsstandarder](https://www.soundplan.eu/en/software/soundplannoise/standards)

#### CadnaA (Buller)

- **Utgivare:** DataKustik
- **Vad som är publicerat:** Nord2000 för industrikällor via två DLL-bibliotek som distribueras av Norwegian Public Roads Administration och är utvecklade av SINTEF. Den 32-bitars DLL:en saknar flertrådning och använder en kärna; 64-bitars-varianten stöder flertrådning. DataKustik anger att man inte utvecklat DLL:erna och därför inte kan förklara resultat från dem.
- **Metod:** Nord2000 via SINTEF:s DLL (industrikällor).
- **Licens:** Kommersiell; licensvillkor inte på sidan som lästes.
- **Pris:** Inte angivet på sidan som lästes.
- **Körtid:** Inte angiven.
- **Källa (hämtad 2026-11-25):** [CadnaA: Nord2000 för industri](https://doku.datakustik.com/CadnaA/en/Industrie_Nord2000.html)

#### Circuitscape 5 (Ekologi)

- **Utgivare:** Circuitscape-projektet
- **Vad som är publicerat:** Använder algoritmer från kretsteori för att förutsäga rörelse, genflöde och genetisk differentiering mellan populationer i heterogena landskap. Skriven i Julia; Windows, macOS och Linux.
- **Metod:** Kretsteori.
- **Licens:** Öppen källkod, MIT-licens.
- **Pris:** Gratis (öppen källkod).
- **Körtid:** Inga prestandatal angivna.
- **Källa (hämtad 2026-11-25):** [Circuitscape: nedladdning](https://circuitscape.org/downloads/)

#### Linkage Mapper (Ekologi)

- **Utgivare:** consbio.org
- **Vad som är publicerat:** Pythonskript förpackade som ArcGIS-verktygslåda. Verktyget Linkage Pathways använder kartor över kärnområden och motstånd för att hitta minsta-kostnadskorridorer mellan närliggande kärnområden.
- **Metod:** Minsta-kostnadskorridorer.
- **Licens:** Licens inte angiven på sidan som lästes.
- **Pris:** Inte angivet på sidan som lästes.
- **Körtid:** Inte angiven.
- **Källa (hämtad 2026-11-25):** [consbio.org: Linkage Mapper](https://consbio.org/software/linkage-mapper/)

#### Conefor Sensinode (Ekologi)

- **Utgivare:** Conefor-projektet
- **Vad som är publicerat:** Kvantifierar habitatytors betydelse för att behålla eller förbättra landskapets konnektivitet. C++; Linux och Windows. Projektet är märkt Beta och sidan på SourceForge uppdaterades senast 2013-05-30.
- **Metod:** Konnektivitetsindex för habitatytor.
- **Licens:** Öppen källkod, GNU GPL version 2.
- **Pris:** Gratis (öppen källkod).
- **Körtid:** Inte angiven.
- **Källa (hämtad 2026-11-25):** [Conefor på SourceForge](https://sourceforge.net/projects/conefor/)

### C. Det som inte är mätt

Det finns ingen jämförande mätning av samma fall i Floden och i något av de andra verktygen: inga körtider på samma indata och maskin, och ingen avvikelse mellan Flodens och ett annat verktygs resultat. Därför anger Floden ingen faktor av typen X gånger snabbare än en produkt, och siffrorna i de två föregående avsnitten får inte läsas mot varandra. Hastighetstalen i avsnitt A gäller Floden mot Floden. Hastighetstalen i avsnitt B är tillverkarnas egna, mot deras egna baser, och Floden har inte kontrollerat dem. Ett jämförelseprov är föreslaget i planen (J1) men inte gjort.

### D. Skillnader som går att belägga

- **Öppen källkod.** Kärnan är under GPL v3 och luftdelen under MIT. Koden, metodregistret och testfallen går att läsa, köra och granska. Bland ekologiverktygen som lästes är Circuitscape (MIT) och Conefor (GPL v2) också öppna. (källa: [`LICENSE`](https://github.com/joelberring/floden/blob/main/LICENSE))
- **Samma flöde för flera ämnen.** Skyfall, dagsljus och sol, ekologi och luft delar kärnan: geografi, projekt, ritat förslag, frågekatalog, beräkningsgraf med cache, beställning, rapport och MCP. I webbens kartflöde finns skyfall och dagsljus och sol fullt ut; ekologi och luft har begränsningarna som står under varje ämne. (källa: [`docs/KARNA.md`](https://github.com/joelberring/floden/blob/main/docs/KARNA.md))
- **Körtid i sekunder för skiss på kvartersnivå.** Skyfall och dagsljus för ett kvarter räknas på sekunder på en delad tvåkärnig molncontainer utan grafikkort (tabellen över körtider). Luft är undantaget och tar lång tid. (källa: [`docs/benchmark/2026-11-23-container.json`](https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-container.json))
- **Osäkerhetsintervall.** Intervall för vatten (K40), SVF (K41), miljökvalitetsnormer och ekologi redovisas som min och max med känsliga överfall utpekade, och visas i resultatet och rapporten. (källa: [`docs/DATAKONTRAKT.md`](https://github.com/joelberring/floden/blob/main/docs/DATAKONTRAKT.md))
- **Kontrollerade påståenden i rapporten.** Varje tal i en rapport är en platshållare med belägg (beräkning, fil, cell, mått). Floden fyller talen och en deterministisk kontroll avvisar tal utan belägg och påståenden som inte stöds, före export till Word och PDF. (källa: [`docs/RAPPORTER.md`](https://github.com/joelberring/floden/blob/main/docs/RAPPORTER.md))
- **Spårbarhet.** Varje beräkning skriver ett manifest med kontrollsummor för all indata, kodversion och parametrar; MCP-anrop loggas i en hashkedjad körlogg. (källa: [`AGENTS.md`](https://github.com/joelberring/floden/blob/main/AGENTS.md))

### E. Vad andra verktyg har som Floden saknar

- **Validering mot oberoende data och etablerade verktyg.** Jämförelsen med Scalgo och SMHI väntar (T09b), Nord2000 klarar bara en del av testfallen och ingen metod är klassad som rapportmetod av en människa. (källa: [`PLAN.md`](https://github.com/joelberring/floden/blob/main/PLAN.md))
- **Certifiering och branschacceptans.** Ingen certifiering och inget godkännande av myndighet eller bransch finns. Floden är ett skissverktyg och kartor och rapporter märks så. (källa: [`data/mcp/metodstatus.toml`](https://github.com/joelberring/floden/blob/main/data/mcp/metodstatus.toml))
- **Fullständig 2D-hydraulik med ledningsnät.** Floden har 2D lokal tröghet och hybrid, men 2D är inte ett steg i webbflödet och ledningsnät i 1D är inte kopplat. Enligt tillverkaren beskriver InfoWorks ICM integrerad 1D- och 2D-modellering av avloppssystem, ytvatten, vattendrag och översvämningsområden, vilket Floden inte har. (källa: [`PLAN.md`](https://github.com/joelberring/floden/blob/main/PLAN.md))
- **Buller enligt Nord2000.** Kärnan är inte Nord2000-konform och rapportläget är spärrat. SoundPLAN listar Nord2000 för väg, järnväg och industri. (källa: [`docs/MATEMATIK.md`](https://github.com/joelberring/floden/blob/main/docs/MATEMATIK.md))
- **Dagsljusfaktor i rum och en Radiance-referens.** Floden räknar himmelsandel, soltimmar och VSC på fasad. Dagsljusfaktor i typrum och reflekterat ljus finns inte än. (källa: [`PLAN.md`](https://github.com/joelberring/floden/blob/main/PLAN.md))
- **Riktiga data i drift.** Det mesta är provat mot syntetiska data. Körning mot riktiga Lantmäteri- och kommundata på servern och mätning av tid och minne per ruta väntar. (källa: [`PLAN.md`](https://github.com/joelberring/floden/blob/main/PLAN.md))
