"""Tester för PR-2 (Göteborg R2-rapporter): P0-2, P0-3a, P0-4 och P1-4."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rapport.belaggskalla import (
    MATTKATALOG,
    TVAD_MATVARDEN,
    BelaggFel,
    FlodenBelaggKalla,
)
from rapport.bestallning import BestallningFel, skapa_bestallning
from rapport.fragekatalog import Fragekatalog
from rapport.pastaende import siffror_utanfor_platshallare
from rapport.referensflode import pastaenden as referens_pastaenden
from rapport.skisskontroll import _ekologitext
from rapport.skissrapport import (
    Karta,
    Rapport,
    RapportTjanst,
    _antaganden,
    _regnantagande,
    _skalfelsantagande,
)
from rapport.webbflode import pastaenden_for, skyfall_pastaenden

REPO = Path(__file__).resolve().parents[2]


# ---- P0-2: regnproveniens i _antaganden ----


def test_antaganden_fast_regn_mm_utan_none() -> None:
    res: dict[str, Any] = {
        "antaganden": {
            "regn": [
                {
                    "analys": "skyfall",
                    "kalla": None,
                    "regn_mm": 118.348,
                    "varaktighet_min": 360.0,
                    "klimatfaktor": None,
                }
            ]
        }
    }
    ant = _antaganden(res, ["skyfall"])
    assert len(ant) == 1
    assert ant[0] == "Regn: angiven regnmängd 118,3 mm, varaktighet 360 min."
    assert "None" not in ant[0]
    assert "Noneregn" not in ant[0]

    # Utan varaktighet_min
    rad_utan_varakt = _regnantagande(
        {"analys": "skyfall", "kalla": None, "regn_mm": 50.0, "klimatfaktor": None}
    )
    assert rad_utan_varakt == "Regn: angiven regnmängd 50 mm."
    assert "None" not in rad_utan_varakt


def test_antaganden_dahlstrom_2010_utan_tabell_och_none() -> None:
    res: dict[str, Any] = {
        "antaganden": {
            "regn": [
                {
                    "analys": "skyfall",
                    "kalla": "dahlstrom-2010",
                    "rapport": "Dahlström (2010) / Svenskt Vatten P104 & P110",
                    "tabell": None,
                    "aterkomsttid_ar": 100.0,
                    "varaktighet_min": 360.0,
                    "regntyp": "block",
                    "klimatfaktor": 1.25,
                    "regn_mm": 118.348,
                }
            ]
        }
    }
    ant = _antaganden(res, ["skyfall"])
    assert len(ant) == 1
    assert "(None)" not in ant[0]
    assert "None" not in ant[0]
    assert ant[0].startswith(
        "Regn: Dahlström (2010) / Svenskt Vatten P104 & P110, återkomsttid 100 år"
    )
    assert "varaktighet 360 min" in ant[0]
    assert "blockregn" in ant[0]
    assert "klimatfaktor 1,25 (uttrycklig parameter)" in ant[0]
    assert "118,3 mm." in ant[0]


def test_antaganden_stockholmsregn_behaller_tabell() -> None:
    res: dict[str, Any] = {
        "antaganden": {
            "regn": [
                {
                    "analys": "skyfall",
                    "kalla": "stockholmsregn-2024",
                    "rapport": "Stockholmsregn 2024",
                    "tabell": "Tabell 7",
                    "aterkomsttid_ar": 100.0,
                    "varaktighet_min": 60.0,
                    "regntyp": "block",
                    "klimatfaktor": 1.4,
                    "regn_mm": 64.96,
                }
            ]
        }
    }
    ant = _antaganden(res, ["skyfall"])
    assert ant == [
        "Regn: Stockholmsregn 2024 (Tabell 7), återkomsttid 100 år, varaktighet 60 min, "
        "blockregn, klimatfaktor 1,4 (uttrycklig parameter), 65 mm."
    ]


# ---- P0-2: SK-02 i referensflode & webbflode ----


def test_referensflode_och_webbflode_anvander_faktisk_regnkalla() -> None:
    dahl = "Dahlström (2010) / Svenskt Vatten P104 & P110"
    for profil in ("sbk", "neutral"):
        ps = referens_pastaenden("skyfall", profil, regnkalla=dahl, regntyp="block")
        sk02 = next(p for p in ps if p["fraga_id"] == "SK-02")
        assert dahl in str(sk02["text"])
        assert "Stockholmsregn 2024" not in str(sk02["text"])
        assert siffror_utanfor_platshallare(str(sk02["text"])) == []

    # Webbflödet (både före/efter och läget idag) läser ur antaganden.regn
    for med_jf in (True, False):
        korning: dict[str, Any] = {
            "korning_id": "k-gbg-1",
            "analyser": [
                {
                    "analys": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "scenarier": ["bas", "ritat"],
                }
            ],
            "jamforelser": (
                [
                    {
                        "analys": "skyfall",
                        "utdata": "max_djup",
                        "fore": "bas",
                        "efter": "ritat",
                        "namn": "skyfall_max_djup_ritat_minus_bas",
                    }
                ]
                if med_jf
                else []
            ),
            "antaganden": [
                f"Regn: {dahl}, återkomsttid 100 år, varaktighet 360 min, blockregn, 118,3 mm."
            ],
        }
        r = SimpleNamespace(
            projekt="gbg-haga",
            amne="skyfall",
            fragor=[f"SK-{i:02d}" for i in range(1, 16)],
            installningar=SimpleNamespace(sprakprofil="sbk"),
            korningar=[korning],
            mottagare=[],
        )
        ps = skyfall_pastaenden(r, demo=False, regn_ur_idf=True)  # type: ignore[arg-type]
        sk02 = next(p for p in ps if p["fraga_id"] == "SK-02")
        assert dahl in str(sk02["text"])
        assert "Stockholmsregn 2024" not in str(sk02["text"])
        assert siffror_utanfor_platshallare(str(sk02["text"])) == []

        # Även via pastaenden_for med _resultat-källa
        rt = SimpleNamespace(
            tjanst=SimpleNamespace(
                resultat_sammanfattning=lambda *a, **kw: {
                    "matvarden": [
                        {"rader": [{"ruta": None, "varden": {"aterkomsttid_ar": 100.0}}]}
                    ]
                }
            ),
            kalla=SimpleNamespace(
                har_avgransning=lambda kid: False,
                _resultat=lambda kid: {
                    "antaganden": {
                        "regn": [
                            {
                                "analys": "skyfall",
                                "kalla": "dahlstrom-2010",
                                "rapport": dahl,
                                "tabell": None,
                                "regntyp": "block",
                            }
                        ]
                    }
                },
            ),
        )
        ps2 = pastaenden_for(rt, r)  # type: ignore[arg-type]
        sk02_2 = next(p for p in ps2 if p["fraga_id"] == "SK-02")
        assert dahl in str(sk02_2["text"])
        assert "(None)" not in str(sk02_2["text"])
        assert "Stockholmsregn 2024" not in str(sk02_2["text"])


# ---- P0-2: _TILLATNA_BETECKNINGAR i pastaende.py ----


def test_pastaende_tillater_goteborgsbeteckningar_men_stoppar_fria_tal() -> None:
    godkand_text = (
        "Enligt Dahlström (2010), Svenskt Vatten P104 och P110, omprojicerat från "
        "EPSG:3007 (SWEREF 99 12 00) och EPSG:3006 (SWEREF 99 TM) till EPSG:3011 "
        "(SWEREF 99 18 00) i RH 2000, med marktäcke ur NMD 2018 / NMD 2023 och "
        "referensjämförelse mot LISFLOOD-FP v8.2.1: största djupet är {{matt:a.b}}."
    )
    assert siffror_utanfor_platshallare(godkand_text) == []

    # Fria tal och påhittade siffror ska fortfarande vägras strikt!
    assert siffror_utanfor_platshallare(
        "Dahlström (2010) ger 118,3 mm under 100 år."
    ) == ["118,3", "100"]
    assert siffror_utanfor_platshallare("Djupet i EPSG:3007 är 0,35 m.") == ["0,35"]
    assert siffror_utanfor_platshallare("År 2025 uppmättes 42 mm.") == ["2025", "42"]
    assert siffror_utanfor_platshallare("P105 är inte en tillåten beteckning.") == ["105"]


# ---- P0-2: belaggskalla + adresser() för explicit regn_mm ----


def _fake_belagg_env(
    tmp_path: Path,
    *,
    losare: str,
    varden: dict[str, Any],
    parametrar: dict[str, Any],
) -> tuple[FlodenBelaggKalla, Any]:
    utmapp = tmp_path / "korning"
    manifest_dir = utmapp / "skyfall" / "bas"
    manifest_dir.mkdir(parents=True)
    (utmapp / "run.json").write_text(json.dumps({"kodversion": "abc123"}), encoding="utf-8")
    (manifest_dir / "run.json").write_text(
        json.dumps({"parametrar": parametrar, "matvarden": varden}),
        encoding="utf-8",
    )

    class _Fragor:
        def _hamta(self, korning_id: str) -> Any:
            return SimpleNamespace(status="klar", utmapp=str(utmapp), projekt_id="gbg-test")

        def projekt_avgransning(self, korning_id: str) -> None:
            return None

    class _Tjanst:
        fragor = _Fragor()
        app = SimpleNamespace()
        rot = tmp_path

        def _resultat(self, korning_id: str) -> dict[str, Any]:
            return {
                "tabeller": [
                    {"analys": "skyfall", "losare": losare, "rader": [{"scenario": "bas"}]}
                ],
                "jamforelser": [],
                "lager": [],
            }

        def resultat_sammanfattning(
            self,
            korning_id: str,
            *,
            analys: str,
            scenario: str,
            ruta: str | None = None,
            max_rader: int = 500,
        ) -> dict[str, Any]:
            return {
                "matvarden": [
                    {
                        "analys": analys,
                        "rader": [
                            {
                                "scenario": scenario,
                                "ruta": ruta,
                                "varden": dict(varden),
                                "belagg": {"fil": f"{analys}/{scenario}/run.json"},
                            }
                        ],
                    }
                ]
            }

    tjanst = _Tjanst()
    return FlodenBelaggKalla(tjanst), tjanst  # type: ignore[arg-type]


def test_adresser_filtrerar_och_belaggskalla_hamtar_regn_mm_ur_parametrar(tmp_path: Path) -> None:
    kalla, tjanst = _fake_belagg_env(
        tmp_path,
        losare="vatten.statisk_fyllning",
        varden={
            "max_djup_m": 0.42,
            "lagrat_m3": 120.5,
            "ut_m3": 85.0,
            "volymfel_rel": 1.2e-15,
            "avtappning_mm_h": 0.0,
            "takvatten_andel_ledning": 0.0,
        },
        parametrar={
            "regn_mm": 118.348,
            "varaktighet_min": 360.0,
            "klimatfaktor": None,
        },
    )
    rt = RapportTjanst(tjanst, rapportmapp=tmp_path / "rapporter")
    r = SimpleNamespace(
        amne="skyfall",
        korningar=[
            {
                "korning_id": "k-1",
                "jamforelser": [],
                "analyser": [
                    {"analys": "skyfall", "losare": "vatten.statisk_fyllning", "scenarier": ["bas"]}
                ],
            }
        ],
        mottagare=[],
    )
    adresser = rt.adresser(r)  # type: ignore[arg-type]
    assert "matvarde.skyfall.bas.regn_mm" in adresser
    assert "matvarde.skyfall.bas.varaktighet_min" in adresser
    assert "matvarde.skyfall.bas.klimatfaktor" not in adresser
    assert "matvarde.skyfall.bas.aterkomsttid_ar" not in adresser

    # Varje utlovad adress i adresser() ska gå att hämta via belaggskalla.hamta()!
    for adr in adresser:
        b = kalla.hamta(adr, "k-1", {})
        assert b.adress == adr
        if adr.endswith(".regn_mm"):
            assert b.varde == pytest.approx(118.348)
            assert b.enhet == "mm"
        if adr.endswith(".varaktighet_min"):
            assert b.varde == pytest.approx(360.0)
            assert b.enhet == "min"

    # Saknad klimatfaktor ska fortfarande vägras tydligt
    with pytest.raises(BelaggFel, match="klimatfaktor"):
        kalla.hamta("matvarde.skyfall.bas.klimatfaktor", "k-1", {})


# ---- P0-3a: bindestreck i bestallning.py ----


def test_bestallning_tillater_bindestreck_i_scenarioslug() -> None:
    katalog = Fragekatalog.las_alla(REPO / "docs" / "utredningar-stockholm" / "fragor.json")
    b = skapa_bestallning(
        katalog,
        syfte="Pröva gårdshus i Haga.",
        amnen=["skyfall"],
        projekt="gbg-haga",
        scenario={"fore": "bas", "efter": ["haga-fiktivt-gardshus"]},
    )
    assert b.scenario == {"fore": "bas", "efter": ["haga-fiktivt-gardshus"]}

    # Ogiltiga namn (inledande bindestreck, för långt) vägras fortfarande
    with pytest.raises(BestallningFel, match="scenario.efter"):
        skapa_bestallning(
            katalog,
            syfte="Ogiltigt namn.",
            amnen=["skyfall"],
            projekt="gbg-haga",
            scenario={"fore": "bas", "efter": ["-ogiltigt"]},
        )
    with pytest.raises(BestallningFel, match="scenario.efter"):
        skapa_bestallning(
            katalog,
            syfte="För långt namn.",
            amnen=["skyfall"],
            projekt="gbg-haga",
            scenario={"fore": "bas", "efter": ["a" * 65]},
        )


# ---- P0-4: vatten.2d i MATTKATALOG + nuläge ekologi ----


def test_vatten_2d_i_mattkatalog_och_hamtbar_som_belagg(tmp_path: Path) -> None:
    for roll in ("max_djup", "hastighet", "ankomsttid"):
        assert ("vatten.2d", roll) in MATTKATALOG
    for nyckel in TVAD_MATVARDEN:
        assert ("vatten.2d", f"matvarde:{nyckel}") in MATTKATALOG

    kalla, tjanst = _fake_belagg_env(
        tmp_path,
        losare="vatten.2d",
        varden={
            "max_djup_m": 0.55,
            "max_hastighet_m_s": 1.42,
            "max_fara_m2_s": 0.68,
            "in_m3": 1500.0,
            "lagrat_m3": 420.0,
            "ut_m3": 980.0,
            "avtappat_m3": 50.0,
            "infiltrerat_m3": 40.0,
            "initial_forlust_m3": 10.0,
            "volymfel_rel": 2.1e-15,
            "massbalans_fel": 2.1e-15,
            "antal_tidssteg": 412,
            "regn_mm": 118.3,
            "varaktighet_min": 360.0,
        },
        parametrar={"regn_mm": 118.3, "varaktighet_min": 360.0},
    )
    b_djup = kalla.hamta("matvarde.skyfall.bas.max_djup_m", "k-2d", {})
    assert b_djup.varde == pytest.approx(0.55)
    assert b_djup.enhet == "m"

    b_hast = kalla.hamta("matvarde.skyfall.bas.max_hastighet_m_s", "k-2d", {})
    assert b_hast.varde == pytest.approx(1.42)
    assert b_hast.enhet == "m/s"

    b_fara = kalla.hamta("matvarde.skyfall.bas.max_fara_m2_s", "k-2d", {})
    assert b_fara.varde == pytest.approx(0.68)
    assert b_fara.enhet == "m²/s"

    b_steg = kalla.hamta("matvarde.skyfall.bas.antal_tidssteg", "k-2d", {})
    assert b_steg.varde == pytest.approx(412.0)
    assert b_steg.enhet == "st"

    # adresser() för vatten.2d innehåller de nya måtten och är 100 % hämtbara
    rt = RapportTjanst(tjanst, rapportmapp=tmp_path / "rapporter")
    r = SimpleNamespace(
        amne="skyfall",
        korningar=[
            {
                "korning_id": "k-2d",
                "jamforelser": [],
                "analyser": [{"analys": "skyfall", "losare": "vatten.2d", "scenarier": ["bas"]}],
            }
        ],
        mottagare=[],
    )
    adr = rt.adresser(r)  # type: ignore[arg-type]
    assert "matvarde.skyfall.bas.max_hastighet_m_s" in adr
    assert "matvarde.skyfall.bas.max_fara_m2_s" in adr
    for a in adr:
        kalla.hamta(a, "k-2d", {})


def test_ekologitext_godkanner_nulage_med_bara_baskarta() -> None:
    text = (
        "Resultatet visar potentiell livsmiljö, okänd kvalitet för vald signalart. "
        "Källa: Stockholms stad (2020)."
    )
    baskarta = Karta(
        kart_id="ekologi_stromtathet_bas.bas",
        korning_id="k-eko",
        jamforelse="",
        analys="ekologi",
        lager_id="ekologi.bas.stromtathet",
        typ="bas",
        scenario="bas",
        fil="kartor/bas.png",
        png_sha256="a" * 64,
        bildtext="Strömtäthet bas",
        meta={},
    )
    rapport_nulage = SimpleNamespace(
        korningar=[{"korning_id": "k-eko", "jamforelser": [], "antaganden": []}],
        kartor=(baskarta,),
    )
    assert _ekologitext(rapport_nulage, text) == []  # type: ignore[arg-type]

    # Nuläge utan baskarta ska fortfarande ge fel
    rapport_nulage_utan_karta = SimpleNamespace(
        korningar=[{"korning_id": "k-eko", "jamforelser": [], "antaganden": []}],
        kartor=(),
    )
    assert len(_ekologitext(rapport_nulage_utan_karta, text)) == 1  # type: ignore[arg-type]

    # Rapport med jämförelser kräver fortfarande bas, scenario och skillnad
    rapport_med_jf = SimpleNamespace(
        korningar=[
            {
                "korning_id": "k-eko",
                "jamforelser": [{"namn": "eko_diff", "fore": "bas", "efter": "ritat"}],
                "antaganden": [],
            }
        ],
        kartor=(baskarta,),
    )
    fel_jf = _ekologitext(rapport_med_jf, text)  # type: ignore[arg-type]
    assert len(fel_jf) == 1
    assert "före, efter eller som skillnad" in fel_jf[0]


# ---- P1-4: Göteborg skalfelsnot i antaganden ----


def test_skalfelsantagande_goteborg_epsg3007_och_tyst_for_stockholm() -> None:
    koord_gbg = {
        "publicering_epsg": 3007,
        "intern_epsg": 3011,
        "skalfel_langd_procent": 0.1592,
        "skalfel_yta_procent": 0.3186,
    }
    not_gbg = _skalfelsantagande(koord_gbg)
    assert not_gbg is not None
    assert "EPSG:3007" in not_gbg
    assert "EPSG:3011" in not_gbg
    assert "0,16 %" in not_gbg
    assert "0,32 %" in not_gbg

    # Stockholm (3011 == 3011) ska inte få någon skalfelsnot
    koord_sthlm = {
        "publicering_epsg": 3011,
        "intern_epsg": 3011,
        "skalfel_langd_procent": 0.0,
        "skalfel_yta_procent": 0.0,
    }
    assert _skalfelsantagande(koord_sthlm) is None

    # Integrerat i _antaganden
    res: dict[str, Any] = {
        "antaganden": {
            "regn": [
                {"analys": "skyfall", "kalla": None, "regn_mm": 118.3, "varaktighet_min": 360.0}
            ]
        }
    }
    ant = _antaganden(res, ["skyfall"], koordinater=koord_gbg)
    assert any("EPSG:3007" in a and "0,16 %" in a and "0,32 %" in a for a in ant)


def test_rapporttjanst_laser_geografi_koordinater_fran_manifest(tmp_path: Path) -> None:
    # Bygg projekt.json + geografimanifest på disk i samma struktur som Floden använder
    proj_rot = tmp_path / "projekt"
    geo_rot = tmp_path / "geografier"
    (proj_rot / "haga").mkdir(parents=True)
    (proj_rot / "haga" / "projekt.json").write_text(
        json.dumps({"projekt_id": "gbg-haga", "geografi": "goteborg-test@v5"}),
        encoding="utf-8",
    )
    ver_dir = geo_rot / "goteborg-test" / "v5"
    ver_dir.mkdir(parents=True)
    (ver_dir / "manifest.json").write_text(
        json.dumps(
            {
                "kontrakt": "K46/v1",
                "id": "goteborg-test",
                "version": "v5",
                "koordinater": {
                    "publicering_epsg": 3007,
                    "intern_epsg": 3011,
                    "skalfel_langd_procent": 0.16,
                    "skalfel_yta_procent": 0.32,
                },
            }
        ),
        encoding="utf-8",
    )

    class _Fragor:
        projektrot = proj_rot

        def korning_eller_none(self, korning_id: str) -> Any:
            return SimpleNamespace(projekt_id="gbg-haga")

    tjanst = SimpleNamespace(
        fragor=_Fragor(),
        app=SimpleNamespace(geomapp=geo_rot),
        rot=tmp_path,
    )
    rt = RapportTjanst(tjanst, rapportmapp=tmp_path / "rapporter")  # type: ignore[arg-type]
    koord = rt._geografi_koordinater("k-haga-1", projekt="gbg-haga")
    assert koord is not None
    assert koord["publicering_epsg"] == 3007
    skalnot = _skalfelsantagande(koord)
    assert skalnot is not None
    assert "EPSG:3007" in skalnot
    assert "0,16 %" in skalnot
    assert "0,32 %" in skalnot
    assert Rapport is not None
