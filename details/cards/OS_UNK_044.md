# OS_UNK_044

来源定位：HOS_0059 / 2.1.8 （24） / 段落3726

正式节点：OS_UNK_044
来源 ID：HOS_0059
科目：操作系统
主知识点：OS02-03 PCB
核心考点：PCB 控制管理信息与进程地址空间数据的边界
匹配方式：BATCH-006 逐图同题核验；现有 HOS_0059 来源身份保持不变

## MarginNote 链接

- marginnote4app://note/802B6F1D-81B2-4CC5-8EA8-767AC14CD678

> [!question] 题目
> ![[assets/OS_UNK_044/question-01.png]]


> [!answer]- 解析
>
> ![[assets/OS_UNK_044/solution-01.png]]
>
> **题目**  
> PCB是进程存在的唯一标志，下列（ ）不属于PCB。  
> A. 进程ID  
> B. CPU状态  
> C. 堆栈指针  
> D. 全局变量  
> 
> **我的错误选择**  
> ✔ 错选：C  
> ✔ 正确：D  
> 
> **解析**  
> - PCB（进程控制块）内容：  
>   - 进程ID（PID）、用户标识符  
>   - CPU状态（寄存器值、程序状态字等）  
>   - 堆栈指针（保存函数调用、局部变量信息）  
>   - 进程调度和管理信息（优先级、状态、时间片等）  
>   - 资源分配清单（代码段指针、数据段指针、堆栈段指针等）  
> - 全局变量属于进程运行时的数据段，不属于PCB。  
> 
> **结论**  
> PCB只保存进程的**控制和管理信息**，而非进程的数据本身（如全局变量）。  
> → 正确答案是 **D 全局变量**。

## 图片质检记录

- 2026-07-03 rendered question pending 复核：已直接打开 `assets/OS_UNK_044/question-01.png`。当前题图为 MarginNote 文字卡渲染，不是原始扫描题图；第一屏清晰可复做，未见答案标记或解析提前暴露；折叠解析文字与 PCB 组成和进程基本概念主题一致。继续补原题扫描图和独立 `solution-01.png`。Tutor 安全范围：OS02-01 进程基本概念 / OS02-03 PCB。
- 2026-07-03 本地书源排除：检索本地《计算机操作系统 第4版 学习指导与题解》命中 PCB 同主题题页，但题面选项结构与当前 `OS_UNK_044` 不一致，不能作为原扫描题图或独立解析图回填。证据图：`indexes/qa-contact-sheets/os-unk-044-local-book-related-not-exact-2026-07-03.png`。继续补原题扫描图和独立 `solution-01.png`。
- 2026-07-03 DOCX/PDF 精确复核：MarginNote Word 导出精确命中 `2.1.8（24）` 和 note link；附近候选图为进程映像、并发特性等相邻题，不是本道原扫描题图或独立解析图。本地操作系统学习指导书命中 PCB 概念页和同主题习题页，但题面结构不一致，仅作为排除证据。继续补原题扫描图和独立 `solution-01.png`。
- 2026-07-03 本地王道 OS 书源恢复：已直接查看王道 OS 选择题本 PDF 第 16 页，确认第 24 题为本节点同题原扫描题图；已直接查看 `2026王道《操作系统》.pdf` PDF 第 70 页（印刷页 59），确认第 24 题独立解析。已替换 `assets/OS_UNK_044/question-01.png`，旧 MarginNote 文字卡题图隔离到 `assets/_quarantine/original_scan_replacement_2026-07-03/OS_UNK_044/`，并新增 `assets/OS_UNK_044/solution-01.png`。证据图：`indexes/qa-contact-sheets/os-unk-044-original-scan-and-solution-recovery-2026-07-03.png`。Tutor 安全范围：OS02-01 进程基本概念 / OS02-03 PCB。
