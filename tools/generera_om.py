"""Genererar docs/OM-FLODEN.md och webb/src/om/data.generated.ts ur data/om/floden.toml.

    python tools/generera_om.py                 # skriv
    python tools/generera_om.py --kontrollera   # felkod 1 om filerna glidit isär

Källor: data/om/floden.toml, data/matematik/metoder.toml och data/mcp/metodstatus.toml
(se src/gemensamt/om_floden.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gemensamt import metodregister as mr  # noqa: E402
from gemensamt import om_floden as om  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kontrollera", action="store_true")
    args = ap.parse_args()
    innehall = om.las_om()
    reg = mr.las_register()
    ms = mr.las_metodstatus()
    fel = om.kontrollera(innehall, reg, ms)
    if fel:
        print("\n".join(fel))
        return 1
    ny_md = om.markdown(innehall, reg, ms)
    ny_ts = om.ts_innehall(innehall, reg, ms)
    if args.kontrollera:
        ok = (
            om.OM_MD.is_file()
            and om.OM_MD.read_text(encoding="utf-8") == ny_md
            and om.TS_UT.is_file()
            and om.TS_UT.read_text(encoding="utf-8") == ny_ts
        )
        if not ok:
            print("Genererade filer är inte i takt med data/om/floden.toml: kör generera_om.py")
        return 0 if ok else 1
    om.OM_MD.write_text(ny_md, encoding="utf-8")
    om.TS_UT.parent.mkdir(parents=True, exist_ok=True)
    om.TS_UT.write_text(ny_ts, encoding="utf-8")
    print(f"Skrev {om.OM_MD} och {om.TS_UT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
