import subprocess, json

# Get log lines around the BNB duplicate close (lines 154320-154400 area based on earlier data)
ssh = ["ssh", "-i", "C:\\Users\\Ree\\.ssh\\id_ed25519_proxmox", "root@192.168.1.107",
       "grep -n 'BNB/USDC:USDC.*CLOSE\\|CLOSE.*BNB\\|_apply_settings\\|_switch_mode\\|restore\\|self.open' /root/binance-usdc-bot/logs/bot.log | tail -30"]

result = subprocess.run(ssh, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
with open("_bnb_log.txt", "w", encoding="utf-8") as f:
    f.write(result.stdout)
    
# Also get raw log lines around the time of BNB closes (12:46 to 12:47)
ssh2 = ["ssh", "-i", "C:\\Users\\Ree\\.ssh\\id_ed25519_proxmox", "root@192.168.1.107",
        "grep -n '12:46\\|12:47' /root/binance-usdc-bot/logs/bot.log | grep -i 'close\\|open\\|stats\\|BNB\\|apply_settings\\|switch' | head -20"]
result2 = subprocess.run(ssh2, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
with open("_bnb_timing.txt", "w", encoding="utf-8") as f:
    f.write(result2.stdout)

print("done")
