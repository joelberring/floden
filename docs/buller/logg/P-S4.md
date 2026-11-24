# P-S4 – Skogsfall (Delmodell 10), körning av nya falltyper och genomgång av "ska utredas"

Datum: 2026-11-24. Gren: `nord2000-skog-och-utredning`.

**Läget i korthet.** Delmodell 10 (spridningszoner/skog, AV 1106/07 avsnitt 5.19) kopplades in i
`Nord2000Karna` och testfallsköraren `korare.py`, så att skogsfallen `N18R121–124` nu är körbara.
Tillsammans med kurvig väg (`N18K1–K8`) och stadsgata (`N18S1–S4`) ger hela grupp N (80 RL 18-18-fall):

- **44 godkända** (förut 39; **+5 nya**: `N18K5-1`, `N18K5-2`, `N18S1`, `N18S2`, `N18S3`)
- **14 ska utredas** (11 rak väg + 3 skog: `N18R121`, `N18R123`, `N18R124`)
- **5 underkända** (3 dal medvind + 1 skog medvind `N18R122` + 1 stadsgata parallellgata `N18S4`)
- **17 ej körbara** (ned från 27: 5 rak väg motvind + 8 kurvig väg vind + 4 årsmedel `N18A1–A4`)

`nord2000_konform` förblir `False`. Inga facitvärden ingår i kod eller logg.

Underlag: DELTA AV 1106/07 (rev. 2014) avsnitt 5.9, 5.10–5.11, 5.12, 5.14–5.15, 5.19–5.20, 5.23.9;
AV 1849/00 avsnitt 4.4.2 och 5; FORCE TC-101327 (2019) bilaga G; DELTA RL 18-18 kap. 2–5.

---

## 1. Skogsfallen (`N18R121–124`, Delmodell 10)

### 1.1 Ändringar i kärna och körare
1. **Koherensfaktor $F_s$ (AV 1106/07 ekv. 110 och 288):** `koherens_spridningszon: Flyttal | None = None`
   fördes in i `delmodell1.markeffekt_plan_mark_db`, `delmodell2.markeffekt_blandad_mark_db` och
   `sammansattning.terrangeffekt` (bakåtkompatibelt förval `None`, dvs. $F_s = 1$).
2. **Krökt stråle i spridningszon (AV 1106/07 ekv. 292 och 5.23.9):** `CirkelStrale` lades till i
   `delmodell10.py` för cirkelbåge med radie $R = 1/|\xi|$ vid med- och motvind.
3. **Tabell 9 vid $R' > 10$ (AV 1106/07 ekv. 291):** För sneda strålar längs rak väg blir
   $R' = n_Q R_{sc} \gg 10$ (upp till $\approx 300$ vid $\theta = 89^\circ$). `nivakorrektion_ae_db`
   begränsar nu $R'$ till tabell 9:s intervall $[0{,}0625, 10]$ (dokumenterat i `docs/buller/tolkningar.md`,
   P-10 post 5). `tabellvarde_dl_db` kastar fortsatt `BullerFel` utanför tabellen.
4. **Kärna och körare:** `Nord2000Karna.berakna` / `overforing_db` tar nu valfritt
   `spridningszon: Spridningszon | None = None` utan ändring i kontraktet `KB1/v1` (`Terrangprofil`).
   `korare.py` läser `[ovrigt] skog_*` för skogsfall och skalar bältets $x$-koordinater med $1/\cos\theta$.

### 1.2 Mätresultat `N18R121–124`

| Fall | Villkor | Utfall | Max avvikelse total (dB) | Max avvikelse band (dB) | Körtid |
| --- | --- | --- | --- | --- | --- |
| `N18R121` | $h_r = 1{,}5\text{ m}, u = 0$ | ska utredas | 0,66 | 2,50 | 6,6 s |
| `N18R122` | $h_r = 1{,}5\text{ m}, u = +3$ | underkänd | 1,52 | 3,05 | 7,6 s |
| `N18R123` | $h_r = 1{,}5\text{ m}, u = -3$ | ska utredas | 0,49 | 2,76 | 7,7 s |
| `N18R124` | $h_r = 4{,}0\text{ m}, u = 0$ | ska utredas | 0,76 | 2,55 | 6,6 s |

**Observation och hypotes (skog):** I `N18R121` och `N18R124` ligger samtliga tersband
$\le 250\text{ Hz}$ (där $k_f = 0$) och $\ge 4\,000\text{ Hz}$ (där $\Delta L_s = -15\text{ dB}$ och
$F_s \approx 0$) inom **0,00–0,01 dB**. Hela avvikelsen ligger i övergångsbandet $315\text{–}3\,150\text{ Hz}$
där $0 < k_f < 1$ (topp $-2{,}50\text{ dB}$ vid $800\text{ Hz}$, avtagande till $-0{,}87\text{ dB}$ vid
$1\,250\text{ Hz}$ och $-0{,}02\text{ dB}$ vid $3\,150\text{ Hz}$). Samtidigt visar kontrollkörning utan
$F_s$ att koherensminskningen $F_s = 1 - k_f T$ står för upp till $+15{,}9\text{ dB}$ i markdippen.
Hypotes: skillnaden i $315\text{–}3\,150\text{ Hz}$ beror på hur referensprogrammet interpolerar $k_f(ka)$
(tabell 8) eller kombinerar $F_s$ och $\Delta L_s$ i övergångsbandet; ingen konstant anpassas mot facit.

---

## 2. Körning av nya falltyper i hela grupp N (80 fall)

### 2.1 Stadsgata (`N18S1–S4`, 4 fall, 6 serier per fall: $h_r \in \{1{,}5; 4; 8\}\text{ m} \times \rho_E \in \{1{,}0; 0{,}7\}$)

| Fall | Utfall | Max avvikelse total (dB) | Max avvikelse band (dB) | Körtid |
| --- | --- | --- | --- | --- |
| `N18S1` | **godkänd** | 0,22 | 0,34 | 3,7 s |
| `N18S2` | **godkänd** | 0,17 | 0,22 | 5,3 s |
| `N18S3` | **godkänd** | 0,21 | 0,24 | 7,4 s |
| `N18S4` | underkänd | 3,32 | 3,81 | 68,2 s |

`N18S1–S3` passerar samtliga 18 serier och alla 27 tersband med bred marginal. `N18S4` (parallellgata
bakom 12 m hög byggnad) saknar ännu 2:a ordningens reflektioner på den skärmade parallellgatan.

### 2.2 Kurvig väg (`N18K1–K8`, 10 fall, 3 vindserier per fall: $u \in \{0, +3, -3\}\text{ m/s}$)

| Fall | Utfall (alla 3 serier) | Max total (alla serier) | Avvikelse vid $u = 0$ (dB) | Orsak vid $u = \pm 3\text{ m/s}$ |
| --- | --- | --- | --- | --- |
| `N18K1` | ej körbar | – | +0,77 | Delmodell 6 med refraktion (ekv. 226) |
| `N18K2` | ej körbar | – | −0,74 | Delmodell 6 med refraktion (ekv. 226) |
| `N18K3` | ej körbar | – | −0,44 | Delmodell 6 med refraktion (ekv. 226) |
| `N18K4-1` | ej körbar | – | −0,46 | Delmodell 6 med refraktion (ekv. 226) |
| `N18K4-2` | ej körbar | – | −0,41 | Delmodell 6 med refraktion (ekv. 226) |
| `N18K5-1` | **godkänd** | **0,62** | −0,62 | – (alla 3 serier godkända, 3,2 s) |
| `N18K5-2` | **godkänd** | **0,15** | −0,14 | – (alla 3 serier godkända, 2,0 s) |
| `N18K6` | ej körbar | – | −1,23 | Delmodell 6 med refraktion (ekv. 226) |
| `N18K7` | ej körbar | – | −0,06 | Delmodell 3 motvind (Tolkning 7) |
| `N18K8` | ej körbar | – | +0,69 | Delmodell 6 med refraktion (ekv. 226) |

Vid vindstilla ($u = 0$) ligger **10 av 10** kurvig-väg-fall inom tolerans (`-1,23` till `+0,77 dB`).
Vid vind ($u = \pm 3\text{ m/s}$) träffar sneda strålar i 7 fall `delmodell6.py` där $\Delta\theta$ för
$T_1\text{–}R$ / $S\text{–}T_2$ (när en skärmtopp ligger under linjen från den andra) saknas i AV 1106/07
ekv. (226), och `N18K7` träffar `delmodell3.py` (Tolkning 7).

### 2.3 Årsmedel (`N18A1–A4`, 4 fall)
Fortsatt **4 ej körbara**: kodstrukturen i `src/buller/nord2000/arsmedel.py` är klar, men tabellen med
parametrar för de 9 aktiva väderklasserna (`M3, M7, M8, M13, M18, M19, M20, M23, M24`) i
`VTT-R-02530-06` saknas i lokala källor.

---

## 3. Genomgång av de 11 rak-väg-fallen i "ska utredas"

Inga konstanter har anpassats mot facit. Nedan sammanfattas avvikelser och metodgrundade hypoteser:

1. **`N18R083` (tjock hård skärm, motvind $u = -3\text{ m/s}$):** total `0,51 dB`, max band `0,72 dB`.
   Samtliga 27 tersband ligger redan under `1,0 dB`; totalnivån är `0,01 dB` över gränsen `0,50 dB`.
   Hypotes: marginell effekt av skuggzons- eller turbulensvägning vid brant motvind över tjock skärm.
2. **`N18R031, N18R032, N18R041, N18R042` (upphöjd väg $h_r = 1{,}5\text{ m}$, $u = 0$ och $+3$, råhet N/M):**
   total `0,01–0,26 dB` (redan godkänd), men höga tersband ($3{,}15\text{–}10\text{ kHz}$) avviker `1,83–3,14 dB`.
   Hypotes: för däckkällan $h_s = 0{,}01\text{ m}$ med $r_{\text{scr1}} \approx 0{,}96$ ger vägd reflektion $Q_{\text{avS}}$
   (TC-101327 bilaga G.2) stark spegling i vägbanan direkt före släntkrönet ($x = 5\text{–}6\text{ m}$).
3. **`N18R101, N18R104` (icke-plan dal, $u = 0$, $h_r = 1{,}5$ och $4{,}0\text{ m}$):**
   ren delmodell 3, total `0,44 / 0,58 dB`, max band `1,25 / 0,90 dB` vid $2\text{–}4\text{ kHz}$.
   Kontrollkörning av alternativet `overgangshojd="bis"` (AV 1849/00 4.4.2) ger kilfel och bekräftar
   förvalet `"prim"`. Hypotes: Fresnelzonsviktens normering i dalbotten (AV 1106/07 ekv. 155–156).
4. **`N18R111–114` (icke-plan kulle, alla 4 fall):**
   total `0,30–0,70 dB`, max band `3,75–5,14 dB` vid $200\text{–}315\text{ Hz}$ (även vid $u = 0$ i `N18R111`
   och `N18R114`). Hård tjock skärm (`N18R081`) ligger inom `0,08 / 0,15 dB`.
   Hypotes: delmodell 5 för mjuk, flack kulle ($\sigma = 200$, $\beta \approx 185^\circ$) överskattar
   konstruktiv interferens längs den mjuka kullryggen i $200\text{–}315\text{ Hz}$ (AV 1106/07 avsnitt 5.7.4 / 5.14).

---

## 4. Verifiering
- `pytest tests/buller/nord2000/ tests/buller/testfall/test_kurvig_vag.py tests/buller/testfall/test_stadsgata.py -q`: **753 passed** (22,74 s).
- `ruff check src/buller tests/buller`: **0 fel**.
- `mypy src/buller`: **0 fel** (83 källfiler).
