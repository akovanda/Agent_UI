# ruff: noqa: E501
MEMORY_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Agent UI Memory</title>
  <style>
    :root { color-scheme: light dark; font: 16px/1.5 system-ui, sans-serif; }
    body { margin: 0 auto; max-width: 1050px; padding: 2rem 1rem 5rem; }
    header, .row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
    section { border: 1px solid #7775; border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    button, input { font: inherit; padding: .45rem .7rem; }
    button { cursor: pointer; }
    .item { border-top: 1px solid #7774; padding: .8rem 0; }
    .item:first-child { border: 0; }
    .muted { opacity: .7; }
    .danger { color: #d33; }
    .healthy { color: #2a6; }
    .unhealthy { color: #d55; }
    label { margin-right: 1rem; white-space: nowrap; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <header><div><h1>Memory</h1><div class="muted">Private, review-first memory for this account.</div></div>
    <button onclick="refreshAll()">Refresh</button></header>
  <div id="notice" role="status"></div>
  <section><div class="row"><h2>Status</h2><span id="provider"></span></div>
    <p id="operator" class="muted"></p>
    <div id="settings"></div>
  </section>
  <section><div class="row"><h2>Pending review</h2><span id="pending-count"></span></div>
    <div id="proposals"></div>
  </section>
  <section><div class="row"><h2>Approved records</h2>
    <button onclick="exportMemory()">Export JSON</button></div>
    <div id="records"></div>
  </section>
  <section><h2>Memory spaces</h2><p class="muted">Spaces are isolated by default. A directional
    read bridge requires an operator allowlist and your consent on both sides.</p><div id="spaces"></div></section>
<script>
const api = '/api/memory/v1';
const recordsById = {};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function call(path, options={}) {
  options.headers = {'Content-Type':'application/json','X-Agent-UI-CSRF':'1',...(options.headers||{})};
  const response = await fetch(api + path, options);
  if (!response.ok) throw new Error((await response.json().catch(()=>({}))).detail || response.statusText);
  return response.json();
}
function notice(message, bad=false) { const n=document.querySelector('#notice'); n.textContent=message; n.className=bad?'danger':'healthy'; }
async function loadStatus() {
  const value=await call('/status'); const p=value.provider;
  document.querySelector('#provider').textContent=`${p.kind}: ${p.healthy?'healthy':'unavailable'}`;
  document.querySelector('#provider').className=p.healthy?'healthy':'unhealthy';
  document.querySelector('#operator').textContent=value.automatic.operator_enabled
    ? 'Automatic memory is enabled by this operator. You remain in control.'
    : 'Automatic capture and retrieval are disabled by this operator.';
  const u=value.user;
  document.querySelector('#settings').innerHTML=['enabled','capture_enabled','retrieval_enabled'].map(k =>
    `<label><input type="checkbox" id="${k}" ${u[k]?'checked':''}> ${k.replace('_',' ')}</label>`).join('')+
    ' <button onclick="saveSettings()">Save</button>';
}
async function saveSettings() {
  const body={}; ['enabled','capture_enabled','retrieval_enabled'].forEach(k=>body[k]=document.querySelector('#'+k).checked);
  await call('/settings',{method:'PATCH',body:JSON.stringify(body)}); notice('Settings saved.'); await loadStatus();
}
async function loadProposals() {
  const value=await call('/proposals?state=pending'); document.querySelector('#pending-count').textContent=value.data.length;
  document.querySelector('#proposals').innerHTML=value.data.length?value.data.map(p=>`<div class="item"><pre>${esc(p.content)}</pre>
    <div class="muted">${esc(p.source_experience)} · expires ${esc(p.expires_at)}</div>
    <button onclick="approve('${p.id}')">Approve / edit</button> <button onclick="rejectProposal('${p.id}')">Reject</button></div>`).join(''):
    '<p class="muted">No proposals are waiting. Nothing is remembered until you approve it.</p>';
}
async function approve(id) { const content=prompt('Edit before approval, or keep as written:');
  await call(`/proposals/${id}/approve`,{method:'POST',body:JSON.stringify(content===null?{}:{content})}); notice('Memory approved.'); await refreshAll(); }
async function rejectProposal(id) { await call(`/proposals/${id}/reject`,{method:'POST',body:'{}'}); notice('Proposal rejected and its text purged.'); await loadProposals(); }
async function loadRecords() { const value=await call('/records');
  value.data.forEach(r=>recordsById[r.id]=r);
  document.querySelector('#records').innerHTML=value.data.length?value.data.map(r=>`<div class="item"><pre>${esc(r.content)}</pre>
    <div class="muted">${esc(r.status)} · ${esc(r.source||'unknown source')}</div>
    <button onclick="correct('${r.id}')">Correct</button>
    <button onclick="forgetRecord('${r.id}')">Forget</button>
    <button class="danger" onclick="purgeRecord('${r.id}')">Hard purge</button></div>`).join(''):
    '<p class="muted">No approved memories.</p>'; }
async function correct(id) { const content=prompt('Correct this memory:',recordsById[id]?.content||''); if(content===null)return;
  await call(`/records/${id}`,{method:'PATCH',body:JSON.stringify({content,reason:'user correction'})}); notice('Memory corrected.'); await loadRecords(); }
async function forgetRecord(id) { await call(`/records/${id}/forget`,{method:'POST',body:JSON.stringify({reason:'user request'})}); notice('Memory forgotten.'); await loadRecords(); }
async function purgeRecord(id) { if(!confirm('Permanently purge this memory and provider projections? This cannot be undone.'))return;
  await call(`/records/${id}`,{method:'DELETE',body:JSON.stringify({reason:'user hard purge'})}); notice('Memory permanently purged.'); await loadRecords(); }
async function loadSpaces() { const value=await call('/spaces'); document.querySelector('#spaces').innerHTML=value.data.map(s=>
  `<div class="item"><strong>${esc(s.display_name)}</strong> <span class="muted">${esc(s.kind)} · ${esc(s.id)}</span></div>`).join(''); }
async function exportMemory() { const value=await call('/export'); const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='agent-ui-memory.json'; a.click(); URL.revokeObjectURL(a.href); }
async function refreshAll() { try { await Promise.all([loadStatus(),loadProposals(),loadRecords(),loadSpaces()]); notice(''); } catch(e) { notice(e.message,true); } }
refreshAll();
</script>
</body></html>"""
