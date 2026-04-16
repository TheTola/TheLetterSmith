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
# - Viewer volume must NEVER be persisted.
# - Letter must ALWAYS initialize from injected INITIAL_VOLUME.
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
  <div id="curtain-overlay" aria-hidden="false" role="dialog" aria-modal="true" aria-labelledby="begin-button">
    <img id="curtain-left"  src="gallery/controls/cleft.png"  alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <img id="curtain-right" src="gallery/controls/cright.png" alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <button id="begin-button" type="button">Tap to Begin</button>
  </div>

  <!-- Main stage -->
  <div id="slideshow" style="opacity:0;visibility:hidden;pointer-events:none;" aria-hidden="true">
    <!-- Page-turn overlay (JS sizes to the ACTIVE page-image rect) -->
    <div id="turn" aria-hidden="true">
      <div class="sheet sheet-front visible" id="sheetFront">
        <img id="turnFrontImg" alt="" aria-hidden="true">
      </div>
      <!-- kept for compatibility; never shown after this fix -->
      <div class="sheet sheet-back hidden" id="sheetBack">
        <img id="turnBackImg" alt="" aria-hidden="true">
      </div>
    </div>
    <div id="turnShadow" aria-hidden="true"></div>

    <!-- Slides -->
    <section class="slide active" data-index="0" id="slide-0">
      <img src="gallery/pages/cover.png" alt="Cover Page" decoding="async" fetchpriority="high">
    </section>

    <section class="slide" data-index="1" id="slide-1">
      <img src="gallery/pages/letter.png" alt="Main Letter" decoding="async">
    </section>

    <section class="slide" data-index="2" id="slide-2">
      <img src="gallery/pages/wall.png" alt="Text Wall Background" decoding="async">
      <button id="close-text" type="button" title="Close Text" aria-label="Close message">&times;</button>
      <div class="text-wall" id="textWall" style="display:none;" role="dialog" aria-modal="false" aria-label="Message text" aria-hidden="true" tabindex="-1">
        <div class="text-wall-content" id="textWallContent">{{MESSAGE_HTML}}</div>
      </div>
    </section>

    <section class="slide" data-index="3" id="slide-3">
      <img src="gallery/pages/back.png" alt="Final Backdrop" decoding="async">
    </section>

    <!-- Navigation -->
    <button id="prev" type="button" title="Previous page" aria-label="Previous page">
      <img src="gallery/controls/ppage.png" alt="" aria-hidden="true">
    </button>
    <button id="next" type="button" title="Next page" aria-label="Next page">
      <img src="gallery/controls/npage.png" alt="" aria-hidden="true">
    </button>

    <div id="progress" aria-live="polite">Page 1 of 4</div>

    <!-- Show message (reopen) icon -->
    <img
      id="open-text"
      src="gallery/controls/showmessageicon.png"
      alt=""
      title="Show Message"
      role="button"
      tabindex="0"
      aria-label="Show message"
      style="display:none;"
    />
  </div>

  <!-- Volume control (slider injected by JS) -->
  <div id="volume-control" style="opacity:0;visibility:hidden;pointer-events:none;" aria-hidden="true">
    <img id="volume-icon" src="gallery/controls/volon.png" alt="" title="Volume" role="button" tabindex="0" aria-label="Toggle volume slider">
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
  opacity:0;
  visibility:hidden;
  pointer-events:none;
  transition:opacity 180ms ease;
  background:
    radial-gradient(900px 600px at 30% 25%, rgba(255,255,255,.08), transparent 60%),
    radial-gradient(900px 600px at 80% 70%, rgba(0,0,0,.35), transparent 60%),
    linear-gradient(180deg, #0b0c12, #05060a);
  transform-style:preserve-3d;
  -webkit-transform-style:preserve-3d;
}
body.stage-ready #slideshow{opacity:1; visibility:visible; pointer-events:auto}

/* Curtain overlay */
#curtain-overlay{
  position:absolute; inset:0; z-index:9999;
  overflow:hidden; pointer-events:all;
  opacity:0;

  background:
    radial-gradient(900px 600px at 30% 25%, rgba(255,255,255,.08), transparent 60%),
    radial-gradient(900px 600px at 80% 70%, rgba(0,0,0,.35), transparent 60%),
    linear-gradient(180deg, #0b0c12, #05060a);

  will-change: opacity;
}
#curtain-overlay.is-visible{animation:curtainIntroFadeIn 520ms ease-out forwards}

#curtain-left,#curtain-right{
  position:absolute; top:0; left:0;
  width:100%; height:100%;
  object-fit:cover;
  z-index:10000;
  pointer-events:none;
  opacity:0;
  will-change:opacity,transform;
}
#curtain-overlay.is-visible #curtain-left,
#curtain-overlay.is-visible #curtain-right{animation:curtainPanelFadeIn 420ms ease-out forwards}
#curtain-overlay.curtain-fallback #curtain-left,
#curtain-overlay.curtain-fallback #curtain-right{display:none}
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
#curtain-overlay:not(.is-visible) #begin-button{opacity:0}
#begin-button:hover{background:rgba(0,0,0,0.78)}
@keyframes curtainIntroFadeIn{from{opacity:0}to{opacity:1}}
@keyframes curtainPanelFadeIn{from{opacity:0}to{opacity:1}}
@keyframes pulse{
  0%,100%{opacity:.65; transform:translate(-50%,-50%) scale(1)}
  50%{opacity:1; transform:translate(-50%,-50%) scale(1.05)}
}
@keyframes curtainLeftOut{from{transform:translateX(0)}to{transform:translateX(-100vw)}}
@keyframes curtainRightOut{from{transform:translateX(0)}to{transform:translateX(100vw)}}
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
.slide.peek{opacity:1; pointer-events:none; z-index:4;}
.slide.ghost{opacity:0; pointer-events:none;}
.slide.asset-failed img{opacity:0}
.slide.asset-failed::after{
  content:attr(data-fallback-label);
  position:absolute;
  inset:50% auto auto 50%;
  transform:translate(-50%,-50%);
  width:min(520px, 72%);
  padding:18px 22px;
  color:#fff;
  text-align:center;
  font:700 20px/1.4 'Segoe UI', Arial, sans-serif;
  background:rgba(0,0,0,.58);
  border:1px solid rgba(255,255,255,.14);
  border-radius:14px;
  box-shadow:0 18px 40px rgba(0,0,0,.45);
  z-index:6;
}

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
#open-text:focus-visible,
#volume-icon:focus-visible,
#begin-button:focus-visible,
#close-text:focus-visible,
#prev:focus-visible,
#next:focus-visible{
  outline:3px solid #fff;
  outline-offset:4px;
}

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
  opacity:0;
  visibility:hidden;
  pointer-events:none;
  transition:opacity 180ms ease;
}
body.stage-ready #volume-control{opacity:1; visibility:visible; pointer-events:auto}
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
.sheet.hidden{opacity:0; visibility:hidden}
.sheet.visible{opacity:1; visibility:visible}
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
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{scroll-behavior:auto !important}
  #slideshow,#volume-control,#open-text{transition:none}
  #begin-button{animation:none}
  #curtain-overlay.is-visible,
  #curtain-overlay.is-visible #curtain-left,
  #curtain-overlay.is-visible #curtain-right{
    animation-duration:0.01ms !important;
    animation-iteration-count:1 !important;
  }
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

  const slideshowEl = document.getElementById('slideshow');
  const volumeControl = document.getElementById('volume-control');
  const volIcon   = document.getElementById('volume-icon');
  const music     = document.getElementById('bg-music');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const curtainIntroRevealMs = prefersReducedMotion ? 80 : 650;
  const curtainOpenMs = prefersReducedMotion ? 140 : 1100;
  const curtainCleanupMs = curtainOpenMs + 150;
  const flipDurationMs = prefersReducedMotion ? 0 : 620;
  const musicFadeMs = prefersReducedMotion ? 120 : 900;

  // ─────────────────────────────────────────────────────────────
  // State
  // ─────────────────────────────────────────────────────────────
  const TOTAL = slides.length; // 4
  let started = false;

  let idx = 0;
  let flipping = false;

  // wall overlay behavior (index 2)
  let wallClosedByUser = false;

  // Volume (NO persistence allowed)
  let slider = null;

  // Audio pool
  const flipPool = Array.from({length: 10}, (_, i) => `gallery/sounds/flip${i+1}.mp3`);
  const glissSrc = 'gallery/sounds/glissando.mp3';
  let stageReady = false;
  let introStarted = false;

  function setHiddenState(el, hidden){
    if (!el) return;
    el.setAttribute('aria-hidden', hidden ? 'true' : 'false');
  }

  function bindPress(el, handler){
    el.addEventListener('click', handler);
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      handler(e);
    });
  }

  function markSlideAssetFailed(slide, img){
    if (!slide || slide.classList.contains('asset-failed')) return;
    slide.classList.add('asset-failed');
    slide.dataset.fallbackLabel = img?.getAttribute('alt') || 'Page image unavailable';
  }

  function installImageFallbacks(){
    slides.forEach((slide) => {
      const img = slideImageEl(slide);
      if (!img) return;

      const handleError = () => markSlideAssetFailed(slide, img);
      img.addEventListener('error', handleError, { once: true });
      if (img.complete && img.naturalWidth === 0){
        handleError();
      }
    });

    [cLeft, cRight].forEach((img) => {
      const handleError = () => {
        img.style.display = 'none';
        overlay.classList.add('curtain-fallback');
      };
      img.addEventListener('error', handleError, { once: true });
      if (img.complete && img.naturalWidth === 0){
        handleError();
      }
    });
  }

  function waitForImageReady(img){
    if (!img) return Promise.resolve(false);

    if (img.complete){
      if (img.naturalWidth === 0) return Promise.resolve(false);
      if (typeof img.decode === 'function'){
        return img.decode().then(() => true).catch(() => true);
      }
      return Promise.resolve(true);
    }

    return new Promise((resolve) => {
      const handleLoad = () => {
        if (typeof img.decode === 'function'){
          img.decode().then(() => resolve(true)).catch(() => resolve(true));
          return;
        }
        resolve(true);
      };

      img.addEventListener('load', handleLoad, { once: true });
      img.addEventListener('error', () => resolve(false), { once: true });
    });
  }

  function waitForCriticalAssets(){
    const criticalImages = [cLeft, cRight, slideImageEl(slides[0])].filter(Boolean);
    const assetWait = Promise.allSettled(criticalImages.map(waitForImageReady));
    const timeoutWait = new Promise((resolve) => {
      setTimeout(resolve, prefersReducedMotion ? 120 : 1600);
    });
    return Promise.race([assetWait, timeoutWait]);
  }

  function revealStage(){
    if (stageReady) return;
    stageReady = true;
    slideshowEl.style.opacity = '';
    slideshowEl.style.visibility = '';
    slideshowEl.style.pointerEvents = '';
    volumeControl.style.opacity = '';
    volumeControl.style.visibility = '';
    volumeControl.style.pointerEvents = '';
    setHiddenState(slideshowEl, false);
    setHiddenState(volumeControl, false);
    document.body.classList.add('stage-ready');
    setActiveIndex(0);
    syncButtons();
    syncWallUI();
    setTurnVisible(false);
  }

  function startCurtainIntro(){
    if (introStarted) return;
    introStarted = true;

    function onIntroEnd(e){
      if (e.animationName !== 'curtainIntroFadeIn') return;
      overlay.removeEventListener('animationend', onIntroEnd);
      revealStage();
    }

    overlay.addEventListener('animationend', onIntroEnd);
    setTimeout(revealStage, curtainIntroRevealMs);

    requestAnimationFrame(() => {
      overlay.classList.add('is-visible');
    });
  }

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

  function setWallOpen(open){
    wall.style.display = open ? 'block' : 'none';
    setHiddenState(wall, !open);
    openText.style.display = open ? 'none' : 'block';
    setHiddenState(openText, open);
    closeText.style.display = open ? 'block' : 'none';
    setHiddenState(closeText, !open);
  }

  function syncWallUI(){
    const onWall = isWallPage();

    if (!onWall){
      wall.style.display = 'none';
      openText.style.display = 'none';
      closeText.style.display = 'none';
      setHiddenState(wall, true);
      setHiddenState(openText, true);
      setHiddenState(closeText, true);
      return;
    }

    if (!wallClosedByUser){
      setWallOpen(true);
    } else {
      setWallOpen(false);
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
      setVolume0to100(v);
    });

    return slider;
  }

  // IMPORTANT: NO persistence. Always comes from injected INITIAL_VOLUME.
  function loadVolume0to100(){
    const v0 = (typeof INITIAL_VOLUME === 'number') ? INITIAL_VOLUME : 50;
    return clamp(Math.round(v0), 0, 100);
  }

  // IMPORTANT: NO persistence. Session-only changes.
  function setVolume0to100(v){
    const vv = clamp(Math.round(v), 0, 100);
    const vol01 = vv / 100;

    music.volume = vol01;
    music.muted = (vv === 0);

    volIcon.src = (vv === 0) ? 'gallery/controls/voloff.png' : 'gallery/controls/volon.png';
    if (slider) slider.value = String(vv);
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

    const DURATION = flipDurationMs;
    if (DURATION <= 0){
      cleanupTransient(curSlide, tgtSlide);
      setActiveIndex(tIdx);
      setTurnVisible(false);
      turn.style.width = '0px';
      turn.style.height = '0px';
      turnShadow.style.width = '0px';
      turnShadow.style.height = '0px';
      flipping = false;
      syncButtons();
      return;
    }

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

      // ALWAYS initialize from injected INITIAL_VOLUME
      const v = loadVolume0to100();
      setVolume0to100(v);

      try{
        music.currentTime = 0;
        music.volume = 0;
        music.muted = (v === 0);
        music.play().catch(()=>{});
      }catch(_){}

      const target = clamp(v / 100, 0, 1);
      const fadeMs = musicFadeMs;
      const start = performance.now();

      function fadeStep(now){
        const t = clamp((now - start) / fadeMs, 0, 1);
        const e = easeInOutCubic(t);
        music.volume = target * e;
        if (t < 1) requestAnimationFrame(fadeStep);
      }
      requestAnimationFrame(fadeStep);
    }

    try{
      const g = new Audio(glissSrc);
      g.preload = 'auto';
      g.volume = 0.10;

      g.addEventListener('ended', startMusicAfterGliss, { once: true });
      g.addEventListener('error', startMusicAfterGliss, { once: true });

      g.play().catch(() => {
        startMusicAfterGliss();
      });

      setTimeout(startMusicAfterGliss, 2500);
    } catch(_){
      startMusicAfterGliss();
    }

    // Curtains move
    cLeft.style.animation = `curtainLeftOut ${curtainOpenMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
    cRight.style.animation = `curtainRightOut ${curtainOpenMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
    overlay.style.animation = `curtainOverlayFadeOut ${curtainOpenMs}ms cubic-bezier(.2,.9,.1,1) forwards`;

    beginBtn.disabled = true;
    beginBtn.style.opacity = '0';
    beginBtn.style.pointerEvents = 'none';

    setTimeout(() => {
      overlay.style.pointerEvents = 'none';
      overlay.setAttribute('aria-hidden', 'true');
      setTimeout(() => overlay.remove(), 250);
      syncButtons();
    }, curtainCleanupMs);
  }

  bindPress(beginBtn, (e) => {
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
        setWallOpen(false);
        wallClosedByUser = true;
        openText.focus({preventScroll:true});
      }
    }
  });

  closeText.addEventListener('click', () => {
    if (!isWallPage()) return;
    setWallOpen(false);
    wallClosedByUser = true;
    openText.focus({preventScroll:true});
  });

  bindPress(openText, () => {
    if (!isWallPage()) return;
    setWallOpen(true);
    wallClosedByUser = false;
    closeText.focus({preventScroll:true});
  });

  bindPress(volIcon, () => {
    const s = ensureSlider();
    s.style.display = (s.style.display === 'none' || !s.style.display) ? 'block' : 'none';
  });

  ensureSlider().style.display = 'none';

  // Initialize immediately (still session-only)
  setVolume0to100(loadVolume0to100());
  setHiddenState(wall, true);
  setHiddenState(openText, true);
  setHiddenState(closeText, true);

  installImageFallbacks();
  waitForCriticalAssets().finally(startCurtainIntro);
});
"""
