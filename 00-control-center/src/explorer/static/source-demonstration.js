"use strict";
// ENGINEERING_GLUE: thin source04 observed demonstration + DATA lifecycle client.
(function (surface) {
  const BASE="/api/lab/demonstrations", ID=/^[0-9a-f]{32}$/, SHA=/^[0-9a-f]{64}$/;
  const copy=value=>value == null ? value : JSON.parse(JSON.stringify(value));
  const rows=value=>Array.isArray(value)?value:[];
  const bindingEqual=(a,b)=>Boolean(a && b && Object.keys(a).length===Object.keys(b).length && Object.keys(a).every(key=>a[key]===b[key]));
  const affirmations=()=>({observing:false,robot_workspace_checked:false,user_observed_test_confirmed:false});
  function createSourceDemonstrationClient(options) {
    if (typeof options?.get!=="function" || typeof options?.post!=="function") throw new Error("Нужен доверенный клиент source04");
    let listing=[],selected="",context=null,status=null,manifest=null,review=null,archiveSHA="",previewSeen=false,importJobs=[];
    let owner=null,challenge=null,frame=null,flags=affirmations(),watching=false,visible=options.visible!==false;
    let generation=0,readSequence=0,frameSequence=0,ownerRevision=0,busy="",dataBusy="",error="",watchBusy=false,uncertainControl=false;
    let pendingPrepare=null,previewIndex=0,phase="",lastCleanup=null,nextWatchAt=0;
    const preservedPrepareRequests=[];
    const cleanups=new Map(),cleanupPending=new Set(),nativePending=new Set(),uncertainScopes=new Map(),observedFrames=new Set(),phaseCatalogues=new Map();
    const now=options.now || (()=>surface.performance?.now?.() ?? Date.now());
    const notify=()=>options.onChange?.();
    const attempt=()=>listing.find(row=>row.demonstration_id===selected);
    const contextMatches=(body,row)=>Boolean(body && row && body.skill_id===row.binding?.skill_id && body.skill_version===row.binding?.skill_version &&
      body.skill_spec_sha256===row.binding?.spec_sha256 && body.session_id===row.record_start_body?.session_id &&
      body.context?.variant_id===row.record_start_body?.context?.variant_id && body.context?.object_instance_id===row.record_start_body?.context?.object_instance_id);
    const requestMatches=(request,body)=>Boolean(request && body && request.skill_id===body.skill_id && request.skill_version===body.skill_version &&
      request.context.session_id===body.session_id && request.context.variant_id===body.context?.variant_id && request.context.object_instance_id===body.context?.object_instance_id);
    const specKey=value=>`${value?.skill_id}:${value?.skill_version ?? value?.version}:${value?.spec_sha256 || value?.skill_spec_sha256}`;
    const phases=()=>phaseCatalogues.get(specKey(attempt()?.binding || context?.record_start_body)) || [];
    const route=id=>`${BASE}/${id || selected}`;
    function newID() {
      if (options.newID) return options.newID();
      if (surface.crypto?.randomUUID) return surface.crypto.randomUUID().replaceAll("-","");
      if (!surface.crypto?.getRandomValues) throw new Error("Недоступен генератор идентичности запроса");
      return Array.from(surface.crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,"0")).join("");
    }
    function releaseFrame() {if(frame?.url)(options.revokeObjectURL || surface.URL?.revokeObjectURL)?.(frame.url);frame=null;frameSequence++;}
    function dropOwner() {owner=null;watching=false;watchBusy=false;ownerRevision++;releaseFrame();challenge=null;flags=affirmations();}
    function snapshot() {
      const row=attempt(),known=row?.acknowledged_recording_id || null,source=status?.capture?.capture;
      const candidate=row?.capture_start_pending && source?.actor_id===row.actor_id &&
        bindingEqual(source?.source_context?.context?.source04_demo_permission?.binding,row.binding) ? source.recording_id : null;
      return copy({listing,selected,context,status,manifest,review,archiveSHA,previewSeen,importJobs,owner,challenge,frame,flags,watching,visible,busy:busy || dataBusy,controlBusy:busy,dataBusy,error,
        uncertainControl,watchBusy,watchDelayMs:Math.max(0,nextWatchAt-now()),cleanupUnconfirmed:lastCleanup?.confirmed===false,pendingNativeCommands:nativePending.size,prepareUncertain:Boolean(pendingPrepare),preservedPrepareRequests:preservedPrepareRequests.length,
        recordingID:known,capturePending:row?.capture_start_pending===true,phases:phases(),phase,previewIndex,
        recoveryCandidate:ID.test(candidate || "") ? candidate : null});
    }
    const scopeKey=receipt=>`${receipt.attempt}:${receipt.grant_id}:${receipt.control_epoch}`;
    function cleanup(receipt,operation="stop") {
      if (!receipt || !ID.test(receipt.grant_id || "") || !receipt.control_epoch || !ID.test(receipt.attempt || "")) return Promise.resolve({confirmed:true,not_needed:true});
      const key=`${receipt.attempt}:${receipt.grant_id}:${receipt.control_epoch}`;
      if(cleanups.has(key))return cleanups.get(key);
      const pending=(async()=>{
        try {
          const response=await options.post(`${route(receipt.attempt)}/${operation}`,{grant_id:receipt.grant_id,expected_control_epoch:receipt.control_epoch});
          const confirmed=operation==="revoke" ? response?.revoked===true : response?.native_command_called===true && response.status?.mode==="idle";
          if(!confirmed)throw new Error("Источник не подтвердил завершение исходного разрешения управления");
          if(lastCleanup?.scope?.attempt===receipt.attempt && lastCleanup.scope.grant_id===receipt.grant_id)lastCleanup=null;
          for(const [identity,value] of uncertainScopes)if(value.attempt===receipt.attempt && value.grant_id===receipt.grant_id)uncertainScopes.delete(identity);
          return {confirmed:true,response};
        } catch(failure) {
          cleanups.delete(key);lastCleanup={confirmed:false,scope:copy(receipt),operation,message:failure.message};
          uncertainScopes.set(scopeKey(receipt),copy(receipt));
          return copy(lastCleanup);
        } finally {notify();}
      })();
      cleanups.set(key,pending);cleanupPending.add(pending);pending.then(()=>cleanupPending.delete(pending),()=>cleanupPending.delete(pending));
      if(cleanups.size>128){const first=cleanups.keys().next().value;if(first!==key)cleanups.delete(first);}
      return pending;
    }
    function tracked(operation) {
      const pending=operation();nativePending.add(pending);
      pending.then(()=>nativePending.delete(pending),()=>nativePending.delete(pending));return pending;
    }
    async function drain(timeoutMs=15000) {
      if(!Number.isFinite(timeoutMs) || timeoutMs<1 || timeoutMs>15000)throw new Error("Нужно ограниченное ожидание собственных команд");
      let timer;
      const work=(async()=>{
        while(nativePending.size || cleanupPending.size)await Promise.allSettled([...nativePending,...cleanupPending]);
        const unresolved=[...uncertainScopes.values(),...(lastCleanup?.confirmed===false?[lastCleanup.scope]:[])];
        for(const scope of unresolved) {
          const packet=await options.get(`${route(scope.attempt)}/status`);
          // Read-only proof that the original scope has ended. Never borrow
          // this packet's epoch/grant and never claim a physical hold receipt.
          if(packet.station?.mode!=="idle" || packet.permission?.execution_permission===true || packet.permission?.grant?.execution_permission===true || packet.station?.last_hold_error)
            throw new Error("STOP своего управления не подтверждён. Вход сохранён; проверьте источник и повторите scoped STOP");
          uncertainScopes.delete(scopeKey(scope));
          if(lastCleanup?.scope?.grant_id===scope.grant_id && lastCleanup.scope.attempt===scope.attempt)lastCleanup=null;
        }
        uncertainControl=false;notify();return {confirmed:true,physical_hold_inferred:false};
      })();
      try {return await Promise.race([work,new Promise((_,reject)=>{timer=(options.setTimeout || surface.setTimeout || setTimeout)(()=>reject(new Error("Собственная команда ещё не завершена. Вход сохранён")),timeoutMs);})]);}
      finally {(options.clearTimeout || surface.clearTimeout || clearTimeout)(timer);}
    }
    function interrupt(reason="Наблюдение выключено") {
      const previous=owner;generation++;readSequence++;dropOwner();busy="";dataBusy="";error=reason;notify();
      return cleanup(previous,previous?.native_mode==="idle"?"revoke":"stop");
    }
    function checkedGrant(value,row) {
      if (!value || !ID.test(value.grant_id || "") || typeof value.control_epoch!=="string" || !value.control_epoch ||
          value.execution_permission!==true || !bindingEqual(value.binding,row?.binding) || value.actor_id!==row.actor_id ||
          !["idle","teleoperation"].includes(value.native_mode)) throw new Error("Не подтверждены точные owner/binding/returned epoch");
      return {...copy(value),attempt:row.demonstration_id};
    }
    function requireOwner(mode) {
      if (!owner || owner.attempt!==selected || !visible || !watching || uncertainControl || mode && owner.native_mode!==mode) throw new Error("Нужно ваше текущее подтверждённое наблюдение и исходный owner/epoch");
      return copy(owner);
    }
    async function refresh() {
      const epoch=generation,sequence=++readSequence;
      try {
        const result=await options.get(BASE);
        if (epoch!==generation || sequence!==readSequence) return {ignored:true};
        listing=rows(result.attempts);
        if (selected && !attempt()) {selected="";dropOwner();}
        if (selected) {
          const id=selected,packet=await options.get(`${route(id)}/status`);
          if (epoch!==generation || sequence!==readSequence || id!==selected) return {ignored:true};
          status=packet;listing=listing.map(row=>row.demonstration_id===id?packet.attempt:row);
          if (owner && !["start","recenter","authorize"].includes(busy)) {
            const actual=packet.permission?.grant;
            if (actual?.grant_id!==owner.grant_id || actual?.control_epoch!==owner.control_epoch || packet.station?.control_epoch!==owner.control_epoch ||
                !bindingEqual(actual?.binding,owner.binding)) dropOwner(); // Foreign/expired GET causes no POST.
          }
          const known=attempt()?.acknowledged_recording_id;
          const entry=rows(packet.capture?.ready_recordings).find(row=>row.recording_id===known && row.project_id==="04");
          archiveSHA=SHA.test(entry?.archive_sha256 || "") ? entry.archive_sha256 : "";
          if (known && entry) {
            const [saved,reviewed,jobs]=await Promise.all([options.get(`${route(id)}/capture/manifest`),options.get(`${route(id)}/capture/review`),options.get("/api/jobs")]);
            if (epoch!==generation || sequence!==readSequence || id!==selected) return {ignored:true};
            manifest=saved;review=reviewed;
            importJobs=rows(jobs.jobs).filter(job=>job.kind==="station_import" && job.project_id==="04" && job.config?.source_format==="so101_source_capture_v1" &&
              job.config.episode_id===known && job.config.archive_sha256===archiveSHA && SHA.test(job.config.source_review_sha256 || ""));
          }
        }
        error="";
      } catch (failure) {if(epoch===generation && sequence===readSequence)error=failure.message;throw failure;}
      finally {if(epoch===generation && sequence===readSequence)notify();}
      return snapshot();
    }
    function selectAttempt(id) {
      if (!ID.test(id || "") || !listing.some(row=>row.demonstration_id===id)) throw new Error("Выберите существующий серверный показ");
      if(id===selected)return Promise.resolve(snapshot());
      const pending=interrupt("Выбран показ; управление не присвоено из статуса");
      selected=id;if(context && !contextMatches(context.record_start_body,attempt()))context=null;
      status=null;manifest=null;review=null;archiveSHA="";importJobs=[];previewSeen=false;previewIndex=0;phase=phases()[0] || "";uncertainControl=false;notify();return pending;
    }
    function setContext(value,phases=[]) {
      const body=value?.record_start_body;
      if (!body || body.project_id!=="04" || !body.skill_id || !Number.isInteger(body.skill_version) || !SHA.test(body.skill_spec_sha256 || "") ||
          !body.session_id || !body.context?.variant_id || !body.context?.object_instance_id) throw new Error("Выберите подтверждённые навык/версию/вариант/сессию в мастере");
      const pending=interrupt("Контекст нового показа подготовлен; разрешение управления не выдано");
      if(pendingPrepare && !requestMatches(pendingPrepare,body)){preservedPrepareRequests.push(copy(pendingPrepare));pendingPrepare=null;}
      if(!pendingPrepare){const prior=preservedPrepareRequests.findIndex(request=>requestMatches(request,body));if(prior>=0)pendingPrepare=preservedPrepareRequests.splice(prior,1)[0];}
      context={record_start_body:copy(body),phases:rows(phases).slice()};phaseCatalogues.set(specKey(body),rows(phases).slice());phase=rows(phases)[0] || "";selected="";status=null;manifest=null;review=null;archiveSHA="";importJobs=[];previewSeen=false;previewIndex=0;uncertainControl=false;notify();return pending;
    }
    async function prepare() {
      if (busy || owner || !context) throw new Error("Подготовьте контекст и завершите текущее управление отдельным STOP");
      const body=context.record_start_body,epoch=generation;
      if (!pendingPrepare) pendingPrepare={skill_id:body.skill_id,skill_version:body.skill_version,request_id:newID(),
        context:{variant_id:body.context.variant_id,session_id:body.session_id,object_instance_id:body.context.object_instance_id,name:body.name || body.task || "Показ",episode_scope:body.context.episode_scope || "whole_episode"}};
      busy="prepare";error="";notify();
      try {
        const row=await options.post(BASE,copy(pendingPrepare));
        if (epoch!==generation) return {ignored:true};
        if (!ID.test(row.demonstration_id || "") || row.request_id!==pendingPrepare.request_id || row.binding?.skill_id!==body.skill_id || row.binding?.skill_version!==body.skill_version ||
            row.binding?.spec_sha256!==body.skill_spec_sha256) throw new Error("Сервер не подтвердил точный контекст нового показа");
        pendingPrepare=null;listing=[...listing.filter(value=>value.demonstration_id!==row.demonstration_id),row];selected=row.demonstration_id;notify();await refresh();return row;
      } catch(failure){if(epoch===generation)error=failure.message;throw failure;}
      finally {if(epoch===generation){busy="";notify();}}
    }
    async function loadFrame(path,kind,expected,epoch) {
      const token=++frameSequence,id=selected;
      const verify=surface.verifiedDeliveredJPEG || (typeof require==="function"?require("./delivered-jpeg.js").verifiedDeliveredJPEG:null);
      if (!verify) throw new Error("Не загружен общий JPEG verifier");
      const packet=await verify(path,expected,options);
      if (epoch!==generation || id!==selected || token!==frameSequence || !visible) return {ignored:true};
      const url=options.makeObjectURL ? options.makeObjectURL(packet.bytes) : surface.URL.createObjectURL(new Blob([packet.bytes],{type:"image/jpeg"}));
      if(frame?.url)(options.revokeObjectURL || surface.URL?.revokeObjectURL)?.(frame.url);
      frame={...packet,bytes:undefined,url,token,kind,displayed:false,observed:false,loadedAt:now()};notify();
      if (options.displayFrame && await options.displayFrame(url,copy(frame))===true && epoch===generation) await displayed(token);
      return snapshot();
    }
    async function requestChallenge() {
      if (!attempt() || busy || owner || !visible) throw new Error("Выберите подготовленный показ и отдельное новое наблюдение");
      const epoch=generation;busy="challenge";releaseFrame();challenge=null;flags=affirmations();notify();
      try {
        const result=await options.post(`${route()}/challenge`,{});
        if(epoch!==generation)return {ignored:true};
        if(!ID.test(result.challenge_id || "") || !result.control_epoch || !SHA.test(result.frame?.jpeg_sha256 || ""))throw new Error("Не получен исходный challenge кадра");
        challenge=result;return await loadFrame(`${route()}/challenge/${result.challenge_id}/frame`,"challenge",result.frame,epoch);
      } catch(failure){if(epoch===generation)error=failure.message;throw failure;}
      finally {if(epoch===generation){busy="";notify();}}
    }
    function fresh(kind) {return frame?.kind===kind && frame.displayed && now()-frame.loadedAt>=0 && now()-frame.loadedAt<=1500;}
    async function authorize() {
      if (busy || !attempt() || !visible || !challenge || !fresh("challenge") || !Object.values(flags).every(value=>value===true)) throw new Error("Покажите свежий кадр и явно подтвердите наблюдение и рабочую область");
      const epoch=generation,row=copy(attempt()),body={...copy(flags),challenge_id:challenge.challenge_id,displayed_jpeg_sha256:frame.jpeg_sha256,expected_control_epoch:challenge.control_epoch};
      busy="authorize";readSequence++;notify();
      try {
        const receipt=checkedGrant(await options.post(`${route(row.demonstration_id)}/authorize`,body),row);
        if(epoch!==generation || !visible){await cleanup(receipt,"revoke");return {ignored:true};}
        if(receipt.control_epoch!==body.expected_control_epoch || receipt.displayed_frame?.jpeg_sha256!==body.displayed_jpeg_sha256)throw new Error("Разрешение не связано с показанным кадром");
        owner=receipt;ownerRevision++;readSequence++;watching=true;nextWatchAt=now();uncertainControl=false;releaseFrame();challenge=null;return copy(receipt);
      } catch(failure){if(epoch===generation)error=failure.message;throw failure;}
      finally {if(epoch===generation){busy="";notify();}}
    }
    async function control(operation) {
      if (busy || !["start","recenter","stop"].includes(operation)) throw new Error("Дождитесь предыдущей команды");
      const receipt=requireOwner(operation==="start"?"idle":operation==="recenter"?"teleoperation":undefined),epoch=generation,row=copy(attempt());
      if(operation==="recenter" && ["RECORDING","PAUSED"].includes(status?.capture?.capture?.state))throw new Error("Перед сменой опорной позы завершите или отмените текущую запись");
      ownerRevision++;readSequence++;releaseFrame();
      busy=operation;notify();
      try {
        const result=operation==="stop" ? await cleanup(receipt,"stop") : await options.post(`${route(receipt.attempt)}/${operation}`,{grant_id:receipt.grant_id,expected_control_epoch:receipt.control_epoch});
        if (operation==="stop") {
          if(!result.confirmed)throw new Error(result.message || "STOP источника не подтверждён");
          if(epoch===generation){readSequence++;dropOwner();if(status && result.response.status)status.station=result.response.status;}return result.response;
        }
        const returned=checkedGrant(result.grant,row);
        if (returned.grant_id!==receipt.grant_id)throw new Error("Native ответ сменил owner");
        if(epoch!==generation || !visible){await cleanup(returned);return {ignored:true};}
        owner=returned;ownerRevision++;readSequence++;nextWatchAt=now();if(status && result.status)status.station=result.status;return result;
      } catch(failure){if(operation!=="stop")uncertainScopes.set(scopeKey(receipt),receipt);if(epoch===generation){uncertainControl=true;dropOwner();error=failure.message;}throw failure;}
      finally {if(epoch===generation){busy="";notify();}}
    }
    async function watchOnce() {
      if (!watching || !owner || !visible || busy || watchBusy || !flags.observing) return {ignored:true};
      nextWatchAt=now()+300;
      if (frame?.kind==="observation" && !frame.displayed) return {ignored:true};
      const epoch=generation,revision=ownerRevision,receipt=copy(owner),id=selected;watchBusy=true;
      try {
        const reply=await options.get(`${route(id)}/observation`),packet=reply.packet?.observation;
        if(epoch!==generation || revision!==ownerRevision || busy)return {ignored:true};
        if(!packet || packet.frame_boot_id!==receipt.boot_id || packet.follower_calibration_sha256!==receipt.follower_calibration_sha256 || packet.camera_id!==receipt.displayed_frame?.camera_id)throw new Error("Источник наблюдения изменился");
        if(observedFrames.has(packet.frame_id))return {ignored:true};
        return await loadFrame(reply.frame_url,"observation",{jpeg_sha256:packet.frame_sha256,frame_id:packet.frame_id,boot_id:packet.frame_boot_id},epoch);
      } catch(failure){if(epoch===generation){error=failure.message;dropOwner();notify();}throw failure;}
      finally{if(epoch===generation){watchBusy=false;notify();}}
    }
    async function displayed(token) {
      if(!frame || frame.token!==token || frame.displayed || !visible)return {ignored:true};
      frame.displayed=true;notify();
      if(frame.kind!=="observation" || !fresh("observation") || !watching || !owner || !flags.observing || frame.observed)return {ignored:true};
      const epoch=generation,revision=ownerRevision,receipt=copy(owner),shown=frame;shown.observed=true;
      if(observedFrames.has(shown.frame_id))return {ignored:true};
      observedFrames.add(shown.frame_id);if(observedFrames.size>64)observedFrames.delete(observedFrames.values().next().value);
      try {
        const result=await options.post(`${route(receipt.attempt)}/observe`,{grant_id:receipt.grant_id,frame_id:shown.frame_id,displayed_jpeg_sha256:shown.jpeg_sha256,expected_control_epoch:receipt.control_epoch,observing:true});
        if(epoch!==generation || revision!==ownerRevision)return {ignored:true};
        const returned=checkedGrant(result,attempt());
        if(returned.grant_id!==receipt.grant_id || returned.control_epoch!==receipt.control_epoch)throw new Error("Наблюдение сменило owner/epoch");
        owner=returned;return result;
      } catch(failure){if(epoch===generation && revision===ownerRevision){dropOwner();error=failure.message;notify();}throw failure;}
    }
    async function dataOperation(operation,body={}) {
      if(dataBusy)throw new Error("Дождитесь предыдущего действия с записью");
      const row=attempt(),id=selected,epoch=generation;if(!row)throw new Error("Выберите серверный показ");
      if(operation==="capture-start") {
        if(busy)throw new Error("Дождитесь подтверждения текущей команды управления");
        const receipt=requireOwner("teleoperation");
        if(row.capture_start_pending || row.acknowledged_recording_id)throw new Error("Этот start уже ожидает подтверждения или сохранён; не повторяйте его");
        body={grant_id:receipt.grant_id,expected_control_epoch:receipt.control_epoch};
      } else if(operation==="capture-reconcile") {
        const candidate=snapshot().recoveryCandidate;
        if(!row.capture_start_pending || !candidate)throw new Error("Нет подходящей записи для явного восстановления DATA");
        body={recording_id:candidate,acknowledge_data_only_recovery:true};
      } else {
        if(!ID.test(row.acknowledged_recording_id || ""))throw new Error("Не подтверждён исходный ID записи; выполните data reconciliation");
        body={...body,recording_id:row.acknowledged_recording_id};
        if(operation==="review") {
          if(!SHA.test(archiveSHA) || body.decision==="approve" && !previewSeen || !["approve","reject"].includes(body.decision))throw new Error("Просмотрите исходный кадр точного архива перед отдельным review");
          body.expected_archive_sha256=archiveSHA;
        }
      }
      if(!["capture-start","capture-reconcile","pause","resume","cancel","finish","stage","review"].includes(operation))throw new Error("Неизвестная операция данных");
      dataBusy=operation;error="";notify();
      try {const result=await options.post(`${route(id)}/${operation}`,body);if(epoch===generation)await refresh();return result;}
      catch(failure){if(epoch===generation){error=failure.message;if(operation==="capture-start")listing=listing.map(value=>value.demonstration_id===id?{...value,capture_start_pending:true}:value);}throw failure;}
      finally{if(epoch===generation){dataBusy="";notify();}}
    }
    return {snapshot,refresh,setContext,selectAttempt,prepare,requestChallenge,authorize:()=>tracked(authorize),watchOnce,displayed,interrupt,drain,
      start:()=>tracked(()=>control("start")),recenter:()=>tracked(()=>control("recenter")),stop:()=>tracked(()=>control("stop")),
      retryCleanup:()=>lastCleanup ? cleanup(lastCleanup.scope,lastCleanup.operation) : Promise.resolve({confirmed:true,not_needed:true}),
      captureStart:()=>dataOperation("capture-start"),reconcile:()=>dataOperation("capture-reconcile"),
      pause:()=>dataOperation("pause"),resume:()=>dataOperation("resume"),cancel:()=>dataOperation("cancel"),
      finish:outcome=>dataOperation("finish",{outcome}),stage:phase_id=>{if(!phases().includes(phase_id))throw new Error("Выберите фазу текущего SkillSpec");return dataOperation("stage",{phase_id,request_id:newID()});},
      review:decision=>dataOperation("review",{decision}),
      previewDisplayed(recordingID,checksum){if(previewSeen || recordingID!==attempt()?.acknowledged_recording_id || !SHA.test(checksum || "") || !Object.values(manifest?.original_jpegs_sha256 || {}).includes(checksum))return {ignored:true};previewSeen=true;notify();},
      selectPreview(index){if(!Number.isInteger(index) || index<0 || index>=Object.keys(manifest?.original_jpegs_sha256 || {}).length)throw new Error("Выберите исходный кадр записи");previewIndex=index;notify();},
      selectPhase(value){if(!phases().includes(value))throw new Error("Выберите объявленную фазу навыка");phase=value;notify();},
      setPhaseCatalogue(spec,values){const key=specKey(spec),next=rows(values).filter(value=>typeof value==="string");if(JSON.stringify(phaseCatalogues.get(key))===JSON.stringify(next))return;phaseCatalogues.set(key,next);if(!phases().includes(phase))phase=phases()[0] || "";notify();},
      affirm(key,value){if(!(key in flags) || typeof value!=="boolean")throw new Error("Нужно явное подтверждение текущего наблюдения");flags[key]=value;if(!value && owner)return interrupt("Наблюдение отозвано");notify();},
      setVisible(value){const next=value===true;if(next===visible)return snapshot();visible=next;if(!visible)return interrupt("Наблюдение выключено при уходе из окна");notify();},
      destroy:()=>{visible=false;return interrupt("Панель закрыта");}};
  }
  const esc=value=>String(value ?? "").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  function sourceDemonstrationRenderKey(view) {
    const owner=view.owner?{...view.owner}:null;
    if(owner)for(const key of ["displayed_frame","observed_monotonic_ns","observation_deadline_ns","remaining_observation_s"])delete owner[key];
    const native=view.status?.station,capture=view.status?.capture;
    return JSON.stringify({...view,owner,watchBusy:undefined,watchDelayMs:undefined,
      frame:view.frame?{kind:view.frame.kind,displayed:view.frame.kind==="challenge"?view.frame.displayed:true}:null,
      status:view.status?{runtime_admission:view.status.permission?.runtime_admission,station:{available:native?.available,mode:native?.mode,control_epoch:native?.control_epoch,error:native?.error,last_hold_error:native?.last_hold_error},
        capture:{recording_id:capture?.recording_id,state:capture?.capture?.state,recovery_required:capture?.recovery_required}}:null});
  }
  function patchSourceDemonstrationFrame(root,view) {
    const image=root.querySelector("img[data-source-demo-frame]");
    if(Boolean(image)!==Boolean(view.frame))return false;
    if(image){
      image.dataset.sourceDemoFrame=String(view.frame.token);
      if(image.getAttribute("src")!==view.frame.url)image.src=view.frame.url;
      const caption=image.closest("figure")?.querySelector("figcaption");
      if(caption)caption.textContent=view.frame.displayed?"Кадр действительно показан":"Ждём загрузки видимого кадра";
    }
    const counter=root.querySelector("[data-source-demo-samples]");
    if(counter)counter.textContent=Number.isInteger(view.status?.station?.recording?.queued_samples)?` · получено измерений: ${view.status.station.recording.queued_samples}`:"";
    return true;
  }
  function sourceDemonstrationAdvancedPanel(view,{phaseTitle=value=>value}={}) {
    const row=rows(view.listing).find(value=>value.demonstration_id===view.selected),body=row?.record_start_body,prepareBody=view.context?.record_start_body;
    const recording=view.status?.capture?.capture,state=recording?.recording_id===view.recordingID ? recording.state : view.manifest?.state;
    const active=["RECORDING","PAUSED"].includes(state),dataBlocked=Boolean(view.dataBusy || view.status?.capture?.recovery_required);
    const semanticBlocked=view.status?.permission?.runtime_admission?.native_commands_blocked===true;
    const nativeBlocked=Boolean(view.controlBusy || semanticBlocked),own=view.owner,files=Object.entries(view.manifest?.original_jpegs_sha256 || {}).filter(([,sha])=>SHA.test(sha));
    const index=Math.min(view.previewIndex || 0,Math.max(0,files.length-1)),saved=files[index];
    const label={RECORDING:"Идёт запись",PAUSED:"Пауза записи для обучения",completed:"Запись сохранена",cancelled:"Показ отменён · raw сохранены",interrupted:"Показ прерван · raw сохранены"}[state] || "Запись ещё не запущена";
    const button=(action,title,disabled=false,kind="secondary")=>`<button type="button" class="button ${kind}" data-source-demo-action="${action}" ${disabled?"disabled":""}>${title}</button>`;
    const live=view.frame ? `<figure class="source-demo-live"><img class="camera-preview" data-source-demo-frame="${view.frame.token}" src="${esc(view.frame.url)}" alt="Проверенный исходный кадр единственной Logitech на Raspberry Pi"><figcaption>${view.frame.displayed?"Кадр действительно показан":"Ждём загрузки видимого кадра"}</figcaption></figure>` : '<p class="form-hint">Запросите исходный кадр. Просмотр статуса и возвращение во вкладку не дают разрешения управления.</p>';
    const checkboxes=[["observing","Я рядом и сейчас наблюдаю за руками"],["robot_workspace_checked","Рабочая область свободна и проверена"],["user_observed_test_confirmed","Подтверждаю текущий наблюдаемый тест"]].map(([key,title])=>`<label class="check-field"><input type="checkbox" data-source-demo-affirm="${key}" ${view.flags?.[key]?"checked":""}>${title}</label>`).join("");
    const sourceReview=view.review?.source_review?.decision;
    return `<section class="panel spaced-section source-demonstration"><div class="panel-heading"><div><p class="eyebrow">04 / Показ ведущей рукой</p><h2>Контекст → наблюдение → запись → разбор</h2></div></div><div class="panel-body">
      <p><strong>Следующий новый показ:</strong> ${prepareBody?`${esc(prepareBody.name || prepareBody.task || "Навык")} · версия ${esc(prepareBody.skill_version)} · ${esc(prepareBody.scene_id || "выбранная сцена")} · ${esc(prepareBody.context?.object_instance_id || "предмет")}`:"выберите подтверждённый контекст в мастере навыка."}</p>
      ${body?`<p><strong>Просматриваемый показ:</strong> ${esc(body.name || body.task || "Навык")} · версия ${esc(body.skill_version)} · ${esc(body.scene_id || "выбранная сцена")}</p>`:""}
      ${(view.prepareUncertain && !prepareBody) || view.preservedPrepareRequests?'<p>Неподтверждённые запросы подготовки сохранены отдельно. Для повторной проверки выберите их исходный контекст в мастере; чужой контекст не подставляется автоматически.</p>':""}
      <div class="teleop-actions">${button("prepare",view.prepareUncertain?"Проверить тот же запрос подготовки":"Подготовить новый показ",!view.context || Boolean(own) || Boolean(view.busy))}${button("refresh","Обновить состояние",Boolean(view.controlBusy))}</div>
      <label>Подготовленный показ <select data-source-demo-select><option value="">Выберите показ</option>${rows(view.listing).map(value=>`<option value="${esc(value.demonstration_id)}" ${value.demonstration_id===view.selected?"selected":""}>${esc(value.record_start_body?.name || value.record_start_body?.task || "Показ")} · ${esc(value.record_start_body?.scene_id || "сессия")}${value.acknowledged_recording_id?" · есть запись":""}</option>`).join("")}</select></label>
      ${live}<div class="teleop-actions">${button("challenge","Показать свежий кадр",!row || nativeBlocked || Boolean(own))}${button("authorize","Подтвердить наблюдение",!row || !view.challenge || !view.frame?.displayed || !Object.values(view.flags || {}).every(Boolean) || nativeBlocked || !view.visible,"primary")}</div>${checkboxes}
      ${semanticBlocked?'<p role="status">Анализ сцены ещё выполняется или освобождение его ресурсов не подтверждено. После завершения потребуется свежее наблюдение перед управлением.</p>':""}
      <div class="teleop-actions">${button("start","Начать повторение",nativeBlocked || own?.native_mode!=="idle","primary")}${button("recenter","Текущая поза — начало",nativeBlocked || own?.native_mode!=="teleoperation" || active)}${button("stop","STOP этого управления",!own || Boolean(view.controlBusy),"danger")}</div>
      <p role="status">${own ? own.native_mode==="teleoperation"?"Вкладка управляет повторением по вашему наблюдаемому разрешению.":"Наблюдение разрешено. Повторение включается отдельным действием." : "Локального права управления нет."} ${view.watching?"Подтверждаются только новые кадры, реально показанные на видимой странице.":"Наблюдение выключено."}</p>
      ${!own && view.status?.station?.mode==="teleoperation"?'<p>Источник сообщает об активном повторении. Эта вкладка его не присвоила.</p>':""}
      <div class="teleop-actions">${button("capture-start","Начать запись",!own || own.native_mode!=="teleoperation" || nativeBlocked || dataBlocked || Boolean(view.recordingID) || view.capturePending)}${button("pause","Пауза записи",dataBlocked || state!=="RECORDING")}${button("resume","Продолжить запись",dataBlocked || state!=="PAUSED")}${button("cancel","Отменить показ · сохранить raw",dataBlocked || !active)}</div>
      <p role="status"><strong>${esc(label)}</strong><span data-source-demo-samples>${Number.isInteger(view.status?.station?.recording?.queued_samples)?` · получено измерений: ${view.status.station.recording.queued_samples}`:""}</span></p>
      ${active?`<label>Фаза навыка <select data-source-demo-phase><option value="">Выберите фазу</option>${rows(view.phases).map(value=>`<option value="${esc(value)}" ${value===view.phase?"selected":""}>${esc(phaseTitle(value))}</option>`).join("")}</select></label>${button("stage","Отметить фазу",dataBlocked || !view.phase)}<p>Пауза исключает эти образцы из обучения и сохраняет исходную запись. Управление и завершение записи — отдельные действия.</p><div class="teleop-actions">${button("finish-success","Завершить · получилось",dataBlocked)}${button("finish-failure","Завершить · не получилось",dataBlocked)}${button("finish-unknown","Завершить · без оценки",dataBlocked)}</div>`:""}
      ${view.capturePending?`<p role="alert">Ответ start записи не подтверждён. Повторный start заблокирован.</p>${button("reconcile","Восстановить связь с найденной записью",!view.recoveryCandidate || dataBlocked)}`:""}
      ${saved?`<figure><img class="camera-preview" data-source-demo-preview="${esc(view.recordingID)}" data-source-demo-sha="${esc(saved[1])}" src="${BASE}/${esc(view.selected)}/capture/frame/${saved[1]}" alt="Исходный сохранённый кадр показа"><figcaption>Исходный кадр ${index+1} из ${files.length}. Исходные кадры включают паузы.</figcaption></figure><label>Кадр записи <input type="range" min="0" max="${files.length-1}" value="${index}" data-source-demo-preview-index></label>`:""}
      ${view.manifest?`<p>Результат оператора: ${esc(view.manifest.operator_outcome || "unknown")}. Разбор переноса: ${esc(sourceReview || "ещё не выбран")}.</p><div class="teleop-actions">${button("approve","Разрешить перенос этого архива",dataBlocked || view.manifest.state!=="completed" || !view.archiveSHA || !view.previewSeen,"primary")}${button("reject","Отклонить этот архив",dataBlocked || !view.archiveSHA)}</div><p class="form-hint">Завершение записи не выключает повторение. Управление останавливается отдельной кнопкой STOP. Перенос разрешается отдельным одобрением. Приёмка целого навыка и разметка фаз выполняются в мастере после импорта.</p>`:""}
      ${rows(view.importJobs).length?`<p>Перенос этого архива: <a href="/queue" data-nav>общая очередь</a>.</p><ul>${rows(view.importJobs).map(job=>`<li>${esc(job.id)} · ${esc(job.status)} · ${sourceReview==="approve" && job.config.source_review_sha256===view.review?.review_sha256?"текущее одобрение":"предыдущее решение · допуск по нему закрыт"}</li>`).join("")}</ul>`:""}
      ${view.error?`<p class="form-error" role="alert">${esc(view.error)}</p>`:""}
      ${view.cleanupUnconfirmed?`<p role="alert">Завершение своего управления не подтверждено. Вход сохраняется до завершения исходной команды или проверки источника.</p>${button("retry-cleanup","Повторить scoped STOP этого сеанса",Boolean(view.controlBusy))}`:""}
      ${view.status?.capture?.recovery_required?'<p role="alert">Источник требует восстановления записи. Неопределённые raw и журнал сохранены; вкладка не снимает этот gate автоматически.</p>':""}
      </div></section>`;
  }
  function sourceDemonstrationPanel(view){
    const active=view.owner?.native_mode==="teleoperation";
    return `<section class="panel"><div class="panel-heading"><h2>Управление руками</h2></div><div class="panel-body"><p>Ведущая рука управляет ведомой.</p><button class="button primary" data-operator-action="${active?"source-disconnect":"source-control"}" ${view.busy?"disabled":""}>${active?"Отключить управление":"Управлять"}</button>${active?`<button class="button secondary" data-source-demo-action="recenter">Текущая поза — начало</button>`:""}${view.error?`<p class="form-error" role="alert">${esc(view.error)}</p>`:""}</div></section>`;
  }
  function bindSourceDemonstration(root,client,{onError=()=>{},document=surface.document,window=surface}={}) {
    let disposed=false,timer=null;
    const clear=()=>{if(timer!==null)(window.clearTimeout || clearTimeout)(timer);timer=null;};
    const schedule=()=>{clear();const view=client.snapshot();if(!disposed && view.watching && view.visible && !view.controlBusy && !view.watchBusy)timer=(window.setTimeout || setTimeout)(()=>run(()=>client.watchOnce()),view.watchDelayMs);};
    const run=async operation=>{try{return await operation();}catch(error){onError(error);}finally{if(!disposed)schedule();}};
    const click=event=>{
      const button=event.target.closest?.("button[data-source-demo-action]");if(!button || !root.contains(button) || button.disabled)return;
      const action=button.dataset.sourceDemoAction;
      const functions={prepare:()=>client.prepare(),refresh:()=>client.refresh(),challenge:()=>client.requestChallenge(),authorize:()=>client.authorize(),
        start:()=>client.start(),recenter:()=>client.recenter(),stop:()=>client.stop(),"capture-start":()=>client.captureStart(),pause:()=>client.pause(),resume:()=>client.resume(),cancel:()=>client.cancel(),
        reconcile:()=>client.reconcile(),"retry-cleanup":()=>client.retryCleanup(),stage:()=>client.stage(client.snapshot().phase),"finish-success":()=>client.finish("success"),"finish-failure":()=>client.finish("failure"),"finish-unknown":()=>client.finish("unknown"),
        approve:()=>client.review("approve"),reject:()=>client.review("reject")};
      if(functions[action])run(functions[action]);
    };
    const change=event=>{
      const element=event.target;if(!root.contains(element) || element.disabled)return;
      if(element.hasAttribute?.("data-source-demo-select")){if(element.value)run(async()=>{await client.selectAttempt(element.value);await client.refresh();});}
      else if(element.dataset?.sourceDemoAffirm)run(()=>client.affirm(element.dataset.sourceDemoAffirm,element.checked));
      else if(element.hasAttribute?.("data-source-demo-phase"))run(()=>client.selectPhase(element.value));
      else if(element.hasAttribute?.("data-source-demo-preview-index"))run(()=>client.selectPreview(Number(element.value)));
    };
    const loaded=event=>{
      const image=event.target,visibleImage=surface.deliveredImageVisible || (typeof require==="function"?require("./delivered-jpeg.js").deliveredImageVisible:null);
      if(!visibleImage || !visibleImage(image,root,{document,window}))return;
      if(image.dataset?.sourceDemoFrame!=null)run(()=>client.displayed(Number(image.dataset.sourceDemoFrame)));
      else if(image.dataset?.sourceDemoPreview)client.previewDisplayed(image.dataset.sourceDemoPreview,image.dataset.sourceDemoSha);
    };
    const scrolled=()=>{for(const image of root.querySelectorAll?.("img[data-source-demo-frame], img[data-source-demo-preview]") || [])loaded({target:image});};
    const lost=()=>{clear();client.setVisible(false);};
    const focus=()=>{if(document?.visibilityState!=="hidden")client.setVisible(true);};
    const visibility=()=>{if(document?.visibilityState==="hidden")lost();else focus();};
    root.addEventListener("click",click);root.addEventListener("change",change);root.addEventListener("load",loaded,true);
    document?.addEventListener("visibilitychange",visibility);window?.addEventListener("blur",lost);window?.addEventListener("pagehide",lost);window?.addEventListener("focus",focus);window?.addEventListener("scroll",scrolled,{passive:true});
    schedule();
    return ()=>{disposed=true;clear();root.removeEventListener("click",click);root.removeEventListener("change",change);root.removeEventListener("load",loaded,true);document?.removeEventListener("visibilitychange",visibility);window?.removeEventListener("blur",lost);window?.removeEventListener("pagehide",lost);window?.removeEventListener("focus",focus);window?.removeEventListener("scroll",scrolled);};
  }
  const exported={createSourceDemonstrationClient,sourceDemonstrationPanel,bindSourceDemonstration,sourceDemonstrationRenderKey,patchSourceDemonstrationFrame};Object.assign(surface,exported);
  if(typeof module!=="undefined" && module.exports)module.exports=exported;
})(typeof window!=="undefined"?window:globalThis);
