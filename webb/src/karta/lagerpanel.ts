/*
 * Lagerpanelen: ritar `LagerLista` som fyra grupper (Underlag, Geometri, Planförslag, Resultat) med
 * synlighet, opacitet, ordning (dra eller pilknappar), hopfällning och, för lager som är egna vyer,
 * en knapp för att byta vy. Panelen har ingen egen logik utöver att visa modellen i `lager.ts`.
 * Upplägget är O-Tids lagerpanel (MIT, joelberring/otid @ 4a446af, map-project-layers-panel.tsx).
 */
import { GRUPPER, type GruppId, type LagerDef, type LagerLista } from "./lager.ts";

const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);

export interface PanelKrokar {
  /** Efter en ändring i panelen, t.ex. för att kartan ska ritas om. */
  andrad(): void;
  /** Ett lager som kan tas bort (egna underlag). */
  taBort?(id: string): void;
  /** Vilka grupper som visas (förval alla). Steg 2 visar inte gruppen Resultat. */
  grupper?: readonly GruppId[];
}

export function monteraLagerpanel(rot: HTMLElement, lista: LagerLista, krokar: PanelKrokar): () => void {
  let hoppaOver = false;
  let dragId: string | null = null;

  const rad = (l: LagerDef, forst: boolean, sist: boolean): string => {
    const av = Boolean(l.ejTillgangligt);
    return `
      <li class="kl-rad${av ? " otillganglig" : ""}" draggable="true" data-lager="${esc(l.id)}">
        <div class="kl-topp">
          <input type="checkbox" id="lv-${esc(l.id)}" data-synlig="${esc(l.id)}" ${l.synlig ? "checked" : ""} ${av ? "disabled" : ""}/>
          <label for="lv-${esc(l.id)}" class="kl-namn">${esc(l.namn)}</label>
          <span class="kl-knappar">
            ${l.egenVy ? `<button type="button" class="ikonknapp vy-knapp" data-vy="${esc(l.id)}" aria-pressed="${lista.aktivVy?.id === l.id}" title="Byt mellan kartan och ${esc(l.namn)}">${esc(l.egenVy.knapp ? (lista.aktivVy?.id === l.id ? l.egenVy.knapp.pa : l.egenVy.knapp.av) : "Vy")}</button>` : ""}
            <button type="button" class="ikonknapp" data-upp="${esc(l.id)}" aria-label="Flytta ${esc(l.namn)} uppåt" ${forst ? "disabled" : ""}>↑</button>
            <button type="button" class="ikonknapp" data-ned="${esc(l.id)}" aria-label="Flytta ${esc(l.namn)} nedåt" ${sist ? "disabled" : ""}>↓</button>
            ${l.kanTasBort ? `<button type="button" class="ikonknapp" data-bort="${esc(l.id)}" aria-label="Ta bort ${esc(l.namn)}">×</button>` : ""}
          </span>
        </div>
        ${av ? `<p class="kl-hjalp">${esc(l.ejTillgangligt as string)}</p>` : `
        <div class="kl-opacitet">
          <label class="etikett-liten" for="lo-${esc(l.id)}">Täckning</label>
          <input type="range" id="lo-${esc(l.id)}" data-opacitet="${esc(l.id)}" min="0" max="100" value="${Math.round(l.opacitet * 100)}" aria-label="Täckning för ${esc(l.namn)}"/>
        </div>
        ${l.hjalp ? `<p class="kl-hjalp">${esc(l.hjalp)}</p>` : ""}`}
      </li>`;
  };

  const rita = (): void => {
    rot.innerHTML = GRUPPER.filter((g) => !krokar.grupper || krokar.grupper.includes(g.id)).map((g) => {
      const innehall = lista.iGrupp(g.id);
      const fallt = lista.arHopfalld(g.id);
      return `
        <section class="lager-grupp" data-grupp="${g.id}">
          <h3 class="lager-grupp-rubrik">
            <button type="button" class="lager-fall" data-fall="${g.id}" aria-expanded="${!fallt}">
              <span class="lager-pil" aria-hidden="true">${fallt ? "+" : "−"}</span> ${esc(g.namn)}
              <span class="dampad liten-text">(${innehall.length})</span>
            </button>
          </h3>
          ${fallt ? "" : innehall.length === 0 ? `<p class="kl-hjalp">${esc(g.hjalp)} Inget här än.</p>` : `<ul class="kl-lista">${innehall.map((l, i) => rad(l, i === 0, i === innehall.length - 1)).join("")}</ul>`}
        </section>`;
    }).join("");
  };

  const tillstand = (): void => {
    if (hoppaOver) {
      hoppaOver = false;
      return;
    }
    rita();
    krokar.andrad();
  };
  lista.vidAndring(tillstand);

  rot.addEventListener("click", (h) => {
    const m = h.target as HTMLElement;
    const g = m.closest<HTMLElement>("[data-fall]");
    if (g) return lista.vaxlaHopfalld(g.dataset.fall as never);
    const upp = m.closest<HTMLElement>("[data-upp]");
    if (upp) return lista.flytta(upp.dataset.upp as string, -1);
    const ned = m.closest<HTMLElement>("[data-ned]");
    if (ned) return lista.flytta(ned.dataset.ned as string, 1);
    const bort = m.closest<HTMLElement>("[data-bort]");
    if (bort) return krokar.taBort?.(bort.dataset.bort as string);
    const vy = m.closest<HTMLElement>("[data-vy]");
    if (vy) {
      const id = vy.dataset.vy as string;
      lista.sattAktivVy(lista.aktivVy?.id === id ? null : id);
    }
  });
  rot.addEventListener("change", (h) => {
    const m = h.target as HTMLInputElement;
    if (m.dataset.synlig) lista.sattSynlig(m.dataset.synlig, m.checked);
  });
  rot.addEventListener("input", (h) => {
    const m = h.target as HTMLInputElement;
    if (m.dataset.opacitet) {
      // Reglaget ritas inte om medan man drar i det, annars tappar det fokus.
      hoppaOver = true;
      lista.sattOpacitet(m.dataset.opacitet, Number(m.value) / 100);
      hoppaOver = false;
      krokar.andrad();
    }
  });
  rot.addEventListener("dragstart", (h) => {
    const li = (h.target as HTMLElement).closest<HTMLElement>("[data-lager]");
    if (!li) return;
    dragId = li.dataset.lager as string;
    h.dataTransfer?.setData("text/plain", dragId);
    if (h.dataTransfer) h.dataTransfer.effectAllowed = "move";
  });
  rot.addEventListener("dragover", (h) => {
    const li = (h.target as HTMLElement).closest<HTMLElement>("[data-lager]");
    if (li && dragId && lista.hamta(li.dataset.lager as string)?.grupp === lista.hamta(dragId)?.grupp) h.preventDefault();
  });
  rot.addEventListener("drop", (h) => {
    const li = (h.target as HTMLElement).closest<HTMLElement>("[data-lager]");
    if (!li || !dragId) return;
    h.preventDefault();
    const r = li.getBoundingClientRect();
    lista.flyttaTill(dragId, li.dataset.lager as string, h.clientY > r.top + r.height / 2);
    dragId = null;
  });
  rita();
  return rita;
}
