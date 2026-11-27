#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Kontrollerad automatisk uppdatering av Floden på server (docs/DRIFT.md).
#
# Flöde:
#   1. Tar exklusivt lås (flock -n) så överlappande körningar avslutar rent (slutkod 0).
#   2. Läser körande floden-containers avbildnings-ID/digest och hämtar målavbildning från ghcr.io.
#   3. Är avbildningen oförändrad: avslutar 0 utan omstart.
#   4. Vid ny avbildning:
#      a) Kör online-säker SQLite-backup (avbryter helt om backupen misslyckas).
#      b) Taggar föregående avbildning som floden-rollback:senaste.
#      c) Startar om enbart tjänsten floden (docker compose up -d --no-deps floden).
#      d) Pollar /api/halsa inifrån containern upp till FLODEN_HALSA_TIMEOUT_S (förval 30s).
#      e) Vid misslyckad hälsokontroll: återtaggar floden-rollback:senaste till målavbildningen,
#         startar om floden, verifierar återställd hälsa, loggar tydligt fel till stderr och
#         avslutar med slutkod 1.
#
# Flaggor:
#   --torrkor    Kontrollera ny avbildning och logga planerade steg utan backup/omstart.
#   -h, --help   Visa hjälptext.
set -euo pipefail

logga() {
  printf '%s [floden-auto-uppdatera] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

fel() {
  printf '%s [floden-auto-uppdatera] FEL: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

visa_hjalp() {
  cat <<'EOF'
Användning: floden-auto-uppdatera.sh [--torrkor] [-h|--help]

Miljövariabler (kan sättas i miljön eller via systemd EnvironmentFile=/srv/floden/.env):
  FLODEN_COMPOSE_FIL        Sökväg till compose-fil (förval: docker-compose.prod.yml)
  FLODEN_IMAGE              Avbildningsnamn (förval: ghcr.io/joelberring/floden)
  FLODEN_TAG                Avbildningstagg (förval: main)
  FLODEN_HALSA_URL          Hälso-URL inuti containern (förval: http://127.0.0.1:8765/api/halsa)
  FLODEN_HALSA_TIMEOUT_S    Max väntetid på hälsokontroll i sekunder (förval: 30)
  FLODEN_HALSA_INTERVALL_S  Paus mellan hälsokontroller i sekunder (förval: 2)
  FLODEN_LASFIL             Låsfil för flock (förval: /tmp/floden-auto-uppdatera.lock)
  FLODEN_S3_TYP             Om satt till "s3" skickas --s3 --s3-prefix backup/konto till backup
EOF
}

# Läser en enskild icke-hemlig konfigurationsnyckel ur .env utan att köra skalkod.
las_env_varde() {
  local fil="$1"
  local nyckel="$2"
  [[ -f "$fil" ]] || return 1
  local rad varde=""
  local re_tom='^[[:space:]]*(#|$)'
  local re_rad="^[[:space:]]*(export[[:space:]]+)?${nyckel}=(.*)$"
  local re_dubbel='^"(.*)"$'
  local re_enkel="^'(.*)'$"
  while IFS= read -r rad || [[ -n "$rad" ]]; do
    rad="${rad%$'\r'}"
    [[ "$rad" =~ $re_tom ]] && continue
    if [[ "$rad" =~ $re_rad ]]; then
      varde="${BASH_REMATCH[2]}"
      if [[ "$varde" =~ $re_dubbel ]]; then
        varde="${BASH_REMATCH[1]}"
      elif [[ "$varde" =~ $re_enkel ]]; then
        varde="${BASH_REMATCH[1]}"
      fi
    fi
  done < "$fil"
  [[ -n "$varde" ]] || return 1
  printf '%s\n' "$varde"
}

TORRKOR=0
while (($# > 0)); do
  case "$1" in
    --torrkor)
      TORRKOR=1
      shift
      ;;
    -h|--help)
      visa_hjalp
      exit 0
      ;;
    *)
      fel "Okänd flagga: $1 (använd --help för hjälp)."
      exit 2
      ;;
  esac
done

COMPOSE_FIL="${FLODEN_COMPOSE_FIL:-docker-compose.prod.yml}"
if [[ ! -f "$COMPOSE_FIL" ]]; then
  fel "Compose-filen finns inte: $COMPOSE_FIL"
  exit 1
fi

ENV_FIL="${FLODEN_ENV_FIL:-$(dirname "$COMPOSE_FIL")/.env}"

# Läs icke-hemliga inställningar ur .env endast om de inte redan satts i miljön.
IMAGE_BAS="${FLODEN_IMAGE:-}"
if [[ -z "$IMAGE_BAS" ]]; then
  IMAGE_BAS="$(las_env_varde "$ENV_FIL" FLODEN_IMAGE 2>/dev/null || true)"
fi
IMAGE_BAS="${IMAGE_BAS:-ghcr.io/joelberring/floden}"

TAG="${FLODEN_TAG:-}"
if [[ -z "$TAG" ]]; then
  TAG="$(las_env_varde "$ENV_FIL" FLODEN_TAG 2>/dev/null || true)"
fi
TAG="${TAG:-main}"

HALSA_URL="${FLODEN_HALSA_URL:-}"
if [[ -z "$HALSA_URL" ]]; then
  HALSA_URL="$(las_env_varde "$ENV_FIL" FLODEN_HALSA_URL 2>/dev/null || true)"
fi
HALSA_URL="${HALSA_URL:-http://127.0.0.1:8765/api/halsa}"

HALSA_TIMEOUT_S="${FLODEN_HALSA_TIMEOUT_S:-}"
if [[ -z "$HALSA_TIMEOUT_S" ]]; then
  HALSA_TIMEOUT_S="$(las_env_varde "$ENV_FIL" FLODEN_HALSA_TIMEOUT_S 2>/dev/null || true)"
fi
HALSA_TIMEOUT_S="${HALSA_TIMEOUT_S:-30}"

HALSA_INTERVALL_S="${FLODEN_HALSA_INTERVALL_S:-}"
if [[ -z "$HALSA_INTERVALL_S" ]]; then
  HALSA_INTERVALL_S="$(las_env_varde "$ENV_FIL" FLODEN_HALSA_INTERVALL_S 2>/dev/null || true)"
fi
HALSA_INTERVALL_S="${HALSA_INTERVALL_S:-2}"

LASFIL="${FLODEN_LASFIL:-}"
if [[ -z "$LASFIL" ]]; then
  LASFIL="$(las_env_varde "$ENV_FIL" FLODEN_LASFIL 2>/dev/null || true)"
fi
LASFIL="${LASFIL:-/tmp/floden-auto-uppdatera.lock}"

S3_TYP="${FLODEN_S3_TYP:-}"
if [[ -z "$S3_TYP" ]]; then
  S3_TYP="$(las_env_varde "$ENV_FIL" FLODEN_S3_TYP 2>/dev/null || true)"
fi

re_pos_int='^[1-9][0-9]*$'
re_nonneg_int='^[0-9]+$'
if [[ ! "$HALSA_TIMEOUT_S" =~ $re_pos_int ]]; then
  fel "FLODEN_HALSA_TIMEOUT_S måste vara ett positivt heltal (fick: '$HALSA_TIMEOUT_S')."
  exit 2
fi
if [[ ! "$HALSA_INTERVALL_S" =~ $re_nonneg_int ]]; then
  fel "FLODEN_HALSA_INTERVALL_S måste vara ett heltal >= 0 (fick: '$HALSA_INTERVALL_S')."
  exit 2
fi

image_sista="${IMAGE_BAS##*/}"
if [[ "$image_sista" == *:* ]]; then
  MAL_IMAGE="$IMAGE_BAS"
else
  MAL_IMAGE="${IMAGE_BAS}:${TAG}"
fi
ROLLBACK_TAG="floden-rollback:senaste"

if ! command -v flock >/dev/null 2>&1; then
  fel "Kommandot 'flock' saknas i PATH; kan inte garantera exklusivt lås."
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  fel "Kommandot 'docker' saknas i PATH."
  exit 1
fi

mkdir -p "$(dirname "$LASFIL")"
exec 9>"$LASFIL"
if ! flock -n 9; then
  logga "En annan uppdatering pågår redan (låsfil: $LASFIL); avslutar utan åtgärd."
  exit 0
fi

# Hämta aktivt container-ID och dess nuvarande avbildnings-ID / RepoDigest.
CONTAINER_ID="$(docker compose -f "$COMPOSE_FIL" ps -q floden | head -n 1 || true)"
if [[ -z "$CONTAINER_ID" ]]; then
  fel "Ingen körande floden-container hittades för $COMPOSE_FIL; avbryter för att inte kringgå manuellt underhåll."
  exit 1
fi

GAMMAL_IMAGE_ID="$(docker inspect --format '{{.Image}}' "$CONTAINER_ID")"
if [[ -z "$GAMMAL_IMAGE_ID" ]]; then
  fel "Kunde inte läsa avbildnings-ID för körande container $CONTAINER_ID."
  exit 1
fi
GAMMAL_DIGEST="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$GAMMAL_IMAGE_ID" 2>/dev/null || true)"

logga "Kontrollerar uppdatering för $MAL_IMAGE (körande: $GAMMAL_IMAGE_ID) …"
if ! docker compose -f "$COMPOSE_FIL" pull -q floden; then
  fel "Kunde inte hämta avbildning ($MAL_IMAGE) med docker compose pull."
  exit 1
fi

NY_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$MAL_IMAGE")"
if [[ -z "$NY_IMAGE_ID" ]]; then
  fel "Kunde inte läsa avbildnings-ID för hämtad målavbildning $MAL_IMAGE."
  exit 1
fi
NY_DIGEST="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$MAL_IMAGE" 2>/dev/null || true)"

if [[ "$GAMMAL_IMAGE_ID" == "$NY_IMAGE_ID" ]] && {
  [[ -z "$GAMMAL_DIGEST" ]] || [[ -z "$NY_DIGEST" ]] || [[ "$GAMMAL_DIGEST" == "$NY_DIGEST" ]]
}; then
  logga "Ingen ny avbildning (oförändrad: $NY_IMAGE_ID${NY_DIGEST:+ / $NY_DIGEST}); avslutar utan omstart."
  exit 0
fi

BACKUP_CMD=(
  floden konto backup
  --ut data/lokal/backup/konto
  --behall-antal 48
  --behall-dagar 14
)
if [[ "$S3_TYP" == "s3" ]]; then
  BACKUP_CMD+=(--s3 --s3-prefix backup/konto)
fi

if [[ "$TORRKOR" -eq 1 ]]; then
  logga "[torrkörning] Ny avbildning upptäckt: $GAMMAL_IMAGE_ID -> $NY_IMAGE_ID"
  logga "[torrkörning] Skulle köra backup: docker compose -f $COMPOSE_FIL exec -T floden ${BACKUP_CMD[*]}"
  logga "[torrkörning] Skulle tagga återställningsankare: docker tag $GAMMAL_IMAGE_ID $ROLLBACK_TAG"
  logga "[torrkörning] Skulle starta om: docker compose -f $COMPOSE_FIL up -d --no-deps floden"
  logga "[torrkörning] Skulle polla $HALSA_URL i upp till ${HALSA_TIMEOUT_S}s och rulla tillbaka vid fel."
  exit 0
fi

kontrollera_halsa_en_gang() {
  docker compose -f "$COMPOSE_FIL" exec -T floden \
    python -c "import json, sys, urllib.request; d = json.loads(urllib.request.urlopen(sys.argv[1], timeout=3).read()); sys.exit(0 if d.get('status') == 'ok' else 1)" \
    "$HALSA_URL" >/dev/null 2>&1
}

vanta_pa_halsa() {
  local timeout_s="$1"
  local start_s="$SECONDS"
  local deadline_s=$((start_s + timeout_s))
  local forsok=0
  local kvar=0
  while true; do
    forsok=$((forsok + 1))
    if kontrollera_halsa_en_gang; then
      logga "Hälsokontroll OK ($HALSA_URL) efter ${forsok} försök ($((SECONDS - start_s))s)."
      return 0
    fi
    kvar=$((deadline_s - SECONDS))
    if (( kvar <= 0 )); then
      return 1
    fi
    if (( HALSA_INTERVALL_S == 0 )); then
      sleep 0.05
    elif (( kvar < HALSA_INTERVALL_S )); then
      sleep "$kvar"
    else
      sleep "$HALSA_INTERVALL_S"
    fi
  done
}

rulla_tillbaka() {
  local orsak="$1"
  fel "ÅTERSTÄLLNING: $orsak ($NY_IMAGE_ID)."
  fel "ÅTERSTÄLLNING: Återtaggar $ROLLBACK_TAG ($GAMMAL_IMAGE_ID) -> $MAL_IMAGE och startar om floden …"

  if ! docker tag "$ROLLBACK_TAG" "$MAL_IMAGE"; then
    fel "KRITISKT: Kunde inte återtagga $ROLLBACK_TAG till $MAL_IMAGE!"
    return 1
  fi

  if ! docker compose -f "$COMPOSE_FIL" up -d --no-deps floden; then
    fel "KRITISKT: docker compose up -d --no-deps floden misslyckades vid återställning!"
    return 1
  fi

  if vanta_pa_halsa "$HALSA_TIMEOUT_S"; then
    fel "ÅTERSTÄLLNING KLAR: Föregående avbildning ($GAMMAL_IMAGE_ID) kör igen och klarade hälsokontrollen. Uppgraderingen till $NY_IMAGE_ID avbröts."
    return 0
  else
    fel "KRITISKT: Återställd avbildning ($GAMMAL_IMAGE_ID) klarade INTE hälsokontrollen inom ${HALSA_TIMEOUT_S}s! Manuell åtgärd krävs."
    return 1
  fi
}

# a) Online-säker SQLite-backup före uppgradering (avbryt helt vid fel).
logga "Skapar online-säker SQLite-backup före uppgradering ($GAMMAL_IMAGE_ID -> $NY_IMAGE_ID) …"
if ! docker compose -f "$COMPOSE_FIL" exec -T floden "${BACKUP_CMD[@]}"; then
  fel "SQLite-backup före uppgradering misslyckades; avbryter driftsättning utan att starta om floden."
  exit 1
fi

# b) Spara föregående körande avbildning som lokalt återställningsankare.
logga "Taggar föregående avbildning ($GAMMAL_IMAGE_ID) som $ROLLBACK_TAG …"
if ! docker tag "$GAMMAL_IMAGE_ID" "$ROLLBACK_TAG"; then
  fel "Kunde inte skapa återställningstagg $ROLLBACK_TAG; avbryter driftsättning före omstart."
  exit 1
fi

# c) Starta uppdaterad floden-container utan att röra Caddy.
logga "Startar uppdaterad floden-container ($MAL_IMAGE -> $NY_IMAGE_ID) …"
if ! docker compose -f "$COMPOSE_FIL" up -d --no-deps floden; then
  rulla_tillbaka "docker compose up -d --no-deps floden misslyckades" || true
  exit 1
fi

# d) Verifiera /api/halsa inifrån containern; e) rulla tillbaka automatiskt vid fel.
logga "Väntar på hälsokontroll ($HALSA_URL, timeout ${HALSA_TIMEOUT_S}s) …"
if ! vanta_pa_halsa "$HALSA_TIMEOUT_S"; then
  rulla_tillbaka "Hälsokontroll ($HALSA_URL) misslyckades inom ${HALSA_TIMEOUT_S}s" || true
  exit 1
fi

logga "Uppgradering slutförd: $GAMMAL_IMAGE_ID -> $NY_IMAGE_ID ($MAL_IMAGE)."
exit 0
