/*
 * Sidan Frågor: välj en utredningsfråga (K20) -> se planen (rutor, cache) -> kör -> se resultatet
 * (kartvy i SWEREF-koordinater, varningar, mätvärden). Allt beräknas av kärnan; klienten visar.
 * Webben körs lokalt utan inloggning.
 */
import "./fragor.css";
import {
  hamtaFragor,
  hamtaKorning,
  hamtaResultat,
  hamtaTabellForhandsvisning,
  planeraFraga,
  startaKorning,
} from "./api";
import type {
  DataTabellRef,
  FragaPost,
  KorningSvar,
  LagerPost,
  MatvardeTabell,
  PlanSvar,
  ResultatSvar,
  Utbredning,
  VarningPost,
} from "./fragor-typer";
import { RasterVy, type VyLager } from "./rastervy";
import { esc } from "./sidor";

const OSM_MALL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const LAGRING_BAKGRUND = "floden.bakgrund";
const LAGRING_BAKGRUND_URL = "floden.bakgrund.url";

interface Tillstand {
  fragor: FragaPost[] | null;
  fragemappar: string[];
  fel: string | null;
  vald: FragaPost | null;
  plan: PlanSvar | null;
  planFel: string | null;
  korning: KorningSvar | null;
  resultat: ResultatSvar | null;
  resultatFel: string | null;
  aktivAnalys: string | null;
  aktivtLager: string | null;
  fleraLager: boolean;
  opacitet: number;
  forhandsvisning: { ref: DataTabellRef; kolumner: string[]; rader: Array<Record<string, unknown>>; antal: number } | null;
}

const t: Tillstand = {
  fragor: null,
  fragemappar: [],
  fel: null,
  vald: null,
  plan: null,
  planFel: null,
  korning: null,
  resultat: null,
  resultatFel: null,
  aktivAnalys: null,
  aktivtLager: null,
  fleraLager: false,
  opacitet: 1,
  forhandsvisning: null,
};

let vy: RasterVy | null = null;
let vyLager = new Map<string, VyLager>();
let timer: number | null = null;
let rot: HTMLElement | null = null;

/* ---------- Formatering ---------- */

const TAL = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 4 });
const HELTAL = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 });

export function formateraVarde(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "number") return Number.isFinite(v) ? TAL.format(v) : String(v);
  if (typeof v === "boolean") return v ? "ja" : "nej";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function el<T extends HTMLElement>(id: string): T | null {
  return (rot?.querySelector(`#${id}`) as T | null) ?? null;
}

function satt(id: string, html: string): void {
  const e = el(id);
  if (e) e.innerHTML = html;
}

/* ---------- Sidans skelett ---------- */

export function visaFragor(container: HTMLElement, ref: string | null): void {
  rot = container;
  if (!container.querySelector(".fragor-sida")) {
    container.innerHTML = `
      <div class="sidbredd innehall fragor-sida">
        <div class="rubrikrad">
          <h1 class="sidrubrik">Frågor</h1>
          <p class="dampad fragor-notis">Körs lokalt utan inloggning. Resultat sparas på den här datorn.</p>
        </div>
        <section id="fr-val" aria-labelledby="fr-val-rubrik"></section>
        <section id="fr-plan" aria-labelledby="fr-plan-rubrik" hidden></section>
        <section id="fr-resultat" aria-labelledby="fr-resultat-rubrik" hidden></section>
      </div>`;
    avslutaVy();
    ritaVal();
    ritaPlan();
    ritaResultat();
  }
  void laddaFragor().then(() => {
    if (ref && t.vald?.ref !== ref) {
      const post = t.fragor?.find((f) => f.ref === ref);
      if (post) void valj(post, false);
    }
  });
}

async function laddaFragor(): Promise<void> {
  if (t.fragor) return;
  try {
    const s = await hamtaFragor();
    t.fragor = s.fragor;
    t.fragemappar = s.fragemappar;
    t.fel = null;
  } catch (e) {
    t.fel = e instanceof Error ? e.message : String(e);
  }
  ritaVal();
}

/* ---------- 1. Välj fråga ---------- */

function ritaVal(): void {
  let innehall: string;
  if (t.fel) {
    innehall = `<p class="fel-text" role="alert">Kunde inte hämta frågorna: ${esc(t.fel)}. Starta API:t med <code>PYTHONPATH=src python -m webb.api</code>.</p>`;
  } else if (!t.fragor) {
    innehall = `<p class="dampad tom">Hämtar frågor …</p>`;
  } else if (t.fragor.length === 0) {
    innehall = `<p class="dampad tom">Inga frågefiler i ${t.fragemappar.map(esc).join(", ") || "frågemappen"}. Sätt <code>[webb] fragemappar</code> i config.toml.</p>`;
  } else {
    innehall = `<ul class="fraga-lista">${t.fragor.map(fragaRad).join("")}</ul>`;
  }
  satt(
    "fr-val",
    `<div class="sektionsrad linje-under-stark">
       <h2 class="etikett" id="fr-val-rubrik">1 · Välj fråga</h2>
       <span class="dampad">Frågefiler (K20) i ${t.fragemappar.map(esc).join(", ") || "…"}</span>
     </div>${innehall}`,
  );
  for (const knapp of Array.from(rot?.querySelectorAll<HTMLButtonElement>("[data-fraga]") ?? [])) {
    knapp.addEventListener("click", () => {
      const post = t.fragor?.find((f) => f.ref === knapp.dataset.fraga);
      if (post) void valj(post, true);
    });
  }
}

function fragaRad(f: FragaPost): string {
  const vald = t.vald?.ref === f.ref;
  const analyser = (f.analyser ?? []).map((a) => `<span class="chip">${esc(a.losare)}</span>`).join("");
  const scen = f.scenarier ? `${f.scenarier.length} scenarier` : "";
  const problem = !f.giltig
    ? `<span class="fel-text">Kan inte köras: ${esc(f.fel ?? "okänt fel")}</span>`
    : (f.indata_saknas?.length ?? 0) > 0
      ? `<span class="varn-text">Indata saknas (${f.indata_saknas?.length}): se hjälptexten nedan när frågan är vald.</span>`
      : "";
  return `
    <li>
      <button type="button" class="fraga-rad${vald ? " vald" : ""}" data-fraga="${esc(f.ref)}" aria-pressed="${vald}" ${f.giltig ? "" : "disabled"}>
        <span class="lista-titel">${esc(f.fraga_id)}</span>
        <span class="dampad">${esc(f.beskrivning)}</span>
        <span class="fraga-meta">${analyser}<span class="dampad">${esc(scen)} · ${esc(f.fil)}</span></span>
        ${problem}
      </button>
    </li>`;
}

async function valj(post: FragaPost, uppdateraAdress: boolean): Promise<void> {
  stoppaTimer();
  t.vald = post;
  t.plan = null;
  t.planFel = null;
  t.korning = null;
  t.resultat = null;
  t.resultatFel = null;
  t.aktivAnalys = null;
  t.aktivtLager = null;
  t.forhandsvisning = null;
  avslutaVy();
  if (uppdateraAdress) {
    history.replaceState(null, "", `#/fragor/${encodeURIComponent(post.ref)}`);
  }
  ritaVal();
  ritaPlan();
  ritaResultat();
  try {
    t.plan = await planeraFraga(post.ref);
  } catch (e) {
    t.planFel = e instanceof Error ? e.message : String(e);
  }
  if (t.vald?.ref === post.ref) ritaPlan();
}

/* ---------- 2. Plan och körning ---------- */

function ritaPlan(): void {
  const sek = el("fr-plan");
  if (!sek) return;
  sek.hidden = t.vald === null;
  if (!t.vald) return;
  const v = t.vald;
  const hjalp =
    (v.indata_saknas?.length ?? 0) > 0
      ? `<div class="hjalp-ruta" role="note">
           <strong>Indata saknas:</strong> ${(v.indata_saknas ?? []).map((s) => `<code>${esc(s)}</code>`).join(", ")}
           ${v.hjalp ? `<pre>${esc(v.hjalp)}</pre>` : ""}
         </div>`
      : "";
  let kropp: string;
  if (t.planFel) {
    kropp = `${hjalp}<p class="fel-text" role="alert">${esc(t.planFel)}</p>`;
  } else if (!t.plan) {
    kropp = `<p class="dampad tom">Planerar …</p>`;
  } else {
    kropp = planInnehall(t.plan, hjalp);
  }
  sek.innerHTML = `
    <div class="sektionsrad linje-under-stark">
      <h2 class="etikett" id="fr-plan-rubrik">2 · Plan</h2>
      <span class="dampad">${esc(v.fraga_id)}</span>
    </div>${kropp}`;
  el<HTMLButtonElement>("kor-fraga")?.addEventListener("click", () => void kor());
  ritaKorStatus();
}

function planInnehall(p: PlanSvar, hjalp: string): string {
  const s = p.sammanfattning;
  const rader = Object.entries(p.per_analys)
    .map(([analys, a]) => {
      const j = p.jobb.find((x) => x.analys === analys);
      const radie = j?.radie_m == null ? "global" : `${HELTAL.format(j.radie_m)} m`;
      return `<tr><td class="mono">${esc(analys)}</td><td class="mono">${esc(j?.losare ?? "")}@${esc(j?.version ?? "")}</td>
        <td class="hoger">${a.jobb}</td><td class="hoger">${a.i_cache}</td><td class="hoger">${a.att_kora}</td><td class="hoger">${esc(radie)}</td></tr>`;
    })
    .join("");
  const u = p.utbredning;
  const rutor = p.scenarier[0]?.antal_rutor ?? 0;
  return `
    ${hjalp}
    <div class="plan-rad">
      <div class="plan-tal">
        <div class="tal-kort"><span class="tal stor-tal">${s.jobb}</span><span class="etikett-liten">Jobb</span></div>
        <div class="tal-kort"><span class="tal stor-tal">${s.i_cache}</span><span class="etikett-liten">Finns i cachen</span></div>
        <div class="tal-kort"><span class="tal stor-tal">${s.att_kora}</span><span class="etikett-liten">Att köra</span></div>
      </div>
      <div class="plan-karta">
        ${planKartaSvg(p)}
        <p class="dampad liten-text">${rutor} rutor${u ? ` · E ${HELTAL.format(u.x_min)}–${HELTAL.format(u.x_max)} · N ${HELTAL.format(u.y_min)}–${HELTAL.format(u.y_max)} (SWEREF 99 18 00)` : ""}.
          Mörk ruta: alla jobb finns i cachen. Streckad: delvis. Vit: ska köras.</p>
      </div>
    </div>
    <div class="tabell-ruta">
      <table class="data-tabell">
        <thead><tr><th>Analys</th><th>Lösare</th><th class="hoger">Jobb</th><th class="hoger">I cache</th><th class="hoger">Att köra</th><th class="hoger">Påverkansradie</th></tr></thead>
        <tbody>${rader}</tbody>
      </table>
    </div>
    <div class="kor-rad">
      <button type="button" id="kor-fraga" class="knapp knapp-svart">${s.att_kora === 0 ? "Hämta från cachen" : "Kör frågan"}</button>
      <div id="kor-status" class="kor-status" role="status" aria-live="polite"></div>
    </div>`;
}

function planKartaSvg(p: PlanSvar): string {
  const u = p.utbredning;
  const sc = p.scenarier[0];
  if (!u || !sc) return "";
  const b = u.x_max - u.x_min;
  const h = u.y_max - u.y_min;
  const per = new Map<string, { alla: number; cache: number }>();
  for (const j of p.jobb) {
    if (!j.ruta) continue;
    const e = per.get(j.ruta) ?? { alla: 0, cache: 0 };
    e.alla += 1;
    e.cache += j.i_cache ? 1 : 0;
    per.set(j.ruta, e);
  }
  const rutor = sc.rutor
    .map((r) => {
      const e = per.get(r.ruta_id);
      const klass = !e || e.cache === 0 ? "ingen" : e.cache === e.alla ? "alla" : "delvis";
      return `<rect class="plan-ruta ${klass}" x="${r.x_min - u.x_min}" y="${u.y_max - r.y_max}" width="${r.x_max - r.x_min}" height="${r.y_max - r.y_min}"><title>${esc(r.ruta_id)}: ${e ? `${e.cache} av ${e.alla} jobb i cache` : "globalt jobb"}</title></rect>`;
    })
    .join("");
  return `<svg class="plan-svg" viewBox="0 0 ${b} ${h}" style="aspect-ratio:${b} / ${h}" role="img" aria-label="Rutor i planen, färgade efter cachestatus">
    <defs><pattern id="rand" width="${b / 40}" height="${b / 40}" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="${b / 40}" stroke="#111" stroke-width="${b / 400}"/></pattern></defs>
    ${rutor}</svg>`;
}

function stoppaTimer(): void {
  if (timer !== null) {
    window.clearInterval(timer);
    timer = null;
  }
}

async function kor(): Promise<void> {
  const v = t.vald;
  if (!v) return;
  t.resultat = null;
  t.resultatFel = null;
  avslutaVy();
  ritaResultat();
  try {
    t.korning = await startaKorning(v.ref);
  } catch (e) {
    t.planFel = e instanceof Error ? e.message : String(e);
    ritaPlan();
    return;
  }
  ritaKorStatus();
  stoppaTimer();
  timer = window.setInterval(() => void fraga(), 700);
}

async function fraga(): Promise<void> {
  const k = t.korning;
  if (!k) return;
  try {
    t.korning = await hamtaKorning(k.korning_id);
  } catch (e) {
    stoppaTimer();
    t.planFel = e instanceof Error ? e.message : String(e);
    ritaPlan();
    return;
  }
  ritaKorStatus();
  if (t.korning.status === "klar" || t.korning.status === "fel") {
    stoppaTimer();
    if (t.korning.status === "klar") {
      await laddaResultat(t.korning.korning_id);
      // Planen visar nu allt som cachat.
      if (t.vald) {
        try {
          t.plan = await planeraFraga(t.vald.ref);
        } catch {
          /* planen är bara en visning; resultatet är redan hämtat */
        }
        ritaPlan();
      }
    }
  }
}

function ritaKorStatus(): void {
  const k = t.korning;
  const status = el("kor-status");
  const knapp = el<HTMLButtonElement>("kor-fraga");
  if (!status) return;
  const pagar = k !== null && (k.status === "ko" || k.status === "kor");
  if (knapp) knapp.disabled = pagar;
  if (!k) {
    status.innerHTML = "";
    return;
  }
  const { klara, totalt } = k.framsteg;
  if (pagar) {
    status.innerHTML = `<progress max="${Math.max(totalt, 1)}" value="${klara}" aria-label="Framsteg"></progress>
      <span>${k.status === "ko" ? "I kö" : "Kör"}: ${klara} av ${totalt} jobb</span>`;
  } else if (k.status === "fel") {
    status.innerHTML = `<span class="fel-text" role="alert">Körningen misslyckades: ${esc(k.fel ?? "okänt fel")}</span>`;
  } else {
    status.innerHTML = `<span>Klar: ${totalt} jobb. Körning <span class="mono">${esc(k.korning_id)}</span></span>`;
  }
}

/* ---------- 3. Resultat ---------- */

async function laddaResultat(id: string): Promise<void> {
  try {
    t.resultat = await hamtaResultat(id);
    t.resultatFel = null;
    const forsta = t.resultat.lager.find((l) => l.primar) ?? t.resultat.lager[0];
    t.aktivAnalys = forsta?.analys ?? null;
    t.aktivtLager = forsta ? valjStandardLager(t.resultat.lager, forsta.analys) : null;
  } catch (e) {
    t.resultatFel = e instanceof Error ? e.message : String(e);
  }
  ritaResultat();
}

function valjStandardLager(lager: LagerPost[], analys: string): string | null {
  const a = lager.filter((l) => l.analys === analys && l.primar);
  return (a.find((l) => l.typ === "skillnad") ?? a[0])?.id ?? null;
}

function avslutaVy(): void {
  vy?.avsluta();
  vy = null;
  vyLager = new Map();
}

function ritaResultat(): void {
  const sek = el("fr-resultat");
  if (!sek) return;
  const r = t.resultat;
  const visa = r !== null || t.resultatFel !== null;
  sek.hidden = !visa;
  if (!visa) return;
  if (!r) {
    sek.innerHTML = `<div class="sektionsrad linje-under-stark"><h2 class="etikett" id="fr-resultat-rubrik">3 · Resultat</h2></div>
      <p class="fel-text" role="alert">${esc(t.resultatFel ?? "")}</p>`;
    return;
  }
  const s = r.sammanfattning;
  sek.innerHTML = `
    <div class="sektionsrad linje-under-stark">
      <h2 class="etikett" id="fr-resultat-rubrik">3 · Resultat</h2>
      <span class="dampad">${esc(r.fraga_id)} · ${s.jobb} jobb, ${s.i_cache} från cache, ${s.att_kora} körda på ${TAL.format(s.total_tid_s)} s · <span class="mono">${esc(r.korning.korning_id)}</span></span>
    </div>
    ${varningarHtml(r.varningar)}
    <div class="resultat-karta">
      <div class="vy-kolumn">
        <div class="vy-verktyg">
          <span id="markor-rad" class="mono dampad">EPSG:3011 · SWEREF 99 18 00</span>
          <span class="knapp-par">
            <button type="button" class="knapp knapp-liten" id="vy-in" aria-label="Zooma in">+</button>
            <button type="button" class="knapp knapp-liten" id="vy-ut" aria-label="Zooma ut">−</button>
            <button type="button" class="knapp knapp-liten" id="vy-anpassa">Anpassa</button>
          </span>
        </div>
        <div id="vy-yta" class="vy-yta"></div>
        <details class="installningar">
          <summary class="etikett-liten">Inställningar</summary>
          <label class="kryss-rad"><input type="checkbox" id="bakgrund-pa" ${lasBakgrund() ? "checked" : ""}/> Bakgrundskarta (hämtar plattor från en extern tjänst; av som standard)</label>
          <label class="falt-rad">Plattmall <input type="text" id="bakgrund-url" class="kontroll" value="${esc(localStorage.getItem(LAGRING_BAKGRUND_URL) ?? OSM_MALL)}"/></label>
          <p class="dampad liten-text">Bakgrunden är bara en orientering. Syntetiska rutor har inga verkliga koordinater, så den stämmer bara för rutor som ligger på riktig plats. Källa: © OpenStreetMap-bidragsgivare när standardmallen används.</p>
        </details>
      </div>
      <aside class="lager-kolumn" id="lager-panel"></aside>
    </div>
    <div id="matvarden" class="resultat-block"></div>
    <div id="jamforelser" class="resultat-block"></div>
    <div id="datatabeller" class="resultat-block"></div>`;
  startaVy(r);
  ritaLagerPanel();
  ritaMatvarden(r);
  ritaJamforelser(r);
  ritaDatatabeller(r);
}

function varningarHtml(v: VarningPost[]): string {
  if (v.length === 0) {
    return `<div class="varning-ruta ingen" role="status"><span class="etikett-liten">Varningar</span> Inga varningar från lösarna.</div>`;
  }
  const rader = v
    .map((w) => {
      const var_ = [
        w.antal_jobb > 0 ? `${w.antal_jobb} jobb` : "webben",
        w.analyser.join(", "),
        w.scenarier.join(", "),
      ]
        .filter((x) => x)
        .join(" · ");
      const varianter =
        w.varianter.length > 0
          ? `<details><summary class="liten-text">${w.varianter.length} varianter (olika tal)</summary><ul class="varianter">${w.varianter
              .map((x) => `<li>${esc(x.text)} <span class="dampad">(${x.antal_jobb} jobb)</span></li>`)
              .join("")}</ul></details>`
          : "";
      return `<li><span>${esc(w.text)}</span><span class="dampad liten-text">${esc(var_)}</span>${varianter}</li>`;
    })
    .join("");
  return `<div class="varning-ruta" role="alert"><span class="etikett-liten">Varningar (${v.length})</span><ul>${rader}</ul></div>`;
}

/* ---------- Kartvy och lager ---------- */

function lasBakgrund(): boolean {
  return localStorage.getItem(LAGRING_BAKGRUND) === "1";
}

function startaVy(r: ResultatSvar): void {
  const yta = el("vy-yta");
  if (!yta) return;
  avslutaVy();
  vy = new RasterVy(yta);
  vy.onMarkor = (e, n) => {
    const rad = el("markor-rad");
    if (rad) {
      rad.textContent =
        e === null || n === null
          ? "EPSG:3011 · SWEREF 99 18 00"
          : `E ${HELTAL.format(e)} · N ${HELTAL.format(n)} · EPSG:3011`;
    }
  };
  for (const l of r.lager) {
    const bild = new Image();
    bild.onload = () => vy?.rita();
    bild.src = l.png;
    vyLager.set(l.id, { id: l.id, bild, utbredning: l.utbredning, opacitet: 1, synlig: false });
  }
  const alla = utbredningen(r.lager);
  if (alla) vy.anpassa(alla);
  uppdateraVyLager();
  el("vy-in")?.addEventListener("click", () => vy?.zooma(0.7));
  el("vy-ut")?.addEventListener("click", () => vy?.zooma(1 / 0.7));
  el("vy-anpassa")?.addEventListener("click", () => {
    const a = utbredningen(r.lager);
    if (a) vy?.anpassa(a);
  });
  const paKryss = el<HTMLInputElement>("bakgrund-pa");
  const url = el<HTMLInputElement>("bakgrund-url");
  const uppdateraBakgrund = (): void => {
    localStorage.setItem(LAGRING_BAKGRUND, paKryss?.checked ? "1" : "0");
    if (url) localStorage.setItem(LAGRING_BAKGRUND_URL, url.value);
    vy?.settBakgrund({ paa: paKryss?.checked ?? false, urlMall: url?.value ?? OSM_MALL });
  };
  paKryss?.addEventListener("change", uppdateraBakgrund);
  url?.addEventListener("change", uppdateraBakgrund);
  uppdateraBakgrund();
}

function utbredningen(lager: LagerPost[]): Utbredning | null {
  if (lager.length === 0) return null;
  return {
    x_min: Math.min(...lager.map((l) => l.utbredning.x_min)),
    y_min: Math.min(...lager.map((l) => l.utbredning.y_min)),
    x_max: Math.max(...lager.map((l) => l.utbredning.x_max)),
    y_max: Math.max(...lager.map((l) => l.utbredning.y_max)),
  };
}

function uppdateraVyLager(): void {
  for (const [id, l] of vyLager) {
    l.synlig = id === t.aktivtLager;
    l.opacitet = t.opacitet;
  }
  vy?.settLager(Array.from(vyLager.values()));
}

const TYP_ETIKETT: Record<LagerPost["typ"], string> = {
  bas: "Bas",
  scenario: "Scenario",
  skillnad: "Skillnad",
};

function ritaLagerPanel(): void {
  const r = t.resultat;
  const panel = el("lager-panel");
  if (!r || !panel) return;
  const analyser = Array.from(new Set(r.lager.map((l) => l.analys)));
  const flikar = analyser
    .map(
      (a) =>
        `<button type="button" class="flik${a === t.aktivAnalys ? " vald" : ""}" data-analys="${esc(a)}" aria-pressed="${a === t.aktivAnalys}">${esc(a)}</button>`,
    )
    .join("");
  const lager = r.lager.filter((l) => l.analys === t.aktivAnalys && (t.fleraLager || l.primar));
  const finnsFler = r.lager.some((l) => l.analys === t.aktivAnalys && !l.primar);
  const rader = lager
    .map(
      (l) => `
      <label class="lager-rad${l.id === t.aktivtLager ? " vald" : ""}">
        <input type="radio" name="lager" value="${esc(l.id)}" ${l.id === t.aktivtLager ? "checked" : ""}/>
        <span class="lager-typ">${TYP_ETIKETT[l.typ]}</span>
        <span class="lager-namn">${esc(l.namn)}</span>
      </label>`,
    )
    .join("");
  const aktivt = r.lager.find((l) => l.id === t.aktivtLager) ?? null;
  panel.innerHTML = `
    <h3 class="etikett-liten">Lager</h3>
    <div class="flikar" role="group" aria-label="Analys">${flikar}</div>
    <div class="lager-lista" role="radiogroup" aria-label="Lager">${rader || '<p class="dampad">Inga rasterlager.</p>'}</div>
    ${finnsFler ? `<label class="kryss-rad"><input type="checkbox" id="fler-lager" ${t.fleraLager ? "checked" : ""}/> Visa fler lager (flaggor, kontroller)</label>` : ""}
    <label class="falt-rad">Opacitet <input type="range" id="opacitet" min="10" max="100" value="${Math.round(t.opacitet * 100)}"/></label>
    ${aktivt ? teckenforklaringHtml(aktivt) : ""}`;
  for (const k of Array.from(panel.querySelectorAll<HTMLButtonElement>("[data-analys]"))) {
    k.addEventListener("click", () => {
      t.aktivAnalys = k.dataset.analys ?? null;
      t.aktivtLager = t.aktivAnalys ? valjStandardLager(r.lager, t.aktivAnalys) : null;
      uppdateraVyLager();
      ritaLagerPanel();
    });
  }
  for (const k of Array.from(panel.querySelectorAll<HTMLInputElement>('input[name="lager"]'))) {
    k.addEventListener("change", () => {
      t.aktivtLager = k.value;
      uppdateraVyLager();
      ritaLagerPanel();
    });
  }
  panel.querySelector<HTMLInputElement>("#fler-lager")?.addEventListener("change", (h) => {
    t.fleraLager = (h.target as HTMLInputElement).checked;
    ritaLagerPanel();
  });
  panel.querySelector<HTMLInputElement>("#opacitet")?.addEventListener("input", (h) => {
    t.opacitet = Number((h.target as HTMLInputElement).value) / 100;
    uppdateraVyLager();
  });
}

function teckenforklaringHtml(l: LagerPost): string {
  const tf = l.teckenforklaring;
  const enhet = l.enhet ? ` ${esc(l.enhet)}` : "";
  let skala: string;
  if (tf.typ === "kontinuerlig") {
    skala = `<div class="skala-rad"><span class="mono">${TAL.format(tf.min)}</span>
      <span class="skala-band" style="background:linear-gradient(to right, ${tf.stopp.join(",")})"></span>
      <span class="mono">${TAL.format(tf.max)}${enhet}</span></div>`;
  } else if (tf.typ === "klasser") {
    skala = `<ul class="klass-lista">${tf.klasser
      .map(
        (k) =>
          `<li><span class="ruta" style="background:${k.farg}"></span><span class="mono">${esc(k.etikett)}${enhet}</span></li>`,
      )
      .join("")}</ul>`;
  } else {
    skala = `<div class="skala-rad"><span class="mono">−${TAL.format(tf.max)}</span>
      <span class="skala-band" style="background:linear-gradient(to right, ${tf.farg_minskning}, #f5f5f5, ${tf.farg_okning})"></span>
      <span class="mono">+${TAL.format(tf.max)}${enhet}</span></div>
      <p class="dampad liten-text">Blå: minskning, röd: ökning (efter − före). Genomskinligt: skillnad ≤ ${TAL.format(tf.troskel)}${enhet}.</p>`;
  }
  const delar = l.typ === "skillnad" ? "" : `<p class="dampad liten-text">Samma skala för bas och scenario.</p>`;
  const nedsamplat = l.nedsampling > 1 ? `<p class="varn-text liten-text">Förhandsvisningen är nedsamplad ${l.nedsampling} gånger.</p>` : "";
  const filer = l.geotiff
    .map((f) => `<li><a href="${esc(f.url)}" download>${esc(f.ruta ?? "hela området")}</a></li>`)
    .join("");
  return `
    <div class="teckenforklaring">
      <h3 class="etikett-liten">${esc(l.namn)}</h3>
      ${skala}${delar}${nedsamplat}
      <details><summary class="liten-text">GeoTIFF (${l.geotiff.length}, full upplösning)</summary><ul class="fil-lista">${filer}</ul></details>
    </div>`;
}

/* ---------- Tabeller ---------- */

function ritaMatvarden(r: ResultatSvar): void {
  const delar = r.tabeller.map(matvardeTabell).join("");
  satt(
    "matvarden",
    `<div class="sektionsrad linje-under-stark"><h3 class="etikett">Mätvärden</h3>
       <span class="dampad">Ett värde per jobb (scenario och ruta), som lösaren lämnat dem.</span></div>${delar || '<p class="dampad tom">Inga mätvärden.</p>'}`,
  );
}

function matvardeTabell(tab: MatvardeTabell): string {
  const rutvisa = tab.rader.some((x) => x.ruta);
  const huvud = tab.kolumner.map((k) => `<th class="hoger">${esc(k)}</th>`).join("");
  const rader = tab.rader
    .map(
      (x) => `<tr><td>${esc(x.scenario)}</td>${rutvisa ? `<td class="mono">${esc(x.ruta ?? "–")}</td>` : ""}
        ${tab.kolumner.map((k) => `<td class="hoger">${esc(formateraVarde(x.varden[k]))}</td>`).join("")}
        <td class="dampad">${x.fran_cache ? "cache" : `körd, ${TAL.format(x.kortid_s)} s`}</td></tr>`,
    )
    .join("");
  return `
    <h4 class="tabell-rubrik"><span class="mono">${esc(tab.analys)}</span> <span class="dampad">${esc(tab.losare)}</span></h4>
    <div class="tabell-ruta rullbar">
      <table class="data-tabell">
        <thead><tr><th>Scenario</th>${rutvisa ? "<th>Ruta</th>" : ""}${huvud}<th>Källa</th></tr></thead>
        <tbody>${rader}</tbody>
      </table>
    </div>`;
}

function ritaJamforelser(r: ResultatSvar): void {
  if (r.jamforelser.length === 0) {
    satt("jamforelser", "");
    return;
  }
  const rader = r.jamforelser
    .map(
      (j) => `<tr><td>${esc(j.analys)} · ${esc(j.utdata)}</td><td>${esc(j.efter)} − ${esc(j.fore)}</td>
        <td class="hoger">${esc(formateraVarde(j.max_okning))}</td><td class="hoger">${esc(formateraVarde(j.max_minskning))}</td>
        <td class="hoger">${esc(formateraVarde(j.yta_okning_m2))}</td><td class="hoger">${esc(formateraVarde(j.yta_minskning_m2))}</td>
        <td class="hoger">${esc(formateraVarde(j.troskel))}</td><td class="hoger">${j.rutor_med_samma_nyckel} av ${j.rutor}</td></tr>`,
    )
    .join("");
  satt(
    "jamforelser",
    `<div class="sektionsrad linje-under-stark"><h3 class="etikett">Före och efter</h3>
       <span class="dampad">Skillnad (efter − före) enligt kärnans jämförelse.</span></div>
     <div class="tabell-ruta"><table class="data-tabell">
       <thead><tr><th>Analys</th><th>Jämförelse</th><th class="hoger">Största ökning</th><th class="hoger">Största minskning</th>
         <th class="hoger">Yta ökning (m²)</th><th class="hoger">Yta minskning (m²)</th><th class="hoger">Tröskel</th><th class="hoger">Rutor oförändrade</th></tr></thead>
       <tbody>${rader}</tbody></table></div>`,
  );
}

function ritaDatatabeller(r: ResultatSvar): void {
  if (r.datatabeller.length === 0) {
    satt("datatabeller", "");
    return;
  }
  const rader = r.datatabeller
    .map(
      (d, i) => `<li><span class="mono">${esc(d.analys)}/${esc(d.scenario)}${d.ruta ? `/${esc(d.ruta)}` : ""}</span>
        <span class="dampad">${esc(d.roll)} (${esc(d.fil)})</span>
        <button type="button" class="textknapp" data-forhandsvisa="${i}">Förhandsgranska</button>
        <a href="${esc(d.url)}" download>Ladda ner</a></li>`,
    )
    .join("");
  const f = t.forhandsvisning;
  const tabell = f
    ? `<p class="dampad liten-text">${esc(f.ref.fil)}: första ${f.rader.length} av ${HELTAL.format(f.antal)} rader.</p>
       <div class="tabell-ruta"><table class="data-tabell"><thead><tr>${f.kolumner.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead>
       <tbody>${f.rader.map((x) => `<tr>${f.kolumner.map((k) => `<td>${esc(formateraVarde(x[k]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : "";
  satt(
    "datatabeller",
    `<div class="sektionsrad linje-under-stark"><h3 class="etikett">Datatabeller</h3>
       <span class="dampad">Filer från lösarna (CSV, Parquet).</span></div>
     <details><summary class="liten-text">${r.datatabeller.length} filer</summary><ul class="tabell-filer">${rader}</ul></details>${tabell}`,
  );
  for (const k of Array.from(rot?.querySelectorAll<HTMLButtonElement>("[data-forhandsvisa]") ?? [])) {
    k.addEventListener("click", () => {
      const ref = r.datatabeller[Number(k.dataset.forhandsvisa)];
      if (!ref) return;
      void hamtaTabellForhandsvisning(ref.forhandsvisning).then((tab) => {
        t.forhandsvisning = { ref, kolumner: tab.kolumner, rader: tab.rader, antal: tab.antal_rader };
        ritaDatatabeller(r);
      });
    });
  }
}
