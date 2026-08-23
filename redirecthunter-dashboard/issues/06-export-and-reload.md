# 06 — Export CSV/TXT terfilter & ganti file scan

**What to build:** User bisa mengunduh hasil yang sedang difilter sebagai CSV (semua kolom
kunci) atau sebagai TXT (hanya `source_url`, dengan placeholder `{TARGET}` diganti ke domain
target scan) — tanpa lewat server manapun — dan bisa mengganti ke file `.db` lain tanpa reload
halaman penuh, supaya bisa membandingkan beberapa hasil scan berurutan dalam satu sesi browser.

**Blocked by:** 04 — Tabel results: search, filter, sort, pagination

**Status:** done

- [x] Tombol "Export CSV" mengunduh seluruh baris yang cocok dengan filter aktif (bukan cuma
      halaman yang sedang tampil), dengan kolom-kolom kunci hasil
- [x] Tombol "Export TXT" mengunduh daftar `source_url` sesuai filter aktif, satu URL per baris
- [x] Di Export TXT, tiap kemunculan literal `{TARGET}` di `source_url` diganti dengan `scan.target`
- [x] Export berjalan sepenuhnya di browser (Blob + download), tidak ada request ke server
- [x] Tombol "ganti file" mengembalikan user ke layar drop tanpa reload halaman
- [x] Setelah ganti file, seluruh state lama (filter, sort, halaman, drawer) tidak nyangkut ke db yang baru
