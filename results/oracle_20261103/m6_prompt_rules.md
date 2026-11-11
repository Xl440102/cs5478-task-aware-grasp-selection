# M6 纯规则基线（`M6_PROMPT_RULES`）

`grasp_c/rules.py` 把 `prompt_version="v2"` 任务描述里写的抓法优先顺序直接用代码执行，不调用 LLM。同一档内取 `geometry_score` 最高者，再按 ID。只读公开字段。

这些规则是看过 seed 10–29 的执行结果后写的，所以下表是回顾性参照，不是盲测。

留出 seed 10–29，20 个 seed 中成功数（括号内为相对 M0 的胜/负与精确符号检验 p）：

| 数据表 | 搬运 | 交接 | 倒水（代理） | 倒水（真实流体） |
| --- | --- | --- | --- | --- |
| 第三轮 `wide_strict_v2` | 18 (3/0, p=0.25) | 20 (9/0, p=0.004) | 18 (6/1, p=0.12) | 16 (4/4, p=1.00) |
| 第四轮 `wide_strict_v3` | 19 (1/1, p=1.00) | 20 (6/0, p=0.03) | 18 (9/2, p=0.07) | 18 (4/2, p=0.69) |

对照：第三轮表上 GPT + v2 为 18/20/18/16，Gemini + v2 为 19/20/18/16（`docs/PART_C_EVALUATION_20261106.md`）。M6 选中的抓取族与 GPT + v2 相同（搬运把手 19、杯沿 1；交接杯沿 20；倒水顶抓 20）。

第四轮两个目录的 `report*.md` 已含 M6 一行。第三轮两个目录是 C 正式评估的冻结输入（其 manifest 校验 `meta.json`、`selections.json` 的哈希），所以未改写；上表第三轮数字由同一命令在副本上生成，其余方法的数字与存档完全一致。

复现（第四轮，所有筛选与执行均已缓存，不会重新仿真）：

```bash
python -m grasp_c.oracle --out results/oracle_20261103/r4_v3p_wide_strict_v3 --generator multifamily_v3p --scene wide_strict_v3 --tasks transport_place handover_transfer pour_water --liquid proxy_margin --workers 2 --seeds $(seq 0 29) --execute all --prior-from results/oracle_20261103/r4_v3p_wide_strict_v3 --prior-seeds $(seq 0 9) --report-seeds $(seq 10 29)
```
