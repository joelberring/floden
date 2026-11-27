"""Rust-brygga för SankGrafs tunga rastersteg (`rust/sankgraf_karna`).

Licens: GPL-3.0-or-later (se `LICENSE` i repots rot).

Binärsökning (i prioritetsordning):
1. Explicit argument `binar`
2. Miljövariabeln `FLODEN_SANKGRAF_KARNA`
3. `/usr/local/bin/sankgraf_karna`
4. `<repo>/rust/sankgraf_karna/target/release/sankgraf_karna`
5. `sankgraf_karna` på `PATH`

Styrning via miljövariabler:
- `FLODEN_SANKGRAF_RUST`:
  - `auto` (standard): använd Rust när binären finns och `n_celler >= tröskel`;
    fall tillbaka till Python om binären saknas.
  - `1` / `pa` / `på` / `alltid`: kräv Rust oavsett rutnätsstorlek; kasta tydligt
    `SankGrafRustFel` (`RuntimeError`) om binären saknas.
  - `0` / `av` / `python`: tvinga ren Python-väg.
- `FLODEN_SANKGRAF_RUST_MIN_CELLER`: celltröskel i `auto`-läge (standard `100_000`).

Kommunikationen sker via kompakta binära `.flsg`-tempfiler med magisk header,
version, dimensioner, exakt längdkontroll och IEEE CRC-32 kontrollsumma.
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TypeVar

import numpy as np
from numpy.typing import NDArray

from sankgraf.typer import SankGrafFel

RUST_MIN_CELLER_STANDARD = 100_000
_INT32_MAX = (1 << 31) - 1

_MAGIC_IN = b"FLSG_IN\x00"
_MAGIC_UT = b"FLSG_UT\x00"
_FORMAT_VERSION = 1
_HEADER_IN_STRUCT = struct.Struct("<8sIIIIIIQQ")
_HEADER_UT_STRUCT = struct.Struct("<8sIIIIIIQQQQ")

_LAGE_KOMPLETT = 0
_LAGE_AVRINNING = 1
_LAGE_GRANSPAR = 2
_LAGE_AVRINNINGSNAT = 3
_LAGE_AVRINNINGSNAT_TILLRINNING = 4

_SYSTEM_BINAR = Path("/usr/local/bin/sankgraf_karna")
_REPO_BINAR = (
    Path(__file__).resolve().parents[2]
    / "rust"
    / "sankgraf_karna"
    / "target"
    / "release"
    / "sankgraf_karna"
)

GransparArrayer = tuple[
    NDArray[np.float64],
    NDArray[np.int32],
    NDArray[np.int32],
    NDArray[np.int64],
    NDArray[np.int64],
    NDArray[np.int32],
    NDArray[np.int32],
    NDArray[np.int32],
    NDArray[np.int32],
]

_ScalarT = TypeVar("_ScalarT", bound=np.generic)


class SankGrafRustFel(SankGrafFel, RuntimeError):
    """Fel när `sankgraf_karna` saknas, misslyckas eller returnerar korrupt utdata."""


def hitta_sankgraf_karna(binar: str | Path | None = None) -> Path:
    """Returnerar körbar sökväg till `sankgraf_karna`, eller kastar `SankGrafRustFel`."""
    kandidater: list[str | Path] = []
    if binar is not None:
        kandidater.append(binar)
    miljo = os.environ.get("FLODEN_SANKGRAF_KARNA")
    if miljo:
        kandidater.append(miljo)
    kandidater.append(_SYSTEM_BINAR)
    kandidater.append(_REPO_BINAR)

    for kandidat in kandidater:
        p = Path(kandidat)
        if p.is_file() and os.access(p, os.X_OK):
            return p

    pa_path = shutil.which("sankgraf_karna")
    if pa_path:
        return Path(pa_path)

    raise SankGrafRustFel(
        "sankgraf_karna saknas. Bygg med "
        "`cargo build --release --locked --manifest-path rust/sankgraf_karna/Cargo.toml` "
        "eller ange FLODEN_SANKGRAF_KARNA."
    )


def rust_min_celler() -> int:
    """Celltröskel för automatisk Rust-dispatch (`FLODEN_SANKGRAF_RUST_MIN_CELLER`)."""
    raw = os.environ.get("FLODEN_SANKGRAF_RUST_MIN_CELLER", "").strip()
    if not raw:
        return RUST_MIN_CELLER_STANDARD
    try:
        varde = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Ogiltigt heltal för FLODEN_SANKGRAF_RUST_MIN_CELLER: {raw!r}."
        ) from exc
    if varde < 0:
        raise ValueError(
            f"FLODEN_SANKGRAF_RUST_MIN_CELLER måste vara >= 0, fick {varde}."
        )
    return varde


def rust_lage() -> Literal["auto", "alltid", "av"]:
    """Läser körläge från `FLODEN_SANKGRAF_RUST` (`auto`, `1`/`pa`/`alltid`, `0`/`av`/`python`)."""
    raw = os.environ.get("FLODEN_SANKGRAF_RUST", "").strip().lower()
    if raw in ("", "auto"):
        return "auto"
    if raw in ("1", "pa", "på", "alltid", "true", "ja"):
        return "alltid"
    if raw in ("0", "av", "python", "false", "nej"):
        return "av"
    raise ValueError(
        f"Ogiltigt värde för FLODEN_SANKGRAF_RUST: {raw!r}. "
        "Tillåtna värden: auto, 1/pa/alltid, 0/av/python."
    )


def valj_sankgraf_karna(
    n_celler: int,
    *,
    anvand_rust: bool | None = None,
    binar: str | Path | None = None,
) -> Path | None:
    """Avgör om `sankgraf_karna` ska användas och returnerar binärens sökväg eller `None`.

    När Rust är tvingat (`anvand_rust=True` eller `FLODEN_SANKGRAF_RUST=1|pa|alltid`)
    kastas `SankGrafRustFel` om binären saknas eller rutnätet överskrider `i32::MAX`.
    """
    if anvand_rust is False:
        lage: Literal["auto", "alltid", "av"] = "av"
    elif anvand_rust is True:
        lage = "alltid"
    else:
        lage = rust_lage()

    if lage == "av":
        return None

    if n_celler >= _INT32_MAX:
        if lage == "alltid":
            raise SankGrafRustFel(
                f"sankgraf_karna stöder högst {_INT32_MAX - 1} celler, fick {n_celler}."
            )
        return None

    if lage == "alltid":
        return hitta_sankgraf_karna(binar)

    if n_celler < rust_min_celler():
        return None

    try:
        return hitta_sankgraf_karna(binar)
    except SankGrafRustFel:
        return None


def _tom_granspar() -> GransparArrayer:
    return (
        np.empty(0, dtype=np.float64),
        np.empty(0, dtype=np.int32),
        np.empty(0, dtype=np.int32),
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.int32),
        np.empty(0, dtype=np.int32),
        np.empty(0, dtype=np.int32),
        np.empty(0, dtype=np.int32),
    )


def _skriv_indata(
    in_fil: Path,
    lage: int,
    z_arr: NDArray[np.float32] | NDArray[np.float64],
    aktiv_arr: NDArray[np.bool_],
    catch_arr: NDArray[np.int32] | NDArray[np.int64] | None,
    kantbarriarer: Mapping[tuple[int, int], float],
    *,
    egen_arr: NDArray[np.float64] | None = None,
) -> None:
    if z_arr.ndim != 2 or aktiv_arr.shape != z_arr.shape:
        raise SankGrafRustFel(
            f"Oförenliga former för indata: z={z_arr.shape}, aktiv={aktiv_arr.shape}."
        )
    hojd, bredd = z_arr.shape
    n_celler = hojd * bredd
    if n_celler >= _INT32_MAX:
        raise SankGrafRustFel(
            f"sankgraf_karna stöder högst {_INT32_MAX - 1} celler, fick {n_celler}."
        )

    if z_arr.dtype == np.float32:
        z_typ = 0
        z_c = np.ascontiguousarray(z_arr, dtype=np.dtype("<f4"))
    else:
        z_typ = 1
        z_c = np.ascontiguousarray(z_arr, dtype=np.dtype("<f8"))
    aktiv_c = np.ascontiguousarray(aktiv_arr, dtype=np.uint8)

    delar: list[memoryview] = [
        memoryview(z_c).cast("B"),
        memoryview(aktiv_c).cast("B"),
    ]

    if lage == _LAGE_GRANSPAR:
        if catch_arr is None or catch_arr.shape != z_arr.shape:
            raise SankGrafRustFel("catch_2d saknas eller har fel form för GRANSPAR.")
        catch_c = np.ascontiguousarray(catch_arr, dtype=np.dtype("<i4"))
        delar.append(memoryview(catch_c).cast("B"))
    elif lage == _LAGE_AVRINNINGSNAT_TILLRINNING:
        if egen_arr is None or egen_arr.size != n_celler:
            raise SankGrafRustFel(
                "egen_yta saknas eller har fel längd för AVRINNINGSNAT_TILLRINNING."
            )
        egen_c = np.ascontiguousarray(egen_arr.ravel(), dtype=np.dtype("<f8"))
        delar.append(memoryview(egen_c).cast("B"))

    antal_barriarer = len(kantbarriarer)
    if antal_barriarer > 0:
        b_u = np.empty(antal_barriarer, dtype=np.dtype("<i4"))
        b_v = np.empty(antal_barriarer, dtype=np.dtype("<i4"))
        b_w = np.empty(antal_barriarer, dtype=np.dtype("<f8"))
        for idx, ((u, v), w) in enumerate(kantbarriarer.items()):
            if u < v:
                b_u[idx] = u
                b_v[idx] = v
            else:
                b_u[idx] = v
                b_v[idx] = u
            b_w[idx] = float(w)
        delar.append(memoryview(b_u).cast("B"))
        delar.append(memoryview(b_v).cast("B"))
        delar.append(memoryview(b_w).cast("B"))

    crc = 0
    payload_langd = 0
    for vy in delar:
        if vy.nbytes > 0:
            crc = zlib.crc32(vy, crc) & 0xFFFFFFFF
            payload_langd += vy.nbytes

    header = _HEADER_IN_STRUCT.pack(
        _MAGIC_IN,
        _FORMAT_VERSION,
        lage,
        z_typ,
        hojd,
        bredd,
        crc,
        antal_barriarer,
        payload_langd,
    )
    with in_fil.open("wb") as fil:
        fil.write(header)
        for vy in delar:
            if vy.nbytes > 0:
                fil.write(vy)


def _las_array(
    fil: io.BufferedReader,
    antal: int,
    le_dtype: np.dtype[_ScalarT],
    crc: int,
    namn: str,
) -> tuple[NDArray[_ScalarT], int]:
    arr: NDArray[_ScalarT] = np.empty(antal, dtype=le_dtype)
    if antal == 0:
        return arr, crc
    vy = memoryview(arr).cast("B")
    last = fil.readinto(vy)
    if last != vy.nbytes:
        raise SankGrafRustFel(
            f"Trunkerad utdatafil vid läsning av {namn}: läste {last} av {vy.nbytes} byte."
        )
    ny_crc = zlib.crc32(vy, crc) & 0xFFFFFFFF
    return arr, ny_crc


def _las_utdata(
    ut_fil: Path,
    forvantat_lage: int,
    hojd: int,
    bredd: int,
) -> tuple[NDArray[np.int32] | None, NDArray[np.int32] | None, int, GransparArrayer | None]:
    if not ut_fil.is_file():
        raise SankGrafRustFel(f"sankgraf_karna skapade ingen utdatafil ({ut_fil}).")
    fil_langd = ut_fil.stat().st_size
    if fil_langd < _HEADER_UT_STRUCT.size:
        raise SankGrafRustFel(
            f"Trunkerad utdatafil: {fil_langd} byte < {_HEADER_UT_STRUCT.size} byte header."
        )

    with ut_fil.open("rb") as fil:
        hdr_bytes = fil.read(_HEADER_UT_STRUCT.size)
        if len(hdr_bytes) != _HEADER_UT_STRUCT.size:
            raise SankGrafRustFel("Kunde inte läsa komplett utdata-header från sankgraf_karna.")
        (
            magic,
            version,
            lage,
            h_ut,
            b_ut,
            antal_lov,
            header_crc,
            antal_par,
            antal_kanter,
            header_payload_langd,
            _reserverad,
        ) = _HEADER_UT_STRUCT.unpack(hdr_bytes)

        if magic != _MAGIC_UT:
            raise SankGrafRustFel("Ogiltig magisk signatur i utdatafil från sankgraf_karna.")
        if version != _FORMAT_VERSION:
            raise SankGrafRustFel(
                f"Okänd formatversion {version} i utdatafil (förväntade {_FORMAT_VERSION})."
            )
        if lage != forvantat_lage:
            raise SankGrafRustFel(
                f"Fel körläge i utdatafil: fick {lage}, förväntade {forvantat_lage}."
            )
        if h_ut != hojd or b_ut != bredd:
            raise SankGrafRustFel(
                f"Dimensioner i utdatafil ({h_ut}x{b_ut}) matchar inte indata ({hojd}x{bredd})."
            )

        n_celler = hojd * bredd
        har_avr = lage in (_LAGE_KOMPLETT, _LAGE_AVRINNING)
        har_gp = lage in (_LAGE_KOMPLETT, _LAGE_GRANSPAR)
        forvantad_payload = (
            (2 * n_celler * 4 if har_avr else 0)
            + (antal_par * 32 + antal_kanter * 16 if har_gp else 0)
        )
        if header_payload_langd != forvantad_payload:
            raise SankGrafRustFel(
                f"Utdata-headerns payload-längd ({header_payload_langd}) matchar inte "
                f"förväntad storlek ({forvantad_payload})."
            )
        faktisk_payload = fil_langd - _HEADER_UT_STRUCT.size
        if faktisk_payload != forvantad_payload:
            raise SankGrafRustFel(
                f"Korrupt eller trunkerad utdatafil: fick {faktisk_payload} payload-byte, "
                f"förväntade {forvantad_payload}."
            )

        crc = 0
        flow_to_2d: NDArray[np.int32] | None = None
        ctn_2d: NDArray[np.int32] | None = None
        if har_avr:
            flow_flat, crc = _las_array(fil, n_celler, np.dtype("<i4"), crc, "flow_to")
            ctn_flat, crc = _las_array(fil, n_celler, np.dtype("<i4"), crc, "cell_till_nod")
            flow_to_2d = np.asarray(flow_flat, dtype=np.int32).reshape((hojd, bredd))
            ctn_2d = np.asarray(ctn_flat, dtype=np.int32).reshape((hojd, bredd))

        granspar: GransparArrayer | None = None
        if har_gp:
            if antal_par == 0 and antal_kanter == 0:
                granspar = _tom_granspar()
            else:
                par_w, crc = _las_array(fil, antal_par, np.dtype("<f8"), crc, "par_w")
                par_c_lo, crc = _las_array(fil, antal_par, np.dtype("<i4"), crc, "par_c_lo")
                par_c_hi, crc = _las_array(fil, antal_par, np.dtype("<i4"), crc, "par_c_hi")
                par_start, crc = _las_array(fil, antal_par, np.dtype("<i8"), crc, "par_start")
                par_slut, crc = _las_array(fil, antal_par, np.dtype("<i8"), crc, "par_slut")
                f_u, crc = _las_array(fil, antal_kanter, np.dtype("<i4"), crc, "f_u")
                f_v, crc = _las_array(fil, antal_kanter, np.dtype("<i4"), crc, "f_v")
                f_cu, crc = _las_array(fil, antal_kanter, np.dtype("<i4"), crc, "f_cu")
                f_cv, crc = _las_array(fil, antal_kanter, np.dtype("<i4"), crc, "f_cv")
                granspar = (
                    np.asarray(par_w, dtype=np.float64),
                    np.asarray(par_c_lo, dtype=np.int32),
                    np.asarray(par_c_hi, dtype=np.int32),
                    np.asarray(par_start, dtype=np.int64),
                    np.asarray(par_slut, dtype=np.int64),
                    np.asarray(f_u, dtype=np.int32),
                    np.asarray(f_v, dtype=np.int32),
                    np.asarray(f_cu, dtype=np.int32),
                    np.asarray(f_cv, dtype=np.int32),
                )

    if crc != header_crc:
        raise SankGrafRustFel(
            f"CRC-32-fel i utdatafil från sankgraf_karna: "
            f"beräknade {crc:#010x}, header angav {header_crc:#010x}."
        )

    return flow_to_2d, ctn_2d, int(antal_lov), granspar


def _kor_binar(
    lage: int,
    z_arr: NDArray[np.float32] | NDArray[np.float64],
    aktiv_arr: NDArray[np.bool_],
    catch_arr: NDArray[np.int32] | NDArray[np.int64] | None,
    kantbarriarer: Mapping[tuple[int, int], float],
    *,
    binar: str | Path | None = None,
    timeout_s: float | None = None,
) -> tuple[NDArray[np.int32] | None, NDArray[np.int32] | None, int, GransparArrayer | None]:
    program = hitta_sankgraf_karna(binar)
    hojd, bredd = z_arr.shape
    tmp_dir = Path(tempfile.mkdtemp(prefix="floden-sankgraf-"))
    try:
        in_fil = tmp_dir / "in.flsg"
        ut_fil = tmp_dir / "ut.flsg"
        _skriv_indata(in_fil, lage, z_arr, aktiv_arr, catch_arr, kantbarriarer)
        try:
            res = subprocess.run(
                [str(program), str(in_fil), str(ut_fil)],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise SankGrafRustFel(
                f"sankgraf_karna överskred tidsgränsen ({timeout_s} s)."
            ) from exc
        except OSError as exc:
            raise SankGrafRustFel(f"Kunde inte starta sankgraf_karna ({program}): {exc}") from exc

        if res.returncode != 0:
            detalj = res.stderr.strip() or res.stdout.strip() or f"returkod {res.returncode}"
            raise SankGrafRustFel(
                f"sankgraf_karna misslyckades (kod {res.returncode}): {detalj}"
            )

        return _las_utdata(ut_fil, lage, hojd, bredd)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def kor_sankgraf_karna_rust(
    eff_z: NDArray[np.float32] | NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    kantbarriarer: Mapping[tuple[int, int], float],
    *,
    binar: str | Path | None = None,
    timeout_s: float | None = None,
) -> tuple[NDArray[np.int32], NDArray[np.int32], int, GransparArrayer]:
    """Kör både avrinningsområden och gränspar i ett enda Rust-anrop."""
    flow_to, ctn, antal_lov, granspar = _kor_binar(
        _LAGE_KOMPLETT,
        eff_z,
        aktiv,
        None,
        kantbarriarer,
        binar=binar,
        timeout_s=timeout_s,
    )
    if flow_to is None or ctn is None or granspar is None:
        raise SankGrafRustFel("Ofullständigt resultat från sankgraf_karna i KOMPLETT-läge.")
    return flow_to, ctn, antal_lov, granspar


def kor_avrinningsomraden_rust(
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    kantbarriarer: Mapping[tuple[int, int], float],
    *,
    binar: str | Path | None = None,
    timeout_s: float | None = None,
) -> tuple[NDArray[np.int32], NDArray[np.int32], int]:
    """Kör enbart avrinningsområdessteget (`_berakna_avrinningsomraden`) i Rust."""
    flow_to, ctn, antal_lov, _ = _kor_binar(
        _LAGE_AVRINNING,
        mark_z,
        aktiv,
        None,
        kantbarriarer,
        binar=binar,
        timeout_s=timeout_s,
    )
    if flow_to is None or ctn is None:
        raise SankGrafRustFel("Ofullständigt resultat från sankgraf_karna i AVRINNING-läge.")
    return flow_to, ctn, antal_lov


def kor_granspar_rust(
    catch_2d: NDArray[np.int32] | NDArray[np.int64],
    aktiv: NDArray[np.bool_],
    eff_z: NDArray[np.float32] | NDArray[np.float64],
    kantbarriarer: Mapping[tuple[int, int], float],
    *,
    binar: str | Path | None = None,
    timeout_s: float | None = None,
) -> GransparArrayer:
    """Kör enbart gränsparssteget (`_bygg_granspar`) i Rust."""
    _, _, _, granspar = _kor_binar(
        _LAGE_GRANSPAR,
        eff_z,
        aktiv,
        catch_2d,
        kantbarriarer,
        binar=binar,
        timeout_s=timeout_s,
    )
    if granspar is None:
        raise SankGrafRustFel("Ofullständigt resultat från sankgraf_karna i GRANSPAR-läge.")
    return granspar


def _las_avrinningsnat_utdata(
    ut_fil: Path,
    forvantat_lage: int,
    hojd: int,
    bredd: int,
) -> tuple[NDArray[np.int32], NDArray[np.int32], NDArray[np.float64] | None]:
    if not ut_fil.is_file():
        raise SankGrafRustFel(f"sankgraf_karna skapade ingen utdatafil ({ut_fil}).")
    fil_langd = ut_fil.stat().st_size
    if fil_langd < _HEADER_UT_STRUCT.size:
        raise SankGrafRustFel(
            f"Trunkerad utdatafil: {fil_langd} byte < {_HEADER_UT_STRUCT.size} byte header."
        )

    with ut_fil.open("rb") as fil:
        hdr_bytes = fil.read(_HEADER_UT_STRUCT.size)
        if len(hdr_bytes) != _HEADER_UT_STRUCT.size:
            raise SankGrafRustFel("Kunde inte läsa komplett utdata-header från sankgraf_karna.")
        (
            magic,
            version,
            lage,
            h_ut,
            b_ut,
            _antal_lov,
            header_crc,
            ordning_len,
            _antal_kanter,
            header_payload_langd,
            _reserverad,
        ) = _HEADER_UT_STRUCT.unpack(hdr_bytes)

        if magic != _MAGIC_UT:
            raise SankGrafRustFel("Ogiltig magisk signatur i utdatafil från sankgraf_karna.")
        if version != _FORMAT_VERSION:
            raise SankGrafRustFel(
                f"Okänd formatversion {version} i utdatafil (förväntade {_FORMAT_VERSION})."
            )
        if lage != forvantat_lage:
            raise SankGrafRustFel(
                f"Fel körläge i utdatafil: fick {lage}, förväntade {forvantat_lage}."
            )
        if h_ut != hojd or b_ut != bredd:
            raise SankGrafRustFel(
                f"Dimensioner i utdatafil ({h_ut}x{b_ut}) matchar inte indata ({hojd}x{bredd})."
            )

        n_celler = hojd * bredd
        if ordning_len > n_celler:
            raise SankGrafRustFel(
                f"Ogiltig ordningslängd ({ordning_len}) > antal celler ({n_celler})."
            )
        har_tillr = lage == _LAGE_AVRINNINGSNAT_TILLRINNING
        forvantad_payload = (n_celler + int(ordning_len)) * 4 + (n_celler * 8 if har_tillr else 0)
        if header_payload_langd != forvantad_payload:
            raise SankGrafRustFel(
                f"Utdata-headerns payload-längd ({header_payload_langd}) matchar inte "
                f"förväntad storlek ({forvantad_payload})."
            )
        faktisk_payload = fil_langd - _HEADER_UT_STRUCT.size
        if faktisk_payload != forvantad_payload:
            raise SankGrafRustFel(
                f"Korrupt eller trunkerad utdatafil: fick {faktisk_payload} payload-byte, "
                f"förväntade {forvantad_payload}."
            )

        crc = 0
        flow_flat, crc = _las_array(fil, n_celler, np.dtype("<i4"), crc, "flow_to")
        ordn_flat, crc = _las_array(fil, int(ordning_len), np.dtype("<i4"), crc, "ordning")
        tillr_arr: NDArray[np.float64] | None = None
        if har_tillr:
            tillr_flat, crc = _las_array(fil, n_celler, np.dtype("<f8"), crc, "tillrinning")
            tillr_arr = np.asarray(tillr_flat, dtype=np.float64)

    if crc != header_crc:
        raise SankGrafRustFel(
            f"CRC-32-fel i utdatafil från sankgraf_karna: "
            f"beräknade {crc:#010x}, header angav {header_crc:#010x}."
        )

    return np.asarray(flow_flat, dtype=np.int32), np.asarray(ordn_flat, dtype=np.int32), tillr_arr


def _kanonisera_barriarer(
    kantbarriarer: Mapping[tuple[int, int], float] | None,
) -> dict[tuple[int, int], float]:
    """Kanoniserar och deduplicerar barriärkanter till `(min(u, v), max(u, v)) -> max(w)`."""
    if not kantbarriarer:
        return {}
    ut: dict[tuple[int, int], float] = {}
    for (u, v), w in kantbarriarer.items():
        nyckel = (int(u), int(v)) if u < v else (int(v), int(u))
        wf = float(w)
        tidigare = ut.get(nyckel)
        if tidigare is None or wf > tidigare:
            ut[nyckel] = wf
    return ut


def _kor_avrinningsnat_binar(
    lage: int,
    z_arr: NDArray[np.float32] | NDArray[np.float64],
    aktiv_arr: NDArray[np.bool_],
    *,
    egen_arr: NDArray[np.float64] | None = None,
    kantbarriarer: Mapping[tuple[int, int], float] | None = None,
    binar: str | Path | None = None,
    timeout_s: float | None = None,
) -> tuple[NDArray[np.int32], NDArray[np.int32], NDArray[np.float64] | None]:
    program = hitta_sankgraf_karna(binar)
    hojd, bredd = z_arr.shape
    barriarer_kanon = _kanonisera_barriarer(kantbarriarer)
    tmp_dir = Path(tempfile.mkdtemp(prefix="floden-avrinningsnat-"))
    try:
        in_fil = tmp_dir / "in.flsg"
        ut_fil = tmp_dir / "ut.flsg"
        _skriv_indata(in_fil, lage, z_arr, aktiv_arr, None, barriarer_kanon, egen_arr=egen_arr)
        try:
            res = subprocess.run(
                [str(program), str(in_fil), str(ut_fil)],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise SankGrafRustFel(
                f"sankgraf_karna överskred tidsgränsen ({timeout_s} s)."
            ) from exc
        except OSError as exc:
            raise SankGrafRustFel(f"Kunde inte starta sankgraf_karna ({program}): {exc}") from exc

        if res.returncode != 0:
            detalj = res.stderr.strip() or res.stdout.strip() or f"returkod {res.returncode}"
            raise SankGrafRustFel(
                f"sankgraf_karna misslyckades (kod {res.returncode}): {detalj}"
            )

        return _las_avrinningsnat_utdata(ut_fil, lage, hojd, bredd)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def kor_avrinningsnat_rust(
    z_arr: NDArray[np.float32] | NDArray[np.float64],
    aktiv_arr: NDArray[np.bool_],
    *,
    kantbarriarer: Mapping[tuple[int, int], float] | None = None,
    binar: str | Path | None = None,
    timeout_s: float | None = None,
) -> tuple[NDArray[np.int32], NDArray[np.int32]]:
    """Kör 8-granne prioritetsfyllning + D8 (`fyll_och_riktning`) i Rust."""
    flow, ordn, _ = _kor_avrinningsnat_binar(
        _LAGE_AVRINNINGSNAT,
        z_arr,
        aktiv_arr,
        kantbarriarer=kantbarriarer,
        binar=binar,
        timeout_s=timeout_s,
    )
    return flow, ordn


def kor_avrinningsnat_tillrinning_rust(
    z_arr: NDArray[np.float32] | NDArray[np.float64],
    aktiv_arr: NDArray[np.bool_],
    egen_arr: NDArray[np.float64],
    *,
    kantbarriarer: Mapping[tuple[int, int], float] | None = None,
    binar: str | Path | None = None,
    timeout_s: float | None = None,
) -> tuple[NDArray[np.int32], NDArray[np.int32], NDArray[np.float64]]:
    """Kör 8-granne prioritetsfyllning + D8 + nedströms tillrinning i ett enda Rust-anrop."""
    flow, ordn, tillr = _kor_avrinningsnat_binar(
        _LAGE_AVRINNINGSNAT_TILLRINNING,
        z_arr,
        aktiv_arr,
        egen_arr=egen_arr,
        kantbarriarer=kantbarriarer,
        binar=binar,
        timeout_s=timeout_s,
    )
    if tillr is None:
        raise SankGrafRustFel(
            "Ofullständigt resultat från sankgraf_karna i AVRINNINGSNAT_TILLRINNING-läge."
        )
    return flow, ordn, tillr
