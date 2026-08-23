# RedirectHunter Dashboard

Dashboard lokal untuk membaca hasil scan `redirecthunter.db` — tanpa server, tanpa upload.

## Pakai

1. Buka `index.html` langsung di browser (double-click, atau `open index.html`).
2. Drag & drop file `.db` ke halaman (atau klik "Pilih file .db").
3. Selesai — semua data diproses di browser Anda.

Butuh koneksi internet sekali saat halaman dibuka (untuk memuat `sql.js` dari CDN dan font).
File `.db` Anda sendiri **tidak pernah** dikirim ke jaringan.

## Struktur folder

```
redirecthunter-dashboard/
├── index.html          ← dashboard (buka file ini)
├── docs/
│   ├── CONTEXT.md       ← ringkasan proyek & skema data
│   ├── ARCHITECTURE.md  ← struktur kode index.html
│   ├── AGENTS.md        ← panduan kerja untuk agent (canonical)
│   ├── CLAUDE.md        ← pointer ke AGENTS.md
│   ├── MEMORY.md        ← catatan keputusan lintas sesi
│   └── CHANGELOG.md     ← riwayat perubahan
├── spec/
│   └── dashboard-spec.md ← spec fitur v1
└── issues/
    └── 01–06...md        ← tiket implementasi (tracer-bullet, semua status: done)
```
