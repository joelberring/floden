"""Profilextraktion: `Terrangprofil` längs snitt från källa till mottagare.

Regler för profilens punkter (samma för enstaka snitt och batch):

1. Varje ben (delsträcka mellan två knäckpunkter) delas i `ceil(längd / steg_m)` lika långa
   steg. Terrängen samplas bilinjärt i stegpunkterna, knäckpunkterna inräknade.
2. Byggnader som benet korsar läggs in exakt (skärning polygon–linje). För varje intervall
   `[a, b]` genom en byggnad skapas fyra punkter: mark i `a`, tak i `a + d`, tak i `b - d`
   och mark i `b`, med `d = min(skarm_dx_m, (b - a) / 3)`. Väggens fot ligger alltså exakt
   i väggen och krönet `d` in på taket, så att x förblir strikt växande.
3. Två intervall som ligger närmare varandra än `3 · skarm_dx_m` (sammanbyggda hus) fogas
   ihop: markpunkterna i fogen utgår och det senare intervallet börjar där det förra slutar.
4. Terrängsampel närmare ett intervall än `skarm_dx_m` utgår, utom snittets ändpunkter.
5. Markegenskaper per segment tas ur markslagsrastret i segmentets mittpunkt. Segment på
   byggnader (vägg och tak) får tabellens hårda byggnadsyta.

Källa eller mottagare inne i en byggnad ger `BullerFel`. Ett snitt som bara nuddar en vägg
utifrån (som i en reflexpunkt) ger ingen skärm och inget fel.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from numpy.typing import NDArray

from buller.scen.bullerscen import Boolar, Bullerscen, Heltal
from buller.scen.markklass import Markklasstabell, standardtabell
from buller.typer import BullerFel, Flyttal, Terrangprofil

STANDARD_STEG_M = 1.0
STANDARD_SKARM_DX_M = 0.01

# Kortare skärning än så räknas som tangering och ger ingen skärm.
_MIN_SKARMBREDD_M = 1e-6
# Kortaste tillåtna ben i ett snitt.
_MIN_BENLANGD_M = 1e-3

_TERRANG = 0
_VAGGFOT_IN = 1
_TAK = 2
_VAGGFOT_UT = 3


@dataclass(frozen=True, eq=False)
class Profilbatch:
    """Många profiler i platta arrayer. Profil `i` har punkterna `start[i]:start[i + 1]`."""

    x_m: Flyttal
    z_m: Flyttal
    flodesresistivitet_kNsm4: Flyttal  # per segment, profil i: start[i] - i : start[i+1] - i - 1
    rahet_m: Flyttal
    start: Heltal  # form (antal + 1,)
    kalla_hojd_m: Flyttal
    mottagare_hojd_m: Flyttal
    # Sant om profilen går över markslag utan klass och fått `okand_klass`.
    okant_markslag: Boolar

    @property
    def antal(self) -> int:
        return int(self.start.shape[0]) - 1

    def profil(self, i: int) -> Terrangprofil:
        """Profil nummer `i` som `Terrangprofil` (validerad av typen)."""
        if not 0 <= i < self.antal:
            raise BullerFel(f"Profilindex {i} finns inte (antal profiler: {self.antal}).")
        p0, p1 = int(self.start[i]), int(self.start[i + 1])
        return Terrangprofil(
            x_m=self.x_m[p0:p1],
            z_m=self.z_m[p0:p1],
            flodesresistivitet_kNsm4=self.flodesresistivitet_kNsm4[p0 - i : p1 - i - 1],
            rahet_m=self.rahet_m[p0 - i : p1 - i - 1],
            kalla_hojd_m=float(self.kalla_hojd_m[i]),
            mottagare_hojd_m=float(self.mottagare_hojd_m[i]),
        )

    def profiler(self) -> list[Terrangprofil]:
        """Alla profiler som lista."""
        return [self.profil(i) for i in range(self.antal)]


def _hojder(varde: Flyttal | float, antal: int, vad: str) -> Flyttal:
    arr = np.asarray(varde, dtype=np.float64)
    if arr.ndim == 0:
        arr = np.full(antal, float(arr))
    if arr.shape != (antal,):
        raise BullerFel(f"{vad} ska vara ett tal eller ha form ({antal},), fick {arr.shape}.")
    if not bool(np.all(np.isfinite(arr))) or bool(np.any(arr < 0.0)):
        raise BullerFel(f"{vad} ska vara ändlig och inte negativ.")
    return arr


def _byggnadsintervall(
    scen: Bullerscen,
    se: Flyttal,
    sn: Flyttal,
    te: Flyttal,
    tn: Flyttal,
    langd: Flyttal,
) -> tuple[Heltal, Flyttal, Flyttal, Heltal]:
    """Skärningar ben–byggnad: (ben, a, b, byggnad) med a, b som avstånd längs benet."""
    tomt = (np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0), np.zeros(0, dtype=np.int64))
    if not scen.byggnader:
        return tomt
    koord = np.stack([np.column_stack([se, sn]), np.column_stack([te, tn])], axis=1)
    linjer = shapely.linestrings(koord)
    ben_i, byggnad_i = scen.byggnadstrad.query(linjer, predicate="intersects")
    if ben_i.size == 0:
        return tomt
    snitt = shapely.intersection(linjer[ben_i], scen.polygoner[byggnad_i])
    delar, agare = shapely.get_parts(snitt, return_index=True)
    # Skärningen kan vara en samling (punkt + linje); plocka isär en nivå till.
    delar, agare2 = shapely.get_parts(delar, return_index=True)
    agare = agare[agare2]
    ar_linje = shapely.get_type_id(delar) == 1
    delar, agare = delar[ar_linje], agare[ar_linje]
    if delar.size == 0:
        return tomt
    punkter, del_i = shapely.get_coordinates(delar, return_index=True)
    ben_per_del = ben_i[agare]
    ben = ben_per_del[del_i]
    t = (
        (punkter[:, 0] - se[ben]) * (te[ben] - se[ben])
        + (punkter[:, 1] - sn[ben]) * (tn[ben] - sn[ben])
    ) / langd[ben]
    grans = np.flatnonzero(np.r_[True, del_i[1:] != del_i[:-1]])
    a = np.clip(np.minimum.reduceat(t, grans), 0.0, langd[ben_per_del])
    b = np.clip(np.maximum.reduceat(t, grans), 0.0, langd[ben_per_del])
    return ben_per_del.astype(np.int64), a, b, byggnad_i[agare].astype(np.int64)


def profiler_langs_polylinjer(
    scen: Bullerscen,
    knack_e_m: Flyttal,
    knack_n_m: Flyttal,
    kalla_hojd_m: Flyttal | float,
    mottagare_hojd_m: Flyttal | float,
    *,
    steg_m: float = STANDARD_STEG_M,
    skarm_dx_m: float = STANDARD_SKARM_DX_M,
    okand_klass: str | None = None,
    tabell: Markklasstabell | None = None,
) -> Profilbatch:
    """Vektoriserad profilextraktion längs många polylinjer med lika många knäckpunkter.

    `knack_e_m` och `knack_n_m` har form (antal, k) med k ≥ 2: källa, eventuella
    reflexpunkter, mottagare. Profilen byggs av benen i följd med x som löpande längd.

    `okand_klass` är impedansklassen (bokstav) för markslag utan översättning, t.ex. kod 0.
    Utan den ger sådant markslag `BullerFel`; med den används klassen och profilen flaggas
    i `Profilbatch.okant_markslag`.
    """
    ke = np.asarray(knack_e_m, dtype=np.float64)
    kn = np.asarray(knack_n_m, dtype=np.float64)
    if ke.ndim != 2 or ke.shape != kn.shape or ke.shape[1] < 2:
        raise BullerFel(
            "Knäckpunkterna ska ha form (antal, k) med k >= 2 och lika för e och n, "
            f"fick {ke.shape} och {kn.shape}."
        )
    if not (steg_m > 0.0 and np.isfinite(steg_m)):
        raise BullerFel(f"Steglängden ska vara > 0 m, fick {steg_m}.")
    if not (skarm_dx_m > 0.0 and np.isfinite(skarm_dx_m)):
        raise BullerFel(f"skarm_dx_m ska vara > 0 m, fick {skarm_dx_m}.")
    if not bool(np.all(np.isfinite(ke)) and np.all(np.isfinite(kn))):
        raise BullerFel("Snitten har icke-ändliga koordinater.")
    antal, antal_knack = ke.shape
    ben_per_profil = antal_knack - 1
    kalla_h = _hojder(kalla_hojd_m, antal, "Källhöjden")
    mottagare_h = _hojder(mottagare_hojd_m, antal, "Mottagarhöjden")
    tabell = tabell if tabell is not None else standardtabell()
    if antal == 0:
        tom = np.zeros(0)
        return Profilbatch(
            tom, tom, tom, tom, np.zeros(1, dtype=np.int64), tom, tom, np.zeros(0, dtype=np.bool_)
        )
    scen._krav_inom_raster(ke, kn, "Profilextraktion")

    # --- ben ---------------------------------------------------------------------------
    se, sn = ke[:, :-1].ravel(), kn[:, :-1].ravel()
    te, tn = ke[:, 1:].ravel(), kn[:, 1:].ravel()
    ben_profil = np.repeat(np.arange(antal, dtype=np.int64), ben_per_profil)
    ben_nr = np.tile(np.arange(ben_per_profil, dtype=np.int64), antal)
    langd = np.hypot(te - se, tn - sn)
    if bool(np.any(langd < _MIN_BENLANGD_M)):
        k = int(np.flatnonzero(langd < _MIN_BENLANGD_M)[0])
        raise BullerFel(
            f"Snitt {int(ben_profil[k])} har ett ben kortare än {_MIN_BENLANGD_M} m "
            "(källa och mottagare i samma punkt?)."
        )
    langd_2d = langd.reshape(antal, ben_per_profil)
    slut_2d = np.cumsum(langd_2d, axis=1)
    forskjutning = (slut_2d - langd_2d).ravel()
    ben_slut = slut_2d.ravel()
    total = slut_2d[:, -1]

    # --- terrängsampel -----------------------------------------------------------------
    antal_steg = np.maximum(1, np.ceil(langd / steg_m)).astype(np.int64)
    forsta = ben_nr == 0
    antal_sampel = antal_steg + forsta
    s_ben = np.repeat(np.arange(langd.shape[0], dtype=np.int64), antal_sampel)
    s_start = np.cumsum(antal_sampel) - antal_sampel
    s_j = np.arange(s_ben.shape[0], dtype=np.int64) - s_start[s_ben] + (~forsta[s_ben])
    s_slutpunkt = s_j == antal_steg[s_ben]
    f = s_j / antal_steg[s_ben]
    s_e = se[s_ben] * (1.0 - f) + te[s_ben] * f
    s_n = sn[s_ben] * (1.0 - f) + tn[s_ben] * f
    s_x = np.where(s_slutpunkt, ben_slut[s_ben], forskjutning[s_ben] + f * langd[s_ben])
    s_profil = ben_profil[s_ben]
    s_ande = (forsta[s_ben] & (s_j == 0)) | ((ben_nr[s_ben] == ben_per_profil - 1) & s_slutpunkt)

    # --- byggnadsintervall -------------------------------------------------------------
    i_ben, a, b, i_byggnad = _byggnadsintervall(scen, se, sn, te, tn, langd)
    behall = (b - a) >= _MIN_SKARMBREDD_M
    i_ben, a, b, i_byggnad = i_ben[behall], a[behall], b[behall], i_byggnad[behall]
    i_profil = ben_profil[i_ben]
    xa = forskjutning[i_ben] + a
    xb = forskjutning[i_ben] + b
    ordning = np.lexsort((xa, i_profil))
    i_ben, i_byggnad, i_profil = i_ben[ordning], i_byggnad[ordning], i_profil[ordning]
    xa, xb = xa[ordning], xb[ordning]
    tak = scen.tak_rh2000_m[i_byggnad] if i_byggnad.size else np.zeros(0)

    i_kallan = xa < _MIN_SKARMBREDD_M
    if bool(np.any(i_kallan)):
        k = int(i_profil[np.flatnonzero(i_kallan)[0]])
        raise BullerFel(
            f"Källan i snitt {k} (E {ke[k, 0]:.2f}, N {kn[k, 0]:.2f}) ligger inne i en "
            f"byggnad; {int(np.count_nonzero(i_kallan))} snitt berörs."
        )
    i_mottagaren = xb > total[i_profil] - _MIN_SKARMBREDD_M
    if bool(np.any(i_mottagaren)):
        k = int(i_profil[np.flatnonzero(i_mottagaren)[0]])
        raise BullerFel(
            f"Mottagaren i snitt {k} (E {ke[k, -1]:.2f}, N {kn[k, -1]:.2f}) ligger inne i "
            f"en byggnad; {int(np.count_nonzero(i_mottagaren))} snitt berörs."
        )

    antal_intervall = int(xa.shape[0])
    fog_vanster = np.zeros(antal_intervall, dtype=np.bool_)
    if antal_intervall > 1:
        samma = i_profil[1:] == i_profil[:-1]
        glapp = xa[1:] - xb[:-1]
        if bool(np.any(samma & (glapp < -3.0 * skarm_dx_m))):
            k = int(i_profil[1:][np.flatnonzero(samma & (glapp < -3.0 * skarm_dx_m))[0]])
            raise BullerFel(
                f"Snitt {k} går genom byggnader som överlappar varandra; rätta byggnadsdata."
            )
        fog_vanster[1:] = samma & (glapp < 3.0 * skarm_dx_m)
        xa = xa.copy()
        xa[1:] = np.where(fog_vanster[1:], xb[:-1], xa[1:])
        if bool(np.any(xb - xa < _MIN_SKARMBREDD_M)):
            raise BullerFel("Ett byggnadsintervall försvann vid hopfogning; rätta byggnadsdata.")
    fog_hoger = np.zeros(antal_intervall, dtype=np.bool_)
    fog_hoger[:-1] = fog_vanster[1:]
    d = np.minimum(skarm_dx_m, (xb - xa) / 3.0)

    # --- terrängsampel som faller i eller intill byggnader utgår -----------------------
    antal_s = int(s_x.shape[0])
    if antal_intervall:
        nyckel_x = np.concatenate([s_x, xa - skarm_dx_m])
        nyckel_p = np.concatenate([s_profil, i_profil])
        ar_intervall = np.concatenate(
            [np.zeros(antal_s, dtype=np.int8), np.ones(antal_intervall, dtype=np.int8)]
        )
        ordn = np.lexsort((ar_intervall, nyckel_x, nyckel_p))
        lopande = np.maximum.accumulate(np.where(ordn >= antal_s, ordn - antal_s, -1))
        ar_sampel = ordn < antal_s
        senaste = np.empty(antal_s, dtype=np.int64)
        senaste[ordn[ar_sampel]] = lopande[ar_sampel]
        kand = np.maximum(senaste, 0)
        bort = (
            (senaste >= 0) & (i_profil[kand] == s_profil) & (s_x < xb[kand] + skarm_dx_m) & ~s_ande
        )
        kvar = ~bort
        s_x, s_e, s_n, s_profil = s_x[kvar], s_e[kvar], s_n[kvar], s_profil[kvar]

    # --- alla punkter ------------------------------------------------------------------
    def _lage(x: Flyttal, urval: Boolar) -> tuple[Flyttal, Flyttal]:
        ben = i_ben[urval]
        andel = (x - forskjutning[ben]) / langd[ben]
        return se[ben] + (te[ben] - se[ben]) * andel, sn[ben] + (tn[ben] - sn[ben]) * andel

    ej_v = ~fog_vanster
    ej_h = ~fog_hoger
    alla = np.ones(antal_intervall, dtype=np.bool_)
    # (x, urval, tagg, höjd eller NaN för mark)
    delar: list[tuple[Flyttal, Boolar, int, Flyttal]] = [
        (xa[ej_v], ej_v, _VAGGFOT_IN, np.full(int(np.count_nonzero(ej_v)), np.nan)),
        (xa[fog_vanster], fog_vanster, _TAK, tak[fog_vanster]),
        ((xa + d)[ej_v], ej_v, _TAK, tak[ej_v]),
        (xb - d, alla, _TAK, tak),
        (xb[ej_h], ej_h, _VAGGFOT_UT, np.full(int(np.count_nonzero(ej_h)), np.nan)),
    ]
    p_x = [s_x]
    p_e = [s_e]
    p_n = [s_n]
    p_profil = [s_profil]
    p_tagg = [np.full(s_x.shape[0], _TERRANG, dtype=np.int8)]
    p_z = [np.full(s_x.shape[0], np.nan)]
    for x_del, urval, tagg, hojd in delar:
        e_del, n_del = _lage(x_del, urval)
        p_x.append(x_del)
        p_e.append(e_del)
        p_n.append(n_del)
        p_profil.append(i_profil[urval])
        p_tagg.append(np.full(x_del.shape[0], tagg, dtype=np.int8))
        p_z.append(hojd)
    x = np.concatenate(p_x)
    e = np.concatenate(p_e)
    n = np.concatenate(p_n)
    profil = np.concatenate(p_profil)
    tagg_alla = np.concatenate(p_tagg)
    z = np.concatenate(p_z)
    ordn = np.lexsort((x, profil))
    x, e, n, profil, tagg_alla, z = (
        x[ordn], e[ordn], n[ordn], profil[ordn], tagg_alla[ordn], z[ordn]
    )  # fmt: skip
    pa_mark = np.isnan(z)
    # Avrundning kan lägga en väggfot en hårsmån utanför rastret; håll den på kanten.
    e_mark = np.clip(e[pa_mark], scen.e_min_m, scen.e_max_m)
    n_mark = np.clip(n[pa_mark], scen.n_min_m, scen.n_max_m)
    z[pa_mark] = scen.markhojd_m(e_mark, n_mark)

    start = np.zeros(antal + 1, dtype=np.int64)
    start[1:] = np.cumsum(np.bincount(profil, minlength=antal))
    inom = profil[1:] == profil[:-1]
    if bool(np.any(inom & (np.diff(x) <= 0.0))):
        k = int(profil[1:][np.flatnonzero(inom & (np.diff(x) <= 0.0))[0]])
        raise BullerFel(
            f"Snitt {k} gav en profil där x inte är strikt växande (urartad geometri vid "
            "byggnad eller för litet skarm_dx_m)."
        )

    # --- markegenskaper per segment ----------------------------------------------------
    seg = np.flatnonzero(inom)
    pa_byggnad = (tagg_alla[seg] == _VAGGFOT_IN) | (tagg_alla[seg] == _TAK)
    sigma_tabell, rahet_tabell = tabell.uppslag()
    sigma = np.full(seg.shape[0], tabell.byggnadsyta.flodesresistivitet_kNsm4)
    rahet = np.full(seg.shape[0], tabell.byggnadsyta.rahet_m)
    mark = ~pa_byggnad
    mitt_e = np.clip(0.5 * (e[seg[mark]] + e[seg[mark] + 1]), scen.e_min_m, scen.e_max_m)
    mitt_n = np.clip(0.5 * (n[seg[mark]] + n[seg[mark] + 1]), scen.n_min_m, scen.n_max_m)
    koder = scen.markslag_i(mitt_e, mitt_n)
    sigma_mark = sigma_tabell[koder]
    rahet_mark = rahet_tabell[koder]
    okand = np.isnan(sigma_mark)
    okant_markslag = np.zeros(antal, dtype=np.bool_)
    if bool(np.any(okand)):
        if okand_klass is None:
            okanda_koder = sorted({int(v) for v in np.unique(koder[okand])})
            raise BullerFel(
                f"{int(np.count_nonzero(okand))} profilsegment ligger på markslag utan "
                f"impedansklass (koder {okanda_koder}). Rätta markslaget eller ange "
                "okand_klass uttryckligen."
            )
        sigma_okand, rahet_okand = tabell.egenskaper_for_impedansklass(okand_klass)
        sigma_mark = np.where(okand, sigma_okand, sigma_mark)
        rahet_mark = np.where(okand, rahet_okand, rahet_mark)
        okant_markslag[profil[seg[mark]][okand]] = True
    sigma[mark] = sigma_mark
    rahet[mark] = rahet_mark

    return Profilbatch(
        x_m=x,
        z_m=z,
        flodesresistivitet_kNsm4=sigma,
        rahet_m=rahet,
        start=start,
        kalla_hojd_m=kalla_h,
        mottagare_hojd_m=mottagare_h,
        okant_markslag=okant_markslag,
    )


def profiler_batch(
    scen: Bullerscen,
    kalla_e_m: Flyttal,
    kalla_n_m: Flyttal,
    kalla_hojd_m: Flyttal | float,
    mottagare_e_m: Flyttal,
    mottagare_n_m: Flyttal,
    mottagare_hojd_m: Flyttal | float,
    *,
    steg_m: float = STANDARD_STEG_M,
    skarm_dx_m: float = STANDARD_SKARM_DX_M,
    okand_klass: str | None = None,
    tabell: Markklasstabell | None = None,
) -> Profilbatch:
    """Vektoriserad profilextraktion för många raka snitt källa → mottagare."""
    delar: list[NDArray[np.float64]] = [
        np.asarray(v, dtype=np.float64)
        for v in (kalla_e_m, kalla_n_m, mottagare_e_m, mottagare_n_m)
    ]
    if any(v.ndim != 1 or v.shape != delar[0].shape for v in delar):
        raise BullerFel("Källornas och mottagarnas koordinater ska vara lika långa vektorer.")
    return profiler_langs_polylinjer(
        scen,
        np.column_stack([delar[0], delar[2]]),
        np.column_stack([delar[1], delar[3]]),
        kalla_hojd_m,
        mottagare_hojd_m,
        steg_m=steg_m,
        skarm_dx_m=skarm_dx_m,
        okand_klass=okand_klass,
        tabell=tabell,
    )


def extrahera_profil(
    scen: Bullerscen,
    kalla_e_m: float,
    kalla_n_m: float,
    kalla_hojd_m: float,
    mottagare_e_m: float,
    mottagare_n_m: float,
    mottagare_hojd_m: float,
    *,
    steg_m: float = STANDARD_STEG_M,
    skarm_dx_m: float = STANDARD_SKARM_DX_M,
    okand_klass: str | None = None,
    tabell: Markklasstabell | None = None,
) -> Terrangprofil:
    """Profil längs ett rakt snitt från källa till mottagare."""
    batch = profiler_batch(
        scen,
        np.array([kalla_e_m]),
        np.array([kalla_n_m]),
        kalla_hojd_m,
        np.array([mottagare_e_m]),
        np.array([mottagare_n_m]),
        mottagare_hojd_m,
        steg_m=steg_m,
        skarm_dx_m=skarm_dx_m,
        okand_klass=okand_klass,
        tabell=tabell,
    )
    return batch.profil(0)
