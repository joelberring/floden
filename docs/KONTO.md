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
| `kontoapi.py` | HTTP-API:t `/konto/nycklar`, `/konto/admin` och `/konto/mcp-token` (avsnitt 10, 11 och 12) |
| `mcptoken.py` | personliga MCP-token, bara sha256 sparas (avsnitt 12) |

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
      ──< mcp_token (namn, token_hash, slutar_pa, skapad, senast_anvand, aterkallad)  (schemaversion 3)
      ──< oauth_grant (klient, resurs, aterkallad) ──< oauth_token (access_hash, refresh_hash)   (schemaversion 4)
oauth_klient (namn, redirect_uris) och oauth_kod (kod_hash, utmaning, förbrukad) hör till OAuth för MCP, avsnitt 13
session, pagaende_inloggning (flödets tillstånd), handelse (logg utan personuppgifter)
```

- **Roller:** `superadmin` är ett fält på kontot (sätts bara med serverkommando). Medlemskapets roll (organisationsroll) är, i stigande ordning, **`medlem` < `ledare` < `organisationsadmin` < `agare`** (schemaversion 5; de två första värdena är de gamla). Projektroll är `lasare` eller `utredare`. Organisationens inbjudningar ligger i `organisationsinbjudan` (schemaversion 5). Projektet har `organisation_synlig` (0/1, förval 0) och `skapad_av` (schemaversion 6).
- **`kalla`** på medlemskap: `idp-tenant`, `idp-grupp`, `doman` eller `manuell`, så att automatiska medlemskap kan prövas om. På projektbehörighet: `inbjudan` eller `manuell`.
- E-postadressen är unik bara bland **verifierade** adresser (partiellt index). En overifierad adress, som Entras, kan därför inte uppta en adress någon annan kan verifiera.

## 5. Regler för behörighet

| Signal | Ger | Aldrig |
| --- | --- | --- |
| IdP-tenant (registrerad) | organisation (medlem) | förvaltning, projekt |
| IdP-grupp (mappningsregel) | förvaltning (medlem), omprövas vid varje inloggning | projekt, organisationsadmin |
| E-postdomän | organisation, **bara** om leverantören intygar domänen (Googles `hd`, och adressen slutar på samma domän) | förvaltning, projekt |
| Inbjudan till verifierad adress | projektroll (`lasare`/`utredare`), en gång | |
| Organisationsroll (uttryckligt, av ägare, organisationsadmin, superadmin eller serverkommando) | `ledare`: läsare, `organisationsadmin` och `agare`: utredare, i de projekt som tillhör organisationen **och är delade med den** (`organisation_synlig`) | rätt i privata projekt, rätt i andra organisationers projekt |
| Superadmin | hantera medlemskap, inbjudningar och projektroller, allt i servern | läsa projektdata utan egen projektroll eller organisationsroll |

- **Effektiv projektroll (`Register.effektiv_projektroll`, Joels beslut 2026-11-23, väntar på att föras in i BESLUT).** Den enda platsen där organisationsroller blir projekträtt. Kontots rätt i ett projekt är den uttryckliga projektrollen (`projektbehorighet`) och, **bara om projektet är delat med organisationen (`organisation_synlig`, förval falskt)**, den högsta av den och det organisationsrollen ger i projektets egen organisation: `ledare` ger `lasare`, `organisationsadmin` och `agare` ger `utredare`, `medlem` ger inget. Den används av `kraver_projektbehorighet`, `har_roll` (körningar, `konto_far_se_korning`, MCP-verktygen), `/api/projekt` och MCP `projekt_lista` (fältet `kalla` är `projekt` eller `organisation`). Åtkomst som beror på organisationsrollen loggas med konto-id och projekt-id (`projektatkomst via organisationsroll`), aldrig e-post. **Ett privat projekt (förval) ger organisationsroller ingenting; förvaltningsmedlemskap, enkelt `medlem` och superadmin ger aldrig projektdata**, och ett spärrat konto har ingen rätt. Delningen ändras av projektets utredare (uttrycklig roll) och den som skapade projektet, och av ingen annan: inte organisationens ägare eller admin, inte superadmin (utan egen roll). Växlar man tillbaka till privat gäller det vid nästa anrop. **Självtilldelning:** en administratör kan inte ge sig själv en roll (direkt eller med en inbjudan till den egna adressen) i ett privat projekt; superadmin får, och det loggas (`projektroll-sjalvtilldelad`, konto-id och projekt-id). I ett delat projekt ger en egen roll inget extra. En admin får fortfarande tilldela andra roller i projektet; tidigare fick en admin tilldela sig själv en roll, och det loggas.
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
- Ingen "logga ut överallt" i adaptern (finns som `Sessioner.aterkalla_alla`), ingen ta-bort-konto, ingen e-postverifiering (Google intygar adressen), OAuth för AI-kopplingar finns i avsnitt 13 (personliga MCP-token i avsnitt 12). Kontohantering för användaren och organisationsadmin finns som funktioner i `Register` men inte som HTTP-ändpunkter ännu.
- Inget är provat mot riktiga Google- eller Entra-slutpunkter. De första riktiga inloggningarna kan avslöja skillnader (t.ex. Googles `hd`, Entras `groups`).

## 7. Administratörskommandon (bootstrap)

Körs på servern (läser `config.toml` och `config.local.toml`; skriver id:n, aldrig e-post):

```
PYTHONPATH=src python -m konto.admin forbered-konto joel@example.se         # skriver konto-id
PYTHONPATH=src python -m konto.admin ge-superadmin <konto-id>
PYTHONPATH=src python -m konto.admin skapa-organisation "Stockholms stad"    # skriver organisations-id
PYTHONPATH=src python -m konto.admin skapa-organisation "Floden" --agare <konto-id>   # med en ägare
PYTHONPATH=src python -m konto.admin bjud-in-organisation <org-id> <epost> <roll>   # medlem, ledare, organisationsadmin eller agare
PYTHONPATH=src python -m konto.admin kontostatus <konto-id>                  # superadmin, spärr och organisationsroller
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
  (`slutar_pa`; för Lantmäteriet användarnamnet eller nyckeln, aldrig lösenordet eller hemligheten; rader som sparades
  före 2026-11-24 har de fyra sista tecknen av hemligheten tills nyckeln sparas på nytt). Svaren har bara `sparad`,
  `sparad_datum` och `slutar_pa`. Användaren kan ersätta (ny `PUT`) eller ta bort sin nyckel.
- **Aldrig i loggar, felmeddelanden, körlogg, `run.json` eller svar.** Felen nämner fältets id, aldrig värdet (inte
  heller namnet på ett okänt fält); händelser och loggrader har konto-id och typ. Klartexten når bara serverkod som
  ska använda nyckeln (`NyckelForrad.las`): det finns ingen väg till den från HTTP eller MCP.
- **Bara kontots egna.** Adressen bär ingen konto-id; kontot kommer ur sessionen.

| Anrop | Gör |
| --- | --- |
| `GET /konto/nycklar` | alla typer med status, fälten de består av, och `paslagen`/`orsak`/`text` |
| `GET /konto/nycklar/<typ>` | en typ: `sparad`, `sparad_datum`, `slutar_pa`, `paslagen` |
| `PUT /konto/nycklar/<typ>` | sparar eller ersätter; kropp `{"nyckel": …}` (`anthropic`) eller `{"klient_id": …, "klient_hemlighet": …, "metod": …}` (`lantmateriet`; `metod` är `basic` eller `oauth2`, förval `basic`) |
| `DELETE /konto/nycklar/<typ>` | tar bort (`borttagen` eller `fanns-inte`) |

Inloggning krävs (401). `PUT` och `DELETE` kräver CSRF (kaka och `X-Floden-CSRF`) och rätt `Origin`, som alla andra skrivande
anrop (403 annars). Okänd typ ger 404, ogiltigt värde 400 (8 till 512 synliga tecken utan blanksteg), kropp över 4 kB 413.

**Lantmäteri på sidan Nycklar.** Avsnittet har tre delar (webb: `webb/src/kontosidor-html.ts`; API: `lantmateriet`-posten i
`GET /konto/nycklar`):

- **Sätt att logga in** som två alternativknappar med begripliga etiketter: *Geotorgets användarnamn och lösenord
  (privatperson)* (`metod = basic`, **förval**, som i O-Tid) och *API-portalens nyckel och hemlighet* (`oauth2`). Fältet
  `metod` har `val_etiketter` och `standard` i API-svaret. Ett förval ändrar inte en redan sparad nyckel: den behåller sin
  metod tills användaren sparar om. (Tidigare var förvalet `oauth2`, och den som missade att sätta `basic` fick 401.)
- **Produkter att beställa på Geotorget**: tabellen *Produkt | Används till | Behövs/Valfri* ur
  `data/lantmateriet/produkter.toml` (svarsfältet `produkter`; koden i `src/lantmateriet/produkter.py`), med länk till
  Geotorget och till den här filen ("Så kopplar du Lantmäteriet"). Registret har bara avgiftsfria produkter
  (`docs/BESLUT.md` 2026-11-24, listan i `docs/GEOTORGET.md`) och inga med personuppgiftsnära innehåll (Lägenhet,
  Belägenhetsadress, Fastighetsindelning, Samfällighetsförening används inte).
- **Beställningstid**: en tydlig rad (`bestallning_text`) om att en beställning på Geotorget kan ta tid, ibland dagar,
  innan produkten är aktiv, och att Lantmäteriet då svarar 401 eller 403.

Sparade värden visas aldrig: bara "Sparad <datum>, slutar på …<fyra tecken>".

**Så kopplar du Lantmäteriet.** (1) Skaffa ett Geotorget-konto (privatperson) på geotorget.lantmateriet.se. (2) Beställ
produkterna i tabellen för det du vill göra; Laserdata Nedladdning, skog behövs för markmodellen, de andra är valfria.
(3) Vänta tills Geotorget visar produkten som aktiv (kan ta dagar). (4) Lägg in användarnamn och lösenord på sidan Nycklar
med *Geotorgets användarnamn och lösenord* valt. Har du i stället en nyckel och hemlighet från API-portalen
(apimanager.lantmateriet.se), välj *API-portalens nyckel och hemlighet*.

**Felmeddelandet vid 401/403.** Svarar Lantmäteriet 401 eller 403 för en produkts tjänst säger Floden vilken produkt det
gäller (namn ur registret, samling och vad Floden använder den till), att den kan saknas eller inte vara aktiv ännu, att
en beställning kan ta tid, och (om `oauth2` är valt) att Geotorgets inloggning kanske är det rätta. Lantmäteriet svarar
likadant vid fel uppgifter, fel inloggningssätt och obeställd produkt, så Floden kan inte skilja dem; det står i texten.
Nekas själva tokenanropet (API-portalen, `oauth2`) sägs att nyckeln eller inloggningssättet inte godtogs, inte att en
produkt saknas.

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
demokvarteret (om `demoprojekt_roll` inte är `av`, med den rollen) och projekt där kontot har en effektiv roll (uttrycklig, eller ur organisationsrollen, avsnitt 5). Projekt ur
registret har ännu inga beräkningar (`finns: false`) och en enkel projektsida utan siffror.

**Körningar.** Med inloggning på gäller en projektregel på `/api/korningar/<id>/*` (status, resultat, `marksiffror`, `avlasning`, lager, filer, tabeller) och i MCP-verktygen som tar ett körnings-id (`korning_id`, `korning_ids`, `korningar`): kontot ska ha minst rollen `lasare` i körningens projekt. Projektet är körningens `projekt_id` i `korning.json` (valfritt fält), och för demokörningarna som `demo.json` pekar ut demokvarteret, som följer `demoprojekt_roll`. K0-manifestet har ingen projektuppgift, och registrets projekt har inga körningar än. En körning utan projekt (testsvit, äldre körningar) kan bara superadmin läsa; superadmin har ingen läsrätt i ett projekts körningar utan egen projektroll. Saknad behörighet ger **404** (samma svar som för ett id som inte finns, så att id:n inte läcker; `/api/projekt/<id>` ger 403). `GET /api/korningar` och MCP `korningar_lista` visar bara körningar kontot har rätt till. Med inloggning av gäller ingen av reglerna. Varje körning som startas får `projekt_id`: `POST /api/fragor/<ref>/kor` tar `projekt_id` i kroppen (valfritt; annars demokvarteret när frågan hör till demon), scenariokörningar i webbflödet och MCP-verktyget `kor` tar projektet de gäller. Med inloggning på krävs minst `utredare` i projektet för att starta (403 annars); en fråga som inte hör till något projekt kan bara superadmin starta (körningen blir då utan projekt). En pågående körning av samma fråga återanvänds bara inom samma projekt. `POST /api/kor`, `/api/risk` och `/api/rutt` (beräkningar på `omrade_id`, inget sparas) kräver minst `lasare` när `omrade_id` är ett projekt (demokvarteret eller ett projekt i kontoregistret); saknad roll ger **404** `Okänt område '<id>'` (inte 403, så att projekt-id:n inte går att pröva sig fram till). Ett `omrade_id` som inte är ett projekt (testsvitens områden) kräver bara inloggning. Med inloggning av gäller ingen projektkontroll. Projekt som skapas med `floden projekt skapa` registreras i kontoregistret med skaparen som `utredare` (registrets id står i `projekt.json` som `register_id`).

**Organisationen (organisationsroller).** Samma sida och samma `/konto/admin`; synlig för superadmin, organisationsadmin och ägare. Vad var och en får:

| Roll | Får | Får inte |
| --- | --- | --- |
| `medlem` | ingen administration, ingen projektdata av organisationen | |
| `ledare` | läsa (`lasare`) organisationens delade projekt | administrera |
| `organisationsadmin` | utreda i organisationens delade projekt; administrera medlemmar och inbjudningar även i privata (utan att se data); bjuda in till organisationen (roll medlem eller ledare) och dess projekt; ändra och ta bort medlem och ledare; ändra projektroller; skapa projekt i en befintlig geografi (webbens "Nytt projekt", `POST /api/projekt`, MCP `projekt_skapa` och serverkommandot `floden projekt skapa --konto`) | mappa geografier, ge superadmin, se andras nycklar, ändra serverinställningar, bjuda in eller ändra organisationsadmin och ägare, byta namn |
| `agare` | som organisationsadmin, och bjuda in med alla roller, utse och ta bort organisationsadmin (och ägare), byta organisationens namn | mappa geografier, ge superadmin, se andras nycklar, ändra serverinställningar; lämna organisationen utan ägare |
| superadmin (fält på kontot) | allt ovan i alla organisationer, mappa geografier, ge superadmin (bara serverkommando) | läsa projektdata utan egen roll |

Bara superadmin och organisationens ägare kan bjuda in med rollen `agare` eller `organisationsadmin`. Att mappa geografier kräver superadmin (`geografi.mappa`) oavsett organisationsroll (test: `tests/kommuner/test_skapa.py`). Den sista ägaren kan bara lämnas av superadmin. Att ta bort ett medlemskap tar inte bort uttryckliga projektroller, och en medlem som kommer in via IdP-tenant eller intygad domän blir `medlem` igen vid nästa inloggning.

| Anrop | Gör |
| --- | --- |
| `GET /konto/admin/organisationer` | organisationer som får administreras: `din_roll`, `far_byta_namn`, `roller`, `tillatna_roller` (rollerna utföraren får bjuda in med och ge) |
| `GET /konto/admin/organisationer/<id>` | `organisation`, `roller`, `medlemmar` (namn, e-post, roll, källa, spärrad) och `inbjudningar` (`oppen`, `anvand`, `utgangen`) |
| `PUT /konto/admin/organisationer/<id>` | `{"namn"}` byter namn (ägare) |
| `POST /konto/admin/organisationer/<id>/inbjudningar` | `{"epost", "roll"}`; 201. Tas emot vid inloggning med en verifierad adress, precis som projektinbjudningar. Ett nytt konto får komma in på en sådan inbjudan (`nya_konton = "inbjudan"`). En högre befintlig roll sänks aldrig |
| `PUT /konto/admin/organisationer/<id>/medlemmar/<konto>` | `{"roll"}` ändrar rollen |
| `DELETE /konto/admin/organisationer/<id>/medlemmar/<konto>` | tar bort medlemskapet |

**Delning av ett projekt.** `PUT /konto/projekt/<id>/delning` med `{"organisation_synlig": true|false}` (inloggad, CSRF; 403 för alla utom projektets utredare och skaparen, och för okänt projekt). `GET /konto/admin/projekt[/<id>]` har `organisation_synlig` och `kan_andra_delning`; `GET /api/projekt` och MCP `projekt_lista` har `organisation_synlig` (och `kalla`) för projekt ur registret. På sidan Administration finns kryssrutan "Synligt för organisationen <namn>" (avstängd för den som inte får ändra), och projektlistan på startsidan visar "delat med organisationen". Projekt som skapas med `floden projekt skapa --konto` är privata och skaparen är utredare och `skapad_av`.

**Projektinställningar (arkivera, radera, medlemmar; Joels fråga 2026-11-25).** Sidan `#/p/<projekt>/installningar` nås från länken "Inställningar" i sidhuvudet i varje projekt och från knappen "Projektinställningar" på projektlistans kartkort; Administration har kvar sin delningsruta. Projektroll nummer tre, **`agare`** (projektets ägare), finns sedan schemaversion 8 (`projektbehorighet` och `projektinbjudan` byggdes om med den i CHECK; ny kolumn `projekt.arkiverad`, tid eller NULL). Ägare är den med uttrycklig roll `agare` och skaparen så länge skaparen är utredare i projektet (så att äldre projekt har en ägare); `skapa_projekt_av` ger fortfarande skaparen `utredare`. `agare` ger samma dataåtkomst som `utredare`; organisationsroller ger aldrig `agare`. En projektägare kan inte lämna projektet utan ägare (den sista ägaren kan inte tas bort eller sänkas av en projektägare); organisationsadmin och superadmin kan, så att ett övergivet projekt kan tas över.

| Åtgärd | Får göra |
| --- | --- |
| Se sidan (medlemmar utan e-post) | alla med en effektiv roll i projektet (läsare och uppåt), och de som får ändra |
| Byta namn, ändra roll, ta bort medlem, bjuda in (läsare, utredare, ägare), återkalla inbjudan | projektets ägare, organisationsadmin och ägare i projektets organisation, superadmin |
| Synlighet för organisationen | oförändrat: projektets utredare, ägare och skaparen (inte organisationens admin eller superadmin; Joels beslut 2026-11-23) |
| Arkivera och återställa, radera permanent | projektets ägare och superadmin (inte en organisationsadmin som inte är ägare i projektet) |

En administratör kan fortfarande inte ge sig själv en roll i ett privat projekt; en ägare får ändra sin egen roll. Arkivering döljer bara: projektet försvinner ur `GET /api/projekt` och MCP `projekt_lista` (och visas med `GET /api/projekt?arkiverade=1`, länken "Visa arkiverade" i projektlistan), men roller, data och körningar rörs inte och adressen fungerar. Det finns ingen automatisk gallring.

**Radera.** `POST /api/projekt/<id>/radera` med `{"bekrafta_namn": "<projektets namn exakt>"}` (CSRF; 400 fel namn, 403 saknad rätt eller okänt projekt, 409 demokvarteret eller en pågående körning). Tas bort: projektmappen (`data/lokal/projekt/<mapp>`: ritade förslag, frågor, projektets egna rutor), projektets körningar (`<korningsmapp>/<id>` vars `korning.json` har projektets id), rapporter och beställningar som hör till projektet, och i registret posten, projektrollerna och inbjudningarna. Rörs inte: geografin, de delade cacherna (`data/cache/karna`), andra projekt och körningar utan projekt. Filerna tas bort före registerposten så att ett avbrutet försök kan göras om. Händelsen `projekt-raderat` har konto-id, projekt-id, organisations-id och antal roller och inbjudningar; aldrig namn eller e-post. Kod: `src/webb/projektinstallningar.py`, `Register.radera_projekt`. `PUT /api/projekt/<id>/namn` (`{"namn"}`) byter registrets namn och `namn` i `projekt.json` (projektmapp och id oförändrade; 409 om namnet finns i organisationen).

| Anrop (`/konto/projekt/<id>/...`) | Gör |
| --- | --- |
| `GET installningar` | namn, organisation, synlighet, arkiverat, `din_roll`, `far_andra`, `far_andra_delning`, `far_arkivera_radera`, `roller`, `medlemmar` (e-post och källa bara om `far_andra`), `inbjudningar` |
| `PUT arkiv` | `{"arkiverad": true\|false}` |
| `POST inbjudningar`, `DELETE inbjudningar/<id>` | `{"epost", "roll"}`; återkalla en öppen inbjudan |
| `PUT medlemmar/<konto>`, `DELETE medlemmar/<konto>` | `{"roll"}` ändrar (eller ger), tar bort |

**Skapa projekt i webben (K48).** `GET /api/geografier` listar de mappade geografierna (version, giltig kärna och yttre gräns i EPSG:3011, om kontot får skapa) och `POST /api/projekt` skapar projektet med samma kod som `floden projekt skapa` (`src/webb/projektskapa.py`, `kommuner.projekt.skapa_projekt`). **Rätt att skapa:** superadmin, eller organisationsadmin eller ägare i organisationen (`Register.organisationer_for_skapa`, `skapa_projekt_av`); `medlem` och `ledare` nekas (403), liksom den som inte är inloggad och alla när inloggning är av (då finns inga roller: använd kommandot). Superadmin som kan skapa i flera organisationer anger `organisation`. Skaparen blir `utredare` och `skapad_av`; projektet är privat om inte `organisation_synlig` är satt i anropet (samma kryssruta som på Administration). Ett namn som redan finns i organisationen ger 409 och återanvänder aldrig ett befintligt projekt, så att ingen kan ge sig själv roll i någons privata projekt. Skrivande anrop kräver CSRF och samma ursprung som övriga. Svaret har registrets projekt-id och `varningar` (delvis utanför giltig kärna visas som en tydlig varning i webben, helt utanför yttre gräns ger 400). Avgränsningen kan vara en riktig polygon (sparas i projektet; byggnader och celler inne i den räknas, rutnätet och bufferten är rektangeln runt den; en polygon som korsar sig själv ger 400). Loggen har bara konto-id, projekt-id och geografi, aldrig namn eller adress. MCP-verktyget `projekt_skapa` har samma behörighet och svar. Kontraktet står i `docs/DATAKONTRAKT.md` (K48).

**Skapa rapport i webben (K48).** `POST /api/projekt/<id>/rapporter` kräver `utredare` i projektet (`effektiv_projektroll`, så organisationsadmin och ägare i ett projekt som delats med organisationen räcker; en annan organisations admin får 403) och startar ett **jobb** (202). Jobbet gör samma som MCP `rapport_skapa`, därefter kontroll och export till PDF och Word, och rapporterar status (`ko`, `kor`, `klar`, `fel`) på `GET .../rapporter/jobb/<jobb-id>` (läsare). Körningen ska höra till projektet. Ämnet `ekologi` ger 409 med en förklaring tills rapportmallen R2 finns.

Händelser och loggar har bara konto-id, organisations-id, inbjudnings-id och roll. Webbklienten visar på sidan Administration organisationens medlemmar, roller, inbjudningsformulär (e-post och roll; bara de roller utföraren får ge) och knappar för att ändra och ta bort.

**Så gör Joel (superadmin) Samuel till organisationsadmin i en ny organisation "Floden".** Ett kommando per rad, på servern (`docs/DRIFT.md` visar `docker compose … exec floden` framför). `<epost>` är adressen Joel loggar in med respektive Samuels Google-adress; `<konto-id>` och `<org-id>` är id:n som föregående rad skrev.

```
PYTHONPATH=src python -m konto.admin konto-id <epost>
PYTHONPATH=src python -m konto.admin kontostatus <konto-id>
PYTHONPATH=src python -m konto.admin skapa-organisation "Floden" --agare <konto-id>
PYTHONPATH=src python -m konto.admin bjud-in-organisation <org-id> <epost> organisationsadmin
```

Rad 2 ska börja med `superadmin=ja`; gör den inte det, kör först `ge-superadmin <konto-id>`. Rad 3 skriver organisations-id. Rad 4 skriver inbjudningens id; Samuel får organisationsadmin när han loggar in med den adressen (ingen e-post skickas, han måste få veta det på annat sätt). Därefter ser han Administration och är utredare i de projekt i Floden-organisationen som är delade med den (kryssrutan "Synligt för organisationen Floden" på projektets administrationssida, som bara projektets utredare och skaparen kan sätta). Privata projekt ser han inte. Projekt i en annan organisation ligger kvar där och ger honom ingenting.

## 12. MCP-token (`/konto/mcp-token`, avsnittet "MCP-token" på sidan Nycklar)

Med en personlig token styr du Floden på servern från Claude Code (eller Codex, ett skript) via MCP på
`https://<värd>/mcp`, som ditt konto. Koden är `src/konto/mcptoken.py`, testerna `tests/konto/test_mcptoken.py` och
`tests/floden_mcp/test_mcp_http.py`.

- **Format och lagring.** `fl_mcp_` + 32 slumpbyte (`secrets`) som base64url (43 tecken). Databasen (`mcp_token`, schemaversion
  3) har bara **sha256 av hela token**, aldrig klartext. Token visas **en enda gång**, i svaret på skapandet; därefter syns bara
  namn, skapad, senast använd (upplösning en minut) och de fyra sista tecknen. En glömd token kan inte läsas ut: skapa en ny.
- **Livscykel.** Skapa (namn 1 till 60 tecken), använd, återkalla. Högst **5 aktiva** per konto (409 `for-manga` därefter).
  En återkallad token slutar gälla vid nästa anrop, även mitt i en MCP-session.
- **Spärrade konton nekas.** Kontrollen går mot kontots status vid varje anrop, så en spärr gäller direkt för redan skapade token,
  och ett spärrat konto kan inte skapa nya.
- **Bara `/mcp`.** Token godtas av `KontoSkydd` (`floden_mcp.http`) som alternativ till session-kakan: `Authorization: Bearer
  fl_mcp_...`, **utan CSRF** (en webbläsare skickar aldrig rubriken av sig själv). Webbens API, `/konto/nycklar`, `/konto/admin` och
  `/konto/mcp-token` tar aldrig emot en token (401): en token kan inte skapa, återkalla eller läsa token eller nycklar.
  Har ett anrop en bearer-rubrik avgör den ensam; en ogiltig token faller aldrig tillbaka på en kaka.
- **Fel.** Ogiltig, okänd, återkallad och spärrad ger samma 401 med `WWW-Authenticate: Bearer realm="floden", error="invalid_token"`
  (och `resource_metadata=...`, adressen till resursens metadata, avsnitt 13). Svaret nämner aldrig token eller orsak. Utan inloggning alls: 401 utan `error`.
- **Loggning.** Körloggen (`docs/MCP.md`) får `konto` (konto-id) och `token` (token-id) på varje rad; händelser och loggrader har
  bara id:n. Token, namn och e-post loggas aldrig (test).
- **MCP-sessioner binds till kontot.** Ett annat konto som känner till ett `Mcp-Session-Id` nekas (404).
- **Rättigheter i verktygen.** Se `docs/MCP.md`: bara projekt kontot har en roll i, och skrivande verktyg kräver `utredare`.

| Anrop | Gör |
| --- | --- |
| `GET /konto/mcp-token` | `{tokens: [{id, namn, skapad_datum, senast_anvand_datum, slutar_pa}], max, mcp_url}` |
| `POST /konto/mcp-token` | `{"namn"}` skapar; 201 med `token` (en gång) och uppgifterna ovan |
| `DELETE /konto/mcp-token/<id>` | återkallar kontots egen token (404 om den inte finns eller redan är återkallad) |

Samma krav som nyckelsidan: session (401), CSRF-kaka och `X-Floden-CSRF` för POST och DELETE samt rätt `Origin` (403), kropp
högst 4 kB. Med inloggning av finns ingen tokenfunktion (404).

## 13. OAuth 2.1 för MCP (Claude-appen) och sidan Nycklar > Anslutna appar

Claude-appen lägger till Floden som en egen koppling (adress `https://<värd>/mcp`) utan token: appen upptäcker att `/mcp` kräver
inloggning, hämtar metadata, registrerar sig, skickar dig till Floden, du loggar in med Google och godkänner, och appen får en kortlivad
token. Koden är `src/konto/oauth.py` (lagring, regler) och `src/konto/oauthapi.py` (HTTP), testerna `tests/konto/test_oauth.py`
och `tests/webb/test_asgi_mcp.py`. Följer MCP-specifikationens auktorisering (2025-11-25) och Claudes krav på anslutningar
(kontrollerat mot modelcontextprotocol.io och claude.com/docs/connectors/building/authentication).

| Ändpunkt | Gör |
| --- | --- |
| `GET /.well-known/oauth-protected-resource` (och `/mcp`-varianten) | Resursens metadata (RFC 9728): `resource` = `<offentlig_url>/mcp`, `authorization_servers` = `[<offentlig_url>]` |
| `GET /.well-known/oauth-authorization-server` | Serverns metadata (RFC 8414): endpoints, `S256`, `none`, `authorization_response_iss_parameter_supported` |
| `POST /oauth/register` | Dynamisk klientregistrering (RFC 7591), publika klienter (`none`). `client_id` är 32 hextecken |
| `GET /oauth/authorize` | Utan session: till Googles inloggning (`/konto/logga-in/<lev>?atervand=...`) och tillbaka hit. Med session: sidan "Ge <app> tillgång till Floden som <konto>?" |
| `POST /oauth/authorize` | Svaret från sidan. Kräver session, CSRF-fält mot kakan och rätt `Origin`. Omdirigerar med `code`, `state` och `iss` (RFC 9207) |
| `POST /oauth/token` | `authorization_code` (PKCE `S256`) och `refresh_token` (rotation). Formulärkropp (`application/x-www-form-urlencoded`) |
| `POST /oauth/revoke` | RFC 7009: återkallar behörigheten bakom en access- eller refresh-token |
| `GET /konto/oauth`, `DELETE /konto/oauth/<id>` | Kontots egna anslutna appar (namn, godkänd, senast använd) och att ta bort tillgången. Session och CSRF som nyckelsidan; en token kan inte röra sina egna anslutningar |

Regler:

- **Klienter.** `redirect_uris` (1 till 5): `https`, eller `http://localhost[:port]/...`. Aldrig fragment, användarinfo eller andra scheman;
  `127.0.0.1` tillåts inte (se STATUS, Öppet). Vid auktorisering och token-utbyte ska adressen vara **exakt** den registrerade, annars en felsida
  (aldrig en omdirigering, RFC 6749 4.1.2.1). Klientnamnet visas på godkännandesidan, maskat, med adressens värdnamn.
- **PKCE.** Kravet `code_challenge_method=S256` och en utmaning på 43 tecken. Fel verifierare ger `invalid_grant` och koden är förbrukad.
- **Koden** `fl_oac_...`: engångs, 60 s, bunden till klient, `redirect_uri` och utmaning. Används den två gånger återkallas allt den gav.
- **Access-token** `fl_oat_...` 1 timme. **Refresh-token** `fl_ort_...` 30 dagar, **roteras** vid varje användning; visas en redan använd
  refresh-token igen återkallas hela behörigheten (grant). Bara sha256 av kod, access och refresh sparas (tabellerna `oauth_klient`,
  `oauth_grant`, `oauth_kod`, `oauth_token`, schemaversion 4). En token visas bara i svaret på utbytet.
- **Resurs (RFC 8707).** `resource`, om den skickas, ska vara `<offentlig_url>/mcp`; annars `invalid_target`. Token gäller bara `/mcp`
  (`KontoSkydd` godtar `fl_oat_...` som bearer, utan CSRF), aldrig webbens API eller kontoändpunkterna.
- **Samma rättigheter som med session.** Tokenen är kontot: rollkontroll per projekt, skrivverktyg kräver `utredare`, öppenhetsspärren
  och körloggen (`konto`, `token` = grant-id) gäller som för en MCP-token. Spärrat konto, återkallad behörighet och återkallad klient
  nekas direkt, även för redan utfärdade token (kontrolleras vid varje anrop och vid varje förnyelse).
- **Hastighetsgräns** i minnet per klientadress: 10 registreringar per timme och 60 token- eller revoke-anrop per 10 minuter (429 med
  `Retry-After`). Klientadressen sätts av ASGI-lagret (`X-Floden-Klient-Ip`, en inkommande rubrik räknas aldrig); bakom Caddy används
  `X-Forwarded-For` som Caddy skriver. Gränsen nollställs vid omstart.
- **Loggning.** Konto-id, klient-id och grant-id; aldrig kod, token eller namn.
- Med inloggning av finns ingen OAuth (404) och `WWW-Authenticate` saknar `resource_metadata`.

## 9. Köra testerna

```
pip install -e ".[dev]"                  # innehåller PyJWT[crypto]
PYTHONPATH=src python -m pytest tests/konto -q
```

Beroende: `PyJWT[crypto]==2.15.0` (extra `konto`, också i `dev`). Det behövs för RS256-kontroll mot JWKS (`jwt.decode`, `PyJWK`) och drar in `cryptography`, som testerna också använder för att skapa den falska leverantörens RSA-nyckel. Motiv: att skriva egen RSA/JWS-kontroll vore den sämsta platsen att spara in; biblioteken är de som O-Tids `jose` motsvarar. Läget `inloggning = "av"` importerar inte PyJWT.
