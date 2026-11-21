# P-R1 – Refraktion, strålar

Datum: 2026-11-21. Paket P-R1 i `docs/buller/B4-uppdelning.md` avsnitt 7.

Ägda filer: `src/buller/nord2000/refraktion.py`, `src/buller/nord2000/stralar_krokta.py`,
`tests/buller/nord2000/test_refraktion.py`, denna logg. Inga andra filer är ändrade (`STATUS.md` och `PLAN.md` ägs av
samordnaren; förslag till statuspost står sist).

Underlag: AV 1106/07 avsnitt 5.3.2, 5.4.2, 5.5.1–5.5.6 och bilaga F; TC-101327 avsnitt 2.1–2.2; AV 1851/00 avsnitt
2–3; AV 1849/00 avsnitt 4.5.3.2. Formlerna är avlästa från sidbilder (`/home/claude/verktyg/sidor/P-R1/`:
AV 1106/07 s. 16, 20, 24, 27–32, 177; TC s. 4; AV 1851/00 s. 18; AV 1849/00 s. 77).

**Läget i korthet.** Allt i paketet är implementerat och prövat mot oberoende geometri. En fråga är inte avgjord och
påverkar resultat: hur strålarna räknas vid uppåtbrytning (ξ < 0). Rapportens föreskrift (|ξ′|) är inte exakt och ger
andra gångtidsskillnader än den verkliga cirkelbågen; båda finns, förvalet är rapportens. Se tolkning 3.

## Implementerat

| Fil | Funktion | Innehåll | Källa |
| --- | --- | --- | --- |
| `refraktion.py` | `Atmosfar` | A, B, s_A, s_B, z₀, t₀, t_air, RH, tryck, C_v², C_T²; `c_m_s` = C | AV 1106/07 5.3.2, ekv. 2–3 |
| | `profilkoefficient_a_m_s`, `profilkoefficient_b_per_s` | A ur u(z_u), B ur dt/dz | AV 1851/00 ekv. 26–27 |
| | `atmosfar_ur_vader` | `Vader` → `Atmosfar`, inklusive s_A, s_B | som ovan + AV 1849/00 4.5.3.2 |
| | `ovre_atmosfar` | A₊, B₊ | AV 1106/07 ekv. 10 |
| | `ljudhastighetsprofil_m_s` | c(z) | ekv. 2 |
| | `medelljudhastighet_m_s` | c̄ | ekv. 19, bilaga F ekv. 401–403 |
| | `ljudhastighetsgradient_per_s` | Δc/Δz, h_min = 5·z₀, ±0,005 m vid lika höjder | ekv. 18 |
| | `ekvivalent_profil_ur_gradient` | c₀, ξ, gräns −0,2, gräns 10⁻⁶ | ekv. 17, 20, TC 2.1 |
| | `calc_eq_ssp` → `EkvivalentProfil` | CalcEqSSP: (ξ, c₀, c̄) | ekv. 15–21 |
| `stralar_krokta.py` | `cirkelbage` | bågen L → U: τ, R, ψ_L, Δθ | ekv. 32–41 |
| | `skuggzonsavstand_m` | d_SZ | ekv. 43 enligt TC 2.2 |
| | `direkt_strale` → `DirektStrale` | DirectRay: τ, R, Δθ, d_SZ, `i_skuggzon` | ekv. 29–44 |
| | `reflexpunkter_m`, `reflexpunkt_m` | kubiska ekvationen, val av lösning | ekv. 49 |
| | `reflekterad_strale` → `ReflekteradStrale` | ReflectedRay: τ, R, R_S, R_R, Δθ_S, Δθ_R, ψ_G, d_refl | ekv. 45–50 |
| | `storsta_gangtidsskillnad_s`, `gangtidsskillnad_s` | Δτ₀, TravelTimeDiff | ekv. 51–53 |
| | `stralar_krokt_plan_mark` | de tre i följd över plan mark | ekv. 116–117 |

Körberoende: endast standardbiblioteket (`math`). numpy används bara i testerna.

### Räkneform för cirkelbågen

Ekv. (34)–(40) är skillnader mellan nästan lika tal och odefinierade för ξ = 0. `cirkelbage` räknar samma båge så här
(härledning i docstring): med t = tan ψ_L (ekv. 32), ℓ = √(d² + Δz²), x = ℓ·ξ′·cos ψ_L/2:

- R = ℓ·arcsin(x)/x, Δθ = arcsin(x),
- τ = d·cos ψ_L/(c₀′·(1 − sin ψ_L·sin θ_U))·artanh(y)/y, sin θ_U = sin ψ_L − ξ′·d·cos ψ_L,
  y = ξ′·d·cos ψ_L/(1 − sin ψ_L·sin θ_U).

Uttrycken är algebraiskt lika med ekv. (34)–(41), täcker båda grenarna (d ≤ d_m och d > d_m) utan fallindelning, gäller
för båda tecken på ξ′ och ger rät stråle för ξ′ = 0. Uppmätt mot ekv. (34)–(41) bokstavligt: största relativa skillnad
2·10⁻¹⁴ i R och 4·10⁻¹⁴ i τ. Iakttagelse: räknas ekv. (39) rakt av i flyttal ger arcsin intill 1 ett relativt fel på
3,5·10⁻⁸ i R (d = 100 m, ξ = 10⁻³), vilket motsvarar ca 10⁻⁸ s i τ; avsnitt 5.5.6 begär högsta möjliga precision.

### Reflexpunkten

Ekv. (49) multipliceras med ξ och skrivs med u = x − d/2: 2ξu³ + (β_S + β_R − ξd²/2)u + d(β_R − β_S)/2 = 0,
β = z(2 + ξz). Formen gäller även för ξ = 0. Intervallet [0, d] delas vid derivatans nollställen och varje del löses
med intervallhalvering (ingen Cardano-formel). Alla lösningar i [0, d] returneras av `reflexpunkter_m`.

## Öppna frågan: s_A och s_B ur s_u och s_dt/dz

**Ingen uttrycklig formel för s_A eller s_B finns** i AV 1106/07, TC-101327, AV 1851/00 eller AV 1849/00 (sökt i
textutdragen efter standardavvikelse, fluktuation, σ, 1,7, s_A, s_B; även SP 2006:12, RL 18/18, Miljøprojekt 1335 och
KCB:s handledning). Det som finns:

- AV 1106/07 ekv. (10): A₊ = A + 1,7·s_A, B₊ = B + 1,7·s_B.
- AV 1849/00 avsnitt 4.5.3.2 (s. 77): de övre gångtiderna räknas för u₊ = u_m + 1,7·σ_u och
  dt/dz₊ = dt/dz_m + 1,7·σ_dt/dz.
- AV 1851/00 ekv. (26)–(27): A = u(z_u)/ln(z_u/z₀ + 1), B = dt/dz·10,025/√(t + 273,15).

Eftersom ekv. (26)–(27) är linjära i u och dt/dz ger de tre tillsammans entydigt

    s_A = σ_u / ln(z_u/z₀ + 1),        s_B = σ_dt/dz · 10,025/√(t + 273,15).

Detta är en härledning, inte ett citat. Den är införd i `atmosfar_ur_vader`. Kontroll av ekv. (26) mot en oberoende
uppgift: KCB:s användarhandledning avsnitt 2 anger A = 0,25 för 1,5 m/s på 10 m med z₀ = 0,025 m; ekv. (26) ger 0,2503.
"User's Guide Nord2000 Road" finns inte bland källorna och är inte läst.

## Prövat

`python -m pytest tests/buller/nord2000/test_refraktion.py -q` → **53 gröna** (0,15 s).
`python -m pytest tests/buller/nord2000 -q` → 332 gröna, 18 deltester, inga fel (inklusive andra agenters filer vid
körningen). `ruff check`, `ruff format --check` och `mypy` (strict) på de tre filerna: rent.

| Vad | Hur | Uppmätt |
| --- | --- | --- |
| A (ekv. 26) | räkneexemplet i KCB:s handledning; c(z_u) − c(0) = u | 0,2503 mot 0,25; 12 decimaler |
| B (ekv. 27) | numerisk derivata av Coft | 8 decimaler |
| s_A, s_B | `ovre_atmosfar` = väder med u + 1,7σ_u, dt/dz + 1,7σ | 13 decimaler |
| c̄ (bilaga F) | Gauss–Legendre-integration av ekv. (401), tre profiler | 8 decimaler (prövar också tolkning 2) |
| CalcEqSSP | A = 0 ger ξ = B/C, c₀ = C; profilen går genom c̄ på medelhöjden; symmetri i h_S, h_R; lika höjder ger dc/dz på höjden; h_min; z₀ < 1 mm; gräns 10⁻⁶; TC 2.1 | 9–14 decimaler |
| Cirkelbåge | ekv. (34)–(41) bokstavligt, 5 fall, båda grenar | rel. 2·10⁻¹⁴ (R), 4·10⁻¹⁴ (τ) |
| Cirkelbåge | oberoende cirkel (medelpunkt där c = 0) med numeriskt integrerad gångtid, båda tecken på ξ | rel. 1,3·10⁻¹⁵ |
| **Svag refraktion** | ξ = 0 mot `geometri.stralar_plan_mark`, 4 geometrier | R₁, R₂, τ₁, τ₂, ψ_G: rel. ≤ 10⁻¹⁵; d_refl: 10⁻¹⁴; Δτ: inom 4·τ·2⁻⁵² |
| **Svag refraktion** | ξ → 0, båda tecken, mot första ordningens störningsräkning (Fermat) | Δτ/Δτ_rät − 1 = ξ·k·(1 ± 2·\|ξ\|k), k ≈ d²/(2(h_S + h_R)) |
| **Symmetri** | ombytt källa och mottagare, samma profil (= omvänd vind), 6 geometrier × 2 lägen | direkt stråle identisk; Δτ rel. 10⁻⁹; d_refl ↔ d − d_refl; Δθ_S ↔ Δθ_R |
| **Reflektionslagen** | lika tangentvinkel i P för de två bågarna, ur oberoende cirklar, 6 fall | 10⁻¹⁰ rad |
| Reflexpunkten | Fermat: gångtiden via marken har minimum i P (oberoende av ekv. 49); ekv. (49) bokstavligt | residual 10⁻¹² |
| Tre reflexpunkter | ξ = 0,01, d = 400 m: tre lösningar, alla uppfyller reflektionslagen; valregeln | 10⁻¹¹ rad |
| Δθ_S, Δθ_R (ekv. 47–48) | lutningsskillnad i ändpunkten mot oberoende cirkel | 10⁻¹⁰ rad |
| **Skuggzonsavstånd** | källa och mottagare på cirkeln med radie 1/\|ξ\| som tangerar marken; den direkta strålens lägsta punkt är 0 på d = d_SZ, > 0 före, < 0 efter | rel. 10⁻¹³ (läget `exakt`) |
| Skuggzonsavstånd | läget `bokstavlig` (ξ′) mot `exakt` | kortare, relativt mindre än \|ξ\|·z_L |
| Skuggzonsgränsen | `exakt`: ψ_G → 0, P → tangeringspunkten, τ₂ − τ₁ → 0 | (τ₂ − τ₁)/τ < 10⁻¹³ på d = d_SZ·(1 − 10⁻⁹) |
| TC 2.2 | negativ parentes ger roten 0 | 12 decimaler |
| TravelTimeDiff | ekv. (52) bokstavligt; begränsning; 0 i skuggzon | rel. 10⁻¹⁰ |
| `BullerFel` | negativa höjder, c ≤ 0 på strålen, reflekterad stråle i skuggzon, okänt läge, h_S + h_R ≥ 10 m vid ξ = −0,2 | – |

### Hur nära de räta strålarna (d = 100 m, h_S = 0,5 m, h_R = 1,5 m, c = 340 m/s)

| ξ (1/m) | Δτ/Δτ_rät − 1 | R₁ − R₁,rät (m) | ψ_G − ψ_G,rät (rad) |
| --- | --- | --- | --- |
| 0 | 1,5·10⁻¹³ | 0 | 0 |
| 10⁻¹⁰ | 2,5·10⁻⁷ | < 10⁻¹³ | 3,1·10⁻⁹ |
| 10⁻⁸ | 2,5·10⁻⁵ | 4,2·10⁻¹² | 3,1·10⁻⁷ |
| 10⁻⁶ (metodens minsta) | 2,5·10⁻³ | 4,2·10⁻⁸ | 3,1·10⁻⁵ |
| 10⁻⁴ | 0,261 | 4,2·10⁻⁴ | 3,3·10⁻³ |
| −10⁻⁶, `exakt` / `bokstavlig` | −2,5·10⁻³ / −4,2·10⁻³ | 4,2·10⁻⁸ | −3,1·10⁻⁵ |
| −10⁻⁴, `exakt` / `bokstavlig` | −0,238 / −0,389 | 4,2·10⁻⁴ | −3,0·10⁻³ |

Avvikelsen är linjär i ξ med lutningen d²/(2(h_S + h_R)) = 2500 m, så även metodens minsta gradient ändrar Δτ med
0,25 % på 100 m. Rapportens regel "ξ = 10⁻¹⁰ för homogen atmosfär" ger alltså 2,5·10⁻⁷ relativt i Δτ; här ger ξ = 0
exakt de räta strålarna, i linje med den befintliga tolkningen för homogen atmosfär.

### Siffror för väder som i testfallen (z₀ = 0,05 m, 15 °C, h_S = 0,3 m)

| Väder | A / B | h_R, d | ξ (1/m) | d_SZ (m) | Δτ `bokstavlig` (µs) | Δτ `exakt` (µs) | Δτ rät (µs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| medvind 3 m/s | A = 0,566 | 1,5 m, 100 m | +2,06·10⁻³ | ∞ | 200,9 | 200,9 | 26,4 |
| motvind 3 m/s | A = −0,566 | 1,5 m, 100 m | −2,07·10⁻³ | 55,1 | skuggzon | skuggzon | 26,4 |
| motvind 3 m/s | A = −0,566 | 4 m, 100 m | −1,10·10⁻³ | 108,3 | 10,4 (rå 136,4; Δτ₀ slår till) | 2,2 | 70,5 |
| dt/dz = −0,1 K/m | B = −0,0591 | 1,5 m, 100 m | −1,74·10⁻⁴ | 190,3 | 12,5 | 14,7 | 26,4 |
| dt/dz = −0,1 K/m | B = −0,0591 | 4 m, 100 m | −1,74·10⁻⁴ | 273,5 | 56,7 | 56,5 | 70,5 |
| dt/dz = +0,1 K/m | B = +0,0591 | 1,5 m, 100 m | +1,74·10⁻⁴ | ∞ | 39,9 | 39,9 | 26,4 |
| övre värde för s_u = 0,5 m/s, u = 0 | A₊ = 0,160 | 1,5 m, 100 m | +5,84·10⁻⁴ | ∞ | 74,2 | 74,2 | 26,4 |

Följder för de andra paketen: motvind 3 m/s med h_R = 1,5 m ligger i skuggzon redan på 55 m, så motvindsfallen bland blad 11–16 hänger på
skuggzonsgrenen (P-R2). Med s_u = 0,5 m/s och u = 0 är Δτ₊ nästan tre gånger Δτ, så F_Δτ blir verksam i alla fall utom
1–10, 17 och 18.

## Tolkningar

| Datum | Paket | Fråga | Val och skäl |
| --- | --- | --- | --- |
| 2026-11-21 | P-R1 | 1. s_A och s_B: testfallen anger s_u och s_dt/dz, AV 1106/07 tar s_A och s_B som indata, och ingen formel finns. | s_A = σ_u/ln(z_u/z₀ + 1), s_B = σ_dt/dz·10,025/√(t + 273,15). Följer av AV 1849/00 avsnitt 4.5.3.2 (u₊ = u + 1,7σ_u, dt/dz₊ = dt/dz + 1,7σ), AV 1851/00 ekv. (26)–(27) och AV 1106/07 ekv. (10). Härledning, inte citat. Alternativ: inget känt. |
| 2026-11-21 | P-R1 | 2. Bilaga F ekv. (403): den linjära termen i c̄ är tryckt B/2·(h_R − h_S). | B/2·(h_R + h_S). Integralen i ekv. (402) ger summan; med differensen skulle en rent linjär profil (A = 0) inte ge c₀ = C i ekv. (20). Kontrollerat mot numerisk integration av ekv. (401). Alternativ: bokstavligt (fel med B·h_S). |
| 2026-11-21 | P-R1 | 3. **Uppåtbrytning (ξ < 0) i DirectRay.** Avsnitt 5.5.4 (och AV 1851/00 avsnitt 2.1) föreskriver ekv. (32)–(41) med \|ξ′\| och teckenbyte på Δθ (ekv. 42). Det speglar bågen men räknar gångtiden i en profil där ljudhastigheten ökar uppåt från den nedre punkten. Samma ekvationer med ξ′ med tecken ger den verkliga bågen exakt. | Båda finns (`uppat="bokstavlig"` eller `"exakt"`); **förval `bokstavlig`**, eftersom rapporten är normativ och säger det uttryckligen på två ställen. Skillnaden är av första ordningen i Δτ: refraktionens verkan på Δτ blir en faktor 1 + 2·h_L²/(h_S·h_R) större än den exakta (1,67 för 0,5 och 1,5 m), Δτ kan bli negativ (t.ex. −7,7 µs för 0,5/1,5 m, ξ = −10⁻⁴, d = 0,8·d_SZ) och för höga källor mycket för stor nära skuggzonen (1,6 ms mot gränsen 39 µs för h_S = 50 m). Det sista är just det fel avsnitt 5.5.6 beskriver och lagar med Δτ₀, vilket talar för att referensprogrammet räknar bokstavligt. Med `exakt` är 0 < τ₂ − τ₁ < Δτ₀ i alla prövade fall och gränsen i ekv. (52) slår aldrig till. **Osäkert; avgörs av fallet med dt/dz = −0,1 (blad 9–10) och motvindsfallen bland blad 11–16 när källmodellen finns.** |
| 2026-11-21 | P-R1 | 4. ψ_G vid ξ < 0. Avsnitt 5.5.5 säger att ψ_L från DirectRay är densamma för strålens två delar. Det stämmer för ekv. (32) med ξ med tecken (det är vad ekv. 49 uttrycker), inte med \|ξ\|. | ψ_G räknas ur ekv. (32) med ξ med tecken i båda lägena. I läget `bokstavlig` räknas bara R_S, R_R, τ_S, τ_R med \|ξ\|. Alternativ: ψ_L med \|ξ\| från källdelen eller mottagardelen (två olika värden, båda större än den räta strålens vinkel trots uppåtbrytning). |
| 2026-11-21 | P-R1 | 5. Ekv. (49) vid ξ < 0: texten har "upward refraction (ξ > 0)" och "only one real solution". | ξ < 0 avses (tryckfel). Ekvationen löses med ξ med tecken; den har då tre reella rötter men precis en mellan källa och mottagare, och den används. |
| 2026-11-21 | P-R1 | 6. Ekv. (49) med tre lösningar när h_S = h_R: rapporten anger val bara för h_S < h_R och h_S > h_R (AV 1851/00 nämner en ofrånkomlig diskontinuitet). | d/2, som alltid är en lösning vid lika höjder och den enda som är oförändrad när källa och mottagare byter plats. |
| 2026-11-21 | P-R1 | 7. d_SZ (TC 2.2) är skriven med \|ξ′\|. | `bokstavlig`: ξ′ = ξ/(1 + ξ·z_L), z_L = den lägre höjden (ekv. 30). `exakt`: ξ, som är geometrin för en cirkel med radien 1/\|ξ\| som tangerar marken. Skillnaden är relativt mindre än \|ξ\|·z_L (10⁻³ för ξ = −2·10⁻³, z_L = 0,5 m). |
| 2026-11-21 | P-R1 | 8. Avsnitt 5.5.4: ξ = 10⁻¹⁰ när \|ξ\| < 10⁻¹⁰, och Δz = 0,01 när Δz < 0,01. | Ingen av reglerna tillämpas: räkneformen har inte de numeriska problem de ska undvika, och ξ = 0 ger räta strålar (samma val som B4 gjorde för homogen atmosfär). Δz-regeln skulle vid lika höjder ändra τ₁ med −1,5·10⁻⁷ s på 100 m vid ξ = 10⁻⁴ (0,1–0,5 % av Δτ). Alternativ: bokstavligt; kan läggas hos anroparen. |
| 2026-11-21 | P-R1 | 9. Ekv. (44) anger c̄ som utdata från DirectRay utan definition i avsnitt 5.5.4. | `DirektStrale.medelljudhastighet_m_s` = R/τ. Alternativ: c̄ ur CalcEqSSP (`EkvivalentProfil.c_medel_m_s`), som också finns. Skillnaden saknar betydelse för F_c. |
| 2026-11-21 | P-R1 | 10. Lika höjder i CalcEqSSP: ekv. (18) ska räknas med h ∓ 0,005 m; ekv. (19) nämns inte. | Samma modifierade höjder i ekv. (19) (AV 1851/00: c̄ är då ljudhastigheten på höjden). Medelhöjden i ekv. (20) är oförändrad. Höjderna begränsas till 5·z₀ före jämförelsen. |
| 2026-11-21 | P-R1 | 11. z₀ < 0,001 m: avsnitt 5.3.2 rekommenderar 0,001 m. | Tillämpas i ekv. (2), (18)–(20) och i ekv. (26) (samma z₀ i profilen och i omräkningen från vind). `Atmosfar.z0_m` behåller det givna värdet. |
| 2026-11-21 | P-R1 | 12. TC 2.1: "c₀ is determined by Eq. (20) using Δc/Δz = −0,2·c₀". | c₀ = c̄/(1 − 0,1·(h_S + h_R)), ξ = −0,2. För h_S + h_R ≥ 10 m blir c₀ ≤ 0: `BullerFel`. Strålfunktionerna ger `BullerFel` när 1 + ξ·z ≤ 0 på strålens höjd (z ≥ 5 m vid ξ = −0,2). |
| 2026-11-21 | P-R1 | 13. Negativ Δτ (kan uppstå i läget `bokstavlig`, se 3). Avsnitt 5.9.2 skriver Δτ ≥ 0 men ekv. (51)–(53) har ingen nedre gräns. | Ingen nedre gräns läggs till. Anroparen får ett negativt värde och måste ta ställning. |
| 2026-11-21 | P-R1 | 14. C i ekv. (52). | `Atmosfar.c_m_s` = Coft(t₀), som texten anger, inte c₀ i den ekvivalenta profilen. |
| 2026-11-21 | P-R1 | 15. Temperaturen i ekv. (27) och t₀. | `Vader.temperatur_c` för båda (samma val som B4: t₀ = t_air). t_mean (avsnitt 5.4.2) är inte införd. |

## Anrop från kärnan (till P-R2 och P-S)

```python
from buller.nord2000.refraktion import atmosfar_ur_vader, calc_eq_ssp, ovre_atmosfar
from buller.nord2000.stralar_krokta import stralar_krokt_plan_mark

atm = atmosfar_ur_vader(vader)                 # ersätter atmosfar.homogen_atmosfar(vader)
atm_ovre = ovre_atmosfar(atm)                  # A₊, B₊ (ekv. 10); bara om atm.har_fluktuerande_refraktion

profil = calc_eq_ssp(h_se, h_re, atm)          # ekv. 21; profil.c0_m_s ger λ₀ och k₀ (ekv. 12–14)
stralar = stralar_krokt_plan_mark(d_e, h_se, h_re, profil.xi_per_m, profil.c0_m_s, atm.c_m_s)
# stralar.direkt.{gangtid_s, vagstracka_m, vinkelandring_rad, skuggzonsavstand_m, i_skuggzon}
# stralar.reflekterad.{gangtid_s, vagstracka_m, strykvinkel_rad, d_refl_m, ...}  (None i skuggzon)
# stralar.gangtidsskillnad_s                                                   (Δτ, 0 i skuggzon)

profil_ovre = calc_eq_ssp(h_se, h_re, atm_ovre)
dtau_ovre = stralar_krokt_plan_mark(d_e, h_se, h_re, profil_ovre.xi_per_m, profil_ovre.c0_m_s,
                                    atm.c_m_s).gangtidsskillnad_s              # Δτ₊ till F_Δτ (ekv. 112)
```

- h_se, h_re, d_e: höjder och avstånd relativt den ekvivalenta plana marken (`terrang.ekvivalent_plan_mark`), med
  minsta höjd 0,01 m satt av anroparen. Strålfunktionerna ändrar inte höjderna; `calc_eq_ssp` begränsar dem internt
  till 5·z₀ bara för profilen.
- `stralar_krokt_plan_mark(…, 0.0, c, c)` ger samma värden som `geometri.stralar_plan_mark` och kan ersätta den.
  Motsvarigheter: `r1_m` = `direkt.vagstracka_m`, `tau1_s` = `direkt.gangtid_s`, `r2_m`, `tau2_s`, `strykvinkel_rad`,
  `d_refl_m` på `reflekterad`.
- CalcEqSSPGround (P-R2): `ekvivalent_profil_ur_gradient(k·gradient, c_medel, h_s, h_r, atm)` med
  `gradient = ljudhastighetsgradient_per_s(h_s, h_r, atm)`, c̄ ur `calc_eq_ssp(...).c_medel_m_s` eller
  `medelljudhastighet_m_s`, och k = faktorn i ekv. (23) (0 under f_L). Δc₁₀ i ekv. (26) =
  `ljudhastighetsprofil_m_s(10, atm) − atm.c_m_s`. Obs: när ξ = 0 returneras c̄ = C, så hämta c̄ före gränsfallet om
  den behövs.
- Skärmbanor: `direkt_strale(d, z_1, z_2, xi, c0)` tar två godtyckliga höjder (källa, mottagare eller kant) och
  hänför själv profilen till den nedre. h″_SCR (ekv. 22) ingår inte här.
- Läget för uppåtbrytning väljs med sista argumentet (`uppat="exakt"`); det bör vara en inställning på kärnan tills
  tolkning 3 är avgjord.

## Önskade ändringar i delade filer

- **`atmosfar.py`** (ägs av B4/P-S): `homogen_atmosfar` bör ersättas av `refraktion.atmosfar_ur_vader`, och
  `HomogenAtmosfar` av `refraktion.Atmosfar`. Fältnamnen skiljer sig: `temperatur_c` → `temperatur_mark_c` och
  `temperatur_luft_c`; `ljudhastighet_m_s` → `c_m_s`. Enklast under övergången: behåll `HomogenAtmosfar` och låt
  `homogen_atmosfar` först anropa `atmosfar_ur_vader(vader)` och ge `BullerFel` om `har_refraktion` eller
  `har_fluktuerande_refraktion` är sann (samma beteende som i dag, en källa för kontrollerna). Modulens docstring
  ("omräkningen … finns inte i AV 1106/07 och hör till refraktionspaketet") kan då hänvisa hit.
- **`delmodell1.py`, `delmodell2.py`, `fresnel.py`**: tar i dag `HomogenAtmosfar` och anropar `stralar_plan_mark`.
  P-R2 behöver byta till `Atmosfar` och `stralar_krokt_plan_mark` per frekvens.
- **`docs/buller/tolkningar.md`**: de 15 raderna ovan. Raden "Hur översätts `Vader` …" (B4) kan då ändras: omräkningen
  finns nu.
- **`docs/buller/B4-uppdelning.md`**: öppen fråga 1 i avsnitt 7 är besvarad (se ovan); statusraderna för 5.5.2 och
  5.5.4–5.5.6 i avsnitt 2 blir "klar" (modul `refraktion.py`, `stralar_krokta.py`).

## Återstår

- Avgöra tolkning 3 (uppåtbrytning) mot testfallen.
- CalcEqSSPGround (5.5.3), h″_SCR (ekv. 22), RayCurvature och strålens höjd (5.5.7, ekv. 54–56), t_mean (5.4.2),
  A₊/B₊ in i delmodellerna, refraktion i FresnelZoneW och ekv. (301): andra paket.
- Ingen prövning mot testfallens facit (kräver P-R2 och B5).

## Förslag till post i `STATUS.md`

```
## 2026-11-21 – B4/P-R1 – Refraktion: väder → A, B, ekvivalent profil, krökta strålar
Gjort: refraktion.py och stralar_krokta.py (AV 1106/07 5.5.2, 5.5.4–5.5.6, bilaga F; TC 2.1–2.2; AV 1851/00 ekv. 26–27).
Mätt: 53 tester gröna. ξ = 0 återger räta strålar (rel. ≤ 1e-15); cirkelbåge mot oberoende geometri rel. 1e-15;
  reflektionslagen 1e-10 rad; d_SZ mot tangerande cirkel 1e-13.
Öppet: uppåtbrytning räknad med |ξ′| (rapporten) eller exakt – skiljer i Δτ; s_A, s_B härledda, inte citerade.
Nästa: P-R2 (CalcEqSSPGround, delmodell 1–2 med krökta strålar, skuggzon).
```
