/* Real headless browser against the running UI. Never sends a motion/start request. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'validation','browser-20261107');fs.mkdirSync(output,{recursive:true});
const base=process.env.STUDIO_URL||'http://127.0.0.1:8020';
const engines=(process.env.STUDIO_BROWSERS||'chromium,webkit').split(',');
(async()=>{
 const report={schema:'so101.browser.acceptance.v1',started_at:new Date().toISOString(),url:base,hardware_commands_sent:0,actual_Mac_Safari:false,browsers:{}};
 let failed=false;
 for(const name of engines){
  let browser;
  const result={checks:[],page_errors:[],blocked_unsafe_requests:[],status:'running'};report.browsers[name]=result;
  try{
   browser=await ({chromium,webkit})[name].launch({headless:true});result.version=browser.version();
   const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});
   await context.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()==='POST'&&url.pathname!=='/api/preflight'){
     result.blocked_unsafe_requests.push(url.pathname);await route.abort();
    }else await route.continue();
   });
   const page=await context.newPage();page.on('pageerror',e=>result.page_errors.push(e.message));
   await page.goto(base,{waitUntil:'domcontentloaded'});
   await page.locator('#connection').filter({hasText:'Homebrain онлайн'}).waitFor();
   await page.waitForFunction(()=>document.querySelector('#top').naturalWidth===640&&document.querySelector('#wrist').naturalWidth===640);
   result.checks.push('actual_top_wrist_images_640px','websocket_connected');
   await page.screenshot({path:path.join(output,name+'-record-desktop.png'),fullPage:true});
   assert(await page.locator('#teleopStart').isVisible());
   assert.equal(await page.locator('#name').inputValue(),'');
   assert.equal(await page.locator('#dataset').inputValue(),'');
   result.checks.push('standalone_teleoperation_visible_without_dataset','no_auto_dataset_selection');
   await page.locator('#name').fill('Браузерная проверка — НЕ создавать');
   await page.locator('#task').fill('Pick up the cube and place it into the box');
   await page.locator('#count').fill('51');
   assert.equal(await page.locator('#count').inputValue(),'51');result.checks.push('episode_count_51_preserved');
   await page.locator('#preflightCheck').click();
   await page.locator('#preflightResult').filter({hasText:'Предзаписная проверка'}).waitFor();
   result.checks.push('readonly_preflight_rendered');
   for(const policy of ['act','smolvla']){
    await page.locator('[data-tab="train"]').click();
    await page.locator('#policyType').selectOption(policy);
    assert.equal(await page.locator('#policyType').inputValue(),policy);
    assert.equal(await page.locator('#denoising').isDisabled(),policy==='act');
    result.checks.push(policy+'_form_switch');
   }
   await page.locator('summary').filter({hasText:'Параметры обучения и сохранения'}).click();
   await page.screenshot({path:path.join(output,name+'-train-desktop.png'),fullPage:true});
   await context.setOffline(true);await page.evaluate(()=>ws.close());await page.locator('#connection').filter({hasText:'Переподключение'}).waitFor();await context.setOffline(false);
   await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('Homebrain онлайн'));
   result.checks.push('network_reconnect');
   await page.locator('[data-tab="record"]').click();
   for(const width of [1024,768,390]){
    await page.setViewportSize({width,height:900});
    await page.screenshot({path:path.join(output,name+'-record-'+width+'.png'),fullPage:true});
    const geometry=await page.evaluate(()=>({width:window.innerWidth,scroll:document.documentElement.scrollWidth,button:document.querySelector('#teleopStart').getBoundingClientRect().toJSON()}));
    assert(geometry.scroll<=width+1,`horizontal overflow ${geometry.scroll} > ${width}`);
    assert(geometry.button.width>0);result.checks.push('responsive_'+width);
   }
   assert.deepEqual(result.page_errors,[]);assert.deepEqual(result.blocked_unsafe_requests,[]);
   result.status='PASS';await context.close();
  }catch(error){failed=true;result.status='FAIL';result.error=error.stack||String(error)}
  finally{if(browser)await browser.close();fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2))}
 }
 report.finished_at=new Date().toISOString();report.status=failed?'FAIL':'PASS';
 fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));process.exitCode=failed?1:0;
})().catch(error=>{console.error(error);process.exitCode=1});
