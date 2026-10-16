# C：多模型抓取选择与评估

C 在 A 的可行候选中选择抓取 ID，并让同一套物理控制器执行，包含 M3、A1、一次
输出修复、几何回退、缓存、批量运行、恢复和汇总。**GPT 默认恢复为 Codex CLI 的
现有 ChatGPT 登录**；Gemini 使用官方 `google-genai` SDK 和 `GEMINI_API_KEY`。
默认运行不需要 `OPENAI_API_KEY`，不会调用额外计费的 OpenAI API。

当前仍为 **pilot**：A 使用 `observable_primitives_v2` 控制器和
`fixed_target_hold_v2` 评分；正式候选、评分及消融定义仍需全组冻结。
C 给 A 的即时输出是 `selected_id/ranking` 和审计 metadata，物理成功由 A 判定。
最终交付是第 7–8 节的 manifest、逐条 trial 和多方法统计 summary。

## 1. 安装与离线验证

推荐 Python 3.11 或 3.12，在仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-c.txt
python -m unittest discover -s tests -v
python -m grasp_c validate --config configs/c.multimodel.json
```

Windows 可执行 `conda env update -f environment.yml`、`conda activate grasp-a`，
再运行上述 `python -m ...` 命令。已有虚拟环境需重新安装 `requirements-c.txt`，
其中包含 `google-genai`、`python-dotenv` 和可选 OpenAI API 适配器使用的 `openai`。
Codex CLI 需单独安装，并在本机运行 `codex login` 完成 ChatGPT 登录。
跨平台安装使用 `requirements-c.txt`；`requirements-c.lock.txt` 是 macOS arm64/
Python 3.12 的版本快照，每次运行的 manifest 会记录实际环境。

无凭据可以验证完整物理流水线：

```bash
python -m grasp_c run --config configs/c.mock.json --out results/local/c_mock
```

`mock` 是确定性接口测试，物理执行由真实 PyBullet 完成，不能用作 LLM 性能结果。
macOS arm64 编译 PyBullet 若在自带旧版 zlib 的 `fdopen` 处报错，可使用：

```bash
CFLAGS='-include stdio.h' python -m pip install -r requirements-c.txt
```

## 2. 默认账号配置与真实调用

GPT 使用本机 Codex CLI 的 ChatGPT 登录：

```bash
codex login status
# 尚未登录时执行 codex login，并在浏览器完成登录
```

模型沿用已验证的 `gpt-5.6-luna`，参数为 `reasoning_effort=low`。GPT 不需要
OpenAI API key。适配器不读取、复制或提交登录 token；ChatGPT/Codex 额度和模型
访问权限由各自账户决定。模型不可用、登录失败或参数错误都会记录并停止，不会自动
改用 API key 或其他模型。Codex 子进程会移除继承的 `OPENAI_API_KEY` 和
`CODEX_API_KEY`，并拒绝 API key 登录状态；父进程环境不变，独立 API 方法仍可使用。

Gemini key 在 [Google AI Studio](https://aistudio.google.com/apikey) 创建。
复制项目模板后在本地编辑器填写 `GEMINI_API_KEY`：

```bash
cp .env.example .env
```

Windows PowerShell 用 `Copy-Item .env.example .env`。也可以在运行终端直接设置：

```bash
export GEMINI_API_KEY='你的 Gemini API key'
```

```powershell
$env:GEMINI_API_KEY = '你的 Gemini API key'
```

`.env` 已被 Git 忽略；不要把真实 key 放入 JSON、源码或聊天。项目只加载仓库根目录
`.env`，不会搜索父目录或展开 key 内的变量表达式；已导出的环境变量优先。
若 IDE/Codex 未读取终端更新的变量，更新其运行配置或重启进程，或使用本地 `.env`。
缺少 Gemini key 会明确报错，不会把该方法换成 GPT 或几何方法并冒充模型成功。

| 配置 | 所需凭据 | 方法 |
|---|---|---|
| `configs/c.codex.json` | 本机 Codex 的 ChatGPT 登录 | GPT 的 M3/A1 和几何参考 |
| `configs/c.gemini.json` | `GEMINI_API_KEY` | Gemini 的 M3/A1 和几何参考 |
| `configs/c.multimodel.json` | 上述两项 | 同条件比较 GPT、Gemini 的 M3/A1 和几何参考 |

先运行关闭缓存的单次样例：

```bash
python -m examples.llm_smoke --out results/local/codex_smoke.json
python -m examples.llm_smoke --provider gemini --out results/local/gemini_smoke.json
```

Gemini 默认是 `gemini-3.8-flash`、`thinking_level=low` 和 1024 输出 token 上限。
可用 `--model` 指定账户可访问的准确 ID，更换模型时应同步调整批量配置中的参数。
样例为仍有旧模型权限的账户保留 2.5 Flash 的 `thinking_budget=0` 分支；
显式指定 `gemini-2.5-pro` 时使用其最小思考预算 `thinking_budget=128`，不关闭思考。
真实调用发现：`models.list()` 即使列出 `gemini-2.5-flash`，新用户生成请求仍可能
收到 404，提示该模型不再向新用户提供并建议 3.8 Flash。列表不保证实际生成权限。

样例见 [`examples/llm_smoke.py`](../examples/llm_smoke.py)。它打印
`raw_initial_response`、`selected_id`、`ranking`、`live_requests`、`cache_hit`、修复/
回退标记与 `episode.success`，并保存完整 trial。只有新调用、未回退的模型回答与
物理成功同时成立时才退出 0；物理失败本身不意味着模型调用失败。输出若已存在会被
拒绝，重跑请指定新路径。

2026-10-16 恢复默认 Codex 方式后，关闭缓存重新实测返回
`{"selected_id":"G02","ranking":["G02","G03"]}`，请求模型 `gpt-5.6-luna`，
真实请求 1 次、cache_hit=false、无修复/回退。新版 A 的倒水准备执行成功，完整
保持 2 秒，无碰撞/掉落。完整记录为本地
`results/local/codex_restored_default_live_smoke.json`。
本次 Gemini 官方 SDK 已读取 key，模型列表请求成功，
但改用 3.8 Flash 后两次完整样例及一次最小生成请求均返回 HTTP 503/UNAVAILABLE，
服务端提示需求过高、稍后重试。完整样例的物理成功来自几何回退，**不能视为 Gemini
生成或评估成功**。OpenAI API key 方式尚未实测；它不属于默认流程，无需为此配置
`OPENAI_API_KEY`。Gemini 服务恢复后应重跑关闭缓存的样例。

随后按要求测试 `gemini-2.5-pro`：项目样例与最小 SDK 生成请求均返回
HTTP 404 / NOT_FOUND，服务端明确说明该模型不再向新用户开放，建议
`gemini-3.1-pro-preview`。本次没有取得 Pro 回答，也没有自动换模型；完整项目记录
在 `results/local/gemini25_pro_native_live_smoke.json`。可使用
`python -m examples.llm_smoke --provider gemini --model gemini-2.5-pro --out results/local/gemini_pro_retry.json`
复测；当前默认仍保留已确认的 `gemini-3.8-flash`。

通过单次测试后运行多模型比较：

```bash
python -m grasp_c run --config configs/c.multimodel.json --out results/local/c_multimodel
python -m grasp_c summarize results/local/c_multimodel
```

Gemini SDK 继承 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，无需把代理凭据写进实验
配置。官方说明：[Codex 登录](https://learn.chatgpt.com/docs/auth)、
[Codex 非交互运行](https://learn.chatgpt.com/docs/non-interactive-mode)、
[Gemini quickstart](https://ai.google.dev/gemini-api/docs/quickstart)。

## 3. 在代码中调用 GPT 或 Gemini

两种后端共用 `LLMProvider` 边界；选择器、修复、缓存和评估器无需修改。
默认 GPT 使用 Codex CLI：

```python
from grasp_a.contracts import load_candidates
from examples.evaluate_one_trial import evaluate_trial
from grasp_c.providers import build_provider
from grasp_c.selectors import LLMSelector

provider = build_provider({"type": "codex", "timeout_s": 120})
provider.preflight()
selector = LLMSelector(
    provider, "gpt-5.6-luna", variant="M3",
    parameters={"reasoning_effort": "low"},
    cache_dir=".cache/grasp_c",
)
trial = evaluate_trial(
    "pour_prep", 0, "M3_CODEX",
    load_candidates("configs/candidates.c-pilot.json"), selector,
)
```

换成 Gemini 时只替换 provider、model 和参数：

```python
provider = build_provider({"type": "gemini", "api_key_env": "GEMINI_API_KEY"})
provider.preflight()
selector = LLMSelector(
    provider, "gemini-3.8-flash", variant="M3",
    parameters={"thinking_level": "low", "max_output_tokens": 1024},
    cache_dir=".cache/grasp_c",
)
```

GPT 适配器通过 `codex exec` 在空临时目录中调用，关闭项目指令、命令、图片、搜索
和多 agent 工具，并检查事件以排除工具调用。Codex 自身仍有系统指令，与直接 API
的输入环境不同，统计中保留独立方法/provider 身份；模型 ID 是请求 ID，不声称已经
核实底层模型快照。

Gemini 使用 `genai.Client(...).models.generate_content(...)`，将相同 prompt 映射
为 `system_instruction` 与对话内容，使用 JSON MIME 类型与 JSON Schema 约束
输出。两种后端均保存实际文本、token 用量和状态，再执行相同的本地输出校验。
环境 seed 只固定仿真并进入日志/缓存，不自动作为模型解码 seed。Gemini 3 的
`thinking_level` 与 2.5 的 `thinking_budget` 不能混用，也不能复制到 Codex 参数。

## 4. 多模型与其他服务

`configs/c.multimodel.json` 是默认多模型配置，包含 Codex GPT 和 Gemini 的 M3/A1，
以及几何参考；所有方法共享同一份 seed、候选、观测配置和固定执行器。
每个方法独立指定 provider、model、parameters 和 variant。新增模型应使用独立
方法 ID 和新的运行目录，不会静默换模型。

Qwen、DeepSeek 或本地服务可使用 OpenAI 兼容接口：

```json
{
  "id": "M3_MY_MODEL",
  "kind": "llm",
  "variant": "M3",
  "provider": {
    "type": "openai_compatible",
    "base_url": "http://localhost:8000/v1",
    "api_key_env": "LOCAL_LLM_API_KEY",
    "structured_outputs": true
  },
  "model": "your-served-model",
  "parameters": {"max_output_tokens": 1024}
}
```

`openai_compatible` 默认使用 Chat Completions；支持 Responses 的服务可加
`"api": "responses"`。不支持 JSON Schema 时可设置 `structured_outputs: false`，
仍执行本地严格输出校验。各服务参数和 token 字段可能有差异，需先做真实冒烟测试；
示例见 `configs/c.compatible.json`。原生 Gemini 使用 `type: gemini`，不填 OpenAI
兼容 base_url。

新服务若不兼容 OpenAI，可增加实现 `LLMProvider` 的适配器，提供 `identity`、
`preflight()`、`generate(ModelRequest) -> ModelResponse`，再在 `build_provider()`
注册。选择器、缓存和物理评估器不依赖具体 SDK。

## 5. 方法与失败策略

| 方法 | 当前定义 |
|---|---|
| M3 | LLM 根据任务意图和公开候选信息排序并选择 |
| A1 | 暂定消融：去掉 task_id 和任务描述，其余输入/输出协议相同 |
| GEOMETRY_REFERENCE | 按输入 geometry_score 降序，分数相同时按 ID 升序 |
| SMOKE / mock | 仅验证接口与流水线 |

原仓库没有规定 A1 的消融含义，本实现把 `remove_task_information` 明确写入 metadata。
若小组已有其他定义，需要修改 prompt、升级版本并使用新运行目录。
几何参考及回退使用的是现有 pilot 分数；B 仍负责冻结正式评分、M0/M1/M2 和独立可接受集合。

LLM 只收到 `id/region/approach/gripper_width/geometry_score`。位姿、筛选失败原因、
仿真轨迹、最终成功以及 oracle 标签均不传入模型。

输出必须严格为 `{"selected_id": "G02", "ranking": ["G02", "G03"]}`：ranking 是
当前所有可行 ID 的完整排列，首项等于 selected_id。非法 JSON、ID 或排名只修复一次；
仍无效则按几何参考规则回退。`fallback_to_geometry` 是当前主字段；`fallback_to_m1` 只保留为旧日志兼容别名，不能据此声称执行了 B 的正式 M1。超时、限流、拒绝或截断单独记原因并回退；鉴权、
模型访问、配置及缓存损坏不当成一次成功的 LLM 回答。SDK 的网络重试与这一次格式修复
分开，SDK 重试上限由 `provider.max_retries` 配置。

## 6. 缓存、重复与公平比较

缓存存放原始模型响应而非只存选中的 ID。键包括 provider 身份、模型、参数、
实际 prompt/schema、prompt_version、环境 seed、sample_id 和初始/修复阶段。
参数、候选公开信息、prompt 或模型改变会得到不同缓存。

- `cache_mode: read_write`：复用已有响应，缺失时调用并保存。
- `cache_mode: read_only`：只重放，缺失明确报错。
- `cache_mode: off`：不读写，用于新的独立模型采样。

设置 `repeats` 后，每个 repeat 都有独立 sample_id，避免把一个缓存回答当成多次
独立 LLM 调用。跨任务的 A1 prompt 相同，可能复用同一个响应；它代表任务无关的相同
选择策略，日志会显示缓存命中。若要跨任务也独立采样，请关闭缓存。
用相同 seed、候选、控制器和环境比较方法。seed 0–9 已参与控制器 pilot；正式
实验划分由小组冻结，配置 `seed_split.evaluation` 时会检查不与 `pilot_controller` 重叠。

## 7. 批量运行、恢复与输出

```bash
python -m grasp_c run --config configs/c.multimodel.json --out results/local/my_run --media
python -m grasp_c run --config configs/c.multimodel.json --out results/local/my_run --media --resume
python -m grasp_c summarize results/local/my_run
```

默认恢复会跳过所有已保存记录，包括错误，避免意外重复调用模型。明确要重试错误时加
`--resume --retry-errors`；旧记录会归档。配置、源码、资产、环境和 provider 身份变化时
拒绝恢复，应创建新目录。

| 输出 | 内容 |
|---|---|
| manifest.json | 配置、候选/源码/资产哈希、Git 状态、包版本、provider、评分版本、观测配置、固定策略模式、计划、seed 划分 |
| filters/seedN.json | 每个 seed 共享的完整筛选或筛选异常 |
| trials/METHOD/TASK/seedN_repeatR.json | 完整 filter、selection、episode、repeat、原始回答和异常 |
| summary.json | 各方法/任务统计、配对比较、分母与排除数 |
| media/ | 按 repeat、方法和任务区分的视频与图片 |

每个 seed 只筛选一次；每次物理 episode 都新建世界。结果逐条原子保存，运行目录有
进程锁。`validate` 离线检查配置，不调用模型或要求 API key；`summarize` 只读取已保存记录。
退出码 0 表示运行完整且没有运行/协议异常，物理失败需读取 `episode.success`。

## 8. 统计口径

summary 区分 completed、空候选、筛选异常、选择异常、非法选择、执行异常和未完成。
物理成功/碰撞/掉落标记的分母只用 completed；缺失位姿误差保持 null，均值不补零。
同时报告计划覆盖率、可行 seed 覆盖率、有效样本数、原始输出有效性、修复与回退率、
选择耗时、repeat 一致性、缓存以及 token 用量。缓存里的原始 token 用量与本次新调用
的 `live_token_usage` 分开，后者也不是账单金额估算。

配对结果仅使用共同完成、task/seed/repeat/候选哈希/控制器/manifest 相同的样本，
排除数单列。独立可接受集合由 B 提供，配置 `acceptable_sets` 路径后才能计算任务
可接受选择比例，否则该指标为 null。支持如下格式：

```json
{"pour_prep": ["G02"], "handover_prep": {"0": ["G03"], "default": ["G02", "G03"]}}
```

这是文件结构示意，不是任务真值。标签必须独立冻结；代码不会从物理成功反推标签，
也不会把标签加入 prompt。当前候选少且两个任务上都较容易成功，结果主要用于接口
验收；正式选择器收益仍需 B 补齐有任务区分度的候选并冻结评分与独立标签。A 的共同固定目标与完整保持判定已接入本版。


## 9. 与 A/B 主分支对齐

C 使用 A/B/C 的公共协议。仓库实际默认分支是 `main`，不存在 `master`。
C 的主实验只选择抓取 ID，所有方法共用 A 的 `policy_mode=fixed`；逐步
primitive 控制属于另一条实验轨道，当前批量配置会拒绝混入 incremental 模式。

每个配置都显式记录 `observation_config`，同一份规范化配置用于筛选、任务执行
及 manifest。可测试观测估计误差或接入外部估计位姿，例如：

```json
{
  "policy_mode": "fixed",
  "observation_config": {
    "object_pose": {"position": [0.55, 0.0, 0.645], "quaternion": [0.0, 0.0, 0.0, 1.0]},
    "source": "calibrated_prior",
    "position_noise_m": 0.003,
    "yaw_noise_deg": 2.0,
    "noise_seed": 1000
  }
}
```

`object_pose` 是标定先验或外部估计，不从仿真真实杯子位姿取得；noise_seed 仅控制
估计扰动，与环境 seed、LLM 的 sample_id 分开。观测配置、评分版本或策略模式不同
时不能作配对比较；恢复运行也会拒绝配置变化或不匹配的筛选缓存。

```bash
python -m grasp_c run --config configs/c.mock.noisy.json --out results/local/c_mock_noisy
```

B 已提供候选生成、实验性几何评分和 Random/Geometry prototype，详见主 README
第 9 节。当前 C 的 API 冒烟配置仍使用 G01/G02/G03 pilot 候选，避免把 API
接入变动与候选集变动混在一起；正式 M0/M1/M2 定义、评分权重和独立任务标签仍待冻结。
五字段公开候选、选择器函数签名和 acceptable_sets 标签接口与共享协议一致。
GEOMETRY_REFERENCE 及其回退明确使用输入分数，不声称执行 B 的正式 M1。
使用 B 导出的 `candidates.scored.json` 时，可替换配置中的 `candidates` 路径；
内部几何 sidecar 不进入 prompt。正式比较前应全组确认评分版本与候选集，创建新的
输出目录，不能把参考算法重命名成正式实验结果。

## 10. 显式可选：OpenAI API key 方式

默认 GPT 不需要本节。仅当组员明确希望使用额外计费的 OpenAI API 时，才在
[OpenAI Platform](https://platform.openai.com/api-keys) 创建 key，并在本机设置
`OPENAI_API_KEY`。其 API 计费和用量独立于 ChatGPT 订阅。

```bash
export OPENAI_API_KEY='你的 OpenAI API key'
python -m examples.llm_smoke --provider openai --model gpt-6-luna --out results/local/openai_smoke.json
python -m grasp_c run --config configs/c.openai.json --out results/local/c_openai
```

`configs/c.api.json` 将 GPT 的 OpenAI API 和 Gemini API 放在同一实验中，需要两个
key；与默认 `configs/c.multimodel.json` 的 Codex GPT 方式不同。OpenAI 适配器使用
官方 SDK 的 Responses API、`store=False` 和严格 JSON Schema。此方式尚未使用
当前账户完成真实联网验证；Codex 登录的成功记录不能替代 API key 验证。

官方参考：[OpenAI quickstart](https://developers.openai.com/api/docs/quickstart)、
[OpenAI Python SDK](https://developers.openai.com/api/reference/python)、
[OpenAI 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。
