# P-S1 – Sammansättning steg 1: plan mark med väder

Datum: 2026-11-21. Paket P-S1 (samordnarens uppdrag; läst: `P-R2.md`, `P-8.md`, `P-R1.md` och regeln sist i
`docs/buller/tolkningar.md`).

**Läget i korthet.** Plan mark med väder stämmer nu med testfallen. Grupp N: **21 godkända** (blad 1–13, 15–18,
21–24; förut 10), **1 ska utredas** (blad 14; förut 4), **0 underkända** (förut 2), 58 ej körbara (förut 64; ingen
vägras längre för delmodell 8), 4 ej facit. Det som ändrade utfallet:

1. Delmodell 8 är inkopplad (ekv. 332). Blad 9, 12, 13, 14, 16, 22 räknas.
2. Fem förval är bytta enligt regeln i `tolkningar.md`, alla märkta **avgjord av testfall** nedan: skuggzonens
   avgörande (`delat`), vikterna i delmodell 2 i skuggzon (`ovre_band`), uppåtbrytning (`exakt`), Fresnelzonen i
   delmodell 2 (`klippt`) och luftabsorptionens bandformel utanför sitt maximum (`monoton`). Inget tal är justerat.
3. 10 kHz-bandet: orsaken är hittad. Ekv. (287) ger för de yttersta källpunkterna (89°, 5,5 km) nästan ingen
   luftabsorption vid 10 kHz (2,9 dB i stället för över 146 dB), eftersom formeln avtar över A₀ = 315 dB.
4. Det som återstår i banden 800–6300 Hz (upp till 1,0 dB, alla blad) är inte utbredning: det är källmodellens
   horisontella direktivitet (SP 2006:12 ekv. 2.12), som inte är införd i `buller.emission`. Med den pålagd utanför
   repot ligger alla 20 blad med personbilar inom 0,06 dB i totalnivå; se avsnitt 5.

`nord2000_konform` är fortfarande `False`. Med P-R2:s läsningar valda (`uppat="bokstavlig"`,
`fresnelzon="oklippt"`, `skuggzonsvikter="hog"`, `Skuggzonslasning(avgorande="per_band")`,
`luftabsorption="tryckt"`) ger kärnan bit för bit P-R2:s resultat plus ΔL₈ (prövat på alla blad, skillnad 0,0 dB).

Ägda och ändrade filer: `src/buller/nord2000/karna.py`, `refraktion_mark.py`, `delmodell1.py`, `delmodell2.py`,
`fresnel.py`; `tests/buller/test_testsvit_n.py`, `tests/buller/nord2000/test_refraktion_mark.py`,
`tests/buller/nord2000/test_terrang_karna.py`; denna logg. `delmodell8.py`, `atmosfar.py`, `fasdiff.py`,
`koherens.py`, `luftabsorption.py`, `stralar_krokta.py`, `delmodell5.py`, `STATUS.md` och `PLAN.md` är orörda.

Underlag, avläst från sidbilder i `/home/claude/verktyg/sidor/P-S1/`: AV 1106/07 s. 25–26 (5.5.3), 29–32
(5.5.4–5.5.6), 50 (5.8), 54–58 (5.10–5.11), 124 (ekv. 287), 148–151 (5.23.6–5.23.7), 155–160 (5.23.14, 5.23.16);
TC-101327 s. 4–13; AV 1851/00 s. 23–25, 41–42; dessutom P-8:s bilder s. 120 och 144 (ekv. 279, 332). Textutdrag:
AV 1851/00 avsnitt 2.2–3 och 6.1, 6.12; AV 1849/00 avsnitt 3; SP 2006:12 avsnitt 2.5; RL 18/18 kapitel 2.

## 1. Delmodell 8 inkopplad

**Ändrat.** `Nord2000Karna.berakna` anropar `delmodell8.flera_markreflexer(frekvenser, profil, atmosfar, summering)`
och lägger ΔL₈ till ΔL_t med ⊕ (`delmodell8.energisumma_db`), ytterst och per band, som ekv. (332) anger (avläst
s. 144). A och B är medelvärdena. Valet `delmodell8` är nu `"hog"` (ΔL_multi,hf, förval, metodens rekommendation),
`"lag"` (ΔL_multi,lf) eller `"utelamna"` (utan ΔL₈, för utredning); `"vagra"` finns inte längre.
`Nord2000Delresultat` har fått `antal_stralar` (N) och `flera_markreflexer_db` (ΔL₈, `None` när N ≤ 4). Kärnans
version är 0.3.0. Anropet görs bara när A eller B är skild från 0; för N ≤ 4 är resultatet bit för bit som utan ΔL₈
(test).

**Dubbleringen borttagen.** `krokningsradie_m`, `antal_stralar_flera_markreflexer` och `hojd_cirkelstrale` fanns i
både `refraktion_mark.py` och `delmodell8.py`. Före borttagandet jämfördes de (skript
`/home/claude/verktyg/ps1/likvardighet.py` och ett test som kördes grönt med båda på plats):

| Storhet | Fall | Största skillnad |
| --- | --- | --- |
| R_A,B (ekv. 54) | 20 000 slumpade väder och avstånd | 0 (relativt) |
| N (ekv. 279, 284) | samma | 4,9·10⁻¹⁶ (relativt); 0 i båda när ingen nedåtbrytning |
| h₀ (HeightOfCircularRay) | 50 000 slumpade strålar, \|ξ\| från 10⁻⁷ till 0,03 1/m | 4,9·10⁻⁸ m |
| R₁, R₂ | samma | 4,9·10⁻⁸ m |

Skillnaden i h₀ kommer av utsläckningen i ekv. (363)–(367) för små ξ, som `delmodell8.py` undviker. De är alltså
likvärdiga, och `refraktion_mark.py` importerar nu `delmodell8.hojd_cirkelstrale`; de två andra användes bara av
kärnan. De borttagna funktionerna, som följde ekvationerna rakt av, ligger kvar i testfilen som oberoende räkneväg
(`TestEnKallaMedDelmodell8`). Verkan av bytet på blad 10, 15, 23 (de enda som använder strålhöjden): högst
9·10⁻¹⁴ dB.

**Utfall för blad 9, 12, 13, 14, 16, 22.** Mellankolumnen är läget direkt efter inkopplingen, med P-R2:s förval i
övrigt. Format: utfall: avvikelse i L_AE; största tersband (Hz); antal band över 1 dB.

| Blad | före | bara ΔL₈ inkopplad | efter hela paketet |
| --- | --- | --- | --- |
| 9 (dt/dz = +0,1) | ej körbar | ska utredas: −0,47; +1,50 (10 k); 1 | godkänd: −0,41; −0,87 (800); 0 |
| 12 (u = 3, φ = 0) | ej körbar | ska utredas: −0,33; +2,61 (10 k); 1 | godkänd: −0,20; +0,41 (6,3 k); 0 |
| 13 (φ = 45) | ej körbar | ska utredas: −0,89; −1,53 (800); 3 | godkänd: −0,46; −0,87 (800); 0 |
| 14 (φ = 90) | ej körbar | underkänd: −1,25; −1,87 (800); 6 | ska utredas: −0,73; −1,03 (800); 1 |
| 16 (u = 5, 1000 m) | ej körbar | ej körbar (ekv. 287: A₀ = 1013 dB vid 3,15 kHz) | godkänd: +0,38; −0,91 (10 k); 0 |
| 22 (blandad mark, φ = 0) | ej körbar | ska utredas: −0,37; +2,08 (10 k); 1 | godkänd: −0,24; +0,42 (6,3 k); 0 |

Det som skiljer mellankolumnen från den sista är Fresnelzonens klippning (25–630 Hz, avsnitt 2.3 D) och
luftabsorptionen (10 kHz, avsnitt 3). Per tersband, med de tre valen för ΔL₈ (övriga förval som efter paketet):


**Blad 9** (beräknat − facit, dB)

| Band (Hz) | hf (förval) | lf | utan ΔL₈ |
| --- | --- | --- | --- |
| 25 | −0,00 | +8,16 | −0,16 |
| 31,5 | +0,00 | +6,93 | −0,13 |
| 40 | +0,00 | +5,76 | −0,10 |
| 50 | +0,02 | +4,69 | −0,07 |
| 63 | +0,02 | +3,72 | −0,05 |
| 80 | +0,02 | +2,90 | −0,04 |
| 100 | +0,03 | +2,26 | −0,02 |
| 125 | +0,02 | +1,75 | −0,02 |
| 160 | +0,02 | +1,41 | −0,01 |
| 200 | −0,00 | +1,22 | −0,04 |
| 250 | −0,00 | +1,29 | −0,06 |
| 315 | +0,00 | +1,69 | −0,12 |
| 400 | +0,01 | +2,20 | −0,25 |
| 500 | +0,01 | +1,78 | −0,28 |
| 630 | +0,01 | +1,66 | −0,34 |
| 800 | −0,87 | +0,66 | −1,24 |
| 1 k | −0,83 | +0,65 | −1,19 |
| 1,25 k | −0,71 | +0,83 | −1,08 |
| 1,6 k | −0,58 | +0,91 | −0,93 |
| 2 k | −0,38 | +0,96 | −0,68 |
| 2,5 k | −0,21 | +0,80 | −0,43 |
| 3,15 k | −0,06 | +0,49 | −0,18 |
| 4 k | −0,00 | +0,23 | −0,05 |
| 5 k | +0,02 | +0,08 | +0,00 |
| 6,3 k | +0,01 | +0,02 | +0,00 |
| 8 k | −0,04 | −0,04 | −0,04 |
| 10 k | −0,05 | −0,05 | −0,05 |
| **L_AE** | **−0,41** | **+1,08** | **−0,65** |
| utfall | godkänd | underkänd | ska utredas |

**Blad 12** (beräknat − facit, dB)

| Band (Hz) | hf (förval) | lf | utan ΔL₈ |
| --- | --- | --- | --- |
| 25 | −0,00 | +0,11 | −0,05 |
| 31,5 | −0,00 | +0,09 | −0,04 |
| 40 | −0,00 | +0,07 | −0,03 |
| 50 | +0,02 | +0,07 | −0,01 |
| 63 | +0,01 | +0,07 | −0,01 |
| 80 | +0,01 | +0,07 | −0,01 |
| 100 | +0,03 | +0,09 | +0,01 |
| 125 | +0,02 | +0,10 | −0,00 |
| 160 | +0,02 | +0,14 | −0,01 |
| 200 | −0,02 | +0,21 | −0,06 |
| 250 | −0,03 | +0,47 | −0,11 |
| 315 | −0,03 | +1,06 | −0,24 |
| 400 | −0,01 | +1,77 | −0,40 |
| 500 | +0,01 | +2,18 | −0,53 |
| 630 | +0,04 | +1,87 | −0,42 |
| 800 | −0,26 | +0,96 | −0,57 |
| 1 k | −0,26 | +0,57 | −0,48 |
| 1,25 k | −0,24 | +0,34 | −0,41 |
| 1,6 k | −0,23 | +0,18 | −0,36 |
| 2 k | −0,21 | +0,08 | −0,31 |
| 2,5 k | −0,16 | +0,04 | −0,24 |
| 3,15 k | −0,06 | +0,07 | −0,13 |
| 4 k | +0,06 | +0,14 | −0,00 |
| 5 k | +0,22 | +0,27 | +0,17 |
| 6,3 k | +0,41 | +0,44 | +0,38 |
| 8 k | +0,00 | +0,01 | −0,03 |
| 10 k | +0,00 | +0,01 | −0,03 |
| **L_AE** | **−0,20** | **+0,27** | **−0,34** |
| utfall | godkänd | ska utredas | godkänd |

**Blad 13** (beräknat − facit, dB)

| Band (Hz) | hf (förval) | lf | utan ΔL₈ |
| --- | --- | --- | --- |
| 25 | −0,00 | +1,04 | −0,08 |
| 31,5 | −0,00 | +0,82 | −0,07 |
| 40 | −0,00 | +0,63 | −0,06 |
| 50 | +0,02 | +0,49 | −0,03 |
| 63 | +0,02 | +0,37 | −0,02 |
| 80 | +0,02 | +0,28 | −0,01 |
| 100 | +0,04 | +0,23 | +0,01 |
| 125 | +0,03 | +0,18 | +0,01 |
| 160 | +0,03 | +0,16 | +0,01 |
| 200 | +0,01 | +0,13 | −0,02 |
| 250 | +0,01 | +0,15 | −0,03 |
| 315 | −0,01 | +0,19 | −0,05 |
| 400 | −0,01 | +0,22 | −0,07 |
| 500 | −0,00 | +0,19 | −0,05 |
| 630 | +0,02 | +0,20 | −0,02 |
| 800 | −0,87 | −0,70 | −0,91 |
| 1 k | −0,73 | −0,57 | −0,77 |
| 1,25 k | −0,59 | −0,41 | −0,63 |
| 1,6 k | −0,46 | −0,27 | −0,50 |
| 2 k | −0,33 | −0,14 | −0,37 |
| 2,5 k | −0,22 | −0,04 | −0,26 |
| 3,15 k | −0,09 | +0,08 | −0,13 |
| 4 k | +0,04 | +0,18 | −0,01 |
| 5 k | +0,18 | +0,29 | +0,14 |
| 6,3 k | +0,32 | +0,39 | +0,28 |
| 8 k | +0,00 | +0,04 | −0,03 |
| 10 k | +0,00 | +0,02 | −0,02 |
| **L_AE** | **−0,46** | **−0,28** | **−0,50** |
| utfall | godkänd | ska utredas | godkänd |

**Blad 14** (beräknat − facit, dB)

| Band (Hz) | hf (förval) | lf | utan ΔL₈ |
| --- | --- | --- | --- |
| 25 | −0,00 | +1,34 | −0,08 |
| 31,5 | −0,00 | +1,06 | −0,07 |
| 40 | −0,00 | +0,82 | −0,06 |
| 50 | +0,02 | +0,64 | −0,03 |
| 63 | +0,02 | +0,49 | −0,02 |
| 80 | +0,02 | +0,37 | −0,01 |
| 100 | +0,04 | +0,30 | +0,01 |
| 125 | +0,03 | +0,23 | +0,01 |
| 160 | +0,04 | +0,19 | +0,02 |
| 200 | +0,01 | +0,16 | −0,01 |
| 250 | +0,01 | +0,18 | −0,02 |
| 315 | −0,01 | +0,22 | −0,05 |
| 400 | −0,03 | +0,22 | −0,08 |
| 500 | −0,00 | +0,19 | −0,04 |
| 630 | +0,02 | +0,20 | −0,02 |
| 800 | −1,03 | −0,86 | −1,07 |
| 1 k | −0,97 | −0,79 | −1,01 |
| 1,25 k | −0,87 | −0,66 | −0,91 |
| 1,6 k | −0,77 | −0,54 | −0,82 |
| 2 k | −0,66 | −0,41 | −0,71 |
| 2,5 k | −0,56 | −0,32 | −0,62 |
| 3,15 k | −0,42 | −0,19 | −0,48 |
| 4 k | −0,31 | −0,11 | −0,37 |
| 5 k | −0,20 | −0,05 | −0,25 |
| 6,3 k | −0,09 | +0,01 | −0,13 |
| 8 k | +0,00 | +0,06 | −0,02 |
| 10 k | +0,00 | +0,03 | −0,02 |
| **L_AE** | **−0,73** | **−0,51** | **−0,77** |
| utfall | ska utredas | ska utredas | ska utredas |

**Blad 16** (beräknat − facit, dB)

| Band (Hz) | hf (förval) | lf | utan ΔL₈ |
| --- | --- | --- | --- |
| 25 | −0,01 | +7,89 | −0,57 |
| 31,5 | −0,00 | +6,91 | −0,46 |
| 40 | +0,01 | +5,91 | −0,36 |
| 50 | +0,03 | +4,93 | −0,27 |
| 63 | +0,04 | +4,01 | −0,20 |
| 80 | +0,04 | +3,17 | −0,15 |
| 100 | +0,04 | +2,42 | −0,12 |
| 125 | +0,02 | +1,73 | −0,10 |
| 160 | +0,01 | +1,29 | −0,09 |
| 200 | −0,04 | +0,98 | −0,12 |
| 250 | −0,20 | +0,76 | −0,29 |
| 315 | −0,46 | +0,31 | −0,54 |
| 400 | −0,35 | +0,05 | −0,39 |
| 500 | +0,01 | +0,17 | −0,00 |
| 630 | +0,18 | +0,29 | +0,16 |
| 800 | +0,46 | +0,57 | +0,44 |
| 1 k | +0,27 | +0,40 | +0,25 |
| 1,25 k | +0,72 | +0,86 | +0,71 |
| 1,6 k | +0,49 | +0,69 | +0,47 |
| 2 k | +0,78 | +1,03 | +0,76 |
| 2,5 k | +0,82 | +1,18 | +0,79 |
| 3,15 k | +0,86 | +1,38 | +0,82 |
| 4 k | +0,88 | +1,64 | +0,83 |
| 5 k | +0,78 | +1,93 | +0,70 |
| 6,3 k | +0,42 | +2,19 | +0,30 |
| 8 k | −0,61 | +1,93 | −0,80 |
| 10 k | −0,91 | +2,13 | −1,14 |
| **L_AE** | **+0,38** | **+0,64** | **+0,36** |
| utfall | godkänd | ska utredas | ska utredas |

**Blad 22** (beräknat − facit, dB)

| Band (Hz) | hf (förval) | lf | utan ΔL₈ |
| --- | --- | --- | --- |
| 25 | −0,00 | +0,23 | −0,07 |
| 31,5 | −0,00 | +0,20 | −0,06 |
| 40 | −0,00 | +0,18 | −0,05 |
| 50 | +0,02 | +0,18 | −0,03 |
| 63 | +0,02 | +0,17 | −0,03 |
| 80 | +0,02 | +0,17 | −0,03 |
| 100 | +0,03 | +0,20 | −0,01 |
| 125 | +0,02 | +0,21 | −0,02 |
| 160 | +0,02 | +0,24 | −0,02 |
| 200 | −0,01 | +0,28 | −0,07 |
| 250 | −0,01 | +0,42 | −0,11 |
| 315 | −0,02 | +0,78 | −0,21 |
| 400 | −0,02 | +1,64 | −0,48 |
| 500 | −0,00 | +2,75 | −0,99 |
| 630 | +0,01 | +2,60 | −0,97 |
| 800 | −0,39 | +1,20 | −0,93 |
| 1 k | −0,36 | +0,62 | −0,69 |
| 1,25 k | −0,32 | +0,32 | −0,55 |
| 1,6 k | −0,28 | +0,14 | −0,45 |
| 2 k | −0,23 | +0,05 | −0,35 |
| 2,5 k | −0,16 | +0,02 | −0,25 |
| 3,15 k | −0,06 | +0,04 | −0,13 |
| 4 k | +0,05 | +0,12 | −0,00 |
| 5 k | +0,23 | +0,27 | +0,18 |
| 6,3 k | +0,42 | +0,44 | +0,38 |
| 8 k | +0,00 | +0,01 | −0,03 |
| 10 k | +0,00 | +0,00 | −0,02 |
| **L_AE** | **−0,24** | **+0,27** | **−0,42** |
| utfall | godkänd | ska utredas | godkänd |

**hf eller lf (P-8 tolkning 7).** Alla sex bladen skiljer läsningarna åt:

| Blad | hf (förval) | lf | utan ΔL₈ |
| --- | --- | --- | --- |
| 9 | godkänd: −0,41; −0,87 (800); 0 | underkänd: +1,08; +8,16 (25); 15 | ska utredas: −0,65; −1,24 (800); 3 |
| 12 | godkänd: −0,20; +0,41 (6,3 k); 0 | ska utredas: +0,27; +2,18 (500); 4 | godkänd: −0,34; −0,57 (800); 0 |
| 13 | godkänd: −0,46; −0,87 (800); 0 | ska utredas: −0,28; +1,04 (25); 1 | godkänd: −0,50; −0,91 (800); 0 |
| 14 | ska utredas: −0,73; −1,03 (800); 1 | ska utredas: −0,51; +1,34 (25); 2 | ska utredas: −0,77; −1,07 (800); 2 |
| 16 | godkänd: +0,38; −0,91 (10 k); 0 | ska utredas: +0,64; +7,89 (25); 17 | ska utredas: +0,36; −1,14 (10 k); 1 |
| 22 | godkänd: −0,24; +0,42 (6,3 k); 0 | ska utredas: +0,27; +2,75 (500); 4 | godkänd: −0,42; −0,99 (500); 0 |

Med lf blir blad 9 underkänt (+8,2 dB vid 25 Hz) och blad 12, 13, 16 och 22 "ska utredas" (blad 16: +7,9 dB vid
25 Hz). Förvalet hf var valt ur texten (rekommendationen för normalt bruk) och bekräftas av alla sex.
ΔL₈ behövs: utan den blir blad 9 "ska utredas" (L_AE −0,65 dB) och blad 16 "ska utredas" (−0,57 dB vid 25 Hz,
−1,14 dB vid 10 kHz). Största verkan av ΔL₈ (hf) på ett tersband: 0,37 dB (blad 9), 0,54 (12), 0,08 (13), 0,08 (14),
0,56 (16) och 0,99 dB (22).

## 2. Skuggzonen

### 2.1 Vad dokumenten säger, ställe för ställe

Skuggzonsavsnitten är lästa om från sidbild: AV 1106/07 avsnitt 5.5.3–5.5.6, 5.10, 5.11, 5.23.6, 5.23.14, 5.23.16;
TC-101327 avsnitt 2.1, 2.2, 2.6, 2.13; AV 1851/00 avsnitt 2.2–2.3, 3, 6.1 (s. 22–25), 6.12 och bilaga A.

| Nr | Ställe | AV 1106/07 (tryckt) | TC-101327 | AV 1851/00 | P-R2 gjorde | Läsning texten stöder bäst | Förval nu |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Q i ekv. (121) | Q(f, 0, 0, Ẑ_G): gångtid 0, vinkel 0 | 2.6 lägger till 10·lg, rör inte Q | ekv. (39): Q med ψ_G = 0 och τ₂ = τ₁, väglängden R₁ | τ₁ (AV 1851/00) | τ₁: med gångtid 0 är Q = 1 för all mark, och ekv. (121) blir inte gränsvärdet av ekv. (120) | τ₁ (oförändrat) |
| 2 | Nivån i ekv. (387) | 10·log(2·R_SR·\|pwedge\|) | nämner inte ekv. (387) | ekv. (42): 20·log(2·\|D_SZ\|·R_SR/R_SZ) | 20·lg | 20·lg: argumentet är ett tryck | 20·lg (oförändrat) |
| 3 | Kilens ytor i ekv. (387) | pwedge utan impedanser; avsnitt 5.7.5 säger själv att skuggzonsavskärmningen räknas med icke-reflekterande kil | – | avsnitt 6.1: helt absorberande ytor | pwedge0 | pwedge0 (båda dokumenten säger det; ingen avvikelse från AV 1106/07) | oförändrat |
| 4 | d_SZ i ShadowZoneShielding (kilens topp) | i ord: "defined in Section 5.5.4" (ekv. 43, avståndet källa–mottagare vid skuggzonens gräns); figur 41–42: avståndet från källan till strålens lägsta punkt | 2.2 ny ekv. (43), nämner inte 5.23.16 | ekv. (41): d·√h_S/(√h_S + √h_R) | AV 1851/00 ekv. (41) | ekv. (41): figurerna visar den punkten, och AV 1106/07 ekv. (347) har samma uttryck för d_refl i skuggzon; med ekv. (43) är d − d_SZ negativ mellan 0,95·d_SZ och d_SZ | ekv. (41) (oförändrat) |
| 5 | h_SZ när strålen inte når marken | ekv. (386) ger strålens höjd; figur 42 visar djupet under marken | – | "vertical distance" mellan mark och stråle | h_SZ = −h₀, och ΔL_SZ = 0 när h_SZ ≤ 0 | utfyllnad: texten täcker inte fallet; 0 dB är gränsvärdet för platt kil | oförändrat |
| 6 | Vilken profil avgör att mottagaren ligger i skuggzon | 5.10: villkoret skrivs med ξ och d_SZ ur DirectRay; texten före ekv. (116) säger att ξ, c₀, τ, R, ψ_G, Δτ är frekvensberoende (d_SZ nämns inte); ekv. (122): ΔL_SZ räknas med den frekvensoberoende profilen; ekv. (385): ξ_SZ(f) tonar från 0 vid 20 Hz till ξ vid 2 kHz | – | avsnitt 6.1: ΔL = ΔL_G + ΔL_SZ, uttryckligen för att undvika språng vid skuggzonens kant; ingen frekvensberoende ξ_SZ | per band, allt med ξ(f) | se 2.3 A | **`delat`** |
| 7 | Negativ Δτ i läget `bokstavlig` | 5.9.2: Δτ ≥ 0 | – | – | sätts till 0 | utfyllnad; uppstår inte i läget `exakt` | oförändrat |
| 8 | R₀ i ekv. (347) | (d/(2·(√h_S + √h_R))) inom parentes utan exponent; inte en längd | 2.13: arccos-argument under −1 sätts till −1 "for high values of h_S or h_R in proportion to d" | ekvationen finns inte | d²/(2·(√h_S + √h_R)²) | den geometriska: bara den gör bågarna förenliga med d_refl (R_S + R_R ≈ d) | geometrisk (oförändrat) |
| 9 | Vikterna i delmodell 2 i skuggzon (f_L, f_H) | PhaseDiffFreq med h̆_S = h̆_R = 0 och ψ_G = 0 ger Δα = π i alla band; ekv. (380) blir 0/0; avsnitt 5.23.14 täcker inte fallet | – | 6.1: Δα räknas med ω·(τ₂ − τ₁) och ℜ_p vid den modifierade vinkeln (samma 0/0) | `hog` (w_H i alla band) | texten avgör inte | **`ovre_band`**, se 2.3 B |
| 10 | Uppåtbrytning: \|ξ′\| eller exakt båge | 5.5.4: ekv. (32)–(41) med \|ξ′\|, teckenbyte på Δθ | 2.2: ekv. (43) med \|ξ′\| | 2.1: samma föreskrift | `bokstavlig` | texten säger `bokstavlig` uttryckligen | **`exakt`**, se 2.3 C |
| 11 | Fresnelzonens utsträckning | 5.8 (s. 50): bara delen mellan källa och mottagare tas med; ekv. (349)–(351) har ingen begränsning | 2.13 rör inte detta | AV 1849/00 4.2: kapningen är tillåten | `oklippt` (samordnarens besked till P-R2) | 5.8 är en uttrycklig definition | **`klippt`**, se 2.3 D |

Utöver de tre ställen P-R2 själv pekade ut (nr 1, 2, 4) och de två som uppdraget nämner (nr 9, 10) hör alltså nr 5,
6, 7, 8 och 11 hit. Nr 3 visade sig inte vara en avvikelse: AV 1106/07 avsnitt 5.7.5 säger samma sak som AV 1851/00.

Om nr 8: TC 2.13 talar om höga h i förhållande till d. Med den geometriska radien blir arccos-argumentet under −1
först när h_S·(1 + √(h_R/h_S)) > d, vilket inte går ihop med skuggzon; med den tryckta parentesen (utan eller med
kvadrat) händer det redan vid måttliga höjder. Det talar för att referensprogrammet räknar med något av de tryckta
uttrycken, men testfallen säger tvärtom (2.2): R₀ tryckt ger +7,9 dB i blad 23.

### 2.2 Utfall för blad 10, 15 och 23 med var och en av läsningarna

Varje rad ändrar **en** läsning från de nya förvalen. Format: utfall: avvikelse i L_AE; största tersband (Hz); antal
band över 1 dB. Köraren `buller.testfall.korare.kor_alla`, källmodell `Vagemission`.

| Läsning | Blad 10 | Blad 15 | Blad 23 |
| --- | --- | --- | --- |
| förval | godkänd: +0,14; +0,83 (1,25 k); 0 | godkänd: −0,01; +0,05 (8 k); 0 | godkänd: +0,01; +0,33 (800); 0 |
| avgörande per band | godkänd: +0,18; +0,83 (1 k); 0 | underkänd: +1,12; +5,49 (800); 8 | ska utredas: +0,87; +3,27 (800); 7 |
| avgörande oberoende | godkänd: +0,37; +0,73 (1,25 k); 0 | underkänd: +1,08; +4,00 (315); 7 | underkänd: +1,02; +3,12 (400); 6 |
| vikter lag | godkänd: +0,14; +0,83 (1,25 k); 0 | godkänd: −0,01; −0,19 (10 k); 0 | godkänd: +0,01; −0,41 (10 k); 0 |
| vikter hog | ska utredas: +0,44; +2,19 (800); 5 | ska utredas: +0,78; +3,60 (500); 8 | ska utredas: +0,93; +2,83 (1 k); 12 |
| uppåt bokstavlig | ska utredas: +0,05; −1,32 (8 k); 4 | godkänd: −0,02; −0,07 (3,15 k); 0 | godkänd: +0,01; +0,37 (800); 0 |
| Q med gångtid 0 | underkänd: +9,99; +18,08 (2,5 k); 12 | underkänd: +13,42; +21,79 (1 k); 12 | underkänd: +12,96; +22,36 (1,25 k); 12 |
| 10·lg | godkänd: +0,16; +0,83 (1 k); 0 | underkänd: +2,13; +7,51 (10 k); 15 | underkänd: +1,32; +7,54 (10 k); 15 |
| kiltopp ekv. 43 | godkänd: +0,15; +0,83 (1 k); 0 | godkänd: −0,05; −0,58 (630); 0 | ska utredas: −0,20; −1,27 (630); 1 |
| R₀ tryckt i kvadrat | godkänd: +0,13; +0,81 (1,25 k); 0 | godkänd: −0,12; −0,38 (630); 0 | underkänd: +1,19; +3,65 (800); 12 |
| R₀ tryckt | godkänd: +0,13; +0,81 (1,25 k); 0 | godkänd: −0,12; −0,38 (630); 0 | underkänd: +7,91; +14,70 (1,25 k); 19 |
| zon oklippt | godkänd: +0,13; +0,82 (1 k); 0 | godkänd: −0,03; −0,13 (25); 0 | godkänd: −0,00; +0,34 (800); 0 |

Samma läsningar prövade en i taget från **P-R2:s** förval (ingen av dem löser motvindsfallen ensam):

| Läsning | Blad 10 | Blad 15 | Blad 23 |
| --- | --- | --- | --- |
| P-R2:s förval | ska utredas: +0,39; +2,26 (800); 9 | underkänd: +1,85; +6,95 (630); 11 | underkänd: +1,75; +5,40 (800); 14 |
| + Q med gångtid 0 | underkänd: +10,01; +18,07 (2,5 k); 13 | underkänd: +13,51; +21,82 (1 k); 19 | underkänd: +13,05; +22,36 (1,25 k); 18 |
| + 10·lg | ska utredas: +0,40; +2,27 (800); 9 | underkänd: +3,39; +7,98 (10 k); 19 | underkänd: +2,89; +8,24 (10 k); 19 |
| + kiltopp ekv. 43 | ska utredas: +0,39; +2,27 (800); 9 | underkänd: +2,31; +6,83 (800); 12 | underkänd: +1,83; +5,22 (800); 14 |
| + R₀ tryckt i kvadrat | ska utredas: +0,06; −1,32 (8 k); 4 | underkänd: +1,02; +5,33 (800); 8 | underkänd: +1,97; +5,77 (800); 14 |
| + R₀ tryckt | ska utredas: +0,06; −1,32 (8 k); 4 | underkänd: +1,02; +5,33 (800); 8 | underkänd: +7,96; +14,71 (1,25 k); 19 |
| + uppåt exakt | ska utredas: +0,48; +2,25 (800); 5 | underkänd: +1,86; +6,95 (630); 11 | underkänd: +1,75; +5,36 (800); 14 |
| + vikter lag | ska utredas: +0,08; −1,32 (8 k); 4 | underkänd: +1,11; +5,51 (800); 8 | ska utredas: +0,86; +3,33 (800); 7 |
| + avgörande oberoende | ska utredas: +0,47; +1,44 (500); 7 | underkänd: +1,71; +5,47 (400); 12 | underkänd: +1,76; +4,44 (400); 14 |

Per tersband, alla läsningar (sista kolumnen är P-R2:s förval, alltså läget före):


**Blad 10** (beräknat − facit, dB)

| Band (Hz) | förval | avgörande per band | avgörande oberoende | vikter lag | vikter hog | uppåt bokstavlig | Q med gångtid 0 | 10·lg | kiltopp ekv. 43 | R₀ tryckt i kvadrat | R₀ tryckt | zon oklippt | P-R2:s förval |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | +0,00 | +0,04 | +0,03 | +0,00 | −0,00 | +0,00 | −0,00 | +0,02 | +0,02 | +0,00 | +0,00 | −0,13 | −0,07 |
| 31,5 | +0,00 | +0,08 | +0,04 | +0,00 | −0,00 | +0,00 | −0,00 | +0,03 | +0,03 | +0,00 | +0,00 | −0,10 | −0,01 |
| 40 | +0,00 | +0,10 | +0,06 | +0,00 | +0,00 | +0,00 | −0,00 | +0,04 | +0,03 | −0,00 | −0,00 | −0,08 | +0,03 |
| 50 | +0,02 | +0,13 | +0,10 | +0,02 | +0,02 | +0,02 | +0,02 | +0,06 | +0,04 | +0,02 | +0,02 | −0,05 | +0,07 |
| 63 | +0,02 | +0,13 | +0,14 | +0,02 | +0,03 | +0,02 | +0,02 | +0,06 | +0,03 | +0,01 | +0,01 | −0,03 | +0,10 |
| 80 | +0,02 | +0,12 | +0,19 | +0,02 | +0,05 | +0,02 | +0,02 | +0,06 | +0,02 | +0,01 | +0,01 | −0,02 | +0,11 |
| 100 | +0,03 | +0,12 | +0,28 | +0,03 | +0,09 | +0,03 | +0,03 | +0,07 | +0,04 | +0,02 | +0,02 | +0,01 | +0,15 |
| 125 | +0,03 | +0,09 | +0,37 | +0,03 | +0,15 | +0,03 | +0,03 | +0,05 | +0,02 | +0,01 | +0,01 | +0,01 | +0,20 |
| 160 | +0,03 | +0,07 | +0,47 | +0,03 | +0,21 | +0,03 | +0,03 | +0,05 | +0,03 | +0,01 | +0,01 | +0,02 | +0,25 |
| 200 | +0,00 | +0,02 | +0,53 | +0,00 | +0,25 | +0,00 | +0,00 | +0,01 | +0,00 | −0,02 | −0,02 | −0,00 | +0,27 |
| 250 | +0,00 | +0,01 | +0,55 | +0,00 | +0,27 | +0,00 | +0,00 | +0,01 | +0,00 | −0,02 | −0,02 | +0,00 | +0,29 |
| 315 | +0,00 | +0,01 | +0,55 | +0,00 | +0,36 | +0,00 | +0,00 | +0,01 | +0,00 | −0,02 | −0,02 | +0,01 | +0,37 |
| 400 | +0,00 | +0,02 | +0,47 | +0,00 | +0,61 | +0,00 | +0,00 | +0,01 | +0,00 | −0,03 | −0,03 | +0,01 | +0,64 |
| 500 | +0,00 | +0,03 | +0,01 | +0,00 | +1,46 | +0,00 | −0,00 | +0,01 | +0,00 | −0,06 | −0,06 | +0,01 | +1,54 |
| 630 | −0,00 | +0,04 | −0,43 | −0,00 | +1,66 | −0,00 | −0,00 | +0,02 | +0,01 | −0,07 | −0,07 | +0,01 | +1,76 |
| 800 | +0,70 | +0,72 | +0,28 | +0,70 | +2,19 | +0,71 | +6,98 | +0,71 | +0,71 | +0,64 | +0,64 | +0,70 | +2,26 |
| 1 k | +0,83 | +0,83 | +0,60 | +0,83 | +1,82 | +0,80 | +13,46 | +0,83 | +0,83 | +0,79 | +0,79 | +0,82 | +1,82 |
| 1,25 k | +0,83 | +0,83 | +0,73 | +0,83 | +1,36 | +0,66 | +16,16 | +0,83 | +0,83 | +0,81 | +0,81 | +0,82 | +1,22 |
| 1,6 k | +0,75 | +0,75 | +0,71 | +0,75 | +0,98 | +0,37 | +17,32 | +0,75 | +0,75 | +0,74 | +0,74 | +0,74 | +0,61 |
| 2 k | +0,59 | +0,59 | +0,58 | +0,59 | +0,66 | −0,06 | +17,73 | +0,59 | +0,59 | +0,59 | +0,59 | +0,58 | +0,02 |
| 2,5 k | +0,48 | +0,48 | +0,47 | +0,48 | +0,49 | −0,38 | +18,08 | +0,48 | +0,48 | +0,48 | +0,48 | +0,47 | −0,36 |
| 3,15 k | +0,30 | +0,30 | +0,30 | +0,30 | +0,30 | −0,74 | +17,16 | +0,30 | +0,30 | +0,30 | +0,30 | +0,29 | −0,74 |
| 4 k | +0,18 | +0,18 | +0,18 | +0,18 | +0,18 | −0,97 | +14,49 | +0,18 | +0,18 | +0,18 | +0,18 | +0,18 | −0,97 |
| 5 k | +0,10 | +0,10 | +0,10 | +0,10 | +0,10 | −1,17 | +11,30 | +0,10 | +0,10 | +0,10 | +0,10 | +0,10 | −1,17 |
| 6,3 k | +0,05 | +0,05 | +0,05 | +0,05 | +0,05 | −1,29 | +7,66 | +0,05 | +0,05 | +0,05 | +0,05 | +0,05 | −1,29 |
| 8 k | +0,03 | +0,03 | +0,03 | +0,03 | +0,03 | −1,32 | +4,10 | +0,03 | +0,03 | +0,03 | +0,03 | +0,03 | −1,32 |
| 10 k | +0,04 | +0,04 | +0,04 | +0,04 | +0,04 | −1,29 | +1,73 | +0,04 | +0,04 | +0,04 | +0,04 | +0,04 | −1,29 |
| **L_AE** | **+0,14** | **+0,18** | **+0,37** | **+0,14** | **+0,44** | **+0,05** | **+9,99** | **+0,16** | **+0,15** | **+0,13** | **+0,13** | **+0,13** | **+0,39** |
| utfall | godkänd | godkänd | godkänd | godkänd | ska utredas | ska utredas | underkänd | godkänd | godkänd | godkänd | godkänd | godkänd | ska utredas |

**Blad 15** (beräknat − facit, dB)

| Band (Hz) | förval | avgörande per band | avgörande oberoende | vikter lag | vikter hog | uppåt bokstavlig | Q med gångtid 0 | 10·lg | kiltopp ekv. 43 | R₀ tryckt i kvadrat | R₀ tryckt | zon oklippt | P-R2:s förval |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | +0,00 | +0,02 | +0,07 | +0,00 | −0,00 | +0,00 | +0,00 | +0,01 | +0,01 | +0,00 | +0,00 | −0,13 | +0,01 |
| 31,5 | +0,00 | +0,06 | +0,11 | +0,00 | −0,00 | +0,00 | +0,00 | +0,03 | +0,01 | +0,00 | +0,00 | −0,11 | +0,05 |
| 40 | +0,00 | +0,11 | +0,16 | +0,00 | +0,00 | +0,00 | +0,00 | +0,06 | +0,01 | −0,00 | −0,00 | −0,08 | +0,10 |
| 50 | +0,02 | +0,20 | +0,25 | +0,02 | +0,03 | +0,02 | +0,02 | +0,11 | +0,03 | +0,01 | +0,01 | −0,05 | +0,20 |
| 63 | +0,02 | +0,30 | +0,36 | +0,02 | +0,05 | +0,02 | +0,02 | +0,15 | +0,03 | +0,01 | +0,01 | −0,04 | +0,30 |
| 80 | +0,02 | +0,41 | +0,52 | +0,02 | +0,07 | +0,02 | +0,02 | +0,21 | +0,03 | +0,01 | +0,01 | −0,02 | +0,42 |
| 100 | +0,04 | +0,59 | +0,76 | +0,04 | +0,14 | +0,04 | +0,04 | +0,31 | +0,05 | +0,02 | +0,02 | +0,01 | +0,63 |
| 125 | +0,03 | +0,76 | +1,05 | +0,03 | +0,26 | +0,03 | +0,03 | +0,40 | −0,02 | +0,00 | +0,00 | +0,01 | +0,89 |
| 160 | +0,04 | +0,96 | +1,51 | +0,04 | +0,43 | +0,04 | +0,04 | +0,50 | −0,06 | −0,01 | −0,01 | +0,03 | +1,23 |
| 200 | +0,02 | +1,17 | +2,13 | +0,02 | +0,66 | +0,02 | +0,02 | +0,59 | −0,10 | −0,07 | −0,07 | +0,02 | +1,66 |
| 250 | +0,03 | +1,49 | +3,05 | +0,03 | +0,99 | +0,03 | +0,03 | +0,76 | −0,05 | −0,11 | −0,11 | +0,04 | +2,28 |
| 315 | +0,04 | +2,01 | +4,00 | +0,04 | +1,65 | +0,04 | +0,04 | +0,99 | −0,08 | −0,18 | −0,18 | +0,06 | +3,33 |
| 400 | +0,02 | +3,15 | +3,56 | +0,02 | +2,43 | +0,02 | +0,02 | +1,46 | −0,14 | −0,30 | −0,30 | +0,04 | +4,90 |
| 500 | −0,02 | +3,83 | +0,41 | −0,02 | +3,60 | −0,02 | −0,02 | +1,75 | −0,56 | −0,37 | −0,37 | +0,01 | +6,36 |
| 630 | −0,03 | +4,78 | −2,06 | −0,03 | +3,45 | −0,03 | −0,03 | +2,19 | −0,58 | −0,38 | −0,38 | −0,01 | +6,95 |
| 800 | +0,04 | +5,49 | −0,26 | +0,04 | +2,14 | +0,06 | +14,38 | +3,14 | +0,08 | −0,25 | −0,25 | +0,06 | +6,68 |
| 1 k | +0,02 | +2,68 | −0,05 | +0,02 | +1,79 | −0,00 | +21,79 | +3,68 | +0,06 | −0,25 | −0,25 | +0,03 | +3,90 |
| 1,25 k | +0,00 | +0,00 | −0,01 | +0,00 | +1,35 | −0,01 | +20,52 | +4,19 | +0,04 | −0,23 | −0,23 | +0,01 | +1,34 |
| 1,6 k | −0,01 | −0,01 | −0,01 | −0,01 | +1,11 | −0,03 | +19,01 | +4,69 | +0,01 | −0,20 | −0,20 | −0,00 | +1,09 |
| 2 k | −0,02 | −0,02 | −0,02 | −0,02 | +0,85 | −0,04 | +17,13 | +5,19 | −0,02 | −0,17 | −0,17 | −0,02 | +0,84 |
| 2,5 k | −0,03 | −0,03 | −0,03 | −0,03 | +0,64 | −0,06 | +15,62 | +5,45 | −0,02 | −0,14 | −0,14 | −0,03 | +0,61 |
| 3,15 k | −0,04 | −0,04 | −0,04 | −0,04 | +0,36 | −0,07 | +12,99 | +5,72 | +0,00 | −0,10 | −0,10 | −0,04 | +0,33 |
| 4 k | −0,04 | −0,04 | −0,04 | −0,04 | +0,23 | −0,06 | +10,90 | +5,97 | +0,08 | −0,08 | −0,08 | −0,03 | +0,20 |
| 5 k | +0,01 | +0,01 | +0,01 | +0,01 | +0,24 | +0,01 | +10,15 | +6,34 | +0,17 | −0,04 | −0,04 | +0,01 | +0,24 |
| 6,3 k | +0,01 | +0,01 | +0,01 | +0,01 | +0,21 | +0,01 | +9,57 | +6,73 | +0,21 | −0,03 | −0,03 | +0,01 | +0,21 |
| 8 k | +0,05 | +0,05 | +0,05 | −0,14 | +0,05 | +0,05 | +8,67 | +7,16 | +0,26 | −0,18 | −0,18 | +0,05 | +0,05 |
| 10 k | −0,00 | −0,00 | −0,00 | −0,19 | −0,00 | −0,00 | +8,12 | +7,51 | +0,24 | −0,23 | −0,23 | −0,00 | +0,06 |
| **L_AE** | **−0,01** | **+1,12** | **+1,08** | **−0,01** | **+0,78** | **−0,02** | **+13,42** | **+2,13** | **−0,05** | **−0,12** | **−0,12** | **−0,03** | **+1,85** |
| utfall | godkänd | underkänd | underkänd | godkänd | ska utredas | godkänd | underkänd | underkänd | godkänd | godkänd | godkänd | godkänd | underkänd |

**Blad 23** (beräknat − facit, dB)

| Band (Hz) | förval | avgörande per band | avgörande oberoende | vikter lag | vikter hog | uppåt bokstavlig | Q med gångtid 0 | 10·lg | kiltopp ekv. 43 | R₀ tryckt i kvadrat | R₀ tryckt | zon oklippt | P-R2:s förval |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | +0,00 | +0,01 | +0,05 | +0,00 | −0,00 | +0,00 | −0,00 | +0,01 | +0,01 | −0,00 | −0,01 | −0,13 | +0,01 |
| 31,5 | +0,00 | +0,04 | +0,07 | +0,00 | −0,00 | +0,00 | −0,00 | +0,03 | +0,01 | −0,00 | +0,00 | −0,11 | +0,03 |
| 40 | +0,00 | +0,08 | +0,11 | +0,00 | +0,00 | +0,00 | −0,00 | +0,06 | +0,01 | +0,01 | +0,03 | −0,08 | +0,07 |
| 50 | +0,02 | +0,15 | +0,18 | +0,02 | +0,03 | +0,02 | +0,01 | +0,11 | +0,03 | +0,03 | +0,09 | −0,05 | +0,14 |
| 63 | +0,02 | +0,21 | +0,25 | +0,02 | +0,05 | +0,02 | +0,01 | +0,16 | +0,03 | +0,05 | +0,18 | −0,04 | +0,21 |
| 80 | +0,02 | +0,29 | +0,36 | +0,02 | +0,08 | +0,02 | +0,01 | +0,22 | +0,03 | +0,08 | +0,32 | −0,02 | +0,31 |
| 100 | +0,04 | +0,42 | +0,53 | +0,04 | +0,14 | +0,04 | +0,03 | +0,32 | +0,05 | +0,15 | +0,54 | +0,00 | +0,47 |
| 125 | +0,03 | +0,54 | +0,73 | +0,03 | +0,19 | +0,03 | +0,02 | +0,41 | −0,02 | +0,22 | +0,64 | +0,01 | +0,64 |
| 160 | +0,03 | +0,69 | +1,05 | +0,03 | +0,30 | +0,03 | +0,02 | +0,52 | −0,07 | +0,35 | +1,12 | +0,02 | +0,87 |
| 200 | +0,01 | +0,83 | +1,48 | +0,01 | +0,46 | +0,01 | −0,00 | +0,61 | −0,11 | +0,54 | +2,14 | +0,01 | +1,18 |
| 250 | +0,02 | +1,03 | +2,14 | +0,02 | +0,79 | +0,02 | +0,00 | +0,77 | −0,07 | +0,89 | +4,40 | +0,02 | +1,67 |
| 315 | +0,03 | +1,29 | +2,90 | +0,03 | +1,24 | +0,03 | +0,01 | +0,96 | −0,12 | +1,43 | +7,45 | +0,04 | +2,35 |
| 400 | +0,03 | +1,65 | +3,12 | +0,03 | +1,77 | +0,03 | −0,00 | +1,22 | −0,28 | +2,10 | +10,96 | +0,04 | +3,20 |
| 500 | +0,00 | +1,91 | +0,73 | +0,00 | +1,73 | +0,00 | −0,03 | +1,41 | −0,99 | +2,47 | +9,65 | +0,02 | +3,45 |
| 630 | −0,01 | +2,33 | −2,28 | −0,01 | +1,78 | −0,01 | −0,05 | +1,71 | −1,27 | +2,58 | +9,67 | +0,01 | +3,87 |
| 800 | +0,33 | +3,27 | +0,88 | +0,33 | +2,83 | +0,37 | +13,30 | +2,74 | −0,50 | +3,65 | +14,32 | +0,34 | +5,40 |
| 1 k | +0,26 | +1,46 | +0,26 | +0,26 | +2,83 | +0,24 | +22,03 | +3,25 | −0,54 | +3,61 | +14,63 | +0,27 | +3,84 |
| 1,25 k | +0,12 | +0,12 | +0,12 | +0,12 | +2,69 | +0,11 | +22,36 | +3,83 | −0,34 | +3,25 | +14,70 | +0,13 | +2,69 |
| 1,6 k | +0,05 | +0,05 | +0,05 | +0,05 | +2,49 | +0,04 | +21,91 | +4,40 | −0,26 | +2,92 | +14,03 | +0,06 | +2,48 |
| 2 k | +0,02 | +0,02 | +0,02 | +0,02 | +2,28 | +0,01 | +20,99 | +4,98 | −0,22 | +2,61 | +13,03 | +0,03 | +2,26 |
| 2,5 k | +0,01 | +0,01 | +0,01 | +0,01 | +2,04 | −0,01 | +20,41 | +5,29 | −0,19 | +2,35 | +11,61 | +0,02 | +2,03 |
| 3,15 k | +0,00 | +0,00 | +0,00 | +0,00 | +1,70 | −0,02 | +19,04 | +5,63 | −0,10 | +1,92 | +9,76 | +0,01 | +1,68 |
| 4 k | +0,00 | +0,00 | +0,00 | +0,00 | +1,24 | −0,02 | +18,45 | +6,00 | −0,05 | +1,43 | +6,94 | +0,01 | +1,23 |
| 5 k | −0,01 | −0,01 | −0,01 | −0,01 | +0,70 | −0,04 | +17,74 | +6,43 | −0,00 | +0,82 | +3,65 | −0,01 | +0,67 |
| 6,3 k | −0,04 | −0,04 | −0,04 | −0,04 | +0,43 | −0,08 | +15,73 | +6,81 | +0,07 | +0,47 | +2,55 | −0,04 | +0,39 |
| 8 k | +0,05 | +0,05 | +0,05 | −0,37 | +0,05 | +0,01 | +12,68 | +7,26 | +0,26 | +0,26 | +2,13 | +0,05 | +0,01 |
| 10 k | −0,05 | −0,05 | −0,05 | −0,41 | −0,05 | −0,09 | +10,29 | +7,54 | +0,23 | +0,13 | +1,73 | −0,05 | +0,02 |
| **L_AE** | **+0,01** | **+0,87** | **+1,02** | **+0,01** | **+0,93** | **+0,01** | **+12,96** | **+1,32** | **−0,20** | **+1,19** | **+7,91** | **−0,00** | **+1,75** |
| utfall | godkänd | ska utredas | underkänd | godkänd | ska utredas | godkänd | underkänd | underkänd | ska utredas | underkänd | underkänd | godkänd | underkänd |

### 2.3 Byten av förval – alla märkta "avgjord av testfall"

Regeln (`tolkningar.md`, sista avsnittet): diskreta läsningar, alla körda och redovisade sida vid sida, byte bara
när testfallen tydligt pekar ut en läsning. Tabellerna i 2.2 och avsnitt 4 innehåller siffrorna för alla läsningar;
här står skälen. **Alla fem posterna ska granskas av någon som kan metoden.**

**A. Skuggzonens avgörande: `per_band` → `delat`. AVGJORD AV TESTFALL (blad 15 och 23).**

- Läsningarna. (a) `per_band` (P-R2): skuggzon prövas band för band med ξ(f); ekv. (121) med ΔL_SZ används bara i
  de band där ξ(f) själv ger skuggzon. (b) `oberoende`: skuggzon prövas en gång med den frekvensoberoende profilen;
  då används ekv. (121) med ψ_G = 0 och Δτ = 0 i alla band. (c) `delat`: markeffektens form avgörs band för band
  med ξ(f) (ekv. 120, eller första termen i ekv. 121 när bandet självt ligger i skuggzon), och ΔL_SZ(f) läggs till
  i alla band så snart den frekvensoberoende profilen ger skuggzon.
- Siffror (utfall: L_AE; största band; band över 1 dB):

  | Läsning | Blad 10 | Blad 15 | Blad 23 |
  | --- | --- | --- | --- |
  | (a) `per_band` | godkänd: +0,18; +0,83 (1 k); 0 | underkänd: +1,12; +5,49 (800); 8 | ska utredas: +0,87; +3,27 (800); 7 |
  | (b) `oberoende` | godkänd: +0,37; +0,73 (1,25 k); 0 | underkänd: +1,08; +4,00 (315); 7 | underkänd: +1,02; +3,12 (400); 6 |
  | (c) `delat` | godkänd: +0,14; +0,83 (1,25 k); 0 | godkänd: −0,01; +0,05 (8 k); 0 | godkänd: +0,01; +0,33 (800); 0 |

  Med (c) ligger blad 15 inom 0,05 dB i alla 27 band. Med (a) är 100–1000 Hz +0,6 till +5,5 dB för högt, med (b)
  är 100–400 Hz +0,8 till +4,0 dB för högt och 630 Hz 2,1 dB för lågt.
- Vad texten säger om (c). Tre saker talar för den, oberoende av utfallet: (1) ekv. (122) föreskriver uttryckligen
  den frekvensoberoende profilen för ΔL_SZ, medan strålvariablerna i ekv. (116)–(120) sägs vara frekvensberoende;
  (2) ekv. (385) låter ξ_SZ(f) tona ut från 2 kHz ned till 20 Hz – det vore meningslöst om ΔL_SZ aldrig användes
  under f_L (omkring 700 Hz i blad 15), vilket är följden av (a); (3) AV 1851/00 avsnitt 6.1 anger att uppdelningen
  ΔL = ΔL_G + ΔL_SZ finns för att undvika språng vid skuggzonens kant. Uppmätt (2.4): (a) ger språng på 12 dB,
  (c) högst 0,4 dB. Det som talar emot (c): ekv. (120)/(121) är skrivna som två fall med ett gemensamt villkor.
  P-R2 valde (a) innan utfallet var känt; därför räknas bytet som avgjort av testfall och inte av texten.

**B. Vikterna i delmodell 2 i skuggzon: `hog` → `ovre_band`. AVGJORD AV TESTFALL (blad 10, 15, 23).**

- Läsningarna (texten ger 0/0, nr 9 i 2.1). `hog`: f_H = 25 Hz, alla band får w_H. `lag`: f_H = 100 kHz, alla band
  får w_L. `ovre_band`: f_H = 8 kHz-bandets mittfrekvens och f_L = 0,8·f_H, det värde den linjära extrapolationen
  "ur värdena vid 8 och 10 kHz" ger när den förankras i 8 kHz och tillägget har täljaren noll; banden till och med
  6,3 kHz får w_L, 8 och 10 kHz får w_H.
- Siffror:

  | Läsning | Blad 10 | Blad 15 | Blad 23 |
  | --- | --- | --- | --- |
  | `hog` | ska utredas: +0,44; +2,19 (800); 5 | ska utredas: +0,78; +3,60 (500); 8 | ska utredas: +0,93; +2,83 (1 k); 12 |
  | `lag` | godkänd: +0,14; +0,83 (1,25 k); 0 | godkänd: −0,01; −0,19 (10 k); 0 | godkänd: +0,01; −0,41 (10 k); 0 |
  | `ovre_band` | godkänd: +0,14; +0,83 (1,25 k); 0 | godkänd: −0,01; +0,05 (8 k); 0 | godkänd: +0,01; +0,33 (800); 0 |

  `hog` är tydligt utpekad som fel (band upp till 3,6 dB). Mellan `lag` och `ovre_band` skiljer bara 8 och 10 kHz:
  blad 15 −0,14/−0,19 dB mot +0,05/0,00 dB; blad 23 −0,37/−0,41 dB mot +0,05/−0,05 dB. Totalnivån är densamma.
  **Den delen av valet vilar på fyra bandvärden och är den svagaste av de fem posterna.** Att mönstret är just
  "w_L till 6,3 kHz, w_H från 8 kHz" tyder på att referensprogrammet får f_H = 8 kHz ur extrapolationen.

**C. Uppåtbrytning: `bokstavlig` → `exakt`. AVGJORD AV TESTFALL (blad 10).**

- Läsningarna: P-R1 tolkning 3. Texten föreskriver `bokstavlig` (|ξ′|) på två ställen; `exakt` är samma ekvationer
  med ξ′ med tecken, alltså den verkliga cirkelbågen.
- Siffror, blad 10 (dt/dz = −0,1 K/m, det enda bladet där uppåtbrytning utan skuggzon dominerar):

  | Band (Hz) | 2 k | 2,5 k | 3,15 k | 4 k | 5 k | 6,3 k | 8 k | 10 k | L_AE | utfall |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | `bokstavlig` | −0,06 | −0,38 | −0,74 | −0,97 | −1,17 | −1,29 | −1,32 | −1,29 | +0,05 | ska utredas (4 band) |
  | `exakt` | +0,59 | +0,48 | +0,30 | +0,18 | +0,10 | +0,05 | +0,03 | +0,04 | +0,14 | godkänd |

  Avgörande är 8 och 10 kHz, där källmodellens direktivitet inte verkar (avsnitt 5): −1,32/−1,29 dB mot
  +0,03/+0,04 dB. Det `exakt` har kvar i 800–6300 Hz är samma rest som i de homogena bladen. I L_AE ligger
  `bokstavlig` närmare (0,05 mot 0,14 dB); det beror på att två fel tar ut varandra (direktivitetsresten är positiv).
  Blad 15 och 23 ändras högst 0,04 dB i något band av valet. **Detta byte går emot textens ordalydelse och vilar på
  ett blad.**

**D. Fresnelzonen i delmodell 2: `oklippt` → `klippt`. AVGJORD AV TESTFALL (alla blad), och textens definition.**

- Läsningarna: P-R2 tolkning 18. Avsnitt 5.8 (s. 50, omläst): bara den del av zonen som ligger mellan källa och
  mottagare tas med. Ekv. (349)–(351) saknar begränsningen.
- Siffror. Största avvikelse i banden 25–630 Hz (där ingen annan känd rest finns):

  | Blad | 1 | 2 | 3–6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 21 | 22 | 23 | 24 |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | `oklippt` | 0,20 | 0,13 | 0,12 | 0,03 | 0,07 | 0,13 | 0,13 | 0,12 | 0,08 | 1,05 | 1,28 | 0,13 | 2,62 | 0,12 | 0,08 | 0,13 | 0,07 |
  | `klippt` | 0,03 | 0,04 | 0,03–0,04 | 0,03 | 0,09 | 0,03 | 0,03 | 0,03 | 0,04 | 0,04 | 0,04 | 0,04 | 0,46 | 0,03 | 0,03 | 0,04 | 0,08 |

  Utfall med `oklippt`: blad 13 ska utredas (−0,91 dB), blad 14 underkänt (−1,32 dB), blad 16 underkänt (−1,84 dB;
  −2,62 dB vid 315 Hz); övriga som med `klippt`. Orsaken i medvindsbladen: för sneda källpunkter i stark
  nedåtbrytning ligger reflexpunkten nära källan (0,5 m i 81°) med brant strykvinkel, zonen sticker ut bakom källan
  och vikterna summerar till 0,61 vid 25 Hz i stället för 1.
- Åt andra hållet: med `klippt` ökar L_AE i blad 1–3 med 0,03–0,07 dB (blad 1: +0,22 → +0,29 dB) och 800 Hz i
  blad 1 från +0,26 till +0,41 dB. Det är direktivitetsresten som inte längre döljs: med direktiviteten pålagd är
  blad 1 −0,01 dB med `klippt`.

**E. Luftabsorptionen över bandformelns maximum: `tryckt` → `monoton`. AVGJORD AV TESTFALL (blad 9, 11, 12, 14, 16,
21, 22, 24).** Se avsnitt 3.

**Bekräftade förval (inte bytta).** Testfallen stöder P-R2:s val i nr 1, 2, 4 och 8 (2.2): Q med gångtid 0 ger
+10 till +13 dB i L_AE; 10·lg ger +2,1 och +1,3 dB i blad 15 och 23 (+7,5 dB vid 10 kHz); kilens topp enligt
ekv. (43) gör blad 23 till "ska utredas" (−1,27 dB vid 630 Hz); R₀ tryckt i kvadrat ger +1,19 dB och R₀ tryckt
+7,91 dB i blad 23. C_T²-formen (P-7) bekräftas på nytt: `tryckt` ger +4,7 dB i blad 11 och 15.

### 2.4 Kontinuitet in i skuggzonen

Största ändring av ΔL₁ över 0,5 m när avståndet går från 30 till 300 m (0,3/1,5 m, motvind 3 m/s), alla band:

| Mark, väder | `per_band`, `bokstavlig` (P-R2) | `delat`, `exakt` (nu) |
| --- | --- | --- |
| σ = 200, turbulens som blad 21–24 | 12,2 dB (800 Hz, 183,5 m) | 0,42 dB (5 kHz, 40 m) |
| σ = 200, turbulens som blad 11–16 | 12,3 dB (800 Hz, 183,5 m) | 0,24 dB (10 kHz, 55,5 m) |
| σ = 20 000, turbulens som blad 21–24 | 2,7 dB (5 kHz, 52 m) | 1,6 dB (5 kHz, 52 m) |
| σ = 200, utan turbulens | 55,8 dB (10 kHz, 52 m) | 37,4 dB (10 kHz, 52 m); under 2 kHz 4,1 dB |
| σ = 20 000, utan turbulens | 5,6 dB (8 kHz, 52 m) | 7,1 dB (10 kHz, 52 m) |

Språnget på upp till 12 dB per band som P-R2 beskrev är borta med testfallens turbulens. Det som finns kvar utan
turbulens kommer av att formen byts vid 0,95·d_SZ och inte där strålen tangerar marken (metodens egen marginal).
Ett test håller fast det (`test_inget_sprang_in_i_skuggzonen_med_delat_avgorande`).

### 2.5 Rena implementationsfel

Inget formelfel hittat i P-R2:s kod. Det som är prövat: (1) med P-R2:s läsningar valda återger den omstrukturerade
koden P-R2:s resultat bit för bit; (2) med de nya förvalen och direktiviteten pålagd (avsnitt 5) ligger alla band i
blad 1–15 och 21–24 inom 0,12 dB, vilket lämnar litet utrymme för fel i plan mark-vägen; (3) varje ekvation i
skuggzonsvägen är jämförd med sidbilden på nytt (ekv. 115–122, 346–351, 384–387, 43 enligt TC, 52). Två saker som
inte är fel men som dolde varandra: testet `test_ovre_grans` (L_p − L_W ≤ fri spridning + 6,03 dB) höll bara
därför att den oklippta zonens vikter summerade till under 1 (delmodell 1 ger själv 6,03 dB över gräs vid 25 Hz);
och `hog`-vikterna gömde den oklippta zonens underskott vid 25 Hz i blad 15 och 23 (+0,01 dB av två fel om
±0,12 dB).

## 3. 10 kHz-bandet: orsaken är luftabsorptionens bandformel

**Orsak.** Ekv. (287), ΔL₉ = −A₀·(1,0053255 − 0,00122622·A₀)^1,6 (avläst s. 124), ger en dämpning som växer upp till
A₀ = 1,0053255/(2,6·0,00122622) = 315,3 dB (146,2 dB), sedan *avtar* och är 0 vid A₀ ≈ 820 dB. Metoden anger inget
giltighetsområde (AV 1849/00 avsnitt 3 visar formeln upp till A₀ = 100 dB). För en rak väg på 100 m:

| Källpunkt | R (m) | A₀ vid 10 kHz (dB) | ΔL₉ enligt ekv. (287) (dB) |
| --- | --- | --- | --- |
| 0° | 97 | 13,9 | −13,6 |
| 80° | 557 | 80,0 | −68,4 |
| 87° | 1849 | 265 | −143,1 |
| 88° | 2772 | 398 | −138,7 |
| 89° | 5544 | 796 | **−2,9** |

Vid 8 kHz har 89°-punkten A₀ = 520 dB och −105 dB, så bara 10 kHz-bandet berörs. I fritt fält bidrar varje
vinkelsteg lika mycket, så de två 89°-punkterna, nästan utan luftabsorption, lyfter 10 kHz-bandet – men bara när
marken inte dämpar dem: i de koherenta bladen (1–10) släcker markreflexen ut dem (strykande infall), i bladen med
turbulens eller fluktuerande refraktion är F = 0 på 5,5 km och markeffekten +0,4 dB. Därför avvek just blad 11, 21,
24 (+2,5 till +2,9 dB) och medvindsbladen (+0,9 till +2,6 dB), och inte blad 1–8, 10, 15, 23. De fyra kandidaterna
i uppdraget: bandmedelvärdet, extrapolationen i PhaseDiffFreq och F_Δτ är inte orsaken (blad 11 är oförändrat i
10 kHz av dem; P-R2 prövade koherensen); luftabsorptionen för sneda källpunkter är det.

**Läsningarna** (texten täcker inte A₀ över formelns maximum): (a) `tryckt`: formeln som den står, `BullerFel` där
parentesen blir ≤ 0 (B4:s tolkning; stoppade blad 16, där A₀ = 1013 dB vid 3,15 kHz i 89°); (b) `monoton`:
dämpningen hålls kvar på formelns största värde, 146,2 dB, för A₀ över 315,3 dB; (c) rentonsdämpningen −A₀ över
maximum (prövad utanför repot).

**Siffror** (utfall: L_AE; största band; band över 1 dB):

| Blad | monoton (förval) | ekv. 287 som tryckt |
| --- | --- | --- |
| 9 | godkänd: −0,41; −0,87 (800); 0 | ska utredas: −0,41; +1,52 (10 k); 1 |
| 11 | godkänd: +0,10; +0,20 (1 k); 0 | ska utredas: +0,10; +2,88 (10 k); 1 |
| 12 | godkänd: −0,20; +0,41 (6,3 k); 0 | ska utredas: −0,20; +2,61 (10 k); 1 |
| 13 | godkänd: −0,46; −0,87 (800); 0 | godkänd: −0,46; −0,87 (800); 0 |
| 14 | ska utredas: −0,73; −1,03 (800); 1 | ska utredas: −0,73; +1,48 (10 k); 2 |
| 16 | godkänd: +0,38; −0,91 (10 k); 0 | ej körbar (ekv. 287: A₀ = 1013 dB vid 3,15 kHz) |
| 21 | godkänd: +0,22; +0,39 (800); 0 | ska utredas: +0,22; +2,61 (10 k); 1 |
| 22 | godkänd: −0,24; +0,42 (6,3 k); 0 | ska utredas: −0,24; +2,08 (10 k); 1 |
| 24 | godkänd: +0,35; +0,54 (1 k); 0 | ska utredas: +0,35; +2,46 (10 k); 1 |
| 15 | godkänd: −0,01; +0,05 (8 k); 0 | godkänd: −0,01; +0,07 (10 k); 0 |
| 23 | godkänd: +0,01; +0,33 (800); 0 | godkänd: +0,01; +0,33 (800); 0 |

(c) ger samma värden som (b) inom 0,001 dB i alla blad. Blad 15 och 23 ändras 0,07 respektive 0,12 dB i 10 kHz.
**AVGJORD AV TESTFALL**: sju blad (9, 11, 12, 14, 21, 22, 24) går från +1,5 till +2,9 dB i 10 kHz till inom
0,05 dB, blad 13 från +0,9 dB till 0,00 dB, och blad 16 från ej körbar till godkänd. Förvalet är (b), eftersom den är kontinuerlig i avståndet; (c) har ett språng från 146 till 315 dB.
Konstanten 315,3 dB är formelns eget maximum (derivatans nollställe), inget anpassat tal.

**Var det ligger.** `luftabsorption.py` ägs inte av detta paket. Funktionen `luftabsorption_monoton_db` ligger
därför i `karna.py` och använder `luftabsorption.rentonsdampning_db_per_m`; kärnans val heter `luftabsorption`
(`"monoton"`, `"tryckt"`). Den bör flyttas till `luftabsorption.py` (se Önskade ändringar).

## 4. Testsvit grupp N före och efter

`Nord2000Karna()` med förval, `Vagemission`, `kor_alla`. 84 fall. Före: godkänd 10, ska utredas 4, underkänd 2,
ej facit 4, ej körbar 64. **Efter: godkänd 21, ska utredas 1, underkänd 0, ej facit 4, ej körbar 58.**
Format: utfall: avvikelse i L_AE (beräknat − facit, dB); största tersband (Hz); antal band över 1 dB.

| Blad | före (P-R2) | efter steg 1 (bara ΔL₈ inkopplad) | efter (P-S1) |
| --- | --- | --- | --- |
| 1 | godkänd: +0,22; +0,28 (1,6 k); 0 | godkänd: +0,22; +0,28 (1,6 k); 0 | godkänd: +0,29; +0,41 (800); 0 |
| 2 | godkänd: +0,22; +0,34 (800); 0 | godkänd: +0,22; +0,34 (800); 0 | godkänd: +0,27; +0,45 (800); 0 |
| 3 | godkänd: +0,20; +0,45 (800); 0 | godkänd: +0,20; +0,45 (800); 0 | godkänd: +0,23; +0,52 (800); 0 |
| 4 | godkänd: +0,14; +0,56 (800); 0 | godkänd: +0,14; +0,56 (800); 0 | godkänd: +0,15; +0,56 (800); 0 |
| 5 | godkänd: +0,10; +0,62 (800); 0 | godkänd: +0,10; +0,62 (800); 0 | godkänd: +0,10; +0,62 (800); 0 |
| 6 | godkänd: +0,24; +0,65 (1,25 k); 0 | godkänd: +0,24; +0,65 (1,25 k); 0 | godkänd: +0,24; +0,65 (1,25 k); 0 |
| 7 | godkänd: +0,07; +0,75 (6,3 k); 0 | godkänd: +0,07; +0,75 (6,3 k); 0 | godkänd: +0,07; +0,75 (6,3 k); 0 |
| 8 | godkänd: +0,24; +0,52 (800); 0 | godkänd: +0,24; +0,52 (800); 0 | godkänd: +0,26; +0,53 (800); 0 |
| 9 | ej körbar (N > 4, delmodell 8 saknades) | ska utredas: −0,47; +1,50 (10 k); 1 | godkänd: −0,41; −0,87 (800); 0 |
| 10 | ska utredas: +0,39; +2,26 (800); 9 | ska utredas: +0,39; +2,26 (800); 9 | godkänd: +0,14; +0,83 (1,25 k); 0 |
| 11 | ska utredas: +0,09; +2,88 (10 k); 1 | ska utredas: +0,09; +2,88 (10 k); 1 | godkänd: +0,10; +0,20 (1 k); 0 |
| 12 | ej körbar (N > 4, delmodell 8 saknades) | ska utredas: −0,33; +2,61 (10 k); 1 | godkänd: −0,20; +0,41 (6,3 k); 0 |
| 13 | ej körbar (N > 4, delmodell 8 saknades) | ska utredas: −0,89; −1,53 (800); 3 | godkänd: −0,46; −0,87 (800); 0 |
| 14 | ej körbar (N > 4, delmodell 8 saknades) | underkänd: −1,25; −1,87 (800); 6 | ska utredas: −0,73; −1,03 (800); 1 |
| 15 | underkänd: +1,85; +6,95 (630); 11 | underkänd: +1,85; +6,95 (630); 11 | godkänd: −0,01; +0,05 (8 k); 0 |
| 16 | ej körbar (N > 4, delmodell 8 saknades) | ej körbar (ekv. 287: A₀ = 1013 dB vid 3,15 kHz) | godkänd: +0,38; −0,91 (10 k); 0 |
| 17 | godkänd: −0,19; −0,32 (8 k); 0 | godkänd: −0,19; −0,32 (8 k); 0 | godkänd: −0,18; −0,32 (10 k); 0 |
| 18 | godkänd: −0,37; −0,69 (630); 0 | godkänd: −0,37; −0,69 (630); 0 | godkänd: −0,36; −0,70 (630); 0 |
| 21 | ska utredas: +0,21; +2,61 (10 k); 1 | ska utredas: +0,21; +2,61 (10 k); 1 | godkänd: +0,22; +0,39 (800); 0 |
| 22 | ej körbar (N > 4, delmodell 8 saknades) | ska utredas: −0,37; +2,08 (10 k); 1 | godkänd: −0,24; +0,42 (6,3 k); 0 |
| 23 | underkänd: +1,75; +5,40 (800); 14 | underkänd: +1,75; +5,40 (800); 14 | godkänd: +0,01; +0,33 (800); 0 |
| 24 | ska utredas: +0,31; +2,46 (10 k); 1 | ska utredas: +0,31; +2,46 (10 k); 1 | godkänd: +0,35; +0,54 (1 k); 0 |
| 31–64, 101–104 | ej körbar (icke-plan terräng) | som före | som före |
| 71–94, 111–114 | ej körbar (skärmar) | som före | som före |
| 121–124 | ej körbar (skog: indata ofullständig) | som före | som före |
| N18K1–8 (10 fall), N18S1–4, N18A1–4 | ej körbar (kurvig väg, stadsgata, årsmedel) | som före | som före |
| N01–N04 | ej facit | som före | som före |

**Efter, per tersband, bladen med väder** (beräknat − facit, dB):

| Band (Hz) | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 21 | 22 | 23 | 24 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | −0,01 | +0,00 | −0,00 | +0,00 | +0,00 |
| 31,5 | +0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | +0,00 |
| 40 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | −0,00 | +0,00 | +0,01 | −0,00 | −0,00 | +0,00 | −0,00 |
| 50 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,03 | +0,02 | +0,02 | +0,02 | +0,04 |
| 63 | +0,02 | +0,02 | +0,02 | +0,01 | +0,02 | +0,02 | +0,02 | +0,04 | +0,02 | +0,02 | +0,02 | +0,04 |
| 80 | +0,02 | +0,02 | +0,02 | +0,01 | +0,02 | +0,02 | +0,02 | +0,04 | +0,02 | +0,02 | +0,02 | +0,04 |
| 100 | +0,03 | +0,03 | +0,03 | +0,03 | +0,04 | +0,04 | +0,04 | +0,04 | +0,03 | +0,03 | +0,04 | +0,08 |
| 125 | +0,02 | +0,03 | +0,03 | +0,02 | +0,03 | +0,03 | +0,03 | +0,02 | +0,03 | +0,02 | +0,03 | +0,07 |
| 160 | +0,02 | +0,03 | +0,03 | +0,02 | +0,03 | +0,04 | +0,04 | +0,01 | +0,03 | +0,02 | +0,03 | +0,07 |
| 200 | −0,00 | +0,00 | +0,00 | −0,02 | +0,01 | +0,01 | +0,02 | −0,04 | +0,00 | −0,01 | +0,01 | +0,00 |
| 250 | −0,00 | +0,00 | +0,00 | −0,03 | +0,01 | +0,01 | +0,03 | −0,20 | +0,00 | −0,01 | +0,02 | +0,00 |
| 315 | +0,00 | +0,00 | +0,00 | −0,03 | −0,01 | −0,01 | +0,04 | −0,46 | +0,00 | −0,02 | +0,03 | +0,00 |
| 400 | +0,01 | +0,00 | +0,00 | −0,01 | −0,01 | −0,03 | +0,02 | −0,35 | +0,00 | −0,02 | +0,03 | +0,00 |
| 500 | +0,01 | +0,00 | +0,00 | +0,01 | −0,00 | −0,00 | −0,02 | +0,01 | +0,00 | −0,00 | +0,00 | +0,00 |
| 630 | +0,01 | −0,00 | +0,00 | +0,04 | +0,02 | +0,02 | −0,03 | +0,18 | +0,00 | +0,01 | −0,01 | +0,00 |
| 800 | −0,87 | +0,70 | +0,18 | −0,26 | −0,87 | −1,03 | +0,04 | +0,46 | +0,39 | −0,39 | +0,33 | +0,51 |
| 1 k | −0,83 | +0,83 | +0,20 | −0,26 | −0,73 | −0,97 | +0,02 | +0,27 | +0,38 | −0,36 | +0,26 | +0,54 |
| 1,25 k | −0,71 | +0,83 | +0,18 | −0,24 | −0,59 | −0,87 | +0,00 | +0,72 | +0,35 | −0,32 | +0,12 | +0,49 |
| 1,6 k | −0,58 | +0,75 | +0,16 | −0,23 | −0,46 | −0,77 | −0,01 | +0,49 | +0,32 | −0,28 | +0,05 | +0,37 |
| 2 k | −0,38 | +0,59 | +0,13 | −0,21 | −0,33 | −0,66 | −0,02 | +0,78 | +0,28 | −0,23 | +0,02 | +0,27 |
| 2,5 k | −0,21 | +0,48 | +0,10 | −0,16 | −0,22 | −0,56 | −0,03 | +0,82 | +0,26 | −0,16 | +0,01 | +0,22 |
| 3,15 k | −0,06 | +0,30 | +0,03 | −0,06 | −0,09 | −0,42 | −0,04 | +0,86 | +0,20 | −0,06 | +0,00 | +0,17 |
| 4 k | −0,00 | +0,18 | +0,04 | +0,06 | +0,04 | −0,31 | −0,04 | +0,88 | +0,16 | +0,05 | +0,00 | +0,13 |
| 5 k | +0,02 | +0,10 | +0,05 | +0,22 | +0,18 | −0,20 | +0,01 | +0,78 | +0,13 | +0,23 | −0,01 | +0,10 |
| 6,3 k | +0,01 | +0,05 | +0,06 | +0,41 | +0,32 | −0,09 | +0,01 | +0,42 | +0,11 | +0,42 | −0,04 | +0,10 |
| 8 k | −0,04 | +0,03 | +0,00 | +0,00 | +0,00 | +0,00 | +0,05 | −0,61 | +0,00 | +0,00 | +0,05 | +0,00 |
| 10 k | −0,05 | +0,04 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,91 | +0,00 | +0,00 | −0,05 | +0,00 |
| **L_AE** | **−0,41** | **+0,14** | **+0,10** | **−0,20** | **−0,46** | **−0,73** | **−0,01** | **+0,38** | **+0,22** | **−0,24** | **+0,01** | **+0,35** |
| största band | −0,87 | +0,83 | +0,20 | +0,41 | −0,87 | −1,03 | +0,05 | −0,91 | +0,39 | +0,42 | +0,33 | +0,54 |
| band över 1 dB | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| utfall | godkänd | godkänd | godkänd | godkänd | godkänd | ska utredas | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd |

**Före, per tersband** (de blad som gick att räkna; ur körningen före första ändringen):

| Band (Hz) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 17 | 18 | 10 | 11 | 15 | 21 | 23 | 24 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | −0,11 | −0,12 | −0,12 | −0,12 | −0,12 | −0,12 | +0,00 | −0,07 | −0,15 | −0,17 | −0,07 | −0,12 | +0,01 | −0,12 | +0,01 | −0,07 |
| 31,5 | −0,07 | −0,09 | −0,09 | −0,09 | −0,09 | −0,09 | +0,00 | −0,05 | −0,12 | −0,14 | −0,01 | −0,09 | +0,05 | −0,09 | +0,03 | −0,05 |
| 40 | −0,03 | −0,06 | −0,06 | −0,06 | −0,06 | −0,06 | +0,00 | −0,04 | −0,09 | −0,11 | +0,03 | −0,06 | +0,10 | −0,06 | +0,07 | −0,04 |
| 50 | +0,02 | −0,01 | −0,02 | −0,02 | −0,02 | −0,02 | +0,02 | +0,01 | −0,06 | −0,07 | +0,07 | −0,02 | +0,20 | −0,02 | +0,14 | +0,01 |
| 63 | +0,03 | +0,00 | −0,01 | −0,01 | −0,01 | −0,01 | +0,02 | +0,02 | −0,04 | −0,05 | +0,10 | −0,01 | +0,30 | −0,01 | +0,21 | +0,02 |
| 80 | +0,03 | +0,02 | +0,00 | −0,00 | −0,00 | −0,00 | +0,02 | +0,02 | −0,04 | −0,05 | +0,11 | −0,00 | +0,42 | −0,00 | +0,31 | +0,02 |
| 100 | +0,02 | +0,05 | +0,03 | +0,02 | +0,02 | +0,01 | +0,03 | +0,07 | −0,04 | −0,09 | +0,15 | +0,02 | +0,63 | +0,02 | +0,47 | +0,07 |
| 125 | −0,09 | +0,05 | +0,02 | +0,02 | +0,01 | +0,01 | +0,02 | +0,06 | −0,05 | −0,13 | +0,20 | +0,02 | +0,89 | +0,01 | +0,64 | +0,06 |
| 160 | −0,14 | +0,03 | +0,03 | +0,02 | +0,02 | +0,01 | +0,02 | +0,07 | −0,07 | −0,15 | +0,25 | +0,02 | +1,23 | +0,02 | +0,87 | +0,06 |
| 200 | −0,18 | −0,03 | +0,01 | −0,00 | −0,00 | −0,01 | +0,00 | +0,00 | −0,11 | −0,19 | +0,27 | −0,00 | +1,66 | −0,00 | +1,18 | +0,00 |
| 250 | −0,17 | −0,05 | +0,01 | +0,00 | −0,00 | −0,00 | +0,00 | +0,01 | −0,15 | −0,24 | +0,29 | +0,00 | +2,28 | −0,00 | +1,67 | +0,00 |
| 315 | −0,18 | −0,08 | +0,00 | +0,00 | −0,00 | −0,00 | +0,00 | +0,01 | −0,18 | −0,29 | +0,37 | +0,00 | +3,33 | +0,00 | +2,35 | +0,00 |
| 400 | −0,17 | −0,10 | −0,01 | +0,01 | +0,00 | −0,00 | +0,00 | +0,01 | −0,14 | −0,43 | +0,64 | +0,00 | +4,90 | +0,00 | +3,20 | +0,01 |
| 500 | −0,20 | −0,13 | −0,04 | +0,01 | +0,00 | −0,00 | +0,00 | +0,01 | −0,06 | −0,52 | +1,54 | +0,00 | +6,36 | +0,01 | +3,45 | +0,00 |
| 630 | −0,17 | −0,12 | −0,06 | +0,01 | +0,01 | −0,00 | +0,00 | +0,01 | −0,05 | −0,69 | +1,76 | +0,00 | +6,95 | +0,00 | +3,87 | +0,00 |
| 800 | +0,26 | +0,34 | +0,45 | +0,56 | +0,62 | +0,36 | −0,02 | +0,52 | +0,25 | −0,44 | +2,26 | +0,18 | +6,68 | +0,39 | +5,40 | +0,49 |
| 1 k | +0,27 | +0,33 | +0,42 | +0,54 | +0,60 | +0,54 | +0,02 | +0,48 | +0,01 | −0,50 | +1,82 | +0,19 | +3,90 | +0,37 | +3,84 | +0,50 |
| 1,25 k | +0,27 | +0,31 | +0,37 | +0,48 | +0,58 | +0,65 | +0,07 | +0,42 | −0,14 | −0,49 | +1,22 | +0,18 | +1,34 | +0,33 | +2,69 | +0,44 |
| 1,6 k | +0,28 | +0,29 | +0,33 | +0,41 | +0,53 | +0,64 | +0,14 | +0,35 | −0,15 | −0,68 | +0,61 | +0,16 | +1,09 | +0,30 | +2,48 | +0,31 |
| 2 k | +0,27 | +0,27 | +0,28 | +0,33 | +0,42 | +0,62 | +0,23 | +0,26 | −0,21 | −0,56 | +0,02 | +0,13 | +0,84 | +0,26 | +2,26 | +0,23 |
| 2,5 k | +0,25 | +0,26 | +0,25 | +0,27 | +0,33 | +0,61 | +0,35 | +0,21 | −0,24 | −0,53 | −0,36 | +0,10 | +0,61 | +0,25 | +2,03 | +0,19 |
| 3,15 k | +0,16 | +0,18 | +0,18 | +0,18 | +0,21 | +0,49 | +0,46 | +0,14 | −0,25 | −0,54 | −0,74 | +0,03 | +0,33 | +0,19 | +1,68 | +0,15 |
| 4 k | +0,11 | +0,11 | +0,13 | +0,12 | +0,13 | +0,30 | +0,58 | +0,11 | −0,27 | −0,53 | −0,97 | +0,04 | +0,20 | +0,16 | +1,23 | +0,12 |
| 5 k | +0,07 | +0,07 | +0,07 | +0,08 | +0,08 | +0,14 | +0,69 | +0,09 | −0,28 | −0,46 | −1,17 | +0,05 | +0,24 | +0,13 | +0,67 | +0,10 |
| 6,3 k | +0,04 | +0,04 | +0,04 | +0,05 | +0,05 | +0,07 | +0,75 | +0,07 | −0,30 | −0,38 | −1,29 | +0,06 | +0,21 | +0,11 | +0,39 | +0,10 |
| 8 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,32 | −0,47 | −1,32 | +0,00 | +0,05 | +0,00 | +0,01 | +0,00 |
| 10 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,32 | −0,59 | −1,29 | +2,88 | +0,06 | +2,61 | +0,02 | +2,46 |
| **L_AE** | **+0,22** | **+0,22** | **+0,20** | **+0,14** | **+0,10** | **+0,24** | **+0,07** | **+0,24** | **−0,19** | **−0,37** | **+0,39** | **+0,09** | **+1,85** | **+0,21** | **+1,75** | **+0,31** |
| största band | +0,28 | +0,34 | +0,45 | +0,56 | +0,62 | +0,65 | +0,75 | +0,52 | −0,32 | −0,69 | +2,26 | +2,88 | +6,95 | +2,61 | +5,40 | +2,46 |
| band över 1 dB | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 1 | 11 | 1 | 14 | 1 |
| utfall | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | ska utredas | ska utredas | underkänd | ska utredas | underkänd | ska utredas |

## 5. Resterna i 800–6300 Hz: källmodellens direktivitet (diagnostik, inte i repot)

Efter paketet är avvikelsen i alla blad med personbilar utom blad 16 högst 0,09 dB i banden 25–630 Hz och
8–10 kHz, medan 800–6300 Hz har en jämn puckel: upp till +0,8 dB i de homogena bladen och i blad 10, 21, 24, och
mellan −1,0 och +0,4 dB i medvinds- och sidvindsbladen. Bandgränserna är exakt de i SP 2006:12 ekv. (2.12): den lägsta
delkällan (0,01 m) har horisontell direktivitet ΔL_H = (−2,5 + 4·|cos φ|)·cos ψ för 800 ≤ f ≤ 6300 Hz och 0 annars
(φ mot fordonets färdriktning). `buller.emission` har inte direktiviteten (`docs/buller/logg/B1-B5.md` punkt 3).

Prov utanför repot (`/home/claude/verktyg/ps1/direktiv.py`: ekv. 2.12 lagd på den lägsta delkällan, cos ψ = 1,
|cos φ| = sin θ för källpunkten i vinkeln θ från normalen), med kärnans förval:

| Band (Hz) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 21 | 22 | 23 | 24 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | +0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | +0,00 | −0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | −0,01 | −0,03 | −0,06 | +0,00 | −0,00 | +0,00 | +0,00 |
| 31,5 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | −0,00 | −0,03 | −0,06 | −0,00 | −0,00 | +0,00 | +0,00 |
| 40 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | −0,00 | +0,00 | +0,01 | −0,03 | −0,05 | −0,00 | −0,00 | +0,00 | −0,00 |
| 50 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,04 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,03 | −0,03 | −0,04 | +0,02 | +0,02 | +0,02 | +0,04 |
| 63 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,04 | +0,02 | +0,02 | +0,02 | +0,01 | +0,02 | +0,02 | +0,02 | +0,04 | −0,02 | −0,03 | +0,02 | +0,02 | +0,02 | +0,04 |
| 80 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,04 | +0,02 | +0,02 | +0,02 | +0,01 | +0,02 | +0,02 | +0,02 | +0,04 | −0,03 | −0,03 | +0,02 | +0,02 | +0,02 | +0,04 |
| 100 | +0,03 | +0,04 | +0,04 | +0,03 | +0,03 | +0,03 | +0,03 | +0,09 | +0,03 | +0,03 | +0,03 | +0,03 | +0,04 | +0,04 | +0,04 | +0,04 | −0,03 | −0,09 | +0,03 | +0,03 | +0,04 | +0,08 |
| 125 | +0,02 | +0,03 | +0,03 | +0,03 | +0,02 | +0,02 | +0,02 | +0,07 | +0,02 | +0,03 | +0,03 | +0,02 | +0,03 | +0,03 | +0,03 | +0,02 | −0,04 | −0,13 | +0,03 | +0,02 | +0,03 | +0,07 |
| 160 | +0,02 | +0,03 | +0,03 | +0,03 | +0,02 | +0,02 | +0,02 | +0,07 | +0,02 | +0,03 | +0,03 | +0,02 | +0,03 | +0,04 | +0,04 | +0,01 | −0,06 | −0,15 | +0,03 | +0,02 | +0,03 | +0,07 |
| 200 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | −0,02 | +0,01 | +0,01 | +0,02 | −0,04 | −0,11 | −0,19 | +0,00 | −0,01 | +0,01 | +0,00 |
| 250 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | −0,03 | +0,01 | +0,01 | +0,03 | −0,20 | −0,15 | −0,24 | +0,00 | −0,01 | +0,02 | +0,00 |
| 315 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,00 | +0,00 | +0,00 | −0,03 | −0,01 | −0,01 | +0,04 | −0,46 | −0,18 | −0,29 | +0,00 | −0,02 | +0,03 | +0,00 |
| 400 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,01 | +0,00 | +0,00 | −0,01 | −0,01 | −0,03 | +0,02 | −0,35 | −0,14 | −0,44 | +0,00 | −0,02 | +0,03 | +0,00 |
| 500 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,00 | +0,00 | +0,01 | −0,00 | −0,00 | −0,02 | +0,01 | −0,07 | −0,53 | +0,00 | −0,00 | +0,00 | +0,00 |
| 630 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,00 | +0,00 | +0,04 | +0,02 | +0,02 | −0,03 | +0,18 | −0,06 | −0,70 | +0,00 | +0,01 | −0,01 | +0,00 |
| 800 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,00 | +0,06 | +0,06 | +0,02 | +0,01 | +0,00 | +0,07 | −0,12 | −0,84 | +0,00 | +0,01 | +0,00 | +0,00 |
| 1 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,02 | +0,10 | +0,06 | +0,02 | +0,01 | +0,00 | −0,23 | −0,18 | −0,74 | +0,00 | +0,01 | +0,00 | +0,00 |
| 1,25 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,01 | −0,03 | +0,12 | +0,05 | +0,02 | +0,02 | −0,00 | +0,23 | −0,22 | −0,62 | −0,00 | +0,01 | −0,00 | +0,00 |
| 1,6 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,00 | −0,03 | +0,11 | +0,03 | +0,02 | +0,02 | −0,01 | −0,13 | −0,22 | −0,81 | −0,00 | +0,00 | −0,01 | +0,00 |
| 2 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,00 | −0,03 | +0,10 | +0,00 | +0,01 | +0,01 | −0,02 | +0,12 | −0,24 | −0,62 | −0,00 | −0,00 | −0,02 | +0,00 |
| 2,5 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,01 | −0,02 | +0,07 | −0,02 | −0,01 | +0,00 | −0,03 | +0,05 | −0,26 | −0,56 | −0,00 | −0,01 | −0,03 | +0,01 |
| 3,15 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,01 | −0,01 | +0,00 | +0,00 | +0,00 | +0,00 | −0,05 | +0,05 | −0,27 | −0,56 | +0,00 | −0,01 | −0,04 | +0,00 |
| 4 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,01 | −0,01 | +0,00 | −0,00 | +0,00 | +0,00 | −0,04 | +0,04 | −0,28 | −0,54 | +0,00 | −0,00 | −0,04 | +0,00 |
| 5 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,02 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,01 | −0,29 | −0,46 | +0,00 | −0,00 | −0,05 | +0,01 |
| 6,3 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,03 | +0,01 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,17 | −0,31 | −0,38 | +0,00 | −0,00 | −0,06 | +0,00 |
| 8 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,04 | +0,03 | +0,00 | +0,00 | +0,00 | +0,00 | +0,05 | −0,61 | −0,32 | −0,47 | +0,00 | +0,00 | +0,05 | +0,00 |
| 10 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,05 | +0,04 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,91 | −0,32 | −0,60 | +0,00 | +0,00 | −0,05 | +0,00 |
| **L_AE** | **−0,01** | **−0,01** | **−0,01** | **−0,02** | **−0,02** | **−0,01** | **−0,01** | **−0,01** | **−0,01** | **−0,02** | **+0,06** | **+0,01** | **+0,00** | **+0,00** | **−0,02** | **−0,02** | **−0,21** | **−0,40** | **−0,01** | **−0,01** | **−0,01** | **−0,01** |
| största band | +0,03 | +0,04 | +0,04 | +0,03 | +0,03 | +0,03 | +0,03 | +0,09 | −0,05 | +0,04 | +0,12 | +0,06 | +0,04 | +0,04 | +0,05 | −0,91 | −0,32 | −0,84 | +0,03 | +0,03 | −0,06 | +0,08 |
| band över 1 dB | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| utfall | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd |

Alla 20 blad med personbilar ligger då inom 0,06 dB i L_AE och 0,12 dB i tersband, utom 8–10 kHz i blad 16. Blad 14
blir godkänt (0,00 dB; största band +0,04 dB). Slutsats: **blad 14 är "ska utredas" på grund av källmodellen, inte
utbredningen**, och de homogena bladens +0,1 till +0,3 dB i L_AE har samma orsak. Blad 17 och 18 (tunga fordon)
ändras inte till det bättre (−0,21 och −0,40 dB); de har andra delkällor (ekv. 2.11 för 0,75 m-källan är inte
prövad). Detta är diagnostik: ingenting av det ligger i repot, och ändringen hör till B5 (se Önskade ändringar).

## 6. De tio godkända bladen i homogen atmosfär: skillnad mot i dag

De är fortfarande godkända. De ändras av två av förvalsbytena, båda redovisade ovan (2.3 D och avsnitt 3); de tre
andra bytena rör dem inte (inget av bladen har refraktion).

| Blad | Största ändring i ett tersband (dB) | Ändring av L_AE (dB) | L_AE före → efter (dB) | Största band före → efter (dB) |
| --- | --- | --- | --- | --- |
| 1 | 0,199 (125–630 Hz: −0,20 → 0,00) | +0,073 | +0,22 → +0,29 | +0,28 → +0,41 |
| 2 | 0,131 | +0,052 | +0,22 → +0,27 | +0,34 → +0,45 |
| 3 | 0,119 | +0,030 | +0,20 → +0,23 | +0,45 → +0,52 |
| 4 | 0,118 | +0,012 | +0,14 → +0,15 | +0,56 → +0,56 |
| 5 | 0,118 | +0,004 | +0,10 → +0,10 | +0,62 → +0,62 |
| 6 | 0,118 | +0,001 | +0,24 → +0,24 | +0,65 → +0,65 |
| 7 | 0,001 | 0,000 | +0,07 → +0,07 | +0,75 → +0,75 |
| 8 | 0,066 | +0,025 | +0,24 → +0,26 | +0,52 → +0,53 |
| 17 | 0,117 | +0,002 | −0,19 → −0,18 | −0,32 → −0,32 |
| 18 | 0,117 | +0,003 | −0,37 → −0,36 | −0,69 → −0,70 |

Av detta står Fresnelzonens klippning för allt utom högst 0,004 dB (luftabsorptionen, 10 kHz; 0,001 dB i blad
1–7). Största skillnad mot i dag: **0,20 dB i ett tersband (blad 1) och 0,07 dB i L_AE (blad 1)**. Efter, per
tersband:

| Band (Hz) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 17 | 18 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | +0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | +0,00 | −0,00 | −0,03 | −0,06 |
| 31,5 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,03 | −0,06 |
| 40 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,03 | −0,05 |
| 50 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,04 | −0,03 | −0,04 |
| 63 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,04 | −0,02 | −0,03 |
| 80 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,02 | +0,04 | −0,03 | −0,03 |
| 100 | +0,03 | +0,04 | +0,04 | +0,03 | +0,03 | +0,03 | +0,03 | +0,09 | −0,03 | −0,09 |
| 125 | +0,02 | +0,03 | +0,03 | +0,03 | +0,02 | +0,02 | +0,02 | +0,07 | −0,04 | −0,13 |
| 160 | +0,02 | +0,03 | +0,03 | +0,03 | +0,02 | +0,02 | +0,02 | +0,07 | −0,06 | −0,15 |
| 200 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,11 | −0,19 |
| 250 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,15 | −0,24 |
| 315 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,18 | −0,29 |
| 400 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,14 | −0,44 |
| 500 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,07 | −0,53 |
| 630 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,06 | −0,70 |
| 800 | +0,41 | +0,45 | +0,52 | +0,56 | +0,62 | +0,37 | −0,02 | +0,53 | +0,25 | −0,44 |
| 1 k | +0,40 | +0,43 | +0,48 | +0,55 | +0,60 | +0,54 | +0,02 | +0,52 | +0,01 | −0,49 |
| 1,25 k | +0,37 | +0,38 | +0,43 | +0,50 | +0,58 | +0,65 | +0,07 | +0,46 | −0,13 | −0,48 |
| 1,6 k | +0,35 | +0,35 | +0,37 | +0,43 | +0,53 | +0,64 | +0,14 | +0,39 | −0,15 | −0,68 |
| 2 k | +0,32 | +0,31 | +0,31 | +0,35 | +0,42 | +0,62 | +0,23 | +0,30 | −0,20 | −0,56 |
| 2,5 k | +0,27 | +0,29 | +0,27 | +0,28 | +0,34 | +0,61 | +0,35 | +0,24 | −0,24 | −0,53 |
| 3,15 k | +0,17 | +0,19 | +0,19 | +0,18 | +0,21 | +0,49 | +0,46 | +0,15 | −0,25 | −0,54 |
| 4 k | +0,11 | +0,12 | +0,13 | +0,13 | +0,13 | +0,30 | +0,58 | +0,12 | −0,27 | −0,53 |
| 5 k | +0,07 | +0,07 | +0,07 | +0,08 | +0,08 | +0,14 | +0,69 | +0,09 | −0,28 | −0,46 |
| 6,3 k | +0,04 | +0,04 | +0,04 | +0,05 | +0,05 | +0,07 | +0,75 | +0,07 | −0,30 | −0,38 |
| 8 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,32 | −0,47 |
| 10 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,32 | −0,60 |
| **L_AE** | **+0,29** | **+0,27** | **+0,23** | **+0,15** | **+0,10** | **+0,24** | **+0,07** | **+0,26** | **−0,18** | **−0,36** |
| största band | +0,41 | +0,45 | +0,52 | +0,56 | +0,62 | +0,65 | +0,75 | +0,53 | −0,32 | −0,70 |
| band över 1 dB | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| utfall | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd | godkänd |

Banden 25–630 Hz ligger nu inom 0,04 dB i blad 1–7 (förut upp till 0,20 dB). Att L_AE ändå ökar något i blad 1–3
förklaras i 2.3 D och avsnitt 5. `test_testsvit_n.py` håller fast dagens värden (`HOMOGENA_FALL_TOTALAVVIKELSE_DB`).

## 7. Ändringar i koden

| Fil | Ändring | Skäl | Verkan |
| --- | --- | --- | --- |
| `karna.py` | ΔL₈ läggs till med ⊕ (ekv. 332); `delmodell8` = `hog`/`lag`/`utelamna`; `antal_stralar`, `flera_markreflexer_db` i resultatet; version 0.3.0 | uppgift 1 | blad 9, 12–14, 16, 22 räknas |
| `karna.py` | `luftabsorption_monoton_db`, `STORSTA_A0_BANDFORMEL_DB`, valet `luftabsorption` | avsnitt 3 | 10 kHz i åtta blad; blad 16 körbart; homogena blad ≤ 0,004 dB |
| `karna.py` | förval `uppat="exakt"`, `fresnelzon="klippt"`, `skuggzonsvikter="ovre_band"`; nytt val `skuggzon` | 2.3 A–D | se 2.2 och 2.3 |
| `refraktion_mark.py` | `hojd_cirkelstrale`, `krokningsradie_m`, `antal_stralar_flera_markreflexer` borttagna (finns i `delmodell8.py`); `Skuggzonslasning` (ny); `skuggzonsavskarmning_db(…, niva)` | uppgift 1; 2.1 | högst 9·10⁻¹⁴ dB |
| `delmodell1.py` | skuggzonsgrenen omskriven så att markeffektens form och ΔL_SZ avgörs var för sig (`Skuggzonslasning.avgorande`); val för Q:s gångtid, 10/20·lg, kilens topp; förval `uppat="exakt"` | 2.3 A, C | blad 15: 1,85 → 0,01 dB; blad 23: 1,75 → 0,01 dB |
| `delmodell2.py` | `skuggzonsvikter="ovre_band"` (nytt, förval); förval `fresnelzon="klippt"`, `uppat="exakt"`; `skuggzon` förs vidare | 2.3 B, D | se 2.3 |
| `fresnel.py` | `reflexgeometri_skuggzon(…, radie)` och `reflexgeometri(…, radie)`: tre läsningar av R₀ | 2.1 nr 8 | ingen med förvalet |

Funktionerna i `fresnel.py` och `stralar_krokta.py` som tar `uppat` har kvar förvalet `"bokstavlig"` (de anropas
alltid med uttryckligt värde från delmodellerna); se Önskade ändringar.

**Tester.** Nya: `TestEnKallaMedDelmodell8` (2), `TestSkuggzonslasningar` (6), `TestSkuggzonsvikter` (2),
`TestLuftabsorptionMonoton` (5), två för ΔL₈ i kärnan. Rättade därför att beteendet ändrats, ingen försvagad:

- `test_refraktion_mark.py`: `test_flera_markreflexer_vagras` → `…_laggs_till_med_energisumma` och
  `…_bidrar_inte_under_troskeln` (prövar ⊕ mot delmodell 8 direkt, 10⁻¹² dB); `test_under_fl_…` prövar nu både
  `per_band` (som förut, 10⁻⁶ dB) och förvalet (under f_L = homogen + ΔL_SZ, 10⁻⁶ dB); `test_oklippt_zon_…` anger
  `fresnelzon="oklippt"` uttryckligen och prövar dessutom att förvalet ger delmodell 1 (10⁻⁹ dB);
  `test_valen_paverkar_bara_det_de_ska` prövar alla val åt båda håll; testerna för de borttagna funktionerna går
  mot `delmodell8.py`.
- `test_terrang_karna.py`: `test_refraktion_raknas_…` väntar inte längre `BullerFel` för medvind (N > 4);
  `test_ovre_grans` prövar nu att ΔL_t ligger mellan marktypernas egna markeffekter (exakt egenskap med klippt
  zon, 10⁻⁹ dB), att hård mark håller 20·lg 2 (10⁻³ dB) och att hela kärnan håller 6,05 dB (förut 6,03 dB; skälet
  står i 2.5).
- `test_testsvit_n.py`: skriven om, se nedan.

`python -m pytest tests/buller -q` → **1016 gröna, 2 överhoppade, 315 deltester**. `ruff check src/buller
tests/buller`: rent. `mypy src/buller`: rent (75 filer). `ruff format --check` på de ändrade filerna: rent.

**`tests/buller/test_testsvit_n.py`.** Varje fall står i precis en lista och testet kräver att listorna tillsammans
är alla 84 fall (`test_varje_fall_star_i_precis_en_lista`): `GODKANDA_FALL` (21: blad 1–13, 15–18, 21–24, alla med
L_AE inom 0,5 dB och tersband inom 1,0 dB), `KANDA_AVVIKELSER` (blad 14: ska utredas, L_AE högst 0,80 dB, tersband
högst 1,10 dB), `EJ_KORBARA` (58, var och en med en del av skälet som måste stå i kommentaren), `EJ_FACIT` (4).
Dessutom: antal per utfall exakt; inget underkänt; de tio homogena bladens L_AE-avvikelse får inte öka; bladen med
N > 4 får inte nämna "markreflexer" som hinder.

## Tolkningar

| Datum | Paket | Fråga | Val och skäl |
| --- | --- | --- | --- |
| 2026-11-21 | P-S1 | 1. Med vilken profil avgörs skuggzon i delmodell 1 (ersätter P-R2 tolkning 6)? | `delat`: markeffektens form (ekv. 120 eller första termen i ekv. 121) band för band med ξ(f); ΔL_SZ(f) i alla band när den frekvensoberoende profilen ger skuggzon. **Avgjord av testfall** (blad 15: 1,12 dB med `per_band`, 1,08 dB med `oberoende`, 0,01 dB med `delat`; blad 23: 0,87, 1,02, 0,01 dB). Textstöd: ekv. (122), ekv. (385), AV 1851/00 avsnitt 6.1. Alternativ finns kvar som val. |
| 2026-11-21 | P-S1 | 2. Vikterna i delmodell 2 i skuggzon (ersätter P-R2 tolkning 9). | `ovre_band`: f_H = 8 kHz-bandets frekvens, f_L = 0,8·f_H. **Avgjord av testfall**: `hog` ger 0,78 och 0,93 dB i blad 15 och 23 (band upp till 3,6 dB); `lag` och `ovre_band` 0,01 dB, men `lag` ligger 0,14–0,41 dB för lågt i 8 och 10 kHz där `ovre_band` ligger inom 0,05 dB. Svagt underlag för skillnaden lag/ovre_band (fyra bandvärden). |
| 2026-11-21 | P-S1 | 3. Uppåtbrytning (ersätter förvalet i P-R1 tolkning 3). | `exakt`. **Avgjord av testfall** (blad 10: 8 och 10 kHz −1,32/−1,29 dB med `bokstavlig`, +0,03/+0,04 dB med `exakt`; fyra band över 1 dB mot inget). Går emot ordalydelsen i avsnitt 5.5.4; vilar på ett blad. |
| 2026-11-21 | P-S1 | 4. Fresnelzonens klippning i delmodell 2 (ersätter förvalet i P-R2 tolkning 18). | `klippt`, som avsnitt 5.8 definierar zonen. **Avgjord av testfall**: banden 25–630 Hz inom 0,04 dB i blad 1–7, 9–15, 21–23 (med `oklippt` 0,07–0,20 dB, och 1,05, 1,28, 2,62 dB i blad 13, 14, 16); blad 14 och 16 underkända med `oklippt`. |
| 2026-11-21 | P-S1 | 5. Ekv. (287) för A₀ över formelns maximum (315,3 dB), där dämpningen avtar med avståndet och formeln slutar vara definierad vid ca 820 dB (ändrar B4:s tolkning "`BullerFel`"). | `monoton`: dämpningen hålls på formelns största värde (146,2 dB). **Avgjord av testfall**: 10 kHz-bandet i blad 9, 11, 12, 14, 21, 22, 24 från +1,5…+2,9 dB till inom 0,05 dB; blad 16 från ej körbar till godkänd. Rentonsdämpning över maximum ger samma värden inom 0,001 dB men är inte kontinuerlig. |
| 2026-11-21 | P-S1 | 6. ΔL_SZ med kilens topp enligt ekv. (43) när d ≤ d_SZ (läsningen `kiltopp="ekv43"`, inte förval). | 0 dB: kilens topp ligger då bortom mottagaren och ekv. (387) är inte definierad. |
| 2026-11-21 | P-S1 | 7. Delmodell 8: hf eller lf (P-8 tolkning 7). | hf, oförändrat; bekräftat av blad 9, 12, 13, 16, 22 (lf: blad 9 underkänt, +8,2 dB vid 25 Hz; blad 16 +7,9 dB vid 25 Hz). |
| 2026-11-21 | P-S1 | 8. P-R2 tolkning 1, 2, 4, 8 (Q:s gångtid, 20·lg, kilens topp, R₀). | Oförändrade; bekräftade av blad 10, 15, 23 (siffror i 2.2). P-R2 tolkning 3 (icke-reflekterande kil) är inte en avvikelse från AV 1106/07: avsnitt 5.7.5 säger det själv. |

## Önskade ändringar i delade filer

- **`src/buller/emission/` och `src/buller/testfall/korare.py` (B5): inför horisontell direktivitet**, SP 2006:12
  ekv. (2.12), på den lägsta delkällan för 800–6300 Hz. Köraren måste ge källmodellen vinkeln φ mellan färdriktningen
  och riktningen mot mottagaren (för källpunkten i θ från normalen är |cos φ| = sin|θ|) och ψ. Provet i avsnitt 5
  tyder på att detta ensamt gör blad 14 godkänt och för alla blad med personbilar till inom 0,06 dB i L_AE. Den
  vertikala direktiviteten (tabell 2.4) och ekv. (2.11) för tunga fordon är inte prövade.
- **`luftabsorption.py`**: flytta dit `karna.luftabsorption_monoton_db` och `STORSTA_A0_BANDFORMEL_DB`, och ändra
  B4-raden i `tolkningar.md` om ekv. (287).
- **`stralar_krokta.py`, `fresnel.py`**: förvalet för `uppat` är kvar på `"bokstavlig"` i strålfunktionerna
  (`stralar_krokta.py` ägs av P-R1). Kärnan och delmodell 1–2 anger alltid värdet uttryckligen, så inget resultat
  beror på det, men förvalen bör bli samma (`"exakt"`) när posten C är granskad.
- **`delmodell3.py`** anropar `markeffekt_plan_mark_db` med homogen atmosfär; när nästa paket ger den refraktion
  ska kärnans `uppat` och `skuggzon` skickas med. `delmodell3` har redan klippt zon som förval (P-3).
- **`docs/buller/tolkningar.md`**: de åtta raderna ovan; P-R2 tolkning 6, 9, 18 och förvalet i P-R1 tolkning 3 är
  ersatta.
- **`docs/buller/B4-uppdelning.md`**: delmodell 8 inkopplad; blad 9–16 och 21–24 godkända utom 14.
- **`docs/buller/logg/testsvit-N-2026-11-21.md`**: raderna för N18R001–024 ersätts av tabellen i avsnitt 4.
- **`STATUS.md`**: posten nedan (filen ägs av samordnaren; jag har inte skrivit i den).

## Olöst och osäkert

- **Blad 14** är "ska utredas" (−0,73 dB; −1,03 dB vid 800 Hz). Orsaken är med största sannolikhet källmodellens
  direktivitet (avsnitt 5), utanför detta paket.
- **Blad 16** (1000 m, u = 5 m/s) är godkänt men har de största resterna: −0,20 till −0,46 dB i 250–400 Hz, +0,4
  till +0,9 dB i 800–6300 Hz (med direktiviteten pålagd −0,23 till +0,23 dB), −0,61 och −0,91 dB i 8 och 10 kHz.
  Inte utrett. Kandidater: delmodell 8 (N = 56, ekv. 281:s språng), A′ för sneda källpunkter.
- **Blad 11**: +0,06 till +0,12 dB i 800–2500 Hz också med direktiviteten pålagd. Litet men systematiskt; inte
  utrett (kandidat: F_Δτ eller t_mean, P-R2 tolkning 12).
- **Blad 17, 18** (tunga fordon): −0,18 och −0,36 dB, som förut; källmodellen.
- De fem "avgjord av testfall"-posterna är inte granskade av någon som kan metoden. Svagast: C (emot textens
  ordalydelse, ett blad) och skillnaden `lag`/`ovre_band` i B (fyra bandvärden).
- Alla jämförelser i 800–6300 Hz är störda av den saknade direktiviteten. Jag har därför byggt valen på banden
  25–630 Hz och 8–10 kHz, och använt provet med direktivitet bara som kontroll.
- `delat` är prövad på plan mark med två källhöjdsklasser och ett mottagaravstånd (100 m) plus sneda källpunkter
  upp till 5,5 km. Skuggzon bakom skärm (delmodell 4–6, ekv. 182–184) har motsvarande fråga och är inte rörd.
- Vindriktningens konvention (P-R2) är oförändrad; att blad 12–15 nu stämmer inom 0,05 dB i 25–630 Hz bekräftar
  den.
- AV 1851/00 avsnitt 6.12 nämner att turbulensspridning (delmodell 7) också kan användas i en meteorologisk
  skuggzon över plan mark (h_e = h_SZ). AV 1106/07 har det inte i ekv. (332), och testfallen behöver det inte
  (blad 15 och 23 inom 0,05 dB i 8–10 kHz). Inte infört.
- Jag har inte skrivit i `STATUS.md` eller `PLAN.md` (arbetsreglerna: bara egna filer; `CLAUDE.md`/`AGENTS.md` ber om
  en post). Samordnaren behöver föra in posten nedan.

## Verktyg (utanför repot)

`/home/claude/verktyg/ps1/`: `kor.py` (kör sviten med valfria kärnval, sparar avvikelser per band), `jfr.py`,
`tabell.py`, `bred.py` (tabellerna ovan), `diag.py`, `diag2.py` (ΔL_t per källpunkt), `sprang.py` (2.4),
`likvardighet.py` (avsnitt 1), `luft.py`, `luftprov.py` (avsnitt 3), `direktiv.py` (avsnitt 5), `hybrid.py` (första
provet av `delat`). Körningar: `fore.json` (före), `steg1.json` (bara ΔL₈), `slut/*.json` (efter, en läsning i taget).

## Förslag till post i `STATUS.md`

```
## 2026-11-21 – B4/P-S1 – Plan mark med väder stämmer med testfallen
Gjort: delmodell 8 inkopplad i kärnan (ekv. 332), dubbletter borttagna ur refraktion_mark.py. Skuggzonen omläst från
  sidbild; fem förval bytta enligt regeln i tolkningar.md, alla märkta "avgjord av testfall" i P-S1.md: ΔL_SZ i alla
  band när den frekvensoberoende profilen ger skuggzon, vikterna i delmodell 2 i skuggzon, exakt uppåtkrökt stråle,
  klippt Fresnelzon i delmodell 2, luftabsorptionens bandformel hållen på sitt maximum. 10 kHz-felet förklarat
  (ekv. 287 avtar över A0 = 315 dB; källpunkten i 89° fick 2,9 dB luftabsorption vid 10 kHz).
Mätt: grupp N godkänd 21 (förut 10), ska utredas 1 (blad 14; förut 4), underkänd 0 (förut 2), ej körbar 58 (förut 64).
  Blad 15: 1,85 → 0,01 dB; blad 23: 1,75 → 0,01 dB; blad 16: ej körbar → 0,38 dB. Banden 25–630 Hz och 8–10 kHz inom
  0,09 dB i blad 1–15, 21–24. De tio homogena bladen: största ändring 0,20 dB i tersband, 0,07 dB i L_AE.
  1016 tester gröna, ruff och mypy rena.
Öppet: resten i 800–6300 Hz (upp till 1,0 dB) är källmodellens horisontella direktivitet (SP 2006:12 ekv. 2.12), inte
  införd i buller.emission; med den pålagd utanför repot ligger alla blad med personbilar inom 0,06 dB. De fem
  testfallsavgjorda valen ska granskas av någon som kan metoden. Blad 16 har rester på upp till 0,9 dB.
Nästa: direktiviteten (B5), sedan P-S2 (delmodell 3–7, 10–11 in i kärnan).
```
