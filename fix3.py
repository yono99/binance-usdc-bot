"""Fix dashboard.py: move all late `from .settings_store import` statements
to before any if-block with early return in the same function.
"""
import re

with open('bot/dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Pattern: function body with imports then early return if-block
# We want to move late imports to be at function body level before the early return
# Strategy: find all locations where `    from .settings_store` appears after an if-return
# and move the from line to before the if

lines = text.split('\n')
new_lines = []
i = 0
fixes = 0

while i < len(lines):
    line = lines[i]
    new_lines.append(line)
    
    # Check if this line is `    from .settings_store import load_settings`
    if (line.strip().startswith('from .settings_store') and 
        line.startswith('    from .')):
        # Check if there's a return before this in same function
        # Look upward for an indented return statement
        # Find the previous 'if' at column 4 (function body level)
        # And check if there's 'return' between that 'if' and current line at column 8
        
        has_return_in_if = False
        for j in range(i-1, -1, -1):
            prev_line = lines[j]
            # If we hit another function definition or top-level statement, stop
            if (prev_line.startswith('def ') or prev_line.startswith('@app.') or 
                prev_line.startswith('class ')):
                break
            if prev_line.startswith('    if ') and 'if _open_orders_cache' not in prev_line:
                # Found an 'if' at function body level - check if it has return inside
                # Look between that 'if' and this 'from' line for a return statement
                for k in range(j+1, i):
                    if 'return JSONResponse' in lines[k] or 'return ' in lines[k]:
                        has_return_in_if = True
                        break
                break
        
        if has_return_in_if:
            # Move this 'from' line to BEFORE the if-block
            # Find the if line above and insert before it
            for j in range(i-1, -1, -1):
                if lines[j].startswith('    if ') and 'if _open_orders_cache' not in lines[j]:
                    # Insert before this if
                    # First pop the from line from new_lines (it's the last appended)
                    from_line = new_lines.pop()
                    new_lines.append(line.strip().replace('    from .settings_store import load_settings', '    from .settings_store import load_settings'))
                    # Actually we need to insert before the if. The new_lines already has
                    # the if and return statements. We need to insert 'from' before the if.
                    # Find the line in new_lines that matches the if
                    if_text = lines[j]
                    # Walk back through new_lines to find the matching if
                    insert_idx = len(new_lines) - 1
                    for m in range(len(new_lines)-1, -1, -1):
                        if new_lines[m] == if_text:
                            insert_idx = m
                            break
                    new_lines.insert(insert_idx, '    from .settings_store import load_settings')
                    fixes += 1
                    break
            else:
                # Couldn't find 'if' - just keep current arrangement
                pass
    
    i += 1

new_text = '\n'.join(new_lines)
if new_text != text:
    with open('bot/dashboard.py', 'w', encoding='utf-8') as f:
        f.write(new_text)
    print(f'FIXED {fixes} locations')
else:
    print('NO CHANGES NEEDED')

# Verify by checking all from .settings_store lines for proper indentation
print('\nAfter fix - all occurrences:')
for i, l in enumerate(new_text.split('\n'), 1):
    if 'from .settings_store' in l:
        print(f'{i}: {l}')
