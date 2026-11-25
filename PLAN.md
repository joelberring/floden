# Plan

Milstolparna byggs i ordning. Inom en milstolpe kan uppdrag med uppfyllda beroenden köras parallellt av olika agenter.
Status: `redo` · `pågår` · `klar` · `blockerad` · `väntar` (väntar på något utanför projektet).

## Lägesbild 2026-11-25

Sammanställd 2026-11-25 ur `STATUS.md` (senaste två veckorna), `docs/MATEMATIK.md` med `data/matematik/metoder.toml`, `docs/design/RITVERKTYG.md`, `docs/UX-GENOMGANG.md` och `docs/OM-FLODEN.md`, med stickprov mot koden (modul och test finns). PR:erna som nämns här (#51–#116) är mergade. Överallt där det står "syntetiskt" är fallet påhittat eller testat med falska tjänster; inget är körningsmätt mot Lantmäteriets eller Göteborgs riktiga tjänster i repots tester. **Klar** betyder kod, test och dokument på main, inte att Joel har granskat regeltolkningen. Alla metoder är fortfarande `skiss` (`data/mcp/metodstatus.toml`). Nummerserierna R1–R7 i Ritverktyg och R1/R2 i Rapporter är två olika serier; tabellen skriver ut vilken som avses.

| Område | Klart | Halvgjort (vad saknas) | Skissat/ej påbörjat | Nästa steg |
| --- | --- | --- | --- | --- |
| **Plattform/drift** | S1 avbildning och S2 basserver live på floden.berring.se (2026-11-23); S3 Google-inloggning; S7b benchmarksvit (`docs/benchmark/`, K37); WebGPU-prov (`#/prov/webgpu`); ASGI-adapter; GPU-backend `src/gemensamt/gpu.py` och rut-kö `floden forberakna` med Colab-skript (#114) | S5 `goteborg-test` (K46): kod, test och dokument klara, Haga mappad på servern, men bara delvis mot riktiga Lantmäteri-/Göteborgsdata (se Geografi); `FLODEN_NYCKELHEMLIGHET` ska vara satt på servern; CUDA-tester är hoppade utan GPU | S4 backup och bucket; S5k kö, arbetare och kostnadsbok; S6d demoläge; S9 GPU-klass (Verda); S10 kvoter; S8 Stockholm | S6 mät tid, minne och disk per ruta på servern (Joel kör); S4 när Joel beslutat om backup |
| **Konto och organisationer** | D1 konton (`src/konto/`, Google, OIDC förberett); E1 nycklar och administration (#51); E2 organisationsroller, delning och inbjudan (#70); projektroller i webb, API och MCP | E1-klienten är enhetstestad men inte prövad i webbläsare; E2-regeln är inte införd i `docs/BESLUT.md` (Joel); Entra/SSO finns men är av | S10 kvot per konto/organisation, därefter delning mellan organisationer | Joel för in E2-regeln i BESLUT; S10 först efter S5k |
| **Geografi och data** | K46 geografi som objekt (#67); Göteborg ur öppna filpaket (#73, #87 Shapefile, #77/#85 `dwgread`); Lantmäteriets avgiftsfria produkter (#89); höjdmodell: hål fylls inom plattorna (#91, #106); takhöjd under mark ignoreras (#108); 3D-baskarta, `LineStringZ` och DEM-sammanslagning (#115); vattenytor ur strandlinjer (#116); Stockholm: byggnader ur lokal stängd baskartefil (#83); NMD och VMI som valfria lager | Höjdmodellen: hål fylls bara inom plattornas yttergräns och högst `FYLL_MAX_M` = 150 m (`src/kommuner/hojdmodell.py`); Joel har sett rutor i `goteborg-test` med 46–85 % NaN (inte reproducerat i repot, ingen siffra i STATUS än); vattenytor ur strandlinjer ej mot riktig baskarta, nivån är närmaste strandpunkts z per cell, Göteborg måste mappas om; 3D-baskartan ej mot riktig kommunal data; NMD/VMI/trädlager ej mot riktiga filer | S7 Hägersten-Älvsjö med Skärholmen och S8 Stockholm som geografier; ledningsnät i 1D (SWMM) och radar (M7) | Diagnosera NaN-rutorna (Neon-uppdrag 6); planera S7 (Neon-uppdrag 7); Joel mappar om `goteborg-test` som ny version |
| **Webbflödet/UX** | Flödet steg 1–5 (#54), en kartsida (#80), U1 genomgång (#93), U2 rapport på användarens körning (#97), U3 ett skal (#96), U4 felmeddelanden och ordlista (#98), U5a läget idag (#99), U6 geografiprojekt: OSM, markhöjd, höjdkurvor, Spara förslag (#109), alla analyser som kartlager i steg 4 (#110, #113), projektlistan fastnar inte på kartan (#111), Om Floden (#112), Nytt projekt och Skapa rapport (#81, #95), kartverktyget för geografiprojekt (#100) | U5 b–g (kort i stället för tabeller på smal skärm, kartvärden med tangentbord, ordlistan genom steg 4, statusprickar med text, en beställning i stället för två, projektraden med roll); steg 4 saknar Luft-flik och buller; steg 4 visar inga mätvärden för läget idag; polygonen ritas bara i steg 1; OSM-plattor aldrig hämtade på riktigt (provet använder påhittade bilder); rännstenslagret `rannsten_flode.geojson` (#115) är inte kopplat till lagerlistan; ekologi i kartverktyget är bara demokvarteret | Rapport enbart om läget idag; höjdkurvor som plattor för mycket stora projekt | U5 b–g (Joels webbagent); Luft-flik (Neon-uppdrag 3); Joel provar `goteborg-test@v2` igen efter ny mappning |
| **Ritverktyget R1–R7 (+R8)** | R1 skarvkontroll (#101, K49); R2 lagermodell (#103, K50); R3 linjer och ytor, K9 `brytlinje_ny`/`oppning_ny` (#104); R4 mark i nivåer i kärnan (#102); R5 skarv i klienten och R6 3D (#105); R7 pekskärm och webbläsarprov (#107); R8 geografiprojekt i webben (#109) | Ej prövat på en riktig telefon (bara Chromium med beröringsemulering); ej prövat mot Göteborgs riktiga data; skarvkontrollens gränser (band 10 m, djup 0,05 m, ökning 0,03 m) är förslag som Joel inte granskat; lagerrollen `punkt` är reserverad | Ritverktyg för luft, buller och vegetation i 3D (`docs/WEBB3D.md`) | Joel prövar på telefon och granskar skarvgränserna; inget Neon-uppdrag (rör webbflödet) |
| **Skyfall** | T01–T08 syntetiskt; T02b slutsänkor, T02c takvatten; testsvit 18/18 (A0–A13, A5b, EA1/EA2/EA8A); 2D, hybrid och Rust-kärna; vattenkärnans rättelser (BESLUT 2026-11-23); 1D-rännstenslänk och täta 3D-kantbarriärer (#115); flödesvägar som avrinningsnät (#113); intervall K40 och bifurkationer K34–K36 i webben; läget idag; GPU-väg för 2D (#114) | Allt syntetiskt, inget verkligt provområde; T09b jämförelse mot Scalgo/SMHI väntar; vattenytor saknar fast randvillkor för nivå/utlopp; `vatten.dynamik` (`src/karna/inbyggda.py:319`) skriver bara `max_djup` men `DynamikResultat` har `tid_till_0_1m_s` och `tid_till_0_2m_s` (`vatten.2d` skriver redan hastighet och ankomsttid); hybridens faktor (12,9–20,0 ×) mättes före rättelserna; Q1–Q5 öppna (`docs/MATEMATIK.md` avsnitt 9) | Ledningsnät och radar (M7); fysikbevarande modellreduktion (Q5) | Ankomsttid som raster ur dynamiken (Neon-uppdrag 5); T09b när Joel har Scalgo-underlag |
| **Dagsljus/sol** | Fas 1 (horisont, soltimmar, SVF, VSC), fas 1b (K1 v2, K11, före/efter), intervall K41, WebGPU-motor (≈ 79 × på Joels Mac) med heltalsräknare mot f32-drift (#71, #55), GPU-väg på CPU/CUDA-backend (#114), rapportunderlag dagsljus och sol (#53), VSC som lager (#110) | Rapportkörningen saknar SVF och höstens ekvinox; CUDA-uppsnabbning ej mätt; ingen Radiance-referens | Fas 5 dagsljusfaktor i typrum och reflekterat ljus (väntar på Joels bekräftelse); J1 mot Radiance | Joel bekräftar fas 5; J1-delen mot Radiance (Neon-uppdrag 8) om J1 godkänns |
| **Ekologi** | Etapp 1–2 (K44, K45, `ekologi.konnektivitet`), nätverk med signalarter K47 och NMD/VMI-inläsare (#68), trädlager och kommunförval (#92), en frågekatalog EK-01–EK-15 (#88), skissunderlag ekologi (R2, #82), kontroll av påstående om borttagen dunge | NMD, VMI och trädlager ej körda mot riktiga filer; källhänvisning för artprofilernas parametrar utöver nätverkens; EK-02 (båda ändarna, alternativ väg) och EK-09 (kumulativt scenario) ej kopplade till körningar; webbens Skapa rapport ger 409 för ekologi (R2-mall bara via MCP); EK-mappningen ej granskad av Joel | Stockholms nätverk mot stadens biotopdatabas (lokalt); J1 mot Circuitscape och Conefor | Joel granskar EK-07/EK-12; lokal körning på riktiga filer (Joel) |
| **Luft** | L-a LBM-LES och adjungerad spridning, L-d, L-f TVD, L-g vägdamm, L-k vindkomfort, L-i luft på gemensam geometri (`luft.halt`, `luft.vindkomfort`); `luft.mkn` med belägg och intervall K43; skissunderlag luft LU-01–LU-15 (#94) | Timserien kan inte komma ur Floden (`luft.halt` till timserie saknas); webbens Skapa rapport kör inte luft (409, serie och bakgrund kan inte anges); steg 4 har inte Luft-flik (skalor finns i `analyslager.ts`); adjungerad känslighet och CELF i `luft.kanslighet` fristående; GPU-väg finns inte för luft | L-b CuPy-körning på server; L-c vindtunnelvalidering (CEDVAL, Michelstadt); L-h mätstationer | Luft-flik (Neon-uppdrag 3) och luft i Skapa rapport (Neon-uppdrag 4) |
| **Buller/Nord2000** | Baslinjekärna och operatorlager (B3–B6, B8–B9); referenskärna delmodell 1–10; stadsgata 4/4, kurvig väg 10/10 (#78, #86, #90); grupp N: **55 av 80 godkända**, 16 ska utredas, 5 underkända (N18R052, 062, 102, 103, 122), 4 ej körbara (årsmedel N18A1–A4); loggar i `docs/buller/logg/` | `nord2000_konform = False`; rapportläget spärrat; ingen K21-lösare, alltså ingen buller i steg 4 eller webbens rapport; årsmedel väntar på väderklasstabellen ur VTT-R-02530-06; mjuk kulle (N18R111–114), upphöjd väg i höga band (N18R031–043) och skogens övergångsband (N18R121–124) | B7 snabbkärna, B10 rapportavsnitt, B11 jämförelse mot etablerad programvara, B12 gradienter, B13 vågfysik | Neon-uppdrag 1 och 2 (fallen kvar i grupp N) |
| **Rapporter R1/R2** | R0 MCP-server; R1 påståenden med belägg, kontroll och export till Word/PDF (skyfall); R2 beställning (K30), frågekatalog, skissunderlag dagsljus/sol, ekologi och luft; Skapa rapport i webben (#81) med användarens förslag och frågor (#97) | Ekologi och luft bara via MCP; buller spärrat; rapport på bara läget idag ger 409; mallens text "hela beräkningsområdet" bör säga "inom projektets polygon" när polygon finns; LU- och EK-mappningen ej granskad av Joel | R3 formell rapport (validerade metoder, signering), R5 export och arkiv; R4 roller och modellval per dataklass | Luft i Skapa rapport (Neon-uppdrag 4); R3 kräver att metoder klassas `rapport` (Joel) |
| **MCP** | R4a MCP på servern (#58): personliga token, `/mcp` bakom kontoskyddet, projektroller, spärr för icke-öppna data (K38); `projekt_skapa` (#81); körlogg med hashkedja; `projekt_lista` per konto | OAuth för Claude-appen stannar efter `POST /oauth/authorize -> 302` utan `/oauth/token` (#60–#64, parkerat av Joel 2026-11-23); riktig koppling från Claude-appen ej prövad (token som begärandehuvud fungerar) | R4 modellval per dataklass i verktygen; R5 arkiv | Parkerat tills Joel tar upp det (logga vart 302:an pekar, prova mot lokal Claude-lik klient) |
| **Prestanda/GPU** | Benchmarksvit (K37, baslinje `docs/benchmark/2026-11-23-container.json`); WebGPU på Joels Mac (dagsljus ≈ 79 ×, vatten 2D ≈ 25 ×, `…-mac-webgpu.json`); CuPy-backend med CPU-fallback, `floden forberakna` återupptagbar kö per 500 × 500 m-ruta, CPU-baslinje 4,42 s/ruta (#114); inkrementell dagsljusomräkning och CELF som moduler | Ingen uppmätt CUDA-tid (testerna hoppas utan GPU); luft saknar GPU-väg (L-b); S6 mätning på servern saknas, uppskattningarna i `docs/ARKITEKTUR-DRIFT.md` 5 gäller | S9 GPU-klass hos Verda; B7 snabbkärna för buller; Q5 modellreduktion | Joel kör `scripts/colab_forberakna.py` på en CUDA-maskin; S6 |
| **Validering och jämförelse J1** | Facittestsvit 18/18; grupp E 7/7; Nord2000 grupp N 55/80; Environment Agency-fallen EA1, EA2, EA8A och hybrid mot full 2D (T09c); sidan `#/om` skiljer uppmätt, tillverkarnas uppgifter och omätt (#112) | T09b (Scalgo och SMHI) väntar på provområde; inga mätningar av samma fall i Floden och ett annat verktyg | J1 jämförelseprov (förslag; `docs/benchmark/jamforelse/` finns inte), kräver licens eller konto hos tredje part | Joel bekräftar eller avvisar J1; den öppna delen mot Radiance kan Neon ta (uppdrag 8) |

Städning samtidigt i planen nedan: dubbletten "Ekologi, nästa steg" (källhänvisning) sammanslagen, raden "Fristående moduler" markerad inaktuell (ersatt av raden "Matematikmoduler"), "PR öppen" ersatt med PR-nummer för de som är mergade, den lösa punkten sist om "Alla analyser som kartlager" flyttad hit (Webbflödet/UX, #110, #113).

## Uppdrag lämpliga för Neon

Neon är en extern agent som arbetar via PR:er och har tidigare gjort Nord2000/buller, GPU-batch och 3D-kantsten/rännsten. Uppdragen nedan är fristående: avgränsade filer, syntetiska tester och inget som kräver att webbflödet byggs om. Joel väljer vilka som startas. Rimlig första omgång (störst nytta, minst krockrisk): 5, 1, 6.

**Regler som gäller alla uppdrag (`AGENTS.md`):**
- **Inga facitvärden i git.** Nord2000-facit (RL 18-18) ligger lokalt och får inte checkas in, inte heller i loggar eller i testernas förväntade värden; tester jämför mot analytiska fall eller mot en lokal fil som saknas i CI (hoppas då). Inga konstanter anpassas mot facit.
- **Inga stängda data till extern AI.** Stockholms baskarta, trädinventeringar, biotopdatabaser och allt annat som inte är dataklass `oppen` får aldrig laddas upp till Neon eller någon annan extern AI; Neon arbetar bara med syntetiska data och öppna data (Lantmäteriets avgiftsfria produkter, Göteborgs öppna data, CC0). Körningar mot stängda data görs lokalt av Joel.
- **`docs/BESLUT.md` ändras bara av Joel.** Neon föreslår i STATUS-posten under "Öppet". Kontraktsändringar i `docs/DATAKONTRAKT.md` är additiva; nytt K-nummer kräver att det sägs i PR:en.
- Arbeta på egen gren, pusha aldrig till main, öppna PR (REST), avsluta med en post i `STATUS.md` (gjort, mätt, öppet, nästa). Kör `ruff`, `mypy` och berörd `pytest` (`export PYTHONPATH=$PWD/src`; `FLODEN_TIDSFAKTOR=4` på långsam maskin). Rör inte `PLAN.md` annat än status för det egna uppdraget. `nord2000_konform` ändras inte av Neon.

### Uppdrag 1 – Nord2000: de fem underkända fallen
- **Mål:** få ned de underkända fallen (dal i medvind N18R052, 062, 102, dubbeldal i motvind N18R103, skog N18R122) genom att kontrollera delmodell 3 (tolkning 7) och delmodell 5/6 mot metodtexten (AV 1106/07, AV 1849/00), utan att konstanter anpassas.
- **Filer:** `src/buller/nord2000/delmodell3.py`, `delmodell5.py`, `delmodell6.py`, `sammansattning.py`, `tests/buller/nord2000/`, `docs/buller/logg/P-S6.md` (ny), `docs/buller/tolkningar.md`.
- **Rör inte:** `src/buller/operator/`, `src/buller/scen/`, kontraktet `KB1/v1`, webb och API, `docs/BESLUT.md`, redan godkända fall (de 55 ska fortsätta vara godkända).
- **Klart när:** antalet underkända är lägre än 5 (mål 0) i en körning av hela grupp N lokalt, 55 godkända kvarstår, nya enhetstest per ändrad formel med analytisk kontroll, `pytest tests/buller` grön, avvikelsen mot metodtexten dokumenterad per fall i loggen utan facitvärden.

### Uppdrag 2 – Nord2000: "ska utredas" och årsmedel
- **Mål:** gå igenom de 16 "ska utredas" (13 rak väg inkl. N18R031–043 och N18R111–114 mjuk kulle, 3 skog N18R121, 123, 124 övergångsband 315–3150 Hz) och, om Joel lägger väderklasstabellen ur VTT-R-02530-06 lokalt, årsmedel N18A1–A4.
- **Filer:** `src/buller/nord2000/delmodell5.py`, `delmodell10.py`, `src/buller/testfall/` (årsmedel), `tests/buller/`, `docs/buller/logg/P-S6b.md`.
- **Rör inte:** samma som uppdrag 1; uppdrag 1 och 2 körs inte samtidigt i samma delmodellsfiler (ta dem i följd eller dela filerna).
- **Klart när:** antalet "ska utredas" är lägre än 16 eller varje kvarvarande fall har en dokumenterad metodtolkning och en fråga till Kunskapscentrum om buller; årsmedelsfallen är körbara (annars skriven stopptext om vad som saknas); inga facitvärden i git; 55 godkända kvarstår.

### Uppdrag 3 – Luft-flik i steg 4
- **Mål:** en Luft-flik i Resultat med kartlagren vindkomfort, vindfara, förstärkning, årsmedel och MKN-intervall som tal (skalor finns redan i `webb/src/flode/analyslager.ts`; bara luftens fliknamn och lagerlogik saknas).
- **Filer:** ny `webb/src/flode/luftflik.ts` med `luftflik.test.ts`; en rad där fliken registreras i `webb/src/flode/resultat.ts`; vid behov ett läsande API-anrop i `src/webb/resultatlager.py`.
- **Rör inte:** övriga flikar, `kartdel.ts`, `rapportskapa.ts`, ritverktygen, `src/luft/`, K21-kontraktet. Låg till medel krockrisk: Joels webbagent rör `resultat.ts`, så håll ändringen i den filen till registreringen och rebasa före PR.
- **Klart när:** fliken syns bara när projektet har luftkörningar, visar lagren med rätt enhet och förklarar att metoden är en skiss; `npm test`, typkontroll och build gröna; skärmbild i PR (påhittad data); ingen ändring av demokvarterets övriga flikar.

### Uppdrag 4 – Luft i Skapa rapport (rapportmall)
- **Mål:** webbens Skapa rapport (steg 5) kan välja ämnet Luft: formulär för en timserie (CSV) och en angiven bakgrund, som matas till `luft.mkn` och R2-mallen LU-01–LU-15 (i dag svarar webben 409).
- **Filer:** `src/rapport/webbflode.py`, `src/rapport/referensflode_luft.py`, `src/webb/` (rapportjobbets indata), `webb/src/flode/rapportskapa.ts` med test, `tests/rapport/`, `docs/RAPPORTER.md`.
- **Rör inte:** `src/luft/` (modellerna), `src/rapport/skisskontroll.py` utom ett tillägg om det behövs, frågekatalogens texter (`fragor.json`), BESLUT. Medel krockrisk med steg 5; kör efter uppdrag 3 eller bevaka PR:er mot `rapportskapa.ts`.
- **Klart när:** ett påhittat exempel (serie + bakgrund) ger en kontrollerad rapport (Word och PDF) via webbens jobb; texten säger att serien och bakgrunden är indata utifrån och att intervallet är en känslighet; kontrollen stoppar påståenden om att MKN klaras eller överskrids; saknad serie ger begriplig text, inte 409; tester i `tests/rapport/` och `npm test` gröna.

### Uppdrag 5 – Dynamikens ankomsttid (och hastighet) som raster
- **Mål:** lösaren `vatten.dynamik` skriver `tid_till_0_1m.tif` och `tid_till_0_2m.tif` ur `DynamikResultat.tid_till_0_1m_s`/`_0_2m_s` (rollen `ankomsttid` finns i `src/webb/resultatlager.py`) och dokumenterar att hastighet inte finns i fyll-spill-modellen (den kommer ur `vatten.2d`).
- **Filer:** `src/karna/inbyggda.py` (klassen för `vatten.dynamik`, rad ca 319–420), `tests/dynamik/` och `tests/karna/`, avsnittet i `docs/MATEMATIK.md` via `data/matematik/metoder.toml` (kör `tools/generera_matematik.py`), `docs/DATAKONTRAKT.md` (K21 additivt: ny `utdataroller`).
- **Rör inte:** `src/dynamik/tidsstegning.py` och `handelsestyrd.py` (numeriken), `vatten.2d`, webbklienten (lagerväljaren plockar upp rollen själv), cache-nycklar för befintliga körningar.
- **Klart när:** en dynamikkörning på A1–A3-fallen ger rastren med NaN där vattnet aldrig når djupet, värdena stämmer med `DynamikResultat`, gamla körningar utan filen läses fortfarande, `tests/dynamik` och `tests/karna` gröna, och UX-genomgångens tabell "Analyser i kartan" kan ändras från "finns inte" till "val under Skyfall" (anmärkning i STATUS; Joels agent ändrar dokumentet).

### Uppdrag 6 – Höjdmodellens luckor (NaN i `goteborg-test`)
- **Mål:** ta reda på varför vissa rutor har 46–85 % NaN (Joels observation; kontrollera mot plattornas yttergräns, nodata i vatten, hål större än `FYLL_MAX_M` = 150 m, rutor vid plattans kant) och rätta eller flagga: varning per ruta med skäl, rutor över en tröskel (förslag 20 %) markeras i geografins manifest, och fyllningen får bara fylla det som är fysiskt rimligt.
- **Filer:** `src/kommuner/hojdmodell.py`, `src/kommuner/k1_filpaket.py` (anrop), `src/geografi/mappa.py` (manifestets varning), `tests/kommuner/test_filpaket_mappa.py` med nya `test_hojdmodell_luckor.py`.
- **Rör inte:** K1-formatet, `src/kommuner/vattenytor.py` (annan logik, men prova samspelet), webben, `data/kommuner/*.toml` utom tröskeln som konfig. Ingen riktig data behövs: bygg syntetiska plattor med hål (stort vatten, plattkant, tomt hörn); öppna Göteborgsdata får användas om de är nåbara.
- **Klart när:** syntetiska fall som motsvarar 46–85 % NaN reproduceras och ger en tydlig varning med orsak, fyllningen gör inget orimligt (inget fylls över hav eller över stora vattenytor), tester gröna (`pytest tests/kommuner`), och STATUS beskriver hur Joel kontrollerar `goteborg-test` på servern (kommandot och vilken siffra som ska ses).

### Uppdrag 7 – Stockholmsgeografin: planeringsverktyg för S7
- **Mål:** `floden geografi planera <kommun> --avgransning <fil>`: delar en avgränsning (Hägersten-Älvsjö plus Skärholmen som polygon, öppna gränser) i 500 m-rutor, räknar rutor, buffert och uppskattad tid, minne och disk med uttryckligt märkta antaganden från `docs/benchmark/` och #114 (CPU-baslinje 4,42 s/ruta), och föreslår maskinklass (batch). Förarbete till S6/S7; ersätts av mätningen S6.
- **Filer:** ny `src/geografi/planera.py`, kommandot i `src/floden/cli.py` (en ny underkommandorad), `data/kommuner/stockholm.toml` (bara fältet för avgränsningsfil om det behövs), `tests/geografi/`, avsnitt i `docs/DRIFT.md`.
- **Rör inte:** `src/geografi/mappa.py`, `src/kommuner/` (inläsare), Stockholms stängda baskarta (Neon får aldrig se den; verktyget läser bara avgränsningen), K46 (additivt endast).
- **Klart när:** körning mot en syntetisk polygon och en öppen stadsdelsgräns ger antal rutor, uppskattade timmar och GB med antagandena utskrivna; dataklass "oppen" kontrolleras; testerna är syntetiska; uppskattningen står tydligt som uppskattning (inte mätning).

### Uppdrag 8 – J1, dagsljusdelen mot Radiance (görs bara om Joel bekräftar J1)
- **Mål:** samma gaturum (syntetiskt kvarter ur `floden demo`) räknat i Floden (SVF, soltimmar, VSC) och i Radiance (öppen programvara, ingen licens), på samma maskin; redovisa körtid (median efter uppvärmning) och avvikelse mot Radiance och mot analytiskt facit, med inställningar och versioner.
- **Filer:** ny `tools/jamfor_radiance.py`, `docs/benchmark/jamforelse/dagsljus-radiance.md` och `.json` (format efter K37), test som hoppas om Radiance saknas.
- **Rör inte:** `data/om/floden.toml` och `docs/OM-FLODEN.md` (Joel ändrar vad sidan får säga först när mätningen är granskad; ingen faktor mot någon produkt före det), `src/dagsljus/`, andra ämnen i J1 (skyfall, ekologi, buller kräver licens eller Joels beslut).
- **Klart när:** mätningen är reproducerbar med ett kommando, avvikelserna är redovisade per mått utan att något justeras för att passa, `docs/benchmark/jamforelse/README` säger vad som är och inte är jämfört, och Joel har läst resultatet innan det refereras någonstans.

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
| Kartverktyg | Kartverktyg: lager och planförslag i kartan: en kartmotor (MapLibre, exakt SWEREF-omräkning), lagerpanel (Underlag, Geometri, Planförslag, Resultat), rita och ändra hus, K9 kontrollerat av kärnan, K20 och körning i samma karta, scenarier som lokala filer; ritlogik porterad från O-Tid | Webb-B, T10, G1 | klar (se STATUS; 3D-vy och verkligt ortofoto/WMS ej prövade). Sedan 2026-11-24 bor verktygen i flödet (steg 2 och 4); den gamla kartsidan `#/karta` är borttagen och adressen omdirigeras |
| 3D-vy i Three.js | 3D-vy som egen vy i lagerpanelen (Three.js, laddas vid behov): terräng och byggnader ur K1 v2 via `/api/3d/` (rutvis, nedsamplad, kvantiserade binärbuffertar), ritat förslag live, soltimmar, VSC på fasader och vattenyta ur beräkningarna, före/efter/skillnad/svep, hovring som i kartan | Kartverktyg, G1 | klar (prövad med programvaru-WebGL; luft, buller, vegetation och vattnets tidsreglage ej i 3D, se `docs/WEBB3D.md`) |
| Webb-B | Webben begriplig för planerare: Start/Projekt/Karta/Utredningsfrågor/Om beräkningarna, demokvarteret som tydligt påhittat projekt med siffror ur körningarna, en statuskälla (`/api/moduler`), inget som låtsas fungera (förvaltning och organisationsväljare bara med inloggning och kontodata), tester mot platshållare | Demo 1, T10 | klar (se STATUS) |
| Dagsljus fas 1 | Horisontprofiler, soltimmar, SVF och VSC (BRE) som rutvisa lösare, facittester (grupp E) och `docs/DAGSLJUS.md` | Fas 0, T20 | klar |
| Dagsljus fas 1b | Dagsljus på K1 v2: `karna.geometri.las_ytmodell`, fasadpunkter K11, K9-scenarier före/efter, K ≥ 180 för skuggstudier | G1, Dagsljus fas 1 | klar |
| G2 | Luft och buller på gemensam geometri: markklass per domän (buller, luft) i `data/markklasser.toml`, luft ur K1 v2 (`karna.geometri.mosaik`/`luftgeometri`, lösarna `luft.vindkomfort`/`luft.halt`, K9 med jämförelse), bullerscen och mottagare ur K1 v2 och K11 (`buller.scen.k1v2`) | G1, Spår L (L-a, L-d, L-k), M9 (B3) | klar (mergad; buller som K21-lösare och hål i byggnader kvar) |
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
| Ekologi, habitatnätverk (K47) | Nätverk med signalarter som konfiguration (`data/ekologi/natverk/*.toml`, källa och sida per värde), p(d) = 2^(−d/d50) med märkt antagande d50 = tröskel/2, källbaserat intervall, ändligt motstånd som förval, NMD-inläsare (`data_inlasning.nmd`), VMI-lager (`data_inlasning.vmi`), nätverksval i webb och API, `docs/EKOLOGI.md` omskriven efter `docs/ekologi/habitatnatverk-svensk-stadsmiljo.md`. Metodstatus `skiss`: parametrar källbelagda (kommunala modellval), biologisk säkerhet låg. NMD och VMI är valfria lager i geografins manifest (K46, `floden geografi mappa --nmd --vmi`). Habitat: kandidater 111–114/121–124 (barrskog) och 116/117/126/127 (ädellöv), snävt urval som jämförelse, resultat kallat "potentiell livsmiljö, okänd kvalitet". **2026-11-24 (grenen `ekologi-trad-kommun`):** valfritt trädlager för ädellövsambandet byggt (`[[valfritt_lager]]` med `status = "byggt"` i `adellov-nacka.toml`: skiva 20 m (Nacka) som NMD 116, undantag från minsta area, urval på trädslag och valfritt stamomfång; indata `tradpunkter` och `anvand_trad`, geografins lager `lager.tradpunkter` via `floden geografi mappa --trad`, stängt tills `--villkor-kontrollerade`; bara punkt, trädslag och stamomfång sparas; `src/ekologi/tradlager.py`), och kommunförvalet flyttat från `data/ekologi/kommuner.toml` (borttagen) till avsnittet `[ekologi]` i `data/kommuner/<kommun>.toml` (strikt i `src/kommuner/konfig.py`). Inget nytt K-nummer (K45, K46, K47 och `floden-kommun/v1` additivt). **Kvar:** köra trädlagret mot en riktig trädinventering (stängd fil, lokalt) | klar (syntetiskt; NMD och VMI inte körda mot riktiga filer, se `docs/DRIFT.md`; grenen `ekologi-natverk`). 2026-11-23: lösaren läser NMD/VMI ur geografins manifest (indata `geografi`, K45 additivt, `rapporter-fler`) |
| Ekologi, nästa steg | Köra NMD- och VMI-inläsarna (och geografivägen) mot riktiga filer på servern; lokal körning av Stockholms nätverk mot stadens biotopdatabas; köra trädlagret (`--trad`, `anvand_trad`) mot en riktig, lokal trädinventering; registrera lagren i `src/geografi` när den finns på main; källhänvisning för artprofilernas parametrar utöver nätverkens | väntar |
| Fristående moduler | CELF i ruttanalysen (`rutt.kritiska`), `luft.kanslighet`, `buller.optimering`, `dagsljus.intervall`, `dynamik.intervall`, `rapport.r2_domaner`, `luft.partiklar`, `dagsljus.uppdatera_horisont_inkrementellt`, `buller.operator.energi.pruna_med_energibudget` | **inaktuell** (2026-11-25): ersatt av raden "Matematikmoduler: inkopplade i API:t, inte i klienten" nedan; flera av modulerna är sedan #57 inkopplade i API:t |
| Matematikmoduler: inkopplade i API:t, inte i klienten | CELF i ruttanalysen (`rutt.kritiska`, `POST /api/rutt`), `dynamik.intervall`, `dagsljus.intervall`, `luft.kanslighet` (bara MKN-intervallet), `buller.optimering` (`POST /api/analys/...`, K40–K43, PR #57); **fortfarande fristående:** adjungerad känslighet och CELF i `luft.kanslighet`, CELF i `ekologi.intervall`, `rapport.r2_domaner`, `luft.partiklar`, `dagsljus.uppdatera_horisont_inkrementellt`, `buller.operator.energi.pruna_med_energibudget` | klar som moduler (kontrollerat mot koden 2026-11-23) |
| Matematik och forskning | `docs/MATEMATIK.md` per domän (formel, kod, kontrakt, facittest, status; öppna frågor Q1–Q5) genererat ur `data/matematik/metoder.toml` och `data/mcp/metodstatus.toml` (`tools/generera_matematik.py`, test `tests/gemensamt/test_metodregister.py`); sidan `#/matematik` i appen (kontomenyn, inloggning krävs) | klar (#69) |
| Göteborg | `data_inlasning.goteborg`, lagerprofil `goteborg_baskarta`: testkommun i EPSG:3007; formatet är antaget och aldrig provat mot tjänsten, testerna är syntetiska | klar (syntetiskt) |
| Webb och drift | ASGI-adapter (`webb.asgi`), projektisolerad 3D-terrängcache, 2D/3D-svep, WebGPU-motor för dagsljus med fallback, WGSL-skuggare, säkerhetsfixar inför server | klar |
| Övrigt | alla 13 SWEREF 99-zoner (EPSG:3006–3018), SMHI MetObs-klient, tvåfraktions-PM10 (`luft.partiklar`, fristående) | klar |

## Rekommenderad fördelning på agenter

- **En agent per uppdrag**, aldrig två i samma uppdrag samtidigt.
- Uppdrag som bara rör kontrakt och tester (T00, T09) passar en snabbare, billigare modell.
- Uppdrag med numerik eller geometrisk robusthet (T01b, T02, T03, T05) behöver den starkaste modellen och en
  människa som granskar mätvärdena.
- T03 innehåller frågor till forskningsparten. De samlas i `docs/MATEMATIK.md` i stället för att lösas av agenten.

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
| R2 Beställning, frågekatalog, mottagare | Beställning (K30: syfte, skede, mottagare, ämnen, frågor, scenario) styr urval och ingress; frågekatalogen i repot nås via `GET /api/fragekatalog` och MCP `fragekatalog_sok` med markering per fråga; skissunderlag dagsljus och sol, ekologi (EK-01 till EK-15; ECA, PC och strömtäthet med belägg) och luft (LU-01 till LU-15; `luft.mkn`-intervall ur en given serie och en angiven bakgrund, kontroll mot påståenden om att MKN klaras eller överskrids; `docs/RAPPORTER.md`) | R1 | delvis (beställning, katalog, dagsljus/sol, ekologi och luft klara på skissnivå via MCP (luft inte i webbens knapp); beställningsformulär och "Skapa rapport" i webben klara (S5b); saknas: buller (spärrat: Nord2000 inte konformt), SVF och höstens ekvinox i körningen, luft utan extern serie (Floden räknar inte fram timserien)) |
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
| S5b Skapa i webben | Steg 1 "Nytt projekt" (`#/nytt`, `GET /api/geografier`, `POST /api/projekt`, MCP `projekt_skapa`; superadmin, organisationsadmin och ägare; skaparen blir utredare) och steg 5 "Skapa rapport" (`POST /api/projekt/<id>/rapporter`, asynkront jobb med status, kontroll och export till PDF och Word; ekologi förklarar att R2-mallen saknas), kontrakt K48, `docs/KONTO.md`, `docs/DRIFT.md` | S5, E1, R2 | **klar (syntetiskt)**: inte körd mot servern, riktig Google-inloggning eller riktiga geografidata; polygon är projektets riktiga avgränsning (sparas, räknas inne i polygonen i mark- och byggnadsmått; rutnätet är rektangeln runt, 2026-11-24); steg 2–3 för geografiprojekt: se S5c; steg 4 orört (se STATUS) |
| S6 Mätning av `goteborg-test` | Tid, minne och disk per ruta och körning på servern; ersätter uppskattningarna i `docs/ARKITEKTUR-DRIFT.md` 5 | S5 | väntar |
| S7 Hägersten-Älvsjö med Skärholmen | Geografi nummer två (en stadsdel), batchkörning på stor maskin (klass `batch`, CCX53/CCX63) | S6 | väntar |
| S8 Stockholm | Hela Stockholm som geografi | S7 | väntar |
| S5k Kö, arbetare och kostnadsbok | Jobbkö med pull över HTTPS (O-Tids `geo-server.ts`), klass `liten`; kostnadsbok, månadstak per klass, uppskattning och godkännande per körning, larm 50/80/100 %, daglig avstämning mot leverantören (var S5 före "geografi först", 2026-11-23) | S4 | väntar |
| S6d Demoläge | Färdigräknade exempel, besökare läser och gör lätta skisser, tunga körningar bara för superadmin (var S6) | S5k | väntar |
| S7b Benchmarksvit | `floden benchmark`: fasta fall (skyfall, dagsljus, luft, buller), tid (median), minne (RSS och tracemalloc) och avvikelse mot facit, format `floden-benchmark/v1` (K37), jämförelse `--jamfor`; GPU-prov i webbläsaren `#/prov/webgpu` (superadmin) mot serverns f64-facit (`docs/benchmark/README.md`, `docs/WEBGPU-ARKITEKTUR.md` 5) | D1 | klar för servern (baslinje i delad container, `docs/benchmark/2026-11-23-container.json`); GPU-provet kördes på Joels Mac 2026-11-23 (`docs/benchmark/2026-11-23-mac-webgpu.json`: dagsljus ≈ 79 ×, vatten 2D ≈ 25 ×; SVF-fältet 0,0126 över toleransen 0,01) |
| S9 GPU | Klass `gpu` hos Verda (L40S), L-b-mätning, vindbibliotek för ett kvarter med höga hus | S5k, L-b | väntar (Joel: konto och budget) |
| S10 Kvoter | Kvot per konto och organisation (förval 0), inbjudan med kvot; först därefter delning | S5k, S6d | väntar |

## Drift – etapp 1 i webben (E)

Etapp 1 av driftplanen (`design/server-och-drift.md`): det som behövs för att användare ska kunna ta sig in och arbeta på den
körande servern, utan serverkommandon. Backup (S4) ingår inte.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| E2 Organisationsroller | Roller medlem < ledare < organisationsadmin < ägare; ledare läser och organisationsadmin/ägare utreder i organisationens delade projekt (`organisation_synlig`, förval privat; `Register.effektiv_projektroll`, används av webb, API, körningar och MCP); inbjudan till organisationen i webben (Administration) och serverkommandona `skapa-organisation --agare`, `bjud-in-organisation`, `kontostatus`; schemaversion 5-6 | E1 | klar (#70; regeln väntar på att föras in i BESLUT, se STATUS) |
| E1 Nycklar, administration, städning, MCP | **Nyckelsida** (`/konto/nycklar`, `#/nycklar`): egna API-nycklar för Lantmäteriet och Anthropic, krypterade med Fernet i `konto_nyckel`, hemlighet i `FLODEN_NYCKELHEMLIGHET` (avstängd utan den), visas aldrig igen (bara datum och fyra sista tecken), aldrig i loggar eller svar; Lantmäteri-hämtningen använder kontots nyckel när inloggning är på. **Administration** (`/konto/admin`, `#/administration`, superadmin och organisationsadmin): bjud in till projekt (`lasare`/`utredare`), se inbjudningar och medlemmar, ändra och ta bort roll, spärra konto. **Städning:** arbetsbänken ut ur menyn och Om beräkningarna (bara adressen, bara superadmin eller lokalt); startsidan visar kontots projekt (`/api/projekt` per konto, demokvarteret inräknat); länkarna Nycklar och Administration i sidhuvudet. **MCP:** `projekt_lista` per konto; nycklar och administration når inte AI:n (`docs/MCP.md`). Dokumenterat i `docs/DRIFT.md`, `docs/KONTO.md` avsnitt 10-11, `deploy/prod.env.example` och `docker-compose.prod.yml` | S3, D1, R0 | klar (#51; klienten är typkontrollerad och enhetstestad men inte prövad i webbläsare; `FLODEN_NYCKELHEMLIGHET` ska sättas på servern, se STATUS) |
| S5c Kartverktyget för geografiprojekt | Steg 2–3 (rita, spara, planera, köra; `/api/kartverktyg/<projekt>/...` och MCP) för projekt i en geografi: projektkälla (`src/webb/projektkalla.py`) med projektets markmodell, byggnader och frågor klippta till projektets rutor och avgränsning (polygon), dataklass ur `oppenhet.toml` (MCP nekar stängda projekt), roller som övriga projekt | S5b | **klar (syntetiskt)**: provat mot den påhittade `goteborg-test`, inte mot servern eller riktig Göteborgs-/Stockholmsdata; ekologi i kartverktyget och `/api/analys/...` är fortfarande bara demokvarteret |
| U1 UX-genomgång | Genomgång av alla vyer som en planarkitekt möter dem (`docs/UX-GENOMGANG.md`, bilder i `docs/bilder/ux/`, `tools/ux_skarmbild.py`); små P1/P2-åtgärder gjorda (projektlistan, steg 3–5, roller, formulär) | E1, S5b | klar (genomgång och små åtgärder) |
| U2 Rapport på användarens körning och val | Rapporten ska bygga på det ritade förslagets körning och valda frågor, inte förvald körning (fynd 1 i UX-GENOMGANG) | U1 | klar (#97: `fragor`, `scenario`, `korning_id` i K48, panelen "Rapporten bygger på", MCP `rapport_skapa` utan `korningar`, polygonens text i beläggen) |
| U3 Ett skal | Flödets sidhuvud på alla sidor (stegrad bara i ett projekt); `#/projekt/<id>` omdirigeras till flödet, `#/fragor` är en ren frågekatalog, okända och gamla adresser omdirigeras | U1 | klar |
| U4 Ordlista, felmeddelanden, tillgänglighet | Gemensam ordlista (`docs/UX-GENOMGANG.md`), fel med handling (`flode/felhandling.ts`: Nytt projekt, projektlistan, steg 2–5), fast handlingsrad i steg 3 och steg 5, steg 3 utan förslag (Rita ett förslag / Visa projektets befintliga resultat), Nytt projekt förklarar 401/403, filfält med etikett, pekytor | U1 | klar (#98) |
| U5 Resterande UX | (a) **klar (#99)**: beräkna "läget idag" utan förslag: en körväg som kör K20 med bara `fore` för projektets frågor (`POST /api/kartverktyg/<projekt>/lage-idag/{plan,kor}`, `GET .../lage-idag/status`, MCP `kor(lage_idag=true)`, knappen "Räkna läget idag" i steg 3, källan "Läget idag" i steg 4; rapporten kräver fortfarande före och efter, se STATUS); (b) kort i stället för tabeller på smal skärm i Administration; (c) kartvärden med tangentbord (steg 4); (d) ordlistan genom steg 4 och lagerpanelen; (e) statusprickarna i rapporten med text; (f) en beställning i stället för två (steg 3 och 5); (g) projektraden med roll och organisation, tomtext för inloggad | U4 | redo (medel) |


## Ritverktyg för marken (R) – design 2026-11-24

Design: `docs/design/RITVERKTYG.md` (Joels önskan 2026-11-24: generellt kartverktyg, 3D-hus, mark i nivåer, kontroll av skarvar). Alla kontraktsändringar är additiva.

| Uppdrag | Beskrivning | Storlek | Beroenden | Status |
| --- | --- | --- | --- | --- |
| R1 Skarvkontroll | `sankgraf.skarvkontroll` (nya/djupare sänkor vid kanten på ny mark, före/efter), `markhojd` i `validera`, fältet `skarvar` (K49), tester med syntetiska markmodeller och demokvarteret | S–M | G1 | klar (#101) |
| R2 Lagermodell | Ritlager med roll (byggnad, mark, hårdgjort, vegetation, vatten, brytlinje, öppning), geometri, stil, synlighet, låsning, källa, dataklass; mallar; K50 | M | R1 | klar (#103): `karna.ritlager` (K50, översättning per roll, dataklass), `data/ritlager/standard.toml`, API additivt, ritlagerpanelen i steg 2; brytlinjer och öppningar räknas först i R3 |
| R3 Linjer och ytor | Rita linje/yta med z, fäst mot linjer, följ kant, klipp, slå ihop, vänd, förenkla, tangentbord, fingerräckvidd; K9 `brytlinje_ny`, `oppning_ny` (porterat ur O-Tid) | L | R2 | klar (#104): `linjeverktyg.ts`, `linje.ts`, `foljkant.ts`, `tangenter.ts`, `egenskaper.ts`; K9 `brytlinje_ny`/`brytlinje_bort`/`oppning_ny` räknas i skyfallet; ej prövat i webbläsare |
| R4 Mark i nivåer | Lutande yta, slänt i dagsljus, exakt kantavstånd utan halvcellstrappa, `delta_m`; `markhojd` `lutning`/`slant`, K3 `HojdYta` additivt | L | R1 | klar (kärnan och API:t, `lapp.markniva`; klientens ritverktyg är R3/R5) |
| R5 Skarv i klienten | Fynden i kartan och i steg 2, "Jämna ut skarven" (slänt eller utlopp) och ny kontroll | M | R1, R3, R4 | klar 2026-11-24 |
| R6 3D | Takformer i `extrudera`, ny mark och slänter, murar som brytlinjer, live | M–L | R3, R4 | klar 2026-11-24 |
| R7 Pekskärm | Håll för att flytta, fingerräckvidd, stora handtag, egna tangentbindningar | S–M | R3 | klar 2026-11-24 (prövad i Chromium med beröringsemulering, `tools/ritverktyg_prov.py`; ej prövad på en riktig telefon) |
| R8 Geografiprojekt i webben | Nytt projekt med OSM-bakgrund och sammanfattade varningar; markmodellen (skuggning, höjdkurvor, markhöjd) och OSM som lager i steg 2 och 4; markhöjd under pekaren; "Spara förslag" med status; prov mot syntetiskt geografiprojekt | S–M | R7 | klar 2026-11-25 (prövat i Chromium mot påhittad geografi; OSM-plattor ersatta av påhittade bilder i provet, riktiga OSM nåddes inte; ej prövat mot Göteborgs riktiga data) |

## Om Floden och jämförelseprov (2026-11-25)

Joels önskan 2026-11-25: programmet ska säga vad det kan nu, vad planen är och hur det står sig mot kommersiella verktyg. Sidan `#/om` ("Om Floden") och `docs/OM-FLODEN.md` genereras ur `data/om/floden.toml` (`tools/generera_om.py`). Jämförelsen skiljer mellan det som är uppmätt i Floden, det som tillverkarna själva har publicerat (med länk) och det som inte är mätt. Det finns ingen jämförande mätning av samma fall i Floden och i ett annat verktyg, och sidan anger därför ingen faktor mot någon produkt.

| Uppdrag | Beskrivning | Beroenden | Status |
| --- | --- | --- | --- |
| U7 Om Floden | Sidan `#/om` byggd ut (vad Floden kan nu per ämne med metodstatus och testat, arbetsflödet, planen, uppmätta tal med källa, offentliga uppgifter om andra verktyg med länk, det som inte är mätt, vad andra har som Floden saknar); `docs/OM-FLODEN.md` och `webb/src/om/data.generated.ts` genereras; test att filerna är i takt, att varje belägg står i sin källa och att körtiderna stämmer mot `docs/benchmark/` | U3 | klar (#112) |
| J1 Jämförelseprov | **Förslag, görs inte utan Joels besked.** Samma fall i Floden och i ett referensverktyg per ämne, på samma maskin: skyfall (Environment Agency Test 8A och Hunter D1 mot ett 2D-referensverktyg som Joel har licens till, till exempel TUFLOW, MIKE 21 eller InfoWorks ICM), dagsljus (SVF, soltimmar och VSC i ett gaturum mot Radiance, som är öppet), ekologi (samma landskap mot Circuitscape och Conefor, som är öppna), buller (grupp N-fallen mot SoundPLAN eller CadnaA). Mäts: körtid (median efter uppvärmning, samma maskin och trådantal), avvikelse mot referensverktyget och mot facit, inställningar och versioner redovisas. Resultat i `docs/benchmark/jamforelse/`; först därefter får `#/om` visa en faktor, och bara för det fall som är mätt. Kräver licens eller konto hos tredje part och är därför ett beslut för Joel (AGENTS.md, stoppvillkor: kostnad och extern part) | S6, T09b, B4 (för buller) | förslag (Joel bekräftar) |

## Sist – OAuth för Claude-appen (parkerat 2026-11-23, Joels beslut)

Claude-appen ansluter i dag med en personlig MCP-token som begärandehuvud (`authorization: Bearer fl_mcp_…`,
autentisering "Ingen inloggning"). OAuth-flödet (PR #58, #60–#64) når hela vägen till godkännandet men slutar
utan kod: loggen 2026-11-23 18:3x visar `GET /oauth/authorize -> 200`, `POST /oauth/authorize -> 302` och
sedan inget `/oauth/token`, och varken "kod utfärdad" eller "godkännande avvisat" loggas. Nästa steg när det
tas upp: logga vart 302:an pekar (felkod eller kod, aldrig koden själv) och prova hela flödet mot en lokal
Claude-liknande klient. Rättat hittills: Origin claude.ai (#60), Origin null (#61), okänt scope (#62),
saknade formulärfält (#64).
