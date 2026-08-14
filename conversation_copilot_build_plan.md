# AI Conversation Copilot — Build Plan v4

## Server-Backend Architecture (Django on VM + CI/CD + Flutter)

> **v4 change (superseding v3's Capacitor client):** the phone app moves
> from Capacitor to **Flutter**. Primary driver: Phase 4's floating
> teleprompter overlay — unproven on Capacitor, but per this app's own
> v1 history it already worked in native Flutter
> (`flutter_overlay_window` / `SYSTEM_ALERT_WINDOW`-based). Flutter also
> gets first-class iOS support and better UI polish than Capacitor's
> WebView-wrapped approach, at the cost of Dart instead of JS/TS (still
> not Python, same tradeoff Capacitor had). See "v3 → v4: what changed
> and why" below.
>
> **v3 change (superseding v2's Cloudflare Tunnel):** the backend moves off
> a home machine onto a cloud VM with a public IP/domain, deployed via
> GitHub Actions CI/CD. See "v2 → v3: what changed and why" below.

---

## 1. Architecture Overview

```text
                    Phone (Flutter App)
┌──────────────────────────────────────────────┐
│  Audio capture → stream up (WebSocket)        │
│  Suggestion / teleprompter UI ← stream down   │
│  Session, history, knowledge base screens     │
│  (thin client — no local AI, no local STT)    │
└───────────────────┬────────────────────────────┘
                    │ HTTPS / WSS (direct — no tunnel hop)
                    ▼
        Caddy (existing, shared with BizYangu — TLS + reverse proxy)
                    │
                    ▼
┌──────────────────────────────────────────────┐
│         Cloud VM (always-on, public IP)        │
│                                                │
│  Django (ASGI, channels for WebSocket)          │
│  Audio ingestion + VAD                          │
│  Streaming STT (Whisper / faster-whisper)       │
│  Speaker separation                             │
│  Conversation State Engine                      │
│  Memory Manager (SQLite / Postgres)             │
│  Knowledge Retrieval (RAG — FAISS)              │
│  Local LLM (llama.cpp, GGUF)                    │
│  Cloud Router (OpenRouter / Groq / Gemini)      │
│  Suggestion Generator                           │
│  Teleprompter Generator                         │
│  Coaching Engine                                │
│  Debrief Engine                                 │
└──────────────────────────────────────────────┘
                    ▲
                    │ deploy on push to main
        GitHub Actions (build → SSH/rsync → restart)
```

**Key shift from v1:** all AI/compute lives on the server, not the phone. The phone is a thin client — audio in, suggestions out. This removes the mobile resource ceiling entirely and lets you run heavier, more accurate models than any phone could hold.

**Key shift from v2:** the server moves from a home machine behind a Cloudflare Tunnel to a cloud VM with its own public IP/domain, reachable directly, and deployed automatically via CI/CD instead of manually.

**Tradeoff to design around explicitly:** the system still depends on network reachability (phone ↔ VM) and real-world latency instead of on-device processing. Phase 0 exists to measure this before anything else is built on top of it — see the updated latency discussion below, since the VM changes what that number is measuring.

---

## 1a. v2 → v3: what changed and why

| | v2 (home server + tunnel) | v3 (VM + CI/CD) |
|---|---|---|
| Reachability | Depends on home network/power/ISP being up | VM is always-on by design |
| Network hops | Phone → Cloudflare edge → tunnel → home machine | Phone → VM directly (one less hop) |
| Deploys | Manual, on whatever machine you're at | `git push` → GitHub Actions builds and deploys |
| Cost | Free (sunk hardware) | Recurring VM bill |
| Resource ceiling | Whatever your desktop has spare | Fixed, known VM spec — sized deliberately |
| Data location | Stays in your house | Leaves to the VM provider's infrastructure |

**Net effect on latency:** likely a small **improvement**, not a regression — removing the tunnel hop and depending on a VM's network path instead of home-ISP upload speed (historically the weaker link for the "up" direction of audio streaming) should help, not hurt. But the actual number depends entirely on:
1. **VM region vs. your physical location** — pick a region close to where you'll actually use the app, or this can just as easily make things worse than the tunnel did.
2. **Where the LLM inference happens** — this dominates response time far more than the network hop does. CPU inference for a 7–8B model can take multiple seconds; that's the real bottleneck Phase 0's ~250–300ms network threshold was never trying to solve. Moving hosting doesn't remove the LLM inference-time question — it's still the same open item from Phase 3 (needs a GGUF model picked and benchmarked on the VM's actual specs).
3. **VM CPU/RAM/GPU tier** — a cheap VM with no GPU offload is not guaranteed to be faster than your home desktop for local LLM inference; it could be slower if under-provisioned for cost reasons.

Bottom line: the VM move should help the *network* leg of latency modestly, but it does not by itself answer "how fast does the app answer questions" — that's still gated on the LLM inference benchmark from Phase 3, which now needs re-running against the VM's actual hardware once it's provisioned, not assumed carried over from home-hardware numbers.

## 1b. v3 → v4: what changed and why

| | v3 (Capacitor) | v4 (Flutter) |
|---|---|---|
| Rendering | WebView-wrapped web app | Native (Flutter's own rendering engine) |
| Floating overlay (Phase 4) | Unproven — needs a custom native plugin bridged out from the WebView | **Already worked in this app's v1** via `flutter_overlay_window` / `SYSTEM_ALERT_WINDOW` |
| Language | JS/TS | Dart |
| iOS support | Yes, standard | Yes, first-class |
| UI polish for teleprompter/live session screens | Good, more manual work | Strongest fit — this is Flutter's core strength |
| Build/CI tooling | Standard npm-based, well-trodden | Standard `flutter build` + Fastlane, well-trodden |

**Why this beat Kivy as the other alternative considered:** Kivy (Python, matching the Django backend) was the other option on the table. It was passed over specifically because the overlay — the one feature this plan already flags as an open risk — is unproven on Kivy for this app, whereas it's known-working history on Flutter. Language match to the Python backend was the point in Kivy's favor, but de-risking the overlay and getting better UI polish outweighed it.

**What this doesn't change:** everything backend-side (VM, CI/CD, Django, VAD/STT/LLM pipeline) is unaffected — this is purely a frontend swap. The WebSocket protocol between phone and server stays the same regardless of which client framework speaks it.

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| Backend framework | Django + Django Channels (ASGI) for WebSocket support |
| Backend hosting | Existing shared Oracle Cloud VM (`bizyangu-vnic`), reached via BizYangu's existing **Caddy** reverse proxy (automatic TLS) — no separate Nginx/certbot needed |
| Backend CI/CD | GitHub Actions: on push to `main`, build → deploy to VM (SSH/rsync or container registry pull) → restart service |
| Frontend | Flutter (Dart) — native Android/iOS app, not a WebView wrapper |
| Overlay (Phase 4) | `flutter_overlay_window` (or equivalent) for the `SYSTEM_ALERT_WINDOW`-based floating teleprompter — previously working in this app's v1 |
| Audio transport | WebSocket, chunked PCM/Opus streaming from phone mic |
| VAD | Silero VAD (server-side) |
| STT | faster-whisper, streaming mode |
| Speaker separation | pyannote.audio (basic diarization) — server-side, no longer resource-constrained |
| Local LLM | llama.cpp + GGUF quantized model, sized for your server's actual hardware (no longer phone-constrained — worth benchmarking a 7B–13B class model) |
| Embeddings | sentence-transformers |
| Vector store | FAISS |
| Database | SQLite for dev → Postgres if you want concurrent sessions/multi-device later |
| Cloud LLM fallback | Provider-abstracted: OpenRouter, Groq, Gemini |
| Auth (phone ↔ server) | Simple token auth is enough for a single-user personal tool; don't over-build this yet |

---

## 3. Folder Structure

```text
conversation_copilot/

├── flutter_app/
│   ├── lib/
│   │   ├── screens/
│   │   │   home_screen.dart
│   │   │   live_session_screen.dart
│   │   │   teleprompter_screen.dart
│   │   │   debrief_screen.dart
│   │   │   knowledge_manager_screen.dart
│   │   │   settings_screen.dart
│   │   ├── audio/
│   │   │   capture.dart
│   │   │   stream_client.dart
│   │   ├── ws/
│   │   │   socket.dart
│   │   ├── overlay/
│   │   │   teleprompter_overlay.dart   # SYSTEM_ALERT_WINDOW via flutter_overlay_window
│   │   └── state/
│   ├── android/
│   ├── ios/
│   └── pubspec.yaml
│
├── server/
│   ├── manage.py
│   ├── copilot/                  # Django project root
│   │   settings.py
│   │   asgi.py
│   │   routing.py                # Channels websocket routes
│   │
│   ├── audio/
│   │   consumer.py               # WebSocket consumer, receives audio chunks
│   │   vad.py
│   │   speaker.py
│   │
│   ├── stt/
│   │   engine.py                 # faster-whisper wrapper
│   │   streaming.py
│   │
│   ├── conversation/
│   │   manager.py
│   │   state.py
│   │   intent.py                 # question/decision/objection extraction
│   │   memory.py
│   │
│   ├── knowledge/
│   │   rag.py
│   │   embeddings.py
│   │   vector_store.py
│   │   documents.py
│   │
│   ├── ai/
│   │   local_llm.py
│   │   cloud_router.py
│   │   prompts.py
│   │   teleprompter.py
│   │   coaching.py
│   │   confidence.py
│   │
│   ├── debrief/
│   │   scoring.py
│   │   suggestions.py
│   │
│   ├── api/
│   │   rest_views.py             # session mgmt, knowledge upload, history
│   │   ws_consumers.py           # live suggestion stream
│   │
│   └── models.py                 # Django ORM: Session, Turn, Fact, Document
│
├── .github/
│   └── workflows/
│       └── deploy.yml             # CI/CD: build + deploy backend to VM on push to main
│
└── infra/
    ├── caddy-copilot.snippet          # site block to append to BizYangu's existing Caddyfile
    ├── docker-compose.prod.yml    # production compose (no cloudflared service)
    └── deploy_notes.md            # VM provisioning steps, secrets, rollback notes
```

---

## 4. Build Phases

### Phase 0 — Infrastructure & Feasibility *(build first)*

Goal: prove the network path and measure real latency before anything else depends on it.

- [x] Django ASGI project scaffolded with Channels installed
- [ ] ~~Cloudflare Tunnel configured~~ — **superseded (v3, revised):** existing shared VM instead, reachable via BizYangu's existing Caddy reverse proxy (not a new Nginx instance — see §6a), confirmed reachable from outside your home network
- [x] Flutter app scaffolded, connects to the VM's `wss://` URL directly (no tunnel) — `flutter_app/` created, Android-first (iOS deferred), monorepo. `lib/main.dart` implements a minimal WebSocket ping-test screen mirroring `latency_test.html`'s protocol against `PingConsumer`. Real toolchain setup pain along the way (Windows PATH GUI's broken 2047-char limit, PATH duplicates from years of installs, Gradle download lock from a stuck process, corrupted NDK download, and a genuine disk-space-full error underneath both of those) — all resolved. First `flutter run` to a physical device (Nokia G21, via USB debugging/adb) took ~44 minutes (one-time Gradle + NDK + SDK Platform 36 + Build-Tools 36 + CMake download/install); subsequent builds should be dramatically faster since everything's now cached. **Follow-up fix:** a later Android build failed at compile time — `record: ^5.1.2` resolved to a broken combination (`record_platform_interface` newer than the pinned `record_linux` implements), which fails the whole kernel snapshot compile regardless of target platform since Dart checks all platform implementations at compile time. Fixed by bumping to `record: ^7.1.1` (the mic-capture API used in `capture_io.dart` — `AudioRecorder`, `hasPermission()`, `startStream(RecordConfig(...))` — is unchanged across that range, so it was a safe version bump, not a rewrite).
- [x] Minimal round-trip test: phone sends a text ping over WebSocket → server echoes it back → measure latency across at least 3 network conditions (phone on cellular, phone on wifi, from a different city/region if possible) — **re-run against the VM, done for WiFi (browser + native Flutter app) and a second non-cellular network; cellular still outstanding** (see decision gate below)
- [x] Audio streaming proof of concept: phone mic → chunked PCM/Opus → WebSocket → server writes to a file, confirm no corruption/drift — **satisfied by inference, not built literally as written.** Development went straight to the full Phase 1 pipeline (VAD → STT → live transcript in `audio_test.html`) instead of a discrete raw-file-dump step. Coherent live transcription against real speech is a stronger proof than a raw file would've been — corrupted/dropped/drifted audio would show up as garbage STT output, so working transcripts already rule that out. Revisit only if a real corruption/drift issue is ever suspected downstream (e.g. STT quality degrading in a way that looks audio-related rather than model-related).
- [ ] **Decision gate:** round-trip latency measured against the live VM (`wss://145-241-102-99.sslip.io/ws/ping/`), via `PingConsumer`/`latency_test.html`:
  - **WiFi (phone), 7 pings:** 274, 228, 257, 250, 223, 311, 275ms — mean ≈260ms, range 223–311ms
  - **WiFi (dev PC, same network), 8 pings:** 189, 260, 194, 566, 300, 225, 234, 195ms — mean ≈270ms (≈228ms excluding the 566ms outlier). Note: this run's client/server clocks weren't synced, so the up/down leg split is unreliable (down leg came back negative) — only the total round-trip figure (client-clock-only) is trustworthy here.
  - **Combined WiFi (15 pings, browser + web):** median 250ms, mean ≈265ms, typical range 189–311ms, one 566ms outlier.
  - **Native Flutter app, WiFi (20 pings, Nokia G21):** mostly 64–103ms, tight and fast — clearly the best readings yet, likely reflecting the native app's leaner networking stack vs. a browser tab.
  - **Native Flutter app, second network — not cellular, but a different network from the WiFi above (28 pings):** mean ≈105ms including two outliers (425ms, 229ms); excluding those, mean ≈88ms, range 66–124ms. Confirms the earlier native-WiFi reading wasn't a fluke.
  - **Consistent observation across every device/network tested so far:** the up/down leg split always shows a ~950–1070ms up leg and a deeply negative down leg. This isn't network-dependent — it's the phone's clock sitting at a fixed ~1s offset from the VM's clock, same magnitude regardless of network. Round-trip totals (client-clock-only) are unaffected and remain the trustworthy number throughout.
  - **Cellular:** still not tested — explicitly confirmed as pending, not covered by the "different network" run above.
  - **Reading against the ~250–300ms Completion Mode threshold:** browser-based WiFi readings sat right on the line, but the native Flutter app is comfortably faster (64–124ms typical) — clears even the original strict threshold with room to spare. Cellular (the harder, more decision-relevant condition) is still unmeasured.
  - **Revised call:** don't block the rest of the build on this. The original 250–300ms line was a rough placeholder, not a hard physical limit, and cellular will realistically run somewhat slower than WiFi regardless. **Provisional threshold widened to ~300–400ms** to leave room for that gap — WiFi numbers already clear this comfortably. Proceeding with the build on that basis. Still **revisit once real cellular numbers come in**: if cellular lands meaningfully above ~400ms or is wildly inconsistent (like the 566ms WiFi outlier, but worse/more frequent), park Completion Mode at that point rather than forcing it.

### Phase 1 — Local Conversation Listener

- [x] Server-side VAD (Silero) running on incoming audio stream — `audio/vad.py`, lazy-loaded singleton, 512-sample/16kHz frames
- [x] Streaming STT (faster-whisper) producing partial + final transcripts — `stt/engine.py` + `audio/consumers.py` (`AudioStreamConsumer`); partials on a timer while speaking, final on VAD silence close
- [x] Basic speaker separation (2-speaker case first) — `audio/speaker.py`: naive pause-based A/B labeler by default; real `pyannote.audio` diarization wired in as opt-in (`ENABLE_DIARIZATION=1`)
- [x] Session model in Django: `Session`, `Turn` (speaker, text, timestamp, confidence) — `conversation/models.py`, migrated, admin-registered
- [x] Live transcript visible in **the Flutter app** during a session — `flutter_app/lib/screens/live_session_screen.dart` exists and is confirmed working against the real VM (connect/mic/live partials/finalized A-B turns with confidence, same as `audio_test.html` was standing in for). `audio_test.html` remains useful as a lightweight browser-only smoke test but is no longer the only Phase 1 UI.

**Verified via a scripted smoke test** (VAD/STT monkeypatched to isolate the segmentation state machine): connect → speech frames → silence closes the segment → STT called → speaker labeled → `Turn` persisted → `final` message sent → matches DB; session resume via URL; two-turn A→B naive speaker flip across a silence gap.

**Verified against real mic/real model on real hardware (VM):** confirmed working end-to-end via `audio_test.html` over `wss://145-241-102-99.sslip.io/ws/audio/` — live partials, finalized turns with A/B speaker labels and confidence scores, matching real spoken sentences. Two real bugs found and fixed along the way:
- **No transcription at all initially** — root cause was Whisper auto-detecting language fresh per segment (`language=None`), which is unreliable on short 1–7s clips at `beam_size=1` and was flipping between English/Italian/French/Swahili/Shona/German at 17–50% confidence, producing garbage/empty text that the consumer silently dropped. Fixed by forcing a configured default language (`WHISPER_LANGUAGE=en`, `stt/engine.py`) instead of per-call auto-detect.
- **iOS Safari `AudioContext` can start suspended** even after a user-gesture-triggered creation — added an explicit `.resume()` call in `audio_test.html` as a defensive fix (not confirmed as the actual root cause this time, since audio was reaching the server regardless, but a known trap worth guarding against).
- **Known latency bottleneck — now fixed.** Partials were re-transcribing the *entire* accumulated segment from scratch each time (not just new audio since the last partial), so a 40-second turn's last partial re-decoded all 40 seconds — cost (and perceived lag) grew with turn length regardless of hardware. Fixed in `audio/consumers.py`: `_emit_partial` now windows to only the most recent `PARTIAL_WINDOW_MS` (new env var, default 8000ms, added to both `.env.example`/`.env.production.example`) of `segment_frames` instead of the whole growing list. Safe because partials are UI-only — `_finalize_segment` still decodes the full (silence-trimmed) segment for the persisted `Turn` text, so extraction/suggestions never see a truncated transcript, only the live "still speaking" preview is windowed.

#### Local-hardware vs. VM decision (session note)

Real-session testing surfaced that this "slower the longer you talk" symptom was compounding two separate things: the per-partial full re-decode above (fixed, see just above) and the VM itself being genuinely under-provisioned for this concurrently with BizYangu (2 vCPU / 5.8GB RAM total, shared — see §6a). Evaluated running Copilot locally instead, on a personal HP Elite 800 G4 (i7-8700, 6c/12t @ 3.2GHz turbo ~4.6GHz, 16GB RAM, AMD Radeon RX 560 4GB) since this is a personal project with no requirement to stay on shared infra.

- **CPU:** clear win over the VM regardless of the windowing fix above — 6 dedicated cores/12 threads with no contention from BizYangu's Django/Redis/Postgres containers, vs. 2 *shared* vCPUs. Whisper `small`/`beam_size=5` should run noticeably faster here even unchanged.
- **GPU: does not help.** The RX 560 is AMD (GCN 4), not Nvidia. `faster-whisper` runs on CTranslate2, whose GPU backend is CUDA-only (Nvidia) — there's no CTranslate2 ROCm/ HIP support for AMD cards, so `WHISPER_DEVICE=cuda` simply isn't an option here. This stays CPU-only either way; the RX 560 is not a lever available to this project.
- **Decision:** not yet finalized — running locally on this box would improve latency (CPU headroom, zero BizYangu contention) but trades away the always-on/remote-accessible nature of the VM deployment (Caddy/sslip.io TLS, reachable from a phone over cellular per Phase 0's original test). Worth deciding deliberately rather than defaulting: is always-on remote access actually needed for how this gets used, or is a local run (only available when that PC is on) an acceptable trade for a personal tool? Revisit before committing either way — no code changes hinge on this choice besides `WHISPER_DEVICE` staying `cpu` regardless.

### Phase 2 — Conversation Understanding *(feasibility-gated — do not skip the checkpoint)*

- [x] Build extraction module for: questions, decisions, requests, objections, action items, topic changes — `conversation/intent.py`, rule-based (regex/keyword), zero extra deps, runs inline after each finalized `Turn`
- [ ] Benchmark 2–3 local LLM sizes for extraction accuracy vs. latency against the rule-based baseline above — not started; needs llama.cpp + a GGUF model, which isn't wired in yet
- [x] Test against a set of real or realistic transcript samples, score extraction accuracy manually — `conversation/fixtures/sample_transcripts.json` (3 sample dialogues) + `python manage.py score_extraction`
- [ ] **Go/no-go checkpoint:** rule-based baseline scored manually against the 3 bundled sample transcripts — question/decision/request/objection/action_item all look solid; topic_change is the weak one (turn-level lexical overlap is a genuinely weak signal, see the caveat in `intent.py`). Rescore against your own real transcripts before treating this as settled, and don't lean on topic_change downstream until it's been tightened or replaced.

### Phase 3 — AI Copilot (basic suggestions) *(v4: now includes the hybrid router, pulled forward from Phase 7)*

- [x] Pipeline: detected question → conversation context → knowledge base (if available) → local LLM → suggested answer — `ai/local_llm.py` (llama.cpp via llama-cpp-python, lazy singleton, same deferred-import pattern as `stt/engine.py`/`audio/vad.py`) + `ai/prompts.py` (prompt construction) + `audio/consumers.py` (`_generate_suggestion`/`_extract_and_emit` wiring). No knowledge base yet — that's Phase 6, and `ai/prompts.py` is structured so RAG context slots in later without a rewrite.
- [x] Single-shot suggestions only (no completion mode yet) — one `create_chat_completion` call per detected question, no streaming tokens.
- [x] Suggestion delivered to phone over WebSocket, rendered in a basic UI — WebSocket delivery is done (`{"type": "suggestion", ...}`, fired as a background `asyncio.create_task` per question so slow CPU inference never blocks live audio ingestion on that connection); persisted to a new `Suggestion` model (`conversation/models.py`, migration `0003_suggestion.py`) and exposed via `GET /api/sessions/<id>/suggestions/`. Confirmed rendering as suggestion bubbles in the actual Flutter screen (`live_session_screen.dart`), not just `audio_test.html` — see Phase 1 note above.
- [ ] **LLM strategy resolved (v4):** `LLM_MODEL_PATH` → a **3B-class** GGUF model (`Qwen2.5-3B-Instruct` or `Llama-3.2-3B-Instruct`, Q4_K_M) sized for the VM's actual 5.8GB RAM / 2 vCPU ceiling — this is the always-available, no-network fallback, not the primary path.
- [x] **New:** `ai/cloud_router.py` — Groq client (free tier, OpenAI-compatible API) as the **primary** suggestion path; falls back to the local 3B model on network failure, timeout, rate-limit, or empty response. `audio/consumers.py._generate_suggestion` now calls `ai.cloud_router.get_suggestion()` instead of `ai.local_llm.get_llm()` directly — same input/output shape, no other caller changes needed. `groq` added to `requirements.txt` (lightweight, installed unconditionally, unlike the still-commented-out `llama-cpp-python`). `GROQ_API_KEY` set on the VM.
- [ ] Benchmark both paths' actual latency on the VM once wired — this replaces the old "measure CPU inference latency" open question with a concrete comparison (local 3B vs. Groq round-trip) instead of assuming one is obviously faster. **Blocked on `llama-cpp-python` actually being installed** (still commented out in `requirements.txt` per its own note) — until then the local fallback path will raise `ModuleNotFoundError` rather than degrade gracefully, so a Groq outage currently means no suggestions, not a slow one. Worth deciding whether to install it now or accept "Groq-only for now" as the real state of Phase 3.

#### Phase 3 detailed path — what's left, in order

This phase has two independent halves: things **you** need to do outside this repo (accounts, downloads), and things **Claude/the build** does in code. Listed in the order they actually need to happen — later steps depend on earlier ones.

1. **[You] Create a free Groq account** at console.groq.com and generate an API key. This is the only account this phase needs — Groq is the primary suggestion path (cloud, fast, free tier). Add the key to `server/.env` as `GROQ_API_KEY=...`.
2. **[You] Download a local fallback model** (no account needed, just a file): `Qwen2.5-3B-Instruct-Q4_K_M.gguf` or `Llama-3.2-3B-Instruct-Q4_K_M.gguf` from Hugging Face, placed on the VM. Set `LLM_MODEL_PATH` in `.env` to point at it. This is the offline/rate-limit fallback, not the default path — the app should work even before this step, just without a fallback if Groq is briefly unreachable.
3. ~~**[Claude] Build `ai/cloud_router.py`**~~ — **done.** `get_suggestion(question, context_turns) -> {"text", "model"}` tries Groq first, catches connection/timeout/rate-limit/empty-response failures specifically (not a bare `except`), falls back to `ai/local_llm.py`'s singleton. Same lazy-singleton pattern as `stt/engine.py`/`audio/vad.py`.
4. ~~**[Claude] Wire the router into `audio/consumers.py`**~~ — **done.** `_generate_suggestion` now calls `cloud_router.get_suggestion()` instead of `local_llm.get_llm()` directly. WebSocket message shape unchanged, so the Flutter screen needed no changes.
5. **[You + Claude] Benchmark both paths on the VM** — still open, and now has a real blocker: `llama-cpp-python` is commented out in `requirements.txt`, so the local fallback path doesn't actually work yet (it'll raise `ModuleNotFoundError`, not silently degrade). Two options: (a) install it now and do the real Groq-vs-local comparison this step was meant for, or (b) consciously treat Phase 3 as "Groq-only, no working fallback yet" and revisit once a local model matters more (e.g. before relying on the app somewhere Groq might be unreachable). Either is fine — just pick one rather than leaving it ambiguous.

Steps 1–2 (Groq account + API key) are done — key is set on the VM. Step 5 is what's left before this phase can close.

**Update:** end-to-end testing surfaced a real bug blocking step 5 (and blocking Phase 1's Flutter checkbox on Chrome specifically) — logged and fixed as its own item below, since it's an audio-capture issue, not a Groq/router issue.

#### Flutter web audio capture — fixed

**Symptom:** on the native Flutter app (Android), live sessions worked end-to-end (transcripts, A/B labels, suggestions). On the same app compiled for **web (Chrome)**, nothing happened at all — no transcript, no suggestion, no server-side log activity of any kind, despite speaking clearly.

**Root cause:** `package:record` (used by `MicCapture` for mic capture) cannot stream raw PCM16 audio in a browser. Browsers' `MediaRecorder` API always encodes to `webm/opus` regardless of the encoder requested — a documented limitation of the browser API itself (confirmed via the package's own issue tracker), not something fixable from the Dart side by configuring `record` differently. So on web, the app was silently sending opus-encoded bytes labeled as raw PCM16 — the server's VAD/STT pipeline received garbage, never finalized a turn, and nothing downstream (extraction, suggestions) ever had a chance to run. This is exactly why `server/audio_test.html` (a plain browser page, not Flutter) worked in earlier Phase 1 testing — it never used `record` at all, and already solved this with hand-rolled Web Audio API code.

**Fix:** `MicCapture` is now split by platform at compile time:
- `flutter_app/lib/audio/capture.dart` — conditional-export facade (checks `dart.library.io` vs `dart.library.js_interop`, the latter chosen over the older `dart.library.html` check since it's also valid under Wasm compilation)
- `flutter_app/lib/audio/capture_io.dart` — the original `package:record`-based implementation, unchanged, used on native
- `flutter_app/lib/audio/capture_web.dart` — new; thin `dart:js_interop` wrapper
- `flutter_app/web/audio_capture.js` — new; the actual browser logic, porting `audio_test.html`'s already-proven `getUserMedia → AudioContext → ScriptProcessorNode → manual downsample to 16kHz → manual Float32-to-PCM16LE` approach verbatim, rather than re-deriving it through JS interop typings for a deprecated Web Audio API surface
- `flutter_app/web/index.html` — loads `audio_capture.js`
- `flutter_app/lib/audio/capture_stub.dart` — unreachable-in-practice fallback so the conditional export stays exhaustive

No changes needed to `live_session_screen.dart`, `main.dart`, or the WebSocket layer — `MicCapture`'s public interface (`start`/`stop`/`dispose`/`sampleRate`) is identical on both platforms, so the swap is invisible above the audio-capture layer. **Confirmed working on real Chrome** — transcripts now appear, same as the Android test. That surfaced a separate, real issue below.

#### Transcription accuracy checkpoint — gates everything past this point

**Symptom:** clean, correctly-pronounced speech ("What's the capital of Kenya?") transcribed as "That's the captain of Kenya" / "the capital of Kenya" (split across two speaker labels). Not a mic or audio-pipeline problem — confirmed the audio itself is reaching the server correctly.

**Cause:** `stt/engine.py` had `beam_size=1` (greedy decoding) hardcoded — tuned for CPU speed on this VM's 2 vCPUs, never tuned for accuracy, and not even configurable without editing code. Short/quiet/accented speech is exactly where greedy decoding produces plausible-sounding wrong guesses like this.

**Fix so far:** `WHISPER_BEAM_SIZE` is now a proper env var (default `5`, was hardcoded `1`), same pattern as `WHISPER_MODEL_SIZE`/`WHISPER_COMPUTE_TYPE`/`WHISPER_LANGUAGE` — so accuracy can actually be tuned and benchmarked instead of guessed at.

**Not yet done — this is the actual checkbox:**
- [ ] Rebuild + restart the backend with `WHISPER_BEAM_SIZE=5` and re-run the same test phrase(s) that failed above. Confirm the transcript is now correct.
- [ ] If `beam_size=5` still isn't accurate enough, the next lever is `WHISPER_MODEL_SIZE=medium` (env-only change, no rebuild needed beyond the container picking up the new env var) — more RAM (~1.5GB int8) and slower per segment, but a real accuracy jump over `small`. Try beam_size before model size — it's free (no extra RAM) and often gets most of the improvement on its own.
- [ ] Once a combination is accurate enough on real test phrases, note the final `WHISPER_MODEL_SIZE`/`WHISPER_BEAM_SIZE` values used here and move on to Phase 3 suggestion testing. Until this box is checked, suggestion-quality testing isn't meaningful — it'd be built on transcripts that are already wrong.

**Update 1:** first live suggestion test (after the beam_size fix landed) produced a correct transcript but no visible suggestion, and — critically — the backend logs showed nothing at all during the live session, not even for the working transcript path. Root cause of *that*: this app has no `LOGGING` config in `settings.py`, so `logger.info`/`logger.debug` calls have nowhere configured to go, and even `logger.warning`/`logger.exception` only reach the console via Python's bare-minimum stderr fallback handler — easy to miss, and indistinguishable from "nothing ran." Added explicit `print(..., flush=True)` diagnostics at each stage of `_extract_and_emit`/`_generate_suggestion` (extraction found/not-found, suggestion start, success with model+latency, failure with the actual exception) — same pattern the startup warmup messages already use, confirmed to reach `docker compose logs`. Also fixed a real bug found in the process: `_recent_turns_context()` was being awaited *outside* the try/except in `_generate_suggestion`, so a failure there would've vanished as an unretrieved background-task exception instead of being caught and logged — moved inside the try.

**Update 2:** logs *still* showed nothing after Update 1 — root cause turned out to be a dev/prod mix-up, not a code bug: `docker compose build backend` (no `-f`/`-p` flags) always targets the *dev* stack (`docker-compose.yml`, container `conversation-copilot-backend-1`), but real traffic — including every browser test in this doc — hits the separate *prod* stack (`infra/docker-compose.prod.yml`, container `copilot_backend`, the one BizYangu's Caddy actually proxies to). Every rebuild done while chasing Update 1 updated a container nothing was talking to. Fixed structurally, not just documented: `infra/docker-compose.prod.yml`'s service key renamed from `backend` to `copilot_backend` (matches its `container_name`, which was already correct — the compose *service key* was the mismatched part), and a root-level `Makefile` added with `make dev-*`/`make prod-*` targets so the correct `-f`/`-p` flags never have to be typed or remembered by hand again — see the Makefile's own header comment for the full rationale. **From here on, use `make prod-deploy` / `make prod-logs` / `make prod-shell` / `make prod-env` for anything meant to affect real sessions** — `make dev-*` only touches the unused dev container.

**Update 3:** with a valid Groq key, the suggestion itself finally succeeded (200 OK from Groq, answer returned in 530ms) — but then failed to persist: `sqlite3.OperationalError: no such table: conversation_suggestion`. The `Suggestion` model and its migration (`0003_suggestion.py`) have existed in the repo since Phase 3 was first built, but neither compose file's startup command ever ran `python manage.py migrate` — only `warmup_models` then `daphne`. So the table was simply never created on the actual VM database. Fixed both `docker-compose.yml` and `infra/docker-compose.prod.yml` to run `migrate` before `warmup_models` on every container start, and added `make dev-migrate`/`make prod-migrate` for one-off manual runs. **This means every future model change needs a real migration + a deploy that includes it — no longer a manual step to remember.**

**Update 4 — Phase 3 confirmed working end-to-end.** Full chain verified live: mic → VAD → Whisper → question detected (`[extract]`) → Groq answered in 467ms (`[suggest]`) → `Suggestion` row persisted, no errors. This closes Phase 3's actual goal — everything from here is refinement, not "is this broken."

Accuracy checkpoint still open, though: same live test showed two garbled turns alongside two clean ones (`"understand that Nairobi is a country and not a city, but..."`, `"beats, like 5 of them, least them."` vs. a correctly-transcribed question). `beam_size=5` alone isn't enough on longer/less-clean speech. Since slower-but-accurate is an acceptable tradeoff here, next step is `WHISPER_MODEL_SIZE=medium` in `server/.env.production` (env-only change, `make prod-up` — no rebuild needed) — this was already flagged as the next lever back when `beam_size` was fixed, now confirmed necessary rather than optional.

**Update 5:** tried `large`, then `medium` — both eventually produced `exited with code 137` mid-session (SIGKILL), `medium`'s happening after several turns had already processed successfully, not during a config change. Very likely a real OOM kill: this VM's 5.8GB RAM is shared with BizYangu, and `medium`/`large`'s footprint on top of that probably exceeded what's available. **Not yet confirmed** — checking `dmesg | grep -i "oom\|killed process"` around those timestamps is the next step whenever convenient, but isn't blocking anything. Reverted to `small` (stable, no crashes) + `beam_size=5`.

Given the model-size lever is likely capped by RAM rather than choice, added a free one instead: `WHISPER_INITIAL_PROMPT` (`stt/engine.py`, `.env.example`, `.env.production.example`) — biases Whisper's vocabulary/spelling toward expected terms (e.g. "N+1 query", "Kubernetes") with zero RAM or latency cost, unlike model size or beam width. Also worth naming plainly: the "Kenyan English / UK English / American English" framing from testing isn't how Whisper works — there's no separate dialect mode, `language=en` already covers accent variation on its own. `initial_prompt` is the real, working version of that same instinct (biasing vocabulary, not accent).

**Current state, good enough to move forward on:** `small` + `beam_size=5` (+ optionally `WHISPER_INITIAL_PROMPT`) is the accuracy/stability ceiling this VM supports without either more RAM or reducing BizYangu's concurrent footprint — a real infrastructure question, not a tuning one, and deliberately deferred per earlier discussion. Phase 3's actual goal (extraction → Groq suggestion → persisted, end-to-end) is confirmed working. **Moving on from this checkpoint.**

**Update 6 — real data-loss bug, not a missing migration.** The `no such table: conversation_suggestion` error came back on 2026-08-12, hours into an already-running container that had processed suggestions successfully earlier in the same session. Root cause: `db.sqlite3` lived at `server/db.sqlite3` — *inside* the bind-mounted, git-tracked source directory (`../server:/app` / `./server:/app` in both compose files), with no `.gitignore` anywhere in the repo covering it. Syncing code between local and the VM via git silently overwrote the live production database file out from under the running container — no crash, no restart, no error, since Django was reading that exact path the whole time. Fixed structurally, not just documented:
- `copilot/settings.py`: `DATABASES` now reads `SQLITE_DB_PATH` from env, defaulting to the old in-tree path for plain local dev without Docker.
- Both `docker-compose.yml` and `infra/docker-compose.prod.yml`: added a dedicated `sqlite_data` volume, mounted at `/data`, with `SQLITE_DB_PATH=/data/db.sqlite3` — outside the bind-mounted source tree entirely, so a `git pull` can never touch it again regardless of `.gitignore`.
- Added a root `.gitignore` (none existed for the Python/`server/` codebase at all) covering `db.sqlite3`, `.env`/`.env.production`, `__pycache__/`, etc.

**This means a fresh, empty database on first start after deploying this fix** — the old in-tree `db.sqlite3`'s data doesn't carry over automatically (fine at this stage; it's test data). **Also worth checking before anything else:** whether `server/.env`/`.env.production` were ever actually committed to git — if so, the Groq key and HF token should be treated as exposed and rotated, since nothing was stopping secrets from being tracked before this fix either.

**Update 7 — verified clean on the new volume, deployed live.** Full migration history applied correctly on first start (`0001_initial` through `0003_suggestion`, all OK). Three live questions tested end-to-end: all answered by Groq in under 600ms, all persisted, all rendered correctly in the Flutter UI with latency shown. **Phase 3's actual goal is confirmed done.**

Two remaining quality gaps, distinct from each other and from Update 5's RAM ceiling:
- **STT word errors persist** ("blackest" → "blockest") — same known, VM-bounded constraint as before, no new information.
- **New: unfiltered conversation context can produce wrong answers.** "Who is the Blackest Man Alive?" got answered "August." — traced to `ai/prompts.py` including the last 6 raw turns unfiltered; an earlier stray turn (`B: August.`) was still in that window and the model latched onto it instead of the actual question. Only surfaced because testing involved repeating the same phrase several times, filling context with noise a normal conversation likely wouldn't have — but the underlying gap (no filtering of short/irrelevant fragments from context) is real regardless.

### Small notes — what's actually left

- [x] **Context filtering (cheap, worth doing):** `_recent_turns_context` (`server/audio/consumers.py`) now over-fetches (`limit * 3`) and drops turns under `CONTEXT_MIN_WORDS` words (default 3, new env var — `.env.example`/`.env.production.example`) before trimming to `limit`, so a run of short/fragment turns can't starve the context window below `limit` usable turns. Fixes the "August." class of bug — a stray one-word turn getting latched onto as the answer to an unrelated question. `ai/prompts.py` untouched; this is fetch-time policy, not a formatting concern.
- [ ] **Extraction accuracy (bigger, already-decided-to-defer):** regex still misses implied questions with no `?`/wh-word (e.g. "the capital of Minnesota."). Options were laid out earlier (stay with regex vs. move to Groq-based extraction) — no decision made yet, not urgent.
- [ ] **Whisper accuracy ceiling:** `small` + `beam_size=5` (+ optional `WHISPER_INITIAL_PROMPT`) is what this VM can sustain; `medium`/`large` caused likely-OOM crashes. Real fix is more RAM or less BizYangu contention, not further tuning — infra decision, deliberately deferred.
- [ ] **`server/db.sqlite3.bak`** left on the VM from the volume migration — harmless, safe to delete whenever.
- [x] Flutter no longer shows "Live Session (**Phase 1**)" in the UI — retitled to "Live Session" in `live_session_screen.dart`.
- [x] **Cloud STT (final segments only):** `stt/cloud_router.py` added, mirroring `ai/cloud_router.py`'s exact shape — Groq Whisper (`whisper-large-v3-turbo`, same `GROQ_API_KEY` already configured for the LLM router) as primary, local `faster-whisper` engine (`stt/engine.py`) as fallback on missing key or any Groq-specific failure (auth/connection/timeout/rate-limit/empty response). New `STT_PROVIDER` (default `groq`) and `GROQ_WHISPER_MODEL` env vars in both `.env.example`/`.env.production.example`. **Deliberately scoped to `_finalize_segment` only, not `_emit_partial`** — partials fire every `PARTIAL_INTERVAL_S` mid-sentence, so routing those to a network call would burn Groq's rate limit fast and add round-trip latency to the thing that most needs to feel instant; local Whisper stays wired directly into the partial path unchanged. This was motivated by the VM's RAM ceiling (5.8GB total) being the likely bottleneck on final-turn transcription speed/accuracy now that the LLM side is already offloaded to Groq. Provider selection left abstracted (`STT_PROVIDER` branch in `get_final_transcription`) so Deepgram or AssemblyAI — real streaming STT, unlike Groq's chunk-based endpoint — can be added later as another branch without touching `audio/consumers.py`; not implemented yet, Groq was the chosen first step since it reuses the existing account/key with no new integration. `manage.py check` and `makemigrations --check --dry-run` both pass; **not yet tested against a live Groq call or on the VM** — next step is smoke-testing with a real `GROQ_API_KEY` and comparing final-turn latency/accuracy against local-only.

### Phase 3.5 — App Design (UI/UX) *(new — the app itself has not been designed yet)*

Goal: the product so far is a working pipeline behind two dev/test
screens, not a designed app. `latency_test.html`/the ping test and
`audio_test.html`/`live_session_screen.dart` (the "Live Session" test
harness) exist to prove the backend works — they were never meant to be
the shipped UI, and no actual screen design, navigation, or visual
identity has been done. This phase has to happen before Phase 4's
teleprompter polish means anything, since right now there's no app
around it to polish.

- [ ] Define the actual screen list and navigation flow — at minimum:
  **Home/dashboard** (start a new session, see recent sessions),
  **Live Session** (the real version of today's test harness — this is
  the one screen that already has a working prototype to build from),
  **Session History** (past sessions, searchable/filterable),
  **Session Detail/Replay** (a single past session's transcript +
  suggestions, feeds Phase 8's debrief later), **Settings** (answer
  level default, diarization toggle, provider keys if self-hosted,
  domain/mode selection once Phase 9 exists). All six screens — Live
  Session, Home/Dashboard, Session History, Session Detail/Replay,
  Settings, and the onboarding flow — are now defined via approved
  mockups (see below). Settings intentionally keeps STT
  provider/API key/server address secondary in the visual hierarchy —
  open question whether that section ships in the consumer build at
  all or stays a self-hosted/debug-only screen. Onboarding is a 3-step
  flow: welcome (amplitude-ring hero, states the product's purpose),
  mic permission pre-prompt (explains *why* before the OS dialog fires,
  notes the mic is only active during a session), and a "ready" step
  that mirrors Home's start-button visual language and closes with a
  low-stakes-first-call tip.
- [x] **Visual identity — decided.** Concept: the app is glanced at
  during a live, high-stakes conversation, often for a second or two —
  legibility at a glance matters more than decoration, so the direction
  leans "instrument panel," not generic AI-chatbot dark mode.
  **Color:** `ink #10121B` (deep indigo bg, not pure black) ·
  `panel #1B1E2E` · `panel-raised #262B40` · `signal-amber #E7A83D`
  (listening/live state) · `signal-blue #6FA3D8` (suggestion/insight
  state) · `paper #EDE9E2` text · `mist #8A8FA6` muted text. The two
  accents are load-bearing, not decorative — amber means "the system
  is listening," blue means "here's a suggestion." **Type:** Space
  Grotesk (titles/timers) · IBM Plex Sans (transcript body) · IBM Plex
  Mono (timestamps, confidence %, latency — the instrument-readout
  register). **Signature element:** a segmented amplitude ring around
  the mic button that lights up amber with live voice level — makes
  the VAD's own signal visible instead of just a status word.
- [ ] Onboarding flow: first-run experience, mic permission request
  (Android's permission prompts need a clear "why" screen before the
  OS dialog, not a cold ask), how a new user starts their first session.
- [ ] Empty states, error states, loading states for each screen above —
  the test harness only has a "Not connected." status line; a shipped
  app needs real states for e.g. no sessions yet, connection lost
  mid-session, mic permission denied.
- [x] Decide whether `live_session_screen.dart`'s existing layout
  (two-box full-transcript + extracted/suggestions split) carries
  forward into the designed app largely as-is, or gets rethought once
  it's sitting inside real navigation instead of standing alone.
  **Decided: rethought, not carried forward as-is.** The transcript +
  suggestions split survives structurally, but the answer-level toggle
  moved into a top telemetry strip (rarely touched, doesn't need prime
  real estate), suggestion cards got a blue left-accent treatment to
  read as distinct from transcript at a glance, and the mic control
  became the amplitude-ring centerpiece described above.
- [x] Once screens are defined: wireframes (low-fidelity, layout only)
  → high-fidelity mockups (Figma or similar) → hand off to Flutter
  implementation, screen by screen. **All six screens mocked and
  approved** — **Live Session** (`live_session_mockup.html`),
  **Home/Dashboard** (`home_dashboard_mockup.html`), **Session
  History** (`session_history_mockup.html`), **Session Detail/Replay**
  (`session_detail_mockup.html`, tabs for Transcript/Suggestions/
  Summary — Summary tab is a placeholder reserved for Phase 8's
  debrief content, not yet built), **Settings**
  (`settings_mockup.html`, Advanced/self-hosted section kept visually
  secondary, Domain mode row tagged "soon" for Phase 9), and
  **Onboarding** (`onboarding_mockup.html`, 3-step: welcome → mic
  permission pre-prompt → ready/first-session tip). None of the six
  approved mockups have been wired into actual Flutter widgets yet —
  they're static HTML references for visual direction, not
  implementation. **Next up:** implementation phase — turning these
  six mockups into real Flutter screens/navigation.
- [x] **Implementation — all six mockups wired into real Flutter
  screens/navigation.** `theme.dart` holds the shared design tokens
  (colors, `CopilotHeader`, `CopilotGroup`, `SectionLabel`) so every
  screen pulls from one source instead of re-declaring the palette.
  `main.dart`'s `AppShell` is a bottom-nav shell across
  **Home / History / Settings** (`NavigationBar`); this is a deliberate
  deviation from the mockup's gear-icon-only Settings access — three
  root destinations map more naturally onto Flutter's tab idiom, and
  Home's gear icon still jumps straight to the Settings tab, so the
  one-tap access the mockup intended is preserved. Onboarding
  (`onboarding_screen.dart`) gates the app shell on first run; there's
  no persistence layer yet, so "seen onboarding" is in-memory only and
  resets on cold start — a `shared_preferences`-backed flag is
  follow-up work once that dependency is added. `home_screen.dart`,
  `session_history_screen.dart` (search + filter chips + month
  grouping), `session_detail_screen.dart` (stats row, Transcript/
  Suggestions/Summary tabs — Summary tab is still the Phase 8 debrief
  placeholder), and `settings_screen.dart` (session defaults,
  in-memory only; Advanced section read-only for now, plus a
  "Connectivity test" row that reaches the old Phase 0 ping harness)
  are all new. `models/session_summary.dart` holds the shared
  mock-session data these three screens render — still no backend
  endpoint for past sessions, so this is placeholder data as noted
  when the model was first sketched. `live_session_screen.dart` was
  restyled in place to the telemetry-strip/tabs/amplitude-ring layout
  from `live_session_mockup.html`; all WS/mic logic (connect, message
  handling, answer-level switching, full-transcript/extracted-entries
  split) is unchanged from the working Phase 4 version, only `build()`
  changed. The amplitude ring reacts to a steady pulse while recording,
  not real per-sample audio level — `MicCapture` doesn't expose an
  amplitude stream yet, noted in the ring's own docstring as a
  follow-up if a true VAD level meter is wanted later.
- **Not yet done — same verification gap as Phase 4 below:** this
  implementation pass was checked with the same brace/paren-balance
  script used previously (all files pass), not a real `flutter
  analyze` or build, for the same sandbox-has-no-Flutter-toolchain
  reason. Treat this as reviewed-by-hand, not compiler-verified, until
  `flutter analyze` / `flutter run -d chrome` actually run against it.

### Phase 4 — Teleprompter UI

- [x] **Quick / Medium / Detailed answer level switching** — `ai/prompts.py` gained `ANSWER_LEVELS` (per-level instruction + token budget, env-overridable via `LLM_MAX_TOKENS_QUICK/MEDIUM/DETAILED`), threaded through `ai/local_llm.py` and `ai/cloud_router.py`. `Suggestion.answer_level` persists which level produced each row (migration `0004_suggestion_answer_level.py`). `AudioStreamConsumer` tracks the level per-connection, accepts `{"type": "set_answer_level", "level": ...}` from the client (silently normalized to `medium` on anything unrecognized — see `ai.prompts.normalize_level`), and snapshots the level at generation-start so a mid-flight switch can't relabel an in-progress suggestion. Flutter: `live_session_screen.dart` has a `SegmentedButton` (Quick/Medium/Detailed) that sends `set_answer_level` and reflects the server's ack.
- [x] **Confidence score display** — suggestion messages/rows now carry `extraction_confidence` (the rule-based extractor's confidence that the source text was actually a question — see `conversation/intent.py`), rendered as a badge under each suggestion bubble alongside level and latency. **Deliberately not a fabricated LLM self-confidence score** — the build plan's own Phase 7 notes already flagged that as something to pair with a cheaper proxy signal once Phase 6's RAG exists, not to trust alone; extraction confidence is a real, already-computed signal instead of inventing a new unreliable one.
- [x] Live Session screen polish — title de-Phase-1'd (previous session), suggestion bubbles restructured (level/confidence/latency badges row under the answer text rather than crammed inline).
- [x] **Split into two boxes: full transcript + extracted/suggestions.** Previously the single transcript box mixed per-turn (VAD-segmented) bubbles with suggestion cards and a flickering partial-preview row — no way to see the conversation as one continuous stream, only as disconnected finalized fragments. `live_session_screen.dart` now renders two separate boxes: a **"Full transcript"** box (new `_fullTranscriptLines` list) that appends every finalized turn's raw, unbadged, unfiltered text in order, with the live in-progress `partial` trailing at the end in italic — this is genuinely everything transcribed, nothing extracted or scored out; and the existing **"Extracted questions & suggestions"** box unchanged in content (finalTurn entries with speaker/confidence badges, suggestion cards with level/extraction-confidence/latency), just no longer sharing space with the live partial preview. Client-side only — no server or WS-protocol changes, since `partial`/`final` messages already carried everything needed; `_fullTranscriptLines` is cleared on each fresh `session_started` so reconnects don't carry stale text over within the same widget lifetime.
- [ ] Floating overlay via `flutter_overlay_window` (`SYSTEM_ALERT_WINDOW`-based) — this worked in this app's v1 Flutter build; **confirm it still works on current Android versions/permissions before relying on it** (Android has tightened overlay permission flows across versions), but this is now a verification task, not an open research spike. In-app persistent bar remains the fallback if a permission or OS-version issue surfaces.

**Not yet done — verification gap:** the answer-level/confidence work above was validated on the server side only (`manage.py check`, `makemigrations --check --dry-run`, and a fresh `migrate` all pass; `ai/prompts.py`'s logic spot-checked directly). The Flutter changes (`live_session_screen.dart`, `ws/socket.dart`) were reviewed by hand — brace/paren balance, Dart 3 switch-expression and `SegmentedButton` (Material 3, confirmed enabled in `main.dart`) usage checked manually — but **not run through `flutter analyze` or a real build**, since this sandbox has no Flutter/Dart toolchain and no network path to install one (Flutter's SDK isn't on the allowed-domains list). Run `flutter analyze` and a `flutter run -d chrome` smoke test as the next step before trusting this compiles. **Same gap applies to the full-transcript/two-box split above** — checked with the same brace-balance script, not the real toolchain.

### Phase 5 — Conversation Memory

- [ ] Persist people, topics, numbers, dates, promises, Q&A pairs per session
- [ ] Cross-session memory (recall from past conversations, if desired)
- [ ] **Open decision — user domain/expertise profile:** should the app
  know what field the user is in (e.g. software engineering vs. sales
  vs. teaching) and use that to shape suggestions — vocabulary,
  assumed background knowledge, the kind of answer a domain expert
  would actually give, not a generic one? Not yet decided whether this
  is a real requirement or nice-to-have. If pursued, the natural home
  is a simple stored user profile (field/role, maybe a short
  free-text description) fed into `ai/prompts.py`'s suggestion prompt
  as fixed context — a lighter version of what Phase 9's domain modes
  do with prompt templates, but per-user rather than per-session-mode.
  Revisit once Phase 9 exists, since the two overlap: Phase 9 picks a
  mode per session, this would set a default based on who the user is.

### Phase 6 — Personal Knowledge Base (RAG)

- [ ] Document upload endpoint (PDF, text, notes)
- [ ] Embedding + FAISS indexing pipeline
- [ ] Retrieval integrated into Phase 3's suggestion pipeline
- [ ] Now that this runs server-side, indexing can be heavier/more accurate than the original mobile-constrained plan allowed

### Phase 7 — Hybrid AI Router *(superseded — pulled forward into Phase 3 as of v4)*

- [x] ~~Routing logic: question difficulty + retrieval quality + local model confidence → local LLM or cloud LLM~~ — see Phase 3; simplified to "Groq primary, local 3B fallback on failure/rate-limit" rather than a difficulty-scored router, since Groq's free tier is fast enough to just be the default path
- [ ] Pair self-reported model confidence with a cheaper proxy signal (e.g. retrieval hit quality, or resampling consistency) rather than trusting confidence scores alone — still relevant once Phase 6's RAG exists, revisit then
- [x] ~~Cloud provider abstraction: OpenRouter, Groq, Gemini, switchable~~ — `ai/cloud_router.py` (Phase 3) starts with Groq only; keep it abstracted enough to add OpenRouter/Gemini later if Groq's free tier limits become a problem

### Phase 8 — Debrief

- [ ] Post-session scoring: questions asked, answered, strong/weak topics
- [ ] Alternative-answer replay (your answer vs. better vs. excellent)
- [ ] Suggestions summary

### Phase 9 — Domain Modes

- [ ] Mode-specific prompt templates, vocabulary, and debrief metrics: Interview Practice, Sales, Engineering, Customer Support, Public Speaking, Teaching, Negotiation, Language Practice, General

### Phase 10 — Distribution & Licensing *(new — deferred, not needed for the current build/test cycle)*

Goal: sell the app as a **one-time purchase**, not a subscription — no
account system for v1.

- [ ] **Decided:** no login/account/subscription system for v1. The app
  is sold as a one-time fee, so there's no per-user server-side
  identity to build or maintain right now — this removes an entire
  category of work (auth, password reset, user table beyond what
  `Session`/`Turn` already need) from the near-term plan.
- [ ] **Open decision — how a purchase is verified without an account
  system:** most likely path is store-based licensing (Google
  Play/App Store purchase receipt validation on launch) rather than a
  custom license-key server, since that avoids standing up any new
  backend just for entitlement checks. Needs a decision once the app
  is closer to shippable — not urgent now.
- [ ] **Revisit if this ever changes:** cross-session Conversation
  Memory (Phase 5) and any future "sync across devices" want *some*
  durable identity even without a subscription model — a purchase
  receipt or device-bound key could stand in for a full account, but
  that's a design choice for when Phase 5 is actually being built, not
  before.

### Phase 0.5 — CI/CD & VM Deployment *(new in v3 — build right after Phase 0's local proof-of-concept works)*

Goal: get `git push` → live on the VM working before more app logic piles up on top of a manual deploy process.

- [x] VM identified: existing Oracle Cloud VM (`bizyangu-vnic`, 145.241.102.99), shared with BizYangu — no new provisioning needed
- [ ] **Revised:** no Nginx — extend BizYangu's existing **Caddy** container (already owns 80/443) with a second Caddyfile site block for Copilot, on the same Docker network so Caddy can reach Copilot's backend container by name
- [ ] TLS via Caddy's automatic HTTPS against `145-241-102-99.sslip.io` (free, zero-setup hostname for the existing IP — no domain purchase, no manual certbot step)
- [ ] `docker-compose.prod.yml` — same services as dev (Redis, backend) minus the `cloudflared` service (no longer needed) and **not** publishing 80/443 directly (Caddy owns those, proxies internally over the shared Docker network)
- [ ] GitHub Actions workflow (`.github/workflows/deploy.yml`): on push to `main` — run tests → build image → deploy to VM (SSH + `docker compose pull && up -d`, or rsync + rebuild on-box, whichever is simpler for a single-VM setup) → restart service
- [ ] Secrets (`.env` values, VM SSH key) stored as GitHub Actions secrets, never committed
- [ ] Confirm a full deploy cycle end-to-end: push a trivial change, watch it go live on the VM without manual intervention
- [ ] Re-run Phase 0's latency ping test against the deployed VM (not local/tunnel) and update the decision-gate numbers

### Stretch — Completion Mode *(gated on Phase 0 numbers)*

- [ ] Only prototype this if Phase 0's round-trip latency is consistently under roughly 250–300ms
- [ ] If latency doesn't support it, leave parked rather than forcing a laggy, unusable feature
- [ ] If viable: mid-sentence prediction pipeline, streamed token-by-token back to the phone

---

## 5. What You Can Start Building Today

Concrete first steps, in order:

1. `django-admin startproject copilot` inside `server/`, add Channels, confirm ASGI runs locally.
2. Install `cloudflared`, run `cloudflared tunnel --url http://localhost:8000` against the dev server, confirm you can hit it from your phone's browser over cellular data (not home wifi) — this is the real test of the tunnel working.
3. `flutter create flutter_app` for the frontend shell, add a bare WebSocket client (`web_socket_channel` package), connect it to the VM's `wss://` URL.
4. Run the Phase 0 latency ping test and record the numbers — this single measurement shapes several downstream decisions (Completion Mode viability, teleprompter refresh rate expectations, whether to buffer or stream audio).

---

## 6. Open Decisions to Confirm Before Phase 1

- ~~React or Vue for the Capacitor frontend?~~ — superseded (v4): Flutter/Dart, no framework choice needed
- SQLite or Postgres from the start? (SQLite is fine solo; Postgres only matters if you want concurrent sessions or eventually multi-device sync)
- Which local LLM size to benchmark first for Phase 2/3 — this depends on the VM's actual RAM/GPU, which is itself an open decision (see below)

## 6a. Open Decisions for v3 (VM + CI/CD)

- **VM provider:** ~~bare VM (DigitalOcean/Hetzner/Vultr-style)...~~ — **resolved:** existing Oracle Cloud (OCI) VM (`bizyangu-vnic`, 145.241.102.99), shared with the BizYangu deployment
- **VM specs (measured):** Ubuntu 22.04, 2 vCPU (AMD EPYC 7742), **5.8GB RAM total (~4.5GB available)**, 35GB free disk, Docker 29.6.2 + Compose v5.3.1 already installed, Nginx not yet installed
- **Port conflict:** BizYangu is fronted by **Caddy** (not raw Docker port publishing) — `bizyangu_caddy` container owns host ports 80/443. Copilot **cannot** run Nginx alongside it (confirmed: Nginx installed and refused to start, "port 80 already in use"). **Resolution (revised):** extend BizYangu's existing Caddyfile with a second site block for Copilot instead of standing up a separate reverse proxy — Caddy already handles automatic HTTPS (including for sslip.io hostnames), so this is simpler than the Nginx+certbot plan it replaces. Nginx has been disabled on the VM as unnecessary.
- **Domain/TLS:** no domain yet, using the raw IP for now. **Resolved via [sslip.io](https://sslip.io)**: `145-241-102-99.sslip.io` resolves to the VM's IP with zero setup, and Caddy's automatic HTTPS will issue a real Let's Encrypt cert against it directly from the Caddyfile (no certbot step needed) — gives real `wss://` for free without buying a domain. Swap to a real domain later by just changing the Caddyfile site address.
- **VM sizing / LLM strategy:** 5.8GB RAM / 2 vCPU rules out a 7–8B local model comfortably fitting alongside Django/Redis/Whisper. **Resolved as hybrid:** local **3B-class** model (`Qwen2.5-3B-Instruct` or `Llama-3.2-3B-Instruct`, Q4_K_M GGUF) as the always-available, no-network fallback and for Phase 2 extraction; **Groq's free tier** (LPU hardware, genuinely free, not trial-limited) as the primary path for actual suggestion generation — far faster than 2 vCPUs could do locally even if RAM allowed a bigger model. This means **Phase 7 (Hybrid AI Router) moves up and merges into Phase 3** rather than staying a late-stage phase — see Phase 3 update below.
- **Deploy mechanism:** SSH+rsync straight onto the VM vs. push image to a container registry and pull — rsync is simpler for a single-VM setup with no orchestration needs

---

## 7. Delivery Convention

- **Every handoff is a ZIP, structurally matching the original upload.** Root folder name `Conversation-copilot-claude-copy/`, same relative paths as the source zip (`server/...`, `flutter_app/...`, `infra/...`, this file at the root, etc.) — not a flat dump of individual changed files. Build caches, virtualenvs, `__pycache__/`, `.dart_tool/`, `.gradle/`, `.idea/`, `node_modules/`, and `db.sqlite3` are excluded, same as they'd be excluded from git.
- **Every ZIP is accompanied by a one-sentence commit message** summarizing that delivery's change, suitable for pasting straight into `git commit -m "..."`.
- This applies from here onward for all future sessions on this project, not just the change that introduced this note.
