/* Retrospective comparison of recorded prices only. No trading actions. */
let portfolioData=null,portfolioKey='',portfolioBusy=false,portfolioOpen=new Set();
const modelNames={hold:'模型1 · Fomo 留存持有',recover2:'模型2 · 2倍回本 / 4倍减半',runner25:'模型3A · 2.5倍净收100U',runner25_110:'模型3B · 2.5倍净收110U'};
const fmtU=n=>Number.isFinite(n)?n.toFixed(2)+' U':'—';
const fmtPrice=n=>Number.isFinite(n)?'$'+n.toLocaleString('en-US',{maximumSignificantDigits:6}):'—';
const fmtPct=n=>Number.isFinite(n)?(n>0?'+':'')+n.toFixed(1)+'%':'—';
const signedU=n=>Number.isFinite(n)?(n>0?'+':'')+fmtU(n):'—';
function modelOf(t){return t.simulation.models.find(m=>m.id===$('modelSelect').value)||t.simulation.models[0];}
async function loadPortfolio(){
 if(portfolioBusy)return;portfolioBusy=true;
 try{
  const buy=Number($('buyFee').value),sell=Number($('sellFee').value);
  if(!Number.isFinite(buy)||!Number.isFinite(sell)||buy<0||buy>=100||sell<0||sell>=100)throw Error('成本比例需为0至99之间的数值');
  const r=await fetch('/api/portfolio');if(!r.ok)throw Error('无法读取模拟账本');
  const data=await r.json();const key=JSON.stringify(data);portfolioData=data;
  if(key!==portfolioKey){portfolioKey=key;renderPortfolio();renderHistory();}
  $('portfolioStatus').textContent='每标的100U · 按已记录价格回放 · '+new Date().toLocaleTimeString('zh-CN')+'同步';
 }catch(e){$('portfolioStatus').textContent=e.message+'；请保留上次结果，等待服务恢复';}finally{portfolioBusy=false;}
}
function cell(main,sub,cls){const td=el('td',cls||'');td.append(el('div','cell-main',main));if(sub)td.append(el('div','cell-sub',sub));return td;}
function renderPortfolio(){
 if(!portfolioData)return;
 const all=portfolioData.tokens,query=$('search').value.trim().toLowerCase(),modelId=$('modelSelect').value;
 const covered=all.filter(t=>!t.awaiting_sample),pnl=covered.reduce((n,t)=>n+modelOf(t).pnl,0);
 $('portfolioInvested').textContent=all.length+'个标的 · 每个方案总投入 '+all.length*100+' U';
 $('portfolioCoverage').textContent=covered.length+' / '+all.length;
 $('portfolioPnl').textContent=covered.length?signedU(pnl):'待采样';$('portfolioPnl').className=pnl>=0?'profit':'loss';
 $('portfolioScope').textContent='仅含 '+covered.length+' 个有后续价格的标的；时间各不相同';
 let rows=all.filter(t=>{
  if(query&&!(t.name+' '+t.ca+' '+t.chain).toLowerCase().includes(query))return false;
  if(activeFilter==='strong'&&t.entry.fomo_ratio_lower<=20)return false;
  if(activeFilter==='watch'&&t.entry.fomo_ratio_lower>20)return false;
  if(activeFilter==='pending'&&!currentEvents.some(e=>e.payload.ca===t.ca&&e.status!=='sent'))return false;
  return true;
 });
 const sort=$('portfolioSort').value;
 rows.sort((a,b)=>{
  if(sort==='waiting')return Number(b.awaiting_sample)-Number(a.awaiting_sample)||new Date(b.latest.observed_at)-new Date(a.latest.observed_at);
  if(sort==='recent')return new Date(b.latest.observed_at)-new Date(a.latest.observed_at);
  if(a.awaiting_sample!==b.awaiting_sample)return Number(a.awaiting_sample)-Number(b.awaiting_sample);
  return (modelOf(a).pnl-modelOf(b).pnl)*(sort==='profit_asc'?1:-1);
 });
 $('portfolioCount').textContent=rows.length+' 个';$('portfolioTable').replaceChildren();
 if(!rows.length){$('portfolioTable').append(el('div','empty','暂无符合当前筛选的已登记标的'));return;}
 const wrap=el('div','table-scroll'),table=el('table','positions-table'),head=el('thead'),tr=el('tr');
 for(const label of ['标的 / 链','首次 → 最新价格','区间涨跌','Fomo 占比','100U 平台费后盈亏','费用规则','对比'])tr.append(el('th','',label));head.append(tr);table.append(head);
 const body=el('tbody');
 for(const t of rows){
  const m=modelOf(t),classes=[t.awaiting_sample?'awaiting':'',t.latest_entry?'latest-entry':'',t.watch.status==='dropping'?'dropping':''].filter(Boolean).join(' '),row=el('tr',classes),asset=cell(t.name,t.chain);
  if(t.latest_entry)asset.prepend(el('span','signal-badge latest-badge','最新入选 · 当前规则观察点'));
  if(t.watch.status==='dropping')asset.prepend(el('span','signal-badge drop-badge','可能移出 '+t.watch.ineligible_streak+'/3轮'));
  const copy=el('button','ca-button',t.ca.slice(0,6)+'…'+t.ca.slice(-4)+' 复制');copy.title=t.ca;copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(t.ca);toast('完整CA已复制');}catch{toast('请在展开详情中复制完整CA');}});asset.append(copy);row.append(asset);
  row.append(cell(t.awaiting_sample?'待采样':fmtPrice(t.latest.price), '入场 '+fmtPrice(t.entry.price)+(t.latest.price_estimated?' · 最新为估算':'')));
  row.append(cell(t.awaiting_sample?'—':fmtPct((t.simulation.multiple-1)*100),t.awaiting_sample?'只有首次观测':t.simulation.multiple.toFixed(2)+'×',t.simulation.multiple>=1?'profit':'loss'));
  const lower=t.latest.fomo_ratio_lower;
  row.append(cell(Number.isFinite(lower)?'≥'+lower+'%':'本轮未核验','初始 ≥'+t.entry.fomo_ratio_lower+'%'));
  row.append(cell(t.awaiting_sample?'待采样':signedU(m.pnl),t.awaiting_sample?'仅入场清算 '+fmtU(m.net_value):'估算清算值 '+fmtU(m.net_value),t.awaiting_sample?'':m.pnl>=0?'profit':'loss'));
  row.append(cell(t.fees.label,t.fees.schedule==='solana'?'100U买入平台费0.95U':'100U买入平台费0.50U'));
  const action=el('td');const button=el('button','text-button',portfolioOpen.has(t.id)?'收起':'比较模型');button.addEventListener('click',()=>{portfolioOpen.has(t.id)?portfolioOpen.delete(t.id):portfolioOpen.add(t.id);renderPortfolio();});action.append(button,el('div','cell-sub',new Date(t.latest.observed_at).toLocaleTimeString('zh-CN')+(t.awaiting_sample?' · 首笔':t.stale?' · 已过期':' · 最后观测')));if(t.watch.status==='dropping')action.append(el('div','cell-sub',t.watch.reason));row.append(action);body.append(row);
  if(portfolioOpen.has(t.id)){const expanded=el('tr','model-expanded'),td=el('td');td.colSpan=7;td.append(tokenDetail(t));expanded.append(td);body.append(expanded);}
 }
 table.append(body);wrap.append(table);$('portfolioTable').append(wrap);
}
function priceCurve(t){
 const samples=t.samples.filter(s=>Number.isFinite(s.price)),prices=samples.map(s=>s.price),w=760,h=190,p=22;
 const root=el('div','history-chart');
 if(!prices.length)return root;
 const low=Math.min(...prices,t.entry.price*.60),high=Math.max(...prices,t.entry.price),span=high-low||high*.01||1;
 const x=i=>p+(w-p*2)*(samples.length===1?.5:i/(samples.length-1)),y=v=>p+(h-p*2)*(high-v)/span;
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.setAttribute('role','img');svg.setAttribute('aria-label',t.name+' 历史价格曲线');
 const stop=document.createElementNS(svg.namespaceURI,'line');for(const [k,v] of Object.entries({x1:p,x2:w-p,y1:y(t.entry.price*.60),y2:y(t.entry.price*.60)}))stop.setAttribute(k,v);stop.setAttribute('class','stop-line');svg.append(stop);
 const path=document.createElementNS(svg.namespaceURI,'polyline');path.setAttribute('points',samples.map((s,i)=>x(i)+','+y(s.price)).join(' '));path.setAttribute('class','price-line');svg.append(path);root.append(svg);
 const meta=el('div','chart-meta');meta.append(el('span','',`首次 ${fmtPrice(t.entry.price)}`),el('span','',`最高 ${fmtPrice(Math.max(...prices))}`),el('span','',`最低 ${fmtPrice(Math.min(...prices))}`),el('span','',`${samples.length} 个观测点`));root.append(meta);return root;
}
function renderHistory(){
 if(!portfolioData)return;const rows=portfolioData.history||[],root=$('historyList');root.replaceChildren();$('historyCount').textContent=rows.length;
 if(!rows.length){root.append(el('div','empty','暂无退出主清单的标的'));return;}
 for(const t of rows){
  const card=el('article','history-card'),head=el('div','history-head'),identity=el('div');identity.append(el('h2','',t.name),el('p','',t.chain+' · '+t.ca));
  const change=t.archive.trigger_change_percent;head.append(identity,el('span','history-reason',t.archive.kind==='price_stop'?'跌幅退出 '+fmtPct(change):'资格退出'));card.append(head,priceCurve(t));
  const note=el('p','history-note',t.archive.reason+' · '+new Date(t.archive.archived_at).toLocaleString('zh-CN')+' · 记录与模拟数据均保留');card.append(note);
  const detail=el('details','history-detail');detail.append(el('summary','','查看完整模拟账本与观测记录'),tokenDetail(t));card.append(detail);root.append(card);
 }
}
function tokenDetail(t){
 const root=el('div','token-detail');root.append(el('h3','',t.name+' · 100U 模拟账本'),el('p','fee-explainer','入场 '+t.entry.observed_at+'，最新采样 '+t.latest.observed_at+'。'+(t.entry.note||'')),el('code','',t.ca));
 const feeInfo=el('section','fee-explainer');feeInfo.append(el('p','',t.fees.rule),el('p','',t.fees.network+'；'+t.fees.exclusions),el('p','',t.fees.basis+' · 核查 '+t.fees.checked_at));const source=el('a','','官方手续费说明');source.href=t.fees.source;source.target='_blank';source.rel='noopener';feeInfo.append(source);root.append(feeInfo);
 const grid=el('div','model-comparison');
 for(const m of t.simulation.models){
  const card=el('section','model-box');card.append(el('h4','',modelNames[m.id]||m.id),el('strong',m.pnl>=0?'profit':'loss',signedU(m.pnl)));
  for(const [label,value] of [['已回收净现金',fmtU(m.cash)],['剩余代币',m.remaining_percent.toFixed(2)+'%'],['剩余仓位市值',fmtU(m.remaining_gross_value)],['已扣平台费用',fmtU(m.paid_fees)],['剩余仓位预估平台费',fmtU(m.liquidation_fee)],['平台费后估算总值',fmtU(m.net_value)]]){const line=el('div','model-line');line.append(el('span','',label),el('b','',value));card.append(line);}
  card.append(el('p','fee-explainer',({stop_loss:'已按观测价止损退出',fomo_retention:'连续两次留存条件成立，已退出',principal_recovered_then_halved:'已回本，并在后续4倍观测减半',principal_recovered:'已净收回100U，等待后续4倍',principal_recovery_unreachable:'费用过高，当前仓位不足以净回本',runner_recover_100:'已净收100U，剩余仓位继续持有',runner_recover_110:'已净收110U，剩余仓位继续持有',runner_recovery_unreachable:'费用过高，当前仓位不足以完成回收',no_2x_observed:'尚未观测到2倍',no_2_5x_observed:'尚未观测到2.5倍',no_exit_trigger:'尚未满足退出条件'})[m.reason]||m.status));
  const trades=el('details');trades.append(el('summary','', '逐笔模拟交易 · '+m.trades.length+' 笔'));
  for(const tx of m.trades){const p=el('div','trade-item');p.textContent=(tx.type==='buy'?'买入':tx.type==='sell'?'卖出':tx.type)+' · '+tx.time+'\n价格 '+fmtPrice(tx.price)+' / 平台费 '+fmtU(tx.fee)+'（'+(tx.effective_fee_rate*100).toFixed(3)+'%）'+' / 净额 '+fmtU(tx.net);trades.append(p);}card.append(trades);grid.append(card);
 }
 root.append(grid);
 const points=el('details','sample-history');points.append(el('summary','', '观测证据 · '+t.samples.length+' 笔（非连续行情）'));
 for(const s of t.samples)points.append(el('p','fee-explainer',s.observed_at+' · '+fmtPrice(s.price)+' · '+s.source+(s.price_estimated?' · 估算':'')+(s.note?' · '+s.note:'')));
 root.append(points);return root;
}
window.renderPortfolio=renderPortfolio;
$('modelSelect').addEventListener('change',renderPortfolio);$('portfolioSort').addEventListener('change',renderPortfolio);
$('recalculate').addEventListener('click',()=>{portfolioKey='';loadPortfolio();});
loadPortfolio();setInterval(loadPortfolio,5000);
