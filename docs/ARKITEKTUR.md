# Arkitektur

## Lager

```
Data (laser, baskarta, ledningar, jordarter, väder)
        │
        ▼
Markmodell (recept per ruta: mark, byggnader, brytlinjer, trummor, osäkerhet)  ◄── Projektlapp
        │
        ▼
Sänkgraf (sänkhierarki, volymkurvor, överfall, flödesvägar, persistens)
        │
        ├──► Graf-dynamik   (millisekunder; skiss, osäkerhet, varning)
        ├──► Hybrid         (sekunder; före/efter, entréer, hastigheter)
        └──► Full 2D        (minuter; utredning, kontroll av hybriden)
                │
                ▼
Resultat (djup, hastighet, tid, fara, osäkerhet) ──► Ruttanalys ──► Rapporter
```

Allt kopplas ihop genom filformaten i `DATAKONTRAKT.md`. Ingen modul läser en annan moduls interna tillstånd.

## Moduler

| Modul | Uppdrag | Indata | Utdata | Språk (start → mål) |
| --- | --- | --- | --- | --- |
| `markmodell` | T01, T01b | Laser, baskarta, trummor, områden | Markmodell-ruta (K1) | Python → Rust för tunga steg |
| `sankgraf` | T02 | Markmodell | Sänkgraf (K2) | Python/C++-bibliotek → Rust |
| `dynamik` | T03 | Sänkgraf, regn (K4), scenario (K5) | Resultat (K6) | Python → Rust |
| `mark_forluster` | T06 | Markmodell, jordarter, grundvatten | Förlustparametrar per cell och nod (del av K5) | Python |
| `lapp` | T04 | Markmodell, lapp (K3) | Ny markmodell för berörda rutor, lista över berörda noder | Python |
| `tvad` | T05 | Markmodell, regn, scenario | Resultat (K6) | SFINCS (extern binär) → Rust/wgpu |
| `rutt` | T07 | Vägnät, resultat, parametrar (K7) | Ruttresultat (K8) | Python |
| `rapport` | T08 | Resultat, manifest, mall | Rapport (Word/PDF) | Python |
| `webb` | T10 | Allt ovan via API | Karta och vyer | TypeScript |
| `testsvit` | T09 | Testfall | Mätvärden | Python |

## Principer

- **Räkna det dyra en gång.** Markmodell och sänkgraf byggs per ruta och cachas med kontrollsumma.
- **Rätt nivå för rätt fråga.** Graf-dynamiken svarar på det mesta. 2D används där vatten rinner i skikt och för kontroll.
- **Lokal omräkning.** En lapp räknar bara om berört delträd och det som ligger nedströms.
- **Samma resultatformat på alla nivåer**, så att skiss och utredning går att jämföra direkt.

## Repo-struktur (förslag)

```
floden/
  AGENTS.md  PLAN.md  STATUS.md  README.md
  docs/                 dokumentation och kontrakt
  tasks/                uppdragsbeskrivningar
  src/<modul>/          en mapp per modul
  tests/<modul>/        enhetstester
  testsvit/             testfall med facit och körskript
  data/omraden/         områdesavgränsningar (öppna)
  data/cache/           nedladdad öppen data (inte i git)
  config.toml           standardinställningar
  config.local.toml     sökvägar till lokal, icke-öppen data (inte i git)
```

## Återanvändning från O-Tid

O-Tid finns på github.com/joelberring/otid. Följande kan lyftas ut och användas, men kopieras in som egna paket med
källhänvisning så att projekten hålls isär:

- `tools/lasclip` (Rust, klipper LAS/LAZ till en ruta).
- Geo-arbetarens laserkedja (PDAL, markklass, vattenmask) och serverstart vid behov.
- DWG-import med lagerprofiler för Stockholms baskarta.
- Klassaren för belagd yta.
- SGU-kopplingen för jordarter.
- Mönstret med recept, kontrollsummor och objektlagring.
