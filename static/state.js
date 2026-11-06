/* Pure display/state rules. No camera, browser or motor side effects. */
(function(root){
 const labels={idle:'Готов к записи',starting:'Подключаем оборудование',recording:'Идёт запись',ready:'Эпизод сохранён',stopped:'Сессия завершена',failed:'Запись остановлена с ошибкой',running:'Выполняется',queued:'В очереди',waiting_resources:'Ожидает свободные ресурсы',completed:'Завершено',interrupted:'Прервано'};
 function technical(d){return d.source!=='hardware'||d.test_only===true||d.purpose==='technical_test'}
 function datasets(items,showTests){return (items||[]).filter(d=>showTests||!technical(d))}
 function selected(items,value){return items.some(d=>d.dataset_id===value)?value:''}
 function clock(seconds){seconds=Math.max(0,Math.floor(Number(seconds)||0));return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0')}
 function checkpointTechnical(path){return /(^|\/)technical[^/]*\//i.test(path)}
 function recording(r){return {title:labels[r.state]||'Ожидание',active:!!r.active,recording:r.state==='recording',ready:r.state==='ready',frames:r.active?(r.frames_written||0):((r.source==='hardware'&&r.last_episode?.frames)||0),elapsed:r.active?(r.episode_elapsed_s||0):0}}
 const readiness={READY_FOR_OPERATOR_START:'Готово к запуску оператором',TELEOPERATION_ACTIVE:'Проверка рук включена',RECORDING:'Идёт запись',SESSION_ACTIVE:'Сессия активна',WAITING_FOR_HARDWARE:'Освободите или подключите устройства',BLOCKED:'Нужна проверка',READY_FOR_MODEL_LOAD_CHECK:'Можно загрузить модель',WAITING_FOR_COMPUTE:'Ожидаем свободные GPU и память'};
 const api={readiness,labels,technical,datasets,selected,clock,checkpointTechnical,recording};if(typeof module!=='undefined')module.exports=api;root.Studio=api;
})(typeof globalThis==='undefined'?this:globalThis);
