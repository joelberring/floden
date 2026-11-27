"""Regressionstester för ekologifokus i `kommuner.projekt`, `Kartverktyg` och `webb.api`."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import numpy as np

from kommuner.projekt import skriv_fragor
from tests.kommuner.test_skapa import bygg_varld
from tests.lantmateriet.conftest import lm  # noqa: F401
from tests.lantmateriet.falsk_server import FalskLm
from testsvit.k1_io import skriv_geotiff
from webb.kartverktyg import KartverktygTjanst, Mall
from webb.projektkalla import Projektkalla


def test_skriv_fragor_skriver_fokus_i_ekologi_toml(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    """Nya geografiprojekt med NMD-lager ska få `fokus = [...]` i `ekologi.toml`."""
    v = bygg_varld(lm, tmp_path)
    geo_rot = v["rot"] / "geo"
    k1_mapp = geo_rot / "_lager" / "k1sha"
    ruta_mapp = k1_mapp / "mm_150000_6580000"
    ruta_mapp.mkdir(parents=True)
    skriv_geotiff(
        ruta_mapp / "mark.tif",
        np.full((10, 10), 10.0, dtype=np.float32),
        x_min=150000.0,
        y_max=6580500.0,
        pixelstorlek=50.0,
    )
    (ruta_mapp / "recept.json").write_text('{"ruta_id": "mm_150000_6580000"}\n', encoding="utf-8")
    manifest_mapp = geo_rot / "testgeo" / "v1"
    manifest_mapp.mkdir(parents=True)
    manifest = {
        "kontrakt": "floden-geografi/v1",
        "id": "testgeo",
        "version": "v1",
        "kommun": v["kommun"].id,
        "karna_3011": [150000.0, 6580000.0, 150500.0, 6580500.0],
        "gransen_3011": [150000.0, 6580000.0, 150500.0, 6580500.0],
        "lager": {
            "k1_rutnat": {"sha256": "k1sha", "rutor": ["mm_150000_6580000"]},
            "nmd": {"sha256": "nmdsha", "buffert_m": 3000.0, "cell_m": 10.0},
        },
    }
    (manifest_mapp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    proj_mapp = tmp_path / "projekt" / "p1"
    proj_mapp.mkdir(parents=True)
    projekt = {
        "kontrakt": "floden-projekt/v1",
        "projekt_id": "p1",
        "geografi": "testgeo@v1",
        "avgransning_3011": [150100.0, 6580100.0, 150400.0, 6580400.0],
    }
    (proj_mapp / "projekt.json").write_text(json.dumps(projekt), encoding="utf-8")
    (proj_mapp / "scenarier.json").write_text(
        json.dumps({"scenarier": []}), encoding="utf-8"
    )
    skrivna = skriv_fragor(proj_mapp, v["kommun"], geo_rot, manifest)
    eko_fil = proj_mapp / "fragor" / "ekologi.toml"
    dag_fil = proj_mapp / "fragor" / "dagsljus.toml"
    assert eko_fil in skrivna
    dok = tomllib.loads(eko_fil.read_text(encoding="utf-8"))
    dag_dok = tomllib.loads(dag_fil.read_text(encoding="utf-8"))
    analyser = dok["analyser"]
    assert len(analyser) == 1
    assert analyser[0]["losare"] == "ekologi.konnektivitet"
    assert analyser[0]["fokus"] == dag_dok["analyser"][0]["fokus"]


def test_fallback_ekologi_fokus_for_aldre_geografiprojekt(tmp_path: Path) -> None:
    """Äldre geografiprojekt utan `fokus` i `ekologi.toml` får automatiskt projektfokus,
    medan projekt utan geografi (`slag='demo'`) lämnas utan fokus."""
    proj_mapp = tmp_path / "p_gammalt"
    proj_mapp.mkdir()
    (proj_mapp / "projekt.json").write_text(
        json.dumps({"avgransning_3011": [-210450.0, 6414350.0, -210100.0, 6414700.0]}),
        encoding="utf-8",
    )

    kalla_geo = Projektkalla(
        id="p_gammalt",
        namn="Gammalt projekt",
        slag="geografi",
        paahittat=False,
        oppen=True,
        licens="CC0-1.0",
        geografi="goteborg-test@v6",
        avgransning=(-210450.0, 6414350.0, -210100.0, 6414700.0),
        mallar={},
        mapp=proj_mapp,
    )
    kalla_demo = Projektkalla(
        id="demokvarteret",
        namn="Demokvarteret",
        slag="demo",
        paahittat=True,
        oppen=True,
        licens="CC0-1.0",
        geografi=None,
        avgransning=None,
        mallar={},
        mapp=None,
    )

    # _fallback_ekologi_fokus kan prövas direkt på en KartverktygTjanst-instans.
    kv = object.__new__(KartverktygTjanst)
    assert kv._fallback_ekologi_fokus(kalla_geo) == [-210450.0, 6414350.0, -210100.0, 6414700.0]
    assert kv._fallback_ekologi_fokus(kalla_demo) is None

    # Verifiera att `_k20` och `_lage_k20` skriver `fokus` på `ekologi.konnektivitet`.
    from karna.utredning import fraga_fran_dict

    bas_mapp = tmp_path / "k1"
    bas_mapp.mkdir()
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "p-ekologi",
            "scenarier": {"bas": {"bas": str(bas_mapp)}},
            "analyser": [
                {
                    "id": "konnektivitet",
                    "losare": "ekologi.konnektivitet",
                    "parametrar": {"natverk": "barrskog-nacka"},
                }
            ],
        },
        tmp_path,
    )
    mall = Mall(
        ref="ekologi",
        kort="ekologi",
        fraga=fraga,
        bas=bas_mapp,
        rutor=(),
        fore_id="bas",
    )
    fok = [-210450.0, 6414350.0, -210100.0, 6414700.0]
    k20_scen = kv._k20(
        mall,
        tmp_path / "q1.json",
        tmp_path / "k9.json",
        "q1",
        "Förslag",
        ["konnektivitet"],
        ekologi_fokus=fok,
    )
    assert k20_scen["analyser"][0]["fokus"] == fok

    k20_lage = kv._lage_k20(
        mall,
        tmp_path / "q2.json",
        "q2",
        ["konnektivitet"],
        None,
        ekologi_fokus=fok,
    )
    assert k20_lage["analyser"][0]["fokus"] == fok
