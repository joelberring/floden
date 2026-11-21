# Dagsljus (fas 1): horisontprofiler, soltimmar, SVF och VSC

Kod: `src/dagsljus/`. Tester: `tests/dagsljus/` och `floden testsvit --grupp E`. Demo och mätning:
`floden dagsljus demo`, `floden dagsljus bench`. Licens GPL v3. Inget importeras från `src/luft` (MIT).

Fas 1 räknar **direkt himmelsljus och direkt sol** ur ett höjdfält. Reflekterat ljus, daylight factor (DF) i
typrum och fönster-/glasningsmodeller hör till senare faser (se "Begränsningar").

## 1. Konventioner

- Koordinater EPSG:3011, höjder RH 2000, enheter i meter och radianer (`h_rad`, `x_m`).
  Grader bara där namnet säger det (`h_min_grader`).
- Azimut phi: norr = 0, medurs (Ö = pi/2, S = pi). Riktning k av K: phi_k = 2 pi k / K.
- Höjdfält (`Hojdfalt`): DSM = mark + byggnader, rad 0 = norr, cell (i, j) har mittpunkt
  x = x_min + (j + 0,5)·cell, y = y_max - (i + 0,5)·cell. NaN/inf i fältet ger fel (inga gissningar).
- Tid: UTC (`numpy.datetime64`). Ett "dygn" börjar vid lokal medelsolmidnatt (00:00 UTC minus lon/15 h) så att
  inget dagsljus delas av en dygnsgräns.

## 2. Solposition (`solposition.py`)

NOAA:s lågprecisionsalgoritm (Meeus kap. 25: medellängd, anomali, ekvationen för medelpunkten, skenbar längd,
skevhet med nutationskorrektion, tidsekvation). Geometrisk solhöjd; refraktion (Saemundsson) är ett val
(`refraktion`) och av som standard. lat/lon ur EPSG:3011 med pyproj (redan beroende).

Mätt mot NREL SPA (pvlib 0.16.1, `nrel_numpy`, Stockholm 59,33 N 18,07 E, 2000–2040, 37-minuterssteg):
största avvikelse **0,013 grader** i höjd och **0,016 grader** i azimut. Kända värden (tester):
högsta solhöjd = 90 - lat + deklination: sommarsolstånd 54,1 grader, vintersolstånd 7,2 grader,
vårdagjämning 30,6–30,7 grader (deklinationen ändras 0,4 grader/dygn, så dygnets maximum ligger 0,1 grader från
30,67). lat/lon-transformen är kontrollerad mot meridianbågen på centralmeridianen (GRS80, egen kvadratur).

Solläget beräknas en gång per ruta (rutans mittpunkt). Över 550 m ändras lat/lon med 0,005 grader; det är
försumbart mot interpolationsfelet nedan.

## 3. Höjdfältsmodell och horisontprofil (`horisont.py`)

Höjdfältet är ett **prismafält**: varje cell är en pelare med konstant höjd. Från en punkt (x, y, z0) i
riktning phi är horisontvinkeln

    h(phi) = max(0, max över celler c som strålen korsar: atan((z_c - z0) / s_c)),

där s_c är det horisontella avståndet till den punkt där strålen går **in** i cellen (för en pelare ger
ingångspunkten störst vinkel). Det är exakt för prismor: en vägg vars yta ligger på en cellgräns ger exakt
atan(H/d) (testat: fel 3e-16 rad). Startcellen hoppas över. Strålen avbryts vid `max_avstand_m`; strålar som
lämnar fältet före det flaggas (`trunkerad`). h klipps vid 0: en horisontell yta ser bara himmel över
horisontalplanet, så fallande terräng ger inte negativ horisont. En stråle som korsar två celler exakt i
hörnet (|tMaxX - tMaxY| < 1e-9 cellenheter) går diagonalt utan att räkna mellancellen (genomskärning noll).

Tre implementationer med samma definition:

1. **`horisont_referens`**: vektoriserad Amanatides–Woo-genomgång, cell för cell, godtyckliga punkter. Facit.
2. **`horisont_riktning_ruta`** (mark): alla cellmittpunkter i en riktning. Alla mittpunkter ser samma relativa
   cellföljd, som därför beräknas en gång (`stralsekvens`) och tillämpas som förskjutna skivor
   (`max((z[i+di, j+dj] - z[i, j]) / s)`), radblockade för cache. Stöd för beräkningsfönster. Stewarts (1998)
   snabba horisontalgoritm är **inte** implementerad; skivsvepet är en enklare metod med kostnaden
   O(celler · steg per riktning) och ingen datastruktur per stråle.
3. **`horisont_mipmap`** (fasadpunkter, godtyckliga punkter): hierarkisk genomgång över en max-pyramid
   (nivå l = 2^l x 2^l celler). Ett block hoppas över när (blockets högsta cell - z0) / (avstånd till blockets
   ingång) inte överstiger bästa vinkeln hittills (alla celler i blocket ligger längre bort och är lägre);
   annars delas blocket. Cellnivån utvärderas exakt, så resultatet är detsamma som referensens.

Mätt överensstämmelse: skivor mot referens i cellmittpunkter 6e-8 rad (utdata lagras som float32);
mipmap mot referens **9,9e-16 rad** (3 000 slumpmässiga strålar, 4 000 i testet, inkl. axelparallella och
diagonala), identiska trunkerings- och inuti-hinder-flaggor.

Punkter som ligger i en cell vars höjd är större än punktens z0 (`inuti_hinder`) flaggas; lösaren lämnar då VSC
tom i stället för att räkna. Punkter exakt på en cellgräns räknas höra till cellen åt öster/söder; ett hinder
på avstånd noll skymmer helt.

## 4. SVF för horisontell yta (`matt.svf_fran_horisont`)

Med isotrop radians över himlen är SVF = (1/pi) Int Int cos(t) sin(t) dt dphi över himlen ovanför horisonten,
zenitvinkel t från 0 till pi/2 - h:

    Int_0^{pi/2-h} sin t cos t dt = (1/2) cos^2 h  =>  SVF = (1/2pi) Int cos^2 h dphi = 1 - (1/2pi) Int sin^2 h dphi.

Diskret: SVF = 1 - (1/K) Sum_k sin^2 h_k. Fri yta (h = 0) ger exakt 1.

### Gaturum (oändligt långa väggar, höjd H)

En vägg på vinkelrätt avstånd d. En stråle vinkeln a från väggens normal träffar den på s = d / cos a, så
tan h = H cos a / d och sin^2 h = H^2 cos^2 a / (d^2 + H^2 cos^2 a). Väggen täcker a i (-pi/2, pi/2):

    Int sin^2 h da = pi - d^2 Int da / (d^2 + H^2 cos^2 a) = pi - pi d / sqrt(d^2 + H^2) = pi (1 - cos b),

med b = atan(H/d) (använd Int_{-pi/2}^{pi/2} da/(d^2 + H^2 cos^2 a) = pi / (d sqrt(d^2 + H^2))). Två väggar på d1 och d2:

    SVF = 1 - (1/2pi) pi [(1 - cos b1) + (1 - cos b2)] = (cos b1 + cos b2) / 2.

Mitt i en gata med bredd W: d1 = d2 = W/2, SVF = cos(atan(2H/W)) = 1 / sqrt(1 + (2H/W)^2). Det är Okes (1981)
gaturumsformel (Oke, T.R., "Canyon geometry and the nocturnal urban heat island", J. Climatol. 1, 237–254).

## 5. VSC under mulen CIE-himmel (`matt.vsc_fran_halvplansprofil`)

CIE:s mulna himmel: L(g) = Lz (1 + 2 sin g) / 3, g = elevation (Lz = 1 nedan).

**Normering N** (horisontell belysning under oskymd himmel, BRE:s definition):

    N = Int_0^{2pi} Int_0^{pi/2} L(g) sin g cos g dg dphi = 2pi (1/3) (1/2 + 2/3) = 7 pi / 9.

**Vertikal fasad** med utåtriktad normal vid azimut phi_n: infallsvinkelns cosinus är cos g cos(phi - phi_n), rymdvinkel
cos g dg dphi:

    E_v = Int_{phi_n-pi/2}^{phi_n+pi/2} Int_{h(phi)}^{pi/2} L(g) cos^2 g cos(phi - phi_n) dg dphi,    VSC = E_v / N.

**Inre integralen i sluten form.** Med Int cos^2 g dg = g/2 + sin(2g)/4 och Int sin g cos^2 g dg = -cos^3 g / 3:

    I(h) = Int_h^{pi/2} (1 + 2 sin g)/3 cos^2 g dg = (1/3) [ pi/4 - h/2 - sin(2h)/4 + (2/3) cos^3 h ].

I(0) = (1/3)(pi/4 + 2/3), I(pi/2) = 0. Alltså VSC = (1/N) Int_{-pi/2}^{pi/2} I(h(phi_n + psi)) cos psi dpsi.

**Oskymd fasad:** Int cos psi dpsi = 2, så E_v = 2 I(0) = (2/3)(pi/4 + 2/3) = 0,9681 och
**VSC_oskymd = 0,9681 / (7 pi / 9) = 0,3962 (39,6 %)**, normeringen i BRE.

Diskret: mittpunktsregel i halvplanet, psi_j = -pi/2 + (j + 1/2) pi / K_f, K_f = `halvplansriktningar`
(standard K/2). Felet för en fri fasad är kvadratiskt i K_f (K_f = 16: 6,4e-4; 32: 1,6e-4; 64: 4,0e-5; 256: < 3e-6).
Testet kontrollerar I(h) mot Gauss–Legendre-kvadratur (fel < 1e-14) och N = 7 pi / 9.

**Gaturum, fasadfot:** punkt på gatan i höjd 0 vid fasaden, motstående vägg på d: h(psi) = atan(H cos psi / d),
VSC = (1/N) Int I(atan(H cos psi / d)) cos psi dpsi (ingen sluten form använd; kvadratur i testet).

## 6. Soltimmar (`matt.SoltimmarAckumulator`)

Solen syns vid tidssteg t om solhöjden (geometrisk, solskivans centrum) > h(phi_sol(t)), där h interpoleras linjärt
(cirkulärt) mellan de två närmaste av K riktningar. Soltimmar = antal synliga steg · steget. Beräkningen
strömmar över riktningarna (minne O(punkter), oberoende av K). `soltimmar_mark` ger **timmar per dygn, medel över
valda datum**.

**Interpolationsfelet sitter vid skuggkanter**: horisonten har ett språng vid en husknut, och interpolationen
sprider språnget över ett azimutsteg (360/K grader). Mätt mot oberoende strålgång (steg 0,02 m) på ett 30 x 20 x 15 m
block, 14 punkter, vårdagjämning, 5-minuterssteg (en stegkvantisering är 0,083 h):

| K | medelfel (h) | maxfel (h) | bias (h) |
| ---: | ---: | ---: | ---: |
| 16 | 1,006 | 2,583 | -0,589 |
| 32 | 0,321 | 1,167 | -0,107 |
| 64 | 0,208 | 0,500 | -0,089 |
| 128 | 0,071 | 0,167 | -0,024 |
| 256 | 0,060 | 0,167 | -0,024 |
| 512 | 0,012 | 0,083 | -0,012 |

**K = 64 är standard för horisont och SVF men räcker inte för skuggstudier**: soltimmar nära skuggkanter kan
avvika upp till en halv timme. Använd `azimutriktningar` >= 180 för soltimmar; lösaren varnar när K < 128.
Fria punkter får exakt dygnets längd.

## 7. Påverkansradie och mosaik (`floden_losare.py`, `ruta.py`)

Radien är **H_max / tan(h_min)** (`karna.dagsljus_paverkansradie_m`; standard 60 m / tan 5 grader = 685,8 m), och
strålarna går högst så långt. Ett hinder med höjd <= H_max på större avstånd skymmer mindre än h_min och ingår
inte. Konsekvenser, medvetet konservativa mot cachenyckeln men **inte** mot resultatet:

- SVF underskattar skymningen med högst sin^2(h_min) = 0,0076 (h_min = 5 grader) per riktning, och mätt
  trunkeringsbias i gaturummet (R = 250 m) är 5,7e-5 i SVF och 5,3e-5 i VSC.
- Soltimmar för sol lägre än h_min är inte garanterade. I Stockholm ligger solen i december mest under 7 grader:
  välj då lägre `h_min_grader` (2 grader ger 1,7 km radie).
- Höjdskillnader i data större än `hojd_max_m` ger en varning (hinder högre än H_max kan skymma över h_min bortom
  radien).

Mosaik: `k.rutor_att_lasa()` ger rutan och grannar inom radien; deras `mark.tif` läggs ihop (rutans egen sist, så den
vinner i överlapp), omsamplas till `cellstorlek_m` (heltalsmultipel av pixelstorleken; `medel`, `max` eller `punkt`,
standard `medel`), och horisonten beräknas för rutans egna celler. Celler i mosaikens utsträckning som saknar data
sätts under alla hinder och **redovisas som varning**. Strålar som lämnar fältet före radien flaggas per cell i
`trunkering.tif` (andel av K) och som varning; det inträffar vid kanten av scenariots data.

## 8. Lösare (K21)

| Lösare | Indata | Utdata | Parametrar (utöver gemensamma) |
| --- | --- | --- | --- |
| `dagsljus.horisont` | - | `horisont.npy` (uint16, 0,01 grader, form K x rad x kol), `horisont.json`, `trunkering.tif` | - |
| `dagsljus.soltimmar_mark` | - | `soltimmar.tif` (h/dygn), `trunkering.tif` | `datum` (lista, standard `["2026-03-20"]`), `steg_min` (10), `refraktion` (false) |
| `dagsljus.svf_mark` | - | `svf.tif`, `trunkering.tif` | - |
| `dagsljus.vsc_fasad` | `fasadpunkter` (CSV) | `vsc.csv` (VSC i procent, status, antal trunkerade riktningar) | `halvplansriktningar` (K/2), `utanfor_m` (halv cell) |

Gemensamma parametrar: `azimutriktningar` (64), `hojd_max_m` (60), `h_min_grader` (5), `cellstorlek_m` (1,0),
`omsampling` (`medel`). Okända parametrar och ogiltiga värden ger `LosarFel`. Alla är rutvisa. Rasterutdata ligger på
rutans utsträckning med `cellstorlek_m` som pixelstorlek (kärnans jämförelse A/B kräver samma rutnät för före och
efter, inte 0,25 m). Värden på byggnadsceller är takets värden (yta ovanpå DSM); maska med `markslag.tif` (9, 2)
om bara mark ska redovisas.

**Fasadpunkter (lösarens egen indataroll):** CSV med kolumnerna `fasad_id,x_m,y_m,z_m,normal_azimut_grader`
(EPSG:3011, RH 2000, normalens azimut norr = 0 medurs; z_m = fönstrets mittpunkt). Positionen är en punkt på
fasadytan; lösaren flyttar den `utanfor_m` längs normalen så att den ligger utanför byggnadens DSM-celler. Rutan
äger de punkter som ligger djupast inne i den (samma regel som kärnan använder för överlappande celler). Punkter
under höjden i sin egen cell får `status = inuti_hinder` och tomt VSC; punkter utanför fältet `utanfor_data`.

## 9. Facit och mätvärden

`floden testsvit --grupp E` och `tests/dagsljus/` (alla mätt på denna kod):

| Fall | Kontroll | Uppmätt avvikelse | Gräns |
| --- | --- | ---: | ---: |
| E1 | Fri plan: VSC (K_f = 256) mot 39,6 %-normeringen; SVF = 1 | 2,5e-6 (SVF exakt 1) | 3e-6 |
| E2 | Oändlig vägg H = 20 m: h mot atan(H cos psi / d), psi = -75..75 grader, celler 2/1/0,5 m | 2,8e-16 rad | 1e-12 |
| E3 | Gaturum H = 20, W = 21–22 m: SVF mitt i gatan mot Oke, K = 64, celler 2/1/0,5 m | 9,2e-15 | 1e-9 |
| E4 | Gaturum: VSC vid fasadfot mot kvadratur (med räckvidd 250 m), K_f = 1 024 | 2,3e-6 | 1e-5 |
| E5 | Skuggstudie: soltimmar mot strålgång, K = 512 | 0,012 h (medel) | 0,03 h |

Jämförelse mot oändligt gaturum (ingen räckviddsbegränsning): VSC 5,3e-5 (0,03 %) i bias från R = 250 m.

**Konvergens i K** (gaturum W = 21, H = 20, R = 250, cell 1 m, fel mot kvadratur med samma räckvidd):

| K (SVF) | fel SVF | K_f (VSC) | fel VSC |
| ---: | ---: | ---: | ---: |
| 16 | 2,4e-4 | 8 | 2,6e-3 |
| 32 | -5,7e-5 | 16 | 5,9e-4 |
| 64 | -5,7e-5 | 32 | 1,7e-4 |
| 128 | -5,7e-5 | 64 | 7,2e-5 |
| 256 | -2,3e-5 | 128 | -5,8e-6 |
| 1 024 | -8,5e-6 | 256 | 9,9e-6 |
| 2 048 | -2,4e-6 | 1 024 | 1,6e-6 |

(Mot det oändliga gaturummet är SVF-felet vid K = 64 enbart 9e-15: den periodiska trapetsregeln på en slät funktion
konvergerar exponentiellt; skillnaden mot räckviddsfacit kommer från riktningarna nära gatans längdaxel.)

**Konvergens i rutstorlek** (W = 21,3 m, väggytorna ligger inte på cellgränser, så rastreringen flyttar dem högst
cell/2; K = 128, fasadpunkt 0,25 m från fasaden; facit SVF = 0,4701, VSC = 0,1623):

| cell (m) | fel SVF | fel VSC |
| ---: | ---: | ---: |
| 4 | -0,0229 | -0,0122 |
| 2 | +0,0118 | +0,0028 |
| 1 | -0,0052 | -0,0012 |
| 0,5 | +0,0034 | +0,0008 |
| 0,25 | -0,0009 | -0,0002 |

Felet avtar ungefär linjärt med cellstorleken och byter tecken med hur väggen råkar rastreras. Det är
lägesfelet hos byggnadskanterna (högst cell/2) som styr, inte metoden. Testet kräver fel < 0,012·cell (SVF)
och < 0,014·cell (VSC) i detta gaturum; det är en uppmätt övre gräns, inte en allmän garanti.

## 10. Prestanda (mätt, `floden dagsljus bench`)

Maskin: 2 CPU-kärnor, Python 3.13, NumPy 2.1.3, en tråd. Slumpmässiga huskroppar; tiden beror inte på värdena.

- **Mark, 1 km² (1 000 x 1 000 celler à 1 m), K = 64, R = 685,8 m**: helt svep **47,2 s** (0,74 s per riktning;
  1,36 miljoner cell·riktningar/s, motsvarande 21 200 celler/s vid K = 64). Fältet har 5,6 miljoner celler
  (ytan plus 687 m marginal).
- **Mark, 1 km² à 0,5 m (4 miljoner celler)**: 7,70 s per riktning (medel över de 8 första riktningarna, 0–39 grader;
  0,52 miljoner cell·riktningar/s). Ett helt K = 64-svep blir därmed ca 8 minuter (uppskattning, inte mätt).
- **Fasadpunkter (mipmap)**: 20 000 punkter x 32 riktningar (640 000 strålar, R = 685,8 m) på 6,7 s =
  **95 000 punkt·riktningar/s**; pyramiden byggs på 0,02 s. Mot referensen på samma strålar (3 000 strålar):
  67 600 mot 17 500 strålar/s (**3,9 x snabbare**, identiskt resultat).
- Demo: kvarter 300 x 300 m à 1 m, K = 180 (R = 514 m), vårdagjämning med 10-minuterssteg: 12,6–15,1 s
  (två körningar).

## 11. Begränsningar (flaggas, inte gissas)

- **Takutsprång, burspråk, balkonger, indragna fasader**: DSM är ett prismafält med lodräta väggar. Fasadpunkter
  under utkragningar får fel VSC och soltimmar (för högt). Sådana punkter ska hänvisas till referenslösaren
  **Radiance** (finns inte i fas 1; ingen automatisk detektion än). Dessutom ger takkanter som inte ligger på
  cellgränser lägesfel enligt avsnitt 9.
- **Byggnadshöjd i K1**: `mark.tif` anger "byggnadens takhöjd eller en fast hinderhöjd". Är det en fast hinderhöjd är
  alla dagsljusmått fel i proportion; markmodellen måste ha verkliga takhöjder för dagsljusbruk.
  K9-ändringarna `byggnad_bort` och `byggnad_hojd` stöds inte av kärnan än (`k9_till_k1`); `byggnad_ny` med
  `hojd_m` går via K3.
- **Vegetation** ingår inte (kräver `vegetation` i K1; trädkronor är dessutom delvis transparenta).
- **Himmel**: bara mulen CIE-himmel för VSC (BRE:s mått). Ingen klarhimmel, ingen reflektion (mark, fasader),
  ingen glasningstransmission, ingen DF i rum. Det är senare faser.
- **Sol**: solskivans centrum (inte 0,53 grader bred skiva); refraktion av som standard; solläge en gång per ruta.
- **Horisont under h_min** bortom radien ingår inte (avsnitt 7). **Negativ horisont** klipps till 0.
- **Interpolation mellan riktningar** ger skuggkantsfel (avsnitt 6).
- Mosaik: saknade grannrutor ger för låga horisonter (varning). Alla rutor måste ligga på samma cellraster.

## 12. Förslag till K1-tillägg (ändras inte här; K1 är ett kontrakt)

Dagsljus behöver två saker som K1 saknar. De är i fas 1 definierade som lösarens **egna indataroller** och
hindrar inte körning:

1. `fasadpunkter` (rollen i `vsc_fasad`): CSV enligt avsnitt 8. Förslag till K1/v2: en fil
   `fasadpunkter.parquet` per ruta (kolumner `fasad_id, byggnads_id, x_m, y_m, z_m, normal_azimut_grader`,
   `fasad_langd_m`), genererad ur byggnadsregistret med en punkt per 2–3 m och våning.
2. `byggnader.gpkg` (lager `byggnader`): `byggnads_id`, polygon, `takhojd_rh2000` (m), `fasadniva_rh2000`,
   `kalla`. Behövs för att `byggnad_bort` och `byggnad_hojd` ska kunna materialiseras i `mark.tif`, och för att
   skilja riktiga takhöjder från "fast hinderhöjd". Samma register behövs av buller och luft.

Tills dess läser dagsljuslösarna höjdfältet direkt ur `mark.tif`.

## 13. Nästa

- Fas 2: använd `horisont.npy` som beroende (`beror_pa`) så att soltimmar, SVF och VSC delar en horisontberäkning.
- Detektera fasadpunkter under utkragningar (DSM-skillnad ovanför punkten) och skicka dem till Radiance.
- Reflekterat ljus (externt reflekterad komponent), DF i typrum, klarhimmel och solstudier för fasader.
- Rust för skivsvepet om 0,5 m-rutor ska vara rutin (8 min per km² och K = 64 i NumPy).
