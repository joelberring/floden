# P-S6 – Spridningszonens strålkrökning (Delmodell 10) och övergångshöjder i Delmodell 3

Datum: 2026-11-25. Gren: `nord2000-underkanda`.

**Läget i korthet.** Två metodgrundade rättningar i `delmodell10.py` / `karna.py` / `sammansattning.py`
och `delmodell3.py` (utan anpassning av konstanter mot facit):

1. **Delmodell 10 (spridningszon/skog, AV 1106/07 ekv. 292 samt AV 1851/00 avsnitt 7):**
   `karna.py` använder nu `delmodell8.cirkelstrale_ur_atmosfar` (`RayCurvature` +
   `HeightOfCircularRay`) med nedre markbegränsning `MarkbegransadStrale` vid uppåtbrytning, i
   stället för `CalcEqSSP(h_S, h_R)` + `delmodell10.CirkelStrale`. Samtidigt förs
   koherensfaktorn $F_s$ (`koherens_spridningszon`, ekv. 288 och avsnitt 5.22) vidare från
   `sammansattning.terrangeffekt` även till delmodell 3–6 (tidigare enbart delmodell 1–2).
   **Utfall:** skogsmedvindsfallet `N18R122` går från **underkänd** (`1,52 / 3,05 dB`) till
   **ska utredas** (`0,71 / 2,30 dB`, i linje med `N18R121`, `N18R123` och `N18R124`).
2. **Delmodell 3 (övergångssegment, AV 1849/00 avsnitt 4.4.2 / P-3 tolkning 9):**
   Bugg i `delmodell3.py` rättad: läget `overgangshojd="bis"` anropade tidigare
   `markeffekt_plan_mark_db` med ett enbandsutsnitt (`frekvenser_hz[b:b+1]`), vilket kraschade i
   `PhaseDiffFreq` vid refraktion/fluktuationer och fick P-S4 att felaktigt förkasta `"bis"`.
   Efter rättning (hela frekvensvektorn, cachat per $(h''_S, h''_R)$) bekräftas den diskreta
   läsningen `"bis"` (AV 1849/00 avsnitt 4.4.2) som förval ("avgjord av testfall", `"prim"` finns
   kvar som val). **Utfall:** `N18R101` (`0,44 / 1,25 → 0,04 / 0,19 dB`) och `N18R104`
   (`0,58 / 0,90 → 0,00 / 0,04 dB`) blir **godkända**, och `N18R053` (`0,15 → 0,08 dB`) samt
   `N18R063` (`0,10 → 0,03 dB`) förbättras utan regressioner.

Aggregerat läge i grupp N (62 rak-väg-fall direkt uppmätta i P-S6; `N18K1–K8` och `N18S1–S4`
kräver geometri ur `.xls` där `xlrd` saknas i den lokala `.venv` och har därför inte körts om
här – `N18S1–S4` använder varken delmodell 3 eller 10, medan `N18K1–K8` bör regressionstestas
i miljö med `xlrd`):

- **Rak väg (62 RL 18-18-fall, direkt uppmätt):**
  - **43 godkända** (upp från 41 efter P-S5; **+2 nya**: `N18R101`, `N18R104`, **0 regressioner**)
  - **15 ska utredas** (ned från 16: `-2` till godkänd, `+1` `N18R122` från underkänd)
  - **4 underkända** (ned från 5: `N18R052`, `N18R062`, `N18R102`, `N18R103`; `N18R122` åtgärdad)
- **Övriga grupp N (18 fall, oförändrad status sedan P-S5, ej omkörda i P-S6):**
  - 10 kurvig väg (`N18K1–K8`, godkända i P-S5), 4 stadsgata (`N18S1–S4`, godkända i P-S5),
    4 årsmedel (`N18A1–A4`, ej körbara i väntan på `VTT-R-02530-06`).
  - Totalt utfall vid oförändrade `N18K`/`N18S`: **57/80 godkända**, **15 ska utredas**,
    **4 underkända**, **4 ej körbara**.

`nord2000_konform` förblir `False`. Inga RL 18-18-facitvärden ingår i kod, tester, tolkningar
eller denna logg.

---

## 1. Genomförda metodändringar

### 1.1 Strålkrökning i spridningszon (AV 1106/07 ekv. 292, AV 1851/00 avsnitt 7)
- `src/buller/nord2000/delmodell10.py`: lade till `MarkbegransadStrale` som omsluter en
  `Stralbana` och klipper strålhöjden nedåt mot terrängprofilen (`z_ray(x) ≥ z_mark(x)`) utan
  att ändra $R_1, R_2$, enligt AV 1851/00 avsnitt 7 för uppåtbrytning.
- `src/buller/nord2000/karna.py`: ersatte `calc_eq_ssp(geo.h_s_m, geo.h_r_m, atmosfar)` +
  `delmodell10.CirkelStrale` med `delmodell8.cirkelstrale_ur_atmosfar(...)` inslagen i
  `MarkbegransadStrale`. Med `CalcEqSSP` vid låg däckkälla ($h_s = 0{,}01\text{ m}$) blev
  krökningen ~4 gånger för stark ($\xi \approx 2{,}2 \cdot 10^{-3}\text{ m}^{-1}$ i stället för
  $\xi_{\text{ray}} \approx 5{,}1 \cdot 10^{-4}\text{ m}^{-1}$), så sneda medvindsstrålar gick
  över 10 m-trädkronorna i `N18R122`.
- `src/buller/nord2000/delmodell3.py` och `src/buller/nord2000/sammansattning.py`:
  `koherens_spridningszon` ($F_s$) förs nu vidare även till delmodell 3, 4, 5 och 6 enligt
  AV 1106/07 avsnitt 5.22.

### 1.2 Övergångssegmentets höjder i delmodell 3 (AV 1849/00 avsnitt 4.4.2)
- `src/buller/nord2000/delmodell3.py`: rättade anropet för `overgangshojd="bis"` till att ge hela
  frekvensvektorn `frekvenser_hz` (cachat per $(h''_S, h''_R)$) till `markeffekt_plan_mark_db`,
  samt satte `"bis"` som nytt förval i `delmodell3`, `Sammansattningsval` och `Nord2000Karna`
  (dokumenterat i `docs/buller/tolkningar.md`, P-S6 post 2, "avgjord av testfall").

---

## 2. Uppmätta ändringar på rak väg i grupp N (62 fall)

| Fall | Före (P-S5) | Nu (P-S6) | Max total (dB) | Max band (dB) | Förändring |
| --- | --- | --- | --- | --- | --- |
| `N18R122` | underkänd (`1,52 / 3,05`) | ska utredas | `0,71` | `2,30` | Ekv. (292) `RayCurvature` |
| `N18R101` | ska utredas (`0,44 / 1,25`) | **godkänd** | `0,04` | `0,19` | `overgangshojd="bis"` |
| `N18R104` | ska utredas (`0,58 / 0,90`) | **godkänd** | `0,00` | `0,04` | `overgangshojd="bis"` |
| `N18R053` | godkänd (`0,06 / 0,15`) | **godkänd** | `0,05` | `0,08` | Förbättrad |
| `N18R063` | godkänd (`0,04 / 0,10`) | **godkänd** | `0,02` | `0,03` | Förbättrad |

Samtliga övriga 41 tidigare godkända rak-väg-fall är fortsatt godkända (0 regressioner).

---

## 3. Utredda kvarvarande avvikelser (öppna metodfrågor, inga konstanter anpassade)

1. **Dal i medvind (`N18R052`: `5,99/7,28`, `N18R062`: `5,88/6,96`, `N18R102`: `2,85/4,39`, underkända):**
   Koden följer AV 1106/07 ekv. (134)–(156) bokstavligt. Segmentdiagnostik visar att vid låg källa
   ($h_s = 0{,}01\text{ m}$) ger `CalcEqSSP(h'_S, h'_R)` för vägbanan (`seg 0`) mycket brant
   krökning, så Fresnelzonen dras ihop mot den hårda vägbanan i 630–2 500 Hz, medan höga band
   (10 kHz) överensstämmer inom `0,21–0,44 dB`. Prövade alternativ (`RayCurvature` i ekv. 135,
   `CalcEqSSPGround` $\xi(f)$ i ekv. 134, räta Fresnelzoner, ekvivalent plan mark-höjd i ekv. 135)
   ger antingen sämre band eller regression på `N18R053`/`N18R102` och saknar stöd i metodtexten.
   Bör ställas som öppen metodfråga till Kunskapscentrum om buller (samma som P-S2 §6 fråga 1).
2. **Dubbeldal i motvind (`N18R103`: `1,20/6,25`, underkänd):**
   Överensstämmer inom `0,04 dB` upp till 500 Hz; från 630 Hz till 10 kHz ligger kärnan över
   referensen (topp vid 4 kHz). Skillnaden mot de godkända enkeldalarna `N18R053`/`N18R063` är
   mittpartiet (`seg 3–5`, $z = -1 \to 0 \to -2$) där `seg 3` (konvex kil) får en djup dipp vid
   500 Hz och sedan stiger mot −19 dB vid 4 kHz och `seg 5` får $\theta_S > \beta$ i
   `kil_med_refraktion` (ekv. 147–150). Koden följer ekv. (142)–(150); ingen metodbugg funnen.
3. **Skog i övergångsbandet 315–3 150 Hz (`N18R121–124`: `0,49–0,76` tot, `2,30–2,75` band):**
   Band $\le 250\text{ Hz}$ ($k_f = 0$) och $\ge 4\,000\text{ Hz}$ ($F_s = 0, \Delta L_s = -15\text{ dB}$)
   ligger inom `0,00–0,01 dB`. Kontrollkörning med $F_s = 0$ resp. $F_s = 1$ visar att avvikelsen
   i 1 250–3 150 Hz ligger strikt i hur referensprogrammet beräknar eller viktar $F_s$ / $k_f(ka)$
   i övergångsbandet (logaritmisk $ka$-interpolation i tabell 8 förändrar inte totalavvikelsen).
   Ingen konstant anpassas mot facit.
4. **Mjuk kulle (`N18R111–114`: `0,30–0,70` tot, `3,75–5,14` band vid 200–315 Hz) och upphöjd väg
   (`N18R031–043`: `0,01–0,26` tot, `1,03–3,14` band över 3,15 kHz):**
   `N18R111` går till 100 % genom delmodell 5 (TC-101327 bilaga G.2/G.3) i 125–630 Hz;
   `N18R031` går genom delmodell 4 över 2 kHz. Klippt Fresnelzon i delmodell 4/5 prövades utan
   effekt (`0,00 dB` på `N18R031/032/111`), och `delmodell4_kilben.py` kontrollerades rad för rad
   mot TC-101327 bilaga G.2/G.3 utan avvikelse.

---

## 4. Verifiering

- Nya oberoende analytiska enhetstester (inga facitvärden):
  - `tests/buller/nord2000/test_delmodell10.py`: `MarkbegransadStrale` (AV 1851/00 avsnitt 7) och
    `Nord2000Karna` med `RayCurvature` + `HeightOfCircularRay` (AV 1106/07 ekv. 292).
  - `tests/buller/nord2000/test_delmodell3.py`: `overgangshojd="bis"` med refraktion/fluktuation
    samt propagering av `koherens_spridningszon` ($F_s$) genom delmodell 3.
  - `tests/buller/nord2000/test_sammansattning.py`: vidarebefordran av `koherens_spridningszon`
    till delmodell 3 och 4.
- `pytest tests/buller/ -q`: **1 067 passed, 38 skipped**.
- `ruff check src/buller tests/buller`: **0 fel**.
- `mypy src/buller`: **0 fel** (83 källfiler).
