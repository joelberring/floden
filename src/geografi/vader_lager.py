# SPDX-License-Identifier: GPL-3.0-or-later
"""Additivt K46-lager `lager.vader_luft` (SMHI vindros, UTCI-värmebölja, bakgrundsluft, timserie).

Lagret ligger innehållsadresserat i `<geo-rot>/_lager/<sha256>/` och läggs in som ett valfritt
additivt lager under `lager.vader_luft` i `floden-geografi/v1` (K46) utan att bryta befintliga
geografier.

Filer i lagret:
- `vindros.json` — 16-sektors vindros med stationsproveniens och bortfiltreringsstatistik.
- `utci_sommar.json` — sommar-/värmeböljepercentiler (p95/p99) inkl. ev. SMHI STRÅNG-strålning.
- `bakgrund_luft.json` — urban bakgrundshalt (NO₂, PM₁₀, O₃) eller ärlig `tillganglig: false`.
- `vader_timserie.csv.gz` (valfri) — deterministiskt komprimerad (`mtime=0`) timvis väderserie
  för NORTRIP-vägdamm.

Öppenhet (K38): licens `CC-BY-4.0` (CC BY 4.0, `© SMHI`), öppen för MCP när
`villkor_kontrollerade=True`.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from data_inlasning.smhi_luft_strang import (
    SMHI_LICENS_SPDX,
    SMHI_LICENS_TEXT,
    BakgrundLuftResultat,
    StrangSommarResultat,
    serialisera_vaderserie_csv,
)
from geografi.modell import (
    KONTRAKT_GEOGRAFI,
    MANIFEST_NAMN,
    GeografiFel,
    lager_mapp,
    lagra,
    las_manifest,
    versionsmapp,
)
from geografi.smhi_vader import VarmeboljaStatistik, Vindros16
from kommuner.skapa import skriv_oppenhet_toml
from luft.emission import VaderserieTimme

VADER_LUFT_KALLA: Final[str] = "SMHI Öppna data (MetObs, STRÅNG & luftkvalitet). © SMHI."
VADER_LUFT_LICENS_SPDX: Final[str] = SMHI_LICENS_SPDX
VADER_LUFT_LICENS_TEXT: Final[str] = SMHI_LICENS_TEXT

FIL_VINDROS: Final[str] = "vindros.json"
FIL_UTCI_SOMMAR: Final[str] = "utci_sommar.json"
FIL_BAKGRUND_LUFT: Final[str] = "bakgrund_luft.json"
FIL_VADER_TIMSERIE_GZ: Final[str] = "vader_timserie.csv.gz"

STANDARD_SOLHOJD_SOMMAR_GRAD: Final[float] = 50.0
STANDARD_SOLAZIMUT_SOMMAR_GRAD: Final[float] = 180.0


def _skriv_deterministisk_json(fil: Path, data: Mapping[str, Any]) -> None:
    fil.parent.mkdir(parents=True, exist_ok=True)
    fil.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _skriv_deterministisk_gzip(fil: Path, innehall: bytes) -> None:
    """Skriver gzip med `mtime=0` och tomt filnamn så samma indata ger exakt samma SHA-256."""
    fil.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, compresslevel=6, mtime=0) as gz:
        gz.write(innehall)
    fil.write_bytes(buf.getvalue())


def _till_dict_vindros(vindros: Vindros16 | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(vindros, Vindros16):
        return vindros.till_dict()
    if isinstance(vindros, Mapping):
        d = dict(vindros)
        if not isinstance(d.get("sektorer"), list) or len(d["sektorer"]) != 16:
            raise GeografiFel("vindros måste innehålla exakt 16 sektorer.")
        return d
    raise GeografiFel(f"Ogiltig vindros-typ: {type(vindros).__name__}.")


def _till_dict_utci(utci_sommar: VarmeboljaStatistik | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(utci_sommar, VarmeboljaStatistik):
        return utci_sommar.till_dict()
    if isinstance(utci_sommar, Mapping):
        d = dict(utci_sommar)
        if "scenario_p95" not in d or "scenario_p99" not in d:
            raise GeografiFel("utci_sommar måste innehålla både 'scenario_p95' och 'scenario_p99'.")
        return d
    raise GeografiFel(f"Ogiltig utci_sommar-typ: {type(utci_sommar).__name__}.")


def _till_dict_bakgrund(
    bakgrund_luft: BakgrundLuftResultat | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if bakgrund_luft is None:
        return {
            "tillganglig": False,
            "station_id": None,
            "station_namn": None,
            "station_typ": None,
            "avstand_km": None,
            "kalla": None,
            "licens": None,
            "period_start": None,
            "period_slut": None,
            "sha256": None,
            "amnen": {},
            "varningar": [
                "Urban bakgrundshalt (NO₂, PM₁₀, O₃) angavs inte vid geografimappning."
            ],
        }
    if isinstance(bakgrund_luft, BakgrundLuftResultat):
        return bakgrund_luft.till_dict()
    if isinstance(bakgrund_luft, Mapping):
        return dict(bakgrund_luft)
    raise GeografiFel(f"Ogiltig bakgrund_luft-typ: {type(bakgrund_luft).__name__}.")


def bygg_vader_lager(
    geo_rot: Path,
    bygge: Path,
    karna_3011: tuple[float, float, float, float],
    *,
    vindros: Vindros16 | Mapping[str, Any],
    utci_sommar: VarmeboljaStatistik | Mapping[str, Any],
    bakgrund_luft: BakgrundLuftResultat | Mapping[str, Any] | None = None,
    vader_timserie_csv: str | bytes | Path | VaderserieTimme | None = None,
    villkor_kontrollerade: bool = False,
    station_avstand_km: float | None = None,
    ar: int | None = None,
    period: str | None = None,
    strang: StrangSommarResultat | Mapping[str, Any] | None = None,
    kallor_sha256: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Bygger och lagrar additivt K46-lager `lager.vader_luft` via `geografi.modell.lagra`.

    Returnerar `(lager_dict, kallor_dict, varningar)` att sammanfoga i geografimanifestet.
    """
    e0, n0, e1, n1 = karna_3011
    if not all(math.isfinite(v) for v in (e0, n0, e1, n1)) or e0 >= e1 or n0 >= n1:
        raise GeografiFel(f"Ogiltig karna_3011 för väderlagret: {karna_3011!r}.")

    vindros_d = _till_dict_vindros(vindros)
    utci_d = _till_dict_utci(utci_sommar)
    bakgrund_d = _till_dict_bakgrund(bakgrund_luft)

    station_id = vindros_d.get("station_id") or utci_d.get("station_id")
    station_namn = vindros_d.get("station_namn") or utci_d.get("station_namn")
    avst = (
        round(float(station_avstand_km), 2)
        if station_avstand_km is not None
        else vindros_d.get("station_avstand_km") or utci_d.get("station_avstand_km")
    )
    valt_ar = int(ar) if ar is not None else vindros_d.get("ar") or utci_d.get("ar")
    karna_lista = [float(e0), float(n0), float(e1), float(n1)]

    # Berika JSON-dokumenten med enhetlig stations- och proveniensmetadata.
    vindros_ut: dict[str, Any] = {
        **vindros_d,
        "karna_3011": karna_lista,
        "station_id": station_id,
        "station_namn": station_namn,
        "station_avstand_km": avst,
        "ar": valt_ar,
        "period": period or vindros_d.get("period"),
        "kalla": VADER_LUFT_KALLA,
        "licens": VADER_LUFT_LICENS_SPDX,
    }
    strang_d: dict[str, Any] | None = None
    if isinstance(strang, StrangSommarResultat):
        strang_d = strang.till_dict()
    elif isinstance(strang, Mapping):
        strang_d = dict(strang)
    elif isinstance(utci_d.get("strang"), Mapping):
        strang_d = dict(utci_d["strang"])

    utci_ut: dict[str, Any] = {
        **utci_d,
        "karna_3011": karna_lista,
        "station_id": station_id,
        "station_namn": station_namn,
        "station_avstand_km": avst,
        "ar": valt_ar,
        "period": period or utci_d.get("period"),
        "strang": strang_d,
        "kalla": VADER_LUFT_KALLA,
        "licens": VADER_LUFT_LICENS_SPDX,
    }
    if kallor_sha256:
        utci_ut["kallor_sha256"] = {str(k): str(v) for k, v in sorted(kallor_sha256.items())}

    mapp = Path(bygge) / "vader_luft"
    if mapp.exists():
        shutil.rmtree(mapp)
    mapp.mkdir(parents=True, exist_ok=True)

    _skriv_deterministisk_json(mapp / FIL_VINDROS, vindros_ut)
    _skriv_deterministisk_json(mapp / FIL_UTCI_SOMMAR, utci_ut)
    _skriv_deterministisk_json(mapp / FIL_BAKGRUND_LUFT, bakgrund_d)

    har_timserie = False
    if vader_timserie_csv is not None:
        if isinstance(vader_timserie_csv, VaderserieTimme):
            ra_bytes = serialisera_vaderserie_csv(vader_timserie_csv).encode("utf-8")
        elif isinstance(vader_timserie_csv, Path):
            if not vader_timserie_csv.is_file():
                raise GeografiFel(f"vader_timserie_csv-filen {vader_timserie_csv} finns inte.")
            fil_bytes = vader_timserie_csv.read_bytes()
            ra_bytes = gzip.decompress(fil_bytes) if fil_bytes[:2] == b"\x1f\x8b" else fil_bytes
        elif isinstance(vader_timserie_csv, bytes):
            ra_bytes = (
                gzip.decompress(vader_timserie_csv)
                if vader_timserie_csv[:2] == b"\x1f\x8b"
                else vader_timserie_csv
            )
        elif isinstance(vader_timserie_csv, str):
            ra_bytes = vader_timserie_csv.encode("utf-8")
        else:
            raise GeografiFel(
                f"Ogiltig typ för vader_timserie_csv: {type(vader_timserie_csv).__name__}."
            )
        if not ra_bytes.strip():
            raise GeografiFel("vader_timserie_csv får inte vara tom.")
        _skriv_deterministisk_gzip(mapp / FIL_VADER_TIMSERIE_GZ, ra_bytes)
        har_timserie = True

    lg = lagra(Path(geo_rot), mapp)
    skriv_oppenhet_toml(
        lg.mapp,
        oppen=bool(villkor_kontrollerade),
        licens=VADER_LUFT_LICENS_SPDX,
        kalla=VADER_LUFT_KALLA,
        anledning=(
            "Villkoren är kontrollerade (--villkor-kontrollerade): SMHI CC BY 4.0 (© SMHI), "
            "öppet lager."
            if villkor_kontrollerade
            else "Villkoren för väder- och luftlagret är inte kontrollerade av Floden: stängt "
            "för MCP tills --villkor-kontrollerade (CC BY 4.0, © SMHI)."
        ),
    )

    varningar: list[str] = []
    if not bakgrund_d.get("tillganglig"):
        for v in bakgrund_d.get("varningar", []):
            if isinstance(v, str) and v not in varningar:
                varningar.append(v)

    lager_post: dict[str, Any] = {
        "sha256": lg.sha256,
        "station_id": station_id,
        "station_namn": station_namn,
        "station_avstand_km": avst,
        "ar": valt_ar,
        "har_bakgrund_luft": bool(bakgrund_d.get("tillganglig")),
        "har_vader_timserie": har_timserie,
        "karna_3011": karna_lista,
        "filer": [{"fil": f, "sha256": s} for f, s in lg.filer],
    }
    kalla_post: dict[str, Any] = {
        "licens": VADER_LUFT_LICENS_SPDX,
        "licens_text": VADER_LUFT_LICENS_TEXT,
        "kalla": VADER_LUFT_KALLA,
        "station_id": station_id,
        "station_namn": station_namn,
        "station_avstand_km": avst,
        "ar": valt_ar,
    }
    return {"vader_luft": lager_post}, {"vader_luft": kalla_post}, varningar


@dataclass(frozen=True)
class GeografiVaderLuft:
    """SHA-256-verifierat K46-lager `lager.vader_luft` inläst från geografimanifest."""

    ref: str
    manifest_sha256: str
    lager_sha256: str
    station_id: str | None
    station_namn: str | None
    station_avstand_km: float | None
    ar: int | None
    vindros: dict[str, Any]
    utci_sommar: dict[str, Any]
    bakgrund_luft: dict[str, Any] | None
    vader_timserie_fil: Path | None
    kalla: str
    licens: str
    varningar: tuple[str, ...] = ()

    @property
    def har_vader_timserie(self) -> bool:
        return self.vader_timserie_fil is not None and self.vader_timserie_fil.is_file()

    def las_vader_timserie_csv(self) -> str | None:
        """Returnerar dekomprimerad CSV-text om `vader_timserie.csv.gz` finns i lagret."""
        if not self.har_vader_timserie or self.vader_timserie_fil is None:
            return None
        return gzip.decompress(self.vader_timserie_fil.read_bytes()).decode("utf-8")

    def kalla_text_kort(self) -> str:
        """Kort källsträng för UI, t.ex. `SMHI Göteborg A, 2,1 km, 2024`."""
        delar: list[str] = []
        if self.station_namn:
            delar.append(f"SMHI {self.station_namn}")
        elif self.station_id:
            delar.append(f"SMHI station {self.station_id}")
        else:
            delar.append("SMHI")
        if self.station_avstand_km is not None:
            avst_sv = f"{float(self.station_avstand_km):.1f}".replace(".", ",")
            delar.append(f"{avst_sv} km")
        if self.ar is not None:
            delar.append(str(self.ar))
        return ", ".join(delar)

    def utci_forval(self) -> dict[str, dict[str, Any]]:
        """Returnerar UTCI-förvalen `platsens_smhi_p95`/`p99` med ev. STRÅNG-strålning."""
        strang_obj = (
            self.utci_sommar.get("strang")
            if isinstance(self.utci_sommar.get("strang"), Mapping)
            else None
        )
        kalla_kort = self.kalla_text_kort()
        ut: dict[str, dict[str, Any]] = {}

        for nyckel, scen_nyckel, p_label in (
            ("platsens_smhi_p95", "scenario_p95", "p95"),
            ("platsens_smhi_p99", "scenario_p99", "p99"),
        ):
            sc = self.utci_sommar.get(scen_nyckel)
            if not isinstance(sc, Mapping):
                continue
            t_luft = sc.get("t_luft_c")
            if not isinstance(t_luft, int | float) or not math.isfinite(float(t_luft)):
                continue

            glob_dag_w = (
                strang_obj.get(f"global_{p_label}_dag_w_m2")
                if strang_obj is not None
                else None
            )
            dir_norm_w = (
                strang_obj.get(f"direkt_normal_{p_label}_dag_w_m2")
                if strang_obj is not None
                else None
            )
            # Para alltid sammanhörande dagtidspercentiler från STRÅNG (global + direkt_normal)
            # när båda finns, så att direkt horisontell strålning inte överstiger median-GHI från
            # sena eftermiddagstimmar och klämmer sol_diffus_w_m2 till 0.
            glob_w = glob_dag_w if glob_dag_w is not None else sc.get("globalstralning_w_m2")

            # Härled horisontell direkt + diffus strålning ur STRÅNG när globalstrålning finns.
            sol_direkt: float | None = None
            sol_diffus: float | None = None
            sin_h = math.sin(math.radians(STANDARD_SOLHOJD_SOMMAR_GRAD))
            if (
                isinstance(glob_w, int | float)
                and math.isfinite(float(glob_w))
                and float(glob_w) >= 0.0
            ):
                g_val = float(glob_w)
                if (
                    isinstance(dir_norm_w, int | float)
                    and math.isfinite(float(dir_norm_w))
                    and float(dir_norm_w) >= 0.0
                    and float(dir_norm_w) * sin_h < g_val
                ):
                    dir_horiz = float(dir_norm_w) * sin_h
                    sol_direkt = round(float(dir_norm_w), 1)
                    sol_diffus = round(max(0.0, g_val - dir_horiz), 1)
                else:
                    # Fysikalisk klarhimmelsuppdelning (82 % direkt horisontell / 18 % diffus).
                    sol_diffus = round(0.18 * g_val, 1)
                    sol_direkt = round(max(0.0, (g_val - sol_diffus) / max(sin_h, 0.1)), 1)

            rh_v = sc.get("rh_pct")
            vind_v = sc.get("vind_10m_m_s")
            t_sv = f"{float(t_luft):.1f}".replace(".", ",")
            ut[nyckel] = {
                "id": nyckel,
                "percentil": float(sc.get("percentil", 95.0 if p_label == "p95" else 99.0)),
                "etikett": f"Platsens SMHI-värmebölja ({p_label}) · {t_sv} °C",
                "station_id": self.station_id,
                "station_namn": self.station_namn,
                "station_avstand_km": self.station_avstand_km,
                "ar": self.ar,
                "kalla_text": kalla_kort,
                "t_luft_c": round(float(t_luft), 2),
                "relativ_fuktighet_pct": (
                    round(float(rh_v), 1) if isinstance(rh_v, int | float) else None
                ),
                "vind_10m_m_s": (
                    round(float(vind_v), 2) if isinstance(vind_v, int | float) else None
                ),
                "globalstralning_w_m2": (
                    round(float(glob_w), 1) if isinstance(glob_w, int | float) else None
                ),
                "direkt_normal_w_m2": (
                    round(float(dir_norm_w), 1) if isinstance(dir_norm_w, int | float) else None
                ),
                "sol_direkt_w_m2": sol_direkt,
                "sol_diffus_w_m2": sol_diffus,
                "solhojd_grad": STANDARD_SOLHOJD_SOMMAR_GRAD,
                "solazimut_grad": STANDARD_SOLAZIMUT_SOMMAR_GRAD,
                "strang_kalla": (
                    str(strang_obj.get("kalla"))
                    if strang_obj is not None and strang_obj.get("kalla")
                    else None
                ),
            }
        return ut

    def som_dict(self) -> dict[str, Any]:
        """JSON-serialiserbar representation utan interna server-sökvägar."""
        return {
            "geografi": self.ref,
            "manifest_sha256": self.manifest_sha256,
            "lager_sha256": self.lager_sha256,
            "station_id": self.station_id,
            "station_namn": self.station_namn,
            "station_avstand_km": self.station_avstand_km,
            "ar": self.ar,
            "kalla_text": self.kalla_text_kort(),
            "kalla": self.kalla,
            "licens": self.licens,
            "vindros": self.vindros,
            "utci_sommar": self.utci_sommar,
            "utci_forval": self.utci_forval(),
            "bakgrund_luft": self.bakgrund_luft,
            "har_vader_timserie": self.har_vader_timserie,
            "varningar": list(self.varningar),
        }


def _las_och_verifiera_manifest(manifest_fil: Path) -> tuple[dict[str, Any], str, Path]:
    fil = Path(manifest_fil)
    if not fil.is_file():
        raise GeografiFel(f"Geografins manifest {fil.name} finns inte.")
    try:
        d = json.loads(fil.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise GeografiFel(f"Manifestet {fil.name} går inte att läsa.") from exc
    if not isinstance(d, dict) or d.get("kontrakt") != KONTRAKT_GEOGRAFI:
        raise GeografiFel(f"{fil.name} är inte ett geografimanifest ({KONTRAKT_GEOGRAFI}).")
    geo_rot = fil.resolve().parents[2]
    ref = f"{d.get('id')}@{d.get('version')}"
    forvantad_mapp = versionsmapp(geo_rot, str(d.get("id")), str(d.get("version"))).resolve()
    if forvantad_mapp != fil.resolve().parent:
        raise GeografiFel(
            f"Manifestet ligger inte på {MANIFEST_NAMN}s plats "
            f"<geo-rot>/{d.get('id')}/{d.get('version')}/."
        )
    manifest, sha = las_manifest(geo_rot, ref, verifiera=True)
    return manifest, sha, geo_rot


def las_vader_lager(manifest_fil: str | Path) -> GeografiVaderLuft | None:
    """Verifierar SHA-256 i manifestet och läser `lager.vader_luft` (eller `None` om saknas)."""
    manifest, msha, geo_rot = _las_och_verifiera_manifest(Path(manifest_fil))
    lager = manifest.get("lager", {})
    if "vader_luft" not in lager:
        return None

    vl_post = lager["vader_luft"]
    vl_sha = str(vl_post["sha256"])
    mapp = lager_mapp(geo_rot, vl_sha)

    vindros_fil = mapp / FIL_VINDROS
    utci_fil = mapp / FIL_UTCI_SOMMAR
    bakgrund_fil = mapp / FIL_BAKGRUND_LUFT
    timserie_fil = mapp / FIL_VADER_TIMSERIE_GZ

    if not vindros_fil.is_file() or not utci_fil.is_file():
        raise GeografiFel(
            f"Lagret 'vader_luft' ({vl_sha[:12]}) saknar {FIL_VINDROS} eller {FIL_UTCI_SOMMAR}."
        )
    try:
        vindros = json.loads(vindros_fil.read_text(encoding="utf-8"))
        utci_sommar = json.loads(utci_fil.read_text(encoding="utf-8"))
        bakgrund_luft = (
            json.loads(bakgrund_fil.read_text(encoding="utf-8"))
            if bakgrund_fil.is_file()
            else None
        )
    except (OSError, ValueError) as exc:
        raise GeografiFel(f"Kunde inte läsa JSON i lager 'vader_luft': {exc}") from exc

    kalla_meta = manifest.get("kallor", {}).get("vader_luft", {})
    kalla = str(kalla_meta.get("kalla") or vindros.get("kalla") or VADER_LUFT_KALLA)
    licens = str(kalla_meta.get("licens") or vindros.get("licens") or VADER_LUFT_LICENS_SPDX)

    st_id = vl_post.get("station_id") or vindros.get("station_id") or utci_sommar.get("station_id")
    st_namn = (
        vl_post.get("station_namn")
        or vindros.get("station_namn")
        or utci_sommar.get("station_namn")
    )
    st_avst_ra = (
        vl_post.get("station_avstand_km")
        if vl_post.get("station_avstand_km") is not None
        else vindros.get("station_avstand_km")
    )
    st_avst = float(st_avst_ra) if isinstance(st_avst_ra, int | float) else None
    ar_ra = vl_post.get("ar") if vl_post.get("ar") is not None else vindros.get("ar")
    ar_val = int(ar_ra) if isinstance(ar_ra, int | float) else None

    varningar: list[str] = []
    if isinstance(bakgrund_luft, Mapping):
        for v in bakgrund_luft.get("varningar", []):
            if isinstance(v, str) and v not in varningar:
                varningar.append(v)

    ref = f"{manifest['id']}@{manifest['version']}"
    return GeografiVaderLuft(
        ref=ref,
        manifest_sha256=msha,
        lager_sha256=vl_sha,
        station_id=str(st_id) if st_id is not None else None,
        station_namn=str(st_namn) if st_namn is not None else None,
        station_avstand_km=st_avst,
        ar=ar_val,
        vindros=dict(vindros),
        utci_sommar=dict(utci_sommar),
        bakgrund_luft=dict(bakgrund_luft) if isinstance(bakgrund_luft, Mapping) else None,
        vader_timserie_fil=timserie_fil if timserie_fil.is_file() else None,
        kalla=kalla,
        licens=licens,
        varningar=tuple(varningar),
    )


__all__ = [
    "FIL_BAKGRUND_LUFT",
    "FIL_UTCI_SOMMAR",
    "FIL_VADER_TIMSERIE_GZ",
    "FIL_VINDROS",
    "VADER_LUFT_KALLA",
    "VADER_LUFT_LICENS_SPDX",
    "VADER_LUFT_LICENS_TEXT",
    "GeografiVaderLuft",
    "bygg_vader_lager",
    "las_vader_lager",
]
