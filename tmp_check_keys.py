import sys, yaml
sys.path.insert(0, '/root/binance-usdc-bot')
cfg = yaml.safe_load(open('/root/binance-usdc-bot/config.yml'))
keys = cfg.get('gemini_keys', [])
print('Total keys:', len(keys))
for i, k in enumerate(keys):
    print('  key[' + str(i) + ']: ...' + k[-8:])
m = cfg.get('gemini', {})
print('Model primary:', m.get('model', 'not set'))
print('Decide cap:', m.get('gemini_decide_cap', 24))
