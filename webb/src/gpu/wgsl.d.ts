/* Typdeklaration för Vite `?raw`-import av WGSL compute shaders. */
declare module "*.wgsl?raw" {
  const kod: string;
  export default kod;
}
