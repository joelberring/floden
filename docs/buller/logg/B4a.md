# B4a – Akustiska grundfunktioner och baslinjekärna

Datum: 2026-11-21. Ägda mappar: `src/buller/akustik/`, `tests/buller/akustik/`, denna fil.

**Detta är inte Nord2000.** Inget i paketet är hämtat ur Nord2000:s metodrapporter (som saknas, se
`docs/buller/kallor.md`). `BaslinjeKarna.nord2000_konform = False`; rapportläget ska vägra den (KB3).

## Gjort

| Fil | Innehåll |
| --- | --- |
| `band.py` | exakta tersbandsmittfrekvenser (bas 10), bandgränser, delfrekvenser inom band, `a_vagning_db() -> (27,)` |
| `luftabsorption.py` | `luftabsorption_db_per_m(f, T, RF, p)`, `ljudhastighet_m_s(T)`, molkoncentration vattenånga |
| `faddeeva.py` | `faddeeva(z)`: w(z) i ren numpy (Weideman, N = 40), hela komplexa planet via spegling |
| `impedans.py` | Delany–Bazley: `delany_bazley_impedans`, `delany_bazley_admittans` (σ = ∞ ger β = 0) |
| `reflektion.py` | `planvagsreflektion`, `numeriskt_avstand`, `randforlustfaktor`, `sfarisk_reflektion`, `tvastralefaktor` (rena toner) |
| `baslinje.py` | `BaslinjeKarna` (`P2PKarna`), `BaslinjeResultat`, `kurze_anderson_db` |
| `__init__.py` | exporterar ovanstående |

Körberoende: endast numpy. scipy används i två tester med `unittest.skipUnless`.

Tidskonvention för alla komplexa storheter: **exp(−iωt)** (Im Z > 0 för mark, utgående våg exp(+ikR)/R).

## Källor per funktion

| Funktion | Källa | Hur kontrollerad |
| --- | --- | --- |
| `exakta_mittfrekvenser_hz`, `bandgranser_hz` | IEC 61260-1:2014 (bas 10: f = 1000·10^(x/10), gränser ·10^(±1/20)) | ur minnet; avviker ≤ 0,95 % från nominella `TERSBAND_HZ` |
| `a_vagning_db` | IEC 61672-1:2013 ekv. för A-vägning + bilaga E (polfrekvenser) | polfrekvenserna lika med pyfars konstanter (20,598997…; 107,65265…; 737,86223…; 12194,217…) till ≥ 9 decimaler; alla 27 band inom 0,05 dB från standardens tabell 3 (tabellen avläst via pyfar) |
| `luftabsorption_db_per_m` | ISO 9613-1:1993 ekv. (3)–(5), bilaga B | ekv. (3)–(5) lästa ur iteh:s förhandsvisning av standarden (OCR); bilaga B (mättnadstryck) och faktorn 8,686 ur minnet men bekräftade genom att alla 48 värden i ISO 9613-2 tabell 2 och fem läsbara värden i ISO 9613-1 tabell 1 återges |
| `ljudhastighet_m_s` | c = 343,2·√(T/293,15 K) | ur minnet; var i ISO 9613-1 uttrycket står är inte kontrollerat |
| `faddeeva` | Weideman, SIAM J. Numer. Anal. 31 (1994) 1497–1518; spegling A&S 7.1.11 | mot `scipy.special.wofz`, se mätvärden |
| `delany_bazley_*` | Delany & Bazley, Applied Acoustics 3 (1970) 105–116; markformen Z = 1 + 9,08(f/σ)^−0,75 + i·11,9(f/σ)^−0,73, σ i kPa·s/m² | koefficienter och exponenter kontrollerade mot python-acoustics (som har exp(+jωt), alltså −j); enhet kontrollerad genom jämförelse med originalets 0,0571/0,087-form (test, < 5 %) |
| `sfarisk_reflektion` m.fl. | Chien & Soroka, JSV 43 (1975) 9–20; Attenborough, Li & Horoshenkov (2007) kap. 2; Salomons (2001) bilaga D | Rp, w² = ikR₂/2·(cos θ + β)², F(w) = 1 + i√π·w·e^(−w²)·erfc(−iw) kontrollerade mot Howorth, Attenborough & Heap (IOA-artikel, OCR-läst) |
| `kurze_anderson_db` | Kurze & Anderson, Applied Acoustics 4 (1971) 35–53 (anpassning till Maekawa 1968) | formeln 5 + 20·lg(√(2πN)/tanh √(2πN)) bekräftad mot Arups Strutt-dokumentation (som kallar den Maekawa); taket 24 dB ur minnet (läroboksvärde, formeln når 24 dB vid N ≈ 12,5) |
| geometrisk spridning | −10·lg(4πR²) (ISO 9613-2 ekv. (7): 20·lg d + 11) | ur minnet, elementärt |

## Val i baslinjekärnan (egna, inte ur någon norm)

1. **Ekvivalent plan:** minsta kvadrat-linje genom hela profilen, kontinuerlig anpassning (exakta integraler per
   segment, oberoende av punkttäthet). Höjder mäts vinkelrätt mot planet. Under planet → höjd 0 + varning.
   Alternativet "segmentet vid spegelpunkten" valdes bort eftersom det är diskontinuerligt när spegelpunkten byter segment.
2. **Impedans:** flödesresistiviteten i segmentet där spegelpunkten på planet hamnar. Ingen zonviktning.
3. **Bandmedelvärde:** 7 frekvenser per band (delbandsmitt, lika logaritmisk bredd), energimedelvärde. Valbart.
4. **Tak +6,02 dB** på tvåstrålefaktorn per delfrekvens (se nedan). Golv 10⁻¹² enbart mot lg(0).
5. **Skärm:** en inre profilpunkt strikt över siktlinjen ⇒ skärmad. Dominerande kant = störst gångvägsskillnad.
   Fresneltal vid bandets mittfrekvens. Markeffekt = 0 dB. Luftabsorption över sträckan R₁ + δ.
6. **Väder:** temperatur, fuktighet och tryck används (ljudhastighet, luftabsorption). Vind, gradient, turbulens,
   fluktuationer och råhet ignoreras utan fel; `BaslinjeKarna.ignorerat` (alltid) och `varningar(profil, vader)` /
   `BaslinjeResultat.varningar` (per anrop) redovisar det.
7. **`ovre_grans_db(d)`** = −10·lg(4πd²) + 10·lg 4, lika i alla band. Bevis: spridningen räknas på R₁ ≥ d,
   marktillskottet ≤ +6,02 dB genom taket, skärm- och luftdämpning ≥ 0. Gäller även med d = `x_m[-1]`.

## Mätvärden (denna miljö: Python 3.13.18, numpy 2.5.3, scipy 1.18.1, en tråd)

- **Tester:** `python -m pytest tests/buller/akustik -q` → 59 gröna, 0 hoppade, ca 1,3 s.
  `ruff check` och `ruff format --check`: rent. `mypy src/buller/akustik` (strict): inga fel.
- **Faddeeva mot scipy:** max relativt fel 7,3·10⁻¹⁵ på rutnätet Re z ∈ {0, ±10^(−3…3)} × Im z ∈ {0, 10^(−6…3)}
  (243 × 182 punkter). N = 32 gav 3,1·10⁻¹³, N = 48 ingen förbättring. Nedre halvplanet
  (|Re z| ≤ 6, −4 ≤ Im z ≤ −0,01): < 10⁻¹¹.
- **A-vägning:** största avvikelse mot tabell 3 < 0,05 dB (dvs. bara avrundning); 100 Hz: −19,14 dB; 1 kHz: 0 exakt.
- **Luftabsorption:** 47 av 48 värden i ISO 9613-2 tabell 2 inom halva sista siffran. Undantag: 15 °C / 80 % / 1 kHz
  ger 4,151 dB/km mot tabellens 4,1 (0,051 ifrån). Testtoleransen är därför 0,06 dB/km. Formeln har inte justerats.
- **Genomströmning `overforing_db`:** fri sikt, 2 punkter: ca 4 100 profiler/s; fri sikt, 40 punkter: ca 3 100/s;
  skärmad (5 punkter): ca 20 600/s; slumpad blandning (38 % skärmade, 2–40 punkter): ca 5 700–5 800/s.
- **Övre gräns:** 3 000 slumpade profiler i testet och 5 000 i mätningen; största `overforing − gräns` = −4·10⁻⁵ dB
  (alltså aldrig över). Gränsen är skarp: hård mark, 25 Hz, 20 m ligger < 0,01 dB under den.
- **Hur ofta taket slår till:** av 3 081 slumpade profiler med fri sikt påverkades 4,5 % i något band (0,8 % av alla
  bandvärden); största sänkning 0,16 dB; största marktillskott utan tak 6,19 dB. Dock: på ett brett parameterrutnät
  ger närmeformen |Q| upp till 2,0 (≈ +9,6 dB i värsta fall), så utan tak finns ingen bevisbar gräns.

## Kända brister

- **Diskontinuitet vid siktlinjen:** när en kant precis bryter siktlinjen hoppar resultatet från markeffekt
  (−∞ … +6 dB) till −5 dB skärmdämpning utan mark. Kurze–Andersons gren för N < 0 är inte med.
  Klustring och interpolation mot baslinjekärnan bör inte anta kontinuitet där.
- **Skärmar som inte bryter siktlinjen** (låga murar, byggnader under en högt placerad mottagare) drar med sig
  minsta kvadrat-planet. 10 % av de slumpade profilerna gav varningen "under det ekvivalenta planet".
- **Taket +6,02 dB** tar bort en liten del av ytvågsbidraget (se mätvärden).
- **Delany–Bazley** används utanför sitt uppmätta område (hård mark, låga frekvenser), som brukligt.
- **Weyl–van der Pol-formen** är en närmeform (kR₂ ≫ 1, |β| litet eller strykande infall).
- **Bandmedelvärde med 7 frekvenser** är glest över ca 1 kHz vid långa avstånd/höga höjder (många fransar per band);
  testet kräver < 0,5 dB mot 201 frekvenser upp till 1 kHz i ett typfall. Luftabsorption och skärm räknas bara vid
  mittfrekvensen (enligt uppdraget).
- **En kant, tunn skärm:** ingen dubbeldiffraktion, ingen tjock skärm, ingen sidodiffraktion, inga markreflexer
  före/efter skärmen.
- **Indatakontroll:** flödesresistivitet ≤ 0 eller NaN samt ogiltigt väder ger `BullerFel`. Inga varningar ges för
  väder utanför ISO 9613-1:s giltighetsområde.
- Genomströmningen (≈ 4 000–6 000 profiler/s) räcker inte för stora skisskörningar; ingen batchmetod finns.

## Avvikelse från uppdragstexten

Uppdraget angav testet "F(w) → 1 för stora |w|". Det stämmer inte med formeln: F(w) → 1 när |w| → **0**, och
F(w) ≈ −1/(2w²) → 0 när |w| → ∞ (då går Q → R_p, vilket är det som ger Q → −1 vid strykande infall över mjuk mark).
Testerna prövar de korrekta gränsvärdena.

## Inte gjort / avstått från

- Inget ur Nord2000: ingen Fresnelzonsviktning, koherensfaktorer, terrängklassning, skärmmodell eller refraktion.
- Ingen råhetsmodell, ingen turbulens-/dekorrelationsterm, ingen impedansmodell utöver Delany–Bazley.
- `ljudhastighet_m_s`: källhänvisningen till exakt ställe i ISO 9613-1 är inte kontrollerad.
- Taket 24 dB för Kurze–Anderson är inte kontrollerat mot originalartikeln (Arup anger 25 dB, ISO 9613-2 20 dB
  för sina respektive formler).
- ISO 9613-1 och IEC 61672-1 har inte lästs i original; kontrollerna ovan är mot sekundära källor och tabellvärden.

## Återstår

- Batchgränssnitt (många profiler i ett anrop) om baslinjekärnan ska användas för stora skisskörningar.
- Kontinuerlig övergång vid siktlinjen (t.ex. N < 0-grenen), om B8:s felgränser kräver det.
- Ersätts av B4 (referenskärnan) när metodrapporterna finns; `band.py`, `luftabsorption.py`, `faddeeva.py` och
  `impedans.py` kan återanvändas där efter kontroll mot rapporterna.
