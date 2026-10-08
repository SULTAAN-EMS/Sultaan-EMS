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
  shadow.innerHTML = `<link rel="stylesheet" href="/static/css/books_reference.css"><style>
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
    @media(max-width:380px){.pseg button{font-size:12px}.bk{gap:9px;padding:9px}.bk-a button{font-size:12px}}
  </style>${icons}${template.content.firstElementChild.outerHTML}`;
  const $ = selector => shadow.querySelector(selector);
  const $$ = selector => [...shadow.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const ALL = library.ALL;
  const LEVELS = library.LEVELS;
  const SUBJECTS = library.SUBJECTS;
  const state = {level:ALL,cls:ALL,subject:ALL,query:'',view:'grid',readerBook:null,pdf:null,page:1,mode:'scroll',zoom:1,theme:'light',marks:{},downloadBook:null,downloadController:null,downloadStatusTimer:null,objectUrl:null,searchHits:[],searchIndex:0,pageObserver:null,searchRun:0};
  const sym = name => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const crest = () => config.logoUrl ? `<img class="crest crest-img" src="${esc(config.logoUrl)}" alt="">` : '<svg class="crest" viewBox="0 0 24 26" aria-hidden="true"><use href="#crest"/></svg>';
  const subjectSym = name => `<span class="subject-icon" style="--subject-icon:url('/static/icons/books-fa-free/${esc(name.replace(/^fa-/,''))}.svg')" aria-hidden="true"></span>`;
  const classText = book => book.class === ALL ? 'Heer dhan' : library.classLabel(book.class);
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
  function renderFilters() {
    $('#pLevel').innerHTML = [ALL,...Object.keys(LEVELS)].map(level => `<button type="button" data-level="${esc(level)}" aria-pressed="${state.level===level}" style="--c:${LEVELS[level]?.c || '#0E7C66'}"><i></i>${level===ALL?'Dhammaan':esc(level)}</button>`).join('');
    const classes = state.level === ALL ? library.allClasses : (LEVELS[state.level]?.classes || []);
    $('#pClass').innerHTML = `<option value="${ALL}">Dhammaan fasallada</option>${classes.map(name=>`<option value="${esc(name)}">${esc(library.classLabel(name))}</option>`).join('')}`;
    $('#pClass').value = state.cls;
    $('#pSubj').innerHTML = [ALL,...SUBJECTS].map(subject=>`<button type="button" class="chip" data-subject="${esc(subject)}" aria-pressed="${state.subject===subject}">${subjectSym((library.SUBJ[subject]||library.SUBJ[ALL]).i)}${subject==='Dhammaan'?'Dhammaan maaddooyinka':esc(subject)}</button>`).join('');
  }
  function filteredBooks() {
    const query = state.query.trim().toLocaleLowerCase();
    return books.filter(book => book.visible && (state.level===ALL || book.level===state.level) && (state.cls===ALL || book.class===state.cls || book.class===ALL) && (state.subject===ALL || book.subject===state.subject || book.subject===ALL) && (!query || `${book.title} ${book.subject} ${book.class} ${classText(book)} ${book.description}`.toLocaleLowerCase().includes(query)));
  }
  function renderBooks() {
    const list = filteredBooks();
    $('#pCount').innerHTML = `<b>${list.length}</b> ${list.length===1?'buug':'buug'}`;
    $('#pvGrid').setAttribute('aria-pressed',String(state.view==='grid'));
    $('#pvList').setAttribute('aria-pressed',String(state.view==='list'));
    $('#pList').className = `plist ${state.view}`;
    $('#pList').innerHTML = list.length ? list.map(book => state.view==='grid'
      ? `<article class="gk"><button type="button" data-read="${book.id}" aria-label="Akhri ${esc(book.title)}">${coverHtml(book,'md')}</button><div class="m"><span class="lv" style="--c:${LEVELS[book.level]?.c}"><i></i>${esc(book.level)}</span><span class="cls">${esc(classText(book))}</span></div><h3>${esc(book.title)}</h3><div class="s">${subjectSym((library.SUBJ[book.subject]||library.SUBJ[ALL]).i)}${book.subject===ALL?'Dhammaan maaddooyinka':esc(book.subject)}</div><div class="gk-a"><button class="rd" type="button" data-read="${book.id}">${sym('bookopen')} Akhri</button><button class="dl" type="button" data-download="${book.id}">${sym('download')} Soo Degso</button></div></article>`
      : `<article class="bk"><button type="button" data-read="${book.id}" aria-label="Akhri ${esc(book.title)}" style="display:block;flex:none;align-self:flex-start">${coverHtml(book,'md')}</button><div class="bk-b"><div class="m"><span class="lv" style="--c:${LEVELS[book.level]?.c}"><i></i>${esc(book.level)}</span><span class="cls">${esc(classText(book))}</span></div><h3>${esc(book.title)}</h3><div class="s">${subjectSym((library.SUBJ[book.subject]||library.SUBJ[ALL]).i)}${book.subject===ALL?'Dhammaan maaddooyinka':esc(book.subject)} · ${book.pages || '—'} bog</div><p>${esc(book.description)}</p><div class="bk-a"><button class="rd" type="button" data-read="${book.id}">${sym('eye')} Akhri</button><button class="dl" type="button" data-download="${book.id}">${sym('download')} Soo Degso</button></div></div></article>`).join('')
      : `<div class="empty"><div class="big">${sym('bookopen')}</div><b>Buug lama helin</b><p>Isku day erey kale ama nadiifi shaandhaynta.</p><button class="btn pri" type="button" data-clear>Nadiifi shaandhaynta</button></div>`;
  }
  function toast(message) { const item=$('#toastP'); item.textContent=message; item.classList.add('show'); clearTimeout(item._t); item._t=setTimeout(()=>item.classList.remove('show'),2600); }
  function render() { renderFilters(); renderBooks(); }
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
    const level=event.target.closest('[data-level]');if(level){state.level=level.dataset.level;state.cls=ALL;render();return}
    const subject=event.target.closest('[data-subject]');if(subject){state.subject=subject.dataset.subject;render();return}
    const read=event.target.closest('[data-read]');if(read){const book=books.find(item=>item.id===Number(read.dataset.read));if(book)openReader(book);return}
    const download=event.target.closest('[data-download]');if(download){const book=books.find(item=>item.id===Number(download.dataset.download));if(book)startDownload(book);return}
    if(event.target.closest('[data-clear]')||event.target.id==='pClear'){state.level=ALL;state.cls=ALL;state.subject=ALL;state.query='';$('#pq').value='';render();return}
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
  shadow.addEventListener('change',event=>{if(event.target.id==='pClass'){state.cls=event.target.value;renderBooks()}if(event.target.id==='rRange'){scrollToPage(event.target.value)}if(event.target.id==='layout'){state.mode=event.target.value;$('#reader').dataset.mode=state.mode}if(event.target.id==='goForm'){event.preventDefault();const page=Math.max(1,Math.min(state.pdf.numPages,Number($('#goPage').value)||1));scrollToPage(page);closeReaderSheet()}});
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
