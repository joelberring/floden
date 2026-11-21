# P-K2 – Dubbel diffraktion (AV 1106/07 avsnitt 5.7.3–5.7.4, TC-101327 2.4–2.5)

Datum: 2026-11-21. Ägda filer: `src/buller/nord2000/kildiffraktion_dubbel.py`,
`tests/buller/nord2000/test_kildiffraktion_dubbel.py`, denna fil. Inga andra filer är ändrade, inte heller P-K1:s.

Underlag: DELTA AV 1106/07 rev. 2014 s. 44–48 (ekv. 95–104, figur 10–11), FORCE TC-101327 avsnitt 2.3–2.5 (s. 5) och
bilaga G.3–G.4 (s. 17–19, lästa i textutdrag för gränssnittet, inte implementerade). AV 1849/00 avsnitt 4.4.3.2–4.4.3.3 (s. 43–48)
som bakgrund. Ekvationerna (95)–(104) och TC 2.4–2.5 är avlästa från sidbilder (`pdftoppm -r 150`,
`/home/claude/verktyg/sidor/P-K2/`). Mätskript: `/home/claude/verktyg/skript/P-K2/` (utanför repot).

## Implementerat

| Funktion | Rapportens namn | Avsnitt, ekvationer |
| --- | --- | --- |
| `TvaKanter`, `tva_kanter_rata_stralar` | indata τ_S, τ_M, τ_R, R_S, R_M, R_R, β₁, θ_1S, θ_1R, β₂, θ_2S, θ_2R; τ och ℓ | punktlistorna i 5.7.3 och 5.7.4; (95)–(96), (100)–(101) |
| `delkilar` | argumentlistorna till Dwedge för de två kilarna | (97)–(98), (102)–(103) |
| `dubbel_diffraktion` | p̂ = faktor·D̂₁·D̂₂·exp(jωτ)/ℓ | (97)–(98), (102)–(103) med TC 2.4–2.5 |
| `tva_kilar_tryck_impedans`, `…_delar` | p2wedge | 5.7.3, (95)–(99) |
| `tva_kilar_tryck_reflektionskoefficienter`, `…_delar` | p2wedge med givna Q för fyra kilytor | 5.7.3 med TC 2.3 (bilaga G.4) |
| `tjock_skarm_tryck_impedans`, `…_delar` | p2edge (hård ovansida, faktor 0,5) | 5.7.4, (100)–(104) |
| `tjock_skarm_tryck_reflektionskoefficienter`, `…_delar` | p2edge med givna Q_S, Q_M, Q_R | 5.7.4 med TC 2.3 (bilaga G.3) |
| `Dubbeldiffraktion` | D̂₁, D̂₂ och p̂ | – |

Dwedge hämtas ur P-K1 (`kildiffraktionskoefficient_impedans`, `…_reflektionskoefficienter`); ingenting i
kildiffraktionen är omskrivet. Samma normering som där: fritt fält på avståndet R är exp(jωτ)/R. Skalärt, en frekvens
per anrop; uppmätt tid 40 µs per anrop med impedanser.

I alla fyra argumentlistor är τ = τ_S' + τ_R' och ℓ = R_S' + R_R' för den kil som räknas (den viktigaste kilen får
summan av två delsträckor på den sida där den andra kanten ligger). `Kil` används alltså med konsekventa summor.

Ändringarna i TC-101327: 2.4–2.5 byter det tryckta j2ρfτ mot jωτ i ekv. (97)–(98) och (102)–(103); infört. 2.3 med
bilaga G gör att delmodell 5 och 6 efter 2018 ger kilytornas koefficienter som vägda medelvärden i stället för
impedanser, och att skärmens ovansida inte längre alltid är hård (Q'_avM, ekv. 422–426). Därför finns båda ingångarna.
Bilaga G själv hör till P-5 och P-6.

## Hur kärnan ska anropa

```python
from buller.nord2000.kildiffraktion_dubbel import (
    TvaKanter, tva_kanter_rata_stralar,
    tva_kilar_tryck_impedans, tva_kilar_tryck_reflektionskoefficienter,
    tjock_skarm_tryck_impedans, tjock_skarm_tryck_reflektionskoefficienter,
    dubbel_diffraktion, delkilar,
)

# Homogen atmosfär:
geo = tva_kanter_rata_stralar(beta_1, theta_1s, theta_1r, beta_2, theta_2s, theta_2r, r_s, r_m, r_r, c0)
# Med refraktion (P-R1 ger gångtider och sträckor per delsträcka, delmodellen vinklarna):
geo = TvaKanter(beta_1, theta_1s, theta_1r, beta_2, theta_2s, theta_2r, tau_s, tau_m, tau_r, r_s, r_m, r_r)

viktigast = "forsta"   # eller "andra"; avgörs i terrängtolkningen (5.21, P-T)

# Delmodell 5, ekv. (192), efter 2018: Q_avS, Q'_avM, Q_avR ur TC-101327 bilaga G.3.
p = tjock_skarm_tryck_reflektionskoefficienter(f_hz, geo, q_av_s, q_av_m, q_av_r, viktigast)
# Delmodell 5 som före 2018 (hård ovansida, impedans på sidorna):
p = tjock_skarm_tryck_impedans(f_hz, geo, z_s, z_r, viktigast)

# Delmodell 6, efter 2018: Q_avS,1, Q'_avR,1, Q'_avS,2, Q_avR,2 ur bilaga G.4.
p = tva_kilar_tryck_reflektionskoefficienter(f_hz, geo, q_1s, q_1r, q_2s, q_2r, viktigast)
p = tva_kilar_tryck_impedans(f_hz, geo, z_1s, z_1r, z_2s, z_2r, viktigast)   # före 2018
```

- Vinklarna är indata, som i ekv. (99) och (104). Punktlistorna säger att θ_1R mäts mot T₂ om T₂ ligger över linjen
  T₁–R och annars mot R, och θ_2S mot T₁ om T₁ ligger över linjen T₂–S och annars mot S; för tjock skärm blir det
  θ_1R = 0 respektive θ_2S = β₂ i det första fallet. Delmodell 5 gör detta i ekv. (194)–(195) (Max-uttrycken),
  delmodell 6 på motsvarande sätt. Modulen kan inte kontrollera det, eftersom den inte har koordinaterna.
- Alla vinklar mäts från respektive kils yta mot mottagaren (figur 9). Vinkelschemat i 5.7.1 för spegelkälla och
  spegelmottagare tillämpas inne i P-K1:s funktioner.
- `…_delar` ger `Dubbeldiffraktion(d_1, d_2, tryck)`.
- `dubbel_diffraktion(f, geo, koefficient_1, koefficient_2, viktigast, faktor)` tar två funktioner `Kil → D̂` och är
  till för den som behöver beskriva kilytorna på annat sätt (till exempel egna Q för den speglade strålen, se öppen
  fråga nedan).
- `BullerFel` ges för: icke-ändliga vinklar, icke-positiva gångtider eller sträckor (däribland R_M = 0), okänt värde på
  `viktigast`, frekvens ≤ 0 eller icke-ändlig, och allt som P-K1 vägrar (β < π, θ_R > θ_S efter schemat, m.m.).

## Prövat

`python -m pytest tests/buller/nord2000/test_kildiffraktion_dubbel.py -q` → **29 gröna** (59 deltester), 1,6 s. Inget
test kräver scipy. `ruff check`, `ruff format --check` och `mypy` på de två filerna: rent.

`python -m pytest tests/buller/nord2000 -q` → 422 gröna, **1 fel i en annan fil**:
`test_terrang_karna.py::TestNord2000Karna::test_refraktion_vagras` (`AssertionError: BullerFel not raised`). Inte mina
filer och inte beroende av dem; inte rättat.

Geometrin i testerna har räta strålar, c = 340 m/s. Koordinater är (x; z) i meter.

### Den andra kanten långt under siktlinjen från den första

Två tunna hårda skärmar, första viktigast (ekv. 97). Källa (0; 1), T₁ (20; 5), mottagare (60; 2), T₂ i x = 40 på
djupet d under linjen T₁–R. Jämförelse med en ensam tunn skärm i T₁ (P-K1). Relativ avvikelse |p₂ − p₁|/|p₁| och
nivåskillnad:

| d | 100 Hz | 1 kHz | 8 kHz |
| --- | --- | --- | --- |
| 0 m (på linjen) | 0,468 / −5,44 dB | 0,490 / −5,84 dB | 0,496 / −5,96 dB |
| 1 m | 0,354 / −3,60 dB | 0,230 / −0,13 dB | 0,098 / +0,20 dB |
| 2 m | 0,269 / −1,87 dB | 0,128 / +0,49 dB | 0,047 / −0,05 dB |
| 4 m | 0,163 / +0,62 dB | 0,062 / +0,48 dB | 0,023 / +0,17 dB |
| 8 m | 0,054 / −0,48 dB | 0,015 / −0,03 dB | 0,007 / +0,05 dB |
| 16 m | 0,068 / +0,57 dB | 0,034 / +0,29 dB | 0,038 / +0,32 dB |

Resultatet går alltså mot en kil, men bara så länge omvägen över T₂ är liten mot sträckorna: vid 16 m växer
avvikelsen igen. Orsaken är att ekv. (97) sätter mottagaren på avståndet R_M + R_R från T₁ (inte |T₁R|) i D̂₁, och att
D̂₂:s direkta stråle bara rättar fasen och 1/R-faktorn för den skillnaden, inte D̂₁:s beroende av R_R. Räknas den
ensamma kilen med R_R = R_M + R_R och skalas med (R_M + R_R)/|T₁R|·exp(jω(|T₁R|/c − τ_M − τ_R)), blir avvikelsen:

| d | 1 m | 2 m | 4 m | 8 m | 16 m | 32 m |
| --- | --- | --- | --- | --- | --- | --- |
| 100 Hz | 0,354 | 0,269 | 0,161 | 0,069 | 0,019 | 0,003 |
| 1 kHz | 0,230 | 0,128 | 0,059 | 0,022 | 0,006 | 0,001 |
| 8 kHz | 0,098 | 0,047 | 0,021 | 0,008 | 0,002 | 0,0004 |

Den går mot noll utan att vända (testet `test_resten_ar_omvagen_over_den_andra_kanten`). Det som då återstår mot den
verkliga ensamma kilen är metodens, inte implementationens: i praktiken tar r_scr2 (5.21.2) bort den andra skärmen
långt innan omvägen blir stor.

På linjen (d = 0) är T₂ skugggräns för den andra kilen och fältet halveras: −5,96 dB vid 8 kHz mot teorins −6,02 dB.

Andra viktigast (ekv. 98), spegelvänt fall (källa (0; 2), T₂ (40; 5), mottagare (60; 1), T₁ 8 m under linjen S–T₂):
0,054 (100 Hz), 0,015 (1 kHz), 0,007 (8 kHz), samma tal som ovan, som reciprociteten kräver.

Tjock hård skärm, första viktigast (ekv. 102): källa (15; 0), T₁ (20; 5), mottagare (60; 30) som ser över T₂; T₂ i
(20 + b; 5). Jämförelse med hård kil i T₁ (β = 3π/2, ovansidan som yta mot mottagaren):

| Bredd b | 2 m | 4 m | 8 m | 16 m |
| --- | --- | --- | --- | --- |
| 100 Hz | 0,245 / −1,03 dB | 0,196 / +0,42 dB | 0,146 / +1,18 dB | 0,087 / −0,46 dB |
| 1 kHz | 0,103 / −0,91 dB | 0,071 / −0,60 dB | 0,049 / +0,27 dB | 0,033 / +0,22 dB |
| 8 kHz | 0,038 / +0,30 dB | 0,026 / +0,15 dB | 0,015 / −0,13 dB | 0,008 / −0,02 dB |

Här syns också att faktorn 0,5 gör vad TC-101327 G.3 säger: D̂₂ innehåller direkt stråle och spegling i den hårda
ovansidan (2 × fritt fält), och 0,5·D̂₂ går mot 1.

### Tjock skärm med bredden mot noll

Avsnitt 5.7.4 säger inte att p2edge går mot tunn skärm när R_M → 0, och det gör den inte heller. Övergången mellan
en och två kanter görs i metoden med r_scr12 (5.21.3, ekv. 314–315). Uppmätt: källa (0; 1), mottagare (60; 2), skärm
med lodräta sidor och ovansida på höjden 5 m kring x = 20, nivå relativt tunn skärm i (20; 5),
första viktigast / andra viktigast:

| Bredd | 100 Hz | 1 kHz | 8 kHz |
| --- | --- | --- | --- |
| 1 m | +1,03 / +3,13 dB | +0,50 / +4,56 dB | −2,28 / +0,32 dB |
| 0,3 m | +1,83 / +4,26 dB | +1,51 / +6,61 dB | −0,21 / +4,38 dB |
| 0,1 m | +2,45 / +5,06 dB | +2,23 / +7,91 dB | +0,94 / +6,93 dB |
| 1 cm | +3,20 / +5,96 dB | +3,60 / +9,84 dB | +2,50 / +10,01 dB |
| 1 mm | +3,47 / +6,28 dB | +4,35 / +10,74 dB | +3,83 / +11,83 dB |

En smal tjock skärm ger alltså **högre** nivå än en tunn skärm i samma läge, och valet av viktigaste kant spelar stor
roll (i det här läget är den första kanten den som skärmar mest). Förklaringen ligger i formlerna: när R_M → 0 går den
korta kilens D̂ mot ett frekvensoberoende tal nära 2π/β (källa i kilens topp), och den långa kilen räknas som en
270°-kil med mottagaren längs ovansidan. `R_M = 0` ger `BullerFel`.

Den korta kilens gränsvärde mot exakt teori (punktkälla i toppen på hård kil: p = (2π/β)·exp(jkR)/R, härledning i
testfilens docstring), 20·lg(|D̂|·β/2π) för θ_R = 0 / 0,3 / 1,0 rad:

| β | 1,2π | 1,5π | 1,8π | 2π |
| --- | --- | --- | --- | --- |
| avvikelse | +1,08 / +0,98 / +0,21 dB | +1,20 / +1,17 / +0,87 dB | +0,56 / +0,57 / +0,64 dB | 0,00 / +0,02 / +0,18 dB |

Det är P-K1:s kilformel (närmeformen i ekv. 81 och 83) i gränsen k·R_S → 0, alltså metodens noggrannhet för en mycket
kort delsträcka; inget i P-K2 lägger till något. För en rätvinklig tjock skärm betyder det upp till 1,2 dB för hög
nivå när bredden är mycket mindre än våglängden.

### Reciprocitet

Ombytt källa och mottagare: kil 1 ↔ kil 2, θ_S ↔ β − θ_R, sträckor, gångtider och ytor byter plats, och
"forsta" ↔ "andra". 300 slumpade fall vardera (β i 1,02π–2π, 25 Hz–10 kHz, σ 10–20 000 kNs/m⁴ eller hård yta, τ och R
oberoende av varandra): största relativa skillnad 4,1·10⁻¹¹ (två kilar, impedans), 1,2·10⁻¹¹ (tjock skärm, impedans),
1,3·10⁻¹¹ (tjock skärm, givna Q i −0,369–1).

Reciprociteten håller bara om valet av viktigaste kant följer med i bytet. I spegelsymmetriska uppställningar ger de
två valen samma tryck (≤ 3·10⁻¹⁵). Annars skiljer de, "forsta" − "andra" vid 100 Hz / 1 kHz / 8 kHz:

| Uppställning | Skillnad |
| --- | --- |
| Två tunna skärmar, T₁ (20; 5), T₂ (40; 5), S (0; 1), R (60; 2) | −0,13 / −0,10 / −0,01 dB |
| Två tunna skärmar, T₁ (10; 4), T₂ (40; 6), S (0; 1), R (60; 2) | +0,07 / +0,03 / 0,00 dB |
| Tjock skärm 2 m, T₁ (20; 5), S (0; 1), R (60; 2) | −1,67 / −3,03 / −1,42 dB |
| Tjock skärm 10 m, T₁ (8; 5), S (0; 0,5), R (60; 4) | −1,41 / −1,69 / −1,10 dB |

För tjock skärm är valet alltså värt flera dB, och bytet av viktigaste kant i terrängtolkningen ger ett språng av den
storleken om inget i 5.21 jämnar ut det. Det är en fråga till P-T och P-5 (se nedan).

### Kontinuitet över skugggränserna

Punktlistornas byte av mätriktning (θ_1R mot T₂ eller R; θ_2S mot T₁ eller S) sker där R, T₂ och T₁ (eller S, T₁ och
T₂) ligger i linje, och där går också skugggränsen θ_S − θ_R = π i den ena kilen. Relativ ändring i p̂ mellan en
punkt 1 µm under och 1 µm över gränsen, 25 Hz / 250 Hz / 2 kHz / 10 kHz, lika för båda valen av viktigaste kant
inom 10 %:

| Gräns | Språng |
| --- | --- |
| Två tunna skärmar (σ = 200 och 12,5 på ytterytorna), mottagaren passerar linjen T₁–T₂ | 8,6·10⁻⁵ / 9,4·10⁻⁵ / 9,2·10⁻⁵ / 7,7·10⁻⁵ |
| Samma, källan passerar linjen T₂–T₁ | 8,7·10⁻⁵ / 9,5·10⁻⁵ / 9,9·10⁻⁵ / 1,1·10⁻⁴ |
| Tjock skärm 2 m (σ = 200 och 12,5), mottagaren passerar ovansidans plan (θ_1R: 0 → positiv) | 8,9·10⁻⁵ / 9,7·10⁻⁵ / 9,7·10⁻⁵ / 9,7·10⁻⁵ |
| Tjock hård skärm 2 m, källan passerar ovansidans plan (θ_2S: β₂ → mindre) | 9,3·10⁻⁵ / 9,6·10⁻⁵ / 9,6·10⁻⁵ / 9,5·10⁻⁵ |

Testets gräns är 2,5·10⁻⁴ (0,002 dB). Språnget är samma som P-K1 redovisar för en ensam kil och kommer av tabellernas
f(0) och g(0) ≠ ½. Ett svep av mottagaren 0–12 m i höjd i steg om 1 mm förbi båda gränserna (två tunna skärmar,
2 kHz) ändrar nivån högst 0,006 dB per steg.

### Övrigt

| Vad | Hur | Uppmätt |
| --- | --- | --- |
| Argumentlistorna i (97), (98), (102), (103) | `delkilar` mot handskrivna `Kil` med tolv olika tal | exakt |
| p̂ = D̂₁·D̂₂·exp(jωτ)/ℓ | D̂ räknad direkt med P-K1, τ ≠ ℓ/c | 10⁻¹⁴ |
| Tjock skärm = 0,5 × två kilar med Z_1R = Z_2S = ∞ | båda valen | D̂ bit för bit, p̂ 10⁻¹⁵ |
| Z = ∞ ≡ Q = 1 | två kilar och tjock skärm | bit för bit |
| Q_M går in i båda kilarna | mot två kilar med (Q_S, Q_M, Q_M, Q_R) | 10⁻¹⁵ |
| `BullerFel` | 5 slags ogiltig indata, okänt `viktigast`, 4 ogiltiga frekvenser, β₂ < π | – |

Inget räkneexempel för 5.7.3–5.7.4 finns i AV 1106/07 eller TC-101327.

## Jämförelselösningen i P-K1 (hårt halvplan) mot källa

Uppdrag: hitta en citerbar källa för den exakta lösningen i `tests/buller/nord2000/test_kildiffraktion.py`
(`macdonald_hart_halvplan`) och jämföra term för term.

**Resultat: en källa är funnen och det som går att läsa ur den stämmer med testets formel, men jämförelsen är inte
fullständig. Tre detaljer gick inte att läsa. Inget i P-K1:s filer är ändrat.**

Vad som gick att nå (endast genom textutdrag med WebFetch; nedladdning av PDF-filer nekas av sessionens proxy med 403,
så ingen sidbild har kunnat läsas):

| Källa | Länk | Utfall |
| --- | --- | --- |
| H. M. Macdonald, "A Class of Diffraction Problems", Proc. London Math. Soc. (2) 14 (1915) 410–427, DOI 10.1112/plms/s2_14.1.410 | https://zenodo.org/records/2451345 (CC0, PDF 706 kB) | Posten finns, men filen gick inte att läsa (binärt innehåll till WebFetch, förhandsvisningen spärrad, curl nekad). **Originalet är alltså fritt tillgängligt men inte läst.** |
| A. D. Rawlins, "Spherical wave diffraction by a rational wedge", Brunel University, Dept. of Mathematics and Statistics, rapport TR/09/86, juli 1986 | https://bura.brunel.ac.uk/bitstream/2438/2210/1/TR_09_86.pdf | Avsnitt 5 "Macdonald's solution for a half plane", ekv. (22)–(31), följt av meningen att resultatet stämmer med Macdonald (1915). Textutdraget är förvanskat; se tabellen nedan. |
| A. D. Rawlins, TR/08/86 (cylindervåg), TR/07/86 (plan våg), TR/14/86 (Greenfunktion) | bura.brunel.ac.uk/bitstream/2438/2240, /2263, /1883 | Inte punktkälla vid halvplan i den här formen. |
| D. Ouis, J. Sound Vib. 252 (2002) 191–221 | https://faculty.kfupm.edu.sa/are/djamel/Publications/hard_half_plane_useful.pdf | Utgår från Biot–Tolstoy i tidsplanet, inte Macdonalds formel. |
| Nethercote, Assier & Abrahams, "Analytical methods for perfect wedge diffraction: a review" (2019) | https://arxiv.org/pdf/1905.10562 | Bara linjekälla (Macdonald 1902). |
| Hewett & Morris (2014) | https://arxiv.org/pdf/1409.6131 | Citerar Macdonald 1915 men återger inte formeln. |
| "A review of commonly used analytical and empirical formulae for predicting sound diffracted by a thin screen", Applied Acoustics; Bowman, Senior & Uslenghi (1969) | sciencedirect.com/science/article/abs/pii/S0003682X04000842 | Inte nåbara (betalvägg respektive ingen nätutgåva funnen). |

Rawlins använder tidsfaktorn exp(iωt) och infallande fält exp(−ikR)/R; testet använder exp(+jkR)/R. Formlerna är
varandras komplexkonjugat, så H₁⁽²⁾ hos Rawlins motsvarar H₁⁽¹⁾ i testet.

| Term i testets formel | Fragment i utdraget ur Rawlins TR/09/86 | Bedömning |
| --- | --- | --- |
| Infallande fält exp(jkR)/R (utan 4π) | ekv. (3): "a spherical wave … ik e … R R (3)" med minustecken i exponenten | stämmer (konjugat) |
| R² = r² + r₀² − 2·r·r₀·cos ζ + z² | "cosrr2)zz(rr)(R 2 0 2 0 2 ζ−−++=ζ" | stämmer |
| Integrand H₁(k·R·cosh ξ), ordning 1 | ekv. (31): "d]cosh)(k[H … )2( … 1 R" | stämmer (H₁⁽²⁾ ↔ H₁⁽¹⁾) |
| Integrationsgränser −∞ till m | "∫ θξ ∞−" | stämmer |
| Faktor jk/2 framför integralen | "2 ik" | beloppet stämmer; **tecknet framför går inte att läsa** |
| m = asinh(2·√(r·r₀)·cos(ζ/2)/R) | ekv. (24): ")2/)cos((rr2 sinh)(" | faktorn 2, r·r₀, cos(·/2) och sinh finns; **rotens omfång och divisionen med R går inte att läsa** |
| Hård skärm: summan av termerna för φ − φ₀ och φ + φ₀ | ekv. (4)–(5) och (22): hård = summa, mjuk = skillnad enligt utdragets sammanfattning | stämmer till uppbyggnaden; **tecknet i själva ekv. (31) går inte att läsa** |
| Uppdelningen exp(jkR)/(2R) + (jk/2)·∫₀^m | finns inte i källan | följer av (jk/2)·∫₋∞⁰ = exp(jkR)/(2R); P-K1 har prövat identiteten numeriskt till 9 siffror |

De tre oläsliga detaljerna är var för sig bundna av annat än källan: tecknet framför integralen av att fältet långt
in i det belysta området (m → ∞) ska bli det infallande fältet; roten måste omfatta bara r·r₀, eftersom cos(ζ/2) är
negativ i skuggan; och summan för hård skärm av randvillkoret. P-K1 har dessutom prövat formeln numeriskt mot
Helmholtz ekvation, randvillkoret och källsingulariteten. Men det är inte detsamma som att ha läst dem i tryck.

**Slutsats:** P-K1:s förbehåll ("inte avläst ur källan") kan mildras till "stämmer med Rawlins TR/09/86 ekv. (24) och
(31) i allt som går att läsa i ett förvanskat textutdrag; tecken och rotens omfång inte bekräftade i tryck". För ett
rent besked behöver någon öppna Macdonalds artikel (Zenodo-länken ovan, s. 410–427) eller Rawlins rapport som PDF och
läsa de tre detaljerna. Det tar några minuter för en människa med webbläsare.

Osäkert i själva källhänvisningen: att Rawlins rapport senare trycktes som tidskriftsartikel har jag inte kontrollerat.

## Tolkningar

| Datum | Paket | Fråga | Val och skäl |
| --- | --- | --- | --- |
| 2026-11-21 | P-K2 | Ekv. (97)–(98), (102)–(103) är tryckta med exponenten j2ρfτ. | exp(jωτ) enligt TC-101327 2.4–2.5 (ρ är ett tryckfel för π; samma värde). τ är hela gångtiden enligt ekv. (95) resp. (100). |
| 2026-11-21 | P-K2 | Rubriken till TC-101327 2.5 lyder "Section 5.7.4 Diffraction of Two Wedge-Shaped Screens". | Läst som 5.7.4 (tjock skärm): ekvationsnumren (102)–(103) finns bara där. |
| 2026-11-21 | P-K2 | Punktlistan i 5.7.3 säger om θ_2S: "Otherwise θ_2R is the angle … and the line T₂S". Punktlistan i 5.7.4 säger: "then θ_1S = β₂. Otherwise θ_1S is the angle … and the line T₂S". | Båda läses som θ_2S (vinkeln mot källsidan i den andra kilen). θ_2R och θ_1S har egna punkter i samma listor, och AV 1849/00 4.4.3.2 har θ_S2 på motsvarande ställe. Rör bara beskrivningen av indata; delmodell 5 räknar vinkeln i ekv. (195). |
| 2026-11-21 | P-K2 | Ska D̂₁ och D̂₂ innehålla direkt och speglad stråle (ekv. 87–90) eller bara diffraktionstermen (78)? | Hela pwedge, eftersom Dwedge definieras genom pwedge i ekv. (93)–(94) och AV 1849/00 ekv. (42)–(43) räknar D ur "p calculated … for a single edge". Det är också det som gör fältet kontinuerligt när en kant passerar siktlinjen och som ger en ensam kil i gränsen (uppmätt ovan). Alternativ: bara p_diffr; då försvinner fältet när den mindre viktiga kanten inte skärmar. |
| 2026-11-21 | P-K2 | Vilken kant är den viktigaste? | Indata (`viktigast`), utan förval. 5.7.3 hänvisar till 5.21. Okänt värde ger `BullerFel`. |
| 2026-11-21 | P-K2 | Vinklarna θ_1R och θ_2S beror på om den andra kanten ligger över siktlinjen. | Anroparen räknar dem (som i ekv. 99 och 104, där vinklarna är argument). Modulen kontrollerar inte regeln, eftersom den inte har koordinaterna. Testfilen har en hjälpfunktion för räta strålar som följer punktlistorna. |
| 2026-11-21 | P-K2 | Bredden R_M = 0 (sammanfallande kanter). | `BullerFel`. Formlerna har ett ändligt gränsvärde, men det är inte tunn skärm (uppmätt +3,5 till +11,8 dB vid 1 mm), och metoden gör övergången med r_scr12. Alternativ: returnera gränsvärdet. |
| 2026-11-21 | P-K2 | TC-101327 2.3 och G.3: hur går ovansidans koefficient in när kilytorna ges som Q? | `q_mitten` är Q_R i den första kilen och Q_S i den andra; faktorn 0,5 behålls (G.3 säger att Q_avR,1 och Q_avS,2 är samma tal Q_avM och hänvisar till ekv. 102 för 0,5). Med `q_mitten = 1` fås ekv. (102)–(103) som tryckta. |
| 2026-11-21 | P-K2 | Vilken koefficient får den speglade strålen (ekv. 89–90) i varianterna med givna Q? | Samma som i diffraktionstermen, alltså P-K1:s förval (P-K1 tolkning 5, öppen fråga till P-4). Den som behöver annat använder `dubbel_diffraktion` med egna koefficientfunktioner. |

Noterat utan val: i ekv. (80) räknas Q med gångtiden τ_S + τ_R för den kil som räknas, alltså med olika gångtid för
samma yta beroende på vilken kant som är viktigast. Implementerat bokstavligt (genom P-K1).

## Önskade ändringar i delade filer

Inga. De nio raderna under "Tolkningar" ska föras in i `docs/buller/tolkningar.md`. I `docs/buller/B4-uppdelning.md`
avsnitt 2 kan raderna "Två kilar" och "Tjock skärm" få status klar och modul `kildiffraktion_dubbel.py`.

`STATUS.md` och `PLAN.md` är inte rörda (ägs av samordnaren enligt B00 regel 7 och arbetsordern). Förslag till post:

```
## 2026-11-21 – B4/P-K2 – Dubbel diffraktion (AV 1106/07 5.7.3–5.7.4, TC-101327 2.4–2.5)
Gjort: p2wedge och p2edge med impedans eller givna Q (TC 2.3, G.3–G.4), val av viktigaste kant som indata.
Mätt: 29 tester gröna. Andra kanten 8 m under siktlinjen: 0,7 % (8 kHz) – 5 % (100 Hz) från ensam kil; på
  siktlinjen −5,96 dB (teori −6,02). Reciprocitet 4·10⁻¹¹. Språng över skugggränser ≤ 1,1·10⁻⁴ relativt.
  Tjock skärm med bredd → 0 går inte mot tunn skärm (+3,5 till +11,8 dB vid 1 mm); r_scr12 sköter övergången.
Öppet: val av viktigaste kant är värt 1–3 dB för tjock skärm; Q för speglad stråle (P-4); Macdonald-referensen i
  P-K1 bekräftad till uppbyggnaden mot Rawlins TR/09/86 men inte läst i tryck (tre detaljer oläsliga).
Nästa: P-5, P-6.
```

## Öppna frågor till andra paket

- **P-T, P-5:** för tjock skärm skiljer ekv. (102) och (103) 1–3 dB i vanliga lägen. Läs 5.21.3 med frågan hur den
  viktigaste kanten väljs och om något jämnar ut bytet; annars blir nivån språngvis när en källa rör sig förbi läget
  där kanterna är lika viktiga.
- **P-5:** en smal tjock skärm ger högre nivå än en tunn (tabellen ovan). Det ska tas om hand av r_scr12; pröva att
  ΔL_scr1 i ekv. (315) går jämnt mot delmodell 4 när bredden krymper.
- **P-5, P-6:** vinklarna i ekv. (194)–(195) kan för spegelkälla och spegelmottagare hamna utanför
  0 ≤ θ_R ≤ θ_S ≤ β; P-K1:s schema tar hand om det, och θ_R > θ_S efter schemat ger `BullerFel`.
- **P-4 (ärvd från P-K1):** koefficienten för den speglade strålen när kilytorna ges som Q_av.

## Osäkert

- Inget oberoende facit finns för dubbel diffraktion med ändlig impedans eller för tjock skärm som helhet: där är
  bara uppbyggnaden ur Dwedge, gränsfallen, reciprociteten och kontinuiteten prövade. Första prövningen mot tal blir
  testfall 81–84 och 91–94 genom P-5 och P-6.
- Tolkningen att Dwedge innehåller direkt och speglad stråle vilar på ekv. (93) och AV 1849/00; den stöds av
  gränsfallen men är inte uttalad i 5.7.3–5.7.4.
- Bilaga G.3–G.4 är läst bara i textutdrag (inte sidbild) för att bestämma gränssnittet (vilka ytor som får
  vilken koefficient). Formlerna där är inte implementerade eller kontrollerade.
- Refraktion: funktionerna tar gångtider och sträckor per delsträcka som indata och är bara prövade med τ = R/c,
  utom att sammansättningen (ekv. 97–98) är prövad med τ oberoende av R.
- Jämförelselösningen i P-K1: se avsnittet ovan. Delvis bekräftad, inte fullt.

## Återstår

Inget inom paketets avgränsning. Utanför: bilaga G (P-5, P-6), delmodell 5 och 6, inkoppling i `karna.py` (P-S).
