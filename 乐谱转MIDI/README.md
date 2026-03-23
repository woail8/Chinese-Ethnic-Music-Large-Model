# 乐谱文本 → MIDI

将你模型输出的“简谱符号序列”按规则解析为标准 MIDI（.mid）。

## 支持的符号规则

单个音符（或休止）表示顺序：

`[#/b] + 数字 + (˙/⸳)+ (_) + (·)`

- 数字：`1-7` 表示唱名（do-re-mi-fa-sol-la-si）；`0` 表示休止
- 升降号：`#` 升半音，`b` 降半音（放在数字前）
- 八度：上方点 `˙` 表示高一八度（可重复），下方点 `⸳` 表示低一八度（可重复）
- 下划线：`_` 数量表示减时线数量（见下方时值）
- 附点：右侧 `·`，时值 × 1.5
- 增时线：单独的 `-`，表示把上一音（或上一休止）延长 1 拍
- 小节线：`|`，只用于分隔，不影响时值计算

## 需要用户输入的参数（暂不自动识别）

- `--do`：把 `1(do)` 映射到哪个绝对音高（默认 `C4`）
- `--ts`：拍号（默认 `4/4`）
- `--bpm`：速度（默认 `120`）

## 时值（当前实现）

- 无下划线：默认 **2 拍**（可用 `--no-underscore-beats` 修改）
- 1 条 `_`：**1 拍**
- 2 条 `__`：**0.25 拍**
- 3 条 `___`：**0.125 拍**
- 更多下划线：在 `___` 基础上继续每加一条再除以 2

如果你后续确认“下划线时值”的精确定义不同，我可以把这部分做成可配置映射表。

## 用法

方式 1：直接传文本

```powershell
python .\score_to_midi.py --text "1_ 2_ 3_ | 5 - - 6_ 5_" --do C4 --ts 4/4 --bpm 96 --out out.mid
```

如果系统提示找不到 `python`（你的 Python 在 `E:\python\anaconda\python.exe`），可以这样运行：

```powershell
& "E:\python\anaconda\python.exe" .\score_to_midi.py --text "1_ 2_ 3_ | 5 - - 6_ 5_" --do C4 --ts 4/4 --bpm 96 --out out.mid
```

也可以用封装脚本（自动找 python 或用 -Python 指定路径）：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_score_to_midi.ps1 -Python "E:\python\anaconda\python.exe" -- --in .\example_score.txt --do C4 --ts 4/4 --bpm 96 --out .\example_score.mid
```

方式 2：从 txt 读

```powershell
python .\score_to_midi.py --in score.txt --do D4 --ts 3/4 --bpm 120
```

方式 3：运行示例文件（仓库已提供）

```powershell
python .\score_to_midi.py --in .\example_score.txt --do C4 --ts 4/4 --bpm 96 --out .\example_score.mid
```

不传 `--out` 时：

- 若使用 `--in`，默认输出到同名 `.mid`
- 否则默认输出 `out.mid`
