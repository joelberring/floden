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

## Markslag ur baskarta, belagd yta och ortofoto

- **Lagerprofil** (`data/lagerprofiler/*.toml`, `markmodell.lagerprofil`): mappar baskartans lager till roller och markslag.
  Repots standardprofil `stockholm_baskarta.toml` (version 2) är **härledd ur O-Tid, prövad på sprintkartan
  Älvsjö–Solberga; ska bekräftas mot full lagerlista**. Grammatik och nyckelord är lyfta från O-Tid (MIT,
  joelberring/otid, `packages/map-revision/src/stockholm-layers.ts` @ 4a446af). Lager som följer Stockholms grammatik
  (grupp + objekttyp + geometri `gy` yta/`gl` linje/`gb` blandat/`glh` höjdkurva/`s` symbol/`t` text + `B` + status
  Befintlig/Odefinierad/Skymd, avkortad; parkkartans `Park` + grupp + objekttyp) tolkas automatiskt per lager
  (`markmodell.stockholm_lager`, som O-Tids `isStockholmLayer`); övriga lager följer profilens prefixregler. Reglerna är
  reguljära uttryck i profilens `[stockholm]`-tabell:
  - ytor (`gy`) → markslag: byggnad (blir husliv/byggnadsregister, inte markslagsyta), skärmtak 2, belaggning/asfalt/plattor/
    gatsten/vagutbredning/infart/refug/parkvag/trappa 1, grus 7, grasyta/oppennaturmark/slatter/buskage 4,
    naturmarktradbevuxen 5, bassang/plaskdamm 8;
  - linjer (`gl`) → brytlinjer för skyfall: kantsten/vagavgransning (infart, refug) kantsten 0,12 m, stodmur 1,5 m,
    stenmur/bruksmur/kallmur/bullerskydd mur 1,0 m, staket 0,15 m, slänt och dike/bäck bankkant 0,30 m, gräskant bankkant
    0,05 m, trumma som öppning. O-Tid hoppar över slänt och gräskant; för skyfall är de med;
  - överhoppat: skymt (`Skymd`), text, höjdkurvor, symboler, fastighetsgränser/registerenhet, snöröjning, byggnadens linje
    (`gl`, ytan används) och ytornas kantlinjer, altan/övrig byggnadsanläggning, broar och träd;
  - status Odefinierad tas med men flaggas: ytor får bit 16 i `markslag_kalla.tif`, linjer redovisas i varningarna.
  Lager som följer grammatiken men saknar regel för objekttypen ignoreras och listas i varningarna. Rader märkta "att
  bekräfta" i profilen är val som inte är kontrollerade mot staden (infartens sänkta kant, dikets/bäckens linjeläge,
  buskage som gräs, gräskantens höjd). Egen profil för andra namn: mall `data/lagerprofiler/lokal_profil.toml.example`,
  utpekad i `config.local.toml` med `[markmodell] lagerprofil = "..."` (relativ sökväg från `[lokal_data] baskarta_mapp`)
  eller `floden markmodell --lagerprofil`. Profilens sha256 skrivs i `recept.json` (roll `lagerprofil`).
- **Prioritet** när källorna möts i `markslag.tif`: baskartans ytor > belagd yta ur laserintensitet > ortofototolkning >
  standardklass; avgörande källa och motsägelseflagga per cell i `markslag_kalla.tif` (K1 v2-tillägget i
  `DATAKONTRAKT.md`). Beslutet är ett förslag under Öppna beslut i `BESLUT.md`.
- Belagd yta kräver ett punktmoln med intensitet: `floden markmodell --laser-las FIL [--laser-epsg 3006] --belagd-yta`
  (LAS direkt, LAZ via `tools/lasclip`; `--laser-csv` har ingen intensitet) eller biblioteket
  (`markmodell.markslagskallor.belagd_mask_for_ruta`). Ingen riktig DWG, dwgread-körning eller laser-LAZ har körts här;
  allt är testat på syntetiska ytor och namn.

## Koordinatsystem

- Intern beräkning: SWEREF 99 18 00 (EPSG:3011), höjder RH 2000.
- Lantmäteriets laser och raster levereras i SWEREF 99 TM (EPSG:3006): transformera vid inläsning.
- Stockholms baskarta: SWEREF 99 18 00 (kontrollera per fil; östliga koordinater kring 150 000).
