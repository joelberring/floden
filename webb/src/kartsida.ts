/*
 * Kartsidan för projektet (`#/karta/demokvarteret`): resultaten ur demokörningarna på en karta,
 * med lägena Före, Efter, Skillnad, Svep och Sida vid sida, avläsning under markören och
 * byggnadslager. Webben räknar inget: bilderna ritas av servern ur kärnans rasterfiler och
 * värdena läses ur samma rasterceller.
 */
import "./fragor.css";
import { hamtaAvlasning, hamtaProjekt, hamtaResultat } from "./api";
import type { AvlasningSvar, LagerPost, ResultatSvar } from "./fragor-typer";
import { LAGEN, lasKartan, type Lage } from "./kartforklaring";
import { RasterVy, type VyByggnad, type VyLager } from "./rastervy";
import { esc, formatera, PROJEKT_ID } from "./sidor";
import { lagerTekniskHtml, skalaHtml } from "./teckenforklaring";
import type { ProjektSvar, ProjektVy } from "./types";

interface Tillstand {
  projekt: ProjektSvar | null;
  fel: string | null;
  resultat: Map<string, ResultatSvar>;
  vy: ProjektVy | null;
  lage: Lage;
  svep: number;
  visaTak: boolean;
  visaByggnader: boolean;
}

const t: Tillstand = {
  projekt: null,
  fel: null,
  resultat: new Map(),
  vy: null,
  lage: "svep",
  svep: 0.5,
  visaTak: false,
  visaByggnader: true,
};

let rot: HTMLElement | null = null;
let vyA: RasterVy | null = null;
let vyB: RasterVy | null = null;
const bilder = new Map<string, { png: HTMLImageElement; tak: HTMLImageElement | null }>();
let avlasningSeq = 0;
let avlasningTimer: number | null = null;

function el<T extends HTMLElement>(id: string): T | null {
  return (rot?.querySelector(`#${id}`) as T | null) ?? null;
}

function avsluta(): void {
  vyA?.avsluta();
  vyB?.avsluta();
  vyA = null;
  vyB = null;
  bilder.clear();
  avlasningSeq += 1;
  if (avlasningTimer !== null) {
    window.clearTimeout(avlasningTimer);
    avlasningTimer = null;
  }
}

function lagerPost(r: ResultatSvar, id: string | null): LagerPost | null {
  return id ? (r.lager.find((l) => l.id === id) ?? null) : null;
}

/** Visar kartsidan. `vyId` är en vy ur projektet (`?vy=`), annars den första. */
export async function visaKarta(container: HTMLElement, vyId: string | null): Promise<void> {
  rot = container;
  avsluta();
  container.innerHTML = `<div class="sidbredd innehall karta-sida"><p class="dampad tom">Hämtar kartan …</p></div>`;
  if (!t.projekt) {
    try {
      t.projekt = await hamtaProjekt(PROJEKT_ID);
      t.fel = null;
    } catch (e) {
      t.fel = e instanceof Error ? e.message : String(e);
    }
  }
  if (rot !== container) return; // användaren har hunnit byta sida
  const p = t.projekt;
  if (!p || p.vyer.length === 0) {
    container.innerHTML = `
      <div class="sidbredd innehall karta-sida">
        <h1 class="sidrubrik stor">Karta</h1>
        <p class="hjalp-ruta" role="note">Det finns inget att visa på kartan än. Demokvarteret byggs med <code>floden demo</code>: kör det i en terminal och ladda om sidan.${t.fel ? ` (${esc(t.fel)})` : ""}</p>
      </div>`;
    return;
  }
  const onskad = vyId ? p.vyer.find((v) => v.id === vyId) : undefined;
  await valjVy(onskad ?? t.vy ?? p.vyer[0] ?? null);
}

async function valjVy(vy: ProjektVy | null): Promise<void> {
  const container = rot;
  if (!vy || !container) return;
  t.vy = vy;
  avsluta();
  if (!t.resultat.has(vy.korning_id)) {
    try {
      t.resultat.set(vy.korning_id, await hamtaResultat(vy.korning_id));
    } catch (e) {
      container.innerHTML = `<div class="sidbredd innehall karta-sida"><h1 class="sidrubrik stor">Karta</h1><p class="fel-text" role="alert">Resultatet gick inte att hämta: ${esc(e instanceof Error ? e.message : String(e))}</p></div>`;
      return;
    }
  }
  if (rot !== container || t.vy !== vy) return;
  history.replaceState(null, "", `#/karta/${PROJEKT_ID}?vy=${encodeURIComponent(vy.id)}`);
  ritaSkelett(container, vy);
}

function ritaSkelett(container: HTMLElement, vy: ProjektVy): void {
  const p = t.projekt;
  const r = t.resultat.get(vy.korning_id);
  if (!p || !r) return;
  const vyKnappar = p.vyer
    .map(
      (v) =>
        `<button type="button" class="flik${v.id === vy.id ? " vald" : ""}" data-vy="${esc(v.id)}" aria-pressed="${v.id === vy.id}">${esc(v.rubrik)}</button>`,
    )
    .join("");
  const lagesKnappar = LAGEN.map(
    ([id, namn]) =>
      `<button type="button" class="flik${id === t.lage ? " vald" : ""}" data-lage="${id}" aria-pressed="${id === t.lage}">${esc(namn)}</button>`,
  ).join("");
  container.innerHTML = `
    <div class="sidbredd innehall karta-sida">
      <div class="rubrikrad">
        <div>
          <p class="projekt-etikett"><span class="chip">Påhittat kvarter</span></p>
          <h1 class="sidrubrik">Karta: ${esc(p.namn)}</h1>
        </div>
        <a class="textlank" href="#/projekt/${PROJEKT_ID}">← Till projektet</a>
      </div>

      <div class="flikar flikrad" role="group" aria-label="Välj fråga">${vyKnappar}</div>
      <p class="andring-text"><span class="etikett-liten">Förändring</span> ${esc(vy.andring)}</p>

      <div class="karta-rad">
        <div class="vy-kolumn">
          <div class="vy-verktyg">
            <div class="flikar" role="group" aria-label="Visa" id="lagen">${lagesKnappar}</div>
            <span class="knapp-par">
              <button type="button" class="knapp knapp-liten" id="vy-in" aria-label="Zooma in">+</button>
              <button type="button" class="knapp knapp-liten" id="vy-ut" aria-label="Zooma ut">−</button>
              <button type="button" class="knapp knapp-liten" id="vy-anpassa">Anpassa</button>
            </span>
          </div>
          <div class="karta-par" id="karta-par">
            <figure class="karta-ruta">
              <figcaption id="rubrik-a" class="karta-bildtext"></figcaption>
              <div id="vy-yta" class="vy-yta"></div>
            </figure>
            <figure class="karta-ruta" id="ruta-b" hidden>
              <figcaption id="rubrik-b" class="karta-bildtext"></figcaption>
              <div id="vy-yta-b" class="vy-yta"></div>
            </figure>
          </div>
          <div id="svep-rad" class="svep-rad" hidden>
            <span class="etikett-liten">Före</span>
            <input id="svep-reglage" type="range" min="0" max="100" value="${Math.round(t.svep * 100)}" aria-label="Svepgräns: till vänster före, till höger efter"/>
            <span class="etikett-liten">Efter</span>
          </div>
          <div id="avlasning" class="avlasning" role="status"><span class="dampad">Håll markören över kartan för att läsa värden.</span></div>
        </div>
        <aside class="lager-kolumn">
          <section class="lasruta" aria-labelledby="las-rubrik">
            <h2 class="etikett-liten" id="las-rubrik">Så läser du kartan</h2>
            <p id="las-text"></p>
          </section>
          <div id="teckenforklaring"></div>
          <div class="kryss-grupp">
            <label class="kryss-rad"><input type="checkbox" id="visa-byggnader" ${t.visaByggnader ? "checked" : ""}/> Visa byggnader</label>
            <label class="kryss-rad" id="tak-rad" hidden><input type="checkbox" id="visa-tak" ${t.visaTak ? "checked" : ""}/> Visa värden även på tak</label>
          </div>
        </aside>
      </div>

      <details class="teknisk" id="teknisk">
        <summary>Teknisk information</summary>
        <ul class="enkel-lista" id="teknisk-lista"></ul>
      </details>
    </div>`;

  vyA = new RasterVy(el("vy-yta") as HTMLElement);
  vyB = new RasterVy(el("vy-yta-b") as HTMLElement);
  vyA.onVyAndrad = () => vyA && vyB?.settVy(vyA.hamtaVy());
  vyB.onVyAndrad = () => vyB && vyA?.settVy(vyB.hamtaVy());
  vyA.onMarkor = planeraAvlasning;
  vyB.onMarkor = planeraAvlasning;
  for (const l of r.lager) {
    if (![vy.fore_lager, vy.efter_lager, vy.skillnad_lager].includes(l.id)) continue;
    const png = new Image();
    png.onload = () => {
      vyA?.rita();
      vyB?.rita();
    };
    png.src = l.png;
    let tak: HTMLImageElement | null = null;
    if (l.png_med_tak) {
      tak = new Image();
      tak.onload = () => {
        vyA?.rita();
        vyB?.rita();
      };
      tak.src = l.png_med_tak;
    }
    bilder.set(l.id, { png, tak });
  }

  for (const k of Array.from(container.querySelectorAll<HTMLButtonElement>("[data-vy]"))) {
    k.addEventListener("click", () => {
      const ny = p.vyer.find((v) => v.id === k.dataset.vy);
      if (ny && ny !== t.vy) void valjVy(ny);
    });
  }
  for (const k of Array.from(container.querySelectorAll<HTMLButtonElement>("[data-lage]"))) {
    k.addEventListener("click", () => {
      t.lage = k.dataset.lage as Lage;
      for (const x of Array.from(container.querySelectorAll<HTMLButtonElement>("[data-lage]"))) {
        const pa = x.dataset.lage === t.lage;
        x.classList.toggle("vald", pa);
        x.setAttribute("aria-pressed", String(pa));
      }
      uppdatera();
      // Vyernas storlek ändras mellan lägena (en eller två kartor): passa om kartan.
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => anpassa()));
    });
  }
  el("vy-in")?.addEventListener("click", () => {
    vyA?.zooma(0.7);
  });
  el("vy-ut")?.addEventListener("click", () => {
    vyA?.zooma(1 / 0.7);
  });
  el("vy-anpassa")?.addEventListener("click", () => anpassa());
  el<HTMLInputElement>("svep-reglage")?.addEventListener("input", (h) => {
    t.svep = Number((h.target as HTMLInputElement).value) / 100;
    vyA?.settSvep(t.svep);
  });
  el<HTMLInputElement>("visa-byggnader")?.addEventListener("change", (h) => {
    t.visaByggnader = (h.target as HTMLInputElement).checked;
    uppdatera();
  });
  el<HTMLInputElement>("visa-tak")?.addEventListener("change", (h) => {
    t.visaTak = (h.target as HTMLInputElement).checked;
    uppdatera();
  });
  uppdatera();
  // Vyn måste ha mätt sin storlek innan den kan passas mot området.
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => anpassa()));
}

function anpassa(): void {
  const vy = t.vy;
  const r = vy ? t.resultat.get(vy.korning_id) : undefined;
  const bas = r && vy ? (lagerPost(r, vy.fore_lager) ?? lagerPost(r, vy.skillnad_lager)) : null;
  if (!bas) return;
  vyA?.anpassa(bas.utbredning);
  vyB?.settVy(vyA?.hamtaVy() ?? { cx: 0, cy: 0, mpp: 1 });
}

function byggnaderFor(r: ResultatSvar, lager: LagerPost | null, del?: "vanster" | "hoger"): VyByggnad[] {
  if (!lager || !t.visaByggnader) return [];
  const sid = lager.typ === "skillnad" ? (lager.efter ?? lager.scenario) : lager.scenario;
  const poster = r.byggnader?.scenarier[sid] ?? [];
  return poster.map((b) => ({ ring: b.ring, hal: b.hal, status: b.status, ...(del ? { del } : {}) }));
}

/** Ställer in vyerna efter valt läge. */
function uppdatera(): void {
  const vy = t.vy;
  const r = vy ? t.resultat.get(vy.korning_id) : undefined;
  if (!vy || !r || !vyA || !vyB) return;
  const fore = lagerPost(r, vy.fore_lager);
  const efter = lagerPost(r, vy.efter_lager);
  const skillnad = lagerPost(r, vy.skillnad_lager);
  const lage = t.lage;

  const vyLager = (l: LagerPost | null, synlig: boolean, del?: "vanster" | "hoger"): VyLager[] => {
    if (!l) return [];
    const b = bilder.get(l.id);
    if (!b) return [];
    return [
      {
        id: l.id,
        bild: t.visaTak && b.tak ? b.tak : b.png,
        utbredning: l.utbredning,
        opacitet: 1,
        synlig,
        ...(del ? { del } : {}),
      },
    ];
  };

  const rutaB = el("ruta-b");
  if (rutaB) rutaB.hidden = lage !== "sidavid";
  const par = el("karta-par");
  par?.classList.toggle("tva", lage === "sidavid");
  const svepRad = el("svep-rad");
  if (svepRad) svepRad.hidden = lage !== "svep";

  const textA = el("rubrik-a");
  const textB = el("rubrik-b");
  vyB.settSvep(null);
  if (lage === "fore") {
    vyA.settLager(vyLager(fore, true));
    vyA.settByggnader(byggnaderFor(r, fore));
    vyA.settSvep(null);
    if (textA) textA.textContent = `Före · ${vy.mattnamn}`;
  } else if (lage === "efter") {
    vyA.settLager(vyLager(efter, true));
    vyA.settByggnader(byggnaderFor(r, efter));
    vyA.settSvep(null);
    if (textA) textA.textContent = `Efter: ${vy.efter_namn.toLowerCase()} · ${vy.mattnamn}`;
  } else if (lage === "skillnad") {
    vyA.settLager(vyLager(skillnad, true));
    vyA.settByggnader(byggnaderFor(r, skillnad));
    vyA.settSvep(null);
    if (textA) textA.textContent = `Skillnad (efter minus före) · ${vy.mattnamn}`;
  } else if (lage === "svep") {
    vyA.settLager([...vyLager(fore, true, "vanster"), ...vyLager(efter, true, "hoger")]);
    vyA.settByggnader([...byggnaderFor(r, fore, "vanster"), ...byggnaderFor(r, efter, "hoger")]);
    vyA.settSvep(t.svep);
    if (textA) textA.textContent = `Före (vänster) och efter: ${vy.efter_namn.toLowerCase()} (höger) · ${vy.mattnamn}`;
  } else {
    vyA.settLager(vyLager(fore, true));
    vyA.settByggnader(byggnaderFor(r, fore));
    vyA.settSvep(null);
    vyB.settLager(vyLager(efter, true));
    vyB.settByggnader(byggnaderFor(r, efter));
    if (textA) textA.textContent = "Före";
    if (textB) textB.textContent = `Efter: ${vy.efter_namn.toLowerCase()}`;
  }
  const notis = fore?.tak_maskat && !t.visaTak ? "Ljusgrå yta: tak, inte mark" : null;
  vyA.settNotis(notis);
  vyB.settNotis(notis);
  vyB.settVy(vyA.hamtaVy());

  const lasText = el("las-text");
  if (lasText) lasText.textContent = lasKartan(vy, lage);
  const takRad = el("tak-rad");
  if (takRad) takRad.hidden = !(fore?.tak_maskat || skillnad?.tak_maskat);
  const tf = el("teckenforklaring");
  if (tf) {
    const visad = lage === "skillnad" ? skillnad : (fore ?? skillnad);
    const rubrik =
      lage === "skillnad" ? "Skillnad" : `${vy.mattnamn}${visad?.enhet ? ` (${visad.enhet})` : ""}`;
    tf.innerHTML = visad
      ? `<div class="teckenforklaring"><h3 class="etikett-liten">${esc(rubrik)}</h3>${skalaHtml(visad)}
         ${lage === "skillnad" ? "" : `<p class="dampad liten-text">Samma skala före och efter.</p>`}</div>`
      : "";
  }
  const lista = el("teknisk-lista");
  if (lista) {
    const lagerTeknik = [fore, efter, skillnad]
      .filter((l): l is LagerPost => l !== null)
      .map(lagerTekniskHtml)
      .join("");
    lista.innerHTML = `
      <li>Körning: <span class="mono">${esc(vy.korning_id)}</span>, ${r.sammanfattning.jobb} beräkningar</li>
      <li>Koordinatsystem: SWEREF 99 18 00 (EPSG:3011). <span id="markor-rad" class="mono">Markörens läge visas här.</span></li>
      ${lagerTeknik}`;
  }
}

/* ---------- Värden under markören ---------- */

function planeraAvlasning(e: number | null, n: number | null): void {
  if (avlasningTimer !== null) window.clearTimeout(avlasningTimer);
  avlasningTimer = null;
  const vy = t.vy;
  const rad = el("markor-rad");
  if (rad) {
    rad.textContent =
      e === null || n === null
        ? "Markörens läge visas här."
        : `E ${Math.round(e).toLocaleString("sv-SE")} · N ${Math.round(n).toLocaleString("sv-SE")}`;
  }
  if (e === null || n === null || !vy) {
    avlasningSeq += 1;
    visaAvlasning(null);
    return;
  }
  avlasningTimer = window.setTimeout(() => void lasAvlasning(vy, e, n), 90);
}

async function lasAvlasning(vy: ProjektVy, e: number, n: number): Promise<void> {
  const nr = ++avlasningSeq;
  try {
    const svar = await hamtaAvlasning(vy.korning_id, vy.skillnad_lager, e, n);
    if (nr === avlasningSeq) visaAvlasning(svar, vy);
  } catch {
    if (nr === avlasningSeq) visaAvlasning(null);
  }
}

function visaAvlasning(a: AvlasningSvar | null, vy: ProjektVy | null = t.vy): void {
  const ruta = el("avlasning");
  if (!ruta) return;
  if (!a || !vy || a.varden.every((x) => !x.inom)) {
    ruta.innerHTML = `<span class="dampad">Håll markören över kartan för att läsa värden.</span>`;
    return;
  }
  const rader: Array<[string, string | null]> = [
    ["Före", vy.fore_lager],
    [`Efter: ${vy.efter_namn.toLowerCase()}`, vy.efter_lager],
    ["Skillnad", vy.skillnad_lager],
  ];
  const delar = rader.flatMap(([namn, id]) => {
    const x = a.varden.find((v) => v.lager_id === id && v.inom);
    if (!x) return [];
    let text: string;
    if (x.tak && !t.visaTak) text = "tak – inte mark";
    else if (x.varde === null) text = "inget värde";
    else {
      const tecken = x.typ === "skillnad" && x.varde > 0 ? "+" : "";
      text = `${tecken}${formatera(x.varde, 2)}${x.enhet ? ` ${x.enhet}` : ""}${x.tak ? " (tak)" : ""}`;
    }
    return [`<span class="avl-post"><span class="etikett-liten">${esc(namn)}</span> <span class="mono">${esc(text)}</span></span>`];
  });
  ruta.innerHTML = delar.join("");
}
