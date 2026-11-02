(function () {
  "use strict";
  var EXAMPLES = [
    "How many orders were delivered in March 2023?",
    "Which 5 products sold the most units?",
    "What is the revenue for each product category?",
    "Which country has the most customers?",
    "What percentage of orders were returned?",
    "Delete all orders from 2023"
  ];
  var log = document.getElementById("log"), form = document.getElementById("form"), input = document.getElementById("q");
  var send = document.getElementById("send"), reset = document.getElementById("reset");
  var sessionId = newSession();

  function newSession() {
    var a = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function showStart() {
    log.textContent = "";
    log.appendChild(el("div", "hint", "Try one of these:"));
    var chips = el("div", "chips");
    EXAMPLES.forEach(function (q) {
      var b = el("button", "chip", q);
      b.type = "button";
      b.addEventListener("click", function () { ask(q); });
      chips.appendChild(b);
    });
    log.appendChild(chips);
  }

  function table(columns, rows) {
    var wrap = el("div", "tablewrap"), t = el("table"), head = el("tr");
    columns.forEach(function (c) { head.appendChild(el("th", null, c)); });
    t.appendChild(head);
    rows.forEach(function (r) {
      var tr = el("tr");
      r.forEach(function (v) { tr.appendChild(el("td", null, v === null ? "NULL" : String(v))); });
      t.appendChild(tr);
    });
    wrap.appendChild(t);
    return wrap;
  }

  function answerCard(d) {
    var card = el("div", "a" + (d.refused ? " refused" : ""));
    if (d.refused) card.appendChild(el("span", "badge", d.category || "refused"));
    card.appendChild(el("p", null, d.answer));
    if (!d.refused && d.sql) {
      card.appendChild(el("div", "label", "SQL"));
      card.appendChild(el("pre", null, d.sql));
      card.appendChild(el("div", "label", d.truncated ? "Rows (first " + d.rows.length + ")" : "Rows (" + d.rows.length + ")"));
      card.appendChild(table(d.columns, d.rows));
    }
    if (d.steps) {
      var bits = [d.steps + (d.steps === 1 ? " step" : " steps"), d.queries_run + (d.queries_run === 1 ? " query" : " queries")];
      if (d.queries_blocked) bits.push(d.queries_blocked + " blocked by the guardrails");
      card.appendChild(el("div", "meta", bits.join(" · ")));
    }
    return card;
  }

  function problem(text) {
    var card = el("div", "a error");
    card.appendChild(el("p", null, text));
    return card;
  }

  function ask(question) {
    question = question.trim();
    if (!question) return;
    if (log.querySelector(".chips")) log.textContent = "";
    log.appendChild(el("div", "q", question));
    var wait = el("div", "a", "Thinking...");
    log.appendChild(wait);
    log.scrollTop = log.scrollHeight;
    send.disabled = true;
    input.value = "";
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 90000);
    fetch("/ask", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
      body: JSON.stringify({ question: question, session_id: sessionId })
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
    }).then(function (res) {
      var card;
      if (res.ok) card = answerCard(res.body);
      else if (res.status === 429) card = problem(res.body.detail || "Too many questions. Please wait a moment.");
      else card = problem("That question could not be sent. Please shorten or rephrase it.");
      wait.replaceWith(card);
    }).catch(function () {
      wait.replaceWith(problem("The service did not answer in time. Please try again."));
    }).then(function () {
      clearTimeout(timer);
      send.disabled = false;
      log.scrollTop = log.scrollHeight;
      input.focus();
    });
  }

  form.addEventListener("submit", function (e) { e.preventDefault(); ask(input.value); });
  reset.addEventListener("click", function () { sessionId = newSession(); showStart(); input.focus(); });

  fetch("/schema").then(function (r) { return r.json(); }).then(function (d) {
    var list = document.getElementById("tables");
    d.tables.forEach(function (line) {
      var open = line.indexOf("("), li = el("li");
      li.appendChild(el("b", null, line.slice(0, open)));
      li.appendChild(document.createTextNode(line.slice(open + 1, -1)));
      list.appendChild(li);
    });
  }).catch(function () {});

  showStart();
})();
