"""Genererar metodtabellen i docs/MATEMATIK.md och webb/src/matematik/data.generated.ts.

    python tools/generera_matematik.py                 # skriv
    python tools/generera_matematik.py --kontrollera   # felkod 1 om filerna glidit isär

Källor: data/matematik/metoder.toml och data/mcp/metodstatus.toml
(se src/gemensamt/metodregister.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gemensamt import metodregister as mr  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kontrollera", action="store_true")
    args = ap.parse_args()
    reg = mr.las_register()
    ms = mr.las_metodstatus()
    fel = mr.kontrollera(reg, ms)
    if fel:
        print("\n".join(fel))
        return 1
    gammal_md = mr.MATEMATIK_MD.read_text(encoding="utf-8")
    ny_md = mr.md_med_tabell(gammal_md, mr.markdown_block(reg, ms))
    ny_ts = mr.ts_innehall(reg, ms)
    if args.kontrollera:
        ok = mr.MATEMATIK_MD.read_text(encoding="utf-8") == ny_md and (
            mr.TS_UT.is_file() and mr.TS_UT.read_text(encoding="utf-8") == ny_ts
        )
        if not ok:
            print("Genererade filer är inte i takt med registret: kör generera_matematik.py")
        return 0 if ok else 1
    mr.MATEMATIK_MD.write_text(ny_md, encoding="utf-8")
    mr.TS_UT.parent.mkdir(parents=True, exist_ok=True)
    mr.TS_UT.write_text(ny_ts, encoding="utf-8")
    print(f"Skrev {mr.MATEMATIK_MD} och {mr.TS_UT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
