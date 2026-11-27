# ruff: noqa: E501
"""Opt-in superadmin utvecklingsläge för Flodens MCP-server (`FLODEN_MCP_UTVECKLINGSLAGE=1`).

Säkerhetsinvarianter (dubbellås och icke-förbikopplingsbara spärrar):

1. **Dubbellås:** Verktygen (`utv_*`) registreras ENDAST när miljövariabeln
   `FLODEN_MCP_UTVECKLINGSLAGE` är exakt `"1"`, och varje anrop kräver dessutom ett
   autentiserat konto med `Register.ar_superadmin(konto_id) == True` (kontrolleras både i
   serverns middleware och inne i varje verktyg).
2. **0 hemligheter:** API-nycklar, krypteringshemligheter, S3-nycklar, OAuth-hemligheter och
   bearer-tokens läses och exponeras aldrig.
3. **0 personuppgifter (PII):** Kontoverktyg selekterar och returnerar enbart anonymiserat
   `konto_id`, roll-/spärrstatus, organisationsmedlemskap och tidsstämplar — aldrig e-post
   eller personnamn. Loggar och felmeddelanden redigeras för e-postadresser och tokens.
4. **K38-öppenhetsspärr:** Stängda kommuner, lokala baskartor, stängda geografier och
   icke-öppna projekt nekas konsekvent (fail-closed).
5. **Inga godtyckliga skalkommandon:** Endast explicita typade verktyg som anropar Flodens
   egna interna moduler eller strikt vitlistade delprocesser (`testsvit`, `benchmark`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from floden_mcp.dataklass import DataKlass
from floden_mcp.karna_api import PROJEKT_ID, FlodenTjanst
from floden_mcp.korlogg import Korlogg
from floden_mcp.oppenhet import Oppenhet, OppenhetNekad

UTVECKLINGSLAGE_ENV = "FLODEN_MCP_UTVECKLINGSLAGE"
UTV_PREFIX = "utv_"

_FEL_PREFIX = "[floden]"
_VERSION_RE = re.compile(r"^v[1-9][0-9]*$")
_REGISTER_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_GILTIGA_TESTSVIT_GRUPPER = frozenset({"A", "B", "C", "D", "E"})
_TILLATNA_LAGRING_LISTA_PREFIX = ("geografi/", "tmp/")
_TILLATNA_LAGRING_STADA_PREFIX = ("tmp/",)
_BENCHMARK_STANDARD_FALL: dict[str, str] = {
    "skyfall": "EA1",
    "dagsljus": "grupp-E",
    "luft": "L5-dualitet",
    "buller": "nord2000-tvastrale",
}

_EPOST_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![A-Za-z0-9-])"
)
_TOKEN_RE = re.compile(
    r"(?:fl_mcp_|fl_oat_|fl_sess_|sk-ant-|AIza)[A-Za-z0-9._-]{4,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b"
    r"|\bgAAAA[A-Za-z0-9_-]{20,}\b"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_HEMLIG_KV_RE = re.compile(
    r"(?i)\b(access_key|secret_key|client_secret|password|hemlighet|authorization)\s*([=:])\s*\S+"
)
_SAMMANFATTNING_RE = re.compile(r"(\d+)/(\d+)\s+(?:testfall\s+)?passerade")
_VOLYMFEL_PCT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
_RUT_PREFIX_RE = re.compile(r"^mm_-?\d+_-?\d+:\s*")
_BYGGNAD_ID_PREFIX_RE = re.compile(r"^b_[0-9a-fA-F]{6,}:\s*")
_TAL_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

Modell = Annotated[
    str | None,
    Field(
        description="Valfritt: vilken AI-modell som styr anropet (loggas i körloggen; påverkar inget resultat)."
    ),
]
Roll = Annotated[
    str | None,
    Field(
        description="Valfritt: modellens roll i arbetet (loggas i körloggen; påverkar inget resultat)."
    ),
]


def utvecklingslage_aktivt(miljo: Mapping[str, str] | None = None) -> bool:
    """Returnerar True endast när `FLODEN_MCP_UTVECKLINGSLAGE` är exakt `'1'`."""
    env = os.environ if miljo is None else miljo
    return env.get(UTVECKLINGSLAGE_ENV) == "1"


def redigera_text(text: str) -> str:
    """Tar bort e-postadresser, tokens, bearer-huvuden och nyckel-/lösenordsvärden ur diagnostiktext."""
    ut = _BEARER_RE.sub("Bearer [redigerad]", text)
    ut = _TOKEN_RE.sub("[redigerad-token]", ut)
    ut = _HEMLIG_KV_RE.sub(r"\1\2[redigerad]", ut)
    ut = _EPOST_RE.sub("[redigerad-epost]", ut)
    return ut


def redigera_objekt(obj: Any, *, djup: int = 0) -> Any:
    """Rekursiv redigering av strängar i nästlade listor/lexikon för 0 PII och 0 tokens."""
    if djup > 8:
        return "[trunkerad]"
    if isinstance(obj, str):
        return redigera_text(obj)
    if isinstance(obj, Mapping):
        ut: dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if ks.lower() in {"epost", "email", "namn", "password", "secret_key", "access_key", "token_klartext"}:
                continue
            ut[ks] = redigera_objekt(v, djup=djup + 1)
        return ut
    if isinstance(obj, list):
        return [redigera_objekt(x, djup=djup + 1) for x in obj]
    return obj


def _kategorisera_mappningsvarning(text: str) -> tuple[str, bool]:
    """Returnerar `(kategori, ar_byggnadsvarning)` för en enskild mappningsvarning."""
    utan_ruta = _RUT_PREFIX_RE.sub("", text.strip())
    har_bygg_id = bool(_BYGGNAD_ID_PREFIX_RE.match(utan_ruta))
    utan_id = _BYGGNAD_ID_PREFIX_RE.sub("", utan_ruta).strip()
    low = utan_id.lower()

    if "ingen markhöjd under eller kring byggnaden" in low:
        return "byggnad_saknar_markhojd", True
    if "ingen cellcentrum i byggnaden" in low:
        return "byggnad_inget_cellcentrum", True
    if "källans takhöjd ligger" in low and "ignorerad" in low:
        return "byggnad_ogiltig_kalltakhojd", True
    if "laserns takhöjd ligger" in low and "ignorerad" in low:
        return "byggnad_ogiltig_lasertakhojd", True
    if "byggnader saknade höjdkälla" in low or "byggnader saknar takhöjd" in low:
        return "byggnad_standardhojd", True
    if har_bygg_id:
        norm = _TAL_RE.sub("<tal>", low)
        return f"byggnad:{norm[:60]}", True
    norm = _TAL_RE.sub("<tal>", low)
    return f"varning:{norm[:80]}", False


def sammanfatta_mappningsvarningar(
    varningar: Sequence[str],
    *,
    max_exempel_per_kategori: int = 3,
    max_enskilda: int = 20,
) -> dict[str, Any]:
    """Sammanfattar repetitiva byggnads- och rutvarningar kompakt per kategori med antal och exempel.

    Håller MCP-svaret från `utv_geografi_mappa` kompakt (i stället för t.ex. 1 496 råa rader)
    samtidigt som totalantal, kategoriuppdelning och toppexempel alltid bevaras.
    """
    max_ex = max(1, int(max_exempel_per_kategori))
    max_ensk = max(1, int(max_enskilda))
    rensade = [redigera_text(str(v).strip()) for v in varningar if str(v).strip()]
    totalt = len(rensade)
    if totalt == 0:
        return {
            "varningar": [],
            "varningar_totalt": 0,
            "varningar_sammanfattning": [],
        }

    grupper: dict[str, list[str]] = {}
    ar_bygg: dict[str, bool] = {}
    for rad in rensade:
        kat, bygg = _kategorisera_mappningsvarning(rad)
        grupper.setdefault(kat, []).append(rad)
        ar_bygg[kat] = ar_bygg.get(kat, False) or bygg

    sammanfattning: list[dict[str, Any]] = []
    enskilda: list[str] = []
    for kat, rader in sorted(grupper.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(rader) >= 2:
            exempel = rader[:max_ex]
            sammanfattning.append(
                {
                    "kategori": kat,
                    "antal": len(rader),
                    "exempel": exempel,
                }
            )
        else:
            enskilda.extend(rader)

    kompakta: list[str] = []
    for post in sammanfattning:
        kompakta.append(
            f"[{post['kategori']}] {post['antal']} st (t.ex. {post['exempel'][0]})"
        )
    if len(enskilda) > max_ensk:
        kompakta.extend(enskilda[:max_ensk])
        kompakta.append(f"... samt {len(enskilda) - max_ensk} ytterligare unika varningar.")
    else:
        kompakta.extend(enskilda)

    return {
        "varningar": kompakta,
        "varningar_totalt": totalt,
        "varningar_sammanfattning": sammanfattning,
    }


def _las_app_konfig(konfig_fil: Path) -> dict[str, Any]:
    from gemensamt.konfig import KonfigFel, las_konfig

    if not konfig_fil.is_file():
        return {}
    lokal = konfig_fil.resolve().parent / "config.local.toml"
    try:
        return las_konfig(konfig_fil, lokal if lokal.is_file() else None)
    except KonfigFel:
        return {}


def _normalisera_lagring_prefix(prefix: str, tillatna: tuple[str, ...]) -> str:
    """Validerar och normaliserar lagringsprefix mot vitlistade rotprefix (`geografi/`, `tmp/`)."""
    if "\\" in prefix or "\x00" in prefix:
        raise ToolError(f"Ogiltigt lagringsprefix. {_FEL_PREFIX}fel_indata")
    ren = prefix.strip().lstrip("/")
    if ren in {"geografi", "tmp"}:
        ren = f"{ren}/"
    segment = [s for s in ren.split("/") if s]
    if any(s in (".", "..") for s in segment):
        raise ToolError(f"Ogiltigt lagringsprefix (path traversal nekas). {_FEL_PREFIX}fel_indata")
    slutar_slash = ren.endswith("/")
    kanon = "/".join(segment) + ("/" if slutar_slash and segment else "")
    if not any(kanon.startswith(p) for p in tillatna):
        tillatna_str = ", ".join(repr(p) for p in tillatna)
        raise ToolError(
            f"Nekad: lagringsprefix måste ligga under {tillatna_str}. {_FEL_PREFIX}nekad_prefix"
        )
    return kanon


def _tolka_bbox_avgransning(avgransning: list[float] | str) -> list[float]:
    """Accepterar enbart bbox (`[xmin, ymin, xmax, ymax]` eller `'xmin,ymin,xmax,ymax'`), aldrig godtyckliga filsökvägar."""
    if isinstance(avgransning, str):
        delar = [p.strip() for p in avgransning.split(",")]
        if len(delar) != 4:
            raise ToolError(
                "avgransning ska vara en bbox [xmin, ymin, xmax, ymax] eller 'xmin,ymin,xmax,ymax' "
                f"(filsökvägar tillåts inte via MCP). {_FEL_PREFIX}fel_indata"
            )
        try:
            varden = [float(p) for p in delar]
        except ValueError as exc:
            raise ToolError(
                f"avgransning innehåller ogiltiga koordinater (endast bbox tillåts). {_FEL_PREFIX}fel_indata"
            ) from exc
    elif isinstance(avgransning, list) and len(avgransning) == 4:
        try:
            varden = [float(v) for v in avgransning]
        except (TypeError, ValueError) as exc:
            raise ToolError(f"avgransning ska innehålla fyra tal. {_FEL_PREFIX}fel_indata") from exc
    else:
        raise ToolError(
            f"avgransning ska vara en bbox med fyra tal [xmin, ymin, xmax, ymax]. {_FEL_PREFIX}fel_indata"
        )
    if varden[2] <= varden[0] or varden[3] <= varden[1]:
        raise ToolError(f"Ogiltig bbox: xmax > xmin och ymax > ymin krävs. {_FEL_PREFIX}fel_indata")
    return varden


def _tolka_testsvit_utdata(utdata: str) -> tuple[list[dict[str, Any]], int, int, float | None, str]:
    """Tolkar Markdown-tabeller (A/B, C/EA, D/Hunter, E/dagsljus) och sammanfattningsraden från `floden testsvit`."""
    fall: list[dict[str, Any]] = []
    max_volymfel_rel: float | None = None
    sammanfattning = ""
    kolumner: list[str] = []
    for rad in utdata.splitlines():
        ren = rad.strip()
        if ren.startswith("Sammanfattning"):
            sammanfattning = redigera_text(ren)
            continue
        if not ren.startswith("|") or not ren.endswith("|"):
            continue
        celler = [c.strip() for c in ren.strip("|").split("|")]
        if len(celler) < 6:
            continue
        if celler[0] in ("ID", "Fall"):
            kolumner = [c.lower() for c in celler]
            continue
        if celler[0] == "---" or all(set(c) <= {"-", ":"} for c in celler if c):
            continue
        rad_map = {kol: celler[i] for i, kol in enumerate(kolumner) if i < len(celler)}
        fid = rad_map.get("id") or rad_map.get("fall") or celler[0]
        grupp = rad_map.get("grupp", "")
        namn = rad_map.get("namn") or rad_map.get("beskrivning") or ""
        if "utfall" in rad_map:
            utfall_text = rad_map["utfall"]
        elif rad_map.get("status") in ("GODKAND", "UNDERKAND", "SKELETT"):
            utfall_text = rad_map["status"]
        else:
            utfall_text = next(
                (c for c in celler if c in ("GODKAND", "UNDERKAND", "SKELETT")), ""
            )
        volym_text = rad_map.get("volymfel", "")
        if not volym_text and not kolumner:
            volym_text = next((c for c in celler if "%" in c), "")
        kortid_text = rad_map.get("körtid (s)") or rad_map.get("2d (s)") or ""
        if volym_text and volym_text not in ("–", "-"):
            m_vol = _VOLYMFEL_PCT_RE.search(volym_text)
            rel: float | None = None
            if m_vol:
                rel = float(m_vol.group(1)) / 100.0
            else:
                try:
                    rel = abs(float(volym_text))
                except ValueError:
                    rel = None
            if rel is not None:
                max_volymfel_rel = rel if max_volymfel_rel is None else max(max_volymfel_rel, rel)
        fall.append(
            {
                "id": fid,
                "grupp": grupp,
                "namn": redigera_text(namn),
                "utfall": utfall_text,
                "volymfel": None if volym_text in ("", "–", "-") else volym_text,
                "kortid_s": None if kortid_text in ("", "–", "-") else kortid_text,
            }
        )
    antal_godkanda = sum(1 for f in fall if f["utfall"] in ("GODKAND", "SKELETT"))
    antal_totalt = len(fall)
    m_sum = _SAMMANFATTNING_RE.search(sammanfattning)
    if m_sum:
        antal_godkanda = int(m_sum.group(1))
        antal_totalt = int(m_sum.group(2))
    elif "GODKAND" in sammanfattning and "UNDERKAND" not in sammanfattning and antal_totalt > 0 and antal_godkanda == 0:
        antal_godkanda = antal_totalt
    return fall, antal_godkanda, antal_totalt, max_volymfel_rel, sammanfattning


def registrera_utvecklingsverktyg(
    *,
    verktyg: Callable[..., Callable[[Callable[..., Any]], Callable[..., Any]]],
    tjanst: FlodenTjanst,
    logg: Korlogg,
    tillstand: Any,
    oppenhet: Oppenhet,
    miljo: Mapping[str, str],
) -> tuple[str, ...]:
    """Registrerar typade superadmin-utvecklingsverktyg (`utv_*`). Anropas endast när flaggan är `'1'`."""
    registrerade: list[str] = []

    def _krav_superadmin() -> str:
        """Försvar på djupet i varje verktyg (utöver serverns middleware)."""
        if not utvecklingslage_aktivt(miljo):
            raise ToolError(
                f"403 Nekad: MCP-utvecklingsläget är avstängt. {_FEL_PREFIX}403"
            )
        konto_id = tillstand.konto
        if not isinstance(konto_id, str) or not konto_id:
            raise ToolError(
                f"401 Inloggning krävs: utvecklingsverktyg kräver ett inloggat superadmin-konto. {_FEL_PREFIX}401"
            )
        reg = tjanst.app.konto.register
        if not tjanst.app.konto.aktiv or reg is None or not reg.ar_superadmin(konto_id):
            raise ToolError(
                f"403 Nekad: utvecklingsverktyg (utv_*) kräver behörigheten superadmin. {_FEL_PREFIX}403"
            )
        return konto_id

    def _hitta_projektmapp(projekt_id: str) -> Path | None:
        mapp = tjanst.projektmapp(projekt_id)
        if mapp is not None:
            return mapp
        from webb.projektkalla import hitta_projektmapp

        return hitta_projektmapp(tjanst.app.projektmapp, projekt_id, tillat_projektid=False)

    def _krav_oppet_projekt(projekt_id: str) -> Path:
        """K38 fail-closed: kräver att projektets mapp finns och är verifierat öppen."""
        if projekt_id in (PROJEKT_ID, tjanst.app.konto.konfig.demoprojekt):
            raise ToolError(f"Nekad: demokvarteret är skyddat för detta verktyg. {_FEL_PREFIX}demo_skyddat")
        mapp = _hitta_projektmapp(projekt_id)
        if mapp is None:
            raise ToolError(
                "Nekad (K38): projektets underlag saknar verifierad öppenhetsmetadata och nekas som standard. "
                f"{_FEL_PREFIX}nekad_oppenhet"
            )
        try:
            oppenhet.kontrollera_sokvag(mapp)
        except OppenhetNekad as exc:
            raise ToolError(f"{exc} {_FEL_PREFIX}nekad_oppenhet") from exc
        return mapp

    def _ar_oppen_kommun(kommun_id: str) -> bool:
        from geografi.planera import bedom_kommun_dataklass
        from kommuner.konfig import KommunKonfigFel, las_kommun

        try:
            kfg = las_kommun(kommun_id, rot=tjanst.rot)
        except KommunKonfigFel:
            return False
        dataklass, stangd_baskarta, _, _ = bedom_kommun_dataklass(kfg)
        return dataklass == "oppen" and not stangd_baskarta and kfg.byggnader.kalla != "lokal_fil"

    def _bygg_lagring() -> tuple[Any, Any]:
        from gemensamt.objektlagring import ObjektLagringFel, las_lagring_konfig, skapa_lagring

        konfig = _las_app_konfig(tjanst.app.konfig_fil)
        try:
            lk = las_lagring_konfig(konfig, miljo=miljo)
            if lk.typ == "lokal" and not lk.rot.is_absolute():
                lk = replace(lk, rot=(tjanst.rot / lk.rot).resolve())
            lagring = skapa_lagring(lk)
        except ObjektLagringFel as exc:
            raise ToolError(f"Lagringsfel: {redigera_text(str(exc))} {_FEL_PREFIX}lagring") from exc
        return lk, lagring

    def utv(namn: str, beskrivning: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        assert namn.startswith(UTV_PREFIX), namn
        registrerade.append(namn)
        return verktyg(namn, beskrivning, klass=DataKlass.OPPET)

    # ------------------------------------------------------------------ 1. drift: hälsa och loggar

    @utv(
        "utv_drift_halsa",
        "Superadmin-utvecklingsverktyg: visar serverns hälsotelemetri, kontodatabasens SQLite-integritet "
        "(WAL, integrity_check, foreign_key_check, anonyma tabellradantal), aktiv git-commit och körloggens "
        "hashkedjestatus. Returnerar 0 PII och 0 hemligheter.",
    )
    def utv_drift_halsa(modell: Modell = None, roll: Roll = None) -> dict[str, Any]:
        _krav_superadmin()
        from gemensamt.manifest import hamta_kodversion
        from konto.backup import drifts_halsa

        webb_halsa = dict(tjanst.app.halsa())
        webb_halsa["mcp_utvecklingslage"] = True
        reg = tjanst.app.konto.register
        db_sokvag = reg.db.sokvag if reg is not None else None
        konto_halsa = drifts_halsa(
            konfig_fil=tjanst.app.konfig_fil,
            db_sokvag=db_sokvag,
            miljo=miljo,
        )
        db_raw = dict(konto_halsa.get("databas", {}))
        db_sanerad = {
            k: db_raw.get(k)
            for k in (
                "finns",
                "storlek_byte",
                "journal_mode",
                "wal_aktivt",
                "integrity_check",
                "foreign_key_check_ok",
                "har_konto_tabell",
                "tabellrader",
                "filrattigheter",
            )
        }
        if db_raw.get("fel"):
            db_sanerad["fel"] = redigera_text(str(db_raw["fel"]))

        lagr_raw = dict(konto_halsa.get("lagring", {}))
        lagr_sanerad = {
            k: lagr_raw.get(k)
            for k in ("typ", "konfigurerad", "endpoint", "bucket", "region", "prefix", "boto3_installerad")
        }
        if lagr_raw.get("fel"):
            lagr_sanerad["fel"] = redigera_text(str(lagr_raw["fel"]))

        logg_ok, logg_rader, logg_fel_rad = logg.verifiera()
        commit = webb_halsa.get("commit") or hamta_kodversion(tjanst.rot)
        return {
            "ok": bool(konto_halsa.get("ok")) and webb_halsa.get("status") == "ok" and logg_ok,
            "mcp_utvecklingslage": True,
            "commit": commit,
            "kodversion": tjanst.kodversion,
            "halsa": webb_halsa,
            "konto_db": db_sanerad,
            "lagring": lagr_sanerad,
            "korlogg": {
                "ok": logg_ok,
                "rader_kontrollerade": logg_rader,
                "forsta_fel_rad": logg_fel_rad,
            },
            "varningar": [redigera_text(str(v)) for v in konto_halsa.get("varningar", [])],
        }

    @utv(
        "utv_drift_loggar",
        "Superadmin-utvecklingsverktyg: läser de senaste N raderna ur den hashkedjade MCP-körloggen "
        "med redigering för 0 PII och 0 tokens samt verifierar hashkedjans integritet.",
    )
    def utv_drift_loggar(
        senaste: Annotated[int, Field(description="Antal senaste rader att läsa (1-200).")] = 50,
        verktyg_filter: Annotated[
            str | None, Field(description="Filtrera på exakt verktygsnamn, t.ex. 'utv_drift_halsa'.")
        ] = None,
        utfall: Annotated[
            str | None, Field(description="Filtrera på utfall, t.ex. 'ok', 'fel', 'nekad_behorighet'.")
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        max_rader = max(1, min(int(senaste), 200))
        rader = logg.las(verktyg=verktyg_filter, senaste=5000)
        if utfall is not None:
            rader = [r for r in rader if r.get("utfall") == utfall]
        urval = [redigera_objekt(r) for r in rader[-max_rader:]]
        ok, antal, fel_rad = logg.verifiera()
        return {
            "antal_rader": len(urval),
            "rader": urval,
            "hashkedja": {"ok": ok, "rader_kontrollerade": antal, "forsta_fel_rad": fel_rad},
        }

    # ------------------------------------------------------------------ 2. geografi (strikt öppen / K38)

    @utv(
        "utv_geografi_lista",
        "Superadmin-utvecklingsverktyg: listar öppna konfigurerade kommuner och öppna mappade geografier "
        "(K46). Icke-öppna kommuner och stängda baskartor döljs helt enligt K38.",
    )
    def utv_geografi_lista(modell: Modell = None, roll: Roll = None) -> dict[str, Any]:
        _krav_superadmin()
        from geografi.modell import LAGERMAPP, GeografiFel, las_manifest
        from geografi.planera import bedom_kommun_dataklass
        from kommuner.konfig import KommunKonfigFel, las_kommun, lista_kommuner

        oppna_kommuner: list[dict[str, Any]] = []
        dolda_kommuner = 0
        for kid in lista_kommuner(tjanst.rot):
            try:
                kfg = las_kommun(kid, rot=tjanst.rot)
            except KommunKonfigFel:
                continue
            dataklass, stangd_baskarta, _, _ = bedom_kommun_dataklass(kfg)
            if dataklass != "oppen" or stangd_baskarta or kfg.byggnader.kalla == "lokal_fil":
                dolda_kommuner += 1
                continue
            oppna_kommuner.append(
                {
                    "kommun_id": kfg.id,
                    "namn": kfg.namn,
                    "publicering_epsg": kfg.publicering_epsg,
                    "dataklass": "oppen",
                    "byggnadskalla": kfg.byggnader.kalla,
                    "hojdkalla": kfg.hojd.kalla,
                    "geografier": [
                        {
                            "id": gid,
                            "namn": g.namn,
                            "omrade": g.omrade,
                            "max_buffert_m": g.max_buffert_m,
                        }
                        for gid, g in sorted(kfg.geografier.items())
                    ],
                }
            )

        mappade: list[dict[str, Any]] = []
        dolda_geografier = 0
        geomapp = tjanst.app.projekt_skapa.geomapp
        if geomapp.is_dir():
            for idmapp in sorted(
                p for p in geomapp.iterdir() if p.is_dir() and p.name != LAGERMAPP and not p.name.startswith("_")
            ):
                for vmapp in sorted(
                    (p for p in idmapp.iterdir() if p.is_dir() and not p.name.startswith("_")),
                    key=lambda p: (len(p.name), p.name),
                ):
                    ref = f"{idmapp.name}@{vmapp.name}"
                    try:
                        d, sha = las_manifest(geomapp, ref, verifiera=False)
                    except GeografiFel:
                        continue
                    kommun_id = str(d.get("kommun", ""))
                    bygg_kalla = str((d.get("parametrar") or {}).get("byggnadskalla", ""))
                    bygg_stangd = bool(((d.get("kallor") or {}).get("byggnader") or {}).get("stangd", False))
                    ar_oppen = (
                        bool(d.get("oppen_for_mcp"))
                        and not bygg_stangd
                        and bygg_kalla != "lokal_fil"
                        and (not kommun_id or _ar_oppen_kommun(kommun_id))
                        and oppenhet.bedom(vmapp).oppen
                    )
                    if not ar_oppen:
                        dolda_geografier += 1
                        continue
                    mappade.append(
                        {
                            "ref": ref,
                            "id": str(d.get("id", idmapp.name)),
                            "version": str(d.get("version", vmapp.name)),
                            "namn": str(d.get("namn", idmapp.name)),
                            "kommun": kommun_id,
                            "oppen_for_mcp": True,
                            "karna_3011": d.get("karna_3011"),
                            "gransen_3011": d.get("gransen_3011"),
                            "manifest_sha256": sha,
                        }
                    )

        return {
            "oppna_kommuner": oppna_kommuner,
            "mappade_geografier": mappade,
            "dolda_icke_oppna_kommuner": dolda_kommuner,
            "dolda_icke_oppna_geografier": dolda_geografier,
        }

    @utv(
        "utv_geografi_planera",
        "Superadmin-utvecklingsverktyg: planerar rutnät, buffert och källbelagda resursuppskattningar "
        "för en öppen kommun utifrån bbox i EPSG:3011 (eller angiven EPSG). Stängda kommuner nekas (K38).",
    )
    def utv_geografi_planera(
        kommun: Annotated[str, Field(description="Kommun-id (endast öppna kommuner, t.ex. 'goteborg').")],
        avgransning: Annotated[
            list[float] | str,
            Field(description="Bbox [xmin, ymin, xmax, ymax] eller 'xmin,ymin,xmax,ymax' (inga filsökvägar)."),
        ],
        avgransning_epsg: Annotated[
            int | None, Field(description="EPSG-kod för bbox (t.ex. 3011, 3007, 4326; standard 3011).")
        ] = None,
        upplosning_m: Annotated[
            float | None, Field(description="Cellstorlek i meter (standard ur kommunfilen).")
        ] = None,
        buffert_m: Annotated[
            float | None, Field(description="Buffert i meter (standard beräknas automatiskt).")
        ] = None,
        rutstorlek_m: Annotated[float, Field(description="Inre rutstorlek i meter (standard 500.0).")] = 500.0,
        overlapp_m: Annotated[float, Field(description="Överlapp per ruta i meter (standard 25.0).")] = 25.0,
        arbetare: Annotated[int, Field(description="Antal CPU-arbetare i uppskattningen (1-64).")] = 1,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        from geografi.planera import PlaneringsFel, bedom_kommun_dataklass, planera_geografi
        from kommuner.konfig import KommunKonfigFel, las_kommun

        bbox = _tolka_bbox_avgransning(avgransning)
        try:
            kfg = las_kommun(kommun, rot=tjanst.rot)
        except KommunKonfigFel as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}okand") from exc
        dataklass, stangd_baskarta, _, _ = bedom_kommun_dataklass(kfg)
        if dataklass != "oppen" or stangd_baskarta or kfg.byggnader.kalla == "lokal_fil":
            raise ToolError(
                f"Nekad (K38): kommunen '{kfg.id}' är inte öppna data och får inte planeras via MCP. "
                f"{_FEL_PREFIX}nekad_oppenhet"
            )
        try:
            plan = planera_geografi(
                kfg,
                bbox,
                upplosning_m=upplosning_m,
                buffert_m=buffert_m,
                rutstorlek_m=rutstorlek_m,
                overlapp_m=overlapp_m,
                arbetare=max(1, min(int(arbetare), 64)),
                avgransning_epsg=avgransning_epsg,
                krav_oppen=True,
            )
        except PlaneringsFel as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}fel_indata") from exc
        return plan.till_dict()

    @utv(
        "utv_geografi_mappa",
        "Superadmin-utvecklingsverktyg: bygger och registrerar en öppen geografi (K46) för en öppen kommun, "
        "valfritt med de additiva K46-lagren trafik, väder/luft (SMHI), skyddad natur/Skogsstyrelsen/VMI "
        "och publika rödlistefynd (SLU SOS med OSL-spärr). Kommuner med stängd/lokal baskarta nekas alltid "
        "(K38); inga lokala filsökvägar tillåts.",
    )
    def utv_geografi_mappa(
        geografi: Annotated[str, Field(description="Geografi-id i öppen kommunfil, t.ex. 'goteborg-test'.")],
        version: Annotated[str, Field(description="Versionsnamn på formen v1, v2, ...")] = "v1",
        villkor_kontrollerade: Annotated[
            bool, Field(description="Bekräfta att kommunens och Lantmäteriets öppna villkor är kontrollerade.")
        ] = False,
        hamta_trafikverket: Annotated[
            bool,
            Field(
                description="Valfritt: hämta NVDB-vägnät från Trafikverket Öppet API med kontots krypterade 'trafikverket'-nyckel."
            ),
        ] = False,
        trafik_fran_baskarta: Annotated[
            bool,
            Field(
                description="Valfritt: bygg klickbara väglänkar (utan påhittat ÅDT) ur kommunens baskarta gator.gpkg till lager.trafik."
            ),
        ] = False,
        hamta_vader_luft: Annotated[
            bool,
            Field(
                description="Valfritt: hämta SMHI MetObs/STRÅNG (vindros, UTCI-värmebölja, timserie) och urban bakgrundsluft till lager.vader_luft."
            ),
        ] = False,
        hamta_skyddad_natur: Annotated[
            bool,
            Field(
                description="Valfritt: hämta Naturvårdsverket WFS (Skyddad natur + Natura 2000), Skogsstyrelsen ArcGIS REST samt VMI läns-ZIP till lager.skyddad_natur."
            ),
        ] = False,
        vmi_lan: Annotated[
            str | None,
            Field(
                description="Valfritt: VMI-länskatalog på Naturvårdsverket (t.ex. 'O_Goteborg_Bohus'); standard hämtas ur kommunens [filpaket.vmi]."
            ),
        ] = None,
        hamta_artfynd: Annotated[
            bool,
            Field(
                description="Valfritt: hämta publika rödlistade/fridlysta artfynd via SLU Artdatabanken SOS med hård OSL-spärr (kräver kontots 'artdatabanken'-nyckel)."
            ),
        ] = False,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        konto_id = _krav_superadmin()
        from geografi.mappa import mappa_geografi
        from geografi.modell import GeografiFel
        from geografi.planera import bedom_kommun_dataklass
        from kommuner.konfig import KommunKonfigFel, hitta_kommun_for_geografi
        from kommuner.skapa import SkapaFel

        if not _VERSION_RE.match(version):
            raise ToolError(
                f"Ogiltig version '{version}': förväntade format v1, v2, ... {_FEL_PREFIX}fel_indata"
            )
        ren_vmi_lan: str | None = None
        if vmi_lan is not None and str(vmi_lan).strip():
            cand = str(vmi_lan).strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", cand):
                raise ToolError(
                    f"Ogiltigt vmi_lan '{cand}': endast bokstäver, siffror, '_' och '-' tillåts. {_FEL_PREFIX}fel_indata"
                )
            ren_vmi_lan = cand
        try:
            kommun = hitta_kommun_for_geografi(geografi, rot=tjanst.rot)
        except KommunKonfigFel as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}okand") from exc
        dataklass, stangd_baskarta, _, _ = bedom_kommun_dataklass(kommun)
        if dataklass != "oppen" or stangd_baskarta or kommun.byggnader.kalla == "lokal_fil":
            raise ToolError(
                f"Nekad (K38): geografin '{geografi}' hör till icke-öppen kommun/lokal baskarta och får "
                f"inte mappas via MCP. {_FEL_PREFIX}nekad_oppenhet"
            )
        konfig = _las_app_konfig(tjanst.app.konfig_fil)
        loggradder: list[str] = []
        extra_kwargs: dict[str, Any] = {}
        if hamta_trafikverket:
            extra_kwargs["hamta_trafikverket"] = True
        if trafik_fran_baskarta:
            extra_kwargs["trafik_fran_baskarta"] = True
        if hamta_vader_luft:
            extra_kwargs["hamta_vader_luft"] = True
        if hamta_skyddad_natur:
            extra_kwargs["hamta_skyddad_natur"] = True
        if ren_vmi_lan is not None:
            extra_kwargs["vmi_lan"] = ren_vmi_lan
        if hamta_artfynd:
            extra_kwargs["hamta_artfynd"] = True
        if hamta_trafikverket or hamta_artfynd:
            extra_kwargs["nyckel_forrad"] = tjanst.app.konto.nycklar
        try:
            res = mappa_geografi(
                kommun,
                geografi,
                konto_id=konto_id,
                geo_rot=tjanst.app.projekt_skapa.geomapp,
                konfig=konfig,
                konfig_fil=tjanst.app.konfig_fil,
                version=version,
                villkor_kontrollerade=bool(villkor_kontrollerade),
                register=tjanst.app.konto.register,
                skriv=lambda rad: loggradder.append(redigera_text(rad)),
                **extra_kwargs,
            )
        except (SkapaFel, GeografiFel) as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}geografi_fel") from exc
        varn_sam = sammanfatta_mappningsvarningar(res.varningar)
        return {
            "ref": res.ref,
            "manifest_sha256": res.manifest_sha256,
            "nytt": res.nytt,
            "oppen_for_mcp": res.oppen_for_mcp,
            "varningar": varn_sam["varningar"],
            "varningar_totalt": varn_sam["varningar_totalt"],
            "varningar_sammanfattning": varn_sam["varningar_sammanfattning"],
            "logg": loggradder[-50:],
        }

    # ------------------------------------------------------------------ 3. lagring och lokal diskstädning

    @utv(
        "utv_lagring_lista",
        "Superadmin-utvecklingsverktyg: listar objekt i konfigurerad objektlagring strikt under prefixen "
        "'geografi/' eller 'tmp/'. Övriga prefix (t.ex. backup/konto/projekt) och stängda kommuner nekas.",
    )
    def utv_lagring_lista(
        prefix: Annotated[
            str, Field(description="Lagringsprefix: måste börja med 'geografi/' eller 'tmp/'.")
        ] = "geografi/",
        max_antal: Annotated[int, Field(description="Högst antal objektnycklar att returnera (1-500).")] = 100,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        from gemensamt.objektlagring import ObjektLagringFel

        ren_prefix = _normalisera_lagring_prefix(prefix, _TILLATNA_LAGRING_LISTA_PREFIX)
        if ren_prefix.startswith("geografi/"):
            delar = [d for d in ren_prefix.split("/") if d]
            if len(delar) >= 2 and not _ar_oppen_kommun(delar[1]):
                raise ToolError(
                    f"Nekad (K38): kommunen '{delar[1]}' är inte öppna data och får inte listas via MCP. "
                    f"{_FEL_PREFIX}nekad_oppenhet"
                )
        lk, lagring = _bygg_lagring()
        try:
            alla = lagring.lista_prefix(ren_prefix)
        except ObjektLagringFel as exc:
            raise ToolError(f"Lagringsfel: {redigera_text(str(exc))} {_FEL_PREFIX}lagring") from exc

        synliga: list[str] = []
        dolda = 0
        for nyckel in alla:
            if nyckel.startswith("geografi/"):
                segment = [s for s in nyckel.split("/") if s]
                if len(segment) >= 2 and not _ar_oppen_kommun(segment[1]):
                    dolda += 1
                    continue
            synliga.append(redigera_text(nyckel))
        grans = max(1, min(int(max_antal), 500))
        visade = synliga[:grans]
        return {
            "typ": lk.typ,
            "prefix": ren_prefix,
            "antal_totalt": len(synliga),
            "antal_visade": len(visade),
            "trunkerad": len(synliga) > len(visade),
            "dolda_icke_oppna": dolda,
            "objekt": visade,
        }

    def _filtrera_lru_sokvagar(sokvagar: tuple[str, ...]) -> tuple[list[str], int]:
        """K38 + redigering för lokala LRU-cachesökvägar: döljer stängda kommuner och känsliga rotprefix."""
        _stangda_rotter = frozenset({"backup", "konto", "projekt", "ledningar", "intern"})
        synliga: list[str] = []
        dolda = 0
        for ra in sokvagar:
            rel = ra.replace("\\", "/").lstrip("/")
            segment = [s for s in rel.split("/") if s]
            if not segment:
                continue
            rot_del = segment[0].lower()
            if rot_del in _stangda_rotter or any(s.lower().startswith("ledning") for s in segment):
                dolda += 1
                continue
            if rot_del == "geografi" and len(segment) >= 2 and not _ar_oppen_kommun(segment[1]):
                dolda += 1
                continue
            synliga.append(redigera_text(rel))
        return synliga, dolda

    @utv(
        "utv_lagring_stada",
        "Superadmin-utvecklingsverktyg: städar lokal S3-LRU-cache eller temporära objekt under 'tmp/' i "
        "konfigurerad objektlagring. Körs alltid som torrkörning (torrkor=true) som förval.",
    )
    def utv_lagring_stada(
        torrkor: Annotated[
            bool, Field(description="Torrkörning utan att ta bort filer (förval: true).")
        ] = True,
        prefix: Annotated[
            str | None,
            Field(description="Valfritt objektlagringsprefix under 'tmp/'. Utelämnat: lokal S3-LRU-cache."),
        ] = None,
        max_gb: Annotated[float | None, Field(description="Maxstorlek i GB för lokal LRU-cache.")] = None,
        max_alder_dagar: Annotated[
            float | None, Field(description="Maxålder i dagar för lokal LRU-cache.")
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        from gemensamt.lokal_cache import LokalCacheFel, las_lokal_cache_konfig, stada_lokal_cache
        from gemensamt.objektlagring import ObjektLagringFel

        if prefix is not None:
            ren_prefix = _normalisera_lagring_prefix(prefix, _TILLATNA_LAGRING_STADA_PREFIX)
            lk, lagring = _bygg_lagring()
            try:
                matchande = lagring.lista_prefix(ren_prefix)
                if torrkor:
                    return {
                        "lage": "objektlagring",
                        "typ": lk.typ,
                        "prefix": ren_prefix,
                        "torrkor": True,
                        "antal_raderade": len(matchande),
                        "objekt": [redigera_text(k) for k in matchande[:200]],
                    }
                raderade = lagring.radera_prefix(ren_prefix)
            except ObjektLagringFel as exc:
                raise ToolError(f"Lagringsfel: {redigera_text(str(exc))} {_FEL_PREFIX}lagring") from exc
            return {
                "lage": "objektlagring",
                "typ": lk.typ,
                "prefix": ren_prefix,
                "torrkor": False,
                "antal_raderade": int(raderade),
            }

        konfig = _las_app_konfig(tjanst.app.konfig_fil)
        try:
            ck = las_lokal_cache_konfig(konfig, miljo=miljo)
            mapp = ck.mapp if ck.mapp.is_absolute() else (tjanst.rot / ck.mapp)
            res = stada_lokal_cache(
                mapp,
                max_gb if max_gb is not None else ck.max_gb,
                max_alder_dagar if max_alder_dagar is not None else ck.max_alder_dagar,
                torrkor=bool(torrkor),
            )
        except LokalCacheFel as exc:
            raise ToolError(f"Cachestädning misslyckades: {redigera_text(str(exc))} {_FEL_PREFIX}fel_indata") from exc
        synliga_filer, dolda_filer = _filtrera_lru_sokvagar(res.raderade_filer)
        return {
            "lage": "lokal_lru_cache",
            "torrkor": res.torrkor,
            "antal_raderade": res.antal_raderade,
            "antal_raderade_alder": res.antal_raderade_alder,
            "antal_raderade_lru": res.antal_raderade_lru,
            "frigjorda_mb": round(res.frigjorda_mb, 3),
            "kvarvarande_mb": round(res.kvarvarande_mb, 3),
            "dolda_icke_oppna": dolda_filer,
            "raderade_filer": synliga_filer[:200],
        }

    @utv(
        "utv_lagring_synka_k1",
        "Superadmin-utvecklingsverktyg: synkroniserar en verifierat öppen K1 v2-geografi (K46) till "
        "konfigurerad objektlagring under 'geografi/<kommun>/<rut_id>/k1/' med SHA-256-hopp och "
        "K38 fail-closed-spärr. Stängda geografier/kommuner nekas alltid. Förval: torrkor=true.",
    )
    def utv_lagring_synka_k1(
        geografi_ref: Annotated[
            str,
            Field(description="Geografireferens på formen '<id>@v<nummer>', t.ex. 'goteborg-test@v5'."),
        ],
        torrkor: Annotated[
            bool, Field(description="Torrkörning utan att ladda upp några filer (förval: true).")
        ] = True,
        max_rutor: Annotated[
            int, Field(description="Högst antal K1-rutor att synkronisera per anrop (1-256, förval: 64).")
        ] = 64,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        from gemensamt.objektlagring import ObjektLagringFel
        from geografi.modell import GeografiFel
        from geografi.s3_sync import (
            K38SyncNekad,
            S3SyncFel,
            synka_k1_till_s3,
            validera_oppen_geografi_for_s3,
        )

        if isinstance(max_rutor, bool) or int(max_rutor) < 1:
            raise ToolError(f"max_rutor måste vara mellan 1 och 256. {_FEL_PREFIX}fel_indata")
        grans = min(int(max_rutor), 256)
        geomapp = tjanst.app.projekt_skapa.geomapp
        try:
            validera_oppen_geografi_for_s3(
                geografi_ref,
                geomapp,
                oppenhet=oppenhet,
                rot=tjanst.rot,
            )
        except K38SyncNekad as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}nekad_oppenhet") from exc
        except (S3SyncFel, GeografiFel) as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}fel_indata") from exc

        lk, lagring = _bygg_lagring()
        try:
            res = synka_k1_till_s3(
                geografi_ref,
                geomapp,
                objektlagring=lagring,
                torrkor=bool(torrkor),
                max_rutor=grans,
                oppenhet=oppenhet,
                rot=tjanst.rot,
            )
        except K38SyncNekad as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}nekad_oppenhet") from exc
        except (S3SyncFel, GeografiFel) as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}fel_indata") from exc
        except ObjektLagringFel as exc:
            raise ToolError(f"Lagringsfel: {redigera_text(str(exc))} {_FEL_PREFIX}lagring") from exc

        return {
            "typ": lk.typ,
            **redigera_objekt(res.till_dict()),
        }

    @utv(
        "utv_disk_stada",
        "Superadmin-utvecklingsverktyg: städar lokal cache (temp, gamla lagerversioner, utgångna körningar "
        "och K22-cache) med atomiskt skydd av aktiva körningar, demo och projekt. Förval: torrkor=true.",
    )
    def utv_disk_stada(
        torrkor: Annotated[
            bool, Field(description="Torrkörning utan att radera några filer (förval: true).")
        ] = True,
        max_gb: Annotated[float | None, Field(description="Maxstorlek i GB för lokal cache.")] = None,
        max_alder_dagar: Annotated[float | None, Field(description="Maxålder i dagar.")] = None,
        grace_minuter: Annotated[
            float | None, Field(description="Skyddstid i minuter för nyligen ändrade filer.")
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        from gemensamt.lokal_cache import LokalCacheFel
        from gemensamt.lokal_diskstadare import (
            LokalDiskStadareFel,
            disk_utrymme_status,
            las_lokal_disk_konfig,
            stada_lokal_disk,
        )

        konfig = _las_app_konfig(tjanst.app.konfig_fil)
        cache_rot = tjanst.app.fragor.cachemapp
        projekt_rot = tjanst.app.projektmapp.parent
        demo_rot = tjanst.app.fragor.demomapp
        try:
            disk_konfig = las_lokal_disk_konfig(
                konfig,
                miljo=miljo,
                cache_rot=cache_rot,
                projekt_rot=projekt_rot,
                demo_rot=demo_rot,
                max_gb=max_gb,
                max_alder_dagar=max_alder_dagar,
                grace_minuter=grace_minuter,
            )
            disk_res = stada_lokal_disk(disk_konfig, torrkor=bool(torrkor))
            ds = disk_utrymme_status(disk_konfig.cache_rot)
        except (LokalCacheFel, LokalDiskStadareFel) as exc:
            raise ToolError(f"Diskstädning misslyckades: {redigera_text(str(exc))} {_FEL_PREFIX}fel_indata") from exc
        return {
            **disk_res.som_dict(),
            "disk": {
                "ledig_gb": round(ds["ledig_gb"], 3),
                "total_gb": round(ds["total_gb"], 3),
                "anvand_pct": round(ds["anvand_pct"], 2),
                "cache_gb": round(ds["cache_gb"], 6),
            },
        }

    # ------------------------------------------------------------------ 4. kontobackup (0 PII)

    @utv(
        "utv_konto_backup",
        "Superadmin-utvecklingsverktyg: kör online-säker SQLite-backup av kontodatabasen samt verifierar "
        "SHA-256-manifest, PRAGMA integrity_check och foreign_key_check. Returnerar 0 PII och 0 hemligheter.",
    )
    def utv_konto_backup(
        s3: Annotated[
            bool, Field(description="Ladda även upp backup till konfigurerad objektlagring (förval: false).")
        ] = False,
        behall_antal: Annotated[
            int, Field(description="Antal lokala backuper att behålla vid rotation (1-200).")
        ] = 48,
        behall_dagar: Annotated[
            int, Field(description="Maxålder i dagar för lokala backuper (0-365).")
        ] = 14,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        from konto.backup import BackupFel, skapa_backup, verifiera_backup

        reg = tjanst.app.konto.register
        if reg is None:
            raise ToolError(f"Kontodatabasen är inte aktiv. {_FEL_PREFIX}fel")
        db_sokvag = reg.db.sokvag
        malmapp = tjanst.mcp_mapp.parent / "backup" / "konto"
        lagring = None
        if s3:
            _lk, lagring = _bygg_lagring()
        antal = max(1, min(int(behall_antal), 200))
        dagar = max(0, min(int(behall_dagar), 365))
        try:
            res = skapa_backup(
                db_sokvag,
                malmapp,
                behall_antal=antal,
                behall_dagar=dagar,
                objektlagring=lagring,
            )
            ver = verifiera_backup(res.backup_fil)
        except BackupFel as exc:
            raise ToolError(f"Kontobackup misslyckades: {redigera_text(str(exc))} {_FEL_PREFIX}backup") from exc
        return {
            "ok": ver.ok,
            "filnamn": res.backup_fil.name,
            "sha256": res.sha256,
            "storlek_byte": res.storlek_byte,
            "skapad_utc": res.skapad_utc,
            "integrity_check": ver.integrity_check,
            "foreign_key_check_ok": ver.foreign_key_check_ok,
            "manifest_verifierat": ver.manifest_verifierat,
            "tabellrader": dict(ver.tabellrader),
            "raderade_lokala": list(res.raderade_lokala),
            "s3_nycklar": list(res.s3_nycklar),
        }

    # ------------------------------------------------------------------ 5. testsvit och benchmark

    @utv(
        "utv_testsvit_kor",
        "Superadmin-utvecklingsverktyg: kör Flodens deterministiska validerings-testsvit (`floden testsvit`) "
        "i en isolerad delprocess och returnerar en strukturerad sammanfattning (antal godkända, volymfel, tid).",
    )
    def utv_testsvit_kor(
        grupp: Annotated[
            str | None,
            Field(description="Valfri grupp: 'A', 'B', 'C', 'D' eller 'E'. Utelämnat: alla 18 fall."),
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        grupp_ren: str | None = None
        if grupp is not None and grupp.strip():
            grupp_ren = grupp.strip().upper()
            if grupp_ren not in _GILTIGA_TESTSVIT_GRUPPER:
                raise ToolError(
                    f"Ogiltig testsvitsgrupp '{grupp}'. Välj bland A, B, C, D, E eller utelämna. "
                    f"{_FEL_PREFIX}fel_indata"
                )
        cmd = [sys.executable, "-m", "floden.cli", "testsvit", "--rot", str(tjanst.app.testsvit_rot)]
        if grupp_ren is not None:
            cmd.extend(["--grupp", grupp_ren])
        # Skicka inte hemligheter (S3/OAuth/API-nycklar) till isolerad barnprocess.
        _hemlig_sub = ("HEMLIGHET", "SECRET", "ACCESS_KEY", "TOKEN", "PASSWORD")
        sub_env = {
            k: v
            for k, v in os.environ.items()
            if not any(ord_ in k.upper() for ord_ in _hemlig_sub)
        }
        src_dir = str((tjanst.rot / "src").resolve())
        bef_pp = sub_env.get("PYTHONPATH", "")
        sub_env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{bef_pp}" if bef_pp else src_dir
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(tjanst.rot),
                env=sub_env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"Testsviten överskred tidsgränsen på 120 s. {_FEL_PREFIX}timeout") from exc
        kortid_s = round(time.perf_counter() - t0, 3)
        fall, antal_ok, antal_totalt, max_volymfel_rel, sammanfattning = _tolka_testsvit_utdata(proc.stdout)
        return {
            "ok": proc.returncode == 0,
            "returkod": proc.returncode,
            "grupp": grupp_ren,
            "kortid_s": kortid_s,
            "antal_godkanda": antal_ok,
            "antal_totalt": antal_totalt,
            "max_volymfel_rel": max_volymfel_rel,
            "sammanfattning": sammanfattning,
            "fall": fall,
            "stderr_kort": redigera_text(proc.stderr.strip())[:500] if proc.stderr.strip() else None,
        }

    @utv(
        "utv_benchmark_kor",
        "Superadmin-utvecklingsverktyg: kör ett lättviktigt eller utvalt benchmarkfall i en isolerad "
        "barnprocess (körtid, RSS-topp och avvikelse mot facit).",
    )
    def utv_benchmark_kor(
        del_namn: Annotated[
            str, Field(description="Benchmarkdel: 'skyfall', 'dagsljus', 'luft' eller 'buller'.")
        ] = "skyfall",
        fall_id: Annotated[
            str | None,
            Field(description="Valfritt fall-id, t.ex. 'EA1', 'D1', 'vatten2d-ref-64x64' eller 'grupp-E'."),
        ] = None,
        uppvarmning: Annotated[int, Field(description="Uppvärmningskörningar (0-3, förval 0).")] = 0,
        upprepningar: Annotated[int, Field(description="Mätta upprepningar (1-5, förval 1).")] = 1,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        from benchmark.fall import FALL
        from benchmark.format import DELAR
        from benchmark.korare import kor_fall_i_barn

        if del_namn not in DELAR:
            raise ToolError(
                f"Okänd benchmarkdel '{del_namn}'. Välj bland: {', '.join(DELAR)}. {_FEL_PREFIX}fel_indata"
            )
        fall_lista = FALL.get(del_namn, ())
        tillgangliga = {f.id: f for f in fall_lista}
        if not tillgangliga:
            raise ToolError(f"Inga körbara benchmarkfall för '{del_namn}'. {_FEL_PREFIX}okand")
        valt_id = fall_id or _BENCHMARK_STANDARD_FALL.get(del_namn) or fall_lista[0].id
        if valt_id not in tillgangliga:
            raise ToolError(
                f"Okänt benchmarkfall '{valt_id}' för '{del_namn}'. Tillgängliga: "
                f"{', '.join(tillgangliga)}. {_FEL_PREFIX}fel_indata"
            )
        uppv = max(0, min(int(uppvarmning), 3))
        uppr = max(1, min(int(upprepningar), 5))
        res = kor_fall_i_barn(del_namn, valt_id, uppv, uppr)
        fall_obj = tillgangliga[valt_id]
        return {
            "del": del_namn,
            "fall_id": valt_id,
            "namn": fall_obj.namn,
            "uppvarmning": uppv,
            "upprepningar": uppr,
            "tillgangliga_fall": list(tillgangliga),
            "resultat": redigera_objekt(res),
        }

    # ------------------------------------------------------------------ 6. anonymiserad kontolista och öppna projekt

    @utv(
        "utv_konto_lista",
        "Superadmin-utvecklingsverktyg: listar registrerade konton helt utan personuppgifter "
        "(endast opakt konto_id, superadmin, sparrad, tidsstämplar och organisationsmedlemskap — aldrig e-post eller namn).",
    )
    def utv_konto_lista(
        max_antal: Annotated[int, Field(description="Högst antal konton att returnera (1-500).")] = 100,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        _krav_superadmin()
        reg = tjanst.app.konto.register
        if reg is None:
            raise ToolError(f"Kontodatabasen är inte aktiv. {_FEL_PREFIX}fel")
        grans = max(1, min(int(max_antal), 500))
        with reg.db.lasning() as c:
            totalt = int(c.execute("SELECT COUNT(*) FROM konto").fetchone()[0])
            # Selektera ALDRIG epost eller namn — enbart anonymiserade driftsfält.
            rader = c.execute(
                "SELECT id, superadmin, sparrad, skapad, senast_inloggad "
                "FROM konto ORDER BY skapad, id LIMIT ?",
                (grans,),
            ).fetchall()
            konto_ids = [str(r["id"]) for r in rader]
            medlemskap_per_konto: dict[str, list[dict[str, Any]]] = {kid: [] for kid in konto_ids}
            if konto_ids:
                platser = ",".join("?" for _ in konto_ids)
                m_rader = c.execute(
                    f"SELECT konto_id, organisation_id, forvaltning_id, roll, kalla, skapad "
                    f"FROM medlemskap WHERE konto_id IN ({platser}) "
                    "ORDER BY organisation_id, forvaltning_id",
                    konto_ids,
                ).fetchall()
                for m in m_rader:
                    medlemskap_per_konto[str(m["konto_id"])].append(
                        {
                            "organisation_id": str(m["organisation_id"]),
                            "forvaltning_id": str(m["forvaltning_id"]) if m["forvaltning_id"] is not None else None,
                            "roll": str(m["roll"]),
                            "kalla": str(m["kalla"]),
                            "skapad": int(m["skapad"]),
                        }
                    )
        konton = [
            {
                "konto_id": str(r["id"]),
                "superadmin": bool(r["superadmin"]),
                "sparrad": r["sparrad"] is not None,
                "skapad": int(r["skapad"]),
                "senast_inloggad": int(r["senast_inloggad"]) if r["senast_inloggad"] is not None else None,
                "organisationer": medlemskap_per_konto.get(str(r["id"]), []),
            }
            for r in rader
        ]
        return {
            "antal_totalt": totalt,
            "antal_visade": len(konton),
            "trunkerad": totalt > len(konton),
            "konton": konton,
        }

    @utv(
        "utv_projekt_roll_satt",
        "Superadmin-utvecklingsverktyg: tilldelar eller uppdaterar rollen 'lasare' eller 'utredare' "
        "för ett opakt konto_id i ett verifierat öppet icke-demoprojekt (K38). Returnerar 0 PII.",
    )
    def utv_projekt_roll_satt(
        projekt_id: Annotated[str, Field(description="Projektets register-id (32 hex-tecken; endast öppna projekt).")],
        konto_id: Annotated[str, Field(description="Målkontots opaka konto-id (aldrig e-postadress).")],
        projekt_roll: Annotated[str, Field(description="Projektroll: 'lasare' eller 'utredare'.")],
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        utforare = _krav_superadmin()
        from konto.register import BehorighetFel, RegisterFel

        if projekt_roll not in ("lasare", "utredare"):
            raise ToolError(
                f"Ogiltig projekt_roll '{projekt_roll}': ange 'lasare' eller 'utredare'. {_FEL_PREFIX}fel_indata"
            )
        _krav_oppet_projekt(projekt_id)
        if not _REGISTER_ID_RE.match(projekt_id):
            raise ToolError(f"Ogiltigt projekt_id '{projekt_id}'. {_FEL_PREFIX}fel_indata")
        reg = tjanst.app.konto.register
        if reg is None:
            raise ToolError(f"Kontodatabasen är inte aktiv. {_FEL_PREFIX}fel")
        if reg.konto(konto_id) is None:
            raise ToolError(f"Okänt konto_id '{konto_id}'. {_FEL_PREFIX}okand")
        try:
            reg.ge_projektroll(utforare, konto_id, projekt_id, projekt_roll)
        except (BehorighetFel, RegisterFel) as exc:
            raise ToolError(f"{redigera_text(str(exc))} {_FEL_PREFIX}behorighet") from exc
        return {
            "status": "uppdaterad",
            "projekt_id": projekt_id,
            "konto_id": konto_id,
            "projekt_roll": projekt_roll,
        }

    @utv(
        "utv_projekt_radera",
        "Superadmin-utvecklingsverktyg: raderar ett verifierat öppet icke-demoprojekt permanent (med "
        "exakt namnbekräftelse). Demokvarteret och icke-öppna projekt nekas alltid (K38).",
    )
    def utv_projekt_radera(
        projekt_id: Annotated[str, Field(description="Projektets register-id (endast öppna icke-demoprojekt).")],
        bekrafta_namn: Annotated[str, Field(description="Projektets exakta namn som säkerhetsbekräftelse.")],
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        utforare = _krav_superadmin()
        from webb.fel import FlodenWebbFel

        if projekt_id in (PROJEKT_ID, tjanst.app.konto.konfig.demoprojekt):
            raise ToolError(f"Nekad: demokvarteret får inte raderas. {_FEL_PREFIX}demo_skyddat")
        _krav_oppet_projekt(projekt_id)
        try:
            svar = tjanst.app.projekt_installningar.radera(
                utforare, projekt_id, {"bekrafta_namn": bekrafta_namn}
            )
        except FlodenWebbFel as exc:
            raise ToolError(
                f"{redigera_text(str(exc))} {_FEL_PREFIX}projekt_radera_{exc.status_kod}"
            ) from exc
        return dict(svar)

    return tuple(registrerade)


__all__ = [
    "UTVECKLINGSLAGE_ENV",
    "UTV_PREFIX",
    "redigera_objekt",
    "redigera_text",
    "registrera_utvecklingsverktyg",
    "sammanfatta_mappningsvarningar",
    "utvecklingslage_aktivt",
]
