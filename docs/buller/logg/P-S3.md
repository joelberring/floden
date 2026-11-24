# P-S3 – Delmodell 7 (C_SR), konvex primärkant T₁ och körtidsoptimering

Datum: 2026-11-23. Gren: `nord2000-csr-och-snabbhet`.

**Läget i korthet.** Två metodavvikelser med stöd i källtexterna rättades i kärnan och fyra
beräkningsmässigt exakta körtidsoptimeringar infördes. Grupp N (80 RL 18-18-fall):
**39 godkända** (förut 33), **11 ska utredas** (förut 16), **3 underkända** (förut 4),
**27 ej körbara** (oförändrat). Två hela skärmgrupper är nu 4/4 godkända: **tunn skärm
(N18R071–074)** och **två skärmar (N18R091–094)**, samt båda höga mottagarfallen på upphöjd
väg (**N18R034, N18R044**). Samtliga 33 tidigare godkända fall förblir godkända (ingen
försämring i `FORE_P_S2_REST_DB`). `nord2000_konform` förblir `False`.

Körtiden för hela grupp N (80 fall) sjönk från **390–640 s** (P-S2) till **121,6 s**
(**3,2–5,3× snabbare**; kulle-fallet `N18R111` sjönk från **54,9 s till 6,4 s**, **8,6×**).

Underlag: AV 1849/00 avsnitt 4.9 (s. 89), AV 1106/07 rev. 2014 avsnitt 5.7.1, 5.16 (s. 116–120),
5.21.1 (s. 132–135) och 5.23.2 (ekv. 336), samt TC-101327 (2019) bilaga G.

## 1. Ändringar i fysikkärnan

### 1.1 `delmodell7.markkorrektion_csr`: C_SR = max(p_G, 1)

I AV 1849/00 avsnitt 4.9 (s. 89) definieras markkorrektionen i turbulensspridningen som
`CSCR is approximated by the ground effect part of the single screen effect pG = p/p1`
(och går in i `10·lg(C_S C_R)`). Löptexten i AV 1106/07 s. 116 och s. 119 upprepar samma
formulering (`CSR ... is the ground effect part of Eq. (188) limited downwards to a value of 1`),
medan ekv. (272) på s. 117 trycker `C_SR = p_G²`.

Med `C_SR = max(p_G, 1)` förbättras samtliga skärmfall där ΔL₇ bidrar märkbart:

| Fall | Före (P-S2): utfall (tot / band, dB) | Efter (P-S3): utfall (tot / band, dB) |
| --- | --- | --- |
| N18R071 | godkänd (0,27 / 0,74) | godkänd (0,14 / 0,23) |
| N18R073 | ska utredas (0,28 / 1,05) | **godkänd (0,18 / 0,68)** |
| N18R081 | godkänd (0,18 / 0,75) | godkänd (0,08 / 0,15) |
| N18R083 | ska utredas (0,39 / 1,22) | ska utredas (0,51 / 0,72) |
| N18R091 | ska utredas (0,76 / 2,03) | **godkänd (0,01 / 0,15)** |
| N18R092 | ska utredas (0,42 / 1,27) | **godkänd (0,15 / 0,74)** |
| N18R093 | underkänd (1,11 / 2,23) | **godkänd (0,20 / 0,66)** |

Tolkningen är märkt "avgjord av testfall" i `docs/buller/tolkningar.md` och bör tas med vid
extern granskning (Kunskapscentrum om buller).

### 1.2 `terrang.primar_skarmkant`: endast konvexa inre punkter som T₁

I AV 1106/07 avsnitt 5.21.1 väljs T₁ bland inre punkter med `h_bas > 0`, och skärmformen
avgränsas sedan av närmaste icke-konvexa punkter `W₁, W₂` (ekv. 336, `Convex(i)`). Kildiffraktion
enligt avsnitt 5.7.1 förutsätter `β > π`. På upphöjd väg med mottagarhöjd 4 m (`N18R034`,
`N18R044`) har profilen en helt plan impedansgräns mellan asfalt och gräs (`x = 5 m, z = 1,5 m`,
`β = π`, `Convex = falskt`) som tidigare valdes som `T₁` i stället för släntkrönet (`x = 6 m`).
Med kravet `Convex(i)` i `primar_skarmkant`:

| Fall | Före (P-S2): utfall (tot / band, dB) | Efter (P-S3): utfall (tot / band, dB) |
| --- | --- | --- |
| N18R034 | ska utredas (0,23 / 1,31) | **godkänd (0,16 / 0,84)** |
| N18R044 | ska utredas (0,21 / 1,31) | **godkänd (0,16 / 0,85)** |

## 2. Exakta körtidsoptimeringar (0,000 dB avvikelse)

Profilering av `N18R111` (54,9 s) visade tre huvudflaskhalsar: 2,7 miljoner anrop till
`kildiffraktion._a_ganger_e` (polynomloopar), 204 000 anrop till `delmodell5.tryck` (samma
`p₁, p₂, p₃` omräknades för varje segmentpar) och 231 000 anrop till `stralar_krokt_plan_mark`
(samma `(ξ, c₀)`-par omräknades för alla 27 tersband).

1. `kildiffraktion.py`: avvecklat Horners schema för `fresnel_f` och `fresnel_g` (exakt samma
   koefficienter, tabell 4–5).
2. `delmodell4.py`: lokal `(ξ, c₀)`-cache i `marksidor_refraktion` samt lokal `tryck`-cache per
   frekvens i `skarm_en_kant`.
3. `delmodell5.py`: lokal `tryck`-cache per frekvens i `skarm_tva_kanter`.
4. `delmodell6.py`: lokal `tryck`-cache per frekvens i `tva_skarmar`.

Uppmätt körtid:
- `N18R091`: 20,9 s → 7,5 s (2,8×)
- `N18R111`: 54,9 s → 6,4 s (8,6×)
- Hela grupp N (80 fall): 390–640 s → 121,6 s (3,2–5,3×)

## 3. Kvarvarande 14 avvikande + 27 ej körbara fall

- **Upphöjd väg, låg mottagare (`N18R031, 032, 041, 042`, 4 fall):** totalnivån ligger redan
  inom `≤ 0,26 dB`; avvikelsen ligger i höga tersband (3,15–10 kHz, 1,83–3,14 dB) där
  källan `h = 0,01 m` har `r_scr1 ≈ 0,96` och `Q_avS` (TC-101327 G.2) ger stark spegling i
  vägbanan före kanten.
- **Kulle (`N18R111–114`, 4 fall):** totalnivå `0,30–0,70 dB`, men tersband `+3,75` till
  `+5,14 dB` i 200–315 Hz (även utan vind i `N18R111` och `N18R114`). Hela felet ligger i
  delmodell 5 för mjuk, flack kulle (`β ≈ 185°`); hård tjock skärm (`N18R081`) ligger inom
  0,08/0,15 dB.
- **Dal utan vind (`N18R101, 104`, 2 fall):** ren delmodell 3, `−1,25` resp. `−0,90 dB` i 2–4 kHz.
- **Tjock skärm i motvind (`N18R083`, 1 fall):** `tot = 0,51 dB, band = 0,72 dB` (0,01 dB över
  totalgränsen; alla band redan under 1,0 dB).
- **Medvind över dal (`N18R052, 062, 102`, 3 underkända)** och **motvind över bruten terräng
  (`N18R033, 043, 053, 063, 103`, 5 ej körbara):** kvarstår som öppna metodfrågor till
  Kunskapscentrum om buller (oförändrade från P-S2).
- **Årsmedel (`N18A1–4`), kurvig väg (`N18K*`), stadsgata (`N18S1–4`), skog (`N18R121–124`):**
  22 ej körbara (format/körare/KB1).
