#!/bin/sh
# Start av Floden i avbildningen (S1, docs/DRIFT.md).
# 1. Vägrar starta utan inloggning, om inte FLODEN_TILLAT_UTAN_INLOGGNING=1 (bara för lokal provkörning bakom Caddy).
# 2. Bygger demovärlden första gången (data/demo är en volym).
# 3. Startar webben (klient + API + /konto) på 0.0.0.0:8765 inne i containern; bara Caddy når den.
set -eu
cd /app

inloggning=$(python - <<'PY'
from pathlib import Path
from gemensamt.konfig import las_konfig
from konto import las_konto_konfig
konf = las_konto_konfig(las_konfig(Path("config.toml"), Path("config.local.toml")))
print("pa" if konf.aktiv else "av")
PY
)

if [ "$inloggning" != "pa" ] && [ "${FLODEN_TILLAT_UTAN_INLOGGNING:-0}" != "1" ]; then
  echo "Floden: inloggningen är av ([konto] inloggning i config.local.toml). Servern startar inte utan inloggning." >&2
  echo "Sätt inloggning = \"pa\" (docs/KONTO.md) eller, bara för lokal provkörning, FLODEN_TILLAT_UTAN_INLOGGNING=1." >&2
  exit 1
fi

if [ ! -d data/demo/varld ] && [ "${FLODEN_DEMO:-1}" = "1" ]; then
  echo "Floden: bygger demovärlden (första starten, kan ta några minuter) …"
  floden demo || echo "Floden: demovärlden kunde inte byggas; webben startar ändå." >&2
fi

# 4. Serverläge: asgi (uvicorn, förval) eller trad (standardbibliotekets trådserver, reserv).
exec floden webb --ingen-bygg --server "${FLODEN_SERVER:-asgi}" --host 0.0.0.0 --port 8765 \
  --konfig config.toml "$@"
