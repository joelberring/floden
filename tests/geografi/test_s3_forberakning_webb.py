"""Tester för S3-läsning av förberäknade kommunrutor, lokal LRU-cache och webb-API.

Helt offline med `LokalLagring` och mockad S3-klient (exakt samma nyckelformat
`geografi/<kommun>/<rut_id>/...` som `src/karna/batch.py` + `geografi_nyckel` producerar).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from gemensamt.objektlagring import LokalLagring, ObjektLagring, S3Lagring, geografi_nyckel
from geografi.forberaknad_lager import (
    DataskyddFel,
    ForberaknadKommunLager,
    ForberaknadLagerFel,
    forberaknad_lager_fran_miljo,
    tolka_rut_bbox_3011,
)
from karna.batch import BatchKonfig, RutUtdata, kor_batch_rutor
from konto import Databas, KontoAdapter, KontoKonfig, Register
from konto.sessioner import Sessioner, kaknamn
from markmodell.rutnat import Ruta
from webb.api import FlodenApiApp
from webb.fragor import FragaTjanst, Korning
from webb.kartverktyg import FORMAT as KV_FORMAT
from webb.kartverktyg import KartverktygTjanst
from webb.projektkalla import Mall, Projektkalla


class _RaknandeLagring(ObjektLagring):
    """Tunn wrapper runt en `ObjektLagring` som räknar anrop för att verifiera cache-träffar."""

    def __init__(self, inre: ObjektLagring) -> None:
        self._inre = inre
        self.antal_finns = 0
        self.antal_ladda_ner = 0
        self.antal_lista = 0

    def finns(self, nyckel: str) -> bool:
        self.antal_finns += 1
        return self._inre.finns(nyckel)

    def ladda_upp_fil(
        self,
        lokal_fil: str | Path,
        nyckel: str,
        *,
        sha256: str | None = None,
    ) -> str:
        return self._inre.ladda_upp_fil(lokal_fil, nyckel, sha256=sha256)

    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        self.antal_ladda_ner += 1
        return self._inre.ladda_ner_fil(nyckel, lokal_fil)

    def radera_prefix(self, prefix: str) -> int:
        return self._inre.radera_prefix(prefix)

    def lista_prefix(self, prefix: str = "") -> list[str]:
        self.antal_lista += 1
        return self._inre.lista_prefix(prefix)

    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        return self._inre.presigned_url(nyckel, giltig_s=giltig_s)


def _skapa_forberaknad_ruta_via_batch(
    lagring: ObjektLagring,
    utdata_rot: Path,
    kommun: str,
    rutor: list[Ruta],
    *,
    dataklass: str = "oppen",
    fil_storlek_bytes: int = 128,
) -> None:
    """Kör `kor_batch_rutor` så nycklar och K0-manifest matchar `batch.py` exakt."""

    def _syntetisk_korare(
        ruta: Ruta, arbetsmapp: Path, _konfig: BatchKonfig, _plats: str
    ) -> RutUtdata:
        poster: list[FilPost] = []
        for namn, roll in (
            ("mark.tif", "mark"),
            ("sankgraf.parquet", "sankgraf"),
            ("max_djup.tif", "max_djup"),
        ):
            fil = arbetsmapp / namn
            fil.write_bytes(f"{kommun}:{ruta.namn}:{namn}:".encode() + b"x" * fil_storlek_bytes)
            poster.append(FilPost(roll=roll, fil=namn, sha256=sha256_fil(fil)))
        return RutUtdata(utdata_filer=tuple(poster), matvarden={"ruta": ruta.namn})

    konfig = BatchKonfig(
        moduler=("vatten2d",),
        utdata_rot=utdata_rot,
        enhet="cpu",
        dataklass=dataklass,  # type: ignore[arg-type]
        kommun=kommun,
        lagring_typ="s3",
    )
    status = kor_batch_rutor(
        rutor,
        konfig,
        rutkorare=_syntetisk_korare,
        objektlagring=lagring,
        miljo={},
        avbryt_vid_fel=True,
    )
    assert len(status.felade) == 0


def test_miljofabrik_returnerar_none_utan_s3_och_utan_boto3(tmp_path: Path) -> None:
    """Utan `FLODEN_S3_*` returneras None direkt (100 % frivilligt boto3)."""
    assert forberaknad_lager_fran_miljo(tmp_path / "cache", miljo={}) is None

    mock_klient = MagicMock()
    lager = forberaknad_lager_fran_miljo(
        tmp_path / "cache",
        miljo={
            "FLODEN_S3_TYP": "s3",
            "FLODEN_S3_ENDPOINT": "https://fsn1.your-objectstorage.com",
            "FLODEN_S3_BUCKET": "floden-oppen",
            "FLODEN_S3_ACCESS_KEY": "test-ak",
            "FLODEN_S3_SECRET_KEY": "test-sk",
        },
        klient=mock_klient,
    )
    assert lager is not None
    assert lager.kalla_typ == "s3"
    assert lager.cache_rot == tmp_path / "cache"


def test_s3_hamtning_lru_cache_traff_och_utbredning(tmp_path: Path) -> None:
    """Offline-flöde: batch → lagring → lista → hamta_ruta → cache-träff → utbredning."""
    lokal = LokalLagring(tmp_path / "s3_bucket")
    raknare = _RaknandeLagring(lokal)

    rutor_nacka = [
        Ruta(e_min_m=150000.0, n_min_m=6580000.0, upplosning_m=1.0),
        Ruta(e_min_m=150500.0, n_min_m=6580000.0, upplosning_m=1.0),
        Ruta(e_min_m=150000.0, n_min_m=6580500.0, upplosning_m=1.0),
        Ruta(e_min_m=150500.0, n_min_m=6580500.0, upplosning_m=1.0),
    ]
    rutor_stockholm = [
        Ruta(e_min_m=153000.0, n_min_m=6581000.0, upplosning_m=1.0),
    ]
    _skapa_forberaknad_ruta_via_batch(lokal, tmp_path / "batch_nacka", "nacka", rutor_nacka)
    _skapa_forberaknad_ruta_via_batch(lokal, tmp_path / "batch_sthlm", "stockholm", rutor_stockholm)

    cache_rot = tmp_path / "lru_cache"
    lager = ForberaknadKommunLager(raknare, cache_rot, max_gb=1.0)

    assert lager.lista_kommuner() == ["nacka", "stockholm"]
    assert lager.lista_rutor("nacka") == [
        "mm_150000_6580000",
        "mm_150000_6580500",
        "mm_150500_6580000",
        "mm_150500_6580500",
    ]
    assert lager.lista_rutor("stockholm") == ["mm_153000_6581000"]

    # 1. Första hämtningen går till lagringen och skriver atomiskt till lokal cache
    mapp1 = lager.hamta_ruta("nacka", "mm_150000_6580000")
    assert mapp1 == cache_rot / "geografi" / "nacka" / "mm_150000_6580000"
    for filnamn in ("mark.tif", "sankgraf.parquet", "max_djup.tif", "run.json"):
        assert (mapp1 / filnamn).is_file()
    assert raknare.antal_ladda_ner == 4

    # 2. Andra hämtningen är ren lokal cache-träff: 0 extra get/finns-anrop och snabb!
    dl_fore = raknare.antal_ladda_ner
    finns_fore = raknare.antal_finns
    gammal_ts = time.time() - 3600.0
    os.utime(mapp1 / "mark.tif", (gammal_ts, gammal_ts))

    t0 = time.perf_counter()
    mapp2 = lager.hamta_ruta("nacka", "mm_150000_6580000")
    latens_ms = (time.perf_counter() - t0) * 1000.0

    assert mapp2 == mapp1
    assert raknare.antal_ladda_ner == dl_fore
    assert raknare.antal_finns == finns_fore
    assert latens_ms < 50.0  # normalt < 1 ms
    # mtime uppdaterad för LRU-ordning
    assert (mapp1 / "mark.tif").stat().st_mtime > gammal_ts + 1000.0

    # 3. `hamta_for_utbredning`: bara de 2 västra rutorna överlappar avgränsningen
    assert tolka_rut_bbox_3011("mm_150000_6580000") == (150000.0, 6580000.0, 150500.0, 6580500.0)
    hamtade = lager.hamta_for_utbredning("nacka", (150100.0, 6580100.0, 150400.0, 6580700.0))
    assert [p.name for p in hamtade] == ["mm_150000_6580000", "mm_150000_6580500"]
    assert all((p / "run.json").is_file() for p in hamtade)
    # De två östra rutorna ska inte ha laddats ner
    assert not (cache_rot / "geografi" / "nacka" / "mm_150500_6580000").exists()
    assert not (cache_rot / "geografi" / "nacka" / "mm_150500_6580500").exists()


def test_dataskydd_avvisar_icke_oppen_data_i_manifest_och_nyckel(tmp_path: Path) -> None:
    """Dataskyddsspärr: `dataklass != 'oppen'` i manifest eller nyckel avvisas alltid."""
    lokal = LokalLagring(tmp_path / "s3_skydd")
    _skapa_forberaknad_ruta_via_batch(
        lokal,
        tmp_path / "batch_intern",
        "nacka",
        [Ruta(e_min_m=150000.0, n_min_m=6580000.0, upplosning_m=1.0)],
        dataklass="intern",
    )

    cache_rot = tmp_path / "cache"
    lager = ForberaknadKommunLager(lokal, cache_rot, max_gb=1.0)

    # Avvisas via `run.json` även om anroparen bara ber om `mark.tif`!
    with pytest.raises(DataskyddFel, match="intern"):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))
    assert not (cache_rot / "geografi" / "nacka" / "mm_150000_6580000" / "mark.tif").exists()

    # Avvisas via nyckelsegment
    with pytest.raises(DataskyddFel):
        lager.hamta_ruta("skyddad", "mm_150000_6580000")
    with pytest.raises(DataskyddFel):
        lager.lista_rutor("intern")

    # Saknad ruta ger tydligt ForberaknadLagerFel (inget tyst fel)
    with pytest.raises(ForberaknadLagerFel):
        lager.hamta_ruta("nacka", "mm_999000_6599000")


def test_lru_stadning_rensar_aldsta_rutan_vid_max_gb(tmp_path: Path) -> None:
    """LRU-städning sker automatiskt vid hämtning när cachen passerar `max_gb`."""
    lokal = LokalLagring(tmp_path / "s3_lru")
    r1 = Ruta(e_min_m=150000.0, n_min_m=6580000.0, upplosning_m=1.0)
    r2 = Ruta(e_min_m=150500.0, n_min_m=6580000.0, upplosning_m=1.0)
    _skapa_forberaknad_ruta_via_batch(
        lokal, tmp_path / "batch_lru", "nacka", [r1, r2], fil_storlek_bytes=4096
    )

    cache_rot = tmp_path / "cache_lru"
    # ~15 KB per ruta -> max_gb ~ 20 KB gör att r1 rensas när r2 hämtas
    lager = ForberaknadKommunLager(lokal, cache_rot, max_gb=20_000 / (1024**3))

    m1 = lager.hamta_ruta("nacka", r1.namn)
    gammal = time.time() - 7200.0
    for fil in m1.iterdir():
        os.utime(fil, (gammal, gammal))

    m2 = lager.hamta_ruta("nacka", r2.namn)
    assert (m2 / "run.json").is_file()
    # Äldsta rutan ska ha rensats av LRU-städningen
    assert not (m1 / "mark.tif").exists()


def _skapa_konto_med_session(tmp_path: Path) -> tuple[KontoAdapter, str]:
    """Skapar en aktiv `KontoAdapter` med en inloggad användare och returnerar (adapter, cookie)."""
    db_fil = tmp_path / "konto.sqlite"
    konf = KontoKonfig(
        inloggning="pa",
        databas=db_fil,
        offentlig_url="https://floden.test",
        cookie_secure=True,
    )
    adapter = KontoAdapter(konf)
    db = Databas(db_fil)
    reg = Register(db)
    konto_id = "konto-test-1"
    nu = int(time.time())
    with db.transaktion() as c:
        c.execute(
            "INSERT INTO konto (id, epost, epost_verifierad, namn, skapad, superadmin) "
            "VALUES (?, ?, 1, ?, ?, 0)",
            (konto_id, "test@kommun.se", "Test Användare", nu),
        )
    sess = Sessioner(reg, konf).skapa(konto_id)
    kaka = f"{kaknamn(True).session}={sess.token}"
    return adapter, kaka


def test_api_geografi_forberaknade_auth_och_svar(tmp_path: Path) -> None:
    """`GET /api/geografi/forberaknade` kräver inloggning och listar förberäknade kommuner."""
    # 1. Utan S3/lager konfigurerat (inloggning av)
    app_tom = FlodenApiApp(
        konfig_fil=tmp_path / "ej_finns.toml",
        testsvit_rot=tmp_path / "testsvit",
        forberaknad_lager=None,
    )
    # Tvinga inläsning med tom miljö så inte lokal miljövariabel påverkar testet
    app_tom._forberaknad_lager_inlast = True  # noqa: SLF001
    status, _, kropp = app_tom.hantera_forfragan("GET", "/api/geografi/forberaknade")
    assert status == 200
    assert json.loads(kropp) == {"s3_aktiv": False, "kommuner": []}

    # 2. Med förberäknade rutor + inloggning PÅ
    lokal = LokalLagring(tmp_path / "s3_api")
    _skapa_forberaknad_ruta_via_batch(
        lokal,
        tmp_path / "b_nacka",
        "nacka",
        [
            Ruta(e_min_m=150000.0, n_min_m=6580000.0, upplosning_m=1.0),
            Ruta(e_min_m=150500.0, n_min_m=6580000.0, upplosning_m=1.0),
        ],
    )
    _skapa_forberaknad_ruta_via_batch(
        lokal,
        tmp_path / "b_sthlm",
        "stockholm",
        [Ruta(e_min_m=153000.0, n_min_m=6581000.0, upplosning_m=1.0)],
    )
    lager = ForberaknadKommunLager(lokal, tmp_path / "api_cache", max_gb=1.0)
    konto, cookie = _skapa_konto_med_session(tmp_path)
    app = FlodenApiApp(
        konfig_fil=tmp_path / "ej_finns.toml",
        testsvit_rot=tmp_path / "testsvit",
        konto=konto,
        forberaknad_lager=lager,
    )

    # Oinloggad -> 401
    st_ut, _, _ = app.hantera_http("GET", "/api/geografi/forberaknade", {}, lambda: b"")
    assert st_ut == 401

    # Inloggad -> 200 med exakt förväntad JSON-struktur
    st_in, _, kropp_in = app.hantera_http(
        "GET", "/api/geografi/forberaknade", {"Cookie": cookie}, lambda: b""
    )
    assert st_in == 200
    assert json.loads(kropp_in) == {
        "s3_aktiv": True,
        "kommuner": [
            {"kommun": "nacka", "antal_rutor": 2},
            {"kommun": "stockholm", "antal_rutor": 1},
        ],
    }


def _skapa_minimal_kartverktyg(
    tmp_path: Path,
    *,
    forberaknad_lager: ForberaknadKommunLager | None = None,
    geografi_ref: str | None = "nacka-jarla@v1",
) -> tuple[KartverktygTjanst, FragaTjanst]:
    """Bygger en lättviktig `KartverktygTjanst` med mockad `kalla()` för snabba enhetstester."""
    fragor = FragaTjanst(
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    geomapp = tmp_path / "geografier"
    if geografi_ref is not None:
        geo_id, ver = geografi_ref.split("@", 1)
        mapp = geomapp / geo_id / ver
        mapp.mkdir(parents=True, exist_ok=True)
        (mapp / "manifest.json").write_text(
            json.dumps(
                {
                    "kontrakt": "floden-geografi/v1",
                    "id": geo_id,
                    "version": ver,
                    "kommun": "nacka",
                }
            ),
            encoding="utf-8",
        )
    kv = KartverktygTjanst(
        fragor,
        tmp_path / "kv",
        geomapp=geomapp,
        forberaknad_lager=forberaknad_lager,
    )

    # Stubbad `kalla` med en minimal ruta i Nacka
    stub_ruta = MagicMock()
    stub_ruta.x_min = 150000.0
    stub_ruta.y_min = 6580000.0
    stub_ruta.x_max = 150500.0
    stub_ruta.y_max = 6580500.0
    stub_ruta.mapp = tmp_path / "tom_ruta"
    stub_ruta.mapp.mkdir(exist_ok=True)

    stub_fraga = MagicMock()
    stub_fraga.analyser = ()
    stub_mall = Mall("vatten", "vatten", stub_fraga, tmp_path / "bas", (stub_ruta,), "fore")
    stub_kalla = Projektkalla(
        id="p1",
        slag="geografi",
        namn="Testprojekt Nacka",
        paahittat=False,
        oppen=True,
        licens="CC0",
        mallar={"vatten": stub_mall},
        geografi=geografi_ref,
        avgransning=(150050.0, 6580050.0, 150700.0, 6580450.0),
    )
    kv.kalla = lambda projekt: stub_kalla  # type: ignore[method-assign]
    kv._alla_byggnader = lambda kalla: []  # type: ignore[method-assign]
    kv._alla_brytlinjer = lambda kalla: []  # type: ignore[method-assign]
    return kv, fragor


def test_projektinfo_forberaknad_bas_s3_lokal_och_null(tmp_path: Path) -> None:
    """`projektinfo` inkluderar additivt `forberaknad_bas` när rutor finns, annars None."""
    # Utan lager -> None
    kv_utan, _ = _skapa_minimal_kartverktyg(tmp_path / "utan", forberaknad_lager=None)
    info_utan = kv_utan.projektinfo("p1")
    assert info_utan["forberaknad_bas"] is None

    # Med lokal lagring som täcker 2 rutor i Nacka
    lokal = LokalLagring(tmp_path / "s3_proj")
    _skapa_forberaknad_ruta_via_batch(
        lokal,
        tmp_path / "batch_proj",
        "nacka",
        [
            Ruta(e_min_m=150000.0, n_min_m=6580000.0, upplosning_m=1.0),
            Ruta(e_min_m=150500.0, n_min_m=6580000.0, upplosning_m=1.0),
            Ruta(e_min_m=152000.0, n_min_m=6582000.0, upplosning_m=1.0),  # utanför avgränsning
        ],
    )
    lager_lokal = ForberaknadKommunLager(lokal, tmp_path / "cache_proj", max_gb=1.0)
    kv_med, _ = _skapa_minimal_kartverktyg(tmp_path / "med", forberaknad_lager=lager_lokal)
    info_med = kv_med.projektinfo("p1")
    assert info_med["forberaknad_bas"] == {
        "kalla": "lokal",
        "kommun": "nacka",
        "antal_rutor": 2,
    }

    # Med S3Lagring (mockad boto3-klient) -> kalla == "s3"
    mock_s3_klient = MagicMock()
    mock_s3_klient.list_objects_v2.return_value = {
        "Contents": [
            {"Key": geografi_nyckel("nacka", "mm_150000_6580000", "run.json")},
        ],
        "IsTruncated": False,
    }
    s3_lagring = S3Lagring(
        endpoint_url="https://fsn1.your-objectstorage.com",
        bucket="floden-oppen",
        access_key="ak",
        secret_key="sk",
        klient=mock_s3_klient,
    )
    lager_s3 = ForberaknadKommunLager(s3_lagring, tmp_path / "cache_s3", max_gb=1.0)
    kv_s3, _ = _skapa_minimal_kartverktyg(tmp_path / "s3", forberaknad_lager=lager_s3)
    assert kv_s3.projektinfo("p1")["forberaknad_bas"] == {
        "kalla": "s3",
        "kommun": "nacka",
        "antal_rutor": 1,
    }


def test_lista_scenarier_har_korning_och_status_lage_idag_regn_text(tmp_path: Path) -> None:
    """`lista_scenarier` ger `har_korning: bool` och `status_lage_idag` ger `regn_text`."""
    kv, fragor = _skapa_minimal_kartverktyg(tmp_path)

    # Skriv två sparade scenarier: ett med klar körning och ett med pågående/ej klar körning
    scen_mapp = kv._scenariomapp("p1")  # noqa: SLF001
    scen_mapp.mkdir(parents=True, exist_ok=True)
    (scen_mapp / "forslag-klart.json").write_text(
        json.dumps(
            {
                "format": KV_FORMAT,
                "namn": "Förslag klart",
                "slug": "forslag-klart",
                "andrad": "2026-11-26T06:00:00+00:00",
                "andringar": [{"typ": "byggnad_ny"}],
                "korningar": {"vatten": {"korning_id": "k-klar-1", "ref": "r1"}},
            }
        ),
        encoding="utf-8",
    )
    (scen_mapp / "forslag-pagaende.json").write_text(
        json.dumps(
            {
                "format": KV_FORMAT,
                "namn": "Förslag pågående",
                "slug": "forslag-pagaende",
                "andrad": "2026-11-26T06:01:00+00:00",
                "andringar": [],
                "korningar": {"vatten": {"korning_id": "k-kor-2", "ref": "r2"}},
            }
        ),
        encoding="utf-8",
    )
    (scen_mapp / "forslag-nytt.json").write_text(
        json.dumps(
            {
                "format": KV_FORMAT,
                "namn": "Förslag nytt",
                "slug": "forslag-nytt",
                "andrad": "2026-11-26T06:02:00+00:00",
                "andringar": [],
                "korningar": {},
            }
        ),
        encoding="utf-8",
    )

    # Registrera körningarna i `fragor` (efter första inläsning så pågående inte avbryts)
    fragor._lasta_korningar()  # noqa: SLF001
    for kid, status in (("k-klar-1", "klar"), ("k-kor-2", "kor"), ("k-lage-1", "klar")):
        kmapp = fragor.korningsmapp / kid
        kmapp.mkdir(parents=True, exist_ok=True)
        k = Korning(
            korning_id=kid,
            ref="ref",
            fraga_id="fid",
            fraga_fil="f.json",
            mapp=kmapp,
            status=status,
            jobb_totalt=1,
            projekt_id="p1",
        )
        fragor._skriv_status(k)  # noqa: SLF001
        fragor._korningar[kid] = k  # noqa: SLF001

    lista = kv.lista_scenarier("p1")["scenarier"]
    per_slug = {s["slug"]: s for s in lista}
    assert per_slug["forslag-klart"]["har_korning"] is True
    assert per_slug["forslag-pagaende"]["har_korning"] is False
    assert per_slug["forslag-nytt"]["har_korning"] is False

    # Skriv `lage-idag.json` för projektet och stubba `fragor.resultat` med antaganden.regn
    lage_fil = kv._lage_fil("p1")  # noqa: SLF001
    lage_fil.parent.mkdir(parents=True, exist_ok=True)
    lage_fil.write_text(
        json.dumps(
            {
                "lage": "idag",
                "korningar": {
                    "vatten": {"korning_id": "k-lage-1", "ref": "lage-idag-p1-vatten"},
                    "dagsljus": {"korning_id": "k-kor-2", "ref": "lage-idag-p1-dagsljus"},
                },
            }
        ),
        encoding="utf-8",
    )

    def _mock_resultat(korning_id: str) -> dict[str, Any]:
        del korning_id
        return {
            "lager": [
                {
                    "id": "skyfall.fore.max_djup",
                    "analys": "skyfall",
                    "scenario": "fore",
                    "typ": "bas",
                    "primar": True,
                    "etikett": "Största vattendjup",
                }
            ],
            "varningar": [],
            "antaganden": {
                "regn": [
                    {
                        "analys": "skyfall",
                        "aterkomsttid_ar": 100.0,
                        "varaktighet_min": 60.0,
                        "klimatfaktor": 1.25,
                        "regn_mm": 68.5,
                    }
                ],
                "analyser": [],
                "vatten": [],
            },
        }

    fragor.resultat = _mock_resultat  # type: ignore[method-assign]
    lage_status = kv.status_lage_idag("p1")
    assert lage_status["lage"] == "idag"
    per_mall = {k["mall"]: k for k in lage_status["korningar"]}
    assert per_mall["vatten"]["status"]["status"] == "klar"
    assert per_mall["vatten"]["regn_text"] == "100-årsregn, 60 min, kf 1.25 (68.5 mm)"
    assert len(per_mall["vatten"]["vyer"]) == 1
    assert per_mall["dagsljus"]["status"]["status"] == "kor"
    assert per_mall["dagsljus"]["regn_text"] is None

    # Testa även enbart direkt regn_mm
    assert (
        KartverktygTjanst._regn_text_fran_resultat(  # noqa: SLF001
            {"antaganden": {"regn": [{"regn_mm": 60.0}]}}
        )
        == "60 mm"
    )
    assert KartverktygTjanst._regn_text_fran_resultat({"antaganden": {"regn": []}}) is None  # noqa: SLF001


def test_dataskydd_avvisar_korrupt_eller_toppniva_dataklass(tmp_path: Path) -> None:
    """Toppnivå `dataklass='skyddad'` i `run.json` avvisas också."""
    lokal = LokalLagring(tmp_path / "s3_top")
    tmp_run = tmp_path / "run_skyddad.json"
    m = skapa_manifest(modul="forberakna", parametrar={"dataklass": "skyddad"})
    skriv_manifest(m, tmp_run)
    lokal.ladda_upp_fil(tmp_run, geografi_nyckel("nacka", "mm_150000_6580000", "run.json"))

    lager = ForberaknadKommunLager(lokal, tmp_path / "cache_top", max_gb=1.0)
    with pytest.raises(DataskyddFel, match="skyddad"):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("run.json",))
