"""Banfinnare: scen + mottagare + källsegment → banor, och överföring per par (KB2).

Banorna byggs i tre steg som är åtskilda för att högre reflexordning ska kunna läggas till:
kandidater i plan (direkta par, reflexer med knäckpunkter), profiler längs knäckpunkterna
och till sist `typer.Bana`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from buller.banor.direkt import par_inom_radie
from buller.banor.korridor import STANDARD_CELL_M, Korridorindex
from buller.banor.reflex import STANDARD_FASADMARGINAL_M, forsta_ordningens_reflexer
from buller.scen.bullerscen import Boolar, Bullerscen
from buller.scen.markklass import Markklasstabell
from buller.scen.profil import (
    STANDARD_SKARM_DX_M,
    STANDARD_STEG_M,
    profiler_langs_polylinjer,
)
from buller.typer import (
    ANTAL_BAND,
    Bana,
    BullerFel,
    Flyttal,
    Kallsegment,
    Mottagare,
    P2PKarna,
    Vader,
)

HOGSTA_REFLEXORDNING = 1
_BANOR_PER_DEL = 20_000

Parberaknare = Callable[[int, Sequence[int]], Flyttal]


def _unika_id(id_lista: list[int], vad: str) -> None:
    if len(set(id_lista)) != len(id_lista):
        raise BullerFel(f"{vad} har id som förekommer flera gånger.")


@dataclass(frozen=True, eq=False)
class Banuppsattning:
    """Banor mellan en uppsättning mottagare och källsegment.

    `banor` är sorterade på (mottagarindex, källindex) med den direkta banan före reflexerna.
    """

    mottagare: tuple[Mottagare, ...]
    kallor: tuple[Kallsegment, ...]
    banor: tuple[Bana, ...]
    # Sant för banor vars profil går över markslag utan klass (se `okand_klass`).
    okant_markslag: Boolar

    def __post_init__(self) -> None:
        _unika_id([m.id for m in self.mottagare], "Mottagarna")
        _unika_id([k.id for k in self.kallor], "Källsegmenten")
        if self.okant_markslag.shape != (len(self.banor),):
            raise BullerFel("okant_markslag ska ha ett värde per bana.")
        m_index = {m.id: i for i, m in enumerate(self.mottagare)}
        k_index = {k.id: i for i, k in enumerate(self.kallor)}
        per_par: dict[tuple[int, int], list[int]] = {}
        for i, bana in enumerate(self.banor):
            if bana.mottagare_id not in m_index or bana.kalla_id not in k_index:
                raise BullerFel(
                    f"Bana {i} hänvisar till mottagare {bana.mottagare_id} eller källa "
                    f"{bana.kalla_id} som inte finns i uppsättningen."
                )
            per_par.setdefault((m_index[bana.mottagare_id], k_index[bana.kalla_id]), []).append(i)
        object.__setattr__(self, "_per_par", per_par)

    def banor_for_par(self, mottagarindex: int, kallindex: int) -> list[Bana]:
        """Banorna för ett par, angivet med index i `mottagare` och `kallor`."""
        per_par: dict[tuple[int, int], list[int]] = self._per_par  # type: ignore[attr-defined]
        return [self.banor[i] for i in per_par.get((mottagarindex, kallindex), [])]

    def korridorindex(
        self, halvbredd_m: float, *, cell_m: float = STANDARD_CELL_M
    ) -> Korridorindex:
        """Korridorindex över uppsättningens banor."""
        return Korridorindex(self.banor, halvbredd_m, cell_m=cell_m)

    def storsta_antal_banor_per_par(self) -> int:
        """Största antalet banor (direkt plus reflexer) som något par har."""
        per_par: dict[tuple[int, int], list[int]] = self._per_par  # type: ignore[attr-defined]
        return max((len(v) for v in per_par.values()), default=0)

    def ovre_grans_par(
        self, ovre_grans_bana: Callable[[float], Flyttal]
    ) -> Callable[[float], Flyttal]:
        """Övre gräns för ett *pars* överföring, givet kärnans gräns för en enskild bana.

        Ett par är energisumman av sina banor. Varje bana är minst lika lång som avståndet
        i plan mellan källa och mottagare, reflexförlusten är aldrig negativ och kärnans
        gräns avtar med avståndet. Parets överföring är därför högst banans gräns plus
        `10·lg(antal banor)`, med det största antalet banor i uppsättningen. Operatorlagrets
        klustring (KB2) ska få den här gränsen, inte kärnans gräns för en bana.
        """
        tillagg_db = 10.0 * math.log10(max(self.storsta_antal_banor_per_par(), 1))

        def grans(avstand_m: float) -> Flyttal:
            enskild: Flyttal = np.asarray(ovre_grans_bana(avstand_m), dtype=np.float64)
            return enskild + tillagg_db

        return grans

    def parberaknare(self, karna: P2PKarna, vaderklasser: Sequence[Vader]) -> Parberaknare:
        """Funktion (mottagarindex, källindex) → överföring, form (n_källor, n_väder, 27).

        Värdet är `10·lg Σ_banor 10^(H_db / 10)` med `H_db = kärna.overforing_db(profil, väder)
        - reflexforlust_db` enligt KB2, alltså energisumman över parets banor per väderklass.
        Väderklassernas vikt används inte här; den läggs på när nivån summeras (KB2).
        Index avser lägen i `mottagare` och `kallor`. Par utan bana ger -inf.
        """
        if not isinstance(karna, P2PKarna):
            raise BullerFel("Kärnan uppfyller inte typer.P2PKarna.")
        vader = tuple(vaderklasser)
        if not vader:
            raise BullerFel("Minst en väderklass måste anges.")
        antal_m, antal_k = len(self.mottagare), len(self.kallor)

        def berakna(mottagarindex: int, kallindex: Sequence[int]) -> Flyttal:
            if not 0 <= mottagarindex < antal_m:
                raise BullerFel(f"Mottagarindex {mottagarindex} finns inte (antal {antal_m}).")
            energi = np.zeros((len(kallindex), len(vader), ANTAL_BAND))
            for rad, k in enumerate(kallindex):
                if not 0 <= k < antal_k:
                    raise BullerFel(f"Källindex {k} finns inte (antal {antal_k}).")
                for bana in self.banor_for_par(mottagarindex, int(k)):
                    for v, klass in enumerate(vader):
                        h_db = np.asarray(karna.overforing_db(bana.profil, klass), dtype=np.float64)
                        if h_db.shape != (ANTAL_BAND,):
                            raise BullerFel(
                                f"Kärnan '{karna.namn}' gav form {h_db.shape}, "
                                f"väntat ({ANTAL_BAND},)."
                            )
                        if bool(np.any(np.isnan(h_db))):
                            raise BullerFel(f"Kärnan '{karna.namn}' gav NaN i överföringen.")
                        energi[rad, v] += 10.0 ** ((h_db - bana.reflexforlust_db) / 10.0)
            with np.errstate(divide="ignore"):
                overforing: Flyttal = 10.0 * np.log10(energi)
            return overforing

        return berakna


def bygg_banuppsattning(
    scen: Bullerscen,
    mottagare: Sequence[Mottagare],
    kallor: Sequence[Kallsegment],
    *,
    maxradie_m: float,
    reflexordning: int = HOGSTA_REFLEXORDNING,
    egen_fasad_reflex: bool = False,
    steg_m: float = STANDARD_STEG_M,
    skarm_dx_m: float = STANDARD_SKARM_DX_M,
    fasadmarginal_m: float = STANDARD_FASADMARGINAL_M,
    okand_klass: str | None = None,
    tabell: Markklasstabell | None = None,
) -> Banuppsattning:
    """Hittar direkta banor och reflexer och bygger profiler för dem.

    En bana tas med när dess längd i plan är högst `maxradie_m` (för reflexer längden via
    reflexpunkten). Med `egen_fasad_reflex = False` reflekterar inte den byggnad som en
    fasadpunkt hör till (`Mottagare.byggnad_id`), så att nivån blir ett frifältsvärde.
    """
    if reflexordning not in range(HOGSTA_REFLEXORDNING + 1):
        raise BullerFel(
            f"Reflexordning {reflexordning} stöds inte (0 till {HOGSTA_REFLEXORDNING})."
        )
    mottagare_t, kallor_t = tuple(mottagare), tuple(kallor)
    _unika_id([m.id for m in mottagare_t], "Mottagarna")
    _unika_id([k.id for k in kallor_t], "Källsegmenten")
    me = np.array([m.e_m for m in mottagare_t], dtype=np.float64)
    mn = np.array([m.n_m for m in mottagare_t], dtype=np.float64)
    mh = np.array([m.hojd_over_mark_m for m in mottagare_t], dtype=np.float64)
    ke = np.array([k.e_m for k in kallor_t], dtype=np.float64)
    kn = np.array([k.n_m for k in kallor_t], dtype=np.float64)
    kh = np.array([k.hojd_over_mark_m for k in kallor_t], dtype=np.float64)
    for vad, e, n, h in (("Mottagarna", me, mn, mh), ("Källsegmenten", ke, kn, kh)):
        if not bool(np.all(np.isfinite(e)) and np.all(np.isfinite(n)) and np.all(np.isfinite(h))):
            raise BullerFel(f"{vad} har icke-ändliga koordinater eller höjder.")
        if bool(np.any(h < 0.0)):
            raise BullerFel(f"{vad} har negativ höjd över mark.")
        scen._krav_inom_raster(e, n, vad)
        i_hus = scen.byggnad_vid(e, n)
        if bool(np.any(i_hus >= 0)):
            k = int(np.flatnonzero(i_hus >= 0)[0])
            raise BullerFel(
                f"{vad}: punkt {k} (E {e[k]:.2f}, N {n[k]:.2f}) ligger inuti byggnaden "
                f"'{scen.byggnader[int(i_hus[k])].id}'; {int(np.count_nonzero(i_hus >= 0))} "
                "punkter berörs."
            )

    # --- kandidater i plan: (mottagare, källa, knäckpunkter, reflekterande väggar) -------
    dm, dk = par_inom_radie(me, mn, ke, kn, maxradie_m)
    grupper: list[
        tuple[NDArray[np.int64], NDArray[np.int64], Flyttal, Flyttal, NDArray[np.int64] | None]
    ] = [
        (dm, dk, np.column_stack([ke[dk], me[dm]]), np.column_stack([kn[dk], mn[dm]]), None)
    ]
    if reflexordning >= 1 and scen.byggnader and me.shape[0] and ke.shape[0]:
        utesluten = None
        if not egen_fasad_reflex:
            utesluten = np.array(
                [
                    -1 if m.byggnad_id is None else scen.byggnadsindex.get(m.byggnad_id, -1)
                    for m in mottagare_t
                ],
                dtype=np.int64,
            )
        reflexer = forsta_ordningens_reflexer(
            scen,
            me,
            mn,
            scen.markhojd_m(me, mn) + mh,
            ke,
            kn,
            scen.markhojd_m(ke, kn) + kh,
            maxradie_m,
            utesluten_byggnad=utesluten,
            fasadmarginal_m=fasadmarginal_m,
        )
        rm, rk = reflexer.mottagare_index, reflexer.kalla_index
        grupper.append(
            (
                rm,
                rk,
                np.column_stack([ke[rk], reflexer.punkt_e_m, me[rm]]),
                np.column_stack([kn[rk], reflexer.punkt_n_m, mn[rm]]),
                reflexer.vagg_index,
            )
        )

    # --- profiler och banor --------------------------------------------------------------
    vaggar = scen.vaggar
    poster: list[tuple[int, int, int, int, Bana, bool]] = []
    for typnr, (gm, gk, plan_e, plan_n, vagg) in enumerate(grupper):
        for start in range(0, int(gm.shape[0]), _BANOR_PER_DEL):
            slut = min(int(gm.shape[0]), start + _BANOR_PER_DEL)
            batch = profiler_langs_polylinjer(
                scen,
                plan_e[start:slut],
                plan_n[start:slut],
                kh[gk[start:slut]],
                mh[gm[start:slut]],
                steg_m=steg_m,
                skarm_dx_m=skarm_dx_m,
                okand_klass=okand_klass,
                tabell=tabell,
            )
            for lokal in range(slut - start):
                i = start + lokal
                m, k = int(gm[i]), int(gk[i])
                if vagg is None:
                    bana = Bana(
                        mottagare_id=mottagare_t[m].id,
                        kalla_id=kallor_t[k].id,
                        typ="direkt",
                        profil=batch.profil(lokal),
                        plan_e_m=plan_e[i].copy(),
                        plan_n_m=plan_n[i].copy(),
                    )
                else:
                    forlust_db = float(np.sum(vaggar.reflektionsforlust_db[vagg[i]]))
                    bana = Bana(
                        mottagare_id=mottagare_t[m].id,
                        kalla_id=kallor_t[k].id,
                        typ="reflex",
                        profil=batch.profil(lokal),
                        plan_e_m=plan_e[i].copy(),
                        plan_n_m=plan_n[i].copy(),
                        reflexforlust_db=np.full(ANTAL_BAND, forlust_db),
                        reflekterande_byggnader=tuple(
                            scen.byggnader[int(b)].id for b in vaggar.byggnad_index[vagg[i]]
                        ),
                    )
                poster.append((m, k, typnr, i, bana, bool(batch.okant_markslag[lokal])))
    poster.sort(key=lambda p: p[:4])
    return Banuppsattning(
        mottagare=mottagare_t,
        kallor=kallor_t,
        banor=tuple(p[4] for p in poster),
        okant_markslag=np.array([p[5] for p in poster], dtype=np.bool_),
    )


def hitta_banor(
    scen: Bullerscen,
    mottagare: Sequence[Mottagare],
    kallor: Sequence[Kallsegment],
    *,
    maxradie_m: float,
    reflexordning: int = HOGSTA_REFLEXORDNING,
    egen_fasad_reflex: bool = False,
    steg_m: float = STANDARD_STEG_M,
    skarm_dx_m: float = STANDARD_SKARM_DX_M,
    fasadmarginal_m: float = STANDARD_FASADMARGINAL_M,
    okand_klass: str | None = None,
    tabell: Markklasstabell | None = None,
) -> list[Bana]:
    """Scen + mottagare + källsegment → lista av banor (direkta och första ordningens reflexer)."""
    return list(
        bygg_banuppsattning(
            scen,
            mottagare,
            kallor,
            maxradie_m=maxradie_m,
            reflexordning=reflexordning,
            egen_fasad_reflex=egen_fasad_reflex,
            steg_m=steg_m,
            skarm_dx_m=skarm_dx_m,
            fasadmarginal_m=fasadmarginal_m,
            okand_klass=okand_klass,
            tabell=tabell,
        ).banor
    )
