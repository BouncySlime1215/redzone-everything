// playbook=BTN-06 in a real browser: the phone loses the app, says so, and resyncs when it's back.
//   node tests/js/phone_offline.cjs        (tests/test_phone_offline.py runs it; skipped without Playwright)
// The real phone page (redzone/web/static) against a small fake app on 127.0.0.1 (no RedZone, no
// network) and a fake page clock. Prints one "@@{json}" line of what it saw; the Python test grades it.
const fs = require("fs");
const http = require("http");
const path = require("path");
const { chromium } = require("playwright");

const STATIC = path.resolve(__dirname, "../../redzone/web/static");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".json": "application/json" };

(async () => {
  const state = JSON.parse(fs.readFileSync(path.join(STATIC, "mock_state.json"), "utf8"));
  const seen = { posts: [], reloads: 0 };
  let up = true, back = false;
  const streams = new Set();
  const json = (res, body, code = 200) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); };
  const server = http.createServer((req, res) => {
    if (!up) return req.socket.destroy();                          // the Mac is unreachable
    const u = new URL(req.url, "http://x");
    if (u.pathname === "/") return fs.createReadStream(path.join(STATIC, "index.html")).pipe(res.writeHead(200, { "Content-Type": "text/html" }));
    if (u.pathname.startsWith("/static/")) {
      const f = path.join(STATIC, path.normalize(u.pathname.slice(8)));
      if (!f.startsWith(STATIC) || !fs.existsSync(f)) return json(res, {}, 404);
      res.writeHead(200, { "Content-Type": TYPES[path.extname(f)] || "application/octet-stream" });
      return fs.createReadStream(f).pipe(res);
    }
    if (u.pathname === "/api/state") { if (back) seen.reloads++; return json(res, state); }
    if (u.pathname === "/api/stream") {
      if (back) seen.reloads++;
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      res.write(`event: state\ndata: ${JSON.stringify(state)}\n\n`);
      streams.add(res);
      req.on("close", () => streams.delete(res));
      return;
    }
    if (u.pathname === "/api/action" && req.method === "POST") {
      let raw = "";
      req.on("data", (c) => (raw += c));
      req.on("end", () => {
        const b = JSON.parse(raw);
        seen.posts.push(b);
        if (b.action === "pause") state.mode.paused = true;
        json(res, { ok: true, message: "Auto-switching paused", id: b.id, duplicate: false, state: "done" });
      });
      return;
    }
    json(res, {}, 404);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;

  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await ctx.route((url) => !url.href.startsWith(base), (route) => route.abort());   // fonts etc.
  await page.clock.install();
  const pill = () => page.$eval("#conn", (el) => el.dataset.state);
  const banner = () => page.$$eval('[data-key="b:offline"]', (els) => els.map((e) => e.textContent));
  const tick = async (ms) => { await page.clock.runFor(ms); await page.waitForTimeout(30); };

  await page.goto(base + "/");
  await page.waitForFunction(() => document.querySelector("#conn")?.dataset.state === "live", null, { timeout: 10000 });
  seen.start = { pill: await pill(), banner: await banner() };

  // The phone loses Wi-Fi: the stream drops and a Pause tapped now waits for the network.
  up = false;
  for (const s of streams) s.socket.destroy();
  await ctx.setOffline(true);
  await page.evaluate(async () => {
    const m = await import("/static/rz-api.js?v=43");
    m.sendTap({ action: "pause" }).then((r) => (window.__tapDone = r), (e) => (window.__tapErr = String(e)));
  });
  await tick(3000);
  seen.early = { pill: await pill(), banner: await banner() };     // a blip: no banner yet
  for (let i = 0; i < 5; i++) await tick(2000);                      // 13 s offline
  seen.offline = { pill: await pill(), banner: await banner(), posts: seen.posts.length,
                   tapErr: await page.evaluate(() => window.__tapErr || null) };

  // Back: the tap lands once with its id, the page reloads the app's state, the banner goes.
  up = true; back = true;
  await ctx.setOffline(false);
  for (let i = 0; i < 6 && !(await page.evaluate(() => window.__tapDone || window.__tapErr)); i++) await tick(1000);
  for (let i = 0; i < 6 && (await pill()) !== "live"; i++) await tick(1000);
  seen.back = { pill: await pill(), banner: await banner(), tap: await page.evaluate(() => window.__tapDone || null),
                tapErr: await page.evaluate(() => window.__tapErr || null),
                paused: await page.evaluate(async () => (await import("/static/rz-core.js?v=41")).app.S?.mode?.paused ?? null) };
  await browser.close();
  server.close();
  console.log("@@" + JSON.stringify(seen));
  process.exit(0);
})().catch((e) => { console.log("@@" + JSON.stringify({ error: String((e && e.stack) || e) })); process.exit(1); });
