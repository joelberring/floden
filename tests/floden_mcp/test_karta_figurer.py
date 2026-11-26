"""Regressionstester för B12: floden_mcp.karta använder rapport.figurer som kartmotor."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from floden_mcp.karta import rendera_karta
from rapport.figurer import FARG_BAKGRUND, FIGURSTILAR

REPO = Path(__file__).resolve().parents[2]


def _lager(
    *,
    roll: str,
    doman: str,
    etikett: str,
    enhet: str,
    farg: str,
    lo: float,
    hi: float,
    typ: str = "bas",
    teckenforklaring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tf = teckenforklaring or {
        "typ": "kontinuerlig",
        "min": lo,
        "max": hi,
        "stopp": ["#00204d", "#ffea46"],
    }
    post: dict[str, Any] = {
        "id": f"a.{typ}.{roll}",
        "analys": "a",
        "scenario": "fore" if typ == "bas" else "efter",
        "roll": roll,
        "doman": doman,
        "etikett": etikett,
        "enhet": enhet,
        "typ": typ,
        "farg": farg,
        "skala": {"min": lo, "max": hi},
        "teckenforklaring": tf,
        "utbredning": {
            "x_min": 152000.0,
            "y_min": 6578000.0,
            "x_max": 152080.0,
            "y_max": 6578060.0,
        },
        "pixelstorlek_m": 2.0,
    }
    if typ == "skillnad":
        post["fore"] = "fore"
        post["efter"] = "efter"
    return post


def test_karta_modul_anvander_inte_bitmapfont_eller_hardskriven_djuptext() -> None:
    """B12: floden_mcp/karta.py ska delegera till rapport.figurer utan ImageFont.load_default."""
    kallkod = (REPO / "src" / "floden_mcp" / "karta.py").read_text(encoding="utf-8")
    assert "load_default" not in kallkod
    assert "Under 0,02 m: ej markerat" not in kallkod
    assert "rapport.figurer" in kallkod


def test_rendera_karta_anvander_figurer_och_ger_distinkta_farger_for_alla_amnen() -> None:
    """Verifierar deterministisk rendering via rapport.figurer för alla domänstilar."""
    fall: list[tuple[dict[str, Any], np.ndarray[Any, Any], str]] = [
        (
            _lager(
                roll="svf",
                doman="dagsljus",
                etikett="Andel himmel som syns",
                enhet="",
                farg="sekventiell",
                lo=0.0,
                hi=1.0,
            ),
            np.linspace(0.05, 0.95, 30 * 40, dtype=np.float32).reshape((30, 40)),
            "dagsljus_svf",
        ),
        (
            _lager(
                roll="tid_till_0_1m",
                doman="vatten",
                etikett="Tid till 10 cm djup",
                enhet="s",
                farg="tid",
                lo=0.0,
                hi=3600.0,
            ),
            np.linspace(120.0, 3600.0, 30 * 40, dtype=np.float32).reshape((30, 40)),
            "skyfall_ankomsttid",
        ),
        (
            _lager(
                roll="komfortklass",
                doman="luft",
                etikett="Vindkomfort (klass)",
                enhet="klass",
                farg="klass",
                lo=0.0,
                hi=4.0,
                teckenforklaring={
                    "typ": "kategorier",
                    "kategorier": [
                        {"varde": i, "farg": "#2e7d32", "etikett": ""} for i in range(5)
                    ],
                },
            ),
            (np.arange(30 * 40, dtype=np.float32) % 5).reshape((30, 40)),
            "luft_vindkomfort",
        ),
        (
            _lager(
                roll="soltimmar",
                doman="dagsljus",
                etikett="Soltimmar på marken",
                enhet="h",
                farg="sekventiell",
                lo=0.0,
                hi=10.0,
            ),
            np.linspace(0.0, 10.0, 30 * 40, dtype=np.float32).reshape((30, 40)),
            "dagsljus_soltimmar",
        ),
    ]

    for la, raster, forvantad_stil in fall:
        png1, meta1 = rendera_karta(
            raster=raster,
            tak=None,
            lager=la,
            byggnader=None,
            korning_id="k-test-1",
            kodversion="abc1234",
            skiss=True,
            max_px=400,
        )
        png2, _ = rendera_karta(
            raster=raster,
            tak=None,
            lager=la,
            byggnader=None,
            korning_id="k-test-1",
            kodversion="abc1234",
            skiss=True,
            max_px=400,
        )
        assert hashlib.sha256(png1).hexdigest() == hashlib.sha256(png2).hexdigest()
        assert meta1["kartmotor"] == "rapport.figurer"
        assert meta1["figurstil"] == forvantad_stil
        assert meta1["norr"] == "uppat"
        assert float(meta1["skalstock_m"]) > 0

        with Image.open(io.BytesIO(png1)) as img:
            assert img.format == "PNG"
            assert img.getpixel((2, 2)) == FARG_BAKGRUND
            arr = np.asarray(img)
            stil = FIGURSTILAR[forvantad_stil]
            traffade_klasser = sum(
                1
                for k in stil.klasser
                if np.any(np.all(arr == np.array(k.farg, dtype=np.uint8), axis=-1))
            )
            assert traffade_klasser == len(stil.klasser), forvantad_stil
