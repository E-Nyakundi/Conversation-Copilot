# Conversation Copilot — Backend (Phase 0 + Phase 1)

Dockerized from the start: Django + Channels (ASGI/WebSocket) + Redis, ready to sit behind a Cloudflare Tunnel.

## What's included right now

**Phase 0 (infra/feasibility):**
- `docker-compose.yml` — Redis, Django/Channels backend, optional Cloudflare Tunnel service
- `server/audio/consumers.py` — `PingConsumer` for round-trip latency testing
- `server/latency_test.html` — a zero-dependency page to test WebSocket
  latency from your phone's browser before the Capacitor app exists

**Phase 1 (local conversation listener) — new:**
- `server/audio/vad.py` — Silero VAD wrapper (lazy-loaded singleton, 512-sample/16kHz frames)
- `server/stt/engine.py` — faster-whisper wrapper (lazy-loaded singleton, produces text + a confidence proxy from avg_logprob)
- `server/audio/speaker.py` — 2-speaker labeling: a naive pause-based A/B labeler (default, zero extra deps) and an opt-in real diarizer via `pyannote.audio` (`ENABLE_DIARIZATION=1`)
- `server/audio/consumers.py` — `AudioStreamConsumer`: accepts raw PCM16/16kHz audio over WebSocket, runs VAD to segment speech, emits partial transcripts while speaking and a final transcript + persisted `Turn` when a segment closes
- `server/conversation/models.py` — `Session` and `Turn` models (+ Django admin registration for quick inspection)
- `server/api/` — REST endpoints for session management (`/api/sessions/`, `/api/sessions/<id>/`, `/api/sessions/<id>/end/`)
- `server/audio_test.html` — a zero-dependency page that captures your mic, streams it to `AudioStreamConsumer`, and renders the live transcript — the Phase 1 equivalent of `latency_test.html`, standing in until the Capacitor app exists

## 1. First run (local only, no tunnel yet)

```bash
cd conversation-copilot
cp server/.env.example server/.env
docker compose up --build redis backend
```

Visit `http://localhost:8000/health/` — should return `{"status": "ok"}`.

## 2. Set up the Cloudflare Tunnel (Phase 0, step 2)

```bash
# One-time, outside Docker:
cloudflared tunnel login
cloudflared tunnel create copilot
```

This gives you a tunnel token. Put it in `server/.env` as `TUNNEL_TOKEN`.
Then either:

- run `cloudflared` as its own compose service: `docker compose up cloudflared`
- or run it directly on the host: `cloudflared tunnel run copilot`

Either way, note the public `https://...trycloudflare.com` (or your own
domain, if you've mapped one) — you'll use `wss://` + that host for
WebSocket connections.

## 3. Run the latency test (Phase 0, step 4)

1. Open `server/latency_test.html` directly in a browser (no server needed to view it).
2. Enter `wss://<your-tunnel-host>/ws/ping/`.
3. Click Connect, then Send Ping a handful of times.
4. **Do this on your phone over cellular data, not home wifi** — that's the
   realistic case the whole architecture depends on.

Record the round-trip numbers. This determines whether Completion Mode
(the stretch phase) is ever worth attempting later.

### Recorded numbers (Phase 0 decision gate)

| Network | Round trip | Up | Down |
|---|---|---|---|
| Home wifi (computer, via Firefox) | ~273–278ms | ~211ms | ~61ms |
| Phone, cellular data | _pending_ | | |
| Phone, different wifi network | _pending_ | | |

Home-wifi number sits right at the edge of the ~250–300ms Completion Mode
threshold in the build plan. The cellular number is the one that actually
decides it — home wifi latency is not representative of the real usage
path.

## 4. Try Phase 1: live transcript over WebSocket

```bash
docker compose up --build redis backend
```

Then, either locally or over the tunnel from your phone:

1. Open `server/audio_test.html` directly in a browser.
2. Enter `wss://<host>/ws/audio/` (omit a session id to start a new one).
3. Click **Connect**, then **Start Mic**, and talk. You should see a live
   `partial` line update while you're speaking, replaced by a finalized
   `A: ...` / `B: ...` line a beat after you pause.
4. Click **End Session** when done — this flushes any in-progress segment
   and marks the session ended in the DB.

Check `/api/sessions/` and `/api/sessions/<id>/` to see persisted sessions
and their turns (also visible in `/admin/` if you've created a superuser
with `python manage.py createsuperuser`).

### First-run notes for Phase 1

- **Model downloads on first use, not first boot.** Both Silero VAD
  (via `torch.hub`, cached under `~/.cache/torch/hub`) and the
  faster-whisper model (cached under `~/.cache/huggingface`) download
  lazily the first time a WebSocket connection actually sends audio — not
  when the container starts. Expect the *first* session to have a delay
  before transcripts start flowing; subsequent runs are fast.
- **`WHISPER_MODEL_SIZE` is an unmeasured starting guess** (`small`, per
  `.env.example`) — the build plan's Phase 2 explicitly calls for
  benchmarking 2–3 sizes against your actual server hardware once
  extraction work starts. Don't read `small` as a considered choice.
- **Speaker labeling is naive by default**: an A/B flip triggered by a
  configurable silence gap (`SPEAKER_FLIP_GAP_SECONDS`, default 0.7s) —
  see `server/audio/speaker.py` for exactly what it will and won't get
  right. Real diarization via `pyannote.audio` is wired in but opt-in
  (`ENABLE_DIARIZATION=1` + `HF_TOKEN` — needs accepting
  `pyannote/speaker-diarization-3.1`'s terms on huggingface.co first).
- **Partial transcripts are not persisted** — only finalized `Turn`s hit
  the DB, by design (see the docstring in `conversation/models.py`).

## 5. Try Phase 2: conversation understanding (extraction)

Phase 2 runs automatically as part of Phase 1's pipeline — no separate
setup needed. Every time a `Turn` finalizes, `conversation/intent.py` runs
against its text and, if anything matches, persists `Extraction` rows and
sends an extra WebSocket message:

```json
{"type": "extraction", "turn_id": "...", "items": [
  {"type": "question", "text": "...", "confidence": 0.9}
]}
```

Extraction types: `question`, `decision`, `request`, `objection`,
`action_item`, `topic_change`.

- Check `/api/sessions/<id>/extractions/` (optionally `?type=question`) to
  see everything extracted from a session so far.
- Also visible per-session in `/admin/` alongside turns.

### Scoring the extractor (the build plan's go/no-go checkpoint)

```bash
python manage.py score_extraction                      # all 3 sample transcripts
python manage.py score_extraction --transcript sales_call
python manage.py score_extraction --type question
```

This runs the extractor against `conversation/fixtures/sample_transcripts.json`
and prints every line next to whatever got extracted from it, so you can
read through and judge false positives/negatives by eye — that's the
actual checkpoint, not something a script can grade for you. Add your own
transcripts to that fixture file (or swap in real ones) before treating
this as settled.

### What Phase 2 is, and isn't, right now

- **Rule-based (regex/keyword), not an LLM.** This is a deliberate first
  step, not a placeholder: it gives the go/no-go checkpoint something to
  score before an LLM-based extractor is worth benchmarking against, per
  the build plan. When you do get to benchmarking local model sizes,
  compare them against this baseline's accuracy *and* latency — zero
  extra deps, no model load, runs inline in the WebSocket handler with no
  noticeable delay.
- **Five of six types score well** against the bundled samples: question,
  decision, request, objection, action_item. **`topic_change` is weak** —
  turn-level lexical overlap between consecutive turns is a real but
  noisy signal; see the caveat comment in `intent.py` before relying on it
  for anything downstream (e.g. Phase 4 teleprompter targeting).
- Extraction is best-effort and never blocks the transcript: it runs
  after the `final` WebSocket message is sent, and any exception in it is
  swallowed rather than taking the session down.

## Next steps (Phase 3 — AI Copilot, basic suggestions)

- Pipeline: detected question (from Phase 2's `Extraction` rows) →
  conversation context → knowledge base (if available) → local LLM →
  suggested answer
- Single-shot suggestions only (no completion mode yet)
- Suggestion delivered to phone over WebSocket, rendered in a basic UI

## Notes

- `DEBUG=1` and a placeholder `SECRET_KEY` are fine for local dev only —
  tighten both before exposing this beyond your own testing.
- SQLite is used for now per the build plan; swap to Postgres later only if
  you need concurrent sessions or multi-device sync.
- No auth is on the WebSocket/REST endpoints yet — fine for solo testing
  behind your own tunnel, but worth a token check before this is reachable
  by anyone besides you.