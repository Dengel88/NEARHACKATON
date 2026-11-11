const {chromium}=require('playwright');const fs=require('fs');
(async()=>{const [,,mode,out]=process.argv;const b=await chromium.launch();const pg=await b.newPage({viewport:{width:1920,height:1080}});
await pg.goto('file://'+__dirname+'/index.html');await pg.evaluate(()=>document.fonts.ready);
fs.mkdirSync(out,{recursive:true});
if(mode==='stills'){for(const t of [2.5,7.5,11.5,14.5,17.5,26.5,36,44,53]){await pg.evaluate(t=>seek(t),t);await pg.screenshot({path:`${out}/t${t}.png`});}}
else{const fps=30,n=Math.round(56*fps);for(let i=0;i<n;i++){await pg.evaluate(t=>seek(t),i/fps);await pg.screenshot({path:`${out}/f_${String(i).padStart(5,'0')}.png`});}}
await b.close();})();
