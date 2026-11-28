# Floden – grafisk riktlinje (utkast)

*2026-11-28. Gäller vägen "C5" från Joels layoutskisser (Claude Design-tavla "Floden – layoutskisser", med rapportsidor, diagram och en exempelrapport). Webben följer riktlinjen sedan grenen `design/c5-grafisk-profil`: tokens i `webb/src/tema.css`, typsnitten via `@fontsource` (SIL OFL, byggs in, inga externa anrop), skiktpanelen i `webb/src/flode/arbetsyta.ts`/`.css`. Rapportexporten (PDF och Word) är nästa etapp.*

**Avvikelser i webben från skissen:** ämnena är sex i appen (Skyfall, Sol & Dagsljus, Mikroklimat, Ekologi & GYF, Vind & Luft, Buller); Mikroklimat har fått färgen #d9b39a (solvarm sten ur målningen). På mobil har flikarna korta namn (Skyfall, Sol, Klimat, Ekologi, Luft, Buller); det fullständiga namnet finns kvar för skärmläsare.

## Idé
Platta färgblock i stället för flytande vita rutor med ram. Varje ämne har en färg från målningen på inloggningssidan (Emm Berring). Färgen följer frågan från fliken till blocket och till rapporten. Hierarkin byggs med storlek och vikt, inte med färg. Resultatspalten till höger är lugn: svart text, tunna linjer och bara en liten färgmarkör. Kartan ska få så mycket plats som möjligt, så paneler går att fälla in.

## Arbetsytan (dator)
- Överst: Floden · geografi · projekt, och Karta | Rapport | Underlag.
- Vänsterkant: stående flikar, en per ämne. En flik blir högre ju fler frågor ämnet har. Inaktiva flikar är indragna 8 px. Den aktiva fliken går ihop med ett uppfällt block i ämnets färg, där frågorna står som rader med tunna linjer emellan. Den valda frågan har vikt 600 och understrykning.
- Fälla in och ut: blocket med frågor fälls in med pilknappen i blockets rubrikrad eller genom att klicka på den aktiva fliken igen. Då finns bara flikarna kvar och kartan växer. Ett klick på en flik fäller ut blocket igen.
- Mitten: kartan. Väljaren för Karta, 3D och Sektion sitter i kartans hörn. Under kartan: Rita förslag, scenario (Nuläge, Alt A, Alt B) och visning (Före, Efter, Skillnad, Svep). Dessa gäller hela projektet och ligger därför inte i ämnesblocket.
- Höger: ämnesmarkör, svarsrubrik, nyckeltal mellan två linjer, påstående, tabell över alla frågor i ämnet (nuläge mot alternativ), uppgifter om körningen och knappen "Lägg i rapporten" med bara ram.
- Bakgrund: Vit eller Kall grå (#eef1ee). Inte gräddvit.

## Mobil
Kartan tar hela höjden mellan sidhuvudet och en hopfälld panel längst ner.
- Verktyg: en mörk knapp i kartans hörn visar läget, till exempel "Alt B · Efter". Den fäller ut en panel över kartan med scenario, visning och Rita förslag.
- Panelen längst ner: när den är hopfälld syns ämnesflikarna i en liggande rad med korta namn (Skyfall, Sol, Buller, Luft, Ekologi) och en rad med den valda frågan och kortsvaret, till exempel "0,24 m · nuläge 0,11 m". Ett tryck på raden eller på den aktiva fliken fäller ut panelen, som då visar frågorna, svaret och Lägg i rapporten. Utfälld är den som högst 520 px hög, så att en bit karta alltid syns.
- Klickytor minst 44 px.

## Färger
| Roll | Färg |
|---|---|
| Text, mörka knappar | #1f2414 (mossa), #2a311c |
| Grå text | #55574a |
| Linjer | #d9d3c2 |
| Skyfall | #a9d6c0 (mint); kartans djupskala #d7ebe1 → #a9d6c0 → #6fb6ad → #2f5f5c |
| Sol och dagsljus | #d4b74f |
| Buller | #a4654c (vit text på fliken) |
| Vind och luft | #b3a3b3 |
| Ekologi | #8b9655 |

Kartans färgskalor ska skilja sig från blockets färg: mörkare eller mer mättade toner i kartan än i fliken. Diagram har egna toner, se avsnittet Diagram.

## Typsnitt
| Familj | Uppgift |
|---|---|
| Newsreader | Det man läser: rubriker, nyckeltal, frågor, påståenden, rapportens brödtext |
| Public Sans | Det man gör: knappar, flikar, blockrubriker, etiketter |
| IBM Plex Mono | Det man kan spåra: körnings-id, tal i tabeller, enheter, status |

Alla tre har öppen licens (SIL OFL) och ska ligga på Flodens egen server, inte hämtas från Google.

## Nivåer i appen
| Nivå | Typsnitt och storlek | Används till |
|---|---|---|
| R1 Svarsrubrik | Newsreader 400, 26/30 px (mobil 24) | Frågans titel i svaret, projektnamn i listan |
| R2 Blockrubrik | Public Sans 700, 17/20 px | Ämnet i det uppfällda blocket, avsnitt i inställningar |
| R3 Etikett | Public Sans 600, 12/14 px, grå | Grupper, tabellhuvuden, "ämne · alternativ" |
| Nyckeltal | Newsreader 400, 40 px (mobil 36), lining + tabular | Svarets tal; enheten i samma storlek efter fast mellanslag |
| Brödtext | Newsreader 400, 16/26 px, högst 60 tecken per rad | Påståenden, förklaringar, tomma lägen |
| Frågerad | Newsreader 15/20 px, vald 600 + understrykning | Frågor i blocket och i tabeller |
| Knapp och flik | Public Sans 600, 13 px (mobil 14 i knappar i full bredd) | Allt klickbart; flikar står upp på datorn |
| Data | IBM Plex Mono 400, 12/19 px, grå | Spårbarhet, tal i tabeller, status |

## Regler
- Serif för det man läser, sans för det man gör, mono för det man kan spåra.
- Rubriker och text är alltid svarta eller grå. Färg bärs av block och små markörer, aldrig av text.
- Inga versaler och ingen spärrning. Meningsversal överallt, även på knappar.
- Högst tre textstorlekar i samma vy, utöver nyckeltalet.
- Två vikter per familj: 400 och 600 (700 bara för blockrubriken). Kursiv bara i ordmärket "Floden".
- Svensk sifferform: decimalkomma, fast mellanslag före enhet och i tusental (151 200).
- Inga rundade hörn, skuggor eller toningar. Block skiljs åt med 3–4 px mellanrum, inte med ramar.
- Paneler som kan fällas in har en pilknapp med aria-expanded, och läget sparas per användare.

## Rapporten
Svarta rubriker, serif i brödtexten, smal spalt och ingen numrering av rubriker eller bilder (enligt rapportstilen från 2026-11-22).

| Nivå | Typsnitt och storlek |
|---|---|
| Titel | Newsreader 600, 26 pt (steg 1: 24 pt) |
| Rubrik 1 | Newsreader 600, 20 pt |
| Rubrik 2 | Public Sans 700, 11,5 pt |
| Ingress | Newsreader 400, 13/20 pt |
| Brödtext | Newsreader 400, 11/16 pt, spalt högst 106 mm |
| Bild- och tabelltext | Public Sans 400, 9 pt, grå, ovanför bilden |
| Nyckeltal i marginalen | Newsreader 400, 30 pt |
| Underlag (körning, geografi, status) | IBM Plex Mono, 9 pt |

**Sidan (A4):** marginaler 20 mm i sidled. Brödtextspalten är 106 mm, och till höger om den finns en marginalspalt på 56 mm för nyckeltal, status och "Om underlaget". Kartor, diagram och tabeller går i full bredd (170 mm). Sidhuvudet har ordmärket, projektet och skedet, med en svart linje under. Sidfoten har rapport-id och sidnummer.

**Ämnen:** varje ämnesavsnitt inleds med ett litet block i ämnets färg (13 × 2 mm) ovanför rubriken. Rubriken är svart. I sammanfattningstabellen har varje rad en liten färgruta före ämnesnamnet.

**Steg 1, skiss:** en sida. En svart etikett "Skiss – ej för beslut", en rad per ämne (färgstrimma, ämne, mening, tal, status), en karta och ett diagram.

**Steg 2, samråd:** en första sida med titel, ingress, sammanfattning, "Om underlaget" i marginalspalten (geografi, scenario, körningar, metodstatus, ansvarig utredare, granskare) och tabellen "Resultat per fråga". Sedan ett avsnitt per ämne: ingress, en rubrik 2 per fråga, nyckeltalet i marginalen, karta och diagram.

## Diagram
- **Rubriken är påståendet**, i Newsreader 600, 17 px i appen och 12 pt i rapporten. Under rubriken står i grått vad som mäts, enhet och regn eller datum.
- **Fast jämförelse:** nuläget är alltid ljusgrått (#cfd1c9). Alternativet har ämnets mörka ton och ett tredje alternativ ämnets mellanton. Ämnenas blockfärger används inte i diagram, eftersom de är för ljusa och gråa för att läsas som data.

| Ämne | Mellanton | Mörk ton (alternativet) |
|---|---|---|
| Skyfall | #4cc0a8 | #00836c |
| Sol och dagsljus | #c9a12a | #94700c |
| Buller | #d0805f | #9a4a2e |
| Vind och luft | #a37fc6 | #67428f |
| Ekologi | #9fb04c | #5f6f24 |

Paren är kontrollerade med en palettvalidator: tillräckligt avstånd för färgblindhet och normalt seende, också mot den grå nulägestonen. Nuläget och mellantonerna har lägre kontrast än 3:1 mot vitt och ska därför alltid ha namn i teckenförklaringen eller direkt vid märket.

- **Märken:** staplar med 2 px mellanrum och raka hörn, linjer 2 px, toppvärdet markerat med en punkt (8 px, vit ring) och ett värde. Värden skrivs bara ut för alternativet och för det man vill peka på.
- **Axlar:** rutnät i hårfin grå linje (#e3e1d8), nollinje och axel i svart. Axeltal i Public Sans 11 px, grå. Utskrivna värden i IBM Plex Mono.
- **En axel per diagram.** Två mått med olika enhet blir två diagram.
- **Teckenförklaring** ovanför diagrammet vid två serier eller fler.
- **Underlaget** står under diagrammet: körning, metod och om det är skiss eller rapportläge.
- **I appen** visar hovring exakt värde och mottagare. **I rapporten** finns samma tal i en tabell eller i texten. Talen kommer alltid från Floden och är bundna till en körning.
