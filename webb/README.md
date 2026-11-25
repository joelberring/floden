# Floden webbklient (T10)

Lokalt webbgränssnitt för Floden. Som standard **utan inloggning** (`inloggning = "av"`): servern
lyssnar bara på `127.0.0.1`, ingenting skickas till någon extern tjänst (utom den valfria
bakgrundskartan, se nedan) och resultaten sparas på den egna disken. Med `inloggning = "pa"` (se
`docs/KONTO.md`) visar sidhuvudet *Logga in med Google*, *Inloggad som* och *Logga ut*, alla POST
bär CSRF-huvudet `X-Floden-CSRF` och API:t kräver en session. Enklast: `floden demo` och
`floden webb` (se README, "Visa Floden på din dator"); `floden webb` bygger klienten vid behov.

## Starta lokalt

```bash
# 1. API (i repo-roten)
PYTHONPATH=src python -m webb.api                 # http://127.0.0.1:8765
#    --port 8800, --fragemapp <mapp> (kan upprepas)

# 2. Klient (i webb/)
npm ci
npm run dev                                       # http://127.0.0.1:5173, proxar /api till :8765
```

Exempelfrågorna ligger i `exempel/fragor/`. Dagsljusfrågan behöver det syntetiska demokvarteret:

```bash
PYTHONPATH=src python -m floden.cli dagsljus demo --fore-efter   # bygger data/cache/exempel/dagsljus-kvarter
```

Vattenfrågan (`vatten_a1_uppfyllnad.toml`) använder testsvitens A1 och behöver inget förarbete.

## Frågekatalogen (`#/fragor`)

Läsvy över alla frågor Floden kan besvara, per ämne, med samma ämnen, ord och statusetiketter som steg 3 (`flode/fragekatalog.ts`).
Inget väljs och inget körs här: frågorna väljs och beräkningarna körs i steg 3 i ett projekt, och sidan länkar dit per projekt.
Den äldre Utredningsfrågor-sidan (körning av enskilda K20-filer med planruta och resultatkarta) är borttagen för att det inte ska finnas två frågesidor;
K20-filer körs med `floden fraga kor` och `floden fraga planera`. Webb-API:t (`/api/fragor`, `/api/korningar`) är oförändrat.

## Kartan (`src/karta/`)

Det finns **en** kartmotor: MapLibre GL (`src/karta/kartmotor.ts`), vald framför den tidigare egna
canvas-vyn (`rastervy.ts`, borttagen) för att den ger sanna koordinater, zoom, överlägg, pekarhändelser
och WMS/WMTS utan egen kod. Allt som ritas ligger i **SWEREF 99 18 00 (EPSG:3011)** och räknas om till
WGS 84 med den exakta Gauss-Krüger-omräkningen i `src/sweref.ts` (kontrollerad mot pyproj med 40 punkter,
avvikelse under 1 cm kravet, några mikrometer uppmätt); den linjära approximationen i `karta.ts` är
borttagen. Rasterbilderna från servern läggs med sina fyra hörn omräknade. Ingen kartbakgrund hämtas från
någon extern tjänst som standard.

| Fil | Innehåll |
| --- | --- |
| `kartmotor.ts` | MapLibre-omslaget: rasterlager i ordning, byggnader, ritlager, pekarhändelser, vy |
| `lager.ts`, `lagerpanel.ts` | Lagermodell (grupper, synlighet, täckning, ordning, hopfällning, sparat per projekt, lager som egen vy för en kommande 3D-vy) och panelen |
| `wms.ts` | Underlag från WMS/kakelmall (servern är proxy, `POST /api/kartverktyg/wms/lager`) |
| `snap.ts`, `hit.ts`, `formedit.ts`, `historik.ts`, `geom.ts` | Porterade ritalgoritmer (fäst, träff, hörnredigering, ångra/gör om) |
| `linjeverktyg.ts`, `linje.ts`, `foljkant.ts` | Linje- och ytverktyget (R3): rita, fäst, vinkellås, följ kant, klipp, slå ihop, vänd, förenkla; porterat ur O-Tid @ b0b952f (ursprung i filhuvudena) |
| `egenskaper.ts`, `tangenter.ts` | Fält per lagerroll med enheter; tangentkartan (förval Q W E Skift+W A S X G V Skift+S F T R Z) som kan ändras och sparas |
| `plandok.ts` | Planförslagets dokument: nya hus (i sina ritlager), höjdändringar, rivningar; K9-ändringar för de fria och K50 för objekten |
| `ritlager.ts`, `ritlagerpanel.ts` | Ritlager (K50): roll, synlighet, låsning, valt lager att rita i, lägga till och ta bort lager; panelen i steg 2 (kommer ur `ritlager` i projektinfo, sparas med förslaget) |
| `ritverktyg.ts` | Styrenhet för verktygen och kortkommandona (testbar utan webbläsare) |
| `kartapi.ts` | Anrop till `/api/kartverktyg/...` |
| `skarv.ts` | Skarvfynd (K49) som kartfigurer (yta, punkt, överfallspil), text i panelen, tillämpning av åtgärdsförslaget (R5) |
| `fordrojning.ts` | `fordrojd(f, ms)`: debounce för 3D-förhandsvisningen (150 ms) |

Klienten ritar bara geometri. Kontrollen (giltig form, höjd över 0 och högst 200 m, ligger inom K1-rutorna),
K9, K20 och körningen görs av servern (`src/webb/kartverktyg.py`, `src/karna/ritat.py`). Porterad logik
står med "Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af, ...)" i filhuvudet. Kortkommandon:
Q markera, W rita hus, A lägg till hörn, S ta bort hörn, T fäst, R rät vinkel, Z ångra, Shift+Z gör om,
Enter avslutar, Esc avbryter, Backsteg tar bort senaste punkten, Delete tar bort det markerade.

**3D (R6, `src/vy3d/`).** `tak.ts` (takformer platt, sadel, pulpet, mansard i `extrudera`), `planyta.ts` (ny mark, slänt och murar som ytor), `planinnehall.ts` (urval ur planförslaget och ritlagren), `scen.ts` (`settPlan` bygger bara om planen). I steg 2 finns knappen "3D-förhandsvisning" som visar förslaget i 3D-vyn och uppdateras 150 ms efter senaste ritändring. **Jämna ut skarven** (R5) finns under skarvfynden i steg 2: föreslå, läs före och efter, tillämpa eller avbryt. Alla tal går genom `tal.ts`.

## Sidor och adresser

Ett skal: **samma sidhuvud på alla sidor** (`flode/huvud.ts`: "Floden / <projekt eller sida>", kontoknapp med meny). Stegraden syns bara i ett projekt.

| Adress | Sida |
| --- | --- |
| `#/` | Flödet, steg 1 Projekt: projektlistan (`GET /api/projekt`) och kartan för det valda projektet |
| `#/nytt` | Nytt projekt (hör till steg 1) |
| `#/p/<projekt>/forslag`, `/fragor`, `/resultat`, `/rapport` | Flödets steg 2 till 5 (kod i `webb/src/flode/`, stilen i `flode.css`; se *Flödet* nedan) |
| `#/fragor` | Frågekatalogen (se ovan) |
| `#/om` | Om Floden (`om/sida.ts`): vad Floden kan nu per ämne med metodstatus och testat, arbetsflödet, planen och jämförelsen med andra verktyg (se *Om Floden* nedan), därunder modulerna och *Kontroll av beräkningarna* (A1–A9) |
| `#/matematik` | Matematik och forskning |
| `#/nycklar`, `#/administration` | Nycklar (inloggade) och Administration (admin) |
| `#/arbetsbank/<id>` | Kontrollfallens äldre arbetsbänk (MapLibre), nåbar bara från *Om Floden* (kontrollfallen) |
| `#/forvaltning` | Visas bara när inloggning är på och kontoservern har data för kontot |
| `#/prov/webgpu` | Prov av WebGPU-skuggarna mot serverns facit (`GET /api/prov/webgpu/fall`); ingen länk i menyn, bara superadmin med inloggning, öppen lokalt. Skissläge (f32). Se `docs/WEBGPU-ARKITEKTUR.md` 5 |

**Omdirigeras** (`omdirigering` i `src/rutter.ts`): `#/projekt/<id>` och `#/oversikt/<id>` till `#/p/<id>/forslag`; `#/projekt` och `#/oversikt` till `#/`;
`#/fragor/<ref>` till `#/fragor`; `#/karta[/<projekt>]` till `#/p/<projekt>/forslag`, med `?vy=<vy>` till `#/p/<projekt>/resultat?vy=<vy>`; okända adresser till `#/`.
Det finns ingen start- eller projektsida vid sidan av flödet.

Kontomenyn är densamma på alla sidor. Inget i gränssnittet visar
hakparentes eller påhittade tal; ett test (`tests/webb/test_klient_platshallare.py`,
`src/platshallare.test.ts`) söker igenom klientens text efter `[Ord]`-platshållare.

## Bygg och test

```bash
npm ci                # exakt enligt package-lock.json (CI gör likadant)
npm run typecheck
npm test              # Nodes testkörare: SWEREF-omräkningen mot pyproj och ritalgoritmerna
npm run build
```

API-testerna ligger i `tests/webb/` (`PYTHONPATH=src python -m pytest -q tests/webb`). Skärmbilderna i
`docs/bilder/flode-*.png` (fem steg plus lager/underlag och osäkerhet, 1440x900 och `-smal` 390x844) tas med `python tools/flode_skarmbild.py`; `docs/bilder/webb-*.png` tas med `python tools/webb_skarmbild.py` (playwright och Chrome).

## Flödet (`#/` och `#/p/<projekt>/<steg>`)

Fem steg med en stegrad överst: 1 Projekt, 2 Förslag, 3 Frågor, 4 Resultat, 5 Rapport. Adresstolkningen
(`flode/adress.ts`), statusetiketterna (`flode/status.ts`: Floden svarar, Delvis, Underlag saknas) och
nyckeltalen (`flode/nyckeltal.ts`, saknade tal blir "–") är ren kod med tester (`npm test`).
Ritlogik, scenario-API:erna, 3D-vyn och svepet är de befintliga. Små tillägg på servern:
`GET /api/korningar/<id>/marksiffror?lager=` (samma mark-siffror som projektsidan) och
`GET /api/projekt/<id>/rapporter/<rapport>` (rubriker, påståenden och kontrollpunkter till förhandsvisningen).
**En kartsida, inte två.** Det som fanns bara på den gamla kartsidan bor nu i flödet:
- Steg 2 (`flode/forslag.ts`): ritverktygen i en fast verktygsrad, zoom, lagerpanelen (grupperna Underlag och Geometri via `PanelKrokar.grupper`) och ritlagerpanelen (R2, K50: förslagets lager med roll, Visa, Lås och valt lager; nya hus hamnar i valt byggnadslager, ett låst eller släckt lager går inte att rita i eller markera i; lager med andra roller än Byggnad ritas med linje- och ytverktyget, R3: Rita linje, Rita yta, Klipp, Slå ihop, Vänd, Förenkla, Följ kant, och egenskapspanelen för det markerade objektet; tangenterna kan ändras under Tangenter), WMS och kakelmall (`flode/underlag.ts`), sparade förslag, info om förslaget (`flode/forslagtext.ts`). Lagerlogiken delas med steg 4 genom `BasLager` (`flode/kartdel.ts`, `flode/rasterlager.ts`).
- Steg 4 (`flode/resultat.ts`): 3D, värdeavläsning, tak-kryssruta, teckenförklaring och *Visa osäkerhet*: K40 `/api/analys/intervall` och K41 `/api/analys/svf-intervall` som `[min; max]` med liten text under talet, och K36 `/api/analys/bifurkationer` som lager med känsliga överfall (`flode/osakerhet.ts`, HTML i `flode/panelhtml.ts`). Intervallen tar bara `omrade_id` och gäller därför projektets markmodell (läget före), inte det ritade förslaget; panelen säger det.
- Avtappning (mm/h) för skyfall ställs in i steg 3 (`flode/tillstand.ts`, `lasAvtappning`).
- **Tal** skrivs alltid med decimalkomma och smalt hårt mellanrum (U+202F) som tusentalsavskiljare via `src/tal.ts` (`formatera`, `formateraHogst`, `talFormat`) och `flode/nyckeltal.ts`. Ett test (`tal.test.ts`) söker i flödets, kartans och 3D-vyns källkod efter `toFixed`, `toLocaleString` och `Intl.NumberFormat`, och `flode/osakerhet.test.ts` söker i de renderade panelerna efter `\d\.\d`. Arbetsbänkens äldre `main.ts` (`#/arbetsbank/<id>`) räknas inte in.
- Kartstilen ligger i `src/karta/karta.css` (ersätter `kartverktyg.css`).

Fel visas med en handling (`flode/felhandling.ts`: inloggning, behörighet, nätverk, server; ren logik med tester). Steg 3 har en fast handlingsrad (`flode/korlage.ts`); utan ritat förslag pekar den till steg 2 och till projektets befintliga resultat, och har knappen "Räkna läget idag" bredvid "Rita ett förslag" (U5a): den kör projektets befintliga läge utan förslag (`korLageIdag` i `karta/kartapi.ts`, `startaLageIdag` i `flode/korning.ts`) och visar uppskattad tid ur planen (`uppskattadTidText`). Resultatet visas i steg 4 under källan "Läget idag" med bara vyn Före (ingen skillnad, inga nyckeltal); rapporten kräver före och efter.
Om Floden, Matematik och forskning och Frågekatalogen nås via kontomenyn.

**Webbläsarprov av ritverktygen** (R1-R7): `python tools/ritverktyg_prov.py` (Playwright och Chrome; kräver `floden demo`, `floden webb --port 8795` och byggd webb). Riktiga musklick, tangenter och beröring; skärmbilder i `docs/bilder/ritverktyg/`; resultat och fynd i `docs/design/RITVERKTYG.md` avsnitt 11. Pekskärm (R7): håll fingret stilla mot en markerad figur för att flytta den; Klar, Backa en punkt och Avbryt ersätter Enter, Backsteg och Esc.

**Geografiprojekt (R8).** Bakgrundskartan är OpenStreetMap (`karta/osm.ts`): rasterplattor från `tile.openstreetmap.org` enligt OSM:s tile usage policy (bara det som syns, ingen förhämtning, inget hämtas när lagret är släckt, webbläsarens cache-huvuden gäller, `Referer` med sidans ursprung via `Referrer-Policy: strict-origin-when-cross-origin`, zoom högst 19, synlig attribution "© OpenStreetMap-bidragsgivare"). Förvald i Nytt projekt och i projekt i en geografi utan ortofoto; avstängbar med ett kryss på kartan (`flode/osmval.ts`), valet sparas. Servern behöver `https://tile.openstreetmap.org` i CSP för `img-src` och `connect-src` (`deploy/Caddyfile`, `docs/DRIFT.md`). Markmodellen som lager: `GET /api/kartverktyg/<projekt>/geometri/{skuggning,hojdkurvor,markhojd}.png` (en bild över projektet, utbredning i `X-Floden-Utbredning`) och markhöjden i en punkt `GET /api/kartverktyg/<projekt>/hojd?e=&n=` (`flode/markhojd.ts`); samma behörighet som övriga projektdata. Varningar visas som sammanfattning och hopfälld lista per slag (`flode/varningar.ts`). Steg 2 har "Spara förslag" och en sparstatus (`sparStatus` i `flode/forslagtext.ts`); "Välj frågor" sparar först.
Prov mot ett påhittat geografiprojekt: `python tools/geografi_prov_server.py --port 8796`, sedan `python tools/ritverktyg_prov.py --bara geografi` (och `--bara nytt` för Nytt projekt med påhittat svar och 32 varningar).

## Alla analyser som kartlager (steg 4, "Visa")

Under ämnesflikarna i steg 4 listar väljaren **Visa** varje roll körningen har (`flode/analyslager.ts`: `lagerVal`), med före, efter och skillnad för den förändring som visas; kontrollager (avskuren horisont, byggnad utan känd höjd) ligger i en egen grupp och har ingen skillnad. Skillnad för roller som frågan inte jämför själv är efter minus före (`harledd`, `skillnad_mosaik` på servern). Skalor och teckenförklaring kommer ur serverns lager (`resultatlager.py`, `PALETTER`; samma färger i `analyslager.ts`, ett test jämför). Värdet under pekaren följer vald roll (`/api/korningar/<id>/avlasning`).
Vektorlager (kryssrutor): **Sänkor med volym** och **Flödesvägar** (avrinningsnät ur markmodellen, `src/sankgraf/avrinningsnat.py`; `?min_yta_m2=` styr minsta tillrinning, förval 500; sänkor tar `?min_volym_m3=` och `?min_djup_m=`; `GET /api/korningar/<id>/sanka?e=&n=&scenario=` ger den sammanhängande vattenytans mått) för skyfall, **Fasadpunkter (VSC)** för sol, via `GET /api/korningar/<id>/vektor/<scenario>/<sankor|flodesvagar|vsc>` (GeoJSON i EPSG:3011, samma behörighet som körningens övriga resultat, `src/webb/analysvektor.py`). Kartmotorn ritar dem som `analys-*`-lager (`settAnalysVektor`) med text vid hovring och etiketter med volym. Alla tal via `tal.ts`.
Prov: `python tools/analyser_prov.py [--geo http://127.0.0.1:8796]` (kräver `floden demo`, `floden webb --port 8795`, byggd webb; skärmbilder i `docs/bilder/analyser/`).

## Om Floden (`#/om`)

Sidan säger vad Floden kan nu (per ämne: skyfall, dagsljus och sol, ekologi, luft, buller; metodstatus, testat, begränsningar; arbetsflödet), vad planen är (5 till 10 punkter ur `PLAN.md`) och hur Floden förhåller sig till andra verktyg. Innehållet genereras, så att det hålls aktuellt:

```
data/om/floden.toml  ->  python tools/generera_om.py  ->  docs/OM-FLODEN.md
                                                      ->  webb/src/om/data.generated.ts  ->  webb/src/om/sida.ts
```

`tools/generera_om.py --kontrollera` ger felkod 1 om filerna glidit isär. Metodstatus kommer ur metodregistret (`data/mcp/metodstatus.toml`), aldrig ur datafilen. Alla tal visas via `tal.ts` (talen står som fält i datafilen, inte i löptexten); `om` ingår i kontrollen av att ingen kod formaterar tal själv (`tal.test.ts`).

Jämförelsen har tre skilda delar. **A. Uppmätt i Floden** (Floden mot Floden: WebGPU mot CPU, hybrid mot full 2D, lokal mot full omräkning, körtider), där varje tal har källa i repot och `tests/gemensamt/test_om_floden.py` kontrollerar körtider och GPU-tider mot `docs/benchmark/*.json` och att beläggstexterna står i sina källfiler. **B. Offentliga uppgifter om andra verktyg** (SCALGO Live, MIKE 21 FM, InfoWorks ICM, ClimateStudio, Accelerad, Honeybee-radiance, SoundPLAN, CadnaA, Circuitscape, Linkage Mapper, Conefor): bara det som står på tillverkarens eller projektets egna sidor, med länk och hämtdatum; licens, pris och körtid anges bara om de är publicerade, annars står att de inte fanns på sidorna som lästes. **C. Det som inte är mätt**: det finns ingen jämförande mätning av samma fall i Floden och i något annat verktyg, och sidan anger ingen faktor mot någon produkt. Generatorn avvisar orden "snabbare än", "fortare än", "bättre än" och "effektivare än" utanför B och C. Ett jämförelseprov är föreslaget i `PLAN.md` (J1). Datafilen och de hämtade uppgifterna ska uppdateras för hand; kör om `tools/generera_om.py` efteråt.
