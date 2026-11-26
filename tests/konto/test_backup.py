"""Tester för S4 kontobackup, verifiering, driftskontroll, CLI och systemd-enheter."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from floden.cli import main as floden_main
from gemensamt.hash import sha256_fil
from gemensamt.objektlagring import LokalLagring, ObjektLagring, ObjektLagringFel
from konto.backup import (
    BackupFel,
    drifts_halsa,
    skapa_backup,
    verifiera_backup,
)
from konto.databas import Databas
from konto.nycklar import NyckelForrad
from konto.register import Register

_REPO = Path(__file__).resolve().parents[2]

_SYNTETISK_EPOST = "anna.testperson@exempel.test"
_SYNTETISKT_NAMN = "Anna Testperson"
_SYNTETISK_S3_ACCESS = "AKIA_TEST_HEMLIG_ACCESS_99"
_SYNTETISK_S3_SECRET = "SUPER_HEMLIG_S3_NYCKEL_XYZ_88"


def _skapa_konto_db(
    db_fil: Path,
    *,
    med_nyckel_hemlighet: str | None = None,
) -> tuple[Databas, Register, str]:
    """Skapar en kontodatabas med syntetisk användare, organisation och valfri krypterad nyckel."""
    db = Databas(db_fil)
    reg = Register(db)
    konto_id = reg.forbered_konto(_SYNTETISK_EPOST)
    with db.transaktion() as c:
        c.execute(
            "UPDATE konto SET namn = ?, epost_verifierad = 1 WHERE id = ?",
            (_SYNTETISKT_NAMN, konto_id),
        )
    reg.skapa_organisation("Testkommun", agare=konto_id)
    if med_nyckel_hemlighet is not None:
        nf = NyckelForrad(reg, hemlighet=med_nyckel_hemlighet)
        nf.spara(konto_id, "anthropic", {"nyckel": "sk-ant-syntetisk-testnyckel-1234567890"})
    return db, reg, konto_id


class _TrasigLagring(ObjektLagring):
    """Mocklagring som alltid fallerar vid uppladdning för att testa att inga fel sväljs."""

    def finns(self, nyckel: str) -> bool:
        return False

    def ladda_upp_fil(
        self, lokal_fil: str | Path, nyckel: str, *, sha256: str | None = None
    ) -> str:
        raise ObjektLagringFel("simulerat S3-nätverksfel")

    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        raise ObjektLagringFel("ej implementerad")

    def radera_prefix(self, prefix: str) -> int:
        return 0

    def lista_prefix(self, prefix: str = "") -> list[str]:
        return []

    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        raise ObjektLagringFel("ej implementerad")


def test_online_backup_under_wal_skrivning_och_noll_pii(tmp_path: Path) -> None:
    """Online-backup under pågående WAL-skrivning → verifiera_backup OK, 0o600, SHA-256, 0 PII."""
    fernet_nyckel = Fernet.generate_key().decode("ascii")
    db_fil = tmp_path / "konto" / "konto.sqlite"
    db, reg, _ = _skapa_konto_db(db_fil, med_nyckel_hemlighet=fernet_nyckel)
    malmapp = tmp_path / "backuper"

    stopp = threading.Event()
    skrivare_aktiv = threading.Event()
    skrivfel: list[Exception] = []

    def _wal_skrivare() -> None:
        try:
            for i in range(40):
                if stopp.is_set():
                    break
                with db.transaktion() as c:
                    reg.notera(c, "test-wal", None, f"steg={i}")
                skrivare_aktiv.set()
                time.sleep(0.002)
        except Exception as exc:  # noqa: BLE001
            skrivfel.append(exc)

    trad = threading.Thread(target=_wal_skrivare, daemon=True)
    trad.start()
    assert skrivare_aktiv.wait(timeout=5.0)
    try:
        res = skapa_backup(db_fil, malmapp, nu=datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC))
    finally:
        stopp.set()
        trad.join(timeout=5.0)

    assert not skrivfel, f"WAL-skrivtråd fick fel: {skrivfel}"
    assert res.backup_fil.name == "konto-20261126T120000Z.sqlite"
    assert res.manifest_fil.name == "konto-20261126T120000Z.manifest.json"
    assert res.backup_fil.is_file()
    assert res.manifest_fil.is_file()
    assert res.integrity_check == "ok"
    assert res.foreign_key_check_ok is True
    assert res.sha256 == sha256_fil(res.backup_fil)
    assert res.tabellrader["konto"] == 1
    assert res.tabellrader["organisation"] == 1
    assert res.tabellrader["konto_nyckel"] == 1

    # Fil- och katalogrättigheter (0o700 på ny målmapp, 0o600 på filer)
    assert stat.S_IMODE(malmapp.stat().st_mode) == 0o700
    assert stat.S_IMODE(res.backup_fil.stat().st_mode) == 0o600
    assert stat.S_IMODE(res.manifest_fil.stat().st_mode) == 0o600

    # Inga kvarlämnade temp- eller WAL-sidofiler i backupmappen
    filer_i_mapp = sorted(p.name for p in malmapp.iterdir())
    assert filer_i_mapp == [res.manifest_fil.name, res.backup_fil.name]

    # Verifiera backupen via verifiera_backup
    ver = verifiera_backup(res.backup_fil)
    assert ver.ok is True
    assert ver.manifest_verifierat is True
    assert ver.sha256 == res.sha256
    assert ver.integrity_check == "ok"
    assert ver.foreign_key_check_ok is True
    assert ver.tabellrader["konto"] == 1

    # 0 PII och 0 hemligheter i manifestet och i drifts_halsa
    manifest_text = res.manifest_fil.read_text(encoding="utf-8")
    manifest_json = json.loads(manifest_text)
    assert manifest_json["filnamn"] == res.backup_fil.name
    assert manifest_json["sha256"] == res.sha256
    assert manifest_json["integrity_check"] == "ok"
    assert manifest_json["tabellrader"]["konto"] == 1

    miljo = {
        "FLODEN_NYCKELHEMLIGHET": fernet_nyckel,
        "FLODEN_S3_TYP": "s3",
        "FLODEN_S3_ENDPOINT": "https://user:topsecret@fsn1.your-objectstorage.com",
        "FLODEN_S3_BUCKET": "floden-test-bucket",
        "FLODEN_S3_ACCESS_KEY": _SYNTETISK_S3_ACCESS,
        "FLODEN_S3_SECRET_KEY": _SYNTETISK_S3_SECRET,
    }
    halsa = drifts_halsa(db_sokvag=db_fil, miljo=miljo)
    halsa_text = json.dumps(halsa, ensure_ascii=False)
    for kanslig in (
        _SYNTETISK_EPOST,
        _SYNTETISKT_NAMN,
        fernet_nyckel,
        "sk-ant-syntetisk-testnyckel-1234567890",
        _SYNTETISK_S3_ACCESS,
        _SYNTETISK_S3_SECRET,
        "topsecret",
    ):
        assert kanslig not in manifest_text
        assert kanslig not in halsa_text
        assert kanslig not in json.dumps(res.till_dict(), ensure_ascii=False)
        assert kanslig not in json.dumps(ver.till_dict(), ensure_ascii=False)

    assert halsa["databas"]["wal_aktivt"] is True
    assert halsa["databas"]["integrity_check"] == "ok"
    assert halsa["nyckelforrad"]["paslagen"] is True
    assert halsa["nyckelforrad"]["orsak"] is None
    assert halsa["lagring"]["typ"] == "s3"
    assert halsa["lagring"]["endpoint"] == "https://fsn1.your-objectstorage.com"
    assert halsa["lagring"]["har_nycklar"] is True


def test_skapa_backup_avvisar_saknad_tom_eller_ogiltig_kalla(tmp_path: Path) -> None:
    mal = tmp_path / "ut"

    with pytest.raises(BackupFel, match="finns inte"):
        skapa_backup(tmp_path / "saknas.sqlite", mal)

    tom = tmp_path / "tom.sqlite"
    tom.write_bytes(b"")
    with pytest.raises(BackupFel, match="tom"):
        skapa_backup(tom, mal)

    skrap = tmp_path / "skrap.sqlite"
    skrap.write_text("inte en sqlite-fil alls", encoding="utf-8")
    with pytest.raises(BackupFel, match="giltig SQLite"):
        skapa_backup(skrap, mal)

    db_fil = tmp_path / "ok.sqlite"
    _skapa_konto_db(db_fil)
    with pytest.raises(BackupFel, match="behall_antal"):
        skapa_backup(db_fil, mal, behall_antal=0)
    with pytest.raises(BackupFel, match="behall_dagar"):
        skapa_backup(db_fil, mal, behall_dagar=-1)


def test_verifiera_backup_upptacker_korrupt_och_manipulerad_fil(tmp_path: Path) -> None:
    db_fil = tmp_path / "konto.sqlite"
    _skapa_konto_db(db_fil)
    mal = tmp_path / "ut"
    res = skapa_backup(db_fil, mal, nu=datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC))

    # 1. Manipulera 1 byte i backupen -> SHA-256-avvikelse mot manifestet
    ra = bytearray(res.backup_fil.read_bytes())
    ra[-1] ^= 0xFF
    res.backup_fil.write_bytes(bytes(ra))
    with pytest.raises(BackupFel, match="SHA-256-avvikelse"):
        verifiera_backup(res.backup_fil)

    # 2. Ta bort manifestet och förstör SQLite-sidor -> integrity_check/SQLite-fel (aldrig tyst OK)
    res.manifest_fil.unlink()
    korrupt_data = bytearray(res.backup_fil.read_bytes())
    # Skriv över delar efter headern för att trigga SQLite-korruption
    for i in range(100, min(len(korrupt_data), 4096)):
        korrupt_data[i] = 0xAA
    res.backup_fil.write_bytes(bytes(korrupt_data))
    with pytest.raises(BackupFel):
        verifiera_backup(res.backup_fil)

    # 3. Giltig SQLite-databas som saknar `konto`-tabellen
    fel_schema = tmp_path / "annan.sqlite"
    c = sqlite3.connect(fel_schema)
    c.execute("CREATE TABLE ovrig (id INTEGER PRIMARY KEY)")
    c.commit()
    c.close()
    with pytest.raises(BackupFel, match="saknar obligatorisk tabell 'konto'"):
        verifiera_backup(fel_schema)

    # 4. Foreign-key-brott i kopierad DB (utan manifest)
    fk_fil = tmp_path / "fk_brott.sqlite"
    res2 = skapa_backup(db_fil, tmp_path / "ut2", nu=datetime(2026, 11, 26, 13, 0, 0, tzinfo=UTC))
    fk_fil.write_bytes(res2.backup_fil.read_bytes())
    c_fk = sqlite3.connect(fk_fil)
    c_fk.execute("PRAGMA foreign_keys = OFF")
    c_fk.execute(
        "INSERT INTO session (id, konto_id, hemlighet_hash, csrf_hash, utfardad, utgar) "
        "VALUES ('s1', 'finns-inte', 'h1', 'c1', 1, 2)"
    )
    c_fk.commit()
    c_fk.close()
    with pytest.raises(BackupFel, match="foreign_key_check"):
        verifiera_backup(fk_fil)

    # 5. Korrupt manifest-JSON
    res2.manifest_fil.write_text("{trasig json", encoding="utf-8")
    with pytest.raises(BackupFel, match="backup-manifestet"):
        verifiera_backup(res2.backup_fil)


def test_rotation_behall_antal_och_behall_dagar_parvis(tmp_path: Path) -> None:
    """Rotation med `behall_antal=2` och `behall_dagar=14` raderar äldre parvis."""
    db_fil = tmp_path / "konto.sqlite"
    _skapa_konto_db(db_fil)
    mal = tmp_path / "backuper"
    bas = datetime(2026, 11, 26, 10, 0, 0, tzinfo=UTC)

    resultat = []
    for h in range(4):
        resultat.append(
            skapa_backup(
                db_fil,
                mal,
                behall_antal=2,
                behall_dagar=14,
                nu=bas + timedelta(hours=h),
            )
        )

    kvar_sqlite = sorted(p.name for p in mal.glob("*.sqlite"))
    kvar_manifest = sorted(p.name for p in mal.glob("*.manifest.json"))
    assert kvar_sqlite == [
        "konto-20261126T120000Z.sqlite",
        "konto-20261126T130000Z.sqlite",
    ]
    assert kvar_manifest == [
        "konto-20261126T120000Z.manifest.json",
        "konto-20261126T130000Z.manifest.json",
    ]
    assert resultat[-1].raderade_lokala == ("konto-20261126T110000Z.sqlite",)

    # Åldersbaserad rotation (`behall_dagar=14`): äldre än 14 d raderas parvis, ny behålls alltid
    mal_alder = tmp_path / "alder"
    skapa_backup(
        db_fil,
        mal_alder,
        behall_antal=48,
        behall_dagar=None,
        nu=bas - timedelta(days=20),
    )
    skapa_backup(
        db_fil,
        mal_alder,
        behall_antal=48,
        behall_dagar=None,
        nu=bas - timedelta(days=3),
    )
    senaste = skapa_backup(
        db_fil,
        mal_alder,
        behall_antal=48,
        behall_dagar=14,
        nu=bas,
    )
    kvar_alder = sorted(p.name for p in mal_alder.glob("*.sqlite"))
    assert len(kvar_alder) == 2
    assert senaste.backup_fil.name in kvar_alder
    assert len(list(mal_alder.glob("*.manifest.json"))) == 2
    assert "konto-20261106T100000Z.sqlite" in senaste.raderade_lokala

    # Två anrop inom samma sekund ger unika filnamn utan överskrivning
    mal_samma = tmp_path / "samma_sekund"
    b1 = skapa_backup(db_fil, mal_samma, behall_antal=2, nu=bas)
    b2 = skapa_backup(db_fil, mal_samma, behall_antal=2, nu=bas)
    b3 = skapa_backup(db_fil, mal_samma, behall_antal=2, nu=bas)
    assert len({b1.backup_fil.name, b2.backup_fil.name, b3.backup_fil.name}) == 3
    assert sorted(p.name for p in mal_samma.glob("*.sqlite")) == [
        b2.backup_fil.name,
        b3.backup_fil.name,
    ]
    assert b3.raderade_lokala == (b1.backup_fil.name,)


def test_s3_uppladdning_med_lokal_lagring_och_felhantering(tmp_path: Path) -> None:
    db_fil = tmp_path / "konto.sqlite"
    _skapa_konto_db(db_fil)
    mal = tmp_path / "backuper"
    lagring = LokalLagring(tmp_path / "objektlager")

    res = skapa_backup(
        db_fil,
        mal,
        objektlagring=lagring,
        s3_prefix="backup/konto",
        nu=datetime(2026, 11, 26, 14, 30, 0, tzinfo=UTC),
    )
    assert res.s3_nycklar == (
        "backup/konto/konto-20261126T143000Z.sqlite",
        "backup/konto/konto-20261126T143000Z.manifest.json",
    )
    for nyckel in res.s3_nycklar:
        assert lagring.finns(nyckel)
    assert sha256_fil(lagring.rot / res.s3_nycklar[0]) == res.sha256

    with pytest.raises(BackupFel, match="objektlagring misslyckades"):
        skapa_backup(
            db_fil,
            tmp_path / "backuper_fel",
            objektlagring=_TrasigLagring(),
            nu=datetime(2026, 11, 26, 14, 31, 0, tzinfo=UTC),
        )


def test_drifts_halsa_scenarier(tmp_path: Path) -> None:
    db_fil = tmp_path / "konto.sqlite"
    _skapa_konto_db(db_fil)
    fernet_nyckel = Fernet.generate_key().decode("ascii")

    # 1. Helt grön status (WAL + 0o600 + giltig FLODEN_NYCKELHEMLIGHET + lokal lagring)
    gron = drifts_halsa(db_sokvag=db_fil, miljo={"FLODEN_NYCKELHEMLIGHET": fernet_nyckel})
    assert gron["ok"] is True
    assert gron["status"] == "ok"
    assert gron["varningar"] == []
    assert gron["databas"]["wal_aktivt"] is True
    assert gron["nyckelforrad"] == {
        "paslagen": True,
        "orsak": None,
        "miljovariabel": "FLODEN_NYCKELHEMLIGHET",
    }
    assert gron["lagring"]["typ"] == "lokal"

    # 2. Saknad FLODEN_NYCKELHEMLIGHET -> varning, men ok=True
    varn = drifts_halsa(db_sokvag=db_fil, miljo={})
    assert varn["ok"] is True
    assert varn["status"] == "varning"
    assert varn["nyckelforrad"]["paslagen"] is False
    assert varn["nyckelforrad"]["orsak"] == "saknas"

    # 3. Ogiltig FLODEN_NYCKELHEMLIGHET -> fel, och värdet läcker aldrig
    fel_nyckel = drifts_halsa(
        db_sokvag=db_fil, miljo={"FLODEN_NYCKELHEMLIGHET": "hemlig-trasig-strang-42"}
    )
    assert fel_nyckel["ok"] is False
    assert fel_nyckel["status"] == "fel"
    assert fel_nyckel["nyckelforrad"]["orsak"] == "ogiltig"
    assert "hemlig-trasig-strang-42" not in json.dumps(fel_nyckel)

    # 4. Ofullständig S3-konfig -> fel
    fel_s3 = drifts_halsa(
        db_sokvag=db_fil,
        miljo={"FLODEN_NYCKELHEMLIGHET": fernet_nyckel, "FLODEN_S3_TYP": "s3"},
    )
    assert fel_s3["ok"] is False
    assert fel_s3["lagring"]["typ"] == "s3"
    assert fel_s3["lagring"]["konfigurerad"] is False

    # 5. Saknad databas -> fel och skapar INTE databasfilen
    saknad = tmp_path / "finns_inte.sqlite"
    fel_db = drifts_halsa(db_sokvag=saknad, miljo={"FLODEN_NYCKELHEMLIGHET": fernet_nyckel})
    assert fel_db["ok"] is False
    assert fel_db["databas"]["finns"] is False
    assert not saknad.exists()

    # 6. Ej WAL + öppna rättigheter -> varningar
    ej_wal = tmp_path / "ej_wal.sqlite"
    _skapa_konto_db(ej_wal)
    c = sqlite3.connect(ej_wal)
    c.execute("PRAGMA journal_mode = DELETE")
    c.close()
    os.chmod(ej_wal, 0o644)
    varn_wal = drifts_halsa(db_sokvag=ej_wal, miljo={"FLODEN_NYCKELHEMLIGHET": fernet_nyckel})
    assert varn_wal["ok"] is True
    assert varn_wal["status"] == "varning"
    assert varn_wal["databas"]["wal_aktivt"] is False
    assert any("WAL" in v for v in varn_wal["varningar"])
    assert any("filrättigheter" in v for v in varn_wal["varningar"])


def test_cli_konto_backup_verifiera_halsa_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fernet_nyckel = Fernet.generate_key().decode("ascii")
    db_fil = tmp_path / "konto.sqlite"
    _skapa_konto_db(db_fil, med_nyckel_hemlighet=fernet_nyckel)
    ut_mapp = tmp_path / "backup_ut"
    s3_rot = tmp_path / "mock_s3"

    monkeypatch.setenv("FLODEN_NYCKELHEMLIGHET", fernet_nyckel)
    monkeypatch.setenv("FLODEN_LAGRING_ROT", str(s3_rot))
    monkeypatch.delenv("FLODEN_S3_TYP", raising=False)

    # 1. `floden konto backup --s3 --json`
    rc = floden_main(
        [
            "konto",
            "backup",
            "--db",
            str(db_fil),
            "--ut",
            str(ut_mapp),
            "--behall-antal",
            "2",
            "--behall-dagar",
            "14",
            "--s3",
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert _SYNTETISK_EPOST not in out
    assert fernet_nyckel not in out
    data: dict[str, Any] = json.loads(out)
    assert data["ok"] is True
    assert data["integrity_check"] == "ok"
    assert len(data["s3_nycklar"]) == 2
    backup_fil = Path(data["backup_fil"])
    assert backup_fil.is_file()
    assert (s3_rot / data["s3_nycklar"][0]).is_file()
    assert (s3_rot / data["s3_nycklar"][1]).is_file()

    # 2. `floden konto verifiera <fil> --json` (OK)
    rc_v = floden_main(["konto", "verifiera", str(backup_fil), "--json"])
    assert rc_v == 0
    ver_out = capsys.readouterr().out
    ver_data = json.loads(ver_out)
    assert ver_data["ok"] is True
    assert ver_data["manifest_verifierat"] is True

    # 3. Korrupt fil -> `floden konto verifiera` ger felkod 2
    raw = bytearray(backup_fil.read_bytes())
    raw[0] ^= 0x01
    backup_fil.write_bytes(bytes(raw))
    rc_bad = floden_main(["konto", "verifiera", str(backup_fil), "--json"])
    assert rc_bad == 2
    bad_caps = capsys.readouterr()
    assert json.loads(bad_caps.out)["ok"] is False
    assert "SHA-256" in bad_caps.err

    # 4. `floden konto halsa --db ... --json`
    rc_h = floden_main(["konto", "halsa", "--db", str(db_fil), "--json"])
    assert rc_h == 0
    halsa_out = capsys.readouterr().out
    assert _SYNTETISK_EPOST not in halsa_out
    assert fernet_nyckel not in halsa_out
    halsa_data = json.loads(halsa_out)
    assert halsa_data["ok"] is True
    assert halsa_data["status"] == "ok"
    assert halsa_data["databas"]["wal_aktivt"] is True
    assert halsa_data["nyckelforrad"]["paslagen"] is True

    # 5. `--s3` utan konfigurerad lagring får inte tyst falla tillbaka lokalt
    monkeypatch.delenv("FLODEN_LAGRING_ROT", raising=False)
    tom_konfig = tmp_path / "tom_config.toml"
    tom_konfig.write_text("[projekt]\nnamn = 'floden'\n", encoding="utf-8")
    rc_s3_fel = floden_main(
        [
            "konto",
            "backup",
            "--db",
            str(db_fil),
            "--ut",
            str(tmp_path / "ut_s3_fel"),
            "--konfig",
            str(tom_konfig),
            "--s3",
        ]
    )
    assert rc_s3_fel == 2
    assert "bucket" in capsys.readouterr().err.lower()

    # 6. `floden konto halsa` på saknad databas -> felkod 1
    rc_h_fel = floden_main(["konto", "halsa", "--db", str(tmp_path / "saknas.sqlite"), "--json"])
    assert rc_h_fel == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_systemd_enheter_har_driftkrav() -> None:
    """Kontrollerar att deploy/floden-konto-backup.{service,timer} följer S4-kraven."""
    service_fil = _REPO / "deploy" / "floden-konto-backup.service"
    timer_fil = _REPO / "deploy" / "floden-konto-backup.timer"
    assert service_fil.is_file()
    assert timer_fil.is_file()

    service_text = service_fil.read_text(encoding="utf-8")
    timer_text = timer_fil.read_text(encoding="utf-8")

    assert "Type=oneshot" in service_text
    assert "User=floden" in service_text
    assert "WorkingDirectory=/srv/floden" in service_text
    assert "EnvironmentFile=-/srv/floden/.env" in service_text
    assert "UMask=0077" in service_text
    assert "floden konto backup" in service_text

    assert "OnCalendar=hourly" in timer_text
    assert "RandomizedDelaySec=5m" in timer_text
    assert "Persistent=true" in timer_text
    assert "Unit=floden-konto-backup.service" in timer_text
