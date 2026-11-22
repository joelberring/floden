# 3D-vyn (Three.js)

3D-vyn är en egen vy i kartans lagerpanel (lagret *3D-vy*, flikarna *Karta/3D*). Den visar kvarteret
i perspektiv och ritar bara det Floden redan har räknat.

## Val: egen vy, inte ett lager i MapLibre

Three.js ritar i en egen canvas som ersätter kartan (`egenVy` i lagermodellen). Skäl: kameran
får egen perspektiv och arbetar i SWEREF-meter med exakta höjder (ingen Mercator-förvrängning),
två scener kan ritas för svep och sida vid sida, och Three.js (ca 530 kB) laddas först när man
öppnar 3D (dynamisk import) så att kartsidan inte blir tyngre. Kameran följer kartan: samma mitt och
meter per pixel vid start (`webb/src/vy3d/koord.ts`), därefter tiltas den.

## Data (server: `src/webb/vy3d.py`, format `src/webb/vy3d_format.py`)

Allt under `GET /api/3d/<projekt>/...`, byggt av `karna.geometri` (K1 v2) och körningens sparade lager:

| Väg | Innehåll |
| --- | --- |
| `<projekt>` | utbredning, origo, rutnät och nivåer, höjdspann |
| `terrang/<lod>/<tx>/<ty>` | höjdfält 129 x 129 hörn, uint16 kvantiserat |
| `byggnader?korning=&scenario=` | fotavtryck och höjder ur scenariots byggnader; status ny/ändrad/bort mot bas |
| `mark/<körning>/<lager>/<lod>/<tx>/<ty>` | cellvärden ur lagrets `.npy` (tak blir "inget värde") |
| `fasad/<körning>/<scenario>?analys=&jamfor=` | VSC-punkter (K11) per våning, skillnad bara där punkten finns i båda |
| `korning/<id>` | lager, skalor och tidsserier som körningen har |

Formatet `floden-3d/v1`: "FL3D", rubrikens längd, JSON-rubrik, sedan 8-byte-justerade typade delar;
höjder och värden är uint16 (65535 = inget värde). Servern cachar i minnet och på disk per innehåll.

## Resultat i 3D

Soltimmar och vattendjup följer samma färgskala och takmask som 2D (skalan hämtas från servern, inte
från klienten). VSC har fast skala 0 till 40 %. Vattnet ritas som genomskinliga plattor vid mark + djup
där djupet är minst 0,02 m. Inga skuggor beräknas i Three.js. Hovring och klick ger värde för före,
efter och skillnad som i kartan. Ett ritat hus räknas om i klienten med samma takregel som servern
(tak = median av markhöjd + husets höjd) och är ett förslag, inte ett beräknat resultat.

## Rutnät och detaljnivå för riktiga områden

Fast rutnät i EPSG:3011: nivå n har cellstorlek 2^n m och rutor om 128 x 128 celler (hörn delas, kjolar
döljer sprickor mellan nivåer). Klienten väljer nivå efter avstånd till kameran (kvadträd), hämtar bara
synliga rutor, och servern nedsamplar (blockmedel) till nivån. Ett område på flera km kräver därmed
bara några tiotal rutor åt gången. Byggnader hämtas i dag för hela området i ett svar.

## Begränsningar

- Vegetation ritas inte. Luft (ingen vindfältsdata i resultaten, bara en höjd) och buller (ingen
  fasadresultat) visas som otillgängliga lager med förklaring.
- Vattnets tidsreglage är förberett (`djup_t<minut>`-lager) men demon har bara maxdjup.
- Rutor utan data i ett lager ger HTTP 404 (en rad i webbläsarens konsol, hanteras av klienten).
- Provat med programvaru-WebGL (SwiftShader), inte med grafikkort.
- Färger: datafärger ritas utan färghantering och tonmappning (`vy3d/farghantering.ts`), så en färg i 3D är exakt skalans RGB; `tools/webb_skarmbild.py --3d-farg` läser av pixlar mot skalan.
- Mäthandtag `window.__floden3d` används av `tools/webb_skarmbild.py --3d`.
