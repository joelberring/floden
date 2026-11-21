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
| `src/buller/scen/`, `src/buller/banor/` | bullerscen, profilextraktion, källsegment, banfinnare, korridorindex | B3, B6 |
| `src/buller/operator/`, `src/buller/lagen/` | överföringslager, klustring, inkrementell omräkning, lägen, manifest | B8, B9 |
| `src/buller/emission/`, `src/buller/testfall/`, `testsvit/N*` | källmodell väg, testfallsformat och körare | B1, B5 |

Tester ligger i `tests/buller/<delmodul>/`. Varje agent skriver sin logg i `docs/buller/logg/<paket>.md`.
