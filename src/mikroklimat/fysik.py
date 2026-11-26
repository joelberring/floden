"""Högnivå-API för termiskt mikroklimat: värmestressanalys, före/efter och JSON-export.

Kopplar samman 6-riktnings medelstrålningstemperatur (`mikroklimat.tmrt`) och UTCI
(`mikroklimat.utci`) till sammanhållna resultatobjekt, planjämförelse (`jamfor_varmestress`)
och deterministisk JSON-serialisering med SHA-256-kontrollsummor ur `gemensamt.hash`.

Licens: GPL v3. Metodstatus: `skiss` (se `docs/MIKROKLIMAT.md`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_bytes
from mikroklimat.tmrt import (
    ALBEDO_MARK_STANDARD,
    ALBEDO_VAGG_STANDARD,
    ALPHA_K_STANDARD,
    EMISSIVITET_MARK_STANDARD,
    EMISSIVITET_TRAD_STANDARD,
    EMISSIVITET_VAGG_STANDARD,
    EPS_P_STANDARD,
    T_MARK_SOLTILLAGG_STANDARD_C,
    T_VAGG_SOLTILLAGG_STANDARD_C,
    TRAD_TRANSMISSIVITET_STANDARD,
    VAGG_SOLANDEL_STANDARD,
    TmrtResultat,
    berakna_tmrt,
)
from mikroklimat.utci import (
    UTCI_STRESSKLASSER,
    SkalarEllerArray,
    UtciResultat,
    berakna_utci,
    vind_fotgangarhojd_till_10m,
)

FloatRaster = NDArray[np.float64]

FORMAT_MIKROKLIMAT: str = "floden-mikroklimat/v1-skiss"
METODSTATUS_MIKROKLIMAT: str = "skiss"


def _kanonisk_raster_sha256(arr: FloatRaster) -> str:
    """Beräknar deterministisk SHA-256 över ett float64-raster (little-endian, kanonisk NaN/+0)."""
    a = np.ascontiguousarray(arr, dtype="<f8").copy()
    # Normalisera -0.0 -> +0.0 och godtyckliga NaN-mönster -> kanonisk NaN.
    a = np.where(a == 0.0, 0.0, a)
    a = np.where(np.isnan(a), np.nan, a)
    huvud = f"{a.ndim}:{','.join(str(d) for d in a.shape)};".encode("ascii")
    return sha256_bytes(huvud + a.tobytes())


def _statistik_fran_falt(
    t_mrt_c: FloatRaster,
    utci: UtciResultat,
) -> VarmestressStatistik:
    """Sammanställer ytstatistik över giltiga celler i ett värmestressresultat."""
    antal_totalt = int(utci.utci_c.size)
    nan_utci = np.isnan(utci.utci_c)
    antal_utanfor = int(np.sum(utci.utanfor_giltighetsomrade))
    giltiga_utci = np.isfinite(utci.utci_c)
    antal_giltiga = int(np.sum(giltiga_utci))
    antal_nan = int(np.sum(nan_utci))
    andel_utanfor = float(antal_utanfor / antal_totalt) if antal_totalt > 0 else 0.0

    giltiga_tmrt = np.isfinite(t_mrt_c)
    if np.any(giltiga_tmrt):
        tmrt_vals = t_mrt_c[giltiga_tmrt]
        t_mrt_medel: float | None = float(np.mean(tmrt_vals))
        t_mrt_min: float | None = float(np.min(tmrt_vals))
        t_mrt_max: float | None = float(np.max(tmrt_vals))
        t_mrt_p90: float | None = float(np.percentile(tmrt_vals, 90))
    else:
        t_mrt_medel = None
        t_mrt_min = None
        t_mrt_max = None
        t_mrt_p90 = None

    klass_andel: dict[int, float] = {k.klass_id: 0.0 for k in UTCI_STRESSKLASSER}

    if antal_giltiga > 0:
        u_vals = utci.utci_c[giltiga_utci]
        k_vals = utci.klass_id[giltiga_utci]
        utci_medel: float | None = float(np.mean(u_vals))
        utci_min: float | None = float(np.min(u_vals))
        utci_max: float | None = float(np.max(u_vals))
        utci_p90: float | None = float(np.percentile(u_vals, 90))

        for k in UTCI_STRESSKLASSER:
            klass_andel[k.klass_id] = float(np.sum(k_vals == k.klass_id) / antal_giltiga)

        andel_ingen = float(np.sum(k_vals == 0) / antal_giltiga)
        andel_mattlig = float(np.sum(k_vals == 1) / antal_giltiga)
        andel_stark = float(np.sum(k_vals >= 2) / antal_giltiga)
        andel_mycket_stark = float(np.sum(k_vals >= 3) / antal_giltiga)
        andel_extrem = float(np.sum(k_vals == 4) / antal_giltiga)
    else:
        utci_medel = None
        utci_min = None
        utci_max = None
        utci_p90 = None
        andel_ingen = 0.0
        andel_mattlig = 0.0
        andel_stark = 0.0
        andel_mycket_stark = 0.0
        andel_extrem = 0.0

    return VarmestressStatistik(
        antal_celler_totalt=antal_totalt,
        antal_giltiga_celler=antal_giltiga,
        antal_nan_celler=antal_nan,
        antal_utanfor_giltighetsomrade=antal_utanfor,
        andel_utanfor_giltighetsomrade=andel_utanfor,
        t_mrt_medel_c=t_mrt_medel,
        t_mrt_min_c=t_mrt_min,
        t_mrt_max_c=t_mrt_max,
        t_mrt_p90_c=t_mrt_p90,
        utci_medel_c=utci_medel,
        utci_min_c=utci_min,
        utci_max_c=utci_max,
        utci_p90_c=utci_p90,
        andel_ingen_stress=andel_ingen,
        andel_mattlig_varmestress=andel_mattlig,
        andel_stark_varmestress=andel_stark,
        andel_mycket_stark_varmestress=andel_mycket_stark,
        andel_extrem_varmestress=andel_extrem,
        klassfordelning_andel=klass_andel,
    )


@dataclass(frozen=True)
class VarmestressStatistik:
    """Sammanfattande statistik för ett värmestressraster (T_mrt och UTCI)."""

    antal_celler_totalt: int
    antal_giltiga_celler: int
    antal_nan_celler: int
    antal_utanfor_giltighetsomrade: int
    andel_utanfor_giltighetsomrade: float
    t_mrt_medel_c: float | None
    t_mrt_min_c: float | None
    t_mrt_max_c: float | None
    t_mrt_p90_c: float | None
    utci_medel_c: float | None
    utci_min_c: float | None
    utci_max_c: float | None
    utci_p90_c: float | None
    andel_ingen_stress: float
    andel_mattlig_varmestress: float
    andel_stark_varmestress: float  # UTCI > 32 °C (stark, mycket stark eller extrem)
    andel_mycket_stark_varmestress: float  # UTCI > 38 °C (mycket stark eller extrem)
    andel_extrem_varmestress: float  # UTCI > 46 °C
    klassfordelning_andel: dict[int, float]


@dataclass(frozen=True)
class VarmestressResultat:
    """Sammanhållet resultat för 6-riktnings T_mrt och UTCI på ett 2D-raster eller punkt."""

    tmrt: TmrtResultat
    utci: UtciResultat
    statistik: VarmestressStatistik
    vind_10m_m_s: FloatRaster
    varningar: tuple[str, ...]
    metodstatus: str = METODSTATUS_MIKROKLIMAT


@dataclass(frozen=True)
class VarmestressJamforelse:
    """Före/efter-jämförelse av termiskt mikroklimat (`efter - fore`, negativt = svalare)."""

    delta_t_mrt_c: FloatRaster
    delta_utci_c: FloatRaster
    antal_jamforda_tmrt_celler: int
    antal_jamforda_utci_celler: int
    delta_t_mrt_medel_c: float | None
    delta_t_mrt_min_c: float | None
    delta_t_mrt_max_c: float | None
    delta_utci_medel_c: float | None
    delta_utci_min_c: float | None
    delta_utci_max_c: float | None
    delta_andel_stark_varmestress: float
    delta_andel_mycket_stark_varmestress: float
    delta_andel_extrem_varmestress: float
    fore_statistik: VarmestressStatistik
    efter_statistik: VarmestressStatistik
    varningar: tuple[str, ...]
    metodstatus: str = METODSTATUS_MIKROKLIMAT


def berakna_varmestress(
    svf: SkalarEllerArray,
    skuggmask: SkalarEllerArray,
    solhojd_rad: SkalarEllerArray,
    sol_direkt_w_m2: SkalarEllerArray,
    sol_diffus_w_m2: SkalarEllerArray,
    t_luft_c: SkalarEllerArray,
    relativ_fuktighet_pct: SkalarEllerArray,
    vind_10m_m_s: SkalarEllerArray | None = None,
    *,
    vind_fotgangarhojd_m_s: SkalarEllerArray | None = None,
    vind_hojd_m: float = 1.5,
    skrovlighet_z0_m: float = 0.01,
    albedo_mark: SkalarEllerArray = ALBEDO_MARK_STANDARD,
    albedo_vagg: SkalarEllerArray = ALBEDO_VAGG_STANDARD,
    emissivitet_mark: SkalarEllerArray = EMISSIVITET_MARK_STANDARD,
    emissivitet_vagg: SkalarEllerArray = EMISSIVITET_VAGG_STANDARD,
    t_mark_soltillagg_c: SkalarEllerArray = T_MARK_SOLTILLAGG_STANDARD_C,
    t_vagg_soltillagg_c: SkalarEllerArray = T_VAGG_SOLTILLAGG_STANDARD_C,
    trad_mask: NDArray[np.bool_] | SkalarEllerArray | None = None,
    trad_transmissivitet: float = TRAD_TRANSMISSIVITET_STANDARD,
    emissivitet_himmel: SkalarEllerArray | None = None,
    emissivitet_trad: float = EMISSIVITET_TRAD_STANDARD,
    vagg_solandel: float = VAGG_SOLANDEL_STANDARD,
    alpha_k: float = ALPHA_K_STANDARD,
    eps_p: float = EPS_P_STANDARD,
    tillat_utci_extrapolering: bool = False,
) -> VarmestressResultat:
    """Beräknar både 6-riktnings T_mrt och UTCI samt ytstatistik för termisk värmestress.

    Exakt en av `vind_10m_m_s` eller `vind_fotgangarhojd_m_s` ska anges (inga gissningar).
    """
    if (vind_10m_m_s is None) == (vind_fotgangarhojd_m_s is None):
        raise ValueError(
            "Ange exakt en av `vind_10m_m_s` eller `vind_fotgangarhojd_m_s`."
        )

    if vind_fotgangarhojd_m_s is not None:
        v10_raw = vind_fotgangarhojd_till_10m(
            np.asarray(vind_fotgangarhojd_m_s, dtype=np.float64),
            hojd_m=vind_hojd_m,
            skrovlighet_z0_m=skrovlighet_z0_m,
        )
    else:
        assert vind_10m_m_s is not None
        v10_raw = np.asarray(vind_10m_m_s, dtype=np.float64)

    tmrt_res = berakna_tmrt(
        svf=svf,
        skuggmask=skuggmask,
        solhojd_rad=solhojd_rad,
        sol_direkt_w_m2=sol_direkt_w_m2,
        sol_diffus_w_m2=sol_diffus_w_m2,
        t_luft_c=t_luft_c,
        relativ_fuktighet_pct=relativ_fuktighet_pct,
        albedo_mark=albedo_mark,
        albedo_vagg=albedo_vagg,
        emissivitet_mark=emissivitet_mark,
        emissivitet_vagg=emissivitet_vagg,
        t_mark_soltillagg_c=t_mark_soltillagg_c,
        t_vagg_soltillagg_c=t_vagg_soltillagg_c,
        trad_mask=trad_mask,
        trad_transmissivitet=trad_transmissivitet,
        emissivitet_himmel=emissivitet_himmel,
        emissivitet_trad=emissivitet_trad,
        vagg_solandel=vagg_solandel,
        alpha_k=alpha_k,
        eps_p=eps_p,
    )

    utci_res = berakna_utci(
        t_luft_c=t_luft_c,
        t_mrt_c=tmrt_res.t_mrt_c,
        vind_10m_m_s=v10_raw,
        relativ_fuktighet_pct=relativ_fuktighet_pct,
        tillat_extrapolering=tillat_utci_extrapolering,
    )

    vind_10m_bc = np.asarray(
        np.broadcast_to(v10_raw, utci_res.utci_c.shape),
        dtype=np.float64,
    ).copy()
    tmrt_bc = np.asarray(
        np.broadcast_to(tmrt_res.t_mrt_c, utci_res.utci_c.shape),
        dtype=np.float64,
    )
    statistik = _statistik_fran_falt(tmrt_bc, utci_res)

    return VarmestressResultat(
        tmrt=tmrt_res,
        utci=utci_res,
        statistik=statistik,
        vind_10m_m_s=vind_10m_bc,
        varningar=utci_res.varningar,
    )


def jamfor_varmestress(
    fore: VarmestressResultat,
    efter: VarmestressResultat,
) -> VarmestressJamforelse:
    """Jämför två värmestresskörningar (före vs efter, t.ex. trädplantering eller ny byggnad).

    Skillnader definieras som `efter - fore`: negativa värden betyder avkylning / lägre stress.

    Raises:
        ValueError: Om rasternas former skiljer sig mellan `fore` och `efter`.
    """
    if fore.utci.utci_c.shape != efter.utci.utci_c.shape:
        raise ValueError(
            f"Kan inte jämföra värmestress med olika rasterformer: "
            f"fore={fore.utci.utci_c.shape}, efter={efter.utci.utci_c.shape}."
        )
    if fore.tmrt.t_mrt_c.shape != efter.tmrt.t_mrt_c.shape:
        raise ValueError(
            f"Kan inte jämföra T_mrt med olika rasterformer: "
            f"fore={fore.tmrt.t_mrt_c.shape}, efter={efter.tmrt.t_mrt_c.shape}."
        )

    delta_tmrt = np.asarray(efter.tmrt.t_mrt_c - fore.tmrt.t_mrt_c, dtype=np.float64)
    delta_utci = np.asarray(efter.utci.utci_c - fore.utci.utci_c, dtype=np.float64)

    giltig_tmrt = np.isfinite(delta_tmrt)
    giltig_utci = np.isfinite(delta_utci)

    n_tmrt = int(np.sum(giltig_tmrt))
    n_utci = int(np.sum(giltig_utci))

    if n_tmrt > 0:
        dt_vals = delta_tmrt[giltig_tmrt]
        dt_medel: float | None = float(np.mean(dt_vals))
        dt_min: float | None = float(np.min(dt_vals))
        dt_max: float | None = float(np.max(dt_vals))
    else:
        dt_medel = None
        dt_min = None
        dt_max = None

    if n_utci > 0:
        du_vals = delta_utci[giltig_utci]
        du_medel: float | None = float(np.mean(du_vals))
        du_min: float | None = float(np.min(du_vals))
        du_max: float | None = float(np.max(du_vals))
    else:
        du_medel = None
        du_min = None
        du_max = None

    varningar_lista: list[str] = []
    for v in (*fore.varningar, *efter.varningar):
        if v not in varningar_lista:
            varningar_lista.append(v)

    fore_mask = np.isfinite(fore.utci.utci_c)
    efter_mask = np.isfinite(efter.utci.utci_c)
    if not np.array_equal(fore_mask, efter_mask):
        n_skillnad = int(np.sum(fore_mask != efter_mask))
        varningar_lista.append(
            f"Antalet giltiga UTCI-celler skiljer sig mellan före och efter i {n_skillnad} "
            "cell(er); cellvisa skillnader beräknas endast på gemensamt giltiga celler."
        )

    return VarmestressJamforelse(
        delta_t_mrt_c=delta_tmrt,
        delta_utci_c=delta_utci,
        antal_jamforda_tmrt_celler=n_tmrt,
        antal_jamforda_utci_celler=n_utci,
        delta_t_mrt_medel_c=dt_medel,
        delta_t_mrt_min_c=dt_min,
        delta_t_mrt_max_c=dt_max,
        delta_utci_medel_c=du_medel,
        delta_utci_min_c=du_min,
        delta_utci_max_c=du_max,
        delta_andel_stark_varmestress=(
            efter.statistik.andel_stark_varmestress - fore.statistik.andel_stark_varmestress
        ),
        delta_andel_mycket_stark_varmestress=(
            efter.statistik.andel_mycket_stark_varmestress
            - fore.statistik.andel_mycket_stark_varmestress
        ),
        delta_andel_extrem_varmestress=(
            efter.statistik.andel_extrem_varmestress - fore.statistik.andel_extrem_varmestress
        ),
        fore_statistik=fore.statistik,
        efter_statistik=efter.statistik,
        varningar=tuple(varningar_lista),
    )


def _statistik_till_dict(s: VarmestressStatistik) -> dict[str, Any]:
    return {
        "antal_celler_totalt": s.antal_celler_totalt,
        "antal_giltiga_celler": s.antal_giltiga_celler,
        "antal_nan_celler": s.antal_nan_celler,
        "antal_utanfor_giltighetsomrade": s.antal_utanfor_giltighetsomrade,
        "andel_utanfor_giltighetsomrade": s.andel_utanfor_giltighetsomrade,
        "t_mrt_medel_c": s.t_mrt_medel_c,
        "t_mrt_min_c": s.t_mrt_min_c,
        "t_mrt_max_c": s.t_mrt_max_c,
        "t_mrt_p90_c": s.t_mrt_p90_c,
        "utci_medel_c": s.utci_medel_c,
        "utci_min_c": s.utci_min_c,
        "utci_max_c": s.utci_max_c,
        "utci_p90_c": s.utci_p90_c,
        "andel_ingen_stress": s.andel_ingen_stress,
        "andel_mattlig_varmestress": s.andel_mattlig_varmestress,
        "andel_stark_varmestress": s.andel_stark_varmestress,
        "andel_mycket_stark_varmestress": s.andel_mycket_stark_varmestress,
        "andel_extrem_varmestress": s.andel_extrem_varmestress,
        "klassfordelning_andel": {
            str(k.klass_id): s.klassfordelning_andel[k.klass_id] for k in UTCI_STRESSKLASSER
        },
    }


def varmestress_till_dict(resultat: VarmestressResultat) -> dict[str, Any]:
    """Serialiserar `VarmestressResultat` till en strikt JSON-kompatibel dict."""
    return {
        "format": FORMAT_MIKROKLIMAT,
        "metodstatus": resultat.metodstatus,
        "form": list(resultat.utci.utci_c.shape),
        "vinkelfaktorer": list(resultat.tmrt.vinkelfaktorer),
        "statistik": _statistik_till_dict(resultat.statistik),
        "varningar": list(resultat.varningar),
        "kontrollsummor": {
            "t_mrt_c_sha256": _kanonisk_raster_sha256(resultat.tmrt.t_mrt_c),
            "utci_c_sha256": _kanonisk_raster_sha256(resultat.utci.utci_c),
        },
        "referenser": [
            "Bröde m.fl. (2012), Int. J. Biometeorol. 56:481-494 (UTCI_a002)",
            "ISO 7726:1998",
            "Prata (1996), Q. J. R. Meteorol. Soc. 122:1127-1151",
            "Lindberg m.fl. (2008), Int. J. Biometeorol. 52:697-711 (SOLWEIG)",
        ],
    }


def varmestress_till_json(resultat: VarmestressResultat) -> str:
    """Deterministisk, strikt JSON-serialisering av `VarmestressResultat` (utan NaN/Inf)."""
    return json.dumps(
        varmestress_till_dict(resultat),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )


def varmestress_jamforelse_till_dict(jamforelse: VarmestressJamforelse) -> dict[str, Any]:
    """Serialiserar `VarmestressJamforelse` till en strikt JSON-kompatibel dict."""
    return {
        "format": FORMAT_MIKROKLIMAT,
        "metodstatus": jamforelse.metodstatus,
        "form": list(jamforelse.delta_utci_c.shape),
        "antal_jamforda_tmrt_celler": jamforelse.antal_jamforda_tmrt_celler,
        "antal_jamforda_utci_celler": jamforelse.antal_jamforda_utci_celler,
        "delta_t_mrt_medel_c": jamforelse.delta_t_mrt_medel_c,
        "delta_t_mrt_min_c": jamforelse.delta_t_mrt_min_c,
        "delta_t_mrt_max_c": jamforelse.delta_t_mrt_max_c,
        "delta_utci_medel_c": jamforelse.delta_utci_medel_c,
        "delta_utci_min_c": jamforelse.delta_utci_min_c,
        "delta_utci_max_c": jamforelse.delta_utci_max_c,
        "delta_andel_stark_varmestress": jamforelse.delta_andel_stark_varmestress,
        "delta_andel_mycket_stark_varmestress": jamforelse.delta_andel_mycket_stark_varmestress,
        "delta_andel_extrem_varmestress": jamforelse.delta_andel_extrem_varmestress,
        "fore_statistik": _statistik_till_dict(jamforelse.fore_statistik),
        "efter_statistik": _statistik_till_dict(jamforelse.efter_statistik),
        "varningar": list(jamforelse.varningar),
        "kontrollsummor": {
            "delta_t_mrt_c_sha256": _kanonisk_raster_sha256(jamforelse.delta_t_mrt_c),
            "delta_utci_c_sha256": _kanonisk_raster_sha256(jamforelse.delta_utci_c),
        },
    }


def varmestress_jamforelse_till_json(jamforelse: VarmestressJamforelse) -> str:
    """Deterministisk, strikt JSON-serialisering av `VarmestressJamforelse` (utan NaN/Inf)."""
    return json.dumps(
        varmestress_jamforelse_till_dict(jamforelse),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
