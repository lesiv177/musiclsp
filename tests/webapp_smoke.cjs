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
      }, limits: { playlists: 5, offline_pack: false }, features: [] };
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
