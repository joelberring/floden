# Status

Senaste posten överst. Format i `AGENTS.md`.

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
