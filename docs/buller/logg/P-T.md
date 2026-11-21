# P-T – Terrängtolkning och terrängförenkling

Datum: 2026-11-21. Paket P-T enligt `docs/buller/B4-uppdelning.md` avsnitt 7.

Ägda filer: `src/buller/nord2000/terrang_skarmar.py`, `src/buller/nord2000/terrang_forenkling.py`,
`tests/buller/nord2000/test_terrang_skarmar.py`, `tests/buller/nord2000/test_terrang_forenkling.py`, denna logg.
Inga andra filer är ändrade (`terrang.py`, `karna.py`, `tolkningar.md`, `STATUS.md` och `PLAN.md` ägs av samordnaren).

Underlag: DELTA AV 1106/07 rev. 2014 avsnitt 5.21.1–5.21.3, 5.23.2, 5.23.18 och bilaga B; FORCE TC-101327 avsnitt 2.11
och 2.15. Alla formler, gränser och tabellvärden är avlästa från sidbilder (150 dpi) i
`/home/claude/verktyg/sidor/P-T/` (AV s. 132–139, 145, 160–161, 166–167; TC s. 6–10). AV 1849/00 avsnitt 4.4.4.3 och
AV 1851/00 avsnitt 6.3 är lästa som bakgrund.

**Ingenting här är prövat mot facit.** Paketet ger geometri och övergångsparametrar; ljudnivåer kräver delmodell 4–6.

## Implementerat

### `terrang_skarmar.py`

| Funktion | Innehåll | Avsnitt, ekvation |
| --- | --- | --- |
| `vertikalt_avstand_m` | VertDist: lodrät teckensatt höjd över en linje | 5.23.18, ekv. 389–390 |
| `konvex(geo, i)` | Convex(i) med marginalen 0,0001 m | 5.23.2, ekv. 336 |
| `gangvagsskillnad_mellan_m` | Δℓ₀ med valfria ändpunkter (S eller R ersatt) | 5.21.1 ekv. 300, 5.21.2 |
| `skarmform(geo, kant)` → `Skarmform` | W₁ och W₂: närmaste icke-konvexa punkt på var sida om kanten | 5.21.1 sista stycket, 5.21.2 |
| `ersattningspunkt_t1` | T₁, eller T₁' på siktlinjen när Δℓ₀(T₁) < 0 | 5.21.2, figur 33, TC 2.11 |
| `sekundar_skarmkant` | T₂: störst Δℓ₀ mot T₁/T₁' bland punkter utanför primärformen, över baslinjen | 5.21.2 |
| `sekundar_kant` | T₁₂: störst Δℓ₀ bland primärformens punkter utom W₁, W₂, T₁ | 5.21.3 |
| `valj_skarmkanter(geo)` → `Skarmkanter` | T₁ (ur `terrang.primar_skarmkant`), former, T₂, T₁₂ – ren geometri | 5.21.1–5.21.3 |
| `overgang_gangvag`, `overgang_hojd`, `overgang_fresnelzon`, `overgang_kantavstand` | r_Δℓ, r_λ, r_Fz, r_w | ekv. 302, 303, 304, 308 |
| `gangvagsskillnad_prim_m` | Δℓ' = max(Δℓ, Δℓ₀) – kroken | 5.21.1 kriterium 1 |
| `skarmparameter_rscr1_allman` | r_scr1 med krok för Δℓ | ekv. 302–305 |
| `skarmparameter_rscr2` | r_scr2 = r_Δℓ2·r_λ2·r_Fz2·r_w | ekv. 308–309 |
| `sekundarkantgeometri` | P₀, T₁₂', h'_SCR12, d₁, d₂ | ekv. 311–313, figur 34 |
| `skarmparameter_rscr12` | r_scr12, sex faktorer | ekv. 314 |
| `tolka_terrang`, `tolka_terrang_homogen` → `Terrangtolkning` | r_scr1, r_scr2, r_scr12 per band; verksamma kanter; skärmtyp; vilka av delmodell 4, 5, 6 som behövs | 5.21.1–5.21.3, ekv. 306, 310, 315 |

Inte implementerat, med avsikt: **ekv. 301 och 307** (Δℓ med krökta strålar; kräver R_S, R_R, θ_S, θ_R ur delmodell 4
resp. 6) och motsvarande för T₁₂ (delmodell 5). Kroken: nyckelordsargumentet `gangvagsskillnad_refraktion_m` (i
`tolka_terrang`: `gangvagsskillnad_t1_m`, `_t2_m`, `_t12_m`, tal eller ett värde per frekvens). Argumentet saknar
standardvärde; `None` är det uttryckliga beskedet "homogen atmosfär, Δℓ = Δℓ₀".

"Konvexa höljet" i uppdragstexten: avsnitt 5.21 använder inget konvext hölje, bara punktvis konvexitet (Convex). Något
hölje är därför inte implementerat i modulen. Det används i testerna som oberoende kontroll (se nedan).

### `terrang_forenkling.py`

| Funktion | Innehåll | Källa |
| --- | --- | --- |
| `storsta_tillatna_avvikelse_m(d)` | gräns för r_max: 0,1 / 0,002·d / 1 | TC 2.15 ekv. (1) = AV ekv. 393 första delen |
| `segmentlangdsgrans_m(d)` | d_segm: 1 / 0,05·d / 10 | AV 1106/07 ekv. 393 andra delen (struken i TC) |
| `representativ_flodesresistivitet_kNsm4` | reduktion till 1–3 marktyper | TC 2.15 tabell 14 |
| `storsta_avvikelse` | r_max och dess punkt mellan två terrängpunkter (vinkelrätt avstånd) | figur 44 |
| `forenkla_terrang_detalj` → `Forenkling` | maximal avvikelse-principen, markytans brytpunkter, sammanslagning; redovisar kvarvarande r_max och om kraven nåddes | TC 2.15 |
| `forenkla_terrang` → `Terrangprofil` | samma, bara profilen | |
| `REGLER_TC_101327` (10 segment), `REGLER_TC_101327_SKARMAD` (20), `REGLER_AV_1106_07` (10 + segmentlängd) | färdiga regeluppsättningar | TC 2.15; AV bilaga B |

Förvalet är TC-101327 (utan segmentlängdskrav), eftersom TC ersätter hela bilaga B. Uppdragets "gränser för r_max,
segmentlängd och antal segment" finns alla tre med `REGLER_AV_1106_07`.

## Så anropas funktionerna från kärnan (till P-S)

```python
from buller.nord2000.terrang import terranggeometri
from buller.nord2000.terrang_skarmar import valj_skarmkanter, tolka_terrang, tolka_terrang_homogen

geo = terranggeometri(profil)

# Homogen atmosfär: ett anrop.
t = tolka_terrang_homogen(geo, frekvenser, c)

# Med refraktion: kanterna väljs alltid med räta strålar (AV 1851/00 avsnitt 6.3), sedan räknar
# skärmdelmodellerna Δℓ för just de kanterna och lämnar dem i kroken.
kanter = valj_skarmkanter(geo)              # T₁, primar_form, T₂, sekundar_form, T₁₂ (kandidater)
dl1 = ...  # ekv. 301 ur delmodell 4 för kanter.primar            (None i homogen atmosfär)
dl2 = ...  # ekv. 307 ur delmodell 6 för kanter.sekundar          (None om kanten saknas eller homogen)
dl12 = ... # motsvarande ur delmodell 5 för kanter.sekundar_kant
t = tolka_terrang(geo, kanter, frekvenser, c,
                  gangvagsskillnad_t1_m=dl1, gangvagsskillnad_t2_m=dl2, gangvagsskillnad_t12_m=dl12)

t.r_scr1, t.r_scr2, t.r_scr12   # Flyttal, ett värde per frekvens → ekv. 306, 310, 315 (332)
t.delmodeller                   # t.ex. (4,), (4, 5), (4, 6), (4, 5, 6), (6,) eller ()
t.primar, t.primar_form         # T₁ och W₁–W₂ till delmodell 4/5 (None när r_scr1 = 0 i alla band)
t.sekundar, t.sekundar_form     # T₂ och dess form till delmodell 6 (None när r_scr2 = 0 i alla band)
t.sekundar_kant                 # T₁₂ till delmodell 5 (None när r_scr12 = 0 i alla band)
```

`Skarmkant.gangvagsskillnad_m` är Δℓ₀: för T₁ mot S och R, för T₂ och T₁₂ mot T₁ eller T₁' enligt 5.21.2.
`t.delmodeller`: 6 när r_scr2 > 0 i något band; 4 och/eller 5 när r_scr2 < 1 i något band (5 om r_scr12 > 0 i något
band, 4 om r_scr12 < 1 i något band). Dagens spärr i `karna.py` ("r_scr1 > 0 → BullerFel") kan ersättas med
`t.delmodeller` när delmodellerna finns.

Förenklingen är förbehandling och anropas inte av kärnan:

```python
from buller.nord2000.terrang_forenkling import forenkla_terrang_detalj, REGLER_TC_101327_SKARMAD
f = forenkla_terrang_detalj(tat_profil, REGLER_TC_101327_SKARMAD)   # eller forenkla_terrang(...) → Terrangprofil
f.profil, f.kraven_uppfyllda, f.r_max_kvar_m, f.r_max_grans_m
```

`kraven_uppfyllda = False` betyder att N_ts,max nåddes med r_max över gränsen. Det är inget fel enligt bilagan, men
anroparen bör logga det (AGENTS.md: inga tysta fel).

## Prövat

`python -m pytest tests/buller/nord2000/test_terrang_skarmar.py tests/buller/nord2000/test_terrang_forenkling.py -q`
→ **63 gröna** (44 + 19), 1,5 s. `python -m pytest tests/buller/nord2000 -q` → **237 gröna**, 4,8 s (inga fel i andra
agenters filer vid körningen). `ruff check`, `ruff format --check` och `mypy` (strict) på de fyra filerna: rent.

### Terrängtolkningen

| Vad | Hur | Utfall |
| --- | --- | --- |
| VertDist, Convex | handräknade punkter; marginalen 0,0001 m (0,00005 → inte konvex, 0,0002 → konvex) | 12 decimaler |
| Δℓ₀ med valfria ändpunkter | lika med `terrang.gangvagsskillnad_m` när ändpunkterna är S och R, alla inre punkter i 4 profiler; 3-4-5-triangel ger ±2 | bitexakt |
| Skärmform | tunn skärm 3 punkter, tjock 4, vall 5; konvex rygg når profilens ändar | – |
| T₁ på konvexa höljet | 300 slumpade profiler (4–13 punkter): när Δℓ₀(T₁) > 0 ligger T₁ på övre höljet av S, terrängpunkterna och R (höljet räknat i testet med monoton kedja; följer av att \|SP\| + \|PR\| är konvex i P) | > 100 prövade, alla på höljet |
| T₂ | två tunna skärmar (5 m och 4 m): Δℓ₀(T₂) = \|T₁T₂\| + \|T₂R\| − \|T₁R\| handräknat, på båda sidor | 12 decimaler |
| T₁' | T₁ under siktlinjen: ersättningspunkten (50; 2,0) på SR; Δℓ₀ för en punkt på källsidan handräknat mot S–T₁' | 12 decimaler |
| Uteslutningar | primärformens punkter blir inte T₂; punkter under baslinjen blir varken T₂ eller T₁₂ | – |
| r_Δℓ, r_λ, r_Fz, r_w | brytpunkter och mittvärden: Δℓ'/λ = −1/30 → 0,5; h/λ = 0,2 → 0,5; h/h_Fz = 0,054 → 0,5; d₁₂ = 0,65λ → 0,5 | 12 decimaler |
| r_scr1 med krok | `None` ger samma tal som `terrang.skarmparameter_rscr1` i alla 27 band, 5 profiler; Δℓ ≥ 0 ger r_Δℓ = 1; Δℓ < Δℓ₀ ändrar inget | bitexakt |
| r_scr2, r_scr12 | lika med produkten av faktorerna räknade var för sig (ekv. 309, 314); r_scr2 = 0 när d₁₂ ≤ 0,3λ | 12 decimaler |
| Figur 34 | \|P₀T₁₂\| = \|ST₁₂\|, P₀ på strålen T₁₂→W₁, T₁₂' på P₀T₁, vinkelrät fot, d₁ + d₂ = \|P₀T₁\| | 8–10 decimaler |
| Nästan rak kant | T₁₂ 1 mm över linjen W₁–T₁: h'_SCR12 < 2 mm, r_scr12 = 0 i alla band, bara delmodell 4 | – |
| Spegling | källa och mottagare byter plats: samma kanter (speglade index), samma r_scr1, r_scr2, r_scr12 i alla band; 4 konstruerade + 100 slumpade profiler | ≤ 10⁻⁹ |
| Sammanställning | plan mark → ingen skärm; tunn → (4); tjock → 5 med; två skärmar → 6 med; två gupp på 2 cm → r_scr1 = 0 och ingen sekundär skärm trots kandidat; 0 ≤ r ≤ 1 i 200 slumpade profiler | – |

### Testfallens profiler (RL 18/18, rak väg) – utan facit

Inläst med `buller.testfall.format.las_fall(...).profil(h_s)`, homogen atmosfär (c vid 15 °C), tre källhöjder
(0,01, 0,30 och 0,75 m). Koordinater som i testfallet (x från vägmitt; källan i x = 3,25). Fall x2 och x3 har samma
profil som x1; fall 41–44 samma geometri som 31–34. Kolumnerna r anger band (nominella) där parametern är > 0.

| Fall | h_r | h_s | T₁ (x; z), Δℓ₀ m | Primärform | r_scr1 | T₂, Δℓ₀ | r_scr2 | T₁₂, Δℓ₀ | r_scr12 | Skärmtyp | Delmodeller |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 31, 41 | 1,5 | 0,01 | (6,00; 1,5) −0,0000 | 5,00…7,00 (3 p) | 1–10 kHz, max 0,96 | – | 0 | – | 0 | en skärm, en kant | 4 |
| 31, 41 | 1,5 | 0,30 | (6,00; 1,5) −0,0159 | 5,00…7,00 (3 p) | 1–2,5 kHz, max 0,08 | – | 0 | – | 0 | en skärm, en kant | 4 |
| 31, 41 | 1,5 | 0,75 | (6,00; 1,5) −0,0975 | 5,00…7,00 (3 p) | 0 | – | 0 | – | 0 | ingen skärm | – |
| 34, 44 | 4 | 0,01 | (5,00; 1,5) −0,0009 | 3,25…7,00 (4 p) | 1,25–10 kHz, max 0,65 | – | 0 | (6,00; 1,5) −0,0033 | 1–10 kHz, max 0,49 | en skärm, två kanter | 4, 5 |
| 34, 44 | 4 | 0,30 | (6,00; 1,5) −0,0245 | 5,00…7,00 (3 p) | 1–1,6 kHz, max 0,02 | – | 0 | – | 0 | en skärm, en kant | 4 |
| 34, 44 | 4 | 0,75 | (6,00; 1,5) −0,1179 | 5,00…7,00 (3 p) | 0 | – | 0 | – | 0 | ingen skärm | – |
| 51, 54, 61, 64 | 1,5 / 4 | alla | – (ingen inre punkt över baslinjen) | – | 0 | – | 0 | – | 0 | ingen skärm | – |
| 71 | 1,5 | 0,01 / 0,30 / 0,75 | (15,00; 3) +0,3762 / +0,3120 / +0,2238 | 14,99…15,01 (3 p) | alla band, = 1 från 40 Hz | – | 0 | – | 0 | en skärm, en kant | 4 |
| 74 | 4 | 0,01 / 0,30 / 0,75 | (15,00; 3) +0,2981 / +0,2414 / +0,1648 | 14,99…15,01 (3 p) | alla band, = 1 från 40 Hz | – | 0 | – | 0 | en skärm, en kant | 4 |
| 81 | 1,5 | 0,01 / 0,30 / 0,75 | (15,00; 2) +0,1573 / +0,1164 / +0,0649 | 14,99…30,01 (4 p) | alla band, = 1 från 63 Hz | – | 0 | (30,00; 2) +0,0003 | alla band, = 1 från 63 Hz | en skärm, två kanter | 4, 5 |
| 84 | 4 | 0,01 / 0,30 / 0,75 | (15,00; 2) +0,1086 / +0,0751 / +0,0353 | 14,99…30,01 (4 p) | alla band, = 1 från 63 Hz | – | 0 | (30,00; 2) −0,0050 | 25 Hz–8 kHz, max 0,92 | en skärm, två kanter | 4, 5 |
| 91 | 1,5 | 0,01 / 0,30 | (15,00; 2) +0,1573 / +0,1164 | 14,99…15,01 (3 p) | alla band, = 1 från 63 Hz | (80,00; 3) +0,0624 | alla band, = 1 från 40 Hz | – | 0 | två skärmar | 4, 6 |
| 91 | 1,5 | 0,75 | **(80,00; 3)** +0,0862 | 74,99…85,01 (5 p) | alla band, = 1 från 40 Hz | (15,00; 2) +0,0410 | alla band, = 1 från 63 Hz | (85,00; 2,5) −0,0021 | alla band, max 0,94 | två skärmar | 4, 5, 6 |
| 94 | 4 | 0,01 / 0,30 / 0,75 | (15,00; 2) +0,1086 / +0,0751 / +0,0353 | 14,99…15,01 (3 p) | alla band, = 1 från 63 Hz | (80,00; 3) −0,0092 | 25 Hz–4 kHz, max 0,91 | – | 0 | två skärmar | 4, 6 |
| 101, 104 | 1,5 / 4 | alla | – (ingen inre punkt över baslinjen) | – | 0 | – | 0 | – | 0 | ingen skärm | – |
| 111 | 1,5 | 0,01 / 0,30 / 0,75 | (25,00; 1,5) +0,0395 / +0,0256 / +0,0100 | 7,00…60,00 (4 p) | alla band, = 1 från 80 Hz | – | 0 | (35,00; 1,5) +0,0000 | 40 Hz–10 kHz, = 1 från 125 Hz | en skärm, två kanter | 4, 5 |
| 114 | 4 | 0,01 / 0,30 / 0,75 | (25,00; 1,5) +0,0104 / +0,0040 / +0,0000 | 7,00…60,00 (4 p) | alla band, = 1 från 80 Hz | – | 0 | (35,00; 1,5) −0,0064 | 40 Hz–6,3 kHz, max 0,87 | en skärm, två kanter | 4, 5 |

Iakttagelser som de som bygger delmodellerna behöver känna till:

1. **Upphöjd väg (31–44)** blir en skärm bara över 1 kHz och bara för de låga källhöjderna; r_scr1 är litet (max 0,96,
   0,08 resp. 0). Fallen är alltså i huvudsak "NoScreen" (delmodell 3 + plan mark) med inblandning av delmodell 4.
   Baslinjen lutar (vägen ligger 1,5 m över mottagarens mark), så vägytans punkter har h_bas på 3–4 cm.
2. **Fall 34/44 med h_s = 0,01 m:** T₁ blir punkten (5,00; 1,5), som ligger på den plana vägytan och *inte* är konvex –
   den har störst Δℓ₀ (närmast siktlinjen). Formen sträcker sig då från profilens första punkt till släntfoten och
   vägkanten (6,00; 1,5) blir sekundär kant. Det följer av definitionerna men ger delmodell 4 en "kil" med vinkeln π.
3. **Fall 91 med h_s = 0,75 m:** primär och sekundär skärm byter roll (vallen blir primär) jämfört med h_s = 0,01 och
   0,30 m. Delkällorna i samma fall räknas alltså med olika primärskärm.
4. **Kullen (111–114)** tolkas som en skärm med två kanter (platåns båda brytpunkter), inte som terräng utan skärm.
   `B4-uppdelning.md` avsnitt 5 anger "delmodell 3/4" för gruppen; delmodell 5 behövs också.
5. Tunn skärm ger alltid en form med tre punkter (fötterna 1 cm från toppen); tjock skärm fyra; vallen fem.
6. Dal, dal med sjö och dalformad terräng (51–64, 101–104) har inga inre punkter över baslinjen och går helt genom
   "NoScreen".

Med strykande infall (profilen utdragen med 1/cos θ för källpunkter längs vägen) har tolkningen inte körts; det hör till
P-S.

### Terrängförenklingen

| Vad | Hur | Utfall |
| --- | --- | --- |
| Gränser | r_max: 0,1 (d ≤ 50), 0,2 vid 100 m, 1 (d ≥ 500), kontinuerlig i brytpunkterna; d_segm: 1, 5 vid 100 m, 10 | exakt |
| Tabell 14 | 12,5/124,9/125/500/800/800,1/20000 → 3 typer: 31,5/31,5/200/200/200/20000/20000; 2 typer: 200 resp. 20000 över 800; 1 typ: 200 | exakt |
| Plan mark | 1001 punkter på rät linje (lutning 0, 0,03, −0,1) → 2 punkter, ändhöjder och h_S, h_R bevarade, r_max kvar < 10⁻⁹ m; brus ±0,15 m på 100 m (gräns 0,2) → 2 punkter | – |
| Maximal avvikelse | första tillagda punkten = punkten med störst vinkelrätt avstånd till linjen mellan ändpunkterna (20 slumpterränger, räknat oberoende); triangelkulle → exakt tre punkter | – |
| Ordning | punktmängden för N_ts,max = k är delmängd av den för k + 1 (k = 1…14) | – |
| Kraven | 60 slumpterränger (30, 100, 300, 1000 m; 401 punkter) × två regeluppsättningar: antal segment ≤ N_ts,max; ändpunkter och höjder över mark bevarade; varje förenklad punkt är en tät punkt; kvarvarande avvikelse räknad oberoende (avstånd till polylinjen) ≤ gränsen när kraven är uppfyllda, annars antal = N_ts,max | > 10 fall av vardera sorten |
| Segmentlängd (AV 2014) | krusning 0,5 m / 3 m över 100 m, utan tak: varje kvarvarande segment har r_max ≤ 0,2 m eller längd ≤ 5 m; 65 segment mot 68 utan kravet, kvarvarande r_max 0,79 m mot 0,13 m | – |
| Markyta | brytpunkt läggs in med z interpolerat i den förenklade formen; reduktion till 3/2/1 marktyper slår ihop grannar; råhet bevaras | 12 decimaler |
| `bevara_index` | punkterna finns kvar; index utanför profilen → `BullerFel` | – |
| Testfallens profiler | alla 62 för rak väg, förtätade till 0,25 m (388 punkter) och förenklade med N_ts,max = 20: kraven uppfyllda, kvarvarande avvikelse ≤ gränsen (0,19 m), högsta punkten kvar med exakt höjd, samma följd av marktyper, aldrig fler punkter än originalet | 57 av 62 ger exakt originalets punkter |

De fem som inte ger exakt originalet: fall 7 (hela profilen σ = 20000, så punkten vid vägkanten är överflödig) och fall
101–104, där punkten (7,00; 0) faller bort (avvikelse 0,14 m mot gränsen 0,19 m). Med N_ts,max = 10 blir resultatet
detsamma för alla 62.

## Tolkningar

| Datum | Paket | Fråga | Val och skäl |
| --- | --- | --- | --- |
| 2026-11-21 | P-T | Convex(i) är definierad med grannarna i − 1 och i + 1. Vad gäller för profilens första och sista punkt när W₁/W₂ söks (5.21.1)? | De räknas som icke-konvexa, så formen slutar senast i profilens ändpunkt. Alternativ: `BullerFel` när ingen icke-konvex punkt finns; det skulle stoppa en konvex rygg mellan källa och mottagare. |
| 2026-11-21 | P-T | Avsnitt 5.21.2: "If Δℓ … for the primary edge … is less than zero" – Δℓ eller Δℓ₀? | Δℓ₀, enligt TC-101327 avsnitt 2.11. T₁' används alltså när T₁ ligger under den räta siktlinjen, oberoende av refraktion; vid Δℓ₀ = 0 används T₁. |
| 2026-11-21 | P-T | Tecknet på Δℓ₀ för T₂ och T₁₂ (S eller R ersatt med T₁/T₁'). | Samma teckenregel som B4 valde för ekv. 300 (negativt under linjen), nu mot linjen S–T₁* resp. T₁*–R. z_m räknas på den linjen. |
| 2026-11-21 | P-T | Avsnitt 5.21.3: gäller villkoret h_bas > 0 också för T₁₂? Texten nämner bara att W₁, W₂ och T₁ utesluts. | Ja, punkter på eller under baslinjen tas inte med ("by and large the same principles" som för T₂). För en sådan punkt är r_λ12 = 0, så valet påverkar bara vilken av flera kandidater som väljs. Alternativ: alla formens punkter. |
| 2026-11-21 | P-T | Lika Δℓ₀ för två kandidater. | Lägst index (närmast källan), som i `terrang.primar_skarmkant`. Rapporten säger inget. Bryter speglingssymmetrin i exakt symmetriska fall. |
| 2026-11-21 | P-T | Ekv. 312: NormLine(P₀, T₁, T₁₂) ger h'_SCR12 med tecken. På mottagarsidan ligger P₀ till höger om T₁, linjen pekar åt vänster och tecknet blir omvänt (negativt för en kant över linjen), så r'_λ12 = 0 för varje sekundär kant på mottagarsidan. | På mottagarsidan riktas linjen T₁ → P₀, så h'_SCR12 är positivt över linjen på båda sidor. Skäl: texten säger att S byts mot R och W₁ mot W₂, inget om tecken; AV 1849/00 talar om "perpendicular distance"; bokstavlig läsning bryter mot reciprocitet (räknat för fall 81 och 111: bokstavligt h' = −14,65 resp. −0,86 m, i den speglade profilen +14,65 resp. +0,86 m). En kant *under* linjen (konkav form) får h' < 0 och r' = 0, vilket är avsikten. Alternativ: absolutbelopp (skulle göra konkava former verksamma). |
| 2026-11-21 | P-T | Ekv. 313: d₁ och d₂ räknas till T₁₂ eller till fotpunkten T₁₂'? Textutdraget tappar primtecknen. | Till fotpunkten (x'_T12, z'_T12), avläst i sidbilden s. 139. |
| 2026-11-21 | P-T | r_λ12 och r_Fz12 (5.21.3) "in the same way … but T₂ is replaced by T₁₂". | h_SCR = h_bas(i_T12) och h_Fz ur d_bas(i_T12), d_bas − d_bas(i_T12): höjden över hela profilens baslinje, som för T₁ och T₂. d₁₂ i r_w12 är avståndet T₁–T₁₂. |
| 2026-11-21 | P-T | När söks T₁₂? 5.21.3: inte om r_scr2 = 1 i alla band. | Bokstavligt: T₁₂ värderas alltid utom när r_scr2 = 1 i alla 27 band. Delmodell 4 anges som nödvändig när r_scr12 < 1 i något band, delmodell 5 när r_scr12 > 0 i något band (utan hänsyn till om r_scr2 = 1 i just det bandet). |
| 2026-11-21 | P-T | Bilaga B: AV 1106/07 rev. 2014 eller TC-101327 avsnitt 2.15, som ersätter hela texten? | TC-101327 är förval (`REGLER_TC_101327`): gräns för r_max och N_ts,max = 10, eller 20 med skärmar; inget krav på segmentlängd. AV 1106/07:s krav på segmentlängd finns som val (`REGLER_AV_1106_07`). |
| 2026-11-21 | P-T | Vad är d i ekv. 393 / TC ekv. (1)? Inte definierat. | Det horisontella avståndet mellan profilens första och sista punkt. Alternativ: avståndet längs baslinjen eller delsträckans längd; skillnaden är liten utom för branta profiler. |
| 2026-11-21 | P-T | Kravet d_segm ≤ … (AV 1106/07 ekv. 393): övre gräns för alla segment, eller gräns under vilken man inte delar vidare? | Det senare: ett segment som är högst d_segm långt delas inte, även om r_max överskrids. Som övre gräns för alla segment skulle 100 m plan mark kräva 20 segment, i strid med N_ts,max = 10 och med att plan mark ska förbli ett segment. Längden räknas längs segmentet (Length). **Osäkert**; TC har strukit kravet. |
| 2026-11-21 | P-T | N_ts,max: gäller det före eller efter att markytans brytpunkter lagts in (TC 2.15)? | Före. Terrängformen förenklas för sig och begränsas till N_ts,max; brytpunkterna i markytan kommer till (redovisas som `inlagda_markpunkter`). Bilagan beskriver stegen i den ordningen. |
| 2026-11-21 | P-T | TC 2.15: "roughness is assumed to be zero" i den sammanslagna profilen. | Råheten behålls och räknas som del av marktypen (σ, r): en brytpunkt bevaras där σ eller r ändras. Att nollställa den skulle tyst ändra indata (fall 41–44 har r = 0,5 m). Den som vill följa bilagan bokstavligt nollställer råheten före anropet. |
| 2026-11-21 | P-T | TC tabell 14: vart hör gränsvärdena 125 och 800 ("< 125", "125–800", "> 800")? | Till mellanintervallet. |
| 2026-11-21 | P-T | TC 2.15: en inlagd markpunkt får z "by linear interpolation between the nearest points … having a defined value of z". | Interpolation i den *förenklade* terrängformen, inte i den täta profilen; annars skulle förenklingen delvis tas tillbaka. |
| 2026-11-21 | P-T | Lika r_max i två segment eller två punkter. | Lägst index. |

## Önskade ändringar i delade filer

`terrang.py` (ingen är nödvändig för att mitt paket ska fungera):

1. `gangvagsskillnad_m(geo, index)` kan uttryckas med `terrang_skarmar.gangvagsskillnad_mellan_m` (eller den senare
   flyttas till `terrang.py`), så att ekv. 300 finns på ett ställe. I dag är de två bitexakt lika (testat).
2. `skarmparameter_rscr1` saknar krok för ekv. 301. Förslag: ersätt den med `skarmparameter_rscr1_allman` (samma
   resultat med `gangvagsskillnad_refraktion_m=None`), eller låt den anropa `overgang_gangvag`, `overgang_hojd` och
   `overgang_fresnelzon` så att ekv. 302–304 inte finns i två kopior.
3. Modulens docstring: "skärmform W₁–W₂ –" i `B4-uppdelning.md` avsnitt 2 kan ändras till klar, med hänvisning hit.
4. `terrang_skarmar.py` importerar `Skarmkant`, `Terranggeometri` och `primar_skarmkant` från `terrang.py`; byter de namn
   eller fält måste modulen följa med.

`karna.py`: spärren "skärmar" kan byggas på `tolka_terrang_homogen(...).delmodeller` (se anropsexemplet ovan).

`docs/buller/B4-uppdelning.md` avsnitt 5: grupp 11 (kulle, fall 111–114) kräver delmodell 5, inte bara 3/4; grupp 3–4
(upphöjd väg) kräver delmodell 5 för fall 34/44 med lägsta källhöjden.

`data/`: tabell 14 ligger som namngivna konstanter i `terrang_forenkling.py` (fem tal). Vill samordnaren ha den som
datafil med källa (B00 regel 6) är det en flytt utan funktionsändring.

## Återstår

- **Ekv. 301, 307 och Δℓ för T₁₂ med krökta strålar** – i P-4, P-6, P-5. När de finns bör P-S pröva att de går mot Δℓ₀
  när ξ → 0; kroken tar max(Δℓ, Δℓ₀) och döljer annars ett fel i tecknet.
- **Förenkling av skärmformen** till W₁, T₁, (T₁₂), W₂ med "impedans och råhet närmast kanten" (5.21 inledningen,
  AV 1849/00 avsnitt 4.4.4.3). Hör till delmodell 4–6 och är inte gjord här; `Skarmform` ger indexen.
- **Förenkling av terrängen mellan källa, skärmar och mottagare var för sig** (AV 1106/07 bilaga B sista stycket, med S
  eller R ersatt av skärmtoppen och ca 5 segment per del). Stycket finns inte i TC 2.15. `bevara_index` finns för att
  skydda en skärmform, men uppdelningen i delsträckor är inte gjord.
- **Impedansklasser A–H** (TC tabell 13) är inte implementerade; bara tabell 14 (σ-intervall).
- Körning av tolkningen för alla 179 källpunkter per fall (utdragna profiler) och inkoppling i `karna.py` – P-S.

## Osäkert

- Teckenvalet för h'_SCR12 på mottagarsidan (ekv. 312). Det avgör om en tjock skärm vars primära kant är den närmast
  källan (fall 81–84, 111–114) alls får två kanter. Stöds av speglingssymmetri, inte av text.
- Tolkningen av d_segm (se ovan). Påverkar bara `REGLER_AV_1106_07`.
- Ekv. 301 som den är tryckt, Sign(θ_S − θ_R − π)·√(…) − R_S − R_R, ser ut att ge ett negativt tal också för en kant i
  skuggzonen (roten är avståndet S–R genom kanten, alltid mindre än R_S + R_R), medan ekv. 302 förutsätter Δℓ > 0 där.
  Jag har inte utrett vinklarnas definition (ekv. 162) och har inte implementerat ekvationen; den som gör det bör
  jämföra med AV 1851/00 ekv. 53 och med Δℓ₀ i gränsen utan refraktion.
- T₁ kan bli en icke-konvex punkt (fall 34, h_s = 0,01 m). Definitionerna tillåter det; hur delmodell 4 ska behandla en
  kil med vinkeln π är en fråga för P-4.
- Vid lika Δℓ₀ väljs lägst index. I fall 114 med h_s = 0,75 m är Δℓ₀(T₁) = +0,000011 m, alltså nära gränsen mellan
  T₁ och T₁'; små ändringar i geometrin byter gren (T₁/T₁'), men Δℓ₀ för T₁₂ är kontinuerlig över bytet.
- Bilaga B är informativ och ett förslag. Förenklingen är prövad mot sina egna krav, inte mot att ljudnivån bevaras.
