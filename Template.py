# ===============================
# File: Template.py
# Purpose: HTML/CSS/JS templates for eLetter gallery (DandR-ready)
# Notes:
#   • Slider markup is created in JS to prevent raw attribute text flashes.
#   • Volume is frozen at build time (INITIAL_VOLUME injected as 0–100).
#   • No storage, no a11y overhead, no true mute (floor > 1%).
#   • Wall behavior: auto-show on first arrival only; never auto-close; “Show message again” shows only after user closes.
#   • Audio polish: music is started muted inside the tap gesture, then unmuted/faded 250ms after glissando ends.
#   • Audio element is hard-wired in HTML (no {{AUDIO_HTML}} injection needed).
# ===============================

TEMPLATE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{TITLE}}</title>
  <link rel="stylesheet" href="styles.css">

  <!-- Preload key images -->
  <link rel="preload" as="image" href="gallery/cover.png">
  <link rel="preload" as="image" href="gallery/letter.png">
  <link rel="preload" as="image" href="gallery/wall.png">
  <link rel="preload" as="image" href="gallery/back.png">
  <link rel="preload" as="image" href="gallery/icons/ppage.png">
  <link rel="preload" as="image" href="gallery/icons/npage.png">
  <link rel="preload" as="image" href="gallery/icons/cleft.png">
  <link rel="preload" as="image" href="gallery/icons/cright.png">
  <link rel="preload" as="image" href="gallery/icons/volon.png">
  <link rel="preload" as="image" href="gallery/icons/voloff.png">
  <link rel="preload" as="image" href="gallery/icons/showmessageicon.png">

  <!-- Preload sounds -->
  <link rel="preload" as="audio" href="gallery/sounds/glissando.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip1.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip2.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip3.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip4.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip5.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip6.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip7.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip8.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip9.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/flip10.mp3">
</head>

<body>
  <!-- Curtain intro -->
  <div id="curtain-overlay">
    <img id="curtain-left"  src="gallery/icons/cleft.png"  alt="">
    <img id="curtain-right" src="gallery/icons/cright.png" alt="">
    <button id="begin-button" type="button">Tap to Begin</button>
  </div>

  <!-- Slides container -->
  <div id="slideshow">
    <section class="slide active" id="slide-0">
      <img src="gallery/cover.png" alt="Cover Page">
    </section>
    <section class="slide" id="slide-1">
      <img src="gallery/letter.png" alt="Main Letter">
    </section>
    <section class="slide" id="slide-2">
      <img src="gallery/wall.png" alt="Text Wall Background">
      <button id="close-text" title="Close Text">&times;</button>
      <div class="text-wall" style="display:none;">
        <div class="text-wall-content">{{MESSAGE_HTML}}</div>
      </div>
    </section>
    <section class="slide" id="slide-3">
      <img src="gallery/back.png" alt="Final Backdrop">
    </section>

    <!-- Navigation -->
    <button id="prev" type="button" title="Previous page">
      <img src="gallery/icons/ppage.png" alt="">
    </button>
    <button id="next" type="button" title="Next page">
      <img src="gallery/icons/npage.png" alt="">
    </button>
    <div id="progress">Page 1 of 4</div>

    <!-- Show message (reopen) icon -->
    <img
      id="open-text"
      src="gallery/icons/showmessageicon.png"
      alt="Show Message"
      style="display:none;"
    />
  </div>

  <!-- Volume control (slider injected by JS) -->
  <div id="volume-control">
    <img id="volume-icon" src="gallery/icons/volon.png" alt="Volume">
    <!-- slider injected at runtime -->
  </div>

  <!-- Background music (hard-wired; no {{AUDIO_HTML}} needed) -->
  <audio
    id="bg-music"
    src="gallery/sounds/music.mp3"
    preload="auto"
    loop
    playsinline
  ></audio>

  <!-- Injected constant (0–100 int, at build time) -->
  <script>
    const INITIAL_VOLUME = {{INITIAL_VOLUME}};
  </script>

  <!-- App script -->
  <script src="script.js"></script>
</body>
</html>
"""

TEMPLATE_CSS = """
/* Global */
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 100%; height: 100%; background: #111; overflow: hidden; font-family: Inter, Segoe UI, Roboto, Arial, sans-serif; }

/* Slideshow container */
#slideshow { position: relative; width: 100%; height: 100%; perspective: 2000px; }

/* Curtain overlay */
#curtain-overlay { position: absolute; inset: 0; z-index: 9999; cursor: pointer; overflow: hidden; pointer-events: all; }
#curtain-left, #curtain-right { position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; z-index: 99999; pointer-events: none; }
#begin-button {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  font-size: 42px; color: #fff; background: rgba(0,0,0,0.6);
  padding: 16px 32px; border: 2px solid #00ffff; border-radius: 10px; cursor: pointer;
  animation: pulse 2s infinite; z-index: 100001;
}
#begin-button:hover { background: rgba(0,0,0,0.8); border-color: #00ffff; }
@keyframes pulse { 0%,100% { opacity: 0.6; transform: translate(-50%, -50%) scale(1); } 50% { opacity: 1; transform: translate(-50%, -50%) scale(1.05); } }

/* Slides */
.slide { display: none; position: absolute; inset: 0; transform-origin: left center; backface-visibility: hidden; transform-style: preserve-3d; }
.slide.active { display: block; }
.slide img {
  max-width: 100%; max-height: 100%; position: absolute; inset: 0; margin: auto; object-fit: contain;
  box-shadow: inset 0 0 40px rgba(0,0,0,.15), 0 0 30px rgba(0,0,0,.25);
}

/* Text wall */
.text-wall {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 80%; max-height: 80%; overflow-y: auto; background: rgba(0,0,0,0.5);
  padding: 1rem; color: #fff; line-height: 1.55; border-radius: 10px; z-index: 105;
}
#close-text {
  position: absolute; top: 40px; right: 40px;
  background: rgba(255,255,255,0.2); border: none; color: #fff; font-size: 24px;
  width: 32px; height: 32px; border-radius: 16px; cursor: pointer; display: none; z-index: 106;
}
#close-text:hover { background: rgba(255,255,255,0.4); }

/* Nav arrows */
#prev,#next { position: absolute; background: none; border: none; padding: 0; cursor: pointer; z-index: 100; }
#prev { left: 50px; top: 50%; transform: translateY(-50%); }
#next { right: 50px; top: 50%; transform: translateY(-50%); }
#prev img,#next img { width: 120px; height: 120px; }

/* Progress */
#progress {
  position: absolute; bottom: 20px; right: 20px;
  background: rgba(0,0,0,0.5); color: #fff; padding: .3em .6em; border-radius: 6px;
  font-weight: 600; letter-spacing: .2px;
}

/* Show-message icon (reopen only) */
#open-text { position: absolute; bottom: 20px; left: 20px; width: 64px; height: 64px; cursor: pointer; z-index: 100; transition: transform .15s; display: none; }
#open-text:hover { transform: scale(1.08); }

/* Volume control */
#volume-control { position: absolute; top: calc(50% + 20px); right: 50px; display: flex; align-items: center; gap: 10px; z-index: 99; }
#volume-icon { width: 48px; height: 48px; cursor: pointer; }
/* Slider (will be injected) */
#volume-slider {
  width: 140px; -webkit-appearance: none; appearance: none; height: 4px; background: rgba(255,255,255,0.28);
  border-radius: 2px; outline: none; display: none;
}
#volume-slider::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none; width: 16px; height: 16px; background: #00ffff; border: 2px solid #fff; border-radius: 50%; cursor: pointer;
}
#volume-slider::-moz-range-thumb {
  width: 16px; height: 16px; background: #00ffff; border: 2px solid #fff; border-radius: 50%; cursor: pointer;
}

/* Flip anims */
@keyframes flipOut { 0% { transform: perspective(2000px) rotateY(0deg); opacity: 1; } 100% { transform: perspective(2000px) rotateY(90deg); opacity: 0; } }
@keyframes flipIn  { 0% { transform: perspective(2000px) rotateY(-90deg); opacity: 0; } 100% { transform: perspective(2000px) rotateY(0); opacity: 1; } }
.slide.flip-out { animation: flipOut .6s ease-in forwards; }
.slide.flip-in  { animation: flipIn  .6s ease-in forwards; }

/* Curtain slide anims */
@keyframes slideLeft  { from { transform: translateX(0); } to { transform: translateX(-100vw); } }
@keyframes slideRight { from { transform: translateX(0); } to { transform: translateX(100vw); } }
"""

TEMPLATE_JS = """
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
"""