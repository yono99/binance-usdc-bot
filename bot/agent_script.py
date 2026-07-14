<script>
const $=s=>document.querySelector(s);
const rcls=v=>v>0?'pos':(v<0?'neg':'');
const esc=s=>(s==null?'':String(s)).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
async function j(u){try{const r=await fetch(u);return await r.json()}catch(e){return null}}
async function load(){
 const h=await j('/api/agent-health');
 if(h){$('#health').innerHTML=`<span>total: ${h.total}</span><span>LLM: ${h.llm}</span>`+
   `<span>fallback: ${h.fallbacks}</span><span>fallback rate: ${(h.fallback_rate*100).toFixed(1)}%</span>`+
   Object.entries(h.by_source||{}).map(([k,v])=>`<span>${esc(k)}: ${v}</span>`).join('');}
const ab=await j('/api/ab');
  if(ab){const sig=ab.significant?'<span class=pos>YA</span>':'<span class=mut>tidak</span>';
    $('#ab').innerHTML=`<b>verdict:</b> ${esc(ab.verdict)} <span class="mut">(${esc(ab.reason||'')})</span><br>`+
    `rules: exp_R <b>${ab.exp_r_rules??'—'}</b> (n=${ab.n_total??0}) · `+
    `rules+ReAct: exp_R <b>${ab.exp_r_rules_react??'—'}</b> (n=${ab.n_kept??0}) · `+
    `ditolak: exp_R ${ab.exp_r_denied??'—'} (n=${ab.n_denied??0})<br>`+
    `improvement: ${ab.improvement??'—'} · p=${ab.p_value??'—'} · signifikan: ${sig}<br>`+
    `<b>risiko (Jalan A):</b> drawdown rules ${ab.risk_rules?ab.risk_rules.max_drawdown_r:'—'}R → `+
    `rules+ReAct ${ab.risk_react?ab.risk_react.max_drawdown_r:'—'}R · `+
    `kurangi risiko: ${ab.reduces_risk?'<span class=pos>YA</span>':'<span class=mut>tidak</span>'}`;}
  await loadDecisions(1, 5);
  await loadLessons(1, 5);
  await loadEvolution(1, 5);
}
 
  // Render pagination for lessons (extracted function)
    const totalPages = l.total_pages || 1;
    const page = l.page || 1;
    const pageSize = l.page_size || 5;
    const total = l.total || 0;
    const pageSizeOptions = [5, 10, 20, 30, 100];
    const pageSizeHtml = pageSizeOptions.map(s => 
      `<option value="${s}" ${s === pageSize ? 'selected' : ''}>${s}</option>`).join('');
    const paginationHtml = `
      <div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center;">
        <button class="btnsm" onclick="loadLessons(${page - 1})" ${page <= 1 ? 'disabled' : ''}>← Prev</button>
        <span style="align-self:center;color:#8aa0c0;">Page ${page} / ${totalPages} (${total} total)</span>
        <button class="btnsm" onclick="loadLessons(${page + 1})" ${page >= totalPages ? 'disabled' : ''}>Next →</button>
        <label style="margin-left:16px;color:#8aa0c0;font-size:12px;">Page size:
          <select onchange="loadLessons(1, parseInt(this.value))" style="margin-left:4px;background:#0b1220;border:1px solid #243049;color:#e2e8f0;border-radius:4px;padding:2px 6px;">
            ${pageSizeOptions.map(s => `<option value="${s}" ${s === pageSize ? 'selected' : ''}>${s}</option>`).join('')}
          </select>
        </label>
      </div>`;
    $('#les-pagination').innerHTML = paginationHtml;
  }

  // Load lessons with pagination
  async function loadLessons(page = 1, pageSize = 5) {
    const l = await j(`/api/lessons?page=${page}&page_size=${pageSize}`);
    if(l) {
      $('#les tbody').innerHTML = (l.lessons||[]).map(x=>{const t=x.times_triggered||0,c=x.times_correct||0;
        const acc=t?(c/t*100).toFixed(0)+'%':'—';return `<tr><td>${esc(x.lesson)}</td><td class="mut">${esc(x.market_regime)}</td>`+
        `<td>${acc} <span class="mut">(${c}/${t})</span></td><td>${t}</td><td class="mut">${esc(x.source)}</td></tr>`;}).join('')
        ||'<tr><td colspan=5 class=mut>belum ada pelajaran</td></tr>';
      renderLessonsPagination(l);
    }
  }
 const e=await j('/api/evolution?limit=30');
 if(e){$('#evo tbody').innerHTML=(e.events||[]).map(x=>`<tr><td class="mut">${esc((x.ts||'').slice(0,19))}</td>`+
   `<td>${esc(x.param)}</td><td>${esc(x.old)} → ${esc(x.new??'—')}</td>`+
   `<td>${esc(x.test_exp_r_baseline??'—')} → ${esc(x.test_exp_r_proposed??'—')}</td>`+
   `<td>${esc(x.p_value??'—')}</td><td>${x.applied?'<span class=pos>YA</span>':'<span class=mut>tidak</span>'}</td></tr>`).join('')
   ||'<tr><td colspan=6 class=mut>belum ada evolusi</td></tr>';}
}
const AGFLAGS=[["agent_manager_mode","Manager-mode"],["agent_full_auto","Full-auto"],
  ["agent_tool_loop","Tool-loop"],["agent_autonomous","Autonomous"],["agent_planner","Planner"],
  ["agent_ab_shadow","A/B shadow"],["news_veto","News-veto"]];
const AGWARN={agent_manager_mode:{on:"Manager-mode (Jalan A): agent = MANAJER DISIPLIN. Arah dari RULES (mematikan teknik gemini), planner+autonomous ON, tool-loop OFF (hemat token). Lanjut?"},
  agent_full_auto:{on:"Full-auto = tool-loop+autonomous+planner. Tool-loop = BANYAK panggilan Gemini (bisa 429 free-tier). LIVE FLAT butuh allow_live_trader. Lanjut?"},
  agent_tool_loop:{on:"Tool-loop: panggilan Gemini jauh lebih banyak tiap keputusan (bisa 429). Lanjut?"},
  agent_autonomous:{on:"Autonomous: agen boleh TUTUP SEMUA posisi (FLAT)/geser stop otomatis. LIVE FLAT butuh allow_live_trader. Lanjut?"},
  agent_planner:{on:"Planner bisa MEMBATASI entry (kuota/eksposur/risk-off). Lanjut?"},
  agent_ab_shadow:{on:"A/B shadow: ReAct catat verdict tanpa memblokir (rules tetap eksekusi). Lanjut?"},
  news_veto:{off:"Matikan News-veto: entry TETAP jalan walau ada berita high-impact. Lanjut?"}};
async function loadAgentCtl(){
  const s=await j('/api/agent-settings'); if(!s)return;
  $('#agentctl').innerHTML=AGFLAGS.map(([k,lbl])=>
    `<label style="margin-right:14px"><input type="checkbox" data-k="${k}" ${s[k]?'checked':''}> ${esc(lbl)}</label>`).join('')+
    '<span id="agnote" class="pos" style="margin-left:8px"></span>';
  document.querySelectorAll('#agentctl input').forEach(el=>el.addEventListener('change',async e=>{
    const k=e.target.dataset.k, v=e.target.checked;
    const w=v?(AGWARN[k]||{}).on:(AGWARN[k]||{}).off;
    if(w && !window.confirm(w)){ loadAgentCtl(); return; }   // batal → kembalikan centang
    await fetch('/api/agent-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[k]:v})});
    await loadAgentCtl();
    const n=document.getElementById('agnote'); if(n){ n.textContent='✓ '+k+' '+(v?'ON':'OFF')+' diterapkan'; setTimeout(()=>{if(n)n.textContent='';},4000); }
  }));
}
loadAgentCtl();
load();setInterval(load,10000);
</script>