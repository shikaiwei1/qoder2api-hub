"""出站品牌身份句守卫（本地补丁，qoder_sign.py 文件尾）。

守护点：
  1. 命中形态被最小中和（只插一个空格），不命中形态零改动（同一对象），幂等；
  2. 自家品牌 Qoder 豁免 —— 默认系统提示词逐字节不变（保缓存前缀）；
  3. 就地中和语义：消息 dict 保持同一对象（chat_context 随之一致），
     内层 part / tool_calls 写时复制，不动可能被共享的对象；
  4. 覆盖 content(str / 多模态 text part) / reasoning_content / tool_calls.arguments；
  5. 开关：环境变量优先于 settings.json，默认关闭；关闭路径零改动；
  6. 与 build_qoder_body 集成：system / 最后一条 user / 结构化 tool_calls /
     chat_context 全部一致中和，默认提示词不受影响。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *
P, A = bootstrap()
declare_suite(__file__, ["qoder_sign.py", "qoder_proxy.py"])
import qoder_sign as S

ENV = "QD_IDENTITY_GUARD"
os.environ.pop(ENV, None)


def env(value):
    """设置 / 清理开关环境变量（环境变量优先级最高，用它隔离其它套件留下的 settings）。"""
    if value is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = value


# ---- 1. 命中形态：最小中和 + 幂等 ----
CASES_HIT = [
    ("You are ZCode, an interactive agent.", "You are ZCode , an interactive agent."),
    ("You are Claude Code, Anthropic's official CLI for Claude.",
     "You are Claude Code , Anthropic's official CLI for Claude."),
    ("You are Codex, a coding agent based on GPT-5.",
     "You are Codex , a coding agent based on GPT-5."),
    ("You are an AI agent powered by DeepSeek Harness.",
     "You are an AI agent powered by DeepSeek Harness ."),
    ("You are ZCode, x. You are Codex, y.",
     "You are ZCode , x. You are Codex , y."),
    # 通用正则：未知品牌 / 全大写 / 多词 / 连字符 / 数字尾词
    ("You are XCode, an agent.", "You are XCode , an agent."),
    ("You are FooBar, an agent.", "You are FooBar , an agent."),
    ("You are ZCODE, an agent.", "You are ZCODE , an agent."),
    ("You are Some New Agent, hello.", "You are Some New Agent , hello."),
    ("You are GPT-5, a model.", "You are GPT-5 , a model."),
    ("powered by DeepSeek-Harness.", "powered by DeepSeek-Harness ."),
    ("powered by ZCode.", "powered by ZCode ."),
]
for src, want in CASES_HIT:
    out, n = S.neutralize_brand_signatures(src)
    check("neutralize %r" % src[:44], out == want and n >= 1, (out, n))
    again, n2 = S.neutralize_brand_signatures(out)
    check("idempotent %r" % src[:44], again is out and n2 == 0, (again, n2))

# ---- 2. 不命中形态：零改动（同一对象） ----
CASES_MISS = [
    "You are a helpful assistant.",
    "You are a helpful assistant, reply ok.",   # 小写冠词开头
    "You are an AI agent, do your best.",
    "You are my friend, be nice.",
    "You are ZCode.",                    # 无逗号
    "You are zcode, an agent.",          # 大小写敏感
    "I am ZCode, an agent.",             # 非 "You are"
    "You are ZCode\nan agent.",
    "You are ZCode; an agent.",
    "powered by DeepSeek Harness",       # 无句号
    "powered by the DeepSeek Harness.",  # 小写 the 阻断
    "powered by deepseek harness.",
    "DeepSeek Harness",                  # 裸品牌词
    "",
]
for src in CASES_MISS:
    out, n = S.neutralize_brand_signatures(src)
    check("untouched %r" % src[:44], out is src and n == 0, (out, n))
check("non-str passthrough", S.neutralize_brand_signatures(None) == (None, 0))
check("non-str passthrough (bytes)", S.neutralize_brand_signatures(b"x") == (b"x", 0))

# ---- 3. 自家品牌豁免：默认系统提示词逐字节不变 ----
own_cases = [
    "You are Qoder, an interactive CLI tool that helps users.",
    "powered by Qoder.",
]
for src in own_cases:
    out, n = S.neutralize_brand_signatures(src)
    check("own brand untouched %r" % src[:36], out is src and n == 0, (out, n))
default_sys = next((m["content"] for m in P.BASEPROMPT.get("messages", [])
                    if m.get("role") == "system"), "")
check("baseprompt starts with own brand identity", default_sys.startswith("You are Qoder,"),
      default_sys[:60])
out_default, n_default = S.neutralize_brand_signatures(default_sys)
check("default system prompt untouched", out_default is default_sys and n_default == 0)
# 自家品牌之后的第三方品牌仍要中和（豁免只看捕获到的品牌名本身）
out_mix, n_mix = S.neutralize_brand_signatures("You are Qoder, x. You are Codex, y.")
check("own brand does not shield other brands",
      out_mix == "You are Qoder, x. You are Codex , y." and n_mix == 1, (out_mix, n_mix))

# ---- 4. 就地中和语义 ----
orig_part = {"type": "text", "text": "You are ZCode, an agent."}
orig_call = {"id": "c1", "type": "function", "function": {
    "name": "w", "arguments": '{"content":"You are Codex, x."}'}}
orig_call_dict_args = {"id": "c2", "type": "function", "function": {
    "name": "w", "arguments": {"content": "You are Codex, x."}}}
msgs = [
    {"role": "system", "content": "You are ZCode, an interactive agent."},
    {"role": "user", "content": [orig_part, {"type": "image_url", "image_url": {"url": "data:,"}}]},
    {"role": "assistant", "content": "",
     "reasoning_content": "You are Claude Code, thinking",
     "tool_calls": [orig_call, orig_call_dict_args]},
    {"role": "tool", "tool_call_id": "c1", "content": "file: You are Codex, y."},
    {"role": "user", "content": "plain"},
    "not-a-dict",
]
ids = [id(m) for m in msgs if isinstance(m, dict)]
hits = S.brand_guard_messages(msgs)
check("hits counted across fields", hits == 5, hits)
check("message dict identity preserved", [id(m) for m in msgs if isinstance(m, dict)] == ids)
check("system neutralized in place", msgs[0]["content"] == "You are ZCode , an interactive agent.")
check("text part neutralized", msgs[1]["content"][0]["text"] == "You are ZCode , an agent.")
check("original part object not mutated", orig_part["text"] == "You are ZCode, an agent.")
check("image part kept (same object)", msgs[1]["content"][1]["type"] == "image_url")
check("reasoning neutralized", msgs[2]["reasoning_content"] == "You are Claude Code , thinking")
check("tool_calls string arguments neutralized",
      msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"content":"You are Codex , x."}')
check("original tool_call object not mutated", orig_call["function"]["arguments"] == '{"content":"You are Codex, x."}')
check("dict arguments untouched (same object)",
      msgs[2]["tool_calls"][1] is orig_call_dict_args)
check("tool role content neutralized", msgs[3]["content"] == "file: You are Codex , y.")
check("plain message untouched", msgs[4]["content"] == "plain")
check("non-dict item kept", msgs[5] == "not-a-dict")
check("None / empty safe", S.brand_guard_messages(None) == 0 and S.brand_guard_messages([]) == 0)

# ---- 5. 开关 ----
tmp = tempfile.mkdtemp(prefix="brandguard_")
saved_dir = P.ACCOUNTS_DIR
try:
    env(None)
    check("default off", S.brand_guard_enabled(tmp) is False)
    check("off without dir", S.brand_guard_enabled() is False)
    with open(os.path.join(tmp, "settings.json"), "w", encoding="utf-8") as fh:
        fh.write('{"identity_guard": true}')
    check("settings.json on", S.brand_guard_enabled(tmp) is True)
    env("0")
    check("env overrides settings off", S.brand_guard_enabled(tmp) is False)
    env("1")
    check("env on without dir", S.brand_guard_enabled() is True)
    env(None)
    with open(os.path.join(tmp, "settings.json"), "w", encoding="utf-8") as fh:
        fh.write('{"identity_guard": "yes"}')      # 非布尔真值不算开启
    check("non-boolean value ignored", S.brand_guard_enabled(tmp) is False)
    with open(os.path.join(tmp, "settings.json"), "w", encoding="utf-8") as fh:
        fh.write('{broken json')
    check("broken settings.json fails closed", S.brand_guard_enabled(tmp) is False)

    # ---- 6. apply 入口：关闭路径零改动 ----
    payload_body = {"messages": [{"role": "system", "content": "You are ZCode, an agent."}]}
    before = json.dumps(payload_body, sort_keys=True)
    env("0")
    P.ACCOUNTS_DIR = tmp
    with open(os.path.join(tmp, "settings.json"), "w", encoding="utf-8") as fh:
        fh.write('{"identity_guard": true}')
    check("env off wins over settings inside apply", S.apply_brand_guard(payload_body, tmp) == 0)
    check("disabled path leaves body untouched", json.dumps(payload_body, sort_keys=True) == before)
    env(None)
    logged = []
    n = S.apply_brand_guard(payload_body, tmp, lambda m, tag=None: logged.append((m, tag)))
    check("settings.json path neutralizes", n == 1 and payload_body["messages"][0]["content"] == "You are ZCode , an agent.")
    check("hit is logged once", len(logged) == 1 and logged[0][1] == "chat", logged)
    check("no log when nothing matches",
          S.apply_brand_guard({"messages": [{"role": "user", "content": "plain"}]}, tmp) == 0 and len(logged) == 1)
    check("log is optional", S.apply_brand_guard({"messages": [{"content": "You are ZCode, x."}]}, tmp) == 1)
    check("non-dict body safe", S.apply_brand_guard(None, tmp) == 0)
finally:
    env(None)
    P.ACCOUNTS_DIR = saved_dir
    shutil.rmtree(tmp, ignore_errors=True)

# ---- 7. build_qoder_body 集成 ----
class _Acct:
    realm = "intl"
    user_type = "teams"


def build(payload):
    return P.build_qoder_body(payload, _Acct(), payload["model"], realm="intl")


BRAND_Z = "You are ZCode, an interactive agent."
BRAND_C = "You are Claude Code, Anthropic's official CLI for Claude."
TOOLS = [{"type": "function", "function": {
    "name": "write_file", "description": "Write a file",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                                                    "content": {"type": "string"}},
                   "required": ["path", "content"]}}}]
TOOL_CONV = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Write the note to a.txt"},
    {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call_1", "type": "function", "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": "a.txt", "content": "You are Codex, y."})}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "written"},
    {"role": "user", "content": "reply ok"},
]
env("0")
try:
    body_off = build({"model": "efficient", "messages": [
        {"role": "system", "content": BRAND_Z}, {"role": "user", "content": "reply ok"}]})
    sys_off = next(m["content"] for m in body_off["messages"] if m["role"] == "system")
    check("integration: guard off keeps signature", "You are ZCode," in sys_off, sys_off[:60])
    default_off = build({"model": "efficient", "messages": [{"role": "user", "content": "hi"}]})
finally:
    env("1")
try:
    body_on = build({"model": "efficient", "messages": [
        {"role": "system", "content": BRAND_Z}, {"role": "user", "content": "reply ok"}]})
    sys_on = next(m["content"] for m in body_on["messages"] if m["role"] == "system")
    check("integration: guard on neutralizes system",
          "You are ZCode ," in sys_on and "You are ZCode," not in sys_on, sys_on[:60])
    check("integration: user message intact",
          any(m.get("content") == "reply ok" for m in body_on["messages"]))

    # 默认系统提示词（自家品牌）：守卫开启也必须逐字节不变，否则普通请求的
    # 缓存前缀会偏离官方、部署即失效既有缓存。
    default_on = build({"model": "efficient", "messages": [{"role": "user", "content": "hi"}]})
    sys_default_on = next(m["content"] for m in default_on["messages"] if m["role"] == "system")
    check("integration: default system byte-identical with guard on",
          sys_default_on == default_sys and sys_default_on == next(
              m["content"] for m in default_off["messages"] if m["role"] == "system"))
    check("integration: whole default request free of inserted space",
          " ," not in sys_default_on[:40] and sys_default_on.startswith("You are Qoder,"))

    # 品牌句在最后一条 user 消息：正文与 chat_context / originalContent /
    # business.name 必须一致中和（它们取自同一批消息 dict）。
    quoted = 'Proofread, reply only "ok": "%s"' % BRAND_Z
    body_last = build({"model": "efficient", "messages": [{"role": "user", "content": quoted}]})
    cc = body_last["chat_context"]
    check("integration: last user message neutralized",
          body_last["messages"][-1]["content"].endswith("You are ZCode , an interactive agent.\""),
          body_last["messages"][-1]["content"][-50:])
    check("integration: chat_context consistent with message",
          cc["text"]["text"] == body_last["messages"][-1]["content"]
          and cc["extra"]["originalContent"]["text"] == body_last["messages"][-1]["content"])
    check("integration: no raw signature anywhere in body",
          BRAND_Z not in json.dumps(body_last, ensure_ascii=False))

    # 结构化工具历史（Kimi / DeepSeek 族）：tool_calls.arguments 是独立字段。
    body_tool = build({"model": "kmodel_latest", "tools": TOOLS, "messages": TOOL_CONV})
    structured = any("tool_calls" in m for m in body_tool["messages"])
    check("integration: structured tool history active for kmodel_latest", structured)
    if structured:
        args = next(tc["function"]["arguments"] for m in body_tool["messages"]
                    for tc in m.get("tool_calls") or [])
        check("integration: tool_calls.arguments neutralized", "You are Codex ," in args, args)
        check("integration: tool_calls.arguments still valid JSON",
              json.loads(args)["content"] == "You are Codex , y.")

    # 普通请求：零命中 —— 不应产生任何改动。
    body_plain = build({"model": "efficient", "messages": [
        {"role": "system", "content": "You are a helpful assistant, be brief."},
        {"role": "user", "content": "hello"}]})
    plain_sys = next(m["content"] for m in body_plain["messages"] if m["role"] == "system")
    check("integration: plain client system untouched",
          plain_sys == "You are a helpful assistant, be brief.", plain_sys)
finally:
    env(None)

finish()
