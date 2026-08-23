# AGENTS

Panduan untuk agent yang mengedit repo `redirecthunter-dashboard`. Baca [`CONTEXT.md`](CONTEXT.md)
dan [`ARCHITECTURE.md`](ARCHITECTURE.md) dulu kalau belum — dokumen ini tidak mengulang isinya.

## Aturan keras

- **Jangan tambahkan server/backend.** Dashboard ini harus tetap bisa dibuka dengan cara
  buka `index.html` langsung di browser (drag & drop file `.db`). Kalau permintaan user
  butuh server (auth, multi-user, dsb.), tanyakan dulu — itu perubahan arsitektur, bukan fitur.
- **Jangan kirim isi database ke luar browser.** Tidak ada `fetch()` yang mengirim data
  `.db`/`results`/`headers` ke endpoint manapun. CDN (`sql.js`, font) boleh, karena itu cuma
  load kode, bukan kirim data user.
- **Satu file `index.html`.** Kalau file mulai kepanjangan dan kamu tergoda pecah jadi
  `app.js`/`style.css` terpisah, itu sinyal untuk tanya user dulu — bukan diambil sendiri.
- Semua query database lewat `q()`/`qOne()` (lihat ARCHITECTURE.md) — jangan panggil
  `db.exec` langsung di tempat baru.

## Alur kerja

1. Cek [`../spec/dashboard-spec.md`](../spec/dashboard-spec.md) dan [`../issues/`](../issues/)
   untuk lihat apa yang sudah direncanakan sebelum menambah fitur baru dari nol.
2. Kalau mengubah palet warna/tipografi, ubah di `:root` CSS variables saja (satu tempat).
3. Kalau menambah kolom/tabel dari skema sumber, verifikasi dulu skema aslinya di file
   `.db` nyata — jangan asumsikan dari dokumen ini (lihat catatan di ARCHITECTURE.md).
4. Setelah perubahan yang terlihat oleh user (fitur baru, perbaikan bug, perubahan visual),
   tambahkan satu baris ke [`CHANGELOG.md`](CHANGELOG.md).
5. Test manual: buka `index.html` di browser, drag & drop `redirecthunter.db` contoh
   (kalau ada), pastikan tidak ada error di console sebelum menganggap selesai.

## Untuk sesi kerja panjang / lintas-sesi

Catat keputusan yang tidak jelas dari kode saja (kenapa suatu pendekatan dipilih, trade-off)
di [`MEMORY.md`](MEMORY.md), bukan di sini — dokumen ini untuk aturan kerja yang stabil,
MEMORY.md untuk hal yang berubah/berkembang antar sesi.
