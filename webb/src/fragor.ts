/*
 * Sidan Frågor: välj en utredningsfråga (K20) -> se planen (rutor, cache) -> kör -> se resultatet
 * (kartvy i SWEREF-koordinater, varningar, mätvärden). Allt beräknas av kärnan; klienten visar.
 * Lokalt körs webben utan inloggning; med `inloggning = "pa"` styr servern åtkomsten (konto.ts).
 */
import "./fragor.css";
import {
  hamtaAvlasning,
  hamtaDemo,
  hamtaFragor,
  hamtaModuler,
  hamtaKorning,
  hamtaKorningar,
  hamtaResultat,
  hamtaTabellForhandsvisning,
  planeraFraga,
  startaKorning,
} from "./api";
import type {
  AvlasningSvar,
  DataTabellRef,
  DemoSvar,
  FragaPost,
  KorningSvar,
  LagerPost,
  MatvardeTabell,
  PlanSvar,
  ResultatSvar,
  Utbredning,
  VarningPost,
} from "./fragor-typer";
import { kontoNotis, vidKontoandring } from "./konto";
import { Kartmotor, type RasterLager, type VyByggnad } from "./karta/kartmotor";
import { giltigKakelmall } from "./karta/wms";
import { lagerTekniskHtml, teckenforklaringHtml } from "./teckenforklaring";
import { lasKartanText } from "./kartforklaring";
import { esc, PROJEKT_ID } from "./sidor";
import type { ModulInfo } from "./types";

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
  visaTak: boolean;
  demo: DemoSvar | null;
  moduler: ModulInfo[];
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
  visaTak: false,
  demo: null,
  moduler: [],
  opacitet: 1,
  forhandsvisning: null,
};

let vy: Kartmotor | null = null;
interface VyLagerPost {
  id: string;
  png: string;
  tak: string | null;
  utbredning: Utbredning;
  synlig: boolean;
  opacitet: number;
  visaTak: boolean;
}
let vyLager = new Map<string, VyLagerPost>();
let bakgrund: string | null = null;
let avlasningSeq = 0;
let avlasningTimer: number | null = null;
let timer: number | null = null;
let rot: HTMLElement | null = null;

/* ---------- Formatering ---------- */

const TAL = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 4 });
const DEC1 = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });
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
          <h1 class="sidrubrik">Utredningsfrågor</h1>
          <p class="dampad fragor-notis" id="fr-notis">${esc(kontoNotis())}</p>
        </div>
        <p class="ingress fragor-ingress">Välj en fråga, se vad som ska beräknas, beräkna och läs resultatet. Exemplen gäller det påhittade demokvarteret.</p>
        <section id="fr-demo" aria-labelledby="fr-demo-rubrik" hidden></section>
        <section id="fr-val" aria-labelledby="fr-val-rubrik"></section>
        <section id="fr-plan" aria-labelledby="fr-plan-rubrik" hidden></section>
        <section id="fr-resultat" aria-labelledby="fr-resultat-rubrik" hidden></section>
      </div>`;
    avslutaVy();
    ritaDemo();
    ritaVal();
    ritaPlan();
    ritaResultat();
  }
  void laddaDemo();
  void laddaFragor().then(() => {
    if (ref && t.vald?.ref !== ref) {
      const post = t.fragor?.find((f) => f.ref === ref);
      if (post) void valj(post, false);
    }
  });
}

vidKontoandring(() => {
  const n = el("fr-notis");
  if (n) n.textContent = kontoNotis();
});

async function laddaDemo(): Promise<void> {
  if (!t.demo) {
    try {
      t.demo = await hamtaDemo();
    } catch {
      t.demo = null; // Demon är valfri: utan den visas bara frågelistan.
    }
  }
  if (t.moduler.length === 0) {
    try {
      t.moduler = (await hamtaModuler()).moduler;
    } catch {
      t.moduler = [];
    }
  }
  ritaDemo();
}

/** Demokorten: vad som finns förkört, och ärligt vad som inte gör det (statusen kommer från /api/moduler). */
function ritaDemo(): void {
  const sek = el("fr-demo");
  if (!sek) return;
  const d = t.demo;
  sek.hidden = !d || !d.finns;
  if (!d || !d.finns || !d.delar) return;
  const kort = (
    del: { ref: string; tid_s: number; jobb: number } | undefined,
    rubrik: string,
    text: string,
  ): string =>
    del
      ? `<article class="demo-kort">
           <h3>${esc(rubrik)}</h3>
           <p>${esc(text)}</p>
           <button type="button" class="knapp knapp-svart" data-demo="${esc(del.ref)}">Visa resultatet</button>
         </article>`
      : "";
  const ovriga = t.moduler
    .filter((m) => m.status !== "finns_i_demon")
    .map(
      (m) => `<article class="demo-kort demo-kommer">
         <h3>${esc(m.namn)}</h3>
         <p>${esc(m.status_text)}</p>
       </article>`,
    )
    .join("");
  sek.innerHTML = `
    <div class="sektionsrad linje-under-stark">
      <h2 class="etikett" id="fr-demo-rubrik">Exempel i demokvarteret</h2>
      <span class="dampad">Ett påhittat kvarter. Inga verkliga data, inga verkliga byggnader.</span>
    </div>
    <div class="demo-rad">
      ${kort(d.delar.demo_dagsljus, "Dagsljus", "Soltimmar på marken före och efter: ett höjt hus och ett nytt hus.")}
      ${kort(d.delar.demo_vatten, "Vatten", "Var vattnet blir stående vid ett skyfall, före och efter ett nytt hus.")}
      ${ovriga}
    </div>`;
  for (const knapp of Array.from(sek.querySelectorAll<HTMLButtonElement>("[data-demo]"))) {
    knapp.addEventListener("click", () => {
      const post = t.fragor?.find((f) => f.ref === knapp.dataset.demo);
      if (post) void valj(post, true);
    });
  }
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
    innehall = `<p class="fel-text" role="alert">Frågorna gick inte att hämta: ${esc(t.fel)}. Är servern igång? Starta den med <code>floden webb</code>.</p>`;
  } else if (!t.fragor) {
    innehall = `<p class="dampad tom">Hämtar frågor …</p>`;
  } else if (t.fragor.length === 0) {
    innehall = `<p class="dampad tom">Det finns inga frågor än.</p>`;
  } else {
    const demo = t.fragor.filter((f) => f.i_demon);
    const egna = t.fragor.filter((f) => !f.i_demon && (f.indata_saknas?.length ?? 0) > 0);
    const kontroll = t.fragor.filter((f) => !f.i_demon && (f.indata_saknas?.length ?? 0) === 0);
    const lista = (poster: FragaPost[]): string => `<ul class="fraga-lista">${poster.map(fragaRad).join("")}</ul>`;
    innehall = `
      ${demo.length > 0 ? lista(demo) : `<p class="dampad tom">Demokvarteret är inte byggt på den här datorn än. Kör <code>floden demo</code> i en terminal och ladda om sidan.</p>`}
      ${
        egna.length > 0
          ? `<details class="fler-exempel"><summary>Fler exempel (kräver egna data)</summary>${lista(egna)}</details>`
          : ""
      }
      ${
        kontroll.length > 0
          ? `<details class="fler-exempel"><summary>Kontrollfrågor (syntetiska fall med känt svar)</summary>${lista(kontroll)}</details>`
          : ""
      }`;
  }
  satt(
    "fr-val",
    `<div class="sektionsrad linje-under-stark">
       <h2 class="etikett" id="fr-val-rubrik">1 · Välj fråga</h2>
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
  const analyser = (f.analyser ?? [])
    .map((a) => `<span class="chip">${esc(a.visningsnamn ?? a.losare)}</span>`)
    .join("");
  const kravEgnaData = (f.indata_saknas?.length ?? 0) > 0;
  const problem = !f.giltig
    ? `<span class="fel-text">Den här frågan kan inte köras: ${esc(f.fel ?? "okänt fel")}</span>`
    : kravEgnaData
      ? `<span class="dampad">Den här frågan kräver egna data och kan inte köras i demon.</span>`
      : "";
  return `
    <li>
      <button type="button" class="fraga-rad${vald ? " vald" : ""}" data-fraga="${esc(f.ref)}" aria-pressed="${vald}" ${f.giltig ? "" : "disabled"}>
        <span class="lista-titel">${esc(f.beskrivning || f.fraga_id)}</span>
        <span class="fraga-meta">${analyser}</span>
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
  await laddaSenasteKorning(post);
}

/** Visar redan körda resultat direkt: senaste klara körning med samma plan (samma jobbnycklar). */
async function laddaSenasteKorning(post: FragaPost): Promise<void> {
  const nyckel = t.plan?.plan_nyckel;
  if (!nyckel) return;
  try {
    const lista = await hamtaKorningar();
    const k = lista.korningar.find(
      (x) => x.ref === post.ref && x.status === "klar" && x.plan_nyckel === nyckel,
    );
    if (!k || t.vald?.ref !== post.ref || t.resultat) return;
    t.korning = k;
    ritaKorStatus();
    await laddaResultat(k.korning_id);
  } catch {
    // Ingen tidigare körning att visa; användaren kan köra frågan.
  }
}

/* ---------- 2. Plan och körning ---------- */

function ritaPlan(): void {
  const sek = el("fr-plan");
  if (!sek) return;
  sek.hidden = t.vald === null;
  if (!t.vald) return;
  const v = t.vald;
  const saknas = (v.indata_saknas?.length ?? 0) > 0;
  const hjalp = saknas
    ? `<div class="hjalp-ruta" role="note">
         <strong>Den här frågan kräver egna data och kan inte köras i demon.</strong>
         <details class="teknisk"><summary>Teknisk information</summary>
           <p>Indata som saknas:</p>
           <ul class="enkel-lista">${(v.indata_saknas ?? []).map((x) => `<li><code>${esc(x)}</code></li>`).join("")}</ul>
           ${v.hjalp ? `<pre>${esc(v.hjalp)}</pre>` : ""}
         </details>
       </div>`
    : "";
  let kropp: string;
  if (t.planFel) {
    kropp = `${hjalp}${saknas ? "" : `<p class="fel-text" role="alert">${esc(t.planFel)}</p>`}`;
  } else if (!t.plan) {
    kropp = `<p class="dampad tom">Planerar …</p>`;
  } else {
    kropp = planInnehall(t.plan, hjalp);
  }
  sek.innerHTML = `
    <div class="sektionsrad linje-under-stark">
      <h2 class="etikett" id="fr-plan-rubrik">2 · Vad som beräknas</h2>
      <span class="dampad">${esc(v.beskrivning)}</span>
    </div>${kropp}`;
  el<HTMLButtonElement>("kor-fraga")?.addEventListener("click", () => void kor());
  ritaKorStatus();
}

function planInnehall(p: PlanSvar, hjalp: string): string {
  const s = p.sammanfattning;
  const analysnamn = (id: string): string =>
    t.vald?.analyser?.find((a) => a.id === id)?.visningsnamn ?? id;
  const rader = Object.entries(p.per_analys)
    .map(([analys, a]) => {
      const j = p.jobb.find((x) => x.analys === analys);
      const radie = j?.radie_m == null ? "global" : `${HELTAL.format(j.radie_m)} m`;
      return `<tr><td>${esc(analysnamn(analys))}</td><td class="mono">${esc(j?.losare ?? "")}@${esc(j?.version ?? "")}</td>
        <td class="hoger">${a.jobb}</td><td class="hoger">${a.i_cache}</td><td class="hoger">${a.att_kora}</td><td class="hoger">${esc(radie)}</td></tr>`;
    })
    .join("");
  const u = p.utbredning;
  const rutor = p.scenarier[0]?.antal_rutor ?? 0;
  const allt = s.att_kora === 0;
  return `
    ${hjalp}
    <div class="plan-rad">
      <div class="plan-tal">
        <div class="tal-kort"><span class="tal stor-tal">${s.jobb}</span><span class="etikett-liten">Beräkningar</span></div>
        <div class="tal-kort"><span class="tal stor-tal">${s.i_cache}</span><span class="etikett-liten">Redan beräknade</span></div>
        <div class="tal-kort"><span class="tal stor-tal">${s.att_kora}</span><span class="etikett-liten">Kvar att beräkna</span></div>
      </div>
      <p class="dampad plan-text">${allt ? "Allt är redan beräknat och sparat; resultatet visas direkt." : "Beräkningen delas upp i delområden. Det som redan är beräknat sparas och används igen."}</p>
    </div>
    <details class="teknisk">
      <summary>Teknisk information</summary>
      <div class="plan-karta">
        ${planKartaSvg(p)}
        <p class="dampad liten-text">${rutor} delområden${u ? ` · E ${HELTAL.format(u.x_min)}–${HELTAL.format(u.x_max)} · N ${HELTAL.format(u.y_min)}–${HELTAL.format(u.y_max)} (SWEREF 99 18 00, EPSG:3011)` : ""}.
          Mörk ruta: allt finns i cachen. Streckad: delvis. Vit: ska beräknas. Ljusgrå med prickad kant: omgivning, ingen beräkning.</p>
      </div>
      <div class="tabell-ruta">
        <table class="data-tabell">
          <thead><tr><th>Beräkning</th><th>Lösare</th><th class="hoger">Antal</th><th class="hoger">I cache</th><th class="hoger">Kvar</th><th class="hoger">Påverkansradie</th></tr></thead>
          <tbody>${rader}</tbody>
        </table>
      </div>
      <p class="dampad liten-text">Fil: <span class="mono">${esc(t.vald?.fil ?? "")}</span></p>
    </details>
    <div class="kor-rad">
      <button type="button" id="kor-fraga" class="knapp knapp-svart">${allt ? "Visa resultatet" : "Beräkna"}</button>
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
  const globalt = p.jobb.some((j) => !j.ruta);
  const rutor = sc.rutor
    .map((r) => {
      const e = per.get(r.ruta_id);
      const klass = !e
        ? globalt
          ? "ingen"
          : "kontext"
        : e.cache === 0
          ? "ingen"
          : e.cache === e.alla
            ? "alla"
            : "delvis";
      const text = e
        ? `${e.cache} av ${e.alla} jobb i cache`
        : globalt
          ? "globalt jobb"
          : "kontext: ingen beräkning i rutan, den finns bara med som omgivning";
      return `<rect class="plan-ruta ${klass}" x="${r.x_min - u.x_min}" y="${u.y_max - r.y_max}" width="${r.x_max - r.x_min}" height="${r.y_max - r.y_min}"><title>${esc(r.ruta_id)}: ${text}</title></rect>`;
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
      <span>${k.status === "ko" ? "I kö" : "Beräknar"}: ${klara} av ${totalt} beräkningar</span>`;
  } else if (k.status === "fel") {
    status.innerHTML = `<span class="fel-text" role="alert">Beräkningen misslyckades: ${esc(k.fel ?? "okänt fel")}</span>`;
  } else {
    status.innerHTML = `<span>Klar: ${totalt} beräkningar.</span>`;
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
  avlasningSeq += 1;
  if (avlasningTimer !== null) {
    window.clearTimeout(avlasningTimer);
    avlasningTimer = null;
  }
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
  const kartlank = t.vald?.i_demon
    ? `<a class="textlank" href="#/karta/${PROJEKT_ID}">Öppna på kartan med före, efter och svep →</a>`
    : "";
  sek.innerHTML = `
    <div class="sektionsrad linje-under-stark">
      <h2 class="etikett" id="fr-resultat-rubrik">3 · Resultat</h2>
      ${kartlank}
    </div>
    ${antagandenHtml(r)}
    ${varningarHtml(r.varningar)}
    <div class="resultat-karta">
      <div class="vy-kolumn">
        <div class="vy-verktyg">
          <span id="markor-rad" class="mono dampad"></span>
          <span class="knapp-par">
            <button type="button" class="knapp knapp-liten" id="vy-in" aria-label="Zooma in">+</button>
            <button type="button" class="knapp knapp-liten" id="vy-ut" aria-label="Zooma ut">−</button>
            <button type="button" class="knapp knapp-liten" id="vy-anpassa">Anpassa</button>
          </span>
        </div>
        <div id="vy-yta" class="vy-yta"></div>
        <div id="avlasning" class="avlasning" role="status"><span class="dampad">Håll markören över kartan för att läsa värden.</span></div>
        <details class="installningar">
          <summary class="etikett-liten">Bakgrundskarta (valfritt)</summary>
          <label class="kryss-rad"><input type="checkbox" id="bakgrund-pa" ${lasBakgrund() ? "checked" : ""}/> Visa kartplattor från adressen nedan (hämtas från den tjänst du anger, inget är förvalt)</label>
          <label class="falt-rad">Adress till kartplattor <input type="text" id="bakgrund-url" class="kontroll" placeholder="https://…/{z}/{x}/{y}.png" value="${esc(localStorage.getItem(LAGRING_BAKGRUND_URL) ?? "")}"/></label>
          <p class="dampad liten-text">Bakgrunden är bara en orientering. Påhittade områden har inga verkliga koordinater, så den stämmer bara för områden som ligger på riktig plats. Ange adressen själv, med {z}, {x} och {y}.</p>
        </details>
      </div>
      <aside class="lager-kolumn" id="lager-panel"></aside>
    </div>
    <div id="tekniskt" class="resultat-block"></div>`;
  startaVy(r);
  ritaLagerPanel();
  ritaTekniskt(r);
}

/** Regnet som användes, ur jobbens manifest. Webben räknar inget: allt här är lösarens egna värden. */
const REGNTYP: Record<string, string> = { block: "blockregn", cds: "CDS-regn" };

function antagandenHtml(r: ResultatSvar): string {
  const a = r.antaganden;
  if (!a) return "";
  const rader: string[] = [];
  for (const g of a.regn) {
    const delar: string[] = [];
    if (g.kalla) {
      delar.push(`${esc(g.rapport ?? g.kalla)}${g.tabell ? ` (${esc(g.tabell)})` : ""}`);
      if (g.aterkomsttid_ar != null) delar.push(`${TAL.format(g.aterkomsttid_ar)} år`);
      if (g.varaktighet_min != null) {
        const typ = g.regntyp ? `, ${esc(REGNTYP[g.regntyp] ?? g.regntyp)}` : "";
        delar.push(`${TAL.format(g.varaktighet_min)} min${typ}`);
      }
    } else {
      delar.push("Regn angivet direkt");
    }
    if (g.klimatfaktor != null) {
      delar.push(`klimatfaktor ${TAL.format(g.klimatfaktor)}${g.klimatfaktor_ingar_i_tabell ? " (ingår i tabellen)" : ""}`);
    }
    if (g.regn_mm != null) delar.push(`${DEC1.format(g.regn_mm)} mm regn totalt`);
    rader.push(`<li><span class="etikett-liten">Regn som användes</span> <span>${delar.join(" · ")}</span></li>`);
  }
  if (rader.length === 0) return "";
  return `<div class="antaganden" aria-label="Antaganden"><ul>${rader.join("")}</ul></div>`;
}

function varningarHtml(v: VarningPost[]): string {
  if (v.length === 0) {
    return `<div class="varning-ruta ingen" role="status"><span class="etikett-liten">Varningar</span> Inga varningar från beräkningarna.</div>`;
  }
  const rader = v
    .map((w) => {
      const var_ = [
        w.antal_jobb > 0 ? `${w.antal_jobb} beräkningar` : "webben",
        w.analyser.join(", "),
        w.scenarier.join(", "),
      ]
        .filter((x) => x)
        .join(" · ");
      const varianter =
        w.varianter.length > 0
          ? `<details><summary class="liten-text">${w.varianter.length} varianter (olika tal)</summary><ul class="varianter">${w.varianter
              .map((x) => `<li>${esc(x.text)} <span class="dampad">(${x.antal_jobb} beräkningar)</span></li>`)
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
  vy = new Kartmotor(yta);
  vy.onMarkor = (e, n) => {
    const rad = el("markor-rad");
    if (rad) {
      rad.textContent = e === null || n === null ? "" : `E ${HELTAL.format(e)} · N ${HELTAL.format(n)}`;
    }
    planeraAvlasning(e, n);
  };
  for (const l of r.lager) {
    vyLager.set(l.id, { id: l.id, png: l.png, tak: l.png_med_tak, utbredning: l.utbredning, synlig: false, opacitet: 1, visaTak: false });
  }
  anpassaTillResultat();
  uppdateraVyLager();
  el("vy-in")?.addEventListener("click", () => vy?.zooma(0.7));
  el("vy-ut")?.addEventListener("click", () => vy?.zooma(1 / 0.7));
  el("vy-anpassa")?.addEventListener("click", () => anpassaTillResultat());
  const paKryss = el<HTMLInputElement>("bakgrund-pa");
  const url = el<HTMLInputElement>("bakgrund-url");
  const uppdateraBakgrund = (): void => {
    localStorage.setItem(LAGRING_BAKGRUND, paKryss?.checked ? "1" : "0");
    if (url) localStorage.setItem(LAGRING_BAKGRUND_URL, url.value);
    bakgrund = paKryss?.checked && url && giltigKakelmall(url.value) ? url.value : null;
    uppdateraVyLager();
  };
  paKryss?.addEventListener("change", uppdateraBakgrund);
  url?.addEventListener("change", uppdateraBakgrund);
  uppdateraBakgrund();
}

/**
 * Vyn passas mot det som faktiskt syns i det aktiva lagret (bounding box för celler med
 * värde, utan maskade). Sker vid start, analysbyte och "Anpassa"; lagerbyte flyttar inte kartan.
 */
function anpassaTillResultat(): void {
  const r = t.resultat;
  if (!r || !vy) return;
  const aktivt = r.lager.find((l) => l.id === t.aktivtLager);
  const u = utbredningen(aktivt ? [aktivt] : r.lager);
  if (u) vy.anpassa(u);
}

function utbredningen(lager: LagerPost[]): Utbredning | null {
  if (lager.length === 0) return null;
  const ut = lager.map((l) => l.synlig_utbredning ?? l.utbredning);
  return {
    x_min: Math.min(...ut.map((u) => u.x_min)),
    y_min: Math.min(...ut.map((u) => u.y_min)),
    x_max: Math.max(...ut.map((u) => u.x_max)),
    y_max: Math.max(...ut.map((u) => u.y_max)),
  };
}

/* ---------- Värden under markören ---------- */

function planeraAvlasning(e: number | null, n: number | null): void {
  if (avlasningTimer !== null) window.clearTimeout(avlasningTimer);
  avlasningTimer = null;
  const r = t.resultat;
  const lager = t.aktivtLager;
  if (e === null || n === null || !r || !lager) {
    avlasningSeq += 1;
    visaAvlasning(null);
    return;
  }
  avlasningTimer = window.setTimeout(() => void lasAvlasning(r.korning.korning_id, lager, e, n), 90);
}

async function lasAvlasning(korning: string, lager: string, e: number, n: number): Promise<void> {
  const nr = ++avlasningSeq;
  try {
    const svar = await hamtaAvlasning(korning, lager, e, n);
    if (nr === avlasningSeq) visaAvlasning(svar);
  } catch {
    if (nr === avlasningSeq) visaAvlasning(null);
  }
}

const DEC2 = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 });

function formateraAvlasning(v: number, enhet: string, typ: string): string {
  const tecken = typ === "skillnad" && v > 0 ? "+" : "";
  return `${tecken}${DEC2.format(v)}${enhet ? ` ${enhet}` : ""}`;
}

function visaAvlasning(a: AvlasningSvar | null): void {
  const ruta = el("avlasning");
  if (!ruta) return;
  if (!a || a.varden.every((x) => !x.inom)) {
    ruta.innerHTML = `<span class="dampad">Håll markören över kartan för att läsa värden.</span>`;
    return;
  }
  const delar = a.varden
    .filter((x) => x.inom)
    .map((x) => {
      let text: string;
      if (x.tak && !t.visaTak) text = "tak – inte mark";
      else if (x.varde === null) text = "inget värde";
      else text = formateraAvlasning(x.varde, x.enhet, x.typ) + (x.tak ? " (tak)" : "");
      return `<span class="avl-post"><span class="etikett-liten">${esc(x.typ === "bas" ? "Före" : `${TYP_ETIKETT[x.typ]} ${x.scenario.replace(/_/g, " ")}`)}</span> <span class="mono">${esc(text)}</span></span>`;
    });
  ruta.innerHTML = delar.join("");
}

function uppdateraVyLager(): void {
  const lista: RasterLager[] = [];
  if (bakgrund) lista.push({ id: "bakgrund", opacitet: 0.9, synlig: true, kalla: { slag: "kakel", mall: bakgrund, kalla: "Kartplattor från adressen du angav" } });
  for (const [id, l] of vyLager) {
    l.synlig = id === t.aktivtLager;
    l.opacitet = t.opacitet;
    lista.push({ id, opacitet: l.opacitet, synlig: l.synlig, kalla: { slag: "bild", url: t.visaTak && l.tak ? l.tak : l.png, utbredning: l.utbredning, skarpa: true } });
  }
  vy?.settLager(lista);
  uppdateraByggnader();
}

/** Byggnadslagret för det aktiva lagret: scenariots byggnader (skillnad: "efter"-scenariot). */
function uppdateraByggnader(): void {
  const r = t.resultat;
  const l = r?.lager.find((x) => x.id === t.aktivtLager);
  if (!r || !l || !vy) return;
  const sid = l.typ === "skillnad" ? (l.efter ?? l.scenario) : l.scenario;
  const poster = r.byggnader?.scenarier[sid] ?? [];
  const byggnader: VyByggnad[] = poster.map((b) => ({ ring: b.ring, hal: b.hal, status: b.status }));
  vy.settByggnader(byggnader);
  const maskat = l.tak_maskat && !t.visaTak;
  vy.settNotis(maskat ? "Ljusgrå yta: tak, inte mark" : null);
}

const TYP_ETIKETT: Record<LagerPost["typ"], string> = {
  bas: "Före",
  scenario: "Efter",
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
        `<button type="button" class="flik${a === t.aktivAnalys ? " vald" : ""}" data-analys="${esc(a)}" aria-pressed="${a === t.aktivAnalys}">${esc(analysnamn(a))}</button>`,
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
    <section class="lasruta" aria-labelledby="las-rubrik">
      <h3 class="etikett-liten" id="las-rubrik">Så läser du kartan</h3>
      <p>${esc(lasKartanText(aktivt, t.vald?.i_demon === true))}</p>
    </section>
    <h3 class="etikett-liten">Lager</h3>
    <div class="flikar" role="group" aria-label="Analys">${flikar}</div>
    <div class="lager-lista" role="radiogroup" aria-label="Lager">${rader || '<p class="dampad">Inga rasterlager.</p>'}</div>
    ${finnsFler ? `<label class="kryss-rad"><input type="checkbox" id="fler-lager" ${t.fleraLager ? "checked" : ""}/> Visa kontrolllager (flaggor)</label>` : ""}
    ${aktivt?.tak_maskat ? `<label class="kryss-rad"><input type="checkbox" id="visa-tak" ${t.visaTak ? "checked" : ""}/> Visa värden på tak (${HELTAL.format(aktivt.tak_yta_m2)} m² byggnadsyta)</label>` : ""}
    <label class="falt-rad">Opacitet <input type="range" id="opacitet" min="10" max="100" value="${Math.round(t.opacitet * 100)}"/></label>
    ${aktivt ? teckenforklaringHtml(aktivt) : ""}
    ${byggnadsforklaring(aktivt)}`;
  panel.querySelector<HTMLInputElement>("#visa-tak")?.addEventListener("change", (h) => {
    t.visaTak = (h.target as HTMLInputElement).checked;
    uppdateraVyLager();
    ritaLagerPanel();
  });
  for (const k of Array.from(panel.querySelectorAll<HTMLButtonElement>("[data-analys]"))) {
    k.addEventListener("click", () => {
      t.aktivAnalys = k.dataset.analys ?? null;
      t.aktivtLager = t.aktivAnalys ? valjStandardLager(r.lager, t.aktivAnalys) : null;
      uppdateraVyLager();
      anpassaTillResultat();
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

function byggnadsforklaring(l: LagerPost | null): string {
  if (!l || !t.resultat?.byggnader) return "";
  const maskat = l.tak_maskat && !t.visaTak;
  return `<div class="teckenforklaring bygg-forklaring">
    <h3 class="etikett-liten">Byggnader</h3>
    <ul class="klass-lista">
      <li><span class="ruta bygg-oforandrad"></span><span class="mono">Byggnad (fotavtryck)</span></li>
      <li><span class="ruta bygg-ny"></span><span class="mono">Ny byggnad</span></li>
      <li><span class="ruta bygg-andrad"></span><span class="mono">Ändrad höjd</span></li>
    </ul>
    ${maskat ? `<p class="dampad liten-text">Grå ytor är tak, inte mark. Där visas inget markvärde.</p>` : ""}
  </div>`;
}

/* ---------- Tabeller ---------- */

function ritaMatvarden(r: ResultatSvar): void {
  const delar = r.tabeller.map(matvardeTabell).join("");
  satt(
    "matvarden",
    `<div class="sektionsrad linje-under-stark"><h4 class="etikett">Mätvärden</h4>
       <span class="dampad">Ett värde per beräkning (scenario och delområde), som lösaren lämnat dem.</span></div>${delar || '<p class="dampad tom">Inga mätvärden.</p>'}`,
  );
}

function analysnamn(id: string): string {
  return t.vald?.analyser?.find((a) => a.id === id)?.visningsnamn ?? id;
}

/** All teknisk information om resultatet, hopfälld: lösare, versioner, parametrar, tabeller, filer. */
function ritaTekniskt(r: ResultatSvar): void {
  const s = r.sammanfattning;
  const analyser = (r.antaganden?.analyser ?? [])
    .map((a) => {
      const par = Object.entries(a.parametrar)
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => `${esc(k)} ${esc(String(v))}`)
        .join(" · ");
      return `<li>${esc(analysnamn(a.analys))}: <span class="mono">${esc(a.losare)}</span>, version ${esc(a.version)}${par ? ` · ${par}` : ""}</li>`;
    })
    .join("");
  const filer = r.lager
    .filter((l) => l.primar)
    .map((l) => lagerTekniskHtml(l))
    .join("");
  satt(
    "tekniskt",
    `<details class="teknisk">
       <summary>Teknisk information</summary>
       <ul class="enkel-lista">
         <li>Fråga <span class="mono">${esc(r.fraga_id)}</span>, körning <span class="mono">${esc(r.korning.korning_id)}</span></li>
         <li>${s.jobb} beräkningar: ${s.i_cache} hämtade ur cachen, ${s.att_kora} körda på ${TAL.format(s.total_tid_s)} s</li>
         <li>Koordinatsystem: SWEREF 99 18 00 (EPSG:3011). Varje beräkning har ett manifest (run.json) i körningens resultatmapp.</li>
         ${analyser}
         ${filer}
       </ul>
       <div id="matvarden" class="resultat-block"></div>
       <div id="jamforelser" class="resultat-block"></div>
       <div id="datatabeller" class="resultat-block"></div>
     </details>`,
  );
  ritaMatvarden(r);
  ritaJamforelser(r);
  ritaDatatabeller(r);
}

function matvardeTabell(tab: MatvardeTabell): string {
  const rutvisa = tab.rader.some((x) => x.ruta);
  const huvud = tab.kolumner.map((k) => `<th class="hoger">${esc(k)}</th>`).join("");
  const rader = tab.rader
    .map(
      (x) => `<tr><td>${esc(x.scenario)}</td>${rutvisa ? `<td class="mono">${esc(x.ruta ?? "–")}</td>` : ""}
        ${tab.kolumner.map((k) => `<td class="hoger">${esc(formateraVarde(x.varden[k]))}</td>`).join("")}
        <td class="dampad">${x.fran_cache ? "cache" : `beräknad, ${TAL.format(x.kortid_s)} s`}</td></tr>`,
    )
    .join("");
  return `
    <h5 class="tabell-rubrik">${esc(analysnamn(tab.analys))} <span class="dampad mono">${esc(tab.losare)}</span></h5>
    <div class="tabell-ruta rullbar">
      <table class="data-tabell">
        <thead><tr><th>Scenario</th>${rutvisa ? "<th>Delområde</th>" : ""}${huvud}<th>Källa</th></tr></thead>
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
    `<div class="sektionsrad linje-under-stark"><h4 class="etikett">Före och efter</h4>
       <span class="dampad">Skillnad (efter minus före) enligt kärnans jämförelse; ytorna räknar alla celler, tak inräknade.</span></div>
     <div class="tabell-ruta"><table class="data-tabell">
       <thead><tr><th>Analys</th><th>Jämförelse</th><th class="hoger">Största ökning</th><th class="hoger">Största minskning</th>
         <th class="hoger">Yta ökning (m²)</th><th class="hoger">Yta minskning (m²)</th><th class="hoger">Tröskel</th><th class="hoger">Oförändrade delområden</th></tr></thead>
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
    `<div class="sektionsrad linje-under-stark"><h4 class="etikett">Datatabeller</h4>
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
