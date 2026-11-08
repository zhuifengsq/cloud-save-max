"""仓储实现（infrastructure → persistence 是允许方向）。

应用层（app.application / app.application）不要直接 import 这里的模块，
而是通过 app.application.container 的访问器拿仓储：容器内部用函数级延迟
import 完成装配（composition），这样分层门禁里不会出现 application→persistence。
"""