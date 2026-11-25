"""Enhetstester för P1-3 i rapport.webbflode: läget idag och projektets polygon."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from rapport.webbflode import (
    INOM_PROJEKTETS_POLYGON,
    anpassa_omradestext,
    ar_lage_idag,
    dagsljus_pastaenden,
    har_avgransningspolygon,
    pastaenden_for,
    skyfall_pastaenden,
)


def _mock_rapport_skyfall(
    *,
    med_jamforelse: bool = False,
    scenarier: list[str] | None = None,
    har_avgransning: bool = False,
    sprakprofil: str = "sbk",
) -> Any:
    korning: dict[str, Any] = {
        "korning_id": "k-lage-idag-1",
        "analyser": [
            {
                "analys": "skyfall",
                "losare": "vatten.statisk_fyllning",
                **({"scenarier": scenarier} if scenarier is not None else {}),
            }
        ],
        "jamforelser": (
            [
                {
                    "analys": "skyfall",
                    "utdata": "max_djup",
                    "fore": "bas",
                    "efter": "forslag-a",
                    "namn": "skyfall_max_djup_forslag-a_minus_bas",
                }
            ]
            if med_jamforelse
            else []
        ),
        "underlag": {"slag": "forslag" if med_jamforelse else "lage_idag"},
    }
    if scenarier is not None:
        korning["scenarier"] = scenarier
    if har_avgransning:
        korning["har_avgransning"] = True
    return SimpleNamespace(
        projekt="mitt-projekt",
        amne="skyfall",
        fragor=[f"SK-{i:02d}" for i in range(1, 16)],
        installningar=SimpleNamespace(sprakprofil=sprakprofil),
        korningar=[korning],
        mottagare=[],
    )


def _mock_rt(*, har_avgransning: bool = False, med_idf: bool = True, ruta: str = "r0") -> Any:
    def resultat_sammanfattning(
        korning_id: str,
        *,
        analys: str,
        scenario: str,
        ruta: str | None = None,
        max_rader: int = 500,
    ) -> dict[str, Any]:
        del korning_id, analys, scenario, ruta, max_rader
        varden = {"aterkomsttid_ar": 100.0} if med_idf else {"regn_mm": 65.0}
        return {"matvarden": [{"rader": [{"ruta": "r0", "varden": varden}]}]}

    return SimpleNamespace(
        tjanst=SimpleNamespace(resultat_sammanfattning=resultat_sammanfattning),
        kalla=SimpleNamespace(har_avgransning=lambda korning_id: har_avgransning),
    )


def test_skyfall_rapport_pa_enbart_laget_idag_lyckas_utan_jamforelse() -> None:
    r = _mock_rapport_skyfall(med_jamforelse=False, scenarier=["bas"])
    rt = _mock_rt(har_avgransning=False, med_idf=True)

    assert ar_lage_idag(r) is True
    ps = pastaenden_for(rt, r)
    assert len(ps) == 15

    serialiserad = json.dumps(ps, ensure_ascii=False)
    assert "matvarde.skyfall.bas.max_djup_m" in serialiserad
    assert "jamforelse." not in serialiserad
    assert ".efter." not in serialiserad

    sk06 = next(p for p in ps if p["fraga_id"] == "SK-06")
    assert sk06["status"] == "ej_tillampligt"
    assert "läget idag" in str(sk06["text"])

    sk02 = next(p for p in ps if p["fraga_id"] == "SK-02")
    assert sk02["status"] == "villkorat"
    assert "matvarde.skyfall.bas.aterkomsttid_ar" in sk02["belagg"]


def test_skyfall_laget_idag_utan_idf_markerar_sk02_som_underlag_saknas() -> None:
    r = _mock_rapport_skyfall(med_jamforelse=False, scenarier=["fore"])
    rt = _mock_rt(med_idf=False)

    ps = pastaenden_for(rt, r)
    sk02 = next(p for p in ps if p["fraga_id"] == "SK-02")
    assert sk02["status"] == "underlag_saknas"
    assert "fast regndjup" in str(sk02["text"])


def test_skyfall_utan_jamforelse_och_utan_scenarier_gissar_inte() -> None:
    r = _mock_rapport_skyfall(med_jamforelse=False, scenarier=None)
    with pytest.raises(ValueError, match="jämförelse"):
        skyfall_pastaenden(r, demo=False)


def test_dagsljus_rapport_pa_enbart_laget_idag_lyckas() -> None:
    r = SimpleNamespace(
        projekt="mitt-projekt",
        amne="dagsljus",
        fragor=["DL-02", "DL-10", "DL-15", "SO-01", "SO-05", "SO-08", "SO-09", "SO-11", "SO-15"],
        installningar=SimpleNamespace(sprakprofil="sbk"),
        korningar=[
            {
                "korning_id": "k-sol-lage-idag",
                "analyser": [
                    {"analys": "sol", "losare": "dagsljus.soltimmar_mark", "scenarier": ["bas"]},
                    {"analys": "vsc", "losare": "dagsljus.vsc_fasad", "scenarier": ["bas"]},
                ],
                "jamforelser": [],
                "scenarier": ["bas"],
                "underlag": {"slag": "lage_idag"},
            }
        ],
        mottagare=[],
    )
    rt = _mock_rt(har_avgransning=False)

    assert ar_lage_idag(r) is True
    ps = pastaenden_for(rt, r)
    per_id = {p["fraga_id"]: p for p in ps}
    for fid in ("DL-10", "DL-15", "SO-01", "SO-09", "SO-11"):
        assert per_id[fid]["status"] == "ej_tillampligt"
        assert "läget idag" in str(per_id[fid]["text"])
    assert "omrade.hela_omradet.sol.bas.soltimmar.medel" in str(per_id["SO-05"]["text"])
    assert "läget idag" in str(per_id["SO-15"]["text"])
    assert "hela beräkningsområdet" in str(per_id["SO-15"]["text"])


def test_hela_berakningsomradet_byts_till_inom_projektets_polygon() -> None:
    assert (
        anpassa_omradestext("Djupet i hela beräkningsområdet är 0,3 m.", har_avgransning=True)
        == f"Djupet {INOM_PROJEKTETS_POLYGON} är 0,3 m."
    )
    assert (
        anpassa_omradestext("Djupet i hela beräkningsområdet är 0,3 m.", har_avgransning=False)
        == "Djupet i hela beräkningsområdet är 0,3 m."
    )

    # Via rt.kalla.har_avgransning(korning_id) på skyfall läget idag
    r_skyfall = _mock_rapport_skyfall(med_jamforelse=False, scenarier=["bas"])
    rt_poly = _mock_rt(har_avgransning=True)
    assert har_avgransningspolygon(rt_poly, r_skyfall) is True

    ps_skyfall = pastaenden_for(rt_poly, r_skyfall)
    sk01 = next(p for p in ps_skyfall if p["fraga_id"] == "SK-01")
    sk12 = next(p for p in ps_skyfall if p["fraga_id"] == "SK-12")
    assert INOM_PROJEKTETS_POLYGON in str(sk01["text"])
    assert "hela beräkningsområdet" not in str(sk01["text"])
    assert INOM_PROJEKTETS_POLYGON in str(sk12["text"])
    assert "hela beräkningsområdet" not in str(sk12["text"])

    # Via dagsljus på läget idag med avgränsningspolygon
    r_sol = SimpleNamespace(
        projekt="mitt-projekt",
        amne="dagsljus",
        fragor=["SO-15"],
        installningar=SimpleNamespace(sprakprofil="sbk"),
        korningar=[
            {
                "korning_id": "k-sol-poly",
                "analyser": [
                    {
                        "analys": "sol",
                        "losare": "dagsljus.soltimmar_mark",
                        "scenarier": ["bas"],
                    }
                ],
                "jamforelser": [],
                "scenarier": ["bas"],
                "har_avgransning": True,
            }
        ],
        mottagare=[],
    )
    ps_sol = dagsljus_pastaenden(rt_poly, r_sol, har_avgransning=True)
    assert INOM_PROJEKTETS_POLYGON in str(ps_sol[0]["text"])
    assert "hela beräkningsområdet" not in str(ps_sol[0]["text"])
