/*
 * HTML för kontosidorna Nycklar och Administration (rena funktioner, prövade i node).
 *
 * Enkel form med befintliga klasser; flödet ska designas om senare. En nyckel visas aldrig igen
 * efter att den sparats: sidan har bara "sparad <datum>, slutar på …abcd" och fälten för en ny
 * nyckel (lösenordsfält, aldrig ifyllda av klienten).
 */
import { kontoLage, rollNamn } from "./konto.ts";
import { esc } from "./sidor.ts";
import type {
  AdminInbjudan,
  AdminMedlem,
  AdminOrganisationDetalj,
  AdminProjektDetalj,
  McpTokenSvar,
  NyckelFaltInfo,
  NyckelProdukt,
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

/** Val med begripliga etiketter som alternativknappar (förvalet först markerat), annars en lista. */
function valFalt(n: NyckelStatus, f: NyckelFaltInfo): string {
  const id = `nyckel-${n.typ}-${f.id}`;
  const etiketter = f.val_etiketter ?? {};
  if (Object.keys(etiketter).length > 0) {
    const forval = f.standard ?? f.val[0] ?? "";
    const knappar = f.val
      .map(
        (v, i) => `<label class="val-rad"><input type="radio" name="${esc(f.id)}" id="${esc(id)}-${i}" value="${esc(v)}"${v === forval ? " checked" : ""} /> ${esc(etiketter[v] ?? v)}</label>`,
      )
      .join("");
    return `<fieldset class="falt-rad val-grupp" data-val="${esc(f.id)}"><legend>${esc(f.namn)}</legend>${knappar}</fieldset>`;
  }
  const val = f.val.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  return `<label class="falt-rad" for="${esc(id)}">${esc(f.namn)}
          <select id="${esc(id)}" class="kontroll" name="${esc(f.id)}">${val}</select></label>`;
}

/** Tabellen "Produkter att beställa på Geotorget": Produkt | Används till | Behövs/Valfri. */
export function produktTabell(n: NyckelStatus): string {
  const rader: NyckelProdukt[] = n.produkter ?? [];
  if (rader.length === 0) return "";
  const lank = n.geotorget_url
    ? ` <a href="${esc(n.geotorget_url)}" target="_blank" rel="noopener noreferrer">Geotorget</a>.`
    : "";
  const tr = rader
    .map(
      (p) => `<tr data-produkt="${esc(p.roll)}">
        <td>${esc(p.namn)}</td><td>${esc(p.anvands_till)}${p.anmarkning ? ` <span class="dampad">${esc(p.anmarkning)}</span>` : ""}</td>
        <td>${p.behovs ? "Behövs" : "Valfri"}</td></tr>`,
    )
    .join("");
  return `<div data-produkter>
      <h3 class="etikett">Produkter att beställa på Geotorget</h3>
      <p class="avsnitt-ingress">Varje produkt beställs för sig, avgiftsfritt, för ditt konto på${lank || " Geotorget."} Beställ de som Floden ska använda för det du vill göra. <a href="https://github.com/joelberring/floden/blob/main/docs/KONTO.md" target="_blank" rel="noopener noreferrer">Så kopplar du Lantmäteriet</a>.</p>
      <div class="tabell-ruta"><table class="data-tabell">
        <thead><tr><th scope="col">Produkt</th><th scope="col">Används till</th><th scope="col">Behövs/Valfri</th></tr></thead>
        <tbody>${tr}</tbody></table></div>
      <p class="hjalp-ruta" role="note" data-bestallning-tid>${esc(n.bestallning_text ?? "En beställning kan ta tid, ibland dagar, innan produkten är aktiv. Lantmäteriet svarar då 401 eller 403.")}</p>
    </div>`;
}

function nyckelFormular(n: NyckelStatus): string {
  const falt = n.falt
    .map((f) => {
      if (f.val.length > 0) return valFalt(n, f);
      const id = `nyckel-${n.typ}-${f.id}`;
      return `<label class="falt-rad" for="${esc(id)}">${esc(f.namn)}
        <input id="${esc(id)}" class="kontroll" type="password" name="${esc(f.id)}" autocomplete="off" spellcheck="false" required /></label>`;
    })
    .join("");
  const sparad = n.sparad !== null;
  return `
    <section class="nyckel-avsnitt" data-nyckel-typ="${esc(n.typ)}">
      <div class="sektionsrad linje-under-stark">${etikett(n.namn)}</div>
      <p class="avsnitt-ingress">${esc(n.beskrivning)}</p>
      ${produktTabell(n)}
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

/* ---------- Primär AI-leverantör ---------- */

/** Sektion för att välja vilken sparad AI-API-nyckel som ska vara aktiv/primär. */
export function primarAiAvsnitt(svar: NycklarSvar): string {
  const aiTyper = svar.typer.filter((t) => t.kategori === "ai");
  if (aiTyper.length === 0) return "";
  const sparade = aiTyper.filter((t) => t.sparad !== null);
  const primar = svar.primar_ai ?? null;
  if (sparade.length === 0) {
    return `<section class="nyckel-avsnitt" data-primar-ai-avsnitt>
      <div class="sektionsrad linje-under-stark">${etikett("Aktiv AI-leverantör")}</div>
      <p class="avsnitt-ingress">Du kan spara nycklar för flera AI-leverantörer (Claude, Google Gemini, OpenAI) nedan och välja vilken som ska vara primär.</p>
      <p class="dampad">Ingen AI-nyckel sparad än.</p>
    </section>`;
  }
  const knappar = aiTyper
    .map((t) => {
      const arSparad = t.sparad !== null;
      const arPrimar = arSparad && primar === t.typ;
      const status = arPrimar ? " (primär)" : arSparad ? " (sparad)" : " (ej sparad)";
      return `<label class="val-rad"><input type="radio" name="primar_ai" value="${esc(t.typ)}" data-primar-ai="${esc(t.typ)}"${arPrimar ? " checked" : ""}${arSparad ? "" : " disabled"} /> ${esc(t.namn)}<span class="dampad">${esc(status)}</span></label>`;
    })
    .join("");
  return `<section class="nyckel-avsnitt" data-primar-ai-avsnitt>
      <div class="sektionsrad linje-under-stark">${etikett("Aktiv AI-leverantör")}</div>
      <p class="avsnitt-ingress">Välj vilken av dina sparade AI-nycklar som ska vara primär.</p>
      <fieldset class="falt-rad val-grupp" data-primar-ai-grupp>
        <legend>Primär leverantör</legend>
        ${knappar}
      </fieldset>
    </section>`;
}

/* ---------- MCP-token ---------- */

export type McpKlient = "antigravity" | "claude-code" | "codex" | "http";

export const MCP_KLIENTER: ReadonlyArray<{ id: McpKlient; namn: string; beskrivning: string }> = [
  {
    id: "antigravity",
    namn: "Antigravity",
    beskrivning: "Lägg till i ~/.gemini/config/mcp_config.json:",
  },
  {
    id: "claude-code",
    namn: "Claude Code",
    beskrivning: "Kör kommandot i terminalen:",
  },
  {
    id: "codex",
    namn: "Codex",
    beskrivning: "Lägg till i ~/.codex/config.toml:",
  },
  {
    id: "http",
    namn: "HTTP / API",
    beskrivning: "Anropa Streamable HTTP direkt med Bearer-huvud:",
  },
];

/** Vad avsnittet MCP-token ritas av: listan, en nyss skapad token (visas en enda gång) och ett fel. */
export interface McpAvsnitt {
  svar: McpTokenSvar | null;
  ny: NyMcpToken | null;
  fel: string | null;
  /** Vald klient i kopieringsrutan (standard: "claude-code"). */
  klient?: McpKlient;
  /** Appar kontot godkänt via OAuth (Claude-appen); utelämnad eller null medan de hämtas. */
  appar?: OauthAnslutning[] | null;
}

/** Kommandot för Claude Code. `token` är den nyss skapade token, annars platshållaren `<token>`. */
export function mcpKommando(mcpUrl: string, token: string | null): string {
  return `claude mcp add --transport http floden ${mcpUrl} --header "Authorization: Bearer ${token ?? "<token>"}"`;
}

/** Konfigurationsblock eller kommando för vald MCP-klient. */
export function mcpKonfig(mcpUrl: string, token: string | null, klient: McpKlient = "claude-code"): string {
  const t = token ?? "<token>";
  if (klient === "antigravity") {
    return JSON.stringify(
      {
        mcpServers: {
          floden: {
            url: mcpUrl,
            headers: {
              Authorization: `Bearer ${t}`,
            },
          },
        },
      },
      null,
      2,
    );
  }
  if (klient === "codex") {
    return `[mcp_servers.floden]\nurl = "${mcpUrl}"\nhttp_headers = { Authorization = "Bearer ${t}" }`;
  }
  if (klient === "http") {
    return `URL: ${mcpUrl}\nAuthorization: Bearer ${t}`;
  }
  return mcpKommando(mcpUrl, token);
}

export function mcpTokenAvsnitt(a: McpAvsnitt): string {
  const huvud = `<div class="sektionsrad linje-under-stark">${etikett("MCP-token")}</div>
      <p class="avsnitt-ingress">Med personliga token kan flera AI-verktyg samtidigt (Antigravity, Claude Code, Codex eller eget API-skript) styra Floden som dig via MCP. Skapa gärna en token per verktyg eller dator så du kan återkalla dem var för sig. Tokenen visas en enda gång, direkt efter att du skapat den.</p>`;
  if (a.fel) return `<section class="nyckel-avsnitt" data-mcp-token>${huvud}<p class="hjalp-ruta" role="alert">${esc(a.fel)}</p></section>`;
  if (!a.svar) return `<section class="nyckel-avsnitt" data-mcp-token>${huvud}<p class="dampad">Hämtar …</p></section>`;
  const svar = a.svar;
  const valdKlient: McpKlient = a.klient ?? "claude-code";
  const klientInfo = MCP_KLIENTER.find((k) => k.id === valdKlient) ?? MCP_KLIENTER[1]!;
  const kommando = mcpKonfig(svar.mcp_url, a.ny?.token ?? null, valdKlient);
  const klientKnappar = MCP_KLIENTER.map(
    (k) =>
      `<button type="button" class="knapp knapp-liten${k.id === valdKlient ? " knapp-svart" : ""}" data-mcp-klient="${esc(k.id)}" aria-pressed="${k.id === valdKlient ? "true" : "false"}">${esc(k.namn)}</button>`,
  ).join("");
  const nyttMeddelande = a.ny
    ? `<p class="hjalp-ruta" role="status" data-ny-mcp-token>Token <strong>${esc(a.ny.namn)}</strong> är skapad. Kopiera konfigurationen nu: tokenen visas inte igen.</p>`
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
        <label class="falt-rad" for="mcp-token-namn">Namn (t.ex. Antigravity Mac eller Claude Code)
          <input id="mcp-token-namn" class="kontroll" type="text" name="namn" maxlength="60" required autocomplete="off" /></label>
        <button type="submit" class="knapp knapp-svart">Skapa token</button>
      </form>`;
  return `<section class="nyckel-avsnitt" data-mcp-token>
      ${huvud}
      ${nyttMeddelande}
      <div class="knapp-par" role="group" aria-label="Välj MCP-klient" data-mcp-klient-valjare>${klientKnappar}</div>
      <p class="avsnitt-ingress">${esc(klientInfo.beskrivning)}${a.ny ? "" : " (fyll i token du skapar nedan)"}</p>
      <pre class="kodruta"><code data-mcp-kommando>${esc(kommando)}</code></pre>
      ${a.ny ? `<div class="knapp-par"><button type="button" class="knapp" data-kopiera-mcp>Kopiera</button></div>` : ""}
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
    innehall = `<p class="hjalp-ruta" role="status" data-meddelande hidden></p>${primarAiAvsnitt(svar)}${svar.typer.map(nyckelFormular).join("")}`;
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
    .map((r) => `<option value="${esc(r)}"${r === vald ? " selected" : ""}>${esc(rollNamn(r))}</option>`)
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
  return `<tr><td>${esc(i.epost)}</td><td>${esc(rollNamn(i.roll))}</td><td>${esc(INBJUDAN_TEXT[i.status])}</td><td>${esc(datumTid(i.giltig_till))}</td></tr>`;
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
    : esc(rollNamn(m.roll));
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
      <p class="avsnitt-ingress">Din roll: <strong>${esc(rollNamn(o.din_roll))}</strong>. Organisationsadmin och ägare utreder i alla organisationens projekt; ledare läser dem.</p>
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
      <p class="ingress">Bjud in personer till organisationen eller till ett projekt, ändra roller och spärra konton. En inbjudan tas emot när personen loggar in med den adressen; <strong>läsare</strong> får läsa, <strong>utredare</strong> får även köra.</p>
      ${meddelande ? `<p class="hjalp-ruta" role="status">${esc(meddelande)}</p>` : ""}
      ${organisation}
      ${innehall}
    </div>`;
}

