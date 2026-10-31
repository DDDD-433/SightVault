"""Start a hidden localhost service and open the app in the default browser."""
from pathlib import Path
import json,os,socket,subprocess,sys,time,urllib.request,webbrowser

ROOT=Path(__file__).resolve().parent.parent
PORT=int(os.environ.get('SIGHTVAULT_PORT','7860'))
URL=f'http://127.0.0.1:{PORT}'

def running():
    status = running_status()
    return compatible_instance(status)

def running_status():
    try:
        with urllib.request.urlopen(URL+'/api/status',timeout=2) as r:
            return json.load(r)
    except Exception:
        return None

def selected_vision(status):
    if not isinstance(status, dict):
        return None
    models = status.get('models') or {}
    vision = models.get('vision') or {}
    return status.get('vision_backend') or vision.get('backend')

def compatible_instance(status):
    if not isinstance(status, dict) or status.get('instance_root') != str(ROOT):
        return False
    expected = os.environ.get('SIGHTVAULT_VISION', 'minicpm').strip().lower()
    return selected_vision(status) == expected

def provider_mismatch(status):
    if not isinstance(status, dict) or status.get('instance_root') != str(ROOT):
        return False
    expected = os.environ.get('SIGHTVAULT_VISION', 'minicpm').strip().lower()
    return selected_vision(status) != expected

def port_busy():
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(('127.0.0.1',PORT))==0

def tail(path,lines=12):
    try:return '\n'.join(path.read_text(encoding='utf8',errors='replace').splitlines()[-lines:])
    except OSError:return ''

def main():
    status = running_status()
    if compatible_instance(status):
        print('SightVault is already running at '+URL)
    else:
        if provider_mismatch(status):
            selected = os.environ.get('SIGHTVAULT_VISION', 'minicpm').strip().lower()
            active = selected_vision(status) or 'unknown (the running service did not report it)'
            print(f'SightVault is already running from this checkout at {URL} with vision backend {active}; '
                  f'this launch selected {selected}. Stop the existing server and relaunch. '
                  'The launcher will not terminate a running process.')
            return 1
        if port_busy():
            print(f'Port {PORT} is used by another program, not SightVault. Close it, or run app.py with a free --port and set SIGHTVAULT_PORT accordingly')
            return 1
        (ROOT/'data').mkdir(exist_ok=True)
        log_path=ROOT/'data'/'server.log'
        log=open(log_path,'a',encoding='utf8')
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        process=subprocess.Popen([sys.executable,'-u',str(ROOT/'app.py'),'--port',str(PORT)],cwd=ROOT,stdout=log,stderr=log,creationflags=flags,start_new_session=(os.name!='nt'))
        print('Starting SightVault…',flush=True)
        deadline=time.time()+90
        while time.time()<deadline:
            if running():break
            if process.poll() is not None:
                print(f'SightVault stopped during startup (exit code {process.returncode}). Last lines of {log_path}:\n'+tail(log_path))
                return 1
            time.sleep(.5)
        else:
            print(f'SightVault did not respond within 90 seconds. Check {log_path}:\n'+tail(log_path))
            return 1
        print('SightVault is running at '+URL)
    webbrowser.open(URL)
    return 0

if __name__=='__main__':sys.exit(main())
