"""Hjälpmedel för testerna av Nord2000-testfall: konstgjord kärna, emission och fall."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from buller.emission import Trafikflode, Vagforhallanden
from buller.testfall import Facit, Facitserie, N2kFall, Terrangdata, Trafikpost, Vagdata
from buller.typer import ANTAL_BAND, Flyttal, Terrangprofil, Vader


class Frifaltskarna:
    """Konstgjord kärna: sfärisk utbredning från punktkälla, L_p − L_W = −10·lg(4πr²)."""

    namn = "frifalt-test"
    version = "0"
    nord2000_konform = False

    def __init__(self, tillagg_db: Flyttal | None = None) -> None:
        self.tillagg_db = np.zeros(ANTAL_BAND) if tillagg_db is None else tillagg_db
        self.anrop = 0

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        self.anrop += 1
        dz = (profil.z_m[-1] + profil.mottagare_hojd_m) - (profil.z_m[0] + profil.kalla_hojd_m)
        r2 = float(profil.x_m[-1]) ** 2 + float(dz) ** 2
        return np.full(ANTAL_BAND, -10.0 * math.log10(4.0 * math.pi * r2)) + self.tillagg_db


class Konstantemission:
    """Konstgjord källmodell: en delkälla, samma nivå per fordon i alla band."""

    def __init__(self, niva_per_fordon_db: float = 100.0, fel: str | None = None) -> None:
        self.niva_per_fordon_db = niva_per_fordon_db
        self.fel = fel

    @property
    def delkallhojder_m(self) -> tuple[float, ...]:
        return (0.5,)

    def ljudeffekt_db(
        self,
        trafik: Sequence[Trafikflode],
        segment_langd_m: float,
        period_s: float,
        delkallhojder_m: Sequence[float] | None = None,
        forhallanden: Vagforhallanden | None = None,
    ) -> Flyttal:
        self.forhallanden = forhallanden
        if self.fel is not None:
            from buller.typer import BullerFel

            raise BullerFel(self.fel)
        andel = sum(
            t.antal_fordon * segment_langd_m / (t.hastighet_km_h / 3.6 * period_s) for t in trafik
        )
        return np.full((1, ANTAL_BAND), self.niva_per_fordon_db + 10.0 * math.log10(andel))


def exempel_fall(**andringar: object) -> N2kFall:
    """Ett fullständigt, påhittat fall med rak väg."""
    grund: dict[str, object] = {
        "id": "NT1",
        "namn": "Påhittat testfall",
        "typ": "nord2000_rak_vag",
        "beskrivning": "Plan mark, konstruerat för enhetstester.",
        "status": "redo",
        "kalla": {"dokument": "påhittat", "sida": 1},
        "indata_fullstandig": True,
        "vag": Vagdata(kalla_x_m=2.0, antal_kallpunkter=179, vinkelupplosning_grader=1.0),
        "mottagare_x_m": 50.0,
        "mottagare_hojd_m": 1.5,
        "terrang": Terrangdata((0.0, 5.0, 60.0), (0.0, 0.0, 1.0), (20000.0, 200.0), (0.0, 0.25)),
        "trafik": (Trafikpost(1, 10000.0, 80.0, "T"),),
        "perioder_h": {"T": 24.0},
        "vader": Vader(),
        "vader_tillagg": {"vindriktning_grader": 0.0},
        "ovrigt": {"anm": "text med \"citat\" och åäö", "flagga": True, "antal": 3},
    }
    grund.update(andringar)
    return N2kFall(**grund)  # type: ignore[arg-type]


def exempel_facit(
    ar_facit: bool,
    totaler: dict[str, float],
    spektrum: dict[str, Flyttal],
    villkor: dict[str, float | str | bool | int] | None = None,
) -> Facit:
    return Facit(
        ar_facit=ar_facit,
        ar_facit_skal="konstruerat i test",
        metodversion="test",
        kalla={"dokument": "påhittat"},
        serier=(Facitserie("s1", villkor or {}, totaler, spektrum),),
    )
