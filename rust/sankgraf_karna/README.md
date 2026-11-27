# sankgraf_karna – SankGraf-kärnan i ren Rust

Licens: GPL-3.0-or-later (se `LICENSE` i repots rot).

Snål, deterministisk Rust-implementation utan externa crates (endast `std`) för de två tunga
rasterstegen i `src/sankgraf/hierarki.py`:

1. `_berakna_avrinningsomraden`: 4-granne brantaste lutning (N, S, V, Ö), 2D/3D-kantbarriärer,
   platå-BFS med flerkälls-dränering och lövsänkeetikettering samt O(N) vägkompression till
   `cell_till_nod`.
2. `_bygg_granspar`: extraktion av unika gränspar mellan distinkta avrinningsområden och mot
   ytterrand (`HAV_NOD_ID = -1`), sadelhöjdsreduktion inom `1e-9 m` tolerans samt sortering i
   Kruskal-ordning `(w_min, c_lo, c_hi)`.

## Bygg och testa

```bash
cargo test --locked --manifest-path rust/sankgraf_karna/Cargo.toml
cargo build --release --locked --manifest-path rust/sankgraf_karna/Cargo.toml
```

## Körning

```bash
sankgraf_karna <indata.flsg> <utdata.flsg>
```

Python-bryggan `src/sankgraf/rust_brygga.py` skapar och städar temporära `.flsg`-filer
automatiskt (magisk header + version + dimensioner + exakt längd + IEEE CRC-32 kontrollsumma).
Styrning via miljövariabler:

- `FLODEN_SANKGRAF_KARNA`: explicit sökväg till binären
- `FLODEN_SANKGRAF_RUST`: `auto` (standard), `1`/`pa`/`alltid`, eller `0`/`av`/`python`
- `FLODEN_SANKGRAF_RUST_MIN_CELLER`: celltröskel i `auto`-läge (standard `100000`)
