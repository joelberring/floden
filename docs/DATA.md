# Data

## Källor

| Data | Källa | Åtkomst | Känslighet | Används i |
| --- | --- | --- | --- | --- |
| Laser (punktmoln, 1–2 p/m²) | Lantmäteriet, Laserdata Nedladdning skog | STAC (`stac-hojd`), Geotorget-konto; CC0 med användarvillkor, ange ©Lantmäteriet | Öppen (villkoren nämner personuppgifter i punktmoln) | T01 |
| Markhöjdmodell 1 m | Lantmäteriet | STAC | Öppen | Jämförelse, reserv |
| Ortofoto | Lantmäteriet | STAC / API | Öppen med villkor | T01 (förändringar), T06 |
| Tätare laser | Stockholms stad | Fråga stadsmätningen | Okänd – behandla som intern | T01b |
| Baskarta (DWG) | Stockholms stad | Intern | Intern; innehåller inga personuppgifter | T01, T01b |
| Vägnät | Trafikverket NVDB | Öppet API med nyckel | Öppen | T07 |
| Jordarter 1:25 000–1:100 000 | SGU | Öppna data (OGC API) | Öppen | T06 |
| Grundvattennivåer | SGU, stadens observationsrör | Öppet / fråga staden | Öppen / intern | T06 |
| Markfuktighet (DTW) | SLU / Skogsstyrelsen | Öppen | Öppen | T06 |
| Ledningsnät, brunnar, trummor | Stockholm Vatten och Avfall | Avtal | **Kan vara skyddsklassat** | T01, T06, M7 |
| Nederbörd, radar | SMHI | Öppna data | Öppen | T03, T07 |
| Skyfallskartering (beta) | SMHI | Webbtjänst | Öppen | T09b |
| Testfall för 2D-modeller | Environment Agency (UK) | Nedladdning | Öppen | T09c |
| Räddningsstationer, särskilda objekt | Räddningstjänsten, staden | Fråga | Inga personuppgifter | T07 |

## Regler

- Öppen data laddas ner till `data/cache/` (inte i git) och cachas med kontrollsumma.
- Intern data ligger i en lokal mapp som anges i `config.local.toml`. Den checkas aldrig in och skickas aldrig till en extern AI.
- Ledningsdata används bara lokalt eller i stadens miljö. Kod för ledningsnät testas med syntetiska nät.
- Ange alltid källan i rapporter och kartor (©Lantmäteriet, SGU, SMHI, NVDB osv.).

## Koordinatsystem

- Intern beräkning: SWEREF 99 18 00 (EPSG:3011), höjder RH 2000.
- Lantmäteriets laser och raster levereras i SWEREF 99 TM (EPSG:3006): transformera vid inläsning.
- Stockholms baskarta: SWEREF 99 18 00 (kontrollera per fil; östliga koordinater kring 150 000).
