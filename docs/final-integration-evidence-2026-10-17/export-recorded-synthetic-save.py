"""Export the synthetic native-edited save through the production Forge path."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

code = Path.cwd()
profile = Path(os.environ['LETTER_SMITH_PROFILE_ROOT'])
from project_paths import ApplicationPaths, configure_application_paths
paths = configure_application_paths(ApplicationPaths.for_runtime(code))
paths.initialize()
os.chdir(paths.workspace_root)
from PySide6 import QtWidgets
app = QtWidgets.QApplication([])
from project_repository import ProjectRepository
from project_session import get_project_session
from forge_render import capture_render, export_render, _plain
seed = json.loads(Path('C:/Temp/ls10/packaged-profile/expected.json').read_text(encoding='utf-8'))
stored = ProjectRepository(Path(seed['saved']) / '.lettersmith-project').load()
session = get_project_session(paths.workspace_root)
session.replace(stored.project, asset_root=stored.asset_root)
render = capture_render(session, 'display')
output = Path('C:/Temp/ls10/delivery-20d0/Portable presentation 雪.zip')
output.parent.mkdir(parents=True, exist_ok=True)
export_render(session, output, render_input=render)
destination = output.parent / 'Portable presentation 雪'
destination.mkdir(exist_ok=True)
entries = []
with zipfile.ZipFile(output) as archive:
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        assert target.is_relative_to(destination.resolve()), member.filename
        data = archive.read(member)
        assert b'PRIVATE-PROMPT-' not in data, member.filename
        assert '.lettersmith-project' not in member.filename
        entries.append({'name': member.filename, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    archive.extractall(destination)
result = {
    'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=code, text=True).strip(),
    'saved_generation': stored.revision, 'project_id': stored.project.project_id,
    'source_asset_hashes': {name: hashlib.sha256((stored.asset_root / name).read_bytes()).hexdigest() for name in seed['asset_hashes']},
    'zip': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
    'snapshot': _plain(render.snapshot), 'entries': entries,
    'privacy_checks': ['Exact prompt sentinels absent from all ZIP members', 'Editable repository absent', 'All archive members contained'],
}
(output.parent / 'portable-export.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: result[key] for key in ('source_revision', 'saved_generation', 'zip', 'sha256')}, ensure_ascii=False))
