# xiaoai-ai

本项目是独立 Git 仓库，本文件所在目录也是 Git 操作的仓库边界。

以下工程技能配置以本文件所在目录为项目根目录。执行项目的 issue、triage 或领域建模任务时，读取对应的 `docs/agents/` 配置。

## Agent skills

### Issue tracker

Issues and specs are tracked as local Markdown under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Local issues use the five default canonical triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Domain documentation uses a single-context layout with root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.

## 上游开发指引

当前根目录基于 `coderzc/open-xiaoai-bridge`；修改 Bridge 的 Python/Rust 实现或运行脚本前，读取 [上游架构与开发约束](docs/agents/upstream.md)。提交使用中文 Conventional Commits。

当前产品范围以 [原生唤醒一期 spec](.scratch/native-wake/spec.md) 为准：自定义词唤起原生小爱。上游说明描述原版行为；本地 `NATIVE_WAKE_ENABLE=1` 独立模式的差异与运行条件见 [运行手册](docs/native-wake-runbook.md)。涉及改变启动约束时，明确记录并验证。

原生唤醒测试运行 `python3 -m unittest discover -s tests -p test_native_wake.py -v`；Bridge 测试按变更选择，连接真实服务或设备的测试需具备对应授权和环境。
