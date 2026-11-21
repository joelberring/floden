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

## K1 – Markmodell-ruta (v1)

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
bland basrutornas `recept.json`, annars fel. Ändringstyper utan K3-motsvarighet (`byggnad_bort`,
`byggnad_hojd`, `vegetation`, `markhojd` med `delta_m`) ger fel. Nyckeln beror på de utplattade
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
