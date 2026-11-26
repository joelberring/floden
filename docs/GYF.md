# Svensk Grönytefaktor (GYF) och delfaktoranalys (skiss)

> **Metodstatus: `skiss`.** Samtliga kommunprofiler i [`data/gyf/profiler.toml`](../data/gyf/profiler.toml) är märkta `status = "skiss"` så att varje delfaktorvikt, tilläggsfaktor och K1-mappning kan granskas och justeras innan den används i skarpa detaljplaneutredningar.

Modulen [`src/ekologi/gyf.py`](../src/ekologi/gyf.py) beräknar **Grönytefaktor (GYF)** och delfaktoruppdelning deterministiskt direkt ur K1 v2-markslagsraster, valfri tomtmask, K50-överlagringar (t.ex. grönt tak, växtbädd på bjälklag, genomsläpplig beläggning, regnbädd) samt tilläggsfaktorer (t.ex. krontäckning för träd och småbiotoper).

---

## Matematisk definition och enheter

All beräkning sker i **SWEREF 99 18 00 (EPSG:3011)** med SI-enheter (`_m`, `_m2`, `_pct`).

För ett reguljärt markslagsraster med cellstorlek $h$ (`cellstorlek_m`, i meter) har varje cell arean $a_{\text{cell}} = h^2\text{ m}^2$. För de $N_{\text{tomt}}$ aktiva cellerna inom tomtgränsen (`tomt_mask`) är tomtarean:

$$A_{\text{tomt}} = N_{\text{tomt}} \cdot h^2 \quad [\text{m}^2]$$

Den totala ekoeffektiva ytan $E$ och grönytefaktorn $\text{GYF}$ beräknas exakt som:

$$E_{\text{grund}} = \sum_{i} A_i \cdot f_i, \qquad E_{\text{tillägg}} = \sum_{j} T_j \cdot g_j$$

$$E = E_{\text{grund}} + E_{\text{tillägg}} \quad [\text{m}^2]$$

$$\text{GYF} = \frac{\sum_{i} A_i \cdot f_i + \sum_{j} T_j \cdot g_j}{A_{\text{tomt}}} \quad [\text{dimensionslös}]$$

där:

| Storhet | Variabelnamn i kod | Enhet | Betydelse |
| --- | --- | --- | --- |
| $A_{\text{tomt}}$ | `tomt_area_m2` | m² | Total yta för kvarteret/tomten inom `tomt_mask` |
| $A_i$ | `area_m2` | m² | Yta som hör till grunddelfaktor $i$ |
| $f_i$ | `faktor` | – | Delfaktorvikt för grundyta $i$ ($f_i \ge 0$) |
| $T_j$ | `area_m2` | m² | Yta för tilläggsfaktor $j$ (t.ex. krontäckning i m²) |
| $g_j$ | `faktor` | – | Tilläggsvikt för tillägg $j$ ($g_j \ge 0$) |
| $E$ | `eko_effektiv_area_m2` | m² | Total ekoeffektiv yta |
| $\text{GYF}_{\text{mål}}$ | `riktmarke` | – | Profilens riktvärde (standard `0.60`) |
| $\text{Saknas}$ | `saknas_till_mal_m2` | m² | $\max(0,\; \text{GYF}_{\text{mål}} \cdot A_{\text{tomt}} - E)$ |

### Areabevarande och inga tysta fel

Varje cell inom `tomt_mask` hamnar i **exakt en** grunddelfaktor eller i `flaggade_celler`:

$$\sum_{i} A_i + A_{\text{flaggad}} = A_{\text{tomt}}$$

- **K1-kod `0` (okänt)** och alla andra koder som inte mappar till profilen räknas **aldrig** om genom gissning. De samlas i `flaggad_area_m2` och `okanda_koder`, ger bidrag `0.0` till ekoeffektiv yta och genererar en tydlig varning i `varningar`.
- **`NaN`, `±inf` eller icke-heltal** i flyttalsraster inom `tomt_mask` räknas i `ogiltiga_celler` och `flaggad_area_m2` och genererar en tydlig varning i `varningar`.
- Ogiltig `cellstorlek_m <= 0`, tom tomtmask (`tomt_celler == 0`), okänd profil eller okänd nyckel i `yt_overlagring` ger direkt `EkologiFel` (subklass av `ValueError`).

### Före/efter-jämförelse (`jamfor_gyf`)

För två körningar med samma profil beräknas exakta förändringar:

$$\Delta\text{GYF} = \text{GYF}_{\text{efter}} - \text{GYF}_{\text{före}}, \qquad \Delta E = E_{\text{efter}} - E_{\text{före}}$$

tillsammans med per-delfaktor-deltan (`delta_area_m2`, `delta_eko_effektiv_area_m2`, `delta_bidrag_gyf`), `uppfyller_mal_fore`, `uppfyller_mal_efter` och kvarvarande underskott `saknas_till_mal_m2` i efter-läget. Om tomtarean skiljer sig mellan före och efter läggs en tydlig varning till.

---

## Mappning av K1 v2-markslagskoder och K50-överlagringar

### K1 v2-markslagskoder (`docs/DATAKONTRAKT.md`)

| K1-kod | K1-namn | Delfaktor-id i profiler | Anmärkning |
| --- | --- | --- | --- |
| `0` | okänt | *(mappas ej)* | Flaggas alltid i `flaggad_area_m2`, `okanda_koder` och `varningar` |
| `1` | hårdgjort | `hardgjord` | Tät beläggning (asfalt, betong) |
| `2` | tak | `byggnad_tak` | Tätt tak |
| `3` | grönt tak | `gront_tak` | K1 anger ej substratdjup; schablonvikt per profil |
| `4` | gräs | `vegetation_mark` | Öppen vegetation på mark |
| `5` | skog | `skog_naturmark` | Skog / trädklädd naturmark på mark |
| `6` | berg i dagen | `berg_i_dagen` | Obebyggd berghäll (skissval) |
| `7` | grus | `genomslapplig` | Genomsläpplig / halvöppen beläggning |
| `8` | vatten | `vattenyta` | Öppen vattenyta |
| `9` | byggnad | `byggnad_tak` | Byggnad / tätt tak |

### K50-överlagring (`yt_overlagring`) och prioritetsordning

Eftersom K1-markslag inte skiljer på t.ex. markgrönska och växtbädd på garagebjälklag eller på en anlagd regnbädd, stöder `berakna_gyf_fran_raster` parameterobjektet `yt_overlagring: dict[str, np.ndarray]` där varje nyckel är ett delfaktor-id i profilen (t.ex. `regnbadd`, `vaxtbadd_bjalklag`, `gront_tak`, `genomslapplig`).

Överlagringar går före basrastret `markslag`. Om flera överlagringar täcker samma cell inom `tomt_mask` löses konflikten **deterministiskt** enligt `overlagring_prioritet` (högst prioritet först, ingen dubbelräkning) och en varning läggs till i `varningar`:

1. `regnbadd` (högst prioritet)
2. `vattenyta`
3. `vaxtbadd_bjalklag`
4. `gront_tak`
5. `genomslapplig`
6. `skog_naturmark`
7. `vegetation_mark`
8. `berg_i_dagen`
9. `hardgjord`
10. `byggnad_tak` (lägst prioritet)

---

## Kommunprofiler i `data/gyf/profiler.toml` (`status = "skiss"`)

Samtliga tre profiler har riktmärket **`0.60`** och `status = "skiss"`. Filens SHA-256 följer med i `GyfResultat.profilfil_sha256` och i JSON-exporten.

### Jämförelsetabell – delfaktorer (grundyta)

| Delfaktor-id | Beskrivning | `stockholm` | `goteborg` | `malmo` |
| --- | --- | ---: | ---: | ---: |
| `skog_naturmark` | Skog / naturmark på mark (K1: `5`) | `1.00` | `0.85` | `1.00` |
| `vegetation_mark` | Vegetation / gräs på mark (K1: `4`) | `1.00` | `0.55` | `1.00` |
| `vaxtbadd_bjalklag` | Växtbädd på bjälklag (K50) | `0.80` | `0.50` | `0.70` |
| `gront_tak` | Grönt tak / sedumtak (K1: `3` / K50) | `0.40` | `0.35` | `0.60` |
| `vattenyta` | Öppen vattenyta (K1: `8` / K50) | `1.00` | `0.65` | `1.00` |
| `regnbadd` | Regnbädd / dagvattenfördröjning (K50) | `1.00` | `0.75` | `1.00` |
| `genomslapplig` | Genomsläpplig beläggning (K1: `7` / K50) | `0.40` | `0.25` | `0.40` |
| `berg_i_dagen` | Berg i dagen (K1: `6`, skissval) | `0.20` | `0.15` | `0.20` |
| `hardgjord` | Hårdgjord tät yta (K1: `1`) | `0.00` | `0.00` | `0.00` |
| `byggnad_tak` | Byggnad och tätt tak (K1: `2, 9`) | `0.00` | `0.00` | `0.00` |

### Jämförelsetabell – tilläggsfaktorer (`m²`)

| Tillägg-id | Beskrivning | `stockholm` | `goteborg` | `malmo` |
| --- | --- | ---: | ---: | ---: |
| `trad_krontackning` | Träd (per m² krontäckning) | `0.40` | `0.50` | `0.50` |
| `befintligt_trad` | Befintligt stort träd (per m² krontäckning) | `0.60` | `0.65` | `0.70` |
| `buskskikt` | Buskskikt (per m²) | `0.20` | `0.25` | `0.20` |
| `smabiotop_regnbadd` | Småbiotop / regnbäddstillägg (per m²) | `0.20` | `0.20` | `0.25` |
| `klattervaxter_fasad` | Vertikal grönska / klätterväxter (per m²) | `0.30` | `0.25` | `0.30` |

### Källhänvisningar per kommun

1. **Stockholm (`stockholm`):** Stockholms stad (Exploateringskontoret & Stadsbyggnadskontoret), *Grönytefaktor för kvartersmark – Beräkningsmodell och vägledning* (ursprungligen utvecklad för Norra Djurgårdsstaden; normalt krav `0,60` vid 50–70 % bebyggd kvartersmark). Se [tillstand.stockholm](https://tillstand.stockholm/tillstand-regler-och-tillsyn/bygga-och-bo/gronytefaktor/) och sammanställningen på `gronytefaktor.se`.
2. **Göteborg (`goteborg`):** Göteborgs Stad (Stadsbyggnadskontoret / Park- och naturförvaltningen), *Grönytefaktor i Göteborg – Beräkningsmodell för kvartersmark och allmän platsmark*. I Göteborgs fulla modell värderas yttyper i `[0, 1]` över sex miljöutmaningar (biologisk mångfald, buller, dagvatten, lokalklimat, luftkvalitet och rekreation) och riktmärket varierar efter stadstyp (t.ex. `0,25` mycket tät stad, `0,35` relativt tät, `0,45` gles bebyggelse, `0,55` park). Här används oviktade schablonmedelvärden och ett enhetligt skissriktmärke på `0,60`. Se [goteborg.se](https://goteborg.se/wps/portal/start/bygga-bo-och-leva-hallbart/bygga-riva-och-forandra/detaljplanering/gronytefaktor) och `boverket.se`.
3. **Malmö (`malmo`):** Malmö stad (Stadsbyggnadskontoret), *Grönytefaktor för kvartersmark – Riktlinjer och beräkningstabell* (2014; ursprung Bo01 / Västra Hamnen, klassiskt riktmärke `0,60`). Centrala tabellvärden: markgrönska `1,0`, bjälklag 200–800 mm `0,7` ($\ge$ 800 mm `0,9`), grönt tak 80–200 mm `0,6` (40–80 mm `0,4`), genomsläpplig mark `0,4` (fogade plattor `0,2`), täta ytor `0,0`. Se [malmo.se](https://malmo.se/Bo-och-leva/Bygga-och-bo/Stadsplanering-och-detaljplaner/Gronytefaktor.html) och `gronytefaktor.se`.

---

## Begränsningar

- **Skissstatus:** Samtliga vikter i `data/gyf/profiler.toml` är `status = "skiss"` och har inte ännu stämts av rad för rad mot respektive kommuns senaste Excel-mall i skarpa bygglovs- eller detaljplaneärenden.
- **Jorddjup och substrattjocklek:** K1 v2-markslag innehåller inte jorddjup (t.ex. skillnad mellan 60 mm sedumtak och 400 mm sedum-örttak, eller 300 mm mot >800 mm bjälklagsbädd). Schablonvikter används tills projektspecifika lager eller egna profiler anges.
- **Balanskrav och lokala delkrav:** Stockholms balanskrav mellan ekosystemtjänster (ofta 60 %), Göteborgs valfria viktning av enskilda miljöutmaningar/trafiknivå och Malmös areatak för avvattning till grönska ingår inte i v1.
- **Trädtillägg:** Modulen beräknar inte krontäckning ur punktmoln automatiskt; träd och småbiotoper anges som `GyfTillagg` i `m²`.
- **Rastreringsupplösning:** Smala planteringsremsor, fogar eller brunnar mindre än `cellstorlek_m` kan rundas av vid rastrering.

---

## Användning i Python

```python
import numpy as np
from ekologi.gyf import (
    GyfTillagg,
    berakna_gyf_fran_raster,
    gyf_jamforelse_till_dict,
    gyf_till_dict,
    jamfor_gyf,
    ladda_gyf_profiler,
)

profiler = ladda_gyf_profiler()
markslag_fore = np.full((10, 10), 4, dtype=np.uint8)  # 100 m² gräs (cellstorlek 1,0 m)
markslag_efter = markslag_fore.copy()
markslag_efter[:5, :] = 9  # 50 m² nytt hus

gront_tak_mask = np.zeros((10, 10), dtype=np.bool_)
gront_tak_mask[:5, :] = True

fore = berakna_gyf_fran_raster(markslag_fore, cellstorlek_m=1.0, profil="stockholm")
efter = berakna_gyf_fran_raster(
    markslag_efter,
    cellstorlek_m=1.0,
    profil="stockholm",
    yt_overlagring={"gront_tak": gront_tak_mask},
    tillagg=[GyfTillagg(tillagg_id="trad_krontackning", area_m2=20.0)],
)
jamf = jamfor_gyf(fore, efter)
payload = gyf_jamforelse_till_dict(jamf)
```
