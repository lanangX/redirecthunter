# 03 — Chart distribusi status code & redirect type

**What to build:** User bisa melihat pola dominan dalam hasil scan tanpa membaca tabel baris
per baris — dua chart bar horizontal: distribusi status code (top 10) dan distribusi
redirect type, masing-masing dengan warna semantik (2xx teal, 3xx amber, 4xx/5xx merah).

**Blocked by:** 01 — Muat & validasi file .db lokal

**Status:** done

- [x] Chart status code menampilkan hingga 10 status code teratas dengan jumlah masing-masing
- [x] Chart redirect type menampilkan semua tipe redirect yang ada dengan jumlah masing-masing
- [x] Warna bar mengikuti semantik status (2xx/3xx/4xx-5xx/none)
- [x] Chart menampilkan pesan "tidak ada data" yang wajar kalau `results` kosong
