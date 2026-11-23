# ruff: noqa: E501
"""`NyckelForrad.las` (klartext för användarens API-nycklar) får aldrig gå att nå via MCP."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from konto.nycklar import NyckelForrad
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp_http import (
    Stack,
    _data,
    _initiera,
    _rpc,
    _skapa_token,
    anropa_verktyg,
)
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_nycklar import _inloggad

MCP_KALLKOD = Path(__file__).resolve().parents[2] / "src" / "floden_mcp"
HEMLIGHETER = {"anthropic": {"nyckel": "sk-ant-api03-HEMLIG-NYCKEL-zz91"}, "lantmateriet": {"klient_id": "lm-id-exempel-1234", "klient_hemlighet": "lm-hemlighet-exempel-5678"}}


def _importerade_namn(fil: Path) -> set[str]:
    tr = ast.parse(fil.read_text(encoding="utf-8"))
    ut: set[str] = set()
    for nod in ast.walk(tr):
        if isinstance(nod, ast.Import):
            ut.update(a.name for a in nod.names)
        elif isinstance(nod, ast.ImportFrom):
            modul = nod.module or ""
            ut.add(modul)
            ut.update(f"{modul}.{a.name}" for a in nod.names)
    return ut


def test_mcp_koden_importerar_aldrig_nyckelforradet_och_rör_inte_nycklar() -> None:
    for fil in sorted(MCP_KALLKOD.glob("*.py")):
        importerat = _importerade_namn(fil)
        for namn in importerat:
            assert not namn.startswith("konto.nycklar"), (fil.name, namn)
            assert not namn.startswith("konto.kontoapi"), (fil.name, namn)
            assert "NyckelForrad" not in namn, (fil.name, namn)
        text = fil.read_text(encoding="utf-8")
        for tabu in (".nycklar", "NyckelForrad", "konto_nyckel", ".las(\"anthropic\"", "las_nycklar_for_konto", "FLODEN_NYCKELHEMLIGHET"):
            assert tabu not in text, (fil.name, tabu)


def test_nyckelsvep_over_http_klartexten_lases_aldrig_och_syns_aldrig(
    demo: Demo, miljo: Miljo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    miljo.adapter.nycklar = NyckelForrad(miljo.register, Fernet.generate_key().decode(), miljo.klocka)
    webb, konto_id = _inloggad(miljo, ANNA)
    for typ, falt in HEMLIGHETER.items():
        assert miljo.adapter.nycklar.spara(konto_id, typ, falt)
    anrop: list[str] = []

    def las_forbjuden(self: NyckelForrad, *a: Any, **k: Any) -> Any:
        anrop.append("las")
        raise AssertionError("NyckelForrad.las anropades under ett MCP-anrop")

    monkeypatch.setattr(NyckelForrad, "las", las_forbjuden)
    token = _skapa_token(webb)["token"]
    stack = Stack(demo, miljo, tmp_path)
    alla_svar: list[str] = []
    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        sess = _initiera(klient, {"Authorization": f"Bearer {token}"})
        listning = _data(_rpc(klient, "tools/list", {}, sess, id_=2))["result"]["tools"]
        alla_svar.append(json.dumps(listning))
        # Inga verktyg för nycklar, och inget verktyg tar ett fält för dem.
        for t in listning:
            assert "nyckel" not in t["name"] and "konto" not in t["name"], t["name"]
            assert "nyckel" not in json.dumps(t["inputSchema"]).lower().replace("cache", ""), t["name"]
        # Alla verktyg som bara läser anropas utan argument (de som kräver argument ger ett fel, också ett svar).
        for t in listning:
            if t["name"] in {"kor", "rapport_exportera", "vanta"}:
                continue
            _, text, struktur = anropa_verktyg(klient, sess, t["name"], {}, id_=10)
            alla_svar.append(text + json.dumps(struktur))
        resurser = _data(_rpc(klient, "resources/list", {}, sess, id_=3))["result"]["resources"]
        for r in resurser:
            alla_svar.append(_rpc(klient, "resources/read", {"uri": r["uri"]}, sess, id_=4).text)
        # Konto- och nyckelsökvägarna finns inte på /mcp-appen.
        assert klient.get("/konto/nycklar", headers={"Authorization": f"Bearer {token}"}).status_code in (401, 404)
    assert anrop == []
    text = "\n".join(alla_svar) + stack.logg.sokvag.read_text(encoding="utf-8")
    for falt in HEMLIGHETER.values():
        for hemligt in falt.values():
            assert hemligt not in text, hemligt


def test_nyckelsidan_nas_inte_med_en_mcp_token(miljo: Miljo) -> None:
    miljo.adapter.nycklar = NyckelForrad(miljo.register, Fernet.generate_key().decode(), miljo.klocka)
    webb, konto_id = _inloggad(miljo, ANNA)
    miljo.adapter.nycklar.spara(konto_id, "anthropic", HEMLIGHETER["anthropic"])
    token = _skapa_token(webb)["token"]
    utan = miljo.webblasare()
    for metod, sokvag in (("GET", "/konto/nycklar"), ("GET", "/konto/nycklar/anthropic"), ("PUT", "/konto/nycklar/anthropic"), ("DELETE", "/konto/nycklar/anthropic"), ("GET", "/konto/admin/projekt")):
        status, _, kropp = utan.anrop(metod, sokvag, headers={"Authorization": f"Bearer {token}"}, kropp=b"{}")
        assert status == 401, sokvag
        assert HEMLIGHETER["anthropic"]["nyckel"].encode() not in kropp
