/*
 * WebGPU-motor för `vatten_2d.wgsl` (2D lokal tröghet, f32), provad bara i webbläsarprovet
 * `#/prov/webgpu`. Samma ordning som serverns referensschema (`benchmark.provfall.vatten_schema`):
 * varje tidssteg kör `uppdatera_qx`, `uppdatera_qy` och `begransa_och_volym` efter varandra i
 * samma beräkningspass. Skissläge: f32, aldrig rapporttal.
 *
 * Varje entry point får sin egen bindgrupp, eftersom `layout: "auto"` bara tar med de bindningar
 * en entry point använder (en gemensam bindgrupp med alla åtta ger ett valideringsfel).
 */
import vattenShaderKod from "./shaders/vatten_2d.wgsl?raw";
import {
  BERAKNINGSPLATS_WEBGPU,
  MAP_MODE_READ,
  USAGE_COPY_DST,
  USAGE_COPY_SRC,
  USAGE_MAP_READ,
  USAGE_STORAGE,
  USAGE_UNIFORM,
  harWebGpu,
  medFelkontroll,
} from "./webgpuMotor";
import type { GpuBufferLike, GpuDeviceLike, GpuNavigatorLike } from "./webgpuMotor";
import {
  BINDNINGAR,
  ENTRIES,
  arbetsgrupper,
  kontrolleraVattenIndata,
  packaVattenParams,
} from "./vattenLayout";
import type { VattenEntry, VattenGpuIndata, VattenGpuResultat } from "./vattenLayout";

export { BINDNINGAR, arbetsgrupper, packaVattenParams };
export type { VattenGpuIndata, VattenGpuResultat, VattenSteg } from "./vattenLayout";

export class WebGpuVattenMotor {
  private readonly device: GpuDeviceLike;
  private readonly pipelines: Record<VattenEntry, { getBindGroupLayout(index: number): unknown }>;

  private constructor(
    device: GpuDeviceLike,
    pipelines: Record<VattenEntry, { getBindGroupLayout(index: number): unknown }>,
  ) {
    this.device = device;
    this.pipelines = pipelines;
  }

  static async skapa(shaderKod: string = vattenShaderKod): Promise<WebGpuVattenMotor | null> {
    if (!harWebGpu()) return null;
    const gpu = (navigator as unknown as GpuNavigatorLike).gpu;
    if (!gpu) return null;
    const adapter = await gpu.requestAdapter();
    if (!adapter) return null;
    const device = await adapter.requestDevice();
    const pipelines = await medFelkontroll(device, async () => {
      const modul = device.createShaderModule({ code: shaderKod });
      const pipe = (entryPoint: VattenEntry): { getBindGroupLayout(index: number): unknown } =>
        device.createComputePipeline({ layout: "auto", compute: { module: modul, entryPoint } });
      return {
        uppdatera_qx: pipe("uppdatera_qx"),
        uppdatera_qy: pipe("uppdatera_qy"),
        begransa_och_volym: pipe("begransa_och_volym"),
      };
    });
    return new WebGpuVattenMotor(device, pipelines);
  }

  async kor(ind: VattenGpuIndata): Promise<VattenGpuResultat> {
    kontrolleraVattenIndata(ind);
    return medFelkontroll(this.device, () => this.korUtanFelkontroll(ind));
  }

  private async korUtanFelkontroll(ind: VattenGpuIndata): Promise<VattenGpuResultat> {
    const t0 = performance.now();
    const { nx, ny } = ind;
    const faltBytes = nx * ny * 4;
    const qxBytes = Math.max(4, ny * (nx - 1) * 4);
    const qyBytes = Math.max(4, (ny - 1) * nx * 4);
    const d = this.device;
    const skapa = (size: number, usage: number): GpuBufferLike => d.createBuffer({ size, usage });
    const buffrar: GpuBufferLike[] = [];
    let totalBytes = 0;
    const ny_ = (size: number, usage: number): GpuBufferLike => {
      const b = skapa(size, usage);
      buffrar.push(b);
      totalBytes += size;
      return b;
    };
    const params = ny_(32, USAGE_UNIFORM | USAGE_COPY_DST);
    const zBuf = ny_(faltBytes, USAGE_STORAGE | USAGE_COPY_DST);
    const manBuf = ny_(faltBytes, USAGE_STORAGE | USAGE_COPY_DST);
    const aktBuf = ny_(faltBytes, USAGE_STORAGE | USAGE_COPY_DST);
    const volBuf = ny_(faltBytes, USAGE_STORAGE | USAGE_COPY_DST | USAGE_COPY_SRC);
    const qxBuf = ny_(qxBytes, USAGE_STORAGE | USAGE_COPY_DST);
    const qyBuf = ny_(qyBytes, USAGE_STORAGE | USAGE_COPY_DST);
    const maxBuf = ny_(faltBytes, USAGE_STORAGE | USAGE_COPY_DST | USAGE_COPY_SRC);
    const volLas = ny_(faltBytes, USAGE_MAP_READ | USAGE_COPY_DST);
    const maxLas = ny_(faltBytes, USAGE_MAP_READ | USAGE_COPY_DST);
    const per: Record<number, GpuBufferLike> = {
      0: params, 1: zBuf, 2: manBuf, 3: aktBuf, 4: volBuf, 5: qxBuf, 6: qyBuf, 7: maxBuf,
    };
    try {
      const noll = (b: GpuBufferLike, bytes: number): void =>
        d.queue.writeBuffer(b, 0, new Uint8Array(bytes));
      d.queue.writeBuffer(zBuf, 0, ind.z_bed);
      d.queue.writeBuffer(manBuf, 0, ind.manning);
      d.queue.writeBuffer(aktBuf, 0, ind.aktiv);
      noll(volBuf, faltBytes);
      noll(qxBuf, qxBytes);
      noll(qyBuf, qyBytes);
      noll(maxBuf, faltBytes);

      const grupper = {} as Record<VattenEntry, unknown>;
      for (const e of ENTRIES) {
        grupper[e] = d.createBindGroup({
          layout: this.pipelines[e].getBindGroupLayout(0),
          entries: BINDNINGAR[e].map((binding) => ({
            binding,
            resource: { buffer: per[binding] as GpuBufferLike },
          })),
        });
      }
      for (const fas of ind.steg) {
        // Regnet är ett uniformvärde: skrivs före varje fas, och kön behåller ordningen.
        d.queue.writeBuffer(params, 0, new Uint8Array(packaVattenParams(ind, fas.regn_m)));
        const enc = d.createCommandEncoder();
        const pass = enc.beginComputePass();
        for (let i = 0; i < fas.antal; i += 1) {
          for (const e of ENTRIES) {
            pass.setPipeline(this.pipelines[e]);
            pass.setBindGroup(0, grupper[e]);
            const [gx, gy] = arbetsgrupper(e, nx, ny);
            pass.dispatchWorkgroups(gx, gy, 1);
          }
        }
        pass.end();
        d.queue.submit([enc.finish()]);
      }
      const enc = d.createCommandEncoder();
      enc.copyBufferToBuffer(volBuf, 0, volLas, 0, faltBytes);
      enc.copyBufferToBuffer(maxBuf, 0, maxLas, 0, faltBytes);
      d.queue.submit([enc.finish()]);
      await Promise.all([volLas.mapAsync(MAP_MODE_READ), maxLas.mapAsync(MAP_MODE_READ)]);
      const volym = new Float32Array(volLas.getMappedRange().slice(0));
      const max_djup_m = new Float32Array(maxLas.getMappedRange().slice(0));
      volLas.unmap();
      maxLas.unmap();
      const area = ind.dx * ind.dy;
      const slutdjup_m = new Float32Array(volym.length);
      for (let i = 0; i < volym.length; i += 1) slutdjup_m[i] = (volym[i] as number) / area;
      return {
        max_djup_m,
        slutdjup_m,
        kortid_ms: performance.now() - t0,
        gpu_buffrar_mb: totalBytes / (1024 * 1024),
        berakningsplats: BERAKNINGSPLATS_WEBGPU,
      };
    } finally {
      for (const b of buffrar) b.destroy();
    }
  }

  avsluta(): void {
    this.device.destroy();
  }
}
