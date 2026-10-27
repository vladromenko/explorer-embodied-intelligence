"""ENGINEERING_GLUE: exact ARM64 Tectonic tool + two offline LaTeX builds.

No replacement PDF generator: only the checked Tectonic TeX/BibTeX executable
produces main.pdf. Original source, all logs, caches and failures are retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
TOOL_URL='https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-aarch64-unknown-linux-musl.tar.gz'
ARCHIVE_SHA='b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8'
BINARY_SHA='19a2b763e5875fffefaa193c42e460ceba5983d027b6d50b0094130d26ba4e4a'
CLASS_SHA='d63d4bf27f29603a733d06e27ba5fbb40488acf9d1d696354a88b886758f70a7'
BUNDLE_URL='https://relay.fullyjustified.net/default_bundle_v33.tar'
EPOCH='1793059200'


def require(ok,message):
    if not ok:raise ValueError(message)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def write(path,value):
    with Path(path).open('xb') as stream:stream.write(json.dumps(value,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n')
def run(command,**kwargs):return subprocess.run(command,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**kwargs).stdout


def prepare_tool(root):
    require(platform.machine()=='aarch64','This exact tool lock is ARM64 only')
    root=Path(root).resolve();root.mkdir(mode=0o700,parents=True,exist_ok=True)
    archive=root/'archive.tar.gz';binary=root/'tectonic'
    if not archive.exists():
        with urllib.request.urlopen(TOOL_URL,timeout=60) as response,archive.open('xb') as stream:
            size=0
            for block in iter(lambda:response.read(1024*1024),b''):
                size+=len(block);require(size<=16*1024**2,'Bounded upstream archive required');stream.write(block)
    require(archive.stat().st_size==9926834 and sha(archive)==ARCHIVE_SHA,'Official release archive SHA differs')
    if not binary.exists():
        with tarfile.open(archive) as tf:
            members=tf.getmembers();require(len(members)==1,'Exact single-binary official archive required')
            member=members[0];require(member.name=='tectonic' and member.isfile() and member.size<128*1024**2,'Regular bounded Tectonic binary required')
            with binary.open('xb') as stream:stream.write(tf.extractfile(member).read(member.size+1))
        binary.chmod(0o700)
    require(not binary.is_symlink() and sha(binary)==BINARY_SHA,'Exact Tectonic0.17.0 ARM64 binary required')
    require(run([str(binary),'--version']).strip()=='Tectonic 0.17.0','Compiler version differs')
    return binary


def relative_file(root,name):
    p=PurePosixPath(name)
    require(name and not p.is_absolute() and all(part not in ('','.','..') for part in name.split('/')),'Contained manuscript input required')
    path=Path(root).joinpath(*p.parts)
    require(not path.is_symlink() and path.resolve().is_relative_to(Path(root).resolve()) and path.is_file(),'Manuscript source escaped/missing')
    return path


def manuscript_inputs(root):
    root=Path(root).resolve();main=root/'main.tex';text=main.read_text()
    names={'main.tex','references.bib','IEEEtran.cls'}
    names.update(re.findall(r'\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}',text))
    names.update(re.findall(r'\\input\{([^}]+)\}',text))
    require(sha(root/'IEEEtran.cls')==CLASS_SHA,'Official IEEEtran class must remain unchanged')
    result={}
    for name in sorted(names):
        p=relative_file(root,name);require(p.stat().st_size<=16*1024**2,'Bounded manuscript input required')
        result[name]=dict(sha256=sha(p),bytes=p.stat().st_size)
    return result


def inventory(root):
    root=Path(root);result={};total=0
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'Cache symlinks are not copied implicitly')
        if path.is_file():
            size=path.stat().st_size;total+=size
            require(len(result)<10000 and size<=128*1024**2 and total<=512*1024**2,'Bounded private TeX cache required')
            result[str(path.relative_to(root))]=dict(sha256=sha(path),bytes=size)
    require(result,'Existing populated TeX cache required; run explicit bootstrap probe first')
    return result


def resources():
    relative=next(row.split('::',1)[1] for row in Path('/proc/self/cgroup').read_text().splitlines() if row.startswith('0::'))
    folder=Path('/sys/fs/cgroup')/relative.lstrip('/')
    value=dict(pid=os.getpid(),cgroup=str(folder),limits={name:(folder/name).read_text().strip() for name in ['cpu.max','memory.max','memory.swap.max']},memory_peak_bytes=int((folder/'memory.peak').read_text()),memory_events=(folder/'memory.events').read_text())
    quota,period=value['limits']['cpu.max'].split()
    require(quota!='max' and int(quota)<=2*int(period) and value['limits']['memory.max']!='max'
            and int(value['limits']['memory.max'])<=2*1024**3 and value['limits']['memory.swap.max']=='0','CPU<=2/MemoryMax<=2GiB/swap0 unit required')
    return value


def audit_pdf(path,log):
    info=run(['pdfinfo',str(path)]);fonts=run(['pdffonts',str(path)]);text=run(['pdftotext','-layout',str(path),'-'])
    require(not re.search(r'^Author:\s*\S',info,re.M),'Author metadata must be empty')
    require(not re.search(r'/Users/|/home/|vladromenko|jetsonnx',text,re.I),'Private identity/path leaked into manuscript')
    require(not re.search(r'(Citation .*undefined|There were undefined references|undefined citations)',log,re.I),'Final TeX pass has unresolved citations/references')
    overfull=re.findall(r'Overfull \\[hv]box[^\n]*',log)
    require(not overfull,'Overfull boxes remain in final manuscript: '+str(overfull))
    fontrows=fonts.strip().splitlines()[2:]
    require(fontrows and all('Type 1' in row and re.search(r'\byes\s+(?:yes|no)\s+(?:yes|no)\s+\d+\s+\d+\s*$',row) for row in fontrows), 'All PDF font programs must be embedded Type1/Type1C; outlined figures have no fonts')
    pages=[page for page in text.split('\f') if page.strip()];refpages=[];content=[]
    pattern=r'(?m)^\s*R\s*E\s*F\s*E\s*R\s*E\s*N\s*C\s*E\s*S\s*$'
    first=None
    for i,page in enumerate(pages):
        found=re.search(pattern,page)
        if found is not None:
            first=i
            if page[:found.start()].strip():content.append(i+1)
        elif first is None:content.append(i+1)
        if first is not None:refpages.append(i+1)
    require(first is not None,'References heading/page boundary not found')
    return dict(pdfinfo=info,pdffonts=fonts,pages=len(pages),content_pages=content,reference_pages=refpages,
                structural_stage1_page_limit=(len(pages)<=6 and len(content)<=5 and len(refpages)<=1),
                fonts_embedded_Type1_or_Type1C=True,figure_labels_outlined_searchability_limited=True,
                undefined_citations=False,overfull_boxes=False,underfull_warning_count=len(re.findall('Underfull',log)),
                submission_ready=False,venue_compliance_certified=False)


def build(output,tool,cache):
    before=resources();require(sha(tool)==BINARY_SHA,'Unpinned compiler binary')
    source=ROOT/'research/paper';inputs=manuscript_inputs(source)
    out=Path(output).resolve();out.mkdir(mode=0o700,parents=True,exist_ok=False)
    write(out/'started.json',dict(resources=before,time=time.time(),compiler=str(tool),compiler_sha256=BINARY_SHA,source_inputs=inputs))
    try:
        cached=inventory(cache);shutil.copytree(cache,out/'cache')
        write(out/'cache-inputs.json',cached)
        pdfs=[];audits=[];cache_states=[]
        for number in [1,2]:
            work=out/('run'+str(number));src=work/'source';dest=work/'output';src.mkdir(parents=True);dest.mkdir()
            for name,descriptor in inputs.items():
                original=source/name;require(sha(original)==descriptor['sha256'],'Source changed during snapshot')
                target=src/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
            command=[str(tool),'--untrusted','--only-cached','--bundle',BUNDLE_URL,'--keep-intermediates','--keep-logs',
                     '--makefile-rules',str(dest/'dependencies.mk'),'-Z','deterministic-mode','--outdir',str(dest),'main.tex']
            env=dict(os.environ,XDG_CACHE_HOME=str(out/'cache'),SOURCE_DATE_EPOCH=EPOCH,TZ='UTC',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1')
            started=time.monotonic()
            try:terminal=run(command,cwd=src,env=env,timeout=550)
            except subprocess.CalledProcessError as error:
                (work/'terminal.log').write_text(error.stdout or '');raise
            (work/'terminal.log').write_text(terminal)
            require('note: downloading' not in terminal,'Offline compilation unexpectedly fetched resources')
            pdf=dest/'main.pdf';require(pdf.is_file(),'Compiler did not emit a PDF')
            audit=audit_pdf(pdf,(dest/'main.log').read_text());audit.update(elapsed_s=time.monotonic()-started,command=command,pdf_sha256=sha(pdf),source_inputs=inputs)
            write(work/'audit.json',audit);pdfs.append(pdf);audits.append(audit);cache_states.append(inventory(out/'cache'))
        require(sha(pdfs[0])==sha(pdfs[1]),'Different source/output roots produced different PDFs')
        require(all(sha(source/name)==descriptor['sha256'] for name,descriptor in inputs.items()),'Paper source changed during build')
        receipt=dict(schema='explorer_actual_offline_LaTeX_paper_build_v1',compiled_TeX=True,compiler='Tectonic0.17.0 (XeTeX/BibTeX)',
                     compiler_binary_sha256=BINARY_SHA,official_archive_sha256=ARCHIVE_SHA,official_release_url=TOOL_URL,
                     source_inputs_sha256={name:value['sha256'] for name,value in inputs.items()},builder_sha256=sha(__file__),
                     source_git_commit=run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip(),SOURCE_DATE_EPOCH=EPOCH,
                     builds=audits,pdf_sha256=sha(pdfs[1]),pdf_bytes=pdfs[1].stat().st_size,
                     byte_identical_across_two_source_output_directories=True,only_cached_resources=True,
                     cache_input_manifest_sha256=sha(out/'cache-inputs.json'),cache_snapshot=str(out/'cache'),
                     cache_unchanged_during_second_build=cache_states[0]==cache_states[1],
                     cache_dependencies_verified=True,OS_dependency_closure='PARTIAL_NOT_SANDBOX_OR_C_OPEN_AUDITED',
                     physical_io=False,model_or_training_or_physics=False,submission_ready=False,whole_master_prompt_complete=False)
        shutil.copyfile(pdfs[1],out/'main.pdf');write(out/'BUILD_VERIFICATION.json',receipt)
        print(json.dumps(dict(output=str(out),pdf_sha256=receipt['pdf_sha256'],pages=audits[-1]['pages'],content_pages=audits[-1]['content_pages'],reference_pages=audits[-1]['reference_pages'],structural_stage1_page_limit=audits[-1]['structural_stage1_page_limit'],compiled_TeX=True,byte_identical=True,submission_ready=False)))
    except BaseException as error:
        write(out/'failure.json',dict(type=type(error).__name__,detail=str(error)))
        raise
    finally:write(out/'resources-final.json',resources())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare-tool','build'])
    parser.add_argument('--tools-root',type=Path,default=Path.home()/'.cache/explorer-paper-tools/tectonic-0.17.0-aarch64')
    parser.add_argument('--cache',type=Path,default=Path.home()/'.cache/explorer-paper-tools/tectonic-cache')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();binary=prepare_tool(args.tools_root)
    if args.stage=='build':build(args.output,binary,args.cache)


if __name__=='__main__':main()
