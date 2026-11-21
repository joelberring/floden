# Floden webbklient (T10)

## Sidor och form (2026-11-21)

Fem sidor med en enkel hash-router (`src/router.ts`, sidorna i `src/sidor.ts`):

| Adress | Sida |
| --- | --- |
| `#/` | Startsida med modulerna |
| `#/oversikt` | Första inre sida: områden, körningar, beräkningsserver |
| `#/projekt/<omrade_id>?modul=<id>` | Projektsida med en flik per modul |
| `#/karta/<omrade_id>` | Kartsidan: arbetsbänken (`main.ts`) i tre kolumner |
| `#/forvaltning` | Förvaltningssida: kopplingsregler, medlemmar |

- **Tema:** `src/tema.css` har alla designtoken som CSS-variabler (svartvitt, hårfina linjer,
  raka hörn, Inter/Inter Tight; färg bara i kartdata). Nya moduler använder variablerna.
- **Moduler:** listan kommer från `GET /api/moduler` (`src/webb/moduler.py`). En modul visas som
  i drift när den registrerar en analystyp i kärnans frågelager, så webben behöver inte ändras.
- **Platshållare:** projekt, förvaltningar, medlemmar och körhistorik saknar backend tills
  kontoservern finns. Där står hakparentes, aldrig påhittade tal eller namn.
  Inloggningsknapparna gör inget än (`TODO(kontoservern)` i `sidor.ts`).

Webbvy och lokalt HTTP/JSON-API för att inspektera markmodellen, sänkor, flödesvägar,
osäkerhetspercentiler (`P10`/`P50`/`P90`) samt skapa projektlappar (K3/v1) och jämföra
före/efter utan kommandoraden.

## Starta lokalt

1. Starta Python-backend (i repo-roten):

```bash
PYTHONPATH=src python3 -m webb.api
```

Servern lyssnar på `http://127.0.0.1:8765` och exponerar:
- `GET /api/halsa`
- `GET /api/moduler`
- `GET /api/omraden`
- `GET /api/omraden/<omrade_id>/lager`
- `POST /api/kor`

2. Starta webbklienten (i `webb/`):

```bash
npm install
npm run dev
```

Vite proxar automatiskt `/api/*` till `http://127.0.0.1:8765`.
