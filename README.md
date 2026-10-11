# kjk-syslog-ntp

Lab GNS3 yang mendemonstrasikan bagaimana **NTP** dan **Syslog** berfungsi
sebagai kontrol keamanan jaringan, bukan sekadar kenyamanan operasional:
waktu yang akurat dan terautentikasi menjadi dasar log yang bisa
dipercaya, dan logging terpusat yang dibatasi aksesnya memberi kita
catatan yang tidak bisa dihapus oleh penyerang di endpoint.

*(English version: [README.en.md](README.en.md))*

## Topologi

```
                      WAN (dikonfigurasi otomatis oleh GNS3)
   NAT1 ─────────────────── eth0 ┐
                                 │  router
                   10.10.10.1 ── eth1 ┐
                                      │
                                 Switch1
                                 ┌─────┴─────┐
                          10.10.10.11   10.10.10.12
                            node1          node2
```

Ketiga node guest menjalankan image Docker `debinet` (Debian trixie).
`router` berperan sebagai gateway LAN, NAT, NTP server, dan syslog
server sekaligus — node infrastruktur yang dipercaya. `node1`/`node2`
adalah client-nya.

- `router` eth0 → `NAT1`: uplink WAN, IP/route-nya diberikan otomatis
  oleh integrasi NAT GNS3 (tidak perlu dikonfigurasi manual).
- `router` eth1 → `Switch1` → `node1`/`node2`: LAN internal
  (`10.10.10.0/24`).
- `router` melakukan MASQUERADE via `iptables` agar `node1`/`node2`
  bisa mengakses internet (diperlukan untuk `apt-get install` mereka
  sendiri) lewat router.

## Bagaimana NTP mengamankan jaringan

NTP (Network Time Protocol) menyinkronkan jam di berbagai mesin. Kalau
berdiri sendiri itu cuma soal kenyamanan — tapi banyak aspek keamanan
yang sebenarnya bergantung padanya:

- **Korelasi log.** Hampir semua insiden melibatkan lebih dari satu
  host. Kalau jam mereka tidak sinkron, kita tidak bisa menentukan
  dengan andal kejadian mana di `node1` yang terjadi sebelum atau
  setelah kejadian di `node2` — timeline yang dibutuhkan untuk
  investigasi jadi berantakan.
- **Validitas sertifikat dan token.** Masa berlaku sertifikat TLS,
  tiket Kerberos, kode TOTP, JWT yang ditandatangani — semuanya
  dibatasi waktu. Client dengan jam yang salah bisa tertipu menerima
  sertifikat yang sudah expired sebagai valid, atau menolak sertifikat
  yang sebenarnya masih sah.
- **Risiko spoofing.** NTP sendiri bisa diserang: penyerang on-path
  atau off-path yang bisa menyisipkan respons NTP palsu dapat
  menggeser jam korban, lalu memanfaatkan pergeseran itu untuk
  mengakali pengecekan berbasis waktu di atas, atau membuat timestamp
  log di host yang sudah disusupi berbohong soal kapan sesuatu
  terjadi.

Pertahanan lab ini terhadap poin terakhir itu adalah **autentikasi NTP
berbasis symmetric key**: `router` dan kedua client berbagi key SHA1
(`/etc/chrony/chrony.keys`). Client hanya menerima update waktu dari
server yang bisa membuktikan memegang key yang sama — penyerang di LAN
yang tidak punya key itu akan di-NAK dan diabaikan, seyakin apapun
paket spoofing-nya terlihat.

```
# router — chrony.conf (cuplikan)
allow 10.10.10.0/24       # layani waktu terautentikasi hanya untuk LAN
local stratum 10          # tetap melayani meski upstream pool sempat tidak terjangkau
keyfile /etc/chrony/chrony.keys

# node1/node2 — chrony.conf
server 10.10.10.1 iburst key 1   # hanya percaya router, dan hanya jika key-nya cocok
```

Cek langsung:

```
./console.py node1 "chronyc -a authdata"   # KeyID 1, NAK=0 -> terautentikasi, respons palsu ditolak
./console.py router "chronyc -a sources"   # sinkronisasi upstream router sendiri
```

## Bagaimana Syslog mengamankan jaringan

Syslog adalah protokol standar bagi suatu mesin untuk mengirim pesan
log. Memusatkan pesan-pesan itu ke server terpisah yang dibatasi
aksesnya adalah kontrol keamanan tersendiri:

- **Tahan terhadap tampering.** Kalau `node1` disusupi, penyerang bisa
  menghapus atau mengubah log lokalnya untuk menghilangkan jejak —
  tapi salinan yang sudah terkirim ke `router` tidak terjangkau oleh
  mereka. Rekaman kejadian tetap selamat.
- **Satu titik pantau.** Mengorelasikan serangan lintas host jauh
  lebih mudah kalau semua event berakhir di satu tempat, dibanding
  harus SSH satu-satu ke setiap host untuk mencarinya.
- **Kontrol akses di sisi collector.** `router` hanya menerima
  trafik syslog dari LAN internal (`10.10.10.0/24`), baik lewat UDP
  maupun TCP 514 — jadi host di sisi WAN tidak bisa menyisipkan entri
  log palsu untuk menjebak mesin lain atau mengubur log asli dengan
  noise.
- **Timestamp yang bisa dipercaya.** Di sinilah NTP dan syslog
  bertemu: log terpusat hanya berguna untuk korelasi kalau timestamp
  setiap entrinya akurat — dan itu persis yang dijamin oleh setup NTP
  terautentikasi di atas.

```
# node1/node2 — forward semua log ke router lewat TCP
*.* action(type="omfwd" target="10.10.10.1" port="514" protocol="tcp" ...)

# router — hanya terima dari LAN, satu file per host pengirim
$AllowedSender TCP, 10.10.10.0/24
template(name="PerHostLog" type="string" string="/var/log/remote/%HOSTNAME%/syslog.log")
```

Cek langsung:

```
./console.py node1 "logger -t demo hello-from-node1"
./console.py router "cat /var/log/remote/node1/syslog.log"
```

Aturan `iptables` di `router` memperkuat batas yang sama di level
paket: NTP (123/udp) dan syslog (514/udp,tcp) di-`ACCEPT` dari `eth1`
(LAN) dan secara eksplisit di-`DROP` dari `eth0` (WAN) — jadi
seandainya ada yang berhasil masuk ke sisi WAN, layanan-layanan ini
tetap sama sekali tidak bisa diakses dari sana.

## Struktur workspace

```
creds.env           # host/kredensial GNS3 + PROJECT_NAME (di-gitignore)
creds.env.example   # template untuk file di atas
topology.py         # membangun topologi GNS3 secara idempotent (node + link)
console.py          # ./console.py <node_name> [command...] -- buka shell, atau jalankan satu komando
push.py             # ./push.py <node_name> -- push <node_name>/* ke /root pada node tersebut
router/init.sh       # skrip provisioning router saat boot
node1/init.sh        # skrip provisioning node1 saat boot
node2/init.sh        # skrip provisioning node2 saat boot
```

`init.sh` adalah entry point per node: `/etc/debinet-init.sh` milik
image `debinet` otomatis menjalankan `/root/init.sh` saat container
boot kalau file itu ada dan executable, sebelum masuk ke shell
interaktif. Di situlah semua konfigurasi di atas (pengalamatan, NAT,
chrony, rsyslog, firewall) benar-benar diterapkan — `push.py` hanya
bertugas menaruh filenya di sana.

## Cara pakai

```
cp creds.env.example creds.env   # isi GNS3_HOST / USERNAME / PASSWORD / PROJECT_NAME

./topology.py                    # buat/update topologi GNS3 (aman dijalankan berulang)

./push.py router                 # push router/init.sh -> router:/root/init.sh
./push.py node1
./push.py node2

# lalu stop/start (atau reload) setiap node dari GNS3 GUI agar init.sh jalan
```

Restart node akan menjalankan ulang `init.sh`-nya. Instalasi paket
dilewati kalau node sudah punya `chronyd`/`rsyslogd` (jadi restart jadi
cepat), dan `node1`/`node2` menunggu `router` siap dulu sebelum
`apt-get` pertama mereka, karena ketiganya mungkin boot bersamaan.

Untuk eksplorasi lebih lanjut:

```
./console.py node1                      # shell interaktif di node1
./console.py router "iptables -L -v -n" # satu komando langsung, tanpa shell
```
