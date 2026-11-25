/*
 * Projektinställningar (`#/p/<projekt>/installningar`): namn, synlighet för organisationen, medlemmar och inbjudningar,
 * arkivera och radera. Servern avgör allt (`/konto/projekt/<id>/...`, `/api/projekt/<id>/namn` och `/radera`); sidan
 * visar bara det svaret säger att man får göra. Radering kräver att projektets namn skrivs.
 */
import {
  andraMedlemsRoll,
  aterkallaProjektInbjudan,
  arkiveraProjekt,
  bjudInTillProjekt,
  bytProjektNamn,
  hamtaProjektInstallningar,
  raderaProjekt,
  sattProjektDelning,
  taBortMedlem,
} from "../api.ts";
import { esc } from "../sidor.ts";
import type { ProjektInstallningar } from "../types.ts";
import { sattProjektNamn } from "./huvud.ts";
import { DEMO_PROJEKT_ID, installningarHtml, raderaTillaten, raderadText } from "./projektinstallningar-logik.ts";
import type { Avslutbar } from "./projektlista.ts";

const felText = (e: unknown): string => (e instanceof Error ? e.message : "Något gick fel.");

export function visaProjektInstallningar(rot: HTMLElement, projekt: string): Avslutbar {
  let avslutad = false;
  let data: ProjektInstallningar | null = null;
  if (projekt === DEMO_PROJEKT_ID) {
    rot.innerHTML = `<div class="flode-sida-smal"><h1 class="flode-h">Projektinställningar</h1>
      <p>Demokvarteret är ett påhittat exempelprojekt. Det har inga medlemmar att ändra och kan inte arkiveras eller raderas.</p>
      <p><a href="#/">Projektlistan</a></p></div>`;
    return { klar: Promise.resolve(), avsluta: () => undefined };
  }
  const rita = (meddelande = "", fel = false): void => {
    if (avslutad || !data) return;
    rot.innerHTML = installningarHtml(data, meddelande, fel);
    koppla();
  };
  const ladda = async (meddelande = ""): Promise<void> => {
    try {
      data = await hamtaProjektInstallningar(projekt);
      sattProjektNamn(projekt, data.projekt.namn);
    } catch (e) {
      if (avslutad) return;
      rot.innerHTML = `<div class="flode-sida-smal"><h1 class="flode-h">Projektinställningar</h1><p class="flode-fel" role="alert">${esc(felText(e))}</p><p><a href="#/">Projektlistan</a></p></div>`;
      return;
    }
    rita(meddelande);
  };
  const gor = async (atgard: () => Promise<unknown>, ok: string): Promise<void> => {
    try {
      await atgard();
    } catch (e) {
      rita(felText(e), true);
      return;
    }
    await ladda(ok);
  };
  function koppla(): void {
    const q = <T extends Element>(s: string): T | null => rot.querySelector<T>(s);
    const d = data;
    if (!d) return;
    q<HTMLFormElement>("[data-byt-namn]")?.addEventListener("submit", (h) => {
      h.preventDefault();
      const namn = String(new FormData(h.target as HTMLFormElement).get("namn") ?? "");
      void gor(() => bytProjektNamn(projekt, namn), "Namnet är ändrat.");
    });
    q<HTMLInputElement>("[data-delning]")?.addEventListener("change", (h) => {
      const synlig = (h.target as HTMLInputElement).checked;
      void gor(() => sattProjektDelning(projekt, synlig), synlig ? "Projektet är delat med organisationen." : "Projektet är privat.");
    });
    q<HTMLFormElement>("[data-bjud-in]")?.addEventListener("submit", (h) => {
      h.preventDefault();
      const f = new FormData(h.target as HTMLFormElement);
      void gor(() => bjudInTillProjekt(projekt, String(f.get("epost") ?? ""), String(f.get("roll") ?? "lasare")), "Inbjudan är skapad.");
    });
    for (const sel of rot.querySelectorAll<HTMLSelectElement>("[data-medlem-roll]")) {
      sel.addEventListener("change", () => void gor(() => andraMedlemsRoll(projekt, sel.dataset.medlemRoll ?? "", sel.value), "Rollen är ändrad."));
    }
    for (const kn of rot.querySelectorAll<HTMLButtonElement>("[data-ta-bort-medlem]")) {
      kn.addEventListener("click", () => void gor(() => taBortMedlem(projekt, kn.dataset.taBortMedlem ?? ""), "Medlemmen är borttagen."));
    }
    for (const kn of rot.querySelectorAll<HTMLButtonElement>("[data-aterkalla-inbjudan]")) {
      kn.addEventListener("click", () => void gor(() => aterkallaProjektInbjudan(projekt, kn.dataset.aterkallaInbjudan ?? ""), "Inbjudan är återkallad."));
    }
    q<HTMLButtonElement>("[data-arkiv]")?.addEventListener("click", (h) => {
      const ja = (h.currentTarget as HTMLButtonElement).dataset.arkiv === "ja";
      void gor(() => arkiveraProjekt(projekt, ja), ja ? "Projektet är arkiverat och syns inte i projektlistan." : "Projektet är återställt.");
    });
    const radera = q<HTMLFormElement>("[data-radera]");
    const falt = radera?.querySelector<HTMLInputElement>('input[name="bekrafta"]');
    const knapp = radera?.querySelector<HTMLButtonElement>("button");
    falt?.addEventListener("input", () => {
      if (knapp) knapp.disabled = !raderaTillaten(d.projekt.namn, falt.value);
    });
    radera?.addEventListener("submit", (h) => {
      h.preventDefault();
      if (!falt || !raderaTillaten(d.projekt.namn, falt.value)) return;
      void (async () => {
        try {
          const svar = await raderaProjekt(projekt, falt.value);
          rot.innerHTML = `<div class="flode-sida-smal"><h1 class="flode-h">Projektet är raderat</h1><p role="status">${esc(raderadText(svar.borttaget))}</p><p><a href="#/">Till projektlistan</a></p></div>`;
        } catch (e) {
          rita(felText(e), true);
        }
      })();
    });
  }
  const klar = ladda();
  return { klar, avsluta: () => {
      avslutad = true;
    },
  };
}
