"""Mätning av ett fall: uppvärmning, körtid (median), minnestopp (RSS och tracemalloc).

Körs i en egen process per fall (`benchmark.barn`) så att RSS-toppen bara gäller fallet.

Mått:

* `tid_s`: `time.perf_counter`, `upprepningar` körningar efter `uppvarmning` körningar som kastas.
  Medianen är värdet som jämförs.
* `rss_topp_mb`: processens högsta RSS (`VmHWM` på Linux, annars `ru_maxrss`) efter
  uppvärmning och tidtagning, före minnesmätningen med tracemalloc. Det räknar med Python, numpy,
  scipy och bibliotekens egna allokeringar, men också med interpretatorn och importerna.
* `rss_start_mb`: samma mått innan fallet körts (efter att benchmarkkoden laddats). `rss_okning_mb`
  är skillnaden och är det som tillhör fallet (inklusive dess importer).
* `tracemalloc_topp_mb`: högsta Python-allokerade minne under en extra körning med tracemalloc
  (numpys buffertar räknas, men inte allokeringar i C-bibliotek som SuperLU). Tidtagningen sker
  inte under tracemalloc eftersom det gör Python-slingor märkbart långsammare.
"""

from __future__ import annotations

import resource
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class TidsgransOverskriden(Exception):
    """Fallets första körning tog längre än fallets tidsgräns."""


def rss_topp_mb() -> float:
    """Processens högsta RSS hittills i MB.

    På Linux läses `VmHWM` ur /proc/self/status (den nuvarande adressrymdens topp). `ru_maxrss`
    ärver nämligen föräldraprocessens topp över exec, så en barnprocess från en stor förälder
    skulle få förälderns siffra som golv. Annars `ru_maxrss` (kB på Linux, byte på macOS)."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for rad in fh:
                if rad.startswith("VmHWM:"):
                    return int(rad.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    v = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return v / (1024.0 * 1024.0) if sys.platform == "darwin" else v / 1024.0


@dataclass(frozen=True)
class Matning:
    tider_s: tuple[float, ...]
    rss_start_mb: float
    rss_topp_mb: float
    tracemalloc_topp_mb: float | None
    utfall: dict[str, Any]

    def tid_dict(self) -> dict[str, Any]:
        return {
            "median": statistics.median(self.tider_s),
            "min": min(self.tider_s),
            "max": max(self.tider_s),
            "n": len(self.tider_s),
            "alla": [round(t, 6) for t in self.tider_s],
        }

    def minne_dict(self) -> dict[str, Any]:
        return {
            "rss_start_mb": round(self.rss_start_mb, 1),
            "rss_topp_mb": round(self.rss_topp_mb, 1),
            "rss_okning_mb": round(max(0.0, self.rss_topp_mb - self.rss_start_mb), 1),
            "tracemalloc_topp_mb": (
                None if self.tracemalloc_topp_mb is None else round(self.tracemalloc_topp_mb, 2)
            ),
        }


def mat(
    fn: Callable[[], dict[str, Any]],
    *,
    uppvarmning: int = 1,
    upprepningar: int = 5,
    tidsgrans_s: float | None = None,
    med_tracemalloc: bool = True,
) -> Matning:
    """Mäter `fn` (som returnerar fallets utfall: avvikelse och extra). Se modulens text."""
    if uppvarmning < 0 or upprepningar < 1:
        raise ValueError("uppvarmning måste vara >= 0 och upprepningar >= 1.")
    rss_start = rss_topp_mb()
    utfall: dict[str, Any] = {}
    for i in range(uppvarmning):
        t0 = time.perf_counter()
        utfall = fn()
        if i == 0 and tidsgrans_s is not None and time.perf_counter() - t0 > tidsgrans_s:
            raise TidsgransOverskriden(
                f"första körningen tog {time.perf_counter() - t0:.1f} s (gräns {tidsgrans_s:g} s)"
            )
    tider: list[float] = []
    for _ in range(upprepningar):
        t0 = time.perf_counter()
        utfall = fn()
        tider.append(time.perf_counter() - t0)
        if uppvarmning == 0 and tidsgrans_s is not None and tider[-1] > tidsgrans_s:
            raise TidsgransOverskriden(f"körningen tog {tider[-1]:.1f} s (gräns {tidsgrans_s:g} s)")
    rss_topp = rss_topp_mb()
    peak_mb: float | None = None
    if med_tracemalloc:
        tracemalloc.start()
        try:
            fn()
            peak_mb = tracemalloc.get_traced_memory()[1] / (1024.0 * 1024.0)
        finally:
            tracemalloc.stop()
    return Matning(tuple(tider), rss_start, rss_topp, peak_mb, utfall)
