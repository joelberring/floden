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
* Advektion i konservativ form med ytvärdet ``c_f`` på varje inre yta och
  flödet ``F c_f``. Två scheman (``schema``):

  - ``"uppstroms"`` (standard): ``c_f = c_U`` (uppströmscellen). Första
    ordningen; numerisk diffusion ≈ ``|u| dx / 2``.
  - ``"tvd"``: flödesbegränsat andra ordningens schema
    ``c_f = c_U + ½ ψ(r) (c_D − c_U)``, ``r = (c_U − c_UU) / (c_D − c_U)``
    (U = uppströms, D = nedströms, UU = cellen uppströms om U), med van Leer
    ``ψ = (r + |r|)/(1 + |r|)`` eller Koren
    ``ψ = max(0, min(2r, (1 + 2r)/3, 2))``. Begränsaren ψ **fryses** per yta ur
    ett referensfält (se nedan), så att operatorn förblir *linjär*. Ytor där UU
    saknas (vägg, mark, domänrand) och alla randytor är uppströms (ψ = 0).

* Diffusion med harmoniskt medel av ``K`` på ytan, konduktans ``g = K_yta · dx``.
* Deposition: sänka ``v_d · dx²`` på varje yta mot fast cell eller mark.
* Nedbrytning: ``λ · dx³`` på diagonalen.

Randvillkor (samma för båda schemana)
-------------------------------------
* Inflöde ``i = 0``: ``c = 0`` (bakgrund adderas separat). Advektivt inflöde ger
  ingen term; diffusion mot randen ger konduktansen ``2 K_P dx`` (Dirichlet på
  ytan).
* Utflöde ``i = nx−1``, tak och öppna sidor: advektivt utflöde ``max(F, 0) c_P``
  (uppströms), ingen diffusion (nollgradient). Inflöde genom dessa ränder bär
  ``c = 0``.
* Väggar och mark: inget advektivt flöde, ingen diffusion, bara deposition och
  sedimentation enligt ovan.

Konservering och positivitet
----------------------------
Varje yta läggs in konservativt (det som lämnar en cell kommer in i grannen),
oavsett ψ. Varje kolumnsumma i ``A`` är därför lika med cellens förluster ut ur
domänen (utflöde, deposition, nedbrytning) ≥ 0 och massbalansen är exakt för
båda schemana.

Med ``"uppstroms"`` är alla element utanför diagonalen ≤ 0, så ``A`` är en
M-matris (kolumnvis diagonaldominant): halter från icke-negativa källor blir
aldrig negativa. Med ``"tvd"`` får elementet (U, D) bidraget ``+|F| ψ/2 − g``;
enligt Godunovs sats kan inget linjärt schema av andra ordningen vara monotont
för alla källfält. Med ``m_matris_tak=True`` kapas ψ till ``2 g / |F|`` (= 2/Pe
för ytan) så att ``A`` förblir en M-matris och positiviteten är garanterad för
alla källor och för den adjungerade lösningen (standard); det ger full andra
ordning bara där cellpecletalet ≤ 2 och närmar sig uppströms vid högre Pe. Med
``m_matris_tak=False`` används den fulla begränsaren; positiviteten gäller då
bara vid fixpunkten (referensfältet), inte för andra källor eller för den
adjungerade, och kontrolleras därför i `TransportOperator.los` och
`TransportOperator.los_adjungerad` (`LuftFel` vid negativ halt).

Frusen begränsare och dualitet
------------------------------
TVD-begränsaren är olinjär i c. Vi väljer *frusen begränsare* (inte
defektkorrektion): ψ beräknas ur ett referensfält och hålls sedan fast, så att
``A_ψ`` är en vanlig linjär matris. Referensfältet tas fram i ``tvd_pass`` pass
ur referenskällorna ``s_ref``: ``c₀ = A_upp⁻¹ s_ref``, ``ψ₁ = ψ(c₀)``,
``c₁ = A_ψ₁⁻¹ s_ref``, ``ψ₂ = ψ(c₁)``, … (Picarditeration mot den olinjära
TVD-lösningen). Därefter gäller exakt, för den frysta operatorn:

* superposition: ``los(s₁ + s₂) = los(s₁) + los(s₂)`` för alla källfält,
* adjungerad dualitet: ``Σ w c / Σ w = Σ y s`` med ``y = los_adjungerad(w)``,
* hastighetsskalning (ψ beror bara av kvoter och av Pe, som inte ändras).

Begränsaren är anpassad till referensfältet: för källfält långt från
referensen är schemat fortfarande konservativt och andra ordningen där fältet
är slätt, men inte nödvändigtvis fritt från svängningar. ``los`` på en ofryst
TVD-operator fryser begränsaren ur sina egna källor (summan av |kolumnerna|);
``los_adjungerad`` på en ofryst TVD-operator kastar `LuftFel`.

Lösare
------
Små system (≤ ``troskel_direkt`` obekanta) löses direkt (splu), större med
Krylovmetod (bicgstab, gmres som reserv) och symmetrisk Gauss–Seidel som
förkonditionerare (``forkonditionering="sgs"``): ``M = (D + L) D⁻¹ (D + U)``,
där triangelfaktorerna tas direkt ur A utan utfyllnad. Eftersom cellerna
numreras med x (medvind) som långsammaste index löser ett Gauss–Seidel-svep
nästan advektionen exakt. ``Mᵀ`` ger förkonditioneraren för ``Aᵀ``
(adjungerad) utan ny faktorisering. ILU (spilu) finns kvar som reserv
(``"ilu"``, eller automatiskt om SGS inte konvergerar). Poissonekvationen i
projektionen löses med CG förkonditionerad med en geometrisk
aggregeringsmultigrid (`_AggregeringsMg`, inga nya beroenden).

Modulen importerar bara numpy, scipy och ``luft.*`` (ingen SFINCS-kod), så att
luftdelen kan licensieras separat.
"""

from __future__ import annotations

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

_ALLA_SCHEMAN = ("uppstroms", "tvd")
_ALLA_BEGRANSARE = ("van_leer", "koren")
_ALLA_FORKONDITIONERINGAR = ("auto", "sgs", "ilu")


def begransare_psi(r: FloatArray, namn: str) -> FloatArray:
    """Flödesbegränsaren ψ(r) (TVD-område: 0 ≤ ψ ≤ min(2r, 2) för r > 0, ψ = 0 annars).

    namn: ``"van_leer"`` (ψ = (r + |r|)/(1 + |r|)) eller ``"koren"``
    (ψ = max(0, min(2r, (1 + 2r)/3, 2))). Båda ger ψ(1) = 1 (andra ordningen
    i släta områden).
    """
    r = np.asarray(r, dtype=np.float64)
    if namn == "van_leer":
        res: FloatArray = (r + np.abs(r)) / (1.0 + np.abs(r))
        return res
    if namn == "koren":
        res = np.maximum(0.0, np.minimum(np.minimum(2.0 * r, (1.0 + 2.0 * r) / 3.0), 2.0))
        return res
    raise LuftFel(f"Okänd begränsare {namn!r}; välj bland {_ALLA_BEGRANSARE}.")


class _Sgs:
    """Symmetrisk Gauss–Seidel ``M = (D + L) D⁻¹ (D + U)`` för en gles kvadratisk matris.

    Triangelfaktorerna (D + L) och (D + U) tas direkt ur matrisen; de löses
    med SuperLU i naturlig ordning utan pivotering, alltså utan utfyllnad.
    ``losa(v, transponerad=True)`` ger ``M⁻ᵀ v`` (förkonditionerare för Aᵀ).
    """

    def __init__(self, a: Any) -> None:
        diag = np.asarray(a.diagonal(), dtype=np.float64)
        if not np.all(diag > 0):
            raise LuftFel("SGS kräver positiv diagonal.")
        self._diag = diag
        flaggor = {"SymmetricMode": True}
        self._nedre = spla.splu(
            sp.tril(a, format="csc"), permc_spec="NATURAL", diag_pivot_thresh=0.0,
            options=flaggor,
        )
        self._ovre = spla.splu(
            sp.triu(a, format="csc"), permc_spec="NATURAL", diag_pivot_thresh=0.0,
            options=flaggor,
        )
        n = diag.size
        identitet = np.arange(n)
        for faktor in (self._nedre, self._ovre):
            if not (
                np.array_equal(faktor.perm_r, identitet)
                and np.array_equal(faktor.perm_c, identitet)
            ):  # pragma: no cover - skyddsnät, SuperLU ska inte pivotera här
                raise LuftFel("SGS-faktoriseringen pivoterade oväntat.")

    def losa(self, v: FloatArray, transponerad: bool = False) -> FloatArray:
        if transponerad:
            w = self._diag * self._ovre.solve(v, trans="T")
            res: FloatArray = self._nedre.solve(w, trans="T")
            return res
        res = self._ovre.solve(self._diag * self._nedre.solve(v))
        return res


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


class _AggregeringsMg:
    """Geometrisk aggregeringsmultigrid (V-cykel) som förkonditionerare för CG.

    För den symmetriska, positivt definita Poissonmatrisen i projektionen.
    Nivå l + 1 slår ihop luftcellerna i varje block om 2 × 2 × 2 celler
    (oslätad aggregering, P = 0/1-matris, grovmatris Pᵀ A P). Utjämning med
    Gauss–Seidel framåt före och bakåt efter grovkorrektionen (gör V-cykeln
    symmetrisk, så att den duger för CG), grovkorrektionen skalas med
    ``omega`` = 1,5 (vanligt för oslätad aggregering). Grövsta nivån
    (≤ ``min_grov`` obekanta) löses direkt. Uppmätt 2026-11-21 på 1,5·10⁵
    obekanta: 22 CG-iterationer mot 169 med SGS och ≈ 2000 med Jacobi.
    """

    def __init__(
        self, a: Any, koord: IntArray, omega: float = 1.5, min_grov: int = 3_000
    ) -> None:
        self._omega = omega
        self._matriser: list[Any] = []
        self._sgs: list[_Sgs] = []
        self._p: list[Any] = []
        self._r: list[Any] = []
        a = sp.csr_matrix(a)
        while a.shape[0] > min_grov and len(self._matriser) < 20:
            k = koord // 2
            dim = k.max(axis=0) + 1
            nyckel = (k[:, 0] * dim[1] + k[:, 1]) * dim[2] + k[:, 2]
            unika, agg = np.unique(nyckel, return_inverse=True)
            if unika.size == a.shape[0]:  # pragma: no cover - går inte att grovna mer
                break
            p = sp.csr_matrix(
                (np.ones(a.shape[0]), (np.arange(a.shape[0]), agg)),
                shape=(a.shape[0], unika.size),
            )
            ny_koord = np.zeros((unika.size, 3), dtype=np.int64)
            ny_koord[agg] = k
            self._matriser.append(a)
            self._sgs.append(_Sgs(a))
            self._p.append(p)
            self._r.append(p.T.tocsr())
            a = sp.csr_matrix(self._r[-1] @ a @ p)
            koord = ny_koord
        self._grov = spla.splu(sp.csc_matrix(a), permc_spec="MMD_AT_PLUS_A")

    def _cykel(self, niva: int, b: FloatArray) -> FloatArray:
        if niva == len(self._matriser):
            grov: FloatArray = self._grov.solve(b)
            return grov
        a, sgs = self._matriser[niva], self._sgs[niva]
        x: FloatArray = sgs._nedre.solve(b)  # Gauss–Seidel framåt: (D + L)⁻¹ b
        rest = self._r[niva] @ (b - a @ x)
        x = x + self._omega * (self._p[niva] @ self._cykel(niva + 1, rest))
        x = x + sgs._ovre.solve(b - a @ x)  # Gauss–Seidel bakåt: (D + U)⁻¹ r
        return x

    def losa(self, b: FloatArray) -> FloatArray:
        return self._cykel(0, b)


class TransportOperator:
    """Glesa operatorn A för stationär spridning på ett `Vindfalt`.

    Parametrar
    ----------
    vind, amne: vindfält och ämne.
    projicera: gör ytflödena divergensfria (Helmholtz-projektion), se modultexten.
    periodisk_y: periodiska sidor i y (standard, som LBM); annars öppna ränder.
    schema: ``"uppstroms"`` (standard, första ordningen, M-matris) eller
        ``"tvd"`` (andra ordningen med frusen flödesbegränsare), se modultexten.
    begransare: ``"van_leer"`` (standard) eller ``"koren"`` (bara för ``"tvd"``).
    referenskallor_g_s: (nx, ny, nz), källfält som TVD-begränsaren fryses ur
        direkt i konstruktorn. Utelämnas den fryses begränsaren vid första
        `los` (ur dess källor) eller med `frys_begransare`.
    tvd_pass: antal Picardpass för referensfältet (≥ 1). 1 = ψ ur
        uppströmslösningen, 2 (standard) = ψ ur första TVD-lösningen.
    m_matris_tak: kapa ψ ≤ 2 g/|F| så att A förblir en M-matris (standard):
        garanterad positivitet för alla källor och för den adjungerade, men
        full andra ordning bara där cellpecletalet ≤ 2. Med False används hela
        begränsaren (experimentellt, bara för Pe ≲ 4 och en enda källuppsättning);
        då är bara referensfältet (ungefär) positivt och `los`
        och `los_adjungerad` kastar `LuftFel` om en lösning blir negativ
        (uppmätt 2026-11-21 på kubfallet: −38 % av max för andra källor).
    troskel_direkt: system med högst så många obekanta löses direkt (splu),
        större iterativt (bicgstab/gmres med förkonditionerare). Gäller både
        Poissonekvationen och transportsystemet. Standard 5 000: med
        SGS-förkonditionering tog den iterativa lösningen 0,2 s mot 2,4 s för
        splu vid 17 000 obekanta (uppmätt 2026-11-21, uppdrag L-f; tidigare
        standard 50 000 med ILU).
    tolerans: relativ residualtolerans för iterativa lösare (standard 1e-12,
        tidigare 1e-10: ger massbalansfel ≲ 1e-11 även för en punktkälla och
        kostar ≈ 10 % fler iterationer).
    max_iter: högsta antal iterationer för iterativa lösare.
    max_divergens_rel: största tillåtna relativa divergens efter projektion.
    forkonditionering: ``"auto"`` (standard: SGS, ILU som reserv om SGS inte
        konvergerar), ``"sgs"`` eller ``"ilu"`` (spilu, drop_tol 1e-5,
        fill_factor 10 – den tidigare standarden).

    Attribut
    --------
    matris: A som ``scipy.sparse.csr_matrix`` (n × n), A c = s med c i g/m³ och
        s i g/s per cell. För en ofryst TVD-operator är det uppströmsmatrisen
        tills begränsaren fryses.
    matris_uppstroms: uppströmsmatrisen (samma som ``matris`` för
        ``schema="uppstroms"``).
    psi: (antal inre ytor,) frusen begränsare, ``None`` före frysning eller med
        uppströmsschemat.
    indexkarta: (nx, ny, nz) int64, index för luftceller, −1 i fasta celler.
    divergens_rel: max |nettoflöde per luftcell| / max |ytflöde| efter
        projektionen (före sedimentation). NaN om ``projicera=False``.
    positivitet: min c / max c över luftcellerna i senaste `los` (för TVD utan
        tak en kontroll av att den frysta operatorn höll sig positiv).
    """

    def __init__(
        self,
        vind: Vindfalt,
        amne: Amne,
        projicera: bool = True,
        *,
        periodisk_y: bool = True,
        schema: str = "uppstroms",
        begransare: str = "van_leer",
        referenskallor_g_s: FloatArray | None = None,
        tvd_pass: int = 2,
        m_matris_tak: bool = True,
        troskel_direkt: int = 5_000,
        tolerans: float = 1e-12,
        max_iter: int = 5_000,
        max_divergens_rel: float = 1e-10,
        forkonditionering: str = "auto",
    ) -> None:
        if troskel_direkt < 0 or tolerans <= 0 or max_iter <= 0:
            raise LuftFel("troskel_direkt ≥ 0, tolerans > 0 och max_iter > 0 krävs.")
        if schema not in _ALLA_SCHEMAN:
            raise LuftFel(f"Okänt schema {schema!r}; välj bland {_ALLA_SCHEMAN}.")
        if begransare not in _ALLA_BEGRANSARE:
            raise LuftFel(f"Okänd begränsare {begransare!r}; välj bland {_ALLA_BEGRANSARE}.")
        if forkonditionering not in _ALLA_FORKONDITIONERINGAR:
            raise LuftFel(
                f"Okänd förkonditionering {forkonditionering!r}; välj bland "
                f"{_ALLA_FORKONDITIONERINGAR}."
            )
        if tvd_pass < 1:
            raise LuftFel("tvd_pass måste vara ≥ 1.")
        if schema == "uppstroms" and referenskallor_g_s is not None:
            raise LuftFel("referenskallor_g_s gäller bara schema='tvd'.")
        self.schema = schema
        self.begransare = begransare
        self.tvd_pass = int(tvd_pass)
        self.m_matris_tak = bool(m_matris_tak)
        self.forkonditionering = forkonditionering
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
        inre_axel: list[IntArray] = []
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
            inre_axel.append(np.full(gl_b.size, axel, dtype=np.int64))
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
        self._inre_axel = (
            np.concatenate(inre_axel) if inre_axel else np.zeros(0, np.int64)
        )
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

        self._ws_a = ws_a
        self._yta_m2 = yta_m2
        self.matris_uppstroms = self._bygg_matris(None)
        self.matris = self.matris_uppstroms
        self.psi: FloatArray | None = None
        self.positivitet = float("nan")
        self._referenskallor: FloatArray | None = None
        self._lu: Any = None
        self._forkond: Any = None
        self._forkond_typ = ""
        if schema == "tvd":
            self._uu_minus, self._uu_plus = self._uppstromsgrannar()
            if referenskallor_g_s is not None:
                self.frys_begransare(referenskallor_g_s)

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
        koord: IntArray = np.argwhere(self._luft.reshape(self.form)).astype(np.int64)
        mg = _AggregeringsMg(lap, koord)
        m_op = spla.LinearOperator((n, n), matvec=mg.losa, dtype=np.float64)

        def los_cg(b: FloatArray) -> FloatArray:
            x, info = spla.cg(lap, b, rtol=1e-13, atol=0.0, M=m_op, maxiter=20 * self.max_iter)
            if info != 0:
                raise LuftFel(f"CG för projektionen konvergerade inte (info={info}).")
            res: FloatArray = np.asarray(x, dtype=np.float64)
            return res

        return los_cg

    def _uppstromsgrannar(self) -> tuple[IntArray, IntArray]:
        """För varje inre yta (L, R): luftcellen före L och efter R längs ytans axel.

        Returnerar (uu_minus, uu_plus), −1 där cellen är fast eller ligger
        utanför domänen (i y periodiskt om ``periodisk_y``). uu_minus är UU när
        F ≥ 0 (U = L), uu_plus när F < 0 (U = R).
        """
        idx = self.indexkarta
        luft_lin = np.flatnonzero(self._luft)
        lin_l = luft_lin[self._inre_l]
        lin_r = luft_lin[self._inre_r]
        uu_minus = np.full(self._inre_l.size, -1, dtype=np.int64)
        uu_plus = np.full(self._inre_l.size, -1, dtype=np.int64)
        for axel in range(3):
            n = self.form[axel]
            periodisk = axel == 1 and self.periodisk_y
            if periodisk:
                minus = np.roll(idx, 1, axis=axel)
                plus = np.roll(idx, -1, axis=axel)
            else:
                minus = np.full(self.form, -1, dtype=np.int64)
                plus = np.full(self.form, -1, dtype=np.int64)
                sl_a = [slice(None)] * 3
                sl_b = [slice(None)] * 3
                sl_a[axel], sl_b[axel] = slice(1, n), slice(0, n - 1)
                minus[tuple(sl_a)] = idx[tuple(sl_b)]
                plus[tuple(sl_b)] = idx[tuple(sl_a)]
            valda = self._inre_axel == axel
            uu_minus[valda] = minus.ravel()[lin_l[valda]]
            uu_plus[valda] = plus.ravel()[lin_r[valda]]
        return uu_minus, uu_plus

    def _bygg_matris(self, psi: FloatArray | None) -> Any:
        """Bygger A med ytvärdet ``c_f = (1 − ψ/2) c_U + ψ/2 c_D`` (ψ = 0: uppströms).

        Per inre yta med ``a = |F|`` och konduktans ``g``:
        (U,U) += a(1 − ψ/2) + g, (D,D) += g − aψ/2,
        (U,D) += aψ/2 − g,       (D,U) += −a(1 − ψ/2) − g.
        Kolumnsummorna för ytan är 0 oavsett ψ (konservativt).
        """
        n = self.antal_obekanta
        el, er = self._inre_l, self._inre_r
        f, g = self._inre_f, self._inre_g
        positiv = f >= 0.0
        upp = np.where(positiv, el, er)
        ned = np.where(positiv, er, el)
        a = np.abs(f)
        halv = np.zeros_like(a) if psi is None else 0.5 * psi

        # förluster ut ur domänen per luftcell (kolumnsummor)
        rand_ut = np.maximum(self._rand_f, 0.0)
        utflode = np.bincount(self._rand_p, rand_ut, minlength=n)
        inflode = self._rand_typ == 0
        p_in = self._rand_p[inflode]
        utflode += np.bincount(p_in, 2.0 * self._k_luft[p_in] * self.dx_m, minlength=n)
        vagg_koef = self.amne.deposition_m_s * self._yta_m2 + self._ws_a * self._vagg_under
        deposition = np.bincount(self._vagg_p, vagg_koef, minlength=n)
        nedbrytning = np.full(n, self.amne.nedbrytning_1_s * self.cellvolym_m3)
        self._utflode_koef = utflode
        self._deposition_koef = deposition
        self._nedbrytning_koef = nedbrytning

        diag = (
            np.bincount(upp, a * (1.0 - halv) + g, minlength=n)
            + np.bincount(ned, g - a * halv, minlength=n)
            + utflode
            + deposition
            + nedbrytning
        )
        rader = np.concatenate([upp, ned, np.arange(n)])
        kolumner = np.concatenate([ned, upp, np.arange(n)])
        varden = np.concatenate([a * halv - g, -a * (1.0 - halv) - g, diag])
        mat = sp.csr_matrix((varden, (rader, kolumner)), shape=(n, n))
        mat.sum_duplicates()
        mat.eliminate_zeros()
        return mat

    # ------------------------------------------------------------ begränsare

    def _psi_ur_halt(self, c: FloatArray) -> FloatArray:
        """Begränsaren ψ per inre yta ur halten c (n,) över luftcellerna."""
        f = self._inre_f
        positiv = f >= 0.0
        upp = np.where(positiv, self._inre_l, self._inre_r)
        ned = np.where(positiv, self._inre_r, self._inre_l)
        uu = np.where(positiv, self._uu_minus, self._uu_plus)
        finns = uu >= 0
        c_uu = np.where(finns, c[np.maximum(uu, 0)], 0.0)
        taljare = c[upp] - c_uu
        namnare = c[ned] - c[upp]
        skala = float(np.max(np.abs(c), initial=0.0))
        giltig = finns & (np.abs(namnare) > 1e-14 * skala) & (np.abs(f) > 0.0)
        r = np.divide(taljare, namnare, out=np.zeros_like(taljare), where=giltig)
        psi = np.where(giltig, begransare_psi(r, self.begransare), 0.0)
        if self.m_matris_tak:
            a = np.abs(f)
            # (1 − 1e-12): elementet (U, D) = aψ/2 − g blir strikt ≤ 0 trots avrundning
            tak = np.divide(
                2.0 * (1.0 - 1e-12) * self._inre_g, a, out=np.zeros_like(a), where=a > 0
            )
            psi = np.minimum(psi, tak)
        res: FloatArray = np.asarray(psi, dtype=np.float64)
        return res

    def _satt_matris(self, mat: Any) -> None:
        self.matris = mat
        self._lu = None
        self._forkond = None
        self._forkond_typ = ""

    @property
    def begransare_fryst(self) -> bool:
        """Sant om operatorn är linjär och klar (alltid för ``"uppstroms"``)."""
        return self.schema == "uppstroms" or self.psi is not None

    def frys_begransare(self, kallor_g_s: FloatArray) -> None:
        """Fryser TVD-begränsaren ur referenskällorna (``tvd_pass`` Picardpass).

        kallor_g_s: (nx, ny, nz) eller (m, nx, ny, nz); flera kolumner slås ihop
            som Σ|s_j|. Efter anropet är ``matris`` den frysta, linjära
            TVD-operatorn och tidigare faktoriseringar kastas.
        """
        if self.schema != "tvd":
            raise LuftFel("frys_begransare gäller bara schema='tvd'.")
        b = self._till_vektor(kallor_g_s, "kallor_g_s")
        ref = np.abs(b).sum(axis=1, keepdims=True)
        if not float(ref.sum()) > 0:
            raise LuftFel("Referenskällorna är noll; begränsaren kan inte frysas.")
        self.psi = None
        self._satt_matris(self.matris_uppstroms)
        c = self._losningsmatris(ref, False)
        for steg in range(self.tvd_pass):
            psi = self._psi_ur_halt(c[:, 0])
            self._satt_matris(self._bygg_matris(psi))
            self.psi = psi
            if steg < self.tvd_pass - 1:
                c = self._losningsmatris(ref, False)
        self._referenskallor = np.asarray(kallor_g_s, dtype=np.float64).copy()

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

    def _bygg_forkond(self, typ: str) -> None:
        if typ == "sgs":
            try:
                self._forkond = _Sgs(self.matris)
            except (LuftFel, RuntimeError):
                self._forkond = None
                if self.forkonditionering == "sgs":
                    raise
                typ = "ilu"
        if typ == "ilu":
            try:
                self._forkond = spla.spilu(
                    sp.csc_matrix(self.matris), drop_tol=1e-5, fill_factor=10.0
                )
            except RuntimeError as exc:
                raise LuftFel(f"ILU-faktoriseringen misslyckades: {exc}") from exc
        self._forkond_typ = typ

    def _krylov(
        self, mat: Any, bj: FloatArray, transponerad: bool
    ) -> tuple[FloatArray, int, float]:
        n = self.antal_obekanta
        forkond = self._forkond
        trans = "T" if transponerad else "N"
        sgs = self._forkond_typ == "sgs"

        def matvec(v: FloatArray) -> FloatArray:
            if sgs:
                res_sgs: FloatArray = forkond.losa(v, transponerad)
                return res_sgs
            res_ilu: FloatArray = forkond.solve(v, trans=trans)
            return res_ilu

        m_op = spla.LinearOperator((n, n), matvec=matvec, dtype=np.float64)
        norm_b = float(np.linalg.norm(bj))
        x, info = spla.bicgstab(
            mat, bj, rtol=self.tolerans, atol=0.0, M=m_op, maxiter=self.max_iter
        )
        if info != 0 or not np.all(np.isfinite(x)):
            # maxiter räknar omstarter: högst ≈ max_iter inre iterationer totalt
            x, info = spla.gmres(
                mat, bj, rtol=self.tolerans, atol=0.0, M=m_op,
                restart=60, maxiter=max(1, self.max_iter // 60),
            )
        res = float(np.linalg.norm(mat @ x - bj)) / norm_b
        return np.asarray(x, dtype=np.float64), int(info), res

    def _iterativ(self, b: FloatArray, transponerad: bool) -> FloatArray:
        """Krylovlösning kolumn för kolumn med en gemensam förkonditionerare.

        Samma faktorisering (SGS eller ILU av A) används för Aᵀ via de
        transponerade triangellösningarna. Konvergerar inte SGS (och
        ``forkonditionering="auto"``) byggs ILU och kolumnen löses om.
        Reserven gmres(60) får högst ≈ ``max_iter`` inre iterationer.
        """
        if self._forkond is None:
            self._bygg_forkond("ilu" if self.forkonditionering == "ilu" else "sgs")
        mat = self.matris.T.tocsr() if transponerad else self.matris
        ut = np.zeros_like(b)
        for j in range(b.shape[1]):
            bj = b[:, j]
            if float(np.linalg.norm(bj)) == 0.0:
                continue
            x, info, res = self._krylov(mat, bj, transponerad)
            ok = info == 0 and math.isfinite(res) and res <= 10.0 * self.tolerans
            if not ok and self._forkond_typ == "sgs" and self.forkonditionering == "auto":
                self._bygg_forkond("ilu")
                x, info, res = self._krylov(mat, bj, transponerad)
                ok = info == 0 and math.isfinite(res) and res <= 10.0 * self.tolerans
            if not ok:
                raise LuftFel(
                    f"Iterativ lösare konvergerade inte (info={info}, rel. residual {res:.2e})."
                )
            ut[:, j] = x
        return ut

    def los(self, kallor_g_s: FloatArray) -> FloatArray:
        """Löser A c = s.

        kallor_g_s: (nx, ny, nz) eller (m, nx, ny, nz), utsläpp i g/s per cell
            (måste vara 0 i fasta celler). Operatorn är linjär, så negativa
            värden (skillnadsscenarier) är tillåtna. Med ``"uppstroms"`` (och
            ``"tvd"`` med ``m_matris_tak``) ger icke-negativa källor alltid
            icke-negativ halt.
        Med ``schema="tvd"`` och ofryst begränsare fryses den först ur just
        dessa källor (`frys_begransare`); alla kolumner och senare anrop löses
        sedan med samma linjära operator. ``positivitet`` sätts till
        min c / max c för den senaste lösningen.
        Returnerar halt i g/m³ med samma form, 0 i fasta celler. Faktorisering
        (splu) eller förkonditionerare (SGS/ILU) byggs första gången och
        återanvänds.
        """
        enkel = np.ndim(kallor_g_s) == 3
        if not self.begransare_fryst:
            self.frys_begransare(kallor_g_s)
        b = self._till_vektor(kallor_g_s, "kallor_g_s")
        x = self._losningsmatris(b, False)
        self.positivitet = self._kontrollera_positivitet(x, b, "halten")
        return self._till_falt(x, enkel)

    def _kontrollera_positivitet(self, x: FloatArray, b: FloatArray, vad: str) -> float:
        """min x / max |x|; kastar för TVD utan tak om x < 0 för icke-negativt b.

        Gränsen −1e-10 · max tillåter avrundning och lösartolerans.
        """
        stor = float(np.max(np.abs(x), initial=0.0))
        andel = float(np.min(x)) / stor if stor > 0 else 0.0
        if (
            self.schema == "tvd"
            and not self.m_matris_tak
            and andel < -1e-10
            and bool(np.all(b >= 0))
        ):
            raise LuftFel(
                f"Den frysta TVD-operatorn utan M-matristak gav negativa värden i {vad} "
                f"(min/max = {andel:.1e}) för icke-negativa källor/vikter. Använd "
                "m_matris_tak=True eller frys begränsaren ur just dessa källor."
            )
        return andel

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
        """Ny operator (ny faktorisering) för samma riktning och referensvind `u_ref_m_s`.

        En frusen TVD-begränsare fryses om ur samma referenskällor.
        """
        return TransportOperator(
            self.vind.skalat(u_ref_m_s),
            self.amne,
            self.projicera,
            periodisk_y=self.periodisk_y,
            schema=self.schema,
            begransare=self.begransare,
            referenskallor_g_s=self._referenskallor,
            tvd_pass=self.tvd_pass,
            m_matris_tak=self.m_matris_tak,
            troskel_direkt=self.troskel_direkt,
            tolerans=self.tolerans,
            max_iter=self.max_iter,
            max_divergens_rel=self.max_divergens_rel,
            forkonditionering=self.forkonditionering,
        )

    def los_adjungerad(self, receptor: FloatArray) -> FloatArray:
        """Känslighetskarta för en receptor.

        receptor: (nx, ny, nz), vikter w ≥ 0 (0 i fasta celler), t.ex. 1 i
            receptorns celler. Receptorhalten definieras som det volymviktade
            medlet ``C_r = Σ w_i V_i c_i / Σ w_i V_i`` (alla V_i = dx³ här).
        Löser ``Aᵀ y = g`` med ``g_i = w_i V_i / Σ w_j V_j`` och returnerar y
        (0 i fasta celler) i enheten (g/m³ i receptorn) per (g/s utsläpp i
        cellen), så att ``C_r = Σ_i y_i s_i`` för varje källfält s.

        Dualiteten är exakt (till lösarens precision) för den linjära operator
        som `los` använder: uppströmsoperatorn, eller den frysta TVD-operatorn.
        En ofryst TVD-operator har ingen väldefinierad adjungerad; då kastas
        `LuftFel` (frys först med `frys_begransare` eller
        ``referenskallor_g_s``). Uppströmsoperatorns adjungerade är en
        M-matris och ger alltid y ≥ 0; den frysta TVD-operatorns adjungerade
        bara med ``m_matris_tak=True``.
        """
        if not self.begransare_fryst:
            raise LuftFel(
                "TVD-begränsaren är inte fryst, så operatorn är olinjär och saknar "
                "exakt adjungerad. Anropa frys_begransare(kallor) eller ange "
                "referenskallor_g_s först."
            )
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
        y = self._losningsmatris(g, True)
        self._kontrollera_positivitet(y, g, "känsligheten")
        return self._till_falt(y, True)

    def massbalans(self, kallor_g_s: FloatArray, halt_g_m3: FloatArray) -> dict[str, float]:
        """Massbalans för en lösning: utsläpp = utflöde + deposition + nedbrytning.

        utflode_g_s omfattar advektivt utflöde genom alla öppna ränder och
        diffusion ut genom inflödesranden; deposition_g_s omfattar v_d och
        sedimentation mot mark/fasta ytor. rel_fel = |utsläpp − summa| / |utsläpp|.
        """
        if not self.begransare_fryst:
            raise LuftFel("massbalans kräver en fryst TVD-begränsare (anropa los först).")
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
