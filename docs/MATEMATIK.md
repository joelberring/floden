# Matematiska frågor

Frågor som hör till forskningsdelen (Samuel och person X). Agenter samlar frågor och observationer här i stället
för att hitta på teori. Varje post: fråga, varför den spelar roll, vad som observerats i data, vem som äger den.

## Q1 – Exakt händelsestyrd fyllning (tropisk algebra)

Vid konstant regn är inflödet till en sänka styckvis linjärt i tiden och kombineras med uppströms överfall genom
max, min och addition. Under vilka antaganden kan tidpunkten då varje sänka svämmar över räknas exakt, och med
vilken komplexitet? Var bryter det (överfall med flöde ∝ h^1,5, tidsvarierande regn)?
Ägare: öppen (Samuel tillfrågas; alternativt person X eller annan).

## Q2 – Flerparameterpersistens under osäkerhet

Sänkor och flödesvägar filtreras i två parametrar samtidigt: höjd och osäkerhet, eller höjd och regnvolym.
Hur avgörs vilka strukturer som är robusta, och kan förenklingen ges en felgräns? Verktyg: graderade moduler,
Hilbertfunktioner, fria upplösningar.
Ägare: Samuel (föreslagen huvuddel).

## Q3 – Fel i hybriden

Hur stort är felet när djupa samlingar räknas som noder med plan vattenyta och resten i 2D, jämfört med full 2D?
Ägare: person X.

## Q4 – Inkrementell omräkning

Vilka datastrukturer gör att en lapp bara kräver omräkning av ett delträd och det som ligger nedströms?
- **Hierarkisk rutkomprimering via randportar & dynamiska träd:** *Top trees* / dynamiska sammanslagningsträd kombinerat med ett *Schur-komplement*-tänk per 500 × 500 m-ruta (där varje ruta endast exponerar sina randportar över rutgränsen) för helstadsskalning (hela Stockholm) med lokal omräkning av projektlappar på sekunder.
Ägare: öppen.

## Q5 – Fysikbevarande modellreduktion för 2D-realtid (M7)

Kan en masskonserverande *Reduced Basis / POD*-modell för 2D-delzoner (deterministisk, med känd *a posteriori* felgräns) ge realtidsrespons för kritiska gator utan svart-låda-AI för djup/flöden (`AGENTS.md` regel 1)?
Ägare: öppen (person X / forskningsspår M7).

## Fördjupade forskningsspår (tillägg 2026-11-22)

- **Q1 – Network Calculus / min-plus-algebra i sänkträdet:**
  - Exakthetsvillkor och tidskomplexitet för händelsestyrd fyllning i min-plus-semiringen (*Network Calculus*: sänka = buffert, flödesväg = fördröjning, överfall = kapacitetsbegränsning).
  - Felgräns mellan min-plus-modellen och det fulla olinjära ODE-systemet med bredskronsöverfall $Q \propto (h - h_{\text{öv}})^{3/2}$ (*multirate*-växling på enstaka olinjära noder).
- **Q2 – Full 2-parameterpersistens över $k[x, y]$ (Samuels huvudspår):**
  - Utöver den praktiska bigraderade bifurkationsdetektorn (`src/sankgraf/bifurkation.py`): minimala fria upplösningar, Hilbertfunktioner och Betti-tal ($\beta_0, \beta_1, \beta_2$) för $H_0(G_{h,\sigma}; k)$.
  - Bevisbar felgräns i interleaving-avstånd / maxdjup vid grafförenkling.
  - `sankgraf.bifurkation.exportera_bifiltrering_json(...)` exporterar nu ett rent, GIS-fritt JSON-dataset (noder + 4-grannsadelkanter med höjd och osäkerhet) så att Samuel kan köra $k[x, y]$-experiment direkt i Python eller Macaulay2 på ett litet Stockholms-exempel.

## Observationer från data

### 2026-11-20 – T03 – Händelsestyrd fyllning, skibord och enhetssvar (Q1)
- **Exakthet vid styckvis konstant/linjärt regn:** I den kinematiska överfallsgränsen (omedelbart överfall vid `overfall_rh2000`, utan fördröjning) blir volymutvecklingen mellan händelser exakt styckvis linjär (konstant regn) resp. styckvis kvadratisk (linjärt regn). På A1–A3 ger detta maskinprecision (`< 1e-12` relativt fel) mot grafens analytiska tider och `0,00 m` avvikelse mot `statisk_fyllning` på A9 (282 noder). Antalet diskreta händelser växer högst som `O(|V| + |I|)` (på A3 krävs endast 3 händelsehopp över 13 000 s).
- **Var exaktheten bryter (skibord Q ∝ Δh^1,5):** Med bredskronsöverfall (`B = 2,0 m`, `C_d = 1,7`) på A2 och A3 uppstår en transient överhöjd (`Δh ≈ 1,0–1,7 cm` vid 72 mm/h) som lagrar vatten ovanför tröskeln. Det fördröjer nedströms överfall i A2 med `+51,8 s` (`+1,1 %`) och syskonsammanslagningen i A3 med `+66,8 s` (`+2,1 %`). Lösningen konvergerar monotont mot den händelsestyrda gränsen när `B → ∞`. Fråga till Q1: kan en enkel effektiv tidsfördröjning eller övre/undre gräns från bredskronsöverfall ges analytiskt?
- **Enhetssvar före första överfall:** Före första överfall är varje lövnod linjär och tidsinvariant; diskret faltning med exakt linjär-reservoar-impulssvar stämmer till `1,26e-14` relativt fel mot tidsstegning och gav `2,7×` snabbare beräkning för 20 regnserier (A3).
- **Känslighet för vattendelare på platta vallar (DEM):** I A2 och A3 dränerar inre celler på 2 celler breda flata vallar fysiskt in i bassängerna, vilket skiftar avrinningsytan ~1–8 % jämfört med en naiv handformel som exkluderar vallcellerna. Öppen fråga för Q2: hur påverkas fyllnadstider av osäker vattendelare på flata ytor?

### 2026-11-20 – T04 – Inkrementell omräkning av sänkgraf (Q4)
- **Slutenhet under sammanslagningsträd och överfallslänkar:** I Barnes (2020) Fill–Spill–Merge räcker det inte att enbart räkna om ett delträd och dess nedströmskomponenter; även en *uppströms* nod `U` med `U.ut_till_id = B` påverkas topologiskt om en ändring i `B` höjer `B`:s utloppssadel `Z_hav(B)` över sadeln `w(U, B)` (vilket omvandlar ett riktat överfall till en syskonsammanslagning). En oberörd komponent `T` är garanterat oförändrad omm (i) ingen flödesriktning lämnar det omräknade området in i `T` och (ii) varje gränssadel `w` mot `T` strikt överstiger `rot(T).overfall_rh2000`.
- **Likahöjdsplatåer över snittet (`HAV` vs sänka):** När en platt platå korsar gränsen mot frysta `HAV`-celler ger trunkerad multi-source BFS upp till ~8 cm djupfel; att dra in hela den 4-sammanhängande likahöjds-`HAV`-platån genom fixpunktsexpansion återställer bitexakt ekvivalens (`0,00e+00 m` fel).
- **Skalning & öppen fråga:** På 256×256 celler med 16 kvarter återanvändes `23/24` noder (`94,5 %` frysta celler) med `9,28×` tidsvinst (`26,7 ms` vs `247,4 ms`). Fråga till Q4: kan dynamiskt MST (link-cut / Euler-tour tree över sadelkanter) även spara omräkning när två komponenter bara möts vid en hög gemensam sadel?

### 2026-11-20 – T05 – 2D lokal tröghet, subgrid och hybridkoppling (Q3)
- **Varför hybriden ger 12,9–20,0× speedup (CFL-frikoppling):** I en explicit/lokal-tröghets-2D-modell styrs det globala CFL-tidssteget `Δt ∝ Δx / sqrt(g · h_max)` av stillastående djupa sänkor (`h ≈ 0,5 m`). När djupa samlingar lyfts ut till grafnoder styrs 2D-tidssteget av det grunda skiktflödet på sluttningar/gator (`h ≈ 0,005–0,03 m`), vilket ger `2–4×` större `Δt` redan vid `subgrid=1` och `12,9–20,0×` snabbare körning vid `subgrid=4`.
- **Fel före vs under kraftigt överfall:** Före överfall ger hybriden `0,01–0,19 cm` MAE mot full 2D och `IoU = 1,0000`. Under pågående kraftigt överfall över smal tröskel (A1, 720 mm/h) uppstår `+1,97 cm` (`subgrid=1`) resp. `+3,60 cm` (`subgrid=4`) extra överhöjd i noden jämfört med full 2D. Fråga till Q3 (person X): kan nod–2D-kopplingen kalibreras med en effektiv utloppslängd eller lokal delstegning för att halvera överfallsfelet vid grov subgrid?
- **Numerisk stabilitet:** Ren Bates (2010) (`theta=1,0`) utvecklade schackbrädesvängningar i släta bassänger (`n=0,015`) med lodräta kanter; SFINCS/de Almeida m.fl. (2012) `theta=0,9` eliminerade dem helt. I hybridkopplingen gav exponentiell jämviktsrelaxation + gemensam positivitetsbegränsare svängningsfri koppling och volymfel `< 2e-13`.

### 2026-11-22 – Vatten – Slutsänkor (slutna kvarter) och avtappning i grafen (Q1, Q4)
- **Slutsänka i sammanslagningsträdet:** ett område som byggnader helt omger får ingen sadel mot randen; Kruskal-
  steget slutar då med en rot per sådant område. Vi ger roten ett *hinder* (lägsta byggnadscell i fyrgrannringen,
  höjden i `mark.tif`) som övre gräns för volymkurvan, och räknar vatten över hindret som utflöde. Det är ett
  modellval, inte teori: den verkliga vägen ut är en okänd tröskel (port, dörr, brunn). Mätt: graf och 2D (lokal
  tröghet, byggnader som väggar) ger samma volym i gården (3,6 m³) och samma vattenyta inom 0,14 mm (2D:s film på
  sluttningen), på plant golv exakt samma djup (50 mm, A10).
- **Avtappning per nod:** grafen tappar en nod med kapaciteten summerad över nodens celler så länge den har vatten
  eller tillrinning (`dynamik.handelsestyrd.avtappning_per_nod_m3_s`). 2D tappar varje *våt cell* för sig. På plant
  golv är det samma sak (A11: graf och 2D ger 15,0 mm topp och tomt efter 30 min). **Fråga (Q1, Samuel):** på
  sluttande golv är bara den våta delen av ytan avtappad i 2D, medan grafen räknar hela avrinningsytan. Ska
  avtappningen i grafen följa ytkurvan `A(h)` (bara vattenytan) i stället, och är händelsestyrd lösning då fortfarande
  exakt (dV/dt = r·A_avr − k·A(h(V)) är inte längre linjär i V mellan händelser)? Vi har inte valt; dagens
  graf överskattar avtappningen när gården bara delvis är våt.
- **Inkrementell omräkning (Q4):** en ändring som rör en slutsänka eller gränsar till ett nytt instängt område ger i
  dag full omräkning (hindret och ringen är globala egenskaper som den lokala sadelsökningen inte ser). Övriga
  ändringar bevarar slutsänkorna orörda (mätt: noden identisk före och efter, samma fyllning som full omräkning).
  **Fråga (Q4):** finns en lokal regel för när ett nytt hus stänger en port, så att bara de berörda komponenterna
  behöver räknas om?

### 2026-11-22 – Vatten – Takvatten och avtappning i statisk fyllning
- **Takvatten är en linjär omfördelning:** varje byggnads takvolym flyttas med fasta vikter (summa 1) till
  markceller. I grafen blir det en extra avrinningsyta per nod, så den händelsestyrda lösningen förblir exakt
  (mätt: A12 och tråget i A13 till maskinprecision, volymfel < 1e-13). I 2D är det en källterm per cell.
- **Graf mot 2D (A13):** grafen lägger vattnet direkt i nodens volym; 2D låter det rinna över markcellerna, där en
  film blir kvar och en del rinner ut över rutkanten innan det når tråget (−1,6 % i långsidornas tråg, −9,9 % i
  gavlarnas, som bara får eget regn från sluttningarna). Ingen ny teori behövs; observationen hör till Q3.
- **Avtappning i statisk fyllning (fråga till Samuel, Q1):** utan tid går det inte att veta hur mycket av en nods
  avtappningskapacitet som hinner användas på vatten som kommer som överfall från andra noder. Vi tappar bara lövens
  direkta tillrinning (högst kapacitet × varaktighet), vilket är en undre gräns för avtappningen. Finns en exakt
  statisk motsvarighet (t.ex. ordna överfallen efter den händelsestyrda lösningens tider), eller ska statisk
  fyllning med avtappning alltid ersättas av dynamiken?

