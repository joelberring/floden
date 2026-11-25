// GENERERAD av tools/generera_om.py ur data/om/floden.toml, data/matematik/metoder.toml
// och data/mcp/metodstatus.toml.
// Redigera inte för hand; kör `python tools/generera_om.py`.
import type { OmData } from "./typer.ts";

export const OM: OmData = {
  "uppdaterad": "2026-11-25",
  "ingress": "Floden är en öppen modell som räknar skyfall, dagsljus och sol, ekologi och luft på gatunivå, före och efter ett förslag. Buller finns som metodkärna under utveckling. Floden räknar och språkmodellen äger aldrig ett tal: varje tal har belägg i en beräkning. Den här sidan säger vad Floden kan nu, vad planen är och hur Floden förhåller sig till andra verktyg, med källa för varje uppgift.",
  "statusText": "Alla metoder är skisser: ingen metod är klassad som rapportmetod av en människa.",
  "jamforelseIngress": "Sidan skiljer strikt på tre saker: det som är uppmätt i Floden, det som andra verktygs tillverkare själva har publicerat, och det som inte är mätt. Det finns ingen mätning av samma fall i Floden och i något annat verktyg.",
  "kodbas": "https://github.com/joelberring/floden/blob/main/docs/OM-FLODEN.md",
  "metodAntal": {
    "alla": 16,
    "skiss": 16,
    "rapport": 0
  },
  "steg": [
    {
      "id": "geografi",
      "namn": "Geografi",
      "text": "En kommun är en konfigurationsfil (`data/kommuner/<id>.toml`) med källor och licenser. `floden geografi mappa` hämtar Lantmäteriets öppna data eller kommunens filpaket och bygger kärna och buffert som innehållsadresserade lager. Göteborg är testkommun; Stockholm kommer efter mätningen."
    },
    {
      "id": "projekt",
      "namn": "Projekt",
      "text": "Nytt projekt väljer geografi och ritar en avgränsning (rektangel eller polygon). Roller (läsare, utredare) och organisationens delade projekt styr vem som ser vad. Demokvarteret är ett tydligt märkt påhittat projekt."
    },
    {
      "id": "forslag",
      "namn": "Förslag",
      "text": "Rita hus med takform, mark i nivåer, hårdgjort, vegetation, vatten, brytlinjer och öppningar i ritlager, med fäst, rät vinkel, ångra och pekskärm. Skarvkontrollen varnar där ny mark möter gammal. 3D-förhandsvisning och sparade förslag."
    },
    {
      "id": "fragor",
      "namn": "Frågor",
      "text": "Välj frågor ur frågekatalogen per ämne. Planen visar vad som räknas och vad som redan finns i cachen. Räkna förslaget, eller läget idag utan förslag. Beräkningen sparar ett manifest med kontrollsummor för all indata."
    },
    {
      "id": "resultat",
      "namn": "Resultat",
      "text": "Flikar per ämne med Före, Efter, Skillnad, Svep och 3D, alla analyser som kartlager, värden under pekaren och Visa osäkerhet med intervall och känsliga överfall."
    },
    {
      "id": "rapport",
      "namn": "Rapport",
      "text": "Beställning (syfte, skede, mottagare) styr frågorna och ingressen. R1 är skissunderlag för skyfall, R2 lägger till dagsljus och sol, ekologi och luft (luft via MCP). Varje tal är en platshållare med belägg; Floden fyller talen och kontrollerar alla påståenden före export till Word och PDF. Buller är spärrat."
    },
    {
      "id": "mcp",
      "namn": "MCP",
      "text": "En AI-klient (Claude Desktop, Claude Code, lokal modell) styr samma kärna: projekt, förslag, beräkning, resultat och rapport. AI:n äger aldrig ett tal; varje tal har belägg, varje anrop loggas i en körlogg och bara öppna data lämnas ut som förval."
    }
  ],
  "amnen": [
    {
      "id": "skyfall",
      "namn": "Skyfall",
      "metoder": [
        {
          "id": "vatten.sankgraf",
          "namn": "Sänkgraf som sammanslagningsträd",
          "status": "skiss",
          "flodeText": "I användarflödet"
        },
        {
          "id": "vatten.dynamik",
          "namn": "Händelsestyrd dynamik i grafen",
          "status": "skiss",
          "flodeText": "I användarflödet"
        },
        {
          "id": "vatten.tvad_hybrid",
          "namn": "2D lokal tröghet, subgrid och hybrid",
          "status": "skiss",
          "flodeText": "Kommandorad, MCP och testsvit"
        },
        {
          "id": "vatten.bifurkation",
          "namn": "Bifurkationsdetektor",
          "status": "skiss",
          "flodeText": "I användarflödet"
        },
        {
          "id": "vatten.intervall",
          "namn": "Monoton intervallanalys för vatten",
          "status": "skiss",
          "flodeText": "I användarflödet"
        }
      ],
      "kan": [
        "Var vattnet samlas och hur djupt vid ett givet regn, som statisk fyllning av terrängens sänkor, med takvatten och avtappning som uttryckliga parametrar.",
        "Djup över tid i sänkorna utan tidsstegning (händelsestyrd dynamik), för många regn.",
        "Före och efter ett ritat förslag, där bara det som berörs räknas om.",
        "Osäkerhetsintervall och känsliga överfall (bifurkationer), som visas i resultatkartan.",
        "Skarvkontroll: varnar där ny mark ger nya eller djupare sänkor vid kanten.",
        "2D lokal tröghet och hybrid mot sänkgrafen, för kommandoraden och MCP (inte som knapp i webben)."
      ],
      "testat": [
        {
          "text": "Testsvit grupp A (A1 till A13, syntetiska fall med känt svar) och Environment Agency-fallen EA1, EA2 och EA8A samt Hunters analytiska fall D1",
          "kalla": {
            "text": "docs/VALIDERING.md",
            "href": "https://github.com/joelberring/floden/blob/main/docs/VALIDERING.md"
          }
        },
        {
          "text": "Hela facittestsviten för alla ämnen (`floden testsvit`), godkända fall",
          "godkanda": 17,
          "av": 17,
          "kalla": {
            "text": "STATUS.md",
            "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
          }
        }
      ],
      "nyckeltal": [],
      "begransningar": [
        "Skiss: ingen metod är klassad som rapportmetod.",
        "Inget ledningsnät är kopplat. Ledningsnät i 1D och radarnederbörd ligger i planen.",
        "Provat mot syntetiska kvarter och en påhittad geografi, inte mot riktiga data på servern.",
        "Det snabba grafspåret förutsätter omedelbart överfall och plan vattenyta i varje sänka; felindikatorn flaggar där 2D behövs."
      ]
    },
    {
      "id": "dagsljus",
      "namn": "Dagsljus och sol",
      "metoder": [
        {
          "id": "dagsljus.horisont_svf_vsc",
          "namn": "Horisont, soltimmar, SVF och VSC",
          "status": "skiss",
          "flodeText": "I användarflödet"
        },
        {
          "id": "dagsljus.dozier_frew",
          "namn": "Dozier–Frew: horisont i linjär tid",
          "status": "skiss",
          "flodeText": "Bara i API:t"
        },
        {
          "id": "dagsljus.svf_intervall",
          "namn": "Monotona SVF-intervall",
          "status": "skiss",
          "flodeText": "I användarflödet"
        }
      ],
      "kan": [
        "Soltimmar på marken, himmelsandel (SVF) och vertikal himmelskomponent (VSC) på fasadpunkter enligt BRE.",
        "Före och efter ett förslag, med intervall för SVF.",
        "Kartor, 3D-vy med VSC på fasader och skuggstudier.",
        "WebGPU-motor i webbläsaren med reservväg på CPU; serverns f64-beräkning är facit."
      ],
      "testat": [
        {
          "text": "Testsvit grupp E (E1 till E7), godkända fall",
          "godkanda": 7,
          "av": 7,
          "kalla": {
            "text": "STATUS.md",
            "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
          }
        },
        {
          "text": "Solposition mot NREL SPA (pvlib, 2000 till 2040): största avvikelse i höjd",
          "varde": 0.013,
          "enhet": "grader",
          "decimaler": 3,
          "kalla": {
            "text": "STATUS.md",
            "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
          }
        }
      ],
      "nyckeltal": [],
      "begransningar": [
        "Skiss: ingen metod är klassad som rapportmetod.",
        "Dagsljusfaktor i typrum (DF) och reflekterat ljus finns inte; Radiance-referens saknas. Se planen.",
        "WebGPU räknar i enkelprecision och SVF-fältet ligger i ett fåtal celler över toleransen (se mätningarna)."
      ]
    },
    {
      "id": "ekologi",
      "namn": "Ekologi",
      "metoder": [
        {
          "id": "ekologi.krets",
          "namn": "Kretsteoretisk konnektivitet",
          "status": "skiss",
          "flodeText": "I användarflödet"
        },
        {
          "id": "ekologi.pc_eca",
          "namn": "PC, ECA och dPC",
          "status": "skiss",
          "flodeText": "I användarflödet"
        },
        {
          "id": "ekologi.intervall",
          "namn": "Monotona osäkerhetsintervall",
          "status": "skiss",
          "flodeText": "I användarflödet"
        }
      ],
      "kan": [
        "Konnektivitet i ett habitatnätverk för signalarter: kretsteoretisk strömtäthet, sannolikhet för konnektivitet (PC), ekvivalent anslutet område (ECA) och bidrag per habitatyta (dPC).",
        "Nätverk som konfiguration (`data/ekologi/natverk/`) med källa och sida per värde; NMD- och VMI-lager som valfria lager i geografin.",
        "Före och efter ett förslag, med källbaserat intervall."
      ],
      "testat": [
        {
          "text": "Analytiska facitfall G1 till G6 (`tests/ekologi/test_facit_g.py`) och lösaren i K21 (G7)",
          "kalla": {
            "text": "tests/ekologi/test_facit_g.py",
            "href": "https://github.com/joelberring/floden/blob/main/tests/ekologi/test_facit_g.py"
          }
        }
      ],
      "nyckeltal": [],
      "begransningar": [
        "Skiss: biologisk säkerhet är låg och en del profilparametrar saknar källa.",
        "Resultatet kallas potentiell livsmiljö, okänd kvalitet, och är inte verifierat mot fältdata.",
        "NMD och VMI är inte körda mot riktiga filer.",
        "I kartverktyget finns ekologi bara för demokvarteret."
      ]
    },
    {
      "id": "luft",
      "namn": "Luft",
      "metoder": [
        {
          "id": "luft.transport",
          "namn": "Vindfält och spridning som M-matris",
          "status": "skiss",
          "flodeText": "Kommandorad, MCP och testsvit"
        },
        {
          "id": "luft.adjungerad",
          "namn": "Adjungerad känslighet och åtgärdsval",
          "status": "skiss",
          "flodeText": "Fristående modul"
        },
        {
          "id": "luft.mkn_intervall",
          "namn": "Monotona MKN-intervall",
          "status": "skiss",
          "flodeText": "Bara i API:t"
        }
      ],
      "kan": [
        "Vindfält och spridning i 3D (LBM-LES med adjungerad lösning och NO₂-kemi) på gemensam geometri.",
        "Vindkomfort och vindfara för höga hus (NEN 8100, Lawson), före och efter.",
        "Överskridanden av miljökvalitetsnormer som intervall ur en given halttidsserie och en angiven bakgrund."
      ],
      "testat": [
        {
          "text": "Analytiska fall L1 till L8 (`docs/LUFT.md`), bland annat Poiseuille-flöde och adjungerad dualitet",
          "kalla": {
            "text": "docs/LUFT.md",
            "href": "https://github.com/joelberring/floden/blob/main/docs/LUFT.md"
          }
        }
      ],
      "nyckeltal": [
        {
          "namn": "Körtid för en vindkomfortberäkning på CPU",
          "prefix": "mer än",
          "varde": 10,
          "enhet": "minuter",
          "decimaler": 0,
          "kalla": {
            "text": "data/mcp/metodstatus.toml",
            "href": "https://github.com/joelberring/floden/blob/main/data/mcp/metodstatus.toml"
          }
        }
      ],
      "begransningar": [
        "Skiss: ingen metod är klassad som rapportmetod.",
        "En beräkning tar lång tid på CPU, så luft är inte förberäknad i demon.",
        "Floden räknar inte fram timserien för halter; den kommer utanför Floden. Validering mot mätstationer väntar.",
        "Luft finns inte som knapp för rapport i webben."
      ]
    },
    {
      "id": "buller",
      "namn": "Buller",
      "metoder": [
        {
          "id": "buller.nord2000",
          "namn": "Nord2000 referenskärna",
          "status": "skiss",
          "flodeText": "Kommandorad, MCP och testsvit"
        },
        {
          "id": "buller.celf",
          "namn": "CELF: submodulärt val av åtgärder",
          "status": "skiss",
          "flodeText": "Bara i API:t"
        }
      ],
      "kan": [
        "Bullerscen, källmodell för väg, banor, fasadpunkter, operatorlager och klustring med felgräns.",
        "Referenskärna för Nord2000 (delmodell 1 till 10 inkopplade) som provas mot de officiella testfallen."
      ],
      "testat": [
        {
          "text": "Nord2000, officiella testfall RL 18-18 (grupp N): godkända",
          "godkanda": 55,
          "av": 80,
          "kalla": {
            "text": "STATUS.md",
            "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
          }
        },
        {
          "text": "Av de övriga: ska utredas",
          "varde": 16,
          "enhet": "fall",
          "decimaler": 0,
          "kalla": {
            "text": "STATUS.md",
            "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
          }
        },
        {
          "text": "Av de övriga: underkända",
          "varde": 5,
          "enhet": "fall",
          "decimaler": 0,
          "kalla": {
            "text": "STATUS.md",
            "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
          }
        },
        {
          "text": "Av de övriga: ej körbara",
          "varde": 4,
          "enhet": "fall",
          "decimaler": 0,
          "kalla": {
            "text": "STATUS.md",
            "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
          }
        }
      ],
      "nyckeltal": [],
      "begransningar": [
        "Under utveckling: kärnan är inte Nord2000-konform och rapportläget är spärrat.",
        "Buller finns inte i användarflödet i webben, bara som kommandorad och modul.",
        "Testfallens facit ligger i en lokal källmapp och finns inte i det publika repot.",
        "Årsmedel, kurvig väg och skog är bara delvis klara; se kända avvikelser i Matematik och forskning."
      ]
    }
  ],
  "plan": [
    {
      "rubrik": "Riktig geografi och mätning",
      "text": "Köra geografin goteborg-test mot Lantmäteriet och Göteborgs tjänster, mäta tid, minne och disk per ruta (S5, S6), sedan Hägersten-Älvsjö med Skärholmen (S7) och Stockholm (S8).",
      "status": "pågår",
      "uppdrag": "S5 till S8"
    },
    {
      "rubrik": "Jämförelse med Scalgo och SMHI",
      "text": "Jämföra var vattnet hamnar för provområdet och förklara skillnaderna (T09b).",
      "status": "väntar",
      "uppdrag": "T09b"
    },
    {
      "rubrik": "Jämförelseprov mot referensverktyg",
      "text": "Samma fall i Floden och ett referensverktyg per ämne, med tid och avvikelse (J1, förslag som projektets ägare bekräftar).",
      "status": "förslag",
      "uppdrag": "J1"
    },
    {
      "rubrik": "Buller enligt Nord2000",
      "text": "Klara kvarvarande testfall i grupp N, årsmedel, kurvig väg och skog, så att rapportläget kan öppnas; därefter snabbkärna, rapport och jämförelse mot etablerad programvara (B4, B7, B10 till B13).",
      "status": "pågår",
      "uppdrag": "B4, B7, B10 till B13"
    },
    {
      "rubrik": "Formella rapporter",
      "text": "Rapport med validerade metoder, granskning och signering av människa (R3), därefter export, arkiv och spårbarhet (R5).",
      "status": "väntar",
      "uppdrag": "R3, R5"
    },
    {
      "rubrik": "Ledningsnät och realtid",
      "text": "Ledningsnät kopplat i 1D (SWMM) och radarnederbörd. Kräver data från Stockholm Vatten och Avfall och beslut om driftmiljö (M7).",
      "status": "senare",
      "uppdrag": "M7"
    },
    {
      "rubrik": "Luft: validering och snabbare körning",
      "text": "Serverkörning med CuPy, vindtunnelvalidering (CEDVAL, Michelstadt) och validering mot mätstationer (L-b, L-c, L-h).",
      "status": "redo",
      "uppdrag": "L-b, L-c, L-h"
    },
    {
      "rubrik": "Dagsljusfaktor",
      "text": "Dagsljusfaktor i typrum ovanpå VSC, och en Radiance-referens för utkragningar (dagsljus fas 5).",
      "status": "redo",
      "uppdrag": "Dagsljus fas 5"
    },
    {
      "rubrik": "Ekologi mot riktiga data",
      "text": "Köra NMD, VMI och trädlagret mot riktiga filer, köra Stockholms nätverk mot stadens biotopdatabas och källhänvisa artprofilernas parametrar.",
      "status": "väntar",
      "uppdrag": "Ekologi, nästa steg"
    },
    {
      "rubrik": "Drift: kö, demoläge, kvoter och backup",
      "text": "Jobbkö med arbetare och kostnadsbok, demoläge med färdigräknade exempel, kvoter per konto, backup, och GPU-klass på servern (S4, S5k, S6d, S9, S10).",
      "status": "väntar",
      "uppdrag": "S4, S5k, S6d, S9, S10"
    }
  ],
  "uppmatt": [
    {
      "id": "webgpu_dagsljus",
      "amne": "Dagsljus och sol",
      "titel": "Dagsljus 300 × 300 m, SVF och soltimmar (64 riktningar)",
      "a_namn": "Webbläsarens GPU (WebGPU, f32)",
      "a_varde": 53.9,
      "b_namn": "Flodens server, CPU (f64)",
      "b_varde": 4242.646,
      "enhet": "ms",
      "decimaler": 1,
      "kalla": {
        "text": "docs/benchmark/2026-11-23-mac-webgpu.json",
        "href": "https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-mac-webgpu.json"
      },
      "maskin": "En Mac (Apple, Metal 3, 16 GB, Chrome 157) mot Flodens server floden.berring.se (Python, CPU)",
      "anm": "SVF-fältet avviker 0,0126 från serverns f64 i 13 av 90 000 celler (nära fasader), över toleransen 0,01; soltimmar ligger långt inom toleransen. Jämförelsen är Flodens GPU-kod mot Flodens CPU-kod, inte mot något annat verktyg.",
      "faktorLag": 78.71328385899814,
      "faktorHog": 78.71328385899814
    },
    {
      "id": "webgpu_vatten",
      "amne": "Skyfall",
      "titel": "Vatten 2D, lutande plan med sänka och byggnad, 64 × 64 celler, 1 000 tidssteg",
      "a_namn": "Webbläsarens GPU (WebGPU, f32)",
      "a_varde": 9.9,
      "b_namn": "Flodens server, CPU (f64)",
      "b_varde": 244.239,
      "enhet": "ms",
      "decimaler": 1,
      "kalla": {
        "text": "docs/benchmark/2026-11-23-mac-webgpu.json",
        "href": "https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-mac-webgpu.json"
      },
      "maskin": "En Mac (Apple, Metal 3, 16 GB, Chrome 157) mot Flodens server floden.berring.se (Python, CPU)",
      "anm": "Facit är skuggarens eget schema i f64, inte lösaren `tvad.lokal_troghet`. Största avvikelsen i max djup ligger långt under toleransen.",
      "faktorLag": 24.67060606060606,
      "faktorHog": 24.67060606060606
    },
    {
      "id": "hybrid_mot_2d",
      "amne": "Skyfall",
      "titel": "Hybrid (sänkgraf och 2D) mot full 2D",
      "kalla": {
        "text": "docs/MATEMATIK.md",
        "href": "https://github.com/joelberring/floden/blob/main/docs/MATEMATIK.md"
      },
      "maskin": "mätt efter rättelserna i vattenkärnan 2026-11-23",
      "anm": "De tidigare redovisade faktorerna (12,9 till 20) gällde före rättelserna och gäller inte längre.",
      "faktorLag": 1.2,
      "faktorHog": 1.8
    },
    {
      "id": "lokal_omrakning",
      "amne": "Skyfall",
      "titel": "Lokal omräkning mot full omräkning, 16 kvarter",
      "a_namn": "Lokal omräkning (bara det som berörs)",
      "a_varde": 26.7,
      "b_namn": "Full omräkning",
      "b_varde": 247.4,
      "enhet": "ms",
      "decimaler": 1,
      "kalla": {
        "text": "STATUS.md",
        "href": "https://github.com/joelberring/floden/blob/main/STATUS.md"
      },
      "maskin": "mätt 2026-11-20 (T04), före rättelserna i vattenkärnan och inte omkörd",
      "anm": "Resultatet är bitexakt lika (avvikelse 0,00 m). Bas är Flodens egen fullberäkning.",
      "faktorLag": 9.265917602996256,
      "faktorHog": 9.265917602996256
    }
  ],
  "kortid": {
    "kalla": {
      "text": "docs/benchmark/2026-11-23-container.json",
      "href": "https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-container.json"
    },
    "maskin": "Delad molncontainer: 2 vCPU (Intel Xeon, 2,30 GHz), 8 GB, Python 3.13, ingen GPU. Andra processer delar maskinen, så tiderna är ungefärliga och varierar med flera tiotals procent. De är ingen referens för produktionsservern.",
    "rader": [
      {
        "id": "ea1",
        "amne": "Skyfall",
        "fall": "EA1: Environment Agency Test 1, sänka över tröskel, 10 m",
        "varde_s": 0.548,
        "decimaler": 2
      },
      {
        "id": "d1",
        "amne": "Skyfall",
        "fall": "D1: Hunters analytiska lösning, 3 600 s, dx 5 m",
        "varde_s": 0.833,
        "decimaler": 2
      },
      {
        "id": "ea8a",
        "amne": "Skyfall",
        "fall": "EA8A: urbant regn, subgrid mot full 2D, 2 m",
        "varde_s": 1.75,
        "decimaler": 2
      },
      {
        "id": "ea2",
        "amne": "Skyfall",
        "fall": "EA2: 4 × 4 sänkor, hybrid 2D och sänkgraf, 5 m",
        "varde_s": 3.43,
        "decimaler": 2
      },
      {
        "id": "kvarter300",
        "amne": "Dagsljus och sol",
        "fall": "SVF och soltimmar, kvarter 300 × 300 m, 64 riktningar",
        "varde_s": 5.03,
        "decimaler": 2
      },
      {
        "id": "demokvarter",
        "amne": "Skyfall och dagsljus",
        "fall": "`floden demo --liten`: bygg värld, dagsljus och vatten med tom cache",
        "varde_s": 11.4,
        "decimaler": 1
      },
      {
        "id": "luft_l4",
        "amne": "Luft",
        "fall": "L4: punktkälla i likformigt flöde, 48 × 28 × 16 celler",
        "varde_s": 0.111,
        "decimaler": 3
      },
      {
        "id": "buller_kedja",
        "amne": "Buller",
        "fall": "Helkedja 300 m, 20 mottagare (påhittad emission, baskärnan, inte Nord2000)",
        "varde_s": 7.39,
        "decimaler": 2
      }
    ]
  },
  "andra": {
    "hamtad": "2026-11-25",
    "anm": "Uppgifterna är hämtade ur tillverkarnas och projektens egna sidor den 2026-11-25. Att en uppgift saknas här betyder inte att den saknas hos tillverkaren, bara att den inte stod på sidorna som lästes. Floden har inte kört något av verktygen.",
    "rader": [
      {
        "id": "scalgo_flash",
        "amne": "Skyfall",
        "verktyg": "SCALGO Live, Flash Flood Map",
        "upphov": "SCALGO",
        "vad": "Beskrivs som en enkel, stationär och händelsebaserad screeningmodell. Vattenvolymen från regnet leds nedströms och fyller sänkor på vägen. Ingen tidskomponent; regnets varaktighet beaktas inte och vattnet antas finnas i cellen direkt. Regnet ställs med ett reglage och gäller likformigt över terrängen; avrinningsfunktioner efter markanvändning kan dra av förluster.",
        "licens": "Licensform inte angiven på sidan som lästes.",
        "pris": "Inte angivet på sidan som lästes.",
        "metod": "Fyllning av sänkor (depression filling).",
        "kortid": "Inte angiven.",
        "lankar": [
          {
            "text": "SCALGO Live: Flash Flood Map",
            "url": "https://scalgo.com/en-US/scalgo-live-documentation/analysis/flash-flood-map"
          }
        ]
      },
      {
        "id": "scalgo_dynamicflood",
        "amne": "Skyfall",
        "verktyg": "SCALGO Live, DynamicFlood",
        "upphov": "SCALGO",
        "vad": "Hydrodynamiska 2D-simuleringar av ytavrinning med TUFLOW som lösare. Är en tilläggsmodul (Core+) som enligt sidan inte släppts i alla länder. Jobben tar enligt sidan avsevärt längre än vanliga beräkningar i arbetsytan, kan köra på GPU-resurser och köas när resurserna är upptagna. Användare med egen TUFLOW-licens kan ladda ner modellkonfigurationen.",
        "licens": "Tilläggsmodul till Scalgo Live; kontakta Scalgo om tillgänglighet.",
        "pris": "Inte angivet på sidan som lästes.",
        "metod": "Hydrodynamisk 2D-ytavrinning (TUFLOW).",
        "kortid": "Ingen körtid eller hastighetsfaktor angiven.",
        "lankar": [
          {
            "text": "SCALGO Live: hydrodynamic engine",
            "url": "https://scalgo.com/en-US/scalgo-live-documentation/hydrodynamic-engine/introduction"
          }
        ]
      },
      {
        "id": "mike21",
        "amne": "Skyfall",
        "verktyg": "MIKE 21 FM (flexibelt nät)",
        "upphov": "DHI",
        "vad": "Den hydrodynamiska modulen (även k-ε-turbulens, temperatur och salthalt) och transportmodulen körs på GPU; in- och utdata körs på CPU med OpenMP och MPI. Vissa moduler, till exempel Navier–Stokes-modulen, sediment och spektralvåg, är inte GPU-parallelliserade. GPU-stödet kräver NVIDIA CUDA (compute capability 7,5 till 12 i MIKE Release 2026). MIKE+ ingår inte i det som lästes.",
        "licens": "Licensfil; GPU-alternativets utgångsdatum följer avtalets utgångsdatum (SMA).",
        "pris": "Inte angivet på sidan som lästes.",
        "metod": "Hydrodynamisk modell på flexibelt nät.",
        "kortid": "Artikeln anger ingen hastighetsfaktor och hänvisar till benchmarkrapporter som inte lästs.",
        "lankar": [
          {
            "text": "DHI: GPU-stöd för MIKE 21/3 FM",
            "url": "https://support.dhigroup.com/knowledgebase/article/KA-01054/en-us"
          }
        ]
      },
      {
        "id": "infoworks_icm",
        "amne": "Skyfall",
        "verktyg": "InfoWorks ICM",
        "upphov": "Autodesk",
        "vad": "Integrerad 1D- och 2D-modellering av avloppssystem, ytvatten, vattendrag och översvämningsområden. Subgrid-sampling gör att större 2D-element kan användas med bibehållen topografisk detalj. 2D-beräkning på GPU kräver NVIDIA CUDA (compute capability 3,5 eller högre).",
        "licens": "Autodesk-produkt; licensform inte angiven på sidan som lästes.",
        "pris": "Inte angivet på sidan som lästes.",
        "metod": "Integrerad 1D/2D-hydraulik.",
        "kortid": "Tillverkaren anger att GPU kan snabba upp simuleringar med stora modeller; inget mätunderlag redovisas på sidan.",
        "lankar": [
          {
            "text": "Autodesk: InfoWorks ICM, funktioner",
            "url": "https://autodesk.com/au/products/infoworks-icm/features"
          },
          {
            "text": "Autodesk: krav på grafikmaskinvara för simulering",
            "url": "https://www.autodesk.com/learn/ondemand/tutorial/planning-graphics-hardware-requirements-for-simulation"
          }
        ],
        "tal": [
          {
            "text": "GPU kan snabba upp simuleringar med stora modeller med (tillverkarens uppgift, utan redovisad mätmetod)",
            "prefix": "minst",
            "varde": 12,
            "enhet": "gånger",
            "decimaler": 0
          }
        ]
      },
      {
        "id": "climatestudio",
        "amne": "Dagsljus och sol",
        "verktyg": "ClimateStudio",
        "upphov": "Tillverkarens webbplats",
        "vad": "Insticksmodul till Rhinoceros 3D (version 6, 7 eller 8). Enligt tillverkaren kör den Radiance i ett progressivt path tracing-läge med maskinvaruacceleration. Tillverkaren skriver att simuleringar som tidigare tog timmar kan göras på sekunder, och anger som exempel att sDA för ett kontor med sidoljus beräknas på en kort tid (se talet nedan) medan Daysim enligt tillverkaren tar timmar och Radiance dagar.",
        "licens": "Kommersiell licens och utbildningslicens finns; närmare form inte angiven på sidorna som lästes.",
        "pris": "Inte angivet på sidorna som lästes.",
        "metod": "Radiance (path tracing).",
        "kortid": "Tillverkarens egen uppgift för ett exempelkontor, utan redovisad mätmetod.",
        "lankar": [
          {
            "text": "ClimateStudio: Why is ClimateStudio so fast?",
            "url": "https://hibiscus-chartreuse-y9mk.squarespace.com/blog/why-is-climatestudio-so-fast"
          },
          {
            "text": "ClimateStudio: produktsida",
            "url": "https://hibiscus-chartreuse-y9mk.squarespace.com/climatestudio"
          }
        ],
        "tal": [
          {
            "text": "sDA för ett kontor med sidoljus (tillverkarens exempel, utan redovisad mätmetod)",
            "prefix": "ungefär",
            "varde": 10,
            "enhet": "sekunder",
            "decimaler": 0
          }
        ]
      },
      {
        "id": "accelerad",
        "amne": "Dagsljus och sol",
        "verktyg": "Accelerad",
        "upphov": "MIT Sustainable Design Lab",
        "vad": "GPU-accelererad version av Radiance. Projektet anger att den är snabbare än Radiance på samma uppgift, med faktorer per program (se talen nedan) mätta på ett RTX 8000-kort. Kräver NVIDIA CUDA-GPU.",
        "licens": "Gratis programsvit under slutanvändaravtal (EULA); licenstypen namnges inte på sidan som lästes.",
        "pris": "Gratis enligt projektet.",
        "metod": "Radiance på GPU.",
        "kortid": "Se hastighetsfaktorerna; de gäller GPU mot samma program på CPU.",
        "lankar": [
          {
            "text": "Accelerad",
            "url": "https://nljones.github.io/Accelerad"
          }
        ],
        "tal": [
          {
            "text": "rpict: GPU mot Radiance på CPU (projektets uppgift, RTX 8000)",
            "prefix": "upp till",
            "varde": 100,
            "enhet": "gånger",
            "decimaler": 0
          },
          {
            "text": "rcontrib: GPU mot Radiance på CPU (projektets uppgift, RTX 8000)",
            "varde": 45,
            "enhet": "gånger",
            "decimaler": 0
          },
          {
            "text": "rtrace: GPU mot Radiance på CPU (projektets uppgift, RTX 8000)",
            "varde": 28,
            "enhet": "gånger",
            "decimaler": 0
          }
        ]
      },
      {
        "id": "honeybee_radiance",
        "amne": "Dagsljus och sol",
        "verktyg": "Honeybee-radiance (Pollination)",
        "upphov": "Pollination",
        "vad": "Tillägg som ger dagsljussimulering med Radiance i Pollination, med recept för bland annat årligt dagsljus, dagsljusfaktor och himmelsandel.",
        "licens": "Öppet förråd på GitHub; licenstexten lästes inte.",
        "pris": "Inte undersökt.",
        "metod": "Radiance.",
        "kortid": "Inte angiven.",
        "lankar": [
          {
            "text": "pollination/honeybee-radiance",
            "url": "https://github.com/pollination/honeybee-radiance"
          }
        ]
      },
      {
        "id": "soundplan",
        "amne": "Buller",
        "verktyg": "SoundPLAN (SoundPLANnoise)",
        "upphov": "SoundPLAN (soundplan.eu)",
        "vad": "Listar Nord2000 för väg, järnväg och industri bland ett stort antal beräkningsstandarder. Nya standarder, till exempel Nord2000 Rev. 2019, läggs enligt tillverkaren till i aktuell version utan extra kostnad via underhåll.",
        "licens": "Kommersiell; licensvillkor ligger på en annan sida som inte lästes.",
        "pris": "Inte angivet på sidan som lästes.",
        "metod": "Beräkningsstandarder inklusive Nord2000.",
        "kortid": "Inte angiven.",
        "lankar": [
          {
            "text": "SoundPLAN: tillgängliga beräkningsstandarder",
            "url": "https://www.soundplan.eu/en/software/soundplannoise/standards"
          }
        ]
      },
      {
        "id": "cadnaa",
        "amne": "Buller",
        "verktyg": "CadnaA",
        "upphov": "DataKustik",
        "vad": "Nord2000 för industrikällor via två DLL-bibliotek som distribueras av Norwegian Public Roads Administration och är utvecklade av SINTEF. Den 32-bitars DLL:en saknar flertrådning och använder en kärna; 64-bitars-varianten stöder flertrådning. DataKustik anger att man inte utvecklat DLL:erna och därför inte kan förklara resultat från dem.",
        "licens": "Kommersiell; licensvillkor inte på sidan som lästes.",
        "pris": "Inte angivet på sidan som lästes.",
        "metod": "Nord2000 via SINTEF:s DLL (industrikällor).",
        "kortid": "Inte angiven.",
        "lankar": [
          {
            "text": "CadnaA: Nord2000 för industri",
            "url": "https://doku.datakustik.com/CadnaA/en/Industrie_Nord2000.html"
          }
        ]
      },
      {
        "id": "circuitscape",
        "amne": "Ekologi",
        "verktyg": "Circuitscape 5",
        "upphov": "Circuitscape-projektet",
        "vad": "Använder algoritmer från kretsteori för att förutsäga rörelse, genflöde och genetisk differentiering mellan populationer i heterogena landskap. Skriven i Julia; Windows, macOS och Linux.",
        "licens": "Öppen källkod, MIT-licens.",
        "pris": "Gratis (öppen källkod).",
        "metod": "Kretsteori.",
        "kortid": "Inga prestandatal angivna.",
        "lankar": [
          {
            "text": "Circuitscape: nedladdning",
            "url": "https://circuitscape.org/downloads/"
          }
        ]
      },
      {
        "id": "linkage_mapper",
        "amne": "Ekologi",
        "verktyg": "Linkage Mapper",
        "upphov": "consbio.org",
        "vad": "Pythonskript förpackade som ArcGIS-verktygslåda. Verktyget Linkage Pathways använder kartor över kärnområden och motstånd för att hitta minsta-kostnadskorridorer mellan närliggande kärnområden.",
        "licens": "Licens inte angiven på sidan som lästes.",
        "pris": "Inte angivet på sidan som lästes.",
        "metod": "Minsta-kostnadskorridorer.",
        "kortid": "Inte angiven.",
        "lankar": [
          {
            "text": "consbio.org: Linkage Mapper",
            "url": "https://consbio.org/software/linkage-mapper/"
          }
        ]
      },
      {
        "id": "conefor",
        "amne": "Ekologi",
        "verktyg": "Conefor Sensinode",
        "upphov": "Conefor-projektet",
        "vad": "Kvantifierar habitatytors betydelse för att behålla eller förbättra landskapets konnektivitet. C++; Linux och Windows. Projektet är märkt Beta och sidan på SourceForge uppdaterades senast 2013-05-30.",
        "licens": "Öppen källkod, GNU GPL version 2.",
        "pris": "Gratis (öppen källkod).",
        "metod": "Konnektivitetsindex för habitatytor.",
        "kortid": "Inte angiven.",
        "lankar": [
          {
            "text": "Conefor på SourceForge",
            "url": "https://sourceforge.net/projects/conefor/"
          }
        ]
      }
    ]
  },
  "ejMatt": {
    "rubrik": "Det som inte är mätt",
    "text": "Det finns ingen jämförande mätning av samma fall i Floden och i något av de andra verktygen: inga körtider på samma indata och maskin, och ingen avvikelse mellan Flodens och ett annat verktygs resultat. Därför anger Floden ingen faktor av typen X gånger snabbare än en produkt, och siffrorna i de två föregående avsnitten får inte läsas mot varandra. Hastighetstalen i avsnitt A gäller Floden mot Floden. Hastighetstalen i avsnitt B är tillverkarnas egna, mot deras egna baser, och Floden har inte kontrollerat dem. Ett jämförelseprov är föreslaget i planen (J1) men inte gjort."
  },
  "skillnader": [
    {
      "rubrik": "Öppen källkod",
      "text": "Kärnan är under GPL v3 och luftdelen under MIT. Koden, metodregistret och testfallen går att läsa, köra och granska. Bland ekologiverktygen som lästes är Circuitscape (MIT) och Conefor (GPL v2) också öppna.",
      "kalla": {
        "text": "LICENSE",
        "href": "https://github.com/joelberring/floden/blob/main/LICENSE"
      }
    },
    {
      "rubrik": "Samma flöde för flera ämnen",
      "text": "Skyfall, dagsljus och sol, ekologi och luft delar kärnan: geografi, projekt, ritat förslag, frågekatalog, beräkningsgraf med cache, beställning, rapport och MCP. I webbens kartflöde finns skyfall och dagsljus och sol fullt ut; ekologi och luft har begränsningarna som står under varje ämne.",
      "kalla": {
        "text": "docs/KARNA.md",
        "href": "https://github.com/joelberring/floden/blob/main/docs/KARNA.md"
      }
    },
    {
      "rubrik": "Körtid i sekunder för skiss på kvartersnivå",
      "text": "Skyfall och dagsljus för ett kvarter räknas på sekunder på en delad tvåkärnig molncontainer utan grafikkort (tabellen över körtider). Luft är undantaget och tar lång tid.",
      "kalla": {
        "text": "docs/benchmark/2026-11-23-container.json",
        "href": "https://github.com/joelberring/floden/blob/main/docs/benchmark/2026-11-23-container.json"
      }
    },
    {
      "rubrik": "Osäkerhetsintervall",
      "text": "Intervall för vatten (K40), SVF (K41), miljökvalitetsnormer och ekologi redovisas som min och max med känsliga överfall utpekade, och visas i resultatet och rapporten.",
      "kalla": {
        "text": "docs/DATAKONTRAKT.md",
        "href": "https://github.com/joelberring/floden/blob/main/docs/DATAKONTRAKT.md"
      }
    },
    {
      "rubrik": "Kontrollerade påståenden i rapporten",
      "text": "Varje tal i en rapport är en platshållare med belägg (beräkning, fil, cell, mått). Floden fyller talen och en deterministisk kontroll avvisar tal utan belägg och påståenden som inte stöds, före export till Word och PDF.",
      "kalla": {
        "text": "docs/RAPPORTER.md",
        "href": "https://github.com/joelberring/floden/blob/main/docs/RAPPORTER.md"
      }
    },
    {
      "rubrik": "Spårbarhet",
      "text": "Varje beräkning skriver ett manifest med kontrollsummor för all indata, kodversion och parametrar; MCP-anrop loggas i en hashkedjad körlogg.",
      "kalla": {
        "text": "AGENTS.md",
        "href": "https://github.com/joelberring/floden/blob/main/AGENTS.md"
      }
    }
  ],
  "saknas": [
    {
      "rubrik": "Validering mot oberoende data och etablerade verktyg",
      "text": "Jämförelsen med Scalgo och SMHI väntar (T09b), Nord2000 klarar bara en del av testfallen och ingen metod är klassad som rapportmetod av en människa.",
      "kalla": {
        "text": "PLAN.md",
        "href": "https://github.com/joelberring/floden/blob/main/PLAN.md"
      }
    },
    {
      "rubrik": "Certifiering och branschacceptans",
      "text": "Ingen certifiering och inget godkännande av myndighet eller bransch finns. Floden är ett skissverktyg och kartor och rapporter märks så.",
      "kalla": {
        "text": "data/mcp/metodstatus.toml",
        "href": "https://github.com/joelberring/floden/blob/main/data/mcp/metodstatus.toml"
      }
    },
    {
      "rubrik": "Fullständig 2D-hydraulik med ledningsnät",
      "text": "Floden har 2D lokal tröghet och hybrid, men 2D är inte ett steg i webbflödet och ledningsnät i 1D är inte kopplat. Enligt tillverkaren beskriver InfoWorks ICM integrerad 1D- och 2D-modellering av avloppssystem, ytvatten, vattendrag och översvämningsområden, vilket Floden inte har.",
      "kalla": {
        "text": "PLAN.md",
        "href": "https://github.com/joelberring/floden/blob/main/PLAN.md"
      }
    },
    {
      "rubrik": "Buller enligt Nord2000",
      "text": "Kärnan är inte Nord2000-konform och rapportläget är spärrat. SoundPLAN listar Nord2000 för väg, järnväg och industri.",
      "kalla": {
        "text": "docs/MATEMATIK.md",
        "href": "https://github.com/joelberring/floden/blob/main/docs/MATEMATIK.md"
      }
    },
    {
      "rubrik": "Dagsljusfaktor i rum och en Radiance-referens",
      "text": "Floden räknar himmelsandel, soltimmar och VSC på fasad. Dagsljusfaktor i typrum och reflekterat ljus finns inte än.",
      "kalla": {
        "text": "PLAN.md",
        "href": "https://github.com/joelberring/floden/blob/main/PLAN.md"
      }
    },
    {
      "rubrik": "Riktiga data i drift",
      "text": "Det mesta är provat mot syntetiska data. Körning mot riktiga Lantmäteri- och kommundata på servern och mätning av tid och minne per ruta väntar.",
      "kalla": {
        "text": "PLAN.md",
        "href": "https://github.com/joelberring/floden/blob/main/PLAN.md"
      }
    }
  ]
};
