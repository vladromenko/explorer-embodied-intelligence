"""Source-only paper pipeline guards; no model, physics or TeX execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT=Path(__file__).resolve().parents[3]


def script(name):
    path=ROOT/'scripts'/name
    spec=importlib.util.spec_from_file_location(name[:-3],path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def inputs():
    plot=script('plot_negative_learning_paper.py')
    return plot,{key:json.loads((ROOT/path).read_bytes()) for key,(path,_) in plot.INPUTS.items()}


def test_actual_paired_tables_keep_all24_negative_rollouts_and_single_train_audit():
    plot,data=inputs();rows,fit=plot.flatten(data['B'],data['C'],data['T'])
    assert len(rows)==24 and all(row['outcome']=='FAILURE' for row in rows)
    assert sum(row['grasp'] for row in rows if row['campaign']=='C')==1
    assert len(fit)==24 and all(row['quantity']=='endpoint' for row in fit if row['representation']=='ABS')
    assert all(row['quantity']=='dispatched_increment' for row in fit if row['representation']=='INCR')


@pytest.mark.parametrize('mutation',['missing','duplicate','wrong_outcome','closedloop_audit'])
def test_figure_rejects_missing_outcomes_or_false_scope(mutation):
    plot,data=inputs();b,c,t=deepcopy(data['B']),deepcopy(data['C']),deepcopy(data['T'])
    if mutation=='missing':c['trials'].pop()
    elif mutation=='duplicate':b['trials'][1]=deepcopy(b['trials'][0])
    elif mutation=='wrong_outcome':c['trials'][0]['outcome']='SUCCESS'
    else:t['scope']['closed_loop_success_evaluated']=True
    with pytest.raises(ValueError):plot.flatten(b,c,t)


def test_untrusted_archive_rejected_before_compiler_execution(tmp_path):
    build=script('build_research_paper.py');(tmp_path/'archive.tar.gz').write_bytes(b'not release archive')
    with pytest.raises(ValueError,match='archive SHA'):build.prepare_tool(tmp_path)


def test_source_escape_or_modified_official_class_rejected(tmp_path):
    build=script('build_research_paper.py');shutil.copyfile(ROOT/'research/paper/IEEEtran.cls',tmp_path/'IEEEtran.cls')
    (tmp_path/'references.bib').write_text('');(tmp_path/'main.tex').write_text(r'\input{../secret.tex}')
    with pytest.raises(ValueError,match='Contained'):build.manuscript_inputs(tmp_path)
    (tmp_path/'IEEEtran.cls').write_text('modified')
    with pytest.raises(ValueError,match='class'):build.manuscript_inputs(tmp_path)


def test_pdf_font_anonymity_and_overfull_guard(monkeypatch):
    build=script('build_research_paper.py')
    result={'info':'Title: A\nPages: 2\n','fonts':'head\n---\nABC Type 1C Custom yes yes yes 4 0\n','text':'Body\fREFERENCES\nCitation\f'}
    def run(args,**kwargs):return result['info' if args[0]=='pdfinfo' else 'fonts' if args[0]=='pdffonts' else 'text']
    monkeypatch.setattr(build,'run',run)
    assert build.audit_pdf(Path('unused'), '')['structural_stage1_page_limit']
    with pytest.raises(ValueError,match='Overfull'):build.audit_pdf(Path('unused'),r'Overfull \hbox too wide')
    result['fonts']='head\n---\nABC CID TrueType Custom yes yes yes 4 0\n'
    with pytest.raises(ValueError,match='font programs'):build.audit_pdf(Path('unused'),'')
    result['info']='Author: Identified Person\nPages: 2\n'
    with pytest.raises(ValueError,match='Author'):build.audit_pdf(Path('unused'),'')
