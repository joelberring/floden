/* Teckenförklaring för ett rasterlager (färgskala eller klasser) i klarspråk. */
import type { LagerPost } from "./fragor-typer";
import { talFormat } from "./tal.ts";

const TAL = talFormat(2);

function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Färgskalan för lagret: gradient, färgklasser eller blå/röd skillnadsskala. */
export function skalaHtml(l: LagerPost): string {
  const tf = l.teckenforklaring;
  const enhet = l.enhet ? ` ${esc(l.enhet)}` : "";
  if (tf.typ === "kontinuerlig") {
    return `<div class="skala-rad"><span class="mono">${TAL.format(tf.min)}</span>
      <span class="skala-band" style="background:linear-gradient(to right, ${tf.stopp.join(",")})"></span>
      <span class="mono">${TAL.format(tf.max)}${enhet}</span></div>`;
  }
  if (tf.typ === "klasser") {
    return `<ul class="klass-lista">${tf.klasser
      .map(
        (k) =>
          `<li><span class="ruta" style="background:${k.farg}"></span><span class="mono">från ${TAL.format(k.fran)}${enhet}</span></li>`,
      )
      .join("")}</ul>`;
  }
  if (tf.typ === "kategorier") {
    return `<ul class="klass-lista">${tf.kategorier
      .map((k) => `<li><span class="ruta" style="background:${k.farg}"></span><span class="mono">${TAL.format(k.varde)}${k.etikett ? ` (${esc(k.etikett)})` : ""}</span></li>`)
      .join("")}</ul>`;
  }
  return `<div class="skala-rad"><span class="mono">−${TAL.format(tf.max)}</span>
      <span class="skala-band" style="background:linear-gradient(to right, ${tf.farg_minskning}, #f5f5f5, ${tf.farg_okning})"></span>
      <span class="mono">+${TAL.format(tf.max)}${enhet}</span></div>
      <p class="dampad liten-text">Blå: minskning, röd: ökning (efter minus före). Ofärgat: ändringen är högst ${TAL.format(tf.troskel)}${enhet}.</p>`;
}

/** Teckenförklaring med rubrik. Tekniska uppgifter (filer, nedsampling) hör till teknisk information. */
export function teckenforklaringHtml(l: LagerPost, rubrik: string = l.namn): string {
  return `
    <div class="teckenforklaring">
      <h3 class="etikett-liten">${esc(rubrik)}</h3>
      ${skalaHtml(l)}
    </div>`;
}

/** Teknisk information om ett lager: skala, nedsampling och länkar till filerna med full upplösning. */
export function lagerTekniskHtml(l: LagerPost): string {
  const filer = l.geotiff
    .map((f) => `<li><a href="${esc(f.url)}" download>${esc(f.ruta ?? "hela området")}</a></li>`)
    .join("");
  const nedsamplat =
    l.nedsampling > 1
      ? `<li>Förhandsvisningen är nedsamplad ${l.nedsampling} gånger; filerna har full upplösning.</li>`
      : "";
  return `<li><span class="mono">${esc(l.id)}</span>: GeoTIFF (${l.geotiff.length} filer, full upplösning)
      <ul class="fil-lista">${filer}</ul></li>${nedsamplat}`;
}
