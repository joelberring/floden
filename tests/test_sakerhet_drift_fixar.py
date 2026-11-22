"""Regressionstester för säkerhets-, drift- och volymbalansfixar (del 3)."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from dynamik.tidsstegning import kor_dynamik_tidssteg
from gemensamt.koordinater import EPSG_INTERN
from konto.konfig import las_konto_konfig
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import GeoTransformInfo
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst
from webb.kartverktyg import KartverktygTjanst


def test_wms_ssrf_blockerar_privata_nat_och_ipv4_mappad_ipv6() -> None:
    """WMS-proxyn ska blockera RFC 1918-adresser och IPv4-mappad IPv6 (::ffff:127.0.0.1)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        fragor = FragaTjanst(
            fragemappar=[p], cachemapp=p / "cache", korningsmapp=p / "kor", rot=p
        )
        kt = KartverktygTjanst(fragor, p / "kv", tillat_loopback=False)
        for url in (
            "http://10.0.0.1/wms",
            "http://172.16.0.5/wms",
            "http://192.168.1.10/wms",
            "http://[::ffff:127.0.0.1]/wms",
            "http://[::ffff:169.254.169.254]/latest/meta-data/",
        ):
            with pytest.raises(FlodenWebbFel) as exc:
                kt._kontrollera_adress(url)
            assert exc.value.status_kod == 400


def test_kartverktyg_lista_scenarier_nekar_okant_projekt() -> None:
    """lista_scenarier ska validera projekt-id och neka path traversal."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        fragor = FragaTjanst(
            fragemappar=[p], cachemapp=p / "cache", korningsmapp=p / "kor", rot=p
        )
        kt = KartverktygTjanst(fragor, p / "kv")
        with pytest.raises(FlodenWebbFel) as exc:
            kt.lista_scenarier("../etc")
        assert exc.value.status_kod == 404


def test_konto_konfig_tom_miljovariabel_skriver_inte_over_toml() -> None:
    """Tom miljövariabel (som docker-compose.prod.yml) ska falla tillbaka på config.local.toml."""
    konf = las_konto_konfig(
        {
            "konto": {
                "inloggning": "pa",
                "offentlig_url": "https://floden.example.se",
                "leverantorer": {
                    "google": {
                        "aktiv": True,
                        "klient_id": "toml-id",
                        "klient_hemlighet": "toml-hemlighet",
                    }
                },
            }
        },
        miljo={
            "FLODEN_KONTO_GOOGLE_KLIENT_ID": "",
            "FLODEN_KONTO_GOOGLE_KLIENT_HEMLIGHET": "",
        },
    )
    assert konf.leverantorer["google"].klient_id == "toml-id"
    assert konf.leverantorer["google"].hemlighet == "toml-hemlighet"


def test_tidsstegning_volymbalans_exakt() -> None:
    """kor_dynamik_tidssteg ska fylla i regn_mark_m3/takvatten så volymbalans() är exakt."""
    z = np.full((6, 6), 10.0, dtype=np.float32)
    z[2:4, 2:4] = 9.5
    osak = np.zeros((6, 6), dtype=np.float32)
    markslag = np.ones((6, 6), dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=6,
        hojd=6,
        x_min=150000.0,
        y_max=6580006.0,
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        epsg=EPSG_INTERN,
        dtype="float32",
    )
    graf = bygg_sankgraf(z, osak, markslag, geo)
    res = kor_dynamik_tidssteg(graf, [0.0, 600.0], [36.0, 36.0], dt_s=10.0)
    bal = res.volymbalans()
    assert math.isclose(float(bal["regn_mark_m3"]), res.in_m3, rel_tol=1e-9)
    assert abs(float(bal["volymfel_rel"])) < 1e-9
