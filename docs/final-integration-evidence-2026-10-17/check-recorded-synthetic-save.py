"""Read-only comparison of this task's synthetic native acceptance save."""
import hashlib
import json
from pathlib import Path
from project_repository import ProjectRepository

profile = Path('C:/Temp/ls10/packaged-profile')
seed = json.loads((profile / 'expected.json').read_text(encoding='utf-8'))
stored = ProjectRepository(Path(seed['saved']) / '.lettersmith-project').load()
assert stored.revision == '7aa7a4193ddb4525'
assert stored.project.project_id == seed['expected']['project_id']
assert len(stored.project.pages) == 5
for index, page in enumerate(stored.project.pages):
    previous = seed['expected']['pages'][index]
    assert page['id'] == previous['id']
    assert page['type'] == previous['type']
    assert page['name'] == previous['name']
    expected_prompt = previous['prompt']['text']
    if index == 3:
        expected_prompt += ' PACKAGED PROMPT FINAL café 雪\n  '
        assert ' PACKAGE B FINAL — 雪' in page['message']['html']
    else:
        assert page['message'] == previous['message']
    assert page['prompt']['text'] == expected_prompt
for name, digest in seed['asset_hashes'].items():
    assert hashlib.sha256((stored.asset_root / name).read_bytes()).hexdigest() == digest
result = {
    'package_source_revision': '30b00ae5287e798f6c077246a56002601b6acf5d',
    'saved_generation': stored.revision,
    'checks': ['original saved generation unchanged after native New Project, Discard, restart and Open',
               'all five identities, names and types retained', 'both messages independent',
               'all exact prompts including final newline and two spaces retained',
               'all synthetic source hashes unchanged'],
}
Path('C:/Temp/ls10/final-preservation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
