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
`kalla_vaningar`, `standard`, `andring`).

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
- **Källor i `recept.json`:** `byggnadskandidater` (sha256 av kandidaternas kanoniska JSON), `byggnadskalla` (sha256 av
  källfilen, satt av CLI:t `--byggnader`), `laser_klasser`, `vegetation_hojd`; byggnadsregistret, terrängen och
  vegetationen finns som utdata (sha256) i `run.json`.
- **Läs-API:** `karna.geometri` – `las_byggnader(ruta)`, `las_terrang`, `las_ytmodell(ruta, vegetation=False)`,
  `las_vegetation`, `las_mark`, `las_markklasser` (se nedan). `las_ytmodell` (DSM) = `max(terräng, tak i polygonerna,
  terräng + vegetationshöjd)` och har **inga** öppningar (en port genom ett hus är ingen öppning i dagsljus/buller).
- **Markklass per domän:** `data/markklasser.toml` har per markslagskod `namn` och tabellerna `[vatten]`, `[luft]`,
  `[buller]`, `[dagsljus]`. Vatten har `initial_faktor` och `ksat_faktor` (samma som `mark_forluster`, ett test håller
  dem lika); de andra är tomma tills respektive domän lägger till egna kolumner. `Markklasstabell.raster(markslag,
  doman, kolumn)` ger ett rasterlager; saknad kolumn för en förekommande kod ger fel. Filens sha256 hör hemma i recept.

## K2 – Sänkgraf (v1)

`sankgraf.parquet`, en rad per nod:

| Kolumn | Typ | Betydelse |
| --- | --- | --- |
| `nod_id` | int64 | Unik inom grafen |
| `forald_id` | int64 / null | Noden som bildas när denna slås ihop med sin syster |
| `barn_id` | list<int64> | Tomt för lövsänkor |
| `overfall_rh2000` | float64 | Nivån där noden svämmar över |
| `overfall_x`, `overfall_y` | float64 | Överfallspunkten |
| `ut_till_id` | int64 / null | Noden vattnet rinner till vid överfall (null = ut ur området) |
| `volymkurva` | list<struct<z, v_m3>> | Volym som funktion av nivå, monotont ökande |
| `ytkurva` | list<struct<z, a_m2>> | Vattenytans area som funktion av nivå |
| `avrinningsyta_m2` | float64 | Yta som rinner direkt till noden |
| `persistens_m` | float64 | Skillnad mellan överfallsnivå och botten |
| `osakerhet_m` | float64 | Osäkerhet i överfallsnivån |

`flodesvagar.gpkg`, lager `flodesvagar`: linjer från varje cellgrupp till sin nod, med `gangtid_s` vid referensdjup.
`cell_till_nod.tif`: COG int64 med nod-id per cell.

Tilläggsfiler (bakåtkompatibla, 2026-11-21) som `skriv_sankgraf_k2` skriver så att grafen kan läsas
tillbaka exakt med `sankgraf.k2_las.las_sankgraf_k2`: `sankgraf_topologi.json` (`ut_lov_id`,
`overfall_celler`), `mark_bearbetad.tif` (markhöjd med brytlinjer och öppningar inbrända, float32)
och `markslag.tif`. Läsare som bara behöver kontraktets filer kan bortse från dem. Negativa värden i
`cell_till_nod.tif` är markörer, inte nod-id.

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

## K21 – Lösare (v1)

Python-gränssnitt `karna.losare.Losare`. Se `docs/ANSLUTA_LOSARE.md`.

| Del | Krav |
| --- | --- |
| `namn` | `<doman>.<namn>`, domän ∈ {vatten, luft, buller, dagsljus, geometri, ovrigt} |
| `version` | Sträng. Höjs när resultatet kan ändras för samma indata. Ingår i nyckeln |
| `validera_parametrar(p)` | Returnerar fullständiga parametrar med standardvärden; okända ger `LosarFel` |
| `paverkansradie_m(p)` | `None` = globalt jobb; tal ≥ 0 = rutvisa jobb, nyckeln beror bara på rutor närmare än radien |
| `kor(kontext)` | Skriver i `kontext.utmapp`, returnerar `LosarUtdata(filer, matvarden, varningar)`. Får parametrarna i kanonisk form (samma som hashades). Rutvisa jobb ser bara rutorna i nyckeln |
| Registrering | `src/<paket>/floden_losare.py` med `LOSARE: list[Losare]`, upptäcks automatiskt |

**Jobbnyckel** = sha256 av kanonisk JSON (sorterade nycklar, flyttal med `repr`, heltalsvärda
flyttal som heltal, NaN förbjudet) över: lösarens namn och version, validerade parametrar,
sha256 för extra indata, radie, ruta, `[ruta_id, rutans innehållshash]` för berörda rutor och
nycklarna för de jobb man beror på.

**K9 på K1-rutor:** kedjan plattas ut (`karna.scenario.platta_ut`) och översätts till en K3-lapp
(`karna.k9_till_k1`, samma tolkning som `vatten.scenario_lapp`). Alla `bas_recept` i K9 måste finnas
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
