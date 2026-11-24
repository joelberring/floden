"""`floden demo`: bygger demovärlden, kör demofrågorna och skriver ett K0-manifest.

Idempotent och deterministisk: världen byggs bara om när dess parametrar eller kodversionen
ändrats, frågorna körs genom kärnan med dess cache (K22, samma jobbnycklar ger samma resultat) och
en körning vars plan är oförändrad lämnas orörd. Webben (`floden webb`) listar körningarna direkt.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from demovarld import varld as w
from demovarld.fragor import (
    FRAGA_DAGSLJUS,
    FRAGA_EKOLOGI,
    FRAGA_LUFT,
    FRAGA_VATTEN,
    text_dagsljus,
    text_ekologi,
    text_luft,
    text_vatten,
)
from demovarld.luftserier import skriv_luftserier
from gemensamt.hash import sha256_bytes, sha256_fil
from gemensamt.manifest import FilPost, hamta_kodversion, skapa_manifest, skriv_manifest
from karna.scenario import Planscenario, skriv_scenario
from markmodell.k1_skrivare import K1SkrivFel  # noqa: F401  (säkerställer att markmodellen laddas)
from webb.fragor import FragaTjanst

VARLD_JSON = "varld.json"
DEMOFRAGOR = (FRAGA_DAGSLJUS, FRAGA_VATTEN, FRAGA_EKOLOGI, FRAGA_LUFT)


class DemoFel(Exception):
    """Fel vid bygge eller körning av demon."""


@dataclass(frozen=True)
class DemoLage:
    """Normal demo (1 m-celler, 700 m värld) eller litet läge för test (2 m-celler)."""

    namn: str
    upplosning_m: float
    riktningar: int

    def varld(self) -> w.DemoVarld:
        return w.DemoVarld(upplosning_m=self.upplosning_m)


NORMAL = DemoLage("normal", 1.0, 180)
LITEN = DemoLage("liten", 2.0, 64)


def _skriv_k9(sokvag: Path, bas: Path, scenario_id: str, andringar: list[Any], text: str) -> Path:
    recept = tuple(sha256_fil(f) for f in sorted(bas.glob("mm_*/recept.json")))
    if not recept:
        raise DemoFel(f"Inga K1-rutor i {bas}")
    return skriv_scenario(
        Planscenario(scenario_id, recept, tuple(andringar), beskrivning=text), sokvag
    )


def varld_nyckel(lage: DemoLage) -> str:
    v = lage.varld()
    return sha256_bytes(
        json.dumps(
            {"kodversion": w.KODVERSION, "varld": asdict(v), "lage": asdict(lage)},
            sort_keys=True,
        ).encode("utf-8")
    )


def bygg_varld(
    varld_dir: Path, lage: DemoLage, *, tvinga: bool = False, logg: Callable[[str], None] = print
) -> bool:
    """Bygger rutor och K9-scenarier i `varld_dir`. Returnerar True om något byggdes."""
    v = lage.varld()
    nyckel = varld_nyckel(lage)
    meta = varld_dir / VARLD_JSON
    if not tvinga and meta.is_file():
        try:
            gammal = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            gammal = {}
        if (
            gammal.get("nyckel") == nyckel
            and (varld_dir / "dagsljus" / "k9_nytt_hus.json").is_file()
            and (varld_dir / "ekologi" / "nmd.tif").is_file()
        ):
            return False
    varld_dir.mkdir(parents=True, exist_ok=True)
    logg("Bygger världen (dagsljus: rutor med marginal) ...")
    bas_d = w.bygg_rutnat_dagsljus(v, varld_dir / "dagsljus" / "bas")
    _skriv_k9(
        varld_dir / "dagsljus" / "k9_hojd.json",
        bas_d,
        "hojd",
        [w.andring_hojd()],
        "Det låga huset i nordöstra gården höjs från 4 till 15 m.",
    )
    _skriv_k9(
        varld_dir / "dagsljus" / "k9_nytt_hus.json",
        bas_d,
        "nytt_hus",
        [w.andring_nytt_hus(v)],
        "Nytt 18 m-hus i sydvästra gården.",
    )
    logg("Bygger världen (vatten: fokusområdet som en ruta) ...")
    bas_v = w.bygg_ruta_vatten(v, varld_dir / "vatten" / "bas")
    _skriv_k9(
        varld_dir / "vatten" / "k9_nytt_hus.json",
        bas_v,
        "efter",
        [w.andring_nytt_hus(v)],
        "Nytt 18 m-hus i sydvästra gården, spärrar avrinningen mot porten.",
    )
    logg("Bygger det påhittade NMD-lagret för ekologidemon ...")
    from data_inlasning.nmd import NmdLager, skriv_nmd

    nmd, x0, y1, cell = w.bygg_nmd(v)
    skriv_nmd(NmdLager(nmd, x0, y1, cell), varld_dir / "ekologi" / "nmd.tif")
    meta.write_text(
        json.dumps(
            {
                "nyckel": nyckel,
                "kodversion": w.KODVERSION,
                "lage": asdict(lage),
                "varld": asdict(v),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return True


def skriv_fragor(fragedir: Path, varld_dir: Path, lage: DemoLage) -> dict[str, Path]:
    """Skriver demofrågorna i `fragedir` (bara om innehållet ändrats, så filer är orörda annars)."""
    v = lage.varld()
    fragedir.mkdir(parents=True, exist_ok=True)
    ut: dict[str, Path] = {}
    for namn, text in (
        (FRAGA_DAGSLJUS, text_dagsljus(v, fragedir, varld_dir, riktningar=lage.riktningar)),
        (FRAGA_VATTEN, text_vatten(v, fragedir, varld_dir)),
        (FRAGA_EKOLOGI, text_ekologi(v, fragedir, varld_dir)),
        (FRAGA_LUFT, text_luft(v, fragedir, varld_dir)),
    ):
        fil = fragedir / f"{namn}.toml"
        if not fil.is_file() or fil.read_text(encoding="utf-8") != text:
            fil.write_text(text, encoding="utf-8")
        ut[namn] = fil
    return ut


def kor_demo(
    *,
    tjanst: FragaTjanst,
    varld_dir: Path,
    fragedir: Path,
    lage: DemoLage = NORMAL,
    tvinga: bool = False,
    skriv_fragefiler: bool = True,
    logg: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Hela demon, körd genom webbens frågetjänst (`tjanst`; `fragedir` ska ingå i dess
    frågemappar). Returnerar en sammanfattning (även skriven som K0-manifest i `varld_dir`)."""
    t0 = time.perf_counter()
    byggd = bygg_varld(varld_dir, lage, tvinga=tvinga, logg=logg)
    t_varld = time.perf_counter() - t0
    logg(f"Världen: {'byggd' if byggd else 'oförändrad'} ({t_varld:.1f} s)")
    skriv_luftserier(varld_dir)  # påhittade timserier för luftdemon (billigt)
    fragor = (
        skriv_fragor(fragedir, varld_dir, lage)
        if skriv_fragefiler
        else {n: fragedir / f"{n}.toml" for n in DEMOFRAGOR}
    )
    delar: dict[str, Any] = {}
    for namn in DEMOFRAGOR:
        t1 = time.perf_counter()
        status, oforandrad = tjanst.kor_synkront(namn, namn.replace("_", "-"))
        delar[namn] = {
            "ref": namn,
            "korning_id": status["korning_id"],
            "jobb": status["jobb_totalt"],
            "oforandrad": oforandrad,
            "tid_s": round(time.perf_counter() - t1, 3),
        }
        logg(
            f"{namn}: {status['jobb_totalt']} jobb, {'oförändrad' if oforandrad else 'körd'} "
            f"({delar[namn]['tid_s']:.1f} s) -> körning {status['korning_id']}"
        )
    total = time.perf_counter() - t0
    # Luft är inte förkörd (mätt: över 9 min för hela världen, över 6 min för fokusområdet).
    # Platsen finns i manifestet och i webbens demoöversikt.
    sammanfattning = {
        "lage": lage.namn,
        "varld": {"byggd": byggd, "nyckel": varld_nyckel(lage), "tid_s": round(t_varld, 3)},
        "delar": {
            **delar,
            "luft": {
                "status": "ej_forkord",
                "text": (
                    "Vindkomfort (luft.vindkomfort) finns men är inte förkörd: en körning tar "
                    "mer än tio minuter på CPU, också för bara fokusområdet. Se "
                    "exempel/fragor/luft_fore_efter.toml."
                ),
            },
        },
        "total_tid_s": round(total, 3),
    }
    manifest = skapa_manifest(
        modul="demo",
        indata=[
            FilPost(roll=f"fraga:{n}", fil=str(f), sha256=sha256_fil(f)) for n, f in fragor.items()
        ]
        + [FilPost(roll="varld", fil=VARLD_JSON, sha256=sha256_fil(varld_dir / VARLD_JSON))],
        parametrar={
            "lage": lage.namn,
            "kodversion_varld": w.KODVERSION,
            "varld_nyckel": varld_nyckel(lage),
        },
        utdata=[],
        matvarden={k: v for k, v in sammanfattning.items() if k != "delar"}
        | {"delar": {n: d for n, d in delar.items()}},
    )
    skriv_manifest(manifest, varld_dir / "demo.run.json")
    (varld_dir / "demo.json").write_text(
        json.dumps(sammanfattning, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _ = hamta_kodversion
    return sammanfattning
