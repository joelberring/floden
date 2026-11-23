# ruff: noqa: E501
"""Kartornas sidfot visar commiten avbildningen byggdes från (`FLODEN_COMMIT`), inte "okand"."""

from __future__ import annotations

from typing import Any

import pytest

from floden_mcp import karna_api
from gemensamt.manifest import kort_commit_ur_miljo, visningsversion
from tests.floden_mcp.test_mcp import Mcp, mcp  # noqa: F401

SHA = "0123456789abcdef0123456789abcdef01234567"


def test_commit_ur_miljon_blir_kort_sha() -> None:
    assert kort_commit_ur_miljo({"FLODEN_COMMIT": SHA}) == SHA[:12]
    assert kort_commit_ur_miljo({"FLODEN_COMMIT": "  ABCDEF1  "}) == "abcdef1"


@pytest.mark.parametrize("varde", ["", "   ", "okand", "main", "ghijklm", "abc12", "$(rm -rf /)", SHA + "0"])
def test_varde_som_inte_ar_en_sha_anvands_aldrig(varde: str) -> None:
    assert kort_commit_ur_miljo({"FLODEN_COMMIT": varde}) is None
    assert kort_commit_ur_miljo({}) is None


def test_visningsversion_foredrar_miljon_och_faller_tillbaka_pa_git(tmp_path: Any) -> None:
    assert visningsversion(tmp_path, {"FLODEN_COMMIT": SHA}) == SHA[:12]
    # Utan variabel och utan git (som i avbildningen) är det den gamla texten, vilket är det som buggen var.
    assert visningsversion(tmp_path, {}) == "okand"


def test_kartans_sidfot_far_commiten(mcp: Mcp, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    sedda: list[str] = []
    ekta = karna_api.rendera_karta

    def spion(**kw: Any) -> Any:
        sedda.append(kw["kodversion"])
        return ekta(**kw)

    monkeypatch.setenv("FLODEN_COMMIT", SHA)
    monkeypatch.setattr(karna_api, "rendera_karta", spion)
    mcp.tjanst._kodversion = None
    try:
        r = mcp.anropa("karta_bild", {"korning_id": "demo-vatten", "analys": "skyfall"})
        assert not r.is_error
        assert sedda == [SHA[:12]]
    finally:
        mcp.tjanst._kodversion = None
