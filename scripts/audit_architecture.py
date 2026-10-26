#!/usr/bin/env python3
"""ENGINEERING_GLUE: reproducible source inventory; candidates are not equivalence proofs.

Reads Python AST, JavaScript declarations, API decorators and service manifests.
Never imports project modules, opens hardware or modifies an execution release.
"""
import argparse
import ast
from copy import deepcopy
from collections import defaultdict
import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess


class Structure(ast.NodeTransformer):
    def visit_Name(self,node):
        return ast.copy_location(ast.Name(id='identifier',ctx=node.ctx),node)
    def visit_arg(self,node):
        return ast.copy_location(ast.arg(arg='argument',annotation=None),node)
    def visit_Constant(self,node):
        return ast.copy_location(ast.Constant(value=type(node.value).__name__),node)
    def visit_FunctionDef(self,node):
        node.name='function';return self.generic_visit(node)
    def visit_AsyncFunctionDef(self,node):
        node.name='function';return self.generic_visit(node)


def module_name(path):
    parts=list(path.with_suffix('').parts)
    if 'src' in parts:parts=parts[parts.index('src')+1:]
    elif parts[0]=='shared':parts=parts[1:]
    if parts[-1]=='__init__':parts=parts[:-1]
    return '.'.join(parts)


def inventory(root):
    root=Path(root).resolve();modules=[];symbols=[];api=[];services=[];jobs=[]
    paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True).splitlines()
    for relative in sorted(set(paths)):
        path=root/relative;parts=Path(relative).parts
        selected=path.is_file() and not any(part in ('.git','__pycache__','node_modules','_archive','_vendor') for part in parts)
        if selected and path.suffix in ('.py','.js','.service','.timer'):
            raw=path.read_bytes();text=raw.decode('utf-8');record=dict(path=relative,sha256=hashlib.sha256(raw).hexdigest(),lines=len(text.splitlines()),
                layer=parts[0],test='tests' in parts or path.name.startswith('test_'),preserved_legacy='legacy' in parts,imports=[])
            modules.append(record)
            if path.suffix=='.py':
                record['module']=module_name(Path(relative));tree=ast.parse(text,filename=relative)
                for node in ast.walk(tree):
                    if isinstance(node,(ast.Import,ast.ImportFrom)):
                        names=[alias.name for alias in node.names] if isinstance(node,ast.Import) else [('.'*node.level)+(node.module or '')]
                        record['imports'].extend(dict(name=name,line=node.lineno) for name in names)
                    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                        item=dict(path=relative,name=node.name,line=node.lineno,end_line=node.end_lineno,kind=type(node).__name__)
                        if not isinstance(node,ast.ClassDef):
                            source=ast.get_source_segment(text,node);body=list(node.body)
                            if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):body=body[1:]
                            exact=ast.dump(ast.Module(body=body,type_ignores=[]),include_attributes=False)
                            normalized=ast.dump(Structure().visit(deepcopy(node)),include_attributes=False)
                            item.update(exact_body_sha256=hashlib.sha256(exact.encode()).hexdigest(),structural_sha256=hashlib.sha256(normalized.encode()).hexdigest(),structural_tokens=re.findall(r'[A-Za-z_]+|\d+',normalized))
                            for decorator in node.decorator_list:
                                if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr in ('get','post','put','patch','delete','api_route'):
                                    value=decorator.args[0] if decorator.args else None
                                    if isinstance(value,ast.Constant) and isinstance(value.value,str):api.append(dict(path=relative,line=node.lineno,handler=node.name,route_literal=value.value,method=decorator.func.attr,router=ast.unparse(decorator.func.value),prefix_resolution='literal_only'))
                        symbols.append(item)
                    if isinstance(node,ast.Assign):
                        for target in node.targets:
                            if isinstance(target,ast.Name) and target.id.endswith('_JOBS'):
                                try:jobs.append(dict(path=relative,line=node.lineno,name=target.id,values=ast.literal_eval(node.value)))
                                except (ValueError,TypeError):pass
            elif path.suffix=='.js':
                for match in re.finditer(r'(?m)^\s*(?:async\s+)?function\s+(\w+)\s*\(',text):symbols.append(dict(path=relative,name=match.group(1),line=text.count('\n',0,match.start())+1,kind='javascript_function'))
                record['imports']=[dict(name=match.group(1),line=text.count('\n',0,match.start())+1) for match in re.finditer(r'(?:require\(|from\s+)[\s]*[\'"]([^\'"]+)[\'"]',text)]
            else:services.append(dict(path=relative,directives=[line for line in text.splitlines() if line.startswith(('ExecStart=','After=','Requires=','Wants=','Restart=','OnUnitActiveSec='))]))
    canonical={row.get('module'):row['path'] for row in modules if row.get('module') and not row['test']}
    edges=[]
    for row in modules:
        for imported in row['imports']:
            name=imported['name']
            if name.startswith('.') and row.get('module'):
                level=len(name)-len(name.lstrip('.'));base=row['module'].split('.')[:-level];name='.'.join(base+[name.lstrip('.')])
            destination=canonical.get(name)
            if destination:edges.append(dict(source=row['path'],target=destination,line=imported['line']))
    eligible=[row for row in symbols if 'structural_sha256' in row and row['end_line']-row['line']>=7 and '/tests/' not in '/'+row['path'] and '/legacy/' not in row['path']]
    duplicate_groups=[]
    for key in ('exact_body_sha256','structural_sha256'):
        groups=defaultdict(list)
        for row in eligible:groups[row[key]].append({name:row[name] for name in ('path','name','line','end_line')})
        duplicate_groups.extend(dict(basis=key,digest=checksum,symbols=members,semantic_equivalence_verified=False) for checksum,members in groups.items() if len({row['path'] for row in members})>1)
    candidates=[]
    for index,left in enumerate(eligible):
        a=left['structural_tokens']
        for right in eligible[index+1:]:
            b=right['structural_tokens'];ratio=min(len(a),len(b))/max(len(a),len(b))
            if left['path']!=right['path'] and left['structural_sha256']!=right['structural_sha256'] and ratio>=.9:
                score=difflib.SequenceMatcher(None,a,b,autojunk=True).quick_ratio()
                if score>=.97:
                    score=difflib.SequenceMatcher(None,a,b,autojunk=True).ratio()
                    if score>=.90:candidates.append(dict(left={key:left[key] for key in ('path','name','line')},right={key:right[key] for key in ('path','name','line')},AST_similarity=round(score,4),semantic_equivalence_verified=False))
    for row in symbols:row.pop('structural_tokens',None)
    dirty=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).splitlines()
    return dict(schema='explorer_source_architecture_inventory_v1',source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),working_tree_changes=dirty,
        limitations=['AST structure similarity is a review candidate, never proof of identical semantics','Dynamic imports/global JS calls and router prefixes require human review','No hardware or runtime capability inferred from source'],
        counts=dict(modules=len(modules),symbols=len(symbols),API_decorators=len(api),resolved_local_import_edges=len(edges),duplicate_groups=len(duplicate_groups),near_duplicate_candidates=len(candidates)),
        modules=modules,symbols=symbols,local_import_edges=edges,api_decorators=api,job_groups=jobs,services=services,duplicate_candidates=duplicate_groups,near_duplicate_candidates=sorted(candidates,key=lambda row:-row['AST_similarity']))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=inventory(args.root);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output),**result['counts'])))


if __name__=='__main__':main()
