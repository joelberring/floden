"""Formatet floden-benchmark/v1 (K37), jämförelsen och tabellerna. Kör inget tungt."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from benchmark.cli import kor, registrera
from benchmark.format import (
    FORMAT,
    BenchmarkFormatFel,
    andring_procent,
    jamfor_dokument,
    jamforelsetabell,
    kontrollera,
    maskininfo,
    skapa_dokument,
    tabell,
)
from benchmark.matare import TidsgransOverskriden, mat


def _fall(id_: str, tid: float, rss: float = 100.0, avv: float | None = 0.01) -> dict[str, Any]:
    return {
        "id": id_,
        "namn": id_,
        "status": "ok",
        "tid_s": {"median": tid, "min": tid, "max": tid, "n": 1, "alla": [tid]},
        "minne": {"rss_topp_mb": rss, "tracemalloc_topp_mb": 5.0},
        "avvikelse": None
        if avv is None
        else {"varde": avv, "enhet": "m", "grans": 0.05, "godkand": True, "matt": {}},
        "extra": {},
    }


def _dok(fall: list[dict[str, Any]] | None = None, cpu: str = "X") -> dict[str, Any]:
    maskin = {**maskininfo(), "cpu": cpu}
    return skapa_dokument(
        kalla="server",
        kodversion="abc123",
        maskin=maskin,
        installning={"uppvarmning": 1, "upprepningar": 3},
        delar={
            "skyfall": {"status": "kord", "skal": None, "fall": fall or [_fall("EA1", 1.0)]},
            "buller": {
                "status": "kraver_lokal_kallmapp",
                "skal": "kräver lokal källmapp",
                "fall": [],
            },
        },
    )


def test_maskininfo_har_de_falt_k37_kraver() -> None:
    m = maskininfo()
    assert m["cpu"] and m["karnor"] >= 1 and m["python"] and m["numpy"]
    assert set(m["rust_karna"]) == {"tillganglig", "sokvag", "anvands_i_svit"}
    assert m["rust_karna"]["anvands_i_svit"] is False


def test_skapat_dokument_ar_giltigt_och_json_serialiserbart() -> None:
    d = _dok()
    assert d["format"] == FORMAT == "floden-benchmark/v1"
    kontrollera(json.loads(json.dumps(d)))


@pytest.mark.parametrize(
    ("andra", "text"),
    [
        (lambda d: d.update(format="floden-benchmark/v2"), "format"),
        (lambda d: d.update(kalla="moln"), "kalla"),
        (lambda d: d.pop("kodversion"), "kodversion"),
        (lambda d: d["delar"]["skyfall"].update(status="kanske"), "status"),
        (lambda d: d["delar"]["buller"].update(skal=None), "skal"),
        (lambda d: d["delar"]["skyfall"]["fall"][0]["tid_s"].update(median=float("nan")), "median"),
        (lambda d: d["delar"]["skyfall"]["fall"][0]["tid_s"].update(median=-1.0), "median"),
        (lambda d: d["delar"]["skyfall"]["fall"][0]["minne"].update(rss_topp_mb="stor"), "minne"),
        (lambda d: d["delar"]["skyfall"]["fall"][0].update(status="fel"), "skal"),
        (lambda d: d["delar"]["skyfall"]["fall"][0]["avvikelse"].update(varde=None), "avvikelse"),
        (lambda d: d["delar"]["skyfall"]["fall"][0].pop("id"), "id"),
    ],
)
def test_ogiltiga_dokument_avvisas_med_forklaring(andra: Any, text: str) -> None:
    d = copy.deepcopy(_dok())
    andra(d)
    with pytest.raises(BenchmarkFormatFel, match=text):
        kontrollera(d)


def test_fall_utan_facit_och_hoppade_fall_ar_giltiga() -> None:
    d = _dok(
        [_fall("A", 1.0, avv=None), {"id": "B", "status": "hoppas_over", "skal": "tar över 60 s"}]
    )
    kontrollera(d)
    t = tabell(d)
    assert "inget facit" in t and "tar över 60 s" in t and "kräver lokal källmapp" in t


def test_andring_procent() -> None:
    assert andring_procent(110.0, 100.0) == pytest.approx(10.0)
    assert andring_procent(50.0, 100.0) == pytest.approx(-50.0)
    assert andring_procent(1.0, 0.0) is None


def test_jamforelse_flaggar_forsamring_over_troskeln_men_inte_forbattring() -> None:
    gammal = _dok([_fall("EA1", 1.0, rss=100.0), _fall("EA2", 2.0, rss=100.0)])
    ny = _dok([_fall("EA1", 1.30, rss=100.0), _fall("EA2", 1.0, rss=140.0)])
    j = jamfor_dokument(ny, gammal, troskel_procent=25.0)
    flaggade = {(r["fall"], r["matt"]) for r in j["forsamringar"]}
    assert flaggade == {("EA1", "tid_s"), ("EA2", "rss_topp_mb")}
    ea2_tid = next(r for r in j["rader"] if r["fall"] == "EA2" and r["matt"] == "tid_s")
    assert ea2_tid["andring_procent"] == pytest.approx(-50.0) and not ea2_tid["forsamring"]
    # Exakt på tröskeln flaggas inte; ett högre värde på tröskeln gör att inget flaggas.
    assert not jamfor_dokument(_dok([_fall("EA1", 1.25)]), _dok(), 25.0)["forsamringar"]
    assert not jamfor_dokument(ny, gammal, troskel_procent=100.0)["forsamringar"]
    text = jamforelsetabell(j)
    assert "FÖRSÄMRING" in text and "+30.0 %" in text


def test_jamforelse_hanterar_nya_och_borttagna_fall_och_annan_maskin() -> None:
    gammal = _dok([_fall("EA1", 1.0), _fall("EA2", 2.0)], cpu="A")
    ny = _dok([_fall("EA1", 1.0), _fall("EA8A", 3.0)], cpu="B")
    j = jamfor_dokument(ny, gammal)
    assert j["bara_i_ny"] == ["skyfall/EA8A"] and j["bara_i_gammal"] == ["skyfall/EA2"]
    assert j["annan_maskin"] is True
    assert "olika maskiner" in jamforelsetabell(j)


def test_jamforelse_avvikelse_ar_ett_matt_som_flaggas_nar_den_vaxer() -> None:
    gammal = _dok([_fall("EA1", 1.0, avv=0.010)])
    ny = _dok([_fall("EA1", 1.0, avv=0.020)])
    j = jamfor_dokument(ny, gammal)
    assert [(r["matt"]) for r in j["forsamringar"]] == ["avvikelse"]


def test_ogiltig_troskel_och_ogiltigt_dokument_i_jamforelsen() -> None:
    with pytest.raises(BenchmarkFormatFel):
        jamfor_dokument(_dok(), _dok(), troskel_procent=-1.0)
    with pytest.raises(BenchmarkFormatFel):
        jamfor_dokument(_dok(), {"format": "annat"})


def test_mat_gor_uppvarmning_median_och_minne() -> None:
    anrop: list[int] = []

    def fn() -> dict[str, Any]:
        anrop.append(1)
        _ = bytearray(5_000_000)
        return {"avvikelse": None, "extra": {"n": len(anrop)}}

    m = mat(fn, uppvarmning=2, upprepningar=3)
    assert len(anrop) == 2 + 3 + 1  # uppvärmning, tidtagna, tracemalloc-körningen
    assert len(m.tider_s) == 3 and m.tid_dict()["n"] == 3
    assert m.tracemalloc_topp_mb is not None and m.tracemalloc_topp_mb >= 4.5
    assert m.rss_topp_mb >= m.rss_start_mb > 0
    with pytest.raises(ValueError):
        mat(fn, upprepningar=0)


def test_mat_markerar_fall_som_overskrider_tidsgransen() -> None:
    import time

    with pytest.raises(TidsgransOverskriden):
        mat(lambda: (time.sleep(0.05), {})[1], uppvarmning=1, upprepningar=1, tidsgrans_s=0.01)


def test_cli_jamfor_tva_filer_utan_att_kora(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    gammal, ny = tmp_path / "gammal.json", tmp_path / "ny.json"
    gammal.write_text(json.dumps(_dok([_fall("EA1", 1.0)])), encoding="utf-8")
    ny.write_text(json.dumps(_dok([_fall("EA1", 2.0)])), encoding="utf-8")
    p = argparse.ArgumentParser()
    registrera(p.add_subparsers(dest="kommando"))
    a = p.parse_args(["benchmark", "--ny", str(ny), "--jamfor", str(gammal)])
    assert kor(a) == 1  # försämring ger slutkod 1
    assert "FÖRSÄMRING" in capsys.readouterr().out
    a = p.parse_args(["benchmark", "--ny", str(gammal), "--jamfor", str(gammal)])
    assert kor(a) == 0
    a = p.parse_args(["benchmark", "--ny", str(tmp_path / "saknas.json"), "--jamfor", str(gammal)])
    assert kor(a) == 2
    a = p.parse_args(["benchmark", "--ny", str(ny)])
    assert kor(a) == 2  # --ny utan --jamfor


def test_cli_kor_en_del_i_barnprocess_och_skriver_giltigt_dokument(tmp_path: Path) -> None:
    """Hela vägen på det snabbaste fallet: barnprocess, mätning, dokument, tabell."""
    from benchmark.korare import kor_svit

    dok = kor_svit(["luft"], uppvarmning=0, upprepningar=1, kodversion="test")
    kontrollera(dok)
    fall = {f["id"]: f for f in dok["delar"]["luft"]["fall"]}
    assert set(fall) == {"L4-punktkalla", "L5-dualitet"}
    for f in fall.values():
        assert f["status"] == "ok" and f["tid_s"]["median"] > 0
        assert f["minne"]["rss_topp_mb"] > 0 and f["minne"]["tracemalloc_topp_mb"] > 0
        assert f["avvikelse"]["godkand"] is True


def test_buller_delen_markerar_kallmappsfallet_som_hoppat_over() -> None:
    from benchmark.fall import FALL, KRAVER_KALLMAPP

    assert KRAVER_KALLMAPP["buller"][0][2].startswith("kräver lokal källmapp")
    assert {f.id for f in FALL["buller"]} == {"helkedja-300m", "nord2000-tvastrale"}


def test_webblasarens_exempeldokument_foljer_samma_format() -> None:
    """Filen skrivs av klientens kod (webb/src/prov/benchmarkFormat.test.ts jämför den)."""
    fil = Path(__file__).with_name("exempel_webblasare.json")
    dok = json.loads(fil.read_text(encoding="utf-8"))
    kontrollera(dok)
    assert dok["kalla"] == "webblasare" and dok["format"] == FORMAT
    assert "Skissläge" in dok["anmarkning"]
    # Och den går att jämföra med ett serverdokument utan att kasta (annan maskin flaggas).
    server = _dok([_fall("dagsljus-kvarter300:svf", 1.0)])
    j = jamfor_dokument(dok, server)
    assert j["annan_maskin"] is True
