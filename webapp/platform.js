/* MusicLSP discovery extension. Does not bind to, modify, or replace player widgets/Connect. */
(()=>{
"use strict";
const $=id=>document.getElementById(id);
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const getData=async path=>{
 if(typeof window.get==="function")return window.get(path);
 const response=await fetch(path,{credentials:"same-origin"});
 const data=await response.json();if(!response.ok||data.ok===false)throw new Error(data.error||"Request failed");return data;
};
let loaded=false, data=null;
function entityCard(item,kind){
 const artist=kind==="artist",name=item.name||item.title||"Без назви",sub=artist?(item.source_label||item.source||"Артист"):(item.artist||item.source_label||"Альбом");
 return '<button class="platform-entity '+(artist?"artist":"album")+'" data-platform-kind="'+kind+'" data-platform-id="'+esc(item.id)+'"><span class="platform-entity-art">'+(item.cover?'<img loading="lazy" src="'+esc(item.cover)+'" alt="" onerror="this.style.opacity=.12">':'')+'</span><b>'+esc(name)+'</b><small>'+esc(sub)+'</small></button>';
}
function render(){
 const root=$("v3-home");if(!root||!data)return;
 let host=$("platformDiscovery");
 if(!host){host=document.createElement("section");host.id="platformDiscovery";host.className="platform-discovery";root.appendChild(host);}
 host.innerHTML='<div class="platform-discovery-head"><div><div class="platform-discovery-kicker">MUSICLSP / GLOBAL CATALOGUE</div><h2>Відкривай артистів та альбоми</h2><p>Єдиний каталог із підключених музичних джерел</p></div></div>'+
 '<div class="platform-discovery-tabs"><button class="active" data-platform-tab="artists">Артисти</button><button data-platform-tab="albums">Альбоми</button></div>'+
 '<div class="platform-entity-grid" id="platformEntities">'+(data.artists?.length?data.artists.slice(0,18).map(x=>entityCard(x,"artist")).join(""):'<div class="platform-empty">Каталог артистів тимчасово недоступний.</div>')+'</div>';
 const grid=$("platformEntities");
 host.querySelectorAll("[data-platform-tab]").forEach(button=>button.addEventListener("click",()=>{
  host.querySelectorAll("[data-platform-tab]").forEach(x=>x.classList.toggle("active",x===button));
  const list=button.dataset.platformTab==="artists"?data.artists:data.albums,kind=button.dataset.platformTab==="artists"?"artist":"album";
  grid.innerHTML=list?.length?list.slice(0,18).map(x=>entityCard(x,kind)).join(""):'<div class="platform-empty">У цьому розділі поки немає результатів.</div>';
 }));
 grid.addEventListener("click",event=>{
  const button=event.target.closest("[data-platform-kind]");if(!button)return;
  const kind=button.dataset.platformKind,list=kind==="artist"?data.artists:data.albums;
  const item=list?.find(x=>String(x.id)===button.dataset.platformId);
  if(item&&window.MLSPv3?.openCollection)window.MLSPv3.openCollection(kind==="artist"?"artists":"albums",item);
 });
}
async function load(){
 if(loaded)return;loaded=true;
 try{data=await getData("/api/discover");render();}
 catch(error){const root=$("v3-home");if(root&&!$("platformDiscovery"))root.insertAdjacentHTML("beforeend",'<section class="platform-discovery"><div class="platform-discovery-kicker">GLOBAL CATALOGUE</div><h2>Каталог артистів та альбомів</h2><p class="platform-empty">Не вдалося завантажити каталог. Спробуй оновити сторінку.</p></section>');}
}
function watch(){
 const root=$("v3-home");if(!root)return;
 const observer=new MutationObserver(()=>{
  if(root.querySelector(".v3-home-heading"))load();
 });
 observer.observe(root,{childList:true,subtree:false});
 if(root.querySelector(".v3-home-heading"))load();
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",watch,{once:true});else watch();
})();
