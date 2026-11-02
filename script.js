const $ = id => document.getElementById(id);
const formatHOT = v => v == null ? '—' : Number(v).toLocaleString('en-GB',{maximumFractionDigits:2});
const intFmt = v => v == null ? '—' : Number(v).toLocaleString('en-GB');
function formatPercent(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  if (n > 0 && n < 0.000001) return '<0.000001%';
  const digits = n >= 1 || n === 0 ? 2 : n >= 0.01 ? 4 : 6;
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: digits }) + '%';
}
function setBridgeShare(data) {
  const supply = Number(data.totalSupply);
  const locked = Number(data.currentLocked);
  const valid = data.totalSupply != null && data.currentLocked != null &&
    Number.isFinite(supply) && Number.isFinite(locked) && supply > 0 && locked >= 0;
  const share = valid ? locked / supply * 100 : null;
  $('bridged-percent').textContent = formatPercent(share);
  $('supply-total').textContent = valid ? 'Total supply: ' + formatHOT(supply) + ' HOT' : 'Total supply: — HOT';
  const progress = $('supply-progress');
  progress.setAttribute('aria-valuetext', valid ? formatPercent(share) + ' of total HOT supply' : 'Supply data unavailable');
  if (valid) progress.setAttribute('aria-valuenow', String(Math.min(100, Math.max(0, share))));
  else progress.removeAttribute('aria-valuenow');
  $('supply-progress-fill').style.width = (valid ? Math.min(100, Math.max(0, share)) : 0) + '%';
}
const escapeText = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function compact(n){return Math.abs(n)>=1e9?(n/1e9).toFixed(1)+'B':Math.abs(n)>=1e6?(n/1e6).toFixed(1)+'M':Math.abs(n)>=1e3?(n/1e3).toFixed(0)+'K':n.toFixed(0)}
function plotChart(canvasId, emptyId, points, metricKey, color, colorFade) {
  const canvas = $(canvasId), empty = $(emptyId), parent = canvas.parentElement;
  const validPoints = (points || []).filter(p => p.date && Number.isFinite(Number(p[metricKey])));
  if (!validPoints.length) { empty.style.display='flex'; return; }
  empty.style.display='none';
  const dpr=window.devicePixelRatio||1, w=parent.clientWidth, h=parent.clientHeight;
  if(w<=0 || h<=0) return;
  canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
  const c=canvas.getContext('2d');c.scale(dpr,dpr);
  const pad={l:63,r:12,t:18,b:37}, W=w-pad.l-pad.r, H=h-pad.t-pad.b;
  const values=validPoints.map(p=>Number(p[metricKey]));
  let lo=Math.min(0,...values), hi=Math.max(1,...values);
  const delta=(hi-lo)||1;hi+=delta*.08;
  const y=v=>pad.t+H-(v-lo)/(hi-lo)*H;
  const x=i=>pad.l+(validPoints.length===1?W/2:i*W/(validPoints.length-1));
  c.font='11px system-ui';c.textAlign='right';c.textBaseline='middle';
  for(let i=0;i<=4;i++){
    const v=lo+(hi-lo)*i/4,yy=y(v);
    c.strokeStyle='#ffffff13';c.beginPath();c.moveTo(pad.l,yy);c.lineTo(w-pad.r,yy);c.stroke();
    c.fillStyle='#8da0b9';c.fillText(compact(v),pad.l-9,yy);
  }
  const gradient=c.createLinearGradient(0,pad.t,0,h-pad.b);
  gradient.addColorStop(0,colorFade);gradient.addColorStop(1,'#5ce1c000');
  c.beginPath();values.forEach((v,i)=>i?c.lineTo(x(i),y(v)):c.moveTo(x(i),y(v)));
  c.lineTo(x(values.length-1),h-pad.b);c.lineTo(x(0),h-pad.b);c.closePath();
  c.fillStyle=gradient;c.fill();
  c.beginPath();values.forEach((v,i)=>i?c.lineTo(x(i),y(v)):c.moveTo(x(i),y(v)));
  c.strokeStyle=color;c.lineWidth=2.5;c.stroke();
  c.textAlign='left';c.fillStyle='#90a3bf';c.textBaseline='bottom';
  c.fillText(validPoints[0].date,pad.l,h-8);
  c.textAlign='right';c.fillText(validPoints[validPoints.length-1].date,w-pad.r,h-8);
}
// Read the public repository directly: GITHUB_TOKEN data commits do not
// trigger GitHub Pages branch builds, so the published site's local JSON
// can remain older than the data stored on the main branch.
const PUBLIC_DATA_URL = 'https://raw.githubusercontent.com/ConorTttttt/holo-unyt-tracker/main/data/bridge.json';
async function fetchLatestBridgeData(){
  const urls = [PUBLIC_DATA_URL, 'data/bridge.json'];
  let lastError;
  for(const url of urls){
    try {
      const res = await fetch(url + '?t=' + Date.now(), {cache: 'no-store'});
      if(!res.ok) throw new Error('Data request failed: ' + res.status);
      const data = await res.json();
      if(data.schemaVersion !== 2) throw new Error('Unexpected data format');
      return data;
    } catch(error) { lastError = error; }
  }
  throw lastError || new Error('Could not load bridge data');
}
async function load(){
  try{
    const data=await fetchLatestBridgeData();
    if (data.schemaVersion !== 2) {
      $('status').textContent='● Older data format — run the updated GitHub Actions workflow';
      return;
    }
    if(!data.updatedAt){$('status').textContent='● Waiting for first GitHub Actions data sync';return;}
    $('locked').textContent=formatHOT(data.currentLocked);
    $('deposited').textContent=formatHOT(data.totalDeposited);
    $('withdrawn').textContent=formatHOT(data.totalWithdrawn);
    setBridgeShare(data);
    $('deposits-count').textContent=intFmt(data.depositTransfers);
    $('estimated-claimed').textContent=formatHOT(data.estimatedClaimed);
    $('admin-withdrawn').textContent=formatHOT(data.adminWithdrawn);
    $('admin-count').textContent=intFmt(data.adminWithdrawalEvents);
    $('unique-wallets').textContent=intFmt(data.uniqueDepositingWallets);
    $('deposit-transactions').textContent=intFmt(data.depositTransactions);
    $('deposit-events').textContent=intFmt(data.depositTransfers);
    $('new-wallets-7d').textContent=intFmt(data.newDepositingWallets7d);
    const date=new Date(data.updatedAt);
    const stale = !Number.isFinite(date.getTime()) || Date.now() - date.getTime() > 90 * 60 * 1000;
    $('status').textContent='● Last successful data sync '+date.toLocaleString('en-GB',{dateStyle:'medium',timeStyle:'short',timeZone:'UTC'})+' UTC · '+(stale ? '⚠ Scheduled updates delayed; check GitHub Actions' : 'GitHub schedule: approximately every 30 minutes (best effort)');
    const txs=data.recentTransactions||[];
    $('transactions').innerHTML=txs.length?txs.map(tx=>{
      const incoming=tx.direction==='in',admin=tx.direction==='admin',hash=String(tx.hash||'');
      if (!incoming && !admin) return '';
      if(!/^0x[a-fA-F0-9]{64}$/.test(hash))return '';
      return `<tr><td class="${incoming?'incoming':'outgoing'}">${incoming?'↓ Lock':'↑ Admin withdrawal'}</td><td><b>${formatHOT(tx.amount)}</b></td><td>${escapeText(new Date(tx.date).toLocaleString('en-GB',{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'}))}</td><td><a class="hash" target="_blank" rel="noopener" href="https://etherscan.io/tx/${hash}">${hash.slice(0,10)}…${hash.slice(-6)} ↗</a></td></tr>`;
    }).join(''):'<tr><td colspan="4" class="emptycell">No verified Lock or AdminWithdraw events found yet.</td></tr>';
    const redraw=()=>{
      plotChart('chart','emptychart',data.timeline,'cumulativeDeposited','#5ce1c0','#5ce1c044');
      plotChart('wallet-chart','empty-wallet-chart',data.walletTimeline,'uniqueWallets','#84b6ff','#84b6ff44');
    };
    redraw();window.onresize=redraw;
  }catch(e){$('status').textContent='● Unable to load data — check GitHub Actions and refresh';console.error(e)}
}
load();
// Keep an open dashboard fresh even when GitHub Pages is not rebuilt.
setInterval(load, 5 * 60 * 1000);
