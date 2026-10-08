window.BookCoverLibrary = (() => {
const ALL = 'Dhammaan';
const LEVELS = {
  Hoose: {c:'#F59E0B', classes:['Fasal 1','Fasal 2','Fasal 3','Fasal 4']},
  Dhexe: {c:'#3B82F6', classes:['Fasal 5','Fasal 6','Fasal 7','Fasal 8']},
  Sare:  {c:'#8B5CF6', classes:['Form 1','Form 2','Form 3','Form 4']}
};
const allClasses = Object.values(LEVELS).flatMap(l => l.classes);
const CLASS_LABELS = {
  'Fasal 1':'1aad','Fasal 2':'2aad','Fasal 3':'3aad','Fasal 4':'4aad',
  'Fasal 5':'5aad','Fasal 6':'6aad','Fasal 7':'7aad','Fasal 8':'8aad',
  'Form 1':'Form One (9aad)','Form 2':'Form Two (10aad)',
  'Form 3':'Form Three (11aad)','Form 4':'Form Four (12aad)'
};
const classLabel = value => CLASS_LABELS[value] || value;

/* ---------- cover art (one illustration per subject) ---------- */
const hexPts=(cx,cy,r)=>[0,1,2,3,4,5].map(k=>{const a=(60*k-30)*Math.PI/180;return (cx+r*Math.cos(a)).toFixed(1)+','+(cy+r*Math.sin(a)).toFixed(1)}).join(' ');
const starPts=(cx,cy,R,r)=>Array.from({length:10},(_,k)=>{const a=(36*k-90)*Math.PI/180,rr=k%2?r:R;return (cx+rr*Math.cos(a)).toFixed(1)+','+(cy+rr*Math.sin(a)).toFixed(1)}).join(' ');
const ART = {
  Math:()=>{let g='';for(let i=0;i<=100;i+=10)g+=`<path d="M${i} 0V133"/>`;for(let j=0;j<=133;j+=10)g+=`<path d="M0 ${j}H100"/>`;
    return `<g stroke="#fff" stroke-width=".35" opacity=".17">${g}</g><g fill="#fff" opacity=".17" font-family="Georgia,serif"><text x="56" y="58" font-size="36">∑</text><text x="8" y="104" font-size="32">π</text><text x="58" y="124" font-size="22">√x</text><text x="12" y="52" font-size="16">x²</text></g>`},
  Physics:()=>`<g fill="none" stroke="#fff" stroke-width=".8" opacity=".32"><ellipse cx="70" cy="92" rx="48" ry="15"/><ellipse cx="70" cy="92" rx="48" ry="15" transform="rotate(60 70 92)"/><ellipse cx="70" cy="92" rx="48" ry="15" transform="rotate(120 70 92)"/></g><g fill="#fff" opacity=".5"><circle cx="70" cy="92" r="4.5"/><circle cx="112" cy="84" r="2.4"/><circle cx="46" cy="64" r="2.4"/><circle cx="52" cy="118" r="2.4"/></g>`,
  Chemistry:()=>`<g fill="none" stroke="#fff" stroke-width=".9" opacity=".3"><polygon points="${hexPts(66,92,20)}"/><polygon points="${hexPts(83.3,122,20)}"/><polygon points="${hexPts(48.7,122,20)}"/><polygon points="${hexPts(83.3,62,20)}"/></g><g fill="#fff" opacity=".28"><circle cx="20" cy="60" r="5"/><circle cx="30" cy="46" r="3"/><circle cx="14" cy="40" r="2"/><circle cx="90" cy="30" r="4"/></g>`,
  Biology:()=>{let p1='',p2='',r='';for(let y=0;y<=133;y+=3){const d=20*Math.sin(y/11),x1=70+d,x2=70-d;p1+=(y?'L':'M')+x1.toFixed(1)+' '+y;p2+=(y?'L':'M')+x2.toFixed(1)+' '+y;if(y%9===0)r+=`<path d="M${x1.toFixed(1)} ${y}L${x2.toFixed(1)} ${y}"/>`}
    return `<g fill="none" stroke="#fff" stroke-width="1.1" opacity=".32"><path d="${p1}"/><path d="${p2}"/><g stroke-width=".7" opacity=".8">${r}</g></g>`},
  Juqraafi:()=>`<g fill="none" stroke="#fff" stroke-width=".8" opacity=".32"><circle cx="68" cy="94" r="42"/><ellipse cx="68" cy="94" rx="17" ry="42"/><ellipse cx="68" cy="94" rx="32" ry="42"/><path d="M26 94h84M32 72h72M32 116h72"/></g><path d="M8 30q22-14 44 0t44 0" fill="none" stroke="#fff" stroke-width=".8" stroke-dasharray="2 2.5" opacity=".4"/>`,
  Taariikh:()=>`<g fill="none" stroke="#fff" stroke-width=".9" opacity=".3"><path d="M2 133V98a11 11 0 0 1 22 0V133"/><path d="M24 133V98a11 11 0 0 1 22 0V133"/><path d="M46 133V98a11 11 0 0 1 22 0V133"/><path d="M68 133V98a11 11 0 0 1 22 0V133"/><path d="M90 133V98a11 11 0 0 1 22 0V133"/></g><circle cx="78" cy="46" r="15" fill="#fff" opacity=".14"/><g stroke="#fff" stroke-width=".8" opacity=".3"><path d="M78 22v6M78 64v6M54 46h6M96 46h6M61 29l4 4M91 59l4 4M95 29l-4 4M65 59l-4 4"/></g>`,
  Technology:()=>`<g stroke="#fff" fill="none" stroke-width=".9" opacity=".34"><path d="M0 98H30L40 88H72L82 98H100"/><path d="M0 114H20L30 124H62"/><path d="M62 133V112L72 102"/><path d="M100 76H74L64 66H38"/><path d="M0 70H18L28 60"/></g><g fill="#fff" opacity=".5"><circle cx="40" cy="88" r="2.4"/><circle cx="72" cy="88" r="2.4"/><circle cx="62" cy="124" r="2.4"/><circle cx="38" cy="66" r="2.4"/><circle cx="28" cy="60" r="2.4"/></g>`,
  'Luuqadda Carabiga':()=>`<g fill="none" stroke="#fff" stroke-width=".8" opacity=".3"><rect x="44" y="70" width="44" height="44" transform="rotate(0 66 92)"/><rect x="44" y="70" width="44" height="44" transform="rotate(45 66 92)"/><circle cx="66" cy="92" r="31"/><circle cx="66" cy="92" r="12"/></g><text x="8" y="62" font-size="58" fill="#fff" opacity=".16" font-family="'Noto Naskh Arabic','Amiri','Segoe UI',serif">ع</text>`,
  'Af Soomaali':()=>`<polygon points="${starPts(68,94,30,12)}" fill="#fff" opacity=".24"/><g fill="#fff" opacity=".17" font-family="Georgia,serif" font-weight="700"><text x="8" y="50" font-size="22">Aa</text><text x="52" y="40" font-size="14">Bb</text><text x="10" y="128" font-size="18">Cc</text><text x="74" y="136" font-size="14">Dd</text></g>`,
  English:()=>`<g fill="#fff" opacity=".17" font-family="Georgia,serif"><text x="6" y="74" font-size="56">Aa</text><text x="52" y="132" font-size="78">”</text></g><g stroke="#fff" opacity=".28" stroke-width=".8"><path d="M8 92h56M8 100h44M8 108h50"/></g>`,
  'English Films':()=>{let h='';for(let y=4;y<133;y+=11)h+=`<rect x="73" y="${y}" width="5" height="6" rx="1"/><rect x="88" y="${y}" width="5" height="6" rx="1"/>`;
    return `<g fill="#fff" opacity=".24">${h}</g><g fill="none" stroke="#fff" stroke-width=".9" opacity=".3"><rect x="70" y="-4" width="26" height="141"/><circle cx="36" cy="96" r="22"/></g><path d="M30 84l18 12-18 12z" fill="#fff" opacity=".4"/>`},
  'Tarbiyadda Islaamka':()=>`<path d="M72 60A28 28 0 1 0 72 120A21 21 0 1 1 72 60Z" fill="#fff" opacity=".22"/><g fill="none" stroke="#fff" stroke-width=".8" opacity=".34"><rect x="80" y="64" width="14" height="14"/><rect x="80" y="64" width="14" height="14" transform="rotate(45 87 71)"/><path d="M0 133V112a10 10 0 0 1 20 0V133M20 133V112a10 10 0 0 1 20 0V133"/></g>`,
  Business:()=>`<g fill="#fff" opacity=".17"><rect x="12" y="104" width="14" height="29"/><rect x="32" y="88" width="14" height="45"/><rect x="52" y="70" width="14" height="63"/><rect x="72" y="48" width="14" height="85"/></g><polyline points="12,98 34,80 54,86 88,40" fill="none" stroke="#fff" stroke-width="1.4" opacity=".5"/><circle cx="88" cy="40" r="3.4" fill="#fff" opacity=".7"/>`
};
ART[ALL] = () => `<g fill="none" stroke="#fff" stroke-width=".8" opacity=".26"><circle cx="70" cy="92" r="16"/><circle cx="70" cy="92" r="30"/><circle cx="70" cy="92" r="44"/><circle cx="70" cy="92" r="58"/></g>`;

const SUBJ = {
  'Math':                {i:'fa-calculator', a:'#2B2A9E', b:'#6E7BFF', n:'Math'},
  'Physics':             {i:'fa-atom', a:'#B93C08', b:'#FF9A4D', n:'Physics'},
  'Chemistry':           {i:'fa-flask', a:'#8E1450', b:'#F25DA6', n:'Chemistry'},
  'Biology':             {i:'fa-dna', a:'#2F6B12', b:'#8BD43A', n:'Biology'},
  'Juqraafi':            {i:'fa-globe', a:'#0B6B63', b:'#35D3C0', n:'Juqraafi'},
  'Taariikh':            {i:'fa-landmark', a:'#6B3A0C', b:'#D9A13C', n:'Taariikh'},
  'Technology':          {i:'fa-microchip', a:'#0A1F3C', b:'#0E8BB0', n:'Technology'},
  'Luuqadda Carabiga':   {i:'fa-language', a:'#701616', b:'#E04646', n:'Luuqadda Carabiga'},
  'Af Soomaali':         {i:'fa-language', a:'#1749B8', b:'#5AA2F0', n:'Af Soomaali'},
  'English':             {i:'fa-language', a:'#52198A', b:'#B26BFA', n:'English'},
  'English Films':       {i:'fa-film', a:'#0E1424', b:'#B3202A', n:'English Films'},
  'Tarbiyadda Islaamka': {i:'fa-mosque', a:'#05473B', b:'#14A57C', n:'Tarbiyadda Islaamka'},
  'Business':            {i:'fa-chart-line', a:'#12395E', b:'#2F7FC4', n:'Business'},
  [ALL]:                 {i:'fa-book-open', a:'#0B3B36', b:'#17A38A', n:'Buug Guud'}
};
const SUBJECTS = Object.keys(SUBJ).filter(s => s !== ALL);
for (const k of Object.keys(SUBJ)) SUBJ[k].art = ART[k];
return {ALL, LEVELS, allClasses, CLASS_LABELS, classLabel, ART, SUBJ, SUBJECTS};
})();
