/*
 * Lagerpanelens modell: grupper, lager, synlighet, opacitet, ordning och hopfällning, sparat per
 * projekt i webbläsarens lagring.
 *
 * Upplägget (grupper med lager som kan tändas, tonas, ordnas och fällas ihop) är O-Tids (MIT,
 * joelberring/otid @ 4a446af, apps/web/src/components/map-project/map-project-layers-panel.tsx,
 * map-project-work-layers.tsx och lib/map-project-layer-model.ts). Här är modellen ren
 * TypeScript utan ramverk. Lagringen är injicerbar och allt omges av try/catch: går det inte att
 * spara (privat läge, full lagring) fungerar kartan ändå.
 *
 * Ett lager kan vara en egen vy (`egenVy`): då visas det inte som ett MapLibre-lager utan monteras i
 * kartytan i stället för kartan, med en knapp i panelen för att byta. Så är 3D-vyn (Three.js, se
 * `vy3d/`) ett lager i samma panel. Ett lager kan höra till bara 2D eller bara 3D (`visning`): panelen
 * visar de lager som gäller för den vy som är aktiv.
 */

export type GruppId = "underlag" | "geometri" | "planforslag" | "resultat";

/** Grupperna i panelens ordning (uppifrån). Kartans staplingsordning är en annan, se kartmotor.ts. */
export const GRUPPER: ReadonlyArray<{ id: GruppId; namn: string; hjalp: string }> = [
  { id: "underlag", namn: "Underlag", hjalp: "Bakgrund, ortofoto och kartor från andra tjänster." },
  { id: "geometri", namn: "Geometri", hjalp: "Marken, byggnaderna och växtligheten som beräkningarna utgår från." },
  { id: "planforslag", namn: "Planförslag", hjalp: "Ändringarna du ritar, ett lager per förslag." },
  { id: "resultat", namn: "Resultat", hjalp: "Före, efter och skillnad från en beräkning." },
];

/** En egen vy som kan ersätta kartan (t.ex. en 3D-vy). `montera` får ytan och lämnar en avmonterare. */
export interface EgenVy {
  id: string;
  namn: string;
  /** Knapptext i panelen när vyn är av respektive på. */
  knapp?: { av: string; pa: string };
  montera(yta: HTMLElement): { avmontera(): void; uppdatera?(): void };
}

/** Vilken vy ett lager gäller för; utan värde gäller det båda. */
export type Visning = "2d" | "3d";

export interface LagerDef {
  id: string;
  grupp: GruppId;
  namn: string;
  synlig: boolean;
  opacitet: number;
  /** Förklaring under namnet (kort). */
  hjalp?: string;
  /** Visas i panelen men går inte att tända (t.ex. ortofoto som saknas lokalt); skälet står här. */
  ejTillgangligt?: string;
  /** Kan tas bort av användaren (egna underlag). */
  kanTasBort?: boolean;
  /** Visas som en egen vy i stället för som kartlager. */
  egenVy?: EgenVy;
  /** Bara i 2D-kartan eller bara i 3D-vyn; utan värde i båda. */
  visning?: Visning;
  /** Fri nyttolast för kartan (t.ex. adress till bild, tjänst, lagernamn). */
  data?: Record<string, unknown>;
}

interface Sparat {
  version: 1;
  lager: Array<{ id: string; synlig: boolean; opacitet: number }>;
  /** Ordning inom varje grupp (lager-id). */
  ordning: Partial<Record<GruppId, string[]>>;
  hopfallda: GruppId[];
  aktivVy: string | null;
}

export interface Lagring {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
}

const NYCKEL = "floden.kartlager.v1.";

function webbLagring(): Lagring | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export class LagerLista {
  private lager: LagerDef[] = [];
  private hopfallda = new Set<GruppId>();
  private aktivVyId: string | null = null;
  private aktuellVisning: Visning = "2d";
  private sparat: Sparat | null = null;
  private lyssnare: Array<() => void> = [];

  private readonly projekt: string;
  private readonly lagring: Lagring | null;

  constructor(projekt: string, lagring: Lagring | null = webbLagring()) {
    this.projekt = projekt;
    this.lagring = lagring;
    this.sparat = this.las();
    if (this.sparat) this.hopfallda = new Set(this.sparat.hopfallda);
    this.aktivVyId = this.sparat?.aktivVy ?? null;
  }

  vidAndring(f: () => void): void {
    this.lyssnare.push(f);
  }

  private signalera(spara = true): void {
    if (spara) this.skriv();
    for (const f of this.lyssnare) f();
  }

  private las(): Sparat | null {
    try {
      const t = this.lagring?.getItem(NYCKEL + this.projekt);
      if (!t) return null;
      const d = JSON.parse(t) as Sparat;
      return d && d.version === 1 && Array.isArray(d.lager) ? d : null;
    } catch {
      return null;
    }
  }

  private skriv(): void {
    try {
      const ordning: Partial<Record<GruppId, string[]>> = {};
      for (const g of GRUPPER) ordning[g.id] = this.lager.filter((l) => l.grupp === g.id).map((l) => l.id);
      // Behåll det som sparats för lager som inte finns just nu (t.ex. ett scenario som inte laddats än).
      const gamla = (this.sparat?.lager ?? []).filter((s) => !this.lager.some((l) => l.id === s.id));
      const d: Sparat = {
        version: 1,
        lager: [...gamla, ...this.lager.map((l) => ({ id: l.id, synlig: l.synlig, opacitet: l.opacitet }))],
        ordning: { ...this.sparat?.ordning, ...ordning },
        hopfallda: [...this.hopfallda],
        aktivVy: this.aktivVyId,
      };
      this.sparat = d;
      this.lagring?.setItem(NYCKEL + this.projekt, JSON.stringify(d));
    } catch {
      /* Det går inte att spara; kartan fungerar ändå. */
    }
  }

  /** Lägger till eller ersätter ett lager. Sparade värden (synlighet, opacitet) går före förvalet. */
  registrera(def: LagerDef): void {
    const s = this.sparat?.lager.find((x) => x.id === def.id);
    const ny: LagerDef = { ...def };
    if (s && !def.ejTillgangligt) {
      ny.synlig = s.synlig;
      ny.opacitet = Math.min(1, Math.max(0, s.opacitet));
    }
    if (def.ejTillgangligt) ny.synlig = false;
    const i = this.lager.findIndex((l) => l.id === def.id);
    if (i >= 0) {
      // Ett omregistrerat lager behåller användarens val.
      const g = this.lager[i] as LagerDef;
      this.lager[i] = { ...ny, synlig: def.ejTillgangligt ? false : g.synlig, opacitet: g.opacitet };
    } else this.lager.push(ny);
    this.sorteraEfterSparat();
    this.signalera(false);
  }

  /** Tar bort ett lager (t.ex. ett borttaget scenario). */
  tabort(id: string): void {
    const n = this.lager.length;
    this.lager = this.lager.filter((l) => l.id !== id);
    if (this.aktivVyId === id) this.aktivVyId = null;
    if (this.lager.length !== n) this.signalera();
  }

  /** Tar bort alla lager i en grupp vars id börjar med `prefix` (t.ex. gamla resultat). */
  taBortMed(grupp: GruppId, prefix: string): void {
    const n = this.lager.length;
    this.lager = this.lager.filter((l) => !(l.grupp === grupp && l.id.startsWith(prefix)));
    if (this.lager.length !== n) this.signalera(false);
  }

  private sorteraEfterSparat(): void {
    const ord = this.sparat?.ordning ?? {};
    for (const g of GRUPPER) {
      const onskad = ord[g.id] ?? [];
      const iG = this.lager.filter((l) => l.grupp === g.id);
      const sorterad = [...iG].sort((a, b) => {
        const x = onskad.indexOf(a.id);
        const y = onskad.indexOf(b.id);
        return (x < 0 ? 1e6 : x) - (y < 0 ? 1e6 : y);
      });
      let k = 0;
      this.lager = this.lager.map((l) => (l.grupp === g.id ? (sorterad[k++] as LagerDef) : l));
    }
  }

  alla(): readonly LagerDef[] {
    return this.lager;
  }

  /** Lagren i en grupp för den aktiva vyn, översta först (som i panelen). */
  iGrupp(g: GruppId): LagerDef[] {
    return this.lager.filter((l) => l.grupp === g && (!l.visning || l.visning === this.aktuellVisning));
  }

  get visning(): Visning {
    return this.aktuellVisning;
  }

  /** Byter vad panelen visar (2D eller 3D). Ger true om något ändrades. Signalerar inte: anroparen ritar om panelen. */
  sattVisning(v: Visning): boolean {
    if (v === this.aktuellVisning) return false;
    this.aktuellVisning = v;
    return true;
  }

  hamta(id: string): LagerDef | undefined {
    return this.lager.find((l) => l.id === id);
  }

  sattSynlig(id: string, synlig: boolean): void {
    const l = this.hamta(id);
    if (!l || l.ejTillgangligt) return;
    l.synlig = synlig;
    this.signalera();
  }

  sattOpacitet(id: string, o: number): void {
    const l = this.hamta(id);
    if (!l) return;
    l.opacitet = Math.min(1, Math.max(0, o));
    this.signalera();
  }

  /** Flyttar ett lager `steg` platser inom sin grupp (negativt = uppåt i panelen). */
  flytta(id: string, steg: number): void {
    const l = this.hamta(id);
    if (!l) return;
    const g = this.iGrupp(l.grupp);
    const fran = g.indexOf(l);
    const till = Math.min(g.length - 1, Math.max(0, fran + steg));
    this.flyttaTill(id, (g[till] as LagerDef).id, till > fran);
  }

  /** Flyttar `id` till platsen vid `mal` (före, eller efter om `efter`). Bara inom samma grupp (dra och släpp). */
  flyttaTill(id: string, mal: string, efter = false): void {
    const a = this.hamta(id);
    const b = this.hamta(mal);
    if (!a || !b || a === b || a.grupp !== b.grupp) return;
    const utan = this.lager.filter((l) => l !== a);
    const i = utan.indexOf(b);
    utan.splice(efter ? i + 1 : i, 0, a);
    this.lager = utan;
    this.signalera();
  }

  arHopfalld(g: GruppId): boolean {
    return this.hopfallda.has(g);
  }

  vaxlaHopfalld(g: GruppId): void {
    if (this.hopfallda.has(g)) this.hopfallda.delete(g);
    else this.hopfallda.add(g);
    this.signalera();
  }

  /** Den egna vyn som visas i stället för kartan, eller null för kartan. */
  get aktivVy(): LagerDef | null {
    const l = this.aktivVyId ? this.hamta(this.aktivVyId) : undefined;
    return l?.egenVy && l.synlig ? l : null;
  }

  sattAktivVy(id: string | null): void {
    const l = id ? this.hamta(id) : undefined;
    this.aktivVyId = l?.egenVy ? id : null;
    this.signalera();
  }
}
