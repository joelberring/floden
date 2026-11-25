"""Alla analyser som kartlager: färgskalor, klassning, härledda skillnader och vektorvägarna.

Skalorna testas på små syntetiska raster; vägarna mot demokörningarna (påhittat kvarter).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.demovarld.test_demo import Demo
from webb.resultatlager import (
    _VATTEN_KLASSER,
    KONTROLLROLLER,
    PALETTER,
    SKALA_FOR_ROLL,
    LagerFel,
    Mosaik,
    Utbredning,
    etikett_och_enhet,
    farg_for_roll,
    klass_farger,
    rita_bild,
    skillnad_mosaik,
    teckenforklaring,
)


def _mosaik(v: list[list[float]], x: float = 100.0) -> Mosaik:
    a = np.array(v, dtype=np.float32)
    return Mosaik(a, Utbredning(x, 0.0, x + a.shape[1], float(a.shape[0])), 1.0)


def test_varje_roll_har_en_skala_som_finns() -> None:
    for roll, farg in SKALA_FOR_ROLL.items():
        assert farg in (*PALETTER, "vatten", "klass"), roll
    assert farg_for_roll("max_djup") == "vatten"
    assert farg_for_roll("soltimmar") == "sekventiell"
    assert farg_for_roll("hastighet") == "hastighet"
    assert farg_for_roll("ankomsttid") == "tid"
    assert farg_for_roll("stromtathet") == "stromtathet"
    assert farg_for_roll("max_djup", skillnad=True) == "skillnad"


def test_etikett_och_enhet_for_nya_roller() -> None:
    assert etikett_och_enhet("hastighet") == ("Vattnets hastighet", "m/s")
    assert etikett_och_enhet("arsmedel")[1] == "µg/m³"
    assert etikett_och_enhet("okand_roll") == ("Okand roll", "")
    assert {"trunkering", "standardhojd", "slutsanka"} == KONTROLLROLLER


def test_klassroll_med_for_manga_varden_faller_tillbaka_till_kontinuerlig_skala() -> None:
    assert farg_for_roll("komfortklass", lo=0.0, hi=4.0) == "klass"
    assert farg_for_roll("komfortklass", lo=0.0, hi=40.0) == "sekventiell"
    with pytest.raises(LagerFel):
        klass_farger(0.0, 40.0)


def test_klassfarger_ar_en_per_heltal_fran_gront_till_rott() -> None:
    k = klass_farger(0.0, 4.0)
    assert [v for v, _ in k] == [0, 1, 2, 3, 4]
    assert k[0][1] == "#2e7d32" and k[-1][1] == "#c62828"
    assert len({f for _, f in k}) == 5
    assert klass_farger(2.0, 2.0) == [(2, "#2e7d32")]


def test_klassraster_far_en_farg_per_klass_och_nan_ar_genomskinlig() -> None:
    r = np.array([[0.0, 1.0, 2.0, np.nan]], dtype=np.float32)
    b = rita_bild(r, "klass", lo=0.0, hi=2.0)
    from io import BytesIO

    from PIL import Image

    px = np.array(Image.open(BytesIO(b.png)))
    assert [tuple(px[0, i, :3]) for i in range(3)] == [(46, 125, 50), (253, 216, 53), (198, 40, 40)]
    assert list(px[0, :, 3]) == [255, 255, 255, 0]


def test_teckenforklaring_for_klasser_och_paletter() -> None:
    tf = teckenforklaring("klass", 0.0, 2.0)
    assert tf["typ"] == "kategorier"
    assert [k["varde"] for k in tf["kategorier"]] == [0, 1, 2]
    for namn, stopp in PALETTER.items():
        t = teckenforklaring(namn, 1.0, 5.0)
        assert t == {"typ": "kontinuerlig", "min": 1.0, "max": 5.0, "stopp": list(stopp)}
    with pytest.raises(LagerFel):
        teckenforklaring("finns_inte", 0.0, 1.0)


def test_paletterna_ar_monotona_i_ljushet() -> None:
    # Läsbart utan färgseende: ljusheten (enkel relativ luminans) ändras åt ett håll.
    def ljus(h: str) -> float:
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for namn, stopp in PALETTER.items():
        L = [ljus(h) for h in stopp]
        stiger = all(a < b for a, b in zip(L, L[1:], strict=False))
        faller = all(a > b for a, b in zip(L, L[1:], strict=False))
        assert stiger or faller, namn


def test_skillnad_mosaik_ar_efter_minus_fore_och_nan_propageras() -> None:
    fore = _mosaik([[1.0, 2.0, np.nan]])
    efter = _mosaik([[1.5, 1.0, 3.0]])
    d = skillnad_mosaik(fore, efter)
    assert d is not None
    assert d.raster[0, 0] == 0.5 and d.raster[0, 1] == -1.0 and np.isnan(d.raster[0, 2])
    assert skillnad_mosaik(fore, _mosaik([[1.0, 2.0, 3.0]], x=101.0)) is None
    assert skillnad_mosaik(fore, _mosaik([[1.0, 2.0]])) is None


@pytest.fixture(scope="module")
def demo() -> Iterator[Demo]:
    rot = Path(tempfile.mkdtemp())
    try:
        yield Demo(rot)
    finally:
        shutil.rmtree(rot, ignore_errors=True)


def _lager(demo: Demo, korning: str) -> list[dict[str, Any]]:
    status, res = demo.get(f"/api/korningar/{korning}/resultat")
    assert status == 200, res
    out: list[dict[str, Any]] = res["lager"]
    return out


def test_lagren_har_doman_kontrollflagga_och_skillnad_for_varje_roll(demo: Demo) -> None:
    sol = _lager(demo, "demo-dagsljus")
    assert {la["doman"] for la in sol} == {"dagsljus"}
    kontroll = {la["roll"] for la in sol if la["kontroll"]}
    assert kontroll == {"trunkering", "standardhojd"}
    # Kontrollagren får ingen härledd skillnad; soltimmar har frågans egna.
    assert not [la for la in sol if la["harledd"]]
    eko = _lager(demo, "demo-ekologi")
    harledda = [la for la in eko if la["harledd"]]
    assert [la["roll"] for la in harledda] == ["kansighet"]
    d = harledda[0]
    assert d["typ"] == "skillnad" and d["fore"] == "fore" and d["efter"] == "efter"
    assert d["geotiff"] == []  # ingen fil: skillnaden är efter minus före av de visade lagren
    assert d["teckenforklaring"]["typ"] == "divergerande"


def test_harledd_skillnad_gar_att_lasa_av_i_en_punkt(demo: Demo) -> None:
    eko = _lager(demo, "demo-ekologi")
    fore = next(la for la in eko if la["id"] == "konnektivitet.fore.kansighet")
    u = fore["utbredning"]
    e, n = (u["x_min"] + u["x_max"]) / 2, (u["y_min"] + u["y_max"]) / 2
    status, svar = demo.get(
        f"/api/korningar/demo-ekologi/avlasning?lager={fore['id']}&e={e}&n={n}"
    )
    assert status == 200, svar
    rad = {x["typ"]: x for x in svar["varden"]}
    assert set(rad) == {"bas", "scenario", "skillnad"}
    if all(rad[t]["varde"] is not None for t in rad):
        assert rad["skillnad"]["varde"] == pytest.approx(
            rad["scenario"]["varde"] - rad["bas"]["varde"], abs=1e-5
        )


def test_vektor_sankor_ar_ytor_med_kapacitet_och_djup(demo: Demo) -> None:
    status, v = demo.get("/api/korningar/demo-vatten/vektor/fore/sankor")
    assert status == 200, v
    assert v["slag"] == "sankor" and v["scenario"] == "fore"
    assert v["meta"]["antal_totalt"] == len(v["features"]) > 0
    kap = [f["properties"]["kapacitet_m3"] for f in v["features"]]
    assert kap == sorted(kap, reverse=True)
    for f in v["features"]:
        p = f["properties"]
        assert f["geometry"]["type"] == "MultiPolygon"
        assert p["yta_m2"] > 0 and p["djup_m"] >= 0 and len(p["mitt"]) == 2
    # Samma svar andra gången (cache) och inget nytt tal.
    status2, v2 = demo.get("/api/korningar/demo-vatten/vektor/fore/sankor")
    assert status2 == 200 and v2 == v


def test_vektor_flodesvagar_har_langd_lutning_och_gangtid(demo: Demo) -> None:
    status, v = demo.get("/api/korningar/demo-vatten/vektor/efter/flodesvagar")
    assert status == 200, v
    assert v["features"]
    for f in v["features"]:
        assert f["geometry"]["type"] == "LineString" and len(f["geometry"]["coordinates"]) >= 2
        assert set(f["properties"]) >= {"langd_m", "lutning", "gangtid_s", "nod_id"}


def test_vektor_vsc_ger_fasadpunkter_med_procent(demo: Demo) -> None:
    status, v = demo.get("/api/korningar/demo-dagsljus/vektor/bas/vsc")
    assert status == 200, v
    assert v["features"][0]["geometry"]["type"] == "Point"
    alla = [f["properties"]["vsc_procent"] for f in v["features"]]
    varden = [x for x in alla if x is not None]
    assert varden and all(0.0 <= x <= 100.0 for x in varden)


def test_vektorvagen_ger_tydliga_fel(demo: Demo) -> None:
    assert demo.get("/api/korningar/demo-vatten/vektor/finns_inte/sankor")[0] == 404
    assert demo.get("/api/korningar/demo-vatten/vektor/fore/okant")[0] == 404
    # Ingen vattenberäkning i dagsljuskörningen:
    assert demo.get("/api/korningar/demo-dagsljus/vektor/bas/sankor")[0] == 404
    assert demo.get("/api/korningar/saknas/vektor/fore/sankor")[0] in (404, 409)
    assert demo.get("/api/korningar/demo-vatten/vektor/..%2Fx/sankor")[0] in (400, 404)


def test_json_i_svaret_ar_giltigt_geojson(demo: Demo) -> None:
    _, v = demo.get("/api/korningar/demo-vatten/vektor/fore/sankor")
    json.dumps(v)
    assert v["type"] == "FeatureCollection"


def test_paletterna_i_klienten_ar_samma_som_serverns() -> None:
    ts = (Path(__file__).resolve().parents[2] / "webb/src/flode/analyslager.ts").read_text(
        encoding="utf-8"
    )
    for namn, stopp in PALETTER.items():
        m = re.search(rf"\b{namn}: \[([^\]]+)\]", ts)
        assert m, namn
        assert re.findall(r'"(#[0-9a-f]{6})"', m.group(1)) == list(stopp), namn
    klasser = re.findall(
        r"fran: ([0-9.]+), farg: \"(#[0-9a-f]{6})\"", ts.split("export const VATTEN_KLASSER")[1]
    )
    assert [(float(f), c) for f, c in klasser] == [(g, c) for g, c in _VATTEN_KLASSER]


def test_losarens_namnrymd_ar_dess_doman() -> None:
    # Webben tar lagrens ämne ur lösarens namn (`dagsljus.svf_mark`): det ska vara lösarens `doman`.
    from karna.analyslosare import lista_losare

    assert [(x.namn, x.doman) for x in lista_losare() if x.namn.split(".")[0] != x.doman] == []
