"""Import av testfallsblad som exporterats från Excel till CSV (ett blad per fil).

Excel-filerna till Miljøprojekt 1335/2010 och RL 18-18 är i det äldre binära formatet
`.xls`, som inte går att läsa med standardbiblioteket. Antingen exporteras varje blad till
CSV (från Excel eller LibreOffice) och läses här, eller så läses arbetsböckerna direkt med
`buller.testfall.xlsimport` (kräver xlrd), som använder samma bladtolkning.

Bladens uppställning är bara känd från exemplet i rapportens bilaga A, som texten återger
utan cellpositioner. Importen letar därför efter *etiketter* i stället för fasta celler:

- parametrar (`Nvc`, `v`, `T`, `hr`, `t0`, `z0`, `zu`, `u`, `su`, `dt/dz`, `sdt/dz`, `Cv2`,
  `Ct2`, vindriktningen `φ`): värdet är första talet till höger om etiketten på samma rad,
- A-vägda totalnivåer (`LAeq,24h`, `LAE`, `LAmax`, `Lden`): på samma sätt,
- terrängprofilen: raden med rubrikerna `X`, `Z`, `Flow res.`, `Roughn.`; talraderna under,
- spektrum: raden med `Freq.`; kolumnerna till höger om den är storheter (`Leq,24h`, `LE`,
  `dL`, …) och raderna under har frekvensen i `Freq.`-kolumnen.

Det som måste kontrolleras när de riktiga filerna finns står i `docs/buller/logg/B1-B5.md`.
Importen sätter aldrig `ar_facit = true` på eget bevåg: det anges av den som har kontrollerat
filens version.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from buller.testfall.format import (
    TYP_RAK_VAG,
    Facit,
    Facitserie,
    N2kFall,
    Terrangdata,
    Trafikpost,
    Vagdata,
)
from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel, Vader

Celler = list[list[str]]

# Etikett i bladet (normaliserad) → fältnamn. Etiketterna är avlästa ur bilaga A.
_VADERETIKETTER = {
    "t0": "temperatur_c",
    "z0": "z0_m",
    "zu": "zu_m",
    "u": "vind_m_s",
    "su": "std_vind_m_s",
    "dt/dz": "dt_dz_k_m",
    "sdt/dz": "std_dt_dz_k_m",
    "cv2": "cv2",
    "ct2": "ct2",
}
_VINDRIKTNING = ("φ", "ϕ", "phi")
_TOTALETIKETTER = {"laeq,24h": "LAeq_24h", "lae": "LAE", "lamax": "LAmax", "lden": "Lden"}
_SPEKTRUMETIKETTER = {"leq,24h": "Leq_24h", "le": "LE", "dl": "dL", "lden": "Lden"}
_TERRANGRUBRIKER = ("x", "z", "flowres.", "roughn.")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower().replace("²", "2").replace("(db)", "")


def _som_tal(text: str) -> float | None:
    rensad = text.strip().replace("−", "-").replace(" ", "")
    if "," in rensad and "." not in rensad:
        rensad = rensad.replace(",", ".")
    try:
        varde = float(rensad)
    except ValueError:
        return None
    return varde if np.isfinite(varde) else None


def las_csv_blad(fil: str | Path, avgransare: str | None = None) -> Celler:
    """Läser ett CSV-exporterat blad till en lista av rader med celltext.

    Avgränsaren (`;`, `,` eller tabb) gissas ur filen om den inte anges. Med kommatecken som
    avgränsare måste decimaltal vara skrivna med punkt.
    """
    sokvag = Path(fil)
    try:
        text = sokvag.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise BullerFel(f"Kunde inte läsa {sokvag} (ska vara CSV i UTF-8): {exc}") from exc
    if not text.strip():
        raise BullerFel(f"{sokvag} är tom.")
    if avgransare is None:
        avgransare = max((";", "\t", ","), key=text.count)
    return [[c.strip() for c in rad] for rad in csv.reader(text.splitlines(), delimiter=avgransare)]


def _hitta(celler: Celler, etiketter: Sequence[str]) -> tuple[int, int] | None:
    for r, rad in enumerate(celler):
        for k, cell in enumerate(rad):
            if _norm(cell) in etiketter:
                return r, k
    return None


def _varde_hoger(celler: Celler, etiketter: Sequence[str]) -> float | None:
    plats = _hitta(celler, etiketter)
    if plats is None:
        return None
    r, k = plats
    for cell in celler[r][k + 1 :]:
        tal = _som_tal(cell)
        if tal is not None:
            return tal
        if cell and _norm(cell) not in ("=", ":"):
            break
    raise BullerFel(f"Etiketten '{celler[r][k]}' (rad {r + 1}) följs inte av något tal.")


def _kraver(celler: Celler, etikett: str, blad: str) -> float:
    varde = _varde_hoger(celler, (etikett,))
    if varde is None:
        raise BullerFel(f"{blad}: etiketten '{etikett}' hittades inte i bladet.")
    return varde


def _las_terrang(celler: Celler, blad: str) -> Terrangdata:
    for r, rad in enumerate(celler):
        normad = [_norm(c) for c in rad]
        if "x" not in normad:
            continue
        k = normad.index("x")
        if tuple(normad[k : k + 4]) != _TERRANGRUBRIKER:
            continue
        punkter: list[tuple[float, float, float, float]] = []
        for under in celler[r + 1 :]:
            tal = [_som_tal(c) for c in under[k : k + 4]] if len(under) >= k + 4 else []
            if len(tal) != 4 or any(t is None for t in tal):
                if punkter:
                    break
                continue  # enhetsraden mellan rubrik och tal
            x, z, sigma, rahet = (float(t) for t in tal if t is not None)
            if punkter and x <= punkter[-1][0]:
                break  # utfyllnadsrader med nollor efter sista punkten
            punkter.append((x, z, sigma, rahet))
        if len(punkter) < 2:
            raise BullerFel(f"{blad}: terrängtabellen har färre än två punkter.")
        # Varje rad anger egenskaperna för segmentet som börjar i punkten; sista radens
        # egenskaper hör inte till något segment.
        return Terrangdata(
            x_m=tuple(p[0] for p in punkter),
            z_m=tuple(p[1] for p in punkter),
            flodesresistivitet_kNsm4=tuple(p[2] for p in punkter[:-1]),
            rahet_m=tuple(p[3] for p in punkter[:-1]),
        )
    raise BullerFel(f"{blad}: hittade inte terrängtabellens rubrikrad (X, Z, Flow res., Roughn.).")


def _las_spektrum(celler: Celler, blad: str) -> dict[str, list[float]]:
    plats = _hitta(celler, ("freq.", "freq", "frequency"))
    if plats is None:
        raise BullerFel(f"{blad}: hittade inte spektrumtabellens rubrik 'Freq.'.")
    r, k = plats
    kolumner: dict[int, str] = {}
    for kol in range(k + 1, len(celler[r])):
        etikett = _norm(celler[r][kol])
        if etikett in _SPEKTRUMETIKETTER:
            kolumner[kol] = _SPEKTRUMETIKETTER[etikett]
        elif etikett:
            raise BullerFel(
                f"{blad}: okänd spektrumkolumn '{celler[r][kol]}'. Kända: "
                f"{', '.join(_SPEKTRUMETIKETTER)}."
            )
    if not kolumner:
        raise BullerFel(f"{blad}: spektrumtabellen saknar kolumner.")
    varden: dict[str, list[float]] = {namn: [] for namn in kolumner.values()}
    for rad in celler[r + 1 :]:
        frekvens = _som_tal(rad[k]) if len(rad) > k else None
        band = len(next(iter(varden.values())))
        if frekvens is None:
            if band:
                break
            continue  # enhetsraden
        if band >= ANTAL_BAND or frekvens != TERSBAND_HZ[band]:
            raise BullerFel(
                f"{blad}: väntade frekvensen {TERSBAND_HZ[min(band, ANTAL_BAND - 1)]:g} Hz i "
                f"spektrumtabellen, fick {frekvens:g}."
            )
        for kol, namn in kolumner.items():
            tal = _som_tal(rad[kol]) if len(rad) > kol else None
            if tal is None:
                raise BullerFel(f"{blad}: värde saknas för {namn} vid {frekvens:g} Hz.")
            varden[namn].append(tal)
    if any(len(v) != ANTAL_BAND for v in varden.values()):
        raise BullerFel(f"{blad}: spektrumtabellen har inte {ANTAL_BAND} band.")
    return varden


def importera_rak_vag_blad(
    celler: Celler,
    *,
    fall_id: str,
    blad: str,
    dokument: str,
    metodversion: str,
    ar_facit: bool,
    ar_facit_skal: str,
    fordonskategori: int,
    kalla_x_m: float | None = None,
    antal_kallpunkter: int = 179,
    vinkelupplosning_grader: float = 1.0,
    relativ_fuktighet_procent: float = 70.0,
) -> tuple[N2kFall, Facit]:
    """Tolkar ett blad för rak väg (uppställning som bilaga A i Miljøprojekt 1335/2010).

    Det som inte står i bladet anges av anroparen: fordonskategorin (kategori 1 utom fall 17
    och 18 enligt rapportens kapitel 2), antal källpunkter och vinkelupplösning (179 och 1°
    enligt kapitel 2) och relativ fuktighet (70 % enligt kapitel 2). Källinjen läggs i
    terrängens första punkt om `kalla_x_m` inte anges, och mottagaren i den sista.
    """
    titel = [c for rad in celler for c in rad if c][:2]
    if len(titel) < 2:
        raise BullerFel(f"{blad}: bladet saknar titelrader.")
    terrang = _las_terrang(celler, blad)
    period_h = _kraver(celler, "t", blad)
    vader_varden = {falt: _kraver(celler, etikett, blad) for etikett, falt in
                    _VADERETIKETTER.items()}
    vader = Vader(relativ_fuktighet_procent=relativ_fuktighet_procent, **vader_varden)
    vindriktning = _varde_hoger(celler, _VINDRIKTNING)

    totaler = {
        namn: varde
        for etikett, namn in _TOTALETIKETTER.items()
        if (varde := _varde_hoger(celler, (etikett,))) is not None
    }
    spektrum = _las_spektrum(celler, blad)

    fall = N2kFall(
        id=fall_id,
        namn=titel[0],
        typ=TYP_RAK_VAG,
        beskrivning=titel[1],
        status="redo" if ar_facit else "ej_facit",
        kalla={"dokument": dokument, "blad": blad},
        indata_fullstandig=True,
        vag=Vagdata(
            kalla_x_m=terrang.x_m[0] if kalla_x_m is None else kalla_x_m,
            antal_kallpunkter=antal_kallpunkter,
            vinkelupplosning_grader=vinkelupplosning_grader,
        ),
        mottagare_x_m=terrang.x_m[-1],
        mottagare_hojd_m=_kraver(celler, "hr", blad),
        terrang=terrang,
        trafik=(
            Trafikpost(
                kategori=fordonskategori,
                antal_fordon=_kraver(celler, "nvc", blad),
                hastighet_km_h=_kraver(celler, "v", blad),
                period="T",
            ),
        ),
        perioder_h={"T": period_h},
        vader=vader,
        vader_tillagg={} if vindriktning is None else {"vindriktning_grader": vindriktning},
    )
    facit = Facit(
        ar_facit=ar_facit,
        ar_facit_skal=ar_facit_skal,
        metodversion=metodversion,
        kalla={"dokument": dokument, "blad": blad},
        serier=(
            Facitserie(
                id="blad",
                villkor={},
                totalnivaer_db=totaler,
                spektrum_db={k: np.asarray(v, dtype=np.float64) for k, v in spektrum.items()},
            ),
        ),
        avlasning={"metod": "maskinell import av CSV-exporterat blad"},
    )
    return fall, facit
