"""ENGINEERING_GLUE: figures/tables from three exact public negative pilots.

No models, simulation, fitting, outcome re-labelling or inferential statistics.
Raw font-bearing PDFs remain; outlined derivatives are separately identified.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
INPUTS={
 'B':('research/audits/target-visual-object-v3-results-20261027.json','b532e1a4c7dcafc1984316b33348dfbbad29a72d3fec926c778ffdf2b7370ff5'),
 'C':('research/audits/target-temporal-factorial-C-v2-results-20261027.json','8119c460e20a3f23142cae7b4940a83830f3c4dbfda714bc5c411e1d8e90c75c'),
 'T':('research/audits/target-train-forward-audit-B-results-20261027.json','4c055097c3236c7f09e5b6537f9568b3a8909d682034f19e4e17fb0e91f8ba8c')}
CELLS=('ZERO_ABS','ELAPSED_ABS','ZERO_INCREMENT','ELAPSED_INCREMENT')
LABELS={'chunk1':'B: c1','chunk8':'B: c8','ZERO_ABS':'C: 0/A','ELAPSED_ABS':'C: t/A','ZERO_INCREMENT':'C: 0/Δ','ELAPSED_INCREMENT':'C: t/Δ'}


def require(ok,why):
    if not ok:raise ValueError(why)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    with Path(path).open('xb') as stream:stream.write(json.dumps(value,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n')


def flatten(b,c,t):
    require(b['physical_io'] is False and b['whole_master_prompt_complete'] is False
            and c['scope']['physical_io'] is False and c['scope']['whole_skill_mastered'] is False
            and t['scope']['closed_loop_success_evaluated'] is False
            and t['scope']['physical_io'] is False,'Pilot domain/scope changed')
    rows=[]
    for r in b['trials']:
        rows.append(dict(campaign='B',policy=r['strategy'],case=r['case_id'],partition=r['partition'],outcome=r['outcome'],
                         grasp=r['conditions']['grasp'],lift_and_carry=r['conditions']['lift'] and r['conditions']['carry'],
                         source_result_sha256=r['raw_result']['sha256']))
    for r in c['trials']:
        condition=r['terminal_score']['conditions']
        require(r['terminal_score']['full_object_success']==(r['outcome']=='SUCCESS'),'C outcome/score differs')
        rows.append(dict(campaign='C',policy=r['cell'],case=r['case']['id'],partition=r['case']['partition'],outcome=r['outcome'],
                         grasp=condition['grasp'],lift_and_carry=condition['lift'] and condition['carry'],source_result_sha256=r['result']['sha256']))
    for campaign,policies,n in [('B',('chunk1','chunk8'),8),('C',CELLS,16)]:
        data=[r for r in rows if r['campaign']==campaign]
        require(len(data)==n and len({(r['policy'],r['case']) for r in data})==n,'Missing/duplicate complete matched outcome')
        for policy in policies:
            selected=[r for r in data if r['policy']==policy]
            require(len(selected)==4 and all(sum(r['partition']==p for r in selected)==2 for p in ['development','sealed_test']), 'Matched DEV2/SEALED2 required per policy')
            require(all(r['outcome']=='FAILURE' for r in selected),'Pinned negative pilot outcomes changed')
    require(c['original_C_v1']['UNKNOWN']==1 and c['original_C_v1']['NOT_STARTED']==15,'Do not merge original precheck UNKNOWN into C-v2')
    require(set(t['cells'])==set(CELLS) and t['counts']['actual_forward_calls']==1756
            and t['training_source']['original_episodes']==1,'Four TRAIN-only fixed cells required')
    fit=[]
    for cell in CELLS:
        result=t['cells'][cell]
        require(result['queries']==439 and result['actual_latent_checks']==dict(latent_zero_checks=439,posterior_calls=0),'Actual prior-only query count differs')
        for method in ('MODEL_PRIOR_ZERO','HOLD_ORIGIN_NATIVE9','ZERO_NORMALIZED'):
            score=result['scores'][method]
            require(score['all']['valid_action_slots']==6904 and score['all']['valid_scalar_labels']==62136,'Masked target denominator differs')
            for phase in ['close','release']:
                value=score['target_phase'][phase]['native_label_MAE_by_joint'][5]
                require(type(value) in (float,int) and math.isfinite(value) and value>=0,'Finite observed error required')
                fit.append(dict(cell=cell,representation='ABS' if cell.endswith('ABS') else 'INCR',method=method,phase=phase,
                                native_gripper_MAE=value,quantity=score['native_arm_quantity'],
                                normalized_MAE=score['all']['normalized_MAE'],source_result_sha256=result['raw_result']['sha256']))
    return rows,fit


def outline(source,dest):
    # Vector linework derivative; never replace the font-bearing scientific PDF.
    subprocess.run(['gs','-q','-dSAFER','-dBATCH','-dNOPAUSE','-sDEVICE=pdfwrite','-dCompatibilityLevel=1.5',
                    '-dNoOutputFonts','-dOmitInfoDate=true','-dOmitID=true','-dAutoRotatePages=/None',
                    '-sOutputFile='+str(dest),str(source)],check=True,timeout=60)
    fonts=subprocess.check_output(['pdffonts',str(dest)],text=True)
    require(len(fonts.strip().splitlines())==2,'Outlined figure unexpectedly contains fonts')
    return fonts


def figures(rows,fit,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'pdf.fonttype':42,'ps.fonttype':42,
                         'svg.fonttype':'path','svg.hashsalt':'explorer-negative-pilots-20261027',
                         'axes.spines.top':False,'axes.spines.right':False})
    def save(fig,name):
        metadata=dict(Title=name,Author='',Creator='Source-bound Matplotlib negative-pilot figure',CreationDate=None,ModDate=None)
        fig.savefig(out/(name+'_source.pdf'),metadata=metadata)
        fig.savefig(out/(name+'.png'),dpi=350,metadata={'Software':'Source-bound Matplotlib negative-pilot figure'})
        fig.savefig(out/(name+'.svg'),metadata={'Date':None,'Creator':'Source-bound Matplotlib negative-pilot figure'})
        # Independent second rendering; output bytes must match, no tolerance.
        temp=out/(name+'_repeat.pdf');fig.savefig(temp,metadata=metadata)
        require(sha(temp)==sha(out/(name+'_source.pdf')),'Same-environment PDF render differs')
        outline(out/(name+'_source.pdf'),out/(name+'.pdf'))
        outline(temp,out/(name+'_repeat_outlined.pdf'))
        require(sha(out/(name+'.pdf'))==sha(out/(name+'_repeat_outlined.pdf')),'Outlined PDF repeat differs')
        plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(7.15,2.15),gridspec_kw={'width_ratios':[1,1.22]})
    for ax,campaign,policies,title in zip(axes,['B','C'],[('chunk1','chunk8'),CELLS],['(a) B: fixed checkpoint, chunk1/8','(b) C-v2: fixed 2×2 candidates']):
        subset=[r for r in rows if r['campaign']==campaign]
        cases=sorted({r['case'] for r in subset},key=lambda k:(0 if next(r for r in subset if r['case']==k)['partition']=='development' else 1,k))
        for j,policy in enumerate(policies):
            for i,case in enumerate(cases):
                row=next(r for r in subset if r['policy']==policy and r['case']==case)
                ax.add_patch(plt.Rectangle((i-.46,j-.42),.92,.84,facecolor='#f7e1dc',edgecolor='white'))
                partial=' +LC' if row['lift_and_carry'] else ' +G' if row['grasp'] else ''
                ax.text(i,j,'F'+partial,ha='center',va='center',color='#7d2516',fontsize=8)
        ax.set(xlim=(-.5,3.5),ylim=(len(policies)-.5,-.5),xticks=range(4),xticklabels=['D1','D2','T1','T2'],yticks=range(len(policies)),yticklabels=[LABELS[p] for p in policies])
        ax.axvline(1.5,color='0.4',ls='--',lw=.7);ax.set_title(title,fontsize=9)
        ax.tick_params(length=0);ax.set_xlabel('Development       |       Sealed',fontsize=8)
        for spine in ax.spines.values():spine.set_visible(False)
    fig.subplots_adjust(left=.08,right=.99,bottom=.22,top=.85,wspace=.32)
    save(fig,'negative_target_outcomes')
    fig,axes=plt.subplots(1,2,figsize=(7.15,2.45))
    for ax,rep,unit,title in zip(axes,['ABS','INCR'],['Endpoint error (deg)','Increment error (deg/step)'],['(a) Absolute labels','(b) Increment labels']):
        cells=[cell for cell in CELLS if cell.endswith('ABS')== (rep=='ABS')]
        for j,(cell,color) in enumerate(zip(cells,['#0072B2','#D55E00'])):
            values=[next(r['native_gripper_MAE'] for r in fit if r['cell']==cell and r['method']=='MODEL_PRIOR_ZERO' and r['phase']==phase) for phase in ['close','release']]
            bars=ax.bar(np.arange(2)+(j-.5)*.28,values,width=.27,color=color,label='Zero clock' if j==0 else 'Elapsed clock')
            for bar,value in zip(bars,values):ax.text(bar.get_x()+bar.get_width()/2,value+.02*max(1,max(values)),f'{value:.2f}',ha='center',va='bottom',fontsize=7)
        hold=[next(r['native_gripper_MAE'] for r in fit if r['cell']==cells[0] and r['method']=='HOLD_ORIGIN_NATIVE9' and r['phase']==phase) for phase in ['close','release']]
        ax.plot(range(2),hold,'_--',color='0.35',lw=1,markersize=16,label='Causal HOLD')
        ax.set(xticks=range(2),xticklabels=['Close','Release'],ylabel=unit,ylim=(0,max(hold)*1.16))
        ax.set_title(title,fontsize=9);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    axes[0].legend(loc='upper center',bbox_to_anchor=(1.12,-.20),ncol=3,frameon=False,fontsize=8)
    fig.subplots_adjust(left=.09,right=.99,bottom=.29,top=.86,wspace=.33)
    save(fig,'train_gripper_fit')
    return dict(matplotlib=matplotlib.__version__,numpy=np.__version__,font_paths=[str(Path(matplotlib.get_data_path())/'fonts/ttf/DejaVuSans.ttf')])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    data={};sources={}
    for key,(relative,checksum) in INPUTS.items():
        path=ROOT/relative;require(sha(path)==checksum,'Exact public data SHA differs: '+relative)
        data[key]=json.loads(path.read_bytes());sources[relative]=checksum
    rows,fit=flatten(data['B'],data['C'],data['T'])
    out=args.output.resolve();out.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,values in [('negative_target_outcomes.csv',rows),('train_gripper_fit.csv',fit)]:
        with (out/name).open('x',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(values[0]),lineterminator='\n');writer.writeheader();writer.writerows(values)
    with (out/'negative_counts_table.tex').open('x') as stream:
        stream.write('\\begin{tabular}{lrrrr}\\hline Policy & DEV & Sealed & Grasp & Lift+carry\\\\\\hline\n')
        for campaign,policies in [('B',('chunk1','chunk8')),('C',CELLS)]:
            for policy in policies:
                r=[row for row in rows if row['campaign']==campaign and row['policy']==policy]
                label=LABELS[policy].replace('Δ',r'$\Delta$')
                stream.write(label+' & 0/2 & 0/2 & '+str(sum(x['grasp'] for x in r))+'/4 & '+str(sum(x['lift_and_carry'] for x in r))+'/4 \\\\\n')
        stream.write('\\hline\\end{tabular}\n')
    with (out/'train_fit_table.tex').open('x') as stream:
        stream.write('\\begin{tabular}{lrrr}\\hline Candidate & Model & HOLD & ZERO\\\\\\hline\n')
        for cell in CELLS:
            values=[data['T']['cells'][cell]['scores'][method]['all']['normalized_MAE'] for method in ['MODEL_PRIOR_ZERO','HOLD_ORIGIN_NATIVE9','ZERO_NORMALIZED']]
            stream.write(LABELS[cell].replace('Δ',r'$\Delta$')+' & '+' & '.join(f'{value:.5f}' for value in values)+r' \\'+'\n')
        stream.write('\\hline\\end{tabular}\n')
    versions=figures(rows,fit,out)
    old=ROOT/'research/paper/figures/contact_development_v10.pdf'
    require(sha(old)=='c19dc28c0ef674bbf4ef3158e51e705c804d2a8d2efc5b36e18a61393ed7d1b9','Historical contact PDF differs')
    outline(old,out/'contact_development_v10_outlined.pdf')
    artifacts={path.name:dict(sha256=sha(path),bytes=path.stat().st_size) for path in out.iterdir() if path.is_file()}
    write(out/'PROVENANCE.json',dict(schema='explorer_negative_pilot_paper_figures_v1',source_json_sha256=sources,plot_script_sha256=sha(__file__),versions=versions,
                                    source_contact_pdf_sha256=sha(old),artifacts=artifacts,ghostscript_version=subprocess.check_output(['gs','--version'],text=True).strip(),
                                    outlined_vector_derivatives=True,figure_text_searchability_limited=True,source_PDF_and_SVG_retained=True,
                                    repeat_same_environment_bytes_equal=True,mastery_or_physical_claim=False,physical_io=False,model_or_physics_executed=False))
    print(json.dumps(dict(output=str(out),artifact_count=len(artifacts),source_digests=sources,physical_io=False)))


if __name__=='__main__':main()
