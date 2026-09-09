"""Share unchanged immutable registry chunks; keep the current JSON readable."""
import os
import uuid

from acceptance_core import canonical, digest, read, write

FORMAT='case-registry-chunks-v1'
CHUNK_ITEMS=128


def _parts(state):
    for field in sorted(state):
        value=state[field]
        if isinstance(value,dict):
            keys=sorted(value)
            parts=[{k:value[k] for k in keys[i:i+CHUNK_ITEMS]} for i in range(0,len(keys),CHUNK_ITEMS)]
            kind='dict'
        elif isinstance(value,list):
            parts=[value[i:i+CHUNK_ITEMS] for i in range(0,len(value),CHUNK_ITEMS)]
            kind='list'
        else:
            kind='value';parts=[value]
        yield field,kind,parts


def _atomic_bytes(path, parts):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('wb') as stream:
            for part in parts:stream.write(part)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()


def store(root, target, state, state_hash):
    components=[];new_bytes=0;new_chunks=0
    for field,kind,parts in _parts(state):
        hashes=[]
        for part in parts:
            raw=(canonical(part)+'\n').encode();hashed=digest(raw)
            path=root/'chunks'/(hashed+'.json')
            if path.exists():
                if digest(path.read_bytes())!=hashed:
                    raise ValueError('Immutable case chunk changed: '+hashed)
            else:
                _atomic_bytes(path,[raw]);new_bytes+=len(raw);new_chunks+=1
            hashes.append(hashed)
        components.append({'field':field,'kind':kind,'chunks':hashes})
    snapshot={'format':FORMAT,'version':state['version'],'registry_sha256':state_hash,'components':components}
    write(target,snapshot)
    return snapshot,{'new_chunk_bytes':new_bytes,'new_chunks':new_chunks,
                     'version_file_bytes':target.stat().st_size}


def verify(root, snapshot, value):
    """Validate each stored chunk against the actual current state, not a flag."""
    components=[]
    for field,kind,parts in _parts(value['payload']):
        hashes=[]
        for part in parts:
            hashed=digest((canonical(part)+'\n').encode())
            path=root/'chunks'/(hashed+'.json')
            if not path.is_file() or digest(path.read_bytes())!=hashed:
                raise ValueError('Case version chunk changed or missing: '+hashed)
            hashes.append(hashed)
        components.append({'field':field,'kind':kind,'chunks':hashes})
    expected={'format':FORMAT,'version':value['payload']['version'],
              'registry_sha256':value['sha256'],'components':components}
    if snapshot!=expected:
        raise ValueError('Case version changed')


def write_current(path, serialized_state, state_hash):
    _atomic_bytes(path,[b'{"payload":',serialized_state,b',"sha256":"',state_hash.encode(),b'"}\n'])
