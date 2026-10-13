#!/usr/bin/env python3
"""Map fine guest-PC buckets to this captured Detroit build's unwind functions.

No code modification. A 16-byte bucket crossing function boundaries is reported
as ambiguous; sample totals cover only the profiler's printed top entries.
"""
import argparse
import bisect
import collections
import hashlib
from pathlib import Path
import re
import struct

EXE_SHA256 = 'ecf52321921387e683904e089082d76b973326fc093af14e524056715519c1cf'
KNOWN = {0x64cf80:'VMA allocation-request search (source match)',
         0x64d8b0:'VMA allocation feasibility/alignment (source match)',
         0x65d640:'loading/compile job',0x65dbb0:'graphics build/cache/job routing',
         0x65ebf0:'graphics pipeline builder',0x65fd80:'compute pipeline builder',
         0x65f3c0:'render-time graphics lookup/wait',0x647b80:'compute bind/cache miss'}

def unwind_functions(data):
    pe = struct.unpack_from('<I',data,0x3c)[0]
    if data[pe:pe+4]!=b'PE\0\0' or struct.unpack_from('<H',data,pe+24)[0]!=0x20b:
        raise ValueError('expected PE32+ executable')
    count = struct.unpack_from('<H',data,pe+6)[0]
    optional_size = struct.unpack_from('<H',data,pe+20)[0]
    sections=[]
    for index in range(count):
        p=pe+24+optional_size+40*index
        name=data[p:p+8].split(b'\0')[0]
        _,rva,size,offset=struct.unpack_from('<IIII',data,p+8)
        sections.append((name,rva,size,offset))
    def offset(rva):
        return next(o+rva-v for _,v,s,o in sections if v<=rva<v+s)
    pdata=next(s for s in sections if s[0]==b'.pdata')
    functions=[struct.unpack_from('<III',data,p) for p in range(pdata[3],pdata[3]+pdata[2]-11,12)]
    by_start={start:(end,unwind) for start,end,unwind in functions}
    def canonical(start):
        seen=set()
        while start in by_start and start not in seen:
            seen.add(start)
            p=offset(by_start[start][1])
            if not (data[p]>>3)&4: break
            p+=((4+2*data[p+2]+3)&~3)
            start=struct.unpack_from('<I',data,p)[0]
        return start
    return sorted((start,end,canonical(start)) for start,end,_ in functions if end>start)

def bucket_functions(functions,rva):
    starts=[x[0] for x in functions]
    index=max(0,bisect.bisect_right(starts,rva)-1)
    roots=set()
    while index<len(functions) and functions[index][0]<rva+16:
        start,end,root=functions[index]
        if end>rva: roots.add(root)
        index+=1
    return roots

def analyze(log,exe):
    data=Path(exe).read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    if digest!=EXE_SHA256: raise ValueError(f'unsupported executable SHA-256 {digest}; refusing version-specific labels')
    functions=unwind_functions(data)
    samples=collections.Counter()
    for m in re.finditer(r'\[perf-classify-game-detail\] tid=([0-9a-f]+) DetroitBecomeHuman\.exe\+0x([0-9a-f]+) samples=(\d+)',Path(log).read_text(),re.I):
        samples[(m[1],int(m[2],16))]+=int(m[3])
    if not samples: raise ValueError('no fine Detroit samples; rebuild and collect [perf-classify-game-detail] first')
    print('Printed-sample totals only; not a whole-process CPU percentage. Bucket width: 16 bytes.')
    for (tid,rva),hits in samples.most_common(30):
        roots=bucket_functions(functions,rva)
        desc=' / '.join(f'RVA 0x{root:x}: {KNOWN.get(root,"unnamed unwind function")}' for root in sorted(roots)) or 'leaf/no unwind entry'
        if len(roots)>1: desc='ambiguous bucket: '+desc
        print(f'tid={tid} bucket=0x{rva:x} samples={hits} -> {desc}')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log'); parser.add_argument('exe')
    args=parser.parse_args()
    try: analyze(args.log,args.exe)
    except (ValueError,StopIteration,struct.error) as error: parser.exit(1,f'{error}\n')
