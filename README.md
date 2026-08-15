# agent-core

Autonomous agent daemon. Consumer of **auth core + tools backend** (contract v1).
This repo holds only the agent; the tools/auth services live elsewhere.

## Status

Built incrementally by milestone (see the implementation spec §11). **All milestones complete (M1–M12).**

| Milestone | Scope | State |
|---|---|---|
| M1 | Skeleton + config + logging + `agent health` + `agent config show` + compose | ✅ done |
| M2 | Bus + episode + `agent tail` / `agent trace` | ✅ done |
| M3 | Minimal loop (Telegram → planner → executor) + budget | ✅ done |
| M4 | Tools client + function calling | ✅ done |
| M5 | Memory (episodic/semantic/pgvector/quarantine) | ✅ done |
| M6 | Regression harness (before any self-modification) | ✅ done |
| M7 | Playbook + curator | ✅ done |
| M8 | Goals + night shift | ✅ done |
| M9 | Reasoning memory + skills | ✅ done |
| M10 | Review + drift | ✅ done |
| M11 | Tool forge | ✅ done |
| M12 | Full TUI dashboard | ✅ done |

## Tutorial: dari nol sampai jalan

Panduan ini buat siapa pun yang baru pertama kali pakai `agent-core` — nggak perlu paham
semua isi repo dulu, ikutin aja urutannya dari atas ke bawah.

### 1. Siapin dulu yang dibutuhkan

Cuma butuh 3 hal:

- **Python 3.12 ke atas** — cek dengan `python3 --version`
- **Git**
- **Docker** — buat jalanin database (Redis + Postgres) secara lokal. Kalau kamu udah punya
  Redis/Postgres sendiri di tempat lain, Docker nggak wajib.

Selain itu kamu juga butuh **API key** dari salah satu penyedia LLM (model AI) — misalnya
[DeepSeek](https://platform.deepseek.com), OpenAI, atau OpenRouter. Ini yang bikin agent-nya
bisa "mikir". Tanpa API key, agent-nya bisa jalan tapi nggak bisa balas apa-apa.

### 2. Install (cuma sekali)

Buka terminal, tempel perintah ini:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhrads/agentic-core/refs/heads/main/scripts/install.sh | bash
```

Perintah ini otomatis akan:

1. Cek Python & Git kamu sudah cukup baru
2. Download (clone) repo ini ke `~/.agentic-core`
3. Bikin virtual environment Python dan install semua yang dibutuhkan
4. Pasang perintah `agent` supaya bisa dipanggil dari mana aja di terminal
5. Nyalain Redis + Postgres lewat Docker (kalau Docker ada, dan kamu mengiyakan saat ditanya)

Kalau di akhir proses ada peringatan `$HOME/.local/bin is not on your PATH`, tinggal jalankan
baris yang ditampilkan (biasanya `export PATH="$HOME/.local/bin:$PATH"`), lalu buka terminal
baru.

Cek instalasi berhasil dengan:

```bash
agent --help
```

Kalau muncul daftar perintah, berarti sudah beres.

### 3. Setup — jawab beberapa pertanyaan

```bash
agent setup
```

Ini wizard interaktif yang bakal nanya beberapa hal, satu-satu, dengan default yang masuk akal
kalau kamu tinggal pencet Enter:

| Ditanya apa | Contoh jawaban | Boleh dikosongin? |
|---|---|---|
| Mode (`dev`/`prod`) | `dev` | Enter aja pakai default |
| Provider LLM | `deepseek`, `openai`, `openrouter`, atau `custom` | Wajib pilih satu |
| API key provider itu | `sk-xxxxx` | Wajib diisi |
| Model | `deepseek-chat`, `gpt-4o-mini`, dst | Enter aja pakai default |
| Ollama (embedding lokal) | Enter aja pakai default | Boleh, asal nanti `ollama serve` jalan |
| Redis URL & Postgres DSN | Enter aja pakai default | Boleh, kalau pakai Docker |
| Jalanin `docker compose up -d` sekarang? | `y` | Boleh `n` kalau mau manual nanti |
| Aktifkan Telegram? | `y`/`n` | Boleh `n`, nanya lagi kapan-kapan cukup `agent setup` ulang |
| Aktifkan WhatsApp? | `y`/`n` | Boleh `n` juga |
| Budget harian (token/biaya/jumlah aksi) | Enter aja pakai default | Boleh, batasnya bisa diubah kapan saja |

Semua jawaban ini ditulis ke file `.env` di folder repo. **Aman dijalankan berkali-kali** —
kalau kamu run `agent setup` lagi, jawaban lama otomatis jadi default, dan cuma yang kamu ubah
yang ditulis ulang.

### 4. Jalanin

```bash
agent
```

Selesai — ini langsung membuka sesi chat interaktif di terminal kamu, mirip ngobrol di WhatsApp
tapi di terminal. Ketik pesan, Enter, agent-nya bakal mikir dan balas.

- Ketik `/exit` atau tekan `Ctrl+C` buat keluar.
- Kalau `.env` belum ada (belum pernah `agent setup`), `agent` bakal otomatis jalanin wizard
  setup dulu sebelum mulai chat — jadi sebenarnya langkah 3 & 4 bisa digabung, tinggal jalanin
  `agent` dari awal.

### 5. Ganti model AI kapan aja

Nggak puas sama model yang dipakai, atau mau coba provider lain? Nggak perlu edit file manual:

```bash
agent model            # lihat provider & model yang lagi aktif
agent model set        # ganti provider/model/API key secara interaktif
```

Providernya bisa DeepSeek, OpenAI, OpenRouter, atau endpoint custom kamu sendiri (misalnya
server lokal yang kompatibel dengan format API OpenAI). Setelah ganti, restart dulu prosesnya
(`agent` lagi, atau `agent up` kalau jalan di background) supaya perubahan kepakai.

### 6. Sambungin ke Telegram (opsional)

Kalau mau ngobrol sama agent-nya lewat Telegram, bukan cuma dari terminal:

1. Bikin bot baru via [@BotFather](https://t.me/BotFather) di Telegram, catat token-nya.
2. Jalankan `agent setup` lagi, pilih "Enable Telegram? y", tempel token-nya.
3. (Opsional) Isi "allowed chat ids" kalau mau batasi siapa aja yang boleh chat bot-nya. Kosongin
   berarti semua orang yang tau bot-nya boleh chat.
4. Jalankan `agent up` (bukan `agent chat`) supaya bot-nya standby dengerin pesan Telegram terus.

### 7. Sambungin ke WhatsApp (opsional)

WhatsApp di sini pakai cara **nggak resmi** (lewat [Baileys](https://github.com/WhiskeySockets/Baileys),
protokol yang sama dipakai WhatsApp Web) — jadi nggak perlu daftar Meta Business/App Review,
cukup scan QR code kayak buka WhatsApp Web biasa. Ada 2 proses yang jalan bareng: `agent up`
(si agent) dan si "bridge" (Node.js kecil yang jadi jembatan ke WhatsApp).

1. Jalankan `agent setup`, pilih "Enable WhatsApp? y". Wizard-nya bikin **secret** otomatis
   (password rahasia antara agent & bridge). Nggak perlu dicatat — bridge-nya baca sendiri
   dari `.env` utama.
2. Masuk folder bridge, install dependency-nya (sekali aja, butuh [Node.js](https://nodejs.org) 18+):
   ```bash
   cd whatsapp-bridge
   npm install
   npm start
   ```
3. Bakal muncul QR code di terminal — scan pakai HP:
   **WhatsApp → Setelan → Perangkat tertaut → Tautkan perangkat**.
4. Kalau di terminal muncul `Connected to WhatsApp.`, berarti udah nyambung. Sesi login-nya
   disimpan di `whatsapp-bridge/auth/` supaya nggak perlu scan ulang tiap kali dijalankan.
5. Jalankan agent-nya (`agent up`, bukan `agent chat`) di terminal lain — biarin bridge & agent
   jalan bersamaan, keduanya harus tetap nyala biar WhatsApp-nya standby.

Setelah nyambung, pesan WhatsApp yang masuk otomatis diteruskan bridge → agent, diproses, dan
balasannya dikirim balik lewat WhatsApp juga.

> Ini pakai jalur nggak resmi (bukan produk resmi Meta), jadi risikonya ada di kamu — nomor
> bisa kena batasan dari WhatsApp kalau dipakai buat spam/bulk messaging. Wajar dipakai buat
> asisten pribadi/personal, jangan buat broadcast massal.

### 8. Cara-cara lain buat jalanin agent-nya

Ada 3 mode, pilih sesuai kebutuhan:

| Perintah | Buat apa |
|---|---|
| `agent` / `agent chat` | Ngobrol langsung di terminal (paling gampang buat coba-coba) |
| `agent up` | Jalan sebagai server/daemon — dipakai kalau ada Telegram/WhatsApp yang harus standby terus. Tambah `--detach` biar jalan di background. |
| `agent watch` | Dashboard live read-only di terminal — lihat budget, episode yang lagi jalan, event terbaru, tanpa ganggu prosesnya |

`agent down` buat matiin proses yang dijalankan dengan `agent up --detach`.

### 9. Bikin agent-nya inget kamu (playbook)

Agent punya 3 file catatan yang **selalu dibaca tiap kali kamu chat** — mirip `MEMORY.md`
di Hermes. Lokasinya di folder `playbook/` (atur lewat `AGENT_PLAYBOOK_DIR`):

| File | Isinya |
|---|---|
| `SELF.md` | Siapa agent-nya: peran, gaya bahasa, batasan |
| `USER.md` | Apa yang dia tau tentang kamu |
| `MEMORY.md` | Fakta-fakta yang perlu diinget terus |

Tinggal edit pakai teks biasa, nggak perlu format khusus:

```markdown
# MEMORY

- Nama panggilan operator: Bang Fakhri.
- Proyek yang lagi dikerjain: agentic-core.
- Lebih suka jawaban Bahasa Indonesia yang santai.
```

Nggak perlu restart — perubahannya kepakai di chat berikutnya.

**Ini jalan tanpa model embedding sama sekali.** Agent punya 2 lapis ingatan: playbook (file
teks, selalu aktif) dan memori vektor (butuh Ollama + model embedding, buat nyari otomatis dari
riwayat lama). Kalau embedding-nya belum ada, playbook tetep bikin agent inget hal-hal penting.
Isi playbook juga diperlakukan lebih dipercaya daripada hasil pencarian vektor, karena ini kamu
yang nulis sendiri.

> Ukurannya dibatasi ~4000 karakter total biar nggak makan jatah token tiap chat. Kalau
> kepanjangan, `MEMORY.md` yang dipotong duluan — `SELF.md` dan `USER.md` diprioritaskan.

### 10. Update ke versi terbaru

```bash
agent update
```

Ini otomatis nemuin folder instalasi kamu (nggak peduli kamu jalanin dari mana), `git pull`
kode terbarunya, terus `pip install -e .` ulang biar dependency baru (kalau ada) ikut kepasang.
Setelah update, restart proses yang lagi jalan (`agent` / `agent up`) biar perubahannya kepakai.

> Kalau `git pull`-nya gagal karena kamu punya perubahan lokal di folder instalasi (jarang
> terjadi kecuali kamu ngedit sendiri file di sana), commit/stash dulu perubahannya, baru
> `agent update` lagi.

### 11. Kalau ada masalah

```bash
agent doctor --fix
```

**Coba ini duluan.** Dia ngecek config yang bikin agent diem-diem nggak jalan, terus
**benerin sendiri**: Redis yang nggak bisa dipakai (bakal otomatis `docker compose up -d`
dan update `.env` kamu), secret WhatsApp yang beda, dst. Tanpa `--fix` dia cuma ngasih tau
tanpa ngubah apa-apa.

```bash
agent health
```

Ini ngecek semua koneksi yang dibutuhkan (Redis, Postgres, provider LLM, Ollama, WhatsApp, dst)
dan bilang mana yang bermasalah beserta detail errornya. Bedanya sama `agent doctor`: `health`
cuma ngasih laporan kondisi sekarang, `doctor` yang benerin config-nya.

```bash
agent config show            # lihat konfigurasi aktif (secret disamarkan)
agent config show --secrets  # sama, tapi secret ditampilkan (hati-hati siapa yang lihat layar kamu)
```

Masalah umum:

- **"connection refused" ke Redis/Postgres** → Docker belum nyala, jalankan `docker compose up -d`.
- **`Timeout reading from localhost:6379`, atau `agent up` nolak jalan** → ada Redis lain
  (biasanya bawaan Homebrew) yang nempatin port 6379 duluan, jadi agent nyambung ke situ,
  bukan ke Redis Docker-nya — dan beberapa build Redis native nge-hang pas dipakai baca stream.
  **Jalanin `agent doctor --fix`**, dia beresin sendiri (Docker di repo ini pakai port **6380**,
  Postgres **5433**). Mau cek manual siapa yang pakai port-nya: `lsof -nP -iTCP:6379 -sTCP:LISTEN`.
- **WhatsApp `403 Forbidden` di log** → secret bridge-nya beda. Normalnya nggak akan kejadian
  (bridge otomatis baca `AGENT_WHATSAPP_BRIDGE_SECRET` dari `.env` utama). Kalau kamu terlanjur
  bikin `whatsapp-bridge/.env` dengan `BRIDGE_SECRET` sendiri, hapus aja baris itu — atau
  jalanin `agent doctor` buat ngecek.
- **Provider LLM error 401** → API key salah/kosong, jalankan `agent model set` buat perbaiki.
- **Ollama error** → `ollama serve` belum jalan di komputer kamu, atau modelnya belum di-pull
  (`ollama pull nomic-embed-text` dan `ollama pull qwen2.5:3b`).
- **postgres error soal `greenlet`** → dependency ketinggalan, jalankan `agent update` (atau
  `pip install -e .` lagi) buat narik versi terbaru.
- **tools_backend / whatsapp `ConnectError`** → wajar kalau kamu belum setup itu; keduanya
  soft dependency, agent tetap jalan tanpa itu.

### 12. Buat yang mau ikut develop

```bash
git clone <repo-url> && cd agentic-core
docker compose up -d                 # redis + postgres(pgvector)
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                 # isi manual, atau `agent setup`

ruff check src tests   # linter
mypy                    # type checker
pytest                  # test suite
```

### 13. Rencana ke depan: aplikasi desktop

Belum ada aplikasi desktop-nya — untuk sekarang fokusnya CLI + web dulu. Tapi fondasinya sudah
disiapkan: `agent up` menyediakan `GET /api/status`, endpoint JSON yang isinya sama persis
dengan yang ditampilkan `agent watch` (budget, episode aktif, kesehatan regression, event
terbaru, antrian approval). Semua perintah CLI juga sudah dukung `--json`. Jadi kalau nanti
dibikin aplikasi desktop (misalnya pakai Tauri/Electron), tinggal manggil endpoint ini dan CLI
yang sudah ada — nggak perlu bikin protokol baru dari nol.

> Catatan: `/api/status` belum ada autentikasi (sama seperti `/health`/`/metrics`) — kalau nanti
> mau diakses dari luar `localhost`, tambahin auth dulu.

## Non-negotiables (from spec)

1. Every artefact (memory/skill/tool) has fitness and can be disabled — never deleted.
2. External content is quarantined before it can become long-term memory.
3. Every autonomous action has a budget and a permission tier.
4. `trace_id` flows through every layer.
5. The CLI is the primary operator interface — if state isn't visible from the CLI, it isn't done.
6. Gating uses only signals the agent cannot fabricate about itself.
