
/* LOCK='qrcode' waa qaabka la doortay. Ku beddel null si badhanka dooridda loo soo celiyo */
var LOCK = 'qrcode';
var THEMES=[
 ['aurora','Aurora','linear-gradient(135deg,#0b1437,#3b1f7a)'],
 ['ocean','Badweyn','linear-gradient(135deg,#031a3a,#0aa5b8)'],
 ['sunset','Qorrax-dhac','linear-gradient(135deg,#3b0a5c,#ff8a3d)'],
 ['forest','Kayn','linear-gradient(135deg,#052e1f,#35c48a)'],
 ['stars','Xiddigo','linear-gradient(135deg,#02030f,#261052)'],
 ['light','Iftiin','linear-gradient(135deg,#f4f8ff,#f6e9ff)'],
 ['royal','Boqor','linear-gradient(135deg,#07070c,#c9962b)'],
 ['neon','Neon','linear-gradient(135deg,#1a0033,#ff2ec4)'],
 ['qrcode','QR Code','linear-gradient(135deg,#020b0a,#00ff9c)'],
 ['idcard','Aqoonsi','linear-gradient(135deg,#0b2a6f,#38bdf8)'],
 ['shield','Xaqiijin','linear-gradient(135deg,#04201f,#1fcf9a)'],
 ['report','Warbixin','linear-gradient(135deg,#e8eef9,#7aa7ff)'],
 ['barcode','Barcode','linear-gradient(135deg,#0f172a,#ff2d55)'],
 ['radar','Radar','linear-gradient(135deg,#020617,#22d3ee)']
];
(function(){
  var ids=THEMES.map(function(t){return t[0]});
  function get(){try{return localStorage.getItem('qrTheme')}catch(e){return null}}
  function set(v){try{localStorage.setItem('qrTheme',v)}catch(e){}}
  var q=new URLSearchParams(location.search).get('theme');
  var cur=LOCK||(ids.indexOf(q)>-1?q:null)||(ids.indexOf(get())>-1?get():'aurora');
  var r=function(a,b){return a+Math.random()*(b-a)};
  var st=document.getElementById('stars');
  for(var i=0;i<60;i++){var d=document.createElement('span');var z=r(1,3);
    d.style.cssText='left:'+r(0,100)+'%;top:'+r(0,100)+'%;width:'+z+'px;height:'+z+'px;animation-delay:'+r(0,4)+'s;animation-duration:'+r(2,5)+'s';st.appendChild(d)}
  var bb=document.getElementById('bubbles');
  for(var j=0;j<18;j++){var b=document.createElement('span');var sz=r(10,46);
    b.style.cssText='left:'+r(0,100)+'%;width:'+sz+'px;height:'+sz+'px;animation-duration:'+r(9,20)+'s;animation-delay:-'+r(0,18)+'s;--dx:'+r(-40,40)+'px';bb.appendChild(b)}

  var SP={
   qrcode:['<svg viewBox="0 0 48 48" fill="currentColor"><path fill-rule="evenodd" d="M2 2h16v16H2zM6 6v8h8V6zM30 2h16v16H30zM34 6v8h8V6zM2 30h16v16H2zM6 34v8h8v-8z"/><path d="M8 8h4v4H8zM36 8h4v4h-4zM8 36h4v4H8zM22 2h4v4h-4zM22 10h4v4h-4zM22 22h4v4h-4zM30 22h4v4h-4zM38 22h8v4h-8zM22 30h4v8h-4zM30 34h4v4h-4zM38 30h4v4h-4zM30 42h8v4h-8zM42 38h4v8h-4z"/></svg>',14,[26,56]],
   idcard:['<svg viewBox="0 0 64 40" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="1.5" width="61" height="37" rx="6"/><circle cx="17" cy="16" r="5.5"/><path d="M8 31c0-5 4-8.5 9-8.5s9 3.500 9 8.500"/><path d="M33 13h22M33 21h16M33 29h20"/></svg>',10,[50,92]],
   shield:['<svg viewBox="0 0 48 56" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M24 3 6 10v16c0 12 8 21 18 26 10-5 18-14 18-26V10z"/><path d="m16 28 6 6 11-12"/></svg>',12,[28,58]],
   photoC:['<div class="pfl c"><img class="im" alt=""><span class="vbm"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 5 5 9-10"/></svg></span></div>',6,[34,58]],
   photoR:['<div class="pfl r"><img class="im" alt=""><span class="vbm"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 5 5 9-10"/></svg></span></div>',6,[34,52]],
   badge:['<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M24 3l5 4 6-.5 2 6 5 3-2 6 2 6-5 3-2 6-6-.5-5 4-5-4-6 .5-2-6-5-3 2-6-2-6 5-3 2-6 6 .5z"/><path d="m16 25 6 6 11-13"/></svg>',6,[28,52]],
   report:['<svg viewBox="0 0 48 60" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h26l12 12v44H6z"/><path d="M32 2v12h12"/><path d="M14 28h20M14 36h20M14 44h12"/></svg>',10,[34,64]]
  };
  var fl=document.getElementById('floaters');
  var studentPhotoUrl=fl.getAttribute('data-photo-url');
  document.documentElement.style.setProperty('--ph','url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 120 150%27%3E%3Crect width=%27120%27 height=%27150%27 rx=%2718%27 fill=%27%23e7edf4%27/%3E%3Ccircle cx=%2760%27 cy=%2750%27 r=%2725%27 fill=%27%2395a4b8%27/%3E%3Cpath d=%27M15 140c2-31 20-48 45-48s43 17 45 48%27 fill=%27%2395a4b8%27/%3E%3C/svg%3E")');
  var MIX={qrcode:['qrcode','idcard','report','shield','badge','photoC','photoR']};
  function fill(id){
    fl.innerHTML='';var keys=MIX[id]||(SP[id]?[id]:null);if(!keys)return;
    var total=MIX[id]?28:SP[id][1];
    for(var i=0;i<total;i++){var key=keys[i%keys.length];var c=SP[key];var e=document.createElement('span');var w=r(c[2][0],c[2][1]);
      e.style.cssText='left:'+r(0,92)+'%;width:'+w+'px;animation-duration:'+r(12,24)+'s;animation-delay:-'+r(0,22)+'s;--dx:'+r(-50,50)+'px;--r0:'+r(-25,25)+'deg;--r1:'+r(-45,45)+'deg';
      e.innerHTML=c[0];
      if(/^photo/.test(key)){
        e.className='ph';
        var image=e.querySelector('.im');
        if(studentPhotoUrl&&image){
          image.loading='lazy';image.decoding='async';image.referrerPolicy='no-referrer';
          image.onerror=function(){this.removeAttribute('src')};
          image.src=studentPhotoUrl;
        }
      }
      fl.appendChild(e)}
  }
  var bars=document.getElementById('bars');
  for(var n=0;n<18;n++){var g=document.createElement('span');g.style.cssText='--h:'+Math.round(r(35,100))+'%;animation-delay:-'+r(0,3.6)+'s;animation-duration:'+r(2.6,4.6)+'s';bars.appendChild(g)}
  var grid=document.getElementById('swGrid'),btns={};
  THEMES.forEach(function(t){
    var e=document.createElement('button');e.className='sw';e.type='button';
    e.innerHTML='<i style="background:'+t[2]+'"></i>'+t[1];
    e.onclick=function(){apply(t[0]);set(t[0])};grid.appendChild(e);btns[t[0]]=e});
  function apply(id){cur=id;fill(id);document.body.setAttribute('data-theme',id);
    var m=document.querySelector('meta[name=theme-color]');
    for(var k in btns)btns[k].classList.toggle('on',k===id)}
  apply(cur);
  var pb=document.getElementById('pickBtn'),pn=document.getElementById('panel');
  if(LOCK){pb.style.display='none';pn.style.display='none';return}
  pb.onclick=function(e){e.stopPropagation();var o=pn.classList.toggle('open');pb.setAttribute('aria-expanded',o)};
  document.addEventListener('click',function(e){if(!pn.contains(e.target)){pn.classList.remove('open');pb.setAttribute('aria-expanded',false)}});
})();
