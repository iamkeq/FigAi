// FigAI Tab Bridge: reports open tabs to the local FigAI server and carries out
// "open a tab" requests it queues. Only ever talks to 127.0.0.1.

const DEFAULT_PORT = 8765;
const LONG_POLL_SECONDS = 20;
let polling = false;
let reportTimer = null;

async function getConfig() {
  const { token, port } = await chrome.storage.local.get(["token", "port"]);
  return { token: (token || "").trim(), port: Number(port) || DEFAULT_PORT };
}

async function call(path, options = {}) {
  const { token, port } = await getConfig();
  if (!token) throw new Error("no token set");
  const response = await fetch(`http://127.0.0.1:${port}${path}`, {
    ...options,
    headers: { "X-Fig-Token": token, "Content-Type": "application/json" },
  });
  if (!response.ok) throw new Error(`FigAI answered ${response.status}`);
  return response.json();
}

async function reportTabs() {
  const tabs = await chrome.tabs.query({});
  await call("/tabs", {
    method: "POST",
    body: JSON.stringify({
      tabs: tabs.map((t) => ({ id: t.id, windowId: t.windowId, title: t.title, url: t.url, active: t.active })),
    }),
  });
}

function scheduleReport() {
  clearTimeout(reportTimer);
  reportTimer = setTimeout(() => reportTabs().catch(() => {}), 1000);
}

async function runCommand(command) {
  if (command.type === "open_tab" && /^https?:\/\//i.test(command.url || "")) {
    await chrome.tabs.create({ url: command.url, active: command.active !== false });
    scheduleReport();
  }
}

async function pollLoop() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      const { token } = await getConfig();
      if (!token) return; // wait for the options page to set one
      try {
        await reportTabs();
        const { commands } = await call(`/commands?wait=${LONG_POLL_SECONDS}`);
        for (const command of commands || []) await runCommand(command);
      } catch (error) {
        await new Promise((resolve) => setTimeout(resolve, 5000)); // FigAI not up yet; back off
      }
    }
  } finally {
    polling = false;
  }
}

chrome.tabs.onCreated.addListener(scheduleReport);
chrome.tabs.onRemoved.addListener(scheduleReport);
chrome.tabs.onActivated.addListener(scheduleReport);
chrome.tabs.onReplaced.addListener(scheduleReport);
chrome.tabs.onUpdated.addListener((_id, change) => {
  if (change.url || change.title || change.status === "complete") scheduleReport();
});

// The service worker can be shut down when idle; this wakes it and restarts the loop.
chrome.alarms.create("keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => pollLoop());
chrome.runtime.onStartup.addListener(pollLoop);
chrome.runtime.onInstalled.addListener(pollLoop);
chrome.storage.onChanged.addListener(() => pollLoop());
pollLoop();
