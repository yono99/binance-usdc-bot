"""Fix dashboard.py carefully - restore from earlier broken fix.
The pattern that needs fixing:
    def xyz(...):
        ...
        if condition:
            return ...
    from .settings_store import ...  <- BROKEN: unindented from original indent
        ...rest of function...

Should be:
    def xyz(...):
        ...
        from .settings_store import ...  <- MOVED UP before the if
        if condition:
            return ...
        ...rest of function...
"""

with open('bot/dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')

# Pristine file from the original repo
# Strategy: revert file to a known-good version before my edits
# Actually, easier: fix every occurrence where we see `from .settings_store` at column 0

# Walk through and fix
fixed_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Check for problematic pattern
    if (line == 'from .settings_store import load_settings' and 
        i + 1 < len(lines) and 
        lines[i+1].startswith('    s = load_settings()')):
        # This is a misplaced from-import at column 0
        # We need to find the matching function above and insert it before the if
        # Find the last if-block with return before this
        # Look backward for the pattern
        # Find '    if X and Y' followed by '        return'
        for j in range(i-1, max(0, i-30), -1):
            if lines[j].startswith('    if ') and 'return' in (lines[j+1] if j+1 < len(lines) else ''):
                # Found it. Insert '    from .settings_store import load_settings' before lines[j]
                fixed_lines.insert(len(fixed_lines)-1, '    from .settings_store import load_settings')
                # Now add the misplaced from line we were going to add next
                fixed_lines.append('from .settings_store import load_settings')
                # ...wait this doesn't work
                break
        
        # Actually let me approach this differently
        # The pattern in fixed_lines is already at this point
        # I need to find the if-then-return in fixed_lines that's before the current position
        # And insert '    from .settings_store import load_settings' before that if
        
        # Find the if we should insert before in fixed_lines
        target_idx = -1
        for k in range(len(fixed_lines)-1, -1, -1):
            if fixed_lines[k].startswith('    if ') and k+1 < len(fixed_lines) and 'return' in fixed_lines[k+1]:
                target_idx = k
                break
        
        if target_idx >= 0:
            # Insert before the if
            fixed_lines.insert(target_idx, '    from .settings_store import load_settings')
            # Skip the original misplaced from line
            i += 1
            # The next lines should now be already in fixed_lines (they continue normally)
            continue
    
    fixed_lines.append(line)
    i += 1

# write back
with open('bot/dashboard.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(fixed_lines))

print('Done')

# Check for any remaining unindented 'from .settings_store' on column 0
for i, l in enumerate(fixed_lines, 1):
    if l == 'from .settings_store import load_settings':
        print(f'WARNING: unfixed line {i}: {l!r}')
