/* Price-alert button for the catalyst dashboard.
   Creating an alert opens a pre-filled GitHub issue ("ALERT NVDA >= 150").
   The price_alerts workflow checks open alerts every 15 minutes in US market hours,
   comments on the issue when the price is reached (GitHub emails that comment to you)
   and closes it. Only issues opened by the repo owner count. */
(function () {
  const REPO = "sagivitzhak/Stock-Scanner";
  const D = window.DATA || { events: [], extra: [], live: {} };
  const names = {};
  (D.events || []).forEach(e => names[e.tk] = names[e.tk] || e.nm);
  Object.entries(D.live || {}).forEach(([k, v]) => names[k] = names[k] || v.name);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const css = `
  .pa-fab{position:fixed;inset-inline-end:16px;bottom:16px;z-index:50;font:700 15px Heebo,sans-serif;min-height:48px;padding:0 18px;border:0;border-radius:24px;background:var(--claret,#990f3d);color:#fff;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25)}
  .pa-fab:focus-visible{outline:3px solid var(--ink,#000);outline-offset:2px}
  .pa-back{position:fixed;inset:0;z-index:60;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;padding:16px}
  .pa-back.on{display:flex}
  .pa-box{background:var(--card,#fff);color:var(--ink,#111);border-radius:6px;max-width:440px;width:100%;padding:18px 20px;max-height:90vh;overflow:auto;font:15px/1.5 Heebo,sans-serif}
  .pa-box h2{margin:0 0 4px;font-size:20px}
  .pa-box p{margin:0 0 12px;color:var(--mute,#666);font-size:13px}
  .pa-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
  .pa-box label{display:grid;gap:4px;font-size:13px;font-weight:700}
  .pa-box input,.pa-box select{font:16px Heebo,sans-serif;min-height:44px;padding:0 10px;border:1px solid var(--ink,#111);border-radius:3px;background:var(--bg,#fff);color:var(--ink,#111);width:100%}
  .pa-now{font-size:13px;color:var(--mute,#666);min-height:20px;margin-bottom:10px;direction:rtl}
  .pa-act{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
  .pa-act button{font:700 15px Heebo,sans-serif;min-height:44px;padding:0 16px;border-radius:3px;cursor:pointer;border:1px solid var(--ink,#111);background:transparent;color:var(--ink,#111)}
  .pa-act .go{background:var(--claret,#990f3d);border-color:var(--claret,#990f3d);color:#fff}
  .pa-list{margin-top:16px;border-top:1px solid var(--line,#ddd);padding-top:10px}
  .pa-list h3{font-size:14px;margin:0 0 6px}
  .pa-list ul{list-style:none;margin:0;padding:0;font-size:14px}
  .pa-list li{display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-top:1px solid var(--line,#ddd)}
  .pa-list li:first-child{border-top:0}
  .pa-list a{color:var(--claret,#990f3d);font-weight:700;font-size:13px}
  .pa-err{color:var(--claret,#990f3d);font-size:13px;min-height:18px}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  const fab = document.createElement("button");
  fab.className = "pa-fab"; fab.type = "button"; fab.textContent = "🔔 התראת מחיר";
  document.body.appendChild(fab);

  const back = document.createElement("div");
  back.className = "pa-back";
  back.innerHTML = `<div class="pa-box" role="dialog" aria-modal="true" aria-labelledby="pa-h">
    <h2 id="pa-h">התראת מחיר במייל</h2>
    <p>כשהמחיר יגיע ליעד תקבל מייל מ-GitHub. המחיר נבדק כל 15 דקות בשעות המסחר בוול סטריט.</p>
    <div class="pa-row">
      <label>מניה<input id="pa-tk" list="pa-tks" autocomplete="off" dir="ltr" placeholder="NVDA"></label>
      <label>תנאי<select id="pa-op"><option value=">=">עולה מעל</option><option value="<=">יורדת מתחת</option></select></label>
    </div>
    <label>מחיר יעד ($)<input id="pa-px" type="number" step="0.01" min="0" inputmode="decimal" dir="ltr"></label>
    <div class="pa-now" id="pa-now"></div>
    <label>הערה (לא חובה)<input id="pa-note" maxlength="120" placeholder="למשל: לבדוק כניסה אחרי הדוח"></label>
    <div class="pa-err" id="pa-err" role="alert"></div>
    <div class="pa-act"><button class="go" id="pa-go" type="button">יצירת התראה</button><button id="pa-x" type="button">סגירה</button></div>
    <p style="margin-top:10px">הלחיצה פותחת ב-GitHub טופס מוכן. לוחצים שם "Create" או "Submit new issue", וההתראה פעילה.</p>
    <div class="pa-list"><h3>התראות פעילות</h3><ul id="pa-ul"><li>טוען…</li></ul></div>
    <datalist id="pa-tks">${Object.keys(names).sort().map(t => `<option value="${esc(t)}">${esc(names[t])}</option>`).join("")}</datalist>
  </div>`;
  document.body.appendChild(back);
  const $ = s => back.querySelector(s);

  async function lastPrice(tk) {
    const L = (D.live || {})[tk];
    if (L && L.price) return { px: L.price, at: L.at };
    try {
      const r = await fetch("ohlc/" + encodeURIComponent(tk) + ".json", { cache: "no-cache" });
      if (r.ok) { const j = await r.json(); const b = j.d[j.d.length - 1]; return { px: b[4], at: b[0] }; }
    } catch (e) {}
    return null;
  }
  let timer;
  $("#pa-tk").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const tk = $("#pa-tk").value.trim().toUpperCase();
      if (!tk) { $("#pa-now").textContent = ""; return; }
      const p = await lastPrice(tk);
      $("#pa-now").textContent = p ? `מחיר אחרון ידוע: $${Number(p.px).toFixed(2)}` : "";
      if (p && !$("#pa-px").value) $("#pa-px").placeholder = Number(p.px).toFixed(2);
    }, 250);
  });

  async function loadList() {
    const ul = $("#pa-ul");
    try {
      const r = await fetch(`https://api.github.com/repos/${REPO}/issues?state=open&per_page=100`, { cache: "no-store" });
      const items = (await r.json()).filter(i => !i.pull_request && /^ALERT\s/i.test(i.title) && i.author_association === "OWNER");
      ul.innerHTML = items.length ? items.map(i => {
        const m = i.title.match(/^ALERT\s+([A-Z0-9.\-]+)\s*(>=|<=)\s*([\d.]+)/i);
        const txt = m ? `${m[1].toUpperCase()} ${m[2] === ">=" ? "מעל" : "מתחת ל-"} $${m[3]}` : esc(i.title);
        return `<li><span dir="rtl">${esc(txt)}</span><a href="${i.html_url}" target="_blank" rel="noopener">ביטול (Close issue)</a></li>`;
      }).join("") : "<li>אין התראות פעילות.</li>";
    } catch (e) { ul.innerHTML = "<li>לא הצלחתי לטעון את הרשימה.</li>"; }
  }

  function open(tk) {
    back.classList.add("on");
    if (tk) { $("#pa-tk").value = tk; $("#pa-tk").dispatchEvent(new Event("input")); }
    $("#pa-err").textContent = "";
    loadList();
    setTimeout(() => $(tk ? "#pa-px" : "#pa-tk").focus(), 30);
  }
  function close() { back.classList.remove("on"); fab.focus(); }
  fab.addEventListener("click", () => {
    const sel = document.querySelector("#tk");                 // chart page: current ticker
    open(sel && sel.value ? sel.value : "");
  });
  $("#pa-x").addEventListener("click", close);
  back.addEventListener("click", e => { if (e.target === back) close(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && back.classList.contains("on")) close(); });

  $("#pa-go").addEventListener("click", async () => {
    const tk = $("#pa-tk").value.trim().toUpperCase().replace(/[^A-Z0-9.\-]/g, "");
    const op = $("#pa-op").value;
    const px = parseFloat($("#pa-px").value);
    if (!tk) { $("#pa-err").textContent = "צריך לבחור מניה."; return; }
    if (!(px > 0)) { $("#pa-err").textContent = "צריך מחיר יעד גדול מ-0."; return; }
    const cur = await lastPrice(tk);
    const title = `ALERT ${tk} ${op} ${px}`;
    const body = [
      `התראת מחיר: **${tk}** ${op === ">=" ? "עולה מעל" : "יורדת מתחת ל-"} **$${px}**`,
      cur ? `מחיר בזמן היצירה: $${Number(cur.px).toFixed(2)}` : "",
      $("#pa-note").value.trim() ? `הערה: ${$("#pa-note").value.trim()}` : "",
      "",
      "_נוצר מהדשבורד. אל תשנה את הכותרת. לסגירת ההתראה: Close issue._"
    ].filter(Boolean).join("\n");
    window.open(`https://github.com/${REPO}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`, "_blank", "noopener");
    $("#pa-err").textContent = "נפתח טופס ב-GitHub. אחרי שתאשר שם, ההתראה תופיע ברשימה.";
    setTimeout(loadList, 8000);
  });

  window.openPriceAlert = open;
})();
