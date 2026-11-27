# ruff: noqa: E501
"""Tester för PR-5: K38 fail-closed S3 K1-brygga, Colab S3-källa, GPU LRU-cache & MCP-ergonomi.

Täcker:
1. `geografi.s3_sync.synka_k1_till_s3`: K38 fail-closed (stängd kommun, `oppen_for_mcp=False`,
   `lokal_fil`, `bygg_stangd`, `oppenhet.bedom==False`, 0 läckage av stängda namn/sökvägar),
   `torrkor=True`, SHA-256-hopp vid omsynk och `max_rutor`.
2. `karna.batch.skapa_s3_k1_rutkorare` & `scripts/colab_forberakna.py --k1-kalla s3`:
   nedladdning av K1 från S3, `dataklass=="oppen"`- och SHA-256-verifiering, GPU/CPU-körning,
   uppladdning av `vatten2d/max_djup.tif` + `dagsljus/svf.tif` + `run.json`, och garanterad
   temp-rensning i `finally` (både vid lyckad körning och vid fel).
3. `geografi.forberaknad_lager`: `PRECOMPUTED_GPU_FILER`, `hamta_forberaknad_modul`,
   path-traversal-/symlänksskydd, `k1/`-isolering och bakåtkompatibilitet med `STANDARD_RUTFILER`.
4. `webb.kartverktyg`: `_hamta_forberaknad_raster()` (LRU-streaming + mjuk fallback + dataskydd)
   samt `Okänd analys '{v}'. Tillåtna: ...` (P2-1a) på båda valideringsställena.
5. `floden_mcp.utveckling`: `utv_lagring_synka_k1` (dubbellås + K38 fail-closed) samt
   `utv_geografi_mappa` / `sammanfatta_mappningsvarningar` (P2-1b, 1496 varningar -> kompakt JSON).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from starlette.testclient import TestClient
from tests.demovarld.test_demo import (
    Demo,
    _registrera_demo_mall,  # noqa: F401
    demo,  # noqa: F401
    demo_mall,  # noqa: F401
)
from tests.floden_mcp.test_mcp_http import BO, _initiera, anropa_verktyg
from tests.floden_mcp.test_mcp_utvecklingslage import UtvStack, _skapa_superadmin
from tests.floden_mcp.test_v1_mcp_sakerhet import _token
from tests.konto.conftest import (
    ANNA,
    Miljo,
    idp,  # noqa: F401
    klocka,  # noqa: F401
    miljo,  # noqa: F401
)
from tests.konto.test_projekt_per_anvandare_hjalp import setup_projekt

from floden_mcp.oppenhet import Oppenhet
from floden_mcp.utveckling import sammanfatta_mappningsvarningar
from gemensamt.hash import sha256_fil
from gemensamt.manifest import las_manifest
from gemensamt.objektlagring import LokalLagring, ObjektLagring, geografi_nyckel
from geografi.forberaknad_lager import (
    PRECOMPUTED_GPU_FILER,
    STANDARD_RUTFILER,
    DataskyddFel,
    ForberaknadKommunLager,
    ForberaknadLagerFel,
    hamta_forberaknad_modul,
)
from geografi.modell import (
    KONTRAKT_GEOGRAFI,
    LAGERMAPP,
    lagra,
    skriv_manifest,
    versionsmapp,
)
from geografi.s3_sync import (
    K1_MANIFEST_FIL,
    K38SyncNekad,
    k1_s3_nyckel,
    synka_k1_till_s3,
)
from karna.batch import (
    BatchFel,
    BatchKonfig,
    kor_batch_rutor,
    skapa_s3_k1_rutkorare,
)
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.rutnat import Ruta
from testsvit.k1_io import skriv_geotiff
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst
from webb.kartverktyg import KartverktygTjanst
from webb.projektkalla import Mall, Projektkalla

REPO = Path(__file__).resolve().parents[2]


class _RaknandeLagring(ObjektLagring):
    """Spion-wrapper runt `ObjektLagring` som räknar uppladdningar och nedladdningar."""

    def __init__(self, inre: ObjektLagring) -> None:
        self._inre = inre
        self.uppladdade_nycklar: list[str] = []
        self.nedladdade_nycklar: list[str] = []

    def finns(self, nyckel: str) -> bool:
        return self._inre.finns(nyckel)

    def ladda_upp_fil(
        self,
        lokal_fil: str | Path,
        nyckel: str,
        *,
        sha256: str | None = None,
    ) -> str:
        self.uppladdade_nycklar.append(nyckel)
        return self._inre.ladda_upp_fil(lokal_fil, nyckel, sha256=sha256)

    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        self.nedladdade_nycklar.append(nyckel)
        return self._inre.ladda_ner_fil(nyckel, lokal_fil)

    def radera_prefix(self, prefix: str) -> int:
        return self._inre.radera_prefix(prefix)

    def lista_prefix(self, prefix: str = "") -> list[str]:
        return self._inre.lista_prefix(prefix)

    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        return self._inre.presigned_url(nyckel, giltig_s=giltig_s)


def _skriv_k1_v2_ruta_med_recept(
    k1_rot: Path,
    ruta: Ruta,
    *,
    med_hus: bool = True,
    extra_recept_salt: str = "v1",
) -> Path:
    """Skriver en komplett, fysikaliskt giltig K1 v2-ruta (mark, terrang, markslag, osakerhet, byggnader, recept)."""
    e_min, n_min, e_max, n_max = ruta.yttre_bbox
    dx = ruta.upplosning_m
    mapp = k1_rot / ruta.namn
    mapp.mkdir(parents=True, exist_ok=True)

    rader, kolumner = ruta.form_rc
    yy, xx = np.mgrid[0:rader, 0:kolumner]
    # Liten sänka i mitten så vatten2d får tydligt positivt djup
    terrang = (10.0 + 0.02 * ((yy - (rader - 1) / 2.0) ** 2 + (xx - (kolumner - 1) / 2.0) ** 2)).astype(
        np.float32
    )
    mark = terrang.copy()
    osak = np.full(ruta.form_rc, 0.05, dtype=np.float32)
    slag = np.full(ruta.form_rc, 4, dtype=np.uint8)

    byggnader: tuple[ByggnadsPost, ...] = ()
    if med_hus:
        x0 = e_min + 0.2 * (e_max - e_min)
        y0 = n_min + 0.2 * (n_max - n_min)
        bredd = max(10.0, 0.2 * (e_max - e_min))
        hus = ByggnadsPost(
            byggnad_id="b_test_hus_01",
            polygon=((x0, y0), (x0 + bredd, y0), (x0 + bredd, y0 + bredd), (x0, y0 + bredd)),
            takhojd_rh2000=30.0,
            markhojd_rh2000=10.0,
            hojd_m=20.0,
            kalla="test",
            hojdkalla="kalla_tak",
            osakerhet_hojd_m=0.1,
        )
        byggnader = (hus,)
        mark[2:5, 2:5] = 30.0
        slag[2:5, 2:5] = 9

    skriv_geotiff(mapp / "mark.tif", mark, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "terrang.tif", terrang, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "markslag.tif", slag, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_byggnader_gpkg(mapp / "byggnader.gpkg", byggnader, (e_min, n_min, e_max, n_max))
    (mapp / "recept.json").write_text(
        json.dumps({"kontrakt": "K1/v2", "ruta": ruta.namn, "salt": extra_recept_salt}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return mapp


def _skapa_mappad_geografi(
    geo_rot: Path,
    geografi_id: str,
    version: str,
    rutor: list[Ruta],
    *,
    kommun: str = "goteborg",
    oppen_for_mcp: bool = True,
    bygg_stangd: bool = False,
    byggnadskalla: str = "filpaket",
    skriv_oppenhet_vmapp: bool | None = True,
    skriv_oppenhet_k1: bool | None = True,
    extra_recept_salt: str = "v1",
) -> tuple[str, Path, Path]:
    """Skapar en komplett K46-geografi med `_lager/<sha>` (k1_rutnat + radata) och `manifest.json`."""
    staging = geo_rot / f"_staging_{geografi_id}_{version}"
    k1_src = staging / "k1_rutnat"
    ra_src = staging / "radata"
    k1_src.mkdir(parents=True, exist_ok=True)
    ra_src.mkdir(parents=True, exist_ok=True)

    for r in rutor:
        _skriv_k1_v2_ruta_med_recept(k1_src, r, extra_recept_salt=extra_recept_salt)
    (ra_src / "hemlig_radata.txt").write_text("radata far aldrig laddas upp", encoding="utf-8")

    lager_k1 = lagra(geo_rot, k1_src)
    lager_ra = lagra(geo_rot, ra_src)
    (lager_ra.mapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = false\nlicens = "intern"\n',
        encoding="utf-8",
    )

    if skriv_oppenhet_k1 is True:
        (lager_k1.mapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
            encoding="utf-8",
        )
    elif skriv_oppenhet_k1 is False:
        (lager_k1.mapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = false\nlicens = "intern"\n',
            encoding="utf-8",
        )

    vmapp = versionsmapp(geo_rot, geografi_id, version)
    vmapp.mkdir(parents=True, exist_ok=True)
    if skriv_oppenhet_vmapp is True:
        (vmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
            encoding="utf-8",
        )
    elif skriv_oppenhet_vmapp is False:
        (vmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = false\nlicens = "intern"\n',
            encoding="utf-8",
        )

    manifest: dict[str, Any] = {
        "kontrakt": KONTRAKT_GEOGRAFI,
        "id": geografi_id,
        "version": version,
        "namn": f"Test {geografi_id}",
        "kommun": kommun,
        "karna_3011": [150000.0, 6580000.0, 150100.0, 6580100.0],
        "gransen_3011": [150000.0, 6580000.0, 150100.0, 6580100.0],
        "parametrar": {"byggnadskalla": byggnadskalla, "upplosning_m": rutor[0].upplosning_m},
        "kallor": {"byggnader": {"kalla": byggnadskalla, "stangd": bygg_stangd}},
        "lager": {
            "k1_rutnat": {
                "sha256": lager_k1.sha256,
                "rutor": sorted(r.namn for r in rutor),
                "filer": [{"fil": f, "sha256": s} for f, s in lager_k1.filer],
            },
            "radata": {
                "sha256": lager_ra.sha256,
                "filer": [{"fil": f, "sha256": s} for f, s in lager_ra.filer],
            },
        },
        "oppen_for_mcp": oppen_for_mcp,
        "varningar": [],
    }
    skriv_manifest(geo_rot, manifest)
    assert (geo_rot / LAGERMAPP / lager_k1.sha256).is_dir()
    return f"{geografi_id}@{version}", vmapp, lager_k1.mapp


# ------------------------------------------------------------------ 1. K38 fail-closed & SHA-256 skip


@pytest.mark.parametrize(
    ("fall_namn", "geo_id", "kwargs"),
    [
        (
            "stangd_kommun_stockholm",
            "stockholm-hemlig",
            {"kommun": "stockholm", "oppen_for_mcp": True, "bygg_stangd": False, "byggnadskalla": "wfs"},
        ),
        (
            "oppen_for_mcp_false",
            "gbg-ej-godkand",
            {"kommun": "goteborg", "oppen_for_mcp": False, "bygg_stangd": False, "byggnadskalla": "filpaket"},
        ),
        (
            "byggnadskalla_lokal_fil",
            "gbg-lokal-baskarta",
            {"kommun": "goteborg", "oppen_for_mcp": True, "bygg_stangd": False, "byggnadskalla": "lokal_fil"},
        ),
        (
            "bygg_stangd_true",
            "gbg-stangd-bygg",
            {"kommun": "goteborg", "oppen_for_mcp": True, "bygg_stangd": True, "byggnadskalla": "filpaket"},
        ),
        (
            "oppenhet_vmapp_false",
            "gbg-vmapp-stangd",
            {"kommun": "goteborg", "oppen_for_mcp": True, "skriv_oppenhet_vmapp": False},
        ),
        (
            "oppenhet_k1_saknas",
            "gbg-k1-utan-meta",
            {"kommun": "goteborg", "oppen_for_mcp": True, "skriv_oppenhet_k1": None},
        ),
    ],
)
def test_synka_k1_k38_fail_closed_nekar_och_lacker_inga_namn_eller_sokvagar(
    tmp_path: Path,
    fall_namn: str,
    geo_id: str,
    kwargs: dict[str, Any],
) -> None:
    """Samtliga K38-fel måste vägras fail-closed med 0 uppladdningar och 0 läckta namn/sökvägar."""
    geo_rot = tmp_path / "geo"
    ruta = Ruta(e_min_m=150000.0, n_min_m=6580000.0, storlek_m=100.0, overlapp_m=0.0, upplosning_m=5.0)
    ref, vmapp, k1_mapp = _skapa_mappad_geografi(geo_rot, geo_id, "v1", [ruta], **kwargs)
    lagring = _RaknandeLagring(LokalLagring(tmp_path / "s3_bucket"))
    opp = Oppenhet(REPO)

    with pytest.raises(K38SyncNekad) as exc_info:
        synka_k1_till_s3(
            ref,
            geo_rot,
            objektlagring=lagring,
            torrkor=False,
            oppenhet=opp,
            rot=REPO,
        )

    feltext = str(exc_info.value)
    assert "K38" in feltext, fall_namn
    assert lagring.uppladdade_nycklar == [], f"Inga filer får laddas upp för {fall_namn}"
    for kansligt in (geo_id, ref, "stockholm", str(tmp_path), str(vmapp), str(k1_mapp)):
        assert kansligt.lower() not in feltext.lower(), (fall_namn, kansligt, feltext)


def test_synka_k1_torrkor_sha256_hopp_och_max_rutor(tmp_path: Path) -> None:
    """`torrkor=True` laddar upp 0 filer; skarp synk laddar upp K1 v2; omsynk hoppar via SHA-256."""
    geo_rot = tmp_path / "geo"
    r1 = Ruta(e_min_m=150000.0, n_min_m=6580000.0, storlek_m=100.0, overlapp_m=0.0, upplosning_m=5.0)
    r2 = Ruta(e_min_m=150100.0, n_min_m=6580000.0, storlek_m=100.0, overlapp_m=0.0, upplosning_m=5.0)
    ref_v1, _, _ = _skapa_mappad_geografi(geo_rot, "goteborg-test", "v1", [r1, r2], kommun="goteborg")

    lokal_s3 = LokalLagring(tmp_path / "s3_bucket")
    spion = _RaknandeLagring(lokal_s3)
    opp = Oppenhet(REPO)

    # 1. Torrkörning (förval): 0 uppladdningar men full rapport
    res_torr = synka_k1_till_s3(ref_v1, geo_rot, objektlagring=spion, oppenhet=opp, rot=REPO)
    assert res_torr.torrkor is True
    assert res_torr.dataklass == "oppen"
    assert res_torr.antal_rutor_totalt == 2
    assert res_torr.antal_rutor_bearbetade == 2
    assert res_torr.antal_filer_uppladdade == 12  # 6 K1-filer × 2 rutor
    assert res_torr.antal_filer_hoppade == 0
    assert spion.uppladdade_nycklar == []

    # 2. max_rutor=1 begränsar antal rutor utan fel och flaggar trunkerad=True
    res_cap = synka_k1_till_s3(
        ref_v1, geo_rot, objektlagring=spion, torrkor=False, max_rutor=1, oppenhet=opp, rot=REPO
    )
    assert res_cap.antal_rutor_totalt == 2
    assert res_cap.antal_rutor_bearbetade == 1
    assert res_cap.trunkerad is True
    assert res_cap.antal_filer_uppladdade == 6
    assert len(spion.uppladdade_nycklar) == 7  # 6 K1-filer + k1_manifest.json sist
    assert spion.uppladdade_nycklar[-1].endswith(f"/k1/{K1_MANIFEST_FIL}")

    # Rådata laddas ALDRIG upp
    alla_nycklar = lokal_s3.lista_prefix("")
    assert all("radata" not in k and "hemlig_radata" not in k for k in alla_nycklar)

    # 3. Full skarp synk: r1 hoppas helt över via SHA-256, r2 laddas upp!
    spion.uppladdade_nycklar.clear()
    res_full = synka_k1_till_s3(
        ref_v1, geo_rot, objektlagring=spion, torrkor=False, oppenhet=opp, rot=REPO
    )
    assert res_full.antal_rutor_bearbetade == 2
    assert res_full.antal_filer_hoppade == 6
    assert res_full.antal_filer_uppladdade == 6
    assert res_full.rutor[0].hoppade == (
        "byggnader.gpkg",
        "mark.tif",
        "markslag.tif",
        "osakerhet.tif",
        "recept.json",
        "terrang.tif",
    )
    assert res_full.rutor[0].manifest_uppdaterad is False
    assert len(spion.uppladdade_nycklar) == 7  # bara r2

    # 4. Omsynk på oförändrad geografi -> 0 uppladdningar!
    spion.uppladdade_nycklar.clear()
    res_igen = synka_k1_till_s3(
        ref_v1, geo_rot, objektlagring=spion, torrkor=False, oppenhet=opp, rot=REPO
    )
    assert res_igen.antal_filer_uppladdade == 0
    assert res_igen.antal_filer_hoppade == 12
    assert spion.uppladdade_nycklar == []

    # 5. Ny version där bara `recept.json` ändrats -> enbart `recept.json` + manifest laddas upp!
    ref_v2, _, _ = _skapa_mappad_geografi(
        geo_rot,
        "goteborg-test",
        "v2",
        [r1],
        kommun="goteborg",
        extra_recept_salt="v2-andrad",
    )
    spion.uppladdade_nycklar.clear()
    res_v2 = synka_k1_till_s3(
        ref_v2, geo_rot, objektlagring=spion, torrkor=False, oppenhet=opp, rot=REPO
    )
    assert res_v2.antal_filer_hoppade == 5
    assert res_v2.antal_filer_uppladdade == 1
    assert res_v2.rutor[0].uppladdade == ("recept.json",)
    assert spion.uppladdade_nycklar == [
        k1_s3_nyckel("goteborg", r1.namn, "recept.json"),
        k1_s3_nyckel("goteborg", r1.namn, K1_MANIFEST_FIL),
    ]


# ------------------------------------------------------------------ 2. S3 K1 runner, cleanup & Colab CLI


def test_skapa_s3_k1_rutkorare_slutet_flode_verifiering_och_finally_rensning(tmp_path: Path) -> None:
    """Slutet flöde: synka_k1_till_s3 -> skapa_s3_k1_rutkorare -> vatten2d+dagsljus -> S3 + 0 tempfiler."""
    geo_rot = tmp_path / "geo"
    ruta = Ruta(e_min_m=150000.0, n_min_m=6580000.0, storlek_m=100.0, overlapp_m=0.0, upplosning_m=5.0)
    ref, _, _ = _skapa_mappad_geografi(geo_rot, "goteborg-test", "v1", [ruta], kommun="goteborg")

    lagring = LokalLagring(tmp_path / "s3_bucket")
    synka_k1_till_s3(ref, geo_rot, objektlagring=lagring, torrkor=False, oppenhet=Oppenhet(REPO), rot=REPO)

    # Före GPU-förberäkning ska `lista_rutor` INTE blanda ihop råa `k1/`-indata med förberäknade utdata
    lager_fore = ForberaknadKommunLager(lagring, tmp_path / "lru_fore", max_gb=1.0)
    assert lager_fore.lista_rutor("goteborg") == []

    k1_tmp_rot = tmp_path / "k1_dl_tmp"
    korare = skapa_s3_k1_rutkorare(
        lagring,
        "goteborg",
        azimutriktningar=24,
        dagsljus_radie_m=100.0,
        tmp_rot=k1_tmp_rot,
    )
    konfig = BatchKonfig(
        moduler=("vatten2d", "dagsljus"),
        utdata_rot=tmp_path / "batch_status",
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        rutstorlek_m=100.0,
        overlapp_m=0.0,
        upplosning_m=5.0,
        kommun="goteborg",
        lagring_typ="s3",
    )
    status = kor_batch_rutor(
        [ruta], konfig, rutkorare=korare, objektlagring=lagring, miljo={}, avbryt_vid_fel=True
    )
    assert status.klara == (ruta.namn,)
    assert status.felade == {}
    # Temp-katalogen MÅSTE vara helt tom efter lyckad körning
    assert list(k1_tmp_rot.iterdir()) == []

    # Verifiera uppladdade GPU-utdata + run.json i S3
    assert lagring.finns(geografi_nyckel("goteborg", ruta.namn, "vatten2d/max_djup.tif"))
    assert lagring.finns(geografi_nyckel("goteborg", ruta.namn, "dagsljus/svf.tif"))
    assert lagring.finns(geografi_nyckel("goteborg", ruta.namn, "run.json"))

    lokal_run = tmp_path / "downloaded_run.json"
    lagring.ladda_ner_fil(geografi_nyckel("goteborg", ruta.namn, "run.json"), lokal_run)
    man = las_manifest(lokal_run)
    assert man.matvarden["k1_kalla"] == "s3"
    assert man.matvarden["vatten2d_max_djup_m"] > 0.0
    assert 0.3 < man.matvarden["dagsljus_svf_medel"] < 1.0

    # Feltest A: korrupt SHA-256 -> BatchFel + temp-katalog rensad i finally!
    korrupt_fil = tmp_path / "korrupt_mark.tif"
    korrupt_fil.write_bytes(b"inte-en-giltig-tif")
    lagring.ladda_upp_fil(korrupt_fil, k1_s3_nyckel("goteborg", ruta.namn, "mark.tif"))
    konfig_ny = BatchKonfig(
        moduler=("dagsljus",),
        utdata_rot=tmp_path / "batch_status_err",
        enhet="cpu",
        dataklass="oppen",
        rutstorlek_m=100.0,
        overlapp_m=0.0,
        upplosning_m=5.0,
        kommun="goteborg",
        lagring_typ="s3",
    )
    with pytest.raises(BatchFel, match="SHA-256"):
        kor_batch_rutor(
            [ruta], konfig_ny, rutkorare=korare, objektlagring=lagring, miljo={}, avbryt_vid_fel=True
        )
    assert list(k1_tmp_rot.iterdir()) == []

    # Feltest B: dataklass != "oppen" i k1_manifest.json -> Dataskyddsspärr + temp rensad!
    stangt_man = tmp_path / "stangt_k1_manifest.json"
    stangt_man.write_text(
        json.dumps(
            {
                "kontrakt": "floden-k1-s3/v1",
                "dataklass": "intern",
                "kommun": "goteborg",
                "rut_id": ruta.namn,
                "filer": [{"fil": "mark.tif", "sha256": sha256_fil(korrupt_fil)}],
            }
        ),
        encoding="utf-8",
    )
    lagring.ladda_upp_fil(stangt_man, k1_s3_nyckel("goteborg", ruta.namn, K1_MANIFEST_FIL))
    with pytest.raises(BatchFel, match="Dataskyddsspärr"):
        kor_batch_rutor(
            [ruta], konfig_ny, rutkorare=korare, objektlagring=lagring, miljo={}, avbryt_vid_fel=True
        )
    assert list(k1_tmp_rot.iterdir()) == []

    # Feltest C: path traversal i k1_manifest.json -> BatchFel + temp rensad!
    trav_man = tmp_path / "trav_k1_manifest.json"
    trav_man.write_text(
        json.dumps(
            {
                "kontrakt": "floden-k1-s3/v1",
                "dataklass": "oppen",
                "kommun": "goteborg",
                "rut_id": ruta.namn,
                "filer": [{"fil": "../secret.tif", "sha256": "a" * 64}],
            }
        ),
        encoding="utf-8",
    )
    lagring.ladda_upp_fil(trav_man, k1_s3_nyckel("goteborg", ruta.namn, K1_MANIFEST_FIL))
    with pytest.raises(BatchFel, match="Otillåtet filnamn"):
        kor_batch_rutor(
            [ruta], konfig_ny, rutkorare=korare, objektlagring=lagring, miljo={}, avbryt_vid_fel=True
        )
    assert list(k1_tmp_rot.iterdir()) == []


def test_colab_forberakna_k1_kalla_s3_och_bakatkompatibilitet(tmp_path: Path) -> None:
    """`scripts/colab_forberakna.py` har `--k1-kalla lokal` som förval och kopplar `s3` end-to-end."""
    skript = REPO / "scripts" / "colab_forberakna.py"
    spec = importlib.util.spec_from_file_location("colab_forberakna_pr5", skript)
    assert spec is not None and spec.loader is not None
    colab_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(colab_mod)

    omrade = tmp_path / "omrade.geojson"
    omrade.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [150010.0, 6580010.0],
                        [150090.0, 6580010.0],
                        [150090.0, 6580090.0],
                        [150010.0, 6580090.0],
                        [150010.0, 6580010.0],
                    ]
                ],
            }
        ),
        encoding="utf-8",
    )

    # 1. Standardvärde: --k1-kalla == "lokal"
    args_std = colab_mod.bygg_parser().parse_args(["--omrade", str(omrade), "--utdata", str(tmp_path / "ut")])
    assert args_std.k1_kalla == "lokal"

    # 2. Synka K1 och kör colab_forberakna --k1-kalla s3 --lagring s3
    geo_rot = tmp_path / "geo"
    ruta = Ruta(e_min_m=150000.0, n_min_m=6580000.0, storlek_m=100.0, overlapp_m=0.0, upplosning_m=5.0)
    ref, _, _ = _skapa_mappad_geografi(geo_rot, "goteborg-test", "v1", [ruta], kommun="goteborg")
    lagring = LokalLagring(tmp_path / "s3_colab")
    synka_k1_till_s3(ref, geo_rot, objektlagring=lagring, torrkor=False, oppenhet=Oppenhet(REPO), rot=REPO)

    rc = colab_mod.main(
        [
            "--omrade",
            str(omrade),
            "--utdata",
            str(tmp_path / "colab_ut"),
            "--moduler",
            "vatten2d,dagsljus",
            "--enhet",
            "cpu",
            "--rutstorlek-m",
            "100",
            "--overlapp-m",
            "0",
            "--upplosning-m",
            "5",
            "--k1-kalla",
            "s3",
            "--lagring",
            "s3",
            "--kommun",
            "goteborg",
        ],
        objektlagring=lagring,
    )
    assert rc == 0
    assert lagring.finns(geografi_nyckel("goteborg", ruta.namn, "vatten2d/max_djup.tif"))
    assert lagring.finns(geografi_nyckel("goteborg", ruta.namn, "dagsljus/svf.tif"))
    assert lagring.finns(geografi_nyckel("goteborg", ruta.namn, "run.json"))

    # 3. Motstridiga flaggor (--k1-kalla s3 + --k1-rot) ger tydlig felkod 2
    rc_konflikt = colab_mod.main(
        [
            "--omrade",
            str(omrade),
            "--utdata",
            str(tmp_path / "colab_konflikt"),
            "--k1-kalla",
            "s3",
            "--k1-rot",
            str(tmp_path / "lokal_k1"),
        ],
        objektlagring=lagring,
    )
    assert rc_konflikt == 2


# ------------------------------------------------------------------ 3. forberaknad_lager & kartverktyg


def test_forberaknad_modul_lru_cache_och_path_traversal_skydd(tmp_path: Path) -> None:
    """`PRECOMPUTED_GPU_FILER` och `hamta_forberaknad_modul` läser nästlade GPU-utdata säkert."""
    assert PRECOMPUTED_GPU_FILER == ("vatten2d/max_djup.tif", "dagsljus/svf.tif", "run.json")
    assert STANDARD_RUTFILER == ("mark.tif", "sankgraf.parquet", "max_djup.tif", "run.json")

    geo_rot = tmp_path / "geo"
    ruta = Ruta(e_min_m=150000.0, n_min_m=6580000.0, storlek_m=500.0, overlapp_m=0.0, upplosning_m=25.0)
    ref, _, _ = _skapa_mappad_geografi(geo_rot, "goteborg-test", "v1", [ruta], kommun="goteborg")
    lokal_s3 = LokalLagring(tmp_path / "s3_gpu")
    synka_k1_till_s3(ref, geo_rot, objektlagring=lokal_s3, torrkor=False, oppenhet=Oppenhet(REPO), rot=REPO)

    korare = skapa_s3_k1_rutkorare(lokal_s3, "goteborg", azimutriktningar=18, dagsljus_radie_m=100.0)
    konfig = BatchKonfig(
        moduler=("vatten2d", "dagsljus"),
        utdata_rot=tmp_path / "batch_ut",
        enhet="cpu",
        dataklass="oppen",
        rutstorlek_m=500.0,
        overlapp_m=0.0,
        upplosning_m=25.0,
        kommun="goteborg",
        lagring_typ="s3",
    )
    kor_batch_rutor([ruta], konfig, rutkorare=korare, objektlagring=lokal_s3, miljo={}, avbryt_vid_fel=True)

    spion = _RaknandeLagring(lokal_s3)
    cache_rot = tmp_path / "lru_cache"
    lager = ForberaknadKommunLager(spion, cache_rot, max_gb=1.0)

    # 1. Hämta nästlad vatten2d/max_djup.tif via metod + modulnivåfunktion
    fil_v = lager.hamta_forberaknad_modul("goteborg", ruta.namn, "vatten2d")
    assert fil_v == cache_rot / "geografi" / "goteborg" / ruta.namn / "vatten2d" / "max_djup.tif"
    assert fil_v.is_file()
    dl_efter_forsta = len(spion.nedladdade_nycklar)
    assert dl_efter_forsta == 2  # run.json + vatten2d/max_djup.tif

    # Andra anropet via modulnivåfunktionen träffar lokal LRU-cache -> 0 nya S3-nedladdningar!
    fil_v2 = hamta_forberaknad_modul("goteborg", ruta.namn, "vatten2d", lager=lager)
    assert fil_v2 == fil_v
    assert len(spion.nedladdade_nycklar) == dl_efter_forsta

    # Hämta dagsljus/svf.tif
    fil_d = lager.hamta_forberaknad_modul("goteborg", ruta.namn, "dagsljus")
    assert fil_d == cache_rot / "geografi" / "goteborg" / ruta.namn / "dagsljus" / "svf.tif"
    assert fil_d.is_file()

    # 2. Path-traversal & okända moduler avvisas alltid!
    for bad_kommun, bad_ruta, bad_modul, bad_fil in (
        ("../etc", ruta.namn, "vatten2d", None),
        ("goteborg", "../other", "vatten2d", None),
        ("goteborg", ruta.namn, "../vatten2d", None),
        ("goteborg", ruta.namn, "k1", None),
        ("goteborg", ruta.namn, "vatten2d", "../run.json"),
        ("goteborg", ruta.namn, "vatten2d", "sub/max_djup.tif"),
        ("goteborg", ruta.namn, "vatten2d", "max_djup.tif\x00.png"),
    ):
        with pytest.raises(ForberaknadLagerFel):
            lager.hamta_forberaknad_modul(bad_kommun, bad_ruta, bad_modul, filnamn=bad_fil)

    # 3. Dataskydd: icke-öppen kommun avvisas med DataskyddFel
    with pytest.raises(DataskyddFel):
        lager.hamta_forberaknad_modul("intern", ruta.namn, "vatten2d")

    # 4. Kartverktyg._hamta_forberaknad_raster() streamar via LRU-cache och faller tillbaka mjukt
    fragor = FragaTjanst(
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    kv = KartverktygTjanst(fragor, tmp_path / "kv", geomapp=geo_rot, forberaknad_lager=lager)
    stub_fraga = MagicMock()
    stub_analys_v = MagicMock(id="skyfall")
    stub_analys_d = MagicMock(id="sol")
    stub_fraga.analyser = (stub_analys_v,)
    stub_fraga_d = MagicMock()
    stub_fraga_d.analyser = (stub_analys_d,)
    stub_ruta = MagicMock(x_min=150000.0, y_min=6580000.0, x_max=150500.0, y_max=6580500.0, mapp=tmp_path)
    mall_v = Mall("vatten", "vatten", stub_fraga, tmp_path / "bas", (stub_ruta,), "fore")
    mall_d = Mall("dagsljus", "dagsljus", stub_fraga_d, tmp_path / "bas", (stub_ruta,), "fore")
    kalla = Projektkalla(
        id="p-gbg",
        slag="geografi",
        namn="Göteborg test",
        paahittat=False,
        oppen=True,
        licens="CC0-1.0",
        mallar={"vatten": mall_v, "dagsljus": mall_d},
        geografi=ref,
        avgransning=(150050.0, 6580050.0, 150450.0, 6580450.0),
    )

    vatten_rastrer = kv._hamta_forberaknad_raster(kalla, "vatten2d")  # noqa: SLF001
    assert len(vatten_rastrer) == 1
    arr_v, meta_v = vatten_rastrer[0]
    assert arr_v.shape == ruta.form_rc
    assert float(np.nanmax(arr_v)) > 0.0
    assert meta_v["kommun"] == "goteborg"
    assert meta_v["rut_id"] == ruta.namn
    assert meta_v["modul"] == "vatten2d"
    assert meta_v["kalla"] == "lokal"

    dagsljus_rastrer = kv._hamta_forberaknad_raster(kalla, "dagsljus")  # noqa: SLF001
    assert len(dagsljus_rastrer) == 1
    arr_d, meta_d = dagsljus_rastrer[0]
    assert arr_d.shape == ruta.form_rc
    assert float(np.nanmin(arr_d)) < 0.9

    # Mjuk fallback när lagret saknas eller modulen inte finns
    kv_utan = KartverktygTjanst(fragor, tmp_path / "kv2", geomapp=geo_rot, forberaknad_lager=None)
    assert kv_utan._hamta_forberaknad_raster(kalla, "vatten2d") == []  # noqa: SLF001
    assert kv._hamta_forberaknad_raster(kalla, "okand_modul") == []  # noqa: SLF001

    # 5. P2-1a: `Okänd analys` inkluderar sorterad `Tillåtna: ...` vid BÅDE _skriv_fragor och _skriv_lage_fragor!
    kv.kalla = lambda projekt: kalla  # type: ignore[method-assign]
    kv._las = lambda projekt, slug: {"namn": "Test", "andringar": []}  # type: ignore[method-assign]
    with pytest.raises(FlodenWebbFel) as exc_scen:
        kv._skriv_fragor("p-gbg", "test-slug", ["skyfall"])  # noqa: SLF001
    assert exc_scen.value.status_kod == 400
    assert "Okänd analys 'skyfall'." in str(exc_scen.value)
    assert "Tillåtna: dagsljus.sol, vatten.skyfall." in str(exc_scen.value)

    with pytest.raises(FlodenWebbFel) as exc_lage:
        kv._skriv_lage_fragor("p-gbg", ["skyfall"])  # noqa: SLF001
    assert exc_lage.value.status_kod == 400
    assert "Okänd analys 'skyfall'." in str(exc_lage.value)
    assert "Tillåtna: dagsljus.sol, vatten.skyfall." in str(exc_lage.value)


# ------------------------------------------------------------------ 4. MCP utv_lagring_synka_k1 & P2-1b


def test_sammanfatta_mappningsvarningar_kompakt_1496_rader() -> None:
    """P2-1b: 1 496 repetitiva byggnadsvarningar komprimeras per kategori med antal & toppexempel."""
    raa: list[str] = []
    for i in range(900):
        raa.append(
            f"mm_-210000_6413500: b_{i:016x}: källans takhöjd ligger -{0.1 + i * 0.001:.2f} m över marken; ignorerad"
        )
    for i in range(450):
        raa.append(
            f"mm_-210000_6413500: b_{i + 1000:016x}: ingen cellcentrum i byggnaden; markhöjd från närmaste cell"
        )
    for i in range(145):
        raa.append(
            f"mm_-210000_6413500: b_{i + 2000:016x}: ingen markhöjd under eller kring byggnaden; utelämnad"
        )
    raa.append("Strandlinjer saknas i den yttre bufferten.")
    assert len(raa) == 1496

    ra_json_bytes = len(json.dumps(raa, ensure_ascii=False).encode("utf-8"))
    assert ra_json_bytes > 80_000

    sam = sammanfatta_mappningsvarningar(raa)
    assert sam["varningar_totalt"] == 1496
    per_kat = {p["kategori"]: p for p in sam["varningar_sammanfattning"]}
    assert per_kat["byggnad_ogiltig_kalltakhojd"]["antal"] == 900
    assert len(per_kat["byggnad_ogiltig_kalltakhojd"]["exempel"]) == 3
    assert per_kat["byggnad_inget_cellcentrum"]["antal"] == 450
    assert len(per_kat["byggnad_inget_cellcentrum"]["exempel"]) == 3
    assert per_kat["byggnad_saknar_markhojd"]["antal"] == 145
    assert len(per_kat["byggnad_saknar_markhojd"]["exempel"]) == 3
    # Unik varning finns kvar oförändrad i `varningar`
    assert "Strandlinjer saknas i den yttre bufferten." in sam["varningar"]
    # Kompakt JSON (< 4 KB i stället för > 80 KB!)
    kompakt_bytes = len(json.dumps(sam, ensure_ascii=False).encode("utf-8"))
    assert kompakt_bytes < 4_000


def test_mcp_utv_lagring_synka_k1_dubbellas_k38_och_utv_geografi_mappa_varningssummering(
    demo: Demo,  # noqa: F811
    miljo: Miljo,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifierar dubbellås + K38 fail-closed för `utv_lagring_synka_k1` och P2-1b i `utv_geografi_mappa`."""
    setup_projekt(miljo)
    ta, tb = _token(miljo, ANNA), _token(miljo, BO)
    _, ts = _skapa_superadmin(miljo)

    lagring_rot = tmp_path / "objektlagring"
    env_map = {
        "FLODEN_MCP_UTVECKLINGSLAGE": "1",
        "FLODEN_LAGRING_ROT": str(lagring_rot),
    }
    stack = UtvStack(demo, miljo, tmp_path / "stack", env=env_map)

    geo_rot = stack.mcp.app.projekt_skapa.geomapp
    ruta = Ruta(e_min_m=150000.0, n_min_m=6580000.0, storlek_m=100.0, overlapp_m=0.0, upplosning_m=5.0)
    ref_gbg, _, _ = _skapa_mappad_geografi(
        geo_rot,
        "goteborg-test",
        "v1",
        [ruta],
        kommun="goteborg",
        extra_recept_salt="goteborg-v1",
    )
    ref_sthlm, _, _ = _skapa_mappad_geografi(
        geo_rot,
        "stockholm-test",
        "v1",
        [ruta],
        kommun="stockholm",
        oppen_for_mcp=False,
        bygg_stangd=True,
        byggnadskalla="lokal_fil",
        skriv_oppenhet_vmapp=False,
        skriv_oppenhet_k1=False,
        extra_recept_salt="stockholm-v1",
    )

    # Stubba mappa_geografi för att returnera 1 496 repetitiva byggnadsvarningar
    from geografi import mappa as geo_mappa_mod

    många_varningar = [
        f"mm_-210000_6413500: b_{i:016x}: källans takhöjd ligger -0.50 m över marken; ignorerad"
        for i in range(1496)
    ]

    def _falsk_mappa(kommun: Any, geografi_id: str, **kwargs: Any) -> geo_mappa_mod.MappaResultat:
        return geo_mappa_mod.MappaResultat(
            ref=f"{geografi_id}@{kwargs.get('version', 'v1')}",
            manifest_fil=tmp_path / "manifest.json",
            manifest_sha256="b" * 64,
            nytt=True,
            varningar=många_varningar,
            oppen_for_mcp=True,
        )

    monkeypatch.setattr(geo_mappa_mod, "mappa_geografi", _falsk_mappa)

    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        sess_anna = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        sess_bo = _initiera(klient, {"Authorization": f"Bearer {tb}"})
        sess_sup = _initiera(klient, {"Authorization": f"Bearer {ts}"})

        # 1. Dubbellås: icke-superadmin (lasare/utredare) får 403
        for sess in (sess_anna, sess_bo):
            fel, text, _ = anropa_verktyg(
                klient, sess, "utv_lagring_synka_k1", {"geografi_ref": ref_gbg}
            )
            assert fel and "403" in text and "superadmin" in text.lower()

        # 2. K38 fail-closed: stängd stockholm-geografi nekas utan att läcka 'stockholm'
        fel_sthlm, text_sthlm, _ = anropa_verktyg(
            klient,
            sess_sup,
            "utv_lagring_synka_k1",
            {"geografi_ref": ref_sthlm, "torrkor": False},
        )
        assert fel_sthlm and "K38" in text_sthlm
        assert "stockholm" not in text_sthlm.lower()
        assert not (lagring_rot / "geografi" / "stockholm").exists()

        # 3. Öppen Göteborg: torrkörning (förval) laddar upp 0 filer
        fel_torr, text_torr, data_torr = anropa_verktyg(
            klient, sess_sup, "utv_lagring_synka_k1", {"geografi_ref": ref_gbg}
        )
        assert not fel_torr, text_torr
        assert data_torr["torrkor"] is True
        assert data_torr["dataklass"] == "oppen"
        assert data_torr["antal_filer_uppladdade"] == 6
        assert not (lagring_rot / "geografi" / "goteborg").exists()

        # 4. Skarp synk (torrkor=False) + omsynk (SHA-256-hopp)
        fel_ok, text_ok, data_ok = anropa_verktyg(
            klient,
            sess_sup,
            "utv_lagring_synka_k1",
            {"geografi_ref": ref_gbg, "torrkor": False, "max_rutor": 16},
        )
        assert not fel_ok, text_ok
        assert data_ok["torrkor"] is False
        assert data_ok["antal_filer_uppladdade"] == 6
        assert (lagring_rot / "geografi" / "goteborg" / ruta.namn / "k1" / K1_MANIFEST_FIL).is_file()

        fel_ok2, text_ok2, data_ok2 = anropa_verktyg(
            klient,
            sess_sup,
            "utv_lagring_synka_k1",
            {"geografi_ref": ref_gbg, "torrkor": False},
        )
        assert not fel_ok2, text_ok2
        assert data_ok2["antal_filer_uppladdade"] == 0
        assert data_ok2["antal_filer_hoppade"] == 6

        # 5. P2-1b: utv_geografi_mappa returnerar kompakt sammanfattning i stället för 1496 råa rader
        fel_m, text_m, data_m = anropa_verktyg(
            klient,
            sess_sup,
            "utv_geografi_mappa",
            {"geografi": "goteborg-test", "version": "v9", "villkor_kontrollerade": True},
        )
        assert not fel_m, text_m
        assert data_m["varningar_totalt"] == 1496
        assert len(data_m["varningar"]) == 1
        assert data_m["varningar_sammanfattning"] == [
            {
                "kategori": "byggnad_ogiltig_kalltakhojd",
                "antal": 1496,
                "exempel": många_varningar[:3],
            }
        ]
        assert len(json.dumps(data_m, ensure_ascii=False).encode("utf-8")) < 3_000
