# Floden som MCP-server

`floden mcp` gör att en AI-klient (Claude Desktop, Claude Code, Codex, en lokal modell) kan styra
Floden: utforska demokvarteret, rita ett hus, se vad som behöver räknas, köra, läsa skillnaden och
hämta en karta. Det är **samma kärna som webben använder** (`webb.api.FlodenApiApp`, K20-frågor,
K9-scenarier, `karna.*`); MCP-lagret i `src/floden_mcp/` räknar ingenting själv.

## Principen: AI äger aldrig en siffra

- **Floden räknar.** Varje tal i ett svar kommer ur en körning (`run.json`, `sammanstallning.json`
  eller ett GeoTIFF-lager). Ingen modell skriver eller avrundar ett värde som sedan presenteras som
  Flodens.
- **AI frågar, väljer körningar och skriver.** Modellen väljer fråga, scenario och analys, och skriver
  text. Texten ska peka på belägg, inte ersätta dem.
- **Varje tal har belägg:** `korning_id`, `fil`, `mottagare` eller `cell` (E/N och cellstorlek),
  `matt` (t.ex. `max_djup:max_okning`) och `enhet`. För rasterceller ingår filens sha256.
- **Tak är inte mark.** Skillnader och statistik gäller mark: byggnadernas tak undantas med samma
  takmask som webben och kartan (`FragaTjanst.marksiffror`, en enda funktion; dagsljus maskar
  fotavtryck i både bas och scenario, vatten bara byggnad i markslaget). Ett nytt hus ger därför
  aldrig "mer sol" genom sitt eget tak. `jamfor` och `resultat_sammanfattning` ger markens tal
  (`yta_mark_enbart`, `tak_maskat`) och kärnans omaskade tal bredvid (`karnans_omaskade`).
  `resultat_varde` i en takcell ger `varde: null` och värdet i `tak_varde`.
- **Allt är märkt skiss.** Metodstatus (`metoder_lista`) är `skiss` för alla metoder tills en
  människa sätter `rapport` i `data/mcp/metodstatus.toml`. Kartan har texten SKISS i foten.
  Server och klient kan inte höja en metod till `rapport`.

## Anslutning

Förutsättning: Floden installerad (`pip install -e ".[dev]"`, eller `pip install -e ".[mcp]"`) och
demon byggd (`floden demo`; `floden demo --liten` är snabbare). Servern behöver Flodens rotmapp
(mappen med `config.toml`); ange den med `--rot` eller `FLODEN_ROT`.

### Claude Desktop (Mac)

Öppna `~/Library/Application Support/Claude/claude_desktop_config.json` (skapa den om den saknas)
och lägg till servern. Använd **absoluta sökvägar**; Claude Desktop startar inte servern i din
terminalmiljö, så `floden` måste anges med full sökväg till virtuella miljön:

```json
{
  "mcpServers": {
    "floden": {
      "command": "/Users/<namn>/floden/.venv/bin/floden",
      "args": ["mcp", "--rot", "/Users/<namn>/floden"],
      "env": { "FLODEN_MCP_MODELL": "claude-desktop" }
    }
  }
}
```

Starta om Claude Desktop helt (Cmd+Q, inte bara stäng fönstret). Floden ska då synas bland
verktygen. `FLODEN_MCP_MODELL` och `FLODEN_MCP_ROLL` är valfria och hamnar i körloggen som
deklarerad modell och roll. Hittas inte `floden`, använd
`"command": "/Users/<namn>/floden/.venv/bin/python"` och
`"args": ["-m", "floden.cli", "mcp", "--rot", "/Users/<namn>/floden"]` med
`"env": {"PYTHONPATH": "/Users/<namn>/floden/src"}`.

Obs: instruktionen för Claude Desktop är **inte provad mot själva appen** (den togs fram och
kontrollerades bara med SDK-klienten över stdio). Fungerar den inte: se Claude Desktops
MCP-logg (`~/Library/Logs/Claude/mcp-server-floden.log`).

### Claude Code

```bash
claude mcp add floden -- floden mcp --rot /sökväg/till/floden
```

Kör kommandot i en terminal där `floden` finns (virtuella miljön aktiv), eller ange full
sökväg till `floden`. `claude mcp list` visar om den startar.

### Codex (CLI)

I `~/.codex/config.toml`:

```toml
[mcp_servers.floden]
command = "/sökväg/till/floden/.venv/bin/floden"
args = ["mcp", "--rot", "/sökväg/till/floden"]
```

### Lokal modell och andra klienter

Allt som talar MCP över stdio fungerar: starta `floden mcp --rot …` som delprocess. Över HTTP:

```bash
floden mcp --http --port 8766        # http://127.0.0.1:8766/mcp
```

`--http` lyssnar bara på loopback så länge inloggningen är av; `--host 0.0.0.0` nekas då. Med
`inloggning = "pa"` (`docs/KONTO.md`) kräver servern samma session som webben (kaka och CSRF-huvud).
Maskinåtkomst med token är inte byggd (R4).

Skriptet `tools/mcp_provflode.py` är ett körbart exempel på hela flödet med SDK-klienten.

### Floden på servern (`https://floden.berring.se/mcp`)

Servern (`floden webb --server asgi`, `docs/DRIFT.md`) monterar MCP:s Streamable HTTP på `/mcp` i samma process som webben. **`/mcp`
är av när inloggningen är av och värden inte är loopback.** Med inloggning på krävs på varje anrop antingen en personlig
**MCP-token** (`Authorization: Bearer fl_mcp_...`, `docs/KONTO.md` avsnitt 12) eller en session; Claude-appen använder OAuth
(`docs/KONTO.md` avsnitt 13). Claude Code:

```bash
claude mcp add --transport http floden https://floden.berring.se/mcp --header "Authorization: Bearer <token>"
```

Token skapas under *Nycklar > MCP-token* i webben (visas en gång). Så kopplar man klienterna: `docs/KOM-IGANG.md`.

## Rättigheter: bara dina projekt

Med inloggning på (servern) kräver varje projektverktyg ett konto med en roll i projektet; det kontrolleras i verktygslagret
(`floden_mcp.server`, `behorighet`) före allt annat, med samma regel som `/api/projekt` (`KontoAdapter.har_roll`):

| Verktyg | Krav |
| --- | --- |
| `scenario_skapa`, `fraga_skapa`, `kor`, `bestallning_skapa`, `rapport_skapa`, `rapport_installningar`, `pastaende_lagg_till`, `rapport_exportera` | rollen **utredare** i projektet |
| övriga projekt- och körningsverktyg | minst **lasare** |
| `projekt_lista`, `korlogg_hamta`, `metoder_lista`, `fragekatalog_sok`, `session_deklarera` | ingen projektroll (listan och loggen visar bara kontots egna) |
| `projekt_skapa` | ingen projektroll (projektet finns inte än): kräver inloggning och superadmin, eller organisationsadmin eller ägare i en organisation; kontot blir utredare (K48, `docs/KONTO.md`) |

- Projektet är argumentet `projekt` (förval `demokvarteret`). Verktyg utan `projekt` (körningar, resultat, rapporter) gäller i dag
  bara demokvarteret och kräver rollen där (`demoprojekt_roll`, förval `utredare`; `av` stänger dem för alla).
- Anrop utan konto (stdio mot en server med inloggning) nekas med "Inloggning krävs". Nekade anrop loggas som `nekad_behorighet`.
- Rapporter och beställningar hör till ett projekt (`projekt` i K33 och K30). Verktyg och resurser med `rapport_id` eller
  `bestallning_id` (och `floden://rapport/<id>`) prövas mot rapportens eller beställningens projekt, inte mot `projekt`-argumentet:
  saknas rollen (lasare, utredare för skrivande verktyg) svarar servern "Okänd rapport" eller "Okänd beställning", samma text som för
  ett id som inte finns. `rapport_hamta` utan id listar bara rapporter i projekt där kontot har en roll.

## Data som inte är öppna

En MCP-klient är en extern AI-tjänst, och `AGENTS.md` förbjuder att ledningsdata och stadens icke-öppna data lämnas till en
extern AI-tjänst. Därför finns en **uttrycklig spärr i verktygslagret** (`floden_mcp.oppenhet`, alltid på, tester i
`tests/floden_mcp/test_oppenhet.py`):

- **Före** varje anrop kontrolleras varje `korning_id` i argumenten; **efter** anropet kontrolleras varje körning som svaret nämner,
  och svaret lämnas inte ut om någon bygger på icke-öppna data (`nekad_oppenhet` i körloggen, felet nämner aldrig en sökväg).
  `korningar_lista` döljer sådana körningar (och anger hur många). `fraga_skapa` kontrollerar varje sökväg.
- En körning är öppen bara om **alla källor** i dess manifest (frågefilens scenarier och analysernas indata) är öppna.
- **Regeln (K38 i `docs/DATAKONTRAKT.md`):** (1) ledningsdata, `data/intern`, `data/konto` och `config.local.toml` är alltid stängda.
  (2) Utan metadata är bara demon och det som följer med repot öppet: `data/demo`, `testsvit`, `exempel`, `docs`, `data/regn`,
  `data/mcp`, `data/lagerprofiler`, samt MCP:s och kartverktygets arbetsmappar (där AI:n själv skriver). (3) En mapp med
  `oppenhet.toml` (`oppen = true` **och** en känd öppen licens, annars stängt) eller ett lager i `config.local.toml`
  (`[oppenhet.lager.<namn>]`) följer sin metadata; `oppen = false` stänger alltid. (4) **Allt annat är stängt**: `data/lokal`,
  stadens `baskarta_mapp` och `tat_laser_mapp`, och varje mapp utan metadata. *Saknas metadata: neka.*
- Stockholms 3D-baskarta (inte öppna data, beslut 2026-11-24): byggnader ur en lokal baskartefil (`kalla = "lokal_fil"`) gör geografin, dess lager och projekten i den till `oppen = false`, licens `intern`, även med `--villkor-kontrollerade` (`docs/DRIFT.md`, avsnittet Stockholm).
- Konsekvens i dag: data som hämtats med `floden data lantmateriet` till en egen mapp är stängda för MCP tills mappen fått en
  `oppenhet.toml` (Lantmäteriets öppna geodata är CC0: `oppen = true`, `licens = "CC0-1.0"`).

## Verktyg

Alla verktyg tar valfritt `modell` och `roll` (deklaration som loggas). Alla svar har `dataklass`.

| Verktyg | Gör |
| --- | --- |
| `projekt_skapa` | skapar ett projekt i en mappad geografi (`geografi`, `namn`, `avgransning` som GeoJSON-polygon eller `[xmin, ymin, xmax, ymax]` i EPSG:3011, `organisation_synlig`, `organisation`): samma kod, behörighet och svar som webbens `POST /api/projekt` (K48); svaret har `projekt_id` och varningar |
| `projekt_lista`, `projekt_info` | projekt (per konto: med inloggning på bara de som kontot har tillgång till, med roll), utbredning, antal byggnader, tillgängliga analyser |
| `geometri_sammanfattning` | byggnader: antal, fotavtryck, höjder, takformer, höjdkälla |
| `byggnader_lista` | byggnader (valfritt inom ett område) |
| `fragor_lista` | K20-frågor och om de är giltiga och körda |
| `korningar_lista`, `korning_status` | körningar och deras status |
| `resultat_sammanfattning` | mätvärden, jämförelser, varningar och antaganden med belägg |
| `resultat_varde` | värde i en punkt, eller statistik i en polygon (tak utesluts), med belägg |
| `jamfor` | skillnad före/efter (efter minus före), valfritt inom ett område |
| `karta_bild` | PNG (MCP-bild) med fast kartografi: ramtext, nord, skala, förklaring, byggnader; mark med värde vit, tak och celler utan värde ljusgrå |
| `metoder_lista` | lösare, version och valideringsstatus (`skiss` eller `rapport`) |
| `scenario_skapa` | ritat hus (polygon eller rektangel + höjd) som K9-scenario; kontrolleras av kärnan (`bara_kontroll` sparar inte) |
| `fraga_skapa` | ny K20-fråga, validerad av kärnan; sökvägar begränsade till demo-, kartverktygs- och cachemapparna |
| `korplan` | vad som skulle räknas och vad som finns i cachen |
| `kor` | startar körning asynkront, ger körnings-id |
| `vanta` | väntar på körningar (tidsgräns) |
| `korlogg_hamta` | läser körloggen och verifierar hashkedjan |
| `session_deklarera` | deklarerar modell och roll för sessionen |
| `bestallning_skapa` | beställning (K30): syfte, skede, mottagare, ämnen, frågor, villkor, scenario; ger frågeurval och id |
| `bestallning_hamta` | en beställning med id, eller projektets beställningar |
| `fragekatalog_sok` | frågekatalogen (Stockholms utredningsfrågor) med vad Floden kan belägga per fråga i skyfall (SK), dagsljus (DL) och solstudier (SO): `floden.markning` är visat, delvis, underlag_saknas eller ej_tillampligt |
| `rapport_skapa` | skissunderlag (nivå 1) för skyfall eller dagsljus/sol (`amne` eller `bestallning_id`) över klara körningar: kartor, metodstatus, frågor, beläggsadresser; valfria `installningar` (språk och layout) |
| `rapport_installningar` | visar eller ändrar rapportens inställningar: `sprakprofil` (`sbk` förval, `neutral`), `numrerade_rubriker`, `numrerade_figurer`, `avsandare`, `diarienummer`, `handlaggare`, `accentfarg`, `logotyp`, `typsnitt_brodtext`, `typsnitt_rubriker` (lokala sökvägar; bara sha256 lagras och svaras) |
| `pastaende_lagg_till` | påstående med status, fördjupning, text med platshållare `{{matt:<adress>}}` och belägg; vägras utan giltiga belägg |
| `rapport_kontrollera` | kontrollen inför export (spårning, scenario, nämnare, mått, osäkerhet, karta, status, dataskydd, metodstatus) |
| `rapport_exportera` | Word eller PDF efter godkänd kontroll; underkänd kontroll stoppar |
| `rapport_hamta` | rapporten (K33) eller listan över rapporter |

Resurser: `floden://metod/{vatten,dagsljus,luft,karna,validering,mcp}` (metoddokumenten) och
`floden://kontrakt/{datakontrakt,k0,k1,k9,k10,k11,k20,k21,k22}` och `floden://rapport/{rapport_id}`
(K33 som JSON). Frågekatalogen nås med `fragekatalog_sok`. Promptar: `utforska_demokvarteret`,
`prova_nytt_hus`, `skissunderlag_skyfall` och `skissunderlag_dagsljus`. Kedjan beställning, frågor, körning, rapport, kontroll och export går att köra helt via MCP (testet `test_bestallning_till_export_via_mcp_for_dagsljus`). Rapporterna och deras inställningar beskrivs i
`docs/RAPPORTER.md`. Sökvägar till logotyp och typsnitt hamnar aldrig i körloggen (argumentvärden
loggas inte), i svaren eller i rapporten; bara filens sha256.

### Nycklar och administration når AI:n aldrig

Det finns **inget MCP-verktyg, ingen resurs och ingen prompt för nyckelhantering**, och det ska inte komma något: AI:n får
aldrig läsa eller skriva en användares API-nycklar (Lantmäteriet, Anthropic; `docs/KONTO.md` avsnitt 10). Nycklarna lagras
krypterade, visas aldrig igen ens för användaren, och klartexten (`NyckelForrad.las`) når bara serverkod som själv ska
använda nyckeln. MCP-koden importerar inte nyckelförrådet, och ett test (`tests/floden_mcp/test_mcp_konto.py`) stoppar både
verktyg med nyckelrelaterade namn och sådana importer; `tests/floden_mcp/test_mcp_nycklar.py` kontrollerar dessutom (AST) att ingen MCP-modul importerar nyckelförrådet och kör alla verktyg och resurser med ett lagrat nyckelvärde utan att `NyckelForrad.las` anropas eller något värde syns i ett svar eller i körloggen. Detsamma gäller administrationen (inbjudningar, roller och spärr av
konton, `docs/KONTO.md` avsnitt 11): e-postadresser är personuppgifter och behörighet ges av människor. Verktygen ser bara
vad kontot redan har tillgång till (`projekt_lista`); körloggen har konto-id men aldrig nyckelvärden.

## Dataklasser

Varje svar och varje projekt har en klass: `oppet` < `internt` < `skyddat` < `personuppgifter`. Ett
svars klass är den högsta av verktygets och projektets. **Förval: en klient får bara `oppet`.** Ett
nekat anrop körs inte alls, ger ett felsvar och loggas med `utfall: nekad_policy`. Okända projekt
räknas som `internt`; demokvarteret är `oppet`. Konfiguration i `config.local.toml`:

```toml
[mcp]
tillatna_klasser = ["oppet"]            # förval för alla klienter
logg = "data/lokal/mcp/korlogg.jsonl"

[mcp.projektklasser]
mitt_projekt = "internt"

[mcp.klienter.claude-desktop]           # klientens namn ur MCP:s clientInfo
tillatna_klasser = ["oppet", "internt"]
```

Även `--tillat-klass oppet,internt` och miljövariabeln `FLODEN_MCP_TILLATNA_KLASSER`. Detta är en
enkel spärr, inte ett åtkomstsystem; roller och datapolicy per organisation kommer i R4.

## Körlogg

Varje MCP-anrop (verktyg, resurser, promptar) skrivs som en rad i en lokal JSONL-fil, som är
tillägg-endast och ligger under `data/lokal/` (inte i git). En rad har: tid, klient (`clientInfo`),
deklarerad modell och roll (argument, annars `session_deklarera`, annars `FLODEN_MCP_MODELL` och
`FLODEN_MCP_ROLL`), session, verktyg, `indata_hash` och `indata_nycklar`, körnings-id, dataklass,
utfall, `svar_sha256`, tid och `rapport_id` (de rapporter anropet rör). **Argumentvärdena skrivs inte**, och inga hemligheter eller
personuppgifter (med inloggning: bara ett opakt konto-id, och token-id när anropet kom med en MCP-token). Raderna är hashkedjade (`foregaende`,
`hash`); `korlogg_hamta` och `Korlogg.verifiera()` visar om något ändrats. Kan loggen inte skrivas
lämnas inget resultat ut. Loggens rader för en rapport och dess körningar är bilaga 2 i rapporten (bilaga B med språkprofilen neutral).

## Kända begränsningar

- Middleware-API:t i MCP-SDK 2.x är markerat som preliminärt; versionen är låst (`mcp==2.3.0`).
- Alla metoder är `skiss`; ingen validering för rapport är gjord.
- Rapporter är inte knutna till ett konto; alla med roll på demokvarteret ser dem.
- Policyn känner bara standardprojektet för körningsbaserade verktyg (ett projekt finns i dag).
- Klientinstruktionerna ovan är bara provade med SDK-klienten (stdio och HTTP), inte med Claude
  Desktop, Claude Code eller Codex.
