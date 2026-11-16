"""Validate versioned, pinned native components; never alters system trust."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def validate_hashes(runtime: Path, specification: dict) -> list[dict]:
    if any(p.is_symlink() or p.is_junction() for p in (runtime, *runtime.parents)):
        raise ValueError('Runtime reparse paths rejected')
    runtime = runtime.resolve()
    if runtime.drive.upper() != 'D:':
        raise ValueError('Runtime must remain on D drive')
    version = specification.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unknown native component specification version')
    permitted = {'DLLs/libffi-8.dll', 'DLLs/sqlite3.dll'}
    if version == 2:
        permitted |= {'DLLs/_sqlite3.pyd', 'DLLs/_uuid.pyd'}
    result = []
    for row in specification['components']:
        relative = Path(row['path'])
        if relative.as_posix() not in permitted:
            raise ValueError('Unexpected native component path')
        path = runtime / relative
        if (any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))
                or not path.is_file() or sha256(path.read_bytes()).hexdigest() != row['sha256']):
            raise ValueError('Native component missing or hash mismatch: ' + relative.as_posix())
        result.append({'path': str(path), 'sha256': row['sha256']})
    if len(result) != len(permitted) or {Path(row['path']).relative_to(runtime).as_posix() for row in result} != permitted:
        raise ValueError('Exactly the versioned distinct native components are required')
    return result


def validate_runtime(runtime: Path, specification: dict | None = None) -> dict:
    if os.name != 'nt':
        raise ValueError('Windows Authenticode validation requires Windows')
    spec = specification or json.loads((ROOT / 'docs/inputs/native-components-rc14.json').read_text(encoding='utf-8'))
    rows = validate_hashes(runtime, spec)
    literals = ','.join("'" + row['path'].replace("'", "''") + "'" for row in rows)
    code = "$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8;$rows=@(foreach($p in @(" + literals + ")){$s=Get-AuthenticodeSignature -LiteralPath $p;[pscustomobject]@{path=$p;status=$s.Status.ToString();thumbprint=$s.SignerCertificate.Thumbprint;signer=$s.SignerCertificate.Subject}});ConvertTo-Json -InputObject $rows -Depth 3 -Compress"
    process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
        capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=0x08000000,
        env={**os.environ, 'PSModulePath': str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/Modules')})
    if process.returncode:
        raise ValueError('Windows signature validation unavailable')
    signatures = json.loads(process.stdout.lstrip('\ufeff'))
    if (not isinstance(signatures, list) or len(signatures) != len(rows)
            or {row['path'].casefold() for row in signatures} != {row['path'].casefold() for row in rows}
            or any(row['status'] != 'Valid' or row['thumbprint'] != spec['signer_thumbprint'] for row in signatures)):
        raise ValueError('Native component publisher signature mismatch')
    return {'status': 'Passed', 'components': signatures, 'hashes_match': True,
            'system_trust_modified': False, 'signatures_created_by_build': False}
