# 04 — Tabel results: search, filter, sort, pagination

**What to build:** User bisa menyisir ribuan baris `results` secara praktis: cari teks bebas
di source URL dan (terpisah) di final URL, filter berdasarkan status code / redirect type
(multi-select — bisa pilih lebih dari satu nilai sekaligus) / alive-dead, sort di tiap kolom
lewat klik header, dan berpindah halaman (50 baris/halaman) — semuanya tetap responsif walau
data ribuan baris, karena difilter & disortir lewat SQL, bukan di JS.

**Blocked by:** 01 — Muat & validasi file .db lokal

**Status:** done

- [x] Kotak pencarian source URL dan kotak pencarian final URL terpisah, dengan debounce supaya tidak lag saat mengetik
- [x] Filter status code multi-select (checkbox dropdown), termasuk opsi "(none)" untuk status_code NULL
- [x] Filter redirect type multi-select (checkbox dropdown)
- [x] Filter alive/dead tersedia dan berfungsi
- [x] Kombinasi beberapa filter sekaligus bekerja (AND antar filter, OR di dalam satu multi-select)
- [x] Klik header kolom (status, source URL, final URL, type, hops, latency) mengurutkan tabel asc/desc, dengan indikator arah di header
- [x] Pagination 50 baris/halaman dengan tombol prev/next dan indikator jumlah hasil & halaman
- [x] Baris tabel menampilkan indikator alive/dead, badge status code berwarna, dan kolom kunci (source, final URL, redirect type, hop count, latency)
