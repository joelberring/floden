/*
 * Sidhuvudet i flödet: 56 px, "Floden / <projekt>", stegraden och kontoknappen. Kontomenyn har
 * Nycklar (inloggade) och Administration (admin) och diskreta länkar till Om
 * beräkningarna, Matematik och forskning och utredningsfrågorna utan projekt.
 */
import { adminTillaten, kontoLage } from "../konto.ts";
import { esc } from "../sidor.ts";
import { stegradHtml, type Steg } from "./adress.ts";

let avlyssnaUtanfor: (() => void) | null = null;
const namnCache = new Map<string, string>();
export function sattProjektNamn(id: string, namn: string): void {
  namnCache.set(id, namn);
}

function kontoInitial(): string {
  const l = kontoLage();
  if (l.lage !== "in") return "";
  return (l.namn || l.epost).trim().charAt(0).toUpperCase();
}

export function kontoMenyHtml(): string {
  const l = kontoLage();
  const ut =
    l.lage === "in"
      ? `<li><button type="button" class="flode-meny-rad" data-konto="logga-ut">Logga ut</button></li>`
      : "";
  const vem = l.lage === "in" ? `<li class="flode-meny-vem">${esc(l.namn || l.epost)}</li>` : "";
  const initial = kontoInitial();
  return `
    <div class="flode-konto">
      <button type="button" class="flode-konto-knapp" aria-label="Konto" aria-haspopup="true" aria-expanded="false" aria-controls="flode-konto-meny">${
        initial
          ? esc(initial)
          : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>`
      }</button>
      <ul class="flode-meny" id="flode-konto-meny" hidden>
        ${vem}
        ${l.lage === "in" ? `<li><a class="flode-meny-rad" href="#/nycklar">Nycklar</a></li>` : ""}
        ${adminTillaten(l) ? `<li><a class="flode-meny-rad" href="#/administration">Administration</a></li>` : ""}
        <li><a class="flode-meny-rad" href="#/om">Om beräkningarna</a></li>
        <li><a class="flode-meny-rad" href="#/matematik">Matematik och forskning</a></li>
        <li><a class="flode-meny-rad" href="#/fragor">Utredningsfrågor</a></li>
        ${ut}
      </ul>
    </div>`;
}

export function flodeHuvudHtml(projekt: string | null, aktivt: Steg | null): string {
  const namn = projekt ? (namnCache.get(projekt) ?? projekt) : null;
  return `
    <div class="flode-huvud">
      <a class="flode-ordmarke" href="#/">Floden${namn ? ` <span class="flode-ordmarke-projekt">/ ${esc(namn)}</span>` : ""}</a>
      ${stegradHtml(projekt, aktivt)}
      ${kontoMenyHtml()}
    </div>`;
}

/** Ritar huvudet och kopplar kontomenyn (öppna, stäng med Esc eller klick utanför). */
export function ritaFlodeHuvud(el: HTMLElement, projekt: string | null, aktivt: Steg | null): void {
  el.innerHTML = flodeHuvudHtml(projekt, aktivt);
  const knapp = el.querySelector<HTMLButtonElement>(".flode-konto-knapp");
  const meny = el.querySelector<HTMLElement>(".flode-meny");
  if (!knapp || !meny) return;
  const stang = (): void => {
    meny.hidden = true;
    knapp.setAttribute("aria-expanded", "false");
  };
  knapp.addEventListener("click", () => {
    const oppna = meny.hidden;
    meny.hidden = !oppna;
    knapp.setAttribute("aria-expanded", String(oppna));
  });
  el.addEventListener("keydown", (h) => {
    if (h.key === "Escape" && !meny.hidden) {
      stang();
      knapp.focus();
    }
  });
  avlyssnaUtanfor?.();
  const utanfor = (h: MouseEvent): void => {
    if (h.target instanceof Node && !el.querySelector(".flode-konto")?.contains(h.target)) stang();
  };
  document.addEventListener("click", utanfor);
  avlyssnaUtanfor = () => document.removeEventListener("click", utanfor);
  meny.addEventListener("click", (h) => {
    if (h.target instanceof Element && h.target.closest("a")) stang();
  });
}
