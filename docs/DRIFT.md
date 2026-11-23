# Drift på server

Upplägget (Joel 2026-11-22, `BESLUT.md`): en liten basserver hos Hetzner (`floden.berring.se`), beräkningsarbetare som
startas vid behov, en egen bucket i O-Tids Object Storage, fasta månadsbudgetar och **demo först**. Hela planen med
motiv, kostnader och GPU står i projektdokumentet *Floden på server – upplägg* (`design/server-och-drift.md` i
Claude-projektet) och sammanfattas i `PLAN.md`, avsnitt "Drift – server (S)".

Mönstret är lyft från O-Tid (github.com/joelberring/otid, MIT): `Dockerfile`, `docker-compose.prod.yml`,
`deploy/Caddyfile`, avbildningar i ghcr.io efter grön CI (O-Tid ADR-0195).

## Avbildningen (S1)

| Del | Vad |
| --- | --- |
| `Dockerfile` | Tre steg: webbklienten (Node 24, `npm run build`), Rust-verktygen (`lasclip`, `tvad_karna`), körning (Python 3.12, `pip install -e ".[konto,mcp,rapport]"`). Kör som användare `floden` (uid 1000). |
| `deploy/start.sh` | Vägrar starta om inloggningen är av (utom med `FLODEN_TILLAT_UTAN_INLOGGNING=1`), bygger demovärlden första gången (`FLODEN_DEMO=1`), startar `floden webb` på port 8765 i containern, i serverläget `FLODEN_SERVER` (`asgi` som förval: uvicorn med src/webb/asgi.py, 25 MB kroppstak även vid chunked överföring och högst fyra samtidiga tunga beräkningar, därefter 503 med `Retry-After`; `trad` är standardbibliotekets trådserver som reserv). |
| `docker-compose.prod.yml` | `floden` (ingen port utåt, minnestak 3 GB) och `caddy` (HTTPS, HSTS, `noindex`, 25 MB-gräns). |
| `.github/workflows/avbildning.yml` | Bygger och röktestar avbildningen på grenar; efter grön CI på `main` läggs den upp som `ghcr.io/joelberring/floden:<sha>`, `:<kort sha>` och `:main`. |

Avbildningen är bara `linux/amd64`, så basservern ska vara en x86-typ (CX23/CX33), inte CAX (ARM).

## Sätta upp servern

```bash
# På servern (Docker installerat), i en mapp, t.ex. /srv/floden
curl -fsSLO https://raw.githubusercontent.com/joelberring/floden/main/docker-compose.prod.yml
mkdir -p deploy && curl -fsSL -o deploy/Caddyfile https://raw.githubusercontent.com/joelberring/floden/main/deploy/Caddyfile
curl -fsSL -o .env https://raw.githubusercontent.com/joelberring/floden/main/deploy/prod.env.example
curl -fsSL -o config.local.toml https://raw.githubusercontent.com/joelberring/floden/main/deploy/config.local.toml.example
# Fyll i .env (domän, Googles klient-id och hemlighet, FLODEN_NYCKELHEMLIGHET) och kontrollera config.local.toml.
mkdir -p data/konto data/cache data/demo data/lokal && sudo chown -R 1000:1000 data
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f floden
```

- **Paketet måste vara publikt** på ghcr.io (GitHub → Packages → floden → Package settings → Change visibility), annars
  kan servern inte hämta avbildningen utan inloggning.
- `config.local.toml` måste finnas **som fil** innan `up` (annars skapar Docker en katalog med det namnet).
- DNS: en A-post (och AAAA) för `floden.berring.se` till serverns adress innan Caddy startar, så att certifikatet går att hämta.
- Google: redirect-URI `https://floden.berring.se/konto/callback/google` (`docs/KONTO.md` avsnitt 2).
- Första administratören: `docker compose -f docker-compose.prod.yml exec floden python -m konto.admin …` (`docs/KONTO.md` avsnitt 7). Därefter sköts inbjudningar, roller och spärr av konton i webben (sidan **Administration**, bara för superadmin och organisationsadmin; `docs/KONTO.md` avsnitt 10).
- Nyckelhemligheten `FLODEN_NYCKELHEMLIGHET` (nästa avsnitt) ska vara satt i `.env` innan användarna lägger in egna nycklar.

## Nyckelhemligheten (`FLODEN_NYCKELHEMLIGHET`)

Varje inloggad användare kan lägga in egna API-nycklar på sidan **Nycklar** (Lantmäteriets STAC/nedladdning, och
Anthropic/Claude för framtida AI-körningar på servern; ingen kod anropar den än). Nycklarna lagras **krypterade**
(Fernet ur `cryptography`) i kontodatabasen, en rad per konto och typ. Krypteringsnyckeln, *nyckelhemligheten*, läses
ur miljövariabeln `FLODEN_NYCKELHEMLIGHET` och ligger aldrig i databasen, i git eller i `config.local.toml`.

Skapa hemligheten en gång, på valfri dator med Python och `cryptography` (finns i Floden-avbildningen):

```bash
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
```

(Går också i containern: `docker compose -f docker-compose.prod.yml exec floden python -c "…"`.) Lägg raden i `.env` bredvid `docker-compose.prod.yml` (`docker-compose.prod.yml` skickar in den i containern) och starta om:

```bash
echo 'FLODEN_NYCKELHEMLIGHET=<det som skrevs ut>' >> .env
docker compose -f docker-compose.prod.yml up -d
```

- **Saknas hemligheten** (eller är den ogiltig) är funktionen avstängd: sidan Nycklar säger att den inte är påslagen
  på servern, ingen nyckel kan sparas och ingenting sparas någonsin okrypterat. Servern startar ändå. En användare kan
  ta bort en redan sparad nyckel även då.
- **Spara hemligheten på ett andra ställe** (lösenordshanterare). Förloras den går de sparade nycklarna inte att läsa
  och användarna får lägga in dem igen; resten av Floden påverkas inte. Hemligheten ingår inte i någon backup av `data/`.
- **Byta hemlighet:** sätt den nya först och den gamla efter ett komma, `FLODEN_NYCKELHEMLIGHET=<ny>,<gammal>`. Den
  första krypterar, alla kan läsa. En nyckel krypteras om med den nya första gången användaren sparar den på nytt;
  ta bort den gamla när alla har gjort det (eller när ni accepterar att de som inte gjort det lägger in nyckeln igen).
- **Vad som skyddas:** en nyckel visas aldrig igen efter att den sparats (bara "sparad <datum>, slutar på …abcd"),
  och hamnar aldrig i loggar, felmeddelanden, körlogg, `run.json` eller MCP. Med inloggning på använder Lantmäteri-
  hämtningen (`floden data lantmateriet --konto <konto-id>`) användarens egen nyckel och aldrig servernyckeln i miljön.

## MCP på servern (`/mcp`)

`floden webb --server asgi` (förvalet i avbildningen) monterar MCP:s Streamable HTTP på `https://floden.berring.se/mcp`, i samma
process som webben (`src/floden_mcp/montera.py`): samma konton, samma körningar, ett enda `data/`. Inloggning krävs på varje anrop,
antingen en personlig MCP-token (`Authorization: Bearer fl_mcp_...`) eller en session; kopplingen från Claude-appen (OAuth) beskrivs i
`docs/KONTO.md` och `docs/KOM-IGANG.md`. `/mcp` är **av** när inloggningen är av och värden inte är loopback (servern skriver då varför
vid start); `--ingen-mcp` stänger den uttryckligen. Skyddet mot DNS rebinding släpper bara in `Host: floden.berring.se`
(`[konto] offentlig_url` i `config.local.toml` måste därför vara `https://floden.berring.se`).

Caddy (`deploy/Caddyfile`) skickar `/mcp` utan komprimering och utan buffring (`flush_interval -1`), så att SSE-strömmarna når klienten
direkt; resten av sajten är som förut. Caddyfilen är kontrollerad med `caddy validate` (Caddy 2.10).

**Det här gör Joel på servern** (`/srv/floden` är inte en git-klon, så filerna hämtas med `curl`; först när ändringen är mergad till `main`
och avbildningen byggts efter grön CI):

```bash
cd /srv/floden
curl -fsSLO https://raw.githubusercontent.com/joelberring/floden/main/docker-compose.prod.yml
curl -fsSL -o deploy/Caddyfile https://raw.githubusercontent.com/joelberring/floden/main/deploy/Caddyfile
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile   # Caddyfilen är en bind-montering: ladda om den
```

Inget nytt i `.env` eller `config.local.toml` behövs. Kontrollera att `config.local.toml` har `offentlig_url = "https://floden.berring.se"`.
OAuth för Claude-appen kräver ingen ytterligare inställning (endpoints under `/oauth/` och `/.well-known/` når Floden genom Caddys
standardflöde); Anthropics servrar måste kunna nå sajten (inga IP-spärrar framför Caddy). Prova sedan: logga in på https://floden.berring.se, skapa en token under *Nycklar > MCP-token* och kör kommandot som visas
(`docs/KOM-IGANG.md`). Kontodatabasen migreras av sig själv vid start (schemaversion 4: tabellen `mcp_token` och OAuth-tabellerna, se
`docs/KONTO.md` avsnitt 12 och 13); ta en kopia av `data/konto/konto.sqlite` före uppdateringen om du vill kunna backa.

Körloggen för MCP-anrop ligger i `data/lokal/mcp/korlogg.jsonl` (volymen `./data/lokal`) och har konto-id och token-id, aldrig token.
Spärren för data som inte är öppna (`docs/MCP.md`) är alltid på; data under `data/lokal` räknas som stängda för MCP.

## Uppdatera

```bash
docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d
```

`FLODEN_TAG` i `.env` låser en version (en commit-sha).

## Prova lokalt

```bash
docker compose -f docker-compose.prod.yml build
FLODEN_DOMAIN=localhost FLODEN_TILLAT_UTAN_INLOGGNING=1 docker compose -f docker-compose.prod.yml up
# https://localhost (Caddys lokala certifikat)
```

## Lagring och backup

Tills förrådet (S4) finns ligger allt på serverns disk under `data/`. Bara `data/konto/konto.sqlite` går inte att räkna
om; S4 lägger en timkopia av den i bucketen. Ingen annan backup (beslut 2026-11-22).

## Kostnadskontroll

Basservern kostar lika mycket oavsett antal användare. Allt som kostar per timme (arbetare, GPU) går genom kostnadsbok
och månadstak (S5) och startas till en början bara av superadmin (demoläget, S6).
