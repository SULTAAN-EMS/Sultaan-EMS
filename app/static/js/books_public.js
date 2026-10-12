(() => {
  'use strict';

  const mount = document.getElementById('booksPublicMount');
  const library = window.BookCoverLibrary;
  if (!mount || !library) return;
  const books = JSON.parse(document.getElementById('booksPublicBootstrap')?.textContent || '[]');
  const config = JSON.parse(document.getElementById('booksPublicConfig')?.textContent || '{}');
  const shadow = mount.attachShadow({mode:'open'});
  const icons = document.getElementById('booksIcons')?.innerHTML || '';
  const template = document.getElementById('booksPublicTemplate');
  shadow.innerHTML = `<link rel="stylesheet" href="/static/css/books_reference.css?v=books-hero-shorter-20261011"><style>
    :host{display:block;min-height:100dvh;background:var(--bg);color:var(--text)}
    .public-shell{width:min(100%,520px);margin:0 auto;background:var(--bg);min-height:100dvh;position:relative;box-shadow:0 0 44px rgba(10,43,39,.08)}
    .screen{height:auto;min-height:100dvh;border:0;border-radius:0;overflow:visible;background:var(--bg)}
    .pscroll{height:auto;min-height:100dvh;overflow:visible;display:flex;flex-direction:column}
    .rw{--rw-g:#0A5F4F;--rw-deep:#06352c;--rw-gold:#D4AF5F;--rw-gold-soft:#F1DDA6;position:relative;isolation:isolate;overflow:hidden;display:flex;align-items:center;justify-content:center;gap:11px;width:100%;min-width:0;min-height:54px;margin-top:auto;padding:9px 14px calc(11px + env(safe-area-inset-bottom,0px));background:linear-gradient(100deg,var(--rw-deep),var(--rw-g) 50%,var(--rw-deep));color:#DCEDE7;font-family:'DM Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:13.5px;line-height:1.2;white-space:nowrap;box-sizing:border-box;text-align:initial}
    .rw *{box-sizing:border-box}
    .rw::before{content:"";position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,transparent,var(--rw-gold),var(--rw-gold-soft),var(--rw-gold),transparent);pointer-events:none}
    .rw::after{content:"";position:absolute;top:0;bottom:0;width:32%;left:-40%;background:linear-gradient(100deg,transparent,rgba(241,221,166,.18),transparent);animation:rw-glide 7s ease-in-out infinite;pointer-events:none;z-index:-1}
    .rw-ic{position:relative;z-index:1;flex:none;width:30px;height:30px;border-radius:50%;background:linear-gradient(145deg,var(--rw-gold-soft),var(--rw-gold));color:#04211b;perspective:80px;animation:rw-pulse 2.8s ease-in-out infinite}
    .rw-ico{position:absolute;inset:0;margin:auto;width:17px;height:17px;display:block;opacity:0;transform:rotateY(90deg) scale(.5);transition:opacity .3s ease,transform .5s cubic-bezier(.3,1.5,.5,1)}
    .rw-ico.on{opacity:1;transform:none}
    .rw-logo-ico{width:24px;height:24px;overflow:hidden;border-radius:50%}
    .rw-school-logo{display:block;width:100%;height:100%;padding:2px;border-radius:50%;background:#fff;object-fit:contain}
    .rw-school-fallback{display:none;width:100%;height:100%}
    .rw-logo-ico.fallback .rw-school-logo{display:none}
    .rw-logo-ico.fallback .rw-school-fallback{display:block}
    .rw-msg{position:relative;z-index:1;display:inline-block;flex:0 1 auto;min-width:0;max-width:100%}
    .rw-ghost{visibility:hidden;display:inline-block;white-space:nowrap}
    .rw-live{position:absolute;left:0;top:0;white-space:nowrap}
    .rw-live span:empty:not(.rw-cur){display:none}
    .rw-b{font-family:'Sora',system-ui,sans-serif;font-weight:700;letter-spacing:.02em;color:var(--rw-gold-soft)}
    .rw[data-kind="copy"] .rw-b{color:#fff}
    .rw-sep{margin:0 .6em;color:var(--rw-gold)}
    .rw-r{color:rgba(220,237,231,.9)}
    .rw-cur{display:inline-block;vertical-align:-.15em;margin-left:2px;width:.5em;height:1.1em;background:var(--rw-gold);animation:rw-blink 1s steps(1) infinite}
    .rw-bar{position:absolute;left:14px;right:14px;bottom:4px;height:3px;display:flex;gap:4px;z-index:1}
    .rw-seg{position:relative;flex:1;height:100%;border-radius:3px;background:rgba(255,255,255,.14)}
    .rw-fill{display:block;position:absolute;left:0;top:0;bottom:0;width:0;border-radius:3px;background:linear-gradient(90deg,#b8862f,var(--rw-gold),#fff1c1,var(--rw-gold),#b8862f);background-size:200% 100%;animation:rw-flow 1.8s linear infinite;transition:none}
    .rw-fill::after{content:"";position:absolute;right:-3px;top:50%;width:7px;height:7px;margin-top:-3.5px;border-radius:50%;background:#fff1c1;box-shadow:0 0 8px 2px rgba(241,221,166,.8);opacity:0;transition:opacity .3s ease}
    .rw-seg.on .rw-fill::after{opacity:1}
    @keyframes rw-flow{to{background-position:-200% 0}}
    @keyframes rw-blink{50%{opacity:0}}
    @keyframes rw-glide{0%{left:-40%}55%,100%{left:120%}}
    @keyframes rw-pulse{0%,100%{box-shadow:0 0 0 3px rgba(212,175,95,.25)}50%{box-shadow:0 0 0 9px rgba(212,175,95,0)}}
    @media(max-width:560px){.rw{font-size:12.5px;gap:9px;padding-left:10px;padding-right:10px}.rw-bar{left:10px;right:10px}}
    @media(max-width:340px){.rw-ic{display:none}}
    @media(prefers-reduced-motion:reduce){.rw::after,.rw-ic,.rw-cur,.rw-fill{animation:none!important}.rw-ico,.rw-fill::after{transition:none!important}}
    .hero{padding-top:max(6px,env(safe-area-inset-top))}
    .reader{position:fixed;top:0;bottom:0;left:50%;right:auto;width:min(100vw,520px);transform:translateX(-50%);z-index:100;background:var(--rbg)}
    .reader.show{display:block}.rbody{overscroll-behavior:contain;padding-bottom:calc(176px + env(safe-area-inset-bottom))}
    .rbot{padding-bottom:calc(22px + env(safe-area-inset-bottom))}
    .pdfpages{display:flex;flex-direction:column;align-items:center;gap:12px}.pdfpage{position:relative;width:calc(100% - 18px);max-width:500px;background:var(--rpaper);box-shadow:0 2px 14px rgba(0,0,0,.2);margin:0 auto;overflow:hidden}.pdfpage canvas{display:block;width:100%;height:auto}.pdftext{position:absolute;inset:0;overflow:hidden;opacity:1;line-height:1;transform-origin:0 0;user-select:text}.pdftext span{position:absolute;white-space:pre;transform-origin:0 0;color:transparent;cursor:text}.pdftext span.hit{background:#ffe066;color:transparent}.pdftext span.current-hit{background:#ff9f1c;box-shadow:0 0 0 2px #ff9f1c}
    .reader[data-mode="page"] .rbody{scroll-snap-type:y mandatory}.reader[data-mode="page"] .pdfpage{scroll-snap-align:start;scroll-margin-top:64px}
    .reader[data-theme="sepia"] .pdfpage canvas{filter:sepia(.28) saturate(.82) brightness(.98)}.reader[data-theme="dark"] .pdfpage canvas{filter:invert(.88) hue-rotate(180deg) brightness(.82)}
    .rsh-bg,.rsheet,.dbg,.dsheet{position:fixed;left:50%;right:auto;width:min(100vw,520px);transform:translateX(-50%)}
    .rsh-bg{inset:0;transform:translateX(-50%);z-index:101}.rsheet{bottom:0;z-index:102;transform:translate(-50%,105%)}.rsheet.show{transform:translate(-50%,0)}
    .dbg{inset:0;transform:translateX(-50%);z-index:110}.dsheet{bottom:0;z-index:111;transform:translate(-50%,105%)}.dsheet.show{transform:translate(-50%,0)}
    .toast{position:fixed;left:50%;width:max-content;max-width:calc(min(100vw,520px) - 32px);z-index:120}
    .pg-tools{display:grid;gap:8px}.pg-tools input,.pg-tools select{width:100%;height:44px;border:1px solid var(--border);border-radius:11px;background:var(--surface);padding:0 12px;color:var(--text)}
    .pg-tools button,.sc button{padding:10px 12px;border-radius:10px;background:var(--surface2);color:var(--text);text-align:left}
    .thumbs{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.thumbs button{padding:5px;text-align:center}.thumbs canvas{display:block;width:100%;height:auto;background:#fff;margin-bottom:5px}
    .empty-reader{padding:22px 8px;color:var(--muted);text-align:center}.theme-options{display:flex;gap:8px}.theme-options button[aria-pressed="true"]{outline:2px solid var(--brand)}
    .filter-stack{display:grid;gap:14px;padding:14px 16px 0}.filter-block{min-width:0}.filter-block[hidden],.sbox[hidden]{display:none!important}.filter-label{display:block;margin:0 0 7px;color:var(--muted);font-size:12px;font-weight:800}.filter-hint{display:block;margin:-2px 0 8px;color:var(--muted);font-size:11px}.filter-block>.pfield,.branch-filter{margin:0}.filter-block .pf{padding:0}.filter-subjects .chips{padding:0;margin:0}
    .catalog-switch{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0}.catalog-switch button{display:flex;align-items:center;gap:10px;min-width:0;padding:12px;border:1px solid var(--border);border-radius:13px;background:var(--surface);text-align:left}.catalog-switch button[aria-pressed="true"]{border-color:var(--brand);background:var(--brandSoft);color:var(--brandText)}.catalog-switch button>span{display:grid;gap:2px;min-width:0}.catalog-switch small{font-size:11px;color:var(--muted)}.catalog-switch button:disabled{opacity:.55;cursor:not-allowed}
    .pseg{grid-template-columns:repeat(5,minmax(0,1fr));gap:3px}.pseg button{min-width:0;white-space:nowrap;gap:4px;padding-inline:2px}.pseg button .ic{width:14px;height:14px;color:var(--c,var(--muted));stroke-width:1.8}.pseg button[aria-pressed="true"] .ic{color:currentColor}
    .sbox{margin:14px 16px 12px}
    .print-centers{padding:16px 16px 14px;display:grid;gap:12px}.print-centers[hidden]{display:none!important}.print-center{position:relative;overflow:hidden;display:grid;gap:14px;padding:16px;border:1px solid color-mix(in srgb,var(--brand) 20%,var(--border));border-radius:17px;background:linear-gradient(145deg,var(--surface),color-mix(in srgb,var(--brandSoft) 34%,var(--surface)));box-shadow:0 9px 24px rgba(10,43,39,.09);animation:pc-enter .34s ease both}.print-center:before{content:"";position:absolute;inset:0 0 auto;height:4px;background:linear-gradient(90deg,var(--brand),#e7b544,#20a47c)}.pc-head{display:flex;align-items:center;gap:12px;min-width:0}.pc-emblem{display:grid;place-items:center;flex:none;width:48px;height:48px;border-radius:14px;background:linear-gradient(145deg,var(--brand),color-mix(in srgb,var(--brand) 72%,#f5b400));color:#fff;box-shadow:0 6px 14px color-mix(in srgb,var(--brand) 22%,transparent)}.pc-emblem .pc-icon{width:24px;height:24px}.pc-heading{display:grid;gap:3px;min-width:0;flex:1}.pc-heading small{color:var(--brandText);font-size:9px;font-weight:850;letter-spacing:.11em}.pc-heading h3{margin:0;overflow-wrap:anywhere;color:var(--text);font-size:16px;font-weight:850;line-height:1.2}.pc-active{display:inline-flex;align-items:center;gap:6px;flex:none;padding:6px 9px;border:1px solid #c9eadb;border-radius:999px;background:#effaf4;color:#18724d;font-size:9px;font-weight:800}.pc-active i{width:6px;height:6px;border-radius:50%;background:#20a36b;box-shadow:0 0 0 3px rgba(32,163,107,.13)}.pc-location{display:flex;align-items:flex-start;gap:10px;padding:11px 12px;border:1px solid var(--border);border-radius:12px;background:color-mix(in srgb,var(--surface) 84%,transparent)}.pc-location-icon{display:grid;place-items:center;flex:none;width:31px;height:31px;border-radius:10px;background:#fff0e7;color:#bd5b25}.pc-location-copy{display:grid;gap:2px;min-width:0}.pc-location-copy small{color:var(--muted);font-size:9px;font-weight:800;letter-spacing:.08em}.pc-location-copy strong{overflow-wrap:anywhere;color:var(--text);font-size:12px;line-height:1.4}.pc-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.pc-action{display:flex;align-items:center;gap:9px;min-width:0;min-height:54px;padding:9px 11px;border:1px solid var(--border);border-radius:12px;background:var(--surface);color:var(--text);text-decoration:none;transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}.pc-action:hover{transform:translateY(-2px);box-shadow:0 7px 16px rgba(10,43,39,.1)}.pc-action.call:hover{border-color:#71cdb8}.pc-action.whatsapp:hover{border-color:#76d39b}.pc-action:focus-visible{outline:3px solid color-mix(in srgb,var(--brand) 55%,transparent);outline-offset:2px}.pc-action.map{grid-column:1/-1;background:linear-gradient(100deg,#fff9ed,var(--surface))}.pc-action.map:hover{border-color:#e9c36e}.pc-action-icon{display:grid;place-items:center;flex:none;width:34px;height:34px;border-radius:10px;background:#e8f7f3;color:#087f6b}.pc-action.whatsapp .pc-action-icon{background:#e8f8ee;color:#1c9b59}.pc-action.map .pc-action-icon{background:#fff0d6;color:#a9680c}.pc-action-copy{display:grid;gap:2px;min-width:0;flex:1}.pc-action-copy small{color:var(--muted);font-size:9px;font-weight:800;letter-spacing:.08em}.pc-action-copy strong{overflow-wrap:anywhere;color:var(--text);font-size:12px;line-height:1.25}.pc-external{width:14px;height:14px;flex:none;color:var(--muted)}.pc-icon{display:block;width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.pc-icon.whatsapp{fill:currentColor;stroke:none}:host([data-theme="dark"]) .print-center{border-color:#31554e;background:linear-gradient(145deg,#172c28,#1a3730);box-shadow:0 10px 24px rgba(0,0,0,.22)}:host([data-theme="dark"]) .pc-location{background:#172522;border-color:#344c45}:host([data-theme="dark"]) .pc-location-icon{background:#392a20;color:#ffad77}:host([data-theme="dark"]) .pc-active{border-color:#28583f;background:#173124;color:#8ee0ae}:host([data-theme="dark"]) .pc-action{border-color:#344c45;background:#172522}:host([data-theme="dark"]) .pc-action.map{background:linear-gradient(100deg,#342b1d,#172522)}:host([data-theme="dark"]) .pc-action-icon{background:#183b34;color:#72d5bd}:host([data-theme="dark"]) .pc-action.whatsapp .pc-action-icon{background:#183a29;color:#75d99c}:host([data-theme="dark"]) .pc-action.map .pc-action-icon{background:#3b301c;color:#f3c46f}@media(prefers-color-scheme:dark){:host:not([data-theme="light"]) .print-center{border-color:#31554e;background:linear-gradient(145deg,#172c28,#1a3730);box-shadow:0 10px 24px rgba(0,0,0,.22)}:host:not([data-theme="light"]) .pc-location{background:#172522;border-color:#344c45}:host:not([data-theme="light"]) .pc-location-icon{background:#392a20;color:#ffad77}:host:not([data-theme="light"]) .pc-active{border-color:#28583f;background:#173124;color:#8ee0ae}:host:not([data-theme="light"]) .pc-action{border-color:#344c45;background:#172522}:host:not([data-theme="light"]) .pc-action.map{background:linear-gradient(100deg,#342b1d,#172522)}:host:not([data-theme="light"]) .pc-action-icon{background:#183b34;color:#72d5bd}:host:not([data-theme="light"]) .pc-action.whatsapp .pc-action-icon{background:#183a29;color:#75d99c}:host:not([data-theme="light"]) .pc-action.map .pc-action-icon{background:#3b301c;color:#f3c46f}}@media(prefers-reduced-motion:reduce){.print-center,.pc-action{animation:none;transition:none}}@keyframes pc-enter{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:translateY(0)}}
    .pc-emblem{position:relative}.pc-emblem .pc-logo{width:100%;height:100%;padding:4px;border-radius:inherit;background:#fff;object-fit:contain}.pc-verified{position:absolute;right:-5px;bottom:-5px;display:grid;place-items:center;width:20px;height:20px;border:2px solid #fff;border-radius:50%;background:#19b96a;color:#fff;box-shadow:0 2px 6px rgba(10,43,39,.25)}.pc-verified .pc-icon{width:11px;height:11px;stroke-width:3}
    @media(max-width:380px){.catalog-switch{gap:6px}.catalog-switch button{padding:9px 7px;gap:6px}.catalog-switch button b{font-size:12px}.catalog-switch small{font-size:10px}}
    @media(max-width:380px){.pseg{gap:2px;padding:3px}.pseg button{font-size:9px;gap:1px}.pseg button .ic{width:11px;height:11px}.bk{gap:9px;padding:9px}.bk-a button{font-size:12px}}
    .pc-book-contact{display:flex!important;align-items:center;justify-content:center;gap:8px;width:100%;min-height:38px;padding:8px 10px;border:1px solid #b8e3d5;border-radius:11px;background:linear-gradient(105deg,#effaf6,#f7fcfa);color:#087a63;font-weight:750;font-size:13px;cursor:pointer;transition:background .16s ease,border-color .16s ease,transform .16s ease}.pc-book-contact:hover{background:#e4f6ef;border-color:#6fc6a9;transform:translateY(-1px)}.pc-book-contact:focus-visible,.pc-dialog-close:focus-visible,.pc-contact-link:focus-visible{outline:3px solid #45ae91;outline-offset:2px}.pc-book-contact .ic{width:16px;height:16px;flex:none}
    .pc-contact-printer{display:block;width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none}
    .bk-a{flex-wrap:wrap}.bk-a .pc-book-contact{flex:1 0 100%}
    .plist.list .bk-a{flex-wrap:nowrap;gap:6px}.plist.list .bk-a button{min-width:0;flex:1 1 0;padding-inline:6px;white-space:nowrap}.plist.list .bk-a .rd{flex:.8 1 0}.plist.list .bk-a .dl{flex:.9 1 0}.plist.list .bk-a .pc-book-contact{width:auto;flex:2 1 0;gap:5px;padding:8px 6px;font-size:13.5px;white-space:nowrap}
    .plist.grid .gk-a{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.plist.grid .gk-a button{min-width:0;white-space:nowrap}.plist.grid .gk-a .pc-book-contact{grid-column:1/-1}
    .plist.grid .gk .s{display:flex;align-items:center;gap:7px;min-width:0;color:var(--muted);font-size:13px;line-height:1.3}.plist.grid .gk .s .subject-icon{width:15px;height:15px;flex:0 0 15px}.plist.grid .gk .s .gk-subject-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.book-page-count{display:inline-flex;align-items:center;gap:4px;flex:none;margin-left:auto;color:var(--muted);font-size:11px;line-height:1.2;white-space:nowrap}.book-page-count .ic{width:14px;height:14px;flex:none}.plist.grid .gk .book-page-count{gap:3px;margin-left:auto;font-size:10px}.plist.grid .gk .book-page-count .ic{width:12px;height:12px}.plist.list .bk-b .s .bk-subject-name{min-width:0;overflow-wrap:anywhere}
    .pc-contact-short{display:none}
    @media(max-width:480px){.plist.list .bk-a button{flex:1 1 0;gap:3px;padding-inline:3px;font-size:10px}.plist.list .bk-a .rd,.plist.list .bk-a .dl,.plist.list .bk-a .pc-book-contact{flex:1 1 0}.plist.list .bk-a .pc-book-contact{gap:3px;font-size:10px}.plist.list .bk-a .pc-book-contact .ic{width:14px;height:14px}.pc-contact-full{display:none}.pc-contact-short{display:inline}}
    @media(max-width:360px){.plist.list .bk-a button{gap:0;padding-inline:0;font-size:0}.plist.list .bk-a .pc-book-contact .ic{width:17px;height:17px}.pc-book-contact .pc-contact-printer{width:17px;height:17px}.pc-contact-short{display:none}}
    .pc-dialog{position:fixed;inset:0;margin:auto;width:min(460px,calc(100vw - 32px));max-width:none;max-height:min(84dvh,720px);padding:0;overflow:hidden;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text);box-shadow:0 24px 80px rgba(8,35,30,.28)}.pc-dialog::backdrop{background:rgba(9,27,24,.48);backdrop-filter:blur(4px)}
    .pc-dialog-head{display:flex;align-items:center;gap:12px;padding:18px 18px 15px;border-bottom:1px solid var(--border);background:linear-gradient(120deg,var(--surface),color-mix(in srgb,#0E7C66 8%,var(--surface)))}.pc-dialog-icon{display:grid;place-items:center;flex:none;width:42px;height:42px;border-radius:13px;background:#e6f5ef;color:#087a63}.pc-dialog-icon .ic{width:21px;height:21px}.pc-dialog-head>div{min-width:0;flex:1}.pc-dialog-head small{display:block;margin-bottom:3px;color:#16856d;font-size:9px;font-weight:850;letter-spacing:.12em}.pc-dialog-head h2{margin:0;color:var(--text);font-family:var(--display);font-size:18px;line-height:1.2}.pc-dialog-close{display:grid;place-items:center;flex:none;width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--muted);cursor:pointer}.pc-dialog-close .ic{width:17px;height:17px}.pc-dialog-body{display:grid;gap:12px;max-height:calc(min(84dvh,720px) - 78px);overflow:auto;padding:16px;overscroll-behavior:contain}.pc-contact-card{display:grid;gap:12px;padding:15px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 3px 12px rgba(10,43,39,.05)}.pc-contact-card h3{margin:0;color:var(--text);font-family:var(--display);font-size:16px;font-weight:800;overflow-wrap:anywhere}.pc-contact-address{display:flex;align-items:flex-start;gap:8px;margin:0;color:var(--muted);font-size:13px;line-height:1.45}.pc-contact-address .pc-contact-icon-svg{width:17px;height:17px;flex:none;color:#b87022}.pc-contact-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.pc-contact-link{display:flex;align-items:center;gap:9px;min-width:0;min-height:48px;padding:8px 10px;border:1px solid var(--border);border-radius:11px;background:var(--surface);color:var(--text);text-decoration:none;transition:border-color .16s ease,background .16s ease}.pc-contact-link:hover{background:var(--surface2);border-color:#9ccfbd}.pc-contact-link>span:first-child{display:grid;place-items:center;flex:none;width:31px;height:31px;border-radius:9px;background:#e7f6f1;color:#087a63}.pc-contact-link.whatsapp>span:first-child{background:#e9f8ee;color:#1d9b59}.pc-contact-link.map>span:first-child{background:#fff2dc;color:#a9680c}.pc-contact-icon-svg{display:block;width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.pc-contact-link.whatsapp .pc-contact-icon-svg{width:18px;height:18px;fill:currentColor;stroke:none}.pc-contact-link>span:last-child{display:grid;gap:2px;min-width:0}.pc-contact-link small{color:var(--muted);font-size:9px;font-weight:850;letter-spacing:.07em}.pc-contact-link strong{overflow-wrap:anywhere;font-size:12px;line-height:1.25}
    .pc-dialog-close{border-color:#c62828;background:#dc2626;color:#fff;box-shadow:0 3px 9px rgba(185,28,28,.24);transition:transform .16s ease,border-color .16s ease,background .16s ease,box-shadow .16s ease}.pc-dialog-close .ic{color:#fff;stroke:#fff}.pc-dialog-close:hover{transform:translateY(-1px);border-color:#991b1b;background:#b91c1c;box-shadow:0 4px 12px rgba(185,28,28,.32)}.pc-dialog-close:active{transform:scale(.95);background:#991b1b}.pc-dialog-close:focus-visible{outline-color:#ef4444}
    :host([data-theme="dark"]) .pc-dialog-close{border-color:#ef4444;background:#dc2626;color:#fff}:host([data-theme="dark"]) .pc-dialog-close:hover{border-color:#f87171;background:#b91c1c}
    @media(prefers-color-scheme:dark){:host:not([data-theme="light"]) .pc-dialog-close{border-color:#ef4444;background:#dc2626;color:#fff}:host:not([data-theme="light"]) .pc-dialog-close:hover{border-color:#f87171;background:#b91c1c}}
    .pc-confirm-dialog{position:fixed;inset:0;margin:auto;width:min(360px,calc(100vw - 32px));max-width:none;padding:18px;border:1px solid var(--border);border-radius:17px;background:var(--surface);color:var(--text);box-shadow:0 20px 64px rgba(8,35,30,.28)}.pc-confirm-dialog::backdrop{background:rgba(9,27,24,.5);backdrop-filter:blur(3px)}.pc-confirm-head{display:flex;align-items:center;gap:11px}.pc-confirm-head h2{margin:0;font-family:var(--display);font-size:17px;line-height:1.25}.pc-confirm-icon{display:grid;place-items:center;flex:none;width:40px;height:40px;border-radius:12px;background:#e6f5ef;color:#087a63}.pc-confirm-icon .pc-icon,.pc-confirm-icon .pc-contact-icon-svg{width:20px;height:20px}.pc-confirm-message{margin:14px 0 18px;color:var(--muted);font-size:14px;line-height:1.5;overflow-wrap:anywhere}.pc-confirm-message strong{color:var(--text)}.pc-confirm-actions{display:grid;grid-template-columns:1fr 1.2fr;gap:8px}.pc-confirm-actions button{min-height:42px;padding:9px 12px;border:1px solid var(--border);border-radius:10px;font:inherit;font-size:13px;font-weight:750;cursor:pointer}.pc-confirm-cancel{background:var(--surface);color:var(--muted)}.pc-confirm-continue{border-color:#087a63!important;background:#087a63;color:#fff}.pc-confirm-actions button:focus-visible{outline:3px solid #45ae91;outline-offset:2px}
    .pc-confirm-message{color:#285447}.pc-confirm-message strong{color:#08664f}:host([data-theme="dark"]) .pc-confirm-message{color:#d1dfd9}:host([data-theme="dark"]) .pc-confirm-message strong{color:#72d6b5}@media(prefers-color-scheme:dark){:host:not([data-theme="light"]) .pc-confirm-message{color:#d1dfd9}:host:not([data-theme="light"]) .pc-confirm-message strong{color:#72d6b5}}
    .pc-disclaimer-boundary{display:grid;grid-template-columns:30px minmax(0,1fr);align-items:start;gap:9px;margin-top:12px;padding:10px;border:1px solid color-mix(in srgb,#b9851c 28%,var(--border));border-left:3px solid #b9851c;border-radius:11px;background:color-mix(in srgb,#f3c75b 9%,var(--surface));color:var(--text);font-weight:600;line-height:1.45}.pc-disclaimer-icon{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;background:#fff2cf;color:#93640a}.pc-disclaimer-icon svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}:host([data-theme="dark"]) .pc-disclaimer-boundary{border-color:#66552d;border-left-color:#efc568;background:#302a1c;color:#e8dfc8}:host([data-theme="dark"]) .pc-disclaimer-icon{background:#453819;color:#f0c665}@media(prefers-color-scheme:dark){:host:not([data-theme="light"]) .pc-disclaimer-boundary{border-color:#66552d;border-left-color:#efc568;background:#302a1c;color:#e8dfc8}:host:not([data-theme="light"]) .pc-disclaimer-icon{background:#453819;color:#f0c665}}
    .pc-confirm-dialog[open]{animation:pc-confirm-enter .18s ease-out both}.pc-confirm-dialog[open]::backdrop{animation:pc-confirm-backdrop .18s ease-out both}@keyframes pc-confirm-enter{from{opacity:.96;transform:translateY(5px) scale(.99)}to{opacity:1;transform:translateY(0) scale(1)}}@keyframes pc-confirm-backdrop{from{opacity:0}to{opacity:1}}
    .pc-choice-note{display:grid;grid-template-columns:34px minmax(0,1fr);align-items:start;gap:10px;margin-top:2px;padding:12px;border:1px solid #efd4d2;border-left:3px solid #b94b4b;border-radius:13px;background:#fff7f6;color:#3e3333}.pc-choice-note-icon{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:#fde9e7;color:#ad3434}.pc-choice-note-icon svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.pc-choice-note-copy{min-width:0}.pc-choice-note h3{margin:0 0 4px;color:#a83232;font-family:var(--display);font-size:13px;font-weight:800;line-height:1.25}.pc-choice-note p{margin:0;color:#514344;font-size:12px;line-height:1.5;overflow-wrap:anywhere}:host([data-theme="dark"]) .pc-choice-note{border-color:#633b3a;border-left-color:#df7771;background:#2b2020;color:#f0dfde}:host([data-theme="dark"]) .pc-choice-note-icon{background:#482b2a;color:#ff9d96}:host([data-theme="dark"]) .pc-choice-note h3{color:#ffaaa4}:host([data-theme="dark"]) .pc-choice-note p{color:#e2cecd}@media(prefers-color-scheme:dark){:host:not([data-theme="light"]) .pc-choice-note{border-color:#633b3a;border-left-color:#df7771;background:#2b2020;color:#f0dfde}:host:not([data-theme="light"]) .pc-choice-note-icon{background:#482b2a;color:#ff9d96}:host:not([data-theme="light"]) .pc-choice-note h3{color:#ffaaa4}:host:not([data-theme="light"]) .pc-choice-note p{color:#e2cecd}}
    .pc-choice-note p{color:#b42318;font-weight:500}:host([data-theme="dark"]) .pc-choice-note p{color:#ff9d96}@media(prefers-color-scheme:dark){:host:not([data-theme="light"]) .pc-choice-note p{color:#ff9d96}}
    @media(prefers-reduced-motion:reduce){.pc-book-contact,.pc-contact-link,.pc-dialog-close{transition:none}.pc-confirm-dialog[open],.pc-confirm-dialog[open]::backdrop{animation:none}}
    @media(max-width:520px){.pc-dialog{inset:auto 0 0;width:min(100vw,520px);max-height:82dvh;margin:0 auto;border-radius:22px 22px 0 0;border-bottom:0}.pc-dialog-body{max-height:calc(82dvh - 78px);padding-bottom:calc(18px + env(safe-area-inset-bottom))}.pc-dialog-head h2{font-size:14px;white-space:nowrap}}
    @media(max-width:360px){.pc-dialog-head{gap:8px;padding-left:12px;padding-right:12px}.pc-dialog-icon{width:36px;height:36px}.pc-dialog-close{width:34px;height:34px}.pc-dialog-head h2{font-size:13px}}
    .pc-dialog-body{padding:20px 16px 36px}@media(max-width:520px){.pc-dialog-body{padding:18px 16px calc(30px + env(safe-area-inset-bottom))}}
    .bk-a .dl,.gk-a .dl{border:1px solid #2563eb;background:#2563eb;color:#fff}.bk-a .dl:hover,.gk-a .dl:hover{border-color:#1d4ed8;background:#1d4ed8}.bk-a .pc-book-contact,.gk-a .pc-book-contact{border-color:#a96508;background:linear-gradient(105deg,#9b5706,#bd760b);color:#fff}.bk-a .pc-book-contact:hover,.gk-a .pc-book-contact:hover{border-color:#874b05;background:linear-gradient(105deg,#874b05,#a96508)}
    .pc-dialog{inset:50% auto auto 50%;height:auto;max-height:94dvh;transform:translate(-50%,-50%);display:block}.pc-dialog:not([open]){display:none}.pc-dialog[open]{display:block}.pc-dialog-body{max-height:calc(94dvh - 78px);align-content:normal}
    @media(max-width:520px){.pc-dialog{inset:auto 0 0;height:auto;max-height:94dvh;margin:0 auto;transform:none}.pc-dialog-body{max-height:calc(94dvh - 78px)}}
  </style>${icons}${template.content.firstElementChild.outerHTML}`;
  const pageStylesheet = shadow.querySelector('link[rel="stylesheet"]');
  const revealBooks = () => {
    mount.dataset.booksReady = 'true';
    mount.setAttribute('aria-busy', 'false');
  };
  if (pageStylesheet) {
    pageStylesheet.addEventListener('load', revealBooks, {once:true});
    pageStylesheet.addEventListener('error', revealBooks, {once:true});
  } else {
    revealBooks();
  }
  const $ = selector => shadow.querySelector(selector);
  const $$ = selector => [...shadow.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const ALL = library.ALL;
  const GENERAL = 'Buug guud';
  const LEVELS = library.LEVELS;
  const SUBJECT_CATALOG = config.subjectCatalog || {};
  const LEVEL_ICONS = {[ALL]:'layers',Xanaano:'star',Hoose:'bookopen',Dhexe:'class',Sare:'landmark'};
  const state = {level:ALL,cls:ALL,subject:ALL,query:'',catalogType:'',branchId:'',view:'grid',readerBook:null,pdf:null,page:1,mode:'scroll',zoom:1,theme:'light',marks:{},downloadBook:null,downloadController:null,downloadStatusTimer:null,objectUrl:null,searchHits:[],searchIndex:0,pageObserver:null,pageCountObserver:null,pageCountJobs:new Map(),pageCountFailures:new Set(),pendingContact:null,searchRun:0};
  const filterQueryKeys = ['branch_id','catalog','level','class','subject','q','view'];
  function restoreFilterState() {
    const params = new URL(location.href).searchParams;
    const branchId = params.get('branch_id') || '';
    state.branchId = (config.branches || []).some(branch => String(branch.id) === branchId) ? branchId : '';
    state.catalogType = ['national','local'].includes(params.get('catalog')) ? params.get('catalog') : '';
    state.level = params.get('level') || ALL;
    state.cls = params.get('class') || ALL;
    state.subject = params.get('subject') || ALL;
    state.query = (params.get('q') || '').slice(0, 120);
    state.view = params.get('view') === 'list' ? 'list' : 'grid';
    $('#pq').value = state.query;
  }
  function syncFilterStateToUrl() {
    const url = new URL(location.href);
    const values = {
      branch_id: state.branchId,
      catalog: state.catalogType,
      level: state.level === ALL ? '' : state.level,
      class: state.cls === ALL ? '' : state.cls,
      subject: state.subject === ALL ? '' : state.subject,
      q: state.query.trim(),
      view: state.view === 'list' ? '' : state.view
    };
    filterQueryKeys.forEach(key => {
      if (values[key]) url.searchParams.set(key, values[key]);
      else url.searchParams.delete(key);
    });
    history.replaceState(history.state, '', url);
  }
  restoreFilterState();
  const sym = name => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const crest = () => config.logoUrl ? `<img class="crest crest-img" src="${esc(config.logoUrl)}" alt="">` : '<svg class="crest" viewBox="0 0 24 26" aria-hidden="true"><use href="#crest"/></svg>';
  const subjectSym = name => `<span class="subject-icon" style="--subject-icon:url('/static/icons/books-fa-free/${esc(name.replace(/^fa-/,''))}.svg')" aria-hidden="true"></span>`;
  const classText = book => book.class === ALL ? 'Heer dhan' : library.classLabel(book.class);
  const subjectText = book => book.bookKind==='general' ? GENERAL : book.subject===ALL ? 'Dhammaan maaddooyinka' : book.subject;
  const pageCountMarkup = book => `${sym('pages')}<span>${Number(book.pages)>0?`${Number(book.pages)} bog`:state.pageCountFailures.has(String(book.id))?'Lama xaqiijin':'Hubinaya…'}</span>`;
  function loadPdfjs() {
    if (!window.pdfjsLibPromise) window.pdfjsLibPromise = import('/static/vendor/pdfjs/pdf.min.mjs').then(module => {
      module.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.mjs';
      return module;
    });
    return window.pdfjsLibPromise;
  }
  function applyPageCount(book, pages) {
    const count = Number(pages);
    if (!Number.isInteger(count) || count < 1 || count > 100000) return;
    book.pages = count;
    state.pageCountFailures.delete(String(book.id));
    try { localStorage.setItem(`books-page-count:${book.id}`, JSON.stringify({updatedAt:book.updatedAt,pages:count})); } catch (_) {}
    $$('[data-page-count]').filter(node => node.dataset.pageCount === String(book.id)).forEach(node => { node.innerHTML = pageCountMarkup(book); });
  }
  async function loadMissingPageCount(book) {
    const id = String(book.id);
    if (Number(book.pages)>0 || state.pageCountFailures.has(id) || state.pageCountJobs.has(id)) return;
    const job = (async () => {
      let task;
      try {
        const pdfjs = await loadPdfjs();
        task = pdfjs.getDocument({url:book.pdfUrl,rangeChunkSize:65536,disableStream:true,disableAutoFetch:true});
        const pdf = await task.promise;
        applyPageCount(book,pdf.numPages);
        if (!(Number(book.pages)>0)) throw new Error('PDF page count unavailable');
      } catch (_) {
        state.pageCountFailures.add(id);
        $$('[data-page-count]').filter(node => node.dataset.pageCount === id).forEach(node => { node.innerHTML = `${sym('pages')}<span>Lama xaqiijin</span>`; });
      } finally {
        await task?.destroy().catch(() => {});
        state.pageCountJobs.delete(id);
      }
    })();
    state.pageCountJobs.set(id,job);
    return job;
  }
  function observeMissingPageCounts() {
    state.pageCountObserver?.disconnect();
    state.pageCountObserver = null;
    const targets = $$('[data-page-count]').filter(node => {
      const book = books.find(item => String(item.id) === node.dataset.pageCount);
      return book && !(Number(book.pages)>0) && !state.pageCountFailures.has(node.dataset.pageCount);
    });
    if (!targets.length || !('IntersectionObserver' in window)) return;
    state.pageCountObserver = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) {
        state.pageCountObserver.unobserve(entry.target);
        const book = books.find(item => String(item.id) === entry.target.dataset.pageCount);
        if (book) loadMissingPageCount(book);
      }
    },{root:$('#pscroll'),rootMargin:'120px'});
    targets.forEach(node => state.pageCountObserver.observe(node));
  }
  const coverHtml = (book, size='md') => {
    if (book.coverUrl && book.hasCover) return `<div class="cover ${size} custom" style="background-image:url('${esc(book.coverUrl)}')" role="img" aria-label="Cover-ka ${esc(book.title)}"></div>`;
    const subject = library.SUBJ[book.scope === 'subject' ? book.subject : ALL] || library.SUBJ[ALL];
    const tag = book.class !== ALL ? library.classLabel(book.class) : `${book.level} · Heer dhan`;
    return `<div class="cover ${size}" style="--a:${subject.a};--b:${subject.b}"><svg class="art" viewBox="0 0 100 133" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${subject.art()}</svg><span class="spine"></span><div class="cv"><div class="cv-top">${crest()}<span>TAYSIIR<em>INTERNATIONAL SCHOOLS</em></span></div><i class="cv-gold"></i><div class="cv-mid"><div class="emb">${subjectSym(subject.i)}</div></div><div class="cv-bot"><small>${esc(subject.n)}</small><b>${esc(book.title)}</b><span class="tag">${esc(tag)}</span></div></div></div>`;
  };
  // Cover presence is deliberately separate from the private filename.
  books.forEach(book => {
    book.hasCover = Boolean(book.hasCover);
    if (!(Number(book.pages)>0)) {
      try {
        const cached = JSON.parse(localStorage.getItem(`books-page-count:${book.id}`) || 'null');
        if (cached?.updatedAt === book.updatedAt && Number(cached.pages)>0) book.pages = Number(cached.pages);
      } catch (_) {}
    }
  });
  $('#schoolName').textContent = config.schoolName || 'Taysiir International Schools';
  function initCopyrightFooter() {
    const root = $('#publicFooter');
    if (!root) return;
    const year = new Date().getFullYear();
    const copy = {icon:4,h:'\u00A9 SULTAAN EMS \u2014 BOOKS',s:'|',t:String(year),hold:10000,copy:true};
    const messages = [
      copy,
      {icon:0,h:'Akhri',s:'\u2014',t:'Halakan ku dhex Akhri Buugga aad dooneyso',hold:2800},
      {icon:1,h:'La Degid',s:'\u2014',t:'Waad la degi kadhaa buug, si toos ah Teleefankaaga',hold:2800},
      copy,
      {icon:2,h:'Buug kasta',s:'\u2014',t:'waa albaab cilmi',hold:2800},
      {icon:3,h:'TIS',s:'\u2014',t:'Dugsiyada Caalamiga ah ee Taysiir',hold:2800},
      copy
    ];
    const msg = root.querySelector('.rw-msg');
    const iconNodes = [...root.querySelectorAll('.rw-ico')];
    const schoolLogo = root.querySelector('.rw-school-logo');
    const schoolLogoIcon = root.querySelector('.rw-logo-ico');
    const segments = [...root.querySelectorAll('.rw-seg')];
    const mobile = window.matchMedia('(max-width:560px)');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let timer = 0;
    let index = 0;
    let parts = [];
    let total = 0;
    let position = 0;
    let direction = 1;
    let delays = [];

    if (schoolLogo && schoolLogoIcon) {
      if (config.logoUrl) {
        schoolLogo.src = config.logoUrl;
        schoolLogo.addEventListener('error',() => schoolLogoIcon.classList.add('fallback'),{once:true});
      } else {
        schoolLogoIcon.classList.add('fallback');
      }
    }
    root.setAttribute('aria-label', `© SULTAAN EMS — BOOKS | ${year}`);

    function draw() {
      let remaining = position;
      parts.forEach(part => {
        const count = Math.max(0, Math.min(part.text.length, remaining));
        remaining -= count;
        part.node.textContent = part.text.slice(0, count);
      });
    }

    function fillAll() {
      segments.forEach(segment => {
        segment.classList.remove('on');
        const fill = segment.querySelector('.rw-fill');
        fill.style.transition = 'none';
        fill.style.width = '100%';
      });
    }

    function startProgress(activeIndex, duration) {
      segments.forEach((segment, segmentIndex) => {
        const fill = segment.querySelector('.rw-fill');
        segment.classList.toggle('on', segmentIndex === activeIndex);
        fill.style.transition = 'none';
        fill.style.width = segmentIndex < activeIndex ? '100%' : '0%';
      });
      void segments[activeIndex].offsetWidth;
      const activeFill = segments[activeIndex].querySelector('.rw-fill');
      activeFill.style.transition = `width ${duration}ms linear`;
      activeFill.style.width = '100%';
    }

    function showMessage(nextIndex) {
      clearTimeout(timer);
      index = nextIndex;
      const item = messages[index];
      root.dataset.kind = item.copy ? 'copy' : 'tip';
      iconNodes.forEach((node, iconIndex) => node.classList.toggle('on', iconIndex === item.icon));
      root.classList.toggle('rw-static', reducedMotion.matches);
      msg.replaceChildren();
      parts = [];
      total = 0;
      const spans = [{className:'rw-b',text:item.h}];
      if (item.t) {
        if (item.s) spans.push({className:'rw-sep',text:item.s});
        spans.push({className:'rw-r',text:item.t});
      }
      const ghost = document.createElement('span');
      ghost.className = 'rw-ghost';
      ghost.setAttribute('aria-hidden','true');
      const live = document.createElement('span');
      live.className = 'rw-live';
      live.setAttribute('aria-hidden','true');
      spans.forEach(span => {
        const ghostPart = document.createElement('span');
        ghostPart.className = span.className;
        ghostPart.textContent = span.text;
        ghost.appendChild(ghostPart);
        const livePart = document.createElement('span');
        livePart.className = span.className;
        live.appendChild(livePart);
        parts.push({node:livePart,text:span.text});
        total += span.text.length;
      });
      msg.append(ghost,live);
      position = 0;
      direction = 1;

      if (reducedMotion.matches) {
        position = total;
        draw();
        fillAll();
        return;
      }

      const cursor = document.createElement('span');
      cursor.className = 'rw-cur';
      cursor.setAttribute('aria-hidden','true');
      live.appendChild(cursor);
      draw();
      delays = [];
      let duration = 380;
      for (let charIndex = 0; charIndex < total; charIndex++) {
        const delay = 50 + Math.random() * 70;
        delays.push(delay);
        duration += delay;
      }
      duration += item.hold + total * 18;
      startProgress(index,duration);
      timer = window.setTimeout(tick,380);
    }

    function tick() {
      draw();
      let delay;
      if (direction > 0) {
        if (position < total) {
          delay = delays[position];
          position++;
        } else {
          direction = -1;
          delay = messages[index].hold;
        }
      } else if (position > 0) {
        position--;
        delay = 18;
      } else {
        showMessage((index + 1) % messages.length);
        return;
      }
      timer = window.setTimeout(tick,delay);
    }

    function restartCurrent() {
      showMessage(index);
    }
    if (mobile.addEventListener) mobile.addEventListener('change',restartCurrent);
    else mobile.addListener(restartCurrent);
    const motionChanged = () => restartCurrent();
    if (reducedMotion.addEventListener) reducedMotion.addEventListener('change',motionChanged);
    else reducedMotion.addListener(motionChanged);
    showMessage(reducedMotion.matches ? messages.length - 1 : 0);
  }
  initCopyrightFooter();
  function branchBooks() {
    if (!state.branchId) return [];
    const branchId = Number(state.branchId);
    return books.filter(book => book.visible && (
      book.catalogType === 'national'
        ? !book.branchIds?.length || book.branchIds.includes(branchId)
        : book.catalogType === 'local' && book.branchIds?.includes(branchId)
    ));
  }
  function catalogBooks() {
    return state.catalogType ? branchBooks().filter(book => book.catalogType === state.catalogType) : [];
  }
  function scopedBooks() {
    return catalogBooks().filter(book => (state.level === ALL || book.level === state.level)
      && (state.cls === ALL || book.class === ALL || book.class === state.cls));
  }
  function renderFilters() {
    $('#pBranch').innerHTML=`<option value="" disabled hidden ${state.branchId?'':'selected'}>Dooro Xarunta Dugsiga</option>${(config.branches||[]).map(branch=>`<option value="${branch.id}">${esc(branch.name)}${branch.region?` · ${esc(branch.region)}`:''}</option>`).join('')}`;
    $('#pBranch').value=state.branchId;
    const branchItems = branchBooks();
    const availableTypes = new Set(branchItems.map(book => book.catalogType));
    if (!availableTypes.has(state.catalogType)) state.catalogType = '';
    $('#pCatalog').querySelectorAll('[data-catalog]').forEach(button=>{
      const available = availableTypes.has(button.dataset.catalog);
      button.disabled = !state.branchId || !available;
      button.setAttribute('aria-pressed',String(button.dataset.catalog===state.catalogType));
      const hint = button.querySelector('[data-catalog-hint]');
      hint.textContent = 'Halakan Buugaag kuma Jiraan';
      hint.hidden = !state.branchId || available;
    });
    $('#pCatalogHint').textContent = !state.branchId
      ? 'Dooro xarunta dugsiga marka hore.'
      : availableTypes.size
        ? 'Dooro nooca buugga si loo muujiyo heerarka iyo fasallada xaruntan.'
        : 'Xaruntan weli buug dadweyne ah laguma darin.';
    const candidates = catalogBooks();
    const levels = Object.keys(LEVELS).filter(level => candidates.some(book => book.level===level));
    if (state.level!==ALL && !levels.includes(state.level)) state.level=ALL;
    $('#pAcademicFilters').hidden = !state.catalogType;
    $('#pSubjectFilters').hidden = !state.catalogType;
    $('#pSearch').hidden = !state.catalogType;
    const levelOptions = [ALL,...levels];
    $('#pLevel').style.gridTemplateColumns = `repeat(${levelOptions.length}, minmax(0, 1fr))`;
    $('#pLevel').innerHTML = levelOptions.map(level => `<button type="button" data-level="${esc(level)}" aria-pressed="${state.level===level}" style="--c:${LEVELS[level]?.c || '#0E7C66'}">${sym(LEVEL_ICONS[level] || 'class')}${level===ALL?'Dhammaan':esc(level)}</button>`).join('');
    const classBooks = candidates.filter(book => state.level===ALL || book.level===state.level);
    const availableClasses = new Set();
    classBooks.forEach(book => {
      if (book.class===ALL) (LEVELS[book.level]?.classes||[]).forEach(name=>availableClasses.add(name));
      else availableClasses.add(book.class);
    });
    const classes = library.allClasses.filter(name=>availableClasses.has(name));
    if (state.cls!==ALL && !availableClasses.has(state.cls)) state.cls=ALL;
    $('#pClass').innerHTML = `<option value="${ALL}">Dhammaan fasallada</option>${classes.map(name=>`<option value="${esc(name)}">${esc(library.classLabel(name))}</option>`).join('')}`;
    $('#pClass').value = state.cls;
    const subjectBooks = scopedBooks();
    const availableSubjects = new Set();
    subjectBooks.forEach(book => {
      if (book.bookKind==='general') availableSubjects.add(GENERAL);
      else if (book.subject===ALL) {
        const classNames = state.cls===ALL
          ? (book.class===ALL ? (LEVELS[book.level]?.classes || []) : [book.class])
          : [state.cls];
        classNames.forEach(className => (SUBJECT_CATALOG[book.level]?.[className] || []).forEach(subject=>availableSubjects.add(subject)));
      } else availableSubjects.add(book.subject);
    });
    if (state.subject!==ALL && !availableSubjects.has(state.subject)) state.subject=ALL;
    $('#pSubj').innerHTML = [ALL,...[...availableSubjects].filter(subject=>subject!==GENERAL).sort((a,b)=>a.localeCompare(b)),...(availableSubjects.has(GENERAL)?[GENERAL]:[])].map(subject=>`<button type="button" class="chip" data-subject="${esc(subject)}" aria-pressed="${state.subject===subject}">${subjectSym((library.SUBJ[subject]||library.SUBJ[ALL]).i)}${subject===ALL?'Dhammaan maaddooyinka':esc(subject)}</button>`).join('');
  }
  function filteredBooks() {
    const query = state.query.trim().toLocaleLowerCase();
    return catalogBooks().filter(book => (state.level===ALL || book.level===state.level) && (state.cls===ALL || book.class===state.cls || book.class===ALL) && (state.subject===ALL || (state.subject===GENERAL ? book.bookKind==='general' : book.bookKind!=='general' && (book.subject===state.subject || book.subject===ALL))) && (!query || `${book.title} ${subjectText(book)} ${book.class} ${classText(book)} ${book.description}`.toLocaleLowerCase().includes(query)));
  }
  function renderBooks() {
    const list = filteredBooks();
    const branch=(config.branches||[]).find(item=>String(item.id)===String(state.branchId));
    const contactButton=state.catalogType==='local'&&(branch?.printCenters||[]).length
      ? `<button class="pc-book-contact" type="button" data-printer-contact aria-label="La xidhiidh Madbacad" title="La xidhiidh Madbacad"><svg class="ic pc-contact-printer" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v7H6zM18 12h.01"/></svg><span class="pc-contact-full">La xidhiidh Madbacad</span><span class="pc-contact-short" aria-hidden="true">Xidhiidh</span></button>`
      : '';
    $('#pCount').innerHTML = `<b>${list.length}</b> ${list.length===1?'buug':'buug'}`;
    $('#pvGrid').setAttribute('aria-pressed',String(state.view==='grid'));
    $('#pvList').setAttribute('aria-pressed',String(state.view==='list'));
    $('#pList').className = `plist ${state.view}`;
    $('#pList').innerHTML = list.length ? list.map(book => state.view==='grid'
      ? `<article class="gk"><button type="button" data-read="${book.id}" aria-label="Akhri ${esc(book.title)}">${coverHtml(book,'md')}</button><div class="m"><span class="lv" style="--c:${LEVELS[book.level]?.c}"><i></i>${esc(book.level)}</span><span class="cls">${esc(classText(book))}</span></div><h3>${esc(book.title)}</h3><div class="s">${subjectSym((library.SUBJ[book.subject]||library.SUBJ[ALL]).i)}<span class="gk-subject-name" title="${esc(subjectText(book))}">${esc(subjectText(book))}</span><span class="book-page-count" data-page-count="${book.id}">${pageCountMarkup(book)}</span></div><div class="gk-a"><button class="rd" type="button" data-read="${book.id}">${sym('bookopen')} Akhri</button><button class="dl" type="button" data-download="${book.id}">${sym('download')} La Dag</button></div></article>`
      : `<article class="bk"><button type="button" data-read="${book.id}" aria-label="Akhri ${esc(book.title)}" style="display:block;flex:none;align-self:flex-start">${coverHtml(book,'md')}</button><div class="bk-b"><div class="m"><span class="lv" style="--c:${LEVELS[book.level]?.c}"><i></i>${esc(book.level)}</span><span class="cls">${esc(classText(book))}</span></div><h3>${esc(book.title)}</h3><div class="s">${subjectSym((library.SUBJ[book.subject]||library.SUBJ[ALL]).i)}<span class="bk-subject-name">${esc(subjectText(book))}</span><span class="book-page-count" data-page-count="${book.id}">${pageCountMarkup(book)}</span></div><p>${esc(book.description)}</p><div class="bk-a"><button class="rd" type="button" data-read="${book.id}">${sym('eye')} Akhri</button><button class="dl" type="button" data-download="${book.id}">${sym('download')} La Dag</button></div></div></article>`).join('')
      : `<div class="empty"><div class="big">${sym('bookopen')}</div><b>${!state.branchId?'Marka hore dooro xarunta dugsiga':!branchBooks().length?'Xaruntan buug laguma darin':!state.catalogType?'Dooro nooca buugga':'Buug lama helin'}</b><p>${!state.branchId?'Dooro xarunta si loo soo saaro noocyada buugga, heerarka iyo fasallada ku habboon.':!branchBooks().length?'Ma jiro buug dadweyne ah oo xaruntan loo xulay.':'Isku day erey kale ama beddel shaandhaynta.'}</p></div>`;
    if(contactButton) $$('#pList .gk-a, #pList .bk-a').forEach(actions=>actions.insertAdjacentHTML('beforeend',contactButton));
    observeMissingPageCounts();
  }
  function renderPrinterContactsSimple() {
    const branch=(config.branches||[]).find(item=>String(item.id)===String(state.branchId));
    const centers=state.catalogType==='local'?(branch?.printCenters||[]):[];
    if(!centers.length){toast('Madbacad xiriir leh lama helin.');return}
    const icon=(path,viewBox)=>'<svg class="pc-contact-icon-svg" viewBox="'+(viewBox||'0 0 24 24')+'" aria-hidden="true">'+path+'</svg>';
    const phoneIcon=icon('<path d="M21 16.5v3a2 2 0 0 1-2.2 2A19.8 19.8 0 0 1 10.2 18a19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 1.8 3.2 2 2 0 0 1 3.8 1h3a2 2 0 0 1 2 1.7l.5 2.8a2 2 0 0 1-.6 1.7L7 8.9a16 16 0 0 0 6 6l1.7-1.7a2 2 0 0 1 1.7-.6l2.8.5a2 2 0 0 1 1.8 1.4Z"/>');
    const pinIcon=icon('<path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>');
    const mapIcon=icon('<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/>');
    const whatsappIcon=icon('<path d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/>','0 0 448 512');
    $('#pcDialogBody').innerHTML=centers.map(center=>{
      const digits=String(center.whatsapp||'').replace(/\D/g,'');
      const phone=String(center.phone||'').trim();
      const links=(phone?'<a class="pc-contact-link call" href="tel:'+esc(phone)+'"><span>'+phoneIcon+'</span><span><small>WAC</small><strong>'+esc(phone)+'</strong></span></a>':'')+(digits?'<a class="pc-contact-link whatsapp" target="_blank" rel="noopener noreferrer" href="https://wa.me/'+esc(digits)+'"><span>'+whatsappIcon+'</span><span><small>WHATSAPP</small><strong>'+esc(center.whatsapp)+'</strong></span></a>':'')+(center.mapUrl?'<a class="pc-contact-link map" target="_blank" rel="noopener noreferrer" href="'+esc(center.mapUrl)+'"><span>'+mapIcon+'</span><span><small>GOOBTA KHARIIDADDA</small><strong>Ka eeg khariidadda</strong></span></a>':'');
      return '<article class="pc-contact-card"><h3>'+esc(center.name)+'</h3>'+(center.address?'<p class="pc-contact-address">'+pinIcon+'<span>'+esc(center.address)+'</span></p>':'')+'<div class="pc-contact-actions">'+links+'</div></article>';
    }).join('');
    $('#printerContactDialog').showModal();
  }
  function openPrinterContacts() {
    const wrap=$('#pcDialogBody');
    const branch=(config.branches||[]).find(item=>String(item.id)===String(state.branchId));
    if(state.catalogType!=='local'||!branch){wrap.innerHTML='';return}
    const centers=branch.printCenters||[];
    const icon = (name, extraClass = '') => {
      const paths = {
        printer: '<path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v7H6zM18 12h.01"/>',
        pin: '<path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
        phone: '<path d="M21 16.5v3a2 2 0 0 1-2.2 2A19.8 19.8 0 0 1 10.2 18a19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 1.8 3.2 2 2 0 0 1 3.8 1h3a2 2 0 0 1 2 1.7l.5 2.8a2 2 0 0 1-.6 1.7L7 8.9a16 16 0 0 0 6 6l1.7-1.7a2 2 0 0 1 1.7-.6l2.8.5a2 2 0 0 1 1.8 1.4Z"/>',
        // Font Awesome Free 6.5.2 WhatsApp brand glyph, licensed CC BY 4.0.
        whatsapp: '<path d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/>',
        map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/>',
        external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
        check: '<path d="m5 12 4 4L19 6"/>'
      };
      const viewBox=name==='whatsapp'?'0 0 448 512':'0 0 24 24';
      const svg=`<svg class="pc-icon ${extraClass}" viewBox="${viewBox}" aria-hidden="true">${paths[name]||''}</svg>`;
      return svg;
    };
    wrap.innerHTML=centers.length?centers.map(center=>{
      const waDigits=String(center.whatsapp||'').replace(/\D/g,'');
      const logoUrl=String(center.logoUrl||'').trim();
      const emblem=logoUrl?`<img class="pc-logo" src="${esc(logoUrl)}" alt="" loading="lazy">`:icon('printer');
      return `<article class="print-center"><header class="pc-head"><span class="pc-emblem">${emblem}<span class="pc-verified" aria-hidden="true">${icon('check')}</span></span><div class="pc-heading"><small>XARUNTA DAABACAADDA</small><h3>${esc(center.name)}</h3></div><span class="pc-active"><i></i>Firfircoon</span></header><div class="pc-location"><span class="pc-location-icon">${icon('pin')}</span><span class="pc-location-copy"><small>GOOBTA MADBACADDA</small><strong>${esc(center.address)}</strong></span></div><div class="pc-actions"><a class="pc-action call" href="tel:${esc(center.phone)}" aria-label="Wac ${esc(center.phone)}"><span class="pc-action-icon">${icon('phone')}</span><span class="pc-action-copy"><small>WAC</small><strong>${esc(center.phone)}</strong></span></a>${waDigits?`<a class="pc-action whatsapp" target="_blank" rel="noopener noreferrer" href="https://wa.me/${esc(waDigits)}" aria-label="WhatsApp ${esc(center.whatsapp)}"><span class="pc-action-icon">${icon('whatsapp','whatsapp')}</span><span class="pc-action-copy"><small>WHATSAPP</small><strong>${esc(center.whatsapp)}</strong></span></a>`:''}${center.mapUrl?`<a class="pc-action map" target="_blank" rel="noopener noreferrer" href="${esc(center.mapUrl)}" aria-label="Ka eeg khariidadda"><span class="pc-action-icon">${icon('map')}</span><span class="pc-action-copy"><small>GOOBTA KHARIIDADDA</small><strong>Ka eeg khariidadda</strong></span>${icon('external','pc-external')}</a>`:''}</div></article>`;
    }).join(''):'<div class="empty-reader">Weli madbacad firfircoon laguma xidhin xaruntan.</div>';
    if(centers.length) wrap.insertAdjacentHTML('beforeend','<aside class="pc-choice-note" role="note" aria-label="Adeeg ikhtiyaari ah"><span class="pc-choice-note-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg></span><div class="pc-choice-note-copy"><h3>Adeeg ikhtiyaari ah</h3><p>Dugsigu wuxuu xogta madbacaddan kuugu soo bandhigayaa si ay kuugu fududaato oo keliya. La xidhiidhkeeda ama ka dalbashada adeeggeeda khasab kuguma aha; waxaad xor u tahay inaad madbacad kale doorato.</p></div></aside>');
    wrap.querySelectorAll('.pc-logo').forEach(image=>image.addEventListener('error',()=>{image.outerHTML=icon('printer')},{once:true}));
    $('#printerContactDialog').showModal();
  }
  function confirmPrinterContact(link) {
    const kind=link.classList.contains('whatsapp')?'whatsapp':'call';
    state.pendingContact={href:link.href,kind};
    $('#pcActionTitle').textContent=kind==='whatsapp'?'Xaqiiji WhatsApp':'Xaqiiji wicitaanka';
    $('#pcActionMessage').innerHTML='<strong>Fadlan Ogsoonow:</strong> Madbacaddani waa adeeg madax-bannaan. Dalab iyo lacag-bixin kasta waxaad si toos ah ula heshiinaysaa madbacadda.<span class="pc-disclaimer-boundary"><span class="pc-disclaimer-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg></span><span>Dugsigu wax lacag ah ma qaado, mana maamulo fulinta dalabka ama waqtiga adeegga ee aad madbacadda ku ballantaan.</span></span>';
    $('#pcActionContinue').textContent='Waan fahmay, sii wad';
    $('#pcActionIcon').innerHTML=link.querySelector('.pc-action-icon, span:first-child')?.innerHTML||'';
    $('#pcActionConfirm').showModal();
  }
  $('#pcActionConfirm').addEventListener('cancel',()=>{state.pendingContact=null});
  function toast(message) { const item=$('#toastP'); item.textContent=message; item.classList.add('show'); clearTimeout(item._t); item._t=setTimeout(()=>item.classList.remove('show'),2600); }
  function render() { renderFilters(); renderBooks(); syncFilterStateToUrl(); }
  render();
  async function shareReaderBook() {
    const url = new URL('/books', location.origin);
    url.searchParams.set('buug', state.readerBook.id);
    if (navigator.share) {
      try {
        await navigator.share({title: state.readerBook.title, url: url.href});
        return;
      } catch (error) {
        if (error.name === 'AbortError') return;
      }
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url.href);
      } else {
        const field = document.createElement('textarea');
        field.value = url.href;
        field.style.position = 'fixed';
        field.style.opacity = '0';
        shadow.append(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        if (!copied) throw new Error('Clipboard is unavailable');
      }
      toast('Link-ga buugga waa la koobiyey.');
    } catch (_) {
      toast('Link-ga lama koobiyeeyn. Isku day browser kale.');
    }
  }

  function setDownloadStatus(text, typewriter=false) {
    if (state.downloadStatusTimer) clearInterval(state.downloadStatusTimer);
    state.downloadStatusTimer=null;
    const status=$('#dStat');
    status.classList.remove('is-typing');
    status.setAttribute('aria-label',text);
    if (!typewriter) { status.textContent=text; return; }
    status.textContent=''; status.classList.add('is-typing');
    let position=0;
    state.downloadStatusTimer=setInterval(()=>{
      status.textContent=text.slice(0,++position);
      if(position>=text.length){clearInterval(state.downloadStatusTimer);state.downloadStatusTimer=null;status.classList.remove('is-typing');}
    },38);
  }
  function downloadUi(book, phase='downloading') {
    state.downloadBook=book;
    $('#dTitle').textContent=book.title; $('#dFile').textContent=book.filename || `${book.title}.pdf`;
    $('#dCover').innerHTML=coverHtml(book,'xs');
    $('#dSheet').classList.add('show'); $('#dBg').classList.add('show');
    $('#dSheet').classList.toggle('done',phase==='done');
    setDownloadStatus(phase==='done'?'Soo dejintu way dhammaatay':phase==='error'?'Soo dejintu way istaagtay':'La degista wey socotaa..........',phase==='downloading');
    $('#dSub').textContent=phase==='done'?'Faylka PDF-ga waxaa lagu kaydiyey qalabkaaga.':'Faylka PDF-ga ayaa la soo dejinayaa.';
    $('#dPct').innerHTML=phase==='done'?'✓':'0<small>%</small>';
    $('#dAct').innerHTML=phase==='done'?`<button type="button" class="btn pri" data-download-read="${book.id}">${sym('bookopen')} Fur buugga</button><button type="button" class="btn sec" data-download-close>Dhammaystir</button>`:`<button type="button" class="btn sec" data-download-cancel>Jooji</button>`;
  }
  async function startDownload(book) {
    downloadUi(book); const controller=new AbortController(); state.downloadController=controller;
    const start=performance.now(); let received=0; let total=Number(book.sizeBytes||0);
    try {
      const response=await fetch(book.downloadUrl,{signal:controller.signal});
      if(!response.ok) throw new Error(response.status===404?'Buuggan hadda lama heli karo.':'Faylka lama soo dejisan.');
      total=Number(response.headers.get('Content-Length'))||total;
      if(!response.body) throw new Error('Browser-ku ma taageerayo soo dejinta stream-ka.');
      const reader=response.body.getReader(); const chunks=[];
      while(true){const {done,value}=await reader.read();if(done)break;chunks.push(value);received+=value.byteLength;const elapsed=Math.max(.001,(performance.now()-start)/1000),rate=received/elapsed,percent=total?Math.min(99,received/total*100):0,eta=total&&rate?(total-received)/rate:null;
        $('#dPct').innerHTML=total?`${Math.floor(percent)}<small>%</small>`:'…';$('#dPrg').style.strokeDashoffset=String(477.5*(1-percent/100));$('#dBar').style.width=`${percent}%`;$('#dMb').textContent=`${(received/1048576).toFixed(1)} MB`;$('#dSp').textContent=`${(rate/1048576).toFixed(2)} MB/s`;$('#dEta').textContent=eta===null?'—':`${Math.ceil(eta)}s`;$('#dSub').textContent=total?`${(received/1048576).toFixed(1)} / ${(total/1048576).toFixed(1)} MB`:`${(received/1048576).toFixed(1)} MB la helay`;
      }
      setDownloadStatus('PDF-ga waa la hubinayaa');$('#dSub').textContent='Waxaan xaqiijinaynaa dhammaadka faylka.';
      const blob=new Blob(chunks,{type:'application/pdf'}),head=new Uint8Array(await blob.slice(0,8).arrayBuffer()),tail=new TextDecoder().decode(await blob.slice(Math.max(0,blob.size-4096)).arrayBuffer());
      if(new TextDecoder().decode(head).indexOf('%PDF-')!==0||!tail.includes('%%EOF'))throw new Error('PDF-ga la soo dejiyey ma dhammaystirna.');
      state.objectUrl=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=state.objectUrl;anchor.download=book.filename||`${book.title}.pdf`;anchor.click();setTimeout(()=>{if(state.objectUrl)URL.revokeObjectURL(state.objectUrl);state.objectUrl=null},60000);
      const counted=await fetch(`/books/api/${book.id}/download-complete`,{method:'POST'});if(!counted.ok)throw new Error('Faylka waa la helay, balse tirada download-ka lama cusboonaysiin.');$('#dPrg').style.strokeDashoffset='0';$('#dBar').style.width='100%';$('#dMb').textContent=`${(received/1048576).toFixed(1)} MB`;$('#dSp').textContent='La dhammeeyey';$('#dEta').textContent='0s';downloadUi(book,'done');
    } catch(error) { if(error.name==='AbortError'){ setDownloadStatus('Soo dejinta waa la joojiyey');$('#dSub').textContent='Wax fayl ah lama dhammeystirin.';$('#dPct').innerHTML='—';$('#dAct').innerHTML=`<button type="button" class="btn sec" data-download-close>Haye</button>`; } else { setDownloadStatus('Soo dejintu way fashilantay');$('#dSub').textContent=error.message;$('#dAct').innerHTML=`<button type="button" class="btn sec" data-download-close>Xir</button>`; } }
    finally { state.downloadController=null; }
  }
  function closeDownload(){state.downloadController?.abort();$('#dSheet').classList.remove('show');$('#dBg').classList.remove('show');}
  function closeReaderSheet(){
    const sheet=$('#rsheet'),bg=$('#rshBg');
    sheet.classList.remove('show');bg.classList.remove('show');
    sheet.setAttribute('aria-hidden','true');bg.setAttribute('aria-hidden','true');
    sheet.inert=true;bg.inert=true;
  }

  const storageKey=id=>`sultaan-books-${id}`;
  function saveReaderPosition(){if(state.readerBook&&state.pdf)localStorage.setItem(storageKey(state.readerBook.id),JSON.stringify({page:state.page,marks:state.marks[state.readerBook.id]||[]}));}
  function setReaderPage(number, save=true){
    if(!state.pdf)return;
    state.page=Math.max(1,Math.min(state.pdf.numPages,Number(number)||1));
    $('#rRange').value=String(state.page);$('#rNum').textContent=`Bogga ${state.page} / ${state.pdf.numPages}`;
    $('#rLeft').textContent=`${state.pdf.numPages-state.page} bog ayaa haray`;
    $('#rProg').style.width=`${state.page/state.pdf.numPages*100}%`;
    $('#rMark').classList.toggle('on',(state.marks[state.readerBook.id]||[]).includes(state.page));
    if(save)saveReaderPosition();
  }
  function scrollToPage(number){
    if(!state.pdf)return;
    const page=Math.max(1,Math.min(state.pdf.numPages,Number(number)||1));
    setReaderPage(page,false);
    $(`.pdfpage[data-page="${page}"]`)?.scrollIntoView({behavior:'smooth',block:'start'});
  }
  async function openReader(book, pageOverride) {
    state.pageObserver?.disconnect();state.pageObserver=null;state.searchRun++;
    state.readerBook=book; closeReaderSheet();$('#reader').classList.add('show');$('#reader').dataset.theme=state.theme;$('#reader').dataset.mode=state.mode;
    $('#rTitle').textContent=book.title;$('#rSub').textContent=`${book.level} · ${classText(book)} · PDF`;
    $('#rpages').innerHTML='<div class="empty-reader">PDF-ga waa la furayaa…</div>';$('#rNum').textContent='Bogga —';$('#rLeft').textContent='';$('#rProg').style.width='0%';
    let previous={};try{previous=JSON.parse(localStorage.getItem(storageKey(book.id))||'{}')}catch(_){}
    state.page=Number(pageOverride||previous.page||1);state.marks[book.id]=previous.marks||[];
    $('#rMark').classList.toggle('on',state.marks[book.id].includes(state.page));
    if(previous.page&&!pageOverride)toast(`Waxaad ku joogtay bogga ${previous.page}`);
    try {
      const pdfjs=await loadPdfjs();window.pdfjsModule=pdfjs;const pdf=await pdfjs.getDocument({url:book.pdfUrl,rangeChunkSize:65536}).promise;state.pdf=pdf;applyPageCount(book,pdf.numPages);
      $('#rRange').max=String(pdf.numPages);$('#rRange').value=String(Math.min(state.page,pdf.numPages));$('#rpages').innerHTML='';
      const firstPage=await pdf.getPage(1),firstViewport=firstPage.getViewport({scale:1}),ratio=`${firstViewport.width}/${firstViewport.height}`;
      for(let number=1;number<=pdf.numPages;number++){const node=document.createElement('article');node.className='pdfpage';node.dataset.page=number;node.style.aspectRatio=ratio;node.innerHTML='<canvas></canvas><div class="pdftext"></div>';$('#rpages').append(node);}
      const observer=new IntersectionObserver(entries=>{
        for(const entry of entries)if(entry.isIntersecting)renderPage(Number(entry.target.dataset.page)).catch(()=>{});
        const bounds=$('#rbody').getBoundingClientRect();
        const top=bounds.top+64,bottom=bounds.bottom-168;
        const current=$$('.pdfpage').map(node=>{const rect=node.getBoundingClientRect();return{node,visible:Math.max(0,Math.min(rect.bottom,bottom)-Math.max(rect.top,top))}}).sort((a,b)=>b.visible-a.visible)[0];
        if(!current?.visible)return;
        setReaderPage(Number(current.node.dataset.page));
      },{root:$('#rbody'),rootMargin:'650px 0px',threshold:[0,.1,.25,.5,.75,1]});
      state.pageObserver=observer;
      $$('.pdfpage').forEach(node=>observer.observe(node));$('#rbody').scrollTop=0;const target=$(`.pdfpage[data-page="${Math.min(state.page,pdf.numPages)}"]`);target?.scrollIntoView({block:'start'});
      fetch(`/books/api/${book.id}/read`,{method:'POST'}).catch(()=>{});
      if(location.search.includes(`buug=${book.id}`)===false){const url=new URL(location.href);url.searchParams.set('buug',book.id);history.replaceState({},'',url)}
    } catch(error) {state.pdf=null;$('#rpages').innerHTML=`<div class="empty-reader">PDF-ga lama furi karin. ${esc(error.message||'Hubi xiriirkaaga oo mar kale isku day.')}</div>`;}
  }
  async function renderPage(number) {
    if(!state.pdf)return;const root=$(`.pdfpage[data-page="${number}"]`);if(!root||root.dataset.rendered==='true')return;
    if(root._renderPromise)return root._renderPromise;
    root.dataset.rendered='loading';
    root._renderPromise=(async()=>{
      const page=await state.pdf.getPage(number),base=page.getViewport({scale:1}),width=Math.min(root.clientWidth||500,500),scale=width/base.width,viewport=page.getViewport({scale}),canvas=root.querySelector('canvas'),context=canvas.getContext('2d');
      canvas.width=Math.ceil(viewport.width*devicePixelRatio);canvas.height=Math.ceil(viewport.height*devicePixelRatio);canvas.style.width=`${viewport.width}px`;canvas.style.height=`${viewport.height}px`;await page.render({canvasContext:context,viewport,transform:devicePixelRatio===1?null:[devicePixelRatio,0,0,devicePixelRatio,0,0]}).promise;
      const text=await page.getTextContent();
      root._textItems=text.items||[];root._textContent=text;root.style.aspectRatio=`${viewport.width}/${viewport.height}`;
      const layer=root.querySelector('.pdftext');
      for(const item of text.items||[]){if(!item.str)continue;const transform=window.pdfjsModule.Util.transform(viewport.transform,item.transform),fontSize=Math.hypot(transform[2],transform[3]),angle=Math.atan2(transform[1],transform[0]),left=transform[4],top=transform[5]-fontSize*.82,span=document.createElement('span');span.textContent=item.str;span.dataset.text=item.str;span.style.left=`${left}px`;span.style.top=`${top}px`;span.style.fontSize=`${fontSize}px`;span.style.height=`${fontSize}px`;span.style.transform=`rotate(${angle}rad)`;const measure=context.measureText(item.str).width;if(measure>0&&item.width)span.style.transform+=` scaleX(${(item.width*scale)/measure})`;layer.append(span);}
      root.dataset.rendered='true';
    })();
    try{await root._renderPromise}catch(error){root.dataset.rendered='';throw error}finally{root._renderPromise=null}
  }
  async function searchPdf(query) {
    const run=++state.searchRun,pdf=state.pdf;query=String(query||'');
    if(!pdf)return;$$('.pdftext span').forEach(span=>span.classList.remove('hit','current-hit'));state.searchHits=[];if(!query.trim()){$('#rsCnt').textContent='';return}const wanted=query.trim().toLocaleLowerCase();$('#rsCnt').textContent='…';
    for(let n=1;n<=state.pdf.numPages;n++){
      if(run!==state.searchRun||pdf!==state.pdf)return;
      const page=await pdf.getPage(n),text=await page.getTextContent();
      if(!(text.items||[]).some(item=>item.str?.toLocaleLowerCase().includes(wanted)))continue;
      const root=$(`.pdfpage[data-page="${n}"]`);await renderPage(n);
      if(run!==state.searchRun||pdf!==state.pdf)return;
      for(const span of root.querySelectorAll('.pdftext span'))if(span.textContent.toLocaleLowerCase().includes(wanted)){span.classList.add('hit');state.searchHits.push({page:n,span})}
    }
    state.searchIndex=0;focusHit();$('#rsCnt').textContent=state.searchHits.length?`1/${state.searchHits.length}`:'0/0';
  }
  function focusHit(){state.searchHits.forEach((hit,index)=>hit.span.classList.toggle('current-hit',index===state.searchIndex));const hit=state.searchHits[state.searchIndex];if(hit){hit.span.scrollIntoView({block:'center'});setReaderPage(hit.page,false);$('#rsCnt').textContent=`${state.searchIndex+1}/${state.searchHits.length}`}}
  function openSheet(type){const sheet=$('#rsheet'),bg=$('#rshBg');sheet.setAttribute('aria-hidden','false');bg.setAttribute('aria-hidden','false');sheet.inert=false;bg.inert=false;sheet.classList.add('show');bg.classList.add('show');let title='',body='';
    if(type==='go'){title='U gudub bog';body=`<form class="pg-tools" id="goForm"><label for="goPage">Bogga aad rabto</label><input id="goPage" type="number" min="1" max="${state.pdf?.numPages||1}" value="${state.page}"><button type="button" id="goSubmit" class="btn pri">U gudub bogga</button></form>`}
    else if(type==='view'){title='Muuqaalka akhriska';body=`<div class="pg-tools"><b>Mawduuc</b><div class="theme-options">${[['light','Iftiin'],['sepia','Sepia'],['dark','Habeen']].map(([value,label])=>`<button type="button" data-theme="${value}" aria-pressed="${state.theme===value}">${label}</button>`).join('')}</div><label for="zoom">Cabbirka bogga <span id="zoomValue">${Math.round(state.zoom*100)}%</span></label><input id="zoom" type="range" min="80" max="180" step="10" value="${state.zoom*100}"><label for="layout">Habka bogagga</label><select id="layout"><option value="scroll" ${state.mode==='scroll'?'selected':''}>Hoos u rog</option><option value="page" ${state.mode==='page'?'selected':''}>Bog-bog</option></select></div>`}
    else if(type==='bookmarks'){title='Calaamadaha';const marks=state.marks[state.readerBook.id]||[];body=marks.length?marks.map(page=>`<button type="button" data-goto="${page}">Bogga ${page}</button>`).join(''):'<div class="empty-reader">Weli calaamad lama kaydin.</div>'}
    else {title='Tusmo iyo bogag';body=`<div class="rtabs"><button type="button" data-tab="outline" aria-pressed="true">Tusmada</button><button type="button" data-tab="thumbs" aria-pressed="false">Bogagga</button><button type="button" data-tab="bookmarks" aria-pressed="false">Calaamadaha</button></div><div id="sheetContent" class="sc">Tusmada PDF-ga waa la akhrinayaa…</div>`}
    sheet.innerHTML=`<div class="sh"><div class="hbar"></div><h4>${title}<button type="button" class="ib" data-sheet-close aria-label="Xir">${sym('x')}</button></h4></div><div class="sc">${body}</div>`;
    if(type==='toc')loadOutline();
  }
  async function loadOutline(){const target=$('#sheetContent');try{const outline=await state.pdf.getOutline();target.innerHTML=outline?.length?outline.map(item=>`<button type="button" data-dest="${esc(JSON.stringify(item.dest||[]))}">${esc(item.title)}</button>`).join(''):'<div class="empty-reader">Buuggan tusmo PDF ah kuma jirto.</div>'}catch(_){target.textContent='Tusmada PDF-ga lama heli karin.'}}
  async function loadThumbs(){const target=$('#sheetContent');target.innerHTML='<div class="thumbs"></div>';for(let n=1;n<=state.pdf.numPages;n++){const button=document.createElement('button');button.type='button';button.dataset.goto=n;button.innerHTML=`<canvas></canvas><span>${n}</span>`;target.querySelector('.thumbs').append(button);const page=await state.pdf.getPage(n),viewport=page.getViewport({scale:.28}),canvas=button.querySelector('canvas');canvas.width=viewport.width;canvas.height=viewport.height;await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise}}
  function closeReader(){saveReaderPosition();state.pageObserver?.disconnect();state.pageObserver=null;state.searchRun++;$('#reader').classList.remove('show');closeReaderSheet();state.pdf?.destroy();state.pdf=null;const url=new URL(location.href);url.searchParams.delete('buug');history.replaceState({},'',url)}
  shadow.addEventListener('click',event=>{
    const button=event.target.closest('button');
    const contactLink=event.target.closest('a.pc-action.call,a.pc-action.whatsapp,a.pc-contact-link.call,a.pc-contact-link.whatsapp');
    if(contactLink){event.preventDefault();confirmPrinterContact(contactLink);return}
    if(event.target.closest('[data-contact-cancel]')){$('#pcActionConfirm').close();state.pendingContact=null;return}
    if(event.target.closest('[data-contact-continue]')){const pending=state.pendingContact;state.pendingContact=null;$('#pcActionConfirm').close();if(pending?.kind==='whatsapp')window.open(pending.href,'_blank','noopener,noreferrer');else if(pending?.kind==='call')window.location.href=pending.href;return}
    if(event.target.id==='pcActionConfirm'){$('#pcActionConfirm').close();state.pendingContact=null;return}
    if(event.target.closest('[data-printer-contact]')){openPrinterContacts();return}
    if(event.target.closest('[data-printer-contact-close]')){$('#printerContactDialog').close();return}
    if(event.target.id==='printerContactDialog'){$('#printerContactDialog').close();return}
    const catalog=event.target.closest('[data-catalog]');if(catalog&&!catalog.disabled){state.catalogType=catalog.dataset.catalog;state.level=ALL;state.cls=ALL;state.subject=ALL;state.query='';$('#pq').value='';render();return}
    const level=event.target.closest('[data-level]');if(level){state.level=level.dataset.level;state.cls=ALL;render();return}
    const subject=event.target.closest('[data-subject]');if(subject){state.subject=subject.dataset.subject;render();return}
    const read=event.target.closest('[data-read]');if(read){const book=books.find(item=>item.id===Number(read.dataset.read));if(book)openReader(book);return}
    const download=event.target.closest('[data-download]');if(download){const book=books.find(item=>item.id===Number(download.dataset.download));if(book)startDownload(book);return}
    if(button?.id==='pvGrid'||button?.id==='pvList'){state.view=button.id==='pvGrid'?'grid':'list';renderBooks();syncFilterStateToUrl();return}
    if(button?.id==='dX'||event.target.closest('[data-download-close]')){closeDownload();return}
    if(event.target.closest('[data-download-cancel]')){state.downloadController?.abort();return}
    const downloadRead=event.target.closest('[data-download-read]');if(downloadRead){const book=books.find(item=>item.id===Number(downloadRead.dataset.downloadRead));closeDownload();if(book)openReader(book);return}
    if(event.target.id==='dBg'){closeDownload();return}
    if(button?.id==='rBack'){closeReader();return}
    if(button?.id==='rSearchBtn'){$('#rsearch').classList.toggle('show');$('#rsIn').focus();return}
    if(button?.id==='rsClose'){$('#rsearch').classList.remove('show');return}
    if(button?.id==='rMark'){const list=state.marks[state.readerBook.id]||[];state.marks[state.readerBook.id]=list.includes(state.page)?list.filter(n=>n!==state.page):[...list,state.page].sort((a,b)=>a-b);saveReaderPosition();$('#rMark').classList.toggle('on',state.marks[state.readerBook.id].includes(state.page));return}
    if(button?.id==='rPrev'||button?.id==='rNext'){scrollToPage(state.page+(button.id==='rPrev'?-1:1));return}
    if(button?.id==='goSubmit'){const page=Math.max(1,Math.min(state.pdf.numPages,Number($('#goPage').value)||1));scrollToPage(page);closeReaderSheet();return}
    if(event.target.closest('[data-ra="toc"]')){openSheet('toc');return}
    if(event.target.closest('[data-ra="go"]')){openSheet('go');return}
    if(event.target.closest('[data-ra="view"]')){openSheet('view');return}
    if(event.target.closest('[data-ra="share"]')){shareReaderBook();return}
    if(event.target.closest('[data-ra="dl"]')){closeReader();startDownload(state.readerBook);return}
    if(event.target.closest('[data-sheet-close]')||event.target.id==='rshBg'){closeReaderSheet();return}
    const theme=event.target.closest('.theme-options button[data-theme]');if(theme){state.theme=theme.dataset.theme;$('#reader').dataset.theme=state.theme;openSheet('view');return}
    const goto=event.target.closest('[data-goto]');if(goto){$(`.pdfpage[data-page="${goto.dataset.goto}"]`)?.scrollIntoView({behavior:'smooth'});closeReaderSheet();return}
    const dest=event.target.closest('[data-dest]');if(dest){try{const target=state.pdf.getDestination(JSON.parse(dest.dataset.dest));Promise.resolve(target).then(([ref])=>state.pdf.getPageIndex(ref).then(index=>{$(`.pdfpage[data-page="${index+1}"]`)?.scrollIntoView({behavior:'smooth'});closeReaderSheet()}))}catch(_){}return}
    const tab=event.target.closest('[data-tab]');if(tab){$$('[data-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button===tab)));if(tab.dataset.tab==='outline')loadOutline();else if(tab.dataset.tab==='thumbs')loadThumbs();else{const target=$('#sheetContent');const marks=state.marks[state.readerBook.id]||[];target.innerHTML=marks.length?marks.map(page=>`<button type="button" data-goto="${page}">Bogga ${page}</button>`).join(''):'<div class="empty-reader">Weli calaamad lama kaydin.</div>'}return}
  });
  shadow.addEventListener('input',event=>{if(event.target.id==='pq'){state.query=event.target.value;renderBooks();syncFilterStateToUrl()}if(event.target.id==='rsIn'){const input=event.target,query=input.value;clearTimeout(input._timer);input._timer=setTimeout(()=>searchPdf(query).catch(()=>{if($('#rsIn').value===query){$('#rsCnt').textContent='!';toast('Raadintu way fashilantay. Mar kale isku day.')}}),260)}if(event.target.id==='rRange'){scrollToPage(event.target.value)}if(event.target.id==='zoom'){state.zoom=Number(event.target.value)/100;$('#zoomValue').textContent=`${event.target.value}%`;$$('.pdfpage').forEach(node=>node.style.width=`min(calc(100% - 18px),${500*state.zoom}px)`)}});
  shadow.addEventListener('change',event=>{if(event.target.id==='pClass'){state.cls=event.target.value;render()}if(event.target.id==='pBranch'){state.branchId=event.target.value;state.catalogType='';state.level=ALL;state.cls=ALL;state.subject=ALL;state.query='';$('#pq').value='';render()}if(event.target.id==='rRange'){scrollToPage(event.target.value)}if(event.target.id==='layout'){state.mode=event.target.value;$('#reader').dataset.mode=state.mode}if(event.target.id==='goForm'){event.preventDefault();const page=Math.max(1,Math.min(state.pdf.numPages,Number($('#goPage').value)||1));scrollToPage(page);closeReaderSheet()}});
  shadow.addEventListener('submit',event=>{if(event.target.id==='goForm'){event.preventDefault();const page=Math.max(1,Math.min(state.pdf.numPages,Number($('#goPage').value)||1));scrollToPage(page);closeReaderSheet()}});
  shadow.addEventListener('keydown',event=>{if(event.key==='Escape'){if($('#rsheet').classList.contains('show'))closeReaderSheet();else if($('#reader').classList.contains('show'))closeReader();else closeDownload()}});
  shadow.addEventListener('click',event=>{const button=event.target.closest('#rsNext,#rsPrev');if(button){if(!state.searchHits.length)return;state.searchIndex=(state.searchIndex+(button.id==='rsNext'?1:-1)+state.searchHits.length)%state.searchHits.length;focusHit();$('#rsCnt').textContent=`${state.searchIndex+1}/${state.searchHits.length}`}});
  $('#rbody').addEventListener('scroll',()=>{const bar=$('#reader');if(bar._scrollTimer)clearTimeout(bar._scrollTimer);bar.classList.add('hide');bar._scrollTimer=setTimeout(()=>bar.classList.remove('hide'),800)});
  $('#rbody').addEventListener('click',event=>{if(state.mode!=='page'||event.target.closest('button,input,select,.pdftext'))return;const bounds=$('#rbody').getBoundingClientRect(),x=event.clientX-bounds.left;if(x<bounds.width*.24||x>bounds.width*.76){const page=Math.max(1,Math.min(state.pdf?.numPages||1,state.page+(x>bounds.width*.76?1:-1)));$(`.pdfpage[data-page="${page}"]`)?.scrollIntoView({behavior:'smooth'})}else $('#reader').classList.toggle('hide')});
  $('#pClass').addEventListener('change',event=>{state.cls=event.target.value;renderBooks()});
  $('#reader').addEventListener('touchstart',event=>{state.touchX=event.changedTouches[0].screenX});
  $('#reader').addEventListener('touchend',event=>{if(state.mode!=='page'||!state.touchX)return;const delta=event.changedTouches[0].screenX-state.touchX;if(Math.abs(delta)>65){const page=Math.max(1,Math.min(state.pdf?.numPages||1,state.page+(delta<0?1:-1)));$(`.pdfpage[data-page="${page}"]`)?.scrollIntoView({behavior:'smooth'})}state.touchX=0});
  const initial=new URL(location.href).searchParams.get('buug');if(initial){const book=books.find(item=>item.id===Number(initial));if(book)openReader(book)}
})();
