# Ritverktyg för marken: generella lager, 3D-hus, mark i nivåer och skarvkontroll

Status: förslag för Joel (2026-11-24), del 1 av uppdraget. R1 (skarvkontrollen), R2 (lagermodellen, K50) och R4 (mark i nivåer i kärnan: lutande yta, `delta_m`, exakt kant, slänt) är byggda och testade.
Joels önskan: kartverktyget ska bli mer generellt (lagerhanteringen i O-Tid är för orienteringsspecifik, linjehanteringen är bra),
kunna ha 3D-objekt som hus och hantera mark i nivåer, särskilt i mötet mellan baskarta och ny mark där "skarvar" lätt skapar sänkor
där vatten samlas.

## 1. Sammanfattning av förslaget

1. **En generell lagermodell** (`floden-ritlager/v1`, additiv) där ett lager har geometrityp, *roll i beräkningen*
   (byggnad, mark, hårdgjort, vegetation, vatten, brytlinje, öppning), stil, synlighet, låsning, källa och dataklass. Rollen, inte
   ett symbolnummer, avgör vad ett objekt blir i K9/K1.
2. **Linjehanteringen från O-Tid återanvänds** (rita, hörn, fäst, flytta, lägg till/ta bort punkt, klipp, slå ihop, vänd, följ kant,
   ångra, tangentbord, pekskärm) och byggs ut från "ringar av hus" till linjer och ytor med höjdattribut.
3. **Hus som extruderade polygoner** med våningar, takform och takhöjd (K9 `byggnad_ny` finns), placeras och redigeras i 2D med
   levande 3D-förhandsvisning i den befintliga Three.js-vyn.
4. **Mark i nivåer** som K9 `markhojd` med tre sätt att beskriva höjden (plan, lutande yta mellan två höjdlinjer, slänt), och
   brytlinjer (mur, trappsteg, kantsten) som K1-brytlinjer.
5. **Skarven är en egen modellerad zon**, inte en bieffekt: en övergångszon med vald slänt (konstant lutning, ingen trappa av halva
   celler), och en **automatisk skarvkontroll** som jämför sänkor före och efter och redovisar nya och djupare sänkor vid kanten med läge,
   yta, volym, största djup och förslag på åtgärd. **Skarvkontrollen är byggd (R1)** och syns redan i steg 2.

## 2. Hur O-Tid hanterar linjer (läst vid commit `b0b952f`, MIT, © 2026 Joel Berring)

Redigeraren ligger i `apps/web/src/components/map-project/` (gränssnitt, hjälphooks) och `apps/web/src/lib/map-project-*.ts` (ren
logik), med geometrin i `packages/map-revision/src/` (utan webbläsare, kartans mm med y uppåt, objekt som `point | line | area | text`
med `path` av kommandona `M`, `L`, `C`).

| Område | Var | Hur det fungerar | Återanvänds i Floden |
| --- | --- | --- | --- |
| Rita | `lib/map-project-draw.ts` | Klick ger en hörnpunkt; tryck och dra ger en mjuk punkt (kubisk Bézier, styrpunkt en tredjedel av draget). Utkast blir objekt först vid två (linje) respektive tre (yta) punkter. | Rita med hörnpunkter återanvänds. Mjuka punkter (kurvor) behövs inte för mark: en höjdlinje är en polylinje med z per punkt. |
| Hörn | `map-project-editor.tsx`, `packages/map-revision/edit.ts` | `vertices`, `moveVertex` (styrpunkter följer med), `insertVertex`, `deleteVertex` (minst 2 punkter på linje, 3 i ring), `insertPoints` (mittpunkter att dra ut). | Redan porterat för husringar (`formedit.ts`); generaliseras till linjer och ringar med hål. |
| Träff | `map-revision/hit.ts` | `hitTest` på nivå, avstånd och ritordning; `boxTest` (Liang–Barsky) för markering med ruta. | Redan porterat (`hit.ts`). Utökas med linjer. |
| Fäst | `lib/map-project-snap.ts` | Tre nivåer: 1) ändpunkt/brytpunkt, 2) skärning mellan två linjer eller kanter, 3) närmaste punkt på linje/kant. Index byggs i en Worker; räckvidd 10 px mus och 22 px finger (`SNAP_MOUSE_PX`, `SNAP_TOUCH_PX`). | Porterat för husens hörn och kanter (`snap.ts`). Utökas till att fästa mot brytlinjer, markytornas kanter och baskartans höjdkurvor. |
| Vinkellås | `lib/map-project-draw-assist.ts` | 45 graders steg mot förra sträckan, 90 mot kanten man startade på, norr som referens; `followEdge` ("följ kant") ritar längs en befintlig linje. | Vinkellås porterat. **Följ kant är nytt att ta med**: det är det rätta sättet att rita ny markyta så att dess kant ligger exakt på en befintlig brytlinje eller byggnadskant (ingen glipa, ingen skarv av avrundning). |
| Dela och slå ihop | `lib/map-project-join.ts` | `cutLineAt` (saxen: delar vid brytpunkt eller på sträckan; ändpunkter nekas), `joinLines` (samma symbol, ändar inom tolerans kedjas, vänds åt rätt håll; exakt två linjer slås alltid ihop vid närmaste ändar), `reverseLine`. | Tas med (R3). Nyckeln i Floden är lagret och rollen, inte symbolen. |
| Lägg till/ta bort punkt | `lib/map-project-point-edit.ts` | `editPointAt(object, punkt, tolerans, ADD | REMOVE | EITHER)`. Ctrl/Cmd+klick i Markera växlar. | Redan porterat. |
| Form | `map-revision/shape-edit.ts` | Förenkla (Douglas–Peucker, stegvis), gör mjuk/rak, cirkel. | Förenkla tas med för höjdlinjer med för många punkter; mjuk/cirkel inte. |
| Ångra | `map-revision/history.ts` | Steg = objektets läge före och efter, grupp för flera objekt, högst 200 steg; ångra är en ny ändring (synkas), och ett steg som någon annan ändrat hoppas över. | Redan porterat (`historik.ts`); Floden har ingen flerpersonssynk i planförslaget, så "annan har ändrat" utelämnas tills det behövs. |
| Tangentbord | `lib/map-project-keys.ts` | Q markera, W rita, E rektangel, A/S lägg till/ta bort punkt, X klipp, G slå ihop, V vänd, T fäst, Z ångra, Shift+Z gör om, Space hjul, 1–8 senaste symboler. Bokstäver läses ur `event.code` (samma oavsett tangentbordets språk), egna bindningar sparas i webbläsaren. | Delvis porterat (Q W A S T R Z). Läggs till: X, G, V, och bindningar som kan ändras. |
| Pekskärm | `use-map-project-flick.ts`, `map-project-editor-types.ts` | Fingret flyttar kartan vid snabbt drag och flyttar det markerade först efter ett kort håll (`TOUCH_HOLD_MS` 120 ms); snäppräckvidd större för finger; verktygshjul med snärt. | Fingerräckvidd och håll-för-att-flytta tas med. Verktygshjulet ersätts av en fast verktygsrad (Floden har redan en). |

**Slutsats om linjerna:** bra och generellt. Allt i `map-revision/edit.ts`, `hit.ts`, `history.ts` och `lib/*-join.ts`, `*-snap.ts`,
`*-draw-assist.ts` är geometri utan orienteringskoppling, med objekt som nycklar och rena funktioner. Det som ska skrivas om i Floden
är bara *datamodellen* (meter i SWEREF 99 18 00, z per punkt, roll i stället för symbol).

### Vad som är orienteringsspecifikt i O-Tids lager

`lib/map-project-layer-model.ts` bygger lagren på **normen** (ISOM 2017-2 eller ISSprOM 2019-2): huvudlagren är normens symbolgrupper
(`LANDFORMS`, `ROCK`, `WATER`, `VEGETATION`, `MANMADE`, `TECHNICAL`, `COURSE`, `OTHER`), ett lager "Utanför normen" tar symboler som saknas,
"FILE" är dolda skisslager ur kartfilen och "MARKING" är kartans ram och logga. `layerOf(symbol, standard)` slår upp symbolnumret i normens
tabell; skala avgör norm (`standardFor`, ≤ 1:5 000 är sprintnormen). Lagrets tillstånd är bara tre flaggor (`hidden`, `locked`,
`dimmed`) per lager och per symbol, och "arbetslager" är symbolnummer som grupperas fritt. Det bärande: **ett lager = en grupp
symbolnummer ur en norm**. Det saknar geometrityp (symbolen avgör), roll i en beräkning, höjd, källa och dataklass. Det som är värt att
behålla är lagerflaggorna (tänd, låst, tonad), ordningen, fällbara grupper och att tillståndet sparas per projekt.

## 3. Jämförelse med Flodens nuvarande ritverktyg

| Del | Nuläge i Floden | Lucka mot önskan |
| --- | --- | --- |
| Kartmotor | `webb/src/karta/kartmotor.ts` (MapLibre, exakt SWEREF-omräkning), steg 2 i `webb/src/flode/forslag.ts` | – |
| Verktyg | `karta/ritverktyg.ts`: Markera, Rita hus, Lägg till/Ta bort hörn, Fäst, Rät vinkel, Ångra/Gör om; ren styrenhet utan webbläsare | Bara husringar. Inga linjer, inga markytor, inget klipp/sammanslagning, ingen följ kant. |
| Fäst, träff, form | `snap.ts` (hörn, skärning, kant på byggnader), `hit.ts`, `formedit.ts` (ringar), `geom.ts` | Fäster bara mot byggnader. Inte mot brytlinjer, markkanter eller kurvor. |
| Dokument | `plandok.ts`: `PlanDok` med `ny`/`hojd`/`bort`, historik, `K9Andring` (`byggnad_ny`, `byggnad_hojd`, `byggnad_bort`) | Inga markändringar. |
| Lager | `karta/lager.ts` och `lagerpanel.ts`: grupperna Underlag, Geometri, Planförslag, Resultat; synlighet, opacitet, ordning, `egenVy` (3D), `visning` 2d/3d | Redan generell nog som panel, men lagren är *visningslager* (vad som syns). Ett **ritlager** med roll saknas: hus är ett lager "Planförslag", resten finns inte. |
| Kontroll | `POST /api/kartverktyg/<projekt>/validera` (`src/webb/kartverktyg.py`, `karna/ritat.py::kontrollera_andringar`, `RITAD_TYPER`): giltig form, höjd, utbredning, K9-kontroll, `_instangt_vatten` (information om slutsänkor) | Ingen kontroll av mark. **Tillagt i R1:** `markhojd` och `skarvar`. |
| K9 | `markhojd` finns som ändringstyp (polygon + `niva_rh2000` eller `landxml`; R4 la till `lutning`, `delta_m` och `slant`) och översätts till K3 `HojdYta` (`karna/k9_till_k1.py`, `lapp/tillampa.py`) | `HojdYta` sätter en **konstant nivå, hård kant** i polygonen: det är exakt det som ger skarvar. Ingen lutning, slänt eller utjämning (före R4; R4 la till dem som valfria fält). |
| K1 brytlinjer | `brytlinjer.gpkg` med `typ` (kantsten, mur, stödmur, staket, bankkant) och `hojd_m`; sänkgrafen läser dem som kantbarriärer (`_berakna_kantbarriarer`) | Går inte att rita; bara härledda. |
| 3D | `webb/src/vy3d/` (Three.js, `extrudera` av hus, terrängnät, vattenyta), `webb/src/kartsida3d.ts` (ritat förslag live), `src/webb/vy3d.py` (`/api/3d/`, rutvis) | Byggnader som lådor med platt tak; ingen takform. Marken visas inte som förändrad av förslaget. |

Det som är gott i dag och ska behållas: servern kontrollerar och räknar, klienten visar (AGENTS.md 1), förslaget är ett K9-scenario med
nyckel, ångra/gör om och sparande per förslag fungerar.

## 4. Generell lagermodell (förslag `floden-ritlager/v1`)

Ett **ritlager** är en samling objekt av samma geometrityp och roll i ett planförslag. Det ligger *bredvid* den befintliga lagerpanelen:
panelens gruppen "Planförslag" listar ritlagren. Modellen är additiv; K9 behåller sina ändringstyper och ritlagren översätts till dem.

```json
{
  "kontrakt": "floden-ritlager/v1",
  "lager_id": "ny-gard",
  "namn": "Ny gård",
  "geometri": "polygon | linje | punkt",
  "roll": "byggnad | mark | hardgjort | vegetation | vatten | brytlinje | oppning",
  "stil": {"fyllning": "#c8d8b0", "linje": "#4a6a30", "linjebredd_px": 2, "monster": null},
  "synlig": true, "last": false, "tonad": false, "opacitet": 1.0,
  "kalla": "ritat | fil:<namn> | k1:<ruta>",
  "dataklass": "oppen | intern | skyddad",
  "egenskaper_mall": {"hojd_m": "tal", "niva_rh2000": "tal", "markslag": "kod"}
}
```

Regler:

- **Rollen avgör översättningen:** `byggnad` -> K9 `byggnad_ny` (polygon) eller `byggnad_hojd`; `mark` -> `markhojd` (polygon, med höjd enligt avsnitt 6);
  `hardgjort` och `vegetation` -> `markhojd` med `markslag` (K1-koderna 1 respektive 4/5) respektive `vegetation` (`hojd_m`);
  `vatten` -> `markslag` 8 eller `magasin`; `brytlinje` -> K1 brytlinje (`typ`, `hojd_m`); `oppning` -> K1 öppning (trumma, port, passage, `botten_rh2000`).
  Rollen avgör också vilken lösare som berörs (`karna.geometri.markklass`, markklass per domän i `data/markklasser.toml`), så buller, luft och dagsljus får samma objekt.
- **Geometritypen låser verktygen:** polygonlager ger Rita yta, linjelager ger Rita linje med klipp och sammanslagning, punktlager ger Placera.
  Rollen begränsar geometrin (en byggnad är alltid polygon, en brytlinje alltid linje).
- **Stil är bara visning;** den ingår inte i K9-nyckeln. **Synlig, låst och tonad** sparas per projekt i webbläsaren som i dag (`karta/lager.ts`), inte i scenariot.
- **Källa och dataklass:** ett lager ritat av användaren är `oppen` om projektet är öppet; ett inläst lager (fil) ärver filens klass. Skyddat
  (ledningar, skyddsklassat) laddas aldrig upp (AGENTS.md); kontrollen och MCP-spärren följer dataklassen som övriga svar.
- **Förvalda mallar** i stället för en norm: *Hus*, *Mark (ny nivå)*, *Gata/hårdgjort*, *Grönyta*, *Vatten*, *Mur och kant*, *Passage/trumma*.
  En användare kan döpa och lägga till egna lager inom rollerna. Det som O-Tid gör med normens grupper blir här rollerna.
- **Hur det lagras:** ritlagrens objekt är i K9 `andringar` (nyckelad, reproducerbar). Lagrens metadata (namn, stil) sparas bredvid scenariot
  som `ritlager.json` i förslagets mapp och påverkar inte nyckeln. Läs- och skrivregler: kontrakt K50 (byggt i R2). **Avvikelse från förslaget i R2:** metadata och objekt sparas i förslagsfilen under `ritlager` (en fil per förslag, ingen `ritlager.json`), och `andringar` i filen är hela K9 (fria ändringar + översatta objekt) så att körvägen och äldre läsare är oförändrade. Synlig och låst följer förslaget i stället för webbläsaren (de ingår aldrig i K9-nyckeln). Stängda lager skriver K9 till `k9_stangda/`. Klienten ritar bara hus (rollen byggnad) i R2; övriga roller listas, bevaras och översätts av servern men ritas först i R3.

## 5. 3D-objekt: hus som extruderade polygoner

K9 `byggnad_ny` har redan `geometri` (polygon), `hojd_m` eller `tak_rh2000`, och valfria `takform`, `andamal`, `vaningar` (K1 v2 kolumner).
Förslaget:

- **Ritas som polygon i 2D** (som i dag). Egenskapspanelen får **våningar** (3 m per våning är nuvarande uppskattning i `ritat.py`), **våningshöjd**,
  **takform** (`platt`, `sadel`, `pulpet`, `valmat`; K1 har redan `takform` och `takfall_azimut_grader`, och `sankgraf.takvatten.tolka_takform` läser dem), och **taknock/ränna** som
  höjd över takfot för sadel/pulpet. Hus har *golvnivå* = markhöjd under huset (som `markhojd_under_byggnad`) om inget annat anges; **entré** (`entre_rh2000`) anges för vattnets
  tröskel.
- **Redigering i 2D, förhandsvisning i 3D:** 2D-kartan är arbetsytan (fäst, hörn, rät vinkel); 3D-vyn (`vy3d/scen.ts`, `extrudera`) uppdateras live från `PlanDok` som i dag.
  Nytt: `extrudera` får takform (väggar till takfot, sedan takytor ur ringen; sadel via ränna, pulpet via fall) och hus på sluttande mark får sockel som i `vy3d.py` (`z_fot`). Dra i en vägg i 3D kan komma
  senare; det kräver träff mot extruderade ytor och ger inget som 2D inte redan gör.
- **Fler 3D-objekt än hus** är inte i första omgången. Murar och trappsteg modelleras som brytlinjer med höjd (avsnitt 6), och visas i 3D som ytor längs linjen.
- **Beräkning:** dagsljus (skuggor, VSC på fasader) och buller (hinder) läser takhöjden; vatten läser byggnaden som hinder och takvattnets fördelning ur takform. Alla redan på K1 v2.

## 6. Mark i nivåer

### 6.1 Fyra sätt att beskriva höjden på en ny markyta (K9 `markhojd`, additivt)

| Sätt | Fält i `egenskaper` | Betydelse |
| --- | --- | --- |
| Plan | `niva_rh2000` (finns) | Konstant nivå i polygonen. Terrasser, torgytor, gårdsplaner. |
| Lutande yta | `lutning`: `{"a": [x, y, z], "b": [x, y, z]}` eller `{"punkt": [x, y, z], "fall": 0.02, "riktning_grader": 190}` | Plan som går genom en eller två punkter med z, eller mellan två **höjdlinjer** (två ritade linjer med z; ytan interpoleras linjärt mellan dem). Gator, ramper, gårdar med fall mot en brunn. |
| Slänt | `slant`: `{"forhallande": 3.0}` (1:3) eller `{"bredd_m": 4.0}` | Övergångszonen mellan ytan och basen, se 6.3. |
| LandXML/TIN | `landxml` (finns) | Detaljprojekterad yta ur CAD. |

`delta_m` (ändring relativt basen) är ett särskilt fall: nivån = basens z + delta; det är mest meningsfullt med slänt (annars hård kant med konstant vägg). **Byggt i R4**
(`lapp.markniva`, K3 `HojdYta` och K9 `markhojd`): fälten heter `lutning` (fyra former: `punkter`, `a`+`b`, `punkt`+`fall`+`riktning_grader`, `hojdlinje_a`+`hojdlinje_b`), `delta_m` och
`slant` (`forhallande` 1:n eller `bredd_m`, ev. `markslag`; K3: `slant_forhallande`, `slant_bredd_m`, `slant_markslag`). Se `docs/DATAKONTRAKT.md` K3 och K9.

### 6.2 Brytlinjer, murar och trappsteg

Ritade linjer med `typ` (`kantsten`, `mur`, `stodmur`, `bankkant`, `staket`) och `hojd_m` (och z per punkt vid behov) blir K1 `brytlinjer`. Sänkgrafen behandlar dem som kantbarriärer (en mur
hindrar vatten över kanten upp till `hojd_m`). **Trappsteg** är en stödmur med höjd = steghöjd plus en **öppning** av typen `passage` där trappan går (K1 `oppningar`, `botten_rh2000`), så att vattnet får en väg
längs trappan. Att rita en brytlinje längs en markyta med "följ kant" ger exakt sammanfallande kanter.

### 6.3 Hur den nya marken sätts ihop med befintlig markmodell

Dagens `HojdYta` skriver `z = niva` i alla celler vars centrum ligger i polygonen (`_polygon_mask`). Det ger två problem: (1) en trappa av halva celler längs en
sned kant, (2) en lodrät kant i terrängen när nivån skiljer sig från basen. Båda är skarvar. Förslag (R4, byggt):

1. **Kantmått utan trappa.** Beräkna kantavståndet med polygonens *exakta* geometri vid cellcentrum (signerat avstånd, `shapely`), inte med celltillhörighet. Celler som polygonen
   delvis täcker får `z = f * z_ny + (1 - f) * z_bas` med f = täckt andel (vikt per cell), så att ytan inte hoppar en halv cell i taget.
2. **Slänt i dagsljus.** För varje punkt p på kanten: Δ(p) = z_ny(p) − z_bas(p). Slänten ligger utanför polygonen på avståndet W(p) = |Δ(p)| · n (n = förhållande, 1:3 betyder n = 3). Utanför
   polygonen, på avståndet d från närmaste kantpunkt p: `z = z_bas + Δ(p) · (1 − d / W(p))` för d < W(p), annars oförändrat. Då är slänten rak med konstant lutning 1/n och möter basen utan hörn
   där kanten är rak. Där Δ ≈ 0 blir W ≈ 0 (inget att jämna). Slänt *inåt* (skärning) är samma formel med Δ < 0.
3. **Fall mot basens vattenväg.** Slänten är nödvändig men inte tillräcklig: den som dämmer en befintlig vattenväg skapar en sänka även med slänt (en uppdämd ramp är en damm).
   Därför kontrolleras resultatet (avsnitt 7) och användaren får förslaget "ge ytan fall mot överfallet" eller "lägg ett utlopp" (en öppning i kanten).
4. **Osäkerhet och markslag.** `osakerhet_m` och `markslag` gäller ytan; slänten får `osakerhet_m` som ytan och markslaget gräs (4) om inget annat anges.
5. **Reproducerbarhet.** Utjämningen är en deterministisk funktion av polygonen, nivåerna, förhållandet och basens recept; ingår i K9-nyckeln via `egenskaper`, ingen ny fil.
6. **Utfall i R4 (byggt, `src/lapp/markniva.py`).** Punkt 1 gäller när ytan har `lutning`, `delta_m` eller slänt; med slänt är höjden kontinuerlig i läget, så täckningsvikten behövs
   bara utan slänt. En yta utan de nya fälten tillämpas **oförändrat** (cellmask, hård kant; byte-för-byte samma raster, testat) så att befintliga scenarier och cache-nycklar inte ändras. Slänten
   hoppar över byggnadsceller. LandXML kan inte kombineras med slänt än. Skarvkontrollen räknar släntens zon som lapp. **Vad slänten gör och inte gör:** den tar bort kanten och halvcellstrappan
   (en snedställd, sänkt yta som med cellmask gav en ny sänka vid kanten ger ingen med exakt kant eller slänt), men en yta som ligger över marken tvärs över en vattenväg dämmer fortfarande med slänt; då
   behövs fall eller utlopp (punkt 3). En slänt kring en sänkt yta (Δ < 0) sänker marken utanför ytan och kan öka den tillkommande volymen något (demokvarteret, flera fall).

## 7. Skarvkontroll (byggd, R1)

Syfte: hitta **nya lokala sänkor eller instängda ytor** som förslaget skapar längs skarven mellan ny mark och basen, redovisa läge, yta, volym och djup, och föreslå åtgärd.

**Metod** (`src/sankgraf/skarvkontroll.py`, återanvänder `bygg_sankgraf`):

1. Bygg sänkgrafen (Fill–Spill–Merge, K2) för markmodellen före och efter, med samma brytlinjer, öppningar och inställningar.
2. **Fyllnadsdjup per cell** = lövsänkans överfall − markhöjd (0 utanför sänkor). Samma definition före och efter.
3. Ökningen per cell = max(0, djup efter − djup före).
4. För varje lövsänka efter: nya om djupet före är under `min_djup_m` (0,05 m) och efter över; djupare om ökningen är minst `min_okning_m` (0,03 m).
5. **Band:** avståndet från varje cell till lappens kant (båda sidor); en sänka räknas om någon cell med ökat djup ligger inom `band_m` (10 m). Andra nya sänkor
   (inne i ytan eller långt från kanten) räknas men listas inte som skarv ("övriga").
6. Redovisning per fynd: läge (lägsta cell, EPSG:3011), botten och överfall (RH 2000, med överfallets läge), största djup före och efter, yta (m²), volym (m³, hela sänkans lagring), tillkommande volym (summan av ökningen), avstånd
   till kanten, åtgärdsförslag. Sorterat på tillkommande volym; samma indata ger samma lista.
7. K1-vägen (`kontrollera_skarvar_k1`) beskär rastret till lappen + band + 40 m (sänkgrafen byggs i Python) och nekar fönster över 1,5 miljoner celler med ett tydligt fel; före och efter beskärs lika.

**Koppling:** `POST /api/kartverktyg/<projekt>/validera` accepterar K9 `markhojd` (polygon + `niva_rh2000`) och svarar additivt med `skarvar` (`floden-skarvkontroll/v1`, K49, eller null) och
texterna i `varningar` (högst tre; resten i `skarvar.fynd`). Webben visar `varningar` redan som text i steg 2.

**Begränsningar** (ärliga): bara markytor (`markhojd`) i vattnets rutor; byggnader som dammar ingår i `_instangt_vatten`, inte här. Fönstrets kant räknas som utlopp. Sänkor under 5 cm ignoreras. Kontrollen är en
tidig varning, inte vattenberäkningen: dynamik, infiltration och ledningar ingår inte. Åtgärdsförslaget är text, inte en automatisk ändring (R5/R6 föreslår och räknar om).

## 8. Stegvis plan

| Steg | Innehåll | Storlek | Beroenden | Kontrakt (additivt) |
| --- | --- | --- | --- | --- |
| **R1** | Skarvkontrollen i Python, `markhojd` i `validera`, tester | S–M (klart) | G1, K9 | `validera`-svar får `skarvar`; `markhojd` tillåts i kartverktygets kontroll; `floden-skarvkontroll/v1` |
| **R2 (klar 2026-11-24)** | Lagermodell: ritlager med roll, geometri, stil, flaggor, källa, dataklass; ritlagerpanelen i steg 2; mallar (`data/ritlager/standard.toml`); lagren sparas **i förslagsfilen** (`ritlager`, inte en separat `ritlager.json`) | M | R1 (för att rollen `mark` ska finnas) | K50 `floden-ritlager/v1` (ny, ändrar inte K9) |
| **R3 (klar 2026-11-24)** | Linjer och ytor i klienten: rita linje/yta med z, fäst mot linjer och kanter, följ kant, klipp, slå ihop, vänd, förenkla, tangentbord (X G V), fingerräckvidd; brytlinjer som K9 `brytlinje_ny`/`brytlinje_bort` och `oppning_ny` | L | R2 | K9: nya ändringstyper `brytlinje_ny`, `oppning_ny` (additivt; K9 v1 behålls); K1 oförändrad |
| **R4 (klar 2026-11-24)** | Mark i nivåer i kärnan: lutande yta, slänt i dagsljus, exakt kantavstånd med täckningsvikt (ingen trappa), `delta_m`; ersätter hård kant i `HojdYta` när något av de nya fälten anges | L | R1 | K9 `markhojd` får `lutning`, `delta_m`, `slant`; K3 `HojdYta` får `lutning`, `delta_m`, `slant_forhallande`, `slant_bredd_m`, `slant_markslag` (valfria fält, K3 v1 läses oförändrat) |
| **R5 (klar 2026-11-24)** | Skarvkontrollen i klienten: markerar fynden i kartan (punkt, yta, överfallspil), listar dem i steg 2, knappen "Jämna ut skarven" (föreslår slänt/utlopp, kör om kontrollen) | M | R1, R3, R4 | `skarvar.fynd` (finns); ev. `atgarder` additivt |
| **R6 (klar 2026-11-24)** | 3D: takformer i `extrudera`, markytor och slänter som yta i 3D-vyn, brytlinjer som murar; förhandsvisning live | M–L | R3, R4 | Inget nytt kontrakt (visning); `/api/3d/` kan få förslagets mark |
| **R7** | Pekskärm och tillgänglighet: håll för att flytta, fingerräckvidd, stora handtag, tangentbordsbindningar som kan ändras, verktygsrad som i U4 | S–M | R3 | – |

Ordning: R1 (klar) -> R2 och R4 kan göras parallellt -> R3 -> R5 -> R6 -> R7. Första användbara leverans för Joels önskan är R1 + R4 (båda klara i kärnan och API:t; klientens ritverktyg återstår i R3/R5): ny mark med slänt och automatisk kontroll av skarven, även innan ritverktyget för linjer är klart (ytan kan då
läggas via K9 eller MCP `scenario_skapa`).

## 9. Källor och licens

- O-Tid (github.com/joelberring/otid, MIT, © 2026 Joel Berring), läst vid `b0b952f`. Kod som porteras får filhuvud med sökväg i O-Tid och commit, som i `webb/src/karta/*.ts` redan görs (`4a446af`); MIT-text ska
  bevaras (`docs/ARKITEKTUR-DRIFT.md` avsnitt om återanvändning). I den här leveransen har ingen O-Tid-kod kopierats; skarvkontrollen är skriven för Floden (GPL v3).
- Sänkgraf: Barnes m.fl. (Priority-Flood, Fill–Spill–Merge) som i `src/sankgraf/hierarki.py`.

## 9. R3 byggt (2026-11-24)

**Verktyg** (`webb/src/karta/linjeverktyg.ts`, `Ritverktyg` delegerar): rita linje (E) och yta (Skift+W) i valt ritlager (mur, kant, passage, mark, hårdgjort, vegetation, vatten), markera, dra hörn och figur, dra mittpunkt till nytt hörn, lägg till (A) och ta bort (S) punkt (Ctrl/Cmd+klick i Markera växlar), klipp (X), slå ihop (G), vänd (V), förenkla (Skift+S, 0,25 m), ta bort (Delete), ångra och gör om (ett steg även för klipp och slå ihop), fäst (T) mot hörn, skärningar och kanter hos befintliga byggnader, nya hus, andras figurer, basens brytlinjer och projektets avgränsning, rät vinkel (R eller Ctrl), **följ kant** (F eller håll Skift: nya punkter följer kanten den senaste punkten ligger på och får kantens egna hörn, så att kanterna sammanfaller exakt; kortaste vägen runt en ring), fingerräckvidd 22 px (mus 10 px). Tangenterna kan ändras i panelen Tangenter och sparas i webbläsaren (`tangenter.ts`).

**Egenskapspanelen** (`egenskaper.ts`, `flode/forslag.ts`): fält efter lagrets roll med enheter: mark (höjd som ny nivå m RH 2000, höjning m, fall % och riktning grader, lutning ur hörnens höjder, slänt 1:n eller bredd m), hårdgjort (nivå), vegetation (höjd m, markslag), vatten (volym m³, botten), brytlinje (typ, höjd m; eller ta bort med tolerans m), öppning (typ, bredd m, botten m RH 2000). Tal skrivs med decimalkomma och visas via `tal.ts`. Servern kontrollerar värdena; skarvkontrollens varningar (K49) visas som förut.

**Porterat från O-Tid** (MIT, joelberring/otid): @ b0b952f `map-project-join.ts` (klipp, slå ihop, vänd) -> `linje.ts`; `generalize.ts` (Douglas–Peucker) -> `linje.ts`; `map-project-draw-assist.ts` (`locate`, `followLine`, `followEdge`) -> `foljkant.ts`; `map-project-keys.ts` -> `tangenter.ts`; `edit.ts` (lägg till, ta bort, flytta punkt) -> `linje.ts`. Fäst, träff, hörnredigering och historik var porterade i R2 (@ 4a446af); `snap.ts` fick `oppna` för öppna linjer. Ursprunget står i varje filhuvud.

**Avvikelser och gränser.** (1) Z per punkt är ett hjälpmedel i klienten och sparas inte i K50-geometrin; lutningen skickas i egenskapen `lutning` (`punkter`, `a`+`b` eller `punkt`+`fall`), som klienten bygger ur hörnens höjder. Två höjdlinjer (`hojdlinje_a`/`hojdlinje_b`) kan läsas och bevaras men skapas inte i ritverktyget. (2) Linjer är raka sträckor, inga Bézierkurvor. (3) Klipp gäller linjer, inte ytor. (4) Slå ihop kräver samma lager; ändar som inte möts binds med en rak sträcka. (5) Provat i test och build, inte i webbläsare eller på pekskärm.

## 10. R5 och R6 byggt (2026-11-24)

**R5 Skarv i klienten** (`webb/src/karta/skarv.ts`, `flode/forslag.ts`, `src/sankgraf/skarvatgard.py`). Fynden (K49) ritas i kartan i steg 2: sänkan som yta (`omrade`), annars som cirkel med samma yta, punkt vid botten, överfallspil mot mottagaren, numrerad markör med popup; listan i panelen ger djup och volym (via `tal.ts`) och klick zoomar till fyndet. Fynden blir föråldrade (grå) när förslaget ändrats efter kontrollen. Knappen **Jämna ut skarven** anropar `skarvatgard`: servern prövar i fast ordning slänt 1:3, 1:5, 1:10 och därefter ett utlopp, kör om kontrollen för varje kandidat och tar den första som tar bort fyndet (annars den som förbättrar mest, upp till tre rundor). Panelen visar text, före och efter; **Tillämpa** lägger ändringen i förslaget (allt eller inget, i gruppen `skarv-atgard`, ångra tar bort den) och kör om kontrollen; **Avbryt** ändrar inget. Samma indata ger samma förslag (testat).

**R6 3D** (`webb/src/vy3d/tak.ts`, `planyta.ts`, `planinnehall.ts`, `scen.ts`, `geometri.ts`, `karta/fordrojning.ts`). Takformer i `extrudera`: platt (som förut), sadel (27 grader), pulpet (10 grader, faller mot söder), mansard (knäck vid 70 % av avståndet); `hojd_m` är nockhöjden, väggarna följer taket och takytan delas längs knäckarna (areabevarande klippning). Ny mark ritas som yta på sin nivå eller lutning, slänten som remsa utåt (bredd |Δ|·n eller angiven bredd), schakt som skuggbild genom terrängen; murar och kanter (brytlinjer med höjd) som band med typens tjocklek. I steg 2 finns knappen "3D-förhandsvisning" som visar ritat förslag i den befintliga 3D-vyn och bygger om bara planen 150 ms efter senaste ritändringen (`fordrojd`).

**Gränser.** (1) En slänt tar inte bort en dammvall tvärs en dalgång (se R4); då föreslås utlopp. (2) Utloppet är en 1 m bred passage, inte en dimensionerad ledning. (3) Takvinklar och pulpetens riktning är visningsval, inte K9-fält; takvattnet läser pulpet utan azimut som brutet tak. (4) Schakt syns bara som ton genom terrängen. (5) Markens höjd i 3D är basens. (6) Provat i test och build, inte i webbläsare eller på pekskärm.

## 11. Prov i webbläsare och R7 (2026-11-24)

**Provet** är `tools/ritverktyg_prov.py` (Playwright, Chromium/Chrome; förutsätter `floden demo`, `floden webb --port 8795` och byggd webb). Det går igenom steg 2 till 4 i demokvarteret med
**riktiga** musklick, mushjul, tangenter och beröring (`page.mouse`, `page.touchscreen` och CDP:s `Input.dispatchTouchEvent`), inte interna anrop. Det enda som läses ur sidan är kartmotorn
(när `window.__FLODEN_PROV__` är satt, bara av provet) för att räkna om SWEREF-punkter till skärmpixlar. Körs `python tools/ritverktyg_prov.py` (4-5 minuter, mest körningen i steg 3; `--utan-korning`, `--bara bred|smal|tangenter`).
Skärmbilder: `docs/bilder/ritverktyg/` (01-16 bred, 20-26 smal 390x844 med beröring, 30-31 tangenter). Provet tar bort sina egna förslag (`ritprov`, `ritprov-smal`) efteråt.

**Vad som fungerar** (alla kontroller gick igenom, 40 + 21 + 7): lagerpanelen och val av ritlager; Rita hus med fäst mot gårdens hörn (219; 219 exakt) och höjd i panelen; markyta med Enter, höjdval,
nivå och slänt 1:3 i egenskapspanelen; skarvfynd (6 sänkor, 31,5 m³) för en upphöjd yta tvärs gatukorsningen och **Jämna ut skarven** (förslag med tre utlopp, före 6 sänkor och 31,5 m³, efter 3 och 5,8 m³,
tillämpas och kan ångras); mur med följ kant (F och Skift) där punkterna sammanfaller exakt med ytans hörn; öppning (passage) med bottennivå; klipp, ångra och gör om i ett steg; markera en yta och dess
egenskaper; 3D-förhandsvisning; sparat förslag som överlever omladdning; steg 3 (körning, Resultat öppnas av sig självt) och steg 4 med förslaget som "Ditt förslag". Smal skärm: ingen sidledes rullning,
verktygsraden en rad, pekytor minst 40 px, tryck-ritning med Klar, Backa en punkt och Avbryt, håll och dra flyttar ett hus, ett snabbt drag panorerar, 3D ryms, alla kontroller har namn. Tangentbord: bindning ändras,
sparas och återställs; Tab når verktygsknapparna med synligt fokus.

**Fel som provet hittade och som är rättade** (varje med regressionstest i `webb/src/`):
1. *Yta med Enter blev tom.* `avsluta` i `linjeverktyg.ts` tömde hörnlistan innan den kopierats när sista hörnet inte dubblerats (Enter efter fyra klick; bara dubbelklick fungerade). Ytan hade 0 hörn och servern svarade "minst tre olika hörn". Testet kontrollerade bara att geometrin var en Polygon.
2. *Nya markytor visade "Ny nivå" men inget fält.* Höjdvalet saknade tomt val, så "Ny nivå" var förvald utan att något satts och ett val av den gav ingen ändring. Nu börjar listan med "Välj hur ytan får sin höjd".
3. *Skift+klick (följ kant) nådde aldrig ritverktyget.* MapLibres rutzoom (`boxZoom`) tog Skift-klicket. Avstängd; F fungerade redan.
4. *NaN när pekaren lämnar kartan under ritning* ("Längd NaN m", `Invalid LngLat object` i konsolen, även med mus när man går till verktygsraden mitt i en ritning): `lamnar` skickade NaN som position. Ny `lamnar()` tar bara bort förhandsvisningen.
5. *favicon.ico gav 404 vid varje sidladdning* (ett konsolfel). Ikonlänk tillagd.
6. *Pekskärm: ett drag i mitten av ett litet hus tog en mittpunkt i stället för huset* (handtagens räckvidd 22 px täckte huset). Räckvidden begränsas nu till en fjärdedel av figurens smalaste mått inuti figuren.

**R7 byggt:** håll fingret stilla 450 ms mot en markerad figur (eller ett handtag) och dra för att flytta (`kartmotor.ts`: `touchstart`/`touchmove`/`touchend`; rör fingret sig före hållningen panorerar kartan); handtag och fästring större med grov pekare (`pointer: coarse`); fingerräckvidd också för husets hörn
(som linjer och ytor redan hade); verktygsraden är en sidledes rullande rad på smal skärm (förut fem rader som täckte halva kartan); knapparna Klar, Backa en punkt och Avbryt (Enter, Backsteg och Esc) så att man kan avsluta utan tangentbord; hjälptexterna säger "tryck" på pekskärm; zoomknapparna 44 px.
Tangentbindningarna (R3) och etiketterna fanns redan och är nu prövade.

**Gränser, ärligt.** Prövat i Chromium med beröringsemulering, inte på en riktig telefon (iOS Safari: långt tryck kan ge förstoringsglas eller snabbmeny trots `-webkit-touch-callout: none`; ej känt). Stödet för att lägga till och ta bort punkt (`punktAtgard`) har fortfarande fast 10 px träffyta (inte fingerräckvidd). Två fingrar (nyp) zoomar kartan som förut men det är inte provat. Dubbeltryck för att avsluta
är opålitligt och ersätts av Klar eller ett tryck på första hörnet. Provet använder en upphöjd yta i gatukorsningen för skarvfyndet; **Jämna ut skarven tog bort tre av sex sänkor och 82 % av volymen, inte alla** (panelen säger det: "Åtgärden tar inte bort alla sänkor"). Alla tal i klienten går genom `tal.ts`; provet skriver sina egna siffror med decimalkomma.

