# Logg B5b – källmodellens direktivitet

2026-11-21. Paket B5b. Läst: `AGENTS.md`, `tasks/B00-buller-oversikt.md`, `docs/buller/KONTRAKT.md`,
`docs/buller/logg/B1-B5.md`, `docs/buller/logg/P-S1.md` (avsnitt 5 och "Olöst") och regeln sist i
`docs/buller/tolkningar.md`.

## Läget i korthet

- Källmodellen har nu direktivitet enligt SP Rapport 2006:12 avsnitt 2.5: horisontell för delkällan på
  0,01 m (ekv. 2.12) och 0,75 m (ekv. 2.11), vertikal för alla tre delkällor (tabell 2.4), summerade
  enligt ekv. (2.9). Rapporten har alltså en vertikal direktivitet, och den är införd.
- Köraren ger källmodellen riktningen (φ, ψ) från varje källpunkt mot mottagaren. Utan riktning räknas
  källan som rundstrålande, och det står då i resultatets kommentar.
- Testsvit grupp N: **22 godkända** (förut 21), **0 ska utredas** (förut 1), 0 underkända, 58 ej körbara,
  4 ej facit. **Ett fall byter utfall: N18R014 (sidvind), från "ska utredas" till "godkänd"**
  (L_AE −0,73 → −0,00 dB; största tersband −1,03 dB vid 800 Hz → −0,03 dB vid 400 Hz).
- Alla 20 blad med personbilar ligger inom 0,06 dB i L_AE. Blad 17 (kategori 2) stämmer inom 0,0005 dB
  i alla 27 band. Blad 18 (kategori 3) stämmer inte: −0,25 dB i L_AE, −0,74 dB vid 800 Hz. Orsaken är
  med stor sannolikhet antalet axlar, inte direktiviteten (se "Diagnostik").
- Inget tal är anpassat. Ingen korrektionsfaktor är införd. Det enda valet mellan läsningar
  (`horisontell_verkan`) gjordes ur texten innan sviten kördes och är inte ändrat efteråt.
- En rättelse av P-S1: faktorn i ekv. (2.10b), (2.11) och (2.12b) är **√cos ψ**, inte cos ψ. Rottecknet
  syns bara i sidbilden; `pdftotext` tappar det.
- `tests/buller/test_testsvit_n.py` slår larm i tre tester, som väntat. Filen är inte ändrad; vad som
  behöver föras in står under "Till samordnaren".

## Filer

| Fil | Ändring |
| --- | --- |
| `src/buller/emission/direktivitet.py` | ny: `Kallriktning`, funktionsformerna, inläsning av `[direktivitet]` |
| `src/buller/emission/modell.py` | `riktning` i `ljudeffekt_db` och `ljudeffekt_per_fordon_db`; `direktivitet_db`; `stoder_direktivitet`; valet `horisontell_verkan` |
| `src/buller/emission/kontroll.py` | avsnittet "Direktivitet" i kontrollutskriften |
| `src/buller/emission/__init__.py` | exporterar `Kallriktning` och `Direktivitetsdata` |
| `src/buller/emission/data/nord2000_road_v3.toml` | ny dataversion 3 = version 2 plus `[direktivitet]`; `nord2000_road_v2.toml` är borttagen |
| `src/buller/testfall/korare.py` | `EmissionsmodellMedDirektivitet`, `riktning_for_kallpunkt`, `vinkel_mot_fardriktning_rad`, `direktivitet=` i `berakna_rak_vag`, `kor_fall`, `kor_alla`; `Berakning.direktivitet` och `.anmarkning` |
| `tests/buller/emission/test_direktivitet.py` | ny: 16 tester |
| `tests/buller/testfall/test_korare.py` | ny klass `TestRiktningTillKallmodellen`: 8 tester |
| `docs/buller/kontroll/emission_tabeller.md` | genererad om (dataversion 3, direktivitetens tabeller) |

Övriga tabeller i datafilen är oförändrade från dataversion 2 (filen är kopierad och utökad, inte
omskriven).

## Underlag och avläsning

Källa: SP Rapport 2006:12 (rev. 2006-10-17), avsnitt 2.5 (s. 13–16) och sammanfattningen (s. 7).
Sidbilder i `/home/claude/verktyg/sidor/B5b/`: `sp-07.png`, `sp-14.png`, `sp-15.png`, `sp-16.png`
(150 dpi) och utsnitt i 300 dpi: `ekv210-14.png`, `ekv212-15.png`, `tab24-16.png`.

| Vad | Avläsning 1 | Avläsning 2 | Utfall |
| --- | --- | --- | --- |
| Tabell 2.4, 24 celler (form och koefficient) | sidbild s. 16, 300 dpi, rad för rad | `pdftotext -layout`, maskinellt tolkad och jämförd med datafilen (`tabellkoll.py`) | lika, 0 skillnader |
| Tabell 2.4 | kontrollutskriften `emission_tabeller.md` | sidbilden, rad för rad | lika |
| Ekv. (2.12a–b): −2,5; 4; 630/800/6300/8000 Hz | sidbild s. 15 | pdftotext | talen lika; rottecknet bara i bilden |
| Ekv. (2.12) | datafilens värden: +1,50 dB vid 0°, +0,07 dB vid 50°, −2,50 dB vid 90° | figur 2.4, kurvan "New": 1,5 vid 0°, nollgenomgång vid ca 50°, −2,5 vid 90° | stämmer |
| Ekv. (2.11): 1,546; 1,425; 0,22; 0,6 | sidbild s. 14 | pdftotext | talen lika; rottecknet bara i bilden |
| Ekv. (2.11) | datafilens värden: +3,42 dB vid 0°, +0,60 vid 90°, −9,25 vid 180° | figur 2.3: ca 3,5 vid 0°, ca 0,6 vid 90°, ca −9,5 vid 180° | stämmer |
| Vad som gäller för vilken delkälla | sammanfattningen s. 7 (sidbild) | avsnitt 2.5 s. 14–16 | stämmer överens |

Det `pdftotext` inte återger: rottecknet över cos ψ i ekv. (2.10b), (2.11), (2.12b) och i meningen
efter ekv. (2.12). P-S1 avsnitt 5 skrev formeln med cos ψ; det är alltså fel läst där, men utan
betydelse för provet (cos ψ = 1 användes).

Kunskapscentrum om bullers användarhandledning (version 1.0, 2026-01-31) nämner inte direktivitet. Det
som rör saken är avsnitt 4.7: en emissionslinje per körfält, mitt i körfältet. Varje emissionslinje har
då en färdriktning, och den behövs för ekv. (2.11); se "Till samordnaren".

## Vad som är infört

∆L(f, φ, ψ) = ∆L_H(f, φ) + ∆L_V(f, ψ) (ekv. 2.9), i dB relativt den passageintegrerade ekvivalenta
ljudeffektnivån. φ är vinkeln i horisontalplanet mellan fordonets färdriktning och riktningen mot
mottagaren, ψ riktningens vinkel över horisontalplanet (figur 2.2).

| Delkälla | Horisontell direktivitet | Vertikal direktivitet |
| --- | --- | --- |
| 0,01 m (alla kategorier) | ekv. (2.12): (−2,5 + 4·\|cos φ\|)·√cos ψ i 800–6300 Hz, 0 i övriga band | tabell 2.4, kolumn 1 |
| 0,30 m (kategori 1) | ingen (ekv. 2.10c) | tabell 2.4, kolumn 2 |
| 0,75 m (kategori 2 och 3) | ekv. (2.11): tredjegradspolynom i (π/2 − φ) gånger √cos ψ, alla band | tabell 2.4, kolumn 3 |

Tabellen står i `docs/buller/kontroll/emission_tabeller.md` för avprickning. Alla koefficienter ligger
i datafilen; i koden finns bara funktionsformerna.

**Gränssnitt.** `Vagemission.ljudeffekt_db(..., riktning=Kallriktning(φ, ψ))`, där ψ är ett tal eller
ett värde per delkälla i resultatets radordning. Med `riktning` är resultatet nivån för den
rundstrålande punktkälla som ger samma ljudtryck i den riktningen, vilket är så AV 1849/00 definierar
L_W för en riktad källa. Utan `riktning` är resultatet bit för bit som förut (prövat: hela sviten med
`direktivitet=False` mot körningen före ändringen, största skillnad 0,0 dB).
`Vagemission.direktivitet_db(riktning)` ger själva tillägget per bullertyp, delkälla och band.

**Köraren.** `Emissionsmodell` är oförändrad. `EmissionsmodellMedDirektivitet` är samma gränssnitt
plus `stoder_direktivitet` och parametern `riktning`. `berakna_rak_vag(..., direktivitet=True)` ger
riktningen till modeller som stöder den. I annat fall, eller med `direktivitet=False`, räknas källan
som rundstrålande och `Fallresultat.kommentar` säger varför ("avstängd i anropet" eller
"emissionsmodellen tar inte emot riktningen"). `Berakning` har fälten `direktivitet` och `anmarkning`.

**Vinklarna i köraren.** Källpunkten i vinkeln θ från normalen (sedd från mottagaren) får φ = π/2 + θ,
alltså |cos φ| = sin|θ| som P-S1 angav. ψ räknas per delkälla ur terrängprofilen mot källpunkten:
tan ψ = (mottagarens höjd − delkällans höjd, markhöjder inräknade) / horisontellt avstånd.

## Tolkningar (ska in i `docs/buller/tolkningar.md` av samordnaren)

| Nr | Fråga | Val | Skäl och alternativ |
| --- | --- | --- | --- |
| 1 | Verkar den horisontella direktiviteten på hela delkällans effekt eller bara på den bullertyp texten motiverar den med? | `horisontell_verkan = "delkalla"` (hela delkällan). Valt ur texten före körning. | Avsnitt 2.5 och sammanfattningen tilldelar direktiviteten *punktkällan*. Alternativet `"bullertyp"` (ekv. 2.12 bara på rullbullret, ekv. 2.11 bara på drivlinebullret) finns som val. Båda körda, se tabellen "Läsningar sida vid sida". Testfallen bekräftar förvalet (blad 17: största band 0,000 mot 0,053 dB; blad 7: 0,006 mot 0,111 dB). Förvalet är inte bytt, så posten är inte "avgjord av testfall". |
| 2 | Gäller ekv. (2.12) den lägsta delkällan också för kategori 2 och 3? | Ja. | Sammanfattningen säger "the lowest point source" utan kategori. Tabell 2.3 ("Passenger cars") beskriver den äldre Nord2000-modellen. Blad 17 stämmer inom 0,0005 dB med detta. |
| 3 | Faktorn på den horisontella direktiviteten. | √cos ψ. | Sidbilden. Rättar P-S1 (cos ψ). |
| 4 | Tabell 2.4 under 50 Hz (25, 31,5, 40 Hz). | Noll. | Tabellen börjar på 50 Hz. Samma hållning som för tabell A.2 under 250 Hz. |
| 5 | Negativ ψ (mottagaren lägre än delkällan). | Formlerna används som de står, så −k·sin ψ blir positiv. | Tabellen anger inget giltighetsområde. Alternativ: \|ψ\| eller noll. Inget körbart testfall har negativ ψ, så valet är oprövat. |
| 6 | Vilken riktning är ψ: räta linjen eller strålens startvinkel vid refraktion? | Räta linjen från delkällan till mottagaren. | AV 1106/07 bilaga D säger att Nord2000 gör så och hänvisar till en särskild rapport (AV 1025/06) för starkare refraktion; den korrektionen är inte införd. |
| 7 | Direkt och markreflekterad stråle. | Samma direktivitet för båda. | AV 1106/07 (avsnittet om reflexer, vid figur 32) och AV 1849/00: en skillnad antas ligga i L_W. |
| 8 | Färdriktningen på rak väg. | Åt det håll θ växer (φ = π/2 + θ). | Testfallen anger ingen färdriktning. Ekv. (2.12) beror inte på den. Ekv. (2.11) gör det bara när utbredningen är olika åt de två hållen längs vägen (vind med komponent längs vägen); inget blad med kategori 2 eller 3 har vind. Ett test visar att summan är oberoende av färdriktningen utan vind. |
| 9 | Var i källsegmentet tas riktningen? | I källpunkten (segmentets mittvinkel). | Följer körarens befintliga punktkällor. Ingen integration över segmentets 1°. |
| 10 | Datafil utan `[direktivitet]` och anrop med riktning. | `BullerFel`. | Inga tysta fel. `stoder_direktivitet` är då falskt, så köraren ger ingen riktning och skriver det i kommentaren. |

## Utfall för testsvit grupp N, före och efter

`buller.testfall.korare.kor_alla` med `buller.nord2000.Nord2000Karna()` (förval) och
`buller.emission.Vagemission()`. "Före" är samma kod med `direktivitet=False`, kört direkt före
"efter" med samma kärna; det är bit för bit lika med körningen innan något ändrades. Antal: före
21 godkända, 1 ska utredas, 58 ej körbara, 4 ej facit; efter 22 godkända, 0 ska utredas, 58 ej
körbara, 4 ej facit. De 58 ej körbara och N02–N04 är oförändrade och saknar siffror.

Kolumnerna: avvikelse i L_AE (beräknat − facit, dB); största tersbandsavvikelse med bandet inom
parentes; största avvikelse i 800–6300 Hz.

| Fall | Utfall före | L_AE före | Största band före | 800–6300 Hz före | Utfall efter | L_AE efter | Största band efter | 800–6300 Hz efter |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| N01 | ej facit | +0,29 | +0,42 (800) | +0,42 (800) | ej facit | −0,01 | +0,01 (100) | −0,00 (1 k) |
| N18R001 | godkänd | +0,29 | +0,41 (800) | +0,41 (800) | godkänd | −0,01 | +0,01 (100) | +0,00 (5 k) |
| N18R002 | godkänd | +0,27 | +0,45 (800) | +0,45 (800) | godkänd | −0,02 | +0,01 (100) | +0,00 (5 k) |
| N18R003 | godkänd | +0,23 | +0,52 (800) | +0,52 (800) | godkänd | −0,02 | +0,01 (100) | +0,00 (5 k) |
| N18R004 | godkänd | +0,15 | +0,56 (800) | +0,56 (800) | godkänd | −0,03 | +0,01 (100) | +0,00 (5 k) |
| N18R005 | godkänd | +0,10 | +0,62 (800) | +0,62 (800) | godkänd | −0,02 | +0,01 (100) | +0,00 (5 k) |
| N18R006 | godkänd | +0,24 | +0,65 (1,25 k) | +0,65 (1,25 k) | godkänd | −0,02 | +0,01 (100) | +0,00 (5 k) |
| N18R007 | godkänd | +0,07 | +0,75 (6,3 k) | +0,75 (6,3 k) | godkänd | −0,01 | +0,01 (100) | +0,00 (2,5 k) |
| N18R008 | godkänd | +0,26 | +0,53 (800) | +0,53 (800) | godkänd | −0,02 | +0,01 (100) | +0,00 (5 k) |
| N18R009 | godkänd | −0,41 | −0,87 (800) | −0,87 (800) | godkänd | −0,02 | −0,05 (10 k) | −0,03 (6,3 k) |
| N18R010 | godkänd | +0,14 | +0,83 (1,25 k) | +0,83 (1,25 k) | godkänd | −0,03 | +0,04 (10 k) | −0,03 (1,6 k) |
| N18R011 | godkänd | +0,10 | +0,20 (1 k) | +0,20 (1 k) | godkänd | +0,05 | +0,12 (1,25 k) | +0,12 (1,25 k) |
| N18R012 | godkänd | −0,20 | +0,41 (6,3 k) | +0,41 (6,3 k) | godkänd | +0,01 | +0,06 (800) | +0,06 (800) |
| N18R013 | godkänd | −0,46 | −0,87 (800) | −0,87 (800) | godkänd | +0,00 | +0,02 (1,25 k) | +0,02 (1,25 k) |
| N18R014 | ska utredas | −0,73 | −1,03 (800) | −1,03 (800) | godkänd | −0,00 | −0,03 (400) | +0,02 (1,25 k) |
| N18R015 | godkänd | −0,01 | +0,05 (8 k) | +0,04 (800) | godkänd | −0,03 | −0,05 (3,15 k) | −0,05 (3,15 k) |
| N18R016 | godkänd | +0,38 | −0,91 (10 k) | +0,88 (4 k) | godkänd | −0,02 | −0,91 (10 k) | −0,23 (1 k) |
| N18R017 | godkänd | −0,18 | −0,32 (10 k) | −0,30 (6,3 k) | godkänd | −0,02 | +0,00 (1,25 k) | +0,00 (1,25 k) |
| N18R018 | godkänd | −0,36 | −0,70 (630) | −0,68 (1,6 k) | godkänd | −0,25 | −0,74 (800) | −0,74 (800) |
| N18R021 | godkänd | +0,22 | +0,39 (800) | +0,39 (800) | godkänd | −0,02 | +0,01 (100) | −0,00 (2 k) |
| N18R022 | godkänd | −0,24 | +0,42 (6,3 k) | +0,42 (6,3 k) | godkänd | −0,01 | −0,02 (315) | +0,01 (800) |
| N18R023 | godkänd | +0,01 | +0,33 (800) | +0,33 (800) | godkänd | −0,02 | −0,06 (6,3 k) | −0,06 (6,3 k) |
| N18R024 | godkänd | +0,35 | +0,54 (1 k) | +0,54 (1 k) | godkänd | −0,01 | +0,01 (100) | +0,00 (5 k) |

Inget band ligger över 1 dB efter ändringen (före: 800 Hz i blad 14).

### Blad 14 (sidvind), 16, 17 och 18 per tersband

Beräknat − facit i dB för L_E.

| Band (Hz) | 14 före | 14 efter | 16 före | 16 efter | 17 före | 17 efter | 18 före | 18 efter |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | −0,00 | −0,00 | −0,01 | −0,01 | −0,03 | +0,00 | −0,06 | −0,03 |
| 31,5 | −0,00 | −0,00 | −0,00 | −0,00 | −0,03 | +0,00 | −0,06 | −0,03 |
| 40 | −0,00 | −0,00 | +0,01 | +0,01 | −0,03 | +0,00 | −0,05 | −0,02 |
| 50 | +0,02 | +0,00 | +0,03 | +0,02 | −0,03 | +0,00 | −0,04 | −0,01 |
| 63 | +0,02 | +0,01 | +0,04 | +0,04 | −0,02 | +0,00 | −0,03 | −0,01 |
| 80 | +0,02 | +0,01 | +0,04 | +0,04 | −0,03 | +0,00 | −0,03 | −0,01 |
| 100 | +0,04 | +0,01 | +0,04 | +0,03 | −0,03 | +0,00 | −0,09 | −0,06 |
| 125 | +0,03 | +0,01 | +0,02 | +0,02 | −0,04 | +0,00 | −0,13 | −0,08 |
| 160 | +0,04 | +0,02 | +0,01 | +0,00 | −0,06 | +0,00 | −0,15 | −0,08 |
| 200 | +0,01 | +0,01 | −0,04 | −0,04 | −0,11 | +0,00 | −0,19 | −0,08 |
| 250 | +0,01 | +0,01 | −0,20 | −0,20 | −0,15 | +0,00 | −0,24 | −0,10 |
| 315 | −0,01 | −0,01 | −0,46 | −0,46 | −0,18 | +0,00 | −0,29 | −0,11 |
| 400 | −0,03 | −0,03 | −0,35 | −0,35 | −0,14 | +0,00 | −0,44 | −0,29 |
| 500 | −0,00 | −0,00 | +0,01 | +0,01 | −0,07 | +0,00 | −0,53 | −0,43 |
| 630 | +0,02 | +0,02 | +0,18 | +0,18 | −0,06 | +0,00 | −0,70 | −0,61 |
| 800 | −1,03 | +0,01 | +0,46 | +0,07 | +0,25 | +0,00 | −0,44 | −0,74 |
| 1 k | −0,97 | +0,01 | +0,27 | −0,23 | +0,01 | +0,00 | −0,49 | −0,59 |
| 1,25 k | −0,87 | +0,02 | +0,72 | +0,23 | −0,13 | +0,00 | −0,48 | −0,42 |
| 1,6 k | −0,77 | +0,02 | +0,49 | −0,13 | −0,15 | +0,00 | −0,68 | −0,60 |
| 2 k | −0,66 | +0,01 | +0,78 | +0,12 | −0,20 | +0,00 | −0,56 | −0,39 |
| 2,5 k | −0,56 | +0,00 | +0,82 | +0,05 | −0,24 | +0,00 | −0,53 | −0,31 |
| 3,15 k | −0,42 | +0,00 | +0,86 | +0,05 | −0,25 | +0,00 | −0,54 | −0,30 |
| 4 k | −0,31 | +0,00 | +0,88 | +0,04 | −0,27 | +0,00 | −0,53 | −0,27 |
| 5 k | −0,20 | +0,00 | +0,78 | −0,01 | −0,28 | +0,00 | −0,46 | −0,18 |
| 6,3 k | −0,09 | +0,00 | +0,42 | −0,17 | −0,30 | +0,00 | −0,38 | −0,08 |
| 8 k | +0,00 | +0,00 | −0,61 | −0,61 | −0,32 | +0,00 | −0,47 | −0,15 |
| 10 k | +0,00 | +0,00 | −0,91 | −0,91 | −0,32 | +0,00 | −0,60 | −0,28 |
| **L_AE** | **−0,73** | **−0,00** | **+0,38** | **−0,02** | **−0,18** | **−0,02** | **−0,36** | **−0,25** |

- **Blad 14:** resten i 800–6300 Hz (−1,03 till −0,09 dB) är borta; kvar är högst 0,03 dB i något band.
  P-S1:s slutsats att blad 14 föll på källmodellen och inte på utbredningen håller.
- **Blad 16** (1000 m, u = 5 m/s): 800–6300 Hz från +0,27…+0,88 dB till −0,23…+0,23 dB. Resterna i
  250–400 Hz (−0,20 till −0,46 dB) och i 8 och 10 kHz (−0,61 och −0,91 dB) är oförändrade; där verkar
  ingen direktivitet. De hör till utbredningen (P-S1, "Olöst").
- **Blad 17** (kategori 2): alla band inom 0,0005 dB. L_AE skiljer ändå 0,02 dB, vilket är facits egen
  avrundning (B1: bladens totalnivå avviker upp till 0,038 dB från A-summan av bladets spektrum).
- **Blad 18** (kategori 3): L_AE −0,36 → −0,25 dB. Banden över 2 kHz blir bättre, 800 och 1000 Hz
  sämre (−0,44 → −0,74 dB och −0,49 → −0,59 dB). Fallet är godkänt före och efter. Se "Diagnostik".

### Alla blad per tersband efter ändringen

Beräknat − facit i dB för L_E, kolumnerna är bladnummer.

| Band (Hz) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 21 | 22 | 23 | 24 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | +0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | +0,00 | −0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | −0,01 | +0,00 | −0,03 | +0,00 | −0,00 | +0,00 | +0,00 |
| 31,5 | −0,00 | +0,00 | +0,00 | −0,00 | −0,00 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | −0,00 | +0,00 | −0,03 | −0,00 | −0,00 | +0,00 | +0,00 |
| 40 | +0,00 | −0,00 | −0,00 | −0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,00 | −0,00 | −0,00 | +0,00 | +0,01 | +0,00 | −0,02 | −0,00 | −0,00 | +0,00 | −0,00 |
| 50 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,02 | +0,00 | −0,01 | +0,00 | +0,00 | +0,00 | +0,00 |
| 63 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,01 | +0,00 | +0,04 | +0,00 | −0,01 | +0,00 | +0,00 | +0,00 | +0,00 |
| 80 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,01 | +0,01 | +0,04 | +0,00 | −0,01 | +0,00 | +0,00 | +0,00 | +0,00 |
| 100 | +0,01 | +0,01 | +0,01 | +0,01 | +0,01 | +0,01 | +0,01 | +0,01 | +0,01 | +0,01 | +0,01 | +0,00 | +0,01 | +0,01 | +0,01 | +0,03 | +0,00 | −0,06 | +0,01 | +0,00 | +0,01 | +0,01 |
| 125 | +0,00 | +0,01 | +0,01 | +0,01 | +0,00 | +0,00 | +0,00 | +0,01 | +0,00 | +0,01 | +0,01 | −0,00 | +0,01 | +0,01 | +0,01 | +0,02 | +0,00 | −0,08 | +0,01 | +0,00 | +0,01 | +0,00 |
| 160 | +0,00 | +0,01 | +0,01 | +0,01 | +0,00 | +0,00 | +0,00 | +0,01 | +0,00 | +0,01 | +0,01 | −0,00 | +0,01 | +0,02 | +0,02 | +0,00 | +0,00 | −0,08 | +0,01 | +0,00 | +0,01 | +0,01 |
| 200 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | −0,02 | +0,01 | +0,01 | +0,02 | −0,04 | +0,00 | −0,08 | +0,00 | −0,01 | +0,01 | +0,00 |
| 250 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | −0,03 | +0,00 | +0,01 | +0,03 | −0,20 | +0,00 | −0,10 | +0,00 | −0,01 | +0,02 | +0,00 |
| 315 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,03 | −0,01 | −0,01 | +0,04 | −0,46 | +0,00 | −0,11 | +0,00 | −0,02 | +0,03 | +0,00 |
| 400 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | +0,00 | +0,00 | −0,01 | −0,01 | −0,03 | +0,02 | −0,35 | +0,00 | −0,29 | +0,00 | −0,02 | +0,03 | +0,00 |
| 500 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,00 | +0,00 | +0,01 | −0,00 | −0,00 | −0,02 | +0,01 | +0,00 | −0,43 | +0,00 | −0,00 | +0,00 | +0,00 |
| 630 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,00 | +0,00 | +0,04 | +0,02 | +0,02 | −0,03 | +0,18 | +0,00 | −0,61 | +0,00 | +0,01 | −0,01 | +0,00 |
| 800 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,00 | +0,06 | +0,06 | +0,02 | +0,01 | +0,00 | +0,07 | +0,00 | −0,74 | −0,00 | +0,01 | +0,00 | +0,00 |
| 1 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,02 | +0,10 | +0,06 | +0,02 | +0,01 | +0,00 | −0,23 | +0,00 | −0,59 | −0,00 | +0,01 | +0,00 | +0,00 |
| 1,25 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,01 | −0,03 | +0,12 | +0,05 | +0,02 | +0,02 | −0,00 | +0,23 | +0,00 | −0,42 | −0,00 | +0,01 | −0,00 | +0,00 |
| 1,6 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,03 | +0,11 | +0,03 | +0,02 | +0,02 | −0,01 | −0,13 | +0,00 | −0,60 | −0,00 | +0,00 | −0,01 | +0,00 |
| 2 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,00 | −0,03 | +0,10 | +0,00 | +0,01 | +0,01 | −0,02 | +0,12 | +0,00 | −0,39 | −0,00 | −0,00 | −0,02 | +0,00 |
| 2,5 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,01 | −0,02 | +0,07 | −0,02 | −0,01 | +0,00 | −0,04 | +0,05 | +0,00 | −0,31 | −0,00 | −0,01 | −0,03 | +0,00 |
| 3,15 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,01 | −0,01 | +0,00 | +0,00 | +0,00 | +0,00 | −0,05 | +0,05 | +0,00 | −0,30 | −0,00 | −0,01 | −0,04 | +0,00 |
| 4 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,01 | −0,01 | +0,00 | −0,00 | +0,00 | +0,00 | −0,04 | +0,04 | +0,00 | −0,27 | +0,00 | −0,00 | −0,04 | +0,00 |
| 5 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,02 | +0,00 | +0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,01 | +0,00 | −0,18 | +0,00 | −0,00 | −0,05 | +0,00 |
| 6,3 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,03 | +0,01 | +0,00 | −0,00 | +0,00 | +0,00 | −0,00 | −0,17 | +0,00 | −0,08 | +0,00 | −0,00 | −0,06 | +0,00 |
| 8 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,04 | +0,03 | +0,00 | +0,00 | +0,00 | +0,00 | +0,05 | −0,61 | +0,00 | −0,15 | +0,00 | +0,00 | +0,05 | +0,00 |
| 10 k | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | −0,05 | +0,04 | +0,00 | +0,00 | −0,00 | +0,00 | −0,00 | −0,91 | +0,00 | −0,28 | +0,00 | −0,00 | −0,05 | +0,00 |
| **L_AE** | **−0,01** | **−0,02** | **−0,02** | **−0,03** | **−0,02** | **−0,02** | **−0,01** | **−0,02** | **−0,02** | **−0,03** | **+0,05** | **+0,01** | **+0,00** | **−0,00** | **−0,03** | **−0,02** | **−0,02** | **−0,25** | **−0,02** | **−0,01** | **−0,02** | **−0,01** |

Det som återstår utöver blad 16 och 18: blad 11 (+0,06 till +0,12 dB i 800–2500 Hz, samma som P-S1
fann i sitt prov) och +0,01 dB i 100–160 Hz i de flesta blad.

### Läsningar sida vid sida

Avvikelse i L_AE; största tersbandsavvikelse (band). "Bara horisontell" och "bara vertikal" är
diagnostik utanför repot (`diag.py`), de två sista kolumnerna är valet `horisontell_verkan` i repot.

| Fall | utan | bara horisontell | bara vertikal | båda, `delkalla` (förval) | båda, `bullertyp` |
| --- | --- | --- | --- | --- | --- |
| N18R001 | +0,29; +0,414 (800) | −0,01; +0,035 (100) | +0,29; +0,413 (800) | −0,01; +0,007 (100) | −0,01; +0,013 (3,15 k) |
| N18R002 | +0,27; +0,454 (800) | −0,01; +0,039 (100) | +0,27; +0,454 (800) | −0,02; +0,008 (100) | −0,01; +0,014 (3,15 k) |
| N18R003 | +0,23; +0,517 (800) | −0,01; +0,035 (100) | +0,22; +0,516 (800) | −0,02; +0,007 (100) | −0,01; +0,014 (3,15 k) |
| N18R004 | +0,15; +0,564 (800) | −0,02; +0,032 (100) | +0,15; +0,563 (800) | −0,03; +0,006 (100) | −0,02; +0,013 (3,15 k) |
| N18R005 | +0,10; +0,616 (800) | −0,02; +0,031 (100) | +0,10; +0,615 (800) | −0,02; +0,006 (100) | −0,02; +0,015 (2 k) |
| N18R006 | +0,24; +0,650 (1,25 k) | −0,01; +0,030 (100) | +0,24; +0,649 (1,25 k) | −0,02; +0,006 (100) | −0,01; +0,036 (3,15 k) |
| N18R007 | +0,07; +0,748 (6,3 k) | −0,01; +0,030 (100) | +0,07; +0,748 (6,3 k) | −0,01; +0,006 (100) | −0,01; +0,111 (6,3 k) |
| N18R008 | +0,26; +0,529 (800) | −0,01; +0,086 (100) | +0,25; +0,523 (800) | −0,02; +0,006 (100) | −0,01; +0,011 (3,15 k) |
| N18R009 | −0,41; −0,874 (800) | −0,01; −0,054 (10 k) | −0,41; −0,874 (800) | −0,02; −0,054 (10 k) | −0,02; −0,054 (10 k) |
| N18R010 | +0,14; +0,826 (1,25 k) | −0,02; +0,042 (10 k) | +0,13; +0,825 (1,25 k) | −0,03; +0,041 (10 k) | −0,03; +0,041 (10 k) |
| N18R011 | +0,10; +0,197 (1 k) | +0,06; +0,116 (1,25 k) | +0,09; +0,197 (1 k) | +0,05; +0,116 (1,25 k) | +0,05; +0,117 (1,25 k) |
| N18R012 | −0,20; +0,413 (6,3 k) | +0,01; +0,064 (800) | −0,20; +0,413 (6,3 k) | +0,01; +0,064 (800) | +0,01; +0,060 (800) |
| N18R013 | −0,46; −0,869 (800) | +0,00; +0,039 (100) | −0,46; −0,869 (800) | +0,00; +0,021 (1,25 k) | −0,01; +0,046 (6,3 k) |
| N18R014 | −0,73; −1,032 (800) | +0,00; +0,040 (100) | −0,73; −1,032 (800) | −0,00; −0,026 (400) | −0,01; −0,028 (3,15 k) |
| N18R015 | −0,01; +0,046 (8 k) | −0,02; +0,046 (8 k) | −0,02; +0,046 (8 k) | −0,03; −0,046 (3,15 k) | −0,03; +0,046 (8 k) |
| N18R016 | +0,38; −0,908 (10 k) | −0,02; −0,908 (10 k) | +0,38; −0,908 (10 k) | −0,02; −0,908 (10 k) | −0,01; −0,908 (10 k) |
| N18R017 | −0,18; −0,318 (10 k) | −0,02; +0,001 (800) | −0,18; −0,318 (10 k) | −0,02; +0,000 (1,25 k) | −0,04; −0,053 (10 k) |
| N18R018 | −0,36; −0,701 (630) | −0,25; −0,738 (800) | −0,36; −0,701 (630) | −0,25; −0,739 (800) | −0,27; −0,762 (800) |
| N18R021 | +0,22; +0,391 (800) | −0,01; +0,032 (100) | +0,21; +0,390 (800) | −0,02; +0,006 (100) | −0,01; +0,015 (6,3 k) |
| N18R022 | −0,24; +0,418 (6,3 k) | −0,01; +0,030 (100) | −0,24; +0,418 (6,3 k) | −0,01; −0,021 (315) | −0,02; +0,060 (6,3 k) |
| N18R023 | +0,01; +0,326 (800) | −0,01; −0,060 (6,3 k) | +0,00; +0,325 (800) | −0,02; −0,060 (6,3 k) | −0,02; −0,057 (6,3 k) |
| N18R024 | +0,35; +0,543 (1 k) | −0,01; +0,085 (100) | +0,34; +0,538 (1 k) | −0,01; +0,006 (100) | −0,01; +0,014 (6,3 k) |

- Den horisontella direktiviteten står för nästan hela ändringen.
- Den vertikala gör liten men mätbar skillnad: 50–160 Hz i blad med personbilar går från +0,03…+0,09 dB
  till +0,01 dB (delkällan på 0,30 m, −2·sin ψ och −4·sin ψ). Den är alltså med i facit.
- `"bullertyp"` ger genomgående något större rester än förvalet (blad 7: 0,111 mot 0,006 dB; blad 17:
  0,053 mot 0,000 dB), men inget fall byter utfall mellan läsningarna.

## Diagnostik (inte i repot): blad 18 och antalet axlar

Blad 18 skiljer sig från blad 17 bara i fordonskategorin. Resten har rullbullrets form (störst i
400–2000 Hz, nära noll under 100 Hz). RL 18-18 anger inget antal axlar för rak väg, men för kurvig väg
anger samma rapport fem axlar för kategori 3. Källmodellens tabell avser fyra. Prov utanför repot
(`/home/claude/verktyg/b5b/diag.py`, `Trafikflode.antal_axlar` satt i anropet, övrigt som i repot):

| Band (Hz) | 4 axlar (i repot) | 5 axlar | 6 axlar |
| --- | --- | --- | --- |
| 25 | −0,03 | −0,00 | +0,03 |
| 31,5 | −0,03 | −0,00 | +0,03 |
| 40 | −0,02 | −0,00 | +0,02 |
| 50 | −0,01 | −0,00 | +0,01 |
| 63 | −0,01 | −0,00 | +0,00 |
| 80 | −0,01 | −0,00 | +0,01 |
| 100 | −0,06 | −0,00 | +0,05 |
| 125 | −0,08 | −0,00 | +0,08 |
| 160 | −0,08 | −0,00 | +0,08 |
| 200 | −0,08 | −0,00 | +0,08 |
| 250 | −0,10 | −0,00 | +0,09 |
| 315 | −0,11 | −0,00 | +0,10 |
| 400 | −0,29 | −0,00 | +0,27 |
| 500 | −0,43 | −0,00 | +0,38 |
| 630 | −0,61 | −0,01 | +0,53 |
| 800 | −0,74 | −0,01 | +0,62 |
| 1 k | −0,59 | −0,01 | +0,51 |
| 1,25 k | −0,42 | −0,00 | +0,37 |
| 1,6 k | −0,60 | −0,01 | +0,52 |
| 2 k | −0,39 | −0,00 | +0,35 |
| 2,5 k | −0,31 | −0,00 | +0,29 |
| 3,15 k | −0,30 | −0,00 | +0,28 |
| 4 k | −0,27 | −0,00 | +0,25 |
| 5 k | −0,18 | −0,00 | +0,17 |
| 6,3 k | −0,08 | −0,00 | +0,07 |
| 8 k | −0,15 | −0,00 | +0,14 |
| 10 k | −0,28 | −0,00 | +0,26 |
| **L_AE** | **−0,25** | **−0,03** | **+0,18** |

Med fem axlar ligger alla 27 band inom 0,01 dB (utfall godkänd, L_AE −0,03 dB). Facit för blad 18 är
alltså med största sannolikhet räknat med fem axlar. **Inget av detta är infört:** fallfilen och
`Trafikpost` ligger utanför paketet, och uppgiften står inte i testfallens text för rak väg. Det bör
avgöras av samordnaren (se nedan). Med det och direktiviteten skulle alla 22 körbara blad ligga inom
0,06 dB i L_AE.

## Till samordnaren

**`tests/buller/test_testsvit_n.py` (inte ändrad av mig).** Tre tester faller nu:
`test_antal_per_utfall`, `test_inga_andra_fall_ar_godkanda`,
`test_kanda_avvikelser_har_oforandrat_utfall`. Det som behöver föras in:

- `GODKANDA_FALL`: lägg till blad 14 (`_blad(*range(1, 17), 17, 18, 21, 22, 23, 24)`).
- `KANDA_AVVIKELSER`: tom (N18R014 utgår). Före: ska utredas, L_AE −0,728 dB, 800 Hz −1,032 dB. Efter:
  godkänd, L_AE −0,001 dB, största band −0,026 dB (400 Hz).
- `test_antal_per_utfall`: godkänd 22, ska utredas 0 (övriga oförändrade: 0, 4, 58).
- `HOMOGENA_FALL_TOTALAVVIKELSE_DB` håller, men gränserna kan skärpas. Uppmätt nu (absolutbelopp, dB):
  N18R001 0,013; 002 0,016; 003 0,019; 004 0,025; 005 0,025; 006 0,016; 007 0,011; 008 0,016;
  017 0,024; 018 0,248.
- Modulens docstring: läget är 22 godkända, 0 ska utredas.

**Övrigt.**

- `docs/buller/KONTRAKT.md` KB2: `L_W[källa, b]` är där oberoende av mottagaren. Med direktivitet beror
  källstyrkan på riktningen mot mottagaren. Jag har inte ändrat kontraktet; bara testfallsköraren
  använder riktningen. För operatorlagret (B8) finns en väg som inte bryter "trafikändring utan ny
  geometri": i läsningen `"delkalla"` är tillägget per delkälla och band oberoende av trafiken, så
  `Vagemission.direktivitet_db(riktning)` kan läggas i `H_db` per par källa–mottagare. Det kräver att
  källsegmenten bär färdriktning och att varje delkälla är en egen källa i lagret. Beslutet är ditt.
- `Kallsegment` (KB1) saknar färdriktning. Den behövs för ekv. (2.11); användarhandledningen avsnitt
  4.7 förutsätter en emissionslinje per körfält.
- Reflexer i byggnader (B6): riktningen bör tas mot spegelmottagaren. Inte hanterat.
- Blad 18: avgör om fall med kategori 3 på rak väg ska ha fem axlar (kräver ett fält i `Trafikpost`
  och i fallfilerna, och en tolkningspost "avgjord av testfall" enligt regeln, eftersom texten inte
  anger antalet för rak väg).
- `src/buller/testfall/__init__.py` exporterar inte de nya namnen (`EmissionsmodellMedDirektivitet`,
  `riktning_for_kallpunkt`, `vinkel_mot_fardriktning_rad`); filen är inte min.
- `docs/buller/logg/B1-B5.md` hänvisar till `nord2000_road_v2.toml`, som nu heter
  `nord2000_road_v3.toml`, och säger att direktiviteten inte är införd.
- `tasks/B00-buller-oversikt.md`, raden B5: direktiviteten är införd; lutning, maxnivå, dubbdäck återstår.
- `docs/buller/logg/testsvit-N-2026-11-21.md`: raderna för N18R001–024 ersätts av tabellen ovan.
- Under arbetet gick `tests/buller/nord2000/test_delmodell6.py` inte att importera (`_marksida` saknas i
  `delmodell4`). Det är den andra agentens pågående arbete och berör inte detta paket.

## Osäkert och olöst

1. Ingen människa har kontrollerat talen. `verifierad_av_manniska = false`.
2. Negativ ψ (tolkning 5) och ekv. (2.11) med vind längs vägen (tolkning 8) prövas inte av något
   testfall.
3. ψ är i de körbara bladen högst ca 1°, så den vertikala direktiviteten och √cos ψ prövas svagt:
   bara termerna med sin ψ (0,30 m, 50–160 Hz) ger mätbart utslag. Formen 1 − cos² ψ och 1 − cos ψ
   är inte prövade mot facit. De blir betydande först för höga mottagare nära vägen.
4. Refraktionens inverkan på utstrålningsvinkeln (AV 1025/06) är inte införd och rapporten finns inte
   i källmappen.
5. Blad 11: +0,06 till +0,12 dB i 800–2500 Hz kvarstår (utbredning, P-S1).
6. Maximalnivå (avsnitt 2.6) är fortfarande inte införd; direktiviteten var en av förutsättningarna.
7. Källmodellen ska fortfarande inte kallas Nord2000-konform: våt vägbana, dubbdäck, högt avgasrör,
   lutning och maximalnivå saknas.

## Mätt

- `python -m pytest tests/buller/emission tests/buller/testfall -q`: 119 godkända, 2 överhoppade
  (omimporten, som kräver källmapp och `xlrd`), 143 deltester. Före paketet: 95 godkända, 2 överhoppade.
- `python -m pytest tests/buller -q` utan `tests/buller/nord2000` och `test_testsvit_n.py`: 321 godkända,
  2 överhoppade.
- `tests/buller/test_testsvit_n.py`: 8 godkända, 3 faller (se ovan; väntat).
- `ruff check` och `mypy` (strict) på `src/buller/emission`, `tests/buller/emission`,
  `src/buller/testfall/korare.py`, `tests/buller/testfall/test_korare.py`: inga fel.

## Verktyg (utanför repot)

`/home/claude/verktyg/b5b/`: `kor.py` (kör sviten, sparar avvikelser per band), `diag.py` (bara
horisontell, bara vertikal, annat axelantal), `tabellkoll.py` (tabell 2.4 och ekvationerna ur
pdftotext mot datafilen), `rapport.py` (tabellerna ovan). Körningar: `fore.json`, `efter.json`,
`efter_bt.json`, `d_h.json`, `d_v.json`, `d_a5.json`, `d_a6.json`; `fore0.json` är körningen innan
något ändrades.

## Förslag till post i `STATUS.md`

```
## 2026-11-21 – B5b – Källmodellens direktivitet införd
Gjort: SP 2006:12 avsnitt 2.5 i `buller.emission`: horisontell direktivitet för delkällan på 0,01 m
(ekv. 2.12) och 0,75 m (ekv. 2.11), vertikal för alla delkällor (tabell 2.4), dataversion 3. Köraren ger
källmodellen riktningen (φ, ψ) per källpunkt; utan riktning ingen direktivitet, och det står i
kommentaren. Inga korrektionsfaktorer.
Mätt: testsvit grupp N 22 godkända (förut 21), 0 ska utredas (förut 1: blad 14, L_AE −0,73 → −0,00 dB,
800 Hz −1,03 → +0,01 dB), 0 underkända, 58 ej körbara, 4 ej facit. Alla 20 blad med personbilar inom
0,06 dB i L_AE; blad 17 inom 0,0005 dB i alla band; blad 18 −0,25 dB (800 Hz −0,74 dB). 119 tester
godkända i emission och testfall; ruff och mypy utan fel.
Öppet: blad 18 stämmer inom 0,01 dB per band med fem axlar (diagnostik, inte infört); KB2 och
`Kallsegment` saknar riktning och färdriktning för direktivitet i operatorlagret; negativ ψ och
refraktionens inverkan på utstrålningsvinkeln oprövade.
Nästa: samordnaren för in utfallet i `tests/buller/test_testsvit_n.py`; beslut om axlar och KB2.
```
