/*
 * Steg 2 · Förslag: kartan tar största ytan, verktygen ligger i en smal lodrät rad till vänster och
 * panelen till höger har förslagets namn, höjd och taktyp. Ritlogiken är den befintliga
 * (`Ritverktyg`, `PlanDok`, `Kartmotor`); servern kontrollerar och sparar förslaget som förut
 * (`/api/kartverktyg/...`). Förslaget sparas av sig självt när kontrollen är grön.
 */
import { infogaPunkter } from "../karta/formedit.ts";
import { btaUppskattning, yta, type Punkt } from "../karta/geom.ts";
import {
  bytScenarioNamn,
  hamtaProjektInfo,
  hamtaScenario,
  kontrolleraAndringar,
  listaScenarier,
  sparaScenario,
  taBortScenario,
  type Kontroll,
  type ScenarioPost,
} from "../karta/kartapi.ts";
import { LagerLista } from "../karta/lager.ts";
import { monteraLagerpanel } from "../karta/lagerpanel.ts";
import { TOM_RITVY, type RitVy } from "../karta/kartmotor.ts";
import { FORVALD_HOJD_M, Ritverktyg, TANGENTER, type Meddelande, type Verktyg } from "../karta/ritverktyg.ts";
import { esc } from "../sidor.ts";
import { felBeskedHtml, kopplaLaddaOm, sidFel } from "./felhandling.ts";
import { kontoLage } from "../konto.ts";
import { formatera, talFormat } from "../tal.ts";
import { flodeAdress } from "./adress.ts";
import { tolkaFil } from "./filimport.ts";
import { projektKallaText } from "./projektkalla.ts";
import { monteraUnderlagForm, taBortUnderlag } from "./underlag.ts";
import { forslagInfoHtml, type HusUppgift } from "./forslagtext.ts";
import { BasLager, byggnaderMedForslag, skapaKarta } from "./kartdel.ts";
import type { Avslutbar } from "./projektlista.ts";
import { andringarMedTak, FORVALD_TAKFORM, skapaDokFor, sparaTillstand, takformFran, TAKFORMER, tillstandFor } from "./tillstand.ts";

const TAL0 = talFormat(0);

function hjalpText(v: Ritverktyg): string {
  if (v.verktyg === "hus") {
    if (v.utkast.length === 0) return "Klicka husets första hörn i kartan.";
    if (v.utkast.length < 3) return "Klicka nästa hörn. Backsteg tar bort senaste, Esc avbryter.";
    return "Klicka hörnen, dubbelklicka för att avsluta";
  }
  if (v.verktyg === "valj") return "Klicka på ett hus för att markera det. Dra för att flytta huset eller ett hörn.";
  if (v.verktyg === "laggtill") return "Klicka på en kant i ett markerat nytt hus för att lägga till ett hörn.";
  return "Klicka på ett hörn i ett markerat nytt hus för att ta bort det.";
}

export function visaForslag(rot: HTMLElement, projekt: string): Avslutbar {
  const t = tillstandFor(projekt);
  let avslutad = false;
  let kontroll: Kontroll | null = null;
  let kontrollFel: string | null = null;
  let meddelande: Meddelande | null = null;
  let sparFel: string | null = null;
  let sparat = "";
  let kontrollTimer: number | null = null;
  let ritaBegard = 0;
  const avbryt: Array<() => void> = [];
  const ac = new AbortController();

  rot.innerHTML = `
    <div class="flode-sida flode-fast">
      <section class="flode-karta" aria-label="Karta">
        <div class="flode-karta-yta" id="fs-karta"></div>
        <div class="flode-verktyg" role="toolbar" aria-label="Ritverktyg" aria-orientation="vertical">
          <button type="button" data-v="valj" aria-pressed="false" title="Markera ett hus: ändra höjd och tak, dra huset eller ett hörn (${TANGENTER.valj})">Markera</button>
          <button type="button" data-v="hus" aria-pressed="false" title="Rita nytt hus (${TANGENTER.hus})">Rita hus</button>
          <button type="button" data-v="laggtill" aria-pressed="false" title="Lägg till hörn (${TANGENTER.laggtill})">Lägg till hörn</button>
          <button type="button" data-v="tabort" aria-pressed="false" title="Ta bort hörn (${TANGENTER.tabort})">Ta bort hörn</button>
          <button type="button" data-v="bort" title="Tar bort det markerade huset (en befintlig byggnad rivs)" disabled>Ta bort hus</button>
          <span class="flode-verktyg-skil" aria-hidden="true"></span>
          <button type="button" data-v="fast" aria-pressed="true" title="Fäst mot hörn och kanter (${TANGENTER.fast})">Fäst</button>
          <button type="button" data-v="rat" aria-pressed="false" title="Rät vinkel (${TANGENTER.ratVinkel}, eller håll Ctrl)">Rät vinkel</button>
          <span class="flode-verktyg-skil" aria-hidden="true"></span>
          <button type="button" data-v="angra" title="Ångra (${TANGENTER.angra})" disabled>Ångra</button>
          <button type="button" data-v="goraom" title="Gör om (Skift+${TANGENTER.angra})" disabled>Gör om</button>
          <span class="flode-verktyg-skil" aria-hidden="true"></span>
          <button type="button" data-v="fil" title="Läs in hus ur en GeoJSON-fil eller ett sparat förslag">Läs in fil</button>
          <input type="file" id="fs-fil" aria-label="Välj en GeoJSON-fil som planunderlag" accept=".geojson,.json,application/geo+json,application/json" hidden />
        </div>
        <div class="flode-zoom" role="group" aria-label="Zoom">
          <button type="button" id="fs-in" aria-label="Zooma in">+</button>
          <button type="button" id="fs-ut" aria-label="Zooma ut">−</button>
          <button type="button" id="fs-anpassa">Anpassa</button>
        </div>
        <div class="flode-kartkort flode-hjalp" id="fs-hjalp" role="status"></div>
        <div class="flode-kartkort flode-kartkort-hoger" id="fs-laddar">Hämtar kartan …</div>
      </section>
      <aside class="flode-panel" aria-labelledby="forslag-rubrik">
        <div class="flode-panelhuvud">
          <h1 id="forslag-rubrik" class="flode-h">Förslag</h1>
          <p class="flode-dampad">Jämförs med dagens läge</p>
        </div>
        <div class="flode-falt"><label for="fs-namn">Namn</label>
          <input id="fs-namn" type="text" maxlength="80" placeholder="Mitt förslag" autocomplete="off" /></div>
        <div class="flode-tva">
          <div class="flode-falt"><label for="fs-hojd">Höjd (m)</label>
            <input id="fs-hojd" type="text" inputmode="decimal" aria-describedby="fs-hojd-hjalp" disabled /></div>
          <div class="flode-falt"><label for="fs-tak">Tak</label>
            <select id="fs-tak" disabled>${TAKFORMER.map((f) => `<option value="${f.id}">${esc(f.namn)}</option>`).join("")}</select></div>
        </div>
        <p class="flode-liten flode-dampad" id="fs-hojd-hjalp"></p>
        <p class="flode-liten flode-dampad" id="fs-projektkalla" hidden></p>
        <div class="flode-ruta" id="fs-info" role="status"></div>
        <details class="flode-detalj" id="fs-lager-ruta">
          <summary>Lager och underlag</summary>
          <div class="lagerpanel" id="fs-lagerpanel"></div>
          <details class="flode-detalj inre" id="fs-underlag-ruta"><summary>Lägg till underlag från en tjänst</summary><div id="fs-underlag"></div></details>
        </details>
        <details class="flode-detalj" id="fs-sparade-ruta">
          <summary>Sparade förslag</summary>
          <div id="fs-sparade"></div>
        </details>
        <div class="flode-rad-slut">
          <a class="flode-knapp svart stor" id="fs-vidare" href="${flodeAdress(projekt, "fragor")}">Välj frågor</a>
        </div>
      </aside>
    </div>`;
  const el = <T extends HTMLElement>(id: string): T => rot.querySelector(`#${id}`) as T;

  const klar = (async () => {
    let info;
    try {
      info = await hamtaProjektInfo(projekt);
    } catch (e) {
      el("fs-laddar").innerHTML = felBeskedHtml(sidFel(e, "Kartan", kontoLage(), `/#/p/${encodeURIComponent(projekt)}/forslag`), esc);
      kopplaLaddaOm(el("fs-laddar"));
      return;
    }
    if (avslutad) return;
    el("fs-laddar").hidden = true;
    const kallText = projektKallaText(info);
    if (kallText) {
      el("fs-projektkalla").textContent = kallText;
      el("fs-projektkalla").hidden = false;
    }
    skapaDokFor(t, info);
    const dok = t.dok;
    const motor = skapaKarta(el("fs-karta"), info);
    const lagerLista = new LagerLista(projekt);
    const bas = new BasLager(motor, info, lagerLista);
    bas.registrera(projekt);
    const synkaLager = (): void => {
      bas.synka();
      motor.settByggnadStil(bas.byggnadStil());
      motor.settPlanStil(bas.planStil());
    };
    monteraLagerpanel(el("fs-lagerpanel"), lagerLista, {
      andrad: synkaLager,
      taBort: (id) => taBortUnderlag(projekt, bas, id),
      grupper: ["underlag", "geometri", "planforslag"],
    });
    monteraUnderlagForm(el("fs-underlag"), projekt, bas, ac.signal);
    void bas.ladda(projekt).then(synkaLager);
    const verktyg = new Ritverktyg(
      dok,
      info.byggnader.map((b) => ({ id: b.id, ring: b.ring.map(([x, y]) => ({ x, y })), hal: b.hal.map((h) => h.map(([x, y]) => ({ x, y }))), hojd_m: b.hojd_m })),
      {
        meterPerPx: () => motor.meterPerPx(),
        meddela: (m) => {
          meddelande = m;
          visaHjalp();
        },
        husSkapat: () =>
          window.requestAnimationFrame(() => {
            const f = el<HTMLInputElement>("fs-hojd");
            if (!f.disabled) {
              f.focus();
              f.select();
            }
          }),
      },
    );
    motor.settPekare({
      ror: (pt, e) => verktyg.ror(pt, e),
      klick: (pt, e) => verktyg.klick(pt, e),
      dubbelklick: () => verktyg.dubbelklick(),
      ned: (pt, e) => verktyg.ned(pt, e),
      upp: () => verktyg.upp(),
      lamnar: () => verktyg.ror({ x: Number.NaN, y: Number.NaN }, { pekare: "mouse", ctrl: false, shift: false, alt: false }),
    });
    motor.settByggnadStil(bas.byggnadStil());
    motor.settPlanStil(bas.planStil());
    motor.onVyAndrad = () => planeraRitning();
    const tangent = (h: KeyboardEvent): void => {
      const m = h.target as HTMLElement | null;
      if (m && (m.tagName === "INPUT" || m.tagName === "TEXTAREA" || m.tagName === "SELECT" || m.isContentEditable)) return;
      if (verktyg.tangent(h)) h.preventDefault();
    };
    document.addEventListener("keydown", tangent);
    avbryt.push(() => document.removeEventListener("keydown", tangent), () => {
      bas.avsluta();
      motor.avsluta();
    });

    // --- Förslagets fil: öppnas om ett sparat förslag finns och inget är ritat ---
    const namnFalt = el<HTMLInputElement>("fs-namn");
    namnFalt.value = t.namn || t.scenario?.namn || "";
    if (t.scenario && dok.tom()) {
      try {
        const d = await hamtaScenario(projekt, t.scenario.slug);
        if (avslutad) return;
        t.takform = takformFran(d.andringar);
        dok.fran(d.andringar);
        namnFalt.value = d.namn;
        t.namn = d.namn;
        sparat = JSON.stringify(andringarMedTak(dok, t.takform)) + d.namn;
      } catch {
        t.scenario = null; // Filen finns inte längre; man börjar om.
      }
    }

    // --- Ritning ---
    const planeraRitning = (): void => {
      if (ritaBegard) return;
      ritaBegard = window.requestAnimationFrame(() => {
        ritaBegard = 0;
        uppdateraRit();
      });
    };
    const uppdateraRit = (): void => {
      const v = verktyg;
      const hus: RitVy["hus"] = [];
      for (const h of dok.ny.values()) hus.push({ id: h.id, ring: v.dragRing?.id === h.id ? v.dragRing.ring : h.ring, vald: Boolean(v.val?.ny && v.val.id === h.id), slag: "ny" });
      if (v.val && !v.val.ny) {
        const b = info.byggnader.find((x) => x.id === v.val?.id);
        if (b) hus.push({ id: b.id, ring: b.ring.map(([x, y]) => ({ x, y })), vald: true, slag: dok.bort.has(b.id) ? "bort" : "andrad" });
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
      motor.settRit({
        ...TOM_RITVY,
        hus,
        utkast: v.utkast,
        mark: v.verktyg === "hus" ? v.mark : null,
        fast: v.fast ? { at: v.fast.at, slag: ({ hornpunkt: "hörn", skarning: "skärning", kant: "kant" } as Record<string, string>)[v.fast.typ] ?? "fäst" } : null,
        lasLinje: v.lasLinje,
        horn,
        mitt,
        etikett: v.ritText(),
      });
      motor.settMarkorstil(v.verktyg === "valj" ? (v.drar ? "grabbing" : "") : "crosshair");
      motor.settByggnader(byggnaderMedForslag(info, dok));
      uppdateraVerktyg();
      uppdateraPanel();
    };

    // --- Verktyg ---
    const knapp = (id: string): HTMLButtonElement | null => rot.querySelector<HTMLButtonElement>(`[data-v="${id}"]`);
    const tryckt = (id: string, pa: boolean): void => knapp(id)?.setAttribute("aria-pressed", String(pa));
    const uppdateraVerktyg = (): void => {
      for (const v of ["valj", "hus", "laggtill", "tabort"] as const) tryckt(v, verktyg.verktyg === v);
      tryckt("fast", verktyg.fastPa);
      tryckt("rat", verktyg.ratVinkel);
      const bort = knapp("bort");
      if (bort) bort.disabled = !verktyg.val;
      const angra = knapp("angra");
      if (angra) angra.disabled = !dok.kanAngra();
      const goraOm = knapp("goraom");
      if (goraOm) goraOm.disabled = !dok.kanGoraOm();
      visaHjalp();
    };
    const visaHjalp = (): void => {
      const h = el("fs-hjalp");
      const text = meddelande ? meddelande.text : hjalpText(verktyg);
      h.textContent = text;
      h.classList.toggle("fel", meddelande?.typ === "fel");
    };
    for (const v of ["valj", "hus", "laggtill", "tabort"] as const) {
      knapp(v)?.addEventListener("click", () => {
        meddelande = null;
        verktyg.sattVerktyg(v as Verktyg);
        if (v === "valj") {
          const f = el<HTMLInputElement>("fs-hojd");
          if (!f.disabled) {
            f.focus();
            f.select();
          } else {
            meddelande = { typ: "info", text: "Klicka på ett hus i kartan och ange sedan höjden i panelen." };
            visaHjalp();
          }
        }
      });
    }
    knapp("bort")?.addEventListener("click", () => verktyg.taBortVal());
    knapp("fast")?.addEventListener("click", () => verktyg.vaxlaFast());
    knapp("rat")?.addEventListener("click", () => verktyg.vaxlaRatVinkel());
    knapp("angra")?.addEventListener("click", () => verktyg.angra());
    knapp("goraom")?.addEventListener("click", () => verktyg.goraOm());
    el("fs-in").addEventListener("click", () => motor.zooma(0.7));
    el("fs-ut").addEventListener("click", () => motor.zooma(1 / 0.7));
    el("fs-anpassa").addEventListener("click", () => motor.anpassa(info.utbredning));
    const filFalt = el<HTMLInputElement>("fs-fil");
    knapp("fil")?.addEventListener("click", () => filFalt.click());
    filFalt.addEventListener("change", () => {
      const fil = filFalt.files?.[0];
      filFalt.value = "";
      if (!fil) return;
      void fil.text().then((text) => {
        const r = tolkaFil(text);
        if (r.slag === "fel") {
          meddelande = { typ: "fel", text: r.text };
        } else if (r.slag === "forslag") {
          t.takform = takformFran(r.andringar);
          dok.fran(r.andringar);
          meddelande = { typ: "info", text: `Läste in ${r.andringar.length} ändringar ur förslagsfilen.` };
        } else {
          dok.grupp("fil", () => {
            for (const h of r.hus) dok.laggTillHus(h.ring, h.hojd_m ?? FORVALD_HOJD_M);
          });
          meddelande = { typ: "info", text: `Läste in ${r.hus.length} hus. Kontrollera höjderna: filer utan höjd får ${FORVALD_HOJD_M} m.` };
        }
        visaHjalp();
        planeraKontroll();
      });
    });

    // --- Panelen ---
    const hojdFalt = el<HTMLInputElement>("fs-hojd");
    const takFalt = el<HTMLSelectElement>("fs-tak");
    const uppdateraPanel = (): void => {
      const val = verktyg.val;
      const hjalp = el("fs-hojd-hjalp");
      if (!val) {
        hojdFalt.disabled = true;
        takFalt.disabled = true;
        if (document.activeElement !== hojdFalt) hojdFalt.value = "";
        hjalp.textContent = "Markera ett hus i kartan för att ändra höjd och tak.";
      } else {
        const h = verktyg.hojdAv(val);
        const riven = dok.bort.has(val.id);
        hojdFalt.disabled = riven;
        if (document.activeElement !== hojdFalt) hojdFalt.value = h === null ? "" : String(Math.round(h * 100) / 100);
        takFalt.disabled = !val.ny;
        takFalt.value = (val.ny ? t.takform.get(val.id) : null) ?? FORVALD_TAKFORM;
        hjalp.textContent = val.ny
          ? `Större än 0 och högst ${TAL0.format(info.hojd_max_m)} m.`
          : `Befintlig byggnad${riven ? ", riven" : ""}. Taktyp kan bara anges för nya hus.`;
      }
      el("fs-info").innerHTML = infoHtml();
    };
    hojdFalt.addEventListener("change", () => {
      const n = Number(hojdFalt.value.replace(",", "."));
      if (hojdFalt.value.trim() === "" || !Number.isFinite(n)) {
        meddelande = { typ: "fel", text: "Ange höjden som ett tal i meter." };
        visaHjalp();
        return;
      }
      meddelande = null;
      verktyg.sattHojd(n);
    });
    takFalt.addEventListener("change", () => {
      const v = verktyg.val;
      if (!v?.ny) return;
      if (takFalt.value === FORVALD_TAKFORM) t.takform.delete(v.id);
      else t.takform.set(v.id, takFalt.value);
      planeraKontroll();
    });
    namnFalt.addEventListener("change", () => {
      t.namn = namnFalt.value.trim();
      sparaTillstand(t);
      planeraKontroll();
    });

    const infoHtml = (): string => {
      const val = verktyg.val;
      let hus: HusUppgift | null = null;
      if (val?.ny) {
        const ring = verktyg.ringAv(val);
        const h = verktyg.hojdAv(val);
        const k = kontroll?.hus.find((x) => x.andring_id === val.id);
        hus = {
          yta_m2: k?.yta_m2 ?? (ring ? yta(ring) : 0),
          vaningar: k?.vaningar ?? Math.max(1, Math.round((h ?? 0) / info.vaningshojd_m)),
          bta_m2: k?.bta_m2 ?? (h !== null && ring ? btaUppskattning(yta(ring), h, info.vaningshojd_m) : 0),
        };
      }
      const antal = dok.tillAndringar().length;
      return forslagInfoHtml({
        antal,
        hus,
        kontrollFel,
        fel: kontroll?.fel ?? [],
        varningar: kontroll?.varningar ?? [],
        information: kontroll?.information ?? [],
        sparFel,
        sparatSom: t.scenario && antal > 0 && kontroll?.giltig ? t.scenario.namn : null,
      });
    };

    // --- Kontroll och sparande hos servern ---
    const planeraKontroll = (): void => {
      if (kontrollTimer !== null) window.clearTimeout(kontrollTimer);
      kontrollTimer = window.setTimeout(() => void kontrolleraOchSpara(), 250);
      planeraRitning();
    };
    const kontrolleraOchSpara = async (): Promise<void> => {
      const andringar = andringarMedTak(dok, t.takform);
      if (andringar.length === 0) {
        kontroll = null;
        kontrollFel = null;
        planeraRitning();
        return;
      }
      try {
        kontroll = await kontrolleraAndringar(projekt, andringar);
        kontrollFel = null;
      } catch (e) {
        kontroll = null;
        kontrollFel = e instanceof Error ? e.message : String(e);
      }
      if (avslutad) return;
      if (kontroll?.giltig) {
        // Sparandet går i en kö så att två ändringar i följd inte skapar två förslag.
        sparKo = sparKo.then(() => spara(andringarMedTak(dok, t.takform)));
        await sparKo;
      }
      planeraRitning();
    };
    let sparKo: Promise<void> = Promise.resolve();
    const spara = async (andringar: ReturnType<typeof andringarMedTak>): Promise<void> => {
      const namn = (namnFalt.value.trim() || "Mitt förslag").trim();
      const nyckel = JSON.stringify(andringar) + namn;
      if (nyckel === sparat) return;
      try {
        const gammal = t.scenario;
        let d = await sparaScenario(projekt, gammal ? gammal.namn : namn, andringar, gammal?.slug);
        if (gammal && gammal.namn !== namn) d = await bytScenarioNamn(projekt, gammal.slug, namn);
        t.scenario = { slug: d.slug, namn: d.namn };
        t.namn = namnFalt.value.trim();
        sparat = nyckel;
        sparFel = null;
        sparaTillstand(t);
        void lasSparade();
      } catch (e) {
        sparFel = e instanceof Error ? e.message : String(e);
      }
    };
    // --- Sparade förslag: öppna, byt namn, ta bort, nytt tomt (filer på den här datorn) ---
    let scenarier: ScenarioPost[] = [];
    let listFel: string | null = null;
    const ritaSparade = (): void => {
      const rad = scenarier
        .map(
          (x) => `<li class="flode-scen${t.scenario?.slug === x.slug ? " aktivt" : ""}">
            <span>${esc(x.namn)} <span class="flode-dampad flode-liten">${formatera(x.antal_andringar, 0)} ändring${x.antal_andringar === 1 ? "" : "ar"}</span></span>
            <span class="flode-scen-knappar">
              <button type="button" data-oppna="${esc(x.slug)}">Öppna</button>
              <button type="button" data-bytnamn="${esc(x.slug)}">Byt namn</button>
              <button type="button" data-tabort="${esc(x.slug)}" aria-label="Ta bort ${esc(x.namn)}">Ta bort</button>
            </span></li>`,
        )
        .join("");
      el("fs-sparade").innerHTML = `
        <div class="flode-rad"><button type="button" class="flode-knapp" id="fs-nytt">Nytt tomt förslag</button></div>
        ${listFel ? `<p class="flode-fel flode-liten" role="alert">${esc(listFel)}</p>` : ""}
        ${rad ? `<ul class="flode-scenlista">${rad}</ul>` : `<p class="flode-liten flode-dampad">Inga sparade förslag. De sparas som filer på din dator, inte i projektets filer.</p>`}`;
    };
    const lasSparade = async (): Promise<void> => {
      try {
        scenarier = (await listaScenarier(projekt)).scenarier;
      } catch {
        /* Listan finns kvar som den var. */
      }
      if (!avslutad) ritaSparade();
    };
    const oppnaScenario = async (slug: string): Promise<void> => {
      const d = await hamtaScenario(projekt, slug);
      if (avslutad) return;
      t.takform = takformFran(d.andringar);
      verktyg.markera(null);
      dok.fran(d.andringar);
      t.scenario = { slug: d.slug, namn: d.namn };
      t.namn = d.namn;
      namnFalt.value = d.namn;
      sparat = JSON.stringify(andringarMedTak(dok, t.takform)) + d.namn;
      sparaTillstand(t);
      ritaSparade();
    };
    el("fs-sparade").addEventListener("click", (h) => {
      const k = (h.target as Element).closest<HTMLButtonElement>("button");
      if (!k) return;
      const fel = (e: unknown): void => {
        listFel = e instanceof Error ? e.message : String(e);
        ritaSparade();
      };
      if (k.id === "fs-nytt") {
        dok.rensa();
        verktyg.markera(null);
        t.scenario = null;
        t.namn = "";
        t.takform.clear();
        namnFalt.value = "";
        sparat = "";
        kontroll = null;
        sparaTillstand(t);
        planeraKontroll();
        ritaSparade();
      } else if (k.dataset.oppna) {
        listFel = null;
        void oppnaScenario(k.dataset.oppna).catch(fel);
      } else if (k.dataset.bytnamn) {
        const slug = k.dataset.bytnamn;
        const gammalt = scenarier.find((x) => x.slug === slug)?.namn ?? "";
        const nytt = window.prompt("Nytt namn på förslaget", gammalt);
        if (!nytt || nytt.trim() === gammalt) return;
        void bytScenarioNamn(projekt, slug, nytt.trim()).then((d) => {
          if (t.scenario?.slug === slug) {
            t.scenario = { slug: d.slug ?? slug, namn: d.namn };
            t.namn = d.namn;
            namnFalt.value = d.namn;
            sparat = JSON.stringify(andringarMedTak(dok, t.takform)) + d.namn;
            sparaTillstand(t);
          }
          return lasSparade();
        }, fel);
      } else if (k.dataset.tabort) {
        const slug = k.dataset.tabort;
        const namn = scenarier.find((x) => x.slug === slug)?.namn ?? slug;
        if (!window.confirm(`Ta bort förslaget ${namn}? Filen på din dator raderas.`)) return;
        void taBortScenario(projekt, slug).then(() => {
          if (t.scenario?.slug === slug) {
            t.scenario = null;
            sparat = "";
            sparaTillstand(t);
          }
          return lasSparade();
        }, fel);
      }
    });
    ritaSparade();
    void lasSparade();

    dok.vidAndring(() => {
      kontroll = null;
      planeraKontroll();
    });
    verktyg.vidAndring(planeraRitning);
    visaHjalp();
    uppdateraRit();
    if (!dok.tom()) planeraKontroll();
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => motor.matOm()));
  })();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      if (kontrollTimer !== null) window.clearTimeout(kontrollTimer);
      if (ritaBegard) window.cancelAnimationFrame(ritaBegard);
      ac.abort();
      for (const f of avbryt) f();
    },
  };
}
