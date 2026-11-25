"""Tester för `gemensamt.objektlagring` (LokalLagring och mockad S3Lagring)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gemensamt.hash import sha256_fil
from gemensamt.objektlagring import (
    LagringKonfig,
    LokalLagring,
    ObjektLagringFel,
    S3Lagring,
    geografi_nyckel,
    las_lagring_konfig,
    projekt_nyckel,
    skapa_lagring,
    tmp_radata_prefix,
    tmp_skiss_prefix,
)


class _MockS3Fel(Exception):
    def __init__(self, code: str, status: int = 404) -> None:
        super().__init__(f"Mock S3 error: {code}")
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class MockS3Klient:
    """Enkel in-memory S3-klient för offline-tester utan boto3 eller nätverk."""

    def __init__(self, *, korrumpera_storlek: bool = False) -> None:
        self.objekt: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.livscykel: dict[str, Any] = {}
        self.korrumpera_storlek = korrumpera_storlek
        self.nedladdnings_fel: Exception | None = None

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict[str, Any] | None = None,
    ) -> None:
        data = Path(Filename).read_bytes()
        meta = dict((ExtraArgs or {}).get("Metadata", {}))
        self.objekt[(Bucket, Key)] = (data, meta)

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objekt:
            raise _MockS3Fel("404", 404)
        data, meta = self.objekt[(Bucket, Key)]
        storlek = len(data) + 1 if self.korrumpera_storlek else len(data)
        return {"ContentLength": storlek, "Metadata": dict(meta)}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        if self.nedladdnings_fel is not None:
            raise self.nedladdnings_fel
        if (Bucket, Key) not in self.objekt:
            raise _MockS3Fel("NoSuchKey", 404)
        data, _ = self.objekt[(Bucket, Key)]
        Path(Filename).write_bytes(data)

    def list_objects_v2(
        self,
        Bucket: str,
        Prefix: str = "",
        ContinuationToken: str | None = None,
    ) -> dict[str, Any]:
        nycklar = sorted(k for (b, k) in self.objekt if b == Bucket and k.startswith(Prefix))
        # Testa paginering med 2 poster per sida
        start = int(ContinuationToken) if ContinuationToken is not None else 0
        sida = nycklar[start : start + 2]
        nasta = start + len(sida)
        svar: dict[str, Any] = {
            "Contents": [{"Key": k} for k in sida],
            "IsTruncated": nasta < len(nycklar),
        }
        if nasta < len(nycklar):
            svar["NextContinuationToken"] = str(nasta)
        return svar

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        raderade = []
        for post in Delete.get("Objects", []):
            k = post["Key"]
            if (Bucket, k) in self.objekt:
                del self.objekt[(Bucket, k)]
                raderade.append({"Key": k})
        return {"Deleted": raderade}

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str:
        assert ClientMethod == "get_object"
        return (
            f"https://fsn1.your-objectstorage.com/{Params['Bucket']}/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}"
        )

    def put_bucket_lifecycle_configuration(
        self,
        Bucket: str,
        LifecycleConfiguration: dict[str, Any],
    ) -> dict[str, Any]:
        self.livscykel[Bucket] = LifecycleConfiguration
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def test_lokal_lagring_rundresa_och_atomisk_skrivning(tmp_path: Path) -> None:
    lagring = LokalLagring(tmp_path / "lagring")
    kalla = tmp_path / "run.json"
    kalla.write_text('{"status": "ok"}\n', encoding="utf-8")
    forv_sha = sha256_fil(kalla)
    nyckel = geografi_nyckel("goteborg", "mm_150000_6400000", "run.json")

    assert not lagring.finns(nyckel)
    sha_ut = lagring.ladda_upp_fil(kalla, nyckel, sha256=forv_sha)
    assert sha_ut == forv_sha
    assert lagring.finns(nyckel)

    # Felaktig sha256 vägras
    with pytest.raises(ObjektLagringFel, match="SHA-256"):
        lagring.ladda_upp_fil(kalla, nyckel, sha256="0" * 64)

    # Atomär nedladdning över befintlig fil
    mal = tmp_path / "nedladdad" / "run.json"
    mal.parent.mkdir(parents=True)
    mal.write_text("gammalt innehall", encoding="utf-8")
    lagring.ladda_ner_fil(nyckel, mal)
    assert mal.read_text(encoding="utf-8") == '{"status": "ok"}\n'
    assert not list(mal.parent.glob(".floden_tmp_*"))

    # presigned_url ger file://-URI
    url = lagring.presigned_url(nyckel, giltig_s=600)
    assert url.startswith("file://")
    assert url.endswith("/geografi/goteborg/mm_150000_6400000/run.json")

    # Lista och radera prefix
    lagring.ladda_upp_fil(kalla, geografi_nyckel("goteborg", "mm_150000_6400000", "mark.tif"))
    lagring.ladda_upp_fil(kalla, projekt_nyckel("p1", "rapport.pdf"))
    assert len(lagring.lista_prefix("geografi/goteborg/")) == 2
    raderade = lagring.radera_prefix("geografi/goteborg/")
    assert raderade == 2
    assert not lagring.finns(nyckel)
    assert lagring.finns("projekt/p1/rapport.pdf")


def test_s3_lagring_rundresa_mock(tmp_path: Path) -> None:
    mock = MockS3Klient()
    lagring = S3Lagring(
        endpoint_url="https://fsn1.your-objectstorage.com",
        bucket="floden-oppen-eu",
        access_key="test-ak",
        secret_key="test-sk",
        region="fsn1",
        klient=mock,
    )
    kalla = tmp_path / "mark.tif"
    kalla.write_bytes(b"COG-ZSTD-TESTDATA" * 100)
    forv_sha = sha256_fil(kalla)

    nycklar = [
        geografi_nyckel("stockholm", "mm_150000_6580000", "mark.tif"),
        geografi_nyckel("stockholm", "mm_150000_6580000", "sankgraf.parquet"),
        geografi_nyckel("stockholm", "mm_150000_6580000", "run.json"),
    ]
    assert not lagring.finns(nycklar[0])
    for n in nycklar:
        assert lagring.ladda_upp_fil(kalla, n, sha256=forv_sha) == forv_sha
        assert lagring.finns(n)

    # Paginerad listning (mock ger 2 per sida, vi har 3)
    assert lagring.lista_prefix("geografi/stockholm/") == sorted(nycklar)

    # Atomär nedladdning
    mal = tmp_path / "ut" / "mark.tif"
    lagring.ladda_ner_fil(nycklar[0], mal)
    assert mal.read_bytes() == kalla.read_bytes()

    # Om nedladdning kraschar ska befintlig målfil förbli orörd och inga tempfiler lämnas
    mal.write_bytes(b"BEFINTLIG")
    mock.nedladdnings_fel = RuntimeError("avbrott")
    with pytest.raises(ObjektLagringFel, match="nedladdning"):
        lagring.ladda_ner_fil(nycklar[0], mal)
    assert mal.read_bytes() == b"BEFINTLIG"
    assert not list(mal.parent.glob(".floden_s3dl_*"))
    mock.nedladdnings_fel = None

    # Presigned URL
    url = lagring.presigned_url(nycklar[0], giltig_s=900)
    assert "floden-oppen-eu/geografi/stockholm/mm_150000_6580000/mark.tif" in url
    assert "X-Amz-Expires=900" in url

    # Radera prefix
    raderade = lagring.radera_prefix("geografi/stockholm/")
    assert raderade == 3
    assert not lagring.finns(nycklar[0])
    assert lagring.lista_prefix("geografi/stockholm/") == []


def test_s3_lagring_upptacker_storleksavvikelse(tmp_path: Path) -> None:
    mock = MockS3Klient(korrumpera_storlek=True)
    lagring = S3Lagring(
        endpoint_url="https://fsn1.your-objectstorage.com",
        bucket="floden-oppen-eu",
        access_key="ak",
        secret_key="sk",
        klient=mock,
    )
    fil = tmp_path / "a.bin"
    fil.write_bytes(b"1234")
    with pytest.raises(ObjektLagringFel, match="Storleksavvikelse"):
        lagring.ladda_upp_fil(fil, "tmp/radata/j1/a.bin")


def test_sakerhet_path_traversal_och_tomt_prefix(tmp_path: Path) -> None:
    lagring = LokalLagring(tmp_path / "lagring")
    with pytest.raises(ObjektLagringFel, match="path traversal"):
        lagring.finns("../hemlig.txt")
    with pytest.raises(ObjektLagringFel, match="Tomt prefix"):
        lagring.radera_prefix("")
    assert tmp_radata_prefix("j1", "r1") == "tmp/radata/j1/r1/"
    assert tmp_skiss_prefix("s1") == "tmp/skiss/s1/"


def test_las_lagring_konfig_och_miljovariabler(tmp_path: Path) -> None:
    konfig = {
        "lagring": {
            "typ": "s3",
            "endpoint": "https://fsn1.your-objectstorage.com",
            "bucket": "floden-oppen-eu",
            "region": "fsn1",
        }
    }
    miljo = {
        "FLODEN_S3_ACCESS_KEY": "ak-env",
        "FLODEN_S3_SECRET_KEY": "sk-env",
    }
    lk = las_lagring_konfig(konfig, miljo=miljo)
    assert lk.typ == "s3"
    assert lk.bucket == "floden-oppen-eu"
    assert lk.access_key == "ak-env"
    assert lk.secret_key == "sk-env"

    # Standard utan konfiguration ger lokal lagring
    lk_lokal = las_lagring_konfig({}, miljo={})
    assert lk_lokal.typ == "lokal"
    obj = skapa_lagring(LagringKonfig(typ="lokal", rot=tmp_path / "lokal"))
    assert isinstance(obj, LokalLagring)
