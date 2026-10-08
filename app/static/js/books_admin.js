(() => {
  'use strict';

  const mount = document.getElementById('booksAdminMount');
  if (!mount || !window.BookCoverLibrary) return;
  const library = window.BookCoverLibrary;
  const ALL = library.ALL;
  const LEVELS = library.LEVELS;
  const SUBJECTS = library.SUBJECTS;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const data = id => JSON.parse(document.getElementById(id).textContent || 'null');
  const booksConfig = data('booksConfig');
  let books = data('booksBootstrap') || [];
  const icons = document.getElementById('booksIcons').innerHTML;
  const shadow = mount.attachShadow({mode: 'open'});
  shadow.innerHTML = `<link rel="stylesheet" href="/static/css/books_reference.css"><style>
    :host{display:block;min-width:0}.main{position:relative;min-height:calc(100vh - 44px);background:var(--bg)}
    .ov{position:fixed;inset:0;padding:16px;overflow-y:auto}.modal{width:min(780px,calc(100vw - 32px));max-height:min(700px,calc(100vh - 32px));max-height:min(700px,calc(100dvh - 32px))}.mb{min-height:0}.toast{position:fixed;bottom:24px}
    .empty .ic{stroke:currentColor}.cover.custom{background-position:center;background-size:cover}
    .stat{width:100%;text-align:left;color:var(--text);font:inherit;cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s}
    .stat:hover{border-color:var(--brand);transform:translateY(-1px)}.stat:focus-visible{outline:3px solid var(--brand);outline-offset:2px}
    .stat.active{border-color:var(--brand);box-shadow:0 0 0 2px var(--brandSoft)}.stat .stat-info{display:flex;flex-direction:column;gap:3px}.stat .stat-info>b{color:var(--text)}
    .books-saving{opacity:.68;pointer-events:none}.fchip .ic{flex:none}
    @media(max-width:900px){.head{flex-direction:column}.headR{width:100%;flex-wrap:wrap}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.gridv{grid-template-columns:repeat(2,minmax(0,1fr))}.mb{grid-template-columns:minmax(0,1fr)}.rside{display:grid;grid-template-columns:140px 1fr}.rside .cover{grid-row:span 2}.three{grid-template-columns:1fr}.modal{width:min(780px,calc(100vw - 28px));max-height:calc(100vh - 28px)}}
    @media(max-width:560px){.content{padding:16px 12px 22px}.headR{align-items:stretch}.linkpill{justify-content:center;flex:1}.stats{gap:8px}.stat{padding:10px;gap:8px}.stat .tile{width:38px;height:38px}.stat b{font-size:22px}.tools{padding:10px}.field{max-width:100%}.field select{max-width:130px}.gridv{grid-template-columns:1fr 1fr;gap:8px;padding:9px}.gcard{padding:9px 8px}.modal{width:calc(100vw - 18px);max-height:calc(100vh - 18px)}.mb{padding:14px}.mf{padding:12px;flex-wrap:wrap}.up{grid-template-columns:1fr}.scanbox{width:110px}.uptot b{font-size:34px}}
  </style>${icons}${mount.innerHTML}`;
  const $ = selector => shadow.querySelector(selector);
  const $$ = selector => [...shadow.querySelectorAll(selector)];
  const icon = (name, extra = '') => `<svg class="ic ${extra}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const subjectIcon = name => `<span class="subject-icon" style="--subject-icon:url('/static/icons/books-fa-free/${esc(name.replace(/^fa-/,''))}.svg')" aria-hidden="true"></span>`;
  const A = {q:'', level:ALL, subject:ALL, status:'all', view:'grid', sortDownloads:false};
  let editing = null;
  let draft = null;
  let xhr = null;
  let coverObjectUrl = null;
  const scopeLabel = scope => ({level:'Heer dhan', class:'Fasal gaar ah', subject:'Maaddo gaar ah'})[scope] || 'Heer dhan';
  const clsLabel = value => value === ALL ? 'Dhammaan fasallada' : library.classLabel(value);
  const formatSize = bytes => `${(Number(bytes || 0) / 1048576).toFixed(1)} MB`;
  const coverSubject = book => book.scope === 'subject' ? book.subject : ALL;
  const crest = () => booksConfig.logoUrl ? `<img class="crest crest-img" src="${esc(booksConfig.logoUrl)}" alt="">` : '<svg class="crest" viewBox="0 0 24 26" aria-hidden="true"><use href="#crest"/></svg>';
  const previewBook = () => ({title:draft.title || 'Cinwaanka buugga', subj:coverSubject(draft), previewUrl:draft.previewUrl || '', coverUrl:draft.coverUrl || '', hasCover:Boolean(draft.hasCover), level:draft.level, cls:draft.scope === 'level' ? ALL : draft.class});
  function cover(book, size = 'md') {
    if (book.previewUrl || (book.hasCover && book.coverUrl)) return `<div class="cover ${size} custom" style="background-image:url('${esc(book.previewUrl || book.coverUrl)}')" role="img" aria-label="Cover-ka ${esc(book.title)}"></div>`;
    const subject = library.SUBJ[book.subj || book.subject] || library.SUBJ[ALL];
    const tag = book.cls && book.cls !== ALL ? library.classLabel(book.cls) : `${book.level || 'Heer'} · Heer dhan`;
    return `<div class="cover ${size}" style="--a:${subject.a};--b:${subject.b}"><svg class="art" viewBox="0 0 100 133" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${subject.art()}</svg><span class="spine"></span><div class="cv"><div class="cv-top">${crest()}<span>TAYSIIR<em>INTERNATIONAL SCHOOLS</em></span></div><i class="cv-gold"></i><div class="cv-mid"><div class="emb">${subjectIcon(subject.i)}</div></div><div class="cv-bot"><small>${esc(subject.n)}</small><b>${esc(book.title || 'Cinwaanka buugga')}</b><span class="tag">${esc(tag)}</span></div></div></div>`;
  }
  const levelBadge = level => `<span class="lv" style="--c:${LEVELS[level]?.c || '#0E7C66'}"><i></i>${esc(level)}</span>`;
  const subjectChip = subject => `<span class="sj">${subjectIcon((library.SUBJ[subject] || library.SUBJ[ALL]).i)}${subject === ALL ? 'Dhammaan maaddooyinka' : esc(subject)}</span>`;
  function toast(message, error = false) {
    const node = $('#toastA');
    node.innerHTML = `${icon(error ? 'x' : 'check')}${esc(message)}`;
    node.classList.toggle('is-error', error);
    node.classList.add('show');
    clearTimeout(node._timeout);
    node._timeout = setTimeout(() => node.classList.remove('show'), 2800);
  }
  function fillFilters() {
    $('#al').innerHTML = `<option value="${ALL}">Dhammaan heerarka</option>${Object.keys(LEVELS).map(level => `<option>${level}</option>`).join('')}`;
    $('#as').innerHTML = `<option value="${ALL}">Dhammaan maaddooyinka</option>${SUBJECTS.map(subject => `<option>${esc(subject)}</option>`).join('')}`;
  }
  function render() {
    const total = books.length;
    const visible = books.filter(book => book.visible).length;
    const downloads = books.reduce((sum, book) => sum + Number(book.downloadCount || 0), 0);
    $('#stats').innerHTML = [
      ['book','Wadarta buugaagta',total,'#0E7C66','all',A.status === 'all' && !A.sortDownloads],
      ['eye','Muuqda dadweynaha',visible,'#2F80ED','visible',A.status === 'on' && !A.sortDownloads],
      ['eyeoff','Qarsan',total-visible,'#8A97A0','hidden',A.status === 'off' && !A.sortDownloads],
      ['download','Wadarta soo dejinta',downloads.toLocaleString('en-US'),'#E39A00','downloads',A.sortDownloads],
    ].map(([sym,label,count,color,filter,active]) => `<button type="button" class="stat ${active?'active':''}" data-stat="${filter}" aria-pressed="${active}" aria-label="${label}: ${count}"><span class="tile" style="--c:${color}">${icon(sym)}</span><span class="stat-info"><b>${count}</b><span>${label}</span></span></button>`).join('');
    const query = A.q.trim().toLocaleLowerCase();
    const filtered = books.filter(book => (A.level === ALL || book.level === A.level) && (A.subject === ALL || book.subject === A.subject) && (A.status === 'all' || (A.status === 'on') === book.visible) && (!query || `${book.title} ${book.subject} ${book.class} ${clsLabel(book.class)} ${book.description}`.toLocaleLowerCase().includes(query)));
    if (A.sortDownloads) filtered.sort((a,b) => Number(b.downloadCount || 0) - Number(a.downloadCount || 0));
    if (!filtered.length) {
      $('#aList').innerHTML = `<div class="empty">${icon('book')}<b>Buug lama helin</b>Beddel shaandhaynta, ama ku dar buug cusub.</div>`;
      return;
    }
    const toggle = book => `<button type="button" class="sw ${book.visible?'on':''}" role="switch" aria-checked="${book.visible}" aria-label="${book.visible?'Qari':'Muuji'} ${esc(book.title)}" data-act="toggle" data-id="${book.id}"><i></i></button><em class="st ${book.visible?'on':'off'}">${book.visible?'Muuqda':'Qarsan'}</em>`;
    const actions = book => `<div class="acts"><button type="button" class="ib" data-act="edit" data-id="${book.id}" title="Wax ka beddel" aria-label="Wax ka beddel ${esc(book.title)}">${icon('pencil')}</button><button type="button" class="ib del" data-act="del" data-id="${book.id}" title="Tirtir" aria-label="Tirtir ${esc(book.title)}">${icon('trash')}</button></div>`;
    if (A.view === 'table') {
      $('#aList').innerHTML = `<div style="overflow:auto"><table><thead><tr><th style="width:34%">Buugga</th><th>Heer</th><th>Fasal</th><th>Maaddo</th><th>Xaalad</th><th></th></tr></thead><tbody>${filtered.map(book => `<tr class="${book.visible?'':'hid'}"><td><div class="bcell">${cover(book,'sm')}<div><b>${esc(book.title)}</b><span>${scopeLabel(book.scope)} · ${book.pages || '—'} bog · ${formatSize(book.sizeBytes)}</span></div></div></td><td>${levelBadge(book.level)}</td><td>${clsLabel(book.class)}</td><td>${subjectChip(book.subject)}</td><td><span style="white-space:nowrap">${toggle(book)}</span></td><td>${actions(book)}</td></tr>`).join('')}</tbody></table></div>`;
    } else {
      $('#aList').innerHTML = `<div class="gridv">${filtered.map(book => `<article class="gcard ${book.visible?'':'hid'}">${cover(book,'lg')}<div><h4>${esc(book.title)}</h4><span style="color:var(--muted);font-size:12.5px">${clsLabel(book.class)} · ${book.subject === ALL ? 'Dhammaan' : esc(book.subject)}</span></div><div>${levelBadge(book.level)}</div><div class="row"><span>${toggle(book)}</span>${actions(book)}</div></article>`).join('')}</div>`;
    }
  }
  function classOptions(level, selected) {
    return (LEVELS[level]?.classes || []).map(name => `<option value="${esc(name)}" ${name===selected?'selected':''}>${esc(library.classLabel(name))}</option>`).join('');
  }
  function selectedScope(scope) {
    const options = [['level','layers','Heer dhan'],['class','class','Fasal gaar ah'],['subject','book','Maaddo gaar ah']];
    return options.map(([value, sym, title]) => `<button type="button" data-scope="${value}" aria-pressed="${scope===value}">${icon(sym)}<b>${title}</b></button>`).join('');
  }
  function openEditor(id = null) {
    editing = id;
    const existing = books.find(book => book.id === id);
    draft = existing ? {...existing, previewUrl:'', fileName:existing.filename, fileChanged:false, pdfFile:null, coverFile:null} : {title:'',level:'Sare',class:'Form 2',subject:'Math',scope:'subject',description:'',visible:true,coverUrl:'',filename:'',fileChanged:false,pdfFile:null,coverFile:null};
    $('#modal').className = 'modal';
    drawEditor();
    $('#ov').classList.add('show');
    setTimeout(() => $('#f-title')?.focus(), 40);
  }
  function drawEditor() {
    const book = draft;
    $('#modal').innerHTML = `<div class="mh"><h3 id="booksModalTitle">${editing?'Wax ka beddel buugga':'Buug cusub'}</h3><button class="ib" data-m="close" aria-label="Xir">${icon('x')}</button></div>
      <div class="mb"><div>
        <div class="fg"><label class="fl" for="f-title">Cinwaanka buugga</label><input class="fi" id="f-title" maxlength="200" value="${esc(book.title)}" placeholder="tusaale: Math Form 2 — Made Easy"><div class="errt" id="e-title">Geli cinwaanka buugga.</div></div>
        <div class="fg"><span class="fl">Buuggan yaa loogu talagalay?</span><div class="scope">${selectedScope(book.scope)}</div></div>
        <div class="fg three"><div><label class="fl" for="f-level">Heer dugsi</label><select class="fi" id="f-level">${Object.keys(LEVELS).map(level => `<option ${level===book.level?'selected':''}>${level}</option>`).join('')}</select></div><div><label class="fl" for="f-class">Fasal</label><select class="fi" id="f-class" ${book.scope==='level'?'disabled':''}>${classOptions(book.level,book.class)}</select></div><div><label class="fl" for="f-subj">Maaddo</label><select class="fi" id="f-subj" ${book.scope!=='subject'?'disabled':''}>${SUBJECTS.map(subject => `<option ${subject===book.subject?'selected':''}>${esc(subject)}</option>`).join('')}</select></div></div>
        <div class="fg"><label class="fl" for="f-desc">Sharaxaad</label><textarea class="fi" id="f-desc" maxlength="3000" placeholder="Muxuu Buuggu ka kooban yahay, yuunu u wanaagsan yahay!?">${esc(book.description)}</textarea></div>
      </div><div class="rside"><div id="prevCover" style="align-self:center">${cover(previewBook(),'xl')}</div><div class="autoc">${icon('sparkle')}${book.coverUrl?'Cover-kaaga ayaa la isticmaalayaa':'Cover default ah (auto)'}</div><input type="file" id="f-img" accept="image/jpeg,image/png,image/webp" hidden><button type="button" class="drop" data-m="img">${icon('image')}${book.coverUrl?'Beddel cover-ka':'Soo geli cover u gaar ah (ikhtiyaari)'}</button>${book.coverUrl?`<button type="button" class="ib remove-cover-btn" data-m="remove-cover" aria-label="Ka saar cover-ka">${icon('trash')}<span>Ka saar cover-ka</span></button>`:''}<input type="file" id="f-file" accept="application/pdf,.pdf" hidden><button type="button" class="drop" data-m="file">${icon('upload')}${book.fileName?`<b>${esc(book.fileName)}</b>`:'Soo geli faylka PDF-ka'}</button><div class="visrow"><div><b>Muuji dadweynaha</b><span>${book.visible?'Waa muuqdaa':'Waa qarsan yahay'}</span></div><button type="button" class="sw ${book.visible?'on':''}" role="switch" aria-checked="${book.visible}" aria-label="Muuji dadweynaha" data-m="vis"><i></i></button></div></div></div>
      <div class="mf"><button class="btn sec" data-m="close">Jooji</button><button class="btn pri" data-m="save">${icon('upload')}${editing?'Kaydi isbeddelka':'Soo geli buugga'}</button></div>`;
  }
  function readForm() {
    const title = $('#f-title');
    if (!title) return;
    draft.title = title.value;
    draft.level = $('#f-level').value;
    draft.class = $('#f-class').value;
    draft.subject = $('#f-subj').value;
    draft.description = $('#f-desc').value;
  }
  function refreshPreview() {
    readForm();
    $('#prevCover').innerHTML = cover(previewBook(),'xl');
  }
  function formData() {
    readForm();
    const form = new FormData();
    for (const key of ['title','level','class','subject','scope','description']) form.append(key, draft[key] || '');
    form.append('visible', String(Boolean(draft.visible)));
    if (draft.fileChanged && draft.pdfFile) form.append('pdf_file', draft.pdfFile);
    if (draft.coverFile) form.append('cover_file', draft.coverFile);
    if (draft.removeCover) form.append('remove_cover','true');
    return form;
  }
  function showProgress() {
    const steps = ['Faylka PDF-ka waa la soo gelinayaa','Bogagga waa la hubinayaa','Cover-ka waa la diyaarinayaa','Buugga waa la kaydinayaa'];
    $('#modal').className = 'modal mid';
    $('#modal').innerHTML = `<div class="mh"><h3 id="booksModalTitle">${editing?'Buugga waa la cusboonaysiinayaa':'Buugga waa la soo gelinayaa'}</h3></div><div class="up"><div><div class="scanbox">${cover(previewBook(),'lg')}</div><div class="fchip">${icon('file')}<div><b>${esc(draft.pdfFile?.name || draft.fileName || 'PDF-ga hadda jira')}</b><span>${draft.pdfFile?formatSize(draft.pdfFile.size):formatSize(draft.sizeBytes)} · PDF</span></div></div></div><div><div class="uptot"><b id="upPct">0%</b><span id="upSub">Waa la bilaabayaa…</span></div><div class="upbar"><i id="upBar"></i></div><ul class="steps" id="upSteps">${steps.map((step,index)=>`<li data-i="${index}"><div class="sdot"><div class="spin"></div></div><div><b>${step}</b><span>La sugayaa</span></div></li>`).join('')}</ul><div id="upError" class="errt"></div></div></div><div class="mf"><button class="btn sec" data-m="upcancel">Jooji</button></div>`;
  }
  function setProgress(percent, message) {
    const pct = Math.max(0,Math.min(100,Math.round(percent)));
    $('#upPct').textContent = `${pct}%`;
    $('#upBar').style.width = `${pct}%`;
    $('#upSub').textContent = message;
    const current = pct < 68 ? 0 : pct < 78 ? 1 : pct < 90 ? 2 : 3;
    $$('#upSteps li').forEach((step,index) => {
      step.classList.toggle('done', index < current || pct === 100);
      step.classList.toggle('act', index === current && pct < 100);
      step.querySelector('span').textContent = index < current || pct === 100 ? 'Waa la dhammeeyey' : index === current ? message : 'La sugayaa';
      if (index < current || pct === 100) step.querySelector('.sdot').innerHTML = icon('check');
    });
  }
  function uploadSave() {
    readForm();
    if (!draft.title.trim()) { $('#f-title').classList.add('err'); $('#e-title').style.display='block'; $('#f-title').focus(); return; }
    if (!editing && !draft.pdfFile) { toast('Soo geli faylka PDF-ka buugga.',true); return; }
    const body = formData();
    const upload = !editing || draft.fileChanged;
    if (upload) showProgress();
    else { $('#modal').classList.add('books-saving'); }
    xhr = new XMLHttpRequest();
    xhr.open(editing?'PUT':'POST', editing?`/admin/books/api/${editing}`:'/admin/books/api');
    xhr.setRequestHeader('X-CSRFToken', booksConfig.csrf);
    if (upload) xhr.upload.onprogress = event => {
      if (event.lengthComputable) setProgress(Math.min(72, event.loaded / event.total * 72), 'Faylka PDF-ka waa la soo gelinayaa');
    };
    xhr.onerror = () => uploadFailure('Shabakadda ayaa go’day intii faylka la soo gelinayey.');
    xhr.onabort = () => { xhr = null; toast('Soo gelinta waa la joojiyey.',true); $('#ov').classList.remove('show'); };
    xhr.onload = () => {
      let result;
      try { result = JSON.parse(xhr.responseText); } catch (_) { result = {success:false,message:'Jawaabta server-ku sax ma aha.'}; }
      if (xhr.status < 200 || xhr.status >= 300 || !result.success) return uploadFailure(result.message || 'Buugga lama kaydin.');
      if (upload) setProgress(100,'Buugga waa la kaydiyey.');
      const index = books.findIndex(book => book.id === result.book.id);
      if (index >= 0) books[index] = result.book; else books.unshift(result.book);
      xhr = null;
      setTimeout(() => { $('#ov').classList.remove('show'); render(); toast(editing?'Isbeddelka waa la kaydiyey.':'Buugga waa la soo geliyey.'); }, upload ? 550 : 0);
    };
    xhr.send(body);
  }
  function uploadFailure(message) {
    xhr = null;
    const error = $('#upError');
    if (error) { error.textContent = message; error.style.display='block'; $('#upSub').textContent='Soo gelintu way fashilantay'; }
    else { $('#modal').classList.remove('books-saving'); toast(message,true); }
  }
  function confirmDelete(id) {
    const book = books.find(item => item.id === id);
    if (!book) return;
    $('#modal').className = 'modal small';
    $('#modal').innerHTML = `<div class="dlg"><div class="big">${icon('trash')}</div><h3 id="booksModalTitle">Tirtir buugga?</h3><p>“${esc(book.title)}” iyo faylkiisa si joogto ah ayaa loo tirtirayaa.</p></div><div class="mf"><button class="btn sec" data-m="close">Jooji</button><button class="btn dng" data-m="delete" data-id="${id}">${icon('trash')} Tirtir buugga</button></div>`;
    $('#ov').classList.add('show');
  }
  async function requestJson(url, options = {}) {
    const response = await fetch(url,{...options,headers:{'X-CSRFToken':booksConfig.csrf,...(options.headers || {})}});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.success === false) throw new Error(payload.message || 'Codsiga lama fulin.');
    return payload;
  }

  fillFilters();
  render();
  shadow.addEventListener('click', async event => {
    const stat = event.target.closest('[data-stat]');
    if (stat) {
      if (stat.dataset.stat === 'all') { A.status='all'; A.sortDownloads=false; }
      else if (stat.dataset.stat === 'visible') { A.status='on'; A.sortDownloads=false; }
      else if (stat.dataset.stat === 'hidden') { A.status='off'; A.sortDownloads=false; }
      else A.sortDownloads = !A.sortDownloads;
      $('#ast').value = A.status;
      render();
      return;
    }
    const action = event.target.closest('[data-act]');
    if (action) {
      const id = Number(action.dataset.id);
      try {
        if (action.dataset.act === 'new') openEditor();
        else if (action.dataset.act === 'edit') openEditor(id);
        else if (action.dataset.act === 'del') confirmDelete(id);
        else if (action.dataset.act === 'copy') {
          await navigator.clipboard.writeText(new URL(booksConfig.publicUrl,location.origin).href);
          toast('Link-ga dadweynaha waa la koobiyey.');
        } else if (action.dataset.act === 'toggle') {
          const result = await requestJson(`/admin/books/api/${id}/visibility`,{method:'POST'});
          const book = books.find(item => item.id === id);
          book.visible = result.visible;
          render();
          toast(result.visible?'Buugga dadweynaha waa loo muujiyey.':'Buugga waa la qariyey.');
        }
      } catch (error) { toast(error.message,true); }
      return;
    }
    const mode = event.target.closest('[data-m]');
    if (mode) {
      const key = mode.dataset.m;
      if (key === 'close') { if (xhr) xhr.abort(); else $('#ov').classList.remove('show'); }
      else if (key === 'save') uploadSave();
      else if (key === 'upcancel') { if (xhr) xhr.abort(); else $('#ov').classList.remove('show'); }
      else if (key === 'delete') {
        try { await requestJson(`/admin/books/api/${mode.dataset.id}`,{method:'DELETE'}); books = books.filter(book => book.id !== Number(mode.dataset.id)); $('#ov').classList.remove('show'); render(); toast('Buugga iyo faylashiisa waa la tirtiray.'); }
        catch (error) { toast(error.message,true); }
      } else if (key === 'vis') { readForm(); draft.visible = !draft.visible; drawEditor(); }
      else if (key === 'img') $('#f-img').click();
      else if (key === 'file') $('#f-file').click();
      else if (key === 'remove-cover') { draft.removeCover = true; draft.coverUrl=''; draft.previewUrl=''; drawEditor(); }
      return;
    }
    const scope = event.target.closest('[data-scope]');
    if (scope) { readForm(); draft.scope = scope.dataset.scope; if (draft.scope === 'level') { draft.class=ALL; draft.subject=ALL; } else if (draft.class===ALL) { draft.class=LEVELS[draft.level].classes[0]; if (draft.scope==='class') draft.subject=ALL; } else if (draft.scope==='class') draft.subject=ALL; else if (draft.subject===ALL) draft.subject=SUBJECTS[0]; drawEditor(); return; }
    const viewButton = event.target.closest('#vTable, #vGrid');
    if (viewButton) {
      A.view = viewButton.id === 'vTable' ? 'table' : 'grid';
      $('#vTable').setAttribute('aria-pressed',String(A.view==='table')); $('#vGrid').setAttribute('aria-pressed',String(A.view==='grid')); render();
      return;
    }
    if (event.target.id === 'ov' && !xhr) $('#ov').classList.remove('show');
  });
  shadow.addEventListener('input', event => {
    if (event.target.id === 'aq') { A.q=event.target.value; render(); }
    if (event.target.id === 'f-title') { refreshPreview(); event.target.classList.remove('err'); $('#e-title').style.display='none'; }
  });
  shadow.addEventListener('change', event => {
    if (event.target.id === 'al') { A.level=event.target.value; render(); }
    if (event.target.id === 'as') { A.subject=event.target.value; render(); }
    if (event.target.id === 'ast') { A.status=event.target.value; render(); }
    if (event.target.id === 'f-level') { readForm(); draft.class=LEVELS[draft.level].classes[0]; drawEditor(); }
    if (event.target.id === 'f-class' || event.target.id === 'f-subj') refreshPreview();
    if (event.target.id === 'f-file' && event.target.files[0]) { readForm(); draft.pdfFile=event.target.files[0]; draft.fileName=draft.pdfFile.name; draft.fileChanged=true; drawEditor(); }
    if (event.target.id === 'f-img' && event.target.files[0]) { readForm(); draft.coverFile=event.target.files[0]; if (coverObjectUrl) URL.revokeObjectURL(coverObjectUrl); coverObjectUrl=URL.createObjectURL(draft.coverFile); draft.previewUrl=coverObjectUrl; drawEditor(); }
  });
  shadow.addEventListener('keydown', event => { if (event.key==='Escape' && !xhr) $('#ov').classList.remove('show'); });
})();
