# Beslut

Fattade beslut. Ändras bara av Joel. Nya beslut läggs överst med datum.

| Datum | Beslut | Skäl |
| --- | --- | --- |
| 2026-11-21 | **Alla spår är startade:** vatten (skyfall), luft, buller (Nord2000), dagsljus och en gemensam kärna. Ersätter raden "första spåret är bara vatten och skyfall". | Joels beslut; spåren byggs parallellt av separata agenter över samma geometri. |
| 2026-11-21 | **Repots befintliga kontrakt behålls:** sha256, COG, EPSG:3011 och 0,25 m. Systemdesignens förslag JAX, Zarr och BLAKE3 införs inte nu. | Joels beslut; kontrakten K0–K8 används redan av alla moduler. |
| 2026-11-21 | **De två kärnorna slås ihop** (`fas0-2` och `t20-gemensam-karna` → `karna-sammanslagen`). Kontrakten **K9** (planscenario), **K10** (utredningsfråga), **K20–K22** (körbar fråga, rutvis lösare, resultatcache) gäller. | Joels beslut; ett gemensamt API för alla lösare. |
| 2026-11-21 | **#10–#18 mergade till `main`** via provsammanfogningen `integration-2026-11-21` i beroendeordning, med full testsvit efter varje steg. | Joels beslut: merga om allt är grönt (459 OK, ruff/mypy rena, testsvit 13/13). |
| 2026-11-21 | **K1 höjs till v2** med gemensam geometri (byggnadsregister, terräng utan byggnader, vegetation), bakåtkompatibelt. Byggs direkt och granskas i PR. | Joels beslut; luft, buller och dagsljus behöver samma geometri, och K9:s `byggnad_*`/`vegetation` kräver den. |
| 2026-11-21 | **Bullerspåret har egna kontrakt KB1–KB4** (`docs/buller/KONTRAKT.md`) för att inte krocka med kärnans K-nummer. Gäller när bullergrenen mergas. | Parallellt arbete; kontrakten ska på sikt anslutas till K1 v2. |
| 2026-11-20 | Kärnan licensieras under **GPL v3**, och kod får lånas från Deltares **SFINCS** (GPL v3). | Öppen kärna krävs för acceptans; SFINCS har en beprövad 2D-lösare med subgrid. GPL gäller vid utlämning, inte vid drift som tjänst. |
| 2026-11-20 | **Eget repo**, skilt från O-Tid. Delar av O-Tid lyfts ut som paket med källhänvisning. | Klubbplattformen och skyfallsmodellen har olika användare och krav. |
| 2026-11-20 | *(Ersatt 2026-11-21, se ovan.)* Första spåret är **bara vatten och skyfall**. Luft och buller kan komma senare på samma geometri. | Smalt och mätbart; störst brist i dagens verktyg. |
| 2026-11-20 | **Karttapullautin används inte.** Höjdmodellen byggs med egen kedja (PDAL, GDAL, egna steg). | Karttapullautin är gjord för orienteringskartor, inte hydraulik. |
| 2026-11-20 | **Byggnader från baskartans fotavtryck**, inte från laser. Kantstenar och murar som brytlinjer från baskartan. | Lantmäteriets laser (1–2 punkter/m²) ser inte kantstenar. |
| 2026-11-20 | **Tre nivåer** (graf, hybrid, full 2D) med samma resultatformat. | Snabbhet i skiss, kontroll i utredning, jämförbarhet. |
| 2026-11-20 | **Språkmodellen genererar aldrig tal.** Rapporttext skrivs från strukturerat resultat och kontrolleras automatiskt. | Spårbarhet och acceptans. |
| 2026-11-20 | **Gränsvärden för fordon, entréer och gångväg är parametrar** som bestäms med räddningstjänsten. Inga standardvärden används skarpt. | Det finns inget officiellt värde; ansvaret ska ligga hos dem som fattar beslut. |
| 2026-11-20 | **Börja i Python, flytta heta delar till Rust** (wgpu för GPU) när de är validerade. | Snabb iteration först, prestanda sedan. |
| 2026-11-21 | **Blandade licenser.** Luftdelen (`src/luft/`) licensieras under **MIT** (egen `LICENSE` i mappen, SPDX-rad i varje fil). Kärnan med lånad SFINCS-kod förblir GPL v3. Luft får inte importera SFINCS-härledd kod. | Joels beslut; tillåtande licens underlättar spridning och samarbete, MIT är förenlig med GPL v3. |
| 2026-11-21 | **Luft startas** som spår L (ersätter "bara vatten först" för luft). 3D krävs: LBM-LES (D3Q19) för vind, linjär spridningsoperator med adjungerad lösning på medelfältet. Se `docs/LUFT.md`. | Joel vill ha 3D-luft och partiklar; separata agenter bygger vatten, buller, dagsljus och gemensam kärna. |
| 2026-11-21 | **Luftens lösare skrivs mot ett array-API (NumPy/CuPy)** i stället för Rust/wgpu i första versionen. Samma kod körs på CPU i tester och CUDA-GPU på server. | LBM är minnesbandbreddsbunden och enkel att uttrycka vektoriserat; Rust/wgpu kan bli aktuellt när lösaren är validerad. |
| 2026-11-20 | **Provområde i Hägersten-Älvsjö**, ca 1 km². | Joels arbetsområde och lokalkännedom. |

## Öppna beslut

- Exakt avgränsning av provområdet.
- Om och hur stadens tätare laser och baskarta får användas utanför stadens miljö.
- Leverantör för GPU-tid när full 2D körs i större skala.
- Vem som är person X (numerik och hydraulik).
- Bullergrenen: får testfallens tal (Miljøstyrelsens referenslaboratorium, RL 18-18) och SP 2006:12-koefficienterna ligga i det publika repot? Förslag: kod och koefficienter öppet, testfallens tal i lokal datamapp tills Referenslaboratoriet svarat.
- Buller: regeln för när testfall får avgöra mellan läsningar av metodtexten, och de fem förval som valts med den (`docs/buller/tolkningar.md`).
- Buller: skog av i rapportläget, på i skissläget; språng vid byte av viktigaste kant (följ metoden eller redovisat avsteg); markslag → impedansklass.
- Kontakt med Kunskapscentrum om buller för granskning av tolkningarna.
- Driftplanens beslutslista (`docs/ARKITEKTUR-DRIFT.md` avsnitt 7).
- K2-tilläggsfilerna från fas 3: K2/v1 eller v1.1.
- `AGENTS.md` säger GPL v3 för hela kärnan; uppdaterad till att nämna MIT för `src/luft/` (se raden 2026-11-21).

