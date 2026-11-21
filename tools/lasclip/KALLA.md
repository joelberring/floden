# lasclip – källa

Lyft oförändrat från O-Tid: <https://github.com/joelberring/otid>, `tools/lasclip`, commit `1d63297`
(2026-11-21). Licens MIT, © 2026 Joel Berring (se `Cargo.toml`). MIT-kod får ingå i Flodens GPL v3-kärna
så länge notisen behålls.

## Hur Floden använder det

Bara de sex första argumenten: `lasclip <in.las|laz> <ut.las> <minx> <miny> <maxx> <maxy>`.
Utfilen skrivs som okomprimerad `.las` och läses av `data_inlasning.las_las_punktmoln`
(omslag: `data_inlasning.lasclip`). Rastren `vatten.bin`, `mark.bin` och `punkter.bin` är gjorda för
O-Tids orienteringskedja (Karttapullautin, som Floden inte använder enligt `docs/BESLUT.md`) och används inte.
Flodens egen cellstatistik för belagd yta finns i `markmodell.laser_belagt`.

## Bygga

```
cargo build --release --locked --manifest-path tools/lasclip/Cargo.toml
```

Binären hamnar i `tools/lasclip/target/release/lasclip`, eller ange `FLODEN_LASCLIP`.
