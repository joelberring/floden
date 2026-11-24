# ruff: noqa: E501
"""Luft (R3, nivå 1): tal i MKN-tabellen kommer bara via belägg ur en K20-körning med `luft.mkn`.

Körningen går genom samma `FragaTjanst` och `FlodenTjanst` som MCP-servern. Serien är påhittad
(konstruerad med känt svar, se `tests/karna/test_luft_mkn.py`); bakgrunden är en parameter utan källa.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator

import pytest

from rapport.belaggskalla import MATTKATALOG, BelaggFel
from rapport.pastaende import PastaendeFel
from rapport.r2_domaner import MKN_BELAGGSMATT, buller_spar, bygg_mkn_tabell_fran_belagg
from tests.demovarld.test_demo import Demo
from tests.rapport.test_skissrapport import Miljo


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    rot = tmp_path_factory.mktemp("luft")
    for namn, lokal in (("fore", [40.0] * 10 + [100.0] * 20 + [5.0] * 335), ("efter", [100.0] * 40 + [5.0] * 325)):
        with (rot / f"{namn}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["tid", "lokal_ug_m3"])
            for d, v in enumerate(lokal):
                for t in range(24):
                    w.writerow([d * 24 + t, v])
    analyser = "".join(
        f'\n[[analyser]]\nid = "mkn_{n}"\nlosare = "luft.mkn"\n'
        f'parametrar = {{ bakgrund_ug_m3 = 10.0 }}\nindata = {{ timserie = "{(rot / f"{n}.csv").as_posix()}" }}\n'
        for n in ("fore", "efter")
    )
    (demo.fragor / "test_luft_mkn.toml").write_text(
        'kontrakt = "K20/v1"\nfraga_id = "test-luft-mkn"\nbeskrivning = "Påhittad timserie, känt svar."\n'
        f'\n[scenarier.bas]\nbas = "{(demo.varld / "dagsljus" / "bas").as_posix()}"\n' + analyser,
        encoding="utf-8",
    )
    demo.tjanst.kor_synkront("test_luft_mkn", "test-luft-mkn")
    yield Miljo(demo, rot)


def _rad(m: Miljo, analys: str) -> dict[str, object]:
    ut = {}
    for matt in MKN_BELAGGSMATT:
        ut[matt] = m.rt.kalla.hamta(f"matvarde.{analys}.bas.{matt}", "test-luft-mkn", {})
    return ut


def test_belagg_har_enhet_tidsdefinition_och_statistik(m: Miljo) -> None:
    b = m.rt.kalla.hamta("matvarde.mkn_fore.bas.arsmedel_hogst_ug_m3", "test-luft-mkn", {})
    assert b.losare == "luft.mkn" and b.enhet == "µg/m³"
    assert "faktorintervallet" in b.statistik and "timserie" in b.tidsdefinition
    d = m.rt.kalla.hamta("matvarde.mkn_fore.bas.hogst_antal_dygn", "test-luft-mkn", {})
    assert (d.varde, d.enhet) == (30.0, "dygn")
    assert m.rt.kalla.hamta("matvarde.mkn_fore.bas.lagst_antal_dygn", "test-luft-mkn", {}).varde == 20.0


def test_matt_utan_katalogpost_nekas(m: Miljo) -> None:
    with pytest.raises(BelaggFel):
        m.rt.kalla.hamta("matvarde.mkn_fore.bas.klarar_garanterat", "test-luft-mkn", {})  # flagga, inget rapporttal
    assert ("luft.mkn", "matvarde:klarar_garanterat") not in MATTKATALOG


def test_tabellen_far_alla_tal_ur_belagg(m: Miljo) -> None:
    t = bygg_mkn_tabell_fran_belagg(
        [("Före", _rad(m, "mkn_fore")), ("Efter", _rad(m, "mkn_efter"))]  # type: ignore[arg-type]
    )
    fore, efter = t["rader"]
    assert fore["dygn_over"] == "20–30" and fore["relation_till_hogsta_antal_dygn"] == "helt under"
    assert efter["dygn_over"] == "40–40" and efter["relation_till_hogsta_antal_dygn"] == "helt över"
    assert all(a.startswith("matvarde.") for r in t["rader"] for a in r["belagg"])
    assert "konfidens" in t["rubrik"] and "klarar" not in t["markdown"].lower()


def test_tabellen_nekar_saknat_belagg_och_fel_losare(m: Miljo) -> None:
    rad = _rad(m, "mkn_fore")
    del rad["lagst_antal_dygn"]
    with pytest.raises(PastaendeFel, match="saknar belägg"):
        bygg_mkn_tabell_fran_belagg([("Före", rad)])  # type: ignore[arg-type]
    rad = _rad(m, "mkn_fore")
    rad["lagst_antal_dygn"] = type(rad["hogst_antal_dygn"])(**{**rad["hogst_antal_dygn"].__dict__, "losare": "vatten.dynamik"})  # type: ignore[attr-defined]
    with pytest.raises(PastaendeFel, match="inte luft.mkn"):
        bygg_mkn_tabell_fran_belagg([("Före", rad)])  # type: ignore[arg-type]


def test_buller_ar_sparrat_sa_lange_nord2000_inte_ar_konformt() -> None:
    from buller.nord2000.karna import Nord2000Karna

    assert Nord2000Karna.nord2000_konform is False
    assert buller_spar() is not None and "Nord2000" in str(buller_spar())
