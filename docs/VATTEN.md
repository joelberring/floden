# Vatten: takvatten och avtappning

Beslut 2026-11-22 (se `BESLUT.md`). Koden: `src/sankgraf/takvatten.py` (takvatten),
`src/sankgraf/avtappning.py` (avtappning som parameter). Testfall: A12, A13 (`VALIDERING.md`),
`tests/sankgraf/test_takvatten.py`, `tests/karna/test_avtappning_fraga.py`.

## Takvatten

Regn som faller på ett tak (byggnadscell, markslag 9 i `mark.tif`) avrinner till marken längs
husets fasader. Byggnadscellerna själva får inget vatten kvar. Det gäller i alla vattenlösare:
statisk fyllning, händelsestyrd dynamik (och tidsstegning, ensemble, enhetssvar, risk via samma
nodareor), 2D (Python och Rust), hybriden och lokal omräkning/K9 (fördelningen byggs alltid ur
scenariots raster och byggnader, så nya, ändrade och borttagna hus ändrar takvattnet).

### Regler

1. **Byggnad.** Polygonen ur `byggnader.gpkg` (K1 v2). Takcellerna är byggnadscellerna vars mitt
   ligger i polygonen (samma regel som inbränningen). En byggnadscell utan polygon hör till
   närmaste polygonbyggnad i samma sammanhängande cellgrupp. Saknas registret (K1 v1, testfallen)
   är varje fyrgrannsammanhängande grupp byggnadsceller en byggnad (dess cellpolygon, förenklad
   med 0,75 cell) med okänd takform.
2. **Fasadlinjer.** Polygonens kanter, yttre ring moturs och hål medurs, normalen åt höger = ut
   ur huset. Kanter i följd som byter riktning mindre än 10° är en linje.
3. **Takform** (`takform`; `platt`/`flat`, `pulpet`/`skillion`, `sadel`/`sadeltak`/`gabled`,
   `brutet`, `mansard`/`gambrel`; annan text = okänd och flaggas):
   - **platt:** alla fasadlinjer, i proportion till längden.
   - **pulpet:** med takfallets riktning (`takfall_azimut_grader`, valfri kolumn i
     `byggnader.gpkg`, grader medurs från norr mot den lägre sidan) de linjer vars normal pekar
     högst 45° från riktningen, i proportion till längden. Utan riktning: som okänd (flaggas).
   - **sadel, brutet, mansard och okänd** (brutet tak/sadeltak med nock längs längdaxeln):
     vattnet går till **takfötterna** = de fasadlinjer som är minst lika långa som husets djup
     bakom dem (djupet mäts från mitten av linjens längsta kant rakt in i huset), i proportion
     till längden. *Rektangel:* de två långsidorna, hälften var (exakt). *Kvadrat:* alla fyra.
     *L-form:* de två yttre och de två inre långsidorna, efter längd; gavlarna får inget.
     Långsidan bestäms alltså av polygonens egna kanter, inte av en omskriven rektangel: det
     fungerar för vridna hus (registrets polygon) och för L, U och T.
   - **Ring** (polygon med hål, t.ex. ett slutet kvarter runt en gård) med sadel/okänd: hälften
     till innersidan (gården), hälften till yttersidan (gatan), var för sig efter längd.
     Ett kvarter av flera hus (polygoner) följer i stället reglerna per hus.
4. **Längs linjen** (stuprörens lägen är okända): jämnt per meter, med ett prov var fjärdedels
   cell, i den första markcellen utanför fasaden (provet flyttas utåt i halvcellssteg upp till
   två celler). Prov som hamnar i en annan byggnad (brandvägg), i en cell utan höjd eller i en
   **öppning** (port, `oppningar.gpkg`) räknas inte: linjens andel går till dess övriga prov,
   en linje utan något giltigt prov lämnar sin andel till byggnadens övriga takfötter (för ring:
   samma sida). Allt flaggas (`takvatten_flaggor`, varningar i K21). Prov utanför rutan räknas
   som utflöde ur modellen (som vatten över rutkanten). Har en byggnad ingen fasad mot mark alls
   går vattnet till alla fasader, därefter jämnt till markcellerna runt byggnadsgruppen; finns
   ingen sådan cell och gruppen inte når rutkanten blir det `TakvattenFel` (ingen gissning).
5. **Andel till ledningsnätet** (`takvatten_andel_ledning`, 0–1, standard 0) tas bort före
   fördelningen och redovisas i volymbalansen.

Fördelningen är linjär i regnet: varje byggnads takvolym flyttas med fasta vikter (summa 1 per
byggnad). Statisk fyllning flyttar volymen per byggnad (även för regnraster med förluster);
grafens dynamik lägger takytan på målcellernas noder; 2D lägger den som källterm i målcellerna
(`regn_area.npy` i Rust).

### Antaganden och begränsningar

- Stuprörens lägen är okända: vattnet sprids jämnt längs takfoten. Verkliga stuprör samlar det
  i punkter.
- Takets egen fördröjning (rinntid på taket) försummas; vattnet når marken samtidigt med regnet.
- Gröna tak och fördröjning på tak modelleras inte (markslag 3 "grönt tak" gäller inte
  byggnadsceller).
- Förluster: i 2D infiltrerar takvattnet i målcellen som annat vatten. I blåytorna dras
  initialförlusten per markslag och taket (markslag 9) har ingen, så takvattnet kommer brutto. I
  grafens dynamik dras den likformiga initialförlusten från allt regn, även takets.
- Takvatten av (`takvatten = false`) ger det gamla beteendet: regn på tak ingår inte; det
  redovisas som `tak_ej_raknat_m3` i volymbalansen.

## Avtappning

`avtappning_mm_h` är en uttrycklig parameter i vattenlösarna (`vatten.statisk_fyllning`,
`vatten.dynamik`, `vatten.2d`, CLI `floden kor --avtappning-mm-h`, kartverktygets fält),
**standard 0 mm/h**: ledningsnätet antas fullt vid ett dimensionerande skyfall. Inget annat
standardvärde gissas. Per område: `avtappning_omraden = [{ geometri = {...}, mm_h = 20 }]` i
frågan eller en fil (indata `avtappning_omraden`, GeoJSON eller GeoPackage i EPSG:3011 med
egenskapen `avtappning_mm_h`). Ett område ersätter grundvärdet i de celler vars mitt ligger i det;
överlappande områden med olika värden ger fel. Värdet och källan står alltid i mätvärdena
(`avtappning_mm_h`, `avtappning_kalla`, `avtappning_antagande`) och visas under Antaganden.

- **Dynamik:** varje nod tappas med summan av sina cellers kapacitet så länge den har vatten
  eller tillrinning (fas 3, exakt händelsestyrt).
- **2D:** varje våt cell tappas med sin kapacitet (`tvad.kalltermer.Avtappning`).
- **Statisk fyllning:** kräver regnets varaktighet (`varaktighet_min`, eller IDF-regn). Varje
  löv tappas högst `Σ kapacitet × cellyta × varaktighet` av sin *direkta* tillrinning (regn och
  takvatten i lövets celler). Överfall från andra noder tappas inte, så den statiska avtappningen
  är en undre gräns (försiktigt). Resultatet är läget vid regnets slut.
- Celler som rinner direkt ut över rutkanten och byggnadsceller tappas inte.

## Volymbalans

Varje körning redovisar `volymbalans` (`sankgraf.typer.volymbalans_dict`):

```
regn på hela rutan (mark + tak) = lagrat + utflöde + infiltration + avtappning
                                  + takvatten till ledningsnät (+ tak som inte räknas, om av)
```

med `volymfel_rel` = |regn − summan| / regn. Mätt (2026-11-22): < 1e-13 i A12, A13, demon och
enhetstesterna, i statisk fyllning, dynamik och 2D.
