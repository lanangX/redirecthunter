# MEMORY

Catatan keputusan dan konteks yang tidak jelas hanya dari membaca kode. Tambahkan entri baru
di atas (paling baru di atas). Jangan hapus entri lama kecuali sudah tidak relevan sama sekali.

## 2026-08-23 — v2.5: `matched_target` ditambahkan ke drawer & export backlink

- Backend baru menambahkan kolom `matched_target` ke `backlink_results` (lihat repo utama,
  `docs/DATABASE_SCHEMA.md` dan `CHANGELOG.md` bagian `[Unreleased]`) — dashboard belum ikut
  disentuh sampai sesi ini, jadi drawer & CSV export tidak menampilkannya walau baris data
  sudah punya nilainya kalau `.db` yang dimuat sudah dibuat/dimigrasi dengan versi backend
  yang baru. `.db` lama (belum dimigrasi) akan tampil `matched_target` kosong seperti kolom
  nullable lain — bukan bug, konsisten dengan cara `robots_meta`/`robots_header` diperlakukan.
- Ini juga jadi pengingat: entri v2.4 di bawah ("drawer detail: semua kolom `backlink_results`")
  cuma benar relatif ke skema backend *saat v2.4 ditulis*. Kalau `database.py`'s `_SCHEMA_SQL`
  nambah kolom baru ke `backlink_results` lagi di masa depan, jangan asumsikan dashboard ikut
  update otomatis — `openBacklinkDrawer()`'s `metaRows` dan `exportBacklinkCsv()`'s `cols` di
  `index.html` keduanya hard-coded daftar kolom, harus disentuh manual tiap kali skema
  `backlink_results` berubah.

## 2026-08-22 — v2.4: tab Backlink Checker

- **`backlink_checks`/`backlink_results` akhirnya mulai berisi data** — skemanya sudah ada sejak
  v2.0 tapi kosong di contoh saat itu (lihat catatan v2.0 di bawah: "kandidat kuat untuk tab
  berikutnya, kemungkinan mirip [Crawl]"). Contoh `.db` terbaru: 1 baris `backlink_checks`
  (domain `medilana.id`, 43 baris `backlink_results`).
- **Pola tab disalin dari Crawl** (`hasBacklinkData` dicek di `boot()` lewat `sqlite_master`,
  tab button disembunyikan kalau tabelnya tidak ada, `backlinkMeta` = baris `backlink_checks`
  terbaru berdasar `started_at` — sama seperti `crawlMeta`) — tapi draweri-nya lebih sederhana:
  `backlink_results` sudah flat satu baris per URL sumber (tidak ada tabel detail turunan
  seperti `chain`/`crawl_links`), jadi `openBacklinkDrawer` cukup satu `qOne` ke baris itu
  sendiri, tanpa query tambahan.
- **`match_type` yang benar-benar muncul di data**: `anchor`, `final_url_is_target`,
  `text_mention_only`, `not_found` (bukan cuma `not_found`/`text_mention_only` seperti 2 contoh
  di komentar kode sumber RedirectHunter) — `matchTypeColor()` sengaja dibuat permisif (default
  ke warna "alive"/hijau untuk match_type apapun selain `not_found`/`text_mention_only`, bukan
  whitelist ketat 1 warna per nilai) supaya tidak perlu diubah lagi kalau RedirectHunter
  menambah match_type baru di masa depan.
- **Stat card "Hanya Teks (no href)" dan "Diblokir" disorot merah** (`dead`) kalau > 0, meniru
  pola "Dead"/"Total Isu SEO" di tab Crawl — keduanya sinyal negatif (link yang seharusnya ada
  tapi tidak/tidak bisa diverifikasi), bukan status netral.
- **Filter status code TIDAK dipartisi jadi bucket** seperti tab Results (`renderStatusBucketBar`)
  — volume `backlink_results` jauh lebih kecil (puluhan, bukan puluhan ribu) dan cardinality
  status code-nya juga rendah, jadi multi-select checklist biasa (`buildMsel`, bukan
  `buildSearchableMsel`) sudah cukup tanpa perlu UX bucket tambahan.
- **Export**: hanya CSV (semua kolom `backlink_results`), tidak ada Export TXT seperti tab
  Results — TXT di tab Results khusus untuk kasus `{TARGET}` placeholder di `source_url` scan
  redirect, yang tidak relevan untuk hasil pengecekan backlink.

## 2026-08-21 — v2.3: boundary status bucket dibuat hundred-aligned penuh

- **Membalik sebagian keputusan v2.1** ("boundary 200/300/400/500 dipilih dari 2 titik yang
  diberikan user: `<=200` dan `>=500`, sisanya `201-300`/`301-400`/`401-499` mengisi celah"):
  user melaporkan status 400 tetap tampil walau checklist "401-499" dicentang — sesuai desain
  v2.1 itu memang benar (400 masuk grup 301-400, bukan 401-499), tapi user menganggap ini
  janggal dan minta pola konsisten `X00-X99` untuk SEMUA bucket, termasuk bucket 1 (`≤200` →
  `≤199`) dan bucket 2 (`201-299` → `200-299`). v2.2 sempat baru membetulkan batas 300/400
  saja (301-400/401-499 → 300-399/400-499); giliran user diminta juga membetulkan bucket 1/2,
  jadilah v2.3 ini. Entri v2.1 di bawah DIPERTAHANKAN sebagai riwayat kenapa boundary awal
  dipilih begitu, tapi sudah tidak berlaku.
- **Partisi final**: `≤199 / 200–299 / 300–399 / 400–499 / ≥500` — hundred-aligned murni, tidak
  ada pengecualian di bucket manapun (beda dari v2.2 yang masih mengecualikan bucket 1 di
  `≤200`).
- **⚠️ Efek samping yang perlu diingat lain kali ada pertanyaan "kenapa status 200 tampil
  padahal biasanya disembunyikan"**: karena batas bucket 1 turun dari `≤200` ke `≤199`, status
  200 pindah dari bucket 1 (disembunyikan default) ke bucket 2 (TIDAK disembunyikan default,
  `DEFAULT_HIDDEN_STATUS_BUCKETS` masih `['1','4','5']`, tidak diubah). Jadi baris "200 OK"
  sekarang ikut muncul di tampilan awal, beda dari sebelum v2.3. Ini flagged ke user saat
  perubahan dibuat, belum ada keputusan eksplisit apakah default perlu diubah supaya
  perilaku "hanya tampilkan redirect di awal" tetap sama persis seperti sebelumnya — kalau
  user mengeluh soal ini lagi, opsinya: tambahkan `'2'` ke `DEFAULT_HIDDEN_STATUS_BUCKETS`.

## 2026-08-21 — v2.2: dropdown filter jadi "faceted" (saling menyempitkan)

- **Membalik keputusan v2.1 di bawah** ("Perhitungan jumlah per-checkbox ... bukan hasil filter
  toolbar lainnya ... konsisten dengan bagaimana count di dropdown filter lain juga tidak
  memperhitungkan filter toolbar yang lain"): itu ternyata dianggap bug oleh user, bukan
  perilaku yang diinginkan — contoh konkret yang dilaporkan: body link yang halamannya 404
  tetap nongol di dropdown Body Link walau bucket 401-499 sudah disembunyikan lewat checklist
  status. Entri v2.1 ini DIPERTAHANKAN sebagai riwayat (kenapa awalnya dibuat begitu), tapi
  perilakunya sudah tidak berlaku — lihat implementasi baru di bawah.
- **Pola baru**: `buildFilterClauses(f)` menghasilkan satu klausa SQL bertag `key` per facet
  (search/statuses/types/hops/finalUrls/bodyLinks/alive/statusBuckets). `whereSqlExcluding(keys)`
  merakit ulang jadi WHERE, membuang facet yang ada di `keys` — dipakai untuk: query tabel utama
  (`whereSqlExcluding([])`), opsi tiap dropdown (`whereSqlExcluding(['facet-itu-sendiri'])` —
  supaya user masih bisa uncheck opsi yang sudah dipilihnya sendiri), dan angka di
  `statusBucketBar`/`aliveBar` (exclude facet masing-masing). `populateFilters()` sekarang
  dipanggil dari `applyFilters()` (bukan cuma load/dedupe/spam toggle) sehingga jalan ulang
  setiap ada perubahan filter apapun.
- **Trade-off performa yang disadari**: ini berarti tiap klik checkbox filter menjalankan ~7
  query `GROUP BY`/`COUNT` tambahan (5 dropdown + 2 bar), bukan cuma 1 query tabel. Untuk
  volume yang sudah diuji (lihat entri v1 di bawah, ~7.890 baris `results`) tidak terasa —
  kalau nanti db jauh lebih besar (>100k baris) dan terasa lag saat toggle filter, ini titik
  pertama yang perlu dioptimasi (mis. debounce populateFilters, atau index tambahan).
- **Kenapa dropdown Final URL/Body Link (`buildSearchableMsel`) direfactor supaya TIDAK
  rebuild total tiap panggilan**: karena sekarang dipanggil ulang di *setiap* perubahan filter
  apapun (termasuk saat user sedang mengetik di kotak cari sendiri), rebuild total akan
  menghapus teks yang sedang diketik user tiap kali. Sekarang kotak `<input>` pencarian dibuat
  sekali saja (disimpan di `panel._msel`), panggilan berikutnya cuma ganti data opsi dan
  render ulang daftarnya — kotak cari & fokus tidak ke-reset.
- **Alive jadi checklist inklusif (bukan "hide" seperti status bucket)**: dicentang = TAMPIL
  (bukan disembunyikan), 2 kotak (`Alive`/`Dead`), default cuma `Alive` yang dicentang. Kalau
  keduanya dicentang atau keduanya kosong = tidak ada filter alive (semantik sama dengan
  Set kosong pada multi-select lain di dashboard ini) — dipilih supaya tidak ada state
  "0 baris karena filter kontradiktif" hanya dari kombinasi checkbox alive sendirian.
- **Default checklist "Sembunyikan status" berubah dari kosong jadi `['1','4','5']`**
  (`DEFAULT_HIDDEN_STATUS_BUCKETS`) — permintaan eksplisit user, supaya begitu buka dashboard
  langsung fokus ke baris redirect (301-400) tanpa perlu centang manual dulu.

## 2026-08-21 — v2.1: checklist sembunyikan status code

- **Kenapa partisi ≤200/201–300/301–400/401–499/≥500, bukan 1xx/2xx/3xx/4xx/5xx murni**: user
  memberi 2 contoh eksplisit — "checklist 5 = status 500 ke atas disembunyikan" dan "checklist 1
  = status di bawah sama dengan 200 disembunyikan". Kalau dipetakan ke kelas HTTP standar
  (1xx/2xx/dst), checklist 1 seharusnya cuma 100-199 — TIDAK termasuk 200 — tapi user eksplisit
  bilang termasuk 200. Supaya tidak ada celah atau tumpang tindih di titik batas, dipilih partisi
  yang pas dengan KEDUA titik yang diberikan lalu diisi 2/3/4 secara konsisten: `<=200`,
  `201-300`, `301-400`, `401-499`, `>=500` — pas persis dengan 2 titik yang diberikan user
  (checklist 1 di titik 200, checklist 5 di titik 500) tanpa saling tumpang tindih di batas.
- **Bug 3-valued-logic yang HAMPIR lolos**: awalnya diimplementasi sebagai
  `NOT (status_code <= 200)` per bucket yang dicentang, di-AND-kan ke klausa lain. Ternyata di
  SQL, `NOT (NULL <= 200)` = `NULL` (bukan `TRUE`), dan `NULL AND <apa pun>` = `NULL` → baris
  dengan `status_code IS NULL` (request yang gagal total, tidak ada respons/status) akan IKUT
  tersembunyi begitu ADA saja bucket yang dicentang — padahal NULL tidak masuk bucket manapun
  dan seharusnya SELALU tampil. Diperbaiki dengan membungkus jadi satu klausa gabungan:
  `(status_code IS NULL OR NOT (bucket1_sql OR bucket2_sql OR ...))` — divalidasi ke `.db` asli:
  6.894 baris `NULL` tetap 6.894 setelah bucket 1 & 5 dicentang bersamaan (harusnya begitu),
  dan total baris tersisa (8.939) matematisnya pas dengan `total - count(bucket1) - count(bucket5)`.
- **Perhitungan jumlah per-checkbox** (`renderStatusBucketBar()`) memakai basis yang sama dengan
  dedupe-bar/spam-bar (`resultsTable()` + `spamWhereFragment`) — bukan hasil filter toolbar
  lainnya (search/status-msel/dst) — konsisten dengan bagaimana count di dropdown filter lain
  juga tidak memperhitungkan filter toolbar yang lain.

## 2026-08-20 — v2.0: tab Crawl + override key body_link + localStorage persistence

- **Schema baru dari `.db` terbaru**: `results` dapat kolom `scan_id` (masih 1 nilai unik per
  db, jadi TIDAK perlu filter khusus — kalau nanti multi-scan beneran dipakai, semua query
  `results`/`chain`/`headers` harus ditambah `WHERE scan_id = ?`) dan `expanded_url` (source_url
  yang `{TARGET}`-nya sudah otomatis disubstitusi oleh RedirectHunter sendiri — TIDAK dipakai
  di dashboard karena logika kita sendiri sudah menghitung ulang hal yang sama plus menangani
  override/protokol-ganda; kalau suatu saat mau disederhanakan, `expanded_url` bisa jadi
  starting point verifikasi). `result_id`/`chain.result_id`/`headers.result_id` sekarang TEXT
  (UUID) bukan INTEGER — tervalidasi tidak masalah karena kode selalu memperlakukannya sebagai
  opaque string (parameter terikat, `dataset.id`), tidak pernah di-parse sebagai angka.
- **5 tabel baru**: `crawls`/`crawl_pages`/`crawl_links` (dipakai, lihat di bawah) dan
  `backlink_checks`/`backlink_results` (skema ada tapi masih 0 baris di contoh — belum dibuatkan
  tab; kalau nanti mulai berisi data, ini kandidat kuat untuk tab berikutnya, kemungkinan mirip
  pola tab Crawl: cek tabel ada dulu, baru tampilkan tab).
- **Tab Crawl disembunyikan otomatis kalau tidak ada data**: `hasCrawlData` dicek di `boot()`
  lewat `sqlite_master` (butuh ketiga tabel `crawls`+`crawl_pages`+`crawl_links` ada) — supaya
  file `.db` lama (skema sebelum v2.0) tetap jalan normal tanpa tab Crawl nongol kosong/error.
  `crawlMeta` diambil dengan `ORDER BY started_at DESC LIMIT 1` (ambil crawl TERBARU kalau
  ternyata ada lebih dari satu crawl tersimpan dalam satu file — belum terjadi di contoh, tapi
  jaga-jaga).
- **`issues_json`/`h1_json` di `crawl_pages`**: JSON array of string biasa (bukan objek
  bertingkat), di-parse di JS (`JSON.parse`, dibungkus try/catch untuk baris yang rusak) —
  sengaja TIDAK pakai ekstensi `json1`/`json_each` SQLite untuk GROUP BY di level SQL, supaya
  tidak bergantung pada apakah build sql.js yang dipakai include json1 atau tidak. Tally isu SEO
  (`loadCrawlIssueTally()`) dan filter `issues_json LIKE '%"kode_isu"%'` di `buildCrawlWhere()`
  keduanya menghindari ekstensi itu dengan cara yang sama.
- **Link halaman di drawer Crawl**: `crawl_links.source_page_url` adalah join key ke
  `crawl_pages.url` (bukan lewat page_id) — divalidasi terhadap `.db` asli: jumlah baris
  `crawl_links` per `source_page_url` persis sama dengan `internal_link_count +
  external_link_count` di baris `crawl_pages`-nya (195 = 194+1, 161 = 160+1, 106 = 91+15).
- **`buildMsel()`/`buildSearchableMsel()` sekarang menerima `applyFn` sebagai parameter
  terakhir**, bukan hardcode manggil `applyFilters()` — ini WAJIB diubah supaya msel filter
  Crawl (`crawlStatusMselPanel`/`crawlIssuesMselPanel`) tidak salah manggil `applyFilters()`
  (punya tab Results) alih-alih `applyCrawlFilters()`. Semua pemanggilan lama sudah diupdate
  untuk mengoper `applyFilters` secara eksplisit. Kalau nanti nambah msel baru di tab manapun,
  WAJIB isi parameter ini dengan fungsi apply yang benar untuk tab itu — jangan andalkan default
  tersembunyi lagi.
- **`th.sortable` di-scope per tab**: sebelumnya `document.querySelectorAll('th.sortable')` di
  `bindEvents()` tidak diberi scope, cuma cocok untuk tabel Results karena itu satu-satunya
  tabel yang ada saat itu ditulis. Sekarang discope jadi `#tabResults th.sortable` dan
  `#tabCrawl th.sortable` terpisah, masing-masing manggil `fetchPage()`/`fetchCrawlPage()` dan
  `sortState`/`crawlSortState` sendiri-sendiri.
- **Redesain override "Perbaikan Body Link" — key body_link, bukan source_url**: user melaporkan
  perbaikan v1.8 (key source_url) berarti harus mengedit ULANG untuk setiap template redirector
  berbeda meski body_link yang dihasilkan identik (mis. puluhan Google-TLD url-redirector
  semuanya menghasilkan `body_link` = "https://www.medilana.id/management.html" yang PERSIS
  sama). Divalidasi terhadap `.db`: satu body_link bisa dihasilkan oleh puluhan source_url
  templates sekaligus (kasus nyata: 1 override langsung memperbaiki ratusan baris + puluhan
  template berbeda). `bodyLinkOverrides` sekarang `Map<body_link, {TARGET}_substitution_value>`
  (bukan `Map<source_url, whole_export_line>` seperti v1.8) — nilai override adalah SUBSTITUSI
  untuk `{TARGET}` saja, bukan baris export utuh, supaya tetap komposabel dengan teks apa pun
  yang mengelilingi `{TARGET}` di template masing-masing baris.
- **Keterbatasan yang diketahui**: kalau teks SETELAH `{TARGET}` di template menempel LANGSUNG
  tanpa pemisah (mis. `...q=https://{TARGET}management.html` — tidak ada `/` sebelum
  "management.html"), override nilai substitusi TIDAK BISA menghasilkan URL yang benar-benar
  bersih menunjuk homepage — hasil terbaik yang bisa dicapai lewat override substitusi saja
  tetap akan menunjuk ke subpage/gabungan aneh. Ini keterbatasan desain (override cuma
  mengganti apa yang ada DI POSISI `{TARGET}`, tidak bisa mengubah teks template di sekitarnya),
  bukan bug — kalau user butuh perbaikan sempurna untuk kasus ini, satu-satunya cara adalah
  edit `source_url` (template-nya sendiri), yang saat ini TIDAK didukung fitur ini (baik drawer
  maupun tab hanya mengedit nilai substitusi, bukan template).
- **Editing dipindah ke drawer**: baris "Body Link" yang bermasalah (`bodySuspicious`, dicek
  dengan `normalizeForCompare()` yang sama dipakai tab) sekarang render kotak isian + tombol
  Simpan/Reset langsung di drawer (bukan cuma catatan baca-saja seperti v1.8) — event listener
  di-bind manual setelah `innerHTML` di-set (bukan lewat atribut `onclick` inline, supaya tidak
  perlu escaping value URL yang rawan berisi karakter aneh). Simpan/Reset memanggil
  `openDrawer(resultId)` lagi di akhir untuk me-refresh seluruh state drawer (badge, preview,
  tombol) dari satu sumber kebenaran, alih-alih menduplikasi logika re-render.
- **localStorage** (`LS_PREFIX = 'redirecthunter_dashboard_v1_'`): dipakai untuk
  `bodyLinkOverrides` (JSON object) dan `blacklistText` (raw string) — ini file HTML mandiri
  yang di-download dan dibuka user di browser sendiri (BUKAN artifact claude.ai), jadi
  localStorage tersedia dan cocok dipakai di sini. Dibungkus try/catch (`lsGet`/`lsSet`) karena
  beberapa browser/setting bisa menonaktifkan storage untuk origin `file://` — kalau gagal,
  fallback diam-diam ke perilaku in-memory saja (fitur tetap jalan dalam satu sesi, cuma tidak
  bertahan setelah ditutup).

## 2026-08-19 — v1.8: tab Perbaikan Body Link (override manual per source_url)

- **Kenapa deteksi v1.7 (regex protokol ganda) tidak cukup**: dicek lagi terhadap `.db`, ada
  657 nilai `body_link` unik yang "tidak bersih" dan cuma sebagian kecil yang polanya
  `http://https://...`. Variasi lain yang ditemukan: `http://www.https://www.medilana.id`
  (ada `www.` disisipkan sebelum protokol kedua — regex `^https?://https?://` v1.7 TIDAK
  menangkap ini), `https://www.medilana.id/management.html` (bukan bug di pihak target site —
  ternyata TEMPLATE `source_url`-nya sendiri sudah punya slug nempel tepat setelah placeholder,
  mis. `http://images.google.gp/url?q={TARGET}/management.html` — begitu `{TARGET}` diganti,
  hasilnya jadi menunjuk ke subpage, bukan homepage), dan banyak yang genuinely TIDAK BISA
  diperbaiki karena memang landing di halaman generik milik layanan itu sendiri (error
  Cloudflare, halaman dukungan Google, dsb — placement yang gagal, bukan target yang rusak).
  Kesimpulan: tidak ada satu regex yang menangkap semua kasus, makanya diikuti ide user untuk
  bikin alat tinjau manual, bukan coba bikin auto-fix yang makin rumit.
- **Deteksi "perlu ditinjau"**: `normalizeForCompare()` (strip skema/`www.`/port/trailing
  slash, lalu lowercase) dibandingkan ke target scan yang dinormalisasi sama — jauh lebih
  longgar dari regex v1.7, sengaja menangkap kasus lain yang mungkin tidak terpikirkan juga.
  Ini LEBIH LUAS dari deteksi `hasDoubleProtocolBodyLink()` v1.7 (yang tetap dipakai sebagai
  DEFAULT export line kalau user belum menetapkan override manual).
- **Kenapa dikelompokkan per `source_url`, bukan per baris**: satu template `source_url`
  (probe URL, mis. `http://images.google.gp/url?q={TARGET}/management.html`) dipakai berulang
  di banyak baris (scan yang sama diulang, atau scan berbeda tapi daftar probe URL-nya sama).
  Perilaku mangling itu properti dari LAYANAN/TEMPLATE-nya, bukan acak per baris — representasi
  `body_link` diambil dari `MIN(body_link)` per grup (asumsi: perilaku layanan konsisten antar
  percobaan; kalau berubah dari waktu ke waktu, contoh yang ditampilkan cuma salah satu variasi,
  tapi override yang ditetapkan tetap berlaku ke SEMUA baris dengan `source_url` sama).
  `bodyLinkOverrides` (Map: `source_url` → baris export pengganti) sengaja TIDAK direset saat
  ganti file `.db` — sama seperti blacklist, daftar probe URL biasanya dipakai ulang di scan
  berikutnya jadi perbaikan yang sudah ditetapkan tetap berguna.
- **Kolom "Ganti jadi"**: defaultnya diisi otomatis dengan `computeDefaultExportLine()` (baris
  yang AKAN diekspor kalau tidak diapa-apakan), bukan kosong — supaya user tinggal EDIT dari
  situ (mis. hapus `/management.html`) daripada mengetik ulang seluruh URL dari nol. Kalau
  user mengembalikan isi kolom persis sama dengan default, override dihapus otomatis (baris
  dianggap "belum diperbaiki" lagi) — dicek di listener `change` pada `.fix-input`.
- **`computeExportLine()`**: satu fungsi bersama dipakai `exportTxt()`, drawer (`openDrawer`),
  dan tab ini sendiri — cek `bodyLinkOverrides` dulu, baru fallback ke
  `computeDefaultExportLine()`. Kalau logika default berubah lagi ke depannya, cukup ubah di
  satu tempat ini.
- **Skala**: ~1.000-an `source_url` unik butuh ditinjau dari total ~4.400-an unik di contoh
  `.db` — makanya tab ini pakai tabel dengan pager (50/halaman, sama seperti tabel Results) dan
  kotak cari (filter substring di `source_url`/`body_link`), diurutkan dari jumlah baris
  terbanyak (paling berdampak) ke paling sedikit, BUKAN alfabetis atau urutan insert.

## 2026-08-19 — v1.7: normalisasi target saat export TXT + link target=_blank di drawer

- **Penemuan kunci**: `source_url` di skema BUKAN url yang sudah jadi — isinya template mentah
  dengan placeholder literal `{TARGET}` (mis. `http://jpn1.fukugan.com/rssimg/cushion.php?url=
  {TARGET}`). `exportTxt()` (sudah ada sejak awal) mengganti `{TARGET}` dengan `scan.target`
  (target satu-satunya untuk seluruh scan, disimpan di tabel `scan`, bukan per-baris) untuk
  menghasilkan URL siap-pakai. `final_url` adalah versi yang SUDAH disubstitusi oleh
  RedirectHunter sendiri saat scan — jadi untuk lihat "apa yang sebenarnya dikirim", cek
  `final_url`, bukan `source_url` mentah.
- **Bug protokol ganda**: beberapa layanan redirector (`fukugan.com`, `kpsearch.com`,
  `country-retreats.com`, dst — cek `docs/CONTEXT.md`/`spec` untuk daftar lengkap kalau ada)
  menambahkan `http://` sendiri di depan value yang mereka terima, meskipun value itu sudah
  punya skema (`https://...`). Hasilnya `body_link` yang di-scrape dari halaman jadi
  `http://https://www.medilana.id` — bukti nyata bahwa utk layanan spesifik ini, target harus
  dikirim TANPA skema supaya tidak dobel. `exportTxt()` sekarang mendeteksi pola ini per baris
  (regex `^https?:\/\/https?:\/\//i` pada `body_link`) dan mengganti `{TARGET}` dengan
  `stripProtocol(scan.target)` (mis. `www.medilana.id`) khusus untuk baris itu — baris lain
  tetap pakai `scan.target` penuh (`https://www.medilana.id`). Fungsi helper: `stripProtocol()`,
  `hasDoubleProtocolBodyLink()`. Tervalidasi terhadap `.db` asli: 12 baris kena pola ini dari
  contoh terbaru, semuanya tervalidasi menghasilkan `www.medilana.id` di baris ekspor.
- **Kenapa CSV tidak diubah**: CSV (`exportCsv()`) menampilkan `source_url`/`body_link` mentah
  sebagai data audit/debug, bukan daftar siap-submit — normalisasi ini cuma relevan untuk
  "Export TXT (source URL)" yang memang tujuannya menghasilkan URL yang bisa langsung dipakai.
- **Drawer**: baris "Body Link" yang kena pola ini menampilkan catatan kuning
  (`.body-link-note`) berisi nilai `{TARGET}` pengganti yang sebenarnya akan dipakai saat
  export, supaya user tidak perlu menebak — dihitung dari `scanMeta.target` yang sama persis
  dipakai `exportTxt()`, jadi kalau logikanya berubah di satu tempat, update juga tempat lain.
- **Link `target="_blank"` di drawer**: value URL apa pun di drawer (Final URL, Body Link,
  Location, tiap hop redirect chain + `location_header`-nya) sekarang dibungkus `<a>` via
  helper `linkifyValue()` (deteksi sederhana: mulai dengan `http://`/`https://`) dan dikasih
  `target="_blank" rel="noopener noreferrer"`. Judul drawer (`source_url` mentah dengan
  `{TARGET}` literal) sengaja TIDAK dijadikan link karena bukan URL yang bisa langsung dibuka.

## 2026-08-19 — v1.6: tombol aksi msel dipindah ke atas + filter Hops

- User melaporkan tombol "Bersihkan" di dropdown multi-select terasa merepotkan karena ada di
  paling bawah panel — panel (`.msel-panel`) sebelumnya satu blok scroll tunggal (search + hint
  + daftar checkbox + tombol aksi semua ikut scroll bareng), jadi kalau daftarnya panjang harus
  discroll penuh dulu untuk sampai ke "Bersihkan". Diperbaiki dengan mengubah `.msel-panel` jadi
  flex column: search box, `.msel-actions`, dan `.msel-hint` fixed (`flex:0 0 auto`) di bagian
  atas, sementara `.msel-opts` (daftar checkbox) jadi elemen yang scroll sendiri
  (`flex:1 1 auto; overflow-y:auto`). `buildMsel()`/`buildSearchableMsel()` disusun ulang supaya
  urutan HTML-nya cocok (actions sebelum opts). Kalau ada dropdown msel baru ke depannya, ikuti
  urutan ini — jangan taruh `.msel-actions` di akhir `innerHTML` lagi.
- Filter "Hops" ditambahkan dengan `buildMsel` (bukan searchable) karena `hop_count` di contoh
  db cuma ada 9 nilai unik (0-10, kebanyakan 0 dan 1) — cardinality-nya setara Status/Redirect
  type, bukan Final URL/Body Link.

## 2026-08-19 — v1.5: filter Spam Blacklist (toggle + tab editor)

- **Kolom yang dicocokkan**: hanya `final_url` + `body_link`, **BUKAN** `source_url`. Dites
  dulu dengan `source_url` ikut — ternyata pola `\bgoogle\..*?/url\?.*` (dan pola redirector
  generik lain) match ke `source_url` itu sendiri, karena `source_url` di RedirectHunter
  adalah URL probe buatan tool ini sendiri (mis. `http://images.google.ae/url?q={TARGET}`),
  bukan URL yang "ditemukan" di web. Kalau `source_url` ikut dicocokkan, toggle akan
  menyembunyikan hasil berdasarkan template probe-nya sendiri, bukan berdasarkan konten
  spam yang sebenarnya ditemukan. Kalau nanti ada kolom probe baru dengan pola serupa,
  pertimbangkan hal yang sama sebelum memasukkannya ke `IS_SPAM(...)`.
- **Kenapa dipecah jadi chunk regex, bukan satu regex besar**: dibenchmark terhadap `.db`
  asli (15.780 baris, blacklist 16.321 pola valid dari `spam_blacklist.txt` yang diberikan):
  - Loop 16k regex individual per baris: ~12.5 detik/scan penuh — terlalu lambat.
  - Satu regex gabungan dari semua 16k pola (`new RegExp(patterns.join('|'))`): ~124 detik
    untuk scan kombinasi unik saja (backtracking pada alternation sebesar itu sangat mahal
    di V8) — jangan pernah gabungkan jadi satu regex besar.
  - Dipecah per chunk pola lalu di-test berurutan (early-exit begitu ada match): waktu
    terbaik didapat di **CHUNK=50** (~1.9 detik untuk compute awal atas semua kombinasi unik
    `final_url`+`body_link`). CHUNK lebih besar (100/150/300/500) justru lebih lambat
    (9-30 detik) — kemungkinan karena alternation yang lebih panjang per regex individual
    lebih mahal untuk di-backtrack meskipun jumlah regex lebih sedikit. Jangan naikkan
    CHUNK tanpa benchmark ulang di `.db` nyata.
- **Kenapa ada cache (`spamCache`)**: `.db` contoh punya 15.780 baris tapi cuma ~3.954 kombinasi
  unik `final_url`+`body_link` (banyak duplikat, mis. `https://www.medilana.id/` muncul 9.243
  kali). `isSpamRow()` memoize hasil per kombinasi (key = `final_url + '\x01' + body_link`),
  jadi compute berat (~2 detik) cuma kena SEKALI per kombinasi baru, bukan per baris — scan
  ulang setelah cache terisi turun ke ~15-20ms. Cache di-clear saat blacklist diedit+diterapkan
  (`applyBlacklistFromTextarea`) dan saat ganti file `.db` (`reloadBtn`), TAPI blacklist teks
  itu sendiri (`blacklistRawText`) sengaja TIDAK direset saat ganti `.db` — daftar blacklist
  bersifat lintas-scan (dikelola user), bukan bagian dari satu file `.db`.
- **Integrasi ke SQLite**: dipasang lewat `db.create_function('IS_SPAM', ...)` (API sql.js),
  didaftarkan ulang tiap kali db baru dimuat (`registerSpamFunction()` dipanggil di
  `handleFile()`, karena `create_function` terikat per instance `Database`, bukan global).
  Konsekuensinya: `IS_SPAM(final_url, body_link)` bisa dipakai langsung di klausa `WHERE` SQL
  apa pun (dites juga digabung dengan `results_dedup` VIEW dan `GROUP BY` — semua jalan
  normal), bukan cuma difilter di JS sisi client setelah data ditarik.
- **Kenapa list default di-embed base64, bukan teks polos**: `spam_blacklist.txt` aslinya
  UTF-8 dan penuh backslash (`\b`, `\.`, dst — sintaks regex). Kalau ditaruh mentah di dalam
  template literal JS (`` `...` ``), `\b` akan diinterpretasi sebagai karakter backspace
  (escape sequence bawaan JS), merusak semua pola yang pakai word-boundary. Base64 di dalam
  `<script type="text/plain" id="defaultBlacklistB64">` menghindari isu ini sepenuhnya (tidak
  perlu escaping apa pun), didekode saat load lewat `atob()` + `TextDecoder('utf-8')` (bukan
  `decodeURIComponent(escape(...))` yang deprecated). Sudah diverifikasi round-trip byte-per-byte
  identik dengan file aslinya.
- **UI**: tab "Spam Blacklist" terpisah dari tab "Results" (dua `.tab-panel`, di-toggle lewat
  `switchTab()`) karena permintaan eksplisit user — daftar ini "bisa diubah user" jadi butuh
  ruang edit sendiri, bukan cuma dropdown filter seperti Status/Type/Final URL/Body Link.
  Tombol "Terapkan perubahan" sengaja tidak auto-jalan saat mengetik (tidak ada listener
  `input`) karena compile+cache-clear bisa makan ~2 detik — auto-apply per keystroke akan
  bikin UI macet tiap ketukan.

## 2026-08-19 — v1.4: filter Body Link + dedupe default ON

- Filter "Body Link" ditambahkan dengan pola yang sama persis dengan Final URL: multi-select
  searchable (`buildSearchableMsel`), karena `body_link` punya ~660 nilai unik dari ~13rb baris
  non-null di contoh db terbaru — jauh lebih tinggi dari status_code/redirect_type (cardinality
  rendah), meski lebih rendah dari final_url (~3.6-3.8rb). Kotak cari di dalam dropdown tetap
  dipertahankan walau cardinality-nya lebih rendah dari final_url, supaya pola interaksi filter
  di toolbar konsisten (lihat juga catatan v1.2 soal kapan pakai `buildMsel` vs
  `buildSearchableMsel`). `buildSearchableMsel` sekarang menerima parameter `placeholder`
  (sebelumnya hardcode teks "cari final URL…") supaya bisa dipakai ulang untuk kolom lain.
  `body_link` bisa NULL (2.699 dari 15.780 baris di contoh db) — direpresentasikan sebagai
  `__null__` di Set, pola yang sama dengan filter lain yang bisa NULL.
- Toggle dedupe (`dedupeOn`) sekarang default `true`, bukan `false` — permintaan user supaya
  dashboard selalu bebas duplikat begitu db dimuat, tanpa perlu klik toggle manual tiap kali.
  Konsekuensi teknis: `ensureDedupView()` hanya dipanggil kalau `dupSourceCount > 0` (lihat
  `boot()`), jadi kalau db yang dimuat TIDAK punya source URL duplikat, `dedupeOn` tetap `true`
  tapi TEMP VIEW `results_dedup` tidak pernah dibuat. `resultsTable()` diubah jadi cek
  `dedupeOn && dedupeViewReady` (bukan `dedupeOn` saja) supaya tidak query ke view yang belum
  ada di kasus itu — fallback otomatis ke `results`. Reset saat ganti file (`reloadBtn`) juga
  diubah balik ke `dedupeOn = true` (bukan `false`) supaya konsisten tiap db baru dimuat.

## 2026-08-19 — v1.3: dedupe source URL

- Data sumber punya banyak baris `results` dengan `source_url` yang sama persis (2.040 dari
  4.461 URL unik di contoh db) — dicek manual: ini percobaan berulang ke URL yang sama dalam
  satu `scan_id`, timestamp berbeda beberapa ratus milidetik, bukan data salah/korup.
- Solusinya `results_dedup`, TEMP VIEW SQLite dibuat sekali via `ensureDedupView()` (pakai
  window function `ROW_NUMBER() OVER (PARTITION BY source_url ORDER BY timestamp DESC,
  result_id DESC)`, ambil `rn=1`) — bukan dihapus permanen dari data. Toggle "Sembunyikan
  duplikat" di UI cuma switch antara query `FROM results` vs `FROM results_dedup` lewat
  `resultsTable()`; data mentah tidak pernah diubah.
- Aturan "ambil yang mana": timestamp terbaru (percobaan terakhir dianggap paling representatif
  keadaan URL itu). Kalau user minta aturan lain (mis. ambil yang alive, atau yang latency
  tercepat), ganti `ORDER BY` di dalam `ROW_NUMBER()` — jangan bikin toggle baru untuk tiap
  aturan, itu satu keputusan yang harus konsisten di seluruh dashboard.
- Penting: SQLite VIEW hasil JOIN tidak mengekspos `rowid` asli (selalu NULL kalau di-select) —
  ini yang bikin `orderClause()` diubah default-nya dari `ORDER BY rowid` ke `ORDER BY
  result_id`. Jangan pakai `rowid` lagi di query manapun yang bisa jalan di atas `results_dedup`.
- Drawer detail (`openDrawer`) sengaja TETAP query ke tabel `results` asli (bukan
  `results_dedup`) by `result_id` — baris yang diklik di tabel (baik mode dedupe aktif atau
  tidak) selalu baris asli yang valid, jadi tidak perlu ikut switch tabel.

## 2026-08-19 — v1.2: final URL jadi multi-select, filter vs sort AND-semantics

- Final URL awalnya sempat dibuat kotak cari terpisah (v1.1), lalu direvisi jadi multi-select
  seperti Status/Redirect type karena user ingin pola interaksi yang konsisten (pilih dari
  daftar, bukan ketik bebas). Karena `final_url` punya ~3.600 nilai unik dari ~7.900 baris
  (jauh lebih tinggi dari status_code/redirect_type), `buildSearchableMsel` di `index.html`
  menambahkan kotak cari di dalam dropdown-nya untuk mempersempit daftar sebelum centang —
  `buildMsel` biasa (tanpa cari) dipertahankan untuk Status/Redirect type yang cardinality-nya
  rendah. Kalau nanti ada kolom lain untuk multi-select, pilih salah satu builder berdasarkan
  perkiraan jumlah nilai unik, bukan otomatis pakai yang searchable untuk semua.
- Status dan Redirect type sengaja TETAP filter terpisah (AND), bukan digabung jadi satu
  kontrol — user sempat mempertanyakan ini karena kombinasi yang tidak match menghasilkan
  0 baris (mis. redirect_type=301 AND status_code=200). Itu perilaku yang benar (kedua kolom
  independen), bukan bug — solusinya pesan "tidak ada hasil" di tabel (`renderTable`), bukan
  menggabung filter. Jangan gabungkan kedua filter ini jadi satu kontrol tanpa alasan baru
  dari user.
- Kotak cari (`combinedSearch`) tetap satu, gabungan `source_url OR final_url LIKE`, sesuai
  desain v1 awal — sempat dipecah jadi dua kotak terpisah di v1.1 lalu dikembalikan.

## 2026-08-19 — v1.1: multi-select filter, sort header, export TXT

- Filter status/redirect type diubah dari `<select>` tunggal jadi komponen multi-select
  custom (checkbox dropdown, lihat `buildMsel`/`bindMsel` di `index.html`) — bukan
  `<select multiple>` native karena UX-nya buruk untuk pilih banyak sambil tetap lihat opsi lain.
  `status_code IS NULL` direpresentasikan sebagai value string `__null__` di Set — kalau
  nambah multi-select baru untuk kolom yang bisa NULL, pakai pola yang sama.
- Sort ada di header tabel (klik kolom), bukan di toolbar terpisah — sesuai permintaan user
  supaya filter di toolbar, sort di header. `sortState` global, satu kolom aktif dalam satu
  waktu (bukan multi-column sort) — cukup untuk kebutuhan saat ini.
- Export TXT sengaja hanya `source_url` (bukan semua kolom seperti CSV) karena use case-nya
  beda: CSV untuk analisis lanjutan, TXT untuk daftar URL siap-pakai (mis. re-submit ke tool
  lain) dengan `{TARGET}` di-resolve ke domain target scan asli.

## 2026-08-18 — v1 dibangun

- Dibangun dari satu file `redirecthunter.db` contoh (~21MB, 7.890 baris `results`,
  ~2.450 baris `chain`, ~83.600 baris `headers`, target scan: `medilana.id`). Query
  pagination/filter di ARCHITECTURE.md diuji secara logis terhadap volume ini, belum
  diuji terhadap db yang jauh lebih besar (mis. >100k results) — kalau itu terjadi,
  cek dulu apakah sql.js (in-memory WASM) masih cukup cepat sebelum menambah fitur lain
  di atasnya.
- Chart sengaja dibuat manual (SVG/CSS bar, bukan Chart.js) supaya dependency eksternal
  cuma satu (`sql.js`). Kalau butuh chart lebih kompleks (histogram latency, scatter),
  pertimbangkan ulang trade-off ini dengan user dulu, jangan tambah library diam-diam.
- Palet warna & font dipilih dengan tema "security/network tool" (mono untuk data,
  amber/teal/red sebagai status semantik) — lihat brief asli di
  [`../spec/dashboard-spec.md`](../spec/dashboard-spec.md).
