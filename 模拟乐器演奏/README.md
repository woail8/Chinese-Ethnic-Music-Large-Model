# 模拟乐器演奏（钢琴采样）

把 MIDI 文件按事件时间轴解析出来，然后用 `音频文件/钢琴88键独立音频文件` 目录下的 88 个钢琴采样（A0～C8）进行混音合成，输出一个可播放的 WAV。

## 目录约定

- 采样目录（已存在）：`D:\民族文化大模型\模拟乐器演奏\音频文件\钢琴88键独立音频文件`
- 主程序：`play_midi_with_piano_samples.py`

## 音色目录结构（多乐器）

后续如果你要新增/切换音色，建议把不同乐器采样放到同一个“音频文件”根目录下的不同子文件夹中。程序会自动识别并在 GUI 的“音色”下拉框里显示有哪些乐器可选。

要求：每个乐器文件夹内必须包含 88 个采样，文件名使用固定音名（A0～C8，含升号 #）。

示例：

- `D:\民族文化大模型\模拟乐器演奏\音频文件\钢琴\A0.wav ... C8.wav`
- `D:\民族文化大模型\模拟乐器演奏\音频文件\古筝\A0.wav ... C8.wav`
- `D:\民族文化大模型\模拟乐器演奏\音频文件\笛子\A0.wav ... C8.wav`

兼容：如果你仍然用原来的 `钢琴88键独立音频文件` 结构，也能被识别。

## 最简单用法

在 `D:\民族文化大模型\模拟乐器演奏` 目录打开 PowerShell，然后执行：

```powershell
python .\play_midi_with_piano_samples.py "D:\path\to\your.mid"
```

默认会在 MIDI 同目录输出同名 `.wav`。

如果你的 Python 不在 PATH（例如你的 Python 在 `E:\python\anaconda\python.exe`），可以这样运行：

```powershell
& "E:\python\anaconda\python.exe" .\play_midi_with_piano_samples.py "D:\path\to\your.mid"
```

也可以用封装脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_piano_midi.ps1 -Midi "D:\path\to\your.mid" -Play
```

也支持显式指定 Python 路径：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_piano_midi.ps1 -Midi "D:\path\to\your.mid" -Play -Python "E:\python\anaconda\python.exe"
```

## 常用参数

```powershell
python .\play_midi_with_piano_samples.py `
  --midi "D:\path\to\your.mid" `
  --samples "D:\民族文化大模型\模拟乐器演奏\音频文件" `
  --out "D:\path\to\out.wav" `
  --sr 44100 `
  --normalize 28000 `
  --play
```

- `--samples`：可指向 `音频文件`，也可直接指向 `钢琴88键独立音频文件`
- `--play`：合成完成后直接播放（Windows）

## 说明

- 只使用标准库实现（不依赖额外第三方包）
- 支持 MIDI Tempo 变速（Meta: Set Tempo）
- MIDI 超出 A0～C8 的音符会被忽略

如果系统提示找不到 `python`，先安装 Python 3.10+ 并把它加入 PATH。

## 可视化界面

运行后可以看到每个音符演奏了什么音、持续了多久（卷帘图 + 列表），并可在界面内一键合成/播放：

```powershell
python .\midi_piano_gui.py
```

如果你的 Python 不在 PATH（例如 `E:\python\anaconda\python.exe`）：

```powershell
& "E:\python\anaconda\python.exe" .\midi_piano_gui.py
```

也可以用封装脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_piano_gui.ps1 -Python "E:\python\anaconda\python.exe"
```

界面内提供常用播放控制：

- 进度条时间轴：拖动可跳转到任意时间点
- 播放/继续、暂停、停止
- 回退 5 秒、快进 5 秒、重播
- 跟随播放指针：自动滚动卷帘图到当前时间
- 速度：在部分系统上可用（取决于 Windows 的 MCI 对 waveaudio 的支持）

## 网页版（推荐）

项目自带 Web 服务时，打开：

- `http://127.0.0.1:8000/simulator`

网页端支持与桌面版同等的核心功能：音色下拉（来自“音频文件”子文件夹）、卷帘图+列表联动、时间轴拖动、暂停/继续、回退/快进、重播、倍速与跟随播放滚动。

网页版额外支持：直接粘贴“乐谱文本序列”（模型输出）并输入调号/拍号/速度，一键转 MIDI 后继续合成与播放，无需再跑命令行。

网页版额外支持：上传“简谱图片”进行识别，查看处理后图片与识别序列，并可在网页内打开手动纠错编辑器（多图层框选/移动/缩放/新增/删除/保存生成序列）。
