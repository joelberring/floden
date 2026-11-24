/*
 * Fördröjd körning (debounce): många anrop i följd ger en enda körning `ms` efter det sista. Används för
 * 3D-förhandsvisningen (R6), som inte ska byggas om för varje hörn som dras. Ren logik; timern kan bytas i test.
 */
export interface Tidtagare {
  satt(f: () => void, ms: number): unknown;
  rensa(id: unknown): void;
}

export const SYSTEMTID: Tidtagare = {
  satt: (f, ms) => setTimeout(f, ms),
  rensa: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
};

export interface Fordrojd {
  /** Begär en körning; en tidigare väntande körning ersätts. */
  (): void;
  /** Tar bort en väntande körning. */
  avbryt(): void;
  /** Körs direkt om en körning väntar (annars ingenting). */
  tom(): void;
  vantar(): boolean;
}

export function fordrojd(f: () => void, ms: number, tid: Tidtagare = SYSTEMTID): Fordrojd {
  let id: unknown = null;
  const kor = (): void => {
    id = null;
    f();
  };
  const g = (() => {
    if (id !== null) tid.rensa(id);
    id = tid.satt(kor, ms);
  }) as Fordrojd;
  g.avbryt = () => {
    if (id !== null) tid.rensa(id);
    id = null;
  };
  g.tom = () => {
    if (id === null) return;
    tid.rensa(id);
    kor();
  };
  g.vantar = () => id !== null;
  return g;
}
