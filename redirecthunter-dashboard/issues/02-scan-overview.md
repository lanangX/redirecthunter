# 02 — Ringkasan scan (overview cards)

**What to build:** Begitu file termuat, user langsung melihat ringkasan satu layar: target
scan, status scan, total URL, jumlah alive/dead, rata-rata hop, rata-rata latency, jumlah
error, dan durasi scan — tanpa perlu klik apapun.

**Blocked by:** 01 — Muat & validasi file .db lokal

**Status:** done

- [x] Header menampilkan target dan status scan dari tabel `scan`
- [x] Stat cards menampilkan total URL, alive, dead, rata-rata hop, rata-rata latency, jumlah error, durasi scan
- [x] Angka-angka di atas dihitung dari `results` yang sebenarnya (bukan hardcode/contoh)
- [x] Card "Dead" dan "Error" tersorot warna beda kalau nilainya > 0
