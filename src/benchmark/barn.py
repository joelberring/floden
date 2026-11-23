"""Barnprocess: kör och mäter ett enda fall och skriver resultatet som JSON på stdout.

    python -m benchmark.barn <del> <fall-id> <uppvarmning> <upprepningar>

Varje fall får en egen process så att RSS-toppen (`ru_maxrss`) bara gäller fallet.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from benchmark.fall import FALL
from benchmark.matare import TidsgransOverskriden, mat


def main(argv: list[str]) -> int:
    del_namn, fall_id, uppv, uppr = argv[0], argv[1], int(argv[2]), int(argv[3])
    fall = next(f for f in FALL[del_namn] if f.id == fall_id)
    if fall.max_upprepningar is not None:
        uppr = min(uppr, fall.max_upprepningar)
    ut: dict[str, Any]
    try:
        m = mat(fall.funktion, uppvarmning=uppv, upprepningar=uppr, tidsgrans_s=fall.tidsgrans_s)
    except TidsgransOverskriden as exc:
        ut = {
            "id": fall.id,
            "namn": fall.namn,
            "status": "hoppas_over",
            "skal": f"överskred tidsgränsen: {exc}",
        }
    except Exception as exc:  # noqa: BLE001 - felet ska redovisas i dokumentet, inte döda svit
        ut = {
            "id": fall.id,
            "namn": fall.namn,
            "status": "fel",
            "skal": f"{type(exc).__name__}: {exc}",
            "spar": traceback.format_exc()[-1500:],
        }
    else:
        ut = {
            "id": fall.id,
            "namn": fall.namn,
            "status": "ok",
            "tid_s": m.tid_dict(),
            "minne": m.minne_dict(),
            "avvikelse": m.utfall.get("avvikelse"),
            "extra": m.utfall.get("extra", {}),
            "uppvarmning": uppv,
        }
    sys.stdout.write("\n@@FLODEN-BENCHMARK@@" + json.dumps(ut, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
