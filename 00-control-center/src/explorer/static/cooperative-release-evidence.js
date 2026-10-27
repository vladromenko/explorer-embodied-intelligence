"use strict";
// ENGINEERING_GLUE: explicit USER control after three new server questions.
(function (surface) {
  const statements=["receiver_holds_object","giver_stationary","receiver_stationary"];
  const id=/^[0-9a-f]{32}$/,sha=/^[0-9a-f]{64}$/;
  function releaseEvidencePins(pending,sessions,previous,draft) {
    const next={};
    for (const row of pending || []) {
      if (row.kind!=="parked_release_bundle") continue;
      const session=sessions.find(value=>value.session_id===row.run_id);
      const pin=JSON.stringify([row.run_id,row.session_id,row.object_id,row.source_challenge_sha256,row.deadline_ns,session?.epoch]);
      next[row.bundle_id]=pin;
      if (previous[row.bundle_id]!==pin) delete draft.evidence[row.bundle_id];
    }
    for (const identity of Object.keys(previous)) if (!Object.hasOwn(next,identity)) delete draft.evidence[identity];
    return next;
  }
  function parkedReleaseBody(session,pending,identity,proof) {
    const row=(pending || []).find(value=>value.bundle_id===identity && value.challenge_id===identity &&
      value.kind==="parked_release_bundle" && value.run_id===session?.session_id && value.state==="pending");
    if (!row || !id.test(identity) || !sha.test(row.source_challenge_sha256 || "") ||
        session?.execution_domain!=="user_observed_physical_test" || session?.pending_operation?.event!=="release_requested" ||
        !["RECEIVER_GRASP_CHECK","RELEASE_AUTHORIZED"].includes(session.protocol_state) ||
        proof?.observing!==true || proof?.neutral!==true || proof?.user_observed_test_confirmed!==true ||
        statements.some(key=>proof?.affirmations?.[key]!==true)) {
      throw new Error("Нужны три новых наблюдения и явное разрешение возобновить отпускание для текущего запроса");
    }
    return {session_id:session.session_id,expected_epoch:session.epoch,release_bundle_id:identity,
      affirmations:Object.fromEntries(statements.map(key=>[key,true])),observing:true,neutral:true,user_observed_test_confirmed:true};
  }
  function parkedReleasePanel(row,proof,{check,button,esc,disabled}) {
    const prefix=`evidence.${row.bundle_id}`;
    const questions=statements.map(key=>check(`${prefix}.affirmations.${key}`,row.questions?.[key]?.question || {
      receiver_holds_object:"Принимающий робот удерживает предмет",giver_stationary:"Подающая рука неподвижна",receiver_stationary:"Принимающая рука неподвижна"}[key],proof?.affirmations?.[key])).join("");
    return `<fieldset data-coop-release-bundle="${esc(row.bundle_id)}"><legend>Отдельно разрешить отпускание</legend>${questions}${check(`${prefix}.observing`,"Наблюдаю обе руки и предмет сейчас",proof?.observing)}${check(`${prefix}.neutral`,"Органы ручного управления в нейтрали",proof?.neutral)}${check(`${prefix}.user_observed_test_confirmed`,"Разрешаю возобновить управление под наблюдением для отпускания",proof?.user_observed_test_confirmed)}${button("release-resume","Подтвердить наблюдения и возобновить отпускание",disabled,`data-coop-bundle="${esc(row.bundle_id)}"`)}${button("stop","Не могу подтвердить · STOP",false)}<p class="form-hint">Окно подтверждения короткое. Если оно истекло, сервер отклонит запрос. Подтверждение не означает, что захват уже раскрылся.</p></fieldset>`;
  }
  const api={releaseEvidencePins,parkedReleaseBody,parkedReleasePanel};
  Object.assign(surface,api);if (typeof module!=="undefined" && module.exports) module.exports=api;
})(typeof globalThis!=="undefined" ? globalThis : this);
