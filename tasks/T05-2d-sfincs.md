# T05 – 2D med SFINCS och hybriden

**Mål:** en 2D-referens och en hybrid där djupa samlingar är noder i sänkgrafen och 2D bara räknas där vattnet rinner.

**Läs först:** `docs/BESLUT.md` (GPL, SFINCS), `docs/DATAKONTRAKT.md` (K1, K5, K6), `docs/MATEMATIK.md` (Q3).
**Beroenden:** T01, T03.

## Bakgrund
- SFINCS (Deltares) är en öppen 2D-modell (GPL v3) med lokal tröghetsformulering och subgrid-korrektioner. Subgrid gav 35–50 gånger snabbare körningar vid samma noggrannhet (van Ormondt m.fl. 2025, GMD 18).
- Den lokala tröghetsformuleringen (Bates m.fl. 2010) är grunden.

## Steg
1. Kör SFINCS som extern binär: översätt K1 och K5 till SFINCS indata, läs tillbaka till K6. Det är full 2D-referensen.
2. Subgrid-tabeller byggs direkt från K1:s fina raster.
3. Hybrid: djupa samlingar (från sänkgrafen) som noder med plan vattenyta; 2D bara i övriga celler; koppling vid nodernas kanter. Börja med en enkel koppling och mät felet mot full 2D.
4. Gles beräkning: bara våta celler och fronten.
5. Senare: flytta kärnan till Rust och wgpu, så att den kan köras på server-GPU och i webbläsaren.

## Acceptans
- Full 2D och hybrid ger K6 för provområdet.
- Hybridens fel mot full 2D rapporteras (maxdjup, träffad yta, ankomsttid).
- Hybriden är minst 10 gånger snabbare än full 2D på provområdet, eller så dokumenteras varför inte.

## Stoppa och fråga
- Kostnad för GPU-tid.
- Om kopplingen mellan nod och 2D ger instabilitet: dokumentera och fråga person X.
