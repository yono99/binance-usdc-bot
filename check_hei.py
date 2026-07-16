from bot import store
events = store.all_events()
hei = [e for e in events if e.get('symbol') == 'HEI/USDT:USDT']
print('Total events:', len(hei))
for e in hei:
    print(e.get('event'), e.get('ts'), e.get('r'), e.get('reason'))