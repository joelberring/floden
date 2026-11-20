# Status

Senaste posten överst. Format i `AGENTS.md`.

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
