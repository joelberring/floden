# WebGPU & klientberäkning i Floden – arkitektur och kontrakt

Mål: köra så mycket som möjligt lokalt på användarens egen GPU (**WebGPU / WGSL**) eller CPU (**Rust → WASM + Web Workers**) för att hålla serverkostnaden nära noll, utan att kompromissa med fysikens noggrannhet eller reproducerbarhet (`run.json`).

---

## 1. Domänfördelning och minnesbudget

| Domän | Primär klientmotor | Precision | Typisk VRAM / RAM | Typisk svarstid (laptop) | Serverroll |
| --- | --- | --- | --- | --- | --- |
| **Dagsljus** (`dagsljus_horisont.wgsl`) | WebGPU compute | `f32` | ~2 MB (`512×512`) | **5–25 ms** | Basdata + formell rapport |
| **Vatten 2D** (`vatten_2d.wgsl`) | WebGPU compute | `f32` | ~4 MB (`256×256`) | **0,3–2 s** | `f64`-verifiering + skyddsklassat ledningsnät |
| **Luft / 3D-LBM** (`luft_lbm_d3q19.wgsl`) | WebGPU compute | `f32` (`f_bar`) | ~22 MB (`80×60×30`) | **2–8 s** (300–1000 MLUPS) | Väderserier, NORTRIP & tung slutrapport |
| **Buller (Nord2000)** | Rust → WASM + Web Workers | `f64` | ~5 MB | **1–5 s** | Basprofilcache & formell verifiering |

---

## 2. Exekveringskedja och automatisk fallback

```text
1. navigator.gpu tillgängligt + maxStorageBufferBindingSize räcker?
   -> Kör WGSL compute shader på klientens GPU (`berakningsplats = "klient_webgpu_f32"`).
2. Annars: WebAssembly + Web Workers?
   -> Kör kompilerad Rust-kärna på klientens CPU (`berakningsplats = "klient_wasm_f64"`).
3. Annars (eller vid "Verifiera för rapport" / skyddsklassad data):
   -> Kör deterministiskt i Python/Rust på servern (`berakningsplats = "server_f64"`).
```

---

## 3. Fysikaliska garantier och `run.json`-kontrakt

- **Fysikparitet:** Alla tre WGSL-shaders i `webb/src/gpu/shaders/` följer samma ekvationer som Python-/Rust-kärnorna:
  - `dagsljus_horisont.wgsl`: Amanatides–Woo-genomgång i `f32`, SVF = $\frac{1}{K}\sum \cos^2 h_k$, och soltimmar begränsade till aktiva solpositioner (`hojd_rad > 0`).
  - `vatten_2d.wgsl`: punkt-implicit kvadratisk Manning-friktion $q = \frac{2 q_{\text{rhs}}}{1 + \sqrt{1 + 4c|q_{\text{rhs}}|}}$ med $h^{7/3} = h^2 h^{1/3}$ och positivitetsbevarande volymuppdatering.
  - `luft_lbm_d3q19.wgsl`: avvikelselagring $\bar{f}_i = f_i - w_i$ (minimerar avrundningsfel i `f32` vid låga Machtal), fuserad pull-stream, Smagorinsky-LES enligt Hou (1996) och exakt implicit kvadratisk vegetationsdämpning.
- **Tydlig märkning i `run.json`:** Klientkörningar i `f32` ska märkas med `berakningsplats: "klient_webgpu_f32"` och `verifierad_f64: false` tills användaren kör slutverifiering i `f64`.
- **Säkerhet:** Skyddsklassad data (t.ex. ledningsnät) skickas aldrig till webbläsaren.

---

## 4. 3D-hantering och modellering av den byggda miljön (Zero-copy GPU)

1. **Delad GPU-buffert mellan fysik och Three.js `WebGPURenderer`:** Fysik-shaders (`dagsljus_horisont.wgsl`, `vatten_2d.wgsl`, `luft_lbm_d3q19.wgsl`, `partikel_spar.wgsl`) skriver direkt till samma storage buffers/texturer som 3D-scenen renderar (0 kopiering via CPU).
2. **GPU-rasterisering av redigerade hus:** När användaren ändrar ett hus renderas förslagshusen ortografiskt rakt uppifrån till `z_m` (< 1 ms på GPU), vilket matar dagsljus- och vattenshadern direkt utan serveranrop.
3. **Statisk stad + redigerbart förslag:** Omgivande kvarter hålls i en enda sammanslagen buffert (1 draw call); bara planförslagets 1–20 hus är separat redigerbara (höjddragning via `scale.y` utan CPU-extrudering).
4. **Terräng-LOD & instancing:** Statisk rutnätsmesh med vertex-displacement från `r32float`-höjdtextur + `InstancedMesh` för fasadpunkter, träd (`lad_m2_m3`) och 50 000 animerade partikelspår (`partikel_spar.wgsl`).

