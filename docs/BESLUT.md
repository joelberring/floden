# Beslut

Fattade beslut. Ändras bara av Joel. Nya beslut läggs överst med datum.

| Datum | Beslut | Skäl |
| --- | --- | --- |
| 2026-11-20 | Kärnan licensieras under **GPL v3**, och kod får lånas från Deltares **SFINCS** (GPL v3). | Öppen kärna krävs för acceptans; SFINCS har en beprövad 2D-lösare med subgrid. GPL gäller vid utlämning, inte vid drift som tjänst. |
| 2026-11-20 | **Eget repo**, skilt från O-Tid. Delar av O-Tid lyfts ut som paket med källhänvisning. | Klubbplattformen och skyfallsmodellen har olika användare och krav. |
| 2026-11-20 | Första spåret är **bara vatten och skyfall**. Luft och buller kan komma senare på samma geometri. | Smalt och mätbart; störst brist i dagens verktyg. |
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
