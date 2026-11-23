# ruff: noqa: E501
"""Spärren för data som inte är öppna (floden_mcp.oppenhet): regeln, körningar och verktygslagret."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from floden_mcp.oppenhet import KANDA_OPPNA_LICENSER, METADATAFIL, Oppenhet, OppenhetNekad
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp


def _meta(mapp: Path, text: str) -> None:
    mapp.mkdir(parents=True, exist_ok=True)
    (mapp / METADATAFIL).write_text(text, encoding="utf-8")


@pytest.fixture
def rot(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    for d in ("data/demo/varld", "data/lokal/prov1/k1", "data/ledningar", "testsvit", "exempel", "docs", "extern"):
        (r / d).mkdir(parents=True)
    return r


def test_standard_ar_att_neka_allt_utom_demon_och_oppna_repomaterial(rot: Path) -> None:
    o = Oppenhet(rot)
    for oppet in ("data/demo/varld/vatten", "testsvit/x", "exempel/fragor/a.toml", "docs/MCP.md"):
        assert o.bedom(rot / oppet).oppen, oppet
    for stangt in ("data/lokal/prov1/k1", "extern/laser", "data/omraden/prov1.gpkg", "/tmp/nagon-annanstans"):
        b = o.bedom(rot / stangt if not stangt.startswith("/") else stangt)
        assert not b.oppen and b.orsak, stangt
        with pytest.raises(OppenhetNekad) as fel:
            o.kontrollera_sokvag(rot / stangt if not stangt.startswith("/") else stangt)
        assert str(rot) not in str(fel.value)  # felet nämner aldrig sökvägen


def test_ledningsdata_ar_alltid_stangda_aven_med_metadata_och_i_demon(rot: Path) -> None:
    o = Oppenhet(rot)
    _meta(rot / "data/ledningar", 'oppen = true\nlicens = "CC0-1.0"\n')
    assert not o.bedom(rot / "data/ledningar/vatten.gpkg").oppen
    _meta(rot / "data/demo/varld/ledningsnat", 'oppen = true\nlicens = "CC0-1.0"\n')
    assert not o.bedom(rot / "data/demo/varld/ledningsnat/nat.gpkg").oppen
    assert not o.bedom(rot / "extern/Ledningar_2025/x.shp").oppen
    assert not o.bedom(rot / "data/intern/x").oppen and not o.bedom(rot / "data/konto/konto.sqlite").oppen
    assert not o.bedom(rot / "config.local.toml").oppen
    egen = rot / "extern" / "stadens_ledningsdata"
    egen.mkdir()
    cfg = {"lokal_data": {"ledningar_mapp": str(rot / "extern" / "vattennat")}}
    assert not Oppenhet(rot, konfig=cfg).bedom(rot / "extern/vattennat/a").oppen


def test_metadata_oppnar_bara_med_en_kand_licens(rot: Path) -> None:
    o = Oppenhet(rot)
    fil = rot / "data/lokal/prov1/k1/mark.tif"
    assert not o.bedom(fil).oppen
    for licens in sorted(KANDA_OPPNA_LICENSER):
        _meta(rot / "data/lokal/prov1", f'oppen = true\nlicens = "{licens}"\nkalla = "Lantmäteriet"\n')
        assert o.bedom(fil).oppen, licens
    for text in (
        'oppen = true\nlicens = "Proprietary"\n',  # okänd licens
        "oppen = true\n",  # ingen licens
        'oppen = false\nlicens = "CC0-1.0"\n',  # uttryckligen stängd
        'oppen = "ja"\nlicens = "CC0-1.0"\n',  # inte ett riktigt sant
        "detta är inte toml {{{",  # otolkbar
    ):
        _meta(rot / "data/lokal/prov1", text)
        assert not o.bedom(fil).oppen, text


def test_metadata_i_en_underordnad_mapp_gar_fore_och_oppen_false_stanger_aven_demon(rot: Path) -> None:
    o = Oppenhet(rot)
    _meta(rot / "extern", 'oppen = true\nlicens = "CC-BY-4.0"\n')
    _meta(rot / "extern/hemligt", "oppen = false\n")
    assert o.bedom(rot / "extern/laser/a.laz").oppen
    assert not o.bedom(rot / "extern/hemligt/a.laz").oppen
    _meta(rot / "data/demo/varld", "oppen = false\n")
    assert not o.bedom(rot / "data/demo/varld/vatten").oppen


def test_lager_i_config_local_toml_foljer_sin_metadata(rot: Path) -> None:
    cfg: dict[str, Any] = {
        "oppenhet": {
            "lager": {
                "baskarta": {"sokvag": "extern/baskarta", "oppen": False},
                "laser": {"sokvag": "extern/laser", "oppen": True, "licens": "CC0-1.0"},
                "obestamd": {"sokvag": "extern/annat", "oppen": True},
            }
        }
    }
    o = Oppenhet(rot, konfig=cfg)
    assert not o.bedom(rot / "extern/baskarta/x").oppen
    assert o.bedom(rot / "extern/laser/x").oppen
    assert not o.bedom(rot / "extern/annat/x").oppen  # öppen utan licens räknas som stängd
    assert not o.bedom(rot / "extern/okant/x").oppen


def test_arbetsytor_ar_oppna_aven_inne_i_data_lokal(rot: Path) -> None:
    kv = rot / "data/lokal/kartverktyg"
    o = Oppenhet(rot, arbetsytor=[kv])
    assert o.bedom(kv / "scenario.json").oppen
    assert not o.bedom(rot / "data/lokal/prov1/k1/mark.tif").oppen


# ------------------------------------------------------------------ körningar och verktygslagret


def _kopiera_korning(demo: Demo, mcp: Mcp, ny_id: str, bas: Path) -> None:
    """En kopia av en demokörning vars fråga pekar på `bas` (en icke-öppen mapp)."""
    k_mapp = demo.rot / "korningar"
    kalla = k_mapp / "demo-vatten"
    ny = k_mapp / ny_id
    shutil.copytree(kalla, ny)
    fraga_fil = demo.fragor / f"{ny_id}.json"
    fraga_fil.write_text(json.dumps(_fraga_dict(bas, ny_id)), encoding="utf-8")
    run = ny / "resultat" / "run.json"
    manifest = json.loads(run.read_text(encoding="utf-8"))
    for post in manifest["indata"]:
        if post["roll"] == "fraga":
            post["fil"] = str(fraga_fil)
    run.write_text(json.dumps(manifest), encoding="utf-8")
    status = json.loads((ny / "korning.json").read_text(encoding="utf-8"))
    status["korning_id"] = ny_id
    (ny / "korning.json").write_text(json.dumps(status), encoding="utf-8")


def test_korning_pa_icke_oppna_data_nekas_i_verktygslagret_men_demon_gar(demo: Demo, tmp_path: Path) -> None:
    mcp = Mcp(demo, tmp_path / "m")
    stangd = tmp_path / "data" / "lokal" / "prov1" / "k1"  # utanför demon, utan metadata
    _kopiera_korning(demo, mcp, "stangd-1", stangd)
    # Demon fungerar som förut.
    assert mcp.ok("resultat_sammanfattning", {"korning_id": "demo-vatten"})
    # Den stängda körningen nekas i alla verktyg som tar ett körnings-id.
    for verktyg, arg in (
        ("resultat_sammanfattning", {}),
        ("korning_status", {}),
        ("jamfor", {}),
        ("karta_bild", {"analys": "skyfall"}),
        ("resultat_varde", {"analys": "skyfall", "roll_namn": "max_djup", "e": 1.0, "n": 1.0}),
    ):
        r = mcp.anropa(verktyg, {"korning_id": "stangd-1", **arg})
        assert r.is_error, verktyg
        text = r.content[0].text  # type: ignore[union-attr]
        assert "som inte är öppna" in text and str(tmp_path) not in text, verktyg
    # Och döljs i listan (med ett räkneverk, inget innehåll).
    lista = mcp.ok("korningar_lista")
    assert "stangd-1" not in {k["korning_id"] for k in lista["korningar"]}
    assert lista["dolda_icke_oppna"] == 1 and "demo-vatten" in {k["korning_id"] for k in lista["korningar"]}
    # Körloggen visar nekandet, med utfallet men utan data.
    nekade = [r for r in mcp.logg.las() if r["utfall"] == "nekad_oppenhet"]
    assert nekade and all("stangd-1" in r["korning_id"] for r in nekade)
    # Metadata med känd öppen licens öppnar den: samma körning, nu tillåten.
    _meta(stangd, 'oppen = true\nlicens = "CC0-1.0"\nkalla = "Lantmäteriet"\n')
    mcp.server  # noqa: B018 - servern är densamma; spärren läser metadata vid varje anrop
    mcp.tjanst.oppenhet._korningscache.clear()  # noqa: SLF001
    assert not mcp.anropa("korning_status", {"korning_id": "stangd-1"}).is_error


def test_svar_som_namner_en_stangd_korning_lamnas_inte_ut(demo: Demo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Efterkontrollen: ett verktyg vars svar nämner en körning på icke-öppna data nekas."""
    mcp = Mcp(demo, tmp_path / "m")
    _kopiera_korning(demo, mcp, "stangd-2", tmp_path / "data" / "lokal" / "prov1" / "k1")
    monkeypatch.setattr(mcp.tjanst, "metoder_lista", lambda: {"korning_id": "stangd-2", "hemligt_tal": 42.5})
    r = mcp.anropa("metoder_lista")
    assert r.is_error
    assert "42.5" not in str(r.content) and "inte är öppna" in r.content[0].text  # type: ignore[union-attr]


def test_fraga_skapa_nekar_sokvagar_i_icke_oppna_data(demo: Demo, tmp_path: Path) -> None:
    mcp = Mcp(demo, tmp_path / "m")
    # En fråga som pekar in i data/lokal (inom en tillåten rot, men stängd enligt öppenhetsregeln).
    stangd = mcp.tjanst.kv.mapp / "ledningar_prov"  # kartverktygets mapp är en arbetsyta ...
    stangd.mkdir(parents=True, exist_ok=True)
    fraga = json.loads(json.dumps(_fraga_dict(demo.varld / "vatten" / "bas")))
    ok = mcp.anropa("fraga_skapa", {"fraga": fraga})
    assert not ok.is_error, ok.content[0].text  # type: ignore[union-attr]
    fraga["fraga_id"] = "ledn"
    for s in fraga["scenarier"].values():
        s["bas"] = str(stangd)  # ... men en mapp som heter ledning* är aldrig öppen
    r = mcp.anropa("fraga_skapa", {"fraga": fraga})
    assert r.is_error and "nekad_oppenhet" in r.content[0].text  # type: ignore[union-attr]


def _fraga_dict(bas: Path, fraga_id: str = "oppen-1") -> dict[str, Any]:
    return {
        "kontrakt": "K20/v1",
        "fraga_id": fraga_id,
        "beskrivning": "test",
        "scenarier": {"fore": {"bas": str(bas)}},
        "analyser": [
            {
                "id": "skyfall",
                "losare": "vatten.statisk_fyllning",
                "parametrar": {"idf": "stockholmsregn-2024", "aterkomsttid_ar": 100, "varaktighet_min": 360, "klimatfaktor": 1.4, "regntyp": "block", "takvatten": True},
            }
        ],
    }
