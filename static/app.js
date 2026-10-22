const chatEl = document.getElementById("chat");
const pendingBar = document.getElementById("pendingBar");
const pendingText = document.getElementById("pendingText");
const appPill = document.getElementById("appPill");
let currentApp = null;
let currentActionId = null;

function addMsg(text, who) {
  const d = document.createElement("div");
  d.className = `msg ${who}`;
  d.textContent = text;
  chatEl.appendChild(d);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function sevClass(msg) {
  return /error|exception|500|fail|panic/i.test(msg) ? "err" : "normal";
}

function renderCard(card) {
  const box = document.createElement("div");
  box.className = "card";
  const h = document.createElement("h4");
  h.textContent = card.title || card.type;
  box.appendChild(h);

  if (card.type === "apps") {
    card.data.forEach(a => {
      const r = document.createElement("div");
      r.className = "row";
      const tagClass = a.status === "RUNNING" ? "ok" : "warn";
      r.innerHTML = `<span>${a.project_id} <small style="color:var(--ink-3)">(${a.type})</small></span><span class="tag ${tagClass}">${a.status}</span>`;
      r.style.cursor = "pointer";
      r.onclick = () => { currentApp = a.project_id; appPill.textContent = currentApp; };
      box.appendChild(r);
    });
  } else if (card.type === "status") {
    const d = card.data;
    [["وضعیت", d.status], ["پلن", d.planID], ["نوع", d.type], ["Scale", d.scale]].forEach(([k, v]) => {
      const r = document.createElement("div"); r.className = "row";
      r.innerHTML = `<span>${k}</span><span>${v ?? "-"}</span>`;
      box.appendChild(r);
    });
  } else if (card.type === "logs" || card.type === "result") {
    const wrap = document.createElement("div");
    wrap.className = "logbox";
    const items = card.type === "logs" ? card.data : [card.data];
    (items || []).forEach(l => {
      const line = document.createElement("div");
      const msg = typeof l === "string" ? l : (l.message ?? JSON.stringify(l));
      line.className = `log-line ${sevClass(msg)}`;
      const dt = l && l.datetime ? `[${l.datetime}] ` : "";
      line.textContent = dt + msg;
      wrap.appendChild(line);
    });
    box.appendChild(wrap);
  } else if (card.type === "causes") {
    if (!card.data || !card.data.length) {
      box.appendChild(document.createTextNode("علت مشخصی پیدا نشد."));
    } else {
      card.data.forEach((c, i) => {
        const item = document.createElement("div");
        item.className = "cause-item";
        item.innerHTML = `<div><span class="cause-score">${i + 1}.</span> ${c.cause} — اطمینان ${c.score}٪</div>` +
          (c.evidence && c.evidence.length ? `<div class="log-line normal">${c.evidence[0]}</div>` : "");
        box.appendChild(item);
      });
    }
  } else if (card.type === "metrics") {
    const mk = (label, arr) => {
      let vals = [];
      (arr || []).forEach(it => (it.value || []).forEach(v => { const n = parseFloat(v); if (!isNaN(n)) vals.push(n); }));
      const max = vals.length ? Math.max(...vals) : 0;
      const wrap = document.createElement("div");
      wrap.innerHTML = `<div class="row"><span>${label}</span><span>${max.toFixed(0)}%</span></div>
        <div class="metric-bar ${max > 85 ? "high" : ""}"><div style="width:${Math.min(max,100)}%"></div></div>`;
      return wrap;
    };
    box.appendChild(mk("CPU", card.data.cpuUsage));
    box.appendChild(mk("RAM", card.data.memoryUsage));
  }
  chatEl.appendChild(box);
  chatEl.scrollTop = chatEl.scrollHeight;
}

async function send(message) {
  addMsg(message, "user");
  const resp = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, app: currentApp })
  });
  const data = await resp.json();
  addMsg(data.reply, "bot");
  (data.cards || []).forEach(renderCard);
  if (data.pending_action) {
    currentActionId = data.pending_action.id;
    pendingText.textContent = `⚠ نیاز به تأیید: ${data.pending_action.description}`;
    pendingBar.hidden = false;
  }
}

document.getElementById("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("msgInput");
  const v = input.value.trim();
  if (!v) return;
  input.value = "";
  send(v);
});

document.querySelectorAll(".quick button").forEach(b => {
  b.addEventListener("click", () => send(b.dataset.q));
});

document.getElementById("btnApprove").addEventListener("click", async () => {
  pendingBar.hidden = true;
  const resp = await fetch("/api/confirm", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_id: currentActionId, approve: true })
  });
  const data = await resp.json();
  addMsg(data.reply, "bot");
  (data.cards || []).forEach(renderCard);
});

document.getElementById("btnReject").addEventListener("click", async () => {
  pendingBar.hidden = true;
  const resp = await fetch("/api/confirm", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_id: currentActionId, approve: false })
  });
  const data = await resp.json();
  addMsg(data.reply, "bot");
});

addMsg("سلام! من همکار فنی لیاراتم. می‌تونم وضعیت اپ‌هات رو چک کنم، لاگ و متریک بگیرم، در صورت ارور یا کندی علت رو پیدا کنم، و با تأیید خودت ری‌استارت یا تغییر env بدم. بپرس: «لیست اپ‌هام چیه»", "bot");
