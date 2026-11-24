// GENERERAD av tools/generera_matematik.py ur data/matematik/metoder.toml
// och data/mcp/metodstatus.toml.
// Redigera inte för hand; kör `python tools/generera_matematik.py`.
import type { MatematikData } from "./typer.ts";

export const MATEMATIK: MatematikData = {
  "doman": [
    {
      "id": "vatten",
      "namn": "Vatten",
      "metoder": [
        {
          "id": "vatten.sankgraf",
          "namn": "Sänkgraf som sammanslagningsträd",
          "sammanfattning": "Terrängens sänkor och deras överfall som ett rotat träd (Fill–Spill–Merge, Barnes 2020), med persistensfilter, slutsänkor och takvatten som linjär omfördelning.",
          "formler": [
            "p(u) = z_öv(u) − z_min(u); sänkor med p(u) < f·σ(u), f = 2, filtreras bort",
            "Takvatten: fasta vikter från varje takcell till markceller, kolumnsumma 1 (linjärt i regnet)"
          ],
          "flode": "flode",
          "flodeText": "I användarflödet",
          "status": "skiss",
          "kod": [
            {
              "text": "src/sankgraf/hierarki.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/hierarki.py"
            },
            {
              "text": "src/sankgraf/persistens.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/persistens.py"
            },
            {
              "text": "src/sankgraf/fyllning.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/fyllning.py"
            },
            {
              "text": "src/sankgraf/takvatten.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/takvatten.py"
            },
            {
              "text": "src/sankgraf/avtappning.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/avtappning.py"
            }
          ],
          "kontrakt": [
            "K1 (v2)",
            "K2 (v2)"
          ],
          "facit": [
            {
              "text": "testsvit/A1_enkel_sanka (A0–A13, `floden testsvit`)",
              "href": "https://github.com/joelberring/floden/tree/main/testsvit/A1_enkel_sanka"
            },
            {
              "text": "tests/sankgraf/test_a_fall_statisk.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/sankgraf/test_a_fall_statisk.py"
            },
            {
              "text": "tests/sankgraf/test_instangt_omrade.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/sankgraf/test_instangt_omrade.py"
            },
            {
              "text": "tests/sankgraf/test_takvatten.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/sankgraf/test_takvatten.py"
            }
          ],
          "anmarkning": "Persistensfiltret är relativt: en sänka tas bort när p(u) < f·σ(u) med f = 2 (inte en fast gräns). Takvattnets omfördelning har kolumnsumma 1 inklusive vatten som lämnar rutan; andelen till ledningsnät (standard 0) tas bort före fördelningen."
        },
        {
          "id": "vatten.dynamik",
          "namn": "Händelsestyrd dynamik i grafen",
          "sammanfattning": "Volymen i varje sänka är styckvis linjär mellan händelser (bräddning, sammanslagning); lösaren hoppar mellan händelserna i stället för att stega i tid.",
          "formler": [
            "dV_u/dt = r(t)·A_eff(u) + Σ Q_(w→u)(t) − Q_avt(u)",
            "Q = C_d·B·max(0, Δh)^(3/2)  (bredskronsöverfall, tidsstegning)"
          ],
          "flode": "flode",
          "flodeText": "I användarflödet",
          "status": "skiss",
          "kod": [
            {
              "text": "src/dynamik/handelsestyrd.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dynamik/handelsestyrd.py"
            },
            {
              "text": "src/dynamik/nodmodell.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dynamik/nodmodell.py"
            },
            {
              "text": "src/dynamik/enhetssvar.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dynamik/enhetssvar.py"
            },
            {
              "text": "src/dynamik/tidsstegning.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dynamik/tidsstegning.py"
            }
          ],
          "kontrakt": [
            "K4",
            "K6",
            "K21 (lösaren vatten.dynamik)"
          ],
          "facit": [
            {
              "text": "tests/dynamik/test_a_fall_dynamik.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dynamik/test_a_fall_dynamik.py"
            },
            {
              "text": "tests/dynamik/test_enhetssvar_och_ensemble.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dynamik/test_enhetssvar_och_ensemble.py"
            },
            {
              "text": "testsvit/A1_enkel_sanka (A1–A3, A9–A13)",
              "href": "https://github.com/joelberring/floden/tree/main/testsvit/A1_enkel_sanka"
            }
          ],
          "anmarkning": "Exakt bara i den kinematiska överfallsgränsen (omedelbart överfall). Bredskronsöverfall Q = C_d·B·max(0, Δh)^(3/2) finns som tidsstegning (`overfall = skibord`) och ger en fördröjning på +1,1 % till +2,1 % i A2 och A3 (mätt 2026-11-20, docs/MATEMATIK.md): öppen fråga Q1."
        },
        {
          "id": "vatten.tvad_hybrid",
          "namn": "2D lokal tröghet, subgrid och hybrid",
          "sammanfattning": "Lokal-tröghetsapproximationen (Bates 2010, de Almeida m.fl. 2012) på rutnät med θ-viktning, subgridtabeller och koppling till grafens djupa noder; en a posteriori-indikator pekar ut var 2D behövs.",
          "formler": [
            "Δt ≤ CFL·Δx / (√(g·h_max) + |u|_max), CFL = 0,5",
            "q̄_i = θ·q_i + ½(1 − θ)(q_(i−1) + q_(i+1)), θ = 0,9",
            "Fr = u / √(g·h) vid överfallskanten (tröskel 0,30); felindikatorn väger också lutning (0,015) och genomströmningskvot (4,0)"
          ],
          "flode": "cli",
          "flodeText": "Kommandorad, MCP och testsvit",
          "status": "skiss",
          "kod": [
            {
              "text": "src/tvad/lokal_troghet.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/tvad/lokal_troghet.py"
            },
            {
              "text": "src/tvad/hybrid.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/tvad/hybrid.py"
            },
            {
              "text": "src/tvad/subgrid.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/tvad/subgrid.py"
            },
            {
              "text": "src/tvad/felindikator.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/tvad/felindikator.py"
            },
            {
              "text": "src/vatten/losare_2d.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/vatten/losare_2d.py"
            },
            {
              "text": "rust/tvad_karna",
              "href": "https://github.com/joelberring/floden/tree/main/rust/tvad_karna"
            }
          ],
          "kontrakt": [
            "K1",
            "K5",
            "K6",
            "K36 (felindikator i API-svaret)"
          ],
          "facit": [
            {
              "text": "testsvit EA1, EA2, EA8A (`floden testsvit`)",
              "href": null
            },
            {
              "text": "testsvit grupp D (Hunter, `floden testsvit --grupp D`)",
              "href": null
            },
            {
              "text": "tests/tvad/test_hastighet_facit.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/tvad/test_hastighet_facit.py"
            },
            {
              "text": "tests/tvad/test_lokal_troghet_och_hybrid.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/tvad/test_lokal_troghet_och_hybrid.py"
            },
            {
              "text": "tests/tvad/test_felindikator.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/tvad/test_felindikator.py"
            }
          ],
          "anmarkning": "Körs med `floden kor --modell 2d|hybrid` och i testsviten; ingår inte i webbflödets lösare. Hybridens uppsnabbning (12,9–20,0× vid subgrid = 4, volymfel < 2·10^-13) mättes 2026-11-20, före vattenkärnans rättelser 2026-11-23 (docs/BESLUT.md), och är inte omätt sedan dess. Hybridens fel under pågående överfall är öppen fråga Q3."
        },
        {
          "id": "vatten.bifurkation",
          "namn": "Bifurkationsdetektor",
          "sammanfattning": "Flaggar överfallspunkter där höjdosäkerheten kan få vattnet att byta utlopp, med påverkad avrinningsyta och volym.",
          "formler": [
            "Δz = max(0, z₂ − z₁), σ = √(σ₁² + σ₂²); instabil om Δz/σ ≤ k (k = 2)"
          ],
          "flode": "karta",
          "flodeText": "I kartsidan (#/karta), inte i flödet",
          "status": "skiss",
          "kod": [
            {
              "text": "src/sankgraf/bifurkation.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/bifurkation.py"
            },
            {
              "text": "src/webb/api.py (POST /api/analys/bifurkationer)",
              "href": "https://github.com/joelberring/floden/blob/main/src/webb/api.py"
            },
            {
              "text": "webb/src/karta/overfall.ts",
              "href": "https://github.com/joelberring/floden/blob/main/webb/src/karta/overfall.ts"
            }
          ],
          "kontrakt": [
            "K34",
            "K35",
            "K36"
          ],
          "facit": [
            {
              "text": "tests/sankgraf/test_bifurkation.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/sankgraf/test_bifurkation.py"
            },
            {
              "text": "tests/webb/test_bifurkation_projekt.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/webb/test_bifurkation_projekt.py"
            },
            {
              "text": "tests/tvad/test_felindikator.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/tvad/test_felindikator.py"
            }
          ],
          "anmarkning": "Nås som lager i kartsidan (#/karta), inte i flödet. Gränsen är m ≤ k (inte strikt <), standard k = 2. Detta är en heuristik, ingen persistensteori: se öppen fråga Q2."
        },
        {
          "id": "vatten.intervall",
          "namn": "Monoton intervallanalys för vatten",
          "sammanfattning": "Under fast flödestopologi är maxdjupet monotont i regn, initial förlust och avtappning; två extremkörningar ger garanterade gränser, med grenunion kring instabila bifurkationer.",
          "formler": [
            "h_min(t) ≤ h_u(t) ≤ h_max(t); gränserna ur två extremkörningar (mer regn, lägre förlust, lägre avtappning ⇒ högre nivå)"
          ],
          "flode": "api",
          "flodeText": "Bara i API:t",
          "status": "skiss",
          "kod": [
            {
              "text": "src/dynamik/intervall.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dynamik/intervall.py"
            },
            {
              "text": "src/webb/api.py (POST /api/analys/intervall)",
              "href": "https://github.com/joelberring/floden/blob/main/src/webb/api.py"
            }
          ],
          "kontrakt": [
            "K40",
            "K34"
          ],
          "facit": [
            {
              "text": "tests/dynamik/test_intervall.py (50 slumpade scenarier på vart och ett av A1–A4, totalt 200, alla inneslutna)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dynamik/test_intervall.py"
            },
            {
              "text": "tests/webb/test_analys_steg_a.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/webb/test_analys_steg_a.py"
            }
          ],
          "anmarkning": "Garantin gäller delträd utan bifurkation; berörda noder märks `monoton_garanterad = False`. Ingen panel i klienten (K40: läses av inget i webbklienten)."
        }
      ]
    },
    {
      "id": "dagsljus",
      "namn": "Dagsljus",
      "metoder": [
        {
          "id": "dagsljus.horisont_svf_vsc",
          "namn": "Horisont, soltimmar, SVF och VSC",
          "sammanfattning": "Horisontvinkel per azimut över ett prismafält (DSM), soltimmar ur solbanan, himmelsandel (SVF) och vertikal himmelskomponent (VSC, mulen CIE-himmel, BRE-normering).",
          "formler": [
            "h(φ) = max(0, max över celler c på strålen: atan((z_c − z₀) / s_c))",
            "SVF = 1 − (1/K)·Σ sin² h_k   (fri yta: 1)",
            "VSC = (1/N)·∫ I(h(φ_n + ψ))·cos ψ dψ, N = 7π/9; oskymd fasad 39,6 %"
          ],
          "flode": "flode",
          "flodeText": "I användarflödet",
          "status": "skiss",
          "kod": [
            {
              "text": "src/dagsljus/horisont.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dagsljus/horisont.py"
            },
            {
              "text": "src/dagsljus/matt.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dagsljus/matt.py"
            },
            {
              "text": "src/dagsljus/solposition.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dagsljus/solposition.py"
            },
            {
              "text": "src/dagsljus/floden_losare.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dagsljus/floden_losare.py"
            },
            {
              "text": "src/dagsljus/ruta.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/dagsljus/ruta.py"
            }
          ],
          "kontrakt": [
            "K1 (v2)",
            "K11",
            "K21 (lösarna dagsljus.*)"
          ],
          "facit": [
            {
              "text": "testsvit grupp E, E1–E7 (`floden testsvit --grupp E`)",
              "href": null
            },
            {
              "text": "tests/dagsljus/test_horisont.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dagsljus/test_horisont.py"
            },
            {
              "text": "tests/dagsljus/test_matt.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dagsljus/test_matt.py"
            },
            {
              "text": "tests/dagsljus/test_losare.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dagsljus/test_losare.py"
            }
          ],
          "anmarkning": "Fri plan ger VSC 39,6 % och SVF 1 (E1). Horisonten räknas med strålgenomgång över pelarceller (referens, riktningsruta och max-pyramid), inte med Dozier–Frew."
        },
        {
          "id": "dagsljus.dozier_frew",
          "namn": "Dozier–Frew: horisont i linjär tid",
          "sammanfattning": "Exakt 1D-horisont längs en stråle med övre konvext hölje (monoton stack), O(N) oberoende av sökradien.",
          "formler": [
            "h_i = max över j > i av atan((z_j − z_i) / d_ij), beräknad i O(N)"
          ],
          "flode": "api",
          "flodeText": "Bara i API:t",
          "status": "skiss",
          "kod": [
            {
              "text": "src/dagsljus/intervall.py (horisont_1d_konvext_holje)",
              "href": "https://github.com/joelberring/floden/blob/main/src/dagsljus/intervall.py"
            },
            {
              "text": "src/webb/api.py (POST /api/analys/svf-intervall)",
              "href": "https://github.com/joelberring/floden/blob/main/src/webb/api.py"
            }
          ],
          "kontrakt": [
            "K41"
          ],
          "facit": [
            {
              "text": "tests/dagsljus/test_modern_matte.py (maxavvikelse < 1e-14 mot O(N²))",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dagsljus/test_modern_matte.py"
            }
          ],
          "anmarkning": "Används bara för den 1D-horisontprofil som svf-intervall returnerar; huvudberäkningen av horisonten (ovan) använder den inte."
        },
        {
          "id": "dagsljus.svf_intervall",
          "namn": "Monotona SVF-intervall",
          "sammanfattning": "SVF är antiton i byggnadshöjd: körningar med z − σ och z + σ ger garanterat [SVF_min, SVF_max] per cell.",
          "formler": [
            "SVF(H − σ) ≥ SVF(H) ≥ SVF(H + σ)"
          ],
          "flode": "api",
          "flodeText": "Bara i API:t",
          "status": "skiss",
          "kod": [
            {
              "text": "src/dagsljus/intervall.py (kor_monoton_svf_intervall)",
              "href": "https://github.com/joelberring/floden/blob/main/src/dagsljus/intervall.py"
            },
            {
              "text": "src/webb/api.py (POST /api/analys/svf-intervall)",
              "href": "https://github.com/joelberring/floden/blob/main/src/webb/api.py"
            }
          ],
          "kontrakt": [
            "K41"
          ],
          "facit": [
            {
              "text": "tests/dagsljus/test_modern_matte.py (20 slumpade höjdscenarier inneslutna)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/dagsljus/test_modern_matte.py"
            },
            {
              "text": "tests/webb/test_analys_steg_a.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/webb/test_analys_steg_a.py"
            }
          ],
          "anmarkning": "Ingen panel i klienten."
        }
      ]
    },
    {
      "id": "luft",
      "namn": "Luft",
      "metoder": [
        {
          "id": "luft.transport",
          "namn": "Vindfält och spridning som M-matris",
          "sammanfattning": "Stationär advektion–diffusion–deposition på voxelnät som ett glest linjärt system; snabbvind ger ett divergensfritt skissfält med Poisson-projektion.",
          "formler": [
            "∇·(u c) − ∇·(K ∇c) + λ c = s  ⇒  L c = s, L M-matris (L⁻¹ ≥ 0)"
          ],
          "flode": "cli",
          "flodeText": "Kommandorad, MCP och testsvit",
          "status": "skiss",
          "kod": [
            {
              "text": "src/luft/transport.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/luft/transport.py"
            },
            {
              "text": "src/luft/snabbvind.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/luft/snabbvind.py"
            },
            {
              "text": "src/luft/lbm.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/luft/lbm.py"
            },
            {
              "text": "src/luft/vindkomfort.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/luft/vindkomfort.py"
            },
            {
              "text": "src/karna/luft_losare.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/karna/luft_losare.py"
            }
          ],
          "kontrakt": [
            "K21 (lösarna luft.halt, luft.vindkomfort)"
          ],
          "facit": [
            {
              "text": "tests/luft/test_transport.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/luft/test_transport.py"
            },
            {
              "text": "tests/luft/test_transport_tvd.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/luft/test_transport_tvd.py"
            },
            {
              "text": "tests/luft/test_lbm_validering.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/luft/test_lbm_validering.py"
            },
            {
              "text": "docs/LUFT.md avsnitt 6 (L1–L8)",
              "href": "https://github.com/joelberring/floden/blob/main/docs/LUFT.md"
            }
          ],
          "anmarkning": "M-matrisen (alla element utanför diagonalen ≤ 0, kolumnvis diagonaldominant) gäller för uppströmsschemat och för TVD med `m_matris_tak = true` (standard). Lösarna finns i K21 men luft är inte ett ämne med beräkning i webbflödet. Licens MIT (src/luft/)."
        },
        {
          "id": "luft.adjungerad",
          "namn": "Adjungerad känslighet och åtgärdsval",
          "sammanfattning": "En adjungerad lösning ger känsligheten hos mottagarhalten för varje källa och för vegetationen, i stället för en körning per cell.",
          "formler": [
            "J = wᵀc, Lᵀλ = w  ⇒  ∂J/∂s_i = λ_i,  ∂J/∂LAD_i = −v_veg·Δx³·λ_i·c_i"
          ],
          "flode": "fristaende",
          "flodeText": "Fristående modul",
          "status": "skiss",
          "kod": [
            {
              "text": "src/luft/kanslighet.py (berakna_adjungerad_kanslighet, valj_basta_luftatgarder_k)",
              "href": "https://github.com/joelberring/floden/blob/main/src/luft/kanslighet.py"
            }
          ],
          "kontrakt": [],
          "facit": [
            {
              "text": "tests/luft/test_kanslighet.py (dualitetsfel < 1e-12, vegetationsgradient mot finit differens, relativt 1e-3)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/luft/test_kanslighet.py"
            }
          ],
          "anmarkning": "Inte inkopplad i något flöde eller API (git grep 2026-11-23): bara kor_monoton_mkn_intervall i samma fil används av API:t. Beskrivs som 'redo för 3D-luftflödet' i modulens docstring."
        },
        {
          "id": "luft.mkn_intervall",
          "namn": "Monotona MKN-intervall",
          "sammanfattning": "Halten är monoton i emission och antiton i deposition; två extremserier av en färdig timserie ger ett garanterat intervall för dygn över 50 µg/m³ (MKN: högst 35 dygn per år).",
          "formler": [
            "c_lo = c_bg + (e_lo/d_hi)·c_lokal,  c_hi = c_bg + (e_hi/d_lo)·c_lokal; dygn över 50 µg/m³ räknas i båda serierna"
          ],
          "flode": "api",
          "flodeText": "Bara i API:t",
          "status": "skiss",
          "kod": [
            {
              "text": "src/luft/kanslighet.py (kor_monoton_mkn_intervall)",
              "href": "https://github.com/joelberring/floden/blob/main/src/luft/kanslighet.py"
            },
            {
              "text": "src/webb/api.py (POST /api/analys/luft-mkn)",
              "href": "https://github.com/joelberring/floden/blob/main/src/webb/api.py"
            }
          ],
          "kontrakt": [
            "K43"
          ],
          "facit": [
            {
              "text": "tests/luft/test_kanslighet.py (50 MKN-scenarier inneslutna)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/luft/test_kanslighet.py"
            },
            {
              "text": "tests/webb/test_analys_steg_b.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/webb/test_analys_steg_b.py"
            }
          ],
          "anmarkning": "Skalar en given lokal timserie med emissionsfaktor/depositionsfaktor; det är ingen ny transportlösning. Ingen panel i klienten."
        }
      ]
    },
    {
      "id": "buller",
      "namn": "Buller",
      "metoder": [
        {
          "id": "buller.nord2000",
          "namn": "Nord2000 referenskärna",
          "sammanfattning": "Punkt-till-punkt-överföring enligt Nord2000 (delmodell 1–9: plan mark, terräng, skärmar, refraktion) med operatorlager och klustring med felgräns.",
          "formler": [
            "L[m,b] = 10·lg Σ_v w_v Σ_banor 10^((L_W[källa,b] + H[bana,v,b]) / 10)   (KB2; energisumma över banor och väderklasser)"
          ],
          "flode": "cli",
          "flodeText": "Kommandorad, MCP och testsvit",
          "status": "skiss",
          "kod": [
            {
              "text": "src/buller/nord2000/",
              "href": "https://github.com/joelberring/floden/tree/main/src/buller/nord2000"
            },
            {
              "text": "src/buller/operator/",
              "href": "https://github.com/joelberring/floden/tree/main/src/buller/operator"
            },
            {
              "text": "src/buller/lagen/",
              "href": "https://github.com/joelberring/floden/tree/main/src/buller/lagen"
            },
            {
              "text": "src/buller/scen/",
              "href": "https://github.com/joelberring/floden/tree/main/src/buller/scen"
            },
            {
              "text": "src/buller/banor/",
              "href": "https://github.com/joelberring/floden/tree/main/src/buller/banor"
            }
          ],
          "kontrakt": [
            "KB1",
            "KB2",
            "KB3",
            "KB4 (docs/buller/KONTRAKT.md)"
          ],
          "facit": [
            {
              "text": "tests/buller/nord2000/",
              "href": "https://github.com/joelberring/floden/tree/main/tests/buller/nord2000"
            },
            {
              "text": "tests/buller/test_helkedja.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/buller/test_helkedja.py"
            },
            {
              "text": "testsvit grupp N (RL 18-18, 80 fall, kräver lokal källmapp)",
              "href": null
            }
          ],
          "anmarkning": "Pågår: `nord2000_konform = False`, rapportläget spärrat. Grupp N 2026-11-21 (docs/buller/logg/P-S2.md): 33 av 80 godkända, 16 ska utredas, 4 underkända, 27 ej körbara. Ej i webbflödet. Newton–Raphson används för krökta strålar (stralar_krokta.py), inte för skärmdiffraktionen."
        },
        {
          "id": "buller.celf",
          "namn": "CELF: submodulärt val av åtgärder",
          "sammanfattning": "Girigt val av de k mest effektiva åtgärderna (bullerskärmar, kritiska gatuavsnitt, luft- och ekologiåtgärder) med lat utvärdering och garantin (1 − 1/e) mot optimum; skuggzonsdetektor för mottagare där Nord2000-dämpningen hoppar.",
          "formler": [
            "f(S) = Σ_i w_i·min(max(0, L_i − L_rikt), max_(s∈S) ΔL_(i,s));  f(S_CELF) ≥ (1 − 1/e)·f(S*), |S| ≤ k",
            "g_krit = −c₀·(√(2h_s) + √(2h_r))² / d²   (kritisk ljudhastighetsgradient för skuggzon)"
          ],
          "flode": "api",
          "flodeText": "Bara i API:t",
          "status": "skiss",
          "kod": [
            {
              "text": "src/buller/optimering.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/buller/optimering.py"
            },
            {
              "text": "src/rutt/kritiska.py (valj_kritisk_insats_k)",
              "href": "https://github.com/joelberring/floden/blob/main/src/rutt/kritiska.py"
            },
            {
              "text": "src/luft/kanslighet.py (valj_basta_luftatgarder_k)",
              "href": "https://github.com/joelberring/floden/blob/main/src/luft/kanslighet.py"
            },
            {
              "text": "src/ekologi/intervall.py (valj_basta_ekologi_atgarder_k)",
              "href": "https://github.com/joelberring/floden/blob/main/src/ekologi/intervall.py"
            },
            {
              "text": "src/webb/api.py (POST /api/analys/buller-skarmar, POST /api/rutt)",
              "href": "https://github.com/joelberring/floden/blob/main/src/webb/api.py"
            }
          ],
          "kontrakt": [
            "K42",
            "K8"
          ],
          "facit": [
            {
              "text": "tests/buller/test_optimering.py (matchar brute force, k = 3)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/buller/test_optimering.py"
            },
            {
              "text": "tests/rutt/test_kritiska.py (brute force; 400 noder och 760 kanter under 0,05 s)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/rutt/test_kritiska.py"
            },
            {
              "text": "tests/ekologi/test_facit_g.py (G6: CELF väljer ekodukt först)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_facit_g.py"
            },
            {
              "text": "tests/luft/test_kanslighet.py",
              "href": "https://github.com/joelberring/floden/blob/main/tests/luft/test_kanslighet.py"
            }
          ],
          "anmarkning": "Bara buller- och ruttvarianten nås via API (buller-skarmar, rutt med antal_insatser); luft- och ekologivarianten är fristående funktioner. Skuggzonsdetektorn är en geometrisk tumregel, inte en del av Nord2000-kärnan. 1,25 ms för 400 noder och 760 kanter vid k = 5 är en enstaka mätning ur STATUS 2026-11-22; testet kräver bara under 50 ms."
        }
      ]
    },
    {
      "id": "ekologi",
      "namn": "Ekologi",
      "metoder": [
        {
          "id": "ekologi.krets",
          "namn": "Kretsteoretisk konnektivitet",
          "sammanfattning": "Landskapet som elektriskt nät: harmoniska kantkonduktanser, Dirichlet-låsning av käll- och målfläck, effektivt motstånd, strömtäthet (flaskhalsar) och exakt känslighet med en lösning.",
          "formler": [
            "G_uv = 2·g_u·g_v / ((g_u + g_v)·ℓ_uv), g = 1/R;  R_eff = 1 / I_tot;  L_II symmetrisk positivt definit M-matris",
            "∂R_eff/∂G_uv = −((v_u − v_v) / I_tot)² ≤ 0   (en lösning, ingen extra körning per kant)"
          ],
          "flode": "flode",
          "flodeText": "I användarflödet",
          "status": "skiss",
          "kod": [
            {
              "text": "src/ekologi/krets.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/ekologi/krets.py"
            },
            {
              "text": "src/ekologi/motstand.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/ekologi/motstand.py"
            },
            {
              "text": "src/ekologi/habitat.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/ekologi/habitat.py"
            },
            {
              "text": "src/karna/ekologi_losare.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/karna/ekologi_losare.py"
            }
          ],
          "kontrakt": [
            "K44",
            "K45"
          ],
          "facit": [
            {
              "text": "tests/ekologi/test_facit_g.py (G1–G3, G5)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_facit_g.py"
            },
            {
              "text": "tests/ekologi/test_losare_k21.py (G7)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_losare_k21.py"
            }
          ],
          "anmarkning": "Skiss: artprofilernas parametrar saknar exakta källhänvisningar och markslagsöversättningen är egna val (docs/EKOLOGI.md)."
        },
        {
          "id": "ekologi.pc_eca",
          "namn": "PC, ECA och dPC",
          "sammanfattning": "Kostnadsavstånd med multi-source Dijkstra, spridningssannolikhet mellan fläckar, Probability of Connectivity, Equivalent Connected Area och Saura–Rubio-dekomposition (intra, flux, connector).",
          "formler": [
            "p*_ij = exp(−α·d*_ij), α = −ln p₀ / d_disp;  PC = Σ a_i·a_j·p*_ij / A_L²;  ECA = A_L·√PC"
          ],
          "flode": "flode",
          "flodeText": "I användarflödet",
          "status": "skiss",
          "kod": [
            {
              "text": "src/ekologi/konnektivitet.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/ekologi/konnektivitet.py"
            },
            {
              "text": "src/ekologi/typer.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/ekologi/typer.py"
            },
            {
              "text": "src/webb/ekologi_analys.py (POST /api/analys/ekologi)",
              "href": "https://github.com/joelberring/floden/blob/main/src/webb/ekologi_analys.py"
            }
          ],
          "kontrakt": [
            "K44",
            "K45"
          ],
          "facit": [
            {
              "text": "tests/ekologi/test_facit_g.py (G4: exakt PC, ECA och dPC för tre fläckar)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_facit_g.py"
            },
            {
              "text": "tests/ekologi/test_losare_k21.py (G7)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_losare_k21.py"
            }
          ],
          "anmarkning": "Före/efter i webbflödet (fliken Ekologi i Resultat). Sannolikhetsfunktionen är exponentiell med p0 = 0,5 vid spridningsavståndet."
        },
        {
          "id": "ekologi.intervall",
          "namn": "Monotona osäkerhetsintervall",
          "sammanfattning": "ECA och PC är antitona i motståndet och monotona i spridningsavståndet; två extremkörningar ger ett garanterat intervall över litteraturosäkerheten.",
          "formler": [
            "ECA(R_max, d_min) ≤ ECA ≤ ECA(R_min, d_max)"
          ],
          "flode": "flode",
          "flodeText": "I användarflödet",
          "status": "skiss",
          "kod": [
            {
              "text": "src/ekologi/intervall.py (kor_monoton_ekologi_intervall)",
              "href": "https://github.com/joelberring/floden/blob/main/src/ekologi/intervall.py"
            },
            {
              "text": "src/karna/ekologi_losare.py",
              "href": "https://github.com/joelberring/floden/blob/main/src/karna/ekologi_losare.py"
            }
          ],
          "kontrakt": [
            "K45"
          ],
          "facit": [
            {
              "text": "tests/ekologi/test_facit_g.py (G6: 50 slumpade scenarier, alla inneslutna)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_facit_g.py"
            },
            {
              "text": "tests/ekologi/test_losare_k21.py (G7: intervallen innesluter nominellt värde)",
              "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_losare_k21.py"
            }
          ],
          "anmarkning": "Intervallens faktorer är egna val utan källa (STATUS 2026-11-23). Motståndet skalas med en gemensam faktor, inte cell för cell."
        }
      ]
    }
  ],
  "fragor": [
    {
      "id": "Q1",
      "titel": "Exakt händelsestyrd fyllning, min-plus-algebra och avtappning",
      "kort": "När är fyllningstiderna i sänkträdet exakt beräkningsbara i min-plus-semiringen, hur bryts exaktheten av olinjära överfall (Q ∝ Δh^(3/2)), och finns en exakt eller garanterat konservativ formulering när avtappningen följer den våta ytan A(h)?",
      "underlag": [
        {
          "text": "tests/dynamik/test_a_fall_dynamik.py",
          "href": "https://github.com/joelberring/floden/blob/main/tests/dynamik/test_a_fall_dynamik.py"
        },
        {
          "text": "testsvit A9–A13",
          "href": null
        },
        {
          "text": "docs/MATEMATIK.md (observationer 2026-11-20 och 2026-11-22)",
          "href": "https://github.com/joelberring/floden/blob/main/docs/MATEMATIK.md"
        }
      ]
    },
    {
      "id": "Q2",
      "titel": "Flerparameterpersistens under höjdosäkerhet",
      "kort": "Kan sänksystemets robusthet formaliseras som tvåparameterpersistens över höjd och osäkerhet (minimala fria upplösningar, Hilbertfunktioner, Betti-tal), och kan en persistensförenkling av en DEM ges en bevisbar felgräns i maxdjup?",
      "underlag": [
        {
          "text": "tools/exportera_q2_underlag.py",
          "href": "https://github.com/joelberring/floden/blob/main/tools/exportera_q2_underlag.py"
        },
        {
          "text": "src/sankgraf/bifurkation.py (exportera_bifiltrering_json)",
          "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/bifurkation.py"
        },
        {
          "text": "docs/DATAKONTRAKT.md (K35, K34)",
          "href": "https://github.com/joelberring/floden/blob/main/docs/DATAKONTRAKT.md"
        }
      ]
    },
    {
      "id": "Q3",
      "titel": "Fel i hybriden",
      "kort": "Hur stort är felet när djupa samlingar räknas som noder med plan vattenyta och resten i 2D, jämfört med full 2D, särskilt under pågående kraftigt överfall över en smal tröskel?",
      "underlag": [
        {
          "text": "src/tvad/hybrid.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/tvad/hybrid.py"
        },
        {
          "text": "src/tvad/felindikator.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/tvad/felindikator.py"
        },
        {
          "text": "testsvit EA2 och EA8A",
          "href": null
        },
        {
          "text": "docs/VALIDERING.md avsnitt C",
          "href": "https://github.com/joelberring/floden/blob/main/docs/VALIDERING.md"
        }
      ]
    },
    {
      "id": "Q4",
      "titel": "Inkrementell omräkning",
      "kort": "Vilka datastrukturer gör att en ändring bara kräver omräkning av berörda delträd och det som ligger nedströms, och finns en lokal regel för när ett nytt hus stänger en port eller skapar en slutsänka?",
      "underlag": [
        {
          "text": "src/sankgraf/hierarki.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/sankgraf/hierarki.py"
        },
        {
          "text": "src/lapp/",
          "href": "https://github.com/joelberring/floden/tree/main/src/lapp"
        },
        {
          "text": "tests/sankgraf/test_instangt_omrade.py",
          "href": "https://github.com/joelberring/floden/blob/main/tests/sankgraf/test_instangt_omrade.py"
        },
        {
          "text": "docs/MATEMATIK.md (observationer T04 och 2026-11-22)",
          "href": "https://github.com/joelberring/floden/blob/main/docs/MATEMATIK.md"
        }
      ]
    },
    {
      "id": "Q5",
      "titel": "Fysikbevarande modellreduktion för 2D i realtid",
      "kort": "Kan en masskonserverande reducerad modell (POD eller Reduced Basis) för 2D-delzoner, med känd a posteriori-felgräns, ge realtidssvar för kritiska gator utan svart-låda-AI (AGENTS.md regel 1)?",
      "underlag": [
        {
          "text": "src/tvad/lokal_troghet.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/tvad/lokal_troghet.py"
        },
        {
          "text": "docs/benchmark/README.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/benchmark/README.md"
        },
        {
          "text": "docs/WEBGPU-ARKITEKTUR.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/WEBGPU-ARKITEKTUR.md"
        }
      ]
    }
  ],
  "avvikelser": [
    {
      "id": "avv.buller.medvind_dal",
      "doman": "buller",
      "domanNamn": "Buller",
      "kategori": "metodtolkning",
      "kategoriText": "Metodtolkning",
      "titel": "Nord2000: medvind över dal (3 underkända fall)",
      "omfattning": "RL 18-18: N18R052, N18R062, N18R102 (avvikelse +2,8 till +6,0 dB i totalnivå, upp till +7,3 dB i 0,8–2,5 kHz)",
      "varfor": "Med källhöjden 0,01 m (däckbuller) ger bokstavlig tillämpning av CalcEqSSP i AV 1106/07 ekv. (134)–(135) en mycket brant ekvivalent ljudhastighetsprofil i medvind. Vägbanans Fresnelzon krymper då från ca 52 m till ca 2 m och tar nästan hela vikten. Metodtexten anger ingen minimihöjd för segmentens Fresnelzon; vi anpassar aldrig konstanter mot testfall utan stöd i specifikationen.",
      "kravs": "Extern granskning och klargörande från Kunskapscentrum om buller om vad referensprogrammet gör för låga källhöjder i delmodell 3.",
      "underlag": [
        {
          "text": "docs/buller/logg/P-S2.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/buller/logg/P-S2.md"
        },
        {
          "text": "docs/buller/tolkningar.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/buller/tolkningar.md"
        },
        {
          "text": "src/buller/nord2000/delmodell3.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/buller/nord2000/delmodell3.py"
        }
      ]
    },
    {
      "id": "avv.buller.motvind_terrang",
      "doman": "buller",
      "domanNamn": "Buller",
      "kategori": "metodtolkning",
      "kategoriText": "Metodtolkning",
      "titel": "Nord2000: motvind över bruten terräng (5 ej körbara fall)",
      "omfattning": "RL 18-18: N18R033, N18R043, N18R053, N18R063, N18R103",
      "varfor": "I skuggzon ger FresnelZoneW (AV 1106/07 ekv. 347–348) strykvinkeln ψ_G = 0, vilket gör att även platta eller konkava terrängsegment klassas som konvexa i delmodell 3. Då krävs en ekvivalent kil som geometriskt inte går att bilda (parallella ytor eller kilvinkel β < π). Kärnan stoppar då med ett tydligt fel i stället för att gissa ett resultat.",
      "kravs": "Klargörande i metodtexten (eller hos Kunskapscentrum om buller) om segmentklassningen i delmodell 3 när mottagaren ligger i motvindens skuggzon.",
      "underlag": [
        {
          "text": "docs/buller/logg/P-S2.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/buller/logg/P-S2.md"
        },
        {
          "text": "docs/buller/tolkningar.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/buller/tolkningar.md"
        },
        {
          "text": "src/buller/nord2000/delmodell3.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/buller/nord2000/delmodell3.py"
        }
      ]
    },
    {
      "id": "avv.buller.kulle_och_bank",
      "doman": "buller",
      "domanNamn": "Buller",
      "kategori": "metodtolkning",
      "kategoriText": "Metodtolkning",
      "titel": "Nord2000: mjuk kulle och upphöjd väg i enskilda tersband",
      "omfattning": "RL 18-18: kulle N18R111–114 (total ≤ 0,70 dB, tersband 3,8–5,1 dB i 200–315 Hz) samt upphöjd väg N18R031–32, N18R041–42 (total ≤ 0,26 dB, tersband 1,8–3,1 dB över 2,5 kHz)",
      "varfor": "Totalnivån ligger redan nära gränsen 0,5 dB, men enskilda tersband avviker över 1 dB. På hård tjock skärm (N18R081) stämmer delmodell 5 väl, men på mjuk, flack kulle (σ = 200 kNs/m⁴, β ≈ 185°) hamnar 200–315 Hz-dippen för högt. På upphöjd väg ger den vägda kilytereflektionen i TC-101327 bilaga G.2 stark spegling i vägbanan för däckkällan (0,01 m) vid höga frekvenser.",
      "kravs": "Fortsatt granskning av TC-101327 bilaga G.2–G.3 och delmodell 5 mot metodtext och extern metodgranskning.",
      "underlag": [
        {
          "text": "docs/buller/logg/P-S2.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/buller/logg/P-S2.md"
        },
        {
          "text": "src/buller/nord2000/delmodell4_kilben.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/buller/nord2000/delmodell4_kilben.py"
        },
        {
          "text": "src/buller/nord2000/delmodell5.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/buller/nord2000/delmodell5.py"
        }
      ]
    },
    {
      "id": "avv.buller.ej_korbara_typer",
      "doman": "buller",
      "domanNamn": "Buller",
      "kategori": "korare_format",
      "kategoriText": "Körare och format",
      "titel": "Nord2000: kurvig väg, stadsgata, årsmedel och skog (22 ej körbara fall)",
      "omfattning": "RL 18-18: kurvig väg (10 fall), stadsgata (4 fall), årsmedel Lden (4 fall) och skog (4 fall, N18R121–124)",
      "varfor": "Dessa fall saknar inte grundfysik i utbredningskärnan, utan begränsas av indataformat, testkörare och beslutade avgränsningar. Testformatet N-fall/v1 beskriver bara raka tvärsnitt och saknar plangeometri för kurvig väg och stadsgata (delmodell 11 finns redan implementerad). Årsmedel saknar väderklassviktning (Lden) i köraren. Skog (delmodell 10) kräver ny kontraktsversion av Terrangprofil (KB1) och är medvetet avstängt i rapportläget.",
      "kravs": "Nytt testformat N-fall/v2 med plangeometri och väderstatistik, körarstöd för Lden och reflexer, samt eventuell kontraktsversion KB1/v2 för skog.",
      "underlag": [
        {
          "text": "docs/buller/logg/P-11.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/buller/logg/P-11.md"
        },
        {
          "text": "docs/BESLUT.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/BESLUT.md"
        },
        {
          "text": "src/buller/testfall/format.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/buller/testfall/format.py"
        },
        {
          "text": "src/buller/testfall/korare.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/buller/testfall/korare.py"
        }
      ]
    },
    {
      "id": "avv.dagsljus.webgpu_svf",
      "doman": "dagsljus",
      "domanNamn": "Dagsljus",
      "kategori": "numerik",
      "kategoriText": "Numerik / GPU",
      "titel": "Dagsljus: WebGPU SVF i enkelprecision (f32)",
      "omfattning": "macOS Apple Metal-3 benchmark 2026-11-23: största cellavvikelse 0,0126 (0,014 % av cellerna över 0,01)",
      "varfor": "GPU-beräkningen använder 32-bitars flyttal (WGSL f32), där upprepade flyttalssteg längs exakt diagonala strålar tidigare kunde driva ett steg fel i enstaka celler. Med heltalsräknare och korsprodukt-DDA i dagsljus_horisont.wgsl har den diagonala ULP-driften eliminerats mot Python-referensen; kvarvarande skillnad ryms inom f32-avrundning.",
      "kravs": "Uppdaterad macOS WebGPU-mätning som verifierar den nya maxavvikelsen efter ändringen i shadern.",
      "underlag": [
        {
          "text": "webb/src/gpu/shaders/dagsljus_horisont.wgsl",
          "href": "https://github.com/joelberring/floden/blob/main/webb/src/gpu/shaders/dagsljus_horisont.wgsl"
        },
        {
          "text": "tests/test_wgsl_referens.py",
          "href": "https://github.com/joelberring/floden/blob/main/tests/test_wgsl_referens.py"
        },
        {
          "text": "docs/benchmark/2026-11-23-mac-webgpu.json",
          "href": "https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-mac-webgpu.json"
        }
      ]
    },
    {
      "id": "avv.vatten.overfall_och_hybrid",
      "doman": "vatten",
      "domanNamn": "Vatten",
      "kategori": "fysikavgransning",
      "kategoriText": "Fysikavgränsning",
      "titel": "Vatten: omedelbart överfall i sänkgrafen och 2D-hybridens räckvidd",
      "omfattning": "A2–A3: +1,1 % till +2,1 % tidsfördröjning med bredskronsöverfall; hybridens uppsnabbning 1,2–1,8× efter rättelser i vattenkärnan",
      "varfor": "Den snabba händelsestyrda grafmodellen förutsätter omedelbart överfall (kinematisk gräns) och plan vattenyta i varje sänka. Vid breda, grunda överfall eller höga hastigheter (Fr ≥ 0,30) krävs 2D lokal tröghet, vilket felindikatorn flaggar automatiskt. Efter rättelserna i vattenkärnan 2026-11-23 är hybriden 1,2–1,8× snabbare än full 2D (inte de tidigare 12,9–20×).",
      "kravs": "Fortsatt analys i de öppna forskningsfrågorna Q1 (olinjära överfall) och Q3 (hybridfel under pågående överfall).",
      "underlag": [
        {
          "text": "src/tvad/felindikator.py",
          "href": "https://github.com/joelberring/floden/blob/main/src/tvad/felindikator.py"
        },
        {
          "text": "docs/VALIDERING.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/VALIDERING.md"
        },
        {
          "text": "docs/BESLUT.md",
          "href": "https://github.com/joelberring/floden/blob/main/docs/BESLUT.md"
        }
      ]
    }
  ],
  "flodeText": {
    "flode": "I användarflödet",
    "karta": "I kartsidan (#/karta), inte i flödet",
    "api": "Bara i API:t",
    "cli": "Kommandorad, MCP och testsvit",
    "fristaende": "Fristående modul"
  },
  "kodbas": "https://github.com/joelberring/floden/blob/main/docs/MATEMATIK.md"
};
