# Data

## Källor

| Data | Källa | Åtkomst | Känslighet | Används i |
| --- | --- | --- | --- | --- |
| Laser (punktmoln, 1–2 p/m²) | Lantmäteriet, Laserdata Nedladdning skog | STAC (`stac-hojd`), Geotorget-konto; CC0 med användarvillkor, ange ©Lantmäteriet | Öppen (villkoren nämner personuppgifter i punktmoln) | T01 |
| Markhöjdmodell 1 m | Lantmäteriet | STAC | Öppen | Jämförelse, reserv |
| Ortofoto | Lantmäteriet | STAC / API | Öppen med villkor | T01 (förändringar), T06 |
| Tätare laser | Stockholms stad | Fråga stadsmätningen | Okänd – behandla som intern | T01b |
| Baskarta (DWG) | Stockholms stad | Intern | Intern; innehåller inga personuppgifter | T01, T01b |
| Vägnät | Trafikverket NVDB | Öppet API med nyckel | Öppen | T07 |
| Jordarter 1:25 000–1:100 000 | SGU | Öppna data (OGC API) | Öppen | T06 |
| Grundvattennivåer | SGU, stadens observationsrör | Öppet / fråga staden | Öppen / intern | T06 |
| Markfuktighet (DTW) | SLU / Skogsstyrelsen | Öppen | Öppen | T06 |
| Ledningsnät, brunnar, trummor | Stockholm Vatten och Avfall | Avtal | **Kan vara skyddsklassat** | T01, T06, M7 |
| Nederbörd, radar | SMHI | Öppna data | Öppen | T03, T07 |
| IDF-tabell Stockholmsregn 2024 (Tabell 7) | Stockholms stad, trafikkontoret (Sweco, DHI, SMHI) | Offentlig rapport, transkriberad till `data/regn/` med sidokort och sha256 | Öppen | R1 (`floden regn/kor/risk --idf stockholmsregn-2024`) |
| Skyfallskartering (beta) | SMHI | Webbtjänst | Öppen | T09b |
| Testfall för 2D-modeller | Environment Agency (UK) | Nedladdning | Öppen | T09c |
| Räddningsstationer, särskilda objekt | Räddningstjänsten, staden | Fråga | Inga personuppgifter | T07 |

## Regler

- Öppen data laddas ner till `data/cache/` (inte i git) och cachas med kontrollsumma.
- Intern data ligger i en lokal mapp som anges i `config.local.toml`. Den checkas aldrig in och skickas aldrig till en extern AI.
- Ledningsdata används bara lokalt eller i stadens miljö. Kod för ledningsnät testas med syntetiska nät.
- Ange alltid källan i rapporter och kartor (©Lantmäteriet, SGU, SMHI, NVDB osv.).

## Koordinatsystem

- Intern beräkning: SWEREF 99 18 00 (EPSG:3011), höjder RH 2000.
- Lantmäteriets laser och raster levereras i SWEREF 99 TM (EPSG:3006): transformera vid inläsning.
- Stockholms baskarta: SWEREF 99 18 00 (kontrollera per fil; östliga koordinater kring 150 000).

## Regn: Stockholmsregn 2024

`data/regn/stockholmsregn_2024_tabell7_intensitet_l_s_ha.csv` (l/(s·ha)) och `..._volym_mm.csv` (mm) är Tabell 7 i
rapporten Stockholmsregn 2024 (kap. 4.1, s. 30): blockregnsintensitet för 5–1440 min och 1–1000 års återkomsttid,
**utan klimatfaktor**. Filerna är oförändrade transkriptioner; `data/regn/stockholmsregn_2024.toml` har källa, URL,
fotnot ("varaktigheterna 5–20 min är utvärderade exklusive 15-minuter Geonor-data"), transkriptionskontroll och
sha256, som kontrolleras vid varje inläsning (ändrad tabell ger `RegnFel`).

- **Enheter:** biblioteket räknar i mm/h. `IdfKalla.las()` omvandlar vid inläsning med 1 l/(s·ha) = 0,36 mm/h.
  CSV-filerna ändras inte. Volymen i mm är kontroll: V = i·D·0,006 inom avrundning (i l/(s·ha), D i min).
- **Interpolation:** som för alla `IdfTabell`: bilinjär i (ln D, ln T) till ln i; stödpunkterna återges exakt och
  ingen extrapolering görs utanför 5–1440 min och 1–1000 år.
- **Klimatfaktor:** en uttrycklig parameter. Standard 1,4 (rapporten kap. 6.3, multipliceras med hela kurvan, kap.
  4.9); 1,25 var stadens faktor när rapporten skrevs. Faktor och källa skrivs i K4-serien (`statistikkalla`,
  `klimatfaktor`) och i K0-manifestet (`idf_kalla`, `idf_klimatfaktor`, `idf_sha256`; tabellfilerna som indata).
- **Tabell 6 och 7:** rapportens Tabell 6 (kap. 3.8) ger andra 60-minutersvärden (100 år: 117,9 ± 9,4 mot 127,8
  l/(s·ha)); rapporten säger att metoderna inte helt överensstämmer. Tabell 7 används (se `docs/BESLUT.md`).

```
floden regn --idf stockholmsregn-2024 --aterkomsttid-ar 100                    # CDS 6 h, kf 1,4
floden regn --idf stockholmsregn-2024 --aterkomsttid-ar 100 --klimatfaktor 1.25 --typ block --varaktighet-min 60
floden kor --k1 A3 --idf stockholmsregn-2024 --aterkomsttid-ar 100 --regntyp block --varaktighet-min 60
floden risk --omrade A3 --idf stockholmsregn-2024 --max-aterkomsttid-ar 500
```

`--idf` kan inte kombineras med `--id`/`--regn-id` (förvalen bygger på Dahlström 2010). I Python:
`skapa_regn_fran_idf_kalla("stockholmsregn-2024", 100, klimatfaktor=1.4)` eller `las_stockholmsregn_2024()`.
