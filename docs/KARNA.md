# Kärnan – så ansluter en modul

För agenter som bygger vatten, luft, buller eller dagsljus. Kärnan (`src/karna/`) räknar inget själv;
den ger scenario, cache, spårbarhet och jämförelse A/B. Kontrakt: K9 (scenario) och K10 (fråga) i
`DATAKONTRAKT.md`.

## Två sätt att ansluta

| | Nodgraf (K10, nedan) | Rutvisa lösare (K20–K21, `docs/ANSLUTA_LOSARE.md`) |
| --- | --- | --- |
| Passar | Egna beräkningskedjor, vatten via sänkgrafen | Dagsljus, buller, luft: allt med en påverkansradie |
| Scenario | `Planscenario` (K9) till nodbyggaren | K9 (eller K3) materialiserat på K1-rutor av kärnan |
| Lokal omräkning | Modulen ansvarar (ta in scenariots nyckel) | Automatisk: nyckel per ruta inom radien |
| Körs med | `karna.kor_fraga` (Python) | `floden fraga kor fraga.toml` |
| Jämförelse A/B | Modulens sak | Kärnan för K1-raster (`[[jamforelser]]`) |

K9-ändringarna för byggnader och vegetation (`byggnad_ny`, `byggnad_bort`, `byggnad_hojd`, `vegetation`) kräver
K1 v2-rutor; de tillämpas av `karna.geometri` och läses via samma API (`las_byggnader`, `las_terrang`,
`las_ytmodell`, `las_vegetation`, `fasadpunkter`, `las_markklasser`). Se K1 v2 och K11 i `DATAKONTRAKT.md`.

Båda delar K9, sha256-nycklar och K0-manifest. Välj rutvisa lösare om resultatet bara beror på
geometrin inom ett avstånd; då behöver modulen inte själv hålla reda på vad som ändrats.

## Tre steg (nodgraf)

1. **Skriv lösaren** som en vanlig funktion och slå in den i `Losare`. Höj `version` när koden ändras
   så att resultaten kan ändras (annars återanvänds gamla resultat ur cachen).

   ```python
   from karna import Losare

   def vsc(indata, parametrar, utkatalog):
       # indata: roll -> sökväg (fil, eller katalogen med en tidigare nods resultat)
       # skriv rastren/tabellerna i utkatalog; returnera mätvärden (bara tal som räknats fram)
       return {"vsc_min_procent": ...}

   VSC = Losare("dagsljus.vsc_fasad", "1", vsc, modul="dagsljus")
   ```

2. **Registrera analystypen** med en nodbyggare som gör ett frågesteg + scenario till en nod:

   ```python
   from karna import Nod, registrera_analystyp

   def bygg_vsc(steg, scenario):
       return Nod(VSC, {"horisont": horisontnod(scenario)},
                  {**dict(steg.parametrar), "scenario": scenario.nyckel})

   registrera_analystyp("vsc_fasad", "dagsljus", bygg_vsc)
   ```

3. **Kör en fråga** (TOML, K10) mot scenarier:

   ```python
   from karna import Berakningsgraf, Cache, kor_fraga, las_fraga

   svar = kor_fraga(las_fraga("fraga.toml"), {"alt-a": a, "alt-b": b},
                    Berakningsgraf(Cache("runs/cache")))
   ```

## Regler

- Alla parametrar som påverkar resultatet ska stå i `Nod.parametrar` (de ingår i nyckeln).
- Ta in scenariots nyckel (eller de ändringar ni använder) i parametrarna eller indata, annars får två
  scenarier samma nyckel.
- Påverkansradie för lokal omräkning: `karna.dagsljus_paverkansradie_m(H, h_min)` och
  `karna.ruta_inom_radie(...)`. Vatten använder sänkgrafen i stället.
- Ett fel i lösaren lämnar ingen cache efter sig; skriv hellre ett tydligt fel än ett osäkert resultat.
- Interna format: EPSG:3011, RH 2000, enheter i namnen (`AGENTS.md`).
