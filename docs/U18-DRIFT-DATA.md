# Uppdrag 18 (P3 grund): Driftkö, SSRF-säker plattproxy och SMHI väderinläsare

Dokumentation för de tre fristående grundmodulerna i **Uppdrag 18** (`feat/v8-drift-u18`).
Samtliga moduler använder enbart Pythons standardbibliotek (samt redan låsta `numpy` för
SMHI-statistiken) och medför **0 nya tredjepartsberoenden**.

## 1. S5k pull-jobbkö, S10 organisationskvoter och kostnadsbok (`src/drift/jobbko.py`)

Implementerar den SQLite WAL-backade pull-kön och kostnadskontrollen beskriven i
`docs/ARKITEKTUR-DRIFT.md` och `PLAN.md` (`S5k`, `S10`).

### Arkitektur och säkerhetsgarantier

- **SQLite WAL + `BEGIN IMMEDIATE`:** databasfilen öppnas i `WAL`-läge med filrättigheter `0o600`
  och `PRAGMA foreign_keys = ON`. Varje skrivning och leasing körs i en atomisk `BEGIN IMMEDIATE`-
  transaktion så flera trådar eller arbetarprocesser aldrig kan dubbel-leasa ett jobb eller smita
  förbi en organisationskvot.
- **S10 fail-closed-kvoter (förval `0`):** en organisation utan tilldelad kvot har
  `max_aktiva_jobb = 0` och `manadsbudget_sek = 0.0`. Överskridanden avvisas alltid med
  `KvotOverskridenFel` (med `orsak` = `"max_aktiva_jobb"` eller `"manadsbudget_sek"`), aldrig
  tyst överskridande.
- **Exakt kostnadsbok i heltaliga mikro-SEK (`1e-6 SEK`):** `lagg_till` reserverar
  `uppskattad_kostnad_sek` mot månadens budget (`faktisk + reserverad + begärd <= budget`). När
  jobbet avslutas med `slutfor` (eller aliaset `slutför`) bokförs `faktisk_kostnad_sek` i den
  oföränderliga tabellen `kostnadspost` och reservationen släpps. `kostnadsbok_manad(org_id, ar_manad)`
  redovisar exakt ackumulerad kostnad, reserverad kostnad, antal jobb, kvarvarande budget och
  S5k-larmnivå (`ok`, `50%`, `80%`, `100%`).
- **Leasing, heartbeat och kraschåterhämtning:**
  - `hamta_nasta(arbetare_id, lease_sekunder=300, typer=...)` väljer högsta `prioritet DESC`,
    därefter FIFO (`skapad_utc ASC`).
  - `fornya_lease(jobb_id, arbetare_id, lease_sekunder=300)` förlänger aktiv lease och avvisar
    fel arbetare eller utgången lease med `LeaseOgiltigFel`.
  - `atertag_utgangna_leasar()` återköar utgångna jobb när `forsok < max_forsok` och markerar dem
    som `misslyckad` när retry-taket `max_forsok` nåtts.
- **0 PII (`AGENTS.md`):** endast anonyma ID:n (`org_id`, `konto_id`, `projekt_id`, `arbetare_id`);
  e-postadresser i ID-fält samt personuppgiftsnycklar i JSON-nyttolast/metadata avvisas med
  `PiiForbjudetFel`.

## 2. SSRF-säker WMTS/XYZ-bakgrundsplattproxy (`src/webb/kartproxy_xyz.py`)

Följer samma etablerade DNS-pinning-mönster som `src/webb/kartverktyg.py` (`PinnadHTTPSConnection`).

### Säkerhetskontroller

1. **Koordinatvalidering:** `validera_plattkoordinater(z, x, y)` kräver heltal (ej `bool`),
   `0 <= z <= 20` och `0 <= x, y < 2**z`.
2. **Strikt käll- och värd-allowlist (`PlattKalla`):** enbart `https://`, konfigurerade
   DNS-värdnamn (inga råa IP-literaler), inga användaruppgifter i URL (`user:pass@`), inga
   styrtecken och enbart kända platshållare (`{z}`, `{x}`, `{y}`, `{TileMatrix}`, `{TileCol}`,
   `{TileRow}`, `{s}`).
3. **Fullständig IP- och DNS-validering (`ar_publik_ip`, `validera_och_los_upp_dns`):**
   - DNS löses **en enda gång** och **samtliga** returnerade IP-adresser måste vara strikt publika
     (blandat svar publik + privat blockeras helt).
   - Blockerar IPv4 `0.0.0.0/8`, `10.0.0.0/8`, `100.64.0.0/10`, `127.0.0.0/8`, `169.254.0.0/16`,
     `172.16.0.0/12`, `192.168.0.0/16`, `224.0.0.0/4`, `240.0.0.0/4` m.fl., samt IPv6 `::/128`,
     `::1/128`, `fc00::/7`, `fe80::/10`, `ff00::/8`, NAT64 (`64:ff9b::/96`), 6to4 (`2002::/16`),
     Teredo (`2001::/32`) och **samtliga** IPv4-mappade IPv6-adresser (`::ffff:0:0/96`).
4. **DNS-pinning + SNI:** `PinnadHTTPSConnection` öppnar TCP direkt mot den validerade IP-adressen
   och sätter ursprungligt värdnamn i TLS SNI och `Host`-header, vilket förhindrar DNS-rebinding.
5. **Omdirigeringsskydd:** varje 3xx-hopp re-valideras mot `https://`, källans värd-allowlist,
   port-allowlist, fullständig DNS-kontroll och `max_omdirigeringar` (kan sättas till `0`).
6. **Svarsskydd & atomisk TTL-diskcache:** storlekstak (`max_bytes`, förval 2 MiB) kontrolleras på
   både `Content-Length` och strömmad kropp; `Content-Type` begränsas till `image/png`,
   `image/jpeg` och `image/webp`; diskcachen skriver SHA-256-nycklade `.tile` + `.meta.json`
   atomiskt via `tempfile` + `os.fsync` + `os.replace` och verifierar både TTL och SHA-256 vid
   läsning.

## 3. Deterministisk SMHI öppna väderinläsare (`src/geografi/smhi_vader.py`)

Läser lokala SMHI Open Data `metobs`-filer eller textsträngar i officiellt CSV- och JSON-format
utan live-nätverksanrop och utan påhittade mätvärden.

- **Parametrar:** lufttemperatur (`1`, °C), vindriktning (`3`, grader), vindhastighet (`4`, m/s),
  relativ luftfuktighet (`6`, %) och global irradians (`11`, W/m²).
- **Explicit kvalitetsfiltrering:** förval godkänner flaggorna `G` och `Y` (strikt läge `G` finns
  via `STRIKT_GODKANDA_KVALITETER`). Ogiltiga flaggor (`O`, `R` m.fl.), saknade värden och
  intervallfel räknas alltid i `bortfiltrerade` och `bortfiltrerade_per_orsak`.
- **16-sektors vindros (`berakna_vindros_16`):** 22,5°-sektorer centrerade på `0°, 22,5°, ...,
  337,5°` (`N`..`NNV`). Returnerar `andel_pct`, `andel_av_vind_pct`, `medelvind_m_s`,
  `p95_vind_m_s` och `andel_svag_vind_pct` (där `sum(andel_pct) + andel_svag_vind_pct == 100 %`).
  `bygg_vindkomfort_underlag` bygger fält direkt kompatibla med `luft.vindbibliotek.Vaderserie`
  och `luft.vindkomfort`.
- **Sommar-/värmeböljepercentiler för UTCI (`berakna_varmebolja_utci`):** beräknar `p95` och `p99`
  lufttemperatur (°C) över sommarurvalet (förval JJA, månader 6–8) samt medianvärdet av samtida
  relativ luftfuktighet (%), 10 m-vind (m/s) och eventuell globalstrålning (W/m²) för timmar med
  `T >= T_percentil`. Saknade samtida mätningar returneras som `None` (aldrig påhittade standardtal).

## Verifiering

Kör kontroller från worktree-roten:

```bash
export PYTHONPATH=$PWD/src:$PWD
ruff check src tests testsvit
mypy --platform linux src
pytest -n 2 --dist loadfile \
  tests/drift/ \
  tests/webb/test_kartproxy_xyz.py \
  tests/geografi/test_smhi_vader.py \
  tests/gemensamt/test_paketgranser.py
```
