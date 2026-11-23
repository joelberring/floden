# Datakontrakt

Formaten mellan modulerna. Varje kontrakt har en version. En ändring som bryter mot äldre data höjer versionen
och skrivs in i `BESLUT.md`.

Gemensamt för alla:
- Koordinatsystem SWEREF 99 18 00 (EPSG:3011), höjder RH 2000.
- Raster som Cloud Optimized GeoTIFF (COG). Vektor som GeoPackage. Tabeller som Parquet eller CSV.
- Kontrollsumma = sha256 av filens innehåll.

## K0 – Körmanifest `run.json` (v1)

Skrivs av varje körning, oavsett modul.

```json
{
  "kontrakt": "K0/v1",
  "modul": "dynamik",
  "kodversion": "git-sha",
  "start": "2026-11-20T17:00:00+01:00",
  "indata": [{"roll": "sankgraf", "fil": "…", "sha256": "…"}],
  "parametrar": {"…": "…"},
  "utdata": [{"roll": "max_djup", "fil": "…", "sha256": "…"}],
  "matvarden": {"volymfel_rel": 0.0004, "kortid_s": 2.1},
  "varningar": ["12 celler utan markpunkter inom 3 m"]
}
```

## K1 – Markmodell-ruta (v2)

En ruta är 500 × 500 m med 25 m överlapp. Namn: `mm_<E>_<N>` (sydvästra hörnet i meter, utan överlapp).

| Fil | Typ | Innehåll |
| --- | --- | --- |
| `mark.tif` | COG float32, 0,25 m | Markhöjd inklusive byggnader som hinder (byggnadsceller får byggnadens takhöjd eller en fast hinderhöjd) |
| `osakerhet.tif` | COG float32, 0,25 m | Uppskattad standardavvikelse i höjd (m) |
| `markslag.tif` | COG uint8, 0,25 m | Kod enligt tabellen nedan |
| `brytlinjer.gpkg` | lager `brytlinjer` | Linjer med `typ` (kantsten, mur, stödmur, staket, bankkant) och `hojd_m` |
| `oppningar.gpkg` | lager `oppningar` | Trummor, portar och passager: geometri, `typ`, `bredd_m`, `botten_rh2000`, `kalla` |
| `recept.json` | JSON | Källor med sha256, inställningar, kodversion. Receptets egen sha256 är rutans identitet |

Markslagskoder: 1 hårdgjort, 2 tak, 3 grönt tak, 4 gräs, 5 skog, 6 berg i dagen, 7 grus, 8 vatten, 9 byggnad, 0 okänt.

### K1 v2 – gemensam geometri (tillägg)

v2 lägger till filer; v1-filerna är oförändrade, så en v1-ruta läses fortfarande (`karna.geometri` ger tydligt
fel i stället för gissning om en v1-ruta har byggnadsceller, se nedan). En ruta är v2 om `byggnader.gpkg` finns
(då ska `terrang.tif` finnas). `recept.json` har `kontrakt = "K1/v2"` och `installningar.k1_version = 2`.

| Fil | Typ | Innehåll |
| --- | --- | --- |
| `terrang.tif` | COG float32, 0,25 m, samma utsträckning som `mark.tif` | Marken **utan** byggnader och öppningar, men med brytlinjer (kantstenar): `z_med_bryt` i markmodellens bygge |
| `byggnader.gpkg` | lager `byggnader`, Polygon (yttre ring moturs, hål medurs) | Byggnadsregister, se kolumner nedan |
| `vegetation.tif` | COG float32, **1 m** (heltalsmultipel av markrutnätet, samma utsträckning), valfri | Vegetationshöjd över mark (m); 0 = ingen vegetation; ≥ 0,5 m |

`byggnader.gpkg`-kolumner: `byggnad_id` (text, unik, `b_` + 16 hex), `takhojd_rh2000`, `markhojd_rh2000`,
`hojd_m` (= tak − mark, kontrolleras), `takform` (valfri), `andamal` (valfri), `vaningar` (valfri heltal), `kalla`
(text), `osakerhet_hojd_m` (valfri, 1 sigma), `hojdkalla` (`kalla_tak`, `laser_klass6`, `kalla_hojd`,
`kalla_vaningar`, `standard`, `andring`). **Valfri kolumn (tillägg 2026-11-22, ingen versionshöjning):**
`takfall_azimut_grader` (REAL, grader medurs från norr mot den lägre sidan) för pulpettak; läses av takvattnet
(`sankgraf.takvatten.las_takpolygoner`) om den finns, skrivs inte av markmodellen. `takform` tolkas av takvattnet:
`platt`/`flat`, `pulpet`/`skillion`, `sadel`/`sadeltak`/`gabled`/`brutet`/`mansard`/`gambrel`; annat = okänd
(brutet tak) och flaggas.

- **`byggnad_id`** är deterministiskt: sha256 av `kalla` + källans eget id om källan har ett, annars av den
  normaliserade geometrin (1 cm, kanonisk startpunkt och orientering). Hela polygonen (inte den utklippta) skrivs i
  varje ruta den berör, så en byggnad som korsar rutgränsen har samma id i alla rutor. Attribut som härleds ur
  rutans egen terräng (`markhojd_rh2000`) kan skilja sig lite mellan rutor; id och polygon gör det inte.
- **Höjdsättning** (`markmodell.byggnadsregister.hojdsatt_byggnader`): källans absoluta tak > laser klass 6
  (percentil 90 av punkterna i polygonen, minst 5; `osakerhet_hojd_m` = standardavvikelsen, minst 0,15 m) > källans
  `hojd_m` > våningar × 3 m > standard 10 m (osäkerhet 5 m, flaggas i `run.json` varningar). Källans
  `Byggnad.hinderhojd_m` = 10 (standardvärdet) räknas som "okänd".
- **Invariant:** `mark.tif = öppningar(byggnader(terrang.tif))`: i varje polygon `max(terräng, takhojd_rh2000)`
  (platt tak), därefter öppningarna ur `oppningar.gpkg`; `markslag.tif` har 9 i byggnadscellerna utom där en
  öppning gjort dem till 1. Kärnan bygger om `mark.tif` med samma funktioner efter varje K9-ändring.
  **Ändring mot v1:** ett hinder utan känt tak fick förut `+ hinderhojd_m` per cell (följde terrängens lutning); nu är
  taket platt på `markhojd (median) + hojd`. På plan mark är resultatet identiskt.
- **Vegetation, härledning:** laserpunkter klass 3–5 (ASPRS låg/medel/hög vegetation), högsta punkt per 1 m-cell
  minus terrängens cellmedel; under 0,5 m och i byggnadsceller blir 0. Skogsstyrelsens Skogliga grunddata
  (trädhöjd) kan vara källa i stället: läs en lokal fil (hämtad via `data_inlasning.LokalDataCache`) och ge den som
  `vegetation_hojd_m` till `bygg_markmodell_ruta`. Koden laddar inget själv. `vegetation.gpkg` (täthet) är **reserverad**
  men inte byggd i v2.
- **Markslag ur flera källor (additivt tillägg, ingen versionshöjning):** `markslag_kalla.tif` (uint8, 0,25 m, samma
  utsträckning som `markslag.tif`, nodata 255) är **valfri** och skrivs bara när `markslagsytor` (DWG), `belagd_yta_mask`
  eller `ortofoto_markslag` getts till `bygg_markmodell_ruta`; saknas filen gäller bara det tidigare beteendet
  (`markslag.tif` oförändrad, ingen ny nyckel i receptet). Prioritetsordning (förslag, se Öppna beslut i `BESLUT.md`):
  baskartans ytor (byggnad, vatten, hårdgjort, tak, grönt tak, berg, grus, gräs, skog i den ordningen när de överlappar;
  därefter byggnader och öppningar som bränts in) > belagd yta ur laserintensitet (ger markslag 1) > ortofototolkning
  (`OKANT` = ingen uppgift) > standardklass (1 hårdgjort). Källfilens värde: bit 0–1 = avgörande källa (0 standard,
  1 ortofoto, 2 belagd yta ur laser, 3 baskarta); bit 2 (4) = en lägre källa, eller en annan baskartayta, sa något annat
  (högre prioritet vann, cellen flaggas); bit 3 (8) = avgörande källa är ortofoto med konfidens under 0,35; bit 4 (16) = avgörande baskartayta ligger på ett lager med
  status Odefinierad (Stockholms lagergrammatik, se `DATA.md`). Receptet får
  `installningar.markslag_prioritet`, `markslag_standard`, `markslag_svag_konfidens`, `markslag_baskarta_prioritet`
  (+ `markslag_belagd_installningar`) och källorna `markslagsytor` (sha256), `belagd_yta`, `ortofoto_markslag` samt
  `lagerprofil` (sha256 av profilfilen; skrivs när en profil getts, så en ändrad profil ger ny recept-sha256). `run.json`
  får `markslag_celler_<källa>`, `markslag_motsagelser`, `markslag_svaga`, `markslag_osakra_kallor`. Läsare som inte känner filen kan ignorera den.
- **Källor i `recept.json`:** `byggnadskandidater` (sha256 av kandidaternas kanoniska JSON), `byggnadskalla` (sha256 av
  källfilen, satt av CLI:t `--byggnader`), `laser_klasser`, `vegetation_hojd`; byggnadsregistret, terrängen och
  vegetationen finns som utdata (sha256) i `run.json`.
- **Läs-API:** `karna.geometri` – `las_byggnader(ruta)`, `las_terrang`, `las_ytmodell(ruta, vegetation=False)`,
  `las_vegetation`, `las_mark`, `las_markklasser` (se nedan). `las_ytmodell` (DSM) = `max(terräng, tak i polygonerna,
  terräng + vegetationshöjd)` och har **inga** öppningar (en port genom ett hus är ingen öppning i dagsljus/buller).
- **Markklass per domän:** `data/markklasser.toml` har per markslagskod `namn` och tabellerna `[vatten]`, `[luft]`,
  `[buller]`, `[dagsljus]`. Vatten har `initial_faktor` och `ksat_faktor` (samma som `mark_forluster`, ett test håller
  dem lika). **Buller** har `impedansklass` och `rahetsklass` (klassbokstäver; Nord2000-tabellerna står under
  `[buller.impedansklass]` och `[buller.rahetsklass]` på toppnivå) och de härledda kolumnerna
  `flodesresistivitet_kNsm4` och `rahet_m`. **Luft** har `z0_m` (råhetslängd, Davenport–Wieringa) och `lad_m2_m3`
  (bladytetäthet där vegetation står). Dagsljus har inga kolumner än. Buller och luft är märkta
  **preliminära** (`[buller]`/`[luft]` `preliminar = true`; bullers version `markklass/v1`): klassvalen per markslag är
  egna val, inte ur källa. Kod 0 (okänt) har med flit inga värden för buller och luft (flaggas, gissas inte).
  Kolumntillägget höjer inte `version` (bakåtkompatibelt). `Markklasstabell.raster(markslag,
  doman, kolumn)` ger ett rasterlager; saknad kolumn för en förekommande kod ger fel. Filens sha256 hör hemma i recept.

## K2 – Sänkgraf (v2)

**Versioner.** K2/v2 (2026-11-22) lägger till *slutsänkor*. En graf **utan** slutsänkor skrivs som
K2/v1, byte för byte som förut (samma filer, samma 12 kolumner, `kontrakt = "K2/v1"`). En graf **med** minst en
slutsänka skrivs som K2/v2. Läsaren (`sankgraf.k2_las.las_sankgraf_k2`) tar båda. Ändringen är brytande för
läsare som förutsätter att varje rotnod har ett ändligt `overfall_rh2000` (i v2 kan det vara null), därför
versionshöjningen (se `BESLUT.md` 2026-11-22).

`sankgraf.parquet`, en rad per nod:

| Kolumn | Typ | Betydelse |
| --- | --- | --- |
| `nod_id` | int64 | Unik inom grafen |
| `forald_id` | int64 / null | Noden som bildas när denna slås ihop med sin syster |
| `barn_id` | list<int64> | Tomt för lövsänkor |
| `overfall_rh2000` | float64 / null | Nivån där noden svämmar över. **v2:** null för en slutsänka |
| `overfall_x`, `overfall_y` | float64 | Överfallspunkten; för en slutsänka hindrets cell (se nedan) |
| `ut_till_id` | int64 / null | Noden vattnet rinner till vid överfall (null = ut ur området; alltid null för en slutsänka) |
| `volymkurva` | list<struct<z, v_m3>> | Volym som funktion av nivå, monotont ökande; för en slutsänka upp till `hinder_rh2000` |
| `ytkurva` | list<struct<z, a_m2>> | Vattenytans area som funktion av nivå |
| `avrinningsyta_m2` | float64 | Yta som rinner direkt till noden |
| `persistens_m` | float64 | Skillnad mellan överfallsnivå (för en slutsänka: hindret) och botten |
| `osakerhet_m` | float64 | Osäkerhet i överfallsnivån (för en slutsänka: i hindrets cell) |
| `typ` | string | **Bara v2.** `"sanka"` eller `"slutsanka"` |
| `hinder_rh2000` | float64 / null | **Bara v2.** Slutsänkans hinder; null för övriga noder |

`sankgraf.json` har samma fält per nod (plus `botten_rh2000` och `cell_antal`) och i v2 även `antal_slutsankor`.

**Slutsänka** (`typ = "slutsanka"`). Ett fyrgrannsammanhängande område av aktiva celler som inte når rutans kant,
eftersom det helt omges av ogenomträngliga celler (byggnader, markslag 9): typiskt ett slutet kvarter med gård.
Rutans **kant** är där vatten lämnar modellen (HAV); byggnader är hinder, inte utlopp.

- Hela området blir ett träd med en rot: slutsänkan. Den är alltid en rotnod med `forald_id = null`,
  `ut_till_id = null` och `overfall_rh2000 = null`. Inre sänkor i gården är vanliga noder under den.
- **Hinder:** `hinder_rh2000` är den lägsta cellen i ringen av ogenomträngliga celler som gränsar (fyrgrannar)
  till området, med höjden ur `mark.tif` (byggnadens takhöjd). Ligger den under rotens inre sadel används sadeln
  (trädet förblir monotont). Vägen vidare över taket prövas inte; på platta tak (K1 v2) är det samma nivå.
  `overfall_x/y` och `overfall_celler` i `sankgraf_topologi.json` pekar på hindrets cell. **Verkliga trösklar,
  portar och brunnar finns inte i markmodellen**: resultatet ska alltid flaggas (se nedan).
- **Fyllning:** vatten som når slutsänkan stannar där och fyller den efter volymkurvan. Når nivån hindret räknas
  överskottet som utflöde ut ur modellen och flaggas (`over_hinder_m3`); modellen beskriver inte vad som händer
  sedan. I Python (`SankNod`) bär en slutsänka `overfall_rh2000 = hinder_rh2000`, så att statisk fyllning,
  dynamik och 2D-hybrid räknar så utan särfall.
- **Avtappning** (fas 3, mm/h per cell, samma storhet som 2D-lösarens `tvad.kalltermer.Avtappning`) är enda vägen
  ut under hindret: i den händelsestyrda dynamiken (`avtappning_mm_h`) tappas varje nod med summan över sina
  celler. Utan avtappning stannar vattnet.
- Ett instängt område vars ring har en cell **utan höjd** (NaN) blir ingen slutsänka: hindret är okänt och
  `InstangtOmradeFel` kastas.
- Persistensfiltret tar aldrig bort en slutsänka (vattnet har ingen mottagare).

**Regn på tak (takvatten, 2026-11-22).** Regn på byggnadsceller avrinner till marken längs fasaderna i alla
vattnets lösare (graf, dynamik, 2D, hybrid), som standard (`takvatten = true`); reglerna står i `docs/VATTEN.md`
(okänd takform = brutet tak, hälften till varje långsida; ring = hälften till gården). Fördelningen byggs vid
körningen ur rastret och `byggnader.gpkg` och skrivs **inte** i K2 (K2-filerna är oförändrade). `in_m3` = regn på
mark + takvatten till mark och ut över rutkanten; volymbalansen (`volymbalans` i mätvärdena) räknar regnet på hela
rutan. Med `takvatten = false` räknas regn på tak inte (som före 2026-11-22) och redovisas som `tak_ej_raknat_m3`.

**Flaggor i resultat.** När ett resultat innehåller slutsänkor skriver lösarna (K6 `skriv_statiskt_resultat_k6`,
K21 `vatten.statisk_fyllning`, `vatten.blaytor`) `slutsanka.tif` (float32, 1,0 i slutsänkornas celler, NaN annars),
mätvärdena `antal_slutsankor`, `slutsanka_area_m2`, `slutsanka_lagrat_m3`, `slutsanka_max_djup_m`,
`slutsanka_over_hinder_m3` och `slutsankor` (per slutsänka), och varningen *"Slutet område: vattnet har ingen väg ut
i modellen; i verkligheten avgör brunnar, portar och trösklar."* (`sankgraf.typer.SLUTSANKA_VARNING`). Utan
slutsänkor skrivs inget av detta och resultaten är oförändrade; därför höjdes inte lösarnas versioner (resultat
som gick att räkna förut är byte-identiska; det som är nytt gav förut fel och cachades aldrig).

`flodesvagar.gpkg`, lager `flodesvagar`: linjer från varje cellgrupp till sin nod, med `gangtid_s` vid referensdjup.
`cell_till_nod.tif`: COG int64 med nod-id per cell.

Tilläggsfiler (bakåtkompatibla, 2026-11-21) som `skriv_sankgraf_k2` skriver så att grafen kan läsas
tillbaka exakt med `sankgraf.k2_las.las_sankgraf_k2`: `sankgraf_topologi.json` (`ut_lov_id`,
`overfall_celler`; samma `kontrakt` som `sankgraf.json`), `mark_bearbetad.tif` (markhöjd med brytlinjer och
öppningar inbrända, float32) och `markslag.tif`. Läsare som bara behöver kontraktets filer kan bortse från dem.
Negativa värden i `cell_till_nod.tif` är markörer, inte nod-id.

## K3 – Projektlapp (v1)

`lapp.json` + `lapp.gpkg`.

```json
{
  "kontrakt": "K3/v1",
  "lapp_id": "rosteriet-alt-a",
  "bas_recept": ["sha256 av berörda rutors recept"],
  "operationer": [
    {"typ": "hojdyta", "lager": "hojdytor"},
    {"typ": "byggnad", "lager": "byggnader", "entre_rh2000": "kolumn"},
    {"typ": "gata", "lager": "gator", "sektion": "standard-12m"},
    {"typ": "magasin", "lager": "magasin", "volym_m3": "kolumn"},
    {"typ": "markslag", "lager": "markslag"}
  ]
}
```

Gatusektioner definieras i `config.toml` under `[sektioner]` (kantsten, tvärfall, bredder, brunnsavstånd).

## K4 – Regn (v1)

- **Punktregn:** CSV med `tid_s, intensitet_mm_h`, samma över hela området.
- **Rumsligt regn:** COG-serie `regn_<tid_s>.tif` (mm/h) eller NetCDF med dimensionerna tid, y, x.

## K5 – Scenario (v1)

```json
{
  "kontrakt": "K5/v1",
  "regn": "fil eller id",
  "forluster": {"modell": "green-ampt", "initial_mm": 2.0},
  "grundvatten": "normal | hog | fil",
  "tjale": false,
  "brunnar_igensatta_andel": 0.0,
  "ledningar_fulla_fran_start": false,
  "slumpfro": 42
}
```

## K6 – Resultat (v1)

| Fil | Innehåll |
| --- | --- |
| `max_djup.tif` | Maxdjup (m) |
| `max_hastighet.tif` | Maxhastighet (m/s), tom för graf-nivån |
| `fara.tif` | Max av djup × hastighet (m²/s) |
| `tid_till_0_1m.tif`, `tid_till_0_2m.tif` | Sekunder från regnstart, tom där djupet aldrig nås |
| `noder.parquet` | Per nod: `nod_id, t_s, niva_rh2000, volym_m3` (tidsserie) |
| `entreer.csv` | Per entré: `entre_id, max_djup_m, tid_till_gransdjup_s` |
| `osakerhet/` | Samma filer som percentil 10, 50 och 90 när scenarierna körts som ensemble |
| `run.json` | Manifest (K0) med nivå: `graf`, `hybrid` eller `full2d` |

## K7 – Ruttparametrar (v1)

```json
{
  "kontrakt": "K7/v1",
  "fordon": {
    "personbil": {"gransdjup_m": null, "hastighet_per_djup": [[0.0, 1.0], [0.1, 0.5]]},
    "ambulans": {"gransdjup_m": null, "hastighet_per_djup": []},
    "tung_raddningsbil": {"gransdjup_m": null, "hastighet_per_djup": []}
  },
  "entre_gransdjup_m": null,
  "gangavstand_max_m": null,
  "gangvag_djup_max_m": null,
  "gangvag_fara_max_m2_s": null,
  "insatstidsmal_min": {"sjukhus": null, "vardboende": null, "skola": null, "forskola": null}
}
```

`null` betyder att värdet inte är bestämt. Rutt-modulen ska vägra köra skarpt med `null`-värden och tydligt
säga vilka som saknas. Testkörningar använder `config.toml` → `[rutt_test]` med markerade testvärden.
`hastighet_per_djup` är par av (djup_m, andel av normal hastighet).

## K8 – Ruttresultat (v1)

| Fil | Innehåll |
| --- | --- |
| `insatstider.parquet` | `adress_id, station_id, t_s, insatstid_s, torr_insatstid_s` |
| `entreer_nabarhet.parquet` | `entre_id, t_s, status (nabar / langre_gangvag / ej_nabar), gangvag_m` |
| `kritiska_avsnitt.gpkg` | Gatuavsnitt med `vinst` = antal adresser som blir nåbara om avsnittet hålls öppet |
| `run.json` | Manifest |

## K9 – Planscenario (v1)

Bas + diff, gemensamt för alla moduler (vatten, luft, buller, dagsljus). Kod: `karna.scenario`.

```json
{
  "kontrakt": "K9/v1",
  "scenario_id": "rosteriet-alt-b",
  "nyckel": "sha256 av innehållet (bas_recept, forald, andringar sorterade på andring_id)",
  "bas_recept": ["sha256 av berörda rutors recept (K1)"],
  "forald": "nyckeln till scenariot som detta ärver, eller null",
  "beskrivning": "fri text, ingår inte i nyckeln",
  "andringar": [
    {"typ": "byggnad_ny", "andring_id": "hus1", "geometri": {"type": "Polygon", "coordinates": []},
     "egenskaper": {"hojd_m": 18.0}}
  ]
}
```

Ändringstyper: `byggnad_ny`, `byggnad_bort` (`byggnads_id`), `byggnad_hojd` (`byggnads_id`, `hojd_m`),
`markhojd`, `markslag` (`kod` enligt K1), `gata`, `magasin` (`volym_m3`), `vegetation` (`hojd_m`).

Tillämpning på K1 v2 (`karna.geometri.tillampa`): `byggnads_id` är `byggnad_id` i `byggnader.gpkg` (ett id som
finns i ingen ruta i basen ger fel; ett id som bara finns i vissa rutor hoppas över i de andra). `byggnad_ny` kräver
`hojd_m` (över mark) eller `tak_rh2000`, inte entré; id = `andring_id`; valfria `takform`, `andamal`, `vaningar`.
`byggnad_hojd` tar exakt en av `hojd_m` (ny höjd över mark), `tak_rh2000` eller `delta_m` (±). `byggnad_bort` tar
valfritt `markslag` (K1-kod för marken efter rivningen; annars närmaste granncellens). `vegetation` sätter
vegetationshöjden över mark (0 tar bort) i polygonen. Alla tre ändringstyperna ger fel på en v1-ruta.
Geometri som GeoJSON i EPSG:3011. En ändring med samma `andring_id` som i föräldern ersätter den.
En fil vars `nyckel` inte stämmer med innehållet avvisas. K3 (projektlapp) är vattnets tillämpning
av ett scenario.

## K10 – Utredningsfråga (v1)

En fråga i TOML (eller motsvarande JSON). Kod: `karna.fraga`, `karna.korning`.

```toml
fraga = "dagsljus-grannar"
scenario = "rosteriet-alt-b"
jamfor_med = "rosteriet-alt-a"   # valfritt: båda körs, modulen redovisar B minus A
utdata = ["karta", "tabell"]       # karta, tabell, tabell_per_fastighet, rapportavsnitt, raster

[[analys]]
typ = "vsc_fasad"                  # registrerad av en modul med registrera_analystyp
urval = "befintliga_bostader"
```

Frågans nyckel = sha256 av innehållet med scenariernas nycklar i stället för namnen (frågans namn
ingår inte). Okända nycklar och analystyper som ingen modul registrerat ger fel. Varje analyssteg
körs som en nod i beräkningsgrafen: nyckel = sha256 av lösarens namn och version, parametrar och
indatanycklar; resultatet sparas i `cache/<nyckel>/` med `run.json` (K0).

---

## Gemensam kärna (T20)

Kontrakten nedan bryter inte mot K0–K10. K20 är filformatet för körbara frågor (`floden fraga`)
och bygger på K9-scenarier; K10 är det programmatiska gränssnittet med nodbyggare. Båda finns kvar.

## K11 – Fasadpunkter (v1)

Mätpunkter längs byggnaders fasader, genererade ur byggnadsregistret av
`karna.geometri.fasadpunkter(rutor, steg_m, vaningshojd_m, hojd_over_golv_m=1.5)`; inte en del av K1. Returnerar
`Fasadpunkter` (numpy-kolumner) som kan skrivas som GeoParquet (`skriv_geoparquet`, EPSG:3011, geometri = punkt).

| Kolumn | Typ | Betydelse |
| --- | --- | --- |
| `fasadpunkt_id` | text | `<byggnad_id>:<ring>-<kant>-<delsegment>:<våning>`, stabilt |
| `byggnad_id` | text | Id i `byggnader.gpkg` |
| `vaning` | int32 | 1 = nedersta |
| `e_m`, `n_m` | float64 | Läge på fasadlinjen (EPSG:3011) |
| `z_rh2000` | float64 | `markhojd + hojd_over_mark_m` |
| `hojd_over_mark_m` | float64 | `(vaning − 1) · vaningshojd_m + hojd_over_golv_m`; punkter över taket utelämnas |
| `normal_azimut_grader` | float64 | Utåtriktad normal, kompassvinkel (0 = norr, 90 = öster) |
| `normal_e`, `normal_n` | float64 | Samma normal som enhetsvektor |
| `delbredd_m` | float64 | Fasadlängd punkten representerar (kantens längd / antal punkter på kanten) |

Regler: per kant `ceil(L / steg_m)` punkter mitt i lika långa delsegment (totalt ≈ omkrets / steg; summan av
`delbredd_m` per kant = kantens längd). Yttre ring moturs, hål medurs, normalen till höger om färdriktningen = ut ur
byggnaden (in mot innergården för hål). Fasader som sitter mot en annan byggnad (punkten 5 cm utåt ligger i en annan
polygon) får inga punkter. Antal våningar = `vaningar` om angivet, annars `max(1, ⌊hojd_m / vaningshojd_m⌋)`. En byggnad ger
punkter en gång även om den finns i flera rutor. Sorterat på `byggnad_id`. Domänerna utgår från samma punkter.

## K20 – Utredningsfråga (v1)

TOML eller JSON. Relativa sökvägar tolkas relativt frågefilen. Okända fält ger fel.

```toml
kontrakt = "K20/v1"
fraga_id = "ornsberg2-grannar"          # a–z, 0–9, '-', '_'
beskrivning = "…"

[scenarier.<id>]
bas = "…"                               # K1-ruta (mark.tif) eller katalog med K1-rutor mm_E_N/
k9 = ["alt_a.json", "alt_b.json"]       # K9-planscenario, förfäder först (valfritt)
lappar = ["…/lapp.json"]                # K3, tillämpas i ordning efter K9 (valfritt)

[[analyser]]
id = "…"
losare = "<doman>.<namn>"               # K21
scenarier = ["…"]                       # valfritt; utelämnat = alla
parametrar = { … }                      # valideras av lösaren
indata = { <roll> = "fil" }             # roller enligt lösarens indataroller
fokus = [x_min, y_min, x_max, y_max]    # valfritt, EPSG:3011: bara rutor vars mittpunkt ligger inom
                                        # rutan körs (övriga rutor är kontext för grannar)
beror_pa = ["<analys-id>"]              # valfritt; cykler ger fel

[[jamforelser]]                         # valfritt
analys = "…"
utdata = "<roll>"                       # GeoTIFF i K1-rutnätet
fore = "<scenario>"
efter = "<scenario>"
troskel = 0.0                           # för ytmåtten
```

Utdata från `floden fraga kor` i `resultat/<fraga_id>/` (eller `--ut`):

| Fil | Innehåll |
| --- | --- |
| `<analys>/<scenario>[/<ruta_id>]/` | Lösarens filer + K0 `run.json` |
| `jamforelser/<analys>_<roll>_<efter>_minus_<fore>/` | `diff.tif` (globalt) eller `<ruta_id>.tif` |
| `sammanstallning.json` | Scenarier med rutors hash, alla jobb med nyckel, `fran_cache`, körtid, mätvärden, varningar, jämförelsemått |
| `run.json` | K0-manifest för hela frågan |

**Vattnets antaganden i K20 (2026-11-22, inga formatändringar).** `vatten.statisk_fyllning` (version 2) och
`vatten.dynamik` (version 1) tar `takvatten` (bool, standard true), `takvatten_andel_ledning` (0–1, standard 0),
`avtappning_mm_h` (≥ 0, standard 0 = ledningsnätet antas fullt) och `avtappning_omraden` (lista
`{geometri, mm_h}`, EPSG:3011), samt valfri indataroll `avtappning_omraden` (GeoJSON/GeoPackage med
`avtappning_mm_h`). Statisk fyllning med avtappning kräver `varaktighet_min` (eller IDF-regn). `vatten.dynamik` tar
regnet som `regn_tid_s` + `regn_mm_h` eller som IDF-parametrarna, och `slut_tid_s`. Mätvärdena får
`takvatten_*`, `avtappning_mm_h`, `avtappning_kalla`, `avtappning_antagande`, `avtappat_m3` och `volymbalans`;
webben visar `takvatten_antagande` och `avtappning_antagande` under Antaganden.

## K21 – Lösare (v1)

Python-gränssnitt `karna.losare.Losare`. Se `docs/ANSLUTA_LOSARE.md`.

| Del | Krav |
| --- | --- |
| `namn` | `<doman>.<namn>`, domän ∈ {vatten, luft, buller, dagsljus, geometri, ovrigt} |
| `version` | Sträng. Höjs när resultatet kan ändras för samma indata. Ingår i nyckeln |
| `indataroller`, `valfria_indataroller` | Roller frågan måste ange resp. får ange (tillägg 2026-11-21, bakåtkompatibelt: lösare utan `valfria_indataroller` beter sig som förut; K21 förblir v1) |
| `validera_parametrar(p)` | Returnerar fullständiga parametrar med standardvärden; okända ger `LosarFel` |
| `paverkansradie_m(p)` | `None` = globalt jobb; tal ≥ 0 = rutvisa jobb, nyckeln beror bara på rutor närmare än radien |
| `kor(kontext)` | Skriver i `kontext.utmapp`, returnerar `LosarUtdata(filer, matvarden, varningar)`. Får parametrarna i kanonisk form (samma som hashades). Rutvisa jobb ser bara rutorna i nyckeln |
| Registrering | `src/<paket>/floden_losare.py` med `LOSARE: list[Losare]`, upptäcks automatiskt |

**Jobbnyckel** = sha256 av kanonisk JSON (sorterade nycklar, flyttal med `repr`, heltalsvärda
flyttal som heltal, NaN förbjudet) över: lösarens namn och version, validerade parametrar,
sha256 för extra indata, radie, ruta, `[ruta_id, rutans innehållshash]` för berörda rutor och
nycklarna för de jobb man beror på.

**K9 på K1-rutor:** kedjan plattas ut (`karna.scenario.platta_ut`) och översätts till en K3-lapp
(`karna.k9_till_k1`; `vatten.scenario_lapp` är ett tunt lager ovanpå samma funktion). Alla `bas_recept` i K9 måste finnas
bland basrutornas `recept.json`, annars fel. På K1 v2-rutor tillämpas `byggnad_ny`,
`byggnad_bort`, `byggnad_hojd` och `vegetation` av `karna.geometri` (se K9); på v1-rutor ger de tre senare
och `markhojd` med `delta_m` fel. Nyckeln beror på de utplattade
ändringarna, inte på hur kedjan är uppdelad.

**Lappnyckel** (scenario med lapp, per ruta) = sha256 över basrutans hash, `lapp.json`,
sha256 för varje LandXML-fil lappen pekar på (relativ sökväg tolkas relativt lappfilen) och de
gatusektioner ur `config.toml` som lappen använder.

**Rutans innehållshash** = sha256 av kanonisk lista `[relativ sökväg, filens sha256]` för alla
filer i rutans katalog. En lapp som inte ändrar en ruta lämnar dess hash orörd.

## K22 – Resultatcache (v1)

Katalog (standard `data/cache/karna/`, inte i git):

```
jobb/<nn>/<nyckel>/      nyckel.json, utdata/, utdata.json, run.json, .klar
scenarier/<nn>/<nyckel>/ ruta/ (K1-ruta med lapp tillämpad) eller bara .klar om lappen inte ändrade rutan
```

`<nn>` är nyckelns två första hextecken. En post räknas som klar bara om `.klar` finns.
Poster skrivs i en temporär katalog och flyttas på plats atomärt. Cachen kan raderas när som
helst; den byggs upp igen från recepten.

## K32 – Påstående och belägg (v1)

Ett påstående i en rapport (R1, `src/rapport/pastaende.py`). Lagras i K33 (`pastaenden`). Tal i
`text` står bara som platshållare `{{matt:<adress>[|avrundning=…][|enhet=…][|tecken=ja][|format=exp]}}`;
siffror utanför platshållare är fel (utom beteckningar som `SK-06`, `RH 2000`, `EPSG:3011`). Se
`docs/RAPPORTER.md`.

```json
{
  "kontrakt": "K32/v1",
  "pastaende_id": "p-006",
  "fraga_id": "SK-06",
  "status": "visat",                       // visat | avvikelse | villkorat | underlag_saknas | ej_tillampligt
  "text": "… djupare än tröskeln på {{matt:jamforelse.skyfall_max_djup_efter_minus_fore.yta_okning_m2}} mark …",
  "fordjupning": "ja",                     // nej | ja | kan_inte_avgoras (trafikljus grön | gul | röd)
  "mottagare": "hela_omradet",             // valfri: rapportens mottagare eller hela_omradet
  "forutsattning": null, "ansvarig_funktion": null,   // krävs för villkorat
  "motivering": null,                      // krävs för ej_tillampligt
  "saknat_underlag": [],                   // krävs för underlag_saknas
  "forfattare": "ai",                      // ai | manniska | referensflode
  "modell": "…", "tid": "2026-11-22T08:00:00+00:00",
  "bedomningsgrund": null, "granskning": null,        // nivå 2 (R3), null i nivå 1
  "belagg": [{
    "adress": "jamforelse.skyfall_max_djup_efter_minus_fore.yta_okning_m2",
    "korning_id": "demo-vatten",
    "fil": "jamforelser/skyfall_max_djup_efter_minus_fore/diff.tif", "fil_sha256": "…",
    "manifest": "run.json", "manifest_sha256": "…",          // K0-manifestet som belägget vilar på
    "analys": "skyfall", "losare": "vatten.statisk_fyllning", "scenarier": ["fore", "efter"],
    "mottagare": "hela beräkningsområdet",
    "namnare": "mark i hela beräkningsområdet; byggnadernas tak undantagna",
    "matt": "max_djup:yta_okning_m2", "storhet": "skillnad i vattendjup över markytan",
    "enhet": "m²", "tidsdefinition": "läget vid regnets slut (statisk fyllning, inget förlopp)",
    "statistik": "yta på mark där värdet ökat mer än tröskeln 0,01 m",
    "varde": 3258.0,                       // före avrundning
    "upplosning": 1.0,                     // minsta meningsfulla steg (ytmått: cellens yta)
    "jamforbarhet": "<sha256 av analysens parametrar utan jobbnyckel>",
    "regn": "Stockholmsregn 2024, återkomsttid 100 år, …",
    "bedomningsgrund": null                // nivå 2
  }]
}
```

Adresser: `jamforelse.<namn>.<mått>`, `matvarde.<analys>.<scenario>[.<ruta>].<namn>`,
`omrade.<mottagare>.<lager_id>.<statistik>`. Belägg skapas bara av Floden
(`rapport.belaggskalla.FlodenBelaggKalla`) ur samma källa som MCP och webben
(`FlodenTjanst.jamfor` = `FragaTjanst.marksiffror`, `resultat_sammanfattning`, `resultat_varde`).

## K33 – Rapport (v1)

`<rapportmapp>/<rapport_id>/rapport.json` (standard `data/lokal/rapporter/`, utanför git), med
`kartor/<kart_id>.png`, `export/<rapport_id>.docx|.pdf` och `resurser/` (lokal logotyp och lokala
typsnitt, namngivna efter sha256). `rapport_id` = `r-` + 12 hex.

| Fält | Innehåll |
| --- | --- |
| `kontrakt` | `"K33/v1"` |
| `rapport_id`, `niva` (1), `amne` (`skyfall`), `projekt`, `titel`, `skapad` | |
| `fragor` | Frågornas id ur katalogen; `katalog` har filen, källäget och frågornas text |
| `korningar` | Per körning: `korning_id`, `fraga_id`, `beskrivning`, `kodversion`, `manifest`, `manifest_sha256`, `start`, `analyser` (analys, lösare, scenarier), `jamforelser`, `antaganden`, `scenarier` |
| `mottagare` | `[{namn, polygon [[e, n], …] EPSG:3011, beskrivning}]` |
| `pastaenden` | K32 |
| `kartor` | `{kart_id, korning_id, jamforelse, analys, lager_id, typ (bas/scenario/skillnad), scenario, fil, png_sha256, bildtext, meta {norr, skalstock_m, enhet, skala, cellstorlek_m, kartutsnitt, utsnitt, metodstatus, roll, rubrik (valfri: kartans rubrik i klarspråk)}}`; `bildtext` är Flodens neutrala bildtext; dokumentet visar den i rapportens språkprofil |
| `metoder` | Per lösare: `losare`, `visningsnamn`, `version`, `status` (skiss/rapport), `underlag`, `anmarkning` |
| `metodnotis` | `{ai_stod, modeller, ansvarig_utredare}` (ansvarig valfri i nivå 1, krävs i nivå 2) |
| `dataklass` | Projektets klass enligt policyn; bara `oppet` exporteras i nivå 1 |
| `handelser` | `{tid, handelse, av, modell, detalj}`: skapad, påstående tillagt/borttaget, kontroll, export |
| `kontroll` | Senaste kontrollen: `{godkand, tid, punkter: [{id, rubrik, godkand, granskat, fel}]}` |
| `exporter` | `{format (word/pdf), fil, sha256, tid, typsnitt (valfri: [{roll, familj, sha256}] för inbäddade lokala typsnitt)}` |
| `skede`, `revision`, `bedomningsgrunder`, `granskning`, `signering` | Nivå 2 (R3), förberedda |
| `installningar` | Valfritt (tillagt 2026-11-22, ingen versionsökning: äldre rapporter utan fältet läses med förvalen). `{sprakprofil ("sbk" \| "neutral"), numrerade_rubriker, numrerade_figurer, avsandare, diarienummer, handlaggare, accentfarg (hex utan #), logotyp, typsnitt_brodtext, typsnitt_rubriker}`; filerna som `{sha256, filtyp, familj}` eller null, aldrig sökvägar. Kopian ligger i `resurser/<sha256><filtyp>` i rapportmappen. Se `docs/RAPPORTER.md` |

MCP-körloggens rader får fältet `rapport_id` (lista) när ett anrop rör en rapport
(tillägg i `floden-korlogg/v1`, äldre rader saknar fältet).

## K34 – Bifurkationspunkter `floden-bifurkationer/v1` (v1)

Registrerat 2026-11-23 i efterhand: formatet fanns redan i koden (`sankgraf.bifurkation`) utan kontrakt.
Ingen ändring av formatet, så versionen är v1. Topologiskt känsliga överfallspunkter i sänkgrafen:
punkter där vattnets utlopp kan byta väg inom höjdosäkerheten (Q2, `docs/MATEMATIK.md`).

**Skrivs av** `sankgraf.bifurkation.skriv_bifurkationer_json` (fil `bifurkationer.json`; anropas av
`tools/exportera_q2_underlag.py`). Samma punkter skrivs som GeoPackage av `skriv_bifurkationer_gpkg`
(`bifurkationer.gpkg`, lager `bifurkationer`, punktgeometri på överfallet, EPSG från argumentet, förval
3011; attributen som nedan utom koordinaterna, och den alternativa sadelns läge ingår inte). **Läses av** inget i Floden idag (testerna
`tests/sankgraf/test_bifurkation.py` läser tillbaka filen); avsedd för skissrapport, webb och Samuels analys.
Punkterna finns också inbäddade i svaret från `POST /api/analys/bifurkationer` (K36).

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-bifurkationer/v1"` |
| `k_sigma` | tal | (antal σ) | Instabil om säkerhetsmarginalen ≤ `k_sigma` (förval 2,0) |
| `referensregn_mm` | tal | mm | Regndjup som `paverkad_volym_m3` räknas med (förval 100) |
| `antal_instabila`, `antal_totalt` | heltal | st | Antal punkter med `instabil = true` respektive alla punkter i listan |
| `punkter` | lista | | Sorterad: störst `paverkad_volym_m3` först, sedan minst `sakerhetsmarginal`, sedan `nod_id` |

Punkt (`punkter[]`):

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `nod_id` | heltal | | Sänkgrafens nod vars utlopp är känsligt |
| `primar_ut_till_id`, `alternativ_ut_till_id` | heltal eller null | | Nuvarande utloppsmål och konkurrerande mål (null = havet/rutans rand, eller okänt) |
| `overfall_hojd_m`, `alternativ_hojd_m` | tal | m RH 2000 | Höjd på primär respektive lägsta alternativa sadel |
| `marginal_m` | tal | m | `max(0, alternativ − överfall)` |
| `osakerhet_komb_m` | tal | m | `√(σ_överfall² + σ_alternativ²)`, 1 σ i höjd |
| `sakerhetsmarginal` | tal eller null | (antal σ) | `marginal_m / osakerhet_komb_m`; null när σ = 0 och marginalen > 0 (oändlig) |
| `instabil` | bool | | `sakerhetsmarginal ≤ k_sigma` |
| `overfall_x`, `overfall_y`, `alternativ_x`, `alternativ_y` | tal | m, EPSG:3011 | Sadlarnas lägen (grafens koordinatsystem; filen har inget eget `epsg`-fält) |
| `paverkad_avrinningsyta_m2` | tal | m² | Uppströms avrinningsyta som når noden |
| `paverkad_volym_m3` | tal | m³ | `paverkad_avrinningsyta_m2 × referensregn_mm / 1000` |

## K35 – Bifiltreringsunderlag `floden-bifiltrering/v1` (v1)

Registrerat 2026-11-23 i efterhand (ingen formatändring). Rent, GIS-fritt dataset av sänkgrafen för
bigraderade k[x, y]-experiment (2-parameterpersistens, Q2).

**Skrivs av** `sankgraf.bifurkation.exportera_bifiltrering_json` (fil `bifiltrering_q2.json`; anropas av
`tools/exportera_q2_underlag.py`). **Läses av** inget i Floden (testerna läser tillbaka filen); avsedd för
Samuels externa experiment i Python eller Macaulay2.

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-bifiltrering/v1"` |
| `epsg` | heltal | | Grafens koordinatsystem (3011) |
| `hav_nod_id` | heltal | | Nod-id som betyder havet/rutans rand i `till_nod` (−1) |
| `antal_noder`, `antal_sadelkanter` | heltal | st | Längden på listorna nedan |
| `noder` | lista | | En post per sänkgrafsnod, sorterad på `nod_id` |
| `sadelkanter` | lista | | Lägsta sadel mellan varje par av grannlövregioner (4-grannar) och mellan en lövregion och havet |

Nod (`noder[]`): `nod_id` (heltal), `ar_lov`, `ar_slutsanka` (bool), `forald_id`, `ut_till_id` (heltal
eller null), `barn_id` (lista av heltal), `botten_hojd_m` (m RH 2000), `overfall_hojd_m` (m RH 2000, null för
slutsänka), `persistens_m` (m), `osakerhet_m` (m, 1 σ i höjd), `avrinningsyta_m2` (m²), `max_volym_m3` (m³).

Sadelkant (`sadelkanter[]`): `fran_nod`, `till_nod` (heltal; −1 = havet), `sadel_hojd_m` (m RH 2000),
`sadel_osakerhet_m` (m, 1 σ), `x`, `y` (m, EPSG i `epsg`).

## K36 – Bifurkationsanalys i webb-API `floden-bifurkation-felindikator/v1` (v1)

Registrerat 2026-11-23 i efterhand (ingen formatändring). Ett JSON-svar, ingen fil: svaret på
`POST /api/analys/bifurkationer` med kroppen `{omrade_id, k_sigma (förval 2,0), referensregn_mm (förval 100)}`.

**Skrivs av** `FlodenWebbTjanst.kor_bifurkation_analys` i `src/webb/api.py`, som kör
`detektera_bifurkationer` och `tvad.felindikator.berakna_2d_felindikator` på områdets basgraf.
**Läses av** webbklienten (`webb/src/karta/kartapi.ts`, `BifurkationAnalysSvar`, panelen i `kartsida.ts`).

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-bifurkation-felindikator/v1"` |
| `omrade_id` | text | | Området som analyserades |
| `k_sigma`, `referensregn_mm` | tal | (antal σ), mm | Som i K34 |
| `antal_instabila` | heltal | st | Antal punkter i `punkter` (bara instabila) |
| `punkter` | lista | | Punkter enligt K34, bara de med `instabil = true`; koordinater i EPSG:3011 (m) |
| `felindikator` | objekt | | A posteriori-bedömning av var 2D-lösaren behövs, se nedan |

`felindikator`: `andel_2d_yta` (andel 0–1, ingen procent), `antal_2d_celler`, `antal_aktiva_celler`
(celler utan byggnad), `antal_noder_2d`, `antal_noder_totalt` (heltal, st). Masken över rekommenderade
2D-celler (`rekommenderad_2d_mask`) ingår inte i svaret.
