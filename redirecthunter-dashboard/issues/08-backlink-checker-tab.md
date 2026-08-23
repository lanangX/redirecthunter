# 08 — Tab Backlink Checker

**What to build:** Tab baru "Backlink Checker" yang muncul otomatis kalau `.db` yang dimuat
punya data di `backlink_checks`/`backlink_results` (skema sudah ada sejak v2.0, baru mulai
berisi data di contoh terbaru). Pola sama seperti tab Crawl: stat cards, chart distribusi,
tabel dengan filter/sort/pager, drawer detail per baris, export CSV.

**Blocked by:** 03 — Distribution charts, 04 — Results table & filters, 05 — Detail drawer,
06 — Export & reload (pola-pola ini disalin ulang untuk tab baru)

**Status:** done

- [x] Tab button "Backlink Checker" disembunyikan kalau db tidak punya tabel
      `backlink_checks`/`backlink_results` (`hasBacklinkData`, dicek di `boot()`)
- [x] Stat cards: Total URL Dicek, Match Ditemukan, Tidak Ditemukan, Hanya Teks (no href),
      Diblokir, Butuh Login, Error — dihitung dari `backlink_results` sungguhan, bukan hardcode
- [x] Chart distribusi match type dan distribusi status code
- [x] Tabel hasil dengan filter (cari source/final URL, match type, status, blocked, butuh
      login), sort per kolom (klik header), pagination 50 baris/halaman
- [x] Drawer detail per baris (semua kolom `backlink_results`) saat baris diklik
- [x] Export CSV sesuai filter aktif
- [x] Diverifikasi terhadap `.db` contoh nyata (43 baris `backlink_results`, 1 baris
      `backlink_checks`, domain `medilana.id`) sebelum dianggap selesai
