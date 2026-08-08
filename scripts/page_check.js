// ============================================================================
// Sichtprüfung einer beliebigen Seite hinter Basic Auth (Redpanda Console,
// QuestDB-Konsole). Prüft nicht nur den Status-Code, sondern ob die SPA im
// Browser wirklich lädt — Assets und XHR laufen bei diesen UIs über eigene
// Pfade und können hinter einem Proxy stolpern, ohne dass curl es merkt.
//
//   URL=https://… NAME=console node /check.js
// ============================================================================
const { chromium } = require('playwright');

const url = process.env.URL;
const name = process.env.NAME || 'page';
const user = process.env.BASIC_AUTH_USER || 'admin';
const pass = process.env.PW || '';
const outDir = process.env.OUT_DIR || '/workspace/artifacts';

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const context = await browser.newContext({
    httpCredentials: { username: user, password: pass },
    viewport: { width: 1600, height: 1000 },
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();

  const failed = [];
  page.on('requestfailed', (r) => failed.push(`${r.failure()?.errorText} ${r.url().slice(0, 90)}`));
  page.on('response', (r) => { if (r.status() >= 400) failed.push(`HTTP ${r.status()} ${r.url().slice(0, 90)}`); });

  await page.goto(url, { waitUntil: 'networkidle', timeout: 40000 });
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${outDir}/${name}.png` });

  const text = (await page.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ').trim();
  console.log(`${name}: ${text.slice(0, 180)}`);
  console.log(`${name}: fehlgeschlagene Requests: ${failed.length ? failed.slice(0, 4).join(' | ') : 'keine'}`);

  await browser.close();
  process.exit(failed.length === 0 && text.length > 20 ? 0 : 1);
})();
