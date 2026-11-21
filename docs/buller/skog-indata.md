# Indata för skog (spridningszoner, delmodell 10)

Anteckning 2026-11-21. Inget av detta är infört i kod.

## Vad modellen behöver

Enligt Kunskapscentrum om bullers användarhandledning (v1.0, 2026-01-31, bilaga 4) är n_Q produkten av trädens
medeldiameter och trädtätheten per m². Därtill behövs skogens medelhöjd H, absorptionskoefficienten α, k_p och
stamradien. Testfallens värden (RL 18-18 tabell 2) är n_Q = 0,075 m⁻¹, H = 10 m, α = 0,1, k_p = 1,25, a = 0,15 m.

## Föreslagen källa: Skogliga grunddata (Skogsstyrelsen/SLU, öppna data)

Raster med grundyta G (m²/ha), grundytevägd medeldiameter D och grundytevägd medelhöjd. Ur dem:

    n_Q = 4·G / (π·D·10 000)        (D i meter)

Att kontrollera innan det byggs: enheter och upplösning i aktuell produktbeskrivning, täckning i tätort (små parker
och trädrader), och att grundytevägd medeldiameter underskattar stamantalet i olikåldrig skog.

Karttapullautins punktandelar per höjdskikt mäter undervegetation och framkomlighet, inte stamantal och diameter.
De kan användas för att avgränsa zoner, inte för n_Q.

## Förslag till hantering i lägena (beslutas av Joel)

- Skissläge: skog på, matad från Skogliga grunddata, för att se storleksordningen.
- Rapportläge: skog av som förval; slås på uttryckligen med parametrarna redovisade. Skäl: handledningen anger att
  noggrannheten i enskilda punkter är låg och att modellen inte kan rekommenderas för vanliga planärenden, och att
  testfallens värden saknar verifiering mot verkliga fall.

## Kända luckor i metoden (se `docs/buller/logg/P-10.md`)

Tabell 9 slutar vid R' = 10 och h' = 1. Med testfallens täthet nås h' = 1 vid 13,3 m trädhöjd. Med lägre, mer
realistisk täthet nås gränsen senare; det ska kontrolleras mot verkliga rastervärden.
