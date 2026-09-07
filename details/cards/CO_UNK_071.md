# CO_UNK_071

来源定位：VISUAL_PENDING / 未提取

正式节点：CO_UNK_071
来源 ID：VISUAL_PENDING
科目：计算机组成原理
主知识点：CO02-20 IEEE 754
核心考点：IEEE 754 单精度编码的符号、阶码与尾数字段解码及偏置边界。
主模块：CO02 数据表示与运算
题型：概念计算题 / IEEE 754 字段解码
解析来源：408 统考真题语料 EXAM408-2013-Q13；current solution-01.png 与 BATCH-016 恢复回执 SHA-256 一致
解析来源角色：canonical exact 解析用于复核 current 题图机制；不授权迁移正式年份、来源或改写用户错误事实。
匹配方式：BATCH-016 current question/canonical solution visual exact / current SHA-256 bound / formal year, source and user facts preserved

## MarginNote 来源

- OO3 item：HPqS7JBXSFC
- OO3 路径：408 > 计算机组成原理 > 2.计算机组成原理 > 第二章 数据的表示和运算 > 2.3 浮点数的表示和运算 > IEEE 754浮点数表示范围 & 几种特殊状态 > 特殊状态的浮点数（阶码全0 或 全1） > IEEE 754：规格化浮点数的表示范围（float 单精度） > 2013年真题选择题 > ## 🧩 二、IEEE 754 单精度格式复习

> [!question] 题目
> ![[assets/CO_UNK_071/question-01.png]]


> [!answer]- 解析
>
> ✅ 最终答案：A
> 
> ---
> 
> ## ⚡️ 五、快速做题口诀
> 
> > “拆三段：符号看头，阶码减127，尾数加1”
> 
> 1️⃣ 符号位：第 1 位决定正负  
> 2️⃣ 阶码：中间 8 位减去 127  
> 3️⃣ 尾数：前面隐藏 “1.” 再转十进制
> 
> ---
> 
> ## 📘 六、知识点总结（简短）
> 
> | 知识点 | 内容 |
> |:--|:--|
> | IEEE754 单精度结构 | 1（符号） + 8（阶码） + 23（尾数） |
> | 阶码偏置值 | 127 |
> | 尾数隐藏位 | 规格化数自动加 1 |
> | 真值公式 | \((-1)^S \times (1 + 小数部分) \times 2^{E - 127})\) |
> 
> ---
> 
> ✅ 一句话总结：
> 
> > C6400000H → 符号负、阶码 140 → 指数 13、尾数 1.5  
> > 结果 = −1.5 × 2¹³

## 图片质检记录

- 2026-07-03 original scan pending U 批：直接查看当前题图，题图为 MarginNote 文字重排卡而非原始扫描图；第一屏可阶段性复做，未见答案标注或解析提前暴露；折叠解析与 IEEE 754 阶码偏置值和真值计算主题一致。继续补原题扫描图、来源 ID 和正式定位。Tutor 安全范围：CO02-20。
- 2026-07-04 open gap B 批追加复核：再次直接打开 `assets/CO_UNK_071/question-01.png`，当前仍为 MarginNote 文字重排题图，题面清晰可阶段性复做；未发现新的错绑、作答痕迹或解析提前暴露。仍缺原题扫描题图、独立 `solution-01.png`、来源 ID 和正式定位。证据拼图：`indexes/qa-contact-sheets/open-gap-batch-b-direct-review-2026-07-04.png`。Tutor 安全范围：`CO02-20 IEEE 754`。
