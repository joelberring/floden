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

`FORWARDED_ALLOW_IPS` (satt till `*` i `docker-compose.prod.yml`, kan åsidosättas i `.env`): Uvicorn litar annars bara på `X-Forwarded-For` från 127.0.0.1, men Caddy kommer från Docker-nätet, så utan den ser Floden Caddys adress i stället för klientens. `*` är säkert eftersom Floden inte har någon port utåt; ta inte med den om containern någonsin exponeras direkt.

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
