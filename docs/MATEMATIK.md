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
Ägare: öppen.

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
