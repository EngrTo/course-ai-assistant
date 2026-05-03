/**
 * AI Assistant — Embeddable Chat Widget
 * Usage: <script src="https://your-domain.com/static/widget.js" data-client="school"></script>
 */
(function () {
  const script = document.currentScript;
  const CLIENT_ID = script.getAttribute("data-client");
  const API_BASE = script.src.replace("/static/widget.js", "");
  const THEME_COLOR = script.getAttribute("data-color") || "#4f46e5";
  const TITLE = script.getAttribute("data-title") || "AI Assistant";
  const WELCOME = script.getAttribute("data-welcome") || "Hi! How can I help you today?";
  const POSITION = script.getAttribute("data-position") || "right";

  if (!CLIENT_ID) {
    console.error("AI Widget: data-client attribute is required.");
    return;
  }

  // Inject styles
  const style = document.createElement("style");
  style.textContent = `
    #ai-widget-btn {
      position: fixed;
      bottom: 24px;
      ${POSITION}: 24px;
      width: 60px;
      height: 60px;
      border-radius: 50%;
      background: ${THEME_COLOR};
      color: white;
      border: none;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,0,0,0.2);
      font-size: 28px;
      z-index: 99999;
      transition: transform 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    #ai-widget-btn:hover { transform: scale(1.1); }
    #ai-widget-btn.open { display: none; }

    #ai-widget-container {
      position: fixed;
      bottom: 24px;
      ${POSITION}: 24px;
      width: 380px;
      height: 520px;
      background: white;
      border-radius: 16px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.15);
      z-index: 99999;
      display: none;
      flex-direction: column;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    #ai-widget-container.open { display: flex; }

    #ai-widget-header {
      background: ${THEME_COLOR};
      color: white;
      padding: 14px 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 15px;
      font-weight: 600;
    }
    #ai-widget-close {
      background: none;
      border: none;
      color: white;
      font-size: 22px;
      cursor: pointer;
      padding: 0 4px;
      line-height: 1;
    }

    #ai-widget-messages {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .ai-w-msg {
      max-width: 85%;
      padding: 10px 14px;
      border-radius: 12px;
      font-size: 14px;
      line-height: 1.5;
      word-wrap: break-word;
    }
    .ai-w-msg.user {
      background: ${THEME_COLOR};
      color: white;
      align-self: flex-end;
      border-bottom-right-radius: 4px;
    }
    .ai-w-msg.bot {
      background: #f3f4f6;
      color: #333;
      align-self: flex-start;
      border-bottom-left-radius: 4px;
    }

    .ai-w-typing span {
      display: inline-block;
      width: 7px;
      height: 7px;
      margin: 0 2px;
      background: #999;
      border-radius: 50%;
      animation: ai-w-bounce 1.4s infinite both;
    }
    .ai-w-typing span:nth-child(2) { animation-delay: 0.2s; }
    .ai-w-typing span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes ai-w-bounce {
      0%, 80%, 100% { transform: scale(0); }
      40% { transform: scale(1); }
    }

    #ai-widget-input-area {
      display: flex;
      padding: 10px;
      border-top: 1px solid #eee;
      gap: 8px;
    }
    #ai-widget-input {
      flex: 1;
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 14px;
      outline: none;
    }
    #ai-widget-input:focus { border-color: ${THEME_COLOR}; }
    #ai-widget-send {
      background: ${THEME_COLOR};
      color: white;
      border: none;
      border-radius: 8px;
      padding: 10px 16px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
    }
    #ai-widget-send:disabled { opacity: 0.5; cursor: not-allowed; }

    #ai-widget-powered {
      text-align: center;
      font-size: 11px;
      color: #bbb;
      padding: 4px;
    }

    @media (max-width: 420px) {
      #ai-widget-container {
        width: calc(100vw - 16px);
        height: calc(100vh - 100px);
        bottom: 8px;
        ${POSITION}: 8px;
      }
    }
  `;
  document.head.appendChild(style);

  // Create widget button
  const btn = document.createElement("button");
  btn.id = "ai-widget-btn";
  btn.innerHTML = "💬";
  btn.title = "Chat with AI Assistant";
  document.body.appendChild(btn);

  // Create widget container
  const container = document.createElement("div");
  container.id = "ai-widget-container";
  container.innerHTML = `
    <div id="ai-widget-header">
      <span>${TITLE}</span>
      <button id="ai-widget-close">&times;</button>
    </div>
    <div id="ai-widget-messages">
      <div class="ai-w-msg bot">${WELCOME}</div>
    </div>
    <div id="ai-widget-input-area">
      <input type="text" id="ai-widget-input" placeholder="Type your question..." autocomplete="off">
      <button id="ai-widget-send">Send</button>
    </div>
    <div id="ai-widget-powered">Powered by AI Assistant</div>
  `;
  document.body.appendChild(container);

  // Elements
  const messages = container.querySelector("#ai-widget-messages");
  const input = container.querySelector("#ai-widget-input");
  const sendBtn = container.querySelector("#ai-widget-send");
  const closeBtn = container.querySelector("#ai-widget-close");

  // Toggle open/close
  btn.addEventListener("click", function () {
    btn.classList.add("open");
    container.classList.add("open");
    input.focus();
  });
  closeBtn.addEventListener("click", function () {
    container.classList.remove("open");
    btn.classList.remove("open");
  });

  // Send on Enter
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  sendBtn.addEventListener("click", sendMessage);

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function addMsg(text, role) {
    var msg = document.createElement("div");
    msg.className = "ai-w-msg " + role;
    msg.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
    messages.appendChild(msg);
    messages.scrollTop = messages.scrollHeight;
  }

  function showTyping() {
    var t = document.createElement("div");
    t.className = "ai-w-msg bot";
    t.id = "ai-w-typing";
    t.innerHTML = '<div class="ai-w-typing"><span></span><span></span><span></span></div>';
    messages.appendChild(t);
    messages.scrollTop = messages.scrollHeight;
  }

  function removeTyping() {
    var el = document.getElementById("ai-w-typing");
    if (el) el.remove();
  }

  async function sendMessage() {
    var question = input.value.trim();
    if (!question) return;

    addMsg(question, "user");
    input.value = "";
    sendBtn.disabled = true;
    showTyping();

    try {
      var res = await fetch(API_BASE + "/" + CLIENT_ID + "/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question }),
      });

      removeTyping();

      if (!res.ok) {
        addMsg("Sorry, something went wrong. Please try again.", "bot");
        return;
      }

      var data = await res.json();
      if (data.error) {
        addMsg(data.error, "bot");
      } else {
        addMsg(data.answer, "bot");
      }
    } catch (err) {
      removeTyping();
      addMsg("Connection error. Please try again.", "bot");
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  }
})();
