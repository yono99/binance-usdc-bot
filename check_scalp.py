from bot import store
events = store.all_events()
scalp = [e for e in events if e.get('setup') == 'scalp_range']
print(f'Total scalp_range events: {len(scalp)}')
for e in scalp:
    print(e.get('event'), e.get('symbol'), e.get('r'), e.get('reason'), e.get('ts'))