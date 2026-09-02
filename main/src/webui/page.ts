export const WEB_UI_PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>FigAi</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #16171a;
    color: #e7e7ea;
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    border-bottom: 1px solid #2a2b30;
    background: #1b1c20;
  }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; flex: 0 0 auto; }
  #model-form { display: flex; gap: 6px; margin-left: auto; align-items: center; }
  #model-input {
    background: #101114;
    border: 1px solid #33343a;
    color: #e7e7ea;
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 12px;
    width: 240px;
  }
  button {
    background: #2f3138;
    border: 1px solid #3a3c44;
    color: #e7e7ea;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    cursor: pointer;
  }
  button:hover { background: #3a3c44; }
  button:disabled { opacity: 0.5; cursor: default; }
  #reset-model { flex: 0 0 auto; }
  #status { font-size: 11px; color: #8a8c94; padding: 4px 16px; min-height: 16px; }
  #log {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .msg { max-width: 78%; padding: 8px 12px; border-radius: 10px; white-space: pre-wrap; line-height: 1.4; font-size: 14px; }
  .msg.user { align-self: flex-end; background: #2b5fd9; color: white; }
  .msg.assistant { align-self: flex-start; background: #232429; border: 1px solid #2f3138; }
  .msg.error { align-self: flex-start; background: #3a1f22; border: 1px solid #5a2a2f; color: #ffb4b4; }
  .msg img { max-width: 100%; border-radius: 6px; margin-top: 6px; display: block; }
  .meta { font-size: 10px; color: #8a8c94; margin-top: 4px; }
  form#send-form { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #2a2b30; background: #1b1c20; }
  #text-input {
    flex: 1;
    resize: none;
    background: #101114;
    border: 1px solid #33343a;
    color: #e7e7ea;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 14px;
    font-family: inherit;
    max-height: 160px;
  }
  #send-form button { padding: 0 18px; }
</style>
</head>
<body>
<header>
  <h1>FigAi</h1>
  <form id="model-form">
    <input id="model-input" type="text" placeholder="provider/model" autocomplete="off" />
    <button type="submit">Set model</button>
    <button type="button" id="reset-model">Reset</button>
  </form>
</header>
<div id="status"></div>
<div id="log"></div>
<form id="send-form">
  <textarea id="text-input" rows="1" placeholder="Message FigAi..." autofocus></textarea>
  <button type="submit">Send</button>
</form>
<script>
(function () {
  const log = document.getElementById("log");
  const status = document.getElementById("status");
  const sendForm = document.getElementById("send-form");
  const textInput = document.getElementById("text-input");
  const modelForm = document.getElementById("model-form");
  const modelInput = document.getElementById("model-input");
  const resetModel = document.getElementById("reset-model");

  function addMessage(role, text, meta) {
    const el = document.createElement("div");
    el.className = "msg " + role;
    el.textContent = text;
    if (meta) {
      const metaEl = document.createElement("div");
      metaEl.className = "meta";
      metaEl.textContent = meta;
      el.appendChild(metaEl);
    }
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  function addImage(container, dataUrl) {
    const img = document.createElement("img");
    img.src = dataUrl;
    container.appendChild(img);
  }

  async function refreshModel() {
    try {
      const res = await fetch("/api/model");
      const data = await res.json();
      modelInput.placeholder = data.model || "provider/model";
      status.textContent = "Model: " + (data.model || "unknown");
    } catch {
      status.textContent = "Could not reach FigAi.";
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch("/api/messages");
      const data = await res.json();
      for (const message of data.messages || []) {
        addMessage(message.role, message.text);
      }
    } catch {}
  }

  sendForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = textInput.value.trim();
    if (!text) return;
    textInput.value = "";
    addMessage("user", text);
    const thinking = addMessage("assistant", "...");
    sendForm.querySelector("button").disabled = true;
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Request failed");
      thinking.textContent = data.text || "(no response)";
      for (const image of data.images || []) addImage(thinking, image);
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = data.model + " · " + data.totalTokens + " tokens";
      thinking.appendChild(meta);
      refreshModel();
    } catch (error) {
      thinking.className = "msg error";
      thinking.textContent = "Error: " + error.message;
    } finally {
      sendForm.querySelector("button").disabled = false;
      textInput.focus();
    }
  });

  textInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendForm.requestSubmit();
    }
  });

  modelForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const model = modelInput.value.trim();
    if (!model) return;
    status.textContent = "Switching model...";
    try {
      const res = await fetch("/api/model", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Request failed");
      modelInput.value = "";
      refreshModel();
    } catch (error) {
      status.textContent = "Error: " + error.message;
    }
  });

  resetModel.addEventListener("click", async () => {
    status.textContent = "Resetting model...";
    try {
      await fetch("/api/model", { method: "DELETE" });
    } finally {
      refreshModel();
    }
  });

  refreshModel();
  loadHistory();
})();
</script>
</body>
</html>
`;
