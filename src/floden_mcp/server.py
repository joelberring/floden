# ruff: noqa: E501
"""Flodens MCP-server: verktyg, resurser och promptar ovanpå `FlodenTjanst`.

Principer (se `docs/MCP.md`):

1. **AI:n äger inget tal.** Varje tal i ett svar kommer ur en körnings filer och bär ett belägg.
2. **En sanning.** Verktygen anropar samma kärn-API som webben via `FlodenTjanst`; ingen logik för
   vatten, dagsljus eller kartor finns här.
3. **Allt loggas.** En middleware skriver en rad per anrop i körloggen (`floden_mcp.korlogg`) och
   tillämpar datapolicyn (`floden_mcp.dataklass`) före anropet.
4. **Dataklass på varje svar.** `dataklass` står först i varje verktygssvar.

Verktygen finns i en tabell längst ned (`VERKTYG`); klassen för varje verktyg är `oppet` i R0.
"""

import base64
import contextlib
import functools
import json
import os
import re
import time
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Annotated, Any

from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, CallToolResult, ImageContent, TextContent
from pydantic import Field

from floden_mcp import MCP_VERSION
from floden_mcp.dataklass import DataKlass, Policy, PolicyNekad, hogsta
from floden_mcp.karna_api import PROJEKT_ID, FlodenFel, FlodenTjanst
from floden_mcp.korlogg import Korlogg, KorloggFel, hash_av, nu, rensa_text
from floden_mcp.oppenhet import Oppenhet, OppenhetNekad
from rapport.bestallning import (
    BESTALLNINGSAMNEN,
    MOTTAGARROLLER,
    SKEDEN,
    sammanfattning_bestallning,
)
from rapport.fragekatalog import FragekatalogFel, fraga_med_formaga
from rapport.pastaende import PastaendeFel
from rapport.skissexport import ExportStoppad
from rapport.skissrapport import RapportFel, RapportTjanst, sammanfattning

Modell = Annotated[
    str | None,
    Field(
        description="Valfritt: vilken AI-modell som styr anropet (t.ex. 'claude-opus-5'). "
        "Loggas i körloggen; påverkar inget resultat."
    ),
]
Roll = Annotated[
    str | None,
    Field(
        description="Valfritt: modellens roll i arbetet (t.ex. 'utforskare', 'skribent'). "
        "Loggas i körloggen; påverkar inget resultat."
    ),
]
Projekt = Annotated[
    str,
    Field(
        description="Projekt-id: 'demokvarteret' eller id:t för ett projekt du har en roll i (projekt_lista). "
        "Projekt vars underlag inte är öppna data lämnas inte ut."
    ),
]
KorningId = Annotated[str, Field(description="Körnings-id från kor, korningar_lista eller vanta.")]
Polygon = Annotated[
    list[list[float]],
    Field(description="Polygonens hörn som [[e, n], ...] i SWEREF 99 18 00 (EPSG:3011), meter."),
]

INSTRUKTIONER = """\
Floden är en öppen modell som räknar skyfall (vatten) och dagsljus på gatunivå, före och efter ett
planförslag. Du styr den med verktyg; Floden räknar.

Regler: (1) Du äger aldrig ett tal. Skriv bara tal som står i ett verktygssvar, oförändrade och med
enhet, och ange beläggen (korning_id, fil, mottagare/cell, matt). Räkna inte om, avrunda inte i tysthet,
gissa inte. Saknas ett tal: kör eller fråga igen. (2) Allt i demokvarteret är påhittat. (3) Metoderna är
skisser (se metoder_lista); skriv det när du redovisar resultat. (4) Varje svar har en dataklass; bara
'oppet' lämnas ut som standard.

Arbetsgång: projekt_info och geometri_sammanfattning för att förstå området; byggnader_lista för
byggnads-id; scenario_skapa för ett ritat hus eller en ändring; korplan för att se vad som räknas;
kor och vanta; resultat_sammanfattning, resultat_varde, jamfor och karta_bild för att läsa resultatet.
Rapporter (R1, R2): bestallning_skapa (syfte, skede, mottagare, ämnen; valfritt), fragekatalog_sok,
rapport_skapa (skyfall, dagsljus/sol, ekologi eller luft; med bestallning_id styr beställningen frågorna och
ingressen), rapport_installningar, pastaende_lagg_till (tal bara som platshållare
{{matt:<adress>}}), rapport_kontrollera, rapport_exportera (word/pdf), rapport_hamta.
Koordinater är EPSG:3011 (SWEREF 99 18 00) i meter. Du kan ange modell och roll i anropen eller
med session_deklarera; det loggas i körloggen (korlogg_hamta).
"""

_FEL_PREFIX = "[floden]"


def _till_dict(r: Any) -> dict[str, Any]:
    if isinstance(r, Mapping):
        return dict(r)
    dump = getattr(r, "model_dump", None)
    return dict(dump(by_alias=True, exclude_none=False)) if callable(dump) else {}


def _hitta_rapporter(obj: Any, ut: set[str], djup: int = 0) -> None:
    """Samlar rapport-id (nyckeln `rapport_id`) ur argument eller svar."""
    if djup > 4:
        return
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k == "rapport_id" and isinstance(v, str):
                ut.add(v)
            elif isinstance(v, Mapping | list):
                _hitta_rapporter(v, ut, djup + 1)
    elif isinstance(obj, list):
        for x in obj[:200]:
            _hitta_rapporter(x, ut, djup + 1)


def _hitta_korningar(obj: Any, ut: set[str], djup: int = 0) -> None:
    """Samlar körnings-id (nycklarna `korning_id` och `korning_ids`) ur ett svar."""
    if djup > 8:
        return
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k == "korning_id" and isinstance(v, str):
                ut.add(v)
            elif k == "korning_ids" and isinstance(v, list):
                ut.update(x for x in v if isinstance(x, str))
            else:
                _hitta_korningar(v, ut, djup + 1)
    elif isinstance(obj, list):
        for x in obj[:500]:
            _hitta_korningar(x, ut, djup + 1)


def _korningar_i_indata(obj: Any, ut: set[str], djup: int = 0) -> None:
    """Körnings-id i verktygets indata: `korning_id`, `korning_ids` och `korningar`."""
    if djup > 8:
        return
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k == "korning_id" and isinstance(v, str):
                ut.add(v)
            elif k in ("korning_ids", "korningar") and isinstance(v, list):
                ut.update(x for x in v if isinstance(x, str))
            elif isinstance(v, Mapping | list):
                _korningar_i_indata(v, ut, djup + 1)
    elif isinstance(obj, list):
        for x in obj[:500]:
            _korningar_i_indata(x, ut, djup + 1)


class _SessionData:
    __slots__ = ("session_id", "modell", "roll")

    def __init__(self, session_id: str, modell: str | None, roll: str | None) -> None:
        self.session_id = session_id
        self.modell = modell
        self.roll = roll


class Sessionstillstand:
    """Deklarerad modell och roll, isolerad per MCP-session (stdio eller HTTP)."""

    _MAX_HTTP_SESSIONER = 1000

    def __init__(self, miljo: Mapping[str, str] | None = None) -> None:
        miljo = os.environ if miljo is None else miljo
        self._standard_modell = rensa_text(miljo.get("FLODEN_MCP_MODELL"))
        self._standard_roll = rensa_text(miljo.get("FLODEN_MCP_ROLL"))
        self._standard = _SessionData(
            f"s-{uuid.uuid4().hex[:12]}", self._standard_modell, self._standard_roll
        )
        self._sessioner: dict[str, _SessionData] = {}
        self._aktiv: ContextVar[tuple[_SessionData, str | None] | None] = ContextVar(
            f"floden_mcp_session_{id(self)}", default=None
        )

    def _for_sid(self, sid: str | None) -> _SessionData:
        if not sid:
            return self._standard
        data = self._sessioner.get(sid)
        if data is None:
            if len(self._sessioner) >= self._MAX_HTTP_SESSIONER:
                self._sessioner.pop(next(iter(self._sessioner)))
            data = _SessionData(sid, self._standard_modell, self._standard_roll)
            self._sessioner[sid] = data
        return data

    def aktivera(
        self, sid: str | None, konto: str | None
    ) -> Token[tuple[_SessionData, str | None] | None]:
        return self._aktiv.set((self._for_sid(sid), konto))

    def aterstall(self, token: Token[tuple[_SessionData, str | None] | None]) -> None:
        self._aktiv.reset(token)

    def _nu(self) -> _SessionData:
        aktiv = self._aktiv.get()
        return self._standard if aktiv is None else aktiv[0]

    @property
    def konto(self) -> str | None:
        aktiv = self._aktiv.get()
        return None if aktiv is None else aktiv[1]

    @property
    def session_id(self) -> str:
        return self._nu().session_id

    @property
    def modell(self) -> str | None:
        return self._nu().modell

    @modell.setter
    def modell(self, varde: str | None) -> None:
        self._nu().modell = varde

    @property
    def roll(self) -> str | None:
        return self._nu().roll

    @roll.setter
    def roll(self, varde: str | None) -> None:
        self._nu().roll = varde


UTAN_ROLLKRAV = frozenset(
    {"session_deklarera", "korlogg_hamta", "metoder_lista", "projekt_lista", "fragekatalog_sok", "projekt_skapa"}
)
"""Verktyg som inte rör ett projekts data: de kräver ingen projektroll (projekt_lista visar bara
kontots egna projekt, korlogg_hamta bara kontots egna rader)."""

SKRIVVERKTYG = frozenset(
    {
        "scenario_skapa",
        "fraga_skapa",
        "kor",
        "bestallning_skapa",
        "rapport_skapa",
        "rapport_installningar",
        "pastaende_lagg_till",
        "rapport_kontrollera",
        "rapport_exportera",
    }
)
"""Verktyg som skriver (scenarier, frågor, körningar, rapporter, kontroll, export): rollen utredare krävs.
Alla andra verktyg läser och kräver minst lasare. Verktyg utan `projekt`-argument (körningar,
resultat, rapporter) gäller i dag bara demokvarteret och kräver rollen där."""

Behorighet = Callable[[str, str, str], bool]
"""(konto-id, projekt-id, minsta roll) -> har kontot rollen? Ges bara när inloggning är på."""


def _result_fel(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _neka_svar(method: str, text: str) -> CallToolResult:
    if method == "resources/read":
        raise MCPError(code=INVALID_PARAMS, message=text)
    return _result_fel(text)


def bygg_server(
    tjanst: FlodenTjanst,
    *,
    policy: Policy | None = None,
    logg: Korlogg | None = None,
    miljo: Mapping[str, str] | None = None,
    tillatna_resursrotter: Sequence[str | Path] | None = None,
    behorighet: Behorighet | None = None,
    oppenhet: Oppenhet | None = None,
) -> MCPServer:
    """Skapar MCP-servern. `policy` standard: bara `oppet`; `logg` standard: data/lokal/mcp.

    `behorighet` ges när inloggning är på: då kräver varje projektverktyg en roll i projektet
    (lasare, eller utredare för `SKRIVVERKTYG`) och anrop utan konto nekas. `oppenhet` är spärren för
    data som inte är öppna (`floden_mcp.oppenhet`); den är alltid på, standard är `Oppenhet.for_tjanst`."""
    policy = policy or Policy()
    oppenhet = oppenhet or Oppenhet.for_tjanst(tjanst)
    tjanst.oppenhet = oppenhet
    logg = logg or Korlogg(tjanst.mcp_mapp / "korlogg.jsonl")
    tillstand = Sessionstillstand(miljo)
    if tillatna_resursrotter is None:
        env = os.environ if miljo is None else miljo
        ra_rotter = env.get("FLODEN_MCP_RESURSROTTER", "").strip()
        if ra_rotter:
            tillatna_resursrotter = [p for p in ra_rotter.split(os.pathsep) if p.strip()]
    rapporter = RapportTjanst(
        tjanst,
        policy=policy,
        korlogg=logg,
        tillatna_resursrotter=tillatna_resursrotter,
    )
    server = MCPServer(
        "floden",
        title="Floden",
        description="Skyfall och dagsljus på gatunivå, styrd av AI; Floden räknar.",
        instructions=INSTRUKTIONER,
        version=MCP_VERSION,
    )
    # Verktygets dataklass (alla öppna i R0) och om det gäller ett projekt.
    klasser: dict[str, DataKlass] = {}
    utan_projekt = {"session_deklarera", "korlogg_hamta", "metoder_lista", "projekt_skapa"}
    explicit_projekt_var: ContextVar[bool] = ContextVar(
        f"floden_mcp_explicit_projekt_{id(server)}", default=False
    )

    def _har_roll(konto_id: str, projekt_id: str, minst: str) -> bool:
        if behorighet is not None:
            return bool(behorighet(konto_id, projekt_id, minst))
        return bool(tjanst.app.konto.har_roll(konto_id, projekt_id, minst))

    def _las_rapport(rid: str) -> Any | None:
        """Läser en rapport ur arkivet (`rapporter.arkiv.las(rid)`), med reserv till `rapporter.hamta`."""
        try:
            return rapporter.arkiv.las(rid)
        except Exception:  # noqa: BLE001 - reserv om tester stubbat `RapportTjanst.hamta`
            with contextlib.suppress(Exception):
                return rapporter.hamta(rid)
        return None

    def _las_bestallning(bid: str) -> Any | None:
        """Läser en beställning ur arkivet (`rapporter.bestallningar.las`), med reserv till `rapporter.bestallning_hamta`."""
        try:
            return rapporter.bestallningar.las(bid)
        except Exception:  # noqa: BLE001 - reserv om tester stubbat `RapportTjanst.bestallning_hamta`
            with contextlib.suppress(Exception):
                return rapporter.bestallning_hamta(bid)
        return None

    def _korningar_i_rapport(r: Any) -> set[str]:
        ut: set[str] = set()
        for post in getattr(r, "korningar", ()) or ():
            if isinstance(post, Mapping) and isinstance(post.get("korning_id"), str):
                ut.add(post["korning_id"])
            elif isinstance(post, str):
                ut.add(post)
        for karta in getattr(r, "kartor", ()) or ():
            kid = getattr(karta, "korning_id", None)
            if isinstance(kid, str):
                ut.add(kid)
        for p in getattr(r, "pastaenden", ()) or ():
            for b in getattr(p, "belagg", ()) or ():
                kid = getattr(b, "korning_id", None)
                if isinstance(kid, str):
                    ut.add(kid)
        return ut

    def _rapport_ids_for(argument: Mapping[str, Any], vilket: str) -> set[str]:
        rapport_ids: set[str] = set()
        _hitta_rapporter(argument, rapport_ids)
        if vilket.startswith("resurs:floden://rapport/"):
            rapport_ids.add(urllib.parse.unquote(vilket.rsplit("/", 1)[1]))
        return rapport_ids

    def _kontrollera_korning_oppenhet(kid: str) -> None:
        oppenhet.kontrollera_korning(kid)
        p_k = tjanst.app.projekt_for_korning(kid)
        if p_k is not None:
            pm_k = tjanst.projektmapp(p_k)
            if pm_k is not None:
                oppenhet.kontrollera_sokvag(pm_k)

    def _kontrollera_rapport_oppenhet(rid: str, korningar_out: set[str]) -> None:
        r = _las_rapport(rid)
        if r is None:
            return
        pmapp = tjanst.projektmapp(str(r.projekt))
        if pmapp is not None:
            oppenhet.kontrollera_sokvag(pmapp)
        kids = _korningar_i_rapport(r)
        korningar_out.update(kids)
        for kid in sorted(kids):
            _kontrollera_korning_oppenhet(kid)

    def _samla_fran_svar(
        obj: Any,
        korningar_out: set[str],
        rapporter_out: set[str],
        projekt_out: set[str],
        djup: int = 0,
    ) -> None:
        if djup > 8:
            return
        if isinstance(obj, Mapping):
            for k, v in obj.items():
                if k == "korning_id" and isinstance(v, str):
                    korningar_out.add(v)
                elif k == "korning_ids" and isinstance(v, list):
                    korningar_out.update(x for x in v if isinstance(x, str))
                elif k == "korningar" and isinstance(v, list):
                    for item in v[:500]:
                        if isinstance(item, str):
                            korningar_out.add(item)
                        elif isinstance(item, Mapping):
                            _samla_fran_svar(item, korningar_out, rapporter_out, projekt_out, djup + 1)
                elif k == "rapport_id" and isinstance(v, str):
                    rapporter_out.add(v)
                elif k == "projekt" and isinstance(v, str) and v:
                    projekt_out.add(v)
                elif isinstance(v, Mapping | list):
                    _samla_fran_svar(v, korningar_out, rapporter_out, projekt_out, djup + 1)
        elif isinstance(obj, list):
            for x in obj[:500]:
                _samla_fran_svar(x, korningar_out, rapporter_out, projekt_out, djup + 1)

    def _kontrollera_svar_oppenhet(
        d: Mapping[str, Any], korningar_out: set[str], rapporter_out: set[str]
    ) -> None:
        sc = d.get("structuredContent") or d.get("structured_content")
        projekt_i_svar: set[str] = set()
        _samla_fran_svar(sc, korningar_out, rapporter_out, projekt_i_svar)
        for falt_namn in ("contents", "content"):
            for post in d.get(falt_namn) or []:
                text = post.get("text") if isinstance(post, Mapping) else getattr(post, "text", None)
                if not isinstance(text, str) or not text.lstrip().startswith(("{", "[")):
                    continue
                try:
                    tolkad = json.loads(text)
                except ValueError:
                    continue
                _samla_fran_svar(tolkad, korningar_out, rapporter_out, projekt_i_svar)
        for pid in sorted(projekt_i_svar):
            pmapp = tjanst.projektmapp(pid)
            if pmapp is not None:
                oppenhet.kontrollera_sokvag(pmapp)
        for rid in sorted(rapporter_out):
            _kontrollera_rapport_oppenhet(rid, korningar_out)
        for kid in sorted(korningar_out):
            _kontrollera_korning_oppenhet(kid)

    def _objekt_nekas(argument: Mapping[str, Any], vilket: str, konto: str | None, minst: str) -> str | None:
        """Text om verktyget/resursen pekar på en rapport eller beställning i ett projekt som kontot inte har
        rollen i (annars None). Samma text som för en som saknas."""
        if behorighet is None and not tjanst.app.konto.aktiv:
            return None
        rapport_ids = _rapport_ids_for(argument, vilket)
        kontrollera: list[tuple[str, str, str]] = []
        for rid in sorted(rapport_ids):
            r = _las_rapport(rid)
            if r is not None:
                kontrollera.append((rid, "rapport", str(r.projekt)))
        bid = argument.get("bestallning_id")
        if isinstance(bid, str) and bid:
            b_obj = _las_bestallning(bid)
            if b_obj is not None:
                kontrollera.append((bid, "beställning", str(b_obj.projekt)))
        for id_, slag, projekt_id in kontrollera:
            if konto is None or not _har_roll(konto, projekt_id, minst):
                return f"Okänd {slag} '{id_}'."
        return None


    def klass_for_projekt(klass: DataKlass, projekt: str | None) -> DataKlass:
        """Anropets dataklass. Ett projekt som policyn klassat (`[mcp.projektklasser]`) följer den. Annars: ett projekt i
        en geografi är `oppet` om dess underlag är öppna data och `internt` om inte (K38); övriga okända projekt är
        `internt` som förut."""
        if projekt is not None and projekt not in policy.projektklasser:
            oppet = tjanst.projekt_oppen(projekt)
            if oppet is not None:
                return hogsta(klass, DataKlass.OPPET if oppet else DataKlass.INTERNT)
        return policy.klass_for(klass, projekt)

    def verktyg(
        namn: str, beskrivning: str, *, klass: DataKlass = DataKlass.OPPET
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Registrerar ett verktyg: FlodenFel blir ett klartextfel, och svaret får `dataklass`."""
        klasser[namn] = klass

        def deko(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            def inre(*args: Any, **kwargs: Any) -> Any:
                try:
                    svar = fn(*args, **kwargs)
                except FlodenFel as exc:
                    raise ToolError(f"{exc} {_FEL_PREFIX}{exc.kod}") from exc
                except (RapportFel, PastaendeFel, FragekatalogFel) as exc:
                    raise ToolError(f"{exc} {_FEL_PREFIX}vagrad") from exc
                except ExportStoppad as exc:
                    raise ToolError(f"{exc} {_FEL_PREFIX}export_stoppad") from exc
                if isinstance(svar, dict):
                    if namn in utan_projekt:
                        projekt_eff: str | None = None
                    elif (
                        namn in ("korplan", "kor")
                        and kwargs.get("fraga")
                        and not explicit_projekt_var.get()
                    ):
                        projekt_eff = tjanst.fraga_projekt(str(kwargs["fraga"])) or PROJEKT_ID
                    elif namn == "bestallning_hamta" and kwargs.get("bestallning_id"):
                        b_obj = _las_bestallning(str(kwargs["bestallning_id"]))
                        projekt_eff = str(b_obj.projekt) if b_obj is not None else PROJEKT_ID
                    elif explicit_projekt_var.get() and "projekt" in kwargs:
                        projekt_eff = str(kwargs["projekt"])
                    elif kwargs.get("korning_id"):
                        p_k = tjanst.app.projekt_for_korning(str(kwargs["korning_id"]))
                        if p_k is not None and (
                            namn == "karta_bild"
                            or p_k in policy.projektklasser
                            or tjanst.projekt_oppen(p_k) is not None
                        ):
                            projekt_eff = p_k
                        else:
                            projekt_eff = PROJEKT_ID
                    elif kwargs.get("rapport_id"):
                        r_obj = _las_rapport(str(kwargs["rapport_id"]))
                        projekt_eff = str(r_obj.projekt) if r_obj is not None else PROJEKT_ID
                    elif kwargs.get("bestallning_id"):
                        b_obj = _las_bestallning(str(kwargs["bestallning_id"]))
                        projekt_eff = str(b_obj.projekt) if b_obj is not None else PROJEKT_ID
                    elif "projekt" in kwargs:
                        projekt_eff = str(kwargs["projekt"])
                    else:
                        projekt_eff = PROJEKT_ID
                    return {"dataklass": klass_for_projekt(klass, projekt_eff).value, **svar}
                return svar

            server.tool(name=namn, description=beskrivning)(inre)
            return fn

        return deko

    # ------------------------------------------------------------------ middleware
    def _klient(ctx: ServerRequestContext[Any, Any]) -> dict[str, str | None]:
        params = getattr(ctx.session, "client_params", None)
        info = getattr(params, "client_info", None)
        if info is None:
            meta = ctx.params.get("_meta") if ctx.params else None
            raw = (meta or {}).get("io.modelcontextprotocol/clientInfo") if meta else None
            if isinstance(raw, Mapping):
                return {
                    "namn": rensa_text(raw.get("name")) or "okand",
                    "version": rensa_text(raw.get("version")),
                }
            return {"namn": "okand", "version": None}
        return {"namn": rensa_text(info.name) or "okand", "version": rensa_text(info.version)}

    def _http(ctx: ServerRequestContext[Any, Any]) -> tuple[str | None, str | None, str | None]:
        """(Mcp-Session-Id, konto-id, token-id) när anropet kom över HTTP."""
        req = ctx.request
        if req is None:
            return None, None, None
        sid = getattr(req, "headers", {}).get("mcp-session-id")
        scope = getattr(req, "scope", {})
        return (
            rensa_text(sid),
            rensa_text(scope.get("floden_konto")),
            rensa_text(scope.get("floden_token")),
        )

    LOGGADE = {"tools/call", "resources/read", "prompts/get"}

    async def mellanvara(
        ctx: ServerRequestContext[Any, Any],
        call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[Any]],
    ) -> Any:
        if ctx.method not in LOGGADE:
            return await call_next(ctx)
        t0 = time.monotonic()
        params = ctx.params or {}
        argument = dict(params.get("arguments") or {})
        if ctx.method == "tools/call":
            vilket = str(params.get("name"))
        elif ctx.method == "resources/read":
            vilket = "resurs:" + str(params.get("uri"))
        else:
            vilket = "prompt:" + str(params.get("name"))
        klient = _klient(ctx)
        sid, konto, token_id = _http(ctx)
        token = tillstand.aktivera(sid, konto)
        exp_token = explicit_projekt_var.set("projekt" in argument)
        try:
            modell = rensa_text(argument.pop("modell", None)) or tillstand.modell
            roll = rensa_text(argument.pop("roll", None)) or tillstand.roll
            projekt = argument.get("projekt", PROJEKT_ID)
            klass = klasser.get(vilket, DataKlass.OPPET)
            korningar: set[str] = set()
            _korningar_i_indata(argument, korningar)
            rapporter_set = _rapport_ids_for(argument, vilket)
            bestallning_ref = (
                str(argument["bestallning_id"])
                if isinstance(argument.get("bestallning_id"), str) and argument["bestallning_id"]
                else None
            )
            bestallning_obj = _las_bestallning(bestallning_ref) if bestallning_ref is not None else None
            bestallning_projekt = (
                str(bestallning_obj.projekt) if bestallning_obj is not None else None
            )
            fraga_ref = (
                str(argument["fraga"])
                if ctx.method == "tools/call"
                and vilket in ("korplan", "kor")
                and isinstance(argument.get("fraga"), str)
                and argument["fraga"]
                else None
            )
            fraga_fil = tjanst.fragefil_eller_none(fraga_ref) if fraga_ref is not None else None
            fraga_agarprojekt = (
                tjanst.fraga_projekt(fraga_ref, fraga_fil) if fraga_ref is not None else None
            )
            if ctx.method == "resources/read" and vilket.startswith("resurs:floden://rapport/"):
                rid_res = urllib.parse.unquote(vilket.rsplit("/", 1)[1])
                r_res = _las_rapport(rid_res)
                effektiv = (
                    klass_for_projekt(DataKlass.OPPET, str(r_res.projekt))
                    if r_res is not None
                    else DataKlass.OPPET
                )
            elif ctx.method != "tools/call" or vilket in utan_projekt:
                effektiv = klass
            else:
                if fraga_ref is not None and fraga_agarprojekt is not None:
                    effektiv_projekt = fraga_agarprojekt
                elif vilket == "bestallning_hamta" and bestallning_projekt is not None:
                    effektiv_projekt = bestallning_projekt
                elif "projekt" not in argument and rapporter_set:
                    rapport_klasser = [
                        klass_for_projekt(klass, str(r_obj.projekt))
                        for rid in sorted(rapporter_set)
                        if (r_obj := _las_rapport(rid)) is not None
                    ]
                    effektiv = hogsta(*rapport_klasser) if rapport_klasser else klass_for_projekt(klass, str(projekt))
                    effektiv_projekt = ""
                elif "projekt" not in argument and bestallning_projekt is not None:
                    effektiv_projekt = bestallning_projekt
                elif "projekt" not in argument and isinstance(argument.get("korning_id"), str):
                    p_k = tjanst.app.projekt_for_korning(str(argument["korning_id"]))
                    if p_k is not None and (
                        vilket == "karta_bild"
                        or p_k in policy.projektklasser
                        or tjanst.projekt_oppen(p_k) is not None
                    ):
                        effektiv_projekt = p_k
                    else:
                        effektiv_projekt = str(projekt)
                else:
                    effektiv_projekt = str(projekt)
                if effektiv_projekt:
                    effektiv = klass_for_projekt(klass, effektiv_projekt)
                if bestallning_projekt is not None and effektiv_projekt != bestallning_projekt:
                    effektiv = hogsta(effektiv, klass_for_projekt(klass, bestallning_projekt))
            post: dict[str, Any] = {
                "tid": nu(),
                "session": tillstand.session_id,
                "klient": klient,
                "protokoll": ctx.protocol_version,
                "modell": modell,
                "roll": roll,
                "konto": konto,
                "token": token_id,
                "verktyg": vilket,
                "indata_hash": hash_av(argument),
                "indata_nycklar": sorted(argument),
                "dataklass": effektiv.value,
                "rapport_id": rapporter_set,
            }
            if behorighet is not None and ctx.method == "tools/call" and vilket not in UTAN_ROLLKRAV:
                minst = "utredare" if vilket in SKRIVVERKTYG else "lasare"
                if konto is None:
                    text = (
                        "Inloggning krävs: den här servern kräver ett konto. Anslut över HTTP med "
                        "en personlig MCP-token eller via Claude-appens koppling (docs/KONTO.md)."
                    )
                    _skriv(logg, post, "nekad_behorighet", korningar, "inget konto", None, t0)
                    return _result_fel(text)
                if (
                    fraga_ref is None
                    and not (rapporter_set and "projekt" not in argument)
                    and not (
                        vilket == "bestallning_hamta"
                        and bestallning_ref is not None
                        and "projekt" not in argument
                    )
                    and not behorighet(konto, str(projekt), minst)
                ):
                    text = (
                        f"Nekad: kontot saknar rollen {minst} i projektet. Verktyget gäller "
                        "bara projekt där du har en roll."
                    )
                    _skriv(logg, post, "nekad_behorighet", korningar, f"saknar {minst}", None, t0)
                    return _result_fel(text)
            if fraga_ref is not None and (behorighet is not None or tjanst.app.konto.aktiv):
                minst = "utredare" if vilket == "kor" else "lasare"
                if konto is None:
                    text = (
                        "Inloggning krävs: den här servern kräver ett konto. Anslut över HTTP med "
                        "en personlig MCP-token eller via Claude-appens koppling (docs/KONTO.md)."
                    )
                    _skriv(logg, post, "nekad_behorighet", korningar, "inget konto", None, t0)
                    return _result_fel(text)
                if fraga_fil is None and fraga_agarprojekt is None:
                    if "projekt" in argument and not _har_roll(konto, str(argument["projekt"]), minst):
                        text = (
                            f"Nekad: kontot saknar rollen {minst} i projektet. Verktyget gäller "
                            "bara projekt där du har en roll."
                        )
                        _skriv(logg, post, "nekad_behorighet", korningar, f"saknar {minst}", None, t0)
                        return _result_fel(text)
                    text = f"Okänd fråga '{fraga_ref}'."
                    _skriv(logg, post, "nekad_behorighet", korningar, "okänd fråga", None, t0)
                    return _result_fel(text)
                if fraga_agarprojekt is None:
                    if not tjanst.app.konto.ar_superadmin(konto):
                        text = f"Okänd fråga '{fraga_ref}'."
                        _skriv(logg, post, "nekad_behorighet", korningar, "fråga utanför projekt", None, t0)
                        return _result_fel(text)
                    if "projekt" in argument and not _har_roll(konto, str(argument["projekt"]), minst):
                        text = (
                            f"Nekad: kontot saknar rollen {minst} i projektet. Verktyget gäller "
                            "bara projekt där du har en roll."
                        )
                        _skriv(logg, post, "nekad_behorighet", korningar, f"saknar {minst}", None, t0)
                        return _result_fel(text)
                else:
                    if not _har_roll(konto, fraga_agarprojekt, minst):
                        if (
                            "projekt" in argument
                            and str(argument["projekt"]) == fraga_agarprojekt
                            and _har_roll(konto, fraga_agarprojekt, "lasare")
                        ):
                            text = (
                                f"Nekad: kontot saknar rollen {minst} i projektet. Verktyget gäller "
                                "bara projekt där du har en roll."
                            )
                        else:
                            text = f"Okänd fråga '{fraga_ref}'."
                        _skriv(logg, post, "nekad_behorighet", korningar, "fråga i annat projekt", None, t0)
                        return _result_fel(text)
                    if (vilket == "kor" and str(projekt) != fraga_agarprojekt) or (
                        vilket == "korplan"
                        and "projekt" in argument
                        and str(argument["projekt"]) != fraga_agarprojekt
                    ):
                        text = (
                            f"Frågan '{fraga_ref}' hör till projektet '{fraga_agarprojekt}', "
                            f"inte '{projekt}'."
                        )
                        _skriv(logg, post, "nekad_behorighet", korningar, "fel ägarprojekt för fråga", None, t0)
                        return _result_fel(text)
            if ctx.method in ("tools/call", "resources/read") and (
                behorighet is not None or tjanst.app.konto.aktiv
            ):
                if (
                    ctx.method == "resources/read"
                    and vilket.startswith("resurs:floden://rapport/")
                    and konto is None
                ):
                    text = (
                        "Inloggning krävs: den här servern kräver ett konto. Anslut över HTTP med "
                        "en personlig MCP-token eller via Claude-appens koppling (docs/KONTO.md)."
                    )
                    _skriv(logg, post, "nekad_behorighet", korningar, "inget konto", None, t0)
                    return _neka_svar(ctx.method, text)
                # Rapporter och beställningar hör till ett projekt: rollen prövas i det projektet, inte i
                # `projekt`-argumentet (som saknas och förvalt är demokvarteret). Okänd och nekad ser lika ut.
                minst_obj = "utredare" if (ctx.method == "tools/call" and vilket in SKRIVVERKTYG) else "lasare"
                nekad_text = _objekt_nekas(argument, vilket, konto, minst_obj)
                if nekad_text is not None:
                    _skriv(logg, post, "nekad_behorighet", korningar, "objekt i annat projekt", None, t0)
                    return _neka_svar(ctx.method, nekad_text)
            if ctx.method == "tools/call" and tjanst.app.konto.aktiv:
                # Samma körningsbehörighet som webben (`/api/korningar/<id>/*`): minst lasare i
                # körningens projekt, annars "okänd körning" (ingen skillnad mot en som saknas).
                begarda: set[str] = set()
                _korningar_i_indata(argument, begarda)
                for kid in sorted(begarda):
                    if not tjanst.app.konto_far_se_korning(tillstand.konto, kid):
                        text = f"Okänd körning '{kid}'."
                        _skriv(logg, post, "nekad_policy", korningar, text, None, t0)
                        return _result_fel(text)
            if ctx.method == "tools/call" and vilket not in utan_projekt and "projekt" in argument:
                # Projekt i en geografi vars underlag inte är öppna data (K38, till exempel Stockholms baskarta) lämnas
                # aldrig ut, vilken dataklass klienten än får: samma spärr som för körningarnas källor.
                pmapp = tjanst.projektmapp(str(projekt))
                if pmapp is not None:
                    try:
                        oppenhet.kontrollera_sokvag(pmapp)
                    except OppenhetNekad:
                        _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                        return _result_fel(
                            "Nekad: projektets underlag är inte öppna data. Innehåll härlett ur icke-öppna data "
                            "lämnas inte ut via MCP. Inget har lämnats ut."
                        )
            if bestallning_projekt is not None:
                pmapp_b = tjanst.projektmapp(bestallning_projekt)
                if pmapp_b is not None:
                    try:
                        oppenhet.kontrollera_sokvag(pmapp_b)
                    except OppenhetNekad:
                        _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                        return _neka_svar(
                            ctx.method,
                            "Nekad: projektets underlag är inte öppna data. Innehåll härlett ur icke-öppna data "
                            "lämnas inte ut via MCP. Inget har lämnats ut.",
                        )
            if fraga_ref is not None:
                try:
                    tjanst.kontrollera_fraga_oppenhet(fraga_ref, fraga_fil)
                except OppenhetNekad as exc:
                    _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                    return _result_fel(str(exc))
            if rapporter_set:
                try:
                    for rid in sorted(rapporter_set):
                        _kontrollera_rapport_oppenhet(rid, korningar)
                except OppenhetNekad as exc:
                    _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                    return _neka_svar(ctx.method, str(exc))
            try:
                policy.kontrollera(str(klient["namn"]), effektiv)
            except PolicyNekad as exc:
                _skriv(logg, post, "nekad_policy", korningar, str(exc), None, t0)
                return _neka_svar(ctx.method, str(exc))
            try:
                for kid in sorted(korningar):
                    _kontrollera_korning_oppenhet(kid)
            except OppenhetNekad as exc:
                _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                return _neka_svar(ctx.method, str(exc))
            try:
                resultat = await call_next(ctx)
            except Exception as exc:
                _skriv(logg, post, "fel", korningar, f"{type(exc).__name__}", None, t0)
                raise
            d = _till_dict(resultat)
            try:
                # Efterkontroll: allt som svaret nämner ska också vara öppet (både structuredContent
                # och contents[].text för resurser), annars lämnas svaret inte ut.
                _kontrollera_svar_oppenhet(d, korningar, rapporter_set)
            except OppenhetNekad as exc:
                _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                return _neka_svar(ctx.method, str(exc))
            fel = bool(d.get("isError") or d.get("is_error"))
            try:
                _skriv(
                    logg,
                    post,
                    "fel" if fel else "ok",
                    korningar,
                    _felrad(d) if fel else None,
                    d,
                    t0,
                )
            except KorloggFel as exc:
                return _neka_svar(
                    ctx.method,
                    f"Körloggen kan inte skrivas, så resultatet lämnas inte ut: {exc}",
                )
            return resultat
        finally:
            explicit_projekt_var.reset(exp_token)
            tillstand.aterstall(token)

    server.middleware.append(mellanvara)

    # ------------------------------------------------------------------ verktyg: session och logg
    @verktyg(
        "session_deklarera",
        "Deklarerar vilken AI-modell och roll som styr den här sessionen. Loggas i körloggen på "
        "varje följande anrop (om anropet inte anger egna värden). Påverkar inget resultat.",
    )
    def session_deklarera(modell: str | None = None, roll: str | None = None) -> dict[str, Any]:
        tillstand.modell = rensa_text(modell) or tillstand.modell
        tillstand.roll = rensa_text(roll) or tillstand.roll
        return {
            "session": tillstand.session_id,
            "modell": tillstand.modell,
            "roll": tillstand.roll,
            "loggfil": "data/lokal/mcp/korlogg.jsonl",
        }

    @verktyg(
        "korlogg_hamta",
        "Hämtar körloggen: vem (klient, modell, roll) anropade vilket verktyg, med hash av indata, "
        "körnings-id och utfall. Filtrera på korning_id eller session (standard: den här sessionen). "
        "Loggen innehåller inga argumentvärden, bara hash.",
    )
    def korlogg_hamta(
        korning_id: Annotated[
            str | None, Field(description="Bara anrop som rör körningen.")
        ] = None,
        session: Annotated[
            str | None, Field(description="Sessions-id; 'alla' för alla sessioner.")
        ] = None,
        senaste: Annotated[int, Field(description="Högst så här många rader (de sista).")] = 50,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        if korning_id is None and session is None:
            session = tillstand.session_id
        max_rader = max(1, min(int(senaste), 500))
        aktivt_konto = tillstand.konto
        rader = logg.las(
            korning_id=korning_id,
            session=None if session in (None, "alla") else session,
            senaste=max_rader if aktivt_konto is None else 5000,
        )
        if aktivt_konto is not None:
            rader = [r for r in rader if r.get("konto") == aktivt_konto][-max_rader:]
        ok, antal, fel_rad = logg.verifiera()
        return {
            "session": tillstand.session_id,
            "antal_rader": len(rader),
            "rader": rader,
            "hashkedja": {"ok": ok, "rader_kontrollerade": antal, "forsta_fel_rad": fel_rad},
        }

    # ------------------------------------------------------------------ verktyg: läsa
    @verktyg(
        "projekt_lista",
        "Listar projekten du har tillgång till: lokalt demokvarteret; på en server med inloggning "
        "kontots projekt (med roll) och demokvarteret. Projekt utan beräkningar har finns=false.",
    )
    def projekt_lista(modell: Modell = None, roll: Roll = None) -> dict[str, Any]:
        return tjanst.projekt_lista(tillstand.konto)

    @verktyg(
        "projekt_skapa",
        "Skapar ett projekt i en mappad geografi (K46), som webbens 'Nytt projekt' och `floden projekt skapa`. "
        "Kräver inloggning och superadmin, eller organisationsadmin eller ägare i en organisation; du blir "
        "utredare och skapare. avgransning: GeoJSON-polygon eller [xmin, ymin, xmax, ymax] i EPSG:3011 (en "
        "polygon sparas och avgör vilka byggnader och celler som räknas till projektet; beräkningsrutnätet och "
        "bufferten är rektangeln runt den; en polygon som korsar sig själv nekas). Svaret har projekt_id och varningar (till exempel "
        "'utanför geografins giltiga kärna'); en avgränsning utanför geografins yttre gräns nekas. "
        "Skriv inga personuppgifter i namnet.",
    )
    def projekt_skapa(
        geografi: Annotated[str, Field(description="Geografi som id@version, t.ex. 'goteborg-test@v1'.")],
        namn: Annotated[str, Field(description="Projektets namn (högst 120 tecken).")],
        avgransning: Annotated[
            dict[str, Any] | list[float] | str,
            Field(description="GeoJSON-polygon, [xmin, ymin, xmax, ymax] eller samma text som i CLI; EPSG:3011."),
        ],
        organisation_synlig: Annotated[
            bool, Field(description="Dela projektet med organisationen (förval: privat).")
        ] = False,
        organisation: Annotated[
            str | None, Field(description="Organisationens id; krävs om du får skapa i flera.")
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        from webb.fel import FlodenWebbFel

        konto = tillstand.konto
        if konto is None:
            raise ToolError(
                "Inloggning krävs för att skapa projekt: anslut med en personlig MCP-token eller via "
                f"Claude-appens koppling (docs/KONTO.md). {_FEL_PREFIX}behorighet"
            )
        payload: dict[str, Any] = {
            "geografi": geografi, "namn": namn, "avgransning": avgransning,
            "organisation_synlig": organisation_synlig,
        }  # fmt: skip
        if organisation is not None:
            payload["organisation"] = organisation
        try:
            return tjanst.app.projekt_skapa.skapa(konto, payload)
        except FlodenWebbFel as exc:
            raise ToolError(f"{exc} {_FEL_PREFIX}projekt_skapa_{exc.status_kod}") from exc

    @verktyg(
        "projekt_info",
        "Projektets K1-utsträckning (EPSG:3011), rutor per bas, antal byggnader, tillgängliga "
        "analyser (dagsljus.sol, dagsljus.vsc, vatten.skyfall), sparade scenarier och demons läge.",
    )
    def projekt_info(
        projekt: Projekt = PROJEKT_ID, modell: Modell = None, roll: Roll = None
    ) -> dict[str, Any]:
        return tjanst.projekt_info(projekt)

    @verktyg(
        "geometri_sammanfattning",
        "Sammanfattning av projektets geometri: antal byggnader, fotavtryck, höjdfördelning, takformer, "
        "höjdkällor och marknivåintervall. Underlag (K1 v2), inga körningsresultat.",
    )
    def geometri_sammanfattning(
        projekt: Projekt = PROJEKT_ID, modell: Modell = None, roll: Roll = None
    ) -> dict[str, Any]:
        return tjanst.geometri_sammanfattning(projekt)

    @verktyg(
        "byggnader_lista",
        "Byggnader med id, höjd (m), fotavtryck (m²), takform och centrum (EPSG:3011). Filtrera på "
        "område [e_min, n_min, e_max, n_max]. Byggnads-id behövs för att riva eller ändra höjd.",
    )
    def byggnader_lista(
        projekt: Projekt = PROJEKT_ID,
        omrade: Annotated[
            list[float] | None, Field(description="[e_min, n_min, e_max, n_max] i EPSG:3011.")
        ] = None,
        max_antal: Annotated[int, Field(description="Högst så många byggnader (1-500).")] = 100,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.byggnader_lista(projekt, omrade, max_antal)

    @verktyg(
        "fragor_lista",
        "Utredningsfrågor (K20) som finns: scenarier, analyser, jämförelser och om de går att köra "
        "här. Frågekatalogen (beställning och mottagare) kommer i R2.",
    )
    def fragor_lista(modell: Modell = None, roll: Roll = None) -> dict[str, Any]:
        return tjanst.fragor_lista(tillstand.konto, behorighet=behorighet)

    @verktyg("korningar_lista", "Körningar (senaste först) med status och framsteg.")
    def korningar_lista(
        max_antal: Annotated[int, Field(description="Högst så många (1-100).")] = 20,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        svar = tjanst.korningar_lista(max_antal, tillstand.konto)
        synliga = [k for k in svar["korningar"] if oppenhet.oppen_korning(k["korning_id"])]
        dolda = len(svar["korningar"]) - len(synliga)
        if dolda:
            svar = {**svar, "korningar": synliga, "dolda_icke_oppna": dolda}
        return svar

    @verktyg("korning_status", "Status och framsteg (klara jobb av totalt) för en körning.")
    def korning_status(
        korning_id: KorningId, modell: Modell = None, roll: Roll = None
    ) -> dict[str, Any]:
        return tjanst.korning_status(korning_id)

    @verktyg(
        "resultat_sammanfattning",
        "Mätvärden ur en klar körning, per analys, scenario och ruta, plus körningens jämförelser "
        "(max ökning/minskning och yta som ändrats), varningar och antaganden (regn, takvatten, "
        "avtappning). Varje värde har belägg. Filtrera med analys, scenario, ruta.",
    )
    def resultat_sammanfattning(
        korning_id: KorningId,
        analys: Annotated[str | None, Field(description="T.ex. 'sol' eller 'skyfall'.")] = None,
        scenario: Annotated[str | None, Field(description="Scenario-id i frågan.")] = None,
        ruta: Annotated[
            str | None, Field(description="Rutans id, t.ex. mm_151200_6580200.")
        ] = None,
        max_rader: Annotated[int, Field(description="Högst så många tabellrader (1-500).")] = 40,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.resultat_sammanfattning(korning_id, analys, scenario, ruta, max_rader)

    @verktyg(
        "resultat_varde",
        "Värdet i en punkt (e, n) eller statistik (min, medel, median, max) i en polygon, för bas, "
        "scenario och skillnad (efter minus före), med belägg (körnings-id, fil, cell, mått, enhet). "
        "Byggnadernas tak räknas inte som mark i polygonstatistik.",
    )
    def resultat_varde(
        korning_id: KorningId,
        analys: Annotated[str, Field(description="Analys-id, t.ex. 'sol' eller 'skyfall'.")],
        roll_namn: Annotated[
            str | None,
            Field(
                description="Rasterroll, t.ex. 'soltimmar' eller 'max_djup'. Standard: huvudrollen."
            ),
        ] = None,
        e: Annotated[float | None, Field(description="Punktens e (EPSG:3011).")] = None,
        n: Annotated[float | None, Field(description="Punktens n (EPSG:3011).")] = None,
        omrade: Annotated[
            list[list[float]] | None,
            Field(description="Polygon [[e, n], ...] i stället för punkt."),
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.resultat_varde(korning_id, analys, roll_namn, e, n, omrade)

    @verktyg(
        "metoder_lista",
        "Lösare (metoder) med version, påverkansradie och valideringsstatus: 'skiss' eller "
        "'rapport'. Ingen metod är klassad som rapport än.",
    )
    def metoder_lista(modell: Modell = None, roll: Roll = None) -> dict[str, Any]:
        return tjanst.metoder_lista()

    @verktyg(
        "karta_bild",
        "Ritar en karta (PNG) av ett resultatlager med fast kartografi: norr uppåt, skalstock, "
        "koordinatticks, teckenförklaring med enhet, byggnader och körnings-id. Välj typ 'bas', "
        "'scenario' eller 'skillnad'. Returnerar bilden och dess belägg.",
    )
    def karta_bild(
        korning_id: KorningId,
        analys: Annotated[str, Field(description="Analys-id, t.ex. 'sol' eller 'skyfall'.")],
        typ: Annotated[str, Field(description="'bas', 'scenario' eller 'skillnad'.")] = "skillnad",
        scenario: Annotated[str | None, Field(description="Scenario-id (vid flera).")] = None,
        roll_namn: Annotated[
            str | None, Field(description="Rasterroll (standard: huvudrollen).")
        ] = None,
        utsnitt: Annotated[
            list[float] | None,
            Field(description="Zooma: [e_min, n_min, e_max, n_max] i EPSG:3011."),
        ] = None,
        max_px: Annotated[int, Field(description="Längsta kartsida i pixlar (300-2000).")] = 900,
        modell: Modell = None,
        roll: Roll = None,
    ) -> CallToolResult:
        try:
            png, meta = tjanst.karta(korning_id, analys, typ, scenario, roll_namn, utsnitt, max_px)
        except FlodenFel as exc:
            raise ToolError(f"{exc} {_FEL_PREFIX}{exc.kod}") from exc
        projekt_k = tjanst.app.projekt_for_korning(korning_id) or PROJEKT_ID
        meta = {"dataklass": klass_for_projekt(DataKlass.OPPET, projekt_k).value, **meta}
        return CallToolResult(
            content=[
                ImageContent(
                    type="image", data=base64.b64encode(png).decode("ascii"), mime_type="image/png"
                ),
                TextContent(type="text", text=json.dumps(meta, ensure_ascii=False)),
            ],
            structured_content=meta,
        )

    # ------------------------------------------------------------------ verktyg: planera och köra
    @verktyg(
        "scenario_skapa",
        "Skapar ett planscenario (K9) och sparar det lokalt: nya hus (polygon eller rektangel i "
        "EPSG:3011 plus hojd_m över mark), rivna byggnader (byggnads-id), höjdändringar, eller "
        "rå K9-ändringar. Kärnans kontroll (giltig form, höjd 0-200 m, inom projektets område, "
        "byggnadsreferenser) körs; ett ogiltigt scenario sparas inte och felen står i svaret. "
        "bara_kontroll=true sparar inget.",
    )
    def scenario_skapa(
        namn: Annotated[str, Field(description="Scenariots namn (blir filnamn).")],
        hus: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description="Nya hus: [{'id'?, 'polygon': [[e,n],...] ELLER 'rektangel': "
                "[e_min,n_min,e_max,n_max], 'hojd_m': 18, 'takform'?}]."
            ),
        ] = None,
        rivna: Annotated[list[str] | None, Field(description="Byggnads-id att riva.")] = None,
        hojdandringar: Annotated[
            list[dict[str, Any]] | None,
            Field(description="[{'byggnads_id': '...', 'hojd_m': 15}] (ny höjd över mark)."),
        ] = None,
        andringar: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Rå K9-ändringar (docs/DATAKONTRAKT.md)."),
        ] = None,
        beskrivning: str = "",
        bara_kontroll: Annotated[bool, Field(description="Kontrollera utan att spara.")] = False,
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.scenario_skapa(
            projekt, namn, hus, rivna, hojdandringar, andringar, beskrivning, bara_kontroll
        )

    @verktyg(
        "fraga_skapa",
        "Skapar en utredningsfråga (K20/v1, som JSON-objekt) och sparar den lokalt efter kärnans "
        "validering (okända fält, lösare och parametrar). Sökvägar måste ligga i demokvarteret eller "
        "kartverktygets mapp. Kör sedan korplan och kor med fraga=ref.",
    )
    def fraga_skapa(
        fraga: Annotated[
            dict[str, Any], Field(description="K20/v1-objekt (docs/DATAKONTRAKT.md).")
        ],
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.fraga_skapa(fraga)

    @verktyg(
        "korplan",
        "Visar vad en körning skulle göra utan att köra: jobb, rutor, vad som redan finns i cachen, "
        "påverkansradie och uppskattad tid. Ange scenario (sparat) eller fraga (ref). "
        "analyser: t.ex. ['dagsljus.sol','vatten.skyfall'] (standard: sol och skyfall).",
    )
    def korplan(
        scenario: Annotated[str | None, Field(description="Sparat scenario (slug).")] = None,
        fraga: Annotated[str | None, Field(description="Frågans ref (fragor_lista).")] = None,
        analyser: list[str] | None = None,
        avtappning_mm_h: Annotated[
            float | None, Field(description="Ledningsnätets avtappning, mm/h (standard 0).")
        ] = None,
        detalj: Annotated[bool, Field(description="Lista varje jobb.")] = False,
        lage_idag: Annotated[
            bool, Field(description="Planen för projektets befintliga läge (bara före, utan förslag).")
        ] = False,
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.korplan(projekt, scenario, fraga, analyser, avtappning_mm_h, detalj, lage_idag)

    @verktyg(
        "kor",
        "Startar en körning i bakgrunden och returnerar körnings-id direkt. Ange scenario (sparat) "
        "eller fraga (ref), eller lage_idag=true för projektets befintliga läge utan förslag. Jobb med "
        "oförändrad nyckel hämtas ur cachen. Använd vanta för att invänta resultatet.",
    )
    def kor(
        scenario: Annotated[str | None, Field(description="Sparat scenario (slug).")] = None,
        fraga: Annotated[str | None, Field(description="Frågans ref (fragor_lista).")] = None,
        analyser: list[str] | None = None,
        avtappning_mm_h: Annotated[
            float | None, Field(description="Ledningsnätets avtappning, mm/h (standard 0).")
        ] = None,
        lage_idag: Annotated[
            bool,
            Field(
                description="Räkna projektets befintliga läge (bara före, utan förslag). Sparas som projektets "
                "egen körning (slag lage_idag)."
            ),
        ] = False,
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        projekt_arg: str | None = projekt
        if (
            fraga is not None
            and not explicit_projekt_var.get()
            and tjanst.app.konto.aktiv
            and tjanst.fraga_projekt(fraga) is None
        ):
            projekt_arg = None
        return tjanst.kor(projekt_arg, scenario, fraga, analyser, avtappning_mm_h, lage_idag)

    @verktyg(
        "vanta",
        "Väntar på att körningarna blir klara (högst tidsgrans_s sekunder, standard 45) och "
        "returnerar status. Anropa igen om tidsgränsen nåddes.",
    )
    def vanta(
        korning_ids: Annotated[list[str], Field(description="Körnings-id att vänta på.")],
        tidsgrans_s: Annotated[float, Field(description="Sekunder (0-600).")] = 45.0,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.vanta(korning_ids, tidsgrans_s)

    @verktyg(
        "jamfor",
        "Körningens jämförelser före/efter: största ökning och minskning och yta som ändrats "
        "(över tröskeln), med belägg. Ange omrade (polygon) för statistik inom ett område. "
        "Andra par än de som står i frågan kräver en ny fråga (fraga_skapa).",
    )
    def jamfor(
        korning_id: KorningId,
        omrade: Annotated[
            list[list[float]] | None, Field(description="Valfri polygon [[e, n], ...] (EPSG:3011).")
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.jamfor(korning_id, omrade)

    _registrera_rapportverktyg(server, verktyg, rapporter, tillstand, behorighet, oppenhet)
    _registrera_resurser(server, tjanst)
    return server


def _registrera_rapportverktyg(
    server: MCPServer,
    verktyg: Callable[..., Callable[[Callable[..., Any]], Callable[..., Any]]],
    rapporter: RapportTjanst,
    tillstand: Sessionstillstand,
    behorighet: Behorighet | None = None,
    oppenhet: Oppenhet | None = None,
) -> None:
    """Rapportverktygen (R1): frågekatalog, skissrapport, påståenden med belägg, kontroll, export."""
    RapportId = Annotated[str, Field(description="Rapport-id från rapport_skapa (r-<12 hex>).")]

    @verktyg(
        "fragekatalog_sok",
        "Söker i frågekatalogen (Stockholms utredningsfrågor, docs/utredningar-stockholm/fragor.json, "
        "redaktionellt underlag; 90 frågor, ekologifrågorna är EK-01 till EK-15). Filtrera på omrade (skyfall, ekologi, luft, buller, dagsljus, sol), villkor "
        "(t.ex. 'Planförslag') och text. För skyfall, dagsljus (DL) och solstudier (SO) står också vad "
        "Floden i dag kan belägga per fråga (floden.markning: visat, delvis, underlag saknas, "
        "ej tillämpligt).",
    )
    def fragekatalog_sok(
        omrade: Annotated[str | None, Field(description="Område, t.ex. 'skyfall'.")] = None,
        villkor: Annotated[
            list[str] | None, Field(description="Villkor (delsträngar); 'Alla …' gäller alltid.")
        ] = None,
        text: Annotated[str | None, Field(description="Delsträng i fråga eller redovisning.")] = None,
        ids: Annotated[list[str] | None, Field(description="Bara dessa id (t.ex. SK-06).")] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        fragor = rapporter.katalog.sok(omrade=omrade, villkor=villkor, text=text, ids=ids)
        ut = [fraga_med_formaga(f) for f in fragor]
        return {
            "antal": len(ut),
            "granskningsdatum": rapporter.katalog.granskningsdatum,
            "fragor": ut,
            "anmarkning": "Katalogen är redaktionellt underlag sammanställt med AI-stöd och ska "
            "granskas; val av regel och bedömning avgörs per ärende.",
        }

    @verktyg(
        "bestallning_skapa",
        "Skapar en beställning (K30) som styr vilka katalogfrågor ett skissunderlag tar med och hur "
        "ingressen skrivs: syfte, skede ('skiss'; 'planunderlag' kommer i R3 och kan inte köras "
        "än), mottagare (planhandlaggare, byggaktor, granskare), ämnen och valfria frågor, "
        "villkor och scenario (före och efter). Svaret listar frågorna som valts och hur många "
        "som utelämnats. Använd bestallning_id i rapport_skapa. Skriv inga personuppgifter i "
        "syftet.",
    )
    def bestallning_skapa(
        syfte: Annotated[str, Field(description="Vad ska underlaget hjälpa till att avgöra?")],
        amnen: Annotated[
            list[str], Field(description=f"Ämnen: {', '.join(BESTALLNINGSAMNEN)}. Dagsljus och sol hör ihop.")
        ],
        skede: Annotated[str, Field(description=f"{' eller '.join(SKEDEN)}.")] = "skiss",
        mottagare_roll: Annotated[
            str, Field(description=f"Vem läser: {', '.join(MOTTAGARROLLER)}.")
        ] = "planhandlaggare",
        fragor: Annotated[
            list[str] | None, Field(description="Frågor (id) som ska med; åsidosätter urvalet.")
        ] = None,
        villkor: Annotated[list[str] | None, Field(description="Filtrera på villkor.")] = None,
        bara_med_underlag: Annotated[
            bool, Field(description="Ta bara med frågor Floden kan besvara helt eller delvis.")
        ] = False,
        scenario: Annotated[
            dict[str, Any] | None,
            Field(description="{'fore': 'bas', 'efter': ['hojd', 'nytt_hus']} (scenarionamn)."),
        ] = None,
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        b = rapporter.bestallning_skapa(
            syfte=syfte,
            amnen=amnen,
            projekt=projekt,
            skede=skede,
            mottagare_roll=mottagare_roll,
            fragor=fragor or (),
            villkor=villkor or (),
            bara_med_underlag=bara_med_underlag,
            scenario=scenario,
        )
        return {
            **sammanfattning_bestallning(b),
            "nasta": "rapport_skapa(korningar=[...], bestallning_id=...)",
        }

    @verktyg(
        "bestallning_hamta",
        "Hämtar en beställning (K30) med id, eller listar projektets beställningar utan id.",
    )
    def bestallning_hamta(
        bestallning_id: Annotated[str | None, Field(description="b-<12 hex>; utelämnat = lista.")] = None,
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        if bestallning_id:
            return sammanfattning_bestallning(rapporter.bestallning_hamta(bestallning_id))
        lista = rapporter.bestallningar.lista(projekt)
        return {"antal": len(lista), "bestallningar": [sammanfattning_bestallning(b) for b in lista]}

    @verktyg(
        "rapport_skapa",
        "Skapar ett skissunderlag (nivå 1: sållning, inte bedömning mot krav) för ett ämne ("
        "'skyfall', 'dagsljus', 'ekologi' eller 'luft', se bestallning_skapa; buller har ingen skissrapport: "
        "det är spärrat eftersom Nord2000 inte är konformt) över en eller flera klara körningar. Floden ritar kartor (före, efter, "
        "skillnad; samma takmask som webben; luft har ingen karta), hämtar metodernas valideringsstatus och väljer frågor "
        "ur katalogen. Luft bygger på lösaren luft.mkn: MKN-intervall för PM10 ur en given timserie och en angiven "
        "bakgrund, en analys per serie (före och efter är två analyser). Svaret listar beläggsadresser du kan använda i pastaende_lagg_till.",
    )
    def rapport_skapa(
        korningar: Annotated[
            list[str] | None,
            Field(
                description="Körnings-id (klara), t.ex. ['demo-vatten']. Utelämnat: användarens senaste körning "
                "för förslaget (forslag), annars projektets senaste egna körning, och demokörningen bara om inget "
                "annat finns (det står då i rapporten)."
            ),
        ] = None,
        forslag: Annotated[
            str | None,
            Field(description="Förslagets namn (slug, som i scenario_skapa) när korningar utelämnas."),
        ] = None,
        amne: Annotated[
            str | None,
            Field(description="'skyfall' (förval), 'dagsljus' (dagsljus och sol), 'ekologi' eller 'luft'."),
        ] = None,
        bestallning_id: Annotated[
            str | None,
            Field(description="Beställning (b-<12 hex>) från bestallning_skapa; styr ämne och frågor."),
        ] = None,
        niva: Annotated[int, Field(description="1 = skissunderlag (enda nivån i R1).")] = 1,
        titel: str | None = None,
        fragor: Annotated[
            list[str] | None, Field(description="Frågor (id); utelämnat = alla för ämnet/villkoren.")
        ] = None,
        villkor: Annotated[list[str] | None, Field(description="Filtrera frågorna på villkor.")] = None,
        mottagare: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description="Platser som påverkas: [{namn (a-z0-9_), polygon [[e, n], ...] "
                "EPSG:3011, beskrivning}]. Används i adresser omrade.<namn>.<lager_id>.<statistik>."
            ),
        ] = None,
        ansvarig_utredare: Annotated[
            str | None, Field(description="Valfritt i nivå 1; skrivs i metodnotisen.")
        ] = None,
        installningar: Annotated[
            dict[str, Any] | None,
            Field(
                description="Språk och layout (valfritt; förval och config.local.toml [rapport] "
                "gäller annars): sprakprofil ('sbk' förval eller 'neutral'), numrerade_rubriker, "
                "numrerade_figurer (förval false), avsandare (förval 'Stadsbyggnadskontoret'), "
                "diarienummer, handlaggare, accentfarg ('#000000'), logotyp, typsnitt_brodtext, "
                "typsnitt_rubriker (lokala sökvägar; bara filens sha256 lagras)."
            ),
        ] = None,
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        amne_val = amne if amne or bestallning_id else "skyfall"
        if not korningar:
            from rapport.korningsval import UnderlagFel, valj_korning
            from rapport.skissrapport import RapportFel

            amne_k = amne_val
            if amne_k is None and bestallning_id:
                amne_k = rapporter.bestallningar.las(bestallning_id).rapportamne
            try:
                korningar = [str(valj_korning(rapporter, projekt, str(amne_k), scenario=forslag)["korning_id"])]
            except UnderlagFel as exc:
                raise RapportFel(str(exc)) from exc
        r = rapporter.skapa(
            projekt=projekt,
            amne=amne_val,
            bestallning=bestallning_id,
            korningar=korningar,
            niva=niva,
            titel=titel,
            fragor=fragor,
            villkor=villkor,
            mottagare=mottagare or [],
            ai_stod=True,
            modell=modell or tillstand.modell,
            ansvarig_utredare=ansvarig_utredare,
            av=tillstand.session_id,
            installningar=installningar,
        )
        return {
            **sammanfattning(r, rapporter.arkiv),
            "installningar": r.installningar.till_dict(),
            "underlag": [
                {"korning_id": k["korning_id"], **(k.get("underlag") or {"slag": "okand", "scenario": None})}
                for k in r.korningar
            ],
            "fragor": [
                {"id": f, "fraga": r.katalog["fragor"][f]["fraga"]} for f in r.fragor
            ],
            "kartor": [{"kart_id": k.kart_id, "bildtext": k.bildtext} for k in r.kartor],
            "metoder": list(r.metoder),
            "belagg_adresser": rapporter.adresser(r),
            "regel": (
                "Skriv tal som platshållare {{matt:<adress>}} (valfritt |avrundning=10, "
                "|enhet=nej, |tecken=ja, |format=exp). Varje adress i texten ska stå i belagg. "
                "Status: visat, avvikelse, villkorat (forutsattning + ansvarig_funktion), "
                "underlag_saknas (saknat_underlag), ej_tillampligt (motivering). Fördjupning: nej, "
                "ja, kan_inte_avgoras. Varje fråga behöver minst ett påstående före export."
            ),
        }

    @verktyg(
        "rapport_installningar",
        "Visar eller ändrar en rapports inställningar för språk och layout. Utan andringar: visar "
        "dem. Nycklar: sprakprofil ('sbk' = utredningstext från stadsbyggnadskontoret, förval; "
        "'neutral'), numrerade_rubriker och numrerade_figurer (förval false), avsandare, "
        "diarienummer, handlaggare, accentfarg (hex), logotyp, typsnitt_brodtext och "
        "typsnitt_rubriker (lokala sökvägar till PNG/JPEG respektive TTF/OTF; tom sträng tar bort). "
        "Sökvägarna lagras inte, bara filens sha256. Språkprofilen styr mallens text; redan "
        "tillagda påståenden ändras inte. Kontrollen behöver göras om efteråt.",
    )
    def rapport_installningar(
        rapport_id: RapportId,
        andringar: Annotated[
            dict[str, Any] | None, Field(description="Inställningar att ändra; utelämnat = visa.")
        ] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        if andringar:
            inst = rapporter.andra_installningar(rapport_id, andringar, av=tillstand.session_id)
        else:
            inst = rapporter.hamta(rapport_id).installningar
        return {
            "rapport_id": rapport_id,
            "installningar": inst.till_dict(),
            "anmarkning": "Logotyp och typsnitt anges med sha256; sökvägar lagras inte.",
        }

    @verktyg(
        "pastaende_lagg_till",
        "Lägger ett påstående i en rapport: fråga (id), status, text med platshållare för tal "
        "({{matt:<adress>}}) och belägg (adresser). Floden hämtar varje belägg ur körningen och "
        "vägrar påståendet om ett belägg saknas, en siffra står utanför en platshållare, "
        "statusreglerna inte följs, måttet tolkas fel (t.ex. maxdjup som vattennivå) eller "
        "nämnaren blandas.",
    )
    def pastaende_lagg_till(
        rapport_id: RapportId,
        fraga_id: Annotated[str, Field(description="Frågans id, t.ex. 'SK-06'.")],
        status: Annotated[
            str,
            Field(description="visat, avvikelse, villkorat, underlag_saknas eller ej_tillampligt."),
        ],
        text_med_platshallare: Annotated[
            str, Field(description="Texten; tal bara som {{matt:<adress>}}.")
        ],
        fordjupning: Annotated[
            str, Field(description="Behöver frågan fördjupas? nej, ja eller kan_inte_avgoras.")
        ],
        belagg: Annotated[
            list[str] | None,
            Field(description="Beläggsadresser (se belagg_adresser i rapport_skapa)."),
        ] = None,
        korning_id: Annotated[
            str | None, Field(description="Körningen beläggen gäller (om rapporten har flera).")
        ] = None,
        mottagare: Annotated[
            str | None, Field(description="Mottagarens namn eller 'hela_omradet' (valfritt).")
        ] = None,
        forutsattning: str | None = None,
        ansvarig_funktion: str | None = None,
        motivering: str | None = None,
        saknat_underlag: list[str] | None = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        bl: list[str | dict[str, Any]] = [
            {"adress": a, "korning_id": korning_id} if korning_id else a for a in belagg or []
        ]
        p = rapporter.lagg_till_pastaende(
            rapport_id,
            fraga_id=fraga_id,
            status=status,
            text=text_med_platshallare,
            belagg=bl,
            fordjupning=fordjupning,
            mottagare=mottagare,
            forutsattning=forutsattning,
            ansvarig_funktion=ansvarig_funktion,
            motivering=motivering,
            saknat_underlag=saknat_underlag or [],
            forfattare="ai",
            modell=modell or tillstand.modell,
            av=tillstand.session_id,
        )
        return {
            "rapport_id": rapport_id,
            "pastaende_id": p.pastaende_id,
            "fraga_id": p.fraga_id,
            "status": p.status,
            "fylld_text": p.fylld_text(),
            "belagg": [
                {
                    "adress": b.adress,
                    "korning_id": b.korning_id,
                    "varde": b.varde,
                    "enhet": b.enhet,
                    "matt": b.matt,
                    "namnare": b.namnare,
                    "tidsdefinition": b.tidsdefinition,
                    "fil": b.fil,
                }
                for b in p.belagg
            ],
        }

    @verktyg(
        "rapport_kontrollera",
        "Kör kontrollen inför export (spåra tal och figurer, scenario, mottagare och nämnare, mått "
        "och tidsstatistik, osäkerhet, kartan, status per fråga, dataskydd, metodstatus). Ett fel "
        "stoppar exporten; meddelandena säger vad som ska rättas.",
    )
    def rapport_kontrollera(
        rapport_id: RapportId, modell: Modell = None, roll: Roll = None
    ) -> dict[str, Any]:
        k = rapporter.kontrollera(rapport_id, av=tillstand.session_id)
        return {"rapport_id": rapport_id, **k}

    @verktyg(
        "rapport_exportera",
        "Exporterar rapporten till Word ('word', .docx) eller PDF ('pdf') efter en godkänd kontroll. "
        "Körloggen och manifesten ingår som bilagor. Underkänd kontroll stoppar exporten.",
    )
    def rapport_exportera(
        rapport_id: RapportId,
        format: Annotated[str, Field(description="'word' eller 'pdf'.")] = "pdf",
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        from gemensamt.hash import sha256_fil

        fil = rapporter.exportera(rapport_id, format, av=tillstand.session_id)
        return {
            "rapport_id": rapport_id,
            "format": format,
            "fil": str(fil),
            "storlek_byte": fil.stat().st_size,
            "sha256": sha256_fil(fil),
        }

    @verktyg(
        "rapport_hamta",
        "Hämtar en rapport (K33): frågor, påståenden med ifyllda tal och belägg, kartor, metoder, "
        "kontroll och exporter. Utan rapport_id: listar rapporterna. Samma innehåll som resursen "
        "floden://rapport/{rapport_id}.",
    )
    def rapport_hamta(
        rapport_id: Annotated[str | None, Field(description="Rapport-id; utelämnat = lista.")] = None,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        if rapport_id is None:
            # Bara rapporter i projekt där kontot har en roll (utan inloggning: alla) och öppna data (K38).
            konto = tillstand.konto
            alla = rapporter.lista()
            if behorighet is not None or rapporter.tjanst.app.konto.aktiv:
                alla = [
                    r
                    for r in alla
                    if konto is not None
                    and (
                        behorighet(konto, str(r.get("projekt", "")), "lasare")
                        if behorighet is not None
                        else rapporter.tjanst.app.konto.har_roll(
                            konto, str(r.get("projekt", "")), "lasare"
                        )
                    )
                ]
            if oppenhet is not None:
                synliga = [
                    r
                    for r in alla
                    if rapporter.tjanst.projekt_oppen(str(r.get("projekt", ""))) is not False
                    and all(oppenhet.oppen_korning(str(kid)) for kid in r.get("korningar", []))
                ]
                dolda = len(alla) - len(synliga)
                if dolda:
                    return {"rapporter": synliga, "dolda_icke_oppna": dolda}
                alla = synliga
            return {"rapporter": alla}
        return _rapport_svar(rapporter, rapport_id)

    @server.resource(
        "floden://rapport/{rapport_id}",
        name="rapport",
        title="Rapport (K33)",
        description="En rapport med påståenden, belägg, kartor och kontroll (JSON).",
        mime_type="application/json",
    )
    def rapport_resurs(rapport_id: str) -> str:
        try:
            return json.dumps(_rapport_svar(rapporter, rapport_id), ensure_ascii=False, indent=1)
        except RapportFel as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    @server.prompt(
        name="skissunderlag_skyfall",
        title="Skissunderlag skyfall för …",
        description="Skissunderlag (nivå 1) för skyfall: välj körning, besvara SK-frågorna med "
        "belägg, kontrollera och exportera Word och PDF.",
    )
    def skissunderlag_skyfall(
        plats: str = "demokvarteret", korning: str = "demo-vatten"
    ) -> str:
        return (
            f"Gör ett skissunderlag för skyfall för {plats} (körning {korning}).\n"
            "1. korningar_lista och resultat_sammanfattning: kontrollera att körningen är klar och "
            "har en jämförelse före/efter.\n"
            "2. fragekatalog_sok(omrade='skyfall'): läs frågorna SK-01 till SK-15 och vad Floden "
            "kan belägga.\n"
            f"3. rapport_skapa(korningar=['{korning}']), gärna med en mottagare (polygon) där "
            "förslaget byggs.\n"
            "4. För varje fråga: pastaende_lagg_till med status och fördjupning. Skriv tal bara som "
            "{{matt:<adress>}} ur belagg_adresser. Saknas underlag: status underlag_saknas och "
            "skriv vad som saknas. Inget 'visat' utan belägg. Bedöm inte mot krav (nivå 1 är "
            "sållning); skriv inte att något är lämpligt eller uppfyller krav.\n"
            "5. rapport_kontrollera; rätta det som underkänns.\n"
            "6. rapport_exportera format 'word' och 'pdf'.\n"
            "Du äger inget tal. Metoderna är skisser."
        )


    @server.prompt(
        name="skissunderlag_dagsljus",
        title="Skissunderlag dagsljus och sol för …",
        description="Skissunderlag (nivå 1) för dagsljus och solstudier: beställning, frågor, "
        "körning, rapport, kontroll och export.",
    )
    def skissunderlag_dagsljus(
        plats: str = "demokvarteret", korning: str = "demo-dagsljus"
    ) -> str:
        return (
            f"Gör ett skissunderlag för dagsljus och sol för {plats} (körning {korning}).\n"
            "1. bestallning_skapa(syfte, amnen=['dagsljus','sol'], mottagare_roll, scenario="
            "{'fore': 'bas', 'efter': [...]}): beställningen väljer frågorna ur DL- och SO-listan.\n"
            "2. fragekatalog_sok(omrade='dagsljus'): se floden.markning per fråga.\n"
            f"3. rapport_skapa(korningar=['{korning}'], bestallning_id=...).\n"
            "4. För varje fråga: pastaende_lagg_till. Tal bara som {{matt:<adress>}} ur "
            "belagg_adresser. Saknas underlag: status underlag_saknas och skriv vad som saknas. "
            "Bedöm inte mot krav; VSC mot BRE:s gräns är ett antal punkter, inte ett utlåtande.\n"
            "5. rapport_kontrollera; rätta det som underkänns.\n"
            "6. rapport_exportera format 'word' och 'pdf'.\n"
            "Du äger inget tal. Metoderna är skisser."
        )

    @server.prompt(
        name="skissunderlag_ekologi",
        title="Skissunderlag ekologi (grön infrastruktur) för …",
        description="Skissunderlag (nivå 1) för ekologisk konnektivitet: beställning, EK-frågor, "
        "körning, rapport, kontroll och export.",
    )
    def skissunderlag_ekologi(
        plats: str = "demokvarteret", korning: str = "demo-ekologi"
    ) -> str:
        return (
            f"Gör ett skissunderlag för ekologi (grön infrastruktur och spridningssamband) för {plats} "
            f"(körning {korning}).\n"
            "1. bestallning_skapa(syfte, amnen=['ekologi'], scenario={'fore': 'fore', 'efter': [...]}): "
            "beställningen väljer frågorna EK-01 till EK-15.\n"
            "2. fragekatalog_sok(omrade='ekologi'): se floden.markning per fråga.\n"
            f"3. rapport_skapa(korningar=['{korning}'], bestallning_id=...).\n"
            "4. För varje fråga: pastaende_lagg_till. Tal bara som {{matt:<adress>}} ur "
            "belagg_adresser (matvarde, ekojamforelse, ekonatverk). Skriv signalart och källa, "
            "'potentiell livsmiljö, okänd kvalitet' om NMD-habitat och antagandet om d50. Saknas "
            "underlag: status underlag_saknas. Säg inget om att arten finns eller att "
            "sambandet är säkrat.\n"
            "5. rapport_kontrollera; rätta det som underkänns.\n"
            "6. rapport_exportera format 'word' och 'pdf'.\n"
            "Du äger inget tal. Metoderna är skisser."
        )

    @server.prompt(
        name="skissunderlag_luft",
        title="Skissunderlag luft (PM10 mot MKN som intervall) för …",
        description="Skissunderlag (nivå 1) för luft: beställning, LU-frågor, körning av luft.mkn, rapport, "
        "kontroll och export.",
    )
    def skissunderlag_luft(plats: str = "demokvarteret", korning: str = "demo-luft") -> str:
        return (
            f"Gör ett skissunderlag för luft (PM10 mot dygnsnormen som intervall) för {plats} (körning {korning}).\n"
            "Förutsättning: lösaren luft.mkn räknar inte fram haltbidraget. Den tar en given timserie (CSV "
            "tid,lokal_ug_m3) från ett annat verktyg och en uttryckligt angiven bakgrund, en analys per serie. "
            "Före och efter är två analyser med samma bakgrund, faktorer och gräns.\n"
            "1. bestallning_skapa(syfte, amnen=['luft']): beställningen väljer frågorna LU-01 till LU-15. "
            "Ange inget scenario (före och efter är analyser, inte scenarier).\n"
            "2. fragekatalog_sok(omrade='luft'): se floden.markning per fråga.\n"
            f"3. rapport_skapa(korningar=['{korning}'], bestallning_id=...).\n"
            "4. För varje fråga: pastaende_lagg_till. Tal bara som {{matt:<adress>}} ur belagg_adresser "
            "(matvarde.<analys>.<scenario>.<mått>). Skriv att serien är extern, att bakgrunden är ett antagande "
            "och att intervallet är en känslighet, inte en konfidens. Säg aldrig att normen klaras eller "
            "överskrids om intervallet korsar högsta antalet dygn; då kan underlaget varken säga det ena eller "
            "det andra. Saknas underlag: status underlag_saknas.\n"
            "5. rapport_kontrollera; rätta det som underkänns.\n"
            "6. rapport_exportera format 'word' och 'pdf'.\n"
            "Du äger inget tal. Metoden är en skiss."
        )


def _rapport_svar(rapporter: RapportTjanst, rapport_id: str) -> dict[str, Any]:
    r = rapporter.hamta(rapport_id)
    d = r.till_dict()
    for p, pd in zip(r.pastaenden, d["pastaenden"], strict=True):
        try:
            pd["fylld_text"] = p.fylld_text()
        except PastaendeFel as exc:
            pd["fylld_text"] = None
            pd["fel"] = str(exc)
    d["sammanfattning"] = sammanfattning(r, rapporter.arkiv)
    return d


def _felrad(d: Mapping[str, Any]) -> str | None:
    for c in d.get("content", []) or []:
        t = c.get("text") if isinstance(c, Mapping) else getattr(c, "text", None)
        if t:
            return str(t)[:160]
    return None


def _skriv(
    logg: Korlogg,
    post: dict[str, Any],
    utfall: str,
    korningar: set[str],
    fel: str | None,
    svar: Mapping[str, Any] | None,
    t0: float,
) -> None:
    rad = {
        **post,
        "rapport_id": sorted(post.get("rapport_id", set())),
        "utfall": utfall,
        "fel": rensa_text(fel),
        "korning_id": sorted(korningar),
        "svar_sha256": None if svar is None else hash_av(svar),
        "tid_ms": int((time.monotonic() - t0) * 1000),
    }
    logg.skriv(rad)


# ---------------------------------------------------------------------- resurser och promptar

_RESURSER: tuple[tuple[str, str, str, str], ...] = (
    ("metod/vatten", "VATTEN.md", "Metod: vatten", "Takvatten, avtappning och volymbalans."),
    (
        "metod/dagsljus",
        "DAGSLJUS.md",
        "Metod: dagsljus",
        "Soltimmar, SVF och VSC: matematik, facit.",
    ),
    ("metod/luft", "LUFT.md", "Metod: luft", "Vindkomfort och luftkvalitet (LES)."),
    ("metod/karna", "KARNA.md", "Kärnan", "Scenario, cache och jämförelse A/B."),
    ("metod/validering", "VALIDERING.md", "Validering", "Testsvit, mått och facit."),
    ("metod/mcp", "MCP.md", "MCP-gränssnittet", "Verktyg, dataklasser och körlogg."),
)


def _kontraktsavsnitt(text: str) -> dict[str, str]:
    """`## K9 – Planscenario (v1)` -> {'K9': avsnittstexten}."""
    ut: dict[str, str] = {}
    delar = re.split(r"^## ", text, flags=re.MULTILINE)
    for d in delar[1:]:
        m = re.match(r"(K\d+)\b", d)
        if m:
            ut[m.group(1)] = "## " + d.rstrip() + "\n"
    return ut


def _text_fn(text: str) -> Callable[[], str]:
    def _t() -> str:
        return text

    return _t


def _registrera_resurser(server: MCPServer, tjanst: FlodenTjanst) -> None:
    docs = tjanst.rot / "docs"

    def las(fil: str) -> Callable[[], str]:
        def _l() -> str:
            return (docs / fil).read_text(encoding="utf-8")

        return _l

    for uri, fil, titel, beskr in _RESURSER:
        if (docs / fil).is_file():
            server.resource(
                f"floden://{uri}",
                name=uri.replace("/", "_"),
                title=titel,
                description=beskr,
                mime_type="text/markdown",
            )(las(fil))
    kontrakt = docs / "DATAKONTRAKT.md"
    if kontrakt.is_file():
        server.resource(
            "floden://kontrakt/datakontrakt",
            name="kontrakt_alla",
            title="Datakontrakten (K0-K22)",
            description="Formaten mellan modulerna.",
            mime_type="text/markdown",
        )(las("DATAKONTRAKT.md"))
        for kod, avsnitt in _kontraktsavsnitt(kontrakt.read_text(encoding="utf-8")).items():
            if kod in {"K0", "K1", "K9", "K10", "K11", "K20", "K21", "K22"}:
                server.resource(
                    f"floden://kontrakt/{kod.lower()}",
                    name=f"kontrakt_{kod.lower()}",
                    title=f"Kontrakt {kod}",
                    description=f"Avsnitt {kod} ur datakontrakten.",
                    mime_type="text/markdown",
                )(_text_fn(avsnitt))

    @server.prompt(
        name="utforska_demokvarteret",
        title="Utforska demokvarteret",
        description="Lär känna det påhittade demokvarteret och läs de förkörda resultaten.",
    )
    def utforska() -> str:
        return (
            "Utforska Flodens demokvarter (påhittat). Gör så här och redovisa med belägg:\n"
            "1. projekt_info och geometri_sammanfattning: hur stort är området, hur många byggnader?\n"
            "2. korningar_lista: vilka förkörda körningar finns (demo-dagsljus, demo-vatten)?\n"
            "3. resultat_sammanfattning för varje körning: vad ändras i scenarierna? Citera jämförelsernas "
            "tal oförändrade med enhet och belägg.\n"
            "4. karta_bild (typ skillnad) för soltimmar och maxdjup.\n"
            "5. metoder_lista: skriv att metoderna är skisser.\n"
            "Du äger inget tal: skriv bara tal som står i verktygssvaren."
        )

    @server.prompt(
        name="prova_nytt_hus",
        title="Pröva ett nytt hus före/efter",
        description="Rita ett hus, kör före/efter för dagsljus och skyfall och läs skillnaden.",
    )
    def prova_hus(
        hojd_m: str = "18", plats: str = "en öppen yta i södra delen av demokvarteret"
    ) -> str:
        return (
            f"Pröva ett nytt hus ({hojd_m} m högt) på {plats} i demokvarteret.\n"
            "1. projekt_info (utsträckning) och byggnader_lista (var finns hus redan?).\n"
            "2. Välj en rektangel för huset inom projektets utsträckning utan att skära befintliga hus; "
            "scenario_skapa med bara_kontroll=true. Rätta efter kärnans felmeddelanden.\n"
            "3. scenario_skapa (spara), korplan, kor, vanta.\n"
            "4. jamfor: redovisa skillnaden i soltimmar och maxdjup med enhet och belägg.\n"
            "5. karta_bild (typ skillnad) för båda analyserna.\n"
            "Skriv att allt är påhittade data och att metoderna är skisser. Du äger inget tal."
        )


__all__ = ["INSTRUKTIONER", "bygg_server"]
