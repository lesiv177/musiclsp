
(()=>{
"use strict";
const $=id=>document.getElementById(id), E=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const tr=(key,fallback)=>{try{const value=typeof window.t==="function"?window.t(key):key;return value&&value!==key?value:(fallback??key)}catch(_){return fallback??key}};
const st={page:"home",home:null,genres:[],moods:[],playlists:[],tab:"liked",kind:"all",seq:0,timer:null};
function section(t,b){return '<section class="v3-section"><div class="v3-section-head"><h2>'+t+'</h2></div>'+b+'</section>'}
function row(t,i,ctx="default"){return '<div class="v3-track" data-i="'+i+'" data-context="'+ctx+'"><img src="'+E(t.cover||"")+'" alt="" loading="lazy" onerror="this.style.opacity=.15"><span class="v3-track-meta"><b>'+E(t.title||"Без назви")+'</b><small>'+E(t.artist||"Невідомий артист")+(t.duration_str?' · '+E(t.duration_str):'')+'</small></span><button class="v3-track-action" data-menu="'+i+'">···</button></div>'}
function bindRows(root,list,ctx="default"){root.querySelectorAll(".v3-track[data-i][data-context='"+ctx+"']").forEach(el=>el.onclick=e=>{const i=+el.dataset.i;if(e.target.closest("[data-menu]"))openTrackMenu(list[i]);else setQueue(list,i);});}
function nav(page,options={}){st.page=page;document.querySelectorAll(".v3-page").forEach(x=>x.classList.toggle("active",x.id==="v3-"+page));document.querySelectorAll("#mlsp-v3 [data-page]").forEach(x=>x.classList.toggle("active",x.dataset.page===page));if(page==="home"&&!$("v3-home")?.querySelector(".v3-hero"))home();if(page==="explore"&&!st.genres.length)explore();if(page==="library"&&!options.skipLibraryReload)library(st.tab);if(page==="profile")profile();window.scrollTo(0,0);}
function build(){
 const root=$("mlsp-v3");
 const preservedLegacy=["topProfile","planChip","deviceChip"].map(id=>$(id)).filter(Boolean);
 preservedLegacy.forEach(node=>node.remove());
 root.innerHTML=
 '<aside class="v3-sidebar"><div class="v3-brand"><span class="v3-brand-mark">♫</span><span>MusicLSP<small>LISTEN BEYOND</small></span></div><nav class="v3-nav"><button data-page="home" class="active"><span>⌂</span>'+tr("nav_home","Home")+'</button><button data-page="explore"><span>◈</span>'+tr("search_genres","Explore")+'</button><button data-page="search"><span>⌕</span>'+tr("nav_search","Search")+'</button><button data-page="library"><span>▤</span>'+tr("nav_lib","Library")+'</button><button data-page="profile"><span>◉</span>'+tr("nav_me","Profile")+'</button></nav><div class="v3-side-note"><b>'+tr("hero_title_a","Більше музики. Більше свободи.")+'</b><p>'+tr("hero_desc","Відкривай незалежних артистів, збирай колекції та підтримуй авторів.")+'</p><button data-premium>'+tr("pr_premium","Відкрити Premium")+' ↗</button></div></aside>'+
 '<main class="v3-main"><header class="v3-topbar"><div class="v3-mobile-brand"><span class="v3-brand-mark">♫</span><b>MusicLSP</b></div><label class="v3-top-search"><span>⌕</span><input id="v3GlobalSearch" placeholder="'+tr("search_ph","Search tracks, artists, albums...")+'"></label><div class="v3-top-actions"><button class="v3-premium-link" data-premium>✦ '+tr("pr_premium","Premium")+'</button><span id="v3AvatarSlot"></span><span id="v3PlanSlot"></span><span id="v3DeviceSlot"></span></div></header>'+
 '<section class="v3-page active" id="v3-home"></section><section class="v3-page" id="v3-explore"></section><section class="v3-page" id="v3-search"></section><section class="v3-page" id="v3-library"></section><section class="v3-page" id="v3-collection"></section><section class="v3-page" id="v3-profile"></section></main>'+
 '<nav class="v3-mobile-nav"><button data-page="home" class="active"><span>⌂</span>'+tr("nav_home","Home")+'</button><button data-page="explore"><span>◈</span>'+tr("search_genres","Explore")+'</button><button data-page="search"><span>⌕</span>'+tr("nav_search","Search")+'</button><button data-page="library"><span>▤</span>'+tr("nav_lib","Library")+'</button><button data-page="profile"><span>◉</span>'+tr("nav_me","Profile")+'</button></nav>';
 preservedLegacy.forEach((n,i)=>{const slot=$(["v3AvatarSlot","v3PlanSlot","v3DeviceSlot"][i]);if(n&&slot)slot.appendChild(n);});
 if($("topProfile"))$("topProfile").onclick=()=>nav("profile");
 root.querySelectorAll("[data-page]").forEach(b=>b.onclick=()=>nav(b.dataset.page));
 root.querySelectorAll("[data-premium]").forEach(b=>b.onclick=()=>openPlan());
 const g=$("v3GlobalSearch");g.onfocus=()=>nav("search");g.oninput=()=>{nav("search");$("v3SearchInput").value=g.value;search();};g.onkeydown=e=>{if(e.key==="Enter"){nav("search");$("v3SearchInput").value=g.value;search();}};
}
function feature(title,sub,t,i){return '<button class="v3-feature" data-feature="'+i+'">'+(t?.cover?'<img src="'+E(t.cover)+'" alt="">':'')+'<span class="v3-play-dot">▶</span><span class="v3-feature-copy"><b>'+title+'</b><small>'+sub+'</small></span></button>'}
function shelfCard(t,i,ctx){
 return '<button class="v3-album-card" data-shelf="'+ctx+'" data-index="'+i+'"><span class="v3-album-cover">'+(t?.cover?'<img src="'+E(t.cover)+'" alt="" loading="lazy" onerror="this.style.opacity=.12">':'<span class="v3-cover-fallback">♫</span>')+'<span class="v3-album-play">▶</span></span><b>'+E(t?.title||"Без назви")+'</b><small>'+E(t?.artist||"Невідомий артист")+'</small></button>';
}
function shelf(title,list,ctx){
 if(!list?.length)return "";
 return section(title,'<div class="v3-shelf">'+list.slice(0,12).map((t,i)=>shelfCard(t,i,ctx)).join("")+'</div>');
}
async function home(){
 const root=$("v3-home");root.innerHTML='<div class="v3-empty">'+E(tr("ui_loading","Завантаження…"))+'</div>';
 try{
  const [h,p,m]=await Promise.all([get("/api/home"),get("/api/playlists"),get("/api/moods")]);
  st.home=h;st.playlists=p.playlists||[];st.moods=m.moods||[];
  const sec=h.sections||[],by=Object.fromEntries(sec.map(x=>[x.id,x]));
  const nb=l=>typeof noBlocked==="function"?noBlocked(l):l;
  const on=k=>!(typeof isPrem==="function"&&isPrem()&&typeof PREF!=="undefined"&&PREF.home&&PREF.home[k]===0);
  const recent=nb(by.recent?.tracks||[]),popular=nb(by.popular?.tracks||[]),fresh=nb(by.fresh?.tracks||[]),trending=nb(by.trending?.tracks||[]);
  const am=new Map();[...popular,...trending,...fresh,...recent].forEach(t=>{if(t.album_id&&!am.has(t.album_id))am.set(t.album_id,{id:t.album_id,title:t.album||t.title,artist:t.artist,cover:t.cover});});
  st.homeAlbums=[...am.values()].slice(0,12);
  const moods=st.moods.slice(0,12);
  root.innerHTML=
   '<div class="v3-home-heading"><div><div class="v3-eyebrow">MUSICLSP / HOME</div><div class="v3-page-kicker">'+tr("home_collections","Made for your listening")+'</div><h1 class="v3-page-title">'+tr("hero_title_a","Your music.")+" "+tr("hero_title_b","Your rules.")+'</h1></div><button class="v3-home-refresh" type="button" data-refresh>↻</button></div>'+
   (moods.length?'<div class="v3-home-moods">'+moods.map((m,i)=>'<button class="v3-pill" data-home-mood="'+i+'">'+E(m.label)+'</button>').join("")+'</div>':'')+
   (on("albums")?shelf("Альбоми для тебе",st.homeAlbums,"album"):"")+
   (on("feature")?shelf("Підбірки для тебе",popular.length?popular:trending,"popular"):"")+
   (on("recent")?shelf("Слухати знову",recent,"recent"):"")+
   (on("fresh")?shelf("Нові відкриття",fresh,"fresh"):"")+
   (on("trending")?shelf("Зараз у тренді",trending.length?trending:popular,"trending"):"")+
   (on("playlists")&&st.playlists.length?section("Your playlists",'<div class="v3-shelf">'+st.playlists.slice(0,12).map(p=>'<button class="v3-album-card v3-playlist-card" data-pl="'+E(p.id)+'"><span class="v3-album-cover v3-playlist-cover"><span>♫</span></span><b>'+E(p.name)+'</b><small>'+(Number(p.count)||0)+' tracks</small></button>').join("")+'</div>'):'')+
   (!recent.length&&!popular.length&&!fresh.length&&!trending.length?'<div class="v3-empty">Поки немає рекомендацій. Знайди перший трек через пошук.</div>':'');
  root.querySelectorAll("[data-shelf]").forEach(b=>b.onclick=()=>{
   if(b.dataset.shelf==="album"){const a=st.homeAlbums[Number(b.dataset.index)||0];if(a)openCollection("albums",a);return;}
   const groups={popular:popular,recent:recent,fresh:fresh,trending:trending.length?trending:popular};
   const list=groups[b.dataset.shelf]||[];const i=Number(b.dataset.index)||0;
   if(list.length)setQueue(list,i);
  });
  root.querySelectorAll("[data-home-mood]").forEach(b=>b.onclick=()=>{
   const mood=st.moods[Number(b.dataset.homeMood)];
   if(mood?.locked)toast(tr("ui_mood_premium","Цей настрій доступний у Premium"));
   else if(mood)moodV3(mood.key,mood.label);
  });
  root.querySelectorAll("[data-pl]").forEach(b=>b.onclick=()=>playlist(b.dataset.pl));
  $("v3-home").querySelector("[data-refresh]")?.addEventListener("click",()=>home());
 }catch(e){root.innerHTML='<div class="v3-empty">Не вдалося завантажити головну: '+E(e.message)+'</div>';}
}
async function explore(){
 const root=$("v3-explore");root.innerHTML='<div class="v3-empty">'+E(tr("ui_loading","Завантаження…"))+'</div>';
 try{
  const [g,m]=await Promise.all([get("/api/genres"),get("/api/moods")]);st.genres=g.genres||[];st.moods=m.moods||[];
  root.innerHTML='<div class="v3-eyebrow">EXPLORE / FIND YOUR NEXT SOUND</div><h1 class="v3-page-title">'+tr("hero_cta","Відкривай більше.")+'</h1><p class="v3-subtitle">'+tr("onboard_body","Жанри, настрої та артисти за межами звичного.")+'</p>'+
  section(tr("h_mood","Настрій"),'<div class="v3-pills">'+st.moods.map((m,i)=>'<button class="v3-pill" data-mood="'+i+'">'+E(m.label)+'</button>').join("")+'</div>')+
  section(tr("search_genres","Жанри"),'<div class="v3-genre-grid">'+st.genres.map((g,i)=>'<button class="v3-genre" data-genre="'+i+'"><b>'+E(g.label)+'</b><small>ДОСЛІДИТИ ↗</small></button>').join("")+'</div>');
  root.querySelectorAll("[data-mood]").forEach(b=>b.onclick=()=>{const m=st.moods[+b.dataset.mood];if(m.locked)toast(tr("ui_mood_premium","Цей настрій доступний у Premium"));else moodV3(m.key,m.label);});
  root.querySelectorAll("[data-genre]").forEach(b=>b.onclick=()=>{const g=st.genres[+b.dataset.genre];genreV3(g.tag,g.label);});
 }catch(e){root.innerHTML='<div class="v3-empty">Не вдалося завантажити каталог: '+E(e.message)+'</div>';}
}
function searchPage(){
 $("v3-search").innerHTML='<div class="v3-eyebrow">SEARCH / MUSIC DATABASE</div><h1 class="v3-page-title">'+tr("search_empty_title","Find your sound.")+'</h1><p class="v3-subtitle">'+tr("search_empty_body","Search tracks, artists and albums.")+'</p><label class="v3-searchbox">⌕ <input id="v3SearchInput" placeholder="'+tr("search_ph","Track, artist or album...")+'"></label><div class="v3-pills" id="v3Kinds">'+["all","tracks","artists","albums"].map((x,i)=>'<button class="v3-pill '+(i===0?"active":"")+'" data-kind="'+x+'">'+({all:tr("search_all","All"),tracks:tr("search_tracks","Tracks"),artists:tr("search_artists","Artists"),albums:tr("search_albums","Albums")}[x])+'</button>').join("")+'</div><div id="v3Results"><div class="v3-empty">'+tr("onboard_body","Введи назву або ім’я виконавця, щоб почати.")+'</div></div>';
 $("v3SearchInput").oninput=()=>{clearTimeout(st.timer);st.timer=setTimeout(search,350);};$("v3SearchInput").onkeydown=e=>{if(e.key==="Enter")search();};
 $("v3Kinds").querySelectorAll("button").forEach(b=>b.onclick=()=>{st.kind=b.dataset.kind;$("v3Kinds").querySelectorAll("button").forEach(x=>x.classList.toggle("active",x===b));search();});
}
function collectionRows(list,kind){
 return '<div class="v3-track-list">'+list.map((x,i)=>'<button class="v3-track" data-collection="'+i+'" data-collection-kind="'+kind+'"><img src="'+E(x.cover||"")+'" alt="" loading="lazy"><span class="v3-track-meta"><b>'+E(x.name||x.title||"")+'</b><small>'+E(kind==="artists"?(tr("artist_label","Artist")):(tr("search_albums","Albums")))+(x.source_label?" · "+E(x.source_label):"")+'</small></span>↗</button>').join("")+'</div>';
}
async function openCollection(kind,item){
 if(st.page!=="collection")st.prev=st.page;
 try{
  const d=await get("/api/"+(kind==="artists"?"artist":"album")+"/"+encodeURIComponent(item.id));
  const list=d.tracks||[];if(!list.length)return toast(tr("nothing_found_body","Нічого не знайдено"));
  const al=d.album||{},total=list.reduce((n,t)=>n+(+t.duration||0),0),isAlb=kind!=="artists";
  const cover=item.cover||al.cover||list[0]?.cover||"",title=item.title||item.name||al.title||"";
  const by=isAlb?(item.artist||al.artist||list[0]?.artist||""):"Артист";
  const meta=[list.length+" треків",total?Math.max(1,Math.round(total/60))+" хв":"",String(al.release_date||item.release_date||"").slice(0,4),list[0]?.source_label||""].filter(Boolean).join(" · ");
  const root=$("v3-collection");
  root.innerHTML='<button class="v3-pill v3-back" id="v3CollBack">‹ Назад</button>'+
   '<div class="v3-coll-hero"><img src="'+E(cover)+'" alt="" onerror="this.style.opacity=.15"><div><div class="v3-eyebrow">'+(isAlb?"АЛЬБОМ":"АРТИСТ")+'</div><h1 class="v3-page-title">'+E(title)+'</h1><p class="v3-coll-by">'+E(by)+'</p><p class="v3-coll-meta">'+E(meta)+'</p></div></div>'+
   '<div class="v3-coll-actions"><button class="v3-coll-play" id="v3CollPlay">▶ Слухати</button><button class="v3-pill" id="v3CollShuffle">Перемішати</button></div>'+
   '<div class="v3-track-list v3-coll-list">'+list.map((t,i)=>row(t,i,"coll")).join("")+'</div>';
  nav("collection");
  document.querySelectorAll("#mlsp-v3 [data-page]").forEach(x=>x.classList.toggle("active",x.dataset.page===st.prev));
  bindRows(root,list,"coll");
  $("v3CollBack").onclick=()=>nav(st.prev||"home");
  $("v3CollPlay").onclick=()=>setQueue(list,0);
  $("v3CollShuffle").onclick=()=>setQueue(list.slice().sort(()=>Math.random()-.5),0);
 }catch(e){toast(e.message);}
}
async function search(){
 const inp=$("v3SearchInput");if(!inp)return;
 const q=inp.value.trim(),seq=++st.seq;
 $("v3GlobalSearch").value=q;
 if(q.length<2){$("v3Results").innerHTML='<div class="v3-empty">'+E(tr("search_empty_body","Enter at least two characters to search."))+'</div>';return;}
 $("v3Results").innerHTML='<div class="v3-empty">'+E(tr("ui_loading","Searching…"))+'</div>';
 try{
  const d=await get("/api/search?q="+encodeURIComponent(q)+"&type="+encodeURIComponent(st.kind)+"&limit=30&sort=relevance");
  if(seq!==st.seq)return;
  const results=d.results||{};
  if(st.kind==="all"&&results&&!Array.isArray(results)){
   const tracks=(typeof noBlocked==="function"?noBlocked:(l=>l))(Array.isArray(results.tracks)?results.tracks:[]);
   const albums=Array.isArray(results.albums)?results.albums:[];
   const artists=Array.isArray(results.artists)?results.artists:[];
   if(!tracks.length&&!albums.length&&!artists.length){$("v3Results").innerHTML='<div class="v3-empty">'+E(tr("nothing_found_body","Try another search."))+'</div>';return;}
   let html="";
   if(tracks.length)html+=section(tr("search_tracks","Tracks"),'<div class="v3-track-list">'+tracks.slice(0,12).map((x,i)=>row(x,i,"alltracks")).join("")+'</div>');
   if(artists.length)html+=section(tr("search_artists","Artists"),collectionRows(artists.slice(0,8),"artists"));
   if(albums.length)html+=section(tr("search_albums","Albums"),collectionRows(albums.slice(0,8),"albums"));
   $("v3Results").innerHTML=html;
   bindRows($("v3Results"),tracks.slice(0,12),"alltracks");
   $("v3Results").querySelectorAll("[data-collection]").forEach(b=>b.onclick=()=>{const kind=b.dataset.collectionKind,index=+b.dataset.collection;openCollection(kind,(kind==="artists"?artists:albums)[index]);});
   return;
  }
  const list=Array.isArray(results)?results:[];
  if(!list.length){$("v3Results").innerHTML='<div class="v3-empty">'+E(tr("nothing_found_body","Try another search."))+'</div>';return;}
  if(st.kind==="artists"||st.kind==="albums"){
   $("v3Results").innerHTML=collectionRows(list,st.kind);
   $("v3Results").querySelectorAll("[data-collection]").forEach(b=>b.onclick=()=>openCollection(st.kind,list[+b.dataset.collection]));
  }else{
   $("v3Results").innerHTML='<div class="v3-track-list">'+list.map((x,i)=>row(x,i)).join("")+'</div>';
   bindRows($("v3Results"),list);
  }
 }catch(e){if(seq==st.seq)$("v3Results").innerHTML='<div class="v3-empty">'+E(e.message)+'</div>';}
}
async function library(tab){
 st.tab=tab;const root=$("v3-library");
 root.innerHTML='<div class="v3-eyebrow">YOUR MUSIC / COLLECTION</div><h1 class="v3-page-title">'+tr("nav_lib","Твоя колекція.")+'</h1><p class="v3-subtitle">'+tr("lib_liked_empty_body","Усе, що ти зберіг, слухав і створив.")+'</p><div class="v3-library-tabs">'+[["liked",tr("lib_liked","Saved")],["playlists",tr("lib_playlists","Playlists")],["history",tr("lib_history","History")],["moments",tr("lib_moments","Moments")]].map(x=>'<button class="'+(tab===x[0]?"active":"")+'" data-tab="'+x[0]+'">'+x[1]+'</button>').join("")+'</div><div id="v3LibraryBody"><div class="v3-empty">'+tr("ui_loading","Завантаження…")+'</div></div>';
 root.querySelectorAll("[data-tab]").forEach(b=>b.onclick=()=>library(b.dataset.tab));const box=$("v3LibraryBody");
 try{
  if(tab==="playlists"){const d=await get("/api/playlists"),ps=d.playlists||[];box.innerHTML='<button class="v3-pill active" id="v3Create">＋ '+tr("lib_create_playlist","Створити плейлист")+'</button><div style="height:12px"></div><div class="v3-track-list">'+ps.map(p=>'<button class="v3-track" data-pl="'+E(p.id)+'"><span class="v3-avatar" style="width:42px;height:42px">♫</span><span class="v3-track-meta"><b>'+E(p.name)+'</b><small>'+(Number(p.count)||0)+' треків</small></span>›</button>').join("")+'</div>'+(ps.length?"":'<div class="v3-empty">'+tr("lib_create_playlist","Створи першу музичну добірку.")+'</div>');$("v3Create").onclick=()=>openCreatePlaylist();box.querySelectorAll("[data-pl]").forEach(b=>b.onclick=()=>playlist(b.dataset.pl));return;}
  if(tab==="moments"){box.innerHTML='<div class="v3-empty">'+tr("moments_empty_body","Збережені моменти можна відкривати з плеєра під час прослуховування.")+'</div>';return;}
  const d=await get(tab==="liked"?"/api/library":"/api/history"),list=d.tracks||[];box.innerHTML=list.length?'<div class="v3-track-list">'+list.map((t,i)=>row(t,i)).join("")+'</div>':'<div class="v3-empty">'+tr("ui_empty_body","Тут поки порожньо. Знайди музику й додай її до своєї колекції.")+'</div>';bindRows(box,list);
 }catch(e){box.innerHTML='<div class="v3-empty">Не вдалося завантажити: '+E(e.message)+'</div>';}
}
async function playlist(id){
 try{const d=await get("/api/playlist/"+encodeURIComponent(id)),p=d.playlist||{},list=p.tracks||[];nav("library",{skipLibraryReload:true});const root=$("v3-library");root.innerHTML='<button class="v3-pill" id="v3Back">← До бібліотеки</button><div class="v3-profile-hero" style="margin:20px 0"><div class="v3-avatar">♫</div><div><div class="v3-eyebrow">PLAYLIST</div><h2>'+E(p.name)+'</h2><p>'+list.length+' треків</p></div></div><button class="v3-pill active" id="v3PlayAll">▶ Слухати все</button><div class="v3-track-list" style="margin-top:14px">'+list.map((t,i)=>row(t,i)).join("")+'</div>'; $("v3Back").onclick=()=>library("playlists");$("v3PlayAll").onclick=()=>{if(list.length)setQueue(list,0);};bindRows(root,list);}catch(e){toast(e.message);}
}
async function moodV3(key,label){
 nav("search");$("v3SearchInput").value="";$("v3GlobalSearch").value="";
 $("v3Results").innerHTML='<div class="v3-empty">Підбираємо музику під настрій…</div>';
 try{const d=await get("/api/mood/"+encodeURIComponent(key)),list=d.tracks||[];$("v3Results").innerHTML='<div class="v3-eyebrow">MOOD / '+E(label)+'</div><div class="v3-track-list">'+list.map((t,i)=>row(t,i)).join("")+'</div>';bindRows($("v3Results"),list);}catch(e){$("v3Results").innerHTML='<div class="v3-empty">'+E(e.message)+'</div>';}
}
async function genreV3(tag,label){
 nav("search");$("v3SearchInput").value="";$("v3GlobalSearch").value="";
 $("v3Results").innerHTML='<div class="v3-empty">Збираємо добірку жанру…</div>';
 try{const d=await get("/api/home?tag="+encodeURIComponent(tag)),list=d.sections?.[0]?.tracks||[];$("v3Results").innerHTML='<div class="v3-eyebrow">GENRE / '+E(label||tag)+'</div><div class="v3-track-list">'+list.map((t,i)=>row(t,i)).join("")+'</div>';if(list.length)bindRows($("v3Results"),list);else $("v3Results").innerHTML+='<div class="v3-empty">Поки немає треків у цій добірці.</div>';}catch(e){$("v3Results").innerHTML='<div class="v3-empty">'+E(e.message)+'</div>';}
}
async function profile(){
 const root=$("v3-profile");root.innerHTML='<div class="v3-empty">'+E(tr("ui_loading","Завантаження…"))+'</div>';
 try{const [m,d]=await Promise.all([get("/api/me"),get("/api/stats?days=30").catch(()=>({stats:{}}))]);const u=m.user||{},s=d.stats||{};S.me=u;S.limits=m.limits||S.limits;S.features=m.features||S.features;
 root.innerHTML='<div class="v3-eyebrow">YOUR MUSIC ID</div><h1 class="v3-page-title">'+tr("nav_me","Твій простір.")+'</h1><p class="v3-subtitle">'+tr("sub_free","Акаунт, активність та можливості MusicLSP.")+'</p><div class="v3-profile-hero" style="margin:20px 0"><div class="v3-avatar">'+E((u.name||"M").slice(0,1).toUpperCase())+'</div><div><div class="v3-eyebrow">'+(u.premium?"PREMIUM MEMBER":"MUSICLSP LISTENER")+'</div><h2>'+E(u.name||tr("ui_listener","Слухач"))+'</h2><p>'+(u.premium?tr("ui_premium_active","Premium активний"):tr("ui_free_plan","Безкоштовний план"))+(u.premium_until?" · до "+E(String(u.premium_until).slice(0,10)):"")+'</p></div></div><div class="v3-profile-actions"><button class="v3-profile-action" data-prof="library"><b>♫ '+tr("nav_lib","Моя музика")+'</b><small>'+tr("lib_liked","Збережене")+', '+tr("lib_playlists","плейлисти")+', '+tr("lib_history","історія")+'</small></button><button class="v3-profile-action" data-prof="wrapped"><b>◷ '+tr("pr_wrapped","Мої підсумки")+'</b><small>'+tr("pr_stats","Статистика та музичні звички")+'</small></button><button class="v3-profile-action" data-prof="space"><b>🌌 Оформлення</b><small>6 космічних тем і фон</small></button><button class="v3-profile-action v3-pm-action" data-prof="pmset"><b>✦ Premium-налаштування</b><small>Налаштуй MusicLSP під себе</small></button>'+(u.is_admin?'<button class="v3-profile-action v3-admin-action" data-prof="admin"><b>Адмін-панель</b><small>Користувачі · скарги · промокоди · система</small></button>':'')+'</div>'+section(tr("pr_stats","Статистика")+" · 30 днів",'<div class="v3-stats"><div class="v3-stat"><b>'+(Number(s.plays)||0)+'</b><small>Прослухано</small></div><div class="v3-stat"><b>'+(Number(s.hours)||0)+'</b><small>Годин</small></div><div class="v3-stat"><b>'+(Number(s.library)||0)+'</b><small>У бібліотеці</small></div></div>')+section("Акаунт",'<div class="v3-track-list"><button class="v3-track" data-prof="plan"><span class="v3-track-meta"><b>'+(u.premium?"Мій тариф":"Тарифи Premium")+'</b><small>'+(u.premium?"Керування підпискою":"Що входить у Premium")+'</small></span></button><button class="v3-track" data-prof="devices"><span class="v3-track-meta"><b>Мої пристрої</b><small>Connect: передати відтворення</small></span></button></div>');
 root.querySelectorAll("[data-prof]").forEach(b=>b.onclick=()=>{if(b.dataset.prof==="library")nav("library");if(b.dataset.prof==="wrapped")openWrapped();if(b.dataset.prof==="space")openSpaceThemes();if(b.dataset.prof==="pmset")openPremiumSettings();if(b.dataset.prof==="plan")openPlan();if(b.dataset.prof==="devices"&&typeof openDevices==="function")openDevices();if(b.dataset.prof==="admin")openAdmin();});
 }catch(e){root.innerHTML='<div class="v3-empty">Не вдалося завантажити профіль: '+E(e.message)+'</div>';}
}
function refreshLocale(){
 const page=st.page,tab=st.tab,kind=st.kind;
 const query=$("v3SearchInput")?.value||$("v3GlobalSearch")?.value||"";
 build();searchPage();
 if(page==="search"){nav("search");$("v3SearchInput").value=query;if(query.trim().length>=2)search();}
 else if(page==="library"){nav("library");library(tab);}
 else if(page==="explore"){nav("explore");explore();}
 else if(page==="profile"){nav("profile");profile();}
 else{nav("home");}
 st.kind=kind;
}
function init(){
 build();searchPage();home();
 let locale=document.documentElement.lang||"uk";
 const localeObserver=new MutationObserver(()=>{
  const next=document.documentElement.lang||"uk";
  if(next!==locale){locale=next;refreshLocale();}
 });
 localeObserver.observe(document.documentElement,{attributes:true,attributeFilter:["lang"]});
 document.addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="k"){e.preventDefault();nav("search");$("v3SearchInput")?.focus();}});
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
window.MLSPv3={navigate:nav,home,explore,library,profile,search};
})();