# Status

Senaste posten överst. Format i `AGENTS.md`.

## 2026-11-20 – T02 & T06 – Sänkgraf (Fill–Spill–Merge), statisk fyllning och markförluster
Gjort: Två parallella agenter byggde (1) **T02 (`src/sankgraf/`)**: 4-granne Priority-Flood + Fill–Spill–Merge sammanslagningsträd (Barnes m.fl. 2021), maskinexakta volym- och ytkurvor, flödesvägar med Manning-gångtid, Elder-Rule persistensfilter (sparar både ofiltrerad och filtrerad graf), global rutgränssammansättning, statisk fyllning, K2 (`sankgraf.parquet`/`flodesvagar.gpkg`/`cell_till_nod.tif`) och K6 (`max_djup.tif`/`run.json`), inkopplat i `floden testsvit --grupp A`; samt (2) **T06 (`src/mark_forluster/`)** på Flash-modell: Green–Ampt per markslag/jordart, grundvattentak, in-/utströmningszoner, syntetiska brunnar/delområden och K5/v1-scenarier med känslighetsjämförelse.
Mätt: `floden testsvit --grupp A`: **10/10** passerade (total körtid ~0,32 s). Relativt volymfel på A1–A9: `0.0000%` (max `6.43e-14`, krav `< 0,1 %`). Maxdjupsfel: `0.0000 cm` på A1–A8 och `0.18 cm` mot brusfri referens på A9 (`282 -> 1` verklig sänka vid persistensfaktor 2,0, krav `< 1 cm`). A4 Manning-gångtid `23.445 / 23.445 s` (`0.00 %` fel). A6 utflöde: 12/12 celler exakt i infartsintervallet. A7/A8: fri avrinning med trumma/port, falsk damm (`0.449 m`) utan trumma. T06 Green–Ampt massbalansfel `< 1e-12`. Totalt `73` enhetstester (`69` OK + `4` CI-specifika för `pyproj`/`rasterio`/`geopandas`/`pyarrow`).
Öppet: Jämförelse med Scalgo/SMHI (T09b) och känslighetskörning på riktigt provområde (T06) väntar på `data/omraden/prov1.gpkg`.
Nästa: T03 (dynamik i sänkgrafen: tidsstegning, händelsestyrd variant, enhetssvar och ensemble mot A1–A4) och T04 (projektlappar).

## 2026-11-20 – T09a & T01 (kärna) – Syntetiska fall A1–A9 och markmodellkärna
Gjort: Två parallella Flash-agenter byggde (1) alla 9 syntetiska testfall `A1–A9` i komplett K1-format (`mark.tif`, `osakerhet.tif`, `markslag.tif`, `brytlinjer.gpkg`, `oppningar.gpkg`, `recept.json`), K4 `regn.csv`, analytiskt facit (`facit.json`) och översiktsbilder (`oversikt.png`) samt uppdaterade testsvitsköraren (`src/testsvit/`), och (2) den deterministiska markmodellkärnan (`src/markmodell/`: 500 m rutnät med 25 m överlapp och skarvkontroll, brytlinje-snapping vid 0,25 m med läckagefri rasterisering, byggnadshinder och trummor/portar för A7/A8, osäkerhetsmodell per cell, K1-skrivning + `recept.json`/`run.json` samt jämförelsebild).
Mätt: Lokalt `unittest`: 44 tester körda (41 OK, 3 skippade för `pyproj`/`rasterio`/`geopandas` som verifieras i CI); `floden testsvit --grupp A`: 10/10 passerade (`A0` SKELETT + `A1`–`A9` GODKAND, alla fall 108–146 KB < 1 MB, deterministisk SHA-256 verifierad). Inga hydrauliska mätvärden ännu (väntar på T02/T03).
Öppet: Provområdets exakta avgränsning (`data/omraden/prov1.gpkg`), stadens baskarta och Lantmäteriets laser behövs för att slutföra T01 på riktigt provområde.
Nästa: T02 (sänkgraf, Fill–Spill–Merge och statisk fyllning) så att A1–A9 kan verifieras fysiskt mot volymfel < 0,1 % och maxdjupsfel < 1 cm.

## 2026-11-20 – T00 – Repo och grund på plats
Gjort: Kopierade in överlämningspaketet i `joelberring/floden`, satte upp mappstruktur enligt `docs/ARKITEKTUR.md`, `pyproject.toml` med låsta beroenden, `config.toml`, `config.local.toml.example`, `.gitignore`, GPL v3 `LICENSE`, `src/gemensamt/` (konfig, sha256, `run.json`-manifest K0/v1, koordinattransformation till EPSG:3011), CLI `floden`, testsvitens skelett (`testsvit/A0_skelett/`, `testsvit/kor.py`), enhetstester och GitHub Actions CI.
Mätt: Lokalt `unittest`: 24 tester körda (23 OK, 1 skippad i VM utan `pyproj`); `floden --help` exit 0; `floden testsvit --grupp A`: 1/1 (`A0` SKELETT, 0.0001 s). Full verifiering med `ruff`, `mypy`, `pytest` och `pyproj` körs i GitHub Actions CI.
Öppet: `pdal` ligger under optional extra `[laser]` eftersom C++-biblioteket PDAL krävs på systemet (aktiveras i T01). Provområdets avgränsning (`data/omraden/prov1.gpkg`) och tillgång till stadens baskarta/tätare laser.
Nästa: T09a (syntetiska testfall A1–A9) och T01 (markmodell) kan startas parallellt.

## 2026-11-20 – Start – överlämningspaketet skrivet
Gjort: README, AGENTS, PLAN, arkitektur, datakontrakt, beslut, data, validering och uppdragsbeskrivningar T00–T10.
Mätt: –
Öppet: provområdets exakta avgränsning; tillgång till stadens baskarta och tätare laser; person X.
Nästa: T00 (repo och grund), sedan T01 och T09a parallellt.
