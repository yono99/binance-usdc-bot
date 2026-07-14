with open('bot/dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')

print('Before:')
for i in range(710, 720):
    if i < len(lines):
        print(f'{i+1}: [{lines[i]!r}] (len={len(lines[i])})')

# The correct function body should be:
# 711: '    import os'
# 712: '    import time as _t'
# 713: '    from .settings_store import load_settings'
# 714: '    if _open_orders_cache["data"] and _t.time() - _open_orders_cache["ts"] < 8:'
# 715: '        return JSONResponse(_open_orders_cache["data"])'
# 716: '    s = load_settings()'

# Find where the function starts and rebuild it
correct = [
    "    import os",
    "    import time as _t",
    "    from .settings_store import load_settings",
    "    if _open_orders_cache[\"data\"] and _t.time() - _open_orders_cache[\"ts\"] < 8:",
    "        return JSONResponse(_open_orders_cache[\"data\"])",
    "    s = load_settings()"
]

# Find the '    import os' line that starts the function body
# It's currently at index 712 (0-indexed 711) - line 712 in 1-indexed
# Replace from index 711 (line 712?) onwards until we find '    s = load_settings()'

start = None
end = None
for i, l in enumerate(lines):
    if l == '    import os' and i > 700:  # the one we care about
        start = i
        break

if start is not None:
    # Now find the 's = load_settings()' line
    for j in range(start, len(lines)):
        if lines[j].strip() == 's = load_settings()' or 's = load_settings()' in lines[j]:
            end = j + 1
            break

print(f'Replacing indices {start} to {end} (lines {start+1} to {end})')
print(f'Original: {lines[start:end]}')

# Replace
lines[start:end] = correct
with open('bot/dashboard.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print('\nAfter:')
for i in range(710, 720):
    if i < len(lines):
        print(f'{i+1}: {lines[i]!r}')
