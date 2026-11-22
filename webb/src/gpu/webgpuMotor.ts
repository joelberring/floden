/*
 * WebGPU-beräkningsmotor i webbläsaren (Pass 4, se `docs/WEBGPU-ARKITEKTUR.md`).
 *
 * Kör Flodens WGSL compute shaders lokalt på användarens GPU i f32 för interaktivt
 * skissläge (0 kr moln-GPU). Om `navigator.gpu` saknas returnerar `skapa()` null så att
 * klienten automatiskt faller tillbaka på servern. Formella rapporter räknas alltid i f64
 * på servern och märks aldrig som `klient_webgpu_f32`.
 */

import dagsljusShaderKod from "./shaders/dagsljus_horisont.wgsl?raw";

export const BERAKNINGSPLATS_WEBGPU = "klient_webgpu_f32" as const;

export interface SolPunktGpu {
  hojd_rad: number;
  azimut_rad: number;
}

export interface DagsljusGpuIndata {
  nx: number;
  ny: number;
  cell_m: number;
  rmax_celler: number;
  n_riktningar: number;
  steg_h: number;
  z_m: Float32Array;
  solbanor: readonly SolPunktGpu[];
}

export interface DagsljusGpuResultat {
  svf: Float32Array;
  soltimmar_h: Float32Array;
  kortid_ms: number;
  berakningsplats: typeof BERAKNINGSPLATS_WEBGPU;
}

// Minimala WebGPU-gränssnitt så att `tsc --noEmit` fungerar utan `@webgpu/types`.
interface GpuBufferLike {
  mapAsync(mode: number): Promise<void>;
  getMappedRange(): ArrayBuffer;
  unmap(): void;
  destroy(): void;
}
interface GpuDeviceLike {
  createShaderModule(desc: { code: string }): unknown;
  createComputePipeline(desc: {
    layout: "auto";
    compute: { module: unknown; entryPoint: string };
  }): { getBindGroupLayout(index: number): unknown };
  createBuffer(desc: { size: number; usage: number }): GpuBufferLike;
  createBindGroup(desc: {
    layout: unknown;
    entries: Array<{ binding: number; resource: { buffer: GpuBufferLike } }>;
  }): unknown;
  createCommandEncoder(): {
    beginComputePass(): {
      setPipeline(p: unknown): void;
      setBindGroup(index: number, bg: unknown): void;
      dispatchWorkgroups(x: number, y?: number, z?: number): void;
      end(): void;
    };
    copyBufferToBuffer(
      src: GpuBufferLike,
      srcOffset: number,
      dst: GpuBufferLike,
      dstOffset: number,
      size: number,
    ): void;
    finish(): unknown;
  };
  queue: {
    writeBuffer(buffer: GpuBufferLike, offset: number, data: ArrayBufferView): void;
    submit(cmds: unknown[]): void;
  };
  destroy(): void;
}
interface GpuNavigatorLike {
  gpu?: {
    requestAdapter(): Promise<{ requestDevice(): Promise<GpuDeviceLike> } | null>;
  };
}

// WebGPU standardflaggor (GPUBufferUsage & GPUMapMode)
const USAGE_MAP_READ = 0x0001;
const USAGE_COPY_SRC = 0x0004;
const USAGE_COPY_DST = 0x0008;
const USAGE_UNIFORM = 0x0040;
const USAGE_STORAGE = 0x0080;
const MAP_MODE_READ = 0x0001;

/** True om webbläsaren exponerar WebGPU (`navigator.gpu`). */
export function harWebGpu(): boolean {
  if (typeof navigator === "undefined") return false;
  return Boolean((navigator as unknown as GpuNavigatorLike).gpu);
}

/** Bygger den 32 byte stora uniform-bufferten som matchar `struct Params` i `dagsljus_horisont.wgsl`. */
export function packaDagsljusParams(indata: DagsljusGpuIndata): ArrayBuffer {
  const buf = new ArrayBuffer(32);
  const u32 = new Uint32Array(buf, 0, 4);
  const f32 = new Float32Array(buf, 16, 4);
  u32[0] = indata.nx >>> 0;
  u32[1] = indata.ny >>> 0;
  u32[2] = indata.n_riktningar >>> 0;
  u32[3] = indata.solbanor.length >>> 0;
  f32[0] = indata.cell_m;
  f32[1] = indata.rmax_celler;
  f32[2] = indata.steg_h;
  f32[3] = 0.0;
  return buf;
}

/** WebGPU-motor för interaktiv dagsljusberäkning (SVF + soltimmar) i f32. */
export class WebGpuDagsljusMotor {
  private readonly device: GpuDeviceLike;
  private readonly pipeline: { getBindGroupLayout(index: number): unknown };

  private constructor(
    device: GpuDeviceLike,
    pipeline: { getBindGroupLayout(index: number): unknown },
  ) {
    this.device = device;
    this.pipeline = pipeline;
  }

  /** Initierar WebGPU-enhet och kompilerar `dagsljus_horisont.wgsl`; null om WebGPU saknas. */
  static async skapa(shaderKod: string = dagsljusShaderKod): Promise<WebGpuDagsljusMotor | null> {
    if (!harWebGpu()) return null;
    const gpu = (navigator as unknown as GpuNavigatorLike).gpu;
    if (!gpu) return null;
    const adapter = await gpu.requestAdapter();
    if (!adapter) return null;
    const device = await adapter.requestDevice();
    const modul = device.createShaderModule({ code: shaderKod });
    const pipeline = device.createComputePipeline({
      layout: "auto",
      compute: { module: modul, entryPoint: "berakna_svf_och_soltimmar" },
    });
    return new WebGpuDagsljusMotor(device, pipeline);
  }

  /** Kör SVF + direkta soltimmar på GPU och returnerar f32-fält utan kvarlämnat VRAM. */
  async korSvfOchSoltimmar(indata: DagsljusGpuIndata): Promise<DagsljusGpuResultat> {
    const t0 = performance.now();
    const nCeller = indata.nx * indata.ny;
    if (indata.z_m.length !== nCeller) {
      throw new Error(`z_m har längd ${indata.z_m.length}, förväntade ${nCeller}.`);
    }

    const faltBytes = Math.max(4, nCeller * 4);
    const solData = new Float32Array(Math.max(2, indata.solbanor.length * 2));
    for (let i = 0; i < indata.solbanor.length; i += 1) {
      const sp = indata.solbanor[i];
      if (!sp) continue;
      solData[2 * i] = sp.hojd_rad;
      solData[2 * i + 1] = sp.azimut_rad;
    }

    const paramsBuf = this.device.createBuffer({
      size: 32,
      usage: USAGE_UNIFORM | USAGE_COPY_DST,
    });
    const zBuf = this.device.createBuffer({
      size: faltBytes,
      usage: USAGE_STORAGE | USAGE_COPY_DST,
    });
    const solBuf = this.device.createBuffer({
      size: solData.byteLength,
      usage: USAGE_STORAGE | USAGE_COPY_DST,
    });
    const svfBuf = this.device.createBuffer({
      size: faltBytes,
      usage: USAGE_STORAGE | USAGE_COPY_SRC,
    });
    const solUtBuf = this.device.createBuffer({
      size: faltBytes,
      usage: USAGE_STORAGE | USAGE_COPY_SRC,
    });
    const svfLasBuf = this.device.createBuffer({
      size: faltBytes,
      usage: USAGE_MAP_READ | USAGE_COPY_DST,
    });
    const solLasBuf = this.device.createBuffer({
      size: faltBytes,
      usage: USAGE_MAP_READ | USAGE_COPY_DST,
    });

    try {
      this.device.queue.writeBuffer(paramsBuf, 0, new Uint8Array(packaDagsljusParams(indata)));
      this.device.queue.writeBuffer(zBuf, 0, indata.z_m);
      this.device.queue.writeBuffer(solBuf, 0, solData);

      const bindGroup = this.device.createBindGroup({
        layout: this.pipeline.getBindGroupLayout(0),
        entries: [
          { binding: 0, resource: { buffer: paramsBuf } },
          { binding: 1, resource: { buffer: zBuf } },
          { binding: 2, resource: { buffer: solBuf } },
          { binding: 3, resource: { buffer: svfBuf } },
          { binding: 4, resource: { buffer: solUtBuf } },
        ],
      });

      const enc = this.device.createCommandEncoder();
      const pass = enc.beginComputePass();
      pass.setPipeline(this.pipeline);
      pass.setBindGroup(0, bindGroup);
      pass.dispatchWorkgroups(Math.ceil(indata.nx / 8), Math.ceil(indata.ny / 8), 1);
      pass.end();
      enc.copyBufferToBuffer(svfBuf, 0, svfLasBuf, 0, faltBytes);
      enc.copyBufferToBuffer(solUtBuf, 0, solLasBuf, 0, faltBytes);
      this.device.queue.submit([enc.finish()]);

      await Promise.all([svfLasBuf.mapAsync(MAP_MODE_READ), solLasBuf.mapAsync(MAP_MODE_READ)]);
      const svf = new Float32Array(svfLasBuf.getMappedRange().slice(0));
      const soltimmar_h = new Float32Array(solLasBuf.getMappedRange().slice(0));
      svfLasBuf.unmap();
      solLasBuf.unmap();

      return {
        svf,
        soltimmar_h,
        kortid_ms: performance.now() - t0,
        berakningsplats: BERAKNINGSPLATS_WEBGPU,
      };
    } finally {
      paramsBuf.destroy();
      zBuf.destroy();
      solBuf.destroy();
      svfBuf.destroy();
      solUtBuf.destroy();
      svfLasBuf.destroy();
      solLasBuf.destroy();
    }
  }

  /** Frigör WebGPU-enheten vid avmontering. */
  avsluta(): void {
    this.device.destroy();
  }
}
