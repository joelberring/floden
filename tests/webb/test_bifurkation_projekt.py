"""`POST /api/analys/bifurkationer` på projektets egen markmodell (demokvarteret).

Anropen går genom ASGI-appen (`webb.asgi.skapa_asgi_app`) med en enkel testklient, utan server.
Demovärldens vattenruta (K1 v2, fokusområdet 300 x 300 m, 1 m-celler) byggs i en tmp-mapp: det tar
några sekunder, och ingen körning behövs eftersom analysen bara läser markmodellen.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from demovarld.varld import DemoVarld, bygg_ruta_vatten
from konto.adapter import KontoAdapter
from tests.konto.conftest import ANNA, ORIGIN, Miljo
from tests.webb.test_asgi import _anropa_asgi
from testsvit.k1_io import las_geotiff
from webb.api import FlodenApiApp
from webb.asgi import FlodenAsgiApp, skapa_asgi_app

PROJEKT = "demokvarteret"
VAG = "/api/analys/bifurkationer"
MARKSLAG_BYGGNAD = 9


class Varld:
    """Demovärldens vattenruta och en konfigurationsfil som pekar på den."""

    def __init__(self, rot: Path) -> None:
        self.rot = rot
        self.varld = DemoVarld()
        bas = bygg_ruta_vatten(self.varld, rot / "varld" / "vatten" / "bas")
        (self.ruta,) = sorted(bas.glob("mm_*"))
        self.konfig = rot / "config.toml"
        self.konfig.write_text(
            '[webb]\ndemomapp = "varld"\nfragemappar = ["fragor"]\ncachemapp = "cache"\n'
            'korningsmapp = "korningar"\nklientmapp = "klient"\n',
            encoding="utf-8",
        )

    def asgi(self, konto: KontoAdapter | None = None) -> FlodenAsgiApp:
        # Testsviten är en tom mapp: projektets analys får inte behöva den.
        tom = self.rot / "tom-testsvit"
        tom.mkdir(exist_ok=True)
        return skapa_asgi_app(testsvit_rot=tom, konfig_fil=self.konfig, konto=konto)


@pytest.fixture(scope="module")
def varld(tmp_path_factory: pytest.TempPathFactory) -> Varld:
    return Varld(tmp_path_factory.mktemp("demovarld"))


def _post(
    app: FlodenAsgiApp,
    kropp: bytes | dict[str, Any],
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, bytes]:
    raa = kropp if isinstance(kropp, bytes) else json.dumps(kropp).encode("utf-8")
    h = [(b"content-type", b"application/json"), *(headers or [])]
    status, _, svar = asyncio.run(_anropa_asgi(app, "POST", VAG, headers=h, chunks=(raa,)))
    return status, svar


def test_demokvarteret_ger_overfallspunkter_inom_projektets_utbredning(varld: Varld) -> None:
    status, kropp = _post(varld.asgi(), {"omrade_id": PROJEKT})
    assert status == 200, kropp
    d = json.loads(kropp)
    assert d["format"] == "floden-bifurkation-felindikator/v1"
    assert d["omrade_id"] == PROJEKT
    punkter = d["punkter"]
    assert d["antal_instabila"] == len(punkter) == 5  # känt svar för demovärlden (1 m-celler)
    assert all(p["instabil"] for p in punkter)

    mark, geo = las_geotiff(varld.ruta / "mark.tif")
    slag, _ = las_geotiff(varld.ruta / "markslag.tif")
    e0, n0, e1, _ = varld.varld.fokus_3011  # fokusområdet = vattenrutan
    assert (geo.x_min, geo.x_max) == (e0, e1)
    for p in punkter:
        for xnyckel, ynyckel in (("overfall_x", "overfall_y"), ("alternativ_x", "alternativ_y")):
            x, y = p[xnyckel], p[ynyckel]
            assert geo.x_min <= x <= geo.x_max and geo.y_min <= y <= geo.y_max, (xnyckel, p)
            # Byggnadernas fotavtryck är inte mark: ingen överfallspunkt ligger på ett tak.
            kol, rad = int((x - geo.x_min) // geo.pixelstorlek_x), int((geo.y_max - y) // 1.0)
            assert int(np.asarray(slag)[rad, kol]) != MARKSLAG_BYGGNAD, (xnyckel, p)
        assert p["marginal_m"] >= 0.0 and p["paverkad_volym_m3"] > 0.0
    f = d["felindikator"]
    assert 0.0 <= f["andel_2d_yta"] <= 1.0 and f["antal_aktiva_celler"] > 0
    assert mark.shape == slag.shape


def test_samma_anrop_ger_samma_svar_bit_for_bit(varld: Varld) -> None:
    app = varld.asgi()
    a = _post(app, {"omrade_id": PROJEKT, "k_sigma": 2.0, "referensregn_mm": 100.0})
    b = _post(app, {"omrade_id": PROJEKT, "k_sigma": 2.0, "referensregn_mm": 100.0})
    c = _post(varld.asgi(), {"omrade_id": PROJEKT})  # ny app, tom cache, standardvärden
    assert a[0] == b[0] == c[0] == 200
    assert a[1] == b[1] == c[1]


def test_takvattnet_ingar_i_paverkad_volym(varld: Varld) -> None:
    """Regnet på taken fyller sänkorna i vattenflödet, så det räknas i det berörda flödet."""
    from sankgraf.fyllning import kor_statisk_k1_mapp

    graf, _ = kor_statisk_k1_mapp(varld.ruta, regn_m=0.1)
    assert graf.takvatten is not None and not graf.takvatten.tom
    _, kropp = _post(varld.asgi(), {"omrade_id": PROJEKT})
    d = json.loads(kropp)
    # Det största flödet är större än markens egen avrinningsyta, eftersom taken avvattnas dit.
    bara_mark = max(n.avrinningsyta_m2 for n in graf.noder.values() if n.ar_lov)
    assert max(p["paverkad_avrinningsyta_m2"] for p in d["punkter"]) > bara_mark


@pytest.mark.parametrize(
    ("kropp", "namn"),
    [
        (b'{"omrade_id": "demokvarteret", "k_sigma": "abc"}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": "2"}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": true}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": [2]}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": NaN}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": Infinity}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": 1e999}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": -0.5}', "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "k_sigma": ' + b"9" * 400 + b"}", "k_sigma"),
        (b'{"omrade_id": "demokvarteret", "referensregn_mm": -1}', "referensregn_mm"),
        (b'{"omrade_id": "demokvarteret", "referensregn_mm": NaN}', "referensregn_mm"),
        (b'{"omrade_id": "demokvarteret", "referensregn_mm": "hundra"}', "referensregn_mm"),
    ],
)
def test_ogiltiga_parametrar_ger_400_med_svenskt_fel(varld: Varld, kropp: bytes, namn: str) -> None:
    status, svar = _post(varld.asgi(), kropp)
    assert status == 400, svar
    fel = json.loads(svar)["fel"]
    assert f"'{namn}'" in fel


def test_ovriga_felaktiga_anrop_ger_400_eller_404_aldrig_500(varld: Varld) -> None:
    app = varld.asgi()
    for kropp, forvantat in (
        (b"", 400),
        (b"{inte json", 400),
        (b"[1, 2]", 400),
        (b"{}", 400),
        (b'{"omrade_id": 7}', 400),
        (b"[" * 100000, 400),
        (b'{"omrade_id": "finns-inte"}', 404),
        (b'{"omrade_id": "DEMOKVARTERET"}', 404),  # projekt-id är exakta, som i projekt-API:t
    ):
        status, _ = _post(app, kropp)
        assert status == forvantat, kropp[:40]


def test_projekt_utan_byggd_markmodell_ger_404(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('[webb]\ndemomapp = "varld"\n', encoding="utf-8")
    app = skapa_asgi_app(testsvit_rot=tmp_path / "t", konfig_fil=tmp_path / "config.toml")
    status, svar = _post(app, {"omrade_id": PROJEKT})
    assert status == 404
    assert "floden demo" in json.loads(svar)["fel"]


# --- projektbehörighet (inloggning på) ------------------------------------------------------


def _registrera_demokvarteret(miljo: Miljo) -> None:
    """Kontoregistret har projekt med slumpade id; demokvarteret läggs in med sitt eget id."""
    r = miljo.register
    org = r.skapa_organisation("Exempelkommun")
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, org)
    with r.db.transaktion() as c:
        c.execute(
            "INSERT INTO projekt (id, organisation_id, forvaltning_id, namn, skapad)"
            " VALUES (?, ?, NULL, 'Demokvarteret', ?)",
            (PROJEKT, org, r.nu()),
        )
    annat = r.skapa_projekt(org, "Annat projekt")
    r.bjud_in(admin, PROJEKT, "anna@example.test", "lasare")
    r.bjud_in(admin, annat, "bo@example.test", "lasare")


def test_projektets_overfallsanalys_kraver_projektroll(varld: Varld, miljo: Miljo) -> None:
    _registrera_demokvarteret(miljo)
    app = varld.asgi(miljo.adapter)
    kropp = json.dumps({"omrade_id": PROJEKT}).encode("utf-8")

    def anropa(webb: Any | None) -> int:
        h: list[tuple[bytes, bytes]] = [(b"origin", ORIGIN.encode())]
        if webb is not None:
            kakor = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
            h += [(b"cookie", kakor.encode()), *((k.lower().encode(), v.encode())
                                               for k, v in webb.csrf_headers().items())]
        return _post(app, kropp, h)[0]

    assert anropa(None) == 401  # utloggad

    anna = miljo.webblasare()
    assert anna.logga_in(miljo.idp, ANNA)[0] == 302
    assert anropa(anna) == 200  # läsare i projektet

    bo = miljo.webblasare()
    assert bo.logga_in(miljo.idp, {**ANNA, "sub": "1002", "email": "bo@example.test"})[0] == 302
    # Demokvarteret är påhittat: alla inloggade har rollen `demoprojekt_roll` (förval utredare).
    assert anropa(bo) == 200
    # Med `demoprojekt_roll = "av"` krävs uttrycklig roll även där, som för riktiga projekt.
    object.__setattr__(miljo.adapter.konfig, "demoprojekt_roll", "av")
    assert anropa(bo) == 403
    assert anropa(anna) == 200


def test_testsvitens_omraden_kraver_bara_inloggning(varld: Varld, miljo: Miljo) -> None:
    """Områden ur testsviten har ingen projektroll; deras behörighet är oförändrad (inloggning)."""
    _registrera_demokvarteret(miljo)
    app = varld.asgi(miljo.adapter)
    bo = miljo.webblasare()
    assert bo.logga_in(miljo.idp, {**ANNA, "sub": "1002", "email": "bo@example.test"})[0] == 302
    kakor = "; ".join(f"{k}={v}" for k, v in bo.kakor.items()).encode()
    h = [(b"origin", ORIGIN.encode()), (b"cookie", kakor)]
    h += [(k.lower().encode(), v.encode()) for k, v in bo.csrf_headers().items()]
    status, _ = _post(app, {"omrade_id": "A1_enkel_sanka"}, h)
    assert status == 404  # når endpointen (här 404: testsviten i den här appen är tom)


def test_testsvitens_rot_ar_oberoende_av_arbetskatalogen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(tmp_path)  # som i Docker, där arbetskatalogen inte är repots rot
    # Utan eget val: bredvid konfigurationsfilen, eller i repots rot om ingen fil finns.
    assert FlodenApiApp(konfig_fil=repo / "config.toml").testsvit_rot == repo / "testsvit"
    assert FlodenApiApp().testsvit_rot == repo / "testsvit"
    assert (
        FlodenApiApp(konfig_fil=tmp_path / "saknas.toml").testsvit_rot == repo / "testsvit"
    )
    assert skapa_asgi_app().app.testsvit_rot == repo / "testsvit"
    egen = tmp_path / "egen"
    egen.mkdir()
    (egen / "config.toml").write_text("", encoding="utf-8")
    assert FlodenApiApp(konfig_fil=egen / "config.toml").testsvit_rot == egen.resolve() / "testsvit"
    # Ett uttryckligt val används som det är.
    assert FlodenApiApp(testsvit_rot=egen / "x").testsvit_rot == egen / "x"
    # Och områdena hittas fortfarande: A1 ur repots testsvit, trots annan arbetskatalog.
    status, _, kropp = FlodenApiApp().hantera_forfragan("GET", "/api/omraden")
    assert status == 200
    assert any(o["omrade_id"] == "A1_enkel_sanka" for o in json.loads(kropp)["omraden"])
