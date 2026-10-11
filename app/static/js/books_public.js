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
    .pscroll{height:auto;min-height:100dvh;overflow:visible}
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
  const state = {level:ALL,cls:ALL,subject:ALL,query:'',catalogType:'',branchId:'',view:'list',readerBook:null,pdf:null,page:1,mode:'scroll',zoom:1,theme:'light',marks:{},downloadBook:null,downloadController:null,downloadStatusTimer:null,objectUrl:null,searchHits:[],searchIndex:0,pageObserver:null,searchRun:0};
  const sym = name => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const crest = () => config.logoUrl ? `<img class="crest crest-img" src="${esc(config.logoUrl)}" alt="">` : '<svg class="crest" viewBox="0 0 24 26" aria-hidden="true"><use href="#crest"/></svg>';
  const subjectSym = name => `<span class="subject-icon" style="--subject-icon:url('/static/icons/books-fa-free/${esc(name.replace(/^fa-/,''))}.svg')" aria-hidden="true"></span>`;
  const classText = book => book.class === ALL ? 'Heer dhan' : library.classLabel(book.class);
  const subjectText = book => book.bookKind==='general' ? GENERAL : book.subject===ALL ? 'Dhammaan maaddooyinka' : book.subject;
  const coverHtml = (book, size='md') => {
    if (book.coverUrl && book.hasCover) return `<div class="cover ${size} custom" style="background-image:url('${esc(book.coverUrl)}')" role="img" aria-label="Cover-ka ${esc(book.title)}"></div>`;
    const subject = library.SUBJ[book.scope === 'subject' ? book.subject : ALL] || library.SUBJ[ALL];
    const tag = book.class !== ALL ? library.classLabel(book.class) : `${book.level} · Heer dhan`;
    return `<div class="cover ${size}" style="--a:${subject.a};--b:${subject.b}"><svg class="art" viewBox="0 0 100 133" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${subject.art()}</svg><span class="spine"></span><div class="cv"><div class="cv-top">${crest()}<span>TAYSIIR<em>INTERNATIONAL SCHOOLS</em></span></div><i class="cv-gold"></i><div class="cv-mid"><div class="emb">${subjectSym(subject.i)}</div></div><div class="cv-bot"><small>${esc(subject.n)}</small><b>${esc(book.title)}</b><span class="tag">${esc(tag)}</span></div></div></div>`;
  };
  // Cover presence is deliberately separate from the private filename.
  books.forEach(book => { book.hasCover = Boolean(book.hasCover); });
  $('#schoolName').textContent = config.schoolName || 'Taysiir International Schools';
  $('#publicFooter').textContent = `${config.schoolName || 'Taysiir International Schools'} · Maktabadda Buugaagta`;
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
    $('#pBranch').innerHTML=`<option value="">Dooro xarunta dugsiga</option>${(config.branches||[]).map(branch=>`<option value="${branch.id}">${esc(branch.name)}${branch.region?` · ${esc(branch.region)}`:''}</option>`).join('')}`;
    $('#pBranch').value=state.branchId;
    const branchItems = branchBooks();
    const availableTypes = new Set(branchItems.map(book => book.catalogType));
    if (!availableTypes.has(state.catalogType)) state.catalogType = '';
    $('#pCatalog').querySelectorAll('[data-catalog]').forEach(button=>{
      const available = availableTypes.has(button.dataset.catalog);
      button.disabled = !state.branchId || !available;
      button.setAttribute('aria-pressed',String(button.dataset.catalog===state.catalogType));
      button.querySelector('[data-catalog-hint]').textContent = !state.branchId
        ? 'Dooro xarun marka hore'
        : available
          ? button.dataset.catalog==='national'?'Manhaj guud ama xaruntan loo xulay':'Buugaagta xaruntan'
          : 'Buug kuma jiro xaruntan';
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
    $('#pCount').innerHTML = `<b>${list.length}</b> ${list.length===1?'buug':'buug'}`;
    $('#pvGrid').setAttribute('aria-pressed',String(state.view==='grid'));
    $('#pvList').setAttribute('aria-pressed',String(state.view==='list'));
    $('#pList').className = `plist ${state.view}`;
    $('#pList').innerHTML = list.length ? list.map(book => state.view==='grid'
      ? `<article class="gk"><button type="button" data-read="${book.id}" aria-label="Akhri ${esc(book.title)}">${coverHtml(book,'md')}</button><div class="m"><span class="lv" style="--c:${LEVELS[book.level]?.c}"><i></i>${esc(book.level)}</span><span class="cls">${esc(classText(book))}</span></div><h3>${esc(book.title)}</h3><div class="s">${subjectSym((library.SUBJ[book.subject]||library.SUBJ[ALL]).i)}${esc(subjectText(book))}</div><div class="gk-a"><button class="rd" type="button" data-read="${book.id}">${sym('bookopen')} Akhri</button><button class="dl" type="button" data-download="${book.id}">${sym('download')} Soo Degso</button></div></article>`
      : `<article class="bk"><button type="button" data-read="${book.id}" aria-label="Akhri ${esc(book.title)}" style="display:block;flex:none;align-self:flex-start">${coverHtml(book,'md')}</button><div class="bk-b"><div class="m"><span class="lv" style="--c:${LEVELS[book.level]?.c}"><i></i>${esc(book.level)}</span><span class="cls">${esc(classText(book))}</span></div><h3>${esc(book.title)}</h3><div class="s">${subjectSym((library.SUBJ[book.subject]||library.SUBJ[ALL]).i)}${esc(subjectText(book))} · ${book.pages || '—'} bog</div><p>${esc(book.description)}</p><div class="bk-a"><button class="rd" type="button" data-read="${book.id}">${sym('eye')} Akhri</button><button class="dl" type="button" data-download="${book.id}">${sym('download')} Soo Degso</button></div></div></article>`).join('')
      : `<div class="empty"><div class="big">${sym('bookopen')}</div><b>${!state.branchId?'Marka hore dooro xarunta dugsiga':!branchBooks().length?'Xaruntan buug laguma darin':!state.catalogType?'Dooro nooca buugga':'Buug lama helin'}</b><p>${!state.branchId?'Dooro xarunta si loo soo saaro noocyada buugga, heerarka iyo fasallada ku habboon.':!branchBooks().length?'Ma jiro buug dadweyne ah oo xaruntan loo xulay.':'Isku day erey kale ama nadiifi shaandhaynta.'}</p><button class="btn pri" type="button" data-clear>Nadiifi shaandhaynta</button></div>`;
  }
  function renderPrintCenters() {
    const wrap=$('#pCenters');
    const branch=(config.branches||[]).find(item=>String(item.id)===String(state.branchId));
    if(state.catalogType!=='local'||!branch){wrap.hidden=true;wrap.innerHTML='';return}
    wrap.hidden=false;
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
    wrap.querySelectorAll('.pc-logo').forEach(image=>image.addEventListener('error',()=>{image.outerHTML=icon('printer')},{once:true}));
  }
  function toast(message) { const item=$('#toastP'); item.textContent=message; item.classList.add('show'); clearTimeout(item._t); item._t=setTimeout(()=>item.classList.remove('show'),2600); }
  function render() { renderFilters(); renderBooks(); renderPrintCenters(); }
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
    if(!window.pdfjsLibPromise)window.pdfjsLibPromise=import('/static/vendor/pdfjs/pdf.min.mjs').then(module=>{module.GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/pdf.worker.min.mjs';return module});
    try {
      const pdfjs=await window.pdfjsLibPromise;window.pdfjsModule=pdfjs;const pdf=await pdfjs.getDocument({url:book.pdfUrl,rangeChunkSize:65536}).promise;state.pdf=pdf;
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
    const catalog=event.target.closest('[data-catalog]');if(catalog&&!catalog.disabled){state.catalogType=catalog.dataset.catalog;state.level=ALL;state.cls=ALL;state.subject=ALL;state.query='';$('#pq').value='';render();return}
    const level=event.target.closest('[data-level]');if(level){state.level=level.dataset.level;state.cls=ALL;render();return}
    const subject=event.target.closest('[data-subject]');if(subject){state.subject=subject.dataset.subject;render();return}
    const read=event.target.closest('[data-read]');if(read){const book=books.find(item=>item.id===Number(read.dataset.read));if(book)openReader(book);return}
    const download=event.target.closest('[data-download]');if(download){const book=books.find(item=>item.id===Number(download.dataset.download));if(book)startDownload(book);return}
    if(event.target.closest('[data-clear]')||event.target.id==='pClear'){state.level=ALL;state.cls=ALL;state.subject=ALL;state.catalogType='';state.branchId='';state.query='';$('#pq').value='';render();return}
    if(button?.id==='pvGrid'||button?.id==='pvList'){state.view=button.id==='pvGrid'?'grid':'list';renderBooks();return}
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
  shadow.addEventListener('input',event=>{if(event.target.id==='pq'){state.query=event.target.value;renderBooks()}if(event.target.id==='rsIn'){const input=event.target,query=input.value;clearTimeout(input._timer);input._timer=setTimeout(()=>searchPdf(query).catch(()=>{if($('#rsIn').value===query){$('#rsCnt').textContent='!';toast('Raadintu way fashilantay. Mar kale isku day.')}}),260)}if(event.target.id==='rRange'){scrollToPage(event.target.value)}if(event.target.id==='zoom'){state.zoom=Number(event.target.value)/100;$('#zoomValue').textContent=`${event.target.value}%`;$$('.pdfpage').forEach(node=>node.style.width=`min(calc(100% - 18px),${500*state.zoom}px)`)}});
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
