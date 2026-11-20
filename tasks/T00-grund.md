# T00 – Repo och grund

**Mål:** ett repo där alla andra uppdrag kan börja direkt: struktur, konfiguration, manifest, testsvitens skelett och CI.

**Läs först:** `AGENTS.md`, `docs/ARKITEKTUR.md` (repo-struktur), `docs/DATAKONTRAKT.md` (K0).

## Steg
1. Skapa strukturen i `docs/ARKITEKTUR.md`. Python-projekt med `pyproject.toml` (Python 3.12), paket under `src/`.
2. Beroenden: numpy, rasterio, pyproj, shapely, geopandas, pyarrow, pdal (Python-bindning), pytest. Lås versioner.
3. `config.toml` med standardinställningar och `config.local.toml.example` för lokala sökvägar. Lägg `config.local.toml`, `data/cache/` och all intern data i `.gitignore`.
4. Gemensam modul `src/gemensamt/`: läsning av konfiguration, sha256 av filer, skrivning och läsning av `run.json` (K0), koordinattransformation till EPSG:3011.
5. Kommandoradsverktyg `floden` med underkommandon som är tomma i början (`markmodell`, `sankgraf`, `kor`, `testsvit`).
6. `testsvit/` med skelett: ett fall = en mapp med `indata/`, `facit/` och `fall.toml`. Ett körskript som kör alla fall och skriver en tabell med mått.
7. CI (GitHub Actions): lint, typkontroll, enhetstester, testsvit A.
8. Licensfil GPL v3.

## Acceptans
- `floden --help` fungerar.
- Ett exempel på `run.json` skrivs och läses tillbaka i ett test.
- CI går igenom på ett tomt testfall.

## Stoppa och fråga
- Om repot ska ligga på GitHub och under vilket namn (förslag: `joelberring/floden`, privat tills vidare).
