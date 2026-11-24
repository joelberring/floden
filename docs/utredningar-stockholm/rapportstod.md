# Rapportmall och granskning för utredningsappen

**En användbar slutsats anger vad planen medger, på vilken plats, under vilka förutsättningar och vad som återstår att säkra.** Använd frågans ID från [katalogen](fragor.json) för att länka ett sådant svar till källan, beräkningen och den ritning eller åtgärd som slutsatsen förutsätter.

Se [hur tolv Stockholmshandlingar är disponerade](rapportdisposition.md) för exempel på vad som hör hemma i sammanfattning, resultatkapitel, bedömning och bilagor i varje ämne.

## Från beställning till rapport

| Skede | Vad rapporten ska hjälpa läsaren att avgöra | Underlag att efterfråga |
| --- | --- | --- |
| Planstart och tidig sållning | Behöver frågan fördjupas, och var finns utrymme för ett annat förslag? | Stadens kartskikt och deras år, användning, kända ändringar, begränsningar och föreslagen omfattning. |
| Samråd | Kan den föreslagna markanvändningen fungera? Vilka alternativ eller skydd behöver prövas? | Jämförbara scenarier, motiverat metodval, känsliga mottagare, översiktliga kvantitativa underlag och förteckning över öppna frågor. |
| Granskning och antagande | Håller bedömningen även för den byggrätt och etappindelning som planhandlingarna faktiskt medger? | Kontroll mot senaste plankarta, reviderade mätetal, objektspecifika villkor, ansvar för åtgärd och referens till planbeskrivningen. |
| Projektering, byggande och drift | Har utförandet de egenskaper som utredningen räknade med? | Reviderad byggnadsgeometri, faktiskt glas, entré- och skärmkrön, växtbäddar, kontrollpunkter och ansvarig funktion. |

## Mall för en utredningsrapport

1. **Beslutssammanfattning.** Besvara utredningsfrågan med scenario, mottagare, bedömningsgrund och villkor. Ange vilken ändring som skulle få slutsatsen att falla.
2. **Uppdrag och avgränsning.** Ange användning, planskede, plangräns, relevant omgivning och vilket referensfall som svarar på frågan. Registrera beslutsdatum för regelval, modellens basår och prognosår separat.
3. **Bedömningsgrunder.** För varje kriterium, ange källa, paragraf/tabell, tillämpningsområde, rättslig status, giltighet, mått, enhet och eventuell tidsstatistik. Lägg till villkor och undantag. För ett lokalt mål, ange vem som har fastställt det och för vilken typ av yta.
4. **Indata och metod.** Lista källa, version/datum, geografisk täckning, åtkomstvillkor och kontrollsumma på använda filer. Redovisa upplösning, koordinatsystem, relevanta höjder, antagna parametrar, beräkningsprogram med version och fackmässig grund för vald noggrannhet. Märk varje indata som uppmätt, hämtad, projekterad eller antagen.
5. **Resultat.** Börja med de objekt som styr beslutet. För varje relevant scenario, visa värde, enhet, tid eller statistik, jämförelsegrund, marginal om den är meningsfull och länk till tabellrad eller kartlager. Före/efter kräver samma mottagare och dokumenterade skillnader i indata. Ange vilka objekt som saknar resultat.
6. **Åtgärd och avvägning.** Beskriv verkningssätt, plats, genomförbarhet, tidpunkt, markåtkomst och underhåll. Redovisa effekten i nytt beräkningsfall när åtgärden är avgörande. Ange vilken ritning eller handling som behöver säkra egenskapen.
7. **Osäkerhet och fortsatt prövning.** Namnge de antaganden som kan vända bedömningen, hur de har varierats och vilken komplettering som kan lösa frågan. Skilj en mät- eller modellavvikelse från en fortfarande oprövad juridisk avvägning.
8. **Bilagor för kontroll.** Bifoga kartor, tabeller, underlagsförteckning, manifest, metodversion, öppna avvikelser och granskningsstatus. Kommunala eller regionala exempel ska citeras med ärende och skede så att de inte förväxlas med resultat för den aktuella platsen.

## Samordning mellan områdena

| Utformningsbeslut | Frågor som måste mötas | Gemensamt underlag |
| --- | --- | --- |
| Sluta ett kvarter mot en trafikled | En skärmande byggnad kan förbättra gårdens ljudmiljö men ändra gaturummets luftomsättning, gårdens soltid och dagsljuset på nedre plan. | Samma byggrätt, takhöjder och etapp. Jämför buller vid fasad, halt på gångbana, dagsljus i kritisk bostad och sol på gården. |
| Placera förskolans lek och samling | Den tysta ytan kan vara solutsatt vid sommarsolståndet eller ligga i en lågpunkt. | Överlagra gårdens användningszoner med luft, ljud, skugga och skyfall. Beräkna vid behov hur stor *sammanhängande användbar* yta som uppfyller det valda villkoret. |
| Höj en entré eller lägg en vall | Tröskeln kan skydda byggnaden men ändra den tillgängliga vägen eller leda vatten mot grannen. | Gemensam marksektion, entré-ID och prövning av närmaste fungerande väg i samma regnscenario. |
| Glasad balkong, skärm eller ny trädrad | Ändrad avskärmning kan påverka ljud, ljustransmission, sol eller vind. Trädens storlek förändras över tid. | Ange transparenta och täta delar, materialegenskaper för respektive beräkning och scenarioår för vegetation. |
| Omforma en träddunge till översvämningsyta | Vattnets väg och groddjurens väg mellan lekvatten och övervintring kan påverkas på olika sätt. | Använd samma markförslag och etapp. Pröva både hydraulisk funktion och ekologisk åtkomst under relevant årstid. Se [ekologiguiden](ekologi.md). |

Tabellen anger frågor att pröva. Effektens riktning och storlek för det egna projektet bestäms av dess underlag.

## Vad AI:n kan skriva från

Bind projektresultat till körnings-ID, resultattabell, rad eller raster, mottagare, scenario, mått, enhet och tidsdefinition. Fyll textens tal med typade platshållare. Bind normtal och historiska mätvärden till en paragraf, tabell eller utpekad källpost. Lagra analys och jämförelse före avrundning. Visa precision som motsvarar underlaget. Två lika tal i olika tabeller får inte kopplas ihop enbart på siffervärdet. Arbetssättet ansluter till [T08](../../tasks/T08-rapporter.md) och [K0-manifestet](../DATAKONTRAKT.md).

**Exempel på rapportmening med saknat underlag.** ”Solstudien visar skugga på sittplatsen vid de redovisade klockslagen. För att avgöra hur länge platsen har direkt sol under öppettiden behövs en beräkning över hela intervallet.” Den meningen kan användas när ögonblicksbilder finns och en tidsserie saknas. Kräv samma upplösning i slutsatsen som i underlaget.

**Status per fråga.** Använd *visat*, *avvikelse*, *villkorat*, *underlag saknas* eller *ej tillämpligt*. För *villkorat* behövs en uttrycklig förutsättning och dess ansvariga funktion. För *ej tillämpligt* behövs saklig motivering. En publicerbar delrapport kan innehålla öppna frågor. Utelämna en samlad slutsats om lämplighet när en avgörande fråga saknar stöd.

| Kontroll inför export | Exempel på fel att fånga | Åtgärd i appen |
| --- | --- | --- |
| Spåra tal och figurer | En källrapports prognos återges som egen mätning eller en figur har ersatts. | Kontrollera härkomst, källa, enhet och filens kontrollsumma. Stoppa den felaktiga utsagan. |
| Kontrollera scenario och regelår | Resultat för gårdagens trafik jämförs med den tänkta planens förbättring eller ett äldre BBR-ärende får ny bostadsregel. | Kräv jämförbara scenario-ID:n och dokumenterat regelval. |
| Kontrollera mottagare och nämnare | Ett områdesmedel döljer en korsning, en bullerutsatt lägenhet eller en förskolegårds oskuggade lekyta. | Härled resultatet till rätt punkt, sträcka, rum, bostad eller användbar yta. |
| Bevara mått och tidsstatistik | Ett maxdjup tolkas som vattennivå, ett percentilvärde som årsmedel eller ett klockslag som timmar sol. | Kontrollera typ, enhet, referenshöjd, tidsfönster och aggregering innan jämförelse. |
| Bevara osäkerhet | Antagen glaskvalitet eller schablon för avledning återges som verifierad egenskap. | Lägg förutsättningen i slutsatsen och begär komplettering där den påverkar beslutet. |
| Kontrollera kartan | Saknade celler ser torra ut eller olika färgskalor döljer en förändring. | Kontrollera teckenförklaring, tomvärde, enheter, norr, skala och scenario i bildtexten. |
| Skydda indata | En bilaga innehåller personuppgifter eller skyddade ledningsdata. | Använd lokala beräkningsresultat och tillåtna rapportfält. Följ [projektets dataregler](../../AGENTS.md). |

## Exempel på hur ett källbelagt svar kan skrivas

**Fråga LU-07, Liljeholmen.** Vilka delar av gång- och cykelnätet behöver följas upp när Södertäljevägen omformas? Indata är SLB:s utredning av planförslaget för 2040. Underlaget är en extern beräkning från 2023 med då använd trafikprognos, dubbdäcksandel och bedömningsgrund.

> SLB:s beräkning för planförslaget visade att dygnsnormen för PM10 klarades vid de studerade fasaderna. På delar av gång- och cykelnätet vid korsningarna fanns däremot beräknade överskridanden. Dessa passager behöver därför redovisas var för sig vid en uppdatering av planen.

**Spårning i rapporten.** Märk texten *tidigare utredning*. Länka fasadpåståendet till resultatavsnittet om dygnsmedelvärden och passagepåståendet till avsnittet om gång- och cykelnätet. Om frågan gäller EU:s gränsvärden 2030 krävs en ny jämförelse med rätt ordningsvärde. Ett kort referat av 2023 års slutsats kan då återanvändas som bakgrund. [SLB 27:2023, sammanfattning och resultat s. 11–15](https://meetingspublic.stockholm.se/welcome-sv/namnder-styrelser/radet-till-skydd-for-stockholms-skonhet/mote-2025-03-24/protocol/luftutredningpdf?downloadMode=open) · [direktiv 2024/2881, bilaga I](https://eur-lex.europa.eu/legal-content/SV/TXT/PDF/?uri=CELEX%3A32024L2881)

## Prov vid införande

Använd kontrollfallet i slutet av varje ämnesguide som negativt acceptansprov. Komplettera med en positiv körning mot ett känt svar och en omkörning från samma manifest. För skyfall anger [valideringsplanen](../VALIDERING.md) referensfall och mätetal. För buller finns [lokala referensfall](../../kallor/buller/testfall/). För de andra disciplinerna behöver respektive metod ha ett eget relevant facit innan en beräknad slutsats kan användas skarpt. En godkänd kontroll av rapportens källspårning verifierar textens koppling till underlaget. Beräkningsmodellens träffsäkerhet kräver en egen validering.
