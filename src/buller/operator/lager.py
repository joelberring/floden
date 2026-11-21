"""Överföringslager: gles lagring av paröverföringen `H_db[par, väderklass, band]` (KB2).

Lagret innehåller allt som beror på geometri och väder men inget som beror på emissionen.
Nivån för en ny emission fås genom omkombination (`niva_db`), utan ny banberäkning.

Ett par (mottagare, källa) är i ett av tre tillstånd:

- *lagrat*: beräknat, med minst ett ändligt värde i `h_db`,
- *utan bana*: beräknat, men överföringen var -inf överallt (lagras inte),
- *rest*: inte beräknat. Källan ligger då i ett restintervall för mottagaren, och dess bidrag
  är utelämnat ur nivån men begränsat uppåt av `rest_grans_db` (se `buller.operator.klustring`).

Restintervallen pekar in i `kall_ordning`, källträdets ordning av källorna. Intervallet
`[rest_start, rest_slut)` betyder källorna `kall_ordning[rest_start:rest_slut]`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from buller.operator.energi import db_till_energi, energisumma_db
from buller.operator.npzfil import skriv_npz_deterministiskt
from buller.typer import ANTAL_BAND, BullerFel, Flyttal
from gemensamt.hash import sha256_bytes

Heltal = NDArray[np.int64]

LAGERFORMAT = "floden.buller.overforingslager/v1"
_VIKTSUMMA_TOLERANS = 1e-9
# Övre gräns för antal element i en temporär array vid omkombination (ca 64 MB float64).
_MAX_ELEMENT_PER_BLOCK = 8_000_000

_ARRAYFALT: tuple[str, ...] = (
    "mottagare_id",
    "mottagare_e_m",
    "mottagare_n_m",
    "kalla_id",
    "kalla_e_m",
    "kalla_n_m",
    "vaderklass_vikter",
    "par_mottagare_index",
    "par_kalla_index",
    "h_db",
    "kall_ordning",
    "rest_mottagare_index",
    "rest_start",
    "rest_slut",
    "rest_grans_db",
)


def _las_array(array: NDArray[np.generic], dtyp: type[np.generic]) -> NDArray[np.generic]:
    """Sammanhängande, skrivskyddad vy med fast datatyp (little endian)."""
    vy = np.ascontiguousarray(array, dtype=np.dtype(dtyp).newbyteorder("<")).view()
    vy.setflags(write=False)
    return vy


@dataclass(frozen=True, eq=False)
class Overforingslager:
    """Paröverföring i dB för ett antal mottagare och källor, med väderklassernas vikter.

    Skapa lagret med `Overforingslager.skapa(...)`. `identitet` är sha256 över alla arrayer
    (datatyp, form och innehåll) och ändras så fort något i lagret ändras.
    """

    mottagare_id: Heltal  # form (n_mottagare,)
    mottagare_e_m: Flyttal
    mottagare_n_m: Flyttal
    kalla_id: Heltal  # form (n_kallor,)
    kalla_e_m: Flyttal
    kalla_n_m: Flyttal
    vaderklass_vikter: Flyttal  # form (n_vaderklasser,), summa 1
    par_mottagare_index: Heltal  # form (n_par,), sorterat på (mottagare, källa)
    par_kalla_index: Heltal  # form (n_par,)
    h_db: Flyttal  # form (n_par, n_vaderklasser, ANTAL_BAND)
    kall_ordning: Heltal  # form (n_kallor,), permutation av källindex
    rest_mottagare_index: Heltal  # form (n_rest,), sorterat på (mottagare, start)
    rest_start: Heltal  # form (n_rest,), position i kall_ordning
    rest_slut: Heltal  # form (n_rest,), exklusiv
    rest_grans_db: Flyttal  # form (n_rest, ANTAL_BAND), övre gräns för överföringen
    identitet: str = field(init=False)

    def __post_init__(self) -> None:
        for namn in _ARRAYFALT:
            ar_flyttal = getattr(self, namn).dtype.kind == "f"
            object.__setattr__(
                self, namn, _las_array(getattr(self, namn), np.float64 if ar_flyttal else np.int64)
            )
        self._validera()
        object.__setattr__(self, "identitet", self._berakna_identitet())

    # ------------------------------------------------------------------ skapande

    @classmethod
    def skapa(
        cls,
        *,
        mottagare_id: NDArray[np.int64],
        mottagare_e_m: Flyttal,
        mottagare_n_m: Flyttal,
        kalla_id: NDArray[np.int64],
        kalla_e_m: Flyttal,
        kalla_n_m: Flyttal,
        vaderklass_vikter: Flyttal,
        par_mottagare_index: NDArray[np.int64],
        par_kalla_index: NDArray[np.int64],
        h_db: Flyttal,
        kall_ordning: NDArray[np.int64] | None = None,
        rest_mottagare_index: NDArray[np.int64] | None = None,
        rest_start: NDArray[np.int64] | None = None,
        rest_slut: NDArray[np.int64] | None = None,
        rest_grans_db: Flyttal | None = None,
    ) -> Overforingslager:
        """Skapar ett lager. Par och restintervall sorteras; par utan bana (-inf) tas bort."""
        n_kallor = int(np.asarray(kalla_id).shape[0])
        par_m = np.asarray(par_mottagare_index, dtype=np.int64)
        par_k = np.asarray(par_kalla_index, dtype=np.int64)
        h = np.asarray(h_db, dtype=np.float64)
        if h.ndim != 3 or h.shape[0] != par_m.shape[0] or par_k.shape != par_m.shape:
            raise BullerFel("h_db ska ha formen (n_par, n_vaderklasser, band) och matcha paren.")
        har_bana = np.any(h != -np.inf, axis=(1, 2))
        if not bool(np.all(har_bana)):
            par_m, par_k, h = par_m[har_bana], par_k[har_bana], h[har_bana]
        ordning = np.lexsort((par_k, par_m))
        if not bool(np.all(ordning[1:] > ordning[:-1])):
            par_m, par_k, h = par_m[ordning], par_k[ordning], h[ordning]

        tom = np.zeros(0, dtype=np.int64)
        rest_m = tom if rest_mottagare_index is None else np.asarray(rest_mottagare_index)
        r_start = tom if rest_start is None else np.asarray(rest_start)
        r_slut = tom if rest_slut is None else np.asarray(rest_slut)
        r_grans = (
            np.zeros((0, ANTAL_BAND)) if rest_grans_db is None else np.asarray(rest_grans_db)
        )
        if not (rest_m.shape == r_start.shape == r_slut.shape) or r_grans.shape[:1] != rest_m.shape:
            raise BullerFel("Restintervallens arrayer ska vara lika långa.")
        rest_ordning = np.lexsort((r_start, rest_m))
        return cls(
            mottagare_id=mottagare_id,
            mottagare_e_m=mottagare_e_m,
            mottagare_n_m=mottagare_n_m,
            kalla_id=kalla_id,
            kalla_e_m=kalla_e_m,
            kalla_n_m=kalla_n_m,
            vaderklass_vikter=vaderklass_vikter,
            par_mottagare_index=par_m,
            par_kalla_index=par_k,
            h_db=h,
            kall_ordning=(
                np.arange(n_kallor, dtype=np.int64) if kall_ordning is None else kall_ordning
            ),
            rest_mottagare_index=rest_m[rest_ordning],
            rest_start=r_start[rest_ordning],
            rest_slut=r_slut[rest_ordning],
            rest_grans_db=r_grans[rest_ordning],
        )

    # ------------------------------------------------------------------ egenskaper

    @property
    def antal_mottagare(self) -> int:
        return int(self.mottagare_id.shape[0])

    @property
    def antal_kallor(self) -> int:
        return int(self.kalla_id.shape[0])

    @property
    def antal_vaderklasser(self) -> int:
        return int(self.vaderklass_vikter.shape[0])

    @property
    def antal_par(self) -> int:
        """Antal lagrade par (beräknade och med minst ett ändligt värde)."""
        return int(self.par_mottagare_index.shape[0])

    @property
    def antal_restpar(self) -> int:
        """Antal par som inte är beräknade utan bara begränsade uppåt."""
        return int(np.sum(self.rest_slut - self.rest_start))

    @property
    def har_rest(self) -> bool:
        return bool(self.rest_mottagare_index.shape[0] > 0)

    # ------------------------------------------------------------------ validering

    def _validera(self) -> None:
        n_m, n_k, n_v = self.antal_mottagare, self.antal_kallor, self.antal_vaderklasser
        for namn, langd in (
            ("mottagare_e_m", n_m),
            ("mottagare_n_m", n_m),
            ("kalla_e_m", n_k),
            ("kalla_n_m", n_k),
            ("kall_ordning", n_k),
        ):
            if getattr(self, namn).shape != (langd,):
                raise BullerFel(f"{namn} har fel form, förväntade ({langd},).")
        for namn in ("mottagare_id", "kalla_id"):
            varden = getattr(self, namn)
            if varden.ndim != 1 or np.unique(varden).shape[0] != varden.shape[0]:
                raise BullerFel(f"{namn} ska vara en endimensionell array med unika id.")
        for namn in ("mottagare_e_m", "mottagare_n_m", "kalla_e_m", "kalla_n_m"):
            if not bool(np.all(np.isfinite(getattr(self, namn)))):
                raise BullerFel(f"{namn} innehåller icke-ändliga koordinater.")

        vikter = self.vaderklass_vikter
        if vikter.ndim != 1 or n_v < 1:
            raise BullerFel("Minst en väderklass krävs.")
        if not bool(np.all(np.isfinite(vikter))) or bool(np.any(vikter < 0.0)):
            raise BullerFel("Väderklassernas vikter ska vara ändliga och icke-negativa.")
        if abs(float(np.sum(vikter)) - 1.0) > _VIKTSUMMA_TOLERANS:
            raise BullerFel(
                f"Väderklassernas vikter ska summera till 1, fick {float(np.sum(vikter))!r}."
            )

        n_par = self.antal_par
        if self.par_mottagare_index.shape != (n_par,) or self.par_kalla_index.shape != (n_par,):
            raise BullerFel("Parindexen ska vara endimensionella och lika långa.")
        if self.h_db.shape != (n_par, n_v, ANTAL_BAND):
            raise BullerFel(
                f"h_db har formen {self.h_db.shape}, förväntade {(n_par, n_v, ANTAL_BAND)}."
            )
        if n_par:
            par_m, par_k = self.par_mottagare_index, self.par_kalla_index
            if int(par_m.min()) < 0 or int(par_m.max()) >= n_m:
                raise BullerFel("par_mottagare_index pekar utanför mottagarlistan.")
            if int(par_k.min()) < 0 or int(par_k.max()) >= n_k:
                raise BullerFel("par_kalla_index pekar utanför källistan.")
            nyckel = par_m * n_k + par_k
            if not bool(np.all(nyckel[1:] > nyckel[:-1])):
                raise BullerFel("Paren ska vara unika och sorterade på (mottagare, källa).")
        if bool(np.any(np.isnan(self.h_db))) or bool(np.any(self.h_db == np.inf)):
            raise BullerFel("h_db innehåller NaN eller +inf. Bara ändliga värden och -inf godtas.")

        if not np.array_equal(np.sort(self.kall_ordning), np.arange(n_k, dtype=np.int64)):
            raise BullerFel("kall_ordning ska vara en permutation av källindexen.")
        n_rest = int(self.rest_mottagare_index.shape[0])
        if self.rest_grans_db.shape != (n_rest, ANTAL_BAND):
            raise BullerFel("rest_grans_db ska ha formen (n_rest, band).")
        if bool(np.any(np.isnan(self.rest_grans_db))):
            raise BullerFel("rest_grans_db innehåller NaN.")
        if n_rest:
            rest_m, start, slut = self.rest_mottagare_index, self.rest_start, self.rest_slut
            if int(rest_m.min()) < 0 or int(rest_m.max()) >= n_m:
                raise BullerFel("rest_mottagare_index pekar utanför mottagarlistan.")
            if bool(np.any(start < 0)) or bool(np.any(slut > n_k)) or bool(np.any(start >= slut)):
                raise BullerFel("Restintervall ska uppfylla 0 <= start < slut <= n_kallor.")
            samma = rest_m[1:] == rest_m[:-1]
            if bool(np.any(rest_m[1:] < rest_m[:-1])) or bool(
                np.any(samma & (start[1:] < slut[:-1]))
            ):
                raise BullerFel("Restintervallen ska vara sorterade och inte överlappa.")
            if n_par and bool(np.any(self.ar_rest(self.par_mottagare_index, self.par_kalla_index))):
                raise BullerFel("Ett par är både lagrat och markerat som rest.")

    def _berakna_identitet(self) -> str:
        rader = [LAGERFORMAT]
        for namn in _ARRAYFALT:
            array = getattr(self, namn)
            rader.append(
                f"{namn}|{array.dtype.str}|{array.shape}|{sha256_bytes(array.tobytes(order='C'))}"
            )
        return sha256_bytes("\n".join(rader).encode("utf-8"))

    # ------------------------------------------------------------------ uppslag

    def ar_rest(self, mottagare_index: Heltal, kalla_index: Heltal) -> NDArray[np.bool_]:
        """Sant för de par som ligger i ett restintervall (inte beräknade)."""
        resultat = np.zeros(np.asarray(mottagare_index).shape, dtype=np.bool_)
        if not self.har_rest or resultat.size == 0:
            return resultat
        position = np.empty(self.antal_kallor, dtype=np.int64)
        position[self.kall_ordning] = np.arange(self.antal_kallor, dtype=np.int64)
        bas = self.antal_kallor + 1
        restnyckel = self.rest_mottagare_index * bas + self.rest_start
        parnyckel = np.asarray(mottagare_index) * bas + position[np.asarray(kalla_index)]
        kandidat = np.searchsorted(restnyckel, parnyckel, side="right") - 1
        finns = kandidat >= 0
        k = np.where(finns, kandidat, 0)
        resultat = (
            finns
            & (self.rest_mottagare_index[k] == np.asarray(mottagare_index))
            & (position[np.asarray(kalla_index)] < self.rest_slut[k])
        )
        return np.asarray(resultat, dtype=np.bool_)

    def radgranser(self) -> Heltal:
        """Radpekare: paren för mottagare `m` ligger i `[radgranser[m], radgranser[m + 1])`."""
        return np.asarray(
            np.searchsorted(
                self.par_mottagare_index, np.arange(self.antal_mottagare + 1, dtype=np.int64)
            ),
            dtype=np.int64,
        )

    # ------------------------------------------------------------------ omkombination

    def _kontrollera_emission(self, lw_db: Flyttal) -> Flyttal:
        lw = np.asarray(lw_db, dtype=np.float64)
        if lw.shape != (self.antal_kallor, ANTAL_BAND):
            raise BullerFel(
                f"lw_db har formen {lw.shape}, förväntade {(self.antal_kallor, ANTAL_BAND)}."
            )
        if bool(np.any(np.isnan(lw))) or bool(np.any(lw == np.inf)):
            raise BullerFel("lw_db innehåller NaN eller +inf.")
        return lw

    def niva_db(self, lw_db: Flyttal) -> Flyttal:
        """Nivå per mottagare och band för emissionen `lw_db[n_kallor, band]` (KB2).

        `L[m, b] = 10·lg Σ_v vikt_v · Σ_par 10^((L_W[källa, b] + H_db[par, v, b]) / 10)`.
        Summan räknas som log-sum-exp i float64. Mottagare utan lagrade par får -inf.
        Restpar (se modulens docstring) ingår inte; deras största möjliga bidrag ges av
        `felgrans_db`.
        """
        lw = self._kontrollera_emission(lw_db)
        n_m, n_v = self.antal_mottagare, self.antal_vaderklasser
        niva = np.full((n_m, ANTAL_BAND), -np.inf, dtype=np.float64)
        if self.antal_par == 0:
            return niva
        with np.errstate(divide="ignore"):
            vikt_db = 10.0 * np.log10(self.vaderklass_vikter)[np.newaxis, :, np.newaxis]
        granser = self.radgranser()
        max_par = max(1, _MAX_ELEMENT_PER_BLOCK // (n_v * ANTAL_BAND))
        m0 = 0
        while m0 < n_m:
            m1 = int(np.searchsorted(granser, granser[m0] + max_par, side="right")) - 1
            m1 = min(max(m1, m0 + 1), n_m)
            p0, p1 = int(granser[m0]), int(granser[m1])
            if p1 > p0:
                antal = np.diff(granser[m0 : m1 + 1])
                har_par = antal > 0
                start = (granser[m0:m1] - p0)[har_par]
                x_db = (
                    self.h_db[p0:p1] + lw[self.par_kalla_index[p0:p1]][:, np.newaxis, :] + vikt_db
                )
                topp = np.maximum.reduceat(np.max(x_db, axis=1), start, axis=0)
                topp = np.where(np.isfinite(topp), topp, 0.0)
                topp_per_par = np.repeat(topp, antal[har_par], axis=0)[:, np.newaxis, :]
                energi = np.sum(np.power(10.0, (x_db - topp_per_par) / 10.0), axis=1)
                summa = np.add.reduceat(energi, start, axis=0)
                with np.errstate(divide="ignore"):
                    niva[m0:m1][har_par] = topp + 10.0 * np.log10(summa)
            m0 = m1
        return niva

    def niva_a_db(self, lw_db: Flyttal, a_vagning_db: Flyttal) -> Flyttal:
        """A-vägd totalnivå per mottagare, form (n_mottagare,)."""
        return a_vagd_niva_db(self.niva_db(lw_db), a_vagning_db)

    def restenergi(self, lw_db: Flyttal, a_vagning_db: Flyttal) -> Flyttal:
        """Övre gräns för restparens sammanlagda A-vägda energi per mottagare (linjär).

        För varje restintervall: intervallets sammanlagda effekt per band gånger övre gränsen
        för överföringen, A-vägt och summerat över banden. Gäller för vilken emission som helst.
        """
        lw = self._kontrollera_emission(lw_db)
        a_vagning = _kontrollera_vagning(a_vagning_db)
        rest = np.zeros(self.antal_mottagare, dtype=np.float64)
        if not self.har_rest:
            return rest
        n_k = self.antal_kallor
        effekt = np.zeros((n_k + 1, ANTAL_BAND), dtype=np.float64)
        effekt[:n_k] = db_till_energi(lw[self.kall_ordning] + a_vagning[np.newaxis, :])
        # Samma intervall förekommer för många mottagare: summera varje unikt intervall en gång.
        nyckel = self.rest_start * (n_k + 1) + self.rest_slut
        unika, omvand = np.unique(nyckel, return_inverse=True)
        blandat = np.empty(2 * unika.shape[0], dtype=np.int64)
        blandat[0::2] = unika // (n_k + 1)
        blandat[1::2] = unika % (n_k + 1)
        intervalleffekt = np.add.reduceat(effekt, blandat, axis=0)[0::2]
        per_rad = intervalleffekt[omvand]
        with np.errstate(over="ignore", invalid="ignore"):
            bidrag = per_rad * db_till_energi(self.rest_grans_db)
        bidrag = np.where(per_rad > 0.0, bidrag, 0.0)
        np.add.at(rest, self.rest_mottagare_index, np.sum(bidrag, axis=1))
        return rest

    def felgrans_db(self, lw_db: Flyttal, a_vagning_db: Flyttal) -> Flyttal:
        """Garanterad gräns för felet i A-vägd totalnivå per mottagare, i dB.

        Den sanna nivån (alla par beräknade) ligger i intervallet
        `[niva_a_db, niva_a_db + felgrans_db]`, förutsatt att den övre gränsen som lagret
        byggdes med håller (se `buller.operator.klustring.ANTAGANDE_OVRE_GRANS`). Utan
        restpar är gränsen 0. Har mottagaren restpar men ingen beräknad energi är den +inf.
        """
        rest = self.restenergi(lw_db, a_vagning_db)
        exakt = db_till_energi(self.niva_a_db(lw_db, a_vagning_db))
        grans = np.zeros(self.antal_mottagare, dtype=np.float64)
        har_rest = rest > 0.0
        utan_energi = har_rest & (exakt <= 0.0)
        raknas = har_rest & ~utan_energi
        grans[utan_energi] = np.inf
        with np.errstate(over="ignore"):
            grans[raknas] = 10.0 * np.log10(1.0 + rest[raknas] / exakt[raknas])
        return grans

    # ------------------------------------------------------------------ fil

    def spara(self, sokvag: str | Path) -> Path:
        """Sparar lagret som `.npz` med deterministiskt innehåll (samma lager, samma byte)."""
        arrayer: dict[str, NDArray[np.generic]] = {
            "format": np.array(LAGERFORMAT),
            "identitet": np.array(self.identitet),
        }
        for namn in _ARRAYFALT:
            arrayer[namn] = getattr(self, namn)
        return skriv_npz_deterministiskt(sokvag, arrayer)


def las_overforingslager(sokvag: str | Path) -> Overforingslager:
    """Läser ett lager från `.npz` och kontrollerar att innehållet stämmer med identiteten."""
    kalla = Path(sokvag)
    try:
        with np.load(kalla, allow_pickle=False) as data:
            filer = set(data.files)
            saknas = sorted(({"format", "identitet"} | set(_ARRAYFALT)) - filer)
            if saknas:
                raise BullerFel(f"Lagerfilen {kalla} saknar: {', '.join(saknas)}.")
            if str(data["format"]) != LAGERFORMAT:
                raise BullerFel(
                    f"Lagerfilen {kalla} har formatet {data['format']!s}, "
                    f"förväntade {LAGERFORMAT}."
                )
            sparad_identitet = str(data["identitet"])
            lager = Overforingslager(**{namn: data[namn] for namn in _ARRAYFALT})
    except (OSError, ValueError) as fel:
        raise BullerFel(f"Kunde inte läsa lagerfilen {kalla}: {fel}") from fel
    if lager.identitet != sparad_identitet:
        raise BullerFel(
            f"Lagerfilen {kalla} är skadad eller ändrad: innehållet ger identiteten "
            f"{lager.identitet}, filen anger {sparad_identitet}."
        )
    return lager


def _kontrollera_vagning(a_vagning_db: Flyttal) -> Flyttal:
    a_vagning = np.asarray(a_vagning_db, dtype=np.float64)
    if a_vagning.shape != (ANTAL_BAND,) or not bool(np.all(np.isfinite(a_vagning))):
        raise BullerFel(f"a_vagning_db ska ha formen ({ANTAL_BAND},) och vara ändlig.")
    return a_vagning


def a_vagd_niva_db(niva_db: Flyttal, a_vagning_db: Flyttal) -> Flyttal:
    """A-vägd totalnivå: vägning per band och energisumma över banden (KB2).

    `niva_db` har banden i sista axeln. `a_vagning_db` (form (ANTAL_BAND,)) kommer från
    `buller.akustik.band.a_vagning_db()`; inga vägningstal finns i den här modulen.
    """
    a_vagning = _kontrollera_vagning(a_vagning_db)
    return energisumma_db(np.asarray(niva_db, dtype=np.float64) + a_vagning, axel=-1)
