const chatEl = document.getElementById("chat");
const composerEl = document.getElementById("composer");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");
const refModeEl = document.getElementById("refMode");
const showPromptEl = document.getElementById("showPrompt");
const promptModalEl = document.getElementById("promptModal");
const promptPreEl = document.getElementById("promptPre");
const closePromptEl = document.getElementById("closePrompt");
const copyPromptEl = document.getElementById("copyPrompt");

const state = {
  messages: [],
  sessionId: null,
  busy: false,
  thinkingNode: null,
  tipTimer: null,
  tipIndex: 0,
};

const tips = [
  "正在整理民族音乐相关资料…",
  "正在梳理曲目与流派脉络…",
  "正在组织更清晰的解释方式…",
  "马上就好，请稍等片刻…",
];

function escapeHtml(s) {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeLinkHref(href) {
  const h = (href || "").trim();
  if (/^https?:\/\//i.test(h)) return h;
  return "";
}

function renderInline(text) {
  let t = escapeHtml(text);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const safe = safeLinkHref(href);
    if (!safe) return `<span>${escapeHtml(label)}</span>`;
    return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${escapeHtml(
      label
    )}</a>`;
  });
  return t;
}

function renderMarkdown(md) {
  const src = (md || "").replaceAll("\r\n", "\n");
  const codeBlocks = [];
  let text = src.replace(/```([\w-]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const i = codeBlocks.length;
    codeBlocks.push({
      lang: (lang || "").trim(),
      code: escapeHtml(code.replaceAll("\n", "\n").replace(/\s+$/, "")),
    });
    return `@@CODEBLOCK_${i}@@`;
  });

  const lines = text.split("\n");
  const html = [];
  let i = 0;
  let inUl = false;
  let inOl = false;
  let inQuote = false;

  function closeLists() {
    if (inUl) {
      html.push("</ul>");
      inUl = false;
    }
    if (inOl) {
      html.push("</ol>");
      inOl = false;
    }
  }

  function closeQuote() {
    if (inQuote) {
      html.push("</blockquote>");
      inQuote = false;
    }
  }

  while (i < lines.length) {
    const raw = lines[i];
    const line = raw.trimEnd();

    if (!line.trim()) {
      closeLists();
      closeQuote();
      i += 1;
      continue;
    }

    const mH = /^(#{1,4})\s+(.*)$/.exec(line.trim());
    if (mH) {
      closeLists();
      closeQuote();
      const level = mH[1].length;
      html.push(`<h${level}>${renderInline(mH[2].trim())}</h${level}>`);
      i += 1;
      continue;
    }

    const mQuote = /^>\s?(.*)$/.exec(line.trim());
    if (mQuote) {
      closeLists();
      if (!inQuote) {
        html.push("<blockquote>");
        inQuote = true;
      }
      html.push(`<p>${renderInline(mQuote[1])}</p>`);
      i += 1;
      continue;
    } else {
      closeQuote();
    }

    const mUl = /^-\s+(.*)$/.exec(line.trim());
    if (mUl) {
      if (!inUl) {
        closeLists();
        html.push("<ul>");
        inUl = true;
      }
      html.push(`<li>${renderInline(mUl[1])}</li>`);
      i += 1;
      continue;
    }

    const mOl = /^(\d+)\.\s+(.*)$/.exec(line.trim());
    if (mOl) {
      if (!inOl) {
        closeLists();
        html.push("<ol>");
        inOl = true;
      }
      html.push(`<li>${renderInline(mOl[2])}</li>`);
      i += 1;
      continue;
    }

    closeLists();

    const para = [];
    while (i < lines.length) {
      const l = lines[i].trimEnd();
      if (!l.trim()) break;
      if (/^(#{1,4})\s+/.test(l.trim())) break;
      if (/^>\s?/.test(l.trim())) break;
      if (/^-\s+/.test(l.trim())) break;
      if (/^\d+\.\s+/.test(l.trim())) break;
      para.push(l);
      i += 1;
    }
    const joined = para.map((p) => renderInline(p)).join("<br />");
    html.push(`<p>${joined}</p>`);
  }

  closeLists();
  closeQuote();

  let out = html.join("\n");
  out = out.replace(/@@CODEBLOCK_(\d+)@@/g, (_, idx) => {
    const b = codeBlocks[Number(idx)];
    const cls = b && b.lang ? ` class="language-${escapeHtml(b.lang)}"` : "";
    const label = b && b.lang ? `<div class="code-label">${escapeHtml(b.lang)}</div>` : "";
    return `<div class="code-block">${label}<pre><code${cls}>${b ? b.code : ""}</code></pre></div>`;
  });
  return out;
}

function scrollToBottom() {
  chatEl.scrollTop = chatEl.scrollHeight;
}

function createRow(role) {
  const row = document.createElement("div");
  row.className = `row ${role}`;
  return row;
}

function createBubble(role, content, metaText) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  if (role === "assistant") {
    const md = document.createElement("div");
    md.className = "md";
    md.innerHTML = renderMarkdown(content);
    bubble.appendChild(md);
    if (metaText) {
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = metaText;
      bubble.appendChild(meta);
    }
  } else {
    bubble.textContent = content;
  }
  return bubble;
}

function addMessage(role, content, metaText) {
  const row = createRow(role);
  const bubble = createBubble(role, content, metaText);
  row.appendChild(bubble);
  chatEl.appendChild(row);
  scrollToBottom();
}

function setBusy(busy) {
  state.busy = busy;
  sendEl.disabled = busy;
  inputEl.disabled = busy;
  if (!busy) inputEl.focus();
}

function startThinking() {
  stopThinking();
  const row = createRow("assistant");
  const bubble = document.createElement("div");
  bubble.className = "bubble assistant";

  const thinking = document.createElement("div");
  thinking.className = "thinking";

  const dots = document.createElement("span");
  dots.className = "dots";
  for (let i = 0; i < 3; i++) {
    const d = document.createElement("span");
    d.className = "dot";
    dots.appendChild(d);
  }

  const tip = document.createElement("span");
  tip.className = "tip";
  tip.textContent = tips[0];

  thinking.appendChild(dots);
  thinking.appendChild(tip);
  bubble.appendChild(thinking);
  row.appendChild(bubble);
  chatEl.appendChild(row);
  scrollToBottom();

  state.thinkingNode = { row, tip };
  state.tipIndex = 0;
  state.tipTimer = window.setInterval(() => {
    state.tipIndex = (state.tipIndex + 1) % tips.length;
    state.thinkingNode.tip.textContent = tips[state.tipIndex];
  }, 1200);
}

function stopThinking() {
  if (state.tipTimer) {
    window.clearInterval(state.tipTimer);
    state.tipTimer = null;
  }
  if (state.thinkingNode) {
    state.thinkingNode.row.remove();
    state.thinkingNode = null;
  }
}

async function sendMessage(text) {
  const content = text.trim();
  if (!content) return;

  addMessage("user", content);
  inputEl.value = "";

  setBusy(true);
  startThinking();

  try {
    const refMode = refModeEl ? (refModeEl.value || "rag") : "rag";
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        message: content,
        ref_mode: refMode,
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = data.detail || "请求失败，请稍后再试。";
      throw new Error(detail);
    }
    const answer = (data.answer || "").toString();
    const sid = (data.session_id || "").toString();
    if (sid) state.sessionId = sid;
    const usedMode = (data.ref_mode || refMode || "").toString() || refMode;

    function fmt(u) {
      if (!u) return "";
      const pt = typeof u.prompt_tokens === "number" ? u.prompt_tokens : null;
      const ct = typeof u.completion_tokens === "number" ? u.completion_tokens : null;
      const tt = typeof u.total_tokens === "number" ? u.total_tokens : null;
      const parts = [];
      if (pt !== null) parts.push(`提示 ${pt}`);
      if (ct !== null) parts.push(`回复 ${ct}`);
      if (tt !== null) parts.push(`总计 ${tt}`);
      return parts.join("｜");
    }

    const uMain = fmt(data.usage);
    const uSum = fmt(data.summary_usage);
    let meta = "";
    if (uMain) meta = `本次 tokens：${uMain}`;
    else meta = "本次 tokens：未返回";
    if (uSum) meta = `${meta}（含摘要：${uSum}）`;
    meta = `${meta}｜资料模式：${usedMode === "full" ? "全量" : "RAG"}`;

    stopThinking();
    addMessage("assistant", answer, meta);
  } catch (e) {
    stopThinking();
    const msg = e && e.message ? e.message : "发生未知错误。";
    addMessage("assistant", `抱歉，出现错误：${msg}`);
  } finally {
    setBusy(false);
  }
}

composerEl.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.busy) return;
  sendMessage(inputEl.value);
});

addMessage(
  "assistant",
  "你好，我是民族音乐问答助手。你可以问：某个民族的音乐特征、代表性曲目、乐器与演奏技法、田野采风与传承等。"
);
inputEl.focus();

function openPromptModal(text) {
  if (!promptModalEl || !promptPreEl) return;
  promptPreEl.textContent = text || "";
  promptModalEl.hidden = false;
}

function closePromptModal() {
  if (!promptModalEl) return;
  promptModalEl.hidden = true;
}

async function showPrompt() {
  if (!state.sessionId) {
    openPromptModal("暂无会话提示词：请先发送一条消息。");
    return;
  }
  try {
    const resp = await fetch(`/api/prompt?session_id=${encodeURIComponent(state.sessionId)}`);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || "获取提示词失败。");
    const payload = {
      session_id: data.session_id,
      ref_mode: data.ref_mode,
      usage: data.usage,
      summary_usage: data.summary_usage,
      messages: data.messages,
    };
    openPromptModal(JSON.stringify(payload, null, 2));
  } catch (e) {
    const msg = e && e.message ? e.message : "发生未知错误。";
    openPromptModal(`获取提示词失败：${msg}`);
  }
}

if (showPromptEl) {
  showPromptEl.addEventListener("click", () => showPrompt());
}
if (closePromptEl) {
  closePromptEl.addEventListener("click", () => closePromptModal());
}
if (promptModalEl) {
  promptModalEl.addEventListener("click", (e) => {
    if (e.target === promptModalEl) closePromptModal();
  });
}
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePromptModal();
});
if (copyPromptEl) {
  copyPromptEl.addEventListener("click", async () => {
    try {
      const text = promptPreEl ? promptPreEl.textContent || "" : "";
      if (!text) return;
      await navigator.clipboard.writeText(text);
      copyPromptEl.textContent = "已复制";
      window.setTimeout(() => {
        copyPromptEl.textContent = "复制";
      }, 900);
    } catch (_) {}
  });
}

