/*
 * Steg 5 · Rapport: innehållsförteckning med en statusprick per fråga, förhandsvisning (serif i
 * brödtexten) och kontroll med export (PDF och Word). Innehållet kommer ur rapportarkivet
 * (`GET /api/projekt/<id>/rapporter/<rapport>`); talen är Flodens värden ur påståendenas belägg och
 * markeras. Panelen "Skapa rapport" (rapportskapa.ts) startar ett jobb på servern (K48), visar förloppet och
 * läser sedan in den nya rapporten med export.
 */
import { hamtaRapportJobb, lasJson, startaRapportJobb } from "../api.ts";
import { esc } from "../sidor.ts";
import { felBeskedHtml, kopplaLaddaOm, sidFel } from "./felhandling.ts";
import { kontoLage } from "../konto.ts";
import type { RapportAmne, RapportJobb, RapportRad } from "../types.ts";
import type { Avslutbar } from "./projektlista.ts";
import { flodeAdress } from "./adress.ts";
import { hamtaFragelista } from "./fragor.ts";
import { arPagaende, skapaPanelHtml, valjUnderlag } from "./rapportskapa.ts";
import type { FlodeFraga } from "./status.ts";
import { sparaTillstand, tillstandFor } from "./tillstand.ts";

export interface RapportDel {
  text: string;
  tal: boolean;
}
export interface RapportPastaende {
  pastaende_id: string;
  status: string;
  status_text: string;
  fordjupning: string;
  fordjupning_text: string;
  delar: RapportDel[];
  belagg: string[];
  forutsattning: string | null;
  saknat_underlag: string[];
}
export interface RapportFraga {
  id: string;
  fraga: string;
  pastaenden: RapportPastaende[];
}
export interface RapportInnehall extends RapportRad {
  skede: string;
  avsandare: string;
  fragor: RapportFraga[];
  metodnotis: string;
  kontroll_punkter: Array<{ id: string; rubrik: string; godkand: boolean; fel: string[] }>;
}

export type Prick = "gron" | "gul" | "gra";

/** Statusprick per fråga ur påståendenas fördjupningsbehov (samma trafikljus som rapporten): sämst gäller. */
export function prickFor(f: RapportFraga): Prick {
  if (f.pastaenden.length === 0) return "gra";
  if (f.pastaenden.some((p) => p.fordjupning === "kan_inte_avgoras")) return "gra";
  if (f.pastaenden.some((p) => p.fordjupning === "ja")) return "gul";
  return "gron";
}

export const PRICK_TEXT: Record<Prick, string> = {
  gron: "Ingen fördjupning behövs i detta skede",
  gul: "Behöver fördjupas",
  gra: "Kan inte avgöras med nuvarande underlag",
};

/** Sammanfattningens mening, bara räknad på rapportens egna frågor. */
export function sammanfattningsText(fragor: ReadonlyArray<RapportFraga>): string {
  const n = fragor.length;
  const gul = fragor.filter((f) => prickFor(f) === "gul").length;
  const gra = fragor.filter((f) => prickFor(f) === "gra").length;
  return `Av de ${n} frågorna behöver ${gul} utredas vidare och ${gra} kan inte bedömas med nuvarande underlag.`;
}

const datum = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10) : d.toLocaleDateString("sv-SE", { year: "numeric", month: "long", day: "numeric" });
};

export function pastaendeHtml(p: RapportPastaende): string {
  const text = p.delar.map((d) => (d.tal ? `<mark>${esc(d.text)}</mark>` : esc(d.text))).join("");
  const meta = [p.status_text, p.fordjupning_text, p.belagg.length > 0 ? `Belägg: ${p.belagg.join(", ")}` : ""].filter(Boolean).join(". ");
  const saknat = p.saknat_underlag.length > 0 ? ` Saknas: ${p.saknat_underlag.join(" ")}` : "";
  return `<p class="flode-brodtext">${text}</p><p class="flode-rapport-meta">${esc(meta)}.${esc(saknat)}</p>`;
}

export function visaRapport(rot: HTMLElement, projekt: string): Avslutbar {
  let avslutad = false;
  rot.innerHTML = `<div class="flode-sida"><p class="flode-dampad flode-pad">Hämtar rapporten …</p></div>`;
  const bas = `/api/projekt/${encodeURIComponent(projekt)}/rapporter`;
  const t = tillstandFor(projekt);
  const ac = new AbortController();
  kopplaLaddaOm(rot, ac.signal);
  let amnen: RapportAmne[] = [];
  let valtAmne = "skyfall";
  let jobb: RapportJobb | null = null;
  let jobbFel: string | null = null;
  let katalog: FlodeFraga[] | null = null;
  let timer: number | null = null;

  const underlagNu = (): ReturnType<typeof valjUnderlag> =>
    valjUnderlag(amnen.find((a) => a.id === valtAmne), t.scenario, t.valda, katalog);

  const frageurvalHtml = (): string => {
    const amnesFragor = (katalog ?? []).filter((f) => f.amne === valtAmne);
    const valdaAntal = amnesFragor.filter((f) => t.valda?.has(f.id)).length;
    const listaHtml =
      amnesFragor.length > 0
        ? `<div class="ar-rapport-fraglista">${amnesFragor
            .map(
              (f) => `<label class="ar-rapport-fragarad"><input type="checkbox" data-rp-fraga="${esc(f.id)}"${t.valda?.has(f.id) ? " checked" : ""} /><span>${esc(f.id)} ${esc(f.text)}</span></label>`,
            )
            .join("")}</div>`
        : `<p class="flode-liten flode-dampad">Inga katalogfrågor hämtade för det här ämnet än.</p>`;
    return `<details class="ar-rapport-fragor" id="rp-frageurval">
      <summary>Frågeurval &amp; beställning (${amnesFragor.length > 0 ? `${valdaAntal} av ${amnesFragor.length} valda` : "alla frågor"})</summary>
      ${listaHtml}
      <p class="flode-liten flode-dampad">Välj vilka frågor som ska ingå i rapporten, eller öppna <a href="${flodeAdress(projekt, "fragor")}">hela frågevyn</a>.</p>
    </details>`;
  };

  const ritaSkapa = (): void => {
    const m = rot.querySelector<HTMLElement>("#rp-skapa");
    if (!m) return;
    const oppen = m.querySelector<HTMLDetailsElement>("#rp-frageurval")?.open ?? false;
    m.innerHTML =
      frageurvalHtml() +
      skapaPanelHtml({ projekt, underlag: underlagNu(), amnen, valt: valtAmne, best: t.bestallning, jobb, fel: jobbFel });
    if (oppen) {
      const det = m.querySelector<HTMLDetailsElement>("#rp-frageurval");
      if (det) det.open = true;
    }
  };

  const ritaTom = (text: string, fel = false, ferdig = false): void => {
    rot.innerHTML = `
      <div class="flode-sida"><section class="flode-bred" aria-labelledby="rp-rubrik">
        <h1 id="rp-rubrik" class="flode-h">Rapport</h1>
        ${ferdig ? text : `<p class="${fel ? "flode-fel" : "flode-dampad"}"${fel ? ' role="alert"' : ""}>${text}</p>`}
        <div id="rp-skapa"></div>
      </section></div>`;
    ritaSkapa();
  };

  const rita = (r: RapportInnehall, lista: RapportRad[]): void => {
    const nav = r.fragor
      .map((f) => {
        const p = prickFor(f);
        return `<button type="button" class="flode-innehall-rad" data-mal="fraga-${esc(f.id)}"><span class="flode-prick ${p}" role="img" aria-label="${esc(PRICK_TEXT[p])}"></span>${esc(f.id)} ${esc(f.fraga)}</button>`;
      })
      .join("");
    const val = lista.length > 1
      ? `<label class="flode-falt"><span class="flode-liten flode-dampad">Rapport</span><select id="rp-val">${lista.map((x) => `<option value="${esc(x.rapport_id)}"${x.rapport_id === r.rapport_id ? " selected" : ""}>${esc(x.titel)} (${esc(x.skapad.slice(0, 10))})</option>`).join("")}</select></label>`
      : "";
    const fragorHtml = r.fragor
      .map((f) => `<h2 id="fraga-${esc(f.id)}">${esc(f.id)} ${esc(f.fraga)}</h2>${f.pastaenden.length > 0 ? f.pastaenden.map(pastaendeHtml).join("") : `<p class="flode-rapport-meta">Inget påstående för frågan i den här rapporten.</p>`}`)
      .join("");
    const k = r.kontroll;
    const kontrollRuta = !k
      ? `<p class="flode-ruta">Rapporten är inte kontrollerad än. Export kräver en godkänd kontroll.</p>`
      : k.godkand
        ? `<p class="flode-ruta ok">Kontrollen är godkänd. Markerade tal hämtas ur Floden och kan inte skrivas för hand.</p>`
        : `<p class="flode-ruta varning">Kontrollen hittade ${k.antal_fel} fel. Rapporten kan inte exporteras förrän de är rättade.</p>`;
    const punkter = r.kontroll_punkter
      .map((q) => `<li><span>${esc(q.rubrik)}</span><span class="${q.godkand ? "ok" : "fel"}">${q.godkand ? "Godkänd" : "Fel"}</span></li>`)
      .join("");
    const knapp = (fmt: "pdf" | "word", namn: string, svart: boolean): string =>
      r.filer[fmt]
        ? `<a class="flode-knapp stor${svart ? " svart" : ""}" href="${bas}/${encodeURIComponent(r.rapport_id)}/fil/${fmt}" download>${namn}</a>`
        : `<span class="flode-knapp stor inaktiv" aria-disabled="true">${namn}</span>`;
    const harFil = Boolean(r.filer.pdf || r.filer.word);
    rot.innerHTML = `
      <div class="flode-sida flode-rapportsida">
        <nav class="flode-innehall" aria-labelledby="innehall-rubrik">
          <h2 id="innehall-rubrik" class="flode-liten-rubrik">Innehåll</h2>
          ${val}
          <button type="button" class="flode-innehall-rad" data-mal="rp-sammanfattning">Sammanfattning</button>
          ${nav}
          <button type="button" class="flode-innehall-rad" data-mal="rp-metod">Metod och osäkerhet</button>
        </nav>
        <section class="flode-forhandsvisning" aria-label="Förhandsvisning" tabindex="0">
          <article class="flode-papper">
            <div class="flode-rapport-huvud"><strong>${esc(r.avsandare || "Floden")}</strong><span>Skissunderlag · ${esc(datum(r.skapad))}</span></div>
            <h1 id="rp-rubrik" class="flode-rapport-titel">${esc(r.titel)}</h1>
            <h2 id="rp-sammanfattning" class="visuellt-dold">Sammanfattning</h2>
            <p class="flode-brodtext">${esc(sammanfattningsText(r.fragor))}</p>
            ${fragorHtml}
            <h2 id="rp-metod">Metod och osäkerhet</h2>
            <p class="flode-brodtext">${esc(r.metodnotis || "Metodens och osäkerhetens redovisning finns i den exporterade rapporten.")}</p>
          </article>
        </section>
        <aside class="flode-panel" aria-labelledby="kontroll-rubrik">
          <div id="rp-skapa"></div>
          <h2 id="kontroll-rubrik" class="flode-h">Kontroll</h2>
          ${kontrollRuta}
          <ul class="flode-kontrollista">${punkter}</ul>
          <div class="flode-rad-slut">
            ${harFil ? "" : `<p class="flode-liten flode-dampad">Ingen fil är exporterad ännu. Exporten görs efter en godkänd kontroll.</p>`}
            <div class="flode-export">${knapp("pdf", "PDF", true)}${knapp("word", "Word", false)}</div>
          </div>
        </aside>
      </div>`;
    ritaSkapa();
    rot.querySelector<HTMLSelectElement>("#rp-val")?.addEventListener("change", (h) => void ladda((h.target as HTMLSelectElement).value, lista));
    for (const b of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-mal]"))) {
      b.addEventListener("click", () => rot.querySelector<HTMLElement>(`#${CSS.escape(b.dataset.mal ?? "")}`)?.scrollIntoView({ block: "start", behavior: "smooth" }));
    }
  };

  const ladda = async (id: string, lista: RapportRad[]): Promise<void> => {
    try {
      const r = await lasJson<RapportInnehall>(`${bas}/${encodeURIComponent(id)}`);
      if (!avslutad) rita(r, lista);
    } catch (e) {
      if (!avslutad) ritaTom(felBeskedHtml(sidFel(e, "Rapporten", kontoLage(), `/#/p/${encodeURIComponent(projekt)}/rapport`), esc), true, true);
    }
  };

  const stoppaTimer = (): void => {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
  };
  const efterJobb = async (): Promise<void> => {
    // Läs in listan på nytt och visa den nya rapporten (med export om kontrollen godkände den).
    try {
      const svar = await lasJson<{ rapporter: RapportRad[]; amnen?: RapportAmne[] }>(bas);
      amnen = svar.amnen ?? amnen;
      const nyId = jobb?.rapport_id ?? svar.rapporter[0]?.rapport_id;
      if (nyId && !avslutad) await ladda(nyId, svar.rapporter);
    } catch (e) {
      jobbFel = e instanceof Error ? e.message : String(e);
      if (!avslutad) ritaSkapa();
    }
  };
  const folj = async (): Promise<void> => {
    if (!jobb || avslutad) return;
    try {
      jobb = await hamtaRapportJobb(jobb.status_url);
    } catch (e) {
      stoppaTimer();
      jobbFel = e instanceof Error ? e.message : String(e);
      jobb = null;
      ritaSkapa();
      return;
    }
    if (!arPagaende(jobb)) {
      stoppaTimer();
      if (jobb.rapport_id) await efterJobb();
      else ritaSkapa();
      return;
    }
    ritaSkapa();
  };
  const startaJobb = async (): Promise<void> => {
    jobbFel = null;
    try {
      jobb = await startaRapportJobb(
        projekt,
        valtAmne,
        { syfte: t.bestallning.syfte, skede: t.bestallning.skede, mottagare_roll: t.bestallning.mottagare },
        underlagNu().val,
      );
    } catch (e) {
      jobbFel = e instanceof Error ? e.message : String(e);
      jobb = null;
      ritaSkapa();
      return;
    }
    ritaSkapa();
    stoppaTimer();
    timer = window.setInterval(() => void folj(), 1500);
  };
  rot.addEventListener(
    "click",
    (h) => {
      const m = (h.target as Element).closest<HTMLElement>("[data-rp-amne],[data-rp-skede],#rp-skapa-knapp");
      if (!m) return;
      if (m.dataset.rpAmne) valtAmne = m.dataset.rpAmne;
      else if (m.dataset.rpSkede) {
        t.bestallning.skede = m.dataset.rpSkede as "skiss" | "planunderlag";
        sparaTillstand(t);
      } else if (m.id === "rp-skapa-knapp") {
        void startaJobb();
        return;
      }
      ritaSkapa();
    },
    { signal: ac.signal },
  );
  rot.addEventListener(
    "change",
    (h) => {
      const m = h.target as HTMLInputElement | HTMLSelectElement;
      if (m.id === "rp-mottagare") {
        t.bestallning.mottagare = m.value;
        sparaTillstand(t);
      } else if (m.dataset.rpFraga) {
        t.valda ??= new Set();
        if ((m as HTMLInputElement).checked) t.valda.add(m.dataset.rpFraga);
        else t.valda.delete(m.dataset.rpFraga);
        sparaTillstand(t);
        ritaSkapa();
      }
    },
    { signal: ac.signal },
  );
  rot.addEventListener(
    "input",
    (h) => {
      const m = h.target as HTMLTextAreaElement;
      if (m.id === "rp-syfte") {
        t.bestallning.syfte = m.value;
        sparaTillstand(t);
      }
    },
    { signal: ac.signal },
  );

  const klar = (async () => {
    // Frågorna från steg 3 (id ur huvudkatalogen); utan katalog skickas inga och rapporten tar alla frågor för ämnet.
    void hamtaFragelista()
      .then((l) => {
        katalog = l.kalla === "katalog" ? l.fragor : null;
        if (!avslutad) ritaSkapa();
      })
      .catch(() => undefined);
    let lista: RapportRad[];
    try {
      const svar = await lasJson<{ rapporter: RapportRad[]; amnen?: RapportAmne[] }>(bas);
      lista = svar.rapporter;
      amnen = svar.amnen ?? [];
      valtAmne = amnen.find((a) => a.kan_skapas)?.id ?? amnen[0]?.id ?? "skyfall";
    } catch (e) {
      if (!avslutad) ritaTom(felBeskedHtml(sidFel(e, "Rapporterna", kontoLage(), `/#/p/${encodeURIComponent(projekt)}/rapport`), esc), true, true);
      return;
    }
    if (avslutad) return;
    const forsta = lista[0];
    if (!forsta) {
      ritaTom("Det finns ingen rapport för projektet än. Skapa en här: välj ämne nedan och tryck Skapa rapport. (Du kan också låta en AI-assistent skapa den via MCP, se Nycklar.)");
      return;
    }
    await ladda(forsta.rapport_id, lista);
  })();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      stoppaTimer();
      ac.abort();
    },
  };
}
