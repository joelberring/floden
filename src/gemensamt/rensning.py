"""Automatisk rensning av temporär data i lager 1 (batch) och lager 2 (S3 Lifecycle Policy).

Lager 1: Omedelbar rensning av temporär rutkatalog (`temporar_rutkatalog`) via `finally`-block
så att rå-LAZ och mellanfiler alltid försvinner efter uppladdning (eller vid fel).
Lager 2: Server-side TTL på Hetzner Object Storage / S3-kompatibel lagring via
`put_bucket_lifecycle_configuration` (`tmp/radata/`, `tmp/skiss/`, ofullständiga multiparts).
Lager 3: Lokal LRU-cache och tidsstädning på VPS:en finns i `gemensamt.lokal_cache`.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gemensamt.objektlagring import ObjektLagringFel, S3Lagring

STANDARD_RADATA_DAGAR = 2
STANDARD_SKISS_DAGAR = 14
STANDARD_MULTIPART_DAGAR = 1


class RensningFel(Exception):
    """Fel vid konfiguration eller körning av automatisk rensning."""


@contextmanager
def temporar_rutkatalog(
    ruta_namn: str,
    *,
    rot: str | Path | None = None,
    behall: bool = False,
) -> Iterator[Path]:
    """Isolerad temporär rutkatalog som alltid rensas i `finally` (om `behall=False`).

    Används i lager 1 för att packa upp rå-LAZ och grundkarta per 500×500 m-ruta och radera
    temporär data så snart resultatet är uppladdat och verifierat (eller om körningen avbryts).
    """
    bas = str(rot) if rot is not None else None
    if bas is not None:
        Path(bas).mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"floden_ruta_{ruta_namn}_", dir=bas))
    try:
        yield tmp_dir
    finally:
        if not behall:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def skapa_livscykelregler(
    *,
    radata_dagar: int = STANDARD_RADATA_DAGAR,
    skiss_dagar: int = STANDARD_SKISS_DAGAR,
    multipart_dagar: int = STANDARD_MULTIPART_DAGAR,
) -> dict[str, Any]:
    """Bygger en S3 LifecycleConfiguration-struktur för Hetzner Object Storage / R2.

    Regler:
    - `tmp/radata/` -> raderas automatiskt efter `radata_dagar` (säkerhetsnät vid krasch).
    - `tmp/skiss/` -> raderas automatiskt efter `skiss_dagar`.
    - `AbortIncompleteMultipartUpload` -> rensar avbrutna uppladdningar efter `multipart_dagar`.
    """
    if radata_dagar < 1:
        raise RensningFel(f"radata_dagar måste vara >= 1, fick {radata_dagar}.")
    if skiss_dagar < 1:
        raise RensningFel(f"skiss_dagar måste vara >= 1, fick {skiss_dagar}.")
    if multipart_dagar < 1:
        raise RensningFel(f"multipart_dagar måste vara >= 1, fick {multipart_dagar}.")

    return {
        "Rules": [
            {
                "ID": "floden-tmp-radata-ttl",
                "Status": "Enabled",
                "Filter": {"Prefix": "tmp/radata/"},
                "Expiration": {"Days": int(radata_dagar)},
            },
            {
                "ID": "floden-tmp-skiss-ttl",
                "Status": "Enabled",
                "Filter": {"Prefix": "tmp/skiss/"},
                "Expiration": {"Days": int(skiss_dagar)},
            },
            {
                "ID": "floden-abort-incomplete-multipart",
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "AbortIncompleteMultipartUpload": {
                    "DaysAfterInitiation": int(multipart_dagar),
                },
            },
        ]
    }


def installera_livscykelregler(
    lagring: S3Lagring,
    *,
    radata_dagar: int = STANDARD_RADATA_DAGAR,
    skiss_dagar: int = STANDARD_SKISS_DAGAR,
    multipart_dagar: int = STANDARD_MULTIPART_DAGAR,
) -> dict[str, Any]:
    """Installerar S3 Lifecycle Policy på mål-bucketen och returnerar installerade regler."""
    regler = skapa_livscykelregler(
        radata_dagar=radata_dagar,
        skiss_dagar=skiss_dagar,
        multipart_dagar=multipart_dagar,
    )
    try:
        lagring.klient.put_bucket_lifecycle_configuration(
            Bucket=lagring.bucket,
            LifecycleConfiguration=regler,
        )
    except Exception as exc:  # noqa: BLE001
        raise ObjektLagringFel(
            f"Kunde inte installera S3-livscykelregler på bucket '{lagring.bucket}': {exc}"
        ) from exc
    return regler
