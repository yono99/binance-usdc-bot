with open('bot/dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')

# Find line 714 and move `from .settings_store import` to before the `if`
# The current structure (line numbers):
# 710: """...""" (end of docstring)
# 711:     import os
# 712:     import time as _t
# 713:     if _open_orders_cache...
# 714:         return JSONResponse(...)
# 715:     from .settings_store import load_settings    <-- MOVE THIS UP
# 716:     s = load_settings()

if 714 < len(lines):
    print('Line 714:', repr(lines[713]))
    if 'from .settings_store' in lines[713]:
        # Remove the from line
        from_line = lines.pop(713)
        # Insert after the first import that appears (e.g., after 'import time as _t' which is now line 712)
        # We want to put it after the imports, before the if
        # Find the import lines section
        insert_idx = 712
        # Check if line 712 is '    import time as _t'
        if 'import' in lines[711]:
            insert_idx = 712
        lines.insert(insert_idx, from_line.strip())
        with open('bot/dashboard.py', 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print('FIXED: moved from .settings_store import to line', insert_idx+1)
    else:
        print('Line 714 does not contain from .settings_store')

# Print lines 710-720 to verify
print('---')
with open('bot/dashboard.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f.read().split('\n')[709:720], start=710):
        print(f'{i}: {line[:80]}')
