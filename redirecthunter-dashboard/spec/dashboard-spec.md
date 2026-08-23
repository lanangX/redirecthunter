# Spec: RedirectHunter Scan Dashboard (v1)

## Problem Statement

Hasil scan RedirectHunter tersimpan sebagai file SQLite (`redirecthunter.db`) berisi ribuan
baris hasil pengecekan redirect/backlink per URL, redirect chain per hop, dan header HTTP
mentah. Tidak ada cara melihat data ini selain query SQL manual — sulit dipakai untuk
menyisir hasil (mana yang mati, mana yang mencurigakan, redirect chain seperti apa) tanpa
tools tambahan, dan user tidak mau data scan (yang menyangkut target domain sensitif) di-upload
ke server pihak manapun.

## Solution

Dashboard HTML statis yang dibuka langsung di browser. User drag & drop file `.db` ke halaman;
file dibaca sepenuhnya di client (sql.js/WASM) — tidak pernah dikirim ke jaringan. Dashboard
menampilkan ringkasan scan, distribusi status/redirect type, tabel hasil yang bisa difilter dan
dicari, serta detail redirect chain + header mentah per hasil.

## User Stories

1. Sebagai user, saya ingin drag & drop file `.db` ke halaman, supaya saya tidak perlu upload
   atau install apapun untuk melihat hasil scan.
2. Sebagai user, saya ingin melihat ringkasan scan (target, status, total URL, durasi, jumlah
   alive/dead) segera setelah file termuat, supaya saya tahu gambaran umum sebelum menyisir detail.
3. Sebagai user, saya ingin melihat distribusi status code (200/301/302/404/dst) dan tipe
   redirect, supaya saya cepat tahu pola dominan dalam hasil scan.
4. Sebagai user, saya ingin mencari hasil berdasarkan source URL atau final URL, supaya saya
   bisa cek cepat apakah URL tertentu ada dan ke mana dia mengarah.
5. Sebagai user, saya ingin memfilter hasil berdasarkan status code, redirect type, dan
   alive/dead, supaya saya bisa fokus ke subset tertentu (mis. semua yang 404, atau semua yang dead).
6. Sebagai user, saya ingin klik satu baris hasil dan melihat detail lengkapnya — termasuk
   redirect chain per hop dan semua header HTTP mentah — supaya saya bisa mendiagnosis kenapa
   suatu redirect berperilaku tertentu.
7. Sebagai user, saya ingin export hasil yang sedang difilter ke CSV, supaya saya bisa
   olah lebih lanjut atau bagikan ke orang lain tanpa membagikan seluruh database mentah.
8. Sebagai user, saya ingin dashboard tetap responsif walau hasil scan ada ribuan baris,
   supaya saya tidak menunggu lama saat mengetik pencarian atau ganti filter.
9. Sebagai user, saya ingin bisa mengganti file `.db` lain tanpa reload halaman penuh, supaya
   saya bisa membandingkan beberapa hasil scan secara berurutan.
10. Sebagai user, jika file yang di-drop bukan database RedirectHunter yang valid, saya ingin
    pesan error yang jelas, supaya saya tahu file itu salah, bukan dashboard yang rusak.

## Implementation Decisions

- **Distribusi**: satu file `index.html`, tanpa build step, tanpa server. Dependency eksternal
  hanya `sql.js` (WASM, via CDN cdnjs) dan dua Google Font. Tidak ada framework JS.
- **Baca database**: `sql.js` memuat seluruh `.db` ke memori browser (`Uint8Array` dari
  `FileReader`/`arrayBuffer`), semua query lewat SQL asli terhadap tabel `scan`, `results`,
  `chain`, `headers` — lihat skema di `docs/CONTEXT.md`.
- **Validasi file**: setelah `.db` dimuat, cek tabel `scan` dan `results` ada di
  `sqlite_master` sebelum lanjut boot dashboard; kalau tidak ada, tampilkan pesan error di
  layar drop, jangan lempar exception mentah ke user.
- **Tabel hasil**: query ulang ke SQL setiap ganti filter/halaman/pencarian (bukan filter
  array besar di JS) — `WHERE` dinamis dari kombinasi search+status+redirect_type+alive,
  `LIMIT 50 OFFSET`. Pencarian di-debounce 250ms.
- **Detail per-result**: query `chain` dan `headers` by `result_id` secara lazy saat baris
  diklik (drawer), tidak di-preload semua di awal.
- **Export CSV**: re-run query filter aktif tanpa `LIMIT`, generate CSV di browser dengan
  `Blob`, download langsung — tidak lewat server manapun.
- **Desain visual**: tema dark "security/network tool" — background hampir hitam, font mono
  (`JetBrains Mono`) untuk data/header, font sans (`Inter`) untuk body text, warna semantik:
  teal (`--alive`) untuk alive/2xx, amber (`--amber`) untuk redirect/3xx, merah (`--dead`)
  untuk dead/4xx/5xx/error. Chart dibuat manual (bar horizontal CSS/SVG), bukan library chart
  terpisah, supaya dependency eksternal cuma satu.

## Testing Decisions

- Tidak ada automated test suite untuk v1 (single static HTML file, tanpa build/test tooling).
- Verifikasi manual yang wajib sebelum ticket dianggap selesai: buka `index.html` di browser,
  drag & drop file `.db` contoh, pastikan tidak ada error di console browser, dan tiap fitur di
  User Stories di atas bisa dicoba langsung (search, filter, klik baris, export CSV, ganti file).
- Kalau ke depan ditambah test otomatis, prior art terdekat: tidak ada di repo ini — akan jadi
  keputusan baru (mis. Playwright untuk smoke test browser) yang perlu didiskusikan dulu dengan user,
  karena akan menambah tooling di luar filosofi "single file, no build" saat ini.

## Out of Scope

- Autentikasi/multi-user, penyimpanan riwayat scan lintas sesi, perbandingan dua scan
  berdampingan.
- Upload/sinkronisasi ke server manapun.
- Histogram latency, chart hop count, atau visualisasi statistik lanjutan lain di luar yang
  disebut di User Stories.
- Edit/tulis balik ke file `.db` (dashboard read-only).
- Mendukung skema database selain skema `scan`/`results`/`chain`/`headers` yang sudah ada.

## Further Notes

Dibangun dan diverifikasi terhadap contoh `redirecthunter.db` nyata (~21MB, 7.890 baris
`results`, target scan `medilana.id`) untuk memastikan query dan performa UI wajar pada
volume data tersebut. Lihat `docs/MEMORY.md` untuk catatan trade-off yang tidak jelas dari
kode saja.
