import {jobs,shares} from '../api/entities.js';
import {downloadShareAdmin,downloadShareClaim,postClaim} from '../api/client.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const STATUS={building:'制包中',ready:'可领取',expired:'已过期',revoked:'已撤销'};
let pendingKey=null;
const statusChip=v=>'<span class="status '+esc(v)+'">'+esc(STATUS[v]||v)+'</span>';
const remaining=s=>s>0?Math.floor(s/3600)+' 小时 '+Math.floor(s%3600/60)+' 分':'已到期';
function ttlHint(p){
 if(!p)return '';
 return '<p class="hint">有效期至 '+date(p.expires_at)+' · 剩余 '+remaining(p.remaining_seconds)+' · 已领取 '+(p.claim_count||0)+' 次</p>';
}
const claimPanel=()=>'<section class="panel"><div class="section-title"><h2>领取核对</h2><span>使用公开标识与领取凭据</span></div>'
 +'<form id="claim-form" class="claim-form"><label>公开标识<input name="public_id" required placeholder="共享包公开标识"></label><label>领取凭据<input name="token" required type="password" placeholder="仅限本包凭据"></label><button type="submit" class="button">核对有效期与状态</button></form>'
 +'<div id="claim-result"><p class="hint">凭据只能领取指定共享包；编号、批次或凭据不匹配都会被拒绝。外部合作方可直接打开 <a href="#/claim">领取页</a>。</p></div></section>';
export async function sharePage(){
 if(session.user.role!=='admin')return heading('06 / SHARED REVIEW','限时共享包','外部凭据只能领取指定共享包；内部数据接入、分析与结果复核照常使用。')+claimPanel();
 const [list,allJobs]=await Promise.all([shares.list(),jobs.list()]);workspace.shares=list;
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed');
 return heading('06 / SHARED REVIEW','限时共享包','从已批准试验冻结当时输入与结果，用包内稳定代号替换真实管号。异包代号不可串联，过期或撤销后不再开放下载。','<button class="button" id="refresh-shares">刷新状态 ↻</button>')
 +'<div class="share-grid"><section class="panel"><div class="section-title"><h2>生成共享包</h2><span>仅已批准试验</span></div>'
 +'<form id="share-form"><label>共享包名称<input name="name" required minlength="2" value="巡检复核共享包"></label>'
 +'<label>来源试验<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+'</option>').join('')+'</select></label>'
 +'<label>给合作方的说明<textarea name="description" rows="3" maxlength="1000" placeholder="说明核对范围。原始管号会在包内替换为稳定代号。"></textarea></label>'
 +'<label>有效期<select name="ttl"><option value="1">1 小时</option><option value="24">1 天</option><option value="168" selected>7 天</option><option value="720">30 天</option></select></label>'
 +'<button type="submit" class="primary" '+(approved.length?'':'disabled')+'>冻结输入并生成共享包</button>'
 +'<p class="hint">制包由后台完成。若中断，可对同一共享包选择“接续制包”，已存代号保持不变；同一次创建重试不会新增共享包。</p></form></section>'
 +'<section class="panel"><div class="section-title"><h2>共享包状态</h2><span>'+list.length+' 个</span></div>'
 +'<div class="share-list">'+list.map(p=>'<article class="share-item" data-share="'+p.id+'"><div class="share-head"><strong>'+esc(p.name)+'</strong>'+statusChip(p.status)+'</div>'
 +'<p>'+esc(p.description||'—')+'</p><small>公开标识 '+esc(p.public_id)+' · 有效期至 '+date(p.expires_at)+' · 剩余 '+remaining(p.remaining_seconds)+'</small>'
 +'<small>样本 '+p.item_count+' 条 · 已领取 '+(p.claim_count||0)+' 次 · 下载 '+(p.download_count||0)+' 次 · 凭据 '+esc(p.token_hint)+(p.content_checksum?' · 指纹 '+esc(p.content_checksum.slice(0,16))+'…':'')+'</small>'
 +(p.build_error?'<p class="warning">上次制包中断：'+esc(p.build_error)+'</p>':'')
 +'<div class="share-actions">'
 +(p.credential?'<button class="text-button" data-copy="'+esc(p.credential)+'">复制领取凭据</button>':'')
 +(p.downloadable?'<button class="text-button" data-share-download="'+p.id+'" data-format="csv">CSV ↓</button><button class="text-button" data-share-download="'+p.id+'" data-format="json">JSON ↓</button>':'')
 +(p.status==='building'?'<button class="text-button" data-resume="'+p.id+'">接续制包</button>':'')
 +(p.status==='ready'||p.status==='building'?'<button class="text-button danger" data-revoke="'+p.id+'">撤销</button>':'')
 +'</div></article>').join('')+(!list.length?'<p class="empty">尚未生成共享包。</p>':'')+'</div></section></div>'
 +claimPanel();
}
export function bindShares(refresh){
 document.querySelector('#refresh-shares')?.addEventListener('click',refresh);
 const createForm=document.querySelector('#share-form');
 if(createForm)submit(createForm,async f=>{
  pendingKey=pendingKey||('ui-'+(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random().toString(36).slice(2)));
  const result=await shares.create({name:f.get('name'),job_id:Number(f.get('job_id')),description:f.get('description'),expires_in_hours:Number(f.get('ttl')),idempotency_key:pendingKey});
  pendingKey=null;
  notify(result.reused?'该次生成已存在，沿用原共享包':(result.status==='ready'?'共享包已生成并可领取':'共享包正在后台制包，稍后刷新状态'));await refresh();
 });
 document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(b.dataset.copy);notify('领取凭据已复制，请通过独立渠道交给合作方');}catch(e){notify('复制失败，请手动记录凭据',true);}});
 document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=async()=>{try{await shares.resume(b.dataset.resume);notify('已接续原进度，已存代号保持不变');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-revoke]').forEach(b=>b.onclick=async()=>{if(!confirm('撤销后该共享包将拒绝新的领取与下载，是否继续？'))return;try{await shares.revoke(b.dataset.revoke);notify('共享包已撤销');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-share-download]').forEach(b=>b.onclick=()=>downloadShareAdmin(b.dataset.shareDownload,b.dataset.format).catch(e=>notify(e.message,true)));
 submit(document.querySelector('#claim-form'),async f=>{
  const p=await postClaim(f.get('public_id').trim(),f.get('token').trim());
  const rows=(p.samples||[]).slice(0,8);
  document.querySelector('#claim-result').innerHTML='<div class="claim-card"><div class="share-head"><strong>'+esc(p.name)+'</strong>'+statusChip(p.status)+'</div>'+ttlHint(p)
   +'<p>'+esc(p.summary||'')+'</p><p class="hint">'+esc(p.pseudonym_note||'')+'</p>'
   +'<div class="metrics">'+(p.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(m.value)+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div>'
   +'<div class="table-wrap"><table><thead><tr><th>样本代号</th><th>叶节点漏点率(%)</th><th>分诊建议</th><th>留出集复核结果</th></tr></thead><tbody>'+rows.map(s=>'<tr><td>'+esc(s.sample_code)+'</td><td>'+esc(s.risk_pct)+'</td><td>'+esc(s.triage)+'</td><td>'+esc(s.actual)+'</td></tr>').join('')+'</tbody></table></div>'
   +'<div class="share-actions"><button class="text-button" data-claim-download="csv">CSV ↓</button><button class="text-button" data-claim-download="json">JSON ↓</button></div></div>';
  document.querySelectorAll('[data-claim-download]').forEach(b=>b.onclick=()=>downloadShareClaim(p.public_id,b.dataset.claimDownload,f.get('token').trim()).catch(e=>notify(e.message,true)));
 });
}
