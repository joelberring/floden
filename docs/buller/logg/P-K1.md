# P-K1 – Kildiffraktion (AV 1106/07 avsnitt 5.7.1, 5.7.2, 5.7.5)

Datum: 2026-11-21. Ägda filer: `src/buller/nord2000/kildiffraktion.py`,
`tests/buller/nord2000/test_kildiffraktion.py`, denna fil. Inga andra filer är ändrade.

Underlag: DELTA AV 1106/07 rev. 2014 s. 38–44 och 48–49, hjälpfunktionen Sign s. 160, FORCE TC-101327 avsnitt 2.3
(s. 4–5) och bilaga G (läst för gränssnittet, inte implementerad). AV 1849/00 bilaga H (s. 119–124) användes för att
avgöra ett tryckfel. Varje formel är avläst från sidbild (`pdftoppm`, 150 dpi och 300 dpi-utsnitt i
`/home/claude/verktyg/sidor/P-K1/`).

Dubbel diffraktion och tjock skärm (5.7.3–5.7.4) ingår inte (P-K2). Bilaga G ingår inte (P-4).

## Implementerat

| Funktion | Rapportens namn | Avsnitt, ekvationer |
| --- | --- | --- |
| `Kil`, `kil_rata_stralar` | indata β, θ_S, θ_R, τ, τ_S, τ_R, ℓ, R_S, R_R | 5.7.1, punktlistan och figur 9 |
| `modifierade_vinklar` | schemat θ'_R, θ'_S, β' | 5.7.1, sist (s. 43) |
| `tecken` | Sign | 5.23.17 (388) |
| `fresnel_f`, `fresnel_g` | f(x), g(x) | 5.7.1 (85)–(86), tabell 4–5 |
| `vinkelfunktion_a` | A(θ_n) | 5.7.1 (82) |
| `kiltryck_impedans`, `…_delar` | pwedge | 5.7.1 (78)–(91), Q ur impedans enligt (80) |
| `kiltryck_reflektionskoefficienter`, `…_delar` | pwedge med givna Q_S, Q_R | 5.7.1 (78)–(90) med TC-101327 2.3 |
| `kiltryck_icke_reflekterande`, `…_delar` | pwedge0 | 5.7.5 (105)–(106) |
| `kildiffraktionskoefficient_impedans` | Dwedge | 5.7.2 (92)–(94) |
| `kildiffraktionskoefficient_reflektionskoefficienter` | Dwedge med givna Q | 5.7.2 med TC-101327 2.3 |
| `kildiffraktionskoefficient_icke_reflekterande` | Dwedge0 | 5.7.5 (107) |
| `Kiltryck` | p_diffr, direkt stråle, speglad stråle var för sig | (87)–(90) |

Normering: fritt fält på avståndet R är exp(jωτ)/R. Tidskonventionen exp(+jωτ) är densamma som i `mark.py`.
Funktionerna är skalära (en frekvens per anrop). Uppmätt tid: 21 µs per anrop med impedans, 11 µs med givna Q.

Ändringen i TC-101327 som rör avsnitten: 2.3 säger att Q₂–Q₄ enligt ekv. (80) efter 2018 bara gäller delmodell 3, och
att delmodell 4–6 ska ge kilytornas koefficienter som vägda medelvärden av den reella ekvivalenta koefficienten
(bilaga G). Därför finns två ingångar: en med impedanser (ekv. 80) och en med färdiga Q_S, Q_R. TC 2.4–2.5 rör
5.7.3–5.7.4 (P-K2).

## Hur kärnan ska anropa

```python
from buller.nord2000.kildiffraktion import (
    Kil, kil_rata_stralar, kiltryck_impedans, kiltryck_reflektionskoefficienter,
    kiltryck_icke_reflekterande, kildiffraktionskoefficient_impedans,
    kildiffraktionskoefficient_reflektionskoefficienter,
)

# Homogen atmosfär: τ_S = R_S/c, τ_R = R_R/c, ℓ = R_S + R_R, τ = ℓ/c.
kil = kil_rata_stralar(beta_rad, theta_s_rad, theta_r_rad, r_s_m, r_r_m, c0_m_s)
# Med refraktion (P-R1 ger τ_S, R_S, τ_R, R_R och de refraktionsändrade vinklarna):
kil = Kil(beta_rad, theta_s_rad, theta_r_rad, tau, tau_s, tau_r, ell, r_s, r_r)

# Delmodell 3, konvext segment, ekv. (151): ΔL_i = 20·lg|p·R_SR|, samma impedans på båda ytor.
p = kiltryck_impedans(f_hz, kil, z_i, z_i)

# Delmodell 4, ekv. (160), efter 2018: Q_avS, Q_avR ur TC-101327 bilaga G (räknas i P-4).
p = kiltryck_reflektionskoefficienter(f_hz, kil, q_av_s, q_av_r)

# Skuggzonsavskärmning (ekv. 387) och ändliga skärmar (bilaga E): icke-reflekterande kil.
p = kiltryck_icke_reflekterande(f_hz, kil)

# P-K2 (ekv. 97–98, 102–103): Dwedge med τ och ℓ angivna för sig i Kil; hård yta = math.inf.
d1 = kildiffraktionskoefficient_impedans(f_hz, kil_1, z_1s, math.inf)
```

- Vinklar: θ_S och θ_R mäts från mottagarsidans yta, β utanför kilen (figur 9). Tunn skärm: β = 2π.
- Schemat för vinklar utanför 0 ≤ θ_R ≤ θ_S ≤ β ≤ 2π tillämpas inne i funktionerna; anroparen ska alltså ge de
  omodifierade vinklarna (även för spegelkälla och spegelmottagare i delmodell 4–6).
- Impedanser är normaliserade (som `mark.markimpedans` ger). `math.inf` ger Q = 1.
- `…_delar` ger `Kiltryck(diffrakterat, direkt, reflekterat)` om en delmodell behöver termerna var för sig.
- `BullerFel` ges för: β < π eller β > 2π, θ_R > θ_S efter schemat, icke-positiva sträckor, gångtider eller frekvens,
  impedans 0 eller NaN, icke-ändliga Q, R₁/R₂/R₃ = 0, samt β = π med θ_S + θ_R = π (se tolkning 3).

## Prövat

`python -m pytest tests/buller/nord2000/test_kildiffraktion.py -q` → **41 gröna**, 0 hoppade, 1,9 s (scipy 1.18.1
finns; utan scipy hoppas 6 tester över). `ruff check`, `ruff format --check` och `mypy` (strict) på de två filerna: rent.

`python -m pytest tests/buller/nord2000 -q` → 235 gröna, **2 fel i en annan agents fil**
(`test_terrang_forenkling.py::TestGranser::test_segmentlangd_av_1106_07_ekv_393` och
`::TestKraven::test_segmentlangdskravet_i_av_1106_07`, `AssertionError: 0 not greater than 0`). Inte mina filer och
inte beroende av dem; inte rättade.

### Hård tunn halvplansskärm mot oberoende lösning

Referens: Macdonalds exakta lösning för punktkälla vid ett hårt halvplan (H. M. Macdonald, "A class of diffraction
problems", Proc. London Math. Soc. (2) 14, 1915, s. 410–427; återgiven i Bowman, Senior & Uslenghi, *Electromagnetic and
Acoustic Scattering by Simple Shapes*, 1969, kap. 8). Med fritt fält exp(jkR)/R:

`p = Σ över (φ − φ₀, φ + φ₀) av [exp(jkR)/(2R) + (jk/2)·∫₀^m H₁⁽¹⁾(kR·cosh u) du]`,
`m = asinh(2·√(r·r₀)·cos(Δφ/2)/R)`, `R² = r² + r₀² − 2·r·r₀·cos Δφ + z²`.

**Förbehåll:** jag har inte haft tillgång till källtexten, så formeln är inte avläst ur den. Den är i stället prövad
som lösning (testklassen `TestReferenslosning`, 500 Hz, källa r₀ = 3 m, φ₀ = 1,3π):

| Egenskap | Uppmätt |
| --- | --- |
| Helmholtz ekvation, centrala differenser h = 2 mm, 10 slumpade punkter (även i skuggan) | residual ≤ 1,4·10⁻⁴ relativt k²\|p\| (differensfelets storlek) |
| ∂p/∂φ = 0 på φ = 0 och φ = 2π, r = 0,7, 2, 5 m | \|∂p/∂φ\|/\|p\| ≤ 1,2·10⁻⁴ (φ = 2π), ≤ 5·10⁻⁸ (φ = 0) |
| Källsingularitet: p − exp(jkR)/R för R = 1 cm och 1 mm | 0,0062 resp. 0,0062 (begränsat, skillnad 5·10⁻⁴) |
| Identiteten ∫₀^∞ H₁⁽¹⁾(x·cosh u) du = −j·exp(jx)/x (används för den oändliga delen) | 9 siffror (skript, utanför repot) |

Referensen räknas med `scipy.special.hankel1` och sammansatt Gauss–Legendre (8 punkter, högst 1 rad fas per panel).

Avvikelse pwedge (β = 2π, Z_S = Z_R = ∞) − referens, R_S och R_R slumpade i 0,5–100 m, θ_S i (π, 2π), θ_R i (0, θ_S):

| Frekvens | 400 geometrier (skript): max \|ΔL\|, skugga / belyst | rms skugga | 60 geometrier (testet): max \|ΔL\| | testets gräns |
| --- | --- | --- | --- | --- |
| 25 Hz | 0,204 / 0,057 dB | 0,044 dB | 0,123 dB | 0,3 dB |
| 100 Hz | 0,144 / 0,031 dB | 0,015 dB | 0,023 dB | 0,3 dB |
| 500 Hz | 0,005 / 0,007 dB | 0,001 dB | 0,007 dB | 0,02 dB |
| 2 kHz | 0,003 / 0,002 dB | 0,001 dB | 0,004 dB | 0,02 dB |
| 10 kHz | 0,001 / 0,0003 dB | 0,0002 dB | 0,003 dB | 0,02 dB |

Största avvikelserna ligger vid låg frekvens och korta sträckor (k·R av storleken 1–5), där rapportens närmeform av
Hadden–Pierce-lösningen (ekv. 81, 83) är som sämst. Komplex relativ avvikelse högst 2,4 % (25 Hz), 2,5·10⁻⁴ (10 kHz).

Skärmfall utan mark (källa 0,5 m hög 10 m framför 3 m hög skärm, mottagare 1,5 m hög 20 m bakom), nivå relativt fritt
fält, Nord2000 / exakt: 25 Hz −6,705 / −6,717; 63 Hz −8,678 / −8,674; 250 Hz −12,633 / −12,622; 1 kHz −17,957 /
−17,954; 4 kHz −23,881 / −23,881; 10 kHz −27,853 / −27,854 dB.

Gränserna i testet är satta som övre gräns för den redovisade avvikelsen; ingenting i koden är justerat efter
jämförelsen.

### Kontinuitet över skugg- och reflexgränserna

Kil β = 1,6π, R_S = 7 m, R_R = 11 m, σ = 200 (källsida) och 12,5 kNs/m⁴ (mottagarsida). Relativ ändring i totalfältet
mellan θ_S = gräns − 2·10⁻⁸ och gräns + 2·10⁻⁸ rad:

| Gräns | 25 Hz | 250 Hz | 2 kHz | 10 kHz |
| --- | --- | --- | --- | --- |
| Skugggräns θ₁ = π | 6,2·10⁻⁵ | 1,1·10⁻⁴ | 1,0·10⁻⁴ | 9,9·10⁻⁵ |
| Reflexgräns mottagarsidans yta θ₂ = π | 3,8·10⁻⁵ | 1,5·10⁻⁵ | 1,5·10⁻⁵ | 7,8·10⁻⁶ |
| Reflexgräns källsidans yta θ₃ = π | 3,9·10⁻⁵ | 2,3·10⁻⁵ | 2,3·10⁻⁵ | 5,2·10⁻⁵ |

Testets gräns: 2,5·10⁻⁴ (0,002 dB). Det språng som finns kvar kommer av att tabellernas a₀ ger f(0) = 0,499975 och
g(0) = 0,500024 i stället för ½: termens språng blir (f(0) + g(0)) + j(f(0) − g(0)) = 1 − 4,9·10⁻⁵j gånger strålen i
stället för exakt 1. Samma storlek för tunn hård skärm, för pwedge0 och med givna Q. På skugggränsen ger pwedge0
−6,02 dB relativt fritt fält (p_diffr = −½·exp(jωτ)/ℓ inom 10⁻⁴).

Med den **tryckta** tilldelningen i ekv. (80) (se tolkning 1) hoppar fältet över reflexgränsen θ₂ = π: hård källsida,
σ = 12,5 på mottagarsidan, 250 Hz ger |p|·ℓ = 1,899 på ena sidan och 0,665 på den andra (9 dB). Med vald tilldelning
1,27034 / 1,27032. Testet `test_tryckt_tilldelning_i_ekv_80_ger_sprang` håller kvar det beviset.

### Reciprocitet

Ombytt källa och mottagare (θ_S ↔ β − θ_R, R, τ och Z byter plats), 300 slumpade fall (β i 1,02π–2π, 25 Hz–10 kHz,
σ 10–20 000): största relativa skillnad 9,1·10⁻¹² (impedans), 3,1·10⁻¹² (givna Q), 3,6·10⁻¹² (pwedge0). Gäller även
med τ ≠ τ_S + τ_R (anrop som för tjock skärm). Reciprociteten skiljer inte mellan tryckt och vald tilldelning i
ekv. (80); det gör bara kontinuiteten.

### Kilvinkel π (ingen kil)

Metoden anger β > π. I gränsen β = π är A(θ₄) = −A(θ₁) och A(θ₃) = −A(θ₂), så termerna i ekv. (78) tar ut varandra
parvis när Q₄ = Q₁ och Q₃ = Q₂.

| Fall (R_S = 20 m, R_R = 30 m) | Resultat |
| --- | --- |
| Hårda ytor, β = π | \|p_diffr\|·ℓ ≤ 4·10⁻¹⁷; p = direkt + speglad stråle, lika med spegelkällemetoden inom 10⁻¹¹ |
| Hårda ytor, β = π + 10⁻⁹ | \|p_diffr\|·ℓ = 1,3·10⁻⁹ (100 Hz), 6·10⁻¹⁰ (1 kHz): gränsen nås kontinuerligt |
| σ = 200 på båda ytor, θ_S = 0,8π, θ_R = 0,1π | \|p_diffr\|/\|direkt + speglad\| = 0,012 (100 Hz; +0,11 dB), 0,001 (1 kHz; 0,00 dB) |
| σ = 200, θ_S = 0,95π, θ_R = 0,02π (nära strykande) | 0,125 (100 Hz; −1,16 dB), 0,022 (1 kHz; −0,12 dB) |
| pwedge0, 1 kHz | \|p\|/\|direkt\| = 1,023 resp. 0,885 (100 Hz: 1,045 resp. 0,867) |

Alltså: för hårda ytor återges fältet utan diffraktion exakt. För ändlig impedans gör metoden inte det, eftersom Q_S
räknas med strykvinkeln β − θ_S och Q_R med θ_R (olika även för samma impedans) och Q₄ = Q_S·Q_R ≠ 1. För den
icke-reflekterande kilen finns bara termen n = 1, som inte tas ut. Metoden påstår ingetdera, så det är inga fel, men
den som anropar med β nära π ska inte vänta sig delmodell 1.

### Övrigt

| Vad | Hur | Uppmätt |
| --- | --- | --- |
| f(x), g(x), tabell 4–5 | mot definitionen genom Fresnelintegralerna (Abramowitz & Stegun 7.3.5–7.3.6, `scipy.special.fresnel`), 7 000 punkter i 0–50 | max fel 2,8·10⁻⁵ (f, x < 5), 2,6·10⁻⁵ (g, x < 5), 3,1·10⁻⁵ (f, x ≥ 5), 2,0·10⁻⁶ (g, x ≥ 5) |
| Skarven i x = 5 | polynom mot asymptot | språng 2,3·10⁻⁵ (f), 1,8·10⁻⁵ (g) |
| A(θ_n) | ±π/2 på var sida om θ = π; (π/2)(1 − ν) i θ = 0 och 2β | 12 decimaler |
| Vinkelschemat | de fyra fallen var för sig och två i följd; trycket lika med direkt angivna modifierade vinklar | exakt |
| D·exp(jωτ)/ℓ = p | alla tre varianter, τ ≠ τ_S + τ_R | 10⁻¹² |
| Z = ∞ ≡ Q = 1; Q = 0, 0 ≡ pwedge0 | exakt likhet | bit för bit |
| Djup skugga, tunn skärm | 20·lg\|D\| vid 100 Hz, 1 kHz, 10 kHz | −26,57, −36,55, −46,55 dB (−10 dB/dekad) |
| `BullerFel` | 13 fall av ogiltig indata | – |

Att tabellernas 26 koefficienter är rätt avlästa bekräftas av jämförelsen med Fresnelintegralerna: ett siffer-fel i
en koefficient skulle synas som ett fel långt över 3·10⁻⁵.

## Tolkningar

| Datum | Paket | Fråga | Val och skäl |
| --- | --- | --- | --- |
| 2026-11-21 | P-K1 | Ekv. (80) är tryckt med Q₂ = Q_S(β − θ_S, Z_S) och Q₃ = Q_R(θ_R, Z_R). Stycket före ekv. (89) har villkoret θ₃ < π och "source face" men ekvationen har θ₂ och Q_R; stycket före ekv. (90) har θ₂ < π och "receiver face" men ekvationen har θ₃ och Q_S. | Q₂ = Q_R, Q₃ = Q_S; ekv. (89) gäller när θ₂ < π och ekv. (90) när θ₃ < π. Så står det i AV 1849/00 bilaga H (Q₂ = Q_R, Q₃ = Q_S; H11 med θ₂ < π, H14 med θ₃ < π). θ₂ = θ_S + θ_R är geometriskt speglingen i mottagarsidans yta (vinklarna mäts från den), vilket ekv. (89) själv följer (ψ_G,R, Z_R). Tryckt tilldelning ger ett språng på upp till en hel speglad stråle över reflexgränsen (uppmätt 9 dB); vald är kontinuerlig. Q₄ och reciprociteten påverkas inte. Alternativ: bokstavligt. |
| 2026-11-21 | P-K1 | ε-regeln (ε = 10⁻⁸ dras från θ_n när \|θ_n − π\| < ε): gäller den bara diffraktionstermen eller också villkoren θ_n < π i ekv. (88)–(90)? Fallet θ₁ = π täcks varken av ekv. (87) eller (88). | Det justerade θ_n används på båda ställen. En geometri exakt på en gräns räknas alltså som strax inne på den belysta sidan (stråle med, diffraktionsterm −½), vilket är gränsvärdet från den sidan. Alternativ: justera bara i ekv. (82); då blir fältet på gränsen ½ stråle för lite eller för mycket. |
| 2026-11-21 | P-K1 | "The solution is only valid for β > π." Vad göra med β = π och β < π? | β < π (utöver 10⁻¹² rad avrundning) och β > 2π ger `BullerFel`. β = π tillåts som gränsvärde: formlerna är reguljära där och ger rätt svar för hårda ytor. Undantag: β = π (inom ε) och θ_S + θ_R = π (inom ε) ger `BullerFel`, eftersom θ₂ = θ₃ = π då sammanfaller och ε-regeln skulle föra båda termerna till samma sida (uppmätt följd: den speglade strålen försvinner helt för hårt plan). För β > π + ε kan det inte inträffa. Alternativ: `BullerFel` för alla β ≤ π. |
| 2026-11-21 | P-K1 | sin\|A\|/\|A\| i ekv. (81) är 0/0 när A(θ_n) = 0 (θ_n = β + π, kan inträffa för θ₄). | Produkten A·E_ν räknas med A·sin\|A\|/\|A\| = sin A, som är 0 där. Ingen ändring av värdet någon annanstans. |
| 2026-11-21 | P-K1 | TC-101327 2.3: när kilytorna ges som Q_avS, Q_avR (bilaga G), vilken koefficient ska den speglade strålen i ekv. (89)–(90) ha? TC nämner bara Q₂–Q₄. | Förval: samma Q_avR resp. Q_avS som i diffraktionstermen, eftersom det håller fältet kontinuerligt över reflexgränsen (i impedansformen är de två koefficienterna lika just på gränsen). Kan ersättas genom parametrarna `q_reflex_kalla`, `q_reflex_mottagare`. **Öppen fråga till P-4**, se nedan. Alternativ: Q(f, τ_n, ψ_G, Z) ur närmaste segments impedans som före 2018. |
| 2026-11-21 | P-K1 | Schemat för vinklar: i vilken ordning, och vad med fall det inte täcker? | Först θ_R < 0 (två fall), sedan θ_S mot β och 2π med de redan modifierade värdena, som rubrikerna i schemat säger. θ_R > θ_S efter schemat ger `BullerFel` (AV 1849/00 bilaga H: lösningen gäller inte när källa och mottagare är på samma sida om kanten). |
| 2026-11-21 | P-K1 | Dwedge0 (ekv. 107) definieras bara som funktionsnamn. | D = ℓ·p/exp(jωτ) som i ekv. (92)–(93), med p ur pwedge0. |
| 2026-11-21 | P-K1 | Z = ∞ skrivs som argument till Dwedge i ekv. (102)–(103). | Oändlig impedans ger Q = 1 utan att gå genom ekv. (58)–(60). Impedans 0 eller NaN ger `BullerFel`. |
| 2026-11-21 | P-K1 | f(x), g(x): AV 1106/07 anger polynomet för x < 5, AV 1849/00 för 0 < x < 5. | Polynomet används för 0 ≤ x < 5 (x = 0 ger a₀). Negativt x ger `BullerFel`; ekv. (84) anropar alltid med \|B\|. |
| 2026-11-21 | P-K1 | arcsin-argumenten i ekv. (89)–(90) kan genom avrundning hamna strax utanför [0, 1]. | Begränsas till [0, 1]. Exakt geometri ger alltid ett värde i intervallet. |

Noterat utan val: ekv. (80) använder τ_S + τ_R (inte τ) som gångtid i Q, och ekv. (88)–(90) räknar R_n och τ_n med
cosinussatsen även när τ och ℓ är definierade på annat sätt; båda är implementerade bokstavligt.

## Önskade ändringar i delade filer

Inga är nödvändiga. Två förslag till samordnaren:

1. `tecken` (Sign, ekv. 388) ligger i `kildiffraktion.py`. Den behövs också i ekv. (301), (307) och (367). Flytta den
   till `frekvenser.py` bredvid `heaviside` när någon ändå rör den filen, och importera därifrån.
2. B00 regel 6 säger att tabellvärden ska ligga i datafiler med källa. Koefficienterna i tabell 4–5 ligger som
   konstanter i modulen (med tabellhänvisning), eftersom paketet bara äger tre filer. Flyttas vid behov.

De tio raderna under "Tolkningar" ska föras in i `docs/buller/tolkningar.md`. `STATUS.md` och `PLAN.md` är inte
rörda (ägs av samordnaren). Förslag till post:

```
## 2026-11-21 – B4/P-K1 – Kildiffraktion (AV 1106/07 5.7.1–5.7.2, 5.7.5)
Gjort: pwedge, Dwedge, pwedge0, Dwedge0 med impedans eller givna Q (TC-101327 2.3), vinkelschemat.
Mätt: 41 tester gröna. Hård tunn skärm mot Macdonalds exakta lösning: max 0,20 dB (25 Hz), 0,14 dB (100 Hz),
  ≤ 0,007 dB (≥ 500 Hz). Språng över skugg- och reflexgränser ≤ 1,1·10⁻⁴ relativt. Reciprocitet 9·10⁻¹².
Öppet: Q för speglad stråle med bilaga G (P-4); tryckfel i ekv. (80) och före (89)–(90) tolkat enligt AV 1849/00.
Nästa: P-K2, P-3, P-4.
```

## Öppna frågor till andra paket

- **P-4:** vilken koefficient den speglade strålen i ekv. (89)–(90) ska ha när kilytorna beskrivs med Q_av (tolkning
  5). Läs TC-101327 bilaga G.2–G.5 och delmodell 4 (5.13) med den frågan; avgörs annars av testfall 71–74.
- **P-R2:** ekv. (387) (ShadowZoneShielding) är tryckt med `pwedge` men utan impedanser i argumentlistan. Avsnitt 5.7.5
  nämner skuggzonsavskärmning som användning av den icke-reflekterande kilen, så `kiltryck_icke_reflekterande` är
  troligen det som avses. Inte avgjort här.
- **P-3:** nära β = π ger kilen med ändlig impedans inte samma fält som plan mark (tabellen ovan); övergången sköts av
  r_flat, inte av kilformeln.

## Osäkert

- Referenslösningen är skriven utan tillgång till Macdonalds eller Bowmans text. Att den är rätt vilar på att den
  uppfyller differentialekvationen, randvillkoret och källsingulariteten (uppmätt ovan) och på att två av varandra
  oberoende framställningar (den och Nord2000:s) möts inom 0,003 dB vid höga frekvenser. Utstrålningsvillkoret och
  kantvillkoret är inte prövade numeriskt för sig.
- Tolkning 1 (Q₂/Q₃) vilar på AV 1849/00 från 2000/2006 mot den normativa texten från 2014. Kontinuitetsbeviset är
  starkt, men om referensprogrammet bakom testfallen följer den tryckta ekv. (80) syns det i fall 31–44 och 71–114
  nära reflexgränser. För hårda ytor (Q_S = Q_R) spelar valet ingen roll.
- Ingen prövning mot ett känt svar finns för kil med ändlig impedans eller för β < 2π utöver gränsfallet β = π:
  där är bara kontinuitet, reciprocitet och gränsvärden prövade.
- Avvikelsen 0,1–0,2 dB mot exakt lösning vid 25–100 Hz och korta sträckor är metodens (närmeformen i ekv. 81 och
  83), inte implementationens, såvitt jag kan bedöma: den avtar som väntat med k·R. Det är inte bevisat var för sig.
- Refraktion: funktionerna tar τ_S, τ_R, R_S, R_R och vinklar som indata och är bara prövade med räta strålar
  (τ = R/c). Med krökta strålar är τ_n ur cosinussatsen (ekv. 88–90) en närmeform i metoden själv.

## Återstår

Inget inom paketets avgränsning. Utanför: P-K2 (5.7.3–5.7.4), bilaga G (P-4), inkoppling i `karna.py` (P-S).
