/*
 * Kontosidorna Nycklar (alla inloggade) och Administration (superadmin och organisationsadmin):
 * hämtar från `/konto/nycklar` och `/konto/admin`, ritar med `kontosidor-html.ts` och skickar
 * ändringar (med CSRF-huvud via `lasJson`). Servern avgör allt; klienten visar bara svaren.
 */
import {
  aterkallaMcpToken,
  bjudIn,
  hamtaAdminProjekt,
  hamtaAdminProjektDetalj,
  hamtaMcpTokens,
  hamtaOauthAnslutningar,
  aterkallaOauthAnslutning,
  hamtaNycklar,
  sattProjektRoll,
  skapaMcpToken,
  sparaNyckel,
  sparraKonto,
  taBortNyckel,
  taBortProjektRoll,
} from "./api";
import { kontoLage } from "./konto";
import { felText, mcpKommando, sidaAdministration, sidaNycklar } from "./kontosidor-html";
import type { McpAvsnitt } from "./kontosidor-html";
import type { NycklarSvar, NyMcpToken } from "./types";

export async function visaNycklar(vy: HTMLElement): Promise<void> {
  // Tillstånd för MCP-avsnittet. En ny token (med klartexten) finns bara i minnet tills sidan ritas
  // om av något annat än skapandet; den lagras aldrig i webbläsaren och hämtas aldrig från servern.
  let mcp: McpAvsnitt = { svar: null, ny: null, fel: null };
  let nycklar: NycklarSvar | null = null;
  let nyckelFel: string | null = null;
  const visaMeddelande = (text: string): void => {
    const m = vy.querySelector<HTMLElement>("[data-meddelande]");
    if (m) {
      m.textContent = text;
      m.hidden = false;
    }
  };
  const rita = (meddelande = ""): void => {
    vy.innerHTML = sidaNycklar(nycklar, nyckelFel, mcp);
    if (meddelande) visaMeddelande(meddelande);
  };
  if (kontoLage().lage === "av") {
    nycklar = null;
    rita();
    return;
  }
  rita();
  const laddaMcp = async (ny: NyMcpToken | null = null): Promise<void> => {
    try {
      mcp = { svar: await hamtaMcpTokens(), ny, fel: null };
    } catch (e) {
      mcp = { svar: null, ny: null, fel: felText(e) };
    }
    try {
      mcp = { ...mcp, appar: (await hamtaOauthAnslutningar()).anslutningar };
    } catch {
      mcp = { ...mcp, appar: [] };
    }
  };
  const ladda = async (meddelande = "", ny: NyMcpToken | null = null): Promise<void> => {
    try {
      nycklar = await hamtaNycklar();
      nyckelFel = null;
    } catch (e) {
      nycklar = null;
      nyckelFel = felText(e);
    }
    await laddaMcp(ny);
    rita(meddelande);
  };
  vy.onsubmit = (h: SubmitEvent): void => {
    const form = h.target instanceof HTMLFormElement ? h.target : null;
    if (form?.hasAttribute("data-mcp-token-form")) {
      h.preventDefault();
      const namn = (form.elements.namedItem("namn") as HTMLInputElement).value;
      void skapaMcpToken(namn).then(
        // Svaret med token ritas direkt; allt annat (lista, status) hämtas på nytt från servern.
        (ny) => ladda("", ny),
        (e: unknown) => {
          mcp = { ...mcp, fel: felText(e) };
          rita();
        },
      );
      return;
    }
    const typ = form?.dataset.nyckelform;
    if (!form || !typ) return;
    h.preventDefault();
    const varden: Record<string, string> = {};
    for (const el of Array.from(form.elements)) {
      if ((el instanceof HTMLInputElement || el instanceof HTMLSelectElement) && el.name) varden[el.name] = el.value;
    }
    void sparaNyckel(typ, varden).then(
      // Hela sidan ritas om från serverns status: de ifyllda fälten försvinner med den gamla DOM:en.
      () => ladda("Nyckeln är sparad."),
      (e: unknown) => visaMeddelande(felText(e)),
    );
  };
  vy.onclick = (h: MouseEvent): void => {
    const mal = h.target instanceof Element ? h.target : null;
    const app = mal?.closest("[data-aterkalla-app]");
    if (app) {
      h.preventDefault();
      const id = app.getAttribute("data-aterkalla-app") ?? "";
      const namn = app.getAttribute("data-aterkalla-namn") ?? "";
      if (!window.confirm(`Ta bort tillgången för "${namn}"? Appen slutar fungera direkt.`)) return;
      void aterkallaOauthAnslutning(id).then(
        () => ladda("Tillgången är borttagen."),
        (e: unknown) => {
          mcp = { ...mcp, fel: felText(e) };
          rita();
        },
      );
      return;
    }
    const aterkalla = mal?.closest("[data-aterkalla-mcp]");
    if (aterkalla) {
      h.preventDefault();
      const id = aterkalla.getAttribute("data-aterkalla-mcp") ?? "";
      const namn = aterkalla.getAttribute("data-aterkalla-namn") ?? "";
      if (!window.confirm(`Återkalla tokenen "${namn}"? Verktyg som använder den slutar fungera direkt.`)) return;
      void aterkallaMcpToken(id).then(
        () => ladda("Tokenen är återkallad."),
        (e: unknown) => {
          mcp = { ...mcp, fel: felText(e) };
          rita();
        },
      );
      return;
    }
    if (mal?.closest("[data-kopiera-mcp]")) {
      h.preventDefault();
      if (mcp.svar && mcp.ny) void navigator.clipboard?.writeText(mcpKommando(mcp.svar.mcp_url, mcp.ny.token));
      return;
    }
    const knapp = mal?.closest("[data-ta-bort-nyckel]") ?? null;
    const typ = knapp?.getAttribute("data-ta-bort-nyckel");
    if (!typ) return;
    h.preventDefault();
    if (!window.confirm("Ta bort den sparade nyckeln?")) return;
    void taBortNyckel(typ).then(
      () => ladda("Nyckeln är borttagen."),
      (e: unknown) => {
        nyckelFel = felText(e);
        rita();
      },
    );
  };
  await ladda();
}

export async function visaAdministration(vy: HTMLElement): Promise<void> {
  const ladda = async (meddelande = ""): Promise<void> => {
    try {
      const lista = await hamtaAdminProjekt();
      const detaljer = await Promise.all(lista.projekt.map((p) => hamtaAdminProjektDetalj(p.id)));
      vy.innerHTML = sidaAdministration(detaljer, null, meddelande);
    } catch (e) {
      vy.innerHTML = sidaAdministration(null, felText(e));
    }
  };
  const klart = (text: string) => () => ladda(text);
  const misslyckades = (e: unknown): void => {
    vy.innerHTML = sidaAdministration(null, felText(e));
  };
  vy.innerHTML = sidaAdministration(null);
  vy.onsubmit = (h: SubmitEvent): void => {
    const form = h.target instanceof HTMLFormElement ? h.target : null;
    const pid = form?.dataset.bjudIn;
    if (!form || !pid) return;
    h.preventDefault();
    const epost = (form.elements.namedItem("epost") as HTMLInputElement).value;
    const roll = (form.elements.namedItem("roll") as HTMLSelectElement).value;
    void bjudIn(pid, epost, roll).then(klart(`Inbjudan till ${epost} är skapad.`), misslyckades);
  };
  vy.onchange = (h: Event): void => {
    const el = h.target instanceof HTMLSelectElement ? h.target : null;
    const pid = el?.dataset.rollProjekt;
    const kid = el?.dataset.rollKonto;
    if (!el || !pid || !kid) return;
    void sattProjektRoll(pid, kid, el.value).then(klart("Rollen är ändrad."), misslyckades);
  };
  vy.onclick = (h: MouseEvent): void => {
    const mal = h.target instanceof Element ? h.target : null;
    const roll = mal?.closest("[data-ta-bort-roll]")?.getAttribute("data-ta-bort-roll");
    if (roll) {
      h.preventDefault();
      const [pid = "", kid = ""] = roll.split("|");
      if (window.confirm("Ta bort personens roll i projektet?")) {
        void taBortProjektRoll(pid, kid).then(klart("Rollen är borttagen."), misslyckades);
      }
      return;
    }
    const sparra = mal?.closest("[data-sparra]");
    const kid = sparra?.getAttribute("data-sparra");
    if (sparra && kid) {
      h.preventDefault();
      const till = sparra.getAttribute("data-sparra-till") === "ja";
      if (!till || window.confirm("Spärra kontot? Personen loggas ut direkt och kommer inte in igen.")) {
        void sparraKonto(kid, till).then(klart(till ? "Kontot är spärrat." : "Kontot är öppnat."), misslyckades);
      }
    }
  };
  await ladda();
}
