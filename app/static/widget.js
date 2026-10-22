/* Support KB Assistant: chat bubble for a client's website.
   <script src="https://YOUR-APP/widget.js" data-token="wk_..." async></script>
   Optional: data-lang="es|en", data-title="...", data-color="#2563eb" */
(function () {
  var s = document.currentScript;
  if (!s) return;
  var token = s.getAttribute("data-token");
  if (!token) return;
  var base = new URL(s.src).origin;
  var lang = (s.getAttribute("data-lang") || navigator.language || "en").toLowerCase().indexOf("es") === 0 ? "es" : "en";
  var color = /^#[0-9a-fA-F]{6}$/.test(s.getAttribute("data-color") || "") ? s.getAttribute("data-color") : "#2563eb";
  var T = {
    en: { title: "Questions? Ask here", hello: "Hi! Ask me a question and I will answer from our documentation.", ph: "Type your question", send: "Send", sources: "Sources", wait: "Searching…", err: "Something went wrong. Please try again.", many: "Too many questions. Please wait a moment.", open: "Open chat", close: "Close" },
    es: { title: "¿Dudas? Preguntá acá", hello: "¡Hola! Haceme una pregunta y te respondo con nuestra documentación.", ph: "Escribí tu pregunta", send: "Enviar", sources: "Fuentes", wait: "Buscando…", err: "Algo salió mal. Probá de nuevo.", many: "Demasiadas preguntas. Esperá un momento.", open: "Abrir chat", close: "Cerrar" }
  }[lang];
  var title = s.getAttribute("data-title") || T.title;

  var host = document.createElement("div");
  host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483000;";
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML =
    '<style>' +
    '*{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}' +
    'button{cursor:pointer}' +
    '.fab{width:56px;height:56px;border-radius:50%;border:0;background:' + color + ';color:#fff;font-size:24px;box-shadow:0 4px 14px rgba(0,0,0,.25)}' +
    '.box{display:none;flex-direction:column;width:340px;max-width:calc(100vw - 32px);height:460px;max-height:calc(100vh - 100px);background:#fff;color:#1a1a1a;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.28);overflow:hidden;margin-bottom:12px}' +
    '.box.on{display:flex}' +
    '.hd{background:' + color + ';color:#fff;padding:12px 14px;font-weight:600;display:flex;justify-content:space-between;align-items:center}' +
    '.hd button{background:none;border:0;color:#fff;font-size:20px;line-height:1}' +
    '.msgs{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#f6f7f9}' +
    '.m{padding:8px 10px;border-radius:10px;max-width:88%;font-size:14px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}' +
    '.bot{background:#fff;border:1px solid #e3e5e8;align-self:flex-start}' +
    '.me{background:' + color + ';color:#fff;align-self:flex-end}' +
    '.src{font-size:12px;color:#555;margin-top:6px}' +
    'form{display:flex;gap:6px;padding:8px;border-top:1px solid #e3e5e8;background:#fff}' +
    'input{flex:1;padding:9px 10px;border:1px solid #c9ccd1;border-radius:8px;font-size:14px}' +
    'form button{background:' + color + ';color:#fff;border:0;border-radius:8px;padding:0 14px;font-size:14px}' +
    'form button:disabled{opacity:.6}' +
    '</style>' +
    '<div class="box" role="dialog" aria-label="' + title.replace(/"/g, "") + '">' +
    '<div class="hd"><span class="t"></span><button type="button" class="x" aria-label="' + T.close + '">×</button></div>' +
    '<div class="msgs"></div>' +
    '<form><input maxlength="500" autocomplete="off"><button type="submit"></button></form></div>' +
    '<button class="fab" type="button" aria-label="' + T.open + '">💬</button>';

  var box = root.querySelector(".box"), msgs = root.querySelector(".msgs"),
      form = root.querySelector("form"), input = root.querySelector("input"),
      send = root.querySelector("form button");
  root.querySelector(".t").textContent = title;
  input.placeholder = T.ph;
  send.textContent = T.send;

  function add(text, who, sources) {
    var d = document.createElement("div");
    d.className = "m " + who;
    d.textContent = text;
    if (sources && sources.length) {
      var p = document.createElement("div");
      p.className = "src";
      var names = [];
      sources.forEach(function (x) { if (names.indexOf(x.title) < 0) names.push(x.title); });
      p.textContent = T.sources + ": " + names.join(" · ");
      d.appendChild(p);
    }
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }
  function toggle(open) {
    box.classList.toggle("on", open);
    if (open) { if (!msgs.children.length) add(T.hello, "bot"); input.focus(); }
  }
  root.querySelector(".fab").onclick = function () { toggle(!box.classList.contains("on")); };
  root.querySelector(".x").onclick = function () { toggle(false); };

  form.onsubmit = function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (q.length < 3) return;
    input.value = "";
    add(q, "me");
    var pending = add(T.wait, "bot");
    send.disabled = true;
    fetch(base + "/widget/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token, question: q })
    }).then(function (r) {
      if (r.status === 429) throw new Error("many");
      if (!r.ok) throw new Error("err");
      return r.json();
    }).then(function (j) {
      pending.remove();
      add(j.answer, "bot", j.sources);
    }).catch(function (err) {
      pending.remove();
      add(err.message === "many" ? T.many : T.err, "bot");
    }).then(function () { send.disabled = false; });
  };
  document.body.appendChild(host);
})();
