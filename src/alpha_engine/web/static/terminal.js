/* The AI terminal.

   Talks to POST /api/v1/agent. The user's API key lives in this tab (and in
   localStorage only if they tick the box); it is sent with each request and the
   server drops it as soon as the provider call returns.

   Two rules this file exists to enforce on the UI side:

   1. Never innerHTML anything that came from the model or a tool. Every dynamic
      string goes in via textContent. A model that echoes back
      "<img onerror=...>" must render as characters, not as markup — and on a
      page where the user has pasted an API key, an injected script is the whole
      ballgame.
   2. Always render the tool calls next to the answer. The prose is an index
      into the tool results, not an authority. Hiding the calls would turn a
      checkable answer into a claim.

   No build step, no framework, no dependencies — same as the dashboard. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var KEY_STORE = "ae-llm-key";
  var PROVIDER_STORE = "ae-llm-provider";
  var MODEL_STORE = "ae-llm-model";

  var log = $("log");
  var input = $("input");
  var composer = $("composer");
  var sendBtn = $("send");
  var providerSel = $("provider");
  var modelInput = $("model");
  var keyInput = $("api-key");
  var rememberBox = $("remember");
  var keysLink = $("keys-link");
  var modelHint = $("model-hint");

  var providers = [];
  var history = [];      // provider-native message list, kept client-side
  var busy = false;

  // ---- storage ------------------------------------------------------------
  // localStorage is opt-in and clearly labelled. sessionStorage would survive a
  // reload too but not a new tab, which surprised more people than it helped.

  function store(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  function recall(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } }
  function forget(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }

  // ---- DOM helpers --------------------------------------------------------

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function addRow(role) {
    var row = el("div", "term-row term-" + role);
    log.appendChild(row);
    return row;
  }

  function addUser(text) {
    var row = addRow("user");
    row.appendChild(el("div", "term-role", "you"));
    row.appendChild(el("div", "term-body", text));
    scrollDown();
  }

  function addSystem(text, kind) {
    var row = addRow("system");
    row.appendChild(el("div", "term-body term-sys" + (kind ? " term-" + kind : ""), text));
    scrollDown();
    return row;
  }

  // ---- tool call rendering ------------------------------------------------

  function argSummary(args) {
    var keys = Object.keys(args || {});
    if (!keys.length) return "";
    return keys.map(function (k) {
      var v = args[k];
      if (v && typeof v === "object") v = JSON.stringify(v);
      return k + "=" + v;
    }).join(", ");
  }

  function renderToolCall(call) {
    var failed = call.result && call.result.error;
    var box = el("details", "term-tool" + (failed ? " term-tool-failed" : ""));
    var head = el("summary");
    head.appendChild(el("span", "term-tool-dot"));
    head.appendChild(el("span", "term-tool-name", call.name));
    var args = argSummary(call.arguments);
    if (args) head.appendChild(el("span", "term-tool-args", "(" + args + ")"));
    if (failed) head.appendChild(el("span", "term-tool-badge", "failed"));
    box.appendChild(head);

    // JSON.stringify then textContent: the payload is engine output, but it can
    // contain a headline scraped from the internet. Never innerHTML.
    var pre = el("pre", "term-tool-out");
    pre.appendChild(el("code", null, JSON.stringify(call.result, null, 2)));
    box.appendChild(pre);
    return box;
  }

  /* Minimal, safe markdown: paragraphs, bullet lists, `inline code` and fenced
     blocks. Deliberately not a markdown library — every branch here builds DOM
     nodes with textContent, so there is no path from model output to HTML. */
  function renderProse(text, into) {
    var blocks = String(text || "").split(/\n{2,}/);
    blocks.forEach(function (block) {
      var trimmed = block.trim();
      if (!trimmed) return;

      if (trimmed.indexOf("```") === 0) {
        var body = trimmed.replace(/^```[^\n]*\n?/, "").replace(/```$/, "");
        var pre = el("pre", "code-block");
        pre.appendChild(el("code", null, body));
        into.appendChild(pre);
        return;
      }

      var lines = trimmed.split("\n");
      var isList = lines.every(function (l) { return /^\s*([-*•]|\d+[.)])\s+/.test(l); });
      if (isList) {
        var ul = el("ul", "term-list");
        lines.forEach(function (l) {
          ul.appendChild(inlineInto(el("li"), l.replace(/^\s*([-*•]|\d+[.)])\s+/, "")));
        });
        into.appendChild(ul);
        return;
      }

      into.appendChild(inlineInto(el("p"), trimmed));
    });
  }

  function inlineInto(node, text) {
    // Split on `code` spans; everything else is plain text.
    String(text).split(/(`[^`]+`)/).forEach(function (part) {
      if (!part) return;
      if (part.charAt(0) === "`" && part.charAt(part.length - 1) === "`" && part.length > 2) {
        node.appendChild(el("code", null, part.slice(1, -1)));
      } else {
        node.appendChild(document.createTextNode(part));
      }
    });
    return node;
  }

  function addAnswer(reply) {
    var row = addRow("ai");
    row.appendChild(el("div", "term-role", reply.model || "assistant"));
    var body = el("div", "term-body");

    if (reply.tool_calls && reply.tool_calls.length) {
      var tools = el("div", "term-tools");
      tools.appendChild(el("div", "term-tools-label",
        reply.tool_calls.length + (reply.tool_calls.length === 1 ? " engine call" : " engine calls")));
      reply.tool_calls.forEach(function (c) { tools.appendChild(renderToolCall(c)); });
      body.appendChild(tools);
    }

    if (reply.answer) renderProse(reply.answer, body);
    if (reply.truncated) {
      body.appendChild(el("div", "term-warn",
        "Stopped early — the model was still calling tools. Ask something narrower."));
    }
    row.appendChild(body);
    scrollDown();
  }

  // ---- providers ----------------------------------------------------------

  function currentProvider() {
    return providers.filter(function (p) { return p.key === providerSel.value; })[0];
  }

  function syncProviderUI() {
    var p = currentProvider();
    if (!p) return;
    modelInput.placeholder = p.default_model;
    modelHint.textContent = "default: " + p.default_model;
    keysLink.href = p.keys_url || "#";
  }

  function loadProviders() {
    return fetch("/api/v1/providers")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        providers = data.providers || [];
        providers.forEach(function (p) {
          var opt = document.createElement("option");
          opt.value = p.key;
          opt.textContent = p.label;
          providerSel.appendChild(opt);
        });
        providerSel.value = recall(PROVIDER_STORE) || (providers[0] && providers[0].key) || "openai";
        modelInput.value = recall(MODEL_STORE);
        var savedKey = recall(KEY_STORE);
        if (savedKey) { keyInput.value = savedKey; rememberBox.checked = true; }
        syncProviderUI();
      })
      .catch(function () {
        addSystem("Could not load the provider list — is the server still running?", "error");
      });
  }

  // ---- slash commands -----------------------------------------------------

  var COMMANDS = {
    "/help": function () {
      addSystem(
        "/tools      list the engine tools the AI can call\n" +
        "/providers  supported LLM providers and where to get a key\n" +
        "/key        clear the stored API key from this browser\n" +
        "/clear      clear the conversation (and the AI's memory of it)\n" +
        "/help       this message\n\n" +
        "Anything else is sent to the AI. It answers by calling engine tools —\n" +
        "it is not allowed to compute or recall a number itself.");
    },
    "/tools": function () {
      fetch("/api/v1/tools").then(function (r) { return r.json(); }).then(function (data) {
        addSystem((data.tools || []).map(function (t) {
          return "  " + t.name + " — " + t.description.split(".")[0] + ".";
        }).join("\n"));
      }).catch(function () { addSystem("Could not reach the API.", "error"); });
    },
    "/providers": function () {
      addSystem(providers.map(function (p) {
        return "  " + p.key + "  " + p.label + "\n      default model: " + p.default_model +
               "\n      keys: " + p.keys_url;
      }).join("\n"));
    },
    "/key": function () {
      forget(KEY_STORE);
      keyInput.value = "";
      rememberBox.checked = false;
      $("setup").classList.remove("is-collapsed");
      addSystem("Stored key cleared from this browser.");
    },
    "/clear": function () {
      history = [];
      log.textContent = "";
      addSystem("Conversation cleared.");
    }
  };

  // ---- sending ------------------------------------------------------------

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    input.disabled = on;
    sendBtn.classList.toggle("is-busy", on);
  }

  function send(question) {
    var key = keyInput.value.trim();
    if (!key) {
      addSystem("Add your API key above first — the terminal uses your key, not the server's.", "error");
      $("setup").classList.remove("is-collapsed");
      keyInput.focus();
      return;
    }

    if (rememberBox.checked) { store(KEY_STORE, key); } else { forget(KEY_STORE); }
    store(PROVIDER_STORE, providerSel.value);
    store(MODEL_STORE, modelInput.value.trim());

    addUser(question);
    setBusy(true);
    var pending = addSystem("thinking…", "pending");

    fetch("/api/v1/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: question,
        api_key: key,
        provider: providerSel.value,
        model: modelInput.value.trim() || null,
        history: history
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (reply) {
        pending.remove();
        if (reply.error) {
          addSystem(reply.error, "error");
          return;
        }
        addAnswer(reply);
        // Keep the turn in client-side history so follow-ups have context. The
        // server is stateless on purpose — it must not sit on conversations
        // next to other people's keys.
        history.push({ role: "user", content: question });
        if (reply.answer) history.push({ role: "assistant", content: reply.answer });
        $("setup").classList.add("is-collapsed");
      })
      .catch(function (err) {
        pending.remove();
        addSystem("Request failed: " + err.message, "error");
      })
      .finally(function () {
        setBusy(false);
        input.focus();
      });
  }

  // ---- wiring -------------------------------------------------------------

  function submit() {
    var text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    autosize();

    if (text.charAt(0) === "/") {
      var cmd = COMMANDS[text.split(/\s+/)[0]];
      if (cmd) { addUser(text); cmd(); return; }
      addSystem("Unknown command. Try /help", "error");
      return;
    }
    send(text);
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  }

  composer.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  providerSel.addEventListener("change", syncProviderUI);
  $("examples").addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-q]");
    if (!btn) return;
    input.value = btn.dataset.q;
    autosize();
    input.focus();
  });

  loadProviders().then(function () { input.focus(); });
})();
