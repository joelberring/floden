# SPDX-License-Identifier: MIT
"""Vindbibliotek (KL1), väderserier och årsstatistik genom viktning (`docs/LUFT.md` 2.3).

Idé
---
Vid neutral skiktning är halten en timme med vindhastighet ``U_h`` i sektor ``d``

    C_h(cell) = C_d(cell) · f_h,      f_h = u_ref / U_h,

där ``C_d`` är halten löst vid referensvinden ``u_ref``. Med riktningsinterpolation
blir ``C_h = f_h · (w_a C_a + w_b C_b)``. Timmar med ``U_h < u_min`` (svag vind)
ligger utanför modellen: de flaggas, räknas separat och ingår inte i statistiken.

Komplexitet (N celler, S sektorer, H modellerade timmar)
--------------------------------------------------------
* `Haltstatistik.arsmedel`: ``mean = Σ_d C_d · G_d / H`` med ``G_d = Σ_h f_h w_{h,d}``.
  Tid O(N·S), minne O(N) utöver indata. Inga timfält.
* `Haltstatistik.antal_timmar_over`, närmaste sektor: ``f_h`` sorteras en gång per
  sektor; för varje cell räknas ``#{h ∈ d : C_d·f_h > g}`` med vektoriserad binärsökning
  på samma flyttalsprodukt som en explicit timberäkning (avrundad multiplikation är
  monoton), alltså exakt. Tid O(N·S·log H), minne O(N).
  Med interpolation: blockvis som percentilen.
* `Haltstatistik.percentil`: blockvis över celler. För ett block om B celler byggs
  timvärdena (B, H) och percentilen tas med `numpy.percentile` (samma definition som
  en explicit beräkning). Tid O(N·H) (+ partitionering), minne O(B·H); B väljs så att
  blocket ryms i `block_bytes` (standard 64 MiB).
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from gemensamt.hash import sha256_bytes, sha256_fil
from luft.geometri import grid_meta
from luft.typer import KONTRAKT_KL1, Inflode, LuftFel, Vindfalt, Voxelgrid

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]

BIBLIOTEK_JSON = "bibliotek.json"


# ---------------------------------------------------------------------------
# KL1: ett vindfält per fil
# ---------------------------------------------------------------------------


def _normera_riktning(r: float) -> float:
    if not math.isfinite(r):
        raise LuftFel(f"Ogiltig vindriktning {r}.")
    v = float(r) % 360.0
    return 0.0 if v == 360.0 else v


def vindfil_namn(riktning_grad: float) -> str:
    """Filnamn för en riktning enligt KL1, t.ex. ``vind_270.000.npz``."""
    return f"vind_{_normera_riktning(riktning_grad):07.3f}.npz"


def geometri_sha256(grid: Voxelgrid) -> str:
    """SHA-256 över rutnätets geometri (fast, lad och skalära uppgifter)."""
    meta = json.dumps(grid_meta(grid), sort_keys=True).encode("utf-8")
    fast = np.ascontiguousarray(grid.fast, dtype=np.bool_).tobytes()
    lad = np.ascontiguousarray(grid.lad_m2_m3, dtype=np.float64).tobytes()
    return sha256_bytes(meta + b"\0fast\0" + fast + b"\0lad\0" + lad)


def spara_vindfalt(falt: Vindfalt, sokvag: str | Path) -> Path:
    """Sparar ett `Vindfalt` som KL1-npz (fält + JSON-sträng `meta`)."""
    g = falt.grid
    meta = {
        "kontrakt": KONTRAKT_KL1,
        "dx_m": g.dx_m,
        "vindriktning_grad": g.vindriktning_grad,
        "origo_x_m": g.origo_x_m,
        "origo_y_m": g.origo_y_m,
        "origo_z_m": g.origo_z_m,
        "rotation_centrum_x_m": g.rotation_centrum_x_m,
        "rotation_centrum_y_m": g.rotation_centrum_y_m,
        "inflode": {
            "u_ref_m_s": falt.inflode.u_ref_m_s,
            "z_ref_m": falt.inflode.z_ref_m,
            "z0_m": falt.inflode.z0_m,
            "turbulensintensitet": falt.inflode.turbulensintensitet,
        },
        "medelperiod_s": falt.medelperiod_s,
        "lbm_parametrar": falt.metadata.get("lbm_parametrar", {}),
        "metadata": falt.metadata,
    }
    try:
        meta_text = json.dumps(meta, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LuftFel(f"Vindfältets metadata går inte att spara som JSON: {exc}") from exc
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with mal.open("wb") as fh:
        np.savez_compressed(
            fh,
            u_m_s=falt.u_m_s,
            nu_t_m2_s=falt.nu_t_m2_s,
            fast=g.fast,
            lad_m2_m3=g.lad_m2_m3,
            meta=np.array(meta_text),
        )
    return mal


def ladda_vindfalt(sokvag: str | Path) -> Vindfalt:
    """Läser ett KL1-npz och validerar kontraktsversionen."""
    kalla = Path(sokvag)
    try:
        with np.load(kalla, allow_pickle=False) as data:
            for nyckel in ("u_m_s", "nu_t_m2_s", "fast", "lad_m2_m3", "meta"):
                if nyckel not in data.files:
                    raise LuftFel(f"{kalla}: saknar fältet '{nyckel}' (KL1).")
            meta = json.loads(str(data["meta"]))
            u = data["u_m_s"]
            nu_t = data["nu_t_m2_s"]
            fast = data["fast"]
            lad = data["lad_m2_m3"]
    except (OSError, ValueError) as exc:
        raise LuftFel(f"Kunde inte läsa vindfältet {kalla}: {exc}") from exc
    if meta.get("kontrakt") != KONTRAKT_KL1:
        raise LuftFel(f"{kalla}: kontrakt '{meta.get('kontrakt')}', förväntade '{KONTRAKT_KL1}'.")
    try:
        grid = Voxelgrid(
            dx_m=meta["dx_m"],
            fast=fast,
            lad_m2_m3=lad,
            vindriktning_grad=meta["vindriktning_grad"],
            origo_x_m=meta["origo_x_m"],
            origo_y_m=meta["origo_y_m"],
            origo_z_m=meta["origo_z_m"],
            rotation_centrum_x_m=meta["rotation_centrum_x_m"],
            rotation_centrum_y_m=meta["rotation_centrum_y_m"],
        )
        inf = meta["inflode"]
        inflode = Inflode(
            u_ref_m_s=inf["u_ref_m_s"],
            z_ref_m=inf["z_ref_m"],
            z0_m=inf["z0_m"],
            turbulensintensitet=inf["turbulensintensitet"],
        )
        metadata = meta["metadata"]
        medelperiod = meta["medelperiod_s"]
    except KeyError as exc:
        raise LuftFel(f"{kalla}: meta saknar fältet {exc} (KL1).") from exc
    return Vindfalt(
        grid=grid,
        inflode=inflode,
        u_m_s=u,
        nu_t_m2_s=nu_t,
        medelperiod_s=medelperiod,
        metadata=metadata,
    )


@dataclass(frozen=True)
class Vindbibliotek:
    """Ett `Vindfalt` per riktning, alla vid samma referensvind `u_ref_m_s`.

    Riktningarna sorteras stigande vid skapandet. `kallgeometri_sha256` kan anges
    med kontrollsumman för indatarastret som rutnäten byggts från.
    """

    falt: tuple[Vindfalt, ...]
    kallgeometri_sha256: str | None = None

    def __post_init__(self) -> None:
        if len(self.falt) == 0:
            raise LuftFel("Ett vindbibliotek måste ha minst en riktning.")
        ordnade = tuple(
            sorted(self.falt, key=lambda f: _normera_riktning(f.grid.vindriktning_grad))
        )
        object.__setattr__(self, "falt", ordnade)
        riktningar = [_normera_riktning(f.grid.vindriktning_grad) for f in ordnade]
        namn = [vindfil_namn(r) for r in riktningar]
        if len(set(namn)) != len(namn):
            raise LuftFel(f"Dubbla eller för närliggande riktningar i biblioteket: {riktningar}")
        u_ref = ordnade[0].inflode.u_ref_m_s
        for f in ordnade:
            if f.inflode.u_ref_m_s != u_ref:
                raise LuftFel(
                    "Alla vindfält i biblioteket måste ha samma u_ref_m_s "
                    f"({u_ref} ≠ {f.inflode.u_ref_m_s}); använd Vindfalt.skalat."
                )

    @property
    def riktningar_grad(self) -> tuple[float, ...]:
        return tuple(_normera_riktning(f.grid.vindriktning_grad) for f in self.falt)

    @property
    def u_ref_m_s(self) -> float:
        return self.falt[0].inflode.u_ref_m_s

    def __len__(self) -> int:
        return len(self.falt)

    def for_riktning(self, riktning_grad: float) -> Vindfalt:
        """Vindfältet med exakt denna riktning."""
        r = _normera_riktning(riktning_grad)
        for f in self.falt:
            if _normera_riktning(f.grid.vindriktning_grad) == r:
                return f
        raise LuftFel(f"Riktningen {r}° finns inte i biblioteket {self.riktningar_grad}.")

    def spara(self, mapp: str | Path) -> Path:
        """Sparar biblioteket som mapp: en npz per riktning + `bibliotek.json`."""
        rot = Path(mapp)
        rot.mkdir(parents=True, exist_ok=True)
        filer: list[dict[str, Any]] = []
        for r, f in zip(self.riktningar_grad, self.falt, strict=True):
            fil = spara_vindfalt(f, rot / vindfil_namn(r))
            filer.append(
                {
                    "riktning_grad": r,
                    "fil": fil.name,
                    "sha256": sha256_fil(fil),
                    "geometri_sha256": geometri_sha256(f.grid),
                }
            )
        index = {
            "kontrakt": KONTRAKT_KL1,
            "riktningar_grad": list(self.riktningar_grad),
            "u_ref_m_s": self.u_ref_m_s,
            "geometri_sha256": _samlad_geometri_sha([p["geometri_sha256"] for p in filer]),
            "kallgeometri_sha256": self.kallgeometri_sha256,
            "filer": filer,
        }
        mal = rot / BIBLIOTEK_JSON
        mal.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return mal

    @classmethod
    def ladda(cls, mapp: str | Path) -> Vindbibliotek:
        """Läser ett bibliotek och validerar kontrakt, filkontrollsummor och geometri."""
        rot = Path(mapp)
        index_fil = rot / BIBLIOTEK_JSON
        try:
            index = json.loads(index_fil.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LuftFel(f"Kunde inte läsa {index_fil}: {exc}") from exc
        if not isinstance(index, dict) or index.get("kontrakt") != KONTRAKT_KL1:
            kontrakt = index.get("kontrakt") if isinstance(index, dict) else None
            raise LuftFel(f"{index_fil}: kontrakt '{kontrakt}', förväntade '{KONTRAKT_KL1}'.")
        falt: list[Vindfalt] = []
        geo: list[str] = []
        for post in index.get("filer", []):
            fil = rot / str(post["fil"])
            if sha256_fil(fil) != post["sha256"]:
                raise LuftFel(f"{fil}: sha256 stämmer inte med {BIBLIOTEK_JSON}.")
            f = ladda_vindfalt(fil)
            g_sha = geometri_sha256(f.grid)
            if g_sha != post["geometri_sha256"]:
                raise LuftFel(f"{fil}: geometrins sha256 stämmer inte.")
            if _normera_riktning(f.grid.vindriktning_grad) != float(post["riktning_grad"]):
                raise LuftFel(f"{fil}: riktningen stämmer inte med {BIBLIOTEK_JSON}.")
            falt.append(f)
            geo.append(g_sha)
        if _samlad_geometri_sha(geo) != index.get("geometri_sha256"):
            raise LuftFel(f"{index_fil}: samlad geometri-sha256 stämmer inte.")
        bib = cls(falt=tuple(falt), kallgeometri_sha256=index.get("kallgeometri_sha256"))
        if list(bib.riktningar_grad) != [float(r) for r in index["riktningar_grad"]]:
            raise LuftFel(f"{index_fil}: riktningslistan stämmer inte med filerna.")
        if bib.u_ref_m_s != float(index["u_ref_m_s"]):
            raise LuftFel(f"{index_fil}: u_ref_m_s stämmer inte med filerna.")
        return bib


def _samlad_geometri_sha(delar: Sequence[str]) -> str:
    return sha256_bytes("\n".join(delar).encode("ascii"))


# ---------------------------------------------------------------------------
# Väderserie
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Vaderserie:
    """Timvärden för vind. `giltig` = False för saknade/underkända timmar."""

    tid: tuple[str, ...]
    vindriktning_grad: FloatArray
    vindhastighet_m_s: FloatArray
    giltig: BoolArray

    def __post_init__(self) -> None:
        n = len(self.tid)
        for namn, arr in (
            ("vindriktning_grad", self.vindriktning_grad),
            ("vindhastighet_m_s", self.vindhastighet_m_s),
            ("giltig", self.giltig),
        ):
            if arr.shape != (n,):
                raise LuftFel(f"Vaderserie.{namn} ska ha formen ({n},).")
        if self.giltig.dtype != np.bool_:
            raise LuftFel("Vaderserie.giltig måste ha dtype bool.")
        g = self.giltig
        r = self.vindriktning_grad[g]
        u = self.vindhastighet_m_s[g]
        if not (np.all(np.isfinite(r)) and np.all(np.isfinite(u))):
            raise LuftFel("Giltiga timmar får inte ha NaN; sätt giltig=False.")
        if np.any(u < 0):
            raise LuftFel("Vindhastighet får inte vara negativ.")
        if np.any((r < 0) | (r > 360)):
            raise LuftFel("Vindriktning ska ligga i [0, 360] grader.")

    @classmethod
    def skapa(
        cls,
        vindriktning_grad: npt.ArrayLike,
        vindhastighet_m_s: npt.ArrayLike,
        giltig: npt.ArrayLike | None = None,
        tid: Sequence[str] | None = None,
    ) -> Vaderserie:
        """Bekväm konstruktor från array-liknande data."""
        r = np.asarray(vindriktning_grad, dtype=np.float64).ravel()
        u = np.asarray(vindhastighet_m_s, dtype=np.float64).ravel()
        g = (
            np.isfinite(r) & np.isfinite(u)
            if giltig is None
            else np.asarray(giltig, dtype=np.bool_).ravel()
        )
        t = tuple(tid) if tid is not None else tuple(str(i) for i in range(r.size))
        return cls(tid=t, vindriktning_grad=r, vindhastighet_m_s=u, giltig=g)

    @classmethod
    def las_csv(cls, sokvag: str | Path) -> Vaderserie:
        """Läser CSV med kolumnerna `tid,vindriktning_grad,vindhastighet_m_s` (+ ev. `giltig`).

        Tomma värden eller NaN ger `giltig = False` för timmen (flaggas, gissas inte).
        Kolumnen `giltig` tolkar 1/0, true/false, ja/nej.
        """
        kalla = Path(sokvag)
        try:
            with kalla.open(encoding="utf-8", newline="") as fh:
                lasare = csv.DictReader(fh)
                falt = lasare.fieldnames or []
                for kol in ("tid", "vindriktning_grad", "vindhastighet_m_s"):
                    if kol not in falt:
                        raise LuftFel(f"{kalla}: saknar kolumnen '{kol}'.")
                rader = list(lasare)
        except OSError as exc:
            raise LuftFel(f"Kunde inte läsa {kalla}: {exc}") from exc

        def tal(text: str | None, rad: int, kol: str) -> float:
            if text is None or text.strip() == "":
                return math.nan
            try:
                return float(text)
            except ValueError as exc:
                raise LuftFel(f"{kalla} rad {rad}: '{text}' i {kol} är inget tal.") from exc

        tider: list[str] = []
        r = np.empty(len(rader), dtype=np.float64)
        u = np.empty(len(rader), dtype=np.float64)
        g = np.ones(len(rader), dtype=np.bool_)
        for n, rad in enumerate(rader):
            tider.append(str(rad["tid"]))
            r[n] = tal(rad["vindriktning_grad"], n + 2, "vindriktning_grad")
            u[n] = tal(rad["vindhastighet_m_s"], n + 2, "vindhastighet_m_s")
            if "giltig" in rad and rad["giltig"] is not None:
                v = rad["giltig"].strip().lower()
                if v in ("1", "true", "ja", "sant"):
                    pass
                elif v in ("0", "false", "nej", "falskt", ""):
                    g[n] = False
                else:
                    raise LuftFel(f"{kalla} rad {n + 2}: ogiltigt värde '{v}' i giltig.")
        g &= np.isfinite(r) & np.isfinite(u)
        return cls(tid=tuple(tider), vindriktning_grad=r, vindhastighet_m_s=u, giltig=g)


# ---------------------------------------------------------------------------
# Fördelning på sektorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fordelning:
    """Timvis fördelning på riktningar i biblioteket.

    För modellerade timmar: ``C_h = skalfaktor_h · Σ_c vikt[h, c] · C[sektor_index[h, c]]``.
    Ej modellerade timmar har ``sektor_index = −1`` och ``skalfaktor = 0``.
    """

    riktningar_grad: tuple[float, ...]
    u_ref_m_s: float
    u_min_m_s: float
    interpolerad: bool
    sektor_index: IntArray  # (n, 2)
    vikt: FloatArray  # (n, 2)
    skalfaktor: FloatArray  # (n,), u_ref / U
    modellerad: BoolArray  # (n,)
    svag_vind: BoolArray  # (n,), giltig och U < u_min
    ogiltig: BoolArray  # (n,)

    @property
    def antal_timmar(self) -> int:
        return int(self.modellerad.size)

    @property
    def antal_modellerade(self) -> int:
        return int(self.modellerad.sum())

    @property
    def antal_svag_vind(self) -> int:
        return int(self.svag_vind.sum())

    @property
    def antal_ogiltiga(self) -> int:
        return int(self.ogiltig.sum())

    @property
    def andel_svag_vind(self) -> float:
        """Andel av de giltiga timmarna med svag vind (utanför modellen)."""
        giltiga = self.antal_timmar - self.antal_ogiltiga
        return self.antal_svag_vind / giltiga if giltiga else math.nan

    def sammanfattning(self) -> dict[str, Any]:
        return {
            "antal_timmar": self.antal_timmar,
            "antal_modellerade": self.antal_modellerade,
            "antal_svag_vind": self.antal_svag_vind,
            "antal_ogiltiga": self.antal_ogiltiga,
            "andel_svag_vind": self.andel_svag_vind,
            "u_min_m_s": self.u_min_m_s,
            "u_ref_m_s": self.u_ref_m_s,
            "interpolerad": self.interpolerad,
        }


def _vinkelavstand(a: FloatArray, b: FloatArray) -> FloatArray:
    d = np.abs(a - b) % 360.0
    ut: FloatArray = np.minimum(d, 360.0 - d)
    return ut


def fordela(
    vaderserie: Vaderserie,
    riktningar_grad: Sequence[float],
    u_ref_m_s: float,
    u_min_m_s: float = 0.5,
    interpolera: bool = False,
) -> Fordelning:
    """Fördelar varje timme på riktningssektorer och ger skalfaktorn ``u_ref/U``.

    Utan interpolation väljs närmaste riktning (vid lika avstånd den först
    listade). Med `interpolera=True` vägs de två omgivande riktningarna linjärt
    efter vinkelavstånd: ``w_a = d_b / (d_a + d_b)``. Timmar med ``U < u_min_m_s``
    flaggas som svag vind och skalas inte.
    """
    rikt = np.array([_normera_riktning(r) for r in riktningar_grad], dtype=np.float64)
    if rikt.size == 0:
        raise LuftFel("Minst en riktning krävs.")
    if np.unique(rikt).size != rikt.size:
        raise LuftFel("Riktningarna måste vara unika.")
    if not (u_ref_m_s > 0 and math.isfinite(u_ref_m_s)):
        raise LuftFel("u_ref_m_s måste vara positiv.")
    if not (u_min_m_s > 0 and math.isfinite(u_min_m_s)):
        raise LuftFel("u_min_m_s måste vara positiv (annars blir u_ref/U obegränsad).")

    n = len(vaderserie.tid)
    ogiltig = ~vaderserie.giltig
    u = np.where(vaderserie.giltig, vaderserie.vindhastighet_m_s, 0.0)
    theta = np.where(vaderserie.giltig, vaderserie.vindriktning_grad % 360.0, 0.0)
    svag = vaderserie.giltig & (u < u_min_m_s)
    modellerad = vaderserie.giltig & ~svag

    idx = np.full((n, 2), -1, dtype=np.int64)
    vikt = np.zeros((n, 2), dtype=np.float64)
    skal = np.zeros(n, dtype=np.float64)
    skal[modellerad] = u_ref_m_s / u[modellerad]

    th = theta[modellerad]
    if not interpolera or rikt.size == 1:
        avst = _vinkelavstand(th[:, None], rikt[None, :])
        narmast = np.argmin(avst, axis=1)
        idx[modellerad, 0] = narmast
        idx[modellerad, 1] = narmast
        vikt[modellerad, 0] = 1.0
    else:
        ordning = np.argsort(rikt)
        sorterade = rikt[ordning]
        # sektor a = sista riktning ≤ θ (cirkulärt), b = nästa
        pos = np.searchsorted(sorterade, th, side="right") - 1
        a_pos = pos % sorterade.size
        b_pos = (pos + 1) % sorterade.size
        ra = sorterade[a_pos]
        rb = sorterade[b_pos]
        da = (th - ra) % 360.0
        db = (rb - th) % 360.0
        summa = da + db
        wa = np.where(summa > 0, db / np.where(summa > 0, summa, 1.0), 1.0)
        idx[modellerad, 0] = ordning[a_pos]
        idx[modellerad, 1] = ordning[b_pos]
        vikt[modellerad, 0] = wa
        vikt[modellerad, 1] = 1.0 - wa

    return Fordelning(
        riktningar_grad=tuple(float(r) for r in rikt),
        u_ref_m_s=float(u_ref_m_s),
        u_min_m_s=float(u_min_m_s),
        interpolerad=bool(interpolera and rikt.size > 1),
        sektor_index=idx,
        vikt=vikt,
        skalfaktor=skal,
        modellerad=modellerad,
        svag_vind=svag,
        ogiltig=ogiltig,
    )


# ---------------------------------------------------------------------------
# Statistik
# ---------------------------------------------------------------------------


class Haltstatistik:
    """Årsstatistik ur halter per riktning (vid u_ref) och en `Fordelning`.

    Args:
        halter_per_riktning: en array per riktning i `fordelning.riktningar_grad`
            (samma ordning), alla med samma form, i g/m³. NaN (t.ex. fasta celler)
            fortplantas till NaN i resultatet.
        fordelning: från `fordela`.
        block_bytes: minnesbudget för blockvisa beräkningar.
    """

    def __init__(
        self,
        halter_per_riktning: Sequence[npt.ArrayLike],
        fordelning: Fordelning,
        block_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if len(halter_per_riktning) != len(fordelning.riktningar_grad):
            raise LuftFel(
                f"{len(halter_per_riktning)} haltfält men "
                f"{len(fordelning.riktningar_grad)} riktningar."
            )
        arrayer = [np.asarray(h, dtype=np.float64) for h in halter_per_riktning]
        form = arrayer[0].shape
        if any(a.shape != form for a in arrayer):
            raise LuftFel("Alla haltfält måste ha samma form (samma rutnät).")
        self.form: tuple[int, ...] = tuple(int(s) for s in form)
        self._c: FloatArray = np.stack([a.ravel() for a in arrayer])  # (S, N)
        if np.any(np.isinf(self._c)):
            raise LuftFel("Haltfälten innehåller Inf.")
        if np.any(self._c[~np.isnan(self._c)] < 0):
            raise LuftFel("Halter får inte vara negativa.")
        if fordelning.antal_modellerade == 0:
            raise LuftFel("Inga modellerade timmar (alla svag vind eller ogiltiga).")
        self.fordelning = fordelning
        self.block_bytes = int(block_bytes)

        m = fordelning.modellerad
        self._f: FloatArray = fordelning.skalfaktor[m]
        self._idx: IntArray = fordelning.sektor_index[m]
        self._w: FloatArray = fordelning.vikt[m]
        s = self._c.shape[0]
        # f per sektor, sorterad (för närmaste sektor)
        self._f_sorterad: list[FloatArray] = [
            np.sort(self._f[self._idx[:, 0] == d]) for d in range(s)
        ]

    @property
    def antal_modellerade_timmar(self) -> int:
        return int(self._f.size)

    @property
    def andel_svag_vind(self) -> float:
        return self.fordelning.andel_svag_vind

    def _forma(self, v: FloatArray) -> FloatArray:
        ut: FloatArray = v.reshape(self.form)
        return ut

    def arsmedel(self) -> FloatArray:
        """Medelvärde över modellerade timmar, g/m³. O(N·S)."""
        s = self._c.shape[0]
        g = np.zeros(s, dtype=np.float64)
        np.add.at(g, self._idx[:, 0], self._f * self._w[:, 0])
        np.add.at(g, self._idx[:, 1], self._f * self._w[:, 1])
        medel = (g @ np.nan_to_num(self._c, nan=0.0)) / self._f.size
        medel[np.any(np.isnan(self._c[g > 0]), axis=0)] = np.nan
        return self._forma(medel)

    def _block(self) -> int:
        return max(1, self.block_bytes // (8 * max(1, self._f.size)))

    def _timvarden(self, sl: slice) -> FloatArray:
        """Timvärden (B, H) för cellerna i `sl`, samma räkneordning som en explicit beräkning."""
        ca = self._c[self._idx[:, 0], sl]  # (H, B)
        cb = self._c[self._idx[:, 1], sl]
        v: FloatArray = (self._f[:, None] * (self._w[:, 0:1] * ca + self._w[:, 1:2] * cb)).T
        return v

    def antal_timmar_over(self, grans_g_m3: float) -> FloatArray:
        """Antal modellerade timmar med halt strikt över gränsen (float, NaN där halt saknas)."""
        n = self._c.shape[1]
        if not self.fordelning.interpolerad:
            antal = np.zeros(n, dtype=np.float64)
            nan_mask = np.zeros(n, dtype=np.bool_)
            for d, f_sort in enumerate(self._f_sorterad):
                m = f_sort.size
                if m == 0:
                    continue
                c = self._c[d]
                nan_mask |= np.isnan(c)
                c0 = np.nan_to_num(c, nan=0.0)
                lo = np.zeros(n, dtype=np.int64)
                hi = np.full(n, m, dtype=np.int64)
                # första index där fl(f·c) > g; predikatet är monotont i f för c ≥ 0
                while True:
                    aktiv = lo < hi
                    if not np.any(aktiv):
                        break
                    mid = (lo + hi) // 2
                    pred = (f_sort[np.minimum(mid, m - 1)] * c0) > grans_g_m3
                    hi = np.where(aktiv & pred, mid, hi)
                    lo = np.where(aktiv & ~pred, mid + 1, lo)
                antal += m - lo
            antal[nan_mask] = np.nan
            return self._forma(antal)
        ut = np.empty(n, dtype=np.float64)
        b = self._block()
        for start in range(0, n, b):
            sl = slice(start, min(n, start + b))
            v = self._timvarden(sl)
            cnt = np.sum(v > grans_g_m3, axis=1).astype(np.float64)
            cnt[np.any(np.isnan(v), axis=1)] = np.nan
            ut[sl] = cnt
        return self._forma(ut)

    def percentil(self, p: float | Sequence[float], metod: str = "linear") -> FloatArray:
        """Percentil(er) p ∈ [0, 100] av timhalten per cell, g/m³ (blockvis, exakt).

        Skalärt p ger formen av haltfälten; en lista ger (len(p), *form).
        `metod` skickas till `numpy.percentile`.
        """
        p_arr = np.atleast_1d(np.asarray(p, dtype=np.float64))
        if np.any((p_arr < 0) | (p_arr > 100)):
            raise LuftFel("Percentiler ska ligga i [0, 100].")
        n = self._c.shape[1]
        ut = np.empty((p_arr.size, n), dtype=np.float64)
        b = self._block()
        for start in range(0, n, b):
            sl = slice(start, min(n, start + b))
            v = self._timvarden(sl)
            ut[:, sl] = np.percentile(v, p_arr, axis=1, method=metod)  # type: ignore[call-overload]
        if np.ndim(p) == 0:
            return self._forma(ut[0])
        res: FloatArray = ut.reshape((p_arr.size, *self.form))
        return res
