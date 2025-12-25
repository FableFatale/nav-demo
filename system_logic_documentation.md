# 「无人机—无人车协同救援仿真系统」统一逻辑与 UI 规范文档（协同版）

---

## 0️⃣ 系统设计总原则（必须先读）

### 核心原则（一句话）

> **所有“协同”，必须由系统调度层显式体现，而不是由单个智能体隐式完成。**

### 三条不可破坏的底线

1. UAV **只能发现事实**，不能确认目标
2. TARGET_CONFIRMED **只能由系统触发**
3. UI **必须能看到“确认前的协同过程”**

---

## 1️⃣ 系统实体与角色划分（非常关键）

### 1.1 实体层级划分

| 层级  | 实体                 | 是否真实存在   |
| --- | ------------------ | -------- |
| 世界层 | Human              | ✅（真实对象）  |
| 执行层 | UAV / UGV          | ✅        |
| 决策层 | System Scheduler   | ✅（逻辑存在）  |
| 表达层 | Collaborative Task | ❌（UI 抽象） |

⚠️ **Collaborative Task 只是 UI 视角的聚合，不是新后端对象**

---

## 2️⃣ 关键地点与路线（Locations & Routes）

### 2.1 固定地点

* **A 点（基地 / Base）**
  `(-50, 0, 50)`

  * UAV / UGV 起点
  * UGV 返回点

* **B 点（搜索中心 / Search Center）**
  `(0, 0, 0)`

  * 核心搜索区

* **C 点（巡逻路径点 / Patrol Point）**
  `(50, 0, -50)`

  * 仅用于巡逻路径闭环
  * ⚠️ **不是救援目标点**

---

### 2.2 UAV 巡逻路线（固定）

```text
A → B → C → A（循环）
```

规则：

* UAV **永远不因为目标确认而改变路线**
* UAV 是否返航，只取决于 **自身状态机**

---

## 3️⃣ Human（目标人物）数据模型

### 3.1 Human 数据结构（逻辑）

```json
{
  "id": "T1",
  "position": { "x": 10, "y": 0, "z": -10 },
  "state": "UNSEEN | DETECTED | CONFIRMED | RESCUED",
  "detected_since_tick": null,
  "detected_by": []
}
```

### 3.2 Human 是「因果起点」

> **所有任务都从 Human 的存在开始，而不是从 UAV 行为开始**

---

## 4️⃣ 系统事件定义（协同的核心）

### 4.1 关键事件表

| 事件名                | 触发者    | 含义   |
| ------------------ | ------ | ---- |
| `HUMAN_DETECTED`   | UAV    | 发现事实 |
| `TARGET_CONFIRMED` | System | 协同确认 |
| `UGV_DISPATCHED`   | System | 执行决策 |
| `TARGET_RESCUED`   | UGV    | 结果完成 |

---

## 5️⃣ 协同因果链（这是系统灵魂）

```text
Human 存在
↓
UAV 探测（事实）
↓
系统聚合（协同中）
↓
系统确认（TARGET_CONFIRMED）
↓
UGV 执行
↓
任务完成
```

⚠️ UI、日志、状态机 **必须严格遵循此顺序**

---

## 6️⃣ UAV 行为逻辑（去个体中心化）

### 6.1 UAV 状态机

* `IDLE`
* `TAKEOFF`
* `PATROL`
* `REPORTING`
* `RETURN`

---

### 6.2 UAV 发现逻辑（只做一件事）

当满足：

```text
distance(UAV, Human) < 15
AND Human.state == UNSEEN
```

执行：

1. 触发 `HUMAN_DETECTED`
2. Human.state → `DETECTED`
3. 记录：

   * `detected_since_tick`
   * `detected_by += UAV_ID`
4. UAV → `REPORTING`

⚠️ UAV **不知道是否会被确认**

---

### 6.3 UAV REPORTING 行为

* 飞至目标上空
* 高度：10
* 悬停：60 ticks
* **不参与确认决策**

结束后：

* 返回 `PATROL`
* 或执行 `RETURN`（如果策略如此）

---

## 7️⃣ 系统协同确认逻辑（唯一决策点）

### 7.1 系统调度器每 tick 检查

对于所有 `DETECTED` Human：

```text
IF
  (current_tick - detected_since_tick ≥ 40)
  OR
  (len(detected_by) ≥ 2)
THEN
  TARGET_CONFIRMED
```

### 7.2 TARGET_CONFIRMED 的含义

* Human.state → `CONFIRMED`
* 这是：

  > **系统级判断，不是感知事实**

---

## 8️⃣ UGV 行为逻辑（执行层）

### 8.1 出动条件

```text
存在 CONFIRMED Human
AND 存在 STANDBY UGV
```

系统触发：

```text
UGV_DISPATCHED
```

---

### 8.2 UGV 状态机

* `STANDBY`
* `DISPATCH`
* `RESCUING`
* `RETURNING`

---

### 8.3 救援完成

* 停留 40 ticks
* 触发 `TARGET_RESCUED`
* Human.state → `RESCUED`

---

## 9️⃣ Mission Phase（全局任务阶段）

### 9.1 阶段定义

| 阶段       | 含义               |
| -------- | ---------------- |
| READY    | 系统就绪             |
| PATROL   | UAV 工作           |
| RESCUE   | 至少一个 CONFIRMED   |
| COMPLETE | 所有 Human RESCUED |

### 9.2 Phase 只由系统改变

⚠️ UAV / UGV **不能切换 Phase**

---

## 🔟 UI 协同表达规范（重点）

---

### 10.1 新增 UI 抽象：协同任务（UI-only）

Sidebar 中新增：

```text
协同任务（Collaborative Tasks）
```

---

### 10.2 单个任务 UI 表达

```text
Task · T2
状态：Confirming
参与 UAV：uav_1, uav_3
确认方式：Multi-UAV
```

状态映射：

| UI 状态      | 数据来源              |
| ---------- | ----------------- |
| Confirming | DETECTED + ≥1 UAV |
| Confirmed  | TARGET_CONFIRMED  |
| Rescuing   | UGV_DISPATCHED    |
| Completed  | TARGET_RESCUED    |

---

### 10.3 3D 协同可视化规则

#### DETECTED（协同中）

* Human：黄色双圈（脉冲）
* UAV 雷达圈：同色
* UAV → Human：淡黄色虚线

#### TARGET_CONFIRMED（系统决策）

* Human：红色实体
* 中心文字（1 秒）：

  ```text
  SYSTEM CONFIRMED
  Reason: Multi-UAV
  ```

---

### 10.4 Realtime / Playback 统一规则

| 模式       | 行为        |
| -------- | --------- |
| Realtime | 有脉冲、有确认动画 |
| Playback | 只复现，不做判断  |

左上角明确标注：

* `REALTIME · Live Decision`
* `PLAYBACK · Historical Replay`

---

## 11️⃣ 任务开始 / 协同 / 结束（完整闭环）

### 开始

* 点击「实时模式」
* Phase：READY → PATROL
* UAV 起飞

---

### 协同中

* UAV 探测 → HUMAN_DETECTED
* UI 显示「Confirming」
* 多 UAV 覆盖可见

---

### 决策

* 系统触发 TARGET_CONFIRMED
* UI 明确显示「System Confirmed」

---

### 执行

* UGV_DISPATCHED
* UI 显示「Rescuing」

---

### 结束

* TARGET_RESCUED
* Phase → COMPLETE
* 所有单位返航

---

## ✅ 你当前系统 vs 该文档

### 现在的问题

* 看不到「确认前的协同」
* UAV 行为像在“自己决定”
* UI 状态与系统逻辑不一致

### 使用本方案后

> **任何观察者都能明确说出：
> “这是系统在等多机确认，然后统一下达救援。”**

---

## 🔚 结论（一句话）

> **你现在不是缺算法，是缺“协同的显性表达层”。
> 这份文档，补的正是这一层。**
