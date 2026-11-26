"""Authored transition choices and the viewer's alternate Canvas effects."""

DEFAULT_PAGE_TRANSITION = "classic"
PAGE_TRANSITIONS = {
    DEFAULT_PAGE_TRANSITION: "Classic page flip",
    "shuffle_deal": "Shuffle and deal",
    "pixelate": "Black-screen pixelation",
    "envelope": "Envelope opening",
    "curtain": "Curtain close and reopen",
}
FLIP_PREFIX = "flip"
FLIP_COUNT = 10
TRANSITION_SOUNDS = {
    DEFAULT_PAGE_TRANSITION: tuple(f"transitions/classic/{FLIP_PREFIX}{index}.mp3" for index in range(1, FLIP_COUNT + 1)),
    "shuffle_deal": ("transitions/shuffle_deal/deal.mp3", "transitions/shuffle_deal/return.mp3"),
    "pixelate": ("transitions/pixelate/dissolve-reveal.mp3",),
    "envelope": ("transitions/envelope/open-letter.mp3", "transitions/envelope/return-letter.mp3"),
    "curtain": ("transitions/curtain/close-open.mp3",),
}


def normalize_page_transition(value: object) -> str:
    return value if isinstance(value, str) and value in PAGE_TRANSITIONS else DEFAULT_PAGE_TRANSITION


PAGE_EFFECTS_JS = r"""
function createPageEffect(name, context, curtains){
  const clamp = value => Math.min(1, Math.max(0, value));
  const mix = (start, end, amount) => start + (end - start) * amount;
  const ease = value => {
    const t = clamp(value);
    return t * t * (3 - 2 * t);
  };

  function artwork(art, ratio, {x = 0, y = 0, angle = 0, scale = 1, opacity = 1} = {}){
    if (opacity <= 0) return;
    context.save();
    context.globalAlpha = opacity;
    context.translate(art.x + art.w / 2 + x, art.y + art.h / 2 + y);
    context.rotate(angle);
    context.scale(scale, scale);
    context.shadowColor = 'rgba(0,0,0,.48)';
    context.shadowBlur = 23 * ratio;
    context.shadowOffsetY = 10 * ratio;
    context.drawImage(art.texture, -art.w / 2, -art.h / 2, art.w, art.h);
    context.restore();
  }

  function shape(points, fill){
    context.beginPath();
    context.moveTo(...points[0]);
    for (const point of points.slice(1)) context.lineTo(...point);
    context.closePath();
    context.fillStyle = fill;
    context.fill();
  }

  function paper(top, height, light, dark){
    const gradient = context.createLinearGradient(0, top, 0, top + height);
    gradient.addColorStop(0, light);
    gradient.addColorStop(1, dark);
    return gradient;
  }

  function shuffle({progress, width, height, leaf, page, pixelRatio}){
    const riffle = Math.sin(Math.PI * clamp(progress / .54));
    const flick = ease((progress - .19) / .43);
    const deal = ease((progress - .39) / .57);
    const settling = Math.sin(Math.PI * deal);
    for (let card = 3; card >= 1; card -= 1){
      const side = card % 2 ? 1 : -1;
      const x = side * riffle * (leaf.w * .08 + card * 5);
      context.save();
      context.globalAlpha = clamp(riffle * 2.4);
      context.translate(leaf.x + leaf.w / 2 + x, leaf.y + leaf.h / 2 + card * (1 + riffle * 3));
      context.rotate(side * riffle * (.027 + card * .034));
      context.beginPath();
      context.roundRect(-leaf.w * .48, -leaf.h * .48, leaf.w * .96, leaf.h * .96, leaf.radius);
      context.fillStyle = card % 2 ? '#544766' : '#253b52';
      context.fill();
      context.lineWidth = 1.4;
      context.strokeStyle = '#cdbfac';
      context.stroke();
      context.restore();
    }
    artwork(leaf, pixelRatio, {
      x:riffle * leaf.w * .09 - flick * (width + leaf.w) * .70,
      y:-Math.sin(Math.PI * flick) * height * .12,
      angle:riffle * .048 - flick * .53,
      opacity:1 - ease((flick - .70) / .27),
    });
    artwork(page, pixelRatio, {
      x:(1 - deal) * (width + page.w) * .64 - settling * page.w * .075,
      y:(1 - deal) * height * .23 - settling * page.h * .16,
      angle:(1 - deal) * .44 - settling * .07,
      scale:mix(.80, 1, deal),
      opacity:ease((progress - .39) / .13),
    });
  }

  function pixelation(){
    const cells = document.createElement('canvas');
    const pixels = cells.getContext('2d');
    if (!pixels) return null;
    return ({progress, width, height, leaf, page}) => {
      const artwork = progress < .5 ? leaf : page;
      const dissolve = ease(Math.min(progress, 1 - progress) / .43);
      context.save();
      context.globalAlpha = dissolve;
      context.fillStyle = '#000';
      context.fillRect(0, 0, width, height);
      if (dissolve < 1){
        const size = mix(1, Math.min(124, Math.max(artwork.w, artwork.h) * .34), dissolve * dissolve);
        const columns = Math.max(1, Math.ceil(artwork.w / size));
        const rows = Math.max(1, Math.ceil(artwork.h / size));
        if (cells.width !== columns || cells.height !== rows){
          cells.width = columns;
          cells.height = rows;
        } else pixels.clearRect(0, 0, columns, rows);
        pixels.drawImage(artwork.texture, 0, 0, columns, rows);
        const dropout = ease((dissolve - .56) / .44);
        if (dropout > 0){
          pixels.fillStyle = '#000';
          for (let y = 0; y < rows; y += 1){
            for (let x = 0; x < columns; x += 1){
              const seed = Math.imul(x + 1, 73856093) ^ Math.imul(y + 1, 19349663);
              const sample = (Math.imul(seed ^ (seed >>> 15), 2246822519) >>> 0) / 4294967296;
              if (sample < dropout) pixels.fillRect(x, y, 1, 1);
            }
          }
        }
        context.imageSmoothingEnabled = false;
        context.globalAlpha = 1 - ease((dissolve - .68) / .32);
        context.drawImage(cells, artwork.x, artwork.y, artwork.w, artwork.h);
      }
      context.restore();
    };
  }

  function envelope({progress, width, height, leaf, page, pixelRatio}){
    const leave = ease(progress / .24);
    artwork(leaf, pixelRatio, {y:height * .065 * leave, scale:1 - .09 * leave, opacity:1 - leave});

    const arrive = ease((progress - .06) / .25);
    const open = ease((progress - .26) / .23);
    const pull = ease((progress - .44) / .50);
    const fall = ease((progress - .74) / .25);
    const w = Math.min(width - 16, Math.max(page.w * 1.19, Math.min(width * .69, 410)));
    const h = Math.min(w * .55, height * .30);
    const center = page.x + page.w / 2;
    const x = Math.min(width - w - 8, Math.max(8, center - w / 2));
    const y = Math.min(height - h - 10, page.y + page.h * .54) + h * (.53 * (1 - arrive) + 1.12 * fall);
    const middle = x + w / 2;
    const opacity = arrive * (1 - fall);
    const flapEdge = y + h * .64 * Math.cos(Math.PI * open);
    const flap = [[x, y], [x + w, y], [middle, flapEdge]];

    if (opacity > 0){
      context.save();
      context.globalAlpha = opacity;
      context.shadowColor = 'rgba(0,0,0,.47)';
      context.shadowBlur = 24 * pixelRatio;
      context.shadowOffsetY = 9 * pixelRatio;
      context.beginPath();
      context.roundRect(x, y, w, h, Math.min(7, h * .06));
      context.fillStyle = paper(y, h, '#d8bb95', '#aa7852');
      context.fill();
      context.shadowColor = 'transparent';
      if (open >= .5) shape(flap, paper(flapEdge, Math.max(1, y - flapEdge), '#e6c9a4', '#ae7e5d'));
      context.restore();
    }

    if (pull > 0){
      const startScale = Math.min(.72, w * .82 / page.w, h * .84 / page.h);
      const startY = y + page.h * startScale / 2 + h * .025;
      const targetY = page.y + page.h / 2;
      context.save();
      context.beginPath();
      context.rect(0, 0, width, opacity > 0 ? Math.min(height, y) : height);
      context.clip();
      artwork(page, pixelRatio, {
        x:(middle - center) * (1 - pull),
        y:mix(startY, targetY, pull) - targetY - Math.sin(Math.PI * pull) * h * .62,
        scale:mix(startScale, 1, ease((progress - .51) / .44)),
      });
      context.restore();
    }

    if (opacity > 0){
      context.save();
      context.globalAlpha = opacity;
      shape([[x, y], [middle, y + h * .69], [x, y + h]], paper(y, h, '#ebd0ab', '#c49b72'));
      shape([[x + w, y], [middle, y + h * .69], [x + w, y + h]], paper(y, h, '#e2c39c', '#bd8e65'));
      shape([[x, y + h], [middle, y + h * .43], [x + w, y + h]], paper(y, h, '#e5c399', '#c49368'));
      if (open < .5) shape(flap, paper(y, h * .66, '#f0d6b2', '#c59c75'));
      context.restore();
    }
  }

  function curtain({progress, width, height, leaf, page, pixelRatio}){
    const amount = ease(Math.min(progress, 1 - progress) / .43);
    artwork(progress < .5 ? leaf : page, pixelRatio);
    const half = width / 2;
    for (let side = 0; side < 2; side += 1){
      const image = curtains[side];
      context.save();
      context.translate((side ? 1 : -1) * half * (1 - amount), 0);
      context.beginPath();
      context.rect(side ? half - .5 : 0, 0, half + .5, height);
      context.clip();
      const folds = context.createLinearGradient(side ? half : 0, 0, side ? width : half, 0);
      folds.addColorStop(0, '#171019');
      folds.addColorStop(.33, '#3b2335');
      folds.addColorStop(.64, '#261723');
      folds.addColorStop(1, '#140d17');
      context.fillStyle = folds;
      context.fillRect(0, 0, width, height);
      if (image?.naturalWidth){
        const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight);
        const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
        context.drawImage(image, (width - w) / 2, (height - h) / 2, w, h);
      }
      context.restore();
    }
  }

  switch (name){
    case 'shuffle_deal': return shuffle;
    case 'pixelate': return pixelation();
    case 'envelope': return envelope;
    case 'curtain': return curtain;
    default: return null;
  }
}
"""
