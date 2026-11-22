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
0,0238 → 0,0185 m, IoU 0,924 → 0,924, v_max 2,60 → 4,23 m/s.

### B. Jämförelse med andra verktyg (T09b)

Provområdet körs i Scalgo och jämförs med SMHI:s kartering (10 × 10 m, beta). Skillnader förklaras en och en:
terräng, förluster eller dynamik.

### C. Standardiserade testfall (T09c)

Environment Agencys testfall för 2D-modeller, bland annat fyllning av sänkor och ett urbant fall med regn och
överbelastade ledningar. Etablerade program har redovisat sina resultat.

### E. Dagsljus (`floden testsvit --grupp E`)

Fri plan (VSC 39,6 %, SVF 1), oändlig vägg, gaturum (SVF mot Oke, VSC mot kvadratur) och skuggstudie mot
oberoende strålgång. Facit, härledningar och uppmätta avvikelser står i `docs/DAGSLJUS.md`.

### D. Verkliga händelser (senare)

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
