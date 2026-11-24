/*
 * Ritlagerpanelen (steg 2): förslagets ritlager (K50) med roll, synlighet och låsning, valt lager att
 * rita i, och att lägga till eller ta bort lager. Panelen visar modellen i `PlanDok`; inga tal beräknas
 * här (antal objekt visas med `formatera`). Roll och vad den blir i beräkningen kommer från servern.
 */
import { formatera } from "../tal.ts";
import type { PlanDok } from "./plandok.ts";
import { arStangt, nyttLager, RITBARA_ROLLER, type RitLager, type Roll, type RollInfo } from "./ritlager.ts";

const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);

export interface RitlagerKrokar {
  /** Efter en ändring i panelen (kartan ritas om, förslaget kontrolleras och sparas). */
  andrad(): void;
}

/** Panelens rader som ren data (för test): ett objekt per lager. */
export interface LagerRad {
  id: string;
  namn: string;
  rollNamn: string;
  blir: string;
  antal: string;
  aktiv: boolean;
  kanRitas: boolean;
  stangt: boolean;
  kanTasBort: boolean;
  synlig: boolean;
  last: boolean;
}

export function lagerRader(dok: PlanDok, roller: readonly RollInfo[]): LagerRad[] {
  return dok.lager.map((l) => {
    const info = roller.find((r) => r.roll === l.roll);
    const antal = dok.antalILager(l.lager_id);
    return {
      id: l.lager_id,
      namn: l.namn,
      rollNamn: info?.namn ?? l.roll,
      blir: info?.blir ?? "",
      antal: formatera(antal, 0),
      aktiv: dok.aktivtLager === l.lager_id,
      kanRitas: RITBARA_ROLLER.has(l.roll),
      stangt: arStangt(l),
      kanTasBort: antal === 0,
      synlig: l.synlig,
      last: l.last,
    };
  });
}

export function monteraRitlagerpanel(rot: HTMLElement, dok: PlanDok, roller: readonly RollInfo[], krokar: RitlagerKrokar): () => void {
  const swatch = (l: RitLager): string => {
    const f = l.stil.fyllning ?? "transparent";
    const k = l.stil.linje ?? "#666";
    return `<span class="rl-prov" style="background:${esc(f)};border-color:${esc(k)}" aria-hidden="true"></span>`;
  };
  const rita = (): void => {
    const rader = lagerRader(dok, roller);
    const rollval = roller.map((r) => `<option value="${esc(r.roll)}">${esc(r.namn)}</option>`).join("");
    rot.innerHTML = `
      <ul class="rl-lista" role="list">${rader
        .map((r, i) => {
          const l = dok.lager[i] as RitLager;
          return `<li class="rl-rad${r.aktiv ? " aktiv" : ""}${r.last ? " last" : ""}" data-lager="${esc(r.id)}">
            <div class="rl-topp">
              <input type="radio" name="rl-aktiv" id="rl-a-${esc(r.id)}" data-aktiv="${esc(r.id)}" ${r.aktiv ? "checked" : ""} ${r.kanRitas ? "" : "disabled"} aria-label="Rita i ${esc(r.namn)}"/>
              ${swatch(l)}
              <label for="rl-a-${esc(r.id)}" class="rl-namn">${esc(r.namn)}</label>
              <span class="rl-antal flode-dampad">${r.antal}</span>
              <label class="rl-vaxla"><input type="checkbox" data-synlig="${esc(r.id)}" ${r.synlig ? "checked" : ""}/> Visa</label>
              <label class="rl-vaxla"><input type="checkbox" data-last="${esc(r.id)}" ${r.last ? "checked" : ""}/> Lås</label>
              <button type="button" class="ikonknapp" data-bort="${esc(r.id)}" aria-label="Ta bort lagret ${esc(r.namn)}" ${r.kanTasBort ? "" : "disabled"} title="${r.kanTasBort ? "Ta bort det tomma lagret" : "Lagret har objekt; ta bort dem först"}">×</button>
            </div>
            <p class="rl-roll flode-liten flode-dampad">${esc(r.rollNamn)}: ${esc(r.blir)}${r.kanRitas ? "" : ""}${r.stangt ? ' <strong class="rl-stangt">Stängt: lämnas inte ut via MCP.</strong>' : ""}</p>
          </li>`;
        })
        .join("")}</ul>
      <div class="rl-ny">
        <label class="etikett-liten" for="rl-roll">Nytt lager</label>
        <select id="rl-roll" aria-label="Roll för nytt lager">${rollval}</select>
        <input id="rl-namn" type="text" maxlength="60" placeholder="Namn (valfritt)" aria-label="Namn på nytt lager" autocomplete="off"/>
        <button type="button" class="flode-knapp" id="rl-lagg">Lägg till</button>
      </div>`;
  };
  dok.vidAndring(() => {
    // Ritas om vid varje ändring i dokumentet (antal objekt, lager); fokus på namnfältet bevaras inte, men det ändras sällan under ritning.
    const aktivt = document.activeElement;
    if (aktivt instanceof HTMLInputElement && aktivt.id === "rl-namn") return;
    rita();
  });
  rot.addEventListener("change", (h) => {
    const m = h.target as HTMLInputElement;
    if (m.dataset.aktiv) dok.sattAktivtLager(m.dataset.aktiv);
    else if (m.dataset.synlig) dok.andraLager(m.dataset.synlig, (l) => (l.synlig = m.checked));
    else if (m.dataset.last) dok.andraLager(m.dataset.last, (l) => (l.last = m.checked));
    else return;
    krokar.andrad();
  });
  rot.addEventListener("click", (h) => {
    const m = h.target as HTMLElement;
    const bort = m.closest<HTMLButtonElement>("[data-bort]");
    if (bort && !bort.disabled) {
      if (dok.taBortLager(bort.dataset.bort as string)) krokar.andrad();
      return;
    }
    if (m.closest("#rl-lagg")) {
      const roll = (rot.querySelector("#rl-roll") as HTMLSelectElement).value as Roll;
      const namn = (rot.querySelector("#rl-namn") as HTMLInputElement).value;
      dok.laggTillLager(nyttLager(dok.lager, roll, namn, roller));
      krokar.andrad();
    }
  });
  rita();
  return rita;
}
