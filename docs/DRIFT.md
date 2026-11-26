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
| `Dockerfile` | Fyra steg: webbklienten (Node 24, `npm run build`), Rust-verktygen (`lasclip`, `tvad_karna`), LibreDWG `dwgread` 0.13.3 (statiskt länkad, GPL v3, `FLODEN_DWGREAD`), samt körning (Python 3.12, `pip install -e ".[konto,mcp,rapport,s3,server]"` inkl. `boto3` för S3 och `uvicorn` för ASGI). Kör som användare `floden` (uid 1000). |
| `deploy/start.sh` | Vägrar starta om inloggningen är av (utom med `FLODEN_TILLAT_UTAN_INLOGGNING=1`), bygger demovärlden första gången om `data/demo/varld/demo.json` saknas (`FLODEN_DEMO=1`), startar `floden webb` på port 8765 i containern i serverläget `FLODEN_SERVER` (`asgi` som förval: uvicorn med `src/webb/asgi.py`, 25 MB kroppstak även vid chunked överföring, semafor på tunga beräknings-/rapport-/projekt-POST och kötak `max_ko` som svarar 503 med `Retry-After`, låsfri `/api/halsa` samt ren avstängning via `lifespan.shutdown`; `trad` är standardbibliotekets trådserver som reserv med stöd för `GET`/`POST`/`PUT`/`DELETE`). |
| `docker-compose.prod.yml` | `floden` (ingen port utåt, minnestak 3 GB, miljövariabler för OAuth, nyckelhemlighet samt valfri S3-objektlagring och lokal LRU-cache: `FLODEN_S3_TYP`, `FLODEN_S3_ENDPOINT`, `FLODEN_S3_BUCKET`, `FLODEN_S3_REGION`, `FLODEN_S3_ACCESS_KEY`, `FLODEN_S3_SECRET_KEY`, `FLODEN_S3_PREFIX`, `FLODEN_LOKAL_CACHE_MAPP`, `FLODEN_LOKAL_CACHE_MAX_GB`) och `caddy` (`caddy:2.11.7`, HTTPS, HSTS, `noindex`, 25 MB-gräns). |
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
- Bjuda in en kollega med stora rättigheter (men inte superadmin): se "Organisation och kollega" nedan.
- Nyckelhemligheten `FLODEN_NYCKELHEMLIGHET` (nästa avsnitt) ska vara satt i `.env` innan användarna lägger in egna nycklar.

## Nyckelhemligheten (`FLODEN_NYCKELHEMLIGHET`)

Varje inloggad användare kan lägga in egna API-nycklar på sidan **Nycklar** (Lantmäteriets STAC/nedladdning samt
AI-leverantörerna Gemini, OpenAI och Anthropic/Claude med primärväljare för framtida AI-körningar på servern; ingen kod anropar AI-nycklarna än). Nycklarna lagras **krypterade**
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

**Content-Security-Policy och OpenStreetMap-bakgrunden.** `deploy/Caddyfile` tillåter `https://tile.openstreetmap.org` för `img-src` och `connect-src` (MapLibre hämtar plattorna med `fetch`), och `blob:` och `data:` för `connect-src` (kartbilder som webben själv hämtat från servern och arbetsbänkens tomma bild; de blockerades förut av `default-src 'self'`). Ingen annan extern adress är tillåten. Klientfilerna och Caddy ger `Referrer-Policy: strict-origin-when-cross-origin` (förut `same-origin` från appen): OpenStreetMaps plattservrar kräver en Referer, och bara sidans ursprung skickas, aldrig sökvägen. Ändringen gäller först när Caddyfilen hämtats på servern och Caddy laddats om (se ovan).

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

## Markunderlag för ekologi: NMD och VMI

Habitatnätverken (`docs/EKOLOGI.md`, K47) behöver NMD (Nationella marktäckedata, Naturvårdsverket, 10 m raster, öppna data, CC0,
SWEREF 99 TM) och, valfritt, Våtmarksinventeringen (VMI, inventerad 1981-2005, äldre). Containern som koden byggdes i når bara
GitHub och paketregister, så inläsarna är testade mot syntetiska raster och vektorer och **inte körda mot riktiga filer**. Exakta
filnamn och adresser är därför inte kontrollerade: hämta dem från Naturvårdsverkets sidor.

- NMD: https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/nationella-marktackedata/ladda-ner-nationella-marktackedata/
  (filer under https://geodata.naturvardsverket.se/nedladdning/marktacke/ , NMD2018 eller NMD2023, basskiktet som GeoTIFF).
- VMI: https://geodata.naturvardsverket.se/nedladdning/VMI/ (en mapp per län, GIS-skikt i `5_GIS_skikt`).

Kommandon på servern, ett per rad (kärnan är områdets avgränsning i EPSG:3011; bufferten väljs efter nätverkets tröskel, 2-5 km):

```bash
mkdir -p data/lokal/ekologi/nmd data/lokal/ekologi/prov1
curl -fL -o data/lokal/ekologi/nmd/nmd.zip "<NMD-URL från nedladdningssidan>"
unzip -o data/lokal/ekologi/nmd/nmd.zip -d data/lokal/ekologi/nmd
docker compose -f docker-compose.prod.yml exec floden python -m data_inlasning.nmd klipp --nmd data/lokal/ekologi/nmd/<NMD-fil>.tif --kaerna <E0> <N0> <E1> <N1> --natverk barrskog-nacka --ut data/lokal/ekologi/prov1
curl -fL -o data/lokal/ekologi/vmi.zip "<VMI-URL för länet>"
docker compose -f docker-compose.prod.yml exec floden python -m data_inlasning.vmi klipp --vmi data/lokal/ekologi/<VMI-fil>.gpkg --kaerna <E0> <N0> <E1> <N1> --buffert-m 3000 --ut data/lokal/ekologi/prov1
```

Kommandona skriver `nmd.tif` och `nmd.json` (källa, kontrollsumma, utbredning) respektive `kand_vatmark.tif` och
`kand_vatmark.json` (märkt som äldre). Lagren är projektlager: ange dem som indata i projektets ekologifråga, som i
`exempel/fragor/demo_ekologi.toml`:

```toml
indata = { nmd = "data/lokal/ekologi/prov1/nmd.tif" }
```

Ritade hus (K9) läggs ovanpå som NMD 51 av lösaren.

**Som lager i geografin (K46).** Hellre än projektlager mappas NMD och VMI en gång per geografi, med kontextbuffert (2-5 km ur
längsta tröskel) runt kärnan, och ingår i manifestet som `lager.nmd` och `lager.vmi` (innehållsadresserade, sha256-verifierade):

```bash
docker compose -f docker-compose.prod.yml exec floden floden geografi mappa <geografi-id> --konto <superadmin> --nmd data/lokal/ekologi/nmd/<NMD-fil>.tif --vmi data/lokal/ekologi/<VMI-fil>.gpkg --nmd-troskel-m 3000
```

Båda är valfria; `--nmd-troskel-m` (förval 3000, begränsas till 2-5 km) är den längsta spridningströskel som geografin ska
prövas mot. **Ekologilösaren läser lagren ur geografin** (indataroll `geografi`, tillagd 2026-11-23): ange manifestet i frågefilen,

**Inventerade träd (valfritt lager för ädellövsambandet).** Lägg trädfilen (GeoPackage, Shapefile eller GeoJSON; bara punkt, trädslag
och stamomkrets/diameter läses) under `data/lokal/ekologi/` och lägg till `--trad data/lokal/ekologi/<tradfil>.gpkg` i kommandot ovan
(valfritt `--trad-natverk`; förval `adellov-nacka`, vars `[valfritt_lager.falt]` anger fältnamnen). Lagret `lager.tradpunkter` är
stängt för MCP tills `--villkor-kontrollerade` anges. Lösaren använder det med `anvand_trad = true` (`docs/EKOLOGI.md`). Inte
provat mot en riktig trädinventering.

```toml
[[analyser]]
id = "konnektivitet"
losare = "ekologi.konnektivitet"
parametrar = { natverk = "barrskog-nacka", max_celler = 600000, max_flackar = 120 }
indata = { geografi = "data/lokal/geo/<geografi-id>/v1/manifest.json" }
```

Lösaren verifierar lagrens sha256, klipper ut projektets rutor plus kontextbuffert (nätverkets längsta tröskel, 2-5 km, aldrig
utanför lagret) och lägger K9-hus ovanpå som NMD 51. Är geografins lager mappat med kortare buffert än behovet varnar lösaren.
Kärna plus 3 km buffert blir många celler: höj `max_celler` och `max_flackar` efter området (felen säger vad som behövs).
Indatarollen `nmd` finns kvar som alternativ och kan inte kombineras med `geografi`. Inget av det här är prövat mot riktiga
NMD- eller VMI-filer. Stockholms nätverk 2022 körs bara lokalt mot stadens biotopdatabas (inte öppen) och ska aldrig laddas upp.

## Organisation och kollega (organisationsroller)

Roller i en organisation: `medlem` < `ledare` < `organisationsadmin` < `agare`. Organisationsadmin och ägare är utredare, ledare är läsare, i de av organisationens projekt som är **delade med organisationen** (inte i privata projekt, inte i andras). Nya projekt är privata. Superadmin ger fortfarande ingen projektdata. Detaljer: `docs/KONTO.md` avsnitt 5 och 11.

Joel (superadmin) skapar organisationen "Floden" med sig själv som ägare och bjuder in Samuel som organisationsadmin. Ett kommando per rad, läs svaret mellan varje. `<epost>` är Joels egen inloggningsadress på första raden och Samuels Google-adress på sista; `<konto-id>` och `<org-id>` skrivs av raderna före.

```bash
docker compose -f docker-compose.prod.yml exec floden python -m konto.admin konto-id <epost>
docker compose -f docker-compose.prod.yml exec floden python -m konto.admin kontostatus <konto-id>
docker compose -f docker-compose.prod.yml exec floden python -m konto.admin skapa-organisation "Floden" --agare <konto-id>
docker compose -f docker-compose.prod.yml exec floden python -m konto.admin bjud-in-organisation <org-id> <epost> organisationsadmin
```

- Rad 1 skriver Joels konto-id (inget konto: logga in en gång med Google). Rad 2 ska skriva `superadmin=ja ...`; annars `... ge-superadmin <konto-id>` först.
- Rad 3 skriver organisationens id. Rad 4 skriver inbjudans id. Ingen e-post skickas: säg till Samuel att logga in på https://floden.berring.se med just den adressen. Därefter kan Joel eller Samuel hantera fler personer på sidan **Administration** (Samuel kan bara bjuda in medlem och ledare; ägare och organisationsadmin utses av Joel).
- Samuel ser ett projekt först när det är delat: projektets utredare eller skaparen (Joel, om han skapade det) kryssar i "Synligt för organisationen Floden" på sidan **Administration**. Före dess kan han bara administrera projektets medlemmar.
- Projekt som ska höra till organisationen skapas med `floden projekt skapa ... --konto <konto-id> --organisation <org-id>` (se nedan). Redan skapade projekt i en annan organisation ändras inte.
- Databasen migreras av sig själv till schemaversion 5 vid nästa start (befintliga roller gäller som förut). Ta en kopia av `data/konto/konto.sqlite` före uppdateringen.

## Nytt projekt och rapport i webben (K48)

Inget nytt kommando och ingen ny inställning: webbens "Nytt projekt" och "Skapa rapport" använder samma mappar som kommandona. Servern måste ha en mappad geografi (`floden geografi mappa ...`, se nedan) för att listan i "Nytt projekt" ska ha något att visa; geografierna läses ur `data/lokal/geo/` (förval, `floden geografi mappa`; projekten ligger i `data/lokal/projekt/`, kontoregistret i `data/konto/konto.sqlite`). Ett projekt som skapas i webben får sin mapp i `data/lokal/projekt/<namn-som-slug>/` (samma som `floden projekt skapa`) och en rad i kontoregistret med skaparen som utredare. Den som får skapa är superadmin, organisationsadmin och ägare (`docs/KONTO.md`). En ritad polygon sparas i `projekt.json` (`avgransning_polygon_3011`, additivt); projekt utan fältet beter sig som förut. Ingen migrering behövs.

Rapportjobb sparas atomiskt som `<rapportmapp>/jobb/j-<12 hex>.json` bredvid rapporterna (en liten fil per jobb, utan personuppgifter) och körs ett i taget i serverprocessen med trådlåst statusläsning (`RapportJobbTjanst.stang()` stänger exekutorn rent vid avstängning). Startas servern om medan ett jobb pågår blir jobbet `fel` ("avbröts när servern stoppades"); starta det igen i webben. Steg 3 i webben stöder nu skyfall, dagsljus/sol, ekologi (EK-01–EK-15), luft (LU-01–LU-15 med extern CSV + explicit bakgrundshalt → `luft.mkn`) samt rapporter på enbart *läget idag* (utan 409). Ingen backup behövs för jobbfilerna, men de ligger med rapporterna i den vanliga lagringen.

Steg 2 i webben (Karta & analys: nuläge, rita förslag, spara, planera, köra, sektion och analyser) gäller även projekt i en geografi: `/api/kartverktyg/<projekt-id>/...` och `/api/analys/*` läser projektets
`fragor/skyfall.toml` (med IDF), `dagsljus.toml` (inkl. `dagsljus.svf_mark` och höstdagjämning), `ekologi.toml`, `luft.toml` och geografins lager (som projektet är låst till via manifestets sha256; ändras geografin svarar steg 2
409 tills projektet skapas på nytt). Ingen ny inställning. Ritade förslag och K9 ligger under `kartverktygsmapp` (`<mapp>/<projekt-id>/scenarier` och
`k9`), de ritade frågorna i projektets `fragor/`. MCP lämnar inte ut ett projekt med `oppen = false` i `oppenhet.toml` (Stockholm), se `docs/MCP.md`.

## Göteborg: geografin `goteborg-test` och ett provprojekt (etapp 3, S5)

Geografi först (`docs/ARKITEKTUR-DRIFT.md` 0b): området mappas en gång som en **geografi** (kärna Haga 500 x 500 m + buffert,
kontrakt K46), sedan skapas projekt i den. Kommunen är konfigurerad i `data/kommuner/goteborg.toml`, inget är kodat.
Allt körs i containern, ett kommando per rad (kör dem ett i taget och läs svaret mellan varje).

**Källor (öppna filpaket, ingen nyckel krävs för Göteborgs egna data)**

Göteborgs WFS är avstängd ("Service WFS is disabled"), så kommunfilen använder stadens öppna filpaket (CC0, EPSG:3007, RH 2000):

- Byggnader: baskartan, lagret TAK (takkonturer): `https://opengeodata.goteborg.se/downloads/baskarta/baskarta_dwg_v2.zip` (DWG, 3D, uppdateras dagligen).
- Höjd: höjdmodell 2022 (GeoTIFF, Float32, 0,5 m, plattor om 1 x 1 km i paket om 10 x 10 km, bara mark). Paketens adresser är **inte verifierade**: se steg 1.
- Paketen hämtas en gång till `data/lokal/geo/_filcache/<sha256>/` och klipps till geografins område. `geo/<id>/v1` sparar den klippta höjden (`radata/hojd/hojdmodell_klippt.tif`), takkonturerna (`radata/kommun/byggnader.gpkg`) och paketens sha256 (`radata/filpaket/kallor.json`).
- **Byggnadshöjd:** baskartans lager har inget höjdattribut. Höjden är takkonturens z (RH 2000, medel av hörnen) minus markhöjden ur höjdmodellen. Konturer utan z (2D) får standardhöjd och en varning; höjdmodellen är en markmodell och ger ingen takhöjd. Utan en kontur för ett hus finns inget hus.
- **Vattenytor (kanaler, sjöar) och NaN-diagnos:** höjdmodellen har bottnen i kanalerna (Rosenlundskanalen ned till -7 m), vilket skyfallet fyller som en grop. `[filpaket.baskarta.vatten]` läser baskartans strandlinjer över samtliga angivna baskartepaket (`hydrografi_l`, `typ` Strandlinje, z = vattenytans nivå). `vattenytor_for_rutor` sätter 4-sammanhängande kandidatceller (även skarvfritt över rutgränser och överlapp) till vattenytans gemensamma globala RH 2000-mediannivå med markslag vatten (8); sänkor på land som inte når en strandlinje rörs inte. Höjdmodellens luckor klassificeras vektoriserat så att inget fylls blint över vatten eller utanför plattgräns, och rutor över tröskeln 20 % NaN flaggas i manifestet. K1-markmodellen har plan vattenyta (2D-lösaren `vatten.2d` stöder valfritt utlopps-/nivårandvillkor via `vattenniva_rh2000_m`). Mappningen varnar "N m² vattenyta ur strandlinjer, nivå X-Y m RH 2000" och `forberakningar.vattenytor` i manifestet har siffrorna; strandlinjerna ligger i `radata/kommun/strandlinjer.gpkg` och används även när projektets skyfallsruta byggs. **Ändrade vattenregler kräver en ny geografiversion** (`--version v2`) och nya projekt i den; befintliga projekt behåller sin markmodell.
- DWG kräver LibreDWG:s `dwgread` (`FLODEN_DWGREAD`; byggs in i avbildningen, LibreDWG 0.13.3, GPL v3). Finns en GeoPackage- eller Shapefile-variant i paketet läses den i stället (inget dwgread behövs); en dwgread-JSON i ZIP-filen läses också.
- Lantmäteriets laser är bara **reserv** för höjden (`[hojd] reserv_lantmateriet`): om höjdmodellen inte går att hämta och kontot har en nyckel. Då varnar manifestet. Göteborgs förval för byggnader är oförändrat (baskartans tak); Lantmäteriets Byggnad Nedladdning, vektor (avgiftsfri) kan väljas med `--byggnader-kalla lantmateriet` och kräver då en Lantmäteri-nyckel även om höjden kommer ur kommunens fil.
- Använder du Lantmäteri-nyckeln: på sidan Nycklar är *Geotorgets användarnamn och lösenord (privatperson)* förvalt (`basic`); *API-portalens nyckel och hemlighet* är `oauth2`. Sidan visar tabellen över produkter att beställa på Geotorget (`data/lantmateriet/produkter.toml`) och att en beställning kan ta dagar innan den är aktiv (alla produkter och avgifter: `docs/GEOTORGET.md`). Svarar Lantmäteriet 401/403 nämner felet vilken produkt det gäller. Produkten för höjd är STAC Höjd, Laserdata Nedladdning, skog (gratis). Ortofoto Nedladdning är gratis och får användas, men är avstängd som förval för Göteborg (`produkter = ["laser"]`).

**Före första körningen**

- Kontot som blir utredare i projektet ska vara medlem i en organisation (steg 2 och 3 nedan om det saknas). Superadmin-kontot behöver ingen Lantmäteri-nyckel för Göteborg, men en nyckel (Sätt att logga in: Geotorgets användarnamn och lösenord) ger reserven.

```bash
docker compose -f docker-compose.prod.yml exec floden python -m konto.admin konto-id <superadmin-epost>
docker compose -f docker-compose.prod.yml exec floden python -m konto.admin skapa-organisation "Göteborgs Stad"
docker compose -f docker-compose.prod.yml exec floden python -m konto.admin gor-organisationsadmin <konto-id> <organisations-id>
```

(Första raden skriver konto-id. Andra raden skriver organisations-id; hoppa över de två sista om kontot redan är medlem.)

**1. Höjdmodellens paket** (adresserna är inte verifierade; kommunfilen försöker läsa `.zip`-länkar på `https://opengeodata.goteborg.se/files/hojdmodell/2022/hojdmodell_2022.html`, en gissning)

Hämta adresserna på `http://www.goteborg.se/psidata` (Höjdmodell 2022). Ange sedan paketen som täcker Haga (E 147400-147900, N 6397500-6398000 i EPSG:3007) i nästa steg med `--hojdmodell-url`, eller lägg dem i `[filpaket.hojdmodell] urlar` i `data/kommuner/goteborg.toml`. Har du hämtat en ZIP för hand: `--hojdmodell-fil <sökväg>`. WFS-lagerlistan (`floden projekt kommun-lager goteborg`) fungerar bara om WFS-tjänsten är på; den är valfri.

**2. Mappa geografin** (bara superadmin; hämtar höjdmodell och baskarta, tar minuter vid första körningen)

```bash
docker compose -f docker-compose.prod.yml exec floden floden geografi mappa goteborg-test --konto <superadmin-konto-id> --hojdmodell-url <adress-till-ett-paket>
```

Utskriften visar vilka paket som hämtades (storlek, sha256), buffert (dagsljus 686 m vid 60 m och 5 grader, och tillrinningens krav), terrängens höjdspann och lägsta
punkt i kärnan, och skalfelet. Kan köras om: en version som finns lämnas orörd (`--version v2` för en ny mappning), och paketen hämtas inte om.
Resultatet ligger i `data/lokal/geo/goteborg-test/v1/manifest.json` och `data/lokal/geo/_lager/<sha256>/`.

```bash
docker compose -f docker-compose.prod.yml exec floden floden geografi visa goteborg-test@v1
```

Visar manifestet och kontrollerar sha256 för varje fil.

**3. Skapa ett projekt i geografin** (avgränsning i meter, EPSG:3011: `xmin,ymin,xmax,ymax`; här 300 x 300 m i mitten av Haga)

```bash
docker compose -f docker-compose.prod.yml exec floden floden projekt skapa --geografi goteborg-test@v1 --avgransning=-209984,6413653,-209684,6413953 --namn "Haga prov" --konto <konto-id>
```

Projektet får id `haga-prov` i `data/lokal/projekt/haga-prov/`, låst till `goteborg-test@v1`, och kontot blir utredare. En
avgränsning som sticker utanför kärnan ger en varning, inte ett fel; utanför geografins yttre gräns (ingen data) är ett fel.

**4. Lägg till ett hus som scenario och se diffen mot nuläget** (centrum i EPSG:3011, höjd över mark i meter)

```bash
docker compose -f docker-compose.prod.yml exec floden floden projekt scenario-hus haga-prov --namn "Nytt hus" --centrum=-209834,6413830 --bredd 20 --djup 12 --hojd 24 --status forslag
```

`--status planerat` märker scenariot som planerat läge (förberett i datamodellen, räknas inte som bas än).

**5. Kör skyfall och dagsljus för nuläget och scenariot**

```bash
docker compose -f docker-compose.prod.yml exec floden floden projekt kor haga-prov
```

Körningarna syns i webben under projektet (frågorna `skyfall` och `dagsljus`), för kontot som utredare.

**Felmeddelanden** skrivs som en rad utan stackspår. Slutkod 3: Lantmäteri-nyckel saknas eller nekas (lägg in den på sidan
Nycklar). Slutkod 4: inte superadmin, konto eller organisation saknas, fel i kommunfilen eller geografin saknas. Slutkod 2:
en källa svarar inte (Lantmäteriet eller Göteborgs WFS), eller svarar med något annat än väntat (fel koordinater, tomt svar,
avkortad lista). `--felsok` skriver stackspår vid oväntade fel.

**Öppenhet.** Lantmäteriets och Göteborgs villkor för AI-visning är inte kontrollerade av Floden: rådata är alltid stängd för
MCP, och det härledda (markmodell, resultat) är stängt tills geografin mappats om med `--villkor-kontrollerade` (en ny version,
`--version v2`). Licenserna står i `oppenhet.toml` bredvid datat. Inga nycklar eller personuppgifter skrivs i utskrift eller
loggar; kommunens adressfält och liknande sparas inte.

**Oprövat.** Hela kedjan är provad bara mot en falsk Lantmäteriserver och syntetiska svar (utvecklingsmiljön når bara GitHub).
Formatet på Göteborgs WFS (lagernamn, attribut, GeoJSON och axelordning) är antaget, och Lantmäteriets verkliga filer har inte
körts genom bygget. Första körningen på servern är därför också provet.

## Stockholm: geografin `stockholm-test` (byggnader ur Stockholms 3D-baskarta, inte öppna data)

Joels beslut 2026-11-24 (`docs/BESLUT.md`): delar av Stockholms 3D-baskarta (DWG) får användas i projekt och ligga på Flodens server, men
baskartan är **inte öppna data**. Därför ligger filen **bara** i serverns lokala datamapp, aldrig i git, loggar, `STATUS.md` eller PR-texter,
och allt som härleds direkt ur den är **stängt** för MCP och extern AI. Kommunfilen är `data/kommuner/stockholm.toml` (EPSG:3011, höjd ur
Lantmäteriets laser, byggnader `kalla = "lokal_fil"`). Geografin `stockholm-test` är ett **provområde** (500 x 500 m kring Mariatorget,
Södermalm), inget planområde.

**Lägg filen på servern** (på värden; `./data/lokal` i `docker-compose.prod.yml` är `/app/data/lokal` i containern). Använd `scp` eller liknande
från din dator, och checka aldrig in den:

```bash
sudo mkdir -p /srv/floden/data/lokal/stockholm && sudo chown 1000:1000 /srv/floden/data/lokal/stockholm
scp <baskartefilen> <server>:/srv/floden/data/lokal/stockholm/baskarta.zip
```

Filen kan vara en ZIP (med DWG eller dwgread-JSON), en DWG eller en dwgread-JSON, och flera filer (kartblad) kan anges. DWG kräver `dwgread`
(finns i avbildningen, `FLODEN_DWGREAD`). Namnet ovan är ett exempel: ge filen vilket namn du vill, det skrivs aldrig i utskrift eller manifest.

**Före första körningen:** superadmin-kontot ska ha en Lantmäteri-nyckel på sidan Nycklar (STAC Höjd, Laserdata Nedladdning, skog, gratis; Sätt att logga in =
Geotorgets användarnamn och lösenord, se Göteborg ovan). Stockholm har ingen reserv: utan nyckel går mappningen inte.

**Mappa geografin** (bara superadmin; tar några minuter):

```bash
docker compose -f docker-compose.prod.yml exec floden floden geografi mappa stockholm-test --konto <superadmin-konto-id> --baskarta-fil /app/data/lokal/stockholm/baskarta.zip
```

`--baskarta-fil` kan anges flera gånger. Utskriften nämner bara antal byggnader och att höjden är okontrollerad, aldrig filnamn eller lagernamn.
Kontrollera sedan, och visa inte utskriften utanför servern:

```bash
docker compose -f docker-compose.prod.yml exec floden floden geografi visa stockholm-test@v1
docker compose -f docker-compose.prod.yml exec floden floden projekt skapa --geografi stockholm-test@v1 --avgransning=153550,6577900,153750,6578100 --namn "Stockholm prov" --konto <konto-id>
```

**Stängt, alltid.** Geografins version, lagren `radata` och `k1_rutnat` och projekten i den får `oppenhet.toml` med `oppen = false` och licens `intern`;
`--villkor-kontrollerade` öppnar dem **inte** (kommandot säger det). Manifestet har `kallor.byggnader.stangd = true` och `oppen_for_mcp = false`.
MCP-spärren (`floden_mcp.oppenhet`, `docs/MCP.md`) nekar därmed sökvägarna (testat) och körningar vars indata ligger under dem (regeln i K38). Resultat och rapporter ur ett Stockholmsprojekt
är alltså inte `oppet` och går inte att hämta via MCP eller skicka till en AI-tjänst.

**Vad som är okontrollerat** (står också som varning i manifestet):

- Byggnadshöjd (`[byggnader.lokal] hojd_fran`): förval `takkontur_z` = z (taknivå) på byggnadens linje, medel av hörnen, minus markhöjden ur lasern. Det är
  **okontrollerat** att z är RH 2000 (annars `z_tillagg_m`), att z är takets och inte fotens höjd, och att byggnadens linje har z för alla hus. Hus utan giltig z
  skattas ur Lantmäteriets laser (oklassade punkter) eller får standardhöjd, med en varning som anger antalet. `hojd_fran = "laser"` hoppar över baskartans z.
- Lagertolkningen är profilen `data/lagerprofiler/stockholm_baskarta.toml`, som är "att bekräfta mot full lagerlista": byggnadens yta ger huset, byggnadens linje ger höjden.
  Hittas inga byggnader säger felet det utan att nämna lagernamn; bryt då mot profilen eller ange `lager_monster` i `[byggnader.lokal]` (serverns egen kopia av kommunfilen).
- Filens koordinatsystem antas vara EPSG:3011 (`[byggnader.lokal] epsg`). Terrängen i provområdet är inte kontrollerad; manifestet har höjdspann och lägsta punkt.
- Filen läses helt in i minnet som objekt innan den klipps: en mycket stor DWG kan ta minuter och mycket minne. Ange i så fall bara det kartblad som täcker området.

## Planera nya geografier (S7, `floden geografi planera`)

Inför större geografier (t.ex. S7 Hägersten-Älvsjö med Skärholmen) delar `floden geografi planera` (`src/geografi/planera.py`, `src/kommuner/cli.py`) upp en avgränsning (`.geojson`, `.json`, `.gpkg` eller bbox `xmin,ymin,xmax,ymax`) i standardiserade 500 × 500 m-rutor (+ 25 m överlapp) i SWEREF 99 18 00 (EPSG:3011) och redovisar kärnrutor, buffertrutor samt källbelagda resursuppskattningar.

```bash
# Exempel: planera en stadsdel i Stockholm (läser enbart avgränsningen och data/kommuner/stockholm.toml)
docker compose -f docker-compose.prod.yml exec floden \
  floden geografi planera stockholm --avgransning <avgransning.geojson> --upplosning-m 0.5 --arbetare 1

# Maskinläsbar JSON (inkl. fältet "uppskattning": true och källhänvisningar per antagande)
docker compose -f docker-compose.prod.yml exec floden \
  floden geografi planera goteborg --avgransning=-210200,6413500,-209400,6414300 --avgransning-epsg 3011 --json
```

- **Uppskattningar, inte servermätning:** alla tid-, RAM- och diskvärden är uttryckligt märkta `uppskattning = True` och skalas från repots dokumenterade baslinjer (`STATUS.md` #114/#120/#123, `docs/benchmark/2026-11-23-container.json` och `docs/ARKITEKTUR-DRIFT.md:263-288`). De ersätts av den skarpa mätningen i **S6**.
- **Valfria flaggor:** `--avgransning-epsg` (t.ex. `3011`, `3006`, `4326`), `--upplosning-m` (standard ur kommunfilen, annars `0.5`), `--buffert-m` (standard: dagsljusets påverkansradie och kommunens kontext), `--rutstorlek-m` (`500.0`), `--overlapp-m` (`25.0`), `--arbetare` (`1`), `--kommunfil`, `--json`.
- **Dataskydd (K38):** verktyget läser **aldrig** stängda baskartor eller lokala geodata utan enbart avgränsningen och kommunfilen. Kommunens dataklass redovisas alltid med tydlig K38-notis: för `stockholm` (`stangd`) är moln-batch, öppen S3 och extern AI inte tillåtna; för `goteborg` (`oppen`) tillåts öppen S3-lagring och batch.

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

## Lagring, cachestädning och backup (S4)

Koden och systemd-enheterna för S4 finns på `main`, medan skarp driftsättning av Hetzner Object Storage-bucket och timers på `floden.berring.se` görs av Joel. Bara `data/konto/konto.sqlite` går inte att räkna om (beslut 2026-11-22).

### Hetzner Object Storage (S3) och lokal LRU-cache

Objektlagring konfigureras i `config.local.toml` (`[lagring]` och `[lagring.lokal_cache]`, se `deploy/config.local.toml.example` och `docs/ARKITEKTUR-DRIFT.md` avsnitt 4b) samt med miljövariabler i `.env` som `docker-compose.prod.yml` skickar in i containern:

- `FLODEN_S3_TYP` (`s3` eller `lokal`), `FLODEN_S3_ENDPOINT`, `FLODEN_S3_BUCKET`, `FLODEN_S3_REGION`, `FLODEN_S3_ACCESS_KEY`, `FLODEN_S3_SECRET_KEY`, `FLODEN_S3_PREFIX`
- `FLODEN_LOKAL_CACHE_MAPP` (t.ex. `data/cache/s3_lru`), `FLODEN_LOKAL_CACHE_MAX_GB` (t.ex. `10.0`)

Använd webbserverns läs-/projektnyckel i `.env` (aldrig batch-skrivnyckeln) och separera alltid öppen grunddata (`floden-oppen-eu`) från icke-öppen stadsdata. Tre-lagers rensning:

1. **Lager 1:** tillfälliga rådatakataloger i batchkörning rensas direkt i `finally` (`floden forberakna` med `--lagring s3`, `--max-arbetare`).
2. **Lager 2:** S3-livscykelregler (`tmp/radata/` 2 d, `tmp/skiss/` 14 d, ofullständiga multiparts 1 d) installeras med `docker compose -f docker-compose.prod.yml exec floden floden lagring installera-livscykel`.
3. **Lager 3:** lokal LRU-cache städas dagligen kl. 03:00 av `deploy/floden-cache-stada.service` (`WorkingDirectory=/srv/floden`, kör `docker compose -f docker-compose.prod.yml exec -T floden floden lagring stada`) och `deploy/floden-cache-stada.timer` (provkör med `--torrkor`).

### Online SQLite-backup och driftskontroll av kontodatabasen (`floden konto`)

Kontodatabasen (`data/konto/konto.sqlite`, WAL-läge) säkerhetskopieras och verifieras med kommandogruppen `floden konto` (`src/konto/backup.py`, `src/konto/cli.py`):

```bash
# Skapa online-säker kopia, kör integrity_check + foreign_key_check, skriv SHA-256-manifest och rotera
docker compose -f docker-compose.prod.yml exec floden \
  floden konto backup --ut data/lokal/backup/konto --behall-antal 48 --behall-dagar 14 [--s3 --s3-prefix backup/konto] [--json]

# Verifiera en enskild backupfil mot SHA-256-manifest och SQLite-integritet
docker compose -f docker-compose.prod.yml exec floden \
  floden konto verifiera data/lokal/backup/konto/konto-<tidpunkt>.sqlite [--json]

# Kontrollera driftsstatus (konto-DB WAL/integrity, FLODEN_NYCKELHEMLIGHET, S3-konfig; slutkod 0 = OK, 1 = fel)
docker compose -f docker-compose.prod.yml exec floden floden konto halsa [--json]
```

- **Online-säker och deterministisk:** använder `sqlite3.Connection.backup()` (sparad med `DELETE`-journal), verifierar `PRAGMA integrity_check` och `PRAGMA foreign_key_check`, skriver atomiskt ett SHA-256-manifest (`floden-konto-backup/v1`), sätter filrättigheter `0o600`/`0o700` och roterar äldre kopior parvis (`.sqlite` + `.manifest.json`). Med `--s3` laddas backup + manifest upp och verifieras mot konfigurerad objektlagring.
- **0 PII och 0 hemligheter:** manifest och hälsosvar innehåller aldrig e-postadresser, namn, tokens, sessionshashar eller nyckelhemligheter — enbart anonyma tabellradantal, filstorlekar, SHA-256 och statusflaggor. `FLODEN_NYCKELHEMLIGHET` ingår aldrig i backupen utan sparas separat i lösenordshanterare.
- **Timvis systemd-timer:** `deploy/floden-konto-backup.service` (`WorkingDirectory=/srv/floden`, `User=floden`, `UMask=0077`, `EnvironmentFile=-/srv/floden/.env`) och `deploy/floden-konto-backup.timer` (`OnCalendar=hourly`, `RandomizedDelaySec=5m`, `Persistent=true`). Lägg till `--s3` på `ExecStart`-raden när Hetzner Object Storage är aktiverat.

Aktivera båda timers på servern (efter att filerna hämtats till `/srv/floden/deploy/`):

```bash
sudo cp /srv/floden/deploy/floden-cache-stada.{service,timer} /srv/floden/deploy/floden-konto-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now floden-cache-stada.timer floden-konto-backup.timer
systemctl list-timers 'floden-*'
```

## Kostnadskontroll

Basservern kostar lika mycket oavsett antal användare. Allt som kostar per timme (arbetare, GPU) går genom kostnadsbok
och månadstak (S5k) och startas till en början bara av superadmin (demoläget, S6d).
