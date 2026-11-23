/*
 * Upptäckt av WebGPU: finns `navigator.gpu`, ger den en adapter, och vilka gränser har den?
 * `nav` kan skickas in så att logiken går att testa utan webbläsare.
 */

export interface GpuAdapterInfo {
  vendor: string;
  architecture: string;
  device: string;
  description: string;
}

export interface WebGpuStatus {
  tillganglig: boolean;
  /** Svensk text som visas för användaren, alltid satt. */
  meddelande: string;
  adapter: GpuAdapterInfo | null;
  begransningar: Record<string, number> | null;
  funktioner: string[];
}

interface NavigatorLike {
  gpu?: {
    requestAdapter(): Promise<AdapterLike | null>;
  };
}
interface AdapterLike {
  info?: Partial<GpuAdapterInfo>;
  requestAdapterInfo?: () => Promise<Partial<GpuAdapterInfo>>;
  limits?: Record<string, unknown>;
  features?: Iterable<string>;
}

/** Begränsningar som är relevanta för skuggarna (buffertstorlek, arbetsgrupper). */
const VIKTIGA_BEGRANSNINGAR = [
  "maxBufferSize",
  "maxStorageBufferBindingSize",
  "maxStorageBuffersPerShaderStage",
  "maxComputeWorkgroupSizeX",
  "maxComputeWorkgroupSizeY",
  "maxComputeInvocationsPerWorkgroup",
  "maxComputeWorkgroupsPerDimension",
  "maxBindGroups",
  "maxUniformBufferBindingSize",
];

export const MEDDELANDE_SAKNAS =
  "WebGPU saknas i den här webbläsaren (navigator.gpu är inte definierad). Prova en aktuell " +
  "Chrome eller Edge på en dator med grafikkort och uppdaterade drivrutiner. Provet kan inte " +
  "köras här; serverns benchmark (floden benchmark) och facit fungerar som vanligt.";
export const MEDDELANDE_INGEN_ADAPTER =
  "WebGPU finns i webbläsaren men ingen grafikadapter gick att få (requestAdapter gav null). " +
  "Grafikdrivrutinen kan vara svartlistad eller hårdvaruacceleration avstängd.";

export async function upptackaWebGpu(nav: NavigatorLike | undefined): Promise<WebGpuStatus> {
  const tom = { adapter: null, begransningar: null, funktioner: [] as string[] };
  if (!nav || !nav.gpu) return { tillganglig: false, meddelande: MEDDELANDE_SAKNAS, ...tom };
  let adapter: AdapterLike | null;
  try {
    adapter = await nav.gpu.requestAdapter();
  } catch (e) {
    const text = e instanceof Error ? e.message : String(e);
    return { tillganglig: false, meddelande: `${MEDDELANDE_INGEN_ADAPTER} (${text})`, ...tom };
  }
  if (!adapter) return { tillganglig: false, meddelande: MEDDELANDE_INGEN_ADAPTER, ...tom };
  let info: Partial<GpuAdapterInfo> = adapter.info ?? {};
  if (!adapter.info && typeof adapter.requestAdapterInfo === "function") {
    try {
      info = await adapter.requestAdapterInfo();
    } catch {
      info = {};
    }
  }
  const begransningar: Record<string, number> = {};
  for (const namn of VIKTIGA_BEGRANSNINGAR) {
    const v = adapter.limits?.[namn];
    if (typeof v === "number") begransningar[namn] = v;
  }
  return {
    tillganglig: true,
    meddelande: "WebGPU finns. Provet kan köras.",
    adapter: {
      vendor: info.vendor ?? "",
      architecture: info.architecture ?? "",
      device: info.device ?? "",
      description: info.description ?? "",
    },
    begransningar,
    funktioner: adapter.features ? [...adapter.features].sort() : [],
  };
}

/** Ryms en buffert av `bytes` byte i adapterns gränser? Falskt när gränsen saknas eller är för liten. */
export function ryms(status: WebGpuStatus, bytes: number): boolean {
  const g = status.begransningar;
  if (!g) return false;
  const lim = Math.min(g.maxStorageBufferBindingSize ?? 0, g.maxBufferSize ?? 0);
  return bytes <= lim;
}
