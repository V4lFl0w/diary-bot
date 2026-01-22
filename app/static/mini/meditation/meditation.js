(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  const $mins = document.getElementById('mins');
  const $timer = document.getElementById('timer');
  const $status = document.getElementById('status');
  const $start = document.getElementById('startBtn');
  const $stop  = document.getElementById('stopBtn');
  const $test  = document.getElementById('testBtn');

  let interval = null;
  let endAt = 0;

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec));
    const m = String(Math.floor(sec / 60)).padStart(2, '0');
    const s = String(sec % 60).padStart(2, '0');
    return `${m}:${s}`;
  }

  function vibrate(ms) {
    try { navigator.vibrate?.(ms); } catch (e) {}
  }

  // “Колокол” без mp3: две ноты + затухание
  async function bell(pattern = 'start') {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;

    const ctx = new AudioCtx();

    // некоторые устройства требуют resume() после user gesture
    try { await ctx.resume(); } catch (e) {}

    const now = ctx.currentTime;

    function tone(freq, t0, dur, gain0) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, t0);
      gain.gain.setValueAtTime(gain0, t0);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

      osc.connect(gain).connect(ctx.destination);
      osc.start(t0);
      osc.stop(t0 + dur);
    }

    if (pattern === 'start') {
      tone(880, now + 0.00, 0.35, 0.25);
      tone(1320, now + 0.05, 0.35, 0.18);
      vibrate(40);
    } else {
      tone(784, now + 0.00, 0.60, 0.28);
      tone(1174, now + 0.06, 0.60, 0.20);
      tone(1568, now + 0.12, 0.60, 0.14);
      vibrate([60, 40, 60]);
    }

    // закрываем контекст через секунду
    setTimeout(() => { try { ctx.close(); } catch(e){} }, 1200);
  }

  function setRunning(running) {
    $start.disabled = running;
    $stop.disabled = !running;
    $mins.disabled = running;
  }

  function tick() {
    const left = (endAt - Date.now()) / 1000;
    $timer.textContent = fmt(left);
    if (left <= 0) finish();
  }

  function finish() {
    if (interval) clearInterval(interval);
    interval = null;
    setRunning(false);

    $timer.textContent = "00:00";
    $status.innerHTML = '<span class="ok">✅ Сессия завершена.</span>';

    bell('end');

    // отправим боту событие (не обязательно, но полезно)
    try {
      tg?.sendData?.(JSON.stringify({ type: "meditation_done", at: Date.now() }));
    } catch (e) {}
  }

  $start.addEventListener('click', async () => {
    const mins = Math.max(1, Math.min(180, parseInt($mins.value || '1', 10)));
    const total = mins * 60;

    endAt = Date.now() + total * 1000;
    $timer.textContent = fmt(total);
    $status.textContent = "⏳ Идёт сессия…";
    setRunning(true);

    // стартовый звук
    await bell('start');

    interval = setInterval(tick, 250);
    tick();
  });

  $stop.addEventListener('click', () => {
    if (interval) clearInterval(interval);
    interval = null;
    setRunning(false);
    $status.textContent = "Остановлено.";
    $timer.textContent = fmt(Math.max(0, (endAt - Date.now())/1000));
    try { tg?.sendData?.(JSON.stringify({ type: "meditation_stop", at: Date.now() })); } catch(e) {}
  });

  $test.addEventListener('click', async () => {
    $status.textContent = "Тест звука…";
    await bell('start');
    setTimeout(() => bell('end'), 600);
    setTimeout(() => { $status.textContent = "Готов."; }, 1200);
  });

  // initial
  $timer.textContent = fmt(parseInt($mins.value || '1', 10) * 60);
})();
