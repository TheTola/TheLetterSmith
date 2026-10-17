"""Synthetic final-acceptance fixture; run only with a new explicit profile."""
import hashlib, json, os, sys, wave
from pathlib import Path
code = Path(sys.argv[1]).resolve()
profile = Path(sys.argv[2]).resolve()
resources = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else code
if profile.exists():
    raise SystemExit('Use a new profile; existing data is never overwritten.')
profile.mkdir(parents=True)
os.environ['LETTER_SMITH_PROFILE_ROOT'] = str(profile)
os.environ['TEMP'] = os.environ['TMP'] = str(profile)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path[:0] = [str(code), str(code/'test')]
from project_paths import ApplicationPaths, configure_application_paths
paths = configure_application_paths(ApplicationPaths.for_runtime(resources))
paths.initialize(); os.chdir(paths.workspace_root)
from PySide6 import QtWidgets
app = QtWidgets.QApplication([])
from PIL import Image, ImageDraw
from project_model import create_project
from project_session import get_project_session
from project_consumers import project_legacy_views
from project_state import ProjectStateController
from project_save import ProjectSaveService
from settings_store import SettingsStore
from test_project_model import canvas_layout
root = paths.workspace_root
project = create_project(['cover','letter','illustration','letter','end_page'])
project.update_project_data('metadata', {'title':'Synthetic journey — five independent pages', 'recipient_title':'Synthetic journey — five independent pages', 'recipient_name':'Synthetic Recipient'})
colors = ['#48384b','#254762','#75664e','#674331','#243648']
for index, page in enumerate(project.pages):
    name = ['Opening','Letter A','Animated illustration','Letter B','Ending'][index]
    relative = f'background-{index}.gif' if index == 2 else f'background-{index}.png'
    frames=[]
    for frame in range(3 if index == 2 else 1):
        im=Image.new('RGB',(400,600),colors[index]); draw=ImageDraw.Draw(im)
        draw.rectangle((10+frame*50,25,90+frame*50,75),fill=['#b8cbd5','#d8a871','#8facad'][frame])
        draw.text((20,550),name,fill='white'); frames.append(im)
    if index==2: frames[0].save(root/relative,save_all=True,append_images=frames[1:],duration=[160,240,320],loop=0)
    else: frames[0].save(root/relative)
    project.rename_page(page['id'],name)
    project.update_artwork(page['id'],{'asset':relative,'animation':{'playback_mode':'ping_pong','speed_percent':100,'play_count':2} if index==2 else {}})
    project.update_prompt(page['id'], {'text': f'  PRIVATE-PROMPT-{name} — Wall / four-page\n雪\n  ', 'stale':False})
    if page['type']=='letter':
        layout=canvas_layout(); layout['box']['y']=100 if index==1 else 280
        layout['text_align']='left' if index==1 else 'right'
        document={**page['message'],'html':f'<p style="color:#ffffff;font-family:Arial;font-size:22px"><b>MESSAGE {name[-1]}</b></p><p style="color:#ffffff">Independent rich text — café.</p>','layout':layout}
        project.update_message(page['id'],document)
with wave.open(str(root/'tone.wav'),'wb') as audio:
    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(bytes(32000))
project.update_project_data('sound',{'mode':'playlist','playlist':['synthetic','synthetic'],'volume':20,'muted':True,'tracks':[{'track_id':'synthetic','display_title':'Synthetic silence','asset':'tone.wav'}]})
session=get_project_session(root); session.replace(project,asset_root=root)
project_legacy_views(session); session.save(sync_workspace=False)
state=ProjectStateController(root); state.initialize(); state.establish_project('Synthetic Recipient',project_id=project.project_id)
SettingsStore(root).update_fields(recipient_title=project.to_dict()['metadata']['title'],ui_window_maximized=False,ui_window_normal_geometry=[80,80,1200,850])
service=ProjectSaveService(root,state); saved,stored=service.save_editable()
from forge_render import capture_render, export_render
render=capture_render(session,'display')
output=profile/'Portable presentation 雪.zip'; export_render(session,output,render_input=render)
result={'source_revision':__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=code,text=True).strip(),'project_id':project.project_id,'saved':str(saved),'revision':stored.revision,'asset_root':str(stored.asset_root),'expected':stored.project.to_dict(),'asset_hashes':{str(p.relative_to(stored.asset_root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in stored.asset_root.rglob('*') if p.is_file() and p.suffix!='.json'},'display_zip':str(output),'display_sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
(profile/'expected.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({key:result[key] for key in ('project_id','saved','revision','display_zip')}))
state.shutdown()
