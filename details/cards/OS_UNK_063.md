# OS_UNK_063

来源定位：HOS_0081 / 3.1.6 （17）；3.1.3 / 段落4473

正式节点：OS_UNK_063
来源 ID：HOS_0081
科目：操作系统
主知识点：OS03-19 基本地址变换机构
核心考点：先用页面大小把逻辑地址拆成页号与页内偏移，再用页号查页表得到页框号，最后保留偏移重组物理地址。
主模块：OS03 内存管理
题型：基本分页数值地址变换题
解析来源：2026操作系统_带书签.pdf 第 215 页同题解析；current solution-01.png 与 BATCH-019 root recovery SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-019 current question/solution direct visual exact / Wangdao solution page 215 / formal identity, year, locator, dates, redo and user facts preserved

## MarginNote 来源

- OO3 item：UahNl6sJTf6
- OO3 路径：408 > 操作系统 > 3.操作系统 > 第三章 内存管理 > 3.1 内存管理的概念 > 3.1.3 基本分页存储管理 > 基本分页存储管理的基本概念 > 3.1.6 （17）

> [!question] 题目
> ![[assets/OS_UNK_063/question-01.png]]

> [!answer]- 解析
>
> 正确答案：B. 4097
> 
> ---
> 
> ## 我的错误选择
> - 我没有想到要先用 除法/取余 来分解逻辑地址 → 页号和页内偏移。  
> 
> ---
> 
> ## 正确解法
> 1. 已知条件
>    - 页大小 = 4KB = 4096 字节  
>    - 逻辑地址 = 4097  
> 
> 2. 分解逻辑地址
>    - 页号 = 4097 ÷ 4096 = 1  
>    - 页内偏移 = 4097 mod 4096 = 1  
> 
> 3. 查页表
>    - 页号 1 → 对应的块号 = 1  
> 
> 4. 计算物理地址
>    - 物理地址 = 块号 × 页面大小 + 页内偏移  
>    - = 1 × 4096 + 1 = 4097
> 
> ---
> 
> ## 我的错误原因
> - 我没有掌握 逻辑地址拆分公式：  
>   \[
>   \text{逻辑地址} = \text{页号} \times \text{页面大小} + \text{页内偏移}
>   \]  
> - 遇到题目时，没有想到“除以页面大小 → 页号；取余 → 页内偏移”。  
> - 导致完全没有方向，不知道如何下手。  
> 
> ---
> 
> ## 正确记忆
> - 逻辑地址 → 页号 + 页内偏移  
>   - 页号 = 逻辑地址 ÷ 页面大小  
>   - 页内偏移 = 逻辑地址 mod 页面大小  
> - 物理地址 = (块号 × 页面大小) + 页内偏移  
> - 做题口诀：除以取整得页号，取余得到偏移量。

## 图片 QA 记录

- 2026-07-03 annotation overlay S 批：直接查看当前题图并清理蓝紫虚线框和蓝色点状标注；页表主体和题设文字仍完整可读。原图已备份到 `assets/_quarantine/annotation_overlay_original_2026-07-03_batch_s/OS_UNK_063/question-01.png`。Tutor 安全范围：`OS03-17`、`OS03-19`。

## 图片质检记录

- 2026-07-03 annotation overlay S 批：直接查看当前题图并清理蓝紫虚线框和蓝色点状标注；页表主体和题设文字仍完整可读。原图备份：assets/_quarantine/annotation_overlay_original_2026-07-03_batch_s/OS_UNK_063/question-01.png。Tutor 安全范围：OS03-17、OS03-19。
