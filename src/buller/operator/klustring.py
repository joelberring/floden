"""Bygge av överföringslager, med eller utan källklustring med garanterad felgräns.

Klustringen går till så här för varje mottagare:

1. Källorna ligger i ett kd-träd. För en nod räknas en övre gräns för nodens A-vägda bidrag:
   nodens sammanlagda effekt per band gånger övre gränsen för överföringen på nodens minsta
   avstånd till mottagaren.
2. Noderna behandlas i fallande ordning efter gräns. En inre nod delas i sina barn. Ett löv
   beräknas exakt med parberäknaren.
3. Arbetet slutar när `Σ_rest <= (10^(ε/10) − 1) · E_exakt`, där `Σ_rest` är summan av de
   kvarvarande nodernas övre gränser och `E_exakt` den hittills exakt beräknade A-vägda energin.

**Val: kvarvarande noder utelämnas** ur nivån i stället för att ersättas av en representant.
Då är felet ensidigt och gränsen sträng: den sanna A-vägda nivån ligger i
`[L_A, L_A + felgräns]` med `felgräns = 10·lg(1 + Σ_rest / E_exakt) <= ε`. En representant
(en källa i nodens tyngdpunkt) skulle ofta ge ett mindre medelfel, men dess överföring är inte
en gräns för något, felet blir tvåsidigt och parberäknaren kan bara räkna verkliga källindex.
Restnodernas övre gränser sparas i lagret, så felgränsen kan räknas om för en ny emission.

Garantin gäller A-vägd totalnivå, inte enskilda band.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from buller.operator.energi import db_till_energi
from buller.operator.granssnitt import OvreGrans, Parberaknare
from buller.operator.kalltrad import Kalltrad, bygg_kalltrad
from buller.operator.lager import Overforingslager, _kontrollera_vagning
from buller.typer import ANTAL_BAND, BullerFel, Flyttal

Heltal = NDArray[np.int64]

ANTAGANDE_OVRE_GRANS = (
    "Felgränsen för klustringen förutsätter att den inskickade övre gränsen håller: för varje "
    "par med planavstånd >= d, varje väderklass och varje band är överföringen H_db <= "
    "ovre_grans(d). Gränsen måste alltså täcka all förstärkning som kärnan kan ge (mark- och "
    "fasadreflexer, medvind) och får inte växa med avståndet. Kontrolleras bara för de par "
    "som faktiskt beräknas."
)

STANDARD_LOVSTORLEK = 8
# Tolerans i dB vid kontroll av att beräknade par inte överskrider övre gränsen.
_GRANSKONTROLL_TOLERANS_DB = 1e-9
_SYNKINTERVALL = 64


@dataclass(frozen=True)
class Klustringsstatistik:
    """Hur mycket arbete ett lagerbygge krävde."""

    antal_mottagare: int
    antal_kallor: int
    antal_mojliga_par: int
    antal_beraknade_par: int
    antal_par_i_lager: int
    antal_restnoder: int
    antal_parberakningsanrop: int
    felgrans_db: float
    maxradie_m: float | None

    @property
    def andel_beraknade_par(self) -> float:
        """Andel av alla par (mottagare × källa) som beräknades med parberäknaren."""
        if self.antal_mojliga_par == 0:
            return 0.0
        return self.antal_beraknade_par / self.antal_mojliga_par


@dataclass(frozen=True, eq=False)
class _Mottagarrader:
    """Resultat för en mottagare: beräknade par och kvarvarande restnoder."""

    kalla_index: Heltal
    h_db: Flyttal
    rest_start: Heltal
    rest_slut: Heltal
    rest_grans_db: Flyttal
    antal_beraknade_par: int
    antal_anrop: int


def _kontrollera_parsvar(h_db: Flyttal, antal_kallor: int, antal_vaderklasser: int) -> Flyttal:
    h = np.asarray(h_db, dtype=np.float64)
    forvantad = (antal_kallor, antal_vaderklasser, ANTAL_BAND)
    if h.shape != forvantad:
        raise BullerFel(f"Parberäknaren gav formen {h.shape}, förväntade {forvantad}.")
    if bool(np.any(np.isnan(h))) or bool(np.any(h == np.inf)):
        raise BullerFel("Parberäknaren gav NaN eller +inf.")
    return h


class _Klustrare:
    """Klustring för en given scen, emission och felgräns. Återanvänds över mottagarna."""

    def __init__(
        self,
        *,
        trad: Kalltrad,
        vaderklass_vikter: Flyttal,
        parberaknare: Parberaknare,
        ovre_grans: OvreGrans,
        lw_db: Flyttal,
        a_vagning_db: Flyttal,
        felgrans_db: float,
        maxradie_m: float | None,
        kontrollera_ovre_grans: bool,
    ) -> None:
        if not math.isfinite(felgrans_db) or felgrans_db < 0.0:
            raise BullerFel("felgrans_db ska vara ändlig och >= 0.")
        if maxradie_m is not None and not maxradie_m > 0.0:
            raise BullerFel("maxradie_m ska vara > 0 (eller None för ingen gräns).")
        self._trad = trad
        self._vikter = np.asarray(vaderklass_vikter, dtype=np.float64)
        self._par = parberaknare
        self._ovre_grans = ovre_grans
        self._lw = np.asarray(lw_db, dtype=np.float64)
        self._a_energi = db_till_energi(_kontrollera_vagning(a_vagning_db))
        self._faktor = 10.0 ** (felgrans_db / 10.0) - 1.0
        self._maxradie_m = maxradie_m
        self._kontrollera = kontrollera_ovre_grans
        # A-vägd effekt per nod och band (linjär), summerad över nodens källor.
        effekt = np.zeros((self._lw.shape[0] + 1, ANTAL_BAND), dtype=np.float64)
        effekt[:-1] = db_till_energi(self._lw[trad.ordning]) * self._a_energi
        blandat = np.empty(2 * trad.antal_noder, dtype=np.int64)
        blandat[0::2] = trad.nod_start
        blandat[1::2] = trad.nod_slut
        self._nodeffekt = (
            np.add.reduceat(effekt, blandat, axis=0)[0::2]
            if trad.antal_noder
            else np.zeros((0, ANTAL_BAND))
        )
        self._nod_har_effekt = self._nodeffekt > 0.0
        # Python-listor: snabbare än numpy-skalärer i den inre slingan.
        self._e_min: list[float] = trad.nod_e_min_m.tolist()
        self._e_max: list[float] = trad.nod_e_max_m.tolist()
        self._n_min: list[float] = trad.nod_n_min_m.tolist()
        self._n_max: list[float] = trad.nod_n_max_m.tolist()
        self._vanster: list[int] = trad.nod_vanster.tolist()
        self._hoger: list[int] = trad.nod_hoger.tolist()
        self._start: list[int] = trad.nod_start.tolist()
        self._slut: list[int] = trad.nod_slut.tolist()

    def _nodgrans(self, nod: int, e_m: float, n_m: float) -> tuple[float, float, Flyttal]:
        """(övre gräns för nodens A-vägda energi, minsta avstånd, gräns per band i dB)."""
        de = max(self._e_min[nod] - e_m, 0.0, e_m - self._e_max[nod])
        dn = max(self._n_min[nod] - n_m, 0.0, n_m - self._n_max[nod])
        avstand_m = math.hypot(de, dn)
        if avstand_m <= 0.0:
            # Mottagaren ligger inom nodens utbredning: ingen ändlig gräns finns, och
            # övre gränsen är bara definierad för positiva avstånd. Noden måste delas.
            grans_db = np.full(ANTAL_BAND, np.inf, dtype=np.float64)
        else:
            grans_db = np.asarray(self._ovre_grans(avstand_m), dtype=np.float64)
        if grans_db.shape != (ANTAL_BAND,):
            raise BullerFel(f"Övre gränsen ska ha formen ({ANTAL_BAND},).")
        energi = float(np.dot(self._nodeffekt[nod], np.power(10.0, grans_db / 10.0)))
        if math.isnan(energi):
            # Ovanligt fall: NaN i gränsen, eller +inf i ett band där noden saknar effekt.
            if bool(np.any(np.isnan(grans_db))):
                raise BullerFel("Övre gränsen får inte vara NaN.")
            per_band = self._nodeffekt[nod] * np.power(10.0, grans_db / 10.0)
            energi = float(np.sum(per_band[self._nod_har_effekt[nod]]))
        return energi, avstand_m, grans_db

    def _berakna(
        self, mottagare_index: int, lov: list[int], lovgrans_db: dict[int, Flyttal]
    ) -> tuple[Heltal, Flyttal, float]:
        """Beräknar lövens par exakt. Returnerar källindex, H och A-vägd energi."""
        ordning = self._trad.ordning
        kallor = np.concatenate([ordning[self._start[j] : self._slut[j]] for j in lov])
        h_db = _kontrollera_parsvar(
            self._par(mottagare_index, kallor.tolist()), kallor.shape[0], self._vikter.shape[0]
        )
        if self._kontrollera:
            storlek = [self._slut[j] - self._start[j] for j in lov]
            grans = np.repeat(np.stack([lovgrans_db[j] for j in lov]), storlek, axis=0)
            if bool(np.any(np.max(h_db, axis=1) > grans + _GRANSKONTROLL_TOLERANS_DB)):
                raise BullerFel(
                    f"Övre gränsen håller inte för mottagarindex {mottagare_index}: ett beräknat "
                    "par har högre överföring än gränsen. Felgränsen kan då inte garanteras."
                )
        per_band = np.sum(
            db_till_energi(h_db + self._lw[kallor][:, np.newaxis, :])
            * self._vikter[np.newaxis, :, np.newaxis],
            axis=(0, 1),
        )
        return kallor, h_db, float(np.sum(per_band * self._a_energi))

    def klustra(self, mottagare_index: int, e_m: float, n_m: float) -> _Mottagarrader:
        """Förfinar trädet för en mottagare tills felgränsen är garanterad."""
        with np.errstate(over="ignore", invalid="ignore"):
            return self._klustra(mottagare_index, e_m, n_m)

    def _klustra(self, mottagare_index: int, e_m: float, n_m: float) -> _Mottagarrader:
        n_v = int(self._vikter.shape[0])
        hog: list[tuple[float, int]] = []  # (-gräns, nod): störst gräns först
        grans_db: dict[int, Flyttal] = {}
        last: list[int] = []  # noder bortom maxradien: delas och beräknas aldrig
        last_summa = 0.0
        hog_andlig = 0.0  # summan av ändliga gränser i högen
        hog_oandliga = 0
        vantande: list[int] = []  # löv som ska beräknas i nästa anrop
        vantande_summa = 0.0
        exakt = 0.0
        kallor_delar: list[Heltal] = []
        h_delar: list[Flyttal] = []
        antal_anrop = 0

        def lagg_till(nod: int) -> None:
            nonlocal last_summa, hog_andlig, hog_oandliga
            energi, avstand_m, per_band_db = self._nodgrans(nod, e_m, n_m)
            grans_db[nod] = per_band_db
            if self._maxradie_m is not None and avstand_m > self._maxradie_m:
                last.append(nod)
                last_summa += energi
                return
            heapq.heappush(hog, (-energi, nod))
            if math.isinf(energi):
                hog_oandliga += 1
            else:
                hog_andlig += energi

        if self._trad.antal_noder:
            lagg_till(0)
        varv = 0
        while True:
            varv += 1
            if varv % _SYNKINTERVALL == 0:
                hog_andlig = math.fsum(-g for g, _ in hog if not math.isinf(g))
            troskel = self._faktor * (exakt + vantande_summa) if self._faktor > 0.0 else 0.0
            rest = math.inf if hog_oandliga else hog_andlig + last_summa
            kan_sluta = not hog or rest <= troskel
            if kan_sluta and hog and not hog_oandliga:
                # Löpande summa kan ha avrundningsfel: räkna om exakt före beslut.
                hog_andlig = math.fsum(-g for g, _ in hog)
                kan_sluta = hog_andlig + last_summa <= troskel
            if kan_sluta:
                if not vantande:
                    break
                kallor, h_db, energi = self._berakna(mottagare_index, vantande, grans_db)
                kallor_delar.append(kallor)
                h_delar.append(h_db)
                exakt += energi
                antal_anrop += 1
                vantande = []
                vantande_summa = 0.0
                continue
            negativ_grans, nod = heapq.heappop(hog)
            if math.isinf(negativ_grans):
                hog_oandliga -= 1
            else:
                hog_andlig += negativ_grans
            if self._vanster[nod] < 0:
                vantande.append(nod)
                vantande_summa += -negativ_grans
            else:
                lagg_till(self._vanster[nod])
                lagg_till(self._hoger[nod])

        # Kvarvarande noder blir rest. Noder vars gräns är -inf i alla band kan aldrig bidra
        # (under antagandet) och räknas som par utan bana.
        restnoder = sorted(
            nod
            for nod in [j for _, j in hog] + last
            if bool(np.any(grans_db[nod] > -np.inf))
        )
        if kallor_delar:
            kallor = np.concatenate(kallor_delar)
            h_db = np.concatenate(h_delar, axis=0)
            sortering = np.argsort(kallor, kind="stable")
            kallor, h_db = kallor[sortering], h_db[sortering]
        else:
            kallor = np.zeros(0, dtype=np.int64)
            h_db = np.zeros((0, n_v, ANTAL_BAND), dtype=np.float64)
        return _Mottagarrader(
            kalla_index=kallor,
            h_db=h_db,
            rest_start=np.asarray([self._start[j] for j in restnoder], dtype=np.int64),
            rest_slut=np.asarray([self._slut[j] for j in restnoder], dtype=np.int64),
            rest_grans_db=(
                np.stack([grans_db[j] for j in restnoder])
                if restnoder
                else np.zeros((0, ANTAL_BAND), dtype=np.float64)
            ),
            antal_beraknade_par=int(kallor.shape[0]),
            antal_anrop=antal_anrop,
        )


def _kontrollera_scen(
    mottagare_e_m: Flyttal, mottagare_n_m: Flyttal, kalla_e_m: Flyttal, lw_db: Flyttal
) -> None:
    if np.asarray(mottagare_e_m).shape != np.asarray(mottagare_n_m).shape:
        raise BullerFel("Mottagarnas koordinater ska vara lika långa.")
    forvantad = (np.asarray(kalla_e_m).shape[0], ANTAL_BAND)
    lw = np.asarray(lw_db)
    if lw.shape != forvantad:
        raise BullerFel(f"lw_db har formen {lw.shape}, förväntade {forvantad}.")
    if bool(np.any(np.isnan(lw))) or bool(np.any(lw == np.inf)):
        raise BullerFel("lw_db innehåller NaN eller +inf.")


def _satt_ihop(
    rader: Sequence[tuple[int, _Mottagarrader]], n_v: int
) -> tuple[Heltal, Heltal, Flyttal, Heltal, Heltal, Heltal, Flyttal]:
    """Slår ihop rader per mottagare till lagrets arrayer."""
    if not rader:
        tom = np.zeros(0, dtype=np.int64)
        return (
            tom,
            tom,
            np.zeros((0, n_v, ANTAL_BAND)),
            tom,
            tom,
            tom,
            np.zeros((0, ANTAL_BAND)),
        )
    return (
        np.concatenate([np.full(r.kalla_index.shape[0], m, dtype=np.int64) for m, r in rader]),
        np.concatenate([r.kalla_index for _, r in rader]),
        np.concatenate([r.h_db for _, r in rader], axis=0),
        np.concatenate([np.full(r.rest_start.shape[0], m, dtype=np.int64) for m, r in rader]),
        np.concatenate([r.rest_start for _, r in rader]),
        np.concatenate([r.rest_slut for _, r in rader]),
        np.concatenate([r.rest_grans_db for _, r in rader], axis=0),
    )


def bygg_overforingslager(
    *,
    mottagare_id: Heltal,
    mottagare_e_m: Flyttal,
    mottagare_n_m: Flyttal,
    kalla_id: Heltal,
    kalla_e_m: Flyttal,
    kalla_n_m: Flyttal,
    vaderklass_vikter: Flyttal,
    parberaknare: Parberaknare,
    ovre_grans: OvreGrans,
    lw_db: Flyttal,
    a_vagning_db: Flyttal,
    felgrans_db: float,
    maxradie_m: float | None = None,
    lovstorlek: int = STANDARD_LOVSTORLEK,
    kontrollera_ovre_grans: bool = True,
) -> tuple[Overforingslager, Klustringsstatistik]:
    """Bygger ett överföringslager med källklustring och garanterad felgräns.

    För varje mottagare beräknas par tills felet i A-vägd totalnivå för emissionen `lw_db`
    garanterat är högst `felgrans_db`. Övriga par utelämnas och deras övre gräns sparas som
    rest i lagret. `felgrans_db = 0` beräknar alla par vars övre gräns är större än noll.

    Antagande (se `ANTAGANDE_OVRE_GRANS`): garantin gäller bara om `ovre_grans(d)` verkligen
    är en övre gräns för överföringen för alla par på planavstånd minst `d`. Med
    `kontrollera_ovre_grans` kastas `BullerFel` om ett *beräknat* par bryter mot gränsen;
    oberäknade par kan inte kontrolleras.

    `maxradie_m`: trädnoder vars närmaste punkt ligger längre bort än så beräknas aldrig.
    (Ett löv som delvis ligger inom radien beräknas helt, så enstaka par strax utanför kan
    komma med.) De utelämnade nodernas övre gräns ingår i felgränsen, som då kan bli större
    än `felgrans_db`. Läs den verkliga gränsen med
    `Overforingslager.felgrans_db`.

    Felgränsen gäller emissionen lagret byggdes för. För en annan emission räknar
    `Overforingslager.felgrans_db` fram den gräns som då gäller (den kan vara större).
    """
    _kontrollera_scen(mottagare_e_m, mottagare_n_m, kalla_e_m, lw_db)
    vikter = np.asarray(vaderklass_vikter, dtype=np.float64)
    trad = bygg_kalltrad(kalla_e_m, kalla_n_m, lovstorlek)
    klustrare = _Klustrare(
        trad=trad,
        vaderklass_vikter=vikter,
        parberaknare=parberaknare,
        ovre_grans=ovre_grans,
        lw_db=lw_db,
        a_vagning_db=a_vagning_db,
        felgrans_db=felgrans_db,
        maxradie_m=maxradie_m,
        kontrollera_ovre_grans=kontrollera_ovre_grans,
    )
    e_lista: list[float] = np.asarray(mottagare_e_m, dtype=np.float64).tolist()
    n_lista: list[float] = np.asarray(mottagare_n_m, dtype=np.float64).tolist()
    rader = [(m, klustrare.klustra(m, e_lista[m], n_lista[m])) for m in range(len(e_lista))]
    par_m, par_k, h_db, rest_m, rest_start, rest_slut, rest_grans = _satt_ihop(
        rader, int(vikter.shape[0])
    )
    lager = Overforingslager.skapa(
        mottagare_id=mottagare_id,
        mottagare_e_m=mottagare_e_m,
        mottagare_n_m=mottagare_n_m,
        kalla_id=kalla_id,
        kalla_e_m=kalla_e_m,
        kalla_n_m=kalla_n_m,
        vaderklass_vikter=vikter,
        par_mottagare_index=par_m,
        par_kalla_index=par_k,
        h_db=h_db,
        kall_ordning=trad.ordning,
        rest_mottagare_index=rest_m,
        rest_start=rest_start,
        rest_slut=rest_slut,
        rest_grans_db=rest_grans,
    )
    statistik = Klustringsstatistik(
        antal_mottagare=lager.antal_mottagare,
        antal_kallor=lager.antal_kallor,
        antal_mojliga_par=lager.antal_mottagare * lager.antal_kallor,
        antal_beraknade_par=sum(r.antal_beraknade_par for _, r in rader),
        antal_par_i_lager=lager.antal_par,
        antal_restnoder=int(rest_m.shape[0]),
        antal_parberakningsanrop=sum(r.antal_anrop for _, r in rader),
        felgrans_db=felgrans_db,
        maxradie_m=maxradie_m,
    )
    return lager, statistik


def bygg_fullt_lager(
    *,
    mottagare_id: Heltal,
    mottagare_e_m: Flyttal,
    mottagare_n_m: Flyttal,
    kalla_id: Heltal,
    kalla_e_m: Flyttal,
    kalla_n_m: Flyttal,
    vaderklass_vikter: Flyttal,
    parberaknare: Parberaknare,
) -> tuple[Overforingslager, Klustringsstatistik]:
    """Oklustrad körning: alla par beräknas, ett anrop per mottagare. Ingen övre gräns behövs."""
    vikter = np.asarray(vaderklass_vikter, dtype=np.float64)
    n_m = int(np.asarray(mottagare_id).shape[0])
    n_k = int(np.asarray(kalla_id).shape[0])
    alla = list(range(n_k))
    tom = np.zeros(0, dtype=np.int64)
    rader: list[tuple[int, _Mottagarrader]] = []
    for m in range(n_m if n_k else 0):
        h_db = _kontrollera_parsvar(parberaknare(m, alla), n_k, int(vikter.shape[0]))
        rader.append(
            (
                m,
                _Mottagarrader(
                    kalla_index=np.arange(n_k, dtype=np.int64),
                    h_db=h_db,
                    rest_start=tom,
                    rest_slut=tom,
                    rest_grans_db=np.zeros((0, ANTAL_BAND)),
                    antal_beraknade_par=n_k,
                    antal_anrop=1,
                ),
            )
        )
    par_m, par_k, h_alla, *_ = _satt_ihop(rader, int(vikter.shape[0]))
    lager = Overforingslager.skapa(
        mottagare_id=mottagare_id,
        mottagare_e_m=mottagare_e_m,
        mottagare_n_m=mottagare_n_m,
        kalla_id=kalla_id,
        kalla_e_m=kalla_e_m,
        kalla_n_m=kalla_n_m,
        vaderklass_vikter=vikter,
        par_mottagare_index=par_m,
        par_kalla_index=par_k,
        h_db=h_alla,
    )
    statistik = Klustringsstatistik(
        antal_mottagare=n_m,
        antal_kallor=n_k,
        antal_mojliga_par=n_m * n_k,
        antal_beraknade_par=len(rader) * n_k,
        antal_par_i_lager=lager.antal_par,
        antal_restnoder=0,
        antal_parberakningsanrop=len(rader),
        felgrans_db=0.0,
        maxradie_m=None,
    )
    return lager, statistik


def omklustra_mottagare(
    lager: Overforingslager,
    mottagare_index: Sequence[int],
    *,
    parberaknare: Parberaknare,
    ovre_grans: OvreGrans,
    lw_db: Flyttal,
    a_vagning_db: Flyttal,
    felgrans_db: float,
    maxradie_m: float | None = None,
    lovstorlek: int = STANDARD_LOVSTORLEK,
    kontrollera_ovre_grans: bool = True,
) -> tuple[Overforingslager, Klustringsstatistik]:
    """Bygger om raderna för utvalda mottagare (t.ex. när felgränsen inte längre hålls).

    Övriga mottagares par och restintervall behålls oförändrade. Resultatet är ett nytt lager
    med ny identitet. Statistiken avser bara de omklustrade mottagarna.
    """
    urval = sorted(set(int(m) for m in mottagare_index))
    if urval and (urval[0] < 0 or urval[-1] >= lager.antal_mottagare):
        raise BullerFel("mottagare_index pekar utanför lagrets mottagare.")
    _kontrollera_scen(lager.mottagare_e_m, lager.mottagare_n_m, lager.kalla_e_m, lw_db)
    trad = bygg_kalltrad(lager.kalla_e_m, lager.kalla_n_m, lovstorlek)
    if lager.har_rest and not np.array_equal(trad.ordning, lager.kall_ordning):
        raise BullerFel(
            "Lagrets restintervall bygger på en annan källordning (annan lovstorlek). "
            "Bygg om hela lagret."
        )
    klustrare = _Klustrare(
        trad=trad,
        vaderklass_vikter=lager.vaderklass_vikter,
        parberaknare=parberaknare,
        ovre_grans=ovre_grans,
        lw_db=lw_db,
        a_vagning_db=a_vagning_db,
        felgrans_db=felgrans_db,
        maxradie_m=maxradie_m,
        kontrollera_ovre_grans=kontrollera_ovre_grans,
    )
    rader = [
        (m, klustrare.klustra(m, float(lager.mottagare_e_m[m]), float(lager.mottagare_n_m[m])))
        for m in urval
    ]
    par_m, par_k, h_db, rest_m, rest_start, rest_slut, rest_grans = _satt_ihop(
        rader, lager.antal_vaderklasser
    )
    urvalsarray = np.asarray(urval, dtype=np.int64)
    behall_par = ~np.isin(lager.par_mottagare_index, urvalsarray)
    behall_rest = ~np.isin(lager.rest_mottagare_index, urvalsarray)
    nytt = Overforingslager.skapa(
        mottagare_id=lager.mottagare_id,
        mottagare_e_m=lager.mottagare_e_m,
        mottagare_n_m=lager.mottagare_n_m,
        kalla_id=lager.kalla_id,
        kalla_e_m=lager.kalla_e_m,
        kalla_n_m=lager.kalla_n_m,
        vaderklass_vikter=lager.vaderklass_vikter,
        par_mottagare_index=np.concatenate([lager.par_mottagare_index[behall_par], par_m]),
        par_kalla_index=np.concatenate([lager.par_kalla_index[behall_par], par_k]),
        h_db=np.concatenate([lager.h_db[behall_par], h_db], axis=0),
        kall_ordning=trad.ordning,
        rest_mottagare_index=np.concatenate([lager.rest_mottagare_index[behall_rest], rest_m]),
        rest_start=np.concatenate([lager.rest_start[behall_rest], rest_start]),
        rest_slut=np.concatenate([lager.rest_slut[behall_rest], rest_slut]),
        rest_grans_db=np.concatenate([lager.rest_grans_db[behall_rest], rest_grans], axis=0),
    )
    statistik = Klustringsstatistik(
        antal_mottagare=len(urval),
        antal_kallor=lager.antal_kallor,
        antal_mojliga_par=len(urval) * lager.antal_kallor,
        antal_beraknade_par=sum(r.antal_beraknade_par for _, r in rader),
        antal_par_i_lager=nytt.antal_par,
        antal_restnoder=int(rest_m.shape[0]),
        antal_parberakningsanrop=sum(r.antal_anrop for _, r in rader),
        felgrans_db=felgrans_db,
        maxradie_m=maxradie_m,
    )
    return nytt, statistik
