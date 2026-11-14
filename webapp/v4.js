/* MusicLSP v4 — надбудова над наявним застосунком (плеєр і Connect не змінюються).
   1) нові фони у виборі тем; 2) новий екран тарифів Free/Premium з кнопкою «Купити Premium»;
   3) зручності для ПК (гарячі клавіші, встановлення як застосунок); 4) service worker. */
(function(){
  "use strict";
  var W=window, D=document;
  function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];}); }
  function safe(fn){ try{ return fn(); }catch(e){ return undefined; } }

  /* ── 1. Нові фони ── */
  var NEW_THEMES=[
    ["sunset","Sunset Drive","Синтвейв: сонце й неонова сітка"],
    ["ocean","Deep Ocean","Глибоке море, промені й бульбашки"],
    ["forest","Misty Forest","Нічний ліс, туман і світлячки"],
    ["city","City Nights","Нічне місто з вогнями вікон"],
    ["sakura","Sakura Dusk","Сутінки й пелюстки сакури"],
    ["desert","Desert Sun","Дюни під великим сонцем"],
    ["rain","Rainy Window","Дощ за вікном"],
    ["lava","Lava Core","Розжарена порода й іскри"],
    ["snow","Snowfall","Тихий снігопад"],
    ["graphite","Graphite","Мінімалізм у відтінках сірого"]
  ];
  var NEW_BASE={sunset:"#12002b",ocean:"#031a2b",forest:"#04120b",city:"#070a16",sakura:"#1a0b1f",
                desert:"#1c0f08",rain:"#070b13",lava:"#0d0402",snow:"#0b1626",graphite:"#0a0a0b"};
  safe(function(){
    if(typeof SPACE!=="undefined"){
      NEW_THEMES.forEach(function(t){ if(!SPACE.some(function(x){return x[0]===t[0];})) SPACE.push(t); });
    }
    if(typeof SPBASE!=="undefined") Object.keys(NEW_BASE).forEach(function(k){ SPBASE[k]=NEW_BASE[k]; });
    if(typeof spChrome==="function") spChrome();      // колір шапки Telegram під поточну тему
  });

  /* ── 2. Тарифи Free / Premium ── */
  var ROWS=[
    ["Каталог, пошук і чарти",true,true],
    ["Відтворення з відкритих джерел",true,true],
    ["Бібліотека, плейлисти, підписки на артистів",true,true],
    ["Рекомендації, добірки, Release Radar",true,true],
    ["Вибір теми й фону",true,true],
    ["Керування пристроями (Connect)",true,true],
    ["Hi-Fi звук, де дозволяє джерело",false,true],
    ["Еквалайзер на 10 смуг",false,true],
    ["Кросфейд і таймер сну",false,true],
    ["Завантаження MP3",false,true],
    ["Колір акценту, яскравість, автотема",false,true],
    ["Власна головна й швидкі кнопки плеєра",false,true],
    ["Приховування виконавців",false,true]
  ];
  function isPremium(){ return !!(W.S&&S.me&&S.me.premium); }
  function planHtml(){
    var p=isPremium(), until=p&&S.me.premium_until?String(S.me.premium_until).slice(0,10):"";
    var rows=ROWS.map(function(r){
      return '<div class="v4-row"><span class="v4-row-t">'+esc(r[0])+'</span>'
        +'<span class="v4-cell '+(r[1]?"yes":"no")+'" aria-label="Free: '+(r[1]?"так":"ні")+'">'+(r[1]?"✓":"—")+'</span>'
        +'<span class="v4-cell yes pro" aria-label="Premium: так">✓</span></div>';
    }).join("");
    var web=W.MLSPWeb&&W.MLSPWeb.isWebSession&&W.MLSPWeb.isWebSession();
    return '<div class="v4-plan">'
      +'<div class="v4-plan-top"><div><div class="v4-kicker">MUSICLSP</div><h2>Обери свій план</h2></div>'
      +'<button class="connect-close" type="button" onclick="closeModal()" aria-label="Закрити">✕</button></div>'
      +'<div class="v4-tiers">'
      +'<div class="v4-tier'+(!p?" cur":"")+'"><span class="v4-tier-name">Free</span><b>Безкоштовно</b><small>Усе для щоденного слухання</small>'+(!p?'<i class="v4-badge">Твій план</i>':"")+'</div>'
      +'<div class="v4-tier pro'+(p?" cur":"")+'"><span class="v4-tier-name">✦ Premium</span><b>Повний доступ</b><small>'+(p&&until?"Активний до "+esc(until):"Більше контролю й звуку")+'</small>'+(p?'<i class="v4-badge">Твій план</i>':"")+'</div>'
      +'</div>'
      +'<div class="v4-table" role="table" aria-label="Порівняння тарифів">'
      +'<div class="v4-row head" role="row"><span class="v4-row-t">Можливість</span><span class="v4-cell">Free</span><span class="v4-cell pro">Premium</span></div>'
      +rows+'</div>'
      +(p?'<p class="v4-note">Дякуємо, що ти з Premium.</p>'
         :'<button class="v4-buy" type="button" onclick="MLSPv4.buyPremium()">Купити Premium</button>'
          +'<button class="v4-promo" type="button" onclick="closeModal();openPromo()">Ввести промокод</button>')
      +(web?'<button class="v4-logout" type="button" onclick="MLSPWeb.logout()">Вийти з веб-сесії</button>':"")
      +'</div>';
  }
  function openPlan(){
    var html=planHtml();
    if(typeof W.showModal==="function") W.showModal(html);
    else {
      var modal=D.getElementById("modal"), box=D.getElementById("modalBox");
      if(modal&&box){ box.innerHTML=html; modal.classList.add("on"); }
    }
    safe(function(){ if(typeof tap==="function") tap(); });
  }
  function buyPremium(){
    if(typeof W.MLSP_BUY_PREMIUM==="function") return W.MLSP_BUY_PREMIUM();   // сюди підключимо оплату
    showModal('<div class="v4-plan"><div class="v4-plan-top"><div><div class="v4-kicker">PREMIUM</div><h2>Оплата скоро</h2></div>'
      +'<button class="connect-close" type="button" onclick="closeModal()" aria-label="Закрити">✕</button></div>'
      +'<p class="v4-note">Купівля Premium з’явиться найближчим часом. Поки що його можна активувати промокодом.</p>'
      +'<button class="v4-promo" type="button" onclick="closeModal();openPromo()">Ввести промокод</button>'
      +'<button class="v4-promo" type="button" onclick="MLSPv4.openPlan()">← До тарифів</button></div>');
  }
  /* openPlan — глобальна функція застосунку; усі кнопки «Premium/Тариф» викликають її за ім'ям */
  W.openPlan=openPlan;
  /* Ensure the dynamically rendered V3 profile plan action opens this overlay. */
  D.addEventListener("click",function(e){
    var b=e.target&&e.target.closest?e.target.closest('[data-prof="plan"]'):null;
    if(!b) return;
    e.preventDefault(); e.stopPropagation();
    if(e.stopImmediatePropagation) e.stopImmediatePropagation();
    openPlan();
  },true);

  /* ── 3. Зручності для ПК ── */
  var fine=safe(function(){ return W.matchMedia("(hover:hover) and (pointer:fine)").matches; });
  function typing(t){
    if(!t||!t.tagName) return false;
    var n=t.tagName.toLowerCase();
    return n==="input"||n==="textarea"||n==="select"||n==="button"||n==="a"||t.isContentEditable;
  }
  if(fine){
    D.addEventListener("keydown",function(e){
      if(e.defaultPrevented||e.ctrlKey||e.metaKey||e.altKey) return;
      if(D.getElementById("modal")&&D.getElementById("modal").classList.contains("on")) return;
      if(typing(e.target)) return;
      var id=null;
      if(e.code==="Space") id="barPlay"; else if(e.code==="ArrowRight") id="barNext"; else if(e.code==="ArrowLeft") id="barPrev";
      if(id){ var b=D.getElementById(id); if(b){ e.preventDefault(); b.click(); } return; }
      if(e.key==="/"){
        var inp=D.querySelector(".v3-top-search input,#v3-top-search input");
        if(inp){ e.preventDefault(); inp.focus(); }
      }
    });
  }

  /* встановлення як застосунок (Chrome/Edge): окреме вікно без панелі вкладок */
  var deferred=null;
  function standalone(){ return safe(function(){ return W.matchMedia("(display-mode: standalone)").matches||navigator.standalone; }); }
  function showInstall(){
    if(!deferred||standalone()||(W.MLSPWeb&&MLSPWeb.inTelegram&&MLSPWeb.inTelegram())) return;
    if(safe(function(){ return localStorage.getItem("mlsp_install_hide")==="1"; })) return;
    if(D.getElementById("v4Install")) return;
    var c=D.createElement("div"); c.id="v4Install"; c.className="v4-install";
    c.innerHTML='<button type="button" class="v4-install-go">Встановити як застосунок</button><button type="button" class="v4-install-x" aria-label="Сховати">✕</button>';
    D.body.appendChild(c);
    c.querySelector(".v4-install-go").onclick=function(){ var d=deferred; deferred=null; c.remove(); if(d){ d.prompt(); } };
    c.querySelector(".v4-install-x").onclick=function(){ safe(function(){ localStorage.setItem("mlsp_install_hide","1"); }); c.remove(); };
  }
  W.addEventListener("beforeinstallprompt",function(e){ e.preventDefault(); deferred=e; if(D.body) showInstall(); else D.addEventListener("DOMContentLoaded",showInstall); });
  W.addEventListener("appinstalled",function(){ deferred=null; var c=D.getElementById("v4Install"); if(c) c.remove(); });

  /* ── 4. Service worker (лише поза Telegram і лише через https/localhost) ── */
  safe(function(){
    var tgMode=W.MLSPWeb&&MLSPWeb.inTelegram&&MLSPWeb.inTelegram();
    var secure=location.protocol==="https:"||location.hostname==="localhost"||location.hostname==="127.0.0.1";
    if(!tgMode&&secure&&"serviceWorker" in navigator){
      W.addEventListener("load",function(){ navigator.serviceWorker.register("./sw.js").catch(function(){}); });
    }
  });

  /* ── 5. Профіль артиста: фото, біографія, дискографія з безкоштовних джерел метаданих ── */
  function wrapArtistProfile(){
    var orig=W.openArtistProfile;
    if(typeof orig!=="function"||orig.__v4) return;
    var wrapped=async function(id,name){
      var r=orig.apply(this,arguments);
      try{ await r; }catch(e){}
      try{ await enrichArtist(name); }catch(e){}
      return r;
    };
    wrapped.__v4=true; W.openArtistProfile=wrapped;
  }
  async function enrichArtist(hint){
    if(typeof get!=="function") return;
    var box=D.getElementById("modalBox"), head=box&&box.querySelector(".artist-profile-head");
    if(!head||head.querySelector(".v4-meta")) return;
    var h3=head.querySelector("h3"), name=(h3&&h3.textContent)||hint||"";
    if(!name) return;
    var d=await get("/api/meta/artist?name="+encodeURIComponent(name));
    if(!d||!D.body.contains(head)) return;
    var html='<div class="v4-meta">';
    if(d.image&&!head.querySelector(".artist-profile-cover")){
      var img=D.createElement("img"); img.className="artist-profile-cover v4-photo"; img.alt=""; img.src=d.image;
      img.onerror=function(){ img.style.display="none"; }; head.insertBefore(img,head.firstChild);
    }
    if(d.bio) html+='<p class="v4-bio">'+esc(d.bio)+'</p>'+(d.bio_url?'<a class="v4-src" target="_blank" rel="noopener noreferrer" href="'+esc(d.bio_url)+'">Джерело: '+esc(d.bio_source)+'</a>':'');
    if(d.albums&&d.albums.length){
      html+='<h4 class="v4-h">Дискографія</h4><div class="v4-disco">'+d.albums.slice(0,18).map(function(a){
        return '<div class="v4-alb">'+(a.cover?'<img loading="lazy" alt="" src="'+esc(a.cover)+'" onerror="this.style.visibility=\'hidden\'">':'<div class="v4-ph"></div>')
          +'<b>'+esc(a.title)+'</b><small>'+esc(a.year||"")+'</small></div>';}).join("")+'</div>'
        +'<p class="v4-src">Лише інформація про релізи; відтворення — з доступних у каталозі джерел.</p>';
    }
    html+='</div>';
    if(html==='<div class="v4-meta"></div>') return;
    var tr=D.getElementById("artistTracks");
    if(tr) tr.insertAdjacentHTML("beforebegin",html); else head.insertAdjacentHTML("afterend",html);
  }
  if(D.readyState==="loading") D.addEventListener("DOMContentLoaded",wrapArtistProfile); else wrapArtistProfile();
  W.addEventListener("load",wrapArtistProfile);

  /* ── 6. Інтернет-радіо (Radio Browser): лише посилання на офіційні ефіри станцій ── */
  var radioSeq=0, radioTimer=null;
  function openRadio(){
    if(typeof showModal!=="function") return;
    showModal('<div class="v4-plan"><div class="v4-plan-top"><div><div class="v4-kicker">LIVE</div><h2>Інтернет-радіо</h2></div>'
      +'<button class="connect-close" type="button" onclick="closeModal()" aria-label="Закрити">✕</button></div>'
      +'<input id="v4RadioQ" class="v4-login-input" placeholder="Назва станції" autocomplete="off">'
      +'<div class="v4-chips" id="v4RadioTags">'+["jazz","rock","pop","electronic","classical","lofi","news"].map(function(t){return '<button type="button" class="v4-chip" data-tag="'+t+'">'+t+'</button>';}).join("")+'</div>'
      +'<div id="v4RadioBody"></div>'
      +'<p class="v4-src">Ефіри належать станціям, ми лише показуємо посилання. Джерело: radio-browser.info</p></div>');
    var q=D.getElementById("v4RadioQ"), tag="";
    function run(){
      var seq=++radioSeq, body=D.getElementById("v4RadioBody"); if(!body) return;
      body.innerHTML='<div class="skel"></div><div class="skel"></div>';
      get("/api/radio-stations?limit=30&q="+encodeURIComponent(q.value.trim())+(tag?"&tag="+encodeURIComponent(tag):""))
        .then(function(d){
          if(seq!==radioSeq||!D.getElementById("v4RadioBody")) return;
          mountRows(D.getElementById("v4RadioBody"),(d&&d.items)||[],"radio_stations");
        }).catch(function(e){ if(seq===radioSeq&&D.getElementById("v4RadioBody")) D.getElementById("v4RadioBody").innerHTML='<div class="empty"><b>Не вдалося завантажити</b>'+esc(e&&e.message||"")+'</div>'; });
    }
    q.oninput=function(){ clearTimeout(radioTimer); radioTimer=setTimeout(run,350); };
    D.getElementById("v4RadioTags").onclick=function(e){
      var b=e.target.closest("[data-tag]"); if(!b) return;
      tag=(tag===b.dataset.tag)?"":b.dataset.tag;
      this.querySelectorAll(".v4-chip").forEach(function(x){ x.classList.toggle("on",x.dataset.tag===tag); });
      run();
    };
    run();
  }
  function injectRadioPill(){
    var kinds=D.getElementById("v3Kinds");
    if(!kinds||kinds.querySelector(".v4-radio-pill")) return;
    var b=D.createElement("button"); b.type="button"; b.className="v3-pill v4-radio-pill"; b.textContent="📻 Радіо";
    b.onclick=openRadio; kinds.appendChild(b);
  }
  new MutationObserver(injectRadioPill).observe(D.documentElement,{childList:true,subtree:true});
  injectRadioPill();

  /* ── 7. Підказки нового рушія пошуку: «Можливо, ви шукали…» і метадані, коли треків немає ── */
  safe(function(){
    var of=W.fetch; if(typeof of!=="function"||of.__v4) return;
    var wf=function(input){
      var p=of.apply(this,arguments), url=typeof input==="string"?input:(input&&input.url)||"";
      if(url.indexOf("/api/search?")>-1){
        p.then(function(r){ return r.clone().json(); }).then(function(d){
          if(d&&(d.did_you_mean||d.discovery)) setTimeout(function(){ showSearchExtras(d); },80);
        }).catch(function(){});
      }
      return p;
    };
    wf.__v4=true; W.fetch=wf;
  });
  function runQuery(q){
    var inp=D.getElementById("v3SearchInput")||D.getElementById("qInput");
    if(!inp) return;
    inp.value=q; inp.dispatchEvent(new Event("input",{bubbles:true}));
    inp.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
  }
  function showSearchExtras(d){
    var host=D.getElementById("v3Results")||D.getElementById("qBody"); if(!host) return;
    var old=host.querySelector(".v4-extras"); if(old) old.remove();
    var html='<div class="v4-extras">';
    if(d.did_you_mean) html+='<div class="v4-dym">Можливо, ви шукали: <button type="button" class="v4-chip" data-q="'+esc(d.did_you_mean)+'">'+esc(d.did_you_mean)+'</button></div>';
    var disc=d.discovery;
    if(disc&&disc.albums&&disc.albums.length){
      html+='<h4 class="v4-h">'+esc((disc.artist&&disc.artist.name)||"Релізи")+' — інформація про релізи</h4><div class="v4-disco">'
        +disc.albums.map(function(a){return '<button type="button" class="v4-alb v4-albbtn" data-q="'+esc(a.search_query)+'">'
          +(a.cover?'<img loading="lazy" alt="" src="'+esc(a.cover)+'" onerror="this.style.visibility=\'hidden\'">':'<div class="v4-ph"></div>')
          +'<b>'+esc(a.title)+'</b><small>'+esc(a.year||"")+' · знайти треки</small></button>';}).join("")+'</div>'
        +'<p class="v4-src">'+esc(disc.note||"")+'</p>';
    }
    html+='</div>';
    host.insertAdjacentHTML("afterbegin",html);
    var box=host.querySelector(".v4-extras");
    box.onclick=function(e){ var b=e.target.closest("[data-q]"); if(b) runQuery(b.dataset.q); };
  }

  W.MLSPv4={openRadio:openRadio,openPlan:openPlan,buyPremium:buyPremium,themes:NEW_THEMES.map(function(t){return t[0];})};
})();