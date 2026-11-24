/*
 * Steg 3 · Frågor: frågorna per ämne med kryssrutor och statusetikett, och panelen Beställning
 * (skede, mottagare, syfte, sammanfattning) med knappen "Kör beräkningarna". Frågorna kommer ur
 * `/api/fragekatalog` om den finns, annars ur `/api/fragor`. Beställningen sparas bara i webbläsaren
 * (det finns inget API för den); körningen startas med de befintliga scenario-API:erna.
 */
import { hamtaFragor, lasJson } from "../api.ts";
import { hamtaProjektInfo, planeraScenario, type ProjektInfo } from "../karta/kartapi.ts";
import { esc } from "../sidor.ts";
import { flodeAdress } from "./adress.ts";
import { webGpuSkissStatus } from "../gpu/webgpuMotor.ts";
import { arPagaende, startaKorning, vidKorning } from "./korning.ts";
import type { Avslutbar } from "./projektlista.ts";
import { AMNEN, etikett, franK20, raknaPerAmne, tolkaKatalog, type FlodeFraga } from "./status.ts";
import { lasAvtappning, MOTTAGARE, sparaTillstand, tillstandFor, type Skede } from "./tillstand.ts";

export interface FragelistaSvar {
  fragor: FlodeFraga[];
  kalla: "katalog" | "k20";
  anmarkning: string;
}

/** Katalogen om servern har den, annars K20-frågorna. */
export async function hamtaFragelista(): Promise<FragelistaSvar> {
  try {
    const kat = tolkaKatalog(await lasJson<unknown>("/api/fragekatalog"));
    if (kat && kat.length > 0) {
      // Katalogen har inga ekologifrågor än; de beräkningsfrågor som finns läggs till (skiss).
      const eko = await hamtaFragor().then((s) => franK20(s.fragor).filter((f) => f.amne === "ekologi" && !f.id.startsWith("api-"))).catch(() => []);
      return { fragor: [...kat, ...eko], kalla: "katalog", anmarkning: "Frågorna kommer ur frågekatalogen för Stockholm; ekologifrågorna (skiss) är beräkningsfrågor." };
    }
  } catch {
    /* Ingen katalog på servern: reserven nedan. */
  }
  const s = await hamtaFragor();
  return { fragor: franK20(s.fragor), kalla: "k20", anmarkning: "Frågekatalogen finns inte på servern än; här visas de beräkningsfrågor som finns." };
}

/** Analyserna (mall.analys) som valda frågor ger: frågornas domäner mot projektets förvalda analyser. */
export function analyserFor(info: ProjektInfo, valda: ReadonlyArray<FlodeFraga>): ProjektInfo["analyser"] {
  const doman = new Set(valda.flatMap((f) => f.doman));
  return info.analyser.filter((a) => doman.has(a.mall) && a.forval);
}

export function visaFragor(rot: HTMLElement, projekt: string): Avslutbar {
  const t = tillstandFor(projekt);
  let avslutad = false;
  const ac = new AbortController();
  let amne = "skyfall";
  let lista: FragelistaSvar | null = null;
  let info: ProjektInfo | null = null;
  let tidText = "–";
  let planNr = 0;

  rot.innerHTML = `
    <div class="flode-sida">
      <section class="flode-bred" aria-labelledby="fragor-rubrik">
        <div class="flode-rubrikrad">
          <h1 id="fragor-rubrik" class="flode-h">Vilka frågor ska besvaras?</h1>
          <div role="group" aria-label="Ämne" class="flode-chips" id="fr-amnen"></div>
        </div>
        <div class="flode-fraglista" id="fr-lista"><p class="flode-dampad">Hämtar frågorna …</p></div>
        <p class="flode-dampad" id="fr-not"></p>
      </section>
      <aside class="flode-panel" aria-labelledby="bestallning-rubrik">
        <h2 id="bestallning-rubrik" class="flode-h">Beställning</h2>
        <fieldset class="flode-fieldset">
          <legend>Skede</legend>
          <div class="flode-segment" id="fr-skede">
            <button type="button" data-skede="skiss">Skiss</button>
            <button type="button" data-skede="planunderlag">Planunderlag</button>
          </div>
          <p class="flode-liten flode-dampad" id="fr-skede-not" hidden>Rapporten för planunderlag finns inte än. Beräkningarna körs som vanligt.</p>
        </fieldset>
        <div class="flode-falt"><label for="fr-mottagare">Mottagare</label>
          <select id="fr-mottagare">${MOTTAGARE.map((m) => `<option value="${m.id}">${esc(m.namn)}</option>`).join("")}</select></div>
        <div class="flode-falt"><label for="fr-syfte">Syfte</label>
          <textarea id="fr-syfte" rows="3"></textarea></div>
        <details class="flode-detalj" id="fr-antaganden">
          <summary>Antaganden för skyfall</summary>
          <div class="flode-falt"><label for="fr-avtappning">Avtappning till ledningsnätet (mm/h, valfritt)</label>
            <input id="fr-avtappning" type="text" inputmode="decimal" placeholder="Tomt: ledningsnätet antas fullt (0 mm/h)" autocomplete="off" /></div>
          <p class="flode-fel flode-liten" id="fr-avtappning-fel" role="alert" hidden></p>
        </details>
        <dl class="flode-sammanfattning" id="fr-sammanfattning"></dl>
        <div class="flode-rad-slut">
          <p class="flode-liten flode-dampad" id="fr-kor-not" role="status"></p>
          <p class="flode-liten flode-dampad" id="fr-gpu"></p>
          <button type="button" class="flode-knapp svart stor" id="fr-kor">Kör beräkningarna</button>
        </div>
      </aside>
    </div>`;
  const lyssna = (m: HTMLElement, typ: string, f: (h: Event) => void): void => m.addEventListener(typ, f, { signal: ac.signal });
  const el = <T extends HTMLElement>(id: string): T => rot.querySelector(`#${id}`) as T;

  const valdaPoster = (): FlodeFraga[] => (lista?.fragor ?? []).filter((f) => t.valda?.has(f.id));

  const ritaAmnen = (): void => {
    const f = lista?.fragor ?? [];
    el("fr-amnen").innerHTML = AMNEN.map((a) => {
      const antal = raknaPerAmne(f, a.id);
      return `<button type="button" data-amne="${a.id}" aria-pressed="${a.id === amne}"${antal === 0 ? ' class="tom"' : ""}>${esc(a.namn)}${antal > 0 ? ` · ${antal}` : ""}</button>`;
    }).join("");
  };

  const ritaLista = (): void => {
    if (!lista) return;
    const rader = lista.fragor.filter((f) => f.amne === amne);
    el("fr-lista").innerHTML =
      rader.length === 0
        ? `<p class="flode-dampad flode-tomt">Inga frågor i det här ämnet än.</p>`
        : rader
            .map(
              (f) => `<label class="flode-fraga">
                <input type="checkbox" data-fraga="${esc(f.id)}"${t.valda?.has(f.id) ? " checked" : ""} />
                <span class="flode-fraga-text${f.niva === "saknas" ? " dampad" : ""}">${lista?.kalla === "katalog" ? `${esc(f.id)} ` : ""}${esc(f.text)}</span>
                <span class="flode-etikett ${f.niva}"${f.saknat ? ` title="${esc(f.saknat)}"` : ""}>${esc(etikett(f.niva))}</span>
              </label>`,
            )
            .join("");
    el("fr-not").textContent = `Visar ${rader.length} av ${lista.fragor.length}. ${lista.anmarkning}`;
  };

  const ritaBestallning = (): void => {
    const b = t.bestallning;
    for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-skede]"))) k.setAttribute("aria-pressed", String(k.dataset.skede === b.skede));
    el("fr-skede-not").hidden = b.skede !== "planunderlag";
    el<HTMLSelectElement>("fr-mottagare").value = b.mottagare;
    el<HTMLTextAreaElement>("fr-syfte").value = b.syfte;
    el<HTMLInputElement>("fr-avtappning").value = t.avtappning === null ? "" : String(t.avtappning).replace(".", ",");
    el("fr-antaganden").toggleAttribute("open", t.avtappning !== null);
    ritaSammanfattning();
  };

  const ritaSammanfattning = (): void => {
    const valda = valdaPoster();
    const analyser = info ? analyserFor(info, valda) : [];
    el("fr-sammanfattning").innerHTML = `
      <dt>Valda frågor</dt><dd>${valda.length}</dd>
      <dt>Beräkningar</dt><dd>${analyser.length > 0 ? esc(analyser.map((a) => a.namn).join(", ")) : "–"}</dd>
      <dt>Uppskattad tid</dt><dd>${esc(tidText)}</dd>`;
    const kor = el<HTMLButtonElement>("fr-kor");
    const nu = arPagaende(t);
    const not = el("fr-kor-not");
    let hinder = "";
    if (!t.scenario) hinder = `Rita först ett förslag i <a href="${flodeAdress(projekt, "forslag")}">steg 2</a>. Utan förslag visar Resultat projektets färdiga beräkningar.`;
    else if (analyser.length === 0) hinder = "Välj minst en fråga som Floden kan räkna på (skyfall eller dagsljus och sol).";
    if (avtappningFel) hinder = "Rätta avtappningen först.";
    kor.disabled = nu || hinder !== "";
    if (nu) not.textContent = t.korning?.text ?? "";
    else if (t.korning?.fel) not.innerHTML = `<span class="flode-fel" role="alert">${esc(t.korning.fel)}</span>`;
    else not.innerHTML = hinder;
    kor.textContent = nu ? "Beräknar …" : "Kör beräkningarna";
  };

  let avtappningFel = false;
  const uppskattaTid = async (): Promise<void> => {
    const nr = ++planNr;
    const analyser = info ? analyserFor(info, valdaPoster()) : [];
    if (!t.scenario || analyser.length === 0) {
      tidText = "–";
      ritaSammanfattning();
      return;
    }
    try {
      const p = await planeraScenario(projekt, t.scenario.slug, analyser.map((a) => a.id), t.avtappning);
      if (nr !== planNr || avslutad) return;
      const tider = p.fragor.map((f) => f.uppskattad_tid_s);
      tidText = tider.every((x): x is number => x !== null) ? (tider.reduce((a, b) => a + b, 0) === 0 ? "klart i cachen" : `ungefär ${Math.round(tider.reduce((a, b) => a + b, 0))} s`) : "–";
    } catch {
      tidText = "–";
    }
    if (!avslutad) ritaSammanfattning();
  };

  lyssna(rot, "click", (h) => {
    const m = (h.target as Element).closest<HTMLElement>("[data-amne],[data-skede],#fr-kor");
    if (!m) return;
    if (m.dataset.amne) {
      amne = m.dataset.amne;
      ritaAmnen();
      ritaLista();
    } else if (m.dataset.skede) {
      t.bestallning.skede = m.dataset.skede as Skede;
      sparaTillstand(t);
      ritaBestallning();
    } else if (m.id === "fr-kor" && info) {
      const analyser = analyserFor(info, valdaPoster()).map((a) => a.id);
      void startaKorning(t, analyser);
    }
  });
  lyssna(rot, "change", (h) => {
    const m = h.target as HTMLInputElement;
    if (m.dataset.fraga) {
      t.valda ??= new Set();
      if (m.checked) t.valda.add(m.dataset.fraga);
      else t.valda.delete(m.dataset.fraga);
      sparaTillstand(t);
      ritaSammanfattning();
      void uppskattaTid();
    } else if (m.id === "fr-avtappning") {
      const r = lasAvtappning(m.value);
      avtappningFel = !r.ok;
      const fel = el("fr-avtappning-fel");
      fel.hidden = r.ok;
      fel.textContent = r.ok ? "" : "Avtappningen måste vara ett tal som är minst 0 (eller tomt för 0).";
      if (r.ok) {
        t.avtappning = r.varde;
        sparaTillstand(t);
        void uppskattaTid();
      }
      ritaSammanfattning();
    } else if (m.id === "fr-mottagare") {
      t.bestallning.mottagare = m.value;
      sparaTillstand(t);
    }
  });
  el("fr-syfte").addEventListener("input", (h) => {
    t.bestallning.syfte = (h.target as HTMLTextAreaElement).value;
    sparaTillstand(t);
  });

  // Körningen följs här; när den är klar öppnas Resultat.
  let tidigare = t.korning?.klar ?? true;
  const avlyssna = vidKorning(() => {
    if (avslutad) return;
    ritaSammanfattning();
    const k = t.korning;
    if (k?.klar && !k.fel && !tidigare) window.location.hash = flodeAdress(projekt, "resultat");
    tidigare = k?.klar ?? true;
  });

  const klar = (async () => {
    try {
      [lista, info] = await Promise.all([hamtaFragelista(), hamtaProjektInfo(projekt)]);
    } catch (e) {
      el("fr-lista").innerHTML = `<p class="flode-fel" role="alert">Frågorna gick inte att hämta: ${esc(e instanceof Error ? e.message : String(e))}. Är servern igång?</p>`;
      ritaBestallning();
      return;
    }
    if (avslutad) return;
    if (t.valda === null) {
      t.valda = new Set(lista.fragor.filter((f) => f.niva !== "saknas" && f.doman.length > 0).map((f) => f.id));
      sparaTillstand(t);
    }
    if (!lista.fragor.some((f) => f.amne === amne)) amne = AMNEN.find((a) => raknaPerAmne(lista?.fragor ?? [], a.id) > 0)?.id ?? amne;
    ritaAmnen();
    ritaLista();
    ritaBestallning();
    void uppskattaTid();
  })();
  el("fr-gpu").textContent = webGpuSkissStatus().beskrivning;
  ritaBestallning();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      ac.abort();
      avlyssna();
    },
  };
}
