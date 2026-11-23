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
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ImageContent, TextContent
from pydantic import Field

from floden_mcp import MCP_VERSION
from floden_mcp.dataklass import DataKlass, Policy, PolicyNekad
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
Projekt = Annotated[str, Field(description="Projekt-id. Just nu finns bara 'demokvarteret'.")]
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
rapport_skapa (skyfall eller dagsljus/sol; med bestallning_id styr beställningen frågorna och
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
    {"session_deklarera", "korlogg_hamta", "metoder_lista", "projekt_lista", "fragekatalog_sok"}
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
        "rapport_exportera",
    }
)
"""Verktyg som skriver (scenarier, frågor, körningar, rapporter, export): rollen utredare krävs.
Alla andra verktyg läser och kräver minst lasare. Verktyg utan `projekt`-argument (körningar,
resultat, rapporter) gäller i dag bara demokvarteret och kräver rollen där."""

Behorighet = Callable[[str, str, str], bool]
"""(konto-id, projekt-id, minsta roll) -> har kontot rollen? Ges bara när inloggning är på."""


def _result_fel(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


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
    utan_projekt = {"session_deklarera", "korlogg_hamta", "metoder_lista"}

    def _objekt_nekas(argument: Mapping[str, Any], vilket: str, konto: str | None, minst: str) -> str | None:
        """Text om verktyget/resursen pekar på en rapport eller beställning i ett projekt som kontot inte har
        rollen i (annars None). Samma text som för en som saknas."""
        if behorighet is None:
            return None
        rapport_ids: set[str] = set()
        _hitta_rapporter(argument, rapport_ids)
        if vilket.startswith("resurs:floden://rapport/"):
            rapport_ids.add(urllib.parse.unquote(vilket.rsplit("/", 1)[1]))
        kontrollera: list[tuple[str, str, str]] = []
        for rid in sorted(rapport_ids):
            try:
                kontrollera.append((rid, "rapport", rapporter.hamta(rid).projekt))
            except Exception:  # noqa: BLE001 - okänd rapport: verktyget svarar själv
                continue
        bid = argument.get("bestallning_id")
        if isinstance(bid, str) and bid:
            with contextlib.suppress(Exception):  # okänd beställning: verktyget svarar själv
                kontrollera.append((bid, "beställning", rapporter.bestallning_hamta(bid).projekt))
        for id_, slag, projekt_id in kontrollera:
            if konto is None or not behorighet(konto, projekt_id, minst):
                return f"Okänd {slag} '{id_}'."
        return None


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
                    projekt = None if namn in utan_projekt else kwargs.get("projekt", PROJEKT_ID)
                    return {"dataklass": policy.klass_for(klass, projekt).value, **svar}
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
        try:
            modell = rensa_text(argument.pop("modell", None)) or tillstand.modell
            roll = rensa_text(argument.pop("roll", None)) or tillstand.roll
            projekt = argument.get("projekt", PROJEKT_ID)
            klass = klasser.get(vilket, DataKlass.OPPET)
            effektiv = (
                klass
                if vilket in utan_projekt or ctx.method != "tools/call"
                else policy.klass_for(klass, str(projekt))
            )
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
            }
            korningar: set[str] = set()
            _hitta_korningar(argument, korningar)
            rapporter_set: set[str] = set()
            _hitta_rapporter(argument, rapporter_set)
            post["rapport_id"] = rapporter_set
            if behorighet is not None and ctx.method == "tools/call" and vilket not in UTAN_ROLLKRAV:
                minst = "utredare" if vilket in SKRIVVERKTYG else "lasare"
                if konto is None:
                    text = (
                        "Inloggning krävs: den här servern kräver ett konto. Anslut över HTTP med "
                        "en personlig MCP-token eller via Claude-appens koppling (docs/KONTO.md)."
                    )
                    _skriv(logg, post, "nekad_behorighet", korningar, "inget konto", None, t0)
                    return _result_fel(text)
                if not behorighet(konto, str(projekt), minst):
                    text = (
                        f"Nekad: kontot saknar rollen {minst} i projektet. Verktyget gäller "
                        "bara projekt där du har en roll."
                    )
                    _skriv(logg, post, "nekad_behorighet", korningar, f"saknar {minst}", None, t0)
                    return _result_fel(text)
            if behorighet is not None and ctx.method in ("tools/call", "resources/read"):
                # Rapporter och beställningar hör till ett projekt: rollen prövas i det projektet, inte i
                # `projekt`-argumentet (som saknas och förvalt är demokvarteret). Okänd och nekad ser lika ut.
                nekad_text = _objekt_nekas(argument, vilket, konto, "utredare" if vilket in SKRIVVERKTYG else "lasare")
                if nekad_text is not None:
                    _skriv(logg, post, "nekad_behorighet", korningar, "objekt i annat projekt", None, t0)
                    return _result_fel(nekad_text)
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
            try:
                policy.kontrollera(str(klient["namn"]), effektiv)
            except PolicyNekad as exc:
                _skriv(logg, post, "nekad_policy", korningar, str(exc), None, t0)
                return _result_fel(str(exc))
            try:
                for kid in sorted(korningar):
                    oppenhet.kontrollera_korning(kid)
            except OppenhetNekad as exc:
                _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                return _result_fel(str(exc))
            try:
                resultat = await call_next(ctx)
            except Exception as exc:
                _skriv(logg, post, "fel", korningar, f"{type(exc).__name__}", None, t0)
                raise
            d = _till_dict(resultat)
            _hitta_korningar(d.get("structuredContent") or d.get("structured_content"), korningar)
            try:
                # Efterkontroll: allt som svaret nämner ska också vara öppet (t.ex. en rapport som
                # bygger på körningar), annars lämnas svaret inte ut.
                for kid in sorted(korningar):
                    oppenhet.kontrollera_korning(kid)
            except OppenhetNekad as exc:
                _skriv(logg, post, "nekad_oppenhet", korningar, "icke-öppna data", None, t0)
                return _result_fel(str(exc))
            _hitta_rapporter(
                d.get("structuredContent") or d.get("structured_content"), rapporter_set
            )
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
                return _result_fel(
                    f"Körloggen kan inte skrivas, så resultatet lämnas inte ut: {exc}"
                )
            return resultat
        finally:
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
        return tjanst.fragor_lista()

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
        meta = {"dataklass": policy.klass_for(DataKlass.OPPET, PROJEKT_ID).value, **meta}
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
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.korplan(projekt, scenario, fraga, analyser, avtappning_mm_h, detalj)

    @verktyg(
        "kor",
        "Startar en körning i bakgrunden och returnerar körnings-id direkt. Ange scenario (sparat) "
        "eller fraga (ref). Jobb med oförändrad nyckel hämtas ur cachen. Använd vanta för att "
        "invänta resultatet.",
    )
    def kor(
        scenario: Annotated[str | None, Field(description="Sparat scenario (slug).")] = None,
        fraga: Annotated[str | None, Field(description="Frågans ref (fragor_lista).")] = None,
        analyser: list[str] | None = None,
        avtappning_mm_h: Annotated[
            float | None, Field(description="Ledningsnätets avtappning, mm/h (standard 0).")
        ] = None,
        projekt: Projekt = PROJEKT_ID,
        modell: Modell = None,
        roll: Roll = None,
    ) -> dict[str, Any]:
        return tjanst.kor(projekt, scenario, fraga, analyser, avtappning_mm_h)

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

    _registrera_rapportverktyg(server, verktyg, rapporter, tillstand, behorighet)
    _registrera_resurser(server, tjanst)
    return server


def _registrera_rapportverktyg(
    server: MCPServer,
    verktyg: Callable[..., Callable[[Callable[..., Any]], Callable[..., Any]]],
    rapporter: RapportTjanst,
    tillstand: Sessionstillstand,
    behorighet: Behorighet | None = None,
) -> None:
    """Rapportverktygen (R1): frågekatalog, skissrapport, påståenden med belägg, kontroll, export."""
    RapportId = Annotated[str, Field(description="Rapport-id från rapport_skapa (r-<12 hex>).")]

    @verktyg(
        "fragekatalog_sok",
        "Söker i frågekatalogen (Stockholms utredningsfrågor, docs/utredningar-stockholm/fragor.json, "
        "redaktionellt underlag). Filtrera på omrade (skyfall, luft, buller, dagsljus, sol), villkor "
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
        "'skyfall' eller 'dagsljus', se bestallning_skapa) över en eller flera klara körningar. Floden ritar kartor (före, efter, "
        "skillnad; samma takmask som webben), hämtar metodernas valideringsstatus och väljer frågor "
        "ur katalogen. Svaret listar beläggsadresser du kan använda i pastaende_lagg_till.",
    )
    def rapport_skapa(
        korningar: Annotated[
            list[str], Field(description="Körnings-id (klara), t.ex. ['demo-vatten'].")
        ],
        amne: Annotated[
            str | None,
            Field(description="'skyfall' (förval) eller 'dagsljus' (dagsljus och sol)."),
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
        r = rapporter.skapa(
            projekt=projekt,
            amne=amne if amne or bestallning_id else "skyfall",
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
            # Bara rapporter i projekt där kontot har en roll (utan inloggning: alla).
            konto = tillstand.konto
            alla = rapporter.lista()
            if behorighet is not None:
                alla = [
                    r for r in alla
                    if konto is not None and behorighet(konto, str(r.get("projekt", "")), "lasare")
                ]  # fmt: skip
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
        return json.dumps(_rapport_svar(rapporter, rapport_id), ensure_ascii=False, indent=1)

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
