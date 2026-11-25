/*
 * Flödet: 1 Projekt → 2 Karta & analys → 3 Rapport.
 * Väljer vy ur adressen (100 % bakåtkompatibel med äldre adresser som /forslag, /resultat, /fragor),
 * ritar huvudet och steget, och flyttar fokus till rubriken vid stegbyte.
 */
import "./flode.css";
import "./arbetsyta.css";
import "../karta/karta.css";
import { hamtaProjektLista } from "../api.ts";
import { tolkaFlodeAdress, type FlodeRutt } from "./adress.ts";
import { visaArbetsyta } from "./arbetsyta.ts";
import { visaFragor } from "./fragor.ts";
import { ritaFlodeHuvud, sattProjektNamn, SKALSIDOR, type SkalSida } from "./huvud.ts";
import { visaNyttProjekt } from "./nyttprojekt.ts";
import { visaProjektInstallningar } from "./projektinstallningar.ts";
import { visaProjektlista, type Avslutbar } from "./projektlista.ts";
import { visaRapport } from "./rapport.ts";
import { tillstandFor } from "./tillstand.ts";

export { tolkaFlodeAdress };

let aktiv: Avslutbar | null = null;

export function avslutaFlode(): void {
  aktiv?.avsluta();
  aktiv = null;
}

export const STEGTITEL: Record<string, string> = {
  projekt: "Projekt",
  karta: "Karta & analys",
  forslag: "Karta & analys",
  fragor: "Frågor",
  resultat: "Karta & analys",
  rapport: "Rapport",
};

/** Visar flödets steg. `huvud` är sidhuvudselementet, `rot` innehållet. */
export function visaFlode(huvud: HTMLElement, rot: HTMLElement, rutt: FlodeRutt): void {
  avslutaFlode();
  document.title = `${STEGTITEL[rutt.steg] ?? "Floden"} – Floden`;
  const aktivtSteg = rutt.installningar ? null : rutt.steg;
  if (rutt.installningar) document.title = "Projektinställningar – Floden";
  ritaFlodeHuvud(huvud, rutt.projekt, aktivtSteg);
  const p = rutt.projekt;
  // Projektets namn i huvudet (id:t tills listan har kommit).
  if (p) {
    void hamtaProjektLista()
      .then((l) => {
        const post = l.projekt.find((x) => x.id === p);
        if (post && aktiv) {
          sattProjektNamn(p, post.namn);
          ritaFlodeHuvud(huvud, p, aktivtSteg);
        }
      })
      .catch(() => undefined);
  }
  let steg: Avslutbar;
  if (rutt.nytt) steg = visaNyttProjekt(rot);
  else if (rutt.installningar && p !== null) steg = visaProjektInstallningar(rot, p);
  else if (rutt.steg === "projekt" || p === null) steg = visaProjektlista(rot, null, rutt.arkiverade === true);
  else if (rutt.steg === "karta") steg = visaArbetsyta(rot, p, "nulage");
  else if (rutt.steg === "forslag") steg = visaArbetsyta(rot, p, "redigera");
  else if (rutt.steg === "resultat") {
    const t = tillstandFor(p);
    const harJustKortForslag = t.ritVyer.length > 0 && !t.korning?.lage;
    steg = visaArbetsyta(rot, p, harJustKortForslag ? "efter" : "nulage");
  } else if (rutt.steg === "fragor") steg = visaFragor(rot, p);
  else steg = visaRapport(rot, p);
  aktiv = steg;
  // Fokus på rubriken när steget är ritat (och går att nå med tangentbordet).
  void steg.klar.then(() => {
    if (aktiv !== steg) return;
    const h = rot.querySelector<HTMLElement>("h1");
    if (h) {
      if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
      h.focus({ preventScroll: true });
    }
  });
}

export function projektLaddat(huvud: HTMLElement, rutt: FlodeRutt): void {
  ritaFlodeHuvud(huvud, rutt.projekt, rutt.installningar ? null : rutt.steg);
}

/**
 * Sida utanför flödet (Frågekatalog, Om Floden, Nycklar ...): samma sidhuvud som flödet, utan
 * stegrad, med sidans namn efter ordmärket. `bygg` ritar innehållet och ger en Avslutbar om sidan
 * har något att städa upp; utan den är innehållet redan ritat av anroparen.
 */
export function visaSkalsida(huvud: HTMLElement, rot: HTMLElement, sida: SkalSida, bygg?: (rot: HTMLElement) => Avslutbar): void {
  avslutaFlode();
  document.title = `${SKALSIDOR[sida]} – Floden`;
  ritaFlodeHuvud(huvud, null, null, sida);
  if (!bygg) return;
  const steg = bygg(rot);
  aktiv = steg;
  void steg.klar.then(() => {
    if (aktiv !== steg) return;
    const h = rot.querySelector<HTMLElement>("h1");
    if (h) {
      if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
      h.focus({ preventScroll: true });
    }
  });
}
