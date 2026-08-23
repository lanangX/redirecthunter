# ARCHITECTURE

Satu file: `index.html`. Tiga lapisan di dalamnya, top to bottom di source:

1. **`<style>`** — token warna/tipografi sebagai CSS custom properties di `:root`
   (`--bg`, `--panel`, `--alive`, `--dead`, `--amber`, dst). Ubah palet di satu tempat ini,
   jangan hardcode hex di tempat lain.

2. **HTML markup** — tiga area:
   - `#dropzone` — layar drag & drop awal, disembunyikan (`.hidden`) setelah db termuat.
   - `#app` — shell dashboard (header, stat cards, chart, tabel results), disembunyikan
     (`.show` ditoggle) sampai db termuat.
   - `#overlay` + `#drawer` — panel detail per-result (chain + headers), fixed-position,
     ditoggle lewat class `.show`.

3. **`<script>`** — vanilla JS, tanpa framework, dibagi per tanggung jawab:
   - **sql.js init** (`initSql`, `q`, `qOne`) — satu-satunya jalur query ke db. Semua query
     lain HARUS lewat `q()`/`qOne()`, jangan panggil `db.exec` langsung di tempat lain.
   - **File loading** (`handleFile`, listener drag/drop & `<input type=file>`) — validasi
     skema (`scan` + `results` harus ada) sebelum `boot()`.
   - **Boot & render** (`boot`, `renderHeader`, `renderStatCards`, `renderCharts`) — jalan
     sekali saat db termuat.
   - **Filter & tabel** (`currentFilters`, `buildWhere`, `applyFilters`, `fetchPage`,
     `renderTable`) — semua filter digabung jadi satu `WHERE` SQL + `LIMIT/OFFSET` di
     `fetchPage`; tabel selalu di-query ulang dari db, tidak ada state list besar di JS.
     Ini yang bikin dashboard tetap ringan walau `results` puluhan ribu baris.
   - **Drawer** (`openDrawer`) — query `chain` + `headers` by `result_id` saat baris diklik
     (lazy, bukan di-preload semua).
   - **Export** (`exportCsv`) — re-run filter SQL yang sama tanpa `LIMIT`, generate CSV di
     browser (`Blob` + `URL.createObjectURL`), tidak lewat server.
   - **Backlink checker tab** (`renderBacklink*`, `*BacklinkFilters`, `fetchBacklinkPage`,
     `openBacklinkDrawer`, `exportBacklinkCsv`) — sama persis polanya dengan Crawl (tab
     disembunyikan lewat `hasBacklinkData` kalau tabel `backlink_checks`/`backlink_results`
     tidak ada, query-per-page lewat `buildBacklinkWhere`/`orderClauseBacklink`), tapi lebih
     sederhana: `backlink_results` sudah flat satu baris per URL, jadi drawer-nya tidak query
     tabel detail terpisah seperti `chain`/`crawl_links` — cukup satu `qOne` ke baris itu sendiri.

## Kenapa query-per-page, bukan load-all-then-filter-in-JS

`results` bisa puluhan ribu baris. sql.js jalan di WASM di main thread; menyaring/mensortir
puluhan ribu objek JS di tiap keystroke pencarian akan nge-lag UI. Query SQL dengan
`WHERE`+`LIMIT` jauh lebih murah dan sql.js sudah punya index rowid bawaan. Kalau nanti
menambah filter baru, tambahkan ke `buildWhere()`, bukan filter manual di JS setelah fetch.

## Menambah tabel/kolom baru dari skema sumber

Skema sumber (`scan`/`results`/`chain`/`headers`) adalah kontrak eksternal dari tool
RedirectHunter — dashboard ini hanya pembaca. Kalau skema berubah, cek dulu file `.db`
nyata (`PRAGMA table_info(<table>)`) sebelum ubah query; jangan asumsikan dari dokumen ini.
