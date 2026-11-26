"""Canvas renderer embedded in the Letter Smith viewer and portable exports."""

PAGE_TURN_JS = r"""
function createPageTurn(canvas, stage, leafImage, pageImage, isCover, transition = 'classic', curtains = []){
  if (!leafImage?.naturalWidth || !pageImage?.naturalWidth) return null;
  const context = canvas.getContext('2d');
  if (!context) return null;
  const effect = transition === 'classic' ? null : createPageEffect(transition, context, curtains);
  if (transition !== 'classic' && !effect) return null;

  const columns = 20;
  const rows = 4;
  const thickness = isCover ? .012 : .006;
  const binding = isCover ? '#493347' : '#ac997e';
  const limit = (value, min, max) => Math.min(Math.max(value, min), max);
  const mix = (from, to, amount) => from + (to - from) * amount;
  const ease = value => {
    const t = limit(value, 0, 1);
    return t * t * (3 - 2 * t);
  };
  let width = 0, height = 0, pixelRatio = 1;
  let leaf, page, lining;

  function capture(image, viewport){
    const rect = image.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2 || !image.naturalWidth) return null;
    const style = getComputedStyle(image);
    const [x, y] = style.objectPosition.split(' ').map(value => parseFloat(value) / 100);
    const fit = style.objectFit === 'cover' ? Math.max : Math.min;
    const imageScale = fit(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
    const imageWidth = image.naturalWidth * imageScale;
    const imageHeight = image.naturalHeight * imageScale;
    const radius = parseFloat(style.borderTopLeftRadius) || 0;
    const resolution = Math.min(pixelRatio, 1600 / Math.max(rect.width, rect.height));
    const texture = document.createElement('canvas');
    texture.width = Math.max(1, Math.round(rect.width * resolution));
    texture.height = Math.max(1, Math.round(rect.height * resolution));
    const paint = texture.getContext('2d');
    if (!paint) return null;
    paint.scale(texture.width / rect.width, texture.height / rect.height);
    paint.beginPath();
    paint.roundRect(0, 0, rect.width, rect.height, radius);
    paint.clip();
    paint.drawImage(image, (rect.width - imageWidth) * (Number.isFinite(x) ? x : .5),
      (rect.height - imageHeight) * (Number.isFinite(y) ? y : .5), imageWidth, imageHeight);
    return { texture, radius, x:rect.left - viewport.left, y:rect.top - viewport.top, w:rect.width, h:rect.height };
  }

  function captureLining(){
    const texture = document.createElement('canvas');
    texture.width = 240;
    texture.height = 360;
    const paint = texture.getContext('2d');
    if (!paint) return null;
    const wash = paint.createRadialGradient(110, 105, 12, 120, 145, 265);
    wash.addColorStop(0, isCover ? '#563c50' : '#f4e8cf');
    wash.addColorStop(1, isCover ? '#291d30' : '#cfba9a');
    paint.beginPath();
    paint.roundRect(0, 0, 240, 360, Math.min(14, 240 * leaf.radius / leaf.w));
    paint.clip();
    paint.fillStyle = wash;
    paint.fillRect(0, 0, 240, 360);
    if (isCover){
      paint.strokeStyle = 'rgba(192,155,108,.35)';
      paint.strokeRect(11, 11, 218, 338);
    }
    return texture;
  }

  function measure(){
    const viewport = stage.getBoundingClientRect();
    width = viewport.width;
    height = viewport.height;
    pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    leaf = capture(leafImage, viewport);
    page = capture(pageImage, viewport);
    if (!leaf || !page) return false;
    if (!effect){
      lining = captureLining();
      if (!lining) return false;
    }
    canvas.width = Math.ceil(width * pixelRatio);
    canvas.height = Math.ceil(height * pixelRatio);
    return true;
  }

  function layout(progress){
    // Keep each page's aspect ratio, reframing only to make room for the spread.
    const spread = ease(Math.min(progress / .23, (1 - progress) / .23));
    const align = ease(progress / .29);
    const tuck = ease((progress - .49) / .23);
    const closedScale = Math.min(1, leaf.w / page.w, leaf.h / page.h);
    const tuckedScale = Math.min(1, page.w / leaf.w, page.h / leaf.h);
    const leafScale = mix(1, tuckedScale, tuck);
    const pageScale = mix(closedScale, 1, align);
    const leafWidth = leaf.w * leafScale, leafHeight = leaf.h * leafScale;
    const pageWidth = page.w * pageScale, pageHeight = page.h * pageScale;
    const tallest = Math.max(leafHeight, pageHeight);
    const margin = 8;
    const fit = Math.min(1, (width - 2 * margin) / (1.12 * (leafWidth + pageWidth)),
      (height - 2 * margin) / (1.29 * tallest));
    const zoom = mix(1, fit, spread);
    const spine = mix(leaf.x, page.x, align);
    const center = mix(leaf.y + leaf.h / 2, page.y + page.h / 2, align);
    const x = mix(spine, limit(spine, margin + leafWidth * fit * 1.12,
      width - margin - pageWidth * fit * 1.12), spread);
    const y = mix(center, limit(center, margin + tallest * fit * .645,
      height - margin - tallest * fit * .645), spread);
    return {
      leafBox:{x, y:y - leafHeight * zoom / 2, w:leafWidth * zoom, h:leafHeight * zoom},
      pageBox:{x, y:y - pageHeight * zoom / 2, w:pageWidth * zoom, h:pageHeight * zoom},
      zoom:zoom * pageScale,
    };
  }

  function project(box, x, y, z, u, v){
    const perspective = 1 / (1 - z / 4.9);
    return {x:box.x + (.5 + (x - .5) * perspective) * box.w,
      y:box.y + (.5 + (y - .5) * perspective) * box.h, z, u, v};
  }

  function polygon(points, overlap = 0){
    const cx = points.reduce((sum, point) => sum + point.x, 0) / points.length;
    const cy = points.reduce((sum, point) => sum + point.y, 0) / points.length;
    context.beginPath();
    points.forEach((point, index) => {
      const x = point.x + overlap * Math.sign(point.x - cx);
      const y = point.y + overlap * Math.sign(point.y - cy);
      if (index) context.lineTo(x, y);
      else context.moveTo(x, y);
    });
    context.closePath();
  }

  function winding(a, b, c){
    return (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y);
  }

  function paintFace(face){
    const [a, b, c] = face.points;
    if (!face.texture){
      polygon(face.points);
      context.fillStyle = binding;
      context.fill();
      return;
    }
    const u = face.reverse ? 1 - a.u : a.u;
    const du = (b.u - a.u) * (face.reverse ? -1 : 1);
    const eu = (c.u - a.u) * (face.reverse ? -1 : 1);
    const dv = b.v - a.v, ev = c.v - a.v;
    const determinant = du * ev - eu * dv;
    const xx = ((b.x - a.x) * ev - (c.x - a.x) * dv) / determinant;
    const xy = ((b.y - a.y) * ev - (c.y - a.y) * dv) / determinant;
    const yx = ((c.x - a.x) * du - (b.x - a.x) * eu) / determinant;
    const yy = ((c.y - a.y) * du - (b.y - a.y) * eu) / determinant;
    context.save();
    // Overlap internal triangle edges by a fraction of a pixel to prevent seams.
    polygon(face.points, .45);
    context.clip();
    context.transform(xx, xy, yx, yy, a.x - u * xx - a.v * yx, a.y - u * xy - a.v * yy);
    context.filter = face.light;
    context.drawImage(face.texture, 0, 0, 1, 1);
    context.restore();
  }

  function mesh(box, radians){
    const swing = Math.sin(radians);
    const hingeX = -.055 * Math.sin(radians / 2);
    const hingeZ = .011 - .065 * (1 - Math.cos(radians / 2));
    const grid = Array.from({length:rows + 1}, (_, r) => {
      const v = r / rows;
      const rootAngle = radians + .18 * swing * (.55 - v);
      const bend = (swing < 0 ? .38 : .69) * Math.abs(swing) * (1.1 - .45 * v);
      return Array.from({length:columns + 1}, (_, i) => {
        const u = i / columns;
        const tangent = rootAngle - bend * u;
        const x = hingeX + (bend > .00001 ? (Math.sin(rootAngle) - Math.sin(tangent)) / bend : u * Math.cos(rootAngle));
        const z = hingeZ + (bend > .00001 ? (Math.cos(tangent) - Math.cos(rootAngle)) / bend : u * Math.sin(rootAngle));
        const y = v - .016 * swing * u * u * (1 - v);
        const nx = -Math.sin(tangent), nz = Math.cos(tangent);
        const dx = nx * thickness / 2, dz = nz * thickness / 2;
        return {x, y, z, light:-.52 * nx + .85 * nz,
          front:project(box, x + dx, y, z + dz, u, v), back:project(box, x - dx, y, z - dz, u, v)};
      });
    });
    const faces = [];
    function add(points, texture, brightness, reverse = false){
      for (const [a, b, c] of [[points[0], points[1], points[2]], [points[0], points[2], points[3]]]){
        const area = winding(a, b, c);
        if (Math.abs(area) < .12 || (texture && (reverse ? area >= 0 : area <= 0))) continue;
        faces.push({points:[a,b,c], texture, reverse, depth:(a.z + b.z + c.z) / 3,
          light:brightness < .995 ? `brightness(${brightness.toFixed(2)})` : 'none'});
      }
    }
    function rim(a, b){ add([a.front, b.front, b.back, a.back], null, 1); }
    for (let r = 0; r < rows; r += 1){
      for (let c = 0; c < columns; c += 1){
        const quad = [grid[r][c], grid[r][c + 1], grid[r + 1][c + 1], grid[r + 1][c]];
        const light = quad.reduce((sum, point) => sum + point.light, 0) / 4;
        add(quad.map(point => point.front), leaf.texture, Math.min(1, .75 + .30 * light));
        add(quad.map(point => point.back), lining, Math.min(1, .8 - .22 * light), true);
      }
      rim(grid[r][0], grid[r + 1][0]);
      rim(grid[r][columns], grid[r + 1][columns]);
    }
    for (let c = 0; c < columns; c += 1){
      rim(grid[0][c], grid[0][c + 1]);
      rim(grid[rows][c], grid[rows][c + 1]);
    }
    faces.sort((a, b) => a.depth - b.depth);
    return {grid, faces};
  }

  function paintStack(box, radius, progress, texture, grid){
    const depth = Math.sin(Math.PI * progress);
    const edge = Math.min(10, box.w * .038) * depth;
    context.save();
    context.shadowColor = 'rgba(0,0,0,.45)';
    context.shadowBlur = 25 * pixelRatio;
    context.shadowOffsetY = 10 * pixelRatio;
    if (grid){
      context.globalAlpha = Math.min(1, depth * 4);
      context.fillStyle = binding;
      context.beginPath();
      context.roundRect(box.x - edge, box.y + edge / 3, box.w + edge * 1.45, box.h + edge / 2, radius);
      context.fill();
      const first = grid[0][0].front, last = grid[rows][0].front;
      const left = Math.min(box.x - edge, first.x - 1);
      const right = box.x + Math.max(2, box.w * .025);
      const top = Math.max(box.y, first.y);
      const bottom = Math.min(box.y + box.h, last.y);
      const spine = context.createLinearGradient(left, 0, right, 0);
      spine.addColorStop(0, binding);
      spine.addColorStop(.36, isCover ? '#72576c' : '#c6b497');
      spine.addColorStop(1, binding);
      context.shadowColor = 'transparent';
      context.fillStyle = spine;
      context.fillRect(left, top, right - left, bottom - top);
    }
    context.globalAlpha = 1;
    context.shadowColor = 'rgba(0,0,0,.45)';
    context.drawImage(texture, box.x, box.y, box.w, box.h);
    context.restore();
  }

  function paintShadow(grid, leafBox, pageBox, radius, radians){
    if (radians <= 0 || radians >= Math.PI) return;
    const edge = [...grid[0], ...grid[rows].slice().reverse()];
    const cast = edge.map(point => {
      const lift = Math.max(0, point.z);
      return project(leafBox, point.x + .27 * lift, point.y + .08 * lift, 0);
    });
    context.save();
    context.beginPath();
    context.roundRect(pageBox.x, pageBox.y, pageBox.w, pageBox.h, radius);
    context.clip();
    context.globalAlpha = .23 * Math.sin(radians);
    context.filter = `blur(${Math.max(2, leafBox.w * .045) * pixelRatio}px)`;
    polygon(cast);
    context.fillStyle = '#1d1420';
    context.fill();
    context.restore();
  }

  if (!measure()) return null;
  return progress => {
    if (context.isContextLost?.()) return false;
    const viewport = stage.getBoundingClientRect();
    if ((viewport.width !== width || viewport.height !== height) && !measure()) return false;
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);
    if (progress === 0 || progress === 1){
      const artwork = progress === 0 ? leaf : page;
      paintStack(artwork, artwork.radius, 0, artwork.texture);
      return true;
    }
    if (effect){
      effect({progress, width, height, leaf, page, pixelRatio});
      return true;
    }
    const {leafBox, pageBox, zoom} = layout(progress);
    const radius = page.radius * zoom;
    const radians = progress * 2 * Math.PI;
    const {grid, faces} = mesh(leafBox, radians);
    const foreground = faces.findIndex(face => face.depth >= 0);
    const behindCount = foreground < 0 ? faces.length : foreground;
    if (behindCount){
      // The stack occludes the cover even when the artwork itself has alpha.
      context.save();
      context.beginPath();
      context.rect(0, 0, width, height);
      context.roundRect(pageBox.x, pageBox.y, pageBox.w, pageBox.h, radius);
      context.clip('evenodd');
      for (let i = 0; i < behindCount; i += 1) paintFace(faces[i]);
      context.restore();
    }
    paintStack(pageBox, radius, progress, page.texture, grid);
    paintShadow(grid, leafBox, pageBox, radius, radians);
    for (let i = behindCount; i < faces.length; i += 1) paintFace(faces[i]);
    return true;
  };
}
"""
