"""`floden webb --server asgi`: startar src/webb/asgi.py i uvicorn med rätt inställningar."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from floden.cli import main
from webb.asgi import FlodenAsgiApp


def test_webb_asgi_startar_uvicorn_med_floden_appen(monkeypatch: pytest.MonkeyPatch) -> None:
    anrop: dict[str, Any] = {}

    def falsk_run(app: Any, **kw: Any) -> None:
        anrop["app"] = app
        anrop.update(kw)

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=falsk_run))
    kod = main(
        [
            "webb",
            "--ingen-bygg",
            "--server",
            "asgi",
            "--host",
            "0.0.0.0",
            "--port",
            "8799",
            "--max-samtidiga",
            "2",
        ]
    )
    assert kod == 0
    app = anrop["app"]
    assert isinstance(app, FlodenAsgiApp)
    assert app.max_samtidiga_berakningar == 2
    assert anrop["host"] == "0.0.0.0" and anrop["port"] == 8799
    assert anrop["workers"] == 1  # konto, cache och körningslogg ligger i processen


def test_webb_asgi_utan_uvicorn_ger_tydligt_fel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert main(["webb", "--ingen-bygg", "--server", "asgi"]) == 1
    assert "uvicorn saknas" in capsys.readouterr().err


def test_halsa_visar_commit_fran_miljon(monkeypatch: pytest.MonkeyPatch) -> None:
    from webb.api import FlodenApiApp

    monkeypatch.setenv("FLODEN_COMMIT", "abc1234")
    assert FlodenApiApp().halsa()["commit"] == "abc1234"
    monkeypatch.delenv("FLODEN_COMMIT")
    assert FlodenApiApp().halsa()["commit"] is None
