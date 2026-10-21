const tokenInput = document.getElementById("token");
const portInput = document.getElementById("port");
const status = document.getElementById("status");

chrome.storage.local.get(["token", "port"]).then(({ token, port }) => {
  tokenInput.value = token || "";
  portInput.value = port || "";
});

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ token: tokenInput.value.trim(), port: Number(portInput.value) || 8765 });
  status.textContent = "Saved. The extension will connect within a few seconds.";
});
