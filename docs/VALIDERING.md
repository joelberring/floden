# Validering

Varje ändring som påverkar resultat körs mot testsviten. Mätvärdena skrivs i `STATUS.md`.

## Testsvitens delar

### A. Syntetiska fall med känt svar (T09a)

Små terränger där svaret kan räknas för hand eller analytiskt.

| Fall | Kontrollerar |
| --- | --- |
| A1 Enkel sänka, konstant regn | Fyllnadstid och slutvolym |
| A2 Två sänkor i serie | Överfallsordning och tidpunkter |
| A3 Två sänkor som slås ihop | Sammanslagning och gemensam nivå |
| A4 Lutande plan | Flödesväg och gångtid |
| A5 Gata med kantsten och rännsten | Att vatten stannar i rännstenen tills kantstenen överskrids |
| A6 Sänkt kantsten vid infart | Att vatten lämnar gatan just där |
| A7 Vägbank med och utan trumma | Falsk damm utan trumma |
| A8 Port genom byggnad | Att vatten passerar porten |
| A9 Brus i terrängen | Att persistensfiltret tar bort små falska sänkor men inte verkliga |
| A10 Sluten gård | Slutsänka (K2/v2): gård 10 × 10 m helt omgiven av byggnader, plant golv, 50 mm regn (300 mm/h i 10 min). Djup = regnvolym / area = 50 mm i alla 1 600 celler, hinder 16,00 m (lägsta taket). Fallets definition: regn på tak räknas inte (`takvatten = false` i `fall.toml`). Graf och 2D (lokal tröghet) mot facit |
| A11 Sluten gård med avtappning | Som A10 (`takvatten = false`), 120 mm/h i 15 min och avtappning 60 mm/h (fas 3): djup (120 − 60) mm/h · t till 15 mm vid 15 min, sedan −60 mm/h, tomt vid 30 min; avtappat 3,0 m³. Händelsestyrd dynamik mot förloppet, 2D med samma avtappning i topp och slutdjup. Samma förlopp via K20-frågan (`vatten.dynamik`, `avtappning_mm_h` och `avtappning_omraden`) i `tests/karna/test_avtappning_fraga.py` |
| A12 Sluten gård med takvatten | Som A10 med takvatten: kvarteret är ett ringhus, så hälften av takets regn (224 m² · 50 mm / 2 = 5,6 m³) rinner till gården. Djup = (5,0 + 5,6) m³ / 100 m² = 106 mm, i statisk fyllning, händelsestyrd dynamik och 2D (efter 1 800 s) |
| A13 Hus i tråg | Hus 12 × 6 m (okänd takform) med ett slutet tråg vid varje fasad, 50 mm: varje trågs volym = eget regn (avrinningsytan) + takvatten; långsidorna får 1,8 m³ var, gavlarna 0. Graf mot facit, 2D mot grafen |

A10 och A11 kör både sänkgrafen och 2D-lösaren och redovisar skillnaden i kommentaren. Uppmätt 2026-11-22: A10 graf
0 m och 2D 7,5e-10 m från 50 mm (|graf − 2D| = 0 i float32), volymfel 0,0000 %; A11 graf 8,3e-16 m från förloppet,
2D topp 15,00 mm och slutdjup 0,00 mm. På sluttande gårdsgolv (enhetstest `tests/tvad/test_slutsanka_2d.py`) skiljer
graf och 2D högst 0,14 mm (2D:s kvarvarande film), med samma volym.

Takvatten (2026-11-22, `docs/VATTEN.md`), uppmätt: A12 graf 0 m, dynamik 0 m och 2D 1,3e-6 m från 106 mm
(2D-volym 10,6000 m³); A13 graf exakt (2,2563 m³ i långsidornas tråg, 0,2375 m³ i gavlarnas = bara eget regn),
2D 0,037 m³ (1,6 %) mindre i långsidornas och 0,023 m³ (9,9 %) mindre i gavlarnas tråg (2D:s film på sluttningarna
och vatten som rinner ut över rutkanten innan det når tråget). Fall utan byggnader (A1–A7, A9, EA1, EA2) är
byte-identiska med takvatten på. A8 med takvatten: samma djup (0), `in` 6,325 → 7,500 m³ (hela rutans regn).
EA8A med takvatten (EA:s Test 8A lägger regnet på hela området): maxdjup 0,420 → 0,558 m, MAE hybrid mot 2D
0,0238 → 0,0185 m, IoU 0,924 → 0,924, v_max 2,60 → 4,23 m/s (uppmätt 2026-11-22, före vattenkärnans numeriska rättelser).

**Uppdaterat 2026-11-23** efter rättelserna i `docs/BESLUT.md` (hastighet, friktion, subgrid; uppmätt med
`floden testsvit`): EA8A maxdjup 0,557 m, MAE hybrid+subgrid mot 2D **0,0200 m**, IoU **0,930**, v_max **2,26 m/s**,
fara_max 0,191 m²/s. A13: 2D −0,0365 m³ (−1,6 %) i långsidornas tråg och −0,0233 m³ (−9,8 %) i gavlarnas; A12: 2D 1,2e-6 m
från 106 mm. A0–A11 oförändrade.

### B. Jämförelse med andra verktyg (T09b)

Provområdet körs i Scalgo och jämförs med SMHI:s kartering (10 × 10 m, beta). Skillnader förklaras en och en:
terräng, förluster eller dynamik.

### C. Standardiserade testfall (T09c)

Environment Agencys testfall för 2D-modeller, bland annat fyllning av sänkor och ett urbant fall med regn och
överbelastade ledningar. Etablerade program har redovisat sina resultat.

Uppmätt 2026-11-23 (`floden testsvit`, 3/3 godkända):

| Fall | Maxdjup | MAE | IoU | Övrigt |
| --- | ---: | ---: | ---: | --- |
| EA1 Sänka över tröskel | 0,405 m | 0,0053 m | 0,980 | sänknivå 10,0447 m mot facit 10,0500 m (MAE 0,53 cm, std 0,032 cm) |
| EA2 Sänknät 4×4, hybrid mot full 2D | 0,577 m | 0,0239 m | 0,850 | största skillnad 0,1173 m; 16 av 16 lövsänkor; kaskaden NV 0,58 → 0,50 → SO 0,28 m |
| EA8A Urbant regn, hybrid+subgrid(f=2) mot full 2D | 0,557 m | 0,0200 m | 0,930 | v_max 2,26 m/s, fara_max 0,191 m²/s |

Före vattenkärnans rättelser 2026-11-23 var EA1 std 0,036 cm, EA2 MAE 0,0217 m, IoU 0,864 och största skillnad
0,0986 m, och EA8A MAE 0,0185 m, IoU 0,924 och v_max 4,23 m/s (tabell i `docs/BESLUT.md`).

### E. Dagsljus (`floden testsvit --grupp E`)

Fri plan (VSC 39,6 %, SVF 1), oändlig vägg, gaturum (SVF mot Oke, VSC mot kvadratur) och skuggstudie mot
oberoende strålgång. Facit, härledningar och uppmätta avvikelser står i `docs/DAGSLJUS.md`.

### D. Analytiska 2D-facit, Hunter (`floden testsvit --grupp D`)

Våg över horisontellt plan med Manning-friktion; analytisk lösning av Hunter m.fl. (2005), som Bates m.fl. (2010) använde
för att validera lokal tröghet (härledning i `src/testsvit/hunter.py`). D1 är friktionsdominerat (Fr ≈ 0,1, n = 0,05,
u = 0,2 m/s) och har krav: RMSE < 0,01 m vid finaste rutnätet (dx 5 m) och fallande RMSE för varje förfining. D2 har
Bates-parametrar (Fr ≈ 0,4, n = 0,01, u = 1 m/s), ligger utanför lösningens antaganden (tröghet försummad) och redovisas
bara som upplysning. Uppmätt 2026-11-23, GODKÄND (före = `6ef22ae`, efter = `5b84a43`):

| Fall | dx (m) | t (s) | RMSE före (m) | RMSE efter (m) | Största fel efter (m) | Frontfel (m) | Volymfel efter |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| D1 | 20 | 1 800 | 0,0205 | 0,0199 | 0,0754 | +40 | 5,0e-16 |
| D1 | 10 | 1 800 | 0,0130 | 0,0125 | 0,0621 | +20 | 5,1e-16 |
| D1 | 5 | 1 800 | 0,0074 | 0,0071 | 0,0476 | +10 | 8,6e-16 |
| D1 | 20 | 3 600 | 0,0182 | 0,0177 | 0,0865 | +60 | 5,1e-16 |
| D1 | 10 | 3 600 | 0,0109 | 0,0105 | 0,0684 | +30 | 1,3e-16 |
| D1 | 5 | 3 600 | 0,0056 | 0,0053 | 0,0493 | +10 | 1,3e-15 |
| D2 | 25 | 1 200 | 0,0569 | 0,0593 | 0,1883 | −100 | 8,7e-16 |

D1 blev 2,7 % till 5,4 % bättre i RMSE, D2 blev 4,2 % sämre (upplysningsfall utan krav). Se `docs/BESLUT.md` 2026-11-23.

### F. Hastighet och CFL i 2D-lösaren (`pytest tests/tvad/test_hastighet_facit.py`)

Handräknat facit för hastigheten (v = |q| / max(h_före, h_efter), `docs/BESLUT.md` 2026-11-23), tillagt eftersom
ändringen saknade facittest. **Jämn lutande ränna med konstant regn**: q(x) = i·x och Manning q = h^(5/3)·S^(1/2)/n ger
h(x) = (i·x·n/S^(1/2))^(3/5) och v(x) = h^(2/3)·S^(1/2)/n. Rännan är 200 m × 5 celler, dx 1 m, regn 3 000 s, öppen
nedre kant, tre kombinationer (S, n, regn): (0,01; 0,03; 100 mm/h), (0,02; 0,05; 60), (0,005; 0,02; 150). Toleranser:
3 % relativt för x = 25–150 m, 6 % för x = 10 m, där djupet bara är några millimeter. Uppmätt avvikelse
(modell − facit)/facit, `max_hastighet_m_s` i rännans mittrad:

| Läge x | Efter (`5b84a43`), alla tre fall | Före (`6ef22ae`), de tre fallen |
| --- | --- | --- |
| 10 m | −2,9 % till −4,0 % | +167 %, +2 407 %, −3,1 % |
| 25 m | −1,3 % till −2,1 % | +20 %, +490 %, −1,7 % |
| 50 m | −0,7 % till −1,3 % | −0,6 %, +235 %, −1,1 % |
| 100 m | −0,4 % till −0,9 % | −0,3 %, +55 %, −0,7 % |
| 150 m | −0,3 % till −0,7 % | −0,3 %, +1,0 %, −0,6 % |

Utfall: alla 8 tester godkända på `5b84a43`+tillägget; de två Manning-fallen med S = 0,01 och 0,02 underkänns på
`6ef22ae` (den gamla beräkningen gav hastighetsspikar där vattnet är tunt). Maxdjupet i samma celler ligger inom
samma toleranser (testas). Rust-kärnan ger bitidentiskt samma hastighet och djup som Python i fallet (0 m/s skillnad,
3 954 tidssteg i båda).

**CFL**: Python-lösaren loggar `courant_hast_max` = största u_max · dt / dx över alla tidssteg, med den u_max som
steget valdes med (diagnostik, ändrar ingen beräkning; Rust och SFINCS-import ger 0 = inte mätt). Krav: ≤ Courant-talet
`cfl`. Uppmätt i en ränna med S = 0,05 (v_max 0,42 m/s), n = 0,03, 100 mm/h, dt_max_s 50 s: cfl 0,5 → 0,263 (4 447
steg), cfl 0,3 → 0,158 (7 416 steg), cfl 0,5 med dx 0,5 m → 0,264 (8 902 steg). Kravet gäller alltså med marginal,
och CFL-steget (inte `dt_max_s`) styr tidssteget. Observera att talet använder u_max från föregående steg: det
mäter att tidssteget följer regeln, inte att den nya hastigheten efter steget aldrig överstiger den.

### G. Ekologisk konnektivitet (`pytest tests/ekologi`)

Analytiska facit för kretsteorikärnan i `src/ekologi/` (G1 homogen korridor, G2 parallella korridorer, G3 port, G4 PC/ECA/dPC
på tre fläckar, G5 derivatan dR/dG mot differenskvot, G6 inneslutna slumpscenarier) och G7 genom hela kedjan (`tests/ekologi/
test_losare_k21.py`): ett nytt hus ritat som K9 ger ΔECA ≤ 0 i demokvarteret, intervallen innesluter nominellt värde och
`ekologi.json` följer K45. Gruppen körs med pytest och inte med `floden testsvit --grupp G`: facitfallen är kod, inte
indatamappar, och bokstaven används av dessa tester. Allt är skiss (`docs/EKOLOGI.md`).

### H. Verkliga händelser (senare)

Dokumenterade skyfall med felanmälningar, räddningstjänstens insatser och försäkringsskador.

## Mått

| Mått | Beskrivning | Gräns (startvärde, justeras) |
| --- | --- | --- |
| Volymfel | (in − ut − lagrat) / in | < 0,1 % |
| Maxdjupsfel | Medelabsolutfel mot referens i våta celler | A-fall: < 1 cm; mot full 2D: rapporteras |
| Träffad yta | Andel celler där båda säger våt (> 0,1 m) av alla som någon säger våt | Rapporteras |
| Ankomsttid | Fel i tid till 0,1 m vid utvalda punkter | Rapporteras |
| Körtid | Väggklocka per nivå och område | Rapporteras |
| Reproducerbarhet | Samma manifest ger samma sha256 för utdata | Krav |

Gränserna sätts av en människa när den första omgången resultat finns. Agenter får inte ändra gränserna för att
få ett test att gå igenom.

## Grupp L – Luft

Testfall L1–L8 och gränser finns i `docs/LUFT.md` avsnitt 6. De körs som enhetstester i `tests/luft/`
(`pytest tests/luft`).
