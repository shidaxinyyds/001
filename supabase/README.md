# 卡密授权底座 · 部署与运营手册（Supabase 免费方案）

一次性卡密 → 激活即绑定设备并起算到期 → 到期/拉黑自动失效 → 续费靠手动发新卡密。
日常使用**离线本地验签秒开**，仅每 3 天静默联网续签一次，Supabase 偶发抽风也不影响用户正常使用。

---

## 0. 建项目（一次性，约 5 分钟）

1. 打开 <https://supabase.com> → Sign in（**只需邮箱**，无需信用卡/实名）。
2. New project，区域选 **Northeast Asia (Tokyo/Singapore)**（离大陆最近）。
3. 记下 Project URL 与 `anon` / `service_role` 两个 key（Settings → API）。

## 1. 建表与函数

- 左侧 **SQL Editor** → 新建 → 粘贴 `supabase/migrations/0001_license.sql` 全文 → Run。
- **RLS**：Authentication → Policies → 对 `card_keys`、`licenses` 两张表 **Enable RLS**（不建任何 policy）；
  这样匿名 key 读不到任何数据，只有 Edge Function（service_role）能访问。

## 2. 生成一个共享密钥

随机 64 位十六进制，两处要用**同一个值**（Edge Function secret、GitHub Secret）：

```powershell
py -3.10 -c "import secrets;print(secrets.token_hex(32))"
```

⚠️ **仓库是公开的，这个密钥绝对不许写进仓库里任何文件**（包括 config.dart）。
它只存在两个地方：Supabase 的 Edge Function Secrets，和 GitHub 仓库的 Actions Secrets。

## 3. 部署 Edge Function

装 [Supabase CLI](https://supabase.com/docs/guides/cli) 后在项目根目录：

```powershell
supabase login
supabase link --project-ref <你的REF>
supabase secrets set LICENSE_TOKEN_SECRET=<第2步的64位十六进制>
supabase functions deploy license --no-verify-jwt
```

**必须带 `--no-verify-jwt`**（或在 Dashboard → Edge Functions → license 里关掉 Verify JWT）：
App 请求不带 Supabase JWT，开着它激活/续签会直接 401，上线即炸。安全性不受影响：
RPC 已在 SQL 里 `revoke` 给匿名，Function 自身只暴露 activate/renew/heartbeat 三个动作。

部署完，Function URL 形如：
`https://<REF>.functions.supabase.co/license` —— 存好，第 4 步要填进 GitHub Secrets（不是代码里）。

## 4. 配置构建注入（不改仓库代码！）

`lib/license/config.dart` 里的两个常量都是 `String.fromEnvironment`，构建时由
`--dart-define` 注入（build.yml 已配好，从 GitHub Secrets 自动传）：

1. GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret，加两个：
   - `LICENSE_TOKEN_SECRET` = 第 2 步的 64 位口令（与 Edge Function secret 同值）；
   - `LICENSE_FUNCTION_URL` = 第 3 步的 Function URL（同时供心跳 workflow 用）。
2. 本地想自己打 APK 验证时手动传参：
   ```powershell
   flutter build apk --release --dart-define=LICENSE_TOKEN_SECRET=<口令> --dart-define=LICENSE_FUNCTION_URL=<url>
   ```

CI 在两个 Secret 缺任一个时会**直接拒建**，不会发布出激活必失败的坏包。
没注入时 App 也不崩，只是激活/验签不通过（退化为未激活页）。

## 5. 签发卡密（日常运营）

```powershell
py -3.10 localtest/gen_cards.py --type month --days 30 --count 20 --prefix MJ
```

- 屏幕上打印的**明文卡密**发给客户（只显示一次）；
- 把脚本末尾的 `insert into public.card_keys ...` 贴进 SQL Editor 执行入库。

## 6. 日常操作（Supabase 后台 Table Editor，像改 Excel 一样）

| 想干什么 | 怎么做 |
|---|---|
| 查某卡密是否被用、绑了哪台设备 | `card_keys` 表看 `status` / `bound_device` |
| 拉黑一个用户 | `licenses` 表找到该 `device_id`，把 `revoked` 勾成 `true` → 下次续签(≤3天)即断 |
| 给某用户延长到期 | 改 `licenses.expires_at` |
| 作废一张未用的卡 | `card_keys` 删行或把 `status` 改 `disabled` |

## 7. 防"7 天没人访问自动暂停"（重要）

Supabase 免费项目 7 天无**数据库/计算活动**会被自动暂停——**付费用户当天就会验证失败**。
注意：只访问 Edge Function 的 HTTP 请求**不算**数据库活动，救不了暂停。仓库已备好心跳：

- `.github/workflows/license-heartbeat.yml` 每天调两次 Function 的 `{"action":"heartbeat"}`；
- Function 收到 heartbeat 会**真正读一行 `licenses`**（见 `touchDb`），这才算数据库活动、才防得住暂停。

启用只需：GitHub 仓库 Secrets 里的 `LICENSE_FUNCTION_URL`（与第 4 步共用同一个）。前期客户持续激活时天然有流量，但空闲期务必开着。

---

## 工作原理速记（给未来的你）

- 卡密 = 一次性兑换券，激活后写死设备与到期时间，**不能重复激活、不能换机**。
- App 本地只存一张**服务端 HMAC 签名的短期凭证（3 天）**，天天离线验签放行、秒开。
- 每 3 天后台静默续签；**到期时间由服务端封顶**，用户改手机时间只会更早触发续签，无法续命。
- "续费"= 你卖一张新卡密、用户重新验证；"拉黑"= 你在 `licenses` 勾 `revoked`。
- **诚实提醒**：最终放行开关在手机 App 里，能 root+改包的高级破解者仍可绕过本地验签；
  本方案挡住的是"改时间白嫖"和"一张卡多人传用"这两类绝大多数场景。要挡住改包需把核心算法搬到服务端，代价是更重后端，暂不做。
