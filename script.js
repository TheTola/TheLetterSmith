
// ===============================
// script.js — eLetter frontend
// - Slider is created in JS (prevents raw attribute text showing).
// - Volume frozen at build time (no storage). No true mute (min 1%).
// - No auto-close of the Wall message. Auto-open only on first arrival.
// - 'Show message again' icon appears only after user closes.
// - Audio polish: start music muted in same gesture as tap, then unmute+fade 250ms after glissando ends.
// ===============================
console.log("🎚️ script.js loaded");

document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const slides         = document.querySelectorAll('.slide');
  const prevBtn        = document.getElementById('prev');
  const nextBtn        = document.getElementById('next');
  const progress       = document.getElementById('progress');
  const closeTextBtn   = document.getElementById('close-text');
  const openTextBtn    = document.getElementById('open-text');
  const textWall       = document.querySelector('.text-wall');
  const curtainOverlay = document.getElementById('curtain-overlay');
  const curtainLeft    = document.getElementById('curtain-left');
  const curtainRight   = document.getElementById('curtain-right');
  const beginButton    = document.getElementById('begin-button');

  // Audio element (hard-wired in HTML)
  const music = /** @type {HTMLAudioElement} */ (document.getElementById('bg-music'));
  if (!music) console.warn('bg-music element missing');
  if (music) {
    if (!music.src) music.src = 'gallery/sounds/music.mp3';
    music.loop = true;
  }

  let current = 0;
  const total = slides.length;

  // Wall message state
  let wallUserClosed = false;  // set true only when user closes
  let wallEverOpened = false;

  // Volume config — frozen at build time (0–100 or 0–1 accepted)
  const START_VOL_PCT = (typeof INITIAL_VOLUME === 'number')
    ? (INITIAL_VOLUME <= 1 ? Math.round(INITIAL_VOLUME * 100) : Math.max(1, Math.min(100, INITIAL_VOLUME)))
    : 10;

  const START_VOL     = START_VOL_PCT / 100;
  const clamp01       = v => Math.min(1, Math.max(0.01, v)); // no true mute

  // Create slider at runtime (prevents raw attribute text flash)
  const vc = document.getElementById('volume-control');
  const slider = document.createElement('input');
  slider.type  = 'range';
  slider.id    = 'volume-slider';
  slider.min   = '1';
  slider.max   = '100';
  slider.value = String(START_VOL_PCT);
  slider.style.display = 'none';
  vc.appendChild(slider);

  const icon = document.getElementById('volume-icon');

  function updateProgress() {
    progress.textContent = `Page ${current + 1} of ${total}`;
  }

  // ----- Wall show/hide helpers (no auto-close) -----
  function showMessage() {
    textWall.style.display = 'block';
    closeTextBtn.style.display = 'block';
    if (current === 2) openTextBtn.style.display = 'none';
    wallEverOpened = true;
  }

  function hideMessage() {
    textWall.style.display = 'none';
    closeTextBtn.style.display = 'none';
    if (current === 2) openTextBtn.style.display = 'block';
    wallUserClosed = true;
  }

  function toggleMessage() {
    if (textWall.style.display === 'none' || !textWall.style.display) {
      showMessage();
    } else {
      hideMessage();
    }
  }

  // ----- Slide change with NO auto-closing -----
  function showSlide(idx) {
    const nextIdx = ((idx % total) + total) % total;
    if (nextIdx === current) {
      slides[nextIdx].classList.add('active');
      updateProgress();

      if (nextIdx === 2) {
        if (!wallUserClosed && textWall.style.display !== 'block') {
          showMessage(); // first arrival, auto-open once
        } else {
          openTextBtn.style.display  = (textWall.style.display === 'block') ? 'none'  : 'block';
          closeTextBtn.style.display = (textWall.style.display === 'block') ? 'block' : 'none';
        }
      } else {
        openTextBtn.style.display = 'none';
      }
      return;
    }

    const oldSlide = slides[current];
    const newSlide = slides[nextIdx];
    oldSlide.classList.add('flip-out');
    oldSlide.addEventListener('animationend', function onOut() {
      oldSlide.removeEventListener('animationend', onOut);
      oldSlide.classList.remove('active', 'flip-out');
      newSlide.classList.add('active', 'flip-in');
      newSlide.addEventListener('animationend', function onIn() {
        newSlide.removeEventListener('animationend', onIn);
        newSlide.classList.remove('flip-in');
        current = nextIdx;
        updateProgress();

        if (current === 2) {
          if (!wallUserClosed && textWall.style.display !== 'block') {
            showMessage();
          } else {
            openTextBtn.style.display  = (textWall.style.display === 'block') ? 'none'  : 'block';
            closeTextBtn.style.display = (textWall.style.display === 'block') ? 'block' : 'none';
          }
        } else {
          openTextBtn.style.display = 'none';
        }
      });
    });
  }

  // Flip SFX (preload once)
  const flipSounds = Array.from({ length: 10 }, (_, i) => new Audio(`gallery/sounds/flip${i + 1}.mp3`));
  flipSounds.forEach(a => { try { a.load(); } catch {} });
  function playFlipSound() {
    const snd = flipSounds[Math.floor(Math.random() * flipSounds.length)];
    snd.currentTime = 0;
    snd.play().catch(err => console.warn("Flip sound blocked:", err));
  }

  // Fade with cancellation
  let fadeTimer = null;
  function fadeToVolume(target01, durationMs = 300) {
    if (!music) return;
    target01 = clamp01(target01);
    if (fadeTimer) { clearInterval(fadeTimer); fadeTimer = null; }
    const steps = 20;
    const delta = (target01 - music.volume) / steps;
    let i = 0;
    fadeTimer = setInterval(() => {
      music.volume = clamp01(music.volume + delta);
      if (++i >= steps) {
        clearInterval(fadeTimer);
        fadeTimer = null;
        music.volume = target01;
      }
    }, Math.max(10, Math.floor(durationMs / steps)));
  }

  // Volume handling (no storage)
  window.handleVolumeChange = function(val) {
    if (!music) return;
    if (fadeTimer) { clearInterval(fadeTimer); fadeTimer = null; }
    const pct = Math.max(1, Math.min(100, parseInt(val, 10) || START_VOL_PCT));
    const vol = clamp01(pct / 100);
    music.volume = vol;
    music.muted = false;
    icon.src = 'gallery/icons/volon.png';
  };

  // Curtain open sequence — start music in gesture (muted), then unmute after gliss + 250ms
  async function openCurtain() {
    // Animate curtains
    curtainLeft.style.animation  = 'slideLeft 2s forwards';
    curtainRight.style.animation = 'slideRight 2s forwards';
    curtainRight.addEventListener('animationend', () => {
      curtainOverlay.style.display = 'none';
    }, { once: true });

    // Prepare sounds INSIDE the same user gesture
    const gliss = new Audio('gallery/sounds/glissando.mp3');
    gliss.volume = 0.3;
    try { gliss.load(); } catch {}

    if (music) {
      try { music.load(); } catch {}
      music.volume = 0;
      music.muted  = true; // gesture-safe
      try { await music.play(); } catch (e) {
        console.warn('Autoplay blocked (music):', e);
      }
    }

    try { await gliss.play(); } catch (e) {
      console.warn('Autoplay blocked (gliss):', e);
    }

    // When gliss ends, unmute and fade in the music
    gliss.addEventListener('ended', () => {
      if (!music) return;
      music.muted = false;
      fadeToVolume(START_VOL, 1200);
    }, { once: true });

    // If metadata is known, schedule timed unmute (safety, never early)
    gliss.addEventListener('loadedmetadata', () => {
      if (!Number.isFinite(gliss.duration) || gliss.duration <= 0) return;
      const ms = Math.ceil(gliss.duration * 1000) + 250;
      setTimeout(() => {
        if (!music) return;
        // If ended didn't fire (edge case), ensure unmute anyway
        if (music.muted) {
          music.muted = false;
          fadeToVolume(START_VOL, 1200);
        }
      }, ms);
    }, { once: true });
  }

  // Wire UI
  prevBtn .addEventListener('click', () => { playFlipSound(); showSlide(current - 1); });
  nextBtn .addEventListener('click', () => { playFlipSound(); showSlide(current + 1); });

  openTextBtn.addEventListener('click', showMessage);
  closeTextBtn.addEventListener('click', hideMessage);

  icon.addEventListener('click', () => {
    slider.style.display = (slider.style.display === 'block') ? 'none' : 'block';
  });
  slider.addEventListener('input', e => window.handleVolumeChange(e.target.value));

  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft')  { playFlipSound(); showSlide(current - 1); }
    if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'Enter') {
      playFlipSound(); showSlide(current + 1);
    }
    if (current === 2) {
      if (e.key.toLowerCase() === 't') { toggleMessage(); }
      if (e.key === 'Escape') { if (textWall.style.display === 'block') hideMessage(); }
    }
  });

  // Gesture wiring (guard against double-trigger)
  let curtainOpened = false;
  const gesture = () => {
    if (curtainOpened) return;
    curtainOpened = true;
    openCurtain();
  };
  if (beginButton) {
    beginButton.addEventListener('click',       gesture);
    beginButton.addEventListener('pointerdown', gesture);
    beginButton.addEventListener('touchstart',  gesture);
  }
  if (curtainOverlay) {
    curtainOverlay.addEventListener('click',       gesture);
    curtainOverlay.addEventListener('pointerdown', gesture);
    curtainOverlay.addEventListener('touchstart',  gesture);
  }

  // Initial state (baked volume, no storage)
  slider.value   = String(START_VOL_PCT);
  if (music) {
    music.volume = START_VOL;   // will fade from 0 → START_VOL on open
    music.muted  = true;        // unmuted by openCurtain after gliss
  }
  icon.src       = 'gallery/icons/volon.png';

  // Initial UI sanity
  openTextBtn.style.display  = 'none';
  closeTextBtn.style.display = 'none';

  updateProgress();
  // No auto-open at load; first-time auto-open executes when we FIRST reach Wall in showSlide()
});
