import { DurableObject, WorkflowEntrypoint } from "cloudflare:workers";
import {handleManagement,directModel} from "./manager.js";

const json = (data, status = 200, extra = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });

const bearerOk = (request, env) => {
  const expected = env.HERMES_EDGE_TOKEN;
  if (!expected) return false;
  return request.headers.get("authorization") === `Bearer ${expected}`;
};

const normalizeLimit = (value, fallback = 100) => {
  const n = Number.parseInt(value || "", 10);
  return Number.isFinite(n) ? Math.min(Math.max(n, 1), 500) : fallback;
};

export class HermesState extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata TEXT
      );
      CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      );
    `);
  }

  _broadcast(payload) {
    const data = JSON.stringify(payload);
    for (const ws of this.ctx.getWebSockets()) {
      try { ws.send(data); } catch {}
    }
  }

  _append(role, content, metadata = null) {
    const ts = Date.now();
    const meta = metadata == null ? null : JSON.stringify(metadata);
    this.sql.exec(
      "INSERT INTO messages (ts, role, content, metadata) VALUES (?, ?, ?, ?)",
      ts, String(role), String(content), meta
    );
    const row = this.sql.exec(
      "SELECT id, ts, role, content, metadata FROM messages ORDER BY id DESC LIMIT 1"
    ).toArray()[0];
    const result = {
      ...row,
      metadata: row?.metadata ? JSON.parse(row.metadata) : null,
    };
    this._broadcast({ type: "message", message: result });
    return result;
  }

  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname.endsWith("/ws")) {
      if (request.headers.get("upgrade") !== "websocket") {
        return new Response("Expected WebSocket", { status: 426 });
      }
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      this.ctx.acceptWebSocket(server);
      server.serializeAttachment({ connectedAt: Date.now() });
      return new Response(null, { status: 101, webSocket: client });
    }

    if (request.method === "GET" && url.pathname.endsWith("/messages")) {
      const limit = normalizeLimit(url.searchParams.get("limit"));
      const rows = this.sql.exec(
        "SELECT id, ts, role, content, metadata FROM messages ORDER BY id DESC LIMIT ?",
        limit
      ).toArray().reverse().map((row) => ({
        ...row,
        metadata: row.metadata ? JSON.parse(row.metadata) : null,
      }));
      return json({ messages: rows });
    }

    if (request.method === "POST" && url.pathname.endsWith("/messages")) {
      const body = await request.json();
      if (!body || typeof body.role !== "string" || body.content == null) {
        return json({ error: "role and content are required" }, 400);
      }
      return json({ message: this._append(body.role, body.content, body.metadata ?? null) }, 201);
    }


    if (request.method === "POST" && url.pathname.endsWith("/chat")) {
      const body = await request.json();
      if (!body || body.content == null) {
        return json({ error: "content is required" }, 400);
      }

      const userMessage = this._append("user", body.content, body.metadata ?? null);
      const historyLimit = normalizeLimit(String(body.history_limit ?? 40), 40);
      const rows = this.sql.exec(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
        historyLimit
      ).toArray().reverse();

      const requestedModel = body.model || this.env.MODEL_NAME || "";
      if (/^(openrouter|gemini)(?:\/|$)/.test(requestedModel)) {
        const r=await directModel(this.env,{model:requestedModel,
          messages:rows.map(row=>({role:row.role,content:row.content})),
          stream:false,max_tokens:Math.min(Math.max(Number(body.max_tokens)||512,1),2048)});
        if(!r.ok)return json({error:"Provider request failed",status:r.status},502);
        const result=await r.json().catch(()=>({}));
        const msg=result.choices?.[0]?.message;
        if(typeof msg?.content!=="string")return json({error:"Provider returned no text"},502);
        const assistant=this._append("assistant",msg.content,{provider:requestedModel,tool_calls:msg.tool_calls||null});
        return json({user:userMessage,assistant,usage:result.usage||null});
      }
      const wantsOmniRoute = typeof requestedModel === "string" && requestedModel.startsWith("omniroute/");
      const useWorkersAI = Boolean(this.env.AI && !this.env.MODEL_BASE_URL &&
        (!requestedModel || requestedModel === "auto" || requestedModel === "cloudflare" || requestedModel === "workers-ai" || requestedModel.startsWith("@cf/")));
      if (useWorkersAI) {
        const model = requestedModel.startsWith("@cf/") ? requestedModel : "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
        let result;
        try {
          result = await this.env.AI.run(model, {
            messages: rows.map(row => ({ role: row.role, content: row.content })),
            max_tokens: Math.min(Math.max(Number(body.max_tokens) || 512, 1), 2048),
          });
        } catch(e) { return json({error:"Workers AI request failed",detail:String(e)},503); }
        const content = result?.response ?? result?.choices?.[0]?.message?.content ?? "";
        if(typeof content !== "string" || !content.trim()) return json({error:"Workers AI returned no text",result},502);
        const assistantMessage = this._append("assistant",content,{provider:"workers-ai",model,tool_calls:result?.tool_calls ?? null});
        return json({user:userMessage,assistant:assistantMessage,usage:result?.usage ?? null});
      }
      if(!this.env.MODEL_BASE_URL && !wantsOmniRoute) return json({error:"Use model auto (Cloudflare Workers AI). Additional providers require configuring MODEL_BASE_URL."},503);
      const useOmniRoute = wantsOmniRoute || !this.env.MODEL_BASE_URL;
      const base = useOmniRoute
        ? `${String(this.env.RENDER_ORIGIN || "").replace(/\/$/, "")}/api/omniroute/v1`
        : String(this.env.MODEL_BASE_URL).replace(/\/$/, "");
      const apiKey = useOmniRoute ? this.env.HERMES_EDGE_TOKEN : this.env.MODEL_API_KEY;
      if (!base) return json({ error: "No model route is configured." }, 503);
      const endpoint = base.endsWith("/v1") ? `${base}/chat/completions` : `${base}/v1/chat/completions`;
      const payload = {
        model: wantsOmniRoute ? requestedModel.slice("omniroute/".length) : (body.model || this.env.MODEL_NAME || "auto"),
        messages: rows.map((row) => ({ role: row.role, content: row.content })),
        stream: false,
      };

      const modelHeaders = { "content-type": "application/json" };
      if (apiKey) modelHeaders["authorization"] = `Bearer ${apiKey}`;
      const upstream = await fetch(endpoint, {
        method: "POST",
        headers: modelHeaders,
        body: JSON.stringify(payload),
      });

      const raw = await upstream.text();
      if (!upstream.ok) {
        return json({ error: "Model provider request failed", status: upstream.status, detail: raw.slice(0, 2000) }, 502);
      }

      let parsed;
      try { parsed = JSON.parse(raw); }
      catch { return json({ error: "Model provider returned invalid JSON" }, 502); }

      const choice = parsed?.choices?.[0]?.message;
      const content = choice?.content ?? "";
      const assistantMessage = this._append("assistant", content, {
        provider_response_id: parsed?.id ?? null,
        tool_calls: choice?.tool_calls ?? null,
        finish_reason: parsed?.choices?.[0]?.finish_reason ?? null,
      });

      return json({
        user: userMessage,
        assistant: assistantMessage,
        usage: parsed?.usage ?? null,
      });
    }

    if (request.method === "GET" && url.pathname.endsWith("/state")) {
      const rows = this.sql.exec("SELECT key, value, updated_at FROM state").toArray();
      const state = {};
      for (const row of rows) {
        try { state[row.key] = JSON.parse(row.value); }
        catch { state[row.key] = row.value; }
      }
      return json({ state });
    }

    if (request.method === "PUT" && url.pathname.endsWith("/state")) {
      const body = await request.json();
      if (!body || typeof body !== "object" || Array.isArray(body)) {
        return json({ error: "JSON object required" }, 400);
      }
      const now = Date.now();
      for (const [key, value] of Object.entries(body)) {
        this.sql.exec(
          "INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
          key, JSON.stringify(value), now
        );
      }
      this._broadcast({ type: "state.updated", keys: Object.keys(body), ts: now });
      return json({ ok: true });
    }

    return json({ error: "Not found" }, 404);
  }

  async webSocketMessage(ws, message) {
    if (typeof message !== "string") return;
    let body;
    try { body = JSON.parse(message); } catch { return; }
    if (body?.type === "ping") {
      ws.send(JSON.stringify({ type: "pong", ts: Date.now() }));
      return;
    }
    if (body?.type === "message" && typeof body.role === "string" && body.content != null) {
      this._append(body.role, body.content, body.metadata ?? null);
    }
  }
}


export class HermesTaskWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const params = typeof event.payload === "string" ? JSON.parse(event.payload) : (event.payload || {});
    const sessionId = params.session_id || event.instanceId;
    if(params.action==="omniroute_models"){
      const response=await step.do("OmniRoute models on demand",async()=>{
        const res=await fetch(this.env.RENDER_ORIGIN+"/api/omniroute/v1/models",{headers:{authorization:"Bearer "+this.env.HERMES_EDGE_TOKEN}});
        const raw=await res.text();let data;try{data=JSON.parse(raw)}catch{data={error:raw.slice(0,400)}};
        return {status:res.status,models:(data.data||[]).map(x=>x.id).slice(0,20),error:data.detail||data.error||null};
      });return {action:"omniroute_models",response};
    }

    if (params.action === "browser_content") {
      const result=await step.do("Cloudflare browser on demand",async()=>{
        if(typeof params.url!=="string")throw Error("URL required");
        const page=await cloudflareBrowserContent(this.env,params.url);
        return {title:page.title,url:page.url,text:page.text?.slice(0,3500)};
      });
      return {action:"browser_content",response:result};
    }

    if (params.action === "native_catalog" || params.action === "native_tool") {
      const action = params.action;
      const response = await step.do("on-demand Render tool", {
        retries: {limit: action === "native_tool" ? 0 : 1, delay: "2 seconds"},
        timeout: "2 minutes",
      }, async () => {
        const origin = String(this.env.RENDER_ORIGIN || "").replace(/\/$/, "");
        if (!origin) throw new Error("Render origin is not configured");
        const catalog = action === "native_catalog";
        const target = origin + "/api/plugins/hermes-edge-executor/" + (catalog ? "tools" : "execute");
        const init = {
          method: catalog ? "GET" : "POST",
          headers: {authorization: "Bearer " + this.env.HERMES_EDGE_TOKEN, "content-type": "application/json"},
        };
        if (!catalog) {
          if (typeof params.name !== "string" || !params.name) throw new Error("Native tool name required");
          init.body = JSON.stringify({name: params.name, arguments: params.arguments || {},
            session_id: params.session_id || event.instanceId, task_id: event.instanceId});
        }
        const res = await fetch(target, init);
        const raw = await res.text();
        if (!res.ok) throw new Error("Native tool request failed (" + res.status + "): " + raw.slice(0,800));
        let parsed; try { parsed = JSON.parse(raw); } catch { throw new Error("Native executor returned non-JSON"); }
        if (catalog) return {tools: (parsed.tools || []).map(t=>t.function||t).map(t=>({name:t.name,description:t.description,parameters:t.parameters}))};
        return parsed;
      });
      return {action, response};
    }


    const response = await step.do(
      "model turn",
      {
        retries: { limit: 3, delay: "2 seconds", backoff: "exponential" },
        timeout: "30 minutes",
      },
      async () => {
        const stub = this.env.HERMES_STATE.getByName(sessionId);
        const res = await stub.fetch("https://state.internal/chat", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            content: params.content,
            model: params.model,
            metadata: { workflow_id: event.instanceId },
          }),
        });
        const raw = await res.text();
        if (!res.ok) throw new Error(`Cloudflare model turn failed (${res.status}): ${raw.slice(0, 1000)}`);
        return JSON.parse(raw);
      },
    );

    return { session_id: sessionId, response };
  }
}

async function routeSession(request, env, url) {
  const match = url.pathname.match(/^\/api\/sessions\/([^/]+)\/(messages|state|ws|chat)$/);
  if (!match) return null;
  if (!bearerOk(request, env)) return new Response("Unauthorized", { status: 401 });
  const id = decodeURIComponent(match[1]);
  const stub = env.HERMES_STATE.getByName(id);
  const target = new URL(request.url);
  target.hostname = "state.internal";
  return stub.fetch(new Request(target.toString(), request));
}


async function proxyNative(request, env, suffix) {
  if (!bearerOk(request, env)) return new Response("Unauthorized", { status: 401 });
  const origin = String(env.RENDER_ORIGIN || "").replace(/\/$/, "");
  if (!origin) return json({ error: "Render native executor origin is not configured." }, 503);

  const target = origin + "/api/plugins/hermes-edge-executor/" + suffix;
  const headers = new Headers();
  headers.set("authorization", "Bearer " + env.HERMES_EDGE_TOKEN);
  headers.set("accept", "application/json");
  if (request.method !== "GET" && request.method !== "HEAD") {
    headers.set("content-type", request.headers.get("content-type") || "application/json");
  }

  const body = (request.method === "GET" || request.method === "HEAD")
    ? undefined
    : await request.arrayBuffer();

  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body,
  });

  const outHeaders = new Headers();
  outHeaders.set("content-type", upstream.headers.get("content-type") || "application/json");
  return new Response(upstream.body, { status: upstream.status, headers: outHeaders });
}

async function proxyModel(request, env) {
  if (!bearerOk(request, env)) return new Response("Unauthorized", { status: 401 });
  const incoming = await request.arrayBuffer();
  const payload = JSON.parse(new TextDecoder().decode(incoming));
  const direct=await directModel(env,payload);
  if(direct)return direct;
  const modelRequest = payload.model || env.MODEL_NAME || "";
  const wantsOmniRoute = typeof modelRequest === "string" && modelRequest.startsWith("omniroute/");
  const useWorkersAI = Boolean(env.AI && !env.MODEL_BASE_URL &&
    (!modelRequest || modelRequest === "auto" || modelRequest === "cloudflare" || modelRequest === "workers-ai" || modelRequest.startsWith("@cf/")));
  if (useWorkersAI) {
    const model = modelRequest.startsWith("@cf/") ? modelRequest : "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
    let result;
    try { result=await env.AI.run(model, {
      messages:payload.messages,
      max_tokens:Math.min(Math.max(Number(payload.max_tokens)||512,1),2048),
    }); } catch(e) { return json({error:"Workers AI request failed",detail:String(e)},503); }
    const content=result?.response ?? result?.choices?.[0]?.message?.content ?? "";
    if(typeof content!=="string" || !content.trim()) return json({error:"Workers AI returned no text",result},502);
    const id="chatcmpl-cf-"+crypto.randomUUID();
    const answer={id,object:"chat.completion",created:Math.floor(Date.now()/1000),model,
      choices:[{index:0,message:{role:"assistant",content},finish_reason:"stop"}],usage:result?.usage ?? null};
    if(payload.stream===true) {
      const first={id,object:"chat.completion.chunk",created:answer.created,model,choices:[{index:0,delta:{role:"assistant",content},finish_reason:null}]};
      const last={id,object:"chat.completion.chunk",created:answer.created,model,choices:[{index:0,delta:{},finish_reason:"stop"}]};
      return new Response("data: "+JSON.stringify(first)+"\n\n"+"data: "+JSON.stringify(last)+"\n\n"+"data: [DONE]\n\n",
        {headers:{"content-type":"text/event-stream; charset=utf-8","cache-control":"no-cache"}});
    }
    return json(answer);
  }
  if(!env.MODEL_BASE_URL && !wantsOmniRoute) return json({error:"Use model auto (Cloudflare Workers AI). Additional providers require configuring MODEL_BASE_URL."},503);
  const useOmniRoute = wantsOmniRoute || !env.MODEL_BASE_URL;
  const base = useOmniRoute
    ? `${String(env.RENDER_ORIGIN || "").replace(/\/$/, "")}/api/omniroute/v1`
    : String(env.MODEL_BASE_URL).replace(/\/$/, "");
  const apiKey = useOmniRoute ? env.HERMES_EDGE_TOKEN : env.MODEL_API_KEY;
  if (!base) return json({ error: "No model route is configured." }, 503);
  const endpoint = base.endsWith("/v1") ? `${base}/chat/completions` : `${base}/v1/chat/completions`;

  const headers = new Headers(request.headers);
  if (apiKey) headers.set("authorization", `Bearer ${apiKey}`);
  else headers.delete("authorization");
  headers.set("content-type", "application/json");
  headers.delete("host");
  headers.delete("content-length");

  const upstream = await fetch(endpoint, {
    method: "POST",
    headers,
    body: wantsOmniRoute ? JSON.stringify({...payload, model: modelRequest.slice("omniroute/".length)}) : incoming,
  });

  const outHeaders = new Headers(upstream.headers);
  outHeaders.delete("set-cookie");
  return new Response(upstream.body, {
    status: upstream.status,
    headers: outHeaders,
  });
}

let browserCommandSeq = 0;
function browserCommand(socket,method,params={}) {
  return new Promise((resolve,reject)=>{
    const id=++browserCommandSeq;
    const timer=setTimeout(()=>{clean();reject(new Error("Browser command timed out: "+method));},20000);
    const clean=()=>{clearTimeout(timer);socket.removeEventListener("message",onMessage);socket.removeEventListener("close",onClose);};
    const onClose=()=>{clean();reject(new Error("Browser connection closed"));};
    const onMessage=event=>{
      const msg=JSON.parse(event.data);
      if(msg.id!==id)return;
      clean();
      if(msg.error)reject(new Error(msg.error.message));else resolve(msg.result);
    };
    socket.addEventListener("message",onMessage);socket.addEventListener("close",onClose);
    try{socket.send(JSON.stringify({id,method,params}));}catch(e){clean();reject(e);}
  });
}
async function cloudflareBrowserContent(env,urlText) {
  if(!env.BROWSER)throw new Error("Browser Run binding unavailable");
  const url=new URL(urlText);
  if(!["https:","http:"].includes(url.protocol))throw new Error("HTTP(S) URL required");
  if(url.username||url.password)throw new Error("URL credentials not accepted");
  const host=url.hostname.toLowerCase();
  if(["localhost","127.0.0.1","::1","0.0.0.0","169.254.169.254"].includes(host)||host.endsWith(".local")||host.endsWith(".internal")||/^(10|127|192\.168)\./.test(host)||/^172\.(1[6-9]|2[0-9]|3[0-1])\./.test(host)||/^169\.254\./.test(host))throw new Error("Private target denied");
  let sessionId=null,socket=null;
  try{
    const session=await env.BROWSER.acquire({keepAlive:30000});
    sessionId=session.sessionId;
    const page=await env.BROWSER.devtools.newTarget(sessionId,url.toString());
    const connection=await env.BROWSER.connectSession(sessionId,{targetId:page.id});
    const upgraded=await connection.webSocket.fetch("https://browser-binding.invalid",{headers:{Upgrade:"websocket"}});
    if(!upgraded.webSocket)throw new Error("Browser Run WebSocket unavailable");
    socket=upgraded.webSocket;socket.accept();
    await browserCommand(socket,"Page.enable");
    const ready="new Promise(resolve => { if(document.readyState === 'complete') resolve(true); else addEventListener('load',()=>resolve(true),{once:true}); })";
    await browserCommand(socket,"Runtime.evaluate",{expression:ready,awaitPromise:true,returnByValue:true});
    const expression="({title:document.title,url:location.href,text:(document.body?.innerText||'').slice(0,20000),html:(document.documentElement?.outerHTML||'').slice(0,200000)})";
    const result=await browserCommand(socket,"Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});
    if(!result?.result?.value)throw new Error("Browser returned no document");
    return result.result.value;
  }finally{try{socket?.close();}catch{} if(sessionId)try{await env.BROWSER.closeSession(sessionId);}catch{}}
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const management=await handleManagement(request,env,url);
    if(management)return management;

    if (url.pathname === "/api/browser/content" && request.method === "POST") {
      if(!bearerOk(request,env))return new Response("Unauthorized",{status:401});
      let body;try{body=await request.json();}catch{return json({error:"Invalid JSON"},400);}
      if(typeof body?.url!=="string" || body.url.length>4096)return json({error:"URL required"},400);
      try {return json(await cloudflareBrowserContent(env,body.url));}
      catch(e){return json({error:String(e)},502);}
    }
    if (url.pathname === "/health") {
      const stub = env.HERMES_STATE.getByName("healthcheck");
      const check = await stub.fetch("https://state.internal/state");
      return json({
        ok: check.ok,
        service: "hermes-edge",
        state: "durable-objects",
        backup: "r2",
        polling: false,
        durable_object: check.ok ? "ok" : "error",
      }, check.ok ? 200 : 503);
    }



    if (url.pathname === "/api/native/health" && request.method === "GET") {
      return proxyNative(request, env, "health");
    }
    if (url.pathname === "/api/native/tools" && request.method === "GET") {
      return proxyNative(request, env, "tools");
    }
    if (url.pathname === "/api/native/execute" && request.method === "POST") {
      return proxyNative(request, env, "execute");
    }

    if ((url.pathname === "/api/models" || url.pathname === "/v1/models") && request.method === "GET") {
      if(!bearerOk(request,env))return new Response("Unauthorized",{status:401});
      if(url.searchParams.get("source")==="omniroute"){
        const origin=String(env.RENDER_ORIGIN||"").replace(/\/$/,"");
        const upstream=await fetch(origin+"/api/omniroute/v1/models",{headers:{authorization:"Bearer "+env.HERMES_EDGE_TOKEN}});
        return new Response(upstream.body,{status:upstream.status,headers:{"content-type":upstream.headers.get("content-type")||"application/json"}});
      }
      return json({object:"list",data:[
        {id:"auto",object:"model",owned_by:"cloudflare"},
        {id:"cloudflare",object:"model",owned_by:"cloudflare"},
        {id:"@cf/meta/llama-3.3-70b-instruct-fp8-fast",object:"model",owned_by:"cloudflare"}
      ]});
    }
    if (url.pathname === "/api/tasks" && request.method === "POST") {
      if (!bearerOk(request, env)) return new Response("Unauthorized", { status: 401 });
      const body = await request.json();
      if (!body || body.content == null) return json({ error: "content is required" }, 400);
      const id = body.id || crypto.randomUUID();
      const instance = await env.HERMES_TASKS.create({
        id,
        params: {
          session_id: body.session_id || id,
          content: body.content,
          model: body.model || null,
        },
      });
      return json({ id: instance.id, status: await instance.status() }, 202);
    }

    const taskMatch = url.pathname.match(/^\/api\/tasks\/([^/]+)$/);
    if (taskMatch && request.method === "GET") {
      if (!bearerOk(request, env)) return new Response("Unauthorized", { status: 401 });
      const instance = await env.HERMES_TASKS.get(decodeURIComponent(taskMatch[1]));
      return json({ id: instance.id, status: await instance.status() });
    }

    if (url.pathname.startsWith("/backup/")) {
      if (!bearerOk(request, env)) return new Response("Unauthorized", { status: 401 });
      const key = url.pathname.slice("/backup/".length) || "latest.tar.gz";

      if (request.method === "PUT") {
        await env.HERMES_BACKUPS.put(key, request.body, {
          httpMetadata: {
            contentType: request.headers.get("content-type") || "application/octet-stream",
          },
        });
        return new Response("ok");
      }

      if (request.method === "GET") {
        const obj = await env.HERMES_BACKUPS.get(key);
        if (!obj) return new Response("Not found", { status: 404 });
        return new Response(obj.body, {
          headers: {
            "content-type": obj.httpMetadata?.contentType || "application/octet-stream",
            "etag": obj.httpEtag || "",
          },
        });
      }

      return new Response("Method not allowed", { status: 405 });
    }

    const sessionResponse = await routeSession(request, env, url);
    if (sessionResponse) return sessionResponse;

    if (url.pathname === "/v1/chat/completions" && request.method === "POST") {
      return proxyModel(request, env);
    }

    return json({
      service: "hermes-edge",
      endpoints: ["/health", "/api/native/health", "/api/native/tools", "/api/native/execute", "/api/models", "/v1/models", "/api/browser/content", "/api/tasks", "/api/tasks/:id", "/backup/*", "/api/sessions/:id/messages", "/api/sessions/:id/state", "/api/sessions/:id/chat", "/api/sessions/:id/ws", "/v1/chat/completions"],
    });
  },
};
