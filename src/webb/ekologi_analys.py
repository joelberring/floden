"""Svaret för `POST /api/analys/ekologi` (K45, `floden-ekologi/v1`).

Analysen körs av kärnan (lösaren `ekologi.konnektivitet`, K21) som en vanlig körning före/efter;
det här läser dess filer (`ekologi.json`, `stromtathet.tif`) och sätter ihop svaret. Inget tal
räknas om här, utom att flaskhalsar plockas ur strömtätheten (största värdena, med ett minsta
avstånd) och att skillnader mellan före och efter tas som differens.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from testsvit.k1_io import las_geotiff
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst

FORMAT_SVAR = "floden-ekologi/v1"


def storsta_flaskhalsar(
    j: np.ndarray[Any, Any], x_min: float, y_max: float, cell_m: float, antal: int, avstand_m: float
) -> list[dict[str, float]]:
    """De `antal` största strömtäthetsvärdena (J > 0), högst `avstand_m` isär, störst först."""
    v = np.where(np.isfinite(j), j, 0.0)
    ordning = np.argsort(-v, axis=None, kind="stable")
    ny, nx = v.shape
    r_cell = max(1, math.ceil(avstand_m / cell_m))
    tagen = np.zeros(v.shape, dtype=bool)
    ut: list[dict[str, float]] = []
    for flat in ordning:
        rad, kol = divmod(int(flat), nx)
        if v[rad, kol] <= 0.0 or len(ut) >= antal:
            break
        if tagen[rad, kol]:
            continue
        ut.append(
            {
                "e_m": float(x_min + (kol + 0.5) * cell_m),
                "n_m": float(y_max - (rad + 0.5) * cell_m),
                "stromtathet": float(v[rad, kol]),
            }
        )
        tagen[max(0, rad - r_cell) : min(ny, rad + r_cell + 1),
              max(0, kol - r_cell) : min(nx, kol + r_cell + 1)] = True
    return ut


def _las_json(fragor: FragaTjanst, korning_id: str, rel: str) -> dict[str, Any]:
    try:
        d = json.loads(fragor.fil_sokvag(korning_id, rel).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FlodenWebbFel(
            f"Ekologiresultatet {rel} går inte att läsa: {exc}", status_kod=500
        ) from exc
    return d  # type: ignore[no-any-return]


def ekologi_svar(
    fragor: FragaTjanst,
    *,
    korning_id: str,
    analys: str,
    omrade_id: str,
    scenario: str | None,
    antal_flaskhalsar: int,
    flaskhals_avstand_m: float,
    max_flackar: int,
    extra_varningar: list[str] | None = None,
) -> dict[str, Any]:
    res = fragor.resultat(korning_id)
    skill = next(
        (la for la in res["lager"] if la["typ"] == "skillnad" and la["roll"] == "stromtathet"),
        None,
    )
    if skill is None:
        raise FlodenWebbFel("Körningen har inget skillnadslager för strömtätheten.", status_kod=500)
    fore_s, efter_s = str(skill["fore"]), str(skill["efter"])

    def lager(sid: str) -> dict[str, Any] | None:
        la = next(
            (
                x for x in res["lager"]
                if x["analys"] == analys and x["scenario"] == sid and x["roll"] == "stromtathet"
                and x["typ"] != "skillnad"
            ),
            None,
        )
        return None if la is None else {"lager_id": la["id"], "png": la["png"],
                                         "utbredning": la["utbredning"]}

    fore = _las_json(fragor, korning_id, f"{analys}/{fore_s}/ekologi.json")
    efter = _las_json(fragor, korning_id, f"{analys}/{efter_s}/ekologi.json")

    def sammanfatta(d: dict[str, Any]) -> dict[str, Any]:
        return {k: d[k] for k in ("ECA_m2", "ECA_intervall_m2", "PC", "PC_intervall",
                                  "antal_flackar", "total_habitat_area_m2")}

    def flaskhalsar(sid: str) -> list[dict[str, float]]:
        fil = fragor.fil_sokvag(korning_id, f"{analys}/{sid}/stromtathet.tif")
        arr, geo = las_geotiff(fil)
        return storsta_flaskhalsar(
            np.asarray(arr, dtype=np.float64), geo.x_min, geo.y_max, geo.pixelstorlek_x,
            antal_flaskhalsar, flaskhals_avstand_m,
        )

    d_eca = float(efter["ECA_m2"]) - float(fore["ECA_m2"])
    varningar = [*(extra_varningar or []), *(str(v) for v in efter["varningar"])]
    natverk = efter.get("natverk")
    if d_eca > 1e-9:
        varningar.append(
            "ΔECA är positiv: ett förslag med nytt hus bör inte öka konnektiviteten. Orsaken är "
            "troligen rastreringen (4 m-celler); resultatet ska inte tolkas som en förbättring."
        )
    return {
        "format": FORMAT_SVAR,
        "omrade_id": omrade_id,
        "scenario": scenario,
        "metodstatus": "skiss",
        "artprofil_id": efter["artprofil_id"],
        "natverk": natverk,
        "intervall_typ": efter.get("intervall_typ", "faktorer"),
        "kalla_rad": natverk["kalla_rad"] if natverk else None,
        "resultat_etikett": (natverk.get("resultat_etikett") or None) if natverk else None,
        "antaganden": list(natverk["antaganden"]) if natverk else [],
        "parameterkallor": efter["parameterkallor"],
        "korning_id": korning_id,
        "cellstorlek_m": efter["cellstorlek_m"],
        "max_flackar": max_flackar,
        "fore": sammanfatta(fore),
        "efter": sammanfatta(efter),
        "delta_eca_m2": d_eca,
        "delta_eca_procent": (
            100.0 * d_eca / float(fore["ECA_m2"]) if float(fore["ECA_m2"]) > 0.0 else None
        ),
        "delta_pc": float(efter["PC"]) - float(fore["PC"]),
        "lager": {
            "stromtathet_fore": lager(fore_s),
            "stromtathet_efter": lager(efter_s),
            "stromtathet_skillnad": {
                "lager_id": skill["id"], "png": skill["png"], "utbredning": skill["utbredning"],
            },
        },
        "storsta_flaskhalsar": {
            "fore": flaskhalsar(fore_s),
            "efter": flaskhalsar(efter_s),
            "minsta_avstand_m": flaskhals_avstand_m,
        },
        "dPC_efter": efter["dPC"][:max_flackar],
        "varningar": varningar,
    }
