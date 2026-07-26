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

  /* Safe markdown: headings, paragraphs, ordered and bullet lists, bold,
     italics, `inline code` and fenced blocks.

     Deliberately not a markdown library — every branch builds DOM nodes and
     sets textContent, so there is no path from model output to HTML. That is
     the XSS defence on a page where the user has pasted an API key, and it is
     why adding a library here would be a downgrade rather than an upgrade.

     The scope is set by what models actually emit, not by the spec. Testing
     against a live model showed `**bold**` and `### headings` rendering as
     literal asterisks and hashes, which made every answer look broken — those
     two are the most common formatting in an LLM reply and were the ones
     missing. */
  var BULLET = /^\s*[-*•]\s+/;
  var NUMBER = /^\s*\d+[.)]\s+/;
  var HEADING = /^(#{1,6})\s+(.*)$/;
  var RULE = /^\s*([-*_])\1{2,}\s*$/;

  function indentOf(line) {
    return line.length - line.replace(/^\s*/, "").length;
  }

  /* One list, plus any lists nested under its items. Returns the next
     unconsumed line index.

     Nesting is not decoration. A model answering "list the top 3 sources"
     writes a numbered item per source with indented bullets of detail beneath
     it; treating those bullets as top-level ends the <ol> after every item, so
     the numbering renders "1. … 1. … 1." and the answer looks broken. Indent
     is the only signal available and it is the one markdown actually uses. */
  function renderList(lines, i, baseIndent, into) {
    var numbered = NUMBER.test(lines[i]);
    var marker = numbered ? NUMBER : BULLET;
    var list = el(numbered ? "ol" : "ul", "term-list");
    var lastItem = null;

    while (i < lines.length) {
      var line = lines[i];

      // A blank line between items is a "loose list" in markdown — still ONE
      // list. Breaking here ended the <ol> after every item, so a model that
      // spaces its items out rendered "1. … 1. … 1." instead of 1, 2, 3.
      // Look past the blanks and only stop if what follows is not an item.
      if (!line.trim()) {
        var j = i;
        while (j < lines.length && !lines[j].trim()) j++;
        if (
          j < lines.length &&
          (NUMBER.test(lines[j]) || BULLET.test(lines[j])) &&
          indentOf(lines[j]) >= baseIndent
        ) {
          i = j;
          continue;
        }
        break;
      }

      var isItem = NUMBER.test(line) || BULLET.test(line);
      if (!isItem) break;

      var indent = indentOf(line);
      if (indent > baseIndent && lastItem) {
        i = renderList(lines, i, indent, lastItem); // deeper: nest under the item
        continue;
      }
      if (indent < baseIndent) break; // shallower: belongs to an outer list
      if (marker.test(line) === false) break; // same level, different kind

      lastItem = inlineInto(el("li"), line.replace(marker, ""));
      list.appendChild(lastItem);
      i++;
    }

    into.appendChild(list);
    return i;
  }

  /* Group lines into blocks, then render each.

     Line-driven rather than regex-preprocessed. The first attempt inserted
     blank lines before list markers to force new blocks, which also split
     *consecutive* items — every bullet became its own list and every numbered
     item restarted at 1. Walking the lines and grouping runs of the same kind
     is both simpler and correct, and it handles the real problem (models do not
     reliably leave a blank line before a heading or a list) without touching
     items that already belong together. */
  function renderProse(text, into) {
    var lines = String(text || "").replace(/\r/g, "").split("\n");
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];

      if (!line.trim()) { i++; continue; }

      if (line.trim().indexOf("```") === 0) {
        var body = [];
        i++;
        while (i < lines.length && lines[i].trim().indexOf("```") !== 0) body.push(lines[i++]);
        i++; // closing fence
        var pre = el("pre", "code-block");
        pre.appendChild(el("code", null, body.join("\n")));
        into.appendChild(pre);
        continue;
      }

      if (RULE.test(line)) { into.appendChild(el("hr", "term-rule")); i++; continue; }

      var heading = line.match(HEADING);
      if (heading) {
        // h4/h5 regardless of depth: these sit inside a chat turn, and an <h1>
        // from a model would outrank the page's own title.
        var tag = heading[1].length <= 2 ? "h4" : "h5";
        into.appendChild(inlineInto(el(tag, "term-heading"), heading[2]));
        i++;
        continue;
      }

      if (NUMBER.test(line) || BULLET.test(line)) {
        i = renderList(lines, i, indentOf(line), into);
        continue;
      }

      // A run of plain lines becomes one paragraph, keeping its single newlines
      // as <br>: models often emit a label per line, and collapsing them runs
      // the answer into one wall of text.
      var para = el("p");
      var first = true;
      while (
        i < lines.length && lines[i].trim() &&
        !HEADING.test(lines[i]) && !BULLET.test(lines[i]) &&
        !NUMBER.test(lines[i]) && !RULE.test(lines[i]) &&
        lines[i].trim().indexOf("```") !== 0
      ) {
        if (!first) para.appendChild(document.createElement("br"));
        inlineInto(para, lines[i]);
        first = false;
        i++;
      }
      into.appendChild(para);
    }
  }

  /* Inline spans, applied in one pass so a bold label containing code still
     renders both. Order matters: code first, so `**` inside a code span stays
     literal. */
  var INLINE = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|(?:^|\s)\*[^*\n]+\*(?=\s|$|[.,;:!?)]))/;

  function inlineInto(node, text) {
    String(text)
      .split(INLINE)
      .forEach(function (part) {
        if (!part) return;
        var lead = "";
        // The italic arm captures a leading space; preserve it as text.
        if (/^\s\*[^*]/.test(part)) {
          lead = part.charAt(0);
          part = part.slice(1);
        }
        if (lead) node.appendChild(document.createTextNode(lead));

        if (part.length > 2 && part.charAt(0) === "`" && part.slice(-1) === "`") {
          node.appendChild(el("code", null, part.slice(1, -1)));
        } else if (part.length > 4 && (part.indexOf("**") === 0 || part.indexOf("__") === 0)) {
          node.appendChild(el("strong", null, part.slice(2, -2)));
        } else if (part.length > 2 && part.charAt(0) === "*" && part.slice(-1) === "*") {
          node.appendChild(el("em", null, part.slice(1, -1)));
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

  /* Test seam. renderProse builds DOM and the only honest way to check it is
     to run it in a browser and inspect what the page ends up containing — so
     tests/test_browser.py needs a handle on it. Exposing one function is a
     smaller price than the alternatives: duplicating the renderer in a shim
     (which then tests the copy, not the code) or asserting about the source
     text (which cannot see nesting or escaping at all).

     Read-only and side-effect free: it renders into a node the caller owns. */
  window.__test_render = renderProse;

  loadProviders().then(function () { input.focus(); });
})();
