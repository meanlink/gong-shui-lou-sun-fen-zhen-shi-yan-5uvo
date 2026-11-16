import {downloadShareClaim,postClaim} from '../api/client.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const STATUS={building:'制包中',ready:'可领取',expired:'已过期',revoked:'已撤销'};
const remaining=s=>s>0?Math.floor(s/3600)+' 小时 '+Math.floor(s%3600/60)+' 分':'已到期';
export function claimPage(){
 document.querySelector('#app').innerHTML='<div class="claim-scene"><section class="claim-box"><p class="eyebrow">SHARED REVIEW</p><h1>领取限时共享包</h1><p>使用负责人单独交给你的公开标识与领取凭据。凭据只对指定共享包有效。</p>'
 +'<form id="claim-form"><label>公开标识<input name="public_id" required autocomplete="off" placeholder="例如 219ed59ca82b97e9"></label><label>领取凭据<input name="token" required type="password" autocomplete="off"></label><button type="submit" class="primary">核对并领取</button></form>'
 +'<div id="claim-result"><p class="hint">共享包过期或撤销后不再提供下载；编号、批次或凭据不匹配都会被拒绝。</p></div>'
 +'<p class="hint"><a href="#/overview">返回内部工作室</a></p></section></div>';
 submit(document.querySelector('#claim-form'),async f=>{
  const p=await postClaim(f.get('public_id').trim(),f.get('token').trim());
  const rows=(p.samples||[]).slice(0,10);
  document.querySelector('#claim-result').innerHTML='<div class="claim-card"><div class="share-head"><strong>'+esc(p.name)+'</strong><span class="status '+esc(p.status)+'">'+esc(STATUS[p.status]||p.status)+'</span></div>'
   +'<small>有效期至 '+date(p.expires_at)+' · 剩余 '+remaining(p.remaining_seconds)+'</small>'
   +'<p>'+esc(p.description||'')+'</p><p class="hint">输入指纹 '+esc(p.input_fingerprint||'')+' · 内容指纹 '+esc((p.content_checksum||'').slice(0,16))+'…</p><h3>'+esc(p.summary||'')+'</h3><p class="hint">'+esc(p.pseudonym_note||'')+'</p>'
   +'<div class="metrics">'+(p.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(m.value)+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div>'
   +'<div class="table-wrap"><table><thead><tr><th>样本代号</th><th>夜间流量(m³/h)</th><th>压力降(kPa)</th><th>声学读数(dB)</th><th>管龄(年)</th><th>叶节点漏点率(%)</th><th>分诊建议</th><th>留出集复核结果</th></tr></thead><tbody>'
   +rows.map(s=>'<tr><td>'+esc(s.sample_code)+'</td><td>'+esc(s.night_flow)+'</td><td>'+esc(s.pressure_drop)+'</td><td>'+esc(s.acoustic_db)+'</td><td>'+esc(s.pipe_age)+'</td><td>'+esc(s.risk_pct)+'</td><td>'+esc(s.triage)+'</td><td>'+esc(s.actual)+'</td></tr>').join('')+'</tbody></table></div>'
   +'<ol class="questions">'+(p.findings||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'
   +(p.warnings||[]).map(v=>'<p class="warning">'+esc(v)+'</p>').join('')
   +'<div class="share-actions"><button class="text-button" data-claim-download="csv">CSV ↓</button><button class="text-button" data-claim-download="json">JSON ↓</button></div></div>';
  document.querySelectorAll('[data-claim-download]').forEach(b=>b.onclick=()=>downloadShareClaim(p.public_id,b.dataset.claimDownload,f.get('token').trim()).catch(e=>notify(e.message,true)));
 });
}
