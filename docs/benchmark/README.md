# Benchmark

Mått på körtid, minne och avvikelse mot facit för fasta fall. Formatet är `floden-benchmark/v1`
(K37 i `docs/DATAKONTRAKT.md`). Samma format skrivs av webbläsarprovet `#/prov/webgpu`
(`docs/WEBGPU-ARKITEKTUR.md` avsnitt 5).

## Köra

```bash
export PYTHONPATH=src            # om `floden` i din miljö pekar på en annan arbetskopia: python -m floden.cli
floden benchmark                                  # alla delar, tabell i terminalen
floden benchmark --del skyfall --ut skyfall.json  # en del, skriv JSON
floden benchmark --jamfor docs/benchmark/2026-11-23-container.json   # jämför mot en tidigare körning
floden benchmark --ny ny.json --jamfor gammal.json                   # jämför två filer utan att köra
```

Flaggor: `--del skyfall|dagsljus|luft|buller|alla`, `--ut`, `--uppvarmning` (förval 1), `--upprepningar` (förval 5),
`--jamfor`, `--troskel-procent` (förval 25), `--ny`. Slutkod 0 = klart, 1 = något mått har försämrats mer än
tröskeln, 2 = fel. En hel körning tar några minuter.

## Vad som mäts

Varje fall körs i en **egen process** (`python -m benchmark.barn`), så att minnesmåttet bara gäller fallet.

- **Tid:** `time.perf_counter`. En uppvärmning som kastas, sedan `upprepningar` körningar; **medianen** är värdet.
  Min, max och alla tider står också i JSON. Tungt fall (`demokvarter-liten`, `helkedja-300m`) körs högst 3 gånger.
- **Minne, två mått** (båda står i JSON):
  - `rss_topp_mb`: processens högsta RSS (`VmHWM` i `/proc/self/status`, annars `resource.getrusage().ru_maxrss`).
    Inkluderar interpretator och importer; `rss_okning_mb` är skillnaden mot RSS före fallet. Räknar också
    allokeringar i C-bibliotek. (`ru_maxrss` används inte på Linux: det ärver föräldraprocessens topp över exec.)
  - `tracemalloc_topp_mb`: högsta Python-allokerade minne i en separat extra körning (numpys buffertar räknas, C-bibliotekens egna
    allokeringar gör det inte). Tidtagningen sker aldrig under tracemalloc.
- **Avvikelse mot facit** (där facit finns): fallets eget mått och den gräns testsviten använder, se tabellen.
  `godkand` betyder värde <= gräns. Fall utan facit har `avvikelse = null`.

| Del | Fall | Vad det kör | Facit och mått |
| --- | --- | --- | --- |
| skyfall | `EA1` | EA Test 1, sänka över tröskel, 10 m | MAE sänknivå mot analytisk jämvikt (gräns 1,5 cm) |
| skyfall | `EA2` | EA Test 2, 4x4 sänkor, hybrid 2D + sänkgraf, 5 m | MAE maxdjup mot full 2D (5 cm) |
| skyfall | `EA8A` | EA Test 8A, urbant regn, subgrid mot full 2D, 2 m | MAE maxdjup mot full 2D (5 cm) |
| skyfall | `D1` | Hunters analytiska lösning, friktionsdominerad, dx 5 m, 3 600 s | RMSE djup (0,01 m) |
| skyfall | `vatten2d-ref-64x64` | Vatten 2D-schemat i f64 på 64x64 celler (serversidan av GPU-provet) | volymbalans i schemat (1e-6) |
| dagsljus | `grupp-E` | Testgrupp E (E1-E7) | största avvikelse som andel av sin gräns (1,0) |
| dagsljus | `kvarter300-svf-soltimmar` | SVF och soltimmar, kvarter 300x300 m, 64 riktningar (serversidan av GPU-provet) | inget facit (är facit) |
| dagsljus | `demokvarter-liten` | `floden demo --liten` i tillfällig mapp med tom cache: bygg värld, dagsljus, vatten | inget facit |
| luft | `L4-punktkalla` | Punktkälla i likformigt flöde, 48x28x16 | största relativa fel mot analytisk lösning (10 %) |
| luft | `L5-dualitet` | Adjungerad dualitet, 24x12x10 | relativt fel (1e-8) |
| buller | `helkedja-300m` | Scen, banor, baskärna, operatorlager; 300 m, 20 mottagare | klustringens största fel mot oklustrad beräkning, mot garanterad felgräns (dB) |
| buller | `nord2000-tvastrale` | Nord2000-kärnan, plan hård mark, 180 profiler | avvikelse mot analytisk tvåstrålesumma, 25-315 Hz (0,02 dB; gränsen är benchmarkens egen) |
| buller | `nord2000-rl18-18` | Nord2000-testfallen RL 18-18 | **hoppas över: kräver lokal källmapp** (`testsvit/N_nord2000` finns inte i det publika repot) |

Luft är begränsad i tid: varje luftfall har en tidsgräns på 60 s för första körningen och markeras annars
"hoppas över". `helkedja-300m` använder påhittad emission och baskärnan, så nivåerna är inga bullernivåer; fallet
mäter kedjans tid och minne och att klustringen håller sitt fel. Rust-kärnan (`rust/tvad_karna`) används inte av
sviten (Python-lösarna körs); `maskin.rust_karna` anger om binären finns.

## Var maskinen står

`2026-11-23-container.json` är mätt i en **delad molncontainer** (Claude Code på webben): 2 vCPU (Intel Xeon @ 2,30 GHz),
8 GB RAM, Python 3.13, numpy 2.1.3, ingen GPU, Rust-kärnan inte byggd. Andra processer delar maskinen, så
**siffrorna är ungefärliga** (räkna med flera tiotals procent i variation mellan körningar) och är ingen referens för
produktionsservern (`docs/DRIFT.md`, S6). De är en baslinje att jämföra ändringar mot på samma maskin.

## Jämförelse och försämring

`--jamfor gammal.json` visar förändring i procent per fall och mått (tid, `rss_topp_mb`, `tracemalloc_topp_mb`,
avvikelse) och flaggar `FÖRSÄMRING` när ett mått ökat mer än tröskeln (förval 25 %). Jämför bara körningar från samma maskin;
dokument från olika maskiner eller källor jämförs men märks som osäkra.

## Webbläsarens GPU-prov

Se `docs/WEBGPU-ARKITEKTUR.md` avsnitt 5. Ladda ned resultatet från sidan (källa `"webblasare"`) och lägg det här,
t.ex. som `docs/benchmark/ÅÅÅÅ-MM-DD-<maskin>-webblasare.json`. Klientens f32-tal är skissläge.

## Webbläsare (WebGPU)

`2026-11-23-mac-webgpu.json`: Joels Mac (Apple, Metal 3, 10 kärnor, 16 GB, Chrome 157), körd på
floden.berring.se. Dagsljus 300 × 300 m: 54 ms mot 4 243 ms på servern (cirka 80 gånger). Vatten 2D
64 × 64, 1 000 steg: 9,9 ms mot 244 ms (cirka 25 gånger). Soltimmar och vatten ligger långt
inom toleransen. SVF hade största fel 0,0126 i 0,0144 % av cellerna (13 av 90 000, nära fasader).

Ett fall är godkänt när alla celler är ändliga, högst 0,1 % av cellerna ligger över toleransen och
ingen cell har större fel än två gånger toleransen (`godkand()` i `webb/src/prov/jamfor.ts`).
Med den regeln är också SVF-fallet godkänt. Filen ovan skrevs med den äldre regeln (största fel ≤
toleransen) och har därför `godkand: false` för SVF.
