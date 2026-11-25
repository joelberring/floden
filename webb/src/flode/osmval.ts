/*
 * Omkopplaren "OpenStreetMap-bakgrund" på kartan i steg 2 och 4: ett kryss som tänder och släcker OSM-lagret i lagerlistan
 * (samma val som i lagerpanelen, sparat per projekt). Släckt lager hämtar inget alls från OpenStreetMap (se `karta/osm.ts`).
 */
import { OSM_ID } from "../karta/osm.ts";
import type { LagerLista } from "../karta/lager.ts";
import { esc } from "../sidor.ts";

export function monteraOsmKryss(yta: HTMLElement, lager: LagerLista, andrad: () => void, signal?: AbortSignal): void {
  yta.classList.add("flode-osm");
  const rita = (): void => {
    const l = lager.hamta(OSM_ID);
    if (!l) {
      yta.hidden = true;
      return;
    }
    yta.hidden = false;
    const kryss = yta.querySelector<HTMLInputElement>("input");
    if (kryss) {
      kryss.checked = l.synlig;
      return;
    }
    yta.innerHTML = `<label class="flode-kryss"><input type="checkbox" id="${esc(yta.id)}-kryss"${l.synlig ? " checked" : ""} /> OpenStreetMap-bakgrund</label>`;
  };
  rita();
  lager.vidAndring(rita);
  yta.addEventListener(
    "change",
    (h) => {
      const m = h.target as HTMLInputElement;
      if (m.tagName !== "INPUT") return;
      lager.sattSynlig(OSM_ID, m.checked);
      andrad();
    },
    signal ? { signal } : undefined,
  );
}
