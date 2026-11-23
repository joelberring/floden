# Konton och inloggning

Status: byggt och testat med en falsk OIDC-leverantör (ingen riktig Google-inloggning är provad än). Paketet är `src/konto/`, testerna `tests/konto/`. Inloggningen är **av** som standard; webbservern (`src/webb/api.py`) är inte ansluten än (avsnitt 8).

Beslut (`docs/BESLUT.md`, raden "Drift"; Joel 2026-11-21): Floden har egen server och eget kontosystem, kopierat och avskalat ur O-Tid utan Eventor, tävlingar och klubbar. Användare utan SSO kommer bara in via inbjudan till projekt. Projektbehörighet ges aldrig automatiskt; förvaltning kopplas automatiskt bara via IdP-tenant och IdP-grupp. **Google-inloggning nu, förberett för SSO** (Microsoft Entra ID / generisk OIDC senare).

Logiken är porterad från O-Tid (MIT, joelberring/otid @ 4a446af): `google-oidc.ts`, `google-login.ts`, `user-account.ts` (sessioner, CSRF), `organizer-account-cookies.ts`. Det som är nytt i Floden är organisation/förvaltning/projekt, tenant- och gruppmappning, serverlagrat flödestillstånd och JWKS-cache med kid-rotation.

## 1. Moduler

| Fil | Innehåll |
| --- | --- |
| `konfig.py` | `[konto]` ur `config.toml`/`config.local.toml`, hemligheter ur miljön, strikt validering |
| `leverantor.py` | `Leverantor` (discovery-URL, klient-id, hemlighet, scopes, claimmappning, issuer-regler), fabrikerna `google()` och `entra()`, generisk OIDC ur konfiguration |
| `oidc.py` | state, nonce, PKCE S256, kodutbyte, kontroll av ID-token, JWKS-cache |
| `databas.py` | SQLite-schema (version 1) och transaktioner |
| `register.py` | organisation, förvaltning, regler, medlemskap, projekt, inbjudan, projektroll |
| `tilldelning.py` | från kontrollerad identitet till konto, organisation, förvaltning och inbjudningar |
| `sessioner.py` | sessioner, kakor, CSRF |
| `adapter.py` | HTTP-adaptern och `kraver_inloggning` |
| `admin.py` | serverkommandon (bootstrap) |
| `nycklar.py` | användarens egna API-nycklar, krypterade (Fernet) i tabellen `konto_nyckel` (avsnitt 10) |
| `kontoapi.py` | HTTP-API:t `/konto/nycklar` och `/konto/admin` (avsnitt 10 och 11) |

## 2. Sätt upp Google-klienten (Joel)

1. Google Cloud Console > välj eller skapa ett projekt > **APIs & Services > OAuth consent screen**. Användartyp **Extern** (eller **Intern** om bara en Workspace-domän ska in). Fyll i appnamn, support- och utvecklarmejl. Scopes: bara `openid`, `email`, `profile` (inga känsliga scopes, så ingen Googlegranskning behövs). Medan appen står i *Testing* måste varje testanvändare läggas till; sätt *In production* när den ska användas.
2. **Credentials > Create credentials > OAuth client ID > Web application.**
3. **Authorized redirect URIs**: exakt `https://<offentlig_url>/konto/callback/google`, till exempel `https://floden.example.se/konto/callback/google`. Inga jokertecken; skiftläge, slutstreck och port måste stämma tecken för tecken. För lokal provkörning läggs `http://localhost:8765/konto/callback/google` till (Google tillåter http bara för localhost). *Authorized JavaScript origins* behövs inte (flödet är serversidigt).
4. Kopiera klient-id och klienthemlighet till servern, **inte** till git:
   - miljö: `FLODEN_KONTO_GOOGLE_KLIENT_ID`, `FLODEN_KONTO_GOOGLE_KLIENT_HEMLIGHET`, eller
   - `config.local.toml` (ignoreras av git): `[konto.leverantorer.google]` med `klient_id` och `klient_hemlighet`. Se `config.local.toml.example`.
5. Sätt i `config.local.toml`: `inloggning = "pa"`, `offentlig_url`, `cookie_secure = true`, och `nya_konton = "inbjudan"`.
6. Skapa första administratören med serverkommandon (avsnitt 7). Utan det kan ingen logga in, eftersom `nya_konton = "inbjudan"` kräver en väg in.

Scopes som begärs: `openid email profile`. Det som sparas av Googles svar: `sub`, e-postadress, namn och (tillfälligt vid inloggning) `hd`. Råa påståenden sparas aldrig.

## 3. Slå på Entra ID senare (generisk OIDC)

Entra och generisk OIDC finns som kod och konfiguration men är **inte aktiverade** (`aktiv = false`). Så här slås Entra på:

1. I Entra admin center (Flodens egen tenant): **App registrations > New registration**, *Accounts in any organizational directory* (multitenant). Redirect URI (Web): `https://<offentlig_url>/konto/callback/entra`. Skapa en klienthemlighet. Under **Token configuration** lägg till *groups*-anspråket (helst *Groups assigned to the application*, så att listan blir kort).
2. Varje kommun godkänner appen i sin tenant (admin consent). Kommunen skickar sitt tenant-id (en GUID).
3. Registrera kommunen på servern: `python -m konto.admin skapa-organisation "<namn>"`, `registrera-tenant <org> entra <tenant-id>`, `skapa-forvaltning`, `mappa-grupp <org> <forvaltning> <grupp-id>` (en rad per IdP-grupp som ska ge en förvaltning).
4. Sätt `aktiv = true`, `klient_id` och hemlighet för `[konto.leverantorer.entra]`.

Vad Entra-läget gör (testat med falsk leverantör, inte mot en riktig tenant):

- **Tenant** läses ur `tid`. Issuer måste vara exakt `https://login.microsoftonline.com/<tid>/v2.0` för just den tenanten. En tenant som inte är registrerad som organisation nekas (`okand-tenant`) även med giltig token; en multitenant-app tar annars emot vem som helst.
- **Grupper** läses ur `groups` och mappas via regler (tabell `forvaltning_grupp`) till förvaltning. Vid *group overage* (`_claim_names`, fler än ca 200 grupper) är grupperna okända: medlemskapen lämnas oförändrade och en `groups`-hämtning via Graph får göras senare.
- **E-posten i Entra är inte verifierad** (nOAuth). Den visas som profiluppgift men används aldrig för att koppla konton eller ta emot inbjudningar. Nyckeln är (`tid`, `sub`).
- Annan IdP (Keycloak, Okta, svensk federation via OIDC-gateway): `typ = "oidc"` med `discovery_url`, `scopes`, `claims` (vilka påståenden som är subject, e-post, grupper, tenant) och `flertenant`. `epost_betrodd = true` får bara sättas för en IdP som verkligen verifierar adresser.
- SAML 2.0, SCIM och svensk identitetsfederation ingår inte (`docs/ARKITEKTUR-DRIFT.md` 3b, steg 3 och uppåt).

## 4. Datamodell (SQLite, `data/konto/konto.sqlite`)

Sökvägen står i `[konto].databas`. Filen skapas med läsrätt bara för ägaren; den ligger på lokal disk och ignoreras av git. Schemaversionen ligger i `PRAGMA user_version` och migreringar är en lista i `databas.py`.

```
organisation ──< organisation_tenant (leverantör, tenant)      IdP-tenant -> organisation
             ──< organisation_doman  (domän)                   domän -> organisation (bara intygad)
             ──< forvaltning ──< forvaltning_grupp (IdP-grupp) IdP-grupp -> förvaltning
             ──< projekt (forvaltning_id valfri)
konto ──< identitet (leverantör, tenant, subject)   unik; nyckeln är aldrig e-post
      ──< medlemskap (organisation, forvaltning?, roll, kalla)
      ──< projektbehorighet (projekt, roll, kalla)
projekt ──< projektinbjudan (e-post, roll, giltig_till, anvand_av)
konto ──< konto_nyckel (typ, krypterad, slutar_pa, sparad)    en rad per konto och typ (schemaversion 2)
session, pagaende_inloggning (flödets tillstånd), handelse (logg utan personuppgifter)
```

- **Roller:** `superadmin` är ett fält på kontot (sätts bara med serverkommando). Medlemskapets roll är `medlem` eller `organisationsadmin`. Projektroll är `lasare` eller `utredare`.
- **`kalla`** på medlemskap: `idp-tenant`, `idp-grupp`, `doman` eller `manuell`, så att automatiska medlemskap kan prövas om. På projektbehörighet: `inbjudan` eller `manuell`.
- E-postadressen är unik bara bland **verifierade** adresser (partiellt index). En overifierad adress, som Entras, kan därför inte uppta en adress någon annan kan verifiera.

## 5. Regler för behörighet

| Signal | Ger | Aldrig |
| --- | --- | --- |
| IdP-tenant (registrerad) | organisation (medlem) | förvaltning, projekt |
| IdP-grupp (mappningsregel) | förvaltning (medlem), omprövas vid varje inloggning | projekt, organisationsadmin |
| E-postdomän | organisation, **bara** om leverantören intygar domänen (Googles `hd`, och adressen slutar på samma domän) | förvaltning, projekt |
| Inbjudan till verifierad adress | projektroll (`lasare`/`utredare`), en gång | |
| Organisationsadmin/superadmin (uttryckligt) | hantera medlemskap, inbjudningar och projektroller | läsa projektdata utan egen projektroll |

- `har_projektroll`/`kraver_projektbehorighet` ser bara `projektbehorighet`. Medlemskap i organisation eller förvaltning, och superadmin, ger ingen projektdata; en admin får tilldela sig själv en roll, och det loggas.
- **Ingen e-postkoppling mellan konton utom** (a) verifierad adress från en betrodd leverantör, till ett verifierat konto eller ett av servern förberett konto utan identitet. Ett konto som skapats via Entra (med identitet och overifierad adress) kan aldrig bli målet för en sådan koppling (skydd mot att någon i förväg skapar ett konto med någon annans adress).
- `demoprojekt_roll = "utredare"` (standard): varje inloggat, ej spärrat konto har den rollen på det påhittade demokvarteret (`demoprojekt = "demokvarteret"`), utan inbjudan. `"lasare"` ger bara läsning, `"av"` kräver uttrycklig roll även där. Riktiga projekt påverkas inte (BESLUT 2026-11-23).
- `nya_konton = "inbjudan"` (standard): ett nytt konto skapas bara om identiteten har en väg in (registrerad tenant, intygad domän som är registrerad, eller en öppen inbjudan till den verifierade adressen). Annars ger callback 403 `ingen-inbjudan` och inget sparas. `"oppen"` skapar konto åt vem som helst, utan någon behörighet.
- Omprövning vid inloggning: ändrad grupp flyttar `idp-grupp`-medlemskap (logg: konto-id, förvaltning, regel-id). `manuell`-medlemskap rörs inte.
- Ett spärrat konto (`sparrad`) kan inte logga in och dess sessioner slutar gälla direkt.

## 6. Säkerhetsval

**Inloggningsflödet (`oidc.py`, `adapter.py`)**

- Kodflöde med **PKCE S256**, `state` och `nonce` (256 bitar vardera, `secrets`). `state` är engångs: raden förbrukas även vid fel; giltig i 10 minuter.
- Flödets hemligheter (nonce, PKCE-verifierare) ligger **i databasen**, inte i en kaka som webbläsaren kan läsa (O-Tid lägger dem i en kaka). Kakan `*-inloggning` (HttpOnly, SameSite=Lax, 10 min) är bara en **bindning** till webbläsaren som startade, så en angripares `state`/`code` i offrets webbläsare ger ingen session (login-CSRF). Antalet pågående inloggningar är begränsat.
- **ID-token:** algoritm måste vara `RS256` (`none` och `HS256` nekas innan något annat), `kid` måste finnas, signaturen kontrolleras mot leverantörens JWKS, sedan `aud` (och `azp` när den finns eller `aud` har flera värden), `iss` (fast lista för Google; för Entra den tenantspecifika adressen), `exp` och `iat` (60 s klocktolerans), `nbf`, `nonce` (konstant tid) och `email_verified` (sant eller `"true"`; annars nekas inloggningen). Tidskontrollen görs av koden med injicerbar klocka.
- **JWKS-cache** (1 timme) med **kid-rotation**: en okänd `kid` ger en omhämtning, men högst en per minut, så att en angripare inte kan driva trafik mot leverantören. En borttagen nyckel slutar gälla vid nästa omhämtning. Discovery cachas en timme; alla adresser måste vara https och issuer i discovery måste stämma.
- Kodutbytet skickar klienthemligheten bara över https (`client_secret_post`) med tidsgräns och storleksgräns. Leverantörens svarskroppar loggas aldrig.
- `atervand` (var man hamnar efter inloggning) får bara vara en sökväg på den här servern (ingen `//`, inget `\`, inga kontrolltecken).

**Sessioner och CSRF (`sessioner.py`)**

- Sessions-id och hemlighet slumpas (256 bitar); databasen lagrar bara sha256 av hemligheten; jämförelsen görs i konstant tid även när sessionen saknas. Utgång efter `sessionstimmar` (standard 12 h), utloggning återkallar sessionen på servern.
- Kaka: `HttpOnly`, `Secure` (av bara för localhost, annars vägrar konfigurationen starta), `SameSite=Lax`, `Path=/`, utan `Domain`. Med Secure används prefixet `__Host-` (`__Host-floden-session`); på localhost enkla namn.
- **CSRF** för POST/PUT/PATCH/DELETE: dubbel inlämning. Kakan `*-csrf` (inte HttpOnly) måste vara lika med headern `X-Floden-CSRF`, och båda måste matcha den hash som hör till sessionen, så en kaka som en annan värd satt eller en annan sessions token duger inte. Finns en `Origin`-header måste den vara exakt `offentlig_url`. Utloggning godtar i stället för CSRF-paret exakt `Origin` (som O-Tid: att logga ut någon är ofarligt).
- Kakor med samma namn två gånger räknas som ingen kaka.

**Personuppgifter**

- Loggar och tabellen `handelse` innehåller konto-id, projekt-id, förvaltnings-id och regel-id, aldrig e-post, namn, `sub` eller token. Testet `test_inga_personuppgifter_i_loggar_eller_handelser` kontrollerar det över inloggning, nekad inloggning och utloggning.
- E-post och namn finns i databasen (kontot, inbjudningar). Databasen är lokal, läsbar bara för ägaren, och ligger utanför git. Inga personuppgifter i repo, prompter eller testdata (testerna använder `example.test`).
- Inga hemligheter i repo: `config.local.toml` är ignorerad, `config.local.toml.example` visar nycklarna, hemligheter går bäst i miljön.

**Avgränsningar (ska inte antas vara löst)**

- Ingen spärr mot upprepade inloggningsförsök per IP (O-Tids `account-throttle.ts` är inte portad; det finns inga lösenord att gissa, men `/konto/logga-in/*` skriver en rad per anrop, begränsad till 2000). Lägg hastighetsgräns i Caddy framför servern.
- Ingen "logga ut överallt" i adaptern (finns som `Sessioner.aterkalla_alla`), ingen ta-bort-konto, ingen e-postverifiering (Google intygar adressen), ingen OAuth-server för AI-kopplingar. Kontohantering för användaren och organisationsadmin finns som funktioner i `Register` men inte som HTTP-ändpunkter ännu.
- Inget är provat mot riktiga Google- eller Entra-slutpunkter. De första riktiga inloggningarna kan avslöja skillnader (t.ex. Googles `hd`, Entras `groups`).

## 7. Administratörskommandon (bootstrap)

Körs på servern (läser `config.toml` och `config.local.toml`; skriver id:n, aldrig e-post):

```
PYTHONPATH=src python -m konto.admin forbered-konto joel@example.se         # skriver konto-id
PYTHONPATH=src python -m konto.admin ge-superadmin <konto-id>
PYTHONPATH=src python -m konto.admin skapa-organisation "Stockholms stad"    # skriver organisations-id
PYTHONPATH=src python -m konto.admin gor-organisationsadmin <konto-id> <org-id>
PYTHONPATH=src python -m konto.admin skapa-forvaltning <org-id> "Stadsbyggnadskontoret"
PYTHONPATH=src python -m konto.admin skapa-projekt <org-id> "Provområdet"
PYTHONPATH=src python -m konto.admin registrera-tenant <org-id> entra <tenant-guid>
PYTHONPATH=src python -m konto.admin mappa-grupp <org-id> <forvaltning-id> <grupp-id>
PYTHONPATH=src python -m konto.admin registrera-doman <org-id> kommun.se        # kräver Googles hd
```

`forbered-konto` skapar ett konto utan identitet. Första gången samma adress loggar in med Google (verifierad) kopplas Googles identitet till det kontot, och därmed får personen superadmin eller de medlemskap som förbereddes. Inbjudningar till projekt skapas med `Register.bjud_in(utforare, projekt_id, epost, roll)` (kräver organisationsadmin eller superadmin) och tas emot automatiskt vid inloggning. Samma sak görs i webben på sidan Administration (avsnitt 11); kommandona här behövs bara för det första steget (superadmin, organisation, projekt) och för regler.

## 8. Integrationsguide för `src/webb/api.py`

**Status:** kopplat i `src/webb/api.py` (`FlodenApiApp.hantera_http`, grenen `demo-visning`). Ordningen nedan följs: `/konto/*` först, klientens statiska filer (ingen data), `kraver_inloggning` före att kroppen läses, sedan API:t. Med `inloggning = "pa"` skickas inget `Access-Control-Allow-Origin`; klienten (`webb/src/konto.ts`, `api.ts`) läser `/konto/jag`, visar Google-knapp, *Inloggad som* och *Logga ut* och lägger `X-Floden-CSRF` på alla POST. Tester: `tests/webb/test_inloggning.py` (falsk leverantör). `/api/halsa` är öppen (`oppna_sokvagar`). Prövat bara mot den falska leverantören, inte mot Google. Texten nedan är den ursprungliga guiden.

Adaptern känner inte till `http.server`. Anropen webbservern ska göra:

| Anrop | När |
| --- | --- |
| `las_konto_konfig(las_konfig(konfig_fil, "config.local.toml"))` och `KontoAdapter(konf)` | en gång vid start i `skapa_http_server` |
| `konto.ager_sokvag(sokvag)` och `konto.hantera_konto(metod, sokvag, query, headers, kropp)` | för allt under `/konto/` |
| `konto.kraver_inloggning(req)` | före varje annan ändpunkt, **före** att kroppen läses (25 MB) |
| `konto.kraver_projektbehorighet(req, projekt_id, minst)` | i ändpunkter som tillhör ett projekt |

Svaret är `(status, headers, kropp)` där `headers` är en **lista av par** (flera `Set-Cookie`). `Forfragan.fran_ra(metod, self.path, dict(self.headers.items()), kropp)` bygger förfrågan ur `BaseHTTPRequestHandler`.

```python
# src/webb/api.py, i skapa_http_server (importer överst: from gemensamt.konfig import las_konfig;
#                                        from konto import Forfragan, KontoAdapter, las_konto_konfig)
app = FlodenApiApp(testsvit_rot=testsvit_rot, konfig_fil=konfig_fil)
konto = KontoAdapter(las_konto_konfig(las_konfig(konfig_fil, "config.local.toml")))

class _FlodenHandler(BaseHTTPRequestHandler):
    def _skicka(self, status: int, headers: Iterable[tuple[str, str]], kropp: bytes) -> None:
        self.send_response(status)
        for nyckel, varde in headers:           # lista av par, inte dict
            self.send_header(nyckel, varde)
        self.send_header("Content-Length", str(len(kropp)))  # saknas i 302-svaren från konto
        self.end_headers()
        self.wfile.write(kropp)

    def _hantera(self, metod: str) -> None:
        rubriker = dict(self.headers.items())
        req = Forfragan.fran_ra(metod, self.path, rubriker)         # utan kropp än
        if konto.ager_sokvag(req.sokvag):
            kropp_in = self._las_kropp()                            # befintlig läsning med 25 MB-spärr
            status, headers, kropp = konto.hantera_konto(
                req.metod, req.sokvag, req.query, req.headers, kropp_in)
            return self._skicka(status, headers, kropp)
        kontroll = konto.kraver_inloggning(req)                     # läget "av" => tillaten
        if not kontroll.tillaten:
            assert kontroll.svar is not None
            return self._skicka(*kontroll.svar)                     # 401 eller 403, oförändrat
        kropp_in = self._las_kropp() if metod == "POST" else b""
        status, headers, kropp = app.hantera_forfragan(metod, self.path, kropp_in)
        self._skicka(status, headers.items(), kropp)

    def do_GET(self) -> None:  # noqa: N802
        self._hantera("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._hantera("POST")
```

Ändpunkter som tillhör ett projekt (när projekt blir en del av API:t) anropar `konto.kraver_projektbehorighet(req, projekt_id, "utredare")` i stället för `kraver_inloggning`. Kontot (`kontroll.inloggad.konto_id`) kan skickas vidare till `FlodenApiApp` för loggning av jobb (konto-id, aldrig e-post).

Att tänka på vid anslutningen:

- **Samma ursprung:** webbklienten och API:t ska nås från samma `offentlig_url` (Caddy framför, som i `docs/ARKITEKTUR-DRIFT.md` avsnitt 4). `_json_svar` skickar i dag `Access-Control-Allow-Origin: *`, vilket inte gäller förfrågningar med kakor och som bör tas bort när inloggningen är på. Vites proxy (`/api/*`) räcker vid utveckling; lägg `/konto/*` i samma proxy.
- **CSRF i klienten:** `fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-Floden-CSRF": <värdet av kakan __Host-floden-csrf> } })`. Kakans namn är `floden_csrf` när `cookie_secure = false`. Vid 401 från API:t: `location.href = "/konto/logga-in/google?atervand=" + encodeURIComponent(location.pathname)`. `GET /konto/jag` ger `{"inloggning": "av"}` (visa ingen inloggningsknapp), 401 eller kontot med medlemskap och projekt.
- **Hälsokollen** `/api/halsa` är öppen (`oppna_sokvagar`); lägg till fler öppna sökvägar där det behövs, till exempel statiska filer.
- `ThreadingHTTPServer` och SQLite: varje anrop öppnar en egen anslutning (WAL, `BEGIN IMMEDIATE`), så trådarna blockerar inte varandra.
- Starta bara servern öppet (`0.0.0.0`) bakom Caddy med https och `inloggning = "pa"`; på localhost fungerar `cookie_secure = false`.

## 10. Nycklar (`/konto/nycklar`, sidan Nycklar)

Varje inloggad användare lägger in sina egna API-nycklar: `lantmateriet` (Lantmäteriets STAC/nedladdning: nyckel,
hemlighet och metod `oauth2`/`basic`, samma som `lantmateriet.nycklar`) och `anthropic` (Claude, för framtida AI-körningar
på servern; ingen kod anropar den än). Lagring och regler (`src/konto/nycklar.py`):

- **Krypterat.** Fernet (`cryptography`) med hemligheten i miljövariabeln `FLODEN_NYCKELHEMLIGHET` (flera nycklar skiljs
  med komma, den första krypterar och alla läser, så att hemligheten kan bytas). En rad per konto och typ i
  `konto_nyckel`; chiffertexten bär konto-id och typ, så en rad som flyttas till ett annat konto går inte att läsa.
  Skapande, drift och byte av hemligheten: `docs/DRIFT.md`.
- **Saknas hemligheten** (eller är den ogiltig) är funktionen avstängd: `GET` svarar `paslagen: false` med en text om
  att nyckelhanteringen inte är påslagen på servern, `PUT` ger 503 `nycklar-av`, och ingenting sparas okrypterat.
  Borttagning kräver ingen hemlighet och fungerar även då. Med inloggning av finns ingen nyckelsida (404).
- **Visas aldrig igen.** Databasen har chiffertexten och de fyra sista tecknen av det fält som identifierar nyckeln
  (`slutar_pa`; för Lantmäteriet hemligheten). Svaren har bara `sparad`, `sparad_datum` och `slutar_pa`. Användaren kan
  ersätta (ny `PUT`) eller ta bort sin nyckel.
- **Aldrig i loggar, felmeddelanden, körlogg, `run.json` eller svar.** Felen nämner fältets id, aldrig värdet (inte
  heller namnet på ett okänt fält); händelser och loggrader har konto-id och typ. Klartexten når bara serverkod som
  ska använda nyckeln (`NyckelForrad.las`): det finns ingen väg till den från HTTP eller MCP.
- **Bara kontots egna.** Adressen bär ingen konto-id; kontot kommer ur sessionen.

| Anrop | Gör |
| --- | --- |
| `GET /konto/nycklar` | alla typer med status, fälten de består av, och `paslagen`/`orsak`/`text` |
| `GET /konto/nycklar/<typ>` | en typ: `sparad`, `sparad_datum`, `slutar_pa`, `paslagen` |
| `PUT /konto/nycklar/<typ>` | sparar eller ersätter; kropp `{"nyckel": …}` (`anthropic`) eller `{"klient_id": …, "klient_hemlighet": …, "metod": …}` (`lantmateriet`; `metod` förval `oauth2`) |
| `DELETE /konto/nycklar/<typ>` | tar bort (`borttagen` eller `fanns-inte`) |

Inloggning krävs (401). `PUT` och `DELETE` kräver CSRF (kaka och `X-Floden-CSRF`) och rätt `Origin`, som alla andra skrivande
anrop (403 annars). Okänd typ ger 404, ogiltigt värde 400 (8 till 512 synliga tecken utan blanksteg), kropp över 4 kB 413.

**Lantmäteri-hämtningen.** Med `[konto] inloggning = "pa"` läser `floden data lantmateriet --konto <konto-id>` kontots egen
nyckel (`lantmateriet.nycklar.las_nycklar_for_konto`) och aldrig servernyckeln i miljön eller `config.local.toml`; saknas
kontots nyckel, eller är nyckelhanteringen avstängd, stoppar hämtningen med ett tydligt fel. Med inloggningen av (lokalt)
gäller `LANTMATERIET_KLIENT_ID`, `LANTMATERIET_KLIENT_HEMLIGHET` och `[lantmateriet]` som förut. Webben har ännu ingen
Lantmäteri-hämtning; när den kommer ska den anropa `las_nycklar_for_konto` med den inloggades konto.

## 11. Administration i webben (`/konto/admin`, sidan Administration)

Synlig för superadmin och organisationsadmin (`GET /konto/jag` har `konto.admin`); alla andra får 403 (utloggade 401).
Använder registrets funktioner (`bjud_in`, `ge_projektroll`, `aterkalla_projektroll`) och deras behörighetskontroller:
en organisationsadmin når bara projekt i sina organisationer, en superadmin alla. Projekt-id:n är slumpade; demokvarteret
behöver ingen inbjudan (`demoprojekt_roll`, avsnitt 5) och visas inte här. Skrivande anrop kräver CSRF som ovan.

| Anrop | Gör |
| --- | --- |
| `GET /konto/admin/projekt` | projekt som får administreras (organisation, antal medlemmar, öppna inbjudningar) |
| `GET /konto/admin/projekt/<id>` | projektet, `roller`, `medlemmar` (namn, e-post, roll, källa, spärrad) och `inbjudningar` (`oppen`, `anvand`, `utgangen`) |
| `POST /konto/admin/projekt/<id>/inbjudningar` | `{"epost", "roll"}` med roll `lasare` eller `utredare`; 201. Tas emot när personen loggar in med en verifierad adress |
| `PUT /konto/admin/projekt/<id>/medlemmar/<konto>` | `{"roll"}` ändrar (eller ger) rollen |
| `DELETE /konto/admin/projekt/<id>/medlemmar/<konto>` | tar bort projektrollen |
| `PUT /konto/admin/konton/<konto>` | `{"sparrad": true|false}` spärrar eller öppnar kontot |

Spärr (`Register.satt_sparrad`): ingen kan spärra sig själv eller en superadmin (det sker bara med serverkommando); en
organisationsadmin kan bara spärra konton som har medlemskap eller projektroll i den egna organisationen, och aldrig en annan
organisationsadmin (det gör superadmin). Ett spärrat konto loggas ut direkt och kommer inte in igen. Att administrera ger
ingen läsrätt till projektdata (avsnitt 5).

**Projektlistan.** `GET /api/projekt` (och MCP-verktyget `projekt_lista`) visar med inloggning på bara kontots projekt:
demokvarteret (om `demoprojekt_roll` inte är `av`, med den rollen) och projekt där kontot har en uttrycklig roll. Projekt ur
registret har ännu inga beräkningar (`finns: false`) och en enkel projektsida utan siffror.

**Körningar.** Med inloggning på gäller en projektregel på `/api/korningar/<id>/*` (status, resultat, `marksiffror`, `avlasning`, lager, filer, tabeller) och i MCP-verktygen som tar ett körnings-id (`korning_id`, `korning_ids`, `korningar`): kontot ska ha minst rollen `lasare` i körningens projekt. Projektet är körningens `projekt_id` i `korning.json` (valfritt fält), och för demokörningarna som `demo.json` pekar ut demokvarteret, som följer `demoprojekt_roll`. K0-manifestet har ingen projektuppgift, och registrets projekt har inga körningar än. En körning utan projekt (testsvit, äldre körningar) kan bara superadmin läsa; superadmin har ingen läsrätt i ett projekts körningar utan egen projektroll. Saknad behörighet ger **404** (samma svar som för ett id som inte finns, så att id:n inte läcker; `/api/projekt/<id>` ger 403). `GET /api/korningar` och MCP `korningar_lista` visar bara körningar kontot har rätt till. Med inloggning av gäller ingen av reglerna. Att starta en körning (`POST /api/fragor/<ref>/kor`) sätter inget projekt än, så nystartade körningar ser bara superadmin.

## 9. Köra testerna

```
pip install -e ".[dev]"                  # innehåller PyJWT[crypto]
PYTHONPATH=src python -m pytest tests/konto -q
```

Beroende: `PyJWT[crypto]==2.15.0` (extra `konto`, också i `dev`). Det behövs för RS256-kontroll mot JWKS (`jwt.decode`, `PyJWK`) och drar in `cryptography`, som testerna också använder för att skapa den falska leverantörens RSA-nyckel. Motiv: att skriva egen RSA/JWS-kontroll vore den sämsta platsen att spara in; biblioteken är de som O-Tids `jose` motsvarar. Läget `inloggning = "av"` importerar inte PyJWT.
