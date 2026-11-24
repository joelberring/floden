# P-S5 – Kurvig väg i vind (Delmodell 6) och konkava segment i motvind (Delmodell 3)

Datum: 2026-11-24. Gren: `nord2000-kurvig-och-motvind`.

**Läget i korthet.** Två kvarstående motvinds- och refraktionsspärrar i `delmodell6.py` och
`delmodell3.py` löstes utifrån metodtexten (AV 1106/07 avsnitt 5.5.2, 5.7.3 och 5.12 samt AV 1849/00
s. 35), utan anpassning av konstanter mot facit. Samtliga **10/10 kurvig-väg-fall** (`N18K1–K8`, alla
30 vindserier) och **4/4 stadsgata-fall** (`N18S1–S4`) är nu godkända, och inga rak-väg-fall är
längre ej körbara.

Hela grupp N (80 RL 18-18-fall):

- **55 godkända** (upp från 45 efter PR #86; **+10 nya**: `N18K1`, `N18K2`, `N18K3`, `N18K4-1`,
  `N18K4-2`, `N18K6`, `N18K7`, `N18K8`, `N18R053`, `N18R063`)
- **16 ska utredas** (13 rak väg inkl. `N18R033`, `N18R043` + 3 skog `N18R121`, `N18R123`, `N18R124`)
- **5 underkända** (3 dal medvind `N18R052, 062, 102` + dubbeldal motvind `N18R103` + skog medvind `N18R122`)
- **4 ej körbara** (ned från 17; enbart årsmedel `N18A1–A4` i väntan på `VTT-R-02530-06`)

`nord2000_konform` förblir `False`. Inga facitvärden ingår i kod eller logg.

---

## 1. Delmodell 6 med refraktion när en skärmtopp ligger under linjen från den andra

I `src/buller/nord2000/delmodell6.py` (`skarmstralar_tva`) gav refraktion tidigare `BullerFel` när
$T_2$ låg under linjen $T_1\text{–}R$ eller $T_1$ under linjen $T_2\text{–}S$ (P-6 tolkning 11).

**Metodgrund:** AV 1106/07 avsnitt 5.7.3 mäter då $\theta_{1R}$ mot $R$ respektive $\theta_{2S}$ mot $S$.
Avsnitt 5.5.2 anger att en krökt stråle som startar eller slutar i en skärmtopp använder den
ekvivalenta profilen `CalcEqSSP(h″_SCR, h_Rv)` (för $T_1\text{–}R$, längd $d_2 + d_3$) respektive
`CalcEqSSP(h_Sv, h″_SCR)` (för $S\text{–}T_2$, längd $d_1 + d_2$). Det ger exakt kontinuitet mot
delmodell 4 för den kvarstående skärmen (verifierat i `test_delmodell6.py` till 12 decimaler).

---

## 2. Delmodell 3 i motvind (geometriskt konkava segment)

I `src/buller/nord2000/delmodell3.py` (`klassa_segment_refraktion`) minskar $h̆' = R \sin\psi_G$ vid
uppåtbrytning även för segment som geometriskt redan är plana eller konkava ($h'_S \ge h_S$ resp.
$h'_R \ge h_R$, AV 1849/00 s. 35). Tidigare klassades sådana segment som övergångs- eller konvexa
segment trots att ingen konvex kil ($\beta > \pi$) existerar, vilket gav `WedgeCross`- eller
$\beta < \pi$-fel i samtliga 5 motvindsfall på rak väg samt `N18K7` och `N18K8`.

**Metodgrund:** En sida som geometriskt uppfyller $h'_S \ge h_S$ respektive $h'_R \ge h_R$ behåller
$h_{\text{rel}} = 1$ även i motvind och skuggzon, så att `SubModel1` (med full skuggzonsmodell
$\Delta L_{\text{SZ}}$) används för det konkava segmentet. Plan mark delad i segment återger nu
exakt $\Delta L_1$ även i motvind (verifierat i `test_delmodell3.py`). Jämförelse mot alternativet
ren geometrisk klassning överallt gav identiska utfall inom `0,01 dB` på `N18R033–103`.

---

## 3. Uppmätta resultat

### 3.1 Kurvig väg (`N18K1–K8`, 10 fall, 30 vindserier $u \in \{0, +3, -3\}\text{ m/s}$)

| Fall | Utfall (alla 3 serier) | Max avvikelse total (dB) | Körtid |
| --- | --- | --- | --- |
| `N18K1` | **godkänd** | 0,59 | 13,5 s |
| `N18K2` | **godkänd** | 1,66 | 28,8 s |
| `N18K3` | **godkänd** | 0,15 | 15,9 s |
| `N18K4-1` | **godkänd** | 0,74 | 12,0 s |
| `N18K4-2` | **godkänd** | 0,80 | 11,5 s |
| `N18K5-1` | **godkänd** | 0,62 | 3,3 s |
| `N18K5-2` | **godkänd** | 0,15 | 2,0 s |
| `N18K6` | **godkänd** | 0,22 | 31,2 s |
| `N18K7` | **godkänd** | 0,16 | 33,8 s |
| `N18K8` | **godkänd** | 0,81 | 33,2 s |

### 3.2 Tidigare ej körbara rak-väg-fall i motvind ($u = -3\text{ m/s}$)

| Fall | Tidigare | Nu | Max avvikelse total (dB) | Max avvikelse band (dB) |
| --- | --- | --- | --- | --- |
| `N18R033` | ej körbar | ska utredas | 0,25 | 1,03 |
| `N18R043` | ej körbar | ska utredas | 0,25 | 1,07 |
| `N18R053` | ej körbar | **godkänd** | 0,06 | 0,15 |
| `N18R063` | ej körbar | **godkänd** | 0,04 | 0,10 |
| `N18R103` | ej körbar | underkänd | 1,11 | 5,62 |

Samtliga övriga 57 rak-väg-fall kördes om utan regressioner.

---

## 4. Verifiering

- `pytest tests/buller/nord2000/ tests/buller/testfall/test_kurvig_vag.py tests/buller/testfall/test_stadsgata.py -q`: **754 passed** (25,88 s).
- `ruff check src/buller tests/buller`: **0 fel**.
- `mypy src/buller`: **0 fel** (83 källfiler).
