/* MusicLSP v4 boot: режим «вкладка браузера» (ПК). Підключається ПЕРШИМ синхронним скриптом у <head>.
   Усередині Telegram нічого не змінює. У звичайному браузері:
     • пам'ятає адресу API (?api=… або збережену), щоб вкладку можна було відкрити без параметрів;
     • якщо є дійсна веб-сесія — віддає її як INIT (той самий канал, що й initData у Mini App);
     • якщо сесії немає — показує екран входу через Telegram Login Widget. */
(function(){
  "use strict";
  var W=window, KEY_API="mlsp_api", KEY_TOK="mlsp_web_session";

  function store(key,val){
    try{
      if(val===undefined) return localStorage.getItem(key);
      if(val===null) localStorage.removeItem(key); else localStorage.setItem(key,val);
    }catch(e){}
    return null;
  }
  function tokenExpired(tok){
    try{
      var body=tok.split(".")[1].replace(/-/g,"+").replace(/_/g,"/");
      while(body.length%4) body+="=";
      var p=JSON.parse(decodeURIComponent(escape(atob(body))));
      return !(p.exp*1000>Date.now()+60000);
    }catch(e){ return true; }
  }
  function validToken(){
    var tok=store(KEY_TOK);
    if(tok && tok.indexOf("mlspweb.")===0 && !tokenExpired(tok)) return tok;
    if(tok) store(KEY_TOK,null);
    return "";
  }
  function inTelegram(){
    try{
      if(/tgWebAppData=/.test(location.hash||"")) return true;
      if(sessionStorage.getItem("__telegram__initParams")) return true;
    }catch(e){}
    return false;
  }

  var q=new URLSearchParams(location.search);
  var apiFromUrl=(q.get("api")||"").replace(/\/$/,"");
  if(apiFromUrl) store(KEY_API,apiFromUrl);
  var meta=document.querySelector('meta[name="mlsp-api"]');
  W.__MLSP_API=apiFromUrl||store(KEY_API)||(meta&&meta.content)||"";

  var tg=inTelegram(), token=tg?"":validToken();
  if(token) W.__MLSP_WEB_INIT=token;

  function el(tag,attrs,html){
    var n=document.createElement(tag);
    for(var k in (attrs||{})) n.setAttribute(k,attrs[k]);
    if(html!==undefined) n.innerHTML=html;
    return n;
  }
  function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];}); }

  function showLogin(){
    if(document.getElementById("v4Login")) return;
    var api=W.__MLSP_API;
    var box=el("div",{id:"v4Login",class:"v4-login",role:"dialog","aria-modal":"true","aria-labelledby":"v4LoginTitle"});
    box.innerHTML='<div class="v4-login-card">'
      +'<div class="v4-login-logo" aria-hidden="true"></div>'
      +'<h1 id="v4LoginTitle">MusicLSP у браузері</h1>'
      +'<p class="v4-login-sub">Увійди через Telegram, щоб слухати з комп’ютера. Бібліотека, плейлисти та підписки — ті самі, що й у боті.</p>'
      +'<div id="v4LoginBody"></div></div>';
    document.body.appendChild(box);
    var body=box.querySelector("#v4LoginBody");

    if(!api){
      body.innerHTML='<label class="v4-login-label" for="v4Api">Адреса API (сервіс bot на Railway)</label>'
        +'<input id="v4Api" class="v4-login-input" placeholder="https://your-app.up.railway.app" inputmode="url" autocomplete="off">'
        +'<button id="v4ApiSave" class="v4-login-btn" type="button">Зберегти</button>'
        +'<p class="v4-login-hint">Її можна не вводити, якщо відкрити посилання з параметром <code>?api=…</code>.</p>';
      body.querySelector("#v4ApiSave").onclick=function(){
        var v=(body.querySelector("#v4Api").value||"").trim().replace(/\/$/,"");
        if(!/^https:\/\/[^\s/]+/.test(v)) return;
        store(KEY_API,v); location.reload();
      };
      return;
    }
    body.innerHTML='<p class="v4-login-hint" id="v4LoginState">Завантажую…</p><div id="v4Widget" class="v4-widget"></div>';
    var state=body.querySelector("#v4LoginState"), host=body.querySelector("#v4Widget");
    fetch(api+"/api/web-config").then(function(r){return r.json();}).then(function(cfg){
      if(!cfg||!cfg.web_login||!cfg.bot_username){
        state.textContent="Вхід із браузера не налаштовано на сервері (потрібен BOT_TOKEN і username бота).";
        return;
      }
      state.innerHTML='Натисни кнопку нижче. Якщо вона не з’являється — в @BotFather виконай <code>/setdomain</code> для адреси цього сайту (<b>'+esc(location.hostname)+'</b>) для бота @'+esc(cfg.bot_username)+'.';
      W.MLSPWebOnAuth=function(user){
        state.textContent="Перевіряю вхід…";
        fetch(api+"/api/auth/web-login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(user)})
          .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
          .then(function(res){
            if(res.ok&&res.d&&res.d.token){ store(KEY_TOK,res.d.token); location.reload(); }
            else state.textContent=(res.d&&res.d.error)||"Не вдалося увійти. Спробуй ще раз.";
          }).catch(function(){ state.textContent="Немає зв’язку із сервером."; });
      };
      var s=document.createElement("script");
      s.async=true; s.src="https://telegram.org/js/telegram-widget.js?22";
      s.setAttribute("data-telegram-login",cfg.bot_username);
      s.setAttribute("data-size","large"); s.setAttribute("data-radius","12");
      s.setAttribute("data-userpic","false"); s.setAttribute("data-onauth","MLSPWebOnAuth(user)");
      host.appendChild(s);
    }).catch(function(){ state.textContent="Не вдалося зв’язатися із сервером. Перевір адресу API."; });
  }

  W.MLSPWeb={
    inTelegram:function(){ return tg; },
    isWebSession:function(){ return !!W.__MLSP_WEB_INIT; },
    needLogin:function(){ return !tg && !W.__MLSP_WEB_INIT; },
    showLogin:function(){ if(document.body) showLogin(); else document.addEventListener("DOMContentLoaded",showLogin); },
    logout:function(){ store(KEY_TOK,null); location.reload(); },
    forgetApi:function(){ store(KEY_API,null); }
  };
})();