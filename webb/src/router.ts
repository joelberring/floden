/*
 * Enkel hash-router. Ett skal: flödets sidhuvud (flode/huvud.ts) på alla sidor, stegraden bara i ett projekt.
 *   Flödet:   #/ (projektlistan) · #/nytt · #/p/<projekt>/<steg>
 *   Sidor:    #/fragor (frågekatalogen) · #/om · #/matematik · #/forvaltning (bara med inloggning och kontodata) ·
 *             #/nycklar (inloggade) · #/administration (admin) · #/prov/webgpu · #/arbetsbank/<omrade>
 *   Omdirigeras: #/karta, #/projekt/<id>, #/oversikt, #/fragor/<ref> och okända adresser (se `omdirigering` i rutter.ts).
 * Arbetsbänken är den äldre kartan i index.html (main.ts) och göms i stället för att ritas om,
 * så att kartan finns kvar mellan sidbyten. Den finns inte i menyn eller på någon sida: den nås
 * bara med adressen och visas bara för superadmin, eller alltid när inloggning är av (lokalt).
 */
import "./fragor.css"; // delade stilar (tabeller, lagerlistor, teckenförklaring) som arbetsbänken, provsidan och kontosidorna använder
import {
  adminTillaten,
  arbetsbankTillaten,
  forvaltningSynlig,
  inloggningsAdress,
  kontoLage,
  laddaKonto,
  vidKontoandring,
} from "./konto";
import { hamtaModuler, hamtaOmraden } from "./api";
import { avslutaFlode, projektLaddat, tolkaFlodeAdress, visaFlode, visaSkalsida } from "./flode/index";
import { visaFragekatalog } from "./flode/fragekatalog";
import { ritaFlodeHuvud, type SkalSida } from "./flode/huvud";
import { visaMatematik } from "./matematik/sida";
import { avslutaProv, visaProvWebgpu } from "./prov/webgpuProv";
import { visaAdministration, visaNycklar } from "./kontosidor";
import { kopplaOm, sidaOmFloden } from "./om/sida";
import { sidaForvaltning, sidaInloggning } from "./sidor";
import { omdirigering, SIDTITLAR, tolkaHash, type RuttSida } from "./rutter";
import type { ModulInfo, OmradeSammanfattning } from "./types";

export interface RouterKrokar {
  /** Arbetsbänken visas; `omradeId` är null när adressen inte anger något område. */
  visaArbetsbank(omradeId: string | null): void;
}

/** Sidor som inte beror på kontosvaret och därför inte ritas om när det kommer. */
const EGET_TILLSTAND: ReadonlySet<RuttSida> = new Set<RuttSida>(["fragor", "arbetsbank", "prov"]);

export function startaRouter(krokar: RouterKrokar): void {
  const sidhuvudEl = document.getElementById("sidhuvud");
  const vySida = document.getElementById("vy-sida");
  const vyArbetsbank = document.getElementById("vy-karta");
  if (!sidhuvudEl || !vySida || !vyArbetsbank) {
    throw new Error("Sidskalet saknas i index.html (sidhuvud, vy-sida, vy-karta).");
  }

  let moduler: ModulInfo[] = [];
  let omraden: OmradeSammanfattning[] = [];
  const data = Promise.all([
    hamtaModuler()
      .then((s) => {
        moduler = s.moduler;
      })
      .catch(() => undefined),
    hamtaOmraden()
      .then((s) => {
        omraden = s.omraden;
      })
      .catch(() => undefined),
  ]);

  const rita = (): void => {
    // Äldre adresser och okända adresser leder till motsvarande plats i flödet (se `omdirigering`).
    const mal = omdirigering(window.location.hash);
    if (mal) {
      window.location.replace(mal);
      return;
    }
    const rutt = tolkaHash(window.location.hash);
    document.title = SIDTITLAR[rutt.sida];
    document.body.dataset.sida = rutt.sida;

    // Utloggad på en server med inloggning: bara inloggningssidan, oavsett adress.
    if (kontoLage().lage === "ut") {
      avslutaFlode();
      document.title = "Floden";
      document.body.dataset.sida = "inloggning";
      sidhuvudEl.innerHTML = "";
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      vySida.innerHTML = sidaInloggning(inloggningsAdress("/"));
      return;
    }
    // Flödet (#/ och #/p/<projekt>/<steg>) har egna steg och stegrad.
    const flode = tolkaFlodeAdress(window.location.hash);
    if (flode) {
      document.body.dataset.sida = "flode";
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      visaFlode(sidhuvudEl, vySida, flode);
      return;
    }
    if (rutt.sida !== "prov") avslutaProv();
    // Allt annat har samma skal som flödet: samma stil och samma sidhuvud, men ingen stegrad.
    document.body.dataset.sida = "flode";
    const sida = rutt.sida as SkalSida;

    if (rutt.sida === "matematik") {
      avslutaFlode();
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      visaMatematik(sidhuvudEl, vySida);
      window.scrollTo(0, 0);
      return;
    }

    const fokuseraRubrik = (): void => {
      window.requestAnimationFrame(() => {
        const h1 = vySida.querySelector<HTMLElement>("h1");
        if (h1) {
          if (!h1.hasAttribute("tabindex")) h1.setAttribute("tabindex", "-1");
          h1.focus({ preventScroll: true });
        }
      });
    };

    if (rutt.sida === "forvaltning" && kontoLage().lage !== "okand" && !forvaltningSynlig()) {
      window.location.replace("#/");
      return;
    }
    // Sidor med behörighetskrav väntar på /konto/jag och går annars tillbaka till projektlistan.
    const lage = kontoLage();
    const kravs: Partial<Record<RuttSida, () => boolean>> = {
      arbetsbank: arbetsbankTillaten,
      administration: adminTillaten,
    };
    const krav = kravs[rutt.sida];
    if (krav && lage.lage !== "okand" && !krav()) {
      window.location.replace("#/");
      return;
    }
    if (rutt.sida !== "fragor") visaSkalsida(sidhuvudEl, vySida, sida);
    if (krav && lage.lage === "okand") {
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      vySida.innerHTML = "";
      return;
    }

    if (rutt.sida === "arbetsbank") {
      vySida.hidden = true;
      vySida.innerHTML = "";
      vyArbetsbank.hidden = false;
      krokar.visaArbetsbank(rutt.id);
      // MapLibre behöver mäta om sin behållare när den blir synlig.
      window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
      return;
    }

    vyArbetsbank.hidden = true;
    vySida.hidden = false;
    if (rutt.sida === "fragor") {
      // Frågekatalogen håller sitt eget tillstånd.
      visaSkalsida(sidhuvudEl, vySida, "fragor", visaFragekatalog);
      window.scrollTo(0, 0);
      return;
    }
    if (rutt.sida === "prov") {
      // Provsidan håller sitt eget tillstånd; den ritas inte om vid andra sidbyten.
      void visaProvWebgpu(vySida);
      window.scrollTo(0, 0);
      fokuseraRubrik();
      return;
    }
    switch (rutt.sida) {
      case "nycklar":
        void visaNycklar(vySida);
        break;
      case "administration":
        void visaAdministration(vySida);
        break;
      case "om":
        vySida.innerHTML = sidaOmFloden(moduler, omraden);
        kopplaOm(vySida);
        break;
      case "forvaltning":
        vySida.innerHTML = kontoLage().lage === "okand" ? "" : sidaForvaltning();
        break;
    }
    window.scrollTo(0, 0);
    fokuseraRubrik();
  };

  // Inloggningsläget kommer från /konto/jag och kan ändra menyn och förvaltningssidan.
  vidKontoandring(() => {
    const flode = tolkaFlodeAdress(window.location.hash);
    if (flode && kontoLage().lage !== "ut") {
      projektLaddat(sidhuvudEl, flode);
      return;
    }
    const sida = tolkaHash(window.location.hash).sida;
    if (kontoLage().lage === "ut" || !EGET_TILLSTAND.has(sida)) {
      rita();
    } else ritaFlodeHuvud(sidhuvudEl, null, null, sida as SkalSida);
  });
  void laddaKonto();

  document.addEventListener("click", (h) => {
    const mal = h.target instanceof Element ? h.target : null;
    const lank = mal?.closest("a");
    if (!lank?.classList.contains("hoppa-till")) return;
    h.preventDefault();
    const innehall = vyArbetsbank.hidden ? vySida : document.getElementById("karta-huvudinnehall");
    innehall?.focus();
  });

  window.addEventListener("hashchange", rita);
  rita();
  // Om Floden ritas om när moduler och områden har kommit.
  void data.then(() => {
    if (tolkaHash(window.location.hash).sida === "om" && !tolkaFlodeAdress(window.location.hash)) rita();
  });
}
