"""Hela kedjan scen -> banor -> kärna -> operatorlager -> skisskörning, med mätning.

Emissionen är syntetisk (samma ljudeffekt i alla källor och band) och kärnan är baslinjen,
som inte är Nord2000. Nivåerna är därför inga bullernivåer; skriptet prövar bara att delarna
hänger ihop och mäter vad ett par kostar med en verklig kärna.

Kör: python bench/buller/helkedja.py [sida_m] [antal_mottagare]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from buller.akustik import BaslinjeKarna  # noqa: E402
from buller.akustik.band import a_vagning_db  # noqa: E402
from buller.banor.banfinnare import bygg_banuppsattning  # noqa: E402
from buller.lagen.korning import kor_buller  # noqa: E402
from buller.lagen.profil import las_inbyggd_profil  # noqa: E402
from buller.scen.fasad import fasadpunkter  # noqa: E402
from buller.scen.kallsegment import kallsegment_fran_vagar  # noqa: E402
from buller.scen.syntetisk import syntetisk_scen  # noqa: E402
from buller.typer import ANTAL_BAND, Vader  # noqa: E402

SYNTETISK_LW_DB = 90.0  # påhittad ljudeffekt per källa och band, endast för mätning


def kor(sida_m: float, antal_mottagare: int) -> dict[str, object]:
    scen = syntetisk_scen(sida_m=sida_m, cell_m=1.0, fro=3)
    alla = fasadpunkter(
        scen,
        avstand_fran_fasad_m=2.0,
        punktavstand_m=10.0,
        forsta_vaning_hojd_m=2.0,
        vaningshojd_m=3.0,
        max_vaningar=1,
    )
    mottagare = list(alla.mottagare)[:: max(1, len(alla.mottagare) // antal_mottagare)]
    mottagare = mottagare[:antal_mottagare]
    kallor = kallsegment_fran_vagar(scen.vagar, max_langd_m=10.0, delkallhojder_m=(0.3,))
    vader = (Vader(),)
    karna = BaslinjeKarna()
    profil = las_inbyggd_profil("skiss")

    t0 = time.perf_counter()
    banor = bygg_banuppsattning(scen, mottagare, kallor, maxradie_m=sida_m * 2.0)
    t_banor = time.perf_counter() - t0
    parber = banor.parberaknare(karna, vader)
    lw = np.full((len(kallor), ANTAL_BAND), SYNTETISK_LW_DB)
    a = a_vagning_db()

    # Oklustrad referens: alla par genom kärnan.
    t0 = time.perf_counter()
    sann = np.empty(len(mottagare))
    alla_index = list(range(len(kallor)))
    for m in range(len(mottagare)):
        h = parber(m, alla_index)[:, 0, :]
        with np.errstate(divide="ignore"):
            energi = np.sum(10.0 ** ((lw + h + a) / 10.0))
            sann[m] = 10.0 * np.log10(energi)
    t_full = time.perf_counter() - t0
    antal_par = len(mottagare) * len(kallor)

    t0 = time.perf_counter()
    korning = kor_buller(
        mottagare=mottagare,
        kallor=kallor,
        lw_db=lw,
        vaderklasser=vader,
        parberaknare=parber,
        ovre_grans=banor.ovre_grans_par(karna.ovre_grans_db),
        a_vagning_db=a,
        karna=karna,
        profil=profil,
        reflexordning=1,
        antal_banor=len(banor.banor),
    )
    t_skiss = time.perf_counter() - t0
    res = korning.resultat
    fel = sann - res.niva_a_db
    matv = korning.manifest.matvarden
    return {
        "scen": {"sida_m": sida_m, "byggnader": len(scen.byggnader), "cell_m": 1.0},
        "antal_mottagare": len(mottagare),
        "antal_kallor": len(kallor),
        "antal_par": antal_par,
        "tid_banor_s": round(t_banor, 3),
        "tid_full_s": round(t_full, 3),
        "mikrosekunder_per_par_full": round(1e6 * t_full / antal_par, 1),
        "tid_skiss_s": round(t_skiss, 3),
        "tidsvinst_skiss_mot_full": round(t_full / t_skiss, 2),
        "felgrans_profil_db": profil.klustring_felgrans_db
        if hasattr(profil, "klustring_felgrans_db")
        else None,
        "storsta_fel_db": float(np.max(fel)),
        "minsta_fel_db": float(np.min(fel)),
        "storsta_garanterade_grans_db": float(np.max(res.felgrans_db)),
        "fel_inom_grans": bool(np.all(fel <= res.felgrans_db + 1e-9) and np.all(fel >= -1e-9)),
        "markning": res.markning,
        "manifest_matvarden": {k: v for k, v in matv.items() if not isinstance(v, (list, dict))},
    }


if __name__ == "__main__":
    sida = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    antal = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    ut = kor(sida, antal)
    print(json.dumps(ut, indent=2, ensure_ascii=False))
    (Path(__file__).parent / "helkedja_matning.json").write_text(
        json.dumps(ut, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
