"use strict";
const test = require('node:test'), assert = require('node:assert/strict'), vm = require('node:vm'), fs = require('node:fs');
const source = fs.readFileSync('00-control-center/src/explorer/static/app.js','utf8');
function render(active, originals=[]) {
  const scope = { model:{robot:{nativeCapture:{ready_recordings:originals,capture:{}},episodeName:'simple',episodeDescription:'<unsafe>'}}, activeProject:()=>({id:'02'}),
    demonstrationTeaching:()=>({active,last:null}),array:x=>Array.isArray(x)?x:[],badge:()=>'',count:x=>String(x??0),text:(x,fallback)=>x||fallback,
    esc:x=>String(x??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),icon:()=>'',reasonText:String };
  vm.createContext(scope); vm.runInContext(source.slice(source.indexOf('function episodeReviewPanel()'), source.indexOf('// ENGINEERING_GLUE: explicit whole-episode')),scope);
  return vm.runInContext('episodePanel()',scope);
}
test('pause/resume/cancel and outcome actions remain distinct from motor STOP',()=>{
  const html=render({id:'c'.repeat(32),source:'canonical',state:'PAUSED',samples:4});
  assert(html.includes('data-operation="resume"') && html.includes('data-operation="cancel"'));
  assert(html.includes('Пауза относится к записи'));
  assert(html.includes('data-episode-outcome="success"'));
});
test('review and download use the sealed original ID; cancelled recordings cannot be approved',()=>{
  const html=render(null,[{recording_id:'c'.repeat(32),skill_capture_id:'capture',state:'cancelled',task_name:'<unsafe>',source_review:null,archive_sha256:'e'.repeat(64)}]);
  assert(html.includes('&lt;unsafe&gt;') && !html.includes('<unsafe>'));
  assert(html.includes('data-decision="approve"') && html.includes('disabled>Одобрить перенос'));
  assert(html.includes('/api/mobile/capture/recordings/'+'c'.repeat(32)+'/download'));
  assert(html.includes('episode-description'));
});
