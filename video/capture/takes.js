// Records clean 1920x1080 browser takes of the live app (local server, live Gemini + live 1Click). No payments.
const {chromium}=require('playwright');
const BASE=process.env.BASE||'http://localhost:8765', OUT=process.argv[3], TAKE=process.argv[2];
const REFUND='5aCi9yjf1SM3tbnU8ZP9hxMoZKmjvLXuNCuKcj87X8od';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const b=await chromium.launch();
 const ctx=await b.newContext({viewport:{width:1920,height:1080},deviceScaleFactor:1,recordVideo:{dir:OUT,size:{width:1920,height:1080}}});
 const QR=process.env.QRJS; if(QR) await ctx.route('**/qrcodejs/**',r=>r.fulfill({path:QR,contentType:'application/javascript'}));
 await ctx.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>{document.documentElement.style.zoom='1.45';}));
 const pg=await ctx.newPage();
 const sid=process.env.SID; if(sid) await ctx.addInitScript(s=>localStorage.setItem('aishtat_sid',s),sid);
 const t0=Date.now(), mark=n=>console.log(n,((Date.now()-t0)/1000).toFixed(1));
 if(TAKE==='A'){
  await pg.goto(BASE+'/'); await sleep(2500); mark('loaded');
  await pg.click('#text'); await pg.keyboard.type('Привет! Хочу купить один кофе, можно оплатить в SOL?',{delay:55}); await sleep(500);
  mark('send'); await pg.keyboard.press('Enter');
  await pg.waitForSelector('.invoice select',{timeout:90000}); mark('invoice'); await sleep(2500);
  const v=await pg.$eval('.invoice select',s=>[...s.options].find(o=>o.textContent.trim()==='SOL on Solana').value);
  await pg.selectOption('.invoice select',v); await sleep(1200);
  await pg.click('.invoice input'); await pg.keyboard.type(REFUND,{delay:18}); await sleep(800);
  mark('getaddr'); await pg.click('.invoice button.primary');
  await pg.waitForSelector('.deposit',{timeout:60000}); mark('deposit');
  await pg.$eval('.deposit',e=>e.scrollIntoView({block:'center'})); await sleep(7000);
  console.log('SID', await pg.evaluate(()=>localStorage.getItem('aishtat_sid')));
 } else if(TAKE==='B'){
  await pg.goto(BASE+'/'); mark('loaded');
  await pg.waitForSelector('.paid',{timeout:60000}); mark('paid'); await sleep(6000);
 } else if(TAKE==='D'){
  await pg.goto(BASE+'/'); await sleep(2000); mark('chat');
  await pg.click('#text'); await pg.keyboard.type('Can I talk to a manager?',{delay:55}); await pg.keyboard.press('Enter');
  await pg.waitForFunction(()=>!document.querySelector('.typing'),null,{timeout:90000}); mark('handoff'); await sleep(3000);
  await pg.goto(BASE+'/merchant'); await sleep(3500); mark('merchant');
  await pg.click('.h'); await sleep(2500); mark('opened');
  await pg.click('#reply'); await pg.keyboard.type('Hi! Sam here. Thanks for the coffee, the next one is on us.',{delay:45}); await sleep(600);
  await pg.click('#send'); mark('replied'); await sleep(2500);
  await pg.goto(BASE+'/'); await sleep(6000); mark('back');
 }
 await ctx.close(); await b.close();
})();
