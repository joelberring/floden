/*
 * Frågekatalogen (`#/fragor`): alla frågor Floden kan besvara, per ämne, med samma ämnen, ord och
 * statusetiketter som steg 3 (Skyfall, Dagsljus och sol, Luft, Buller, Ekologi; Floden svarar,
 * Delvis, Underlag saknas). Bara läsning: frågorna väljs och beräkningarna körs i steg 3 i ett
 * projekt, så det finns ingen andra frågesida med egen körning. Sidan ersätter den äldre
 * Utredningsfrågor-sidan (K20-körningen fanns bara där och finns kvar i kommandoraden: `floden fraga kor`).
 */
import { hamtaProjektLista } from "../api.ts";
import { katalogAmnenHtml, katalogListaHtml, katalogProjektHtml } from "./fragekatalog-html.ts";
import { hamtaFragelista, type FragelistaSvar } from "./fragor.ts";
import type { Avslutbar } from "./projektlista.ts";

export function visaFragekatalog(rot: HTMLElement): Avslutbar {
  let avslutad = false;
  const ac = new AbortController();
  let amne = "skyfall";
  let lista: FragelistaSvar | null = null;
  rot.innerHTML = `
    <div class="flode-sida">
      <section class="flode-bred" aria-labelledby="katalog-rubrik">
        <div class="flode-rubrikrad">
          <h1 id="katalog-rubrik" class="flode-h">Frågekatalog</h1>
          <div role="group" aria-label="Ämne" class="flode-chips" id="katalog-amnen"></div>
        </div>
        <p class="flode-dampad">Frågorna Floden kan besvara. Här väljs inga frågor och inget körs: det görs i steg 3, Frågor, i ett projekt.</p>
        <div class="flode-fraglista" id="katalog-lista"><p class="flode-dampad">Hämtar frågorna …</p></div>
        <p class="flode-dampad" id="katalog-not"></p>
        <h2 class="flode-h">Välj frågor i ett projekt</h2>
        <div id="katalog-projekt" aria-live="polite"><p class="flode-dampad">Hämtar projekten …</p></div>
      </section>
    </div>`;
  const el = <T extends HTMLElement>(id: string): T => rot.querySelector(`#${id}`) as T;
  const rita = (): void => {
    el("katalog-amnen").innerHTML = katalogAmnenHtml(lista, amne);
    if (!lista) return;
    el("katalog-lista").innerHTML = katalogListaHtml(lista, amne);
    const n = lista.fragor.filter((f) => f.amne === amne).length;
    el("katalog-not").textContent = `Visar ${n} av ${lista.fragor.length}. ${lista.anmarkning}`;
  };
  rita();
  rot.addEventListener(
    "click",
    (h) => {
      const m = (h.target as Element).closest<HTMLElement>("[data-amne]");
      if (!m?.dataset.amne) return;
      amne = m.dataset.amne;
      rita();
    },
    { signal: ac.signal },
  );
  const fragorLaddade = hamtaFragelista()
    .then((l) => {
      if (avslutad) return;
      lista = l;
      rita();
    })
    .catch(() => {
      if (avslutad) return;
      el("katalog-lista").innerHTML = `<p class="flode-fel" role="alert">Frågorna gick inte att hämta. Ladda om sidan, eller kontrollera anslutningen.</p>`;
    });
  const projekt = hamtaProjektLista()
    .then((s) => {
      if (!avslutad) el("katalog-projekt").innerHTML = katalogProjektHtml(s.projekt);
    })
    .catch(() => {
      if (!avslutad) el("katalog-projekt").innerHTML = `<p class="flode-dampad">Projekten gick inte att hämta. <a href="#/">Öppna projektlistan</a>.</p>`;
    });
  return {
    avsluta: () => {
      avslutad = true;
      ac.abort();
    },
    klar: Promise.all([fragorLaddade, projekt]).then(() => undefined),
  };
}
