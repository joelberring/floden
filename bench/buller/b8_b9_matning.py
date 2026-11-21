"""Mätning för B8/B9: omkombination, klustring och inkrementell omräkning.

Kör från repots rot:

    PYTHONPATH=src python bench/buller/b8_b9_matning.py

Resultatet skrivs till `bench/buller/b8_b9_matning.json`. Scenen är syntetisk
(`buller.operator.syntetisk`), så tiderna säger något om operatorlagrets egen kostnad men
inget om en verklig kärna. Parberäkningen här kostar under en mikrosekund per par; en verklig
Nord2000-kärna kostar flera storleksordningar mer. Därför redovisas också klustringens fasta
kostnad och brytpunkten (den parkostnad där klustringen börjar löna sig).
"""

from __future__ import annotations

import json
import platform
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from buller.operator import (
    Overforingslager,
    Rektangel,
    a_vagd_niva_db,
    bygg_fullt_lager,
    bygg_overforingslager,
    rakna_om_inkrementellt,
)
from buller.operator.syntetisk import (
    skapa_syntetisk_scen,
    syntetisk_emission_db,
    syntetisk_vagning_db,
)
from buller.typer import Flyttal

ANTAL_MOTTAGARE = 200
ANTAL_KALLOR = 5000
ANTAL_VADERKLASSER = 1
SIDA_M = 2000.0
UPPREPNINGAR = 3


def basta_tid_s(funktion: Callable[[], Any], upprepningar: int = UPPREPNINGAR) -> float:
    tider = []
    for _ in range(upprepningar):
        t0 = time.perf_counter()
        funktion()
        tider.append(time.perf_counter() - t0)
    return min(tider)


def tid_omkombination(lager: Overforingslager, lw_db: Flyttal, vagning_db: Flyttal) -> float:
    return basta_tid_s(lambda: lager.niva_a_db(lw_db, vagning_db))


def tid_felgrans(lager: Overforingslager, lw_db: Flyttal, vagning_db: Flyttal) -> float:
    return basta_tid_s(lambda: lager.felgrans_db(lw_db, vagning_db))


def main() -> None:
    scen = skapa_syntetisk_scen(
        antal_mottagare=ANTAL_MOTTAGARE,
        antal_kallor=ANTAL_KALLOR,
        antal_vaderklasser=ANTAL_VADERKLASSER,
        sida_m=SIDA_M,
    )
    lw_db = syntetisk_emission_db(scen)
    vagning = syntetisk_vagning_db()
    antal_par = ANTAL_MOTTAGARE * ANTAL_KALLOR
    gemensamt: dict[str, Any] = {
        "mottagare_id": scen.mottagare_id,
        "mottagare_e_m": scen.mottagare_e_m,
        "mottagare_n_m": scen.mottagare_n_m,
        "kalla_id": scen.kalla_id,
        "kalla_e_m": scen.kalla_e_m,
        "kalla_n_m": scen.kalla_n_m,
        "vaderklass_vikter": scen.vaderklass_vikter,
        "parberaknare": scen.parberaknare(),
    }

    # --- Full körning och omkombination -------------------------------------------------
    tid_full_s = basta_tid_s(lambda: bygg_fullt_lager(**gemensamt))
    fullt, _ = bygg_fullt_lager(**gemensamt)
    ny_lw_db = lw_db + np.random.default_rng(7).uniform(-5.0, 5.0, size=lw_db.shape)
    tid_omkombination_s = tid_omkombination(fullt, ny_lw_db, vagning)
    facit_a_db = a_vagd_niva_db(scen.referensniva_db(lw_db), vagning)
    fel_omkombination_db = float(
        np.max(
            np.abs(
                fullt.niva_a_db(ny_lw_db, vagning)
                - a_vagd_niva_db(scen.referensniva_db(ny_lw_db), vagning)
            )
        )
    )
    parkostnad_s = tid_full_s / antal_par

    # --- Klustring ----------------------------------------------------------------------
    klustring: list[dict[str, Any]] = []
    for felgrans_db in (0.1, 0.5):

        def bygg(felgrans_db: float = felgrans_db) -> Any:
            return bygg_overforingslager(
                **gemensamt,
                ovre_grans=scen.ovre_grans(),
                lw_db=lw_db,
                a_vagning_db=vagning,
                felgrans_db=felgrans_db,
            )

        tid_s = basta_tid_s(bygg)
        lager, statistik = bygg()
        fel_db = facit_a_db - lager.niva_a_db(lw_db, vagning)
        grans_db = lager.felgrans_db(lw_db, vagning)
        andel = statistik.andel_beraknade_par
        fast_kostnad_s = max(tid_s - andel * tid_full_s, 0.0)
        klustring.append(
            {
                "felgrans_db": felgrans_db,
                "andel_beraknade_par": andel,
                "antal_beraknade_par": statistik.antal_beraknade_par,
                "antal_parberakningsanrop": statistik.antal_parberakningsanrop,
                "antal_restnoder": statistik.antal_restnoder,
                "storsta_uppmatta_fel_db": float(np.max(fel_db)),
                "minsta_uppmatta_fel_db": float(np.min(fel_db)),
                "storsta_garanterade_grans_db": float(np.max(grans_db)),
                "tid_s": tid_s,
                "tidsvinst_syntetisk_parberaknare": tid_full_s / tid_s,
                "tid_omkombination_s": tid_omkombination(lager, ny_lw_db, vagning),
                "tid_felgrans_s": tid_felgrans(lager, ny_lw_db, vagning),
                "harlett": {
                    "forklaring": (
                        "Härlett ur mätningarna, inte mätt: fast kostnad = tid_s - andel * "
                        "tid för full körning. Tidsvinsten med en parberäknare som kostar c "
                        "sekunder per par är N*c / (fast + andel*N*c) och går mot 1/andel."
                    ),
                    "fast_kostnad_s": fast_kostnad_s,
                    "fast_kostnad_per_mottagare_s": fast_kostnad_s / ANTAL_MOTTAGARE,
                    "brytpunkt_parkostnad_s": fast_kostnad_s / (antal_par * (1.0 - andel)),
                    "storsta_mojliga_tidsvinst": 1.0 / andel,
                    "tidsvinst_vid_parkostnad_1_ms": antal_par
                    * 1e-3
                    / (fast_kostnad_s + andel * antal_par * 1e-3),
                },
            }
        )

    # --- Inkrementell omräkning ---------------------------------------------------------
    gammal = scen.byggnad
    ny = Rektangel(
        gammal.e_min_m + 150.0, gammal.n_min_m + 100.0, gammal.e_max_m + 150.0,
        gammal.n_max_m + 100.0,
    )  # fmt: skip
    efter = scen.med_byggnad(ny)
    yta = gammal.union(ny)
    index = efter.korridorindex()
    t0 = time.perf_counter()
    berorda = index.berorda_par(yta.e_min_m, yta.n_min_m, yta.e_max_m, yta.n_max_m)
    tid_index_s = time.perf_counter() - t0

    class FardigtIndex:
        def berorda_par(
            self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
        ) -> set[tuple[int, int]]:
            return berorda

    t0 = time.perf_counter()
    nytt, inkrementell = rakna_om_inkrementellt(fullt, yta, FardigtIndex(), efter.parberaknare())
    tid_inkrementell_s = time.perf_counter() - t0
    gemensamt_efter = {**gemensamt, "parberaknare": efter.parberaknare()}
    tid_full_efter_s = basta_tid_s(lambda: bygg_fullt_lager(**gemensamt_efter))
    full_efter, _ = bygg_fullt_lager(**gemensamt_efter)

    resultat = {
        "paket": "B8-B9",
        "beskrivning": "Syntetisk scen (buller.operator.syntetisk), inte Nord2000.",
        "scen": {
            "antal_mottagare": ANTAL_MOTTAGARE,
            "antal_kallor": ANTAL_KALLOR,
            "antal_par": antal_par,
            "antal_vaderklasser": ANTAL_VADERKLASSER,
            "antal_band": 27,
            "sida_m": SIDA_M,
            "kallor": "10 raka vägar i rutnät, jämnt fördelade punktkällor",
            "byggnad_m": [gammal.e_min_m, gammal.n_min_m, gammal.e_max_m, gammal.n_max_m],
        },
        "miljo": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "maskin": platform.machine(),
            "tidmatning": f"bästa av {UPPREPNINGAR} körningar, en tråd",
        },
        "full_korning": {
            "tid_s": tid_full_s,
            "parkostnad_syntetisk_s": parkostnad_s,
            "antal_par_i_lager": fullt.antal_par,
        },
        "omkombination_andrad_emission": {
            "tid_s": tid_omkombination_s,
            "tid_per_par_s": tid_omkombination_s / fullt.antal_par,
            "andel_av_full_korning_syntetisk": tid_omkombination_s / tid_full_s,
            "storsta_avvikelse_mot_rak_summering_db": fel_omkombination_db,
        },
        "klustring": klustring,
        "inkrementell_flyttad_byggnad": {
            "andrad_yta_m": [yta.e_min_m, yta.n_min_m, yta.e_max_m, yta.n_max_m],
            "antal_omraknade_par": inkrementell.antal_omraknade_par,
            "andel_omraknade_par": inkrementell.andel_omraknade_par,
            "tid_s": tid_inkrementell_s,
            "tid_korridorindex_syntetiskt_s": tid_index_s,
            "tid_full_omrakning_s": tid_full_efter_s,
            "tidsvinst_syntetisk_parberaknare": tid_full_efter_s / tid_inkrementell_s,
            "lika_med_full_omrakning": nytt.identitet == full_efter.identitet,
        },
    }
    mal = Path(__file__).resolve().parent / "b8_b9_matning.json"
    mal.write_text(json.dumps(resultat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(resultat, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
