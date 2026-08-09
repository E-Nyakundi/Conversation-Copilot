# Conversation Copilot — No Tunnel Setup

Your project is now configured to run **locally without Cloudflare Tunnel** or any external tunnel service.

## Quick Start

### 1. Start the Services

```bash
docker compose up --build redis backend
```

This starts:
- **Redis** (message broker) on port 6380
- **Backend** (Django + Daphne) on port 8000

### 2. Access the UI

Open your browser and go to:
```
http://localhost:8000/
```

The frontend (`index.html`) is served directly by the Django backend.

### 3. Test Audio Streaming

1. Click **Connect** to establish a WebSocket connection.
2. Click **Start** to begin microphone recording.
3. Speak into your microphone.
4. You'll see:
   - **Partial** transcripts (live updates) in gray italic text
   - **Final** transcripts once a silence gap is detected
   - Confidence scores for each utterance
5. Click **Stop** to stop recording.
6. Click **End Session** to close the session and database record.

The debug log at the bottom shows frame transmission counts and events.

## Architecture

```
Browser (localhost:8000)
    ↓ WebSocket connection (ws://localhost:8000/ws/audio/)
    ↓ Raw PCM16 audio frames (16kHz mono)
Django Backend (port 8000)
    ↓
AudioStreamConsumer (Channels WebSocket handler)
    ↓ VAD (Silero, 512-sample frames @ 16kHz)
    ↓ Speech detection → buffer audio
    ↓ ~600ms silence → finalize segment
    ↓ STT (faster-whisper "small" model)
    ↓ Speaker label + Turn record → DB
    ↓ Send "final" message + extracted items back to client
```

## Key Files

- **`index.html`** — Modern, responsive UI. No external CDN dependencies.
- **`server/audio/consumers.py`** — WebSocket consumer, VAD pipeline, STT calls.
- **`server/stt/engine.py`** — Whisper model wrapper (lazy-loaded, singleton).
- **`server/audio/vad.py`** — Silero VAD wrapper (frame-by-frame speech detection).
- **`docker-compose.yml`** — No tunnel service (removed `cloudflared`).

## Configuration

All settings are in `server/.env`:

```env
# STT Model (small, tiny, base, medium, large)
WHISPER_MODEL_SIZE=small

# VAD Threshold (0.0-1.0; lower = more sensitive)
VAD_THRESHOLD=0.5

# Silence duration before segment finalization (milliseconds)
VAD_SILENCE_MS=600

# Minimum segment duration to avoid VAD blips (milliseconds)
MIN_SEGMENT_MS=250

# Partial transcript interval (milliseconds)
PARTIAL_INTERVAL_MS=1200
```

## Accessing from Phone (Optional)

To test from a phone on the same network:

1. Find your computer's IP:
   ```bash
   ipconfig  # Windows
   ifconfig  # macOS/Linux
   ```

2. Replace `localhost` in the browser with your IP, e.g.:
   ```
   http://192.168.1.100:8000/
   ```

3. Ensure both devices are on the same network (WiFi or LAN).

## REST API Endpoints

- **POST `/api/sessions/`** — Create a new session
- **GET `/api/sessions/`** — List all sessions
- **GET `/api/sessions/<id>/`** — Get session details + all turns
- **GET `/api/sessions/<id>/extractions/`** — Get extracted items
- **POST `/api/sessions/<id>/end/`** — Mark session as ended

## Troubleshooting

### "WebSocket connection failed"
- Check `docker logs conversation-copilot-backend-1` for Django errors
- Ensure port 8000 is not in use: `netstat -an | findstr 8000`
- Try `docker compose down` then `docker compose up --build`

### "Microphone permission denied"
- Browser may require HTTPS for microphone access
- On localhost, most modern browsers allow HTTP microphone access
- Check browser console (F12) for specific permission errors

### "No transcripts appearing"
- Check the debug log for `[AudioStreamConsumer]` messages
- VAD may be filtering out noise; lower `VAD_THRESHOLD` in `.env`
- Whisper model may still be downloading; wait for first run to complete

### "Slow transcription"
- First inference is slow (model warm-up). Subsequent transcripts are faster.
- Use smaller model: `WHISPER_MODEL_SIZE=tiny`
- Check CPU/memory with `docker stats`

## Next Steps

- **Add speaker diarization** — detect speaker identity (optional, Phase 2)
- **Add extraction rules** — extract questions, action items, decisions (optional, Phase 2)
- **Optimize latency** — batch VAD frames, stream Whisper (Phase 3)
- **Deploy to production** — use ngrok, AWS, or fixed IP + reverse proxy
