/*
 * Underlag från andra tjänster i steg 2: adressen till en WMS-tjänst (läs lager, välj lager) eller en
 * kakelmall med {z}, {x} och {y}. Inget är förvalt, och tjänsten anropas via servern (SSRF-skydd).
 * Underlagen sparas per projekt i webbläsaren och ligger som lager i lagerpanelen. Flyttat från den
 * gamla kartsidan.
 */
import { lasWmsLager } from "../karta/kartapi.ts";
import { giltigKakelmall, lasUnderlag, sparaUnderlag, vardAv, type Underlag, type WmsSvar } from "../karta/wms.ts";
import { esc } from "../sidor.ts";
import type { BasLager } from "./kartdel.ts";

/** Underlaget som ett lager-id: samma id används i lagerlistan och i sparade underlag. */
export const underlagLagerId = (u: Pick<Underlag, "id">): string => `u:${u.id}`;

/** Tar bort ett underlag ur lagerlistan och ur den sparade listan. */
export function taBortUnderlag(projekt: string, bas: BasLager, lagerId: string): void {
  if (!lagerId.startsWith("u:")) return;
  sparaUnderlag(
    projekt,
    lasUnderlag(projekt).filter((u) => underlagLagerId(u) !== lagerId),
  );
  bas.lager.tabort(lagerId);
}

/** Formuläret för att lägga till underlag. Ritar om sig själv; lagren går via `bas`. */
export function monteraUnderlagForm(rot: HTMLElement, projekt: string, bas: BasLager, signal: AbortSignal): void {
  let wms: WmsSvar | null = null;
  let fel: string | null = null;
  const lagg = (u: Underlag): void => {
    sparaUnderlag(projekt, [...lasUnderlag(projekt), u]);
    bas.registreraUnderlag(u);
  };
  const rita = (): void => {
    const adress = rot.querySelector<HTMLInputElement>("#wms-url")?.value ?? "";
    const anv = wms?.anvandbara ?? [];
    rot.innerHTML = `
      <p class="flode-liten flode-dampad">Ange adressen till en WMS-tjänst eller en kakelmall med {z}, {x} och {y}. Inget är förvalt. Tjänsten anropas via servern.</p>
      <div class="flode-falt"><label for="wms-url">Adress</label><input id="wms-url" type="url" placeholder="https://…" value="${esc(adress)}" autocomplete="off" /></div>
      <div class="flode-rad"><button type="button" class="flode-knapp" id="wms-lasa">Läs lager</button><button type="button" class="flode-knapp" id="kakel-lagg">Lägg till som kakelmall</button></div>
      ${fel ? `<p class="flode-fel flode-liten" role="alert">${esc(fel)}</p>` : ""}
      ${
        anv.length > 0
          ? `<div class="flode-falt"><label for="wms-lager">Lager</label><select id="wms-lager">${anv.map((l) => `<option value="${esc(l.namn)}">${esc(l.titel || l.namn)}</option>`).join("")}</select></div>
             <div class="flode-rad"><button type="button" class="flode-knapp" id="wms-lagg">Lägg till lagret</button></div>`
          : ""
      }`;
  };
  const adress = (): string => (rot.querySelector<HTMLInputElement>("#wms-url")?.value ?? "").trim();
  rot.addEventListener(
    "click",
    (h) => {
      const k = (h.target as Element).closest<HTMLButtonElement>("button");
      if (!k) return;
      if (k.id === "wms-lasa") {
        fel = null;
        const url = adress();
        void lasWmsLager(url).then(
          (svar) => {
            wms = svar;
            if (svar.anvandbara.length === 0) fel = "Tjänsten har inga lager som kan visas i kartan (den måste kunna leverera EPSG:3857 eller EPSG:4326).";
            rita();
          },
          (e: unknown) => {
            wms = null;
            fel = e instanceof Error ? e.message : String(e);
            rita();
          },
        );
      } else if (k.id === "wms-lagg") {
        const val = rot.querySelector<HTMLSelectElement>("#wms-lager")?.value;
        const l = wms?.anvandbara.find((x) => x.namn === val);
        if (!wms || !l || !l.stod) return;
        lagg({ id: `wms-${Date.now().toString(36)}`, typ: "wms", namn: l.titel || l.namn, url: adress(), lager: l.namn, stod: l.stod, version: wms.version, kalla: `WMS från ${vardAv(adress())}` });
      } else if (k.id === "kakel-lagg") {
        const mall = adress();
        if (!giltigKakelmall(mall)) {
          fel = "En kakelmall måste börja med http eller https och innehålla {z}, {x} och {y}.";
          rita();
          return;
        }
        fel = null;
        lagg({ id: `kakel-${Date.now().toString(36)}`, typ: "kakel", namn: `Kakel från ${vardAv(mall) || "tjänst"}`, url: mall, kalla: `Kakel från ${vardAv(mall)}` });
        rita();
      }
    },
    { signal },
  );
  rita();
}
