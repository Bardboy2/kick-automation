#!/usr/bin/env python3
"""
caption_editor.py — Web-based caption review & editor
Opus Clips-inspired dark UI with:
  - Dashboard listing all clips by status
  - Per-clip video player (Landscape / Portrait Crop / Portrait Black BG tabs)
  - Editable word chips with re-burn button
  - Approve / Reject actions

Start:  python scripts/caption_editor.py
Open:   http://localhost:8888  (or http://LOCAL_IP:8888 from your phone)
"""

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import db

try:
    from flask import Flask, Response, abort, jsonify, render_template_string, request, send_file
except ImportError:
    print("Flask not installed. Run: pip install flask", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [editor] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
PORT = int(os.environ.get("CAPTION_EDITOR_PORT", "8888"))

# Reburn lock — one at a time per clip
_reburn_locks: dict = {}
_reburn_status: dict = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "localhost"


def serve_video(path: str) -> Response:
    """Serve a video file with HTTP Range support for seeking."""
    p = Path(path)
    if not p.exists():
        abort(404)
    file_size = p.stat().st_size
    range_header = request.headers.get("Range")
    mime = "video/mp4"

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not m:
            abort(416)
        start = int(m.group(1))
        end   = int(m.group(2)) if m.group(2) else file_size - 1
        end   = min(end, file_size - 1)
        length = end - start + 1

        def stream():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)

        headers = {
            "Content-Range":  f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges":  "bytes",
            "Content-Length": str(length),
            "Content-Type":   mime,
        }
        return Response(stream(), 206, headers=headers)

    return send_file(path, mimetype=mime, conditional=True)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    pending  = db.get_pending_clips()
    approved = [c for c in db.get_recent_clips(200) if c["status"] == "approved"]
    rejected = [c for c in db.get_recent_clips(200) if c["status"] == "rejected"]
    return render_template_string(DASHBOARD_HTML,
                                  pending=pending,
                                  approved=approved,
                                  rejected=rejected)


@app.route("/editor/<int:clip_id>")
def editor(clip_id):
    clip = db.get_clip(clip_id)
    if not clip:
        abort(404)
    return render_template_string(EDITOR_HTML, clip=clip, clip_id=clip_id)


@app.route("/video/<int:clip_id>/<fmt>")
def video(clip_id, fmt):
    clip = db.get_clip(clip_id)
    if not clip:
        abort(404)
    paths = {
        "landscape":   clip.get("landscape_path") or clip.get("captioned_path") or clip.get("clip_path"),
        "crop":        clip.get("portrait_crop_path"),
        "blackbg":     clip.get("portrait_blackbg_path"),
        "original":    clip.get("clip_path"),
    }
    path = paths.get(fmt)
    if not path:
        abort(404)
    return serve_video(path)


@app.route("/api/clip/<int:clip_id>")
def api_clip(clip_id):
    clip = db.get_clip(clip_id)
    if not clip:
        abort(404)
    words_raw = clip.get("words_json") or "[]"
    try:
        words = json.loads(words_raw)
    except Exception:
        words = []
    return jsonify({**clip, "words": words})


@app.route("/api/clip/<int:clip_id>/words", methods=["POST"])
def save_words(clip_id):
    """Save edited words JSON to DB and trigger a reburn in the background."""
    body = request.get_json()
    if not body or "words" not in body:
        return jsonify({"error": "missing words"}), 400

    words = body["words"]
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE clips SET words_json=?, transcript=? WHERE id=?",
            (json.dumps(words),
             " ".join(w.get("word", "") for w in words),
             clip_id),
        )
    _trigger_reburn(clip_id)
    return jsonify({"ok": True, "reburning": True})


@app.route("/api/clip/<int:clip_id>/approve", methods=["POST"])
def approve(clip_id):
    db.approve_clip(clip_id)
    return jsonify({"ok": True})


@app.route("/api/clip/<int:clip_id>/reject", methods=["POST"])
def reject(clip_id):
    db.reject_clip(clip_id)
    return jsonify({"ok": True})


@app.route("/api/clip/<int:clip_id>/reburn_status")
def reburn_status(clip_id):
    return jsonify(_reburn_status.get(clip_id, {"status": "idle"}))


# ── Background reburn ──────────────────────────────────────────────────────────

def _trigger_reburn(clip_id: int):
    if clip_id in _reburn_locks and _reburn_locks[clip_id].is_alive():
        return  # already running
    t = threading.Thread(target=_do_reburn, args=(clip_id,), daemon=True)
    _reburn_locks[clip_id] = t
    _reburn_status[clip_id] = {"status": "running"}
    t.start()


def _do_reburn(clip_id: int):
    try:
        clip = db.get_clip(clip_id)
        if not clip:
            _reburn_status[clip_id] = {"status": "error", "msg": "Clip not found"}
            return

        script = SCRIPTS_DIR / "burn_captions.py"
        payload = json.dumps({"clips": [clip]})
        r = subprocess.run(
            [sys.executable, str(script), "-"],
            input=payload, capture_output=True, text=True,
        )
        if r.returncode != 0:
            _reburn_status[clip_id] = {"status": "error", "msg": r.stderr[-500:]}
            return

        result = json.loads(r.stdout)
        if result.get("clips"):
            c = result["clips"][0]
            with db.get_connection() as conn:
                conn.execute(
                    """UPDATE clips SET
                        landscape_path=?, portrait_crop_path=?,
                        portrait_blackbg_path=?, captioned_path=?
                       WHERE id=?""",
                    (c.get("landscape_path"), c.get("portrait_crop_path"),
                     c.get("portrait_blackbg_path"), c.get("captioned_path"),
                     clip_id),
                )
        _reburn_status[clip_id] = {"status": "done"}
    except Exception as e:
        _reburn_status[clip_id] = {"status": "error", "msg": str(e)}


# ── HTML templates ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html><html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip Editor</title>
<style>
  :root{--bg:#0f0f0f;--surface:#1a1a1a;--border:#2a2a2a;--text:#f0f0f0;--muted:#888;
        --accent:#53fc18;--yellow:#ffe600;--red:#ff4444;--blue:#3b9eff}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}
  nav{background:var(--surface);border-bottom:1px solid var(--border);padding:16px 24px;
      display:flex;align-items:center;gap:12px}
  nav h1{font-size:18px;font-weight:700;letter-spacing:-0.3px}
  nav span{color:var(--muted);font-size:13px}
  .tabs{display:flex;border-bottom:1px solid var(--border);padding:0 24px}
  .tab{padding:14px 20px;cursor:pointer;font-size:14px;color:var(--muted);border-bottom:2px solid transparent;transition:.15s}
  .tab.active{color:var(--text);border-bottom-color:var(--accent)}
  .panel{display:none;padding:24px}.panel.active{display:block}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;overflow:hidden;
        text-decoration:none;color:var(--text);transition:.15s;display:block}
  .card:hover{border-color:var(--accent);transform:translateY(-2px)}
  .card-thumb{aspect-ratio:16/9;background:#111;display:flex;align-items:center;justify-content:center;
              font-size:40px;color:var(--muted)}
  .card-info{padding:12px}
  .card-info h3{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .card-info p{font-size:11px;color:var(--muted);margin-top:4px}
  .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;margin-top:6px}
  .badge-pending{background:#2a2000;color:var(--yellow)}
  .badge-approved{background:#0a2a0a;color:var(--accent)}
  .badge-rejected{background:#2a0a0a;color:var(--red)}
  .empty{color:var(--muted);font-size:14px;padding:40px 0;text-align:center}
</style>
</head>
<body>
<nav><h1>🎬 Clip Editor</h1><span>{{ pending|length }} pending review</span></nav>
<div class="tabs">
  <div class="tab active" onclick="showTab('pending',this)">Pending ({{ pending|length }})</div>
  <div class="tab" onclick="showTab('approved',this)">Approved ({{ approved|length }})</div>
  <div class="tab" onclick="showTab('rejected',this)">Rejected ({{ rejected|length }})</div>
</div>

{% for tab_name, clips_list in [('pending',pending),('approved',approved),('rejected',rejected)] %}
<div class="panel {% if tab_name=='pending' %}active{% endif %}" id="tab-{{tab_name}}">
  {% if clips_list %}
  <div class="grid">
    {% for c in clips_list %}
    <a class="card" href="/editor/{{ c.id }}">
      <div class="card-thumb">🎥</div>
      <div class="card-info">
        <h3>Clip #{{ c.clip_index }} — {{ c.vod_id[:8] if c.vod_id else 'unknown' }}</h3>
        <p>{{ '%.1f'|format(c.duration or 0) }}s &nbsp;·&nbsp; {{ c.created_at[:10] if c.created_at else '' }}</p>
        <span class="badge badge-{{c.status}}">{{ c.status }}</span>
      </div>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <p class="empty">No {{ tab_name }} clips.</p>
  {% endif %}
</div>
{% endfor %}

<script>
function showTab(name,el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
}
</script>
</body></html>
"""

EDITOR_HTML = """
<!DOCTYPE html><html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Editor — Clip #{{ clip_id }}</title>
<style>
  :root{--bg:#0f0f0f;--surface:#1a1a1a;--border:#2a2a2a;--text:#f0f0f0;--muted:#888;
        --accent:#53fc18;--yellow:#ffe600;--red:#ff4444;--blue:#3b9eff}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       min-height:100vh;display:flex;flex-direction:column}
  nav{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 20px;
      display:flex;align-items:center;gap:12px;flex-shrink:0}
  nav a{color:var(--muted);text-decoration:none;font-size:13px}
  nav a:hover{color:var(--text)}
  nav h2{font-size:16px;font-weight:700;flex:1}
  .status-badge{padding:4px 10px;border-radius:6px;font-size:12px;font-weight:700}
  .main{display:flex;flex:1;overflow:hidden;min-height:0}
  /* ─ Video panel ─ */
  .video-panel{flex:1;display:flex;flex-direction:column;border-right:1px solid var(--border);min-width:0}
  .format-tabs{display:flex;background:var(--surface);border-bottom:1px solid var(--border)}
  .fmt-tab{padding:10px 16px;font-size:13px;cursor:pointer;color:var(--muted);
           border-bottom:2px solid transparent;transition:.15s;user-select:none}
  .fmt-tab.active{color:var(--text);border-bottom-color:var(--accent)}
  .video-wrap{flex:1;display:flex;align-items:center;justify-content:center;
              background:#000;min-height:0;padding:16px}
  video{max-width:100%;max-height:100%;border-radius:8px}
  /* ─ Caption panel ─ */
  .caption-panel{width:340px;flex-shrink:0;display:flex;flex-direction:column;overflow:hidden}
  .caption-panel h3{padding:14px 16px;font-size:13px;font-weight:600;border-bottom:1px solid var(--border);
                    background:var(--surface);flex-shrink:0}
  .words-scroll{flex:1;overflow-y:auto;padding:12px 16px}
  .chunk{margin-bottom:14px}
  .chunk-label{font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
  .chip-row{display:flex;flex-wrap:wrap;gap:6px}
  .chip{display:inline-flex;align-items:center;gap:4px;padding:5px 10px;border-radius:6px;
        background:#222;border:1px solid var(--border);font-size:13px;cursor:text;
        transition:.1s;position:relative}
  .chip:hover{border-color:var(--accent)}
  .chip input{background:none;border:none;outline:none;color:var(--text);font-size:13px;
              font-family:inherit;width:80px;min-width:40px}
  .chip-time{font-size:10px;color:var(--muted)}
  /* ─ Actions ─ */
  .actions{padding:14px 16px;border-top:1px solid var(--border);background:var(--surface);
           display:flex;flex-direction:column;gap:8px;flex-shrink:0}
  .btn{padding:10px 16px;border-radius:8px;border:none;font-size:13px;font-weight:600;
       cursor:pointer;transition:.15s;width:100%;letter-spacing:.2px}
  .btn-reburn{background:#1a3a00;color:var(--accent);border:1px solid #2a5a00}
  .btn-reburn:hover{background:#2a5a00}
  .btn-approve{background:#0a2a0a;color:var(--accent);border:1px solid #1a4a1a}
  .btn-approve:hover{background:#1a4a1a}
  .btn-reject{background:#2a0a0a;color:var(--red);border:1px solid #4a1a1a}
  .btn-reject:hover{background:#4a1a1a}
  .btn:disabled{opacity:.4;cursor:not-allowed}
  .status-bar{font-size:12px;color:var(--muted);text-align:center;padding:4px 0}
</style>
</head>
<body>
<nav>
  <a href="/">← Dashboard</a>
  <h2>Clip #{{ clip_id }} &nbsp;<span style="font-weight:400;color:var(--muted);font-size:13px">{{ clip.vod_id[:8] if clip.vod_id else '' }}</span></h2>
  <span class="status-badge" id="status-badge" style="background:#1a1a00;color:var(--yellow)">{{ clip.status }}</span>
</nav>

<div class="main">
  <!-- Video -->
  <div class="video-panel">
    <div class="format-tabs">
      <div class="fmt-tab active" onclick="setFmt('landscape',this)">Landscape</div>
      <div class="fmt-tab" onclick="setFmt('crop',this)">Portrait Crop</div>
      <div class="fmt-tab" onclick="setFmt('blackbg',this)">Portrait BG</div>
      <div class="fmt-tab" onclick="setFmt('original',this)" style="margin-left:auto">Original</div>
    </div>
    <div class="video-wrap">
      <video id="player" controls playsinline>
        <source id="video-src" src="/video/{{ clip_id }}/landscape" type="video/mp4">
      </video>
    </div>
  </div>

  <!-- Captions -->
  <div class="caption-panel">
    <h3>📝 Caption Words</h3>
    <div class="words-scroll" id="words-container">
      <p style="color:var(--muted);font-size:13px;padding:20px 0">Loading words...</p>
    </div>
    <div class="actions">
      <button class="btn btn-reburn" id="btn-reburn" onclick="reburn()">🔥 Re-burn with edits</button>
      <button class="btn btn-approve" id="btn-approve" onclick="approve()">✓ Approve</button>
      <button class="btn btn-reject"  id="btn-reject"  onclick="reject()">✗ Reject</button>
      <div class="status-bar" id="status-bar"></div>
    </div>
  </div>
</div>

<script>
const CLIP_ID = {{ clip_id }};
let words = [];
let pollInterval = null;

// ── Load clip data ─────────────────────────────────────────────────────────────
fetch(`/api/clip/${CLIP_ID}`)
  .then(r => r.json())
  .then(data => {
    words = data.words || [];
    renderWords(words);
    updateStatusBadge(data.status);
  });

// ── Word rendering ─────────────────────────────────────────────────────────────
function renderWords(wordList) {
  const container = document.getElementById('words-container');
  if (!wordList.length) {
    container.innerHTML = '<p style="color:var(--muted);font-size:13px;padding:20px 0">No transcription found. You can re-run captions after adding words.</p>';
    return;
  }

  // Group into chunks of ≤3 words with gap > 0.4s or max 2.5s
  const chunks = [];
  let cur = [];
  for (const w of wordList) {
    if (cur.length) {
      const gap = w.start - cur[cur.length-1].end;
      const dur = w.end - cur[0].start;
      if (gap > 0.4 || cur.length >= 3 || dur > 2.5) {
        chunks.push(cur);
        cur = [];
      }
    }
    cur.push(w);
  }
  if (cur.length) chunks.push(cur);

  const html = chunks.map((chunk, ci) => {
    const chips = chunk.map((w, wi) => {
      const globalIdx = wordList.indexOf(w);
      return `<div class="chip">
        <input type="text" value="${escHtml(w.word)}"
               data-idx="${globalIdx}"
               onchange="updateWord(${globalIdx}, this.value)"
               oninput="autosize(this)"
               style="width:${Math.max(40, w.word.length * 8)}px">
        <span class="chip-time">${w.start.toFixed(1)}s</span>
      </div>`;
    }).join('');
    const t0 = chunk[0].start.toFixed(1);
    const t1 = chunk[chunk.length-1].end.toFixed(1);
    return `<div class="chunk">
      <div class="chunk-label">Chunk ${ci+1} &nbsp; ${t0}s – ${t1}s</div>
      <div class="chip-row">${chips}</div>
    </div>`;
  }).join('');
  container.innerHTML = html;
}

function updateWord(idx, val) {
  words[idx].word = val;
}

function autosize(input) {
  input.style.width = Math.max(40, input.value.length * 8) + 'px';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Video format switching ─────────────────────────────────────────────────────
function setFmt(fmt, el) {
  document.querySelectorAll('.fmt-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  const player = document.getElementById('player');
  const src    = document.getElementById('video-src');
  const t      = player.currentTime;
  src.src = `/video/${CLIP_ID}/${fmt}`;
  player.load();
  player.play().catch(()=>{});
}

// ── Actions ────────────────────────────────────────────────────────────────────
function reburn() {
  setStatus('Saving words & re-burning...');
  disableButtons(true);
  fetch(`/api/clip/${CLIP_ID}/words`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({words})
  }).then(r => r.json()).then(() => {
    setStatus('Burning captions...');
    pollReburn();
  }).catch(e => setStatus('Error: ' + e));
}

function pollReburn() {
  pollInterval = setInterval(() => {
    fetch(`/api/clip/${CLIP_ID}/reburn_status`)
      .then(r => r.json())
      .then(data => {
        if (data.status === 'done') {
          clearInterval(pollInterval);
          setStatus('Done! Reload to see new captions.');
          disableButtons(false);
          // Reload video
          const player = document.getElementById('player');
          const src = document.getElementById('video-src');
          src.src = src.src + '?t=' + Date.now();
          player.load();
        } else if (data.status === 'error') {
          clearInterval(pollInterval);
          setStatus('Error: ' + (data.msg || 'unknown'));
          disableButtons(false);
        }
      });
  }, 2000);
}

function approve() {
  if (!confirm('Approve this clip for posting?')) return;
  fetch(`/api/clip/${CLIP_ID}/approve`, {method:'POST'})
    .then(() => { updateStatusBadge('approved'); setStatus('Approved ✓'); });
}

function reject() {
  if (!confirm('Reject this clip?')) return;
  fetch(`/api/clip/${CLIP_ID}/reject`, {method:'POST'})
    .then(() => { updateStatusBadge('rejected'); setStatus('Rejected'); });
}

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

function disableButtons(disabled) {
  ['btn-reburn','btn-approve','btn-reject'].forEach(id => {
    document.getElementById(id).disabled = disabled;
  });
}

function updateStatusBadge(status) {
  const badge = document.getElementById('status-badge');
  badge.textContent = status;
  const colors = {pending:'#1a1a00;color:#ffe600', approved:'#0a1a0a;color:#53fc18', rejected:'#1a0a0a;color:#ff4444'};
  const c = colors[status] || colors.pending;
  badge.style.cssText = `background:#${c}`;
}
</script>
</body></html>
"""


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    ip = local_ip()
    log.info("Caption editor starting on http://localhost:%d", PORT)
    log.info("Access from phone:   http://%s:%d", ip, PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
