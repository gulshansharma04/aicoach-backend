// recorder.js
// Drop this file in /www and include it from batting.html (or index.html).
// Relies on window.AI_COACH_CONFIG.apiUrl(path) from your existing config.js

(function () {
  const DEFAULT_RECORD_MS = 3000;

  async function getAudioStream() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("getUserMedia not supported in this browser");
    }
    return await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  }

  function createRecorder(stream, mimeTypePriority = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg']) {
    if (typeof MediaRecorder === 'undefined') throw new Error('MediaRecorder not supported');
    // pick a mimeType supported by browser
    let mimeType = '';
    for (const m of mimeTypePriority) {
      if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) {
        mimeType = m;
        break;
      }
    }
    const options = mimeType ? { mimeType } : {};
    return new MediaRecorder(stream, options);
  }

  async function recordFixedDuration(durationMs = DEFAULT_RECORD_MS) {
    const stream = await getAudioStream();
    const recorder = createRecorder(stream);
    const chunks = [];

    return await new Promise((resolve, reject) => {
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size) chunks.push(e.data);
      };
      recorder.onerror = (ev) => {
        stream.getTracks().forEach(t => t.stop());
        reject(ev.error || new Error('record error'));
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' });
        try {
          const res = await sendBlobToServer(blob);
          resolve(res);
        } catch (err) {
          reject(err);
        }
      };

      try {
        recorder.start();
      } catch (e) {
        stream.getTracks().forEach(t => t.stop());
        return reject(e);
      }
      setTimeout(() => {
        try { recorder.stop(); } catch (e) { /* ignore */ }
      }, durationMs);
    });
  }

  async function sendBlobToServer(blob) {
    const fd = new FormData();
    fd.append('audio', blob, 'recording.webm');
    const url = window.AI_COACH_CONFIG.apiUrl('/stt_upload');

    const resp = await fetch(url, {
      method: 'POST',
      body: fd
    });

    if (!resp.ok) {
      const t = await resp.text().catch(() => '');
      throw new Error(`Upload failed ${resp.status} ${t}`);
    }
    return await resp.json();
  }

  // Helper UI wiring function (will create a simple record button if asked)
  function createRecorderButton({ targetSelector = '#record-btn', durationMs = DEFAULT_RECORD_MS } = {}) {
    const el = document.querySelector(targetSelector);
    if (!el) return;
    el.addEventListener('click', async (ev) => {
      el.disabled = true;
      el.innerText = 'Recording...';
      try {
        const result = await recordFixedDuration(durationMs);
        // expected { text: "..." } 
        console.log('Transcription result', result);
        // dispatch custom event so your app can handle the transcribed text
        window.dispatchEvent(new CustomEvent('aiCoachTranscribed', { detail: result }));
        el.innerText = 'Recorded ✓';
        setTimeout(() => { el.innerText = 'Record'; el.disabled = false; }, 1200);
      } catch (err) {
        console.error('record error', err);
        el.innerText = 'Error';
        setTimeout(() => { el.innerText = 'Record'; el.disabled = false; }, 1500);
      }
    });
  }

  // expose
  window.AIRecorder = {
    recordFixedDuration,
    sendBlobToServer,
    createRecorderButton
  };
})();
