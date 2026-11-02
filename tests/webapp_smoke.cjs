"use strict";
const { chromium } = require("playwright");
const { spawn } = require("node:child_process");

(async () => {
  const server = spawn("python", ["-m", "http.server", "8765", "--directory", "webapp"], { stdio: "ignore" });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const pageErrors = [];
  const apiCalls = [];
  const apiRequests = [];
  const scriptResponses = [];
  page.on("pageerror", error => pageErrors.push({message:error.message,stack:error.stack}));
  page.on("console", msg => { if (msg.type() === "error" && !/websocket/i.test(msg.text())) pageErrors.push({console:msg.text(),location:msg.location()}); });
  page.on("response", response => { if (response.request().resourceType() === "script") scriptResponses.push({url:response.url(),status:response.status(),contentType:response.headers()["content-type"]||""}); });
  page.on("requestfailed", request => { if (request.resourceType() === "script") scriptResponses.push({url:request.url(),failure:request.failure()?.errorText||"failed"}); });

  await page.route("https://mock-api.local/**", async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    apiCalls.push(path);
    apiRequests.push({ path, method: route.request().method(), body: route.request().postDataJSON() });
    let data = { ok: true };
    if (path === "/api/me") data = { ok:true, user:{id:7,name:"QA Listener",premium:false,theme:"midnight",lang:"uk",crossfade:0,eq:null}, limits:{playlists:5,offline_pack:false,ambient_styles:[],wrapped:true}, features:[] };
    else if (path === "/api/home") data = { ok:true, sections:[{id:"recent",title:"Recent",tracks:[{id:"qa-seed",title:"Recent track",artist:"QA Artist",cover:"",duration:180,duration_str:"3:00"}]},{id:"popular",title:"Popular",tracks:[{id:"qa-pop",title:"Popular track",artist:"QA Artist",cover:"",duration:180}]}] };
    else if (path === "/api/moods") data = {ok:true,moods:[{key:"calm",label:"Спокій",locked:false}]};
    else if (path === "/api/genres") data = {ok:true,genres:[{tag:"jazz",label:"Jazz"}]};
    else if (path === "/api/stats") data = {ok:true,stats:{plays:12,hours:3,library:1,top_artists:[]}};
    else if (path === "/api/library") data = {ok:true,tracks:[{id:"qa-liked",title:"Saved track",artist:"QA Artist",cover:"",duration:180,duration_str:"3:00"}]};
    else if (path === "/api/playlists") data = {ok:true,playlists:[]};
    else if (path === "/api/history") data = {ok:true,tracks:[]};
    else if (path === "/api/search") {
      const q=url.searchParams.get("q"),type=url.searchParams.get("type");
      data={ok:true,results:type==="all"?{tracks:[{id:"qa-search",title:"Search result",artist:"QA Artist",cover:"",duration:180}],albums:[{id:"jam:album-1",name:"QA Album",cover:""}],artists:[{id:"jam:artist-1",name:"QA Artist",cover:""}]}:
        (type==="artists"?[{id:"jam:artist-1",name:"QA Artist",cover:""}]:type==="albums"?[{id:"jam:album-1",name:"QA Album",cover:""}]:(q?[{id:"qa-search",title:"Search result",artist:"QA Artist",cover:"",duration:180}]:[]))};
    } else if (path === "/api/artist/jam%3Aartist-1" || path === "/api/artist/jam:artist-1" || path === "/api/album/jam%3Aalbum-1" || path === "/api/album/jam:album-1") data={ok:true,tracks:[{id:"qa-collection-track",title:"Collection track",artist:"QA Artist",cover:"",duration:180}]};
    else if (path === "/api/mood/calm") data={ok:true,tracks:[{id:"qa-mood",title:"Mood track",artist:"QA Artist",cover:"",duration:180}]};
    else if (path.startsWith("/api/home?tag=")) data={ok:true,sections:[{id:"genre",tracks:[{id:"qa-genre",title:"Jazz track",artist:"QA Artist",cover:"",duration:180}]}]};
    else if (path.startsWith("/api/devices/")) data={ok:true,devices:[],commands:[],session:null,state:null};
    else if (path === "/api/playlists" && route.request().method()==="POST") data={ok:true,playlist:{id:8,name:"QA playlist"}};
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(data)});
  });

  try {
    await page.addInitScript(() => {
      window.Telegram={WebApp:{version:"8.0",isVersionAtLeast:()=>true,CloudStorage:{getItem:(k,cb)=>cb(null,localStorage.getItem("tg:"+k)),setItem:(k,v,cb)=>{localStorage.setItem("tg:"+k,v);cb(null);},removeItem:(k,cb)=>{localStorage.removeItem("tg:"+k);cb(null);}},initData:"qa-init-data",initDataUnsafe:{user:{id:7,first_name:"QA",language_code:"uk"}},ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){},enableClosingConfirmation(){},disableVerticalSwipes(){},onEvent(){},HapticFeedback:{impactOccurred(){}},MainButton:{hide(){},show(){},setText(){},onClick(){}}}};
    });
    await page.goto("http://127.0.0.1:8765/?api=https%3A%2F%2Fmock-api.local",{waitUntil:"domcontentloaded"});
    await page.waitForFunction(()=>document.querySelector("#appRoot")?.classList.contains("ready"),null,{timeout:15000});
    try {
      await page.waitForSelector("#mlsp-v3 .v3-mobile-brand",{timeout:5000});
    } catch (error) {
      const diagnostics=await page.evaluate(()=>({
        v3Html:document.querySelector("#mlsp-v3")?.innerHTML?.slice(0,1800)||null,
        v3Api:typeof window.MLSPv3,
        mobileBrandCount:document.querySelectorAll("#mlsp-v3 .v3-mobile-brand").length,
        appClass:document.querySelector("#appRoot")?.className,
        mobileBrandDisplay:(()=>{const el=document.querySelector("#mlsp-v3 .v3-mobile-brand");return el?getComputedStyle(el).display:null})(),
        scriptStatus:[...document.scripts].map(s=>({src:s.src,loaded:!!s.src})),
        styles:[...document.styleSheets].map(s=>s.href).filter(Boolean),
        htmlHasV3Root:document.documentElement.innerHTML.includes("id=\"mlsp-v3\""),
        scriptsText:[...document.scripts].filter(s=>s.src).map(s=>({src:s.src,text:s.textContent.slice(0,100)}))
      }));
      throw new Error("V3 bootstrap diagnostic: "+JSON.stringify({diagnostics,pageErrors,scriptResponses,error:error.message}));
    }
    if (!(await page.locator("#v3-home").isVisible())) throw new Error("V3 home screen is not visible");
    const visibleLegacy=await page.locator("#scHome").evaluate(el=>getComputedStyle(el).display!=="none");
    if(visibleLegacy)throw new Error("Legacy app screen is still visible");

    for(const target of ["explore","search","library","profile","home"]){
      await page.locator('.v3-mobile-nav [data-page="'+target+'"]').first().click();
      await page.waitForFunction(t=>document.querySelector("#v3-"+t)?.classList.contains("active"),target);
    }
    await page.locator('.v3-mobile-nav [data-page="search"]').click();
    await page.locator("#v3SearchInput").fill("test artist");
    await page.waitForSelector("#v3Results .v3-track",{timeout:5000});
    if(!apiCalls.includes("/api/search"))throw new Error("V3 search did not call the search API");
    await page.waitForSelector('#v3Results [data-collection-kind="artists"]');
    await page.waitForSelector('#v3Results [data-collection-kind="albums"]');
    const artistRequest=page.waitForRequest(req=>new URL(req.url()).pathname.startsWith("/api/artist/"));
    await page.locator('#v3Results [data-collection-kind="artists"]').first().click();
    if(!(await artistRequest).url().includes("jam%3Aartist-1"))throw new Error("Artist selection requested the wrong collection");
    await page.locator('.v3-mobile-nav [data-page="search"]').click();
    await page.locator("#v3SearchInput").fill("qa album");
    await page.locator('#v3Kinds [data-kind="albums"]').click();
    await page.waitForSelector('#v3Results [data-collection-kind="albums"]');
    const albumRequest=page.waitForRequest(req=>new URL(req.url()).pathname.startsWith("/api/album/"));
    await page.locator('#v3Results [data-collection-kind="albums"]').first().click();
    if(!(await albumRequest).url().includes("jam%3Aalbum-1"))throw new Error("Album selection requested the wrong collection");

    await page.locator('.v3-mobile-nav [data-page="explore"]').first().click();
    await page.waitForSelector("#v3-explore [data-genre]");
    await page.locator("#v3-explore [data-genre]").first().click();
    await page.waitForSelector("#v3Results .v3-track");
    await page.locator('.v3-mobile-nav [data-page="explore"]').first().click();
    await page.locator("#v3-explore [data-mood]").first().click();
    await page.waitForSelector("#v3Results .v3-track");

    await page.locator('.v3-mobile-nav [data-page="library"]').first().click();
    await page.waitForSelector("#v3LibraryBody .v3-track");
    await page.locator('#v3-library [data-tab="history"]').click();
    await page.waitForSelector("#v3LibraryBody .v3-empty");
    await page.locator('#v3-library [data-tab="playlists"]').click();
    await page.locator("#v3Create").click();
    await page.locator("#plName").fill("QA playlist");
    await page.locator("#plDesc").fill("Created by browser smoke test");
    await page.locator('#modalBox button[onclick="createPlaylist()"]').click();
    await page.waitForFunction(()=>document.querySelector("#modal")?.classList.contains("on")===false);
    const createRequest=apiRequests.find(x=>x.path==="/api/playlists"&&x.method==="POST"&&x.body?.action==="create");
    if(!createRequest||createRequest.body.name!=="QA playlist"||createRequest.body.description!=="Created by browser smoke test")throw new Error("V3 playlist creation payload is incorrect");

    await page.locator('.v3-mobile-nav [data-page="profile"]').first().click();
    await page.waitForSelector("#v3-profile .v3-profile-hero");
    await page.locator('#v3-profile [data-prof="plan"]').first().click();
    await page.waitForSelector("#modal.on .plan-choice");
    await page.locator("#modal").evaluate(el=>el.classList.remove("on"));

    // The original device-control element and Connect runtime remain mounted.
    // Changing the account locale refreshes V3 copy without reloading the app.
    await page.evaluate(()=>{S.me.lang="en";document.documentElement.lang="en";});
    await page.waitForFunction(()=>document.querySelector('#mlsp-v3 [data-page="home"]')?.textContent.includes("Home"));
    await page.evaluate(()=>{S.me.lang="uk";document.documentElement.lang="uk";});
    await page.waitForFunction(()=>document.querySelector('#mlsp-v3 [data-page="home"]')?.textContent.includes("Головна"));

    // Every account theme must recolor V3 accent controls and panel surfaces.
    await page.evaluate(()=>window.MLSPv3.navigate("home"));
    await page.waitForSelector("#v3-home .v3-home-heading");
    const scrollCheck = await page.evaluate(async () => {
      // Grow the actual V3 app container: a body-level probe can pass even when
      // #appRoot is still position:fixed and the app itself cannot extend the document.
      const app = document.querySelector("#appRoot");
      const v3 = document.querySelector("#mlsp-v3");
      const originalMinHeight = v3.style.minHeight;
      v3.style.minHeight = Math.max(window.innerHeight * 2.5, 1600) + "px";
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const rootPosition = getComputedStyle(app).position;
      const documentHeight = document.documentElement.scrollHeight;
      const before = window.scrollY;
      window.scrollTo(0, documentHeight);
      await new Promise(resolve => requestAnimationFrame(resolve));
      const after = window.scrollY;
      v3.style.minHeight = originalMinHeight;
      window.scrollTo(0, before);
      return {rootPosition,documentHeight,viewport:window.innerHeight,after};
    });
    if (scrollCheck.rootPosition === "fixed" || scrollCheck.after <= 0)
      throw new Error("V3 app container does not participate in document scrolling: "+JSON.stringify(scrollCheck));
    const themeSamples = await page.evaluate(() => {
      const samples = {};
      for (const theme of ["midnight","ember","aurora","neon","paper"]) {
        applyTheme(theme);
        samples[theme] = {
          accent: getComputedStyle(document.querySelector(".v3-brand-mark")).backgroundColor,
          panel: getComputedStyle(document.querySelector(".v3-album-card")).backgroundColor
        };
      }
      applyTheme("midnight");
      return samples;
    });
    if (new Set(Object.values(themeSamples).map(x=>x.accent)).size !== 5)
      throw new Error("V3 accent did not change across all account themes: "+JSON.stringify(themeSamples));
    if (new Set(Object.values(themeSamples).map(x=>x.panel)).size !== 5)
      throw new Error("V3 panel did not change across all account themes: "+JSON.stringify(themeSamples));

    await page.evaluate(() => { window.cnWake = () => {}; openDevices(); });
    await page.waitForSelector("#devicesList",{timeout:5000});
    await page.locator("#modal").evaluate(el=>el.classList.remove("on"));
    for(const id of ["barRemote","devicesList","barOutput"])if(!(await page.locator("#"+id).count()))throw new Error("Connect control missing: "+id);

    // Queue management is part of the retained player runtime.
    await page.evaluate(()=>{window.eval('S.queue=[{id:"qa-1",title:"One",artist:"QA"},{id:"qa-2",title:"Two",artist:"QA"},{id:"qa-3",title:"Three",artist:"QA"}];S.index=0;');openQueue();});
    await page.locator('#qModal [data-down="0"]').click();
    const order=await page.locator("#qModal .row .t").allTextContents();
    if(order.join("|")!=="Two|One|Three")throw new Error("Queue reorder failed: "+order.join("|"));
    await page.locator('#qModal [data-rm="2"]').click();
    const remaining=await page.locator("#qModal .row .t").allTextContents();
    if(remaining.join("|")!=="Two|One")throw new Error("Queue removal failed: "+remaining.join("|"));
    if(pageErrors.length)throw new Error("Browser runtime errors: "+pageErrors.map(e=>e?.stack||e?.message||e?.console||JSON.stringify(e)).join(" | "));
    const required=["/api/me","/api/home","/api/library","/api/playlists","/api/search","/api/genres","/api/history","/api/stats"];
    const missing=required.filter(x=>!apiCalls.includes(x));
    if(missing.length)throw new Error("Expected API calls missing: "+missing.join(", "));
    console.log("MusicLSP V3 browser smoke passed: new navigation, search, discovery, library, playlists, profile, Premium, Connect control and queue management.");
  } finally { await browser.close();server.kill(); }
})().catch(error=>{console.error(error);process.exit(1);});