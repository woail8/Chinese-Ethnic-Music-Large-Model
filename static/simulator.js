const $ = (id) => document.getElementById(id)

function fmtTimeSec(sec) {
  const ms = Math.max(0, Math.floor(sec * 1000))
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const ss = s % 60
  const msec = ms % 1000
  return `${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}.${String(msec).padStart(3, "0")}`
}

async function fileToBase64(file) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error("读取文件失败"))
    reader.onload = () => {
      const res = String(reader.result || "")
      const idx = res.indexOf(",")
      if (idx >= 0) return resolve(res.slice(idx + 1))
      reject(new Error("读取文件失败"))
    }
    reader.readAsDataURL(file)
  })
}

function binarySearchRightmostLE(arr, x) {
  let lo = 0
  let hi = arr.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid] <= x) {
      ans = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return ans
}

function ensureVisibleX(scroller, x, margin) {
  const viewW = scroller.clientWidth
  const left = scroller.scrollLeft
  const right = left + viewW
  let newLeft = null
  if (x < left + margin) newLeft = x - margin
  else if (x > right - margin) newLeft = x - (viewW - margin)
  if (newLeft === null) return
  if (newLeft < 0) newLeft = 0
  const maxLeft = Math.max(0, scroller.scrollWidth - viewW)
  if (newLeft > maxLeft) newLeft = maxLeft
  scroller.scrollLeft = newLeft
}

function ensureVisibleY(scroller, y0, y1, margin) {
  const viewH = scroller.clientHeight
  const top = scroller.scrollTop
  const bottom = top + viewH
  let newTop = null
  if (y0 < top + margin) newTop = y0 - margin
  else if (y1 > bottom - margin) newTop = y1 - (viewH - margin)
  if (newTop === null) return
  if (newTop < 0) newTop = 0
  const maxTop = Math.max(0, scroller.scrollHeight - viewH)
  if (newTop > maxTop) newTop = maxTop
  scroller.scrollTop = newTop
}

function velColor(vel) {
  const v = Math.max(0, Math.min(127, Number(vel) || 0))
  const t = v / 127
  const r = Math.round(60 + 160 * t)
  const g = Math.round(120 + 80 * t)
  const b = Math.round(200 - 120 * t)
  return `rgb(${r},${g},${b})`
}

const state = {
  midiName: "",
  midiBase64: "",
  events: [],
  starts: [],
  totalSeconds: 0,
  activeIdx: -1,
  instrumentItems: [],
  pxPerSec: 140,
  rowH: 8,
  left: 70,
  top: 22,
  minNote: 21,
  maxNote: 108,
  rects: [],
  drawingW: 1000,
  drawingH: 600,
  rafId: 0,
  follow: true,
  isSeeking: false,
  lastFollowIdx: -1,
  sourceMode: "midi",
  midiBytes: null,
}

function setStatus(text) {
  $("status").textContent = text
}

function setHover(text) {
  $("hoverHint").textContent = text || ""
}

function syncTimeText(cur, total) {
  $("timeText").textContent = `${fmtTimeSec(cur)} / ${fmtTimeSec(total)}`
}

function clearActiveRow() {
  const prev = $("eventTbody").querySelector("tr.is-active")
  if (prev) prev.classList.remove("is-active")
}

function setActiveIdx(idx, scrollList) {
  if (idx === state.activeIdx) return
  clearActiveRow()
  state.activeIdx = idx
  if (idx < 0) return
  const row = $(`row-${idx}`)
  if (row) {
    row.classList.add("is-active")
    if (scrollList) row.scrollIntoView({ block: "nearest" })
  }
}

function currentEventIndex(t) {
  if (!state.starts.length) return -1
  let idx = binarySearchRightmostLE(state.starts, t)
  if (idx < 0) idx = 0
  if (idx >= state.events.length) idx = state.events.length - 1
  const e = state.events[idx]
  if (e && e.end_s < t && idx + 1 < state.events.length) idx += 1
  return idx
}

function setupInstrumentSelect(items) {
  const sel = $("instrument")
  sel.innerHTML = ""
  const valid = items.filter((x) => x.valid)
  items.forEach((it) => {
    const opt = document.createElement("option")
    opt.value = it.name
    if (!it.valid) {
      opt.disabled = true
      opt.textContent = `${it.name}（缺${it.missing}）`
    } else {
      opt.textContent = it.name
    }
    sel.appendChild(opt)
  })
  if (valid.length) sel.value = valid[0].name
}

async function refreshInstruments() {
  const res = await fetch("/api/sim/instruments")
  if (!res.ok) throw new Error("获取音色失败")
  const data = await res.json()
  state.instrumentItems = Array.isArray(data.items) ? data.items : []
  setupInstrumentSelect(state.instrumentItems)
}

function prepareCanvases(totalSeconds) {
  const wrap = $("rollWrap")
  const w = Math.max(1000, Math.ceil(totalSeconds * state.pxPerSec) + state.left + 60)
  const h = Math.max(600, (state.maxNote - state.minNote + 1) * state.rowH + state.top + 30)
  state.drawingW = w
  state.drawingH = h

  const dpr = window.devicePixelRatio || 1
  const base = $("rollCanvas")
  const head = $("headCanvas")
  base.style.width = `${w}px`
  base.style.height = `${h}px`
  head.style.width = `${w}px`
  head.style.height = `${h}px`
  base.width = Math.floor(w * dpr)
  base.height = Math.floor(h * dpr)
  head.width = Math.floor(w * dpr)
  head.height = Math.floor(h * dpr)

  const bctx = base.getContext("2d")
  const hctx = head.getContext("2d")
  bctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  hctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  wrap.scrollLeft = 0
  wrap.scrollTop = 0
  return { bctx, hctx }
}

function drawRoll() {
  const { bctx } = prepareCanvases(state.totalSeconds || 0)
  const w = state.drawingW
  const h = state.drawingH

  bctx.clearRect(0, 0, w, h)
  bctx.fillStyle = "#0c1226"
  bctx.fillRect(0, 0, w, h)

  const secMarks = Math.ceil(state.totalSeconds || 0)
  for (let s = 0; s <= secMarks + 1; s++) {
    const x = state.left + Math.floor(s * state.pxPerSec)
    bctx.strokeStyle = "rgba(255,255,255,0.06)"
    bctx.beginPath()
    bctx.moveTo(x, state.top)
    bctx.lineTo(x, h - 10)
    bctx.stroke()
    bctx.fillStyle = "rgba(255,255,255,0.45)"
    bctx.font = "12px ui-sans-serif, system-ui"
    bctx.fillText(String(s), x + 2, 14)
  }

  for (let n = state.minNote; n <= state.maxNote; n++) {
    const y = state.top + (state.maxNote - n) * state.rowH
    const pc = n % 12
    const isBlack = pc === 1 || pc === 3 || pc === 6 || pc === 8 || pc === 10
    if (isBlack) {
      bctx.fillStyle = "rgba(255,255,255,0.03)"
      bctx.fillRect(0, y, w, state.rowH)
    }
    if (pc === 0) {
      bctx.fillStyle = "rgba(255,255,255,0.45)"
      bctx.font = "12px ui-sans-serif, system-ui"
      const name = state.events.length ? "" : ""
      const octave = Math.floor(n / 12) - 1
      bctx.fillText(`C${octave}`, 6, y + 11)
    }
  }

  state.rects = new Array(state.events.length)
  for (let i = 0; i < state.events.length; i++) {
    const e = state.events[i]
    const x0 = state.left + Math.floor(e.start_s * state.pxPerSec)
    const x1 = state.left + Math.max(1, Math.floor(e.end_s * state.pxPerSec))
    const y0 = state.top + (state.maxNote - e.note) * state.rowH
    const y1 = y0 + state.rowH - 1
    bctx.fillStyle = velColor(e.velocity)
    bctx.fillRect(x0, y0, x1 - x0, y1 - y0)
    state.rects[i] = { x0, x1, y0, y1 }
  }

  if (!state.events.length) {
    bctx.fillStyle = "rgba(255,255,255,0.35)"
    bctx.font = "14px ui-sans-serif, system-ui"
    bctx.fillText("未检测到可用音符事件（可能全部超出 A0–C8 范围）", state.left + 10, state.top + 22)
  }
}

function drawHead(playSec) {
  const head = $("headCanvas")
  const dpr = window.devicePixelRatio || 1
  const ctx = head.getContext("2d")
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, state.drawingW, state.drawingH)

  const x = state.left + Math.floor(playSec * state.pxPerSec)
  ctx.strokeStyle = "#dd3333"
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(x, state.top)
  ctx.lineTo(x, state.drawingH - 10)
  ctx.stroke()

  if (state.activeIdx >= 0 && state.activeIdx < state.rects.length) {
    const r = state.rects[state.activeIdx]
    if (r) {
      ctx.strokeStyle = "rgba(0,0,0,0.9)"
      ctx.lineWidth = 2
      ctx.strokeRect(r.x0 - 1, r.y0 - 1, r.x1 - r.x0 + 2, r.y1 - r.y0 + 2)
    }
  }
}

function updateFollow(playSec) {
  if (!state.follow) return
  const wrap = $("rollWrap")
  const x = state.left + Math.floor(playSec * state.pxPerSec)
  ensureVisibleX(wrap, x, 160)
  const idx = currentEventIndex(playSec)
  if (idx >= 0 && idx !== state.lastFollowIdx) {
    state.lastFollowIdx = idx
    const r = state.rects[idx]
    if (r) ensureVisibleY(wrap, r.y0, r.y1, 80)
    setActiveIdx(idx, true)
  }
}

async function analyzeMidi() {
  const file = $("midiFile").files && $("midiFile").files[0]
  if (!file) {
    setStatus("请先选择 MIDI 文件")
    return
  }
  setStatus("正在读取 MIDI…")
  const midiBase64 = await fileToBase64(file)
  state.midiBase64 = midiBase64
  state.midiBytes = null
  state.midiName = file.name
  setStatus("正在解析 MIDI…")
  const res = await fetch("/api/sim/midi/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ midi_base64: midiBase64, filename: file.name }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || "解析失败")
  }
  state.events = Array.isArray(data.events) ? data.events : []
  state.starts = state.events.map((e) => e.start_s)
  state.totalSeconds = Number(data.total_seconds) || 0
  renderTable()
  drawRoll()
  const t = $("audio").currentTime || 0
  drawHead(t)
  setStatus(
    `已加载：${file.name} | 可用音符数：${data.in_range ?? state.events.length} | 超出范围：${data.out_range ?? 0} | 时长：${state.totalSeconds.toFixed(
      2
    )}s`
  )
}

function renderTable() {
  const tbody = $("eventTbody")
  tbody.innerHTML = ""
  for (let i = 0; i < state.events.length; i++) {
    const e = state.events[i]
    const tr = document.createElement("tr")
    tr.id = `row-${i}`
    tr.dataset.idx = String(i)
    tr.innerHTML = `
      <td>${e.start_s.toFixed(3)}</td>
      <td>${(e.end_s - e.start_s).toFixed(3)}</td>
      <td>${e.note_name}</td>
      <td>${e.note}</td>
      <td>${e.velocity}</td>
      <td>${e.channel}</td>
    `
    tr.addEventListener("click", () => {
      const audio = $("audio")
      const t = e.start_s
      audio.currentTime = Math.max(0, t)
      setActiveIdx(i, false)
      drawHead(audio.currentTime)
      updateFollow(audio.currentTime)
    })
    tbody.appendChild(tr)
  }
}

function b64ToBytes(b64) {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}

function downloadBytes(bytes, filename, mime) {
  const blob = new Blob([bytes], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

async function scoreToMidiAndLoad() {
  const text = String($("scoreText").value || "").trim()
  if (!text) {
    setStatus("请先粘贴乐谱文本")
    return
  }
  setStatus("正在转 MIDI…")
  const body = {
    score_text: text,
    do: String($("doNote").value || "C4").trim(),
    key: String($("keySig").value || "C major").trim(),
    ts: String($("timeSig").value || "4/4").trim(),
    bpm: Number($("bpm").value) || 120,
    no_underscore_beats: Number($("noUnderscore").value) || 2,
    tpq: 480,
    vel: 96,
  }
  const res = await fetch("/api/sim/score/to_midi", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || "转 MIDI 失败")

  state.midiBase64 = data.midi_base64 || ""
  state.midiBytes = state.midiBase64 ? b64ToBytes(state.midiBase64) : null
  state.midiName = "score.mid"
  $("btnDownloadMidi").disabled = !state.midiBytes

  state.events = Array.isArray(data.events) ? data.events : []
  state.starts = state.events.map((e) => e.start_s)
  state.totalSeconds = Number(data.total_seconds) || 0
  renderTable()
  drawRoll()
  const t = $("audio").currentTime || 0
  drawHead(t)
  setStatus(
    `已加载：乐谱文本 | 可用音符数：${data.in_range ?? state.events.length} | 超出范围：${data.out_range ?? 0} | 时长：${state.totalSeconds.toFixed(
      2
    )}s`
  )
}

async function renderMidi(autoplay) {
  const inst = $("instrument").value
  if (!inst) {
    setStatus("没有可用音色：请在音频文件目录下放置乐器子文件夹")
    return
  }
  if (!state.midiBase64) {
    if (state.sourceMode === "score") {
      await scoreToMidiAndLoad()
    } else {
      await analyzeMidi()
    }
    if (!state.midiBase64) return
  }
  setStatus("正在合成 WAV…")
  const res = await fetch("/api/sim/midi/render", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ midi_base64: state.midiBase64, instrument: inst, sr: 44100, normalize: 28000 }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || "合成失败")
  }
  const audio = $("audio")
  audio.src = data.wav_url
  audio.load()
  await new Promise((resolve) => {
    const onMeta = () => {
      audio.removeEventListener("loadedmetadata", onMeta)
      resolve()
    }
    audio.addEventListener("loadedmetadata", onMeta)
  })
  $("timeline").max = String(Math.floor(audio.duration * 1000) || 0)
  syncTimeText(audio.currentTime, audio.duration || 0)
  setStatus(`已合成：${data.wav_url}`)
  if (autoplay) {
    await audio.play()
  }
}

const ocr = {
  sid: "",
  image: null,
  imageUrl: "",
  processedUrl: "",
  items: [],
  lines: [],
  selected: -1,
  mode: "idle",
  drag: null,
  scale: 1,
  active: "digit",
  visible: { digit: true, vline: true, hline: true, dot: true, accidental: true, red: true },
}

function ocrColorFor(t) {
  if (t === "digit") return "#ffcc00"
  if (t === "hline") return "#ff9900"
  if (t === "vline") return "#00cccc"
  if (t === "dot") return "#ff33cc"
  if (t === "accidental") return "#800080"
  return "#00aa00"
}

function ocrSetMsg(text, ok) {
  const el = $("ocrMsg")
  el.textContent = text || ""
  el.style.color = ok ? "rgba(140,255,140,0.85)" : "rgba(255,140,140,0.85)"
}

function ocrCanvases() {
  const base = $("ocrBaseCanvas")
  const digit = $("ocrDigitCanvas")
  const vline = $("ocrVlineCanvas")
  const hline = $("ocrHlineCanvas")
  const dot = $("ocrDotCanvas")
  const acc = $("ocrAccCanvas")
  const red = $("ocrRedCanvas")
  return {
    base: { el: base, ctx: base.getContext("2d"), type: null },
    digit: { el: digit, ctx: digit.getContext("2d"), type: "digit" },
    vline: { el: vline, ctx: vline.getContext("2d"), type: "vline" },
    hline: { el: hline, ctx: hline.getContext("2d"), type: "hline" },
    dot: { el: dot, ctx: dot.getContext("2d"), type: "dot" },
    accidental: { el: acc, ctx: acc.getContext("2d"), type: "accidental" },
    red: { el: red, ctx: red.getContext("2d"), type: "red" },
  }
}

function ocrSyncLayerPointerEvents() {
  const cs = ocrCanvases()
  for (const k of ["digit", "vline", "hline", "dot", "accidental", "red"]) {
    cs[k].el.style.pointerEvents = ocr.active === k ? "auto" : "none"
  }
}

function ocrResizeAll() {
  if (!ocr.image) return
  const cs = ocrCanvases()
  const iw = ocr.image.width
  const ih = ocr.image.height
  const cw = cs.base.el.clientWidth
  const scale = cw / iw
  ocr.scale = scale
  const pw = Math.max(1, Math.floor(iw * scale))
  const ph = Math.max(1, Math.floor(ih * scale))
  for (const k of Object.keys(cs)) {
    cs[k].el.width = pw
    cs[k].el.height = ph
  }
}

function ocrClear(ctx) {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height)
}

function ocrDrawBase() {
  const cs = ocrCanvases()
  const ctx = cs.base.ctx
  ocrClear(ctx)
  if (!ocr.image) return
  ctx.drawImage(ocr.image, 0, 0, ctx.canvas.width, ctx.canvas.height)
}

function ocrDrawBoxes(layer, items, selectedIndex) {
  const cs = ocrCanvases()
  const ctx = cs[layer].ctx
  ocrClear(ctx)
  if (!ocr.visible[layer]) return
  const scale = ocr.scale || 1
  ctx.lineWidth = 2
  for (const ent of items) {
    const x = ent.it.x * scale
    const y = ent.it.y * scale
    const w = ent.it.w * scale
    const h = ent.it.h * scale
    ctx.strokeStyle = ocrColorFor(ent.it.type)
    ctx.strokeRect(x, y, w, h)
    if (ent.idx === selectedIndex && ocr.active === layer) {
      ctx.strokeStyle = "#00aaff"
      ctx.strokeRect(x - 2, y - 2, w + 4, h + 4)
      ctx.fillStyle = "#00aaff"
      ctx.fillRect(x + w - 6, y + h - 6, 6, 6)
    }
  }
}

function ocrDrawRedLines() {
  const cs = ocrCanvases()
  const ctx = cs.red.ctx
  ocrClear(ctx)
  if (!ocr.visible.red) return
  const scale = ocr.scale || 1
  ctx.strokeStyle = "#ff0000"
  ctx.lineWidth = 2
  const h = ctx.canvas.height
  for (const it of ocr.items) {
    if (it.type !== "digit") continue
    const cx = (it.x + it.w / 2) * scale
    ctx.beginPath()
    ctx.moveTo(cx, 0)
    ctx.lineTo(cx, h)
    ctx.stroke()
  }
}

function ocrRedrawAll(preview) {
  if (!ocr.image) return
  ocrResizeAll()
  ocrDrawBase()
  const grouped = { digit: [], vline: [], hline: [], dot: [], accidental: [] }
  for (let i = 0; i < ocr.items.length; i++) {
    const it = ocr.items[i]
    if (grouped[it.type]) grouped[it.type].push({ idx: i, it })
  }
  ocrDrawBoxes("digit", grouped.digit, ocr.selected)
  ocrDrawBoxes("vline", grouped.vline, ocr.selected)
  ocrDrawBoxes("hline", grouped.hline, ocr.selected)
  ocrDrawBoxes("dot", grouped.dot, ocr.selected)
  ocrDrawBoxes("accidental", grouped.accidental, ocr.selected)
  ocrDrawRedLines()
  if (preview && ocr.active && ocrCanvases()[ocr.active]) {
    const ctx = ocrCanvases()[ocr.active].ctx
    const scale = ocr.scale || 1
    ctx.strokeStyle = "#00aaff"
    ctx.lineWidth = 2
    ctx.strokeRect(preview.x * scale, preview.y * scale, preview.w * scale, preview.h * scale)
  }
}

function ocrPick(mx, my) {
  const scale = ocr.scale || 1
  const x = mx / scale
  const y = my / scale
  for (let i = ocr.items.length - 1; i >= 0; i--) {
    const it = ocr.items[i]
    if (it.type !== ocr.active) continue
    if (x >= it.x && x <= it.x + it.w && y >= it.y && y <= it.y + it.h) return i
  }
  return -1
}

function ocrSetSelected(i) {
  ocr.selected = i
  if (i < 0) {
    $("ocrX").value = ""
    $("ocrY").value = ""
    $("ocrW").value = ""
    $("ocrH").value = ""
    $("ocrText").value = ""
    return
  }
  const it = ocr.items[i]
  $("ocrType").value = it.type
  $("ocrText").value = it.text || ""
  $("ocrX").value = it.x
  $("ocrY").value = it.y
  $("ocrW").value = it.w
  $("ocrH").value = it.h
}

function ocrClampItem(it) {
  it.x = Math.max(0, Math.round(it.x))
  it.y = Math.max(0, Math.round(it.y))
  it.w = Math.max(1, Math.round(it.w))
  it.h = Math.max(1, Math.round(it.h))
}

function ocrCanvasRect() {
  return ocrCanvases()[ocr.active].el.getBoundingClientRect()
}

function ocrHandlePointerDown(e) {
  if (!ocr.image) return
  const cs = ocrCanvases()
  cs[ocr.active].el.setPointerCapture(e.pointerId)
  const rect = ocrCanvasRect()
  const mx = e.clientX - rect.left
  const my = e.clientY - rect.top
  const idx = ocrPick(mx, my)
  const scale = ocr.scale || 1
  const x = mx / scale
  const y = my / scale

  if (ocr.mode === "adding") {
    ocr.drag = { startX: x, startY: y }
    ocr.selected = -1
    return
  }

  ocrSetSelected(idx)
  if (idx < 0) {
    ocrRedrawAll()
    return
  }
  const it = ocr.items[idx]
  const onHandle = e.shiftKey && Math.abs(it.x + it.w - x) <= 8 && Math.abs(it.y + it.h - y) <= 8
  ocr.drag = { idx, startX: x, startY: y, orig: { ...it }, mode: onHandle ? "resize" : "move" }
  ocrRedrawAll()
}

function ocrHandlePointerMove(e) {
  if (!ocr.drag || !ocr.image) return
  const rect = ocrCanvasRect()
  const mx = e.clientX - rect.left
  const my = e.clientY - rect.top
  const scale = ocr.scale || 1
  const x = mx / scale
  const y = my / scale

  if (ocr.mode === "adding") {
    const sx = ocr.drag.startX
    const sy = ocr.drag.startY
    const px = Math.min(sx, x)
    const py = Math.min(sy, y)
    const pw = Math.abs(x - sx)
    const ph = Math.abs(y - sy)
    ocrRedrawAll({ x: px, y: py, w: pw, h: ph })
    return
  }

  const idx = ocr.drag.idx
  if (idx == null || idx < 0 || idx >= ocr.items.length) return
  const orig = ocr.drag.orig
  const dx = x - ocr.drag.startX
  const dy = y - ocr.drag.startY
  if (ocr.drag.mode === "move") {
    ocr.items[idx].x = orig.x + dx
    ocr.items[idx].y = orig.y + dy
  } else {
    ocr.items[idx].w = Math.max(1, orig.w + dx)
    ocr.items[idx].h = Math.max(1, orig.h + dy)
  }
  ocrClampItem(ocr.items[idx])
  ocrSetSelected(idx)
  ocrRedrawAll()
}

function ocrHandlePointerUp(_e) {
  if (!ocr.drag || !ocr.image) return
  if (ocr.mode === "adding") {
    const sx = ocr.drag.startX
    const sy = ocr.drag.startY
    const x = ocr.drag.lastX ?? sx
    const y = ocr.drag.lastY ?? sy
    const px = Math.min(sx, x)
    const py = Math.min(sy, y)
    const pw = Math.abs(x - sx)
    const ph = Math.abs(y - sy)
    ocr.drag = null
    if (pw >= 2 && ph >= 2) {
      const it = {
        type: $("ocrType").value,
        text: String($("ocrText").value || "").trim(),
        x: px,
        y: py,
        w: pw,
        h: ph,
        conf: 100,
      }
      ocrClampItem(it)
      ocr.items.push(it)
      ocrSetSelected(ocr.items.length - 1)
    }
    ocr.mode = "idle"
    ocrRedrawAll()
    ocrSetMsg("已新增框（提示：按住 Shift 拖右下角可缩放选中框）", true)
    return
  }
  ocr.drag = null
}

async function ocrRecognize() {
  const file = $("ocrFile").files && $("ocrFile").files[0]
  if (!file) {
    setStatus("请先选择乐谱图片")
    return
  }
  setStatus("正在读取图片…")
  const dataUrl = await new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onerror = () => reject(new Error("读取图片失败"))
    r.onload = () => resolve(String(r.result || ""))
    r.readAsDataURL(file)
  })
  setStatus("正在识别…")
  const res = await fetch("/api/sim/ocr/recognize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_base64: dataUrl, filename: file.name }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || "识别失败")
  ocr.sid = data.sid
  $("ocrSequence").value = data.sequence || ""
  $("ocrProcessedImg").src = data.processed_url || ""
  $("btnOcrToScore").disabled = !data.sequence
  $("btnOcrOpenEditor").disabled = !data.sid
  setStatus("识别完成")
}

function ocrFillToScore() {
  const seq = String($("ocrSequence").value || "").trim()
  if (!seq) return
  $("scoreText").value = seq
  $("tabScore").click()
  setStatus("已填入乐谱文本，可直接“文本转MIDI并加载”或合成播放")
}

async function ocrOpenEditor() {
  if (!ocr.sid) return
  $("ocrEditor").hidden = false
  $("ocrEditorStatus").textContent = "正在加载标注…"
  const res = await fetch(`/api/sim/ocr/annotation/${ocr.sid}`)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || "加载标注失败")
  ocr.lines = Array.isArray(data.lines) ? data.lines : []
  ocr.items = Array.isArray(data.items) ? data.items : []
  $("ocrEditorSeq").value = data.sequence || ""

  await new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error("加载原图失败"))
    img.src = data.image_url
  }).then((img) => {
    ocr.image = img
  })

  ocr.active = "digit"
  ocr.mode = "idle"
  ocr.selected = -1
  ocr.visible = { digit: true, vline: true, hline: true, dot: true, accidental: true, red: true }
  ocrSyncLayerPointerEvents()
  ocrRedrawAll()
  $("ocrEditorStatus").textContent = `sid=${ocr.sid} | items=${ocr.items.length}`
  ocrSetMsg("", true)
}

async function ocrSave() {
  if (!ocr.sid) return
  ocrSetMsg("正在保存…", true)
  const payload = { sid: ocr.sid, lines: ocr.lines, items: ocr.items }
  const res = await fetch(`/api/sim/ocr/annotation/${ocr.sid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || "保存失败")
  $("ocrEditorSeq").value = data.sequence || ""
  $("ocrSequence").value = data.sequence || ""
  $("btnOcrToScore").disabled = !data.sequence
  ocrSetMsg("保存成功", true)
}

function ocrApply() {
  const i = ocr.selected
  if (i < 0 || i >= ocr.items.length) return
  const it = ocr.items[i]
  it.type = $("ocrType").value
  it.text = String($("ocrText").value || "")
  it.x = Number($("ocrX").value) || 0
  it.y = Number($("ocrY").value) || 0
  it.w = Number($("ocrW").value) || 1
  it.h = Number($("ocrH").value) || 1
  ocrClampItem(it)
  ocrSetSelected(i)
  ocrRedrawAll()
}

function ocrDelete() {
  const i = ocr.selected
  if (i < 0 || i >= ocr.items.length) return
  ocr.items.splice(i, 1)
  ocrSetSelected(-1)
  ocrRedrawAll()
}

function ocrAddMode() {
  ocr.mode = "adding"
  ocr.drag = null
  ocrSetMsg("拖动鼠标框选新增区域", true)
}

function ocrCloseEditor() {
  $("ocrEditor").hidden = true
  ocrSetMsg("", true)
}

function tick() {
  const audio = $("audio")
  const total = Number.isFinite(audio.duration) ? audio.duration : state.totalSeconds || 0
  const cur = audio.currentTime || 0
  if (!state.isSeeking) {
    $("timeline").value = String(Math.floor(cur * 1000))
  }
  syncTimeText(cur, total)
  drawHead(cur)
  updateFollow(cur)
  state.rafId = window.requestAnimationFrame(tick)
}

function bindUI() {
  $("btnAnalyze").addEventListener("click", () => {
    const fn = state.sourceMode === "score" ? scoreToMidiAndLoad : analyzeMidi
    fn().catch((e) => setStatus(String(e.message || e)))
  })
  $("btnRender").addEventListener("click", () => renderMidi(false).catch((e) => setStatus(String(e.message || e))))
  $("btnRenderPlay").addEventListener("click", () => renderMidi(true).catch((e) => setStatus(String(e.message || e))))

  $("btnScoreToMidi").addEventListener("click", () => scoreToMidiAndLoad().catch((e) => setStatus(String(e.message || e))))
  $("btnDownloadMidi").addEventListener("click", () => {
    if (!state.midiBytes) return
    downloadBytes(state.midiBytes, state.midiName || "score.mid", "audio/midi")
  })

  $("tabMidi").addEventListener("click", () => {
    state.sourceMode = "midi"
    $("tabMidi").classList.add("is-active")
    $("tabScore").classList.remove("is-active")
    $("tabOcr").classList.remove("is-active")
    $("panelMidi").hidden = false
    $("panelScore").hidden = true
    $("panelOcr").hidden = true
    $("btnDownloadMidi").disabled = true
    state.midiBase64 = ""
    state.midiBytes = null
    state.midiName = ""
  })
  $("tabScore").addEventListener("click", () => {
    state.sourceMode = "score"
    $("tabScore").classList.add("is-active")
    $("tabMidi").classList.remove("is-active")
    $("tabOcr").classList.remove("is-active")
    $("panelScore").hidden = false
    $("panelMidi").hidden = true
    $("panelOcr").hidden = true
    state.midiBase64 = ""
    state.midiBytes = null
    state.midiName = ""
  })

  $("tabOcr").addEventListener("click", () => {
    state.sourceMode = "ocr"
    $("tabOcr").classList.add("is-active")
    $("tabMidi").classList.remove("is-active")
    $("tabScore").classList.remove("is-active")
    $("panelOcr").hidden = false
    $("panelMidi").hidden = true
    $("panelScore").hidden = true
    $("btnDownloadMidi").disabled = true
    state.midiBase64 = ""
    state.midiBytes = null
    state.midiName = ""
  })

  $("speed").addEventListener("change", () => {
    const audio = $("audio")
    audio.playbackRate = Number($("speed").value) || 1
  })

  $("follow").addEventListener("change", () => {
    state.follow = $("follow").checked
    state.lastFollowIdx = -1
  })

  $("btnPlay").addEventListener("click", async () => {
    const audio = $("audio")
    if (!audio.src) {
      await renderMidi(true).catch((e) => setStatus(String(e.message || e)))
      return
    }
    try {
      await audio.play()
    } catch (e) {
      setStatus(String(e.message || e))
    }
  })

  $("btnPause").addEventListener("click", () => {
    $("audio").pause()
  })

  $("btnStop").addEventListener("click", () => {
    const audio = $("audio")
    audio.pause()
    audio.currentTime = 0
    state.lastFollowIdx = -1
  })

  $("btnReplay").addEventListener("click", async () => {
    const audio = $("audio")
    audio.currentTime = 0
    try {
      await audio.play()
    } catch {
      drawHead(0)
    }
  })

  $("btnBack").addEventListener("click", () => {
    const audio = $("audio")
    audio.currentTime = Math.max(0, audio.currentTime - 5)
  })

  $("btnForward").addEventListener("click", () => {
    const audio = $("audio")
    audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5)
  })

  $("timeline").addEventListener("input", (e) => {
    state.isSeeking = true
    const ms = Number(e.target.value) || 0
    const sec = ms / 1000
    drawHead(sec)
    syncTimeText(sec, Number($("audio").duration) || state.totalSeconds || 0)
  })
  $("timeline").addEventListener("change", (e) => {
    const audio = $("audio")
    const ms = Number(e.target.value) || 0
    audio.currentTime = Math.max(0, ms / 1000)
    state.isSeeking = false
    state.lastFollowIdx = -1
  })

  $("rollWrap").addEventListener("click", (e) => {
    const rect = $("rollCanvas").getBoundingClientRect()
    const x = e.clientX - rect.left + $("rollWrap").scrollLeft
    const sec = Math.max(0, (x - state.left) / state.pxPerSec)
    const audio = $("audio")
    if (audio.src) audio.currentTime = Math.max(0, sec)
    drawHead(sec)
    updateFollow(sec)
  })

  $("headCanvas").addEventListener("mousemove", (e) => {
    const rect = $("rollCanvas").getBoundingClientRect()
    const x = e.clientX - rect.left + $("rollWrap").scrollLeft
    const y = e.clientY - rect.top + $("rollWrap").scrollTop
    const sec = Math.max(0, (x - state.left) / state.pxPerSec)
    const note = state.maxNote - Math.floor((y - state.top) / state.rowH)
    if (note < state.minNote || note > state.maxNote) {
      setHover("")
      return
    }
    if (!state.events.length) {
      setHover("")
      return
    }
    const guess = currentEventIndex(sec)
    let best = null
    const start = Math.max(0, guess - 24)
    const end = Math.min(state.events.length - 1, guess + 24)
    for (let i = start; i <= end; i++) {
      const ev = state.events[i]
      if (ev.note !== note) continue
      if (sec < ev.start_s || sec > ev.end_s) continue
      best = ev
      break
    }
    if (!best) {
      setHover("")
      return
    }
    setHover(`${best.note_name} | start ${best.start_s.toFixed(3)}s | dur ${(best.end_s - best.start_s).toFixed(3)}s`)
  })

  $("btnOcrRecognize").addEventListener("click", () => ocrRecognize().catch((e) => setStatus(String(e.message || e))))
  $("btnOcrToScore").addEventListener("click", () => ocrFillToScore())
  $("btnOcrOpenEditor").addEventListener("click", () => ocrOpenEditor().catch((e) => setStatus(String(e.message || e))))

  $("ocrApply").addEventListener("click", () => ocrApply())
  $("ocrDel").addEventListener("click", () => ocrDelete())
  $("ocrAdd").addEventListener("click", () => ocrAddMode())
  $("ocrSave").addEventListener("click", () => ocrSave().catch((e) => ocrSetMsg(String(e.message || e), false)))
  $("ocrCloseEditor").addEventListener("click", () => ocrCloseEditor())

  for (const t of ["digit", "vline", "hline", "dot", "accidental", "red"]) {
    const id = `ocr_show_${t === "accidental" ? "acc" : t}`
    const el = $(id)
    if (!el) continue
    el.addEventListener("change", () => {
      const key = t
      ocr.visible[key] = el.checked
      ocrRedrawAll()
    })
  }

  document.querySelectorAll('input[name="ocrActiveLayer"]').forEach((el) => {
    el.addEventListener("change", () => {
      ocr.active = el.value
      $("ocrType").value = el.value
      ocrSetSelected(-1)
      ocrSyncLayerPointerEvents()
      ocrRedrawAll()
    })
  })

  const cs = ocrCanvases()
  for (const k of ["digit", "vline", "hline", "dot", "accidental", "red"]) {
    const canvas = cs[k].el
    canvas.addEventListener("pointerdown", ocrHandlePointerDown)
    canvas.addEventListener("pointermove", (e) => {
      if (ocr.drag && ocr.mode === "adding") {
        const rect = ocrCanvasRect()
        const scale = ocr.scale || 1
        ocr.drag.lastX = (e.clientX - rect.left) / scale
        ocr.drag.lastY = (e.clientY - rect.top) / scale
      }
      ocrHandlePointerMove(e)
    })
    canvas.addEventListener("pointerup", ocrHandlePointerUp)
    canvas.addEventListener("pointercancel", ocrHandlePointerUp)
  }
}

async function init() {
  setStatus("正在加载音色…")
  await refreshInstruments()
  setStatus("就绪")
  bindUI()
  const audio = $("audio")
  audio.addEventListener("loadedmetadata", () => {
    $("timeline").max = String(Math.floor(audio.duration * 1000) || 0)
    syncTimeText(0, audio.duration || 0)
  })
  audio.addEventListener("timeupdate", () => {
    if (!state.isSeeking) {
      $("timeline").value = String(Math.floor(audio.currentTime * 1000))
    }
  })
  audio.addEventListener("ended", () => {
    state.lastFollowIdx = -1
  })
  state.rafId = window.requestAnimationFrame(tick)
  window.addEventListener("resize", () => {
    drawRoll()
    drawHead($("audio").currentTime || 0)
  })
}

init().catch((e) => setStatus(String(e.message || e)))
