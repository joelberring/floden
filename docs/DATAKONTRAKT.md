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

**Tillägg i webb-API:t (2026-11-23, additivt, K8 förblir v1).** `POST /api/rutt` (testområden och
testvärden, `tillat_testvarden: true`) tar det valfria heltalsfältet `antal_insatser` (0–50, förval 0; annat ger 400).
Är det 0 eller utelämnat är svaret oförändrat. Är det > 0 väljs `k = antal_insatser` vägavsnitt som ska hållas
öppna (CELF, `rutt.kritiska.valj_kritisk_insats_k`, för regnet med längst återkomsttid i körningen; en station i
nätets första nod och en adress i varje övrig nod) och svaret får fältet `rekommenderade_insatser`, en lista i vald ordning:
`kant_id` (text), `marginalvinst`, `kumulativ_vinst` och `vinst_ensam`, alla tre i antal adresser som blir nåbara
(dimensionslösa tal). Fältet saknas när `antal_insatser` är 0 eller när körningen inte har något regn. Äldre klienter ser ingen
skillnad.

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
| `namn` | `<doman>.<namn>`, domän ∈ {vatten, luft, buller, dagsljus, ekologi, geometri, ovrigt} (`ekologi` tillagd 2026-11-23 för `ekologi.konnektivitet`, K45; ett värde till i listan, bakåtkompatibelt, K21 förblir v1) |
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

## K30 – Beställning (v1)

Valfri beställning som styr vilka katalogfrågor ett skissunderlag tar med och hur ingressen
skrivs (R2, `src/rapport/bestallning.py`). Lagras i `<rapportmapp>/bestallningar/b-<12 hex>.json`
och som kopia i K33 (`bestallning`). Innehåller ingen personuppgift (syftet kontrolleras mot
e-postadress och personnummer).

```json
{
  "kontrakt": "K30/v1",
  "bestallning_id": "b-0a1b2c3d4e5f",
  "syfte": "Skissa hur ett höjt hus påverkar sol på marken och dagsljus vid fasad",
  "skede": "skiss",                       // skiss | planunderlag (planunderlag kan inte köras förrän R3)
  "mottagare_roll": "planhandlaggare",    // planhandlaggare | byggaktor | granskare
  "amnen": ["dagsljus", "sol"],           // skyfall | dagsljus | sol | luft | buller (en rapport per beställning)
  "projekt": "demokvarteret",
  "skapad": "2026-11-23T09:00:00+00:00",
  "fragor": [],                           // uttryckliga frågor (id); går före urvalet
  "villkor": [],                          // filtrerar på frågans villkor
  "bara_med_underlag": true,              // bara frågor Floden kan besvara helt eller delvis
  "scenario": {"fore": "bas", "efter": ["hojd", "nytt_hus"]},   // eller null
  "urval": ["DL-02", "DL-10", "…"],       // frågorna som beställningen ger
  "utelamnade": 15,                       // frågor i ämnena som inte kom med
  "katalog": {"granskningsdatum": "2026-11-21"}
}
```

Urval: uttryckliga `fragor` annars ämnenas frågor, filtrerade på `villkor`, mottagarrollens
frågetyper och `bara_med_underlag`. Tomt urval ger fel.

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
`omrade.<mottagare>.<lager_id>.<statistik>` (mottagaren `hela_omradet` är hela beräkningsområdet;
statistiken `yta_m2` är ytan med värde), samt för dagsljus (R2) `fasad.vsc.<scenario>.<mått>`,
`fasad.vsc.<parameter>` och `fasadjamforelse.vsc.<före>.<efter>.<mått>` och jämförelsemåttet
`yta_minskning_over_<X>h_m2`. Belägg skapas bara av Floden
(`rapport.belaggskalla.FlodenBelaggKalla`) ur samma källa som MCP och webben
(`FlodenTjanst.jamfor` = `FragaTjanst.marksiffror`, `resultat_sammanfattning`, `resultat_varde`).

## K33 – Rapport (v1)

`<rapportmapp>/<rapport_id>/rapport.json` (standard `data/lokal/rapporter/`, utanför git), med
`kartor/<kart_id>.png`, `export/<rapport_id>.docx|.pdf` och `resurser/` (lokal logotyp och lokala
typsnitt, namngivna efter sha256). `rapport_id` = `r-` + 12 hex.

| Fält | Innehåll |
| --- | --- |
| `kontrakt` | `"K33/v1"` |
| `rapport_id`, `niva` (1), `amne` (`skyfall` eller `dagsljus`), `projekt`, `titel`, `skapad` | |
| `fragor` | Frågornas id ur katalogen; `katalog` har filen, källäget och frågornas text |
| `bestallning` | Valfritt (tillagt 2026-11-23, ingen versionsökning: äldre rapporter utan fältet läses som förut). K30, eller null |
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
`tests/sankgraf/test_bifurkation.py` läser tillbaka filen); avsedd för skissrapport, webb och forskningspartens analys.
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
Forskningspartens externa experiment i Python eller Macaulay2.

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

## K37 – Benchmark `floden-benchmark/v1` och provfall `floden-prov-webgpu/v1` (v1)

Nästa lediga nummer efter K36 (kontrollerat mot `main` 2026-11-23). Två JSON-format som hör ihop:
benchmarkdokumentet som både servern och webbläsaren skriver, och API-svaret med provfallets indata och facit.

### Benchmarkdokumentet `floden-benchmark/v1`

**Skrivs av** `floden benchmark --ut fil.json` (`src/benchmark/`, källa `"server"`) och av provsidan
`#/prov/webgpu` (`webb/src/prov/benchmarkFormat.ts`, källa `"webblasare"`). **Läses av** `floden benchmark --jamfor`
och människor. Reglerna kontrolleras av `benchmark.format.kontrollera` (Python) och `kontrolleraDokument` (TypeScript);
filen `tests/benchmark/exempel_webblasare.json` kontrolleras av båda.

| Fält | Typ | Innehåll |
| --- | --- | --- |
| `format` | text | `"floden-benchmark/v1"` |
| `kalla` | text | `"server"` eller `"webblasare"` |
| `skapad_utc` | text | ÅÅÅÅ-MM-DDTHH:MM:SSZ |
| `kodversion` | text | git-revision (K0). För webbläsaren: serverns revision vid hämtningen av provfallet |
| `maskin` | objekt | Server: `cpu`, `karnor`, `karnor_tillgangliga`, `minne_totalt_mb`, `os`, `python`, `numpy`, `rust_karna` (`tillganglig`, `sokvag`, `anvands_i_svit`). Webbläsare: `gpu` (`tillganglig`, `adapter`, `begransningar`) och `webblasare` (`user_agent`, `sprak`, `enhetsminne_gb`); övriga fält `null`. Fält utan värde är `null`, aldrig utelämnade |
| `installning` | objekt | `uppvarmning`, `upprepningar`, `tidsmatt`, `minnesmatt` (text om hur det mättes) |
| `anmarkning` | text | Fri text. Webbläsardokument anger alltid att klientens f32-tal bara är skissläge |
| `delar` | objekt | Nycklar `skyfall`, `dagsljus`, `luft`, `buller`; varje värde `{status, skal, fall}` |

`delar.<namn>.status`: `"kord"`, `"hoppas_over"` eller `"kraver_lokal_kallmapp"`. Är den inte `"kord"` ska `skal`
vara en text. `fall` är en lista; varje fall har `id`, `namn`, `status` (`"ok"`, `"fel"`, `"hoppas_over"`) och, när
status inte är `"ok"`, ett `skal`. Ett fall med status `"ok"` har:

| Fält | Enhet | Innehåll |
| --- | --- | --- |
| `tid_s` | s | `{median, min, max, n, alla}`: tidtagna körningar efter uppvärmning. Medianen jämförs |
| `minne` | MB | Server: `rss_start_mb`, `rss_topp_mb`, `rss_okning_mb` (processens högsta RSS, `VmHWM`, egen process per fall) och `tracemalloc_topp_mb` (separat körning). Webbläsare: `gpu_buffrar_mb` (summan av skapade buffertar, beräknad). Får vara `{}` |
| `avvikelse` | | `null` om fallet saknar facit. Annars `{varde, enhet, grans, godkand, beskrivning, matt}`: `varde` är fallets eget mått mot facit (största fel), `grans` är gränsen testsviten använder (eller `null`), `godkand = varde <= grans`, `matt` är extra tal (t.ex. `medelfel`, `andel_over_tolerans`) |
| `extra` | | Fri ordbok med uppgifter om fallet (storlekar, delresultat). Jämförs inte |

Inga värden får vara `NaN` eller oändliga (de går inte att skriva som JSON). Ett oändligt GPU-fel skrivs som
`avvikelse.varde = 1.797e308` med `godkand = false` och `matt.antal_ickeandliga > 0`.

**Jämförelse** (`--jamfor gammal.json`): fall matchas på `delar.<del>` och `id`; måtten `tid_s.median`,
`minne.rss_topp_mb`, `minne.tracemalloc_topp_mb` och `avvikelse.varde` jämförs, och ett mått som ökat med mer än
tröskeln (förval 25 %, `--troskel-procent`) flaggas som försämring (slutkod 1). Dokument från olika maskiner eller
källor jämförs men märks som osäkra.

### Provfall `floden-prov-webgpu/v1` (webb-API)

`GET /api/prov/webgpu/fall` ger katalogen och `GET /api/prov/webgpu/fall/<id>` ger indata och facit, som JSON.
Bara superadmin när inloggning är på (403 annars); öppet lokalt när inloggning är av. Skrivs av `webb/prov.py`
med `benchmark.provfall`; läses av `webb/src/prov/webgpuProv.ts`. Fält (matriser):
`{"dtyp": "float32" | "uint8", "form": [rader, kolumner], "base64": "…"}`, little endian, rad 0 = norr.

| Fält | Innehåll |
| --- | --- |
| `format`, `id`, `typ` (`"dagsljus"` eller `"vatten_2d"`), `namn`, `beskrivning`, `kodversion` | |
| `markning` | Skisslägestexten |
| `indata` | Dagsljus: `nx`, `ny`, `cell_m`, `rmax_celler`, `n_riktningar`, `steg_h`, matriserna `z_m`, `sol_hojd_rad`, `sol_azimut_rad`. Vatten: `nx`, `ny`, `dx`, `dy`, `dt`, `theta`, `min_djup`, matriserna `z_bed`, `manning`, `aktiv` och `steg` (`[{antal, regn_m}]`) |
| `facit` | `beraknad_med` (text), `kortid_s`, `falt` (namn → matris + `enhet`), `tolerans` (förslag per fält, absolut fel i fältets enhet), vatten också `massbalans_rel` |

Facit är serverns f64-beräkning: dagsljus med `dagsljus.horisont`, vatten med referensschemat
`benchmark.provfall.vatten_schema` (samma schema som `vatten_2d.wgsl`, inte hela lösaren `tvad.lokal_troghet`).
Klientens tal är f32 och får bara användas i skissläge.

## K38 – Öppenhetsmetadata `floden-oppenhet/v1` (v1)

Tillägg (inget K0–K37 ändras). En fil `oppenhet.toml` i en datamapp säger om innehållet är öppet. MCP-lagret
(`floden_mcp.oppenhet`) lämnar aldrig ut innehåll härlett ur data som inte är öppna; regeln står i `docs/MCP.md`
("Data som inte är öppna").

```toml
kontrakt = "floden-oppenhet/v1"      # valfritt
oppen = true                         # true | false; allt annat än true räknas som false
licens = "CC0-1.0"                   # SPDX-id; krävs när oppen = true
kalla = "Lantmäteriet, Laserdata Nedladdning skog"   # fri text
```

- Närmaste `oppenhet.toml` i mappen eller en överordnad mapp (högst 12 nivåer) gäller. Filen som inte går att tolka stänger mappen.
- Öppet kräver `oppen = true` **och** en känd öppen licens: `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `ODbL-1.0`, `ODC-By-1.0`,
  `PDDL-1.0`. Annars stängt. `oppen = false` stänger alltid, även inom demon.
- Ledningsdata (en sökvägsdel som börjar på `ledning`, `data/ledningar`, `[lokal_data] ledningar_mapp`), `data/intern`, `data/konto`
  och `config.local.toml` är aldrig öppna, vad en metadatafil än säger.
- Utan metadata är bara demon och material som följer med repot öppet (`data/demo`, `testsvit`, `exempel`, `docs`, `data/regn`,
  `data/mcp`, `data/lagerprofiler`) samt MCP:s och kartverktygets egna arbetsmappar. Allt annat (inklusive `data/lokal`) är stängt.
- I `config.local.toml` kan lager anges utan fil: `[oppenhet.lager.<namn>]` med `sokvag`, `oppen` och `licens` (samma regler).

## K39 – OAuth för MCP: token- och metadataformat `floden-oauth/v1` (v1)

Tillägg (inget K0–K38 ändras). Beskriver vad Claude-appen och andra MCP-klienter ser; regler i `docs/KONTO.md` avsnitt 13.

- Token: `fl_oac_` (auktoriseringskod, 60 s, engångs), `fl_oat_` (access, 3600 s) och `fl_ort_` (refresh, 30 dagar, roteras), alla +
  32 slumpbyte som base64url (43 tecken). Bara sha256 sparas. `fl_mcp_` (personlig token) är oförändrad.
- Metadata: `/.well-known/oauth-protected-resource[/mcp]` (RFC 9728) och `/.well-known/oauth-authorization-server` (RFC 8414); `scope`
  är `mcp` (`offline_access` accepteras och ignoreras). Registrering: `POST /oauth/register` (RFC 7591), svar med `client_id`.
- Token-svaret: `{"access_token", "token_type": "Bearer", "expires_in": 3600, "refresh_token", "scope": "mcp"}`; fel som
  `{"error", "error_description"}` (RFC 6749).
- Körloggens fält `token` är för en OAuth-token grant-id (32 hextecken), för en personlig token token-id.

## K40 – Vattenintervall `floden-vatten-intervall/v1` (v1)

Nästa lediga nummer efter K39 (K38 och K39 kom med MCP-grenen, PR #58). Registrerat 2026-11-23 i efterhand
(PR #57, ingen formatändring). Ett JSON-svar, ingen fil: monoton intervallanalys av maxdjup per sänknod när
klimatfaktor, initial förlust och avtappning varierar inom intervall, med grenunion över bifurkationspunkter (K34).

**Anrop:** `POST /api/analys/intervall`. **Skrivs av** `FlodenApiApp.kor_intervall_analys` (`src/webb/api.py`)
som kör `dynamik.intervall.kor_monoton_intervallanalys`. **Läses av** inget i webbklienten än.

Begäran (JSON-objekt):

| Fält | Typ | Enhet | Förval | Innehåll |
| --- | --- | --- | --- | --- |
| `omrade_id` | text | | krävs | Testsvitens område eller projekt (demokvarteret) |
| `klimatfaktor_intervall` | `[lo, hi]` | (faktor) | `[1,0, 1,25]` | 0 ≤ lo ≤ hi, ändliga tal |
| `initial_forlust_mm_intervall` | `[lo, hi]` | mm | `[0, 2]` | som ovan |
| `avtappning_mm_h_intervall` | `[lo, hi]` | mm/h | `[0, 0]` | som ovan |
| `k_sigma` | tal ≥ 0 | (antal σ) | 2,0 | Bifurkationsgräns, som K34 |
| `referensregn_mm` | tal > 0 | mm | 100 | Som K34; används också som regn (1 h) för projekt, som saknar eget regn |

Svar (200):

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-vatten-intervall/v1"` |
| `omrade_id` | text | | Som i begäran |
| `klimatfaktor_intervall`, `initial_forlust_mm_intervall`, `avtappning_mm_h_intervall` | `[lo, hi]` | faktor, mm, mm/h | Intervallen som användes |
| `antal_korningar` | heltal | st | Antal lösarkörningar (extremfall och grenar) |
| `antal_bifurkationsgrenar` | heltal | st | Antal grenar som bifurkationer gav upphov till |
| `max_volymfel_rel` | tal | (relativt) | Största relativa volymfel över körningarna |
| `kortid_s` | tal | s | Analysens körtid |
| `sammanfattning` | objekt | | `antal_noder`, `antal_monoton_garanterad`, `antal_bifurkationspaverkade` (heltal, st; `antal_noder` räknar alla noder, inte bara de i `noder`), `storsta_min_djup_m`, `storsta_max_djup_m` (m) |
| `noder` | lista | | De högst 50 noder med störst `max_maxdjup_m`, störst först |

Nod (`noder[]`): `nod_id` (heltal), `min_maxdjup_m`, `max_maxdjup_m` (m, innesluter nodens största djup),
`monoton_garanterad` (bool), `bifurkationspaverkad` (bool), `paverkande_bifurkationer` (lista av heltal, nod-id
för bifurkationerna i K34).

**Fel:** 400 för tom eller ogiltig JSON, saknat `omrade_id`, intervall som inte är två ändliga tal med
0 ≤ lo ≤ hi, tal som inte är ≥ 0 (`referensregn_mm` ska vara > 0) och `DynamikFel` från lösaren; 404 okänt område;
401 inte inloggad (bara när inloggning är på). Felsvaret är `{"fel": text, "status": heltal}`.
**Behörighet:** inloggning av: öppet lokalt. Inloggning på: inloggat konto (CSRF som för alla POST); är `omrade_id`
ett projekt (demokvarteret) krävs minst rollen `lasare` i projektet (403 annars). Testsvitens områden kräver bara
inloggning.

## K41 – SVF-intervall `floden-dagsljus-svf-intervall/v1` (v1)

Registrerat 2026-11-23 i efterhand (PR #57, ingen formatändring). Ett JSON-svar: monotont garanterat intervall för
himmelsandel (sky view factor, SVF) per markcell när byggnadshöjden är osäker ±σ, i sammandrag, plus största
horisontvinkel på områdets mittrad.

**Anrop:** `POST /api/analys/svf-intervall`. **Skrivs av** `kor_svf_intervall_analys` (`src/webb/api.py`) som kör
`dagsljus.intervall.kor_monoton_svf_intervall` och `horisont_1d_konvext_holje`. Byggnadsceller är celler med
markslag 2 (tak); byggnadshöjden är takhöjden över medianen av markcellerna (10 m om den blir 0).

Begäran:

| Fält | Typ | Enhet | Förval | Innehåll |
| --- | --- | --- | --- | --- |
| `omrade_id` | text | | krävs | Som K40 |
| `osakerhet_byggnad_m` | tal ≥ 0 | m | 1,0 | Byggnadshöjdens osäkerhet, 1 σ |
| `antal_riktningar` | heltal 4–180 | st | 16 | Azimutriktningar i SVF-beräkningen |
| `svf_troskel` | tal 0–1 eller utelämnad | (andel) | ingen | Tröskel för `antal_osakra_celler` |

Svar (200):

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-dagsljus-svf-intervall/v1"` |
| `omrade_id` | text | | Som i begäran |
| `osakerhet_byggnad_m` | tal | m | Som i begäran |
| `antal_riktningar` | heltal | st | Som i begäran |
| `svf_min_medel`, `svf_max_medel` | tal | andel 0–1 | Medelvärde över markceller (utan byggnadsceller) av undre respektive övre SVF-gräns |
| `bredd_max` | tal | andel 0–1 | Största intervallbredd `svf_max − svf_min` över cellerna |
| `antal_osakra_celler` | heltal | st | Markceller med `svf_min` < `svf_troskel` ≤ `svf_max` (0 om ingen tröskel anges) |
| `mittlinje_max_horisont_rad` | tal | rad | Största horisontvinkel mot öst på områdets mittrad |

Rasterna `svf_min` och `svf_max` ingår inte i svaret.

**Fel:** 400 för ogiltig JSON, saknat `omrade_id`, `antal_riktningar` utanför 4–180 eller inte heltal,
`svf_troskel` utanför [0, 1], negativa eller ej ändliga tal och `HorisontFel`; 404 okänt område; 401 när inloggning
är på och kontot saknas. **Behörighet:** som K40 (projekt i `omrade_id` kräver minst `lasare`).

## K42 – Bullerskärmsval `floden-buller-optimering/v1` (v1)

Registrerat 2026-11-23 i efterhand (PR #57, ingen formatändring). Ett JSON-svar: val av `k` bullerskärmar som
minskar överskridandet mest (CELF på en submodulär målfunktion), och valfritt en kontroll av vilka mottagare som
kan växla mellan direkt sikt och refraktiv skuggzon inom ett väderintervall. Indata är färdiga dB-tal från
beräkningen; inget terräng- eller områdesunderlag läses.

**Anrop:** `POST /api/analys/buller-skarmar`. **Skrivs av** `kor_buller_optimering_analys` (`src/webb/api.py`) som
kör `buller.optimering.valj_bullerskarmar_k` och `detektera_akustiska_bifurkationer`.

Begäran:

| Fält | Typ | Enhet | Förval | Innehåll |
| --- | --- | --- | --- | --- |
| `bas_niva_db` | lista av tal, 1–5000 | dB | krävs | Ljudnivå per mottagare utan skärm |
| `skarm_reduktion_db` | objekt, högst 200 | dB | krävs | Skärm-id (text) → lista av dämpning per mottagare, samma längd som `bas_niva_db`; negativa värden räknas som 0 |
| `k` | heltal 1–50 | st | 5 | Antal skärmar att välja |
| `riktvarde_db` | tal ≥ 0 | dB | 55,0 | Riktvärde mottagarna jämförs med |
| `mal` | text | | `"overskridande_db"` | `"overskridande_db"` (minskning av överskridandet, vinst i dB) eller `"antal_godkanda"` (antal mottagare som blir godkända, vinst i st) |
| `avstand_m` | lista av tal, 1–5000 | m | utelämnad | Källa–mottagare-avstånd (> 0). Anges den körs skuggzonsdetektorn |
| `h_kalla_m`, `h_mottagare_m` | tal ≥ 0 | m | 0,5 och 4,0 | Käll- och mottagarhöjd (bara med `avstand_m`) |
| `dc_dz_intervall` | `[min, max]` | 1/s | `[−0,05, 0,05]` | Vertikal ljudhastighetsgradient (bara med `avstand_m`) |

Svar (200):

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-buller-optimering/v1"` |
| `riktvarde_db` | tal | dB | Som i begäran |
| `mal` | text | | Som i begäran |
| `valda_skarmar` | lista | | Skärmarna i vald ordning (tom om inget har nytta) |
| `akustiska_bifurkationer` | lista | | Finns bara när `avstand_m` angavs; en post per mottagare, i samma ordning |

`valda_skarmar[]`: `skarm_id` (text), `marginal_vinst` (vinsten i just detta steg), `kumulativ_vinst`,
`vinst_ensam` (vinsten om skärmen vore ensam), alla tre i målets enhet (dB eller st enligt `mal`), och
`nya_godkanda` (heltal, st mottagare som i steget går från över till under riktvärdet).

`akustiska_bifurkationer[]`: `mottagare_idx` (heltal, index i `avstand_m`), `avstand_m` (m),
`kritisk_dc_dz_per_s` (1/s, gradienten där skuggzonen når avståndet), `skuggzon_bifurkation` (bool, gränsen ligger
inom `dc_dz_intervall`), `monoton_garanterad` (bool, = inte `skuggzon_bifurkation`).

**Fel:** 400 för ogiltig JSON, fel längd eller typ i listorna, NaN/oändligt, `k` utanför 1–50, okänt `mal`,
`BullerOptimeringFel`; 401 när inloggning är på och kontot saknas. Inget 404: det finns inget område.
**Behörighet:** inloggning av: öppet lokalt. Inloggning på: inloggat konto; ingen projektroll krävs eftersom
begäran bara innehåller tal och inga projektdata.

## K43 – Luft-MKN-intervall `floden-luft-mkn-intervall/v1` (v1)

Registrerat 2026-11-23 i efterhand (PR #57, ingen formatändring). Ett JSON-svar: monotont garanterad inneslutning av
MKN-statistik för PM10 när emission och deposition är osäkra. Två extremserier (lägsta emission med högsta
deposition, och tvärtom) ger undre och övre gräns. Indata är en färdig timserie; inget områdesunderlag läses.

**Anrop:** `POST /api/analys/luft-mkn`. **Skrivs av** `kor_luft_mkn_analys` (`src/webb/api.py`) som kör
`luft.kanslighet.kor_monoton_mkn_intervall`.

Begäran:

| Fält | Typ | Enhet | Förval | Innehåll |
| --- | --- | --- | --- | --- |
| `timvis_lokal_ug_m3` | lista av tal ≥ 0, 24–8784 | µg/m³ | krävs | Lokalt haltbidrag per timme |
| `bakgrund_ug_m3` | tal ≥ 0 | µg/m³ | 0 | Regional bakgrund, läggs till |
| `emissionsfaktor_intervall` | `[lo, hi]` | (faktor) | `[0,8, 1,25]` | 0 ≤ lo ≤ hi |
| `depositionsfaktor_intervall` | `[lo, hi]` | (faktor) | `[0,8, 1,25]` | 0 < lo ≤ hi |
| `grans_dygn_ug_m3` | tal ≥ 0 | µg/m³ | 50 | Dygnsgräns |
| `max_dygn_ar` | heltal ≥ 0 | dygn | 35 | Högsta tillåtna antal dygn över gränsen |

Svar (200):

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-luft-mkn-intervall/v1"` |
| `min_dygn_over_grans`, `max_dygn_over_grans` | heltal | dygn | Antal hela dygn (24 timmar; överskjutande timmar räknas inte) med dygnsmedel över gränsen |
| `min_arsmedel_ug_m3`, `max_arsmedel_ug_m3` | tal | µg/m³ | Medelvärde över hela serien |
| `min_percentil_90_4_ug_m3`, `max_percentil_90_4_ug_m3` | tal | µg/m³ | 90,4-percentilen av dygnsmedlen |
| `klarar_mkn_garanterat` | bool | | `max_dygn_over_grans ≤ max_dygn_ar` |
| `bryter_mkn_garanterat` | bool | | `min_dygn_over_grans > max_dygn_ar` |
| `osaker_mkn_grans` | bool | | Varken klarar eller bryter garanterat |
| `emissionsfaktor_intervall`, `depositionsfaktor_intervall` | `[lo, hi]` | faktor | Intervallen som användes |

Svaret säger inte om gränsvärdet i miljöbalkens mening klaras för en plats; percentilen och dygnsantalet är
underlag, och 90,4-percentilen jämförs inte med något värde här.

**Fel:** 400 för ogiltig JSON, `timvis_lokal_ug_m3` med färre än 24 eller fler än 8784 värden, icke-tal eller
NaN/oändligt, intervall som bryter ovan, `max_dygn_ar` som inte är ett heltal ≥ 0, `LuftFel`; 401 när inloggning
är på och kontot saknas. **Behörighet:** som K42 (inloggat konto, ingen projektroll).

## K44 – Artprofil `floden-artprofil/v1` (v1)

Registrerat 2026-11-23 (ekologiläget, BESLUT 2026-11-23). En JSON-fil som beskriver en artgrupps eller en generell
spridningsprofil för `ekologi.konnektivitet` (K45). **Skiss:** profilernas parametrar saknar exakta källhänvisningar
(källa, tabell, sida); de är till för att jämföra planalternativ, inte värden ur en granskad källa. En profil är en
artgrupp eller en generell profil, aldrig fynd av enskilda arter eller lokaler: skyddsklassade artfynd får inte
förekomma i Floden. De fyra standardprofilerna (`ek_insekter`, `barrskogsfagel`, `groddjur`, `pollinerare`) ligger i
`src/ekologi/artprofiler.py`; en egen profil anges som valfri indataroll `artprofil` till lösaren (och ska ha ett id
som inte är en standardprofils). Läses av `ekologi.artprofil_format.artprofil_fran_dict` (strikt: okända och saknade fält
ger fel) och skrivs av `artprofil_till_dict`.

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-artprofil/v1"` |
| `profil_id` | text | | `a–z`, `0–9`, `_`, börjar med bokstav, högst 40 tecken |
| `namn`, `beskrivning`, `kalla` | text | | Icke tomma. `kalla` är fritext och är inte en exakt källhänvisning |
| `habitat_markslag` | lista av heltal | | Ekologikärnans markslagskoder som är habitat (icke tom) |
| `min_area_m2` | tal > 0 | m² | Minsta (effektiva) area för en habitatfläck |
| `spridningsavstand_m` | tal > 0 | m | Spridningsavstånd (p = 0,5 vid detta kostnadsavstånd) |
| `motstand_per_markslag` | objekt | | Kod (text) → motstånd ≥ 1, eller `null` = total barriär (oändligt motstånd) |
| `standard_motstand` | tal ≥ 1 | | Motstånd för markslag som saknas i tabellen (bl.a. okänt markslag) |
| `min_kronhojd_m` | tal ≥ 0 | m | Habitat kräver minst denna kronhöjd (0 = inget krav; kräver vegetation.tif) |
| `motstand_osakerhet_faktor` | `[lo, hi]` | (faktor) | 0 < lo ≤ hi; skalar motståndet i de två extremkörningarna |
| `metodstatus` | text | | Alltid `"skiss"` |

Ekologikärnans markslagskoder: 0 öppen mark/grus, 1 hårdgjord, 2 byggnad, 3 vatten, 4 skog/träd/buskage, 5 våtmark, 6
äng/grönyta. K1:s koder översätts till dessa i `data/ekologi/markslag.toml` (konfiguration, egna val, skiss).

## K45 – Ekologisk konnektivitet `floden-ekologi-konnektivitet/v1` (v1)

Registrerat 2026-11-23 (ekologiläget). Lösaren `ekologi.konnektivitet` (K21, version 1, domän `ekologi`, **global**,
metodstatus `skiss`) räknar kretsteoretisk och grafbaserad konnektivitet (McRae m.fl. 2008; Saura och Pascual-Hortal
2007; Saura och Rubio 2010) för en artprofil (K44) på K1 v2-rutor efter K9. Rutorna läggs som en mosaik på `cellstorlek_m`
(största vegetationshöjd och vanligaste markslag per cell), K1:s markslag översätts enligt `data/ekologi/markslag.toml`
(sha256 ingår i parametrarna och i `ekologi.json`) och celler med vegetation.tif ≥ 0,5 m får trädskikt.

Parametrar: `natverk` (habitatnätverkets id, K47; **förval i demon**) eller `artprofil` (standardprofilens id eller indataroll `artprofil`, föråldrad; exakt en av dem), `cellstorlek_m` (förval 4; för nätverk NMD-lagrets cellstorlek), `anvand_vmi`, `biotop_klasskoder`,
`max_flackar` (förval 40), `max_celler` (förval 250000), `motstand_faktor_intervall` (förval profilens) och
`spridning_faktor_intervall` (förval `[0,8, 1,25]`); de två sista gäller bara `artprofil` och nekas för `natverk`, som har källbaserat intervall (K46). Valfria indataroller (tillagda 2026-11-23, additivt, K45 förblir v1): `nmd` (NMD-lager, uint8, EPSG:3011), `kand_vatmark` (0/1-mask ur VMI, äldre) och `biotopraster` (lokal klassraster för kommunspecifika nätverk). För många fläckar eller celler ger `LosarFel` med text, inte ett
avkortat resultat.

Utdataroller (alla lämnas alltid):

| Roll | Fil | Innehåll |
| --- | --- | --- |
| `stromtathet` | `stromtathet.tif` | float32, värden i [0, 1], EPSG:3011, `cellstorlek_m` stora celler, NaN = nodata. Normaliserad strömtäthet J_u summerad över fläckparen med vikt a_i·a_j·p*_ij (flaskhalsar: högt värde = mycket av förflyttningen passerar cellen). NaN överallt om det finns färre än två habitatfläckar |
| `kansighet` | `kansighet.tif` | float32, ≤ 0, samma rutnät. Viktat medel (samma vikter) av dR_eff/dG per cell: hur mycket det effektiva motståndet ändras om cellens konduktans ökar. NaN överallt om färre än två fläckar |
| `ekologi` | `ekologi.json` | Se tabellen nedan |

Skillnaden före/efter tas med `[[jamforelser]]` på `stromtathet`. Fälten i `ekologi.json`:

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-ekologi-konnektivitet/v1"` |
| `metodstatus` | text | | `"skiss"` |
| `artprofil_id` | text | | Profilens id (K44); för ett nätverk nätverkets id |
| `artprofil` | objekt | | Hela profilen i K44-format, så att resultatet går att återskapa (för ett nätverk huvudscenariots profil; oändligt motstånd skrivs `null`) |
| `parameterkallor` | text | | Artprofil: att parametrarna saknar exakta källhänvisningar. Nätverk: att parametrarna är källbelagda (kommunala modellval) och den biologiska säkerheten låg |
| `cellstorlek_m` | tal | m | Rutnätets cellstorlek |
| `analys_area_m2` | tal | m² | Rutnätets area |
| `antal_flackar` | heltal | st | Habitatfläckar (area ≥ `min_area_m2`) |
| `total_habitat_area_m2` | tal | m² | Summa effektiv habitatarea |
| `ECA_m2` | tal | m² | Equivalent Connected Area, sqrt(Σ a_i a_j p*_ij) |
| `PC` | tal | (andel) | Probability of Connectivity: Σ a_i a_j p*_ij delat med rutnätets area i kvadrat |
| `ECA_intervall_m2` | `[min, max]` | m² | Artprofil: från två extremkörningar (motstånd × hi, spridningsavstånd × lo) och (motstånd × lo, spridningsavstånd × hi). Nätverk: lägsta och högsta ECA mellan nätverkets scenarier (`intervall_typ`); med ett scenario är intervallet en punkt |
| `PC_intervall` | `[min, max]` | (andel) | Samma två körningar |
| `intervall_faktorer` | objekt | | `motstand` och `spridning`: `[lo, hi]` som användes |
| `dPC` | lista | | En post per habitatfläck, störst `dPC_procent` först |
| `markslagsmappning` | objekt | | `sha256` och `version` för `data/ekologi/markslag.toml` |
| `varningar` | lista av text | | T.ex. okänt markslag, saknad vegetation, färre än två fläckar |
| `intervall_typ` | text | | `"faktorer"` (artprofil: två extremkörningar) eller `"kallbaserat"` (nätverk: lägsta och högsta värde mellan nätverkets scenarier). Tillägg 2026-11-23 |
| `resultat_etikett` | text | | `"Potentiell livsmiljö, okänd kvalitet"` för NMD-nätverk (ålder, naturvärde och trädinventering saknas), annars tom. Tillägg 2026-11-23 |
| `natverk` | objekt eller `null` | | Nätverket (K47) som kördes: `habitat_huvud`, `habitatvarianter` (jämförelseurval vid huvudscenariots tröskel: `id`, `klasser`, `ECA_m2`, `PC`, `antal_flackar`), `antagna_motstand` (motstånd med typ antagande, redovisas separat), `valfria_lager`, `natverk_id`, `namn`, `signalarter`, `kalla` (`nyckel`, `referens`, `sida`), `kalla_rad`, `scenario` (huvudscenariot), `scenarier` (`id`, `troskel_m`, `d50_m`, `d50_antagen`, `ECA_m2`, `PC`), `d50_m`, `troskel_m`, `habitat_klasser`, `min_area_m2`, `oandliga_klasser`, `antaganden` (till exempel "p saknas i källan; d50 = tröskel/2"), `saknas`, `biologisk_sakerhet`. `null` för artprofil. Tillägg 2026-11-23 |

`dPC[]`: `flack_id` (heltal), `area_m2`, `effektiv_area_m2` (m²), `centroid_e_m`, `centroid_n_m` (m, EPSG:3011),
`dPC_procent` (hur många procent av PC som försvinner om fläcken tas bort) och dess tre komponenter `dPC_intra_procent`
(fläckens egen area), `dPC_flux_procent` (flöde till och från fläcken) och `dPC_connector_procent` (fläcken som
länk mellan andra). Intervallet är monotont garanterat men säger inget om osäkerheten i profilens parametrar eller i
kartunderlaget. Att ett nytt hus ger ΔECA ≤ 0 gäller exakt på ett givet rutnät; rastreringen av K1 till `cellstorlek_m`
kan i enstaka fall bryta det (API:t varnar då).

### Webb-API `floden-ekologi/v1`

**Anrop:** `POST /api/analys/ekologi`. **Skrivs av** `FlodenApiApp.kor_ekologi_analys` (`src/webb/api.py`) som låter
kärnan köra `ekologi.konnektivitet` före och efter (ett sparat scenario eller projektets förslag) och läser filerna.
Körningen är synkron och återanvänds ur cachen vid samma anrop.

Begäran:

| Fält | Typ | Enhet | Förval | Innehåll |
| --- | --- | --- | --- | --- |
| `omrade_id` | text | | krävs | Projektet (demokvarteret) |
| `natverk` | text | | projektmallens | Habitatnätverkets id (K47), bara NMD-nätverk som går att köra på servern; kan inte kombineras med `artprofil` |
| `artprofil` | text | | – | En standardprofil (K44), föråldrad; svaret varnar |
| `scenario` | text | | projektets förslag | Id på ett sparat scenario (ritat förslag) |
| `max_flackar` | heltal 2–200 | st | 40 | Fler habitatfläckar än så ger 400 |
| `cellstorlek_m` | tal 0,25–100 | m | 4 | Rutnätets cellstorlek; ett för stort antal celler (`max_celler`) ger 400 |
| `antal_flaskhalsar` | heltal 1–20 | st | 5 | Antal flaskhalsar per läge i svaret |
| `flaskhals_avstand_m` | tal ≥ `cellstorlek_m` | m | 20 | Minsta avstånd mellan två redovisade flaskhalsar |

Svar (200):

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `format` | text | | `"floden-ekologi/v1"` |
| `omrade_id`, `scenario` | text | | Som i begäran (`scenario` är `null` för projektets förslag) |
| `metodstatus` | text | | `"skiss"` |
| `artprofil_id`, `parameterkallor` | text | | Som i K45 |
| `natverk` | objekt eller `null` | | Som `natverk` i K45 (från läget efter) |
| `intervall_typ` | text | | Som i K45 |
| `kalla_rad` | text eller `null` | | En rad med signalart och källa, till resultatpanelen |
| `resultat_etikett` | text eller `null` | | Som i K45 |
| `antaganden` | lista av text | | Nätverkets antaganden, till exempel "p saknas i källan; d50 = tröskel/2 (500 effektiva m)." |
| `korning_id` | text | | Körningen som beläggen hör till |
| `cellstorlek_m`, `max_flackar` | tal | m, st | Som användes |
| `fore`, `efter` | objekt | | `ECA_m2`, `ECA_intervall_m2`, `PC`, `PC_intervall`, `antal_flackar`, `total_habitat_area_m2` (som K45) |
| `delta_eca_m2` | tal | m² | `efter.ECA_m2 − fore.ECA_m2` |
| `delta_eca_procent` | tal eller `null` | % | `null` om ECA före är 0 |
| `delta_pc` | tal | (andel) | `efter.PC − fore.PC` |
| `lager` | objekt | | `stromtathet_fore`, `stromtathet_efter`, `stromtathet_skillnad`: `lager_id`, `png`, `utbredning` (lagerreferenser i körningen, flaskhalskartan) |
| `storsta_flaskhalsar` | objekt | | `fore` och `efter`: listor av `e_m`, `n_m` (m, EPSG:3011), `stromtathet` (J, 0–1), störst först, plus `minsta_avstand_m` |
| `dPC_efter` | lista | | `dPC[]` ur K45 för läget efter, högst `max_flackar` poster |
| `varningar` | lista av text | | Lösarens varningar och en varning om ΔECA är positiv |

**Fel:** 400 för tom eller ogiltig JSON, saknat `omrade_id`, okänd `artprofil`, tal utanför intervallen och fel från
lösaren (för många fläckar eller celler, `LosarFel`); 404 okänt projekt eller sparat scenario; 401 inte inloggad (när
inloggning är på). **Behörighet:** som K40 (projektet i `omrade_id` kräver minst `lasare` när inloggning är på).

`GET /api/ekologi/natverk` (tillägg 2026-11-23, `floden-ekologi-natverk/v1`) listar habitatnätverken (K47): `natverk[]` med `natverk_id`, `namn`, `visningsnamn` (till exempel "Barrskogssamband (Nacka/NMD)"), `underlag`, `omfattning`, `korbar`, `korbar_orsak`, `kvalitetskalla`, `signalarter`, `kalla_rad`, `scenarier`, `huvudscenario`, `lokalt`, samt `kommuner[]` med förval per kommun. Ingen behörighet utöver den vanliga läsbehörigheten.

## K46 – Geografi `floden-geografi/v1` (v1)

Registrerat 2026-11-23 (Joels beslut "geografi först", `docs/ARKITEKTUR-DRIFT.md` 0b). K44 och K45 är ekologins
(`docs/BESLUT.md` 2026-11-23) och används inte här. En geografi är ett kartlagt område som projekt byggs i: en **giltig
kärna** (hela området där alla villkor nedan gäller) och en **buffert** runt den. Projekten delar geografins lager och
låser dess version.

**Skrivs av** `floden geografi mappa <geografi-id>` (`src/geografi/mappa.py`, `src/geografi/modell.py`). **Läses av**
`floden projekt skapa --geografi <id>@<version>` (`src/kommuner/projekt.py`) och `floden geografi visa <id>@<version>`.
Geografin definieras i kommunfilen (`[geografier.<id>]` i `data/kommuner/<kommun>.toml`, `docs/KOM-IGANG.md`).

**Mappar** under geo-roten (förval `data/lokal/geo`):

```text
<geo-rot>/<id>/<version>/manifest.json   manifestet (detta kontrakt), ingen tid, inga nycklar, inga sökvägar
<geo-rot>/<id>/<version>/oppenhet.toml   K38, följer lagrens öppenhet
<geo-rot>/_lager/<sha256>/               ett lager, adresserat med trädhashen över sina filer (delas mellan versioner)
```

Trädhashen är sha256 över sorterade rader `<sha256 av filen>  <relativ sökväg>` (en per fil, åtskilda av radbrytning); dolda filer, `oppenhet.toml` och körloggar (`run.json`, K0: starttid och körtid) ingår
inte. Två versioner med samma innehåll i ett lager pekar på samma mapp (lagras en gång).

Manifest (JSON, nycklarna sorterade, UTF-8):

| Fält | Typ | Enhet | Innehåll |
| --- | --- | --- | --- |
| `kontrakt` | text | | `"floden-geografi/v1"` |
| `id`, `version`, `namn`, `kommun` | text | | Id (`[a-z][a-z0-9-]*`), version (`v1`, `v2` ...), visningsnamn, kommunens id |
| `koordinater` | objekt | | `publicering_epsg` (kommunens), `intern_epsg` (3011), `skalfel_langd_procent`, `skalfel_yta_procent` (mätt i kärnans mitt) |
| `karna_3011` | `[x0, y0, x1, y1]` | m, EPSG:3011 | Den giltiga kärnan |
| `gransen_3011` | `[x0, y0, x1, y1]` | m, EPSG:3011 | Yttre gräns (kärnan plus vald buffert): data finns överallt inom den |
| `buffert` | objekt | m | `dagsljus_paverkansradie_m` (= `hojd_max_m / tan(h_min_grader)`), `skyfall_tillrinning_krav_m` (avstånd från kärnan till tillrinningsområdets yttersta cell), `vald_m`, `max_m`, `tillrinning_nar_kanten` (bool: sant betyder att bufferten är för liten) |
| `parametrar` | objekt | | `hojd_max_m`, `h_min_grader`, `upplosning_m`, `ruta_storlek_m` (500), `byggnadskalla` |
| `lager.k1_rutnat` | objekt | | `sha256` (trädhash), `rutor` (`mm_<E>_<N>`), `filer` (`fil`, `sha256`): K1 v2-rutor med DTM (`mark.tif`), DSM/takhöjd, byggnader och markklass |
| `lager.radata` | objekt | | `sha256`, `lantmateriet_manifest_sha256`, `filer`: Lantmäteriets filer och kommunens byggnader (så att ett projekt kan beskära en egen ruta) |
| `lager.nmd` | objekt | | Valfritt. `sha256`, `buffert_m` (2000-5000, ur längsta spridningströskel), `cell_m` (10), `filer`: `nmd.tif` (uint8, EPSG:3011) och `nmd.json`. Skrivs av `floden geografi mappa --nmd <tif>`; mappas en gång per geografi och delas av projekten |
| `lager.vmi` | objekt | | Valfritt. Som `lager.nmd` men `vmi.tif` (0/1-mask, känd våtmark) på NMD-rutnätet; märkt `alder: "äldre"` (inventerad 1981-2005). Skrivs med `--vmi <fil>` |
| `forberakningar` | objekt | | `terrang_karna` (höjdspann, lägsta punkt), `tillrinning` (`cell_m`, `celler`, `yta_m2`, `krav_buffert_m`, `nar_kanten`), `sankgraf` (text: inte förberäknad; byggs och cachas av kärnan, K22, vid första körningen) |
| `kallor` | objekt | | Licens och anmärkning per källa (`lantmateriet`, `byggnader`) |
| `motivering_karna` | text | | Varför kärnan valdes |
| `oppen_for_mcp` | bool | | Sant bara om mappningen gjordes med `--villkor-kontrollerade` |
| `varningar` | lista av text | | T.ex. tillrinning som når kanten vid största bufferten |

**Buffertregler.** Dagsljus: påverkansradien `hojd_max_m / tan(h_min_grader)` (60 m och 5 grader ger 686 m).
Skyfall: hela tillrinningsområdet uppströms kärnan, ur höjdmodellen (D8 med fyllning av sänkor, nedsamplad till
`tillrinning_cell_m`). Bufferten är den större; når tillrinningsområdet kanten av det hämtade området vidgas området
(högst `max_buffert_m`), och räcker inte det står det som varning. Kärnan är giltig när alla projekt i den får full
tillrinning och skuggor utifrån; ett projekt som sticker utanför kärnan får en varning, inget fel.

**Öppenhet (K38).** Lagret `radata` är alltid stängt för MCP. Det härledda (`k1_rutnat`, versionens `oppenhet.toml`) är
stängt tills mappningen gjorts med `--villkor-kontrollerade`; en ny version behövs för att ändra det.

**Version och projekt.** En version ändras aldrig; ny mappning ger ny version (`--version v2`). Ett projekt
(`projekt.json`, `floden-projekt/v1`) bär `geografi` (`id@version`) och `geografi_manifest_sha256` och låser därmed
versionen. `las_manifest` kontrollerar att varje fil i lagren har sin sha256 och stoppar om något ändrats.

**Fel:** geografi eller version som saknas, lager vars sha256 inte stämmer och manifest med fel kontrakt ger `GeografiFel`
(CLI: slutkod 4 med en rad som säger vad som ska göras).

## K47 – Habitatnätverk `floden-habitatnatverk/v1` (v1)

Registrerat 2026-11-23. En TOML-fil per nätverk och källa i `data/ekologi/natverk/<natverk_id>.toml`, strikt läst av
`ekologi.natverk.las_natverk` (okända källnycklar, värden utan `kalla`, `sida`, `typ` eller `sakerhet`, motstånd under 1 och
oändligt motstånd utan `motivering` ger `EkologiFel`). Källnycklarna ligger i `data/ekologi/kallor.toml` och är samma som i
källsammanställningen `docs/ekologi/habitatnatverk-svensk-stadsmiljo.md`. Förval per kommun: `data/ekologi/kommuner.toml`.
Nätverk ersätter artprofiler (K44, som består) som val i webben och API:t. Ett nätverk är ett aggregerat samband med
signalarter, inte fynd av arter; skyddsklassade artfynd får inte förekomma.

| Fält | Innehåll |
| --- | --- |
| `format`, `natverk_id`, `namn`, `underlag`, `beskrivning` | Id = filnamnet utan `.toml` (`a–z`, `0–9`, `-`) |
| `omfattning` | `nmd` (klasserna är NMD-koder, körs på servern med öppen data) eller `biotop` (stadens biotopklasser, inte öppen data, körs bara lokalt; klasserna kan inte översättas rakt av till NMD) |
| `korbar`, `korbar_orsak` | `false` när motståndstabell saknas (Södertälje, Göteborg) |
| `signalarter`, `biologisk_sakerhet`, `biologisk_sakerhet_text`, `huvudscenario` | Signalarter (svenska namn); biologisk säkerhet (förval `låg`) |
| `[kalla]` | `nyckel` och `sida` för nätverket som helhet |
| `[habitat]` | `klasser` (NMD-koder eller biotopnamn): habitatkandidater |
| `[[habitatvariant]]` | Valfritt jämförelseurval (`id`, `namn`, `klasser`, `kalla`, `sida`, `typ`, `sakerhet`); körs vid huvudscenariots tröskel och redovisas bredvid kandidaterna |
| `[[valfritt_lager]]` | Valfritt indatalager som datamodellen förutser men som inte är byggt (`id`, `namn`, `text`, `status`, belägg), t.ex. inventerade gamla ädellövträd som punkter |
| `[min_area]` | `varde_ha`, eller `saknas = true` (med `antagande_m2` om ett antagande används) |
| `[[scenario]]` | `id`, `namn`, `troskel_m` (effektiva meter), `d50_m` om källan anger det |
| `[d50_antagande]` | `regel = "troskel/2"`, `kvot`, `text` ("p saknas i källan; d50 = tröskel/2"), `typ = "antagande"` |
| `[standard_motstand]` | För klasser som saknas i tabellen (märkt antagande) |
| `[[motstand]]` | `klasser`, `varde` (≥ 1, ändligt) eller `oandligt = true` med `motivering`; inget förval är oändligt |
| `[[saknas]]`, `[[kompletterande]]` | Det som saknas i källan; värden som visas men inte används |

Varje värde har `kalla`, `sida` (sida eller tabell enligt sammanställningen), `typ` (`uppmätt`, `litteratur`, `modell`,
`expertbedömning` eller `antagande`; antagande = värdet saknas i källan och är ett uttryckligt Flodenval) och `sakerhet` (`hög`,
`måttlig`, `låg`, som sammanställningen anger den för värdet). Spridningsfunktionen är p(d) = 2^(−d/d50) i kostnadsavstånd;
saknas d50 används `[d50_antagande]` och antagandet märks i `ekologi.json` och i rapporttexten. Tröskeln är ingen hård
avklippning. Markunderlag för NMD-nätverk: indataroll `nmd` (uint8-GeoTIFF i EPSG:3011, `data_inlasning.nmd`); ritade hus
(K9) läggs ovanpå som NMD 51. Äldre känd våtmark (VMI, inventerad 1981–2005): indataroll `kand_vatmark`
(`data_inlasning.vmi`), märkt som äldre. Metodstatus: `skiss`, parametrar källbelagda (kommunala modellval), biologisk
säkerhet låg. K45 förblir v1; ändringarna i K45 (fälten `intervall_typ` och `natverk`, parametern `natverk`, indataroller) är
additiva och bakåtkompatibla.
