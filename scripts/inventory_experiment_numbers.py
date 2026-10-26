#!/usr/bin/env python3
"""ENGINEERING_GLUE: read-only numbered-reference inventory before a semantic migration.

Text matches are review locations, never instructions for blind replacement.
Immutable raw artifacts and provenance remain separate from mutable source.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib

FIELDS={'project_id','experiment_id','owner_experiment','source_project_id','target_project_id','origin_project_id','compatible_source_projects','experiments','owner','canonical_project_id','legacy_experiment_id'}


def metadata_references(value,path=''):
    result=[]
    if isinstance(value,dict):
        for key,item in value.items():
            child=path+'/'+str(key)
            if key in FIELDS:
                values=item if isinstance(item,list) else [item]
                result.extend(dict(pointer=child,value=part) for part in values if part in ('04','06','personal'))
            result.extend(metadata_references(item,child))
    elif isinstance(value,list):
        for index,item in enumerate(value):result.extend(metadata_references(item,path+'/'+str(index)))
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();root=args.root.resolve()
    paths=sorted(set(subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True).splitlines()))
    records=[];counts=Counter();unreadable=[]
    for name in paths:
        path=root/name
        if path.is_file():
            raw=path.read_bytes()
            try:text=raw.decode('utf-8')
            except UnicodeDecodeError:text=None
            if text is not None:
                matches=[dict(line=text.count('\n',0,match.start())+1,text=match.group(0)) for match in re.finditer(r'(?<![A-Za-z0-9])(?:04|06)(?![A-Za-z0-9])|04-cross-embodiment-transfer|06-so101',text)]
                role='tests' if '/tests/' in '/'+name or Path(name).name.startswith('test_') else 'legacy_or_evidence' if any(part in Path(name).parts for part in ('_archive','evidence','audits','literature')) else 'active_source_or_document'
                details=[];literals=[]
                if path.suffix in ('.json','.toml'):
                    try:details=metadata_references(json.loads(text) if path.suffix=='.json' else tomllib.loads(text))
                    except (ValueError,TypeError):unreadable.append(name)
                if path.suffix=='.py':
                    try:
                        tree=ast.parse(text)
                        literals=[dict(line=node.lineno,value=node.value) for node in ast.walk(tree) if isinstance(node,ast.Constant) and isinstance(node.value,str) and (node.value in ('04','06','personal') or '/experiments/04' in node.value or '/experiments/06' in node.value)]
                    except SyntaxError:unreadable.append(name)
                if matches or details or literals or name.startswith('04-cross-embodiment-transfer/'):
                    records.append(dict(path=name,sha256=hashlib.sha256(raw).hexdigest(),role=role,text_locations=matches,structured_fields=details,python_literals=literals))
                    counts['files']+=1;counts['text_locations']+=len(matches);counts['structured_fields']+=len(details);counts['python_literals']+=len(literals);counts['files_'+role]+=1
    result=dict(schema='explorer_experiment_renumber_inventory_v1',source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),working_tree_changes=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).splitlines(),
        mapping={'01':'01','02':'02','03':'03','04':'06','05':'05','06':'04','personal':'04'},counts=dict(counts),unparsed_metadata=unreadable,records=records,
        source_mutations=0,meaning='Review locations and exact pre-migration hashes; ambiguous text is not automatically rewritten')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(dict(output=str(args.output),**result['counts'])))


if __name__=='__main__':main()
