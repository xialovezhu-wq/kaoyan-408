# OS_UNK_110

来源定位：HOS_0128 / 5.3.6（24） / 段落5747

正式节点：OS_UNK_110
来源 ID：HOS_0128
科目：操作系统
主知识点：OS05-32 固态硬盘 SSD
核心考点：按闪存介质、无机械寻道、随机读写相对表现和有限擦写寿命分别判断固态硬盘特性。
主模块：OS05 输入输出 I/O 管理
题型：SSD 特性否定式辨析题
解析来源：本地 2026 操作系统参考书同题独立解析；current solution image set 与 BATCH-022 root asset receipt SHA-256 绑定一致
解析来源角色：独立同题解析只支持答案安全机制与知识角色复核，不据解析新增、选择或改写用户事实。
匹配方式：BATCH-022 current question and solution direct visual exact；formal identity、year、locator、dates、redo 与 user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/OS_UNK_110/question-01.png]]

> [!answer]- 解析
>
> # 错题回顾：固态硬盘 SSD 特点
> 
> ## 题目
> 下列关于固态硬盘 (SSD) 的说法中，错误的是（ ）  
> A. 基于闪存的存储技术  
> B. 随机读/写性能明显高于磁盘  
> C. 随机写比较慢  
> D. 不易磨损  
> 
> 正确答案：D  
> 我的选择：C ❌
> 
> ---
> 
> ## 知识点对比
> - A. 基于闪存 ✅ → 正确，SSD 属于 Flash Memory 存储器  
> - B. 随机读写性能高 ✅ → 正确，SSD 无机械结构，随机访问速度远快于磁盘  
> - C. 随机写比较慢 ✅ → 正确，但“比较慢”是相对于随机读，整体仍优于 HDD  
> - D. 不易磨损 ❌ → 错误，SSD 的缺点是擦写寿命有限，需要“磨损均衡技术”
> 
> ---
> 
> ## 错误原因
> - 将“随机写慢”误解为错误描述，实际上它是 SSD 的真实特点。  
> - 忽视了 SSD 寿命有限、容易磨损的关键缺点。
> 
> ---
> 
> ## 延伸总结
> - SSD 优点：速度快、功耗低、抗震动、静音  
> - SSD 缺点：擦写寿命有限，容易磨损，需要磨损均衡  
> - 考点提醒：考试常考“SSD 为什么需要磨损均衡技术”

## 图片 QA 记录

- 2026-07-03 handwriting overlay R 批：直接查看当前题图并清理左侧红叉痕迹；题面主体仍完整可读，原图已备份到 `assets/_quarantine/handwriting_overlay_original_2026-07-03_batch_r/OS_UNK_110/question-01.png`。Tutor 安全范围：`OS05-31`、`OS05-32`。
