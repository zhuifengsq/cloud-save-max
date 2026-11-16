# F2：通知组织整理

日期：2026-11-16。本地实现，未提交、未推送，远端CI及真实渠道联调未运行。

## 职责与兼容

旧 legacy_notify 的全部23渠道实现、同步扇出和配置快照锁迁到 channel_delivery.py；未删除任何在用渠道。filtering.py 保存纯关键词过滤；sender.py 负责同步测试发送、运行时过滤与排队、唯一消费者装配和关闭队列开关后的同步发送。queue.py 保持有界单消费者、队满丢弃、异常继续消费的语义。完成 api、Telegram、包入口、TaskRunNotifier 和测试全部引用迁移后删除旧文件。

同步测试通知继续直接返回渠道结果；运行时默认入队返回，关闭 use_notify_queue 后仅同步发送一次。队满不会补偿直发，避免重复发送。TaskRunNotifier 现在将队满的 False 反馈给调用方（原实现无条件True），不改变业务提交结果。修复 sender 同步异常被静默吞掉的问题，仅记录异常类型、不输出凭据或原始异常，维持通知尽力而为，不推翻已经提交的业务执行。渠道HTTP参数/返回结构、关键词过滤默认值和队列容量1000不变。

本轮保留渠道发送时的模块配置快照锁和每渠道线程扇出，未宣称渠道内部已完全无状态或取消所有线程。发送实现仍完整保留，没有简单按 legacy 文件名删除能力。

## 测试证据

先在原实现运行新增行为测试和既有队列测试：

```text
python -m pytest regression_tests/test_notification_delivery.py regression_tests/test_notify_queue.py -q --basetemp .tmp/f2-red
1 failed, 29 passed in 2.21s
```

失败明确来自运行时同步发送异常没有诊断日志；其他29项形成迁移前基线。修复、迁移后同一组通过，并补全真实渠道序列化测试：

```text
python -m pytest regression_tests/test_notification_delivery.py regression_tests/test_notify_queue.py -q --basetemp .tmp/f2-transports
53 passed in 1.62s
```

覆盖23渠道（Bark、钉钉、飞书、go-cqhttp、Gotify、iGot、Server酱、PushDeer、Synology Chat、PushPlus、WePlus、Qmsg、企业微信应用/机器人、Telegram、Aibotk、SMTP、PushMe、Chronocat、DoDo、Webhook、ntfy、WxPusher）的配置选择、标题/正文、发送一次与结果归属；真实渠道函数使用假 requests/SMTP 校验序列化到传输边界。企业微信获取token+发送两次请求、SMTP登录/收件人/正文、Telegram token/chat/proxy、WxPusher topicIds/uids另有断言。所有值为假凭据，没有实际网络发送。

另覆盖渠道关闭、渠道异常、关键词拦截、队满不直发、消费者异常继续下一条、同步测试与异步运行分离、关闭开关同步发送、TaskRunNotifier配置/过滤与容量拒绝。最终F1/F2组合命令：

```text
python -m pytest regression_tests/test_run_task_use_case.py regression_tests/test_task_run_phases.py regression_tests/test_drama_run_schedule.py regression_tests/test_drama_lead_in_trigger.py regression_tests/test_ports_conformance.py regression_tests/test_task_run_conflicts.py regression_tests/test_notification_delivery.py regression_tests/test_notify_queue.py -q --basetemp .tmp/f-acceptance
109 passed in 5.43s
```

均从backend运行，设置XXM_TESTING=1、DATABASE_URL=sqlite://、RUN_MIGRATIONS=0、SCHEDULER_ENABLED=0。独立工具 Ruff通过，mypy strict通过。最终完整测试和分层检查由主报告记录，不用假传输测试代替真实渠道可用性。

最后将同步及入队错误日志改为仅异常类型后，同一组合用 `--basetemp .tmp/f-final` 再验：**109 passed in 4.86s**；Ruff `All checks passed!`。

## 提交归属和回滚

本包不新增数据库提交点、模型或迁移。渠道迁移、sender/filtering/queue、api/Telegram/notifier调用方与测试应作为同一回滚单元；恢复旧文件必须同时恢复所有引用。保持F1执行入口删除与其他包修改，不使用整仓reset恢复。
