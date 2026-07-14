with open('dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the loadTrades function in the main dashboard script (PAGE)
# It's between the tradeQ function and delTrade function
start = content.find('async function loadTrades(){')
if start == -1:
    print('loadTrades not found')
else:
    # Find the end of loadTrades function (next function definition)
    end = content.find('async function delTrade', start)
    if end == -1:
        end = content.find('function delTrade', start)
    if end == -1:
        end = content.find('async function clearTrades', start)
    if end == -1:
        end = start + 2000
    
    old_fn = content[start:end]
    print('Found loadTrades, length:', len(old_fn))
    print('---OLD---')
    print(old_fn[:500])
    print('...')
    print(old_fn[-200:])
    print('---END---')

    # New function with pagination
    new_fn = '''async function loadTrades(page = 1, pageSize = 5){
  const q = tradeQ();
  q.set('page', page);
  q.set('page_size', pageSize);
  document.getElementById('fcsv').href = '/api/trades.csv' + (q.toString() ? '?' + q.toString() : '');
  const d = await (await fetch('/api/trades?' + q.toString())).json();
  document.getElementById('tcount').textContent = d.total + ' trade';
  document.getElementById('thist').innerHTML = table(
    [{t:'Close',f:r=>(r.close_ts||'').slice(0,16).replace('T',' ')},
     {t:'Pair',k:'symbol'},
     {t:'Side',f:r=>(r.side||'').toUpperCase(),cls:r=>r.side==='long'?'pos':(r.side==='short'?'neg':'')},
     {t:'Reason',f:r=>r.reason==='liq'?'⚠ LIQ':(r.reason||'—')},
     {t:'R',f:r=>r.r!=null?((r.r>0?'+':'')+f(r.r,3)):'—',cls:r=>cls(r.r||0)},
     {t:'PnL$',f:r=>r.pnl_usd!=null?((r.pnl_usd>=0?'+':'')+f(r.pnl_usd,2)):'—',cls:r=>r.pnl_usd!=null?(r.pnl_usd>=0?'pos':'neg'):''},
     {t:'Entry',f:r=>r.entry!=null?f(r.entry,4):'—'},
     {t:'Exit',f:r=>r.exit!=null?f(r.exit,4):'—'},
     {t:'Equity',f:r=>r.equity!=null?f(r.equity,2):'—'},
     {t:'',f:r=>r.id!=null?`<button class="del" onclick="delTrade(${r.id})" title="Hapus trade ini">✕</button>`:''}],
    d.trades, r=>r.reason==='liq'?'liqrow':'');
  renderPagination(d, 'thist', 'loadTrades');
}

'''
    
    new_content = content[:start] + new_fn + content[end:]
    
    with open('dashboard.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print('Updated loadTrades function')