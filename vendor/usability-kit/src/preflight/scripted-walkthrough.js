'use strict';
/* A deterministic browser walkthrough for known controls. It performs real
 * actions and assertions; there is no model, credential or inferred pass. */
const path=require('node:path');
const {pageSlug}=require('./report');
const {scanOverlaysInPage}=require('./checks/overlays');
const {launchArgs}=require('./media');
const {expandDates}=require('./step-dates');
async function action(page,step){
 if(step==='reload')return page.reload({waitUntil:'load'});
 const pause=/^wait (\d{1,2}) seconds?$/.exec(step);
 if(pause)return page.waitForTimeout(Number(pause[1])*1000);
 const match=/^(click|check|wait-visible|expect-text|expect-url) (.+)$/.exec(step);
 if(!match)throw new Error('Unsupported scripted action');
 const [,kind,arg]=match;
 if(kind==='click')return page.locator(arg).click({timeout:15000});
 if(kind==='check')return page.locator(arg).check({timeout:15000});
 if(kind==='wait-visible')return page.locator(arg).waitFor({state:'visible',timeout:20000});
 if(kind==='expect-url')return page.waitForURL(u=>String(u).includes(arg),{timeout:15000});
 const [selector,expected]=arg.split(' | ');
 if(!selector||!expected)throw new Error('expect-text requires selector | literal text');
 await page.waitForFunction(({selector,expected})=>document.querySelector(selector)?.textContent.includes(expected),{selector,expected},{timeout:20000});
}
async function runScripted({config,opts,baseUrl,add,skipped,shotsDir,walkthroughs}){
 const list=(config.walkthroughs||[]).filter(w=>w.driver==='playwright');
 if(!list.length)return;
 let browser;
 try{
  const {chromium}=require('playwright');
  const launch=opts.scriptedLaunchOptions||{},media=launchArgs(list);
  browser=await chromium.launch(media.args?{...launch,args:[...(launch.args||[]),...media.args]}:launch);
  for(const w of list){
   const context=await browser.newContext(config.language?{locale:config.language}:{});let failed=false,ran=false;
   const flag=detail=>{failed=true;add({check:'walkthrough',severity:'serious',page:w.start,role:w.role||'public',title:'Scripted walkthrough failed',detail,selector:''});};
   try{
    const setup=opts.setupContext||opts.setupScriptedContext;
    if(setup)await setup(context,w);
    const page=await context.newPage();
    await page.goto(baseUrl+w.start,{waitUntil:'load',timeout:45000});ran=true;
    const start=Date.now();
    for(const [i,step]of w.steps.entries()){
     try{await action(page,expandDates(step,{language:config.language}));}catch(e){flag(`${w.name}, step ${i+1}: ${String(e.message).split('\n')[0].slice(0,120)}`);break;}
     const overlays=await scanOverlaysInPage(page).catch(()=>[]);
     if(overlays.length>1)flag(`${w.name}: ${overlays.length} overlays open`);
     if(shotsDir)await page.screenshot({path:path.join(shotsDir,`walkthrough-${pageSlug(w.name)}-${i+1}.png`)}).catch(()=>{});
    }
    if(!failed&&w.expect){
     try{await action(page,w.expect);}catch(e){flag(`${w.name}: expectation not met`);}
    }
    walkthroughs.push({name:w.name,start:w.start,steps:w.steps.length,ran,ok:!failed,driver:'playwright',elapsedMs:Date.now()-start});
   }catch(e){flag(`${w.name}: browser navigation or setup failed`);walkthroughs.push({name:w.name,start:w.start,steps:w.steps.length,ran,ok:false,driver:'playwright'});}
   finally{await context.close();}
  }
 }catch(e){skipped.push({kind:'check',name:'walkthrough',check:'walkthrough',reason:'scripted browser unavailable'});}
 finally{await browser?.close();}
}
module.exports={runScripted,action};
