// ============================================================================
// AutoVision 卡密授权 · Supabase Edge Function
// 部署：supabase functions deploy license
// 需要 Secrets（Dashboard → Settings → Edge Functions → Secrets 或 CLI）：
//   LICENSE_TOKEN_SECRET : 与服务端/客户端共享的 HMAC 密钥（64 位十六进制）
//   SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY : 项目自动注入
// 逻辑：只走两条 RPC(sp_activate / sp_renew)，用 service_role 绕 RLS；
//       签发的短期凭证 token 用 HMAC-SHA256，客户端只用同一密钥验签。
// ============================================================================
// deno-lint-ignore-file no-explicit-any

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const TOKEN_SECRET = Deno.env.get("LICENSE_TOKEN_SECRET")!;

// 单张短期凭证的有效期（秒）：到期客户端静默续签。总时长由服务端 expires_at 封顶。
const TOKEN_S = 3 * 24 * 3600; // 3 天

const enc = new TextEncoder();

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.trim();
  const out = new Uint8Array(clean.length >> 1);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.substr(i * 2, 2), 16);
  }
  return out;
}

function b64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// 规范串：字段用 U+00A6 分隔，避免与卡密里的连字符/竖线冲突。
function canonical(device: string, code: string, expiresAt: number, tokenExp: number): string {
  return `${device}\u00a6${code}\u00a6${expiresAt}\u00a6${tokenExp}`;
}

async function signPayload(canon: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    hexToBytes(TOKEN_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(canon));
  return b64url(sig);
}

async function rpc(fn: string, params: Record<string, any>): Promise<any> {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/${fn}`, {
    method: "POST",
    headers: {
      apikey: SERVICE_KEY,
      Authorization: `Bearer ${SERVICE_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(params),
  });
  return await res.json();
}

// 一次极轻量的数据库读取：Supabase 免费项目「是否被暂停」看的是数据库/计算活动，
// 只访问 Edge Function 本身不算数——必须真正打到 Postgres 才算心跳。
async function touchDb(): Promise<number> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/licenses?select=id&limit=1`,
    { headers: { apikey: SERVICE_KEY, Authorization: `Bearer ${SERVICE_KEY}` } },
  );
  await res.arrayBuffer();
  return res.status;
}

async function handle(req: Request): Promise<Response> {
  const json = await req.json().catch(() => ({} as any));
  const action = String(json.action ?? "");
  const device = String(json.device_id ?? "").trim();
  const code = String(json.code ?? "").trim();

  // 注：不在这里统一要求 device —— heartbeat 不需要设备号；
  // activate / renew 各自在分支内校验。
  let out: any;
  if (action === "heartbeat") {
    // GitHub Actions 保活用：读一行即算数据库活动，防止免费项目 7 天空闲被暂停。
    const status = await touchDb();
    return Response.json({ ok: true, db: status });
  } else if (action === "activate") {
    if (!device) return Response.json({ ok: false, error: "no_device" });
    if (!code) return Response.json({ ok: false, error: "no_code" });
    out = await rpc("sp_activate", { p_code: code, p_device: device });
  } else if (action === "renew") {
    if (!device) return Response.json({ ok: false, error: "no_device" });
    out = await rpc("sp_renew", { p_device: device, p_token_s: TOKEN_S });
  } else {
    return Response.json({ ok: false, error: "bad_action" });
  }

  if (!out || out.ok !== true) {
    return Response.json({ ok: false, error: out?.error ?? "server_error" });
  }

  const expiresAt = Number(out.expires_at);
  const now = Math.floor(Date.now() / 1000);
  // activate: 服务端不返回 token_exp，取 min(现在+窗口, 总到期)；renew 用服务端值。
  const tokenExp = action === "activate"
    ? Math.min(now + TOKEN_S, expiresAt)
    : Number(out.token_exp);

  const canon = canonical(device, code || "renew", expiresAt, tokenExp);
  const sig = await signPayload(canon);
  // 客户端 token 串：device|code|expiresAt|tokenExp|b64sig
  const token = `${device}|${code || "renew"}|${expiresAt}|${tokenExp}|${sig}`;

  return Response.json({
    ok: true,
    token,
    expires_at: expiresAt,
    token_exp: tokenExp,
    card_type: out.card_type ?? null,
  });
}

Deno.serve((req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
      },
    });
  }
  if (req.method !== "POST") {
    return Response.json({ ok: false, error: "method_not_allowed" });
  }
  return handle(req);
});
