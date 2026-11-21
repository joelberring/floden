# tvad_karna – 2D-lösaren i Rust

Fas 3, del 1 (systemdesign avsnitt 4.2). BESLUT 2026-11-20: "Börja i Python, flytta heta delar till
Rust (wgpu för GPU) när de är validerade". Python-lösaren `tvad.lokal_troghet` är facit; den här
kärnan kör samma tidsslinga parallellt på CPU (rayon).

## Vad som stöds

Fint rutnät (`subgrid_faktor=1`): Bates (2010) / de Almeida (2012) lokal tröghet, theta-utjämning,
CFL-tidssteg, regnserie med brytpunkter, ogenomträngliga byggnader, öppna ränder, massbevarande
flödesbegränsare, maxdjup, maxhastighet, fara och ankomsttider (0,1 och 0,2 m). Subgrid ger fel i
omslaget tills den är portad.

## Användning

```
cargo build --release --locked --manifest-path rust/tvad_karna/Cargo.toml
```

Från Python: `tvad.rust_backend.kor_2d_lokal_troghet_rust(...)` – samma argument och samma
`TvadResultat` som `kor_2d_lokal_troghet`, plus `tradar`. Omslaget gör förberedelserna (Manning,
öppna ränder, summor) med Python-lösarens egna funktioner och byter `.npy`-filer med binären:

`tvad_karna <in> <ut> [<trådar>]`

| In | Innehåll |
| --- | --- |
| `z_bed.npy` | float64 (h, w), m RH 2000 |
| `manning.npy`, `aktiv.npy` | float64 (h, w); uint8 (h, w) |
| `rand_{vast,ost,norr,soder}_{mask,lutning,bredd}.npy` | öppna ränder (uint8 / float64) |
| `regn_tider.npy`, `regn_intens_m_s.npy` | float64 |
| `param.npy` | float64: dx, dy, cfl, min_djup, dt_max, theta, t_start, t_slut, total_aktiv_area, total_doman_area, gles_front |

Ut: `max_djup`, `slut_djup`, `max_hastighet`, `fara`, `tid_till_0_1m`, `tid_till_0_2m` (float32),
`volym` (float64), `matvarden.npy` (in_m3, ut_m3, antal_steg, summa aktiv andel, körtid, trådar) och
`matvarden.json`. Alla flyttal går binärt, så inget avrundas på vägen.

## Samma svar som Python

Varje uttryck följer Python-lösarens räkneordning operation för operation, och summorna (`ut_m3`,
aktiv andel) använder samma parvisa summering som `np.sum` (8-vägs utrullning, block om 128 och
buffertblock om 8192). Reduktioner är max, heltal eller summor i fast ordning, så resultatet är
bitidentiskt oavsett antal trådar.

Kvarvarande skillnad: NumPy använder på AVX-512-processorer en vektoriserad `pow` som skiljer
högst 1 ulp från libm i ca 5 % av värdena; Rust använder libm. Det ger skillnader på ulp-nivå som
syns först när de passerar ett randflöde (A4: 2,2e-7 m i slutdjupet i en enda nästan torr cell).
Validering: `PYTHONPATH=src python3 rust/tvad_karna/validering.py`.

## Prestanda

`PYTHONPATH=src python3 rust/tvad_karna/prestanda.py <simulerad_tid_s> <celler>`. Mätt på en VM med
2 logiska kärnor (AVX-512): se STATUS/PR. Tidsslingan per steg domineras av kantflödena
(`powf` ~13 ns per kant och riktning), sedan av hastighet/fara och flödesbegränsaren.

## Nästa steg: GPU (wgpu)

Kärnan är skriven för att kunna lyftas:

1. **Rena funktioner per kant/cell** (`src/karna.rs`: `bates_flode`, `kant_djup`, `rand_flode`,
   `utjamnat_flode`) blir WGSL-funktioner; varje pass i `losare.rs` blir en compute shader med en
   tråd per kant eller cell: (a) x- och y-kantflöden, (b) begränsare `skala`, (c) skalning,
   (d) volym/djup/max/ankomst, (e) hastighet/fara.
2. **Reduktioner** på GPU: h_max (för CFL) och "någon våt/begränsad" som parallella reduktioner;
   `ut_m3` på CPU (randvektorerna är små) eller som fast trädreduktion för determinism.
3. **Precision:** WGSL har bara f32 i grundutförandet; f64 kräver `SHADER_F64` (stöds på många
   men inte alla kort). Plan: f64 där det finns, annars f32 med kompenserad volymsumma, och
   valideringen mot Python körs med tolerans (bitidentitet är inte målet på GPU). `pow` i WGSL är
   inte korrekt avrundad – jämför mot `validering.py` med toleranser i stället för likhet.
4. **Minne:** hela tillståndet på GPU, bara resultat tillbaka i slutet; aktiv-cell-listor
   (systemdesign 4.2) som kompakterade indexbuffertar när våtfronten är liten.
5. **Subgrid** (portas först på CPU, sedan GPU) och lokalt tidssteg.
