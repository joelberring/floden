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

Klienten ritar bara geometri. Kontrollen (giltig form, höjd över 0 och högst 200 m, ligger inom K1-rutorna),
K9, K20 och körningen görs av servern (`src/webb/kartverktyg.py`, `src/karna/ritat.py`). Porterad logik
står med "Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af, ...)" i filhuvudet. Kortkommandon:
Q markera, W rita hus, A lägg till hörn, S ta bort hörn, T fäst, R rät vinkel, Z ångra, Shift+Z gör om,
Enter avslutar, Esc avbryter, Backsteg tar bort senaste punkten, Delete tar bort det markerade.

## Sidor och adresser

Ett skal: **samma sidhuvud på alla sidor** (`flode/huvud.ts`: "Floden / <projekt eller sida>", kontoknapp med meny). Stegraden syns bara i ett projekt.

| Adress | Sida |
| --- | --- |
| `#/` | Flödet, steg 1 Projekt: projektlistan (`GET /api/projekt`) och kartan för det valda projektet |
| `#/nytt` | Nytt projekt (hör till steg 1) |
| `#/p/<projekt>/forslag`, `/fragor`, `/resultat`, `/rapport` | Flödets steg 2 till 5 (kod i `webb/src/flode/`, stilen i `flode.css`; se *Flödet* nedan) |
| `#/fragor` | Frågekatalogen (se ovan) |
| `#/om` | Om beräkningarna: modulerna och *Kontroll av beräkningarna* (A1–A9) |
| `#/matematik` | Matematik och forskning |
| `#/nycklar`, `#/administration` | Nycklar (inloggade) och Administration (admin) |
| `#/arbetsbank/<id>` | Kontrollfallens äldre arbetsbänk (MapLibre), nåbar bara från *Om beräkningarna* |
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
Om beräkningarna, Matematik och forskning och Frågekatalogen nås via kontomenyn.
