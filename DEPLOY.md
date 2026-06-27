# Deploy di Proxmox (Debian) — server lokal 12700H

Target: jalankan **bot forward-test + dashboard** 24/7 di Proxmox VE, di container/VM
Debian, dengan DNS `1.1.1.1`. Forward-test paper → **tanpa API key, nol risiko**.

## 1. Buat container Debian (LXC, paling ringan)

Di Proxmox: **Create CT** (atau VM kalau mau isolasi penuh).
- Template: **Debian 12**
- Resources (12700H punya 14 core/20 thread — lega):
  - Bot + dashboard saja: **2 vCPU / 2 GB RAM / 8 GB disk** sudah cukup.
  - Kalau mau sekalian sweep optimasi berat (lihat §5): **8–12 vCPU / 8 GB**.
- **DNS**: di tab *DNS* CT, isi **DNS servers: `1.1.1.1`** (atau `1.1.1.1 1.0.0.1`).
- Network: bridge `vmbr0`, IP DHCP/static di LAN.

> Untuk Docker di LXC: jadikan container **unprivileged** + aktifkan fitur `nesting=1`
> (Options → Features → Nesting). Atau pakai VM bila ragu — VM paling mulus untuk Docker.

## 2. Setup dasar Debian + DNS 1.1.1.1

```bash
# di console CT/VM
apt update && apt -y upgrade
apt -y install git curl ca-certificates

# pastikan DNS 1.1.1.1 (kalau belum lewat Proxmox)
echo "nameserver 1.1.1.1" > /etc/resolv.conf
echo "nameserver 1.0.0.1" >> /etc/resolv.conf
getent hosts fapi.binance.com    # tes resolusi
```

## 3. Install Docker + Compose

```bash
curl -fsSL https://get.docker.com | sh
docker run --rm hello-world      # verifikasi
```

## 4. Jalankan bot + dashboard

```bash
git clone https://github.com/yono99/binance-usdc-bot.git
cd binance-usdc-bot
docker compose up -d --build
docker compose logs -f bot       # pantau
```

- Dashboard: `http://<IP-CT>:8000` (akses dari LAN).
- Default `MODE=dry` (paper). Pengaturan diatur dari UI (teknik/leverage/bet).
- Data tersimpan di `./logs` (jurnal + `runtime.json`), persisten antar-restart.

### Jaringan (penting untuk akurasi data)
Bot butuh **outbound HTTPS** ke Binance:
`api.binance.com`, `fapi.binance.com`, `fstream.binance.com`. Pastikan firewall LAN
mengizinkan. DNS `1.1.1.1` membantu resolusi cepat & stabil. Untuk latency lebih
rendah saat live nanti, pertimbangkan VPS region Tokyo (lihat README) — server lokal
bagus untuk **riset & forward-test**, bukan untuk eksekusi live latency-kritis.

## 5. Manfaatkan tenaga 12700H untuk riset strategi

Server lokalmu kuat → pakai untuk **sweep walk-forward lebih besar** (lebih banyak
simbol, lebih panjang histori, lebih banyak kombinasi) demi sampel & keyakinan lebih tinggi:

```bash
# masuk ke container app (atau buat venv Debian: apt install python3-venv)
docker compose exec bot python optimize.py --strategy v4 \
  --symbols "BTC/USDC:USDC" "ETH/USDC:USDC" "SOL/USDC:USDC" "BNB/USDC:USDC" \
  --bars 6000 --train 1200 --test 300
```

> Jujur: lebih banyak compute = uji lebih banyak hipotesis & sampel lebih besar
> (bagus untuk **keyakinan statistik**), TAPI **tidak menciptakan edge** yang tak ada.
> Walk-forward kita konsisten menunjukkan ~impas. Compute mempercepat pembuktian,
> bukan menjamin profit.

## 6. Operasional

```bash
docker compose ps                 # status
docker compose restart bot        # restart
docker compose pull && docker compose up -d --build   # update setelah git pull
docker compose down               # stop
```

Auto-start saat boot: Docker service sudah `enabled` by default; `restart: unless-stopped`
di compose memastikan kedua service hidup lagi setelah reboot Proxmox/CT.
