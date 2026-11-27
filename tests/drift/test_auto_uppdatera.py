# SPDX-License-Identifier: GPL-3.0-or-later
"""Statiska och beteendetester för kontrollerad auto-deploy (`deploy/floden-auto-uppdatera.*`)."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SKRIPT = REPO / "deploy" / "floden-auto-uppdatera.sh"
SERVICE = REPO / "deploy" / "floden-auto-uppdatera.service"
TIMER = REPO / "deploy" / "floden-auto-uppdatera.timer"
DRIFT_DOC = REPO / "docs" / "DRIFT.md"

_MOCK_DOCKER_KOD = """#!{python}
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

state_fil = Path(os.environ["MOCK_STATE_FIL"])
logg_fil = Path(os.environ["MOCK_LOGG_FIL"])
args = sys.argv[1:]

with logg_fil.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"cmd": "docker", "args": args}}) + "\\n")

state = json.loads(state_fil.read_text(encoding="utf-8"))


def spara() -> None:
    state_fil.write_text(json.dumps(state), encoding="utf-8")


if len(args) >= 4 and args[0] == "compose" and args[1] == "-f":
    rest = args[3:]
    if rest[:3] == ["ps", "-q", "floden"]:
        cid = state.get("container_id", "")
        if cid:
            print(cid)
        sys.exit(0)

    if rest[:3] == ["pull", "-q", "floden"]:
        if state.get("pull_rc", 0) != 0:
            print("simulerat pull-fel", file=sys.stderr)
            sys.exit(int(state["pull_rc"]))
        for tag, info in state.get("remote_tags", {{}}).items():
            state["local_tags"][tag] = info["id"]
            if info.get("digest"):
                state["digests_by_id"][info["id"]] = info["digest"]
        spara()
        sys.exit(0)

    if rest[:4] == ["up", "-d", "--no-deps", "floden"]:
        if state.get("up_rc", 0) != 0:
            print("simulerat up-fel", file=sys.stderr)
            sys.exit(int(state["up_rc"]))
        mal_tag = state["target_tag"]
        ny_id = state["local_tags"][mal_tag]
        state["running_image_id"] = ny_id
        spara()
        sys.exit(0)

    if rest[:3] == ["exec", "-T", "floden"]:
        inuti = rest[3:]
        if inuti[:3] == ["floden", "konto", "backup"]:
            rc = int(state.get("backup_rc", 0))
            if rc != 0:
                print("simulerat backup-fel", file=sys.stderr)
            sys.exit(rc)
        if inuti[:2] == ["python", "-c"]:
            running = state["running_image_id"]
            sekvens = state.get("health_by_image", {{}}).get(running, [1])
            if isinstance(sekvens, list) and len(sekvens) > 1:
                rc = int(sekvens.pop(0))
                spara()
            elif isinstance(sekvens, list) and len(sekvens) == 1:
                rc = int(sekvens[0])
            else:
                rc = 1
            sys.exit(rc)

    print(f"Oväntat docker compose-anrop: {{args}}", file=sys.stderr)
    sys.exit(99)

if len(args) >= 4 and args[0] == "inspect" and args[1] == "--format":
    fmt = args[2]
    target = args[3]
    if fmt == "{{{{.Image}}}}" and target == state.get("container_id"):
        print(state["running_image_id"])
        sys.exit(0)
    print(f"Oväntat docker inspect-anrop: {{args}}", file=sys.stderr)
    sys.exit(99)

if len(args) >= 5 and args[0] == "image" and args[1] == "inspect" and args[2] == "--format":
    fmt = args[3]
    ref = args[4]
    img_id = state["local_tags"].get(ref, ref)
    if fmt == "{{{{.Id}}}}":
        if ref in state["local_tags"]:
            print(state["local_tags"][ref])
            sys.exit(0)
        print(f"Okänd bild: {{ref}}", file=sys.stderr)
        sys.exit(1)
    if "RepoDigests" in fmt:
        digest = state.get("digests_by_id", {{}}).get(img_id, "")
        if digest:
            print(digest)
        sys.exit(0)
    print(f"Oväntat docker image inspect-anrop: {{args}}", file=sys.stderr)
    sys.exit(99)

if len(args) == 3 and args[0] == "tag":
    src, dst = args[1], args[2]
    resolved = state["local_tags"].get(src, src)
    state["local_tags"][dst] = resolved
    spara()
    sys.exit(0)

print(f"Oväntat docker-anrop: {{args}}", file=sys.stderr)
sys.exit(99)
"""

_MOCK_FLOCK_KOD = """#!{python}
from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

logg_fil = Path(os.environ["MOCK_LOGG_FIL"])
args = sys.argv[1:]
with logg_fil.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"cmd": "flock", "args": args}}) + "\\n")

tvingad_rc = os.environ.get("MOCK_FLOCK_RC")
if tvingad_rc is not None:
    sys.exit(int(tvingad_rc))

# Emulera util-linux `flock -n <fd>` med riktigt OS-lås på den ärvda filbeskrivaren.
nonblock = "-n" in args
fd_str = args[-1]
fd = int(fd_str)
flaggor = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblock else 0)
try:
    fcntl.flock(fd, flaggor)
except BlockingIOError:
    sys.exit(1)
sys.exit(0)
"""


def _bygg_mockmiljo(
    tmp_path: Path,
    state: dict[str, Any],
    *,
    env_fil_innehall: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[Path, Path, Path, dict[str, str]]:
    """Skapar tillfällig compose-fil, mockad `docker`/`flock` i PATH samt miljödict."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()

    docker_bin = mock_bin / "docker"
    docker_bin.write_text(_MOCK_DOCKER_KOD.format(python=sys.executable), encoding="utf-8")
    docker_bin.chmod(0o755)

    flock_bin = mock_bin / "flock"
    flock_bin.write_text(_MOCK_FLOCK_KOD.format(python=sys.executable), encoding="utf-8")
    flock_bin.chmod(0o755)

    compose_fil = tmp_path / "docker-compose.prod.yml"
    compose_fil.write_text("name: floden\n", encoding="utf-8")

    if env_fil_innehall is not None:
        (tmp_path / ".env").write_text(env_fil_innehall, encoding="utf-8")

    state_fil = tmp_path / "state.json"
    state_fil.write_text(json.dumps(state), encoding="utf-8")
    logg_fil = tmp_path / "logg.jsonl"
    logg_fil.write_text("", encoding="utf-8")

    miljo: dict[str, str] = {
        "PATH": f"{mock_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "MOCK_STATE_FIL": str(state_fil),
        "MOCK_LOGG_FIL": str(logg_fil),
        "FLODEN_COMPOSE_FIL": str(compose_fil),
        "FLODEN_LASFIL": str(tmp_path / "auto-uppdatera.lock"),
        "FLODEN_HALSA_TIMEOUT_S": "1",
        "FLODEN_HALSA_INTERVALL_S": "0",
    }
    if extra_env:
        miljo.update(extra_env)
    return compose_fil, state_fil, logg_fil, miljo


def _las_logg(logg_fil: Path) -> list[dict[str, Any]]:
    rader = [r for r in logg_fil.read_text(encoding="utf-8").splitlines() if r.strip()]
    return [json.loads(r) for r in rader]


def _kor_skript(
    miljo: dict[str, str],
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [str(SKRIPT), *(extra_args or [])]
    return subprocess.run(cmd, env=miljo, text=True, capture_output=True, check=False)


# ------------------------------------------------------------------ Statiska kontroller


def test_skript_syntax_och_korbarhetsbit() -> None:
    """Skriptet ska finnas, vara körbart och passera `bash -n` utan fel."""
    assert SKRIPT.is_file(), f"Saknas: {SKRIPT}"
    mode = stat.S_IMODE(SKRIPT.stat().st_mode)
    assert mode & stat.S_IXUSR, "deploy/floden-auto-uppdatera.sh saknar körbarhetsbit (chmod +x)"

    res = subprocess.run(["bash", "-n", str(SKRIPT)], text=True, capture_output=True, check=False)
    assert res.returncode == 0, f"bash -n misslyckades:\n{res.stderr}"

    text = SKRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "flock -n" in text
    assert "floden-rollback:senaste" in text
    assert "--torrkor" in text
    # Inga hårdkodade hemligheter eller riktiga nycklar
    for forbjudet in ("ghp_", "fl_mcp_", "AKIA", "sk-ant-", "BEGIN PRIVATE KEY"):
        assert forbjudet not in text


def test_systemd_enheter_struktur_och_hygien() -> None:
    """Kontrollerar systemd-enheterna mot repo-konventioner och utan hemligheter."""
    assert SERVICE.is_file()
    assert TIMER.is_file()

    service_text = SERVICE.read_text(encoding="utf-8")
    timer_text = TIMER.read_text(encoding="utf-8")

    for krav in (
        "After=docker.service",
        "Requires=docker.service",
        "Type=oneshot",
        "WorkingDirectory=/srv/floden",
        "EnvironmentFile=-/srv/floden/.env",
        "UMask=0077",
        "ExecStart=/srv/floden/deploy/floden-auto-uppdatera.sh",
    ):
        assert krav in service_text, f"Saknas i service-enhet: {krav}"

    for krav in (
        "OnBootSec=2min",
        "OnUnitActiveSec=3min",
        "RandomizedDelaySec=15s",
        "Persistent=false",
        "Unit=floden-auto-uppdatera.service",
        "WantedBy=timers.target",
    ):
        assert krav in timer_text, f"Saknas i timer-enhet: {krav}"

    for forbjudet in ("SECRET=", "PASSWORD=", "TOKEN=", "FLODEN_NYCKELHEMLIGHET="):
        assert forbjudet not in service_text
        assert forbjudet not in timer_text


def test_drift_dokumentation_for_auto_deploy_och_mcp_lage() -> None:
    """docs/DRIFT.md ska dokumentera installation, rollback, 1-kommando-stopp och MCP-läge."""
    drift = DRIFT_DOC.read_text(encoding="utf-8")
    for nyckel in (
        "floden-auto-uppdatera.sh",
        "floden-auto-uppdatera.service",
        "floden-auto-uppdatera.timer",
        "--torrkor",
        "floden-rollback:senaste",
        "systemctl disable --now floden-auto-uppdatera.timer",
        "FLODEN_MCP_UTVECKLINGSLAGE=1",
        "FLODEN_MCP_UTVECKLINGSLAGE=0",
    ):
        assert nyckel in drift, f"Saknas i docs/DRIFT.md: {nyckel}"


# ------------------------------------------------------------------ Beteendetester


def test_oforandrad_avbildning_ger_noop_utan_omstart(tmp_path: Path) -> None:
    """(a) Oförändrad avbildning -> avslutar 0 utan backup, taggning eller omstart."""
    state: dict[str, Any] = {
        "container_id": "cid-floden-1",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "sha256:aaa111",
        "local_tags": {"ghcr.io/joelberring/floden:main": "sha256:aaa111"},
        "remote_tags": {
            "ghcr.io/joelberring/floden:main": {
                "id": "sha256:aaa111",
                "digest": "ghcr.io/joelberring/floden@sha256:dig111",
            }
        },
        "digests_by_id": {"sha256:aaa111": "ghcr.io/joelberring/floden@sha256:dig111"},
        "backup_rc": 0,
        "health_by_image": {"sha256:aaa111": [0]},
    }
    _, state_fil, logg_fil, miljo = _bygg_mockmiljo(tmp_path, state)
    res = _kor_skript(miljo)

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert "Ingen ny avbildning" in res.stdout

    anrop = _las_logg(logg_fil)
    assert any(a["cmd"] == "flock" and "-n" in a["args"] for a in anrop)
    docker_anrop = [a["args"] for a in anrop if a["cmd"] == "docker"]
    assert any("pull" in a for a in docker_anrop)
    assert not any("backup" in a for a in docker_anrop)
    assert not any(a and a[0] == "tag" for a in docker_anrop)
    assert not any("up" in a for a in docker_anrop)
    assert json.loads(state_fil.read_text(encoding="utf-8"))["running_image_id"] == "sha256:aaa111"


def test_ny_avbildning_backup_ok_och_halsa_ok_lyckas_med_s3(tmp_path: Path) -> None:
    """(b) Ny avbildning + backup OK (inkl. --s3 från .env) + frisk hälsokontroll -> slutkod 0."""
    state: dict[str, Any] = {
        "container_id": "cid-floden-1",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "sha256:old_111",
        "local_tags": {"ghcr.io/joelberring/floden:main": "sha256:old_111"},
        "remote_tags": {
            "ghcr.io/joelberring/floden:main": {
                "id": "sha256:new_222",
                "digest": "ghcr.io/joelberring/floden@sha256:dignew222",
            }
        },
        "digests_by_id": {"sha256:old_111": "ghcr.io/joelberring/floden@sha256:digold111"},
        "backup_rc": 0,
        # Första hälsoförsöket misslyckas (uppstart), andra lyckas
        "health_by_image": {"sha256:old_111": [0], "sha256:new_222": [1, 0]},
    }
    _, state_fil, logg_fil, miljo = _bygg_mockmiljo(
        tmp_path,
        state,
        env_fil_innehall="FLODEN_TAG=main\nFLODEN_S3_TYP=s3\n",
    )
    res = _kor_skript(miljo)

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert "Uppgradering slutförd" in res.stdout

    slut = json.loads(state_fil.read_text(encoding="utf-8"))
    assert slut["running_image_id"] == "sha256:new_222"
    assert slut["local_tags"]["floden-rollback:senaste"] == "sha256:old_111"

    docker_anrop = [a["args"] for a in _las_logg(logg_fil) if a["cmd"] == "docker"]
    backup_anrop = [a for a in docker_anrop if "backup" in a]
    assert len(backup_anrop) == 1
    assert "--ut" in backup_anrop[0]
    assert "data/lokal/backup/konto" in backup_anrop[0]
    assert "--behall-antal" in backup_anrop[0]
    assert "48" in backup_anrop[0]
    assert "--behall-dagar" in backup_anrop[0]
    assert "14" in backup_anrop[0]
    assert "--s3" in backup_anrop[0]
    assert "--s3-prefix" in backup_anrop[0]
    assert "backup/konto" in backup_anrop[0]

    # Backup måste köras före taggning och omstart
    idx_backup = next(i for i, a in enumerate(docker_anrop) if "backup" in a)
    idx_tag = next(i for i, a in enumerate(docker_anrop) if a[:1] == ["tag"])
    idx_up = next(i for i, a in enumerate(docker_anrop) if "up" in a)
    idx_halsa = next(i for i, a in enumerate(docker_anrop) if "python" in a)
    assert idx_backup < idx_tag < idx_up < idx_halsa


def test_backup_misslyckas_avbryter_utan_uppgradering(tmp_path: Path) -> None:
    """(c) Misslyckad SQLite-backup avbryter driftsättningen utan taggning eller omstart."""
    state: dict[str, Any] = {
        "container_id": "cid-floden-1",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "sha256:old_111",
        "local_tags": {"ghcr.io/joelberring/floden:main": "sha256:old_111"},
        "remote_tags": {
            "ghcr.io/joelberring/floden:main": {
                "id": "sha256:new_222",
                "digest": "ghcr.io/joelberring/floden@sha256:dignew222",
            }
        },
        "digests_by_id": {"sha256:old_111": "ghcr.io/joelberring/floden@sha256:digold111"},
        "backup_rc": 2,
        "health_by_image": {"sha256:old_111": [0], "sha256:new_222": [0]},
    }
    _, state_fil, logg_fil, miljo = _bygg_mockmiljo(tmp_path, state)
    res = _kor_skript(miljo)

    assert res.returncode != 0
    assert "SQLite-backup" in res.stderr

    slut = json.loads(state_fil.read_text(encoding="utf-8"))
    assert slut["running_image_id"] == "sha256:old_111"
    assert "floden-rollback:senaste" not in slut["local_tags"]

    docker_anrop = [a["args"] for a in _las_logg(logg_fil) if a["cmd"] == "docker"]
    assert any("backup" in a for a in docker_anrop)
    assert not any("--s3" in a for a in docker_anrop)
    assert not any(a[:1] == ["tag"] for a in docker_anrop)
    assert not any("up" in a for a in docker_anrop)


def test_trasig_ny_avbildning_rullar_automatiskt_tillbaka(tmp_path: Path) -> None:
    """(d) Ny avbildning som misslyckas med /api/halsa rullas tillbaka till föregående ID."""
    state: dict[str, Any] = {
        "container_id": "cid-floden-1",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "sha256:old_stable_111",
        "local_tags": {"ghcr.io/joelberring/floden:main": "sha256:old_stable_111"},
        "remote_tags": {
            "ghcr.io/joelberring/floden:main": {
                "id": "sha256:broken_999",
                "digest": "ghcr.io/joelberring/floden@sha256:digbroken999",
            }
        },
        "digests_by_id": {"sha256:old_stable_111": "ghcr.io/joelberring/floden@sha256:digstable"},
        "backup_rc": 0,
        "health_by_image": {
            "sha256:old_stable_111": [0],
            "sha256:broken_999": [1],
        },
    }
    _, state_fil, logg_fil, miljo = _bygg_mockmiljo(tmp_path, state)
    res = _kor_skript(miljo)

    assert res.returncode != 0
    assert "ÅTERSTÄLLNING" in res.stderr
    assert "ÅTERSTÄLLNING KLAR" in res.stderr

    slut = json.loads(state_fil.read_text(encoding="utf-8"))
    # Containern och målavbildningens tagg ska vara tillbaka på föregående stabila ID
    assert slut["running_image_id"] == "sha256:old_stable_111"
    assert slut["local_tags"]["ghcr.io/joelberring/floden:main"] == "sha256:old_stable_111"
    assert slut["local_tags"]["floden-rollback:senaste"] == "sha256:old_stable_111"

    docker_anrop = [a["args"] for a in _las_logg(logg_fil) if a["cmd"] == "docker"]
    tag_anrop = [a for a in docker_anrop if a[:1] == ["tag"]]
    assert tag_anrop == [
        ["tag", "sha256:old_stable_111", "floden-rollback:senaste"],
        ["tag", "floden-rollback:senaste", "ghcr.io/joelberring/floden:main"],
    ]
    up_anrop = [a for a in docker_anrop if "up" in a]
    assert len(up_anrop) == 2


def test_overlappande_las_avslutar_rent_utan_docker_anrop(tmp_path: Path) -> None:
    """Exklusivt flock-lås gör att en samtidig körning avslutar 0 utan att röra Docker."""
    state: dict[str, Any] = {
        "container_id": "cid-floden-1",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "sha256:old_111",
        "local_tags": {"ghcr.io/joelberring/floden:main": "sha256:old_111"},
        "remote_tags": {},
        "digests_by_id": {},
    }
    _, _, logg_fil, miljo = _bygg_mockmiljo(tmp_path, state)
    lasfil = Path(miljo["FLODEN_LASFIL"])
    with lasfil.open("w", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            res = _kor_skript(miljo)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert "pågår redan" in res.stdout
    docker_anrop = [a for a in _las_logg(logg_fil) if a["cmd"] == "docker"]
    assert docker_anrop == []


def test_torrkor_upptacker_ny_avbildning_utan_att_mutera(tmp_path: Path) -> None:
    """--torrkor ska upptäcka ny avbildning men aldrig köra backup, taggning eller omstart."""
    state: dict[str, Any] = {
        "container_id": "cid-floden-1",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "sha256:old_111",
        "local_tags": {"ghcr.io/joelberring/floden:main": "sha256:old_111"},
        "remote_tags": {
            "ghcr.io/joelberring/floden:main": {
                "id": "sha256:new_222",
                "digest": "ghcr.io/joelberring/floden@sha256:dignew222",
            }
        },
        "digests_by_id": {"sha256:old_111": "ghcr.io/joelberring/floden@sha256:digold111"},
        "backup_rc": 0,
        "health_by_image": {"sha256:new_222": [0]},
    }
    _, state_fil, logg_fil, miljo = _bygg_mockmiljo(tmp_path, state)
    res = _kor_skript(miljo, extra_args=["--torrkor"])

    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert "[torrkörning]" in res.stdout
    assert json.loads(state_fil.read_text(encoding="utf-8"))["running_image_id"] == "sha256:old_111"

    docker_anrop = [a["args"] for a in _las_logg(logg_fil) if a["cmd"] == "docker"]
    assert not any("backup" in a for a in docker_anrop)
    assert not any(a[:1] == ["tag"] for a in docker_anrop)
    assert not any("up" in a for a in docker_anrop)


def test_ingen_korande_container_avbryter_tydligt(tmp_path: Path) -> None:
    """Om ingen floden-container kör ska auto-deploy avbryta med tydligt fel."""
    state: dict[str, Any] = {
        "container_id": "",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "",
        "local_tags": {},
        "remote_tags": {},
        "digests_by_id": {},
    }
    _, _, logg_fil, miljo = _bygg_mockmiljo(tmp_path, state)
    res = _kor_skript(miljo)

    assert res.returncode != 0
    assert "Ingen körande floden-container" in res.stderr
    docker_anrop = [a["args"] for a in _las_logg(logg_fil) if a["cmd"] == "docker"]
    assert not any("up" in a for a in docker_anrop)


@pytest.mark.parametrize("ogiltig_timeout", ["0", "-5", "trettio"])
def test_ogiltig_halsa_timeout_avvisas(tmp_path: Path, ogiltig_timeout: str) -> None:
    """Ogiltiga numeriska miljövariabler ska ge tydligt fel (slutkod 2), aldrig tyst fel."""
    state: dict[str, Any] = {
        "container_id": "cid-floden-1",
        "target_tag": "ghcr.io/joelberring/floden:main",
        "running_image_id": "sha256:old_111",
        "local_tags": {"ghcr.io/joelberring/floden:main": "sha256:old_111"},
        "remote_tags": {},
        "digests_by_id": {},
    }
    _, _, _, miljo = _bygg_mockmiljo(
        tmp_path,
        state,
        extra_env={"FLODEN_HALSA_TIMEOUT_S": ogiltig_timeout},
    )
    res = _kor_skript(miljo)
    assert res.returncode == 2
    assert "FLODEN_HALSA_TIMEOUT_S" in res.stderr
