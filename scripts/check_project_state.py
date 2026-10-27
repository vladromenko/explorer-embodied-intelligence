#!/usr/bin/env python3
"""ENGINEERING_GLUE: lightweight, read-only canonical Explorer state consistency."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote,urlsplit

REQUIRED=('AGENTS.md','docs/PROJECT_STATE.md','docs/BACKLOG.md','docs/ACCEPTANCE_MATRIX.md',
          'docs/PHYSICAL_CHECKS.md','docs/DECISIONS.md','docs/AI_OPERATIONS.md',
          '.agents/skills/explorer-project-operator/SKILL.md')
TASK_STATUS={'DONE','IN_PROGRESS','OPEN','WAIT_USER','BLOCKED','PARKED'}
LEVELS=({'YES','PARTIAL','NO','N/A'},{'PASS','PARTIAL','NOT_TESTED','N/A'},
        *[{'DEPLOYED','PARTIAL','NOT_DEPLOYED','N/A'}]*3,{'PASS','OPEN','N/A'})
FEATURES={'E02-KEYBOARD','E02-JOYSTICK','E02-CAMERA','E02-RECORD','E02-TRAIN',
          'E03-RETARGET','E03-RECENTER','E03-SMOOTHNESS','E03-MOBILE-RECORD','E03-TRAIN',
          'E04-CAPTURE','E04-TRAIN','E04-REPLAY','E05-HANDOVER','E06-TRANSFER','E01-AUTONOMY'}
IDENTITY=re.compile(r'[A-Z][A-Z0-9]*-[A-Z0-9-]+')
LINK=re.compile(r'\[[^\]]*\]\(([^)]+)\)')


def rows(text,first):
    result=[];inside=False
    for line in text.splitlines():
        cells=[part.strip() for part in line.strip().strip('|').split('|')]
        if line.startswith('|') and cells[0]==first:inside=True
        elif inside and line.startswith('|') and cells and not all(re.fullmatch(r'[-: ]+',cell) for cell in cells):result.append(cells)
        elif inside and not line.startswith('|'):inside=False
    return result


def field(text,name):
    match=re.search(r'^'+re.escape(name)+r':\s*(.+)$',text,re.M)
    return match[1].strip().strip('`') if match else ''


def check(root):
    root=Path(root).resolve();errors=[];documents={}
    for name in REQUIRED:
        path=root/name
        if not path.is_file():errors.append('Missing required file: '+name)
        else:documents[name]=path.read_text()
    state=documents.get('docs/PROJECT_STATE.md','');backlog=documents.get('docs/BACKLOG.md','')
    current=field(state,'Current task');next_ids=IDENTITY.findall(field(state,'Next tasks'))
    commit=field(state,'Code commit');updated=field(state,'Updated')
    if not re.fullmatch('[0-9a-f]{40}',commit):errors.append('PROJECT_STATE requires a full verified Code commit')
    elif (root/'.git').exists():
        result=subprocess.run(['git','cat-file','-e',commit+'^{commit}'],cwd=root,capture_output=True)
        if result.returncode:errors.append('PROJECT_STATE Code commit is absent from Git')
    try:
        if datetime.fromisoformat(updated).tzinfo is None:raise ValueError('timezone missing')
    except ValueError:errors.append('PROJECT_STATE requires an ISO timestamp with timezone')
    tasks={}
    for row in rows(backlog,'ID'):
        if len(row)!=6:errors.append('BACKLOG row requires six columns: '+str(row))
        if len(row)==6:
            identity=row[0]
            if not IDENTITY.fullmatch(identity):errors.append('Invalid backlog ID: '+identity)
            if identity in tasks:errors.append('Duplicate backlog ID: '+identity)
            if row[4] not in TASK_STATUS:errors.append('Unknown backlog status: '+row[4])
            tasks[identity]=row
    if current not in tasks or tasks[current][4]!='IN_PROGRESS':errors.append('Current task must name one IN_PROGRESS backlog ID')
    if sum(row[4]=='IN_PROGRESS' for row in tasks.values())!=1:errors.append('Exactly one root backlog task must be IN_PROGRESS')
    if len(next_ids)!=3 or len(set(next_ids))!=3 or any(identity not in tasks for identity in next_ids):errors.append('PROJECT_STATE Next tasks requires three distinct known backlog IDs')
    for identity,row in tasks.items():
        for dependency in IDENTITY.findall(row[2]):
            if dependency not in tasks:errors.append(identity+' has unknown dependency '+dependency)
    functions=set()
    for row in rows(documents.get('docs/ACCEPTANCE_MATRIX.md',''),'Function'):
        if len(row)!=9:errors.append('Acceptance row requires nine columns: '+str(row))
        if len(row)==9:
            identity=row[0].split(' ',1)[0]
            if identity in functions:errors.append('Duplicate acceptance function: '+identity)
            functions.add(identity)
            for index,allowed in enumerate(LEVELS,1):
                if row[index] not in allowed:errors.append(identity+' has unknown acceptance level '+row[index])
            if not LINK.search(row[7]):errors.append(identity+' requires a local evidence link')
    if not FEATURES<=functions:errors.append('Missing required user functions: '+','.join(sorted(FEATURES-functions)))
    checked=0
    for name,text in documents.items():
        for target in LINK.findall(text):
            target=target.strip().strip('<>');parsed=urlsplit(target)
            if parsed.scheme or not parsed.path:pass
            else:
                path=(root/name).parent/unquote(parsed.path)
                if not path.is_file():errors.append(name+' has missing local link: '+target)
                checked+=1
    return {'status':'FAIL' if errors else 'PASS','current_task':current,'code_commit':commit,
            'required_files':len(REQUIRED),'backlog_ids':len(tasks),'acceptance_functions':len(functions),
            'local_links_checked':checked,'errors':errors}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args();result=check(args.root);print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
