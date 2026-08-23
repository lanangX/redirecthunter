# CONTEXT

RedirectHunter Dashboard adalah viewer lokal untuk file `.db` yang dihasilkan oleh tool
RedirectHunter (scanner redirect/backlink). Satu file `index.html`, tanpa server, tanpa
build step. User men-drag-drop file `.db` ke browser; `sql.js` (WASM) membaca database
langsung di client. File tidak pernah dikirim ke jaringan.

## Skema sumber (read-only, jangan diasumsikan berubah tanpa cek file nyata)

- `scan` (1 baris): metadata satu sesi scan — `scan_id`, `label`, `input_path`, `target`,
  `status`, `total_urls`, `config_json`, `started_at`, `finished_at`.
- `results` (banyak baris, satu per URL yang di-scan): `result_id`, `scan_id`, `source_url`,
  `expanded_url`, `http_method`, `status_code`, `redirect_type`, `location`, `final_url`,
  `body_link`, `hop_count`, `server`, `content_type`, `content_length`, `cookies_json`,
  `fingerprint_json`, `alive`, `latency_ms`, `error`, `timestamp`.
- `chain` (banyak baris per result): tiap hop redirect — `result_id`, `hop_index`, `url`,
  `status_code`, `redirect_type`, `location_header`, `server_header`, `latency_ms`.
- `headers` (banyak baris per result): header HTTP mentah — `result_id`, `header_name`,
  `header_value`.
- `backlink_checks` (1 baris per sesi pengecekan backlink): `backlink_id`, `label`, `domain`
  (domain yang dicari backlink-nya), `input_path`, `status`, `config_json`, `started_at`,
  `finished_at`.
- `backlink_results` (banyak baris, satu per URL sumber yang dicek): `result_id`, `backlink_id`,
  `source_url`, `final_url`, `status_code`, `match_found`, `match_type` (`anchor` /
  `final_url_is_target` / `text_mention_only` / `not_found`), `matched_href`, `rel`, `target`,
  `blocked`, `requires_login`, `text_mentions`, `robots_meta`, `robots_header`, `notes`, `error`,
  `checked_at`. Tidak punya tabel detail turunan seperti `chain`/`headers` — satu baris flat per
  URL sumber.

`result_id` adalah foreign key yang menghubungkan `results` ↔ `chain` ↔ `headers`, dan (secara
terpisah) `backlink_results` ↔ `backlink_id` di `backlink_checks`.

## View turunan

- `results_dedup` — TEMP VIEW yang dibuat runtime oleh dashboard (bukan bagian file `.db`),
  1 baris per `source_url` (percobaan terakhir menurut `timestamp`). Dipakai saat toggle
  "Sembunyikan duplikat" aktif. Lihat `docs/MEMORY.md` § v1.3 untuk alasannya.

## Kenapa desainnya begini

- **Tanpa server**: data hasil scan bisa berisi target sensitif (mis. cek link afiliasi/backlink
  ke domain milik user) — drag & drop client-side menghindari upload data ke pihak ketiga.
  Jangan tambahkan endpoint server atau kirim data db ke luar browser.
- **Single HTML file**: memudahkan distribusi (kirim 1 file, buka di browser mana saja),
  tidak butuh `npm install`. Pertahankan ini kecuali user eksplisit minta build step.
- **sql.js via CDN**: satu-satunya dependency eksternal. WASM di-load dari
  `cdnjs.cloudflare.com` saat runtime — butuh koneksi internet sekali saat dashboard dibuka,
  tapi data `.db` sendiri tidak pernah keluar dari browser.

## Dokumen terkait

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — struktur kode `index.html`.
- [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) — panduan kerja untuk agent yang mengedit repo ini.
- [`../spec/dashboard-spec.md`](../spec/dashboard-spec.md) — spec fitur v1.
- [`../issues/`](../issues/) — tiket implementasi (tracer-bullet).
- [`CHANGELOG.md`](CHANGELOG.md) — riwayat perubahan.
