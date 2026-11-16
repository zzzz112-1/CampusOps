"""Create a NEW clean runtime from the reviewed base; never edit the old runtime."""
import argparse
import importlib.util
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def stage(base:Path, base_manifest:Path, signed_component_source:Path | None = None):
    base=base.absolute()
    if (Path.cwd().resolve()!=ROOT or ROOT.drive.upper()!='D:'
            or not base.is_relative_to(ROOT/'var/releases')
            or any(p.is_symlink() or p.is_junction() for p in (base,*base.parents))
            or not (base/'python.exe').is_file()):
        raise ValueError('Use an existing reviewed D-drive project runtime')
    if subprocess.check_output(['git','status','--porcelain']).strip():
        raise ValueError('Commit reviewed source before freezing runtime')
    base_manifest=base_manifest.absolute()
    if not base_manifest.is_relative_to(ROOT/'var/releases'):
        raise ValueError('Use the reviewed project release manifest')
    accepted=json.loads(base_manifest.read_text(encoding='utf-8'))['files']
    version=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    release=ROOT/'var/releases'/('totp-hardening-staging-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:6])
    release.mkdir(exist_ok=False)
    runtime=release/'runtime'
    def ignore(directory,names):
        result={name for name in names if name=='__pycache__' or name.endswith(('.pyc','.pyo','.whl')) or name in {'.env','.git','.tmp','.cache'}}
        if Path(directory).name=='site-packages':
            result.update(name for name in names if name=='campusops' or name.lower().startswith(('campusops-','__editable__.campusops','__editable___campusops')))
        if Path(directory)==base/'Scripts':
            result.update(name for name in names if name.lower().startswith('campusops'))
        return result
    # Never silently copy a newly placed credential, backup or unknown file from
    # an old tree. Each copied byte must belong to the accepted RC12 manifest.
    for path in base.rglob('*'):
        if path.is_symlink() or path.is_junction():raise ValueError('Runtime reparse paths rejected')
        relative=path.relative_to(base)
        parent=base
        excluded=False
        for part in relative.parts:
            if part in ignore(str(parent),[part]):excluded=True;break
            parent=parent/part
        if path.is_file() and not excluded:
            key='runtime/'+relative.as_posix()
            if accepted.get(key)!=sha256(path.read_bytes()).hexdigest():
                raise ValueError('Unregistered/modified base runtime file: '+relative.as_posix())
    shutil.copytree(base,runtime,ignore=ignore)
    native_validation = None
    if signed_component_source is not None:
        # Copy only the four exact, reviewed open-source components into a NEW
        # staging tree. Do not execute the runtime until publisher checks pass.
        component_spec=json.loads((ROOT/'docs/inputs/native-components-rc14.json').read_text(encoding='utf-8'))
        for row in component_spec['components']:
            source=signed_component_source/row['path']
            if (any(p.is_symlink() or p.is_junction() for p in (source,*source.parents))
                    or not source.is_file() or sha256(source.read_bytes()).hexdigest()!=row['sha256']):
                raise ValueError('Signed native source missing or hash mismatch')
            shutil.copyfile(source,runtime/row['path'])
        loader=importlib.util.spec_from_file_location('staged_native_components',ROOT/'scripts/runtime_components.py')
        component_module=importlib.util.module_from_spec(loader);loader.loader.exec_module(component_module)
        native_validation=component_module.validate_runtime(runtime,component_spec)
    wheel_dir=release/'wheel';wheel_dir.mkdir()
    env=dict(os.environ);env['PIP_NO_INDEX']='1';env['PYTHONDONTWRITEBYTECODE']='1'
    subprocess.run([sys.executable,'-B','-m','pip','wheel','--no-index','--no-deps','--no-build-isolation',
                    '--wheel-dir',str(wheel_dir),str(ROOT)],env=env,check=True)
    wheel=wheel_dir/f'campusops-{version}-py3-none-any.whl'
    subprocess.run([str(runtime/'python.exe'),'-I','-B','-m','pip','install','--no-index','--no-deps','--no-compile',str(wheel)],env=env,check=True)
    subprocess.run([str(runtime/'python.exe'),'-I','-B','-m','pip','check'],env=env,check=True)
    assert not list((runtime/'Lib/site-packages/campusops').rglob('*.pyc'))
    proof={'version':version,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
           'base_runtime':str(base),'base_preserved':True,'runtime':str(runtime),'wheel':str(wheel),
           'base_manifest':str(base_manifest),'base_copied_bytes_registered':True,
           'wheel_sha256':sha256(wheel.read_bytes()).hexdigest(),
           'clean_project_package_install':True,'owner_tools_included':False,
           'old_project_package_and_caches_copied':False,'actual_owner_state_read':False}
    proof['native_component_validation']=native_validation
    target=release/'staging-manifest.json'
    with target.open('x',encoding='utf-8') as stream:json.dump(proof,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'manifest':str(target),**proof},ensure_ascii=False))
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-runtime',required=True,type=Path)
    parser.add_argument('--base-manifest',required=True,type=Path)
    parser.add_argument('--signed-component-source',type=Path,
                        help='Reviewed original-publisher open-source runtime; only fixed-hash components are admitted')
    args=parser.parse_args()
    stage(args.base_runtime,args.base_manifest,args.signed_component_source)
