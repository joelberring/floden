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
| A10 Sluten gård | Slutsänka (K2/v2): gård 10 × 10 m helt omgiven av byggnader, plant golv, 50 mm regn (300 mm/h i 10 min). Djup = regnvolym / area = 50 mm i alla 1 600 celler, hinder 16,00 m (lägsta taket). Regn på tak räknas inte (taken avvattnas inte till gården i modellen). Graf och 2D (lokal tröghet) mot facit |
| A11 Sluten gård med avtappning | Som A10, 120 mm/h i 15 min och avtappning 60 mm/h (fas 3): djup (120 − 60) mm/h · t till 15 mm vid 15 min, sedan −60 mm/h, tomt vid 30 min; avtappat 3,0 m³. Händelsestyrd dynamik mot förloppet, 2D med samma avtappning i topp och slutdjup |

A10 och A11 kör både sänkgrafen och 2D-lösaren och redovisar skillnaden i kommentaren. Uppmätt 2026-11-22: A10 graf
0 m och 2D 7,5e-10 m från 50 mm (|graf − 2D| = 0 i float32), volymfel 0,0000 %; A11 graf 8,3e-16 m från förloppet,
2D topp 15,00 mm och slutdjup 0,00 mm. På sluttande gårdsgolv (enhetstest `tests/tvad/test_slutsanka_2d.py`) skiljer
graf och 2D högst 0,14 mm (2D:s kvarvarande film), med samma volym.

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
