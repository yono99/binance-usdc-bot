from bot import store
st = store.get_kv('status:dry')
if st:
    print('open_count:', st.get('open_count'))
    print('enabled:', st.get('enabled'))
    for s in st.get('symbols', []):
        if s.get('in_position'):
            pos = s.get('position', {})
            print(s['symbol'], s.get('signal'), pos.get('side'))
else:
    print('No status found')