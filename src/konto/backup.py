"""Online-säker SQLite-backup, verifiering och driftskontroll för kontodatabasen (S4).

Säkerhetsregler (`AGENTS.md`):
- 0 personuppgifter (PII) i backup-manifest, loggar eller hälsosvar: e-postadresser, namn,
  tokens och sessionshashar skrivs aldrig ut. Endast anonyma tabellradantal och filmetadata.
- Hemligheter (`FLODEN_NYCKELHEMLIGHET`, `FLODEN_S3_ACCESS_KEY`, `FLODEN_S3_SECRET_KEY`,
  OAuth-klienthemligheter) exponeras aldrig i manifest, hälsosvar eller felmeddelanden.
- Inga tysta fel: varje backup verifieras med `PRAGMA integrity_check`, `PRAGMA foreign_key_check`
  och SHA-256 innan den ersätter målfilen atomiskt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from gemensamt.hash import sha256_fil
from gemensamt.konfig import KonfigFel, las_konfig
from gemensamt.objektlagring import ObjektLagring, ObjektLagringFel, las_lagring_konfig
from konto.konfig import KontoKonfig, KontoKonfigFel, las_konto_konfig
from konto.nycklar import HEMLIGHET_MILJOVARIABEL, _bygg_fernet

STANDARD_BEHALL_ANTAL: int = 48
STANDARD_BEHALL_DAGAR: int = 14
STANDARD_S3_PREFIX: str = "backup/konto"
STANDARD_BACKUP_MAPP: Path = Path("data/lokal/backup/konto")
MANIFEST_FORMAT: str = "floden-konto-backup/v1"
OBLIGATORISK_TABELL: str = "konto"

_SQLITE_MAGI: bytes = b"SQLite format 3\x00"
_BACKUP_FIL_RE = re.compile(r"^konto-(\d{8}T\d{6}Z)(?:-(\d+))?\.sqlite$")


class BackupFel(Exception):
    """Fel vid skapande, rotation, uppladdning eller verifiering av kontobackup."""


@dataclass(frozen=True)
class BackupResultat:
    """Resultat från `skapa_backup` (0 PII, 0 hemligheter)."""

    backup_fil: Path
    manifest_fil: Path
    sha256: str
    storlek_byte: int
    skapad_utc: str
    integrity_check: str
    foreign_key_check_ok: bool
    tabellrader: dict[str, int]
    raderade_lokala: tuple[str, ...] = ()
    s3_nycklar: tuple[str, ...] = ()

    def till_dict(self) -> dict[str, Any]:
        return {
            "backup_fil": str(self.backup_fil),
            "filnamn": self.backup_fil.name,
            "manifest_fil": str(self.manifest_fil),
            "sha256": self.sha256,
            "storlek_byte": self.storlek_byte,
            "skapad_utc": self.skapad_utc,
            "integrity_check": self.integrity_check,
            "foreign_key_check_ok": self.foreign_key_check_ok,
            "tabellrader": dict(self.tabellrader),
            "raderade_lokala": list(self.raderade_lokala),
            "s3_nycklar": list(self.s3_nycklar),
        }


@dataclass(frozen=True)
class VerifieringsResultat:
    """Resultat från `verifiera_backup` (0 PII, 0 hemligheter)."""

    backup_fil: Path
    manifest_fil: Path | None
    manifest_verifierat: bool
    sha256: str
    storlek_byte: int
    integrity_check: str
    foreign_key_check_ok: bool
    tabellrader: dict[str, int]
    ok: bool = True

    def till_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backup_fil": str(self.backup_fil),
            "filnamn": self.backup_fil.name,
            "manifest_fil": str(self.manifest_fil) if self.manifest_fil is not None else None,
            "manifest_verifierat": self.manifest_verifierat,
            "sha256": self.sha256,
            "storlek_byte": self.storlek_byte,
            "integrity_check": self.integrity_check,
            "foreign_key_check_ok": self.foreign_key_check_ok,
            "tabellrader": dict(self.tabellrader),
        }


def _manifest_sokvag_for(backup_fil: Path) -> Path:
    """Returnerar sökvägen till `.manifest.json` bredvid en `.sqlite`-backup."""
    return backup_fil.with_name(f"{backup_fil.stem}.manifest.json")


def _sqlite_ro_uri(sokvag: Path) -> str:
    return f"{sokvag.resolve().as_uri()}?mode=ro"


def _kontrollera_sqlite_fil(sokvag: Path, etikett: str) -> int:
    """Säkerställer att filen finns, inte är tom och inleds med SQLite-signaturen."""
    if not sokvag.exists():
        raise BackupFel(f"{etikett} finns inte: {sokvag}")
    if not sokvag.is_file():
        raise BackupFel(f"{etikett} är inte en vanlig fil: {sokvag}")
    storlek = sokvag.stat().st_size
    if storlek <= 0:
        raise BackupFel(f"{etikett} är tom (0 byte): {sokvag}")
    try:
        with sokvag.open("rb") as fh:
            huvud = fh.read(len(_SQLITE_MAGI))
    except OSError as exc:
        raise BackupFel(f"Kunde inte läsa {etikett.lower()} {sokvag}: {exc}") from exc
    if huvud != _SQLITE_MAGI:
        raise BackupFel(f"{etikett} är inte en giltig SQLite-databas: {sokvag.name}")
    return storlek


def _citat_identifierare(namn: str) -> str:
    return '"' + namn.replace('"', '""') + '"'


def _inspektera_sqlite_ro(
    sokvag: Path, *, kraver_konto_tabell: bool = True
) -> tuple[str, str, bool, bool, dict[str, int]]:
    """Öppnar SQLite read-only: `(journal_mode, integrity, fk_ok, har_konto, tabellrader)`."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(sokvag), uri=True, timeout=10.0)
        conn.execute("PRAGMA query_only = ON")
        jm_rad = conn.execute("PRAGMA journal_mode").fetchone()
        journal_mode = str(jm_rad[0]).lower() if jm_rad and jm_rad[0] is not None else "okand"

        ic_rader = conn.execute("PRAGMA integrity_check").fetchall()
        if len(ic_rader) == 1 and str(ic_rader[0][0]).lower() == "ok":
            integrity = "ok"
        else:
            feltexter = [str(r[0]) for r in ic_rader[:5]]
            integrity = "; ".join(feltexter) if feltexter else "tomt svar"
            raise BackupFel(
                f"SQLite PRAGMA integrity_check misslyckades för {sokvag.name}: {integrity[:200]}"
            )

        fk_rader = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_rader:
            tabeller_med_fel = sorted({str(r[0]) for r in fk_rader})
            raise BackupFel(
                f"SQLite PRAGMA foreign_key_check misslyckades för {sokvag.name}: "
                f"{len(fk_rader)} brott i tabeller {', '.join(tabeller_med_fel)}."
            )

        tabeller = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
        ]
        har_konto = OBLIGATORISK_TABELL in tabeller
        if kraver_konto_tabell and not har_konto:
            raise BackupFel(
                f"Databasen {sokvag.name} saknar obligatorisk tabell '{OBLIGATORISK_TABELL}'."
            )

        tabellrader: dict[str, int] = {}
        for tabell in tabeller:
            rad = conn.execute(
                f"SELECT COUNT(*) FROM {_citat_identifierare(tabell)}"
            ).fetchone()
            tabellrader[tabell] = int(rad[0]) if rad is not None else 0

        return journal_mode, integrity, True, har_konto, tabellrader
    except BackupFel:
        raise
    except sqlite3.Error as exc:
        raise BackupFel(f"SQLite-fel vid kontroll av {sokvag.name}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def _skriv_json_atomiskt(
    mal: Path, data: Mapping[str, Any], *, filrattigheter: int = 0o600
) -> Path:
    """Skriver JSON atomiskt till `mal` via tempfil + `chmod` + `fsync` + `os.replace`."""
    innehall = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_namn = tempfile.mkstemp(prefix=f".{mal.name}.", suffix=".tmp", dir=str(mal.parent))
    tmp_path = Path(tmp_namn)
    try:
        os.chmod(tmp_path, filrattigheter)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(innehall)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, filrattigheter)
        os.replace(tmp_path, mal)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return mal


def _normalisera_utc(nu: datetime | None) -> datetime:
    if nu is None:
        return datetime.now(UTC)
    if nu.tzinfo is None:
        return nu.replace(tzinfo=UTC)
    return nu.astimezone(UTC)


def _valj_malfil(malmapp: Path, nu_utc: datetime) -> Path:
    """Skapar unikt `konto-YYYYMMDDTHHMMSSZ.sqlite`-namn utan att skriva över äldre filer."""
    stampel = nu_utc.strftime("%Y%m%dT%H%M%SZ")
    grund = malmapp / f"konto-{stampel}.sqlite"
    if not grund.exists():
        return grund
    for nr in range(1, 10000):
        kandidat = malmapp / f"konto-{stampel}-{nr:02d}.sqlite"
        if not kandidat.exists():
            return kandidat
    raise BackupFel(f"Kunde inte skapa unikt backupfilnamn i {malmapp} för tidsstämpel {stampel}.")


def _rotera_lokala_backuper(
    malmapp: Path,
    *,
    nyss_skapad: Path,
    behall_antal: int,
    behall_dagar: int | None,
    nu_utc: datetime,
) -> tuple[str, ...]:
    """Raderar äldre lokala backuper parvis (`.sqlite` + `.manifest.json`), behåller senaste."""
    kandidater: list[tuple[datetime, int, Path]] = []
    for post in malmapp.iterdir():
        if not post.is_file():
            continue
        m = _BACKUP_FIL_RE.match(post.name)
        if not m:
            continue
        dt = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        sekvens = int(m.group(2)) if m.group(2) is not None else 0
        kandidater.append((dt, sekvens, post))

    kandidater.sort(key=lambda rad: (rad[0], rad[1], rad[2].name), reverse=True)
    grans_dt = nu_utc - timedelta(days=behall_dagar) if behall_dagar is not None else None
    nyss_resolved = nyss_skapad.resolve()

    raderade: list[str] = []
    for index, (dt, _, fil) in enumerate(kandidater):
        if fil.resolve() == nyss_resolved:
            continue
        for_manga = index >= behall_antal
        for_gammal = grans_dt is not None and dt < grans_dt
        if for_manga or for_gammal:
            manifest = _manifest_sokvag_for(fil)
            fil.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            raderade.append(fil.name)

    return tuple(raderade)


def _rensa_endpoint_utan_userinfo(endpoint: str | None) -> str | None:
    """Tar bort eventuell `user:pass@` från endpoint-URL så inga hemligheter någonsin skrivs ut."""
    if not endpoint:
        return None
    delar = urlsplit(endpoint)
    if "@" not in delar.netloc:
        return endpoint
    ren_netloc = delar.netloc.rsplit("@", 1)[-1]
    return urlunsplit((delar.scheme, ren_netloc, delar.path, "", ""))


def skapa_backup(
    db_sokvag: Path | str,
    malmapp: Path | str,
    *,
    behall_antal: int = STANDARD_BEHALL_ANTAL,
    behall_dagar: int | None = STANDARD_BEHALL_DAGAR,
    objektlagring: ObjektLagring | None = None,
    s3_prefix: str = STANDARD_S3_PREFIX,
    nu: datetime | None = None,
) -> BackupResultat:
    """Skapar en online-säker, verifierad SQLite-backup med SHA-256-manifest och rotation.

    Skriver 0 PII och 0 hemligheter i manifest och resultat.
    """
    if isinstance(behall_antal, bool) or behall_antal < 1:
        raise BackupFel(f"behall_antal måste vara >= 1, fick {behall_antal}.")
    if behall_dagar is not None and (isinstance(behall_dagar, bool) or behall_dagar < 0):
        raise BackupFel(f"behall_dagar måste vara >= 0 eller None, fick {behall_dagar}.")

    kalla = Path(db_sokvag)
    _kontrollera_sqlite_fil(kalla, "Källdatabasen")

    mal = Path(malmapp)
    mal_var_ny = not mal.exists()
    mal.mkdir(parents=True, exist_ok=True)
    if mal_var_ny:
        os.chmod(mal, 0o700)

    nu_utc = _normalisera_utc(nu)
    skapad_utc = nu_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    fd, tmp_namn = tempfile.mkstemp(prefix=".konto-backup-", suffix=".sqlite.tmp", dir=str(mal))
    os.close(fd)
    tmp_db = Path(tmp_namn)
    os.chmod(tmp_db, 0o600)

    kalla_conn: sqlite3.Connection | None = None
    mal_conn: sqlite3.Connection | None = None
    try:
        try:
            kalla_conn = sqlite3.connect(_sqlite_ro_uri(kalla), uri=True, timeout=30.0)
            mal_conn = sqlite3.connect(str(tmp_db), timeout=30.0)
            kalla_conn.backup(mal_conn, pages=256, sleep=0.05)
            # Gör backupfilen helt fristående (DELETE-journal, inga -wal/-shm-sidofiler).
            mal_conn.execute("PRAGMA journal_mode = DELETE")
            mal_conn.commit()
        except sqlite3.Error as exc:
            raise BackupFel(f"SQLite online-backup misslyckades för {kalla.name}: {exc}") from exc
        finally:
            if mal_conn is not None:
                mal_conn.close()
            if kalla_conn is not None:
                kalla_conn.close()
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{tmp_db}{suffix}").unlink(missing_ok=True)

        os.chmod(tmp_db, 0o600)
        _, integrity, fk_ok, _, tabellrader = _inspektera_sqlite_ro(
            tmp_db, kraver_konto_tabell=True
        )
        sha256 = sha256_fil(tmp_db)
        storlek_byte = tmp_db.stat().st_size

        slut_db = _valj_malfil(mal, nu_utc)
        slut_manifest = _manifest_sokvag_for(slut_db)
        os.replace(tmp_db, slut_db)
    except Exception:
        tmp_db.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{tmp_db}{suffix}").unlink(missing_ok=True)
        raise

    manifest_data: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "filnamn": slut_db.name,
        "sha256": sha256,
        "storlek_byte": storlek_byte,
        "skapad_utc": skapad_utc,
        "integrity_check": integrity,
        "foreign_key_check": "ok" if fk_ok else "fel",
        "tabellrader": tabellrader,
    }
    try:
        _skriv_json_atomiskt(slut_manifest, manifest_data, filrattigheter=0o600)
    except Exception:
        slut_db.unlink(missing_ok=True)
        raise

    s3_nycklar: tuple[str, ...] = ()
    if objektlagring is not None:
        ren_prefix = s3_prefix.strip().strip("/")
        db_nyckel = f"{ren_prefix}/{slut_db.name}" if ren_prefix else slut_db.name
        manifest_nyckel = f"{ren_prefix}/{slut_manifest.name}" if ren_prefix else slut_manifest.name
        manifest_sha = sha256_fil(slut_manifest)
        try:
            uppladdad_db_sha = objektlagring.ladda_upp_fil(slut_db, db_nyckel, sha256=sha256)
            uppladdad_manifest_sha = objektlagring.ladda_upp_fil(
                slut_manifest, manifest_nyckel, sha256=manifest_sha
            )
            if uppladdad_db_sha.lower() != sha256.lower() or not objektlagring.finns(db_nyckel):
                raise BackupFel(f"Uppladdningsverifiering misslyckades för {db_nyckel!r}.")
            if uppladdad_manifest_sha.lower() != manifest_sha.lower() or not objektlagring.finns(
                manifest_nyckel
            ):
                raise BackupFel(f"Uppladdningsverifiering misslyckades för {manifest_nyckel!r}.")
        except BackupFel:
            raise
        except (ObjektLagringFel, OSError) as exc:
            raise BackupFel(f"Uppladdning till objektlagring misslyckades: {exc}") from exc
        s3_nycklar = (db_nyckel, manifest_nyckel)

    raderade = _rotera_lokala_backuper(
        mal,
        nyss_skapad=slut_db,
        behall_antal=behall_antal,
        behall_dagar=behall_dagar,
        nu_utc=nu_utc,
    )

    return BackupResultat(
        backup_fil=slut_db,
        manifest_fil=slut_manifest,
        sha256=sha256,
        storlek_byte=storlek_byte,
        skapad_utc=skapad_utc,
        integrity_check=integrity,
        foreign_key_check_ok=fk_ok,
        tabellrader=tabellrader,
        raderade_lokala=raderade,
        s3_nycklar=s3_nycklar,
    )


def verifiera_backup(backup_fil: Path | str) -> VerifieringsResultat:
    """Verifierar en backupfil (SHA-256, `integrity_check`, `foreign_key_check`, `konto`).

    Kastar alltid `BackupFel` vid saknad, korrupt eller manipulerad fil (aldrig tyst OK).
    """
    fil = Path(backup_fil)
    if not fil.exists():
        raise BackupFel(f"Backupfilen finns inte: {fil}")
    if not fil.is_file():
        raise BackupFel(f"Backupfilen är inte en vanlig fil: {fil}")
    storlek_byte = fil.stat().st_size
    if storlek_byte <= 0:
        raise BackupFel(f"Backupfilen är tom (0 byte): {fil}")

    faktisk_sha = sha256_fil(fil)
    manifest_sokvag = _manifest_sokvag_for(fil)
    manifest_fil: Path | None = None
    manifest_verifierat = False

    if manifest_sokvag.exists():
        if not manifest_sokvag.is_file():
            raise BackupFel(f"Backup-manifestet är inte en vanlig fil: {manifest_sokvag}")
        try:
            manifest_data = json.loads(manifest_sokvag.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupFel(
                f"Kunde inte läsa backup-manifestet {manifest_sokvag.name}: {exc}"
            ) from exc
        if not isinstance(manifest_data, dict):
            raise BackupFel(
                f"Ogiltigt backup-manifest (måste vara JSON-objekt): {manifest_sokvag.name}"
            )
        forvantad_sha = manifest_data.get("sha256")
        if not isinstance(forvantad_sha, str) or not forvantad_sha.strip():
            raise BackupFel(
                f"Backup-manifestet {manifest_sokvag.name} saknar giltigt fält 'sha256'."
            )
        if forvantad_sha.strip().lower() != faktisk_sha.lower():
            raise BackupFel(
                f"SHA-256-avvikelse för {fil.name}: "
                f"manifestet anger {forvantad_sha.strip()}, filen har {faktisk_sha}."
            )
        if "storlek_byte" in manifest_data:
            forvantad_storlek = manifest_data["storlek_byte"]
            if (
                isinstance(forvantad_storlek, bool)
                or not isinstance(forvantad_storlek, int)
                or forvantad_storlek != storlek_byte
            ):
                raise BackupFel(
                    f"Storleksavvikelse för {fil.name}: "
                    f"manifestet anger {forvantad_storlek} B, filen är {storlek_byte} B."
                )
        manifest_fil = manifest_sokvag
        manifest_verifierat = True

    _kontrollera_sqlite_fil(fil, "Backupfilen")
    _, integrity, fk_ok, _, tabellrader = _inspektera_sqlite_ro(fil, kraver_konto_tabell=True)

    return VerifieringsResultat(
        backup_fil=fil,
        manifest_fil=manifest_fil,
        manifest_verifierat=manifest_verifierat,
        sha256=faktisk_sha,
        storlek_byte=storlek_byte,
        integrity_check=integrity,
        foreign_key_check_ok=fk_ok,
        tabellrader=tabellrader,
        ok=True,
    )


def _las_app_konfig(konfig_fil: Path | None) -> tuple[dict[str, Any], Path | None, str | None]:
    """Läser `config.toml` + eventuell `config.local.toml`. Returnerar `(konfig, basmapp, fel)`."""
    if konfig_fil is not None:
        sokvag = Path(konfig_fil)
        if not sokvag.is_file():
            return {}, None, f"Konfigurationsfilen saknas: {sokvag}"
        rot = sokvag.resolve().parent
        lokal = rot / "config.local.toml"
        try:
            return las_konfig(sokvag, lokal if lokal.is_file() else None), rot, None
        except KonfigFel as exc:
            return {}, rot, str(exc)

    standard = Path("config.toml")
    if not standard.is_file():
        return {}, None, None
    rot = standard.resolve().parent
    lokal = rot / "config.local.toml"
    try:
        return las_konfig(standard, lokal if lokal.is_file() else None), rot, None
    except KonfigFel as exc:
        return {}, rot, str(exc)


def _bestam_db_sokvag(
    app_konfig: Mapping[str, Any],
    basmapp: Path | None,
    *,
    db_sokvag: Path | str | None,
    miljo: Mapping[str, str],
) -> tuple[Path, str | None]:
    """Avgör sökväg till konto-DB och fångar ev. `KontoKonfigFel` utan att exponera hemligheter."""
    if db_sokvag is not None:
        return Path(db_sokvag), None
    konto_fel: str | None = None
    try:
        kk = las_konto_konfig(app_konfig, miljo=miljo)
        db = kk.databas
    except KontoKonfigFel as exc:
        konto_fel = str(exc)
        konto_sektion = app_konfig.get("konto", {})
        ra_db = konto_sektion.get("databas") if isinstance(konto_sektion, Mapping) else None
        db = Path(ra_db) if isinstance(ra_db, str) and ra_db.strip() else KontoKonfig().databas
    if not db.is_absolute() and basmapp is not None:
        db = basmapp / db
    return db, konto_fel


def drifts_halsa(
    konfig_fil: Path | str | None = None,
    *,
    db_sokvag: Path | str | None = None,
    miljo: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Driftskontroll för kontodatabas, nyckelförråd och objektlagring (0 PII, 0 hemligheter).

    Kontrollerar (read-only, utan att ändra databasen eller skicka nätverksanrop):
    (a) Konto-DB existens, filrättigheter, `journal_mode` (WAL), `PRAGMA integrity_check`
        och `PRAGMA foreign_key_check`.
    (b) `NyckelForrad.paslagen` / `orsak` via `FLODEN_NYCKELHEMLIGHET` utan att exponera nycklar.
    (c) S3-/objektlagringskonfiguration (`lokal`/`s3`) och eventuella konfigurationsvarningar.
    """
    env = os.environ if miljo is None else miljo
    varningar: list[str] = []
    kritiska_fel: list[str] = []

    app_konfig, basmapp, konfig_fel = _las_app_konfig(
        Path(konfig_fil) if konfig_fil is not None else None
    )
    if konfig_fel is not None:
        kritiska_fel.append(konfig_fel)
        varningar.append(konfig_fel)

    effektiv_db, konto_konfig_fel = _bestam_db_sokvag(
        app_konfig, basmapp, db_sokvag=db_sokvag, miljo=env
    )
    if konto_konfig_fel is not None:
        varningar.append(f"Kontokonfiguration: {konto_konfig_fel}")

    # (a) Konto-DB (strikt read-only)
    db_info: dict[str, Any] = {
        "sokvag": str(effektiv_db),
        "finns": effektiv_db.is_file(),
        "storlek_byte": None,
        "journal_mode": None,
        "wal_aktivt": False,
        "integrity_check": None,
        "foreign_key_check_ok": None,
        "har_konto_tabell": False,
        "tabellrader": {},
        "filrattigheter": None,
        "fel": None,
    }
    if not effektiv_db.exists():
        fel = f"Kontodatabasen finns inte: {effektiv_db}"
        db_info["fel"] = fel
        kritiska_fel.append(fel)
    elif not effektiv_db.is_file():
        fel = f"Kontodatabasen är inte en vanlig fil: {effektiv_db}"
        db_info["fel"] = fel
        kritiska_fel.append(fel)
    else:
        st = effektiv_db.stat()
        db_info["storlek_byte"] = st.st_size
        fil_mode = stat.S_IMODE(st.st_mode)
        db_info["filrattigheter"] = f"{fil_mode:04o}"
        if fil_mode & 0o077:
            varningar.append(
                f"Kontodatabasen har öppna filrättigheter ({fil_mode:04o}); rekommenderat är 0600."
            )
        try:
            _kontrollera_sqlite_fil(effektiv_db, "Kontodatabasen")
            jm, integrity, fk_ok, har_konto, rader = _inspektera_sqlite_ro(
                effektiv_db, kraver_konto_tabell=True
            )
            db_info["journal_mode"] = jm
            db_info["wal_aktivt"] = jm == "wal"
            db_info["integrity_check"] = integrity
            db_info["foreign_key_check_ok"] = fk_ok
            db_info["har_konto_tabell"] = har_konto
            db_info["tabellrader"] = rader
            if jm != "wal":
                varningar.append(
                    f"Kontodatabasen använder inte WAL-läge (journal_mode={jm}); "
                    "aktivera WAL för online-säker drift och backup."
                )
        except BackupFel as exc:
            db_info["fel"] = str(exc)
            if db_info["integrity_check"] is None:
                db_info["integrity_check"] = "fel"
            kritiska_fel.append(str(exc))

    # (b) Nyckelförråd (samma kontroll som NyckelForrad.paslagen/orsak, 0 nyckelmaterial)
    fernet, nyckel_orsak = _bygg_fernet(env.get(HEMLIGHET_MILJOVARIABEL))
    nyckel_paslagen = fernet is not None
    nyckelforrad_info: dict[str, Any] = {
        "paslagen": nyckel_paslagen,
        "orsak": nyckel_orsak,
        "miljovariabel": HEMLIGHET_MILJOVARIABEL,
    }
    if nyckel_orsak == "ogiltig":
        fel = f"{HEMLIGHET_MILJOVARIABEL} är ogiltig: nyckelförrådet är avstängt."
        kritiska_fel.append(fel)
        varningar.append(fel)
    elif nyckel_orsak == "saknas":
        varningar.append(f"{HEMLIGHET_MILJOVARIABEL} saknas: nyckelförrådet är avstängt.")

    # (c) S3- / objektlagringskonfig (aldrig access_key eller secret_key i output!)
    lagring_info: dict[str, Any] = {
        "typ": None,
        "konfigurerad": False,
        "endpoint": None,
        "bucket": None,
        "region": None,
        "prefix": None,
        "rot": None,
        "har_nycklar": False,
        "boto3_installerad": importlib.util.find_spec("boto3") is not None,
        "fel": None,
    }
    try:
        lk = las_lagring_konfig(app_konfig, miljo=env)
        lagring_info["typ"] = lk.typ
        lagring_info["prefix"] = lk.prefix
        if lk.typ == "lokal":
            lagring_info["konfigurerad"] = True
            lagring_info["rot"] = str(lk.rot)
        else:
            lagring_info["endpoint"] = _rensa_endpoint_utan_userinfo(lk.endpoint)
            lagring_info["bucket"] = lk.bucket
            lagring_info["region"] = lk.region
            har_nycklar = bool((lk.access_key or "").strip() and (lk.secret_key or "").strip())
            lagring_info["har_nycklar"] = har_nycklar
            saknade_falt: list[str] = []
            if not (lk.endpoint or "").strip():
                saknade_falt.append("endpoint")
            if not (lk.bucket or "").strip():
                saknade_falt.append("bucket")
            if not har_nycklar:
                saknade_falt.append("FLODEN_S3_ACCESS_KEY/FLODEN_S3_SECRET_KEY")
            if saknade_falt:
                fel = f"S3-lagring är vald men saknar: {', '.join(saknade_falt)}."
                lagring_info["fel"] = fel
                lagring_info["konfigurerad"] = False
                kritiska_fel.append(fel)
                varningar.append(fel)
            else:
                lagring_info["konfigurerad"] = True
                if not lagring_info["boto3_installerad"]:
                    varningar.append(
                        "S3-lagring är konfigurerad men 'boto3' saknas i Python-miljön "
                        "(installera extras [s3])."
                    )
    except ObjektLagringFel as exc:
        lagring_info["fel"] = str(exc)
        kritiska_fel.append(str(exc))
        varningar.append(f"Lagringskonfiguration: {exc}")

    ok = len(kritiska_fel) == 0
    status = "fel" if not ok else ("varning" if varningar else "ok")
    return {
        "ok": ok,
        "status": status,
        "kontrollerad_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "databas": db_info,
        "nyckelforrad": nyckelforrad_info,
        "lagring": lagring_info,
        "varningar": varningar,
    }
