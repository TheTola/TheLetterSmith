"""Desktop controls embedded in the generated Letter Smith viewer."""

DESKTOP_VIEWER_CSS = r"""
#letter-preview.desktop-viewer{
  --desktop-margin:clamp(5px,1.4vw,18px);
  --desktop-nav-gap:clamp(4px,1vw,12px);
  --desktop-nav-size:clamp(38px,6vw,64px);
  --desktop-button-height:34px;
  --control-rail:calc(var(--desktop-margin) + var(--desktop-button-height) + 10px);
  --bottom-control-rail:var(--control-rail);
  --page-side-rail:calc(var(--desktop-nav-size) + var(--desktop-nav-gap) + var(--desktop-margin));
}
.desktop-viewer :is(#prev,#next){top:50%;width:var(--desktop-nav-size);height:var(--desktop-nav-size);padding:4px}
.desktop-viewer #prev{left:calc(var(--desktop-page-left,var(--page-side-rail)) - var(--desktop-nav-size) - var(--desktop-nav-gap))}
.desktop-viewer #next{right:calc(var(--desktop-page-right,var(--page-side-rail)) - var(--desktop-nav-size) - var(--desktop-nav-gap))}
.desktop-viewer .nav-button img{width:100%;height:100%}
.desktop-viewer #viewer-actions{top:var(--desktop-margin);left:50%;transform:translateX(-50%)}
.desktop-viewer #viewer-actions-left{gap:6px}
.desktop-viewer #viewer-actions .hud-button,.desktop-viewer #desktop-zoom-controls .hud-button{
  width:auto;min-width:var(--desktop-button-height);height:var(--desktop-button-height);padding:0 10px;
  font:700 12px/1 var(--font-ui);white-space:nowrap;pointer-events:auto;
}
.desktop-viewer :is(#restart-button,#mute-button,#fullscreen-button)::before{content:none}
.desktop-viewer #fullscreen-button{position:static}
.desktop-viewer :is(#volume-control,#volume-control.slider-open){position:static;transform:none;padding:0;border:0;background:none;box-shadow:none;backdrop-filter:none}
.desktop-viewer #volume-icon{gap:5px}
.desktop-viewer #volume-icon-img{width:17px;height:17px;padding:0}
.desktop-viewer #desktop-sound-settings{
  position:absolute;top:calc(100% + 8px);left:50%;transform:translateX(-50%);
  display:flex;align-items:center;gap:12px;width:min(208px,calc(100vw - 2 * var(--desktop-margin)));
  padding:9px 10px;border:1px solid var(--hud-border);border-radius:12px;
  background:#10141e;box-shadow:var(--hud-shadow);pointer-events:auto;
}
.desktop-viewer #desktop-sound-settings[hidden]{display:none}
.desktop-viewer #desktop-sound-settings label{display:grid;flex:1;gap:9px;min-width:0;font:600 12px/1 var(--font-ui)}
.desktop-viewer #volume-control.slider-open #volume-slider{width:100%;margin:4px 0;transform:none}
.desktop-viewer #desktop-reading-controls{
  position:absolute;bottom:var(--desktop-margin);left:50%;transform:translateX(-50%);
  display:flex;align-items:center;justify-content:center;gap:7px;width:calc(100% - 2 * var(--desktop-margin));
  z-index:106;pointer-events:none;
}
.desktop-viewer #page-navigation{position:static;transform:none;min-width:0;max-width:280px;white-space:nowrap}
.desktop-viewer #desktop-zoom-controls{display:flex;flex:none;gap:4px}
.desktop-viewer #desktop-zoom-controls .hud-button:disabled{opacity:.45;cursor:default;box-shadow:none}
.desktop-viewer #open-text{position:static;transform:none;flex:none;width:var(--desktop-button-height);height:var(--desktop-button-height)}
.desktop-viewer #open-text:not(.is-visible){display:none}
.desktop-viewer #open-text img{width:100%;height:100%;padding:0}
.desktop-viewer .slide > img{-webkit-user-drag:none;user-select:none}
.desktop-viewer .slide > img.artwork-zoomed{transform:var(--desktop-zoom-transform);clip-path:var(--desktop-zoom-clip);cursor:grab;touch-action:none}
.desktop-viewer .slide > img.artwork-zoomed:active{cursor:grabbing}
.desktop-viewer .slide:has(> img.artwork-zoomed:focus-visible)::before{
  content:"";position:absolute;inset:var(--desktop-page-top) var(--desktop-page-right) var(--desktop-page-bottom) var(--desktop-page-left);
  z-index:1;border-radius:var(--page-radius);outline:2px solid var(--accent);outline-offset:2px;pointer-events:none;
}
.desktop-viewer .text-wall:not(.canvas-message){overflow-anchor:none}

#letter-preview.desktop-viewer[data-turn-effect="pixelate"] :is(#volume-control,#desktop-zoom-controls){visibility:hidden}
#letter-preview.desktop-viewer.desktop-controls-hidden :is(#viewer-actions,#desktop-reading-controls,#prev,#next,.hud-button-close){opacity:0;pointer-events:none}
#letter-preview.desktop-viewer.desktop-controls-hidden :is(#viewer-actions,#desktop-reading-controls) *{pointer-events:none}

@media (max-width:300px){
  #letter-preview.desktop-viewer{--desktop-nav-size:30px;--control-rail:calc(var(--desktop-margin) + var(--desktop-button-height) + 38px)}
  .desktop-viewer #viewer-actions .hud-button{padding:0 7px;font-size:11px}
  .desktop-viewer #viewer-actions-left{gap:4px}
  .desktop-viewer #volume-icon-img{display:none}
  .desktop-viewer #desktop-reading-controls{flex-wrap:wrap;gap:4px 7px}
  .desktop-viewer #page-navigation{flex-basis:100%;max-width:100%;justify-content:center}
}
"""

DESKTOP_VIEWER_JS = r"""
function installDesktopViewer(root, {slideshow, volumeControl, slider, muteButton, soundButton, fullscreenButton,
    openText, currentArtwork, canZoom, closeSound}){
  root.classList.add('desktop-viewer');
  const actions = root.querySelector('#viewer-actions-left');
  actions.parentElement.inert = true;
  actions.append(volumeControl, fullscreenButton);
  soundButton.append('Sound');
  soundButton.title = 'Sound';
  soundButton.setAttribute('aria-controls', 'desktop-sound-settings');
  const soundSettings = document.createElement('div');
  soundSettings.id = 'desktop-sound-settings';
  soundSettings.hidden = true;
  const volumeLabel = document.createElement('label');
  volumeLabel.append('Volume', slider);
  soundSettings.append(volumeLabel, muteButton);
  volumeControl.append(soundSettings);

  let pageNavigation = root.querySelector('#page-navigation');
  if (!pageNavigation){
    pageNavigation = document.createElement('div');
    pageNavigation.id = 'page-navigation';
    const indicator = document.createElement('span');
    indicator.id = 'page-indicator';
    indicator.setAttribute('role', 'status');
    pageNavigation.append(indicator);
  }
  pageNavigation.querySelector('#page-selector')?.remove();
  pageNavigation.removeAttribute('aria-label');

  const readingControls = document.createElement('div');
  readingControls.id = 'desktop-reading-controls';
  const zoomControls = document.createElement('div');
  zoomControls.id = 'desktop-zoom-controls';
  zoomControls.setAttribute('role', 'group');
  zoomControls.setAttribute('aria-label', 'Artwork zoom');
  const zoomOut = addZoomButton('−', 'Zoom out', () => changeZoom(zoom - .5));
  const zoomIn = addZoomButton('+', 'Zoom in', () => changeZoom(zoom + .5));
  const reset = addZoomButton('Reset', 'Reset zoom', resetZoom);
  readingControls.append(pageNavigation, zoomControls, openText);
  slideshow.append(readingControls);

  let zoom = 1;
  const maxZoom = 4;
  let zoomedArtwork = null;
  let panX = 0;
  let panY = 0;
  let drag = null;
  let controlsTimer = null;
  let reading = null;
  const directions = {ArrowLeft:[1,0], ArrowRight:[-1,0], ArrowUp:[0,1], ArrowDown:[0,-1]};

  function addZoomButton(text, label, onClick){
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'hud-button';
    button.textContent = text;
    button.title = label;
    button.setAttribute('aria-label', label);
    button.addEventListener('click', onClick);
    zoomControls.append(button);
    return button;
  }

  function updateArtwork(){
    const artwork = currentArtwork();
    const available = !!artwork?.naturalWidth && !artwork.parentElement.classList.contains('asset-failed');
    if (available){
      const left = artwork.offsetLeft;
      const top = artwork.offsetTop;
      slideshow.style.setProperty('--desktop-page-left', `${left}px`);
      slideshow.style.setProperty('--desktop-page-right', `${slideshow.clientWidth - left - artwork.offsetWidth}px`);
      slideshow.style.setProperty('--desktop-page-top', `${top}px`);
      slideshow.style.setProperty('--desktop-page-bottom', `${slideshow.clientHeight - top - artwork.offsetHeight}px`);
    } else {
      slideshow.style.removeProperty('--desktop-page-left');
      slideshow.style.removeProperty('--desktop-page-right');
    }
    if (zoomedArtwork && (artwork !== zoomedArtwork || !available)) resetZoom();
    if (zoomedArtwork) renderZoom();
    const enabled = available && canZoom();
    zoomIn.disabled = !enabled || zoom >= maxZoom;
    zoomOut.disabled = reset.disabled = !enabled || zoom === 1;
  }

  function renderZoom(){
    // Pan in artwork units so a resize keeps the same part of the image in view.
    const limit = (zoom - 1) / 2;
    panX = Math.max(-limit, Math.min(limit, panX));
    panY = Math.max(-limit, Math.min(limit, panY));
    const x = panX * zoomedArtwork.offsetWidth;
    const y = panY * zoomedArtwork.offsetHeight;
    const insets = [limit - panY, limit + panX, limit + panY, limit - panX]
      .map(value => `${100 * value / zoom}%`).join(' ');
    zoomedArtwork.style.setProperty('--desktop-zoom-transform', `translate(${x}px,${y}px) scale(${zoom})`);
    zoomedArtwork.style.setProperty('--desktop-zoom-clip', `inset(${insets} round calc(var(--page-radius) / ${zoom}))`);
  }

  function changeZoom(next){
    if (!canZoom()) return;
    if (next <= 1){ resetZoom(); updateArtwork(); return; }
    const artwork = currentArtwork();
    if (!artwork?.naturalWidth || artwork.parentElement.classList.contains('asset-failed')) return;
    next = Math.min(maxZoom, next);
    panX *= next / zoom;
    panY *= next / zoom;
    zoom = next;
    zoomedArtwork = artwork;
    artwork.tabIndex = 0;
    artwork.setAttribute('aria-keyshortcuts', 'ArrowUp ArrowDown ArrowLeft ArrowRight');
    artwork.classList.add('artwork-zoomed');
    updateArtwork();
  }

  function resetZoom(){
    const artwork = zoomedArtwork;
    if (!artwork) return;
    if (drag && artwork.hasPointerCapture(drag.id)) artwork.releasePointerCapture(drag.id);
    drag = null;
    artwork.classList.remove('artwork-zoomed');
    artwork.removeAttribute('tabindex');
    artwork.removeAttribute('aria-keyshortcuts');
    artwork.style.removeProperty('--desktop-zoom-transform');
    artwork.style.removeProperty('--desktop-zoom-clip');
    zoomedArtwork = null;
    zoom = 1;
    panX = panY = 0;
    zoomIn.disabled = !canZoom();
    zoomOut.disabled = reset.disabled = true;
  }

  function pan(dx, dy){
    panX += dx / Math.max(1, zoomedArtwork.offsetWidth);
    panY += dy / Math.max(1, zoomedArtwork.offsetHeight);
    renderZoom();
  }

  slideshow.addEventListener('pointerdown', event => {
    if (event.target !== zoomedArtwork || event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    zoomedArtwork.focus({preventScroll:true});
    zoomedArtwork.setPointerCapture(event.pointerId);
    drag = {id:event.pointerId, x:event.clientX, y:event.clientY};
  });
  slideshow.addEventListener('pointermove', event => {
    if (event.pointerId !== drag?.id) return;
    pan(event.clientX - drag.x, event.clientY - drag.y);
    drag.x = event.clientX;
    drag.y = event.clientY;
  });
  slideshow.addEventListener('lostpointercapture', event => {
    if (event.pointerId === drag?.id) drag = null;
  });
  slideshow.addEventListener('keydown', event => {
    if (event.target !== zoomedArtwork || event.altKey || event.ctrlKey || event.metaKey) return;
    const direction = directions[event.key];
    if (!Array.isArray(direction)) return;
    event.preventDefault();
    event.stopPropagation();
    pan(direction[0] * 40, direction[1] * 40);
  });

  const focusControls = '#viewer-actions, #desktop-reading-controls, #prev, #next, .hud-button-close';
  function revealControls(){
    clearTimeout(controlsTimer);
    root.classList.remove('desktop-controls-hidden');
    if (document.fullscreenElement !== root) return;
    controlsTimer = setTimeout(() => {
      const focus = document.activeElement;
      if (drag || (focus?.matches(':focus-visible') && focus.closest(focusControls))) return;
      root.classList.add('desktop-controls-hidden');
    }, 3000);
  }
  root.addEventListener('pointermove', revealControls, {passive:true});
  root.addEventListener('lostpointercapture', revealControls);
  root.addEventListener('focusin', revealControls);
  root.addEventListener('focusout', revealControls);
  document.addEventListener('fullscreenchange', revealControls);
  document.addEventListener('pointerdown', event => {
    if (!soundSettings.hidden && !volumeControl.contains(event.target)) closeSound();
  }, {passive:true});
  volumeControl.addEventListener('focusout', event => {
    if (!soundSettings.hidden && event.relatedTarget && !volumeControl.contains(event.relatedTarget)) closeSound();
  });
  document.addEventListener('keydown', event => {
    revealControls();
    if (event.key !== 'Escape' || soundSettings.hidden) return;
    event.preventDefault();
    event.stopPropagation();
    closeSound();
    soundButton.focus({preventScroll:true});
  }, true);

  function rememberPosition(){
    const {wall, content, width, height, restoredTop} = reading;
    const top = wall.scrollTop;
    if (width !== slideshow.clientWidth || height !== slideshow.clientHeight || top === restoredTop) return;
    reading.position = {progress:top / Math.max(1, wall.scrollHeight - wall.clientHeight)};
    if (!top) return;

    const frame = wall.getBoundingClientRect();
    const text = content.getBoundingClientRect();
    const x = (Math.max(frame.left, text.left) + Math.min(frame.right, text.right)) / 2;
    for (const offset of [4, 24]){
      const y = Math.min(frame.top + offset, frame.bottom - 1);
      const caret = document.caretPositionFromPoint?.(x, y);
      const fallback = caret ? null : document.caretRangeFromPoint?.(x, y);
      const node = caret?.offsetNode ?? fallback?.startContainer;
      if (node?.nodeType !== Node.TEXT_NODE || !node.length || !content.contains(node)) continue;
      const anchor = document.createRange();
      const start = Math.min(caret?.offset ?? fallback.startOffset, node.length - 1);
      anchor.setStart(node, start);
      anchor.setEnd(node, start + 1);
      const scale = frame.height / wall.offsetHeight || 1;
      reading.position.anchor = anchor;
      reading.position.offset = (anchor.getBoundingClientRect().top - frame.top) / scale;
      return;
    }
  }

  function trackMessage(wall){
    if (reading?.wall === wall) return;
    reading?.wall.removeEventListener('scroll', rememberPosition);
    reading = null;
    // An authored canvas scrolls in fixed logical pixels even as it scales.
    if (!wall || wall.classList.contains('canvas-message')) return;
    const content = wall.querySelector('.text-wall-content');
    reading = {wall, content, width:slideshow.clientWidth, height:slideshow.clientHeight};
    rememberPosition();
    wall.addEventListener('scroll', rememberPosition, {passive:true});
  }

  function restorePosition(){
    if (!reading) return;
    const width = slideshow.clientWidth;
    const height = slideshow.clientHeight;
    const {wall, position} = reading;
    if (width === reading.width && height === reading.height) return;
    reading.width = width;
    reading.height = height;
    if (position?.anchor?.startContainer.isConnected){
      const frame = wall.getBoundingClientRect();
      const scale = frame.height / wall.offsetHeight || 1;
      wall.scrollTop += (position.anchor.getBoundingClientRect().top - frame.top) / scale - position.offset;
    } else if (position){
      wall.scrollTop = position.progress * (wall.scrollHeight - wall.clientHeight);
    }
    reading.restoredTop = wall.scrollTop;
  }

  return {
    pageIndicator:pageNavigation.querySelector('#page-indicator'),
    start(){ actions.parentElement.inert = false; },
    setSoundOpen(open){ soundSettings.hidden = !open; },
    updateArtwork,
    resetZoom,
    trackMessage,
    restorePosition,
  };
}
"""
