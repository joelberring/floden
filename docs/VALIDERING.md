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
