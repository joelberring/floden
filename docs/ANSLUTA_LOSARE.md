# Ansluta en lösare till kärnan (för domänagenterna)

Den gemensamma kärnan (`src/karna/`, uppdrag T20) kör utredningsfrågor över alla domäner:
vatten, luft, buller och dagsljus. Varje domän ansluter sina beräkningar som **lösare**.
Kärnan sköter scenarier (bas + lappar), cachenycklar, lokal omräkning, manifest och
jämförelser före/efter. Lösaren behöver bara räkna.

## Snabbstart

1. Lägg en fil `src/<ditt_paket>/floden_losare.py` (t.ex. `src/dagsljus/floden_losare.py`).
2. Exponera `LOSARE: list[Losare]`.
3. Klart. Kärnan hittar filen automatiskt, och ingen gemensam fil behöver ändras.
   Kontrollera med `floden fraga losare`.

```python
from collections.abc import Mapping
from typing import Any, ClassVar

from karna.losare import Losare, LosarFel, LosarKontext, LosarUtdata


class VscFasad(Losare):
    namn: ClassVar[str] = "dagsljus.vsc_fasad"   # <doman>.<namn>
    doman: ClassVar[str] = "dagsljus"            # vatten | luft | buller | dagsljus | geometri | ovrigt
    version: ClassVar[str] = "1"                 # höj när resultatet kan ändras för samma indata
    beskrivning: ClassVar[str] = "Himmelsexponering (VSC) på fasadpunkter."
    indataroller: ClassVar[tuple[str, ...]] = ("fasadpunkter",)   # filer frågan måste ange
    utdataroller: ClassVar[tuple[str, ...]] = ("vsc",)            # filer du alltid lämnar

    def validera_parametrar(self, p: Mapping[str, Any]) -> dict[str, Any]:
        okanda = set(p) - {"azimutriktningar", "max_avstand_m"}
        if okanda:
            raise LosarFel(f"Okända parametrar: {sorted(okanda)}")
        # Fyll i standardvärden HÄR (de ingår i cachenyckeln), inte i kor().
        return {
            "azimutriktningar": int(p.get("azimutriktningar", 64)),
            "max_avstand_m": float(p.get("max_avstand_m", 700.0)),  # ≈ 60 m / tan(5°)
        }

    def paverkansradie_m(self, p: Mapping[str, Any]) -> float | None:
        # Hur långt bort en ändring kan påverka resultatet. None = globalt.
        return float(p["max_avstand_m"])

    def kor(self, k: LosarKontext) -> LosarUtdata:
        for ruta in k.rutor_att_lasa():         # rutan själv + grannar inom radien
            ...                                  # läs ruta.mapp / "mark.tif" osv. (K1)
        fasader = k.indata["fasadpunkter"]
        ...
        (k.utmapp / "vsc.parquet").write_bytes(...)
        return LosarUtdata(
            filer={"vsc": "vsc.parquet"},
            matvarden={"min_vsc_procent": 12.3},
            varningar=("3 fasadpunkter under takutsprång, skickade till referenslösaren",),
        )


LOSARE: list[Losare] = [VscFasad()]
```

## Regler

- **Deterministisk.** Samma kontext ska ge byte-identiska filer. Ingen tid, slump utan
  frö eller nätverk i resultatet. Slumpfrö är en parameter.
- **Skriv bara i `k.utmapp`.** Läs aldrig andra lösares cache direkt; använd `k.beroenden`.
- **Läs bara rutorna i `k.rutor_att_lasa()`.** För rutvisa lösare innehåller
  `k.scenario.rutor` bara rutan och grannarna inom radien, eftersom det är de som ingår i
  nyckeln. En ruta utanför radien kan ändras utan att nyckeln ändras.
- **Parametrar kommer i kanonisk form.** Lösaren får exakt det som hashades: ett
  heltalsvärt flyttal (3.0) kommer som heltal (3). Konvertera själv (`float(...)`) där
  typen spelar roll.
- **Extra indata ska vara en fil.** Bara den namngivna filen hashas, så använd GeoPackage,
  Parquet eller CSV, inte Shapefile (vars .dbf/.shx då inte skulle synas i nyckeln).
- **Alla parametrar valideras.** Okända parametrar ska ge `LosarFel`, inte ignoreras
  (stavfel får inte bli tysta standardvärden).
- **Höj `version`** när en kodändring kan ändra resultatet, också i moduler som lösaren
  anropar. Kärnan hashar inte källkoden, så annars återanvänds gamla resultat ur cachen.
- **Påverkansradien ska vara konservativ.** Är du osäker, välj större radie eller `None`.
  En för liten radie ger fel resultat som ser rätt ut, eftersom en granne som ändrats inte
  syns i nyckeln.
  - Dagsljus/sol: `H_max / tan(h_min)`, t.ex. 60 m / tan(5°) ≈ 690 m.
  - Buller: avståndet där källan har sjunkit under relevant nivå, eller `None` för
    trafikflöden som ändras i hela nätet.
  - Luft: spridningsavståndet i modellen; `None` för regional bakgrund.
  - Vatten: `None` (avrinningsområden har ingen fast radie).
  - Radien `0` betyder att rutan räcker själv (K1-rutor har 25 m överlapp).
- **Fel ska synas.** Kasta `LosarFel` med tydlig text. Kärnan sparar aldrig ett halvfärdigt
  resultat: en post blir synlig först när lösaren har lyckats.
- **Raster i K1-rutnätet** (GeoTIFF, EPSG:3011, samma utsträckning som rutan) kan
  jämföras före/efter av kärnan utan extra kod (`[[jamforelser]]`). Andra format (Parquet,
  GeoPackage) sparas och kopieras, men jämförs inte automatiskt än.

## Beroenden mellan analyser

En analys kan bero på en annan (`beror_pa = ["trafik"]`). Lösaren får då
`k.beroenden["trafik"]`:

- `.mapp`: utdatakatalogen om beroendet är globalt,
- `.per_ruta[ruta_id]`: utdatakatalog per ruta om beroendet är rutvis (de rutor som ingår
  i det aktuella jobbet).

Nycklarna kedjas: ändras trafikresultatet ändras bullrets nyckel automatiskt.

## Testa din lösare

Se `tests/karna/test_karna.py` för mönster: bygg en fråga med `fraga_fran_dict`, kör med
`kor_fraga` mot en temporär `ResultatCache` och kontrollera ett fall med känt svar.

Kontrakten finns i `docs/DATAKONTRAKT.md` under K20 (fråga), K21 (lösare) och K22 (cache).
