# ruff: noqa: E501
"""K38 fail-closed synkronisering av öppna K1 v2-rutor till S3 / objektlagring.

Laddar upp öppna K1 v2-rutor från en lokalt mappad geografi (K46) under nyckelschemat:
    `geografi/<kommun>/<rut_id>/k1/{mark.tif, terrang.tif, markslag.tif, osakerhet.tif, byggnader.gpkg, recept.json, k1_manifest.json}`

Säkerhetsinvarianter (K38 / `AGENTS.md`, fail-closed):
- Vägrar alltid synk om:
  1. `manifest["oppen_for_mcp"]` inte är `True`,
  2. `manifest["kallor"]["byggnader"]["stangd"]` är sant,
  3. `byggnadskalla == "lokal_fil"` (i `parametrar` eller `kallor.byggnader`),
  4. kommunen inte är verifierat öppen (`dataklass != "oppen"`, stängd baskarta eller okänd kommun),
  5. `oppenhet.bedom(mapp).oppen` är `False` (för antingen versionsmappen eller K1-lagermappen).
- Rådatalagret (`radata`) laddas aldrig upp; enbart strikt vitlistade K1-filer tillåts.
- Felmeddelanden vid K38-avvisning läcker aldrig stängda geografiers namn, kommuner eller filsökvägar.
- SHA-256-kontroll mot befintligt `k1_manifest.json` i objektlagringen hoppar över oförändrade filer.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_fil
from gemensamt.objektlagring import ObjektLagring, ObjektLagringFel, geografi_nyckel
from geografi.forberaknad_lager import (
    _FORBJUDNA_DATAKLASS_SEGMENT,
    _SEGMENT_RE,
)
from geografi.modell import (
    MANIFEST_NAMN,
    STANDARD_GEOROT,
    GeografiFel,
    lager_mapp,
    las_manifest,
    tolka_ref,
    trad_filer,
    trad_hash,
    versionsmapp,
)

K1_MANIFEST_KONTRAKT = "floden-k1-s3/v1"
K1_MANIFEST_FIL = "k1_manifest.json"
K1_SYNC_FILER: tuple[str, ...] = (
    "mark.tif",
    "terrang.tif",
    "markslag.tif",
    "osakerhet.tif",
    "byggnader.gpkg",
    "recept.json",
)
K1_VALFRIA_FILER: tuple[str, ...] = (
    "brytlinjer.gpkg",
    "oppningar.gpkg",
    "vegetation.tif",
)
K1_TILLATNA_FILER: frozenset[str] = frozenset((*K1_SYNC_FILER, *K1_VALFRIA_FILER))

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_K38_NEKAD_TEXT = (
    "Nekad (K38): geografin uppfyller inte kraven för öppen S3-synkning "
    "(kräver oppen_for_mcp=true, öppen kommun, ej stängd/lokal baskarta och godkänd oppenhet.toml)."
)


class S3SyncFel(GeografiFel):
    """Fel vid K1-synkronisering till objektlagring."""


class K38SyncNekad(S3SyncFel):
    """K38 fail-closed-avvisning: icke-öppen geografi eller kommun får aldrig synkas till extern lagring."""


@dataclass(frozen=True)
class ValideradOppenGeografi:
    """K38-verifierad öppen geografi klar för K1-synkning."""

    geografi_ref: str
    geografi_id: str
    version: str
    kommun: str
    manifest_sha256: str
    k1_lager_sha256: str
    versionsmapp: Path
    k1_mapp: Path


@dataclass(frozen=True)
class RutSyncResultat:
    """Synkresultat för en enskild K1-ruta."""

    rut_id: str
    uppladdade: tuple[str, ...]
    hoppade: tuple[str, ...]
    manifest_uppdaterad: bool
    bytes_uppladdade: int
    bytes_hoppade: int

    def till_dict(self) -> dict[str, Any]:
        return {
            "rut_id": self.rut_id,
            "uppladdade": list(self.uppladdade),
            "hoppade": list(self.hoppade),
            "manifest_uppdaterad": self.manifest_uppdaterad,
            "bytes_uppladdade": self.bytes_uppladdade,
            "bytes_hoppade": self.bytes_hoppade,
        }


@dataclass(frozen=True)
class K1SyncResultat:
    """Samlad rapport för `synka_k1_till_s3`."""

    geografi_ref: str
    kommun: str
    torrkor: bool
    dataklass: str
    k1_lager_sha256: str
    antal_rutor_totalt: int
    antal_rutor_bearbetade: int
    trunkerad: bool
    antal_filer_uppladdade: int
    antal_filer_hoppade: int
    bytes_uppladdade: int
    bytes_hoppade: int
    rutor: tuple[RutSyncResultat, ...]

    def till_dict(self) -> dict[str, Any]:
        return {
            "geografi_ref": self.geografi_ref,
            "kommun": self.kommun,
            "torrkor": self.torrkor,
            "dataklass": self.dataklass,
            "k1_lager_sha256": self.k1_lager_sha256,
            "antal_rutor_totalt": self.antal_rutor_totalt,
            "antal_rutor_bearbetade": self.antal_rutor_bearbetade,
            "trunkerad": self.trunkerad,
            "antal_filer_uppladdade": self.antal_filer_uppladdade,
            "antal_filer_hoppade": self.antal_filer_hoppade,
            "bytes_uppladdade": self.bytes_uppladdade,
            "bytes_hoppade": self.bytes_hoppade,
            "rutor": [r.till_dict() for r in self.rutor],
        }


def _validera_segment_strikt(varde: str, namn: str) -> str:
    if not isinstance(varde, str) or "\x00" in varde:
        raise S3SyncFel(f"Ogiltigt {namn}.")
    ren = varde.strip()
    if not ren or "/" in ren or "\\" in ren or ".." in ren or not _SEGMENT_RE.match(ren):
        raise S3SyncFel(f"Ogiltigt {namn}.")
    if ren.lower() in _FORBJUDNA_DATAKLASS_SEGMENT or ren.lower().startswith("dataklass="):
        raise K38SyncNekad(_K38_NEKAD_TEXT)
    return ren


def k1_s3_nyckel(kommun: str, rut_id: str, filnamn: str) -> str:
    """Returnerar säker objektnyckel `geografi/<kommun>/<rut_id>/k1/<filnamn>`."""
    ren_kommun = _validera_segment_strikt(kommun, "kommun")
    ren_ruta = _validera_segment_strikt(rut_id, "rut_id")
    if filnamn not in K1_TILLATNA_FILER and filnamn != K1_MANIFEST_FIL:
        raise S3SyncFel(f"Filnamnet {filnamn!r} är inte tillåtet i K1 S3-synk.")
    return geografi_nyckel(ren_kommun, ren_ruta, f"k1/{filnamn}")


def _ar_oppen_kommun(kommun_id: str, *, rot: Path | None = None) -> bool:
    from geografi.planera import bedom_kommun_dataklass
    from kommuner.konfig import KommunKonfigFel, las_kommun

    try:
        kfg = las_kommun(kommun_id, rot=rot) if rot is not None else las_kommun(kommun_id)
    except KommunKonfigFel:
        return False
    dataklass, stangd_baskarta, _, _ = bedom_kommun_dataklass(kfg)
    return dataklass == "oppen" and not stangd_baskarta and kfg.byggnader.kalla != "lokal_fil"


def validera_oppen_geografi_for_s3(
    geografi_ref: str,
    geo_rot: str | Path = STANDARD_GEOROT,
    *,
    oppenhet: Any | None = None,
    rot: str | Path | None = None,
    verifiera_lager: bool = True,
    kontrollera_kommun: bool = True,
) -> ValideradOppenGeografi:
    """K38 fail-closed-kontroll av en lokalt mappad geografi före S3-synk.

    Läcker aldrig stängda geografiers namn, kommuner eller filsökvägar i felmeddelanden.
    """
    try:
        geo_id, version = tolka_ref(geografi_ref)
    except GeografiFel as exc:
        raise S3SyncFel("Ogiltig geografireferens (förväntade formatet '<id>@v<nummer>').") from exc

    geo_rot_p = Path(geo_rot).resolve()
    vmapp = versionsmapp(geo_rot_p, geo_id, version)
    if vmapp.is_symlink():
        raise K38SyncNekad(_K38_NEKAD_TEXT)
    try:
        vmapp_abs = vmapp.resolve()
    except OSError as exc:
        raise K38SyncNekad(_K38_NEKAD_TEXT) from exc
    if not vmapp_abs.is_relative_to(geo_rot_p):
        raise K38SyncNekad(_K38_NEKAD_TEXT)
    if not (vmapp_abs / MANIFEST_NAMN).is_file():
        raise S3SyncFel("Geografin saknas eller är inte mappad lokalt.")

    try:
        manifest, manifest_sha = las_manifest(geo_rot_p, geografi_ref, verifiera=False)
    except GeografiFel as exc:
        raise S3SyncFel("Geografins manifest är ogiltigt eller oläsbart.") from exc

    # 1. Manifestets explicita öppenhetsflagga
    if manifest.get("oppen_for_mcp") is not True:
        raise K38SyncNekad(_K38_NEKAD_TEXT)

    # 2. Byggnadskälla & stängd-flagga (både kallor.byggnader och parametrar)
    kallor = manifest.get("kallor")
    bygg_kalla_obj = kallor.get("byggnader") if isinstance(kallor, Mapping) else None
    bygg_stangd = (
        bool(bygg_kalla_obj.get("stangd", False)) if isinstance(bygg_kalla_obj, Mapping) else False
    )
    if bygg_stangd:
        raise K38SyncNekad(_K38_NEKAD_TEXT)

    parametrar = manifest.get("parametrar")
    param_bygg_kalla = (
        str(parametrar.get("byggnadskalla", "")).strip() if isinstance(parametrar, Mapping) else ""
    )
    kalla_bygg_kalla = (
        str(bygg_kalla_obj.get("kalla", "")).strip() if isinstance(bygg_kalla_obj, Mapping) else ""
    )
    if param_bygg_kalla == "lokal_fil" or kalla_bygg_kalla == "lokal_fil":
        raise K38SyncNekad(_K38_NEKAD_TEXT)

    # 3. Kommunvalidering & öppenhet
    kommun_raw = manifest.get("kommun")
    if not isinstance(kommun_raw, str) or not kommun_raw.strip():
        raise K38SyncNekad(_K38_NEKAD_TEXT)
    try:
        kommun = _validera_segment_strikt(kommun_raw, "kommun")
    except S3SyncFel as exc:
        raise K38SyncNekad(_K38_NEKAD_TEXT) from exc

    rot_p = Path(rot).resolve() if rot is not None else None
    if kontrollera_kommun and not _ar_oppen_kommun(kommun, rot=rot_p):
        raise K38SyncNekad(_K38_NEKAD_TEXT)

    # 4. K1-lager + symlänkskontroll
    lager_raw = manifest.get("lager")
    k1_info = lager_raw.get("k1_rutnat") if isinstance(lager_raw, Mapping) else None
    k1_sha = str(k1_info.get("sha256", "")).strip() if isinstance(k1_info, Mapping) else ""
    if not _SHA256_RE.match(k1_sha):
        raise S3SyncFel("Geografin saknar ett giltigt k1_rutnat-lager i manifestet.")
    try:
        k1_mapp = lager_mapp(geo_rot_p, k1_sha)
    except GeografiFel as exc:
        raise S3SyncFel("Geografins k1_rutnat-lager saknas på disk.") from exc
    if k1_mapp.is_symlink() or not k1_mapp.resolve().is_relative_to(geo_rot_p):
        raise K38SyncNekad(_K38_NEKAD_TEXT)

    # 5. K38 oppenhet.bedom på BÅDE versionsmappen och k1_rutnat-lagermappen
    eff_oppenhet = oppenhet
    if eff_oppenhet is None:
        from floden_mcp.oppenhet import Oppenhet

        eff_oppenhet = Oppenhet(rot_p if rot_p is not None else geo_rot_p.parent)

    if not eff_oppenhet.bedom(vmapp_abs).oppen or not eff_oppenhet.bedom(k1_mapp.resolve()).oppen:
        raise K38SyncNekad(_K38_NEKAD_TEXT)

    # 6. Validera K1-lagrets trädhash mot manifestet (upptäcker ändrade/korrupta filer)
    if verifiera_lager:
        faktisk_hash = trad_hash(trad_filer(k1_mapp))
        if faktisk_hash != k1_sha:
            raise S3SyncFel("K1-lagrets innehåll stämmer inte med manifestets SHA-256.")

    return ValideradOppenGeografi(
        geografi_ref=f"{geo_id}@{version}",
        geografi_id=geo_id,
        version=version,
        kommun=kommun,
        manifest_sha256=manifest_sha,
        k1_lager_sha256=k1_sha,
        versionsmapp=vmapp_abs,
        k1_mapp=k1_mapp.resolve(),
    )


def _las_fjarr_k1_manifest(
    objektlagring: ObjektLagring,
    manifest_nyckel: str,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Läser befintligt `k1_manifest.json` från objektlagringen för SHA-256-hopp."""
    if not objektlagring.finns(manifest_nyckel):
        return None, {}
    with tempfile.TemporaryDirectory(prefix="floden_k1sync_chk_") as tmp_dir:
        lokal = Path(tmp_dir) / K1_MANIFEST_FIL
        try:
            objektlagring.ladda_ner_fil(manifest_nyckel, lokal)
            raa = json.loads(lokal.read_text(encoding="utf-8"))
        except (ObjektLagringFel, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None, {}
    if not isinstance(raa, Mapping) or raa.get("dataklass") != "oppen":
        return None, {}
    sha_per_fil: dict[str, str] = {}
    filer = raa.get("filer")
    if isinstance(filer, list):
        for post in filer:
            if (
                isinstance(post, Mapping)
                and isinstance(post.get("fil"), str)
                and isinstance(post.get("sha256"), str)
            ):
                sha_per_fil[post["fil"]] = post["sha256"].strip().lower()
    return dict(raa), sha_per_fil


def synka_k1_till_s3(
    geografi_ref: str,
    geo_rot: str | Path = STANDARD_GEOROT,
    *,
    objektlagring: ObjektLagring,
    torrkor: bool = True,
    max_rutor: int | None = None,
    rutor: Sequence[str] | None = None,
    oppenhet: Any | None = None,
    rot: str | Path | None = None,
    verifiera_lager: bool = True,
    kontrollera_kommun: bool = True,
) -> K1SyncResultat:
    """Synkroniserar öppna K1 v2-rutor till `geografi/<kommun>/<rut_id>/k1/*` med K38-spärr.

    - Kontrollerar alltid K38 fail-closed via `validera_oppen_geografi_for_s3`.
    - Hoppar över filer som redan finns i objektlagringen med identisk SHA-256 i `k1_manifest.json`.
    - Vid `torrkor=True` (förval) laddas inga filer upp, men exakta rutor/filer/storlekar rapporteras.
    - Laddar alltid upp `k1_manifest.json` med `dataklass="oppen"` sist per ruta (atomisk markör).
    """
    if max_rutor is not None and (isinstance(max_rutor, bool) or int(max_rutor) < 1):
        raise S3SyncFel("max_rutor måste vara ett heltal >= 1.")

    val = validera_oppen_geografi_for_s3(
        geografi_ref,
        geo_rot,
        oppenhet=oppenhet,
        rot=rot,
        verifiera_lager=verifiera_lager,
        kontrollera_kommun=kontrollera_kommun,
    )

    alla_rutor: list[str] = []
    for p in sorted(val.k1_mapp.iterdir()):
        if not p.name.startswith("mm_") or p.is_symlink() or not p.is_dir():
            continue
        if not p.resolve().is_relative_to(val.k1_mapp):
            raise K38SyncNekad(_K38_NEKAD_TEXT)
        alla_rutor.append(_validera_segment_strikt(p.name, "rut_id"))

    if rutor is not None:
        onskade = [_validera_segment_strikt(r, "rut_id") for r in rutor]
        saknade = [r for r in onskade if r not in set(alla_rutor)]
        if saknade:
            raise S3SyncFel(f"Begärda K1-rutor saknas i geografin: {', '.join(saknade[:5])}.")
        alla_rutor = [r for r in alla_rutor if r in set(onskade)]

    if not alla_rutor:
        raise S3SyncFel("Inga K1-rutor hittades i geografin.")

    antal_totalt = len(alla_rutor)
    if max_rutor is not None:
        valda_rutor = alla_rutor[: int(max_rutor)]
        trunkerad = antal_totalt > len(valda_rutor)
    else:
        valda_rutor = alla_rutor
        trunkerad = False

    rut_resultat: list[RutSyncResultat] = []
    tot_uppladdade = 0
    tot_hoppade = 0
    tot_bytes_upp = 0
    tot_bytes_hopp = 0

    for rut_id in valda_rutor:
        rut_mapp = val.k1_mapp / rut_id
        lokala_poster: list[tuple[str, Path, str, int]] = []
        for filnamn in sorted(K1_TILLATNA_FILER):
            lokal_fil = rut_mapp / filnamn
            if lokal_fil.is_symlink():
                raise S3SyncFel(f"Säkerhetsfel: symlänk i K1-ruta {rut_id}/{filnamn}.")
            if lokal_fil.is_file():
                if not lokal_fil.resolve().is_relative_to(rut_mapp.resolve()):
                    raise S3SyncFel(f"Säkerhetsfel: fil pekar utanför K1-ruta {rut_id}/{filnamn}.")
                lokala_poster.append(
                    (filnamn, lokal_fil, sha256_fil(lokal_fil).lower(), int(lokal_fil.stat().st_size))
                )

        filnamn_mangd = {f for f, _, _, _ in lokala_poster}
        if "mark.tif" not in filnamn_mangd:
            raise S3SyncFel(f"K1-rutan {rut_id} saknar obligatorisk fil 'mark.tif'.")

        k1_manifest: dict[str, Any] = {
            "kontrakt": K1_MANIFEST_KONTRAKT,
            "dataklass": "oppen",
            "kommun": val.kommun,
            "rut_id": rut_id,
            "geografi_ref": val.geografi_ref,
            "geografi_manifest_sha256": val.manifest_sha256,
            "k1_lager_sha256": val.k1_lager_sha256,
            "filer": [
                {"fil": namn, "sha256": sha, "storlek_byte": storlek}
                for namn, _, sha, storlek in lokala_poster
            ],
        }

        manifest_nyckel = k1_s3_nyckel(val.kommun, rut_id, K1_MANIFEST_FIL)
        fjarr_manifest, fjarr_sha = _las_fjarr_k1_manifest(objektlagring, manifest_nyckel)

        uppladdade: list[str] = []
        hoppade: list[str] = []
        bytes_upp = 0
        bytes_hopp = 0

        for filnamn, lokal_fil, sha, storlek in lokala_poster:
            nyckel = k1_s3_nyckel(val.kommun, rut_id, filnamn)
            if fjarr_sha.get(filnamn) == sha and objektlagring.finns(nyckel):
                hoppade.append(filnamn)
                bytes_hopp += storlek
                continue
            uppladdade.append(filnamn)
            bytes_upp += storlek
            if not torrkor:
                objektlagring.ladda_upp_fil(lokal_fil, nyckel, sha256=sha)

        manifest_matchar = fjarr_manifest == k1_manifest and not uppladdade
        manifest_uppdaterad = not manifest_matchar
        if manifest_uppdaterad and not torrkor:
            with tempfile.TemporaryDirectory(prefix="floden_k1sync_man_") as tmp_dir:
                tmp_manifest = Path(tmp_dir) / K1_MANIFEST_FIL
                tmp_manifest.write_text(
                    json.dumps(k1_manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                objektlagring.ladda_upp_fil(
                    tmp_manifest, manifest_nyckel, sha256=sha256_fil(tmp_manifest)
                )

        rut_resultat.append(
            RutSyncResultat(
                rut_id=rut_id,
                uppladdade=tuple(uppladdade),
                hoppade=tuple(hoppade),
                manifest_uppdaterad=manifest_uppdaterad,
                bytes_uppladdade=bytes_upp,
                bytes_hoppade=bytes_hopp,
            )
        )
        tot_uppladdade += len(uppladdade)
        tot_hoppade += len(hoppade)
        tot_bytes_upp += bytes_upp
        tot_bytes_hopp += bytes_hopp

    return K1SyncResultat(
        geografi_ref=val.geografi_ref,
        kommun=val.kommun,
        torrkor=bool(torrkor),
        dataklass="oppen",
        k1_lager_sha256=val.k1_lager_sha256,
        antal_rutor_totalt=antal_totalt,
        antal_rutor_bearbetade=len(rut_resultat),
        trunkerad=trunkerad,
        antal_filer_uppladdade=tot_uppladdade,
        antal_filer_hoppade=tot_hoppade,
        bytes_uppladdade=tot_bytes_upp,
        bytes_hoppade=tot_bytes_hopp,
        rutor=tuple(rut_resultat),
    )


__all__ = [
    "K1_MANIFEST_FIL",
    "K1_MANIFEST_KONTRAKT",
    "K1_SYNC_FILER",
    "K1_TILLATNA_FILER",
    "K1_VALFRIA_FILER",
    "K1SyncResultat",
    "K38SyncNekad",
    "RutSyncResultat",
    "S3SyncFel",
    "ValideradOppenGeografi",
    "k1_s3_nyckel",
    "synka_k1_till_s3",
    "validera_oppen_geografi_for_s3",
]
