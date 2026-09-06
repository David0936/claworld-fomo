let csrf = '', lastEvents = '', lastRecords = '', loadedSettings = false;
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
    const ek = JSON.stringify(s.events);
    if (ek !== lastEvents) { lastEvents = ek; $('events').replaceChildren();
      if (!s.events.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = '等待新的核验事件。已有历史档案见下方。\n不会将历史记录自动当作新命中推送。'; $('events').append(empty); }
      for (const e of s.events) { const card = document.createElement('article'); card.className = 'event';
        const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = when(e.created) + ' · ' + (e.status === 'sent' ? '已发送' : '等待发送') + ' · 尝试 ' + e.attempts + ' 次';
        const text = document.createElement('pre'); text.textContent = e.payload.text; card.append(meta,text);
        if(e.deliveries?.length){const receipts=document.createElement('p');receipts.className='hint';receipts.textContent=e.deliveries.map(d=>names[d.channel]+'：'+(d.status==='sent'?'已发送':d.error?'失败待重试':'待发送')).join(' · ');card.append(receipts);}
        if(e.error){ const error = document.createElement('p'); error.className='error'; error.textContent=e.error; card.append(error); } $('events').append(card);
      }
    }
    const rk=JSON.stringify(s.records); if(rk!==lastRecords){lastRecords=rk; $('records').replaceChildren(); for(const r of s.records){const d=document.createElement('details'), title=document.createElement('summary'),text=document.createElement('pre');title.textContent=r.name;text.textContent=r.text;d.append(title,text);$('records').append(d);}}
    $('connection').textContent = '页面同步于 ' + new Date().toLocaleTimeString('zh-CN');
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
update(); setInterval(update,5000);
