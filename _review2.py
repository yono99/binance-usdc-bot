import sqlite3
conn = sqlite3.connect('logs/bot.db')
rows = conn.execute("""
    SELECT ts, symbol, setup, side, outcome_r, exit_reason, conviction
    FROM gemini_decisions
    WHERE status = 'settled'
    ORDER BY ts DESC
""").fetchall()
print("ALL SETTLED TRADES:")
for r in rows:
    print(f"  {r[0][:19]} {r[1]:<18} setup={r[2]:<22} side={r[3]:<5} R={r[4]:>6} exit={r[5]:<12} conf={r[6]}")
conn.close()