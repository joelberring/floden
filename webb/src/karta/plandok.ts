/*
 * Planförslaget som dokument: nya hus, ändrade höjder och borttagna byggnader, med ångra och
 * gör om. Dokumentet blir ett K9-scenario (ändringstyperna `byggnad_ny`, `byggnad_hojd`,
 * `byggnad_bort`); servern kontrollerar det med kärnans egen validering.
 *
 * Mönstret (objekt med nyckel, läge före och efter, grupp) är O-Tids (MIT, joelberring/otid @
 * 4a446af, packages/map-revision/src/history.ts och state.ts), se historik.ts.
 */
import { kopiaP, type P3 } from "./linje.ts";
import { franGeoRing, normaliseraRing, signeradYta, tillGeoRing, type Ring } from "./geom.ts";
import { registreraSteg, ta, tomHistorik, type Historik, type Steg } from "./historik.ts";
import { KONTRAKT_RITLAGER, kopiaLager, type Geometrityp, type RitLager, type RitObjekt, type RitlagerDok } from "./ritlager.ts";

export interface NyttHus {
  id: string;
  /** Öppen ring, moturs. */
  ring: Ring;
  hojd_m: number;
  /** Ritlagret (K50) huset ligger i; utan värde det första byggnadslagret. */
  lager?: string;
  /** Övriga egenskaper ur filen (t.ex. våningar, tak_rh2000) som bevaras vid sparande. */
  extra?: Record<string, unknown>;
}

/** En ritad linje eller yta i ett ritlager (K50) med annan roll än byggnad: mur, kant, öppning, ny mark, vatten ... */
export interface Figur {
  id: string;
  lager: string;
  typ: "linje" | "yta";
  /** Linje: punkterna i ordning. Yta: öppen ring, moturs. `z` (m RH 2000) är bara ett hjälpmedel i klienten; lutningen skickas i egenskaperna. */
  punkter: P3[];
  /** Egenskaper enligt rollen (K50), t.ex. `hojd_m` för en mur eller `niva_rh2000` för ny mark. */
  egenskaper: Record<string, unknown>;
}

export const kopiaFigur = (f: Figur): Figur => ({
  id: f.id,
  lager: f.lager,
  typ: f.typ,
  punkter: f.punkter.map(kopiaP),
  egenskaper: JSON.parse(JSON.stringify(f.egenskaper)) as Record<string, unknown>,
});

export type DokNyckel = `ny:${string}` | `hojd:${string}` | `bort:${string}` | `fig:${string}`;
type Lage = NyttHus | Figur | number | true;

/** En K9-ändring så som servern tar emot den. */
export interface K9Andring {
  /** Byggnadstyperna ritas här; andra K9-typer (t.ex. markhojd) bevaras oförändrade. */
  typ: string;
  andring_id: string;
  geometri: { type: "Polygon"; coordinates: number[][][] } | null;
  egenskaper: Record<string, unknown>;
}

const kopia = (h: NyttHus): NyttHus => {
  const k: NyttHus = { id: h.id, hojd_m: h.hojd_m, ring: h.ring.map((p) => ({ x: p.x, y: p.y })) };
  if (h.lager !== undefined) k.lager = h.lager;
  if (h.extra) k.extra = { ...h.extra };
  return k;
};

/** Linjen med punkterna avrundade till 1 cm och utan dubbletter efter varandra (som servern). `z` följer med (mm). */
export function normaliseraLinje(punkter: readonly P3[]): P3[] {
  const r = (v: number): number => Math.round(v * 100) / 100;
  const ut: P3[] = [];
  for (const p of punkter) {
    const q: P3 = { x: r(p.x), y: r(p.y) };
    if (p.z !== undefined) q.z = Math.round(p.z * 1000) / 1000;
    const s = ut[ut.length - 1];
    if (!s || s.x !== q.x || s.y !== q.y) ut.push(q);
  }
  return ut;
}

/** Ytans öppna ring moturs (som servern), med `z` per hörn kvar. */
export function normaliseraYta(punkter: readonly P3[]): P3[] {
  const ren = normaliseraLinje(punkter);
  const f = ren[0];
  const s = ren[ren.length - 1];
  if (ren.length > 1 && f && s && f.x === s.x && f.y === s.y) ren.pop();
  if (ren.length >= 3 && signeradYta(ren) < 0) return [ren[0] as P3, ...ren.slice(1).reverse()];
  return ren;
}

export class PlanDok {
  readonly ny = new Map<string, NyttHus>();
  readonly hojd = new Map<string, number>();
  readonly bort = new Set<string>();
  /** Ritade linjer och ytor (R3) i lager med annan roll än byggnad. */
  readonly figurer = new Map<string, Figur>();
  /** Ritlagren (K50). Tomt tills de satts (förvalda lager ur projektinfo, eller förslagets egna). Inte en del av ångra. */
  lager: RitLager[] = [];
  /** Lagret nya hus ritas i. */
  aktivtLager: string | null = null;
  /** Ritade objekt i lager som klienten inte kan redigera än (andra roller än byggnad): bevaras oförändrade. */
  private ovrigaObjekt: RitObjekt[] = [];
  /** K9-ändringar klienten inte känner igen (t.ex. markhojd ur en fil): bevaras oförändrade. */
  private ovrigaAndringar: K9Andring[] = [];
  private historik: Historik<DokNyckel, Lage> = tomHistorik();
  private version = 0;
  private lyssnare: Array<() => void> = [];

  /** `basHojd` ger en befintlig byggnads höjd (för att ta bort en höjdändring som inte ändrar något). */
  private readonly basHojd: (id: string) => number | undefined;

  constructor(basHojd: (id: string) => number | undefined = () => undefined) {
    this.basHojd = basHojd;
  }

  vidAndring(f: () => void): void {
    this.lyssnare.push(f);
  }

  get andringsNr(): number {
    return this.version;
  }

  private signalera(): void {
    this.version++;
    for (const f of this.lyssnare) f();
  }

  get(nyckel: DokNyckel): Lage | null {
    const [typ, id = ""] = [nyckel.slice(0, nyckel.indexOf(":")), nyckel.slice(nyckel.indexOf(":") + 1)];
    if (typ === "ny") {
      const h = this.ny.get(id);
      return h ? kopia(h) : null;
    }
    if (typ === "fig") {
      const f = this.figurer.get(id);
      return f ? kopiaFigur(f) : null;
    }
    if (typ === "hojd") return this.hojd.get(id) ?? null;
    return this.bort.has(id) ? true : null;
  }

  private satt(nyckel: DokNyckel, lage: Lage | null): void {
    const i = nyckel.indexOf(":");
    const typ = nyckel.slice(0, i);
    const id = nyckel.slice(i + 1);
    if (typ === "ny") {
      if (lage === null) this.ny.delete(id);
      else this.ny.set(id, kopia(lage as NyttHus));
    } else if (typ === "fig") {
      if (lage === null) this.figurer.delete(id);
      else this.figurer.set(id, kopiaFigur(lage as Figur));
    } else if (typ === "hojd") {
      if (lage === null) this.hojd.delete(id);
      else this.hojd.set(id, lage as number);
    } else if (lage === null) this.bort.delete(id);
    else this.bort.add(id);
  }

  /** Ändrar ett objekt och sparar steget (läget före och efter) i historiken. */
  andra(nyckel: DokNyckel, efter: Lage | null, grupp?: string): void {
    const fore = this.get(nyckel);
    if (JSON.stringify(fore) === JSON.stringify(efter)) return;
    this.satt(nyckel, efter);
    const steg: Steg<DokNyckel, Lage> = { key: nyckel, before: fore, after: efter === null ? null : efter, group: grupp };
    this.historik = registreraSteg(this.historik, [steg]);
    this.signalera();
  }

  /** Flera ändringar som ett steg för ångra. */
  grupp(namn: string, f: () => void): void {
    const gammal = this.pagaende;
    this.pagaende = namn;
    try {
      f();
    } finally {
      this.pagaende = gammal;
    }
  }
  private pagaende: string | undefined;

  /** Ändring som respekterar en pågående grupp. */
  satta(nyckel: DokNyckel, efter: Lage | null): void {
    this.andra(nyckel, efter, this.pagaende);
  }

  kanAngra(): boolean {
    return this.historik.undo.length > 0;
  }

  kanGoraOm(): boolean {
    return this.historik.redo.length > 0;
  }

  /** Ångrar senaste steget (eller gruppen). Returnerar de ändrade nycklarna. */
  angra(): DokNyckel[] {
    return this.stega("undo");
  }

  goraOm(): DokNyckel[] {
    return this.stega("redo");
  }

  private stega(riktning: "undo" | "redo"): DokNyckel[] {
    const r = ta(this.historik as Historik<string, unknown>, riktning);
    if (r.steg.length === 0) return [];
    this.historik = r.historik as Historik<DokNyckel, Lage>;
    for (const s of r.steg) this.satt(s.key as DokNyckel, (riktning === "undo" ? s.before : s.after) as Lage | null);
    this.signalera();
    return r.steg.map((s) => s.key as DokNyckel);
  }

  /** Nästa lediga id: `nytt_hus`, `nytt_hus_2`, ... */
  nyttId(): string {
    if (!this.ny.has("nytt_hus")) return "nytt_hus";
    let n = 2;
    while (this.ny.has(`nytt_hus_${n}`)) n++;
    return `nytt_hus_${n}`;
  }

  /** Lägger till ett nytt hus (ring moturs, avrundad) och returnerar det. */
  laggTillHus(ring: Ring, hojd: number, id = this.nyttId(), lager: string | undefined = this.aktivtHusLager()?.lager_id): NyttHus {
    const h: NyttHus = { id, ring: normaliseraRing(ring), hojd_m: hojd };
    if (lager !== undefined) h.lager = lager;
    this.satta(`ny:${id}`, h);
    return h;
  }

  sattHojd(husId: string, hojd: number, ny: boolean): void {
    if (ny) {
      const h = this.ny.get(husId);
      if (h) this.satta(`ny:${husId}`, { ...kopia(h), hojd_m: hojd });
      return;
    }
    const bas = this.basHojd(husId);
    this.satta(`hojd:${husId}`, bas !== undefined && Math.abs(bas - hojd) < 1e-9 ? null : hojd);
  }

  sattRing(husId: string, ring: Ring): void {
    const h = this.ny.get(husId);
    if (h) this.satta(`ny:${husId}`, { ...kopia(h), ring: normaliseraRing(ring) });
  }

  taBortNyttHus(husId: string): void {
    this.satta(`ny:${husId}`, null);
  }

  /** Tar bort en befintlig byggnad (rivs) eller ångrar rivningen. */
  sattBort(husId: string, bort: boolean): void {
    this.grupp(`bort-${husId}`, () => {
      this.satta(`bort:${husId}`, bort ? true : null);
      if (bort) this.satta(`hojd:${husId}`, null); // en riven byggnad behöver ingen ny höjd
    });
  }

  tom(): boolean {
    return this.ny.size === 0 && this.figurer.size === 0 && this.hojd.size === 0 && this.bort.size === 0 && this.ovrigaObjekt.length === 0 && this.ovrigaAndringar.length === 0;
  }

  // ------------------------------------------------------------------ linjer och ytor (R3)

  /** Nästa lediga id för en linje eller yta: `linje_1`, `yta_1`, ... (K50 `objekt_id`). */
  nyttFigurId(typ: "linje" | "yta"): string {
    let n = 1;
    while (this.figurer.has(`${typ}_${n}`) || this.ny.has(`${typ}_${n}`)) n++;
    return `${typ}_${n}`;
  }

  /** Lagret en figur ligger i. */
  lagerForFigur(f: Figur): RitLager | undefined {
    return this.lagerAv(f.lager);
  }

  arFigurSynlig(f: Figur): boolean {
    return this.lagerForFigur(f)?.synlig ?? true;
  }

  /** Figuren går att markera och ändra om dess lager är tänt och olåst. */
  arFigurRedigerbar(f: Figur): boolean {
    const l = this.lagerForFigur(f);
    return !l || (l.synlig && !l.last);
  }

  /**
   * Lagret en ny linje (`linje`) eller yta (`polygon`) ritas i: det valda om det passar (rätt geometri,
   * inte byggnad för ytor, tänt och olåst), annars det första som passar, som då blir valt.
   * Undefined när inget lager passar.
   */
  lagerForRitning(geometri: Geometrityp): RitLager | undefined {
    const passar = (l: RitLager): boolean => l.geometri === geometri && l.roll !== "byggnad" && l.synlig && !l.last;
    const valt = this.lagerAv(this.aktivtLager);
    if (valt && passar(valt)) return valt;
    const forsta = this.lager.find(passar);
    if (forsta) this.aktivtLager = forsta.lager_id;
    return forsta;
  }

  /** Lägger till en ny linje eller yta i ett lager (förvalda egenskaper ur lagrets förval). */
  laggTillFigur(typ: "linje" | "yta", punkter: readonly P3[], lager: string, egenskaper: Record<string, unknown> = {}, id = this.nyttFigurId(typ)): Figur {
    const f: Figur = {
      id,
      lager,
      typ,
      punkter: typ === "yta" ? normaliseraYta(punkter) : normaliseraLinje(punkter),
      egenskaper: { ...egenskaper },
    };
    this.satta(`fig:${id}`, f);
    return f;
  }

  sattFigurPunkter(id: string, punkter: readonly P3[]): void {
    const f = this.figurer.get(id);
    if (f) this.satta(`fig:${id}`, { ...kopiaFigur(f), punkter: f.typ === "yta" ? normaliseraYta(punkter) : normaliseraLinje(punkter) });
  }

  /** Sätter egenskaper på en figur. `null` som värde tar bort egenskapen. */
  sattFigurEgenskaper(id: string, egenskaper: Record<string, unknown>): void {
    const f = this.figurer.get(id);
    if (!f) return;
    const ny = { ...f.egenskaper };
    for (const [k, v] of Object.entries(egenskaper)) {
      if (v === null || v === undefined) delete ny[k];
      else ny[k] = v;
    }
    this.satta(`fig:${id}`, { ...kopiaFigur(f), egenskaper: ny });
  }

  taBortFigur(id: string): void {
    this.satta(`fig:${id}`, null);
  }

  /** Delar en linje i två (klipp): den första behåller id, den andra blir en ny figur med samma lager och egenskaper. Ett steg för ångra. */
  delaFigur(id: string, ett: readonly P3[], tva: readonly P3[]): Figur | undefined {
    const f = this.figurer.get(id);
    if (!f || f.typ !== "linje") return undefined;
    let ny: Figur | undefined;
    this.grupp(`dela-${id}`, () => {
      this.sattFigurPunkter(id, ett);
      ny = this.laggTillFigur("linje", tva, f.lager, f.egenskaper);
    });
    return ny;
  }

  /** Slår ihop två linjer i samma lager (slå ihop): den första får de nya punkterna, den andra tas bort. Ett steg för ångra. */
  slaIhopFigurer(forsta: string, andra: string, punkter: readonly P3[]): void {
    this.grupp(`ihop-${forsta}`, () => {
      this.sattFigurPunkter(forsta, punkter);
      this.taBortFigur(andra);
    });
  }

  // ------------------------------------------------------------------ ritlager (K50)

  /** Sätter lagren (förval eller förslagets egna). Behåller det aktiva lagret om det finns kvar. */
  sattLager(lista: readonly RitLager[]): void {
    this.lager = lista.map(kopiaLager);
    if (!this.lager.some((l) => l.lager_id === this.aktivtLager)) this.aktivtLager = this.forstaHusLager()?.lager_id ?? null;
    this.signalera();
  }

  lagerAv(id: string | null | undefined): RitLager | undefined {
    return id ? this.lager.find((l) => l.lager_id === id) : undefined;
  }

  private forstaHusLager(): RitLager | undefined {
    return this.lager.find((l) => l.roll === "byggnad" && l.synlig && !l.last) ?? this.lager.find((l) => l.roll === "byggnad");
  }

  /** Lagret ett hus ligger i (utan angivet lager: det första byggnadslagret). */
  lagerForHus(h: NyttHus): RitLager | undefined {
    return this.lagerAv(h.lager) ?? this.lager.find((l) => l.roll === "byggnad");
  }

  /** Lagret ett nytt hus ritas i just nu, om det är ett tänt och olåst byggnadslager. */
  aktivtHusLager(): RitLager | undefined {
    const l = this.lagerAv(this.aktivtLager);
    return l && l.roll === "byggnad" && l.synlig && !l.last ? l : undefined;
  }

  /** Huset syns om dess lager är tänt (utan lagerlista syns allt). */
  arSynligt(h: NyttHus): boolean {
    return this.lagerForHus(h)?.synlig ?? true;
  }

  /** Huset går att markera och ändra om dess lager är tänt och olåst. */
  arRedigerbart(h: NyttHus): boolean {
    const l = this.lagerForHus(h);
    return !l || (l.synlig && !l.last);
  }

  sattAktivtLager(id: string): void {
    if (this.lagerAv(id) && this.aktivtLager !== id) {
      this.aktivtLager = id;
      this.signalera();
    }
  }

  andraLager(id: string, f: (l: RitLager) => void): void {
    const l = this.lagerAv(id);
    if (!l) return;
    f(l);
    this.signalera();
  }

  laggTillLager(l: RitLager): void {
    if (this.lagerAv(l.lager_id)) return;
    this.lager.push(kopiaLager(l));
    if (!this.aktivtLager && l.roll === "byggnad") this.aktivtLager = l.lager_id;
    this.signalera();
  }

  /** Antal objekt i ett lager (nya hus och bevarade objekt). */
  antalILager(id: string): number {
    let n = this.ovrigaObjekt.filter((o) => o.lager_id === id).length;
    for (const h of this.ny.values()) if (this.lagerForHus(h)?.lager_id === id) n++;
    for (const f of this.figurer.values()) if (f.lager === id) n++;
    return n;
  }

  /** Tar bort ett tomt lager. Returnerar false om det har objekt (de måste flyttas eller tas bort först). */
  taBortLager(id: string): boolean {
    if (this.antalILager(id) > 0 || !this.lagerAv(id)) return false;
    this.lager = this.lager.filter((l) => l.lager_id !== id);
    if (this.aktivtLager === id) this.aktivtLager = this.forstaHusLager()?.lager_id ?? null;
    this.signalera();
    return true;
  }

  /** K50-dokumentet för servern: lagren och alla ritade objekt, med husen i sina lager. `extra` ger egenskaper per hus (t.ex. takform). */
  tillRitlager(extra: (id: string) => Record<string, unknown> = () => ({})): RitlagerDok {
    const objekt: RitObjekt[] = [];
    for (const h of [...this.ny.values()].sort((a, b) => a.id.localeCompare(b.id))) {
      const lager = this.lagerForHus(h)?.lager_id ?? h.lager;
      if (!lager) continue;
      objekt.push({
        objekt_id: h.id,
        lager_id: lager,
        geometri: { type: "Polygon", coordinates: [tillGeoRing(normaliseraRing(h.ring))] },
        egenskaper: { ...h.extra, hojd_m: h.hojd_m, ...extra(h.id) },
      });
    }
    for (const f of [...this.figurer.values()].sort((a, b) => a.id.localeCompare(b.id))) {
      objekt.push({
        objekt_id: f.id,
        lager_id: f.lager,
        geometri:
          f.typ === "yta"
            ? { type: "Polygon", coordinates: [tillGeoRing(normaliseraYta(f.punkter))] }
            : { type: "LineString", coordinates: normaliseraLinje(f.punkter).map((p) => [p.x, p.y]) },
        egenskaper: { ...f.egenskaper },
      });
    }
    objekt.push(...this.ovrigaObjekt.map((o) => ({ ...o, egenskaper: { ...o.egenskaper } })));
    const dok: RitlagerDok = { kontrakt: KONTRAKT_RITLAGER, objekt };
    if (this.lager.length > 0) dok.lager = this.lager.map(kopiaLager);
    return dok;
  }

  /** K9-ändringar som inte kommer ur ritade objekt: höjdändring och rivning av befintliga byggnader, och bevarade okända. */
  tillFriaAndringar(): K9Andring[] {
    return this.tillAndringar().filter((a) => a.typ !== "byggnad_ny").concat(this.ovrigaAndringar.map((a) => ({ ...a })));
  }

  /** K9-ändringarna: nya hus, höjdändringar, borttagningar. Samma ordning varje gång (id). */
  tillAndringar(): K9Andring[] {
    const ut: K9Andring[] = [];
    for (const h of [...this.ny.values()].sort((a, b) => a.id.localeCompare(b.id))) {
      ut.push({
        typ: "byggnad_ny",
        andring_id: h.id,
        geometri: { type: "Polygon", coordinates: [tillGeoRing(normaliseraRing(h.ring))] },
        egenskaper: { ...h.extra, hojd_m: h.hojd_m },
      });
    }
    for (const [bid, hojd] of [...this.hojd.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      if (this.bort.has(bid)) continue;
      ut.push({ typ: "byggnad_hojd", andring_id: `hojd_${bid}`, geometri: null, egenskaper: { byggnads_id: bid, hojd_m: hojd } });
    }
    for (const bid of [...this.bort].sort()) {
      ut.push({ typ: "byggnad_bort", andring_id: `bort_${bid}`, geometri: null, egenskaper: { byggnads_id: bid } });
    }
    return ut;
  }

  /**
   * Ersätter dokumentet med K9-ändringar (laddat scenario). Historiken töms. Med `ritlager` (K50) kommer
   * husen ur objekten i sina lager och `andringar` är de fria ändringarna; utan hamnar husen i första byggnadslagret.
   */
  fran(andringar: ReadonlyArray<K9Andring>, ritlager?: RitlagerDok | null): void {
    this.ny.clear();
    this.hojd.clear();
    this.bort.clear();
    this.figurer.clear();
    this.ovrigaObjekt = [];
    this.ovrigaAndringar = [];
    this.historik = tomHistorik();
    if (ritlager?.lager && ritlager.lager.length > 0) {
      this.lager = ritlager.lager.map(kopiaLager);
      if (!this.lager.some((l) => l.lager_id === this.aktivtLager)) this.aktivtLager = this.forstaHusLager()?.lager_id ?? null;
    }
    const nyttHus = (id: string, geo: { coordinates: number[][][] }, e: Record<string, unknown>, lager?: string): void => {
      const { hojd_m, takform: _tak, ...extra } = e;
      void _tak;
      const h: NyttHus = { id, ring: franGeoRing(geo.coordinates[0] ?? []), hojd_m: Number(hojd_m) };
      if (lager !== undefined) h.lager = lager;
      if (Object.keys(extra).length > 0) h.extra = extra;
      this.ny.set(id, h);
    };
    for (const a of andringar) {
      const e = a.egenskaper;
      if (a.typ === "byggnad_ny" && a.geometri) {
        nyttHus(a.andring_id, a.geometri, e);
      } else if (a.typ === "byggnad_hojd") {
        this.hojd.set(String(e.byggnads_id), Number(e.hojd_m));
      } else if (a.typ === "byggnad_bort") {
        this.bort.add(String(e.byggnads_id));
      } else {
        this.ovrigaAndringar.push({ ...a });
      }
    }
    for (const o of ritlager?.objekt ?? []) {
      const roll = this.lagerAv(o.lager_id)?.roll;
      if (roll === "byggnad" && o.geometri.type === "Polygon") {
        nyttHus(o.objekt_id, o.geometri as { coordinates: number[][][] }, o.egenskaper, o.lager_id);
      } else if (roll !== undefined && roll !== "byggnad" && (o.geometri.type === "Polygon" || o.geometri.type === "LineString")) {
        const yta = o.geometri.type === "Polygon";
        const koord = o.geometri.coordinates as number[][] | number[][][];
        const punkter = yta ? franGeoRing((koord as number[][][])[0] ?? []) : (koord as number[][]).map((p) => ({ x: p[0] as number, y: p[1] as number }));
        this.figurer.set(o.objekt_id, { id: o.objekt_id, lager: o.lager_id, typ: yta ? "yta" : "linje", punkter, egenskaper: { ...o.egenskaper } });
      } else {
        this.ovrigaObjekt.push({ ...o, egenskaper: { ...o.egenskaper } });
      }
    }
    this.signalera();
  }

  /** Töm dokumentet (nytt scenario). */
  rensa(): void {
    this.fran([]);
  }

  /** Tar över hela innehållet (och lagren) från ett annat dokument, t.ex. när sidan byggs om. */
  taOver(annat: PlanDok): void {
    this.lager = annat.lager.map(kopiaLager);
    this.aktivtLager = annat.aktivtLager;
    const lager = annat.tillRitlager();
    this.fran(annat.tillFriaAndringar(), { ...lager, lager: this.lager });
  }
}
