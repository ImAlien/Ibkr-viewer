const $=s=>document.querySelector(s);let data=null,view='overview',toastTimer,calendarMonth='',calendarYear='',calendarMode='month',pnlPeriod='month',pnlSortKey='total',pnlSortDesc=true;
let flexMessage='',wealthMode='nav',pnlDisplay='heatmap';
const securityViewCache=new Map(),securityRequests=new Set();
const money=(x,c='USD',digits=2)=>`${c} ${new Intl.NumberFormat('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits}).format(Number(x)||0)}`;
const signed=(x,c='USD')=>{const n=Number(x)||0;return `<span class="${n>0?'positive':n<0?'negative':''}">${n>0?'+':''}${money(n,c)}</span>`};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const field=(row,key,i)=>row?.[key]!==undefined&&row?.[key]!==null&&row?.[key]!==''?row[key]:row?._values?.[i]??'';
const num=(row,key,i)=>Number(String(field(row,key,i)||'0').replace(/,/g,''))||0;
const val=field;
const fmtDate=d=>{if(!d)return '—';const m=String(d).match(/\d{4}-\d{2}-\d{2}/);return m?m[0]:d};
const pct=(x,total)=>total?Math.max(0,Math.min(100,x/total*100)):0;
function showToast(s){const t=$('#toast');t.textContent=s;t.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),2800)}
function heading(title,sub,period=true){const start=data.range?.start||data.latest?.period_start,end=data.range?.end||data.latest?.period_end;return `<div class="page-heading"><div><h1>${title}</h1><p>${sub}</p></div>${period&&start?`<span class="period-pill">${esc(start)} — ${esc(end)}</span>`:''}</div>`}
function kpi(label,value,foot=''){return `<article class="card kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div>${foot?`<div class="kpi-foot">${foot}</div>`:''}</article>`}
function cardHead(title,sub='',action=''){return `<div class="card-head"><div><div class="card-title">${title}</div>${sub?`<div class="card-subtitle">${sub}</div>`:''}</div>${action}</div>`}
function table(headers,rows,empty='暂无记录'){return `<div class="table-wrap"><table class="table"><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.join(''):`<tr><td colspan="${headers.length}"><div class="empty-state">${empty}</div></td></tr>`}</tbody></table></div>`}
function positionRows(rows,detailed=false){return rows.map(r=>{
 const symbol=val(r,'代码',3)||'—',name=val(r,'描述',2),qty=num(r,'数量',4),currency=val(r,'货币',2)||data.base_currency;
 const price=x=>`${esc(currency)} ${new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:4}).format(Number(x)||0)}`;
 const averagePrice=x=>`${esc(currency)} ${new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(x)||0)}`;
 const signedPercent=x=>x==null?'<span class="muted-value">—</span>':`<span class="${x>0?'positive':x<0?'negative':''}">${x>0?'+':''}${Number(x).toFixed(2)}%</span>`;
 const average=r.average_entry_price==null?'<span class="muted-value">—</span>':averagePrice(r.average_entry_price);
 const extra=detailed?`<td class="mono" title="按已导入的股票成交重建；买入手续费计入，卖出按当时平均成本扣减">${average}</td><td class="mono" title="Flex 未平仓持仓报表的 CostBasisPrice，可能与交易页面的平均价不同">${price(num(r,'成本价格',6))}</td><td class="mono">${price(num(r,'收盘价格',8))}</td><td class="mono">${signedPercent(r.profit_pct)}</td><td class="mono" title="${r.previous_price_date?`较 ${esc(r.previous_price_date)} 收盘价；上一价 ${price(r.previous_price)}`:'无前一估值日的同一合约报价'}">${signedPercent(r.day_change_pct)}</td>`:'';
 return `<tr><td class="symbol-cell">${esc(symbol)}${name?`<span class="secondary">${esc(name)}</span>`:''}</td><td><span class="badge stock">${esc(val(r,'资产分类',1)||'资产')}</span></td><td class="mono">${new Intl.NumberFormat('en-US',{maximumFractionDigits:4}).format(qty)}</td>${extra}<td class="mono">${money(num(r,'成本基础',7),currency)}</td><td class="mono">${money(num(r,'价值',9),currency)}</td><td class="mono">${signed(num(r,'未实现的损益',10),currency)}</td></tr>`
})}
function positionPie(){
 const categories=data.position_distribution||[],total=categories.reduce((sum,row)=>sum+row.gross_value,0);
 if(!total)return `<article class="card holdings-pie-card">${cardHead('持仓类别占比','所选日期没有可计算的持仓市值')}<div class="empty-state">暂无持仓</div></article>`;
 const colors=['#14846d','#dc9870','#6c91b9','#a988c1','#b5ba72','#839397'];let offset=0;
 const segments=categories.map((row,index)=>{const next=offset+row.gross_value/total*100,part=`${colors[index%colors.length]} ${offset.toFixed(4)}% ${next.toFixed(4)}%`;offset=next;return part});
 const legend=categories.map((row,index)=>`<div class="pie-legend-row"><i style="background:${colors[index%colors.length]}"></i><span>${esc(({STK:'股票',OPT:'期权',BOND:'债券',FUND:'基金'})[row.asset]||row.asset)} <small>${row.count} 项</small></span><strong>${(row.gross_value/total*100).toFixed(1)}%</strong><em>${money(row.gross_value,data.base_currency)}</em></div>`).join('');
 const missing=(data.position_distribution_missing_rates||[]).length?`；缺少 ${esc(data.position_distribution_missing_rates.join('、'))} 汇率的持仓未计入`:'';
 return `<article class="card holdings-pie-card">${cardHead('持仓类别占比',`截至 ${esc(data.latest?.period_end||'所选日期无快照')} · 基础货币市值绝对值`)}<div class="holdings-pie-body"><div class="position-pie" role="img" aria-label="各类持仓市值占比" style="background:conic-gradient(${segments.join(',')})"></div><div class="pie-legend">${legend}</div></div><p class="pnl-note">按持仓市值绝对值计算，空头期权按绝对值计入${missing}。</p></article>`;
}
function stockPie(){
 const stocks=data.stock_distribution||[],total=stocks.reduce((sum,row)=>sum+row.gross_value,0);
 if(!total)return `<article class="card holdings-pie-card">${cardHead('各股票占比','所选日期没有可计算的股票市值')}<div class="empty-state">暂无股票持仓</div></article>`;
 const colors=['#14846d','#dc9870','#6c91b9','#a988c1','#b5ba72','#3d9d9b','#c2768a','#839397'];let offset=0;
 const segments=stocks.map((row,index)=>{const next=offset+row.gross_value/total*100,part=`${colors[index%colors.length]} ${offset.toFixed(4)}% ${next.toFixed(4)}%`;offset=next;return part});
 const legend=stocks.map((row,index)=>`<div class="pie-legend-row"><i style="background:${colors[index%colors.length]}"></i><span>${esc(row.symbol)} <small>${esc(row.currency)}</small></span><strong>${(row.gross_value/total*100).toFixed(1)}%</strong><em>${money(row.gross_value,data.base_currency)}</em></div>`).join('');
 return `<article class="card holdings-pie-card">${cardHead('各股票占比',`截至 ${esc(data.latest?.period_end||'所选日期无快照')} · 股票市值占比`)}<div class="holdings-pie-body"><div class="position-pie" role="img" aria-label="各股票市值占比" style="background:conic-gradient(${segments.join(',')})"></div><div class="pie-legend">${legend}</div></div><p class="pnl-note">各股票占比的分母为全部股票持仓市值绝对值，按快照日汇率折算为 ${esc(data.base_currency)}。</p></article>`;
}
function chartRangeStart(key,end){
 const d=new Date(end+'T00:00:00Z');
 if(key==='all')return data.range.min;
 if(key==='mtd'||key==='ytd')return [key==='mtd'?end.slice(0,7)+'-01':end.slice(0,4)+'-01-01',data.range.min].sort().pop();
 if(key==='week')d.setUTCDate(d.getUTCDate()-7);
 else {const day=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-({month:1,quarter:3,year:12}[key]));const last=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();d.setUTCDate(Math.min(day,last))}
 d.setUTCDate(d.getUTCDate()+1); // Inclusive filter: use the cutoff day's closing NAV as the opening baseline.
 return [d.toISOString().slice(0,10),data.range.min].sort().pop();
}
function navChart(points,percentage=false){
 if(!points?.length)return '<div class="empty-chart">'+(percentage?'暂无可计算的时间加权收益':'所选日期范围内没有净值快照')+'</div>';
 const W=900,H=250,L=72,R=16,T=20,B=34,values=points.map(p=>p.value),minimum=Math.min(...values),maximum=Math.max(...values),padding=(maximum-minimum||Math.abs(maximum)*.02||1)*.12,lo=minimum-padding,hi=maximum+padding,span=hi-lo;
 const timestamp=p=>Date.parse(p.date+'T00:00:00Z'),first=timestamp(points[0]),last=timestamp(points[points.length-1]);
 const x=p=>L+(last===first?(W-L-R)/2:(timestamp(p)-first)/(last-first)*(W-L-R)),y=v=>T+(hi-v)/span*(H-T-B);
 const format=v=>percentage?`${v.toFixed(2)}%`:money(v,data.base_currency);
 const axis=v=>percentage?`${v.toFixed(1)}%`:new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(v);
 const grid=[0,.25,.5,.75,1].map(q=>{const v=hi-q*span;return `<line class="chart-gridline" x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}"/><text class="axis-label" x="${L-9}" text-anchor="end" y="${y(v)+3}">${axis(v)}</text>`}).join('');
 const ticks=[...new Set(Array.from({length:Math.min(6,points.length)},(_,i)=>Math.round(i*(points.length-1)/Math.max(1,Math.min(6,points.length)-1))))];
 const line=points.map((p,i)=>`${i?'L':'M'}${x(p)},${y(p.value)}`).join(' ');
 const labels=ticks.map(i=>`<text class="axis-label" text-anchor="${i===0?'start':i===points.length-1?'end':'middle'}" x="${x(points[i])}" y="${H-7}">${esc(points[i].date)}</text>`).join('');
 const markers=points.map(p=>`<circle class="wealth-point" cx="${x(p)}" cy="${y(p.value)}" r="5" aria-hidden="true" data-chart-date="${esc(p.date)}" data-chart-value="${esc(format(p.value))}" data-chart-number="${p.value}" data-chart-label="${esc(p.date+' · '+format(p.value))}"><title>${esc(p.date)} · ${format(p.value)}</title></circle>`).join('');
 return `<svg class="interactive-wealth-svg" data-percentage="${percentage}" viewBox="0 0 ${W} ${H}" tabindex="0" role="img" aria-label="鼠标移动或点击查看数据，方向键切换日期，Esc 清除选择。${percentage?'时间加权收益':'净清算价值'}曲线">${grid}${percentage&&lo<0&&hi>0?`<line class="chart-zero" x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}"/>`:''}<path class="chart-line" d="${line}"/>${markers}${labels}<g class="wealth-crosshair" visibility="hidden" aria-hidden="true"><line class="wealth-guide-vertical" y1="${T}" y2="${H-B}"/><line class="wealth-guide-horizontal" x1="${L}" x2="${W-R}"/><circle class="wealth-active-point" r="4.5"/></g><rect class="wealth-hit-area" x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent"/></svg>`;
}
function setupWealthCursor(){
 const svg=document.querySelector('.interactive-wealth-svg');if(!svg)return;
 const card=svg.closest('.wealth-card'),wrap=svg.parentElement,tooltip=wrap.querySelector('.wealth-tooltip'),readout=card.querySelector('.wealth-readout'),summary=card.querySelector('.wealth-summary strong'),caption=card.querySelector('.wealth-summary span');
 const markers=[...svg.querySelectorAll('[data-chart-label]')],cursor=svg.querySelector('.wealth-crosshair'),vertical=cursor.querySelector('.wealth-guide-vertical'),horizontal=cursor.querySelector('.wealth-guide-horizontal'),dot=cursor.querySelector('circle'),hit=svg.querySelector('.wealth-hit-area');
 const original={value:summary.textContent,caption:caption.textContent,className:summary.className,readout:readout.textContent};let pinned=null,current=null;
 const show=index=>{
  const marker=markers[index];if(!marker)return;current=index;
  const x=Number(marker.getAttribute('cx')),y=Number(marker.getAttribute('cy')),label=marker.dataset.chartLabel;
  cursor.setAttribute('visibility','visible');vertical.setAttribute('x1',x);vertical.setAttribute('x2',x);horizontal.setAttribute('y1',y);horizontal.setAttribute('y2',y);dot.setAttribute('cx',x);dot.setAttribute('cy',y);
  summary.textContent=marker.dataset.chartValue;summary.className=svg.dataset.percentage==='true'?(Number(marker.dataset.chartNumber)<0?'negative':'positive'):'';
  caption.textContent=marker.dataset.chartDate+' · '+(svg.dataset.percentage==='true'?'截至该日的区间累计收益':'当日净清算价值');readout.textContent=label+(pinned===index?' · 已选中':'');
  tooltip.querySelector('span').textContent=marker.dataset.chartDate;tooltip.querySelector('strong').textContent=marker.dataset.chartValue;tooltip.querySelector('strong').className=summary.className;tooltip.hidden=false;
  const point=svg.createSVGPoint();point.x=x;point.y=y;const matrix=svg.getScreenCTM();if(!matrix)return;const screen=point.matrixTransform(matrix),rect=wrap.getBoundingClientRect();
  const left=screen.x-rect.left,top=screen.y-rect.top,w=tooltip.offsetWidth,h=tooltip.offsetHeight;
  tooltip.style.left=Math.max(6,Math.min(rect.width-w-6,left+w+18<rect.width?left+14:left-w-14))+'px';
  tooltip.style.top=Math.max(4,Math.min(rect.height-h-4,top-h-12))+'px';
 };
 const reset=()=>{current=null;cursor.setAttribute('visibility','hidden');tooltip.hidden=true;summary.textContent=original.value;summary.className=original.className;caption.textContent=original.caption;readout.textContent=original.readout};
 const nearest=event=>{const matrix=svg.getScreenCTM();if(!matrix)return null;const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;const x=point.matrixTransform(matrix.inverse()).x;let index=0;for(let i=1;i<markers.length;i++)if(Math.abs(Number(markers[i].getAttribute('cx'))-x)<Math.abs(Number(markers[index].getAttribute('cx'))-x))index=i;return index};
 hit.onpointermove=event=>{const index=nearest(event);if(index!==null)show(index)};
 hit.onclick=event=>{const index=nearest(event);if(index!==null){pinned=index;show(index);svg.focus({preventScroll:true})}};
 svg.onpointerleave=()=>pinned===null?reset():show(pinned);
 svg.onkeydown=event=>{
  if(event.key==='Escape'){event.preventDefault();pinned=null;reset();return}
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();const index=current??markers.length-1;
  pinned=event.key==='Home'?0:event.key==='End'?markers.length-1:Math.max(0,Math.min(markers.length-1,index+(event.key==='ArrowRight'?1:-1)));show(pinned);
 };
 svg.onfocus=()=>{if(current===null)show(markers.length-1)};
 svg.onblur=()=>{if(pinned===null)reset()};
}
function wealthChart(){
 const isTwr=wealthMode==='twr',twr=data.twr_history||{},points=isTwr?twr.points:data.nav_history,end=data.range.max;
 const options=[['week','一周'],['month','一月'],['quarter','三月'],['year','一年'],['mtd','本月'],['ytd','本年'],['all','全部']];
 const active=options.find(([key])=>data.range.end===end&&data.range.start===chartRangeStart(key,end))?.[0];
 const ranges=options.map(([key,label])=>`<button data-wealth-range="${key}" class="${active===key?'active':''}" aria-pressed="${active===key}">${label}</button>`).join('');
 const tabs=[['nav','净清算价值'],['twr','时间加权收益']].map(([key,label])=>`<button data-wealth-mode="${key}" class="${wealthMode===key?'active':''}" aria-pressed="${wealthMode===key}">${label}</button>`).join('');
 const value=isTwr?(twr.total==null?'—':`${twr.total>=0?'+':''}${twr.total.toFixed(2)}%`):(points?.length?money(points[points.length-1].value,data.base_currency):'—');
 const period=points?.length?`${points[0].date} — ${points[points.length-1].date}`:'暂无数据';
 return `<article class="card wealth-card"><div class="card-head"><div class="pnl-period-switch" aria-label="曲线类型">${tabs}</div><div class="pnl-period-switch wealth-ranges" aria-label="曲线时间范围">${ranges}${!active?'<span class="custom-range-label">自定义</span>':''}</div></div><form id="wealthRangeForm" class="wealth-custom-range"><span>自定义区间</span><label for="wealthRangeStart">开始日期</label><input id="wealthRangeStart" type="date" required min="${esc(data.range.min)}" max="${esc(end)}" value="${esc(data.range.start)}"/><label for="wealthRangeEnd">结束日期</label><input id="wealthRangeEnd" type="date" required min="${esc(data.range.min)}" max="${esc(end)}" value="${esc(data.range.end)}"/><button type="submit" class="range-apply">查看区间</button><span class="wealth-range-hint">可选 ${esc(data.range.min)} 至 ${esc(end)}</span></form><div class="wealth-summary"><strong class="${isTwr?(twr.total<0?'negative':'positive'):''}">${value}</strong><span>${esc(period)} · ${isTwr?'区间累计收益':'基础货币净值'}</span></div><div class="chart-wrap wealth-chart">${navChart(points,isTwr)}<div class="wealth-tooltip" hidden><span></span><strong></strong></div></div><div class="wealth-readout" aria-live="polite">移动鼠标查看每日数值，点击保留选中日期；方向键切换，Esc 清除</div><p class="pnl-note">${isTwr?esc(twr.note||'暂无日频数据'): '按已导入的每日净值绘制；快捷区间截至账本最新日期 '+esc(end)+'，本月 / 本年从该日期所在月 / 年的第一天开始，也可自定义日期区间。'}${isTwr&&points?.length?' 起点使用可用的期初净值；首次入金前净值为零时，收益从首次正净值起算。':''}</p></article>`;
}
function historyChart(points,period='month'){if(!points?.length)return '<div class="empty-chart">所选日期范围内没有盈亏数据</div>';const W=700,H=180,L=52,R=12,T=13,B=28,vals=points.map(p=>p.pnl),lo=Math.min(0,...vals),hi=Math.max(0,...vals),span=hi-lo||1,y=v=>T+(hi-v)/span*(H-T-B),x=i=>L+(points.length===1?(W-L-R)/2:i*(W-L-R)/(points.length-1));let grid=[0,.5,1].map(q=>{let v=hi-q*span,yy=y(v);return `<line class="chart-gridline" x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}"/><text class="axis-label" x="0" y="${yy+3}">${Math.round(v/1000)}k</text>`}).join('');let line=points.map((p,i)=>`${i?'L':'M'}${x(i)},${y(p.pnl)}`).join(' '),area=`${line} L${x(points.length-1)},${H-B} L${x(0)},${H-B} Z`;const label=p=>period==='day'?p.date.slice(5):period==='year'?p.year:p.month||((p.period||'').split(' – ')[1]||(p.period||'')).slice(0,7);const tickCount=Math.min(points.length,period==='day'?7:8),tickIndices=[...new Set(Array.from({length:tickCount},(_,i)=>tickCount===1?0:Math.round(i*(points.length-1)/(tickCount-1))))],ticks=tickIndices.map(i=>`<text class="axis-label" text-anchor="${i===0?'start':i===points.length-1?'end':'middle'}" x="${x(i)}" y="${H-5}">${esc(label(points[i]))}</text>`).join(''),markerIndices=points.length<=60?points.map((_,i)=>i):tickIndices,markers=markerIndices.map(i=>`<circle class="chart-end" cx="${x(i)}" cy="${y(points[i].pnl)}" r="3.2"><title>${esc(label(points[i]))} · ${money(points[i].pnl,data.base_currency)}</title></circle>`).join('');return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${period==='day'?'逐日':period==='year'?'逐年':'逐月'}盈亏趋势"><defs><linearGradient id="areaGrad" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#42a88c" stop-opacity=".19"/><stop offset="1" stop-color="#42a88c" stop-opacity="0"/></linearGradient></defs>${grid}<path class="chart-area" d="${area}"/><path class="chart-line" d="${line}"/>${markers}${ticks}</svg>`}
function distribution(){
 const total=data.kpis.net_liquidation,rows=data.allocation||[],scale=Math.max(1,...rows.map(row=>Math.abs(row.value)));
 return `<div class="allocation-body"><div class="allocation-total">${money(total,data.base_currency)}</div><div class="allocation-note">期末净清算价值 · ${esc(data.base_currency)} 基础货币</div>${rows.map(row=>{
  const negative=row.value<0,width=Math.abs(row.value)/scale*50;
  const detail=row.name==='期权'&&row.long_value!=null&&row.short_value!=null?`多头 ${money(row.long_value,data.base_currency)} · 空头 ${money(row.short_value,data.base_currency)}`:row.note||'';
  return `<div class="asset-row"><div class="asset-name"><i class="asset-dot ${negative?'liability':''}"></i>${esc(row.name)}${row.name==='期权'?'净市值':''}</div><div class="asset-val ${negative?'negative':''}">${money(row.value,data.base_currency)}</div>${detail?`<div class="allocation-detail">${esc(detail)}</div>`:''}<div class="allocation-signed-bar" title="${esc(row.name)} ${money(row.value,data.base_currency)}"><i class="${negative?'liability':''}" style="left:${negative?50-width:50}%;width:${width}%"></i></div></div>`;
 }).join('')}<p class="allocation-explanation">负值向左显示，正值向右显示；条形按市值绝对值同比例绘制。期权为多空净市值，负值表示净负债，不是盈亏。各项合计为净清算价值。</p></div>`;
}
function overview(){const k=data.kpis,positions=data.positions.slice(0,6),cashflows=data.cashflows.slice(0,5);return `${heading('账户总览','历史总账本 · 资产快照截至所选日期')}<div class="grid kpis">${kpi('净清算价值',money(k.net_liquidation,data.base_currency),'所选日期期末 · 基础货币')}${kpi('期末现金',money(k.cash,data.base_currency),'所选日期期末 · 含多币种折算')}${kpi('所选区间净盈亏',`<span class="${k.period_realized_unrealized>=0?'positive':'negative'}">${money(k.period_realized_unrealized,data.base_currency)}</span>`,'期末净资产变化减去净出入金')}${kpi('基础货币股息',money(k.dividends,data.base_currency),'所选区间累计 · 含其他币种折算')}</div>${wealthChart()}<div class="grid content-grid"><article class="card chart-card">${cardHead('逐月净盈亏','所选区间内每日净值盈亏按月汇总',`<div class="chart-legend"><span><i class="legend-mark"></i>月度盈亏</span></div>`)}<div class="chart-wrap">${historyChart(data.history)}</div></article><article class="card allocation-card">${cardHead('资产配置',`截至 ${esc(data.nav_as_of||data.latest?.period_end||'所选日期无快照')}`)}${distribution()}</article></div><div class="grid lower-grid"><article class="card section-card">${cardHead('主要持仓',`共 ${data.positions.length} 项 · 截至 ${esc(data.latest?.period_end||'所选日期无快照')}`,`<button class="card-link" data-goto="holdings">全部持仓　→</button>`)}${table(['代码 / 名称','类别','数量','成本基础','市值','未实现盈亏'],positionRows(positions),'所选日期之前没有持仓快照')}</article><article class="card section-card">${cardHead('近期出入金','所选区间 · 各笔金额按原始币种列示',`<button class="card-link" data-goto="activity">查看资金明细　→</button>`)}${table(['日期','币种','说明','金额'],cashflows.map(e=>`<tr><td class="mono">${esc(fmtDate(e.date))}</td><td><span class="badge">${esc(e.currency)}</span></td><td>${esc(e.description)}</td><td class="mono">${signed(e.amount,e.currency)}</td></tr>`),'所选区间没有存取款记录')}</article></div>`}
function holdings(){
 const asof=data.latest?.period_end||'所选日期无快照';
 return `${heading('持仓','历史总账本中的期末快照；按所选日期查看')}<div class="grid kpis">${kpi('净清算价值',money(data.kpis.net_liquidation,data.base_currency),'所选日期期末')}${kpi('股票市值',money(data.kpis.stocks,data.base_currency),'所选日期期末')}${kpi('持仓项目',new Intl.NumberFormat('zh-CN').format(data.positions.length),'所选日期的未平仓持仓')}${kpi('期末现金',money(data.kpis.cash,data.base_currency),'所选日期期末')}</div><div class="holdings-breakdown-grid">${positionPie()}${stockPie()}</div><article class="card section-card view-grid">${cardHead('未平仓持仓',`截至 ${esc(asof)} · 昨日涨幅比较 ${esc(data.previous_price_date||'无前一估值日')} 的收盘价`)}${table(['代码 / 名称','类别','数量','平均买入价','报表成本价','最新价格','盈亏百分比','昨日涨幅','成本基础','市值','未实现盈亏'],positionRows(data.positions,true),'所选日期之前没有持仓快照')}<p class="pnl-note holdings-table-note">平均买入价按已导入的股票成交和买入手续费重建，卖出按当时平均成本扣减；交易记录无法完整匹配时显示 —。报表成本价来自 Flex 的 CostBasisPrice，可与平均买入价不同。盈亏百分比＝报表未实现盈亏 ÷ 成本基础绝对值；昨日涨幅＝最新价格相对前一估值日收盘价的变化。最新价格为快照日 IBKR 标记价，并非实时行情；价格和金额保留原币。</p></article>`
}
function performanceCalendar(){
 const months=(data.monthly_pnl||[]).map(x=>x.month),years=(data.yearly_pnl||[]).map(x=>x.year);
 const titles={day:'每日盈亏日历',month:'每月盈亏',year:'每年盈亏'};
 const tabs=`<div class="pnl-period-switch" aria-label="盈亏日历粒度">${[['day','每日'],['month','每月'],['year','每年']].map(([key,label])=>`<button data-calendar-mode="${key}" class="${calendarMode===key?'active':''}" aria-pressed="${calendarMode===key}">${label}</button>`).join('')}</div>`;
 if(!months.length)return `<article class="card calendar-card">${cardHead(titles[calendarMode],'所选区间没有可用的日频盈亏数据',tabs)}<div class="empty-state">请调整日期区间或补充日频报表。</div></article>`;
 if(!months.includes(calendarMonth))calendarMonth=months[months.length-1];
 if(!years.includes(calendarYear))calendarYear=calendarMonth.slice(0,4);
 const cls=point=>!point?'calendar-no-data':point.pnl>0?'calendar-positive':point.pnl<0?'calendar-negative':'calendar-flat';
 const coverage=prefix=>{const days=(data.daily_pnl||[]).filter(x=>x.date.startsWith(prefix));return days.length?`${days[0].date} 至 ${days[days.length-1].date}`:''};
 const aggregateCell=(label,point,attribute,prefix)=>`<button type="button" class="calendar-day calendar-aggregate ${cls(point)}" ${point?attribute:'disabled'} title="${esc(point?`${label} · ${money(point.pnl,data.base_currency)} · 已有数据 ${coverage(prefix)}`:label+' · 无数据')}"><span class="calendar-date">${esc(label)}</span><span class="calendar-pnl">${point?signed(point.pnl,data.base_currency):'—'}</span><span class="calendar-coverage">${point?esc(coverage(prefix)):'无数据'}</span></button>`;
 let controls='',cells='',gridClass='calendar-grid',total=0,count=0,period='';
 if(calendarMode==='day'){
  const index=months.indexOf(calendarMonth),[year,month]=calendarMonth.split('-').map(Number),offset=(new Date(year,month-1,1).getDay()+6)%7,days=new Date(year,month,0).getDate(),point=data.monthly_pnl.find(x=>x.month===calendarMonth),dayMap=new Map(data.daily_pnl.map(x=>[x.date,x]));
  period=calendarMonth;total=point.pnl;count=point.trading_days;
  controls=`<div class="calendar-controls"><button data-calendar-step="-1" ${index===0?'disabled':''} aria-label="上个月">‹</button><strong>${calendarMonth}</strong><button data-calendar-step="1" ${index===months.length-1?'disabled':''} aria-label="下个月">›</button></div>`;
  cells=['一','二','三','四','五','六','日'].map(x=>`<div class="calendar-weekday">周${x}</div>`).join('')+'<div class="calendar-blank"></div>'.repeat(offset);
  for(let day=1;day<=days;day++){
   const date=`${calendarMonth}-${String(day).padStart(2,'0')}`,p=dayMap.get(date),value=p?.pnl||0,amount=Math.abs(value)>=1000?`${(Math.abs(value)/1000).toFixed(1)}k`:new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Math.abs(value));
   cells+=`<div class="calendar-day ${cls(p)}" title="${esc(p?`${date} · ${money(value,data.base_currency)} · 净出入金 ${money(p.external_flows,data.base_currency)}`:date+' · 无日频净值')}"><span class="calendar-date">${day}</span><span class="calendar-pnl">${p?(value>0?'+':value<0?'-':'')+amount:'—'}</span></div>`;
  }
 }else if(calendarMode==='month'){
  const index=years.indexOf(calendarYear),point=data.yearly_pnl.find(x=>x.year===calendarYear),monthMap=new Map(data.monthly_pnl.map(x=>[x.month,x]));
  period=calendarYear+' 年';total=point.pnl;count=point.trading_days;gridClass+=' calendar-month-grid';
  controls=`<div class="calendar-controls"><button data-calendar-year-step="-1" ${index===0?'disabled':''} aria-label="上一年">‹</button><strong>${calendarYear}</strong><button data-calendar-year-step="1" ${index===years.length-1?'disabled':''} aria-label="下一年">›</button></div>`;
  for(let month=1;month<=12;month++){const key=`${calendarYear}-${String(month).padStart(2,'0')}`;cells+=aggregateCell(month+' 月',monthMap.get(key),`data-calendar-month="${key}"`,key)}
 }else{
  period='所选区间';total=data.yearly_pnl.reduce((sum,p)=>sum+p.pnl,0);count=data.yearly_pnl.reduce((sum,p)=>sum+p.trading_days,0);gridClass+=' calendar-year-grid';
  cells=data.yearly_pnl.map(p=>aggregateCell(p.year+' 年',p,`data-calendar-year="${p.year}"`,p.year)).join('');
 }
 const hint=calendarMode==='day'?'无数据日期显示为 —':calendarMode==='month'?'点击月份查看每日明细；无数据月份显示为 —':'点击年份查看每月明细';
 return `<article class="card calendar-card"><div class="card-head"><div><div class="card-title">${titles[calendarMode]}</div><div class="card-subtitle">日频净值变化扣除净出入金；${hint}</div></div><div class="calendar-toolbar">${tabs}${controls}</div></div><div class="calendar-summary"><span>${period} 累计盈亏</span><strong>${signed(total,data.base_currency)}</strong><span>${count} 个净值记录日</span></div><p class="pnl-note">仅汇总所选区间 ${esc(data.range.start)} 至 ${esc(data.range.end)} 内已导入的数据；未覆盖完整月份或年份时显示该区间内的累计值。</p><div class="${gridClass}">${cells}</div></article>`;
}
function setupCalendar(){
 document.querySelectorAll('[data-calendar-mode]').forEach(b=>b.onclick=()=>{calendarMode=b.dataset.calendarMode;pnlPeriod=calendarMode;render()});
 document.querySelectorAll('[data-calendar-step]').forEach(b=>b.onclick=()=>{const months=data.monthly_pnl.map(x=>x.month),next=months[months.indexOf(calendarMonth)+Number(b.dataset.calendarStep)];if(next){calendarMonth=next;calendarYear=next.slice(0,4);pnlPeriod='day';render()}});
 document.querySelectorAll('[data-calendar-year-step]').forEach(b=>b.onclick=()=>{const years=data.yearly_pnl.map(x=>x.year),next=years[years.indexOf(calendarYear)+Number(b.dataset.calendarYearStep)];if(next){calendarYear=next;pnlPeriod='month';render()}});
 document.querySelectorAll('[data-calendar-month]').forEach(b=>b.onclick=()=>{calendarMonth=b.dataset.calendarMonth;calendarMode='day';pnlPeriod='day';render()});
 document.querySelectorAll('[data-calendar-year]').forEach(b=>b.onclick=()=>{calendarYear=b.dataset.calendarYear;calendarMode='month';pnlPeriod='month';render()});
}
function securityScopeRange(){
 const months=(data.monthly_pnl||[]).map(x=>x.month),years=(data.yearly_pnl||[]).map(x=>x.year);
 if(!months.includes(calendarMonth))calendarMonth=months.at(-1)||data.range.end.slice(0,7);
 if(!years.includes(calendarYear))calendarYear=calendarMonth.slice(0,4);
 let start=data.range.min,end=data.range.max;
 if(pnlPeriod==='day'){start=calendarMonth+'-01';const [year,month]=calendarMonth.split('-').map(Number);end=new Date(Date.UTC(year,month,0)).toISOString().slice(0,10)}
 else if(pnlPeriod==='month'){start=calendarYear+'-01-01';end=calendarYear+'-12-31'}
 start=[start,data.range.min].sort().pop();end=[end,pnlPeriod==='year'?data.range.max:data.range.end].sort()[0];
 return {start,end,key:JSON.stringify([data.account,(data.imports||[]).map(x=>[x.id,x.sha256,x.imported_at]),start,end])};
}
function selectedSecurityView(){
 const scope=securityScopeRange(),cached=securityViewCache.get(scope.key),loaded=Object.values(data.pnl_asset_views||{}).find(x=>x.start===scope.start&&x.end===scope.end);
 return cached||loaded||{...scope,groups:[],coverage:[],gaps:[],loading:true};
}
async function ensureSecurityScope(){
 const scope=securityScopeRange();if(!selectedSecurityView().loading||securityRequests.has(scope.key))return;
 securityRequests.add(scope.key);
 try{const params=new URLSearchParams({account:data.account,from:scope.start,to:scope.end}),response=await fetch('/api/security-performance?'+params),result=await response.json();if(!response.ok||result.error)throw Error(result.error||'读取个股盈亏失败');securityViewCache.set(scope.key,result)}
 catch(error){securityViewCache.set(scope.key,{...scope,groups:[],coverage:[],gaps:[],error:error.message})}
 finally{securityRequests.delete(scope.key);if(view==='performance'&&!data.empty&&securityScopeRange().key===scope.key)render()}
}
function performance(){
 const modes={day:{points:data.daily_pnl||[],title:'逐日盈亏',field:'date'},month:{points:data.monthly_pnl||[],title:'逐月盈亏',field:'month'},year:{points:data.yearly_pnl||[],title:'逐年盈亏',field:'year'}};
 const current=modes[pnlPeriod]||modes.month,securityView=selectedSecurityView(),groups=securityView.groups||[];
 const stockCount=groups.filter(g=>(g.securities||[]).some(s=>s.asset==='STK')).length;
 const optionCount=groups.reduce((count,g)=>count+(g.securities||[]).filter(s=>s.asset==='OPT').length,0);
 const sortedGroups=[...groups].sort((a,b)=>{
  const left=pnlSortKey==='underlying'?String(a.underlying||'').toLocaleLowerCase():Number(a[pnlSortKey])||0;
  const right=pnlSortKey==='underlying'?String(b.underlying||'').toLocaleLowerCase():Number(b[pnlSortKey])||0;
  const comparison=typeof left==='string'?left.localeCompare(right):left-right;
  return pnlSortDesc?-comparison:comparison;
 });
 const rows=sortedGroups.map((g,i)=>{
  const securities=(g.securities||[]).map(s=>`<tr><td><span class="badge ${s.asset==='STK'?'stock':''}">${s.asset==='OPT'?'期权':'股票'}</span></td><td class="symbol-cell">${esc(s.symbol)}</td><td class="mono">${signed(s.total,data.base_currency)}</td><td class="mono">${s.local_total==null?'—':signed(s.local_total,s.currency)}</td></tr>`);
  const trades=(g.trades||[]).map(t=>`<tr><td class="mono">${esc(t.date)}</td><td><span class="badge ${t.asset==='STK'?'stock':''}">${t.asset==='OPT'?'期权':'股票'}</span></td><td class="symbol-cell">${esc(t.symbol)}</td><td>${esc(t.action||'—')}</td><td class="mono">${new Intl.NumberFormat('en-US',{maximumFractionDigits:4}).format(t.quantity)}</td><td class="mono">${money(t.price,t.currency)}</td><td class="mono">${signed(t.realized,t.currency)}</td></tr>`);
  const detail=`<div class="pnl-expanded"><section><h3>证券盈亏组成 · ${esc(data.base_currency)}</h3>${table(['类别','证券 / 合约','基础货币盈亏','原币盈亏'],securities,'无证券汇总')}</section><section><h3>逐笔交易 · 已实现盈亏按成交币种</h3>${table(['日期','类别','证券 / 合约','类型','数量','价格','已实现盈亏'],trades,'该标的在所选完整报表区间内没有成交明细')}</section></div>`;
  return `<tr class="pnl-summary-row" data-pnl-expand-row="${i}"><td class="symbol-cell"><button type="button" class="pnl-expand-button" aria-expanded="false"><span class="expand-indicator">›</span>${esc(g.underlying)}</button>${g.description?`<span class="secondary">${esc(g.description)}</span>`:''}</td><td class="mono">${signed(g.stock_pnl,data.base_currency)}</td><td class="mono">${signed(g.option_pnl,data.base_currency)}</td><td class="mono">${signed(g.total,data.base_currency)}</td></tr><tr class="pnl-detail-row" data-pnl-detail="${i}" hidden><td colspan="4">${detail}</td></tr>`;
 });
 const timelinePoints=pnlPeriod==='day'?current.points.slice(-12):current.points;
 const chips=timelinePoints.map(x=>`<span>${esc(x[current.field])}　${signed(x.pnl,data.base_currency)}</span>`).join('');
 const periodSwitch=`<div class="pnl-period-switch" role="tablist" aria-label="盈亏统计周期">${[['day','逐日'],['month','逐月'],['year','逐年']].map(([key,label])=>`<button type="button" role="tab" aria-selected="${pnlPeriod===key}" class="${pnlPeriod===key?'active':''}" data-pnl-period="${key}">${label}</button>`).join('')}</div>`;
 const sortHeader=(label,key)=>`<button type="button" class="table-sort" data-pnl-sort="${key}" aria-pressed="${pnlSortKey===key}">${label}<span aria-hidden="true">${pnlSortKey===key?(pnlSortDesc?' ↓':' ↑'):' ↕'}</span></button>`;
 const scopeLabel=({day:'月度个股盈亏',month:'年度个股盈亏',year:'全部个股盈亏'})[pnlPeriod];
 const displaySwitch=`<div class="pnl-period-switch" aria-label="个股盈亏展示方式">${[['heatmap','热力图'],['list','列表']].map(([key,label])=>`<button data-pnl-display="${key}" class="${pnlDisplay===key?'active':''}" aria-pressed="${pnlDisplay===key}">${label}</button>`).join('')}</div>`;
 const coverage=(securityView.coverage||[]).map(x=>`${esc(x.start)} 至 ${esc(x.end)}`).join('；');
 const gaps=(securityView.gaps||[]).map(x=>`${esc(x.start)} 至 ${esc(x.end)}`).join('；');
 const scopeNote=`${esc(securityView.start||'')} 至 ${esc(securityView.end||'')} · ${stockCount} 个正股标的 · ${optionCount} 个期权合约 · ${esc(data.base_currency)}`;
 const maxMagnitude=Math.max(1,...groups.map(g=>Math.abs(g.total)));
 const heatmap=`<div class="security-heatmap">${sortedGroups.map((g,i)=>{const strength=.08+.30*Math.sqrt(Math.abs(g.total)/maxMagnitude),color=g.total>0?'8,126,103':g.total<0?'183,81,72':'130,140,135';return `<button class="security-heat-tile" data-pnl-heat-open="${i}" style="background:rgba(${color},${strength});border-color:rgba(${color},.35)" aria-label="${esc(g.underlying)} 盈亏 ${esc(money(g.total,data.base_currency))}，展开交易"><strong>${esc(g.underlying)}</strong><b>${signed(g.total,data.base_currency)}</b><span>股票 ${money(g.stock_pnl,data.base_currency)}</span><span>期权 ${money(g.option_pnl,data.base_currency)}</span></button>`}).join('')}</div>`;
 const empty=`<div class="empty-state">${securityView.loading?'正在读取该区间的个股盈亏…':securityView.error?esc(securityView.error):'该区间暂无可精确归属的个股盈亏，请补充对应日期的证券盈亏报表。'}</div>`;
 const reconstructionNote=securityView.method==='reconstructed'?`<p class="pnl-note">按账单明细计算：${esc(securityView.note)}${securityView.estimated_currencies?.length?` ${esc(securityView.estimated_currencies.join('、'))} 使用区间内账单汇率均值（无记录时用期末前最近汇率）折算，基础货币金额为估算；展开列表可查看原币盈亏。`:''}</p>${securityView.missing_securities?.length?`<p class="pnl-note security-coverage">以下证券暂未计入：${securityView.missing_securities.map(esc).join('；')}</p>`:''}`:'';
 const securityPanel=`<article class="card section-card stock-pnl-card">${cardHead(scopeLabel,scopeNote,displaySwitch)}<p class="pnl-note">${pnlPeriod==='year'?'展示全部已导入历史。':'跟随盈亏日历所选'+(pnlPeriod==='day'?'月份':'年份')+'统计。'}股票与对应期权合并；绿色为盈利，红色为亏损，颜色越深金额越大。</p>${reconstructionNote}${!securityView.complete&&securityView.method!=='reconstructed'&&!securityView.loading&&!securityView.error?`<p class="pnl-note security-coverage"><strong>部分数据</strong> · 已覆盖：${coverage||'暂无'}${gaps?`。缺少可独立汇总的区间：${gaps}`:''}。不将跨区间报表拆分或补零。</p>`:''}${groups.length?(pnlDisplay==='heatmap'?heatmap:table([sortHeader('正股标的','underlying'),sortHeader('正股盈亏','stock_pnl'),sortHeader('期权盈亏','option_pnl'),sortHeader('合计盈亏','total')],rows)):empty}</article>`;
 return `${heading('盈亏表现','按所选日期范围计算；切换逐日、逐月或逐年查看')}<div class="grid kpis">${kpi('所选区间净盈亏',`<span class="${data.kpis.period_realized_unrealized>=0?'positive':'negative'}">${money(data.kpis.period_realized_unrealized,data.base_currency)}</span>`,'期末净资产 − 期初净资产 − 净出入金')}${kpi('净资产变动',`<span class="${data.kpis.period_mtm>=0?'positive':'negative'}">${money(data.kpis.period_mtm,data.base_currency)}</span>`,'含资金流影响')}${kpi('基础货币股息',money(data.kpis.dividends,data.base_currency),'所选区间累计')}${kpi('基础货币利息',money(data.kpis.interest,data.base_currency),'所选区间累计')}</div>${wealthChart()}<div class="grid content-grid"><article class="card chart-card">${cardHead(current.title,`${current.points.length} 个${pnlPeriod==='day'?'交易日':pnlPeriod==='year'?'自然年':'月份'} · 净值变化扣除净出入金`,periodSwitch)}<div class="chart-wrap">${historyChart(current.points,pnlPeriod)}</div><div class="timeline">${chips}</div></article><article class="card allocation-card">${cardHead('盈亏口径说明','选定日期范围')}<div class="allocation-body"><div class="asset-row"><div class="asset-name">区间净盈亏</div><div class="asset-val">期末日频净资产 − 期初日频净资产 − 净出入金</div></div><div class="asset-row"><div class="asset-name">逐日盈亏</div><div class="asset-val">当日净值变化 − 当日外部资金流</div></div><p class="card-subtitle">日频净值按 IBKR 的基础货币快照；报告区间汇总用于校准首尾边界。</p></div></article></div>${performanceCalendar()}${securityPanel}`
}
function eventRows(events,showBase=false){
 return events.map(e=>`<tr><td class="mono">${esc(fmtDate(e.date))}</td><td><span class="badge">${esc(e.currency)}</span></td><td>${esc(e.description)}</td><td class="mono">${signed(e.amount,e.currency)}</td>${showBase?`<td class="mono">${e.base_amount===null||e.base_amount===undefined?'—':signed(e.base_amount,data.base_currency)}</td>`:''}</tr>`);
}
function activity(){
 const convertedHeaders=['日期','币种','说明','原币金额','基础货币折算'];
 return `${heading('资金与分红','所选区间 · 历史总账本中的资金、股息、税费和利息流水')}${ledgerRangeToolbar()}<div class="grid kpis">${kpi('基础货币净存取款',money(data.kpis.deposits_withdrawals,data.base_currency),'按 IBKR 汇率折算；明细保留原币')}${kpi('基础货币股息',money(data.kpis.dividends,data.base_currency),'含其他币种折算；明细保留原币')}${kpi('基础货币代扣税',money(data.kpis.withholding_tax,data.base_currency),'股息等代扣税；按交易时间汇率折算')}${kpi('基础货币交易税费',money(data.kpis.trading_taxes,data.base_currency),'印花税及交易征费；按交易时间汇率折算')}${kpi('基础货币手续费',money(data.kpis.commissions,data.base_currency),'优先使用 Flex 基础货币汇总；明细保留原币')}${kpi('基础货币利息',money(data.kpis.interest,data.base_currency),'含其他币种折算；明细保留原币')}</div><div class="grid lower-grid"><article class="card section-card">${cardHead('存款和取款',`${data.cashflows.length} 条 · 跨账单去重`)}${table(['结算日期','币种','描述','金额'],eventRows(data.cashflows),'没有资金流水')}</article><article class="card section-card">${cardHead('股息',`${data.dividends_detail.length} 条`)}${table(['日期','币种','说明','金额'],eventRows(data.dividends_detail),'没有股息记录')}</article><article class="card section-card">${cardHead('代扣税',`${data.tax_detail.length} 条`)}${table(convertedHeaders,eventRows(data.tax_detail,true),'没有代扣税记录')}</article><article class="card section-card">${cardHead('交易税费',`${data.trading_taxes_detail.length} 条`)}${table(convertedHeaders,eventRows(data.trading_taxes_detail,true),'没有交易税费记录')}</article><article class="card section-card">${cardHead('手续费',`${data.commissions_detail.length} 条佣金`)}${table(convertedHeaders,eventRows(data.commissions_detail,true),'没有手续费记录')}</article><article class="card section-card">${cardHead('利息',`${data.interest_detail.length} 条 · 不含汇总行`)}${table(['日期','币种','说明','金额'],eventRows(data.interest_detail),'没有利息记录')}</article></div>`;
}
function ledgerRangeStart(key,end){
 if(key==='all')return data.range.min;
 const day=new Date(`${end}T00:00:00Z`);
 if(key==='week')day.setUTCDate(day.getUTCDate()-(day.getUTCDay()+6)%7);
 else if(key==='month')day.setUTCDate(1);
 else if(key==='year'){day.setUTCMonth(0);day.setUTCDate(1)}
 else if(key==='recentWeek')day.setUTCDate(day.getUTCDate()-6);
 else {const originalDay=day.getUTCDate();day.setUTCDate(1);day.setUTCMonth(day.getUTCMonth()-(key==='recentMonth'?1:12));const last=new Date(Date.UTC(day.getUTCFullYear(),day.getUTCMonth()+1,0)).getUTCDate();day.setUTCDate(Math.min(originalDay,last))}
 return [day.toISOString().slice(0,10),data.range.min].sort().pop();
}
function ledgerRangeToolbar(){
 const options=[['week','本周'],['month','本月'],['year','本年'],['recentWeek','近一周'],['recentMonth','近一月'],['recentYear','近一年'],['all','全部']];
 const latest=data.range.max,active=options.find(([key])=>data.range.end===latest&&data.range.start===ledgerRangeStart(key,latest))?.[0];
 const buttons=options.map(([key,label])=>`<button data-activity-range="${key}" class="${active===key?'active':''}" aria-pressed="${active===key}">${label}</button>`).join('');
 return `<div class="ledger-range-toolbar"><span>查看 ${esc(data.range.start)} 至 ${esc(data.range.end)}</span><div class="pnl-period-switch trade-ranges" aria-label="资金流水时间范围">${buttons}${!active?'<span class="custom-range-label">自定义</span>':''}</div></div>`;
}
function trades(){
 const options=[['week','本周'],['month','本月'],['year','本年'],['recentWeek','近一周'],['recentMonth','近一月'],['recentYear','近一年'],['all','全部']];
 const latest=data.range.max,active=options.find(([key])=>data.range.end===latest&&data.range.start===ledgerRangeStart(key,latest))?.[0];
 const ranges=options.map(([key,label])=>`<button data-trade-range="${key}" class="${active===key?'active':''}" aria-pressed="${active===key}">${label}</button>`).join('');
 const charge=(amount,currency)=>amount==null?'<span class="muted-value">—</span>':signed(amount,currency);
 const rows=data.trades.map((t,index)=>{const digits=String(t.time||'').split(';')[1]||'',time=digits.length===6?`${digits.slice(0,2)}:${digits.slice(2,4)}:${digits.slice(4)}`:'';
  const symbol=t.fill_count>1?`<button class="trade-expand-button" data-trade-expand-row="${index}" aria-expanded="false" aria-controls="trade-detail-${index}">${esc(t.symbol||'—')} <span class="expand-indicator">›</span><span class="secondary">${t.fill_count} 笔成交 · 点击展开</span></button>`:esc(t.symbol||'—');
  const main=`<tr><td class="mono">${esc(t.date)}${time?`<span class="secondary">${esc(time)}</span>`:''}</td><td class="symbol-cell">${symbol}</td><td>${esc(t.asset||'—')}</td><td><span class="badge ${t.side==='BUY'?'stock':''}">${esc(t.side||'—')}</span></td><td class="mono">${new Intl.NumberFormat('en-US',{maximumFractionDigits:5}).format(t.quantity)}</td><td class="mono">${money(t.price,t.currency)}</td><td class="mono">${charge(t.commission,t.currency)}</td><td class="mono">${charge(t.trading_fees||null,t.currency)}</td><td class="mono">${signed(t.realized,t.currency)}</td></tr>`;
  if(!t.fills?.length)return main;
  const fills=t.fills.map(fill=>`<tr><td class="mono">${new Intl.NumberFormat('en-US',{maximumFractionDigits:5}).format(fill.quantity)}</td><td class="mono">${money(fill.price,fill.currency)}</td><td class="mono">${charge(fill.commission,fill.currency)}</td><td class="mono">${charge(fill.trading_fees||null,fill.currency)}</td><td class="mono">${signed(fill.realized,fill.currency)}</td></tr>`).join('');
  return main+`<tr class="trade-detail-row" id="trade-detail-${index}" hidden><td colspan="9"><div class="trade-fills"><strong>订单的 ${t.fill_count} 笔分批成交</strong>${table(['数量','成交价格','佣金 / 手续费','交易税费','已实现盈亏'],[fills])}</div></td></tr>`;
 });
 const commissionCount=data.trades.filter(t=>t.commission&&Math.abs(t.commission)>1e-9).length,taxCount=data.trades.filter(t=>t.trading_fees&&Math.abs(t.trading_fees)>1e-9).length;
 return `${heading('交易记录','按所选日期查看历史总账本中的实际成交')}<div class="grid kpis">${kpi('订单数',new Intl.NumberFormat('zh-CN').format(data.trades.length),`${data.trade_fills_count||data.trades.length} 笔分批成交 · 跨账单去重`)}${kpi('含佣金订单',new Intl.NumberFormat('zh-CN').format(commissionCount),'佣金及执行手续费 · 原币')}${kpi('含交易税费订单',new Intl.NumberFormat('zh-CN').format(taxCount),'印花税及交易征费 · 原币')}${kpi('账单文件',new Intl.NumberFormat('zh-CN').format(data.imports.length),'同账户导入记录')}</div><article class="card section-card view-grid">${cardHead('交易活动',`${data.trades.length} 个订单 · ${esc(data.range.start)} 至 ${esc(data.range.end)}`,`<div class="pnl-period-switch trade-ranges" aria-label="交易时间范围">${ranges}${!active?'<span class="custom-range-label">自定义</span>':''}</div>`)}${table(['交易日期','代码','资产类别','买卖','数量','加权成交价','佣金 / 手续费','交易税费','已实现盈亏'],rows,'所选日期没有成交记录')}<p class="pnl-note holdings-table-note">同一秒、同一标的及买卖方向的分笔成交合并显示；价格按数量加权，费用和盈亏逐笔相加，点击标的可查看原始成交。Flex 未提供订单号，因此同一秒内的独立同向订单也可能合并。金额保留成交原币。</p></article>`;
}
async function postJson(url,payload){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw Error(result.error||'请求失败');return result}
async function setupFlexView(){
 const query=$('#flexQueryId'),token=$('#flexToken'),status=$('#flexStatus');
 const configPayload=()=>({query_id:query.value,token:token.value});
 const showError=error=>{flexMessage=error.message;status.textContent=flexMessage;status.style.color='#b75148'};
 $('#flexSave').onclick=async()=>{try{const result=await postJson('/api/flex/config',configPayload());token.value='';flexMessage=`配置已保存（Query ID ${result.query_id}）。`;status.textContent=flexMessage;status.style.color=''}catch(error){showError(error)}};
 $('#flexSync').onclick=async()=>{
  const button=$('#flexSync');button.disabled=true;status.style.color='';status.textContent='正在请求 IBKR 并等待报告生成…';
  try{await postJson('/api/flex/config',configPayload());token.value='';const from=$('#flexFrom').value,to=$('#flexTo').value;const result=await postJson('/api/flex/sync',{from,to});flexMessage=`查询完成：${result.import.duplicate?'文件已存在，跳过重复导入':`已导入 ${result.import.rows} 条记录`}。`;await refreshCurrentRange()}catch(error){showError(error)}finally{button.disabled=false}
 };
 $('#flexUpdate').onclick=async()=>{
  const button=$('#flexUpdate');button.disabled=true;status.style.color='';status.textContent='正在读取账本最后日期，并查询到昨天…';
  try{await postJson('/api/flex/config',configPayload());token.value='';const result=await postJson('/api/flex/update',{account:data.account});flexMessage=result.updated?`已更新 ${result.from} 至 ${result.to}：${result.import.duplicate?'报告已存在，跳过重复导入':`导入 ${result.import.rows} 条记录`}。${result.unavailable_dates?.length?'较新日期的报告暂不可用，已自动回退查询。':''}`:result.message;status.textContent=flexMessage;if(result.updated)await refreshCurrentRange()}catch(error){showError(error)}finally{button.disabled=false}
 };
 try{const response=await fetch('/api/flex/config'),config=await response.json();if(!query.value)query.value=config.query_id||'';if(config.configured&&!flexMessage)status.textContent=`已配置 Query ID ${config.query_id}；令牌已保存。`}catch(error){showError(new Error('无法读取本地 Flex 配置。'))}
}
function flexDefaultStart(){
 const lastDay=data.range?.max;if(!lastDay)return '2025-03-18';
 const next=new Date(lastDay+'T00:00:00Z');next.setUTCDate(next.getUTCDate()+1);return next.toISOString().slice(0,10);
}
function flexView(){
 const today=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
 return `${heading('IBKR Flex API','连接 Flex Query，按日期获取并导入账单',false)}<article class="card section-card flex-card"><div class="card-head"><div><div class="card-title">Flex Web Service 配置</div><div class="card-subtitle">令牌保存在本机 data/flex_config.json，不会回显到页面。</div></div></div><div class="flex-form"><label>Flex Query ID<input id="flexQueryId" inputmode="numeric" placeholder="例如 123456" /></label><label>访问令牌<input id="flexToken" type="password" autocomplete="new-password" placeholder="更新令牌时填写；留空保留已保存令牌" /></label><div class="flex-dates"><label>开始日期<input id="flexFrom" type="date" value="${flexDefaultStart()}" /></label><label>结束日期<input id="flexTo" type="date" value="${today}" /></label></div><div class="flex-actions"><button class="range-apply" id="flexSave">保存配置</button><button class="primary-button" id="flexSync">按日期查询并导入</button><button class="range-apply" id="flexUpdate">增量更新至昨天</button></div><div class="upload-status" id="flexStatus">${esc(flexMessage)}</div><p class="import-note">增量更新会从当前账户账本最后一个报告日期的后一天查询到昨天；若报告暂不可用，会逐日回退结束日期，退到已导入日期时提示无需更新。查询导入或增量更新完成后均保留当前查看区间；查询日期不会自动应用为筛选条件。在 IBKR Client Portal 的 Reporting → Flex Queries 中启用 Flex Web Service，并使用 Activity Flex Query 的 Query ID。报告保存在本机 data/flex_reports。</p></div></article>`;
}
function importsBanner(){if(data.empty)return `<div class="empty-banner"><div><strong>从第一份 IBKR 活动账单开始</strong><p>上传 CSV 后会建立本地投资组合账簿；之后上传新账单即可自动合并。</p></div><button class="primary-button" id="emptyUpload">＋ 导入 CSV</button></div>`;return ''}
async function applyDateRange(start,end){
 start.setCustomValidity('');end.setCustomValidity('');
 if(!start.value)start.setCustomValidity('请选择开始日期');
 if(!end.value)end.setCustomValidity('请选择结束日期');
 if(start.value&&end.value&&start.value>end.value)end.setCustomValidity('结束日期不能早于开始日期');
 if(!start.reportValidity()||!end.reportValidity())return;
 const buttons=document.querySelectorAll('#wealthRangeForm button,#applyRange,[data-wealth-range]');buttons.forEach(b=>b.disabled=true);
 try{await load(data.account,start.value,end.value)}catch(error){showToast(error.message)}finally{buttons.forEach(b=>b.disabled=false)}
}
async function refreshCurrentRange(){
 const account=data.account,from=data.range?.start,to=data.range?.end;
 const draftFrom=$('#rangeStart').value,draftTo=$('#rangeEnd').value;
 await load(account,from,to);
 $('#rangeStart').value=draftFrom;$('#rangeEnd').value=draftTo;
}
async function load(account,dateFrom,dateTo){const params=new URLSearchParams();if(account)params.set('account',account);if(dateFrom)params.set('from',dateFrom);if(dateTo)params.set('to',dateTo);const query=params.toString();const res=await fetch('/api/summary'+(query?'?'+query:''));const next=await res.json();if(!res.ok||next.error)throw Error(next.error||'读取账本失败，请重试');data=next;render()}
function render(){const select=$('#accountSelect'),old=select.value;select.innerHTML=(data.accounts||[]).map(a=>`<option value="${esc(a)}">${esc(a)}</option>`).join('');if(old&&(data.accounts||[]).includes(old))select.value=old;select.style.display=data.accounts?.length?'':'none';const start=$('#rangeStart'),end=$('#rangeEnd');start.min=data.range?.min||'';start.max=data.range?.max||'';start.value=data.range?.start||'';end.min=data.range?.min||'';end.max=data.range?.max||'';end.value=data.range?.end||'';$('#applyRange').disabled=!data.range?.min;$('#updatedLabel').textContent=data.nav_as_of?`净值日期 · ${data.nav_as_of}`:data.empty?'等待导入账单':'所选日期没有账单快照';const pages={overview,holdings,performance,activity,trades,flex:flexView};$('#content').innerHTML=importsBanner()+(data.empty?`<article class="card"><div class="empty-state"><strong>尚未导入账单</strong>在右上角选择 IBKR CSV 文件即可开始。<br/>支持同一账户的多份连续或重叠活动账单。</div></article>`:pages[view]());document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>{view=b.dataset.goto;render()});document.querySelectorAll('[data-pnl-period]').forEach(b=>b.onclick=()=>{pnlPeriod=b.dataset.pnlPeriod;calendarMode=pnlPeriod;render()});document.querySelectorAll('[data-pnl-sort]').forEach(b=>b.onclick=()=>{const key=b.dataset.pnlSort;if(pnlSortKey===key)pnlSortDesc=!pnlSortDesc;else{pnlSortKey=key;pnlSortDesc=key!=='underlying'}render()});document.querySelectorAll('[data-pnl-expand-row]').forEach(row=>row.onclick=()=>{const detail=document.querySelector(`[data-pnl-detail="${row.dataset.pnlExpandRow}"]`),button=row.querySelector('.pnl-expand-button'),expanded=button.getAttribute('aria-expanded')!=='true';if(detail)detail.hidden=!expanded;button.setAttribute('aria-expanded',String(expanded));button.querySelector('.expand-indicator').textContent=expanded?'⌄':'›';row.classList.toggle('expanded',expanded)});setupCalendar();document.querySelectorAll('[data-wealth-mode]').forEach(b=>b.onclick=()=>{wealthMode=b.dataset.wealthMode;render()});document.querySelectorAll('[data-wealth-range]').forEach(b=>b.onclick=async()=>{document.querySelectorAll('[data-wealth-range]').forEach(x=>x.disabled=true);try{await load(data.account,chartRangeStart(b.dataset.wealthRange,data.range.max),data.range.max)}catch(e){showToast(e.message);render()}});setupWealthCursor();const rangeForm=$('#wealthRangeForm');if(rangeForm){rangeForm.onsubmit=e=>{e.preventDefault();applyDateRange($('#wealthRangeStart'),$('#wealthRangeEnd'))};rangeForm.querySelectorAll('input').forEach(input=>input.oninput=()=>rangeForm.querySelectorAll('input').forEach(x=>x.setCustomValidity('')))}const empty=$('#emptyUpload');if(empty)empty.onclick=()=>$('#uploadDialog').showModal();if(view==='flex')setupFlexView();if(view==='performance'&&!data.empty)ensureSecurityScope()}
document.addEventListener('click',event=>{const button=event.target.closest('[data-pnl-display]');if(button){pnlDisplay=button.dataset.pnlDisplay;render();return}const tile=event.target.closest('[data-pnl-heat-open]');if(tile){const index=tile.dataset.pnlHeatOpen;pnlDisplay='list';render();const row=document.querySelector(`[data-pnl-expand-row="${index}"]`);row?.click();row?.scrollIntoView({block:'nearest',behavior:'smooth'})}});
document.addEventListener('click',async event=>{const button=event.target.closest('[data-trade-range]');if(!button)return;const buttons=document.querySelectorAll('[data-trade-range]');buttons.forEach(item=>item.disabled=true);try{await load(data.account,ledgerRangeStart(button.dataset.tradeRange,data.range.max),data.range.max)}catch(error){showToast(error.message);buttons.forEach(item=>item.disabled=false)}});
document.addEventListener('click',async event=>{const button=event.target.closest('[data-activity-range]');if(!button)return;const buttons=document.querySelectorAll('[data-activity-range]');buttons.forEach(item=>item.disabled=true);try{await load(data.account,ledgerRangeStart(button.dataset.activityRange,data.range.max),data.range.max)}catch(error){showToast(error.message);buttons.forEach(item=>item.disabled=false)}});
document.addEventListener('click',event=>{const button=event.target.closest('[data-trade-expand-row]');if(!button)return;const detail=document.getElementById(`trade-detail-${button.dataset.tradeExpandRow}`);if(!detail)return;const expanded=button.getAttribute('aria-expanded')!=='true';button.setAttribute('aria-expanded',String(expanded));button.querySelector('.expand-indicator').textContent=expanded?'⌄':'›';detail.hidden=!expanded});
document.querySelectorAll('.nav-item').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.view;render()}));$('#accountSelect').addEventListener('change',e=>load(e.target.value));$('#applyRange').addEventListener('click',()=>applyDateRange($('#rangeStart'),$('#rangeEnd')));['#rangeStart','#rangeEnd'].forEach(id=>$(id).addEventListener('input',()=>{$('#rangeStart').setCustomValidity('');$('#rangeEnd').setCustomValidity('')}));$('#uploadOpen').addEventListener('click',()=>$('#uploadDialog').showModal());
const input=$('#fileInput'),drop=$('#dropZone');input.addEventListener('change',()=>$('#fileList').textContent=[...input.files].map(f=>f.name).join(' · '));['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('dragging')}));['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('dragging')}));drop.addEventListener('drop',e=>{if(e.dataTransfer.files.length){input.files=e.dataTransfer.files;$('#fileList').textContent=[...input.files].map(f=>f.name).join(' · ')}});
$('#uploadForm').addEventListener('submit',async e=>{e.preventDefault();if(!input.files.length)return;const status=$('#uploadStatus'),button=$('#uploadSubmit');button.disabled=true;status.textContent='正在解析并导入…';let count=0,duplicates=0;try{for(const file of input.files){const form=new FormData();form.append('file',file);const res=await fetch('/api/import',{method:'POST',body:form});const result=await res.json();if(!res.ok)throw Error(`${file.name}：${result.error||'导入失败'}`);count+=result.duplicate?0:1;duplicates+=result.duplicate?1:0}await load($('#accountSelect').value||undefined);$('#uploadDialog').close();$('#uploadForm').reset();$('#fileList').textContent='';status.textContent='';showToast(`导入完成：新增 ${count} 份，重复跳过 ${duplicates} 份`)}catch(err){status.textContent=err.message;status.style.color='#b75148'}finally{button.disabled=false}});
$('#clearImports').addEventListener('click',async()=>{if(!window.confirm('确定清空所有账户的已导入账单和账本记录？此操作无法撤销。Flex 配置和已下载 CSV 文件会保留。'))return;const button=$('#clearImports');button.disabled=true;button.textContent='正在清空…';try{const result=await postJson('/api/imports/clear',{confirm:true});$('#uploadDialog').close();await load();showToast(`已清空 ${result.cleared} 份账单；现在可以重新导入`)}catch(error){button.textContent='清空已导入数据';window.alert(error.message)}finally{button.disabled=false;button.textContent='清空已导入数据'}});
load().catch(e=>{console.error(e);$('#content').innerHTML=`<div class="empty-state"><strong>无法读取本地账簿</strong>${esc(e.message)}</div>`});
