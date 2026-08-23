# 07 — Sembunyikan duplikat source URL

**What to build:** Sebagian source URL ter-scan lebih dari sekali dalam satu sesi scan (data
sumber, bukan bug dashboard). User bisa mengaktifkan toggle "Sembunyikan duplikat" supaya
hanya 1 baris per source URL (percobaan terakhir) yang dihitung/ditampilkan di seluruh
dashboard — stat cards, chart, tabel, filter, dan kedua export.

**Blocked by:** 02 — Ringkasan scan (overview cards), 03 — Chart distribusi status code & redirect type, 04 — Tabel results: search, filter, sort, pagination, 06 — Export CSV/TXT terfilter & ganti file scan

**Status:** done

- [x] Kalau tidak ada source URL yang duplikat, panel menampilkan info bahwa tidak ada duplikat (tanpa toggle yang membingungkan)
- [x] Kalau ada, panel menampilkan jumlah source URL yang duplikat dan jumlah baris ekstra, plus toggle untuk mengaktifkan/menonaktifkan
- [x] Saat toggle aktif, stat cards, chart, tabel, opsi filter, dan hasil export (CSV & TXT) semuanya dihitung dari data yang sudah di-dedupe (1 baris per source URL, percobaan terakhir)
- [x] Data mentah `.db` tidak diubah — dedupe murni tampilan/view
- [x] Klik baris di tabel (mode dedupe aktif atau tidak) tetap membuka drawer detail yang benar untuk baris tersebut
- [x] Toggle dan state dedupe di-reset saat user ganti file `.db`
