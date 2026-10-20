#!/usr/bin/env node
/**
 * render-page.js — renderiza una página con Chromium local y GUARDA la evidencia.
 *
 * Para qué existe: `web_extract` es barato y cachea, pero hay dos casos en los que miente
 * o no llega — cuando el dato lo inyecta JavaScript (no está en el HTML servido) y cuando
 * el dato depende de quién pregunta (precio por país, moneda, sesión). Ahí se renderiza.
 *
 * Dos reglas de diseño que valen más que el código:
 *
 *   1. Devuelve SÓLO texto limpio y las rutas. Nunca el HTML al contexto: el render de una
 *      página comercial son 130 KB y leerlos cuesta decenas de miles de tokens.
 *   2. Todo lo que ve se guarda en disco con su ficha de procedencia (`*.meta.json` con la
 *      URL final, el estado y la fecha), porque una cita que no se puede comprobar contra
 *      lo que se leyó no sirve. El verificador lee esos archivos.
 *
 * Uso:
 *   node render-page.js <url> [más urls...] --dir <carpeta> [--need "texto"] [--wait 2000]
 *                       [--images] [--json]
 *
 *   --dir     dónde guardar (por defecto ./evidence)
 *   --need    comprueba si ese texto aparece en lo renderizado; sale en el resultado como
 *             `need_found`. Es el disparador mecánico: si es false, la página no trae el dato.
 *   --wait    milisegundos extra de asentamiento después de networkidle
 *   --images  no bloquea imágenes/fuentes (por defecto se bloquean: más rápido y no aportan texto)
 *   --json    una línea JSON por URL (recomendado para el worker)
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

function cargarPlaywright() {
  for (const candidato of ["playwright", path.join(process.env.HOME || "", "node_modules/playwright")]) {
    try {
      return require(candidato);
    } catch (e) {
      /* siguiente */
    }
  }
  console.error("no encontré el módulo playwright (probé 'playwright' y $HOME/node_modules/playwright)");
  process.exit(3);
}

function args(argv) {
  const o = { urls: [], dir: "evidence", need: null, wait: 1500, images: false, json: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dir") o.dir = argv[++i];
    else if (a === "--need") o.need = argv[++i];
    else if (a === "--wait") o.wait = parseInt(argv[++i], 10) || 0;
    else if (a === "--images") o.images = true;
    else if (a === "--json") o.json = true;
    else if (!a.startsWith("--")) o.urls.push(a);
  }
  if (!o.urls.length) {
    console.error("falta la URL. node render-page.js <url> --dir <carpeta> [--need texto]");
    process.exit(2);
  }
  return o;
}

function slugDe(url) {
  let host = "page";
  try {
    host = new URL(url).hostname.replace(/^www\./, "");
  } catch (e) {
    /* url rara */
  }
  const hash = crypto.createHash("sha256").update(url).digest("hex").slice(0, 8);
  return `${host}-${hash}`;
}

async function renderizar(ctx, url, o) {
  const pagina = await ctx.newPage();
  const resultado = {
    url,
    final_url: null,
    status: null,
    title: "",
    chars: 0,
    txt: null,
    html: null,
    full: null,
    meta: null,
    need_found: null,
    error: null,
    ms: 0,
  };
  const t0 = Date.now();
  try {
    const resp = await pagina.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    resultado.status = resp ? resp.status() : null;
    resultado.final_url = pagina.url();
    try {
      await pagina.waitForLoadState("networkidle", { timeout: 15000 });
    } catch (e) {
      /* networkidle no siempre llega: no es un fallo, se sigue */
    }
    if (o.wait) await pagina.waitForTimeout(o.wait);
    resultado.title = await pagina.title();
    // El texto visible es lo que un humano leería; el HTML queda en disco para el verificador.
    const texto = await pagina.evaluate(() => document.body.innerText);
    resultado.chars = texto.length;
    if (o.need !== null) resultado.need_found = texto.includes(o.need);
    const base = path.join(o.dir, slugDe(url));
    fs.mkdirSync(o.dir, { recursive: true });
    fs.writeFileSync(`${base}.txt`, texto);
    const html = await pagina.content();
    fs.writeFileSync(`${base}.html`, html);
    // El texto visible (`innerText`) es lo que el worker lee sin gastar tokens, pero deja
    // fuera lo que está oculto (pestañas, FAQ plegadas) y eso también es fuente. El texto
    // completo baja el DOM a texto con separación entre nodos: es contra esto que el
    // verificador comprueba las citas.
    const completo = html
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<!--[\s\S]*?-->/g, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    fs.writeFileSync(`${base}.full.txt`, completo);
    fs.writeFileSync(
      `${base}.meta.json`,
      JSON.stringify(
        {
          url,
          final_url: resultado.final_url,
          status: resultado.status,
          title: resultado.title,
          fetched_at: new Date().toISOString(),
          chars: resultado.chars,
          via: "playwright-local",
        },
        null,
        2
      )
    );
    resultado.txt = `${base}.txt`;
    resultado.html = `${base}.html`;
    resultado.full = `${base}.full.txt`;
    resultado.meta = `${base}.meta.json`;
  } catch (e) {
    resultado.error = String(e.message || e).split("\n")[0].slice(0, 200);
  }
  resultado.ms = Date.now() - t0;
  await pagina.close();
  return resultado;
}

(async () => {
  const o = args(process.argv.slice(2));
  const { chromium } = cargarPlaywright();
  const navegador = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const ctx = await navegador.newContext({
    locale: "es-PA",
    timezoneId: "America/Panama",
    viewport: { width: 1440, height: 900 },
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  });
  if (!o.images) {
    // imágenes, fuentes y media no aportan texto y son la mayor parte del tiempo de carga
    await ctx.route("**/*", (ruta) => {
      const tipo = ruta.request().resourceType();
      if (tipo === "image" || tipo === "font" || tipo === "media") ruta.abort();
      else ruta.continue();
    });
  }

  const resultados = [];
  const tanda = 3;
  for (let i = 0; i < o.urls.length; i += tanda) {
    const grupo = o.urls.slice(i, i + tanda);
    resultados.push(...(await Promise.all(grupo.map((u) => renderizar(ctx, u, o)))));
  }
  await navegador.close();

  if (o.json) {
    for (const r of resultados) console.log(JSON.stringify(r));
  } else {
    for (const r of resultados) {
      if (r.error) {
        console.log(`✗ ${r.url}\n   ${r.error} (${r.ms} ms)`);
        continue;
      }
      const need = r.need_found === null ? "" : r.need_found ? " · el texto buscado ESTÁ" : " · el texto buscado NO está";
      console.log(
        `✓ ${r.status} ${r.url}\n   ${r.title.slice(0, 70)} · ${r.chars} chars · ${r.ms} ms${need}\n   ${r.txt}`
      );
    }
  }
  const fallos = resultados.filter((r) => r.error).length;
  process.exit(fallos === resultados.length ? 1 : 0);
})();
