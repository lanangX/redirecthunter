# CHANGELOG

## 2026-08-23 — v2.5

- **Fix bug: tab Backlink Checker belum menampilkan `matched_target`** — kolom ini baru
  benar-benar tersimpan ke `backlink_results` mulai sesi backend 2026-08-23 (lihat
  `../MEMORY.md`); drawer detail (`openBacklinkDrawer()`) dan Export CSV
  (`exportBacklinkCsv()`) sekarang menyertakannya (posisi setelah "Target"/`target`,
  mengikuti urutan `BACKLINK_RESULT_COLUMNS` di `backlink.py`). Sebelumnya kolom ini
  hilang dari keduanya walau v2.4 sudah mengklaim drawer menampilkan "semua kolom
  `backlink_results`" — klaim itu benar untuk skema saat v2.4 ditulis, tapi jadi salah
  begitu kolom baru ditambahkan di backend tanpa dashboard-nya ikut disentuh.

## 2026-08-22 — v2.4

- **Tab baru "Backlink Checker"** — muncul otomatis kalau `.db` yang dimuat punya data di
  `backlink_checks`/`backlink_results` (skema sudah ada sejak v2.0, tapi tabel itu kosong sampai
  sekarang — lihat MEMORY.md v2.0). Disembunyikan kalau tidak ada, sama seperti tab Crawl.
  Berisi stat cards (Total URL Dicek, Match Ditemukan, Tidak Ditemukan, Hanya Teks/no href,
  Diblokir, Butuh Login, Error), 2 chart (distribusi match type, distribusi status code), tabel
  hasil pengecekan backlink dengan filter (cari source/final URL, match type, status, blocked,
  butuh login) + sort header + pager, drawer detail per baris (semua kolom `backlink_results`),
  dan Export CSV sesuai filter aktif.

## 2026-08-21 — v2.3

- **Boundary checklist "Sembunyikan status" diperbaiki jadi pola hundred-aligned penuh**
  (`X00–X99`), termasuk bucket 1 & 2 yang sebelumnya punya batas ganjil: `≤200` → `≤199`,
  `201–299` → `200–299` (batas 300/400 sudah diperbaiki di v2.2). Sebelumnya status 400 tetap
  tampil walau checklist "401–499" dicentang, karena secara sql 400 masuk grup 301-400, bukan
  401-499 — sekarang partisinya `≤199 / 200–299 / 300–399 / 400–499 / ≥500`, tidak ada celah
  atau tumpang tindih.
  ⚠️ **Efek samping**: status 200 OK sekarang ikut masuk bucket 2 (200–299), yang TIDAK
  disembunyikan secara default — jadi baris status 200 sekarang tampil di tampilan awal,
  beda dari sebelumnya (dulu masuk bucket 1 yang disembunyikan default). Lihat MEMORY.md v2.3.

## 2026-08-21 — v2.2

- **Fix bug: opsi di dropdown Status/Redirect type/Hops/Final URL/Body Link tidak ikut
  menyempit ketika filter lain aktif** — mis. body link yang halamannya 404 tetap muncul di
  dropdown Body Link walau checklist "Sembunyikan status" sudah menyembunyikan bucket
  401-499 dari tabel. Sekarang tiap dropdown menghitung opsinya berdasarkan SEMUA filter
  aktif lainnya (search, checklist status, alive, multi-select lain) kecuali dirinya
  sendiri, dan di-refresh ulang setiap ada filter apapun yang berubah — lihat
  `buildFilterClauses()`/`whereSqlExcluding()` dan catatan di MEMORY.md.
- **Checklist "Sembunyikan status" sekarang punya default**: ≤200, 401–499, ≥500 tersembunyi
  begitu `.db` dimuat (hanya 201–300/301–400 yang tampil) — sebelumnya defaultnya kosong
  (semua status tampil). Ganti file (`Ganti file`) juga kembali ke default ini, bukan kosong.
- **Filter Alive diubah dari dropdown `<select>` jadi checklist** (sejajar dengan "Sembunyikan
  status", label "Tampilkan:") dengan kotak centang Alive/Dead + jumlah baris masing-masing.
  Default: hanya "Alive" yang dicentang.

## 2026-08-21 — v2.1

- Checklist "Sembunyikan status" baru di panel Results (di bawah toggle dedupe/spam): 5 kotak
  centang partisi status code — ≤200, 201–300, 301–400, 401–499, ≥500 — masing-masing menampilkan
  jumlah baris yang akan disembunyikan. Dicentang = disembunyikan dari tabel/export. Baris dengan
  status kosong (NULL) tidak pernah ikut disembunyikan oleh checklist ini, apa pun yang dicentang.

## 2026-08-20 — v2.0

- Skema `.db` yang lebih baru menambahkan `scan_id`/`crawl_id` dan 5 tabel baru: `crawls`,
  `crawl_pages`, `crawl_links` (audit crawl SEO), `backlink_checks`/`backlink_results` (belum
  ada tab-nya — masih kosong di contoh terbaru, akan dibuatkan tab kalau sudah mulai dipakai).
- **Tab baru "Crawl"** — muncul otomatis kalau `.db` yang dimuat punya data crawl (disembunyikan
  kalau tidak ada, supaya file `.db` lama tetap jalan normal). Berisi stat cards (Total Halaman,
  Alive/Dead, rata² word count & latency, Total Isu SEO, Total Link, Link Rusak), 2 chart
  (distribusi status code, isu SEO terbanyak), tabel halaman ter-crawl dengan filter (cari
  URL/title, status, isu SEO, alive) + pager, dan drawer detail per halaman (meta description,
  daftar H1, daftar isu, serta semua link yang ditemukan di halaman itu — internal/eksternal,
  broken, anchor text).
- **Redesain "Perbaikan Body Link"**: perbaikan sekarang di-key oleh nilai `body_link` (bukan
  `source_url`) — satu perbaikan otomatis berlaku ke semua source URL berbeda yang menghasilkan
  body_link yang identik (banyak redirector berbeda sering menghasilkan mangling yang sama
  persis). Bisa diedit langsung dari **drawer** (baris "Body Link" yang bermasalah sekarang
  punya kotak isian + tombol Simpan/Reset dengan live preview), tidak perlu lagi ke tab
  terpisah untuk kasus biasa — tab "Perbaikan Body Link" sekarang jadi alat tinjau/cari saja.
- **Persistensi via localStorage**: perbaikan body link dan daftar spam blacklist kustom
  sekarang benar-benar tersimpan di browser (bukan cuma di sesi tab) — kalau file dibuka lagi
  nanti (bahkan setelah browser ditutup), pengaturan yang sudah dibuat tetap ada.

## 2026-08-19 — v1.8

- Tab baru **"Perbaikan Body Link"**: daftar semua `source_url` unik yang `body_link`-nya tidak
  cocok dengan target scan setelah dinormalisasi (skema/`www.`/port/trailing slash diabaikan) —
  bukan cuma pola protokol ganda dari v1.7, tapi juga kasus lain seperti slug tambahan yang
  menempel setelah target (`{TARGET}/management.html` di dalam template `source_url`) dan
  variasi mangling lain (`http://www.https://…`). Diurutkan dari yang paling banyak baris
  duplikatnya. Untuk tiap baris, isi kolom "Ganti jadi" untuk menetapkan baris export manual —
  dipakai oleh **Export TXT (source URL)** menggantikan hasil substitusi otomatis, berlaku untuk
  semua baris yang berbagi `source_url` yang sama. Perbaikan tersimpan lintas file `.db` selama
  sesi browser (di-reset saat halaman direfresh).
- Drawer detail: catatan pada baris "Body Link" sekarang berbasis deteksi yang sama dengan tab
  Perbaikan Body Link (bukan cuma pola protokol ganda), dan menunjukkan status "sudah diperbaiki
  manual" kalau baris itu sudah pernah ditetapkan lewat tab Perbaikan Body Link.

## 2026-08-19 — v1.7

- **Export TXT (source URL)** sekarang mendeteksi baris dengan `body_link` berpola protokol
  ganda (mis. `http://https://www.medilana.id`) — untuk baris seperti itu, `{TARGET}` di
  `source_url` diganti dengan bentuk domain polos tanpa skema (`www.medilana.id`), bukan URL
  lengkap target scan (`https://www.medilana.id`), supaya URL yang diekspor tidak dobel
  protokol. Export CSV tidak diubah (tetap raw `source_url`/`body_link` apa adanya).
- Drawer detail (klik baris di tabel Results): baris "Body Link" yang kena pola protokol ganda
  sekarang menampilkan catatan kuning kecil yang menjelaskan nilai `{TARGET}` pengganti yang
  akan dipakai saat export.
- Semua URL di drawer detail (Final URL, Body Link, Location, redirect chain) sekarang jadi
  tautan yang bisa diklik dan terbuka di tab baru (`target="_blank"`).

## 2026-08-19 — v1.6

- Filter multi-select baru: **Hops** (`hop_count`, cardinality rendah — 9 nilai unik di contoh
  db terbaru), pola sama seperti Status/Redirect type (`buildMsel`, bukan searchable).
- Perbaikan UX: tombol "Pilih semua"/"Bersihkan" pada semua dropdown multi-select (Status,
  Redirect type, Hops, Final URL, Body Link) dipindah ke **atas** panel (tepat di bawah kotak
  cari, di atas daftar checkbox) — sebelumnya di paling bawah, jadi harus discroll dulu lewat
  seluruh daftar checkbox untuk mencapainya kalau opsinya banyak. Daftar checkbox sekarang
  scroll sendiri di dalam area terbatas, sementara tombol aksi (dan kotak cari, untuk yang
  searchable) tetap terlihat.

## 2026-08-19 — v1.5

- Fitur baru: **filter Spam Blacklist**, toggle "Sembunyikan hasil spam" di panel Results
  (default **aktif**), dicocokkan ke `final_url` + `body_link` (bukan `source_url` — lihat
  alasan di `docs/MEMORY.md`).
- Tab baru "Spam Blacklist" (terpisah dari tab "Results") berisi textarea yang bisa diedit
  langsung, tombol muat dari file `.txt`, unduh `.txt`, kembalikan ke default, dan terapkan
  perubahan. Daftar default sudah memuat `spam_blacklist.txt` yang diberikan (~16.300 pola
  aktif) — bisa diganti/diedit user kapan saja, perubahan hanya tersimpan di sesi browser
  (tidak ikut ke file `.db`), dan tetap dipertahankan saat ganti file `.db` (di-reset hanya
  saat halaman dimuat ulang/direfresh).
- Performa: pencocokan 16k+ pola regex terhadap ribuan baris hasil dioptimasi dengan cache
  per kombinasi unik `final_url`+`body_link` dan regex dipecah jadi chunk ~50 pola — detail
  angka benchmark ada di `docs/MEMORY.md`.

## 2026-08-19 — v1.4

- Fitur baru: filter multi-select "Body Link" pada panel Results (dropdown checkbox dengan
  kotak cari, sama seperti Final URL) — ~660 nilai unik pada contoh db terbaru. Termasuk di
  `buildWhere()`, jadi berlaku ke tabel, export CSV, dan export TXT.
- Toggle "Sembunyikan duplikat" sekarang **default aktif** (sebelumnya default nonaktif) saat
  db baru dimuat atau file diganti — supaya tabel/chart/stat cards langsung bebas duplikat
  tanpa perlu klik toggle dulu. Tetap bisa dimatikan manual per sesi.

## 2026-08-19 — v1.3

- Fitur baru: toggle "Sembunyikan duplikat" pada panel Results — mendeteksi source URL yang
  ter-scan lebih dari sekali (2.040 dari 4.461 URL unik pada contoh db, +3.429 baris ekstra)
  dan, saat diaktifkan, hanya menampilkan 1 baris per source URL (percobaan terakhir/timestamp
  terbaru). Berlaku ke stat cards, chart, tabel, filter, dan export (CSV & TXT).

## 2026-08-19 — v1.2

- Kotak cari dikembalikan jadi satu (gabungan source URL + final URL), sesuai desain awal.
- Filter Final URL sekarang dropdown multi-select seperti Status/Redirect type — dilengkapi
  kotak cari di dalam dropdown karena final URL punya ribuan nilai unik.
- Tabel menampilkan pesan "tidak ada hasil" yang jelas saat kombinasi filter antar kolom
  (mis. Status × Redirect type) tidak match apa pun — ini perilaku AND yang memang benar,
  bukan bug.

## 2026-08-19 — v1.1

- Filter status code dan redirect type jadi multi-select (dropdown checkbox, bisa pilih lebih dari satu nilai).
- Filter final URL terpisah dari filter source URL (dua kotak pencarian).
- Header tabel results bisa diklik untuk sort (asc/desc) per kolom: status, source URL, final URL, type, hops, latency.
- Export TXT baru: daftar source URL sesuai filter aktif, dengan placeholder `{TARGET}` diganti ke domain target scan.

## 2026-08-18 — v1

- Dashboard awal: drag & drop `.db` lokal, baca via sql.js (WASM), tanpa server.
- Overview cards: total URL, alive/dead, rata-rata hop, rata-rata latency, jumlah error, durasi scan.
- Chart distribusi status code dan redirect type.
- Tabel `results` dengan filter (search source/final URL, status code, redirect type, alive/dead),
  pagination 50 baris/halaman, export CSV sesuai filter aktif.
- Drawer detail per-result: metadata lengkap, redirect chain (tabel `chain`), header HTTP mentah
  (tabel `headers`).
