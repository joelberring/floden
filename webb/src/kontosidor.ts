/*
 * Kontosidorna Nycklar (alla inloggade) och Administration (superadmin och organisationsadmin):
 * hämtar från `/konto/nycklar` och `/konto/admin`, ritar med `kontosidor-html.ts` och skickar
 * ändringar (med CSRF-huvud via `lasJson`). Servern avgör allt; klienten visar bara svaren.
 */
import {
  aterkallaMcpToken,
  aterkallaOauthAnslutning,
  bjudIn,
  bjudInOrganisation,
  bytOrganisationsnamn,
  hamtaAdminOrganisationDetalj,
  hamtaAdminOrganisationer,
  hamtaAdminProjekt,
  hamtaAdminProjektDetalj,
  hamtaMcpTokens,
  hamtaNycklar,
  hamtaOauthAnslutningar,
  sattOrganisationsRoll,
  sattPrimarAi,
  sattProjektDelning,
  sattProjektRoll,
  skapaMcpToken,
  sparaNyckel,
  sparraKonto,
  taBortNyckel,
  taBortOrganisationsMedlem,
  taBortProjektRoll,
} from "./api";
import { kontoLage } from "./konto";
import { felText, sidaAdministration, sidaNycklar, skapaNycklarStyrning } from "./kontosidor-html";
import type { McpKlient } from "./kontosidor-html";

export async function visaNycklar(vy: HTMLElement): Promise<void> {
  const visaMeddelande = (text: string): void => {
    const m = vy.querySelector<HTMLElement>("[data-meddelande]");
    if (m) {
      m.textContent = text;
      m.hidden = false;
    }
  };
  if (kontoLage().lage === "av") {
    vy.innerHTML = sidaNycklar(null);
    return;
  }
  const styrning = skapaNycklarStyrning(
    {
      hamtaNycklar,
      sparaNyckel,
      taBortNyckel,
      sattPrimarAi,
      hamtaMcpTokens,
      skapaMcpToken,
      aterkallaMcpToken,
      hamtaOauthAnslutningar,
      aterkallaOauthAnslutning,
    },
    (html, meddelande) => {
      vy.innerHTML = html;
      if (meddelande) visaMeddelande(meddelande);
    },
    visaMeddelande,
  );
  vy.innerHTML = styrning.html();
  vy.onsubmit = (h: SubmitEvent): void => {
    const form = h.target instanceof HTMLFormElement ? h.target : null;
    if (form?.hasAttribute("data-mcp-token-form")) {
      h.preventDefault();
      const namn = (form.elements.namedItem("namn") as HTMLInputElement).value;
      void styrning.skapaToken(namn);
      return;
    }
    const typ = form?.dataset.nyckelform;
    if (!form || !typ) return;
    h.preventDefault();
    const varden: Record<string, string> = {};
    for (const el of Array.from(form.elements)) {
      if (el instanceof HTMLInputElement && el.type === "radio" && !el.checked) continue;
      if ((el instanceof HTMLInputElement || el instanceof HTMLSelectElement) && el.name) varden[el.name] = el.value;
    }
    void styrning.spara(typ, varden);
  };
  vy.onclick = (h: MouseEvent): void => {
    const mal = h.target instanceof Element ? h.target : null;
    const app = mal?.closest("[data-aterkalla-app]");
    if (app) {
      h.preventDefault();
      const id = app.getAttribute("data-aterkalla-app") ?? "";
      const namn = app.getAttribute("data-aterkalla-namn") ?? "";
      if (!window.confirm(`Ta bort tillgången för "${namn}"? Appen slutar fungera direkt.`)) return;
      void styrning.aterkallaApp(id);
      return;
    }
    const aterkalla = mal?.closest("[data-aterkalla-mcp]");
    if (aterkalla) {
      h.preventDefault();
      const id = aterkalla.getAttribute("data-aterkalla-mcp") ?? "";
      const namn = aterkalla.getAttribute("data-aterkalla-namn") ?? "";
      if (!window.confirm(`Återkalla tokenen "${namn}"? Verktyg som använder den slutar fungera direkt.`)) return;
      void styrning.aterkallaToken(id);
      return;
    }
    const kopiera = mal?.closest("[data-kopiera-mcp]");
    if (kopiera) {
      h.preventDefault();
      const text = styrning.kopierbarKonfig();
      if (text) {
        void navigator.clipboard?.writeText(text);
        kopiera.textContent = "Kopierad";
      }
      return;
    }
    const klientKnapp = mal?.closest("[data-mcp-klient]");
    if (klientKnapp) {
      h.preventDefault();
      const k = klientKnapp.getAttribute("data-mcp-klient") as McpKlient | null;
      if (k) styrning.valjMcpKlient(k);
      return;
    }
    const knapp = mal?.closest("[data-ta-bort-nyckel]") ?? null;
    const typ = knapp?.getAttribute("data-ta-bort-nyckel");
    if (!typ) return;
    h.preventDefault();
    if (!window.confirm("Ta bort den sparade nyckeln?")) return;
    void styrning.taBort(typ);
  };
  vy.onchange = (h: Event): void => {
    const el = h.target instanceof HTMLInputElement ? h.target : null;
    const primar = el?.dataset.primarAi;
    if (!el || !primar || !el.checked) return;
    void styrning.valjPrimarAi(primar);
  };
  await styrning.ladda();
}

export async function visaAdministration(vy: HTMLElement): Promise<void> {
  const ladda = async (meddelande = ""): Promise<void> => {
    try {
      const lista = await hamtaAdminProjekt();
      const detaljer = await Promise.all(lista.projekt.map((p) => hamtaAdminProjektDetalj(p.id)));
      const orgar = await hamtaAdminOrganisationer();
      const orgDetaljer = await Promise.all(orgar.organisationer.map((o) => hamtaAdminOrganisationDetalj(o.id)));
      vy.innerHTML = sidaAdministration(detaljer, null, meddelande, orgDetaljer);
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
    const orgBjud = form?.dataset.orgBjudIn;
    if (form && orgBjud) {
      h.preventDefault();
      const epost = (form.elements.namedItem("epost") as HTMLInputElement).value;
      const roll = (form.elements.namedItem("roll") as HTMLSelectElement).value;
      void bjudInOrganisation(orgBjud, epost, roll).then(klart(`Inbjudan till ${epost} är skapad.`), misslyckades);
      return;
    }
    const orgNamn = form?.dataset.orgNamn;
    if (form && orgNamn) {
      h.preventDefault();
      const namn = (form.elements.namedItem("namn") as HTMLInputElement).value;
      void bytOrganisationsnamn(orgNamn, namn).then(klart("Namnet är ändrat."), misslyckades);
      return;
    }
    const pid = form?.dataset.bjudIn;
    if (!form || !pid) return;
    h.preventDefault();
    const epost = (form.elements.namedItem("epost") as HTMLInputElement).value;
    const roll = (form.elements.namedItem("roll") as HTMLSelectElement).value;
    void bjudIn(pid, epost, roll).then(klart(`Inbjudan till ${epost} är skapad.`), misslyckades);
  };
  vy.onchange = (h: Event): void => {
    if (h.target instanceof HTMLInputElement && h.target.dataset.delning) {
      const ruta = h.target;
      void sattProjektDelning(ruta.dataset.delning ?? "", ruta.checked).then(
        klart(ruta.checked ? "Projektet är delat med organisationen." : "Projektet är privat igen."),
        misslyckades,
      );
      return;
    }
    const el = h.target instanceof HTMLSelectElement ? h.target : null;
    if (el?.dataset.orgRoll && el.dataset.orgKonto) {
      void sattOrganisationsRoll(el.dataset.orgRoll, el.dataset.orgKonto, el.value).then(
        klart("Rollen är ändrad."),
        misslyckades,
      );
      return;
    }
    const pid = el?.dataset.rollProjekt;
    const kid = el?.dataset.rollKonto;
    if (!el || !pid || !kid) return;
    void sattProjektRoll(pid, kid, el.value).then(klart("Rollen är ändrad."), misslyckades);
  };
  vy.onclick = (h: MouseEvent): void => {
    const mal = h.target instanceof Element ? h.target : null;
    const orgMedlem = mal?.closest("[data-org-ta-bort]")?.getAttribute("data-org-ta-bort");
    if (orgMedlem) {
      h.preventDefault();
      const [oid = "", kid = ""] = orgMedlem.split("|");
      if (window.confirm("Ta bort personen från organisationen? Personens roller i projekt påverkas inte.")) {
        void taBortOrganisationsMedlem(oid, kid).then(klart("Medlemmen är borttagen."), misslyckades);
      }
      return;
    }
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
