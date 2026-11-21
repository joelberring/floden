# B4 – uppdelning av Nord2000:s spridningsmodell

Datum: 2026-11-21. Arbetsorder för agenter som fortsätter på referenskärnan i `src/buller/nord2000/`.
Läget just nu står i `docs/buller/logg/B4.md`; val och tryckfel i `docs/buller/tolkningar.md`.

Sidhänvisningar gäller `metod/AV-1106-07.pdf` (177 s.). "TC" = `metod/TC-101327.pdf`.

## 1. Normativt underlag

| Dokument | Roll |
| --- | --- |
| DELTA AV 1106/07, rev. 13 jan 2014 | Normativ metodtext. Allt implementeras härifrån, med avsnitt och ekvationsnummer i docstring. |
| FORCE TC-101327, 9 okt 2019 | Ändringar från 2018 års revision. **Gäller före AV 1106/07** där de skiljer sig. |
| DELTA AV 1849/00 och AV 1851/00, rev. 2006 | Bakgrund. Används bara för att avgöra tryckfel och för det AV 1106/07 saknar (väder → A, B: AV 1851/00 ekv. 26–27). |
| RL 18/18 (okt 2019) med Excel-filer `*_20181101.xls` | Testfall "Version December 2018". |
| KCB 2024 (rättelser) | Två fel i testfallen; båda rör källmodellen (axelbredd, maxnivå), inte spridningen. |

RL 18/18 hänvisar för spridningen till AV 1106/07 rev. 2014 ("including the latest changes") och nämner
"the 2018-revision of Nord2000" utan att citera TC-101327. TC-101327 är den enda beskrivningen av den revisionen
och gavs ut samma månad som RL 18/18. Slutsats: **testfallen från 2018 ska räknas med AV 1106/07 + TC-101327.**

Ändringarna i TC-101327 och var de slår:

| TC | Rör | Innehåll | Påverkar |
| --- | --- | --- | --- |
| 2.1 | 5.5.2 | ξ begränsas nedåt till −0,2 | refraktion |
| 2.2 | 5.5.4 | ny ekv. (43) för d_SZ | refraktion, skuggzon |
| 2.3, 2.8–2.10, 2.16 | 5.7.1, 5.13–5.15, ny bilaga G | reflektionskoefficienterna på skärmens kilytor (Q₂–Q₄) blir vägda medelvärden av en reell ekvivalent koefficient Q_e, nedre gräns −0,369; gamla definitionen gäller bara delmodell 3 | **alla skärmfall** |
| 2.4–2.5 | 5.7.3–5.7.4 | j2ρfτ → jωτ i ekv. (97)–(98), (102)–(103) | två kanter, två skärmar |
| 2.6 | 5.10 | 10·lg saknas i ekv. (120)–(121) | delmodell 1 (**infört**) |
| 2.7 | 5.12 | h″_SCR definieras av ekv. (22) | delmodell 3 |
| 2.11 | 5.21.2 | Δℓ → Δℓ₀ i tredje stycket | sekundär skärm |
| 2.13 | 5.23.6 | arccos-argument begränsas till −1 i ekv. (347) | FresnelZoneW i skuggzon |
| 2.15 | bilaga B | ny förenkling av terräng (informativ) | förbehandling, B3 |

## 2. Delmodeller

Status: **klar** = implementerad och prövad för homogen atmosfär; **del** = delvis; **–** = ej påbörjad.

| Del | Avsnitt (sida) | Ekv. | Status | Modul |
| --- | --- | --- | --- | --- |
| Indata: terrängprofil, väder (A, B, C, s_A, s_B, C_v², C_T²), spridningszon, reflexbana | 5.3 (14–18) | 1–3 | del (väder endast A = B = 0) | `atmosfar.py`, `terrang.py` |
| Källa, mottagare, baslinje, d_bas(i), h_bas(i) | 5.4.1 (18–20) | 4–9 | klar | `terrang.py` |
| A₊, B₊ | 5.4.2 | 10 | – | |
| Tersband, λ₀, k₀ | 5.4.3–5.4.4 | 11–14 | klar | `frekvenser.py` |
| Ekvivalent linjär ljudhastighetsprofil (CalcEqSSP), bilaga F | 5.5.2 (23–25), s. 177, TC 2.1 | 15–22 | – | |
| Frekvensberoende profil för mjuk mark (CalcEqSSPGround) | 5.5.3 (25–26) | 23–28 | – | |
| Direkt stråle, skuggzonsavstånd (DirectRay) | 5.5.4 (26–29), TC 2.2 | 29–44 | del (endast ξ = 0) | `geometri.py` |
| Reflekterad stråle (ReflectedRay), kubisk ekvation | 5.5.5 (29–31) | 45–50 | del (endast ξ = 0) | `geometri.py` |
| Gångtidsskillnad (TravelTimeDiff) | 5.5.6 | 51–53 | del (endast ξ = 0) | `geometri.py` |
| Strålens höjd, RayCurvature, HeightOfCircularRay | 5.5.7, 5.23.9 | 54–56, 355–368 | – | |
| Impedansklasser A–H, råhetsklasser | 5.6.1 | tab. 2–3 | – (hör till B3) | |
| Markimpedans (Delany–Bazley) | 5.6.2 | 57 | klar | `mark.py` |
| Sfärisk reflektionskoefficient, w(ρ) | 5.6.3 | 58–74 | klar | `mark.py` |
| Inkoherent och planvågsreflektion | 5.6.4–5.6.5 | 75–77 | klar | `mark.py` |
| Kildiffraktion med impedans (pwedge, Dwedge) | 5.7.1–5.7.2 (38–44) | 78–94 | – | |
| Två kilar | 5.7.3 (44–46), TC 2.4 | 95–100 | – | |
| Tjock skärm (två kanter) | 5.7.4 (46–48), TC 2.5 | 101–104 | – | |
| Icke-reflekterande kil (pwedge0) | 5.7.5 (48–49) | 105–107 | – | |
| Fresnelzoner, vikter, Fresnelzonsinterpolation | 5.8 (49–51), 5.23.4–5.23.7 | 108–109, 338–353 | klar för ξ = 0 (utan ekv. 346–348) | `fresnel.py` |
| Koherens F_f, F_Δτ, F_c, F_r | 5.9 (51–53) | 110–114 | klar som funktioner; F_Δτ används bara med Δτ₊ = Δτ | `koherens.py` |
| Delmodell 1: plan mark, en marktyp | 5.10 (53–55), TC 2.6 | 115–123 | klar för ξ = 0; skuggzon (121–122) – | `delmodell1.py` |
| Delmodell 2: plan mark, flera marktyper | 5.11 (55–58), 5.23.14 | 124–133, 378–381 | klar för ξ = 0 | `delmodell2.py`, `fasdiff.py` |
| Delmodell 3: icke-plan terräng utan skärm (konkava, konvexa, övergångssegment) | 5.12 (59–67), 5.23.12, 5.23.15, 5.23.19 | 134–156 | – | |
| Delmodell 4: en skärm, en kant (bas + allmän) | 5.13 (67–80), TC bilaga G.1–G.2 | 157–ca 188, TC 404– | – | |
| Delmodell 5: en skärm, två kanter | 5.14 (80–94), TC G.3 | ca 189–221 | – | |
| Delmodell 6: två skärmar | 5.15 (94–115), TC G.4 | ca 222–270 | – | |
| Delmodell 7: turbulensspridning bakom skärm | 5.16 (115–120) | 271–277 | – | |
| Delmodell 8: flera markreflexer | 5.17 (120–123) | 278–285 | – (bidrar inte när A = 0: N < 4) | |
| Delmodell 9: luftabsorption | 5.18 (123–124) | 286–287 | klar | `luftabsorption.py` |
| Delmodell 10: spridningszoner (skog), F_s, tabell 9 | 5.19 (124–129) | 288–295 | – | |
| Delmodell 11: reflexer, ändliga reflektorer | 5.20 (129–131), 5.3.4 | 296–299 | – | |
| Terrängtolkning: primär skärmkant, r_scr1 | 5.21.1 (132–135) | 300–306 | klar för ξ = 0 (Δℓ = Δℓ₀); skärmform W₁–W₂ – | `terrang.py` |
| Sekundär skärm, r_scr2; sekundär kant, r_scr12 | 5.21.2–5.21.3 (135–139), TC 2.11 | 307–315 | – | |
| Planhet: r_f, r_flat | 5.21.4 (139–140) | 316–319 | r_f klar; r_flat (318) kräver delmodell 3 | `terrang.py` |
| Ekvivalent plan mark, Δh | 5.21.5 (140–142) | 320–327 | klar | `terrang.py` |
| Ändliga skärmar, sidodiffraktion | 5.21.6 (142–143), bilaga E (170–176) | 328 | – | |
| Sammansatt modell | 5.22 (143–144) | 329–334 | del: ΔL_d, ΔL_a, ΔL_flat | `karna.py` |
| Hjälpfunktioner | 5.23 (144–163) | 335–392 | Coft, Exp', CalcFZd, FresnelZoneSize/W/Wm, H, Length, NormLine, PhaseDiffFreq klara; Convex, HeightOfCircularRay, ImagePoint, MinConcaveHeight, SegmentVariables, ShadowZoneShielding, Sign, VertDist, WedgeCross – | |

## 3. Beroendeordning

```
frekvenser ─┬─ mark ──────────────┬─ delmodell1 ─ delmodell2 ─┐
            ├─ koherens ──────────┤                            ├─ "Flat" ─┐
            ├─ geometri (strålar) ┤                            │          │
            └─ fresnel ───────────┴────────────────────────────┘          │
                                                                          ├─ "NoScreen" ─┐
kildiffraktion (5.7) ─┬─ delmodell3 (konvexa segment) ── r_flat ──────────┘              │
                      ├─ delmodell4 ─ delmodell5 ─ delmodell6 ── "Screen" ───────────────┼─ ΔL_t
                      └─ ShadowZoneShielding (skuggzon i delmodell 1)                    │
terrang: T₁, W₁–W₂, T₂, T₁₂, r_scr1, r_scr2, r_scr12 ────────────────────────────────────┤
delmodell7 (kräver T₁) ─ ⊕ till skärmmodellerna                                          │
delmodell8 (kräver refraktion) ─ ⊕ till ΔL_t ────────────────────────────────────────────┘
refraktion (5.5): CalcEqSSP → CalcEqSSPGround → DirectRay → ReflectedRay → TravelTimeDiff
   └─ ersätter räta strålar i geometri, fresnel (ekv. 346–348), terrang (ekv. 301), alla delmodeller
delmodell9, delmodell10, delmodell11: fristående termer i ekv. (329); F_s ur delmodell 10 går in i koherensen
```

Sammansättningen (ekv. 332):
`ΔL_t = (r_scr1·ΔL_scr + (1 − r_scr1)·ΔL_noscr) ⊕ ΔL_8`,
`ΔL_scr = r_scr2·(ΔL_6 ⊕ ΔL_7,2) + (1 − r_scr2)·ΔL_scr1`,
`ΔL_scr1 = r_scr12·(ΔL_5 ⊕ ΔL_7,2) + (1 − r_scr12)·(ΔL_4 ⊕ ΔL_7,1)`,
`ΔL_noscr = r_flat·ΔL_flat + (1 − r_flat)·ΔL_3`.

## 4. Gränssnitt

### Finns (homogen atmosfär)

```python
mittfrekvenser_hz() -> Flyttal                                             # ekv. 11
ljudhastighet_m_s(temperatur_c) -> float                                   # Coft, ekv. 335
markimpedans(frekvens_hz, flodesresistivitet_kNsm4) -> complex             # ekv. 57
sfarisk_reflektion(frekvens_hz, gangtid_reflekterad_s, strykvinkel_rad, impedans) -> complex   # ekv. 58–60
inkoherent_reflektion(impedans) -> float                                   # ekv. 75–76
koherens_bandmedelvarde / _fluktuerande_refraktion / _turbulens / _markrahet   # ekv. 111–114
calc_fzd(r_s_m, r_r_m, theta_rad, f_lambda_m) -> float                     # ekv. 339
fresnelzonsvikt(d, h_s, h_r, d1, d2, f_lambda_m) -> float                  # FresnelZoneW, ξ = 0
fresnelzonsvikt_modifierad(...) -> float                                   # FresnelZoneWm, ξ = 0
fasdifferensfrekvens_hz(frekvenser, d, h_s, h_r, impedanser, c0, fasskillnad) -> float   # PhaseDiffFreq
stralar_plan_mark(d, h_s, h_r, c) -> StralarPlanMark                       # R₁, R₂, τ₁, τ₂, ψ_G, d_refl
markeffekt_plan_mark_db(frekvenser, d, h_s, h_r, sigma, rahet, atmosfar) -> Flyttal      # SubModel1
markeffekt_blandad_mark_db(frekvenser, d, h_s, h_r, segment, atmosfar) -> Flyttal        # delmodell 2
terranggeometri(profil) -> Terranggeometri                                 # ekv. 4–9
ekvivalent_plan_mark(x, z) -> EkvivalentPlanMark                           # ekv. 320–326
primar_skarmkant(geo) -> Skarmkant | None; skarmparameter_rscr1(f, kant, d_bas, c) -> float
luftabsorption_db(frekvenser, vagstracka_m, t_c, rf, tryck) -> Flyttal     # delmodell 9
Nord2000Karna().berakna(profil, vader) -> Nord2000Delresultat              # ekv. 329
```

### Föreslås

```python
# refraktion.py – allt i 5.5; ersätter HomogenAtmosfar och stralar_plan_mark
@dataclass(frozen=True)
class Atmosfar: a_m_s, b_per_s, c_m_s, s_a, s_b, z0_m, cv2, ct2, t_luft_c, rf_procent, tryck_kpa
def atmosfar_ur_vader(vader: Vader) -> Atmosfar                 # AV 1851/00 ekv. 26–27; s_A, s_B: källa saknas, se §7
def calc_eq_ssp(h_s, h_r, atm) -> tuple[xi, c0, c_medel]        # ekv. 15–21, bilaga F, TC 2.1
def calc_eq_ssp_mark(h_s, h_r, impedanser, atm) -> ...          # ekv. 23–28 (per frekvens)
def direkt_strale(d, h_s, h_r, xi, c0) -> DirektStrale          # τ, R, Δθ, d_SZ; ekv. 29–44, TC 2.2
def reflekterad_strale(d, h_s, h_r, xi, c0) -> ReflekteradStrale  # τ, R, R_S, R_R, Δθ_S, Δθ_R, ψ_G, d_refl
def gangtidsskillnad_s(tau2, tau1, ...) -> float                # ekv. 51–53

# diffraktion.py – 5.7, rena funktioner av frekvens och geometri
def pwedge(f, tau, ell, theta_s, theta_r, beta, r_s, r_r, z_s, z_r) -> complex          # ekv. 78–92
def dwedge(...) -> complex                                                              # ekv. 93–94
def pwedge2(...), def ptjock(...), def pwedge0(...), def dwedge0(...)                   # 5.7.3–5.7.5

# terrang.py – resten av 5.21
def konvex(geo, i) -> bool                                       # ekv. 336
def skarmform(geo, kant) -> tuple[int, int]                      # W₁, W₂
def sekundar_skarmkant(geo, primar) -> Skarmkant | None; def skarmparameter_rscr2(...)
def sekundar_kant(geo, primar) -> Skarmkant | None; def skarmparameter_rscr12(...)

# delmodell3.py … delmodell8.py, delmodell10.py, delmodell11.py: en funktion per delmodell som tar
# (frekvenser, Terranggeometri, [skärmbeskrivning], Atmosfar) och returnerar ΔL per band (Flyttal).
```

Regler för alla: skalärt och läsbart, float64, en funktion per ekvationsgrupp, `BullerFel` utanför täckningen,
varje formel avläst från sidbild (textutdraget förvanskar ekvationerna).

## 5. Testfall och delmodeller

Blad i `teststraightroad_20181101.xls` (profilen börjar i källan, x = 3,25 m; h_r och väder står på bladet;
RH = 70 %). Alla fall utom 1–10, 17 och 18 har s_u = 0,5 eller 1 m/s och turbulens, och kräver därför
refraktionsräkning (F_Δτ) även när u = 0.

| Blad | Grupp (RL 18/18 tab. 1) | Prövar | Kräver utöver det som finns |
| --- | --- | --- | --- |
| 1–7 | 1: plan mark, impedans A–G | delmodell 2 (väg + mark), 9 | källmodell B5 |
| 8 | 1: h_r = 4 m | som ovan | B5 |
| 9–10 | 1: dt/dz = ±0,1 | refraktion (B), skuggzon vid −0,1 | refraktion |
| 11–16 | 1: delvis inkoherent, u = 0/3/5, φ = 0–180°, d = 100/1000 m | F_Δτ, F_c, medvind, motvind (skuggzon, ekv. 121–122), 16: ev. delmodell 8 | refraktion, ShadowZoneShielding (→ pwedge) |
| 17–18 | 1: fordonskategori 2, 3 | källhöjd 0,75 m | B5 |
| 21–24 | 2: blandad plan mark | delmodell 2 med fyra segment | refraktion (s_u) |
| 31–34 | 3: upphöjd väg, råhet N | terrängtolkning, delmodell 3/4 (vägkant) | diffraktion, delmodell 3–4, refraktion |
| 41–44 | 4: upphöjd väg, råhet M | F_r | som ovan |
| 51–54 | 5: dal | delmodell 3 (konkava segment), r_flat | delmodell 3, refraktion |
| 61–64 | 6: dal med sjö | delmodell 3 med olika impedans | som ovan |
| 71–74 | 7: tunn hård skärm | delmodell 4, 7, TC bilaga G | diffraktion, delmodell 4, 7 |
| 81–84 | 8: tjock hård skärm | delmodell 5, r_scr12 | + delmodell 5 |
| 91–94 | 9: två skärmar | delmodell 6, r_scr2 | + delmodell 6 |
| 101–104 | 10: icke-plan, dalformad | delmodell 3, övergångssegment | delmodell 3 |
| 111–114 | 11: icke-plan, kulle | delmodell 3/4, konvexa segment | delmodell 3–4 |
| 121–124 | 12: skog | delmodell 10, F_s (parametrar i RL 18/18 tab. 2) | delmodell 10 |

Inom varje grupp 2–12 är fallen: u = 0 (h_r = 1,5), medvind 3 m/s, motvind 3 m/s, u = 0 (h_r = 4).

Övriga filer: `testcurvedroad` (terräng med nivåkurvor, tunn och tjock skärm, vall; prövar helheten med
förbehandling, tolerans 2 dB), `testcitystreet` (reflexer i fasader: delmodell 11 och banfinnaren B6),
`testyearlyaverage` (väderklasser; samma geometri som fall 4).

Godkänt enligt RL 18/18 avsnitt 6: A-vägd totalnivå inom 1 dB (oftast inom 0,5); tersband inom 1 dB godtas direkt,
större avvikelser ska granskas, och en förskjutning av markdippen med ett tersband godtas.

## 6. Kolumnen dL

dL är enligt RL 18/18 avsnitt 2 "the difference between L_E and the free-field value of L_E (disregarding ground and
screening effect as well as air absorption)" och ges "for information, not required in the test process". Det är alltså
`10·lg(Σ_k w_k·10^((ΔL_t,k + ΔL_a,k)/10) / Σ_k w_k)` över 179 källpunkter gånger delkällor, där vikterna w_k
innehåller delkällornas effektfördelning per band, direktivitet och källpunktens andel av förbifarten.

**dL kan inte användas som facit för en ren punkt-till-punkt-jämförelse.** Ingen uppgift i testfallen gäller en
enskild källpunkt. Slutlig prövning kräver källmodellen (B5) och linjekällans integration.

Vad som ändå gick att göra står i `docs/buller/avvikelser.md` (2026-11-21): fall 1–8 räknade med en källhöjd i
taget. Med lika vinkelvikt hamnar testvärdet upp till 1,7 dB utanför det intervall som de två källhöjderna spänner
upp; med vikten cos θ som mest 0,43 dB utanför, och för hård mark (fall 7) inom 0,04 dB. Orsaken till cos θ är inte
utredd och hör till B5/B1.

## 7. Återstår – paket för parallella agenter

Storlek anges som antal sidor metodtext. Alla paket skriver i `src/buller/nord2000/` med egna filer; bara
`karna.py` och `terrang.py` delas (samordnas av den som har P-S).

| Paket | Innehåll | Sidor | Beror på | Kan prövas med |
| --- | --- | --- | --- | --- |
| **P-R1** Refraktion, strålar | `Atmosfar`, väder → A, B (AV 1851/00 ekv. 26–27), CalcEqSSP + bilaga F, DirectRay, ReflectedRay (kubisk ekvation), TravelTimeDiff, TC 2.1–2.2 | 12 | – | gränsfall ξ → 0 mot `stralar_plan_mark`; symmetrier; d_SZ |
| **P-R2** Refraktion in i plan mark | CalcEqSSPGround, FresnelZoneW ekv. 346–348 + TC 2.13, F_Δτ med A₊/B₊, delmodell 1–2 med krökta strålar, skuggzonsgrenen (ekv. 121–122) | 8 | P-R1, P-K1 (ShadowZoneShielding) | fall 9–16, 21–24 (med B5) |
| **P-K1** Kildiffraktion | pwedge, Dwedge, pwedge0, Dwedge0 (5.7.1–5.7.2, 5.7.5) | 9 | – | hård halvplansskärm mot känd lösning; kontinuitet över skugggränsen; reciprocitet |
| **P-K2** Dubbel diffraktion | två kilar, tjock skärm (5.7.3–5.7.4, TC 2.4–2.5) | 5 | P-K1 | gräns: andra kanten försumbar → P-K1 |
| **P-T** Terrängtolkning | Convex, VertDist, skärmform, sekundär skärm och kant, r_scr2, r_scr12 (5.21.1–5.21.3, TC 2.11) | 7 | – (ekv. 301 efter P-4) | profilerna i fall 31–114: vilka kanter väljs |
| **P-3** Delmodell 3 | segmentklassning, MinConcaveHeight, SegmentVariables, WedgeCross, ekv. 134–156, r_flat (ekv. 318) | 9 | P-K1 | plan mark ska ge delmodell 1/2; fall 51–64, 101–104 |
| **P-4** Delmodell 4 | bas- och allmän modell, TC bilaga G.1–G.2 och G.5, ImagePoint | 14 + 7 | P-K1, P-T | fall 71–74 |
| **P-5** Delmodell 5 | en skärm, två kanter, TC G.3 | 14 | P-K2, P-4 | fall 81–84 |
| **P-6** Delmodell 6 | två skärmar, TC G.4 | 21 | P-K2, P-4 | fall 91–94 |
| **P-7** Delmodell 7 | turbulensspridning bakom skärm | 5 | P-T (kant) | gräns: C_v² = C_T² = 0 → inget bidrag |
| **P-8** Delmodell 8 | flera markreflexer, RayCurvature | 3 | P-R1 | fall 16; N < 4 → inget bidrag |
| **P-10** Delmodell 10 | spridningszoner, tabell 9 (till datafil med källa), F_s, begränsad höjd | 5 | P-R1 för strålhöjd (rät stråle räcker först) | fall 121–124 |
| **P-11** Delmodell 11 | reflexer, ändlig reflektor, ρ_E | 3 | – (samordnas med B6) | testcitystreet |
| **P-E** Ändliga skärmar | 5.21.6 och bilaga E (informativ) | 8 | P-4 | – |
| **P-S** Sammansättning och prövning | ekv. 332 i `karna.py`, körning av hela `teststraightroad` genom B5, avvikelse per tersband | 2 | alla | grupp N i testsviten |

Kan starta genast och parallellt: **P-R1, P-K1, P-T, P-7, P-10, P-11**. Därefter P-3, P-4, P-K2, P-R2; sist P-5,
P-6, P-8, P-E, P-S.

Öppna frågor som någon måste äga:

1. **s_A och s_B ur s_u och s_dt/dz.** AV 1106/07 tar s_A, s_B som indata; testfallen anger s_u och s_dt/dz.
   Omräkningen har inte hittats i de lästa avsnitten (AV 1851/00 ekv. 26–27 ger A och B). Sök i AV 1851/00 och
   User's Guide Nord2000 Road innan P-R2.
2. **C_T²-termen i ekv. (113)** (med eller utan kvadrat på temperaturen), se tolkningar. Avgörs av fall 11–16.
3. **cos θ-vikten i dL**, se avsnitt 6. Avgörs av B5.
4. **Sträckan R i luftabsorption och spridning för skärmade banor.** Avgörs i P-4.
