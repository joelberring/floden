/*
 * Planförslaget som dokument: nya hus, ändrade höjder och borttagna byggnader, med ångra och
 * gör om. Dokumentet blir ett K9-scenario (ändringstyperna `byggnad_ny`, `byggnad_hojd`,
 * `byggnad_bort`); servern kontrollerar det med kärnans egen validering.
 *
 * Mönstret (objekt med nyckel, läge före och efter, grupp) är O-Tids (MIT, joelberring/otid @
 * 4a446af, packages/map-revision/src/history.ts och state.ts), se historik.ts.
 */
import { franGeoRing, normaliseraRing, tillGeoRing, type Ring } from "./geom.ts";
import { registreraSteg, ta, tomHistorik, type Historik, type Steg } from "./historik.ts";

export interface NyttHus {
  id: string;
  /** Öppen ring, moturs. */
  ring: Ring;
  hojd_m: number;
}

export type DokNyckel = `ny:${string}` | `hojd:${string}` | `bort:${string}`;
type Lage = NyttHus | number | true;

/** En K9-ändring så som servern tar emot den. */
export interface K9Andring {
  typ: "byggnad_ny" | "byggnad_hojd" | "byggnad_bort";
  andring_id: string;
  geometri: { type: "Polygon"; coordinates: number[][][] } | null;
  egenskaper: Record<string, unknown>;
}

const kopia = (h: NyttHus): NyttHus => ({ id: h.id, hojd_m: h.hojd_m, ring: h.ring.map((p) => ({ x: p.x, y: p.y })) });

export class PlanDok {
  readonly ny = new Map<string, NyttHus>();
  readonly hojd = new Map<string, number>();
  readonly bort = new Set<string>();
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
  laggTillHus(ring: Ring, hojd: number, id = this.nyttId()): NyttHus {
    const h: NyttHus = { id, ring: normaliseraRing(ring), hojd_m: hojd };
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
    return this.ny.size === 0 && this.hojd.size === 0 && this.bort.size === 0;
  }

  /** K9-ändringarna: nya hus, höjdändringar, borttagningar. Samma ordning varje gång (id). */
  tillAndringar(): K9Andring[] {
    const ut: K9Andring[] = [];
    for (const h of [...this.ny.values()].sort((a, b) => a.id.localeCompare(b.id))) {
      ut.push({
        typ: "byggnad_ny",
        andring_id: h.id,
        geometri: { type: "Polygon", coordinates: [tillGeoRing(normaliseraRing(h.ring))] },
        egenskaper: { hojd_m: h.hojd_m },
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

  /** Ersätter dokumentet med K9-ändringar (laddat scenario). Historiken töms. */
  fran(andringar: ReadonlyArray<K9Andring>): void {
    this.ny.clear();
    this.hojd.clear();
    this.bort.clear();
    this.historik = tomHistorik();
    for (const a of andringar) {
      const e = a.egenskaper;
      if (a.typ === "byggnad_ny" && a.geometri) {
        const ring = franGeoRing(a.geometri.coordinates[0] ?? []);
        this.ny.set(a.andring_id, { id: a.andring_id, ring, hojd_m: Number(e.hojd_m) });
      } else if (a.typ === "byggnad_hojd") {
        this.hojd.set(String(e.byggnads_id), Number(e.hojd_m));
      } else if (a.typ === "byggnad_bort") {
        this.bort.add(String(e.byggnads_id));
      }
    }
    this.signalera();
  }

  /** Töm dokumentet (nytt scenario). */
  rensa(): void {
    this.fran([]);
  }
}
