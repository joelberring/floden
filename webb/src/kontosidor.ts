/*
 * Kontosidorna Nycklar (alla inloggade) och Administration (superadmin och organisationsadmin):
 * hämtar från `/konto/nycklar` och `/konto/admin`, ritar med `kontosidor-html.ts` och skickar
 * ändringar (med CSRF-huvud via `lasJson`). Servern avgör allt; klienten visar bara svaren.
 */
import {
  bjudIn,
  hamtaAdminProjekt,
  hamtaAdminProjektDetalj,
  hamtaNycklar,
  sattProjektRoll,
  sparaNyckel,
  sparraKonto,
  taBortNyckel,
  taBortProjektRoll,
} from "./api";
import { kontoLage } from "./konto";
import { felText, sidaAdministration, sidaNycklar } from "./kontosidor-html";
import type { NycklarSvar } from "./types";

export async function visaNycklar(vy: HTMLElement): Promise<void> {
  const rita = (svar: NycklarSvar | null, fel: string | null = null, meddelande = ""): void => {
    vy.innerHTML = sidaNycklar(svar, fel);
    const m = vy.querySelector<HTMLElement>("[data-meddelande]");
    if (m && meddelande) {
      m.textContent = meddelande;
      m.hidden = false;
    }
  };
  if (kontoLage().lage === "av") {
    rita(null);
    return;
  }
  rita(null);
  const ladda = async (meddelande = ""): Promise<void> => {
    try {
      rita(await hamtaNycklar(), null, meddelande);
    } catch (e) {
      rita(null, felText(e));
    }
  };
  vy.onsubmit = (h: SubmitEvent): void => {
    const form = h.target instanceof HTMLFormElement ? h.target : null;
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
      (e: unknown) => {
        const m = vy.querySelector<HTMLElement>("[data-meddelande]");
        if (m) {
          m.textContent = felText(e);
          m.hidden = false;
        }
      },
    );
  };
  vy.onclick = (h: MouseEvent): void => {
    const knapp = h.target instanceof Element ? h.target.closest("[data-ta-bort-nyckel]") : null;
    const typ = knapp?.getAttribute("data-ta-bort-nyckel");
    if (!typ) return;
    h.preventDefault();
    if (!window.confirm("Ta bort den sparade nyckeln?")) return;
    void taBortNyckel(typ).then(
      () => ladda("Nyckeln är borttagen."),
      (e: unknown) => rita(null, felText(e)),
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
