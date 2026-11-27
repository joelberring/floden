# ruff: noqa: E501
"""Enhetstester för `FLODEN_COMMIT`-fallback i `gemensamt.manifest.hamta_kodversion` (P1-1)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import gemensamt.manifest as manifest_mod
from gemensamt.manifest import hamta_kodversion, skapa_manifest, visningsversion

_SHA = "ad0bca4925951234567890abcdef1234567890ab"
_SHA_ANNAN = "fedcba9876543210fedcba9876543210fedcba98"


def test_fallback_till_floden_commit_nar_git_saknas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I Docker-containern (utan git-binär) ska `hamta_kodversion` och `skapa_manifest` använda `FLODEN_COMMIT`."""

    def _ingen_git(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("git saknas")

    monkeypatch.setattr(manifest_mod.subprocess, "run", _ingen_git)
    monkeypatch.setenv("FLODEN_COMMIT", _SHA)

    assert hamta_kodversion(repo_rot=tmp_path) == _SHA[:12]
    m = skapa_manifest("dagsljus.svf_mark", repo_rot=tmp_path)
    assert m.kodversion == _SHA[:12]


def test_fallback_till_floden_commit_nar_katalog_saknar_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """När `git rev-parse` misslyckas (t.ex. `/app` utan `.git`) används `FLODEN_COMMIT`."""
    monkeypatch.setenv("FLODEN_COMMIT", f"  {_SHA.upper()}  ")
    assert hamta_kodversion(repo_rot=tmp_path) == _SHA[:12]
    assert skapa_manifest("vatten.statisk_fyllning", repo_rot=tmp_path).kodversion == _SHA[:12]
    # Explicit `miljo`-parameter styr fallbacken utan att läsa `os.environ`.
    assert hamta_kodversion(repo_rot=tmp_path, miljo={"FLODEN_COMMIT": "ABCDEF1"}) == "abcdef1"


@pytest.mark.parametrize(
    "varde",
    [None, "", "   ", "okand", "main", "abc12", "ghijklm", "$(rm -rf /)", _SHA + "0"],
)
def test_okand_nar_varken_git_eller_giltig_floden_commit_finns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, varde: str | None
) -> None:
    """Utan git och utan giltig SHA i `FLODEN_COMMIT` returneras fortsatt `"okand"`."""
    if varde is None:
        monkeypatch.delenv("FLODEN_COMMIT", raising=False)
    else:
        monkeypatch.setenv("FLODEN_COMMIT", varde)

    assert hamta_kodversion(repo_rot=tmp_path) == "okand"
    assert skapa_manifest("testmodul", repo_rot=tmp_path).kodversion == "okand"


def test_git_foredras_framfor_floden_commit_nar_git_finns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I lokal utvecklingsmiljö med git ska verklig git-revision (inkl. `-smutsig`) gälla."""
    monkeypatch.setenv("FLODEN_COMMIT", _SHA_ANNAN)

    def _ren_git(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{_SHA[:12]}\n", stderr="")
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Oväntat kommando: {cmd}")

    monkeypatch.setattr(manifest_mod.subprocess, "run", _ren_git)
    assert hamta_kodversion(repo_rot=tmp_path) == _SHA[:12]
    assert skapa_manifest("testmodul", repo_rot=tmp_path).kodversion == _SHA[:12]

    def _smutsig_git(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{_SHA[:12]}\n", stderr="")
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=" M src/gemensamt/manifest.py\n", stderr="")
        raise AssertionError(f"Oväntat kommando: {cmd}")

    monkeypatch.setattr(manifest_mod.subprocess, "run", _smutsig_git)
    assert hamta_kodversion(repo_rot=tmp_path) == f"{_SHA[:12]}-smutsig"


def test_visningsversion_respekterar_explicit_tom_miljo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`visningsversion(tmp_path, {})` läcker inte `os.environ['FLODEN_COMMIT']`."""
    monkeypatch.setenv("FLODEN_COMMIT", _SHA)
    assert visningsversion(tmp_path, {}) == "okand"
    assert visningsversion(tmp_path, {"FLODEN_COMMIT": _SHA}) == _SHA[:12]


def test_skapa_manifest_explicit_kodversion_overstyr_allt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLODEN_COMMIT", _SHA)
    m = skapa_manifest("testmodul", kodversion="deadbeef1234", repo_rot=tmp_path)
    assert m.kodversion == "deadbeef1234"
