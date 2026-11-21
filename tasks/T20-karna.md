# T20 – Gemensam kärna: utredningsfrågor, scenarier, lösarregister och cache

**Mål:** ett ställe där utredningsfrågor över alla domäner (vatten, luft, buller, dagsljus)
ställs, körs och jämförs, med cache och lokal omräkning som gäller för alla domäner.

**Läs först:** `docs/DATAKONTRAKT.md` (K0, K1, K3, K20–K22), `docs/ANSLUTA_LOSARE.md`,
projektdokumentet *Floden – systemdesign* (avsnitt 2 och 6).
**Beroenden:** T00, T04 (lappar).

## Steg
1. K20 frågeformat (TOML/JSON) med strikt validering. **Klart.**
2. K21 lösargränssnitt med påverkansradie och automatisk upptäckt via `floden_losare.py`. **Klart.**
3. Scenario = bas + K3-lappar, materialiserat ruta för ruta; oförändrade rutor behåller hash. **Klart.**
4. Innehållsnycklar och atomär cache (K22); beroenden mellan analyser kedjar nycklar. **Klart.**
5. Jämförelse före/efter för K1-raster. **Klart.**
6. CLI `floden fraga {kor,plan,losare}`. **Klart.**
7. Referenslösare `vatten.statisk_fyllning` och `geometri.lutning`. **Klart.**
8. Webb-API: `POST /api/fraga` som kör en K20-fråga (T10 ansluter). *Kvar.*
9. Jämförelse för tabeller (Parquet per fasadpunkt/byggnad/entré). *Kvar.*
10. Parallell körning av oberoende jobb (process-pool). *Kvar.*
11. Globala lösare över flera rutor för vatten (`sankgraf.sammanfoga_rutor`). *Kvar, i samråd med vattenuppdraget.*

## Acceptans
- Andra körningen av samma fråga kör 0 jobb.
- En lapp i en ruta av tre räknar bara om berörda rutor för rutvisa lösare, och deras beroenden.
- Ett avbrutet jobb lämnar ingen cachepost.
- Känt svar: A1 före/efter genom kärnan ger samma djup som `sankgraf` direkt.

## Stoppa och fråga
- Om en domän behöver ändra K20–K22 på ett sätt som bryter befintliga frågor.
