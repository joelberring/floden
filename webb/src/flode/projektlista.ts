/*
 * Steg 1 · Projekt: projekten användaren har (vänsterlista med miniatyr, namn och en rad status) och
 * kartan över det valda projektet. "Nytt projekt" finns men säger "kommer snart": det finns inget
 * API för att skapa projekt.
 */
import { hamtaProjekt, hamtaProjektLista } from "../api.ts";
import { hamtaBildMedUtbredning, hamtaProjektInfo, type ProjektInfo } from "../karta/kartapi.ts";
import type { Kartmotor } from "../karta/kartmotor.ts";
import { esc, formatera } from "../sidor.ts";
import type { ProjektSvar } from "../types.ts";
import { flodeAdress } from "./adress.ts";
import { sattProjektNamn } from "./huvud.ts";
import { BasLager, byggnaderMedForslag, skapaKarta } from "./kartdel.ts";

interface Rad {
  id: string;
  namn: string;
  undertitel: string;
  paahittat: boolean;
  detalj: ProjektSvar | null;
  miniatyr: string | null;
}

/** Statusraden ur projektets egna avsnitt: vad som är beräknat. Inget påhittat. */
export function statusRad(p: ProjektSvar | null): string {
  if (!p) return "Status gick inte att hämta";
  const delar: string[] = [];
  if (p.avsnitt.some((a) => a.id === "vatten")) delar.push("skyfall");
  if (p.avsnitt.some((a) => a.id === "dagsljus")) delar.push("sol");
  if (delar.length === 0) return "Inget beräknat än";
  const text = delar.join(" och ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)} beräknade`;
}

export interface Avslutbar {
  avsluta(): void;
  klar: Promise<void>;
}

export function visaProjektlista(rot: HTMLElement, valtId: string | null): Avslutbar {
  let avslutad = false;
  let karta: Kartmotor | null = null;
  let bas: BasLager | null = null;
  const urler: string[] = [];
  rot.innerHTML = `
    <div class="flode-sida">
      <section class="flode-lista" aria-labelledby="projekt-rubrik">
        <div class="flode-listhuvud">
          <h1 id="projekt-rubrik" class="flode-h">Projekt</h1>
          <button type="button" class="flode-knapp svart liten" id="nytt-projekt" aria-describedby="nytt-not">Nytt projekt</button>
        </div>
        <div id="projekt-rader" class="flode-rader" aria-live="polite"><p class="flode-dampad">Hämtar projekten …</p></div>
        <p class="flode-not" id="nytt-not">Kommer snart: att skapa nya projekt här. Än så länge byggs projekt med kommandona i README (<code>floden demo</code>).</p>
      </section>
      <section class="flode-karta" aria-label="Karta över valt projekt">
        <div class="flode-karta-yta" id="projekt-karta"></div>
        <div class="flode-kartkort" id="projekt-kartkort" hidden></div>
      </section>
    </div>`;
  const rader = rot.querySelector<HTMLElement>("#projekt-rader") as HTMLElement;
  const kort = rot.querySelector<HTMLElement>("#projekt-kartkort") as HTMLElement;
  rot.querySelector<HTMLButtonElement>("#nytt-projekt")?.addEventListener("click", () => {
    rot.querySelector("#nytt-not")?.classList.add("framhav");
  });

  const visaKarta = async (r: Rad): Promise<void> => {
    let info: ProjektInfo;
    try {
      info = await hamtaProjektInfo(r.id);
    } catch (e) {
      kort.hidden = false;
      kort.innerHTML = `<span class="flode-fel" role="alert">Kartan gick inte att hämta: ${esc(e instanceof Error ? e.message : String(e))}</span>`;
      return;
    }
    if (avslutad) return;
    bas?.avsluta();
    karta?.avsluta();
    const yta = rot.querySelector<HTMLElement>("#projekt-karta") as HTMLElement;
    yta.innerHTML = "";
    karta = skapaKarta(yta, info);
    karta.settByggnadStil({ synlig: true, opacitet: 1 });
    karta.settByggnader(byggnaderMedForslag(info, null));
    bas = new BasLager(karta, info);
    void bas.ladda(r.id);
    const u = info.utbredning;
    kort.hidden = false;
    kort.innerHTML = `<strong>${esc(r.namn)}</strong><span class="flode-dampad">${esc(formatera(u.x_max - u.x_min, 0))} × ${esc(formatera(u.y_max - u.y_min, 0))} m · SWEREF 99 18 00</span>`;
  };

  const klar = (async () => {
    let lista: Rad[] = [];
    try {
      const s = await hamtaProjektLista();
      lista = await Promise.all(
        s.projekt.map(async (p): Promise<Rad> => {
          sattProjektNamn(p.id, p.namn);
          let detalj: ProjektSvar | null = null;
          let miniatyr: string | null = null;
          try {
            detalj = await hamtaProjekt(p.id);
          } catch {
            detalj = null;
          }
          try {
            const b = await hamtaBildMedUtbredning(`/api/kartverktyg/${encodeURIComponent(p.id)}/geometri/skuggning.png`);
            miniatyr = b.url;
            urler.push(b.url);
          } catch {
            miniatyr = null;
          }
          return { id: p.id, namn: p.namn, undertitel: p.undertitel, paahittat: p.paahittat, detalj, miniatyr };
        }),
      );
    } catch (e) {
      if (avslutad) return;
      rader.innerHTML = `<p class="flode-fel" role="alert">Projekten gick inte att hämta: ${esc(e instanceof Error ? e.message : String(e))}. Är servern igång?</p>`;
      return;
    }
    if (avslutad) return;
    if (lista.length === 0) {
      rader.innerHTML = `<p class="flode-dampad">Det finns inga projekt än. Demokvarteret byggs med <code>floden demo</code>; ladda sedan om sidan.</p>`;
      return;
    }
    let val: Rad = lista.find((r) => r.id === valtId) ?? (lista[0] as Rad);
    const ritaRader = (): void => {
      rader.innerHTML = lista
        .map((r) => {
          const pa = r.id === val.id;
          return `<a class="flode-projekt${pa ? " vald" : ""}" href="${flodeAdress(r.id, "forslag")}" data-projekt="${esc(r.id)}"${pa ? ' aria-current="true"' : ""}>
            ${r.miniatyr ? `<img src="${esc(r.miniatyr)}" alt="" />` : `<span class="flode-miniatyr-tom" aria-hidden="true"></span>`}
            <span class="flode-projekt-text">
              <span class="flode-projekt-namn">${esc(r.namn)}</span>
              <span class="flode-dampad">${esc(r.paahittat ? "Påhittat" : "Verkligt")} · ${esc(r.undertitel)}</span>
              <span class="flode-dampad">${esc(statusRad(r.detalj))}</span>
            </span>
          </a>`;
        })
        .join("");
    };
    ritaRader();
    // Första klicket på ett annat projekt väljer det och visar det på kartan; på det valda öppnas Förslag.
    rader.addEventListener("click", (h) => {
      const a = (h.target as Element).closest<HTMLAnchorElement>("a[data-projekt]");
      if (!a || a.dataset.projekt === val.id) return;
      h.preventDefault();
      val = lista.find((r) => r.id === a.dataset.projekt) ?? val;
      ritaRader();
      void visaKarta(val);
    });
    await visaKarta(val);
  })();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      bas?.avsluta();
      karta?.avsluta();
      for (const u of urler) URL.revokeObjectURL(u);
    },
  };
}
