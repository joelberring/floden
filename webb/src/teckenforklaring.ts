/* Teckenförklaring för ett rasterlager (färgskala eller klasser) i klarspråk. */
import type { LagerPost } from "./fragor-typer.ts";
import { osakerhetNotisText } from "./kartforklaring.ts";
import { talFormat } from "./tal.ts";

const TAL = talFormat(2);

function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export interface TeckenforklaringVal {
  /** Visa tydlig notis om att osäkerhetsintervall avser läget före. */
  osakerhetFore?: boolean;
  /** Egen liten notis under skalan (ersätter standardnotisen om satt). */
  notis?: string;
  /** Tydlig 1-radig etikett om vilket regn kartan visar (ovanför skalan). */
  regnRad?: string;
}

/** Rubrik med enhet när enheten inte redan ingår i rubriktexten. */
export function rubrikMedEnhet(rubrik: string, enhet: string): string {
  const ren = rubrik.trim();
  const e = enhet.trim();
  if (!e || ren.includes(e)) return ren;
  return `${ren} (${e})`;
}

/** Färgskalan för lagret: gradient, färgklasser eller blå/röd skillnadsskala. */
export function skalaHtml(l: LagerPost): string {
  const tf = l.teckenforklaring;
  const enhet = l.enhet ? ` ${esc(l.enhet)}` : "";
  if (tf.typ === "kontinuerlig") {
    return `<div class="skala-rad"><span class="mono">${TAL.format(tf.min)}${enhet}</span>
      <span class="skala-band" style="background:linear-gradient(to right, ${tf.stopp.join(",")})"></span>
      <span class="mono">${TAL.format(tf.max)}${enhet}</span></div>`;
  }
  if (tf.typ === "klasser") {
    return `<ul class="klass-lista">${tf.klasser
      .map((k) => {
        const text = k.etikett
          ? `${esc(k.etikett)}${l.enhet && !k.etikett.includes(l.enhet) ? enhet : ""}`
          : `från ${TAL.format(k.fran)}${enhet}`;
        return `<li><span class="ruta" style="background:${k.farg}"></span><span class="mono">${text}</span></li>`;
      })
      .join("")}</ul>`;
  }
  if (tf.typ === "kategorier") {
    return `<ul class="klass-lista">${tf.kategorier
      .map((k) => {
        const etikett = k.etikett ? ` · ${esc(k.etikett)}` : enhet;
        return `<li><span class="ruta" style="background:${k.farg}"></span><span class="mono">${TAL.format(k.varde)}${etikett}</span></li>`;
      })
      .join("")}</ul>`;
  }
  return `<div class="skala-rad"><span class="mono">−${TAL.format(tf.max)}${enhet}</span>
      <span class="skala-band" style="background:linear-gradient(to right, ${tf.farg_minskning}, #f7f5ee, ${tf.farg_okning})"></span>
      <span class="mono">+${TAL.format(tf.max)}${enhet}</span></div>
      <p class="dampad liten-text">Blå: minskning, röd: ökning (efter minus före). Ofärgat: ändringen är högst ${TAL.format(tf.troskel)}${enhet}.</p>`;
}

/** Teckenförklaring med tydlig rubrik, färgrutor, enheter och valfri notis om osäkerhetsintervall. */
export function teckenforklaringHtml(l: LagerPost, rubrik: string = l.namn, val: TeckenforklaringVal = {}): string {
  const titel = rubrikMedEnhet(rubrik || l.etikett || l.namn, l.enhet);
  const notis = val.notis ?? osakerhetNotisText(Boolean(val.osakerhetFore));
  return `
    <div class="teckenforklaring">
      ${val.regnRad ? `<p class="tecken-regn">${esc(val.regnRad)}</p>` : ""}
      <h3 class="etikett-liten">${esc(titel)}</h3>
      ${skalaHtml(l)}
      ${notis ? `<p class="tecken-notis">${esc(notis)}</p>` : ""}
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
