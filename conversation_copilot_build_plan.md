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
- [ ] Flutter app scaffolded, connects to the VM's `wss://` URL directly (no tunnel)
- [ ] Minimal round-trip test: phone sends a text ping over WebSocket → server echoes it back → measure latency across at least 3 network conditions (phone on cellular, phone on wifi, from a different city/region if possible) — **re-run this against the VM**, don't carry over the old home-wifi/tunnel numbers below, since the network path has changed
- [ ] Audio streaming proof of concept: phone mic → chunked PCM/Opus → WebSocket → server writes to a file, confirm no corruption/drift
- [ ] **Decision gate:** record actual round-trip latency numbers here. This number determines whether Completion Mode (stretch phase, below) is ever attempted. (Old home-wifi-via-tunnel number: ~273–278ms round trip — kept for reference only, not valid for the VM path.)

### Phase 1 — Local Conversation Listener

- [x] Server-side VAD (Silero) running on incoming audio stream — `audio/vad.py`, lazy-loaded singleton, 512-sample/16kHz frames
- [x] Streaming STT (faster-whisper) producing partial + final transcripts — `stt/engine.py` + `audio/consumers.py` (`AudioStreamConsumer`); partials on a timer while speaking, final on VAD silence close
- [x] Basic speaker separation (2-speaker case first) — `audio/speaker.py`: naive pause-based A/B labeler by default; real `pyannote.audio` diarization wired in as opt-in (`ENABLE_DIARIZATION=1`)
- [x] Session model in Django: `Session`, `Turn` (speaker, text, timestamp, confidence) — `conversation/models.py`, migrated, admin-registered
- [ ] Live transcript visible in **the Flutter app** during a session — Flutter app doesn't exist yet (not in this repo). Stood up `server/audio_test.html` (mic → WS → live transcript in a browser) as the Phase 1 equivalent of `latency_test.html`, so the pipe is confirmed clean end-to-end without blocking on frontend scaffolding. Swap this box to done once the actual Flutter screen exists.

**Verified via a scripted smoke test** (VAD/STT monkeypatched to isolate the segmentation state machine): connect → speech frames → silence closes the segment → STT called → speaker labeled → `Turn` persisted → `final` message sent → matches DB; session resume via URL; two-turn A→B naive speaker flip across a silence gap. Not yet tested against a real mic/real model on real hardware — that's the actual Phase 1 acceptance test and needs your server.

### Phase 2 — Conversation Understanding *(feasibility-gated — do not skip the checkpoint)*

- [x] Build extraction module for: questions, decisions, requests, objections, action items, topic changes — `conversation/intent.py`, rule-based (regex/keyword), zero extra deps, runs inline after each finalized `Turn`
- [ ] Benchmark 2–3 local LLM sizes for extraction accuracy vs. latency against the rule-based baseline above — not started; needs llama.cpp + a GGUF model, which isn't wired in yet
- [x] Test against a set of real or realistic transcript samples, score extraction accuracy manually — `conversation/fixtures/sample_transcripts.json` (3 sample dialogues) + `python manage.py score_extraction`
- [ ] **Go/no-go checkpoint:** rule-based baseline scored manually against the 3 bundled sample transcripts — question/decision/request/objection/action_item all look solid; topic_change is the weak one (turn-level lexical overlap is a genuinely weak signal, see the caveat in `intent.py`). Rescore against your own real transcripts before treating this as settled, and don't lean on topic_change downstream until it's been tightened or replaced.

### Phase 3 — AI Copilot (basic suggestions) *(v4: now includes the hybrid router, pulled forward from Phase 7)*

- [x] Pipeline: detected question → conversation context → knowledge base (if available) → local LLM → suggested answer — `ai/local_llm.py` (llama.cpp via llama-cpp-python, lazy singleton, same deferred-import pattern as `stt/engine.py`/`audio/vad.py`) + `ai/prompts.py` (prompt construction) + `audio/consumers.py` (`_generate_suggestion`/`_extract_and_emit` wiring). No knowledge base yet — that's Phase 6, and `ai/prompts.py` is structured so RAG context slots in later without a rewrite.
- [x] Single-shot suggestions only (no completion mode yet) — one `create_chat_completion` call per detected question, no streaming tokens.
- [ ] Suggestion delivered to phone over WebSocket, rendered in a basic UI — WebSocket delivery is done (`{"type": "suggestion", ...}`, fired as a background `asyncio.create_task` per question so slow CPU inference never blocks live audio ingestion on that connection); persisted to a new `Suggestion` model (`conversation/models.py`, migration `0003_suggestion.py`) and exposed via `GET /api/sessions/<id>/suggestions/`. "Phone" is still `server/audio_test.html` standing in for the not-yet-built Flutter screen, same as Phase 1 — it now renders suggestion bubbles too. Swap to done once the actual Flutter screen exists.
- [ ] **LLM strategy resolved (v4):** `LLM_MODEL_PATH` → a **3B-class** GGUF model (`Qwen2.5-3B-Instruct` or `Llama-3.2-3B-Instruct`, Q4_K_M) sized for the VM's actual 5.8GB RAM / 2 vCPU ceiling — this is the always-available, no-network fallback, not the primary path.
- [ ] **New:** `ai/cloud_router.py` — Groq client (free tier, OpenAI-compatible API) as the **primary** suggestion path; falls back to the local 3B model on network failure or rate-limit. Needs a free Groq API key in `.env` (`GROQ_API_KEY`).
- [ ] Benchmark both paths' actual latency on the VM once wired — this replaces the old "measure CPU inference latency" open question with a concrete comparison (local 3B vs. Groq round-trip) instead of assuming one is obviously faster.

### Phase 4 — Teleprompter UI

- [ ] Quick / Medium / Detailed answer level switching
- [ ] Confidence score display
- [ ] Live Session screen polish
- [ ] Floating overlay via `flutter_overlay_window` (`SYSTEM_ALERT_WINDOW`-based) — this worked in this app's v1 Flutter build; **confirm it still works on current Android versions/permissions before relying on it** (Android has tightened overlay permission flows across versions), but this is now a verification task, not an open research spike. In-app persistent bar remains the fallback if a permission or OS-version issue surfaces.

### Phase 5 — Conversation Memory

- [ ] Persist people, topics, numbers, dates, promises, Q&A pairs per session
- [ ] Cross-session memory (recall from past conversations, if desired)

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
