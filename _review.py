import sqlite3
conn = sqlite3.connect('logs/bot.db')
rows = conn.execute("""
    SELECT setup, side, COUNT(*), ROUND(AVG(outcome_r),3), ROUND(SUM(outcome_r),2)
    FROM gemini_decisions
    WHERE status = 'settled'
    GROUP BY setup, side
""").fetchall()
for r in rows:
    print(f"{r[0]:<25} {r[1]:<6} n={r[2]} avgR={r[3]} sumR={r[4]}")
conn.close()