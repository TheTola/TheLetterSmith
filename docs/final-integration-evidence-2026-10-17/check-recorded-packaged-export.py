import hashlib
import json
import re
from pathlib import Path
import zipfile

root = Path('C:/Temp/ls10/delivery-20d0')
output = root / 'Packaged UI export 雪.zip'
target = root / 'Packaged UI export 雪'
target.mkdir(exist_ok=True)
def entries(path):
    with zipfile.ZipFile(path) as archive:
        return {entry.filename: archive.read(entry) for entry in archive.infolist() if not entry.is_dir()}
actual = entries(output)
expected = entries(root / 'Portable presentation 雪.zip')
assert set(actual) == set(expected)
def normalized_html(data):
    nonces = re.findall(rb'(?:nonce-|nonce=")([A-Za-z0-9_-]+)', data)
    assert len(nonces) == 3 and len(set(nonces)) == 1
    return data.replace(nonces[0], b'COMPARISON-NONCE')

for bundle in (actual, expected):
    manifest = json.loads(bundle['letter/lettersmith-display.json'])
    for name, digest in manifest['files'].items():
        assert hashlib.sha256(bundle['letter/' + name]).hexdigest() == digest, name
for name, data in actual.items():
    assert (target / name).resolve().is_relative_to(target.resolve())
    assert b'PRIVATE-PROMPT-' not in data
    assert '.lettersmith-project' not in name
    if name == 'letter/index.html':
        assert normalized_html(data) == normalized_html(expected[name])
    elif name == 'letter/lettersmith-display.json':
        left, right = json.loads(data), json.loads(expected[name])
        # The only derived difference is the hash of the per-export CSP nonce.
        left['files']['index.html'] = right['files']['index.html'] = 'checked-above'
        assert left == right
    else:
        assert data == expected[name], name
with zipfile.ZipFile(output) as archive:
    archive.extractall(target)
result = {'source_revision':'20d0be7abc48eac95c1fbc00e4b6161b8b5baeda',
          'method':'Native packaged Forge Export > ZIP Package > save to Unicode path',
          'path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
          'file_count':len(actual),
          'checks':['all resource bytes and semantic HTML match the production-service export; only fresh CSP nonce and its HTML digest differ',
                    'every display-manifest file digest verified',
                    'private prompt sentinels absent','editable repository absent','contained extraction']}
(root/'packaged-export-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
