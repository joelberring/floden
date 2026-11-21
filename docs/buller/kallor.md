# Källor för bullermodulen

Status per 2026-11-21 (tabellen uppdaterad av B1 samma dag). "Nåbar" betyder att dokumentet gick att läsa från byggmiljön.

| Dokument | Används till | Nåbar | Plats |
| --- | --- | --- | --- |
| Nord2000 Part 1, DELTA AV 1849/00 (rev. 2006) | spridning utan refraktion (B4) | ja (fil, 2026-11-21) | lokal källmapp `metod/AV-1849-00.pdf`; hämtad från web.archive.org/web/20240618064154id_/https://forcetechnology.com/-/media/force-technology-media/pdf-files/projects/nord2000/nord2000-comprehensive-outdoor-sound-propagation.pdf. FORCE:s egen länk leder numera till en informationssida. |
| Nord2000 Part 2, DELTA AV 1851/00 (rev. 2006) | spridning med refraktion (B4) | ja (fil; även direkt på webben) | lokal källmapp `metod/AV-1851-00.pdf`; forcetechnology.com/da/-/media/force-technology-media/pdf-files/projects/nord2000/nord2000-comprehensive-outdoor-sound-propagation-model---part-2-propagation-in-an-atmosphere-with-re (50 s., gick att läsa 2026-11-21) |
| Proposal for Nordtest Method, DELTA AV 1106/07 (rev. 2014) | samlad metodbeskrivning (B4) | ja (fil) | lokal källmapp `metod/AV-1106-07.pdf`; hämtad från web.archive.org/web/20240221070539id_/https://forcetechnology.com/-/media/force-technology-media/pdf-files/projects/nord2000/nord2000-nordtestproposal-rev4.pdf. Den direkta länken gav en informationssida. |
| Validation of the Propagation Model, DELTA AV 1117/06 | 544 punkt-till-punkt-fall (B1, B4) | ja (text, figurer oläsbara) | forcetechnology.com/-/media/force-technology-media/pdf-files/projects/nord2000/nord2000-validation-of-the-propagation-model-av-1117-06.pdf |
| Revised test cases for updated version of Nord2000-road, Miljøprojekt 1335/2010 | testfallens indata och tillåtna avvikelser, äldre omgång (B1) | ja (PDF och fil) | lokal källmapp `testfall/2010/Miljoprojekt-1335.pdf`; edit.mst.dk/media/ecyi5sso/revised_test_cases_for_updated_version_of_nord2000.pdf; HTML-utgåva www2.mst.dk/udgiv/publikationer/2010/978-87-92668-26-4/html/default_eng.htm (bilagorna är bilder där) |
| Excel-filer till Miljøprojekt 1335 (TestStraightRoad, TestCurvedRoad, TestCityStreet, TestYearlyAverage, alla `_20100610.xls`) | facit per fall, äldre omgång (B1) | ja (filer); inte importerade | lokal källmapp `testfall/2010/`; www2.mst.dk/udgiv/publikationer/2010/978-87-92668-26-4/TestStraightRoad/TestStraightRoad_20100610.xls (motsvarande för de tre övriga) |
| Test Cases for Road Traffic Noise – Nord2000, Referencelaboratoriet RL 18-18 (version december 2018) | gällande testfall och tillåtna avvikelser (B1) | ja (PDF och fil) | lokal källmapp `testfall/RL-18-18.pdf`; referencelaboratoriet.dk/media/5rdfg2ui/rl18-18_test-cases-for-road-traffic-noise-nord2000_version-december-2018.pdf |
| Excel-filer till RL 18-18 (`teststraightroad`, `testcurvedroad`, `testcitystreet`, `testyearlyaverage`, alla `_20181101.xls`) | gällande facit (B1); importerade till `testsvit/N_nord2000/N18*` | ja (filer; webbhämtning ger bara binärdata) | lokal källmapp `testfall/`; referencelaboratoriet.dk/media/05ml00mq/teststraightroad_20181101.xls, …/o01pcbwb/testcurvedroad_20181101.xls, …/hpeg13n1/testcitystreet_20181101.xls, …/pzsjlvc4/testyearlyaverage_20181101.xls |
| A comment on two errors in the official test cases for Nord2000 road traffic noise version December 2018, Kunskapscentrum om buller (A. Gustafson, VTI), 2024-05-20 | kända fel i testfallen; undantag i jämförelsen (B1) | ja (fil) | lokal källmapp `testfall/KCB-2024-rattelser.pdf`; kunskapscentrumbuller.se/documents/Comment%20on%20two%20errors%20in%20the%20Nord2000%20test%20cases%20for%20road%20traffic%20KCB%202024.pdf |
| User's Guide Nord2000 Road, DELTA AV 1171/06 (Kragh, Jonasson, Plovsing, Sarinen 2006) | användning; innehåller inga emissionskoefficienter (B5) | ja (PDF) | egra.cedex.es/EGRA-ingles/I-Documentacion/National_Methods/Users_Guide_Nord2000_Road.pdf |
| Källmodell: Jonasson, Acoustic Source Modelling of Nordic Road Vehicles, SP Rapport 2006:12 (rev. 2006-10-17) | emissionskoefficienter, primärkälla (B5); inlagda i `src/buller/emission/data/` | ja (fil) | lokal källmapp `metod/SP-2006-12.pdf`; kunskapscentrumbuller.se/documents/Source%20modelling%20report%20final%20rev%20061017.pdf |
| Användarhandledning Nord2000 version 1.0, Kunskapscentrum om buller, 2026-01-31 | svenska inställningar (B5, B9); inte använd ännu | ja (fil; webbplatsen gick inte att hämta från byggmiljön) | lokal källmapp `metod/KCB-Anvandarhandledning-20260131.pdf`; kunskapscentrumbuller.se/documents/slutversioner/Anv%C3%A4ndarhandledning%20Nord2000%20version%201.0%20KCB%20260131.pdf; översiktssida kunskapscentrumbuller.se/anvisningarn2k.html |
| Nord2000 – Prediction of Outdoor Sound Propagation, Amendments 2018, FORCE TC-101327 | ändringar i spridningsmodellen (B4) | ja (fil) | lokal källmapp `metod/TC-101327.pdf`; forcetechnology.com/-/media/force-technology-media/pdf-files/projects/nord2000/118-22465---vejdirektoratet---nord2000-prediction-of-outdoor-sound-propagation-amendments-2018---tc.pdf |
| Test Cases for Railway Noise – Nord2000, RL 14-18 (2018) med Excel-filer | spårtrafik (inte aktuellt nu) | ej prövad | referencelaboratoriet.dk/media/3kckrkii/rl14-18_test-cases-for-railway-noise-nord2000_ver-28september-2018.pdf |
| Brukerveileder Nord2000 Road, Statens vegvesen Håndbok V717 (2019; tidigare Håndbok 290, 2014) | norsk översättning av User's Guide (jämförelse) | bara katalogposten läst | kudos.dfo.no/documents/3005/files/2922.pdf och kudos.dfo.no/documents/1257/files/1230.pdf |
| Rekommendation Nord2000, Nationell samordning av omgivningsbuller 2024-02-20 | normläge | ja | utv.naturvardsverket.se/492ac2/contentassets/42a2605fb3664aacbb06207f5238b9cc/nord2000.pdf |

Den lokala källmappen ligger utanför repot och anges med `[buller] kallmapp` i `config.local.toml`
(valfri). Förteckning med ursprungsadresser och sha256 finns i källmappens `kallforteckning.csv`.

## Följder

- Metodrapporterna och testfallens Excel-filer finns sedan 2026-11-21 i den lokala källmappen (utanför git), med
  ursprungsadresser och sha256 i `kallforteckning.csv`. Sökvägen anges i `config.local.toml` under `[buller] kallmapp`.
- Rapporterna checkas aldrig in.
- **Testfallens facit ligger inte i det publika repot** (beslut 2026-11-21 inför första push). `testsvit/N_nord2000/` och
  `docs/buller/kontroll/testfall_tabeller.md` står i `.gitignore` och skapas lokalt:
  `python -m buller.testfall.xlsimport --kallmapp <källmapp> --ut testsvit/N_nord2000`, där källmappen innehåller
  `testfall/` enligt `kallforteckning.csv`. Testerna som kräver fallen hoppas över när mappen saknas, så CI prövar
  inte överensstämmelsen; den körs lokalt.
- Metodens tabeller (AV 1106/07, i `src/buller/nord2000/`) och källmodellens koefficienter (SP 2006:12, i
  `src/buller/emission/data/`) ligger kvar i repot med källa per värde: utan dem går metoden inte att räkna.
- Gällande testfall är RL 18-18 (2018). Kunskapscentrum om bullers två rättelser (2024) hanteras som uttryckliga
  undantag i jämförelsen, inte som ändringar av facit.
