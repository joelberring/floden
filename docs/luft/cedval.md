# CEDVAL A1-1 – Vindtunnelvalidering för 3D-vind (`src/luft/cedval.py`)

*Spår L, uppdrag L-c / 12c. Metodstatus: `skiss` (ingen lokal experimentell vindtunnel-CSV incheckad eller jämförd i denna baslinje).*

**Licens:** MIT (samma licensgräns som övriga `src/luft/`; importerar inga GPL-paket, verifierat med `tests/gemensamt/test_paketgranser.py`).

## 1. Strikt åtskillnad mellan källor

I enlighet med projektets regel *”Fysiken är facit, AI är verktyg”* skiljer koden och rapporterna strikt mellan tre separata lager:

1. **Publicerad geometri och inloppsprofil (CEDVAL A1-1):**
   Källa: Universität Hamburg, Environmental Wind Tunnel Laboratory (EWTL), CEDVAL-databasen, fall **A1-1** (fristående rektangulär byggnad; Leitl & Schatzmann 2010; Franke m.fl. 2007 / COST Action 732).
   - Fullskalemått (1:200-modell uppskalad ×200): $L_x \times L_y \times H = 20\text{ m} \times 30\text{ m} \times 25\text{ m}$ (vindtunnelmodell $0{,}10 \times 0{,}15 \times 0{,}125\text{ m}$).
   - Neutral logaritmisk gränsskiktsprofil (ABL):
     $$U(z) = \frac{u_*}{\kappa} \ln\!\left(\frac{z + z_0}{z_0}\right) = U_{\text{ref}} \frac{\ln((z + z_0)/z_0)}{\ln((z_{\text{ref}} + z_0)/z_0)} \quad (z > 0), \qquad U(z) = 0 \quad (z \le 0)$$
     med $z_0 = 0{,}7\text{ m}$ (modellskala $0{,}0035\text{ m}$), $z_{\text{ref}} = H = 25\text{ m}$, $U_{\text{ref}} = 6{,}0\text{ m/s}$, $\kappa = 0{,}41$ ($u_* = 0{,}6827\text{ m/s}$).
   - Publicerat litteraturintervall för återanslutningslängd bakom byggnaden: $X_R / H \approx 1{,}0\text{–}1{,}8$.
2. **Faktiska deterministiska körningar av Flodens egna 3D-vindlösare (`snabbvind` och `lbm`):**
   Se tabellen i avsnitt 3 med uppmätta värden på nuvarande kodbas. Varje körning markeras med `metodstatus = "skiss"` och `jamford_mot_vindtunnel_data = False` tills den körs med en verklig lokal vindtunnel-CSV.
3. **Saknad lokal experimentell vindtunnel-CSV:**
   Inga experimentella mätdata från EWTL Hamburg är incheckade i repot och inga mätvärden uppskattas eller genereras syntetiskt som facit. När användaren själv laddat ned officiella CEDVAL A1-1-mätdata lokalt läses de med `jamfor_mot_cedval_csv` / `--cedval-csv`. Saknad eller ogiltig fil kastar `FileNotFoundError` respektive `ValueError` (inga tysta fel).

## 2. Koordinat- och CSV-konvention

Origo $(x = 0, y = 0, z = 0)$ ligger i byggnadens bascentrum på markplanet (fullskala, meter), med vinden i $+x$-riktningen:

- Byggnad: $x \in [-10, +10]\text{ m}$, $y \in [-15, +15]\text{ m}$, $z \in [0, 25]\text{ m}$.
- Standarddomän (COST 732): $5H = 125\text{ m}$ uppströms, $10H = 250\text{ m}$ nedströms, $4H = 100\text{ m}$ sidomarginal per sida och $5H = 125\text{ m}$ höjd (blockeringsgrad $2{,}61\ \% < 3\ \%$).
- Lokal mätfil (`--cedval-csv`) ska vara UTF-8 CSV med rubrikrad:

```csv
x_m,y_m,z_m,u_norm,v_norm,w_norm
```

där hastighetskomponenterna är normerade med $U_{\text{ref}}$. `jamfor_mot_cedval_csv` trilinjärinterpolerar det beräknade 3D-vindfältet till varje giltig luftpunkt, räknar punkter utanför domänen eller inne i byggnaden separat och beräknar FAIRMODE / COST 732 / VDI 3783 Blatt 9-måtten:

| Mått | Definition | COST 732-riktvärde |
| --- | --- | --- |
| `RMSE` | $\sqrt{\overline{(P - O)^2}}$ | Rapporteras (mindre är bättre) |
| `NMSE` | $\overline{(O - P)^2} / (\bar{O}\bar{P})$ | $\le 4{,}0$ (när $\bar{O}\bar{P} > 0$) |
| `FB` | $2(\bar{O} - \bar{P}) / (\bar{O} + \bar{P})$ | $|\text{FB}| \le 0{,}3$ |
| `FAC2` | Andel punkter med $0{,}5 \le P / O \le 2{,}0$ | $\ge 0{,}5$ |
| `hit_rate` ($q$) | Andel med $|P - O| \le \max(D|O|, W)$, $D = 0{,}25$, $W = 0{,}05$ | $\ge 0{,}66$ |

## 3. Uppmätta resultat på CPU (baslinje utan lokal vindtunnel-CSV)

Uppmätt lokalt på CPU (`numpy`-backend, ingen lokal vindtunnel-CSV, `jamford_mot_vindtunnel_data = False`, `metodstatus = "skiss"`):

| Körning | Rutnät (`nx×ny×nz`) | `dx` (m) | Block. (%) | Körtid (s) | Max solid (m/s) | Proj. div. (rel) | Inre div. RMS (1/s) | Inloppsfel (%) | Min `u_x/U_ref` i lä | Recirk. (`u_x < 0`) | `X_R / H` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `snabbvind` (COST 732-domän) | `79 × 46 × 25` | 5,0 | 2,61 | 0,331 | 0,0000 | `3,12e-7` | 0,0036 | 0,044 | 0,0217 | Nej | – |
| `snabbvind` (COST 732-domän) | `158 × 92 × 50` | 2,5 | 2,61 | 2,775 | 0,0000 | `1,43e-7` | 0,0039 | 0,021 | 0,0167 | Nej | – |
| `lbm` (kompakt CPU-domän, 1+1 genomstr., `loglag`) | `59 × 36 × 20` | 5,0 | 4,17 | 11,736 | 0,0000 | – | 0,0033 | 0,000 | −0,1781 | Ja | 4,170 |

**Tolkning av mätvärdena:**
- **No-slip och inloppsprofil:** Båda lösarna håller exakt `0,0000 m/s` i byggnadscellerna och återskapar den logaritmiska ABL-profilen vid inloppet med $< 0{,}05\ \%$ fel (`lbm`: $7{,}03 \times 10^{-7}\text{ m/s}$).
- **Masskonservering:** `snabbvind` ger diskret divergensfria ytflöden till `1,4–3,1e-7` relativ rest (inre centraldifferens-RMS `0,0036–0,0039 1/s` vid skalan $U_{\text{ref}}/dx = 1{,}2\text{–}2{,}4\text{ s}^{-1}$); `lbm` har inre divergens-RMS `0,0033 1/s`.
- **Vindskugga och lävirvel:** `snabbvind` skapar en tydlig vindskugga bakom byggnaden (`min_u_x_la_norm ≈ 0,017–0,022`), men tryckprojektionen slätar ut den svaga empiriska returströmmen så att `u_x` förblir svagt positiv på centrumlinjen. `lbm` löser däremot upp en verklig 3D-recirkulationszon (`min_u_x_la_norm = -0,1781`), men på grovt rutnät ($H / dx = 5$ celler) blir återanslutningslängden kraftigt överskattad ($X_R / H = 4{,}17$ mot litteraturens $1{,}0\text{–}1{,}8$). Det stämmer helt överens med den befintliga grova kubvalideringen i `tests/luft/test_lbm_validering.py` ($H/dx = 6 \Rightarrow x_r/H = 3{,}9\text{–}4{,}3$, där förfining krymper vaken mot litteraturvärdet).

## 4. Körning via CLI och K37-export

```bash
# Snabb skisskontroll på CPU med K37-kompatibel JSON och Markdown-rapport
python tools/validera_cedval.py \
    --cellstorlek-m 5.0 \
    --losare snabbvind \
    --ut-json utdata/cedval_a11_snabbvind.json \
    --ut-md utdata/cedval_a11_snabbvind.md

# Fin GPU-körning (kräver CUDA/CuPy-server och lokalt nedladdad CEDVAL A1-1 CSV)
python tools/validera_cedval.py \
    --cellstorlek-m 1.0 \
    --losare lbm \
    --backend cupy \
    --lbm-upprampning 3.0 \
    --lbm-medel 4.0 \
    --cedval-csv /sokvag/till/cedval_a11_fullskala.csv \
    --ut-json utdata/cedval_a11_lbm_1m.json \
    --ut-md utdata/cedval_a11_lbm_1m.md
```

JSON-utdata följer kontrakt **K37** (`floden-benchmark/v1`, kontrollerat mot `benchmark.format.kontrollera`). När `--cedval-csv` utelämnas sätts `avvikelse = null` och `extra.jamford_mot_vindtunnel_data = false`.

## 5. Vad som krävs för skarp experimentell validering

1. Ladda ned officiella CEDVAL A1-1 vindtunnelmätdata (laser-Doppler-anemometri, $u, v, w$) från EWTL, Universität Hamburg, och omvandla koordinater till fullskala (×200) i CSV-formatet i avsnitt 2.
2. Kör `lbm` på GPU-server (`--backend cupy`) med minst $H / dx \ge 20$ (`--cellstorlek-m 1.0` eller `1.25`), syntetisk inflödesturbulens (SEM) och längre medelvärdesbildning ($\ge 4$ genomströmningar) i full COST 732-domän.
3. Redovisa uppmätta COST 732-mått (`FAC2`, `hit_rate`, `FB`, `NMSE`, `RMSE`) och $X_R / H$ innan metodstatus kan höjas från `skiss`.
