# Data

## Källor

| Data | Källa | Åtkomst | Känslighet | Används i |
| --- | --- | --- | --- | --- |
| Laser (punktmoln, 1–2 p/m²) | Lantmäteriet, Laserdata Nedladdning skog | STAC (`stac-hojd`), Geotorget-konto; CC0 med användarvillkor, ange ©Lantmäteriet | Öppen (villkoren nämner personuppgifter i punktmoln) | T01 |
| Markhöjdmodell 1 m | Lantmäteriet | STAC | Öppen | Jämförelse, reserv |
| Ortofoto | Lantmäteriet | STAC / API | Öppen med villkor | T01 (förändringar), T06 |
| Tätare laser | Stockholms stad | Fråga stadsmätningen | Okänd – behandla som intern | T01b |
| Baskarta (DWG) | Stockholms stad | Intern | Intern; innehåller inga personuppgifter | T01, T01b |
| Vägnät | Trafikverket NVDB | Öppet API med nyckel | Öppen | T07 |
| Jordarter 1:25 000–1:100 000 | SGU | Öppna data (OGC API) | Öppen | T06 |
| Grundvattennivåer | SGU, stadens observationsrör | Öppet / fråga staden | Öppen / intern | T06 |
| Markfuktighet (DTW) | SLU / Skogsstyrelsen | Öppen | Öppen | T06 |
| Ledningsnät, brunnar, trummor | Stockholm Vatten och Avfall | Avtal | **Kan vara skyddsklassat** | T01, T06, M7 |
| Nederbörd, radar | SMHI | Öppna data | Öppen | T03, T07 |
| IDF-tabell Stockholmsregn 2024 (Tabell 7) | Stockholms stad, trafikkontoret (Sweco, DHI, SMHI) | Offentlig rapport, transkriberad till `data/regn/` med sidokort och sha256 | Öppen | R1 (`floden regn/kor/risk --idf stockholmsregn-2024`) |
| Skyfallskartering (beta) | SMHI | Webbtjänst | Öppen | T09b |
| Testfall för 2D-modeller | Environment Agency (UK) | Nedladdning | Öppen | T09c |
| Räddningsstationer, särskilda objekt | Räddningstjänsten, staden | Fråga | Inga personuppgifter | T07 |

## Regler

- Öppen data laddas ner till `data/cache/` (inte i git) och cachas med kontrollsumma.
- Intern data ligger i en lokal mapp som anges i `config.local.toml`. Den checkas aldrig in och skickas aldrig till en extern AI.
- Ledningsdata används bara lokalt eller i stadens miljö. Kod för ledningsnät testas med syntetiska nät.
- Ange alltid källan i rapporter och kartor (©Lantmäteriet, SGU, SMHI, NVDB osv.).

## Markslag ur baskarta, belagd yta och ortofoto

- **Lagerprofil** (`data/lagerprofiler/*.toml`, `markmodell.lagerprofil`): mappar baskartans lager till roller och markslag.
  Repots standardprofil `stockholm_baskarta.toml` (version 2) är **härledd ur O-Tid, prövad på sprintkartan
  Älvsjö–Solberga; ska bekräftas mot full lagerlista**. Grammatik och nyckelord är lyfta från O-Tid (MIT,
  joelberring/otid, `packages/map-revision/src/stockholm-layers.ts` @ 4a446af). Lager som följer Stockholms grammatik
  (grupp + objekttyp + geometri `gy` yta/`gl` linje/`gb` blandat/`glh` höjdkurva/`s` symbol/`t` text + `B` + status
  Befintlig/Odefinierad/Skymd, avkortad; parkkartans `Park` + grupp + objekttyp) tolkas automatiskt per lager
  (`markmodell.stockholm_lager`, som O-Tids `isStockholmLayer`); övriga lager följer profilens prefixregler. Reglerna är
  reguljära uttryck i profilens `[stockholm]`-tabell:
  - ytor (`gy`) → markslag: byggnad (blir husliv/byggnadsregister, inte markslagsyta), skärmtak 2, belaggning/asfalt/plattor/
    gatsten/vagutbredning/infart/refug/parkvag/trappa 1, grus 7, grasyta/oppennaturmark/slatter/buskage 4,
    naturmarktradbevuxen 5, bassang/plaskdamm 8;
  - linjer (`gl`) → brytlinjer för skyfall: kantsten/vagavgransning (infart, refug) kantsten 0,12 m, stodmur 1,5 m,
    stenmur/bruksmur/kallmur/bullerskydd mur 1,0 m, staket 0,15 m, slänt och dike/bäck bankkant 0,30 m, gräskant bankkant
    0,05 m, trumma som öppning. O-Tid hoppar över slänt och gräskant; för skyfall är de med;
  - överhoppat: skymt (`Skymd`), text, höjdkurvor, symboler, fastighetsgränser/registerenhet, snöröjning, byggnadens linje
    (`gl`, ytan används) och ytornas kantlinjer, altan/övrig byggnadsanläggning, broar och träd;
  - status Odefinierad tas med men flaggas: ytor får bit 16 i `markslag_kalla.tif`, linjer redovisas i varningarna.
  Lager som följer grammatiken men saknar regel för objekttypen ignoreras och listas i varningarna. Rader märkta "att
  bekräfta" i profilen är val som inte är kontrollerade mot staden (infartens sänkta kant, dikets/bäckens linjeläge,
  buskage som gräs, gräskantens höjd). Egen profil för andra namn: mall `data/lagerprofiler/lokal_profil.toml.example`,
  utpekad i `config.local.toml` med `[markmodell] lagerprofil = "..."` (relativ sökväg från `[lokal_data] baskarta_mapp`)
  eller `floden markmodell --lagerprofil`. Profilens sha256 skrivs i `recept.json` (roll `lagerprofil`).
- **Prioritet** när källorna möts i `markslag.tif`: baskartans ytor > belagd yta ur laserintensitet > ortofototolkning >
  standardklass; avgörande källa och motsägelseflagga per cell i `markslag_kalla.tif` (K1 v2-tillägget i
  `DATAKONTRAKT.md`). Beslutet är ett förslag under Öppna beslut i `BESLUT.md`.
- Belagd yta kräver ett punktmoln med intensitet: `floden markmodell --laser-las FIL [--laser-epsg 3006] --belagd-yta`
  (LAS direkt, LAZ via `tools/lasclip`; `--laser-csv` har ingen intensitet) eller biblioteket
  (`markmodell.markslagskallor.belagd_mask_for_ruta`). Ingen riktig DWG, dwgread-körning eller laser-LAZ har körts här;
  allt är testat på syntetiska ytor och namn.

## Hämta öppna data från Lantmäteriet

`floden data lantmateriet` hämtar laser, ortofoto och byggnader för ett provområde till en **lokal datamapp**
(utanför repot, aldrig uppladdad någonstans) och skriver ett manifest med sha256. `floden omrade bygg` bygger
sedan K1 v2-rutor ur det. Paketet är `src/lantmateriet/`. Protokoll och logik är porterade från O-Tid (MIT,
joelberring/otid @ 4a446af: `packages/application/src/map-project-lantmateriet.ts`, `map-project-laser.ts`).

**Status, ärligt:** all kod är testad mot en **falsk** Lantmäteriet-server (`tests/lantmateriet/`), inget mot den
riktiga tjänsten. O-Tid markerar själv adresserna som "provas, inte provade mot riktiga Lantmäteriet". Första
körningen på din dator är därför första riktiga provet; `--lista-samlingar` visar vad tjänsten faktiskt har.

### Nycklar

Ur miljön (vinner) eller `[lantmateriet]` i `config.local.toml` (ignorerad av git, se `config.local.toml.example`):

| Miljövariabel | `config.local.toml` | Betydelse |
| --- | --- | --- |
| `LANTMATERIET_KLIENT_ID` | `klient_id` | API-portalens nyckel (OAuth2) eller Geotorgets användarnamn (`basic`) |
| `LANTMATERIET_KLIENT_HEMLIGHET` | `klient_hemlighet` | hemligheten respektive lösenordet |
| `LANTMATERIET_METOD` | `metod` | `oauth2` (standard) eller `basic` (Geotorgets användarnamn och lösenord) |

Nycklarna skrivs aldrig i loggar, manifest eller felmeddelanden (`Nycklar` har en `repr` som döljer dem; testat).
O-Tid sparar kontots nycklar krypterade och **visar dem aldrig igen** (bara fyra tecken): har du lagt in dem under
Mitt konto i O-Tid kan de inte läsas ut därifrån. Hämta dem på nytt hos Lantmäteriet (API-portalen
`apimanager.lantmateriet.se` för nyckel och hemlighet, eller använd Geotorgets inloggning med `basic`).
Produkterna måste vara **beställda på Geotorget** för kontot. Floden använder de gratis: STAC Höjd med **Laserdata
Nedladdning, skog** och Ortofoto Nedladdning (gratis, men av som förval för Göteborg). Lantmäteriets byggnadsprodukter
är avstängda som förval (kommunens egna öppna data används: se `docs/DRIFT.md`, "Göteborg"); att de skulle kosta
stämmer inte för alla: **Byggnad Nedladdning, vektor** är märkt avgiftsfri på Geotorget (kontrollerat 2026-11-24).
Se `docs/GEOTORGET.md` för alla produkter och avgifter; Joel har beslutat att de avgiftsfria får användas. Geotorgets
användarnamn och lösenord kräver **Metod = basic** på sidan Nycklar; förvalet oauth2 gäller API-portalens nyckel och
hemlighet. Lantmäteriet svarar 401/403 både vid fel nyckel eller metod och vid obeställd produkt; det går inte att skilja
dem, och felet säger det och nämner metoden.
Adresserna kan ändras med `LANTMATERIET_TOKEN_URL`, `LANTMATERIET_STAC_HOJD_URL`, `LANTMATERIET_STAC_BILD_URL` och
`LANTMATERIET_STAC_VEKTOR_URL` (som O-Tids `OTID_LM_*`).

### Vad som finns och vad som hämtas

| Produkt | STAC-tjänst (standardadress) | Samling | Fil | Vad som hämtas |
| --- | --- | --- | --- | --- |
| Laserdata Nedladdning, skog | `https://api.lantmateriet.se/stac-hojd/v1` | den vars id eller titel nämner "laser" och "skog" (O-Tids regel; `--laser-samling` tvingar) | LAZ, 2,5 x 2,5 km-rutor, EPSG:3006 | nyaste skanningen per ruta |
| Ortofoto Nedladdning | `.../stac-bild/v1` | id eller titel med "ortofoto" utan "historisk" (O-Tid söker utan samling; `--orto-samling` tvingar) | GeoTIFF (COG) | nyaste först tills området är täckt |
| Byggnad (vektor) | `.../stac-vektor/v1` | "byggnad" utan "inspire", "visning", "direkt" (O-Tids regel) | zip med GeoPackage per kommun | högst tre zip-filer, klipps till området |

- **Sökning:** `POST <tjänst>/search` med `{"bbox": [väst, syd, öst, nord], "collections": [id], "limit": 200}`.
  **bbox är WGS 84 (grader, lon först)**; området (EPSG:3011) räknas om med pyproj och kanterna tätas upp så att
  rutan inte blir för liten (testat: E 150 000 ligger exakt på 18,0 °E, och mot vägen via EPSG:3006 inom 2e-7 grader).
  Sidor följs via STAC:s `links` med `rel = next` (GET, eller POST med `body` och `merge`); en sida som upprepas
  stoppar loopen. O-Tid tar en sida om 200, Lantmäteriets STAC-webbläsare ställs på 1000 per sida.
- **Inloggning:** OAuth2 client credentials: `POST https://apimanager.lantmateriet.se/oauth2/token` med Basic
  (nyckel:hemlighet) och `grant_type=client_credentials`; token cachas till `expires_in` minus 30 s och byts vid 401.
- **Fel:** 401/403 ger `NyckelFel` (utgångskod 3) med texten ovan; 429 och 502-504 väntas ut (`Retry-After`,
  högst 120 s per väntan, fyra omförsök) och ger sedan `StrypningsFel`; nätfel försöks om; allt annat ger ett läsbart fel (kod 2).
- **Nedladdning:** via `<fil>.part` med `Range`-återupptagning, omdirigeringar för hand (högst 4). Nycklar skickas
  bara till `*.lantmateriet.se` (eller den inställda värden i tester); en omdirigering till en annan https-värd följs
  utan nycklar, andra adresser nekas.
- **Idempotent:** en fil hoppas över om url, storlek och ändringstid är desamma som senast (annars räknas sha256 om och
  jämförs). Samma körning två gånger hämtar inget och ger ett manifest som är byte för byte detsamma.
- **Manifestet** `lantmateriet_manifest.json` (`LM-hamtning/v1`) har källa, villkor, områdets sha256 och bbox,
  valda samlingar och per fil roll, url (utan fråga), storlek, sha256, tidpunkt, EPSG och bbox. Det har inga tider
  och inga nycklar, så dess sha256 är stabil. Tillstånd (ändringstider) ligger i `.lantmateriet_tillstand.json`.
  Receptets `kallor` (K1) får rollerna `lantmateriet_manifest` (manifestets sha256), `laser_laz`, `ortofoto`, `omrade`
  och `omrade_bygg_parametrar`; additivt, K1/v2 förblir K1/v2.

### Laserns täthet och klasser

Den täta laserdata som Joel helst vill ha finns **inte** i Lantmäteriets öppna STAC: produkten (Laserdata
Nedladdning, skog) har enligt Lantmäteriets produktsida och produktbeskrivning **1-2 punkter per m2** (skanningarna
sedan 2009; tredje varvet från 2024 i LAS 1.4), LAZ i EPSG:3006. Jag kunde inte lista tjänstens samlingar utan
nycklar, så om `stac-hojd` har fler laserprodukter visar `floden data lantmateriet --lista-samlingar` det och
`--laser-samling` väljer. Standardvalet är O-Tids: samlingen med "laser" och "skog". Stadens tätare laser är inte
öppen och ligger kvar i den lokala datamappen (`[lokal_data] tat_laser_mapp`, se tabellen ovan).

Klasserna i produkten är 1 (oklassad), 2 (mark), 7 (lågt brus), 9 (vatten), 17 (bro, bara nivå 3) och 18 (högt brus):
**ingen klass 6 (byggnad)**. K1 v2:s byggnadshöjd ur laser (`hojdkalla = laser_klass6`) skulle därför aldrig
slå in. `floden omrade bygg` räknar därför oklassade punkter (1) inne i en byggnadspolygon som tak (klass 6) i
höjdsättningen (90:e percentilen, minst 0,5 m över marken, minst 5 punkter), annars gäller källans höjd,
våningar eller standardhöjden 10 m. Träd som hänger över ett tak kan ge för höga hus; det står i varningarna
och i `omrade_bygg.json`. `--utan-tak-ur-laser` stänger av det. Byggnadsytorna kommer från vektorprodukten eller
`--byggnader FIL`, inte från laser (beslut 2026-11-20).

Markhöjdmodell 1 m: Lantmäteriets produktsida beskriver den som nedladdning via WCS/Atom, inte STAC; O-Tids falska
server har en samling "markhojdmodell" i `stac-hojd`, men det är en påhittad testserver. Den hämtas inte här.

### Licens och villkor

Lantmäteriets produktsida för Laserdata Nedladdning, skog anger **CC0** (öppna data); produktbeskrivningen säger
"öppna data" utan licensnamn, och O-Tids dokumentation anger CC BY 4.0. **Kontrollera villkoren** på
lantmateriet.se innan data eller resultat sprids, och ange alltid **©Lantmäteriet** (manifestet och kommandot gör det).
Villkoren nämner att punktmoln kan innehålla personuppgifter: därför körs allt lokalt och **inget laddas upp till en
AI-tjänst**. Ortofoto och Byggnad: villkoren är inte kontrollerade här, skriv "kontrollera villkoren".

### Byggnader

`--byggnader` hämtar zip-filer ur `stac-vektor` och packar upp GeoPackage; bara polygonlager med "byggnad" i namnet
används (`--byggnads-lager` väljer när flera finns; lagernamnen i riktig data är okända här). Byggnaderna klipps
till området (+ marginal) i EPSG:3011 till `byggnader/byggnader_klippt.gpkg`. Utan hämtade byggnader: ange en
lokal fil till bygget (`floden omrade bygg --byggnader FIL`, t.ex. OSM-byggnader från QGIS) eller så byggs marken
utan hus (varning). Fältnamnen som läses är `markmodell.byggnadskallor.FaltMappning`; Lantmäteriets egna fältnamn
för höjd och våningar är inte kontrollerade.

### Bygget av rutor

`floden omrade bygg --omrade prov1.gpkg --data <datamapp> --ut <rutmapp>` delar området på ett fast nät
(`--ruta-storlek-m`, standard 500; hörn i hela multiplar, så `mm_<E>_<N>` är stabilt), klipper laserpunkter per ruta
med 25 m marginal (LAZ via `tools/lasclip`: `cargo build --release --locked --manifest-path tools/lasclip/Cargo.toml`
eller `FLODEN_LASCLIP`; saknas den ger kommandot ett fel med byggkommandot), och kör markmodellens egen kedja
(`bygg_markmodell_ruta`). Standard 1 m-celler (`--upplosning-m`), eftersom 1-2 punkter per m2 inte bär finare. Ortofoto
från manifestet används för markslag (`--utan-orto` stänger av), `--belagd-yta` räknar belagd yta ur intensiteten.
Redan byggda rutor hoppas över när manifestet och parametrarna är desamma (`--tvinga` bygger om). `vatten.statisk_fyllning`
tar bara **en** K1-ruta: bygg då området en gång till med `--en-ruta` till en egen mapp.
Rutorna vid områdets kant saknar grannar, så dagsljuslösaren varnar för trunkerade strålar; rita provområdet med
marginal runt det du vill studera och avgränsa frågan med `fokus`.

## Koordinatsystem

- Intern beräkning: SWEREF 99 18 00 (EPSG:3011), höjder RH 2000.
- Lantmäteriets laser och raster levereras i SWEREF 99 TM (EPSG:3006): transformera vid inläsning.
- Stockholms baskarta: SWEREF 99 18 00 (kontrollera per fil; östliga koordinater kring 150 000).

## Regn: Stockholmsregn 2024

`data/regn/stockholmsregn_2024_tabell7_intensitet_l_s_ha.csv` (l/(s·ha)) och `..._volym_mm.csv` (mm) är Tabell 7 i
rapporten Stockholmsregn 2024 (kap. 4.1, s. 30): blockregnsintensitet för 5–1440 min och 1–1000 års återkomsttid,
**utan klimatfaktor**. Filerna är oförändrade transkriptioner; `data/regn/stockholmsregn_2024.toml` har källa, URL,
fotnot ("varaktigheterna 5–20 min är utvärderade exklusive 15-minuter Geonor-data"), transkriptionskontroll och
sha256, som kontrolleras vid varje inläsning (ändrad tabell ger `RegnFel`).

- **Enheter:** biblioteket räknar i mm/h. `IdfKalla.las()` omvandlar vid inläsning med 1 l/(s·ha) = 0,36 mm/h.
  CSV-filerna ändras inte. Volymen i mm är kontroll: V = i·D·0,006 inom avrundning (i l/(s·ha), D i min).
- **Interpolation:** som för alla `IdfTabell`: bilinjär i (ln D, ln T) till ln i; stödpunkterna återges exakt och
  ingen extrapolering görs utanför 5–1440 min och 1–1000 år.
- **Klimatfaktor:** en uttrycklig parameter. Standard 1,4 (rapporten kap. 6.3, multipliceras med hela kurvan, kap.
  4.9); 1,25 var stadens faktor när rapporten skrevs. Faktor och källa skrivs i K4-serien (`statistikkalla`,
  `klimatfaktor`) och i K0-manifestet (`idf_kalla`, `idf_klimatfaktor`, `idf_sha256`; tabellfilerna som indata).
- **Tabell 6 och 7:** rapportens Tabell 6 (kap. 3.8) ger andra 60-minutersvärden (100 år: 117,9 ± 9,4 mot 127,8
  l/(s·ha)); rapporten säger att metoderna inte helt överensstämmer. Tabell 7 används (se `docs/BESLUT.md`).

```
floden regn --idf stockholmsregn-2024 --aterkomsttid-ar 100                    # CDS 6 h, kf 1,4
floden regn --idf stockholmsregn-2024 --aterkomsttid-ar 100 --klimatfaktor 1.25 --typ block --varaktighet-min 60
floden kor --k1 A3 --idf stockholmsregn-2024 --aterkomsttid-ar 100 --regntyp block --varaktighet-min 60
floden risk --omrade A3 --idf stockholmsregn-2024 --max-aterkomsttid-ar 500
```

`--idf` kan inte kombineras med `--id`/`--regn-id` (förvalen bygger på Dahlström 2010). I Python:
`skapa_regn_fran_idf_kalla("stockholmsregn-2024", 100, klimatfaktor=1.4)` eller `las_stockholmsregn_2024()`.
