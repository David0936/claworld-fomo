let csrf = '', lastEvents = '', lastRecords = '', loadedSettings = false, currentEvents = [], activeFilter = 'all';
const $ = id => document.getElementById(id);
const labels = {waiting:'等待首轮核验',running:'正在核验',complete:'本轮核验完成',partial:'本轮部分完成',blocked:'行情核验受阻'};
const when = t => t ? new Date(t * 1000).toLocaleString('zh-CN') : '尚无记录';
function toast(text) { $('toast').textContent = text; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 6500); }
async function update() {
  try {
    const r = await fetch('/api/state'); if (!r.ok) throw Error(); const s = await r.json(); csrf = s.csrf;
    $('user').textContent = s.user ? (s.user.provider || '飞书') + ' 已登录 · ' + s.user.name : '本机工作台';
    const stale = s.scan.updated_at && Date.now()/1000 - s.scan.updated_at > 1200;
    $('scanStatus').textContent = (stale ? '数据已过期 · ' : '') + (labels[s.scan.status] || s.scan.status);
    $('scanDetail').textContent = s.scan.detail;
    $('scanTime').textContent = '最近状态更新：' + when(s.scan.updated_at);
    const names={feishu:'飞书',telegram:'Telegram'};
    $('deliveryStatus').textContent = !s.configured ? '等待配置' : s.channels.map(c=>names[c]).join(' + ');
    $('deliveryDetail').textContent = s.delivery?.detail || '等待发送首条消息';
    $('pending').textContent = s.counts.pending || 0; $('sent').textContent = s.counts.sent || 0;
    $('hasWebhook').textContent = s.config.webhook ? '（已保存）' : '';
    $('hasTelegram').textContent = s.config.telegram_token ? '（已保存）' : '';
    $('savedChat').textContent = s.telegram_chat_id ? '当前接收：' + s.telegram_chat_id : '尚未绑定接收会话';
    for(const c of ['feishu','telegram']){
      const ready=s.channels.includes(c), enabled=s.enabled[c];
      $(c+'Channel').textContent=!enabled?'已暂停推送':!ready?'等待配置完整连接信息':s.channel_delivery[c]?.detail || '已配置，可发送测试消息';
      if(!loadedSettings) $(c+'Enabled').checked=enabled;
    }
    loadedSettings=true;
    $('oauthStatus').textContent = s.oauth_ready ? '应用凭证已保存，可以登录' : '先保存应用凭证，再点击登录';
    $('refreshState').textContent = s.refresh ? when(s.refresh.at) + ' · ' + s.refresh.detail : '';
    const version=s.version || {};
    $('versionStatus').textContent=version.status==='available'
      ? `发现新版本 ${version.latest}（当前 ${version.current}）`
      : version.status==='error' ? '暂时无法检查更新'
      : `当前版本 ${version.current || '—'}${version.latest ? ' · 已是最新版' : ''}`;
    $('hitCount').textContent=s.events.filter(e=>e.payload.kind==='hit').length;
    $('navCount').textContent=s.events.filter(e=>e.payload.kind==='hit').length;
    $('configDot').style.background=s.configured?'var(--green)':'var(--amber)';
    $('scanDot').style.background=stale||['blocked','partial'].includes(s.scan.status)?'var(--amber)':'var(--green)';
    const ek=JSON.stringify(s.events);
    currentEvents=s.events;
    if(ek!==lastEvents){lastEvents=ek;renderEvents();}
    const rk=JSON.stringify(s.records);
    if(rk!==lastRecords){lastRecords=rk;$('records').replaceChildren();for(const r of s.records){
      const d=document.createElement('details');d.className='record';
      const title=document.createElement('summary');title.textContent=r.name.replace(/_监测记录\.md$/, '').replace(/\.md$/, '');
      const text=document.createElement('pre');text.textContent=r.text;
      const meta=document.createElement('div');meta.className='record-meta';meta.textContent=r.name+' · 本地监测档案';
      d.append(title,meta,text);$('records').append(d);
    }}
    $('connection').textContent = '已同步 ' + new Date().toLocaleTimeString('zh-CN');
  } catch { $('connection').textContent='本机服务连接失败，请重新启动监控台'; }
}
async function post(path, data, button) {
  if(button) button.disabled=true;
  try { const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)}); const d=await r.json(); if(!r.ok) throw Error(d.error || '请求失败'); toast(d.detail); await update(); return d; }
  catch(e){toast(e.message || '请求失败');return false;} finally{if(button)button.disabled=false;}
}
$('settings').addEventListener('submit',async e=>{e.preventDefault(); const data=Object.fromEntries(new FormData(e.target)); for(const k of ['clear_webhook','clear_telegram','feishu_enabled','telegram_enabled']) data[k]=Boolean(data[k]); if(await post('/api/config',data,e.submitter)){e.target.reset();loadedSettings=false;await update();$('telegramBinding').hidden=true;}});
$('testFeishu').addEventListener('click',e=>post('/api/test',{channel:'feishu'},e.target));
$('testTelegram').addEventListener('click',e=>post('/api/test',{channel:'telegram'},e.target));
$('telegramStart').addEventListener('click',async e=>{const d=await post('/api/telegram/start',{},e.target);if(d){$('telegramLink').href=d.url;$('telegramBinding').hidden=false;}});
$('telegramFinish').addEventListener('click',async e=>{if(await post('/api/telegram/finish',{},e.target))$('telegramBinding').hidden=true;});
$('refresh').addEventListener('click',e=>post('/api/refresh',{},e.target));
$('checkUpdate').addEventListener('click',async e=>{
  const result=await post('/api/update/check',{},e.target);
  if(result) toast(result.status==='available' ? `发现新版本 ${result.latest}` : '当前已是最新版');
});
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function renderEvents(){
  if(window.renderPortfolio)window.renderPortfolio();
  const query=$('search').value.trim().toLowerCase();
  const rows=currentEvents.filter(e=>{
    const p=e.payload;
    if(query&&!JSON.stringify(p).toLowerCase().includes(query))return false;
    if(activeFilter==='strong')return p.kind==='hit'&&p.fomo_ratio>20;
    if(activeFilter==='watch')return p.kind==='hit'&&p.fomo_ratio<=20;
    if(activeFilter==='pending')return e.status!=='sent';
    return true;
  });
  const expanded=new Set([...$('events').querySelectorAll('details[open]')].map(d=>d.dataset.event));
  $('events').replaceChildren();$('resultCount').textContent=rows.length+' 条';
  if(!rows.length){$('events').append(el('div','empty',query||activeFilter!=='all'?'没有符合当前筛选的记录':'等待新的核验结果。通过筛选的信号将在这里出现。'));return;}
  for(const e of rows){
    const p=e.payload,hit=p.kind==='hit',strong=hit&&p.fomo_ratio>20;
    const kindNames={risk:'风险提醒',strategy:'策略提醒',milestone:'阶段提醒',outage:'核验异常',recovery:'服务恢复',stopped:'停止跟踪',test:'连接测试'};
    const title=hit?(p.symbol||p.name||p.text.split('/')[0].replace(/^.*?新命中\s*/, '').trim().slice(0,28)):(kindNames[p.kind]||'监测事件');
    const card=el('article','event');const top=el('div','event-top');const heading=el('div','token-heading');
    const identity=el('div');identity.append(el('h3','token-name',title),el('div','token-chain',p.chain||'系统通知'));
    heading.append(el('div','token-avatar',hit?title.slice(0,2).toUpperCase():'·'),identity);
    top.append(heading,el('span','signal-badge'+(strong?' strong':''),hit?(strong?'强信号':'观察信号'):(kindNames[p.kind]||'通知')));card.append(top);
    if(hit){const grid=el('div','event-grid');for(const [label,value,cls] of [
      ['首次观测市值',Number.isFinite(p.market_cap)?'$'+(p.market_cap/1000).toLocaleString('en-US',{maximumFractionDigits:1})+'K':'未核验',''],
      ['创建时间',Number.isFinite(p.age_days)?p.age_days.toFixed(1)+' 天':'未核验',''],
      ['Fomo 持仓占比',Number.isFinite(p.fomo_ratio)?'≥'+p.fomo_ratio+'%':'未核验','ratio']]){
        const cell=el('div');cell.append(el('label','',label),el('b',cls,value));grid.append(cell);
      }card.append(grid);
    }
    const bottom=el('div','event-bottom');
    if(p.ca){const copy=el('button','ca-button',p.ca.slice(0,8)+'…'+p.ca.slice(-6)+'  复制');copy.type='button';copy.title='复制完整 CA：'+p.ca;copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(p.ca);toast('完整 CA 已复制');}catch{toast('复制失败，请在核验详情中选择完整 CA');}});bottom.append(copy);}
    bottom.append(el('span',e.status==='sent'?'sent-status':'pending-status',e.status==='sent'?'已完成推送':'等待推送'),el('time','',when(e.created)));card.append(bottom);
    const detail=el('details');detail.dataset.event=e.id;detail.open=expanded.has(e.id);detail.append(el('summary','',hit?'查看完整核验与策略详情':'查看事件详情'),el('pre','',p.text));
    const links=el('div','event-links');const seen=new Set();
    for(const raw of (p.text.match(/https:\/\/[^\s<>]+/g)||[])){try{const url=new URL(raw);if(!['gmgn.ai','fomo.family','wind.jokkimon.club'].includes(url.hostname)||seen.has(url.hostname))continue;seen.add(url.hostname);const a=el('a','',({'gmgn.ai':'GMGN','fomo.family':'Fomo','wind.jokkimon.club':'Windvane'})[url.hostname]+' ↗');a.href=url.href;a.target='_blank';a.rel='noreferrer';links.append(a);}catch{}}
    if(links.children.length)detail.append(links);card.append(detail);
    if(e.deliveries?.length)card.append(el('p','receipt',e.deliveries.map(d=>(d.channel==='feishu'?'飞书':'Telegram')+'：'+(d.status==='sent'?'已发送':d.error?'失败待重试':'待发送')).join(' · ')));
    if(e.error)card.append(el('p','error',e.error));$('events').append(card);
  }
}
function showView(view){if(!['overview','archive','connect'].includes(view))view='overview';for(const n of document.querySelectorAll('.view'))n.hidden=n.id!=='view-'+view;for(const n of document.querySelectorAll('.nav-item'))n.classList.toggle('active',n.dataset.view===view);window.scrollTo({top:0,behavior:'instant'});$('viewName').textContent={overview:'信号总览',archive:'监测档案',connect:'消息连接'}[view];}
for(const b of document.querySelectorAll('[data-view]'))b.addEventListener('click',()=>{location.hash=b.dataset.view;showView(b.dataset.view);});
window.addEventListener('hashchange',()=>showView(location.hash.slice(1)));showView(location.hash.slice(1));
for(const b of document.querySelectorAll('[data-filter]'))b.addEventListener('click',()=>{activeFilter=b.dataset.filter;for(const n of document.querySelectorAll('[data-filter]'))n.classList.toggle('selected',n===b);renderEvents();});
$('search').addEventListener('input',renderEvents);
try{$('density').value=localStorage.getItem('fomo-density')||'standard';}catch{}
document.body.dataset.density=$('density').value;
$('density').addEventListener('change',()=>{document.body.dataset.density=$('density').value;try{localStorage.setItem('fomo-density',$('density').value);}catch{}});
update();setInterval(update,5000);
