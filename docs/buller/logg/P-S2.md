# P-S2 – Sammansättning steg 2: terräng och skärmar i Nord2000-kärnan

Datum: 2026-11-21. Paket P-S2 (samordnarens uppdrag). Grenen `buller-ps2` = det avbrutna passet
`buller-ps2-pagaende` (b8bb734, "PÅGÅENDE, OTESTAT") ihopslaget med `main` (1ea99eb).

**Läget i korthet.** Delmodell 3–7 är inkopplade i `Nord2000Karna` genom ekv. (332) med refraktion i markdelen
(`src/buller/nord2000/sammansattning.py`). Grupp N (80 fall ur RL 18-18): **33 godkända** (förut 22),
**16 ska utredas**, **4 underkända**, **27 ej körbara** (förut 58). De 22 tidigare godkända fallen är **bit för bit
oförändrade** (största ändring av en rest i något tersband: 0,000 dB). `nord2000_konform` är fortfarande `False`:
ingen grupp med icke-plan terräng eller skärm är helt godkänd, och rapportläget är spärrat enligt
`docs/BESLUT.md` (2026-11-21) tills tolkningarna granskats externt.

Inga val i passet är avgjorda med testfallen. De två ändringar passet gjorde i b8bb734 (avsnitt 2) följer
ordalydelsen; den ena ger ingen ändring alls i testfallen och den andra högst 0,03 dB i totalnivå.

Underlag: AV 1106/07 s. 59–67 (5.12), 70–76 (5.13.1), 82–83 (5.14.1), 99–100 (5.15), 118–119 (5.16),
132–144 (5.21–5.22), 148–149 (5.23.6), sidbilder för ekv. (134)–(139), (165)–(186), (193), (231), (347);
TC-101327 hela avsnitt 2; AV 1851/00 avsnitt 6.2–6.3 (s. 25–28).

## 1. Granskning av b8bb734

Varje del lästes mot metodtexten. Inget hittades som är avgjort med testfall; flera val var inte
dokumenterade (loggen som docstrings hänvisade till fanns inte) och står nu i avsnitt 3.

| Fil | Innehåll i b8bb734 | Bedömning |
| --- | --- | --- |
| `sammansattning.py` (ny) | Ekv. (332) med `_blanda`; terrängtolkning per profil; Δℓ med refraktion för T₁ (ekv. 301) och T₂ (ekv. 307); delmodell 4–6 med ⊕ ΔL₇; led med vikt 0 läses inte | Behållen. Raderna i ekv. (332) stämmer med sidbild s. 144. **Omskriven:** Δℓ för T₁₂ (tolkning 2). |
| `karna.py` | Spärrarna för skärm och icke-plan terräng borttagna; `berakna` anropar `terrangeffekt`; nya val `viktregel`, `vinkelregel`, `fresnelzon_delmodell3`, `overgangshojd` med oförändrade förval | Behållen. Förvalen är delmodellernas egna (P-3, P-4, P-6, valda ur texten). |
| `delmodell3.py` | Krökta strålar: ekv. (134)–(139) via FresnelZoneW (346)–(348), ekv. (140) med delmodell 1 med refraktion, kilen med CalcEqSSP/DirectRay (147)–(150), konvex vikt (153)–(154); fel i band med vikt 0 skjuts upp; `overgangshojd` | Behållen. Kontrollerad mot sidbild s. 61–66. Ekv. (150) används med ξ_R (tolkning 4). |
| `delmodell4.py` | Markdelen med krökta strålar (ekv. 165–174), F_Δτ, skuggzonsfallen (184)–(186), klippning av spegelvinkeln | Behållen utom **skuggzonens avgörande**, som b8bb734 tog från delmodell 1:s testfallsvalda förval `delat`; nu band för band enligt ekv. (167) och (184) (tolkning 1). |
| `delmodell5.py` | Strålarna S–T₁, T₁–T₂, T₂–R med CalcEqSSP/DirectRay (ekv. 193–195); markdelen som delmodell 4 | Behållen; h″_SCR per kant (tolkning 11). |
| `delmodell6.py` | Markdelen i tre områden med krökta strålar; Δθ i spegelvinklarna (231)–(232); F_Δτ över områdena; skuggzon (261)–(267) | Behållen. Δθ_Mi kontrollerat mot sidbild s. 99 (tolkning 10). |
| Testerna (6 filer) | Gränsfall svag refraktion → räta strålar, plan profil → delmodell 1/2 bit för bit, kontinuitet när skärmhöjden går mot 0, ekv. (332) omräknad ur leden | Behållna; alla går igenom. Två testklasser tillagda (avsnitt 4). |

## 2. Ändrat i passet

1. **Skuggzonen i delmodell 4–6 avgörs band för band** (`delmodell4.marksidor_refraktion`, som 5 och 6 också
   använder). Ekv. (167) och (171) anropar DirectRay med ξ_S(f), c_0S(f), och villkoret d′₁ > 0,95·d_SZ,1 före
   ekv. (184) gäller det d_SZ,1. Fältet `avgorande` i `Skuggzonslasning` används inte längre i skärmdelmodellerna.
   Utfall: inget fall byter klass; största ändring i totalnivå 0,03 dB (blad 83: 0,42 → 0,39; blad 73:
   0,26 → 0,28).
2. **Δℓ för den sekundära kanten T₁₂ med refraktion** (`sammansattning.gangvagsskillnader_refraktion`). b8bb734
   använde Δℓ₀. Avsnitt 5.21.3 säger att parametrarna räknas "in the same way as for the secondary screen … but T2
   is replaced by T12", alltså med Δℓ med refraktion som ekv. (307), här ur delmodell 5:s strålar (ekv. 193–195).
   Utfall: tillsammans med punkt 1 ovan; inget fall byter klass.
3. `tests/buller/test_testsvit_n.py`: nya listor (33/16/4/27), en vakt för de 22 tidigare godkända fallen
   (`FORE_P_S2_REST_DB`, rester före passet uppåt avrundade) och de 20 avvikande fallen med uppmätta gränser.

## 3. Tolkningar (förs in i `docs/buller/tolkningar.md`)

| Nr | Fråga | Val och skäl |
| --- | --- | --- |
| 1 | Skuggzon i delmodell 4–6: med vilken profil avgörs den? (s. 71–76, ekv. 167, 171, 184–186; motsvarande 217–219 och 261–267) | Band för band med ξ(f): texten anropar DirectRay med den frekvensberoende profilen och använder det d_SZ i villkoret. Delmodell 1:s förval `delat` (P-S1, avgjort av testfall, preliminärt) förs **inte** över. Alternativ: samma läsning som delmodell 1 (b8bb734). |
| 2 | Δℓ för T₁₂ med refraktion (5.21.3, s. 137–138) | Som ekv. (307) med delmodell 5:s strålar: T₁₂ på källsidan R′_S = R_S, R′_R = R_M, θ_1S, θ_1R; annars R_M, R_R, θ_2S, θ_2R. Går strålarna inte att räkna används Δℓ₀ (samma reservregel som b8bb734 har för T₂; r_Δℓ tar ändå max(Δℓ, Δℓ₀)). Alternativ: Δℓ₀ alltid (b8bb734). |
| 3 | T₂: delmodell 6:s strålar går inte att räkna (en topp under linjen från den andra, P-6 tolkning 11) | Δℓ₀ för T₂ (b8bb734). Ingen text. |
| 4 | Ekv. (150) är tryckt med ξ_S, c_0S | ξ_R, c_0R ur ekv. (149). AV 1851/00 avsnitt 6.3 (s. 27): mottagarsidan räknas med egen profil ur mottagarens höjd och skärmhöjden; ekv. (149) har annars ingen användning. |
| 5 | Ekv. (142)–(145) med källa/mottagare under sin kilytas förlängning (h′ < 0) | Beloppet av höjden: för ξ = 0 ger det den räta sträckan till toppen, som den homogena räkningen. Ingen text. |
| 6 | Ekv. (139): d̆_x = d − d_i + d_refl − d̆_refl | d läses som segmentets d′ (samma som den homogena räkningen, P-3). |
| 7 | Typ av segment med krökta strålar (s. 60–61): "If the segment is a convex segment h̆′ = h′" | Konvext i den meningen = källa eller mottagare på eller under segmentets förlängning (figur 17b); då h̆′ = h′. Annars FresnelZoneW:s korrigerade höjder. **Följd:** i skuggzon ger ekv. (347)–(348) ψ_G = 0 och h̆′ = 0, så varje sådant segment blir konvext (ekv. 136–137). Bokstavligt; se avsnitt 5.3. |
| 8 | MinConcaveHeight när d₁ > d (ändpunkten bortom mottagarens fotpunkt) | 0, samma skäl som P-3 tolkning 4 (d₁ < 0). |
| 9 | "Image point angles … may become larger than corresponding angles of the direct ray … shall be set equal" (s. 71, 99–100) | Jämförs med hela diffraktionsvinkeln inklusive Δθ (θ_Si mot θ_S, θ_Ri mot θ_R), och bara med refraktion: texten motiverar regeln med "strong upward refraction", och den geometriska spegelvinkeln θ′₁ beror inte av refraktionen. Homogen atmosfär oförändrad. |
| 10 | Delmodell 6, mittområdet: ekv. (231)–(232) skriver Δθ_Mi i båda topparna | Inte ett tryckfel: (231) tar Δθ_Mi ur ReflectedRay:s femte utdata (Δθ_S, vid T₁), (232) ur den sjätte (Δθ_R, vid T₂); kontrollerat mot ekv. (50) s. 31. |
| 11 | Ekv. (193) tryckt med ett enda h″_SCR för två kanter | h″_SCR (ekv. 22) för den kant strålen börjar eller slutar i, enligt avsnitt 5.5.2 ("If the ray is starting or ending at a screen top"); som P-6 gjorde. |
| 12 | Δθ_Si och Δθ_Ri i ekv. (165) | Ur ReflectedRay i den frekvensoberoende profilen: Δθ_Si = sjätte utdata (Δθ_R, i toppen), Δθ_Ri = femte (Δθ_S, i toppen); kontrollerat mot ekv. (50). |
| 13 | ΔL_d och ΔL_a för skärmade banor (öppen fråga 4 i B4-uppdelning) | R_SR (ekv. 8, 330): delmodell 4–6 är normerade mot fritt fält på R_SR (ekv. 163). Ej prövat mot text för luftabsorptionen; **öppet**. |
| 14 | Plan profil i flera segment | Genvägen `PLAN_TOLERANS_M` (10⁻⁶ m) behålls: metodens egen övergång ger r_flat = 1 där (provat i homogen atmosfär), men delmodell 3:s klassning går inte alltid att räkna med starkt krökta strålar. Numeriskt skydd, inte metod. |
| 15 | Delmodell 3 kan inte bilda en kil i ett band | Felet sparas och ges bara om bandet har vikt: r_flat < 1 och r_scr1 < 1. Ett band utan vikt läses inte (ekv. 319, 306). |

## 4. Fristående prövning

* `tests/buller/nord2000/test_sammansattning.py`: plan mark ger plan mark-modellen bit för bit, med och utan
  delmodell 8; metodens egen övergång ger r_flat = 1 för plan mark; ekv. (332) räknad om ur leden för tunn, låg,
  tjock skärm, två skärmar och dal, med och utan väder; skärmhöjd → 0 ger plan mark kontinuerligt (inget språng
  när r_scr1 blir > 0); turbulensspridningen höjer bara nivån. Nytt: (a) Δℓ för T₁₂ med svag refraktion
  (dt/dz = 10⁻⁷) lika med Δℓ₀ inom 10⁻⁴ relativt, `None` i homogen atmosfär, mindre i medvind; (b) skuggzonen i
  skärmdelmodellerna oberoende av `avgorande` och med ΔL_SZ < 0 i motvind (testet faller med b8bb734:s kod).
* Delmodell 3–6 (b8bb734): svag refraktion → räta strålar; fluktuerande refraktion rör bara koherensen;
  medvind/motvind ändliga; skuggzon på mottagarsidan.
* Metodens språng (byte av viktigaste kant, antal strålar i delmodell 8) jämnas inte ut.

## 5. Testsvit grupp N

Körd med `buller.testfall.korare.kor_alla(Nord2000Karna(), Vagemission(), testsvit/N_nord2000)`, källmodell
B5b. Körtid för grupp N: **17 s före, 390–640 s efter** (mätt två gånger på en delad maskin; delmodell 3–6 med
krökta strålar räknas per segment, band och källpunkt). Hela `pytest` påverkas eftersom `test_testsvit_n.py`
kör grupp N.

### 5.1 Per blad (rak väg)

| Blad | Före (main 1ea99eb) | Efter P-S2 | max \|rest\| total (dB) | max \|rest\| tersband (dB) |
| --- | --- | --- | --- | --- |
| 1 | godkänd | godkänd | 0,01 | 0,01 |
| 2 | godkänd | godkänd | 0,02 | 0,01 |
| 3 | godkänd | godkänd | 0,02 | 0,01 |
| 4 | godkänd | godkänd | 0,03 | 0,01 |
| 5 | godkänd | godkänd | 0,02 | 0,01 |
| 6 | godkänd | godkänd | 0,02 | 0,01 |
| 7 | godkänd | godkänd | 0,01 | 0,01 |
| 8 | godkänd | godkänd | 0,02 | 0,01 |
| 9 | godkänd | godkänd | 0,02 | 0,05 |
| 10 | godkänd | godkänd | 0,03 | 0,04 |
| 11 | godkänd | godkänd | 0,05 | 0,12 |
| 12 | godkänd | godkänd | 0,01 | 0,06 |
| 13 | godkänd | godkänd | 0,00 | 0,02 |
| 14 | godkänd | godkänd | 0,00 | 0,03 |
| 15 | godkänd | godkänd | 0,03 | 0,05 |
| 16 | godkänd | godkänd | 0,02 | 0,91 |
| 17 | godkänd | godkänd | 0,02 | 0,00 |
| 18 | godkänd | godkänd | 0,25 | 0,74 |
| 21 | godkänd | godkänd | 0,02 | 0,01 |
| 22 | godkänd | godkänd | 0,01 | 0,02 |
| 23 | godkänd | godkänd | 0,02 | 0,06 |
| 24 | godkänd | godkänd | 0,01 | 0,01 |
| 31 | ej körbar | ska utredas | 0,26 | 1,84 |
| 32 | ej körbar | ska utredas | 0,01 | 3,14 |
| 33 | ej körbar | ej körbar | – | – |
| 34 | ej körbar | ska utredas | 0,23 | 1,31 |
| 41 | ej körbar | ska utredas | 0,23 | 1,83 |
| 42 | ej körbar | ska utredas | 0,07 | 3,14 |
| 43 | ej körbar | ej körbar | – | – |
| 44 | ej körbar | ska utredas | 0,21 | 1,31 |
| 51 | ej körbar | godkänd | 0,03 | 0,13 |
| 52 | ej körbar | underkänd | 5,99 | 7,28 |
| 53 | ej körbar | ej körbar | – | – |
| 54 | ej körbar | godkänd | 0,01 | 0,02 |
| 61 | ej körbar | godkänd | 0,03 | 0,07 |
| 62 | ej körbar | underkänd | 5,88 | 6,96 |
| 63 | ej körbar | ej körbar | – | – |
| 64 | ej körbar | godkänd | 0,01 | 0,02 |
| 71 | ej körbar | godkänd | 0,27 | 0,74 |
| 72 | ej körbar | godkänd | 0,45 | 0,88 |
| 73 | ej körbar | ska utredas | 0,28 | 1,05 |
| 74 | ej körbar | godkänd | 0,03 | 0,73 |
| 81 | ej körbar | godkänd | 0,18 | 0,75 |
| 82 | ej körbar | godkänd | 0,19 | 0,88 |
| 83 | ej körbar | ska utredas | 0,39 | 1,22 |
| 84 | ej körbar | godkänd | 0,03 | 0,42 |
| 91 | ej körbar | ska utredas | 0,76 | 2,03 |
| 92 | ej körbar | ska utredas | 0,42 | 1,27 |
| 93 | ej körbar | underkänd | 1,11 | 2,23 |
| 94 | ej körbar | godkänd | 0,05 | 0,56 |
| 101 | ej körbar | ska utredas | 0,44 | 1,25 |
| 102 | ej körbar | underkänd | 2,84 | 4,39 |
| 103 | ej körbar | ej körbar | – | – |
| 104 | ej körbar | ska utredas | 0,58 | 0,90 |
| 111 | ej körbar | ska utredas | 0,41 | 3,75 |
| 112 | ej körbar | ska utredas | 0,70 | 5,14 |
| 113 | ej körbar | ska utredas | 0,35 | 4,27 |
| 114 | ej körbar | ska utredas | 0,39 | 4,00 |
| 121 | ej körbar | ej körbar | – | – |
| 122 | ej körbar | ej körbar | – | – |
| 123 | ej körbar | ej körbar | – | – |
| 124 | ej körbar | ej körbar | – | – |

Kurvig väg (10), stadsgata (4) och årsmedel (4) är ej körbara som förut (köraren).

### 5.2 Fall som faller och trolig orsak

* **Medvind över dal, blad 52, 62, 102 (underkända; totalnivå +6,0, +5,9, +2,8 dB; tersband upp till +7,3 dB
  i 0,8–2,5 kHz).** Kärnan ligger över facit. Spårat för blad 52, mittersta källpunkten, delkälla 0,01 m:
  delmodell 3 ger +5,3 dB vid 1,6 kHz mot −6,2 dB utan vind (blad 51, godkänt). Orsaken är vikterna: med
  CalcEqSSP(0,01 m, 1,5 m) ger medvinden ψ_G = 0,12 rad (rät stråle 0,016 rad), Fresnelzonen för vägbanans
  förlängning krymper från 0–52 m till 0–2 m (ekv. 134, 346–348), vägbanan (hård, 1,75 m) får vikten 0,88
  i stället för 0,03, och det konvexa segmentet vid vägkanten (ekv. 153–154, samma krökta strålar) får vikten 0
  i stället för 0,37. Det är metodtexten läst bokstavligt med P-R1:s profil; inget fel i koden är hittat. Möjliga
  förklaringar, ej prövade: minsta höjd i CalcEqSSP för segment (källan 0,01 m över vägbanan ger en mycket
  brant ekvivalent profil), eller att referensprogrammet använder den frekvensberoende profilen
  (CalcEqSSPGround) i ekv. (135). Ingen av dem har stöd i texten än.
* **Två skärmar i medvind, blad 93 (underkänt; +1,1 dB, tersband +2,2 dB över 1,25 kHz).** Samma tecken och
  frekvensområde som blad 91–92 (ska utredas). Ej utrett.
* **Upphöjd väg, blad 31–34, 41–44 (ska utredas; totalnivå ≤ 0,26 dB, tersband 1,3–3,2 dB över 1,6 kHz).**
  Kärnan över facit i höga band, mest i medvind. Delmodell 7 påverkar inte blad 31 (provat utan ΔL₇:
  oförändrat). Ej utrett.
* **Kulle, blad 111–114 (ska utredas; totalnivå ≤ 0,71 dB, tersband 3,7–5,2 dB i 125–500 Hz).** Även utan vind
  (blad 111: bara fluktuationer). Delmodell 2+3+4+5+7 används; dippen ligger fel. Ej utrett.
* **Tunn och tjock skärm, blad 73, 83; dal 101, 104; två skärmar 91, 92 (ska utredas; tersband 0,9–2,0 dB,
  totalnivå ≤ 0,8 dB).** Överskattning över ca 1,6 kHz i de flesta. I blad 73 ändrar ΔL₇ tecknet på resten i
  4–10 kHz (provat utan ΔL₇: −1,8 dB i stället för +0,7 dB), så turbulensspridningens storlek är en kandidat.
  Inget är ändrat.
* **Ej körbara, blad 33, 43, 53, 63, 103 (motvind över icke-plan terräng).** Delmodell 3 kräver en kil för ett
  konvext segment med vikt > 0 som inte går att bilda (parallella ytor, eller β < π). Följer av tolkning 7: i
  skuggzon blir varje segment med källa och mottagare ovanför konvext. Metoden säger inte vad som gäller då
  (P-3 tolkning 13: `BullerFel`).
* **Skog, blad 121–124 (ej körbara).** Delmodell 10 finns men spridningszonen kan inte uttryckas i
  `Terrangprofil` (KB1/v1); kräver ny kontraktsversion. Enligt BESLUT 2026-11-21 ska skog vara av i rapportläget.
  Delmodell 11 (reflexer) hör till stadsgatan och köraren; inte inkopplad.

## 6. Öppna frågor

1. Blad 52/62/102: höjden i CalcEqSSP för segmentens Fresnelzon (källa 0,01 m) – vad gör referensprogrammet?
   Frågan bör ställas till den som granskar tolkningarna (Kunskapscentrum om buller), inte avgöras med facit.
2. Tolkning 7: alla segment konvexa i skuggzon, vilket gör motvindsfallen ej körbara.
3. Överskattning över 1,6 kHz i skärm- och vägbankfallen: turbulensspridningen (delmodell 7) och kilytornas
   reflektion (TC bilaga G) är kandidater.
4. Körtiden för grupp N (minuter); B7 (snabbkärnan) eller cache per profil.
5. Sträckan R i ΔL_a för skärmade banor (tolkning 13).
