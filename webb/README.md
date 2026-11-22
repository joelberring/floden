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

## Sidan Utredningsfrågor (`#/fragor`)

1. **Välj fråga.** Listan är frågefilerna (K20, `.toml`/`.json`) i frågemapparna. En fil som inte går att
   tolka visas som ogiltig med kärnans felmeddelande; saknad indata visas tillsammans med frågefilens
   inledande kommentar (där står kommandot som bygger den).
2. **Plan.** Kärnans `planera`: antal jobb, hur många som redan finns i cachen (K22) och hur många som
   ska köras, per analys, med rutor ritade i SWEREF-koordinater (mörk = allt i cachen).
3. **Kör.** Körningen går i en bakgrundstråd i API-processen, ett jobb i taget (ingen extern kö). Status
   och framsteg (antal jobb vars nyckel nu finns i cachen) hämtas var 0,7 s.
4. **Resultat.** Varningar från lösarna överst, kartvy med lager per analys (bas, scenario, skillnad;
   samma färgskala för bas och scenario), mätvärden per jobb (scenario och ruta), jämförelsetabell
   (`efter − före`) och förhandsvisning av CSV/Parquet-filer. Till varje lager finns länkar till
   GeoTIFF-filerna (full upplösning, en per ruta).

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
| `plandok.ts` | Planförslagets dokument: nya hus, höjdändringar, rivningar, som K9-ändringar |
| `ritverktyg.ts` | Styrenhet för verktygen och kortkommandona (testbar utan webbläsare) |
| `kartapi.ts` | Anrop till `/api/kartverktyg/...` |

Klienten ritar bara geometri. Kontrollen (giltig form, höjd över 0 och högst 200 m, ligger inom K1-rutorna),
K9, K20 och körningen görs av servern (`src/webb/kartverktyg.py`, `src/karna/ritat.py`). Porterad logik
står med "Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af, ...)" i filhuvudet. Kortkommandon:
Q markera, W rita hus, A lägg till hörn, S ta bort hörn, T fäst, R rät vinkel, Z ångra, Shift+Z gör om,
Enter avslutar, Esc avbryter, Backsteg tar bort senaste punkten, Delete tar bort det markerade.

## Övriga sidor

| Adress | Sida |
| --- | --- |
| `#/` | Start: tre steg och modulernas status (`GET /api/moduler`) |
| `#/projekt` (alias `#/oversikt`) | Demokvarteret med siffror ur körningarna (`GET /api/projekt`) |
| `#/karta/<projekt>?vy=<vy>` | Kartan: lagerpanel, rita planförslag, före, efter, skillnad, svep, sida vid sida, avläsning |
| `#/fragor` och `#/fragor/<ref>` | Utredningsfrågor (se ovan) |
| `#/om` | Om beräkningarna: modulerna och *Kontroll av beräkningarna* (A1–A9) |
| `#/arbetsbank/<id>` | Kontrollfallens äldre arbetsbänk (MapLibre), nåbar bara från *Om beräkningarna* |
| `#/forvaltning` | Visas bara när inloggning är på och kontoservern har data för kontot |

Menyn är densamma på alla sidor och blir en *Meny*-knapp under 1100 px. Inget i gränssnittet visar
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
`docs/bilder/webb-*.png` tas med `python tools/webb_skarmbild.py` (playwright och Chrome).
