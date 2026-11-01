
(()=>{
"use strict";
const $=id=>document.getElementById(id), E=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const st={page:"home",home:null,genres:[],moods:[],playlists:[],tab:"liked",kind:"all",seq:0,timer:null};
function section(t,b){return '<section class="v3-section"><div class="v3-section-head"><h2>'+t+'</h2></div>'+b+'</section>'}
function row(t,i,ctx="default"){return '<div class="v3-track" data-i="'+i+'" data-context="'+ctx+'"><img src="'+E(t.cover||"")+'" alt="" loading="lazy" onerror="this.style.opacity=.15"><span class="v3-track-meta"><b>'+E(t.title||"Без назви")+'</b><small>'+E(t.artist||"Невідомий артист")+(t.duration_str?' · '+E(t.duration_str):'')+'</small></span><button class="v3-track-action" data-menu="'+i+'">···</button></div>'}
function bindRows(root,list,ctx="default"){root.querySelectorAll(".v3-track[data-i][data-context='"+ctx+"']").forEach(el=>el.onclick=e=>{const i=+el.dataset.i;if(e.target.closest("[data-menu]"))openTrackMenu(list[i]);else setQueue(list,i);});}
function nav(page){st.page=page;document.querySelectorAll(".v3-page").forEach(x=>x.classList.toggle("active",x.id==="v3-"+page));document.querySelectorAll("#mlsp-v3 [data-page]").forEach(x=>x.classList.toggle("active",x.dataset.page===page));if(page==="explore"&&!st.genres.length)explore();if(page==="library")library(st.tab);if(page==="profile")profile();window.scrollTo(0,0);}
function build(){
 const root=$("mlsp-v3");root.innerHTML=
 '<aside class="v3-sidebar"><div class="v3-brand"><span class="v3-brand-mark">♫</span><span>MusicLSP<small>LISTEN BEYOND</small></span></div><nav class="v3-nav"><button data-page="home" class="active"><span>⌂</span>Головна</button><button data-page="explore"><span>◈</span>Відкриття</button><button data-page="search"><span>⌕</span>Пошук</button><button data-page="library"><span>▤</span>Моя музика</button><button data-page="profile"><span>◉</span>Профіль</button></nav><div class="v3-side-note"><b>Більше музики. Більше свободи.</b><p>Відкривай незалежних артистів, збирай колекції та підтримуй авторів.</p><button data-premium>Відкрити Premium ↗</button></div></aside>'+
 '<main class="v3-main"><header class="v3-topbar"><div class="v3-mobile-brand"><span class="v3-brand-mark">♫</span><b>MusicLSP</b></div><label class="v3-top-search"><span>⌕</span><input id="v3GlobalSearch" placeholder="Шукай треки, артистів, альбоми..."></label><div class="v3-top-actions"><button class="v3-premium-link" data-premium>✦ Premium</button><span id="v3AvatarSlot"></span><span id="v3PlanSlot"></span><span id="v3DeviceSlot"></span></div></header>'+
 '<section class="v3-page active" id="v3-home"></section><section class="v3-page" id="v3-explore"></section><section class="v3-page" id="v3-search"></section><section class="v3-page" id="v3-library"></section><section class="v3-page" id="v3-profile"></section></main>'+
 '<nav class="v3-mobile-nav"><button data-page="home" class="active"><span>⌂</span>Головна</button><button data-page="explore"><span>◈</span>Відкриття</button><button data-page="search"><span>⌕</span>Пошук</button><button data-page="library"><span>▤</span>Музика</button><button data-page="profile"><span>◉</span>Профіль</button></nav>';
 ["topProfile","planChip","deviceChip"].forEach((id,i)=>{const n=$(id),slot=$(["v3AvatarSlot","v3PlanSlot","v3DeviceSlot"][i]);if(n&&slot)slot.appendChild(n);});
 if($("topProfile"))$("topProfile").onclick=()=>nav("profile");
 root.querySelectorAll("[data-page]").forEach(b=>b.onclick=()=>nav(b.dataset.page));
 root.querySelectorAll("[data-premium]").forEach(b=>b.onclick=()=>openPlan());
 const g=$("v3GlobalSearch");g.onfocus=()=>nav("search");g.oninput=()=>{nav("search");$("v3SearchInput").value=g.value;search();};g.onkeydown=e=>{if(e.key==="Enter"){nav("search");$("v3SearchInput").value=g.value;search();}};
}
function feature(title,sub,t,i){return '<button class="v3-feature" data-feature="'+i+'">'+(t?.cover?'<img src="'+E(t.cover)+'" alt="">':'')+'<span class="v3-play-dot">▶</span><span class="v3-feature-copy"><b>'+title+'</b><small>'+sub+'</small></span></button>'}
async function home(){
 const root=$("v3-home");root.innerHTML='<div class="v3-empty">Завантажуємо музичну стрічку…</div>';
 try{
  const [h,p,m]=await Promise.all([get("/api/home"),get("/api/playlists"),get("/api/moods")]);st.home=h;st.playlists=p.playlists||[];st.moods=m.moods||[];
  const sec=h.sections||[],by=Object.fromEntries(sec.map(x=>[x.id,x])),recent=by.recent?.tracks||[],popular=by.popular?.tracks||[],fresh=by.fresh?.tracks||[],trending=by.trending?.tracks||[];
  const sets=[["Твій мікс","Персональна добірка",recent[0]||popular[0],recent.length?recent:popular],["У тренді","Що слухають зараз",popular[0]||trending[0],popular],["Свіжі відкриття","Нові голоси",fresh[0]||trending[0],fresh],["Повільний ритм","Час розслабитись",trending[0]||recent[0],trending]];
  root.innerHTML='<div class="v3-hero"><div class="v3-eyebrow">MUSICLSP / YOUR SOUND</div><h1>Твоя музика.<br>Твої правила.</h1><p>Відкривай нові голоси, знаходь звучання під настрій і збирай музику, яка залишається з тобою.</p><label class="v3-hero-search">⌕ <input id="v3HeroSearch" placeholder="Що хочеш послухати сьогодні?"></label></div>'+
  section("Твоя музична хвиля",'<div class="v3-card-grid">'+sets.map((x,i)=>feature(x[0],x[1],x[2],i)).join("")+'</div>')+
  (recent.length?section("Продовжити слухати",'<div class="v3-track-list">'+recent.slice(0,7).map((t,i)=>row(t,i,"recent")).join("")+'</div>'):'')+
  (fresh.length?section("Нові відкриття",'<div class="v3-track-list">'+fresh.slice(0,6).map((t,i)=>row(t,i,"fresh")).join("")+'</div>'):'')+
  (st.playlists.length?section("Твої плейлисти",'<div class="v3-track-list">'+st.playlists.slice(0,6).map(p=>'<button class="v3-track" data-pl="'+E(p.id)+'"><span class="v3-avatar" style="width:42px;height:42px">♫</span><span class="v3-track-meta"><b>'+E(p.name)+'</b><small>'+(Number(p.count)||0)+' треків</small></span>›</button>').join("")+'</div>'):'')+
  (!recent.length&&!popular.length&&!fresh.length?'<div class="v3-empty">Поки немає рекомендацій. Знайди перший трек через пошук.</div>':'');
  const inp=$("v3HeroSearch");inp.onfocus=()=>nav("search");inp.onkeydown=e=>{if(e.key==="Enter"){nav("search");$("v3SearchInput").value=inp.value;search();}};
  root.querySelectorAll("[data-feature]").forEach(b=>b.onclick=()=>{const x=sets[+b.dataset.feature];if(x[3]?.length)setQueue(x[3],0);});
  bindRows(root,recent,"recent");bindRows(root,fresh,"fresh");
  root.querySelectorAll("[data-pl]").forEach(b=>b.onclick=()=>playlist(b.dataset.pl));
 }catch(e){root.innerHTML='<div class="v3-empty">Не вдалося завантажити головну: '+E(e.message)+'</div>';}
}
async function explore(){
 const root=$("v3-explore");root.innerHTML='<div class="v3-empty">Завантажуємо каталог…</div>';
 try{
  const [g,m,h]=await Promise.all([get("/api/genres"),get("/api/moods"),get("/api/home")]);st.genres=g.genres||[];st.moods=m.moods||[];
  root.innerHTML='<div class="v3-eyebrow">EXPLORE / FIND YOUR NEXT SOUND</div><h1 class="v3-page-title">Відкривай більше.</h1><p class="v3-subtitle">Жанри, настрої та артисти за межами звичного.</p>'+
  section("Обери настрій",'<div class="v3-pills">'+st.moods.map((m,i)=>'<button class="v3-pill" data-mood="'+i+'">'+E(m.label)+'</button>').join("")+'</div>')+
  section("Досліджуй жанри",'<div class="v3-genre-grid">'+st.genres.map((g,i)=>'<button class="v3-genre" data-genre="'+i+'"><b>'+E(g.label)+'</b><small>ДОСЛІДИТИ ↗</small></button>').join("")+'</div>');
  root.querySelectorAll("[data-mood]").forEach(b=>b.onclick=()=>{const m=st.moods[+b.dataset.mood];if(m.locked)toast("Цей настрій доступний у Premium");else moodV3(m.key,m.label);});
  root.querySelectorAll("[data-genre]").forEach(b=>b.onclick=()=>{const g=st.genres[+b.dataset.genre];genreV3(g.tag,g.label);});
 }catch(e){root.innerHTML='<div class="v3-empty">Не вдалося завантажити каталог: '+E(e.message)+'</div>';}
}
function searchPage(){
 $("v3-search").innerHTML='<div class="v3-eyebrow">SEARCH / MUSIC DATABASE</div><h1 class="v3-page-title">Знайди свій звук.</h1><p class="v3-subtitle">Шукай треки, артистів та альбоми в музичному каталозі.</p><label class="v3-searchbox">⌕ <input id="v3SearchInput" placeholder="Назва треку, артист або альбом..."></label><div class="v3-pills" id="v3Kinds">'+["all","tracks","artists","albums"].map((x,i)=>'<button class="v3-pill '+(i===0?"active":"")+'" data-kind="'+x+'">'+({all:"Усе",tracks:"Треки",artists:"Артисти",albums:"Альбоми"}[x])+'</button>').join("")+'</div><div id="v3Results"><div class="v3-empty">Введи назву або ім’я виконавця, щоб почати.</div></div>';
 $("v3SearchInput").oninput=()=>{clearTimeout(st.timer);st.timer=setTimeout(search,350);};$("v3SearchInput").onkeydown=e=>{if(e.key==="Enter")search();};
 $("v3Kinds").querySelectorAll("button").forEach(b=>b.onclick=()=>{st.kind=b.dataset.kind;$("v3Kinds").querySelectorAll("button").forEach(x=>x.classList.toggle("active",x===b));search();});
}
async function search(){
 const inp=$("v3SearchInput");if(!inp)return;const q=inp.value.trim(),seq=++st.seq;$("v3GlobalSearch").value=q;
 if(q.length<2){$("v3Results").innerHTML='<div class="v3-empty">Введи хоча б два символи для пошуку.</div>';return;}
 $("v3Results").innerHTML='<div class="v3-empty">Шукаємо…</div>';
 try{const d=await get("/api/search?q="+encodeURIComponent(q)+"&type="+encodeURIComponent(st.kind)+"&limit=30&sort=relevance");if(seq!==st.seq)return;const list=Array.isArray(d.results)?d.results:(d.results?.tracks||[]);
 if(!list.length){$("v3Results").innerHTML='<div class="v3-empty">Нічого не знайдено. Спробуй інший запит.</div>';return;}
 if(st.kind==="artists"||st.kind==="albums"){
  $("v3Results").innerHTML='<div class="v3-track-list">'+list.map((x,i)=>'<button class="v3-track" data-collection="'+i+'"><img src="'+E(x.cover||"")+'" alt=""><span class="v3-track-meta"><b>'+E(x.name||x.title)+'</b><small>'+(st.kind==="artists"?"Артист":"Альбом")+'</small></span>↗</button>').join("")+'</div>';
  $("v3Results").querySelectorAll("[data-collection]").forEach(b=>b.onclick=async()=>{const x=list[+b.dataset.collection];try{const d=await get("/api/"+(st.kind==="artists"?"artist":"album")+"/"+encodeURIComponent(x.id));if(d.tracks?.length)setQueue(d.tracks,0);}catch(e){toast(e.message);}});
 }else{$("v3Results").innerHTML='<div class="v3-track-list">'+list.map((t,i)=>row(t,i)).join("")+'</div>';bindRows($("v3Results"),list);}
 }catch(e){$("v3Results").innerHTML='<div class="v3-empty">Помилка пошуку: '+E(e.message)+'</div>';}
}
async function library(tab){
 st.tab=tab;const root=$("v3-library");
 root.innerHTML='<div class="v3-eyebrow">YOUR MUSIC / COLLECTION</div><h1 class="v3-page-title">Твоя колекція.</h1><p class="v3-subtitle">Усе, що ти зберіг, слухав і створив.</p><div class="v3-library-tabs">'+[["liked","Збережене"],["playlists","Плейлисти"],["history","Історія"],["moments","Моменти"]].map(x=>'<button class="'+(tab===x[0]?"active":"")+'" data-tab="'+x[0]+'">'+x[1]+'</button>').join("")+'</div><div id="v3LibraryBody"><div class="v3-empty">Завантажуємо…</div></div>';
 root.querySelectorAll("[data-tab]").forEach(b=>b.onclick=()=>library(b.dataset.tab));const box=$("v3LibraryBody");
 try{
  if(tab==="playlists"){const d=await get("/api/playlists"),ps=d.playlists||[];box.innerHTML='<button class="v3-pill active" id="v3Create">＋ Створити плейлист</button><div style="height:12px"></div><div class="v3-track-list">'+ps.map(p=>'<button class="v3-track" data-pl="'+E(p.id)+'"><span class="v3-avatar" style="width:42px;height:42px">♫</span><span class="v3-track-meta"><b>'+E(p.name)+'</b><small>'+(Number(p.count)||0)+' треків</small></span>›</button>').join("")+'</div>'+(ps.length?"":'<div class="v3-empty">Створи першу музичну добірку.</div>');$("v3Create").onclick=()=>openCreatePlaylist();box.querySelectorAll("[data-pl]").forEach(b=>b.onclick=()=>playlist(b.dataset.pl));return;}
  if(tab==="moments"){box.innerHTML='<div class="v3-empty">Збережені моменти можна відкривати з плеєра під час прослуховування.</div>';return;}
  const d=await get(tab==="liked"?"/api/library":"/api/history"),list=d.tracks||[];box.innerHTML=list.length?'<div class="v3-track-list">'+list.map((t,i)=>row(t,i)).join("")+'</div>':'<div class="v3-empty">Тут поки порожньо. Знайди музику й додай її до своєї колекції.</div>';bindRows(box,list);
 }catch(e){box.innerHTML='<div class="v3-empty">Не вдалося завантажити: '+E(e.message)+'</div>';}
}
async function playlist(id){
 try{const d=await get("/api/playlist/"+encodeURIComponent(id)),p=d.playlist||{},list=p.tracks||[],root=$("v3-library");root.innerHTML='<button class="v3-pill" id="v3Back">← До бібліотеки</button><div class="v3-profile-hero" style="margin:20px 0"><div class="v3-avatar">♫</div><div><div class="v3-eyebrow">PLAYLIST</div><h2>'+E(p.name)+'</h2><p>'+list.length+' треків</p></div></div><button class="v3-pill active" id="v3PlayAll">▶ Слухати все</button><div class="v3-track-list" style="margin-top:14px">'+list.map((t,i)=>row(t,i)).join("")+'</div>'; $("v3Back").onclick=()=>library("playlists");$("v3PlayAll").onclick=()=>{if(list.length)setQueue(list,0);};bindRows(root,list);}catch(e){toast(e.message);}
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
 const root=$("v3-profile");root.innerHTML='<div class="v3-empty">Завантажуємо профіль…</div>';
 try{const [m,d]=await Promise.all([get("/api/me"),get("/api/stats?days=30").catch(()=>({stats:{}}))]);const u=m.user||{},s=d.stats||{};S.me=u;S.limits=m.limits||S.limits;S.features=m.features||S.features;
 root.innerHTML='<div class="v3-eyebrow">YOUR MUSIC ID</div><h1 class="v3-page-title">Твій простір.</h1><p class="v3-subtitle">Акаунт, активність та можливості MusicLSP.</p><div class="v3-profile-hero" style="margin:20px 0"><div class="v3-avatar">'+E((u.name||"M").slice(0,1).toUpperCase())+'</div><div><div class="v3-eyebrow">'+(u.premium?"PREMIUM MEMBER":"MUSICLSP LISTENER")+'</div><h2>'+E(u.name||"Слухач")+'</h2><p>'+(u.premium?"Premium активний":"Безкоштовний план")+(u.premium_until?" · до "+E(String(u.premium_until).slice(0,10)):"")+'</p></div></div><div class="v3-profile-actions"><button class="v3-profile-action" data-prof="library"><b>♫ Моя музика</b><small>Збережене, плейлисти, історія</small></button><button class="v3-profile-action" data-prof="wrapped"><b>◷ Мої підсумки</b><small>Статистика та музичні звички</small></button><button class="v3-profile-action" data-prof="premium"><b>✦ '+(u.premium?"Premium":"Перейти на Premium")+'</b><small>Тарифи та можливості</small></button></div>'+section("Твоя активність · 30 днів",'<div class="v3-stats"><div class="v3-stat"><b>'+(Number(s.plays)||0)+'</b><small>прослуховувань</small></div><div class="v3-stat"><b>'+(Number(s.hours)||0)+'</b><small>годин музики</small></div><div class="v3-stat"><b>'+(Number(s.library)||0)+'</b><small>у колекції</small></div></div>')+section("Налаштування акаунта",'<div class="v3-track-list"><button class="v3-track" data-prof="premium"><span class="v3-avatar" style="width:38px;height:38px">✦</span><span class="v3-track-meta"><b>Підписка та оплата</b><small>Керування планом</small></span>›</button><button class="v3-track" data-prof="wrapped"><span class="v3-avatar" style="width:38px;height:38px">◷</span><span class="v3-track-meta"><b>Мій рік у музиці</b><small>Особиста статистика</small></span>›</button></div>');
 root.querySelectorAll("[data-prof]").forEach(b=>b.onclick=()=>{if(b.dataset.prof==="library")nav("library");if(b.dataset.prof==="wrapped")openWrapped();if(b.dataset.prof==="premium")openPlan();});
 }catch(e){root.innerHTML='<div class="v3-empty">Не вдалося завантажити профіль: '+E(e.message)+'</div>';}
}
function init(){build();searchPage();home();document.addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="k"){e.preventDefault();nav("search");$("v3SearchInput")?.focus();}});}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
window.MLSPv3={navigate:nav,home,explore,library,profile,search};
})();
