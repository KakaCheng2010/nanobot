---
name: mlps-linux-baseline
description: 通过 SSH 对远程 Linux 主机执行等保风格的基线检查，自动截图命令结果，结合 Milvus 中的规则库判断账户、口令策略、防火墙配置是否合规。适用于需要对 Linux 主机做等保基线巡检、自动测试或输出结构化合规报告的场景。
---

# 等保 Linux 基线检查

使用远程 SSH 检查 Linux 主机的基础安全配置，并结合 `milvus_search` 输出结构化合规报告。

## 执行流程

1. 从用户消息中提取 SSH 连接参数。
   - 必须包含 `host`、`username`
   - 认证方式二选一：
     - `password`
     - `private_key_path`
   - 未提供 `port` 时默认使用 `22`

2. 确定本轮检查项。
   - V1 支持：
     - `accounts`
     - `password_policy`
     - `firewall`
   - 如果用户没有明确指定，默认只执行一个检查项。
   - 默认优先级：
     - `accounts`
     - `password_policy`
     - `firewall`
   - 不要在默认情况下把三项一起执行完，否则容易导致超时。
   - 只有用户明确要求多项检查时，才进入分步执行模式。

3. 先在本地生成远程执行命令。
   - 使用 `scripts/package_for_ssh.py`
   - 输入某个检查脚本路径
   - 输出一段可直接交给 `ssh_exec` 的远程命令

4. 逐项调用 `ssh_exec`。
   - 每次只执行一个检查项
   - 必须设置 `capture_screenshot=true`
   - 保留以下证据：
     - 执行命令
     - `stdout`
     - `stderr`
     - `exit_status`
     - `screenshot_path`

5. 针对每个检查项调用 `milvus_search` 检索规则。
   - 查询词应包含：
     - 检查项名称
     - 关键观测字段
     - 风险词
     - 典型命令关键词
   - 示例：
     - `uid=0 root 超级账户 linux 基线`
     - `minlen 口令长度 pam pwquality 合规`
     - `firewalld 防火墙 启用 默认策略 开放端口`

6. 保守判定合规性。
   - `pass`：观测结果明确满足命中的规则 `pass_condition`
   - `fail`：观测结果明确符合规则 `fail_condition`
   - `uncertain`：证据不足、命令失败、截图不足以支撑判断，或 Milvus 命中不可靠
   - 如果 Milvus 返回弱相关结果，不要编造规则

7. 输出结构化报告。
   - 严格参考 `references/report-template.md`
   - 每个检查项单独成段
   - 每段必须保留截图路径

## 分步返回规则

- 默认策略：单项执行、单项返回
  - 如果用户没有明确指定多个检查项，只执行一个检查项并立即返回结果
  - 不要继续自动执行第二项或第三项

- 多项策略：逐项执行、逐项返回
  - 如果用户明确要求执行多个检查项，不要把所有检查项做完后再一次性总结
  - 每完成一个检查项，就立即返回该检查项的结果
  - 返回内容至少包含：
    - 当前检查项名称
    - 命令执行结果摘要
    - 截图路径
    - Milvus 命中规则
    - 当前项合规结论
    - 下一步将执行的检查项（如果还有）

- 超时保护
  - 如果当前会话看起来有 API 超时风险，优先缩小本轮范围，只执行一个检查项
  - 如果用户要求“三项都查”，先完成第一项并返回，再提示继续下一项
  - 默认不要为了追求一次性完成而牺牲成功率

## 检查项映射

- `accounts`
  - 脚本：`scripts/check_accounts.sh`
  - 关注点：UID=0 账户、空口令账户、测试/默认账户

- `password_policy`
  - 脚本：`scripts/check_password_policy.sh`
  - 关注点：`minlen`、复杂度、过期策略、失败锁定

- `firewall`
  - 脚本：`scripts/check_firewall.sh`
  - 关注点：防火墙启用状态、默认策略、对外监听端口

## 本地辅助脚本用法

在调用 `ssh_exec` 前，先在本地运行打包脚本，把检查脚本转换成远程命令。

示例：

```bash
python nanobot/skills/mlps-linux-baseline/scripts/package_for_ssh.py nanobot/skills/mlps-linux-baseline/scripts/check_accounts.sh
```

将打印出的整段命令作为 `ssh_exec` 的 `command` 参数使用。

## 判定原则

- 优先以脚本输出为主证据
- 截图作为留痕证据和辅助核验
- 如果 SSH 执行失败，通常标记为 `uncertain`，除非失败本身已经能证明不合规
- 如果脚本输出中含有 `RESULT: FAIL`，可视为强提示，但最终结论仍要结合 Milvus 规则判断
- 如果 Milvus 返回多条规则，优先选择与当前检查项和观测字段最匹配的一条

## 注意事项

- 这套技能用于第一阶段可行性验证，不等同于完整等保测评
- 脚本尽量兼容常见 Linux 发行版，但实际环境可能需要微调
- 如果远程主机缺少某些命令，要在报告中明确记录，并将该项标记为 `uncertain`，除非存在可确认的不合规证据
- 默认以“成功返回结果”为优先目标，宁可拆成多轮，也不要在一轮内堆太多 SSH 检查和规则检索
