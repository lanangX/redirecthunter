# 01 — Muat & validasi file .db lokal

**What to build:** User bisa drag & drop (atau pilih manual) file `.db` ke halaman; file
dibaca sepenuhnya di browser lewat sql.js, divalidasi skemanya (tabel `scan` + `results`
harus ada), dan dashboard mulai tampil begitu valid. Kalau file salah, muncul pesan error
yang jelas di layar drop — bukan crash diam-diam.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] Layar drop awal tampil saat halaman dibuka, sebelum ada file dimuat
- [x] Drag & drop file bekerja, begitu juga tombol "pilih file" manual
- [x] File `.db` valid (ada tabel `scan` + `results`) berhasil membuka dashboard
- [x] File yang bukan database RedirectHunter menampilkan pesan error di layar drop, dashboard tidak tampil
- [x] Tidak ada request jaringan yang mengirim isi file `.db` ke luar browser
