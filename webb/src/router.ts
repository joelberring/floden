/*
 * Enkel hash-router: #/ · #/oversikt · #/projekt/<id> · #/karta/<id> · #/forvaltning/<id>
 * Kartsidan är arbetsbänken i index.html (main.ts) och göms i stället för att ritas om,
 * så att kartan och pågående ritningar finns kvar mellan sidbyten.
 */
import { laddaKonto, vidKontoandring } from "./konto";
import { hamtaModuler, hamtaOmraden } from "./api";
import { visaFragor } from "./fragor";
import {
  sidaForvaltning,
  sidaOversikt,
  sidaProjekt,
  sidaStart,
  sidhuvudApp,
  sidhuvudStart,
  type SidId,
} from "./sidor";
import type { ModulInfo, OmradeSammanfattning } from "./types";

export interface Rutt {
  sida: SidId;
  id: string | null;
  parametrar: URLSearchParams;
}

export interface RouterKrokar {
  /** Kartsidan visas; `omradeId` är null när adressen inte anger något område. */
  visaKarta(omradeId: string | null): void;
  /** Modulerna är hämtade (för modulväxlaren på kartsidan). */
  modulerKlara?(moduler: ModulInfo[]): void;
}

export function tolkaHash(hash: string): Rutt {
  const utan = hash.replace(/^#/, "");
  const [sokvag = "", fraga = ""] = utan.split("?", 2);
  const delar = sokvag.split("/").filter((d) => d.length > 0);
  const parametrar = new URLSearchParams(fraga);
  const sidor: Record<string, SidId> = {
    oversikt: "oversikt",
    projekt: "projekt",
    karta: "karta",
    fragor: "fragor",
    forvaltning: "forvaltning",
  };
  const sida = delar[0] ? sidor[delar[0]] : undefined;
  if (!sida) {
    return { sida: "start", id: null, parametrar };
  }
  return { sida, id: delar[1] ? decodeURIComponent(delar[1]) : null, parametrar };
}

const SIDTITLAR: Record<SidId, string> = {
  start: "Floden – floden av data och utredningar",
  oversikt: "Översikt – Floden",
  projekt: "Projekt – Floden",
  karta: "Karta – Floden",
  fragor: "Frågor – Floden",
  forvaltning: "Förvaltning – Floden",
};

export function startaRouter(krokar: RouterKrokar): void {
  const sidhuvud = document.getElementById("sidhuvud");
  const vySida = document.getElementById("vy-sida");
  const vyKarta = document.getElementById("vy-karta");
  if (!sidhuvud || !vySida || !vyKarta) {
    throw new Error("Sidskalet saknas i index.html (sidhuvud, vy-sida, vy-karta).");
  }

  let moduler: ModulInfo[] = [];
  let omraden: OmradeSammanfattning[] = [];
  let senasteOmrade: string | null = null;
  const data = Promise.all([
    hamtaModuler()
      .then((s) => {
        moduler = s.moduler;
        krokar.modulerKlara?.(moduler);
      })
      .catch(() => undefined),
    hamtaOmraden()
      .then((s) => {
        omraden = s.omraden;
      })
      .catch(() => undefined),
  ]);

  const ritaHuvud = (): void => {
    const rutt = tolkaHash(window.location.hash);
    sidhuvud.innerHTML =
      rutt.sida === "start" ? sidhuvudStart() : sidhuvudApp(rutt.sida, senasteOmrade);
  };
  // Inloggningsläget kommer från /konto/jag: bara sidhuvudet (och startsidan) ritas om.
  vidKontoandring(() => {
    ritaHuvud();
    if (tolkaHash(window.location.hash).sida === "start") {
      vySida.innerHTML = sidaStart(moduler);
    }
  });
  void laddaKonto();

  const rita = (): void => {
    const rutt = tolkaHash(window.location.hash);
    const omradeId = rutt.id ?? senasteOmrade ?? omraden[0]?.omrade_id ?? null;
    if (rutt.sida === "projekt" || rutt.sida === "karta") {
      senasteOmrade = omradeId;
    }
    document.title = SIDTITLAR[rutt.sida];
    document.body.dataset.sida = rutt.sida;
    sidhuvud.innerHTML =
      rutt.sida === "start" ? sidhuvudStart() : sidhuvudApp(rutt.sida, senasteOmrade);

    if (rutt.sida === "karta") {
      vySida.hidden = true;
      vySida.innerHTML = "";
      vyKarta.hidden = false;
      const tillbaka = document.getElementById("karta-tillbaka");
      if (tillbaka instanceof HTMLAnchorElement && omradeId) {
        tillbaka.href = `#/projekt/${encodeURIComponent(omradeId)}`;
      }
      krokar.visaKarta(rutt.id);
      // MapLibre behöver mäta om sin behållare när den blir synlig.
      window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
      return;
    }

    vyKarta.hidden = true;
    vySida.hidden = false;
    if (rutt.sida === "fragor") {
      // Frågesidan håller sitt eget tillstånd och ritas inte om vid adressbyte.
      visaFragor(vySida, rutt.id);
      window.scrollTo(0, 0);
      return;
    }
    switch (rutt.sida) {
      case "start":
        vySida.innerHTML = sidaStart(moduler);
        break;
      case "oversikt":
        vySida.innerHTML = sidaOversikt(moduler, omraden);
        break;
      case "projekt":
        vySida.innerHTML = sidaProjekt(
          moduler,
          omraden.find((o) => o.omrade_id === omradeId),
          rutt.parametrar.get("modul") ?? "vatten",
        );
        break;
      case "forvaltning":
        vySida.innerHTML = sidaForvaltning();
        break;
    }
    const till = rutt.parametrar.get("till");
    const mal = till ? document.getElementById(till) : null;
    if (mal) {
      mal.scrollIntoView();
    } else {
      window.scrollTo(0, 0);
    }
  };

  // Hoppa-till-länken och ankarlänkar på startsidan ska inte byta sida.
  document.addEventListener("click", (h) => {
    const mal = h.target instanceof Element ? h.target.closest("a") : null;
    if (!mal) return;
    if (mal.classList.contains("hoppa-till")) {
      h.preventDefault();
      const innehall = vyKarta.hidden ? vySida : document.getElementById("karta-huvudinnehall");
      innehall?.focus();
    }
  });

  window.addEventListener("hashchange", rita);
  rita();
  // Sidorna ritas om när modul- och områdeslistan har kommit (karta ritas inte om).
  void data.then(() => {
    const sida = tolkaHash(window.location.hash).sida;
    if (sida !== "karta" && sida !== "fragor") {
      rita();
    }
  });
}

export function renderaModulVaxlare(behallare: HTMLElement, moduler: ModulInfo[]): void {
  behallare.innerHTML = "";
  for (const m of moduler) {
    const knapp = document.createElement("button");
    knapp.type = "button";
    knapp.className = "modul-knapp";
    knapp.textContent = m.namn;
    const iDrift = m.status === "i_drift";
    knapp.disabled = !iDrift;
    knapp.setAttribute("aria-pressed", m.modul_id === "vatten" ? "true" : "false");
    if (!iDrift) {
      knapp.title = `${m.namn} kommer i en senare version`;
    }
    behallare.append(knapp);
  }
}
