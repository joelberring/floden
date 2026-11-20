# Floden webbklient (T10)

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
- `GET /api/omraden`
- `GET /api/omraden/<omrade_id>/lager`
- `POST /api/kor`

2. Starta webbklienten (i `webb/`):

```bash
npm install
npm run dev
```

Vite proxar automatiskt `/api/*` till `http://127.0.0.1:8765`.
