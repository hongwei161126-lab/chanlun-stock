// 缠论股票APP 前端逻辑
const API = '';
const TOKEN_KEY = 'clstock_token_v1';
let curSymbol = null, curName = null, curLevel = 'daily';
let chartData = null, showZS = true, showBI = false;
let refreshTimer = null;  // 实时刷新定时器
let g_auth_state = { password_set: false, logged_in: false };

// ============ 鉴权工具 ============
function getToken(){ return localStorage.getItem(TOKEN_KEY) || ''; }
function setToken(t){
  if(t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}
function showLogin(){
  // 显示登录/设密码遮罩
  document.getElementById('authOverlay').classList.add('show');
  document.getElementById('authForm').innerHTML = '<div style="text-align:center;color:var(--txt2);padding:10px">加载中...</div>';
  _render_auth();
}
function hideLogin(){
  document.getElementById('authOverlay').classList.remove('show');
}
async function _render_auth(){
  const s = await fetch(API+'/api/auth/state').then(r=>r.json()).catch(()=>({password_set:false,logged_in:false}));
  g_auth_state = s;
  const el = document.getElementById('authForm');
  if(!s.password_set){
    // 首次使用：设置密码
    el.innerHTML = `
    <div class="auth-title">
      <div class="auth-logo"><span class="dot"></span>缠论股票</div>
      <div class="auth-sub">首次使用，请设置访问密码（至少4位）</div>
    </div>
    <div class="auth-row">
      <label>设置密码</label>
      <input type="password" id="setPwd1" placeholder="至少4位字符" autocomplete="new-password">
    </div>
    <div class="auth-row">
      <label>确认密码</label>
      <input type="password" id="setPwd2" placeholder="再输一次">
    </div>
    <button class="btn success auth-btn" onclick="doSetPwd()">设置密码并进入</button>`;
    setTimeout(()=>{ const p=document.getElementById('setPwd1'); if(p) p.focus(); },50);
  }else if(!s.logged_in){
    // 已设密码，需登录
    el.innerHTML = `
    <div class="auth-title">
      <div class="auth-logo"><span class="dot"></span>缠论股票</div>
      <div class="auth-sub">请输入访问密码登录（7天免登录）</div>
    </div>
    <div class="auth-row">
      <label>访问密码</label>
      <input type="password" id="loginPwd" placeholder="请输入密码" autocomplete="current-password">
    </div>
    <button class="btn success auth-btn" onclick="doLogin()">登录</button>
    <div class="auth-tip"><a href="javascript:doShowChangePwd()" style="color:var(--accent)">我忘记密码 / 修改密码</a></div>`;
    setTimeout(()=>{ const p=document.getElementById('loginPwd'); if(p){ p.focus(); p.onkeydown=e=>{ if(e.key==='Enter') doLogin(); }; } },50);
  }else{
    // 已登录，隐藏
    hideLogin();
  }
}
async function doSetPwd(){
  const p1 = document.getElementById('setPwd1').value;
  const p2 = document.getElementById('setPwd2').value;
  if(!p1 || p1.length<4){ toast('密码至少4位'); return; }
  if(p1 !== p2){ toast('两次密码不一致'); return; }
  const r = await fetch(API+'/api/auth/set-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p1})}).then(r=>r.json());
  if(!r || !r.ok){ toast(r?.msg||'设置失败'); return; }
  // 设完密码自动登录
  const lr = await fetch(API+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p1})}).then(r=>r.json());
  if(lr && lr.ok){ setToken(lr.token); toast('设置成功，已登录'); hideLogin(); bootApp(); return; }
  toast(r.msg || '设置成功，请登录'); _render_auth();
}
async function doLogin(){
  const p = document.getElementById('loginPwd').value;
  if(!p){ toast('请输入密码'); return; }
  const r = await fetch(API+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p})}).then(r=>r.json());
  if(!r || !r.ok){ toast(r?.msg||'登录失败'); return; }
  setToken(r.token);
  toast('登录成功，7天免登录'); hideLogin(); bootApp();
}
function doShowChangePwd(){
  const el = document.getElementById('authForm');
  el.innerHTML = `
  <div class="auth-title">
    <div class="auth-logo"><span class="dot"></span>缠论股票</div>
    <div class="auth-sub">重置密码：请输入旧密码+新密码<br>（如果忘记旧密码，可SSH登录数据库清空settings表中auth开头的key）</div>
  </div>
  <div class="auth-row"><label>旧密码</label><input type="password" id="chgOld"></div>
  <div class="auth-row"><label>新密码</label><input type="password" id="chgP1"></div>
  <div class="auth-row"><label>确认新密码</label><input type="password" id="chgP2"></div>
  <button class="btn success auth-btn" onclick="doChangePwd()">提交修改</button>
  <div class="auth-tip"><a href="javascript:_render_auth()" style="color:var(--accent)">← 返回登录</a></div>`;
}
async function doChangePwd(){
  const old = document.getElementById('chgOld').value;
  const p1 = document.getElementById('chgP1').value;
  const p2 = document.getElementById('chgP2').value;
  if(p1 && p1.length<4){ toast('新密码至少4位'); return; }
  if(p1 !== p2){ toast('两次新密码不一致'); return; }
  const r = await fetch(API+'/api/auth/set-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old_password:old,password:p1})}).then(r=>r.json());
  if(!r || !r.ok){ toast(r?.msg||'修改失败'); return; }
  toast('密码已修改，请用新密码重新登录');
  setToken(''); // 旧token自动失效
  setTimeout(_render_auth, 800);
}
async function doLogout(){
  try{ await fetch(API+'/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+getToken()}}); }catch(e){}
  setToken('');
  toast('已退出登录');
  showLogin();
}

// ============ 工具函数 ============
function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2000);
}
async function api(path, opt){
  opt = opt || {};
  opt.headers = Object.assign({'Content-Type':'application/json'}, opt.headers||{});
  const tok = getToken();
  if(tok) opt.headers['Authorization'] = 'Bearer ' + tok;
  try{
    const r = await fetch(API+path, opt);
    if(r.status === 401){
      try{ const d = await r.json(); if(d && d.need_login){ setToken(''); showLogin(); return null; } }catch(e){}
      setToken(''); showLogin(); return null;
    }
    const d = await r.json();
    if(d.error){ toast(d.error); return null; }
    return d;
  }catch(e){ toast('请求失败: '+e.message); return null; }
}
function fmt(n, d=2){ return Number(n).toFixed(d); }
function chgCls(c){ return c>=0?'up':'down'; }
function chgTxt(c){ return (c>=0?'+':'')+fmt(c)+'%'; }
function chgBadge(c){ return `<span class="chg-badge ${chgCls(c)}">${chgTxt(c)}</span>`; }
// 空状态SVG图标
const EMPTY_ICONS = {
  watch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/></svg>',
  pos: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7h18v13H3z"/><path d="M3 7l2-3h14l2 3"/></svg>',
  trade: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>',
};

// ============ Tab切换 ============
const titles = {market:'行情', detail:'分析', positions:'持仓', mine:'我的'};
function switchTab(page){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.getElementById('page-'+page).classList.add('active');
  document.getElementById('pageTitle').textContent = titles[page];
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.page===page));
  // 实时刷新仅在行情页开启
  if(refreshTimer){ clearInterval(refreshTimer); refreshTimer=null; }
  if(detailTimer){ clearInterval(detailTimer); detailTimer=null; }
  if(page==='market'){
    loadMarket();
    refreshTimer = setInterval(loadIndexRealtime, 5000);  // 5秒刷新大盘
  }
  if(page==='positions') loadPositions();
  if(page==='mine') loadMine();
}

// ============ 大盘实时看板 ============
async function loadIndexRealtime(){
  const d = await api('/api/index');
  if(!d || !d.length) return;
  const now = new Date();
  const tStr = now.toLocaleTimeString('zh-CN',{hour12:false});
  let html = `<div class="refresh-bar"><span class="live"><span class="dot"></span>LIVE ${tStr}</span><span>大盘指数（点击查看详情）</span></div>`;
  html += '<div class="index-board">';
  d.forEach(idx=>{
    const cls = idx.change_pct>=0?'up':'down';
    const idxCode = idx.code || (idx.prefix||'').replace(/^(sh|sz)/,'');
    html += `<div class="index-card clickable" onclick="openIndexDetail('${idxCode}','${idx.name}')">
      <div class="nm">${idx.name}</div>
      <div class="pr ${cls}">${idx.price}</div>
      <div class="cg ${cls}">${idx.change>=0?'+':''}${idx.change} (${idx.change_pct>=0?'+':''}${idx.change_pct}%)</div>
    </div>`;
  });
  html += '</div>';
  document.getElementById('indexBoard').innerHTML = html;
}

// ============ 行情页 ============
async function loadMarket(){
  loadIndexRealtime(); loadWatchlist(); loadPool(); checkScanStatus();
}
async function loadWatchlist(){
  const d = await api('/api/watchlist');
  const el = document.getElementById('watchList');
  if(!d || !d.length){
    el.innerHTML = `<div class="empty"><div class="ic">${EMPTY_ICONS.watch}</div>暂无自选<br>输入代码添加</div>`;
    document.getElementById('watchCount').textContent='';
    return;
  }
  document.getElementById('watchCount').textContent = d.length+'只';
  const codes = d.map(s=>{
    const pre = s.symbol.match(/^[695]/)?'sh':'sz';
    return pre+s.symbol;
  });
  const rt = await api('/api/realtime?codes='+codes.join(','));
  const map = {};
  if(rt) rt.forEach(r=>{ map[r.code] = r; });
  el.innerHTML = d.map(s=>{
    const r = map[s.symbol] || {};
    const price = r.price || s.price || 0;
    const chg = r.change_pct!=null?r.change_pct:(s.change_pct||0);
    return `<div class="quote-item" onclick="openDetail('${s.symbol}','${s.name}')">
      <div class="info"><div class="name">${s.name}</div><div class="sym">${s.symbol}</div></div>
      <div class="price ${chgCls(chg)}">${fmt(price)}</div>
      ${chgBadge(chg)}
    </div>`;
  }).join('');
}
async function loadPool(){
  const d = await api('/api/pool');
  const el = document.getElementById('poolList');
  if(!d) return;
  // /api/pool 已带实时行情，无需再单独请求
  el.innerHTML = d.map(s=>{
    const price = s.price || 0;
    const chg = s.change_pct||0;
    // 有买点类型则显示标签
    const bpTag = (s.buy_types && s.buy_types.length)
      ? `<span class="bp-tag">${s.buy_types.map(t=>t.replace('类买点','买')).join('/')}</span>`
      : '';
    // 评分标签（按分数分色）
    const sc = s.score||0;
    const scCls = sc>=80?'hi':(sc>=65?'md':'lo');
    const scTag = sc>0 ? `<span class="sc-tag ${scCls}">${sc}分</span>` : '';
    // 评分明细（小字）
    const scDetail = s.score_detail ? `<div class="sc-detail">${s.score_detail}</div>` : '';
    return `<div class="quote-item" onclick="openDetail('${s.symbol}','${s.name}')">
      <div class="info">
        <div class="name">${s.name} ${bpTag} ${scTag}</div>
        <div class="sym">${s.symbol}</div>
        ${scDetail}
      </div>
      <div class="price ${chgCls(chg)}">${fmt(price)}</div>
      ${chgBadge(chg)}
    </div>`;
  }).join('');
}
// ============ 全市场扫描 ============
let scanTimer = null;
async function checkScanStatus(){
  // 页面加载时检查是否正在扫描，若有则恢复进度显示
  const s = await api('/api/scan/status');
  if(!s) return;
  if(s.status === 'scanning'){
    document.getElementById('scanProgress').style.display='block';
    document.querySelector('.scan-btn').style.display='none';
    pollScan();
  } else if(s.cached_hits > 0){
    // 有缓存结果，更新按钮文字
    const btn = document.querySelector('.scan-btn');
    if(btn){ btn.textContent = `重新扫描（上次命中${s.cached_hits}只）`; }
  }
}
async function startScan(){
  const r = await api('/api/scan/start',{method:'POST'});
  if(!r) return;
  if(!r.ok){ toast(r.msg||'扫描进行中'); }
  document.getElementById('scanProgress').style.display='block';
  document.querySelector('.scan-btn').style.display='none';
  pollScan();
}
async function pollScan(){
  const s = await api('/api/scan/status');
  if(!s) return;
  const pct = s.total>0 ? Math.round(s.scanned/s.total*100) : 0;
  document.getElementById('spFill').style.width = pct+'%';
  document.getElementById('spTxt').textContent = s.msg || `${s.scanned}/${s.total}`;
  if(s.status === 'scanning'){
    scanTimer = setTimeout(pollScan, 1500);
  } else {
    // 扫描完成或出错
    if(s.status === 'done'){
      toast(`扫描完成，命中 ${s.cached_hits} 只`);
    }
    document.querySelector('.scan-btn').style.display='block';
    document.querySelector('.scan-btn').textContent = '重新扫描';
    document.getElementById('scanProgress').style.display='none';
    loadPool();
  }
}
async function addWatch(){
  const inp = document.getElementById('searchInput');
  const sym = inp.value.trim();
  if(!sym){ toast('请输入代码'); return; }
  const r = await api('/api/watchlist', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({symbol:sym})});
  if(r && r.ok){ inp.value=''; toast('已添加'); loadWatchlist(); }
}

// ============ 详情/分析页 ============
let detailTimer = null;
async function openDetail(symbol, name){
  curSymbol = symbol; curName = name; curLevel = 'daily';
  document.getElementById('levelSeg').querySelectorAll('span').forEach(s=>s.classList.toggle('on', s.dataset.lv==='daily'));
  switchTab('detail');
  document.getElementById('pageTitle').textContent = name;
  if(detailTimer){ clearInterval(detailTimer); detailTimer=null; }
  await loadQuote();
  await loadAnalyze(); await loadRecommend();
  detailTimer = setInterval(loadQuote, 5000);
}
async function loadQuote(){
  const d = await api(`/api/quote/${curSymbol}`);
  if(!d) return;
  document.getElementById('dName').innerHTML = `${d.name}<span class="sym">${d.symbol}</span>`;
  document.getElementById('dPrice').textContent = fmt(d.price);
  document.getElementById('dPrice').className = 'pr '+chgCls(d.change_pct);
  const tStr = d.time ? `${d.time.slice(8,10)}:${d.time.slice(10,12)}` : '';
  document.getElementById('dChg').innerHTML = `
    <span class="tag ${chgCls(d.change_pct)}" style="background:${d.change_pct>=0?'var(--up-bg)':'var(--down-bg)'}">${d.change>=0?'+':''}${d.change} ${chgTxt(d.change_pct)}</span>
    <span>开 ${fmt(d.open)}</span><span>高 ${fmt(d.high)}</span><span>低 ${fmt(d.low)}</span>${tStr?`<span>${tStr}</span>`:''}`;
}
async function loadAnalyze(){
  if(curLevel === 'minute'){
    // 分时图模式
    const d = await api(`/api/minute/${curSymbol}`);
    if(!d) return;
    chartData = null;  // 清除K线数据
    drawMinute(d);
    // 分时模式下简化分析信息
    const last = d.points[d.points.length-1];
    const chg = (last.price - d.prev_close) / d.prev_close * 100;
    let html = `<div class="analysis-row">`;
    html += `<div class="item"><span class="k">日期</span><span class="v">${d.date}</span></div>`;
    html += `<div class="item"><span class="k">昨收</span><span class="v">${d.prev_close}</span></div>`;
    html += `<div class="item"><span class="k">最新</span><span class="v ${chgCls(chg)}">${last.price}</span></div>`;
    html += `<div class="item"><span class="k">涨跌</span><span class="v ${chgCls(chg)}">${chg>=0?'+':''}${chg.toFixed(2)}%</span></div>`;
    html += `</div>`;
    html += `<div class="analysis-row"><div class="item"><span class="k">均价</span><span class="v">${last.avg_price}</span></div>`;
    const amt = last.amount;
    html += `<div class="item"><span class="k">成交额</span><span class="v">${amt>=1e8?(amt/1e8).toFixed(2)+'亿':(amt/1e4).toFixed(0)+'万'}</span></div></div>`;
    document.getElementById('analysisInfo').innerHTML = html;
    return;
  }
  const d = await api(`/api/analyze/${curSymbol}?level=${curLevel}&count=120`);
  if(!d) return;
  chartData = d;
  drawKline();
  let html = `<div class="analysis-row">`;
  html += `<div class="item"><span class="k">现价</span><span class="v">${fmt(d.last_price)}</span></div>`;
  html += `<div class="item"><span class="k">中枢</span><span class="v">${d.zhongshu.length}</span></div>`;
  html += `<div class="item"><span class="k">背驰</span><span class="v ${d.divergence?'up':''}">${d.divergence?'是':'否'}</span></div>`;
  html += `</div>`;
  if(d.zhongshu.length){
    const z = d.zhongshu[d.zhongshu.length-1];
    html += `<div class="analysis-row"><div class="item"><span class="k">最近中枢</span><span class="v">ZG ${fmt(z.ZG)} / ZD ${fmt(z.ZD)}</span></div></div>`;
  }
  if(d.buy_points && d.buy_points.length){
    html += `<div class="analysis-row"><span class="signal-tag buy">买点: ${d.buy_points.map(b=>b.type).join(', ')}</span></div>`;
  }else{
    html += `<div class="analysis-row"><span class="signal-tag none">暂无买点信号</span></div>`;
  }
  html += `</div>`;
  document.getElementById('analysisInfo').innerHTML = html;
}
async function loadRecommend(){
  const d = await api(`/api/recommend/${curSymbol}?level=daily`);
  if(!d) return;
  let html = '';
  if(d.buy_points && d.buy_points.length){
    d.buy_points.forEach(bp=>{
      html += `<div class="rec-card">
        <span class="tag">${bp.buy_type}</span>
        <div class="detail-txt">${bp.detail}</div>
        <div class="price-grid">
          <div class="cell"><div class="lb">买入</div><div class="vl down">${fmt(bp.buy)}</div></div>
          <div class="cell"><div class="lb">止盈</div><div class="vl up">${fmt(bp.take_profit)}</div><div class="sub up">+${fmt(bp.take_profit_pct)}%</div></div>
          <div class="cell"><div class="lb">止损</div><div class="vl" style="color:var(--gold)">${fmt(bp.stop_loss)}</div><div class="sub" style="color:var(--gold)">-${fmt(bp.stop_loss_pct)}%</div></div>
        </div>
        <div class="rec-logic"><div class="logic-item"><span class="logic-k">止损依据</span><span class="logic-v">${bp.stop_logic}</span></div></div>
        <div class="rec-logic"><div class="logic-item"><span class="logic-k">止盈依据</span><span class="logic-v">${bp.take_logic}</span></div></div>
        <div class="rec-meta"><span>目标2 ${fmt(bp.target2)}</span><span>盈亏比 ${bp.risk_reward}</span></div>
        <div style="margin-top:10px;text-align:center"><button class="btn success sm" onclick="quickBuy(${bp.buy},${bp.stop_loss},${bp.take_profit},'${bp.buy_type}')">按推荐买入</button></div>
      </div>`;
    });
  }
  if(d.sell_points && d.sell_points.length){
    d.sell_points.forEach(sp=>{
      html += `<div class="rec-card sell">
        <span class="tag">${sp.type}</span>
        <div class="detail-txt">${sp.detail} @ ${fmt(sp.price)}</div>
      </div>`;
    });
  }
  if(!html) html = `<div class="empty"><div class="ic">${EMPTY_ICONS.search}</div>当前无买卖点信号</div>`;
  document.getElementById('detailRec').innerHTML = html;
  document.getElementById('tradePanel').innerHTML = `
    <div class="trade-panel">
      <div class="tp-title">手动交易</div>
      <div class="row"><label>价格</label><input type="number" id="tradePrice" value="${fmt(d.last_price)}" step="0.01"></div>
      <div class="row"><label>数量</label><input type="number" id="tradeShares" value="100" step="100"></div>
      <div class="acts">
        <button class="btn success" onclick="manualTrade('buy')">买入</button>
        <button class="btn danger" onclick="manualTrade('sell')">卖出</button>
      </div>
    </div>`;
}

// ============ 分时图绘制 ============
function drawMinute(d){
  const cv = document.getElementById('kcanvas');
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 320;
  cv.width = w*dpr; cv.height = h*dpr;
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0,0,w,h);

  const pts = d.points;
  if(!pts.length) return;
  const prev = d.prev_close;
  // 价格区间：以昨收为中心，对称取最大偏离
  let maxDev = 0;
  pts.forEach(p=>{
    maxDev = Math.max(maxDev, Math.abs(p.price - prev), Math.abs(p.avg_price - prev));
  });
  maxDev = maxDev * 1.1 || prev * 0.01;
  const pmin = prev - maxDev, pmax = prev + maxDev;
  const top=12, bot=28, left=6, right=42;
  const ph = h-top-bot;
  const n = pts.length;
  const xStep = (w-left-right)/(n-1);
  const y = p => top + (pmax-p)/(pmax-pmin)*ph;

  // 网格 + 价格刻度
  ctx.strokeStyle = '#222c3d'; ctx.lineWidth=1;
  ctx.font='10px "SF Mono",monospace'; ctx.fillStyle='#4a5668';
  for(let i=0;i<=4;i++){
    const yy = top + ph*i/4;
    ctx.beginPath(); ctx.moveTo(left,yy); ctx.lineTo(w-right,yy); ctx.stroke();
    const price = pmax - (pmax-pmin)*i/4;
    ctx.fillText(price.toFixed(2), w-right+4, yy+3);
  }
  // 时间刻度
  const timeMarks = [0, Math.floor(n*0.25), Math.floor(n*0.5), Math.floor(n*0.75), n-1];
  timeMarks.forEach(i=>{
    if(i>=0 && i<n){
      const xx = left + i*xStep;
      ctx.fillStyle='#4a5668';
      ctx.fillText(pts[i].time, xx-14, h-8);
    }
  });

  // 昨收水平虚线
  ctx.strokeStyle = '#7d8ba1'; ctx.setLineDash([4,4]);
  ctx.beginPath(); ctx.moveTo(left, y(prev)); ctx.lineTo(w-right, y(prev)); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle='#7d8ba1'; ctx.fillText('昨收'+prev.toFixed(2), left+2, y(prev)-3);

  // 价格区域填充（红涨绿跌）
  const isUp = pts[pts.length-1].price >= prev;
  const fillColor = isUp ? 'rgba(239,68,68,0.10)' : 'rgba(34,197,94,0.10)';
  ctx.fillStyle = fillColor;
  ctx.beginPath();
  ctx.moveTo(left, y(prev));
  pts.forEach((p,i)=> ctx.lineTo(left+i*xStep, y(p.price)));
  ctx.lineTo(left+(n-1)*xStep, y(prev));
  ctx.closePath();
  ctx.fill();

  // 价格线
  ctx.strokeStyle = isUp ? '#ef4444' : '#22c55e';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  pts.forEach((p,i)=>{
    const xx = left+i*xStep, yy = y(p.price);
    if(i===0) ctx.moveTo(xx,yy); else ctx.lineTo(xx,yy);
  });
  ctx.stroke();

  // 均价线（黄色虚线）
  ctx.strokeStyle = '#f59e0b'; ctx.lineWidth=1; ctx.setLineDash([3,3]);
  ctx.beginPath();
  pts.forEach((p,i)=>{
    const xx = left+i*xStep, yy = y(p.avg_price);
    if(i===0) ctx.moveTo(xx,yy); else ctx.lineTo(xx,yy);
  });
  ctx.stroke();
  ctx.setLineDash([]);

  // 图例
  ctx.font='10px sans-serif';
  ctx.fillStyle = isUp?'#ef4444':'#22c55e'; ctx.fillText('● 价格', left+2, top+10);
  ctx.fillStyle = '#f59e0b'; ctx.fillText('┄ 均价', left+44, top+10);
}

// ============ K线图绘制 ============
function drawKline(){
  if(!chartData) return;
  const cv = document.getElementById('kcanvas');
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 300;
  cv.width = w*dpr; cv.height = h*dpr;
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0,0,w,h);

  const ks = chartData.klines;
  if(!ks.length) return;
  // 价格区间
  let pmin=Infinity, pmax=-Infinity;
  ks.forEach(k=>{ pmin=Math.min(pmin,k.low); pmax=Math.max(pmax,k.high); });
  chartData.zhongshu.forEach(z=>{ pmin=Math.min(pmin,z.ZD); pmax=Math.max(pmax,z.ZG); });
  const pad = (pmax-pmin)*0.08;
  pmin-=pad; pmax+=pad;
  const top=10, bot=30, left=4, right=4;
  const ph = h-top-bot;
  const n = ks.length;
  const cw = (w-left-right)/n;
  const y = p => top + (pmax-p)/(pmax-pmin)*ph;

  // 网格
  ctx.strokeStyle = '#222c3d'; ctx.lineWidth=1;
  ctx.font='10px "SF Mono",monospace'; ctx.fillStyle='#4a5668';
  for(let i=0;i<4;i++){
    const yy = top + ph*i/4;
    ctx.beginPath(); ctx.moveTo(left,yy); ctx.lineTo(w-right,yy); ctx.stroke();
    ctx.fillText(fmt(pmax-(pmax-pmin)*i/4), 2, yy-2);
  }

  // 中枢矩形
  if(showZS && chartData.zhongshu){
    chartData.zhongshu.forEach((z, i)=>{
      ctx.fillStyle = 'rgba(6,182,212,0.10)';
      ctx.strokeStyle = 'rgba(6,182,212,0.4)';
      ctx.fillRect(left, y(z.ZG), w-left-right, y(z.ZD)-y(z.ZG));
      ctx.strokeRect(left, y(z.ZG), w-left-right, y(z.ZD)-y(z.ZG));
      ctx.fillStyle='#06b6d4'; ctx.font='bold 10px sans-serif';
      ctx.fillText(`Z${i+1}`, left+4, y(z.ZG)+12);
    });
  }

  // 蜡烛图
  ks.forEach((k,i)=>{
    const x = left + i*cw + cw/2;
    const isUp = k.close>=k.open;
    ctx.strokeStyle = isUp?'#ef4444':'#22c55e';
    ctx.fillStyle = isUp?'#ef4444':'#22c55e';
    ctx.beginPath(); ctx.moveTo(x, y(k.high)); ctx.lineTo(x, y(k.low)); ctx.stroke();
    const bw = Math.max(1, cw*0.6);
    const yo = y(k.open), yc = y(k.close);
    ctx.fillRect(x-bw/2, Math.min(yo,yc), bw, Math.max(1,Math.abs(yc-yo)));
  });

  // 笔的趋势线
  if(showBI && chartData.bi){
    ctx.strokeStyle='#f59e0b'; ctx.lineWidth=1.5;
    ctx.beginPath();
    chartData.bi.forEach((b,i)=>{
      const x = left + (i/(chartData.bi.length))*(w-left-right);
      if(i===0) ctx.moveTo(x, y(b.start_value));
      ctx.lineTo(x, y(b.end_value));
    });
    ctx.stroke();
  }

  // 买卖点标注
  if(chartData.buy_points){
    chartData.buy_points.forEach(bp=>{
      const py = y(bp.price);
      ctx.fillStyle='#22c55e';
      ctx.beginPath(); ctx.arc(w-right-10, py, 5, 0, Math.PI*2); ctx.fill();
      ctx.fillStyle='#22c55e'; ctx.font='bold 10px sans-serif';
      ctx.fillText('买', w-right-24, py+3);
    });
  }
}

// ============ 交易 ============
function curTerm(){ return (curLevel==='daily') ? 'long' : 'short'; }
async function quickBuy(price, sl, tp, strategy){
  const shares = 100;
  const r = await api('/api/trade/buy', {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({symbol:curSymbol, price, shares, strategy, mode:'manual', stop_loss:sl, take_profit:tp, reason:'推荐买入', strategy_term:curTerm()})});
  if(r && r.ok){ toast(r.msg); }
}
async function manualTrade(act){
  const price = parseFloat(document.getElementById('tradePrice').value);
  const shares = parseInt(document.getElementById('tradeShares').value);
  if(!price || !shares){ toast('请填写价格和数量'); return; }
  const url = act==='buy'?'/api/trade/buy':'/api/trade/sell';
  const body = {symbol:curSymbol, price, shares, reason:'手动交易'};
  if(act==='buy') body.strategy_term = curTerm();
  const r = await api(url, {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  if(r && r.ok){ toast(r.msg); }
}

// ============ 持仓页 ============
async function loadPositions(){
  const acct = await api('/api/account');
  if(acct){
    document.getElementById('totalAssets').textContent = fmt(acct.total_assets, 0);
    document.getElementById('totalPnl').textContent = (acct.total_pnl>=0?'+':'')+fmt(acct.total_pnl,0);
    document.getElementById('totalPnl').className = 'vl '+chgCls(acct.total_pnl);
  }
  const pos = await api('/api/positions');
  const el = document.getElementById('posList');
  if(!pos || !pos.length){
    el.innerHTML = `<div class="empty"><div class="ic">${EMPTY_ICONS.pos}</div>暂无持仓</div>`;
    return;
  }
  el.innerHTML = pos.map(p=>{
    const buyPrice = p.buy_price!=null ? fmt(p.buy_price) : fmt(p.avg_cost);
    const curPrice = p.current_price!=null ? fmt(p.current_price) : '--';
    return `
    <div class="pos-item" onclick="openDetail('${p.symbol}','${p.name}')">
      <div class="lt">
        <div class="nm">${p.name}</div>
        <div class="sh">${p.shares}股 | 买${buyPrice} | 现${curPrice}</div>
      </div>
      <div class="rt">
        <div class="pn ${chgCls(p.float_pnl)}">${p.float_pnl>=0?'+':''}${fmt(p.float_pnl)}</div>
        <div class="pct ${chgCls(p.float_pnl_pct)}">${chgTxt(p.float_pnl_pct)}</div>
      </div>
    </div>`;
  }).join('');
}

// ============ 我的/统计页 ============
async function loadMine(){
  const s = await api('/api/stats');
  if(s){
    document.getElementById('winRate').textContent = fmt(s.win_rate,1)+'%';
    document.getElementById('tradeCount').textContent = s.total_trades;
    document.getElementById('realizedPnl').textContent = (s.realized_pnl>=0?'+':'')+fmt(s.realized_pnl);
    document.getElementById('realizedPnl').className = 'vl '+chgCls(s.realized_pnl);
    renderTermCompare(s);
  }
  const acct = await api('/api/account');
  if(acct) document.getElementById('balance').textContent = fmt(acct.balance,0);
  const st = await api('/api/settings');
  if(st){
    document.getElementById('autoMode').value = st.auto_mode;
  }
  loadNotifyConfig();
  loadAutoConfig();
  loadSchedulerJobs();
  const trades = await api('/api/trades?limit=50');
  const el = document.getElementById('tradeHistory');
  if(!trades || !trades.length){
    el.innerHTML = `<div class="empty"><div class="ic">${EMPTY_ICONS.trade}</div>暂无交易记录</div>`;
    return;
  }
  el.innerHTML = trades.map(t=>`
    <div class="trade-item">
      <div class="lt"><span class="tag-mini ${t.action}">${t.action==='buy'?'买':'卖'}</span><div><div>${t.name} <span class="lb">${t.shares}股@${fmt(t.price)}</span></div></div></div>
      <div style="text-align:right">${t.pnl?`<div class="pnl-val ${chgCls(t.pnl)}">${t.pnl>=0?'+':''}${fmt(t.pnl)}</div>`:''}<div class="lb">${t.created_at.slice(5,16)}</div></div>
    </div>`).join('');
}
async function saveSettings(){
  const data = {
    auto_mode: document.getElementById('autoMode').value,
  };
  const r = await api('/api/settings', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r && r.ok) toast('设置已保存');
}

// 渲染长期/短期策略对比（数字可点击查看明细）
function renderTermCompare(s){
  const el = document.getElementById('termCompare');
  if(!el) return;
  const L = s.long || {};
  const S = s.short || {};
  const row = (label, d, term) => {
    const wr = d.win_rate!=null ? fmt(d.win_rate,1)+'%' : '--';
    const pnl = d.realized_pnl!=null ? (d.realized_pnl>=0?'+':'')+fmt(d.realized_pnl) : '--';
    const pct = d.pnl_pct!=null ? (d.pnl_pct>=0?'+':'')+fmt(d.pnl_pct,2)+'%' : '--';
    const wrCls = d.win_rate>=50 ? 'up' : (d.total_trades>0 ? 'down' : '');
    const pnlCls = chgCls(d.realized_pnl);
    return `<div class="term-row">
      <div class="term-name">${label}</div>
      <div class="term-cells">
        <div class="term-cell clickable" onclick="openTradesDetail('${term}','')"><span class="lb">交易</span><span class="vl">${d.total_trades||0}</span></div>
        <div class="term-cell clickable" onclick="openTradesDetail('${term}','')"><span class="lb">胜率</span><span class="vl ${wrCls}">${wr}</span></div>
        <div class="term-cell clickable" onclick="openTradesDetail('${term}','sell')"><span class="lb">盈亏</span><span class="vl ${pnlCls}">${pnl}</span></div>
        <div class="term-cell clickable" onclick="openTradesDetail('${term}','sell')"><span class="lb">收益率</span><span class="vl ${pnlCls}">${pct}</span></div>
      </div>
    </div>`;
  };
  el.innerHTML = row('长期(日线)', L, 'long') + row('短期(30/60分)', S, 'short');
}

// 弹窗通用
function closeModal(id){ document.getElementById(id).classList.remove('show'); }

// 指数详情弹窗（分时图）
async function openIndexDetail(code, name){
  document.getElementById('indexModalTitle').textContent = name + ' 分时';
  document.getElementById('indexModalInfo').innerHTML = '<div style="text-align:center;color:var(--txt2);padding:20px">加载中...</div>';
  document.getElementById('indexModal').classList.add('show');
  try {
    const d = await api(`/api/minute/${code}`);
    if(!d || !d.points || !d.points.length){
      document.getElementById('indexModalInfo').innerHTML = '<div style="text-align:center;color:var(--txt2);padding:20px">暂无分时数据</div>';
      return;
    }
    // 画分时图
    drawIndexMinute(d, code);
    const last = d.points[d.points.length-1];
    const chg = (last.price - d.prev_close) / d.prev_close * 100;
    const amt = last.amount;
    document.getElementById('indexModalInfo').innerHTML = `
      <div class="analysis-row">
        <div class="item"><span class="k">昨收</span><span class="v">${d.prev_close}</span></div>
        <div class="item"><span class="k">最新</span><span class="v ${chgCls(chg)}">${last.price}</span></div>
        <div class="item"><span class="k">涨跌</span><span class="v ${chgCls(chg)}">${chg>=0?'+':''}${chg.toFixed(2)}%</span></div>
        <div class="item"><span class="k">成交额</span><span class="v">${amt>=1e8?(amt/1e8).toFixed(2)+'亿':(amt/1e4).toFixed(0)+'万'}</span></div>
      </div>`;
  } catch(e){
    document.getElementById('indexModalInfo').innerHTML = '<div style="text-align:center;color:var(--txt3);padding:20px">加载失败</div>';
  }
}
// 画指数分时图（简化版）
function drawIndexMinute(d, code){
  const cv = document.getElementById('indexCanvas');
  const ctx = cv.getContext('2d');
  const W = cv.clientWidth, H = 220;
  cv.width = W; cv.height = H;
  ctx.clearRect(0,0,W,H);
  const pts = d.points;
  const prices = pts.map(p=>p.price);
  const avgPrices = pts.map(p=>p.avg_price||p.price);
  const all = prices.concat(avgPrices, [d.prev_close]);
  const hi = Math.max(...all), lo = Math.min(...all);
  const pad = (hi-lo)*0.1 || 1;
  const top = hi+pad, bot = lo-pad;
  const xStep = W / Math.max(pts.length-1, 1);
  // 昨收虚线
  const prevY = H - (d.prev_close - bot)/(top-bot)*H;
  ctx.strokeStyle='rgba(150,160,180,.4)'; ctx.setLineDash([4,4]);
  ctx.beginPath(); ctx.moveTo(0,prevY); ctx.lineTo(W,prevY); ctx.stroke();
  ctx.setLineDash([]);
  // 价格线
  ctx.strokeStyle = d.points[d.points.length-1].price >= d.prev_close ? '#ef4444' : '#22c55e';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  pts.forEach((p,i)=>{ const x=i*xStep, y=H-(p.price-bot)/(top-bot)*H; i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
  ctx.stroke();
  // 均价线
  ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 1;
  ctx.beginPath();
  pts.forEach((p,i)=>{ const x=i*xStep, y=H-((p.avg_price||p.price)-bot)/(top-bot)*H; i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
  ctx.stroke();
}

// 交易明细弹窗（单股单行：买入价/卖出价/盈亏/收益率）
async function openTradesDetail(term, action){
  const termLabel = term==='long' ? '长期(日线)' : '短期(30/60分)';
  document.getElementById('tradesModalTitle').textContent = `${termLabel} - 交易明细`;
  document.getElementById('tradesModalBody').innerHTML = '<div style="text-align:center;color:var(--txt2);padding:20px">加载中...</div>';
  document.getElementById('tradesModal').classList.add('show');
  const rows = await api(`/api/trades/paired?term=${term}&limit=100`);
  if(!rows || !rows.length){
    document.getElementById('tradesModalBody').innerHTML = '<div style="text-align:center;color:var(--txt3);padding:30px">暂无交易记录</div>';
    return;
  }
  let html = '<div class="paired-list">';
  rows.forEach(r=>{
    const hasSell = r.sell_price!=null;
    const pnlTxt = hasSell ? `<span class="${chgCls(r.pnl)}">${r.pnl>=0?'+':''}${fmt(r.pnl)}</span>` : '<span class="lb">持仓中</span>';
    const pctTxt = hasSell ? `<span class="${chgCls(r.pnl_pct)}">${r.pnl_pct>=0?'+':''}${fmt(r.pnl_pct,2)}%</span>` : '--';
    const sellTxt = hasSell ? fmt(r.sell_price) : '--';
    html += `<div class="paired-item">
      <div class="pi-top">
        <span class="pi-name">${r.name}</span>
        <span class="pi-strat">${r.strategy||''}</span>
        <span class="lb">${r.shares}股</span>
      </div>
      <div class="pi-cells">
        <div class="pi-cell"><span class="lb">买入</span><span class="vl">${fmt(r.buy_price)}</span></div>
        <div class="pi-cell"><span class="lb">卖出</span><span class="vl">${sellTxt}</span></div>
        <div class="pi-cell"><span class="lb">盈亏</span><span class="vl ${chgCls(r.pnl)}">${pnlTxt}</span></div>
        <div class="pi-cell"><span class="lb">收益率</span><span class="vl ${chgCls(r.pnl_pct)}">${pctTxt}</span></div>
      </div>
      <div class="pi-foot">
        <span class="lb">买${r.buy_date}${r.sell_date?' → 卖'+r.sell_date:''}</span>
        <span class="lb">费${fmt(r.fee||0)} ${r.reason?'· '+r.reason:''}</span>
      </div>
    </div>`;
  });
  html += '</div>';
  document.getElementById('tradesModalBody').innerHTML = html;
}
async function runAuto(){
  toast('执行中，后台正在扫描（约需10分钟），稍后请查看持仓和交易明细...');
  const r = await api('/api/auto/run', {method:'POST'});
  if(r){
    if(r && r.async){
      toast(r.msg || '已触发，后台扫描+交易中（约10分钟），完成后持仓/明细页查看结果');
    }else{
      toast(`执行完成，共${r.count||0}项操作`);
    }
    setTimeout(()=>{ loadMine(); loadPositions(); }, 5000);
  }
}

// ============ 推送/自动交易/定时任务配置 ============
async function loadNotifyConfig(){
  const d = await api('/api/notify/config');
  if(!d) return;
  document.getElementById('emailEnabled').value = d.email_enabled || 'off';
  document.getElementById('smtpHost').value = d.smtp_host || '';
  document.getElementById('smtpPort').value = d.smtp_port || '465';
  document.getElementById('smtpUser').value = d.smtp_user || '';
  document.getElementById('emailTo').value = d.email_to || '';
  document.getElementById('sctEnabled').value = d.sct_enabled || 'off';
  document.getElementById('sctKey').value = d.sct_key || '';
}
async function saveNotifyConfig(){
  const data = {
    email_enabled: document.getElementById('emailEnabled').value,
    smtp_host: document.getElementById('smtpHost').value,
    smtp_port: document.getElementById('smtpPort').value,
    smtp_user: document.getElementById('smtpUser').value,
    smtp_pass: document.getElementById('smtpPass').value,
    email_to: document.getElementById('emailTo').value,
    sct_enabled: document.getElementById('sctEnabled').value,
    sct_key: document.getElementById('sctKey').value,
  };
  const r = await api('/api/notify/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  toast(r && r.ok ? r.msg || '推送配置已保存' : (r && r.msg || '保存失败'));
}
async function testNotify(){
  toast('发送测试推送...');
  const r = await api('/api/notify/test', {method:'POST'});
  toast(r && r.msg ? r.msg : '发送完成');
}
async function loadAutoConfig(){
  const d = await api('/api/auto/config');
  if(!d) return;
  document.getElementById('autoModeConfig').value = d.auto_mode || 'off';
  document.getElementById('buyScore').value = d.buy_score || '70';
  document.getElementById('maxPositions').value = d.max_positions || '5';
  document.getElementById('buyRatio').value = d.buy_ratio || '0.18';
}
async function saveAutoConfig(){
  const data = {
    auto_mode: document.getElementById('autoModeConfig').value,
    buy_score: document.getElementById('buyScore').value,
    max_positions: document.getElementById('maxPositions').value,
    buy_ratio: document.getElementById('buyRatio').value,
  };
  const r = await api('/api/auto/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  toast(r && r.ok ? r.msg || '自动交易配置已保存' : (r && r.msg || '保存失败'));
}
async function triggerScan(){
  toast('已触发，扫描约需8-10分钟...');
  const r = await api('/api/scheduler/trigger', {method:'POST'});
  if(r) toast(r.msg || '已触发');
}
async function loadSchedulerJobs(){
  const jobs = await api('/api/scheduler/jobs');
  const el = document.getElementById('schedulerInfo');
  if(!el) return;
  if(!jobs || !jobs.length){ el.textContent = '(无定时任务)'; return; }
  // 显示下一个运行时间
  const j = jobs[0];
  const next = j.next_run ? j.next_run.replace('T',' ').slice(0,16) : '--';
  el.textContent = `下次: ${next}`;
}
async function resetData(){
  if(!confirm('确定清空所有交易/持仓数据，重置为10万初始资金？此操作不可恢复！')) return;
  const r = await api('/api/reset', {method:'POST'});
  if(r && r.ok){
    toast(r.msg);
    loadMine(); loadPositions();
  } else {
    toast(r && r.msg || '清空失败');
  }
}

// ============ 事件绑定 ============
document.getElementById('levelSeg').addEventListener('click', e=>{
  if(e.target.dataset.lv){
    curLevel = e.target.dataset.lv;
    e.target.parentNode.querySelectorAll('span').forEach(s=>s.classList.remove('on'));
    e.target.classList.add('on');
    // 分时模式隐藏中枢/笔切换
    document.getElementById('chartTypeSeg').style.display = curLevel==='minute'?'none':'flex';
    loadAnalyze();
  }
});
document.getElementById('toggleZS').addEventListener('click', e=>{ showZS=!showZS; e.target.classList.toggle('on',showZS); drawKline(); });
document.getElementById('toggleBI').addEventListener('click', e=>{ showBI=!showBI; e.target.classList.toggle('on',showBI); drawKline(); });
document.getElementById('searchInput').addEventListener('keydown', e=>{ if(e.key==='Enter') addWatch(); });

// 启动：先检查鉴权状态，已登录才加载行情；未登录弹出登录/设密码页
function bootApp(){
  // 如果本地有token，先走一次auth/state确认有效性（token可能在服务端被改密码失效）
  loadMarket();
}
(async function init(){
  try{
    const tok = getToken();
    const s = await fetch(API+'/api/auth/state'+(tok?'?token='+encodeURIComponent(tok):'')).then(r=>r.json()).catch(()=>({password_set:false,logged_in:false}));
    g_auth_state = s;
    if(!s.password_set){
      // 首次使用，弹设密码页
      showLogin();
    }else if(!s.logged_in){
      // 有密码但未登录
      showLogin();
    }else{
      hideLogin();
      bootApp();
    }
  }catch(e){
    // 接口异常（服务器没启动等），还是先加载内容，等实际请求时再处理401
    hideLogin();
    bootApp();
  }
})();
