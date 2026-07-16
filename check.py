from bot.store import get_kv

bt = get_kv('botstate_dry')
st = get_kv('status:dry')
print('botstate open positions:', list(bt.get('open', {}).keys()))
print('status symbols:', [s.get('symbol') for s in st.get('symbols', [])])
trb = bt.get('open', {}).get('TRB/USDT:USDT')
print("open TRB in botstate:", trb)
if trb:
    print("  sl:", trb.get('sl'), "tp:", trb.get('tp'), "entry:", trb.get('entry'), "side:", trb.get('side'))
print("symbol entry in status for TRB:", [s for s in st.get('symbols', []) if s.get('symbol') == 'TRB/USDT:USDT'])
print()
print('ZRO status symbol:', [s for s in st.get('symbols', []) if s.get('symbol') == 'ZRO/USDT:USDT'])
