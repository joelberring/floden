"""Tester för före/efter-simulering, fördröjningsvolym och alternativ A vs B (T04)."""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from dynamik.nodmodell import EntrePunkt
from gemensamt.manifest import KONTRAKT_K0, las_manifest
from lapp.fore_efter import formatera_alternativ_tabell, jamfor_alternativ, kor_fore_efter
from lapp.kontrakt import (
    HojdYta,
    LappByggnad,
    LappGata,
    LappMagasin,
    ProjektLapp,
    las_gatusektioner_fran_konfig,
)
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo

_REPO_ROT = Path(__file__).resolve().parents[2]


def _skapa_kvarter_och_grannsanka() -> tuple[
    NDArray[np.float32],
    NDArray[np.float32],
    NDArray[np.uint8],
    GeoTransformInfo,
    EntrePunkt,
]:
    """Bygger syntetiskt provområde (32 m x 40 m = 128 x 160 celler à 0,25 m).

    - Västra delen (kol 0..55): hydrologiskt separat oberoende sänka (återanvänds vid lokal
      omräkning).
    - Östra delen (kol 64..159):
      * Norra sluttningen (rad 6..68): projektområde (kvarter) med en naturlig svacka som
        i basfallet lagrar regnvatten på tomten.
      * Södra delen (rad 76..120, kol 84..140): befintlig grannsänka nedströms projektet
        med en grannentré som ligger precis under gränsdjupet 0,10 m i basfallet.
    """
    hojd, bredd = 128, 160
    geo = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6580032.0,
        pixelstorlek_x=0.25,
        pixelstorlek_y=0.25,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )

    # Grundsluttning från norr (14,5 m) mot söder (12,0 m)
    rader_idx = np.arange(hojd, dtype=np.float64)[:, None]
    z64 = 14.5 - 2.5 * (rader_idx / float(hojd - 1))
    z = np.broadcast_to(z64, (hojd, bredd)).astype(np.float32).copy()

    # Sänk ytterranden runt om så randutlopp alltid är väldefinierat
    z[0, :] = 11.5
    z[-1, :] = 11.5
    z[:, 0] = 11.5
    z[:, -1] = 11.5

    # Vattendelare mellan väst och öst (kol 56..63)
    z[1:-1, 56:64] = 16.0

    # Oberoende västra sänkan (rad 36..88, kol 14..44)
    z[36:88, 14:44] = 12.2
    z[42:82, 20:38] = 11.8

    # Östra projektområdet (norr, rad 12..64, kol 74..148):
    # Skapa en befintlig grund tomt-svacka (rad 18..54, kol 84..136) som i basfallet håller
    # kvar regnvatten uppe på tomten (botten 13,50 m, tröskel söderut ~13,80 m).
    z[18:54, 84:136] = 13.50
    z[54:62, 72:150] = 13.80
    # Sidoramar så östra sluttningen rinner rakt söderut mot grannsänkan
    z[6:124, 64:72] = 15.2
    z[6:124, 150:158] = 15.2

    # Grannsänka söder om projektet (rad 78..116, kol 84..140):
    # Flat botten på 12,10 m (56 x 38 = 2128 celler = 133 m²), omgärdad av vall på 12,80 m
    z[74:120, 72:150] = 12.80
    # Sluttande inlopp från projektet ned i grannsänkan
    for r in range(62, 78):
        andel = (r - 62) / 16.0
        z[r, 84:140] = np.float32(13.75 - 1.35 * andel)
    z[78:116, 84:140] = 12.10
    # Lägsta randutlopp från grannsänkan söderut vid 12,75 m
    z[116:127, 108:116] = 12.75

    osak = np.full((hojd, bredd), 0.03, dtype=np.float32)
    slag = np.full((hojd, bredd), 4, dtype=np.uint8)

    # Grannentré på en svagt upphöjd terrass (12,24 m) i grannsänkan:
    # med basdjup ~0,20 m i botten (wse ~12,30 m) får entrén ~0,06 m (< 0,10 m gränsdjup) före,
    # men när projektet fyller upp tomtsvackan stiger wse över 12,34 m -> > 0,10 m efter!
    z[108:112, 108:112] = 12.24
    entre_e = geo.x_min + (110 + 0.5) * geo.pixelstorlek_x
    entre_n = geo.y_max - (110 + 0.5) * geo.pixelstorlek_y
    grann_entre = EntrePunkt(
        entre_id="granne-villa-1",
        e_m=entre_e,
        n_m=entre_n,
        gransdjup_m=0.10,
    )

    return z, osak, slag, geo, grann_entre


class TestForeEfterAlternativ(unittest.TestCase):
    """Verifierar före/efter, fördröjningsvolym och sid-vid-sida-jämförelse av Alt A och B."""

    def test_alternativ_a_utan_magasin_vs_alternativ_b_med_magasin(self) -> None:
        """Alt A försämrar grannen och ger fordrojningsvolym_m3 > 0; Alt B eliminerar skadan."""
        bas_z, bas_osak, bas_slag, geo, grann_entre = _skapa_kvarter_och_grannsanka()
        bas_graf = bygg_sankgraf(bas_z, bas_osak, bas_slag, geo)
        sektioner = las_gatusektioner_fran_konfig(_REPO_ROT / "config.toml")
        bas_recept = "c" * 64

        # Kraftigt 1-timmes skyfall (65 mm) så tomtsvackan i basfallet håller kvar ~25 m³
        regn_t = [0.0, 1800.0, 3600.0]
        regn_i = [65.0, 65.0, 65.0]

        # Alternativ A: planera ut kvarteret (fyller svackan till 14,10 m), bygg nytt hus och
        # dra ny parametrisk 12 m-gata som lutar söderut mot grannen — UTAN fördröjningsmagasin
        alt_a = ProjektLapp(
            lapp_id="kvarter-alt-a-utan-magasin",
            bas_recept=(bas_recept,),
            beskrivning="Nytt kvarter + gata standard-12m utan fördröjningsmagasin",
            hojdytor=(
                HojdYta(
                    yta_id="tomtplanering",
                    polygon=(
                        (150018.0, geo.y_max - 15.5),
                        (150037.5, geo.y_max - 15.5),
                        (150037.5, geo.y_max - 4.0),
                        (150018.0, geo.y_max - 4.0),
                    ),
                    hojd_rh2000=14.10,
                    markslag=1,
                ),
            ),
            gator=(
                LappGata(
                    gata_id="ny-lokalgata",
                    mittlinje=(
                        (150027.75, geo.y_max - 4.0),
                        (150027.75, geo.y_max - 15.5),
                    ),
                    sektion="standard-12m",
                    profil_rh2000=(14.20, 13.85),
                ),
            ),
            byggnader=(
                LappByggnad(
                    byggnad_id="nytt-flerfamiljshus",
                    polygon=(
                        (150019.0, geo.y_max - 10.0),
                        (150021.5, geo.y_max - 10.0),
                        (150021.5, geo.y_max - 5.5),
                        (150019.0, geo.y_max - 5.5),
                    ),
                    entre_rh2000=14.40,
                    entre_punkt=(150021.75, geo.y_max - 7.75),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            res_a = kor_fore_efter(
                bas_graf,
                bas_z,
                bas_osak,
                bas_slag,
                alt_a,
                regn_t,
                regn_i,
                bas_recept_sha256=bas_recept,
                grann_entreer=[grann_entre],
                sektioner=sektioner,
                lokal=True,
                ut_mapp=Path(tmpdir) / "alt_a",
            )

            # Verifiera K0/v1 run.json skrevs och är giltigt
            manifest_inlast = las_manifest(Path(tmpdir) / "alt_a" / "run.json")
            self.assertEqual(manifest_inlast.kontrakt, KONTRAKT_K0)
            self.assertEqual(manifest_inlast.modul, "lapp")

        # 1. Alternativ A ska orsaka ökat djup hos grannen, ny drabbad entré och positiv
        #    erforderlig fördröjningsvolym
        self.assertGreater(res_a.max_okning_djup_granne_m, 0.05)
        self.assertGreater(res_a.grann_forsamring_volym_m3, 5.0)
        self.assertGreaterEqual(res_a.fordrojningsvolym_m3, res_a.grann_forsamring_volym_m3)
        self.assertEqual(len(res_a.nya_drabbade_entreer), 1)
        self.assertEqual(res_a.nya_drabbade_entreer[0].entre_id, "granne-villa-1")
        self.assertEqual(len(res_a.forsamrade_entreer), 1)
        self.assertEqual(len(res_a.projekt_entreer), 1)
        self.assertFalse(res_a.projekt_entreer[0].overstiger_entre)
        self.assertIsNotNone(res_a.lokal_omrakning)
        if res_a.lokal_omrakning is not None:
            self.assertGreaterEqual(res_a.lokal_omrakning.antal_ateranvanda_noder, 1)

        # Verifiera att lokal före/efter ger identisk diff_max_djup_m som full omräkning
        res_a_full = kor_fore_efter(
            bas_graf,
            bas_z,
            bas_osak,
            bas_slag,
            alt_a,
            regn_t,
            regn_i,
            bas_recept_sha256=bas_recept,
            grann_entreer=[grann_entre],
            sektioner=sektioner,
            lokal=False,
        )
        diff_lokal_full = float(np.max(np.abs(res_a.diff_max_djup_m - res_a_full.diff_max_djup_m)))
        self.assertLess(diff_lokal_full, 1e-6)

        # 2. Alternativ B: lägg till ett fördröjningsmagasin i projektområdets nedre del
        #    dimensionerat >= fordrojningsvolym_m3 (med liten säkerhetsmarginal uppåt)
        behov_m3 = math.ceil(res_a.fordrojningsvolym_m3 * 1.05 * 10.0) / 10.0
        alt_b = ProjektLapp(
            lapp_id="kvarter-alt-b-med-magasin",
            bas_recept=(bas_recept,),
            beskrivning=f"Samma kvarter + fördröjningsmagasin ({behov_m3:.1f} m³)",
            hojdytor=alt_a.hojdytor,
            gator=alt_a.gator,
            byggnader=alt_a.byggnader,
            magasin=(
                LappMagasin(
                    magasin_id="fordrojningsmagasin-syd",
                    polygon=(
                        (150018.5, geo.y_max - 15.25),
                        (150037.0, geo.y_max - 15.25),
                        (150037.0, geo.y_max - 11.25),
                        (150018.5, geo.y_max - 11.25),
                    ),
                    volym_m3=behov_m3,
                    markslag=4,
                ),
            ),
        )

        # Kör sid-vid-sida-jämförelse av Alt A och Alt B
        jamforelse = jamfor_alternativ(
            bas_graf,
            bas_z,
            bas_osak,
            bas_slag,
            [alt_a, alt_b],
            regn_t,
            regn_i,
            bas_recept_sha256=bas_recept,
            grann_entreer=[grann_entre],
            sektioner=sektioner,
            lokal=True,
        )
        self.assertEqual(len(jamforelse), 2)
        sam_a, sam_b = jamforelse

        # Alternativ B ska helt eliminera försämringen hos grannen
        self.assertAlmostEqual(sam_b.max_okning_djup_granne_m, 0.0, places=5)
        self.assertAlmostEqual(sam_b.grann_forsamring_volym_m3, 0.0, places=5)
        self.assertAlmostEqual(sam_b.fordrojningsvolym_m3, 0.0, places=5)
        self.assertEqual(sam_b.antal_nya_drabbade_entreer, 0)
        self.assertEqual(sam_b.antal_forsamrade_entreer, 0)

        # Total körtid ska vara långt under acceptansgränsen 60 s (skalad med FLODEN_TIDSFAKTOR)
        total_tid_s = sam_a.kortid_s + sam_b.kortid_s
        grans_s = 60.0 * float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
        self.assertLess(total_tid_s, grans_s)

        # Jämförelsetabellen ska innehålla båda alternativen
        tabell = formatera_alternativ_tabell(jamforelse)
        self.assertIn("kvarter-alt-a-utan-magasin", tabell)
        self.assertIn("kvarter-alt-b-med-magasin", tabell)

    def test_overfallande_grannsanka_inkluderar_extra_overfallsvolym(self) -> None:
        """När grannsänkan bräddar över efter projekt ska fordrojningsvolym > lagrad ökning."""
        bas_z, bas_osak, bas_slag, geo, grann_entre = _skapa_kvarter_och_grannsanka()
        bas_graf = bygg_sankgraf(bas_z, bas_osak, bas_slag, geo)
        sektioner = las_gatusektioner_fran_konfig(_REPO_ROT / "config.toml")
        bas_recept = "d" * 64

        # Extremt skyfall (180 mm på 1 h) så grannsänkan (88 m³) fylls helt och bräddar över
        # efter projektet, men inte i basfallet (~61 m³ < 88 m³).
        regn_t = [0.0, 3600.0]
        regn_i = [180.0, 180.0]
        alt_a = ProjektLapp(
            lapp_id="extremregn-alt-a",
            bas_recept=(bas_recept,),
            hojdytor=(
                HojdYta(
                    yta_id="tomtplanering",
                    polygon=(
                        (150018.0, geo.y_max - 15.5),
                        (150037.5, geo.y_max - 15.5),
                        (150037.5, geo.y_max - 4.0),
                        (150018.0, geo.y_max - 4.0),
                    ),
                    hojd_rh2000=14.10,
                    markslag=1,
                ),
            ),
        )
        res = kor_fore_efter(
            bas_graf,
            bas_z,
            bas_osak,
            bas_slag,
            alt_a,
            regn_t,
            regn_i,
            bas_recept_sha256=bas_recept,
            grann_entreer=[grann_entre],
            sektioner=sektioner,
            lokal=True,
        )
        self.assertGreater(res.grann_forsamring_volym_m3, 0.0)
        self.assertGreater(res.fordrojningsvolym_m3, res.grann_forsamring_volym_m3)


if __name__ == "__main__":
    unittest.main()
