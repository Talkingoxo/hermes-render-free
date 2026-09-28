import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// Data-URL import keeps this test self-contained without a package.json.
const source=await readFile(new URL("./free-models.js",import.meta.url),"utf8");
const {runFreeModels,handleManagement}=await import("data:text/javascript;base64,"+Buffer.from(source).toString("base64"));

function context(){
  const state={providers:{}};
  const storage={getByName:()=>({fetch:async(req,init={})=>{
    if(init.method==="PUT")Object.assign(state,JSON.parse(init.body));
    return new Response(JSON.stringify({state}),{headers:{"content-type":"application/json"}});
  }})};
  return {state,env:{HERMES_STATE:storage,HERMES_EDGE_TOKEN:"unit-test-edge",HERMES_ADMIN_TOKEN:"unit-test-admin"}};
}
const payload={model:"auto",messages:[{role:"user",content:"Say OK"}],max_tokens:24};

test("automatic router switches to second keyless Cloudflare model",async()=>{
  const {env}=context(),calls=[];
  env.AI={run:async model=>{calls.push(model);if(calls.length===1)throw Error("3040 out of capacity");return {response:"OK"}}};
  const res=await runFreeModels(env,payload);
  assert.equal(res.status,200);const body=await res.json();
  assert.equal(body.provider,"cloudflare");assert.equal(body.choices[0].message.content,"OK");
  assert.equal(calls.length,2);assert.notEqual(calls[0],calls[1]);
});

test("account-wide quota skips duplicate calls against Cloudflare",async()=>{
  const {env}=context();let calls=0;
  env.AI={run:async()=>{calls++;throw Error("3036 free allocation exceeded")}};
  const res=await runFreeModels(env,payload);
  assert.equal(res.status,503);assert.equal(calls,1);
  const body=await res.json();assert.equal(body.attempts[0].error,"free_quota_exhausted");
});

test("external provider cannot be used without an explicitly configured key",async()=>{
  const {env}=context();const result=await runFreeModels(env,{...payload,model:"openrouter"});
  assert.equal(result.status,409);
  assert.match((await result.json()).error,/API key/);
});

test("enabled optional provider joins fallback; API key is never exposed in status",async()=>{
  const {env}=context();
  const req=new Request("https://hermes.test/api/admin/providers",{
    method:"PUT",
    headers:{"authorization":"Bearer unit-test-admin","content-type":"application/json","origin":"https://hermes.test"},
    body:JSON.stringify({id:"openrouter",model:"openrouter/free",enabled:true,apiKey:"test-private-key"})
  });
  const saved=await handleManagement(req,env,new URL(req.url));
  assert.equal(saved.status,200);

  const get=new Request("https://hermes.test/api/admin/providers",{headers:{"authorization":"Bearer unit-test-admin"}});
  const list=await handleManagement(get,env,new URL(get.url));
  assert.equal(list.status,200);assert.equal((await list.text()).includes("test-private-key"),false);

  env.AI={run:async()=>{throw Error("3040 busy")}};
  const oldFetch=globalThis.fetch;
  let count=0;
  globalThis.fetch=async(url,init)=>{
    count++;assert.equal(new URL(url).hostname,"openrouter.ai");
    assert.equal(init.headers.authorization,"Bearer test-private-key");
    return new Response(JSON.stringify({model:"openrouter/free",choices:[{message:{role:"assistant",content:"FREE OK"}}]}),{
      headers:{"content-type":"application/json"}
    });
  };
  try{
    const result=await runFreeModels(env,payload);
    assert.equal(result.status,200);
    const body=await result.json();
    assert.equal(body.provider,"openrouter");
    assert.equal(body.choices[0].message.content,"FREE OK");
    assert.equal(count,1);
  }finally{globalThis.fetch=oldFetch}
});

test("unknown model names are left for existing external model backends",async()=>{
  const {env}=context();assert.equal(await runFreeModels(env,{...payload,model:"custom-model"}),null);
});
