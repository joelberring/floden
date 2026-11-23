"""Kör benchmarkens fall i egna processer och sätter ihop dokumentet `floden-benchmark/v1`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from benchmark.fall import FALL, KRAVER_KALLMAPP
from benchmark.format import DELAR, maskininfo, skapa_dokument

MARKOR = "@@FLODEN-BENCHMARK@@"
SRC_MAPP = Path(__file__).resolve().parents[1]
BARN_TIDSGRANS_S = 1800.0

ANMARKNING_STANDARD = (
    "Ungefärliga siffror: tiderna beror på maskinen och på vad som annars körs där. "
    "Tid = median av upprepningar efter uppvärmning. Minne: RSS-topp (VmHWM/ru_maxrss, egen "
    "process "
    "per fall) och tracemalloc-topp (separat körning). Se docs/benchmark/README.md."
)


class BenchmarkFel(Exception):
    """Ett fall eller en del kunde inte köras på ett sätt som ska stoppa svit."""


def kor_fall_i_barn(
    del_namn: str, fall_id: str, uppvarmning: int, upprepningar: int
) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_MAPP), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    try:
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmark.barn",
                del_namn,
                fall_id,
                str(uppvarmning),
                str(upprepningar),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=BARN_TIDSGRANS_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "id": fall_id,
            "status": "hoppas_over",
            "skal": f"barnprocessen överskred {BARN_TIDSGRANS_S:g} s",
        }
    for rad in reversed(p.stdout.splitlines()):
        if rad.startswith(MARKOR):
            res: dict[str, Any] = json.loads(rad[len(MARKOR) :])
            return res
    return {
        "id": fall_id,
        "status": "fel",
        "skal": f"barnprocessen gav inget resultat (returkod {p.returncode}): "
        + (p.stderr.strip().splitlines() or ["tomt stderr"])[-1][:300],
    }


def kor_svit(
    delar: Sequence[str],
    *,
    uppvarmning: int = 1,
    upprepningar: int = 5,
    kodversion: str | None = None,
    logg: Callable[[str], None] = lambda _t: None,
) -> dict[str, Any]:
    """Kör valda delar och returnerar ett kontrollerat dokument (K37)."""
    if kodversion is None:
        from gemensamt.manifest import hamta_kodversion

        kodversion = hamta_kodversion(SRC_MAPP.parent)
    ut: dict[str, Any] = {}
    for namn in delar:
        if namn not in DELAR:
            raise BenchmarkFel(f"Okänd del {namn!r}; välj bland {', '.join(DELAR)}.")
        fall_ut: list[dict[str, Any]] = []
        for fall in FALL.get(namn, ()):
            logg(f"  {namn}/{fall.id} ...")
            r = kor_fall_i_barn(namn, fall.id, uppvarmning, upprepningar)
            r.setdefault("namn", fall.namn)
            fall_ut.append(r)
        for fid, fnamn, skal in KRAVER_KALLMAPP.get(namn, ()):
            fall_ut.append({"id": fid, "namn": fnamn, "status": "hoppas_over", "skal": skal})
        if not any(f["status"] == "ok" for f in fall_ut) and KRAVER_KALLMAPP.get(namn):
            ut[namn] = {
                "status": "kraver_lokal_kallmapp",
                "skal": "kräver lokal källmapp",
                "fall": fall_ut,
            }
        elif not fall_ut:
            ut[namn] = {"status": "hoppas_over", "skal": "inga fall definierade", "fall": []}
        else:
            ut[namn] = {"status": "kord", "skal": None, "fall": fall_ut}
    return skapa_dokument(
        kalla="server",
        kodversion=kodversion,
        maskin=maskininfo(),
        installning={
            "uppvarmning": uppvarmning,
            "upprepningar": upprepningar,
            "tidsmatt": "median av upprepningar, time.perf_counter",
            "minnesmatt": [
                "rss_topp_mb: VmHWM (Linux) eller ru_maxrss, egen process per fall",
                "tracemalloc_topp_mb: separat körning",
            ],
        },
        delar=ut,
        anmarkning=ANMARKNING_STANDARD,
    )


def skriv_dokument(dok: dict[str, Any], sokvag: Path) -> None:
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    sokvag.write_text(json.dumps(dok, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
