# Matematik och öppna forskningsfrågor

Det här dokumentet samlar hur Flodens beräkningar hänger ihop matematiskt: formel, kod, kontrakt, facittest och
status per metod, ordnat per domän (vatten, dagsljus, luft, buller, ekologi). Därefter kommer de öppna frågorna
Q1–Q5 som agenter och utvecklare **inte** får gissa svaret på (`AGENTS.md`), och de observationer ur data som hör
till dem. Sidan "Matematik och forskning" i appen (`#/matematik`, kontomenyn) visar samma innehåll i kort form.

Dokumentet skrivs också som underlag till en tänkt oberoende forskningspart. Den parten kallas inte vid namn i
repot; ägarraden för en fråga säger "forskningspart" eller "öppen".

**Tre principer** (`AGENTS.md`): (1) *Fysiken är facit, AI är verktyg*: alla djup, soltimmar, halter och nivåer
kommer ur deterministiska beräkningar; språkmodeller via MCP styr anrop och skriver text med platshållare men äger
aldrig ett tal. (2) *Mät innan du påstår*: varje numerisk modul prövas mot analytiska facit, oberoende referensfall
och en reproducerbar testsvit (`docs/VALIDERING.md`). (3) *Spårbarhet och tydlig mognadsgrad*: varje körning skriver
ett manifest med sha256 för indata, kod och parametrar, och **ingen metod är klassad som `rapport`**; alla är `skiss`
tills en människa beslutar annat (`data/mcp/metodstatus.toml`, `docs/BESLUT.md`).

**Var en metod nås** (kontrollerat mot koden 2026-11-23): *i användarflödet* (webbflödet `#/p/<projekt>/...` via
K21-lösare), *i kartsidan* (`#/karta`), *bara i API:t* (endpoint utan knapp eller panel i klienten), *kommandorad,
MCP och testsvit* eller *fristående modul* (testad men inte inkopplad). Att något har ett API betyder alltså inte
att en planerare når det.

**Så hålls dokumentet i takt med koden.** Avsnitt 2–7 genereras ur `data/matematik/metoder.toml` (formler, sökvägar,
kontrakt, facittest, var metoden nås) och `data/mcp/metodstatus.toml` (skiss eller rapport) med
`python tools/generera_matematik.py`. `tests/gemensamt/test_metodregister.py` underkänner dokumentet om det glider isär,
om en sökväg inte finns eller om en metod pekar på en saknad metodstatus. Formler skrivs i LaTeX.

## 2. Metodtabell

<!-- METODTABELL:START (genereras av tools/generera_matematik.py) -->

| Domän | Metod | Kod | Kontrakt | Metodstatus | Var den nås |
| --- | --- | --- | --- | --- | --- |
| Vatten | Sänkgraf som sammanslagningsträd | `src/sankgraf/hierarki.py`<br>`src/sankgraf/persistens.py`<br>`src/sankgraf/fyllning.py`<br>`src/sankgraf/takvatten.py`<br>`src/sankgraf/avtappning.py` | K1 (v2), K2 (v2) | skiss | I användarflödet |
| Vatten | Händelsestyrd dynamik i grafen | `src/dynamik/handelsestyrd.py`<br>`src/dynamik/nodmodell.py`<br>`src/dynamik/enhetssvar.py`<br>`src/dynamik/tidsstegning.py` | K4, K6, K21 (lösaren vatten.dynamik) | skiss | I användarflödet |
| Vatten | 2D lokal tröghet, subgrid och hybrid | `src/tvad/lokal_troghet.py`<br>`src/tvad/hybrid.py`<br>`src/tvad/subgrid.py`<br>`src/tvad/felindikator.py`<br>`src/vatten/losare_2d.py`<br>`rust/tvad_karna` | K1, K5, K6, K36 (felindikator i API-svaret) | skiss | Kommandorad, MCP och testsvit |
| Vatten | Bifurkationsdetektor | `src/sankgraf/bifurkation.py`<br>`src/webb/api.py`<br>`webb/src/karta/overfall.ts` | K34, K35, K36 | skiss | I kartsidan (#/karta), inte i flödet |
| Vatten | Monoton intervallanalys för vatten | `src/dynamik/intervall.py`<br>`src/webb/api.py` | K40, K34 | skiss | Bara i API:t |
| Dagsljus | Horisont, soltimmar, SVF och VSC | `src/dagsljus/horisont.py`<br>`src/dagsljus/matt.py`<br>`src/dagsljus/solposition.py`<br>`src/dagsljus/floden_losare.py`<br>`src/dagsljus/ruta.py` | K1 (v2), K11, K21 (lösarna dagsljus.*) | skiss | I användarflödet |
| Dagsljus | Dozier–Frew: horisont i linjär tid | `src/dagsljus/intervall.py`<br>`src/webb/api.py` | K41 | skiss | Bara i API:t |
| Dagsljus | Monotona SVF-intervall | `src/dagsljus/intervall.py`<br>`src/webb/api.py` | K41 | skiss | Bara i API:t |
| Luft | Vindfält och spridning som M-matris | `src/luft/transport.py`<br>`src/luft/snabbvind.py`<br>`src/luft/lbm.py`<br>`src/luft/vindkomfort.py`<br>`src/karna/luft_losare.py` | K21 (lösarna luft.halt, luft.vindkomfort) | skiss | Kommandorad, MCP och testsvit |
| Luft | Adjungerad känslighet och åtgärdsval | `src/luft/kanslighet.py` | – | skiss | Fristående modul |
| Luft | Monotona MKN-intervall | `src/luft/kanslighet.py`<br>`src/webb/api.py` | K43 | skiss | Bara i API:t |
| Buller | Nord2000 referenskärna | `src/buller/nord2000/`<br>`src/buller/operator/`<br>`src/buller/lagen/`<br>`src/buller/scen/`<br>`src/buller/banor/` | KB1, KB2, KB3, KB4 (docs/buller/KONTRAKT.md) | skiss | Kommandorad, MCP och testsvit |
| Buller | CELF: submodulärt val av åtgärder | `src/buller/optimering.py`<br>`src/rutt/kritiska.py`<br>`src/luft/kanslighet.py`<br>`src/ekologi/intervall.py`<br>`src/webb/api.py` | K42, K8 | skiss | Bara i API:t |
| Ekologi | Kretsteoretisk konnektivitet | `src/ekologi/krets.py`<br>`src/ekologi/motstand.py`<br>`src/ekologi/habitat.py`<br>`src/karna/ekologi_losare.py` | K44, K45 | skiss | I användarflödet |
| Ekologi | PC, ECA och dPC | `src/ekologi/konnektivitet.py`<br>`src/ekologi/typer.py`<br>`src/webb/ekologi_analys.py` | K44, K45 | skiss | I användarflödet |
| Ekologi | Monotona osäkerhetsintervall | `src/ekologi/intervall.py`<br>`src/karna/ekologi_losare.py` | K45 | skiss | I användarflödet |

<!-- Per domän, genererat ur samma register -->

## 3. Vatten

### Sänkgraf som sammanslagningsträd (`vatten.sankgraf`)

Terrängens sänkor och deras överfall som ett rotat träd (Fill–Spill–Merge, Barnes 2020), med persistensfilter, slutsänkor och takvatten som linjär omfördelning.

$$ p(u) = z_{\text{öv}}(u) - z_{\min}(u), \qquad \text{filtrera bort } u \text{ när } p(u) < f\,\sigma(u),\; f = 2 $$

$$ T_{\text{tak}} : \mathbb{R}^{|\Omega_{\text{tak}}|} \to \mathbb{R}^{|\Omega_{\text{mark}}|}, \qquad \text{kolumnsumma } 1 $$

- **Kod:** `src/sankgraf/hierarki.py`, `src/sankgraf/persistens.py`, `src/sankgraf/fyllning.py`, `src/sankgraf/takvatten.py`, `src/sankgraf/avtappning.py`
- **Kontrakt:** K1 (v2), K2 (v2)
- **Facittest:** testsvit/A1_enkel_sanka (A0–A13, `floden testsvit`); tests/sankgraf/test_a_fall_statisk.py; tests/sankgraf/test_instangt_omrade.py; tests/sankgraf/test_takvatten.py
- **Status:** skiss (metodstatus); i användarflödet
- **Observera:** Persistensfiltret är relativt: en sänka tas bort när p(u) < f·σ(u) med f = 2 (inte en fast gräns). Takvattnets omfördelning har kolumnsumma 1 inklusive vatten som lämnar rutan; andelen till ledningsnät (standard 0) tas bort före fördelningen.

### Händelsestyrd dynamik i grafen (`vatten.dynamik`)

Volymen i varje sänka är styckvis linjär mellan händelser (bräddning, sammanslagning); lösaren hoppar mellan händelserna i stället för att stega i tid.

$$ \frac{\mathrm{d}V_u}{\mathrm{d}t} = r(t)\,A_{\text{eff}}(u) + \sum_{w \to u} Q_{w \to u}(t) - Q_{\text{avt}}(u) $$

$$ Q = C_d\,B\,\max(0, \Delta h)^{3/2} $$

- **Kod:** `src/dynamik/handelsestyrd.py`, `src/dynamik/nodmodell.py`, `src/dynamik/enhetssvar.py`, `src/dynamik/tidsstegning.py`
- **Kontrakt:** K4, K6, K21 (lösaren vatten.dynamik)
- **Facittest:** tests/dynamik/test_a_fall_dynamik.py; tests/dynamik/test_enhetssvar_och_ensemble.py; testsvit/A1_enkel_sanka (A1–A3, A9–A13)
- **Status:** skiss (metodstatus); i användarflödet
- **Observera:** Exakt bara i den kinematiska överfallsgränsen (omedelbart överfall). Bredskronsöverfall Q = C_d·B·max(0, Δh)^(3/2) finns som tidsstegning (`overfall = skibord`) och ger en fördröjning på +1,1 % till +2,1 % i A2 och A3 (mätt 2026-11-20, docs/MATEMATIK.md): öppen fråga Q1.

### 2D lokal tröghet, subgrid och hybrid (`vatten.tvad_hybrid`)

Lokal-tröghetsapproximationen (Bates 2010, de Almeida m.fl. 2012) på rutnät med θ-viktning, subgridtabeller och koppling till grafens djupa noder; en a posteriori-indikator pekar ut var 2D behövs.

$$ \Delta t \le \mathrm{CFL}\cdot\frac{\Delta x}{\sqrt{g\,h_{\max}} + |u|_{\max}}, \qquad \mathrm{CFL} = 0{,}5 $$

$$ \bar q_i = \theta\, q_i + \tfrac12 (1-\theta)(q_{i-1} + q_{i+1}), \qquad \theta = 0{,}9 $$

$$ \mathrm{Fr} = \frac{u}{\sqrt{g\,h}} \quad \text{(Manning-normalflöde vid överfallet; tröskel } 0{,}30\text{)} $$

- **Kod:** `src/tvad/lokal_troghet.py`, `src/tvad/hybrid.py`, `src/tvad/subgrid.py`, `src/tvad/felindikator.py`, `src/vatten/losare_2d.py`, `rust/tvad_karna`
- **Kontrakt:** K1, K5, K6, K36 (felindikator i API-svaret)
- **Facittest:** testsvit EA1, EA2, EA8A (`floden testsvit`); testsvit grupp D (Hunter, `floden testsvit --grupp D`); tests/tvad/test_hastighet_facit.py; tests/tvad/test_lokal_troghet_och_hybrid.py; tests/tvad/test_felindikator.py
- **Status:** skiss (metodstatus); kommandorad, mcp och testsvit
- **Observera:** Körs med `floden kor --modell 2d|hybrid` och i testsviten; ingår inte i webbflödets lösare. Hybridens uppsnabbning (12,9–20,0× vid subgrid = 4, volymfel < 2·10^-13) mättes 2026-11-20, före vattenkärnans rättelser 2026-11-23 (docs/BESLUT.md), och är inte omätt sedan dess. Hybridens fel under pågående överfall är öppen fråga Q3.

### Bifurkationsdetektor (`vatten.bifurkation`)

Flaggar överfallspunkter där höjdosäkerheten kan få vattnet att byta utlopp, med påverkad avrinningsyta och volym.

$$ \Delta z = \max(0,\, z_2 - z_1), \qquad \sigma = \sqrt{\sigma_1^2 + \sigma_2^2}, \qquad \text{instabil om } \frac{\Delta z}{\sigma} \le k $$

- **Kod:** `src/sankgraf/bifurkation.py`, `src/webb/api.py (POST /api/analys/bifurkationer)`, `webb/src/karta/overfall.ts`
- **Kontrakt:** K34, K35, K36
- **Facittest:** tests/sankgraf/test_bifurkation.py; tests/webb/test_bifurkation_projekt.py; tests/tvad/test_felindikator.py
- **Status:** skiss (metodstatus); i kartsidan (#/karta), inte i flödet
- **Observera:** Nås som lager i kartsidan (#/karta), inte i flödet. Gränsen är m ≤ k (inte strikt <), standard k = 2. Detta är en heuristik, ingen persistensteori: se öppen fråga Q2.

### Monoton intervallanalys för vatten (`vatten.intervall`)

Under fast flödestopologi är maxdjupet monotont i regn, initial förlust och avtappning; två extremkörningar ger garanterade gränser, med grenunion kring instabila bifurkationer.

$$ h_{\min}(t) \le h_u(t) \le h_{\max}(t), \qquad (h_{\min}, h_{\max}) \text{ ur två extremkörningar} $$

- **Kod:** `src/dynamik/intervall.py`, `src/webb/api.py (POST /api/analys/intervall)`
- **Kontrakt:** K40, K34
- **Facittest:** tests/dynamik/test_intervall.py (50 slumpade scenarier på vart och ett av A1–A4, totalt 200, alla inneslutna); tests/webb/test_analys_steg_a.py
- **Status:** skiss (metodstatus); bara i api:t
- **Observera:** Garantin gäller delträd utan bifurkation; berörda noder märks `monoton_garanterad = False`. Ingen panel i klienten (K40: läses av inget i webbklienten).

## 4. Dagsljus

### Horisont, soltimmar, SVF och VSC (`dagsljus.horisont_svf_vsc`)

Horisontvinkel per azimut över ett prismafält (DSM), soltimmar ur solbanan, himmelsandel (SVF) och vertikal himmelskomponent (VSC, mulen CIE-himmel, BRE-normering).

$$ h(\varphi) = \max\Bigl(0,\; \max_{c \in \text{stråle}(\varphi)} \arctan\frac{z_c - z_0}{s_c}\Bigr) $$

$$ \mathrm{SVF} = 1 - \frac{1}{K}\sum_{k=1}^{K} \sin^2 h_k $$

$$ \mathrm{VSC} = \frac{1}{N}\int_{-\pi/2}^{\pi/2} I\bigl(h(\varphi_n + \psi)\bigr)\cos\psi\,\mathrm{d}\psi, \quad N = \tfrac{7\pi}{9}, \quad \mathrm{VSC}_{\text{oskymd}} = 0{,}3962 $$

- **Kod:** `src/dagsljus/horisont.py`, `src/dagsljus/matt.py`, `src/dagsljus/solposition.py`, `src/dagsljus/floden_losare.py`, `src/dagsljus/ruta.py`
- **Kontrakt:** K1 (v2), K11, K21 (lösarna dagsljus.*)
- **Facittest:** testsvit grupp E, E1–E7 (`floden testsvit --grupp E`); tests/dagsljus/test_horisont.py; tests/dagsljus/test_matt.py; tests/dagsljus/test_losare.py
- **Status:** skiss (metodstatus); i användarflödet
- **Observera:** Fri plan ger VSC 39,6 % och SVF 1 (E1). Horisonten räknas med strålgenomgång över pelarceller (referens, riktningsruta och max-pyramid), inte med Dozier–Frew.

### Dozier–Frew: horisont i linjär tid (`dagsljus.dozier_frew`)

Exakt 1D-horisont längs en stråle med övre konvext hölje (monoton stack), O(N) oberoende av sökradien.

$$ h_i = \max_{j > i} \arctan\frac{z_j - z_i}{d_{ij}} \quad \text{i } O(N) \text{ i stället för } O(N^2) $$

- **Kod:** `src/dagsljus/intervall.py (horisont_1d_konvext_holje)`, `src/webb/api.py (POST /api/analys/svf-intervall)`
- **Kontrakt:** K41
- **Facittest:** tests/dagsljus/test_modern_matte.py (maxavvikelse < 1e-14 mot O(N²))
- **Status:** skiss (metodstatus); bara i api:t
- **Observera:** Används bara för den 1D-horisontprofil som svf-intervall returnerar; huvudberäkningen av horisonten (ovan) använder den inte.

### Monotona SVF-intervall (`dagsljus.svf_intervall`)

SVF är antiton i byggnadshöjd: körningar med z − σ och z + σ ger garanterat [SVF_min, SVF_max] per cell.

$$ \mathrm{SVF}(H - \sigma) \ge \mathrm{SVF}(H) \ge \mathrm{SVF}(H + \sigma) $$

- **Kod:** `src/dagsljus/intervall.py (kor_monoton_svf_intervall)`, `src/webb/api.py (POST /api/analys/svf-intervall)`
- **Kontrakt:** K41
- **Facittest:** tests/dagsljus/test_modern_matte.py (20 slumpade höjdscenarier inneslutna); tests/webb/test_analys_steg_a.py
- **Status:** skiss (metodstatus); bara i api:t
- **Observera:** Ingen panel i klienten.

## 5. Luft

### Vindfält och spridning som M-matris (`luft.transport`)

Stationär advektion–diffusion–deposition på voxelnät som ett glest linjärt system; snabbvind ger ett divergensfritt skissfält med Poisson-projektion.

$$ \nabla\!\cdot(\mathbf{u}\,c) - \nabla\!\cdot(K\nabla c) + \lambda c = s \;\;\Longrightarrow\;\; L\,c = s, \qquad L^{-1} \ge 0 $$

- **Kod:** `src/luft/transport.py`, `src/luft/snabbvind.py`, `src/luft/lbm.py`, `src/luft/vindkomfort.py`, `src/karna/luft_losare.py`
- **Kontrakt:** K21 (lösarna luft.halt, luft.vindkomfort)
- **Facittest:** tests/luft/test_transport.py; tests/luft/test_transport_tvd.py; tests/luft/test_lbm_validering.py; docs/LUFT.md avsnitt 6 (L1–L8)
- **Status:** skiss (metodstatus); kommandorad, mcp och testsvit
- **Observera:** M-matrisen (alla element utanför diagonalen ≤ 0, kolumnvis diagonaldominant) gäller för uppströmsschemat och för TVD med `m_matris_tak = true` (standard). Lösarna finns i K21 men luft är inte ett ämne med beräkning i webbflödet. Licens MIT (src/luft/).

### Adjungerad känslighet och åtgärdsval (`luft.adjungerad`)

En adjungerad lösning ger känsligheten hos mottagarhalten för varje källa och för vegetationen, i stället för en körning per cell.

$$ J(c) = w^{\top} c, \quad L^{\top}\lambda = w \;\;\Longrightarrow\;\; \frac{\partial J}{\partial s_i} = \lambda_i, \quad \frac{\partial J}{\partial \mathrm{LAD}_i} = -v_{\text{veg}}\,\Delta x^3\,\lambda_i\,c_i $$

- **Kod:** `src/luft/kanslighet.py (berakna_adjungerad_kanslighet, valj_basta_luftatgarder_k)`
- **Kontrakt:** inget eget
- **Facittest:** tests/luft/test_kanslighet.py (dualitetsfel < 1e-12, vegetationsgradient mot finit differens, relativt 1e-3)
- **Status:** skiss (metodstatus); fristående modul
- **Observera:** Inte inkopplad i något flöde eller API (git grep 2026-11-23): bara kor_monoton_mkn_intervall i samma fil används av API:t. Beskrivs som 'redo för 3D-luftflödet' i modulens docstring.

### Monotona MKN-intervall (`luft.mkn_intervall`)

Halten är monoton i emission och antiton i deposition; två extremserier av en färdig timserie ger ett garanterat intervall för dygn över 50 µg/m³ (MKN: högst 35 dygn per år).

$$ c_{\text{lo}} = c_{\text{bg}} + \frac{e_{\text{lo}}}{d_{\text{hi}}}\,c_{\text{lokal}}, \qquad c_{\text{hi}} = c_{\text{bg}} + \frac{e_{\text{hi}}}{d_{\text{lo}}}\,c_{\text{lokal}} $$

- **Kod:** `src/luft/kanslighet.py (kor_monoton_mkn_intervall)`, `src/webb/api.py (POST /api/analys/luft-mkn)`
- **Kontrakt:** K43
- **Facittest:** tests/luft/test_kanslighet.py (50 MKN-scenarier inneslutna); tests/webb/test_analys_steg_b.py
- **Status:** skiss (metodstatus); bara i api:t
- **Observera:** Skalar en given lokal timserie med emissionsfaktor/depositionsfaktor; det är ingen ny transportlösning. Ingen panel i klienten.

## 6. Buller

### Nord2000 referenskärna (`buller.nord2000`)

Punkt-till-punkt-överföring enligt Nord2000 (delmodell 1–9: plan mark, terräng, skärmar, refraktion) med operatorlager och klustring med felgräns.

$$ L[m,b] = 10\lg \sum_{v} w_v \sum_{\text{banor}} 10^{\,(L_W[\text{källa},b] + H[\text{bana},v,b])/10} $$

- **Kod:** `src/buller/nord2000/`, `src/buller/operator/`, `src/buller/lagen/`, `src/buller/scen/`, `src/buller/banor/`
- **Kontrakt:** KB1, KB2, KB3, KB4 (docs/buller/KONTRAKT.md)
- **Facittest:** tests/buller/nord2000/; tests/buller/test_helkedja.py; testsvit grupp N (RL 18-18, 80 fall, kräver lokal källmapp)
- **Status:** skiss (metodstatus); kommandorad, mcp och testsvit
- **Observera:** Pågår: `nord2000_konform = False`, rapportläget spärrat. Grupp N 2026-11-21 (docs/buller/logg/P-S2.md): 33 av 80 godkända, 16 ska utredas, 4 underkända, 27 ej körbara. Ej i webbflödet. Newton–Raphson används för krökta strålar (stralar_krokta.py), inte för skärmdiffraktionen.

### CELF: submodulärt val av åtgärder (`buller.celf`)

Girigt val av de k mest effektiva åtgärderna (bullerskärmar, kritiska gatuavsnitt, luft- och ekologiåtgärder) med lat utvärdering och garantin (1 − 1/e) mot optimum; skuggzonsdetektor för mottagare där Nord2000-dämpningen hoppar.

$$ f(S) = \sum_i w_i \min\bigl(\max(0, L_i - L_{\text{rikt}}),\, \max_{s \in S}\Delta L_{i,s}\bigr), \qquad f(S_{\text{CELF}}) \ge \bigl(1 - \tfrac1e\bigr) f(S^*), \; |S| \le k $$

$$ g_{\text{krit}} = -\,c_0\,\frac{\bigl(\sqrt{2h_s} + \sqrt{2h_r}\bigr)^2}{d^2} $$

- **Kod:** `src/buller/optimering.py`, `src/rutt/kritiska.py (valj_kritisk_insats_k)`, `src/luft/kanslighet.py (valj_basta_luftatgarder_k)`, `src/ekologi/intervall.py (valj_basta_ekologi_atgarder_k)`, `src/webb/api.py (POST /api/analys/buller-skarmar, POST /api/rutt)`
- **Kontrakt:** K42, K8
- **Facittest:** tests/buller/test_optimering.py (matchar brute force, k = 3); tests/rutt/test_kritiska.py (brute force; 400 noder och 760 kanter under 0,05 s); tests/ekologi/test_facit_g.py (G6: CELF väljer ekodukt först); tests/luft/test_kanslighet.py
- **Status:** skiss (metodstatus); bara i api:t
- **Observera:** Bara buller- och ruttvarianten nås via API (buller-skarmar, rutt med antal_insatser); luft- och ekologivarianten är fristående funktioner. Skuggzonsdetektorn är en geometrisk tumregel, inte en del av Nord2000-kärnan. 1,25 ms för 400 noder och 760 kanter vid k = 5 är en enstaka mätning ur STATUS 2026-11-22; testet kräver bara under 50 ms.

## 7. Ekologi

### Kretsteoretisk konnektivitet (`ekologi.krets`)

Landskapet som elektriskt nät: harmoniska kantkonduktanser, Dirichlet-låsning av käll- och målfläck, effektivt motstånd, strömtäthet (flaskhalsar) och exakt känslighet med en lösning.

$$ G_{uv} = \frac{2\,g_u g_v}{(g_u + g_v)\,\ell_{uv}}, \qquad g = 1/R, \qquad R_{\text{eff}} = 1/I_{\text{tot}} $$

$$ \frac{\partial R_{\text{eff}}(P_i, P_j)}{\partial G_{uv}} = -\left(\frac{v_u - v_v}{I_{\text{tot}}}\right)^{2} \le 0 $$

- **Kod:** `src/ekologi/krets.py`, `src/ekologi/motstand.py`, `src/ekologi/habitat.py`, `src/karna/ekologi_losare.py`
- **Kontrakt:** K44, K45
- **Facittest:** tests/ekologi/test_facit_g.py (G1–G3, G5); tests/ekologi/test_losare_k21.py (G7)
- **Status:** skiss (metodstatus); i användarflödet
- **Observera:** Skiss: artprofilernas parametrar saknar exakta källhänvisningar och markslagsöversättningen är egna val (docs/EKOLOGI.md).

### PC, ECA och dPC (`ekologi.pc_eca`)

Kostnadsavstånd med multi-source Dijkstra, spridningssannolikhet mellan fläckar, Probability of Connectivity, Equivalent Connected Area och Saura–Rubio-dekomposition (intra, flux, connector).

$$ p^*_{ij} = e^{-\alpha d^*_{ij}},\; \alpha = -\ln p_0 / d_{\text{disp}}; \qquad \mathrm{PC} = \frac{\sum_{i,j} a_i a_j\, p^*_{ij}}{A_L^{2}}, \qquad \mathrm{ECA} = A_L\sqrt{\mathrm{PC}} $$

- **Kod:** `src/ekologi/konnektivitet.py`, `src/ekologi/typer.py`, `src/webb/ekologi_analys.py (POST /api/analys/ekologi)`
- **Kontrakt:** K44, K45
- **Facittest:** tests/ekologi/test_facit_g.py (G4: exakt PC, ECA och dPC för tre fläckar); tests/ekologi/test_losare_k21.py (G7)
- **Status:** skiss (metodstatus); i användarflödet
- **Observera:** Före/efter i webbflödet (fliken Ekologi i Resultat). Sannolikhetsfunktionen är exponentiell med p0 = 0,5 vid spridningsavståndet.

### Monotona osäkerhetsintervall (`ekologi.intervall`)

ECA och PC är antitona i motståndet och monotona i spridningsavståndet; två extremkörningar ger ett garanterat intervall över litteraturosäkerheten.

$$ \mathrm{ECA}(R_{\max}, d_{\min}) \le \mathrm{ECA} \le \mathrm{ECA}(R_{\min}, d_{\max}) $$

- **Kod:** `src/ekologi/intervall.py (kor_monoton_ekologi_intervall)`, `src/karna/ekologi_losare.py`
- **Kontrakt:** K45
- **Facittest:** tests/ekologi/test_facit_g.py (G6: 50 slumpade scenarier, alla inneslutna); tests/ekologi/test_losare_k21.py (G7: intervallen innesluter nominellt värde)
- **Status:** skiss (metodstatus); i användarflödet
- **Observera:** Intervallens faktorer är egna val utan källa (STATUS 2026-11-23). Motståndet skalas med en gemensam faktor, inte cell för cell.


<!-- METODTABELL:SLUT -->

## 8. Uppmätt nuläge

Uppmätt 2026-11-23 på `main` (`aa6073d`) i en delad molncontainer; tal med annat datum har egen källa.

| Kontroll | Resultat | Källa |
| --- | --- | --- |
| Hela pytest-sviten (`FLODEN_TIDSFAKTOR=4 python -m pytest`, Python och Rust-jämförelser där Rust-kärnan är byggd) | **2 409 godkända, 50 hoppade över**, 0 fel (18 min 36 s) | körd 2026-11-23, före de 10 testerna i `tests/gemensamt/test_metodregister.py` som tillkom i den här ändringen |
| Vattentestsvit (`floden testsvit`: A0–A13 och EA1, EA2, EA8A) | **17/17 godkända**, volymfel 0,0000 % | körd 2026-11-23; `docs/VALIDERING.md` |
| Dagsljus (`floden testsvit --grupp E`, E1–E7) | **7/7 godkända** | körd 2026-11-23; `docs/DAGSLJUS.md` |
| Hastighet och CFL i 2D (`tests/tvad/test_hastighet_facit.py`) | 8/8 godkända, avvikelse −0,3 % till −4,0 % mot Manning (toleranser 3 % och 6 %) | `docs/VALIDERING.md` F, 2026-11-23 |
| Ekologi (`tests/ekologi`, G1–G7) | 24 godkända, 1 hoppat över; G1–G4 `< 1e-12`, adjungerad derivata mot differenskvot `< 1e-6`, 50 av 50 scenarier inneslutna | körd 2026-11-23 |
| Typning och kodkvalitet | `mypy src`: 0 fel (333 källfiler); `ruff` 0.8.4: 0 fel | körd 2026-11-23 |
| Webbklienten | `npm test` 177 godkända; typkontroll och build rena | körd 2026-11-23 |

Hunter D1 (RMSE 0,0071 m vid dx 5 m, 1 800 s) och EA-fallen står i `docs/VALIDERING.md`. Räkna inte med att gamla
siffror ("2 158 tester", "319 källfiler") gäller: de är från tidigare datum.

**Hastighet i webbläsaren** (`docs/benchmark/2026-11-23-mac-webgpu.json`, Joels Mac med Apple Metal 3 och Chrome,
körd mot floden.berring.se; median av 5 körningar inklusive dataöverföring; serverns tid mättes samma gång).
Klientens f32-tal är skiss och facit är serverns f64.

| Fall | WebGPU (f32) | Server (f64) | Faktor | Största avvikelse |
| --- | ---: | ---: | ---: | --- |
| Dagsljus 300 × 300 (90 000 celler, 64 riktningar), soltimmar | 53,9 ms | 4 243 ms | ≈ 79 | 9,5e-7 h (gräns 0,25 h) |
| Samma fall, SVF | 53,9 ms | 4 243 ms | ≈ 79 | **0,0126, över toleransen 0,01** i 13 av 90 000 celler (nära fasader); medelfel 5,7e-6 |
| Vatten 2D, 64 × 64, 1 000 steg | 9,9 ms | 244 ms | ≈ 25 | 9,2e-7 m (gräns 0,0005 m) |

SVF-fältet klarade alltså inte sin tolerans i den körningen. Serverns baslinje (2 vCPU, delad container, `docs/benchmark/
2026-11-23-container.json`) är ungefärlig, med flera tiotals procents variation mellan körningar. Hybridens
uppsnabbning mot full 2D i testsviten är 1,82 × (EA2) och 1,21 × (EA8A) efter rättelserna 2026-11-23; de äldre
12,9–20,0 × (2026-11-20) är inte omätta sedan dess.

## 9. Öppna frågor Q1–Q5

Varje fråga har: själva frågan, varför den spelar roll, vad som redan är gjort och vilket underlag som finns. Tal ur
data har datum och källa; de som mättes före vattenkärnans rättelser 2026-11-23 (`docs/BESLUT.md`) är märkta så.

### Q1 – Exakt händelsestyrd fyllning, min-plus-algebra och avtappning

**Frågan.**
1. Under vilka antaganden kan tidpunkten då varje sänka svämmar över räknas exakt, och med vilken komplexitet, om
   fyllningen formuleras i min-plus-semiringen (*Network Calculus*: sänka = buffert, flödesväg = fördröjning,
   överfall = kapacitetsbegränsning)? Vilken är värsta-falls-komplexiteten?
2. Går det att ge en analytisk övre och undre gräns för tid eller volym mellan den händelsestyrda modellen och
   bredskronsöverfall $Q = C_d B \max(0,\Delta h)^{3/2}$ (*multirate*-växling på enstaka olinjära noder)?
3. Avtappning: i 2D tappas bara våta celler, medan grafen tappar nodens hela avrinningsyta. Om grafen i stället följer
   $\mathrm{d}V/\mathrm{d}t = r\,A_{\text{avr}} - k\,A(h(V))$ blir ekvationen olinjär i $V$ mellan händelser. Finns en
   exakt eller garanterat konservativ händelsestyrd formulering? Finns en meningsfull statisk motsvarighet till
   avtappning (utan tid går det inte att veta hur mycket kapacitet som hinner användas), eller bör statisk fyllning med
   avtappning ersättas av dynamiken?

**Varför den spelar roll.** Exaktheten är det som gör att tusentals regn kan räknas på sekunder utan tidsstegning, och
avtappningen avgör om graf och 2D ger samma svar för slutna kvarter.

**Redan gjort.** Vid konstant regn är volymen styckvis linjär och vid linjärt regn styckvis kvadratisk mellan
händelser; på A1–A3 stämmer lösaren med grafens analytiska tider till `< 1e-12` relativt fel och ger `0,00 m`
avvikelse mot `statisk_fyllning` på A9 (282 noder). Antalet händelser växer högst som $O(|V|+|I|)$ (A3: 3 hopp över
13 000 s). Med bredskronsöverfall ($B = 2{,}0$ m, $C_d = 1{,}7$) fördröjs nedströms överfall i A2 med +51,8 s (+1,1 %)
och syskonsammanslagningen i A3 med +66,8 s (+2,1 %), överhöjd 1,0–1,7 cm vid 72 mm/h; lösningen närmar sig den
händelsestyrda gränsen monotont när $B \to \infty$. Enhetssvaret före första överfall stämmer med tidsstegning till
`1,26e-14` relativt fel (2,7 × snabbare för 20 regnserier, A3). Avtappning: på plant golv ger graf och 2D samma förlopp
(A11); på sluttande golv överskattar grafen avtappningen när gården bara delvis är våt. I statisk fyllning tappas bara
lövens direkta tillrinning (en undre gräns). Mätt 2026-11-20 och 2026-11-22 (observationerna nedan).

**Underlag.** `src/dynamik/handelsestyrd.py`, `src/dynamik/nodmodell.py` (`overfall = skibord`),
`tests/dynamik/test_a_fall_dynamik.py`, testsvitens A1–A3 och A9–A13, `docs/VATTEN.md`, observationerna i avsnitt 10.
**Ägare:** öppen (forskningspart tillfrågas).

### Q2 – Flerparameterpersistens under höjdosäkerhet (föreslaget huvudspår)

**Frågan.** Sänkgrafen filtreras i praktiken i två parametrar samtidigt: höjd $h$ och osäkerhet $\sigma$ (eller höjd
och regnvolym $V$).
1. Kan sänksystemets robusthet formaliseras med tvåparameterpersistensmoduler $H_0(G_{h,\sigma};k)$ över $k[x,y]$, med
   minimala fria upplösningar, Hilbertfunktioner och graderade Betti-tal $(\beta_0,\beta_1,\beta_2)$?
2. Kan persistensförenklingen av en höjdmodell ges en **bevisbar felgräns**, till exempel i interleaving-avstånd
   översatt till största vattendjupsfel?
3. Hur påverkas fyllnadstider och avrinningsytor av osäkra vattendelare på platta ytor och vallar?

**Varför den spelar roll.** Dagens detektor är en heuristik utan teori bakom: den pekar ut vilka sadelpunkter som bör
mätas in i fält, men säger inget om hur stort felet blir om de inte mäts.

**Redan gjort.** Bifurkationsdetektorn (`src/sankgraf/bifurkation.py`, K34–K36) jämför varje sänkas primära utloppssadel
med den lägsta konkurrerande sadeln mot en annan mottagare och flaggar punkten när $\Delta z/\sqrt{\sigma_1^2+\sigma_2^2}
\le k$ (standard $k=2$). Persistensfiltret är relativt: $p(u) < 2\sigma(u)$. Vattenintervallen (K40) använder
bifurkationerna för grenunion och har 200 av 200 slumpade scenarier (50 per fall A1–A4) inneslutna. I A2 och A3 skiftar
2 celler breda flata vallar avrinningsytan 1–8 % mot en naiv handformel (observation 2026-11-20).

**Underlag.** `tools/exportera_q2_underlag.py` exporterar ett GIS-fritt dataset (`bifiltrering_q2.json`, K35 i
`docs/DATAKONTRAKT.md`, `floden-bifiltrering/v1`) med noder (bottenhöjd, överfallshöjd, persistens, osäkerhet,
avrinningsyta, hierarki) och samtliga fyrgrannssadelkanter (höjd, osäkerhet, läge), samt `bifurkationer.json` (K34) och
`bifurkationer.gpkg`. Verifierat 2026-11-23 på `testsvit/A9_brus_persistens/indata`: 282 noder och 744 sadelkanter; alla
282 överfall flaggas instabila, eftersom A9 är en brusterräng med standardosäkerheten 0,05 m, så exemplet visar formatet
och inte en verklig terräng. Kom igång:

```bash
git clone https://github.com/joelberring/floden.git && cd floden
export PYTHONPATH=$PWD/src
python tools/exportera_q2_underlag.py --indata testsvit/A9_brus_persistens/indata --ut utdata/q2_exempel
```

Filerna läses utan GIS-bibliotek i Python, Julia eller Macaulay2. Python-miljö: `pip install -e ".[dev]"`.
**Ägare:** forskningspart (föreslagen huvuddel).

### Q3 – Fel i hybriden

**Frågan.** Hur stort är felet när djupa samlingar räknas som noder med plan vattenyta och resten i 2D, jämfört med
full 2D? Kan en skarp *a posteriori*-felgräns ges, särskilt under pågående kraftigt överfall över en smal tröskel, och
kan nod–2D-kopplingen kalibreras (effektiv utloppslängd, lokal delstegning) så att överfallsfelet halveras vid grov
subgrid?

**Varför den spelar roll.** Hybriden är det som gör 2D användbart i skisskedet (CFL-frikoppling), men utan felgräns vet
man inte när man ska lita på den.

**Redan gjort.** Felindikatorn (`src/tvad/felindikator.py`, K36) väljer 2D-zoner ur lutning, Froudetal vid överfall och
genomströmningskvot. Före överfall: MAE 0,01–0,19 cm mot full 2D, IoU 1,0000; under pågående överfall (A1, 720 mm/h)
+1,97 cm (`subgrid=1`) och +3,60 cm (`subgrid=4`) extra överhöjd i noden, uppsnabbning 12,9–20,0 × vid `subgrid=4`
(alla tal mätta 2026-11-20, före vattenkärnans rättelser 2026-11-23, inte omätta). Efter rättelserna (2026-11-23,
`docs/VALIDERING.md`): EA2 MAE 0,0239 m, IoU 0,850; EA8A MAE 0,0200 m, IoU 0,930.

**Underlag.** `src/tvad/hybrid.py`, `tests/tvad/test_lokal_troghet_och_hybrid.py`, testsvitens EA2 och EA8A, grupp D
(Hunter), observation T05 nedan. **Ägare:** öppen (forskningspart).

### Q4 – Inkrementell omräkning

**Frågan.** Vilka datastrukturer gör att en ändring (en ny byggnad) bara kräver omräkning av ett delträd och det som
ligger nedströms? Konkret: dynamiska sammanslagningsträd (*link-cut*, *top trees*, Euler-tour-träd över sadelkanter)
tillsammans med ett Schur-komplement-tänk per 500 × 500 m-ruta, där varje ruta bara exponerar sina randportar, för
helstadsskalning med lokal omräkning av projektlappar på sekunder. Finns en lokal regel för när ett nytt hus stänger
en port eller skapar en slutsänka utan global omräkning, och kan dynamiskt MST spara omräkning när två komponenter bara
möts vid en hög gemensam sadel?

**Redan gjort.** En oberörd komponent $T$ är oförändrad om (i) ingen flödesriktning lämnar det omräknade området in i
$T$ och (ii) varje gränssadel mot $T$ strikt överstiger `rot(T).overfall_rh2000`; även uppströms noder kan påverkas när
ett överfall blir en syskonsammanslagning. Likahöjdsplatåer mot frusna havsceller kräver fixpunktsexpansion (annars
upp till ~8 cm djupfel; med den `0,00 m`). På 256 × 256 celler med 16 kvarter återanvändes 23 av 24 noder (94,5 %
frusna celler) med 9,28 × tidsvinst (26,7 ms mot 247,4 ms), mätt 2026-11-20. Slutsänkor ger i dag full omräkning.

**Underlag.** `src/sankgraf/hierarki.py`, `src/lapp/`, `tests/sankgraf/test_instangt_omrade.py`, observationerna T04 och
2026-11-22. **Ägare:** öppen.

### Q5 – Fysikbevarande modellreduktion för 2D i realtid

**Frågan.** Kan en masskonserverande reducerad modell (POD eller *Reduced Basis*) för 2D-delzoner, deterministisk och
med känd *a posteriori*-felgräns, ge realtidssvar för kritiska gator utan svart-låda-AI för djup och flöden
(`AGENTS.md` regel 1)?

**Redan gjort.** Ingen reducerad modell är byggd. Utgångspunkter: facit för hastighet och CFL (`tests/tvad/
test_hastighet_facit.py`), en Rust-kärna som är bitidentisk med Python i fallet, och en WebGPU-motor (f32) som körde
64 × 64 celler och 1 000 steg på 9,9 ms mot 0,244 s på servern, med största djupfel 9,2e-7 m (Joels Mac, Chrome,
2026-11-23, `docs/benchmark/2026-11-23-mac-webgpu.json`; serverns tid från samma körning). Det är en snabbare
fullmodell, inte en reducerad modell.

**Underlag.** `src/tvad/lokal_troghet.py`, `rust/tvad_karna`, `docs/benchmark/`, `docs/WEBGPU-ARKITEKTUR.md`.
**Ägare:** öppen (forskningsspår M7 i `PLAN.md`).

## Hur ett samarbete kring verifiering kan läggas upp

1. **Oberoende matematisk granskning** av en avgränsad del (sänkgrafens persistens och bifurkationer, eller den
   händelsestyrda dynamiken): var håller teorin och var brister den?
2. **Ett avgränsat forsknings- eller examensarbetsspår på Q2 (eller Q1)** med öppen kod och öppen data som grund för
   en gemensam metodrapport eller artikel.
3. **Formella kriterier för steget `skiss` → `rapport`**: vilka matematiska krav och valideringstester som ska gälla
   innan en metod får klassas som granskad (beslutet är projektägarens, `data/mcp/metodstatus.toml`).

## 10. Observationer från data

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
- **Fel före vs under kraftigt överfall:** Före överfall ger hybriden `0,01–0,19 cm` MAE mot full 2D och `IoU = 1,0000`. Under pågående kraftigt överfall över smal tröskel (A1, 720 mm/h) uppstår `+1,97 cm` (`subgrid=1`) resp. `+3,60 cm` (`subgrid=4`) extra överhöjd i noden jämfört med full 2D. Fråga till Q3 (forskningspart): kan nod–2D-kopplingen kalibreras med en effektiv utloppslängd eller lokal delstegning för att halvera överfallsfelet vid grov subgrid?
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
  golv är det samma sak (A11: graf och 2D ger 15,0 mm topp och tomt efter 30 min). **Fråga (Q1, forskningspart):** på
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
- **Avtappning i statisk fyllning (fråga till forskningsparten, Q1):** utan tid går det inte att veta hur mycket av en nods
  avtappningskapacitet som hinner användas på vatten som kommer som överfall från andra noder. Vi tappar bara lövens
  direkta tillrinning (högst kapacitet × varaktighet), vilket är en undre gräns för avtappningen. Finns en exakt
  statisk motsvarighet (t.ex. ordna överfallen efter den händelsestyrda lösningens tider), eller ska statisk
  fyllning med avtappning alltid ersättas av dynamiken?

