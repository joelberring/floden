# Termiskt mikroklimat och värmestress: $T_{\text{mrt}}$ och UTCI (skiss)

Kod: `src/mikroklimat/` (`utci.py`, `tmrt.py`, `fysik.py`). Tester: `tests/mikroklimat/test_utci_tmrt.py`.
Licens: GPL v3. Fristående domänpaket som endast importerar stdlib, NumPy och `gemensamt` (noll
korsdomänimporter).

**Metodstatus: `skiss`.** Kärnformlerna för UTCI (Bröde m.fl. 2012), klarhimmelsemissivitet (Prata 1996)
och 6-riktnings $T_{\text{mrt}}$ (ISO 7726 / Lindberg m.fl. 2008) är deterministiska och testade mot
analytiska och publicerade referensfall, men yttemperaturmodellen för mark/fasad och den isotropa
2D-vägggeometrin är förenklade jämfört med fullständig 3D-SOLWEIG med dynamisk värmelagring och är
ännu inte fältvaliderade mot mätdata i svenska städer.

---

## 1. Enheter och konventioner

Alla variabler och returvärden bär enheten i namnet (`_c`, `_m`, `_m_s`, `_pct`, `_hpa`, `_kpa`,
`_w_m2`, `_rad`):

- Temperaturer i grader Celsius (`_c`), intern omvandling till Kelvin via $T_K = T + 273{,}15$.
- Vinklar i radianer (`solhojd_rad`).
- Strålningsflöden i $\text{W/m}^2$.
- Vindhastighet i $\text{m/s}$ på 10 m referenshöjd (`vind_10m_m_s`) för UTCI; fotgängarhöjd skalas
  explicit med `vind_fotgangarhojd_till_10m`.
- `NaN` representerar saknad data (t.ex. byggnadsfotavtryck i 2D-raster) och bevaras exakt.
- Inga tysta fel: värden utanför Brödes giltighetsområde flaggas alltid i
  `utanfor_giltighetsomrade` med tydliga meddelanden i `varningar` och klipps aldrig tyst.

---

## 2. Universal Thermal Climate Index — UTCI (`utci.py`)

### 2.1 Officiell 6:e-ordningens polynomapproximation

Implementerar de **210 officiella koefficienterna** ur Bröde m.fl. (2012) och referenskoden
`UTCI_a002.f90` (Version a 0.002, Peter Bröde / COST Action 730):

$$\text{UTCI}(T_a, T_{\text{mrt}}, v_{10}, p_a) = T_a + \sum_{i+j+k+l \le 6} c_{ijkl}\, T_a^i\, v_{10}^j\, (T_{\text{mrt}} - T_a)^k\, p_a^l$$

där:

- $T_a$ är lufttemperatur ($^\circ\text{C}$),
- $v_{10}$ är vindhastighet på 10 m höjd ($\text{m/s}$),
- $\Delta T_{\text{mrt}} = T_{\text{mrt}} - T_a$ ($^\circ\text{C}$),
- $p_a = e_a / 10$ är vattenångans partialtryck i **kPa** (där $e_a$ är i hPa).

### 2.2 Mättat vattenångtryck (Hardy 1998 ITS-90)

Exakt enligt funktionen `es(ta)` i `UTCI_a002.f90` (Hardy 1998, ITS-90 över vatten):

$$\ln e_s[\text{Pa}] = \sum_{i=0}^{6} g_i T_K^{i-2} + g_7 \ln T_K, \qquad e_s[\text{hPa}] = 0{,}01 \exp(\ln e_s[\text{Pa}])$$

med $T_K = T_a + 273{,}15$ och:

| Term | Koefficient |
| --- | ---: |
| $g_0$ | $-2836{,}5744$ |
| $g_1$ | $-6028{,}076559$ |
| $g_2$ | $19{,}54263612$ |
| $g_3$ | $-0{,}02737830188$ |
| $g_4$ | $1{,}6261698 \times 10^{-5}$ |
| $g_5$ | $7{,}0229056 \times 10^{-10}$ |
| $g_6$ | $-1{,}8680009 \times 10^{-13}$ |
| $g_7$ | $2{,}7150305$ |

Partialtrycket i kPa erhålls som $p_a = e_s(T_a) \cdot (\text{RH}/100) / 10$.

### 2.3 Logaritmisk vindprofil

Omvandling från fotgängarhöjd $z$ (standard $z = 1{,}5\ \text{m}$; Bröde 2012 använder även $1{,}1\ \text{m}$)
till 10 m referenshöjd över öppen mark ($z_0 = 0{,}01\ \text{m}$, kort gräs):

$$v_{10} = v_z \cdot \frac{\ln(10 / z_0)}{\ln(z / z_0)}$$

Vid $z = 1{,}5\ \text{m}, z_0 = 0{,}01\ \text{m}$ är skalfaktorn $\ln(1000)/\ln(150) \approx 1{,}3786$;
vid $z = 1{,}1\ \text{m}$ är den $\ln(1000)/\ln(110) \approx 1{,}4696$.

### 2.4 Giltighetsområde och hantering av avvikelser

Bröde m.fl. (2012) / `UTCI_a002.f90` anger följande giltighetsintervall (alla inklusiva):

| Storhet | Giltigt intervall | Anmärkning |
| --- | --- | --- |
| `t_luft_c` ($T_a$) | $[-50,\ 50]\ ^\circ\text{C}$ | |
| `t_mrt_c - t_luft_c` | $[-30,\ 70]\ ^\circ\text{C}$ | |
| `vind_10m_m_s` ($v_{10}$) | $[0{,}5,\ 17{,}0]\ \text{m/s}$ | Kläms aldrig tyst |
| `relativ_fuktighet_pct` | $[5,\ 100]\ \%$ | |
| `pa_kpa` ($p_a$) | $[0,\ 5{,}0]\ \text{kPa}$ | Motsvarar max 50 hPa i `UTCI_a002.f90` |

Utanför 5 kPa (t.ex. $50^\circ\text{C}$ vid $100\,\%$ RH) vänder 6:e-ordningens polynom nedåt och ger
ofysikaliska värden (ned till $-326^\circ\text{C}$). Därför flaggas varje cell utanför något intervall
(samt `+/-inf`) i `utanfor_giltighetsomrade`, tydliga varningar läggs i `varningar`, och med standardläget
`tillat_extrapolering=False` sätts utom-intervall-celler till `NaN` (med `klass_id = -99`). För
diagnostik kan `tillat_extrapolering=True` användas för att se det oklämda polynomvärdet samtidigt som
flaggning och varningar kvarstår.

### 2.5 Officiell 10-gradig UTCI-stresskala (Bröde m.fl. 2012, Tabell 1)

Skalan har 5 köldklasser, 1 neutral klass (`0 = Ingen termisk stress`) och 4 värmeklasser, varför
heltalsindexeringen centrerad kring noll löper från `-5` till `+4`:

| `klass_id` | Intervall ($^\circ\text{C}$) | Svenskt namn | Internationellt namn (Bröde 2012) |
| ---: | --- | --- | --- |
| `-5` | $< -40$ | Extrem köldstress | extreme cold stress |
| `-4` | $-40 \le \text{UTCI} < -27$ | Mycket stark köldstress | very strong cold stress |
| `-3` | $-27 \le \text{UTCI} < -13$ | Stark köldstress | strong cold stress |
| `-2` | $-13 \le \text{UTCI} < 0$ | Måttlig köldstress | moderate cold stress |
| `-1` | $0 \le \text{UTCI} < 9$ | Svag köldstress | slight cold stress |
| `0` | $9 \le \text{UTCI} \le 26$ | Ingen termisk stress | no thermal stress |
| `+1` | $26 < \text{UTCI} \le 32$ | Måttlig värmestress | moderate heat stress |
| `+2` | $32 < \text{UTCI} \le 38$ | Stark värmestress | strong heat stress |
| `+3` | $38 < \text{UTCI} \le 46$ | Mycket stark värmestress | very strong heat stress |
| `+4` | $> 46$ | Extrem värmestress | extreme heat stress |
| `-99` | `NaN` | Ogiltig / saknas | invalid / missing |

### 2.6 Anmärkning om polynomets regressionsavvikelse vid referensvillkoret

I den ursprungliga multi-nod-modellen (Fiala m.fl. 2012) gäller per definition att
$\text{UTCI}(T_a, T_a, 0{,}5\ \text{m/s}, 50\,\%) = T_a$ (för $T_a \le 29^\circ\text{C}$).
Bröde m.fl. (2012) anpassade det operativa 6:e-ordningens polynomet (`UTCI_a002.f90`, 210 termer)
med minsta kvadratmetoden över hela 4D-domänen med ett redovisat **globalt RMSE på $1{,}1^\circ\text{C}$**.
Vid referenspunkten $(20^\circ\text{C},\ 20^\circ\text{C},\ 0{,}5\ \text{m/s},\ 50\,\%)$ ger det
officiella oförändrade polynomet **`19.847649 °C`** (residual $-0{,}152^\circ\text{C}$ mot
$20{,}00^\circ\text{C}$). Floden modifierar **aldrig** Brödes koefficienter; testerna verifierar
exakt överensstämmelse ($< 10^{-8}\ ^\circ\text{C}$) mot `UTCI_a002.f90` / `pythermalcomfort` /
`ladybug-comfort`.

---

## 3. Medelstrålningstemperatur — $T_{\text{mrt}}$ (`tmrt.py`)

### 3.1 Klar himmels emissivitet (Prata 1996)

$$\xi = 46{,}5 \cdot \frac{e_a[\text{hPa}]}{T_K}, \qquad \varepsilon_{\text{himmel}} = 1 - (1 + \xi)\exp\!\left(-\sqrt{1{,}2 + 3\xi}\right)$$

Valfritt kan `emissivitet_himmel` anges explicit (t.ex. för mulet väder eller analytiska facitfall).

### 3.2 Övre halvklotets geometri och trädkronor

Med byggnadernas Sky View Factor $\text{svf} \in [0, 1]$, solbelysningsmask $\text{skuggmask} \in [0, 1]$,
valfri trädkronetäckning $c \in [0, 1]$ (`trad_mask`) och krontransmissivitet $\tau_{\text{trad}} \in [0, 1]$
(standard $0{,}03$, lövträd i sommargrönska enligt Lindberg & Grimmond 2011):

$$\tau_{\text{eff}} = 1 - c(1 - \tau_{\text{trad}}), \qquad \psi_{\text{sol}} = \text{skuggmask} \cdot \tau_{\text{eff}}$$
$$\text{svf}_{\text{eff}} = \text{svf} \cdot \tau_{\text{eff}}, \qquad f_{\text{trad}} = \text{svf} - \text{svf}_{\text{eff}}, \qquad f_{\text{vagg}} = 1 - \text{svf}$$

Summan $\text{svf}_{\text{eff}} + f_{\text{trad}} + f_{\text{vagg}} = 1$ exakt.

### 3.3 Sexriktade kortvågsflöden ($K_i$, $\text{W/m}^2$)

Med direkt normal solstrålning $I_{\perp}$ (`sol_direkt_w_m2`), diffus horisontell strålning
$K_{\text{diff}}$ (`sol_diffus_w_m2`), solhöjd $\alpha = \text{solhojd\_rad}$ (noll direkt sol när
$\alpha \le 0$), fasadalbedo $\alpha_{\text{vagg}}$ (standard $0{,}20$), markalbedo $\alpha_{\text{mark}}$
(standard $0{,}15$) och solbelyst fasadandel $f_{\text{vagg,sol}}$ (standard $0{,}50$):

$$K_{\text{dir}, h} = I_{\perp}\sin\alpha, \qquad K_{\text{vagg,refl}} = \alpha_{\text{vagg}}\, f_{\text{vagg}} \left(f_{\text{vagg,sol}} K_{\text{dir}, h} + K_{\text{diff}}\right)$$
$$K_{\downarrow} = \psi_{\text{sol}} K_{\text{dir}, h} + \text{svf}_{\text{eff}} K_{\text{diff}} + K_{\text{vagg,refl}}, \qquad K_{\uparrow} = \alpha_{\text{mark}} K_{\downarrow}$$
$$K_{\text{sida}} = \frac{1}{\pi}\psi_{\text{sol}} I_{\perp}\cos\alpha + \frac{1}{2}\text{svf}_{\text{eff}} K_{\text{diff}} + \frac{1}{2}K_{\text{vagg,refl}} + \frac{1}{2}K_{\uparrow}$$

Direktstrålningens sidoprojektion $\frac{1}{\pi} I_{\perp}\cos\alpha$ motsvarar den azimutoberoende
cylindermodellen för stående människa (Holmer m.fl. 2015), där
$4 F_{\text{sida}} / \pi = 0{,}88/\pi \approx 0{,}2801 \approx F_{\text{cyl}} = 0{,}28$.

### 3.4 Sexriktade långvågsflöden ($L_i$, $\text{W/m}^2$) och yttemperaturmodell

Stefan–Boltzmann-konstanten är $\sigma = 5{,}670374419 \times 10^{-8}\ \text{W/(m}^2\text{K}^4)$
(CODATA 2018 / SI 2019). När direkt solstrålning förekommer ($\mathbb{1}_{\text{sol}} = 1$ om
$\alpha > 0$ och $I_{\perp} > 0$, annars $0$) antas solbelysta ytor vara varmare än luften enligt
de explicita parametrarna `t_mark_soltillagg_c` ($\Delta T_{\text{mark}}$, standard $+15^\circ\text{C}$)
och `t_vagg_soltillagg_c` ($\Delta T_{\text{vagg}}$, standard $+10^\circ\text{C}$):

$$T_{\text{mark}, K} = T_K + \mathbb{1}_{\text{sol}} \psi_{\text{sol}} \Delta T_{\text{mark}}, \qquad T_{\text{vagg,sol}, K} = T_K + \mathbb{1}_{\text{sol}} \Delta T_{\text{vagg}}$$

Med Kirchhoffs reflektion för samtliga ytor (emissivitet $\varepsilon_{\text{mark}} = 0{,}95$,
$\varepsilon_{\text{vagg}} = 0{,}90$, $\varepsilon_{\text{trad}} = 0{,}98$):

$$L_{\text{himmel}} = \varepsilon_{\text{himmel}} \sigma T_K^4$$
$$L_{\text{trad}} = \varepsilon_{\text{trad}} \sigma T_K^4 + (1 - \varepsilon_{\text{trad}}) L_{\text{himmel}}$$
$$L_{\text{vagg}} = \varepsilon_{\text{vagg}} \sigma \left[ f_{\text{vagg,sol}} T_{\text{vagg,sol}, K}^4 + (1 - f_{\text{vagg,sol}}) T_K^4 \right] + (1 - \varepsilon_{\text{vagg}}) L_{\text{himmel}}$$
$$L_{\downarrow} = \text{svf}_{\text{eff}} L_{\text{himmel}} + f_{\text{trad}} L_{\text{trad}} + f_{\text{vagg}} L_{\text{vagg}}$$
$$L_{\uparrow} = \varepsilon_{\text{mark}} \sigma T_{\text{mark}, K}^4 + (1 - \varepsilon_{\text{mark}}) L_{\downarrow}$$
$$L_{\text{sida}} = \frac{1}{2} L_{\downarrow} + \frac{1}{2} L_{\uparrow}$$

### 3.5 Absorberad strålningstäthet och $T_{\text{mrt}}$

Med ISO 7726 / Höppe (1992) / SOLWEIG standardparametrar för stående människa
($F_{\downarrow} = F_{\uparrow} = 0{,}06$, $F_{\text{sida}} = 0{,}22 \times 4$, $\sum F_i = 1{,}00$,
$\alpha_k = 0{,}70$, $\varepsilon_p = 0{,}97$):

$$S_{\text{str}} = \alpha_k \left(F_{\downarrow} K_{\downarrow} + F_{\uparrow} K_{\uparrow} + 4 F_{\text{sida}} K_{\text{sida}}\right) + \varepsilon_p \left(F_{\downarrow} L_{\downarrow} + F_{\uparrow} L_{\uparrow} + 4 F_{\text{sida}} L_{\text{sida}}\right)$$
$$T_{\text{mrt}} = \left(\frac{S_{\text{str}}}{\varepsilon_p \sigma}\right)^{1/4} - 273{,}15\ [^\circ\text{C}]$$

Vid isotermiskt svartkroppsvillkor ($K = 0$, $\varepsilon_{\text{himmel}} = \varepsilon_{\text{mark}} = \varepsilon_{\text{vagg}} = 1{,}0$,
yttemperatur $= T_a$) uppfylls $L_{\downarrow} = L_{\uparrow} = L_{\text{sida}} = \sigma T_K^4$ och
$T_{\text{mrt}} = T_a$ exakt ned till flyttalsprecision ($< 10^{-12}\ ^\circ\text{C}$).

---

## 4. Högnivåanalys och planjämförelse (`fysik.py`)

- `berakna_varmestress(...) -> VarmestressResultat`: kopplar `berakna_tmrt` och `berakna_utci`
  (med valfri vindomvandling från fotgängarhöjd) samt beräknar ytstatistik (medel, min, max, p90,
  stressklassfördelning, andel stark värmestress $> 32^\circ\text{C}$, andel mycket stark värmestress
  $> 38^\circ\text{C}$, andel extrem värmestress $> 46^\circ\text{C}$).
- `jamfor_varmestress(fore, efter) -> VarmestressJamforelse`: beräknar cellvisa $\Delta T_{\text{mrt}}$
  och $\Delta\text{UTCI}$ (`efter - fore`, negativt = svalare) samt förändring i andel yta med
  stark/mycket stark/extrem värmestress.
- `varmestress_till_json` och `varmestress_jamforelse_till_json`: strikt deterministisk JSON-export
  (`sort_keys=True`, `allow_nan=False`) med SHA-256-kontrollsummor för rastrerna via `gemensamt.hash`.

---

## 5. Begränsningar (`skiss`)

1. **Isotrop vägg- och himmelsmodell:** 2D-rasterfält (`svf`, `skuggmask`) saknar riktade
   kardinal-SVF:er (`svfN/E/S/W`) och anisotrop Perez-himmel; solbelyst fasadandel är därför en
   parameter (`vagg_solandel`, standard `0,50`).
2. **Statisk yttemperaturmodell:** `t_mark_soltillagg_c` och `t_vagg_soltillagg_c` modellerar
   överskottstemperatur vid direkt sol men saknar termisk tröghet (SOLWEIG:s vågfördröjning under
   sen eftermiddag/kväll) och markmaterialberoende energibalans.
3. **Klar himmel som standard:** Prata (1996) gäller klar himmel; molnighet måste anges via explicit
   `emissivitet_himmel` samt uppmätt/modellerad `sol_direkt_w_m2` och `sol_diffus_w_m2`.
4. **UTCI-polynomets giltighetsgränser:** Lugna innergårdar med $v_{10} < 0{,}5\ \text{m/s}$ faller
   utanför Brödes polynomintervall och flaggas som osäkra (sätts till `NaN` som standard).

---

## 6. Litteraturkällor

- **Bröde, P., Fiala, D., Błażejczyk, K., Holmér, I., Jendritzky, G., Kampmann, B., Tinz, B. & Havenith, G. (2012).** *Deriving the operational procedure for the Universal Thermal Climate Index (UTCI).* Int. J. Biometeorol. 56:481–494. Referenskod: `UTCI_a002.f90` (Version a 0.002, `utci.org`).
- **Hardy, B. (1998).** *ITS-90 Formulations for Vapor Pressure, Frostpoint Temperature, Dewpoint Temperature, and Enhancement Factors in the Range −100 to +100 °C.* Proc. 3rd Int. Symp. on Humidity & Moisture, NPL, London, s. 214–221.
- **Holmer, B., Lindberg, F., Rayner, D. & Thorsson, S. (2015).** *How to transform the standing man from a box to a cylinder — a modified methodology to calculate mean radiant temperature in field studies and models.* 9th International Conference on Urban Climate (ICUC9), Toulouse.
- **ISO 7726 (1998).** *Ergonomics of the thermal environment — Instruments for measuring physical quantities.* International Organization for Standardization.
- **Jonsson, P., Eliasson, I., Holmer, B. & Grimmond, C. S. B. (2006).** *Longwave incoming radiation in the Tropics: results from field work in three African cities.* Theor. Appl. Climatol. 85:185–201.
- **Lindberg, F., Holmer, B. & Thorsson, S. (2008).** *SOLWEIG 1.0 — Modelling spatial variations of 3D radiant fluxes and mean radiant temperature in complex urban settings.* Int. J. Biometeorol. 52:697–711.
- **Lindberg, F. & Grimmond, C. S. B. (2011).** *The influence of vegetation and building morphology on shadow patterns and mean radiant temperatures in urban areas: model development and evaluation.* Theor. Appl. Climatol. 105:311–323.
- **Prata, A. J. (1996).** *A new long-wave formula for estimating downward clear-sky radiation at the surface.* Q. J. R. Meteorol. Soc. 122:1127–1151.
