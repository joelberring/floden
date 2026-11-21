"""Syntetisk testscen för operatorlagret. INTE en ljudutbredningsmodell.

Överföringen här är påhittad för att pröva lagring, klustring och inkrementell omräkning:
den avtar med avståndet, har en bandberoende dämpning per meter och en "byggnad" (rektangel)
som dämpar eller blockerar de par vars siktlinje korsar den. Inget tal här kommer från
Nord2000 eller någon annan metod, och inget får användas för verkliga resultat.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

from buller.operator.granssnitt import OvreGrans, Parberaknare, Rektangel
from buller.typer import ANTAL_BAND, TERSBAND_HZ, Flyttal

_MINSTA_AVSTAND_M = 1.0


def syntetisk_vagning_db() -> Flyttal:
    """Påhittad bandvägning för tester (inte A-vägning): parabel i log-frekvens kring 2 kHz."""
    oktaver = np.log2(np.asarray(TERSBAND_HZ) / 2000.0)
    return np.asarray(-1.5 * oktaver**2, dtype=np.float64)


def _korsar(
    e0_m: float, n0_m: float, e1_m: Flyttal, n1_m: Flyttal, yta: Rektangel
) -> NDArray[np.bool_]:
    """Sant där sträckan (e0, n0)–(e1, n1) skär rektangeln (slab-metoden)."""
    t_in = np.zeros(e1_m.shape, dtype=np.float64)
    t_ut = np.ones(e1_m.shape, dtype=np.float64)
    traff = np.ones(e1_m.shape, dtype=np.bool_)
    for p0, p1, lag, hog in (
        (e0_m, e1_m, yta.e_min_m, yta.e_max_m),
        (n0_m, n1_m, yta.n_min_m, yta.n_max_m),
    ):
        riktning = p1 - p0
        parallell = riktning == 0.0
        saker = np.where(parallell, 1.0, riktning)
        t_a = (lag - p0) / saker
        t_b = (hog - p0) / saker
        t_in = np.where(parallell, t_in, np.maximum(t_in, np.minimum(t_a, t_b)))
        t_ut = np.where(parallell, t_ut, np.minimum(t_ut, np.maximum(t_a, t_b)))
        traff &= ~parallell | ((p0 >= lag) & (p0 <= hog))
    return np.asarray(traff & (t_in <= t_ut), dtype=np.bool_)


@dataclass(frozen=True, eq=False)
class SyntetiskScen:
    """Mottagare, källor och en flyttbar byggnad. Id är lika med index."""

    mottagare_e_m: Flyttal
    mottagare_n_m: Flyttal
    kalla_e_m: Flyttal
    kalla_n_m: Flyttal
    byggnad: Rektangel
    vaderklass_vikter: Flyttal
    dampning_db_per_m: Flyttal  # form (ANTAL_BAND,), >= 0
    skarmdampning_db: Flyttal  # form (ANTAL_BAND,), >= 0
    byggnad_blockerar: bool = False  # sant: par bakom byggnaden saknar bana (-inf)

    @property
    def antal_mottagare(self) -> int:
        return int(self.mottagare_e_m.shape[0])

    @property
    def antal_kallor(self) -> int:
        return int(self.kalla_e_m.shape[0])

    @property
    def mottagare_id(self) -> NDArray[np.int64]:
        return np.arange(self.antal_mottagare, dtype=np.int64)

    @property
    def kalla_id(self) -> NDArray[np.int64]:
        return np.arange(self.antal_kallor, dtype=np.int64)

    def med_byggnad(self, byggnad: Rektangel) -> SyntetiskScen:
        """Samma scen med byggnaden på ett nytt ställe."""
        return replace(self, byggnad=byggnad)

    def overforing_db(self, mottagare_index: int, kalla_index: Sequence[int]) -> Flyttal:
        """Påhittad överföring, form (n_kallor, n_vaderklasser, ANTAL_BAND)."""
        kallor = np.asarray(kalla_index, dtype=np.int64)
        e0 = float(self.mottagare_e_m[mottagare_index])
        n0 = float(self.mottagare_n_m[mottagare_index])
        e1, n1 = self.kalla_e_m[kallor], self.kalla_n_m[kallor]
        avstand_m = np.maximum(np.hypot(e1 - e0, n1 - n0), _MINSTA_AVSTAND_M)
        n_v = int(self.vaderklass_vikter.shape[0])
        vaderfaktor = 1.0 + 0.5 * np.arange(n_v, dtype=np.float64)
        h_db = (
            -20.0 * np.log10(avstand_m)[:, np.newaxis, np.newaxis]
            - 11.0
            - avstand_m[:, np.newaxis, np.newaxis]
            * vaderfaktor[np.newaxis, :, np.newaxis]
            * self.dampning_db_per_m[np.newaxis, np.newaxis, :]
        )
        skymd = _korsar(e0, n0, e1, n1, self.byggnad)
        if self.byggnad_blockerar:
            h_db[skymd] = -np.inf
        else:
            h_db[skymd] -= self.skarmdampning_db
        return np.asarray(h_db, dtype=np.float64)

    def parberaknare(self) -> Parberaknare:
        return self.overforing_db

    def ovre_grans(self, forskjutning_db: float = 0.0) -> OvreGrans:
        """Övre gräns för `overforing_db`: fritt fält utan byggnad på minsta avståndet.

        `forskjutning_db < 0` ger en medvetet felaktig (för låg) gräns, för tester som visar
        vad som händer när antagandet om övre gränsen inte håller.
        """

        def grans(minsta_avstand_m: float) -> Flyttal:
            avstand_m = max(minsta_avstand_m, _MINSTA_AVSTAND_M)
            return np.asarray(
                -20.0 * np.log10(avstand_m)
                - 11.0
                - avstand_m * self.dampning_db_per_m
                + forskjutning_db,
                dtype=np.float64,
            )

        return grans

    def korridorindex(self) -> SyntetisktKorridorindex:
        return SyntetisktKorridorindex(self)

    def referensniva_db(self, lw_db: Flyttal) -> Flyttal:
        """Nivå per mottagare och band, rakt av enligt KB2 utan lager (oberoende facit)."""
        alla = list(range(self.antal_kallor))
        niva = np.empty((self.antal_mottagare, ANTAL_BAND), dtype=np.float64)
        for m in range(self.antal_mottagare):
            energi = np.power(10.0, (self.overforing_db(m, alla) + lw_db[:, np.newaxis, :]) / 10.0)
            per_band = np.sum(
                energi * self.vaderklass_vikter[np.newaxis, :, np.newaxis], axis=(0, 1)
            )
            with np.errstate(divide="ignore"):
                niva[m] = 10.0 * np.log10(per_band)
        return niva


@dataclass(frozen=True, eq=False)
class SyntetisktKorridorindex:
    """Uttömmande korridorindex för den syntetiska scenen (siktlinje mot rektangel)."""

    scen: SyntetiskScen

    def berorda_par(
        self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
    ) -> set[tuple[int, int]]:
        yta = Rektangel(e_min_m, n_min_m, e_max_m, n_max_m)
        par: set[tuple[int, int]] = set()
        for m in range(self.scen.antal_mottagare):
            traff = _korsar(
                float(self.scen.mottagare_e_m[m]),
                float(self.scen.mottagare_n_m[m]),
                self.scen.kalla_e_m,
                self.scen.kalla_n_m,
                yta,
            )
            par.update((m, int(k)) for k in np.flatnonzero(traff))
        return par


def skapa_syntetisk_scen(
    *,
    antal_mottagare: int,
    antal_kallor: int,
    fro: int = 1,
    sida_m: float = 2000.0,
    antal_vagar: int = 10,
    antal_vaderklasser: int = 1,
    byggnad_blockerar: bool = False,
) -> SyntetiskScen:
    """Kvadratisk scen med ett rutnät av "vägar" (källor längs linjer) och slumpade mottagare."""
    slump = np.random.default_rng(fro)
    per_vag = -(-antal_kallor // antal_vagar)
    langs_m = (np.arange(per_vag, dtype=np.float64) + 0.5) * sida_m / per_vag
    e_delar: list[Flyttal] = []
    n_delar: list[Flyttal] = []
    for vag in range(antal_vagar):
        tvars_m = np.full(per_vag, (vag // 2 + 0.5) * sida_m / (-(-antal_vagar // 2)))
        if vag % 2 == 0:
            e_delar.append(langs_m)
            n_delar.append(tvars_m)
        else:
            e_delar.append(tvars_m + 7.0)
            n_delar.append(langs_m)
    kalla_e_m = np.concatenate(e_delar)[:antal_kallor]
    kalla_n_m = np.concatenate(n_delar)[:antal_kallor]
    vikter = np.arange(antal_vaderklasser, 0, -1, dtype=np.float64)
    frekvens_khz = np.asarray(TERSBAND_HZ) / 1000.0
    mitt_m = sida_m / 2.0
    return SyntetiskScen(
        mottagare_e_m=slump.uniform(0.0, sida_m, antal_mottagare),
        mottagare_n_m=slump.uniform(0.0, sida_m, antal_mottagare),
        kalla_e_m=np.ascontiguousarray(kalla_e_m),
        kalla_n_m=np.ascontiguousarray(kalla_n_m),
        byggnad=Rektangel(mitt_m - 60.0, mitt_m - 15.0, mitt_m + 60.0, mitt_m + 15.0),
        vaderklass_vikter=vikter / np.sum(vikter),
        dampning_db_per_m=np.asarray(0.004 * frekvens_khz**1.2, dtype=np.float64),
        skarmdampning_db=np.asarray(8.0 + 4.0 * np.log10(frekvens_khz / 0.025), dtype=np.float64),
        byggnad_blockerar=byggnad_blockerar,
    )


def syntetisk_emission_db(scen: SyntetiskScen, fro: int = 2) -> Flyttal:
    """Påhittad emission `L_W[n_kallor, band]` med variation mellan källor och band."""
    slump = np.random.default_rng(fro)
    oktaver = np.log2(np.asarray(TERSBAND_HZ) / 1000.0)
    spektrum_db = 95.0 - 0.8 * oktaver**2
    per_kalla_db = slump.uniform(-6.0, 6.0, size=(scen.antal_kallor, 1))
    return np.asarray(spektrum_db[np.newaxis, :] + per_kalla_db, dtype=np.float64)
