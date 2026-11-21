# Bullerkontrakt

Gäller mellan delmodulerna i `src/buller/`. Ändringar kräver höjd version och en rad i `docs/buller/tolkningar.md`.
Övergripande plan: projektdokumentet `design/buller-genomforandeplan.md` (sammanfattad i `tasks/B00-buller-oversikt.md`).

## KB1 – Typer (v1)

Definieras i `src/buller/typer.py`: `Terrangprofil`, `Vader`, `P2PKarna`, `Kallsegment`, `Mottagare`, `Bana`, `Byggnad`,
`TERSBAND_HZ` (27 band, 25 Hz–10 kHz).

## KB2 – Överföring (v1)

- Överföringen för en bana är `H_db[band] = kärna.overforing_db(profil, väder) - reflexforlust_db`, alltså `L_p − L_W`.
- Nivån i en mottagare och ett band är energisumman över banor och väderklasser:
  `L[m, b] = 10·lg Σ_v vikt_v · Σ_banor 10^((L_W[källa, b] + H_db[bana, v, b]) / 10)`.
- `L_W[källa, b]` är ljudeffektnivån för den punktkälla som representerar segmentet (emissionen), i dB re 1 pW.
- A-vägd totalnivå fås genom A-vägning per band och energisumma över banden.

## KB3 – Lägen (v1)

| | `skiss` | `rapport` |
| --- | --- | --- |
| Kärna | valfri `P2PKarna` | endast `nord2000_konform = True`, annars `BullerFel` |
| Källklustring | tillåten, felgräns ur profilen | tillåten endast med felgräns ≤ 0,1 dB |
| Märkning | `"Skiss – ej för beslut"` i manifest och utdata | körmanifest K0 med status för testsvit grupp N |

Båda lägena går genom samma kod. Skillnaden ligger i toleransprofilen (`src/buller/lagen/profiler/*.toml`).

## KB4 – Manifest

Körmanifest enligt K0/v1 (`gemensamt.manifest.RunManifest`) med `modul = "buller"`. I `parametrar` ska finnas:
`lage`, `karna` (namn, version, `nord2000_konform`), `toleransprofil` (namn och sha256), `vaderklasser`,
`kontrakt_buller` (KB1–KB3-versioner). I `matvarden`: antal mottagare, källor och banor, körtid, uppmätt klustringsfel.

## Mappar och ägare

| Mapp | Innehåll | Paket |
| --- | --- | --- |
| `src/buller/akustik/` | tersband, A-vägning, luftabsorption, impedans, reflektionskoefficient, baslinjekärna | B4a |
| `src/buller/scen/`, `src/buller/banor/` | bullerscen (minnesadapter och K1 v2-adapter `scen/k1v2.py`), profilextraktion, källsegment, banfinnare, korridorindex | B3, B6 |
| `src/buller/operator/`, `src/buller/lagen/` | överföringslager, klustring, inkrementell omräkning, lägen, manifest | B8, B9 |
| `src/buller/emission/`, `src/buller/testfall/`, `testsvit/N*` | källmodell väg, testfallsformat och körare | B1, B5 |

Tester ligger i `tests/buller/<delmodul>/`. Varje agent skriver sin logg i `docs/buller/logg/<paket>.md`.

## KB1 mot K1 v2 och K11 (gemensam geometri)

KB1:s typer (`Byggnad`, `Mottagare`, `Bullerscen`) är oförändrade, och **ingen KB-version höjs**: K1 v2 och K11
bara fyller de befintliga typerna ur gemensam data (tillägg utan brytande ändring, BESLUT 2026-11-21).
`buller.scen.k1v2` bygger scenen ur K1 v2-rutor; minnesadaptern (`Bullerscen(...)`, `syntetisk_scen`,
`bullerscen_fran_k1_ruta`) finns kvar för testfallen.

| KB1 / scen | K1 v2 / K11 | Anmärkning |
| --- | --- | --- |
| `Byggnad.id` | `byggnader.gpkg` `byggnad_id` | stabilt; scenens id är K1:s id (utom delade hål, se nedan) |
| `Byggnad.e_m`, `n_m` | polygonens yttre ring (`las_byggnader`, flera rutor slås ihop på id) | |
| `Byggnad.tak_rh2000_m` | `takhojd_rh2000` | platt tak (K1 v2); `byggnad_hojd` ändrar den |
| `Byggnad.reflektionsforlust_db` | – | K1 saknar uppgift; typens standard gäller |
| `Bullerscen.terrang_rh2000_m` | `terrang.tif` (marken utan byggnader, med kantstenar) | medel per block på `cell_m` (0,25 m-multipel) |
| `Bullerscen.markslag` | `markslag.tif` | vanligaste koden per block; 9 i byggnadsceller |
| markslag → impedans, råhet | `data/markklasser.toml` `[buller]`, `[markklass.N.buller]` | EN källa för alla domäner; version `markklass/v1`, **preliminär**; `buller.scen.markklass` läser därifrån |
| `Mottagare` (fasadpunkter) | K11 `fasadpunkter` | punkten flyttas `avstand_fran_fasad_m` ut längs `normal_e/n` (K11-punkten ligger på fasadlinjen) |
| `Mottagare.byggnad_id` | `byggnad_id` | samma id som i registret, så resultat kan kopplas till byggnaden |
| `Mottagare.vaning`, `hojd_over_mark_m`, `fasadnormal_deg` | `vaning`, `hojd_over_mark_m`, `normal_azimut_grader` | |
| `Mottagare.id` (int) | `fasadpunkt_id` (text) | 56-bitars hash av `fasadpunkt_id` (`mottagar_id`), stabil mellan scenarier; `K1v2Scen.fasadpunkt_id` ger tillbaka texten |

Fasader mot en annan byggnad ger inga K11-punkter, och punkter vars flyttade läge hamnar inuti en byggnad eller
utanför terrängrastret utelämnas och räknas (`matvarden`, `varningar`). `avstand_fran_fasad_m`, `fasad_steg_m` och
`vaningshojd_m` har inga standardvärden: de hör till bullermetodens anvisningar (B9), inte till geometrin.

**Byggnader med hål (innergårdar).** `Byggnad` har bara en yttre ring. Standard är `BullerFel`;
`byggnader_med_hal="dela"` skär polygonen lodrätt genom varje hål i delar utan hål (delarna får väggar där
snittet går som inte finns i verkligheten; effekten på reflexer är inte kontrollerad) och `"yttre_ring"` fyller igen
gården (skärmningen överskattas, gårdsfasaderna saknas). Båda ger en varning. **Öppet:** riktigt stöd för hål i
`Byggnad` kräver ändring i KB1 (bullerägarens beslut, ändrar KB1 till v2).

**Rutvis lösare (nästa steg, inte gjort).** Buller är inte kopplat som K21-lösare: en lösare behöver källor
(vägar med emission, `Vag`/`Kallsegment`), väderklasser och en kärna, och operatorlagret (`buller/operator/`,
`buller/lagen/`) ligger hos bullerägaren. Att koppla `k1v2_scen` till `lagen.korning` rörde kärnan, så det är
inte gjort här. Förslag: en lösare `buller.fasadnivaer` i `src/karna/` (GPL, som luftens) med indata `vagar` och
`emission`, påverkansradie = avståndet där källan sjunkit under relevant nivå (inte `None`, eftersom bara de
vägar vars flöde ändrats berörs), och parametrar `lage`, `cell_m`, `avstand_fran_fasad_m`.
Test: `tests/buller/scen/test_k1v2.py`.

