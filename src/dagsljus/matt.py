"""Dagsljusmått ur horisontprofiler: SVF, VSC (BRE, mulen CIE-himmel) och soltimmar.

Härledningarna står i `docs/DAGSLJUS.md`. Alla vinklar i radianer, azimut norr = 0, medurs.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

Falt = NDArray[np.float64]

# Horisontell belysning under oskymd CIE-mulen himmel med zenitluminans 1: 7 pi / 9.
N_HORISONTELL_OSKYMD = 7.0 * math.pi / 9.0
# VSC för en oskymd vertikal fasad (BRE-normeringen): (2/3) (pi/4 + 2/3) / (7 pi / 9) = 0,3962.
VSC_OSKYMD = (2.0 / 3.0) * (math.pi / 4.0 + 2.0 / 3.0) / N_HORISONTELL_OSKYMD


class MattFel(Exception):
    """Ogiltig indata till ett dagsljusmått."""


def svf_fran_horisont(h_rad: NDArray[Any], axel: int = 0) -> Falt:
    """SVF för en horisontell yta: 1 - (1/2pi) Int sin^2 h dphi, som medelvärde över K lika
    fördelade riktningar längs `axel`. Fri yta (h = 0) ger exakt 1."""
    h = np.asarray(h_rad, dtype=np.float64)
    if h.size and (h.min() < -1e-12 or h.max() > math.pi / 2 + 1e-12):
        raise MattFel("Horisontvinklar måste ligga i [0, pi/2].")
    return np.asarray(1.0 - np.mean(np.sin(h) ** 2, axis=axel), dtype=np.float64)


def himmelsintegral(h_rad: NDArray[Any]) -> Falt:
    """Inre integralen I(h) = Int_h^{pi/2} (1 + 2 sin g)/3 cos^2 g dg i sluten form:

        I(h) = (1/3) [ pi/4 - h/2 - sin(2h)/4 + (2/3) cos^3 h ]

    I(0) = (1/3)(pi/4 + 2/3), I(pi/2) = 0.
    """
    h = np.asarray(h_rad, dtype=np.float64)
    return np.asarray(
        (math.pi / 4.0 - h / 2.0 - np.sin(2.0 * h) / 4.0 + (2.0 / 3.0) * np.cos(h) ** 3) / 3.0,
        dtype=np.float64,
    )


def vsc_fran_halvplansprofil(h_rad: NDArray[Any]) -> Falt:
    """VSC (andel, 0–0,396) ur horisontvinklar i `K` mittpunktsriktningar över fasadens halvplan.

    `h_rad` har formen (..., K) där riktning j ligger i psi_j = -pi/2 + (j + 1/2) pi / K
    relativt fasadens normalazimut. Midpunktsregel för
    VSC = (1/N) Int_{-pi/2}^{pi/2} I(h(psi)) cos psi dpsi.
    """
    h = np.asarray(h_rad, dtype=np.float64)
    if h.ndim < 1 or h.shape[-1] < 2:
        raise MattFel("Halvplansprofilen måste ha minst 2 riktningar längs sista axeln.")
    if h.size and (h.min() < -1e-12 or h.max() > math.pi / 2 + 1e-12):
        raise MattFel("Horisontvinklar måste ligga i [0, pi/2].")
    k = h.shape[-1]
    psi = -math.pi / 2.0 + (np.arange(k) + 0.5) * math.pi / k
    summa = np.sum(himmelsintegral(h) * np.cos(psi), axis=-1)
    return np.asarray(summa * (math.pi / k) / N_HORISONTELL_OSKYMD, dtype=np.float64)


def halvplansriktningar(normal_azimut_rad: Falt, antal: int) -> Falt:
    """Azimuter (n, antal) för fasadens halvplan: normal + psi_j (se `vsc_fran_halvplansprofil`)."""
    psi = -math.pi / 2.0 + (np.arange(antal) + 0.5) * math.pi / antal
    n = np.atleast_1d(np.asarray(normal_azimut_rad, dtype=np.float64))
    return np.asarray((n[:, None] + psi[None, :]) % (2.0 * math.pi), dtype=np.float64)


def interpolera_horisont(h_k: NDArray[Any], azimut_rad: Falt) -> Falt:
    """Linjär interpolation (cirkulär) av en horisontprofil h_k (K riktningar, phi_k = 2pi k/K)
    till godtyckliga azimuter. Returnerar vinklar med samma form som `azimut_rad`."""
    k = len(h_k)
    pos = (np.asarray(azimut_rad, dtype=np.float64) % (2.0 * math.pi)) * (k / (2.0 * math.pi))
    i0 = np.floor(pos).astype(np.int64) % k
    i1 = (i0 + 1) % k
    f = pos - np.floor(pos)
    h = np.asarray(h_k, dtype=np.float64)
    return np.asarray(h[i0] * (1.0 - f) + h[i1] * f, dtype=np.float64)


class SoltimmarAckumulator:
    """Strömmande soltimmar: matas med horisontvinkeln för riktning 0, 1, ..., K-1 i ordning.

    Solen syns vid ett tidssteg om solhöjden (geometrisk, centrum av solskivan) är större än
    horisontvinkeln linjärt interpolerad till solens azimut mellan de två närmaste riktningarna.
    Minnet är O(punkter), oberoende av K.
    """

    def __init__(
        self,
        k: int,
        sol_hojd_rad: Falt,
        sol_azimut_rad: Falt,
        steg_h: float,
        form: tuple[int, ...],
    ) -> None:
        if k < 4:
            raise MattFel(f"K måste vara >= 4, fick {k}.")
        if not (math.isfinite(steg_h) and steg_h > 0):
            raise MattFel(f"steg_h måste vara > 0, fick {steg_h}.")
        hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
        az = np.asarray(sol_azimut_rad, dtype=np.float64) % (2.0 * math.pi)
        if hojd.shape != az.shape or hojd.ndim != 1:
            raise MattFel("Solhöjd och solazimut måste vara endimensionella och lika långa.")
        self._k = k
        self._steg_h = steg_h
        upp = hojd > 0.0
        pos = az[upp] * (k / (2.0 * math.pi))
        self._hojd = hojd[upp]
        self._seg = np.floor(pos).astype(np.int64) % k
        self._frak = pos - np.floor(pos)
        akt: set[int] = set()
        for s in self._seg.tolist():
            si = int(s)
            akt.add(si)
            akt.add((si + 1) % k)
        self._aktiva = frozenset(akt)
        self._antal = np.zeros(form, dtype=np.int32)
        self._forra: NDArray[np.float64] | None = None
        self._h0: NDArray[np.float64] | None = None
        self._nasta = 0
        self._klar = False

    def aktiva_riktningar(self) -> frozenset[int]:
        """Index för de riktningar 0..K-1 som faktiskt används av någon solposition."""
        return self._aktiva

    def _segment(self, s: int, h_a: Falt | None, h_b: Falt | None) -> None:
        traffar = np.nonzero(self._seg == s)[0]
        if traffar.size == 0:
            return
        if h_a is None or h_b is None:
            raise MattFel(f"Saknar horisontdata för aktivt solsegment {s}.")
        for m in traffar:
            f = self._frak[m]
            hh = h_a * (1.0 - f) + h_b * f
            self._antal += (self._hojd[m] > hh).astype(np.int32)

    def mata(self, k: int, h_rad: NDArray[Any] | None) -> None:
        if k != self._nasta:
            raise MattFel(f"Riktningarna måste matas i ordning, väntade {self._nasta}, fick {k}.")
        if h_rad is None:
            if k in self._aktiva:
                raise MattFel(f"Riktning {k} är aktiv för solen och kan inte hoppas över.")
            h: NDArray[np.float64] | None = None
        else:
            h = np.asarray(h_rad, dtype=np.float64)
            if h.shape != self._antal.shape:
                raise MattFel(f"Formen {h.shape} stämmer inte med {self._antal.shape}.")
        if k == 0:
            self._h0 = h
        else:
            self._segment(k - 1, self._forra, h)
        self._forra = h
        self._nasta += 1

    def timmar(self) -> Falt:
        """Soltimmar (h) när alla K riktningar matats."""
        if self._nasta != self._k:
            raise MattFel(f"Bara {self._nasta} av {self._k} riktningar har matats.")
        if not self._klar:
            self._segment(self._k - 1, self._forra, self._h0)
            self._klar = True
        return np.asarray(self._antal * self._steg_h, dtype=np.float64)


def soltimmar_fran_profiler(
    h_k: NDArray[Any],
    sol_hojd_rad: Falt,
    sol_azimut_rad: Falt,
    steg_h: float,
    *,
    tidsmask: NDArray[np.bool_] | None = None,
) -> Falt:
    """Soltimmar för punkter med färdiga profiler h_k av formen (K, ...).

    Med `tidsmask` (boolsk 1D-vektor av samma längd som solpositionerna) räknas bara de valda
    tidpunkterna, t.ex. ett tidsfönster (brukstid 09:00–17:00).
    """
    hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
    az = np.asarray(sol_azimut_rad, dtype=np.float64)
    if tidsmask is not None:
        m = np.asarray(tidsmask, dtype=bool)
        if m.shape != hojd.shape:
            raise MattFel(f"tidsmask har formen {m.shape}, förväntade {hojd.shape}.")
        hojd = hojd[m]
        az = az[m]
    ack = SoltimmarAckumulator(h_k.shape[0], hojd, az, steg_h, h_k.shape[1:])
    for k in range(h_k.shape[0]):
        ack.mata(k, h_k[k])
    return ack.timmar()


def tidsfonster_mask(
    steg_min: float,
    start_timme: float = 0.0,
    slut_timme: float = 24.0,
) -> NDArray[np.bool_]:
    """Boolsk mask för tidssteg ur `dygnsfonster_utc` inom `[start_timme, slut_timme)`.

    Exempel: `start_timme=9.0, slut_timme=17.0` väljer brukstid kl. 09–17 (lokal medelsoltid).
    """
    if not (math.isfinite(steg_min) and steg_min > 0.0):
        raise MattFel(f"steg_min måste vara > 0, fick {steg_min}.")
    antal_f = 1440.0 / steg_min
    if abs(antal_f - round(antal_f)) > 1e-9:
        raise MattFel(f"1440 måste vara delbart med steg_min={steg_min}.")
    if not (
        math.isfinite(start_timme)
        and math.isfinite(slut_timme)
        and 0.0 <= start_timme < slut_timme <= 24.0
    ):
        raise MattFel(
            f"Tidsfönstret måste uppfylla 0 <= start_timme < slut_timme <= 24, "
            f"fick [{start_timme}, {slut_timme}]."
        )
    antal = int(round(antal_f))
    t_h = (np.arange(antal, dtype=np.float64) + 0.5) * (steg_min / 60.0)
    return np.asarray((t_h >= start_timme) & (t_h < slut_timme), dtype=bool)


def solsynlighet_fran_profiler(
    h_k: NDArray[Any],
    sol_hojd_rad: Falt,
    sol_azimut_rad: Falt,
) -> NDArray[np.bool_]:
    """Vektoriserad solsynlighet (T, *form) direkt ur färdiga horisontprofiler h_k (K, *form).

    `True` när solen ligger över horisonten (`sol_hojd > 0`) och över den cirkulärt linjärt
    interpolerade horisontvinkeln i solens azimutriktning.
    """
    h = np.asarray(h_k, dtype=np.float64)
    if h.ndim < 1 or h.shape[0] < 4:
        raise MattFel(f"Horisontprofilen måste ha minst 4 riktningar längs axel 0, fick {h.shape}.")
    hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
    az = np.asarray(sol_azimut_rad, dtype=np.float64) % (2.0 * math.pi)
    if hojd.shape != az.shape or hojd.ndim != 1:
        raise MattFel("Solhöjd och solazimut måste vara endimensionella och lika långa.")
    k = h.shape[0]
    form = h.shape[1:]
    if hojd.size == 0:
        return np.zeros((0, *form), dtype=bool)
    pos = az * (k / (2.0 * math.pi))
    i0 = np.floor(pos).astype(np.int64) % k
    i1 = (i0 + 1) % k
    extra_axlar = (1,) * len(form)
    f = (pos - np.floor(pos)).reshape((hojd.size, *extra_axlar))
    h_interp = h[i0] * (1.0 - f) + h[i1] * f
    hojd_bc = hojd.reshape((hojd.size, *extra_axlar))
    return np.asarray((hojd_bc > 0.0) & (hojd_bc > h_interp), dtype=bool)


def sammanhangande_soltimmar_fran_synlighet(
    synlig: NDArray[np.bool_],
    steg_h: float,
) -> Falt:
    """Längsta sammanhängande solperiod (h) längs tidsaxeln 0 ur en boolsk synlighetsmatris."""
    if not (math.isfinite(steg_h) and steg_h > 0.0):
        raise MattFel(f"steg_h måste vara > 0, fick {steg_h}.")
    s = np.asarray(synlig, dtype=bool)
    if s.ndim < 1:
        raise MattFel("synlig måste ha minst en tidsaxel (axel 0).")
    form = s.shape[1:]
    aktuell = np.zeros(form, dtype=np.int32)
    basta = np.zeros(form, dtype=np.int32)
    for t in range(s.shape[0]):
        aktuell = np.where(s[t], aktuell + 1, 0)
        basta = np.maximum(basta, aktuell)
    return np.asarray(basta * steg_h, dtype=np.float64)


def sammanhangande_soltimmar_mark(
    h_k: NDArray[Any],
    sol_hojd_rad: Falt,
    sol_azimut_rad: Falt,
    steg_h: float,
    *,
    tidsmask: NDArray[np.bool_] | None = None,
) -> Falt:
    """Längsta sammanhängande solperiod (h) på marken ur horisontprofiler `h_k` av formen (K, ...).

    Ren vektoriserad NumPy ovanpå befintliga horisontprofiler (inga extra strålgångar).
    Med `tidsmask` begränsas perioden till t.ex. ett brukstidsfönster.
    """
    hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
    az = np.asarray(sol_azimut_rad, dtype=np.float64)
    if tidsmask is not None:
        m = np.asarray(tidsmask, dtype=bool)
        if m.shape != hojd.shape:
            raise MattFel(f"tidsmask har formen {m.shape}, förväntade {hojd.shape}.")
        hojd = hojd[m]
        az = az[m]
    synlig = solsynlighet_fran_profiler(h_k, hojd, az)
    return sammanhangande_soltimmar_fran_synlighet(synlig, steg_h)


def skuggmask_vid_klockslag(
    h_k: NDArray[Any],
    sol_hojd_rad: Falt | float,
    sol_azimut_rad: Falt | float,
) -> NDArray[np.uint8]:
    """Klockslags-skuggmask (`uint8`: 1 = sol, 0 = skugga) ur horisontprofiler `h_k` (K, ...).

    Tar antingen skalära solpositioner (ger form `h_k.shape[1:]`) eller 1D-vektorer med M klockslag
    (ger form `(M, *h_k.shape[1:])`).
    """
    hojd_arr = np.asarray(sol_hojd_rad, dtype=np.float64)
    az_arr = np.asarray(sol_azimut_rad, dtype=np.float64)
    skalar = hojd_arr.ndim == 0 and az_arr.ndim == 0
    if hojd_arr.shape != az_arr.shape:
        raise MattFel("Solhöjd och solazimut måste ha samma form.")
    hojd_1d = np.atleast_1d(hojd_arr)
    az_1d = np.atleast_1d(az_arr)
    synlig = solsynlighet_fran_profiler(h_k, hojd_1d, az_1d)
    ut = synlig.astype(np.uint8)
    return np.asarray(ut[0], dtype=np.uint8) if skalar else ut


def solsynlighet_fasad(
    h_profil: NDArray[Any],
    normal_azimut_rad: Falt | float,
    sol_hojd_rad: Falt,
    sol_azimut_rad: Falt,
    *,
    hel_horisont: bool = False,
) -> NDArray[np.bool_]:
    """Vektoriserad solsynlighet (T, N) på vertikala fasadpunkter (K11).

    Solen träffar fasaden när:
    1. Solen står över horisonten (`sol_hojd > 0`),
    2. Solen befinner sig i fasadens synfält (`cos(sol_az - normal_az) > 0`, dvs. |dpsi| < pi/2),
    3. Solhöjden är större än horisontvinkeln i solens azimutriktning.

    Standard (`hel_horisont=False`): `h_profil` har formen `(N, K_f)` (eller `(K_f,)` för en punkt)
    i mittpunktsriktningar över halvplanet `psi_j = -pi/2 + (j + 0.5) * pi / K_f` (samma som VSC).
    Med `hel_horisont=True`: `h_profil` har formen `(K, N)` (360-gradersprofil, `phi_k = 2pi k/K`).
    """
    hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
    az = np.asarray(sol_azimut_rad, dtype=np.float64) % (2.0 * math.pi)
    if hojd.shape != az.shape or hojd.ndim != 1:
        raise MattFel("Solhöjd och solazimut måste vara endimensionella och lika långa.")
    n_az = np.atleast_1d(np.asarray(normal_azimut_rad, dtype=np.float64)) % (2.0 * math.pi)
    if n_az.ndim != 1:
        raise MattFel("normal_azimut_rad måste vara skalär eller endimensionell.")
    n_pts = n_az.size
    t_len = hojd.size
    h = np.asarray(h_profil, dtype=np.float64)
    if not hel_horisont:
        if h.ndim == 1:
            h = h[None, :]
        if h.ndim != 2 or h.shape[0] != n_pts or h.shape[1] < 2:
            raise MattFel(
                f"Halvplansprofilen måste ha formen ({n_pts}, K_f) med K_f >= 2, fick {h.shape}."
            )
    else:
        if h.ndim == 1:
            h = h[:, None]
        if h.ndim != 2 or h.shape[1] != n_pts or h.shape[0] < 4:
            raise MattFel(
                f"Helhorisontprofilen måste ha formen (K, {n_pts}) med K >= 4, fick {h.shape}."
            )
    if t_len == 0 or n_pts == 0:
        return np.zeros((t_len, n_pts), dtype=bool)
    # Relativ azimut dpsi i [-pi, pi) med form (T, N).
    dpsi = (az[:, None] - n_az[None, :] + math.pi) % (2.0 * math.pi) - math.pi
    framfor = (hojd[:, None] > 0.0) & (np.abs(dpsi) < (math.pi / 2.0 - 1e-12))
    if hel_horisont:
        synlig_hel = solsynlighet_fran_profiler(h, hojd, az)
        return np.asarray(framfor & synlig_hel, dtype=bool)
    kf = h.shape[1]
    # Mittpunktsriktningarna ligger vid psi_j = -pi/2 + (j + 0.5) * pi / kf.
    u = np.clip((dpsi + math.pi / 2.0) * (kf / math.pi) - 0.5, 0.0, float(kf - 1))
    i0 = np.floor(u).astype(np.int64)
    i1 = np.minimum(i0 + 1, kf - 1)
    f = u - i0
    rad_idx = np.arange(n_pts, dtype=np.int64)[None, :]
    h_interp = h[rad_idx, i0] * (1.0 - f) + h[rad_idx, i1] * f
    return np.asarray(framfor & (hojd[:, None] > h_interp), dtype=bool)


def soltimmar_fasad(
    h_profil: NDArray[Any],
    normal_azimut_rad: Falt | float,
    sol_hojd_rad: Falt,
    sol_azimut_rad: Falt,
    steg_h: float,
    *,
    hel_horisont: bool = False,
    tidsmask: NDArray[np.bool_] | None = None,
) -> Falt:
    """Direkta soltimmar (h) på vertikala fasadpunkter (K11) ur horisontprofil och fasadnormal."""
    if not (math.isfinite(steg_h) and steg_h > 0.0):
        raise MattFel(f"steg_h måste vara > 0, fick {steg_h}.")
    hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
    az = np.asarray(sol_azimut_rad, dtype=np.float64)
    if tidsmask is not None:
        m = np.asarray(tidsmask, dtype=bool)
        if m.shape != hojd.shape:
            raise MattFel(f"tidsmask har formen {m.shape}, förväntade {hojd.shape}.")
        hojd = hojd[m]
        az = az[m]
    synlig = solsynlighet_fasad(
        h_profil, normal_azimut_rad, hojd, az, hel_horisont=hel_horisont
    )
    return np.asarray(synlig.sum(axis=0, dtype=np.int32) * steg_h, dtype=np.float64)


class SolsynlighetAckumulator:
    """Strömmande solsynlighet `(T, *form)` per tidssteg från horisontriktningar 0..K-1.

    Används av sammanhängande soltid och klockslags-skuggmask så att bara aktiva solriktningar
    behöver strålgångsberäknas i `_horisontpass`.
    """

    def __init__(
        self,
        k: int,
        sol_hojd_rad: Falt,
        sol_azimut_rad: Falt,
        form: tuple[int, ...],
    ) -> None:
        if k < 4:
            raise MattFel(f"K måste vara >= 4, fick {k}.")
        hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
        az = np.asarray(sol_azimut_rad, dtype=np.float64) % (2.0 * math.pi)
        if hojd.shape != az.shape or hojd.ndim != 1:
            raise MattFel("Solhöjd och solazimut måste vara endimensionella och lika långa.")
        self._k = k
        self._t = int(hojd.size)
        self._form = form
        upp_idx = np.nonzero(hojd > 0.0)[0]
        pos = az[upp_idx] * (k / (2.0 * math.pi))
        self._upp_idx = upp_idx
        self._hojd = hojd[upp_idx]
        self._seg = np.floor(pos).astype(np.int64) % k
        self._frak = pos - np.floor(pos)
        akt: set[int] = set()
        for s in self._seg.tolist():
            si = int(s)
            akt.add(si)
            akt.add((si + 1) % k)
        self._aktiva = frozenset(akt)
        self._synlig = np.zeros((self._t, *form), dtype=bool)
        self._forra: NDArray[np.float64] | None = None
        self._h0: NDArray[np.float64] | None = None
        self._nasta = 0
        self._klar = False

    def aktiva_riktningar(self) -> frozenset[int]:
        return self._aktiva

    def _segment(self, s: int, h_a: Falt | None, h_b: Falt | None) -> None:
        traffar = np.nonzero(self._seg == s)[0]
        if traffar.size == 0:
            return
        if h_a is None or h_b is None:
            raise MattFel(f"Saknar horisontdata för aktivt solsegment {s}.")
        for m in traffar:
            f = self._frak[m]
            hh = h_a * (1.0 - f) + h_b * f
            self._synlig[int(self._upp_idx[m])] = self._hojd[m] > hh

    def mata(self, k: int, h_rad: NDArray[Any] | None) -> None:
        if k != self._nasta:
            raise MattFel(f"Riktningarna måste matas i ordning, väntade {self._nasta}, fick {k}.")
        if h_rad is None:
            if k in self._aktiva:
                raise MattFel(f"Riktning {k} är aktiv för solen och kan inte hoppas över.")
            h: NDArray[np.float64] | None = None
        else:
            h = np.asarray(h_rad, dtype=np.float64)
            if h.shape != self._form:
                raise MattFel(f"Formen {h.shape} stämmer inte med {self._form}.")
        if k == 0:
            self._h0 = h
        else:
            self._segment(k - 1, self._forra, h)
        self._forra = h
        self._nasta += 1

    def synlighet(self) -> NDArray[np.bool_]:
        """Boolsk synlighetsmatris `(T, *form)` när alla K riktningar matats."""
        if self._nasta != self._k:
            raise MattFel(f"Bara {self._nasta} av {self._k} riktningar har matats.")
        if not self._klar:
            self._segment(self._k - 1, self._forra, self._h0)
            self._klar = True
        return self._synlig
