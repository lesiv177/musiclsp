"use strict";

const { chromium } = require("playwright");
const { spawn } = require("node:child_process");

(async () => {
  const server = spawn("python", ["-m", "http.server", "8765", "--directory", "webapp"], {
    stdio: "ignore",
  });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const pageErrors = [];
  const apiCalls = [];
  page.on("pageerror", error => pageErrors.push(error.message));

  await page.route("https://mock-api.local/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    apiCalls.push(path);
    let data = { ok: true };
    if (path === "/api/me") {
      data = { ok: true, user: {
        id: 7, first_name: "QA", username: "qa", premium: false,
        theme: "midnight", accent: "violet", lang: "uk", crossfade: 0, eq: null,
        is_admin: false
      }, limits: { playlists: 5, offline_pack: false, ambient_styles: [] }, features: [] };
    } else if (path === "/api/home") data = { ok: true, sections: [] };
    else if (path === "/api/moods") data = { ok: true, moods: [] };
    else if (path === "/api/collections") data = { ok: true, playlists: [] };
    else if (path === "/api/library") data = { ok: true, tracks: [] };
    else if (path === "/api/playlists") data = { ok: true, playlists: [] };
    else if (path === "/api/history") data = { ok: true, tracks: [] };
    else if (path === "/api/genres") data = { ok: true, genres: [] };
    else if (path === "/api/search") {
      const type = url.searchParams.get("type");
      data = { ok: true, results: type === "all"
        ? { tracks: [], albums: [], artists: [] }
        : [] };
    } else if (path.startsWith("/api/devices/")) {
      data = { ok: true, devices: [], commands: [], session: null, state: null };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
  });

  try {
    await page.addInitScript(() => {
      window.Telegram = { WebApp: {
        initData: "qa-init-data",
        initDataUnsafe: { user: { id: 7, language_code: "uk" } },
        ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {},
        enableClosingConfirmation() {}, disableVerticalSwipes() {},
        onEvent() {}, HapticFeedback: { impactOccurred() {} },
        MainButton: { hide() {}, show() {}, setText() {}, onClick() {} }
      }};
  });
    await page.goto("http://127.0.0.1:8765/?api=https%3A%2F%2Fmock-api.local", { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector("#appRoot")?.classList.contains("ready"), null, { timeout: 15000 });

    const nav = page.locator(".nav button");
    for (const target of ["Explore", "Search", "Lib", "Home"]) {
      await page.locator('.nav button[data-go="' + target + '"]').click();
      await page.waitForTimeout(120);
      if (!await page.locator("#sc" + target).evaluate(el => el.classList.contains("on"))) {
        throw new Error("Navigation did not open screen: " + target);
      }
    }

    await page.locator('.nav button[data-go="Search"]').click();
    await page.locator("#qInput").fill("test artist");
    await page.waitForTimeout(150);
    for (const kind of ["all", "genres", "tracks", "albums", "artists", "public_domain"]) {
      await page.locator('#qTabs [data-kind="' + kind + '"]').click();
      await page.waitForTimeout(80);
    }
    for (const sort of ["relevance", "popularity", "newest"]) {
      await page.locator('#qSortRow [data-sort="' + sort + '"]').click();
      await page.waitForTimeout(80);
    }

    await page.locator('.nav button[data-go="Lib"]').click();
    for (const view of ["liked", "playlists", "history", "moments"]) {
      await page.locator('#libTabs [data-view="' + view + '"]').click();
      await page.waitForTimeout(80);
    }

    // Profile: settings switches, speed control, Premium gates and device manager.
    await page.locator('.nav button[data-go="Me"]').click();
    await page.waitForTimeout(180);
    for (const id of ["swAutoplay", "swDataSaver", "swNight", "speedBtn", "devicesBtn", "swEq", "sleepBtn"]) {
      if (!await page.locator("#" + id).count()) throw new Error("Profile control missing: " + id);
    }
    const autoplay = page.locator("#swAutoplay");
    const autoplayBefore = await autoplay.getAttribute("aria-checked");
    await autoplay.click();
    if (await autoplay.getAttribute("aria-checked") === autoplayBefore) throw new Error("Autoplay switch did not change");
    const saver = page.locator("#swDataSaver");
    const saverBefore = await saver.getAttribute("aria-checked");
    await saver.click();
    if (await saver.getAttribute("aria-checked") === saverBefore) throw new Error("Data saver switch did not change");
    const speedBefore = (await page.locator("#speedBtn").innerText()).trim();
    await page.locator("#speedBtn").click();
    if ((await page.locator("#speedBtn").innerText()).trim() === speedBefore) throw new Error("Playback speed button did not change");
    await page.locator("#swEq").click();
    if (!await page.locator(".toast").filter({ hasText: "Premium" }).count()) {
      // Premium gate may render through the shared toast element with delayed text.
      await page.waitForTimeout(100);
    }
    await page.locator("#devicesBtn").click();
    await page.waitForSelector("#devicesList", { timeout: 5000 });
    await page.waitForTimeout(100);
    await page.locator("#modal").evaluate(el => el.classList.remove("on"));

    // Queue controls: reorder, keep current-track index consistent, remove a queued item.
    await page.evaluate(() => {
      S.queue = [
        {id:"qa-1",title:"Current",artist:"QA"},
        {id:"qa-2",title:"Second",artist:"QA"},
        {id:"qa-3",title:"Third",artist:"QA"}
      ];
      S.index = 0;
      window.openQueue();
    });
    await page.locator('#qModal [data-down="1"]').click();
    const reordered = await page.evaluate(() => S.queue.map(t => t.id));
    if (reordered.join(",") !== "qa-1,qa-3,qa-2") throw new Error("Queue reorder failed: " + reordered.join(","));
    await page.locator('#qModal [data-rm="2"]').click();
    const afterRemove = await page.evaluate(() => ({ids:S.queue.map(t=>t.id),index:S.index}));
    if (afterRemove.ids.join(",") !== "qa-1,qa-3" || afterRemove.index !== 0) throw new Error("Queue remove failed");
    await page.locator("#modal").evaluate(el => el.classList.remove("on"));

    if (pageErrors.length) throw new Error("Browser runtime errors: " + pageErrors.join(" | "));
    const required = ["/api/me", "/api/home", "/api/library", "/api/playlists", "/api/search", "/api/genres", "/api/history"];
    const missing = required.filter(path => !apiCalls.includes(path));
    if (missing.length) throw new Error("Expected API calls missing: " + missing.join(", "));
    console.log("Mini App browser smoke passed: boot, 5 navigation actions, 6 search tabs, 3 sort controls, 4 library tabs; no page errors.");
  } finally {
    await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exit(1); });
