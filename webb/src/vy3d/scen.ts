/*
 * 3D-scenen (Three.js). Laddas först när 3D-vyn öppnas (dynamisk import från kartsidan).
 *
 * Allt som syns kommer ur serverns buffertar (terräng, byggnader) och ur körningarnas resultat
 * (soltimmar och vattendjup på marken, VSC på fasaderna). Three.js räknar ingenting av det som
 * visas som svar: inga skuggor kastas (`castShadow` är aldrig på) och ljussättningen av husen är
 * neutral och visar bara former. Färgerna är exakt 2D-skalornas (färghantering av, sRGB rakt igenom).
 *
 * Två sidor (varsin `Scene`) gör före/efter möjligt: svep delar bilden med en sax, sida vid sida
 * ritar två fönster med samma kamera. Terrängens geometri delas mellan sidorna.
 */
import {
  BufferAttribute,
  BufferGeometry,
  ColorManagement,
  DataTexture,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
  Group,
  HemisphereLight,
  LinearSRGBColorSpace,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  MeshLambertMaterial,
  NearestFilter,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Raycaster,
  RGBAFormat,
  Scene,
  SphereGeometry,
  Uint32BufferAttribute,
  UnsignedByteType,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Teckenforklaring } from "../fragor-typer.ts";
import { byggnadText, fasadText, straleMotHojdfalt, type FasadVarden } from "./avlasning.ts";
import { fargaCeller, fargFor, VSC_TECKENFORKLARING, vscSkillnadsskala } from "./farger.ts";
import type { Behallare } from "./format.ts";
import {
  byggnaderFran,
  extrudera,
  Hojdfalt,
  husFranBuffert,
  markRutaFran,
  STATUS_ANDRAD,
  STATUS_BORT,
  STATUS_NY,
  terrangNat,
  terrangRutaFran,
  vattenYta,
  type ByggnaderBuf,
  type Hus,
  type MarkRuta,
  type TerrangRuta,
} from "./geometri.ts";
import type { Hamtare, Vy3dInfo } from "./hamta.ts";
import { franScen, kameraPosition, kameraVinklar, SYNFALT_GRADER, tillScen, type Kamera, type Origo } from "./koord.ts";
import { arVattenYta, type SidKonfig, type Uppdelning } from "./lagerval.ts";
import { rutNyckel, valjRutor, type Rut } from "./rutnat.ts";

ColorManagement.enabled = false;

const MARK_GRA = 0xe9e9e9;
const HUS_OFORANDRAD = 0xd2d2d2;
const HUS_FORSLAG = 0x7f93ad;
const KANT_OFORANDRAD = 0x757575;
const KANT_FORSLAG = 0x111111;

/** Det användaren ser i en sida: vad som är tänt och hur genomskinligt. */
export interface Stil {
  terrang: { synlig: boolean; opacitet: number };
  byggnader: { synlig: boolean; opacitet: number };
  resultat: { synlig: boolean; opacitet: number };
  fasad: { synlig: boolean; opacitet: number };
  plan: { synlig: boolean; opacitet: number };
}

/** Ett ritat hus ur planförslaget (live, inte beräknat). `hojd_m` är höjden över marken. */
export interface PlanHus {
  slag: "ny" | "andrad" | "bort";
  /** Yttre ring först, därefter hål; SWEREF (E, N). */
  ringar: Array<Array<[number, number]>>;
  hojd_m: number;
}

export interface Tillstand {
  korning: string | null;
  uppdelning: Uppdelning;
  stil: Stil;
  /** Gräns för svep (0..1 av bredden). */
  svep: number;
  plan: PlanHus[];
  /** Analysen vars VSC visas på fasaderna (null = inga fasader). */
  fasadAnalys: string | null;
  vattenTroskel: number;
}

export type Hover =
  | { slag: "mark"; e: number; n: number; sida: "a" | "b" }
  | { slag: "text"; text: string }
  | null;

interface Rutdel {
  bas: Mesh;
  overlay: Mesh | null;
  overlayNyckel: string;
}

interface BygglistaPost {
  mesh: Mesh;
  hus: number[];
  triHus: Uint32Array;
}

interface FasadData {
  antal: number;
  vsc: Float32Array;
  fore: Float32Array | null;
  skillnad: Float32Array | null;
  vaning: Uint16Array;
  byggnad: Uint32Array;
  byggnadIds: string[];
}

function stilMat<T extends { opacity: number; transparent: boolean }>(m: T, o: number, genomskinlig: boolean): void {
  m.opacity = o;
  m.transparent = genomskinlig || o < 1;
}

class Sida {
  readonly scene = new Scene();
  readonly terrang = new Group();
  readonly mark = new Group();
  readonly bygg = new Group();
  readonly fasad = new Group();
  readonly plan = new Group();
  rutor = new Map<string, Rutdel>();
  byggnader: BygglistaPost[] = [];
  byggBuf: ByggnaderBuf | null = null;
  byggNyckel = "";
  fasadPunkter: Points | null = null;
  fasadData: FasadData | null = null;
  fasadNyckel = "";
  config: SidKonfig | null = null;
  readonly basMat = new MeshLambertMaterial({ color: MARK_GRA, side: DoubleSide });

  constructor() {
    this.scene.add(this.terrang, this.mark, this.bygg, this.fasad, this.plan);
    this.scene.add(new HemisphereLight(0xffffff, 0xcfcfcf, 1.9));
    const sol = new DirectionalLight(0xffffff, 0.5);
    sol.position.set(-120, 200, -80); // ljus för former, inte en solriktning; inga skuggor kastas
    sol.castShadow = false;
    this.scene.add(sol);
  }
}

export class Vy3d {
  readonly renderer: WebGLRenderer;
  readonly camera = new PerspectiveCamera(SYNFALT_GRADER, 1, 1, 12000);
  readonly controls: OrbitControls;
  readonly hojdfalt = new Hojdfalt();
  readonly sidor: [Sida, Sida] = [new Sida(), new Sida()];
  /** Anropas när användaren flyttat kameran (för synk mot 2D-kartan och rutval). */
  onKamera: (() => void) | null = null;
  onHover: ((h: Hover) => void) | null = null;
  onFel: ((text: string) => void) | null = null;

  private tillstand: Tillstand | null = null;
  private readonly terrangCache = new Map<string, { t: TerrangRuta; geom: BufferGeometry }>();
  private visaNycklar = "";
  private gen = 0;
  private behovRita = true;
  private lopande = true;
  private rutTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly storlek = { b: 1, h: 1 };
  private readonly raycaster = new Raycaster();
  private readonly markor: Mesh;
  private hoverBegard = false;
  private senastePekare: { x: number; y: number } | null = null;
  private readonly o: Origo;

  constructor(
    private readonly yta: HTMLElement,
    private readonly hamtare: Hamtare,
    private readonly info: Vy3dInfo,
    start: Kamera,
  ) {
    this.o = info.origo;
    this.renderer = new WebGLRenderer({ antialias: true, powerPreference: "high-performance", preserveDrawingBuffer: true });
    this.renderer.outputColorSpace = LinearSRGBColorSpace;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0xf2f2f2, 1);
    this.renderer.shadowMap.enabled = false;
    this.renderer.domElement.classList.add("vy3d-duk");
    this.renderer.domElement.setAttribute("aria-label", "3D-vy av kvarteret. Dra för att rotera, högerknapp för att flytta, rulla för att zooma.");
    yta.appendChild(this.renderer.domElement);
    this.camera.up.set(0, 1, 0);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.screenSpacePanning = false;
    this.controls.maxPolarAngle = (84 * Math.PI) / 180;
    this.controls.minDistance = 15;
    this.controls.maxDistance = 3500;
    this.controls.addEventListener("change", () => {
      this.behovRita = true;
      this.planeraRutor();
      this.onKamera?.();
    });
    this.markor = new Mesh(new SphereGeometry(0.9, 12, 8), new MeshBasicMaterial({ color: 0x111111, depthTest: false, transparent: true, opacity: 0.9 }));
    this.markor.renderOrder = 10;
    this.markor.visible = false;
    for (const s of this.sidor) s.scene.add(this.markor.clone());
    this.settKamera(start);
    this.raycaster.params.Points = { threshold: 1.3 };
    const dom = this.renderer.domElement;
    dom.addEventListener("pointermove", (h) => {
      this.senastePekare = { x: h.clientX, y: h.clientY };
      if (!this.hoverBegard) {
        this.hoverBegard = true;
        requestAnimationFrame(() => {
          this.hoverBegard = false;
          if (this.senastePekare) this.hovra(this.senastePekare.x, this.senastePekare.y);
        });
      }
    });
    dom.addEventListener("pointerleave", () => {
      this.senastePekare = null;
      for (const s of this.sidor) this.dolj(s);
      this.onHover?.(null);
      this.behovRita = true;
    });
    this.observator = new ResizeObserver(() => this.storleksandring());
    this.observator.observe(yta);
    this.storleksandring();
    const loop = (): void => {
      if (!this.lopande) return;
      if (this.behovRita) {
        this.behovRita = false;
        this.rita();
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  private readonly observator: ResizeObserver;

  // ------------------------------------------------------------------ kamera

  hamtaKamera(): Kamera {
    const v = kameraVinklar(this.camera.position, this.controls.target);
    const m = franScen(this.controls.target, this.o);
    return { mal: m, avstand: v.avstand, azimut: v.azimut, lutning: v.lutning };
  }

  settKamera(k0: Kamera): void {
    const k = { ...k0, lutning: Math.max(0.3, k0.lutning) };
    const p = kameraPosition(k, this.o);
    const mal = tillScen(k.mal.e, k.mal.n, k.mal.z, this.o);
    this.camera.position.set(p.x, p.y, p.z);
    this.controls.target.set(mal.x, mal.y, mal.z);
    // Exakt lodrät blick har ingen bestämd "uppåt"-riktning; en tiondels grad räcker.
    // Rakt uppifrån är "upp" i bild norr; OrbitControls behöver en lutning > 0 för att hålla azimuten.
    this.camera.lookAt(mal.x, mal.y, mal.z);
    this.controls.update();
    this.behovRita = true;
    this.planeraRutor();
  }

  /** Höjden över terrängen i målpunkten (för att lägga måltavlan på marken). */
  markHojd(e: number, n: number): number {
    const z = this.hojdfalt.hojd(e, n);
    return Number.isFinite(z) ? z : this.info.hojd.z_min;
  }

  private storleksandring(): void {
    const b = Math.max(1, this.yta.clientWidth);
    const h = Math.max(1, this.yta.clientHeight);
    this.storlek.b = b;
    this.storlek.h = h;
    this.renderer.setSize(b, h, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.behovRita = true;
  }

  // ------------------------------------------------------------------ tillstånd och rutor

  satt(t: Tillstand): void {
    this.tillstand = t;
    this.behovRita = true;
    void this.synka();
  }

  /** Kamerans läge i E, N och höjd över nollnivån, för rutval. */
  private kameraPunkt(): { e: number; n: number; z: number } {
    const p = franScen(this.camera.position, this.o);
    return { e: p.e, n: p.n, z: Math.max(1, p.z - this.info.hojd.z_min) };
  }

  private onskadeRutor(): Rut[] {
    return valjRutor(this.info.utbredning, this.kameraPunkt());
  }

  private planeraRutor(): void {
    if (this.rutTimer !== null) return;
    this.rutTimer = setTimeout(() => {
      this.rutTimer = null;
      const nyckel = this.onskadeRutor().map(rutNyckel).sort().join(",");
      if (nyckel !== this.visaNycklar) void this.synka();
    }, 140);
  }

  private async terrangRuta(r: Rut): Promise<{ t: TerrangRuta; geom: BufferGeometry } | null> {
    const k = rutNyckel(r);
    const finns = this.terrangCache.get(k);
    if (finns) return finns;
    let b: Behallare;
    try {
      b = await this.hamtare.terrang(r);
    } catch {
      return null; // rutan ligger utanför projektet
    }
    const t = terrangRutaFran(b);
    const g = terrangNat(t, this.o);
    const geom = new BufferGeometry();
    geom.setAttribute("position", new BufferAttribute(g.position, 3));
    geom.setAttribute("uv", new BufferAttribute(g.uv, 2));
    geom.setIndex(new Uint32BufferAttribute(g.index, 1));
    geom.computeVertexNormals();
    const post = { t, geom };
    this.terrangCache.set(k, post);
    this.hojdfalt.lagg(t);
    // Håller minnet i styr: de äldsta rutorna släpps (de hämtas om vid behov).
    if (this.terrangCache.size > 220) {
      const aktiva = new Set(this.visaNycklar.split(","));
      for (const [nyckel, v] of this.terrangCache) {
        if (this.terrangCache.size <= 180) break;
        if (aktiva.has(nyckel)) continue;
        v.geom.dispose();
        this.terrangCache.delete(nyckel);
      }
    }
    return post;
  }

  /** Bygger om det som syns: rätt rutor för kameran, resultatlager, byggnader, fasader och förslag. */
  private async synka(): Promise<void> {
    const t = this.tillstand;
    if (!t) return;
    const gen = ++this.gen;
    const onskade = this.onskadeRutor();
    try {
      const terr = await Promise.all(onskade.map((r) => this.terrangRuta(r)));
      if (gen !== this.gen) return;
      const rutor = onskade.map((r, i) => ({ r, post: terr[i] ?? null })).filter((x): x is { r: Rut; post: { t: TerrangRuta; geom: BufferGeometry } } => x.post !== null);
      const sidor: Array<[Sida, SidKonfig | null]> = [
        [this.sidor[0], t.uppdelning.a],
        [this.sidor[1], t.uppdelning.b],
      ];
      for (const [sida, cfg] of sidor) {
        sida.config = cfg;
        if (cfg === null) {
          this.tomSida(sida);
          continue;
        }
        await this.byggRutor(sida, cfg, rutor, t, gen);
        if (gen !== this.gen) return;
      }
      const aktiva = sidor.filter(([, cfg]) => cfg !== null);
      await Promise.all(aktiva.map(([sida, cfg]) => this.byggByggnader(sida, cfg, t)));
      if (gen !== this.gen) return;
      await Promise.all(aktiva.map(([sida, cfg]) => this.byggFasad(sida, cfg, t)));
      if (gen !== this.gen) return;
      for (const [sida] of aktiva) this.byggPlan(sida, t);
      this.visaNycklar = onskade.map(rutNyckel).sort().join(",");
      this.tillampaStil();
      this.behovRita = true;
    } catch (e) {
      this.onFel?.(e instanceof Error ? e.message : String(e));
    }
  }

  /** Tömmer en sida som inte används (en enda vy visar bara sida A). */
  private tomSida(sida: Sida): void {
    for (const d of sida.rutor.values()) {
      sida.terrang.remove(d.bas);
      this.slappOverlay(sida, d);
    }
    sida.rutor.clear();
    for (const p of sida.byggnader) p.mesh.geometry.dispose();
    sida.bygg.clear();
    sida.byggnader = [];
    sida.byggNyckel = "";
    if (sida.fasadPunkter) {
      sida.fasad.remove(sida.fasadPunkter);
      sida.fasadPunkter.geometry.dispose();
      sida.fasadPunkter = null;
      sida.fasadData = null;
    }
    sida.fasadNyckel = "";
  }

  private async byggRutor(sida: Sida, cfg: SidKonfig | null, rutor: Array<{ r: Rut; post: { t: TerrangRuta; geom: BufferGeometry } }>, t: Tillstand, gen: number): Promise<void> {
    const mark = cfg?.mark ?? null;
    const vatten = arVattenYta(mark);
    const marker: Array<Behallare | null> = await Promise.all(
      rutor.map(({ r }) => (mark && t.korning ? this.hamtare.mark(t.korning, mark.id, r).catch(() => null) : Promise.resolve(null))),
    );
    if (gen !== this.gen) return;
    const behall = new Set(rutor.map(({ r }) => rutNyckel(r)));
    for (const [k, d] of sida.rutor) {
      if (behall.has(k)) continue;
      sida.terrang.remove(d.bas);
      this.slappOverlay(sida, d);
      sida.rutor.delete(k);
    }
    rutor.forEach(({ r, post }, i) => {
      const k = rutNyckel(r);
      let d = sida.rutor.get(k);
      if (!d) {
        const bas = new Mesh(post.geom, sida.basMat);
        sida.terrang.add(bas);
        d = { bas, overlay: null, overlayNyckel: "" };
        sida.rutor.set(k, d);
      }
      const nyckel = mark ? `${mark.id}|${vatten ? `v${t.vattenTroskel}` : "m"}` : "";
      if (d.overlayNyckel === nyckel) return;
      this.slappOverlay(sida, d);
      d.overlayNyckel = nyckel;
      const b = marker[i];
      if (!mark || !b) return;
      const mr = markRutaFran(b);
      d.overlay = vatten ? this.vattenMesh(mr, mark.teckenforklaring, t.vattenTroskel) : this.drapering(mr, mark.teckenforklaring, post.geom);
      if (d.overlay) sida.mark.add(d.overlay);
    });
  }

  private slappOverlay(sida: Sida, d: Rutdel): void {
    if (!d.overlay) return;
    sida.mark.remove(d.overlay);
    const m = d.overlay.material as MeshBasicMaterial;
    m.map?.dispose();
    m.dispose();
    if (d.overlay.userData.egenGeometri) d.overlay.geometry.dispose();
    d.overlay = null;
  }

  private drapering(mr: MarkRuta, tf: Teckenforklaring, geom: BufferGeometry): Mesh {
    const tex = new Uint8Array(128 * 128 * 4);
    fargaCeller(mr.q, mr.lo, mr.hi, tf, tex);
    const textur = new DataTexture(tex, 128, 128, RGBAFormat, UnsignedByteType);
    textur.magFilter = NearestFilter;
    textur.minFilter = NearestFilter;
    textur.flipY = false;
    textur.needsUpdate = true;
    const mat = new MeshBasicMaterial({ map: textur, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
    const m = new Mesh(geom, mat);
    m.renderOrder = 1;
    return m;
  }

  private vattenMesh(mr: MarkRuta, tf: Teckenforklaring, troskel: number): Mesh | null {
    const y = vattenYta(mr, (e, n) => this.hojdfalt.hojd(e, n), this.o, troskel, (d) => fargFor(tf, d));
    if (y.celler === 0) return null;
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(y.position, 3));
    g.setAttribute("color", new Float32BufferAttribute(y.color, 3));
    g.setIndex(new Uint32BufferAttribute(y.index, 1));
    const mat = new MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.8, depthWrite: false, side: DoubleSide, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
    const m = new Mesh(g, mat);
    m.userData.egenGeometri = true;
    m.renderOrder = 2;
    return m;
  }

  // ------------------------------------------------------------------ byggnader

  private async byggByggnader(sida: Sida, cfg: SidKonfig | null, t: Tillstand): Promise<void> {
    const korning = t.korning && cfg?.scen ? t.korning : null;
    const scen = cfg?.scen ?? null;
    const nyckel = `${korning}|${scen}`;
    if (sida.byggNyckel === nyckel && sida.byggnader.length > 0) return;
    const b = await this.hamtare.byggnader(korning, scen);
    const buf = byggnaderFran(b);
    for (const p of sida.byggnader) {
      sida.bygg.remove(p.mesh);
      p.mesh.geometry.dispose();
    }
    sida.bygg.clear();
    sida.byggnader = [];
    sida.byggBuf = buf;
    sida.byggNyckel = nyckel;
    const grupper: Array<{ namn: string; urval: (i: number) => boolean; farg: number; kant: number; genomskinlig: boolean; opacitet: number }> = [
      { namn: "oforandrad", urval: (i) => buf.status[i] === 0, farg: HUS_OFORANDRAD, kant: KANT_OFORANDRAD, genomskinlig: false, opacitet: 1 },
      { namn: "forslag", urval: (i) => buf.status[i] === STATUS_NY || buf.status[i] === STATUS_ANDRAD, farg: HUS_FORSLAG, kant: KANT_FORSLAG, genomskinlig: false, opacitet: 1 },
      { namn: "bort", urval: (i) => buf.status[i] === STATUS_BORT, farg: 0xffffff, kant: KANT_FORSLAG, genomskinlig: true, opacitet: 0.4 },
    ];
    for (const g of grupper) {
      const { hus, index } = husFranBuffert(buf, g.urval);
      if (hus.length === 0) continue;
      const e = extrudera(hus);
      const geom = new BufferGeometry();
      geom.setAttribute("position", new BufferAttribute(e.position, 3));
      geom.setAttribute("normal", new BufferAttribute(e.normal, 3));
      geom.setIndex(new Uint32BufferAttribute(e.index, 1));
      const mat = new MeshLambertMaterial({ color: g.farg, side: DoubleSide, transparent: g.genomskinlig, opacity: g.opacitet, depthWrite: !g.genomskinlig });
      const mesh = new Mesh(geom, mat);
      mesh.name = g.namn;
      sida.bygg.add(mesh);
      sida.byggnader.push({ mesh, hus: index, triHus: e.triHus });
      const kg = new BufferGeometry();
      kg.setAttribute("position", new BufferAttribute(e.kanter, 3));
      const kant = new LineSegments(kg, new LineBasicMaterial({ color: g.kant, transparent: g.genomskinlig, opacity: g.genomskinlig ? 0.7 : 1 }));
      sida.bygg.add(kant);
    }
    // Det höjda husets tidigare takhöjd som tunn linje, så att ändringen syns utan siffror.
    const gamla = husFranBuffert(buf, (i) => buf.status[i] === STATUS_ANDRAD, "fore");
    if (gamla.hus.length > 0) {
      const lin: number[] = [];
      for (const h of gamla.hus) {
        for (const ring of h.ringar) {
          for (let k = 0; k < ring.length; k++) {
            const a = ring[k] as [number, number];
            const c = ring[(k + 1) % ring.length] as [number, number];
            lin.push(a[0], h.yTak, a[1], c[0], h.yTak, c[1]);
          }
        }
      }
      const lg = new BufferGeometry();
      lg.setAttribute("position", new Float32BufferAttribute(lin, 3));
      sida.bygg.add(new LineSegments(lg, new LineBasicMaterial({ color: 0xb4462b })));
    }
  }

  // ------------------------------------------------------------------ fasader

  private async byggFasad(sida: Sida, cfg: SidKonfig | null, t: Tillstand): Promise<void> {
    const f = cfg?.fasad ?? null;
    const nyckel = f && t.korning && t.fasadAnalys ? `${t.korning}|${f.scenario}|${f.jamfor}|${t.fasadAnalys}` : "";
    if (sida.fasadNyckel === nyckel) return;
    if (sida.fasadPunkter) {
      sida.fasad.remove(sida.fasadPunkter);
      sida.fasadPunkter.geometry.dispose();
      (sida.fasadPunkter.material as PointsMaterial).dispose();
      sida.fasadPunkter = null;
      sida.fasadData = null;
    }
    sida.fasadNyckel = nyckel;
    if (!f || !t.korning || !t.fasadAnalys) return;
    const b = await this.hamtare.fasad(t.korning, f.scenario, t.fasadAnalys, f.jamfor);
    const h = b.huvud as unknown as { antal: number; byggnad_ids: string[] };
    const d = b.delar;
    const xyz = d.xyz as Float32Array;
    const n = h.antal;
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      // Buffertens (öster, norr, upp) relativt origo blir scenens (x, y, z) = (öster, upp, -norr).
      pos[i * 3] = xyz[i * 3] as number;
      pos[i * 3 + 1] = xyz[i * 3 + 2] as number;
      pos[i * 3 + 2] = -(xyz[i * 3 + 1] as number);
    }
    const vsc = d.vsc as Float32Array;
    const skillnad = (d.skillnad as Float32Array | undefined) ?? null;
    const fore = (d.vsc_fore as Float32Array | undefined) ?? null;
    const data: FasadData = { antal: n, vsc, fore, skillnad, vaning: d.vaning as Uint16Array, byggnad: d.byggnad as Uint32Array, byggnadIds: h.byggnad_ids };
    const farger = new Float32Array(n * 3);
    let storst = 0.5;
    if (skillnad) for (const v of skillnad) if (Number.isFinite(v)) storst = Math.max(storst, Math.abs(v));
    const tf: Teckenforklaring = f.jamfor && skillnad ? vscSkillnadsskala(storst) : VSC_TECKENFORKLARING;
    for (let i = 0; i < n; i++) {
      const v = f.jamfor && skillnad ? (skillnad[i] as number) : (vsc[i] as number);
      const c = fargFor(tf, v);
      // Punkt utan värde (ingen jämförelse eller inget resultat) ritas grå, aldrig som ett värde.
      const rgb: [number, number, number] = c[3] === 0 ? (Number.isFinite(v) ? [235, 235, 235] : [154, 154, 154]) : [c[0], c[1], c[2]];
      farger[i * 3] = rgb[0] / 255;
      farger[i * 3 + 1] = rgb[1] / 255;
      farger[i * 3 + 2] = rgb[2] / 255;
    }
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(pos, 3));
    g.setAttribute("color", new BufferAttribute(farger, 3));
    const pts = new Points(g, new PointsMaterial({ size: 2.4, sizeAttenuation: true, vertexColors: true }));
    pts.renderOrder = 3;
    sida.fasad.add(pts);
    sida.fasadPunkter = pts;
    sida.fasadData = data;
  }

  // ------------------------------------------------------------------ planförslag (live)

  private byggPlan(sida: Sida, t: Tillstand): void {
    for (const c of [...sida.plan.children]) {
      sida.plan.remove(c);
      if ((c as Mesh).geometry) (c as Mesh).geometry.dispose();
    }
    if (t.plan.length === 0) return;
    for (const slag of ["ny", "andrad", "bort"] as const) {
      const lista = t.plan.filter((p) => p.slag === slag);
      if (lista.length === 0) continue;
      const hus: Hus[] = lista.map((p) => {
        // Samma regel som servern för ett nytt hus: tak = mediankänd markhöjd + husets höjd (platt tak).
        const z = (p.ringar[0] ?? []).map(([e, n]) => this.markHojd(e, n)).sort((a, b) => a - b);
        const lagst = z[0] ?? this.info.hojd.z_min;
        const median = z[Math.floor(z.length / 2)] ?? lagst;
        return {
          ringar: p.ringar.map((r) => r.map(([e, n]) => [e - this.o.e, -(n - this.o.n)] as [number, number])),
          yFot: lagst - 0.3 - this.o.z,
          yTak: median + p.hojd_m - this.o.z,
        };
      });
      const e = extrudera(hus);
      const g = new BufferGeometry();
      g.setAttribute("position", new BufferAttribute(e.position, 3));
      g.setAttribute("normal", new BufferAttribute(e.normal, 3));
      g.setIndex(new Uint32BufferAttribute(e.index, 1));
      const mat = new MeshLambertMaterial({ color: slag === "bort" ? 0xffffff : HUS_FORSLAG, side: DoubleSide, transparent: true, opacity: slag === "bort" ? 0.35 : 0.72, depthWrite: false });
      const m = new Mesh(g, mat);
      m.renderOrder = 4;
      sida.plan.add(m);
      const kg = new BufferGeometry();
      kg.setAttribute("position", new BufferAttribute(e.kanter, 3));
      const l = new LineSegments(kg, new LineBasicMaterial({ color: 0xc2410c }));
      l.renderOrder = 5;
      sida.plan.add(l);
    }
  }

  // ------------------------------------------------------------------ stil

  /** Tänder, släcker och tonar lagren utan att bygga om något. */
  tillampaStil(): void {
    const t = this.tillstand;
    if (!t) return;
    for (const sida of this.sidor) {
      sida.terrang.visible = t.stil.terrang.synlig;
      sida.basMat.opacity = t.stil.terrang.opacitet;
      sida.basMat.transparent = t.stil.terrang.opacitet < 1;
      sida.mark.visible = t.stil.resultat.synlig;
      for (const d of sida.rutor.values()) {
        if (!d.overlay) continue;
        const m = d.overlay.material as MeshBasicMaterial;
        stilMat(m, d.overlay.userData.egenGeometri ? t.stil.resultat.opacitet * 0.9 : t.stil.resultat.opacitet, true);
      }
      sida.bygg.visible = t.stil.byggnader.synlig;
      for (const c of sida.bygg.children) {
        const m = (c as Mesh).material as MeshLambertMaterial | LineBasicMaterial;
        if (c.type === "Mesh" && (c as Mesh).name !== "bort") stilMat(m, t.stil.byggnader.opacitet, false);
      }
      sida.fasad.visible = t.stil.fasad.synlig;
      if (sida.fasadPunkter) stilMat(sida.fasadPunkter.material as PointsMaterial, t.stil.fasad.opacitet, false);
      sida.plan.visible = t.stil.plan.synlig;
    }
    this.behovRita = true;
  }

  // ------------------------------------------------------------------ ritning

  /** Vilken sida som ligger under en skärmpunkt, och punktens läge i sidans fönster (-1..1). */
  private sidaVid(x: number, y: number): { sida: Sida; vilken: "a" | "b"; ndc: Vector2 } | null {
    const r = this.renderer.domElement.getBoundingClientRect();
    const fx = (x - r.left) / r.width;
    const fy = (y - r.top) / r.height;
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;
    const t = this.tillstand;
    const d = t?.uppdelning.delning ?? "ingen";
    if (d === "ingen") return { sida: this.sidor[0], vilken: "a", ndc: new Vector2(fx * 2 - 1, -(fy * 2 - 1)) };
    if (d === "svep") {
      const hoger = fx > (t?.svep ?? 0.5);
      return { sida: this.sidor[hoger ? 1 : 0], vilken: hoger ? "b" : "a", ndc: new Vector2(fx * 2 - 1, -(fy * 2 - 1)) };
    }
    const hoger = fx > 0.5;
    const lx = hoger ? (fx - 0.5) * 2 : fx * 2;
    return { sida: this.sidor[hoger ? 1 : 0], vilken: hoger ? "b" : "a", ndc: new Vector2(lx * 2 - 1, -(fy * 2 - 1)) };
  }

  private rita(): void {
    const t = this.tillstand;
    const r = this.renderer;
    const b = this.storlek.b;
    const h = this.storlek.h;
    const d = t?.uppdelning.delning ?? "ingen";
    r.setScissorTest(false);
    r.setViewport(0, 0, b, h);
    r.clear();
    if (d === "ingen" || !t) {
      this.camera.aspect = b / h;
      this.camera.updateProjectionMatrix();
      r.render(this.sidor[0].scene, this.camera);
      return;
    }
    r.setScissorTest(true);
    if (d === "svep") {
      const gx = Math.round(b * t.svep);
      this.camera.aspect = b / h;
      this.camera.updateProjectionMatrix();
      r.setViewport(0, 0, b, h);
      r.setScissor(0, 0, gx, h);
      r.render(this.sidor[0].scene, this.camera);
      r.setScissor(gx, 0, b - gx, h);
      r.clearDepth();
      r.render(this.sidor[1].scene, this.camera);
    } else {
      const halv = Math.floor(b / 2);
      this.camera.aspect = halv / h;
      this.camera.updateProjectionMatrix();
      r.setViewport(0, 0, halv, h);
      r.setScissor(0, 0, halv, h);
      r.render(this.sidor[0].scene, this.camera);
      r.setViewport(halv + 1, 0, b - halv - 1, h);
      r.setScissor(halv + 1, 0, b - halv - 1, h);
      r.render(this.sidor[1].scene, this.camera);
    }
    r.setScissorTest(false);
  }

  /** Ritar om nästa bildruta (efter ändringar utanför scenen, t.ex. svepets läge). */
  begarRita(): void {
    this.behovRita = true;
  }

  // ------------------------------------------------------------------ avläsning

  private dolj(s: Sida): void {
    const m = s.scene.children[s.scene.children.length - 1];
    if (m) m.visible = false;
  }

  private visaMarkor(s: Sida, p: Vector3): void {
    const m = s.scene.children[s.scene.children.length - 1];
    if (m) {
      m.visible = true;
      m.position.copy(p);
    }
  }

  private hovra(x: number, y: number): void {
    const v = this.sidaVid(x, y);
    for (const s of this.sidor) this.dolj(s);
    if (!v) {
      this.onHover?.(null);
      return;
    }
    // Kameran ritar bara ett fönster per sida; strålen byggs i sidans eget fönster.
    const b = this.storlek.b;
    const h = this.storlek.h;
    const halv = this.tillstand?.uppdelning.delning === "sida";
    this.camera.aspect = (halv ? Math.floor(b / 2) : b) / h;
    this.camera.updateProjectionMatrix();
    this.raycaster.setFromCamera(v.ndc, this.camera);
    const ray = this.raycaster.ray;
    let basta: { avstand: number; punkt: Vector3; text?: string } | null = null;
    // 1. Fasadpunkter
    const sida = v.sida;
    if (sida.fasadPunkter && sida.fasad.visible && sida.fasadData) {
      const tr = this.raycaster.intersectObject(sida.fasadPunkter)[0];
      if (tr && tr.index !== undefined) {
        const f = sida.fasadData;
        const i = tr.index;
        const lage = sida.config?.fasad?.jamfor ? "skillnad" : "annat";
        const fv: FasadVarden = {
          vsc: Number.isFinite(f.vsc[i] as number) ? (f.vsc[i] as number) : null,
          fore: f.fore && Number.isFinite(f.fore[i] as number) ? (f.fore[i] as number) : null,
          skillnad: f.skillnad && Number.isFinite(f.skillnad[i] as number) ? (f.skillnad[i] as number) : null,
          vaning: f.vaning[i] as number,
          byggnad: f.byggnadIds[f.byggnad[i] as number] ?? "",
        };
        basta = { avstand: tr.distance, punkt: tr.point.clone(), text: fasadText(fv, lage) };
      }
    }
    // 2. Byggnader
    if (sida.bygg.visible) {
      for (const p of sida.byggnader) {
        const tr = this.raycaster.intersectObject(p.mesh)[0];
        if (!tr || tr.faceIndex === undefined || tr.faceIndex === null) continue;
        if (basta && tr.distance >= basta.avstand) continue;
        const bi = p.hus[p.triHus[tr.faceIndex] as number] as number;
        const buf = sida.byggBuf;
        if (!buf) continue;
        basta = { avstand: tr.distance, punkt: tr.point.clone(), text: byggnadText(buf.ids[bi] as string, buf.hojd_m[bi] as number, buf.status[bi] as number) };
      }
    }
    // 3. Marken, genom höjdfältet
    const marken = straleMotHojdfalt({ x: ray.origin.x, y: ray.origin.y, z: ray.origin.z }, { x: ray.direction.x, y: ray.direction.y, z: ray.direction.z }, (e, n) => this.hojdfalt.hojd(e, n), this.o);
    if (marken && (!basta || marken.avstand < basta.avstand)) {
      const p = tillScen(marken.e, marken.n, marken.z, this.o);
      this.visaMarkor(sida, new Vector3(p.x, p.y, p.z));
      this.onHover?.({ slag: "mark", e: marken.e, n: marken.n, sida: v.vilken });
    } else if (basta) {
      this.visaMarkor(sida, basta.punkt);
      this.onHover?.({ slag: "text", text: basta.text ?? "" });
    } else {
      this.onHover?.(null);
    }
    this.behovRita = true;
  }

  // ------------------------------------------------------------------ mätning och avslut

  /** Roterar kameran `bilder` bildrutor och mäter tiden per bild (för prestandamätningen). */
  async matBildfrekvens(bilder = 90): Promise<{ bilder: number; ms_per_bild: number; fps: number; anrop: number; trianglar: number; gl: string; programvara: boolean }> {
    const k = this.hamtaKamera();
    await new Promise<void>((klar) => requestAnimationFrame(() => klar()));
    const t0 = performance.now();
    for (let i = 0; i < bilder; i++) {
      this.settKamera({ ...k, azimut: (k.azimut + (i * 360) / bilder) % 360, lutning: Math.max(k.lutning, 50) });
      this.behovRita = false;
      this.rita();
      this.renderer.getContext().finish();
      await new Promise<void>((klar) => requestAnimationFrame(() => klar()));
    }
    const ms = (performance.now() - t0) / bilder;
    this.settKamera(k);
    const gl = this.renderer.getContext();
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const namn = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : String(gl.getParameter(gl.RENDERER));
    return {
      bilder,
      ms_per_bild: Math.round(ms * 10) / 10,
      fps: Math.round((1000 / ms) * 10) / 10,
      anrop: this.renderer.info.render.calls,
      trianglar: this.renderer.info.render.triangles,
      gl: namn,
      programvara: /swiftshader|llvmpipe|software/i.test(namn),
    };
  }

  /** Antal ritade rutor och trianglar just nu (för mätning). */
  status(): { rutor: number; nivaer: Record<number, number> } {
    const nivaer: Record<number, number> = {};
    for (const k of this.visaNycklar.split(",")) {
      if (!k) continue;
      const lod = Number(k.split("/")[0]);
      nivaer[lod] = (nivaer[lod] ?? 0) + 1;
    }
    return { rutor: this.visaNycklar ? this.visaNycklar.split(",").length : 0, nivaer };
  }

  avmontera(): void {
    this.lopande = false;
    this.observator.disconnect();
    if (this.rutTimer !== null) clearTimeout(this.rutTimer);
    this.controls.dispose();
    for (const v of this.terrangCache.values()) v.geom.dispose();
    this.terrangCache.clear();
    for (const s of this.sidor) {
      for (const d of s.rutor.values()) this.slappOverlay(s, d);
      s.basMat.dispose();
    }
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
}
