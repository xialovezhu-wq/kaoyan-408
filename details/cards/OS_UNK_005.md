# OS_UNK_005

来源定位：HOS_0005 / 1.6.2 （06） / 段落3707

正式节点：OS_UNK_005
来源 ID：HOS_0005
科目：操作系统
主知识点：OS01-12 微内核
核心考点：微内核只保留与硬件和基本执行紧密相关的最小机制，更高层服务由用户态服务器承担。
主模块：OS01 操作系统概述
题型：单项选择题 / 微内核功能归属辨析题
解析来源：2026操作系统_带书签.pdf 第 46 页同题解析；current solution-01.png 与 BATCH-017 恢复回执 SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-017 current question/solution direct visual exact / Wangdao question page 44 and solution page 46 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：J1ClbQeTSLu
- OO3 路径：408 > 操作系统 > 3.操作系统 > 第一章 计算机系统概述 > 操作系统体系结构 > 1.6.2 （06）

> [!question] 题目
> ![[assets/OS_UNK_005/question-01.png]]

> [!answer]- 解析
>
> ## 知识点总结
> 1. **微内核设计原则**  
>    - 只保留最基本、必须和底层硬件直接交互的功能在内核：  
>      - 进程通信  
>      - 低级I/O  
>      - 低级进程管理与调度  
>      - 中断与陷入处理  
>    - 其余功能（如文件系统、设备驱动、网络协议）放在用户态，通过消息机制与内核交互。
> 
> 2. **错误原因**  
>    - 文件系统服务是高层功能，不属于内核最基本的部分，放入微内核会破坏“小而精”的设计思想。  
>    - 看到“低级”就要联想到“靠近硬件”，说明它们必须在微内核中。  
> 
> ---
> 
> ## 记忆口诀
> **微内核留“底层”，高层上用户。**  
> - 留：进程通信、低级 I/O、低级调度、中断陷入  
> - 上：文件系统、驱动、协议

## 图片 QA 记录

- 2026-07-03 QA（pass question only AB）：题图清晰可复做；浅水印不遮挡关键题面；无独立解析图；折叠解析与微内核结构功能归属主题一致；追加详情卡 QA 说明；不改正式关系网；证据图 `indexes/qa-contact-sheets/pass-question-only-batch-ab-ds-os-after-clean-2026-07-03.png`。检测线只保留概念范围：OS01-08；不向 Tutor 输入题图、完整解析、答案、选项字母或手写痕迹。
