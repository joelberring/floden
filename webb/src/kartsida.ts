/*
 * Kartsidan för projektet (`#/karta/demokvarteret`): en karta där man ser underlag, geometri,
 * planförslag och resultat som lager, ritar ett eget förslag (nya hus, höjdändringar, rivning)
 * och räknar före och efter. Webben räknar inget: det ritade skickas som K9 till servern, som
 * kontrollerar det med kärnans kod och kör det som en K20-fråga. Bilderna ritas av servern ur
 * kärnans rasterfiler och värdena läses ur samma rasterceller.
 */
import "./fragor.css";
import "./kartverktyg.css";
import { hamtaAvlasning, hamtaProjekt, hamtaResultat } from "./api";
import type { AvlasningSvar, LagerPost, ResultatSvar } from "./fragor-typer";
import { LAGEN, lasKartan, type Lage } from "./kartforklaring";
import {
  bytScenarioNamn,
  hamtaBifurkationer,
  hamtaBildMedUtbredning,
  hamtaProjektInfo,
  hamtaScenario,
  hamtaSvfIntervall,
  hamtaVattenIntervall,
  korScenario,
  kontrolleraAndringar,
  lasWmsLager,
  listaScenarier,
  planeraScenario,
  scenarioStatus,
  sparaScenario,
  taBortScenario,
  type BifurkationAnalysSvar,
  type BifurkationsPunktPost,
  type Kontroll,
  type PlanPost,
  type ProjektInfo,
  type ScenarioPost,
  type SvfIntervallSvar,
  type VattenIntervallSvar,
} from "./karta/kartapi";
import { infogaPunkter } from "./karta/formedit";
import { btaUppskattning, yta, type Punkt } from "./karta/geom";
import { Kartmotor, TOM_RITVY, type RasterLager, type RitVy, type VyByggnad } from "./karta/kartmotor";
import { LagerLista } from "./karta/lager";
import { OVERFALL_LAGER_ID, overfallBeskrivning, overfallLager, ritadeOverfall, MAT_I_FALT } from "./karta/overfall";
import { monteraLagerpanel } from "./karta/lagerpanel";
import { PlanDok } from "./karta/plandok";
import { webGpuSkissStatus } from "./gpu/webgpuMotor";
import { Ritverktyg, TANGENTER, type Meddelande, type Verktyg } from "./karta/ritverktyg";
import { giltigKakelmall, lasUnderlag, sparaUnderlag, vardAv, wmsBrickmall, type Underlag, type WmsSvar } from "./karta/wms";
import { felHtml, informationHtml, korVarningarHtml } from "./felvisning";
import { esc, formatera, PROJEKT_ID } from "./sidor";
import { fasadForklaringHtml, skapa3dVy, styrning3d, VY3D_ID, type Kontext3d } from "./kartsida3d";
import { meterPerPxFranZoom, zoomFranMeterPerPx } from "./vy3d/koord";
import { swerefTillWgs84, wgs84TillSweref } from "./sweref";
import { lagerTekniskHtml, skalaHtml } from "./teckenforklaring";
import type { ProjektSvar, ProjektVy } from "./types";

interface Tillstand {
  projekt: ProjektSvar | null;
  info: ProjektInfo | null;
  fel: string | null;
  resultat: Map<string, ResultatSvar>;
  vy: ProjektVy | null;
  /** Vyer ur körningar av ritade förslag. */
  ritVyer: ProjektVy[];
  lage: Lage;
  svep: number;
  visaTak: boolean;
  visaByggnader: boolean;
}

const t: Tillstand = { projekt: null, info: null, fel: null, resultat: new Map(), vy: null, ritVyer: [], lage: "svep", svep: 0.5, visaTak: false, visaByggnader: true };

/** Det som bara finns medan sidan visas. */
interface Sida {
  rot: HTMLElement;
  dok: PlanDok;
  verktyg: Ritverktyg;
  lager: LagerLista;
  a: Kartmotor;
  b: Kartmotor | null;
  meddelande: Meddelande | null;
  kontroll: Kontroll | null;
  kontrollFel: string | null;
  scenario: { slug: string; namn: string } | null;
  scenarier: ScenarioPost[];
  underlag: Underlag[];
  plan: { fragor: PlanPost[]; valda: string[] } | null;
  planFel: string | null;
  /** Avtappning för körningen (mm/h); null = standard 0 mm/h (ledningsnätet antas fullt). */
  avtappning: number | null;
  korning: { slug: string; text: string; klar: boolean; fel: string | null; forklaring?: string | null; varningar?: string[]; startadMs?: number } | null;
  pollTimer: number | null;
  kontrollTimer: number | null;
  ritaBegard: number;
  panelNyckel: string;
  avbryt: Array<() => void>;
  objektUrl: string[];
  wms: WmsSvar | null;
  wmsFel: string | null;
  vy3d: { avmontera(): void; uppdatera?(): void } | null;
  ritaPanel: () => void;
  /** Överfallspunkterna från senaste analysen (lagret `ana:overfall`). */
  overfall: BifurkationsPunktPost[];
}

let s: Sida | null = null;
let senastLage = "";
let avlasningSeq = 0;
let visaKartaSeq = 0;
let avlasningTimer: number | null = null;

const el = <T extends HTMLElement>(id: string): T | null => (s?.rot.querySelector(`#${id}`) as T | null) ?? null;

export function avslutaKarta(): void {
  avsluta();
}

function avsluta(): void {
  visaKartaSeq += 1;
  if (!s) return;
  s.vy3d?.avmontera();
  s.a.avsluta();
  s.b?.avsluta();
  for (const f of s.avbryt) f();
  for (const u of s.objektUrl) URL.revokeObjectURL(u);
  if (s.pollTimer !== null) window.clearInterval(s.pollTimer);
  if (s.kontrollTimer !== null) window.clearTimeout(s.kontrollTimer);
  if (s.ritaBegard) window.cancelAnimationFrame(s.ritaBegard);
  s = null;
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
  avsluta();
  const seq = visaKartaSeq;
  const avbruten = (): boolean => seq !== visaKartaSeq || !container.isConnected;
  container.innerHTML = `<div class="sidbredd innehall karta-sida"><p class="dampad tom">Hämtar kartan …</p></div>`;
  try {
    if (!t.projekt) t.projekt = await hamtaProjekt(PROJEKT_ID);
    if (avbruten()) return;
    t.info = await hamtaProjektInfo(PROJEKT_ID);
    t.fel = null;
  } catch (e) {
    t.fel = e instanceof Error ? e.message : String(e);
  }
  if (avbruten()) return;
  const p = t.projekt;
  const info = t.info;
  if (!p || !info) {
    container.innerHTML = `
      <div class="sidbredd innehall karta-sida">
        <h1 class="sidrubrik stor">Karta</h1>
        <p class="hjalp-ruta" role="note">Det finns inget att visa på kartan än. Demokvarteret byggs med <code>floden demo</code>: kör det i en terminal och ladda om sidan.${t.fel ? ` (${esc(t.fel)})` : ""}</p>
      </div>`;
    return;
  }
  const onskad = vyId ? p.vyer.find((v) => v.id === vyId) : undefined;
  t.vy = onskad ?? t.vy ?? p.vyer[0] ?? null;
  const vy = t.vy;
  if (vy && !t.resultat.has(vy.korning_id)) {
    try {
      const res = await hamtaResultat(vy.korning_id);
      if (avbruten()) return;
      t.resultat.set(vy.korning_id, res);
    } catch {
      t.vy = null;
    }
  }
  if (avbruten()) return;
  byggSida(container, p, info);
}

// ---------------------------------------------------------------------------------------------
// Uppbyggnad

function byggSida(container: HTMLElement, p: ProjektSvar, info: ProjektInfo): void {
  const baseHojd = new Map(info.byggnader.map((b) => [b.id, b.hojd_m]));
  const dok = new PlanDok((id) => baseHojd.get(id));
  container.innerHTML = sidHtml(p);
  const a = new Kartmotor(container.querySelector("#vy-yta") as HTMLElement);
  const lager = new LagerLista(PROJEKT_ID);
  const verktyg = new Ritverktyg(
    dok,
    info.byggnader.map((b) => ({ id: b.id, ring: b.ring.map(([x, y]) => ({ x, y })), hal: b.hal.map((h) => h.map(([x, y]) => ({ x, y }))), hojd_m: b.hojd_m })),
    {
      meterPerPx: () => a.meterPerPx(),
      meddela: (m) => {
        if (s) s.meddelande = m;
        visaMeddelande();
      },
      husSkapat: () => {
        window.requestAnimationFrame(() => {
          const falt = el<HTMLInputElement>("hojd-falt");
          if (falt && !falt.disabled) {
            falt.focus();
            falt.select();
          }
        });
      },
    },
  );
  senastLage = "";
  s = {
    rot: container,
    dok,
    verktyg,
    lager,
    a,
    b: null,
    meddelande: null,
    kontroll: null,
    kontrollFel: null,
    scenario: null,
    scenarier: info.scenarier,
    underlag: lasUnderlag(PROJEKT_ID),
    plan: null,
    planFel: null,
    avtappning: null,
    korning: null,
    pollTimer: null,
    kontrollTimer: null,
    ritaBegard: 0,
    panelNyckel: "",
    avbryt: [],
    objektUrl: [],
    wms: null,
    wmsFel: null,
    vy3d: null,
    ritaPanel: () => undefined,
    overfall: [],
  };
  const sida = s;

  // Lager
  registreraLager(info);
  registrera3dVy(info);
  sida.ritaPanel = monteraLagerpanel(el("lagerpanel") as HTMLElement, lager, {
    andrad: () => {
      synka();
    },
    taBort: (id) => {
      if (!id.startsWith("u:")) return;
      sida.underlag = sida.underlag.filter((u) => `u:${u.id}` !== id);
      sparaUnderlag(PROJEKT_ID, sida.underlag);
      lager.tabort(id);
    },
  });

  // Ritverktyg och karta
  a.settPekare({
    ror: (pt, e) => verktyg.ror(pt, e),
    klick: (pt, e) => verktyg.klick(pt, e),
    dubbelklick: () => verktyg.dubbelklick(),
    ned: (pt, e) => verktyg.ned(pt, e),
    upp: () => verktyg.upp(),
    lamnar: () => verktyg.ror({ x: Number.NaN, y: Number.NaN }, { pekare: "mouse", ctrl: false, shift: false, alt: false }),
  });
  a.onMarkor = planeraAvlasning;
  a.onVyAndrad = () => {
    if (sida.b) sida.b.settVy(a.hamtaVy());
    planeraRitning();
  };
  verktyg.vidAndring(planeraRitning);
  dok.vidAndring(() => {
    sida.plan = null;
    sida.planFel = null;
    planeraKontroll();
    sida.vy3d?.uppdatera?.();
  });
  const tangent = (h: KeyboardEvent): void => {
    const m = h.target as HTMLElement | null;
    if (m && (m.tagName === "INPUT" || m.tagName === "TEXTAREA" || m.tagName === "SELECT" || m.isContentEditable)) return;
    // Ett ritverktygs kortkommando i 3D tar en tillbaka till kartan först.
    if (sida.lager.aktivVy && [TANGENTER.valj, TANGENTER.hus, TANGENTER.laggtill, TANGENTER.tabort].some((k) => k.toLowerCase() === h.key.toLowerCase())) {
      sida.lager.sattAktivVy(null);
    }
    if (verktyg.tangent(h)) h.preventDefault();
  };
  document.addEventListener("keydown", tangent);
  sida.avbryt.push(() => document.removeEventListener("keydown", tangent));

  kopplaKnappar(p, info);
  a.settRutor(Object.values(info.baser).flatMap((b) => b.rutor));
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => anpassa()));
  uppdateraResultatLager();
  synka();
  ritaAllaPaneler();
  void laddaBilder(info);
  for (const u of sida.underlag) registreraUnderlag(u);
  void lasSparadeScenarier();
}

function sidHtml(p: ProjektSvar): string {
  const vyKnappar = ""; // fylls av ritaVyVal
  const lagesKnappar = LAGEN.map(
    ([id, namn]) => `<button type="button" class="flik${id === t.lage ? " vald" : ""}" data-lage="${id}" aria-pressed="${id === t.lage}">${esc(namn)}</button>`,
  ).join("");
  return `
    <div class="sidbredd innehall karta-sida">
      <div class="rubrikrad">
        <div>
          <p class="projekt-etikett"><span class="chip">Påhittat kvarter</span></p>
          <h1 class="sidrubrik">Karta: ${esc(p.namn)}</h1>
        </div>
        <a class="textlank" href="#/projekt/${PROJEKT_ID}">← Till projektet</a>
      </div>

      <details class="prova-sjalv" open aria-labelledby="prova-rubrik">
        <summary class="etikett-liten" id="prova-rubrik">Prova själv</summary>
        <ol class="prova-steg">
          <li>Välj <strong>Rita hus</strong> (W) och klicka husets hörn i kartan. Dubbelklicka eller tryck Enter när formen är klar.</li>
          <li>Ange husets höjd i panelen till höger.</li>
          <li>Tryck <strong>Räkna före och efter</strong>. Du ser först vad som räknas om, sedan resultatet i samma karta.</li>
        </ol>
        <p class="dampad liten-text">Det du ritar sparas som ett lokalt förslag på din dator, inte i projektets filer.</p>
      </details>

      <div class="flikar flikrad" role="group" aria-label="Välj resultat" id="vyval">${vyKnappar}</div>
      <p class="andring-text" id="andring-text" hidden></p>

      <div class="flikar flikrad vy-vaxel" role="group" aria-label="Visa som" id="vy-vaxel">
        <span class="etikett-liten vyval-etikett">Visa som</span>
        <button type="button" class="flik vald" data-visa="2d" aria-pressed="true">Karta</button>
        <button type="button" class="flik" data-visa="3d" aria-pressed="false">3D</button>
      </div>

      <div class="karta-rad">
        <div class="vy-kolumn">
          <div class="verktygsrad" id="verktygsrad" role="toolbar" aria-label="Ritverktyg">
            <button type="button" class="verktyg" data-verktyg="valj" aria-pressed="false" title="Markera hus (${TANGENTER.valj})"><span class="vk">${TANGENTER.valj}</span>Markera</button>
            <button type="button" class="verktyg" data-verktyg="hus" aria-pressed="false" title="Rita nytt hus (${TANGENTER.hus})"><span class="vk">${TANGENTER.hus}</span>Rita hus</button>
            <button type="button" class="verktyg" data-verktyg="laggtill" aria-pressed="false" title="Lägg till hörn (${TANGENTER.laggtill})"><span class="vk">${TANGENTER.laggtill}</span>Lägg till hörn</button>
            <button type="button" class="verktyg" data-verktyg="tabort" aria-pressed="false" title="Ta bort hörn (${TANGENTER.tabort})"><span class="vk">${TANGENTER.tabort}</span>Ta bort hörn</button>
            <span class="verktyg-skil" aria-hidden="true"></span>
            <button type="button" class="verktyg" id="v-fast" aria-pressed="true" title="Fäst mot hörn och kanter (${TANGENTER.fast})"><span class="vk">${TANGENTER.fast}</span>Fäst</button>
            <button type="button" class="verktyg" id="v-rat" aria-pressed="false" title="Rät vinkel (${TANGENTER.ratVinkel}, eller håll Ctrl)"><span class="vk">${TANGENTER.ratVinkel}</span>Rät vinkel</button>
            <span class="verktyg-skil" aria-hidden="true"></span>
            <button type="button" class="verktyg" id="v-angra" title="Ångra (${TANGENTER.angra})"><span class="vk">${TANGENTER.angra}</span>Ångra</button>
            <button type="button" class="verktyg" id="v-gora-om" title="Gör om (${TANGENTER.gora_om})"><span class="vk">⇧Z</span>Gör om</button>
            <span class="knapp-par verktyg-zoom">
              <button type="button" class="knapp knapp-liten" id="vy-in" aria-label="Zooma in">+</button>
              <button type="button" class="knapp knapp-liten" id="vy-ut" aria-label="Zooma ut">−</button>
              <button type="button" class="knapp knapp-liten" id="vy-anpassa">Anpassa</button>
            </span>
          </div>
          <p class="verktygshjalp" id="verktygshjalp" role="status"></p>
          <div class="vy-verktyg" id="lagesrad" hidden>
            <div class="flikar" role="group" aria-label="Visa" id="lagen">${lagesKnappar}</div>
          </div>
          <div class="karta-par" id="karta-par">
            <figure class="karta-ruta">
              <figcaption id="rubrik-a" class="karta-bildtext"></figcaption>
              <div class="vy-stapel"><div id="vy-yta" class="vy-yta"></div><div id="vy-yta-b" class="vy-yta vy-b" hidden></div><div id="vy3d-yta" class="vy-yta vy3d-yta" hidden></div></div>
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
          <section class="panel" aria-labelledby="lp-rubrik">
            <h2 class="etikett-liten" id="lp-rubrik">Lager</h2>
            <div id="lagerpanel" class="lagerpanel"></div>
            <details class="installningar" id="underlag-ruta">
              <summary>Lägg till underlag från en tjänst</summary>
              <div id="underlag-form"></div>
            </details>
          </section>
          <section class="panel" id="hojdpanel" aria-labelledby="hp-rubrik" hidden></section>
          <section class="panel" aria-labelledby="fp-rubrik">
            <h2 class="etikett-liten" id="fp-rubrik">Planförslag</h2>
            <div id="forslag"></div>
          </section>
          <section class="panel" aria-labelledby="rp-rubrik">
            <h2 class="etikett-liten" id="rp-rubrik">Räkna före och efter</h2>
            <div id="rakna"></div>
          </section>
          <section class="panel" aria-labelledby="bp-rubrik">
            <details class="installningar" id="bifurkation-panel">
              <summary id="bp-rubrik">Känsliga överfall &amp; 2D-kontroll</summary>
              <p class="dampad">Identifierar överfallspunkter där höjdosäkerheten kan ändra rinnväg (bör mätas in i fält) och var 2D-beräkning behövs.</p>
              <button type="button" class="knapp knapp-liten" id="kontroll-bifurkation">Analysera överfall</button>
              <div id="bifurkation-svar" role="status"></div>
            </details>
          </section>
          <section class="panel" aria-labelledby="ip-rubrik">
            <details class="installningar" id="intervall-panel">
              <summary id="ip-rubrik">Osäkerhetsintervall (vatten &amp; sol)</summary>
              <p class="dampad">Beräknar monotont garanterade gränser [min, max] för vattendjup (klimatfaktor 1,0–1,25) och himmelsandel SVF (±1,0 m hushöjd).</p>
              <button type="button" class="knapp knapp-liten" id="kontroll-intervall">Beräkna intervall</button>
              <div id="intervall-svar" role="status"></div>
            </details>
          </section>
          <section class="lasruta" aria-labelledby="las-rubrik" id="lasruta" hidden>
            <h2 class="etikett-liten" id="las-rubrik">Så läser du kartan</h2>
            <p id="las-text"></p>
          </section>
          <div id="teckenforklaring"></div>
          <div class="kryss-grupp">
            <label class="kryss-rad" id="tak-rad" hidden><input type="checkbox" id="visa-tak" ${t.visaTak ? "checked" : ""}/> Visa värden även på tak</label>
          </div>
        </aside>
      </div>

      <details class="teknisk" id="teknisk">
        <summary>Teknisk information</summary>
        <ul class="enkel-lista" id="teknisk-lista"></ul>
      </details>
    </div>`;
}

function kopplaKnappar(_p: ProjektSvar, _info: ProjektInfo): void {
  const sida = s as Sida;
  const rot = sida.rot;
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-verktyg]"))) {
    k.addEventListener("click", () => {
      // Ritar man i 3D går man tillbaka till kartan: ritverktygen arbetar i kartans koordinater.
      if (sida.lager.aktivVy) sida.lager.sattAktivVy(null);
      sida.verktyg.sattVerktyg(k.dataset.verktyg as Verktyg);
    });
  }
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-visa]"))) {
    k.addEventListener("click", () => vaxlaVy(k.dataset.visa === "3d"));
  }
  el("v-fast")?.addEventListener("click", () => sida.verktyg.vaxlaFast());
  el("v-rat")?.addEventListener("click", () => sida.verktyg.vaxlaRatVinkel());
  el("v-angra")?.addEventListener("click", () => sida.verktyg.angra());
  el("v-gora-om")?.addEventListener("click", () => sida.verktyg.goraOm());
  el("vy-in")?.addEventListener("click", () => (styrning3d() ? styrning3d()?.zooma(0.7) : sida.a.zooma(0.7)));
  el("vy-ut")?.addEventListener("click", () => (styrning3d() ? styrning3d()?.zooma(1 / 0.7) : sida.a.zooma(1 / 0.7)));
  el("vy-anpassa")?.addEventListener("click", () => (styrning3d() ? styrning3d()?.anpassa() : anpassa()));
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-lage]"))) {
    k.addEventListener("click", () => {
      t.lage = k.dataset.lage as Lage;
      for (const x of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-lage]"))) {
        const pa = x.dataset.lage === t.lage;
        x.classList.toggle("vald", pa);
        x.setAttribute("aria-pressed", String(pa));
      }
      tillampaLage();
      synka();
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => sida.a.matOm()));
    });
  }
  el<HTMLInputElement>("svep-reglage")?.addEventListener("input", (h) => {
    t.svep = Number((h.target as HTMLInputElement).value) / 100;
    stallSvep();
  });
  el<HTMLInputElement>("visa-tak")?.addEventListener("change", (h) => {
    t.visaTak = (h.target as HTMLInputElement).checked;
    uppdateraResultatLager();
    synka();
  });
  el<HTMLButtonElement>("kontroll-bifurkation")?.addEventListener("click", () => {
    void korBifurkationsKontroll();
  });
  el<HTMLButtonElement>("kontroll-intervall")?.addEventListener("click", () => {
    void korIntervallKontroll();
  });
}

function formateraIntervallHtml(
  vatten: VattenIntervallSvar | null,
  vattenFel: string | null,
  svf: SvfIntervallSvar | null,
  svfFel: string | null,
): string {
  const delar: string[] = [];
  if (vatten) {
    const sam = vatten.sammanfattning;
    delar.push(
      `<p><strong>Skyfall (K40):</strong> största djup ${sam.storsta_min_djup_m.toFixed(2)}–${sam.storsta_max_djup_m.toFixed(2)} m ` +
        `(${vatten.antal_korningar} körningar; ${sam.antal_monoton_garanterad}/${sam.antal_noder} monotont garanterade).</p>`,
    );
  } else if (vattenFel) {
    delar.push(`<p class="dampad">Skyfall: ${esc(vattenFel)}</p>`);
  }
  if (svf) {
    delar.push(
      `<p><strong>Dagsljus SVF (K41, ±${svf.osakerhet_byggnad_m.toFixed(1)} m):</strong> medel ${svf.svf_min_medel.toFixed(3)}–${svf.svf_max_medel.toFixed(3)} ` +
        `(största bredd ${svf.bredd_max.toFixed(3)}).</p>`,
    );
  } else if (svfFel) {
    delar.push(`<p class="dampad">Dagsljus: ${esc(svfFel)}</p>`);
  }
  return delar.join("");
}

async function korIntervallKontroll(): Promise<void> {
  const sida = s;
  const ut = el("intervall-svar");
  const knapp = el<HTMLButtonElement>("kontroll-intervall");
  if (!sida || !ut) return;
  if (knapp) knapp.disabled = true;
  ut.innerHTML = `<p class="dampad">Beräknar monotona intervall för vatten och sol …</p>`;
  try {
    const [vRes, sRes] = await Promise.allSettled([
      hamtaVattenIntervall(PROJEKT_ID),
      hamtaSvfIntervall(PROJEKT_ID),
    ]);
    if (s !== sida) return;
    const vatten = vRes.status === "fulfilled" ? vRes.value : null;
    const vattenFel =
      vRes.status === "rejected"
        ? vRes.reason instanceof Error
          ? vRes.reason.message
          : String(vRes.reason)
        : null;
    const svf = sRes.status === "fulfilled" ? sRes.value : null;
    const svfFel =
      sRes.status === "rejected"
        ? sRes.reason instanceof Error
          ? sRes.reason.message
          : String(sRes.reason)
        : null;
    ut.innerHTML = formateraIntervallHtml(vatten, vattenFel, svf, svfFel);
  } finally {
    if (s === sida && knapp) knapp.disabled = false;
  }
}

function formateraBifurkationHtml(svar: BifurkationAnalysSvar): string {
  const andelPct = (svar.felindikator.andel_2d_yta * 100).toFixed(1);
  const f = svar.felindikator;
  const instabila = svar.punkter.filter((p) => p.instabil).slice(0, 5);
  const lista =
    instabila.length === 0
      ? `<p class="dampad">Inga instabila överfallspunkter vid k = ${svar.k_sigma.toFixed(1)}σ.</p>`
      : `<ul class="enkel-lista">${instabila
          .map(
            (p) =>
              `<li>${esc(overfallBeskrivning(p))} — <strong>${MAT_I_FALT}</strong></li>`,
          )
          .join("")}</ul>`;
  return `<p><strong>2D-behov:</strong> ${andelPct} % av ytan (${f.antal_noder_2d} av ${f.antal_noder_totalt} sänkor). <strong>Instabila överfall:</strong> ${svar.antal_instabila} st.</p>${lista}`;
}

async function korBifurkationsKontroll(): Promise<void> {
  const sida = s;
  const ut = el("bifurkation-svar");
  const knapp = el<HTMLButtonElement>("kontroll-bifurkation");
  if (!sida || !ut) return;
  if (knapp) knapp.disabled = true;
  ut.innerHTML = `<p class="dampad">Analyserar överfall och 2D-behov …</p>`;
  try {
    const svar = await hamtaBifurkationer(PROJEKT_ID);
    if (s !== sida) return;
    ut.innerHTML = formateraBifurkationHtml(svar);
    visaOverfallILager(sida, svar);
  } catch (e) {
    if (s !== sida) return;
    const msg = e instanceof Error ? e.message : String(e);
    ut.innerHTML = `<p class="dampad">${esc(msg)}</p>`;
  } finally {
    if (s === sida && knapp) knapp.disabled = false;
  }
}

/** Överfallspunkterna som eget lager i 2D-kartan (kan tändas och släckas i lagerpanelen). */
function visaOverfallILager(sida: Sida, svar: BifurkationAnalysSvar): void {
  const punkter = ritadeOverfall(svar.punkter);
  sida.overfall = punkter;
  sida.lager.registrera(overfallLager(punkter.length));
  sida.ritaPanel();
  synka();
}

// ---------------------------------------------------------------------------------------------
// Lager

function registreraLager(info: ProjektInfo): void {
  const sida = s as Sida;
  const l = sida.lager;
  l.registrera({
    id: "underlag:orto",
    grupp: "underlag",
    namn: "Ortofoto",
    synlig: info.ortofoto.finns,
    opacitet: 1,
    hjalp: info.ortofoto.finns ? info.ortofoto.kalla : undefined,
    ejTillgangligt: info.ortofoto.finns ? undefined : "Inget ortofoto finns lokalt. Det hämtas med floden data lantmateriet (se README).",
    visning: "2d",
  } as never);
  l.registrera({ id: "geo:skuggning", grupp: "geometri", namn: "Terräng (skuggning)", synlig: false, opacitet: 0.9, hjalp: "Skuggad terrängmodell ur projektets höjddata.", visning: "2d" });
  l.registrera({ id: "geo:vegetation", grupp: "geometri", namn: "Vegetation", synlig: false, opacitet: 0.9, hjalp: "Växtlighetens höjd ur projektets data.", visning: "2d" });
  l.registrera({ id: "geo:byggnader", grupp: "geometri", namn: "Byggnader", synlig: true, opacitet: 1, hjalp: "Byggnaderna: fotavtryck i kartan, volymer i 3D." });
  l.registrera({ id: "plan:aktuellt", grupp: "planforslag", namn: "Aktuellt förslag", synlig: true, opacitet: 1, hjalp: "Det du ritar just nu." });
}

function registreraUnderlag(u: Underlag): void {
  (s as Sida).lager.registrera({
    id: `u:${u.id}`,
    grupp: "underlag",
    namn: u.namn,
    synlig: true,
    opacitet: 1,
    hjalp: u.kalla,
    kanTasBort: true,
    data: { underlag: u },
  });
}

async function laddaBilder(info: ProjektInfo): Promise<void> {
  const sida = s as Sida;
  const hamta = async (id: string, url: string, namn: string): Promise<void> => {
    try {
      const b = await hamtaBildMedUtbredning(url);
      if (s !== sida) {
        URL.revokeObjectURL(b.url);
        return;
      }
      sida.objektUrl.push(b.url);
      const g = sida.lager.hamta(id);
      if (g) g.data = { url: b.url, utbredning: b.utbredning };
      synka();
    } catch (e) {
      if (s !== sida) return;
      const g = sida.lager.hamta(id);
      sida.lager.registrera({ ...(g ?? { id, grupp: "geometri", namn, synlig: false, opacitet: 1 }), ejTillgangligt: `Kunde inte hämtas: ${e instanceof Error ? e.message : String(e)}` });
    }
  };
  const jobb = [hamta("geo:skuggning", `/api/kartverktyg/${PROJEKT_ID}/geometri/skuggning.png`, "Terräng (skuggning)"), hamta("geo:vegetation", `/api/kartverktyg/${PROJEKT_ID}/geometri/vegetation.png`, "Vegetation")];
  if (info.ortofoto.finns) jobb.push(hamta("underlag:orto", info.ortofoto.png, "Ortofoto"));
  await Promise.all(jobb);
}

/** Lagren för det aktiva resultatet (före, efter, skillnad) i gruppen Resultat. */
function uppdateraResultatLager(): void {
  const sida = s;
  if (!sida) return;
  sida.lager.taBortMed("resultat", "res:");
  const vy = t.vy;
  const r = vy ? t.resultat.get(vy.korning_id) : undefined;
  if (!vy || !r) return;
  const rader: Array<[string, string, LagerPost | null]> = [
    ["skillnad", `Skillnad · ${vy.mattnamn}`, lagerPost(r, vy.skillnad_lager)],
    ["efter", `Efter: ${vy.efter_namn.toLowerCase()} · ${vy.mattnamn}`, lagerPost(r, vy.efter_lager)],
    ["fore", `Före · ${vy.mattnamn}`, lagerPost(r, vy.fore_lager)],
    ["slutet", "Slutna områden: vattnet har ingen väg ut", vy.markering_lager ? lagerPost(r, vy.markering_lager) : null],
  ];
  for (const [id, namn, lp] of rader) {
    if (!lp) continue;
    sida.lager.registrera({
      id: `res:${id}`,
      grupp: "resultat",
      namn,
      // Slutna områden märks alltid (de styrs inte av läget före/efter/skillnad).
      synlig: id === "slutet",
      opacitet: 0.85,
      data: { url: t.visaTak && lp.png_med_tak ? lp.png_med_tak : lp.png, utbredning: lp.utbredning, skarpa: true },
    });
  }
  tillampaLage();
}

/** Läget (före, efter, skillnad, svep, sida vid sida) styr vilka resultatlager som är tända. */
function tillampaLage(): void {
  const sida = s;
  if (!sida) return;
  const tand: Record<string, string[]> = { fore: ["fore"], efter: ["efter"], skillnad: ["skillnad"], svep: ["fore"], sidavid: ["fore"] };
  const pa = tand[t.lage] ?? [];
  for (const id of ["fore", "efter", "skillnad"]) {
    if (sida.lager.hamta(`res:${id}`)) sida.lager.sattSynlig(`res:${id}`, pa.includes(id));
  }
}

// ---------------------------------------------------------------------------------------------
// Kartan: lager till motorn

function rasterAv(l: { id: string; synlig: boolean; opacitet: number; data?: Record<string, unknown> }, tvinga?: boolean): RasterLager | null {
  const d = l.data;
  if (!d) return null;
  if (d.underlag) {
    const u = d.underlag as Underlag;
    const mall = u.typ === "wms" ? wmsBrickmall(u) : u.url;
    return { id: l.id, opacitet: l.opacitet, synlig: tvinga ?? l.synlig, kalla: { slag: "kakel", mall, kalla: u.kalla } };
  }
  if (typeof d.url === "string" && d.utbredning) {
    return { id: l.id, opacitet: l.opacitet, synlig: tvinga ?? l.synlig, kalla: { slag: "bild", url: d.url, utbredning: d.utbredning as never, skarpa: Boolean(d.skarpa) } };
  }
  return null;
}

function rasterLista(sida: Sida, motor: "a" | "b"): RasterLager[] {
  const ut: RasterLager[] = [];
  for (const g of ["underlag", "geometri", "resultat"] as const) {
    // Panelen visar översta lagret först; kartan staplas nerifrån.
    for (const l of [...sida.lager.iGrupp(g)].reverse()) {
      const r = rasterAv(l, g === "resultat" ? resultatSynligt(l.id, motor, l.synlig) : undefined);
      if (r) ut.push(r);
    }
  }
  return ut;
}

/** Är något beräkningsresultat (före, efter, skillnad) tänt? Överfallslagret räknas inte. */
function harSynligtResultat(sida: Sida): boolean {
  return sida.lager.iGrupp("resultat").some((l) => l.synlig && l.id.startsWith("res:"));
}

function resultatSynligt(id: string, motor: "a" | "b", panel: boolean): boolean {
  const delat = t.lage === "svep" || t.lage === "sidavid";
  if (!delat) return panel;
  // Slutna områden hör till efter-läget: i svep och sida vid sida visas de på efter-sidan.
  if (id === "res:slutet") return panel && motor === "b";
  if (motor === "a") return id === "res:fore";
  return id === "res:efter";
}

/** Skickar lager, byggnader och ritlager till kartan. */
function synka(): void {
  const sida = s;
  if (!sida) return;
  if (hanteraVyval(sida)) return;
  const delat = (t.lage === "svep" || t.lage === "sidavid") && sida.lager.hamta("res:fore") !== undefined;
  sida.a.settLager(rasterLista(sida, "a"));
  if (delat) {
    ensureB();
    sida.b?.settLager(rasterLista(sida, "b"));
  } else if (sida.b) {
    sida.b.avsluta();
    sida.b = null;
    const ruta = el("vy-yta-b");
    if (ruta) ruta.hidden = true;
  }
  const byggnaderLager = sida.lager.hamta("geo:byggnader");
  const byggStil = { synlig: Boolean(byggnaderLager?.synlig), opacitet: byggnaderLager?.opacitet ?? 1 };
  const medVarden = harSynligtResultat(sida) && t.visaTak;
  for (const m of [sida.a, sida.b]) {
    if (!m) continue;
    m.settByggnadStil({ ...byggStil, opacitet: medVarden ? byggStil.opacitet * 0.25 : byggStil.opacitet });
    m.settByggnader(byggnaderAtt());
    m.settNotis(notis());
  }
  const overfallLagret = sida.lager.hamta(OVERFALL_LAGER_ID);
  for (const m of [sida.a, sida.b]) {
    if (!m) continue;
    m.settOverfall(sida.overfall);
    m.settOverfallStil({ synlig: Boolean(overfallLagret?.synlig), opacitet: overfallLagret?.opacitet ?? 1 });
  }
  const plan = sida.lager.hamta("plan:aktuellt");
  sida.a.settPlanStil({ synlig: Boolean(plan?.synlig), opacitet: plan?.opacitet ?? 1 });
  uppdateraSidopaneler();
  planeraRitning();
  stallLage();
}

// ---------------------------------------------------------------------------------------------
// 3D-vyn som lager (egen vy) i samma panel

function kontext3d(): Kontext3d {
  const sida = s as Sida;
  const info = t.info as ProjektInfo;
  return {
    projekt: PROJEKT_ID,
    lager: sida.lager,
    info,
    dok: sida.dok,
    vy: () => t.vy,
    resultat: () => (t.vy ? t.resultat.get(t.vy.korning_id) : undefined),
    lage: () => t.lage,
    svep: () => t.svep,
    arRitatResultat: () => Boolean(t.vy && t.ritVyer.includes(t.vy)),
    kartvy: () => {
      const v = sida.a.hamtaVy();
      const [e, n] = wgs84TillSweref(v.lng, v.lat);
      const h = (el("vy-yta") as HTMLElement).clientHeight || (el("vy3d-yta") as HTMLElement).clientHeight || 600;
      return { e, n, mpp: meterPerPxFranZoom(v.zoom, v.lat), hojdPx: h };
    },
    settKartvy: (e, n, mpp) => {
      const [lng, lat] = swerefTillWgs84(e, n);
      sida.a.settVy({ lng, lat, zoom: zoomFranMeterPerPx(mpp, lat) });
    },
    avlasning: (e, n) => planeraAvlasning(e, n),
    text: (text) => visaAvlasningText(text),
    fel: (text) => {
      sida.meddelande = { text: `3D-vyn: ${text}`, fel: true } as never;
      visaMeddelande();
    },
  };
}

/** Lagret "3D-vy" i Geometri: en egen vy som ersätter kartan. */
function registrera3dVy(info: ProjektInfo): void {
  void info;
  const sida = s as Sida;
  sida.lager.registrera({
    id: VY3D_ID,
    grupp: "geometri",
    namn: "3D-vy",
    synlig: true,
    opacitet: 1,
    hjalp: "Kvarteret i 3D: terräng, byggnader, ditt förslag och resultaten. Lagren nedan styr båda vyerna.",
    egenVy: skapa3dVy(kontext3d()),
  });
  // Öppna alltid i kartan, också om 3D var aktiv sist.
  sida.lager.sattAktivVy(null);
  sida.lager.sattSynlig(VY3D_ID, true);
}

function vaxlaVy(till3d: boolean): void {
  const sida = s;
  if (!sida) return;
  sida.lager.sattSynlig(VY3D_ID, true);
  sida.lager.sattAktivVy(till3d ? VY3D_ID : null);
}

/**
 * Byter mellan kartan och 3D när lagerpanelen eller flikarna sagt det. Ger true när 3D är aktiv
 * (då ritas inte kartan om: den ligger kvar dold och följer med när man går tillbaka).
 */
function hanteraVyval(sida: Sida): boolean {
  const lage3d = sida.lager.aktivVy;
  const vill3d = lage3d !== null;
  const rot = sida.rot;
  const kartYta = el("vy-yta") as HTMLElement;
  const yta3d = el("vy3d-yta") as HTMLElement;
  if (vill3d && !sida.vy3d) {
    // Samma vy i båda: kartans läge läses innan kartan döljs.
    sida.b?.avsluta();
    sida.b = null;
    (el("vy-yta-b") as HTMLElement).hidden = true;
    yta3d.hidden = false;
    yta3d.style.height = `${kartYta.clientHeight}px`;
    kartYta.style.display = "none";
    sida.lager.sattVisning("3d");
    sida.vy3d = lage3d.egenVy?.montera(yta3d) ?? null;
    sida.ritaPanel();
    rot.querySelector(".karta-sida")?.classList.add("vy3d-aktiv");
    (rot.querySelector(".karta-sida") ?? rot).classList.add("vy3d-aktiv");
  } else if (!vill3d && sida.vy3d) {
    sida.vy3d.avmontera();
    sida.vy3d = null;
    yta3d.hidden = true;
    yta3d.style.height = "";
    kartYta.style.display = "";
    sida.lager.sattVisning("2d");
    sida.ritaPanel();
    (rot.querySelector(".karta-sida") ?? rot).classList.remove("vy3d-aktiv");
    senastLage = "";
    window.requestAnimationFrame(() => {
      sida.a.matOm();
      synka();
    });
  }
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-visa]"))) {
    const pa = (k.dataset.visa === "3d") === vill3d;
    k.classList.toggle("vald", pa);
    k.setAttribute("aria-pressed", String(pa));
  }
  const hjalp = el("verktygshjalp");
  if (vill3d && hjalp) hjalp.textContent = "Du tittar i 3D. Välj ett ritverktyg (eller tryck W) för att gå tillbaka till kartan och rita; förslaget syns här direkt.";
  if (!vill3d) return false;
  const svepRad = el("svep-rad");
  if (svepRad) svepRad.hidden = t.lage !== "svep";
  sida.vy3d?.uppdatera?.();
  uppdateraSidopaneler();
  return true;
}

function ensureB(): void {
  const sida = s as Sida;
  if (sida.b) return;
  const yta = el("vy-yta-b") as HTMLElement;
  yta.hidden = false;
  sida.b = new Kartmotor(yta, sida.a.hamtaVy());
  sida.b.onVyAndrad = () => sida.b && sida.a.settVy(sida.b.hamtaVy());
  sida.b.onMarkor = planeraAvlasning;
  stallLage();
}

function stallLage(): void {
  const sida = s;
  if (!sida) return;
  const delat = Boolean(sida.b);
  const par = el("karta-par");
  par?.classList.remove("tva");
  const svepRad = el("svep-rad");
  if (svepRad) svepRad.hidden = !(t.lage === "svep" && (delat || Boolean(sida.vy3d)));
  const b = el("vy-yta-b");
  const stapel = b?.parentElement;
  stapel?.classList.toggle("svep", delat && t.lage === "svep");
  stapel?.classList.toggle("sida", delat && t.lage === "sidavid");
  stallSvep();
  sida.vy3d?.uppdatera?.();
  const nyckel = `${t.lage}|${delat}`;
  if (nyckel === senastLage) return;
  senastLage = nyckel;
  // Kartytorna ändrar storlek mellan lägena: mät om och passa in kartan en gång per lägesbyte.
  window.requestAnimationFrame(() =>
    window.requestAnimationFrame(() => {
      sida.a.matOm();
      sida.b?.matOm();
      anpassa();
    }),
  );
}

function stallSvep(): void {
  const b = el("vy-yta-b");
  if (b) {
    if (t.lage === "svep") b.style.clipPath = `inset(0 0 0 ${Math.round(t.svep * 1000) / 10}%)`;
    else b.style.clipPath = "";
  }
  s?.vy3d?.uppdatera?.();
}

function notis(): string | null {
  const vy = t.vy;
  const r = vy ? t.resultat.get(vy.korning_id) : undefined;
  const fore = r ? lagerPost(r, vy?.fore_lager ?? null) : null;
  return fore?.tak_maskat && !t.visaTak && harSynligtResultat(s as Sida) ? "Ljusgrå yta: tak, inte mark" : null;
}

/** Byggnaderna som ritas: projektets befintliga med ändringarna i förslaget (ändrad höjd, riven). */
function byggnaderAtt(): VyByggnad[] {
  const sida = s as Sida;
  const info = t.info as ProjektInfo;
  const vy = t.vy;
  const r = vy ? t.resultat.get(vy.korning_id) : undefined;
  const ritasResultat = r && vy && (t.lage === "fore" || t.lage === "efter" || t.lage === "skillnad");
  if (ritasResultat && r.byggnader) {
    const lager = lagerPost(r, t.lage === "fore" ? vy.fore_lager : t.lage === "efter" ? vy.efter_lager : vy.skillnad_lager);
    if (lager) {
      const sid = lager.typ === "skillnad" ? (lager.efter ?? lager.scenario) : lager.scenario;
      return (r.byggnader.scenarier[sid] ?? []).map((b) => ({ ring: b.ring, hal: b.hal, status: b.status }));
    }
  }
  return info.byggnader.map((b) => ({
    ring: b.ring,
    hal: b.hal,
    status: sida.dok.bort.has(b.id) ? "bort" : sida.dok.hojd.has(b.id) ? "andrad" : "oforandrad",
  }));
}

// ---------------------------------------------------------------------------------------------
// Ritning

function planeraRitning(): void {
  const sida = s;
  if (!sida || sida.ritaBegard) return;
  sida.ritaBegard = window.requestAnimationFrame(() => {
    sida.ritaBegard = 0;
    uppdateraRit();
  });
}

function uppdateraRit(): void {
  const sida = s;
  if (!sida) return;
  const v = sida.verktyg;
  const info = t.info as ProjektInfo;
  const hus: RitVy["hus"] = [];
  for (const h of sida.dok.ny.values()) {
    const ring = v.dragRing?.id === h.id ? v.dragRing.ring : h.ring;
    hus.push({ id: h.id, ring, vald: Boolean(v.val?.ny && v.val.id === h.id), slag: "ny" });
  }
  if (v.val && !v.val.ny) {
    const b = info.byggnader.find((x) => x.id === v.val?.id);
    if (b) hus.push({ id: b.id, ring: b.ring.map(([x, y]) => ({ x, y })), vald: true, slag: sida.dok.bort.has(b.id) ? "bort" : "andrad" });
  }
  let horn: Punkt[] = [];
  let mitt: Punkt[] = [];
  if (v.val?.ny && v.verktyg !== "hus") {
    const ring = v.ringAv(v.val);
    if (ring) {
      horn = ring;
      if (v.verktyg === "laggtill" || v.verktyg === "valj") mitt = infogaPunkter(ring).map((m) => m.at);
    }
  }
  const rit: RitVy = {
    ...TOM_RITVY,
    hus,
    utkast: v.utkast,
    mark: v.verktyg === "hus" ? v.mark : null,
    fast: v.fast ? { at: v.fast.at, slag: fastText(v.fast.typ) } : null,
    lasLinje: v.lasLinje,
    horn,
    mitt,
    etikett: v.ritText(),
  };
  sida.a.settRit(rit);
  sida.a.settMarkorstil(v.verktyg === "valj" ? (v.drar ? "grabbing" : "") : "crosshair");
  uppdateraVerktygsrad();
  // Paneler byggs om först när något som visas i dem ändrats (inte vid varje musrörelse).
  const nyckel = `${v.val?.id ?? ""}|${v.val?.ny ?? ""}|${sida.dok.andringsNr}|${v.verktyg}|${sida.kontroll ? "k" : ""}`;
  if (nyckel !== sida.panelNyckel) {
    sida.panelNyckel = nyckel;
    ritaHojdpanel();
    ritaForslag();
    ritaRakna();
  }
}

function fastText(slag: string): string {
  const m: Record<string, string> = { hornpunkt: "hörn", skarning: "skärning", kant: "kant" };
  return m[slag] ?? "fäst";
}

function uppdateraVerktygsrad(): void {
  const sida = s;
  if (!sida) return;
  const v = sida.verktyg;
  for (const k of Array.from(sida.rot.querySelectorAll<HTMLButtonElement>("[data-verktyg]"))) {
    const pa = k.dataset.verktyg === v.verktyg;
    k.classList.toggle("vald", pa);
    k.setAttribute("aria-pressed", String(pa));
  }
  const satt = (id: string, pa: boolean): void => {
    const k = el(id);
    k?.classList.toggle("vald", pa);
    k?.setAttribute("aria-pressed", String(pa));
  };
  satt("v-fast", v.fastPa);
  satt("v-rat", v.ratVinkel);
  (el("v-angra") as HTMLButtonElement).disabled = !sida.dok.kanAngra();
  (el("v-gora-om") as HTMLButtonElement).disabled = !sida.dok.kanGoraOm();
  const hjalp: Record<Verktyg, string> = {
    valj: "Klicka på ett hus för att markera det. Dra i ett markerat nytt hus för att flytta det, eller i ett hörn för att flytta hörnet.",
    hus: v.utkast.length === 0 ? "Klicka husets första hörn i kartan." : v.utkast.length < 3 ? "Klicka nästa hörn. Backsteg tar bort senaste, Esc avbryter." : "Klicka fler hörn. Dubbelklicka, tryck Enter eller klicka på första hörnet för att avsluta.",
    laggtill: "Klicka på en kant i ett markerat nytt hus för att lägga till ett hörn.",
    tabort: "Klicka på ett hörn i ett markerat nytt hus för att ta bort det.",
  };
  visaMeddelande(hjalp[v.verktyg]);
}

function visaMeddelande(hjalp?: string): void {
  const sida = s;
  const rad = el("verktygshjalp");
  if (!sida || !rad) return;
  if (hjalp !== undefined) rad.dataset.hjalp = hjalp;
  const m = sida.meddelande;
  rad.classList.toggle("fel", m?.typ === "fel");
  rad.textContent = m ? m.text : (rad.dataset.hjalp ?? "");
}

// ---------------------------------------------------------------------------------------------
// Kontroll hos servern

function planeraKontroll(): void {
  const sida = s;
  if (!sida) return;
  if (sida.kontrollTimer !== null) window.clearTimeout(sida.kontrollTimer);
  sida.kontrollTimer = window.setTimeout(() => void kontrollera(), 250);
  sida.panelNyckel = "";
  planeraRitning();
}

async function kontrollera(): Promise<void> {
  const sida = s;
  if (!sida) return;
  const andringar = sida.dok.tillAndringar();
  if (andringar.length === 0) {
    sida.kontroll = null;
    sida.kontrollFel = null;
    sida.panelNyckel = "";
    planeraRitning();
    return;
  }
  try {
    const k = await kontrolleraAndringar(PROJEKT_ID, andringar);
    if (s !== sida) return;
    sida.kontroll = k;
    sida.kontrollFel = null;
  } catch (e) {
    if (s !== sida) return;
    sida.kontroll = null;
    sida.kontrollFel = e instanceof Error ? e.message : String(e);
  }
  sida.panelNyckel = "";
  planeraRitning();
}

function kontrollHtml(): string {
  const sida = s as Sida;
  const k = sida.kontroll;
  if (sida.kontrollFel) return `<p class="fel-text" role="alert">${esc(sida.kontrollFel)}</p>`;
  if (!k) return "";
  const fel = k.fel.map((f) => `<p class="fel-text" role="alert">${esc(f)}</p>`).join("");
  const varn = k.varningar.map((f) => `<p class="varnings-text" role="note">${esc(f)}</p>`).join("");
  return fel + varn + informationHtml(k.information);
}

// ---------------------------------------------------------------------------------------------
// Paneler

const TAL0 = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 });
const TAL1 = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

function ritaHojdpanel(): void {
  const sida = s as Sida;
  const panel = el("hojdpanel") as HTMLElement;
  const v = sida.verktyg;
  const val = v.val;
  if (!val) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  const info = t.info as ProjektInfo;
  const hojd = v.hojdAv(val);
  const max = info.hojd_max_m;
  const hojdVarde = hojd === null ? "" : String(Math.round(hojd * 100) / 100);
  if (val.ny) {
    const ring = v.ringAv(val);
    const area = ring ? yta(ring) : 0;
    const bta = hojd !== null && Number.isFinite(hojd) ? btaUppskattning(area, hojd, info.vaningshojd_m) : 0;
    const k = sida.kontroll?.hus.find((x) => x.andring_id === val.id);
    panel.innerHTML = `
      <h2 class="etikett-liten" id="hp-rubrik">Nytt hus</h2>
      <div class="falt-rad"><label for="hojd-falt">Höjd (m)</label>
        <input class="kontroll" id="hojd-falt" type="number" inputmode="decimal" step="0.5" value="${esc(hojdVarde)}" aria-describedby="hojd-hjalp"/></div>
      <p class="dampad liten-text" id="hojd-hjalp">Större än 0 och högst ${TAL0.format(max)} m.</p>
      <dl class="matt-lista">
        <div><dt>Yta</dt><dd>${TAL0.format(k?.yta_m2 ?? area)} m²</dd></div>
        <div><dt>Våningar</dt><dd>${TAL0.format(k?.vaningar ?? Math.max(1, Math.round((hojd ?? 0) / info.vaningshojd_m)))}</dd></div>
        <div><dt>Bruttoarea (uppskattning)</dt><dd>${TAL0.format(k?.bta_m2 ?? bta)} m²</dd></div>
      </dl>
      <p class="dampad liten-text">Uppskattningen räknar ${TAL1.format(info.vaningshojd_m)} m per våning och är ingen beräkning av lösarna.</p>
      <div class="knapp-rad"><button type="button" class="knapp knapp-liten" id="hus-bort">Ta bort huset</button></div>
      ${kontrollHtml()}`;
  } else {
    const grund = info.byggnader.find((b) => b.id === val.id);
    const riven = sida.dok.bort.has(val.id);
    panel.innerHTML = `
      <h2 class="etikett-liten" id="hp-rubrik">Befintlig byggnad</h2>
      <p class="mono liten-text">${esc(val.id)}</p>
      <div class="falt-rad"><label for="hojd-falt">Höjd (m)</label>
        <input class="kontroll" id="hojd-falt" type="number" inputmode="decimal" step="0.5" value="${esc(hojdVarde)}" ${riven ? "disabled" : ""} aria-describedby="hojd-hjalp"/></div>
      <p class="dampad liten-text" id="hojd-hjalp">Nu ${grund ? TAL1.format(grund.hojd_m) : "?"} m. Större än 0 och högst ${TAL0.format(max)} m.</p>
      <div class="knapp-rad"><button type="button" class="knapp knapp-liten" id="hus-bort">${riven ? "Ångra rivning" : "Riv byggnaden"}</button></div>
      ${kontrollHtml()}`;
  }
  const falt = panel.querySelector<HTMLInputElement>("#hojd-falt");
  falt?.addEventListener("change", () => {
    const n = Number(falt.value.replace(",", "."));
    if (falt.value.trim() === "" || !Number.isFinite(n)) {
      sida.meddelande = { typ: "fel", text: "Ange höjden som ett tal i meter." };
      visaMeddelande();
      return;
    }
    sida.meddelande = null;
    sida.verktyg.sattHojd(n);
  });
  panel.querySelector("#hus-bort")?.addEventListener("click", () => sida.verktyg.taBortVal());
}

function ritaForslag(): void {
  const sida = s as Sida;
  const rot = el("forslag") as HTMLElement;
  const antal = sida.dok.tillAndringar().length;
  const sc = sida.scenario;
  const lista = sida.scenarier
    .map(
      (x) => `
      <li class="scen-rad${sc?.slug === x.slug ? " aktivt" : ""}">
        <span class="scen-namn">${esc(x.namn)} <span class="dampad liten-text">${x.antal_andringar} ändring${x.antal_andringar === 1 ? "" : "ar"}</span></span>
        <span class="kl-knappar">
          <button type="button" class="knapp knapp-liten" data-oppna="${esc(x.slug)}">Öppna</button>
          <button type="button" class="knapp knapp-liten" data-bytnamn="${esc(x.slug)}">Byt namn</button>
          <button type="button" class="knapp knapp-liten" data-tabort="${esc(x.slug)}" aria-label="Ta bort ${esc(x.namn)}">Ta bort</button>
        </span>
      </li>`,
    )
    .join("");
  rot.innerHTML = `
    <p class="dampad liten-text">${antal === 0 ? "Inget ritat än." : `${antal} ändring${antal === 1 ? "" : "ar"} i förslaget.`}</p>
    <div class="falt-rad"><label for="scen-namn">Namn</label><input class="kontroll" id="scen-namn" type="text" maxlength="80" value="${esc(sc?.namn ?? "")}" placeholder="Mitt förslag"/></div>
    <div class="knapp-rad">
      <button type="button" class="knapp knapp-liten" id="scen-spara" ${antal === 0 ? "disabled" : ""}>Spara förslaget</button>
      <button type="button" class="knapp knapp-liten" id="scen-nytt">Nytt tomt förslag</button>
    </div>
    <p class="fel-text" id="scen-fel" role="alert" hidden></p>
    ${lista ? `<ul class="scen-lista">${lista}</ul>` : `<p class="dampad liten-text">Inga sparade förslag. De sparas som filer på din dator, inte i projektets filer.</p>`}`;
  const fel = (text: string | null): void => {
    const p = el("scen-fel");
    if (p) {
      p.textContent = text ?? "";
      p.hidden = !text;
    }
  };
  rot.querySelector("#scen-spara")?.addEventListener("click", () => void spara().catch((e: unknown) => fel(e instanceof Error ? e.message : String(e))));
  rot.querySelector("#scen-nytt")?.addEventListener("click", () => {
    sida.dok.rensa();
    sida.scenario = null;
    sida.verktyg.markera(null);
    sida.korning = null;
    ritaForslag();
    ritaRakna();
  });
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-oppna]"))) {
    k.addEventListener("click", () => void oppna(k.dataset.oppna as string).catch((e: unknown) => fel(e instanceof Error ? e.message : String(e))));
  }
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-bytnamn]"))) {
    k.addEventListener("click", () => {
      const slug = k.dataset.bytnamn as string;
      const gammalt = sida.scenarier.find((x) => x.slug === slug)?.namn ?? "";
      const nytt = window.prompt("Nytt namn på förslaget", gammalt);
      if (!nytt || nytt.trim() === gammalt) return;
      void bytScenarioNamn(PROJEKT_ID, slug, nytt.trim()).then(
        async (d) => {
          if (sida.scenario?.slug === slug) sida.scenario = { slug: d.slug ?? slug, namn: d.namn };
          await lasSparadeScenarier();
        },
        (e: unknown) => fel(e instanceof Error ? e.message : String(e)),
      );
    });
  }
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-tabort]"))) {
    k.addEventListener("click", () => {
      const slug = k.dataset.tabort as string;
      const namn = sida.scenarier.find((x) => x.slug === slug)?.namn ?? slug;
      if (!window.confirm(`Ta bort förslaget ${namn}? Filen på din dator raderas.`)) return;
      void taBortScenario(PROJEKT_ID, slug).then(
        async () => {
          if (sida.scenario?.slug === slug) sida.scenario = null;
          await lasSparadeScenarier();
        },
        (e: unknown) => fel(e instanceof Error ? e.message : String(e)),
      );
    });
  }
}

async function lasSparadeScenarier(): Promise<void> {
  const sida = s;
  if (!sida) return;
  try {
    const l = await listaScenarier(PROJEKT_ID);
    if (s !== sida) return;
    sida.scenarier = l.scenarier;
  } catch {
    /* Listan finns kvar som den var. */
  }
  sida.panelNyckel = "";
  ritaForslag();
  ritaRakna();
}

async function spara(): Promise<{ slug: string; namn: string }> {
  const sida = s as Sida;
  const falt = el<HTMLInputElement>("scen-namn");
  const namn = (falt?.value.trim() || sida.scenario?.namn || "Mitt förslag").trim();
  const d = await sparaScenario(PROJEKT_ID, namn, sida.dok.tillAndringar(), sida.scenario && sida.scenario.namn === namn ? sida.scenario.slug : undefined);
  sida.scenario = { slug: d.slug, namn: d.namn };
  await lasSparadeScenarier();
  return sida.scenario;
}

async function oppna(slug: string): Promise<void> {
  const sida = s as Sida;
  const d = await hamtaScenario(PROJEKT_ID, slug);
  sida.dok.fran(d.andringar);
  sida.scenario = { slug: d.slug, namn: d.namn };
  sida.verktyg.markera(null);
  sida.plan = null;
  sida.korning = null;
  await lasSparadeScenarier();
  planeraKontroll();
}

function valdaAnalyser(): string[] {
  const sida = s as Sida;
  return Array.from(sida.rot.querySelectorAll<HTMLInputElement>("[data-analys]"))
    .filter((k) => k.checked)
    .map((k) => k.dataset.analys as string);
}

function ritaRakna(): void {
  const sida = s as Sida;
  const rot = el("rakna") as HTMLElement;
  const info = t.info as ProjektInfo;
  const antal = sida.dok.tillAndringar().length;
  const giltig = antal > 0 && Boolean(sida.kontroll?.giltig) && !sida.kontrollFel;
  const tidigare = new Set(Array.from(rot.querySelectorAll<HTMLInputElement>("[data-analys]")).filter((k) => k.checked).map((k) => k.dataset.analys));
  const forstaGangen = rot.querySelector("[data-analys]") === null;
  const analyser = info.analyser
    .map((a) => `<label class="kryss-rad"><input type="checkbox" data-analys="${esc(a.id)}" ${(forstaGangen ? a.forval : tidigare.has(a.id)) ? "checked" : ""}/> ${esc(a.namn)} <span class="dampad liten-text">(${esc(a.mall)})</span></label>`)
    .join("");
  let planHtml = "";
  if (sida.planFel) planHtml = `<p class="fel-text" role="alert">${esc(sida.planFel)}</p>`;
  else if (sida.plan) planHtml = planUtseende(sida.plan.fragor);
  let korHtml = "";
  const k = sida.korning;
  const pagar = Boolean(k && !k.klar);
  rot.setAttribute("aria-busy", String(pagar));
  if (k?.forklaring) korHtml = felHtml(k.fel, k.forklaring);
  else if (k) korHtml = `<p class="${k.fel ? "fel-text" : ""}" role="${k.fel ? "alert" : "status"}">${esc(k.fel ?? k.text)}</p>${korVarningarHtml(k.varningar)}`;
  const gpuStatus = webGpuSkissStatus();
  rot.innerHTML = `
    <p class="dampad liten-text">Räknar om det du ritat och jämför med läget före, med samma beräkningar som resten av Floden.</p>
    <p class="dampad liten-text">${esc(gpuStatus.beskrivning)}</p>
    <div class="kryss-grupp">${analyser}</div>
    <label class="falt-rad">Avtappning till ledningsnätet (mm/h, valfritt) <input type="number" min="0" step="any" id="rakna-avtappning" class="kontroll" placeholder="0 – ledningsnätet antas fullt" value="${sida.avtappning === null ? "" : esc(String(sida.avtappning))}"/></label>
    <div class="knapp-rad"><button type="button" class="knapp" id="rakna-plan" ${giltig && !pagar ? "" : "disabled"}>Räkna före och efter</button></div>
    ${antal === 0 ? `<p class="dampad liten-text">Rita först ett hus eller ändra en byggnad.</p>` : !giltig && sida.kontroll ? `<p class="dampad liten-text">Rätta felen ovan först.</p>` : ""}
    ${planHtml}${korHtml}`;
  rot.querySelector<HTMLInputElement>("#rakna-avtappning")?.addEventListener("change", (e) => {
    const v = (e.target as HTMLInputElement).value.trim();
    sida.avtappning = v === "" ? null : Number(v);
  });
  rot.querySelector("#rakna-plan")?.addEventListener("click", () => void visaPlan());
  rot.querySelector("#rakna-starta")?.addEventListener("click", () => void startaKorning());
  rot.querySelector("#rakna-avbryt")?.addEventListener("click", () => {
    sida.plan = null;
    ritaRakna();
  });
}

function planUtseende(fragor: PlanPost[]): string {
  const rader = fragor
    .map((f) => {
      const nya = f.plan.jobb.filter((j) => !j.i_cache).length;
      const ater = f.plan.jobb.length - nya;
      const radier = Object.entries(f.paverkansradie_m)
        .filter(([, r]) => r !== null)
        .map(([a, r]) => `${a}: ${TAL0.format(r as number)} m`)
        .join(", ");
      const tid = f.uppskattad_tid_s === null ? (f.tid_text ?? "Tiden går inte att uppskatta.") : `ungefär ${TAL0.format(f.uppskattad_tid_s)} s, uppskattat efter tidigare körningar`;
      return `
        <li>
          <strong>${esc(f.mall)}</strong>
          <ul class="enkel-lista">
            <li>${nya} beräkning${nya === 1 ? "" : "ar"} körs, ${ater} återanvänds från tidigare körningar.</li>
            <li>Påverkansradie: ${radier ? esc(radier) : "ingen angiven"}.</li>
            <li>Tid: ${esc(tid)}.</li>
          </ul>
        </li>`;
    })
    .join("");
  return `
    <div class="planruta" role="group" aria-label="Plan för beräkningen">
      <h3 class="etikett-liten">Det här räknas om</h3>
      <ul class="enkel-lista">${rader}</ul>
      <details class="teknisk"><summary>Teknisk information</summary><ul class="enkel-lista">${fragor.map((f) => `<li>K20-fråga: <span class="mono">${esc(f.ref)}</span>, K9: <span class="mono">${esc(f.k9)}</span> (nyckel <span class="mono">${esc(f.k9_nyckel.slice(0, 12))}</span>)</li>`).join("")}</ul></details>
      <div class="knapp-rad"><button type="button" class="knapp" id="rakna-starta">Starta beräkningen</button><button type="button" class="knapp knapp-liten" id="rakna-avbryt">Avbryt</button></div>
    </div>`;
}

async function visaPlan(): Promise<void> {
  const sida = s as Sida;
  sida.planFel = null;
  const valda = valdaAnalyser();
  try {
    if (valda.length === 0) throw new Error("Välj minst en analys att räkna.");
    if (sida.avtappning !== null && !(Number.isFinite(sida.avtappning) && sida.avtappning >= 0)) {
      throw new Error("Avtappningen måste vara ett tal ≥ 0 mm/h (eller tomt för 0).");
    }
    const sc = await spara();
    const p = await planeraScenario(PROJEKT_ID, sc.slug, valda, sida.avtappning);
    if (s !== sida) return;
    sida.plan = { fragor: p.fragor, valda };
  } catch (e) {
    sida.plan = null;
    sida.planFel = e instanceof Error ? e.message : String(e);
  }
  ritaRakna();
}

async function startaKorning(): Promise<void> {
  const sida = s as Sida;
  const plan = sida.plan;
  const sc = sida.scenario;
  if (!plan || !sc) return;
  sida.plan = null;
  const startadMs = Date.now();
  sida.korning = { slug: sc.slug, text: "Beräknar … (0 s)", klar: false, fel: null, startadMs };
  ritaRakna();
  try {
    await korScenario(PROJEKT_ID, sc.slug, plan.valda, sida.avtappning);
  } catch (e) {
    sida.korning = { slug: sc.slug, text: "", klar: true, fel: e instanceof Error ? e.message : String(e) };
    ritaRakna();
    return;
  }
  if (sida.pollTimer !== null) window.clearInterval(sida.pollTimer);
  sida.pollTimer = window.setInterval(() => void kolla(sida, sc.slug), 1200);
  void kolla(sida, sc.slug);
}

async function kolla(sida: Sida, slug: string): Promise<void> {
  if (s !== sida) return;
  try {
    const st = await scenarioStatus(PROJEKT_ID, slug);
    if (s !== sida) return;
    const alla = st.korningar;
    const fel = alla.find((k) => k.status.status === "fel");
    const klara = alla.filter((k) => k.status.status === "klar");
    const startadMs = sida.korning?.startadMs ?? Date.now();
    const forflutetS = Math.max(0, Math.round((Date.now() - startadMs) / 1000));
    if (fel) {
      stoppaPoll(sida);
      sida.korning = { slug, text: "", klar: true, fel: fel.status.fel_forklaring ? String(fel.status.fel) : `Beräkningen misslyckades: ${String(fel.status.fel ?? "okänt fel")}`, forklaring: typeof fel.status.fel_forklaring === "string" ? fel.status.fel_forklaring : null };
    } else if (alla.length > 0 && klara.length === alla.length) {
      stoppaPoll(sida);
      const varningar = [...new Set(klara.flatMap((k) => k.varningar ?? []))];
      sida.korning = { slug, text: `Klart på ${forflutetS} s. Resultatet ligger i gruppen Resultat i kartan.`, klar: true, fel: null, varningar };
      await visaResultatFor(klara.flatMap((k) => k.vyer));
    } else {
      const fr = alla.map((k) => k.status.framsteg as { klara: number; totalt: number } | undefined).filter(Boolean) as Array<{ klara: number; totalt: number }>;
      const k = fr.reduce((x, y) => x + y.klara, 0);
      const tot = fr.reduce((x, y) => x + y.totalt, 0);
      sida.korning = { slug, text: tot > 0 ? `Beräknar … ${k} av ${tot} delar klara (${forflutetS} s).` : `Beräknar … (${forflutetS} s)`, klar: false, fel: null, startadMs };
    }
  } catch (e) {
    stoppaPoll(sida);
    sida.korning = { slug, text: "", klar: true, fel: e instanceof Error ? e.message : String(e) };
  }
  ritaRakna();
}

function stoppaPoll(sida: Sida): void {
  if (sida.pollTimer !== null) window.clearInterval(sida.pollTimer);
  sida.pollTimer = null;
}

async function visaResultatFor(vyer: ProjektVy[]): Promise<void> {
  if (vyer.length === 0) return;
  t.ritVyer = vyer;
  for (const v of vyer) {
    if (!t.resultat.has(v.korning_id)) t.resultat.set(v.korning_id, await hamtaResultat(v.korning_id));
  }
  t.vy = vyer[0] as ProjektVy;
  t.lage = "svep";
  ritaVyVal();
  uppdateraResultatLager();
  for (const x of Array.from((s as Sida).rot.querySelectorAll<HTMLButtonElement>("[data-lage]"))) {
    const pa = x.dataset.lage === t.lage;
    x.classList.toggle("vald", pa);
    x.setAttribute("aria-pressed", String(pa));
  }
  synka();
}

// ---------------------------------------------------------------------------------------------
// Resultatval och sidopaneler

function ritaVyVal(): void {
  const sida = s as Sida;
  const p = t.projekt as ProjektSvar;
  const rot = el("vyval") as HTMLElement;
  const alla = [...p.vyer, ...t.ritVyer];
  const lagesrad = el("lagesrad") as HTMLElement;
  lagesrad.hidden = !t.vy;
  rot.innerHTML = alla.length === 0 ? "" : `<span class="etikett-liten vyval-etikett">Resultat</span>${alla
    .map((v) => `<button type="button" class="flik${v.id === t.vy?.id ? " vald" : ""}" data-vy="${esc(v.id)}" aria-pressed="${v.id === t.vy?.id}">${esc(v.rubrik)}</button>`)
    .join("")}`;
  for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-vy]"))) {
    k.addEventListener("click", () => {
      const ny = alla.find((v) => v.id === k.dataset.vy);
      if (!ny || ny === t.vy) return;
      void (async () => {
        if (!t.resultat.has(ny.korning_id)) t.resultat.set(ny.korning_id, await hamtaResultat(ny.korning_id));
        if (s !== sida) return;
        t.vy = ny;
        history.replaceState(null, "", `#/karta/${PROJEKT_ID}?vy=${encodeURIComponent(ny.id)}`);
        uppdateraResultatLager();
        synka();
      })();
    });
  }
  const text = el("andring-text");
  if (text) {
    text.hidden = !t.vy;
    text.innerHTML = t.vy ? `<span class="etikett-liten">Förändring</span> ${esc(t.vy.andring)}` : "";
  }
}

function ritaAllaPaneler(): void {
  ritaVyVal();
  ritaHojdpanel();
  ritaForslag();
  ritaRakna();
  ritaUnderlagForm();
  uppdateraSidopaneler();
}

function uppdateraSidopaneler(): void {
  const vy = t.vy;
  const r = vy ? t.resultat.get(vy.korning_id) : undefined;
  const sida = s as Sida;
  const harResultat = Boolean(vy && r);
  const rubrikA = el("rubrik-a");
  if (rubrikA) {
    rubrikA.hidden = !harResultat;
    if (vy && harResultat) {
      const m: Record<Lage, string> = {
        fore: `Före · ${vy.mattnamn}`,
        efter: `Efter: ${vy.efter_namn.toLowerCase()} · ${vy.mattnamn}`,
        skillnad: `Skillnad (efter minus före) · ${vy.mattnamn}`,
        svep: `Före (vänster) och efter: ${vy.efter_namn.toLowerCase()} (höger) · ${vy.mattnamn}`,
        sidavid: `Före (vänster) och efter: ${vy.efter_namn.toLowerCase()} (höger) · ${vy.mattnamn}`,
      };
      rubrikA.textContent = m[t.lage];
    }
  }
  const las = el("lasruta");
  if (las) las.hidden = !harResultat;
  const lasText = el("las-text");
  if (lasText && vy) lasText.textContent = lasKartan(vy, t.lage);
  const fore = r && vy ? lagerPost(r, vy.fore_lager) : null;
  const skillnad = r && vy ? lagerPost(r, vy.skillnad_lager) : null;
  const takRad = el("tak-rad");
  if (takRad) takRad.hidden = !(fore?.tak_maskat || skillnad?.tak_maskat);
  const tf = el("teckenforklaring");
  if (tf) {
    const visad = t.lage === "skillnad" ? skillnad : (fore ?? skillnad);
    const rubrik = t.lage === "skillnad" ? "Skillnad" : `${vy?.mattnamn ?? ""}${visad?.enhet ? ` (${visad.enhet})` : ""}`;
    tf.innerHTML = visad && vy && harResultat ? `<div class="teckenforklaring"><h3 class="etikett-liten">${esc(rubrik)}</h3>${skalaHtml(visad)}${t.lage === "skillnad" ? "" : `<p class="dampad liten-text">Samma skala före och efter.</p>`}</div>` : "";
    const fasadTand = sida.lager.aktivVy && sida.lager.hamta("3d:fasad")?.synlig && !sida.lager.hamta("3d:fasad")?.ejTillgangligt;
    if (fasadTand) tf.innerHTML += fasadForklaringHtml(t.lage, t.lage === "skillnad");
  }
  const lista = el("teknisk-lista");
  if (lista) {
    const efter = r && vy ? lagerPost(r, vy.efter_lager) : null;
    const lagerTeknik = [fore, efter, skillnad].filter((l): l is LagerPost => l !== null).map(lagerTekniskHtml).join("");
    lista.innerHTML = `
      ${vy && r ? `<li>Körning: <span class="mono">${esc(vy.korning_id)}</span>, ${r.sammanfattning.jobb} beräkningar</li>` : ""}
      <li>Koordinatsystem: SWEREF 99 18 00 (EPSG:3011). Kartan är MapLibre GL; varje punkt räknas om exakt mellan SWEREF och WGS 84 (Gauss-Krüger, kontrollerat mot pyproj). <span id="markor-rad" class="mono">Markörens läge visas här.</span></li>
      <li>Det du ritar skickas som ändringar av typen byggnad_ny, byggnad_hojd och byggnad_bort (K9) till servern, som kontrollerar dem med kärnans kod och kör dem som en K20-fråga i den lokala frågemappen.</li>
      ${sida.scenario ? `<li>Aktuellt förslag: <span class="mono">${esc(sida.scenario.slug)}</span></li>` : ""}
      ${lagerTeknik}`;
  }
}

function ritaUnderlagForm(): void {
  const sida = s as Sida;
  const rot = el("underlag-form") as HTMLElement;
  const anv = sida.wms?.anvandbara ?? [];
  rot.innerHTML = `
    <p class="dampad liten-text">Ange adressen till en WMS-tjänst (till exempel en kommuns eller Lantmäteriets) eller en kakelmall med {z}, {x} och {y}. Inget är förvalt. Tjänsten anropas via servern.</p>
    <div class="falt-rad"><label for="wms-url">Adress</label><input class="kontroll" id="wms-url" type="url" placeholder="https://…" value="${esc(el<HTMLInputElement>("wms-url")?.value ?? "")}"/></div>
    <div class="knapp-rad"><button type="button" class="knapp knapp-liten" id="wms-lasa">Läs lager</button><button type="button" class="knapp knapp-liten" id="kakel-lagg">Lägg till som kakelmall</button></div>
    ${sida.wmsFel ? `<p class="fel-text" role="alert">${esc(sida.wmsFel)}</p>` : ""}
    ${anv.length > 0 ? `<div class="falt-rad"><label for="wms-lager">Lager</label><select class="kontroll" id="wms-lager">${anv.map((l) => `<option value="${esc(l.namn)}">${esc(l.titel || l.namn)}</option>`).join("")}</select></div>
    <div class="knapp-rad"><button type="button" class="knapp knapp-liten" id="wms-lagg">Lägg till lagret</button></div>` : ""}`;
  const url = (): string => (el<HTMLInputElement>("wms-url")?.value ?? "").trim();
  rot.querySelector("#wms-lasa")?.addEventListener("click", () => {
    sida.wmsFel = null;
    void lasWmsLager(url()).then(
      (svar) => {
        sida.wms = svar;
        if (svar.anvandbara.length === 0) sida.wmsFel = "Tjänsten har inga lager som kan visas i kartan (den måste kunna leverera EPSG:3857 eller EPSG:4326).";
        ritaUnderlagForm();
      },
      (e: unknown) => {
        sida.wms = null;
        sida.wmsFel = e instanceof Error ? e.message : String(e);
        ritaUnderlagForm();
      },
    );
  });
  rot.querySelector("#wms-lagg")?.addEventListener("click", () => {
    const val = el<HTMLSelectElement>("wms-lager")?.value;
    const w = sida.wms;
    const l = w?.anvandbara.find((x) => x.namn === val);
    if (!w || !l || !l.stod) return;
    const u: Underlag = { id: `wms-${Date.now().toString(36)}`, typ: "wms", namn: l.titel || l.namn, url: url(), lager: l.namn, stod: l.stod, version: w.version, kalla: `WMS från ${vardAv(url())}` };
    sida.underlag.push(u);
    sparaUnderlag(PROJEKT_ID, sida.underlag);
    registreraUnderlag(u);
  });
  rot.querySelector("#kakel-lagg")?.addEventListener("click", () => {
    const mall = url();
    if (!giltigKakelmall(mall)) {
      sida.wmsFel = "En kakelmall måste börja med http eller https och innehålla {z}, {x} och {y}.";
      ritaUnderlagForm();
      return;
    }
    sida.wmsFel = null;
    const u: Underlag = { id: `kakel-${Date.now().toString(36)}`, typ: "kakel", namn: `Kakel från ${vardAv(mall) || "tjänst"}`, url: mall, kalla: `Kakel från ${vardAv(mall)}` };
    sida.underlag.push(u);
    sparaUnderlag(PROJEKT_ID, sida.underlag);
    registreraUnderlag(u);
    ritaUnderlagForm();
  });
}

// ---------------------------------------------------------------------------------------------
// Anpassa

function anpassa(): void {
  const sida = s;
  if (!sida) return;
  const info = t.info as ProjektInfo;
  const vy = t.vy;
  const r = vy ? t.resultat.get(vy.korning_id) : undefined;
  const bas = r && vy ? (lagerPost(r, vy.fore_lager) ?? lagerPost(r, vy.skillnad_lager)) : null;
  sida.a.anpassa(bas?.utbredning ?? info.utbredning);
  sida.b?.settVy(sida.a.hamtaVy());
}

// ---------------------------------------------------------------------------------------------
// Värden under markören

function planeraAvlasning(e: number | null, n: number | null): void {
  if (avlasningTimer !== null) window.clearTimeout(avlasningTimer);
  avlasningTimer = null;
  const vy = t.vy;
  const rad = el("markor-rad");
  if (rad) {
    rad.textContent = e === null || n === null ? "Markörens läge visas här." : `E ${Math.round(e).toLocaleString("sv-SE")} · N ${Math.round(n).toLocaleString("sv-SE")}`;
  }
  if (e === null || n === null || !vy || !t.resultat.has(vy.korning_id) || !harSynligtResultat(s as Sida)) {
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

function visaAvlasningText(text: string | null): void {
  const ruta = el("avlasning");
  if (!ruta || text === null) return;
  ruta.innerHTML = `<span class="avl-post">${esc(text)}</span>`;
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
