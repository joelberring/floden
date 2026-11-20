"""Tester för vägnät, broar/tunnlar och tidsberoende FIFO-Dijkstra (T07)."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from rutt import (
    RuttParametrarFel,
    VagKant,
    VagNat,
    VagNod,
    berakna_insatstider,
    effektivt_vagdjup_m,
    las_testparametrar_fran_konfig,
    tidsberoende_dijkstra,
)

REPO_ROT = Path(__file__).resolve().parents[2]
CONFIG_TOML = REPO_ROT / "config.toml"


class TestVagnatOchDijkstra(unittest.TestCase):
    """Verifierar bro/tunnel-djup samt tidsberoende FIFO-Dijkstra."""

    def test_bro_over_oversvammat_omrade_ar_farbar(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        noder = [
            VagNod("S", 150000.0, 6577000.0, 15.0),
            VagNod("M", 150500.0, 6577000.0, 15.0),
        ]
        bro = VagKant(
            kant_id="k_bro",
            fran_nod="S",
            till_nod="M",
            langd_m=360.0,
            normal_hastighet_km_h=36.0,  # 10 m/s -> 36 s
            typ="bro",
            vagbana_rh2000=15.0,
        )
        tunnel = VagKant(
            kant_id="k_tunnel",
            fran_nod="S",
            till_nod="M",
            langd_m=360.0,
            normal_hastighet_km_h=36.0,
            typ="tunnel",
            vagbana_rh2000=10.0,
        )

        # Vattenytan är 12.5 m RH2000, marken under bron har 2.0 m vattendjup.
        self.assertEqual(
            effektivt_vagdjup_m(bro, mark_vattendjup_m=2.0, vattenniva_rh2000=12.5),
            0.0,
        )
        # Härledd vattenyta från markhöjd 10.5 + djup 2.0 = 12.5 < brobana 15.0 -> 0.0 m
        self.assertEqual(
            effektivt_vagdjup_m(bro, mark_vattendjup_m=2.0, mark_hojd_rh2000=10.5),
            0.0,
        )
        # Om vattenytan överstiger brobanan (15.4 > 15.0) blir djupet 0.4 m
        self.assertAlmostEqual(
            effektivt_vagdjup_m(bro, mark_vattendjup_m=2.0, vattenniva_rh2000=15.4),
            0.4,
        )
        # Tunnel på 10.0 m under vattenyta 12.5 m -> 2.5 m vattendjup
        self.assertAlmostEqual(
            effektivt_vagdjup_m(tunnel, mark_vattendjup_m=0.0, vattenniva_rh2000=12.5),
            2.5,
        )

        graf = VagNat.fran_komponenter(noder, [bro])
        resultat = berakna_insatstider(
            graf,
            stationer={"st1": "S"},
            adresser={"adr1": "M"},
            tider_s=[0.0],
            kant_djup_per_tid={0.0: {"k_bro": 2.0}},
            vattenniva_per_tid={0.0: {"k_bro": 12.5}},
            parametrar=param,
            fordon_typ="ambulans",
            skarp=False,
        )
        self.assertEqual(len(resultat), 1)
        self.assertAlmostEqual(resultat[0].torr_insatstid_s, 36.0)
        self.assertAlmostEqual(resultat[0].insatstid_s, 36.0)

    def test_tidsberoende_dijkstra_omvag_och_avskuren(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        noder = [
            VagNod("S", 0.0, 0.0, 10.0),
            VagNod("A", 500.0, 0.0, 10.0),
            VagNod("M", 1000.0, 0.0, 10.0),
            VagNod("B", 500.0, 500.0, 14.0),
        ]
        # Direktväg S -> A -> M: 500 m + 500 m vid 36 km/h (10 m/s) => 50 s + 50 s = 100 s
        # Omväg S -> B -> M: 1000 m + 1000 m vid 36 km/h (10 m/s) => 100 s + 100 s = 200 s
        kanter = [
            VagKant("k_sa", "S", "A", 500.0, 36.0),
            VagKant("k_am", "A", "M", 500.0, 36.0),
            VagKant("k_sb", "S", "B", 1000.0, 36.0),
            VagKant("k_bm", "B", "M", 1000.0, 36.0),
        ]
        graf = VagNat.fran_komponenter(noder, kanter)

        # Vid t=0..300 är allt torrt; mellan t=300..600 översvämmas k_am (0.50 m >= 0.30 m).
        # k_bm förblir torr till t=1100 och översvämmas till t=1200.
        djup_serie: dict[float, dict[str, float]] = {
            0.0: {"k_sa": 0.0, "k_am": 0.0, "k_sb": 0.0, "k_bm": 0.0},
            300.0: {"k_sa": 0.0, "k_am": 0.0, "k_sb": 0.0, "k_bm": 0.0},
            600.0: {"k_sa": 0.0, "k_am": 0.50, "k_sb": 0.0, "k_bm": 0.0},
            1100.0: {"k_sa": 0.0, "k_am": 0.50, "k_sb": 0.0, "k_bm": 0.0},
            1200.0: {"k_sa": 0.0, "k_am": 0.50, "k_sb": 0.0, "k_bm": 0.50},
        }

        # Avgång vid t=560 s: anländer till A vid t=610 s -> k_am redan ofarbar -> väljer omväg!
        ank_560 = tidsberoende_dijkstra(
            graf, "S", 560.0, djup_serie, param.fordon["ambulans"]
        )
        self.assertAlmostEqual(ank_560["M"] - 560.0, 200.0)

        # När k_bm stiger linjärt mellan t=600 (0.0 m) och t=1200 (0.50 m):
        # Ankomst till B vid t=660 s ger djup 0.05 m -> faktor 13/15 -> 100 + 1500/13 s.
        djup_stigande: dict[float, dict[str, float]] = {
            600.0: {"k_sa": 0.0, "k_am": 0.50, "k_sb": 0.0, "k_bm": 0.0},
            1200.0: {"k_sa": 0.0, "k_am": 0.50, "k_sb": 0.0, "k_bm": 0.50},
        }
        ank_interp = tidsberoende_dijkstra(
            graf, "S", 560.0, djup_stigande, param.fordon["ambulans"]
        )
        self.assertAlmostEqual(ank_interp["M"] - 560.0, 100.0 + 1500.0 / 13.0)

        res = berakna_insatstider(
            graf,
            stationer={"st1": "S"},
            adresser={"adr1": "M"},
            tider_s=[0.0, 600.0, 1200.0],
            kant_djup_per_tid=djup_serie,
            parametrar=param,
            fordon_typ="ambulans",
            skarp=False,
        )
        per_tid = {r.t_s: r for r in res}
        self.assertAlmostEqual(per_tid[0.0].insatstid_s, 100.0)
        self.assertAlmostEqual(per_tid[0.0].torr_insatstid_s, 100.0)
        self.assertAlmostEqual(per_tid[600.0].insatstid_s, 200.0)
        self.assertGreater(per_tid[600.0].insatstid_s, per_tid[600.0].torr_insatstid_s)
        self.assertTrue(math.isinf(per_tid[1200.0].insatstid_s))
        self.assertAlmostEqual(per_tid[1200.0].torr_insatstid_s, 100.0)

    def test_berakna_insatstider_nekar_testvarden_skarpt(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        graf = VagNat.fran_komponenter(
            [VagNod("S", 0.0, 0.0, 10.0), VagNod("M", 100.0, 0.0, 10.0)],
            [VagKant("k1", "S", "M", 100.0, 36.0)],
        )
        with self.assertRaises(RuttParametrarFel):
            berakna_insatstider(
                graf,
                stationer={"st1": "S"},
                adresser={"adr1": "M"},
                tider_s=[0.0],
                kant_djup_per_tid={0.0: {}},
                parametrar=param,
                fordon_typ="ambulans",
                skarp=True,
            )


if __name__ == "__main__":
    unittest.main()
