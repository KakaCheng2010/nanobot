# 报告模板

最终回答请严格使用这个结构。

## 总体结论

- 主机：`host`
- 检查范围：`accounts/password_policy/firewall`
- 总体结果：`pass | fail | partial | uncertain`
- 备注：一小段总结

## 检查结果

### 1. `check_id`

- 检查名称：`...`
- 命令来源：`脚本文件名`
- SSH 执行结果：`success | failed`
- 退出码：`...`
- 截图路径：`绝对路径`
- 观测事实：
  - `fact 1`
  - `fact 2`
- 命中的 Milvus 规则：
  - `rule_id`
  - `title`
  - `pass_condition`
  - `fail_condition`
- 合规判定：`pass | fail | uncertain`
- 判定理由：`一段话`
- 整改建议：`一段话`

每个检查项都重复一段。

## 最终判断

- `pass`：所有检查项均满足要求
- `fail`：至少一项明确不合规
- `partial`：同时存在合规项和不合规项
- `uncertain`：证据不足、执行失败或 Milvus 命中不可靠
