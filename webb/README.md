# Floden webbklient (T10)

Lokalt webbgränssnitt för Floden. Som standard **utan inloggning** (`inloggning = "av"`): servern
lyssnar bara på `127.0.0.1`, ingenting skickas till någon extern tjänst (utom den valfria
bakgrundskartan, se nedan) och resultaten sparas på den egna disken. Med `inloggning = "pa"` (se
`docs/KONTO.md`) visar sidhuvudet *Logga in med Google*, *Inloggad som* och *Logga ut*, alla POST
bär CSRF-huvudet `X-Floden-CSRF` och API:t kräver en session. Enklast: `floden demo` och
`floden webb` (se README, "Visa Floden på din dator"); `floden webb` bygger klienten vid behov.

## Starta lokalt

```bash
# 1. API (i repo-roten)
PYTHONPATH=src python -m webb.api                 # http://127.0.0.1:8765
#    --port 8800, --fragemapp <mapp> (kan upprepas)

# 2. Klient (i webb/)
npm ci
npm run dev                                       # http://127.0.0.1:5173, proxar /api till :8765
```

Exempelfrågorna ligger i `exempel/fragor/`. Dagsljusfrågan behöver det syntetiska demokvarteret:

```bash
PYTHONPATH=src python -m floden.cli dagsljus demo --fore-efter   # bygger data/cache/exempel/dagsljus-kvarter
```

Vattenfrågan (`vatten_a1_uppfyllnad.toml`) använder testsvitens A1 och behöver inget förarbete.

## Sidan Utredningsfrågor (`#/fragor`)

1. **Välj fråga.** Listan är frågefilerna (K20, `.toml`/`.json`) i frågemapparna. En fil som inte går att
   tolka visas som ogiltig med kärnans felmeddelande; saknad indata visas tillsammans med frågefilens
   inledande kommentar (där står kommandot som bygger den).
2. **Plan.** Kärnans `planera`: antal jobb, hur många som redan finns i cachen (K22) och hur många som
   ska köras, per analys, med rutor ritade i SWEREF-koordinater (mörk = allt i cachen).
3. **Kör.** Körningen går i en bakgrundstråd i API-processen, ett jobb i taget (ingen extern kö). Status
   och framsteg (antal jobb vars nyckel nu finns i cachen) hämtas var 0,7 s.
4. **Resultat.** Varningar från lösarna överst, kartvy med lager per analys (bas, scenario, skillnad;
   samma färgskala för bas och scenario), mätvärden per jobb (scenario och ruta), jämförelsetabell
   (`efter − före`) och förhandsvisning av CSV/Parquet-filer. Till varje lager finns länkar till
   GeoTIFF-filerna (full upplösning, en per ruta).

Kartvyn (`src/rastervy.ts`) ritar rastren direkt i **SWEREF 99 18 00 (EPSG:3011)** på en duk, med
koordinatnät och skalstock. Ingen kartbakgrund hämtas som standard: de syntetiska rutorna har inga
verkliga koordinater att lägga en bakgrundskarta mot. Under *Inställningar* under kartan kan man slå på
en bakgrundskarta (OpenStreetMap-plattor, plattmallen kan ändras); då hämtas kartplattor från den
tjänsten. Valet sparas i webbläsaren. Omräkningen SWEREF till WGS 84 (`src/sweref.ts`) görs bara för
att placera plattorna; Flodens beräkningar sker i EPSG:3011 i kärnan, aldrig i klienten.

## API

Allt läser via kärnans API (`karna.utredning`, `karna.korplan`, `karna.resultatcache`,
`karna.analyslosare`); webben har ingen egen beräkningslogik.

| Metod och adress | Svar |
| --- | --- |
| `GET /api/moduler` | De fyra produktmodulerna (vatten, luft, buller, dagsljus) med en enda statuskälla: `status` (`finns_i_demon`, `finns_ej_forberaknad`, `under_utveckling`) och färdig `status_text`. Kärnans lösare och demons körningar avgör vatten och dagsljus; luft och buller läses ur `data/webb/modulstatus.toml` (bullrets testfallstal med källa `docs/buller/logg/P-S2.md`, kontrollerat av ett test). Lösare har `visningsnamn`. |
| `GET /api/projekt`, `GET /api/projekt/<id>` | Demokvarteret: mått ur körningarnas resultat (mark utan tak), regn, vyer och teknisk information. Mått som inte går att räkna utelämnas. |
| `GET /api/fragor` | K20-frågorna i frågemapparna: analyser, scenarier, jämförelser, saknad indata, felmeddelande. |
| `GET /api/fragor/<ref>` | En fråga. `ref` är filnamnet utan ändelse. |
| `POST /api/fragor/<ref>/plan` | Plan: `sammanfattning` (jobb, i_cache, att_kora), `per_analys`, `scenarier` med rutor och utbredning, `jobb`. 422 med kärnans felmeddelande vid fel (t.ex. saknad bas). |
| `POST /api/fragor/<ref>/kor` | 202 med `korning_id`. Planeras direkt (fel ger 422), körs sedan i bakgrunden. Har frågan redan en pågående körning returneras den (`ateranvand: true`). |
| `GET /api/korningar`, `GET /api/korningar/<id>` | Status `ko`/`kor`/`klar`/`fel`, `framsteg {klara, totalt}`, `fel`. |
| `GET /api/korningar/<id>/resultat` | `tabeller` (mätvärden per jobb), `jamforelser`, `varningar` (grupperade, med var de uppstod), `lager`, `datatabeller`. 409 om körningen inte är klar. |
| `GET /api/korningar/<id>/lager/<lager_id>.png` | PNG-förhandsvisning (transparent RGBA, nordkant överst, rutnätets egna koordinater). Ritas på servern ur resultatrastren. |
| `GET /api/korningar/<id>/fil/<sökväg>` | En fil i resultatmappen (GeoTIFF, CSV, JSON). Inget utanför resultatmappen går att läsa. |
| `GET /api/korningar/<id>/tabell/<sökväg>` | De första 200 raderna ur en CSV- eller Parquet-fil. |

Ett lager (`lager[]`) har `id` (`<analys>.<scenario>.<roll>` eller `skillnad.<jämförelse>`), `typ`
(`bas`/`scenario`/`skillnad`), `primar` (lösarens första utdataroll; övriga är flaggor och kontroller),
`utbredning` i EPSG:3011, `skala`, `teckenforklaring`, `png` och `geotiff[]`. Förhandsvisningen
nedsamplas om den är större än 2048 px; det anges i lagret och som varning.

### Konfiguration

`[webb]` i `config.toml` (relativa sökvägar räknas från repots rot):

```toml
[webb]
fragemappar = ["exempel/fragor"]            # kan åsidosättas med FLODEN_FRAGEMAPPAR eller --fragemapp
cachemapp = "data/cache/karna"              # kärnans resultatcache (samma som `floden fraga`)
korningsmapp = "data/cache/webb/korningar"  # webbens körningar: korning.json, resultat/, webb/ (PNG)
```

Körningar ligger kvar på disken och läses in igen när servern startas om; en körning som pågick när
servern stoppades markeras som misslyckad. Inget rensas automatiskt.

## Tema

`src/tema.css` har alla designtoken som CSS-variabler (svartvitt, hårfina linjer, raka hörn,
Inter/Inter Tight; färg bara i kartdata och varningar). `src/fragor.css` använder bara variablerna.

## Övriga sidor

| Adress | Sida |
| --- | --- |
| `#/` | Start: tre steg och modulernas status (`GET /api/moduler`) |
| `#/projekt` (alias `#/oversikt`) | Demokvarteret med siffror ur körningarna (`GET /api/projekt`) |
| `#/karta/<projekt>?vy=<vy>` | Kartan: före, efter, skillnad, svep, sida vid sida, avläsning och byggnadslager |
| `#/fragor` och `#/fragor/<ref>` | Utredningsfrågor (se ovan) |
| `#/om` | Om beräkningarna: modulerna och *Kontroll av beräkningarna* (A1–A9) |
| `#/arbetsbank/<id>` | Kontrollfallens äldre arbetsbänk (MapLibre), nåbar bara från *Om beräkningarna* |
| `#/forvaltning` | Visas bara när inloggning är på och kontoservern har data för kontot |

Menyn är densamma på alla sidor och blir en *Meny*-knapp under 1100 px. Inget i gränssnittet visar
hakparentes eller påhittade tal; ett test (`tests/webb/test_klient_platshallare.py`,
`src/platshallare.test.ts`) söker igenom klientens text efter `[Ord]`-platshållare.

## Bygg och test

```bash
npm ci                # exakt enligt package-lock.json (CI gör likadant)
npm run typecheck
npm test              # Nodes testkörare: SWEREF-omräkningen mot pyproj (1 mm)
npm run build
```

API-testerna ligger i `tests/webb/` (`PYTHONPATH=src python -m pytest -q tests/webb`). Skärmbilderna i
`docs/bilder/webb-*.png` tas med `python tools/webb_skarmbild.py` (playwright och Chrome).
