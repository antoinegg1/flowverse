# Agent Workflow 的 Pareto 最优理论框架

> 状态：研究笔记 / 设计草案  
> 更新日期：2026-09-04  
> 适用对象：Flame Chase（FC）、Parallel Flame Chase（PFC）、PFC Git/PR 及后续自适应 workflow

## 1. 核心结论

长期运行的 coding-agent workflow 不应被直接建模成普通多臂老虎机，也不应被字面理解成梯度下降。更合适的主模型是：

> **一个带共享可变状态、异构执行者、延迟反馈、动态候选和多种资源成本的 composite anytime search；其调度器是 contextual bandit / rational metareasoning controller。**

并行与随机梯度优化文献仍然很重要，但它提供的是结构性类比和局部理论：

- 随机并行 coordinate descent：并行收益取决于不同更新的可分离性和冲突图；
- HOGWILD!/异步 SGD：低冲突的稀疏更新可以少同步，陈旧更新需要按 staleness 降权或复核；
- Local SGD/FedAvg：lane 可以先独立运行若干步再同步，但同步过少会产生 local drift；
- doubly stochastic gradient：提示我们分开控制“搜索随机性”和“评估随机性”，但 kernel 领域的原始算法与 coding workflow 只有弱类比；
- stochastic compositional optimization：对“候选修改 → 测试/评估 → best-so-far”这种嵌套、带噪目标更直接。

本文采用以下六层框架：

1. 用 **composite anytime algorithm** 表达整个 workflow；
2. 用 **algorithm portfolio** 表达并行 lane；
3. 用 **contextual bandit / Value of Computation（VOC）** 做动态调度；
4. 用 **performance–wall-time Pareto front** 作为主要评价；
5. output token 只作为二级约束和 tie-break；
6. 对共享 Git、分支漂移和合并影响采用经验交互模型，不强求不现实的全局闭式证明。

## 2. 问题定义

### 2.1 状态、动作与反馈

在调度时刻 `t`，系统状态可写为：

\[
s_t = (x_t, b_t, \mathcal{B}_t, \mathcal{H}_t, \mathcal{R}_t, c_t)
\]

其中：

- \(x_t\)：共享 `main` 上的当前程序状态；
- \(b_t\)：截至当前的 best-so-far evaluator score；
- \(\mathcal{B}_t\)：各 lane 的 branch、base commit、head commit 和工作区状态；
- \(\mathcal{H}_t\)：各 lane 的动作、假设、测试和得分历史；
- \(\mathcal{R}_t\)：report、knowledge、待合并 PR 和冲突信息；
- \(c_t\)：累计 wall time、active compute、input/output token、eval 和同步成本。

调度动作包括：

\[
a_t \in \{\text{spawn},\text{continue},\text{pause},\text{stop},
\text{evaluate},\text{review},\text{rebase},\text{merge},\text{share}\}
\]

每个动作产生随机结果：

\[
o_t = (\Delta p_t,\Delta \tau_t,\Delta z_t,\Delta m_t,\Delta r_t)
\]

- \(\Delta p_t\)：性能变化；
- \(\Delta \tau_t\)：wall-clock time；
- \(\Delta z_t\)：output token；
- \(\Delta m_t\)：merge/rebase/同步成本；
- \(\Delta r_t\)：失败、回归或冲突风险。

这里的动作空间会被 agent 新产生的 hypothesis 扩展，因此 arms 不是预先固定的。

### 2.2 Anytime 性能

令 \(L\) 为 evaluator loss；在当前 AOPT 语境中 valid cycles 越低越好。一次 run 的 anytime 轨迹为：

\[
B(\tau,z)=\min_{k:\,\tau_k\leq\tau,\,z_k\leq z} L_k
\]

比较 workflow 时必须使用 best-so-far 曲线，而不是只比较最终一次 evaluation。至少保留三种横轴：

- active elapsed time；
- cumulative output tokens；
- max non-parallel output tokens，用于近似关键路径 token latency。

## 3. 目标优先级：performance ≈ time ≫ output token

### 3.1 不使用单一固定加权和

简单目标

\[
-p+\alpha\tau+\beta z
\]

把偏好隐藏在不稳定的单位换算中，也可能遗漏非凸 Pareto front。当前偏好应表达为层级 Pareto 关系。

### 3.2 主层：performance–time Pareto front

令点 \(u=(L_u,\tau_u)\)。如果：

\[
L_u\leq L_v,\quad \tau_u\leq\tau_v
\]

且至少一个严格小于，则 \(u\) 支配 \(v\)。主要结果应报告：

- 二维 `loss × wall time` Pareto front；
- time-to-target：达到固定 loss 阈值所需时间；
- best-at-deadline：在 2h、4h、8h、12h 等期限的最好结果；
- 二维 dominated hypervolume 或归一化面积。

### 3.3 次层：条件 token 效率

token 不应和 performance、time 平权。建议先保留主 Pareto front 附近的点：

\[
L \leq L^*+\epsilon_L,\qquad
\tau \leq \tau^*(L)+\epsilon_\tau
\]

再在这个集合中最小化 output token。报告：

- iso-performance token：达到相同 loss 所需 token；
- iso-time token：相同 wall time 内的 token；
- Pareto 邻域内的 token overhead；
- marginal token efficiency：\(-\Delta L/\Delta z\)。

这比三维无差别 Pareto front 更忠实于 `performance ≈ time ≫ token`：一个极慢但省 token 的 run 不会因此进入主要候选集。

## 4. 六层理论架构

### 4.1 Composite anytime algorithm：整个 workflow

每条 lane 是一个可暂停、恢复、终止的随机 anytime process；workflow 是这些 process 与 evaluator、merger、monitor 的组合。调度器需要同时区分：

- objective time：用户实际等待的 wall time；
- subjective time：各 process 累计 active time；
- computation cost：token、模型调用、CPU/GPU；
- composition cost：eval、review、rebase、merge、上下文同步。

这条理论线与 FC/PFC 的对应最直接，因为它本来就研究如何根据 performance profile 给多个可中断进程分配时间。

### 4.2 Algorithm portfolio：并行 lane

不同模型、prompt、seed 和 hypothesis 是具有相关成功概率与长尾运行时间的 portfolio 成员。并行的主要价值是降低 time-to-improvement 的尾部，而不保证降低总 token。

需要显式维持的不是 lane 数量本身，而是：

- 行为多样性；
- 失败时间分布的互补性；
- 对不同代码区域/假设的覆盖；
- 相关性与重复探索的惩罚。

### 4.3 Contextual bandit / Bandits with Knapsacks：资源分配

每条 lane 的预期收益依赖当前上下文，不满足固定 arm 的平稳假设。上下文至少包括：

- 最近得分改进率与距上次改进的时间；
- 当前 hypothesis 类型；
- branch staleness；
- 与其他 lane 的代码/测试/语义重叠；
- 模型、当前上下文长度和近期 token 速率；
- 未评估修改量、测试通过情况和冲突风险。

时间、token、eval 次数和并发槽分别是 knapsack resources。bandit 层负责长期 exploration/exploitation，不直接负责某次 merge 的正确性。

### 4.4 Rational metareasoning / VOC：单步决策

对动作 \(a\) 定义近似 Value of Computation：

\[
\operatorname{VOC}(a\mid s_t)
=\mathbb{E}[\Delta U\mid s_t,a]-C_{time}(a)-\lambda_z C_{token}(a)-C_{risk}(a)
\]

其中 \(\lambda_z\) 很小，只在 primary objectives 近似相同时起作用。VOC 用于判断：

- 是否值得再运行一轮；
- 是否现在做昂贵 full eval；
- 是否需要 review，而非每步都 review；
- 一个 stale PR 应 rebase、直接测试还是放弃；
- 是否值得由 coordinator 处理冲突；
- 是否应新增或回收 lane。

### 4.5 Hierarchical Pareto evaluator：比较 workflow

离线分析用 Pareto front；在线 controller 使用目标阈值、deadline 和局部 VOC。不要让 controller 为了优化一个跨任务固定 scalar score 而学习 task-specific 顺序。

### 4.6 Empirical Git interaction model：共享状态

Git 操作不是线性或可微更新，不能像梯度那样安全求和。应该直接学习：

\[
P(\text{improve},\text{clean merge},\text{no regression}
\mid \phi_{branch},\phi_{main},\phi_{interaction})
\]

模型可以从历史事件拟合，不要求证明任意程序空间上的全局收敛。

## 5. 为什么经典 bandit 假设失效

| 经典假设 | Workflow 中的问题 | 应对方法 |
|---|---|---|
| arms 固定 | agent 动态提出新 hypothesis | sleeping/rotting arms 或动态候选池 |
| arms 独立 | lane 共享 main、report、knowledge 和测试结果 | 显式相关性与 interaction features |
| reward 平稳 | main 每次 merge 后，所有 lane 的收益分布改变 | contextual/non-stationary model、时间衰减 |
| reward 立即出现 | eval、review、merge 后才知道真实收益 | delayed/censored feedback |
| pull 只消耗一种资源 | 每步同时消耗时间、token、eval 与合并成本 | bandits with knapsacks |
| reward 可重复采样 | 一次 merge 会永久改变未来搜索路径 | MDP/POMDP 或经验状态转移模型 |
| 同时 pull 互不干扰 | lane 可能修改相同文件、覆盖或重复探索 | 冲突图、分支 staleness、因果日志 |

因此 bandit 是 scheduler 的近似组件，不是完整系统理论。

## 6. 随机并行优化文献如何映射到 workflow

### 6.1 Parallelized SGD：独立探索后聚合

并行 SGD 的经典形式让 worker 从共同初始参数出发，处理不同随机样本，随后平均或聚合更新。对应到 Git/PR：

- 公共初始模型参数 → 共同 base commit；
- worker update → lane 的 commit/PR；
- model averaging → 测试后选择、合并或手工整合；
- communication round → report/main update/rebase/merge。

关键限制是：代码 patch 没有自然的向量平均操作。因此只能迁移“并行时间收益、独立噪声、聚合频率”的理论结构，不能直接迁移 SGD 收敛率。

### 6.2 Randomized parallel coordinate descent：冲突稀疏度

这是 SGD 家族中对 coding workflow 最有启发性的方向之一。把代码库分成文件、symbol、test 或行为模块后，一条 lane 的修改近似一个 block update。

并行加速取决于 partial separability：

- 如果 lane 修改互不相关模块，接近线性 wall-time 加速；
- 如果多条 lane 集中在同一 hotspot，并行度增加只会带来冲突和重复；
- 最佳并行度应由运行时冲突图决定，而非固定为 1、2 或 4。

定义 lane 冲突图 \(G_t=(V,E)\)，边权可由下式组合：

\[
w_{ij}=\alpha J_{file}(i,j)+\beta J_{symbol}(i,j)
+\gamma J_{test}(i,j)+\delta S_{semantic}(i,j)
\]

其中 `J` 是集合 Jaccard overlap，\(S_{semantic}\) 是 hypothesis/report 的语义相似度。调度器优先并行低边权的 lane。

### 6.3 HOGWILD!：低锁共享更新

HOGWILD! 的核心结论依赖 sparse updates：当不同 worker 很少触碰相同坐标时，无锁异步更新可以接近理想并行加速。

对应到 Git：

- 稀疏梯度 → 修改少量且不同的文件/symbol；
- overwrite → merge conflict 或逻辑覆盖；
- lock → coordinator/reviewer 串行化；
- sparsity graph → patch conflict graph。

由此得到一个可检验命题：**只有观察到的 patch overlap/语义冲突率足够低时，减少 review 与同步才可能稳定省时。** 不能从 HOGWILD! 推导“所有 PR 都应自动合并”。代码冲突不仅是文本覆盖，还可能是测试可见的高阶语义交互。

### 6.4 Local SGD / FedAvg：本地多步与周期同步

Local SGD 让 worker 做 \(H\) 个本地更新后再聚合，减少通信，但过大的 \(H\) 会产生 drift。Git/PR workflow 几乎有同样的控制旋钮：

- \(H\) 小：频繁读取 main/rebase/report，信息新鲜但同步 token 和时间高；
- \(H\) 大：lane 深化充分、同步低，但 branch stale、重复探索和冲突上升；
- lane 异质：不同模型与 hypothesis 相当于 non-IID clients，drift 风险更高。

所以 Main Update Monitor 最合理的作用不是强制每次 rebase，而是提供低成本事件：

1. main 已更新；
2. 新 best score；
3. 与本 branch 的重叠与 staleness；
4. 由 lane/controller 决定继续、rebase、挑选部分变更或终止。

同步间隔应动态变化：早期探索可长，接近高质量解或热点冲突增加时应缩短。

### 6.5 Asynchronous SGD：陈旧更新与 straggler

异步 SGD 在减少等待慢 worker 的同时会引入 stale gradients。对 workflow 定义：

\[
d_i(t)=\#\{\text{main commits since lane }i\text{ base}\}
\]

但 commit count 不足以描述实际 staleness，还应加入：

- main 新提交与本 PR 的文件/symbol overlap；
- best score 改变量；
- 受影响测试集合；
- elapsed time；
- 当前 patch 的可拆分程度。

可以借鉴 staleness-adaptive step size，但对应动作不是数值缩小梯度，而是：

- staleness 低：直接测试并进入合并候选；
- 中等：只复核受影响测试，或重放 patch；
- 高且重叠低：保留独立成果并在新 main 上验证；
- 高且重叠高：让 coordinator 简短整合，或放弃低 VOC 修改。

### 6.6 Decentralized SGD 与 doubly stochastic mixing matrix

去中心化 SGD 中的 “doubly stochastic” 常指通信 mixing matrix 的行列和都为 1，并不等于“双重随机采样”。它研究 worker 通过邻居通信趋于共识。

当前 Git/PR workflow 有中央 `main` 和 merger，因此更像 parameter-server/local-SGD，而非完全去中心化 SGD。只有在 lane 之间直接交换 report、局部合并或 peer review 时，gossip/consensus 文献才成为主要模型。

### 6.7 Doubly stochastic functional gradients：两种随机近似

Dai 等人的 doubly stochastic gradient 同时采样训练点和随机特征，在 kernel function space 中得到无偏的双重随机梯度。可借用的设计思想是将两种随机性拆开记账：

\[
\text{search noise}\;\xi_t
\quad\times\quad
\text{evaluation noise}\;\zeta_t
\]

在 workflow 中可对应：

- \(\xi_t\)：选择 lane/model/hypothesis/seed 的随机性；
- \(\zeta_t\)：选择 smoke test、test shard、evaluator sample/seed 的随机性。

实际可做“两阶段评估”：大量便宜的随机 smoke/shard eval，只有提交候选才运行 full eval。这可能显著减少 eval 时间，但必须校准 selection bias 和假阳性率。

该论文的 \(O(1/t)\) 收敛结论依赖凸 RKHS 与无偏梯度，**不能**转移到程序搜索；它只提供双采样与成本分解的启发。

### 6.8 Stochastic compositional optimization：嵌套反馈

如果总体目标写成嵌套形式：

\[
F(\pi)=\mathbb{E}_{task}\left[
f_{task}\left(\mathbb{E}_{agent,seed,test}[g(\pi)]\right)
\right]
\]

那么直接用少量 noisy inner evaluations 优化 outer workflow policy 会产生偏差。SCGD 的 two-timescale 思路提示：

- 快时间尺度：更新 lane 局部成功率、token 率、冲突和 staleness；
- 慢时间尺度：更新跨任务 workflow policy 与资源分配先验；
- 不要因为单次 seed 的偶然结果立即重写全局策略。

这比 kernel doubly stochastic gradient 更接近我们的跨 task 学习问题。

## 7. 从理论到可实现的 controller

### 7.1 候选动作的收益估计

对每个候选动作估计：

\[
q_a=P(\text{产生新 best}\mid s_t,a)
\]

\[
g_a=\mathbb{E}[-\Delta L\mid\text{improve},s_t,a]
\]

以及成本 \(T_a,Z_a,M_a,R_a\)。可以使用保守的 acquisition score：

\[
A(a)=\frac{q_ag_a+\kappa\,\sigma_a}
{T_a+M_a+\varepsilon}
-\lambda_z Z_a-R_a-D_a
\]

- \(\sigma_a\)：不确定性奖励，用于探索；
- \(D_a\)：与其他 active lane 的重复/冲突惩罚；
- \(\lambda_z\)：较小 token 系数；
- deadline 临近时，提高 time 与 merge-risk 惩罚。

这个 acquisition 只用于在线启发式；最终比较仍采用层级 Pareto 指标，避免把临时权重误当成科学结论。

### 7.2 自适应控制循环

```text
initialize candidate lanes from model × hypothesis priors
while deadline and budget remain:
    update best-so-far performance and resource counters
    update conflict graph, branch staleness and lane posteriors
    generate actions: continue / pause / spawn / eval / rebase / merge

    reject actions outside hard safety and resource constraints
    preserve a small exploration quota for novel, low-correlation hypotheses
    select concurrent actions with high expected primary utility
        subject to CPU/RAM/model slots and conflict constraints

    run cheap validation for intermediate updates
    run full evaluation only for promotion/merge candidates
    merge only after estimating incremental value on current main
    decay or invalidate evidence made stale by main updates
```

### 7.3 与固定 phase strategy 的区别

controller 不预设“先串行后并行”或“始终四 lane”。并行度、同步率和 eval 率由观测状态决定：

- 高不确定性、高多样性、低冲突 → 增加并行探索；
- 已发现强方向但仍有独立子问题 → 并行局部深化；
- 高冲突、高重复、merge backlog → 降低并行度；
- stale lane 仍有独特低重叠修改 → 验证后保留；
- 目标附近且 deadline 临近 → 收缩搜索，增加 current-main 验证。

## 8. 共享 Git 的经验交互模型

### 8.1 建议特征

每个 PR/commit 至少提取：

- `base_sha`、`head_sha`、`main_sha_at_eval`；
- `commits_behind_main`、`seconds_since_base`；
- touched files、symbols、tests；
- insertions/deletions、patch size；
- 与已合并和 active PR 的 overlap；
- merge/rebase 是否冲突、处理耗时；
- pre-merge branch score、current-main score、post-merge score；
- hypothesis/report embedding 或离散主题；
- lane/model/seed、input/output token、active time；
- cheap test 与 full evaluator 的结果。

### 8.2 三个需要分别估计的概率

不要只拟合“这个 lane 会不会成功”，而应拆成：

\[
P_{useful}=P(\text{branch candidate improves its base})
\]

\[
P_{merge}=P(\text{clean or cheaply resolvable merge}\mid\text{useful})
\]

\[
P_{retain}=P(\text{improvement survives on current main}\mid\text{merge})
\]

期望增量价值近似为：

\[
EV \approx P_{useful}P_{merge}P_{retain}\,E[-\Delta L]
-C_{eval}-C_{merge}-C_{token}-C_{delay}
\]

这能区分“lane 本地表现很好”和“该 PR 对当前 main 真正有增量价值”。

### 8.3 Report/Knowledge 的理论位置

Report Share 可视为低成本通信；Knowledge Memory 可视为跨时间的压缩公共状态。它们同时有两种相反效应：

- 降低重复探索和 branch staleness；
- 增加 lane 相关性、锚定偏差和上下文 token。

因此 knowledge 量越大不一定越好。应测量条件互信息或更实际的 proxy：读到共享信息后，重复 patch/hypothesis 是否下降，以及跨 lane diversity 是否同时下降。

## 9. 可证明的简化模型与不能声称的结论

### 9.1 可以尝试证明

在下列简化条件下，可从 anytime scheduling、portfolio 或异步优化借用 regret/bound：

- lane 的 improvement-time distribution 在短窗口内平稳；
- lane 间相关性由固定 conflict graph 上界；
- staleness 和 merge delay 有界或有可估计尾分布；
- reward 使用固定 evaluator 且噪声可控；
- 候选池在一个 epoch 内固定；
- resource cost 可观测。

可研究的理论量：

- 在 deadline 下发现目标性能的概率；
- time-to-target 的期望和尾概率；
- 相对 oracle scheduler 的 contextual/Pareto regret；
- 冲突稀疏度与安全并行度之间的上界；
- 同步间隔对 drift、通信成本和 wall time 的界；
- delayed feedback 下的性能损失。

### 9.2 目前不应声称

- coding-agent update 是无偏梯度；
- Git merge 等价于向量平均；
- AOPT 的非凸、离散程序空间满足 SGD 光滑性或凸性；
- HOGWILD!/Local SGD 的收敛率可直接用于 PFC；
- 单一 task 上的 Pareto front 能推出跨 task 的最优 workflow；
- 全局 knowledge 一定减少探索成本；
- 更多并行 lane 一定提高 sample/token efficiency。

## 10. 可检验的研究假设

### H1：冲突稀疏度决定并行收益

控制模型和资源后，PFC 相对 FC 的 wall-time speedup 随 patch conflict graph 密度上升而下降。该假设来自 randomized parallel coordinate descent 与 HOGWILD!。

### H2：同步频率存在 U 型最优点

过于频繁同步浪费 token/time；过少同步增加 stale work、重复探索与 merge cost。不同 task 的最优同步间隔不同。该假设来自 Local SGD。

### H3：staleness 应按语义重叠而非 commit 数惩罚

相同 `commits_behind_main` 下，低 file/symbol/test overlap 的 PR 仍可能有高增量价值。Main Update Monitor 应提示而非强制 rebase。

### H4：双阶段评估可以降低时间和 token

cheap stochastic eval 用于筛选，full eval 只用于 promotion/merge；在校准假阳性后应改善 primary Pareto front，而不只改善 token。

### H5：动态并行度优于固定阶段策略

基于 diversity、conflict、staleness 和 recent improvement 的 controller，应在跨 task 平均的 performance–time hypervolume 上优于固定 FC/PFC 策略。

### H6：共享信息存在 diversity–redundancy trade-off

Report/Knowledge 在减少重复探索的同时可能造成 lane convergence。最佳共享内容应是短、可验证、增量式事件，而不是完整轨迹。

### H7：token-efficient review 必须由风险条件触发

取消所有中间 review 未必优于原始 Git/PR；按 patch size、测试覆盖、staleness、冲突和 expected improvement 触发 review 更可能保留性能，同时降低 token。

## 11. 推荐实验与分析顺序

1. **离线重建事件表**：对已有 FC/PFC/Git-PR run 对齐 score、wall time、token、commit、branch 和 merge 事件。
2. **冲突与重复分析**：计算 file/symbol/test/hypothesis overlap，验证 H1、H3、H6。
3. **同步间隔分析**：以 main update 到 lane 采纳/忽略的延迟为自然实验，验证 H2。
4. **评估漏斗回放**：用已有 cheap tests 预测 full eval，画 precision–recall 与节省成本曲线，验证 H4。
5. **离线 policy evaluation**：先用 replay/bootstrap 比较固定并行、successive-halving 和 VOC policy。
6. **小规模在线实验**：跨多个不同 task、至少两个 seed，比较 primary Pareto hypervolume 与 token tie-break。
7. **再做完整 8h/12h 实验**：避免只在单任务上调出 task-specific controller。

## 12. 评价报告规范

每个 workflow 至少报告：

| 维度 | 主指标 | 补充指标 |
|---|---|---|
| Performance | final/best valid cycles | improvement count、回归率 |
| Wall time | time-to-target、best-at-deadline | active time、等待时间 |
| Token | iso-performance output tokens | total/max-nonparallel、按角色拆分 |
| Parallelism | effective concurrent lanes | utilization、straggler time |
| Git | retained improvement rate | conflict、rebase、stale age、merge cost |
| Diversity | hypothesis/patch diversity | 重复探索率、共享后相关性 |
| Evaluation | full-eval precision | cheap/full eval 数与成本 |

统计时：

- 曲线画 seed mean，并显示 seed min–max 或 bootstrap interval；
- 只在所有 seed 的共同支持区间比较均值曲线；
- time-to-target 对未达到目标的 run 使用 censored survival analysis，而不是删除；
- 同时给出 absolute cost 和相对 FC/PFC baseline；
- 预先固定 primary target/deadline，避免事后挑点。

## 13. 与当前 Flowverse 文件的关系

- 串行基线：[Flame Chase](../../flows/flame_chase/)
- 原始并行基线：[Parallel Flame Chase](../../flows/parallel_flame_chase/)
- 当前 Git/PR Lite：[Parallel Flame Chase Git/PR](../../flows/parallel_flame_chase_git_pr/)
- Main Update Monitor 变种：[Git/PR Main Monitor](../../flows/parallel_flame_chase_git_pr_main_monitor/)
- token-efficient 变种（当前暂停实验）：[Git/PR Token Efficient](../../flows/parallel_flame_chase_git_pr_token_efficient/)

本机现有的核心对比图：

- `/home/changye/aopt-experiments-20260829/analyses/browsable/aopt-all-experiments-curves-20260826/aopt_fc_pfc_git_pr_token_comparison.png`
- `/home/changye/aopt-experiments-20260829/analyses/browsable/aopt-all-experiments-curves-20260826/aopt_git_pr_lite_factorial_token_comparison.png`

## 14. 参考文献与阅读路线

以下优先列原论文、正式 proceedings 或作者稿。

### 14.1 第一优先级：直接构成主框架

1. Zilberstein, S. and Russell, S. J. (1996). **Optimal Composition of Real-Time Systems**. Artificial Intelligence. [DOI](https://doi.org/10.1016/0004-3702(94)00074-3)  
   作用：composite anytime algorithms；联合调度计算时间与解质量。

2. Finkelstein, L., Markovitch, S. and Rivlin, E. (2002). **Optimal Schedules for Parallelizing Anytime Algorithms: The Case of Independent Processes**. AAAI. [PDF](https://cdn.aaai.org/AAAI/2002/AAAI02-108.pdf)  
   作用：区分 wall time 与各 process active time，研究暂停/恢复与并行调度。

3. Gomes, C. P. and Selman, B. (2001). **Algorithm Portfolios**. Artificial Intelligence. [Article](https://www.sciencedirect.com/science/article/pii/S0004370200000813)  
   作用：解释并行随机搜索为何改善 runtime tail，却未必节省总计算。

4. Li, L. et al. (2018). **Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization**. JMLR. [Article](https://www.jmlr.org/beta/papers/v18/16-558.html)  
   作用：在未知收敛速度下，使用 successive halving 自适应早停和资源晋级。

5. Badanidiyuru, A., Kleinberg, R. and Slivkins, A. (2013/2018). **Bandits with Knapsacks**. [Author preprint](https://arxiv.org/abs/1305.2545)  
   作用：把时间、token、eval 次数和并发槽作为多个有限预算。

6. Russell, S. and Wefald, E. (1991). **Principles of Metareasoning**. Artificial Intelligence. [Article](https://www.sciencedirect.com/science/article/pii/000437029190015C)  
   作用：Value of Computation；判断继续、eval、review 或 merge 是否值得。

7. Xu, W. and Klabjan, D. (2023). **Pareto Regret Analyses in Multi-objective Multi-armed Bandit**. ICML. [PMLR](https://proceedings.mlr.press/v202/xu23i.html)  
   作用：不依赖固定 scalarization 的多目标 Pareto regret。

8. Zhang, R. and Golovin, D. (2020). **Random Hypervolume Scalarizations for Provable Multi-Objective Black Box Optimization**. ICML. [PMLR](https://proceedings.mlr.press/v119/zhang20i.html)  
   作用：黑盒 Pareto frontier 与 hypervolume regret；适合离线 workflow 比较。

### 14.2 第二优先级：随机并行梯度与共享状态

9. Zinkevich, M. et al. (2010). **Parallelized Stochastic Gradient Descent**. NeurIPS. [Paper](https://proceedings.neurips.cc/paper/2010/hash/abea47ba24142ed16b7d8fbf2c740e0d-Abstract.html)  
   作用：独立随机 worker 与聚合、并行加速的早期理论。

10. Recht, B. et al. (2011). **HOGWILD!: A Lock-Free Approach to Parallelizing Stochastic Gradient Descent**. NeurIPS. [Paper](https://proceedings.neurips.cc/paper_files/paper/2011/hash/218a0aefd1d1a4be65601cc6ddc1520e-Abstract.html)  
    作用：更新稀疏性、无锁并行与冲突概率；可转化为 patch conflict graph 假设。

11. Richtárik, P. and Takáč, M. (2016). **Parallel Coordinate Descent Methods for Big Data Optimization**. Mathematical Programming. [Article](https://link.springer.com/article/10.1007/s10107-015-0901-6)  
    作用：并行加速取决于 partial separability；可解释为什么固定并行度不是跨任务最优。

12. Qu, Z. and Richtárik, P. (2016). **Coordinate Descent with Arbitrary Sampling I: Algorithms and Complexity**. Optimization Methods and Software. [Author PDF](https://richtarik.org/papers/alpha1.pdf)  
    作用：任意、非均匀 block sampling；对应按成功率/冲突度选择 lane。

13. Stich, S. U. (2019). **Local SGD Converges Fast and Communicates Little**. ICLR. [OpenReview PDF](https://openreview.net/pdf?id=S1g2JnRcFX)  
    作用：本地多步与低频通信，提供同步间隔的理论原型。

14. Woodworth, B. et al. (2020). **Is Local SGD Better than Minibatch SGD?** ICML. [PMLR](https://proceedings.mlr.press/v119/woodworth20a.html)  
    作用：重要反例；Local SGD 并不在所有一般凸问题上优于 minibatch SGD。

15. McMahan, B. et al. (2017). **Communication-Efficient Learning of Deep Networks from Decentralized Data**. AISTATS. [PMLR](https://proceedings.mlr.press/v54/mcmahan17a.html)  
    作用：FedAvg 与 non-IID/local update；对应异构模型和 hypothesis 的 periodic merge。

16. Lian, X. et al. (2018). **Asynchronous Decentralized Parallel Stochastic Gradient Descent**. ICML. [PMLR](https://proceedings.mlr.press/v80/lian18a.html)  
    作用：异构环境、异步与去中心化通信；也帮助区分 mixing matrix 意义的 doubly stochastic。

17. Lian, X. et al. (2015). **Asynchronous Parallel Stochastic Gradient for Nonconvex Optimization**. NeurIPS. [PDF](https://proceedings.neurips.cc/paper/2015/file/452bf208bf901322968557227b8f6efe-Paper.pdf)  
    作用：bounded delay/staleness 下的异步非凸优化。

18. Dutta, S. et al. (2018). **Slow and Stale Gradients Can Win the Race: Error-Runtime Trade-offs in Distributed SGD**. AISTATS. [PMLR](https://proceedings.mlr.press/v84/dutta18a.html)  
    作用：直接分析 error–wall-clock runtime、straggler 与 stale update 的折衷。

19. Bäckström, K. et al. (2022). **ASAP.SGD: Instance-based Adaptiveness to Staleness in Asynchronous SGD**. ICML. [PMLR](https://proceedings.mlr.press/v162/backstrom22a.html)  
    作用：不存在通用固定 staleness 规则，应根据运行实例自适应处理旧更新。

20. Yu, H. and Jin, R. (2019). **On the Computation and Communication Complexity of Parallel SGD with Dynamic Batch Sizes for Stochastic Non-Convex Optimization**. ICML. [PMLR](https://proceedings.mlr.press/v97/yu19c.html)  
    作用：分开分析 computation complexity 与 communication complexity；对应 token 与同步/merge 成本。

### 14.3 第三优先级：双随机、嵌套随机与两时间尺度

21. Dai, B. et al. (2014). **Scalable Kernel Methods via Doubly Stochastic Gradients**. NeurIPS. [Paper](https://papers.nips.cc/paper_files/paper/2014/hash/c6cc81e8589ebb6accf27b78afad82d9-Abstract.html)  
    作用：训练样本与随机特征的两次无偏随机近似。对 workflow 是双采样启发，不是直接收敛理论。

22. Wang, M., Fang, E. X. and Liu, H. (2017). **Stochastic Compositional Gradient Descent: Algorithms for Minimizing Compositions of Expected-Value Functions**. Mathematical Programming. [Preprint](https://arxiv.org/abs/1411.3803)  
    作用：嵌套期望、quasi-gradient 和 two-timescale tracking；适合跨 task 学习 workflow policy。

23. Wang, M., Liu, J. and Fang, E. X. (2017). **Accelerating Stochastic Composition Optimization**. JMLR. [Article](https://jmlr.csail.mit.edu/beta/papers/v18/16-504.html)  
    作用：stochastic composition 的加速与 noisy inner oracle。

### 14.4 并行黑盒优化与 LLM test-time compute

24. Kandasamy, K. et al. (2018). **Parallelised Bayesian Optimisation via Thompson Sampling**. AISTATS. [PMLR](https://proceedings.mlr.press/v84/kandasamy18a.html)  
    作用：解释并行可能主要改善时间 regret，而不是总 sample efficiency。

25. Paria, B., Kandasamy, K. and Póczos, B. (2020). **A Flexible Framework for Multi-Objective Bayesian Optimization using Random Scalarizations**. UAI. [PMLR](https://proceedings.mlr.press/v115/paria20a.html)  
    作用：只探索决策者关心的 Pareto 区域，而非无差别恢复整个 front。

26. De Sabbata, C. N., Sumers, T. R. and Griffiths, T. L. (2024). **Rational Metareasoning for Large Language Models**. [Preprint](https://arxiv.org/abs/2410.05563)  
    作用：把 VOC 用于选择性推理和 token 控制；LLM 相关但尚非长期 coding workflow。

27. Zhu, K. et al. (2025). **Scaling Test-time Compute for LLM Agents**. [Preprint](https://arxiv.org/abs/2506.12928)  
    作用：并行 sampling、顺序 revision、verifier/merge 与 rollout diversity 的系统消融。

28. Wunderlich, F. V. et al. (2026). **Multi-Agent Reasoning Improves Compute Efficiency: Pareto-Optimal Test-Time Scaling**. [Preprint](https://arxiv.org/abs/2605.01566)  
    作用：直接画多 agent accuracy–compute Pareto front；目前主要是短时推理，不含共享 Git 状态。

### 14.5 传统 workflow scheduling：术语相同但问题不同

29. Durillo, J. J. et al. (2014). **Multi-objective List Scheduling of Workflow Applications in Distributed Computing Infrastructures**. Journal of Parallel and Distributed Computing. [Article](https://www.sciencedirect.com/science/article/pii/S0743731513002384)  
    作用：makespan、成本、能耗和可靠性的传统 Pareto workflow scheduling。其任务 DAG 与运行成本通常事先给定，而 agent workflow 的搜索图和收益是运行中产生的，因此只能借评价方法，不能直接套调度算法。

## 15. 推荐阅读次序

若目标是尽快形成下一版设计，建议按以下次序：

1. Finkelstein et al.：建立 wall time 与 active compute 的区分；
2. Gomes & Selman：理解 portfolio 与长尾；
3. HOGWILD! + Parallel Coordinate Descent：建立冲突稀疏度假设；
4. Local SGD + Dutta et al.：建立同步、straggler 和 staleness 权衡；
5. Hyperband + Bandits with Knapsacks + VOC：构造在线 controller；
6. Pareto regret/hypervolume：固定离线评价；
7. Doubly stochastic/SCGD：设计双阶段评估与跨 task 两时间尺度学习。

## 16. 当前研究判断

最值得优先验证的 SGD 启发不是“把每条 lane 当作梯度求平均”，而是：

1. **冲突感知并行**：根据实时 patch conflict graph 选择可同时运行的 lane；
2. **自适应同步**：根据 staleness × overlap 决定提醒、rebase、复核或继续本地探索；
3. **双阶段随机评估**：便宜筛选与昂贵 full eval 分层；
4. **两时间尺度学习**：run 内快速调度，跨 task 缓慢更新 workflow policy；
5. **层级 Pareto 决策**：先保护 performance–time front，再优化 token。

这条路线既吸收了并行 SGD 的成熟结论，也承认代码搜索不可微、merge 非线性和 lane 强相关的现实，因而比直接移植某个收敛定理更稳健。
