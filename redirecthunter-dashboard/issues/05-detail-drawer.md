# 05 — Detail per-result: redirect chain & headers

**What to build:** User klik satu baris di tabel results dan melihat panel detail (drawer)
berisi seluruh metadata hasil itu, urutan redirect chain per hop (dari tabel `chain`), dan
semua header HTTP mentah (dari tabel `headers`) — untuk mendiagnosis kenapa suatu URL
berperilaku tertentu.

**Blocked by:** 04 — Tabel results: search, filter, pagination

**Status:** done

- [x] Klik baris tabel membuka drawer dari sisi kanan dengan detail result terkait
- [x] Drawer menampilkan metadata lengkap (method, status, redirect type, location, final URL, server, content-type, latency, alive, error, timestamp)
- [x] Drawer menampilkan redirect chain berurutan sesuai `hop_index`, dengan status/type/server/latency per hop
- [x] Drawer menampilkan semua header HTTP mentah untuk result tsb
- [x] Drawer bisa ditutup lewat tombol close, klik overlay, atau tombol Escape
- [x] Result tanpa chain atau tanpa headers menampilkan pesan kosong yang wajar, bukan area kosong membingungkan
