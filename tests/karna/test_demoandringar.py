"""K20 `fokus`, regn ur IDF-källa i `vatten.statisk_fyllning` och takmaskade rasterbilder."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image

from karna.analyslosare import LosarFel, hamta_losare
from karna.korplan import _inom_fokus
from karna.rutscenario import Ruta
from karna.utredning import FragaFel, fraga_fran_dict
from webb.resultatlager import rita_bild

REPO = Path(__file__).resolve().parents[2]
A1 = REPO / "testsvit" / "A1_enkel_sanka" / "indata"


def _fraga(fokus: object) -> dict[str, object]:
    return {
        "kontrakt": "K20/v1",
        "fraga_id": "f",
        "scenarier": {"fore": {"bas": str(A1)}},
        "analyser": [{"id": "a", "losare": "geometri.lutning", "fokus": fokus}],
    }


def test_fokus_laeses_och_valideras() -> None:
    f = fraga_fran_dict(_fraga([0, 0, 10, 20]), REPO)
    assert f.analyser[0].fokus == (0.0, 0.0, 10.0, 20.0)
    assert fraga_fran_dict({**_fraga(None)}, REPO).analyser[0].fokus is None
    for fel in (
        [1, 2, 3],
        [0, 0, 0, 5],
        [5, 0, 1, 5],
        "x",
        [0, 0, float("nan"), 1],
        [True, 0, 1, 1],
    ):
        with pytest.raises(FragaFel, match="fokus"):
            fraga_fran_dict(_fraga(fel), REPO)


def test_inom_fokus_gar_pa_rutans_mittpunkt() -> None:
    r = Ruta(ruta_id="r", mapp=Path("."), hash="h", x_min=0.0, y_min=0.0, x_max=100.0, y_max=100.0)
    assert _inom_fokus(r, None)
    assert _inom_fokus(r, (40.0, 40.0, 60.0, 60.0))
    assert not _inom_fokus(r, (60.0, 0.0, 200.0, 100.0))


def test_idf_parametrar_ger_regn_och_manifestfalt() -> None:
    losare = hamta_losare("vatten.statisk_fyllning")
    p = losare.validera_parametrar(
        {"idf": "stockholmsregn-2024", "aterkomsttid_ar": 100, "varaktighet_min": 360}
    )
    assert p["klimatfaktor"] == 1.4 and p["regntyp"] == "block"
    assert p["idf_kalla"] == "stockholmsregn-2024" and p["idf_sha256"]
    p2 = losare.validera_parametrar(
        {"idf": "stockholmsregn-2024", "aterkomsttid_ar": 100, "klimatfaktor": 1.0}
    )
    assert p2["klimatfaktor"] == 1.0 and p2["varaktighet_min"] == 60.0
    # Det gamla sättet är oförändrat.
    assert losare.validera_parametrar({"regn_mm": 30.0}) == {
        "regn_mm": 30.0,
        "persistens_faktor": None,
    }


@pytest.mark.parametrize(
    "par",
    [
        {"idf": "stockholmsregn-2024", "aterkomsttid_ar": 100, "regn_mm": 30.0},
        {"aterkomsttid_ar": 100, "regn_mm": 30.0},
        {"idf": "finns-inte", "aterkomsttid_ar": 100},
        {"idf": "stockholmsregn-2024", "aterkomsttid_ar": 100, "regntyp": "x"},
        {"idf": 3, "aterkomsttid_ar": 100},
    ],
)
def test_idf_parametrar_avvisar_fel(par: dict[str, object]) -> None:
    with pytest.raises(LosarFel):
        hamta_losare("vatten.statisk_fyllning").validera_parametrar(par)


def _alpha(png: bytes) -> NDArray[np.uint8]:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[:, :, 3]


def test_rita_bild_doljer_takceller_och_ger_synlig_utbredning() -> None:
    r = np.full((20, 30), 0.0, dtype=np.float32)
    r[5:10, 8:12] = 1.0
    tak = np.zeros(r.shape, dtype=np.bool_)
    tak[5:8, 8:12] = True  # delar av de synliga cellerna är tak
    utan = rita_bild(r, "skillnad", lo=-1.0, hi=1.0, troskel=0.1)
    med = rita_bild(r, "skillnad", lo=-1.0, hi=1.0, troskel=0.1, dold=tak)
    assert utan.synlig_px == (8, 5, 12, 10)
    assert med.synlig_px == (8, 8, 12, 10)
    assert (_alpha(utan.png)[5:8, 8:12] > 0).all()
    assert (_alpha(med.png)[5:8, 8:12] == 0).all()
    with pytest.raises(Exception, match="Masken"):
        rita_bild(r, "skillnad", lo=-1.0, hi=1.0, dold=np.zeros((2, 2), dtype=np.bool_))
