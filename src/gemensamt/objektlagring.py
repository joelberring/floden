"""Lagringsabstraktion för Floden: lokalt filsystem och S3-kompatibel objektlagring.

Stöder Hetzner Object Storage (t.ex. `https://fsn1.your-objectstorage.com` / `hel1`) och andra
S3-kompatibla tjänster via valfritt `boto3`-beroende (`pip install -e ".[s3]"`).

Säkerhetsregler (`AGENTS.md`):
- Inga S3-nycklar i git; nycklar läses enbart från miljövariabler (`FLODEN_S3_*`) eller
  `config.local.toml` (som ligger i `.gitignore`).
- Offline-kompatibelt: när S3 inte är konfigurerat används `LokalLagring` direkt mot disk.
- Dataklassning: öppen grunddata hålls i separat bucket/prefix från intern eller skyddad data.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gemensamt.hash import sha256_fil

LagringTyp = Literal["lokal", "s3"]
TILLATNA_LAGRINGSTYPER: tuple[str, ...] = ("lokal", "s3")


class ObjektLagringFel(Exception):
    """Fel vid läsning, skrivning eller konfiguration av objektlagring."""


@dataclass(frozen=True)
class LagringKonfig:
    """Konfiguration för objektlagring (från `[lagring]` i config + `FLODEN_S3_*`)."""

    typ: LagringTyp = "lokal"
    rot: Path = Path("data/lagring")
    endpoint: str | None = None
    bucket: str | None = None
    region: str = "fsn1"
    access_key: str | None = None
    secret_key: str | None = None
    prefix: str = ""

    def __post_init__(self) -> None:
        if self.typ not in TILLATNA_LAGRINGSTYPER:
            raise ObjektLagringFel(
                f"Ogiltig lagringstyp {self.typ!r}; välj bland {TILLATNA_LAGRINGSTYPER}."
            )


def _normalisera_nyckel(nyckel: str, *, tillat_tom: bool = False) -> str:
    """Validerar och normaliserar en objektnyckel (POSIX-format, ingen path traversal)."""
    if "\\" in nyckel:
        raise ObjektLagringFel(f"Ogiltig objektnyckel (innehåller '\\'): {nyckel!r}")
    ren = nyckel.strip().lstrip("/")
    if not ren:
        if tillat_tom:
            return ""
        raise ObjektLagringFel("Objektnyckel får inte vara tom.")
    segment = [s for s in ren.split("/") if s]
    if any(s in (".", "..") for s in segment):
        raise ObjektLagringFel(f"Ogiltig objektnyckel (path traversal): {nyckel!r}")
    return "/".join(segment)


def _normalisera_prefix(prefix: str) -> str:
    """Normaliserar ett prefix (tillåter tomt prefix och behåller avslutande `/` vid behov)."""
    if not prefix or not prefix.strip():
        return ""
    slutar_med_snedstreck = prefix.endswith("/")
    ren = _normalisera_nyckel(prefix, tillat_tom=True)
    if not ren:
        return ""
    return f"{ren}/" if slutar_med_snedstreck else ren


def geografi_nyckel(kommun: str, ruta: str, filnamn: str) -> str:
    """Returnerar nyckel `geografi/<kommun>/<ruta>/<filnamn>`."""
    return _normalisera_nyckel(f"geografi/{kommun}/{ruta}/{filnamn}")


def projekt_nyckel(projekt_id: str, filnamn: str) -> str:
    """Returnerar nyckel `projekt/<projekt_id>/<filnamn>`."""
    return _normalisera_nyckel(f"projekt/{projekt_id}/{filnamn}")


def tmp_radata_prefix(jobb_id: str, ruta: str | None = None) -> str:
    """Returnerar prefix `tmp/radata/<jobb_id>/[<ruta>/]`."""
    if ruta is None:
        return _normalisera_prefix(f"tmp/radata/{jobb_id}/")
    return _normalisera_prefix(f"tmp/radata/{jobb_id}/{ruta}/")


def tmp_skiss_prefix(session_id: str) -> str:
    """Returnerar prefix `tmp/skiss/<session_id>/`."""
    return _normalisera_prefix(f"tmp/skiss/{session_id}/")


def las_lagring_konfig(
    konfig: Mapping[str, Any] | None = None,
    *,
    miljo: Mapping[str, str] | None = None,
) -> LagringKonfig:
    """Läser `[lagring]` ur konfigurationslexikon och överlagrar `FLODEN_S3_*`-variabler."""
    env = os.environ if miljo is None else miljo
    sektion = konfig.get("lagring", {}) if konfig is not None else {}
    if not isinstance(sektion, Mapping):
        raise ObjektLagringFel("Avsnittet [lagring] i konfigurationen måste vara ett objekt.")

    def _str_val(env_namn: str, konfig_nyckel: str, standard: str | None = None) -> str | None:
        fran_env = env.get(env_namn)
        if fran_env is not None and fran_env.strip() != "":
            return fran_env.strip()
        fran_konfig = sektion.get(konfig_nyckel)
        if fran_konfig is not None and str(fran_konfig).strip() != "":
            return str(fran_konfig).strip()
        return standard

    typ_raw = _str_val("FLODEN_S3_TYP", "typ", "lokal") or "lokal"
    if typ_raw not in TILLATNA_LAGRINGSTYPER:
        raise ObjektLagringFel(
            f"Ogiltig lagringstyp {typ_raw!r}; välj bland {TILLATNA_LAGRINGSTYPER}."
        )
    typ: LagringTyp = "s3" if typ_raw == "s3" else "lokal"

    rot_raw = _str_val("FLODEN_LAGRING_ROT", "rot", "data/lagring") or "data/lagring"
    endpoint = _str_val("FLODEN_S3_ENDPOINT", "endpoint")
    bucket = _str_val("FLODEN_S3_BUCKET", "bucket")
    region = _str_val("FLODEN_S3_REGION", "region", "fsn1") or "fsn1"
    access_key = _str_val("FLODEN_S3_ACCESS_KEY", "access_key")
    secret_key = _str_val("FLODEN_S3_SECRET_KEY", "secret_key")
    prefix = _str_val("FLODEN_S3_PREFIX", "prefix", "") or ""

    return LagringKonfig(
        typ=typ,
        rot=Path(rot_raw),
        endpoint=endpoint,
        bucket=bucket,
        region=region,
        access_key=access_key,
        secret_key=secret_key,
        prefix=_normalisera_prefix(prefix),
    )


class ObjektLagring(ABC):
    """Gemensamt gränssnitt för lokal lagring och S3-kompatibel objektlagring."""

    @abstractmethod
    def finns(self, nyckel: str) -> bool:
        """Returnerar True om objektet finns i lagringen."""

    @abstractmethod
    def ladda_upp_fil(
        self,
        lokal_fil: str | Path,
        nyckel: str,
        *,
        sha256: str | None = None,
    ) -> str:
        """Laddar upp en lokal fil, verifierar kontrollsumma/storlek och returnerar SHA-256."""

    @abstractmethod
    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        """Laddar ner objektet atomärt (tempfil -> rename) till `lokal_fil`."""

    @abstractmethod
    def radera_prefix(self, prefix: str) -> int:
        """Raderar alla objekt under `prefix` och returnerar antal raderade objekt."""

    @abstractmethod
    def lista_prefix(self, prefix: str = "") -> list[str]:
        """Returnerar sorterad lista över objektnycklar under `prefix`."""

    @abstractmethod
    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        """Returnerar en tidsbegränsad läs-URL (eller lokal `file://`-URI för `LokalLagring`)."""


def _kopiera_atomiskt(kalla: Path, mal: Path) -> None:
    """Kopierar en fil atomärt via temporärfil i målmappen följt av `os.replace`."""
    mal.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_namn = tempfile.mkstemp(prefix=".floden_tmp_", dir=str(mal.parent))
    tmp_path = Path(tmp_namn)
    try:
        os.close(fd)
        shutil.copyfile(kalla, tmp_path)
        os.replace(tmp_path, mal)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


class LokalLagring(ObjektLagring):
    """Filsystemsbaserad objektlagring för lokal utveckling och CI utan nätverk."""

    def __init__(self, rot: str | Path) -> None:
        self._rot = Path(rot).resolve()
        self._rot.mkdir(parents=True, exist_ok=True)

    @property
    def rot(self) -> Path:
        return self._rot

    def _sokvag_for(self, nyckel: str) -> Path:
        ren = _normalisera_nyckel(nyckel)
        mal = (self._rot / ren).resolve()
        if not mal.is_relative_to(self._rot):
            raise ObjektLagringFel(f"Objektnyckel pekar utanför lagringsroten: {nyckel!r}")
        return mal

    def finns(self, nyckel: str) -> bool:
        return self._sokvag_for(nyckel).is_file()

    def ladda_upp_fil(
        self,
        lokal_fil: str | Path,
        nyckel: str,
        *,
        sha256: str | None = None,
    ) -> str:
        kalla = Path(lokal_fil)
        if not kalla.is_file():
            raise ObjektLagringFel(f"Lokal fil finns inte: {kalla}")
        faktisk_sha = sha256_fil(kalla)
        if sha256 is not None and faktisk_sha.lower() != sha256.lower():
            raise ObjektLagringFel(
                f"SHA-256-avvikelse före uppladdning av {kalla}: "
                f"förväntade {sha256}, fick {faktisk_sha}."
            )
        mal = self._sokvag_for(nyckel)
        _kopiera_atomiskt(kalla, mal)
        if mal.stat().st_size != kalla.stat().st_size or sha256_fil(mal) != faktisk_sha:
            mal.unlink(missing_ok=True)
            raise ObjektLagringFel(f"Verifiering efter skrivning misslyckades för {nyckel!r}.")
        return faktisk_sha

    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        kalla = self._sokvag_for(nyckel)
        if not kalla.is_file():
            raise ObjektLagringFel(f"Objektet finns inte i lokal lagring: {nyckel!r}")
        mal = Path(lokal_fil)
        _kopiera_atomiskt(kalla, mal)
        return mal

    def lista_prefix(self, prefix: str = "") -> list[str]:
        ren_prefix = _normalisera_prefix(prefix)
        hittade: list[str] = []
        for fil in self._rot.rglob("*"):
            if fil.is_symlink() or not fil.is_file():
                continue
            rel = fil.relative_to(self._rot).as_posix()
            if not ren_prefix or rel.startswith(ren_prefix):
                hittade.append(rel)
        hittade.sort()
        return hittade

    def radera_prefix(self, prefix: str) -> int:
        ren_prefix = _normalisera_prefix(prefix)
        if not ren_prefix:
            raise ObjektLagringFel("Tomt prefix vägras vid radera_prefix (skydd mot helradering).")
        nycklar = self.lista_prefix(ren_prefix)
        raderade = 0
        for nyckel in nycklar:
            fil = self._sokvag_for(nyckel)
            if fil.is_file():
                fil.unlink()
                raderade += 1
                # Rensa tomma överordnade kataloger upp till (men aldrig inklusive) roten
                parent = fil.parent
                while parent != self._rot and parent.is_relative_to(self._rot):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        return raderade

    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        if giltig_s <= 0:
            raise ObjektLagringFel(f"giltig_s måste vara > 0, fick {giltig_s}.")
        mal = self._sokvag_for(nyckel)
        if not mal.is_file():
            raise ObjektLagringFel(f"Objektet finns inte i lokal lagring: {nyckel!r}")
        return mal.as_uri()


def _ar_s3_not_found(exc: Exception) -> bool:
    """Avgör om ett boto3/botocore-fel (eller mockfel) motsvarar HTTP 404 / NoSuchKey."""
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        err = response.get("Error", {})
        if isinstance(err, Mapping):
            kod = str(err.get("Code", ""))
            if kod in ("404", "NoSuchKey", "NotFound"):
                return True
        meta = response.get("ResponseMetadata", {})
        if isinstance(meta, Mapping) and meta.get("HTTPStatusCode") == 404:
            return True
    return isinstance(exc, FileNotFoundError)


class S3Lagring(ObjektLagring):
    """S3-kompatibel objektlagring (Hetzner Object Storage, Cloudflare R2 m.fl.) via `boto3`."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "fsn1",
        prefix: str = "",
        klient: Any | None = None,
    ) -> None:
        if not bucket.strip():
            raise ObjektLagringFel("S3-konfiguration saknar 'bucket'.")
        if klient is None:
            if not endpoint_url.strip():
                raise ObjektLagringFel("S3-konfiguration saknar 'endpoint'.")
            if not access_key.strip() or not secret_key.strip():
                raise ObjektLagringFel(
                    "S3-konfiguration saknar nycklar (FLODEN_S3_ACCESS_KEY / FLODEN_S3_SECRET_KEY)."
                )
            try:
                import boto3
            except ImportError as exc:
                raise ObjektLagringFel(
                    "S3-lagring kräver det valfria beroendet 'boto3'. "
                    "Installera med: pip install -e '.[s3]'"
                ) from exc
            klient = boto3.client(
                "s3",
                endpoint_url=endpoint_url.strip(),
                aws_access_key_id=access_key.strip(),
                aws_secret_access_key=secret_key.strip(),
                region_name=region.strip() or "fsn1",
            )
        self._klient: Any = klient
        self._bucket = bucket.strip()
        self._endpoint_url = endpoint_url.strip()
        self._region = region.strip() or "fsn1"
        self._prefix = _normalisera_prefix(prefix)
        if self._prefix and not self._prefix.endswith("/"):
            self._prefix = f"{self._prefix}/"

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def klient(self) -> Any:
        return self._klient

    @property
    def endpoint_url(self) -> str:
        return self._endpoint_url

    @property
    def region(self) -> str:
        return self._region

    def _full_nyckel(self, nyckel: str) -> str:
        ren = _normalisera_nyckel(nyckel)
        return f"{self._prefix}{ren}"

    def _full_prefix(self, prefix: str) -> str:
        ren = _normalisera_prefix(prefix)
        return f"{self._prefix}{ren}"

    def finns(self, nyckel: str) -> bool:
        full = self._full_nyckel(nyckel)
        try:
            self._klient.head_object(Bucket=self._bucket, Key=full)
            return True
        except Exception as exc:  # noqa: BLE001 - botocore ClientError saknas utan boto3
            if _ar_s3_not_found(exc):
                return False
            raise ObjektLagringFel(f"S3 head_object misslyckades för {full!r}: {exc}") from exc

    def ladda_upp_fil(
        self,
        lokal_fil: str | Path,
        nyckel: str,
        *,
        sha256: str | None = None,
    ) -> str:
        kalla = Path(lokal_fil)
        if not kalla.is_file():
            raise ObjektLagringFel(f"Lokal fil finns inte: {kalla}")
        lokal_storlek = kalla.stat().st_size
        faktisk_sha = sha256_fil(kalla)
        if sha256 is not None and faktisk_sha.lower() != sha256.lower():
            raise ObjektLagringFel(
                f"SHA-256-avvikelse före uppladdning av {kalla}: "
                f"förväntade {sha256}, fick {faktisk_sha}."
            )
        full = self._full_nyckel(nyckel)
        try:
            self._klient.upload_file(
                str(kalla),
                self._bucket,
                full,
                ExtraArgs={"Metadata": {"sha256": faktisk_sha}},
            )
            head = self._klient.head_object(Bucket=self._bucket, Key=full)
        except Exception as exc:  # noqa: BLE001
            raise ObjektLagringFel(f"S3-uppladdning misslyckades för {full!r}: {exc}") from exc

        fjarr_storlek = head.get("ContentLength") if isinstance(head, Mapping) else None
        if fjarr_storlek is not None and int(fjarr_storlek) != lokal_storlek:
            raise ObjektLagringFel(
                f"Storleksavvikelse efter S3-uppladdning av {full!r}: "
                f"lokalt {lokal_storlek} B, S3 {fjarr_storlek} B."
            )
        meta = head.get("Metadata", {}) if isinstance(head, Mapping) else {}
        if (
            isinstance(meta, Mapping)
            and "sha256" in meta
            and str(meta["sha256"]).lower() != faktisk_sha.lower()
        ):
            raise ObjektLagringFel(
                f"SHA-256-avvikelse i S3-metadata för {full!r}: "
                f"lokalt {faktisk_sha}, S3 {meta['sha256']}."
            )
        return faktisk_sha

    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        full = self._full_nyckel(nyckel)
        mal = Path(lokal_fil)
        mal.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_namn = tempfile.mkstemp(prefix=".floden_s3dl_", dir=str(mal.parent))
        tmp_path = Path(tmp_namn)
        try:
            os.close(fd)
            self._klient.download_file(self._bucket, full, str(tmp_path))
            os.replace(tmp_path, mal)
        except Exception as exc:  # noqa: BLE001
            tmp_path.unlink(missing_ok=True)
            raise ObjektLagringFel(f"S3-nedladdning misslyckades för {full!r}: {exc}") from exc
        return mal

    def lista_prefix(self, prefix: str = "") -> list[str]:
        full_prefix = self._full_prefix(prefix)
        resultat: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": full_prefix}
            if token is not None:
                kwargs["ContinuationToken"] = token
            try:
                svar = self._klient.list_objects_v2(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise ObjektLagringFel(
                    f"S3 list_objects_v2 misslyckades för {full_prefix!r}: {exc}"
                ) from exc
            for post in svar.get("Contents", []):
                k = str(post["Key"])
                if self._prefix and k.startswith(self._prefix):
                    k = k[len(self._prefix) :]
                resultat.append(k)
            if svar.get("IsTruncated") and svar.get("NextContinuationToken"):
                token = str(svar["NextContinuationToken"])
            else:
                break
        resultat.sort()
        return resultat

    def radera_prefix(self, prefix: str) -> int:
        ren_prefix = _normalisera_prefix(prefix)
        if not ren_prefix:
            raise ObjektLagringFel("Tomt prefix vägras vid radera_prefix (skydd mot helradering).")
        nycklar = self.lista_prefix(ren_prefix)
        if not nycklar:
            return 0
        raderade = 0
        for i in range(0, len(nycklar), 1000):
            batch = [{"Key": self._full_nyckel(k)} for k in nycklar[i : i + 1000]]
            try:
                self._klient.delete_objects(Bucket=self._bucket, Delete={"Objects": batch})
            except Exception as exc:  # noqa: BLE001
                raise ObjektLagringFel(
                    f"S3 delete_objects misslyckades för prefix {ren_prefix!r}: {exc}"
                ) from exc
            raderade += len(batch)
        return raderade

    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        if giltig_s <= 0:
            raise ObjektLagringFel(f"giltig_s måste vara > 0, fick {giltig_s}.")
        full = self._full_nyckel(nyckel)
        try:
            url = self._klient.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": full},
                ExpiresIn=int(giltig_s),
            )
        except Exception as exc:  # noqa: BLE001
            raise ObjektLagringFel(
                f"S3 generate_presigned_url misslyckades för {full!r}: {exc}"
            ) from exc
        return str(url)


def skapa_lagring(
    konfig: LagringKonfig | Mapping[str, Any] | None = None,
    *,
    miljo: Mapping[str, str] | None = None,
    klient: Any | None = None,
) -> ObjektLagring:
    """Skapar `LokalLagring` eller `S3Lagring` utifrån `LagringKonfig` eller konfig-dict."""
    lk = konfig if isinstance(konfig, LagringKonfig) else las_lagring_konfig(konfig, miljo=miljo)
    if lk.typ == "lokal":
        return LokalLagring(lk.rot)
    return S3Lagring(
        endpoint_url=lk.endpoint or "",
        bucket=lk.bucket or "",
        access_key=lk.access_key or "",
        secret_key=lk.secret_key or "",
        region=lk.region,
        prefix=lk.prefix,
        klient=klient,
    )
