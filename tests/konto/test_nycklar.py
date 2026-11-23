"""Användarens egna API-nycklar: kryptering, API, CSRF, isolering och avstängning utan hemlighet."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from cryptography.fernet import Fernet

from konto.nycklar import HEMLIGHET_MILJOVARIABEL, NyckelForrad, NyckelForradFel
from tests.konto.conftest import ANNA, ORIGIN, Miljo, Webblasare

ANTHROPIC = "sk-ant-api03-HEMLIG-NYCKEL-abcd"
LM_ID = "lm-klient-id-exempel-9876"
LM_HEMLIGHET = "lm-hemlighet-exempel-wxyz"
BO = {"sub": "sub-bo-x2", "email": "bo@example.test", "email_verified": True, "name": "Bo Exempel"}


@pytest.fixture(autouse=True)
def _ingen_hemlighet_i_miljon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HEMLIGHET_MILJOVARIABEL, raising=False)


def _slå_på(miljo: Miljo, hemlighet: str | None = None) -> str:
    h = hemlighet or Fernet.generate_key().decode()
    miljo.adapter.nycklar = NyckelForrad(miljo.register, h, miljo.klocka)
    return h


def _inloggad(miljo: Miljo, claims: dict[str, Any] = ANNA) -> tuple[Webblasare, str]:
    epost = claims["email"]
    konto_id = miljo.register.forbered_konto(epost)
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, claims)[0] == 302
    return webb, konto_id


def _skriv(
    webb: Webblasare, metod: str, sokvag: str, data: Any = None, *, csrf: bool = True
) -> tuple[int, dict[str, Any]]:
    h = {"Origin": ORIGIN, **(webb.csrf_headers() if csrf else {})}
    kropp = b"" if data is None else json.dumps(data).encode()
    status, _, rakropp = webb.anrop(metod, sokvag, headers=h, kropp=kropp)
    return status, json.loads(rakropp)


def _las(webb: Webblasare, sokvag: str) -> tuple[int, dict[str, Any], bytes]:
    status, _, kropp = webb.anrop("GET", sokvag)
    return status, json.loads(kropp), kropp


def test_kryptering_fram_och_tillbaka_och_inget_okrypterat_i_databasen(miljo: Miljo) -> None:
    hemlighet = _slå_på(miljo)
    webb, konto_id = _inloggad(miljo)
    status, svar = _skriv(webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC})
    assert status == 200 and svar["status"] == "sparad"
    lm = {"klient_id": LM_ID, "klient_hemlighet": LM_HEMLIGHET, "metod": "basic"}
    assert _skriv(webb, "PUT", "/konto/nycklar/lantmateriet", lm)[0] == 200

    forrad = miljo.adapter.nycklar
    assert forrad is not None
    assert forrad.las(konto_id, "anthropic") == {"nyckel": ANTHROPIC}
    assert forrad.las(konto_id, "lantmateriet") == lm

    with miljo.register.db.lasning() as c:
        rader = c.execute("SELECT * FROM konto_nyckel").fetchall()
    assert len(rader) == 2
    bytes_i_db = json.dumps([list(map(str, r)) for r in rader])
    for hemligt in (ANTHROPIC, LM_ID, LM_HEMLIGHET):
        assert hemligt not in bytes_i_db
    assert hemligt_filen_saknar(miljo, (ANTHROPIC, LM_ID, LM_HEMLIGHET))
    # Fel hemlighet kan inte läsa raden.
    annan = NyckelForrad(miljo.register, Fernet.generate_key().decode())
    with pytest.raises(NyckelForradFel) as fel:
        annan.las(konto_id, "anthropic")
    assert fel.value.kod == "kan-inte-lasas" and ANTHROPIC not in fel.value.text
    # Rätt hemlighet i en lista (rotation): den gamla räcker för att läsa.
    ny = Fernet.generate_key().decode()
    roterad = NyckelForrad(miljo.register, f"{ny},{hemlighet}")
    assert roterad.las(konto_id, "anthropic") == {"nyckel": ANTHROPIC}


def hemligt_filen_saknar(miljo: Miljo, hemligheter: tuple[str, ...]) -> bool:
    """Ingen av hemligheterna finns i klartext i databasfilen (inte heller i WAL-filen)."""
    sokvag = miljo.register.db.sokvag
    for fil in sokvag.parent.glob(sokvag.name + "*"):
        data = fil.read_bytes()
        if any(h.encode() in data for h in hemligheter):
            return False
    return True


def test_nyckeln_visas_aldrig_igen_bara_datum_och_fyra_sista_tecken(miljo: Miljo) -> None:
    _slå_på(miljo)
    webb, _ = _inloggad(miljo)
    lm = {"klient_id": LM_ID, "klient_hemlighet": LM_HEMLIGHET}
    svar_put: list[bytes] = []
    for typ, data in (("anthropic", {"nyckel": ANTHROPIC}), ("lantmateriet", lm)):
        h = {"Origin": ORIGIN, **webb.csrf_headers()}
        status, _, kropp = webb.anrop(
            "PUT", f"/konto/nycklar/{typ}", headers=h, kropp=json.dumps(data).encode()
        )
        assert status == 200
        svar_put.append(kropp)
    status, enskild, rakropp = _las(webb, "/konto/nycklar/anthropic")
    assert status == 200
    assert enskild["slutar_pa"] == "abcd" == ANTHROPIC[-4:]
    assert enskild["sparad_datum"] == "2027-01-15"  # klockan i testerna: 1 800 000 000 s
    assert enskild["paslagen"] is True
    _, oversikt, rak_oversikt = _las(webb, "/konto/nycklar")
    lmrad = next(t for t in oversikt["typer"] if t["typ"] == "lantmateriet")
    assert lmrad["slutar_pa"] == LM_HEMLIGHET[-4:]
    for kropp in (*svar_put, rakropp, rak_oversikt):
        for hemligt in (ANTHROPIC, LM_ID, LM_HEMLIGHET):
            assert hemligt.encode() not in kropp
    # Ersätta och ta bort.
    ny = "sk-ant-ANNAN-nyckel-9999"
    assert _skriv(webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ny})[1]["slutar_pa"] == "9999"
    status, svar = _skriv(webb, "DELETE", "/konto/nycklar/anthropic")
    assert status == 200 and svar["status"] == "borttagen"
    assert _las(webb, "/konto/nycklar/anthropic")[1]["sparad"] is None
    assert _skriv(webb, "DELETE", "/konto/nycklar/anthropic")[1]["status"] == "fanns-inte"


def test_nyckeln_hamnar_aldrig_i_loggar_handelser_eller_fel(
    miljo: Miljo, caplog: pytest.LogCaptureFixture
) -> None:
    _slå_på(miljo)
    caplog.set_level(logging.DEBUG)
    webb, _ = _inloggad(miljo)
    lm = {"klient_id": LM_ID, "klient_hemlighet": LM_HEMLIGHET}
    svar: list[str] = []
    svar.append(json.dumps(_skriv(webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC})))
    svar.append(json.dumps(_skriv(webb, "PUT", "/konto/nycklar/lantmateriet", lm)))
    # Felaktiga anrop vars värden innehåller hemligheten: felen får inte ekot värdet.
    dalig = {"nyckel": ANTHROPIC + " med blanksteg"}
    status, fel = _skriv(webb, "PUT", "/konto/nycklar/anthropic", dalig)
    assert status == 400
    svar.append(json.dumps(fel))
    status, fel = _skriv(
        webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC, ANTHROPIC: 1}
    )
    assert status == 400
    svar.append(json.dumps(fel))
    status, fel = _skriv(webb, "PUT", "/konto/nycklar/lantmateriet", {**lm, "metod": LM_HEMLIGHET})
    assert status == 400
    svar.append(json.dumps(fel))
    status, _, rak = webb.anrop(
        "PUT",
        "/konto/nycklar/anthropic",
        headers={"Origin": ORIGIN, **webb.csrf_headers()},
        kropp=b'{"nyckel": "' + ANTHROPIC.encode(),
    )
    assert status == 400
    svar.append(rak.decode())
    _las(webb, "/konto/nycklar/anthropic")
    _skriv(webb, "DELETE", "/konto/nycklar/anthropic")

    text = caplog.text + "\n".join(svar)
    with miljo.register.db.lasning() as c:
        text += json.dumps([tuple(r) for r in c.execute("SELECT * FROM handelse").fetchall()])
        assert (
            c.execute("SELECT COUNT(*) FROM handelse WHERE handelse LIKE 'nyckel-%'").fetchone()[0]
            == 3
        )
    for hemligt in (ANTHROPIC, LM_ID, LM_HEMLIGHET):
        assert hemligt not in text
    assert "nyckel sparad" in caplog.text  # loggen finns, bara utan värdet


def test_csrf_kravs_for_att_spara_och_ta_bort(miljo: Miljo) -> None:
    _slå_på(miljo)
    webb, konto_id = _inloggad(miljo)
    forrad = miljo.adapter.nycklar
    assert forrad is not None
    # Utan CSRF-huvud, med fel värde och med fel Origin: 403 och inget sparas.
    assert (
        _skriv(webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC}, csrf=False)[0] == 403
    )
    status, _, _ = webb.anrop(
        "PUT",
        "/konto/nycklar/anthropic",
        headers={"Origin": ORIGIN, "X-Floden-CSRF": "fel"},
        kropp=json.dumps({"nyckel": ANTHROPIC}).encode(),
    )
    assert status == 403
    status, _, _ = webb.anrop(
        "PUT",
        "/konto/nycklar/anthropic",
        headers={"Origin": "https://annan.test", **webb.csrf_headers()},
        kropp=json.dumps({"nyckel": ANTHROPIC}).encode(),
    )
    assert status == 403
    assert forrad.las(konto_id, "anthropic") is None
    # Med rätt CSRF sparas den; DELETE utan CSRF rör den inte.
    assert _skriv(webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC})[0] == 200
    assert _skriv(webb, "DELETE", "/konto/nycklar/anthropic", csrf=False)[0] == 403
    assert forrad.las(konto_id, "anthropic") == {"nyckel": ANTHROPIC}


def test_inloggning_kravs_och_okand_typ_ger_404(miljo: Miljo) -> None:
    _slå_på(miljo)
    ut = miljo.webblasare()
    for metod in ("GET", "PUT", "DELETE"):
        assert ut.anrop(metod, "/konto/nycklar/anthropic")[0] == 401
    assert ut.anrop("GET", "/konto/nycklar")[0] == 401
    webb, _ = _inloggad(miljo)
    assert _las(webb, "/konto/nycklar/openai")[0] == 404
    assert _skriv(webb, "PUT", "/konto/nycklar/openai", {"nyckel": ANTHROPIC})[0] == 404
    assert (
        webb.anrop(
            "POST", "/konto/nycklar/anthropic", headers={"Origin": ORIGIN, **webb.csrf_headers()}
        )[0]
        == 405
    )


def test_en_anvandare_nar_inte_en_annans_nycklar(miljo: Miljo) -> None:
    _slå_på(miljo)
    anna, anna_id = _inloggad(miljo, ANNA)
    bo, bo_id = _inloggad(miljo, BO)
    assert _skriv(anna, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC})[0] == 200
    # Bo ser ingenting, och Bos borttagning rör inte Annas nyckel.
    assert _las(bo, "/konto/nycklar/anthropic")[1]["sparad"] is None
    assert all(t["sparad"] is None for t in _las(bo, "/konto/nycklar")[1]["typer"])
    assert _skriv(bo, "DELETE", "/konto/nycklar/anthropic")[1]["status"] == "fanns-inte"
    # Konto-id i adressen ignoreras: det finns ingen väg att nå någon annans nyckel.
    assert _las(bo, f"/konto/nycklar/{anna_id}")[0] == 404
    assert _las(bo, f"/konto/nycklar/anthropic/{anna_id}")[0] == 404
    forrad = miljo.adapter.nycklar
    assert forrad is not None
    assert forrad.las(anna_id, "anthropic") == {"nyckel": ANTHROPIC}
    assert forrad.las(bo_id, "anthropic") is None
    # En chiffertext som flyttas till ett annat konto går inte att läsa (konto-id ligger i den).
    with miljo.register.db.transaktion() as c:
        c.execute("UPDATE konto_nyckel SET konto_id = ? WHERE konto_id = ?", (bo_id, anna_id))
    with pytest.raises(NyckelForradFel) as fel:
        forrad.las(bo_id, "anthropic")
    assert fel.value.kod == "kan-inte-lasas"


def test_sidan_stangs_av_nar_hemligheten_saknas_och_inget_sparas(miljo: Miljo) -> None:
    # Ingen FLODEN_NYCKELHEMLIGHET (rensad av autouse-fixturen): adaptern byggdes utan den.
    assert miljo.adapter.nycklar is not None and not miljo.adapter.nycklar.paslagen
    webb, konto_id = _inloggad(miljo)
    status, oversikt, _ = _las(webb, "/konto/nycklar")
    assert status == 200
    assert oversikt["paslagen"] is False and oversikt["orsak"] == "saknas"
    assert HEMLIGHET_MILJOVARIABEL in oversikt["text"] and "inte påslagen" in oversikt["text"]
    assert _las(webb, "/konto/nycklar/anthropic")[1]["paslagen"] is False
    status, svar = _skriv(webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC})
    assert status == 503 and svar["kod"] == "nycklar-av"
    assert ANTHROPIC not in json.dumps(svar)
    with miljo.register.db.lasning() as c:
        assert c.execute("SELECT COUNT(*) FROM konto_nyckel").fetchone()[0] == 0
    with pytest.raises(NyckelForradFel):
        miljo.adapter.nycklar.las(konto_id, "anthropic")


def test_ogiltig_hemlighet_ar_avstangd_och_loggar_inte_vardet(
    miljo: Miljo, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    forrad = NyckelForrad(miljo.register, "detta-ar-ingen-fernet-nyckel")
    assert not forrad.paslagen and forrad.orsak == "ogiltig"
    assert "ingen-fernet-nyckel" not in caplog.text and "ogiltig" in caplog.text
    miljo.adapter.nycklar = forrad
    webb, _ = _inloggad(miljo)
    oversikt = _las(webb, "/konto/nycklar")[1]
    assert oversikt["paslagen"] is False and oversikt["orsak"] == "ogiltig"
    assert _skriv(webb, "PUT", "/konto/nycklar/anthropic", {"nyckel": ANTHROPIC})[0] == 503


def test_hemligheten_laeses_ur_miljon_och_inloggning_av_har_ingen_nyckelsida(
    miljo: Miljo, tmp_path: Any
) -> None:
    from konto.adapter import KontoAdapter
    from konto.konfig import KontoKonfig

    h = Fernet.generate_key().decode()
    forrad = NyckelForrad(miljo.register, miljo={HEMLIGHET_MILJOVARIABEL: h})
    assert forrad.paslagen
    # Inloggning av: ingen databas, ingen nyckelsida.
    av = KontoAdapter(KontoKonfig())
    assert av.nycklar is None
    assert av.hantera_konto("GET", "/konto/nycklar", {}, {})[0] == 404


def test_ogiltiga_varden_nekas(miljo: Miljo) -> None:
    _slå_på(miljo)
    webb, _ = _inloggad(miljo)
    for data in ({}, {"nyckel": ""}, {"nyckel": "kort"}, {"nyckel": 5}, {"nyckel": "x" * 600}):
        assert _skriv(webb, "PUT", "/konto/nycklar/anthropic", data)[0] == 400, data
    for data in (
        {"klient_id": LM_ID},
        {"klient_id": LM_ID, "klient_hemlighet": LM_HEMLIGHET, "metod": "ftp"},
    ):
        assert _skriv(webb, "PUT", "/konto/nycklar/lantmateriet", data)[0] == 400, data
    status, _, _ = webb.anrop(
        "PUT",
        "/konto/nycklar/anthropic",
        headers={"Origin": ORIGIN, **webb.csrf_headers()},
        kropp=b"x" * 5000,
    )
    assert status == 413
    with miljo.register.db.lasning() as c:
        assert c.execute("SELECT COUNT(*) FROM konto_nyckel").fetchone()[0] == 0
    # Metod utelämnad: oauth2.
    lm = {"klient_id": LM_ID, "klient_hemlighet": LM_HEMLIGHET}
    assert _skriv(webb, "PUT", "/konto/nycklar/lantmateriet", lm)[0] == 200
    forrad = miljo.adapter.nycklar
    assert forrad is not None
    konto_id = miljo.register.konto_via_epost("anna@example.test")
    assert konto_id is not None
    assert forrad.las(konto_id.id, "lantmateriet") == {**lm, "metod": "oauth2"}
