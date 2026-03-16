# 股票分时买点监控脚本说明

当前版本已经删除旧的“分时一 / 分时三”规则，只保留下面两套新策略。

## 文件位置

- 脚本文件：[stock_intraday_pattern_watch.py](stock_intraday_pattern_watch.py)
- 配置文件：[stock_intraday_pattern_watch.json](stock_intraday_pattern_watch.json)

## 策略一

规则顺序如下：

1. 开盘后分时涨幅达到 `2.5%` 及以上形成的最高点，定义为 `右1`
2. `右1` 下跌后，低于 `右1` 的第一个次高点，定义为 `右2`
3. `右2` 下跌后再次上涨，首次突破 `右2` 高点的位置，定义为 `左1`
4. `左1` 下跌后形成的第一个次高点，定义为 `左2`
5. 再次下跌后向上突破 `左2`，触发 `买入点`

脚本输出：

- `形态 = 策略一`
- `买点 = 突破左二`

## 策略二

规则顺序如下：

1. 开盘后分时涨幅达到 `5%` 及以上形成的最高点，定义为 `右1`
2. `右1` 下跌后形成的第一个次高点，定义为 `右2`
3. `右2` 下跌后再次上涨，首次突破 `右2`，触发 `买入点`

脚本输出：

- `形态 = 策略二`
- `买点 = 突破右二`

## 主要参数

最常改的是这些字段：

- `symbols`
- `right1_search_minutes`
- `strategy1_right1_min_pct`
- `strategy2_right1_min_pct`
- `min_pullback_pct`
- `breakout_buffer_pct`
- `signal_cooldown_minutes`
- `paper_trading_enabled`
- `paper_trade_amount`
- `paper_max_positions`
- `paper_max_entries_per_symbol`
- `paper_force_exit_time`

## 软件里怎么用

1. 打开“脚本策略”
2. 启动 `股票分时买点监控`
3. 去脚本日志看实时提醒
4. 打开 `历史信号结果` 做最近几天回放
5. 打开 `模拟交易` 查看纸面账户、持仓和成交

## 当前实现说明

- 数据源是公开 A 股分时接口
- 当前版本只做 `信号提醒 + 股票纸面交易`
- 不自动下单
- 如果同一时刻两套策略都满足，脚本优先输出先识别到的那一套
