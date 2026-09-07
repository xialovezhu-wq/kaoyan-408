# CO_UNK_064

来源定位：HCO_0067 / 7.3.4 (27) / 段落3539

正式节点：CO_UNK_064
来源 ID：HCO_0067
科目：计算机组成原理
主知识点：CO06-18 DMA 方式
相关命中：CO05-22 指令流水线
核心考点：DMA 总线或存储周期响应边界与外部中断指令完成边界的比较。
主模块：CO06 总线与输入输出系统
题型：选择题 / DMA 与中断响应边界辨析题
解析来源：2026计算机组成原理_带书签.pdf 第 340 页同题解析；current solution-01.png 与 BATCH-016 恢复回执 SHA-256 一致
解析来源角色：独立同题解析用于复核模型派生安全机制；不据解析追加或改写用户事实。
匹配方式：BATCH-016 current question/solution direct visual exact / root recovery receipt bound / formal identity, dates and user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/CO_UNK_064/question-01.png]]

> [!answer]- 解析
>
> ### 正确答案  
> B. 在该指令的第二级流水段执行完后响应
> 
> ---
> 
> ### 知识点总结  
> 1. 中断方式：中断响应发生在 一条指令周期结束后。  
> 2. DMA 方式：DMA 请求只涉及总线控制权转交，CPU 响应发生在 一个总线周期结束后，而不是等整条指令执行完。  
> 3. 流水线场景：若在第二级流水段产生 DMA 请求，则 CPU 会在该流水段完成后（即一个总线周期结束）进行响应。
> 
> ---
> 
> ### 错因分析  
> - 将 DMA 响应混淆为中断响应。  
> - 实际上 DMA 不需保存/恢复程序状态，只需 CPU 在合适的总线周期空隙让出控制权。  
> 
> ---
> 
> ### 记忆要点  
> - 中断响应：指令周期末。  
> - DMA 响应：总线周期末。

## 图片 QA 记录

- 2026-07-04 直接复核：题图清晰，主题与流水线场景下 DMA 请求响应时机一致；当前无独立解析图。
- 折叠解析：同题文字解析可用，能覆盖 DMA 响应与中断响应的时序边界。
- 证据图：`indexes/qa-contact-sheets/mixed-question-text-batch-n-direct-review-2026-07-04.png`
- Tutor 安全检测范围：CO06-18 DMA 方式；CO05-22 指令流水线；CO06-11 程序中断方式。
- 边界：QA 只登记图文质量和概念范围；正式 408 不写入题图、完整题面、完整解析、答案、选项字母或手写痕迹。
