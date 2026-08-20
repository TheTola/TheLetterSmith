
document.addEventListener('DOMContentLoaded', () => {
  const overlay   = document.getElementById('curtain-overlay');
  const cLeft     = document.getElementById('curtain-left');
  const cRight    = document.getElementById('curtain-right');
  const beginBtn  = document.getElementById('begin-button');
  const titleBanner = document.getElementById('title-banner');

  const slides    = Array.from(document.querySelectorAll('.slide'));
  const prevBtn   = document.getElementById('prev');
  const nextBtn   = document.getElementById('next');
  const restartBtn = document.getElementById('restart-button');
  const muteBtn = document.getElementById('mute-button');
  const fullscreenBtn = document.getElementById('fullscreen-button');
  const letterPreviewEl = document.getElementById('letter-preview');
  const turn = document.getElementById('turn');
  const turnShadow = document.getElementById('turnShadow');
  const sheetFront = document.getElementById('sheetFront');
  const imgFront = document.getElementById('turnFrontImg');

  const wall       = document.getElementById('textWall');
  const closeText  = document.getElementById('close-text');
  const openText   = document.getElementById('open-text');

  const slideshowEl = document.getElementById('slideshow');
  const volumeControl = document.getElementById('volume-control');
  const volIcon   = document.getElementById('volume-icon');
  const volIconImg = document.getElementById('volume-icon-img');
  let music       = document.getElementById('bg-music');
  let musicStandby = document.getElementById('bg-music-standby');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const curtainIntroRevealMs = prefersReducedMotion ? 80 : 520;
  const curtainFallbackOpenMs = prefersReducedMotion ? 140 : 2600;
  const curtainCleanupPadMs = prefersReducedMotion ? 20 : 0;
  const titleBannerDelayMs = prefersReducedMotion ? 80 : 500;
  const titleBannerFadeInMs = prefersReducedMotion ? 10 : 280;
  const titleBannerHoldMs = 3500;
  const titleBannerFadeOutMs = prefersReducedMotion ? 10 : 360;
  const glissSafetyPadMs = prefersReducedMotion ? 120 : 450;
  const musicFadeMs = prefersReducedMotion ? 120 : 900;
  const wallRevealDelayMs = prefersReducedMotion ? 80 : 2200;
  const wallRevealFadeMs = prefersReducedMotion ? 120 : 900;

  const TOTAL = slides.length;
  let started = false;
  let introControlsLocked = false;
  let idx = 0;
  const imageAnimationStates = new Map();
  let wallClosedByUser = false;
  let wallRevealLocked = false;
  let wallRevealTimer = null;
  let wallRevealUnlockTimer = null;
  let slider = null;
  let stageReady = false;
  let introStarted = false;
  let titleBannerStarted = false;
  let titleBannerRevealTimer = null;
  let titleBannerAutoDismissTimer = null;
  let titleBannerCleanupTimer = null;
  let deferredWarmStarted = false;
  let flipping = false;
  let musicPlaylistIndex = 0;
  let playlistTransitioning = false;
  let playlistTransitionTimer = null;
  const musicLoopDelayMs = 1200;
  const muteStorageKey = 'lettersmith.viewerMuted';
  let viewerMuted = loadViewerMuted();
  let currentVolume = loadVolume0to100();

  const flipPool = Array.from({length: 10}, (_, i) => `gallery/sounds/flip${i+1}.mp3`);
  const glissSrc = 'gallery/sounds/glissando.mp3';
  const deferredAssets = [
    { as: 'image', href: 'gallery/pages/letter.png' },
    { as: 'image', href: 'gallery/pages/wall.png' },
    { as: 'image', href: 'gallery/pages/back.png' },
    { as: 'image', href: 'gallery/controls/ppage.png' },
    { as: 'image', href: 'gallery/controls/npage.png' },
    { as: 'image', href: 'gallery/controls/volon.png' },
    { as: 'image', href: 'gallery/controls/voloff.png' },
    { as: 'image', href: 'gallery/controls/showmessageicon.png' },
    ...((Array.isArray(MUSIC_PLAYLIST) ? MUSIC_PLAYLIST : []).map((href) => ({ as: 'audio', href, type: 'audio/mpeg' }))),
    ...flipPool.map((href) => ({ as: 'audio', href, type: 'audio/mpeg' })),
    ...Object.values(IMAGE_ANIMATIONS).flatMap((config) => (
      Array.isArray(config.frames)
        ? config.frames.map((href) => ({ as: 'image', href }))
        : []
    )),
  ];

  function clamp(n, a, b){ return Math.max(a, Math.min(b, n)); }
  function setHiddenState(el, hidden){ if (el) el.setAttribute('aria-hidden', hidden ? 'true' : 'false'); }
  function setExpandedState(el, expanded){ if (el) el.setAttribute('aria-expanded', expanded ? 'true' : 'false'); }

  function bindPress(el, handler){
    if (!el) return;
    el.addEventListener('click', handler);
    if (el instanceof HTMLButtonElement) return;
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      handler(e);
    });
  }

  function installUltralinks(){
    const content = document.getElementById('textWallContent');
    if (!content) return;
    const links = Array.from(
      content.querySelectorAll('a[href^="ultralink:"],a[href^="hypernote:"]')
    );
    if (!links.length) return;

    const tooltip = document.createElement('div');
    tooltip.id = 'ultralink-tooltip';
    tooltip.className = 'ultralink-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    document.body.appendChild(tooltip);

    function tooltipTheme(){
      if (!wall) return 'theme-dark';
      const resolved = window.getComputedStyle(wall);
      const opacity = Number.parseFloat(
        resolved.getPropertyValue('--message-overlay-opacity')
      );
      if (Number.isFinite(opacity) && opacity <= 0.05){
        return 'theme-minimal';
      }
      const raw = resolved.getPropertyValue('--message-overlay-rgb').trim();
      const channels = raw.match(/[\d.]+/g);
      if (!channels || channels.length < 3) return 'theme-dark';
      const rgb = channels.slice(0, 3).map((value) => clamp(Number(value), 0, 255) / 255);
      const linear = rgb.map((value) => (
        value <= 0.04045
          ? value / 12.92
          : Math.pow((value + 0.055) / 1.055, 2.4)
      ));
      const luminance = (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
      return luminance < 0.42 ? 'theme-paper' : 'theme-dark';
    }
    tooltip.classList.add(tooltipTheme());

    function messageFor(link){
      const href = link.getAttribute('href') || '';
      const match = href.match(/^(?:ultralink|hypernote):(.*)$/i);
      if (!match) return '';
      try { return decodeURIComponent(match[1]); }
      catch (_error) { return match[1]; }
    }

    let activeLink = null;

    function place(link){
      const gap = 10;
      const margin = 16;
      const rect = link.getBoundingClientRect();
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      const left = clamp(
        rect.left + (rect.width / 2) - (width / 2),
        margin,
        Math.max(margin, window.innerWidth - width - margin)
      );
      let top = rect.bottom + gap;
      if (top + height > window.innerHeight - margin){
        top = Math.max(margin, rect.top - height - gap);
      }
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    }

    function show(link){
      const message = link.dataset.ultralinkMessage || messageFor(link);
      if (!message) return;
      activeLink = link;
      tooltip.textContent = message;
      tooltip.style.visibility = 'hidden';
      tooltip.classList.add('is-visible');
      place(link);
      tooltip.style.visibility = '';
    }

    function hide(link){
      if (link && activeLink !== link) return;
      activeLink = null;
      tooltip.classList.remove('is-visible');
    }

    links.forEach((link) => {
      link.dataset.ultralinkMessage = messageFor(link);
      link.setAttribute('role', 'button');
      link.setAttribute('tabindex', '0');
      link.setAttribute('aria-describedby', tooltip.id);
      link.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (activeLink === link) hide(link);
        else show(link);
      });
      link.addEventListener('mouseenter', () => show(link));
      link.addEventListener('mouseleave', () => {
        if (document.activeElement !== link) hide(link);
      });
      link.addEventListener('focus', () => show(link));
      link.addEventListener('blur', () => hide(link));
      link.addEventListener('keydown', (event) => {
        if (event.key === 'Escape'){
          hide(link);
          return;
        }
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        if (activeLink === link) hide(link);
        else show(link);
      });
    });

    window.addEventListener('scroll', () => activeLink && place(activeLink), true);
    window.addEventListener('resize', () => activeLink && place(activeLink));
    document.addEventListener('pointerdown', (event) => {
      if (!activeLink || activeLink.contains(event.target)) return;
      hide(activeLink);
    });
  }

  function warmDeferredAssets(){
    if (deferredWarmStarted) return;
    deferredWarmStarted = true;
    const warm = () => {
      deferredAssets.forEach((asset) => {
        const link = document.createElement('link');
        link.rel = 'preload';
        link.as = asset.as;
        link.href = asset.href;
        if (asset.type) link.type = asset.type;
        document.head.appendChild(link);
      });
    };
    if (typeof window.requestIdleCallback === 'function'){
      window.requestIdleCallback(warm, { timeout: prefersReducedMotion ? 120 : 900 });
      return;
    }
    setTimeout(warm, prefersReducedMotion ? 60 : 180);
  }

  function slideImageEl(slide){ return slide ? slide.querySelector('img') : null; }
  function slideImageSrc(slide){
    const image = slideImageEl(slide);
    return image ? image.getAttribute('src') || '' : '';
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
      if (img.complete && img.naturalWidth === 0) handleError();
    });
    [cLeft, cRight].forEach((img) => {
      if (!img) return;
      const handleError = () => {
        img.style.display = 'none';
        overlay.classList.add('curtain-fallback');
      };
      img.addEventListener('error', handleError, { once: true });
      if (img.complete && img.naturalWidth === 0) handleError();
    });
  }

  function waitForImageReady(img){
    if (!img) return Promise.resolve(false);
    if (img.complete){
      if (img.naturalWidth === 0) return Promise.resolve(false);
      if (typeof img.decode === 'function') return img.decode().then(() => true).catch(() => true);
      return Promise.resolve(true);
    }
    return new Promise((resolve) => {
      img.addEventListener('load', () => resolve(true), { once: true });
      img.addEventListener('error', () => resolve(false), { once: true });
    });
  }

  function waitForCriticalAssets(){
    const criticalImages = [cLeft, cRight, slideImageEl(slides[0])].filter(Boolean);
    const assetWait = Promise.allSettled(criticalImages.map(waitForImageReady));
    const timeoutWait = new Promise((resolve) => setTimeout(resolve, prefersReducedMotion ? 120 : 1600));
    return Promise.race([assetWait, timeoutWait]);
  }

  function imageAnimationConfig(slideIndex){
    const config = IMAGE_ANIMATIONS[String(slideIndex)];
    if (!config) return null;
    if (config.render_mode === 'native_gif'){
      return config.source && config.preview_source ? config : null;
    }
    return Array.isArray(config.frames) && config.frames.length > 1 ? config : null;
  }

  function cancelImageAnimation(slideIndex){
    const state = imageAnimationStates.get(slideIndex);
    if (!state) return;
    state.cancelled = true;
    if (state.timer !== null) clearTimeout(state.timer);
    if (state.config.render_mode === 'native_gif' && state.config.preview_source){
      state.image.src = state.config.preview_source;
    }
    state.timer = null;
    imageAnimationStates.delete(slideIndex);
  }

  function showImageAnimationFrame(state, frameIndex){
    if (state.cancelled) return;
    const source = state.config.frames[frameIndex];
    if (source) state.image.src = source;
  }

  function imageFrameDuration(state, frameIndex){
    const value = Number(state.config.durations_ms?.[frameIndex]);
    return Number.isFinite(value) && value > 0 ? value : 100;
  }

  function scheduleImageAnimation(state, delayMs, callback){
    if (state.cancelled) return;
    state.timer = setTimeout(() => {
      state.timer = null;
      if (!state.cancelled) callback();
    }, Math.max(0, Number(delayMs) || 0));
  }

  function configuredImagePlayCount(config){
    if (config.play_count === 'forever') return Infinity;
    const value = Number.parseInt(String(config.play_count), 10);
    return Number.isFinite(value) && value > 0 ? value : 1;
  }

  function effectiveImagePlayCount(config){
    const configured = configuredImagePlayCount(config);
    if (config.playback_mode !== 'original') return configured;
    const embedded = config.embedded_play_count === 'forever'
      ? Infinity
      : Math.max(1, Number.parseInt(String(config.embedded_play_count), 10) || 1);
    if (configured === Infinity) return embedded;
    return embedded === Infinity ? configured : Math.min(configured, embedded);
  }

  function playImageForward(state, frameIndex){
    const lastFrame = state.config.frames.length - 1;
    showImageAnimationFrame(state, frameIndex);
    scheduleImageAnimation(state, imageFrameDuration(state, frameIndex), () => {
      if (frameIndex < lastFrame){
        playImageForward(state, frameIndex + 1);
        return;
      }
      state.completedForwardPlays += 1;
      if (state.completedForwardPlays >= state.totalForwardPlays){
        // Hard rule: a completed animation stays on its last displayed frame.
        showImageAnimationFrame(state, lastFrame);
        return;
      }
      scheduleImageAnimation(state, state.config.loop_pause_ms, () => {
        if (state.config.playback_mode === 'ping_pong'){
          playImageReverse(state, lastFrame - 1);
        } else {
          playImageForward(state, 0);
        }
      });
    });
  }

  function playImageReverse(state, frameIndex){
    showImageAnimationFrame(state, frameIndex);
    scheduleImageAnimation(state, imageFrameDuration(state, frameIndex), () => {
      if (frameIndex > 0){
        playImageReverse(state, frameIndex - 1);
        return;
      }
      scheduleImageAnimation(
        state,
        state.config.loop_pause_ms,
        () => playImageForward(state, 0),
      );
    });
  }

  function prepareImageAnimation(slideIndex){
    const config = imageAnimationConfig(slideIndex);
    if (!config) return;
    cancelImageAnimation(slideIndex);
    const image = slideImageEl(slides[slideIndex]);
    if (!image) return;
    if (config.render_mode === 'native_gif') image.src = config.preview_source;
    else if (config.frames[0]) image.src = config.frames[0];
  }

  function activateImageAnimation(slideIndex){
    const config = imageAnimationConfig(slideIndex);
    if (!config) return;
    cancelImageAnimation(slideIndex);
    const image = slideImageEl(slides[slideIndex]);
    if (!image) return;
    const state = {
      cancelled: false,
      timer: null,
      image,
      config,
      completedForwardPlays: 0,
      totalForwardPlays: effectiveImagePlayCount(config),
    };
    imageAnimationStates.set(slideIndex, state);
    if (config.render_mode === 'native_gif'){
      image.src = config.source;
      return;
    }
    showImageAnimationFrame(state, 0);
    scheduleImageAnimation(
      state,
      config.start_delay_ms,
      () => playImageForward(state, 0),
    );
  }

  function revealStage(){
    if (stageReady) return;
    stageReady = true;
    setHiddenState(slideshowEl, false);
    setHiddenState(volumeControl, false);
    document.body.classList.add('stage-ready');
    setActiveIndex(0, { playSound: false });
  }

  function startCurtainIntro(){
    if (introStarted) return;
    introStarted = true;
    warmDeferredAssets();
    function onIntroEnd(e){
      if (e.animationName !== 'curtainIntroFadeIn') return;
      overlay.removeEventListener('animationend', onIntroEnd);
      revealStage();
    }
    overlay.addEventListener('animationend', onIntroEnd);
    setTimeout(revealStage, curtainIntroRevealMs);
    requestAnimationFrame(() => overlay.classList.add('is-visible'));
  }

  function isWallPage(){ return idx === 2; }
  function setDisabled(btn, disabled){
    btn.disabled = !!disabled;
    btn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  }
  function syncButtons(){
    const locked = !started || introControlsLocked || wallRevealLocked || flipping;
    setDisabled(prevBtn, locked || idx === 0);
    setDisabled(nextBtn, locked || idx === TOTAL - 1);
  }

  function clearWallRevealTimers(){
    if (wallRevealTimer !== null){ clearTimeout(wallRevealTimer); wallRevealTimer = null; }
    if (wallRevealUnlockTimer !== null){ clearTimeout(wallRevealUnlockTimer); wallRevealUnlockTimer = null; }
  }
  function setWallOpen(open){
    wall.classList.toggle('is-open', open);
    setHiddenState(wall, !open);
    openText.classList.toggle('is-visible', !open);
    setHiddenState(openText, open);
    closeText.classList.toggle('is-visible', open);
    setHiddenState(closeText, !open);
    setExpandedState(openText, open);
  }
  function hideWallDuringRevealDelay(){
    wall.classList.remove('is-open');
    openText.classList.remove('is-visible');
    closeText.classList.remove('is-visible');
    setHiddenState(wall, true);
    setHiddenState(openText, true);
    setHiddenState(closeText, true);
    setExpandedState(openText, false);
  }
  function unlockWallReveal(){
    wallRevealLocked = false;
    syncButtons();
  }
  function beginWallRevealSequence(){
    clearWallRevealTimers();
    wallRevealLocked = true;
    syncButtons();
    hideWallDuringRevealDelay();
    wallRevealTimer = setTimeout(() => {
      wallRevealTimer = null;
      if (!isWallPage() || wallClosedByUser){ unlockWallReveal(); return; }
      setWallOpen(true);
      wallRevealUnlockTimer = setTimeout(() => {
        wallRevealUnlockTimer = null;
        unlockWallReveal();
      }, wallRevealFadeMs + 120);
    }, wallRevealDelayMs);
  }
  function syncWallUI(){
    if (!HAS_MESSAGE){
      clearWallRevealTimers();
      wallRevealLocked = false;
      wall.classList.remove('is-open');
      openText.classList.remove('is-visible');
      closeText.classList.remove('is-visible');
      setHiddenState(wall, true);
      setHiddenState(openText, true);
      setHiddenState(closeText, true);
      setExpandedState(openText, false);
      syncButtons();
      return;
    }
    if (!isWallPage()){
      clearWallRevealTimers();
      wallRevealLocked = false;
      wall.classList.remove('is-open');
      openText.classList.remove('is-visible');
      closeText.classList.remove('is-visible');
      setHiddenState(wall, true);
      setHiddenState(openText, true);
      setHiddenState(closeText, true);
      setExpandedState(openText, false);
      syncButtons();
      return;
    }
    if (wallClosedByUser){
      clearWallRevealTimers();
      wallRevealLocked = false;
      setWallOpen(false);
      syncButtons();
      return;
    }
    beginWallRevealSequence();
  }

  function playOneShot(src, volume01){
    try{
      const a = new Audio(src);
      a.preload = 'auto';
      a.volume = clamp(volume01, 0, 1);
      a.muted = viewerMuted;
      a.play().catch(()=>{});
    }catch(_){ }
  }
  function playFlip(){
    const pick = flipPool[Math.floor(Math.random() * flipPool.length)];
    const vol = music ? clamp(music.volume, 0, 1) : 0.5;
    playOneShot(pick, vol);
  }

  function setActiveIndex(newIdx, opts = {}){
    const target = clamp(newIdx, 0, TOTAL - 1);
    if (target === idx && opts.force !== true){
      syncButtons();
      syncWallUI();
      return;
    }
    clearWallRevealTimers();
    wallRevealLocked = false;
    cancelImageAnimation(idx);
    prepareImageAnimation(target);
    idx = target;
    slides.forEach((s, i) => {
      s.classList.toggle('active', i === idx);
      s.classList.remove('peek');
      s.classList.remove('ghost');
    });
    if (idx === 2) wallClosedByUser = false;
    if (opts.playSound !== false) playFlip();
    syncButtons();
    syncWallUI();
    if (started) activateImageAnimation(idx);
  }

  function activeSlide(){ return slides[idx]; }
  function activeImageRect(){
    const image = slideImageEl(activeSlide());
    if (!image) return null;
    const rect = image.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2 ? rect : null;
  }
  function placeTurn(rect){
    for (const element of [turn, turnShadow]){
      element.style.left = `${rect.left}px`;
      element.style.top = `${rect.top}px`;
      element.style.width = `${rect.width}px`;
      element.style.height = `${rect.height}px`;
    }
  }
  function setTurnVisible(visible){
    turn.style.opacity = visible ? '1' : '0';
    turnShadow.style.opacity = visible ? '1' : '0';
  }
  function setTurnRotation(degrees){
    turn.style.transformOrigin = '0% 50%';
    turn.style.transform = `rotateY(${degrees}deg)`;
    const amount = clamp(Math.abs(degrees) / 180, 0, 1);
    const edge = Math.pow(Math.sin(amount * Math.PI), 1.2);
    const glint = Math.pow(Math.sin(amount * Math.PI), 2);
    sheetFront.style.setProperty('--edgeA', String(0.28 * edge));
    sheetFront.style.setProperty('--glintA', String(0.22 * glint));
    turnShadow.style.setProperty('--sx', degrees < 0 ? '26%' : '16%');
    turnShadow.style.setProperty('--sd', String(0.14 + (0.22 * edge)));
    turnShadow.style.setProperty('--sb', `${10 + (10 * edge)}px`);
  }
  function finishTurn(currentSlide, targetSlide, targetIndex){
    currentSlide.classList.remove('ghost');
    targetSlide.classList.remove('peek');
    setActiveIndex(targetIndex, { playSound: false });
    setTurnVisible(false);
    for (const element of [turn, turnShadow]){
      element.style.width = '0px';
      element.style.height = '0px';
    }
    slideshowEl.classList.remove('page-turning');
    slideshowEl.setAttribute('aria-busy', 'false');
    flipping = false;
    syncButtons();
  }
  function flipTo(targetIndex){
    if (!started || flipping || introControlsLocked || wallRevealLocked) return;
    const target = clamp(targetIndex, 0, TOTAL - 1);
    if (target === idx) return;
    const rect = activeImageRect();
    if (!rect || prefersReducedMotion){
      playFlip();
      setActiveIndex(target, { playSound: false });
      return;
    }

    flipping = true;
    syncButtons();
    slideshowEl.classList.add('page-turning');
    slideshowEl.setAttribute('aria-busy', 'true');

    const goingNext = target > idx;
    const currentSlide = slides[idx];
    const targetSlide = slides[target];
    prepareImageAnimation(target);
    placeTurn(rect);
    sheetFront.classList.remove('hidden');
    sheetFront.classList.add('visible');
    imgFront.src = goingNext
      ? slideImageSrc(currentSlide)
      : slideImageSrc(targetSlide);

    if (goingNext){
      targetSlide.classList.add('peek');
      currentSlide.classList.add('ghost');
      setTurnRotation(0);
    } else {
      setTurnRotation(-180);
    }
    setTurnVisible(true);
    playFlip();

    const duration = 620;
    const startedAt = performance.now();
    function animate(now){
      const raw = clamp((now - startedAt) / duration, 0, 1);
      const eased = raw < 0.5
        ? 4 * raw * raw * raw
        : 1 - (Math.pow((-2 * raw) + 2, 3) / 2);
      const degrees = goingNext
        ? -180 * eased
        : -180 + (180 * eased);
      setTurnRotation(degrees);
      if (raw < 1){
        requestAnimationFrame(animate);
        return;
      }
      finishTurn(currentSlide, targetSlide, target);
    }
    requestAnimationFrame(animate);
  }
  window.addEventListener('resize', () => {
    if (!flipping) return;
    const rect = activeImageRect();
    if (rect) placeTurn(rect);
  });

  function go(delta){
    if (!started || introControlsLocked || wallRevealLocked || flipping) return;
    const target = clamp(idx + delta, 0, TOTAL - 1);
    if (target === idx) return;
    dismissTitleBanner();
    flipTo(target);
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
    slider.setAttribute('aria-label', 'Volume level');
    setHiddenState(slider, true);
    volumeControl.appendChild(slider);
    slider.addEventListener('input', () => setVolume0to100(clamp(parseInt(slider.value || '0', 10), 0, 100)));
    return slider;
  }
  function loadVolume0to100(){
    const v0 = (typeof INITIAL_VOLUME === 'number') ? INITIAL_VOLUME : 50;
    return clamp(Math.round(v0), 0, 100);
  }
  function loadViewerMuted(){
    try{
      return window.sessionStorage.getItem(muteStorageKey) === 'true';
    }catch(_){
      return false;
    }
  }
  function saveViewerMuted(){
    try{
      window.sessionStorage.setItem(muteStorageKey, viewerMuted ? 'true' : 'false');
    }catch(_){ }
  }
  function setVolume0to100(v){
    const vv = clamp(Math.round(v), 0, 100);
    currentVolume = vv;
    const vol01 = vv / 100;
    const muted = viewerMuted || vv === 0;
    [music, musicStandby].forEach((audio) => {
      if (!audio) return;
      audio.volume = vol01;
      audio.muted = muted;
    });
    volIconImg.src = muted ? 'gallery/controls/voloff.png' : 'gallery/controls/volon.png';
    volIcon.setAttribute('aria-label', muted ? 'Audio muted. Toggle volume slider' : 'Toggle volume slider');
    if (slider) slider.value = String(vv);
  }

  function syncMuteButton(){
    if (!muteBtn) return;
    const label = viewerMuted ? 'Unmute' : 'Mute';
    const description = viewerMuted ? 'Unmute letter audio' : 'Mute letter audio';
    muteBtn.textContent = label;
    muteBtn.title = description;
    muteBtn.setAttribute('aria-label', description);
    muteBtn.setAttribute('aria-pressed', viewerMuted ? 'true' : 'false');
  }

  function setViewerMuted(muted){
    viewerMuted = !!muted;
    saveViewerMuted();
    setVolume0to100(currentVolume);
    syncMuteButton();
  }

  function musicSources(){
    return Array.isArray(MUSIC_PLAYLIST) ? MUSIC_PLAYLIST.filter((value) => typeof value === 'string' && value) : [];
  }
  function ensureInitialMusicSource(){
    const sources = musicSources();
    if (!sources.length || !music) return false;
    musicPlaylistIndex = clamp(musicPlaylistIndex, 0, sources.length - 1);
    const wanted = sources[musicPlaylistIndex];
    if (!music.getAttribute('src') || !music.src.endsWith(wanted)) music.src = wanted;
    return true;
  }
  function nextMusicIndex(sources){
    if (!sources.length) return -1;
    return (musicPlaylistIndex + 1) % sources.length;
  }
  function installPlaylistListeners(audio){
    if (!audio) return;
    audio.addEventListener('timeupdate', () => {
      if (audio !== music || playlistTransitioning) return;
      const sources = musicSources();
      const crossfadeMs = Math.max(0, Number(MUSIC_CROSSFADE_MS) || 0);
      if (sources.length < 2 || crossfadeMs <= 0) return;
      if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
      const remainingMs = (audio.duration - audio.currentTime) * 1000;
      if (remainingMs > 0 && remainingMs <= Math.max(120, crossfadeMs)) crossfadeToNextTrack();
    });
    audio.addEventListener('ended', () => {
      if (audio !== music || playlistTransitioning) return;
      advanceMusicSequence();
    });
  }
  function advanceMusicSequence(){
    const sources = musicSources();
    if (playlistTransitioning || !sources.length || !music) return;
    if (sources.length > 1 && Number(MUSIC_CROSSFADE_MS) > 0){
      crossfadeToNextTrack();
      return;
    }
    const nextIndex = nextMusicIndex(sources);
    playlistTransitioning = true;
    if (playlistTransitionTimer !== null) clearTimeout(playlistTransitionTimer);
    playlistTransitionTimer = setTimeout(() => {
      playlistTransitionTimer = null;
      const currentSources = musicSources();
      if (!music || !currentSources.length){
        playlistTransitioning = false;
        return;
      }
      musicPlaylistIndex = nextIndex % currentSources.length;
      const wanted = currentSources[musicPlaylistIndex];
      try{
        if (!music.getAttribute('src') || !music.src.endsWith(wanted)) music.src = wanted;
        music.currentTime = 0;
        music.volume = clamp(currentVolume / 100, 0, 1);
        music.muted = viewerMuted || currentVolume === 0;
        music.play().catch(()=>{});
      }catch(_){ }
      playlistTransitioning = false;
    }, musicLoopDelayMs);
  }
  function crossfadeToNextTrack(){
    const sources = musicSources();
    if (playlistTransitioning || !sources.length || !music || !musicStandby) return;
    playlistTransitioning = true;
    const nextIndex = nextMusicIndex(sources);
    const target = clamp(currentVolume / 100, 0, 1);
    const muted = viewerMuted || target === 0;
    musicStandby.src = sources[nextIndex];
    musicStandby.currentTime = 0;
    musicStandby.volume = 0;
    musicStandby.muted = muted;
    const duration = Math.max(120, Number(MUSIC_CROSSFADE_MS) || 1000);
    const startedAt = performance.now();
    musicStandby.play().catch(() => {
      playlistTransitioning = false;
    });
    function step(now){
      if (!playlistTransitioning) return;
      const t = clamp((now - startedAt) / duration, 0, 1);
      music.volume = target * (1 - t);
      musicStandby.volume = target * t;
      if (t < 1){ requestAnimationFrame(step); return; }
      music.pause();
      music.currentTime = 0;
      const previous = music;
      music = musicStandby;
      musicStandby = previous;
      musicPlaylistIndex = nextIndex;
      music.volume = target;
      music.muted = muted;
      musicStandby.volume = target;
      musicStandby.muted = muted;
      playlistTransitioning = false;
    }
    requestAnimationFrame(step);
  }
  installPlaylistListeners(music);
  installPlaylistListeners(musicStandby);
  if (!musicSources().length && volumeControl) volumeControl.style.display = 'none';
  function setSliderOpen(open){
    const shouldOpen = !!open;
    volumeControl.classList.toggle('slider-open', shouldOpen);
    setExpandedState(volIcon, shouldOpen);
    if (slider) setHiddenState(slider, !shouldOpen);
  }

  function glissDurationMs(audioEl){
    const d = audioEl && Number.isFinite(audioEl.duration) ? audioEl.duration : 0;
    if (d > 0.25) return Math.round(d * 1000);
    return curtainFallbackOpenMs;
  }
  function runCurtainMotion(durationMs, onDone){
    const openMs = prefersReducedMotion ? 140 : Math.max(500, Math.round(durationMs || curtainFallbackOpenMs));
    overlay.style.opacity = '1';
    overlay.style.animation = 'none';
    overlay.style.background = 'transparent';
    cLeft.style.opacity = '1';
    cRight.style.opacity = '1';
    cLeft.style.transform = 'translateX(0)';
    cRight.style.transform = 'translateX(0)';
    cLeft.style.animation = 'none';
    cRight.style.animation = 'none';
    void cLeft.offsetWidth;
    cLeft.style.animation = `curtainLeftOut ${openMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
    cRight.style.animation = `curtainRightOut ${openMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
    setTimeout(() => {
      overlay.style.pointerEvents = 'none';
      overlay.setAttribute('aria-hidden', 'true');
      overlay.remove();
      if (typeof onDone === 'function') onDone();
    }, openMs + curtainCleanupPadMs);
    return openMs;
  }
  function runTitleBanner(){
    if (titleBannerStarted) return;
    titleBannerStarted = true;
    titleBannerRevealTimer = setTimeout(() => {
      titleBannerRevealTimer = null;
      beginBtn.classList.add('is-dismissed');
      titleBanner.setAttribute('aria-hidden', 'false');
      titleBanner.classList.add('is-showing');
      titleBannerAutoDismissTimer = setTimeout(() => {
        titleBannerAutoDismissTimer = null;
        dismissTitleBanner();
      }, titleBannerFadeInMs + titleBannerHoldMs);
    }, titleBannerDelayMs);
  }
  function dismissTitleBanner(){
    if (titleBannerRevealTimer !== null){
      clearTimeout(titleBannerRevealTimer);
      titleBannerRevealTimer = null;
    }
    if (titleBannerAutoDismissTimer !== null){
      clearTimeout(titleBannerAutoDismissTimer);
      titleBannerAutoDismissTimer = null;
    }
    if (titleBannerCleanupTimer !== null){
      clearTimeout(titleBannerCleanupTimer);
      titleBannerCleanupTimer = null;
    }
    beginBtn.classList.add('is-dismissed');
    titleBanner.classList.remove('is-showing');
    if (titleBanner.getAttribute('aria-hidden') === 'true') return;
    titleBanner.classList.add('is-hiding');
    titleBannerCleanupTimer = setTimeout(() => {
      titleBannerCleanupTimer = null;
      titleBanner.classList.remove('is-hiding');
      titleBanner.setAttribute('aria-hidden', 'true');
    }, titleBannerFadeOutMs);
  }
  function openCurtain(){
    if (started) return;
    started = true;
    introControlsLocked = true;
    syncButtons();
    revealStage();
    activateImageAnimation(idx);
    beginBtn.disabled = true;
    beginBtn.style.pointerEvents = 'none';
    runTitleBanner();

    let musicStarted = false;
    let glissDone = false;
    let curtainDone = false;
    let introMotionStarted = false;
    let safetyTimer = null;

    function tryUnlockIntroControls(){
      if (!glissDone || !curtainDone) return;
      introControlsLocked = false;
      syncButtons();
    }
    function startMusicAfterGliss(){
      if (musicStarted) return;
      musicStarted = true;
      glissDone = true;
      if (safetyTimer !== null){ clearTimeout(safetyTimer); safetyTimer = null; }
      const v = currentVolume;
      setVolume0to100(v);
      if (!ensureInitialMusicSource()){
        tryUnlockIntroControls();
        return;
      }
      try{
        music.currentTime = 0;
        music.volume = 0;
        music.muted = viewerMuted || v === 0;
        music.play().catch(()=>{});
      }catch(_){ }
      const target = clamp(v / 100, 0, 1);
      const start = performance.now();
      function fadeStep(now){
        const t = clamp((now - start) / musicFadeMs, 0, 1);
        const e = t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t + 2, 2)/2;
        music.volume = target * e;
        if (t < 1) requestAnimationFrame(fadeStep);
      }
      requestAnimationFrame(fadeStep);
      tryUnlockIntroControls();
    }
    function beginGlissAndCurtain(g){
      if (introMotionStarted) return;
      introMotionStarted = true;
      const openMs = runCurtainMotion(glissDurationMs(g), () => {
        curtainDone = true;
        tryUnlockIntroControls();
      });
      try{
        g.currentTime = 0;
        g.play().catch(() => startMusicAfterGliss());
      }catch(_){ startMusicAfterGliss(); }
      safetyTimer = setTimeout(startMusicAfterGliss, openMs + glissSafetyPadMs);
    }
    try{
      const g = new Audio(glissSrc);
      g.preload = 'auto';
      g.volume = 0.10;
      g.muted = viewerMuted;
      g.addEventListener('ended', startMusicAfterGliss, { once: true });
      g.addEventListener('error', () => { beginGlissAndCurtain(g); startMusicAfterGliss(); }, { once: true });
      g.addEventListener('loadedmetadata', () => beginGlissAndCurtain(g), { once: true });
      g.load();
      setTimeout(() => beginGlissAndCurtain(g), 250);
    }catch(_){
      const openMs = runCurtainMotion(curtainFallbackOpenMs, () => { curtainDone = true; tryUnlockIntroControls(); });
      setTimeout(startMusicAfterGliss, openMs);
    }
  }

  bindPress(beginBtn, (e) => { e.preventDefault(); openCurtain(); });
  prevBtn.addEventListener('click', () => go(-1));
  nextBtn.addEventListener('click', () => go(1));
  if (restartBtn){
    restartBtn.addEventListener('click', () => {
      window.location.reload();
    });
  }
  if (muteBtn){
    muteBtn.addEventListener('click', () => {
      setViewerMuted(!viewerMuted);
    });
  }
  if (fullscreenBtn){
    fullscreenBtn.addEventListener('click', async () => {
      try{
        if (document.fullscreenElement === letterPreviewEl){
          await document.exitFullscreen();
        } else {
          await letterPreviewEl.requestFullscreen();
        }
      }catch(err){
        console.warn('Fullscreen request failed', err);
      }
    });
    document.addEventListener('fullscreenchange', () => {
      const active = document.fullscreenElement === letterPreviewEl;
      const label = active ? 'Exit fullscreen' : 'Fullscreen';
      fullscreenBtn.textContent = label;
      fullscreenBtn.title = active ? 'Exit fullscreen' : 'Enter fullscreen';
      fullscreenBtn.setAttribute('aria-label', fullscreenBtn.title);
    });
  }
  window.addEventListener('keydown', (e) => {
    if (!started || introControlsLocked || wallRevealLocked) return;
    if (e.key === 'ArrowLeft'){
      e.preventDefault();
      go(-1);
    } else if (e.key === 'ArrowRight'){
      e.preventDefault();
      go(1);
    } else if (e.key === 'Escape'){
      if (isWallPage() && wall.classList.contains('is-open')){
        setWallOpen(false);
        wallClosedByUser = true;
        openText.focus({preventScroll:true});
      }
    }
  });
  closeText.addEventListener('click', () => {
    if (!isWallPage()) return;
    clearWallRevealTimers();
    wallRevealLocked = false;
    setWallOpen(false);
    wallClosedByUser = true;
    syncButtons();
    openText.focus({preventScroll:true});
  });
  bindPress(openText, () => {
    if (!isWallPage()) return;
    clearWallRevealTimers();
    wallRevealLocked = false;
    setWallOpen(true);
    wallClosedByUser = false;
    syncButtons();
    closeText.focus({preventScroll:true});
  });
  bindPress(volIcon, () => {
    const s = ensureSlider();
    const shouldOpen = !volumeControl.classList.contains('slider-open');
    setSliderOpen(shouldOpen);
    if (shouldOpen) s.focus({preventScroll:true});
  });

  ensureSlider();
  setSliderOpen(false);
  syncMuteButton();
  setVolume0to100(loadVolume0to100());
  setHiddenState(wall, true);
  setHiddenState(openText, true);
  setHiddenState(closeText, true);
  setExpandedState(openText, false);
  try{
    installUltralinks();
  }catch(error){
    console.error('Ultralink setup failed', error);
  }
  installImageFallbacks();
  waitForCriticalAssets().finally(startCurtainIntro);
});
