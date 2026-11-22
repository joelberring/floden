"""Regressionstester för säkerhets-, drift- och volymbalansfixar (del 3)."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from dynamik.tidsstegning import kor_dynamik_tidssteg
from floden_mcp.server import Sessionstillstand
from gemensamt.koordinater import EPSG_INTERN
from konto.konfig import las_konto_konfig
from rapport.installningar import (
    MAX_RESURS_BYTES,
    InstallningsFel,
    RapportInstallningar,
    tillampa,
)
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import GeoTransformInfo
from webb.api import FlodenApiApp, _projekt_krav
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst
from webb.kartverktyg import KartverktygTjanst


def test_wms_ssrf_blockerar_privata_nat_och_ipv4_mappad_ipv6() -> None:
    """WMS-proxyn ska blockera RFC 1918-adresser och IPv4-mappad IPv6 (::ffff:127.0.0.1)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        fragor = FragaTjanst(
            fragemappar=[p], cachemapp=p / "cache", korningsmapp=p / "kor", rot=p
        )
        kt = KartverktygTjanst(fragor, p / "kv", tillat_loopback=False)
        for url in (
            "http://10.0.0.1/wms",
            "http://172.16.0.5/wms",
            "http://192.168.1.10/wms",
            "http://[::ffff:127.0.0.1]/wms",
            "http://[::ffff:169.254.169.254]/latest/meta-data/",
        ):
            with pytest.raises(FlodenWebbFel) as exc:
                kt._kontrollera_adress(url)
            assert exc.value.status_kod == 400


def test_kartverktyg_lista_scenarier_nekar_okant_projekt() -> None:
    """lista_scenarier ska validera projekt-id och neka path traversal."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        fragor = FragaTjanst(
            fragemappar=[p], cachemapp=p / "cache", korningsmapp=p / "kor", rot=p
        )
        kt = KartverktygTjanst(fragor, p / "kv")
        with pytest.raises(FlodenWebbFel) as exc:
            kt.lista_scenarier("../etc")
        assert exc.value.status_kod == 404


def test_konto_konfig_tom_miljovariabel_skriver_inte_over_toml() -> None:
    """Tom miljövariabel (som docker-compose.prod.yml) ska falla tillbaka på config.local.toml."""
    konf = las_konto_konfig(
        {
            "konto": {
                "inloggning": "pa",
                "offentlig_url": "https://floden.example.se",
                "leverantorer": {
                    "google": {
                        "aktiv": True,
                        "klient_id": "toml-id",
                        "klient_hemlighet": "toml-hemlighet",
                    }
                },
            }
        },
        miljo={
            "FLODEN_KONTO_GOOGLE_KLIENT_ID": "",
            "FLODEN_KONTO_GOOGLE_KLIENT_HEMLIGHET": "",
        },
    )
    assert konf.leverantorer["google"].klient_id == "toml-id"
    assert konf.leverantorer["google"].hemlighet == "toml-hemlighet"


def test_tidsstegning_volymbalans_exakt() -> None:
    """kor_dynamik_tidssteg ska fylla i regn_mark_m3/takvatten så volymbalans() är exakt."""
    z = np.full((6, 6), 10.0, dtype=np.float32)
    z[2:4, 2:4] = 9.5
    osak = np.zeros((6, 6), dtype=np.float32)
    markslag = np.ones((6, 6), dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=6,
        hojd=6,
        x_min=150000.0,
        y_max=6580006.0,
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        epsg=EPSG_INTERN,
        dtype="float32",
    )
    graf = bygg_sankgraf(z, osak, markslag, geo)
    res = kor_dynamik_tidssteg(graf, [0.0, 600.0], [36.0, 36.0], dt_s=10.0)
    bal = res.volymbalans()
    assert math.isclose(float(bal["regn_mark_m3"]), res.in_m3, rel_tol=1e-9)
    assert abs(float(bal["volymfel_rel"])) < 1e-9


def test_rapport_installningar_blockerar_utanfor_rot_och_for_stor_fil(tmp_path: Path) -> None:
    """Resursinläsning ska begränsas till tillatna_rotter och MAX_RESURS_BYTES."""
    tillaten = tmp_path / "tillaten"
    utanfor = tmp_path / "hemlig_mapp"
    resurser = tmp_path / "resurser"
    tillaten.mkdir()
    utanfor.mkdir()

    ok_png = tillaten / "logga.png"
    utanfor_png = utanfor / "hemlig.png"
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(ok_png)
    Image.new("RGB", (4, 4), color=(40, 50, 60)).save(utanfor_png)

    with pytest.raises(InstallningsFel) as exc_ut:
        tillampa(
            RapportInstallningar(),
            {"logotyp": str(utanfor_png)},
            resurser,
            tillatna_rotter=[tillaten],
        )
    assert "utanför tillåtna mappar" in str(exc_ut.value)
    assert "hemlig_mapp" not in str(exc_ut.value)

    stor_png = tillaten / "stor.png"
    with stor_png.open("wb") as fh:
        fh.truncate(MAX_RESURS_BYTES + 1)
    with pytest.raises(InstallningsFel, match="för stor"):
        tillampa(
            RapportInstallningar(),
            {"logotyp": str(stor_png)},
            resurser,
            tillatna_rotter=[tillaten],
        )

    ny = tillampa(
        RapportInstallningar(),
        {"logotyp": str(ok_png)},
        resurser,
        tillatna_rotter=[tillaten],
    )
    assert ny.logotyp is not None
    assert not list(resurser.glob("*.tmp"))


def test_webb_api_maskerar_ovantat_500_fel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oväntade 500-fel får inte läcka interna felmeddelanden till HTTP-klienten."""
    repo = Path(__file__).resolve().parents[1]
    app = FlodenApiApp(
        testsvit_rot=repo / "testsvit",
        konfig_fil=repo / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )

    def krascha() -> dict[str, object]:
        raise RuntimeError("hemlig-intern-sokvag-/var/db/secret")

    monkeypatch.setattr(app, "halsa", krascha)
    status, _, kropp = app.hantera_forfragan("GET", "/api/halsa")
    assert status == 500
    data = json.loads(kropp)
    assert data["fel"] == "Internt serverfel."
    assert "hemlig-intern-sokvag" not in kropp.decode("utf-8")


def test_projekt_krav_och_behorighetskontroll(tmp_path: Path) -> None:
    """Projektbundna API-vägar kräver läs-/utredarroll när inloggning är aktiv."""
    assert _projekt_krav("GET", "/api/projekt/demo") == ("demo", "lasare")
    assert _projekt_krav("GET", "/api/projekt") is None
    assert _projekt_krav("GET", "/api/3d/demo/terrang/0/0/0") == ("demo", "lasare")
    assert _projekt_krav("GET", "/api/kartverktyg/demo/scenarier") == ("demo", "lasare")
    assert _projekt_krav("POST", "/api/kartverktyg/demo/scenarier") == ("demo", "utredare")
    assert _projekt_krav("GET", "/api/kartverktyg/wms/bild") is None
    assert _projekt_krav("POST", "/api/kartverktyg/wms/lager") is None

    from konto.adapter import KontoAdapter
    from konto.leverantor import google
    from tests.konto.conftest import ANNA, ORIGIN, Miljo, bygg_konfig
    from tests.konto.fake_idp import FalskIdp, Klocka
    from tests.konto.test_inloggning import bjud_in_anna

    klocka = Klocka()
    idp = FalskIdp(klocka)
    lev = google(
        idp.klient_id, idp.hemlighet, discovery_url=idp.discovery_url, issuers=(idp.issuer,)
    )
    adapter = KontoAdapter(
        bygg_konfig(tmp_path), hamtare=idp, klocka=klocka, leverantorer={"google": lev}
    )
    assert adapter.register is not None
    miljo = Miljo(adapter, adapter.register, idp, klocka)
    bjud_in_anna(miljo)
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302
    repo = Path(__file__).resolve().parents[1]
    app = FlodenApiApp(
        testsvit_rot=repo / "testsvit",
        konfig_fil=repo / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=miljo.adapter,
    )
    cookie = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())

    # Utan projektroll -> 403 på projekt-, 3d- och kartverktygs-endpoints.
    for metod, sokvag in (
        ("GET", "/api/projekt/demo"),
        ("GET", "/api/3d/demo"),
        ("GET", "/api/kartverktyg/demo/scenarier"),
    ):
        status, _, kropp = app.hantera_http(metod, sokvag, {"Cookie": cookie}, lambda: b"")
        assert status == 403, f"{metod} {sokvag}: {kropp!r}"

    # Efter tilldelad läsarroll -> GET släpps igenom, men POST kräver utredare (403).
    assert miljo.adapter.register is not None
    reg = miljo.adapter.register
    anna_konto = reg.konto_via_epost(str(ANNA["email"]))
    assert anna_konto is not None
    admin_id = reg.forbered_konto("admin@example.test", "Admin")
    reg.ge_superadmin(admin_id)
    org_id = reg.skapa_organisation("Testkommun")
    pid = reg.skapa_projekt(org_id, "Demo")
    reg.ge_projektroll(admin_id, anna_konto.id, pid, "lasare")
    status_get, _, _ = app.hantera_http(
        "GET", f"/api/projekt/{pid}", {"Cookie": cookie}, lambda: b""
    )
    assert status_get != 403
    status_post, _, _ = app.hantera_http(
        "POST",
        f"/api/kartverktyg/{pid}/scenarier",
        {"Cookie": cookie, "Origin": ORIGIN, **webb.csrf_headers()},
        lambda: b"{}",
    )
    assert status_post == 403


def test_mcp_sessionstillstand_isolerar_http_sessioner() -> None:
    """Två HTTP-sessioner ska inte skriva över varandras modell, roll eller konto."""
    st = Sessionstillstand(miljo={})
    t1 = st.aktivera("http-s1", "konto-1")
    try:
        st.modell = "modell-a"
        st.roll = "roll-a"
        assert st.session_id == "http-s1"
        assert st.konto == "konto-1"
    finally:
        st.aterstall(t1)

    t2 = st.aktivera("http-s2", "konto-2")
    try:
        assert st.session_id == "http-s2"
        assert st.modell is None
        assert st.roll is None
        assert st.konto == "konto-2"
        st.modell = "modell-b"
    finally:
        st.aterstall(t2)

    t1b = st.aktivera("http-s1", "konto-1")
    try:
        assert st.modell == "modell-a"
        assert st.roll == "roll-a"
    finally:
        st.aterstall(t1b)


def test_webb_api_analys_bifurkationer_och_felindikator(tmp_path: Path) -> None:
    """POST /api/analys/bifurkationer ska returnera bifurkationer och 2D-felindikator."""
    repo = Path(__file__).resolve().parents[1]
    app = FlodenApiApp(
        testsvit_rot=repo / "testsvit",
        konfig_fil=repo / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    kropp_in = json.dumps({"omrade_id": "A1_enkel_sanka", "referensregn_mm": 50.0}).encode("utf-8")
    status, _, kropp = app.hantera_forfragan("POST", "/api/analys/bifurkationer", kropp_in)
    assert status == 200
    data = json.loads(kropp)
    assert data["format"] == "floden-bifurkation-felindikator/v1"
    assert data["omrade_id"] == "A1_enkel_sanka"
    assert data["felindikator"]["andel_2d_yta"] == 0.0
