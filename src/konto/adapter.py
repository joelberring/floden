"""Ramverksoberoende HTTP-adapter för kontosystemet.

Adaptern känner inte till `http.server`: den tar (metod, sökväg, query, headers, kropp) och ger
(status, headers, kropp). Headers är en lista av par eftersom ett svar kan ha flera `Set-Cookie`.

Ändpunkter (alla under `/konto/`):

- `GET  /konto/logga-in/<leverantör>[?atervand=/sida]` startar kodflödet (302 till leverantören)
- `GET  /konto/callback/<leverantör>` tar emot koden, kontrollerar allt och sätter sessionskakor
- `POST /konto/logga-ut` återkallar sessionen (CSRF-header, eller exakt Origin)
- `GET  /konto/jag` visar vem som är inloggad (401 om ingen)
- `GET/PUT/DELETE /konto/nycklar[/<typ>]` kontots egna API-nycklar, krypterade (`konto.nycklar`)
- `/konto/admin/...` inbjudningar, roller och spärr för admin (`konto.kontoapi`)

Med `inloggning = "av"` kräver ingenting inloggning och `/konto/jag` svarar `{"inloggning": "av"}`.
Flödets tillstånd (state, nonce, PKCE-verifierare) ligger i databasen, inte i en kaka som
webbläsaren kan läsa; kakan `*-inloggning` är bara en bindning till den webbläsare som började.
Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af): `google-login.ts`, `google-oidc.ts`,
`organizer-account-cookies.ts`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from konto.databas import Databas
from konto.hamtare import Hamtare, UrllibHamtare
from konto.konfig import KontoKonfig
from konto.leverantor import Leverantor, bygg_leverantor
from konto.register import Register, _roll_niva
from konto.sessioner import (
    CSRF_HEADER,
    Inloggad,
    Sessioner,
    Utfall,
    kaknamn,
    las_kakor,
    satt_kaka,
)
from konto.tilldelning import InloggningNekad, logga_in_identitet

if TYPE_CHECKING:
    from konto.nycklar import NyckelForrad

logg = logging.getLogger("konto")

Svar = tuple[int, list[tuple[str, str]], bytes]
INLOGGNING_GILTIG_S = 600
MAX_PAGAENDE = 2000
_NAMN = re.compile(r"^[a-z0-9_-]{1,40}$")
_STATE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
_KOD = re.compile(r"^[\x21-\x7e]{1,2048}$")
_OSAKRA_METODER = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Forfragan:
    """En HTTP-förfrågan i ramverksoberoende form (rubriknamn i gemener)."""

    metod: str
    sokvag: str
    query: Mapping[str, Sequence[str]]
    headers: Mapping[str, str]
    kropp: bytes = b""

    @classmethod
    def fran_ra(
        cls,
        metod: str,
        ra_sokvag: str,
        headers: Mapping[str, str],
        kropp: bytes = b"",
    ) -> Forfragan:
        """Bygger ur `self.path` (med query) och rubriker, t.ex. `dict(self.headers.items())`."""
        delar = urlsplit(ra_sokvag)
        return cls(
            metod=metod.upper(),
            sokvag=delar.path or "/",
            query=parse_qs(delar.query, keep_blank_values=False),
            headers={k.lower(): v for k, v in headers.items()},
            kropp=kropp,
        )


@dataclass(frozen=True)
class Kontroll:
    """Resultatet av `kraver_inloggning`. Är `tillaten` falsk ska `svar` skickas oförändrat."""

    tillaten: bool
    inloggad: Inloggad | None = None
    svar: Svar | None = None


def _json(status: int, data: Mapping[str, Any], extra: Sequence[tuple[str, str]] = ()) -> Svar:
    rubriker = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        *extra,
    ]
    return status, rubriker, json.dumps(data, ensure_ascii=False).encode("utf-8")


def _fel(status: int, kod: str, text: str, extra: Sequence[tuple[str, str]] = ()) -> Svar:
    return _json(status, {"fel": text, "status": status, "kod": kod}, extra)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def saker_atervand(varde: str | None) -> str:
    """Bara en sökväg på den här servern: enkel `/` först, inga kontrolltecken eller `\\`."""
    if (
        not varde
        or len(varde) > 512
        or not varde.startswith("/")
        or varde.startswith("//")
        or "\\" in varde
        or any(ord(t) < 0x20 or ord(t) == 0x7F for t in varde)
    ):
        return "/"
    return varde


class KontoAdapter:
    """Kontosystemets ingång för webbservern."""

    def __init__(
        self,
        konfig: KontoKonfig,
        *,
        register: Register | None = None,
        hamtare: Hamtare | None = None,
        klocka: Callable[[], float] = time.time,
        leverantorer: Mapping[str, Leverantor] | None = None,
        nyckelhemlighet: str | None = None,
    ) -> None:
        self.konfig = konfig
        self._klocka = klocka
        self._kak = kaknamn(konfig.cookie_secure)
        self.register: Register | None = None
        self.sessioner: Sessioner | None = None
        self.nycklar: NyckelForrad | None = None
        self._klienter: dict[str, Any] = {}
        if not konfig.aktiv:
            return
        # Importen sker först här: läget "av" kräver varken PyJWT eller databas.
        from konto.nycklar import NyckelForrad
        from konto.oidc import OidcKlient

        self.register = register or Register(Databas(konfig.databas), klocka)
        # Hemligheten för nyckelkrypteringen: FLODEN_NYCKELHEMLIGHET (saknas den är sidan avstängd).
        self.nycklar = NyckelForrad(self.register, nyckelhemlighet, klocka)
        self.sessioner = Sessioner(self.register, konfig)
        levs = (
            dict(leverantorer)
            if leverantorer is not None
            else {
                namn: bygg_leverantor(inst)
                for namn, inst in konfig.leverantorer.items()
                if inst.aktiv
            }
        )
        for namn, lev in levs.items():
            self._klienter[namn] = OidcKlient(
                lev, konfig.redirect_uri(namn), hamtare or UrllibHamtare(), klocka
            )

    @property
    def aktiv(self) -> bool:
        return self.konfig.aktiv

    def ager_sokvag(self, sokvag: str) -> bool:
        return sokvag == "/konto" or sokvag.startswith("/konto/")

    # --- webbserverns kontroll ----------------------------------------------------------------

    def kraver_inloggning(self, request: Forfragan) -> Kontroll:
        """Anropas före varje skyddad ändpunkt. Är `tillaten` falsk skickas `svar` oförändrat."""
        oppen = request.metod == "OPTIONS" or request.sokvag in self.konfig.oppna_sokvagar
        if not self.aktiv or oppen:
            return Kontroll(tillaten=True)
        assert self.sessioner is not None  # noqa: S101 - aktiv => skapad i __init__
        osaker = request.metod in _OSAKRA_METODER
        kakor = las_kakor(request.headers.get("cookie"))
        res = self.sessioner.kontrollera(
            kakor.get(self._kak.session),
            kraver_csrf=osaker,
            csrf_kaka=kakor.get(self._kak.csrf),
            csrf_header=request.headers.get(CSRF_HEADER),
        )
        if res.utfall is Utfall.EJ_INLOGGAD:
            return Kontroll(False, svar=_fel(401, "ej-inloggad", "Inloggning krävs"))
        if res.utfall is Utfall.CSRF or (osaker and not self._origin_ok(request)):
            return Kontroll(False, svar=_fel(403, "csrf", "Ogiltig CSRF-kontroll"))
        return Kontroll(tillaten=True, inloggad=res.inloggad)

    def kraver_projektbehorighet(
        self, request: Forfragan, projekt_id: str, minst: str = "lasare"
    ) -> Kontroll:
        """Inloggning och en uttrycklig projektroll. Medlemskap och superadmin räcker inte.

        Undantag: det påhittade demoprojektet (`[konto] demoprojekt`, inga verkliga data), där
        varje inloggat konto har rollen `demoprojekt_roll` (beslut 2026-11-23, Joel kan ändra)."""
        kontroll = self.kraver_inloggning(request)
        if not kontroll.tillaten or kontroll.inloggad is None:
            return kontroll
        assert self.register is not None  # noqa: S101
        if self.register.har_projektroll(kontroll.inloggad.konto_id, projekt_id, minst):
            return kontroll
        demo_roll = self.konfig.demoprojekt_roll
        if (
            projekt_id == self.konfig.demoprojekt
            and demo_roll != "av"
            and _roll_niva(demo_roll) >= _roll_niva(minst)
        ):
            return kontroll
        logg.info("projekt nekat konto=%s projekt=%s", kontroll.inloggad.konto_id, projekt_id)
        svar = _fel(403, "ingen-behorighet", "Saknar behörighet till projektet")
        return Kontroll(False, svar=svar)

    def projekt_for_konto(self, konto_id: str) -> list[dict[str, str]]:
        """Registrets projekt där kontot har en uttrycklig roll (id, namn, roll). Demokvarteret
        ingår inte här: det följer `demoprojekt_roll` (se `demoroll`)."""
        if self.register is None:
            return []
        proj = self.register.sammanfattning(konto_id)["projekt"]
        assert isinstance(proj, list)  # noqa: S101
        return [{"id": p["id"], "namn": p["namn"], "roll": p["roll"]} for p in proj]

    def demoroll(self) -> str | None:
        """Rollen varje inloggat konto har på demoprojektet, eller None när den är `av`."""
        roll = self.konfig.demoprojekt_roll
        return None if roll == "av" else roll

    def _origin_ok(self, request: Forfragan) -> bool:
        origin = request.headers.get("origin")
        return origin is None or origin == self.konfig.origin

    # --- /konto/* ---------------------------------------------------------------------------------

    def hantera_konto(
        self,
        metod: str,
        sokvag: str,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        kropp: bytes = b"",
    ) -> Svar:
        """Hanterar `/konto/...`. Andra sökvägar ger 404."""
        req = Forfragan(
            metod.upper(),
            sokvag.rstrip("/") or "/",
            query,
            {k.lower(): v for k, v in headers.items()},
            kropp,
        )
        delar = [d for d in req.sokvag.split("/") if d]
        if not delar or delar[0] != "konto":
            return _fel(404, "okand", "Okänd ändpunkt")
        if len(delar) == 2 and delar[1] == "jag":
            return self._jag(req) if req.metod == "GET" else _fel(405, "metod", "Metod ej tillåten")
        if not self.aktiv:
            return _fel(404, "inloggning-av", "Inloggning är avstängd")
        if len(delar) == 3 and delar[1] in ("logga-in", "callback"):
            if req.metod != "GET":
                return _fel(405, "metod", "Metod ej tillåten")
            klient = self._klienter.get(delar[2]) if _NAMN.match(delar[2]) else None
            if klient is None:
                return _fel(404, "okand-leverantor", "Okänd inloggningsleverantör")
            if delar[1] == "logga-in":
                return self._logga_in(delar[2], klient, req)
            return self._callback(delar[2], klient, req)
        if len(delar) == 2 and delar[1] == "logga-ut":
            if req.metod != "POST":
                return _fel(405, "metod", "Metod ej tillåten")
            return self._logga_ut(req)
        if len(delar) >= 2 and delar[1] in ("nycklar", "admin"):
            from konto import kontoapi

            if delar[1] == "nycklar":
                return kontoapi.hantera_nycklar(self, req, delar[2:])
            return kontoapi.hantera_admin(self, req, delar[2:])
        return _fel(404, "okand", "Okänd ändpunkt")

    def _jag(self, req: Forfragan) -> Svar:
        if not self.aktiv:
            return _json(200, {"inloggning": "av"})
        assert self.sessioner is not None and self.register is not None  # noqa: S101
        kakor = las_kakor(req.headers.get("cookie"))
        res = self.sessioner.kontrollera(kakor.get(self._kak.session))
        if res.inloggad is None:
            return _fel(401, "ej-inloggad", "Inloggning krävs")
        i = res.inloggad
        return _json(
            200,
            {
                "inloggning": "pa",
                "konto": {
                    "id": i.konto_id,
                    "namn": i.namn,
                    "epost": i.epost,
                    "superadmin": i.superadmin,
                    "admin": self.register.ar_admin(i.konto_id),
                    "utgar": i.utgar,
                },
                **self.register.sammanfattning(i.konto_id),
            },
        )

    def _logga_in(self, namn: str, klient: Any, req: Forfragan) -> Svar:
        from konto.oidc import nytt_slump

        assert self.register is not None  # noqa: S101
        state, nonce, verifierare, bindning = (nytt_slump() for _ in range(4))
        atervand = saker_atervand((req.query.get("atervand") or [""])[0])
        nu = self.register.nu()
        with self.register.db.transaktion() as c:
            c.execute(
                "DELETE FROM pagaende_inloggning WHERE skapad < ?", (nu - INLOGGNING_GILTIG_S,)
            )
            antal = c.execute("SELECT COUNT(*) FROM pagaende_inloggning").fetchone()[0]
            if antal >= MAX_PAGAENDE:  # skydd mot att fylla databasen utan inloggning
                c.execute(
                    "DELETE FROM pagaende_inloggning WHERE state_hash IN"
                    " (SELECT state_hash FROM pagaende_inloggning ORDER BY skapad LIMIT ?)",
                    (MAX_PAGAENDE // 4,),
                )
            c.execute(
                "INSERT INTO pagaende_inloggning (state_hash, leverantor, nonce, code_verifier,"
                " bindning_hash, atervand, skapad) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_hash(state), namn, nonce, verifierare, _hash(bindning), atervand, nu),
            )
        try:
            url = klient.auktoriserings_url(state, nonce, verifierare)
        except Exception as exc:  # noqa: BLE001 - OidcFel eller nätverksfel: fast felkod
            logg.warning("inloggning kunde inte startas leverantor=%s kod=%s", namn, _kod(exc))
            return _fel(502, "leverantor", "Inloggningsleverantören svarar inte")
        kaka = satt_kaka(
            self._kak.inloggning,
            bindning,
            secure=self.konfig.cookie_secure,
            http_only=True,
            max_alder_s=INLOGGNING_GILTIG_S,
        )
        return 302, [("Location", url), ("Cache-Control", "no-store"), kaka], b""

    def _callback(self, namn: str, klient: Any, req: Forfragan) -> Svar:
        from konto.oidc import OidcFel

        assert self.register is not None and self.sessioner is not None  # noqa: S101
        rensa = self._rensa_inloggningskaka()
        state = (req.query.get("state") or [""])[0]
        kod = (req.query.get("code") or [""])[0]
        bindning = las_kakor(req.headers.get("cookie")).get(self._kak.inloggning, "")
        if not _STATE.match(state) or not bindning:
            return _fel(400, "state", "Ogiltig eller saknad inloggningsbegäran", rensa)
        nu = self.register.nu()
        with self.register.db.transaktion() as c:
            rad = c.execute(
                "SELECT * FROM pagaende_inloggning WHERE state_hash = ?", (_hash(state),)
            ).fetchone()
            if rad is not None:  # engångs: raden förbrukas oavsett utfall
                c.execute("DELETE FROM pagaende_inloggning WHERE state_hash = ?", (_hash(state),))
        if (
            rad is None
            or rad["leverantor"] != namn
            or nu - rad["skapad"] > INLOGGNING_GILTIG_S
            or not hmac.compare_digest(rad["bindning_hash"], _hash(bindning))
        ):
            logg.warning("inloggning nekad leverantor=%s kod=state", namn)
            return _fel(400, "state", "Ogiltig eller utgången inloggningsbegäran", rensa)
        if "error" in req.query:
            logg.warning("inloggning avbruten leverantor=%s kod=idp-fel", namn)
            return _fel(400, "idp-fel", "Inloggningen avbröts eller nekades", rensa)
        if not _KOD.match(kod):
            return _fel(400, "kod", "Saknad inloggningskod", rensa)
        try:
            identitet = klient.byt_kod(kod, rad["code_verifier"], rad["nonce"])
        except OidcFel as exc:
            logg.warning("inloggning nekad leverantor=%s kod=%s", namn, exc.kod)
            status = 502 if exc.kod in ("token-utbyte", "discovery", "jwks") else 403
            return _fel(status, exc.kod, "Inloggningen kunde inte godkännas", rensa)
        try:
            resultat = logga_in_identitet(self.register, identitet, klient.leverantor, self.konfig)
        except InloggningNekad as exc:
            logg.warning("inloggning nekad leverantor=%s kod=%s", namn, exc.kod)
            return _fel(403, exc.kod, "Kontot har inte tillgång", rensa)
        session = self.sessioner.skapa(resultat.konto_id)
        alder = max(0, session.utgar - self.register.nu())
        sakra = self.konfig.cookie_secure
        kakor = [
            satt_kaka(
                self._kak.session, session.token, secure=sakra, http_only=True, max_alder_s=alder
            ),
            # CSRF-kakan läses av webbklienten och skickas som header: därför inte HttpOnly.
            satt_kaka(
                self._kak.csrf, session.csrf, secure=sakra, http_only=False, max_alder_s=alder
            ),
            *rensa,
        ]
        return 302, [("Location", rad["atervand"]), ("Cache-Control", "no-store"), *kakor], b""

    def _logga_ut(self, req: Forfragan) -> Svar:
        assert self.sessioner is not None  # noqa: S101
        kakor = las_kakor(req.headers.get("cookie"))
        res = self.sessioner.kontrollera(
            kakor.get(self._kak.session),
            kraver_csrf=True,
            csrf_kaka=kakor.get(self._kak.csrf),
            csrf_header=req.headers.get(CSRF_HEADER),
        )
        rensa = [
            self._rensa_kaka(self._kak.session, True),
            self._rensa_kaka(self._kak.csrf, False),
        ]
        if res.utfall is Utfall.CSRF:
            # Att logga ut någon är ofarligt, så exakt Origin duger i stället för CSRF-paret
            # (som O-Tid). En annan webbplats kan inte skicka exakt vår Origin.
            if req.headers.get("origin") != self.konfig.origin:
                return _fel(403, "csrf", "Ogiltig CSRF-kontroll")
            res = self.sessioner.kontrollera(kakor.get(self._kak.session))
        if res.inloggad is not None:
            self.sessioner.aterkalla(res.inloggad.session_id)
            logg.info("utloggning konto=%s", res.inloggad.konto_id)
        return _json(200, {"status": "utloggad"}, rensa)

    def _rensa_kaka(self, namn: str, http_only: bool) -> tuple[str, str]:
        return satt_kaka(
            namn, "", secure=self.konfig.cookie_secure, http_only=http_only, max_alder_s=0
        )

    def _rensa_inloggningskaka(self) -> list[tuple[str, str]]:
        return [self._rensa_kaka(self._kak.inloggning, True)]


def _kod(exc: Exception) -> str:
    return str(getattr(exc, "kod", type(exc).__name__))
