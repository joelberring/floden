"""Mätning för B3/B6: profiler per sekund och banor per sekund på en syntetisk scen.

Kör från repots rot:

    PYTHONPATH=src python bench/buller/b3_b6_matning.py

Resultatet skrivs till `bench/buller/b3_b6_matning.json`. Tiderna beror på maskinen;
filen anger därför plattform och biblioteksversioner.
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from buller.banor.banfinnare import bygg_banuppsattning
from buller.banor.korridor import Korridorindex
from buller.scen.fasad import fasadpunkter
from buller.scen.kallsegment import kallsegment_fran_vagar
from buller.scen.profil import profiler_batch
from buller.scen.syntetisk import syntetisk_scen

UTFIL = Path(__file__).resolve().parent / "b3_b6_matning.json"

SIDA_M = 1000.0
CELL_M = 0.5
TOMT_M = 50.0
FRO = 2026
ANTAL_SNITT = 100_000
SNITT_PER_ANROP = 20_000
STEG_M = 1.0
MAXRADIE_M = 300.0
ANTAL_MOTTAGARE = 60
KALLSEGMENT_M = 10.0
HALVBREDD_M = 5.0
ANTAL_RUTOR = 200


def _fria_punkter(scen: Any, slump: np.random.Generator, antal: int) -> tuple[Any, Any]:
    e_ut: list[Any] = []
    n_ut: list[Any] = []
    kvar = antal
    while kvar > 0:
        e = scen.e_min_m + slump.uniform(2.0, SIDA_M - 2.0, antal)
        n = scen.n_min_m + slump.uniform(2.0, SIDA_M - 2.0, antal)
        fri = ~scen.i_byggnad(e, n)
        e_ut.append(e[fri])
        n_ut.append(n[fri])
        kvar -= int(np.count_nonzero(fri))
    return np.concatenate(e_ut)[:antal], np.concatenate(n_ut)[:antal]


def main() -> None:
    t0 = time.perf_counter()
    scen = syntetisk_scen(sida_m=SIDA_M, cell_m=CELL_M, tomt_m=TOMT_M, fro=FRO)
    scen.vaggar  # noqa: B018 - bygg väggtabell och träd före mätningen
    scen.byggnadstrad  # noqa: B018
    tid_scen_s = time.perf_counter() - t0

    # --- profiler -------------------------------------------------------------------
    slump = np.random.default_rng(FRO)
    se, sn = _fria_punkter(scen, slump, ANTAL_SNITT)
    riktning = slump.uniform(0.0, 2.0 * np.pi, ANTAL_SNITT)
    langd = slump.uniform(10.0, MAXRADIE_M, ANTAL_SNITT)
    te = np.clip(se + langd * np.cos(riktning), scen.e_min_m + 2.0, scen.e_max_m - 2.0)
    tn = np.clip(sn + langd * np.sin(riktning), scen.n_min_m + 2.0, scen.n_max_m - 2.0)
    giltig = ~scen.i_byggnad(te, tn) & (np.hypot(te - se, tn - sn) > 1.0)
    se, sn, te, tn = se[giltig], sn[giltig], te[giltig], tn[giltig]
    antal_snitt = int(se.shape[0])

    tid_batch_s = 0.0
    tid_objekt_s = 0.0
    antal_punkter = 0
    antal_med_byggnad = 0
    for start in range(0, antal_snitt, SNITT_PER_ANROP):
        slut = min(antal_snitt, start + SNITT_PER_ANROP)
        t0 = time.perf_counter()
        batch = profiler_batch(
            scen, se[start:slut], sn[start:slut], 0.5, te[start:slut], tn[start:slut], 2.0,
            steg_m=STEG_M,
        )  # fmt: skip
        t1 = time.perf_counter()
        profiler = batch.profiler()
        t2 = time.perf_counter()
        tid_batch_s += t1 - t0
        tid_objekt_s += t2 - t1
        antal_punkter += int(batch.x_m.shape[0])
        antal_med_byggnad += sum(int(np.max(p.z_m) - np.min(p.z_m) > 5.5) for p in profiler)

    # --- banor ----------------------------------------------------------------------
    alla_mottagare = fasadpunkter(
        scen,
        avstand_fran_fasad_m=2.0,
        punktavstand_m=10.0,
        forsta_vaning_hojd_m=2.0,
        vaningshojd_m=3.0,
        max_vaningar=1,
    ).mottagare
    urval = np.sort(slump.choice(len(alla_mottagare), ANTAL_MOTTAGARE, replace=False))
    mottagare = [alla_mottagare[int(i)] for i in urval]
    kallor = kallsegment_fran_vagar(scen.vagar, max_langd_m=KALLSEGMENT_M, delkallhojder_m=[0.5])
    t0 = time.perf_counter()
    direkta = bygg_banuppsattning(
        scen, mottagare, kallor, maxradie_m=MAXRADIE_M, steg_m=STEG_M, reflexordning=0
    )
    tid_direkta_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    uppsattning = bygg_banuppsattning(scen, mottagare, kallor, maxradie_m=MAXRADIE_M, steg_m=STEG_M)
    tid_banor_s = time.perf_counter() - t0
    antal_banor = len(uppsattning.banor)
    antal_reflex = sum(b.typ == "reflex" for b in uppsattning.banor)

    # --- korridorindex --------------------------------------------------------------
    t0 = time.perf_counter()
    index = Korridorindex(uppsattning.banor, HALVBREDD_M)
    tid_index_s = time.perf_counter() - t0
    linjer = np.array(
        [shapely.LineString(np.column_stack([b.plan_e_m, b.plan_n_m])) for b in uppsattning.banor],
        dtype=object,
    )
    rutor = []
    for _ in range(ANTAL_RUTOR):
        sida = float(slump.choice([5.0, 20.0, 50.0]))
        e0 = scen.e_min_m + float(slump.uniform(0.0, SIDA_M - sida))
        n0 = scen.n_min_m + float(slump.uniform(0.0, SIDA_M - sida))
        rutor.append((e0, n0, e0 + sida, n0 + sida))
    t0 = time.perf_counter()
    funna = [index.berorda_banor(*ruta) for ruta in rutor]
    tid_fragor_s = time.perf_counter() - t0
    exakta_totalt = 0
    funna_totalt = 0
    missade = 0
    for ruta, traffar in zip(rutor, funna, strict=True):
        exakta = np.flatnonzero(shapely.distance(linjer, shapely.box(*ruta)) <= HALVBREDD_M)
        missade += int(np.setdiff1d(exakta, traffar).size)
        exakta_totalt += int(exakta.size)
        funna_totalt += int(traffar.size)

    resultat = {
        "paket": "B3/B6",
        "datum": time.strftime("%Y-%m-%d"),
        "miljo": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "shapely": shapely.__version__,
            "plattform": platform.platform(),
            "processorer": os.cpu_count(),
            "anm": "en tråd, ingen GPU",
        },
        "scen": {
            "typ": "buller.scen.syntetisk.syntetisk_scen",
            "sida_m": SIDA_M,
            "cell_m": CELL_M,
            "rasterceller": int(scen.terrang_rh2000_m.size),
            "tomt_m": TOMT_M,
            "fro": FRO,
            "byggnader": len(scen.byggnader),
            "vaggar": scen.vaggar.antal,
            "vagar": len(scen.vagar),
            "tid_bygga_scen_s": round(tid_scen_s, 3),
        },
        "profiler": {
            "antal_snitt": antal_snitt,
            "snitt_per_anrop": SNITT_PER_ANROP,
            "steg_m": STEG_M,
            "snittlangd_m": [10.0, MAXRADIE_M],
            "medellangd_m": round(float(np.mean(np.hypot(te - se, tn - sn))), 1),
            "punkter_per_profil": round(antal_punkter / antal_snitt, 1),
            "andel_snitt_genom_byggnad": round(antal_med_byggnad / antal_snitt, 3),
            "tid_batch_s": round(tid_batch_s, 3),
            "tid_terrangprofil_objekt_s": round(tid_objekt_s, 3),
            "profiler_per_s_batch": round(antal_snitt / tid_batch_s),
            "profiler_per_s_med_objekt": round(antal_snitt / (tid_batch_s + tid_objekt_s)),
        },
        "banor": {
            "mottagare": len(mottagare),
            "kallsegment": len(kallor),
            "kallsegment_maxlangd_m": KALLSEGMENT_M,
            "maxradie_m": MAXRADIE_M,
            "steg_m": STEG_M,
            "antal_banor": antal_banor,
            "varav_direkta": antal_banor - antal_reflex,
            "varav_reflex": antal_reflex,
            "tid_s": round(tid_banor_s, 3),
            "banor_per_s": round(antal_banor / tid_banor_s),
            "bara_direkta_antal": len(direkta.banor),
            "bara_direkta_tid_s": round(tid_direkta_s, 3),
            "bara_direkta_banor_per_s": round(len(direkta.banor) / tid_direkta_s),
        },
        "korridorindex": {
            "halvbredd_m": HALVBREDD_M,
            "cell_m": index.cell_m,
            "tid_bygga_s": round(tid_index_s, 3),
            "antal_rutor": ANTAL_RUTOR,
            "rutsidor_m": [5.0, 20.0, 50.0],
            "tid_per_fraga_ms": round(1000.0 * tid_fragor_s / ANTAL_RUTOR, 3),
            "berorda_banor_exakt": exakta_totalt,
            "berorda_banor_index": funna_totalt,
            "missade": missade,
            "andel_falska_traffar": round((funna_totalt - exakta_totalt) / max(funna_totalt, 1), 5),
        },
    }
    UTFIL.write_text(json.dumps(resultat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(resultat, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
