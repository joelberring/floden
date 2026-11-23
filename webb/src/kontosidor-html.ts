/*
 * HTML för kontosidorna Nycklar och Administration (rena funktioner, prövade i node).
 *
 * Enkel form med befintliga klasser; flödet ska designas om senare. En nyckel visas aldrig igen
 * efter att den sparats: sidan har bara "sparad <datum>, slutar på …abcd" och fälten för en ny
 * nyckel (lösenordsfält, aldrig ifyllda av klienten).
 */
import { kontoLage } from "./konto.ts";
import { esc } from "./sidor.ts";
import type {
  AdminInbjudan,
  AdminMedlem,
  AdminOrganisationDetalj,
  AdminProjektDetalj,
  McpTokenSvar,
  NyckelStatus,
  NycklarSvar,
  NyMcpToken,
  OauthAnslutning,
} from "./types.ts";

function etikett(text: string, tagg = "h2"): string {
  return `<${tagg} class="etikett">${esc(text)}</${tagg}>`;
}

export function felText(e: unknown): string {
  return e instanceof Error ? e.message : "Något gick fel.";
}

function datumTid(sek: number): string {
  return new Date(sek * 1000).toISOString().slice(0, 10);
}

/* ---------- Nycklar ---------- */

/** "Sparad 2026-11-23, slutar på …abcd", eller att ingen nyckel finns. Aldrig nyckeln. */
export function nyckelStatusText(n: NyckelStatus): string {
  if (n.sparad === null || !n.sparad_datum) return "Ingen nyckel sparad.";
  return `Sparad ${n.sparad_datum}, slutar på …${n.slutar_pa ?? ""}`;
}

function nyckelFormular(n: NyckelStatus): string {
  const falt = n.falt
    .map((f) => {
      const id = `nyckel-${n.typ}-${f.id}`;
      if (f.val.length > 0) {
        const val = f.val.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
        return `<label class="falt-rad" for="${esc(id)}">${esc(f.namn)}
          <select id="${esc(id)}" class="kontroll" name="${esc(f.id)}">${val}</select></label>`;
      }
      return `<label class="falt-rad" for="${esc(id)}">${esc(f.namn)}
        <input id="${esc(id)}" class="kontroll" type="password" name="${esc(f.id)}" autocomplete="off" spellcheck="false" required /></label>`;
    })
    .join("");
  const sparad = n.sparad !== null;
  return `
    <section class="nyckel-avsnitt" data-nyckel-typ="${esc(n.typ)}">
      <div class="sektionsrad linje-under-stark">${etikett(n.namn)}</div>
      <p class="avsnitt-ingress">${esc(n.beskrivning)}</p>
      <p class="nyckel-status" data-status><strong>${esc(nyckelStatusText(n))}</strong></p>
      <form data-nyckelform="${esc(n.typ)}" autocomplete="off">
        ${falt}
        <div class="knapp-par">
          <button type="submit" class="knapp knapp-svart">${sparad ? "Ersätt nyckeln" : "Spara nyckeln"}</button>
          ${sparad ? `<button type="button" class="knapp" data-ta-bort-nyckel="${esc(n.typ)}">Ta bort</button>` : ""}
        </div>
      </form>
    </section>`;
}

/* ---------- MCP-token ---------- */

/** Vad avsnittet MCP-token ritas av: listan, en nyss skapad token (visas en enda gång) och ett fel. */
export interface McpAvsnitt {
  svar: McpTokenSvar | null;
  ny: NyMcpToken | null;
  fel: string | null;
  /** Appar kontot godkänt via OAuth (Claude-appen); utelämnad eller null medan de hämtas. */
  appar?: OauthAnslutning[] | null;
}

/** Kommandot för Claude Code. `token` är den nyss skapade token, annars platshållaren `<token>`. */
export function mcpKommando(mcpUrl: string, token: string | null): string {
  return `claude mcp add --transport http floden ${mcpUrl} --header "Authorization: Bearer ${token ?? "<token>"}"`;
}

export function mcpTokenAvsnitt(a: McpAvsnitt): string {
  const huvud = `<div class="sektionsrad linje-under-stark">${etikett("MCP-token")}</div>
      <p class="avsnitt-ingress">Med en personlig token kan Claude Code (eller ett annat verktyg) styra Floden som dig, via MCP. Du ser bara dina egna projekt, och tokenen gäller bara <code>/mcp</code>. Tokenen visas en enda gång, direkt efter att du skapat den; sedan finns bara namn, datum och de fyra sista tecknen kvar.</p>`;
  if (a.fel) return `<section class="nyckel-avsnitt" data-mcp-token>${huvud}<p class="hjalp-ruta" role="alert">${esc(a.fel)}</p></section>`;
  if (!a.svar) return `<section class="nyckel-avsnitt" data-mcp-token>${huvud}<p class="dampad">Hämtar …</p></section>`;
  const svar = a.svar;
  const kommando = mcpKommando(svar.mcp_url, a.ny?.token ?? null);
  const nyttMeddelande = a.ny
    ? `<p class="hjalp-ruta" role="status" data-ny-mcp-token>Token <strong>${esc(a.ny.namn)}</strong> är skapad. Kopiera kommandot nu: tokenen visas inte igen.</p>`
    : "";
  const rader = svar.tokens
    .map(
      (t) => `<tr>
        <td>${esc(t.namn)}</td><td>${esc(t.skapad_datum ?? "–")}</td><td>${esc(t.senast_anvand_datum ?? "Aldrig")}</td><td>…${esc(t.slutar_pa)}</td>
        <td><button type="button" class="knapp knapp-liten" data-aterkalla-mcp="${esc(t.id)}" data-aterkalla-namn="${esc(t.namn)}">Återkalla</button></td></tr>`,
    )
    .join("");
  const tabell = svar.tokens.length
    ? `<div class="tabell-ruta"><table class="data-tabell">
        <thead><tr><th scope="col">Namn</th><th scope="col">Skapad</th><th scope="col">Senast använd</th><th scope="col">Slutar på</th><th scope="col">Åtgärd</th></tr></thead>
        <tbody>${rader}</tbody></table></div>`
    : `<p class="dampad">Inga token än.</p>`;
  const full = svar.tokens.length >= svar.max;
  const formular = full
    ? `<p class="hjalp-ruta" role="note">Du har ${svar.max} token, det högsta antalet. Återkalla en för att skapa en ny.</p>`
    : `<form data-mcp-token-form class="knapp-par" autocomplete="off">
        <label class="falt-rad" for="mcp-token-namn">Namn (t.ex. vilken dator)
          <input id="mcp-token-namn" class="kontroll" type="text" name="namn" maxlength="60" required autocomplete="off" /></label>
        <button type="submit" class="knapp knapp-svart">Skapa token</button>
      </form>`;
  return `<section class="nyckel-avsnitt" data-mcp-token>
      ${huvud}
      ${nyttMeddelande}
      <p class="avsnitt-ingress">Lägg till Floden i Claude Code med kommandot${a.ny ? "" : " (fyll i token du skapar nedan)"}:</p>
      <pre class="kodruta"><code data-mcp-kommando>${esc(kommando)}</code></pre>
      ${a.ny ? `<div class="knapp-par"><button type="button" class="knapp" data-kopiera-mcp>Kopiera kommandot</button></div>` : ""}
      ${tabell}
      ${formular}
    </section>`;
}

export function anslutnaApparAvsnitt(a: McpAvsnitt): string {
  const mcpUrl = a.svar?.mcp_url ?? "";
  const huvud = `<div class="sektionsrad linje-under-stark">${etikett("Anslutna appar")}</div>
      <p class="avsnitt-ingress">Claude-appen ansluts utan token: lägg till en egen koppling (Inställningar, Kopplingar, Lägg till egen koppling) med adressen <code>${esc(mcpUrl)}</code>. Du loggar in här och godkänner appen. Här kan du ta bort tillgången igen.</p>`;
  if (!a.appar) return `<section class="nyckel-avsnitt" data-anslutna-appar>${huvud}<p class="dampad">Hämtar …</p></section>`;
  const rader = a.appar
    .map(
      (p) => `<tr>
        <td>${esc(p.namn)}</td><td>${esc(p.skapad_datum ?? "–")}</td><td>${esc(p.senast_anvand_datum ?? "Aldrig")}</td>
        <td><button type="button" class="knapp knapp-liten" data-aterkalla-app="${esc(p.id)}" data-aterkalla-namn="${esc(p.namn)}">Ta bort tillgång</button></td></tr>`,
    )
    .join("");
  const tabell = a.appar.length
    ? `<div class="tabell-ruta"><table class="data-tabell">
        <thead><tr><th scope="col">App</th><th scope="col">Godkänd</th><th scope="col">Senast använd</th><th scope="col">Åtgärd</th></tr></thead>
        <tbody>${rader}</tbody></table></div>`
    : `<p class="dampad">Inga anslutna appar.</p>`;
  return `<section class="nyckel-avsnitt" data-anslutna-appar>${huvud}${tabell}</section>`;
}

export function sidaNycklar(
  svar: NycklarSvar | null,
  fel: string | null = null,
  mcp?: McpAvsnitt,
): string {
  const l = kontoLage();
  let innehall: string;
  if (l.lage === "av") {
    innehall = `<p class="hjalp-ruta" role="note">Nycklar sparas på servern och kräver inloggning. Körs Floden lokalt utan inloggning används nycklarna i <code>config.local.toml</code> eller miljön (se README).</p>`;
  } else if (fel) {
    innehall = `<p class="hjalp-ruta" role="alert">${esc(fel)}</p>`;
  } else if (!svar) {
    innehall = `<p class="dampad">Hämtar …</p>`;
  } else if (!svar.paslagen) {
    innehall = `<p class="hjalp-ruta" role="note">${esc(svar.text ?? "Nyckelhanteringen är inte påslagen på servern.")}</p>`;
  } else {
    innehall = `<p class="hjalp-ruta" role="status" data-meddelande hidden></p>${svar.typer.map(nyckelFormular).join("")}`;
  }
  return `
    <div class="sidbredd innehall">
      <h1 class="sidrubrik stor">Nycklar</h1>
      <p class="ingress">Dina egna API-nycklar. De lagras krypterade på servern och visas aldrig igen efter att du sparat dem: du ser bara när de sparades och hur de slutar. Att ersätta eller ta bort en nyckel gör du här.</p>
      ${innehall}
      ${mcp && l.lage !== "av" ? mcpTokenAvsnitt(mcp) + anslutnaApparAvsnitt(mcp) : ""}
    </div>`;
}

/* ---------- Administration ---------- */

function rollVal(roller: string[], vald: string, attribut: string): string {
  return `<select class="kontroll" ${attribut}>${roller
    .map((r) => `<option value="${esc(r)}"${r === vald ? " selected" : ""}>${esc(r)}</option>`)
    .join("")}</select>`;
}

function medlemsRad(pid: string, roller: string[], m: AdminMedlem): string {
  return `<tr>
    <td>${esc(m.namn)}${m.sparrad ? ` <span class="chip">Spärrad</span>` : ""}</td>
    <td>${esc(m.epost ?? "–")}</td>
    <td>${rollVal(roller, m.roll, `data-roll-projekt="${esc(pid)}" data-roll-konto="${esc(m.konto_id)}" aria-label="Roll för ${esc(m.namn)}"`)}</td>
    <td>
      <button type="button" class="knapp knapp-liten" data-ta-bort-roll="${esc(pid)}|${esc(m.konto_id)}">Ta bort från projektet</button>
      <button type="button" class="knapp knapp-liten" data-sparra="${esc(m.konto_id)}" data-sparra-till="${m.sparrad ? "nej" : "ja"}">${m.sparrad ? "Öppna kontot" : "Spärra kontot"}</button>
    </td></tr>`;
}

const INBJUDAN_TEXT: Record<AdminInbjudan["status"], string> = {
  oppen: "Öppen",
  anvand: "Använd",
  utgangen: "Utgången",
};

function inbjudanRad(i: AdminInbjudan): string {
  return `<tr><td>${esc(i.epost)}</td><td>${esc(i.roll)}</td><td>${esc(INBJUDAN_TEXT[i.status])}</td><td>${esc(datumTid(i.giltig_till))}</td></tr>`;
}

/** Kryssrutan "Synligt för organisationen <namn>". Bara projektets utredare och skaparen kan ändra. */
export function delningHtml(p: AdminProjektDetalj["projekt"]): string {
  const synlig = p.organisation_synlig === true;
  const kan = p.kan_andra_delning === true;
  return `<p class="delning" data-delning-rad>
    <label><input type="checkbox" data-delning="${esc(p.id)}"${synlig ? " checked" : ""}${kan ? "" : " disabled"} />
      Synligt för organisationen ${esc(p.organisation)}</label>
    <span class="dampad">${synlig ? "Organisationens ägare och administratörer utreder i projektet, ledare läser." : "Privat: bara de som har en roll i projektet ser det."}${kan ? "" : " Bara projektets utredare och skaparen ändrar detta."}</span>
  </p>`;
}

export function adminProjektHtml(d: AdminProjektDetalj): string {
  const p = d.projekt;
  const medlemmar = d.medlemmar.length
    ? `<div class="tabell-ruta"><table class="data-tabell">
        <thead><tr><th scope="col">Namn</th><th scope="col">E-post</th><th scope="col">Roll</th><th scope="col">Åtgärd</th></tr></thead>
        <tbody>${d.medlemmar.map((m) => medlemsRad(p.id, d.roller, m)).join("")}</tbody></table></div>`
    : `<p class="dampad">Inga medlemmar än.</p>`;
  const inbjudningar = d.inbjudningar.length
    ? `<div class="tabell-ruta"><table class="data-tabell">
        <thead><tr><th scope="col">E-post</th><th scope="col">Roll</th><th scope="col">Status</th><th scope="col">Giltig till</th></tr></thead>
        <tbody>${d.inbjudningar.map(inbjudanRad).join("")}</tbody></table></div>`
    : `<p class="dampad">Inga inbjudningar.</p>`;
  return `
    <section class="admin-projekt" data-admin-projekt="${esc(p.id)}">
      <div class="sektionsrad linje-under-stark">${etikett(`${p.organisation}: ${p.namn}`)}</div>
      ${delningHtml(p)}
      <form data-bjud-in="${esc(p.id)}" class="knapp-par">
        <label class="falt-rad">E-postadress <input class="kontroll" type="email" name="epost" required autocomplete="off" /></label>
        <label class="falt-rad">Roll ${rollVal(d.roller, d.roller[0] ?? "lasare", 'name="roll"')}</label>
        <button type="submit" class="knapp knapp-svart">Bjud in</button>
      </form>
      ${etikett("Medlemmar", "h3")}
      ${medlemmar}
      ${etikett("Inbjudningar", "h3")}
      ${inbjudningar}
    </section>`;
}

function orgMedlemsRad(oid: string, d: AdminOrganisationDetalj, m: AdminMedlem): string {
  const tillatna = d.organisation.tillatna_roller;
  // Raden kan ändras bara om utföraren får ge den nuvarande rollen (en organisationsadmin rör inte en annan admin).
  const redigerbar = tillatna.includes(m.roll);
  const namn = `${esc(m.namn)}${m.sparrad ? ` <span class="chip">Spärrad</span>` : ""}`;
  const roll = redigerbar
    ? rollVal(tillatna, m.roll, `data-org-roll="${esc(oid)}" data-org-konto="${esc(m.konto_id)}" aria-label="Roll för ${esc(m.namn)}"`)
    : esc(m.roll);
  const atgard = redigerbar
    ? `<button type="button" class="knapp knapp-liten" data-org-ta-bort="${esc(oid)}|${esc(m.konto_id)}">Ta bort från organisationen</button>`
    : "–";
  return `<tr><td>${namn}</td><td>${esc(m.epost ?? "–")}</td><td>${roll}</td><td>${atgard}</td></tr>`;
}

export function adminOrganisationHtml(d: AdminOrganisationDetalj): string {
  const o = d.organisation;
  const medlemmar = d.medlemmar.length
    ? `<div class="tabell-ruta"><table class="data-tabell">
        <thead><tr><th scope="col">Namn</th><th scope="col">E-post</th><th scope="col">Roll</th><th scope="col">Åtgärd</th></tr></thead>
        <tbody>${d.medlemmar.map((m) => orgMedlemsRad(o.id, d, m)).join("")}</tbody></table></div>`
    : `<p class="dampad">Inga medlemmar än.</p>`;
  const inbjudningar = d.inbjudningar.length
    ? `<div class="tabell-ruta"><table class="data-tabell">
        <thead><tr><th scope="col">E-post</th><th scope="col">Roll</th><th scope="col">Status</th><th scope="col">Giltig till</th></tr></thead>
        <tbody>${d.inbjudningar.map(inbjudanRad).join("")}</tbody></table></div>`
    : `<p class="dampad">Inga inbjudningar.</p>`;
  const namnForm = o.far_byta_namn
    ? `<form data-org-namn="${esc(o.id)}" class="knapp-par">
        <label class="falt-rad">Organisationens namn <input class="kontroll" type="text" name="namn" value="${esc(o.namn)}" maxlength="120" required autocomplete="off" /></label>
        <button type="submit" class="knapp">Byt namn</button>
      </form>`
    : "";
  const bjud = o.tillatna_roller.length
    ? `<form data-org-bjud-in="${esc(o.id)}" class="knapp-par">
        <label class="falt-rad">E-postadress <input class="kontroll" type="email" name="epost" required autocomplete="off" /></label>
        <label class="falt-rad">Roll ${rollVal(o.tillatna_roller, o.tillatna_roller[0] ?? "medlem", 'name="roll"')}</label>
        <button type="submit" class="knapp knapp-svart">Bjud in till organisationen</button>
      </form>`
    : "";
  return `
    <section class="admin-organisation" data-admin-organisation="${esc(o.id)}">
      <div class="sektionsrad linje-under-stark">${etikett(`Organisation: ${o.namn}`)}</div>
      <p class="avsnitt-ingress">Din roll: <strong>${esc(o.din_roll)}</strong>. Organisationsadmin och ägare utreder i alla organisationens projekt; ledare läser dem.</p>
      ${namnForm}
      ${bjud}
      ${etikett("Medlemmar och roller", "h3")}
      ${medlemmar}
      ${etikett("Inbjudningar till organisationen", "h3")}
      ${inbjudningar}
    </section>`;
}

export function sidaAdministration(
  projekt: AdminProjektDetalj[] | null,
  fel: string | null = null,
  meddelande = "",
  organisationer: AdminOrganisationDetalj[] = [],
): string {
  let innehall: string;
  if (fel) innehall = `<p class="hjalp-ruta" role="alert">${esc(fel)}</p>`;
  else if (!projekt) innehall = `<p class="dampad">Hämtar …</p>`;
  else if (projekt.length === 0) innehall = `<p class="dampad">Du administrerar inga projekt än.</p>`;
  else innehall = projekt.map(adminProjektHtml).join("");
  const organisation = fel || !projekt ? "" : organisationer.map(adminOrganisationHtml).join("");
  return `
    <div class="sidbredd innehall">
      <h1 class="sidrubrik stor">Administration</h1>
      <p class="ingress">Bjud in personer till organisationen eller till ett projekt, ändra roller och spärra konton. En inbjudan tas emot när personen loggar in med den adressen; <strong>lasare</strong> får läsa, <strong>utredare</strong> får även köra.</p>
      ${meddelande ? `<p class="hjalp-ruta" role="status">${esc(meddelande)}</p>` : ""}
      ${organisation}
      ${innehall}
    </div>`;
}

