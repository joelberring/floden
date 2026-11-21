# SPDX-License-Identifier: MIT
"""Spridning som linjärt problem på ett sparat medelvindfält (`docs/LUFT.md` 2.2).

Stationär advektion–diffusion för halten ``c`` (g/m³)::

    ∇·(u c) − ∇·(K ∇c) + λ c = s,      K = ν_t / Sc_t + D

diskretiseras med finita volymer på vindfältets `Voxelgrid`. Bara luftceller är
obekanta; de numreras med en indexkarta (``indexkarta``, −1 i fasta celler).

Ytor och flöden
---------------
* Ytflöden ``F = u_yta · dx²`` (m³/s) tas ur cellcentrumhastigheterna: medel av
  de två grannarnas normalkomponent. Mot fast cell och mot marken under ``k = 0``
  är flödet 0.
* Domänränder: inflöde ``i = 0`` (flödet = första cellens ``u``), utflöde
  ``i = nx−1`` och tak ``k = nz−1`` (flödet = randcellens normalkomponent).
  Sidorna i y är periodiska (standard, som i LBM) eller, om
  ``periodisk_y=False``, öppna ränder av samma slag som utflödet.
* **Projektion** (``projicera=True``): ytflödena görs diskret divergensfria,
  ``F' = F − (φ_R − φ_L)``, där φ löser Poissonekvationen ``L φ = −div F`` på
  luftcellerna. Randvillkor: inflödet har givet flöde (Neumann, ingen
  korrektion), utflöde, tak och öppna sidor har φ = 0 utanför randen
  (Dirichlet), så att korrektionen kan föra flöde genom dem. Varje luftcells
  nettoflöde blir då 0 till lösarens precision (``divergens_rel``).
* **Sedimentation** ``w_s`` läggs på de vertikala ytflödena *efter* projektionen
  (−w_s·dx² på luft–luft-ytor och taket). Ytor ovanpå mark/fast cell får
  utflödet ``w_s·dx²`` (räknas som deposition); undersidan av en fast cell ger 0.

Operatorn
---------
* Uppströms advektion (första ordningen) i konservativ form, diffusion med
  harmoniskt medel av ``K`` på ytan, konduktans ``K_yta · dx``.
* Deposition: sänka ``v_d · dx²`` på varje yta mot fast cell eller mark.
* Nedbrytning: ``λ · dx³`` på diagonalen.
* Inflöde: ``c = 0`` (bakgrund adderas separat). Advektivt inflöde ger ingen
  term; diffusion mot randen ger konduktansen ``2 K_P dx`` (Dirichlet på ytan).
* Utflöde, tak och öppna sidor: advektivt utflöde ``max(F, 0) c_P``, ingen
  diffusion (nollgradient). Inflöde genom dessa ränder bär ``c = 0``.

Eftersom varje yta läggs in konservativt (det som lämnar en cell kommer in i
grannen) är varje kolumnsumma i ``A`` lika med cellens förluster ut ur domänen
(utflöde, deposition, nedbrytning) ≥ 0, och alla element utanför diagonalen är
≤ 0. ``A`` är alltså en M-matris (kolumnvis diagonaldominant): halter från
icke-negativa källor blir aldrig negativa och massbalansen är exakt.

Modulen importerar bara numpy, scipy och ``luft.*`` (ingen SFINCS-kod), så att
luftdelen kan licensieras separat.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp
import scipy.sparse.csgraph as csgraph
import scipy.sparse.linalg as spla

from luft.typer import FloatArray, LuftFel, Vindfalt

IntArray = npt.NDArray[np.int64]

_ALLA_LOSARE = ("auto", "direkt", "iterativ")


@dataclass(frozen=True)
class Amne:
    """Ett spårämne (gas eller partikelfraktion) för transportoperatorn.

    sc_t: turbulent Schmidttal (K_t = ν_t / sc_t).
    diffusivitet_m2_s: molekylär diffusivitet D.
    deposition_m_s: depositionshastighet v_d mot mark och fasta ytor.
    sedimentation_m_s: sedimentationshastighet w_s (nedåt, ≥ 0).
    nedbrytning_1_s: första ordningens nedbrytning λ.
    """

    namn: str
    sc_t: float = 0.7
    diffusivitet_m2_s: float = 2e-5
    deposition_m_s: float = 0.0
    sedimentation_m_s: float = 0.0
    nedbrytning_1_s: float = 0.0

    def __post_init__(self) -> None:
        if not self.namn.strip():
            raise LuftFel("Amne.namn får inte vara tomt.")
        varden = {
            "sc_t": self.sc_t,
            "diffusivitet_m2_s": self.diffusivitet_m2_s,
            "deposition_m_s": self.deposition_m_s,
            "sedimentation_m_s": self.sedimentation_m_s,
            "nedbrytning_1_s": self.nedbrytning_1_s,
        }
        for namn, varde in varden.items():
            if not math.isfinite(varde):
                raise LuftFel(f"Amne.{namn} måste vara ändligt (fick {varde}).")
            if varde < 0:
                raise LuftFel(f"Amne.{namn} får inte vara negativ (fick {varde}).")
        if self.sc_t <= 0:
            raise LuftFel("Amne.sc_t måste vara positiv.")

    @property
    def har_linjar_hastighetsskalning(self) -> bool:
        """Sant om A(fU) = f·A(U) gäller (ingen deposition, sedimentation, nedbrytning)."""
        return (
            self.deposition_m_s == 0.0
            and self.sedimentation_m_s == 0.0
            and self.nedbrytning_1_s == 0.0
        )


def _par(lin: IntArray, axel: int, periodisk: bool) -> tuple[IntArray, IntArray]:
    """Linjära index (L, R) för alla grannpar längs `axel` (R = L + 1 längs axeln)."""
    n = lin.shape[axel]
    if periodisk and n > 1:
        r = np.roll(lin, -1, axis=axel)
        return lin.ravel(), r.ravel()
    sl_l = [slice(None)] * 3
    sl_r = [slice(None)] * 3
    sl_l[axel] = slice(0, n - 1)
    sl_r[axel] = slice(1, n)
    return lin[tuple(sl_l)].ravel(), lin[tuple(sl_r)].ravel()


def _skiva(lin: IntArray, axel: int, index: int) -> IntArray:
    sl = [slice(None)] * 3
    sl[axel] = slice(index, index + 1)
    return lin[tuple(sl)].ravel()


class TransportOperator:
    """Glesa operatorn A för stationär spridning på ett `Vindfalt`.

    Parametrar
    ----------
    vind, amne: vindfält och ämne.
    projicera: gör ytflödena divergensfria (Helmholtz-projektion), se modultexten.
    periodisk_y: periodiska sidor i y (standard, som LBM); annars öppna ränder.
    troskel_direkt: system med högst så många obekanta löses direkt (splu),
        större med ILU-förkonditionerad bicgstab/gmres. Gäller både Poisson-
        ekvationen och transportsystemet. Standard 50 000: uppmätt 2026-11-21
        tog splu (MMD-ordning) ≈ 7 s för 57 600 obekanta medan ILU + bicgstab
        tog ≈ 4 s; 3D-fyllnaden gör direktlösning dyr långt under 2·10⁵.
    tolerans: relativ residualtolerans för iterativa lösare.
    max_iter: högsta antal iterationer för iterativa lösare.
    max_divergens_rel: största tillåtna relativa divergens efter projektion.

    Attribut
    --------
    matris: A som ``scipy.sparse.csr_matrix`` (n × n), A c = s med c i g/m³ och
        s i g/s per cell.
    indexkarta: (nx, ny, nz) int64, index för luftceller, −1 i fasta celler.
    divergens_rel: max |nettoflöde per luftcell| / max |ytflöde| efter
        projektionen (före sedimentation). NaN om ``projicera=False``.
    """

    def __init__(
        self,
        vind: Vindfalt,
        amne: Amne,
        projicera: bool = True,
        *,
        periodisk_y: bool = True,
        troskel_direkt: int = 50_000,
        tolerans: float = 1e-10,
        max_iter: int = 5_000,
        max_divergens_rel: float = 1e-10,
    ) -> None:
        if troskel_direkt < 0 or tolerans <= 0 or max_iter <= 0:
            raise LuftFel("troskel_direkt ≥ 0, tolerans > 0 och max_iter > 0 krävs.")
        self.vind = vind
        self.amne = amne
        self.projicera = projicera
        self.periodisk_y = periodisk_y
        self.troskel_direkt = int(troskel_direkt)
        self.tolerans = float(tolerans)
        self.max_iter = int(max_iter)
        self.max_divergens_rel = float(max_divergens_rel)

        grid = vind.grid
        self.form = grid.form
        nx, ny, nz = self.form
        dx = float(grid.dx_m)
        self.dx_m = dx
        self.cellvolym_m3 = dx**3
        yta_m2 = dx * dx

        luft = grid.luft.ravel()
        self._luft = luft
        n = int(np.count_nonzero(luft))
        if n == 0:
            raise LuftFel("Rutnätet saknar luftceller.")
        self.antal_obekanta = n
        idx = np.full(luft.size, -1, dtype=np.int64)
        idx[luft] = np.arange(n, dtype=np.int64)
        self.indexkarta: IntArray = idx.reshape(self.form)

        u = [np.asarray(vind.u_m_s[a], dtype=np.float64).ravel() for a in range(3)]
        k_cell = (
            np.asarray(vind.nu_t_m2_s, dtype=np.float64).ravel() / amne.sc_t
            + amne.diffusivitet_m2_s
        )
        self._k_luft: FloatArray = k_cell[luft]
        lin: IntArray = np.arange(luft.size, dtype=np.int64).reshape(self.form)

        # --- inre ytor (luft–luft) och väggytor (luft–fast) -------------------
        inre_l: list[IntArray] = []
        inre_r: list[IntArray] = []
        inre_f: list[FloatArray] = []
        inre_g: list[FloatArray] = []
        inre_vertikal: list[npt.NDArray[np.bool_]] = []
        vagg_p: list[IntArray] = []  # luftcell vid vägg (inkl. mark)
        vagg_under: list[npt.NDArray[np.bool_]] = []  # fast cell/mark under cellen
        for axel in range(3):
            periodisk = axel == 1 and periodisk_y
            if periodisk and self.form[1] == 1:
                continue  # periodisk med ny = 1: inga ytor i y
            gl, gr = _par(lin, axel, periodisk)
            al, ar = luft[gl], luft[gr]
            bada = al & ar
            gl_b, gr_b = gl[bada], gr[bada]
            inre_l.append(idx[gl_b])
            inre_r.append(idx[gr_b])
            inre_f.append(0.5 * (u[axel][gl_b] + u[axel][gr_b]) * yta_m2)
            kl, kr = k_cell[gl_b], k_cell[gr_b]
            summa = kl + kr
            harm = np.divide(
                2.0 * kl * kr, summa, out=np.zeros_like(summa), where=summa > 0
            )
            inre_g.append(harm * dx)
            inre_vertikal.append(np.full(gl_b.size, axel == 2))
            # vägg på +sidan av L
            vl = al & ~ar
            vagg_p.append(idx[gl[vl]])
            vagg_under.append(np.zeros(int(vl.sum()), dtype=np.bool_))
            # vägg på −sidan av R
            vr = ~al & ar
            vagg_p.append(idx[gr[vr]])
            vagg_under.append(np.full(int(vr.sum()), axel == 2))
        # mark under k = 0
        mark = _skiva(lin, 2, 0)
        mark = mark[luft[mark]]
        vagg_p.append(idx[mark])
        vagg_under.append(np.ones(mark.size, dtype=np.bool_))

        self._inre_l = np.concatenate(inre_l) if inre_l else np.zeros(0, np.int64)
        self._inre_r = np.concatenate(inre_r) if inre_r else np.zeros(0, np.int64)
        inre_flode = np.concatenate(inre_f) if inre_f else np.zeros(0)
        self._inre_g = np.concatenate(inre_g) if inre_g else np.zeros(0)
        vertikal = np.concatenate(inre_vertikal) if inre_vertikal else np.zeros(0, bool)
        self._vagg_p = np.concatenate(vagg_p)
        self._vagg_under = np.concatenate(vagg_under)

        # --- domänränder --------------------------------------------------------
        # (cell, utåtriktat flöde, typ) med typ 0 = inflöde (Neumann), 1 = öppen (Dirichlet)
        rand_p: list[IntArray] = []
        rand_f: list[FloatArray] = []
        rand_typ: list[IntArray] = []
        rand_tak: list[npt.NDArray[np.bool_]] = []

        def lagg_rand(celler: IntArray, axel: int, tecken: float, typ: int, tak: bool) -> None:
            celler = celler[luft[celler]]
            rand_p.append(idx[celler])
            rand_f.append(tecken * u[axel][celler] * yta_m2)
            rand_typ.append(np.full(celler.size, typ, dtype=np.int64))
            rand_tak.append(np.full(celler.size, tak))

        lagg_rand(_skiva(lin, 0, 0), 0, -1.0, 0, False)
        lagg_rand(_skiva(lin, 0, nx - 1), 0, 1.0, 1, False)
        if not periodisk_y:
            lagg_rand(_skiva(lin, 1, 0), 1, -1.0, 1, False)
            lagg_rand(_skiva(lin, 1, ny - 1), 1, 1.0, 1, False)
        lagg_rand(_skiva(lin, 2, nz - 1), 2, 1.0, 1, True)
        self._rand_p = np.concatenate(rand_p)
        rand_flode = np.concatenate(rand_f)
        self._rand_typ = np.concatenate(rand_typ)
        rand_tak_arr = np.concatenate(rand_tak)

        self._kontrollera_sammanhang()

        # --- projektion ---------------------------------------------------------
        self.divergens_rel = float("nan")
        if projicera:
            inre_flode, rand_flode = self._projicera(inre_flode, rand_flode)
        self._inre_f_projicerat = inre_flode.copy()
        self._rand_f_projicerat = rand_flode.copy()

        # --- sedimentation ------------------------------------------------------
        ws_a = amne.sedimentation_m_s * yta_m2
        if ws_a > 0:
            inre_flode = inre_flode - ws_a * vertikal
            rand_flode = rand_flode - ws_a * rand_tak_arr
        self._inre_f = inre_flode
        self._rand_f = rand_flode

        self.matris = self._bygg_matris(ws_a, yta_m2)
        self._lu: Any = None
        self._ilu: Any = None

    # ------------------------------------------------------------------ bygg

    def _kontrollera_sammanhang(self) -> None:
        """Varje sammanhängande luftområde måste nå en öppen rand (utflöde/tak/sida)."""
        n = self.antal_obekanta
        graf = sp.coo_matrix(
            (np.ones(self._inre_l.size), (self._inre_l, self._inre_r)), shape=(n, n)
        )
        antal, etikett = csgraph.connected_components(graf, directed=False)
        oppna = self._rand_p[self._rand_typ == 1]
        nar_rand = np.zeros(int(antal), dtype=np.bool_)
        nar_rand[np.asarray(etikett)[oppna]] = True
        if not np.all(nar_rand):
            instangda = int(np.isin(np.asarray(etikett), np.flatnonzero(~nar_rand)).sum())
            raise LuftFel(
                f"{instangda} luftceller ligger i slutna fickor utan förbindelse med "
                "utflöde/tak. Fyll dem som fasta i geometrin."
            )

    def _divergens(self, inre_f: FloatArray, rand_f: FloatArray) -> FloatArray:
        n = self.antal_obekanta
        d: FloatArray = np.asarray(
            np.bincount(self._inre_l, inre_f, minlength=n)
            - np.bincount(self._inre_r, inre_f, minlength=n)
            + np.bincount(self._rand_p, rand_f, minlength=n),
            dtype=np.float64,
        )
        return d

    def _projicera(
        self, inre_f: FloatArray, rand_f: FloatArray
    ) -> tuple[FloatArray, FloatArray]:
        n = self.antal_obekanta
        el, er = self._inre_l, self._inre_r
        oppen = self._rand_typ == 1
        diag = (
            np.bincount(el, minlength=n).astype(np.float64)
            + np.bincount(er, minlength=n)
            + np.bincount(self._rand_p[oppen], minlength=n)
        )
        rader = np.concatenate([el, er, np.arange(n)])
        kolumner = np.concatenate([er, el, np.arange(n)])
        varden = np.concatenate([-np.ones(el.size), -np.ones(er.size), diag])
        lap = sp.csr_matrix((varden, (rader, kolumner)), shape=(n, n))
        losare: Any = None
        skala = max(float(np.max(np.abs(inre_f), initial=0.0)),
                    float(np.max(np.abs(rand_f), initial=0.0)))
        if skala == 0.0:
            self.divergens_rel = 0.0
            return inre_f, rand_f
        for _ in range(4):  # iterativ förfining
            div = self._divergens(inre_f, rand_f)
            if float(np.max(np.abs(div))) <= 1e-15 * skala:
                break
            if losare is None:
                losare = self._poissonlosare(lap)
            phi = losare(-div)
            inre_f = inre_f - (phi[er] - phi[el])
            rand_f = rand_f + np.where(oppen, phi[self._rand_p], 0.0)
        skala = max(float(np.max(np.abs(inre_f), initial=0.0)),
                    float(np.max(np.abs(rand_f), initial=0.0)))
        div = self._divergens(inre_f, rand_f)
        self.divergens_rel = float(np.max(np.abs(div))) / skala
        if self.divergens_rel > self.max_divergens_rel:
            raise LuftFel(
                f"Projektionen nådde bara relativ divergens {self.divergens_rel:.2e} "
                f"(gräns {self.max_divergens_rel:.0e})."
            )
        return inre_f, rand_f

    def _poissonlosare(self, lap: Any) -> Any:
        """Returnerar en funktion b → φ för den symmetriska, positivt definita L."""
        n = self.antal_obekanta
        if n <= self.troskel_direkt:
            lu = spla.splu(sp.csc_matrix(lap), permc_spec="MMD_AT_PLUS_A")
            return lu.solve
        try:
            pyamg = importlib.import_module("pyamg")
        except ImportError:
            pyamg = None
        if pyamg is not None:  # pragma: no cover - pyamg krävs inte
            ml = pyamg.smoothed_aggregation_solver(lap)

            def los_amg(b: FloatArray) -> FloatArray:
                x: FloatArray = ml.solve(b, tol=1e-13, accel="cg", maxiter=self.max_iter)
                return x

            return los_amg
        dinv = 1.0 / lap.diagonal()
        m_op = spla.LinearOperator((n, n), matvec=lambda v: dinv * v, dtype=np.float64)

        def los_cg(b: FloatArray) -> FloatArray:
            x, info = spla.cg(lap, b, rtol=1e-13, atol=0.0, M=m_op, maxiter=20 * self.max_iter)
            if info != 0:
                raise LuftFel(f"CG för projektionen konvergerade inte (info={info}).")
            res: FloatArray = np.asarray(x, dtype=np.float64)
            return res

        return los_cg

    def _bygg_matris(self, ws_a: float, yta_m2: float) -> Any:
        n = self.antal_obekanta
        el, er = self._inre_l, self._inre_r
        f, g = self._inre_f, self._inre_g
        fp, fm = np.maximum(f, 0.0), np.maximum(-f, 0.0)

        # förluster ut ur domänen per luftcell (kolumnsummor)
        rand_ut = np.maximum(self._rand_f, 0.0)
        utflode = np.bincount(self._rand_p, rand_ut, minlength=n)
        inflode = self._rand_typ == 0
        p_in = self._rand_p[inflode]
        utflode += np.bincount(p_in, 2.0 * self._k_luft[p_in] * self.dx_m, minlength=n)
        vagg_koef = self.amne.deposition_m_s * yta_m2 + ws_a * self._vagg_under
        deposition = np.bincount(self._vagg_p, vagg_koef, minlength=n)
        nedbrytning = np.full(n, self.amne.nedbrytning_1_s * self.cellvolym_m3)
        self._utflode_koef = utflode
        self._deposition_koef = deposition
        self._nedbrytning_koef = nedbrytning

        diag = (
            np.bincount(el, fp + g, minlength=n)
            + np.bincount(er, fm + g, minlength=n)
            + utflode
            + deposition
            + nedbrytning
        )
        rader = np.concatenate([el, er, np.arange(n)])
        kolumner = np.concatenate([er, el, np.arange(n)])
        varden = np.concatenate([-(fm + g), -(fp + g), diag])
        a = sp.csr_matrix((varden, (rader, kolumner)), shape=(n, n))
        a.sum_duplicates()
        a.eliminate_zeros()
        return a

    # ------------------------------------------------------------- egenskaper

    def m_matris_marginal(self) -> dict[str, float]:
        """Kontroll av M-matrisegenskapen.

        Returnerar ``max_offdiagonal`` (ska vara ≤ 0) och ``min_kolumnmarginal_rel``:
        min över kolumner av (diagonal − Σ|offdiagonal|) / diagonal (ska vara ≥ 0
        upp till avrundning).
        """
        a = sp.coo_matrix(self.matris)
        av = a.row != a.col
        max_off = float(np.max(a.data[av], initial=-np.inf))
        n = self.antal_obekanta
        diag = np.asarray(self.matris.diagonal(), dtype=np.float64)
        off_abs = np.bincount(a.col[av], np.abs(a.data[av]), minlength=n)
        marg = (diag - off_abs) / np.where(diag > 0, diag, 1.0)
        return {"max_offdiagonal": max_off, "min_kolumnmarginal_rel": float(np.min(marg))}

    # --------------------------------------------------------------- lösning

    def _till_vektor(self, falt: FloatArray, namn: str) -> FloatArray:
        """(…, nx, ny, nz) → (n, m) över luftceller; kastar om värden ligger i fasta celler."""
        a = np.asarray(falt, dtype=np.float64)
        if a.shape == self.form:
            a = a[None]
        if a.ndim != 4 or a.shape[1:] != self.form:
            raise LuftFel(f"{namn} ska ha formen {self.form} eller (n, *{self.form}).")
        if not np.all(np.isfinite(a)):
            raise LuftFel(f"{namn} innehåller NaN/Inf.")
        platt = a.reshape(a.shape[0], -1)
        i_fast = platt[:, ~self._luft]
        if np.any(i_fast != 0):
            raise LuftFel(
                f"{namn} har {int(np.count_nonzero(i_fast))} värden ≠ 0 i fasta celler."
            )
        res: FloatArray = np.ascontiguousarray(platt[:, self._luft].T)
        return res

    def _till_falt(self, x: FloatArray, enkel: bool) -> FloatArray:
        m = x.shape[1]
        ut = np.zeros((m, self._luft.size), dtype=np.float64)
        ut[:, self._luft] = x.T
        falt: FloatArray = ut.reshape((m, *self.form))
        if enkel:
            enkel_falt: FloatArray = falt[0]
            return enkel_falt
        return falt

    def _direkt(self) -> bool:
        return self.antal_obekanta <= self.troskel_direkt

    def _losningsmatris(self, b: FloatArray, transponerad: bool) -> FloatArray:
        if self._direkt():
            if self._lu is None:
                try:
                    self._lu = spla.splu(
                        sp.csc_matrix(self.matris), permc_spec="MMD_AT_PLUS_A"
                    )
                except RuntimeError as exc:
                    raise LuftFel(f"Transportoperatorn är singulär: {exc}") from exc
            x: FloatArray = np.asarray(
                self._lu.solve(b, trans="T" if transponerad else "N"), dtype=np.float64
            )
            if not np.all(np.isfinite(x)):
                raise LuftFel("Direktlösningen gav NaN/Inf (singulär operator?).")
            return x
        return self._iterativ(b, transponerad)

    def _iterativ(self, b: FloatArray, transponerad: bool) -> FloatArray:
        n = self.antal_obekanta
        if self._ilu is None:
            try:
                self._ilu = spla.spilu(
                    sp.csc_matrix(self.matris), drop_tol=1e-5, fill_factor=10.0
                )
            except RuntimeError as exc:
                raise LuftFel(f"ILU-faktoriseringen misslyckades: {exc}") from exc
        ilu = self._ilu
        trans = "T" if transponerad else "N"
        mat = self.matris.T.tocsr() if transponerad else self.matris
        m_op = spla.LinearOperator(
            (n, n), matvec=lambda v: ilu.solve(v, trans=trans), dtype=np.float64
        )
        ut = np.zeros_like(b)
        for j in range(b.shape[1]):
            bj = b[:, j]
            norm_b = float(np.linalg.norm(bj))
            if norm_b == 0.0:
                continue
            x, info = spla.bicgstab(
                mat, bj, rtol=self.tolerans, atol=0.0, M=m_op, maxiter=self.max_iter
            )
            if info != 0 or not np.all(np.isfinite(x)):
                x, info = spla.gmres(
                    mat, bj, rtol=self.tolerans, atol=0.0, M=m_op,
                    restart=60, maxiter=self.max_iter,
                )
            res = float(np.linalg.norm(mat @ x - bj)) / norm_b
            if info != 0 or not math.isfinite(res) or res > 10.0 * self.tolerans:
                raise LuftFel(
                    f"Iterativ lösare konvergerade inte (info={info}, rel. residual {res:.2e})."
                )
            ut[:, j] = x
        return ut

    def los(self, kallor_g_s: FloatArray) -> FloatArray:
        """Löser A c = s.

        kallor_g_s: (nx, ny, nz) eller (m, nx, ny, nz), utsläpp i g/s per cell
            (måste vara 0 i fasta celler). Operatorn är linjär, så negativa
            värden (skillnadsscenarier) är tillåtna; icke-negativa källor ger
            icke-negativ halt.
        Returnerar halt i g/m³ med samma form, 0 i fasta celler. Faktorisering
        (splu) eller förkonditionerare (ILU) byggs första gången och återanvänds.
        """
        enkel = np.ndim(kallor_g_s) == 3
        b = self._till_vektor(kallor_g_s, "kallor_g_s")
        return self._till_falt(self._losningsmatris(b, False), enkel)

    def los_skalad(self, kallor_g_s: FloatArray, faktor: float) -> FloatArray:
        """Halt för vindfältet skalat med `faktor` (= U_ny / U_ref), utan ny faktorisering.

        Vid neutral skalning blir u → f·u och ν_t → f·ν_t, så utan deposition,
        sedimentation och nedbrytning är A(fU) = f·A(U) och c = A⁻¹ s / f.
        Molekylär diffusivitet D skalar inte; dess bidrag behandlas som om det
        skalade. Felet är högst av storleksordningen max(D/K)·|1 − 1/f|; om det
        överstiger 1e-3 kastas LuftFel. Med deposition, sedimentation eller
        nedbrytning kastas LuftFel: skapa då en ny operator med
        `skalad_operator(u_ref_m_s)` (bygger om ur ``vind.skalat``).
        """
        if not (math.isfinite(faktor) and faktor > 0):
            raise LuftFel("faktor måste vara positiv och ändlig.")
        if not self.amne.har_linjar_hastighetsskalning:
            raise LuftFel(
                "los_skalad kräver deposition = sedimentation = nedbrytning = 0 "
                "(annars är A inte linjär i U). Använd skalad_operator()."
            )
        k = self._k_luft
        d = self.amne.diffusivitet_m2_s
        andel = float(np.max(np.divide(d, k, out=np.ones_like(k), where=k > 0)))
        if d > 0 and andel * abs(1.0 - 1.0 / faktor) > 1e-3:
            raise LuftFel(
                f"Molekylär diffusivitet utgör upp till {andel:.1e} av K; skalningen "
                "blir inte exakt nog. Använd skalad_operator()."
            )
        res: FloatArray = self.los(kallor_g_s) / faktor
        return res

    def skalad_operator(self, u_ref_m_s: float) -> TransportOperator:
        """Ny operator (ny faktorisering) för samma riktning och referensvind `u_ref_m_s`."""
        return TransportOperator(
            self.vind.skalat(u_ref_m_s),
            self.amne,
            self.projicera,
            periodisk_y=self.periodisk_y,
            troskel_direkt=self.troskel_direkt,
            tolerans=self.tolerans,
            max_iter=self.max_iter,
            max_divergens_rel=self.max_divergens_rel,
        )

    def los_adjungerad(self, receptor: FloatArray) -> FloatArray:
        """Känslighetskarta för en receptor.

        receptor: (nx, ny, nz), vikter w ≥ 0 (0 i fasta celler), t.ex. 1 i
            receptorns celler. Receptorhalten definieras som det volymviktade
            medlet ``C_r = Σ w_i V_i c_i / Σ w_i V_i`` (alla V_i = dx³ här).
        Löser ``Aᵀ y = g`` med ``g_i = w_i V_i / Σ w_j V_j`` och returnerar y
        (0 i fasta celler) i enheten (g/m³ i receptorn) per (g/s utsläpp i
        cellen), så att ``C_r = Σ_i y_i s_i`` för varje källfält s.
        """
        w = np.asarray(receptor, dtype=np.float64)
        if w.shape != self.form:
            raise LuftFel(f"receptor ska ha formen {self.form}.")
        if np.any(w < 0):
            raise LuftFel("receptorvikterna får inte vara negativa.")
        rv = self._till_vektor(w, "receptor")
        summa = float(rv.sum())
        if summa <= 0:
            raise LuftFel("receptorn saknar vikt i luftceller.")
        g = rv / summa  # V lika i alla celler och tar ut sig
        return self._till_falt(self._losningsmatris(g, True), True)

    def massbalans(self, kallor_g_s: FloatArray, halt_g_m3: FloatArray) -> dict[str, float]:
        """Massbalans för en lösning: utsläpp = utflöde + deposition + nedbrytning.

        utflode_g_s omfattar advektivt utflöde genom alla öppna ränder och
        diffusion ut genom inflödesranden; deposition_g_s omfattar v_d och
        sedimentation mot mark/fasta ytor. rel_fel = |utsläpp − summa| / |utsläpp|.
        """
        if np.shape(kallor_g_s) != self.form or np.shape(halt_g_m3) != self.form:
            raise LuftFel(f"kallor_g_s och halt_g_m3 ska ha formen {self.form}.")
        s = self._till_vektor(kallor_g_s, "kallor_g_s")[:, 0]
        c = self._till_vektor(halt_g_m3, "halt_g_m3")[:, 0]
        utslapp = float(s.sum())
        ut = float(self._utflode_koef @ c)
        dep = float(self._deposition_koef @ c)
        ned = float(self._nedbrytning_koef @ c)
        nam = max(abs(utslapp), 1e-300)
        return {
            "utslapp_g_s": utslapp,
            "utflode_g_s": ut,
            "deposition_g_s": dep,
            "nedbrytning_g_s": ned,
            "rel_fel": abs(utslapp - (ut + dep + ned)) / nam,
        }
