# ===============================
# File: Template.py
# Purpose: HTML/CSS/JS templates for the eLetter viewer
#
# BOOK MODEL (YOUR RULE):
# - Like a Bible sitting closed
# - Spine ALWAYS on the LEFT
# - No center seam / no open spread
#
# FIX APPLIED:
# - The “turning sheet” NEVER becomes the next page image.
# - NEXT: the turning sheet is ALWAYS the CURRENT page rotating away; the NEXT page is revealed underneath.
# - PREV: the turning sheet is the PREVIOUS page rotating back in over the CURRENT page.
#
# CHANGE REQUEST (THIS TURN):
# - Do NOT remove anything.
# - Add curtain overlay fade-out so it "makes sense" visually.
# - Also prevent pitch-black start by using stage gradient behind curtains.
# ===============================

TEMPLATE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{TITLE}}</title>
  <link rel="stylesheet" href="styles.css">

  <!-- Preload key images -->
  <link rel="preload" as="image" href="gallery/pages/cover.png">
  <link rel="preload" as="image" href="gallery/pages/letter.png">
  <link rel="preload" as="image" href="gallery/pages/wall.png">
  <link rel="preload" as="image" href="gallery/pages/back.png">

  <link rel="preload" as="image" href="gallery/controls/ppage.png">
  <link rel="preload" as="image" href="gallery/controls/npage.png">
  <link rel="preload" as="image" href="gallery/controls/cleft.png">
  <link rel="preload" as="image" href="gallery/controls/cright.png">
  <link rel="preload" as="image" href="gallery/controls/volon.png">
  <link rel="preload" as="image" href="gallery/controls/voloff.png">
  <link rel="preload" as="image" href="gallery/controls/showmessageicon.png">

  <!-- Preload sounds -->
  <link rel="preload" as="audio" href="gallery/sounds/glissando.mp3">
  <link rel="preload" as="audio" href="gallery/sounds/music.mp3">
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
  <div id="curtain-overlay" aria-hidden="false">
    <img id="curtain-left"  src="gallery/controls/cleft.png"  alt="">
    <img id="curtain-right" src="gallery/controls/cright.png" alt="">
    <button id="begin-button" type="button">Tap to Begin</button>
  </div>

  <!-- Main stage -->
  <div id="slideshow">
    <!-- Page-turn overlay (JS sizes to the ACTIVE page-image rect) -->
    <div id="turn" aria-hidden="true">
      <div class="sheet sheet-front visible" id="sheetFront">
        <img id="turnFrontImg" alt="">
      </div>
      <!-- kept for compatibility; never shown after this fix -->
      <div class="sheet sheet-back hidden" id="sheetBack">
        <img id="turnBackImg" alt="">
      </div>
    </div>
    <div id="turnShadow" aria-hidden="true"></div>

    <!-- Slides -->
    <section class="slide active" data-index="0" id="slide-0">
      <img src="gallery/pages/cover.png" alt="Cover Page">
    </section>

    <section class="slide" data-index="1" id="slide-1">
      <img src="gallery/pages/letter.png" alt="Main Letter">
    </section>

    <section class="slide" data-index="2" id="slide-2">
      <img src="gallery/pages/wall.png" alt="Text Wall Background">
      <button id="close-text" type="button" title="Close Text" aria-label="Close message">&times;</button>
      <div class="text-wall" id="textWall" style="display:none;">
        <div class="text-wall-content" id="textWallContent">{{MESSAGE_HTML}}</div>
      </div>
    </section>

    <section class="slide" data-index="3" id="slide-3">
      <img src="gallery/pages/back.png" alt="Final Backdrop">
    </section>

    <!-- Navigation -->
    <button id="prev" type="button" title="Previous page" aria-label="Previous page">
      <img src="gallery/controls/ppage.png" alt="">
    </button>
    <button id="next" type="button" title="Next page" aria-label="Next page">
      <img src="gallery/controls/npage.png" alt="">
    </button>

    <div id="progress" aria-live="polite">Page 1 of 4</div>

    <!-- Show message (reopen) icon -->
    <img
      id="open-text"
      src="gallery/controls/showmessageicon.png"
      alt="Show Message"
      title="Show Message"
      style="display:none;"
    />
  </div>

  <!-- Volume control (slider injected by JS) -->
  <div id="volume-control">
    <img id="volume-icon" src="gallery/controls/volon.png" alt="Volume" title="Volume">
  </div>

  <!-- Background music -->
  <audio id="bg-music" src="gallery/sounds/music.mp3" preload="auto" loop playsinline></audio>

  <!-- Injected constant (0–100 int, at build time) -->
  <script>
    const INITIAL_VOLUME = {{INITIAL_VOLUME}};
  </script>

  <script src="script.js"></script>
</body>
</html>
"""

TEMPLATE_CSS = r"""
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;background:#0b0c12;overflow:hidden;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif}

/* Stage */
#slideshow{
  position:relative;width:100%;height:100%;
  perspective: 6000px;
  perspective-origin: 50% 50%;
  background:
    radial-gradient(900px 600px at 30% 25%, rgba(255,255,255,.08), transparent 60%),
    radial-gradient(900px 600px at 80% 70%, rgba(0,0,0,.35), transparent 60%),
    linear-gradient(180deg, #0b0c12, #05060a);
  transform-style:preserve-3d;
  -webkit-transform-style:preserve-3d;
}

/* Curtain overlay */
#curtain-overlay{
  position:absolute; inset:0; z-index:9999;
  overflow:hidden; pointer-events:all;

  /* CHANGED: was background:#000; (pitch black)
     Now matches stage so it "makes sense" visually behind curtains. */
  background:
    radial-gradient(900px 600px at 30% 25%, rgba(255,255,255,.08), transparent 60%),
    radial-gradient(900px 600px at 80% 70%, rgba(0,0,0,.35), transparent 60%),
    linear-gradient(180deg, #0b0c12, #05060a);

  /* Added: makes fade smooth / reduces repaint jitter */
  will-change: opacity;
}

#curtain-left,#curtain-right{
  position:absolute; top:0; left:0;
  width:100%; height:100%;
  object-fit:cover;
  z-index:10000;
  pointer-events:none;
}
#begin-button{
  position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
  font-size:42px; color:#fff;
  background:rgba(0,0,0,0.60);
  padding:16px 32px;
  border:2px solid #00ffff;
  border-radius:12px;
  cursor:pointer;
  z-index:10001;
  animation:pulse 2s infinite;
}
#begin-button:hover{background:rgba(0,0,0,0.78)}
@keyframes pulse{
  0%,100%{opacity:.65; transform:translate(-50%,-50%) scale(1)}
  50%{opacity:1; transform:translate(-50%,-50%) scale(1.05)}
}
@keyframes curtainLeftOut{from{transform:translateX(0)}to{transform:translateX(-100vw)}}
@keyframes curtainRightOut{from{transform:translateX(0)}to{transform:translateX(100vw)}}

/* ADDED: overlay fade that runs alongside curtain slide */
@keyframes curtainOverlayFadeOut{from{opacity:1}to{opacity:0}}

/* Slides */
.slide{
  position:absolute; inset:0;
  opacity:0;
  pointer-events:none;
  transform-style:preserve-3d;
  -webkit-transform-style:preserve-3d;
  backface-visibility:hidden;
  -webkit-backface-visibility:hidden;
  z-index:5;
}
.slide.active{opacity:1; pointer-events:auto;}

/* allow a “next page underneath” without changing idx early */
.slide.peek{opacity:1; pointer-events:none; z-index:4;}
/* hide the real active slide when the overlay is representing it */
.slide.ghost{opacity:0; pointer-events:none;}

.slide img{
  max-width:100%;
  max-height:100%;
  position:absolute;
  inset:0;
  margin:auto;
  object-fit:contain;
  border-radius:14px;
  box-shadow:
    inset 0 0 30px rgba(0,0,0,.20),
    0 18px 40px rgba(0,0,0,.45);
}

/* Text overlay (page 3) */
.text-wall{
  position:absolute;
  top:50%; left:50%;
  transform:translate(-50%,-50%);
  width:min(900px, 82%);
  max-height:80%;
  overflow-y:auto;
  background:rgba(0,0,0,0.56);
  padding:18px 18px;
  color:#fff;
  font-family:Georgia, 'Times New Roman', serif;
  line-height:1.5;
  border-radius:14px;
  z-index:105;
  box-shadow: 0 18px 40px rgba(0,0,0,.55);
  border:1px solid rgba(255,255,255,.10);
}
.text-wall-content{font-size:18px}
#close-text{
  position:absolute;
  top:40px; right:40px;
  background:rgba(0,0,0,.36);
  border:1px solid rgba(255,255,255,.14);
  color:#fff;
  font-size:24px;
  width:38px; height:38px;
  border-radius:12px;
  cursor:pointer;
  z-index:106;
  display:none;
}
#close-text:hover{background:rgba(255,255,255,.12)}

/* Show-message icon */
#open-text{
  position:absolute;
  bottom:20px; left:20px;
  width:64px; height:64px;
  cursor:pointer;
  z-index:101;
  transition:transform 0.18s ease;
}
#open-text:hover{transform:scale(1.08)}

/* Navigation */
#prev,#next{
  position:absolute; top:50%; transform:translateY(-50%);
  background:none; border:none; padding:0;
  cursor:pointer;
  z-index:100;
}
#prev{left:50px}
#next{right:50px}
#prev img,#next img{width:120px;height:120px}
#prev[disabled],#next[disabled]{opacity:.35;cursor:not-allowed;filter:grayscale(1)}

/* Progress */
#progress{
  position:absolute;
  bottom:20px; right:20px;
  background:rgba(0,0,0,0.50);
  color:#fff;
  padding:0.32em 0.62em;
  border-radius:10px;
  font-family:Arial,sans-serif;
  font-weight:800;
  z-index:101;
  border:1px solid rgba(255,255,255,.10);
}

/* Volume */
#volume-control{
  position:absolute;
  top: calc(50% + 0.25 * 80px);
  right:50px;
  display:flex;
  align-items:center;
  gap:10px;
  z-index:99;
}
#volume-icon{width:48px;height:48px;cursor:pointer}
#volume-slider{
  width:140px;
  -webkit-appearance:none;
  appearance:none;
  height:4px;
  background:rgba(255,255,255,0.30);
  border-radius:2px;
}
#volume-slider::-webkit-slider-thumb{
  -webkit-appearance:none;
  appearance:none;
  width:16px;height:16px;
  background:#00ffff;
  border-radius:50%;
  cursor:pointer;
  border:2px solid #fff;
}
#volume-slider::-moz-range-thumb{
  width:16px;height:16px;
  background:#00ffff;
  border:2px solid #fff;
  border-radius:50%;
  cursor:pointer;
}

/* ==============================
   PAGE TURN OVERLAY (SPINE LEFT)
   - Overlay bounded to active page image rect by JS.
   - Hinge ALWAYS LEFT edge.
   - Turning sheet ALWAYS single image.
   ============================== */
#turn{
  position:absolute;
  left:0; top:0;
  width:0; height:0;
  opacity:0;
  pointer-events:none;
  overflow:hidden;
  border-radius:14px;
  z-index:40;

  transform-style:preserve-3d;
  -webkit-transform-style:preserve-3d;
  will-change: transform, opacity;
}
.sheet{
  position:absolute;
  inset:0;
  border-radius:14px;
  overflow:hidden;

  transform-style:preserve-3d;
  -webkit-transform-style:preserve-3d;

  backface-visibility:hidden;
  -webkit-backface-visibility:hidden;

  transform-origin: 0% 50%;
  -webkit-transform-origin: 0% 50%;

  --edgeA: 0.0;
  --glintA: 0.0;
}
.sheet img{
  width:100%;
  height:100%;
  object-fit:contain;
  border-radius:14px;
  display:block;
}

/* Deterministic visibility */
.sheet.hidden{opacity:0; visibility:hidden}
.sheet.visible{opacity:1; visibility:visible}

/* Edge highlight */
.sheet::after{
  content:"";
  position:absolute;
  inset:0;
  pointer-events:none;
  background:
    linear-gradient(
      90deg,
      rgba(255,255,255,0) 60%,
      rgba(255,255,255,var(--edgeA)) 86%,
      rgba(255,255,255,0) 100%
    );
  mix-blend-mode: screen;
}
.sheet::before{
  content:"";
  position:absolute;
  inset:-10%;
  pointer-events:none;
  background:
    radial-gradient(320px 220px at 92% 45%,
      rgba(255,255,255,var(--glintA)) 0%,
      rgba(255,255,255,0) 62%);
  mix-blend-mode: overlay;
}

/* Shadow under the turning sheet */
#turnShadow{
  position:absolute;
  left:0; top:0;
  width:0; height:0;
  pointer-events:none;
  z-index:39;
  opacity:0;
  border-radius:14px;
  overflow:hidden;

  --sx: 18%;
  --sd: .28;
  --sb: 14px;

  background:
    radial-gradient(140% 90% at var(--sx) 55%,
      rgba(0,0,0,var(--sd)) 0%,
      rgba(0,0,0,0) 62%);
  filter: blur(var(--sb));
}
"""

TEMPLATE_JS = r"""
document.addEventListener('DOMContentLoaded', () => {
  // ─────────────────────────────────────────────────────────────
  // Elements
  // ─────────────────────────────────────────────────────────────
  const overlay   = document.getElementById('curtain-overlay');
  const cLeft     = document.getElementById('curtain-left');
  const cRight    = document.getElementById('curtain-right');
  const beginBtn  = document.getElementById('begin-button');

  const slides    = Array.from(document.querySelectorAll('.slide'));
  const prevBtn   = document.getElementById('prev');
  const nextBtn   = document.getElementById('next');
  const progress  = document.getElementById('progress');

  const turn       = document.getElementById('turn');
  const turnShadow = document.getElementById('turnShadow');

  const sheetFront = document.getElementById('sheetFront');
  const sheetBack  = document.getElementById('sheetBack');     // kept, always hidden
  const imgFront   = document.getElementById('turnFrontImg');
  const imgBack    = document.getElementById('turnBackImg');    // unused after fix

  const wall       = document.getElementById('textWall');
  const closeText  = document.getElementById('close-text');
  const openText   = document.getElementById('open-text');

  const volIcon   = document.getElementById('volume-icon');
  const music     = document.getElementById('bg-music');

  // ─────────────────────────────────────────────────────────────
  // State
  // ─────────────────────────────────────────────────────────────
  const TOTAL = slides.length; // 4
  let started = false;

  let idx = 0;
  let flipping = false;

  // wall overlay behavior (index 2)
  let wallClosedByUser = false;

  // Volume
  let slider = null;
  const VOL_KEY = 'ls_volume_0_100';

  // Audio pool
  const flipPool = Array.from({length: 10}, (_, i) => `gallery/sounds/flip${i+1}.mp3`);
  const glissSrc = 'gallery/sounds/glissando.mp3';

  // ─────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────
  function clamp(n, a, b){ return Math.max(a, Math.min(b, n)); }

  function setDisabled(btn, disabled){
    btn.disabled = !!disabled;
    btn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  }

  function updateProgress(){
    progress.textContent = `Page ${idx + 1} of ${TOTAL}`;
  }

  function activeSlide(){
    return slides[idx];
  }

  function slideImageEl(slide){
    return slide ? slide.querySelector('img') : null;
  }

  function slideImageSrc(slide){
    const im = slideImageEl(slide);
    return im ? im.getAttribute('src') : '';
  }

  function setActiveIndex(newIdx){
    idx = clamp(newIdx, 0, TOTAL - 1);
    slides.forEach((s, i) => {
      s.classList.toggle('active', i === idx);
      s.classList.remove('peek');
      s.classList.remove('ghost');
    });
    updateProgress();
    syncButtons();
    syncWallUI();
  }

  function syncButtons(){
    const atFirst = (idx === 0);
    const atLast  = (idx === TOTAL - 1);
    setDisabled(prevBtn, !started || flipping || atFirst);
    setDisabled(nextBtn, !started || flipping || atLast);
  }

  function isWallPage(){ return idx === 2; }

  function syncWallUI(){
    const onWall = isWallPage();
    closeText.style.display = onWall ? 'block' : 'none';

    if (!onWall){
      wall.style.display = 'none';
      openText.style.display = 'none';
      return;
    }

    if (!wallClosedByUser){
      wall.style.display = 'block';
      openText.style.display = 'none';
    } else {
      wall.style.display = 'none';
      openText.style.display = 'block';
    }
  }

  function playOneShot(src, volume01){
    try{
      const a = new Audio(src);
      a.preload = 'auto';
      a.volume = clamp(volume01, 0, 1);
      a.play().catch(()=>{});
    }catch(_){}
  }

  function playFlip(){
    const pick = flipPool[Math.floor(Math.random() * flipPool.length)];
    const vol = clamp(music.volume, 0, 1);
    playOneShot(pick, vol);
  }

  function ensureSlider(){
    if (slider) return slider;
    slider = document.createElement('input');
    slider.type = 'range';
    slider.id = 'volume-slider';
    slider.min = '0';
    slider.max = '100';
    slider.value = String(Math.round(loadVolume0to100()));
    slider.title = 'Volume';
    document.getElementById('volume-control').appendChild(slider);

    slider.addEventListener('input', () => {
      const v = clamp(parseInt(slider.value || '0', 10), 0, 100);
      setVolume0to100(v, true);
    });

    return slider;
  }

  function loadVolume0to100(){
    const raw = localStorage.getItem(VOL_KEY);
    if (raw !== null){
      const v = parseInt(raw, 10);
      if (!Number.isNaN(v)) return clamp(v, 0, 100);
    }
    const v0 = (typeof INITIAL_VOLUME === 'number') ? INITIAL_VOLUME : 30;
    return clamp(Math.round(v0), 0, 100);
  }

  function setVolume0to100(v, persist){
    const vv = clamp(Math.round(v), 0, 100);
    const vol01 = vv / 100;

    music.volume = vol01;
    music.muted = (vv === 0);

    volIcon.src = (vv === 0) ? 'gallery/controls/voloff.png' : 'gallery/controls/volon.png';
    if (slider) slider.value = String(vv);

    if (persist){
      try{ localStorage.setItem(VOL_KEY, String(vv)); }catch(_){}
    }
  }

  function rectForActiveImage(){
    const s = activeSlide();
    const im = slideImageEl(s);
    if (!im) return null;
    const r = im.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return null;
    return r;
  }

  function placeTurnToRect(r){
    turn.style.left = `${r.left}px`;
    turn.style.top = `${r.top}px`;
    turn.style.width = `${r.width}px`;
    turn.style.height = `${r.height}px`;

    turnShadow.style.left = `${r.left}px`;
    turnShadow.style.top = `${r.top}px`;
    turnShadow.style.width = `${r.width}px`;
    turnShadow.style.height = `${r.height}px`;
  }

  function easeInOutCubic(t){
    return t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3)/2;
  }

  function setTurnVisible(on){
    turn.style.opacity = on ? '1' : '0';
    turnShadow.style.opacity = on ? '1' : '0';
  }

  function setTurnRotationDeg(deg){
    turn.style.transformOrigin = '0% 50%';
    turn.style.transform = `rotateY(${deg}deg)`;

    const a = Math.abs(deg);
    const t = clamp(a / 180, 0, 1);
    const edge = Math.pow(Math.sin(t * Math.PI), 1.2);
    const glint = Math.pow(Math.sin(t * Math.PI), 2.0);

    sheetFront.style.setProperty('--edgeA',  String(0.28 * edge));
    sheetFront.style.setProperty('--glintA', String(0.22 * glint));

    const dir = (deg < 0) ? 1 : -1;
    const sx = (dir > 0) ? 26 : 16;
    const sd = 0.14 + 0.22 * edge;
    const sb = 10 + 10 * edge;
    turnShadow.style.setProperty('--sx', `${sx}%`);
    turnShadow.style.setProperty('--sd', `${sd}`);
    turnShadow.style.setProperty('--sb', `${sb}px`);
  }

  function cleanupTransient(curSlide, tgtSlide){
    if (curSlide) curSlide.classList.remove('ghost');
    if (tgtSlide) tgtSlide.classList.remove('peek');
  }

  function flipTo(targetIdx){
    if (!started) return;
    if (flipping) return;

    const tIdx = clamp(targetIdx, 0, TOTAL - 1);
    if (tIdx === idx) return;

    const r = rectForActiveImage();
    if (!r){
      setActiveIndex(tIdx);
      return;
    }

    flipping = true;
    syncButtons();

    const goingNext = (tIdx > idx);

    const curSlide = slides[idx];
    const tgtSlide = slides[tIdx];

    const curSrc = slideImageSrc(curSlide);
    const tgtSrc = slideImageSrc(tgtSlide);

    placeTurnToRect(r);

    sheetBack.classList.add('hidden');
    sheetBack.classList.remove('visible');
    imgBack.src = '';

    sheetFront.classList.remove('hidden');
    sheetFront.classList.add('visible');

    if (goingNext){
      tgtSlide.classList.add('peek');
      curSlide.classList.add('ghost');
      imgFront.src = curSrc;
      setTurnVisible(true);
      setTurnRotationDeg(0);
    } else {
      imgFront.src = tgtSrc;
      setTurnVisible(true);
      setTurnRotationDeg(-180);
    }

    playFlip();

    const DURATION = 620;
    const t0 = performance.now();

    function step(now){
      const elapsed = now - t0;
      const raw = clamp(elapsed / DURATION, 0, 1);
      const e = easeInOutCubic(raw);

      const deg = goingNext
        ? (0 + (-180 - 0) * e)
        : (-180 + (0 - (-180)) * e);

      setTurnRotationDeg(deg);

      if (raw < 1){
        requestAnimationFrame(step);
        return;
      }

      cleanupTransient(curSlide, tgtSlide);
      setActiveIndex(tIdx);

      setTurnVisible(false);
      turn.style.width = '0px';
      turn.style.height = '0px';
      turnShadow.style.width = '0px';
      turnShadow.style.height = '0px';

      flipping = false;
      syncButtons();
    }

    requestAnimationFrame(step);
  }

  window.addEventListener('resize', () => {
    if (!flipping) return;
    const r = rectForActiveImage();
    if (r) placeTurnToRect(r);
  });

  function openCurtain(){
  if (started) return;
  started = true;
  syncButtons();

  // --- Gliss: play and THEN start music when it actually ends ---
  let musicStarted = false;

  function startMusicAfterGliss(){
    if (musicStarted) return;
    musicStarted = true;

    const v = loadVolume0to100();
    setVolume0to100(v, false);

    try{
      music.currentTime = 0;
      music.volume = 0;
      music.muted = (v === 0);
      music.play().catch(()=>{});
    }catch(_){}

    const target = clamp(v / 100, 0, 1);
    const fadeMs = 900;
    const start = performance.now();

    function fadeStep(now){
      const t = clamp((now - start) / fadeMs, 0, 1);
      const e = easeInOutCubic(t);
      music.volume = target * e;
      if (t < 1) requestAnimationFrame(fadeStep);
    }
    requestAnimationFrame(fadeStep);
  }

  // Play gliss as an Audio element so we can listen for "ended"
  try{
    const g = new Audio(glissSrc);
    g.preload = 'auto';
    g.volume = 0.10;

    // If it ends normally, start music immediately after
    g.addEventListener('ended', startMusicAfterGliss, { once: true });

    // If it errors or can't load, don't stall forever
    g.addEventListener('error', startMusicAfterGliss, { once: true });

    g.play().catch(() => {
      // If play fails (rare after a click, but possible), just start music
      startMusicAfterGliss();
    });

    // Fallback: if "ended" doesn't fire for any reason, start anyway after a hard limit
    // Set this to slightly longer than your gliss file length.
    setTimeout(startMusicAfterGliss, 2500);
  } catch(_){
    startMusicAfterGliss();
  }

  // Curtains move
  cLeft.style.animation = 'curtainLeftOut 1100ms cubic-bezier(.2,.9,.1,1) forwards';
  cRight.style.animation = 'curtainRightOut 1100ms cubic-bezier(.2,.9,.1,1) forwards';

  // If you already added this fade line, keep it here:
  overlay.style.animation = 'curtainOverlayFadeOut 1100ms cubic-bezier(.2,.9,.1,1) forwards';

  beginBtn.disabled = true;
  beginBtn.style.opacity = '0';
  beginBtn.style.pointerEvents = 'none';

  setTimeout(() => {
    overlay.style.pointerEvents = 'none';
    overlay.setAttribute('aria-hidden', 'true');
    setTimeout(() => overlay.remove(), 250);
    syncButtons();
  }, 1250);
}


  beginBtn.addEventListener('click', (e) => {
    e.preventDefault();
    openCurtain();
  });

  prevBtn.addEventListener('click', () => flipTo(idx - 1));
  nextBtn.addEventListener('click', () => flipTo(idx + 1));

  window.addEventListener('keydown', (e) => {
    if (!started) return;
    if (flipping) return;

    if (e.key === 'ArrowLeft'){
      e.preventDefault();
      flipTo(idx - 1);
    } else if (e.key === 'ArrowRight'){
      e.preventDefault();
      flipTo(idx + 1);
    } else if (e.key === 'Escape'){
      if (isWallPage() && wall.style.display !== 'none'){
        wall.style.display = 'none';
        openText.style.display = 'block';
        wallClosedByUser = true;
      }
    }
  });

  closeText.addEventListener('click', () => {
    if (!isWallPage()) return;
    wall.style.display = 'none';
    openText.style.display = 'block';
    wallClosedByUser = true;
  });

  openText.addEventListener('click', () => {
    if (!isWallPage()) return;
    wall.style.display = 'block';
    openText.style.display = 'none';
    wallClosedByUser = false;
  });

  volIcon.addEventListener('click', () => {
    const s = ensureSlider();
    s.style.display = (s.style.display === 'none' || !s.style.display) ? 'block' : 'none';
  });

  ensureSlider().style.display = 'none';

  setVolume0to100(loadVolume0to100(), false);

  setActiveIndex(0);
  syncButtons();
  syncWallUI();
  setTurnVisible(false);
});
"""

